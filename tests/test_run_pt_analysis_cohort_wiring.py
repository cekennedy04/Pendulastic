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


def test_main_calls_run_cohort_comparison_once(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py"])
    monkeypatch.setattr(rpa.common, "list_participants", lambda: {})
    calls = []
    monkeypatch.setattr(rpa.pt_cohort_common, "run_cohort_comparison", lambda: calls.append(True))
    rpa.main()
    assert calls == [True]


def test_main_survives_cohort_comparison_exception(monkeypatch, capsys):
    # A malformed participant_groups.json (or any other cohort-comparison
    # failure) must not take down the whole script -- the per-participant
    # reports already generated above are fine on their own.
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py"])
    monkeypatch.setattr(rpa.common, "list_participants", lambda: {})

    def _boom():
        raise ValueError("boom")

    monkeypatch.setattr(rpa.pt_cohort_common, "run_cohort_comparison", _boom)
    rpa.main()   # must not raise
    assert "Cohort comparison failed: boom" in capsys.readouterr().out


def test_main_calls_cohort_comparison_even_with_single_pid_arg(monkeypatch):
    # Regression guard for the exact bug this design fixed during review:
    # cohort comparison must still run when main() was invoked for one
    # specific participant, not the full sweep -- run_cohort_comparison()
    # recomputes the full qualifying set itself rather than reusing
    # main()'s pid-scoped `qualified` set.
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py", "999"])
    monkeypatch.setattr(rpa, "leg_trial_counts", lambda pid: {"left": 0, "right": 0})
    monkeypatch.setattr(rpa, "run_for_participant", lambda pid: [])
    calls = []
    monkeypatch.setattr(rpa.pt_cohort_common, "run_cohort_comparison", lambda: calls.append(True))
    rpa.main()
    assert calls == [True]
