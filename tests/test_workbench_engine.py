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


def test_compute_raw_sensor_diagnostics_hold_gravity_z_frac_level_hold(tmp_path):
    """A hold where gravity reads (0,0,9.81) -- perfectly level -- must
    report hold_gravity_z_frac close to 1.0."""
    base = tmp_path / "Trial_1"
    _write_component_csv(str(base) + "_accel.csv", "accel", _accel_rows_100hz(n=150))
    gyro_rows = [(i * 10.0, int(i * 10), "proximal", "Gyroscope", 0.0, 0.0, 0.0)
                for i in range(150)]
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    assert diag["hold_gravity_z_frac"] == pytest.approx(1.0, abs=1e-6)
    assert diag["hold_stillness_ok"] is True


def test_compute_raw_sensor_diagnostics_hold_gravity_z_frac_tilted_hold(tmp_path):
    """A hold whose measured gravity is spread across X/Y as well as Z
    (a real tilted-hold shape, e.g. ~[5,5,8]) must report a fraction well
    below 1.0."""
    base = tmp_path / "Trial_1"
    accel_rows = [(i * 10.0, int(i * 10), "proximal", "Accelerometer", 5.0, 5.0, 8.0)
                 for i in range(150)]
    _write_component_csv(str(base) + "_accel.csv", "accel", accel_rows)
    gyro_rows = [(i * 10.0, int(i * 10), "proximal", "Gyroscope", 0.0, 0.0, 0.0)
                for i in range(150)]
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    expected = 8.0 / (5.0 ** 2 + 5.0 ** 2 + 8.0 ** 2) ** 0.5
    assert diag["hold_gravity_z_frac"] == pytest.approx(expected, abs=1e-6)


def test_compute_raw_sensor_diagnostics_hold_stillness_ok_false_when_handled(tmp_path):
    """A hold with real gyro motion (examiner still handling the sensor)
    must report hold_stillness_ok=False, not silently pass."""
    base = tmp_path / "Trial_1"
    _write_component_csv(str(base) + "_accel.csv", "accel", _accel_rows_100hz(n=150))
    gyro_rows = [(i * 10.0, int(i * 10), "proximal", "Gyroscope",
                 1.5 if i % 2 == 0 else -1.5, 0.0, 0.0)
                for i in range(150)]   # oscillating +/-1.5 rad/s, above GYRO_STATIONARY_MAX_RAD_S
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    assert diag["hold_stillness_ok"] is False


def test_compute_raw_sensor_diagnostics_hold_fields_none_when_too_few_samples(tmp_path):
    """Fewer than GYRO_BIAS_MIN_SAMPLES in the hold window must report None
    (unknown), never a misleadingly-computed value from too little data."""
    base = tmp_path / "Trial_1"
    accel_rows = [(0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 9.81)]
    _write_component_csv(str(base) + "_accel.csv", "accel", accel_rows)
    gyro_rows = [(0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0)]
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    assert diag["hold_gravity_z_frac"] is None
    assert diag["hold_stillness_ok"] is None


def test_compute_optitrack_quality_signals_no_dropout_no_warn():
    """Clean pendulum trial: flat pre-release hold at 180°, then exponentially
    decaying oscillation settling near neutral angle. Real N>0 (peaks/troughs
    detected), low area_ratio (balanced positive/negative areas), no warnings."""
    t = np.linspace(0, 10, 400)
    hold = t < 2.0
    swing = 130.0 + 50.0 * np.exp(-0.4 * (t - 2.0)) * np.cos(2.0 * (t - 2.0))
    angle = np.where(hold, 180.0, swing)
    signals = engine.compute_optitrack_quality_signals(t, angle)
    assert signals["optitrack_dropout_frac"] == pytest.approx(0.0)
    assert signals["optitrack_area_ratio_warn"] is False


def test_compute_optitrack_quality_signals_reports_dropout_fraction():
    t = np.linspace(0, 5, 200)
    angle = 180.0 - 10.0 * np.sin(t)
    angle[:80] = np.nan   # 40% missing
    signals = engine.compute_optitrack_quality_signals(t, angle)
    assert signals["optitrack_dropout_frac"] == pytest.approx(0.40, abs=1e-6)


def test_suggest_quality_tag_no_signal_returns_none_category():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion == {"category": None, "details": ""}


