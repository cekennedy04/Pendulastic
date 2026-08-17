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

import csv
import glob
import json
import os
import re
import sys

from datetime import datetime, timezone

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
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
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


def release_aligned_hpe_curve(mdl_t, mdl_ang):
    """Per-source mirror of release_aligned_waveform() for an HPE/IMU curve
    (mdl_t, mdl_ang) rather than a trial record dict -- SG-smooth, detect
    release with pt._detect_release on the raw/smoothed (not detrended)
    signal, shift so that curve's OWN detected release lands at t=0.
    Without this, a source's timing offset from OptiTrack would visually
    read as an angle-tracking error instead of a timing artifact.

    Owns its own input validation (pt._detect_release doesn't reject
    degenerate input): fewer than 4 finite samples, or non-monotonic t,
    returns None rather than raising or producing a bogus alignment --
    same "unavailable for this timepoint" path callers already use for a
    missing/rejected HPE curve. No separate short-window guard is needed:
    pt._sg() degrades gracefully (returns the signal unsmoothed) for a
    window too small to fit, rather than raising, matching how
    _detect_release() already tolerates short input.

    Early-release guard (2026-08-10 full-report-hpe-accuracy design spec
    §5.1, addressed here after real-data verification against participant
    14): pt._detect_release's adaptive threshold can fire almost
    immediately when a curve's very first samples are themselves noisy --
    e.g. unreliable early MediaPipe pose tracking -- rather than a real
    pre-release hold. Using that index as the alignment anchor confirmed
    shifting a real MediaPipe curve to physically implausible knee angles
    (~260 degrees, against OptiTrack's own ~150 degree range for the same
    trial). The global constraint on this plan forbids modifying
    _detect_release's own algorithm, so this function instead refuses to
    trust an anchor with fewer than MIN_BASELINE_SAMPLES pre-release
    samples -- too few to represent a real hold baseline -- and returns
    None (same "unavailable for this timepoint" path as every other
    rejection here) rather than a silently-misaligned curve."""
    MIN_BASELINE_SAMPLES = 4
    mask = np.isfinite(mdl_ang) & np.isfinite(mdl_t)
    if mask.sum() < 4:
        return None
    t_masked = mdl_t[mask]
    a_masked = mdl_ang[mask]
    if np.any(np.diff(t_masked) <= 0):
        return None
    a_smooth = pt._sg(a_masked, w=15, p=3)
    release_idx = pt._detect_release(t_masked, a_smooth)
    release_idx = max(0, min(release_idx, len(t_masked) - 1))
    if release_idx < MIN_BASELINE_SAMPLES:
        return None
    t_release = t_masked[release_idx]
    y_off = 180.0 - float(a_smooth[release_idx])
    t_plot = t_masked - t_release
    a_plot = a_masked + y_off
    return t_plot, a_plot


def _hpe_overlay_series(rec):
    """For one trial record (after attach_rmse), returns
    [(source_label, linestyle, t_plot, a_plot), ...] for whichever of
    mediapipe_curve/imu_curve are present and release-align successfully.
    OptiTrack itself is plotted separately by the existing Row 1 loop
    (unchanged) -- this only covers the two overlay sources."""
    out = []
    for key, label, linestyle in (("mediapipe_curve", "MediaPipe", "--"), ("imu_curve", "IMU", ":")):
        curve = rec.get(key)
        if curve is None:
            continue
        aligned = release_aligned_hpe_curve(curve["t"], curve["ang"])
        if aligned is None:
            continue
        t_plot, a_plot = aligned
        out.append((label, linestyle, t_plot, a_plot))
    return out


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
TRIAL_QUALITY_TAGS_PATH = os.path.join(BASE_DIR, "trial_quality_tags.json")

# Stratification-only tag categories (design spec
# docs/superpowers/specs/2026-08-11-trial-quality-triage-design.md Section
# 3) -- distinct from EXCLUDED_TRIALS_PATH's free-text reasons, which stay
# unvalidated (existing behavior, unchanged).
QUALITY_TAG_CATEGORIES = ("calibration_hold", "marker_occlusion", "mounting_slip",
                          "release_contamination", "other")


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


