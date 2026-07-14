"""
model_vs_optitrack_eval.py
==========================
Evaluates pose-estimation model knee-angle outputs against OptiTrack ground
truth for Participant 2 (healthy) and Participant 4 (MS).

Two graph types per model × trial
  A — Model PT Score: same layout as pendulastic_pt_score.py but driven by
      the model's knee_angle_deg column.  Lets you see whether a given model
      produces the right MAS estimate.

  B — OptiTrack vs Model overlay: both time series on one plot with RMSE
      annotated.  Ground truth (OptiTrack) in gold, model in cyan.

Summary outputs
  • rmse_heatmap.png     — RMSE per model × trial (heatmap)
  • mas_accuracy.png     — MAS estimated by each model vs. OptiTrack MAS
  • model_evaluation.csv — per-trial numerical results

Run:
  $env:OPENBLAS_NUM_THREADS="1"
  .venv\\Scripts\\python.exe model_vs_optitrack_eval.py
"""
from __future__ import annotations

import os
import re
import sys
import glob
import math
from typing import Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as mgridspec
import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

# ── Re-use core computation and plotting utilities from PT-score module ─────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pendulastic_pt_score import (
    compute_pt_params,
    compute_pt_score,
    pt_to_mas,
    load_optitrack,
    HEALTHY_REF,
    _PARAM_KEYS,
    _MAS_COLOR,
    _BG, _PANEL, _TABLE, _HDR,
)

# ── Paths ───────────────────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
REC_ROOT  = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
OUT_DIR   = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Model_Eval")

OUT_PT    = os.path.join(OUT_DIR, "pt_score")
OUT_CMP   = os.path.join(OUT_DIR, "comparison")
for d in (OUT_PT, OUT_CMP):
    os.makedirs(d, exist_ok=True)

# ── Participant labels ───────────────────────────────────────────────────────
PARTICIPANT_LABEL = {}   # populated during discovery; pid_key → "P2 (healthy)" etc.

# Human-readable model display names
_MODEL_DISPLAY = {
    "detrpose_s_0.5":    "DETRPose-S (0.5)",
    "hrnet_w48_0.3":     "HRNet-W48 (0.3)",
    "mediapipe_full_0.5": "MediaPipe Full (0.5)",
    "mediapipe_heavy_0.5":"MediaPipe Heavy (0.5)",
    "mediapipe_lite_0.5": "MediaPipe Lite (0.5)",
    "openpose_body25_0.1":"OpenPose B25 (0.1)",
    "rtmpose_m_0.3":     "RTMPose-M (0.3)",
    "rtmpose_s_0.3":     "RTMPose-S (0.3)",
    "yolo_n_0.5":        "YOLO-N (0.5)",
}


# ════════════════════════════════════════════════════════════════════════════
# Discovery
# ════════════════════════════════════════════════════════════════════════════

_CSV_RE = re.compile(
    r"P_([^_]+(?:_[^_]+)*)_Pos_(\d+)_H_(.+?)_T_(\d+)_(.+)\.csv$",
    re.IGNORECASE,
)

def discover_model_csvs() -> list[dict]:
    """
    Scan Recordings/ for all P2 and P4 model CSVs.
    Returns list of dicts with keys:
      pid, pos, height, trial, model, path, optitrack_path
    Only includes entries where a matching OptiTrack file exists.
    """
    pattern = os.path.join(REC_ROOT, "**", "*.csv")
    results = []

    for path in sorted(glob.glob(pattern, recursive=True)):
        fname = os.path.basename(path)
        m = _CSV_RE.match(fname)
        if not m:
            continue
        pid, pos, height, trial, model = (
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        )
        # Only P2 and P4
        if not (pid.startswith("2") or pid.startswith("4")):
            continue

        opti_path = os.path.join(
            OPTI_ROOT,
            f"Participant_{pid}",
            f"Position_{pos}",
            f"Height_{height}",
            f"trial_{trial}_optitrack.csv",
        )
        if not os.path.exists(opti_path):
            # Try without height subdirectory (some older layouts)
            opti_path2 = os.path.join(
                OPTI_ROOT,
                f"Participant_{pid}",
                f"Position_{pos}",
                f"trial_{trial}_optitrack.csv",
            )
            if os.path.exists(opti_path2):
                opti_path = opti_path2
            else:
                continue  # no matching ground truth

        results.append(dict(
            pid=pid, pos=pos, height=height, trial=trial, model=model,
            path=path, optitrack_path=opti_path,
        ))

    return results


# ════════════════════════════════════════════════════════════════════════════
# Model CSV loading
# ════════════════════════════════════════════════════════════════════════════