def test_suggest_quality_tag_tilted_hold_suggests_calibration_hold():
    raw = {"hold_gravity_z_frac": 0.57, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "calibration_hold"
    assert "57%" in suggestion["details"]


def test_suggest_quality_tag_unstable_hold_suggests_calibration_hold():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": False}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "calibration_hold"


def test_suggest_quality_tag_high_dropout_suggests_marker_occlusion():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.45, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "marker_occlusion"
    assert "45%" in suggestion["details"]


def test_suggest_quality_tag_area_ratio_warn_suggests_marker_occlusion():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": True}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "marker_occlusion"


def test_suggest_quality_tag_calibration_hold_takes_priority_over_dropout():
    """When both a tilted hold and high dropout are present, calibration
    hold wins -- it's checked first (design spec Section 4's ordered rule
    list)."""
    raw = {"hold_gravity_z_frac": 0.50, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.60, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "calibration_hold"


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


# ---------------------------------------------------------------------------
# Capture-side start/stop skew (2026-08-28)
# ---------------------------------------------------------------------------

def test_capture_lag_prior_reads_the_skew_off_the_durations():
    """The phone IMU log is started before motive_sync's NatNet handshake
    lets Motive begin its take, so the IMU trace runs longer at the front
    by that same skew. The prior must read that straight off the two
    durations -- no cross-correlation, no release detection -- and return
    the (negative) shift that moves the IMU trace back onto the take."""
    ref_t = np.arange(0.0, 10.0, 1 / 120)          # OptiTrack, 10.0s
    test_t = np.arange(0.0, 10.6, 1 / 20)          # IMU, 0.6s longer
    prior = engine.capture_lag_prior(ref_t, test_t)
    assert prior is not None
    # dur_delta ~= +0.6 -> lag ~= -0.6 + _STOP_SKEW_SEC
    assert abs(prior - (-0.6 + engine._STOP_SKEW_SEC)) < 0.05


def test_capture_lag_prior_declines_when_durations_show_no_skew():
    """Equal-length traces (synthetic signals, or any capture that did not
    go through the motive_sync handshake) carry no metadata evidence of a
    skew, so the prior must decline and leave the existing release-anchored
    search in charge rather than inventing a shift."""
    t = np.arange(0.0, 10.0, 1 / 60)
    assert engine.capture_lag_prior(t, t) is None


def test_capture_lag_prior_declines_on_implausible_duration_delta():
    """A trace pair whose lengths differ by far more than the handshake
    could explain (one recording stopped independently -- real cases in the
    corpus differ by 5s and 18s) is not evidence of a start skew. The
    prior must decline rather than propose an 18-second shift."""
    ref_t = np.arange(0.0, 10.0, 1 / 120)
    assert engine.capture_lag_prior(ref_t, np.arange(0.0, 28.0, 1 / 20)) is None
    assert engine.capture_lag_prior(ref_t, np.arange(0.0, 4.0, 1 / 20)) is None


def test_compare_pair_recovers_alignment_the_free_search_loses():
    """Regression test for the 2026-08-28 fix. A ~1 Hz decaying pendulum
    swing correlates almost as well a whole cycle off as it does at the
    truth, so the release-anchored search can settle on the wrong peak and
    score a well-tracked trial in double digits -- Participant_19's four
    post trials all landed at a lag of -0.08 to -0.13s where the real skew
    was -0.78s, scoring 10.3-11.5 deg instead of 2.6-4.7 deg. Reproduced
    here deterministically: with the pre-fix +/-5s bound and no prior this
    fixture aligned at +0.25s, a full cycle from the true -0.75s. The
    durations pin the skew, so it must now land near the truth. (The
    tightened MAX_LAG_SEC alone also rescues this clean synthetic -- on the
    real corpus, where the traces are noisy, the prior is what does it.)"""
    fs_ref, fs_test = 120.0, 20.0
    start_skew = 0.75      # IMU log opens this far before Motive's take
    stop_offset = 0.20     # ...and closes this far before it (STOP_FLUSH)
    imu_duration = 9.0
    opti_duration = imu_duration + stop_offset - start_skew

    def swing(u):
        """Leg held at the release angle, then let go at u=0."""
        u = np.maximum(np.asarray(u, dtype=float), 0.0)
        return 160.0 - 35.0 * np.exp(-0.5 * u) * np.cos(2 * np.pi * 1.0 * u)

    # Each trace is timestamped from its own first sample, so the shared wall
    # clock survives only in the durations -- exactly as it does on disk.
    # Release sits at start_skew in the IMU's base and at 0.0 in Motive's.
    test_t = np.linspace(0.0, imu_duration, int(round(imu_duration * fs_test)) + 1)
    test_y = swing(test_t - start_skew)
    ref_t = np.linspace(0.0, opti_duration, int(round(opti_duration * fs_ref)) + 1)
    ref_y = swing(ref_t)

    result = engine.compare_pair(ref_t, ref_y, test_t, test_y,
                                 capture_skew_prior=True)
    assert result["status"] == "ok"
    # Two IMU samples at 20 Hz. The trace cannot localise a shift finer than
    # that, and it still separates the truth from the cycle-off alias by an
    # order of magnitude.
    assert abs(result["lag_sec"] - (-start_skew)) < 0.1, (
        f"expected the recovered lag near {-start_skew}s, got "
        f"{result['lag_sec']}s -- the durations should have pinned it")
    assert result["rmse_deg"] < 5.0

    # And the alias it used to pick must still be visibly worse, so this
    # test fails loudly if the prior ever stops being consulted.
    aliased = engine.compare_pair(ref_t, ref_y, test_t, test_y,
                                  lag_override_sec=0.25)
    assert aliased["rmse_deg"] > 3 * result["rmse_deg"]


def test_capture_skew_prior_is_not_consulted_unless_requested(monkeypatch):
    """The prior is IMU-only: it reads the start skew off the two recording
    durations, which holds for a phone IMU log but not for video, whose
    files are trimmed and re-encoded independently of the capture window.
    Measured on 77 cached MediaPipe trials it had no relationship to the
    true lag (corr +0.02, residual sd 0.48s) and switching it on made RMSE
    worse (27.6 -> 31.7 deg median). Asserting on the resulting lag is too
    weak a check -- the tightened MAX_LAG_SEC bound already aligns a clean
    synthetic correctly on its own -- so this asserts the contract itself:
    the prior must not even be CONSULTED without an explicit opt-in."""
    calls = []
    real = engine.capture_lag_prior

    def spy(ref_t, test_t):
        calls.append(1)
        return real(ref_t, test_t)

    monkeypatch.setattr(engine, "capture_lag_prior", spy)

    t = np.arange(0.0, 6.0, 1 / 60)
    ref_y = 160.0 - 30.0 * np.exp(-0.5 * t) * np.cos(2 * np.pi * 1.0 * t)
    test_t = np.arange(0.0, 6.5, 1 / 20)
    test_y = 160.0 - 30.0 * np.exp(-0.5 * test_t) * np.cos(2 * np.pi * 1.0 * test_t)

    engine.compare_pair(t, ref_y, test_t, test_y)
    assert calls == [], (
        "compare_pair consulted the capture-skew prior without being asked -- "
        "that silently changes every video/HPE comparison too")

    engine.compare_pair(t, ref_y, test_t, test_y, capture_skew_prior=True)
    assert calls, "capture_skew_prior=True did not reach capture_lag_prior"


def test_max_lag_sec_excludes_physically_unreachable_skew():
    """The capture skew is bounded by motive_sync's handshake (~1.2s worst
    case), so the lag search must not be free to roam multiple seconds. The
    old +/-5s bound placed 22 of 77 real video trials at |lag| > 2.5s."""
    assert analysis_pipeline.MAX_LAG_SEC <= 1.5


def test_replay_samples_uses_the_native_analysis_grid():
    # Workbench comparisons are analysis, not display: replaying at the
    # 20 Hz display cadence put compute_pt_params' 0.10 s smoothing window
    # below savgol's floor, so every PT parameter it derived was filtered
    # over 0.25 s while the 120 Hz OptiTrack reference used 0.10 s.
    import numpy as np
    import imu_calibration_tuner as tuner
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
              "gravity_seed": True, "method": "relative"}
    t, _angle = engine._replay_samples(_solo_hold_then_burst_samples(),
                                       config, None, None)
    assert len(t) > 50
    assert float(np.median(np.diff(t))) == pytest.approx(
        tuner.ANALYSIS_TICK_S, rel=1e-6)


# ── compare_pair: polarity of the pair ───────────────────────────────────────
#
# The optical marker path returns a relative curve whose sense is a hinge
# eigenvector's, so about half the references arrive upside down. 4792020
# flipped any curve whose first 5% of samples sat below its settled tail; the
# independent review (docs/reports/2026-11-11-scorer-ports-independent-review.md
# on origin/docs/scorer-ports-review) showed that rule wrong on occluded holds
# and on recordings that open at rest, dependent on tail length, and tested on
# the one curve shape that works. Every shape the review names is here, and
# where the review says the old rule is wrong the test first PROVES it is
# wrong on this very curve (_rule_4792020), so the shape cannot quietly stop
# exercising the failure it was written for.


def _rule_4792020(y):
    """The rejected rule, verbatim from 4792020's _orient_release_positive,
    reduced to its verdict: True when it would mirror the curve."""
    y = np.asarray(y, dtype=float)
    f = y[np.isfinite(y)]
    if len(f) < 4:
        return False
    tail = f[int(len(f) * 0.75):]
    a0 = float(np.median(f[:max(1, len(f) // 20)]) - np.median(tail))
    if not np.isfinite(a0) or abs(a0) < 1.0:
        return False
    return a0 < 0.0


def _pendulum_trial(rest_s=0.0, lift_s=0.0, hold_s=1.5, tail_s=8.0, a0=45.0,
                    f=0.9, tau=1.6, fs=60.0, drift_deg_s=0.0, level=0.0,
                    start_after_release_s=None):
    """rest -> lift -> hold -> release -> decaying swing, release-positive.

    Settles at `level`, held at `level + a0`. drift_deg_s is a slow ramp over
    the whole record. start_after_release_s crops the record to begin that
    long after the release, which is what an occluded optical hold looks
    like: the first finite sample is already in the swing."""
    n_rest, n_lift, n_hold = (int(round(s * fs)) for s in (rest_s, lift_s, hold_s))
    n_tail = int(round(tail_s * fs))
    ramp = 0.5 * (1.0 - np.cos(np.pi * (np.arange(n_lift) + 0.5) / max(n_lift, 1)))
    tau_t = np.arange(n_tail) / fs
    y = np.concatenate([
        np.zeros(n_rest), a0 * ramp, np.full(n_hold, a0),
        a0 * np.exp(-tau_t / tau) * np.cos(2 * np.pi * f * tau_t)])
    t = np.arange(len(y)) / fs
    y = y + level + drift_deg_s * t
    if start_after_release_s is not None:
        keep = t >= (n_rest + n_lift + n_hold) / fs + start_after_release_s
        t, y = t[keep] - t[keep][0], y[keep]
    return t, y


def _mirror(y):
    return 2.0 * float(np.nanmean(y)) - y


def test_release_side_reads_a_curve_that_starts_on_its_hold():
    """The existing case, with an actual hold in it."""
    t, y = _pendulum_trial(hold_s=1.5, level=135.0)
    assert engine._release_side(t, y)["sign"] == 1
    assert engine._release_side(t, _mirror(y))["sign"] == -1


def test_release_side_reads_rest_lift_hold_release():
    """Opens at rest, so start is about equal to tail. The review: the mirror
    of this curve 'is not flagged' by the start-versus-tail rule."""
    t, y = _pendulum_trial(rest_s=1.5, lift_s=0.8, hold_s=1.5)
    assert _rule_4792020(_mirror(y)) is False, "old rule should miss this mirror"
    assert engine._release_side(t, y)["sign"] == 1
    assert engine._release_side(t, _mirror(y))["sign"] == -1


def test_release_side_ignores_slow_drift_on_a_recording_that_opens_at_rest():
    """The review: with +0.3 deg/s of drift an upright rest-lift-hold-release
    curve 'is flagged inverted', because drift alone puts the tail above the
    start."""
    t, y = _pendulum_trial(rest_s=1.5, lift_s=0.8, hold_s=1.5, tail_s=10.0,
                           drift_deg_s=0.3)
    assert _rule_4792020(y) is True, "old rule should flip this upright curve"
    assert engine._release_side(t, y)["sign"] == 1
    # ...and its mirror, drift and all, is still read as upside down.
    assert engine._release_side(t, _mirror(y))["sign"] == -1


def test_release_side_abstains_on_a_resting_trace_that_only_drifts():
    """No lift, no release: start-versus-tail is then nothing but the drift,
    so the old rule flips whichever of the curve and its mirror drifts up."""
    t = np.arange(0, 12.0, 1 / 60.0)
    rng = np.random.default_rng(7)
    y = 90.0 + 0.3 * t + rng.normal(0.0, 0.05, len(t))
    assert _rule_4792020(y) is True, "old rule should decide this on drift"
    for curve in (y, _mirror(y)):
        side = engine._release_side(t, curve)
        assert side["sign"] == 0
        assert side["reason"] == "hold_not_clear_of_rest"


@pytest.mark.parametrize("start_after_release_s", [0.0, 0.15, 0.55])
def test_release_side_abstains_when_the_hold_is_missing(start_after_release_s):
    """Occluded optical hold: the first finite sample is at or after release
    (0.55 s is the first trough at 0.9 Hz). The review: 'a synthetic curve
    starting exactly at release is flagged inverted' on a 12 s trial."""
    t, y = _pendulum_trial(tail_s=12.0, start_after_release_s=start_after_release_s)
    if start_after_release_s == 0.0:
        assert _rule_4792020(y) is True, "old rule should flip this upright curve"
    for curve in (y, _mirror(y)):
        side = engine._release_side(t, curve)
        assert side["sign"] == 0
        assert side["reason"] == "no_prerelease_plateau"


def test_release_side_abstains_when_the_hold_is_mostly_a_gap():
    """Markers reappear a few samples before the release: a window with a
    handful of samples in it is not a plateau."""
    t, y = _pendulum_trial(hold_s=1.5)
    y = y.copy()
    y[:int(1.5 * 60) - 6] = np.nan
    assert engine._release_side(t, y)["reason"] == "no_prerelease_plateau"


def test_release_side_does_not_depend_on_how_long_the_tail_was_recorded():
    """Same swing, same slow drift, 6 s versus 60 s of recorded tail. The old
    window is 5% of the FILE, so the long recording drags it off the hold."""
    short_t, short_y = _pendulum_trial(hold_s=0.8, tail_s=6.0, drift_deg_s=0.3)
    long_t, long_y = _pendulum_trial(hold_s=0.8, tail_s=60.0, drift_deg_s=0.3)
    assert _rule_4792020(short_y) != _rule_4792020(long_y), (
        "old rule should change its mind with the tail length")
    assert engine._release_side(short_t, short_y)["sign"] == 1
    assert engine._release_side(long_t, long_y)["sign"] == 1
    assert engine._release_side(short_t, _mirror(short_y))["sign"] == -1
    assert engine._release_side(long_t, _mirror(long_y))["sign"] == -1


def test_release_side_dead_band_in_degrees():
    """A hold under 5 degrees from rest has no release to orient by, however
    clean the ratio. Pinned from both sides in degrees, not relative to the
    module constant, so moving the constant fails here."""
    t, y = _pendulum_trial(a0=4.8, tau=0.25, f=0.5)
    assert engine._release_side(t, y)["reason"] == "hold_not_clear_of_rest"
    t, y = _pendulum_trial(a0=5.2, tau=0.25, f=0.5)
    assert engine._release_side(t, y)["sign"] == 1


def _hold_with_big_opposite_swing(hold_frac, fs=60.0):
    """A plateau about `hold_frac` of the signal range above rest: a 10 deg
    hold followed by an oscillation large enough to set the range."""
    hold_dev = 10.0
    other = hold_dev * (1.0 - hold_frac) / hold_frac
    tau_t = np.arange(int(10 * fs)) / fs
    swing = (hold_dev * np.exp(-tau_t / 0.15)
             - other * np.exp(-tau_t / 4.0) * np.sin(2 * np.pi * 0.9 * tau_t))
    y = np.concatenate([np.full(int(1.5 * fs), hold_dev), swing])
    return np.arange(len(y)) / fs, y


def test_release_side_margin_relative_to_range():
    """The hold must clear a quarter of the range. Pinned from both sides on
    the measured ratio, in numbers: 0.29 of the range is a release side, 0.18
    is not."""
    t, y = _hold_with_big_opposite_swing(0.28)
    side = engine._release_side(t, y)
    assert 0.27 < side["a0_deg"] / side["range_deg"] < 0.31
    assert side["sign"] == 1
    t, y = _hold_with_big_opposite_swing(0.20)
    side = engine._release_side(t, y)
    assert 0.16 < side["a0_deg"] / side["range_deg"] < 0.20
    assert side["a0_deg"] > 9.0, "well clear of the dead-band in degrees"
    assert side["sign"] == 0 and side["reason"] == "hold_not_clear_of_rest"


def test_polarity_from_correlation_margin_boundaries():
    """The winning sense must reach 0.5 and lead by 0.3. In numbers, so that
    moving either constant fails here."""
    f = engine._polarity_from_correlation
    assert f(0.5, -0.5) == 1 and f(-0.5, 0.5) == -1
    assert f(0.45, -0.9) == 0 and f(-0.9, 0.45) == 0, "under 0.5: no verdict"
    assert f(0.9, 0.59) == 1 and f(0.59, 0.9) == -1
    assert f(0.9, 0.65) == 0 and f(0.65, 0.9) == 0, "lead of 0.25: no verdict"
    assert f(None, 0.9) == 0 and f(0.9, None) == 0


def test_decide_relative_polarity_table():
    up = {"sign": 1, "reason": None}
    down = {"sign": -1, "reason": None}
    unknown = {"sign": 0, "reason": "no_prerelease_plateau"}
    d = engine._decide_relative_polarity
    for blocked in (None, "with_unpinned_lag"):
        assert d(up, down, -0.9, 0.9, blocked) == {
            "relative": -1, "basis": "shape+correlation", "reason": None}
        assert d(down, down, 0.2, 0.1, blocked)["basis"] == "shape"
        assert d(down, down, 0.2, 0.1, blocked)["relative"] == 1
        conflict = d(up, up, -0.9, 0.9, blocked)
        assert conflict["relative"] == 0
        assert conflict["reason"] == "shape_correlation_conflict"
        neither = d(unknown, up, 0.4, 0.3, blocked)
        assert neither["relative"] == 0
        assert neither["reason"] == (
            "correlation_inside_margin (ref: no_prerelease_plateau)")
        # Opposition on the correlation alone is never a verdict, whatever
        # the caller says about the lag: it would mirror a curve on the fit.
        opposed = d(unknown, up, -0.9, 0.9, blocked)
        assert opposed["relative"] == 0 and opposed["basis"] is None
        assert opposed["reason"] == (
            "correlation_alone_never_mirrors (ref: no_prerelease_plateau)")
        # No correlation at all is "undefined", not "inside the margin".
        for r_pair in ((None, None), (0.9, None), (None, -0.9)):
            assert d(unknown, up, *r_pair, blocked)["reason"] == (
                "correlation_undefined (ref: no_prerelease_plateau)")
    # The correlation alone may confirm a pair as given, and only unblocked.
    assert d(unknown, up, 0.9, -0.9, None) == {
        "relative": 1, "basis": "correlation", "reason": None}
    free = d(unknown, up, 0.9, -0.9, "with_unpinned_lag")
    assert free["relative"] == 0 and free["basis"] is None
    assert free["reason"] == (
        "correlation_alone_with_unpinned_lag (ref: no_prerelease_plateau)")


def test_compare_pair_mirrors_an_upside_down_relative_reference():
    """The corpus case: the optical curve arrives upside down on its own
    datum, against an IMU curve that does not."""
    t, imu = _pendulum_trial(rest_s=1.0, lift_s=0.8, hold_s=1.5, level=135.0)
    optical = -(imu - 135.0) + 20.0
    as_given = engine.compare_pair(t, optical, t, imu)
    assert as_given["orientation"] == "opposite_not_mirrored"
    assert as_given["ref_mirrored"] is False
    assert as_given["rmse_deg_offset_removed"] > 10.0
    r = engine.compare_pair(t, optical, t, imu, ref_polarity_arbitrary=True)
    assert r["status"] == "ok"
    assert r["orientation"] == "ref_mirrored" and r["ref_mirrored"] is True
    assert r["orientation_basis"] == "shape+correlation"
    assert r["ref_inverted"] is True and r["test_inverted"] is False
    assert r["rmse_deg_offset_removed"] < 0.5
    # Mirrored about its own mean, not negated: the arbitrary offset stays
    # about where it was (the scored window moves with the lag, so not exactly).
    assert r["bias_deg"] == pytest.approx(as_given["bias_deg"], abs=5.0)


def test_compare_pair_leaves_a_pair_of_the_same_sense_alone():
    t, imu = _pendulum_trial(hold_s=1.5, level=135.0)
    _t, optical = _pendulum_trial(hold_s=1.5, a0=40.0, f=0.95)
    plain = engine.compare_pair(t, optical, t, imu)
    r = engine.compare_pair(t, optical, t, imu, ref_polarity_arbitrary=True)
    assert r["orientation"] == "as_given" and r["ref_mirrored"] is False
    assert r["ref_inverted"] is False and r["test_inverted"] is False
    for key in ("rmse_deg", "rmse_deg_offset_removed", "bias_deg", "lag_sec",
                "n_samples", "mae_deg", "loa_lower_deg", "loa_upper_deg"):
        assert r[key] == plain[key]
    assert r["rmse_deg_offset_removed"] > 0.5, "different curves must still disagree"


def test_compare_pair_when_it_is_the_test_curve_that_is_inverted():
    """The relative polarity is what the RMSE needs; WHICH curve is upside
    down is reported, and the test curve is never the one that is changed."""
    t, optical = _pendulum_trial(hold_s=1.5)
    imu_inverted = 180.0 - optical
    r = engine.compare_pair(t, optical, t, imu_inverted, ref_polarity_arbitrary=True)
    assert r["orientation"] == "ref_mirrored"
    assert r["ref_inverted"] is False and r["test_inverted"] is True
    assert r["rmse_deg_offset_removed"] < 0.5
    # A reference with a real zero is not mirrored to hide it: the error stays.
    kept = engine.compare_pair(t, optical, t, imu_inverted)
    assert kept["orientation"] == "opposite_not_mirrored"
    assert kept["test_inverted"] is True and kept["ref_mirrored"] is False
    assert kept["rmse_deg_offset_removed"] > 10.0


@pytest.mark.parametrize("ref_upside_down", [False, True])
def test_compare_pair_with_a_take_that_starts_at_the_release(ref_upside_down):
    """The take STARTS at release, so the reference has no release side of its
    own, and nothing is missing from it: the duration prior is right. The old
    rule gets the upright one wrong. The correlation may confirm the same-sense
    pair. It may not mirror the opposite one: with no release side behind it
    that would be choosing the reported number by the fit."""
    # Capture geometry as on the rig: the take starts 0.7 s into the IMU log
    # (here, exactly at the release) and outlasts it by the stop skew, so the
    # duration prior pins the lag and the search cannot reach the next half-cycle.
    t_imu, imu = _pendulum_trial(hold_s=0.7, tail_s=12.0, level=135.0)
    t_opt, optical = _pendulum_trial(tail_s=12.0 - engine._STOP_SKEW_SEC,
                                     start_after_release_s=0.0)
    assert _rule_4792020(optical) is True, "old rule should flip this upright curve"
    if ref_upside_down:
        optical = -optical
    plain = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True)
    r = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True,
                            ref_polarity_arbitrary=True)
    assert r["ref_inverted"] is None and r["test_inverted"] is False
    assert r["ref_mirrored"] is False
    assert r["rmse_deg_offset_removed"] == plain["rmse_deg_offset_removed"]
    if ref_upside_down:
        assert r["orientation"] == "undetermined" and r["orientation_basis"] is None
        assert r["orientation_reason"] == (
            "correlation_alone_never_mirrors (ref: no_prerelease_plateau)")
        assert r["polarity_corr_mirrored"] > 0.9 > 0.0 > r["polarity_corr_as_given"]
        # What the other way up would have scored is reported for bounding a
        # summary, and is the only place that number appears.
        assert r["rmse_deg_offset_removed"] > 10.0
        assert r["rmse_deg_offset_removed_other_polarity"] < 1.0
    else:
        assert r["orientation"] == "as_given"
        assert r["orientation_basis"] == "correlation"
        assert r["rmse_deg_offset_removed"] < 1.0
        assert r["rmse_deg_offset_removed_other_polarity"] > 10.0


def _rig_take(hold_s, occluded_past_release_s=None, sign=1.0, scale=1.0,
              f=0.9, tau=2.5):
    """An IMU log, and the optical take of the same swing as the rig captures
    it: started 0.7 s into the IMU log, stopped 0.192 s after it, on its own
    clock. The take's time base is complete; its angle is NaN from the start
    of the take until `occluded_past_release_s` after the release (None: the
    markers were seen throughout). True lag is -0.7 s."""
    t_imu, imu = _pendulum_trial(hold_s=hold_s, tail_s=12.0, f=f, tau=tau, level=135.0)
    t_opt = np.arange(0.0, t_imu[-1] - 0.7 + 0.192, 1 / 120.0)
    optical = sign * scale * np.interp(t_opt + 0.7, t_imu, imu - 135.0, right=0.0)
    if occluded_past_release_s is not None:
        optical[t_opt < (hold_s - 0.7) + occluded_past_release_s] = np.nan
    return t_opt, optical, t_imu, imu


def test_rig_take_fixture_is_decided_when_the_hold_is_seen():
    """The fixture itself, markers seen throughout: both release sides and the
    correlation agree, at the true lag."""
    for sign, expected in ((1.0, "as_given"), (-1.0, "ref_mirrored")):
        t_opt, optical, t_imu, imu = _rig_take(1.2, None, sign)
        r = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True,
                                ref_polarity_arbitrary=True)
        assert r["orientation"] == expected
        assert r["orientation_basis"] == "shape+correlation"
        assert r["lag_sec"] == pytest.approx(-0.7, abs=0.02)
        assert r["rmse_deg_offset_removed"] < 0.5


@pytest.mark.parametrize("freq_hz", [0.8, 0.9, 1.0, 1.1])
@pytest.mark.parametrize("past_release_s", [0.0, 0.1, 0.2])
def test_an_occluded_hold_in_a_normally_started_take_is_never_mirrored(
        freq_hz, past_release_s):
    """The independent review's case (section 4 of
    2026-11-11-compare-pair-orientation-review.md). A SAME-sense pair; the
    take started on time and its first half-second or so is occluded. The
    duration prior is read off the finite span, so it is short by the occluded
    time, the "pinned" search lands half a cycle off, and there the mirror
    fits: a5a5cf4 returned ref_mirrored with an RMSE of 2 to 4 degrees. The
    pair must come back undetermined and scored as given."""
    t_opt, optical, t_imu, imu = _rig_take(1.2, past_release_s, f=freq_hz)
    assert np.isnan(optical[0]) and t_opt[0] == 0.0, "time base kept, angle missing"
    plain = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True)
    r = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True,
                            ref_polarity_arbitrary=True)
    assert r["polarity_corr_mirrored"] > 0.7, "the mirror does fit, half a cycle off"
    assert abs(r["lag_sec"] - (-0.7)) > 0.3, "and the pin is not where the take was"
    assert r["orientation"] == "undetermined" and r["ref_mirrored"] is False
    # At 1.1 Hz with the longest occlusion the as-given sense fits too and the
    # margin already refuses; everywhere else the mirror wins outright and the
    # only thing between it and the reported number is "never mirrors".
    refused_by = r["orientation_reason"].split(" (")[0]
    if (freq_hz, past_release_s) == (1.1, 0.2):
        assert refused_by == "correlation_inside_margin"
    else:
        assert refused_by == "correlation_alone_never_mirrors"
    assert r["rmse_deg_offset_removed"] == plain["rmse_deg_offset_removed"]