def _atomic_write_json(path, data):
    """Temp-file-then-os.replace atomic write, matching
    imu_calibration_config.save_config()'s existing pattern."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def load_quality_tags():
    """{trial_key: {"category","details","timestamp"}} for trials tagged
    with a quality concern. Drives stratified reporting only -- presence
    here never removes a trial from discover_all_trials()/discover_trials()
    (design spec Section 3). Missing or malformed file -> {}, matching
    load_excluded_trials()'s convention."""
    try:
        with open(TRIAL_QUALITY_TAGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_quality_tag(key, category, details="", timestamp=None):
    """Record (or overwrite) one trial's quality tag. Raises ValueError (no
    write attempted) if category isn't one of QUALITY_TAG_CATEGORIES,
    mirroring mas_validation._valid_grade()'s existing gate on mas_grade."""
    if category not in QUALITY_TAG_CATEGORIES:
        raise ValueError(
            f"invalid category {category!r} (must be one of {QUALITY_TAG_CATEGORIES})")
    tags = load_quality_tags()
    tags[key] = {
        "category": category,
        "details": details,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(TRIAL_QUALITY_TAGS_PATH, tags)


def clear_quality_tag(key):
    """Remove key's tag if present. No-op (not an error) if key isn't tagged."""
    tags = load_quality_tags()
    if key in tags:
        del tags[key]
        _atomic_write_json(TRIAL_QUALITY_TAGS_PATH, tags)


def add_excluded_trial(key, reason):
    """Add (or overwrite) key's hard-exclusion reason. No validation on
    reason -- exclusion reasons have always been free text (see this file's
    existing muscle-intervention entries); only quality tags (above) get a
    validated category."""
    excluded = load_excluded_trials()
    excluded[key] = reason
    _atomic_write_json(EXCLUDED_TRIALS_PATH, excluded)


def clear_excluded_trial(key):
    """Remove key's exclusion entry if present. No-op if key isn't excluded."""
    excluded = load_excluded_trials()
    if key in excluded:
        del excluded[key]
        _atomic_write_json(EXCLUDED_TRIALS_PATH, excluded)


def trial_candidates(participant_id, include_archive=True):
    """Every trial_*_optitrack.csv discovered for this participant, kept
    regardless of exclusion/validity/scoreability, each tagged with a
    terminal status -- the data source for the full report's data-
    completeness caption line (2026-08-10 full-report-hpe-accuracy design
    spec §5.6). Standalone from discover_all_trials()/collect_participant()
    by design (see this function's task in the implementation plan for
    why) -- does its own raw glob rather than filtering their output, so
    it can preserve candidates they intentionally drop (INVALID paths,
    excluded trials) plus files _parse_trial_path() can't attribute to a
    participant/leg at all (status "unparseable").

    Never raises -- matches this module's other discovery/loader
    conventions. status is one of:
      "unparseable"  -- _parse_trial_path() returned None
      "invalid_path" -- "INVALID" in the path
      "excluded"     -- key present in load_excluded_trials(); reason set
      "unreadable"   -- pt.load_optitrack() raised
      "unscoreable"  -- score_trial() returned None
      "scored"       -- record set to the scored trial dict
    """
    excluded = load_excluded_trials()
    out = []
    seen = set()
    roots = [OPTI_ROOT] + ([ARCHIVE_ROOT] if include_archive and os.path.isdir(ARCHIVE_ROOT) else [])
    for root in roots:
        for csv_path in glob.glob(os.path.join(root, "**", "trial_*_optitrack.csv"), recursive=True):
            real = os.path.realpath(csv_path)
            if real in seen:
                continue
            seen.add(real)

            if "INVALID" in csv_path.upper():
                rec = _parse_trial_path(csv_path, root)
                if rec is not None and rec["participant"] != participant_id:
                    continue
                out.append({"leg": None, "condition": None, "trial": None, "path": csv_path,
                           "status": "invalid_path", "reason": None, "record": None})
                continue

            rec = _parse_trial_path(csv_path, root)
            if rec is None:
                # Can't attribute to a participant at all -- can't filter
                # by participant_id either, so every unparseable file in
                # the tree is included regardless of which pid was asked
                # for. Callers building a per-participant completeness
                # tally should treat this bucket as tree-wide, not
                # per-participant, and say so in the UI copy.
                out.append({"leg": None, "condition": None, "trial": None, "path": csv_path,
                           "status": "unparseable", "reason": None, "record": None})
                continue
            if rec["participant"] != participant_id:
                continue

            key = trial_key(rec["participant"], rec["leg"], rec["condition"], rec["trial"])
            if key in excluded:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "excluded", "reason": excluded[key], "record": None})
                continue

            try:
                t, angle = pt.load_optitrack(csv_path)
            except Exception:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "unreadable", "reason": None, "record": None})
                continue

            pid_key = f"{participant_id}_{rec['leg']}_{rec['condition']}"
            record = score_trial(pid_key, rec["trial"], t, angle)
            if record is None:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "unscoreable", "reason": None, "record": None})
            else:
                out.append({"leg": rec["leg"], "condition": rec["condition"], "trial": rec["trial"],
                           "path": csv_path, "status": "scored", "reason": None, "record": record})
    return out


