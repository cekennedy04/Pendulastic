#!/usr/bin/env python3
"""
validate_tracking.py
====================
Validates that batch_mediapipe.py is tracking the PATIENT (not the assessor)
by cross-correlating HPE knee angles against OptiTrack ground truth — without
requiring gen_training_data.py to be re-run first.

Reads directly from:
  Recordings/**/*_mediapipe_full_0.5.csv   (batch_mediapipe.py output)
  OptiTrack_Recordings/**/*_optitrack.csv  (ground truth)

Pass criteria — "fix is working":
  - >= 75% of trials have |r| >= 0.65 after optimal lag + sagittal flip
  - Median |r| >= 0.65
  - Flip rate drops (was 11/32 with assessor tracking; should be <= 6/N with patient)

Secondary checks per trial:
  - Ankle jitter   : median frame-to-frame ankle displacement (px) — should be smooth
  - Shank CV       : coefficient of variation of shank length — should be < 0.15

Outputs:
  figures/validation_summary.png    bar chart of per-trial |r|
  figures/validation_timeseries.png time-series overlay for top 6 trials

Usage:
  .venv\\Scripts\\python.exe validate_tracking.py
  .venv\\Scripts\\python.exe validate_tracking.py --max-lag 150 --min-r 0.65
"""

import sys
import re
import json
import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT      = Path(__file__).parent
REC_ROOT  = ROOT / "Recordings"
OPTI_ROOT = ROOT / "OptiTrack_Recordings"
FIG_DIR   = ROOT / "figures"

sys.path.insert(0, str(ROOT))
from pendulastic_pt_score import load_optitrack  # noqa: E402

MIN_OVERLAP = 30   # minimum overlapping frames to bother aligning


# ── Discovery ─────────────────────────────────────────────────────────────────

def discover_pairs():
    """Yield (hpe_path, opti_path, label) for all matched trial pairs.

    Deduplicates by opti_path: when multiple HPE CSVs exist in the same
    directory for the same trial number (e.g. two naming conventions),
    only the alphabetically-first one is used.  Labels include the
    position sub-directory so Pos1/T1 and Pos2/T1 are distinct.
    """
    seen_opti: set = set()
    for hpe_path in sorted(REC_ROOT.rglob("*_mediapipe_full_0.5.csv")):
        m = re.search(r"_T_(\d+)_mediapipe", hpe_path.name)
        if not m:
            continue
        trial_n = int(m.group(1))
        rel = hpe_path.parent.relative_to(REC_ROOT)
        opti_dir = OPTI_ROOT / rel
        # Support both 'Trial_N' and 'trial_N' naming conventions
        opti_path = None
        for candidate in (f"Trial_{trial_n}_optitrack.csv", f"trial_{trial_n}_optitrack.csv"):
            p = opti_dir / candidate
            if p.exists():
                opti_path = p
                break
        if opti_path is None:
            continue
        if opti_path in seen_opti:
            continue
        seen_opti.add(opti_path)
        parts = rel.parts  # e.g. ('Participant_0_control', 'Position_1', 'Height_Joint-Level')
        participant = parts[0] if parts else "unknown"
        position = parts[1] if len(parts) > 1 else ""
        pos_tag = f"_Pos{position[-1]}" if position.startswith("Position_") else ""
        label = f"{participant}{pos_tag}_T{trial_n}"
        yield hpe_path, opti_path, label


# ── Cross-correlation ─────────────────────────────────────────────────────────

def pearson_at_lags(x, y, max_lag):
    """Return (lags, r_values) for integer lags in [-max_lag, +max_lag]."""
    n    = len(x)
    lags = np.arange(-max_lag, max_lag + 1, dtype=int)
    rs   = np.full(len(lags), np.nan, dtype=np.float64)
    for i, L in enumerate(lags):
        if L > 0:
            xa, ya = x[L:], y[:-L]
        elif L < 0:
            xa, ya = x[:n + L], y[-L:]
        else:
            xa, ya = x, y
        if min(len(xa), len(ya)) < MIN_OVERLAP:
            continue
        sx, sy = xa.std(ddof=1), ya.std(ddof=1)
        if sx < 1e-6 or sy < 1e-6:
            continue
        rs[i] = float(np.corrcoef(xa, ya)[0, 1])
    return lags, rs


