"""
generate_figures_by_spasticity.py
=================================
Stratify the PT parameters by SPASTICITY rather than by diagnosis, in parallel
with the existing paper figures rather than in place of them.

Why this is a separate script
-----------------------------
The published figure generators (generate_paper_results_analysis.py and the
three that import from it) stay locked to their original ("Control", "MS")
grouping on purpose. Rewiring them while data-integrity work is still in flight
would make it impossible to tell whether a shift in a figure came from a data
fix or from the regrouping. This script is the parallel track: same underlying
trials, different stratification, its own outputs.

Grouping unit is the LEG
------------------------
MAS is assessed per leg and the PT parameters are computed per leg, so a leg is
the unit. P4 is the case that forces it: MAS 0 on the left, 1+ on the right.
Rolling that participant up to one label would throw away the contrast the
pendulum test is supposed to see.

The circularity caveat, and what this script does about it
----------------------------------------------------------
Some spasticity labels are DERIVED from A0, the swing amplitude. A0 is not one
of the seven scored PT parameters, but it is not independent of them either --
several are normalised on it. So a "PT parameters differ by spasticity group"
result computed over all legs is partly circular for the derived subset.

Every comparison is therefore run twice:

  * ALL       -- every labelled leg, derived labels included.
  * CLINICAL  -- only legs whose label came from a clinician (an overall MAS
                 grade, or the flexion/extension components when the overall
                 is still pending). No circularity, much smaller n.

A finding that holds in CLINICAL is a finding. A finding that appears only in
ALL is a hypothesis about the proxy, not about spasticity. The two are printed
side by side so the difference cannot be missed.

Outputs land in Model_Analysis_Outputs/Spasticity_Stratified/.
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
import warnings

warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import pt_cohort_common as pcc
import pt_report_common as common
import pendulastic_pt_score as pts
import spasticity_grouping as sg
import data_purpose as dp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "Spasticity_Stratified")
LEGS_CSV = os.path.join(OUT_DIR, "leg_labels.csv")
STATS_CSV = os.path.join(OUT_DIR, "spasticity_stats.csv")
FIGURE_PNG = os.path.join(OUT_DIR, "spasticity_boxplots.png")

# The seven scored parameters, plus A0 so the amplitude the derived labels lean
# on is visible rather than implicit, and pt7 for continuity with the other
# reports.
PARAMS = list(pcc._PARAM_KEYS) + ["pt7", "A0_deg"]

# Label sources that came from a clinician rather than from the swing.
CLINICAL_SOURCES = (sg.SRC_CLINICAL, sg.SRC_CLINICAL_COMPONENT)


def a0_by_leg_from_optitrack():
    """{(pid, leg): median A0_deg} over each leg's pre-condition trials."""
    import glob
    per_leg = {}
    pattern = os.path.join(BASE_DIR, "OptiTrack_Recordings", "Participant_*", "*",
                           "pre", "**", "trial_*_optitrack.csv")
    for path in glob.glob(pattern, recursive=True):
        parts = path.replace("\\", "/").split("/")
        try:
            pid = [p for p in parts if p.startswith("Participant_")][0]
            pid = pid.replace("Participant_", "")
            leg = parts[parts.index("Participant_" + pid) + 1].lower()
        except (IndexError, ValueError):
            continue
        try:
            t, angle, _q = pts.load_optitrack_detailed(path)
            val = pts.compute_pt_params(t, angle).get("A0_deg")
        except Exception:
            continue
        if val is not None and float(val) == float(val):
            per_leg.setdefault((pid, leg), []).append(float(val))
    return {k: statistics.median(v) for k, v in per_leg.items() if v}


# Folder names under Recordings/ that are not real participants.
NON_PARTICIPANT_IDS = {"test", "0", "demo"}


