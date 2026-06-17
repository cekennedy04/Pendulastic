"""
==============================================================================
 compute_metrics.py - Gold-standard evaluation math engine
==============================================================================
 Scores the model-grid prediction CSVs (written by analysis_pipeline.run_model_grid)
 against the OptiTrack / Motive ground-truth joint-coordinate exports, and builds
 a leaderboard ranking every model / complexity / threshold permutation.

 Pipeline:
   1. FILE MATCHING   - find every prediction CSV
        P_{id}_Pos_{pos}_H_{height}_T_{trial}_{model}_{variant}_{threshold}.csv
      and match it to the matching Trial_{trial}_optitrack.csv. The match is by
      (participant, position, height, trial) tokens, so predictions in the webcam
      tree (Recordings/) pair with ground truth in OptiTrack_Recordings/ even
      though they live in different folders. Zero-padding differences in numeric
      ids/positions/trials (e.g. 0 vs 000) are tolerated.
   2. MOTIVE HEADER   - Motive CSVs carry several metadata/header rows. We scan
      for the axis-header row (starts with 'Frame'/'Time') and the joint-name row
      above it, then map columns -> hip/knee/ankle (right side preferred). A plain
      single-row header (hip_x, knee_y, ...) is also supported as a fallback.
   3. RESAMPLING      - webcam (~30 fps) and OptiTrack (~120 Hz) are linearly
      resampled (scipy interp1d) to a common frame count over the trial window.
   4. ALIGNMENT       - Umeyama / SVD absolute-orientation (rigid + uniform scale)
      maps the CV pixel coords into the OptiTrack lab frame (3-D GT reduced to its
      plane of motion for the 2-D pixel comparison).
   5. METRICS         - per-joint (Hip/Knee/Ankle) and overall RMSE in mm + PCK at
      configurable mm thresholds, appended to a master leaderboard.
   6. EXPORT          - leaderboard summary CSV (best-first) + optional per-pair
      detail CSV.

 Usage:
     python compute_metrics.py [--root .] [--out metrics_summary.csv]
                               [--pck 25,50] [--detail per_pair_metrics.csv]

 Requires:  numpy, scipy, pandas  (all in requirements.txt).
==============================================================================
"""

import os
import re
import csv
import argparse

import numpy as np

try:
    from scipy.interpolate import interp1d
except ImportError:
    print("ERROR: scipy is required. Run:  pip install scipy")
    raise SystemExit(1)

try:
    import pandas as pd
except ImportError:
    pd = None   # Motive parsing falls back to the csv module.


JOINTS = ["hip", "knee", "ankle"]
AXES = ["x", "y", "z"]

PRED_PATTERN = re.compile(
    r"^P_(?P<id>.+?)_Pos_(?P<position>.+?)_H_(?P<height>.+?)_T_(?P<trial>.+?)_"
    r"(?P<model>mediapipe|rtmpose|mmpose|fremocap|openpose)_(?P<variant>.+?)_"
    r"(?P<threshold>[0-9]*\.?[0-9]+)\.csv$"
)
OPTITRACK_SUFFIX = "_optitrack.csv"
_OPTITRACK_RE = re.compile(r"^Trial_(\d+)_optitrack\.csv$", re.IGNORECASE)

# Directories we never need to scan (keeps a project-root scan fast).
_PRUNE_DIRS = {".venv", "venv", "env", ".git", "__pycache__", "models", "node_modules"}


# =============================================================================
# 1. FILE MATCHING (token-based, cross-tree)
# =============================================================================
def _tok(value):
    """Normalise a token for matching: ints compare by value (0 == 000), else lower."""
    s = (value or "").strip()
    if s.lstrip("+-").isdigit():
        return str(int(s))
    return s.lower()


def _match_key(idv, pos, height, trial):
    return (_tok(idv), _tok(pos), _tok(height), _tok(trial))


