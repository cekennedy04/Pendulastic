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


def test_release_aligned_hpe_curve_aligns_release_to_zero():
    import numpy as np
    t = np.linspace(0, 3, 90)
    # Held at 180 for 1s, then swings down -- same shape release_aligned_waveform's
    # own docstring describes for OptiTrack.
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is not None
    t_plot, a_plot = result
    # Release should land near t=0 in the shifted output -- the original
    # hold-then-swing transition was at t=1.0 in input coordinates.
    assert abs(t_plot[np.argmin(np.abs(t_plot))]) < 0.2


def test_release_aligned_hpe_curve_rejects_too_few_samples():
    import numpy as np
    result = common.release_aligned_hpe_curve(np.array([0.0, 0.1, 0.2]), np.array([180.0, 179.0, 178.0]))
    assert result is None


def test_release_aligned_hpe_curve_rejects_non_monotonic_time():
    import numpy as np
    t = np.array([0.0, 0.2, 0.1, 0.3, 0.4])
    ang = np.array([180.0, 179.0, 178.0, 177.0, 176.0])
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is None
