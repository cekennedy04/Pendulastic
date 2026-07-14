"""
Overlay OptiTrack ground-truth knee flexion (rest=0 convention) against every
pose-estimation model's knee angle for a given trial, and compute RMSE.

Usage:
    python plot_model_vs_optitrack_comparison.py <participant_dir> <pos> <trial> [optitrack_csv]

Example:
    python plot_model_vs_optitrack_comparison.py Participant_4_left Pos_1 T_2 \
        OptiTrack_Recordings/Participant_4_left/Position_1/Height_Joint-Level/trial_2_optitrack_left_redid.csv
"""
import os
import re
import sys
import glob

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pendulastic_pt_score as pts

REC_ROOT = "Recordings"


def load_model_csv(path: str, ref_window_s: float = 1.0):
    """Return (t_sec, flexion_deg) where flexion_deg uses rest=0 convention
    (increasing = more knee flexion), matching the OptiTrack signal."""
    df = pd.read_csv(path)
    t = df["time_sec"].values.astype(float)
    raw = df["knee_angle_deg"].values.astype(float)
    if np.all(np.isnan(raw)):
        return t, raw
    ref_mask = t < ref_window_s
    valid_in_ref = np.isfinite(raw) & ref_mask
    # Widen the reference window until it contains enough valid samples
    # (handles models that drop detections in the first second)
    window = ref_window_s
    while valid_in_ref.sum() < 3 and window < t.max():
        window *= 2
        ref_mask = t < window
        valid_in_ref = np.isfinite(raw) & ref_mask
    if valid_in_ref.sum() < 3:
        valid_in_ref = np.isfinite(raw)
        if valid_in_ref.sum() == 0:
            return t, np.full_like(raw, np.nan)
    neutral = np.nanmedian(raw[valid_in_ref])
    flexion = neutral - raw   # model angle decreases with flexion -> flip sign
    return t, flexion


def compute_rmse(t_ref, ang_ref, t_mdl, ang_mdl):
    """Interpolate GT onto model's time grid and compute RMSE over the
    overlapping, non-NaN range."""
    valid = np.isfinite(ang_mdl)
    if valid.sum() < 5:
        return np.nan, None, None
    t_lo = max(t_ref.min(), t_mdl[valid].min())
    t_hi = min(t_ref.max(), t_mdl[valid].max())
    mask = valid & (t_mdl >= t_lo) & (t_mdl <= t_hi)
    if mask.sum() < 5:
        return np.nan, None, None
    ref_interp = np.interp(t_mdl[mask], t_ref, ang_ref)
    diff = ang_mdl[mask] - ref_interp
    rmse = float(np.sqrt(np.mean(diff ** 2)))
    return rmse, t_mdl[mask], ang_mdl[mask]


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    pid, pos, trial = sys.argv[1], sys.argv[2], sys.argv[3]
    opti_path = sys.argv[4] if len(sys.argv) > 4 else None

    trial_dir = None
    for root, _, files in os.walk(REC_ROOT):
        if pid in root and pos.replace("Pos_", "Position_") in root.replace(
            "Pos", "Pos"
        ):
            trial_dir = root
    # Simpler: find directory containing files matching the pattern
    pattern = f"P_{pid.split('Participant_')[-1]}_{pos}_H_*_{trial}_*.csv"
    candidates = glob.glob(os.path.join(REC_ROOT, "**", pattern), recursive=True)
    if not candidates:
        print(f"No model CSVs found matching pattern: {pattern}")
        sys.exit(1)

    model_files = sorted(
        f for f in candidates if "annotated" not in f and f.endswith(".csv")
    )
    print(f"Found {len(model_files)} model CSVs:")
    for f in model_files:
        print(f"  {f}")

    if opti_path is None:
        print("No OptiTrack CSV given; skipping ground-truth overlay.")
        t_opti, ang_opti = None, None
    else:
        t_opti, ang_opti = pts.load_optitrack(opti_path)

    # First pass: load every model and compute RMSE before plotting, so we
    # can scale the y-axis off only OptiTrack + reasonably-tracking models.
    loaded = []
    for f in model_files:
        name = re.sub(r"^P_.*?_H_.*?_T_\d+_", "", os.path.basename(f)).replace(".csv", "")
        t_m, ang_m = load_model_csv(f)
        if np.all(np.isnan(ang_m)):
            print(f"  [SKIP] {name}: all-NaN (model failed to detect joints)")
            loaded.append({"model": name, "t": t_m, "ang": ang_m, "rmse": np.nan,
                           "note": "all-NaN / detection failed"})
            continue
        rmse = np.nan
        if t_opti is not None:
            rmse, _, _ = compute_rmse(t_opti, ang_opti, t_m, ang_m)
        loaded.append({"model": name, "t": t_m, "ang": ang_m, "rmse": rmse, "note": ""})
        print(f"  {name:25s} RMSE={rmse:.1f} deg" if np.isfinite(rmse) else f"  {name:25s} RMSE=n/a")

    fig, ax = plt.subplots(figsize=(13, 6.5))
    if t_opti is not None:
        ax.plot(t_opti, ang_opti, color="black", lw=2.2, label="OptiTrack (ground truth)", zorder=10)

    colors = plt.cm.tab10(np.linspace(0, 1, max(len(loaded), 1)))
    results = []
    for i, r in enumerate(loaded):
        if np.all(np.isnan(r["ang"])):
            results.append({"model": r["model"], "rmse": np.nan, "note": r["note"]})
            continue
        label = f"{r['model']} (RMSE={r['rmse']:.1f}°)" if np.isfinite(r["rmse"]) else f"{r['model']} (RMSE=n/a)"
        ax.plot(r["t"], r["ang"], lw=1.1, alpha=0.85, color=colors[i], label=label)
        results.append({"model": r["model"], "rmse": r["rmse"], "note": ""})

    ax.axhline(0, color="gray", ls="--", lw=0.8)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Knee flexion from rest (deg)")
    ax.set_title(f"{pid} / {pos} / {trial} — model knee angle vs OptiTrack ground truth")
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)

    # Clip the visible y-range to OptiTrack + models with reasonable RMSE so a
    # badly-failed model (huge noise) doesn't squash the readable detail.
    finite_rmse = [r["rmse"] for r in loaded if np.isfinite(r["rmse"])]
    rmse_cutoff = np.nanmedian(finite_rmse) * 2.5 if finite_rmse else np.inf
    ref_for_scale = [ang_opti] if t_opti is not None else []
    ref_for_scale += [r["ang"] for r in loaded
                       if np.isfinite(r["rmse"]) and r["rmse"] <= rmse_cutoff]
    if ref_for_scale:
        lo = min(np.nanpercentile(a, 1) for a in ref_for_scale)
        hi = max(np.nanpercentile(a, 99) for a in ref_for_scale)
        pad = 0.15 * (hi - lo)
        ax.set_ylim(lo - pad, hi + pad)

    plt.tight_layout()

    out_dir = "Model_Analysis_Outputs/PT_Scores"
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{pid}_{pos}_{trial}_model_comparison.png")
    plt.savefig(out_path, dpi=150)
    print(f"\nSaved -> {out_path}")

    res_df = pd.DataFrame(results).sort_values("rmse")
    csv_path = os.path.join(out_dir, f"{pid}_{pos}_{trial}_model_rmse.csv")
    res_df.to_csv(csv_path, index=False)
    print(f"Saved -> {csv_path}")
    print()
    print(res_df.to_string(index=False))


if __name__ == "__main__":
    main()