def _optitrack_path_tokens(path):
    """Parse (id, position, height, trial) from an OptiTrack file path."""
    idv = pos = height = None
    for part in os.path.normpath(path).split(os.sep):
        if part.startswith("Participant_"):
            idv = part[len("Participant_"):]
        elif part.startswith("Position_"):
            pos = part[len("Position_"):]
        elif part.startswith("Height_"):
            height = part[len("Height_"):]
    m = _OPTITRACK_RE.match(os.path.basename(path))
    trial = m.group(1) if m else None
    return idv, pos, height, trial


def find_matches(root_dir):
    """
    Find every prediction CSV under root_dir and match each to its OptiTrack
    ground-truth file by (id, position, height, trial). Returns a list of dicts;
    gt_csv is None when no ground truth was found for that permutation.
    """
    predictions = []
    gt_by_key = {}

    for dirpath, dirnames, files in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        for fname in files:
            pm = PRED_PATTERN.match(fname)
            if pm:
                predictions.append((os.path.join(dirpath, fname), pm.groupdict(), dirpath))
                continue
            if _OPTITRACK_RE.match(fname):
                idv, pos, height, trial = _optitrack_path_tokens(os.path.join(dirpath, fname))
                if trial is not None:
                    gt_by_key[_match_key(idv, pos, height, trial)] = os.path.join(dirpath, fname)

    matches = []
    for pred_path, info, dirpath in predictions:
        # Fast path: a ground-truth file sitting in the same folder.
        same = os.path.join(dirpath, f"Trial_{info['trial']}_optitrack.csv")
        gt = same if os.path.exists(same) else gt_by_key.get(
            _match_key(info["id"], info["position"], info["height"], info["trial"]))
        matches.append({**info, "pred_csv": pred_path, "gt_csv": gt, "folder": dirpath})
    return matches