def _parse_mas_assessed_date(date_str):
    """mas_scores.csv's assessed_date column is free-text M/D/YYYY (e.g.
    "8/6/2026"), not zero-padded or ISO -- lexicographic string sort would
    silently misorder "12/1/2026" before "8/6/2026". Returns a
    datetime.date, or None for blank/unparseable (sorted last by callers,
    never raised)."""
    import datetime
    date_str = (date_str or "").strip()
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
    except ValueError:
        return None


def clinician_mas_matches(participant_id, leg, condition):
    """All valid mas_scores.csv rows for this participant/leg/condition,
    sorted most-recent-first by assessed_date. Local import of
    mas_validation -- mas_validation.py already imports pt_report_common
    at module scope, so a module-scope import here would be circular; a
    function-local import is safe since both modules are fully
    initialized in Python's module cache by the time this is ever called
    (during report generation). Reuses mas_validation's own bag-of-tokens
    condition matching and grade validation rather than reimplementing
    them, so this stays consistent with mas_validation.py's own
    pair_pt_and_mas()/_pt_lookup_factory()."""
    import datetime
    import mas_validation as mv
    if not os.path.isfile(mv.MAS_CSV):
        return []
    wanted = mv._tokenize_condition(condition)
    rows = mv.load_mas_scores(mv.MAS_CSV)
    matches = [r for r in rows
              if r.get("participant") == participant_id
              and r.get("leg") == leg
              and mv._tokenize_condition(r.get("condition", "")) == wanted
              and mv._valid_grade(r.get("mas_grade", ""))]
    matches.sort(key=lambda r: _parse_mas_assessed_date(r.get("assessed_date")) or datetime.date.min,
                reverse=True)
    return matches


