"""
pt_cohort_common.py
====================
MS-vs-Control cohort comparison, built on top of pt_report_common.py's
7-parameter Popovic PT score so it stays numerically and visually
consistent with every per-participant report run_pt_analysis.py produces --
unlike the older, disconnected ms_vs_healthy_analysis.py (4-parameter
score, static CSV, different visual style), which this supersedes for
MS-vs-Control purposes without modifying it.

See docs/superpowers/specs/2026-08-06-ms-vs-control-cohort-design.md for
the full design. Called from run_pt_analysis.py's main(); not run
standalone.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import os
import warnings

import numpy as np
from scipy.stats import mannwhitneyu

import pt_report_common as common

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_JSON = os.path.join(BASE_DIR, "participant_groups.json")
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MS_vs_Control")
COMPOSITION_CSV = os.path.join(OUT_DIR, "cohort_composition.csv")
STATS_CSV = os.path.join(OUT_DIR, "ms_vs_control_stats.csv")
FIGURE_PNG = os.path.join(OUT_DIR, "ms_vs_control_boxplots.png")

_PARAM_KEYS = common._PARAM_KEYS  # R2n, N, phi_max_ratio, omega_max_n, omega_min_n, f, area_ratio
_SCORE_KEYS = _PARAM_KEYS + ["pt7"]
_LEGS = ("left", "right")

# Same vocabulary as master_app.py's diagnosis dropdown (MS / Stroke /
# Unaffected Control / Other Motor Impairment) plus participant_groups.json's
# shorter "Control" spelling. Matched case-insensitively. Anything not in
# this map is treated as "not present" (falls through metadata -> registry
# -> unclassified), never guessed into an arm.
_DIAGNOSIS_TO_ARM = {
    "ms": "MS",
    "unaffected control": "Control",
    "control": "Control",
    "stroke": "Excluded",
    "other motor impairment": "Excluded",
}


# ══════════════════════════════════════════════════════════════════════════
# Pure functions (unit-testable, no I/O)
# ══════════════════════════════════════════════════════════════════════════

def classify_participant(pid, metadata_diagnosis, registry, registry_exists):
    """Priority: metadata.json diagnosis, then participant_groups.json
    entry, then unclassified (design spec §6.2). Returns (group, source):
      group  -- "MS" | "Control" | "Excluded" | "Unclassified"
      source -- "metadata" | "registry" | "no_entry" | "registry_missing"

    An unrecognized diagnosis string (typo, or a value not yet in
    _DIAGNOSIS_TO_ARM) is treated the same as "not present" and falls
    through to the next source, rather than being guessed into an arm."""
    if metadata_diagnosis:
        arm = _DIAGNOSIS_TO_ARM.get(metadata_diagnosis.strip().lower())
        if arm:
            return arm, "metadata"
    if not registry_exists:
        return "Unclassified", "registry_missing"
    entry = registry.get(pid)
    if entry:
        arm = _DIAGNOSIS_TO_ARM.get(entry.strip().lower())
        if arm:
            return arm, "registry"
    return "Unclassified", "no_entry"


def aggregate_participant_summary(trials):
    """trials: one participant/leg's list of scored trial records (each a
    dict with at least the _SCORE_KEYS), as returned by
    pt_report_common.collect_participant(). Returns the median across
    trials for each of the 7 PT params + pt7, rounded to 4 decimal places
    (matching this repo's existing stats-CSV rounding convention). Returns
    None for an empty list -- callers must handle that: a participant can
    pass the raw TRIAL_THRESHOLD gate (pt_report_common.leg_trial_counts)
    yet still summarize to None here if every discovered trial failed to
    score (pt_report_common.score_trial already returns None upstream for
    trials with no clean release/oscillation). An even trial count makes
    np.median interpolate between the two middle values -- expected, not
    a bug."""
    if not trials:
        return None
    return {key: round(float(np.median([t[key] for t in trials])), 4) for key in _SCORE_KEYS}


def cliffs_delta(a, b):
    """Proportion of (b > a) pairs minus (a > b) pairs, -1..+1. Ported from
    ms_vs_healthy_analysis.py's helper of the same name -- the formula
    isn't in question, only its input granularity (see
    aggregate_participant_summary) and which module owns it."""
    n = len(a) * len(b)
    if n == 0:
        return float("nan")
    pairs = sum((1 if bi > ai else (-1 if ai > bi else 0)) for ai in a for bi in b)
    return pairs / n


def mann_whitney(a, b):
    """Two-sided Mann-Whitney U. Returns (nan, nan) if either sample has
    fewer than 2 values -- the design spec treats that as "n/a", not an
    error (§7.1)."""
    if len(a) < 2 or len(b) < 2:
        return float("nan"), float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, p = mannwhitneyu(a, b, alternative="two-sided")
    return float(stat), float(p)


def effect_label(d):
    ad = abs(d)
    if math.isnan(ad):
        return "n/a"
    if ad < 0.147:
        return "negligible"
    if ad < 0.330:
        return "small"
    if ad < 0.474:
        return "medium"
    return "large"


def compute_cohort_stats(ms_summaries, control_summaries):
    """ms_summaries/control_summaries: {"left": [...], "right": [...]},
    each a list of per-participant summary dicts (aggregate_participant_
    summary() output, already filtered of None -- see run_cohort_comparison).
    Returns one row per (leg, parameter) covering every _SCORE_KEYS entry:
    median/IQR per arm, Mann-Whitney p, Cliff's delta, effect label,
    n_ms, n_control. Whenever either arm has fewer than 2 values for a
    given leg/parameter, the significance-test fields are None/"n/a" --
    never raised -- while the medians (even from n=1 or n=0) are still
    reported."""
    rows = []
    for leg in _LEGS:
        ms_leg = ms_summaries.get(leg, [])
        ctrl_leg = control_summaries.get(leg, [])
        for key in _SCORE_KEYS:
            ms_vals = np.array([s[key] for s in ms_leg], dtype=float)
            ctrl_vals = np.array([s[key] for s in ctrl_leg], dtype=float)
            row = {"leg": leg, "parameter": key,
                  "n_ms": len(ms_vals), "n_control": len(ctrl_vals)}
            if len(ms_vals):
                q1, q3 = np.percentile(ms_vals, [25, 75])
                row["ms_median"] = round(float(np.median(ms_vals)), 4)
                row["ms_iqr"] = round(float(q3 - q1), 4)
            else:
                row["ms_median"] = row["ms_iqr"] = None
            if len(ctrl_vals):
                q1, q3 = np.percentile(ctrl_vals, [25, 75])
                row["control_median"] = round(float(np.median(ctrl_vals)), 4)
                row["control_iqr"] = round(float(q3 - q1), 4)
            else:
                row["control_median"] = row["control_iqr"] = None
            if len(ms_vals) >= 2 and len(ctrl_vals) >= 2:
                _, p = mann_whitney(ms_vals, ctrl_vals)
                d = cliffs_delta(ms_vals, ctrl_vals)
                row["mann_whitney_p"] = round(p, 4)
                row["cliffs_delta"] = round(d, 4)
                row["effect_size"] = effect_label(d)
            else:
                row["mann_whitney_p"] = row["cliffs_delta"] = None
                row["effect_size"] = "n/a"
            rows.append(row)
    return rows


# ══════════════════════════════════════════════════════════════════════════
# I/O
# ══════════════════════════════════════════════════════════════════════════

def load_registry():
    """Returns (dict, exists). Missing file -> ({}, False). Malformed JSON
    -> ({}, False) too (printed note), treated the same as missing rather
    than raising -- a corrupt registry shouldn't take down run_pt_analysis.py."""
    if not os.path.isfile(REGISTRY_JSON):
        return {}, False
    try:
        with open(REGISTRY_JSON, encoding="utf-8") as f:
            return json.load(f), True
    except (json.JSONDecodeError, OSError):
        print(f"{REGISTRY_JSON} failed to parse -- treating as empty.")
        return {}, False


