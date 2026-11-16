"""
evaluate_models_participant_1_grid.py
======================================
Evaluates HPE model variants for Participant 1, Position 2, Trials 1 & 2.

Ground truth: OptiTrack Thigh + Shank rigid-body quaternions
  → relative rotation magnitude = knee flexion angle in degrees.

Model data: pre-processed per-variant CSVs in
  Recordings/Participant_1/Position_2/Height_Joint-Level/
  (same knee_angle_deg CSV format as Participant 0)

Output: grid graphs identical in style to evaluate_grid_participant_0.py
  pendulastic model analysis/grid_participant_1/
    Grid_P1_Pos2_Trial{N}_{family}.png
"""

import os
import re
import glob
import collections
import math

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation as R

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------
BASE_DIR     = r"C:\Users\cladi\Pendulastic"
OPTI_DIR     = os.path.join(BASE_DIR, "OptiTrack_Recordings",
                             "Participant_1", "Position_2", "Height_Joint-Level")
MODEL_DIR    = os.path.join(BASE_DIR, "Recordings",
                             "Participant_1", "Position_2", "Height_Joint-Level")
OUTPUT_DIR   = os.path.join(BASE_DIR, "pendulastic model analysis",
                             "grid_participant_1")
os.makedirs(OUTPUT_DIR, exist_ok=True)

POSITION     = "2"
PARTICIPANT  = "1"

MODEL_FPS       = 30
DROP_AMPLITUDE  = 5.0
BASELINE_SEC    = 3.0
PEAK_PROMINENCE = 3.0
PLOT_XLIM       = 15.0

FAMILY_CMAPS = {
    "mediapipe": "Blues",
    "rtmpose":   "Oranges",
    "mmpose":    "Greens",
    "fremocap":  "Purples",
}

# ---------------------------------------------------------------------------
# OPTITRACK LOADER  (Thigh + Shank quaternions → knee angle in degrees)
# ---------------------------------------------------------------------------