@pytest.mark.parametrize("past_release_s", [0.0, 0.1, 0.2])
def test_an_occluded_hold_does_not_let_the_correlation_confirm_a_pair(past_release_s):
    """The same take with the reference upside down. Now the half-cycle-off
    fit is the AS-GIVEN one (r 0.99, RMSE under 3 degrees for a pair that is
    in truth opposed), so refusing to mirror is not enough: the correlation
    may not confirm anything on a pin that was moved by a gap."""
    t_opt, optical, t_imu, imu = _rig_take(1.2, past_release_s, sign=-1.0)
    r = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True,
                            ref_polarity_arbitrary=True)
    assert r["polarity_corr_as_given"] > 0.9 and r["rmse_deg_offset_removed"] < 3.0
    assert r["orientation"] == "undetermined" and r["orientation_basis"] is None
    assert r["orientation_reason"] == (
        "correlation_alone_with_unverified_pin (ref: no_prerelease_plateau)")


def test_end_gap_tolerance_is_the_refine_margin():
    """A gap the lag search can absorb (under 0.15 s at the ends of the take)
    leaves the pin verified; one it cannot does not. In seconds, so moving the
    tolerance fails here."""
    block = engine._correlation_alone_block
    swing = 30.0 * np.sin(np.linspace(0.0, 20.0, 600))
    assert block(-0.7, 0.14, swing, 60.0) is None
    assert block(-0.7, 0.16, swing, 60.0) == "with_unverified_pin"
    assert block(None, 0.0, swing, 60.0) == "with_unpinned_lag"
    t = np.arange(0.0, 10.0, 0.01)
    kept = t[(t >= 0.5) & (t <= 9.75)]
    assert engine._end_gap_sec(t, kept) == pytest.approx(0.5 + 0.24, abs=1e-6)
    assert engine._end_gap_sec(t, t) == 0.0
    assert engine._end_gap_sec(np.array([np.nan, 1.0, 2.0, 3.0]), np.array([2.0, 3.0])) == 1.0