def participant_roster():
    """Every real participant id, from BOTH discovery paths.

    pt_report_common.list_participants() is built from discovered OptiTrack
    trials, so a participant whose OptiTrack export is missing or empty never
    appears -- P17's Left/ and Right/ folders exist but hold no CSVs, so it
    drops out silently. Union it with the Recordings/ metadata folders, which
    is where a participant is registered regardless of which modality survived.
    """
    ids = set(common.list_participants().keys())
    rec_root = os.path.join(BASE_DIR, "Recordings")
    if os.path.isdir(rec_root):
        for name in os.listdir(rec_root):
            if not name.startswith("Participant_"):
                continue
            pid = name.replace("Participant_", "").strip()
            if pid.lower() in NON_PARTICIPANT_IDS:
                continue
            if os.path.isfile(os.path.join(rec_root, name, "metadata.json")):
                ids.add(pid)
    return sorted(ids, key=lambda x: int(x) if x.isdigit() else 10 ** 6)


def label_every_leg(param_conditions):
    """{(pid, leg): SpasticityLabel} for BOTH legs of every real participant.

    Enumerated from the participant roster and LEGS, never from whatever data
    happens to exist, so a leg with no recordings shows up as UNKNOWN instead
    of vanishing -- P7 left, P17 both, P22 left are the live cases.
    """
    registry, registry_exists = pcc.load_registry()
    mas_all = sg.load_mas_all_conditions()
    comp_all = sg.load_mas_components_all_conditions()
    a0 = a0_by_leg_from_optitrack()

    out = {}
    for (pid, leg), (condition, _vals) in sorted(param_conditions.items()):
        arm, _src = pcc.classify_participant(
            pid, pcc.load_metadata_diagnosis(pid), registry, registry_exists)
        # The label and the parameters must come from the SAME session.
        legs = sg.classify_participant_legs(
            pid, arm=arm,
            mas_by_leg=sg.for_condition(mas_all, condition),
            mas_components=sg.for_condition(comp_all, condition),
            a0_by_leg={lg: a0.get((pid, lg)) for lg in sg.LEGS})
        out[(pid, leg)] = legs[leg]

    # Legs with no parameters at all still need a label, from whichever
    # condition has one, so they stay visible rather than vanishing.
    for pid in participant_roster():
        arm, _src = pcc.classify_participant(
            pid, pcc.load_metadata_diagnosis(pid), registry, registry_exists)
        for leg in sg.LEGS:
            if (pid, leg) in out:
                continue
            conds = sorted({c for (p2, l2, c) in list(mas_all) + list(comp_all)
                            if p2 == pid and l2 == leg}) or [""]
            legs = sg.classify_participant_legs(
                pid, arm=arm,
                mas_by_leg=sg.for_condition(mas_all, conds[0]),
                mas_components=sg.for_condition(comp_all, conds[0]),
                a0_by_leg={lg: a0.get((pid, lg)) for lg in sg.LEGS})
            out[(pid, leg)] = legs[leg]
    return out


MODALITY = "_modality"
CONDITION = "_condition"


def _medians_of(records):
    vals = {}
    for key in PARAMS:
        got = [r[key] for r in records
               if isinstance(r, dict) and r.get(key) is not None
               and float(r[key]) == float(r[key])]
        if got:
            vals[key] = float(np.median(got))
    return vals


def imu_leg_params(pid, leg):
    """PT parameters for one leg from its IMU recordings, or {} if there are none.

    The recovery path for a leg whose OptiTrack export is missing or empty.
    P17 is the case: both legs have a full IMU component set (accel/gyro/mag
    plus the raw jsonl) while OptiTrack_Recordings/Participant_17/{Left,Right}/pre
    are empty directories.

    Tagged with its modality and NEVER pooled into the OptiTrack comparison --
    IMU A0 runs a median +20.4 deg above OptiTrack A0 on the same leg (n=16,
    sd 8.4), so mixing the two would put a systematic offset straight into the
    group difference this script is measuring.
    """
    import glob
    import batch_imu_vs_optitrack_rmse as batch
    import workbench_engine as engine

    pattern = os.path.join(BASE_DIR, "Recordings", f"Participant_{pid}",
                           leg.capitalize(), "pre", "**", "Trial_*_imu.csv")
    records = []
    for imu_path in sorted(glob.glob(pattern, recursive=True)):
        try:
            paths = batch.derive_component_paths(imu_path)
            vals = {k: engine.validate_component_csv(v, k)
                    for k, v in paths.items() if v and os.path.exists(v)}
            res = engine.load_imu_trial_from_components(vals)
            t = np.asarray(res[0], dtype=float)
            angle = np.asarray(res[1], dtype=float)
            params = pts.compute_pt_params(t, angle)
        except Exception:
            continue
        if not params:
            continue
        rec = dict(params)
        rec["pt7"] = pts.compute_pt_score(params)
        records.append(rec)
    return _medians_of(records) if records else {}


