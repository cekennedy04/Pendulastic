"""
pt_report_common.py
====================
Shared plotting/scoring logic for the per-participant "full report" figures
(p13_full_report.py, p5_full_report.py) and the p13_vs_p5_comparison.py
figure. Factored out so all three stay visually and numerically consistent
-- same 7-parameter Popovic PT score, same release-alignment method, same
representative-trial selection, same styling.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

import matplotlib
# pendulastic_app.py sets the TkAgg backend for its own embedded, live
# figures before ever importing this module; forcing Agg here would yank
# that out from under it. tkinter being already imported is a reliable
# proxy for "a GUI app is running" -- checking for a module literally named
# "pendulastic_app" doesn't work when it's launched directly, since Python
# then registers it in sys.modules as "__main__" instead. Only force Agg
# for standalone/headless use (the p*_full_report.py / p13_vs_p5_comparison.py
# scripts, none of which import tkinter).
if "tkinter" not in sys.modules:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import pendulastic_pt_score as pt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "PT_Scores")
os.makedirs(OUT_DIR, exist_ok=True)

OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
ARCHIVE_ROOT = (r"C:\Users\cladi\OneDrive\Desktop\Shirley Ryan\Pendulastic_7_28_Archive"
                r"\Optitrack recordings")

plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Helvetica']
plt.rcParams['axes.edgecolor'] = '#cccccc'
plt.rcParams['axes.linewidth'] = 0.8

COLORS = {'red': '#d62728', 'green': '#2ca02c', 'purple': '#9467bd',
          'blue': '#1f77b4', 'orange': '#ff7f0e'}
BG_GRID = '#f2f2f2'

_PARAM_KEYS = pt._PARAM_KEYS  # R2n, N, phi_max_ratio, omega_max_n, omega_min_n, f, area_ratio
PARAM_DISPLAY = ["R2n", "N", "phi_ratio", "w_max_n", "w_min_n", "f (Hz)", "Area"]

# 7-param score zones, using this codebase's own pt_to_mas() bin edges
# collapsed to 3 bands (Healthy=MAS0, Borderline=MAS1/1+, Impaired=MAS2+).
ZONE_EDGES = [0.0, 0.12, 0.44, 3.0]
ZONE_COLORS = ['#d4edda', '#fff3cd', '#f8d7da']
ZONE_LINE_COLORS = ['#28a745', '#ffc107']
ZONE_LABELS = ['Healthy', 'Borderline', 'Impaired']


def score_trial(pid, trial, t, angle):
    """Compute PT params + full 7-param PT score for one trial. Returns None
    if no clean release/oscillation was detected."""
    params = pt.compute_pt_params(t, angle)
    if params is None:
        return None
    rec = dict(params)
    rec["pid"] = pid
    rec["trial"] = trial
    rec["pt7"] = pt.compute_pt_score(params)
    rec["t_raw"] = t
    rec["angle_raw"] = angle
    return rec


def representative(trials):
    """Lowest area_ratio (best marker-tracking quality) among valid trials."""
    if not trials:
        return None
    return min(trials, key=lambda r: r["area_ratio"])


def release_aligned_waveform(rep):
    """Explicit release-index alignment: locate the exact array index of the
    release sample (end of the static hold / start of the dynamic swing) and
    shift the time vector so that index lands at t=0.0s. No interpolation or
    padding -- both arrays are sliced directly from the recorded samples, so
    shorter trials simply end wherever their last finite sample falls.

    Release is detected directly on the raw (smoothed but NOT detrended)
    signal, not via compute_pt_params()'s internal t_r[0]/ang_r[0]. Almost
    every trial holds at an exact 180.0 sentinel value for a few (sometimes
    5+) seconds before marker tracking engages; compute_pt_params detrends
    across the WHOLE trial (sentinel hold + real motion together), which
    injects a spurious slope into that flat sentinel stretch and fires its
    release detector while the leg is still provably at rest -- confirmed by
    checking the raw angle at that reported index: exactly 180.0 in every
    case inspected. Detecting on the raw/smoothed signal (no detrend) finds
    where the leg is actually released."""
    mask = np.isfinite(rep["angle_raw"])
    t_masked = rep["t_raw"][mask]
    a_masked = rep["angle_raw"][mask]
    a_smooth = pt._sg(a_masked, w=15, p=3)
    release_idx = pt._detect_release(t_masked, a_smooth)
    release_idx = max(0, min(release_idx, len(t_masked) - 1))
    t_release = t_masked[release_idx]
    y_off = 180.0 - float(a_smooth[release_idx])
    t_plot = t_masked - t_release
    a_plot = a_masked + y_off
    return t_plot, a_plot


# ══════════════════════════════════════════════════════════════════════════
# Generic multi-participant discovery
#
# Folder conventions seen in this project so far:
#   Participant_{N}_{leg}_{condition}/Position_1/Height_Joint-Level/trial_*.csv
#   Participant_{N}_{leg}_{condition}/Session_{condition}/Position_1/.../trial_*.csv
#   Participant_{N}/{Leg}/{condition}/trial_*.csv           (no Position_N)
# New participants may not match any of these exactly, so parsing is
# heuristic: pull the participant number and leg out with regex, and treat
# whatever folder segments are left (with structural names like Position_N /
# Height_Joint-Level / the leg token itself stripped) as the condition label.
# Good enough for grouping and display -- not used for any score computation.
# ══════════════════════════════════════════════════════════════════════════

def _parse_trial_path(csv_path, root):
    rel = os.path.relpath(csv_path, root).replace("\\", "/")

    pids = sorted(set(m.group(1) for m in re.finditer(r"Participant_(\d+)", rel, re.I)))
    if len(pids) != 1:
        # Zero matches, or more than one distinct participant number in the
        # same path -- the latter happens when archived data has a stray
        # nested folder from a different participant (seen: a whole
        # Participant_0_control/ tree misplaced inside a Participant_5
        # folder). Either way this path can't be attributed unambiguously.
        return None
    pid = pids[0]

    m_leg = re.search(r"(?:^|[_/])(left|right)(?:[_/]|$)", rel, re.I)
    if not m_leg:
        return None
    leg = m_leg.group(1).lower()

    parts = rel.split("/")[:-1]
    cond_parts = []
    for part in parts:
        cleaned = part
        low = cleaned.lower()
        if low.startswith("participant_"):
            cleaned = re.sub(r"^participant_\d+_?", "", cleaned, flags=re.I)
        elif low.startswith("session_"):
            cleaned = cleaned[len("session_"):]
        elif low.startswith("position_") or low.startswith("height_"):
            continue
        cleaned = re.sub(r"(left|right)", "", cleaned, flags=re.I).strip("_")
        if cleaned:
            cond_parts.append(cleaned)
    condition = "_".join(dict.fromkeys(cond_parts)) or "default"

    m_trial = re.search(r"trial[_\s]*(\d+)", os.path.basename(csv_path), re.I)
    trial = m_trial.group(1) if m_trial else "0"
    return {"participant": pid, "leg": leg, "condition": condition,
            "trial": trial, "path": csv_path, "mtime": os.path.getmtime(csv_path)}


EXCLUDED_TRIALS_PATH = os.path.join(BASE_DIR, "excluded_trials.json")


def trial_key(participant, leg, condition, trial):
    """Canonical exclusion/cache key shared with rmse_pipeline_common.py's
    trial keys, so one registry covers both PT-score reporting and the RMSE
    validation pipeline."""
    return f"{participant}_{leg}_{condition}_T{trial}"


def load_excluded_trials():
    """{trial_key: reason} for trials that must be dropped from every
    discovery/report/sweep -- e.g. a trial where the participant actively
    used their own muscles to stop the pendulum swing instead of a passive
    release, which invalidates the Popovic PT-score physics (and, if left
    in, would corrupt the RMSE pipeline's parameter search too, since a
    config could spuriously score well against non-passive motion).
    Missing or malformed file -> {} , never raises, matching this
    codebase's other JSON-registry loaders (imu_calibration_config.py,
    pt_cohort_common.load_registry())."""
    try:
        with open(EXCLUDED_TRIALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def discover_all_trials(include_archive=True):
    """Every trial_*_optitrack.csv under the live repo (and, optionally, the
    known archive) parsed into {participant, leg, condition, trial, path}
    records. Quarantined/invalid data (INVALID_ in the path) is excluded,
    as is any trial listed in excluded_trials.json (non-viable recordings,
    e.g. active muscle intervention during the swing)."""
    excluded = load_excluded_trials()
    records = []
    seen = set()
    roots = [OPTI_ROOT] + ([ARCHIVE_ROOT] if include_archive and os.path.isdir(ARCHIVE_ROOT) else [])
    for root in roots:
        for csv_path in glob.glob(os.path.join(root, "**", "trial_*_optitrack.csv"), recursive=True):
            if "INVALID" in csv_path.upper():
                continue
            real = os.path.realpath(csv_path)
            if real in seen:
                continue
            seen.add(real)
            rec = _parse_trial_path(csv_path, root)
            if rec is None:
                continue
            key = trial_key(rec["participant"], rec["leg"], rec["condition"], rec["trial"])
            if key in excluded:
                continue
            records.append(rec)
    return records


def list_participants(include_archive=True):
    """{participant_id: {"legs": {...}, "n_trials": int, "conditions": [...]}}
    sorted by participant id, for populating a UI picker."""
    records = discover_all_trials(include_archive=include_archive)
    by_pid = {}
    for r in records:
        entry = by_pid.setdefault(r["participant"], {"legs": set(), "conditions": set(), "n_trials": 0})
        entry["legs"].add(r["leg"])
        entry["conditions"].add(r["condition"])
        entry["n_trials"] += 1
    return dict(sorted(by_pid.items(), key=lambda kv: int(kv[0])))


TRIAL_THRESHOLD = 4


def leg_trial_counts(participant_id):
    """Total recorded trials per leg for this participant, summed across
    every condition/session found (pre, post, side, control, etc.) -- not
    per-condition. A participant with 2 pre + 3 post right-leg trials counts
    as 5 right, matching TRIAL_THRESHOLD against the cumulative total.

    Moved here from run_pt_analysis.py (2026-08-06) so pt_cohort_common.py
    can independently recompute the full qualifying-participant set without
    importing back from run_pt_analysis.py -- see
    docs/superpowers/specs/2026-08-06-ms-vs-control-cohort-design.md, §6.1."""
    counts = {"left": 0, "right": 0}
    for r in discover_all_trials():
        if r["participant"] == participant_id and r["leg"] in counts:
            counts[r["leg"]] += 1
    return counts


def collect_participant(participant_id, include_archive=True):
    """Score every trial found for one participant. Returns
    by_leg_tp: {(leg, condition): [trial_records]}, and
    timepoints: [(condition, display_label, color)] ordered by first-seen
    mtime (a chronological proxy that's robust to arbitrary condition names)."""
    records = [r for r in discover_all_trials(include_archive=include_archive)
              if r["participant"] == participant_id]

    first_seen = {}
    for r in records:
        first_seen[r["condition"]] = min(first_seen.get(r["condition"], r["mtime"]), r["mtime"])
    conditions = sorted(first_seen, key=lambda c: first_seen[c])

    palette = [COLORS['red'], COLORS['green'], COLORS['purple'], COLORS['blue'], COLORS['orange']]
    timepoints = [(c, c.replace("_", " ").title(), palette[i % len(palette)])
                 for i, c in enumerate(conditions)]

    by_leg_tp = {}
    for leg in ("left", "right"):
        for cond in conditions:
            trials = []
            for r in records:
                if r["leg"] != leg or r["condition"] != cond:
                    continue
                try:
                    t, angle = pt.load_optitrack(r["path"])
                except Exception:
                    continue
                rec = score_trial(f"{participant_id}_{leg}_{cond}", r["trial"], t, angle)
                if rec is not None:
                    trials.append(rec)
            by_leg_tp[(leg, cond)] = trials
    return by_leg_tp, timepoints


def make_report_figure(participant_label, by_leg_tp, timepoints, out_filename, caveat_text,
                       save=True, return_fig=False):
    """3x2 grid: rows = Waveforms / 7-param bars / PT-score trend,
    columns = Left leg / Right leg. `by_leg_tp` keys are (leg, tp_key) with
    leg in ("left","right") and tp_key matching timepoints' first element."""
    fig, axes = plt.subplots(3, 2, figsize=(15, 13), facecolor='white')

    waveforms = {}
    t_lo, t_hi = 0.0, 0.0
    for leg in ("left", "right"):
        for tp_key, tp_label, color in timepoints:
            rep = representative(by_leg_tp.get((leg, tp_key), []))
            if rep is None:
                continue
            t_plot, a_plot = release_aligned_waveform(rep)
            waveforms[(leg, tp_key)] = (t_plot, a_plot, rep)
            t_lo = min(t_lo, float(t_plot.min()))
            t_hi = max(t_hi, float(t_plot.max()))
    margin = 0.05 * (t_hi - t_lo) if t_hi > t_lo else 1.0
    shared_xlim = (t_lo - margin, t_hi + margin)

    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):

        # ── Row 1: Waveforms ────────────────────────────────────────────────
        ax = axes[0, col_idx]
        ax.set_facecolor('#f8f9fa')
        ax.grid(True, color=BG_GRID, linestyle='-', linewidth=0.8)
        for tp_key, tp_label, color in timepoints:
            entry = waveforms.get((leg, tp_key))
            if entry is None:
                continue
            t_plot, a_plot, rep = entry
            ax.plot(t_plot, a_plot, color=color, linewidth=1.5,
                   label=f'{tp_label} (PT={rep["pt7"]:.2f}, T{rep["trial"]})')
        ax.axvline(0, color='gray', linestyle='--', linewidth=0.8)
        ax.set_xlim(shared_xlim)
        ax.set_title(f'{participant_label} {leg_label} – Waveforms', fontsize=10, fontweight='bold', pad=10)
        ax.set_xlabel('Time from release (s)', fontsize=8)
        ax.set_ylabel('Knee angle (°)', fontsize=8)
        ax.tick_params(labelsize=8)
        ax.legend(loc='best', fontsize=7, framealpha=0.8)

        # ── Row 2: Parameters (7-param bars) ────────────────────────────────
        ax = axes[1, col_idx]
        ax.set_facecolor('#f8f9fa')
        ax.grid(True, color=BG_GRID, linestyle='-', linewidth=0.8, axis='y')
        x = np.arange(len(_PARAM_KEYS))
        width = 0.8 / max(len(timepoints), 1)
        for i, (tp_key, tp_label, color) in enumerate(timepoints):
            trials = by_leg_tp.get((leg, tp_key), [])
            if trials:
                vals = [float(np.mean([r[k] for r in trials])) for k in _PARAM_KEYS]
            else:
                vals = [0.0] * len(_PARAM_KEYS)
            offset = (i - (len(timepoints) - 1) / 2) * width
            ax.bar(x + offset, vals, width, label=tp_label, color=color, alpha=0.85)
        ax.set_title(f'{participant_label} {leg_label} – Parameters (7-param)', fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(x)
        ax.set_xticklabels(PARAM_DISPLAY, fontsize=8)
        ax.set_ylabel('Value (mean across trials, log scale)', fontsize=8)
        ax.set_yscale('log')
        ax.set_ylim(bottom=1e-3)
        ax.tick_params(labelsize=8)
        ax.legend(loc='upper right', fontsize=7, framealpha=0.8)

        # ── Row 3: PT Score trend with threshold zones ──────────────────────
        ax = axes[2, col_idx]
        ax.set_facecolor('white')
        all_vals = [r["pt7"] for tp in timepoints for r in by_leg_tp.get((leg, tp[0]), [])]
        y_max = (max(all_vals) if all_vals else 1.6) * 1.15
        for (lo, hi), zcolor in zip(zip(ZONE_EDGES[:-1], ZONE_EDGES[1:]), ZONE_COLORS):
            ax.axhspan(lo, min(hi, y_max), facecolor=zcolor, alpha=0.4, zorder=0)
        for edge, lcolor in zip(ZONE_EDGES[1:-1], ZONE_LINE_COLORS):
            ax.axhline(edge, color=lcolor, linestyle='--', linewidth=0.8)

        x_pts = list(range(len(timepoints)))
        means = []
        rng = np.random.RandomState(13)
        for xc, (tp_key, tp_label, color) in zip(x_pts, timepoints):
            trials = by_leg_tp.get((leg, tp_key), [])
            vals = [r["pt7"] for r in trials]
            if vals:
                m = float(np.mean(vals))
                means.append(m)
                ax.errorbar(xc, m, yerr=float(np.std(vals)) if len(vals) > 1 else 0.02,
                           fmt='o', color=color, capsize=4, elinewidth=1.5, zorder=4)
                for v in vals:
                    ax.scatter(xc + rng.uniform(-0.05, 0.05), v, color=color,
                             s=20, alpha=0.7, zorder=3)
            else:
                means.append(np.nan)
        valid_pts = [(xc, m) for xc, m in zip(x_pts, means) if np.isfinite(m)]
        if len(valid_pts) > 1:
            ax.plot([p[0] for p in valid_pts], [p[1] for p in valid_pts],
                   color='black', linewidth=1, zorder=3)

        zone_x = len(timepoints) - 0.75
        for i, (lab, color) in enumerate(zip(ZONE_LABELS, ['#28a745', '#d39e00', '#dc3545'])):
            lo, hi = ZONE_EDGES[i], min(ZONE_EDGES[i + 1], y_max)
            ax.text(zone_x, (lo + hi) / 2, lab, color=color, fontsize=7, fontweight='bold')

        ax.set_title(f'{participant_label} {leg_label} – PT Score (7-param)', fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(x_pts)
        ax.set_xticklabels([tp[1] for tp in timepoints], fontsize=8)
        ax.set_ylabel('PT Score (7-parameter)', fontsize=8)
        ax.tick_params(labelsize=8)
        ax.set_xlim(-0.5, len(timepoints) - 0.4)
        ax.set_ylim(0, y_max)

    fig.suptitle(f"{participant_label} — Full Report (7-parameter Popovic PT score)\n{caveat_text}",
                fontsize=10, y=0.998, color='#333333')
    plt.tight_layout(rect=[0, 0, 1, 0.965])
    out_path = None
    if save:
        out_path = os.path.join(OUT_DIR, out_filename)
        fig.savefig(out_path, dpi=150, facecolor='white')
        print(f"-> {out_path}")
    if return_fig:
        return out_path, fig
    plt.close(fig)
    return out_path


# ══════════════════════════════════════════════════════════════════════════
# Two-participant comparison (generalized from the P13-vs-P5 figure)
# ══════════════════════════════════════════════════════════════════════════

_CMP_COLOR_A = '#1f77b4'   # blue
_CMP_COLOR_B = '#ff7f0e'   # orange
_STAGE_FALLBACK_LABELS = ["Baseline", "Stage 2", "Stage 3", "Stage 4", "Stage 5"]


def _stage_series(by_leg_tp, timepoints, leg):
    return [[r["pt7"] for r in by_leg_tp.get((leg, tp[0]), [])] for tp in timepoints]


def _param_means(by_leg_tp, timepoints, leg, stage_idx):
    if stage_idx >= len(timepoints):
        return None
    trials = by_leg_tp.get((leg, timepoints[stage_idx][0]), [])
    if not trials:
        return None
    return {k: float(np.mean([r[k] for r in trials])) for k in _PARAM_KEYS}


def make_comparison_figure(label_a, data_a, tp_a, label_b, data_b, tp_b,
                           out_filename, save=True, return_fig=False):
    """3x2 grid comparing two participants (any label/timepoints -- doesn't
    require matching protocols). Row 1: PT-score trajectory overlay. Row 2:
    baseline (first-timepoint) spasticity signature. Row 3: per-parameter
    change from first to last timepoint. Timepoints are aligned by ORDINAL
    position, not label text."""
    n_stages = max(len(tp_a), len(tp_b))
    stage_labels = [(tp_a[i][1] if i < len(tp_a) else (tp_b[i][1] if i < len(tp_b) else _STAGE_FALLBACK_LABELS[i]))
                    for i in range(n_stages)]

    fig, axes = plt.subplots(3, 2, figsize=(15, 14), facecolor='white')

    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):

        # ── Row 1: PT-score trajectory overlay ──────────────────────────────
        ax = axes[0, col_idx]
        ax.set_facecolor('white')
        y_all = ([v for vals in _stage_series(data_a, tp_a, leg) for v in vals] +
                [v for vals in _stage_series(data_b, tp_b, leg) for v in vals])
        y_max = (max(y_all) if y_all else 1.6) * 1.15
        for (lo, hi), zcolor in zip(zip(ZONE_EDGES[:-1], ZONE_EDGES[1:]), ZONE_COLORS):
            ax.axhspan(lo, min(hi, y_max), facecolor=zcolor, alpha=0.3, zorder=0)

        rng = np.random.RandomState(13)
        for label, by_leg_tp, timepoints, color, marker in (
            (label_a, data_a, tp_a, _CMP_COLOR_A, 'o'),
            (label_b, data_b, tp_b, _CMP_COLOR_B, 's'),
        ):
            x_pts = list(range(len(timepoints)))
            series = _stage_series(by_leg_tp, timepoints, leg)
            means = [float(np.mean(v)) if v else np.nan for v in series]
            for xc, vals in zip(x_pts, series):
                for v in vals:
                    ax.scatter(xc + rng.uniform(-0.06, 0.06), v, color=color,
                             s=16, alpha=0.4, zorder=2)
            valid = [(xc, m) for xc, m in zip(x_pts, means) if np.isfinite(m)]
            if valid:
                ax.plot([p[0] for p in valid], [p[1] for p in valid], color=color,
                       linewidth=2.2, marker=marker, markersize=7, zorder=4, label=label)
            if len(valid) >= 2:
                delta = valid[-1][1] - valid[0][1]
                ax.annotate(f"{label} Δ{delta:+.2f}", xy=valid[-1], xytext=(8, 0),
                          textcoords='offset points', color=color, fontsize=8,
                          fontweight='bold', va='center')

        ax.set_title(f'{leg_label} Leg – PT Score Trajectory ({label_a} vs {label_b})',
                    fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(list(range(n_stages)))
        ax.set_xticklabels(stage_labels, fontsize=8)
        ax.set_ylabel('PT Score (7-parameter)', fontsize=8)
        ax.tick_params(labelsize=8)
        ax.set_xlim(-0.4, n_stages - 0.6)
        ax.set_ylim(0, y_max)
        ax.legend(loc='lower right', fontsize=8, framealpha=0.9)

        # ── Row 2: Baseline spasticity signature (first timepoint) ─────────
        ax = axes[1, col_idx]
        ax.set_facecolor('#f8f9fa')
        ax.grid(True, color=BG_GRID, linestyle='-', linewidth=0.8, axis='y')
        x = np.arange(len(_PARAM_KEYS))
        width = 0.35
        for offset, (label, by_leg_tp, timepoints, color) in zip(
            (-1, 1),
            ((label_a, data_a, tp_a, _CMP_COLOR_A), (label_b, data_b, tp_b, _CMP_COLOR_B)),
        ):
            means = _param_means(by_leg_tp, timepoints, leg, 0)
            vals = [means[k] if means else 0.0 for k in _PARAM_KEYS]
            ax.bar(x + offset * width / 2, vals, width, label=label, color=color, alpha=0.85)
        ax.set_title(f'{leg_label} Leg – Baseline Spasticity Signature ({label_a} vs {label_b})',
                    fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(x)
        ax.set_xticklabels(PARAM_DISPLAY, fontsize=8)
        ax.set_ylabel('Baseline value (log scale)', fontsize=8)
        ax.set_yscale('log')
        ax.set_ylim(bottom=1e-3)
        ax.tick_params(labelsize=8)
        ax.legend(loc='upper right', fontsize=8, framealpha=0.9)

        # ── Row 3: Parameter change, first -> last timepoint ─────────────────
        ax = axes[2, col_idx]
        ax.set_facecolor('#f8f9fa')
        ax.grid(True, color=BG_GRID, linestyle='-', linewidth=0.8, axis='y')
        for offset, (label, by_leg_tp, timepoints, color) in zip(
            (-1, 1),
            ((label_a, data_a, tp_a, _CMP_COLOR_A), (label_b, data_b, tp_b, _CMP_COLOR_B)),
        ):
            m0 = _param_means(by_leg_tp, timepoints, leg, 0)
            m2 = _param_means(by_leg_tp, timepoints, leg, len(timepoints) - 1)
            if m0 and m2 and len(timepoints) > 1:
                deltas = [m2[k] - m0[k] for k in _PARAM_KEYS]
            else:
                deltas = [0.0] * len(_PARAM_KEYS)
            ax.bar(x + offset * width / 2, deltas, width, label=label, color=color, alpha=0.85)
        ax.axhline(0, color='#888888', linewidth=0.8)
        ax.set_title(f'{leg_label} Leg – Parameter Change, First→Last Timepoint ({label_a} vs {label_b})',
                    fontsize=10, fontweight='bold', pad=10)
        ax.set_xticks(x)
        ax.set_xticklabels(PARAM_DISPLAY, fontsize=8)
        ax.set_ylabel('Δ value (symlog scale)', fontsize=8)
        ax.set_yscale('symlog', linthresh=0.1)
        ax.tick_params(labelsize=8)
        ax.legend(loc='upper right', fontsize=8, framealpha=0.9)

    fig.suptitle(
        f"{label_a} vs {label_b} — Spasticity Signature & Change Comparison\n"
        "Both use the full 7-parameter Popovic PT score. Timepoints aligned by ordinal position, "
        "not by matching protocol.",
        fontsize=10, y=0.997, color='#333333')
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out_path = None
    if save:
        out_path = os.path.join(OUT_DIR, out_filename)
        fig.savefig(out_path, dpi=150, facecolor='white')
        print(f"-> {out_path}")
    if return_fig:
        return out_path, fig
    plt.close(fig)
    return out_path


# ══════════════════════════════════════════════════════════════════════════
# RMSE agreement (OptiTrack vs MediaPipe / IMU), generalized from
# p13_leg_session_comparison.py
# ══════════════════════════════════════════════════════════════════════════

_C_MEDIAPIPE = "#AA44FF"
_C_IMU = "#FF9000"


def attach_rmse(by_leg_tp):
    """Best-effort MediaPipe/IMU RMSE lookup for every scored trial, via
    pt.load_hpe_model_curves's standard Recordings/Participant_{pid}/
    Position_1/Height_Joint-Level/ convention (with its own recursive
    Session_*/ fallback). Silently finds nothing (not an error) for
    participants/conditions laid out differently or never processed through
    MediaPipe -- those simply won't have RMSE bars. Mutates trial records
    in place and returns by_leg_tp for chaining."""
    for trials in by_leg_tp.values():
        for rec in trials:
            try:
                curves = pt.load_hpe_model_curves(
                    rec["pid"], "1", rec["trial"],
                    rec["t_raw"], rec["angle_raw"], rec["neutral_deg_raw"])
            except Exception:
                curves = []
            for c in curves:
                if c["name"].startswith("mediapipe"):
                    rec["mediapipe_rmse"] = c.get("rmse")
                elif c["name"] == "imu_viewer":
                    rec["imu_rmse"] = c.get("rmse")
    return by_leg_tp


def make_rmse_figure(participant_label, by_leg_tp, timepoints, out_filename,
                     methodologies=("mediapipe", "imu"), save=True, return_fig=False):
    """1x2 grid (Left, Right): per-trial RMSE bars vs OptiTrack, for whichever
    of MediaPipe/IMU data is actually available. `methodologies` filters
    which series to show even if both were found."""
    attach_rmse(by_leg_tp)
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), facecolor='white')
    any_bars = False

    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):
        ax = axes[col_idx]
        ax.set_facecolor('#f8f9fa')
        ax.grid(True, color=BG_GRID, linestyle='-', linewidth=0.8, axis='y')

        bar_records = []
        for tp_key, tp_label, _ in timepoints:
            for rec in by_leg_tp.get((leg, tp_key), []):
                has_mp = "mediapipe" in methodologies and rec.get("mediapipe_rmse") is not None
                has_imu = "imu" in methodologies and rec.get("imu_rmse") is not None
                if has_mp or has_imu:
                    bar_records.append((tp_label, rec))

        if bar_records:
            any_bars = True
            labels = [f"{tp}\nT{rec['trial']}" for tp, rec in bar_records]
            xpos = np.arange(len(bar_records))
            width = 0.36
            if "mediapipe" in methodologies:
                vals = [rec.get("mediapipe_rmse", np.nan) if rec.get("mediapipe_rmse") is not None else np.nan
                       for _, rec in bar_records]
                ax.bar(xpos - width / 2, vals, width, color=_C_MEDIAPIPE, label="OptiTrack vs MediaPipe", zorder=3)
            if "imu" in methodologies:
                vals = [rec.get("imu_rmse", np.nan) if rec.get("imu_rmse") is not None else np.nan
                       for _, rec in bar_records]
                ax.bar(xpos + width / 2, vals, width, color=_C_IMU, label="OptiTrack vs IMU (Viewer)", zorder=3)
            ax.set_xticks(xpos)
            ax.set_xticklabels(labels, fontsize=7.5)
            ax.legend(loc='upper left', fontsize=8, framealpha=0.9)
        else:
            ax.text(0.5, 0.5, "No MediaPipe/IMU comparison data found\nfor the selected methodology",
                   transform=ax.transAxes, ha='center', va='center', color='#888888', fontsize=10)
            ax.set_xticks([])

        ax.set_title(f'{participant_label} {leg_label} – RMSE vs OptiTrack', fontsize=10, fontweight='bold', pad=10)
        ax.set_ylabel('RMSE (deg)', fontsize=8)
        ax.tick_params(labelsize=8)

    fig.suptitle(f"{participant_label} — Flexion-Angle RMSE Agreement (lower = better)",
                fontsize=11, y=1.0, color='#333333')
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    out_path = None
    if save:
        out_path = os.path.join(OUT_DIR, out_filename)
        fig.savefig(out_path, dpi=150, facecolor='white')
        print(f"-> {out_path}")
    if return_fig:
        return out_path, fig
    plt.close(fig)
    return out_path, any_bars
