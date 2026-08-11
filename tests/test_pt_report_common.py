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


def test_attach_rmse_keeps_curve_arrays(monkeypatch):
    import numpy as np
    fake_curves = [
        {"name": "mediapipe", "t": np.array([0.0, 1.0]), "ang": np.array([180.0, 150.0]), "rmse": 3.5},
        {"name": "imu_viewer", "t": np.array([0.0, 1.0]), "ang": np.array([180.0, 155.0]), "rmse": 5.0},
    ]
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": np.array([0.0, 1.0]),
          "angle_raw": np.array([180.0, 152.0]), "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: fake_curves)

    common.attach_rmse(by_leg_tp)

    assert rec["mediapipe_rmse"] == 3.5
    assert rec["mediapipe_curve"] is not None
    assert list(rec["mediapipe_curve"]["ang"]) == [180.0, 150.0]
    assert rec["imu_rmse"] == 5.0
    assert rec["imu_curve"] is not None


def test_attach_rmse_deterministic_candidate_not_overwritten(monkeypatch):
    """Two mediapipe-name matches -- today's loop silently keeps whichever
    came last. Curves arrive sorted best-RMSE-first, so the FIRST match
    (RMSE=2.0) must win, not the second (RMSE=9.0)."""
    import numpy as np
    fake_curves = [
        {"name": "mediapipe", "t": np.array([0.0]), "ang": np.array([180.0]), "rmse": 2.0},
        {"name": "mediapipe_alt", "t": np.array([0.0]), "ang": np.array([170.0]), "rmse": 9.0},
    ]
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": np.array([0.0]),
          "angle_raw": np.array([180.0]), "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: fake_curves)

    common.attach_rmse(by_leg_tp)

    assert rec["mediapipe_rmse"] == 2.0
    assert list(rec["mediapipe_curve"]["ang"]) == [180.0]


def test_attach_rmse_no_curves_leaves_curve_fields_none(monkeypatch):
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": [0.0],
          "angle_raw": [180.0], "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: [])

    common.attach_rmse(by_leg_tp)

    assert rec.get("mediapipe_curve") is None
    assert rec.get("imu_curve") is None