def load_optitrack_p1(csv_path):
    """
    Parse Motive rigid-body quaternion CSV for Participant 1.

    Column layout after the 'Frame' header row:
      col0=Frame  col1=Time(s)
      col2=Thigh_X  col3=Thigh_Y  col4=Thigh_Z  col5=Thigh_W  (quaternion)
      col6=Thigh_PosX  col7=PosY  col8=PosZ
      col9=Shank_X  col10=Shank_Y  col11=Shank_Z  col12=Shank_W (quaternion)
      col13..15 = Shank position

    Returns (t, knee_angle_deg, fs).
    """
    # Find the 'Frame' header row
    header_idx = 0
    with open(csv_path, "r", encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header_idx = i
                break

    df = pd.read_csv(csv_path, skiprows=header_idx, header=0)
    df = df.apply(pd.to_numeric, errors="coerce").ffill().bfill()

    t  = df.iloc[:, 1].values.astype(float)
    t  = t - t[0]
    fs = round(1.0 / float(np.median(np.diff(t[t > 0]))))

    # Thigh quaternion: Motive exports X,Y,Z,W → scipy wants (x,y,z,w)
    tx = df.iloc[:, 2].values.astype(float)
    ty = df.iloc[:, 3].values.astype(float)
    tz = df.iloc[:, 4].values.astype(float)
    tw = df.iloc[:, 5].values.astype(float)

    # Shank quaternion
    sx = df.iloc[:, 9].values.astype(float)
    sy = df.iloc[:, 10].values.astype(float)
    sz = df.iloc[:, 11].values.astype(float)
    sw = df.iloc[:, 12].values.astype(float)

    r_thigh = R.from_quat(np.column_stack([tx, ty, tz, tw]))
    r_shank = R.from_quat(np.column_stack([sx, sy, sz, sw]))

    # Relative rotation: angle between thigh and shank segments
    r_rel    = r_thigh.inv() * r_shank
    knee_rad = r_rel.magnitude()                      # radians
    knee_deg = np.degrees(knee_rad)                   # degrees

    return t, knee_deg, int(fs)


# ---------------------------------------------------------------------------
# SHARED HELPERS  (identical logic to evaluate_grid_participant_0.py)
# ---------------------------------------------------------------------------

def align_and_normalize(raw_angles, release_idx, baseline=None):
    trimmed = np.array(raw_angles[release_idx:], dtype=float)
    if baseline is None:
        baseline = np.nanmean(raw_angles[:max(1, release_idx)])
    normalised = np.abs(trimmed - baseline)
    if len(normalised) and np.nanmax(normalised) < 6.2832:
        normalised = np.rad2deg(normalised)
    return normalised


def _model_baseline(m_ang, m_rel_idx):
    """
    For model CSVs where the leg is undetected pre-release (angle flat/interpolated),
    the pre-release mean is wrong.  Detect this by comparing the pre-release mean to
    the signal's 95th-percentile: if they differ by >10° the pre-release period is
    unreliable and we use the 95th-percentile (≈ extended-leg angle) as baseline.
    """
    pre_mean = float(np.nanmean(m_ang[:max(1, m_rel_idx)]))
    p95      = float(np.nanpercentile(m_ang, 95))
    return p95 if abs(pre_mean - p95) > 10.0 else pre_mean


def find_all_pits(signal, peaks):
    """Return index of the minimum sample between each consecutive pair of peaks."""
    pits = []
    for i in range(len(peaks) - 1):
        start = int(peaks[i])
        end   = int(peaks[i + 1]) + 1
        end   = min(end, len(signal))
        local_min = int(np.argmin(signal[start:end])) + start
        pits.append(local_min)
    return np.array(pits, dtype=int)


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
    df = pd.read_csv(csv_path)
    return df["time_sec"].values.astype(float), \
           df["knee_angle_deg"].values.astype(float)


def analyze_variant(o_final, o_t_event, o_peaks, o_peak1_t,
                    m_ang, m_times_raw, release_time_s):
    """
    Evaluate one model variant against OptiTrack.

    Peak matching: N-th model peak  ↔ N-th OptiTrack peak.
    Pit  matching: minimum between each consecutive peak pair.
    RMSE is computed over ALL matched extrema (peaks + pits).
    """
    m_rel_idx  = int(np.searchsorted(m_times_raw, release_time_s))
    m_rel_idx  = min(m_rel_idx, len(m_ang) - 1)

    m_base     = _model_baseline(m_ang, m_rel_idx)
    m_final    = align_and_normalize(m_ang, m_rel_idx, baseline=m_base)
    m_t_event  = m_times_raw[m_rel_idx : m_rel_idx + len(m_final)] - release_time_s
    m_peaks    = find_all_peaks(m_final, MODEL_FPS)

    if len(o_peaks) and len(m_peaks):
        time_shift = o_peak1_t - float(m_t_event[m_peaks[0]])
    else:
        time_shift = 0.0
    m_t_plot = m_t_event + time_shift

    # ---- peaks ----
    o_t_pks, o_vals, m_vals, abs_errs, sq_errs = [], [], [], [], []
    for i, o_pk in enumerate(o_peaks):
        o_t_pks.append(float(o_t_event[o_pk]))
        o_vals.append(float(o_final[o_pk]))
        if i < len(m_peaks):
            m_val = float(m_final[int(m_peaks[i])])
            err   = m_val - o_vals[-1]
            m_vals.append(m_val)
            abs_errs.append(abs(err))
            sq_errs.append(err ** 2)
        else:
            m_vals.append(None)
            abs_errs.append(None)
            sq_errs.append(None)

    # ---- pits (minima between consecutive peaks) ----
    o_pits     = find_all_pits(o_final, o_peaks)
    m_pits     = find_all_pits(m_final, m_peaks)

    o_pit_t, o_pit_vals, m_pit_vals, pit_abs_errs, pit_sq_errs = [], [], [], [], []
    for j, o_pit in enumerate(o_pits):
        o_pit_t.append(float(o_t_event[o_pit]))
        o_pit_vals.append(float(o_final[o_pit]))
        if j < len(m_pits):
            m_pv = float(m_final[int(m_pits[j])])
            perr = m_pv - o_pit_vals[-1]
            m_pit_vals.append(m_pv)
            pit_abs_errs.append(abs(perr))
            pit_sq_errs.append(perr ** 2)
        else:
            m_pit_vals.append(None)
            pit_abs_errs.append(None)
            pit_sq_errs.append(None)

    # ---- RMSE over all matched extrema (peaks + pits) ----
    all_sq  = [v for v in sq_errs + pit_sq_errs if v is not None]
    rmse    = float(np.sqrt(np.mean(all_sq))) if all_sq else float("nan")

    return dict(
        m_final=m_final,
        m_t_event=m_t_event,
        m_t_plot=m_t_plot,
        m_peaks=m_peaks,
        m_pits=m_pits,
        time_shift=time_shift,
        o_t_pks=o_t_pks,   o_vals=o_vals,
        m_vals=m_vals,      abs_errs=abs_errs,
        o_pit_t=o_pit_t,   o_pit_vals=o_pit_vals,
        m_pit_vals=m_pit_vals, pit_abs_errs=pit_abs_errs,
        rmse=rmse,
    )


# ---------------------------------------------------------------------------
# DISCOVER OPTITRACK + VARIANT CSVs
# ---------------------------------------------------------------------------
optitrack_csvs = {}   # trial_num → path
for path in glob.glob(os.path.join(OPTI_DIR, "trial_*_optitrack.csv")):
    m = re.search(r"trial_(\d+)_optitrack\.csv$", os.path.basename(path), re.I)
    if m:
        optitrack_csvs[m.group(1)] = path

print(f"OptiTrack CSVs found: {sorted(optitrack_csvs.keys())}")

# Pattern: P_1_Pos_2_H_Joint-Level_T_{trial}_{family}_{complexity}_{thr}.csv
CSV_RE = re.compile(
    r"P_1_Pos_2_H_Joint-Level_T_(\d+)_"
    r"(mediapipe|rtmpose|mmpose|fremocap)_(.+)\.csv$",
    re.IGNORECASE,
)

variants = collections.defaultdict(list)   # (trial, family) → [(complexity, thr, path)]
for path in glob.glob(os.path.join(MODEL_DIR, "P_1_*.csv")):
    m = CSV_RE.search(os.path.basename(path))
    if not m:
        continue
    trial, family, rest = m.group(1), m.group(2).lower(), m.group(3)
    parts = rest.rsplit("_", 1)
    complexity = parts[0] if len(parts) == 2 else rest
    threshold  = parts[1] if len(parts) == 2 else "?"
    variants[(trial, family)].append((complexity, threshold, path))

for key in variants:
    variants[key].sort(key=lambda x: (x[0], float(x[1]) if x[1] != "?" else 0))

n_variants = sum(len(v) for v in variants.values())
print(f"Found {n_variants} variant CSVs across {len(variants)} "
      f"(trial,family) groups.\n")


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
total_saved = 0

for trial_num, opti_path in sorted(optitrack_csvs.items()):
    print(f"=== Trial {trial_num} ===")
    try:
        o_t, o_ang, opti_fs = load_optitrack_p1(opti_path)
    except Exception as exc:
        print(f"  [OptiTrack error] {exc}")
        continue

    o_rel_idx, release_time_s = detect_master_anchor(o_t, o_ang, opti_fs)
    o_final   = align_and_normalize(o_ang, o_rel_idx)
    o_t_event = np.arange(len(o_final)) / opti_fs
    o_peaks   = find_all_peaks(o_final, opti_fs)
    o_peak1_t = float(o_t_event[o_peaks[0]]) if len(o_peaks) else 0.0

    print(f"  OptiTrack: {len(o_peaks)} swing peaks | "
          f"drop={release_time_s:.2f}s | fs={opti_fs} Hz")
    if len(o_peaks) == 0:
        print("  [warn] No OptiTrack peaks found — skipping trial.")
        continue

    best_per_family = {}   # populated below; used for summary grid after family loop

    for family in ["mediapipe", "rtmpose", "mmpose", "fremocap"]:
        key = (trial_num, family)
        if key not in variants:
            continue

        variant_list = variants[key]

        # Track best variant (lowest raw RMSE) for the summary grid
        _best_rmse_fam  = float("inf")
        _best_res_fam   = None
        _best_label_fam = ""

        # Build subplot grid: rows=complexities, cols=thresholds
        complexities = sorted(set(c for c, t, _ in variant_list))
        thresholds   = sorted(set(t for c, t, _ in variant_list),
                              key=lambda x: float(x) if x != "?" else 0)
        c_idx = {c: i for i, c in enumerate(complexities)}
        t_idx = {t: i for i, t in enumerate(thresholds)}
        nrows, ncols = len(complexities), len(thresholds)

        cmap = plt.colormaps.get_cmap(FAMILY_CMAPS[family])

        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(max(5 * ncols, 10), max(4 * nrows, 5)),
            squeeze=False,
        )
        fig.suptitle(
            f"Participant 1  |  Position {POSITION}  |  Trial {trial_num}  |  "
            f"{family.upper()} — all complexities × thresholds",
            fontsize=14, fontweight="bold", y=1.01,
        )

        plotted    = set()
        rmse_table = []    # (complexity, threshold, rmse) for summary print

        for complexity, threshold, csv_path in variant_list:
            row = c_idx[complexity]
            col = t_idx[threshold]
            ax  = axes[row][col]
            plotted.add((row, col))

            try:
                m_times, m_ang = load_variant_csv(csv_path)
            except Exception as exc:
                ax.set_title(f"{complexity}/{threshold}\n[load error]", fontsize=8)
                ax.axis("off")
                continue

            res      = analyze_variant(o_final, o_t_event, o_peaks, o_peak1_t,
                                       m_ang, m_times, release_time_s)
            rmse     = res["rmse"]
            rmse_str = f"{rmse:.1f}" if not math.isnan(rmse) else "N/A"
            rmse_table.append((complexity, threshold, rmse))

            # Keep track of the best-performing variant for the summary grid
            if not math.isnan(rmse) and rmse < _best_rmse_fam:
                _best_rmse_fam  = rmse
                _best_res_fam   = res
                _best_label_fam = f"{family} {complexity}  thr={threshold}"

            shade = cmap(0.4 + 0.5 * (row / max(nrows - 1, 1)))
            color = shade

            # X-axis: crop to first OptiTrack swing (t=0 = first peak)
            t_zero    = float(o_t_event[o_peaks[0]]) if len(o_peaks) else 0.0
            o_t_sh    = o_t_event - t_zero
            m_t_sh    = res["m_t_plot"] - t_zero   # time-shifted model axis
            o_mask    = o_t_sh >= -0.15
            m_mask    = m_t_sh >= -0.15

            # Waveforms
            ax.plot(o_t_sh[o_mask], o_final[o_mask],
                    color="black", linewidth=1.8, zorder=5, label="OptiTrack")
            ax.plot(m_t_sh[m_mask], res["m_final"][m_mask],
                    color=color, linewidth=1.8, alpha=0.85, zorder=4,
                    label=f"{family} {complexity}")

            # Peaks: filled dots; pits: open diamonds
            for i, (o_t_pk, o_val, m_val, abs_err) in enumerate(
                    zip(res["o_t_pks"], res["o_vals"],
                        res["m_vals"], res["abs_errs"])):
                xp = o_t_pk - t_zero
                ax.scatter(xp, o_val, color="black", s=65, zorder=8,
                           edgecolors="white", linewidths=1.0)
                ax.text(xp, o_val + 2.0, str(i + 1),
                        ha="center", va="bottom", fontsize=7,
                        color="black", fontweight="bold")
                if m_val is None:
                    continue
                if i < len(res["m_peaks"]):
                    m_t_pk = float(res["m_t_plot"][int(res["m_peaks"][i])]) - t_zero
                    ax.scatter(m_t_pk, m_val, color=color, s=55, zorder=7,
                               edgecolors="black", linewidths=0.7)
                ax.plot([xp, xp], [o_val, m_val],
                        color="gray", linewidth=1.1, linestyle="--",
                        alpha=0.6, zorder=3)
                if abs_err is not None:
                    ax.text(xp + 0.12, (o_val + m_val) / 2,
                            f"{abs_err:.1f}°",
                            ha="left", va="center",
                            fontsize=6.5, color="dimgray")

            for j, (o_pit_t_j, o_pv, m_pv, perr) in enumerate(
                    zip(res["o_pit_t"], res["o_pit_vals"],
                        res["m_pit_vals"], res["pit_abs_errs"])):
                xpit = o_pit_t_j - t_zero
                ax.scatter(xpit, o_pv, color="black", s=50, marker="D",
                           zorder=8, edgecolors="white", linewidths=0.8)
                if m_pv is None:
                    continue
                if j < len(res["m_pits"]):
                    m_pit_t = float(res["m_t_plot"][int(res["m_pits"][j])]) - t_zero
                    ax.scatter(m_pit_t, m_pv, color=color, s=45, marker="D",
                               zorder=7, edgecolors="black", linewidths=0.6,
                               facecolors="none")
                ax.plot([xpit, xpit], [o_pv, m_pv],
                        color="steelblue", linewidth=0.9, linestyle=":",
                        alpha=0.55, zorder=3)
                if perr is not None:
                    ax.text(xpit + 0.12, (o_pv + m_pv) / 2,
                            f"{perr:.1f}°",
                            ha="left", va="center",
                            fontsize=6, color="steelblue")

            # RMSE box — fixed lower-left to avoid title overlap
            ax.text(0.03, 0.97, f"RMSE = {rmse_str} deg",
                    transform=ax.transAxes,
                    ha="left", va="top",
                    fontsize=10, fontweight="bold", color=color,
                    bbox=dict(boxstyle="round,pad=0.4",
                              facecolor="white", edgecolor=color,
                              linewidth=2.0, alpha=0.97))

            swing_dur = PLOT_XLIM - t_zero
            ax.set_xlim(-0.2, max(swing_dur, 8.0))
            ax.set_ylim(bottom=0)
            ax.set_title(f"complexity={complexity}  |  threshold={threshold}",
                         fontsize=9, fontweight="bold")
            ax.set_xlabel("Time since first swing (s)", fontsize=8)
            ax.set_ylabel("Knee Flexion (deg)", fontsize=8)
            ax.legend(loc="upper right", fontsize=7, framealpha=0.85)
            ax.grid(True, linestyle=":", alpha=0.35)
            ax.tick_params(labelsize=7)

        # Hide unused cells
        for r in range(nrows):
            for c in range(ncols):
                if (r, c) not in plotted:
                    axes[r][c].set_visible(False)

        fig.tight_layout()
        out_name = f"Grid_P1_Pos2_Trial{trial_num}_{family}.png"
        fig.savefig(os.path.join(OUTPUT_DIR, out_name),
                    dpi=150, bbox_inches="tight")
        plt.close(fig)
        total_saved += 1

        # Print RMSE summary for this family / trial
        best = min((r for r in rmse_table if not math.isnan(r[2])),
                   key=lambda x: x[2], default=None)
        print(f"  {family:<12} {nrows}x{ncols} grid | "
              f"{len(variant_list)} variants | "
              f"best: complexity={best[0]} threshold={best[1]} "
              f"RMSE={best[2]:.1f} deg" if best else
              f"  {family:<12} no valid variants")
        print(f"  Saved: {out_name}")

        # Store best result for the per-trial summary grid
        if _best_res_fam is not None:
            best_per_family[family] = {
                "res":   _best_res_fam,
                "label": _best_label_fam,
                "rmse":  _best_rmse_fam,
            }

    # -----------------------------------------------------------------------
    # SUMMARY GRID — one subplot per model family, best variant vs OptiTrack
    # -----------------------------------------------------------------------
    FAMILY_SOLID  = {"mediapipe": "#1976D2", "rtmpose": "#E65100",
                     "mmpose":    "#2E7D32", "fremocap": "#6A1B9A"}
    FAMILY_MARKER = {"mediapipe": "^", "rtmpose": "s",
                     "mmpose":    "D", "fremocap": "P"}

    fams_present = [f for f in ["mediapipe", "rtmpose", "mmpose", "fremocap"]
                    if f in best_per_family]
    if fams_present:
        n_fam     = len(fams_present)
        ncols_sum = 2
        nrows_sum = math.ceil(n_fam / ncols_sum)
        t_zero    = float(o_t_event[o_peaks[0]]) if len(o_peaks) else 0.0

        fig_sum, axes_sum = plt.subplots(
            nrows_sum, ncols_sum,
            figsize=(16, 5.5 * nrows_sum), squeeze=False,
        )
        fig_sum.suptitle(
            f"Participant 1  |  Position {POSITION}  |  Trial {trial_num}  |  "
            f"All Models vs OptiTrack  (best variant per family)",
            fontsize=14, fontweight="bold", y=1.01,
        )

        o_t_sh = o_t_event - t_zero
        o_mask = o_t_sh >= -0.15

        for idx, fam in enumerate(fams_present):
            r_s, c_s = divmod(idx, ncols_sum)
            ax_s  = axes_sum[r_s][c_s]
            d     = best_per_family[fam]
            res   = d["res"]
            color = FAMILY_SOLID.get(fam, "gray")
            mkr   = FAMILY_MARKER.get(fam, "o")

            m_t_sh = res["m_t_plot"] - t_zero   # time-shifted model axis
            m_mask = m_t_sh >= -0.15

            # Waveforms
            ax_s.plot(o_t_sh[o_mask], o_final[o_mask],
                      color="black", linewidth=2.0, zorder=5, label="OptiTrack")
            ax_s.plot(m_t_sh[m_mask], res["m_final"][m_mask],
                      color=color, linewidth=2.0, alpha=0.88, zorder=4,
                      label=d["label"])

            # Peaks: filled dots; pits: open diamonds
            for i, (o_t_pk, o_val, m_val, abs_err) in enumerate(
                    zip(res["o_t_pks"], res["o_vals"],
                        res["m_vals"], res["abs_errs"])):
                xp = o_t_pk - t_zero
                ax_s.scatter(xp, o_val, color="black", s=75, zorder=8,
                             edgecolors="white", linewidths=1.0)
                ax_s.text(xp, o_val + 2.0, str(i + 1),
                          ha="center", va="bottom", fontsize=8,
                          color="black", fontweight="bold")
                if m_val is None:
                    continue
                if i < len(res["m_peaks"]):
                    m_t_pk_s = float(res["m_t_plot"][int(res["m_peaks"][i])]) - t_zero
                    ax_s.scatter(m_t_pk_s, m_val, color=color, marker=mkr,
                                 s=75, zorder=7, edgecolors="black", linewidths=0.8)
                ax_s.plot([xp, xp], [o_val, m_val],
                          color="gray", linewidth=1.2, linestyle="--",
                          alpha=0.55, zorder=3)
                if abs_err is not None:
                    ax_s.text(xp + 0.15, (o_val + m_val) / 2,
                              f"{abs_err:.1f}°",
                              ha="left", va="center",
                              fontsize=7.5, color="dimgray")

            for j, (o_pit_t_j, o_pv, m_pv, perr) in enumerate(
                    zip(res["o_pit_t"], res["o_pit_vals"],
                        res["m_pit_vals"], res["pit_abs_errs"])):
                xpit = o_pit_t_j - t_zero
                ax_s.scatter(xpit, o_pv, color="black", s=60, marker="D",
                             zorder=8, edgecolors="white", linewidths=0.8)
                if m_pv is None:
                    continue
                if j < len(res["m_pits"]):
                    m_pit_t = float(res["m_t_plot"][int(res["m_pits"][j])]) - t_zero
                    ax_s.scatter(m_pit_t, m_pv, color=color, s=55, marker="D",
                                 zorder=7, edgecolors="black", linewidths=0.7,
                                 facecolors="none")
                ax_s.plot([xpit, xpit], [o_pv, m_pv],
                          color="steelblue", linewidth=1.0, linestyle=":",
                          alpha=0.55, zorder=3)
                if perr is not None:
                    ax_s.text(xpit + 0.15, (o_pv + m_pv) / 2,
                              f"{perr:.1f}°",
                              ha="left", va="center",
                              fontsize=7, color="steelblue")

            rmse_str_s = f"{d['rmse']:.1f}" if not math.isnan(d["rmse"]) else "N/A"
            ax_s.text(0.03, 0.97, f"RMSE = {rmse_str_s} deg",
                      transform=ax_s.transAxes,
                      ha="left", va="top",
                      fontsize=13, fontweight="bold", color=color,
                      bbox=dict(boxstyle="round,pad=0.45",
                                facecolor="white", edgecolor=color,
                                linewidth=2.5, alpha=0.97))

            ax_s.set_title(fam.upper(), fontsize=13, fontweight="bold", color=color)
            ax_s.set_xlim(-0.2, max(PLOT_XLIM - t_zero, 8.0))
            ax_s.set_ylim(bottom=0)
            ax_s.set_xlabel("Time since first swing (s)", fontsize=9)
            ax_s.set_ylabel("Knee Flexion (deg)", fontsize=9)
            ax_s.legend(loc="upper right", fontsize=8, framealpha=0.88)
            ax_s.grid(True, linestyle=":", alpha=0.35)
            ax_s.tick_params(labelsize=8)

        # Hide unused cells
        for idx in range(n_fam, nrows_sum * ncols_sum):
            rr, cc = divmod(idx, ncols_sum)
            axes_sum[rr][cc].set_visible(False)

        fig_sum.tight_layout()
        sum_name = f"Grid_P1_Pos2_Trial{trial_num}_Summary.png"
        fig_sum.savefig(os.path.join(OUTPUT_DIR, sum_name),
                        dpi=150, bbox_inches="tight")
        plt.close(fig_sum)
        total_saved += 1
        print(f"  Summary grid saved: {sum_name}")

    print()

