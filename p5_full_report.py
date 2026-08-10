"""
p5_full_report.py
==================
Participant 5: full clinical-report-style figure, same structure/methodology
as p13_full_report.py (see pt_report_common.py). Uses the full 7-parameter
Popovic PT score.

P5's OptiTrack data now lives in the live tree --
OptiTrack_Recordings/Participant_5/{Left,Right}/{pre,post,week_1_post}/
trial_N_optitrack.csv -- discovered the same generic way run_pt_analysis.py
discovers every other participant, via pt_report_common.collect_participant().

This used to read directly from an OneDrive archive (Pendulastic_7_28_Archive/
Optitrack recordings/Participant_5_{leg}_{side,post,post_again}/...) because
P5's data hadn't been migrated into the live tree yet. It since has been, and
that archive path no longer contains any Participant_5_* folders on this
machine (confirmed 2026-08-10) -- glob.glob() against a since-removed archive
directory returns no matches rather than raising, so this script kept
running and silently wrote out an all-empty report (every leg/timepoint
showing "0 valid trial(s)") instead of failing loudly. Delegating to
collect_participant() instead of maintaining a second, now-stale discovery
path means this can't silently drift out of sync with where the data
actually lives again.

Run:
    .venv\\Scripts\\python.exe p5_full_report.py
"""
from __future__ import annotations

import pt_report_common as common

CAVEAT = ("Source: live OptiTrack_Recordings/Participant_5/ tree. "
         "Same 7-parameter Popovic scoring as the P13 report for direct comparability.")


if __name__ == "__main__":
    by_leg_tp, timepoints = common.collect_participant("5")
    for (leg, tp), trials in by_leg_tp.items():
        print(f"{leg}/{tp}: {len(trials)} valid trial(s)")
    common.make_report_figure("Participant 5", by_leg_tp, timepoints,
                              "P5_full_report.png", CAVEAT)