def best_alignment(mp_ang, ot_ang, max_lag):
    """Return dict with lag, flipped, r_best, abs_r that maximise |r|."""
    if len(mp_ang) < MIN_OVERLAP:
        return {"lag": 0, "flipped": False, "r_best": np.nan, "abs_r": 0.0}
    best = {"lag": 0, "flipped": False, "r_best": np.nan, "abs_r": 0.0}
    for flipped in (False, True):
        x = (180.0 - mp_ang) if flipped else mp_ang
        lags, rs = pearson_at_lags(x, ot_ang, max_lag)
        if np.all(np.isnan(rs)):
            continue
        idx = int(np.nanargmax(np.abs(rs)))
        if np.abs(rs[idx]) > best["abs_r"]:
            best.update(lag=int(lags[idx]), flipped=flipped,
                        r_best=float(rs[idx]), abs_r=float(np.abs(rs[idx])))
    return best


# ── Per-trial validation ──────────────────────────────────────────────────────

def validate_trial(hpe_path, opti_path, max_lag=150, cal_coeffs=None):
    """Run all checks on one trial. Returns a stats dict (or {"error": ...})."""
    # Load HPE CSV
    try:
        hpe = pd.read_csv(hpe_path)
        hpe.columns = [c.strip().lower() for c in hpe.columns]
    except Exception as exc:
        return {"error": f"HPE CSV: {exc}"}

    required = {"time_sec", "knee_angle_deg", "knee_x", "knee_y", "ankle_x", "ankle_y"}
    missing = required - set(hpe.columns)
    if missing:
        return {"error": f"missing columns: {missing}"}

    # Load OptiTrack
    try:
        ot_t, ot_ang = load_optitrack(str(opti_path))
    except Exception as exc:
        return {"error": f"OptiTrack: {exc}"}

    if float(np.std(ot_ang)) < 1.0:
        return {"error": f"OptiTrack signal is flat (std={np.std(ot_ang):.2f}°) — recording error"}

    hpe_t  = hpe["time_sec"].values.astype(float)
    mp_ang = hpe["knee_angle_deg"].values.astype(float)

    nan_pct = float(np.isnan(mp_ang).mean())
    if nan_pct > 0.40:
        return {"error": f"HPE coverage too low ({100*nan_pct:.0f}% frames missing) — occlusion/recording error"}

    hpe_std = float(np.nanstd(mp_ang))
    if hpe_std < 5.0:
        return {"error": f"HPE signal flat (std={hpe_std:.2f}°) — assessor may be tracked instead of patient"}

    # Interpolate OT onto HPE frame times
    ot_interp = np.interp(hpe_t, ot_t, ot_ang, left=np.nan, right=np.nan)

    # Keep only frames where both signals are finite
    valid = np.isfinite(mp_ang) & np.isfinite(ot_interp)
    if valid.sum() < MIN_OVERLAP:
        return {"error": f"only {valid.sum()} overlapping frames"}

    mp_v  = mp_ang[valid]
    ot_v  = ot_interp[valid]
    t_v   = hpe_t[valid]

    # Find best lag + orientation
    aln = best_alignment(mp_v, ot_v, max_lag)
    lag     = aln["lag"]
    flipped = aln["flipped"]

    # Apply flip + lag to get aligned pair
    mp_x = (180.0 - mp_v) if flipped else mp_v
    n = len(mp_x)
    if lag > 0:
        mp_al, ot_al, t_al = mp_x[lag:], ot_v[:-lag], t_v[lag:]
    elif lag < 0:
        mp_al, ot_al, t_al = mp_x[:n + lag], ot_v[-lag:], t_v[:n + lag]
    else:
        mp_al, ot_al, t_al = mp_x, ot_v, t_v

    # Final NaN filter
    mask = np.isfinite(mp_al) & np.isfinite(ot_al)
    mp_al, ot_al, t_al = mp_al[mask], ot_al[mask], t_al[mask]
    n_aligned = len(mp_al)

    if n_aligned < MIN_OVERLAP:
        return {"error": f"only {n_aligned} aligned frames after lag"}

    raw_rmse = float(np.sqrt(np.mean((mp_al - ot_al) ** 2)))
    raw_mae  = float(np.mean(np.abs(mp_al - ot_al)))

    # Calibrated RMSE (old calibration — for reference only)
    cal_rmse = np.nan
    if cal_coeffs is not None:
        c0, c1, c2 = cal_coeffs[:3]
        mp_pred = c0 + c1 * mp_al + c2 * mp_al ** 2
        cal_rmse = float(np.sqrt(np.mean((mp_pred - ot_al) ** 2)))

    # Ankle jitter: median frame-to-frame pixel displacement
    ank_x = hpe["ankle_x"].values.astype(float)
    ank_y = hpe["ankle_y"].values.astype(float)
    diffs = np.sqrt(np.diff(ank_x) ** 2 + np.diff(ank_y) ** 2)
    valid_d = diffs[np.isfinite(diffs)]
    ankle_jitter = float(np.median(valid_d)) if len(valid_d) > 0 else np.nan

    # Shank length coefficient of variation (stability check)
    kne_x = hpe["knee_x"].values.astype(float)
    kne_y = hpe["knee_y"].values.astype(float)
    slens = np.sqrt((ank_x - kne_x) ** 2 + (ank_y - kne_y) ** 2)
    valid_sl = slens[np.isfinite(slens)]
    if len(valid_sl) > 5 and np.mean(valid_sl) > 1e-3:
        shank_cv = float(np.std(valid_sl, ddof=1) / np.mean(valid_sl))
    else:
        shank_cv = np.nan

    # Knee x-median (for spatial check: is knee on LEFT/patient side?)
    knee_x_med = float(np.nanmedian(kne_x))

    return {
        "n_frames":        int(len(hpe)),
        "n_valid":         int(valid.sum()),
        "n_aligned":       n_aligned,
        "lag":             aln["lag"],
        "flipped":         aln["flipped"],
        "r_best":          aln["r_best"],
        "abs_r":           aln["abs_r"],
        "raw_rmse":        raw_rmse,
        "raw_mae":         raw_mae,
        "cal_rmse":        cal_rmse,
        "ankle_jitter_px": ankle_jitter,
        "shank_cv":        shank_cv,
        "knee_x_med":      knee_x_med,
        # arrays for plotting
        "_mp_al": mp_al,
        "_ot_al": ot_al,
        "_t_al":  t_al,
    }


