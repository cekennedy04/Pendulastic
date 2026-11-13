"""
evaluate_grid_participant_0.py
==============================
Reads the per-variant grid CSVs already produced for Participant 0
(e.g. P_0_Pos_1_H_Joint-Level_T_1_rtmpose_l_0.3.csv) and generates
ModelGrid-style comparison graphs vs OptiTrack for every trial × model family.

Output per trial:
  Grid_Pos{N}_Trial{N}_mediapipe.png   (complexity 0/1/2 × threshold 0.5/0.75)
  Grid_Pos{N}_Trial{N}_rtmpose.png     (size s/m/l × threshold 0.2/0.3/0.35/0.5/0.6)
  Grid_Pos{N}_Trial{N}_mmpose.png      (size s × thresholds)
  Grid_Pos{N}_Trial{N}_fremocap.png    (single panel)

All graphs use the same style as evaluate_models_participant_1.py:
  - OptiTrack in black, model in colour
  - Swing peaks numbered, dashed connector lines showing per-swing error
  - Large RMSE box in each subplot corner
"""

import os
import re
import glob
import collections

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

# ---------------------------------------------------------------------------
# CONFIG  (mirrors evaluate_models_participant_1.py exactly)
# ---------------------------------------------------------------------------
BASE_DIR       = r"C:\Users\cladi\Pendulastic"
RECORDINGS_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings", "Participant_0")
OUTPUT_DIR     = os.path.join(BASE_DIR, "pendulastic model analysis", "grid_participant_0")
os.makedirs(OUTPUT_DIR, exist_ok=True)

MODEL_FPS       = 30
DROP_AMPLITUDE  = 5.0
BASELINE_SEC    = 3.0
PEAK_PROMINENCE = 3.0
PLOT_XLIM       = 15.0

# Base colours per model family
FAMILY_COLORS = {
    "mediapipe": "#1f77b4",
    "rtmpose":   "#ff7f0e",
    "mmpose":    "#2ca02c",
    "fremocap":  "#9467bd",
}
FAMILY_CMAPS = {
    "mediapipe": "Blues",
    "rtmpose":   "Oranges",
    "mmpose":    "Greens",
    "fremocap":  "Purples",
}

# ---------------------------------------------------------------------------
# HELPERS  (identical to evaluate_models_participant_1.py)
# ---------------------------------------------------------------------------

def align_and_normalize(raw_angles, release_idx):
    trimmed    = np.array(raw_angles[release_idx:], dtype=float)
    baseline   = np.nanmean(raw_angles[:max(1, release_idx)])
    normalised = np.abs(trimmed - baseline)
    if len(normalised) and np.nanmax(normalised) < 6.2832:
        normalised = np.rad2deg(normalised)
    return normalised