def write_clinician_mas_sidecar(participant_id, matches_by_leg_condition, out_dir=None):
    """Complete, untruncated clinician-MAS record for this participant --
    Row 5 of the full report (see _draw_row5_table) may only show the 2
    most recent matches per leg/condition for density reasons, but this
    file always has every match, so 'all relevant participant data is
    included' is true of the report's full output (figure + sidecar),
    not just the PNG in isolation. Same pattern this codebase already
    uses for the MS-vs-Control cohort artifacts (ms_vs_control_stats.csv
    as the complete record, the PNG as a bounded summary)."""
    out_dir = out_dir or OUT_DIR
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"P{participant_id}_clinician_mas.csv")
    fieldnames = ["participant", "leg", "condition", "diagnosis", "mas_grade",
                 "assessed_by", "assessed_date"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for matches in matches_by_leg_condition.values():
            for row in matches:
                w.writerow(row)
    return out_path


def _row5_source_pt7(rec, curve_key):
    """PT7 (7-param, same compute_pt_score used for OptiTrack everywhere
    else in this module) computed directly from one source's own curve on
    this trial record, or None if the curve is missing or fails to
    score. curve_key is "mediapipe_curve" or "imu_curve" (set by
    attach_rmse, Task 2)."""
    curve = rec.get(curve_key)
    if curve is None:
        return None
    params = pt.compute_pt_params(curve["t"], curve["ang"])
    if params is None:
        return None
    return pt.compute_pt_score(params)


def _draw_row5_table(ax, leg, by_leg_tp, timepoints, participant_id):
    """PT7/MAS source-agreement table for one leg: OptiTrack vs MediaPipe
    and OptiTrack vs IMU, each with its OWN paired OptiTrack baseline
    (not one shared number -- MediaPipe and IMU can pass the quality gate
    on different trial subsets), three-stage missing/rejected/unscored
    accounting, and clinician MAS shown at most 2-most-recent with a
    '+N more' note (full record goes to the sidecar CSV -- see
    write_clinician_mas_sidecar, Task 6). Returns
    {(leg, condition): matches_used} so the caller can write the sidecar
    from the exact matches this table displayed, without recomputing."""
    ax.axis("off")
    matches_by_leg_condition = {}
    rows = []

    for tp_key, tp_label, color in timepoints:
        trials = by_leg_tp.get((leg, tp_key), [])
        if not trials:
            continue

        # Per-trial gate accounting, fetched fresh with return_rejected=True
        # -- attach_rmse's rec["mediapipe_curve"]/rec["imu_curve"] only
        # carries POST-gate survivors, so re-deriving "had a candidate at
        # all" from those keys would conflate "no candidate CSV" with
        # "candidate CSV existed but was rejected by the quality gate",
        # exactly the distinction Task 1's return_rejected mode exists to
        # preserve. One call per trial, reused for both sources below.
        gate_by_trial = {}
        for r in trials:
            try:
                accepted, rejected = pt.load_hpe_model_curves(
                    r["pid"], "1", r["trial"], r["t_raw"], r["angle_raw"], r["neutral_deg_raw"],
                    return_rejected=True)
            except Exception:
                accepted, rejected = [], []
            gate_by_trial[id(r)] = (accepted, rejected)

        for source_label, curve_key, name_matches in (
            ("MediaPipe", "mediapipe_curve", lambda n: n.startswith("mediapipe")),
            ("IMU", "imu_curve", lambda n: n == "imu_viewer"),
        ):
            n_candidate = 0
            n_passed_gate = 0
            for r in trials:
                accepted, rejected = gate_by_trial[id(r)]
                has_accepted = any(name_matches(c["name"]) for c in accepted)
                has_rejected = any(name_matches(c["name"]) for c in rejected)
                if has_accepted or has_rejected:
                    n_candidate += 1
                if has_accepted:
                    n_passed_gate += 1

            source_pt7s = [_row5_source_pt7(r, curve_key) for r in trials if r.get(curve_key) is not None]
            n_scored = sum(1 for v in source_pt7s if v is not None)
            paired_opti = [r["pt7"] for r in trials if r.get(curve_key) is not None]
            opti_paired_mean = float(np.mean(paired_opti)) if paired_opti else None
            source_mean = float(np.mean([v for v in source_pt7s if v is not None])) if n_scored else None
            delta = (source_mean - opti_paired_mean) if (source_mean is not None and opti_paired_mean is not None) else None

            rows.append({
                "timepoint": tp_label, "source": source_label,
                "opti_paired_pt7": opti_paired_mean, "opti_paired_n": len(paired_opti),
                "source_pt7": source_mean, "source_mas": pt.pt_to_mas(source_mean) if source_mean is not None else None,
                "delta": delta,
                "n_candidate": n_candidate, "n_passed_gate": n_passed_gate,
                "n_total": len(trials), "n_scored": n_scored,
            })

        matches = clinician_mas_matches(participant_id, leg, tp_key)
        matches_by_leg_condition[(leg, tp_key)] = matches

    if not rows:
        ax.text(0.5, 0.5, "No timepoints available", transform=ax.transAxes,
               ha="center", va="center", color="#888888", fontsize=9)
        return matches_by_leg_condition

    def _fmt_pt7(v):
        return f"{v:.3f}" if v is not None else "—"

    def _fmt_delta(v):
        return f"{v:+.3f}" if v is not None else "—"

    caveat = ("Algorithm agreement, not independent clinical scores — PT7 computed identically "
             "across sources but on curves with different sampling, smoothing, and cleaning "
             "characteristics.")
    ax.text(0.0, 1.0, caveat, transform=ax.transAxes, fontsize=6.5, style="italic",
           color="#666666", ha="left", va="top", wrap=True)

    table_rows = []
    for r in rows:
        clin = matches_by_leg_condition.get((leg, next(tp for tp, lbl, _ in timepoints if lbl == r["timepoint"])), [])
        clin_shown = clin[:2]
        clin_txt = "; ".join(f"{m['mas_grade']} ({m.get('assessed_date') or 'date n/a'})" for m in clin_shown)
        if len(clin) > 2:
            clin_txt += f" +{len(clin) - 2} more"
        table_rows.append([
            r["timepoint"], r["source"],
            f"{_fmt_pt7(r['opti_paired_pt7'])} (n={r['opti_paired_n']})",
            f"{_fmt_pt7(r['source_pt7'])}" + (f" [{r['source_mas']}]" if r["source_mas"] else ""),
            _fmt_delta(r["delta"]),
            f"{r['n_candidate']}/{r['n_total']} had candidate, "
            f"{r['n_passed_gate']}/{r['n_candidate'] or 1} passed gate, "
            f"{r['n_scored']}/{r['n_passed_gate'] or 1} scored",
            clin_txt or "—",
        ])

    col_labels = ["Timepoint", "Source", "OptiTrack (paired)", "Source PT7 [MAS]",
                 "Δ", "Accounting", "Clinician MAS"]
    tbl = ax.table(cellText=table_rows, colLabels=col_labels, loc="center", cellLoc="center")
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(6.5)
    # Equal-width columns (ax.table()'s default) overlap: "Accounting"'s long
    # status strings bleed into "Clinician MAS", and the numeric "Δ" values
    # (e.g. "+0.747") bleed into "Accounting" -- confirmed against real
    # participant data (2026-08-10 full-report-hpe-accuracy plan, Task 14
    # manual verification). auto_set_column_width sizes each column to its
    # own widest cell (including header) instead.
    tbl.auto_set_column_width(col=list(range(len(col_labels))))
    tbl.scale(1, 1.4)
    return matches_by_leg_condition


def _build_caption_text(participant_label, participant_id, by_leg_tp, timepoints, cohort_snapshot):
    """Caption block below the full-report figure: leg-specific MS/Control
    cohort reference (leave-one-out for the participant's own arm, if
    any) plus a per-leg/condition data-completeness tally. Every `n` is
    explicitly labeled by what it counts -- never a bare number.
    cohort_snapshot=None (or either arm empty) simply omits the cohort
    lines rather than raising."""
    lines = []

    if cohort_snapshot is not None:
        import pt_cohort_common as pcc
        for leg in ("left", "right"):
            own_trials = [r for (leg_key, _cond), recs in by_leg_tp.items()
                         if leg_key == leg for r in recs]
            if not own_trials:
                continue
            own_pt7 = float(np.mean([r["pt7"] for r in own_trials]))
            ref = pcc.leg_cohort_reference(cohort_snapshot, participant_id, leg)
            if ref is None:
                continue
            leg_label = leg.capitalize()
            parts = [f"{leg_label} leg — this participant PT7 (n={len(own_trials)} trials): {own_pt7:.2f}"]
            ms_suffix = " (leave-one-out)" if ref["leave_one_out_arm"] == "MS" else ""
            control_suffix = " (leave-one-out)" if ref["leave_one_out_arm"] == "Control" else ""
            ms_txt = f"{ref['ms_median']:.2f}" if ref["ms_median"] is not None else "n/a"
            control_txt = f"{ref['control_median']:.2f}" if ref["control_median"] is not None else "n/a"
            parts.append(f"MS arm median (n={ref['ms_n']} contributing participants{ms_suffix}): {ms_txt}")
            parts.append(f"Control arm median (n={ref['control_n']}{control_suffix}): {control_txt}")
            lines.append(" | ".join(parts))

    candidates = trial_candidates(participant_id)
    by_leg_condition = {}
    for c in candidates:
        if c["leg"] is None:
            continue
        key = (c["leg"], c["condition"])
        by_leg_condition.setdefault(key, {"recorded": 0, "excluded": 0, "unreadable": 0,
                                          "unscoreable": 0, "scored": 0})
        if c["status"] in ("excluded",):
            by_leg_condition[key]["excluded"] += 1
        elif c["status"] == "unreadable":
            by_leg_condition[key]["unreadable"] += 1
        elif c["status"] == "unscoreable":
            by_leg_condition[key]["unscoreable"] += 1
        elif c["status"] == "scored":
            by_leg_condition[key]["scored"] += 1
        if c["status"] != "invalid_path":
            by_leg_condition[key]["recorded"] += 1

    plotted_keys = {(leg_key, cond_key) for (leg_key, cond_key) in by_leg_tp.keys()}
    for (leg, cond), tally in sorted(by_leg_condition.items()):
        if (leg, cond) not in plotted_keys:
            continue
        lines.append(f"{leg.capitalize()}/{cond}: {tally['recorded']} recorded, "
                     f"{tally['excluded']} excluded, {tally['unreadable']} unreadable, "
                     f"{tally['unscoreable']} unscoreable, {tally['scored']} scored")

    return "\n".join(lines)


def discover_all_trials(include_archive=True, *, include_excluded=False):
    """Every trial_*_optitrack.csv under the live repo (and, optionally, the
    known archive) parsed into {participant, leg, condition, trial, path}
    records. Quarantined/invalid data (INVALID_ in the path) is always
    excluded.

    include_excluded=False (default): trials listed in excluded_trials.json
    are dropped, and records carry no trial_key/excluded fields --
    byte-for-byte the shape every existing caller has always gotten.
    include_excluded=True: excluded trials are included too, and every
    returned record additionally carries trial_key (str) and excluded
    (bool) -- used by AnalysisPanel's trial-exclusion UI (design spec
    docs/superpowers/specs/2026-08-07-trial-exclusion-ui-design.md Section 3)."""
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
            try:
                rec = _parse_trial_path(csv_path, root)
            except OSError:
                # _parse_trial_path() calls os.path.getmtime() uncaught --
                # a deleted/inaccessible file must only drop this one
                # record, not abort the whole discovery call.
                continue
            if rec is None:
                continue
            key = trial_key(rec["participant"], rec["leg"], rec["condition"], rec["trial"])
            is_excluded = key in excluded
            if is_excluded and not include_excluded:
                continue
            if include_excluded:
                rec = dict(rec, trial_key=key, excluded=is_excluded)
            records.append(rec)
    return records


def list_participants(include_archive=True, *, include_excluded=False):
    """{participant_id: {"legs": {...}, "n_trials": int, "conditions": [...]}}
    sorted by participant id, for populating a UI picker.

    include_excluded=False (default, unchanged for every existing caller): a
    participant whose every trial is excluded doesn't appear at all.
    include_excluded=True: such a participant still appears, with
    n_trials == 0 (excluded trials aren't counted into legs/conditions/
    n_trials either), so AnalysisPanel can flag and let the operator
    re-select/undo them."""
    records = discover_all_trials(include_archive=include_archive, include_excluded=include_excluded)
    by_pid = {}
    for r in records:
        entry = by_pid.setdefault(r["participant"], {"legs": set(), "conditions": set(), "n_trials": 0})
        if r.get("excluded"):
            continue
        entry["legs"].add(r["leg"])
        entry["conditions"].add(r["condition"])
        entry["n_trials"] += 1
    return dict(sorted(by_pid.items(), key=lambda kv: int(kv[0])))


def duplicate_trial_keys(records: list) -> dict:
    """{trial_key: [path, ...]} for every trial_key shared by more than one
    record in the given list. A pure function over an already-fetched
    records list -- never calls discover_all_trials() itself, so the caller
    must pass it the exact same list a table/report was already built from
    (design spec Section 3): duplicate detection can't disagree with what's
    on screen, and it works whether or not the caller filtered to a single
    participant first."""
    by_key = {}
    for r in records:
        by_key.setdefault(r["trial_key"], []).append(r["path"])
    return {k: paths for k, paths in by_key.items() if len(paths) > 1}


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


def first_recording_time(participant_id):
    """Earliest mtime among this participant's discoverable (non-excluded)
    trials, across both legs and every condition/session -- lets
    run_pt_analysis.py gate report generation on elapsed time since the
    first recording landed rather than on TRIAL_THRESHOLD trial counts,
    since not every timepoint reaches TRIAL_THRESHOLD trials per leg.
    Returns None if this participant has no discoverable trials yet."""
    mtimes = [r["mtime"] for r in discover_all_trials() if r["participant"] == participant_id]
    return min(mtimes) if mtimes else None


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
                       cohort_snapshot=None, save=True, return_fig=False):
    """5x2 grid: rows = Waveforms (+ HPE/IMU overlay) / 7-param bars /
    PT-score trend / RMSE agreement / PT7-MAS source-agreement table,
    columns = Left leg / Right leg. `by_leg_tp` keys are (leg, tp_key) with
    leg in ("left","right") and tp_key matching timepoints' first element.
    cohort_snapshot (pt_cohort_common.build_cohort_snapshot() output) is
    optional -- None simply omits the caption's cohort-reference lines."""
    fig, axes = plt.subplots(5, 2, figsize=(15, 21), facecolor='white')

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
            for source_label, linestyle, hpe_t, hpe_a in _hpe_overlay_series(rep):
                ax.plot(hpe_t, hpe_a, color=color, linewidth=1.1, linestyle=linestyle,
                       alpha=0.85, label=f'{tp_label} {source_label}')
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

    # participant_label always has the "P{pid}" shape every existing call
    # site (run_pt_analysis.py, p13_full_report.py, p5_full_report.py) uses,
    # so lstrip("P") recovers the raw participant id clinician_mas_matches/
    # write_clinician_mas_sidecar/trial_candidates expect.
    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):
        ax4 = axes[3, col_idx]
        _draw_rmse_axes(ax4, leg, by_leg_tp, timepoints)
        ax4.set_title(f'{participant_label} {leg_label} – RMSE vs OptiTrack', fontsize=10, fontweight='bold', pad=10)

        ax5 = axes[4, col_idx]
        matches_used = _draw_row5_table(ax5, leg, by_leg_tp, timepoints, participant_label.lstrip("P"))
        if matches_used:
            write_clinician_mas_sidecar(participant_label.lstrip("P"), matches_used)
        ax5.set_title(f'{participant_label} {leg_label} – PT7/MAS Source Agreement', fontsize=10, fontweight='bold', pad=10)

    fig.suptitle(f"{participant_label} — Full Report (7-parameter Popovic PT score)\n{caveat_text}",
                fontsize=10, y=0.998, color='#333333')
    caption = _build_caption_text(participant_label, participant_label.lstrip("P"), by_leg_tp, timepoints, cohort_snapshot)
    if caption:
        fig.text(0.02, 0.005, caption, fontsize=6.5, color="#444444", va="bottom", ha="left")
    plt.tight_layout(rect=[0, 0.04, 1, 0.975])
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
    """Best-effort MediaPipe/IMU RMSE + curve lookup for every scored trial,
    via pt.load_hpe_model_curves's standard Recordings/Participant_{pid}/
    Position_1/Height_Joint-Level/ convention (with its own recursive
    Session_*/ fallback). Silently finds nothing (not an error) for
    participants/conditions laid out differently or never processed through
    MediaPipe -- those simply won't have RMSE bars or curve overlays.
    Mutates trial records in place and returns by_leg_tp for chaining.

    Deterministic candidate: curves arrive sorted best-RMSE-first from
    load_hpe_model_curves, so the FIRST mediapipe-name match and the FIRST
    imu_viewer match are used -- not whichever the loop happens to see
    last -- and that same candidate's rmse and t/ang curve are stored
    together, so a later consumer (the waveform overlay, the PT7 score,
    the RMSE bar) can never end up looking at mismatched candidates for
    the same trial."""
    for trials in by_leg_tp.values():
        for rec in trials:
            try:
                curves = pt.load_hpe_model_curves(
                    rec["pid"], "1", rec["trial"],
                    rec["t_raw"], rec["angle_raw"], rec["neutral_deg_raw"])
            except Exception:
                curves = []
            mediapipe_curve = next((c for c in curves if c["name"].startswith("mediapipe")), None)
            imu_curve = next((c for c in curves if c["name"] == "imu_viewer"), None)
            rec["mediapipe_rmse"] = mediapipe_curve.get("rmse") if mediapipe_curve else None
            rec["mediapipe_curve"] = {"t": mediapipe_curve["t"], "ang": mediapipe_curve["ang"]} if mediapipe_curve else None
            rec["imu_rmse"] = imu_curve.get("rmse") if imu_curve else None
            rec["imu_curve"] = {"t": imu_curve["t"], "ang": imu_curve["ang"]} if imu_curve else None
    return by_leg_tp


