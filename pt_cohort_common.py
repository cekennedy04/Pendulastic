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
import matplotlib.pyplot as plt

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
    by_group = {"MS": [], "Control": [], "Excluded": [], "Unclassified": []}
    for row in rows:
        by_group[row["group"]].append(row)

    print("=" * 20 + " MS vs Control cohort " + "=" * 20)
    if rows and _recordings_root_missing_or_empty():
        print("Note: Recordings/ is empty or absent in this checkout -- metadata.json diagnoses "
             "are unavailable, classification is registry-only until real data is present.")
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
    compute_cohort_stats and the figure's box/whiskers read only from
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
            raw_trials[leg].extend(trials)
            summary = aggregate_participant_summary(trials)
            summaries_by_pid[(pid, leg)] = summary
            if summary is not None:
                summaries[leg].append(summary)
                contributing_pids.add(pid)
    return summaries, raw_trials, contributing_pids, summaries_by_pid


def write_stats_csv(stats_rows, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # Display-only rename: the dict key stays "cliffs_delta" internally
        # (see cliffs_delta()'s docstring / compute_cohort_stats). This
        # header records the sign convention -- positive means Control
        # exceeds MS -- so a reader doesn't have to go read source to
        # interpret a value like 0.83.
        w.writerow(["leg", "parameter", "ms_n", "ms_median", "ms_iqr",
                   "control_n", "control_median", "control_iqr",
                   "mann_whitney_p", "cliffs_delta_control_minus_ms", "effect_size"])
        for row in stats_rows:
            w.writerow([row["leg"], row["parameter"], row["n_ms"], row["ms_median"], row["ms_iqr"],
                       row["n_control"], row["control_median"], row["control_iqr"],
                       row["mann_whitney_p"], row["cliffs_delta"], row["effect_size"]])
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
    -- always recomputes the full qualifying set (design spec §6.1)."""
    pids = current_qualifying_participants()
    rows = build_composition_rows(pids)
    n_excluded_unclassified = sum(1 for r in rows if r["group"] in ("Excluded", "Unclassified"))

    ms_pids = [r["pid"] for r in rows if r["group"] == "MS"]
    control_pids = [r["pid"] for r in rows if r["group"] == "Control"]

    if not ms_pids or not control_pids:
        return {
            "composition_rows": rows, "ms_pids": ms_pids, "control_pids": control_pids,
            "ms_summaries": None, "control_summaries": None,
            "ms_raw": None, "control_raw": None, "summaries_by_pid": {},
            "ms_n_participants": None, "ms_n_trials": None,
            "control_n_participants": None, "control_n_trials": None,
            "stats_rows": None, "n_excluded_unclassified": n_excluded_unclassified,
        }

    ms_summaries, ms_raw, ms_contrib, ms_by_pid = _collect_arm_data(ms_pids)
    control_summaries, control_raw, control_contrib, control_by_pid = _collect_arm_data(control_pids)
    stats_rows = compute_cohort_stats(ms_summaries, control_summaries)

    return {
        "composition_rows": rows, "ms_pids": ms_pids, "control_pids": control_pids,
        "ms_summaries": ms_summaries, "control_summaries": control_summaries,
        "ms_raw": ms_raw, "control_raw": control_raw,
        "summaries_by_pid": {**ms_by_pid, **control_by_pid},
        "ms_n_participants": len(ms_contrib), "ms_n_trials": sum(len(v) for v in ms_raw.values()),
        "control_n_participants": len(control_contrib),
        "control_n_trials": sum(len(v) for v in control_raw.values()),
        "stats_rows": stats_rows, "n_excluded_unclassified": n_excluded_unclassified,
    }


def write_cohort_artifacts(snapshot):
    """Writes cohort_composition.csv (always), and ms_vs_control_stats.csv
    / ms_vs_control_boxplots.png (only when both arms are non-empty) from
    an already-built snapshot -- zero rediscovery, zero recollection.
    Renamed from today's run_cohort_comparison(), which now only writes
    artifacts from a snapshot instead of recomputing one."""
    write_composition_csv(snapshot["composition_rows"])
    print_composition_banner(snapshot["composition_rows"])

    if not snapshot["ms_pids"] or not snapshot["control_pids"]:
        print(f"Cohort comparison skipped: {len(snapshot['ms_pids'])} MS / "
             f"{len(snapshot['control_pids'])} Control qualifying participants "
             f"(need >=1 in each arm).")
        return

    write_stats_csv(snapshot["stats_rows"], STATS_CSV)
    make_cohort_comparison_figure(
        snapshot["ms_summaries"], snapshot["ms_raw"],
        snapshot["ms_n_participants"], snapshot["ms_n_trials"],
        snapshot["control_summaries"], snapshot["control_raw"],
        snapshot["control_n_participants"], snapshot["control_n_trials"],
        snapshot["n_excluded_unclassified"], FIGURE_PNG, snapshot["stats_rows"])


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

    is_ms = participant_id in snapshot["ms_pids"]
    is_control = participant_id in snapshot["control_pids"]
    ms_median, ms_n = _median_excluding(snapshot["ms_pids"], participant_id if is_ms else None)
    control_median, control_n = _median_excluding(snapshot["control_pids"], participant_id if is_control else None)
    return {"ms_median": ms_median, "ms_n": ms_n,
           "control_median": control_median, "control_n": control_n,
           "leave_one_out_arm": "MS" if is_ms else ("Control" if is_control else None)}


# ══════════════════════════════════════════════════════════════════════════
# Plotting
# ══════════════════════════════════════════════════════════════════════════

def make_cohort_comparison_figure(ms_summaries, ms_raw, ms_n_participants, ms_n_trials,
                                  control_summaries, control_raw, control_n_participants,
                                  control_n_trials, n_excluded_unclassified, out_path, stats_rows):
    """Light/clinical style matching pt_report_common.py (white background,
    same color conventions) -- NOT the dark dashboard style of the older
    ms_vs_healthy_analysis.py, so every figure run_pt_analysis.py produces
    reads as one visual system (design spec §7.3).

    Two point layers per box, deliberately: the box/whiskers are built
    from ms_summaries/control_summaries (one median per participant --
    the statistical layer compute_cohort_stats also reads from, avoiding
    pseudoreplication). ms_raw/control_raw (every individual scored
    trial) are drawn underneath as lighter background jitter for
    descriptive transparency only -- never used for a statistic.

    stats_rows: compute_cohort_stats() output, used to annotate each
    subplot with its Mann-Whitney p / Cliff's delta (spec §7.3); also used
    to zone-shade the pt7 column the same way pt_report_common.py's
    make_report_figure does, since pt7 (unlike the raw params) has a
    clinically meaningful healthy/borderline/impaired scale."""
    ms_color = common.COLORS["red"]
    control_color = common.COLORS["green"]
    n_cols = len(_SCORE_KEYS)
    pt7_col_idx = len(_SCORE_KEYS) - 1
    stats_by_leg_key = {(r["leg"], r["parameter"]): r for r in stats_rows}
    fig, axes = plt.subplots(2, n_cols, figsize=(3.2 * n_cols, 8), facecolor="white")
    rng = np.random.RandomState(13)

    for row_idx, leg in enumerate(_LEGS):
        for col_idx, key in enumerate(_SCORE_KEYS):
            ax = axes[row_idx, col_idx]
            ax.set_facecolor("#f8f9fa")
            ax.grid(True, color=common.BG_GRID, linestyle="-", linewidth=0.8, axis="y")

            ms_med = [s[key] for s in ms_summaries[leg]]
            ctrl_med = [s[key] for s in control_summaries[leg]]

            if col_idx == pt7_col_idx:
                # Zone shading, same convention as pt_report_common.py's
                # make_report_figure -- healthy/borderline/impaired bands
                # only make sense for the 7-parameter composite score, not
                # the raw individual params in the other columns.
                zone_vals = (ms_med + ctrl_med + [t[key] for t in ms_raw[leg]]
                            + [t[key] for t in control_raw[leg]])
                y_max = (max(zone_vals) if zone_vals else 1.6) * 1.15
                for (lo, hi), zcolor in zip(zip(common.ZONE_EDGES[:-1], common.ZONE_EDGES[1:]),
                                           common.ZONE_COLORS):
                    ax.axhspan(lo, min(hi, y_max), facecolor=zcolor, alpha=0.4, zorder=0)
                ax.set_ylim(0, y_max)

            bp = ax.boxplot([ms_med, ctrl_med], positions=[0, 1], widths=0.4,
                            patch_artist=True, showfliers=False)
            bp["boxes"][0].set_facecolor(ms_color)
            bp["boxes"][0].set_alpha(0.5)
            bp["boxes"][1].set_facecolor(control_color)
            bp["boxes"][1].set_alpha(0.5)

            ms_raw_vals = [t[key] for t in ms_raw[leg]]
            ctrl_raw_vals = [t[key] for t in control_raw[leg]]
            if ms_raw_vals:
                ax.scatter(rng.uniform(-0.08, 0.08, len(ms_raw_vals)), ms_raw_vals,
                          color=ms_color, s=10, alpha=0.25, zorder=2)
            if ctrl_raw_vals:
                ax.scatter(1 + rng.uniform(-0.08, 0.08, len(ctrl_raw_vals)), ctrl_raw_vals,
                          color=control_color, s=10, alpha=0.25, zorder=2)
            if ms_med:
                ax.scatter(rng.uniform(-0.05, 0.05, len(ms_med)), ms_med, color=ms_color,
                          s=40, alpha=0.9, zorder=4, edgecolors="#333333", linewidths=0.5)
            if ctrl_med:
                ax.scatter(1 + rng.uniform(-0.05, 0.05, len(ctrl_med)), ctrl_med, color=control_color,
                          s=40, alpha=0.9, zorder=4, edgecolors="#333333", linewidths=0.5)

            ax.set_xticks([0, 1])
            ax.set_xticklabels(["MS", "Control"], fontsize=8)
            ax.set_title(key, fontsize=9, fontweight="bold")
            ax.tick_params(labelsize=7)

            stat_row = stats_by_leg_key.get((leg, key))
            if stat_row and stat_row.get("mann_whitney_p") is not None \
                    and stat_row.get("cliffs_delta") is not None:
                annotation = f"p={stat_row['mann_whitney_p']:.3f}, δ={stat_row['cliffs_delta']:+.2f}"
            else:
                annotation = "n/a (n<2)"
            ax.text(0.5, 0.98, annotation, transform=ax.transAxes, fontsize=7,
                    ha="center", va="top", color="#555555", zorder=5)

    for row_idx, leg_label in enumerate(("Left leg", "Right leg")):
        axes[row_idx, 0].set_ylabel(leg_label, fontsize=10, fontweight="bold")

    excl_txt = f" · {n_excluded_unclassified} excluded/unclassified" if n_excluded_unclassified else ""
    fig.suptitle(
        "MS vs Control — Pendulum Test Parameters (7-parameter Popovic PT score)\n"
        f"MS n={ms_n_participants} participants ({ms_n_trials} trials) · "
        f"Control n={control_n_participants} participants ({control_n_trials} trials)"
        f"{excl_txt} · see cohort_composition.csv",
        fontsize=11, y=1.02, color="#333333")
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out_path}")
