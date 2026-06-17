"""
==============================================================================
 compute_metrics.py - Gold-standard evaluation math engine
==============================================================================
 Scores the model-grid prediction CSVs (written by analysis_pipeline.run_model_grid)
 against the OptiTrack ground-truth joint-coordinate exports.

 Pipeline per matched (prediction, ground-truth) pair:
   1. FILE MATCHING   - find each P_{id}_Pos_{pos}_H_{height}_T_{trial}_{model}_
                        {variant}_{threshold}.csv and its sibling
                        Trial_{trial}_optitrack.csv in the same folder.
   2. RESAMPLING      - webcam (~30 fps) and OptiTrack (~120 Hz) differ, so both
                        trajectories are linearly resampled (scipy interp1d) to a
                        common frame count over the trial window. Triggered
                        start/stop (motive_sync) keeps the windows aligned.
   3. ALIGNMENT       - a rigid Procrustes / SVD absolute-orientation transform
                        (Umeyama, with uniform scale) maps the CV pixel/relative
                        coordinates into the OptiTrack laboratory frame.
   4. METRICS         - per-joint and overall RMSE in millimetres for the Hip,
                        Knee and Ankle joint centres, plus PCK (% of joints within
                        a distance threshold) at one or more mm thresholds.
   5. EXPORT          - a summary CSV of mean RMSE + PCK per model variation, so
                        you can read off the best configuration.

 Usage:
     python compute_metrics.py [--root Recordings] [--out metrics_summary.csv]
                               [--pck 25,50] [--detail per_pair_metrics.csv]

 Requires:  numpy, scipy  (already in requirements.txt).

 -----------------------------------------------------------------------------
 EXPECTED OptiTrack EXPORT SCHEMA (tolerant, but verify against your real file):
   a header row + one column we can read as time, plus per-joint coordinate
   columns named like hip_x/hip_y[/hip_z], knee_x..., ankle_x...  (also accepts
   RHip_X, right_hip_x, "Hip X", hipx, etc.). 2-D (x,y) or 3-D (x,y,z) both work;
   3-D is reduced to its plane of motion for the 2-D pixel comparison.
   If your Motive export uses different headers, adjust _COORD_ALIASES below.
==============================================================================
"""

import os
import re
import csv
import glob
import argparse

import numpy as np

try:
    from scipy.interpolate import interp1d
except ImportError:
    print("ERROR: scipy is required. Run:  pip install scipy")
    raise SystemExit(1)


JOINTS = ["hip", "knee", "ankle"]
AXES = ["x", "y", "z"]

# Prediction filenames written by run_model_grid.
PRED_PATTERN = re.compile(
    r"^P_(?P<id>.+?)_Pos_(?P<position>.+?)_H_(?P<height>.+?)_T_(?P<trial>.+?)_"
    r"(?P<model>mediapipe|rtmpose|mmpose|fremocap|openpose)_(?P<variant>.+?)_"
    r"(?P<threshold>[0-9]*\.?[0-9]+)\.csv$"
)
OPTITRACK_SUFFIX = "_optitrack.csv"


# =============================================================================
# 1. FILE MATCHING
# =============================================================================
def find_matches(root_dir):
    """Find (prediction CSV, OptiTrack CSV) pairs sharing a folder + trial."""
    matches = []
    for dirpath, _dirs, files in os.walk(root_dir):
        for fname in files:
            m = PRED_PATTERN.match(fname)
            if not m:
                continue
            trial = m.group("trial")
            gt_name = f"Trial_{trial}{OPTITRACK_SUFFIX}"
            gt_path = os.path.join(dirpath, gt_name)
            if not os.path.exists(gt_path):
                continue
            info = m.groupdict()
            info.update({
                "pred_csv": os.path.join(dirpath, fname),
                "gt_csv": gt_path,
                "folder": dirpath,
            })
            matches.append(info)
    return matches