def _draw_rmse_axes(ax, leg, by_leg_tp, timepoints, methodologies=("mediapipe", "imu")):
    """Per-trial RMSE bars vs OptiTrack for one leg's axes -- extracted
    from make_rmse_figure() so both the standalone P{pid}_rmse.png and
    Row 4 of the full report (make_report_figure(), Task 11) draw from
    the exact same code and can never drift apart. Returns whether any
    bars were drawn."""
    ax.set_facecolor('#f8f9fa')
    ax.grid(True, color=BG_GRID, linestyle='-', linewidth=0.8, axis='y')

    bar_records = []
    for tp_key, tp_label, _ in timepoints:
        for rec in by_leg_tp.get((leg, tp_key), []):
            has_mp = "mediapipe" in methodologies and rec.get("mediapipe_rmse") is not None
            has_imu = "imu" in methodologies and rec.get("imu_rmse") is not None
            if has_mp or has_imu:
                bar_records.append((tp_label, rec))

    any_bars = False
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

    ax.set_ylabel('RMSE (deg)', fontsize=8)
    ax.tick_params(labelsize=8)
    return any_bars


def make_rmse_figure(participant_label, by_leg_tp, timepoints, out_filename,
                     methodologies=("mediapipe", "imu"), save=True, return_fig=False):
    """1x2 grid (Left, Right): per-trial RMSE bars vs OptiTrack, for whichever
    of MediaPipe/IMU data is actually available. `methodologies` filters
    which series to show even if both were found. Unchanged external
    behavior from before the _draw_rmse_axes extraction (Task 8 of the
    implementation plan)."""
    attach_rmse(by_leg_tp)
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), facecolor='white')
    any_bars = False

    for col_idx, (leg, leg_label) in enumerate((("left", "Left"), ("right", "Right"))):
        ax = axes[col_idx]
        bars_this_leg = _draw_rmse_axes(ax, leg, by_leg_tp, timepoints, methodologies)
        any_bars = any_bars or bars_this_leg
        ax.set_title(f'{participant_label} {leg_label} – RMSE vs OptiTrack', fontsize=10, fontweight='bold', pad=10)

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
