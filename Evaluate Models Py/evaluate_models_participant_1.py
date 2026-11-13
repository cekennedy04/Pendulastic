import os
import json
import glob
import re
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import find_peaks

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
BASE_DIR        = r"C:\Users\cladi\Pendulastic"
OUTPUT_DIR      = os.path.join(BASE_DIR, "pendulastic model analysis")
OPTITRACK_ROOT  = os.path.join(BASE_DIR, "OptiTrack_Recordings", "Participant_0")
EVALUATION_JSON = r"C:\Users\cladi\OneDrive\Desktop\Participant_0\Results\evaluation_results.json"
MODEL_FPS       = 30
DROP_AMPLITUDE  = 5.0    # deg above baseline that marks the actual drop
BASELINE_SEC    = 3.0    # pre-drop hold used to compute resting baseline
PEAK_PROMINENCE = 3.0    # minimum prominence (deg) to accept a swing peak
PLOT_XLIM       = 15.0   # x-axis limit (seconds)
os.makedirs(OUTPUT_DIR, exist_ok=True)

MODEL_COLORS = {
    "mediapipe": "#1f77b4",
    "rtmpose":   "#ff7f0e",
    "mmpose":    "#2ca02c",
    "fremocap":  "#9467bd",
    "posepipe":  "#8c564b",
    "openpose":  "#e377c2",
}
MODEL_MARKERS = {
    "mediapipe": "^",
    "rtmpose":   "s",
    "mmpose":    "D",
    "fremocap":  "P",
    "posepipe":  "X",
    "openpose":  "*",
}


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def align_and_normalize(raw_angles, release_idx):
    """Trim at anchor, subtract baseline, abs(), convert rad→deg if needed."""
    trimmed    = np.array(raw_angles[release_idx:], dtype=float)
    baseline   = np.nanmean(raw_angles[:max(1, release_idx)])
    normalised = np.abs(trimmed - baseline)
    if len(normalised) and np.nanmax(normalised) < 6.2832:
        normalised = np.rad2deg(normalised)
    return normalised


def load_optitrack(csv_path):
    """Skip Motive metadata, pick angle column by variance. Returns (t, ang, fs)."""
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
    """First frame after hold where OptiTrack moves > DROP_AMPLITUDE from baseline."""
    n_base   = int(BASELINE_SEC * opti_fs)
    baseline = np.nanmean(o_ang[:n_base])
    search   = np.abs(o_ang[n_base:] - baseline)
    for thr in [DROP_AMPLITUDE, DROP_AMPLITUDE / 2, 2.0, 1.0]:
        hits = np.where(search > thr)[0]
        if len(hits):
            return int(n_base + hits[0]), float(o_t[n_base + hits[0]])
    print("  [warn] No drop found; using index 0.")
    return 0, float(o_t[0])


def find_all_peaks(signal, fs):
    """
    All major swing peaks in an event-aligned signal.

    Minimum height = max(15 deg, 25% of signal max).
    This rejects sub-threshold noise spikes in the dead-zone region
    (MediaPipe/FreMoCap noise < 10 deg) while accepting every real
    flexion swing (typically >= 30 deg).
    """
    guard    = max(1, int(0.1 * fs))
    min_dist = max(5, int(0.3 * fs))
    min_h    = max(15.0, float(np.nanmax(signal)) * 0.25)
    peaks, _ = find_peaks(signal[guard:], distance=min_dist,
                          prominence=PEAK_PROMINENCE, height=min_h)
    return peaks + guard   # global indices into `signal`


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------
with open(EVALUATION_JSON, "r", encoding="utf-8-sig") as f:
    data = json.load(f)

all_records = []