# ---------------------------------------------------------------------------
# ACCURACY RANKING  (best variant per family across all trials)
# ---------------------------------------------------------------------------
print("=" * 65)
print("ACCURACY RANKING — best variant per family (lowest RMSE)")
print("=" * 65)

from scipy import stats as scipy_stats

# Re-run all variants to collect all records
all_records = []

for trial_num, opti_path in sorted(optitrack_csvs.items()):
    try:
        o_t, o_ang, opti_fs = load_optitrack_p1(opti_path)
    except Exception:
        continue
    o_rel_idx, release_time_s = detect_master_anchor(o_t, o_ang, opti_fs)
    o_final   = align_and_normalize(o_ang, o_rel_idx)
    o_t_event = np.arange(len(o_final)) / opti_fs
    o_peaks   = find_all_peaks(o_final, opti_fs)
    o_peak1_t = float(o_t_event[o_peaks[0]]) if len(o_peaks) else 0.0

    for family in ["mediapipe", "rtmpose", "mmpose", "fremocap"]:
        for complexity, threshold, csv_path in variants.get((trial_num, family), []):
            try:
                m_times, m_ang = load_variant_csv(csv_path)
            except Exception:
                continue
            res = analyze_variant(o_final, o_t_event, o_peaks, o_peak1_t,
                                  m_ang, m_times, release_time_s)
            for i, (o_val, m_val, abs_err) in enumerate(
                    zip(res["o_vals"], res["m_vals"], res["abs_errs"])):
                if m_val is not None and abs_err is not None:
                    all_records.append({
                        "trial":       trial_num,
                        "family":      family,
                        "extremum":    "peak",
                        "complexity":  complexity,
                        "threshold":   threshold,
                        "variant":     f"{family}_{complexity}_{threshold}",
                        "swing":       i + 1,
                        "opti_deg":    round(o_val, 2),
                        "model_deg":   round(m_val, 2),
                        "error_deg":   round(m_val - o_val, 2),
                        "abs_err":     round(abs_err, 2),
                    })
            # pits
            for j, (o_pv, m_pv, perr) in enumerate(
                    zip(res["o_pit_vals"], res["m_pit_vals"], res["pit_abs_errs"])):
                if m_pv is not None and perr is not None:
                    all_records.append({
                        "trial":       trial_num,
                        "family":      family,
                        "extremum":    "pit",
                        "complexity":  complexity,
                        "threshold":   threshold,
                        "variant":     f"{family}_{complexity}_{threshold}",
                        "swing":       j + 1,
                        "opti_deg":    round(o_pv, 2),
                        "model_deg":   round(m_pv, 2),
                        "error_deg":   round(m_pv - o_pv, 2),
                        "abs_err":     round(perr, 2),
                    })

if all_records:
    df_all = pd.DataFrame(all_records)

    # Per-variant RMSE across all trials
    ranking = (df_all.groupby("variant")
               .apply(lambda g: pd.Series({
                   "family":     g["family"].iloc[0],
                   "complexity": g["complexity"].iloc[0],
                   "threshold":  g["threshold"].iloc[0],
                   "n_swings":   len(g),
                   "rmse_deg":   round(float(np.sqrt(np.mean(g["abs_err"]**2))), 2),
                   "mae_deg":    round(float(g["abs_err"].mean()), 2),
               }))
               .sort_values("rmse_deg")
               .reset_index())
    ranking.index += 1

    print(ranking[["variant", "n_swings", "rmse_deg", "mae_deg"]].to_string())
    print()

    out_csv = os.path.join(OUTPUT_DIR, "accuracy_ranking_participant_1.csv")
    ranking.to_csv(out_csv, index=True)
    print(f"Saved ranking: {out_csv}")

    out_csv2 = os.path.join(OUTPUT_DIR, "all_swing_errors_participant_1.csv")
    df_all.to_csv(out_csv2, index=False)
    print(f"Saved all errors: {out_csv2}")

print(f"\nDone. {total_saved} grid graphs -> {OUTPUT_DIR}")
