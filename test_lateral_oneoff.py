"""
test_lateral_oneoff.py
======================
One-off diagnostic: does medial-lateral (out-of-plane) motion correlate with
angular error in RTMPose, MoveNet, and MediaPipe?

Participant 0 / Position 1 / Trial 1 only.
Outputs:
  oneoff_lateral_check.png        — original 4-panel diagnostic
  lateral_impact_presentation.png — clean 2-panel presentation version

Run with:
    C:\\Users\\cladi\\miniconda3\\python.exe test_lateral_oneoff.py
"""

from __future__ import annotations

import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from scipy.spatial.transform import Rotation as R

# ─── paths ──────────────────────────────────────────────────────────────────

OPTI_BASE  = r"C:\Users\cladi\Pendulastic\OptiTrack_Recordings\Participant_1\Position_2\Height_Joint-Level"
MODEL_BASE = r"C:\Users\cladi\Pendulastic\Recordings\Participant_1\Position_2\Height_Joint-Level"
LABEL      = "P1_Pos2_T1"   # used in output filenames

OPTITRACK_CSV = os.path.join(OPTI_BASE, "trial_1_optitrack.csv")

MODEL_CSVS = {
    "RTMPose-L":  os.path.join(MODEL_BASE, "P_1_Pos_2_H_Joint-Level_T_1_rtmpose_l_0.35.csv"),
    "MoveNet":    os.path.join(MODEL_BASE, "P_1_Pos_2_H_Joint-Level_T_1_movenet_lightning_0.3.csv"),
    "MediaPipe":  os.path.join(MODEL_BASE, "P_1_Pos_2_H_Joint-Level_T_1_mediapipe_0_0.5.csv"),
}

OUT_PNG  = os.path.join(r"C:\Users\cladi\Pendulastic", f"oneoff_lateral_check_{LABEL}.png")
OUT_PRES = os.path.join(r"C:\Users\cladi\Pendulastic", f"lateral_impact_presentation_{LABEL}.png")

LATERAL_ONSET = 3.83  # seconds — auto-detected from P1 ShankZ deviation threshold

# Desaturated palette — readable at conference-poster size
COLORS = {
    "RTMPose-L": "#4878A8",   # steel blue
    "MoveNet":   "#C87048",   # burnt sienna
    "MediaPipe": "#5A9068",   # sage green
    "OptiTrack": "#1A1A1A",   # near-black
    "LateralZ":  "#4A7C7C",   # dark teal (right-axis lateral trace)
}

# ─── 1. Load OptiTrack ───────────────────────────────────────────────────────

