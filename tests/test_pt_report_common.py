import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pt_report_common as common
import run_pt_analysis


def test_leg_trial_counts_sums_across_conditions_for_one_participant(monkeypatch):
    fake_records = [
        {"participant": "13", "leg": "right", "condition": "pre"},
        {"participant": "13", "leg": "right", "condition": "post"},
        {"participant": "13", "leg": "left", "condition": "pre"},
        {"participant": "14", "leg": "right", "condition": "pre"},
    ]
    monkeypatch.setattr(common, "discover_all_trials", lambda: fake_records)
    assert common.leg_trial_counts("13") == {"left": 1, "right": 2}


def test_leg_trial_counts_zero_for_unknown_participant(monkeypatch):
    monkeypatch.setattr(common, "discover_all_trials", lambda: [])
    assert common.leg_trial_counts("99") == {"left": 0, "right": 0}


def test_run_pt_analysis_leg_trial_counts_is_common_function():
    assert run_pt_analysis.leg_trial_counts is common.leg_trial_counts


def test_first_recording_time_returns_earliest_mtime_for_participant(monkeypatch):
    fake_records = [
        {"participant": "13", "leg": "right", "mtime": 300.0},
        {"participant": "13", "leg": "left", "mtime": 100.0},
        {"participant": "14", "leg": "right", "mtime": 1.0},
    ]
    monkeypatch.setattr(common, "discover_all_trials", lambda: fake_records)
    assert common.first_recording_time("13") == 100.0


def test_first_recording_time_none_when_no_trials(monkeypatch):
    monkeypatch.setattr(common, "discover_all_trials", lambda: [])
    assert common.first_recording_time("13") is None
