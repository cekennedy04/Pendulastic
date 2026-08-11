import os, sys, math, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pytest
import analysis_pipeline
import workbench_engine as engine

_IMU_REFERENCE_HEADER = ("t_epoch,t_rel,phone_ts_ms,t_phone_aligned,"
                         "hip_roll_deg,hip_pitch_deg,hip_yaw_deg,"
                         "prox_roll,prox_pitch,prox_yaw,"
                         "dist_roll,dist_pitch,dist_yaw,paired\n")


def _write_component_csv(path, kind, rows):
    """rows: list of tuples matching engine._COMPONENT_HEADERS[kind]'s column order."""
    header = ",".join(engine._COMPONENT_HEADERS[kind]) + "\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(header)
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def _accel_rows_100hz(n=60, sensor_name="Accelerometer", role="proximal"):
    """100 Hz cadence -- well above the 10 Hz fusion floor."""
    rows = []
    for i in range(n):
        t_ms = i * 10.0
        rows.append((t_ms, int(t_ms), role, sensor_name, 0.0, 0.0, 9.81))
    return rows


def _imu_reference_rows_100hz(n=60):
    rows = []
    for i in range(n):
        t_epoch = 1_700_000_000.0 + i * 0.01
        rows.append((t_epoch, i * 0.01, int(i * 10), t_epoch,
                     0.0, 180.0, 0.0, 0.0, 90.0, 0.0, 0.0, 90.0, 0.0, True))
    return rows


def test_validate_component_csv_happy_path_accel(tmp_path):
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", _accel_rows_100hz())
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is True
    assert result["error"] is None
    assert result["n_samples"] == 60
    assert result["fs_eff"] == pytest.approx(100.0, rel=0.05)
    assert result["rows"][0] == {
        "t": 0.0, "role": "proximal", "sensor": "accel",
        "v": [0.0, 0.0, 9.81], "phone_ts_ms": 0,
    }


def test_validate_component_csv_happy_path_imu_reference(tmp_path):
    path = tmp_path / "Trial_1_imu.csv"
    _write_component_csv(path, "imu", _imu_reference_rows_100hz())
    result = engine.validate_component_csv(str(path), "imu")
    assert result["ok"] is True
    assert result["n_samples"] == 60
    assert result["fs_eff"] == pytest.approx(100.0, rel=0.05)
    assert result["rows"][0]["hip_pitch_deg"] == "180.0"
    assert result["rows"][0]["t_epoch"] == pytest.approx(1_700_000_000.0)


def test_validate_component_csv_missing_file(tmp_path):
    result = engine.validate_component_csv(str(tmp_path / "nope.csv"), "accel")
    assert result["ok"] is False
    assert "nope.csv" in result["error"]
    assert result["rows"] == []


def test_validate_component_csv_wrong_header(tmp_path):
    path = tmp_path / "bad_header.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write("wrong,header,columns\n1,2,3\n")
    result = engine.validate_component_csv(str(path), "gyro")
    assert result["ok"] is False
    assert "bad_header.csv" in result["error"]


def test_validate_component_csv_sensor_name_mismatch(tmp_path):
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", _accel_rows_100hz(sensor_name="Gyroscope"))
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "Gyroscope" in result["error"]
    assert "Accelerometer" in result["error"]


def test_validate_component_csv_sensor_name_mismatch_second_pairing(tmp_path):
    """A second slot/sensor pairing, distinct from the accel/Gyroscope case
    above -- confirms the mismatch check isn't accidentally hardcoded to
    one slot."""
    path = tmp_path / "Trial_1_mag.csv"
    _write_component_csv(path, "mag", _accel_rows_100hz(sensor_name="Accelerometer"))
    result = engine.validate_component_csv(str(path), "mag")
    assert result["ok"] is False
    assert "Accelerometer" in result["error"]
    assert "Magnetometer" in result["error"]