def _find_header(path: str) -> int:
    with open(path, encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                return i
    raise ValueError(f"No 'Frame' header row found in {path}")


def load_optitrack(path: str) -> dict:
    """Returns dict with time array and per-axis position arrays."""
    hi = _find_header(path)
    df = pd.read_csv(path, skiprows=hi, header=0)
    df = df.apply(pd.to_numeric, errors="coerce").ffill().bfill()

    t = df.iloc[:, 1].values.astype(float)
    t = t - t[0]

    # Thigh rigid body: rotation cols 2-5, position cols 6-8
    tx, ty, tz, tw = (df.iloc[:, c].values.astype(float) for c in [2, 3, 4, 5])
    thigh_x = df.iloc[:, 6].values.astype(float)
    thigh_y = df.iloc[:, 7].values.astype(float)
    thigh_z = df.iloc[:, 8].values.astype(float)

    # Shank rigid body: rotation cols 9-12, position cols 13-15
    sx, sy, sz, sw = (df.iloc[:, c].values.astype(float) for c in [9, 10, 11, 12])
    shank_x = df.iloc[:, 13].values.astype(float)
    shank_y = df.iloc[:, 14].values.astype(float)
    shank_z = df.iloc[:, 15].values.astype(float)

    # Ground-truth knee angle from relative quaternion magnitude
    r_thigh = R.from_quat(np.column_stack([tx, ty, tz, tw]))
    r_shank = R.from_quat(np.column_stack([sx, sy, sz, sw]))
    knee_deg = np.degrees((r_thigh.inv() * r_shank).magnitude())

    return dict(
        t=t,
        thigh_x=thigh_x, thigh_y=thigh_y, thigh_z=thigh_z,
        shank_x=shank_x, shank_y=shank_y, shank_z=shank_z,
        knee_deg=knee_deg,
    )


# ─── 2. Load 2D model CSVs ──────────────────────────────────────────────────

def load_model(path: str) -> dict:
    df = pd.read_csv(path)
    t   = df["time_sec"].values.astype(float)
    ang = df["knee_angle_deg"].values.astype(float)

    hip_x   = df["hip_x"].values.astype(float)
    hip_y   = df["hip_y"].values.astype(float)
    knee_x  = df["knee_x"].values.astype(float)
    knee_y  = df["knee_y"].values.astype(float)
    ankle_x = df["ankle_x"].values.astype(float)
    ankle_y = df["ankle_y"].values.astype(float)

    # Apparent 2D segment lengths (pixels)
    thigh_len = np.hypot(knee_x - hip_x,   knee_y - hip_y)
    shank_len = np.hypot(ankle_x - knee_x, ankle_y - knee_y)

    # Normalise to mean of first 15% of valid frames to show relative change
    n15 = max(5, int(0.15 * len(thigh_len)))
    thigh_norm = thigh_len / np.nanmean(thigh_len[:n15])
    shank_norm = shank_len / np.nanmean(shank_len[:n15])

    return dict(t=t, angle=ang,
                thigh_len=thigh_len, shank_len=shank_len,
                thigh_norm=thigh_norm, shank_norm=shank_norm)


# ─── 3. Time-align OptiTrack ← model timestamps ─────────────────────────────

def interp_opti_to_model(opti: dict, model_t: np.ndarray) -> np.ndarray:
    """Linearly interpolate OptiTrack knee_deg onto model time grid."""
    t_ref = opti["t"]
    t_clip = np.clip(model_t, t_ref[0], t_ref[-1])
    fn = interp1d(t_ref, opti["knee_deg"], kind="linear", fill_value="extrapolate")
    return fn(t_clip)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _spine_clean(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def _vline(ax, x, label, color="#555555"):
    ax.axvline(x, color=color, lw=1.4, ls="--", zorder=3)
    ax.text(
        x + 0.08, ax.get_ylim()[1] * 0.97, label,
        fontsize=10, color=color, va="top", ha="left",
        style="italic",
    )


# ─── 4a. Original diagnostic figure (unchanged logic) ────────────────────────

def _build_diagnostic(opti, models):
    opti_t = opti["t"]
    fig = plt.figure(figsize=(14, 16), facecolor="white")
    fig.suptitle(
        f"Lateral-Motion Diagnostic  —  {LABEL.replace('_', ' ')}\n"
        "(OptiTrack Z-axis = medial-lateral / webcam depth)",
        fontsize=13, fontweight="bold", y=0.995,
    )
    gs = gridspec.GridSpec(
        4, 2, figure=fig, height_ratios=[1, 1, 1, 1.2],
        hspace=0.48, wspace=0.32, left=0.08, right=0.97, top=0.96, bottom=0.05,
    )
    ax_lat  = fig.add_subplot(gs[0, :])
    ax_fsh  = fig.add_subplot(gs[1, :])
    ax_ang  = fig.add_subplot(gs[2, :])
    ax_scat = fig.add_subplot(gs[3, 0])
    ax_corr = fig.add_subplot(gs[3, 1])

    ax_lat.plot(opti_t, opti["thigh_z"]*100, color="#9E9E9E", lw=1.2, label="Thigh Z")
    ax_lat.plot(opti_t, opti["shank_z"]*100, color="#212121", lw=1.5, label="Shank Z")
    ax_lat.fill_between(opti_t, opti["thigh_z"]*100, opti["shank_z"]*100,
                        alpha=0.12, color="#607D8B", label="Lateral separation")
    ax_lat.set_ylabel("Depth / Z position (cm)", fontsize=9)
    ax_lat.set_title("3D Medial-Lateral Trajectory (OptiTrack Z-axis)", fontsize=10)
    ax_lat.legend(fontsize=8, loc="upper right"); ax_lat.set_xlim(opti_t[0], opti_t[-1])
    ax_lat.grid(True, alpha=0.3)

    for label, m in models.items():
        col = COLORS[label]
        ax_fsh.plot(m["t"], m["thigh_norm"], color=col, lw=1.2, ls="--", alpha=0.7, label=f"{label} thigh")
        ax_fsh.plot(m["t"], m["shank_norm"], color=col, lw=1.5, label=f"{label} shank")
    ax_fsh.axhline(1.0, color="black", lw=0.8, ls=":", alpha=0.5, label="baseline")
    ax_fsh.set_ylabel("Normalised pixel length", fontsize=9)
    ax_fsh.set_title("2D Apparent Segment Lengths — Perspective Foreshortening", fontsize=10)
    ax_fsh.legend(fontsize=7, loc="upper right", ncol=2)
    ax_fsh.set_xlim(opti_t[0], opti_t[-1]); ax_fsh.set_ylim(0.3, 1.5); ax_fsh.grid(True, alpha=0.3)

    ax_ang.plot(opti_t, opti["knee_deg"], color=COLORS["OptiTrack"], lw=2, zorder=5, label="OptiTrack GT")
    for label, m in models.items():
        ang = m["angle"].copy(); ang[~np.isfinite(ang)] = np.nan
        ax_ang.plot(m["t"], ang, color=COLORS[label], lw=1.3, alpha=0.85, label=label)
    ax_ang.set_ylabel("Knee angle (°)", fontsize=9); ax_ang.set_xlabel("Time (s)", fontsize=9)
    ax_ang.set_title("Knee Flexion Angle — Ground Truth vs 2D Models", fontsize=10)
    ax_ang.legend(fontsize=8, loc="upper right"); ax_ang.set_xlim(opti_t[0], opti_t[-1]); ax_ang.grid(True, alpha=0.3)

    pearson_rs = {}
    for label, m in models.items():
        col = COLORS[label]; m_t = m["t"]; m_ang = m["angle"]
        t_clip = np.clip(m_t, opti_t[0], opti_t[-1])
        shank_z_interp = interp1d(opti_t, opti["shank_z"]*100, kind="linear", fill_value="extrapolate")(t_clip)
        lat_dev = np.abs(shank_z_interp - np.nanmean(shank_z_interp))
        gt_interp = interp_opti_to_model(opti, m_t); ang_err = np.abs(m_ang - gt_interp)
        valid = np.isfinite(ang_err) & np.isfinite(lat_dev); x_v, y_v = lat_dev[valid], ang_err[valid]
        ax_scat.scatter(x_v, y_v, color=col, s=4, alpha=0.35, label=label)
        if len(x_v) > 10:
            r = float(np.corrcoef(x_v, y_v)[0, 1]); pearson_rs[label] = r
            p = np.polyfit(x_v, y_v, 1); xs = np.linspace(x_v.min(), x_v.max(), 100)
            ax_scat.plot(xs, np.polyval(p, xs), color=col, lw=1.5, alpha=0.8)
    ax_scat.set_xlabel("Lateral deviation |shank Z − mean| (cm)", fontsize=9)
    ax_scat.set_ylabel("Angle error |model − GT| (°)", fontsize=9)
    ax_scat.set_title("Lateral Displacement vs Angular Error", fontsize=10)
    ax_scat.legend(fontsize=8, markerscale=3); ax_scat.grid(True, alpha=0.3)

    if pearson_rs:
        labels_r = list(pearson_rs.keys()); vals_r = [pearson_rs[k] for k in labels_r]
        bars = ax_corr.barh(labels_r, vals_r, color=[COLORS[k] for k in labels_r], edgecolor="white", height=0.5)
        ax_corr.axvline(0, color="black", lw=0.8); ax_corr.set_xlim(-1, 1)
        ax_corr.set_xlabel("Pearson r  (lateral deviation vs angle error)", fontsize=9)
        ax_corr.set_title("Correlation Strength", fontsize=10); ax_corr.grid(True, axis="x", alpha=0.3)
        for bar, val in zip(bars, vals_r):
            ax_corr.text(val + (0.03 if val >= 0 else -0.03), bar.get_y() + bar.get_height()/2,
                         f"{val:.2f}", va="center", ha=("left" if val >= 0 else "right"),
                         fontsize=9, fontweight="bold")

    fig.savefig(OUT_PNG, dpi=150, bbox_inches="tight")
    print(f"Saved: {OUT_PNG}")


# ─── 4b. Presentation figure ─────────────────────────────────────────────────

def _build_presentation(opti, models):
    opti_t    = opti["t"]
    t_max     = opti_t[-1]
    opti_knee = opti["knee_deg"]

    # Baseline lateral reference: mean of first 0.5 s (60 frames at 120 Hz)
    n_base = min(60, len(opti["shank_z"]))
    lat_baseline = np.nanmean(opti["shank_z"][:n_base]) * 100          # cm
    lat_shift    = opti["shank_z"] * 100 - lat_baseline                  # cm deviation

    # Interpolate each model's angle onto the OptiTrack time grid
    # (higher resolution → cleaner fill_between shading)
    model_on_opti: dict[str, np.ndarray] = {}
    for label, m in models.items():
        fn = interp1d(m["t"], m["angle"], kind="linear",
                      bounds_error=False, fill_value=np.nan)
        model_on_opti[label] = fn(opti_t)

    # Pre-compute per-model absolute error on the model's own time grid
    model_err: dict[str, tuple] = {}   # label -> (t, err)
    for label, m in models.items():
        m_t   = m["t"]
        m_ang = m["angle"].copy()
        m_ang[~np.isfinite(m_ang)] = np.nan
        gt_i  = interp_opti_to_model(opti, m_t)
        err   = np.abs(m_ang - gt_i)
        err[~np.isfinite(err)] = np.nan
        model_err[label] = (m_t, err)

    # ── Figure ───────────────────────────────────────────────────────────────
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(13, 11), facecolor="white",
        gridspec_kw=dict(hspace=0.52, left=0.09, right=0.88, top=0.93, bottom=0.07),
    )

    fig.suptitle(
        "Out-of-Plane Motion Causes Angular Illusion in 2D Pose Models\n"
        f"{LABEL.replace('_', ' ')}  —  OptiTrack 120 Hz ground truth",
        fontsize=14, fontweight="bold",
    )

    # ── Panel 1: The Angular Illusion ────────────────────────────────────────
    # Red error shading first (behind everything)
    for label, ang_interp in model_on_opti.items():
        valid = np.isfinite(ang_interp)
        ax1.fill_between(
            opti_t, opti_knee, ang_interp,
            where=valid,
            color="#CC3333", alpha=0.10,
            linewidth=0, zorder=1,
        )

    # OptiTrack GT
    ax1.plot(opti_t, opti_knee,
             color=COLORS["OptiTrack"], lw=2.8, zorder=5,
             label="OptiTrack GT  (quaternion)")

    # Model lines
    for label, ang_interp in model_on_opti.items():
        ax1.plot(opti_t, ang_interp,
                 color=COLORS[label], lw=1.8, zorder=4, label=label)

    # Vertical onset line
    ax1.axvline(LATERAL_ONSET, color="#C0392B", lw=1.6, ls="--", zorder=6)
    # Place the label just above the x-axis so it doesn't clash with oscillating lines
    ax1.text(
        LATERAL_ONSET + 0.12, ax1.get_ylim()[0] + 4,
        "Out-of-Plane\nMovement Begins",
        fontsize=10, color="#C0392B", va="bottom", ha="left", style="italic",
        zorder=7,
    )

    ax1.set_xlim(0, t_max)
    ax1.set_ylabel("Knee Flexion Angle (°)", fontsize=12)
    ax1.set_title("Panel 1 — The Angular Illusion", fontsize=12, fontweight="bold", pad=8)
    ax1.tick_params(labelsize=11)
    ax1.legend(
        fontsize=10, loc="upper left",
        framealpha=0.92, edgecolor="#CCCCCC",
    )
    ax1.grid(True, alpha=0.25, linewidth=0.7)
    _spine_clean(ax1)

    # Re-draw the vertical line label now that ylim is set
    ybot = ax1.get_ylim()[0]
    for txt in ax1.texts:
        txt.set_y(ybot + 4)

    # ── Panel 2: Why It Fails ────────────────────────────────────────────────
    # Left axis: absolute angular error per model
    for label, (m_t, err) in model_err.items():
        ax2.plot(m_t, err, color=COLORS[label], lw=1.8, label=label, zorder=4)

    ax2.set_xlim(0, t_max)
    ax2.set_ylim(bottom=0)
    ax2.set_ylabel("Absolute Angular Error  |model − GT|  (°)", fontsize=12, color="#333333")
    ax2.tick_params(axis="y", labelsize=11, labelcolor="#333333")
    ax2.tick_params(axis="x", labelsize=11)
    ax2.set_xlabel("Time (s)", fontsize=12)
    ax2.set_title("Panel 2 — Why It Fails: Error vs Lateral Shift", fontsize=12, fontweight="bold", pad=8)
    ax2.grid(True, alpha=0.25, linewidth=0.7)
    _spine_clean(ax2)

    # Right axis: OptiTrack lateral Z shift
    ax2r = ax2.twinx()
    ax2r.plot(opti_t, lat_shift,
              color=COLORS["LateralZ"], lw=2.2, ls="--",
              alpha=0.85, label="Lateral Z shift (OptiTrack)", zorder=5)
    ax2r.fill_between(opti_t, 0, lat_shift,
                      color=COLORS["LateralZ"], alpha=0.12, zorder=2)
    ax2r.axhline(0, color=COLORS["LateralZ"], lw=0.8, alpha=0.4)
    ax2r.set_ylabel("Shank Z lateral shift (cm)\nrelative to baseline",
                    fontsize=11, color=COLORS["LateralZ"])
    ax2r.tick_params(axis="y", labelsize=11, labelcolor=COLORS["LateralZ"])
    ax2r.spines["top"].set_visible(False)

    # Vertical onset line on panel 2
    ax2.axvline(LATERAL_ONSET, color="#C0392B", lw=1.6, ls="--", zorder=6)
    ax2.text(
        LATERAL_ONSET + 0.12, ax2.get_ylim()[1] * 0.92,
        "Out-of-Plane\nMovement Begins",
        fontsize=10, color="#C0392B", va="top", ha="left", style="italic",
        zorder=7,
    )

    # Combined legend: left + right axis handles
    handles_l, labels_l = ax2.get_legend_handles_labels()
    handles_r, labels_r = ax2r.get_legend_handles_labels()
    ax2.legend(
        handles_l + handles_r, labels_l + labels_r,
        fontsize=10, loc="upper left",
        framealpha=0.92, edgecolor="#CCCCCC",
    )

    # ── Caption ──────────────────────────────────────────────────────────────
    fig.text(
        0.5, 0.005,
        "Red shading (Panel 1): angular gap between OptiTrack ground truth and 2D model estimate.  "
        "Dashed teal (Panel 2): shank medial-lateral depth deviation (OptiTrack Z-axis, webcam out-of-plane direction).",
        ha="center", va="bottom", fontsize=8.5, color="#555555", style="italic",
        wrap=True,
    )

    fig.savefig(OUT_PRES, dpi=150, bbox_inches="tight")
    print(f"Saved: {OUT_PRES}")


# ─── 5. Entry point ──────────────────────────────────────────────────────────

def main():
    print("Loading OptiTrack...")
    opti = load_optitrack(OPTITRACK_CSV)

    models = {}
    for label, path in MODEL_CSVS.items():
        if not os.path.isfile(path):
            print(f"  SKIP (not found): {path}")
            continue
        print(f"Loading {label}...")
        models[label] = load_model(path)

    if not models:
        sys.exit("No model CSVs found — check paths.")

    print("\nBuilding diagnostic figure...")
    _build_diagnostic(opti, models)

    print("Building presentation figure...")
    _build_presentation(opti, models)

    print("\nDone.")


if __name__ == "__main__":
    main()
