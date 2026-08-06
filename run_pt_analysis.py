"""
run_pt_analysis.py
===================
Manual trigger: once a participant has at least TRIAL_THRESHOLD right-leg
trials AND TRIAL_THRESHOLD left-leg trials recorded (counted across all
recorded conditions/sessions for that leg), generates their full PT-score
report, an RMSE-vs-OptiTrack figure (MediaPipe + IMU), and PT-score
comparison figures against the two reference participants (P5, P13).

Built entirely on pt_report_common.py's generic collect_participant() /
make_report_figure() / make_rmse_figure() / make_comparison_figure() --
the same functions the hardcoded P13/P5 reports and the P13-vs-P5
comparison (p13_full_report.py, p5_full_report.py, p13_vs_p5_comparison.py)
are built from -- so every output here stays numerically and visually
consistent with what's already in Model_Analysis_Outputs/PT_Scores.

Below-threshold participants are reported (trials still needed per leg)
and skipped -- nothing in Model_Analysis_Outputs is touched for them.

Run after a recording session:
    .venv\\Scripts\\python.exe run_pt_analysis.py <participant_id>
    .venv\\Scripts\\python.exe run_pt_analysis.py            # every participant that currently qualifies
"""
from __future__ import annotations

import csv
import os
import sys

import pt_report_common as common

TRIAL_THRESHOLD = 4
REFERENCE_PARTICIPANTS = ("5", "13")
MAS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mas_scores.csv")


def leg_trial_counts(participant_id):
    """Total recorded trials per leg for this participant, summed across
    every condition/session found (pre, post, side, control, etc.) -- not
    per-condition. A participant with 2 pre + 3 post right-leg trials counts
    as 5 right, matching TRIAL_THRESHOLD against the cumulative total."""
    counts = {"left": 0, "right": 0}
    for r in common.discover_all_trials():
        if r["participant"] == participant_id and r["leg"] in counts:
            counts[r["leg"]] += 1
    return counts


def run_for_participant(pid):
    counts = leg_trial_counts(pid)
    missing = {leg: max(0, TRIAL_THRESHOLD - n) for leg, n in counts.items()}
    if any(missing.values()):
        print(f"P{pid}: right={counts['right']} left={counts['left']} trials "
              f"-- needs {missing['right']} more right / {missing['left']} more left "
              f"to reach the {TRIAL_THRESHOLD}+{TRIAL_THRESHOLD} threshold. Skipping.")
        return []

    print(f"P{pid}: right={counts['right']} left={counts['left']} trials -- threshold met, generating figures...")
    by_leg_tp, timepoints = common.collect_participant(pid)
    label = f"P{pid}"
    outputs = []

    outputs.append(common.make_report_figure(
        label, by_leg_tp, timepoints, f"P{pid}_full_report.png",
        caveat_text=f"Auto-generated once the {TRIAL_THRESHOLD}+{TRIAL_THRESHOLD} right/left trial threshold was met."))

    rmse_path, has_rmse_data = common.make_rmse_figure(
        label, by_leg_tp, timepoints, f"P{pid}_rmse.png")
    if has_rmse_data:
        outputs.append(rmse_path)
    else:
        print(f"P{pid}: no MediaPipe/IMU comparison data found -- RMSE figure has empty panels.")

    for ref_pid in REFERENCE_PARTICIPANTS:
        if ref_pid == pid:
            continue
        ref_by_leg_tp, ref_timepoints = common.collect_participant(ref_pid)
        if not any(ref_by_leg_tp.values()):
            print(f"P{pid}: reference participant P{ref_pid} has no scoreable trials -- skipping that comparison.")
            continue
        outputs.append(common.make_comparison_figure(
            label, by_leg_tp, timepoints, f"P{ref_pid}", ref_by_leg_tp, ref_timepoints,
            f"P{pid}_vs_P{ref_pid}_comparison.png"))

    return outputs


def _mas_scored_participants():
    """Distinct participant ids with at least one row in mas_scores.csv, or
    an empty set if that file doesn't exist yet -- this is only used for the
    end-of-run nudge below, never to gate figure generation itself."""
    if not os.path.isfile(MAS_CSV):
        return set()
    with open(MAS_CSV, newline="", encoding="utf-8") as f:
        return {row["participant"].strip() for row in csv.DictReader(f) if row.get("participant", "").strip()}


def main():
    if len(sys.argv) > 1:
        pids = [sys.argv[1]]
    else:
        pids = list(common.list_participants().keys())

    qualified = set()
    for pid in pids:
        counts = leg_trial_counts(pid)
        if counts["left"] >= TRIAL_THRESHOLD and counts["right"] >= TRIAL_THRESHOLD:
            qualified.add(pid)
        run_for_participant(pid)

    ready_for_mas = qualified & _mas_scored_participants()
    if ready_for_mas:
        print(f"{len(ready_for_mas)} participant(s) now have both trial data and MAS scores on file "
             f"-- run mas_validation.py to refresh the validation report.")


if __name__ == "__main__":
    main()