def test_validate_component_csv_non_monotonic_timestamps(tmp_path):
    rows = _accel_rows_100hz(n=5)
    rows[3] = (5.0, 5, "proximal", "Accelerometer", 0.0, 0.0, 9.81)   # earlier than row 2's 20.0
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "row 5" in result["error"]


def test_validate_component_csv_fs_eff_below_floor(tmp_path):
    rows = [(i * 500.0, int(i * 500), "proximal", "Accelerometer", 0.0, 0.0, 9.81)
           for i in range(5)]   # 500ms spacing == 2 Hz, below the 10 Hz floor
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "2.00 Hz" in result["error"]


def test_validate_component_csv_fs_eff_at_floor_is_not_a_false_positive(tmp_path):
    rows = [(i * 80.0, int(i * 80), "proximal", "Accelerometer", 0.0, 0.0, 9.81)
           for i in range(5)]   # 80ms spacing == 12.5 Hz, above the 10 Hz floor
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is True


def test_validate_component_csv_mag_below_floor_is_still_ok(tmp_path):
    """Regression test for the 2026-08-11 fix: magnetometer correction is
    never fed into the AHRS (mag=None always passed to
    MadgwickAHRS.update()), so a slow/sparse mag stream cannot degrade
    fusion and must not block an otherwise-valid trial. Real Sensor Stream
    recordings confirmed this happens (~1 Hz mag while accel/gyro run
    ~100 Hz) -- unlike accel/gyro, which must still be rejected below the
    floor (see test_validate_component_csv_fs_eff_below_floor)."""
    rows = [(i * 1000.0, int(i * 1000), "proximal", "Magnetometer", 0.0, 0.0, 22.6)
           for i in range(5)]   # 1000ms spacing == 1 Hz, below the 10 Hz floor
    path = tmp_path / "Trial_1_mag.csv"
    _write_component_csv(path, "mag", rows)
    result = engine.validate_component_csv(str(path), "mag")
    assert result["ok"] is True


def test_validate_component_csv_too_few_rows(tmp_path):
    rows = [(0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 9.81)]
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "1 data row" in result["error"]


def test_validate_component_csv_non_numeric_value_in_numeric_field(tmp_path):
    """Test that non-numeric values in numeric fields are caught and reported
    without raising ValueError."""
    path = tmp_path / "Trial_1_accel.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n")
        f.write("0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n")
        f.write("bad_value,10,proximal,Accelerometer,0.0,0.0,9.81\n")  # bad timestamp
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "row 3" in result["error"]
    assert "non-numeric" in result["error"].lower()


def test_validate_component_csv_identical_timestamps_zero_gap(tmp_path):
    """Test that all identical timestamps (zero median gap) are caught and reported
    without raising ZeroDivisionError."""
    path = tmp_path / "Trial_1_accel.csv"
    rows = [(0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 9.81) for _ in range(5)]  # all same time
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "zero" in result["error"].lower() or "invalid gaps" in result["error"].lower()


def test_validate_component_csv_imu_skips_hash_preamble(tmp_path):
    """Regression for the real recorder's start_recording() preamble: it
    writes a few '# key,value' metadata lines plus '# sync_state',
    '# sync_offset_s', '# sync_jitter_s' rows before the real 14-column
    header row. validate_component_csv must skip them rather than treating
    the first preamble line as the header (0 of 9 real _imu.csv files in
    this repo's Recordings/ folder validated before this fix)."""
    path = tmp_path / "Trial_1_imu.csv"
    header = ",".join(engine._COMPONENT_HEADERS["imu"]) + "\n"
    rows = _imu_reference_rows_100hz(n=10)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("# session_id,abc123\n")
        f.write("# participant,P1\n")
        f.write("# sync_state,synced\n")
        f.write("# sync_offset_s,0.012345\n")
        f.write("# sync_jitter_s,0.000210\n")
        f.write(header)
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")
    result = engine.validate_component_csv(str(path), "imu")
    assert result["ok"] is True
    assert result["n_samples"] == 10


