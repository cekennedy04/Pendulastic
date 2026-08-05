import csv
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pytest

import evaluate_all_participants as eap
import reliability_stats


def test_save_reliability_report_writes_icc_per_family(tmp_path):
    """Given fake per-extrema abs_err records spanning >=2 trials for a
    participant/family, _save_reliability_report() must compute a per-trial
    RMSE (sqrt(mean(abs_err**2)) grouped by family+participant+position+
    trial), then ICC(1,1) across each participant's trials, and write it --
    not fail or silently skip it."""
    ev = eap.PendulasticEvaluator.__new__(eap.PendulasticEvaluator)
    ev.output_root = str(tmp_path)
    ev.all_records = [
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "1", "abs_err": 4.0},
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "1", "abs_err": 4.4},
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "2", "abs_err": 4.8},
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "2", "abs_err": 4.9},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "1", "abs_err": 5.0},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "1", "abs_err": 5.2},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "2", "abs_err": 4.9},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "2", "abs_err": 5.0},
    ]

    ev._save_reliability_report()

    out_path = os.path.join(str(tmp_path), "reliability_report.csv")
    assert os.path.isfile(out_path)
    with open(out_path) as f:
        header = f.readline().strip().split(",")
    assert "family" in header
    assert "icc_rmse" in header

    # Independently recompute the expected per-trial RMSE (sqrt(mean(abs_err**2)))
    # by hand from the fixture data, grouped by participant+trial, then feed those
    # RMSE values through the real reliability_stats.icc_one_way() -- the same
    # function _save_reliability_report() is documented to call. This exercises
    # the RMSE formula and the (family, participant, position, trial) grouping
    # actually implemented in _save_reliability_report(), rather than only
    # checking the CSV header exists.
    def rmse(vals):
        return float(np.sqrt(np.mean(np.array(vals) ** 2)))

    p001_trial1_rmse = rmse([4.0, 4.4])
    p001_trial2_rmse = rmse([4.8, 4.9])
    p002_trial1_rmse = rmse([5.0, 5.2])
    p002_trial2_rmse = rmse([4.9, 5.0])
    expected = reliability_stats.icc_one_way([
        [p001_trial1_rmse, p001_trial2_rmse],
        [p002_trial1_rmse, p002_trial2_rmse],
    ])

    with open(out_path, newline="") as f:
        rows = {row["family"]: row for row in csv.DictReader(f)}
    pendulastic_row = rows["pendulastic"]
    assert pendulastic_row["n_participants_with_repeats"] == "2"
    written_icc = float(pendulastic_row["icc_rmse"])
    assert 0.0 <= written_icc <= 1.0
    assert written_icc == pytest.approx(expected["icc"], abs=1e-4)


def test_save_reliability_report_skips_family_with_no_repeat_trials():
    """A family where every participant has exactly 1 trial has no repeat
    -measures data for ICC -- must not crash, and reports icc_rmse as blank
    rather than a fabricated value."""
    ev = eap.PendulasticEvaluator.__new__(eap.PendulasticEvaluator)
    import tempfile
    ev.output_root = tempfile.mkdtemp()
    ev.all_records = [
        {"family": "hrnet", "participant": "P001", "position": "1", "trial": "1", "abs_err": 3.0},
        {"family": "hrnet", "participant": "P002", "position": "1", "trial": "1", "abs_err": 3.5},
    ]
    ev._save_reliability_report()   # must not raise

    out_path = os.path.join(ev.output_root, "reliability_report.csv")
    with open(out_path, newline="") as f:
        rows = {row["family"]: row for row in csv.DictReader(f)}
    hrnet_row = rows["hrnet"]
    # icc_rmse must be genuinely blank -- not "nan" or any other fabricated
    # placeholder -- since downstream (Task 9) treats a non-blank value as a
    # real, usable ICC number.
    assert hrnet_row["icc_rmse"] == ""
    assert hrnet_row["n_participants_with_repeats"] == "0"