# =============================================================================
# 2a. PREDICTION LOADER
# =============================================================================
def load_prediction(pred_csv):
    """Load a grid prediction CSV -> (time[N], pts[N, 3 joints, 2]) (NaN if missing)."""
    times, pts = [], []
    with open(pred_csv, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = float(row["time_sec"])
            except (TypeError, ValueError, KeyError):
                continue
            frame_pts = []
            for j in JOINTS:
                try:
                    x = float(row[f"{j}_x"]); y = float(row[f"{j}_y"])
                except (TypeError, ValueError, KeyError):
                    x = y = np.nan
                frame_pts.append((x, y))
            times.append(t)
            pts.append(frame_pts)
    if not times:
        raise ValueError(f"No usable rows in prediction CSV: {pred_csv}")
    return np.asarray(times, float), np.asarray(pts, float)


# =============================================================================
# 2b. OPTITRACK / MOTIVE LOADER (multi-row header aware)
# =============================================================================
# Single-row-header aliases (used only by the simple fallback).
_COORD_ALIASES = {
    (j, ax): [f"{j}_{ax}", f"{j}{ax}", f"r{j}_{ax}", f"l{j}_{ax}",
              f"right_{j}_{ax}", f"left_{j}_{ax}", f"{j} {ax}"]
    for j in JOINTS for ax in AXES
}
_TIME_ALIASES = ["time", "timestamp", "time_s", "time_sec", "time (seconds)",
                 "frame_time", "t"]


def _read_rows(path):
    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        return list(csv.reader(f))


def _find_axis_header_row(rows):
    """Index of the Motive axis row (first cell 'Frame' or 'Time')."""
    for i, r in enumerate(rows):
        if not r:
            continue
        c0 = r[0].strip().lower()
        if c0.startswith("frame") or c0 in ("time", "time (seconds)"):
            return i
    return None


def _find_name_row(rows, header_idx):
    """Row above the axis header that carries joint/marker names."""
    for i in range(header_idx - 1, -1, -1):
        for cell in rows[i]:
            cl = cell.strip().lower()
            if any(j in cl for j in JOINTS):
                return i
    return None


def _side_score(name, joint):
    """Prefer right-side markers (to match the prediction's right-leg default)."""
    score = 0
    if "right" in name:
        score += 3
    if re.search(r"(^|[^a-z])r[_: ]?" + joint, name):
        score += 2
    if "left" in name or re.search(r"(^|[^a-z])l[_: ]?" + joint, name):
        score -= 2
    return score


def _map_motive_columns(axis_row, name_row):
    """
    Build {(joint, axis): column_index} + dim from the Motive Name row + axis row.
    Returns (None, 0) if any joint is missing (caller falls back to simple header).
    """
    axis_l = [c.strip().lower() for c in axis_row]
    name_l = [(name_row[c].strip().lower() if name_row and c < len(name_row) else "")
              for c in range(len(axis_row))]

    joint_markers = {j: {} for j in JOINTS}
    for c, ax in enumerate(axis_l):
        if ax not in AXES:
            continue
        nm = name_l[c]
        for j in JOINTS:
            if j in nm:
                joint_markers[j].setdefault(nm, {})[ax] = c
                break

    col_map, dims = {}, []
    for j in JOINTS:
        markers = joint_markers[j]
        if not markers:
            return None, 0
        best = sorted(markers, key=lambda nm: (-_side_score(nm, j), nm))[0]
        present = markers[best]
        if "x" not in present or "y" not in present:
            return None, 0
        col_map[(j, "x")] = present["x"]
        col_map[(j, "y")] = present["y"]
        if "z" in present:
            col_map[(j, "z")] = present["z"]
        dims.append(3 if "z" in present else 2)
    return col_map, min(dims)


def _data_block(path, header_idx):
    """Read the numeric data block below the axis header (pandas if available)."""
    if pd is not None:
        df = pd.read_csv(path, skiprows=header_idx + 1, header=None,
                         engine="python", on_bad_lines="skip")
        return df.apply(pd.to_numeric, errors="coerce").to_numpy()
    rows = _read_rows(path)[header_idx + 1:]
    out = []
    for r in rows:
        if not r:
            continue
        vals = []
        for c in r:
            try:
                vals.append(float(c))
            except ValueError:
                vals.append(np.nan)
        out.append(vals)
    width = max((len(r) for r in out), default=0)
    arr = np.full((len(out), width), np.nan)
    for i, r in enumerate(out):
        arr[i, :len(r)] = r
    return arr


def _load_optitrack_motive(rows, path):
    """Parse a multi-row Motive export. Returns (time, pts) or None to fall back."""
    header_idx = _find_axis_header_row(rows)
    if header_idx is None:
        return None
    axis_row = rows[header_idx]
    name_row_idx = _find_name_row(rows, header_idx)
    name_row = rows[name_row_idx] if name_row_idx is not None else None

    col_map, dim = _map_motive_columns(axis_row, name_row)
    if not col_map:
        return None

    time_col = next((c for c, cell in enumerate(axis_row)
                     if "time" in cell.strip().lower()), None)
    data = _data_block(path, header_idx)
    if data.size == 0:
        return None

    n = data.shape[0]
    axes = AXES[:dim]
    times = (data[:, time_col] if time_col is not None and time_col < data.shape[1]
             else np.arange(n, dtype=float))
    pts = np.full((n, len(JOINTS), dim), np.nan)
    for ji, j in enumerate(JOINTS):
        for ai, ax in enumerate(axes):
            c = col_map.get((j, ax))
            if c is not None and c < data.shape[1]:
                pts[:, ji, ai] = data[:, c]
    return np.asarray(times, float), pts


def _load_optitrack_simple(rows, path):
    """Fallback: a single header row with hip_x / knee_y / ... columns."""
    header = rows[0]
    header_lower = {h.strip().lower(): i for i, h in enumerate(header)}

    def find_col(aliases):
        for a in aliases:
            if a in header_lower:
                return header_lower[a]
        return None

    time_idx = find_col(_TIME_ALIASES)
    has_z = all(find_col(_COORD_ALIASES[(j, "z")]) is not None for j in JOINTS)
    dim = 3 if has_z else 2
    axes = AXES[:dim]

    col_idx = {}
    for j in JOINTS:
        for ax in axes:
            idx = find_col(_COORD_ALIASES[(j, ax)])
            if idx is None:
                raise ValueError(
                    f"Could not locate joint coordinates in {os.path.basename(path)}.\n"
                    f"Columns present: {header}\n"
                    "Need per-joint hip/knee/ankle x,y[,z] (Motive multi-row header "
                    "or simple *_x/*_y columns). Adjust _COORD_ALIASES if needed."
                )
            col_idx[(j, ax)] = idx

    times, pts = [], []
    for r in rows[1:]:
        if not r:
            continue
        try:
            t = float(r[time_idx]) if time_idx is not None else float(len(times))
        except (ValueError, IndexError):
            t = float(len(times))
        frame = []
        for j in JOINTS:
            coord = []
            for ax in axes:
                try:
                    coord.append(float(r[col_idx[(j, ax)]]))
                except (ValueError, IndexError):
                    coord.append(np.nan)
            frame.append(coord)
        times.append(t)
        pts.append(frame)
    return np.asarray(times, float), np.asarray(pts, float)


def load_optitrack(gt_csv):
    """Load OptiTrack joint coordinates -> (time[M], pts[M, 3, D]). Motive-aware."""
    rows = _read_rows(gt_csv)
    if not rows:
        raise ValueError(f"Empty OptiTrack CSV: {gt_csv}")
    motive = _load_optitrack_motive(rows, gt_csv)
    if motive is not None:
        return motive
    return _load_optitrack_simple(rows, gt_csv)


# =============================================================================
# 3. RESAMPLING
# =============================================================================
def _resample_series(t, series, n):
    t = np.asarray(t, float)
    series = np.asarray(series, float)
    finite = np.isfinite(series) & np.isfinite(t)
    if finite.sum() < 2:
        return np.full(n, np.nan)
    tf, sf = t[finite], series[finite]
    f = interp1d(tf, sf, kind="linear", bounds_error=False, fill_value=(sf[0], sf[-1]))
    return f(np.linspace(tf[0], tf[-1], n))


def resample_points(t, pts, n):
    M, J, D = pts.shape
    out = np.empty((n, J, D), float)
    for j in range(J):
        for d in range(D):
            out[:, j, d] = _resample_series(t, pts[:, j, d], n)
    return out


# =============================================================================
# 4. ALIGNMENT (Umeyama / SVD, rigid + uniform scale)
# =============================================================================
def umeyama(src, dst, with_scale=True):
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    k, d = src.shape
    mu_s, mu_d = src.mean(0), dst.mean(0)
    sc, dc = src - mu_s, dst - mu_d
    cov = (dc.T @ sc) / k
    U, Dvals, Vt = np.linalg.svd(cov)
    S = np.eye(d)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[-1, -1] = -1.0
    R = U @ S @ Vt
    if with_scale:
        var_s = (sc ** 2).sum() / k
        scale = float((Dvals * np.diag(S)).sum() / var_s) if var_s > 0 else 1.0
    else:
        scale = 1.0
    t = mu_d - scale * (R @ mu_s)
    aligned = (scale * (R @ src.T)).T + t
    return scale, R, t, aligned


def _gt_to_plane(pts):
    M, J, D = pts.shape
    if D == 2:
        return pts, (0, 1)
    flat = pts.reshape(-1, D)
    var = np.nanvar(flat, axis=0)
    axes = tuple(int(a) for a in sorted(np.argsort(var)[-2:]))
    return pts[:, :, axes], axes


# =============================================================================
# 5. METRICS
# =============================================================================
def evaluate_pair(pred_csv, gt_csv, pck_thresholds=(25.0, 50.0)):
    t_pred, pred = load_prediction(pred_csv)
    t_gt, gt = load_optitrack(gt_csv)

    n = max(10, min(len(t_pred), len(t_gt)))
    pred_rs = resample_points(t_pred, pred, n)
    gt_rs = resample_points(t_gt, gt, n)
    gt2d, axes = _gt_to_plane(gt_rs)

    valid = np.isfinite(pred_rs).all(2) & np.isfinite(gt2d).all(2)
    if valid.sum() < 3:
        return {"status": "skipped", "reason": "fewer than 3 valid joint samples"}

    scale, R, t, _ = umeyama(pred_rs[valid], gt2d[valid], with_scale=True)
    flat = pred_rs.reshape(-1, 2)
    aligned = ((scale * (R @ flat.T)).T + t).reshape(n, len(JOINTS), 2)
    dist = np.linalg.norm(aligned - gt2d, axis=2)

    per_joint = {}
    for ji, jname in enumerate(JOINTS):
        m = valid[:, ji]
        per_joint[jname] = (float(np.sqrt(np.mean(dist[m, ji] ** 2)))
                            if m.any() else float("nan"))
    overall = float(np.sqrt(np.mean(dist[valid] ** 2)))
    pck = {thr: float(np.mean(dist[valid] <= thr)) for thr in pck_thresholds}

    return {
        "status": "ok", "n_frames": int(n),
        "n_valid_joint_samples": int(valid.sum()),
        "scale_px_to_mm": scale, "gt_plane_axes": axes,
        "rmse_mm": overall, "rmse_per_joint_mm": per_joint, "pck": pck,
    }


# =============================================================================
# 6. LEADERBOARD AGGREGATION + EXPORT
# =============================================================================
def aggregate(results, pck_thresholds):
    buckets = {}
    for r in results:
        if r["metrics"].get("status") != "ok":
            continue
        key = (r["model"], r["variant"], r["threshold"])
        buckets.setdefault(key, []).append(r["metrics"])

    rows = []
    for (model, variant, threshold), ms in buckets.items():
        row = {"model": model, "variant": variant, "threshold": threshold,
               "n_trials": len(ms),
               "mean_rmse_mm": float(np.mean([m["rmse_mm"] for m in ms]))}
        for j in JOINTS:
            row[f"rmse_{j}_mm"] = float(np.nanmean([m["rmse_per_joint_mm"][j] for m in ms]))
        for thr in pck_thresholds:
            row[f"pck@{thr:g}mm"] = float(np.mean([m["pck"][thr] for m in ms]))
        rows.append(row)
    rows.sort(key=lambda r: r["mean_rmse_mm"])
    return rows


def export_summary(rows, out_path, pck_thresholds):
    fields = (["model", "variant", "threshold", "n_trials", "mean_rmse_mm"]
              + [f"rmse_{j}_mm" for j in JOINTS]
              + [f"pck@{thr:g}mm" for thr in pck_thresholds])
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in r.items() if k in fields})