def test_validate_component_csv_wrong_header_imu(tmp_path):
    """Header-mismatch check for the imu kind specifically -- only gyro was
    previously covered."""
    path = tmp_path / "bad_header_imu.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write("wrong,header,columns\n1,2,3\n")
    result = engine.validate_component_csv(str(path), "imu")
    assert result["ok"] is False
    assert "bad_header_imu.csv" in result["error"]


def test_validate_component_csv_non_monotonic_timestamps_imu(tmp_path):
    """Monotonicity-violation check for the imu kind's t_epoch column --
    previously only accel/gyro/mag were covered."""
    rows = list(_imu_reference_rows_100hz(n=5))
    bad = list(rows[3])
    bad[0] = rows[1][0]   # earlier t_epoch than row index 2's (the previous row)
    rows[3] = tuple(bad)
    path = tmp_path / "Trial_1_imu.csv"
    _write_component_csv(path, "imu", rows)
    result = engine.validate_component_csv(str(path), "imu")
    assert result["ok"] is False
    assert "row 5" in result["error"]


def test_validate_component_csv_fs_eff_below_floor_imu(tmp_path):
    """fs-floor check for the imu kind -- previously only accel was
    covered."""
    rows = [(1_700_000_000.0 + i * 0.5, i * 0.5, int(i * 500), 1_700_000_000.0 + i * 0.5,
            0.0, 180.0, 0.0, 0.0, 90.0, 0.0, 0.0, 90.0, 0.0, True)
           for i in range(5)]   # 500ms spacing == 2 Hz, below the 10 Hz floor
    path = tmp_path / "Trial_1_imu.csv"
    _write_component_csv(path, "imu", rows)
    result = engine.validate_component_csv(str(path), "imu")
    assert result["ok"] is False
    assert "2.00 Hz" in result["error"]


def test_validate_component_csv_column_count_mismatch(tmp_path):
    """No existing test reached the column-count-mismatch branch: the old
    wrong-header test fails earlier, at the header check, before column
    count is ever checked."""
    path = tmp_path / "Trial_1_accel.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n")
        f.write("0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n")
        f.write("10.0,10,proximal,Accelerometer,0.0,0.0\n")   # missing z
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "row 3" in result["error"]
    assert "columns" in result["error"].lower()


def test_validate_component_csv_empty_file(tmp_path):
    """A file with zero lines (header row completely missing) must return
    a graceful ok=False rather than raising."""
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert result["rows"] == []


def test_validate_component_csv_unrecognized_kind(tmp_path):
    """An unrecognized kind must return a graceful ok=False rather than
    raising KeyError."""
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", _accel_rows_100hz())
    result = engine.validate_component_csv(str(path), "barometer")
    assert result["ok"] is False
    assert "barometer" in result["error"]


def test_validate_component_csv_nan_timestamp_rejected(tmp_path):
    """A NaN timestamp must not silently pass validation: t < prev_t is
    False for NaN (defeats monotonicity), and fs_eff < floor is False for
    NaN (defeats the fs floor check) -- must be caught explicitly."""
    rows = list(_accel_rows_100hz(n=5))
    bad = list(rows[2])
    bad[0] = float("nan")
    rows[2] = tuple(bad)
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False


def test_validate_component_csv_inf_numeric_field_does_not_raise(tmp_path):
    """`inf` in phone_ts_ms raises OverflowError from int(float('inf'));
    validate_component_csv must catch it and return ok=False, not raise."""
    rows = list(_accel_rows_100hz(n=5))
    bad = list(rows[2])
    bad[1] = float("inf")   # phone_ts_ms field
    rows[2] = tuple(bad)
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False


def test_validate_component_csv_binary_file_does_not_raise(tmp_path):
    """A non-UTF-8/binary file (realistic since the UI's file filter
    includes 'All files *.*') must not raise UnicodeDecodeError."""
    path = tmp_path / "Trial_1_accel.csv"
    with open(path, "wb") as f:
        f.write(b"\xff\xfe\x00\x01\x02\x03garbage binary data\xfa\xfb")
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False


