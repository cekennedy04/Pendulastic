import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import run_pt_analysis as rpa


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