# ── Plotting ──────────────────────────────────────────────────────────────────

def plot_summary(results, labels, min_r):
    """Bar chart of |r| per trial, sorted descending."""
    ok = [(lbl, r) for lbl, r in zip(labels, results) if "error" not in r]
    ok.sort(key=lambda x: x[1]["abs_r"], reverse=True)
    if not ok:
        return

    names  = [x[0] for x in ok]
    abs_rs = [x[1]["abs_r"] for x in ok]
    colors = ["#2ecc71" if v >= min_r else "#e74c3c" for v in abs_rs]

    fig, ax = plt.subplots(figsize=(max(12, len(ok) * 0.4), 5))
    bars = ax.bar(range(len(names)), abs_rs, color=colors, edgecolor="white", linewidth=0.5)
    ax.axhline(min_r, color="black", linewidth=1.2, linestyle="--", label=f"|r| = {min_r}")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(names, rotation=75, ha="right", fontsize=7)
    ax.set_ylabel("|Pearson r| (after lag + flip)")
    ax.set_ylim(0, 1.05)
    ax.set_title("Per-trial tracking correlation: MediaPipe vs OptiTrack")
    ax.legend()

    n_pass = sum(v >= min_r for v in abs_rs)
    ax.text(0.98, 0.97, f"{n_pass}/{len(ok)} pass",
            transform=ax.transAxes, ha="right", va="top", fontsize=10,
            color="#2ecc71" if n_pass / len(ok) >= 0.75 else "#e74c3c")

    fig.tight_layout()
    FIG_DIR.mkdir(exist_ok=True)
    fig.savefig(FIG_DIR / "validation_summary.png", dpi=120)
    plt.close(fig)