def _write_full_component_set(tmp_path, prefix="Trial_1", fs=100.0, n=150):
    """All four component files, well-formed, at a consistent fs above the
    fusion floor. The gyro rows include a real burst of motion (not
    all-zero) in the middle third of the trial: the AHRS replay engine's
    motion-detection threshold produces empty output for an all-still gyro
    signal, so a fixture meant to exercise load_imu_trial_from_components()
    end-to-end (not just validate_component_csv() in isolation) needs an
    actual rotation to fuse."""
    dt_ms = 1000.0 / fs
    gyro_rows, accel_rows, mag_rows, imu_rows = [], [], [], []

    # Create a motion pattern: hold still for ~0.5s, burst for ~0.5s, hold still for ~0.5s
    hold_samples = int(n / 3)
    burst_samples = int(n / 3)

    for i in range(n):
        t_ms = i * dt_ms
        # Add gyro burst in the middle third
        if hold_samples <= i < hold_samples + burst_samples:
            gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 2.0, 0.0))
        else:
            gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 0.0, 0.0))

        accel_rows.append((t_ms, int(t_ms), "proximal", "Accelerometer", 0.0, 0.0, 9.81))
        mag_rows.append((t_ms, int(t_ms), "proximal", "Magnetometer", -50.0, 20.0, 30.0))
        t_epoch = 1_700_000_000.0 + i / fs
        imu_rows.append((t_epoch, i / fs, int(t_ms), t_epoch,
                         0.0, 180.0, 0.0, 0.0, 90.0, 0.0, 0.0, 90.0, 0.0, True))

    paths = {
        "gyro": tmp_path / f"{prefix}_gyro.csv", "accel": tmp_path / f"{prefix}_accel.csv",
        "mag": tmp_path / f"{prefix}_mag.csv", "imu": tmp_path / f"{prefix}_imu.csv",
    }
    _write_component_csv(paths["gyro"], "gyro", gyro_rows)
    _write_component_csv(paths["accel"], "accel", accel_rows)
    _write_component_csv(paths["mag"], "mag", mag_rows)
    _write_component_csv(paths["imu"], "imu", imu_rows)
    return {kind: engine.validate_component_csv(str(p), kind) for kind, p in paths.items()}


def test_bind_split_csv_components_merges_and_sorts_fusion_samples(tmp_path):
    validations = _write_full_component_set(tmp_path)
    bound = engine.bind_split_csv_components(validations)
    ts = [s["t"] for s in bound["fusion_samples"]]
    assert ts == sorted(ts)
    assert {s["sensor"] for s in bound["fusion_samples"]} == {"gyro", "accel", "mag"}
    assert len(bound["fusion_samples"]) == 450   # 150 rows x 3 sensors


def test_bind_split_csv_components_keeps_imu_reference_separate(tmp_path):
    validations = _write_full_component_set(tmp_path)
    bound = engine.bind_split_csv_components(validations)
    assert len(bound["imu_reference"]) == 150
    assert all(s["sensor"] != "imu" for s in bound["fusion_samples"])
    assert bound["imu_reference"][0]["hip_pitch_deg"] == "180.0"


def test_bind_split_csv_components_raises_on_incomplete_set(tmp_path):
    validations = _write_full_component_set(tmp_path)
    del validations["mag"]
    try:
        engine.bind_split_csv_components(validations)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "mag" in str(e)


def test_bind_split_csv_components_raises_when_one_kind_not_ok(tmp_path):
    validations = _write_full_component_set(tmp_path)
    validations["gyro"] = {"ok": False, "error": "bad", "n_samples": 0,
                           "fs_eff": None, "rows": []}
    try:
        engine.bind_split_csv_components(validations)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "gyro" in str(e)