# =============================================================================
# 2. LOADERS
# =============================================================================
def load_prediction(pred_csv):
    """Load a grid prediction CSV -> (time[N], pts[N, 3 joints, 2]) (NaN if missing)."""
    times, pts = [], []
    with open(pred_csv, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                t = float(row["time_sec"])
            except (TypeError, ValueError):
                continue
            frame_pts = []
            for j in JOINTS:
                try:
                    x = float(row[f"{j}_x"]); y = float(row[f"{j}_y"])
                except (TypeError, ValueError):
                    x = y = np.nan
                frame_pts.append((x, y))
            times.append(t)
            pts.append(frame_pts)
    if not times:
        raise ValueError(f"No usable rows in prediction CSV: {pred_csv}")
    return np.asarray(times, float), np.asarray(pts, float)


# Column-name aliases for the OptiTrack joint coordinates. Edit to match your
# Motive export headers if needed.
_COORD_ALIASES = {
    ("hip", "x"): ["hip_x", "hipx", "rhip_x", "lhip_x", "right_hip_x", "left_hip_x", "hip x"],
    ("hip", "y"): ["hip_y", "hipy", "rhip_y", "lhip_y", "right_hip_y", "left_hip_y", "hip y"],
    ("hip", "z"): ["hip_z", "hipz", "rhip_z", "lhip_z", "right_hip_z", "left_hip_z", "hip z"],
    ("knee", "x"): ["knee_x", "kneex", "rknee_x", "lknee_x", "right_knee_x", "left_knee_x", "knee x"],
    ("knee", "y"): ["knee_y", "kneey", "rknee_y", "lknee_y", "right_knee_y", "left_knee_y", "knee y"],
    ("knee", "z"): ["knee_z", "kneez", "rknee_z", "lknee_z", "right_knee_z", "left_knee_z", "knee z"],
    ("ankle", "x"): ["ankle_x", "anklex", "rankle_x", "lankle_x", "right_ankle_x", "left_ankle_x", "ankle x"],
    ("ankle", "y"): ["ankle_y", "ankley", "rankle_y", "lankle_y", "right_ankle_y", "left_ankle_y", "ankle y"],
    ("ankle", "z"): ["ankle_z", "anklez", "rankle_z", "lankle_z", "right_ankle_z", "left_ankle_z", "ankle z"],
}
_TIME_ALIASES = ["time", "timestamp", "time_s", "time_sec", "frame_time", "t"]


def _find_col(header_lower, aliases):
    for a in aliases:
        if a in header_lower:
            return header_lower[a]
    return None


def load_optitrack(gt_csv):
    """
    Load OptiTrack joint coordinates -> (time[M], pts[M, 3 joints, D]) with D in {2,3}.

    Tolerant of column naming (see _COORD_ALIASES). Raises ValueError with the
    columns it actually found if the joint coordinates can't be located - this is
    the one spot likely to need tweaking for a real Motive export.
    """
    with open(gt_csv, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise ValueError(f"Empty OptiTrack CSV: {gt_csv}")

    header = rows[0]
    header_lower = {h.strip().lower(): i for i, h in enumerate(header)}

    time_idx = _find_col(header_lower, _TIME_ALIASES)

    has_z = all(_find_col(header_lower, _COORD_ALIASES[(j, "z")]) is not None for j in JOINTS)
    dim = 3 if has_z else 2
    axes = AXES[:dim]

    col_idx = {}
    for j in JOINTS:
        for ax in axes:
            idx = _find_col(header_lower, _COORD_ALIASES[(j, ax)])
            if idx is None:
                raise ValueError(
                    f"Could not find a '{j}_{ax}' column in {os.path.basename(gt_csv)}.\n"
                    f"Columns present: {header}\n"
                    "Joint-centre RMSE needs per-joint coordinates (hip/knee/ankle "
                    "x,y[,z]). Edit _COORD_ALIASES in compute_metrics.py to map your "
                    "Motive export headers, or export joint centres from Motive."
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
        ok = True
        for j in JOINTS:
            coord = []
            for ax in axes:
                try:
                    coord.append(float(r[col_idx[(j, ax)]]))
                except (ValueError, IndexError):
                    coord.append(np.nan); ok = False
            frame.append(coord)
        times.append(t)
        pts.append(frame)
    if not times:
        raise ValueError(f"No numeric rows in OptiTrack CSV: {gt_csv}")
    return np.asarray(times, float), np.asarray(pts, float)


# =============================================================================
# 3. RESAMPLING
# =============================================================================
def _resample_series(t, series, n):
    """Linearly resample a 1-D series (possibly with NaN) to n points (interp1d)."""
    t = np.asarray(t, float)
    series = np.asarray(series, float)
    finite = np.isfinite(series) & np.isfinite(t)
    if finite.sum() < 2:
        return np.full(n, np.nan)
    tf, sf = t[finite], series[finite]
    f = interp1d(tf, sf, kind="linear", bounds_error=False,
                 fill_value=(sf[0], sf[-1]))
    grid = np.linspace(tf[0], tf[-1], n)
    return f(grid)


def resample_points(t, pts, n):
    """Resample pts[M, J, D] to n frames along time (each joint/axis independently)."""
    M, J, D = pts.shape
    out = np.empty((n, J, D), float)
    for j in range(J):
        for d in range(D):
            out[:, j, d] = _resample_series(t, pts[:, j, d], n)
    return out


# =============================================================================
# 4. ALIGNMENT (Umeyama / SVD absolute orientation, rigid + uniform scale)
# =============================================================================
def umeyama(src, dst, with_scale=True):
    """
    Estimate similarity transform mapping src -> dst (Umeyama 1991).
    src, dst: (K, D) corresponding points. Returns (scale, R, t, aligned_src).
    """
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
    """Reduce 3-D GT points to their 2-D plane of motion (2 highest-variance axes)."""
    M, J, D = pts.shape
    if D == 2:
        return pts, (0, 1)
    flat = pts.reshape(-1, D)
    var = np.nanvar(flat, axis=0)
    axes = tuple(int(a) for a in sorted(np.argsort(var)[-2:]))   # two most-varying axes
    return pts[:, :, axes], axes


# =============================================================================
# 5. METRICS
# =============================================================================
def evaluate_pair(pred_csv, gt_csv, pck_thresholds=(25.0, 50.0)):
    """
    Align one prediction CSV to its OptiTrack CSV and return joint metrics.
    Returns a dict (or {"status": "skipped", ...} when it cannot be scored).
    """
    t_pred, pred = load_prediction(pred_csv)          # (Np, J, 2)
    t_gt, gt = load_optitrack(gt_csv)                 # (Mg, J, D)

    n = max(10, min(len(t_pred), len(t_gt)))
    pred_rs = resample_points(t_pred, pred, n)        # (n, J, 2)
    gt_rs = resample_points(t_gt, gt, n)              # (n, J, D)
    gt2d, axes = _gt_to_plane(gt_rs)                  # (n, J, 2)

    valid = np.isfinite(pred_rs).all(2) & np.isfinite(gt2d).all(2)   # (n, J)
    if valid.sum() < 3:
        return {"status": "skipped", "reason": "fewer than 3 valid joint samples"}

    src = pred_rs[valid]            # (K, 2)
    dst = gt2d[valid]              # (K, 2)
    scale, R, t, _ = umeyama(src, dst, with_scale=True)

    # Apply the transform to every joint/frame, then measure residuals (mm).
    flat = pred_rs.reshape(-1, 2)
    aligned = ((scale * (R @ flat.T)).T + t).reshape(n, len(JOINTS), 2)
    dist = np.linalg.norm(aligned - gt2d, axis=2)     # (n, J), mm

    per_joint_rmse = {}
    for ji, jname in enumerate(JOINTS):
        m = valid[:, ji]
        per_joint_rmse[jname] = (float(np.sqrt(np.mean(dist[m, ji] ** 2)))
                                 if m.any() else float("nan"))
    overall_rmse = float(np.sqrt(np.mean(dist[valid] ** 2)))
    pck = {thr: float(np.mean(dist[valid] <= thr)) for thr in pck_thresholds}

    return {
        "status": "ok",
        "n_frames": int(n),
        "n_valid_joint_samples": int(valid.sum()),
        "scale_px_to_mm": scale,
        "gt_plane_axes": axes,
        "rmse_mm": overall_rmse,
        "rmse_per_joint_mm": per_joint_rmse,
        "pck": pck,
    }


# =============================================================================
# 6. AGGREGATION + EXPORT
# =============================================================================
def aggregate(results, pck_thresholds):
    """Group per-pair metrics by (model, variant, threshold) and average them."""
    buckets = {}
    for r in results:
        if r["metrics"].get("status") != "ok":
            continue
        key = (r["model"], r["variant"], r["threshold"])
        buckets.setdefault(key, []).append(r["metrics"])

    rows = []
    for (model, variant, threshold), ms in buckets.items():
        row = {
            "model": model, "variant": variant, "threshold": threshold,
            "n_trials": len(ms),
            "mean_rmse_mm": float(np.mean([m["rmse_mm"] for m in ms])),
        }
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


def print_table(rows, pck_thresholds):
    print("=" * 78)
    print(" EVALUATION SUMMARY  (mean over trials; RMSE in mm; lower RMSE = better)")
    print("=" * 78)
    if not rows:
        print(" No prediction/ground-truth pairs were successfully scored.")
        return
    pck_cols = "  ".join(f"PCK@{thr:g}" for thr in pck_thresholds)
    print(f" {'model':<10}{'var':<8}{'thr':<6}{'n':<3} {'RMSE_mm':>8}  {pck_cols}")
    print("-" * 78)
    for r in rows:
        pck_vals = "  ".join(f"{r[f'pck@{thr:g}mm']:6.2%}" for thr in pck_thresholds)
        print(f" {r['model']:<10}{str(r['variant']):<8}{str(r['threshold']):<6}"
              f"{r['n_trials']:<3} {r['mean_rmse_mm']:8.2f}  {pck_vals}")
    best = rows[0]
    print("-" * 78)
    print(f" BEST: {best['model']} / variant {best['variant']} / threshold "
          f"{best['threshold']}  ->  {best['mean_rmse_mm']:.2f} mm mean RMSE")
    print("=" * 78)


# =============================================================================
# Main
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Score grid predictions vs OptiTrack.")
    parser.add_argument("--root", default="Recordings",
                        help="Root folder to scan (default: Recordings).")
    parser.add_argument("--out", default="metrics_summary.csv",
                        help="Summary CSV output path.")
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
    print(f"Found {len(matches)} prediction/ground-truth pair(s) under {args.root}.")

    results = []
    skipped = 0
    for mt in matches:
        try:
            metrics = evaluate_pair(mt["pred_csv"], mt["gt_csv"], pck_thresholds)
        except Exception as e:
            metrics = {"status": "error", "reason": f"{type(e).__name__}: {e}"}
        if metrics.get("status") != "ok":
            skipped += 1
            print(f"  [skip] {os.path.basename(mt['pred_csv'])}: "
                  f"{metrics.get('reason', metrics.get('status'))}")
        results.append({**mt, "metrics": metrics})

    rows = aggregate(results, pck_thresholds)
    export_summary(rows, args.out, pck_thresholds)
    if args.detail:
        export_detail(results, args.detail, pck_thresholds)

    print_table(rows, pck_thresholds)
    print(f"\nScored {len(results) - skipped}/{len(results)} pairs. "
          f"Summary written to: {args.out}")
    if args.detail:
        print(f"Per-pair detail written to: {args.detail}")


if __name__ == "__main__":
    main()
