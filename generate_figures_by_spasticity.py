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


def label_every_leg():
    """{(pid, leg): SpasticityLabel} for BOTH legs of every real participant.

    Enumerated from the participant roster and LEGS, never from whatever data
    happens to exist, so a leg with no recordings shows up as UNKNOWN instead
    of vanishing -- P7 left, P17 both, P22 left are the live cases.
    """
    registry, registry_exists = pcc.load_registry()
    mas_by_leg = sg.load_mas_by_leg()
    mas_components = sg.load_mas_components_by_leg()
    a0 = a0_by_leg_from_optitrack()

    out = {}
    for pid in participant_roster():
        arm, _src = pcc.classify_participant(
            pid, pcc.load_metadata_diagnosis(pid), registry, registry_exists)
        legs = sg.classify_participant_legs(
            pid, arm=arm, mas_by_leg=mas_by_leg, mas_components=mas_components,
            a0_by_leg={leg: a0.get((pid, leg)) for leg in sg.LEGS})
        for leg, lab in legs.items():
            out[(pid, leg)] = lab
    return out


def leg_param_medians():
    """{(pid, leg): {param: median}} from each leg's scored pre trials."""
    out = {}
    for pid in participant_roster():
        try:
            # collect_participant returns (by_leg_tp, timepoints); the first
            # element is {(leg, condition): [trial_records]}.
            by_leg_tp, _timepoints = common.collect_participant(pid)
        except Exception:
            continue
        for (leg, condition), trials in by_leg_tp.items():
            if not str(condition).lower().startswith("pre") or not trials:
                continue
            vals = {}
            for key in PARAMS:
                got = [t[key] for t in trials
                       if isinstance(t, dict) and t.get(key) is not None
                       and float(t[key]) == float(t[key])]
                if got:
                    vals[key] = float(np.median(got))
            if vals:
                out[(pid, str(leg).lower())] = vals
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


def build_groups(labels, medians, clinical_only=False):
    groups = {sg.NON_SPASTIC: [], sg.SPASTIC: []}
    for key, lab in labels.items():
        if lab.level not in groups:
            continue
        if clinical_only and lab.source not in CLINICAL_SOURCES:
            continue
        vals = medians.get(key)
        if vals:
            groups[lab.level].append(vals)
    return groups


def write_leg_labels(labels, medians):
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(LEGS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["participant", "leg", "spasticity", "source", "detail",
                    "has_pt_data"])
        for (pid, leg), lab in sorted(labels.items(),
                                      key=lambda kv: (int(kv[0][0]) if kv[0][0].isdigit()
                                                      else 999, kv[0][1])):
            w.writerow([pid, leg, lab.level, lab.source, lab.detail,
                        "yes" if medians.get((pid, leg)) else "no"])
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


def main():
    labels = label_every_leg()
    medians = leg_param_medians()

    counts = sg.summarise(labels)
    by_source = {}
    for lab in labels.values():
        by_source[lab.source] = by_source.get(lab.source, 0) + 1
    print(f"legs labelled: {len(labels)}  {counts}")
    print(f"label sources: {by_source}")
    print(f"legs with scored PT data: {len(medians)}")

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