def test_end_gaps_of_the_two_curves_add(monkeypatch):
    """0.09 s missing from the end of each curve is 0.18 s of unexplained
    skew, over the 0.15 s tolerance, though neither gap is on its own. The
    re-review's surviving mutation took the larger of the two instead."""
    seen = []
    real = engine._correlation_alone_block

    def spy(capture_prior, end_gap_sec, *rest):
        seen.append(end_gap_sec)
        return real(capture_prior, end_gap_sec, *rest)

    monkeypatch.setattr(engine, "_correlation_alone_block", spy)
    t_opt, optical, t_imu, imu = _rig_take(1.2, 0.0)
    optical[t_opt > t_opt[-1] - 0.09] = np.nan
    imu = imu.copy()
    imu[t_imu > t_imu[-1] - 0.09] = np.nan
    r = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True,
                            ref_polarity_arbitrary=True)
    assert len(seen) == 1
    lead = 1.2 - 0.7        # the occluded start of the take, reference only
    assert seen[0] == pytest.approx(lead + 0.09 + 0.09, abs=0.03)
    assert seen[0] > max(lead + 0.09, 0.09) + 0.05, "a sum, not the larger gap"
    assert r["orientation"] == "undetermined"


@pytest.mark.parametrize("which", ["reference", "test"])
def test_a_gap_at_the_end_of_either_take_unverifies_the_pin(which):
    """The prior is the difference of two durations, so a gap at the end of
    EITHER curve moves it. 0.2 s missing from the resting tail: the search can
    still reach the true lag and the correlation is clear, but the pin is no
    longer something the durations vouch for. 0.1 s is inside what the refine
    margin absorbs, and the pair is confirmed."""
    def pair(gap_s):
        t_imu, imu = _pendulum_trial(hold_s=0.7, tail_s=12.0, level=135.0)
        t_opt, optical = _pendulum_trial(tail_s=12.0 - engine._STOP_SKEW_SEC,
                                         start_after_release_s=0.0)
        if which == "reference":
            optical = np.where(t_opt > t_opt[-1] - gap_s, np.nan, optical)
        else:
            imu = np.where(t_imu > t_imu[-1] - gap_s, np.nan, imu)
        return engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True,
                                   ref_polarity_arbitrary=True)
    r = pair(0.2)
    assert r["polarity_corr_as_given"] > 0.9
    assert r["orientation"] == "undetermined"
    assert r["orientation_reason"] == (
        "correlation_alone_with_unverified_pin (ref: no_prerelease_plateau)")
    r = pair(0.1)
    assert (r["orientation"], r["orientation_basis"]) == ("as_given", "correlation")


