#!/usr/bin/env python3
"""
align_and_calibrate.py
======================
Per-trial cross-correlation alignment of 2D MediaPipe knee angles against
3D OptiTrack ground truth in the Pendulastic dataset.

Two systematic artifacts suppress the pooled correlation and must be removed
before any calibration model can be trained:

  1. Sagittal mirroring
     Depending on which direction the patient faces relative to the camera,
     knee extension either increases the 2D image-plane angle (patient facing
     left) or decreases it (patient facing right).  This produces individual
     trials with r ≈ +0.85 and r ≈ -0.83, which cancel when pooled.
     Fix: test both θ and (180° − θ) per trial; keep whichever maximises |r|.

  2. Temporal lag / clock drift
     The video and OptiTrack data streams lack hardware synchronisation.
     The generator (gen_training_data.py) matched them by wall-clock timestamp,
     but residual drift of 15–60+ frames persists per trial.
     Fix: integer-lag cross-correlation sweep over ±max_lag frames.

The two corrections are searched jointly so that neither contaminates the
other's optimum.

Output
------
  aligned_training_data.json   clean, lag-corrected, sign-corrected
                               (mp_angle_aligned, ot_angle) pairs ready
                               for calibration regression.

Usage
-----
  .venv\\Scripts\\python.exe align_and_calibrate.py
  .venv\\Scripts\\python.exe align_and_calibrate.py --max-lag 150 --min-r 0.70
  .venv\\Scripts\\python.exe align_and_calibrate.py --output custom_output.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
COCO_JSON   = ROOT / "training_data" / "annotations" / "coco_keypoints.json"
OUTPUT_JSON = ROOT / "training_data" / "aligned_training_data.json"

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_MAX_LAG = 150   # sweep ±N frames around zero
DEFAULT_MIN_R   = 0.65  # |r| threshold; trials below this are pruned
MIN_OVERLAP     = 30    # min aligned-frame pairs needed to compute r reliably


# ═══════════════════════════════════════════════════════════════════════════════
# Data loading
# ═══════════════════════════════════════════════════════════════════════════════

def load_paired_data(coco_path: Path) -> pd.DataFrame:
    """
    Parse the COCO JSON and return a flat DataFrame with columns:
      trial, frame, mp_angle, ot_angle, hip_vis, knee_vis

    Only rows where both mp_image_angle_deg and knee_angle_deg are non-null
    are included.

    Trial prefix is extracted from the image file_name by splitting on
    '_frame_', e.g.:
      'Participant_4_left_T2_frame_000042.jpg'
        → trial = 'Participant_4_left_T2'
        → frame = 42
    """
    with open(coco_path, encoding="utf-8") as f:
        coco = json.load(f)

    # Map image_id → (trial_prefix, frame_number)
    img_meta: dict[int, tuple[str, int]] = {}
    for img in coco["images"]:
        stem = Path(img["file_name"]).stem          # strip extension
        if "_frame_" in stem:
            trial_prefix, frame_str = stem.rsplit("_frame_", 1)
            frame_num = int(frame_str)
        else:
            # Fallback: use image id as frame index
            trial_prefix = stem
            frame_num    = int(img["id"])
        img_meta[img["id"]] = (trial_prefix, frame_num)

    rows: list[dict] = []
    for ann in coco["annotations"]:
        iid    = ann["image_id"]
        mp_ang = ann.get("mp_image_angle_deg")
        ot_ang = ann.get("knee_angle_deg")
        if mp_ang is None or ot_ang is None:
            continue
        if iid not in img_meta:
            continue
        trial, frame = img_meta[iid]
        rows.append({
            "trial":    trial,
            "frame":    frame,
            "mp_angle": float(mp_ang),
            "ot_angle": float(ot_ang),
            "hip_vis":  float(ann.get("hip_visibility",  0.0)),
            "knee_vis": float(ann.get("knee_visibility", 0.0)),
        })

    df = (pd.DataFrame(rows)
            .sort_values(["trial", "frame"])
            .reset_index(drop=True))
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-correlation
# ═══════════════════════════════════════════════════════════════════════════════

def pearson_at_lags(
    x: np.ndarray,
    y: np.ndarray,
    max_lag: int,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Pearson r between x and y at every integer lag in [-max_lag, +max_lag].

    Sign convention (x = MediaPipe, y = OptiTrack)
    -----------------------------------------------
    Positive lag L  →  x is delayed (lags behind y) by L frames.
                       Overlapping window: x[L:] versus y[:-L].

    Negative lag L  →  y is delayed by |L| frames.
                       Overlapping window: x[:N+L] versus y[-L:].

    Lags where the overlap shrinks below MIN_OVERLAP yield NaN.

    Returns
    -------
    lags : int ndarray  shape (2·max_lag + 1,)
    rs   : float ndarray  same shape, Pearson r or NaN
    """
    n    = len(x)
    lags = np.arange(-max_lag, max_lag + 1, dtype=int)
    rs   = np.full(len(lags), np.nan, dtype=np.float64)

    for i, L in enumerate(lags):
        if L > 0:
            if n - L < MIN_OVERLAP:
                continue
            xa, ya = x[L:], y[:-L]
        elif L < 0:
            if n + L < MIN_OVERLAP:
                continue
            xa, ya = x[:n + L], y[-L:]
        else:
            if n < MIN_OVERLAP:
                continue
            xa, ya = x, y

        sx, sy = xa.std(ddof=1), ya.std(ddof=1)
        if sx < 1e-6 or sy < 1e-6:
            continue                          # constant segment → r undefined

        rs[i] = float(np.corrcoef(xa, ya)[0, 1])

    return lags, rs


