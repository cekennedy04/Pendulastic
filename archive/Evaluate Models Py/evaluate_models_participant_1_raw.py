"""
evaluate_models_participant_1_raw.py
=====================================
Re-evaluation of Participant 1 HPE models vs OptiTrack with corrected
signal processing.

Key differences from evaluate_models_participant_1_grid.py:
  - Model release moment is detected from the MODEL SIGNAL ITSELF (last frame
    near full extension before the first swing), not from OptiTrack sync timing.
  - Flex signal = deviation from release angle (not deviation from pre-release mean).
    For model:    model_flex = release_angle - knee_angle_deg  (positive = flexion)
    For OptiTrack: opti_flex = quat_magnitude - quat_at_release (positive = flexion)
  - Both signals start at 0 at the release moment and go positive during flexion.
  - Raw angles plotted alongside flex signals so the user can verify values.
  - RMSE computed at matched oscillation extrema (peaks + pits).
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
BASE_DIR   = r"C:\Users\cladi\Pendulastic"
OPTI_DIR   = os.path.join(BASE_DIR, "OptiTrack_Recordings",
                           "Participant_1", "Position_2", "Height_Joint-Level")
MODEL_DIR  = os.path.join(BASE_DIR, "Recordings",
                           "Participant_1", "Position_2", "Height_Joint-Level")
OUTPUT_DIR = os.path.join(BASE_DIR, "pendulastic model analysis",
                           "grid_participant_1_raw")
os.makedirs(OUTPUT_DIR, exist_ok=True)

POSITION     = "2"
PARTICIPANT  = "1"
MODEL_FPS    = 30
PLOT_XLIM    = 14.0      # seconds shown after release on x-axis
PEAK_PROM    = 3.0       # minimum peak prominence (deg)
BASELINE_SEC = 3.0       # OptiTrack baseline duration

FAMILY_CMAPS  = {"mediapipe": "Blues", "rtmpose": "Oranges",
                 "mmpose": "Greens", "fremocap": "Purples", "yolo": "Reds"}
FAMILY_SOLID  = {"mediapipe": "#1976D2", "rtmpose": "#E65100",
                 "mmpose": "#2E7D32",    "fremocap": "#6A1B9A", "yolo": "#B71C1C"}
FAMILY_MARKER = {"mediapipe": "^", "rtmpose": "s", "mmpose": "D", "fremocap": "P",
                 "yolo": "o"}


# ---------------------------------------------------------------------------
# OPTITRACK LOADER  (Thigh + Shank quaternions → inter-segment rotation mag)
# ---------------------------------------------------------------------------
def load_optitrack_p1(csv_path):
    header_idx = 0
    with open(csv_path, "r", encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header_idx = i
                break
    df = pd.read_csv(csv_path, skiprows=header_idx, header=0)
    df = df.apply(pd.to_numeric, errors="coerce").ffill().bfill()
    t  = df.iloc[:, 1].values.astype(float);  t -= t[0]
    fs = round(1.0 / float(np.median(np.diff(t[t > 0]))))
    tx,ty,tz,tw = (df.iloc[:,c].values.astype(float) for c in [2,3,4,5])
    sx,sy,sz,sw = (df.iloc[:,c].values.astype(float) for c in [9,10,11,12])
    r_thigh  = R.from_quat(np.column_stack([tx,ty,tz,tw]))
    r_shank  = R.from_quat(np.column_stack([sx,sy,sz,sw]))
    knee_deg = np.degrees((r_thigh.inv() * r_shank).magnitude())
    return t, knee_deg, int(fs)


# ---------------------------------------------------------------------------
# OPTITRACK RELEASE DETECTOR
# ---------------------------------------------------------------------------
def detect_optitrack_release(o_t, o_ang, opti_fs):
    """
    Find the release moment: first sample where the angle deviates
    significantly from the pre-release baseline.
    Returns (release_idx, release_time, release_angle, baseline_angle).
    """
    n_base   = int(BASELINE_SEC * opti_fs)
    baseline = float(np.nanmean(o_ang[:n_base]))
    search   = np.abs(o_ang[n_base:] - baseline)
    for thr in [5.0, 2.5, 2.0, 1.0]:
        hits = np.where(search > thr)[0]
        if len(hits):
            idx = int(n_base + hits[0])
            return idx, float(o_t[idx]), float(o_ang[idx]), baseline
    return 0, float(o_t[0]), float(o_ang[0]), baseline


# ---------------------------------------------------------------------------
# MODEL RELEASE DETECTOR  (key new function)
# ---------------------------------------------------------------------------
def detect_model_release(m_ang, m_t, plateau_thr=165.0, first_drop_thr=100.0):
    """
    Identify the release moment in a model knee-angle CSV.

    Logic:
      1. The leg starts at natural hang (~120 deg), rises as the examiner extends
         it to ~175-179 deg (plateau_thr = 165 ensures we capture this plateau).
      2. The examiner then releases; the angle drops to ~82 deg on the first swing
         (first_drop_thr = 100 marks 'deep flexion').
      3. We return the LAST frame above plateau_thr that precedes the FIRST frame
         below first_drop_thr — i.e. the moment just before the first swing began.

    Falls back gracefully when the model never reaches the plateau threshold.
    Returns (idx, time_s, angle_deg).
    """
    # Find first frame that enters deep flexion
    below = np.where(m_ang < first_drop_thr)[0]
    if len(below) == 0:
        # Never reaches deep flexion — use global argmax
        idx = int(np.argmax(m_ang))
        return idx, float(m_t[idx]), float(m_ang[idx])

    first_deep = int(below[0])
    if first_deep == 0:
        # Very first frame already below threshold — use global argmax
        idx = int(np.argmax(m_ang))
        return idx, float(m_t[idx]), float(m_ang[idx])

    # Last frame above plateau_thr before first_deep
    mask  = (np.arange(len(m_ang)) < first_deep) & (m_ang >= plateau_thr)
    above = np.where(mask)[0]
    if len(above) > 0:
        idx = int(above[-1])
        return idx, float(m_t[idx]), float(m_ang[idx])

    # Plateau never reached — try a lower threshold (model may not see full extension)
    for fallback_thr in [155.0, 145.0, 135.0, 125.0]:
        mask  = (np.arange(len(m_ang)) < first_deep) & (m_ang >= fallback_thr)
        above = np.where(mask)[0]
        if len(above) > 0:
            idx = int(above[-1])
            return idx, float(m_t[idx]), float(m_ang[idx])

    # Last resort: global argmax before first_deep
    sub = m_ang[:first_deep]
    idx = int(np.argmax(sub)) if len(sub) > 0 else 0
    return idx, float(m_t[idx]), float(m_ang[idx])


# ---------------------------------------------------------------------------
# LOAD MODEL CSV
# ---------------------------------------------------------------------------
def load_variant_csv(csv_path):
    df = pd.read_csv(csv_path)
    return (df["time_sec"].values.astype(float),
            df["knee_angle_deg"].values.astype(float))


# ---------------------------------------------------------------------------
# FLEX SIGNAL HELPERS
# ---------------------------------------------------------------------------
def make_opti_flex(o_ang, o_rel_idx, o_rel_ang):
    """
    OptiTrack flex = (quat_magnitude - magnitude_at_release).
    Positive values = more rotation than at release = knee flexion.
    The signal may go slightly negative during extension past the release point.
    """
    return o_ang[o_rel_idx:] - o_rel_ang


def make_model_flex(m_ang, m_rel_idx, m_rel_ang):
    """
    Model flex = (release_angle - knee_angle_deg).
    Positive values = knee angle dropped below release angle = flexion.
    """
    return m_rel_ang - m_ang[m_rel_idx:]


def find_flex_peaks(flex_sig, fs):
    """Find peaks (maximum flexion moments) in a flex signal."""
    guard    = max(1, int(0.1 * fs))
    min_dist = max(5, int(0.3 * fs))
    max_h    = float(np.nanmax(flex_sig))
    min_h    = max(5.0, max_h * 0.20)
    pks, _   = find_peaks(flex_sig[guard:], distance=min_dist,
                           prominence=PEAK_PROM, height=min_h)
    return pks + guard


def find_pits(signal, peaks):
    """Minimum of signal between consecutive peak pairs."""
    pits = []
    for i in range(len(peaks) - 1):
        s = int(peaks[i]); e = min(int(peaks[i+1]) + 1, len(signal))
        pits.append(int(np.argmin(signal[s:e])) + s)
    return np.array(pits, dtype=int)


# ---------------------------------------------------------------------------
# ANALYZE ONE VARIANT
# ---------------------------------------------------------------------------
def analyze_variant(o_flex, o_t_rel, o_fs,
                    m_ang, m_t, m_rel_idx, m_rel_ang):
    """
    Compare one model variant to OptiTrack.

    Parameters
    ----------
    o_flex     : OptiTrack flex signal (from release onward)
    o_t_rel    : OptiTrack time array from release (starts at 0)
    o_fs       : OptiTrack sample rate
    m_ang      : raw model knee_angle_deg (full CSV)
    m_t        : raw model time_sec (full CSV)
    m_rel_idx  : index of model release in m_ang/m_t
    m_rel_ang  : model angle at release

    Returns dict of results.
    """
    m_flex    = make_model_flex(m_ang, m_rel_idx, m_rel_ang)
    m_t_rel   = m_t[m_rel_idx:] - m_t[m_rel_idx]

    o_peaks   = find_flex_peaks(o_flex, o_fs)
    m_peaks   = find_flex_peaks(m_flex, MODEL_FPS)
    o_pits    = find_pits(o_flex, o_peaks)
    m_pits    = find_pits(m_flex, m_peaks)

    # Align model's first peak to OptiTrack's first peak
    if len(o_peaks) > 0 and len(m_peaks) > 0:
        time_shift = float(o_t_rel[o_peaks[0]]) - float(m_t_rel[m_peaks[0]])
    else:
        time_shift = 0.0
    m_t_plot = m_t_rel + time_shift

    # ---- match peaks ----
    o_t_pks, o_pk_v, m_pk_v, pk_sq = [], [], [], []
    for i, op in enumerate(o_peaks):
        ov = float(o_flex[op])
        o_t_pks.append(float(o_t_rel[op])); o_pk_v.append(ov)
        if i < len(m_peaks):
            mv  = float(m_flex[m_peaks[i]])
            m_pk_v.append(mv); pk_sq.append((mv - ov)**2)
        else:
            m_pk_v.append(None); pk_sq.append(None)

    # ---- match pits ----
    o_t_pts, o_pt_v, m_pt_v, pt_sq = [], [], [], []
    for j, op in enumerate(o_pits):
        ov = float(o_flex[op])
        o_t_pts.append(float(o_t_rel[op])); o_pt_v.append(ov)
        if j < len(m_pits):
            mv  = float(m_flex[m_pits[j]])
            m_pt_v.append(mv); pt_sq.append((mv - ov)**2)
        else:
            m_pt_v.append(None); pt_sq.append(None)

    all_sq = [v for v in pk_sq + pt_sq if v is not None]
    rmse   = float(np.sqrt(np.mean(all_sq))) if all_sq else float("nan")

    return dict(
        m_flex=m_flex, m_t_rel=m_t_rel, m_t_plot=m_t_plot,
        m_peaks=m_peaks, m_pits=m_pits,
        o_peaks=o_peaks, o_pits=o_pits,
        time_shift=time_shift,
        o_t_pks=o_t_pks, o_pk_v=o_pk_v, m_pk_v=m_pk_v,
        o_t_pts=o_t_pts, o_pt_v=o_pt_v, m_pt_v=m_pt_v,
        rmse=rmse,
        m_rel_ang=m_rel_ang,
        n_matched_peaks=sum(1 for v in m_pk_v if v is not None),
        n_matched_pits=sum(1 for v in m_pt_v if v is not None),
    )


# ---------------------------------------------------------------------------
# PLOTTING HELPER
# ---------------------------------------------------------------------------
def plot_variant_on_ax(ax, o_flex, o_t_rel, res, color, label, t_zero=0.0):
    """
    Draw one variant on a single Axes:
      - OptiTrack flex signal (black)
      - Model flex signal (color)
      - Peak dots and pit diamonds with error lines
    """
    # Shift so t=0 is at first OptiTrack peak
    o_t = o_t_rel - t_zero
    m_t = res["m_t_plot"] - t_zero

    o_mask = o_t >= -0.2
    m_mask = m_t >= -0.2

    ax.plot(o_t[o_mask], o_flex[o_mask],
            color="black", lw=1.8, zorder=5, label="OptiTrack")
    ax.plot(m_t[m_mask], res["m_flex"][m_mask],
            color=color, lw=1.8, alpha=0.85, zorder=4, label=label)

    # Peaks — filled circles
    for i, (ot, ov, mv) in enumerate(zip(res["o_t_pks"], res["o_pk_v"], res["m_pk_v"])):
        xp = ot - t_zero
        ax.scatter(xp, ov, color="black", s=60, zorder=8,
                   edgecolors="white", linewidths=0.9)
        ax.text(xp, ov + 1.5, str(i+1),
                ha="center", va="bottom", fontsize=7, color="black", fontweight="bold")
        if mv is None:
            continue
        if i < len(res["m_peaks"]):
            xm = float(res["m_t_plot"][res["m_peaks"][i]]) - t_zero
            ax.scatter(xm, mv, color=color, s=50, zorder=7,
                       edgecolors="black", linewidths=0.7)
        err = abs(mv - ov)
        ax.plot([xp, xp], [ov, mv],
                color="gray", lw=1.0, ls="--", alpha=0.55, zorder=3)
        ax.text(xp + 0.1, (ov + mv) / 2,
                f"{err:.1f}", ha="left", va="center",
                fontsize=6.5, color="dimgray")

    # Pits — open diamonds
    for j, (ot, ov, mv) in enumerate(zip(res["o_t_pts"], res["o_pt_v"], res["m_pt_v"])):
        xp = ot - t_zero
        ax.scatter(xp, ov, color="black", s=45, marker="D", zorder=8,
                   edgecolors="white", linewidths=0.8)
        if mv is None:
            continue
        if j < len(res["m_pits"]):
            xm = float(res["m_t_plot"][res["m_pits"][j]]) - t_zero
            ax.scatter(xm, mv, color=color, s=40, marker="D", zorder=7,
                       facecolors="none", edgecolors=color, linewidths=0.7)
        err = abs(mv - ov)
        ax.plot([xp, xp], [ov, mv],
                color="steelblue", lw=0.9, ls=":", alpha=0.5, zorder=3)
        ax.text(xp + 0.1, (ov + mv) / 2,
                f"{err:.1f}", ha="left", va="center",
                fontsize=6, color="steelblue")


# ---------------------------------------------------------------------------
# DISCOVER FILES
# ---------------------------------------------------------------------------
optitrack_csvs = {}
for path in glob.glob(os.path.join(OPTI_DIR, "trial_*_optitrack.csv")):
    m = re.search(r"trial_(\d+)_optitrack\.csv$", os.path.basename(path), re.I)
    if m:
        optitrack_csvs[m.group(1)] = path

print(f"OptiTrack CSVs: {sorted(optitrack_csvs.keys())}")

CSV_RE = re.compile(
    r"P_1_Pos_2_H_Joint-Level_T_(\d+)_"
    r"([A-Za-z][A-Za-z0-9_]*)_(.+)\.csv$",
)
variants = collections.defaultdict(list)
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

n_var = sum(len(v) for v in variants.values())
print(f"Model CSVs: {n_var} variants in {len(variants)} (trial, family) groups.\n")


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
total_saved  = 0
all_records  = []

for trial_num, opti_path in sorted(optitrack_csvs.items()):
    print(f"=== Trial {trial_num} ===")
    try:
        o_t, o_ang, o_fs = load_optitrack_p1(opti_path)
    except Exception as exc:
        print(f"  [OptiTrack load error] {exc}"); continue

    o_rel_idx, o_rel_t, o_rel_ang, o_baseline = detect_optitrack_release(
        o_t, o_ang, o_fs)

    # OptiTrack flex signal (positive = flexion)
    o_flex    = make_opti_flex(o_ang, o_rel_idx, o_rel_ang)
    o_t_rel   = o_t[o_rel_idx:] - o_t[o_rel_idx]

    # OptiTrack peaks (used for peak-matching inside analyze_variant)
    o_peaks_global = find_flex_peaks(o_flex, o_fs)
    t_zero = 0.0   # x-axis origin = release moment for both signals

    print(f"  OptiTrack: release t={o_rel_t:.3f}s, angle_at_release={o_rel_ang:.2f} deg, "
          f"baseline={o_baseline:.2f} deg, n_peaks={len(o_peaks_global)}")

    if len(o_peaks_global) == 0:
        print("  [warn] No OptiTrack peaks found — skipping trial."); continue

    best_per_family = {}

    for family in sorted(set(fam for (t, fam) in variants if t == trial_num)):
        key = (trial_num, family)
        if key not in variants:
            continue

        variant_list = variants[key]
        complexities = sorted(set(c for c,_,_ in variant_list))
        thresholds   = sorted(set(t for _,t,_ in variant_list),
                              key=lambda x: float(x) if x != "?" else 0)
        c_idx = {c: i for i, c in enumerate(complexities)}
        t_idx = {t: i for i, t in enumerate(thresholds)}
        nrows, ncols = len(complexities), len(thresholds)

        cmap  = plt.colormaps.get_cmap(FAMILY_CMAPS.get(family, "Greys"))
        fig, axes = plt.subplots(
            nrows, ncols,
            figsize=(max(5*ncols, 10), max(4*nrows, 5)),
            squeeze=False,
        )
        fig.suptitle(
            f"P1 | Pos {POSITION} | Trial {trial_num} | {family.upper()}  —  "
            f"Knee Flex Deviation from Release (deg)  |  t=0 = release moment",
            fontsize=13, fontweight="bold", y=1.01,
        )

        plotted   = set()
        rmse_tab  = []
        _best_rmse = float("inf"); _best_res = None; _best_label = ""

        for complexity, threshold, csv_path in variant_list:
            row = c_idx[complexity]; col = t_idx[threshold]
            ax  = axes[row][col]; plotted.add((row, col))

            try:
                m_t, m_ang = load_variant_csv(csv_path)
            except Exception as exc:
                ax.set_title(f"{complexity}/{threshold}\n[load error]", fontsize=8)
                ax.axis("off"); continue

            m_rel_idx, m_rel_t, m_rel_ang = detect_model_release(m_ang, m_t)

            res   = analyze_variant(o_flex, o_t_rel, o_fs,
                                    m_ang, m_t, m_rel_idx, m_rel_ang)
            rmse  = res["rmse"]
            rmse_tab.append((complexity, threshold, rmse))

            if not math.isnan(rmse) and rmse < _best_rmse:
                _best_rmse = rmse; _best_res = res
                _best_label = f"{family} {complexity}  thr={threshold}"

            shade = cmap(0.4 + 0.5 * (row / max(nrows-1, 1)))
            plot_variant_on_ax(ax, o_flex, o_t_rel, res, shade,
                               f"{family} {complexity}", t_zero=t_zero)

            rmse_str = f"{rmse:.1f}" if not math.isnan(rmse) else "N/A"
            n_ext = res["n_matched_peaks"] + res["n_matched_pits"]
            ax.text(0.03, 0.97,
                    f"RMSE = {rmse_str} deg  (n={n_ext})",
                    transform=ax.transAxes, ha="left", va="top",
                    fontsize=10, fontweight="bold", color=shade,
                    bbox=dict(boxstyle="round,pad=0.4",
                              facecolor="white", edgecolor=shade,
                              linewidth=2.0, alpha=0.97))

            ax.set_xlim(-0.3, PLOT_XLIM - t_zero)
            ax.set_ylim(bottom=-5)
            ax.set_title(f"complexity={complexity}  |  threshold={threshold}",
                         fontsize=9, fontweight="bold")
            ax.set_xlabel("Time since release (s)", fontsize=8)
            ax.set_ylabel("Knee Flexion Deviation (deg)", fontsize=8)
            ax.axhline(0, color="gray", lw=0.6, ls=":", alpha=0.5)
            ax.legend(loc="upper right", fontsize=7, framealpha=0.85)
            ax.grid(True, ls=":", alpha=0.35); ax.tick_params(labelsize=7)

            # Records for ranking
            for i, (ov, mv) in enumerate(zip(res["o_pk_v"], res["m_pk_v"])):
                if mv is not None:
                    all_records.append(dict(
                        trial=trial_num, family=family, extremum="peak",
                        complexity=complexity, threshold=threshold,
                        variant=f"{family}_{complexity}_{threshold}",
                        swing=i+1, opti_deg=round(ov,2), model_deg=round(mv,2),
                        error_deg=round(mv-ov,2), abs_err=round(abs(mv-ov),2)))
            for j, (ov, mv) in enumerate(zip(res["o_pt_v"], res["m_pt_v"])):
                if mv is not None:
                    all_records.append(dict(
                        trial=trial_num, family=family, extremum="pit",
                        complexity=complexity, threshold=threshold,
                        variant=f"{family}_{complexity}_{threshold}",
                        swing=j+1, opti_deg=round(ov,2), model_deg=round(mv,2),
                        error_deg=round(mv-ov,2), abs_err=round(abs(mv-ov),2)))

        # Hide unused cells
        for r in range(nrows):
            for c in range(ncols):
                if (r, c) not in plotted:
                    axes[r][c].set_visible(False)

        fig.tight_layout()
        name = f"Raw_P1_Pos2_Trial{trial_num}_{family}.png"
        fig.savefig(os.path.join(OUTPUT_DIR, name), dpi=150, bbox_inches="tight")
        plt.close(fig); total_saved += 1

        best = min((r for r in rmse_tab if not math.isnan(r[2])),
                   key=lambda x: x[2], default=None)
        if best:
            print(f"  {family:<12} {nrows}x{ncols} grid | "
                  f"best complexity={best[0]} thr={best[1]} RMSE={best[2]:.1f} deg")
        print(f"  Saved: {name}")

        if _best_res is not None:
            best_per_family[family] = {"res": _best_res, "label": _best_label,
                                        "rmse": _best_rmse}

    # -------------------------------------------------------------------
    # SUMMARY GRID (best variant per family) + RAW ANGLE DIAGNOSTIC
    # -------------------------------------------------------------------
    fams_present = sorted(best_per_family.keys())
    if fams_present:
        n_fam     = len(fams_present)
        ncols_sum = 2
        nrows_sum = math.ceil(n_fam / ncols_sum)

        # ---- Panel A: flex-signal summary ----
        fig_s, axes_s = plt.subplots(
            nrows_sum, ncols_sum,
            figsize=(16, 5 * nrows_sum), squeeze=False,
        )
        fig_s.suptitle(
            f"P1 | Pos {POSITION} | Trial {trial_num} | "
            f"Best Variant per Family  —  Knee Flex Deviation from Release",
            fontsize=13, fontweight="bold", y=1.01,
        )
        for idx, fam in enumerate(fams_present):
            r_s, c_s = divmod(idx, ncols_sum)
            ax_s = axes_s[r_s][c_s]
            d    = best_per_family[fam]
            color = FAMILY_SOLID.get(fam, "gray")
            mkr   = FAMILY_MARKER.get(fam, "o")

            plot_variant_on_ax(ax_s, o_flex, o_t_rel, d["res"], color,
                               d["label"], t_zero=t_zero)

            rmse_str = f"{d['rmse']:.1f}" if not math.isnan(d["rmse"]) else "N/A"
            n_ext    = d["res"]["n_matched_peaks"] + d["res"]["n_matched_pits"]
            ax_s.text(0.03, 0.97, f"RMSE = {rmse_str} deg  (n={n_ext})",
                      transform=ax_s.transAxes, ha="left", va="top",
                      fontsize=13, fontweight="bold", color=color,
                      bbox=dict(boxstyle="round,pad=0.45",
                                facecolor="white", edgecolor=color,
                                linewidth=2.5, alpha=0.97))
            ax_s.set_title(fam.upper(), fontsize=13, fontweight="bold", color=color)
            ax_s.set_xlim(-0.3, PLOT_XLIM - t_zero)
            ax_s.set_ylim(bottom=-5)
            ax_s.set_xlabel("Time since release (s)", fontsize=9)
            ax_s.set_ylabel("Knee Flexion Deviation (deg)", fontsize=9)
            ax_s.axhline(0, color="gray", lw=0.6, ls=":", alpha=0.5)
            ax_s.legend(loc="upper right", fontsize=8, framealpha=0.88)
            ax_s.grid(True, ls=":", alpha=0.35); ax_s.tick_params(labelsize=8)

        for idx in range(n_fam, nrows_sum * ncols_sum):
            rr, cc = divmod(idx, ncols_sum)
            axes_s[rr][cc].set_visible(False)

        fig_s.tight_layout()
        sum_name = f"Raw_P1_Pos2_Trial{trial_num}_Summary.png"
        fig_s.savefig(os.path.join(OUTPUT_DIR, sum_name), dpi=150, bbox_inches="tight")
        plt.close(fig_s); total_saved += 1
        print(f"  Flex summary: {sum_name}")

        # ---- Panel B: RAW angle diagnostic (one row per family) ----
        # Shows actual model knee_angle_deg and actual OptiTrack quat-magnitude
        # on the SAME plot with dual y-axes so the user can verify raw values.
        fig_raw, axes_raw = plt.subplots(
            n_fam, 1,
            figsize=(14, 4 * n_fam), squeeze=False,
            sharex=False,
        )
        fig_raw.suptitle(
            f"P1 | Pos {POSITION} | Trial {trial_num} | "
            f"RAW Knee Angles — Model (left y) vs OptiTrack (right y)\n"
            f"Both shifted so t=0 = release moment",
            fontsize=12, fontweight="bold", y=1.01,
        )

        # OptiTrack raw for the diagnostic: trim to the post-release window
        for idx, fam in enumerate(fams_present):
            ax_r  = axes_raw[idx][0]
            d     = best_per_family[fam]
            res   = d["res"]
            color = FAMILY_SOLID.get(fam, "gray")

            # --- OptiTrack raw angle on right y-axis ---
            ax_r2 = ax_r.twinx()
            o_t_sh = o_t_rel - t_zero
            o_mask = (o_t_sh >= -0.5) & (o_t_sh <= PLOT_XLIM - t_zero)
            ax_r2.plot(o_t_sh[o_mask],
                       o_ang[o_rel_idx:][o_mask],
                       color="black", lw=1.4, alpha=0.6,
                       ls="--", label="OptiTrack (quat-mag, right axis)")
            ax_r2.set_ylabel("OptiTrack quat magnitude (deg)", fontsize=8, color="gray")
            ax_r2.tick_params(axis="y", labelsize=7, colors="gray")

            # --- Model raw angle on left y-axis ---
            m_t_sh = res["m_t_plot"] - t_zero
            m_start = res["m_t_rel"][0]
            # Full model time from release
            m_mask = (m_t_sh >= -0.5) & (m_t_sh <= PLOT_XLIM - t_zero)
            m_raw_trim = m_ang[0]  # placeholder — need to use the right CSV
            # Re-load the best model CSV for the raw plot
            # (the CSV path is stored in the variant list)
            best_csv = None
            for cmp, thr, path in variants.get((trial_num, fam), []):
                lbl = f"{fam} {cmp}  thr={thr}"
                if lbl == d["label"]:
                    best_csv = path; break
            if best_csv is None and variants.get((trial_num, fam)):
                best_csv = variants[(trial_num, fam)][0][2]

            if best_csv:
                try:
                    _mt, _mang = load_variant_csv(best_csv)
                    _rel_idx, _rel_t, _rel_ang = detect_model_release(_mang, _mt)
                    _mt_rel   = _mt[_rel_idx:] - _mt[_rel_idx]
                    _mt_sh    = _mt_rel + res["time_shift"] - t_zero
                    _mang_raw = _mang[_rel_idx:]
                    _mm       = (_mt_sh >= -0.5) & (_mt_sh <= PLOT_XLIM - t_zero)
                    ax_r.plot(_mt_sh[_mm], _mang_raw[_mm],
                              color=color, lw=1.8, label=f"{d['label']} raw (left axis)")
                except Exception:
                    pass

            ax_r.set_ylabel("Model knee_angle_deg (deg)", fontsize=8, color=color)
            ax_r.tick_params(axis="y", labelsize=7, labelcolor=color)
            ax_r.set_xlabel("Time since release (s)", fontsize=8)
            ax_r.set_title(f"{fam.upper()} — best variant: {d['label']}", fontsize=9)
            ax_r.set_xlim(-0.5, PLOT_XLIM - t_zero)

            # Add a note explaining the two conventions
            ax_r.text(0.02, 0.03,
                      "Model: 179 deg = extended, 82 deg = max flex  |  "
                      "OptiTrack: quat mag (higher = more relative rotation; "
                      "direction may vary by marker placement)",
                      transform=ax_r.transAxes, fontsize=6.5, color="gray",
                      va="bottom")

            lines1, labels1 = ax_r.get_legend_handles_labels()
            lines2, labels2 = ax_r2.get_legend_handles_labels()
            ax_r.legend(lines1 + lines2, labels1 + labels2,
                        loc="upper right", fontsize=7, framealpha=0.85)
            ax_r.grid(True, ls=":", alpha=0.35)

        fig_raw.tight_layout()
        raw_name = f"Raw_P1_Pos2_Trial{trial_num}_RawAngles.png"
        fig_raw.savefig(os.path.join(OUTPUT_DIR, raw_name),
                        dpi=150, bbox_inches="tight")
        plt.close(fig_raw); total_saved += 1
        print(f"  Raw angles: {raw_name}")

    print()

# ---------------------------------------------------------------------------
# ACCURACY RANKING
# ---------------------------------------------------------------------------
print("=" * 65)
print("ACCURACY RANKING — best variant per family (lowest RMSE)")
print("=" * 65)

if all_records:
    df_all = pd.DataFrame(all_records)
    ranking = (df_all.groupby("variant")
               .apply(lambda g: pd.Series({
                   "family":     g["family"].iloc[0],
                   "complexity": g["complexity"].iloc[0],
                   "threshold":  g["threshold"].iloc[0],
                   "n_extrema":  len(g),
                   "rmse_deg":   round(float(np.sqrt(np.mean(g["abs_err"]**2))), 2),
                   "mae_deg":    round(float(g["abs_err"].mean()), 2),
               }))
               .sort_values("rmse_deg")
               .reset_index())
    ranking.index += 1
    print(ranking[["variant", "n_extrema", "rmse_deg", "mae_deg"]].to_string())
    print()

    out_csv = os.path.join(OUTPUT_DIR, "accuracy_ranking_p1_raw.csv")
    ranking.to_csv(out_csv)
    print(f"Saved: {out_csv}")

    out_csv2 = os.path.join(OUTPUT_DIR, "all_errors_p1_raw.csv")
    df_all.to_csv(out_csv2, index=False)
    print(f"Saved: {out_csv2}")

print(f"\nDone. {total_saved} figures -> {OUTPUT_DIR}")