@pytest.mark.parametrize("scale, moved", [(0.10, False), (0.15, True)])
def test_correlation_alone_needs_a_reference_that_moved(scale, moved):
    """Pearson's r has no scale. A reference that swings 7 degrees end to end
    against an IMU curve that swings 77 correlates perfectly and says nothing;
    one that moves past 10 degrees over the scored overlap may confirm."""
    t_imu, imu = _pendulum_trial(hold_s=0.7, tail_s=12.0, level=135.0)
    t_opt, optical = _pendulum_trial(tail_s=12.0 - engine._STOP_SKEW_SEC,
                                     start_after_release_s=0.0)
    optical = scale * optical
    assert bool(np.ptp(optical) > 10.0) is moved
    r = engine.compare_pair(t_opt, optical, t_imu, imu, capture_skew_prior=True,
                            ref_polarity_arbitrary=True)
    assert r["polarity_corr_as_given"] > 0.99
    if moved:
        assert (r["orientation"], r["orientation_basis"]) == ("as_given", "correlation")
    else:
        assert r["orientation"] == "undetermined"
        assert r["orientation_reason"] == (
            "correlation_alone_on_a_reference_that_barely_moved "
            "(ref: no_prerelease_plateau)")


def test_correlation_alone_floors_in_degrees_and_seconds():
    """10 degrees of reference range (3rd to 97th percentile, so one spike
    does not count as motion) over at least 2 s of scored overlap."""
    block = engine._correlation_alone_block
    wave = np.sin(np.linspace(0.0, 20.0, 600))
    assert block(-0.7, 0.0, 4.6 * wave, 60.0) == "on_a_reference_that_barely_moved"
    assert block(-0.7, 0.0, 5.4 * wave, 60.0) is None
    spiked = 4.0 * wave
    spiked[300:303] = -150.0
    assert block(-0.7, 0.0, spiked, 60.0) == "on_a_reference_that_barely_moved"
    assert block(-0.7, 0.0, 30.0 * wave[:114], 60.0) == "on_too_short_an_overlap"
    assert block(-0.7, 0.0, 30.0 * wave[:126], 60.0) is None
    gappy = 30.0 * wave[:126].copy()
    gappy[:20] = np.nan
    assert block(-0.7, 0.0, gappy, 60.0) == "on_too_short_an_overlap"


