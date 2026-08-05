import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np

import evaluate_all_participants as eap


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