def leg_param_medians():
    """{(pid, leg): {param: median, _modality: "optitrack"|"imu"}}.

    OptiTrack is preferred wherever it reconstructs. IMU fills in only for a
    leg OptiTrack cannot supply at all, and carries a modality tag so the
    caller can keep the two apart.
    """
    out = {}
    for pid in participant_roster():
        try:
            # collect_participant returns (by_leg_tp, timepoints); the first
            # element is {(leg, condition): [trial_records]}.
            by_leg_tp, _timepoints = common.collect_participant(pid)
        except Exception:
            by_leg_tp = {}
        # The BASELINE condition, taken as the participant's chronologically
        # first timepoint rather than by matching the name "pre". Condition
        # names are not standardised -- P16's baseline is called "control" and
        # P2's are "pre_duo"/"pre_solo" -- so a startswith("pre") filter
        # silently dropped P16 entirely and read as "P16 has no data".
        baseline = _timepoints[0][0] if _timepoints else None
        for (leg, condition), trials in by_leg_tp.items():
            if condition != baseline or not trials:
                continue
            vals = _medians_of(trials)
            if vals:
                vals[MODALITY] = "optitrack"
                vals[CONDITION] = str(condition)
                out[(pid, str(leg).lower())] = vals

        for leg in sg.LEGS:
            if out.get((pid, leg)):
                continue
            vals = imu_leg_params(pid, leg)
            if vals:
                vals[MODALITY] = "imu"
                vals[CONDITION] = "pre"
                out[(pid, leg)] = vals
    return out


def compare(groups, param):
    """Mann-Whitney + Cliff's delta for one parameter across two label groups."""
    a = [v[param] for v in groups[sg.NON_SPASTIC] if param in v]
    b = [v[param] for v in groups[sg.SPASTIC] if param in v]
    stat, p = pcc.mann_whitney(a, b)
    d = pcc.cliffs_delta(a, b)
    return {
        "parameter": param,
        "n_non_spastic": len(a), "n_spastic": len(b),
        "median_non_spastic": round(float(np.median(a)), 4) if a else None,
        "median_spastic": round(float(np.median(b)), 4) if b else None,
        "mann_whitney_u": None if stat != stat else round(stat, 3),
        "p_value": None if p != p else round(p, 5),
        "cliffs_delta": None if d != d else round(d, 4),
        "effect": pcc.effect_label(d),
    }


def build_groups(labels, medians, clinical_only=False, modality="optitrack"):
    """Group the per-leg parameter medians by spasticity level.

    `modality` restricts to one measurement source. It defaults to optitrack
    and should stay there for any statistical comparison: IMU-derived
    amplitudes carry a systematic +20.4 deg offset, so pooling the two would
    inject that offset into the between-group difference.
    """
    groups = {sg.NON_SPASTIC: [], sg.SPASTIC: []}
    for key, lab in labels.items():
        if lab.level not in groups:
            continue
        if clinical_only and lab.source not in CLINICAL_SOURCES:
            continue
        vals = medians.get(key)
        if not vals:
            continue
        if modality and vals.get(MODALITY) != modality:
            continue
        groups[lab.level].append(vals)
    return groups