def load_optitrack(csv_path):
    header_idx = 0
    with open(csv_path, "r", encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header_idx = i
                break
    df = pd.read_csv(csv_path, skiprows=header_idx)
    df = df.apply(pd.to_numeric, errors="coerce").ffill().bfill()
    t  = df.iloc[:, 1].values.astype(float)
    t  = t - t[0]
    fs = round(1.0 / float(np.median(np.diff(t[t > 0]))))
    post = t > BASELINE_SEC
    best_v, best_c = -1.0, df.columns[2]
    for col in df.columns[2:]:
        v = np.nanvar(df[col].values.astype(float)[post])
        if v > best_v:
            best_v, best_c = v, col
    ang = df[best_c].values.astype(float)
    if np.nanmax(np.abs(ang)) < 6.2832:
        ang = np.rad2deg(ang)
    return t, ang, int(fs)


def detect_master_anchor(o_t, o_ang, opti_fs):
    n_base   = int(BASELINE_SEC * opti_fs)
    baseline = np.nanmean(o_ang[:n_base])
    search   = np.abs(o_ang[n_base:] - baseline)
    for thr in [DROP_AMPLITUDE, DROP_AMPLITUDE / 2, 2.0, 1.0]:
        hits = np.where(search > thr)[0]
        if len(hits):
            return int(n_base + hits[0]), float(o_t[n_base + hits[0]])
    return 0, float(o_t[0])


def find_all_peaks(signal, fs):
    guard    = max(1, int(0.1 * fs))
    min_dist = max(5, int(0.3 * fs))
    min_h    = max(15.0, float(np.nanmax(signal)) * 0.25)
    peaks, _ = find_peaks(signal[guard:], distance=min_dist,
                          prominence=PEAK_PROMINENCE, height=min_h)
    return peaks + guard


def load_variant_csv(csv_path):
    """Read a grid-output CSV → (time_sec array, knee_angle_deg array)."""
    df = pd.read_csv(csv_path)
    t   = df["time_sec"].values.astype(float)
    ang = df["knee_angle_deg"].values.astype(float)
    return t, ang


def analyze_variant(o_final, o_t_event, o_peaks, o_peak1_t, opti_fs,
                    m_ang, release_time_s):
    """Apply the same alignment + peak-match + RMSE logic as the main script."""
    m_rel_idx = min(int(release_time_s * MODEL_FPS), len(m_ang) - 1)
    m_final   = align_and_normalize(m_ang, m_rel_idx)
    m_t_event = np.arange(len(m_final)) / MODEL_FPS
    m_peaks   = find_all_peaks(m_final, MODEL_FPS)

    if len(o_peaks) and len(m_peaks):
        time_shift = o_peak1_t - float(m_t_event[m_peaks[0]])
    else:
        time_shift = 0.0
    m_t_plot = m_t_event + time_shift

    # Match swings by index
    o_t_pks, o_vals, m_vals, abs_errs, sq_errs = [], [], [], [], []
    for i, o_pk in enumerate(o_peaks):
        o_t_pks.append(float(o_t_event[o_pk]))
        o_vals.append(float(o_final[o_pk]))
        if i < len(m_peaks):
            m_pk  = int(m_peaks[i])
            m_val = float(m_final[m_pk])
            err   = m_val - o_vals[-1]
            m_vals.append(m_val)
            abs_errs.append(abs(err))
            sq_errs.append(err ** 2)
        else:
            m_vals.append(None)
            abs_errs.append(None)
            sq_errs.append(None)

    valid_sq = [v for v in sq_errs if v is not None]
    rmse = float(np.sqrt(np.mean(valid_sq))) if valid_sq else float("nan")

    return dict(
        m_final=m_final, m_t_plot=m_t_plot,
        m_t_event=m_t_event, m_peaks=m_peaks,
        time_shift=time_shift,
        o_t_pks=o_t_pks, o_vals=o_vals, m_vals=m_vals,
        abs_errs=abs_errs, rmse=rmse,
    )


# ---------------------------------------------------------------------------
# DISCOVER ALL GRID CSVs
# ---------------------------------------------------------------------------
# Pattern: P_0_Pos_{pos}_H_Joint-Level_T_{trial}_{family}_{complexity}_{thr}.csv
CSV_RE = re.compile(
    r"P_0_Pos_(\d+)_H_Joint-Level_T_(\d+)_"
    r"(mediapipe|rtmpose|mmpose|fremocap)_(.+)\.csv$",
    re.IGNORECASE,
)

# Also need the matching OptiTrack CSV per (pos, trial)
OPTI_RE = re.compile(r"trial_(\d+)_optitrack\.csv$", re.IGNORECASE)

# Collect: {(pos, trial): path_to_optitrack_csv}
optitrack_csvs = {}
for path in glob.glob(os.path.join(RECORDINGS_ROOT, "**", "*_optitrack.csv"),
                      recursive=True):
    m = OPTI_RE.search(os.path.basename(path))
    if not m:
        continue
    trial = m.group(1)
    # Infer position from folder path
    parts = os.path.normpath(path).split(os.sep)
    pos = None
    for p in parts:
        if p.startswith("Position_"):
            pos = p.replace("Position_", "")
    if pos:
        optitrack_csvs[(pos, trial)] = path

print(f"Found OptiTrack CSVs: {sorted(optitrack_csvs.keys())}")

# Collect: {(pos, trial, family): [(complexity, threshold, csv_path), ...]}
variants = collections.defaultdict(list)
for path in glob.glob(os.path.join(RECORDINGS_ROOT, "**", "P_0_*.csv"),
                      recursive=True):
    m = CSV_RE.search(os.path.basename(path))
    if not m:
        continue
    pos, trial, family, rest = m.group(1), m.group(2), m.group(3).lower(), m.group(4)
    # rest = "complexity_threshold" or "default_threshold"
    parts = rest.rsplit("_", 1)
    complexity = parts[0] if len(parts) == 2 else rest
    threshold  = parts[1] if len(parts) == 2 else "?"
    variants[(pos, trial, family)].append((complexity, threshold, path))

# Sort variants within each group
for key in variants:
    variants[key].sort(key=lambda x: (x[0], float(x[1]) if x[1] != "?" else 0))

print(f"Found {sum(len(v) for v in variants.values())} variant CSVs across "
      f"{len(variants)} (pos,trial,family) groups.\n")

# ---------------------------------------------------------------------------
# GRID LAYOUT DEFINITIONS
# ---------------------------------------------------------------------------
# For each family, define how variants map to (row, col) in the subplot grid.
# Key: complexity string → row index; threshold → col index.

def get_layout(family, variant_list):
    """
    Returns (nrows, ncols, cell_fn) where cell_fn(complexity, threshold) → (row, col).
    """
    complexities = sorted(set(c for c, t, _ in variant_list))
    thresholds   = sorted(set(t for c, t, _ in variant_list),
                          key=lambda x: float(x) if x != "?" else 0)

    c_idx = {c: i for i, c in enumerate(complexities)}
    t_idx = {t: i for i, t in enumerate(thresholds)}

    nrows = len(complexities)
    ncols = len(thresholds)

    def cell_fn(c, t):
        return c_idx[c], t_idx[t]

    return nrows, ncols, cell_fn, complexities, thresholds


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
total_saved = 0

for (pos, trial), opti_path in sorted(optitrack_csvs.items()):
    print(f"=== Pos={pos} Trial={trial} ===")

    # Load OptiTrack
    try:
        o_t, o_ang, opti_fs = load_optitrack(opti_path)
    except Exception as exc:
        print(f"  [error loading OptiTrack] {exc}")
        continue

    o_rel_idx, release_time_s = detect_master_anchor(o_t, o_ang, opti_fs)
    o_final    = align_and_normalize(o_ang, o_rel_idx)
    o_t_event  = np.arange(len(o_final)) / opti_fs
    o_peaks    = find_all_peaks(o_final, opti_fs)
    o_peak1_t  = float(o_t_event[o_peaks[0]]) if len(o_peaks) else 0.0
    print(f"  OptiTrack: {len(o_peaks)} peaks | drop={release_time_s:.2f}s")

    # Build one figure per model family
    for family in ["mediapipe", "rtmpose", "mmpose", "fremocap"]:
        key = (pos, trial, family)
        if key not in variants:
            continue

        variant_list = variants[key]
        nrows, ncols, cell_fn, complexities, thresholds = get_layout(
            family, variant_list)

        cmap     = plt.colormaps.get_cmap(FAMILY_CMAPS[family])
        n_shades = max(len(complexities), 2)

        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(max(5 * ncols, 10), max(4 * nrows, 5)),
            squeeze=False,
        )

        fig.suptitle(
            f"Participant 0  |  Position {pos}  |  Trial {trial}  |  "
            f"{family.upper()} — all complexities × thresholds",
            fontsize=14, fontweight="bold", y=1.01,
        )

        plotted = set()

        for complexity, threshold, csv_path in variant_list:
            row, col = cell_fn(complexity, threshold)
            ax = axes[row][col]
            plotted.add((row, col))

            # Load + analyse
            try:
                _, m_ang = load_variant_csv(csv_path)
            except Exception as exc:
                ax.set_title(f"{complexity} / {threshold}\n[load error]", fontsize=8)
                ax.axis("off")
                continue

            res = analyze_variant(o_final, o_t_event, o_peaks, o_peak1_t,
                                  opti_fs, m_ang, release_time_s)
            rmse     = res["rmse"]
            rmse_str = f"{rmse:.1f}" if not np.isnan(rmse) else "N/A"

            # Pick shade from colormap based on row (complexity level)
            shade = cmap(0.4 + 0.5 * (row / max(nrows - 1, 1)))
            color = shade

            # Waveforms
            ax.plot(o_t_event, o_final,
                    color="black", linewidth=1.8, zorder=5, label="OptiTrack")
            ax.plot(res["m_t_plot"], res["m_final"],
                    color=color, linewidth=1.8, alpha=0.85, zorder=4,
                    label=f"{family} {complexity}")

            # OptiTrack peaks + swing numbers
            for i, (o_pk, o_t_pk, o_val) in enumerate(
                    zip(o_peaks, res["o_t_pks"], res["o_vals"])):
                ax.scatter(o_t_pk, o_val, color="black",
                           s=60, zorder=8, edgecolors="white", linewidths=1.0)
                ax.text(o_t_pk, o_val + 2.0, str(i + 1),
                        ha="center", va="bottom", fontsize=7,
                        color="black", fontweight="bold")

            # Model peaks + error connectors
            for i, (o_t_pk, o_val, m_val, abs_err) in enumerate(
                    zip(res["o_t_pks"], res["o_vals"],
                        res["m_vals"], res["abs_errs"])):
                if m_val is None:
                    continue
                if i < len(res["m_peaks"]):
                    m_t_pk = res["m_t_event"][int(res["m_peaks"][i])] + res["time_shift"]
                    ax.scatter(m_t_pk, m_val, color=color, marker="o",
                               s=60, zorder=7, edgecolors="black", linewidths=0.7)
                # Connector at OptiTrack peak x
                ax.plot([o_t_pk, o_t_pk], [o_val, m_val],
                        color="gray", linewidth=1.0, linestyle="--",
                        alpha=0.55, zorder=3)
                if abs_err is not None:
                    mid_y = (o_val + m_val) / 2
                    ax.text(o_t_pk + 0.15, mid_y, f"{abs_err:.1f}",
                            ha="left", va="center",
                            fontsize=6.5, color="dimgray")

            # RMSE box
            ax.text(0.97, 0.97,
                    f"RMSE = {rmse_str} deg",
                    transform=ax.transAxes,
                    ha="right", va="top",
                    fontsize=10, fontweight="bold",
                    color=color,
                    bbox=dict(boxstyle="round,pad=0.35",
                              facecolor="white",
                              edgecolor=color, linewidth=1.8, alpha=0.93))

            ax.set_xlim(0, PLOT_XLIM)
            ax.set_ylim(bottom=0)
            ax.set_title(f"complexity={complexity}  |  threshold={threshold}",
                         fontsize=9, fontweight="bold")
            ax.set_xlabel("Time since drop (s)", fontsize=8)
            ax.set_ylabel("Knee Flexion (deg)", fontsize=8)
            ax.legend(loc="upper left", fontsize=7, framealpha=0.85)
            ax.grid(True, linestyle=":", alpha=0.35)
            ax.tick_params(labelsize=7)

        # Hide any unused subplot cells
        for r in range(nrows):
            for c in range(ncols):
                if (r, c) not in plotted:
                    axes[r][c].set_visible(False)

        # Column headers (threshold values)
        for c, thr in enumerate(thresholds):
            axes[0][c].set_title(
                f"threshold={thr}\n" + axes[0][c].get_title().split("\n")[-1]
                if "\n" not in axes[0][c].get_title()
                else axes[0][c].get_title(),
                fontsize=9, fontweight="bold",
            )

        fig.tight_layout()
        out_path = os.path.join(
            OUTPUT_DIR,
            f"Grid_Pos{pos}_Trial{trial}_{family}.png",
        )
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        total_saved += 1
        print(f"  Saved: Grid_Pos{pos}_Trial{trial}_{family}.png  "
              f"({nrows}x{ncols} grid, {len(variant_list)} variants)")

print(f"\nDone. {total_saved} grid graphs saved to:\n  {OUTPUT_DIR}")
