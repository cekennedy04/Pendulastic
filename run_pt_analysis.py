"""
run_pt_analysis.py
===================
Manual trigger: 30 minutes after a participant's first discoverable
recording lands, generates their full PT-score report, an
RMSE-vs-OptiTrack figure (MediaPipe + IMU), and PT-score comparison
figures against the two reference participants (P5, P13) -- using
whatever trials are available at that point, however many that is. Not
every timepoint reaches TRIAL_THRESHOLD trials per leg (some sessions run
short), so trial count alone can no longer gate report generation; elapsed
time since the first recording can (2026-08-07 policy change -- see
git history for the trial-count-threshold version this replaced).

Built entirely on pt_report_common.py's generic collect_participant() /
make_report_figure() / make_rmse_figure() / make_comparison_figure() --
the same functions the hardcoded P13/P5 reports and the P13-vs-P5
comparison (p13_full_report.py, p5_full_report.py, p13_vs_p5_comparison.py)
are built from -- so every output here stays numerically and visually
consistent with what's already in Model_Analysis_Outputs/PT_Scores.

Participants with no discoverable trials yet, or whose first recording
landed less than READY_AFTER_SECONDS ago, are reported and skipped --
nothing in Model_Analysis_Outputs is touched for them.

Run after a recording session (or any time after -- re-running is safe and
just regenerates the same figures from current data):
    .venv\\Scripts\\python.exe run_pt_analysis.py <participant_id>
    .venv\\Scripts\\python.exe run_pt_analysis.py            # every participant that currently qualifies
"""
from __future__ import annotations

import csv
import os
import sys
import time

import pt_cohort_common
import pt_report_common as common

READY_AFTER_SECONDS = 30 * 60   # 30 minutes since the first recording landed
REFERENCE_PARTICIPANTS = ("5", "13")
MAS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mas_scores.csv")

leg_trial_counts = common.leg_trial_counts


def run_for_participant(pid, cohort_snapshot=None):
    counts = leg_trial_counts(pid)
    first_ts = common.first_recording_time(pid)
    if first_ts is None:
        print(f"P{pid}: no discoverable trials yet. Skipping.")
        return []

    elapsed = time.time() - first_ts
    if elapsed < READY_AFTER_SECONDS:
        print(f"P{pid}: right={counts['right']} left={counts['left']} trials -- "
              f"first recording landed {elapsed / 60:.1f} min ago, waiting "
              f"{(READY_AFTER_SECONDS - elapsed) / 60:.1f} more min before analyzing. Skipping.")
        return []

    print(f"P{pid}: right={counts['right']} left={counts['left']} trials -- "
          f"{elapsed / 60:.0f} min since first recording, generating figures...")
    by_leg_tp, timepoints = common.collect_participant(pid)
    label = f"P{pid}"
    outputs = []

    outputs.append(common.make_report_figure(
        label, by_leg_tp, timepoints, f"P{pid}_full_report.png",
        caveat_text=f"Auto-generated {READY_AFTER_SECONDS // 60} min after the first recording "
                   f"landed, from whatever trials were available at that point "
                   f"(right={counts['right']}, left={counts['left']}).",
        cohort_snapshot=cohort_snapshot))

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

    try:
        cohort_snapshot = pt_cohort_common.build_cohort_snapshot()
    except Exception as e:
        # A malformed hand-edit to participant_groups.json (or any other
        # cohort-snapshot failure) shouldn't take down the whole run --
        # per-participant reports still generate, just without cohort context.
        print(f"Cohort snapshot build failed, reports will omit cohort context: {e}")
        cohort_snapshot = None

    qualified = set()
    for pid in pids:
        if run_for_participant(pid, cohort_snapshot=cohort_snapshot):
            qualified.add(pid)

    ready_for_mas = qualified & _mas_scored_participants()
    if ready_for_mas:
        print(f"{len(ready_for_mas)} participant(s) now have both trial data and MAS scores on file "
             f"-- run mas_validation.py to refresh the validation report.")

    try:
        pt_cohort_common.write_cohort_artifacts(cohort_snapshot)
    except Exception as e:
        # A malformed hand-edit to participant_groups.json (or any other
        # cohort-comparison failure) shouldn't take down the whole run --
        # the per-participant reports above already succeeded.
        print(f"Cohort comparison failed: {e}")


if __name__ == "__main__":
    main()
