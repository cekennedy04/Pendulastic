"""
plot_multisource_trials.py
===========================
For every trial that has OptiTrack ground truth plus MediaPipe and/or
phone-IMU data, generates:

  1. A per-trial figure: waveform overlay (OptiTrack + phone IMU +
     MediaPipe, all on the shared recording timeline) on top, and a table
     underneath giving each available modality's RMSE vs OptiTrack, PT
     score (7-parameter Popovic), and all 7 raw parameters.
  2. A pooled summary (multisource_summary.csv, one row per trial) and a
     concurrent-validity stats table (multisource_concurrent_validity.csv:
     ICC(2,1), Pearson r, Bland-Altman bias/LoA between each modality's PT
     score and OptiTrack's, plus pooled RMSE) -- the numbers a conference
     abstract's central claim would rest on.

Reuses pendulastic_pt_score.py's load_optitrack/compute_pt_params/
compute_pt_score/load_hpe_model_curves, pt_report_common.py's trial
discovery, and reliability_stats.py's icc_two_way/bland_altman -- no
scoring/alignment/statistics logic is reimplemented here.

Curves from load_hpe_model_curves() are already angle-baseline-aligned to
OptiTrack's coordinate space and share OptiTrack's recording-time origin
(no cross-correlation time-shift is applied anywhere in this pipeline), so
they're plotted directly against elapsed time -- not re-aligned to release,
which would risk introducing per-curve jitter from each curve independently
re-detecting its own (possibly different) release point.

Run:
    .venv\\Scripts\\python.exe plot_multisource_trials.py
"""
from __future__ import annotations

import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import pearsonr

import pendulastic_pt_score as pt
import pt_report_common as common
import reliability_stats as rel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Multisource_Waveforms")
os.makedirs(OUT_DIR, exist_ok=True)
SUMMARY_CSV = os.path.join(OUT_DIR, "multisource_summary.csv")
STATS_CSV = os.path.join(OUT_DIR, "multisource_concurrent_validity.csv")

_PARAM_KEYS = pt._PARAM_KEYS
_MODALITY_COLOR = {"imu": "#FF9000", "mediapipe": "#AA44FF"}
_MODALITY_LABEL = {"imu": "Phone IMU", "mediapipe": "MediaPipe"}
_MIN_PAIRS_FOR_STATS = 3


def _modality_key(curve_name):
    """Collapse a curve's raw model name (e.g. "mediapipe_full_0.5",
    "imu_viewer") down to the coarse modality bucket used everywhere else in
    this project (pt_report_common.attach_rmse): "mediapipe" or "imu". Any
    other HPE model (rtmpose, mmpose, ...) isn't part of this comparison and
    is ignored."""
    if curve_name.startswith("mediapipe"):
        return "mediapipe"
    if curve_name == "imu_viewer":
        return "imu"
    return None


# ══════════════════════════════════════════════════════════════════════════
# Discovery
# ══════════════════════════════════════════════════════════════════════════

def discover_multisource_trials():
    """Every (participant, leg, condition, trial) with valid OptiTrack PT
    params AND at least one of {mediapipe, imu} curves present. Returns a
    list of dicts: {pid, participant, leg, condition, trial, opti_t,
    opti_ang, opti_params, opti_score, modalities: {name: {t, ang, rmse,
    params, score}}}."""
    out = []
    for rec in common.discover_all_trials():
        try:
            t, ang = pt.load_optitrack(rec["path"])
        except Exception:
            continue
        opti_params = pt.compute_pt_params(t, ang)
        if opti_params is None:
            continue
        opti_score = pt.compute_pt_score(opti_params)

        pid = f"{rec['participant']}_{rec['leg']}_{rec['condition']}"
        try:
            curves = pt.load_hpe_model_curves(pid, "1", rec["trial"], t, ang, opti_params["neutral_deg_raw"])
        except Exception:
            curves = []

        modalities = {}
        for c in curves:
            key = _modality_key(c["name"])
            if key is None or key in modalities:   # curves arrive RMSE-sorted; keep the best
                continue
            m_params = pt.compute_pt_params(c["t"], c["ang"])
            modalities[key] = {
                "t": c["t"], "ang": c["ang"], "rmse": c["rmse"],
                "params": m_params,
                "score": pt.compute_pt_score(m_params) if m_params is not None else None,
            }
        if not modalities:
            continue

        out.append(dict(
            pid=pid, participant=rec["participant"], leg=rec["leg"],
            condition=rec["condition"], trial=rec["trial"],
            opti_t=t, opti_ang=ang, opti_params=opti_params, opti_score=opti_score,
            modalities=modalities,
        ))
    return out


# ══════════════════════════════════════════════════════════════════════════
# Per-trial figure
# ══════════════════════════════════════════════════════════════════════════

