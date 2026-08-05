"""
p13_full_report.py
===================
Participant 13: full clinical-report-style figure (3x2 grid: waveforms,
7-parameter bars, PT-score trend x Left/Right leg). Uses the full
7-parameter Popovic PT score (pendulastic_pt_score.compute_pt_score --
labeled "6-param" in that module for historical reasons, but sums all 7
_PARAM_KEYS) for BOTH the parameter bars and the PT-score trend row, per
explicit user instruction.

Caveat carried from pendulastic_pt_score.py's own docstring: the 4-param
score (excludes area_ratio and f) is treated as "primary" elsewhere in this
codebase specifically because area_ratio is unreliable for marker-based
angles in duo-session recordings (assessor + patient both in frame) -- which
is every P13 trial. The 7-param score used here pulls all P13 trials into
the same MAS-4 zone, so the PT-score trend row is expected to show little
separation between timepoints; that's a real property of this score on this
data, not a plotting bug.

week_1_post uses direct collection (different folder layout, no Position_N
level, so discover_optitrack() doesn't recognize it).

Shared plotting/scoring logic lives in pt_report_common.py, reused by
p5_full_report.py and p13_vs_p5_comparison.py for a consistent methodology.

Run:
    .venv\\Scripts\\python.exe p13_full_report.py
"""
from __future__ import annotations

import glob
import os

import pendulastic_pt_score as pt
import pt_report_common as common

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TIMEPOINTS = [("pre", "Pre", common.COLORS['red']),
              ("post", "Post", common.COLORS['green']),
              ("week1post", "+1wk Post", common.COLORS['purple'])]

CAVEAT = ("Caveat: every trial carries an area-ratio quality warning (duo-session marker tracking); "
         "area_ratio is part of this 7-param score, which is why scores cluster in the Impaired zone.")


def collect_optitrack_trials():
    pids = {"13_left_pre", "13_left_post", "13_right_pre", "13_right_post"}
    records = []
    for entry in pt.discover_optitrack():
        if entry["pid"] not in pids:
            continue
        try:
            t, angle = pt.load_optitrack(entry["path"])
        except Exception:
            continue
        rec = common.score_trial(entry["pid"], entry["trial"], t, angle)
        if rec is not None:
            records.append(rec)
    return records


def collect_week1post_trials():
    records = []
    for leg, pid in (("Left", "13_left_week1post"), ("Right", "13_right_week1post")):
        opti_dir = os.path.join(BASE_DIR, "OptiTrack_Recordings", "Participant_13", leg, "week_1_post")
        for csv_path in sorted(glob.glob(os.path.join(opti_dir, "trial_*_optitrack.csv"))):
            trial = os.path.basename(csv_path).split("_")[1]
            try:
                t, angle = pt.load_optitrack(csv_path)
            except Exception:
                continue
            rec = common.score_trial(pid, trial, t, angle)
            if rec is not None:
                records.append(rec)
    return records


def collect_all():
    records = collect_optitrack_trials() + collect_week1post_trials()
    by_leg_tp = {}
    for leg in ("left", "right"):
        for tp_key, _, _ in TIMEPOINTS:
            pid = f"13_{leg}_{tp_key}"
            by_leg_tp[(leg, tp_key)] = [r for r in records if r["pid"] == pid]
    return by_leg_tp


if __name__ == "__main__":
    by_leg_tp = collect_all()
    for (leg, tp), trials in by_leg_tp.items():
        print(f"{leg}/{tp}: {len(trials)} valid trial(s)")
    common.make_report_figure("Participant 13", by_leg_tp, TIMEPOINTS,
                              "P13_full_report.png", CAVEAT)