@pytest.mark.parametrize("ref_upside_down", [False, True])
def test_correlation_alone_does_not_decide_when_the_lag_is_not_pinned(ref_upside_down):
    """The same pair with no capture prior. The correlation is just as lopsided
    (0.98 against 0.41), but nothing independent of polarity says the search
    window holds the true lag, and without that a mirrored swing half a cycle
    off is all a search may find. Measured on MediaPipe-versus-OptiTrack,
    which has no prior: such verdicts were wrong-way against the IMU pair's on
    16 of 50. A manual lag is no pin either: an operator sets one by matching
    the curves, and at a fixed lag the two correlations are exact negatives."""
    t_imu, imu = _pendulum_trial(hold_s=0.7, tail_s=12.0, level=135.0)
    t_opt, optical = _pendulum_trial(tail_s=12.0 - engine._STOP_SKEW_SEC,
                                     start_after_release_s=0.0)
    if ref_upside_down:
        optical = -optical
    plain = engine.compare_pair(t_opt, optical, t_imu, imu)
    r = engine.compare_pair(t_opt, optical, t_imu, imu, ref_polarity_arbitrary=True)
    assert abs(r["polarity_corr_as_given"] - r["polarity_corr_mirrored"]) > 0.5
    assert r["orientation"] == "undetermined" and r["ref_mirrored"] is False
    assert r["orientation_reason"] == (
        ("correlation_alone_never_mirrors" if ref_upside_down
         else "correlation_alone_with_unpinned_lag")
        + " (ref: no_prerelease_plateau)")
    assert r["rmse_deg_offset_removed"] == plain["rmse_deg_offset_removed"]
    plain_manual = engine.compare_pair(t_opt, optical, t_imu, imu, lag_override_sec=-0.7)
    manual = engine.compare_pair(t_opt, optical, t_imu, imu, lag_override_sec=-0.7,
                                 ref_polarity_arbitrary=True)
    assert manual["polarity_corr_as_given"] == pytest.approx(
        -manual["polarity_corr_mirrored"], abs=1e-9)
    assert manual["orientation"] == "undetermined" and manual["ref_mirrored"] is False
    assert manual["orientation_reason"] == (
        ("correlation_alone_never_mirrors" if ref_upside_down
         else "correlation_alone_with_a_manual_lag")
        + " (ref: no_prerelease_plateau)")
    assert manual["rmse_deg_offset_removed"] == plain_manual["rmse_deg_offset_removed"]


def test_a_manual_lag_does_not_stop_the_release_sides_deciding():
    """Only the correlation-alone path is closed under a manual lag. A pair
    whose two curves each show their release side is decided as before."""
    t_opt, optical, t_imu, imu = _rig_take(1.2, None, sign=-1.0)
    r = engine.compare_pair(t_opt, optical, t_imu, imu, lag_override_sec=-0.7,
                            ref_polarity_arbitrary=True)
    assert r["orientation"] == "ref_mirrored"
    assert r["orientation_basis"] == "shape+correlation"
    assert r["rmse_deg_offset_removed"] < 0.5


@pytest.mark.parametrize("ref_upside_down", [False, True])
def test_compare_pair_abstains_when_the_mirror_fits_half_a_cycle_away(ref_upside_down):
    """Same occluded reference, but a lag search wide enough to reach the
    neighbouring half-cycle, where a lightly damped swing's MIRROR also fits
    (r 0.98 against 1.00, and 4 deg of RMSE). Taking whichever fits better
    would be choosing the answer by the metric; the margin refuses."""
    t_imu, imu = _pendulum_trial(hold_s=1.5, tail_s=12.0, level=135.0)
    t_opt, optical = _pendulum_trial(tail_s=12.0, start_after_release_s=0.0)
    t_opt = t_opt + 1.5                       # same clock as the IMU curve
    if ref_upside_down:
        optical = -optical
    plain = engine.compare_pair(t_opt, optical, t_imu, imu)
    r = engine.compare_pair(t_opt, optical, t_imu, imu, ref_polarity_arbitrary=True)
    assert min(r["polarity_corr_as_given"], r["polarity_corr_mirrored"]) > 0.9
    assert r["orientation"] == "undetermined" and r["ref_mirrored"] is False
    assert r["orientation_reason"] == (
        "correlation_inside_margin (ref: no_prerelease_plateau)")
    assert r["rmse_deg_offset_removed"] == plain["rmse_deg_offset_removed"]


def test_compare_pair_abstains_on_a_noisy_low_amplitude_reference():
    """Nothing in this reference says which way up it is. The comparison still
    runs, on the curves exactly as given, and says it could not tell."""
    t, imu = _pendulum_trial(hold_s=1.5, level=135.0)
    rng = np.random.default_rng(11)
    optical = 2.0 * np.sin(2 * np.pi * 0.31 * t) + rng.normal(0.0, 1.5, len(t))
    plain = engine.compare_pair(t, optical, t, imu)
    r = engine.compare_pair(t, optical, t, imu, ref_polarity_arbitrary=True)
    assert r["status"] == "ok"
    assert r["orientation"] == "undetermined"
    assert r["orientation_basis"] is None and r["ref_mirrored"] is False
    assert r["orientation_reason"].startswith("correlation_inside_margin")
    assert r["ref_inverted"] is None
    assert r["rmse_deg_offset_removed"] == plain["rmse_deg_offset_removed"]
    assert r["lag_sec"] == plain["lag_sec"]


def test_compare_pair_abstains_when_shape_and_correlation_contradict():
    """Both curves are held above rest, yet they swing in opposition. One of
    the two readings is wrong and nothing here says which."""
    t, ref = _pendulum_trial(hold_s=0.7, tau=12.0, tail_s=15.0)
    test = ref.copy()
    released = t >= 0.7
    test[released] = -ref[released]
    r = engine.compare_pair(t, ref, t, test, lag_override_sec=0.0,
                            ref_polarity_arbitrary=True)
    assert r["ref_inverted"] is False and r["test_inverted"] is False
    assert r["polarity_corr_mirrored"] > 0.5 > r["polarity_corr_as_given"]
    assert r["orientation"] == "undetermined"
    assert r["orientation_reason"] == "shape_correlation_conflict"
    assert r["ref_mirrored"] is False


