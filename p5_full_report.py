"""
p5_full_report.py
==================
Participant 5: full clinical-report-style figure, same structure/methodology
as p13_full_report.py (see pt_report_common.py). Uses the full 7-parameter
Popovic PT score.

P5's raw OptiTrack data is NOT in the live OptiTrack_Recordings/ (which
currently only holds Participant_13) -- it was found intact in the archive:
  OneDrive/Desktop/Shirley Ryan/Pendulastic_7_28_Archive/Optitrack recordings/
    Participant_5_{left,right}_{side,post,post_again}/
      Position_1/Height_Joint-Level/trial_N_optitrack.csv

Three timepoints per leg: side (initial/pre), post (post-training),
post_again (+1 week later) -- verified genuinely distinct via checksum, not
a duplicate-export bug like P13's right_post originally was. Read directly
from the archive path (read-only); nothing is copied into the repo.

Note: Participant_5_right_side/ has a stray nested Participant_0_control/
subfolder (looks like a misplaced archival artifact, not P5 data) -- the
glob below is non-recursive so it's excluded automatically.

Run:
    .venv\\Scripts\\python.exe p5_full_report.py
"""
from __future__ import annotations

import glob
import os

import pendulastic_pt_score as pt
import pt_report_common as common

ARCHIVE_ROOT = (r"C:\Users\cladi\OneDrive\Desktop\Shirley Ryan\Pendulastic_7_28_Archive"
                r"\Optitrack recordings")

TIMEPOINTS = [("side", "Initial", common.COLORS['red']),
              ("post", "Post", common.COLORS['green']),
              ("post_again", "+1wk Post", common.COLORS['purple'])]

CAVEAT = ("Source: archived OptiTrack export (Pendulastic_7_28_Archive), read-only. "
         "Same 7-parameter Popovic scoring as the P13 report for direct comparability.")


def collect_all():
    records = []
    for leg in ("left", "right"):
        for tp_key, _, _ in TIMEPOINTS:
            folder = os.path.join(ARCHIVE_ROOT, f"Participant_5_{leg}_{tp_key}",
                                  "Position_1", "Height_Joint-Level")
            pid = f"5_{leg}_{tp_key}"
            for csv_path in sorted(glob.glob(os.path.join(folder, "trial_*_optitrack.csv"))):
                trial = os.path.basename(csv_path).split("_")[1]
                try:
                    t, angle = pt.load_optitrack(csv_path)
                except Exception:
                    continue
                rec = common.score_trial(pid, trial, t, angle)
                if rec is not None:
                    records.append(rec)

    by_leg_tp = {}
    for leg in ("left", "right"):
        for tp_key, _, _ in TIMEPOINTS:
            pid = f"5_{leg}_{tp_key}"
            by_leg_tp[(leg, tp_key)] = [r for r in records if r["pid"] == pid]
    return by_leg_tp


if __name__ == "__main__":
    by_leg_tp = collect_all()
    for (leg, tp), trials in by_leg_tp.items():
        print(f"{leg}/{tp}: {len(trials)} valid trial(s)")
    common.make_report_figure("Participant 5", by_leg_tp, TIMEPOINTS,
                              "P5_full_report.png", CAVEAT)