def find_best_alignment(
    mp: np.ndarray,
    ot: np.ndarray,
    max_lag: int,
) -> dict:
    """
    Joint grid search over lag ∈ [-max_lag, +max_lag] and orientation ∈ {raw, flipped}.

    The objective is to maximise |Pearson r(mp_transformed, ot)|.

    Returns a dict with:
      lag         — optimal frame shift (positive = mp was delayed)
      flipped     — True if 180° − θ is the better orientation
      r_best      — signed Pearson r at the optimum
      abs_r_best  — |r| at the optimum
      r_original  — baseline r (no lag, no flip)
    """
    if len(mp) < MIN_OVERLAP:
        return {
            "lag": 0, "flipped": False,
            "r_best": np.nan, "abs_r_best": 0.0, "r_original": np.nan,
        }

    r_orig = _safe_pearson(mp, ot)

    best: dict = {
        "lag":        0,
        "flipped":    False,
        "r_best":     r_orig,
        "abs_r_best": abs(r_orig) if not np.isnan(r_orig) else 0.0,
        "r_original": r_orig,
    }

    for flipped in (False, True):
        x         = (180.0 - mp) if flipped else mp
        lags, rs  = pearson_at_lags(x, ot, max_lag)
        if np.all(np.isnan(rs)):
            continue

        idx = int(np.nanargmax(np.abs(rs)))
        if np.abs(rs[idx]) > best["abs_r_best"]:
            best.update(
                lag        = int(lags[idx]),
                flipped    = flipped,
                r_best     = float(rs[idx]),
                abs_r_best = float(np.abs(rs[idx])),
            )

    return best


def _safe_pearson(x: np.ndarray, y: np.ndarray) -> float:
    if x.std(ddof=1) < 1e-6 or y.std(ddof=1) < 1e-6:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


# ═══════════════════════════════════════════════════════════════════════════════
# Apply alignment
# ═══════════════════════════════════════════════════════════════════════════════