def plot_timeseries(results, labels, top_n=6):
    """Time-series overlay for the top_n trials by |r|."""
    ok = [(lbl, r) for lbl, r in zip(labels, results) if "error" not in r]
    ok.sort(key=lambda x: x[1]["abs_r"], reverse=True)
    ok = ok[:top_n]
    if not ok:
        return

    ncols = 2
    nrows = (len(ok) + 1) // 2
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 3.5 * nrows), squeeze=False)
    axes_flat = axes.flatten()

    for i, (lbl, r) in enumerate(ok):
        ax = axes_flat[i]
        t  = r["_t_al"]
        mp = r["_mp_al"]
        ot = r["_ot_al"]
        ax.plot(t, ot, color="#e74c3c", linewidth=1.2, label="OptiTrack", alpha=0.85)
        ax.plot(t, mp, color="#3498db", linewidth=1.0, label="MediaPipe (HPE)", alpha=0.85)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Knee angle (deg)")
        flip_tag = " [FLIP]" if r["flipped"] else ""
        ax.set_title(f"{lbl}{flip_tag}\nr={r['r_best']:+.3f}  raw RMSE={r['raw_rmse']:.1f} deg"
                     f"  lag={r['lag']:+d}fr", fontsize=9)
        ax.legend(fontsize=8, loc="upper right")

    # Hide unused panels
    for j in range(len(ok), len(axes_flat)):
        axes_flat[j].set_visible(False)

    fig.suptitle("MediaPipe vs OptiTrack: top trials by correlation", fontsize=12)
    fig.tight_layout()
    FIG_DIR.mkdir(exist_ok=True)
    fig.savefig(FIG_DIR / "validation_timeseries.png", dpi=120)
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-lag", type=int, default=150,
                        help="max lag to search in frames (default 150)")
    parser.add_argument("--min-r",  type=float, default=0.65,
                        help="pass threshold for |r| (default 0.65)")
    args = parser.parse_args()

    # Load calibration coefficients if available (for reference only)
    cal_path   = ROOT / "calibration_coefficients.json"
    cal_coeffs = None
    if cal_path.exists():
        with open(cal_path) as fh:
            raw = json.load(fh)
        cal_coeffs = list(raw["coefficients"] if isinstance(raw, dict) else raw)
        cal_coeffs += [0.0] * max(0, 3 - len(cal_coeffs))  # pad to 3 for degree-1 JSON
        c0, c1, c2 = cal_coeffs[:3]
        print(f"Calibration loaded: ot = {c0:.3f} + {c1:.3f}*mp + {c2:.4f}*mp^2")
        if c1 < -0.5:
            print("  WARNING: c1 is negative -> calibration was trained on ASSESSOR data.")
            print("  Calibrated RMSE below is not meaningful; shown for reference only.\n")
        else:
            print()

    # Discover all pairs
    pairs = list(discover_pairs())
    if not pairs:
        print(f"No paired (HPE CSV, OptiTrack CSV) files found.")
        print(f"  HPE CSVs expected in: {REC_ROOT}")
        print(f"  OptiTrack CSVs expected in: {OPTI_ROOT}")
        return

    print(f"Found {len(pairs)} trial pairs — processing...\n")
    print(f"  {'Label':42s} {'|r|':>5} {'r':>6} {'flip':4} {'lag':>5} "
          f"{'raw_rmse':>9} {'cal_rmse':>9} {'jitter':>8} {'shankCV':>8}")
    print("  " + "-" * 110)

    results = []
    labels  = []
    for hpe_path, opti_path, label in pairs:
        r = validate_trial(hpe_path, opti_path, args.max_lag, cal_coeffs)
        results.append(r)
        labels.append(label)

        if "error" in r:
            print(f"  SKIP  {label:42s} -- {r['error']}")
            continue

        pass_tag = "PASS" if r["abs_r"] >= args.min_r else "FAIL"
        flip_tag = "YES " if r["flipped"] else "    "
        cal_str  = f"{r['cal_rmse']:9.2f}" if not np.isnan(r["cal_rmse"]) else "      N/A"
        ji_str   = f"{r['ankle_jitter_px']:8.1f}" if not np.isnan(r["ankle_jitter_px"]) else "     N/A"
        cv_str   = f"{r['shank_cv']:8.3f}" if not np.isnan(r["shank_cv"]) else "     N/A"

        print(f"  {pass_tag}  {label:42s} {r['abs_r']:5.3f} {r['r_best']:+6.3f} {flip_tag} "
              f"{r['lag']:+5d} {r['raw_rmse']:9.2f}{cal_str}{ji_str}{cv_str}")

    # ── Summary statistics ────────────────────────────────────────────────────
    ok = [r for r in results if "error" not in r]
    if not ok:
        print("\nNo valid trials to summarise.")
        return

    abs_rs    = [r["abs_r"]     for r in ok]
    raw_rmses = [r["raw_rmse"]  for r in ok if not np.isnan(r["raw_rmse"])]
    cal_rmses = [r["cal_rmse"]  for r in ok if not np.isnan(r["cal_rmse"])]
    flipped   = [r["flipped"]   for r in ok]
    n_pass    = sum(v >= args.min_r for v in abs_rs)
    pct_pass  = 100 * n_pass / len(abs_rs)

    print("\n" + "=" * 80)
    print("Summary")
    print("=" * 80)
    print(f"  Valid trials:           {len(ok)} / {len(pairs)}")
    print(f"  Passing |r| >= {args.min_r}:     {n_pass} / {len(ok)}  ({pct_pass:.0f}%)")
    print(f"  Median |r|:             {np.median(abs_rs):.3f}")
    print(f"  Median raw RMSE:        {np.median(raw_rmses):.2f} deg")
    if cal_rmses:
        print(f"  Median cal RMSE:        {np.median(cal_rmses):.2f} deg  (OLD calibration)")
    print(f"  Flip rate:              {sum(flipped)} / {len(flipped)}  ({100*sum(flipped)/len(flipped):.0f}%)")
    print(f"  Median ankle jitter:    {np.median([r['ankle_jitter_px'] for r in ok if not np.isnan(r['ankle_jitter_px'])]):.1f} px")

    # ── Pass/fail verdict ─────────────────────────────────────────────────────
    print()
    med_r = float(np.median(abs_rs))
    fix_ok = pct_pass >= 75.0 and med_r >= 0.65

    if fix_ok:
        print("RESULT: PASS — batch_mediapipe.py is tracking the PATIENT.")
        print()
        print("Next steps to reach RMSE <= 5 deg:")
        print("  1. Regenerate training data (30-90 min):")
        print("       .venv\\Scripts\\python.exe gen_training_data.py")
        print("  2. Align and calibrate:")
        print("       .venv\\Scripts\\python.exe align_and_calibrate.py")
        print("       .venv\\Scripts\\python.exe calibrate.py")
        print("  3. The new calibration_coefficients.json will then have c1 near +1.")
    else:
        print("RESULT: FAIL — tracking may still be picking up the assessor.")
        print(f"  Median |r| = {med_r:.3f} (need >= 0.65)")
        print(f"  Pass rate  = {pct_pass:.0f}% (need >= 75%)")
        print()
        print("  Likely causes:")
        print("  - gen_training_data.py fix not applied (check num_poses and lm_idx selection)")
        print("  - batch_mediapipe.py may not have re-run after the fix")
        print("  - Camera angle may be frontal (not sagittal) for some participants")

    # ── Plots ─────────────────────────────────────────────────────────────────
    print()
    print("Generating figures...")
    plot_summary(results, labels, args.min_r)
    plot_timeseries(results, labels, top_n=6)
    print(f"  Saved to {FIG_DIR}/")
    print(f"    validation_summary.png    -- per-trial |r| bar chart")
    print(f"    validation_timeseries.png -- time-series overlay (top 6 trials)")


if __name__ == "__main__":
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    main()