def test_compare_pair_never_mirrors_unless_the_reference_is_declared_arbitrary():
    """Default behaviour is main's, number for number: a rigid-body reference
    or a model trace has a real zero and a real sign."""
    t, y = _pendulum_trial(hold_s=1.5)
    r = engine.compare_pair(t, -y, t, y)
    assert r["ref_mirrored"] is False
    assert r["orientation"] == "opposite_not_mirrored"
    assert r["rmse_deg"] > 10.0 and r["rmse_deg_offset_removed"] > 10.0


def test_compare_pair_polarity_survives_nan_gaps_in_the_reference():
    t, imu = _pendulum_trial(hold_s=1.5, level=135.0)
    optical = -(imu - 135.0)
    optical[200:230] = np.nan              # half a second lost mid-swing
    r = engine.compare_pair(t, optical, t, imu, ref_polarity_arbitrary=True)
    assert r["orientation"] == "ref_mirrored"
    assert r["ref_inverted"] is True
    assert r["rmse_deg_offset_removed"] < 3.0   # the gap is interpolated across


def test_other_polarity_rmse_is_the_score_of_the_reference_the_other_way_up():
    t, imu = _pendulum_trial(rest_s=1.0, lift_s=0.8, hold_s=1.5, level=135.0)
    optical = -(imu - 135.0) + 20.0
    plain = engine.compare_pair(t, optical, t, imu)
    r = engine.compare_pair(t, optical, t, imu, ref_polarity_arbitrary=True)
    assert r["ref_mirrored"] is True
    assert r["rmse_deg_offset_removed_other_polarity"] == plain["rmse_deg_offset_removed"]
    # Not mirrored: the same key holds what mirroring would have scored.
    assert plain["rmse_deg_offset_removed_other_polarity"] == r["rmse_deg_offset_removed"]


def test_compare_pair_says_undefined_when_there_is_no_correlation():
    """A flat reference has no correlation with anything. That is not a
    correlation 'inside the margin'."""
    t, imu = _pendulum_trial(hold_s=1.5, level=135.0)
    r = engine.compare_pair(t, np.full_like(t, 90.0), t, imu, ref_polarity_arbitrary=True)
    assert r["polarity_corr_as_given"] is None and r["polarity_corr_mirrored"] is None
    assert r["orientation"] == "undetermined"
    assert r["orientation_reason"].startswith("correlation_undefined (")