def make_trial_figure(trial, out_path):
    fig = plt.figure(figsize=(13, 9.5), facecolor="white")
    gs = fig.add_gridspec(2, 1, height_ratios=[2, 1.3], hspace=0.4)

    # ── Waveform overlay ─────────────────────────────────────────────────
    ax = fig.add_subplot(gs[0])
    ax.set_facecolor("#f8f9fa")
    ax.grid(True, color=common.BG_GRID, linestyle="-", linewidth=0.8)
    ax.plot(trial["opti_t"], trial["opti_ang"], color="#222222", linewidth=2.0,
           label=f"OptiTrack (PT={trial['opti_score']:.2f})", zorder=5)
    for key in ("imu", "mediapipe"):
        m = trial["modalities"].get(key)
        if m is None:
            continue
        score_txt = f"{m['score']:.2f}" if m["score"] is not None else "n/a"
        rmse_txt = f"{m['rmse']:.1f}°" if np.isfinite(m["rmse"]) else "n/a"
        ax.plot(m["t"], m["ang"], color=_MODALITY_COLOR[key], linewidth=1.4, alpha=0.85,
               label=f"{_MODALITY_LABEL[key]} (RMSE={rmse_txt}, PT={score_txt})", zorder=4)
    ax.set_xlabel("Time (s)", fontsize=9)
    ax.set_ylabel("Knee angle (°)", fontsize=9)
    ax.set_title(f"P{trial['participant']} {trial['leg']}/{trial['condition']} T{trial['trial']} "
                f"— Waveform Comparison", fontsize=11, fontweight="bold")
    ax.tick_params(labelsize=8)
    ax.legend(loc="best", fontsize=8, framealpha=0.9)

    # ── Parameter / PT-score / RMSE table ───────────────────────────────
    ax2 = fig.add_subplot(gs[1])
    ax2.axis("off")
    columns = ["Source", "RMSE (deg)"] + common.PARAM_DISPLAY + ["PT score"]
    rows = []
    op = trial["opti_params"]
    rows.append(["OptiTrack", "—"] + [f"{op[k]:.3f}" for k in _PARAM_KEYS] + [f"{trial['opti_score']:.3f}"])
    for key in ("imu", "mediapipe"):
        m = trial["modalities"].get(key)
        label = _MODALITY_LABEL[key]
        if m is None:
            rows.append([label, "—"] + ["—"] * len(_PARAM_KEYS) + ["—"])
        elif m["params"] is None:
            rmse_txt = f"{m['rmse']:.1f}" if np.isfinite(m["rmse"]) else "n/a"
            rows.append([label, rmse_txt] + ["n/a"] * len(_PARAM_KEYS) + ["n/a"])
        else:
            rmse_txt = f"{m['rmse']:.1f}" if np.isfinite(m["rmse"]) else "n/a"
            rows.append([label, rmse_txt] + [f"{m['params'][k]:.3f}" for k in _PARAM_KEYS] +
                       [f"{m['score']:.3f}"])
    table = ax2.table(cellText=rows, colLabels=columns, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.9)

    fig.savefig(out_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def main():
    trials = discover_multisource_trials()
    print(f"{len(trials)} trials with OptiTrack + >=1 modality (MediaPipe/IMU) found.")
    if not trials:
        return

    summary_rows = []
    pooled = {key: {"rmse": [], "opti_pt": [], "model_pt": []} for key in ("imu", "mediapipe")}

    for trial in trials:
        fname = f"P{trial['participant']}_{trial['leg']}_{trial['condition']}_T{trial['trial']}_multisource.png"
        out_path = os.path.join(OUT_DIR, fname)
        make_trial_figure(trial, out_path)
        print(f"-> {out_path}")

        row = {"participant": trial["participant"], "leg": trial["leg"],
              "condition": trial["condition"], "trial": trial["trial"],
              "optitrack_pt_score": trial["opti_score"]}
        for key in ("imu", "mediapipe"):
            m = trial["modalities"].get(key)
            row[f"{key}_rmse"] = m["rmse"] if m else None
            row[f"{key}_pt_score"] = m["score"] if m else None
            if m and np.isfinite(m["rmse"]):
                pooled[key]["rmse"].append(m["rmse"])
            if m and m["score"] is not None:
                pooled[key]["opti_pt"].append(trial["opti_score"])
                pooled[key]["model_pt"].append(m["score"])
        summary_rows.append(row)

    with open(SUMMARY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        w.writerows(summary_rows)
    print(f"-> {SUMMARY_CSV}")

    print("\n--- Pooled concurrent validity (device PT score vs OptiTrack PT score) ---")
    with open(STATS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["modality", "n_pt_pairs", "icc_2_1", "pearson_r", "pearson_p",
                   "rmse_n", "rmse_median_deg", "rmse_mean_deg", "ba_bias", "ba_loa_lo", "ba_loa_hi"])
        for key in ("imu", "mediapipe"):
            opti_pt, model_pt, rmse_vals = pooled[key]["opti_pt"], pooled[key]["model_pt"], pooled[key]["rmse"]
            n = len(opti_pt)
            if n >= _MIN_PAIRS_FOR_STATS:
                icc = rel.icc_two_way(opti_pt, model_pt)["icc"]
                ba = rel.bland_altman(opti_pt, model_pt)
                r, p = pearsonr(opti_pt, model_pt)
            else:
                icc, r, p = float("nan"), float("nan"), float("nan")
                ba = {"bias": float("nan"), "loa_lo": float("nan"), "loa_hi": float("nan")}
            rmse_n = len(rmse_vals)
            rmse_median = float(np.median(rmse_vals)) if rmse_vals else float("nan")
            rmse_mean = float(np.mean(rmse_vals)) if rmse_vals else float("nan")
            w.writerow([key, n, icc, r, p, rmse_n, rmse_median, rmse_mean,
                       ba["bias"], ba["loa_lo"], ba["loa_hi"]])
            caveat = "" if n >= _MIN_PAIRS_FOR_STATS else "  (n < 3 -- not enough pairs for ICC/Pearson)"
            print(f"{_MODALITY_LABEL[key]}: n_pt_pairs={n}  ICC(2,1)={icc:.3f}  "
                 f"Pearson r={r:.3f} (p={p:.4f})  RMSE median={rmse_median:.1f}° (n={rmse_n}){caveat}")
    print(f"-> {STATS_CSV}")


if __name__ == "__main__":
    main()