def test_load_imu_trial_from_components_produces_finite_angle_series(tmp_path):
    # n=350 (not the fixture's default 150): the AHRS replay's zero-capture
    # guard (2026-08-07 fix, imu_calibration_tuner._recently_calm) requires
    # a full ~1s trailing window of low-magnitude gyro before it will trust
    # a motion burst as the true release -- the default fixture's 0.5s
    # pre-burst hold doesn't leave enough margin for that on top of the
    # window itself, so the trial would never zero.
    validations = _write_full_component_set(tmp_path, n=350)
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t, angle, imu_reference = engine.load_imu_trial_from_components(validations, config=config)
    assert len(t) > 0
    assert len(angle) > 0
    assert np.isfinite(t).all()
    assert np.isfinite(angle).all()
    assert len(imu_reference) == 350


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


def test_synchronize_signals_lag_search_is_bounded():
    """Root-cause regression test for the 2026-08-08 fix: a real trial
    (Participant_14/Right/pre/Trial_4, raw signals ~30-48s long) had an
    unbounded cross-correlation search pick a lag of -18 to -21s over the
    true near-zero alignment, because a long, periodic signal can have a
    numerically higher-correlation match at a huge, physically implausible
    lag. Reproduced deterministically: a pure sinusoid repeated over many
    periods has EQUAL correlation at every period-multiple lag, so an
    unbounded search can land arbitrarily far from the true small lag
    (numpy's argmax picks the first/most-negative tie). The bounded search
    must find the true lag within analysis_pipeline.MAX_LAG_SEC instead."""
    period = 2.0
    true_lag = 0.15
    t = np.arange(0, 40, 1 / 60)   # 20 periods -- long enough for the
                                    # unbounded global argmax to land tens
                                    # of seconds from the true lag
    ref_y = np.sin(2 * np.pi * t / period)
    test_y = np.sin(2 * np.pi * (t - true_lag) / period)

    result = engine.compare_pair(t, ref_y, t, test_y)
    assert result["status"] == "ok"
    assert abs(result["lag_sec"]) <= analysis_pipeline.MAX_LAG_SEC
    # A pure periodic sinusoid ties the correlation at every period-multiple
    # lag (both +true_lag and -true_lag + k*period score identically), so
    # this doesn't assert an exact signed match -- it asserts the bounded
    # search picked one of the NEAR ties (within half a period of zero),
    # not one of the many equally-valid far ties an unbounded search could
    # have landed on arbitrarily.
    assert abs(result["lag_sec"]) < period / 2, (
        f"expected a lag near zero (within half a period), got "
        f"{result['lag_sec']}s -- the bounded search should never return "
        f"a far tied peak just because argmax happened to find it first")


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


def test_load_imu_trial_rejects_non_jsonl_path(tmp_path):
    path = tmp_path / "Trial_1_accel.csv"
    path.write_text("timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n", encoding="utf-8")
    try:
        engine.load_imu_trial(str(path))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "load_imu_trial_from_components" in str(e)


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
_SPLIT_CSV_HEADER = "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"


