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
