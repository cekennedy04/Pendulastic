import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np

import reliability_stats as rs


def test_bland_altman_zero_diff_gives_zero_bias_and_loa():
    x = np.array([10.0, 20.0, 30.0, 40.0])
    y = x.copy()
    result = rs.bland_altman(x, y)
    assert result["bias"] == 0.0
    assert result["sd"] == 0.0
    assert result["loa_lo"] == 0.0
    assert result["loa_hi"] == 0.0


def test_bland_altman_constant_offset_gives_that_bias():
    x = np.array([10.0, 20.0, 30.0, 40.0])
    y = x + 5.0
    result = rs.bland_altman(x, y)
    assert abs(result["bias"] - 5.0) < 1e-9
    assert result["sd"] == 0.0


def test_bland_altman_drops_nonfinite_pairs():
    x = np.array([10.0, np.nan, 30.0])
    y = np.array([10.0, 20.0, np.nan])
    result = rs.bland_altman(x, y)
    assert len(result["diffs"]) == 1


def test_icc_one_way_identical_groups_gives_high_icc():
    groups = [[10.0, 10.1, 9.9], [20.0, 20.2, 19.8], [30.0, 30.1, 29.9]]
    result = rs.icc_one_way(groups)
    assert result["icc"] > 0.9


def test_icc_one_way_pure_noise_gives_low_icc():
    rng = np.random.RandomState(0)
    groups = [list(rng.normal(0, 10, 5)) for _ in range(5)]
    result = rs.icc_one_way(groups)
    assert result["icc"] < 0.5


def test_icc_one_way_too_few_groups_returns_nan():
    result = rs.icc_one_way([[1.0, 2.0]])
    assert np.isnan(result["icc"])


def test_icc_two_way_identical_series_gives_icc_near_one():
    x = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    y = x.copy()
    result = rs.icc_two_way(x, y)
    assert result["icc"] > 0.95