def export_detail(results, out_path, pck_thresholds):
    fields = (["model", "variant", "threshold", "trial_folder", "status",
               "n_frames", "rmse_mm"] + [f"rmse_{j}_mm" for j in JOINTS]
              + [f"pck@{thr:g}mm" for thr in pck_thresholds])
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            m = r["metrics"]
            row = {"model": r["model"], "variant": r["variant"],
                   "threshold": r["threshold"], "trial_folder": r["folder"],
                   "status": m.get("status")}
            if m.get("status") == "ok":
                row["n_frames"] = m["n_frames"]
                row["rmse_mm"] = round(m["rmse_mm"], 3)
                for j in JOINTS:
                    row[f"rmse_{j}_mm"] = round(m["rmse_per_joint_mm"][j], 3)
                for thr in pck_thresholds:
                    row[f"pck@{thr:g}mm"] = round(m["pck"][thr], 4)
            w.writerow(row)


def print_leaderboard(rows, pck_thresholds):
    print("=" * 88)
    print(" LEADERBOARD  (mean over trials; RMSE in mm, lower = better)")
    print("=" * 88)
    if not rows:
        print(" No prediction/ground-truth pairs were successfully scored.")
        return
    pck_hdr = "  ".join(f"PCK@{thr:g}" for thr in pck_thresholds)
    print(f" {'model':<10}{'var':<6}{'thr':<6}{'n':<3} {'RMSE':>7} "
          f"{'hip':>6} {'knee':>6} {'ankle':>6}  {pck_hdr}")
    print("-" * 88)
    for r in rows:
        pck_vals = "  ".join(f"{r[f'pck@{thr:g}mm']:6.1%}" for thr in pck_thresholds)
        print(f" {r['model']:<10}{str(r['variant']):<6}{str(r['threshold']):<6}"
              f"{r['n_trials']:<3} {r['mean_rmse_mm']:7.1f} "
              f"{r['rmse_hip_mm']:6.1f} {r['rmse_knee_mm']:6.1f} {r['rmse_ankle_mm']:6.1f}"
              f"  {pck_vals}")
    best = rows[0]
    print("-" * 88)
    print(f" BEST: {best['model']} / variant {best['variant']} / threshold "
          f"{best['threshold']}  ->  {best['mean_rmse_mm']:.1f} mm mean RMSE")
    print("=" * 88)


