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


def test_windowed_pt_params_zero_for_flat_signal():
    t = np.arange(0, 3.0, 1 / 60)
    angle = np.full_like(t, 180.0)
    result = engine.windowed_pt_params(t, angle)
    assert result["area_ratio"] == 0.0
    assert result["N"] == 0.0


def test_windowed_pt_params_area_ratio_lower_than_naive_unwindowed_calc():
    """Regression test for diagnose_area_ratio.py's own P5 T3/T5 finding:
    naively integrating P+/P- over the full series (including a long
    resting tail) inflates area_ratio relative to windowing to the active
    oscillation only.

    The tail is a slow one-directional ramp (145 -> 140), not a perfectly
    flat value: phi_inf is estimated from only the *last* second of the
    series (~140, the ramp's endpoint), so for nearly the entire 15s tail
    angle > phi_inf, feeding almost exclusively into P_minus with no
    offsetting P_plus contribution -- a genuinely one-directional,
    non-self-cancelling imbalance whose size scales with tail duration. (A
    perfectly flat tail wouldn't exercise this: it would sit exactly at
    whatever phi_inf gets estimated from it either way, contributing ~zero
    net area regardless of windowing.) _active_window_end ends the active
    window shortly after the oscillation's last real extremum (~4.5s),
    well before the ramp even starts, so windowed_pt_params never sees it."""
    fs = 60.0
    t_osc = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.5 * t_osc)
    osc = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t_osc)
    t_tail = np.arange(t_osc[-1] + 1.0 / fs, t_osc[-1] + 15.0, 1.0 / fs)
    tail = np.linspace(145.0, 140.0, len(t_tail))
    t = np.concatenate([t_osc, t_tail])
    angle = np.concatenate([osc, tail])

    windowed = engine.windowed_pt_params(t, angle)

    phi_inf = float(np.median(angle[-int(fs):]))
    phi_full = phi_inf - angle
    dt_full = np.diff(t)
    phi_mid_full = (phi_full[:-1] + phi_full[1:]) / 2.0
    p_plus_full = float(np.sum(dt_full * np.maximum(phi_mid_full, 0)))
    p_minus_full = float(np.sum(dt_full * np.maximum(-phi_mid_full, 0)))
    naive_area_ratio = abs(p_plus_full - p_minus_full) / (p_plus_full + p_minus_full)

    assert windowed["area_ratio"] < naive_area_ratio


def test_windowed_pt_params_finds_expected_oscillation_count():
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.4 * t)
    angle = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t)
    result = engine.windowed_pt_params(t, angle)
    assert result["N"] >= 2.0
    assert result["f"] > 0.5


def test_extrema_jitter_finds_known_peak_and_trough_times():
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    angle = 140.0 + 30.0 * np.cos(2 * np.pi * 1.0 * t)
    result = engine.extrema_jitter(t, angle)
    assert len(result["tr_i"]) >= 2
    first_trough_t = t[result["tr_i"][0]]
    assert abs(first_trough_t - 0.5) < 0.05


def test_extrema_jitter_timing_offset_between_two_modalities():
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    angle_a = 140.0 + 30.0 * np.cos(2 * np.pi * 1.0 * t)
    angle_b = 140.0 + 30.0 * np.cos(2 * np.pi * 1.0 * (t - 0.05))
    ja = engine.extrema_jitter(t, angle_a)
    jb = engine.extrema_jitter(t, angle_b)
    offset = jb["cycle_times"][0] - ja["cycle_times"][0]
    assert abs(offset - 0.05) < 0.02


def test_extrema_jitter_too_short_series_returns_empty():
    t = np.array([0.0, 0.01])
    angle = np.array([180.0, 179.0])
    result = engine.extrema_jitter(t, angle)
    assert len(result["pk_i"]) == 0
    assert len(result["tr_i"]) == 0
    assert len(result["cycle_times"]) == 0


def _write_jsonl(path, samples):
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")


def _solo_hold_then_burst_samples():
    """Same fixture shape as tests/test_imu_calibration_tuner.py's own
    helper: hold still for 1s, a scripted 2.0 rad/s burst around Y for
    0.5s, then hold again."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
    for _ in range(100):
        t += dt
        ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    for _ in range(50):
        t += dt
        ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
    for _ in range(100):
        t += dt
        ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    return samples


def test_load_imu_trial_reproduces_hand_computed_rotation(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    _write_jsonl(path, _solo_hold_then_burst_samples())
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t, angle = engine.load_imu_trial(str(path), config=config)
    expected_final = 180.0 - math.degrees(2.0 * 0.5)
    assert abs(angle[-1] - expected_final) < 1.0
    assert np.isfinite(angle).all()
    assert np.isfinite(t).all()


def test_load_imu_trial_ft_ratio_override_changes_ockendon_output(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    _write_jsonl(path, _solo_hold_then_burst_samples())
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "ockendon"}
    t1, angle1 = engine.load_imu_trial(str(path), config=config)
    t2, angle2 = engine.load_imu_trial(str(path), config=config, ft_ratio=1.5)
    assert abs(angle1[-1] - angle2[-1]) > 0.5


def test_load_imu_trial_method_override_forces_ockendon(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    _write_jsonl(path, _solo_hold_then_burst_samples())
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t_rel, angle_rel = engine.load_imu_trial(str(path), config=config)
    t_ock, angle_ock = engine.load_imu_trial(str(path), config=config, method="ockendon")
    assert abs(angle_rel[-1] - angle_ock[-1]) > 1.0


def test_load_imu_trial_skips_malformed_lines(tmp_path):
    path = tmp_path / "trial_raw.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"t": 0.0, "role": "distal", "sensor": "accel", "v": [0,0,9.81], "phone_ts_ms": 0}\n')
        f.write("not valid json\n")
    t, angle = engine.load_imu_trial(str(path))
    assert len(t) == 0 and len(angle) == 0