for trial_data in data.get("trials", []):
    if "0" not in str(trial_data.get("participant_id", "")):
        continue
    raw_pos   = str(trial_data.get("position", ""))
    clean_pos = re.sub(r"[^0-9]", "", raw_pos)
    if clean_pos == "3" or clean_pos not in ("1", "2"):
        continue
    t_num = str(trial_data.get("trial", ""))

    csv_hits = glob.glob(
        os.path.join(OPTITRACK_ROOT, f"Position_{clean_pos}", "**",
                     f"*{t_num}_optitrack.csv"),
        recursive=True,
    )
    if not csv_hits:
        print(f"  [warn] No OptiTrack CSV — Pos={clean_pos} Trial={t_num}")
        continue

    try:
        o_t, o_ang, opti_fs = load_optitrack(csv_hits[0])
    except Exception as exc:
        print(f"  [error] {exc}")
        continue

    # OptiTrack locked anchor + event-aligned signal
    o_rel_idx, release_time_s = detect_master_anchor(o_t, o_ang, opti_fs)
    o_final   = align_and_normalize(o_ang, o_rel_idx)
    o_t_event = np.arange(len(o_final)) / opti_fs
    o_peaks   = find_all_peaks(o_final, opti_fs)

    o_peak1_t = float(o_t_event[o_peaks[0]]) if len(o_peaks) else 0.0

    print(f"[trial] Pos={clean_pos} Trial={t_num} | "
          f"drop t={release_time_s:.2f}s | "
          f"{len(o_peaks)} OptiTrack peaks | "
          f"1st peak at t={o_peak1_t:.2f}s")

    # ------------------------------------------------------------------
    # Clean 2×2 model grid  (one subplot per model, each vs OptiTrack)
    # ------------------------------------------------------------------
    # Collect processed model data first, then draw
    model_data_for_plot = {}   # m_name → dict with all computed arrays

    # Also need the waveform-overlay figure (kept for archive)
    fig_ov, ax_ov = plt.subplots(figsize=(14, 5))
    ax_ov.plot(o_t_event, o_final,
               color="black", linewidth=2.5, zorder=5,
               label="OptiTrack (Gold Standard)")
    for i, pk in enumerate(o_peaks):
        ax_ov.scatter(o_t_event[pk], o_final[pk],
                      color="black", s=80, zorder=8,
                      edgecolors="white", linewidths=1.2)
        ax_ov.text(o_t_event[pk], o_final[pk] + 1.5, str(i + 1),
                   ha="center", va="bottom", fontsize=8,
                   color="black", fontweight="bold")

    model_names      = []
    model_peak_vals  = {}   # m_name → list of peak values (by swing index)
    model_sq_errors  = {}   # m_name → list of err² per swing (for RMSE)
    model_abs_errors = {}   # m_name → list of |error_deg| per swing (for error panel)
    model_line_hdls  = {}   # m_name → Line2D handle (for rebuilding legend with RMSE)

    for m_name, m_payload in trial_data.get("models", {}).items():
        raw = m_payload.get("trajectories", {}).get("knee_angle")
        if raw is None or len(raw) == 0:
            continue

        # Use effective_fps from payload if present (e.g. frame-stepped OpenPose)
        m_fps     = float(m_payload.get("effective_fps", MODEL_FPS))

        m_ang     = np.array(raw, dtype=float)
        m_rel_idx = min(int(release_time_s * m_fps), len(m_ang) - 1)
        m_final   = align_and_normalize(m_ang, m_rel_idx)
        m_t_event = np.arange(len(m_final)) / m_fps

        # --- Peak detection on the model signal ---
        m_peaks = find_all_peaks(m_final, m_fps)

        # --- First-peak alignment: shift model time so swing 1 overlaps ---
        if len(o_peaks) and len(m_peaks):
            m_peak1_t  = float(m_t_event[m_peaks[0]])
            time_shift = o_peak1_t - m_peak1_t
        else:
            time_shift = 0.0

        m_t_plot = m_t_event + time_shift   # display time axis only

        color  = MODEL_COLORS.get(m_name.lower(), "gray")
        marker = MODEL_MARKERS.get(m_name.lower(), "o")

        # Plot onto overlay figure
        line, = ax_ov.plot(m_t_plot, m_final,
                           color=color, linewidth=1.6, alpha=0.75)
        model_line_hdls[m_name] = line

        # --- Amplitude error: match by swing index ---
        model_names.append(m_name)
        peak_vals  = []
        sq_errors  = []
        abs_errors = []
        o_peak_times = []
        o_peak_vals  = []

        for i, o_pk in enumerate(o_peaks):
            o_t_pk = float(o_t_event[o_pk])
            o_val  = float(o_final[o_pk])
            if i < len(m_peaks):
                m_pk  = int(m_peaks[i])
                m_val = float(m_final[m_pk])
                err   = m_val - o_val
                peak_vals.append(round(m_val, 2))
                sq_errors.append(err ** 2)
                abs_errors.append(round(abs(err), 2))

                # Marker on overlay figure
                ax_ov.scatter(m_t_event[m_pk] + time_shift, m_val,
                              color=color, marker=marker,
                              s=70, zorder=7,
                              edgecolors="black", linewidths=0.6)

                all_records.append({
                    "position":    clean_pos,
                    "trial":       t_num,
                    "model":       m_name,
                    "swing":       i + 1,
                    "opti_deg":    round(o_val, 2),
                    "model_deg":   round(m_val, 2),
                    "error_deg":   round(err, 2),
                    "abs_err_deg": round(abs(err), 2),
                })
            else:
                peak_vals.append(None)
                sq_errors.append(None)
                abs_errors.append(None)

            o_peak_times.append(o_t_pk)
            o_peak_vals.append(o_val)

        model_peak_vals[m_name]  = peak_vals
        model_sq_errors[m_name]  = sq_errors
        model_abs_errors[m_name] = abs_errors

        # Store everything needed for the grid plot
        model_data_for_plot[m_name] = {
            "m_final":      m_final,
            "m_t_plot":     m_t_plot,
            "m_peaks":      m_peaks,
            "m_t_event":    m_t_event,
            "time_shift":   time_shift,
            "color":        color,
            "marker":       marker,
            "o_peak_times": o_peak_times,
            "o_peak_vals":  o_peak_vals,
            "peak_vals":    peak_vals,
            "abs_errors":   abs_errors,
        }
        print(f"  {m_name:<14} shift={time_shift:+.2f}s | "
              f"peaks: {[round(v,1) if v else 'N/A' for v in peak_vals]}")

    # --- Per-model RMSE (across matched swings in this trial) ---
    model_rmse = {}
    for mn in model_names:
        sq = [v for v in model_sq_errors.get(mn, []) if v is not None]
        model_rmse[mn] = float(np.sqrt(np.mean(sq))) if sq else float("nan")
        rmse_str = f"{model_rmse[mn]:.1f}" if not np.isnan(model_rmse[mn]) else "N/A"
        print(f"  {mn:<14} RMSE={rmse_str} deg  ({len(sq)} swings)")

    # ── Finish overlay figure (archive copy) ──────────────────────────────
    ax_ov.axvline(x=0, color="crimson", linestyle=":", linewidth=1.0, alpha=0.7)
    leg_handles = [ax_ov.lines[0]]
    leg_labels  = ["OptiTrack (Gold Standard)"]
    for mn in model_names:
        rmse = model_rmse.get(mn, float("nan"))
        rmse_str = f"{rmse:.1f} deg" if not np.isnan(rmse) else "N/A"
        leg_handles.append(model_line_hdls[mn])
        leg_labels.append(f"{mn}  RMSE={rmse_str}")
    ax_ov.legend(leg_handles, leg_labels,
                 loc="upper right", fontsize=8, framealpha=0.93)
    ax_ov.set_xlim(0, PLOT_XLIM)
    ax_ov.set_ylim(bottom=0)
    ax_ov.set_title(f"Position {clean_pos}  |  Trial {t_num}  |  All Models",
                    fontsize=12, fontweight="bold")
    ax_ov.set_xlabel("Time since drop (s)", fontsize=10)
    ax_ov.set_ylabel("Knee Flexion (deg)", fontsize=10)
    ax_ov.grid(True, linestyle=":", alpha=0.4)
    fig_ov.tight_layout()
    fig_ov.savefig(os.path.join(OUTPUT_DIR,
                                f"Pos{clean_pos}_Trial{t_num}_Overlay.png"),
                   dpi=150, bbox_inches="tight")
    plt.close(fig_ov)

    # ── Clean 2×2 grid: one panel per model ───────────────────────────────
    n_models = len(model_names)
    ncols    = 2
    nrows    = max(1, (n_models + 1) // 2)
    fig_g, axes_g = plt.subplots(nrows, ncols,
                                 figsize=(15, 5 * nrows),
                                 squeeze=False)
    fig_g.suptitle(
        f"Position {clean_pos}  |  Trial {t_num}  —  "
        f"Model vs OptiTrack  (peak-aligned)",
        fontsize=15, fontweight="bold", y=1.01)

    swing_nums = list(range(1, len(o_peaks) + 1))

    for mi, m_name in enumerate(model_names):
        row, col = divmod(mi, ncols)
        ax_g     = axes_g[row][col]
        d        = model_data_for_plot[m_name]
        color    = d["color"]
        marker   = d["marker"]
        rmse     = model_rmse.get(m_name, float("nan"))
        rmse_str = f"{rmse:.1f}" if not np.isnan(rmse) else "N/A"

        # ── Waveforms ──
        ax_g.plot(o_t_event, o_final,
                  color="black", linewidth=2.2, zorder=5,
                  label="OptiTrack")
        ax_g.plot(d["m_t_plot"], d["m_final"],
                  color=color, linewidth=2.0, alpha=0.8, zorder=4,
                  label=m_name)

        # ── OptiTrack swing peaks ──
        for i, (o_pk, o_t_pk, o_val) in enumerate(
                zip(o_peaks, d["o_peak_times"], d["o_peak_vals"])):
            ax_g.scatter(o_t_pk, o_val,
                         color="black", s=90, zorder=8,
                         edgecolors="white", linewidths=1.4)
            ax_g.text(o_t_pk, o_val + 2.0, str(i + 1),
                      ha="center", va="bottom", fontsize=9,
                      color="black", fontweight="bold")

        # ── Model peaks + per-swing error annotation ──
        m_pk_vals  = d["peak_vals"]
        abs_errs   = d["abs_errors"]
        m_peaks_i  = d["m_peaks"]
        m_t_event  = d["m_t_event"]
        time_shift = d["time_shift"]

        for i, (o_t_pk, o_val, m_val, abs_err) in enumerate(
                zip(d["o_peak_times"], d["o_peak_vals"], m_pk_vals, abs_errs)):
            if m_val is None or abs_err is None:
                continue
            m_t_pk = m_t_event[int(m_peaks_i[i])] + time_shift

            # Model peak marker
            ax_g.scatter(m_t_pk, m_val,
                         color=color, marker=marker,
                         s=90, zorder=7, edgecolors="black", linewidths=0.8)

            # Vertical connector: model peak → OptiTrack peak at same swing
            ax_g.plot([o_t_pk, o_t_pk], [o_val, m_val],
                      color="gray", linewidth=1.2,
                      linestyle="--", alpha=0.6, zorder=3)

            # Error label at midpoint of connector
            mid_y = (o_val + m_val) / 2
            ax_g.text(o_t_pk + 0.12, mid_y,
                      f"{abs_err:.1f}",
                      ha="left", va="center",
                      fontsize=8, color="dimgray")

        # ── RMSE box ──
        ax_g.text(0.97, 0.97,
                  f"RMSE = {rmse_str} deg",
                  transform=ax_g.transAxes,
                  ha="right", va="top",
                  fontsize=13, fontweight="bold",
                  color=color,
                  bbox=dict(boxstyle="round,pad=0.4",
                            facecolor="white",
                            edgecolor=color, linewidth=2.0,
                            alpha=0.92))

        ax_g.set_xlim(0, PLOT_XLIM)
        ax_g.set_ylim(bottom=0)
        ax_g.set_title(m_name, fontsize=13, fontweight="bold", color=color)
        ax_g.set_xlabel("Time since drop (s)", fontsize=10)
        ax_g.set_ylabel("Knee Flexion (deg)", fontsize=10)
        ax_g.legend(loc="upper left", fontsize=9, framealpha=0.9)
        ax_g.grid(True, linestyle=":", alpha=0.35)
        ax_g.tick_params(labelsize=9)

    # Hide unused panels
    for mi in range(n_models, nrows * ncols):
        row, col = divmod(mi, ncols)
        axes_g[row][col].set_visible(False)

    fig_g.tight_layout()
    fig_g.savefig(os.path.join(OUTPUT_DIR,
                               f"Pos{clean_pos}_Trial{t_num}_ModelGrid.png"),
                  dpi=150, bbox_inches="tight")
    plt.close(fig_g)
    print(f"  Saved: Pos{clean_pos}_Trial{t_num}_ModelGrid.png")

    # ------------------------------------------------------------------
    # Grouped bar chart — peak flexion by swing number
    # ------------------------------------------------------------------
    n_swings = len(o_peaks)
    if n_swings > 0 and model_names:
        series = [("OptiTrack", "black",
                   [round(float(o_final[pk]), 2) for pk in o_peaks])]
        for m_name in model_names:
            raw_vals = model_peak_vals.get(m_name, [])
            padded   = [(v if v is not None else 0.0)
                        for v in (raw_vals + [0.0] * n_swings)[:n_swings]]
            series.append((m_name,
                           MODEL_COLORS.get(m_name.lower(), "gray"),
                           padded))

        n_ser = len(series)
        bar_w = 0.8 / n_ser
        x     = np.arange(n_swings)

        fig2, ax2 = plt.subplots(figsize=(max(10, n_swings + 4), 6))

        for si, (label, color, vals) in enumerate(series):
            offset = (si - n_ser / 2 + 0.5) * bar_w
            bars   = ax2.bar(x + offset, vals, bar_w * 0.92,
                             color=color,
                             linewidth=0.6,
                             alpha=0.85 if color != "black" else 1.0,
                             label=label, zorder=3)
            # Extract OptiTrack baseline for error annotation
            o_vals = series[0][2] if si > 0 else None
            for bi, (bar, val) in enumerate(zip(bars, vals)):
                if val > 0:
                    ax2.text(bar.get_x() + bar.get_width() / 2,
                             bar.get_height() + 0.4,
                             f"{val:.1f}",
                             ha="center", va="bottom",
                             fontsize=6.5, rotation=90)
                    # Show error relative to OptiTrack for model bars
                    if o_vals is not None and bi < len(o_vals) and o_vals[bi] > 0:
                        err_v = val - o_vals[bi]
                        ax2.text(bar.get_x() + bar.get_width() / 2,
                                 0.5,
                                 f"Δ{err_v:+.0f}",
                                 ha="center", va="bottom",
                                 fontsize=5.0, color="dimgray", rotation=90)

        ax2.set_xticks(x)
        ax2.set_xticklabels([f"Swing {i+1}" for i in range(n_swings)],
                            fontsize=9)
        ax2.set_ylim(bottom=0)
        ax2.set_xlabel("Oscillation Swing", fontsize=11)
        ax2.set_ylabel("Peak Knee Flexion (deg)", fontsize=11)
        ax2.set_title(
            f"Position {clean_pos}  |  Trial {t_num}  |  "
            f"Peak Flexion by Swing — Model vs OptiTrack",
            fontsize=12, fontweight="bold")
        ax2.legend(loc="upper right", fontsize=9, framealpha=0.92)
        ax2.grid(True, axis="y", linestyle=":", alpha=0.45, zorder=0)
        plt.tight_layout()
        fig2.savefig(os.path.join(OUTPUT_DIR,
                                  f"Pos{clean_pos}_Trial{t_num}_SwingBars.png"),
                     dpi=150)
        plt.close(fig2)
        print(f"  Saved: Pos{clean_pos}_Trial{t_num}_SwingBars.png")

    # Per-trial console table
    print(f"\n  Swing table  (Pos={clean_pos} Trial={t_num}):")
    hdr = f"  {'Swing':>5}  {'OptiTrack':>10}" + \
          "".join(f"  {n:>12}" for n in model_names)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for i, o_pk in enumerate(o_peaks):
        row = f"  {i+1:>5}  {o_final[o_pk]:>10.2f}"
        for mn in model_names:
            v = (model_peak_vals[mn][i]
                 if i < len(model_peak_vals.get(mn, [])) else None)
            row += f"  {str(v) if v is not None else 'N/A':>12}"
        print(row)
    print()

# ---------------------------------------------------------------------------
# GLOBAL SUMMARY CSV
# ---------------------------------------------------------------------------
from scipy import stats as scipy_stats

df = pd.DataFrame(all_records)
if df.empty:
    print("[warn] No records generated.")
    raise SystemExit

def safe_csv(dataframe, path):
    """Save CSV; if the file is locked (Excel open), append a counter."""
    for suffix in ["", "_1", "_2", "_3"]:
        p = path.replace(".csv", f"{suffix}.csv")
        try:
            dataframe.to_csv(p, index=False)
            print(f"  Exported: {p}")
            return
        except PermissionError:
            continue
    print(f"  [warn] Could not write {path} — close it in Excel and re-run.")

safe_csv(df, os.path.join(OUTPUT_DIR, "all_peaks_error_summary.csv"))
wide = df.pivot_table(
    index=["position", "trial", "swing", "opti_deg"],
    columns="model", values="model_deg",
).reset_index()
safe_csv(wide, os.path.join(OUTPUT_DIR, "all_peaks_wide.csv"))

# ---------------------------------------------------------------------------
# LINEAR CALIBRATION  (model = a * opti + b  →  corrected = (model - b) / a)
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print("LINEAR CALIBRATION + ACCURACY RANKING")
print("=" * 70)

calib = {}
for m_name in sorted(df["model"].unique()):
    sub = df[df["model"] == m_name].dropna(subset=["opti_deg", "model_deg"])
    if len(sub) < 3:
        continue
    x = sub["opti_deg"].values
    y = sub["model_deg"].values
    slope, intercept, r, _, _ = scipy_stats.linregress(x, y)
    corrected  = (y - intercept) / slope
    rmse_raw   = float(np.sqrt(np.mean((y - x) ** 2)))
    rmse_cal   = float(np.sqrt(np.mean((corrected - x) ** 2)))
    mae_cal    = float(np.mean(np.abs(corrected - x)))
    calib[m_name] = dict(slope=slope, intercept=intercept,
                         r=r, r2=r ** 2,
                         rmse_raw=rmse_raw, rmse_cal=rmse_cal, mae_cal=mae_cal)

# Add corrected column to the main dataframe
def _apply_calib(row):
    c = calib.get(row["model"])
    if c is None or pd.isna(row["model_deg"]):
        return np.nan
    return (row["model_deg"] - c["intercept"]) / c["slope"]

df["corrected_deg"] = df.apply(_apply_calib, axis=1)
df["error_cal_deg"] = df["corrected_deg"] - df["opti_deg"]
safe_csv(df, os.path.join(OUTPUT_DIR, "all_peaks_calibrated.csv"))

# ---------------------------------------------------------------------------
# CALIBRATION SCATTER PLOTS
# ---------------------------------------------------------------------------
model_list = sorted(calib.keys())
n_models   = len(model_list)
ncols      = 2
nrows      = (n_models + ncols - 1) // ncols

fig_s, axes_s = plt.subplots(nrows, ncols,
                              figsize=(12, 5 * nrows), squeeze=False)
axes_s = axes_s.flatten()

for mi, m_name in enumerate(model_list):
    ax  = axes_s[mi]
    sub = df[df["model"] == m_name].dropna(subset=["opti_deg", "model_deg"])
    x, y = sub["opti_deg"].values, sub["model_deg"].values
    c    = calib[m_name]
    col  = MODEL_COLORS.get(m_name.lower(), "gray")

    # Colour points by trial for context
    for (pos, trial), grp in sub.groupby(["position", "trial"]):
        ax.scatter(grp["opti_deg"], grp["model_deg"],
                   s=55, alpha=0.8, zorder=3,
                   label=f"Pos{pos} T{trial}")

    xl = np.array([x.min() * 0.92, x.max() * 1.08])
    ax.plot(xl, c["slope"] * xl + c["intercept"],
            color="red", linewidth=2,
            label=f"Fit: {c['slope']:.2f}x + {c['intercept']:.1f}  "
                  f"R²={c['r2']:.3f}")
    ax.plot(xl, xl, "k--", linewidth=1.2, alpha=0.45, label="Ideal (y = x)")

    ax.set_title(
        f"{m_name}  |  Gain={c['slope']:.2f}  Offset={c['intercept']:.1f} deg\n"
        f"RMSE raw={c['rmse_raw']:.1f} deg   RMSE calibrated={c['rmse_cal']:.1f} deg",
        fontsize=10, fontweight="bold")
    ax.set_xlabel("OptiTrack Peak Flexion (deg)", fontsize=9)
    ax.set_ylabel("Model Peak Flexion (deg)", fontsize=9)
    ax.legend(fontsize=7.5, framealpha=0.85)
    ax.grid(True, linestyle=":", alpha=0.4)

for ax in axes_s[n_models:]:
    ax.set_visible(False)

fig_s.suptitle(
    "Calibration Fit: Model vs OptiTrack Peak Flexion (all trials)",
    fontsize=13, fontweight="bold")
plt.tight_layout()
fig_s.savefig(os.path.join(OUTPUT_DIR, "calibration_scatter.png"), dpi=150)
plt.close(fig_s)
print("  Saved: calibration_scatter.png")

# ---------------------------------------------------------------------------
# CORRECTED BAR CHARTS (per trial)
# ---------------------------------------------------------------------------
for (pos, trial), grp in df.groupby(["position", "trial"]):
    # OptiTrack values (same for every model row at same swing)
    opti_row = (grp.drop_duplicates("swing")
                   .sort_values("swing")[["swing", "opti_deg"]])
    n_sw   = len(opti_row)
    swings = opti_row["swing"].tolist()

    series = [("OptiTrack", "black", opti_row["opti_deg"].tolist())]
    for m_name in sorted(grp["model"].unique()):
        msub = grp[grp["model"] == m_name].sort_values("swing")
        vals = []
        for sw in swings:
            row = msub[msub["swing"] == sw]
            vals.append(float(row["corrected_deg"].values[0])
                        if len(row) and not pd.isna(row["corrected_deg"].values[0])
                        else 0.0)
        series.append((f"{m_name} (cal)", MODEL_COLORS.get(m_name.lower(), "gray"), vals))

    n_ser = len(series)
    bar_w = 0.8 / n_ser
    x     = np.arange(n_sw)

    fig_b, ax_b = plt.subplots(figsize=(max(10, n_sw + 3), 6))
    for si, (label, color, vals) in enumerate(series):
        offset = (si - n_ser / 2 + 0.5) * bar_w
        bars   = ax_b.bar(x + offset, vals, bar_w * 0.92,
                          color=color, alpha=0.85 if color != "black" else 1.0,
                          label=label, zorder=3)
        for bar, val in zip(bars, vals):
            if val > 0:
                ax_b.text(bar.get_x() + bar.get_width() / 2,
                          bar.get_height() + 0.4, f"{val:.1f}",
                          ha="center", va="bottom", fontsize=6.5, rotation=90)

    ax_b.set_xticks(x)
    ax_b.set_xticklabels([f"Swing {s}" for s in swings], fontsize=9)
    ax_b.set_ylim(bottom=0)
    ax_b.set_xlabel("Oscillation Swing", fontsize=11)
    ax_b.set_ylabel("Peak Knee Flexion (deg)", fontsize=11)
    ax_b.set_title(
        f"Position {pos}  |  Trial {trial}  |  "
        f"Calibrated Model vs OptiTrack",
        fontsize=12, fontweight="bold")
    ax_b.legend(loc="upper right", fontsize=8, framealpha=0.92)
    ax_b.grid(True, axis="y", linestyle=":", alpha=0.45, zorder=0)
    plt.tight_layout()
    fig_b.savefig(os.path.join(OUTPUT_DIR,
                               f"Pos{pos}_Trial{trial}_CalibratedBars.png"),
                  dpi=150)
    plt.close(fig_b)
    print(f"  Saved: Pos{pos}_Trial{trial}_CalibratedBars.png")

# ---------------------------------------------------------------------------
# ACCURACY RANKING TABLE
# ---------------------------------------------------------------------------
ranking = pd.DataFrame([
    {
        "model":          m,
        "gain_a":         round(c["slope"], 3),
        "offset_b_deg":   round(c["intercept"], 2),
        "r":              round(c["r"], 4),
        "r2":             round(c["r2"], 4),
        "rmse_raw_deg":   round(c["rmse_raw"], 2),
        "rmse_cal_deg":   round(c["rmse_cal"], 2),
        "mae_cal_deg":    round(c["mae_cal"], 2),
    }
    for m, c in calib.items()
]).sort_values("rmse_cal_deg").reset_index(drop=True)
ranking.index += 1   # rank starts at 1

print("\n" + "=" * 70)
print("ACCURACY RANKING  (sorted by calibrated RMSE — lower = better)")
print("=" * 70)
print(ranking.to_string())
print()
print("Columns:")
print("  gain_a      : slope of model=a*opti+b  (1.0 = perfect scale)")
print("  offset_b_deg: intercept in degrees     (0.0 = no bias)")
print("  r / r2      : Pearson correlation / R-squared  (1.0 = perfect shape)")
print("  rmse_raw    : RMSE before calibration (deg)")
print("  rmse_cal    : RMSE after calibration  (deg)  <-- primary rank metric")
print("  mae_cal     : Mean Absolute Error after calibration (deg)")

safe_csv(ranking, os.path.join(OUTPUT_DIR, "accuracy_ranking.csv"))
print("\nDone.")