# =============================================================================
# Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Score grid predictions vs OptiTrack.")
    parser.add_argument("--root", default=".",
                        help="Root to scan for predictions + OptiTrack files "
                             "(default: current dir, covers both Recordings/ and "
                             "OptiTrack_Recordings/).")
    parser.add_argument("--out", default="metrics_summary.csv",
                        help="Leaderboard summary CSV output path.")
    parser.add_argument("--detail", default=None,
                        help="Optional per-pair detail CSV output path.")
    parser.add_argument("--pck", default="25,50",
                        help="Comma-separated PCK thresholds in mm (default 25,50).")
    args = parser.parse_args()

    pck_thresholds = tuple(float(x) for x in args.pck.split(",") if x.strip())
    if not os.path.isdir(args.root):
        print(f"Not a directory: {args.root}")
        raise SystemExit(1)

    matches = find_matches(args.root)
    n_pred = len(matches)
    n_unmatched = sum(1 for m in matches if not m["gt_csv"])
    print(f"Found {n_pred} prediction CSV(s); {n_pred - n_unmatched} matched to an "
          f"OptiTrack file, {n_unmatched} unmatched.")

    results, scored, skipped = [], 0, 0
    for mt in matches:
        if not mt["gt_csv"]:
            mt = {**mt, "metrics": {"status": "no_ground_truth"}}
            results.append(mt)
            skipped += 1
            continue
        try:
            metrics = evaluate_pair(mt["pred_csv"], mt["gt_csv"], pck_thresholds)
        except Exception as e:
            metrics = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
        if metrics.get("status") == "ok":
            scored += 1
        else:
            skipped += 1
            print(f"  [skip] {os.path.basename(mt['pred_csv'])}: "
                  f"{metrics.get('reason', metrics.get('status'))}")
        results.append({**mt, "metrics": metrics})

    rows = aggregate(results, pck_thresholds)
    export_summary(rows, args.out, pck_thresholds)
    if args.detail:
        export_detail(results, args.detail, pck_thresholds)

    print_leaderboard(rows, pck_thresholds)
    print(f"\nScored {scored}/{n_pred} permutations. Leaderboard written to: {args.out}")
    if args.detail:
        print(f"Per-pair detail written to: {args.detail}")


if __name__ == "__main__":
    main()