def write_leg_labels(labels, medians):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(LEGS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["participant", "leg", "spasticity", "source", "detail",
                    "pt_data_modality", "imu_purpose", "n_tak", "condition"])
        for (pid, leg), lab in sorted(labels.items(),
                                      key=lambda kv: (int(kv[0][0]) if kv[0][0].isdigit()
                                                      else 999, kv[0][1])):
            purpose = dp.classify(pid)
            w.writerow([pid, leg, lab.level, lab.source, lab.detail,
                        (medians.get((pid, leg)) or {}).get(MODALITY, "none"),
                        purpose.imu_purpose, purpose.n_tak,
                        (medians.get((pid, leg)) or {}).get(CONDITION, "")])
    print(f"-> {LEGS_CSV}")


def write_stats(rows_all, rows_clinical):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(STATS_CSV, "w", newline="", encoding="utf-8") as fh:
        cols = ["subset", "parameter", "n_non_spastic", "n_spastic",
                "median_non_spastic", "median_spastic", "mann_whitney_u",
                "p_value", "cliffs_delta", "effect"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for subset, rows in (("all", rows_all), ("clinical_only", rows_clinical)):
            for r in rows:
                w.writerow(dict(r, subset=subset))
    print(f"-> {STATS_CSV}")


def make_figure(groups_all, groups_clinical):
    os.makedirs(OUT_DIR, exist_ok=True)
    n = len(PARAMS)
    fig, axes = plt.subplots(2, n, figsize=(3.0 * n, 7.5), squeeze=False)
    for col, param in enumerate(PARAMS):
        for row, (groups, title) in enumerate(
                ((groups_all, "all labels"), (groups_clinical, "clinical only"))):
            ax = axes[row][col]
            a = [v[param] for v in groups[sg.NON_SPASTIC] if param in v]
            b = [v[param] for v in groups[sg.SPASTIC] if param in v]
            data = [a or [np.nan], b or [np.nan]]
            bp = ax.boxplot(data, tick_labels=[f"non\nn={len(a)}", f"sp\nn={len(b)}"],
                            patch_artist=True, widths=0.55)
            for patch, colour in zip(bp["boxes"], ("#4C78A8", "#E45756")):
                patch.set_facecolor(colour)
                patch.set_alpha(0.65)
            ax.set_title(f"{param}\n({title})", fontsize=8)
            ax.tick_params(labelsize=7)
    fig.suptitle("PT parameters stratified by spasticity (per leg, pre condition)\n"
                 "Top: every labelled leg. Bottom: clinician-labelled legs only "
                 "-- the non-circular comparison.", fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.93])
    fig.savefig(FIGURE_PNG, dpi=150)
    plt.close(fig)
    print(f"-> {FIGURE_PNG}")


def arm_by_participant():
    registry, registry_exists = pcc.load_registry()
    out = {}
    for pid in participant_roster():
        arm, _src = pcc.classify_participant(
            pid, pcc.load_metadata_diagnosis(pid), registry, registry_exists)
        out[pid] = arm
    return out


def print_diagnosis_crosstab(labels, arms):
    """Diagnosis against spasticity, per leg.

    Printed because the whole point of this grouping is that the two are NOT
    the same variable. An MS diagnosis does not imply spasticity -- several MS
    participants here have none, and they belong in the non-spastic group
    beside the controls rather than in an "impaired" bucket defined by their
    chart. If this table ever collapses to a diagonal, the grouping has
    silently reverted to diagnosis.
    """
    levels = [sg.NON_SPASTIC, sg.SPASTIC, sg.UNKNOWN]
    table = {}
    for (pid, _leg), lab in labels.items():
        arm = arms.get(pid, "?")
        table.setdefault(arm, {lv: 0 for lv in levels})
        table[arm][lab.level] += 1

    print("")
    print(f"{'diagnosis':<12} {'non-spastic':>12} {'spastic':>9} {'unknown':>9}   (legs)")
    for arm in sorted(table):
        row = table[arm]
        print(f"{arm:<12} {row[sg.NON_SPASTIC]:>12} {row[sg.SPASTIC]:>9} "
              f"{row[sg.UNKNOWN]:>9}")
    ms_non = table.get("MS", {}).get(sg.NON_SPASTIC, 0)
    if ms_non:
        ms_pids = sorted({pid for (pid, _l), lab in labels.items()
                          if arms.get(pid) == "MS" and lab.level == sg.NON_SPASTIC},
                         key=lambda x: int(x) if x.isdigit() else 999)
        print("")
        print(f"  {ms_non} MS legs are non-spastic (participants "
              f"{', '.join('P' + p for p in ms_pids)}) -- grouped with the "
              f"controls, which is the point of grouping by spasticity.")