def load_metadata_diagnosis(pid):
    """Recordings/Participant_<pid>*/metadata.json -> diagnosis field, or
    None if nothing matches. The glob pattern alone can over-match (e.g.
    "Participant_13*" also matches "Participant_130"), so every
    candidate's own participant_id field must equal `pid` exactly before
    its diagnosis is used. Multiple real folders can exist for one
    participant (e.g. Participant_13 and Participant_13_right_post); the
    first with a non-empty diagnosis wins."""
    pattern = os.path.join(REC_ROOT, f"Participant_{pid}*", "metadata.json")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if str(meta.get("participant_id", "")).strip() != pid:
            continue
        diagnosis = str(meta.get("diagnosis", "")).strip()
        if diagnosis:
            return diagnosis
    return None


def current_qualifying_participants():
    """Every participant id currently meeting common.TRIAL_THRESHOLD on
    BOTH legs -- independent of whichever pid(s) run_pt_analysis.py was
    invoked with this run (design spec §6.1). Always recomputed from the
    full discoverable participant set."""
    qualifying = set()
    for pid in common.list_participants().keys():
        counts = common.leg_trial_counts(pid)
        if counts["left"] >= common.TRIAL_THRESHOLD and counts["right"] >= common.TRIAL_THRESHOLD:
            qualifying.add(pid)
    return qualifying