def _write_split_csv(path, rows):
    """rows: list of (timestamp_ms, phone_ts_ms, role, sensor_name, x, y, z)
    tuples. accel/gyro/mag share this schema (engine._COMPONENT_HEADERS)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_SPLIT_CSV_HEADER)
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def test_peak_raw_gyro_velocity_finds_known_burst(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_split_csv(str(prefix) + "_gyro.csv", [
        (0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
        (10.0, 10, "proximal", "Gyroscope", 3.0, 4.0, 0.0),   # magnitude 5.0
        (20.0, 20, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
    ])
    _write_split_csv(str(prefix) + "_accel.csv", [
        (0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 1.0),
        (20.0, 20, "proximal", "Accelerometer", 0.0, 0.0, 1.0),
    ])
    _write_split_csv(str(prefix) + "_mag.csv", [
        (0.0, 0, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (20.0, 20, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])

    peak = engine._peak_raw_gyro_velocity(str(prefix) + "_gyro.csv")
    assert peak == 5.0


def _write_accel_release_csv(path, fs_hz, baseline=1.0, step=1.5,
                             release_t_sec=1.0, duration_sec=3.0):
    """Synthetic single-axis accel signal (y=z=0, so magnitude == |x|):
    steady baseline until release_t_sec, then steps to a new level and
    stays there -- a clean, unambiguous magnitude change for the release
    detector to find. Verified empirically before writing this plan: this
    exact shape detects within ~0.1s of release_t_sec at 50/100/200 Hz."""
    dt = 1.0 / fs_hz
    n = int(duration_sec / dt)
    rows = []
    for i in range(n):
        t_ms = i * dt * 1000.0
        x = step if (i * dt) >= release_t_sec else baseline
        rows.append((t_ms, int(t_ms), "proximal", "Accelerometer", x, 0.0, 0.0))
    _write_split_csv(path, rows)


def test_accel_release_time_detects_known_step(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=100.0, release_t_sec=1.0)
    _write_split_csv(str(prefix) + "_gyro.csv", [
        (0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
        (2900.0, 2900, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
    ])
    _write_split_csv(str(prefix) + "_mag.csv", [
        (0.0, 0, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (2900.0, 2900, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])

    release_t = engine._accel_release_time(str(prefix) + "_accel.csv")
    assert release_t is not None
    assert abs(release_t - 1.0) < 0.15


def test_accel_release_time_adapts_to_actual_sample_rate(tmp_path):
    """Same release shape at two different sample rates -- both must detect
    near the same known release time, proving the filter design adapts to
    each file's own fs_eff rather than assuming one fixed rate."""
    for fs_hz in (50.0, 200.0):
        prefix = tmp_path / f"Trial_{int(fs_hz)}"
        _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=fs_hz, release_t_sec=1.0)
        release_t = engine._accel_release_time(str(prefix) + "_accel.csv")
        assert release_t is not None
        assert abs(release_t - 1.0) < 0.15, f"fs_hz={fs_hz}: got {release_t}"


def test_accel_release_time_returns_none_below_nyquist_guard(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=5.0, release_t_sec=1.0)
    release_t = engine._accel_release_time(str(prefix) + "_accel.csv")
    assert release_t is None


def test_compute_raw_sensor_diagnostics_returns_both_keys(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=100.0, release_t_sec=1.0)
    _write_split_csv(str(prefix) + "_gyro.csv", [
        (0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
        (1000.0, 1000, "proximal", "Gyroscope", 3.0, 4.0, 0.0),
        (2900.0, 2900, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
    ])
    _write_split_csv(str(prefix) + "_mag.csv", [
        (0.0, 0, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (2900.0, 2900, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])

    diagnostics = engine.compute_raw_sensor_diagnostics(str(prefix) + "_imu.csv")
    assert diagnostics["peak_gyro_velocity_dps"] == 5.0
    assert diagnostics["accel_release_time_sec"] is not None
    assert abs(diagnostics["accel_release_time_sec"] - 1.0) < 0.15


def test_release_lag_sec_returns_ref_minus_test():
    assert engine.release_lag_sec(2.5, 1.0) == pytest.approx(1.5)
    assert engine.release_lag_sec(1.0, 2.5) == pytest.approx(-1.5)


def test_release_lag_sec_rejects_non_finite_input():
    with pytest.raises(ValueError):
        engine.release_lag_sec(float("nan"), 1.0)
    with pytest.raises(ValueError):
        engine.release_lag_sec(1.0, float("inf"))


def test_release_marks_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.release_marks_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []
    assert fieldnames == ["participant_id", "session_date", "label", "t_trace", "source"]


def test_release_marks_to_csv_rows_one_row_per_trace():
    release_marks = {
        "imu": {"t_trace": 1.23, "source": "manual"},
        "optitrack": {"t_trace": 0.98, "source": "auto"},
    }
    fieldnames, rows = engine.release_marks_to_csv_rows(release_marks, "P5", "2026-08-04")
    assert len(rows) == 2
    row = next(r for r in rows if r["label"] == "imu")
    assert row == {
        "participant_id": "P5", "session_date": "2026-08-04",
        "label": "imu", "t_trace": 1.23, "source": "manual",
    }