def apply_alignment(
    mp: np.ndarray,
    ot: np.ndarray,
    frames: np.ndarray,
    lag: int,
    flipped: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Apply the optimal lag shift and optional sign flip.

    Returns (mp_aligned, ot_aligned, frame_indices_from_mp_side), all of
    the same length — the overlap window after shifting.

    The frame index array follows the mp side so callers can trace each
    aligned pair back to its original video frame.
    """
    x = (180.0 - mp) if flipped else mp.copy()
    n = len(x)
    if lag > 0:
        return x[lag:], ot[:-lag], frames[lag:]
    elif lag < 0:
        return x[:n + lag], ot[-lag:], frames[:n + lag]
    else:
        return x.copy(), ot.copy(), frames.copy()


# ═══════════════════════════════════════════════════════════════════════════════
# Console diagnostics
# ═══════════════════════════════════════════════════════════════════════════════

def print_table(results: list[dict], min_r: float) -> None:
    W = 44   # trial-name column width
    hdr = (
        f"{'Trial':<{W}} {'Lag':>6}  {'Orient':>7}  "
        f"{'|r| opt':>7}  {'r orig':>7}  {'d|r|':>6}  "
        f"{'N raw->N al':>11}  {'Decision':>8}"
    )
    rule = "-" * len(hdr)
    print("\n" + rule)
    print(hdr)
    print(rule)

    for res in results:
        orient   = "FLIP" if res["flipped"] else "normal"
        abs_r    = res["abs_r_best"]
        r_orig   = res["r_original"]
        delta    = abs_r - (abs(r_orig) if not np.isnan(r_orig) else 0.0)
        decision = "KEEP " if abs_r >= min_r else "prune"
        n_al     = res.get("n_aligned", 0)
        print(
            f"{res['trial']:<{W}} {res['lag']:>+6d}  {orient:>7}  "
            f"{abs_r:>7.3f}  "
            f"{(r_orig if not np.isnan(r_orig) else 0.0):>7.3f}  "
            f"{delta:>+6.3f}  "
            f"{res['n_raw']:>4d} -> {n_al:>3d}  "
            f"{decision:>8}"
        )
    print(rule)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Align MediaPipe knee-angle series to OptiTrack via cross-correlation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--coco-json", type=Path, default=COCO_JSON,
                    metavar="PATH", help="COCO annotations file")
    ap.add_argument("--max-lag",   type=int,   default=DEFAULT_MAX_LAG,
                    metavar="N",   help="Lag search window: sweep ±N frames")
    ap.add_argument("--min-r",     type=float, default=DEFAULT_MIN_R,
                    metavar="R",   help="Minimum |r| to retain a trial")
    ap.add_argument("--output",    type=Path,  default=OUTPUT_JSON,
                    metavar="PATH", help="Output JSON path")
    args = ap.parse_args()

    # ── Load ──────────────────────────────────────────────────────────────────
    print(f"\nLoading {args.coco_json} ...")
    if not args.coco_json.exists():
        print(f"ERROR: file not found: {args.coco_json}")
        sys.exit(1)

    df = load_paired_data(args.coco_json)
    if df.empty:
        print("ERROR: no valid paired samples found (both angles non-null).")
        sys.exit(1)

    trials = sorted(df["trial"].unique())
    print(f"  {len(df):,} paired samples  /  {len(trials)} trials\n")

    # ── Per-trial alignment ───────────────────────────────────────────────────
    results: list[dict]  = []
    kept_rows: list[dict] = []
    pruned:   list[str]  = []

    print(f"Sweeping lag window +/-{args.max_lag} frames, |r| threshold {args.min_r} ...\n")

    for trial in trials:
        tdf    = df[df["trial"] == trial]
        mp_arr = tdf["mp_angle"].values.astype(np.float64)
        ot_arr = tdf["ot_angle"].values.astype(np.float64)
        fr_arr = tdf["frame"].values

        res          = find_best_alignment(mp_arr, ot_arr, args.max_lag)
        res["trial"] = trial
        res["n_raw"] = len(mp_arr)

        if res["abs_r_best"] >= args.min_r:
            mp_al, ot_al, fr_al = apply_alignment(
                mp_arr, ot_arr, fr_arr, res["lag"], res["flipped"]
            )
            res["n_aligned"] = len(mp_al)

            # Subset of visibility scores from the mp-side rows after shifting
            lag = res["lag"]
            n   = len(mp_arr)
            if lag > 0:
                hip_vis  = tdf["hip_vis"].values[lag:]
                knee_vis = tdf["knee_vis"].values[lag:]
            elif lag < 0:
                hip_vis  = tdf["hip_vis"].values[:n + lag]
                knee_vis = tdf["knee_vis"].values[:n + lag]
            else:
                hip_vis  = tdf["hip_vis"].values
                knee_vis = tdf["knee_vis"].values

            for k in range(len(mp_al)):
                kept_rows.append({
                    "trial":            trial,
                    "frame_original":   int(fr_al[k]),
                    "mp_angle_aligned": round(float(mp_al[k]), 4),
                    "ot_angle":         round(float(ot_al[k]), 4),
                    "hip_vis":          round(float(hip_vis[k]), 3),
                    "knee_vis":         round(float(knee_vis[k]), 3),
                    "lag_applied":      res["lag"],
                    "flipped":          res["flipped"],
                })
        else:
            res["n_aligned"] = 0
            pruned.append(trial)

        results.append(res)

    # ── Diagnostics ───────────────────────────────────────────────────────────
    print_table(results, args.min_r)

    n_kept  = len(trials) - len(pruned)
    n_pairs = len(kept_rows)
    print(f"\n  Kept   : {n_kept}/{len(trials)} trials  /  {n_pairs:,} aligned frame pairs")
    print(f"  Pruned : {len(pruned)} trial(s)" +
          (f"  [{', '.join(pruned)}]" if pruned else "  [none]"))

    kept_rs  = [r["abs_r_best"] for r in results if r["abs_r_best"] >= args.min_r]
    if kept_rs:
        print(
            f"\n  |r| after alignment:  "
            f"mean = {np.mean(kept_rs):.3f}  "
            f"min = {np.min(kept_rs):.3f}  "
            f"max = {np.max(kept_rs):.3f}"
        )

    n_flipped = sum(1 for r in results
                    if r["flipped"] and r["abs_r_best"] >= args.min_r)
    print(f"  Sagittal-corrected (FLIP) trials: {n_flipped}/{n_kept}\n")

    # ── Save ──────────────────────────────────────────────────────────────────
    def _json_val(v: float) -> float | None:
        return None if (v is None or (isinstance(v, float) and np.isnan(v))) else round(v, 4)

    output = {
        "meta": {
            "source":        str(args.coco_json),
            "max_lag":       args.max_lag,
            "min_r":         args.min_r,
            "total_trials":  len(trials),
            "kept_trials":   n_kept,
            "pruned_trials": pruned,
            "total_samples": n_pairs,
        },
        "trial_alignments": [
            {
                "trial":      r["trial"],
                "lag":        r["lag"],
                "flipped":    r["flipped"],
                "abs_r_best": _json_val(r["abs_r_best"]),
                "r_best":     _json_val(r["r_best"]),
                "r_original": _json_val(r["r_original"]),
                "n_raw":      r["n_raw"],
                "n_aligned":  r["n_aligned"],
                "kept":       r["abs_r_best"] >= args.min_r,
            }
            for r in results
        ],
        "samples": kept_rows,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    print(f"  Saved -> {args.output}\n")


if __name__ == "__main__":
    main()