def _folder_hints_control(pid):
    """Best-effort cosmetic hint only -- NEVER used for classification.
    True if any trial path discovered for this participant has a
    condition string containing 'control' (case-insensitive), matching
    the legacy OptiTrack_Recordings/Participant_N_leg_control naming
    convention. Used only to decorate a no_entry warning line."""
    return any(r["participant"] == pid and "control" in r["condition"].lower()
              for r in common.discover_all_trials())


def build_composition_rows(pids):
    """One row per pid in `pids` (already the qualifying set): classify,
    look up raw trial counts, package for the composition CSV/banner.
    `diagnosis` carries the raw source string (for the Excluded banner
    line) and is not written to the CSV."""
    registry, registry_exists = load_registry()
    rows = []
    for pid in sorted(pids, key=int):
        metadata_diagnosis = load_metadata_diagnosis(pid)
        group, source = classify_participant(pid, metadata_diagnosis, registry, registry_exists)
        raw_diagnosis = metadata_diagnosis if source == "metadata" else registry.get(pid)
        counts = common.leg_trial_counts(pid)
        rows.append({"pid": pid, "group": group, "source": source, "diagnosis": raw_diagnosis,
                    "n_trials_left": counts["left"], "n_trials_right": counts["right"]})
    return rows


def write_composition_csv(rows, out_path=None):
    out_path = out_path or COMPOSITION_CSV
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pid", "group", "source", "n_trials_left", "n_trials_right"])
        for row in rows:
            w.writerow([row["pid"], row["group"], row["source"],
                       row["n_trials_left"], row["n_trials_right"]])
    print(f"-> {out_path}")


def print_composition_banner(rows):
    by_group = {"MS": [], "Control": [], "Excluded": [], "Unclassified": []}
    for row in rows:
        by_group[row["group"]].append(row)

    print("=" * 20 + " MS vs Control cohort " + "=" * 20)
    ms_txt = ", ".join(r["pid"] for r in by_group["MS"]) or "(none yet)"
    print(f"MS:           {ms_txt}  (n={len(by_group['MS'])})")
    ctrl_txt = ", ".join(r["pid"] for r in by_group["Control"]) or "(none yet)"
    print(f"Control:      {ctrl_txt}  (n={len(by_group['Control'])})")

    excl_txt = ", ".join(f"{r['pid']} ({r['diagnosis']})" for r in by_group["Excluded"]) or "(none)"
    print(f"Excluded:     {excl_txt}  (n={len(by_group['Excluded'])})")

    no_entry = [r for r in by_group["Unclassified"] if r["source"] == "no_entry"]
    missing = [r for r in by_group["Unclassified"] if r["source"] == "registry_missing"]
    if no_entry:
        parts = []
        for r in no_entry:
            hint = " (folder suggests 'control')" if _folder_hints_control(r["pid"]) else ""
            parts.append(f"{r['pid']}{hint}")
        print(f"Unclassified: {', '.join(parts)}  (n={len(no_entry)}, no_entry -- add to participant_groups.json)")
    if missing:
        pids_txt = ", ".join(r["pid"] for r in missing)
        print(f"              registry_missing ({pids_txt}): participant_groups.json not found --")
        print("              if you're in a worktree/isolated checkout, copy it over.")
    print("=" * 63)