def load_model_csv(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Return (t_sec, knee_angle_deg) from a model output CSV."""
    df = pd.read_csv(path)
    t = df["time_sec"].values.astype(float)
    t -= t[0]
    ang = df["knee_angle_deg"].values.astype(float)
    return t, ang


# ════════════════════════════════════════════════════════════════════════════
# RMSE computation (neutral-normalised to compare oscillation dynamics)
# ════════════════════════════════════════════════════════════════════════════

def _neutral_subtract(t: np.ndarray, ang: np.ndarray, params) -> np.ndarray:
    """
    Return angle array with neutral subtracted (φ = ang − neutral).
    Uses the neutral computed by compute_pt_params; falls back to the
    mean of the last 25 % of the signal if params is None.
    """
    if params is not None:
        neut = params["neutral_deg"]
    else:
        tail = ang[int(0.75 * len(ang)):]
        neut = float(np.nanmedian(tail))
    return ang - neut


def compute_rmse(t_ref: np.ndarray, ang_ref: np.ndarray,
                 t_mdl: np.ndarray, ang_mdl: np.ndarray,
                 params_ref=None, params_mdl=None,
                 t_start: float = 0.0) -> Tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    """
    Normalise both signals to their neutral angle (φ = angle − neutral),
    interpolate GT onto the model time grid, compute RMSE over the common
    post-release window.

    Returns (rmse_deg, t_common, phi_ref_interp, phi_mdl_common).
    Comparing φ removes the systematic ~40–50° camera-perspective offset
    between the 3-D OptiTrack angle and the 2-D pixel-space model angle,
    leaving only oscillation-dynamics error.
    """
    phi_ref = _neutral_subtract(t_ref, ang_ref, params_ref)
    phi_mdl = _neutral_subtract(t_mdl, ang_mdl, params_mdl)

    t_lo = max(t_ref[0], t_mdl[0], t_start)
    t_hi = min(t_ref[-1], t_mdl[-1])
    if t_hi <= t_lo:
        return float("nan"), np.array([]), np.array([]), np.array([])

    mask = (t_mdl >= t_lo) & (t_mdl <= t_hi)
    t_c     = t_mdl[mask]
    phi_m_c = phi_mdl[mask]

    valid_ref = np.isfinite(phi_ref)
    if valid_ref.sum() < 4:
        return float("nan"), t_c, np.full_like(t_c, np.nan), phi_m_c
    f_interp = interp1d(t_ref[valid_ref], phi_ref[valid_ref],
                        kind="linear", bounds_error=False, fill_value=np.nan)
    phi_r_c = f_interp(t_c)

    good = np.isfinite(phi_m_c) & np.isfinite(phi_r_c)
    if good.sum() < 10:
        return float("nan"), t_c, phi_r_c, phi_m_c
    rmse = float(np.sqrt(np.mean((phi_m_c[good] - phi_r_c[good]) ** 2)))
    return rmse, t_c, phi_r_c, phi_m_c


# ════════════════════════════════════════════════════════════════════════════
# Graph A — Model PT Score (reuses PT-score style)
# ════════════════════════════════════════════════════════════════════════════

def _pt_short_keys():
    return {
        "R2n":           "R₂ₙ  (A₁/1.6A₀)",
        "N":             "N  (swing cycles)",
        "phi_max_ratio": "φ_max ratio  (A₂/A₀)",
        "omega_max_n":   "ω_max / A₀  (s⁻¹)",
        "omega_min_n":   "ω_min / A₀  (s⁻¹)",
        "f":             "f  (Hz)",
        "area_ratio":    "|P₊−P₋| / P_total",
    }

def _pt_notes():
    return {
        "R2n":           "≥ 0.91 healthy",
        "N":             "↓ = more impaired",
        "phi_max_ratio": "energy retention",
        "omega_max_n":   "peak velocity",
        "omega_min_n":   "min velocity",
        "f":             "oscillation freq.",
        "area_ratio":    "↑ = asymmetric",
    }


def make_pt_plot(t_full, angle_raw, params, pt, mas, title, out_path):
    """Graph A: PT score + MAS badge, identical style to pendulastic_pt_score."""
    mc = _MAS_COLOR.get(mas, "#FFFFFF")
    fig = plt.figure(figsize=(16, 9), facecolor=_BG)
    gs  = fig.add_gridspec(1, 2, width_ratios=[1.7, 1.0],
                           left=0.05, right=0.98,
                           top=0.88, bottom=0.09, wspace=0.07)
    ax_p = fig.add_subplot(gs[0, 0]); ax_p.set_facecolor(_PANEL)
    ax_r = fig.add_subplot(gs[0, 1]); ax_r.set_facecolor(_BG); ax_r.axis("off")

    fig.text(0.5, 0.975, title, ha="center", va="top", color="#D0DEFF",
             fontsize=17, fontweight="bold")

    ax_r.text(0.5, 0.985, "Estimated MAS", ha="center", va="top",
              transform=ax_r.transAxes, color="#90A8CC", fontsize=12)
    ax_r.text(0.5, 0.92, mas, ha="center", va="top",
              transform=ax_r.transAxes, color=mc, fontsize=56, fontweight="black",
              bbox=dict(boxstyle="round,pad=0.4", facecolor="#151e38",
                        edgecolor=mc, linewidth=3.0))
    ax_r.text(0.5, 0.70, f"PT Score  =  {pt:.3f}",
              ha="center", va="top", transform=ax_r.transAxes,
              color="#B8CEFF", fontsize=14, fontweight="bold")

    if params:
        SHORT = _pt_short_keys(); NOTE = _pt_notes()
        tbl_rows = []; impaired = set()
        for idx, key in enumerate(_PARAM_KEYS):
            val = params[key]; ref = HEALTHY_REF[key]
            if key in ("N","R2n","phi_max_ratio","omega_max_n"):
                bad = val < ref * 0.7
            elif key == "area_ratio":
                bad = val > ref * 1.8
            else:
                bad = abs(val - ref) > ref * 0.5
            if bad:
                impaired.add(idx + 1)
            tbl_rows.append([SHORT[key], f"{val:.3f}", f"{ref:.3f}", NOTE[key]])

        tbl = ax_r.table(cellText=tbl_rows,
                         colLabels=["Parameter","Measured","Healthy","Note"],
                         loc="lower center", cellLoc="center",
                         colWidths=[0.38, 0.19, 0.19, 0.24],
                         bbox=[0.0, 0.0, 1.0, 0.62])
        tbl.auto_set_font_size(False); tbl.set_fontsize(10.5); tbl.scale(1, 1.72)
        for (r, c), cell in tbl.get_celld().items():
            cell.set_edgecolor("#2A3A5A")
            if r == 0:
                cell.set_facecolor(_HDR)
                cell.set_text_props(color="#90B8FF", fontweight="bold", fontsize=11)
            elif r in impaired and c == 1:
                cell.set_facecolor("#3D1010")
                cell.set_text_props(color="#FF7070", fontsize=10.5, fontweight="bold")
            else:
                cell.set_facecolor(_TABLE)
                cell.set_text_props(color="#D0E4FF" if c != 3 else "#8899BB", fontsize=10.5)
            if c == 0:
                cell.set_text_props(ha="left")

    # Angle trace
    mask = np.isfinite(angle_raw)
    ax_p.plot(t_full[mask], angle_raw[mask], color="#6070A8", lw=1.0, alpha=0.5, label="Raw")

    if params:
        t_r  = params["t_r"]; phi = params["phi"]
        neut = params["neutral_deg"]; A0  = params["A0_deg"]
        t_abs = t_r + t_full[0]
        arc   = phi + neut

        ax_p.plot(t_abs, arc, color="#00E8C8", lw=2.5, label="Smoothed (φ+neutral)")
        ax_p.axhline(neut, color="#90AAC8", lw=1.5, ls="--", alpha=0.85,
                     label=f"Neutral = {neut:.1f}°")
        ax_p.axvline(t_abs[0], color="#FFB040", lw=2.0, ls=":", label="Release")

        ax_p.annotate(f"A₀ = {A0:.1f}°",
                      xy=(t_abs[0], neut + A0),
                      xytext=(t_abs[0] + 0.15, neut + A0 + 3),
                      color="#FFB040", fontsize=11, fontweight="bold",
                      arrowprops=dict(arrowstyle="-", color="#FFB040", lw=1.0))

        neg_tr = [(i, phi[i]) for i in params["tr_i"] if phi[i] < 0]
        if neg_tr:
            ti    = neg_tr[0][0]; depth = params["first_trough_depth"]
            ax_p.annotate(f"|trough| = {depth:.1f}°",
                          xy=(t_abs[ti], neut + phi[ti]),
                          xytext=(t_abs[ti] + 0.15, neut + phi[ti] - 5),
                          color="#60C8FF", fontsize=10,
                          arrowprops=dict(arrowstyle="-", color="#60C8FF", lw=1.0))
            ax_p.text(t_abs[ti] + 0.12, (neut + A0 + neut + phi[ti]) / 2,
                      f"A₁(pp) = {params['A1_deg']:.1f}°\n= A₀ + |trough|",
                      color="#FFE080", fontsize=10, va="center")

        ax_p.fill_between(t_abs, neut, arc, where=(phi >= 0), alpha=0.18,
                          color="#FFB040", label="Extension P₊")
        ax_p.fill_between(t_abs, neut, arc, where=(phi < 0), alpha=0.18,
                          color="#4888FF", label="Flexion P₋")

    ax_p.set_xlabel("Time (s)", color="#C8D8F0", fontsize=12)
    ax_p.set_ylabel("Knee Angle (°)", color="#C8D8F0", fontsize=12)
    ax_p.tick_params(colors="#A8BCD8", labelsize=11)
    for sp in ax_p.spines.values(): sp.set_edgecolor("#3A4A6A")
    ax_p.legend(fontsize=10, facecolor=_HDR, labelcolor="#D0E0FF",
                edgecolor="#3A4A6A", ncol=2, loc="upper right", framealpha=0.9)

    plt.savefig(out_path, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()


# ════════════════════════════════════════════════════════════════════════════
# Graph B — OptiTrack vs Model comparison
# ════════════════════════════════════════════════════════════════════════════

def make_comparison_plot(
    t_opti, ang_opti,           # ground truth raw (120 Hz)
    t_mdl,  ang_mdl,            # model raw (~30 fps)
    t_common,                   # common time grid (model resolution)
    phi_opti_interp,            # GT φ (neutral-subtracted) on model grid
    phi_mdl_common,             # model φ on common grid
    rmse: float,
    title: str,
    participant_label: str,
    mas_opti: str, mas_mdl: str,
    params_opti, params_mdl,
    out_path: str,
):
    """
    Graph B: Two-panel comparison.
    Top: raw angle traces (OptiTrack gold, model cyan) — shows absolute values
         and confirms the systematic camera-perspective offset is expected.
    Bottom: neutral-normalised φ traces overlaid — shows oscillation alignment
            used for RMSE (apples-to-apples dynamics comparison).
    Right: RMSE badge + MAS comparison.
    """
    fig = plt.figure(figsize=(16, 9), facecolor=_BG)
    gs  = mgridspec.GridSpec(2, 2, figure=fig,
                             left=0.07, right=0.97, top=0.88, bottom=0.08,
                             wspace=0.08, hspace=0.35,
                             height_ratios=[1, 1], width_ratios=[3, 1])

    ax_raw   = fig.add_subplot(gs[0, 0]); ax_raw.set_facecolor(_PANEL)
    ax_phi   = fig.add_subplot(gs[1, 0]); ax_phi.set_facecolor(_PANEL)
    ax_info  = fig.add_subplot(gs[:, 1]);  ax_info.set_facecolor(_BG); ax_info.axis("off")

    fig.text(0.5, 0.975, title, ha="center", va="top", color="#D0DEFF",
             fontsize=17, fontweight="bold")

    # ── Top: raw angle traces ───────────────────────────────────────────────
    ax_raw.plot(t_opti, ang_opti, color="#FFB040", lw=1.8, alpha=0.9,
                label="OptiTrack — raw angle (°)")
    ax_raw.plot(t_mdl, ang_mdl,  color="#00E8C8", lw=1.6, alpha=0.85,
                label="Model — raw angle (°)")
    # Mark neutral lines if available
    if params_opti:
        ax_raw.axhline(params_opti["neutral_deg"], color="#FFB040",
                       lw=1.0, ls="--", alpha=0.5)
    if params_mdl:
        ax_raw.axhline(params_mdl["neutral_deg"], color="#00E8C8",
                       lw=1.0, ls="--", alpha=0.5)
    ax_raw.set_ylabel("Knee Angle (°)", color="#C8D8F0", fontsize=11)
    ax_raw.tick_params(colors="#A8BCD8", labelsize=9)
    for sp in ax_raw.spines.values(): sp.set_edgecolor("#3A4A6A")
    ax_raw.legend(fontsize=9, facecolor=_HDR, labelcolor="#D0E0FF",
                  edgecolor="#3A4A6A", loc="upper right", framealpha=0.9)
    ax_raw.set_title("Raw angles  (offset is expected — camera vs 3-D OptiTrack convention)",
                     color="#708090", fontsize=9, pad=3)

    # ── Bottom: neutral-normalised φ comparison ─────────────────────────────
    if len(t_common) > 0:
        # Full-resolution φ for smoother visual
        neut_ot = params_opti["neutral_deg"] if params_opti else float(np.nanmedian(ang_opti))
        neut_md = params_mdl["neutral_deg"]  if params_mdl  else float(np.nanmedian(ang_mdl))
        phi_ot_full = ang_opti - neut_ot
        phi_md_full = ang_mdl  - neut_md

        ax_phi.plot(t_opti, phi_ot_full, color="#FFB040", lw=1.8, alpha=0.85,
                    label="OptiTrack φ (° from neutral)")
        ax_phi.plot(t_mdl,  phi_md_full, color="#00E8C8", lw=1.6, alpha=0.85,
                    label="Model φ (° from neutral)")
        ax_phi.axhline(0, color="#556080", lw=1.0, ls="--", alpha=0.7)

        # Shade residual on normalised traces
        if np.any(np.isfinite(phi_opti_interp)) and np.any(np.isfinite(phi_mdl_common)):
            resid = phi_mdl_common - phi_opti_interp
            ax_phi.fill_between(t_common, phi_opti_interp, phi_mdl_common,
                                where=(resid > 0), color="#FF6060", alpha=0.30,
                                label="Model > GT")
            ax_phi.fill_between(t_common, phi_opti_interp, phi_mdl_common,
                                where=(resid <= 0), color="#6090FF", alpha=0.30,
                                label="Model < GT")

    ax_phi.set_xlabel("Time (s)", color="#C8D8F0", fontsize=12)
    ax_phi.set_ylabel("φ — Swing angle (°)", color="#C8D8F0", fontsize=11)
    ax_phi.tick_params(colors="#A8BCD8", labelsize=9)
    for sp in ax_phi.spines.values(): sp.set_edgecolor("#3A4A6A")
    ax_phi.legend(fontsize=9, facecolor=_HDR, labelcolor="#D0E0FF",
                  edgecolor="#3A4A6A", loc="upper right", framealpha=0.9)
    ax_phi.set_title("Normalised oscillation  φ = angle − neutral  (RMSE computed here)",
                     color="#708090", fontsize=9, pad=3)

    # ── Info panel ─────────────────────────────────────────────────────────
    rmse_txt = f"{rmse:.2f}°" if math.isfinite(rmse) else "N/A"
    col_opti = _MAS_COLOR.get(mas_opti, "#FFFFFF")
    col_mdl  = _MAS_COLOR.get(mas_mdl,  "#FFFFFF")

    ax_info.text(0.5, 0.97, participant_label,
                 ha="center", va="top", transform=ax_info.transAxes,
                 color="#90A8CC", fontsize=13, fontweight="bold")

    # RMSE badge
    ax_info.text(0.5, 0.88, "RMSE",
                 ha="center", va="top", transform=ax_info.transAxes,
                 color="#90A8CC", fontsize=12)
    rmse_col = "#00E87A" if math.isfinite(rmse) and rmse < 8 else \
               "#FFB040" if math.isfinite(rmse) and rmse < 15 else "#FF4040"
    ax_info.text(0.5, 0.82, rmse_txt,
                 ha="center", va="top", transform=ax_info.transAxes,
                 color=rmse_col, fontsize=36, fontweight="black",
                 bbox=dict(boxstyle="round,pad=0.4", facecolor="#151e38",
                           edgecolor=rmse_col, linewidth=2.5))

    # MAS comparison
    ax_info.text(0.5, 0.57, "MAS estimate", ha="center", va="top",
                 transform=ax_info.transAxes, color="#90A8CC", fontsize=11)

    ax_info.text(0.22, 0.50, "OptiTrack", ha="center", va="top",
                 transform=ax_info.transAxes, color="#FFB040", fontsize=10)
    ax_info.text(0.22, 0.44, mas_opti, ha="center", va="top",
                 transform=ax_info.transAxes, color=col_opti,
                 fontsize=30, fontweight="black",
                 bbox=dict(boxstyle="round,pad=0.3", facecolor="#151e38",
                           edgecolor=col_opti, linewidth=2.0))

    ax_info.text(0.72, 0.50, "Model", ha="center", va="top",
                 transform=ax_info.transAxes, color="#00E8C8", fontsize=10)
    ax_info.text(0.72, 0.44, mas_mdl, ha="center", va="top",
                 transform=ax_info.transAxes, color=col_mdl,
                 fontsize=30, fontweight="black",
                 bbox=dict(boxstyle="round,pad=0.3", facecolor="#151e38",
                           edgecolor=col_mdl, linewidth=2.0))

    match_icon = "✓" if mas_opti == mas_mdl else "✗"
    match_col  = "#00E87A" if mas_opti == mas_mdl else "#FF4040"
    ax_info.text(0.5, 0.28, f"MAS match: {match_icon}",
                 ha="center", va="top", transform=ax_info.transAxes,
                 color=match_col, fontsize=14, fontweight="bold")

    # Legend hint
    ax_info.text(0.5, 0.14,
                 "RMSE < 8°  good\n8–15°  acceptable\n> 15°  poor",
                 ha="center", va="top", transform=ax_info.transAxes,
                 color="#708090", fontsize=9, linespacing=1.6)

    plt.savefig(out_path, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()


# ════════════════════════════════════════════════════════════════════════════
# Summary plots
# ════════════════════════════════════════════════════════════════════════════

def make_rmse_heatmap(records: list[dict], out_path: str) -> None:
    """RMSE heatmap: rows = model, cols = trial key (pid_pos_trial)."""
    if not records:
        return

    df = pd.DataFrame(records)
    # Build pivot: rows = model, cols = trial label
    df["trial_key"] = (df["pid"].str.replace("_", " ") + " P"
                       + df["pos"] + " T" + df["trial"])
    df["model_disp"] = df["model"].map(lambda m: _MODEL_DISPLAY.get(m, m))

    pivot = df.pivot_table(index="model_disp", columns="trial_key",
                           values="rmse", aggfunc="mean")
    pivot = pivot.sort_index()

    fig, ax = plt.subplots(figsize=(max(12, len(pivot.columns) * 1.1),
                                    max(5,  len(pivot)        * 0.65)),
                           facecolor=_BG)
    ax.set_facecolor(_PANEL)

    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=25)

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns, rotation=55, ha="right",
                       color="#C8D8F0", fontsize=8)
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index, color="#C8D8F0", fontsize=9)

    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            v = pivot.values[i, j]
            if not math.isfinite(v):
                txt, col = "N/A", "#556080"
            else:
                txt = f"{v:.1f}°"
                col = "black" if v < 12 else "white"
            ax.text(j, i, txt, ha="center", va="center",
                    fontsize=7.5, color=col, fontweight="bold")

    cbar = fig.colorbar(im, ax=ax, pad=0.01)
    cbar.set_label("RMSE (°)", color="#C8D8F0", fontsize=10)
    cbar.ax.yaxis.set_tick_params(color="#C8D8F0")
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color="#C8D8F0")

    ax.set_title("RMSE vs OptiTrack Ground Truth (°)\nP2 = healthy  |  P4 = MS",
                 color="#D0DEFF", fontsize=13, pad=12)
    for sp in ax.spines.values(): sp.set_edgecolor("#3A4A6A")

    plt.tight_layout()
    plt.savefig(out_path, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out_path)}")


def make_mas_accuracy_plot(records: list[dict], out_path: str) -> None:
    """Bar chart: per-model MAS match rate (% trials where model MAS == OptiTrack MAS)."""
    if not records:
        return
    df = pd.DataFrame(records)
    df = df[df["mas_opti"] != "?"]
    if df.empty:
        return

    df["match"] = (df["mas_mdl"] == df["mas_opti"]).astype(int)
    df["model_disp"] = df["model"].map(lambda m: _MODEL_DISPLAY.get(m, m))

    summary = (df.groupby("model_disp")
               .agg(match_rate=("match", "mean"), count=("match", "count"))
               .reset_index())
    summary["match_pct"] = summary["match_rate"] * 100
    summary = summary.sort_values("match_pct", ascending=True)

    fig, ax = plt.subplots(figsize=(10, max(5, len(summary) * 0.55)), facecolor=_BG)
    ax.set_facecolor(_PANEL)

    colors = [("#00E87A" if p >= 70 else "#FFB040" if p >= 40 else "#FF4040")
              for p in summary["match_pct"]]
    bars = ax.barh(summary["model_disp"], summary["match_pct"],
                   color=colors, edgecolor="#2A3A5A", height=0.65)

    for bar, pct, n in zip(bars, summary["match_pct"], summary["count"]):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"{pct:.0f}%  (n={n})", va="center", color="#C8D8F0", fontsize=10)

    ax.set_xlim(0, 115)
    ax.set_xlabel("MAS Match Rate (%)", color="#C8D8F0", fontsize=12)
    ax.set_title("Model MAS Accuracy vs OptiTrack Ground Truth\n"
                 "(% trials where estimated MAS equals OptiTrack MAS)",
                 color="#D0DEFF", fontsize=13, pad=10)
    ax.tick_params(colors="#A8BCD8", labelsize=10)
    ax.axvline(100, color="#3A4A6A", lw=1.0, ls="--")
    for sp in ax.spines.values(): sp.set_edgecolor("#3A4A6A")

    plt.tight_layout()
    plt.savefig(out_path, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out_path)}")


def make_model_ranking_plot(records: list[dict], out_path: str) -> None:
    """Combined ranking: mean RMSE + MAS accuracy per model."""
    if not records:
        return
    df = pd.DataFrame(records)
    df_valid = df[df["rmse"].apply(math.isfinite)]
    if df_valid.empty:
        return

    df_valid = df_valid.copy()
    df_valid["match"] = (df_valid["mas_mdl"] == df_valid["mas_opti"]).astype(float)
    df_valid["model_disp"] = df_valid["model"].map(lambda m: _MODEL_DISPLAY.get(m, m))

    grp = df_valid.groupby("model_disp").agg(
        mean_rmse=("rmse",   "mean"),
        std_rmse =("rmse",   "std"),
        mas_acc  =("match",  "mean"),
        n        =("rmse",   "count"),
    ).reset_index()
    grp["mas_acc_pct"] = grp["mas_acc"] * 100
    grp = grp.sort_values("mean_rmse")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, max(5, len(grp)*0.7)),
                                   facecolor=_BG)
    for ax in (ax1, ax2): ax.set_facecolor(_PANEL)

    # Left: mean RMSE with error bars
    colors_rmse = [("#00E87A" if r < 8 else "#FFB040" if r < 15 else "#FF4040")
                   for r in grp["mean_rmse"]]
    ax1.barh(grp["model_disp"], grp["mean_rmse"], xerr=grp["std_rmse"].fillna(0),
             color=colors_rmse, edgecolor="#2A3A5A", height=0.65,
             error_kw=dict(ecolor="#AAB8C8", capsize=4, lw=1.2))
    ax1.axvline(8,  color="#FFB040", lw=1.0, ls="--", alpha=0.6, label="8° threshold")
    ax1.axvline(15, color="#FF4040", lw=1.0, ls="--", alpha=0.6, label="15° threshold")
    for val, y in zip(grp["mean_rmse"], range(len(grp))):
        ax1.text(val + 0.3, y, f"{val:.1f}°", va="center",
                 color="#C8D8F0", fontsize=9)
    ax1.set_xlabel("Mean RMSE vs OptiTrack (°)", color="#C8D8F0", fontsize=11)
    ax1.set_title("Angle Accuracy (lower = better)", color="#D0DEFF", fontsize=12)
    ax1.tick_params(colors="#A8BCD8", labelsize=9)
    ax1.legend(fontsize=9, facecolor=_HDR, labelcolor="#D0E0FF", edgecolor="#3A4A6A")
    for sp in ax1.spines.values(): sp.set_edgecolor("#3A4A6A")

    # Right: MAS accuracy
    colors_mas = [("#00E87A" if p >= 70 else "#FFB040" if p >= 40 else "#FF4040")
                  for p in grp["mas_acc_pct"]]
    ax2.barh(grp["model_disp"], grp["mas_acc_pct"],
             color=colors_mas, edgecolor="#2A3A5A", height=0.65)
    for val, y in zip(grp["mas_acc_pct"], range(len(grp))):
        ax2.text(val + 1, y, f"{val:.0f}%", va="center",
                 color="#C8D8F0", fontsize=9)
    ax2.set_xlim(0, 115)
    ax2.set_xlabel("MAS Match Rate (%)", color="#C8D8F0", fontsize=11)
    ax2.set_title("MAS Accuracy (higher = better)", color="#D0DEFF", fontsize=12)
    ax2.tick_params(colors="#A8BCD8", labelsize=9)
    ax2.set_yticklabels([])
    for sp in ax2.spines.values(): sp.set_edgecolor("#3A4A6A")

    fig.suptitle("Model Ranking: Angle RMSE vs OptiTrack  +  MAS Accuracy\n"
                 "P2 = healthy  |  P4 = MS  |  sorted by RMSE (best on top)",
                 color="#D0DEFF", fontsize=14, y=1.01)
    plt.tight_layout()
    plt.savefig(out_path, dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close()
    print(f"  -> {os.path.basename(out_path)}")


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    SEP = "=" * 72
    print(f"\n{SEP}")
    print("  Model vs OptiTrack Evaluation  (P2=healthy, P4=MS)")
    print(f"{SEP}\n")

    entries = discover_model_csvs()
    if not entries:
        print("No model CSVs found with matching OptiTrack files."); return
    print(f"Found {len(entries)} model-trial combinations\n")

    # Pre-compute OptiTrack angles (cache by optitrack_path to avoid re-loading)
    opti_cache: dict[str, tuple] = {}
    opti_params_cache: dict[str, tuple] = {}

    records = []

    for i, entry in enumerate(entries):
        pid   = entry["pid"]
        pos   = entry["pos"]
        trial = entry["trial"]
        model = entry["model"]
        mdisp = _MODEL_DISPLAY.get(model, model)

        pid_label = (f"P{pid.split('_')[0]} ({'healthy' if pid.startswith('2') else 'MS'})"
                     f" — {pid.replace('_', ' ')}")
        tag = f"{pid_label} | Pos{pos} T{trial} | {mdisp}"
        print(f"[{i+1}/{len(entries)}] {tag}")

        # ── Load model angles ────────────────────────────────────────────────
        try:
            t_mdl, ang_mdl = load_model_csv(entry["path"])
        except Exception as exc:
            print(f"  [ERR] model CSV: {exc}"); continue

        safe_pid = pid.replace(" ", "_")

        # ── Model PT params ─────────────────────────────────────────────────
        mdl_params = compute_pt_params(t_mdl, ang_mdl)
        if mdl_params is None:
            print("  [WARN] insufficient model oscillation data — skipping PT graph")
            pt_mdl = float("nan"); mas_mdl = "?"
        else:
            pt_mdl  = compute_pt_score(mdl_params)
            mas_mdl = pt_to_mas(pt_mdl)

        # Graph A — model PT score
        if mdl_params is not None:
            pt_fname = f"{safe_pid}_Pos{pos}_T{trial}_{model}_pt.png"
            make_pt_plot(
                t_mdl, ang_mdl, mdl_params, pt_mdl, mas_mdl,
                title=f"{pid_label}  /  Pos{pos}  /  T{trial}  —  {mdisp}",
                out_path=os.path.join(OUT_PT, pt_fname),
            )
            print(f"  [A] PT={pt_mdl:.3f}  MAS={mas_mdl}  -> {pt_fname}")

        # ── Load OptiTrack ───────────────────────────────────────────────────
        opti_key = entry["optitrack_path"]
        if opti_key not in opti_cache:
            try:
                t_ot, ang_ot = load_optitrack(opti_key)
                opti_cache[opti_key] = (t_ot, ang_ot)
                ot_params = compute_pt_params(t_ot, ang_ot)
                if ot_params is not None:
                    pt_ot  = compute_pt_score(ot_params)
                    mas_ot = pt_to_mas(pt_ot)
                else:
                    pt_ot = float("nan"); mas_ot = "?"
                opti_params_cache[opti_key] = (ot_params, pt_ot, mas_ot)
            except Exception as exc:
                print(f"  [ERR] OptiTrack: {exc}")
                opti_cache[opti_key] = None
                opti_params_cache[opti_key] = (None, float("nan"), "?")

        cached = opti_cache.get(opti_key)
        ot_params_t, pt_ot, mas_ot = opti_params_cache.get(opti_key, (None, float("nan"), "?"))

        if cached is None:
            rmse = float("nan")
            records.append(dict(pid=pid, pos=pos, trial=trial, model=model,
                                rmse=rmse, pt_mdl=pt_mdl, mas_mdl=mas_mdl,
                                pt_opti=pt_ot, mas_opti=mas_ot))
            continue

        t_ot, ang_ot = cached

        # ── Compute normalised RMSE post-release (φ = angle − neutral) ────
        # t_start = release time from model params; falls back to 0 if unavailable
        t_start_rmse = 0.0
        if mdl_params is not None and len(mdl_params["t_r"]) > 0:
            t_start_rmse = float(mdl_params["t_r"][0])
        elif ot_params_t is not None and len(ot_params_t["t_r"]) > 0:
            t_start_rmse = float(ot_params_t["t_r"][0])

        rmse, t_common, phi_ot_interp, phi_mdl_c = compute_rmse(
            t_ot, ang_ot, t_mdl, ang_mdl,
            params_ref=ot_params_t, params_mdl=mdl_params,
            t_start=t_start_rmse,
        )
        status = f"{rmse:.2f}°" if math.isfinite(rmse) else "N/A"
        print(f"  [B] RMSE(phi)={status}  OptiTrack MAS={mas_ot}  Model MAS={mas_mdl}")

        # Graph B — comparison
        cmp_fname = f"{safe_pid}_Pos{pos}_T{trial}_{model}_cmp.png"
        make_comparison_plot(
            t_ot, ang_ot, t_mdl, ang_mdl,
            t_common, phi_ot_interp, phi_mdl_c,
            rmse=rmse,
            title=f"OptiTrack vs {mdisp}  —  {pid_label}  Pos{pos} T{trial}",
            participant_label=pid_label,
            mas_opti=mas_ot, mas_mdl=mas_mdl,
            params_opti=ot_params_t, params_mdl=mdl_params,
            out_path=os.path.join(OUT_CMP, cmp_fname),
        )
        print(f"       -> {cmp_fname}")

        records.append(dict(pid=pid, pos=pos, trial=trial, model=model,
                            rmse=rmse, pt_mdl=round(pt_mdl, 4), mas_mdl=mas_mdl,
                            pt_opti=round(pt_ot, 4) if math.isfinite(pt_ot) else float("nan"),
                            mas_opti=mas_ot))

    # ── Summary outputs ───────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("Summary outputs...")

    csv_out = os.path.join(OUT_DIR, "model_evaluation.csv")
    pd.DataFrame(records).to_csv(csv_out, index=False)
    print(f"  -> {os.path.basename(csv_out)}")

    make_rmse_heatmap(records, os.path.join(OUT_DIR, "rmse_heatmap.png"))
    make_mas_accuracy_plot(records, os.path.join(OUT_DIR, "mas_accuracy.png"))
    make_model_ranking_plot(records, os.path.join(OUT_DIR, "model_ranking.png"))

    # ── Console ranking ───────────────────────────────────────────────────
    print(f"\n{'Model':<30} {'Mean RMSE':>10} {'Std':>7} {'MAS Acc%':>9} {'n':>4}")
    print("-" * 62)
    df = pd.DataFrame(records)
    df_v = df[df["rmse"].apply(math.isfinite)].copy()
    if not df_v.empty:
        df_v["match"] = (df_v["mas_mdl"] == df_v["mas_opti"]).astype(float)
        df_v["mdisp"] = df_v["model"].map(lambda m: _MODEL_DISPLAY.get(m, m))
        rank = (df_v.groupby("mdisp")
                .agg(mean_rmse=("rmse","mean"), std_rmse=("rmse","std"),
                     mas_acc=("match","mean"), n=("rmse","count"))
                .reset_index().sort_values("mean_rmse"))
        for _, row in rank.iterrows():
            print(f"{row['mdisp']:<30} {row['mean_rmse']:>9.2f}° "
                  f"{row['std_rmse']:>6.2f}° {row['mas_acc']*100:>8.1f}% "
                  f"{row['n']:>4.0f}")
    print(f"\n{SEP}\n")
    print(f"All outputs -> {OUT_DIR}")


if __name__ == "__main__":
    main()