def test_a_reference_that_cannot_be_aligned_mirrored_is_not_called_opposite(monkeypatch):
    """Opposite, declared arbitrary, but the mirrored curve fails to align:
    there is nothing to score. 'opposite_not_mirrored' means the reference was
    NOT declared arbitrary, so this must not borrow that label."""
    t, imu = _pendulum_trial(rest_s=1.0, lift_s=0.8, hold_s=1.5, level=135.0)
    optical = -(imu - 135.0) + 20.0
    real = engine.analysis_pipeline.synchronize_signals
    calls = []

    def second_alignment_fails(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise ValueError("Lag-corrected overlap too short to score reliably.")
        return real(*args, **kwargs)
    monkeypatch.setattr(engine.analysis_pipeline, "synchronize_signals",
                        second_alignment_fails)
    r = engine.compare_pair(t, optical, t, imu, ref_polarity_arbitrary=True)
    assert len(calls) == 2
    assert r["status"] == "ok" and r["ref_mirrored"] is False
    assert r["orientation"] == "undetermined" and r["orientation_basis"] is None
    assert r["orientation_reason"] == "mirrored_alignment_failed"
    assert r["rmse_deg_offset_removed_other_polarity"] is None
    assert r["rmse_deg_offset_removed"] > 10.0, "scored as given"


# ── _release_side: each criterion, pinned on a curve that needs it ───────────
#
# Independent review ran 53 mutations against the first version of these tests
# and 21 survived (docs/reports/2026-11-11-compare-pair-orientation-review.md
# on origin/docs/compare-pair-orientation-review, section 6). Each test below
# is a curve on which exactly one criterion is what decides.


def _hold_seen_only_for_the_last(seconds):
    """A 1.5 s hold whose samples are missing from 0.6 s before the release
    until `seconds` before it. Samples exist before the gap, so the curve does
    not simply start late."""
    t, y = _pendulum_trial(hold_s=1.5)
    y = y.copy()
    y[(t >= 0.9) & (t < 1.5 - seconds)] = np.nan
    return t, y


def test_release_side_needs_the_hold_window_covered_in_time():
    """Markers back for the last 0.27 s before the release: over half the
    window's sample slots are filled, but they span under 0.3 s. Not a hold.
    Back for the last 0.36 s: it is."""
    t, y = _hold_seen_only_for_the_last(0.27)
    assert engine._release_side(t, y)["reason"] == "no_prerelease_plateau"
    t, y = _hold_seen_only_for_the_last(0.36)
    assert engine._release_side(t, y)["sign"] == 1
    assert engine._release_side(t, _mirror(y))["sign"] == -1


def test_release_side_needs_the_hold_window_half_filled():
    """One sample in three across the whole half-second: covered end to end,
    a third full. Not a hold."""
    t, y = _pendulum_trial(hold_s=1.5)
    y = y.copy()
    hold = np.arange(90)
    y[hold[hold % 3 != 0]] = np.nan
    assert engine._release_side(t, y)["reason"] == "no_prerelease_plateau"


def test_release_side_accepts_a_hold_thinned_evenly_by_dropouts():
    """Three samples in five across the whole hold, as the corpus has (33 to
    35 of 60 present over the half-second). Still a hold."""
    t, y = _pendulum_trial(hold_s=1.5)
    y = y.copy()
    hold = np.arange(90)
    y[hold[np.isin(hold % 5, [1, 3])]] = np.nan
    assert engine._release_side(t, y)["sign"] == 1
    assert engine._release_side(t, _mirror(y))["sign"] == -1


def test_release_side_does_not_call_a_wobbling_hold_a_plateau():
    """A hold that wobbles +/-8 degrees moves a quarter of the signal range
    inside the half-second: more than the release detector's own 'has it
    moved' band, so no plateau. +/-3 degrees is a plateau."""
    t, y = _pendulum_trial(hold_s=1.5)
    held = t < 1.5
    wobbling = y.copy()
    wobbling[held] += 8.0 * np.sin(2 * np.pi * 3.0 * t[held])
    assert engine._release_side(t, wobbling)["reason"] == "no_prerelease_plateau"
    steady = y.copy()
    steady[held] += 3.0 * np.sin(2 * np.pi * 3.0 * t[held])
    assert engine._release_side(t, steady)["sign"] == 1


def test_release_side_reads_the_half_second_before_release_not_the_second():
    """Rest, lift, then a hold of 0.6 s. The half-second before the release is
    a plateau; the full second before it includes the lift."""
    t, y = _pendulum_trial(rest_s=1.5, lift_s=0.8, hold_s=0.6)
    assert engine._release_side(t, y)["sign"] == 1
    assert engine._release_side(t, _mirror(y))["sign"] == -1


def test_release_side_settles_where_the_swing_ends_not_where_the_file_ends():
    """Two recordings whose last second is nowhere near the settled level: one
    drifts 0.3 deg/s for a minute after a 10 degree release, one has the leg
    picked up 8 s after the release and held 70 degrees higher. Read against
    the end of the FILE both come out upside down."""
    t, y = _pendulum_trial(hold_s=1.5, a0=10.0, tau=0.6, tail_s=60.0, drift_deg_s=0.3)
    assert y[-60:].mean() > y[:90].mean(), "the file ends above the hold"
    side = engine._release_side(t, y)
    assert side["sign"] == 1 and side["a0_deg"] == pytest.approx(10.0, abs=1.5)
    assert engine._release_side(t, _mirror(y))["sign"] == -1

    t, y = _pendulum_trial(hold_s=1.5, tail_s=10.0)
    y = y.copy()
    y[t > 1.5 + 8.0] += 70.0
    side = engine._release_side(t, y)
    assert side["sign"] == 1 and side["a0_deg"] == pytest.approx(45.0, abs=1.5)


@pytest.mark.parametrize("tail_s", [4.0, 4.6])
def test_release_side_settled_level_averages_a_swing_cycle(tail_s):
    """A lightly damped limb still swinging +/-20 degrees when the recording
    stops. The settled level is the median over a second either side of the
    last extremum, which spans a cycle and lands near rest; a window narrow
    enough to sit on that extremum reads the amplitude 20 degrees out (high
    for one of these tails, low for the other)."""
    t, y = _pendulum_trial(hold_s=1.5, tau=6.0, tail_s=tail_s)
    side = engine._release_side(t, y)
    assert side["sign"] == 1
    assert side["a0_deg"] == pytest.approx(45.0, abs=6.0)


def test_release_side_of_an_arrested_limb_settles_at_the_end_of_the_record():
    """Rest, lift, hold, then the limb sinks back without a single swing. The
    last extremum is the hold itself, so the settled level has to come from
    the end of the record: read around the hold it comes out a third the
    size."""
    t, y = _pendulum_trial(rest_s=1.5, lift_s=0.8, hold_s=1.5, f=0.0, tau=0.8,
                           tail_s=14.0)
    side = engine._release_side(t, y)
    assert side["sign"] == 1
    assert side["a0_deg"] == pytest.approx(45.0, abs=1.5)


def test_a_slow_arrested_limb_reads_inverted_on_both_curves_and_the_pair_is_right():
    """Known limit of the per-curve flag, kept visible. When the limb sinks
    back slowly (tau 1.5 s) after rest-lift-hold, _detect_release takes the
    LIFT for the release, the plateau before it is the rest position, and the
    curve reads upside down. It does so on both curves alike, so the pair's
    relative polarity is still right: ref_inverted / test_inverted are each
    curve's reading, not a finding that the curve is wrong."""
    t, y = _pendulum_trial(rest_s=1.5, lift_s=0.8, hold_s=1.5, f=0.0, tau=1.5,
                           tail_s=14.0)
    same = engine.compare_pair(t, y + 20.0, t, y, ref_polarity_arbitrary=True)
    assert same["ref_inverted"] is True and same["test_inverted"] is True
    assert same["orientation"] == "as_given"
    opposed = engine.compare_pair(t, -y + 20.0, t, y, ref_polarity_arbitrary=True)
    assert opposed["ref_inverted"] is False and opposed["test_inverted"] is True
    assert opposed["orientation"] == "ref_mirrored"
    assert opposed["rmse_deg_offset_removed"] < 0.5


def test_release_side_rest_is_read_after_the_release_only():
    """A record that stops 0.4 s after the release. What follows the release
    is the limb falling toward rest, and that is the side it was released
    from; the second before the end of the record is mostly the hold itself
    and must not be read as the settled level."""
    t, y = _pendulum_trial(hold_s=1.5, tail_s=0.4)
    assert engine._release_side(t, y)["sign"] == 1
    assert engine._release_side(t, _mirror(y))["sign"] == -1


def test_release_side_range_ignores_a_spike():
    """Four samples at -150 (a marker swap) on a 20 degree release. Range is
    the 3rd-to-97th percentile spread, so the hold is still three quarters of
    it; measured max-to-min the same hold is an eighth and would not count."""
    t, y = _pendulum_trial(hold_s=1.5, a0=20.0, tau=0.5)
    y = y.copy()
    y[450:454] = -150.0
    assert np.ptp(y) == 170.0
    side = engine._release_side(t, y)
    assert side["sign"] == 1
    assert side["range_deg"] < 30.0


def test_release_side_sample_floor_is_forty():
    """A 6.5 s curve at 30 Hz (195 samples) has a release side; 39 samples do
    not."""
    t, y = _pendulum_trial(hold_s=1.5, tail_s=5.0, fs=30.0)
    assert len(t) == 195 and engine._release_side(t, y)["sign"] == 1
    t, y = _pendulum_trial(hold_s=0.6, tail_s=1.35, fs=20.0)
    assert len(t) == 39
    assert engine._release_side(t, y)["reason"] == "too_few_samples"


def test_pearson_r_needs_four_samples():
    """Three points always correlate; that is not evidence."""
    assert engine._pearson_r([1.0, 2.0, 3.0], [1.0, 2.0, 4.0]) is None
    assert engine._pearson_r([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 4.0, 5.0]) > 0.9
    assert engine._pearson_r([1.0, 2.0, 3.0, np.nan, 5.0], [1.0, 2.0, 4.0, 5.0, np.nan]) is None
    assert engine._pearson_r([1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 4.0, 5.0]) is None


# ── Which references may be mirrored: the loader's own word, not the tag ─────


def test_load_optitrack_reference_calls_a_knee_axis_curve_relative(monkeypatch):
    def rigid_body_refuses(path):
        raise ValueError("no rigid bodies with rotation data")
    pts = engine.pendulastic_pt_score
    declared = pts._KNEE_AXIS_WARNINGS["uncalibrated_offset"]
    assert "RELATIVE" in declared

    def marker_path(path):
        return (np.array([0.0, 1.0]), np.array([10.0, -20.0]),
                pts.TrialQuality(coverage=1.0, warnings=("something else", declared)))
    monkeypatch.setattr(engine.analysis_pipeline, "_optitrack_knee_angle_series",
                        rigid_body_refuses)
    monkeypatch.setattr(pts, "load_optitrack_detailed", marker_path)
    t, angle, method, relative = engine.load_optitrack_reference("dummy.csv")
    assert (method, relative) == ("marker_pca", True)
    assert list(angle) == [10.0, -20.0]

    def quaternion_fallback(path):
        return (np.array([0.0, 1.0]), np.array([180.0, 160.0]),
                pts.TrialQuality(coverage=1.0, warnings=("Curve is nearly flat.",)))
    monkeypatch.setattr(pts, "load_optitrack_detailed", quaternion_fallback)
    assert engine.load_optitrack_reference("dummy.csv")[2:] == ("marker_pca", False)


def test_load_optitrack_reference_never_calls_a_rigid_body_curve_relative(monkeypatch):
    monkeypatch.setattr(engine.analysis_pipeline, "_optitrack_knee_angle_series",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 150.0])))
    t, angle, method, relative = engine.load_optitrack_reference("dummy.csv")
    assert (method, relative) == ("rigid_body", False)


def test_a_precomputed_angle_file_is_tagged_marker_pca_but_is_not_relative(tmp_path):
    """The real loaders on a real file. A precomputed-angle export has a zero
    and a sign (180 - flexion). The strict rigid-body path rejects it, so it
    carries the 'marker_pca' tag, which is why the tag cannot be what decides
    whether a reference may be mirrored."""
    path = tmp_path / "angles.csv"
    lines = ["frame,time_sec,knee_angle_deg"]
    lines += [f"{i},{i / 100:.2f},{30.0 + 0.1 * i:.2f}" for i in range(200)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    t, angle, method, relative = engine.load_optitrack_reference(str(path))
    assert method == "marker_pca"
    assert relative is False
    assert angle[0] == pytest.approx(150.0)
