"""
pt_cohort_common.py
====================
Three-arm cohort comparison (MS / Stroke / Control), built on top of
pt_report_common.py's 7-parameter Popovic PT score so it stays numerically
and visually consistent with every per-participant report run_pt_analysis.py
produces -- unlike the older, disconnected ms_vs_healthy_analysis.py
(4-parameter score, static CSV, different visual style), which this
supersedes without modifying it.

Stroke became a full arm on 2026-08-26, replacing the original two-arm
MS-vs-Control design: post-stroke participants are part of the study, so
computing a single MS-vs-Control contrast (and excluding them outright, as
this module did before) dropped them from the analysis entirely.

NOT THE PRIMARY COMPARISON (2026-08-28)
---------------------------------------
Participants are grouped by SPASTICITY now, not by diagnosis. The primary
stratification is generate_figures_by_spasticity.py; this module is retained
as a secondary, reference view and its outputs are labelled as such.

The reason is that diagnosis is a proxy for the thing the pendulum test
actually responds to. MS and stroke each produce a wide range of spasticity, so
a diagnosis arm mixes severities together and a difference between arms can be
read either as an effect of the disease or as an accident of who happened to
enrol. Grouping on the measured impairment removes that ambiguity. Spasticity
grouping carries its own caveat -- some labels are derived from A0, which is
also an outcome -- and generate_figures_by_spasticity.py documents how it
handles the circularity.

Kept rather than deleted because the diagnosis contrast is still the one
clinical readers expect to see, and because MS-vs-Stroke is a genuine etiology
question that spasticity grouping cannot answer by construction. Treat its
output as supporting material, not as a headline result.

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
import matplotlib.pyplot as plt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_JSON = os.path.join(BASE_DIR, "participant_groups.json")
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Cohort_Comparison")
COMPOSITION_CSV = os.path.join(OUT_DIR, "cohort_composition.csv")
STATS_CSV = os.path.join(OUT_DIR, "cohort_stats.csv")
FIGURE_PNG = os.path.join(OUT_DIR, "cohort_boxplots.png")
RANGES_CSV = os.path.join(OUT_DIR, "normative_ranges.csv")

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
    "stroke": "Stroke",
    "other motor impairment": "Excluded",
}

# Comparison arms, in reporting order. Stroke became a full arm on
# 2026-08-26: post-stroke participants are part of the study, and pooling them
# into MS would hide any etiology-specific effect while pooling them into
# "impaired" would hide both. "Excluded" remains for diagnoses that genuinely
# aren't part of this comparison (currently only "other motor impairment").
_ARMS = ("MS", "Stroke", "Control")

# Pairwise contrasts run per (leg, parameter). Control is the reference for the
# two clinical arms; MS vs Stroke is the etiology contrast.
_CONTRASTS = (("MS", "Control"), ("Stroke", "Control"), ("MS", "Stroke"))
_ARM_ABBR = {"MS": "MS", "Stroke": "Str", "Control": "Ctl"}


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
    if isinstance(entry, str) and entry:
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


def _holm_adjust(rows):
    """Holm-Bonferroni across the contrasts sharing one (leg, parameter),
    written back as `holm_p`. Three pairwise tests on the same values are
    three chances to find a difference, so the raw p is optimistic; both are
    reported rather than replacing one with the other. Contrasts that could
    not be tested (an arm with <2 values) carry holm_p = None and are not
    counted in the family size."""
    testable = [r for r in rows if r.get("mann_whitney_p") is not None]
    m = len(testable)
    for r in rows:
        r["holm_p"] = None
    if not m:
        return
    running = 0.0
    for i, r in enumerate(sorted(testable, key=lambda x: x["mann_whitney_p"])):
        adj = min(1.0, (m - i) * r["mann_whitney_p"])
        running = max(running, adj)          # enforce monotonicity
        r["holm_p"] = round(running, 4)


def _median_iqr(vals):
    if not len(vals):
        return None, None
    q1, q3 = np.percentile(vals, [25, 75])
    return round(float(np.median(vals)), 4), round(float(q3 - q1), 4)


def compute_pairwise_stats(arm_summaries):
    """Per-(leg, parameter) pairwise contrasts across the three arms.

    arm_summaries: {"MS": {"left": [...], "right": [...]}, "Stroke": {...},
    "Control": {...}} of per-participant summary dicts. Returns one row per
    (leg, parameter, contrast) -- so 3x the rows of the two-arm version --
    each carrying both arms' median/IQR/n, Mann-Whitney p, Holm-adjusted p,
    Cliff's delta and its effect label. Untestable contrasts (either arm with
    <2 values, which Stroke will hit for a while at n=4) report medians and
    leave the test fields None/"n/a", never raising -- same contract as the
    two-arm version it replaces."""
    rows = []
    for leg in _LEGS:
        for key in _SCORE_KEYS:
            vals = {arm: np.array([s[key] for s in arm_summaries.get(arm, {}).get(leg, [])],
                                  dtype=float)
                    for arm in _ARMS}
            group = []
            for arm_a, arm_b in _CONTRASTS:
                a, b = vals[arm_a], vals[arm_b]
                a_med, a_iqr = _median_iqr(a)
                b_med, b_iqr = _median_iqr(b)
                row = {"leg": leg, "parameter": key,
                      "arm_a": arm_a, "arm_b": arm_b,
                      "n_a": len(a), "n_b": len(b),
                      "a_median": a_med, "a_iqr": a_iqr,
                      "b_median": b_med, "b_iqr": b_iqr}
                if len(a) >= 2 and len(b) >= 2:
                    _, p = mann_whitney(a, b)
                    d = cliffs_delta(a, b)
                    row["mann_whitney_p"] = round(p, 4)
                    row["cliffs_delta"] = round(d, 4)
                    row["effect_size"] = effect_label(d)
                else:
                    row["mann_whitney_p"] = row["cliffs_delta"] = None
                    row["effect_size"] = "n/a"
                group.append(row)
            _holm_adjust(group)
            rows.extend(group)
    return rows


# ══════════════════════════════════════════════════════════════════════════
# I/O
# ══════════════════════════════════════════════════════════════════════════

def load_registry():
    """Returns (dict, exists). Missing file -> ({}, False). Malformed JSON
    -> ({}, False) too (printed note), treated the same as missing rather
    than raising -- a corrupt registry shouldn't take down run_pt_analysis.py.
    Syntactically-valid JSON that isn't a dict (e.g. a hand-edit that turns
    the file into a bare list) is treated the same way, for the same reason."""
    if not os.path.isfile(REGISTRY_JSON):
        return {}, False
    try:
        with open(REGISTRY_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{REGISTRY_JSON} failed to parse -- treating as empty.")
        return {}, False
    if not isinstance(data, dict):
        print(f"{REGISTRY_JSON} is not a JSON object (dict) -- treating as empty.")
        return {}, False
    return data, True


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


def all_classified_pids():
    """Every discoverable participant classified as MS or Control, with NO
    trial-count gate -- unlike current_qualifying_participants(), which
    requires TRIAL_THRESHOLD on both legs before a participant counts
    toward the significance-test cohort. The normative min/max range
    (compute_normative_ranges) wants every scored trial from every
    diagnosed participant, since an envelope only gets more honest with
    more data -- a participant one trial short of the report-comparison
    threshold still has real swings worth including. Returns one list per
    arm in _ARMS, e.g. {"MS": [pids], "Stroke": [pids], "Control": [pids]}.

    Keyed off _ARMS rather than a literal dict so that adding a fourth arm
    cannot silently drop it here -- which is exactly what happened to Stroke
    between 2026-08-26 (when it became a full arm) and 2026-08-27."""
    registry, registry_exists = load_registry()
    result = {arm: [] for arm in _ARMS}
    for pid in common.list_participants().keys():
        diagnosis = load_metadata_diagnosis(pid)
        group, _source = classify_participant(pid, diagnosis, registry, registry_exists)
        if group in result:
            result[group].append(pid)
    return result


def compute_normative_ranges(ms_raw, control_raw):
    """Strict min-max envelope per (leg, parameter) across every
    individual scored trial -- not participant medians, since "high/low
    swings seen in healthy" describes individual swings, not per-person
    averages -- for both arms, plus whether the two envelopes overlap.
    ms_raw/control_raw: {"left": [...], "right": [...]} raw trial dicts,
    expected to come from the full all_classified_pids() set rather than
    the threshold-gated qualifying set used by compute_pairwise_stats."""
    rows = []
    for leg in _LEGS:
        ms_trials = ms_raw.get(leg, [])
        ctrl_trials = control_raw.get(leg, [])
        ms_pids = {t["participant_id"] for t in ms_trials}
        ctrl_pids = {t["participant_id"] for t in ctrl_trials}
        for key in _SCORE_KEYS:
            ms_vals = [t[key] for t in ms_trials]
            ctrl_vals = [t[key] for t in ctrl_trials]
            row = {
                "leg": leg, "parameter": key,
                "control_min": round(min(ctrl_vals), 4) if ctrl_vals else None,
                "control_max": round(max(ctrl_vals), 4) if ctrl_vals else None,
                "control_n_trials": len(ctrl_vals), "control_n_participants": len(ctrl_pids),
                "ms_min": round(min(ms_vals), 4) if ms_vals else None,
                "ms_max": round(max(ms_vals), 4) if ms_vals else None,
                "ms_n_trials": len(ms_vals), "ms_n_participants": len(ms_pids),
            }
            if ctrl_vals and ms_vals:
                row["overlap"] = not (row["control_max"] < row["ms_min"] or row["ms_max"] < row["control_min"])
            else:
                row["overlap"] = None
            rows.append(row)
    return rows


def write_ranges_csv(rows, out_path=None):
    out_path = out_path or RANGES_CSV
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["leg", "parameter", "control_min", "control_max", "control_n_trials",
                   "control_n_participants", "ms_min", "ms_max", "ms_n_trials",
                   "ms_n_participants", "overlap"])
        for row in rows:
            w.writerow([row["leg"], row["parameter"], row["control_min"], row["control_max"],
                       row["control_n_trials"], row["control_n_participants"], row["ms_min"],
                       row["ms_max"], row["ms_n_trials"], row["ms_n_participants"], row["overlap"]])
    print(f"-> {out_path}")


def _folder_hints_control(pid):
    """Best-effort cosmetic hint only -- NEVER used for classification.
    True if any trial path discovered for this participant has a
    condition string containing 'control' (case-insensitive), matching
    the legacy OptiTrack_Recordings/Participant_N_leg_control naming
    convention. Used only to decorate a no_entry warning line."""
    return any(r["participant"] == pid and "control" in r["condition"].lower()
              for r in common.discover_all_trials())


def _recordings_root_missing_or_empty():
    """True when REC_ROOT doesn't exist, or exists but has no
    Participant_* subdirectories -- i.e. metadata.json (the PRIMARY
    classification source, see classify_participant's priority order) is
    unavailable in this checkout. Recordings/ is gitignored, so this is a
    real, expected state for a fresh clone or an isolated worktree, not a
    hypothetical -- unlike participant_groups.json (which IS committed and
    already has its own registry_missing hint)."""
    if not os.path.isdir(REC_ROOT):
        return True
    return not any(os.path.isdir(p) for p in glob.glob(os.path.join(REC_ROOT, "Participant_*")))


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
        # Keyed on `source`, not a blanket `or`: when classification actually
        # resolved from metadata or the registry, raw_diagnosis must show
        # exactly that source's value -- never the other one -- so the
        # Excluded/Unclassified banner line reflects what was actually used
        # to classify this pid. Only in the fallthrough cases (no_entry --
        # metadata unrecognized and no registry entry resolved it either --
        # or registry_missing) do we fall back to "whichever diagnosis text
        # exists, for visibility", since neither source resolved there and
        # the unrecognized metadata string is the only thing worth surfacing.
        if source == "metadata":
            raw_diagnosis = metadata_diagnosis
        elif source == "registry":
            raw_diagnosis = registry.get(pid)
        else:  # no_entry or registry_missing
            raw_diagnosis = metadata_diagnosis or registry.get(pid)
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
    by_group = {"MS": [], "Stroke": [], "Control": [], "Excluded": [], "Unclassified": []}
    for row in rows:
        by_group.setdefault(row["group"], []).append(row)

    print("=" * 22 + " Cohort comparison " + "=" * 22)
    print("Secondary view: grouped by DIAGNOSIS. The primary stratification is "
          "by spasticity\n(generate_figures_by_spasticity.py). See this module's "
          "docstring for why.")
    if rows and _recordings_root_missing_or_empty():
        print("Note: Recordings/ is empty or absent in this checkout -- metadata.json diagnoses "
             "are unavailable, classification is registry-only until real data is present.")
    for arm in _ARMS:
        txt = ", ".join(r["pid"] for r in by_group[arm]) or "(none yet)"
        print(f"{arm + ':':<14}{txt}  (n={len(by_group[arm])})")

    excl_txt = ", ".join(f"{r['pid']} ({r['diagnosis']})" for r in by_group["Excluded"]) or "(none)"
    print(f"Excluded:     {excl_txt}  (n={len(by_group['Excluded'])})")

    no_entry = [r for r in by_group["Unclassified"] if r["source"] == "no_entry"]
    missing = [r for r in by_group["Unclassified"] if r["source"] == "registry_missing"]
    if no_entry:
        parts = []
        for r in no_entry:
            if r["diagnosis"]:
                # A diagnosis string exists but didn't resolve to a known
                # arm -- surface it by name (typo / unrecognized value),
                # rather than printing a bare pid that gives no clue why.
                parts.append(f"{r['pid']} (unrecognized diagnosis: {r['diagnosis']!r})")
            else:
                hint = " (folder suggests 'control')" if _folder_hints_control(r["pid"]) else ""
                parts.append(f"{r['pid']}{hint}")
        print(f"Unclassified: {', '.join(parts)}  (n={len(no_entry)}, no_entry -- add to participant_groups.json)")
    if missing:
        pids_txt = ", ".join(r["pid"] for r in missing)
        print(f"              registry_missing ({pids_txt}): participant_groups.json not found --")
        print("              if you're in a worktree/isolated checkout, copy it over.")
    print("=" * 63)


def _collect_arm_data(pids):
    """pids -> (summaries, raw_trials, contributing_pids, summaries_by_pid).
    summaries / raw_trials: {"left": [...], "right": [...]}. summaries holds
    one aggregate_participant_summary() dict per participant that had at
    least one scored trial for that leg (the statistical layer --
    compute_pairwise_stats and the figure's box/whiskers read only from
    this). raw_trials holds every individual scored trial record (the
    figure's descriptive-layer background jitter only -- never used for
    a statistic). contributing_pids is the post-filter participant set,
    which can be smaller than `pids` itself (see aggregate_participant_
    summary's None case, design spec §7.2 step 4). summaries_by_pid is
    {(pid, leg): summary|None} for every pid in `pids` -- needed by
    leg_cohort_reference() to compute a leave-one-out median when the
    report's own participant is a member of the arm being shown as their
    reference; the plain list in `summaries` has no pid attached to each
    entry, so it can't support excluding one participant."""
    summaries = {"left": [], "right": []}
    raw_trials = {"left": [], "right": []}
    contributing_pids = set()
    summaries_by_pid = {}
    for pid in pids:
        by_leg_tp, _ = common.collect_participant(pid)
        for leg in _LEGS:
            trials = [r for (leg_key, _cond), recs in by_leg_tp.items()
                     if leg_key == leg for r in recs]
            # Tag with the real participant id -- each trial record's own
            # "pid" field is actually "<pid>_<leg>_<condition>" (set by
            # score_trial from collect_participant's compound key), not
            # the bare id, so anything counting distinct participants from
            # raw trial dicts (compute_normative_ranges) needs this instead.
            for t in trials:
                t["participant_id"] = pid
            raw_trials[leg].extend(trials)
            summary = aggregate_participant_summary(trials)
            summaries_by_pid[(pid, leg)] = summary
            if summary is not None:
                summaries[leg].append(summary)
                contributing_pids.add(pid)
    return summaries, raw_trials, contributing_pids, summaries_by_pid


def write_contrasts_csv(contrast_rows, out_path):
    """Three-arm stats CSV: one row per (leg, parameter, contrast).

    cliffs_delta is signed b-minus-a for whichever pair the row names, so the
    header carries arm_a/arm_b rather than hardcoding "control_minus_ms" the
    way the two-arm writer could."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["leg", "parameter", "arm_a", "arm_b",
                   "n_a", "a_median", "a_iqr",
                   "n_b", "b_median", "b_iqr",
                   "mann_whitney_p", "holm_p",
                   "cliffs_delta_b_minus_a", "effect_size"])
        for r in contrast_rows:
            w.writerow([r["leg"], r["parameter"], r["arm_a"], r["arm_b"],
                       r["n_a"], r["a_median"], r["a_iqr"],
                       r["n_b"], r["b_median"], r["b_iqr"],
                       r["mann_whitney_p"], r["holm_p"],
                       r["cliffs_delta"], r["effect_size"]])
    print(f"-> {out_path}")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def build_cohort_snapshot():
    """I/O-bearing snapshot builder (reads participant_groups.json/
    metadata.json, calls discovery and collect_participant() -- writes
    nothing). Not a pure function in the no-I/O sense, only in the
    no-side-effects sense. Returns a single snapshot dict used by both
    per-participant reports (pt_report_common.make_report_figure(), via
    leg_cohort_reference()) and the end-of-run cohort artifacts
    (write_cohort_artifacts()), so the two never rescan independently and
    diverge within one run_pt_analysis.py invocation. Takes no arguments
    -- always recomputes the full qualifying set (design spec §6.1), broadened
    per-arm below so a participant short on trials for one leg still counts."""
    all_pids = all_classified_pids()
    # The composition/stats/range participant pool is the union of the
    # TRIAL_THRESHOLD-qualifying set (needed so Excluded/Unclassified
    # participants keep showing up exactly as before) and every classified
    # participant in ANY arm regardless of trial count -- a median or a
    # min/max envelope only gets more honest with more trials, unlike the
    # per-participant full report (run_pt_analysis.py's own, separate
    # TRIAL_THRESHOLD gate) which genuinely needs enough trials for a
    # meaningful release-alignment figure. So pids 6/7/10 (Control, short
    # one trial on one leg) and pid 4 (MS, same situation) now count
    # toward both the composition banner and the stats/range comparison.
    #
    # Unions over _ARMS, not a hardcoded MS|Control pair: when Stroke became
    # an arm this line still named only two, so a Stroke participant one
    # trial short would have been dropped from the cohort comparison while
    # an MS participant in the identical situation was kept.
    pids = current_qualifying_participants()
    for _arm in _ARMS:
        pids = pids | set(all_pids[_arm])
    rows = build_composition_rows(pids)
    n_excluded_unclassified = sum(1 for r in rows if r["group"] in ("Excluded", "Unclassified"))

    ms_pids = [r["pid"] for r in rows if r["group"] == "MS"]
    control_pids = [r["pid"] for r in rows if r["group"] == "Control"]
    stroke_pids = [r["pid"] for r in rows if r["group"] == "Stroke"]

    if not ms_pids or not control_pids:
        return {
            "composition_rows": rows, "ms_pids": ms_pids, "control_pids": control_pids,
            "stroke_pids": stroke_pids,
            "ms_summaries": None, "control_summaries": None, "stroke_summaries": None,
            "ms_raw": None, "control_raw": None, "stroke_raw": None,
            "summaries_by_pid": {},
            "ms_n_participants": None, "ms_n_trials": None,
            "control_n_participants": None, "control_n_trials": None,
            "stroke_n_participants": None, "stroke_n_trials": None,
            "contrast_rows": None,
            "n_excluded_unclassified": n_excluded_unclassified,
            "range_rows": [],
        }

    ms_summaries, ms_raw, ms_contrib, ms_by_pid = _collect_arm_data(ms_pids)
    control_summaries, control_raw, control_contrib, control_by_pid = _collect_arm_data(control_pids)
    # Stroke may legitimately be empty (no stroke participants recorded yet);
    # _collect_arm_data on an empty pid list yields empty legs, which
    # compute_pairwise_stats reports as untestable rather than treating as an
    # error -- so the two clinical arms alone still gate the comparison above.
    stroke_summaries, stroke_raw, stroke_contrib, stroke_by_pid = _collect_arm_data(stroke_pids)

    contrast_rows = compute_pairwise_stats({
        "MS": ms_summaries, "Stroke": stroke_summaries, "Control": control_summaries})
    # ms_pids/control_pids are already the broadened (threshold-free) set,
    # so ms_raw/control_raw double as the range computation's input too --
    # no separate collection pass needed.
    range_rows = compute_normative_ranges(ms_raw, control_raw)

    return {
        "composition_rows": rows, "ms_pids": ms_pids, "control_pids": control_pids,
        "stroke_pids": stroke_pids,
        "ms_summaries": ms_summaries, "control_summaries": control_summaries,
        "stroke_summaries": stroke_summaries,
        "ms_raw": ms_raw, "control_raw": control_raw, "stroke_raw": stroke_raw,
        "summaries_by_pid": {**ms_by_pid, **control_by_pid, **stroke_by_pid},
        "ms_n_participants": len(ms_contrib), "ms_n_trials": sum(len(v) for v in ms_raw.values()),
        "control_n_participants": len(control_contrib),
        "control_n_trials": sum(len(v) for v in control_raw.values()),
        "stroke_n_participants": len(stroke_contrib),
        "stroke_n_trials": sum(len(v) for v in stroke_raw.values()),
        "contrast_rows": contrast_rows,
        "n_excluded_unclassified": n_excluded_unclassified,
        "range_rows": range_rows,
    }


def write_cohort_artifacts(snapshot):
    """Writes cohort_composition.csv and normative_ranges.csv (always --
    the range comes from all_classified_pids(), independent of the
    threshold-gated qualifying set), and cohort_stats.csv /
    cohort_boxplots.png (only when both clinical-vs-control arms have >=1 qualifying
    participant) from an already-built snapshot -- zero rediscovery, zero
    recollection. Renamed from today's run_cohort_comparison(), which now
    only writes artifacts from a snapshot instead of recomputing one."""
    write_composition_csv(snapshot["composition_rows"])
    print_composition_banner(snapshot["composition_rows"])
    write_ranges_csv(snapshot["range_rows"], RANGES_CSV)

    if not snapshot["ms_pids"] or not snapshot["control_pids"]:
        print(f"Cohort comparison skipped: {len(snapshot['ms_pids'])} MS / "
             f"{len(snapshot['control_pids'])} Control qualifying participants "
             f"(need >=1 in each arm).")
        return

    write_contrasts_csv(snapshot["contrast_rows"], STATS_CSV)
    make_cohort_comparison_figure(
        snapshot["ms_summaries"], snapshot["ms_raw"],
        snapshot["ms_n_participants"], snapshot["ms_n_trials"],
        snapshot["control_summaries"], snapshot["control_raw"],
        snapshot["control_n_participants"], snapshot["control_n_trials"],
        snapshot["n_excluded_unclassified"], FIGURE_PNG, snapshot["contrast_rows"],
        snapshot["range_rows"],
        stroke_summaries=snapshot.get("stroke_summaries"),
        stroke_raw=snapshot.get("stroke_raw"),
        stroke_n_participants=snapshot.get("stroke_n_participants"),
        stroke_n_trials=snapshot.get("stroke_n_trials"))


def run_cohort_comparison():
    """Back-compat combinator: build_cohort_snapshot() + write_cohort_artifacts().
    Kept so every existing caller/test that calls run_cohort_comparison()
    directly keeps working unchanged. run_pt_analysis.py's main() (Task 13
    of the implementation plan) calls the two halves directly instead, so
    it can also pass the snapshot into per-participant reports before the
    artifacts are written."""
    write_cohort_artifacts(build_cohort_snapshot())


def leg_cohort_reference(snapshot, participant_id, leg):
    """{"ms_median", "ms_n", "control_median", "control_n",
    "leave_one_out_arm"} for one leg, using leave-one-out on whichever arm
    `participant_id` itself belongs to (small cohorts make an inclusive
    median partially self-referential -- design spec §5.6). Returns None
    when the snapshot has no comparison at all (either arm empty)."""
    if not snapshot["ms_pids"] or not snapshot["control_pids"]:
        return None

    def _median_excluding(arm_pids, exclude_pid):
        vals = [snapshot["summaries_by_pid"][(pid, leg)]["pt7"]
                for pid in arm_pids
                if pid != exclude_pid and snapshot["summaries_by_pid"].get((pid, leg)) is not None]
        return (float(np.median(vals)) if vals else None), len(vals)

    stroke_pids = snapshot.get("stroke_pids") or []
    is_ms = participant_id in snapshot["ms_pids"]
    is_control = participant_id in snapshot["control_pids"]
    is_stroke = participant_id in stroke_pids
    ms_median, ms_n = _median_excluding(snapshot["ms_pids"], participant_id if is_ms else None)
    control_median, control_n = _median_excluding(snapshot["control_pids"], participant_id if is_control else None)
    # Stroke joined as a full arm on 2026-08-26. Reported as None/0 rather
    # than omitted when the arm is empty, so callers can render it uniformly
    # without branching on whether any stroke participant exists yet.
    stroke_median, stroke_n = _median_excluding(stroke_pids, participant_id if is_stroke else None)
    return {"ms_median": ms_median, "ms_n": ms_n,
           "control_median": control_median, "control_n": control_n,
           "stroke_median": stroke_median, "stroke_n": stroke_n,
           "leave_one_out_arm": ("MS" if is_ms else
                                 ("Stroke" if is_stroke else
                                  ("Control" if is_control else None)))}


# ══════════════════════════════════════════════════════════════════════════
# Plotting
# ══════════════════════════════════════════════════════════════════════════

def make_cohort_comparison_figure(ms_summaries, ms_raw, ms_n_participants, ms_n_trials,
                                  control_summaries, control_raw, control_n_participants,
                                  control_n_trials, n_excluded_unclassified, out_path, contrast_rows,
                                  range_rows=(), stroke_summaries=None, stroke_raw=None,
                                  stroke_n_participants=None, stroke_n_trials=None):
    """Light/clinical style matching pt_report_common.py (white background,
    same color conventions) -- NOT the dark dashboard style of the older
    ms_vs_healthy_analysis.py, so every figure run_pt_analysis.py produces
    reads as one visual system (design spec §7.3).

    Two point layers per box, deliberately: the box/whiskers are built
    from ms_summaries/control_summaries (one median per participant --
    the statistical layer compute_pairwise_stats also reads from, avoiding
    pseudoreplication). ms_raw/control_raw (every individual scored
    trial) are drawn underneath as lighter background jitter for
    descriptive transparency only -- never used for a statistic.

    contrast_rows: compute_pairwise_stats() output, used to annotate each
    subplot with its Mann-Whitney p / Cliff's delta (spec §7.3); also used
    to zone-shade the pt7 column the same way pt_report_common.py's
    make_report_figure does, since pt7 (unlike the raw params) has a
    clinically meaningful healthy/borderline/impaired scale.

    range_rows: compute_normative_ranges() output (from the fuller
    all_classified_pids() pool, not the ms_raw/control_raw drawn above) --
    drawn as dashed min/max envelope lines on every column, since this is
    where "what's the high/low swing seen in healthy" actually shows up
    visually. Deliberately a different pid pool than the box/jitter layers
    above, so the envelope can be more inclusive than the significance
    tests without silently changing what the boxplots themselves show."""
    ms_color = common.COLORS["red"]
    stroke_color = common.COLORS["purple"]
    control_color = common.COLORS["green"]
    # Stroke sits between MS and Control so the two clinical arms are adjacent
    # and Control anchors the right-hand edge as the reference in every panel.
    has_stroke = bool(stroke_summaries) and any(stroke_summaries.get(l) for l in _LEGS)
    n_cols = len(_SCORE_KEYS)
    pt7_col_idx = len(_SCORE_KEYS) - 1
    contrast_by_leg_key = {}
    for r in contrast_rows:
        contrast_by_leg_key.setdefault((r["leg"], r["parameter"]), []).append(r)
    range_by_leg_key = {(r["leg"], r["parameter"]): r for r in range_rows}
    fig, axes = plt.subplots(2, n_cols, figsize=(3.2 * n_cols, 8), facecolor="white")
    rng = np.random.RandomState(13)

    for row_idx, leg in enumerate(_LEGS):
        for col_idx, key in enumerate(_SCORE_KEYS):
            ax = axes[row_idx, col_idx]
            ax.set_facecolor("#f8f9fa")
            ax.grid(True, color=common.BG_GRID, linestyle="-", linewidth=0.8, axis="y")

            ms_med = [s[key] for s in ms_summaries[leg]]
            ctrl_med = [s[key] for s in control_summaries[leg]]
            stroke_med = ([s[key] for s in (stroke_summaries or {}).get(leg, [])]
                          if has_stroke else [])
            arm_series = ([(ms_med, ms_color, "MS")] +
                          ([(stroke_med, stroke_color, "Stroke")] if has_stroke else []) +
                          [(ctrl_med, control_color, "Control")])
            range_row = range_by_leg_key.get((leg, key))
            range_bounds = [v for v in (
                (range_row or {}).get("control_min"), (range_row or {}).get("control_max"),
                (range_row or {}).get("ms_min"), (range_row or {}).get("ms_max")) if v is not None]

            if col_idx == pt7_col_idx:
                # Zone shading, same convention as pt_report_common.py's
                # make_report_figure -- healthy/borderline/impaired bands
                # only make sense for the 7-parameter composite score, not
                # the raw individual params in the other columns.
                zone_vals = (ms_med + ctrl_med + [t[key] for t in ms_raw[leg]]
                            + [t[key] for t in control_raw[leg]] + range_bounds)
                y_max = (max(zone_vals) if zone_vals else 1.6) * 1.15
                for (lo, hi), zcolor in zip(zip(common.ZONE_EDGES[:-1], common.ZONE_EDGES[1:]),
                                           common.ZONE_COLORS):
                    ax.axhspan(lo, min(hi, y_max), facecolor=zcolor, alpha=0.4, zorder=0)
                ax.set_ylim(0, y_max)

            bp = ax.boxplot([v for v, _, _ in arm_series],
                            positions=list(range(len(arm_series))), widths=0.4,
                            patch_artist=True, showfliers=False)
            # Colour every box from arm_series, so the box, its background
            # jitter and its participant medians all sit at the same x and
            # share one colour. Hardcoding indices 0/1 here (as this did while
            # there were only two arms) silently painted Stroke with Control's
            # colour and drew Control's jitter over Stroke's box.
            for _bi, (_v, _c, _n) in enumerate(arm_series):
                bp["boxes"][_bi].set_facecolor(_c)
                bp["boxes"][_bi].set_alpha(0.5)

            raw_by_arm = {"MS": ms_raw, "Stroke": stroke_raw or {}, "Control": control_raw}
            for _pos, (_med_vals, _c, _name) in enumerate(arm_series):
                _raw_vals = [t[key] for t in raw_by_arm.get(_name, {}).get(leg, [])]
                if _raw_vals:
                    ax.scatter(_pos + rng.uniform(-0.08, 0.08, len(_raw_vals)), _raw_vals,
                              color=_c, s=10, alpha=0.25, zorder=2)
                if _med_vals:
                    ax.scatter(_pos + rng.uniform(-0.05, 0.05, len(_med_vals)), _med_vals,
                              color=_c, s=40, alpha=0.9, zorder=4,
                              edgecolors="#333333", linewidths=0.5)

            # Normative min/max envelope (all_classified_pids() pool, see
            # range_rows docstring above) -- dashed rather than a filled
            # span so it reads as a boundary marker without fighting the
            # pt7 column's zone shading or the box/jitter colors.
            if range_row:
                for bound in (range_row["control_min"], range_row["control_max"]):
                    if bound is not None:
                        ax.axhline(bound, color=control_color, linestyle="--", linewidth=1,
                                   alpha=0.7, zorder=3)
                for bound in (range_row["ms_min"], range_row["ms_max"]):
                    if bound is not None:
                        ax.axhline(bound, color=ms_color, linestyle="--", linewidth=1,
                                   alpha=0.7, zorder=3)

            ax.set_xticks([0, 1])
            ax.set_xticks(list(range(len(arm_series))))
            ax.set_xticklabels([n for _, _, n in arm_series], fontsize=8)
            ax.set_title(key, fontsize=9, fontweight="bold")
            ax.tick_params(labelsize=7)

            # One line per contrast. Annotating a three-box panel with only
            # the MS-vs-Control p (what this did while the figure was two-arm)
            # silently attributed one contrast's result to the whole panel.
            # "*" marks a contrast still significant after Holm correction.
            shown_arms = [n for _, _, n in arm_series]
            panel_rows = [r for r in contrast_by_leg_key.get((leg, key), [])
                          if r["arm_a"] in shown_arms and r["arm_b"] in shown_arms]
            ann_lines = []
            for r in panel_rows:
                tag = f"{_ARM_ABBR[r['arm_a']]}-{_ARM_ABBR[r['arm_b']]}"
                if r.get("mann_whitney_p") is None:
                    ann_lines.append(f"{tag} n/a")
                    continue
                star = "*" if (r.get("holm_p") is not None and r["holm_p"] < 0.05) else ""
                ann_lines.append(f"{tag} p={r['mann_whitney_p']:.3f}{star}")
            annotation = "\n".join(ann_lines) if ann_lines else "n/a (n<2)"
            ax.text(0.5, 0.985, annotation, transform=ax.transAxes, fontsize=6,
                    ha="center", va="top", color="#555555", zorder=5, linespacing=1.35)

    for row_idx, leg_label in enumerate(("Left leg", "Right leg")):
        axes[row_idx, 0].set_ylabel(leg_label, fontsize=10, fontweight="bold")

    excl_txt = f" · {n_excluded_unclassified} excluded/unclassified" if n_excluded_unclassified else ""
    arm_counts = [f"MS n={ms_n_participants} participants ({ms_n_trials} trials)"]
    if has_stroke:
        arm_counts.append(
            f"Stroke n={stroke_n_participants} participants ({stroke_n_trials} trials)")
    arm_counts.append(
        f"Control n={control_n_participants} participants ({control_n_trials} trials)")
    title_arms = " vs ".join(n for _, _, n in
                             ([("", "", "MS")] +
                              ([("", "", "Stroke")] if has_stroke else []) +
                              [("", "", "Control")]))
    # The secondary-view label belongs ON THE FIGURE, not only in the module
    # docstring: whoever opens this PNG in six months will not have read the
    # source, and a diagnosis-grouped result presented bare invites being taken
    # for the headline. Spasticity grouping is the primary stratification.
    fig.suptitle(
        f"{title_arms} — Pendulum Test Parameters (7-parameter Popovic PT score)\n"
        + " · ".join(arm_counts)
        + f"{excl_txt} · see cohort_composition.csv\n"
        + "SECONDARY VIEW — grouped by diagnosis. Primary stratification is by "
          "spasticity (figures_by_spasticity).",
        fontsize=11, y=1.02, color="#333333")
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out_path}")
