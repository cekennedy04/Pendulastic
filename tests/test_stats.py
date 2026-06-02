"""Tests for reliability and validity statistical functions."""

import numpy as np
import pytest

from pendulastic.stats import (
    bland_altman,
    minimal_detectable_change,
    receiver_operating_characteristic,
    spearman_correlation,
    standard_error_of_measurement,
)


def test_bland_altman_perfect_agreement():
    a = np.array([10.0, 20.0, 30.0, 40.0])
    result = bland_altman(a, a)
    assert result["mean_diff"] == pytest.approx(0.0)
    assert result["std_diff"] == pytest.approx(0.0)


def test_bland_altman_known_bias():
    a = np.array([10.0, 20.0, 30.0])
    b = a + 5.0
    result = bland_altman(a, b)
    assert result["mean_diff"] == pytest.approx(-5.0)


def test_sem_formula():
    sem = standard_error_of_measurement(icc=0.9, sd_total=10.0)
    assert sem == pytest.approx(10.0 * np.sqrt(0.1), rel=1e-4)


def test_mdc_formula():
    sem = 3.162
    mdc = minimal_detectable_change(sem)
    assert mdc == pytest.approx(sem * 1.96 * np.sqrt(2), rel=1e-3)


def test_spearman_perfect_correlation():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = spearman_correlation(x, x)
    assert result["rho"] == pytest.approx(1.0, abs=1e-3)
    assert result["p_value"] < 0.05


def test_roc_auc_perfect_classifier():
    y_true = np.array([0, 0, 1, 1])
    y_score = np.array([0.1, 0.2, 0.8, 0.9])
    result = receiver_operating_characteristic(y_true, y_score)
    assert result["auc"] == pytest.approx(1.0)


def test_icc_not_implemented():
    import pandas as pd
    from pendulastic.stats import intraclass_correlation
    df = pd.DataFrame({"r1": [1, 2, 3], "r2": [1, 2, 3]})
    with pytest.raises(NotImplementedError):
        intraclass_correlation(df)