def print_recovery_status(labels, medians):
    """Which legs have parameters, from which modality, and which have none."""
    imu = sorted((k for k, v in medians.items() if v.get(MODALITY) == "imu"),
                 key=lambda k: (int(k[0]) if k[0].isdigit() else 999, k[1]))
    missing = sorted((k for k in labels if k not in medians),
                     key=lambda k: (int(k[0]) if k[0].isdigit() else 999, k[1]))
    if imu:
        print("")
        print("recovered via IMU (no usable OptiTrack; held out of the pooled "
              "stats because IMU A0 runs +20.4 deg high):")
        for pid, leg in imu:
            purpose = dp.classify(pid)
            note = "" if purpose.imu_purpose == dp.PURPOSE_RESULTS else                 "   [TRAINING ONLY -- no .tak for this participant]"
            print(f"   P{pid} {leg}{note}")
    training = dp.training_only_participants()
    if training:
        print("")
        print(f"IMU/recording data marked TRAINING ONLY (no .tak, so the optical "
              f"reference cannot be regenerated): "
              f"{', '.join('P' + p for p in training)}")
    if missing:
        print("")
        print("no PT parameters from any modality:")
        for pid, leg in missing:
            print(f"   P{pid} {leg}  ({labels[(pid, leg)].level})")


def main():
    medians = leg_param_medians()
    param_conditions = {k: (v.get(CONDITION, ""), v) for k, v in medians.items()}
    labels = label_every_leg(param_conditions)
    arms = arm_by_participant()

    counts = sg.summarise(labels)
    by_source = {}
    for lab in labels.values():
        by_source[lab.source] = by_source.get(lab.source, 0) + 1
    print(f"legs labelled: {len(labels)}  {counts}")
    print(f"label sources: {by_source}")
    print(f"legs with scored PT data: {len(medians)}")

    print_diagnosis_crosstab(labels, arms)
    print_recovery_status(labels, medians)

    groups_all = build_groups(labels, medians)
    groups_clinical = build_groups(labels, medians, clinical_only=True)
    print(f"\nALL      -> non-spastic n={len(groups_all[sg.NON_SPASTIC])}, "
          f"spastic n={len(groups_all[sg.SPASTIC])}")
    print(f"CLINICAL -> non-spastic n={len(groups_clinical[sg.NON_SPASTIC])}, "
          f"spastic n={len(groups_clinical[sg.SPASTIC])}")

    rows_all = [compare(groups_all, p) for p in PARAMS]
    rows_clinical = [compare(groups_clinical, p) for p in PARAMS]

    print(f"\n{'parameter':<16} {'ALL p':>9} {'delta':>8} {'effect':<12} "
          f"{'CLIN p':>9} {'delta':>8} {'effect'}")
    for ra, rc in zip(rows_all, rows_clinical):
        print(f"{ra['parameter']:<16} {str(ra['p_value']):>9} "
              f"{str(ra['cliffs_delta']):>8} {ra['effect']:<12} "
              f"{str(rc['p_value']):>9} {str(rc['cliffs_delta']):>8} {rc['effect']}")

    write_leg_labels(labels, medians)
    write_stats(rows_all, rows_clinical)
    make_figure(groups_all, groups_clinical)
    print("\nPublished figure generators are untouched -- they remain on the "
          "original (Control, MS) grouping by design.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
