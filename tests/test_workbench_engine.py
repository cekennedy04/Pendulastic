import os, sys, math, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import workbench_engine as engine

_SPLIT_CSV_HEADER = "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"


def _write_split_csv(path, rows):
    """rows: list of (timestamp_ms, phone_ts_ms, role, sensor_name, x, y, z) tuples."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_SPLIT_CSV_HEADER)
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def _write_solo_split_csv_trial(tmp_path, prefix="Trial_1"):
    """Three well-formed sibling CSVs (gyro/accel/mag), solo/proximal-only
    role: hold still for 1s, a scripted 2.0 rad/s gyro burst around Y for
    0.5s, then hold again -- the same shape (and ~2.5s total duration) as
    this file's own _solo_hold_then_burst_samples(). The duration matters:
    replay_trial ticks at a 50ms cadence, so a too-short fixture (e.g. a
    handful of samples spanning only a few milliseconds) ticks zero times
    and every test below would pass vacuously on empty arrays rather than
    actually exercising anything -- verified empirically against the real
    engine before writing this plan (254 samples in, 51 ticks out, 50/51
    finite, matching _solo_hold_then_burst_samples()'s own expected final
    angle of 180 - degrees(2.0 * 0.5) = 122.7 deg)."""
    gyro_path  = tmp_path / f"{prefix}_gyro.csv"
    accel_path = tmp_path / f"{prefix}_accel.csv"
    mag_path   = tmp_path / f"{prefix}_mag.csv"
    imu_path   = tmp_path / f"{prefix}_imu.csv"

    gyro_rows = []
    t_ms = 0.0
    for _ in range(100):
        t_ms += 10.0
        gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 0.0, 0.0))
    for _ in range(50):
        t_ms += 10.0
        gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 2.0, 0.0))
    for _ in range(100):
        t_ms += 10.0
        gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 0.0, 0.0))
    _write_split_csv(gyro_path, gyro_rows)

    _write_split_csv(accel_path, [
        (0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 9.81),
        (t_ms, int(t_ms), "proximal", "Accelerometer", 0.0, 0.0, 9.81),
    ])
    _write_split_csv(mag_path, [
        (1.0, 1, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (t_ms, int(t_ms), "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])
    imu_path.write_text("# participant,test\n", encoding="utf-8")
    return {"gyro": gyro_path, "accel": accel_path, "mag": mag_path, "imu": imu_path}


def test_read_split_csv_samples_merges_and_sorts_chronologically(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    samples = engine._read_split_csv_samples(str(paths["gyro"]))
    assert len(samples) == 254   # 250 gyro + 2 accel + 2 mag
    ts = [s["t"] for s in samples]
    assert ts == sorted(ts)
    assert {s["sensor"] for s in samples} == {"gyro", "accel", "mag"}
    assert all(s["role"] == "proximal" for s in samples)
    first_accel = next(s for s in samples if s["sensor"] == "accel")
    assert first_accel["v"] == [0.0, 0.0, 9.81]
    assert first_accel["t"] == 0.0
    assert first_accel["phone_ts_ms"] == 0


def test_read_split_csv_samples_missing_sibling_names_the_file(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    os.remove(paths["accel"])
    try:
        engine._read_split_csv_samples(str(paths["gyro"]))
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as e:
        assert "accel" in str(e)


def test_read_split_csv_samples_malformed_header_names_the_file(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    with open(paths["mag"], "w", encoding="utf-8") as f:
        f.write("wrong,header,columns\n")
        f.write("1,2,3\n")
    try:
        engine._read_split_csv_samples(str(paths["gyro"]))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "mag" in str(e).lower() or str(paths["mag"]) in str(e)


def test_read_split_csv_samples_unrecognized_sensor_name(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    _write_split_csv(paths["accel"], [
        (999.0, 999, "proximal", "Barometer", 0.0, 0.0, 9.81),
    ])
    try:
        engine._read_split_csv_samples(str(paths["gyro"]))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Barometer" in str(e)


def test_derive_split_csv_siblings_from_non_imu_anchor(tmp_path):
    """A _gyro.csv/_accel.csv/_mag.csv anchor must not be treated as if it
    were _imu.csv -- the derivation must identify the anchor's actual
    suffix, not assume a fixed one."""
    paths = _write_solo_split_csv_trial(tmp_path)
    derived = engine._derive_split_csv_siblings(str(paths["accel"]))
    assert derived["gyro"] == str(paths["gyro"])
    assert derived["accel"] == str(paths["accel"])
    assert derived["mag"] == str(paths["mag"])
    assert derived["imu"] == str(paths["imu"])


def test_load_imu_trial_dispatches_to_split_csv_for_non_jsonl_path(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t, angle = engine.load_imu_trial(str(paths["gyro"]), config=config)
    # Explicit non-empty checks, not just np.isfinite(t).all() -- that's
    # vacuously True on an empty array, so it wouldn't fail against the
    # pre-fix code (a CSV path fed through the JSONL reader silently
    # yields zero samples via its per-line `except ValueError: continue`,
    # and replay_trial([]) returns two EMPTY arrays rather than raising).
    assert len(t) > 0
    assert len(angle) > 0
    assert np.isfinite(t).all()
    assert np.isfinite(angle).all()


def test_load_imu_trial_same_result_regardless_of_which_sibling_is_the_anchor(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    results = [engine.load_imu_trial(str(paths[k]), config=config)
              for k in ("gyro", "accel", "mag", "imu")]
    assert len(results[0][0]) > 0, "sanity check: the fixture must actually produce samples"
    for t, angle in results[1:]:
        assert list(t) == list(results[0][0])
        assert list(angle) == list(results[0][1])


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


def test_load_optitrack_trial_prefers_rigid_body_when_available(monkeypatch):
    def fake_rigid_body(path):
        return np.array([0.0, 1.0]), np.array([180.0, 150.0])
    monkeypatch.setattr(engine.analysis_pipeline, "_optitrack_knee_angle_series", fake_rigid_body)
    t, angle, method = engine.load_optitrack_trial("dummy.csv")
    assert method == "rigid_body"
    assert list(angle) == [180.0, 150.0]


def test_load_optitrack_trial_falls_back_to_marker_pca_on_value_error(monkeypatch):
    def fake_rigid_body_fails(path):
        raise ValueError("Could not find both a Thigh-like and a Shank-like body with rotation data")
    def fake_pca(path):
        return np.array([0.0, 1.0]), np.array([180.0, 160.0])
    monkeypatch.setattr(engine.analysis_pipeline, "_optitrack_knee_angle_series", fake_rigid_body_fails)
    monkeypatch.setattr(engine.pendulastic_pt_score, "load_optitrack", fake_pca)
    t, angle, method = engine.load_optitrack_trial("dummy.csv")
    assert method == "marker_pca"
    assert list(angle) == [180.0, 160.0]


def test_load_video_trial_isolates_one_model_failure(monkeypatch):
    def good_model(path):
        return np.array([0.0, 1.0]), np.array([180.0, 150.0])
    def bad_model(path):
        raise RuntimeError("ONNX weights missing")
    monkeypatch.setattr(engine.analysis_pipeline, "MODEL_FUNCTIONS",
                        {"good": good_model, "bad": bad_model})
    results = engine.load_video_trial("dummy.mp4", ["good", "bad"])
    t, angle = results["good"]
    assert list(angle) == [180.0, 150.0]
    assert "error" in results["bad"]
    assert "ONNX weights missing" in results["bad"]["error"]


def test_load_video_trial_reports_progress(monkeypatch):
    def m1(path):
        return np.array([0.0]), np.array([180.0])
    def m2(path):
        return np.array([0.0]), np.array([180.0])
    monkeypatch.setattr(engine.analysis_pipeline, "MODEL_FUNCTIONS", {"m1": m1, "m2": m2})
    seen = []
    engine.load_video_trial("dummy.mp4", ["m1", "m2"], progress_cb=seen.append)
    assert seen == [0.5, 1.0]


def test_load_video_trial_unknown_model_name_reports_error():
    results = engine.load_video_trial("dummy.mp4", ["nonexistent_model_xyz"])
    assert "error" in results["nonexistent_model_xyz"]


def test_export_session_bundles_all_three_sections():
    trial_meta = {"imu_path": "a.jsonl", "video_path": "b.mp4", "optitrack_path": "c.csv"}
    annotations = {"Release Start": (42, 0.7), "Maximum Flexion": (88, 1.47)}
    metrics = {"mediapipe": {"rmse_deg": 5.2, "mae_deg": 3.1}}
    result = engine.export_session(trial_meta, annotations, metrics)
    assert result["trial"] == trial_meta
    assert result["annotations"]["Release Start"] == {"frame_index": 42, "t_sec": 0.7}
    assert result["metrics"] == metrics


def test_export_session_round_trips_through_json(tmp_path):
    result = engine.export_session(
        {"imu_path": "a.jsonl"}, {"Rest/Settled": (10, 0.1)}, {"imu": {"rmse_deg": 1.0}})
    path = tmp_path / "session.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded == result


def test_traces_to_csv_rows_empty_traces_returns_no_rows():
    fieldnames, rows = engine.traces_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []
    assert fieldnames == ["participant_id", "session_date", "label", "t_sec", "angle_deg"]


def test_traces_to_csv_rows_one_row_per_sample_per_trace():
    traces = {
        "imu": (np.array([0.0, 0.1, 0.2]), np.array([180.0, 170.0, 160.0])),
        "optitrack": (np.array([0.0, 0.1]), np.array([181.0, 171.0])),
    }
    fieldnames, rows = engine.traces_to_csv_rows(traces, "P5", "2026-08-04")
    assert len(rows) == 5
    imu_rows = [r for r in rows if r["label"] == "imu"]
    assert len(imu_rows) == 3
    assert imu_rows[0] == {
        "participant_id": "P5", "session_date": "2026-08-04",
        "label": "imu", "t_sec": 0.0, "angle_deg": 180.0,
    }


def test_per_trace_metrics_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.per_trace_metrics_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []


def test_per_trace_metrics_to_csv_rows_one_row_per_label():
    per_trace = {
        "imu": {"R2n": 1.1, "N": 2.0, "phi_max_ratio": 0.5, "omega_max_n": 3.0,
                "f": 1.2, "area_ratio": 0.07, "omega_min_n": 0.4},
    }
    fieldnames, rows = engine.per_trace_metrics_to_csv_rows(per_trace, "P5", "2026-08-04")
    assert rows == [{
        "participant_id": "P5", "session_date": "2026-08-04", "label": "imu",
        "area_ratio": 0.07, "N": 2.0, "f_hz": 1.2, "R2n": 1.1,
        "omega_max_n": 3.0, "omega_min_n": 0.4,
    }]


def test_vs_reference_metrics_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.vs_reference_metrics_to_csv_rows("optitrack", {}, "P5", "2026-08-04")
    assert rows == []


def test_vs_reference_metrics_to_csv_rows_ok_and_error_status():
    vs_reference = {
        "imu": {"status": "ok", "rmse_deg": 5.2, "mae_deg": 3.1, "lag_sec": 0.05,
                "timing_offset_sec": 0.12},
        "mediapipe": {"status": "error",
                      "error": "Need at least 4 finite samples in both signals."},
    }
    fieldnames, rows = engine.vs_reference_metrics_to_csv_rows(
        "optitrack", vs_reference, "P5", "2026-08-04")
    assert len(rows) == 2
    ok_row = next(r for r in rows if r["label"] == "imu")
    assert ok_row["reference"] == "optitrack"
    assert ok_row["rmse_deg"] == 5.2
    assert ok_row["error"] is None
    err_row = next(r for r in rows if r["label"] == "mediapipe")
    assert err_row["status"] == "error"
    assert err_row["rmse_deg"] is None
    assert err_row["error"] == "Need at least 4 finite samples in both signals."


def test_annotations_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.annotations_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []


def test_annotations_to_csv_rows_one_row_per_milestone():
    annotations = {"Release Start": (42, 0.7), "Maximum Flexion": (88, 1.47)}
    fieldnames, rows = engine.annotations_to_csv_rows(annotations, "P5", "2026-08-04")
    assert len(rows) == 2
    row = next(r for r in rows if r["label"] == "Release Start")
    assert row == {
        "participant_id": "P5", "session_date": "2026-08-04",
        "label": "Release Start", "frame_index": 42, "t_sec": 0.7,
    }
