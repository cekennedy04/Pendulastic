import os, sys, math, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import workbench_engine as engine


def _decaying_oscillation_with_tail(n_osc_cycles=4, tail_s=10.0, fs=100.0):
    """Synthetic knee-angle-like signal: decaying oscillation for a few
    cycles (mirrors a real pendulum-test trial), then a long flat resting
    tail -- the exact shape diagnose_area_ratio.py's P5 T3/T5 finding was
    about (a naive full-series integral gets diluted/inflated by the tail)."""
    t_osc = np.arange(0, n_osc_cycles * 1.0, 1.0 / fs)
    decay = np.exp(-0.5 * t_osc)
    osc = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t_osc)
    t_tail = np.arange(t_osc[-1] + 1.0 / fs, t_osc[-1] + tail_s, 1.0 / fs)
    tail = np.full_like(t_tail, 140.0)
    t = np.concatenate([t_osc, t_tail])
    angle = np.concatenate([osc, tail])
    return t, angle, fs, n_osc_cycles


def test_active_window_end_excludes_long_resting_tail():
    t, angle, fs, n_osc_cycles = _decaying_oscillation_with_tail()
    end_i = engine._active_window_end(t, angle)
    n_osc_samples = int(n_osc_cycles * fs)
    assert end_i < n_osc_samples + int(0.5 * fs) + 5
    assert end_i < len(t) - 1


def test_active_window_end_no_extrema_returns_full_series():
    t = np.arange(0, 5.0, 0.01)
    angle = np.full_like(t, 180.0)
    end_i = engine._active_window_end(t, angle)
    assert end_i == len(t) - 1


def test_active_window_end_too_short_series_returns_last_index():
    t = np.array([0.0, 0.01])
    angle = np.array([180.0, 179.0])
    end_i = engine._active_window_end(t, angle)
    assert end_i == 1


def test_compare_pair_identical_signals_zero_error():
    t = np.arange(0, 5, 1 / 60)
    y = 180 - 30 * np.abs(np.sin(2 * np.pi * 1.0 * t))
    result = engine.compare_pair(t, y, t, y)
    assert result["status"] == "ok"
    assert result["rmse_deg"] < 1e-6
    assert result["mae_deg"] < 1e-6


def test_compare_pair_nan_samples_are_filtered_not_propagated():
    """OptiTrack marker occlusion can produce NaN samples. np.interp does
    not handle NaN gracefully on its own (it propagates and corrupts
    neighboring interpolated points across a gap), so this must be filtered
    before synchronize_signals ever sees it."""
    t = np.arange(0, 5, 1 / 60)
    y_ref = 180 - 30 * np.abs(np.sin(2 * np.pi * 1.0 * t))
    y_test = y_ref.copy()
    y_test[10:15] = np.nan
    result = engine.compare_pair(t, y_ref, t, y_test)
    assert result["status"] == "ok"
    assert math.isfinite(result["rmse_deg"])
    assert result["rmse_deg"] < 1.0


def test_compare_pair_lag_override_shifts_test_signal():
    t = np.arange(0, 5, 1 / 60)
    y = 180 - 30 * np.abs(np.sin(2 * np.pi * 1.0 * t))
    shifted_t = t + 0.2
    result_manual = engine.compare_pair(t, y, shifted_t, y, lag_override_sec=-0.2)
    assert result_manual["status"] == "ok"
    assert abs(result_manual["lag_sec"] - (-0.2)) < 1e-9
    assert result_manual["rmse_deg"] < 1.0


def test_compare_pair_ignores_divergent_resting_tail():
    """Active-window masking (Section 4a): a trace that agrees during the
    active oscillation but wildly diverges afterward (e.g. tracking drift
    once settled) must not have that divergence dominate the score."""
    fs = 60.0
    t_osc = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.6 * t_osc)
    osc = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t_osc)
    t_tail = np.arange(t_osc[-1] + 1.0 / fs, t_osc[-1] + 10.0, 1.0 / fs)
    ref_tail = np.full_like(t_tail, 140.0)
    test_tail = np.full_like(t_tail, 140.0) + 50.0
    ref_t = np.concatenate([t_osc, t_tail])
    ref_y = np.concatenate([osc, ref_tail])
    test_t = ref_t.copy()
    test_y = np.concatenate([osc, test_tail])

    result = engine.compare_pair(ref_t, ref_y, test_t, test_y)
    assert result["status"] == "ok"
    assert result["rmse_deg"] < 5.0


def test_compare_pair_no_overlap_returns_error():
    t1 = np.arange(0, 2, 1 / 60)
    t2 = np.arange(10, 12, 1 / 60)
    y = np.full_like(t1, 180.0)
    result = engine.compare_pair(t1, y, t2, y)
    assert result["status"] == "error"
