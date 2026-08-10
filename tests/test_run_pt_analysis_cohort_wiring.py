import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import run_pt_analysis as rpa


# ── run_for_participant time-based gating (2026-08-07 policy change:      ──
# ── trial count alone no longer gates report generation -- not every      ──
# ── timepoint reaches TRIAL_THRESHOLD trials per leg, so elapsed time     ──
# ── since the first recording gates it instead)                          ──

def test_run_for_participant_skips_when_no_trials(monkeypatch):
    monkeypatch.setattr(rpa, "leg_trial_counts", lambda pid: {"left": 0, "right": 0})
    monkeypatch.setattr(rpa.common, "first_recording_time", lambda pid: None)
    assert rpa.run_for_participant("999") == []


def test_run_for_participant_skips_before_ready_after_seconds(monkeypatch):
    now = 10_000.0
    monkeypatch.setattr(rpa.time, "time", lambda: now)
    monkeypatch.setattr(rpa, "leg_trial_counts", lambda pid: {"left": 1, "right": 1})
    # Recorded 10 minutes ago -- under the 30 minute READY_AFTER_SECONDS floor.
    monkeypatch.setattr(rpa.common, "first_recording_time", lambda pid: now - 10 * 60)
    assert rpa.run_for_participant("15") == []


def test_run_for_participant_proceeds_after_ready_after_seconds_with_low_trial_count(monkeypatch):
    # Core of the policy change: only 1 trial per leg, well under the old
    # TRIAL_THRESHOLD of 4, but 30+ minutes have passed -- must still
    # generate a report from whatever data is available.
    now = 10_000.0
    monkeypatch.setattr(rpa.time, "time", lambda: now)
    monkeypatch.setattr(rpa, "leg_trial_counts", lambda pid: {"left": 1, "right": 1})
    monkeypatch.setattr(rpa.common, "first_recording_time", lambda pid: now - 31 * 60)
    monkeypatch.setattr(rpa.common, "collect_participant", lambda pid: ({}, []))
    monkeypatch.setattr(rpa.common, "make_report_figure", lambda *a, **k: "report.png")
    monkeypatch.setattr(rpa.common, "make_rmse_figure", lambda *a, **k: ("rmse.png", False))

    outputs = rpa.run_for_participant("15")
    assert outputs == ["report.png"]


def test_run_for_participant_attaches_rmse_before_report_figure(monkeypatch):
    """Regression: make_report_figure()'s Row 1 (HPE/IMU overlay), Row 4
    (RMSE bars), and Row 5 (per-source paired PT7) all read
    rec["mediapipe_curve"]/rec["mediapipe_rmse"] -- fields only
    attach_rmse() ever sets. Confirmed against real participant data
    (2026-08-10 full-report-hpe-accuracy plan, Task 14 manual verification):
    calling make_report_figure() before attach_rmse() has ever run on
    by_leg_tp silently renders those rows empty even when real MediaPipe/
    IMU data exists, because make_rmse_figure() (the only other caller of
    attach_rmse()) used to run second. attach_rmse() must run before
    make_report_figure() is called."""
    now = 10_000.0
    calls = []
    monkeypatch.setattr(rpa.time, "time", lambda: now)
    monkeypatch.setattr(rpa, "leg_trial_counts", lambda pid: {"left": 1, "right": 1})
    monkeypatch.setattr(rpa.common, "first_recording_time", lambda pid: now - 31 * 60)
    by_leg_tp = {("left", "pre"): [{"mediapipe_rmse": None}]}
    monkeypatch.setattr(rpa.common, "collect_participant", lambda pid: (by_leg_tp, []))
    monkeypatch.setattr(rpa.common, "attach_rmse", lambda blt: calls.append("attach_rmse") or blt)
    monkeypatch.setattr(rpa.common, "make_report_figure",
                        lambda *a, **k: calls.append("make_report_figure") or "report.png")
    monkeypatch.setattr(rpa.common, "make_rmse_figure",
                        lambda *a, **k: calls.append("make_rmse_figure") or ("rmse.png", False))
    monkeypatch.setattr(rpa.common, "make_comparison_figure", lambda *a, **k: "comparison.png")

    rpa.run_for_participant("15")

    assert "attach_rmse" in calls
    assert calls.index("attach_rmse") < calls.index("make_report_figure")


def test_main_calls_write_cohort_artifacts_once(monkeypatch):
    # 2026-08-10 full-report-hpe-accuracy plan (Task 13): main() now builds
    # one cohort snapshot up front and threads it into every per-participant
    # report before writing cohort artifacts from that same snapshot,
    # instead of calling the old run_cohort_comparison() combinator (which
    # recomputed its own snapshot independently). run_cohort_comparison()
    # itself still exists, unchanged, for any other direct caller.
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py"])
    monkeypatch.setattr(rpa.common, "list_participants", lambda: {})
    fake_snapshot = {"ms_pids": [], "control_pids": []}
    monkeypatch.setattr(rpa.pt_cohort_common, "build_cohort_snapshot", lambda: fake_snapshot)
    calls = []
    monkeypatch.setattr(rpa.pt_cohort_common, "write_cohort_artifacts", lambda snap: calls.append(snap))
    rpa.main()
    assert calls == [fake_snapshot]


def test_main_survives_cohort_comparison_exception(monkeypatch, capsys):
    # A malformed participant_groups.json (or any other cohort-comparison
    # failure) must not take down the whole script -- the per-participant
    # reports already generated above are fine on their own.
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py"])
    monkeypatch.setattr(rpa.common, "list_participants", lambda: {})
    monkeypatch.setattr(rpa.pt_cohort_common, "build_cohort_snapshot", lambda: {"ms_pids": [], "control_pids": []})

    def _boom(snap):
        raise ValueError("boom")

    monkeypatch.setattr(rpa.pt_cohort_common, "write_cohort_artifacts", _boom)
    rpa.main()   # must not raise
    assert "Cohort comparison failed: boom" in capsys.readouterr().out


def test_main_calls_cohort_artifacts_even_with_single_pid_arg(monkeypatch):
    # Regression guard for the exact bug this design fixed during review:
    # cohort artifacts must still be written when main() was invoked for one
    # specific participant, not the full sweep -- build_cohort_snapshot()
    # recomputes the full qualifying set itself rather than reusing
    # main()'s pid-scoped `qualified` set.
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py", "999"])
    monkeypatch.setattr(rpa, "leg_trial_counts", lambda pid: {"left": 0, "right": 0})
    monkeypatch.setattr(rpa, "run_for_participant", lambda pid, cohort_snapshot=None: [])
    fake_snapshot = {"ms_pids": [], "control_pids": []}
    monkeypatch.setattr(rpa.pt_cohort_common, "build_cohort_snapshot", lambda: fake_snapshot)
    calls = []
    monkeypatch.setattr(rpa.pt_cohort_common, "write_cohort_artifacts", lambda snap: calls.append(snap))
    rpa.main()
    assert calls == [fake_snapshot]
