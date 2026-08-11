# tests/test_imu_server.py
import os, sys, math, csv
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pendulastic_imu_server as imu


@pytest.fixture(autouse=True)
def _reset_imu_config(monkeypatch):
    """Every test in this file must see the hardcoded defaults, regardless of
    whatever imu_calibration_config.json a prior tuning run may have written
    to the real repo root."""
    import imu_calibration_config as _cfgmod
    monkeypatch.setattr(imu, "_CONFIG", dict(_cfgmod.DEFAULT_CONFIG))


def test_qconj_negates_vector_part():
    q = np.array([0.9, 0.1, 0.2, 0.3])
    c = imu._qconj(q)
    assert c[0] == 0.9
    assert c[1] == -0.1
    assert c[2] == -0.2
    assert c[3] == -0.3


def test_qmul_identity():
    """q * identity = q."""
    q    = np.array([0.7071, 0.7071, 0.0, 0.0])
    iden = np.array([1.0, 0.0, 0.0, 0.0])
    result = imu._qmul(q, iden)
    np.testing.assert_allclose(result, q, atol=1e-6)


def test_qmul_self_conj_is_near_identity():
    """q * conj(q) should equal [1,0,0,0] for a unit quaternion."""
    q = np.array([0.6, 0.2, -0.7, 0.3])
    q /= np.linalg.norm(q)
    result = imu._qmul(q, imu._qconj(q))
    np.testing.assert_allclose(result, [1., 0., 0., 0.], atol=1e-6)


def test_imudevice_get_quaternion_ahrs_mode():
    """AHRS mode returns ahrs.q directly."""
    dev = imu._IMUDevice("1.2.3.4")
    dev.from_orientation_stream = False
    dev.ahrs.q = np.array([0.9, 0.1, 0.2, 0.3])
    q = dev.get_quaternion()
    np.testing.assert_allclose(q, [0.9, 0.1, 0.2, 0.3])


def test_imudevice_get_quaternion_orientation_stream_mode():
    """Orientation-stream mode converts stored Euler angles to a unit quaternion."""
    dev = imu._IMUDevice("1.2.3.5")
    dev.from_orientation_stream = True
    dev.roll = 0.0
    dev.pitch = 0.0
    dev.yaw = 0.0
    q = dev.get_quaternion()
    # Identity pose → [1,0,0,0]
    np.testing.assert_allclose(q, [1., 0., 0., 0.], atol=1e-6)


def test_imudevice_get_quaternion_orientation_stream_is_unit():
    """Quaternion from Euler angles must be unit length."""
    dev = imu._IMUDevice("1.2.3.6")
    dev.from_orientation_stream = True
    dev.roll = 30.0
    dev.pitch = 45.0
    dev.yaw = 10.0
    q = dev.get_quaternion()
    assert abs(np.linalg.norm(q) - 1.0) < 1e-6


def test_ahrs_updates_even_when_orientation_stream_active():
    """on_gyro() must still integrate the AHRS filter when from_orientation_stream=True."""
    dev = imu._IMUDevice("1.2.3.7")
    dev.from_orientation_stream = True
    dev.accel = np.array([0.0, 0.0, 9.81])
    q_before = dev.ahrs.q.copy()
    # Feed a non-zero gyro packet — AHRS should rotate away from identity.
    dev.on_gyro(np.array([0.5, 0.0, 0.0]), ts=0)
    assert not np.allclose(dev.ahrs.q, q_before), (
        "AHRS quaternion must evolve on gyro input even in orientation-stream mode")


def test_get_quaternion_prefers_ahrs_when_gyro_present():
    """get_quaternion() must return the AHRS quaternion when gyro data has arrived,
    even if from_orientation_stream is True (avoids staircase from low-rate orientation)."""
    dev = imu._IMUDevice("1.2.3.8")
    dev.from_orientation_stream = True
    dev.roll, dev.pitch, dev.yaw = 30.0, 45.0, 10.0
    dev.accel = np.array([0.0, 0.0, 9.81])
    # Simulate gyro arrival (sets last_gyro_t)
    dev.on_gyro(np.array([0.1, 0.0, 0.0]), ts=1000)
    q = dev.get_quaternion()
    # Must match the AHRS quaternion, not the Euler-converted one
    np.testing.assert_allclose(q, dev.ahrs.q, atol=1e-9)


def test_swing_angle_deg_returns_nan_before_zero():
    """swing_angle_deg() must return NaN before zero() is called."""
    imu.reset_devices()
    imu.clear_zero()
    angle = imu.swing_angle_deg()
    assert math.isnan(angle)


def test_get_state_contains_swing_angle_deg_key():
    """get_state() always returns the 'swing_angle_deg' key."""
    st = imu.get_state()
    assert "swing_angle_deg" in st


def test_dynamic_beta_skips_correction_during_impact():
    """Accelerometer correction must be skipped when |a| >> g (high-impact)."""
    ahrs = imu.MadgwickAHRS(beta=0.5)  # high beta so correction is very visible
    # Warm up the g-magnitude estimate with a few near-g samples
    g_vec = np.array([0.0, 0.0, 9.81])
    gyro_zero = np.array([0.0, 0.0, 0.0])
    for _ in range(20):
        ahrs.update(gyro_zero, g_vec, None, 0.01)
    q_after_warmup = ahrs.q.copy()

    # Apply impact: accel 5× gravity. With correction enabled this would distort
    # the orientation; with correction skipped, only gyro drives the update.
    impact_accel = np.array([0.0, 0.0, 9.81 * 5])
    ahrs.update(gyro_zero, impact_accel, None, 0.01)
    # q should be nearly unchanged (gyro is zero, correction skipped)
    np.testing.assert_allclose(ahrs.q, q_after_warmup, atol=1e-4,
        err_msg="Impact accel should not distort orientation (correction skipped)")


def test_accel_correction_skipped_during_rotation_despite_near_g_norm():
    """Regression test for the 2026-08-10 gate fix: magnitude-proximity-to-g
    alone is not enough to trust an accel reading as a gravity reference. A
    sensor that's actively rotating (e.g. a slowly swinging/settling
    pendulum) can produce real centripetal/tangential acceleration whose
    NORM sits within the gate's old ±10% tolerance while its DIRECTION is
    meaningfully off from true gravity -- correcting toward that direction
    steers orientation toward the sensor's own motion instead of doing
    nothing. update() must now also require low gyro magnitude before
    trusting accel direction.

    Proof: with gyro magnitude above ACCEL_CORRECTION_GYRO_MAX_RAD_S, two
    accel vectors of the same (near-g) magnitude but different directions
    must produce the SAME resulting orientation -- if correction were still
    firing, the two directions would pull the filter apart."""
    gyro_rotating = np.array([0.0, 0.0, 0.6])   # above the 0.3 rad/s gate
    assert np.linalg.norm(gyro_rotating) > imu.ACCEL_CORRECTION_GYRO_MAX_RAD_S

    ahrs_a = imu.MadgwickAHRS(beta=0.5)
    ahrs_b = imu.MadgwickAHRS(beta=0.5)
    g_vec = np.array([0.0, 0.0, 9.81])
    gyro_zero = np.array([0.0, 0.0, 0.0])
    for ahrs in (ahrs_a, ahrs_b):
        for _ in range(20):
            ahrs.update(gyro_zero, g_vec, None, 0.01)   # warm up _a_est near g

    accel_a = np.array([0.0, 0.0, 9.81])                       # gravity-aligned
    accel_b = np.array([3.0, 3.0, math.sqrt(9.81**2 - 18.0)])  # tilted, same norm as accel_a
    np.testing.assert_allclose(np.linalg.norm(accel_a), np.linalg.norm(accel_b), atol=1e-9)

    ahrs_a.update(gyro_rotating, accel_a, None, 0.01)
    ahrs_b.update(gyro_rotating, accel_b, None, 0.01)

    np.testing.assert_allclose(ahrs_a.q, ahrs_b.q, atol=1e-9,
        err_msg="Differing accel direction changed the result while gyro was "
                "above the rotation gate -- accel correction is still firing "
                "during rotation instead of being gated off")


def test_accel_correction_still_applies_when_genuinely_still():
    """Companion to the rotation-gate test above: below
    ACCEL_CORRECTION_GYRO_MAX_RAD_S, differing accel direction MUST still
    change the result -- the new gyro-magnitude check must not accidentally
    disable correction outright."""
    gyro_still = np.array([0.0, 0.0, 0.05])   # well below the 0.3 rad/s gate

    ahrs_a = imu.MadgwickAHRS(beta=0.5)
    ahrs_b = imu.MadgwickAHRS(beta=0.5)
    g_vec = np.array([0.0, 0.0, 9.81])
    gyro_zero = np.array([0.0, 0.0, 0.0])
    for ahrs in (ahrs_a, ahrs_b):
        for _ in range(20):
            ahrs.update(gyro_zero, g_vec, None, 0.01)

    accel_a = np.array([0.0, 0.0, 9.81])
    accel_b = np.array([3.0, 3.0, 9.14])

    ahrs_a.update(gyro_still, accel_a, None, 0.01)
    ahrs_b.update(gyro_still, accel_b, None, 0.01)

    assert not np.allclose(ahrs_a.q, ahrs_b.q, atol=1e-6), (
        "Correction should still fire while genuinely still -- differing "
        "accel direction should have produced different orientations")


def test_calibrate_gyro_bias_uses_trailing_hold_buffer_mean():
    """calibrate_gyro_bias() must set gyro_bias to the mean of the buffered
    raw samples, once enough have accumulated."""
    dev = imu._IMUDevice("12.0.0.1")
    bias = np.array([0.02, -0.03, 0.01])
    now = __import__("time").time()
    for i in range(imu.GYRO_BIAS_MIN_SAMPLES):
        dev._gyro_hold_buf.append((now - 0.01 * i, bias.copy()))
    dev.calibrate_gyro_bias()
    np.testing.assert_allclose(dev.gyro_bias, bias, atol=1e-9)


def test_calibrate_gyro_bias_leaves_zero_with_too_few_samples():
    """Below GYRO_BIAS_MIN_SAMPLES the estimate is untrustworthy; gyro_bias
    must stay at its previous value (zero, for a fresh device) rather than
    being set from a handful of noisy samples."""
    dev = imu._IMUDevice("12.0.0.2")
    now = __import__("time").time()
    for i in range(imu.GYRO_BIAS_MIN_SAMPLES - 1):
        dev._gyro_hold_buf.append((now - 0.01 * i, np.array([1.0, 1.0, 1.0])))
    dev.calibrate_gyro_bias()
    np.testing.assert_array_equal(dev.gyro_bias, np.zeros(3))


def test_on_gyro_subtracts_calibrated_bias_before_ahrs_update():
    """Once a bias is calibrated, feeding gyro readings equal to that bias
    (i.e. the device is still holding at the same static offset) must leave
    the AHRS quaternion effectively unchanged — the bias-corrected angular
    velocity fed to the filter should be ~0, not the raw biased value."""
    dev = imu._IMUDevice("12.0.0.3")
    dev.accel = np.array([0.0, 0.0, 9.81])
    dev.gyro_bias = np.array([0.02, -0.03, 0.01])
    q_before = dev.ahrs.q.copy()
    dev.on_gyro(dev.gyro_bias.copy(), ts=1000)
    np.testing.assert_allclose(dev.ahrs.q, q_before, atol=1e-3,
        err_msg="A gyro reading equal to the calibrated bias should not "
                "rotate the AHRS quaternion once bias is subtracted")


def test_zero_calibrates_gyro_bias_for_connected_device():
    """zero() must call calibrate_gyro_bias() on each connected device, so a
    device that was held still just before zeroing gets its bias measured."""
    imu.reset_devices()
    imu.clear_zero()
    imu._devices["12.0.0.4"] = imu._IMUDevice("12.0.0.4")
    imu._roles["12.0.0.4"]   = imu.ROLE_DISTAL
    dev = imu._devices["12.0.0.4"]
    dev.accel = np.array([0.0, 0.0, 9.81])
    dev.last_rx = __import__("time").time()
    bias = np.array([0.015, 0.0, -0.02])
    now = __import__("time").time()
    for i in range(imu.GYRO_BIAS_MIN_SAMPLES + 5):
        dev._gyro_hold_buf.append((now - 0.01 * i, bias.copy()))

    imu.zero()

    np.testing.assert_allclose(dev.gyro_bias, bias, atol=1e-9)
    imu.reset_devices()
    imu.clear_zero()


def test_flex_axis_captured_on_first_motion_after_zero():
    """After zero(), the first gyro burst above threshold must populate _flex_axis."""
    imu.reset_devices()
    imu.clear_zero()
    # Register a distal device
    imu._devices["10.0.0.2"] = imu._IMUDevice("10.0.0.2")
    imu._roles["10.0.0.2"]   = imu.ROLE_DISTAL
    dev = imu._devices["10.0.0.2"]
    dev.accel    = np.array([0.0, 0.0, 9.81])
    dev.ahrs.q   = np.array([1.0, 0.0, 0.0, 0.0])
    dev.last_rx  = __import__("time").time()
    imu.zero()
    assert imu._flex_axis_armed, "zero() must arm flex-axis capture"
    assert imu._flex_axis is None, "_flex_axis should be None before first motion"

    # Feed a gyro burst above the capture threshold
    omega = np.array([0.0, 1.0, 0.0])   # 1 rad/s around y-axis
    dev.on_gyro(omega, ts=1000)
    assert imu._flex_axis is not None, "_flex_axis must be captured after first motion"
    assert not imu._flex_axis_armed, "_flex_axis_armed must be cleared after capture"
    np.testing.assert_allclose(
        np.linalg.norm(imu._flex_axis), 1.0, atol=1e-6,
        err_msg="_flex_axis must be a unit vector")
    imu.reset_devices()
    imu.clear_zero()


def test_swing_angle_projection_excludes_out_of_plane_rotation():
    """When _flex_axis is set, only the on-axis component is returned."""
    imu.reset_devices()
    imu.clear_zero()
    imu._devices["10.0.0.3"] = imu._IMUDevice("10.0.0.3")
    imu._roles["10.0.0.3"]   = imu.ROLE_DISTAL
    dev = imu._devices["10.0.0.3"]
    dev.from_orientation_stream = False
    dev.ahrs.q  = np.array([1.0, 0.0, 0.0, 0.0])  # identity = zero pose
    dev.last_rx = __import__("time").time()
    imu.zero()

    # Manually lock the flexion axis as +Y in sensor frame
    imu._flex_axis       = np.array([0.0, 1.0, 0.0])
    imu._flex_axis_armed = False

    # Rotate 45° purely around Y (sagittal axis) — projected angle should be 45°
    angle_rad = math.radians(45.0)
    dev.ahrs.q = np.array([math.cos(angle_rad / 2), 0.0,
                            math.sin(angle_rad / 2), 0.0])
    projected = imu.swing_angle_deg()
    assert abs(projected - 45.0) < 0.5, f"Pure Y-rotation: expected ~45°, got {projected:.2f}°"

    # Rotate 45° purely around Z (out-of-plane) — projected angle should be ~0°
    dev.ahrs.q = np.array([math.cos(angle_rad / 2), 0.0, 0.0,
                            math.sin(angle_rad / 2)])
    projected_oop = imu.swing_angle_deg()
    assert abs(projected_oop) < 1.0, (
        f"Out-of-plane Z-rotation: expected ~0°, got {projected_oop:.2f}°")

    imu.reset_devices()
    imu.clear_zero()


def test_gravity_seed_z_up_is_identity():
    """accel = [0,0,+1] (sensor Z already up) → identity quaternion."""
    q = imu._gravity_seed(np.array([0., 0., 9.81]))
    np.testing.assert_allclose(q, [1., 0., 0., 0.], atol=1e-6)


def test_gravity_seed_z_down_is_unit():
    """accel = [0,0,-1] (phone face-down, Z inverted) → unit quaternion with w=0."""
    q = imu._gravity_seed(np.array([0., 0., -9.81]))
    assert abs(np.linalg.norm(q) - 1.0) < 1e-6
    # Must represent a 180° rotation (w ≈ 0)
    assert abs(q[0]) < 1e-6


def test_gravity_seed_result_is_unit_quaternion():
    """Seeded quaternion must be unit length for any non-zero accel."""
    for a in [[0, 0, 9.81], [0, 0, -9.81], [9.81, 0, 0], [0, 9.81, 0],
              [5., 5., 5.], [-3., 1., 8.]]:
        q = imu._gravity_seed(np.array(a, float))
        assert abs(np.linalg.norm(q) - 1.0) < 1e-6, f"not unit for accel={a}: {q}"


def test_gravity_seed_applied_on_first_accel():
    """on_accel() must seed ahrs.q from gravity on the very first call."""
    dev = imu._IMUDevice("9.9.9.9")
    assert not dev._ahrs_seeded
    np.testing.assert_allclose(dev.ahrs.q, [1., 0., 0., 0.], atol=1e-9)
    # Feed accel pointing straight down (phone face-down in shoe)
    dev.on_accel(np.array([0., 0., -9.81]), ts=0)
    assert dev._ahrs_seeded
    # The seeded quaternion must NOT be identity any more
    assert not np.allclose(dev.ahrs.q, [1., 0., 0., 0.], atol=0.1), (
        "First accel should have seeded AHRS away from identity")
    # Second call must NOT re-seed (flag stays True, q stays whatever it is now)
    q_after_first = dev.ahrs.q.copy()
    dev.on_accel(np.array([0., 0., 9.81]), ts=1)   # different accel
    np.testing.assert_allclose(dev.ahrs.q, q_after_first, atol=1e-9,
        err_msg="Second on_accel must not overwrite the seed")


def test_swing_angle_near_zero_after_gravity_seeded_zero():
    """With gravity-seeded AHRS, zeroing while stationary should give ~0° angle."""
    imu.reset_devices()
    imu.clear_zero()
    imu._devices["11.0.0.1"] = imu._IMUDevice("11.0.0.1")
    imu._roles["11.0.0.1"]   = imu.ROLE_DISTAL
    dev = imu._devices["11.0.0.1"]
    dev.last_rx = __import__("time").time()

    # Simulate phone face-down (screen toward ground): accel ≈ [0, 0, -1g]
    dev.on_accel(np.array([0., 0., -9.81]), ts=0)
    # AHRS is now seeded for face-down orientation
    imu.zero()
    # Same pose → angle must be ≈ 0°
    angle = imu.swing_angle_deg()
    assert abs(angle) < 1.0, f"Expected ~0° after correct seed+zero, got {angle:.3f}°"
    imu.reset_devices()
    imu.clear_zero()


def test_swing_angle_zero_returns_zero():
    """Immediately after zero(), swing_angle_deg() should return ~0°."""
    imu.reset_devices()
    # Inject a fake proximal device at a known quaternion
    imu._devices["10.0.0.1"] = imu._IMUDevice("10.0.0.1")
    imu._roles["10.0.0.1"] = imu.ROLE_DISTAL
    dev = imu._devices["10.0.0.1"]
    dev.from_orientation_stream = False
    dev.ahrs.q = np.array([1.0, 0.0, 0.0, 0.0])
    dev.last_rx = __import__("time").time()
    # zero() captures this quaternion
    imu.zero()
    # Same pose → 0° swing
    angle = imu.swing_angle_deg()
    assert abs(angle) < 1e-4, f"Expected ~0°, got {angle}"
    imu.reset_devices()
    imu.clear_zero()


def test_raw_csv_prefix_strips_imu_suffix():
    assert imu._raw_csv_prefix("C:/x/Trial_4_imu.csv") == "C:/x/Trial_4"


def test_raw_csv_prefix_without_imu_suffix():
    assert imu._raw_csv_prefix("C:/x/Trial_4.csv") == "C:/x/Trial_4"


def test_raw_csv_prefix_case_insensitive():
    assert imu._raw_csv_prefix("C:/x/Trial_4_IMU.CSV") == "C:/x/Trial_4"


def test_open_raw_csv_writes_header(tmp_path):
    path = str(tmp_path / "trial_accel.csv")
    f, w = imu._open_raw_csv(path)
    assert f is not None and w is not None
    f.close()
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["timestamp_ms", "phone_ts_ms", "role",
                        "sensor_name", "x", "y", "z"]


def test_open_raw_csv_returns_none_on_unwritable_path():
    f, w = imu._open_raw_csv("Z:/definitely/not/a/real/drive/trial_accel.csv")
    assert f is None and w is None


def test_start_recording_creates_three_raw_csvs(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_4_imu.csv")
    assert imu.start_recording(path)
    try:
        for suffix in ("accel", "gyro", "mag"):
            p = tmp_path / f"Trial_4_{suffix}.csv"
            assert p.exists(), f"missing {p}"
            with open(p, newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh))
            assert header == ["timestamp_ms", "phone_ts_ms", "role",
                               "sensor_name", "x", "y", "z"]
    finally:
        imu.stop_recording()


def test_start_recording_fused_csv_header_unchanged(tmp_path):
    """Regression: the existing fused-angle CSV format must not change."""
    imu.reset_devices()
    path = str(tmp_path / "Trial_5_imu.csv")
    assert imu.start_recording(path)
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        header_row = next(r for r in rows if r and r[0] == "t_epoch")
        assert header_row == [
            "t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned",
            "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg",
            "prox_roll", "prox_pitch", "prox_yaw",
            "dist_roll", "dist_pitch", "dist_yaw", "paired",
        ]
    finally:
        imu.stop_recording()


def test_log_raw_csv_appends_row_to_correct_csv(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_6_imu.csv")
    assert imu.start_recording(path)
    try:
        imu._log_raw_csv("proximal", "Gyroscope", [0.018327, -0.023543, 0.002843],
                     1785500950869, 1000.25)
    finally:
        imu.stop_recording()
    with open(tmp_path / "Trial_6_gyro.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 2
    _, phone_ts, role, sensor_name, x, y, z = rows[1]
    assert phone_ts == "1785500950869"
    assert role == "proximal"
    assert sensor_name == "Gyroscope"
    assert x == "0.018327" and y == "-0.023543" and z == "0.002843"


def test_log_raw_csv_noop_when_no_writer_open():
    """No CSV is open (never recorded) — must not raise."""
    imu.reset_devices()
    imu._log_raw_csv("distal", "Accelerometer", [0.0, 0.0, 9.81], 0, 0.0)


def test_stop_recording_closes_and_resets_all_handles(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_7_imu.csv")
    assert imu.start_recording(path)
    imu.stop_recording()
    assert imu._rec_file is None and imu._rec_writer is None
    assert all(f is None for f in imu._raw_csv_files.values())
    assert all(w is None for w in imu._raw_csv_writers.values())


def test_stop_recording_is_idempotent(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_8_imu.csv")
    assert imu.start_recording(path)
    imu.stop_recording()
    imu.stop_recording()   # must not raise


def test_on_accel_logs_raw_row_while_recording(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_9_imu.csv")
    assert imu.start_recording(path)
    try:
        dev = imu._IMUDevice("10.0.0.9")
        imu._devices["10.0.0.9"] = dev
        imu._roles["10.0.0.9"] = imu.ROLE_PROXIMAL
        dev.on_accel(np.array([-0.030792, -0.551956, -0.825500]), 1785500949800)
    finally:
        imu.stop_recording()
    with open(tmp_path / "Trial_9_accel.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 2
    _, phone_ts, role, sensor_name, x, y, z = rows[1]
    assert phone_ts == "1785500949800"
    assert role == "proximal"
    assert sensor_name == "Accelerometer"
    assert x == "-0.030792" and y == "-0.551956" and z == "-0.825500"
    imu.reset_devices()


def test_on_gyro_logs_raw_row_while_recording(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_10_imu.csv")
    assert imu.start_recording(path)
    try:
        dev = imu._IMUDevice("10.0.0.10")
        imu._devices["10.0.0.10"] = dev
        imu._roles["10.0.0.10"] = imu.ROLE_DISTAL
        dev.accel = np.array([0.0, 0.0, 9.81])
        dev.on_gyro(np.array([0.018327, -0.023543, 0.002843]), 1785500950869)
    finally:
        imu.stop_recording()
    with open(tmp_path / "Trial_10_gyro.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[1][2] == "distal"
    assert rows[1][3] == "Gyroscope"
    imu.reset_devices()


def test_on_mag_logs_raw_row_using_ip_when_role_unassigned(tmp_path):
    """A third/unassigned phone has no _roles entry — role falls back to IP."""
    imu.reset_devices()
    path = str(tmp_path / "Trial_11_imu.csv")
    assert imu.start_recording(path)
    try:
        dev = imu._IMUDevice("10.0.0.11")
        imu._devices["10.0.0.11"] = dev   # deliberately no _roles entry
        dev.on_mag(np.array([-23.497269, -29.110579, -33.166870]), 1785500954175)
    finally:
        imu.stop_recording()
    with open(tmp_path / "Trial_11_mag.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[1][2] == "10.0.0.11"
    assert rows[1][3] == "Magnetometer"
    imu.reset_devices()


def test_raw_logging_skipped_when_not_recording():
    imu.reset_devices()
    assert not imu._recording
    dev = imu._IMUDevice("10.0.0.12")
    imu._devices["10.0.0.12"] = dev
    imu._roles["10.0.0.12"] = imu.ROLE_PROXIMAL
    # Must not raise, and there is no open writer to touch.
    dev.on_accel(np.array([0.0, 0.0, 9.81]), 0)
    dev.on_gyro(np.array([0.0, 0.0, 0.0]), 0)
    dev.on_mag(np.array([0.0, 0.0, 0.0]), 0)
    imu.reset_devices()


def test_ahrs_fusion_unaffected_by_raw_logging(tmp_path):
    """Regression (requirement: non-breaking integration): the fused AHRS
    quaternion after accel+gyro must be identical whether or not raw logging
    is active."""
    imu.reset_devices()
    imu.clear_zero()
    dev1 = imu._IMUDevice("10.0.0.13")
    imu._devices["10.0.0.13"] = dev1
    imu._roles["10.0.0.13"] = imu.ROLE_DISTAL
    dev1.last_rx = __import__("time").time()
    dev1.on_accel(np.array([0.0, 0.0, 9.81]), 0)
    dev1.on_gyro(np.array([0.2, 0.0, 0.0]), 10)

    imu.reset_devices()
    imu.clear_zero()
    dev2 = imu._IMUDevice("10.0.0.13")
    imu._devices["10.0.0.13"] = dev2
    imu._roles["10.0.0.13"] = imu.ROLE_DISTAL
    dev2.last_rx = __import__("time").time()
    path = str(tmp_path / "Trial_12_imu.csv")
    assert imu.start_recording(path)
    try:
        dev2.on_accel(np.array([0.0, 0.0, 9.81]), 0)
        dev2.on_gyro(np.array([0.2, 0.0, 0.0]), 10)
    finally:
        imu.stop_recording()

    np.testing.assert_allclose(dev1.ahrs.q, dev2.ahrs.q, atol=1e-12)
    imu.reset_devices()
    imu.clear_zero()


def _rec_loop(tag, tmp_path, stop_evt, n_iters=50):
    """Shared helper: repeatedly start/stop a recording. Checks stop_evt
    each iteration so a merely-slow (not deadlocked, not lock-starved) run
    can be interrupted promptly between iterations instead of becoming an
    unbounded zombie thread — extracted from test_concurrent_start_
    recording_and_gyro_callbacks_do_not_deadlock while investigating an
    intermittent full-suite crash (Windows fatal exception, code
    0x80000003). This alone was NOT sufficient to fix that crash: the
    actual root cause was gyro_loop starving these threads' _lock
    acquisition *inside* a single start_recording() call (see its
    docstring) -- this per-iteration check only helps once a thread
    actually gets to complete a call. Kept anyway as defense in depth: it
    bounds the zombie-thread window for any other reason a call might run
    long. See test_rec_loop_stops_promptly_when_signaled_even_if_slow for
    the regression pin on this specific contract."""
    for n in range(n_iters):
        if stop_evt.is_set():
            break
        path = str(tmp_path / f"Trial_deadlock_{tag}_{n}_imu.csv")
        imu.start_recording(path)
        imu.stop_recording()


def test_rec_loop_stops_promptly_when_signaled_even_if_slow(monkeypatch, tmp_path):
    """Regression pin for the zombie-thread gap that caused an intermittent
    full-suite crash: _rec_loop must notice stop_evt within its bounded
    per-iteration check, even when start_recording() is slow, rather than
    blindly running to completion. Reproduces the mechanism deterministically:
    start_recording is monkeypatched to sleep 50ms/call (100 iterations would
    take ~5s), so a correctly-interruptible loop signaled after ~120ms must
    exit within a short bounded join -- an uninterruptible loop would still
    be alive well past it."""
    import threading
    import time

    real_start = imu.start_recording

    def slow_start_recording(path):
        time.sleep(0.05)
        return real_start(path)

    monkeypatch.setattr(imu, "start_recording", slow_start_recording)

    stop_evt = threading.Event()
    t = threading.Thread(target=_rec_loop, args=("x", tmp_path, stop_evt, 100), daemon=True)
    t.start()
    time.sleep(0.12)   # let it get a couple of slow iterations in
    stop_evt.set()
    t.join(timeout=1.0)

    assert not t.is_alive(), (
        "_rec_loop did not notice stop_evt promptly -- a merely-slow run "
        "must be interruptible, not left running as an unbounded zombie thread")
    imu.reset_devices()
    imu.clear_zero()


def test_concurrent_start_recording_and_gyro_callbacks_do_not_deadlock(tmp_path):
    """Regression for the _lock/_rec_lock ordering hazard fixed in Task 2:
    on_gyro() acquires _rec_lock while its caller already holds _lock (as
    _dispatch() does in production), so start_recording()/stop_recording()
    must never acquire _lock while holding _rec_lock. Two concurrent
    start/stop threads are required to close the cyclic wait: with only one
    rec thread, _recording is never True at the instant that thread holds
    _rec_lock and blocks on _lock (its own prior stop_recording() call
    already cleared the flag), so a single-threaded version of this test
    cannot reach the deadlock state at all — confirmed empirically during
    Task 5. All worker threads are daemons and joined with a timeout, so a
    regression is bounded to roughly 65s and reported as an assertion
    failure here, rather than hanging silently forever. A genuine deadlock
    will still wedge whichever test runs next and touches _rec_lock (that
    risk is inherent to actually catching a deadlock at all) -- but a
    merely-slow-under-load run no longer leaves permanent zombie threads:
    both rec threads get a bounded follow-up join after stop_evt is set,
    giving them the same chance to notice it that gyro already had.

    gyro_loop sleeps briefly (1ms, not time.sleep(0)) after releasing
    _lock each iteration. Without it, this tight acquire/release/
    immediately-reacquire spin against the same threading.RLock can
    starve the rec threads' own _lock acquisition (inside sync_status(),
    the first thing start_recording() calls) for tens of seconds under
    real CPU contention -- this, not test slowness alone, was the actual
    root cause of an intermittent full-suite crash (Windows fatal
    exception, code 0x80000003): the rec threads were captured blocked on
    `with _lock:` itself, never reaching their next per-iteration
    stop_evt check at all, while gyro_loop's thread held _lock deep
    inside a numpy call at the moment a GC cycle triggered. time.sleep(0)
    was tried first and measurably reduced (but did not eliminate) the
    crash's reproduction rate across repeated full-suite runs: on
    Windows, Sleep(0) only yields to already-ready threads, and a thread
    blocked on a mutex isn't necessarily made ready the instant it's
    released -- it needs an actual OS wake/schedule, which a real (if
    tiny) sleep duration forces much more reliably than a zero-length one."""
    import threading
    import time

    imu.reset_devices()
    imu._devices["10.0.0.20"] = imu._IMUDevice("10.0.0.20")
    imu._roles["10.0.0.20"] = imu.ROLE_DISTAL
    dev = imu._devices["10.0.0.20"]
    dev.accel = np.array([0.0, 0.0, 9.81])

    stop_evt = threading.Event()

    def gyro_loop():
        i = 0
        while not stop_evt.is_set():
            with imu._lock:
                dev.on_gyro(np.array([0.01, 0.0, 0.0]), i)
            time.sleep(0.001)   # real sleep so rec threads can win the RLock too
            i += 1

    t_gyro = threading.Thread(target=gyro_loop, daemon=True)
    t_rec_a = threading.Thread(target=_rec_loop, args=("a", tmp_path, stop_evt), daemon=True)
    t_rec_b = threading.Thread(target=_rec_loop, args=("b", tmp_path, stop_evt), daemon=True)
    t_gyro.start()
    t_rec_a.start()
    t_rec_b.start()
    t_rec_a.join(timeout=30.0)
    t_rec_b.join(timeout=30.0)
    stop_evt.set()
    t_gyro.join(timeout=5.0)
    # Bounded follow-up chance for the rec threads to notice stop_evt too --
    # closes the zombie-thread gap for a merely-slow (not deadlocked) run.
    t_rec_a.join(timeout=5.0)
    t_rec_b.join(timeout=5.0)

    assert not t_rec_a.is_alive(), \
        "start_recording()/stop_recording() (thread a) hung — lock-order regression"
    assert not t_rec_b.is_alive(), \
        "start_recording()/stop_recording() (thread b) hung — lock-order regression"
    assert not t_gyro.is_alive(), \
        "gyro callback loop hung — lock-order regression"
    imu.reset_devices()
    imu.clear_zero()


def test_new_device_uses_configured_beta(monkeypatch):
    monkeypatch.setitem(imu._CONFIG, "beta", 0.15)
    dev = imu._IMUDevice("12.0.0.1")
    assert dev.ahrs.beta == 0.15


def test_on_accel_skips_seeding_when_gravity_seed_disabled(monkeypatch):
    monkeypatch.setitem(imu._CONFIG, "gravity_seed", False)
    dev = imu._IMUDevice("12.0.0.2")
    dev.on_accel(np.array([0., 0., -9.81]), ts=0)
    assert dev._ahrs_seeded, "seeded flag must still be set so we never re-seed later"
    np.testing.assert_allclose(dev.ahrs.q, [1., 0., 0., 0.], atol=1e-9,
        err_msg="AHRS quaternion must stay at identity when gravity_seed=False")


def test_zero_does_not_arm_flex_axis_when_capture_disabled(monkeypatch):
    monkeypatch.setitem(imu._CONFIG, "flex_axis_capture", False)
    imu.reset_devices()
    imu.clear_zero()
    imu._devices["13.0.0.1"] = imu._IMUDevice("13.0.0.1")
    imu._roles["13.0.0.1"]   = imu.ROLE_DISTAL
    dev = imu._devices["13.0.0.1"]
    dev.last_rx = __import__("time").time()
    imu.zero()
    assert not imu._flex_axis_armed, "flex-axis capture must not arm when disabled in config"
    imu.reset_devices()
    imu.clear_zero()


def test_start_stop_raw_log_writes_jsonl(tmp_path):
    import json as _json
    imu.reset_devices()
    path = str(tmp_path / "trial_raw.jsonl")
    imu.start_raw_log(path)
    dev = imu._IMUDevice("14.0.0.1")
    imu._devices["14.0.0.1"] = dev
    imu._roles["14.0.0.1"] = imu.ROLE_DISTAL
    dev.on_accel(np.array([0., 0., -9.81]), ts=100)
    dev.on_gyro(np.array([0.1, 0.2, 0.3]), ts=110)
    dev.on_mag(np.array([1., 0., 0.]), ts=120)
    returned_path = imu.stop_raw_log()
    assert returned_path == path

    lines = [_json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert len(lines) == 3
    assert lines[0]["sensor"] == "accel"
    assert lines[0]["role"] == imu.ROLE_DISTAL
    assert lines[0]["v"] == [0.0, 0.0, -9.81]
    assert lines[0]["phone_ts_ms"] == 100
    assert lines[1]["sensor"] == "gyro"
    assert lines[2]["sensor"] == "mag"
    imu.reset_devices()


def test_stop_raw_log_returns_none_when_nothing_open():
    imu._raw_log_file = None
    imu._raw_log_path = None
    assert imu.stop_raw_log() is None


def test_on_accel_on_gyro_on_mag_are_no_ops_for_raw_log_when_not_recording():
    """Packets arriving with no raw log open must not raise."""
    imu.reset_devices()
    dev = imu._IMUDevice("14.0.0.2")
    dev.on_accel(np.array([0., 0., 9.81]), ts=0)
    dev.on_gyro(np.array([0., 0., 0.]), ts=10)
    dev.on_mag(np.array([1., 0., 0.]), ts=20)
    imu.reset_devices()


def test_dispatch_end_to_end_writes_all_three_raw_csvs(tmp_path):
    """Full pipeline: _dispatch() receiving the exact Sensor Stream JSON shapes
    from the spec must produce three populated raw CSVs plus the fused CSV.

    Also dispatches an accelerometer packet from a second device/IP to prove
    the `role` column does what it's for: separating two phones' rows within
    the same raw CSV (first phone seen -> proximal, second -> distal)."""
    imu.reset_devices()
    imu.clear_zero()
    path = str(tmp_path / "Trial_20_imu.csv")
    assert imu.start_recording(path)
    try:
        ip = "192.168.1.50"
        imu._dispatch("/accelerometer",
            '{"SensorName":"Accelerometer","Timestamp":1785500949800,'
            '"x":"-0.030792","y":"-0.551956","z":"-0.825500"}', ip)
        imu._dispatch("/gyroscope",
            '{"SensorName":"Gyroscope","Timestamp":1785500950869,'
            '"x":"0.018327","y":"-0.023543","z":"0.002843"}', ip)
        imu._dispatch("/magnetometer",
            '{"SensorName":"Magnetometer","Timestamp":1785500954175,'
            '"x":"-23.497269","y":"-29.110579","z":"-33.166870"}', ip)

        ip2 = "192.168.1.51"
        imu._dispatch("/accelerometer",
            '{"SensorName":"Accelerometer","Timestamp":1785500949900,'
            '"x":"0.012345","y":"0.098765","z":"9.750000"}', ip2)
    finally:
        imu.stop_recording()

    for suffix, sensor_name, ts, xyz in (
        ("gyro", "Gyroscope", "1785500950869",
         ("0.018327", "-0.023543", "0.002843")),
        ("mag", "Magnetometer", "1785500954175",
         ("-23.497269", "-29.110579", "-33.166870")),
    ):
        with open(tmp_path / f"Trial_20_{suffix}.csv", newline="",
                  encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == ["timestamp_ms", "phone_ts_ms", "role",
                            "sensor_name", "x", "y", "z"]
        assert len(rows) == 2
        _, phone_ts, role, name, x, y, z = rows[1]
        assert phone_ts == ts
        assert role == "proximal"          # first phone seen -> proximal
        assert name == sensor_name
        assert (x, y, z) == xyz

    with open(tmp_path / "Trial_20_accel.csv", newline="",
              encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["timestamp_ms", "phone_ts_ms", "role",
                        "sensor_name", "x", "y", "z"]
    assert len(rows) == 3, "expected two data rows — one per device"
    _, phone_ts1, role1, name1, x1, y1, z1 = rows[1]
    assert phone_ts1 == "1785500949800"
    assert role1 == "proximal"             # first phone seen -> proximal
    assert name1 == "Accelerometer"
    assert (x1, y1, z1) == ("-0.030792", "-0.551956", "-0.825500")
    _, phone_ts2, role2, name2, x2, y2, z2 = rows[2]
    assert phone_ts2 == "1785500949900"
    assert role2 == "distal"               # second phone seen -> distal
    assert name2 == "Accelerometer"
    assert (x2, y2, z2) == ("0.012345", "0.098765", "9.750000")

    imu.reset_devices()
    imu.clear_zero()


def test_quat_to_euler_deg_matches_identity_quaternion():
    roll, pitch, yaw = imu._quat_to_euler_deg(np.array([1.0, 0.0, 0.0, 0.0]))
    assert abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9


def test_euler_deg_delegates_to_quat_to_euler_deg():
    ahrs = imu.MadgwickAHRS(beta=0.1)
    ahrs.update(np.array([0.3, 0.1, -0.2]), np.array([0.0, 0.0, 9.81]), None, 0.05)
    assert ahrs.euler_deg() == imu._quat_to_euler_deg(ahrs.q)


def test_is_stationary_window_true_for_flat_gyro_and_accel():
    """A window with near-zero gyro variance and accel magnitude pinned near
    gravity, spanning the full GYRO_BIAS_WINDOW_S, is stationary."""
    now = 10.0
    gyro_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.01, -0.01, 0.0]))
                for i in range(21)]
    accel_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.0, 0.0, 9.81]))
                 for i in range(21)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is True


def test_is_stationary_window_false_for_handling_gyro():
    """A window whose raw gyro OSCILLATES DIRECTION on one axis, at a
    magnitude well past GYRO_STATIONARY_MAX_RAD_S -- e.g. an examiner
    gripping/repositioning the limb -- must not count as stationary, even
    with a flat accel signal. Scaled relative to the actual (empirically
    -determined, per Task 1) threshold rather than a hardcoded literal, so
    this test stays correct regardless of what Task 1 picked. Oscillating
    direction (not just varying magnitude) is deliberate: a peak-to-peak-of
    -magnitude check would see ~0 range here even though the sensor is
    clearly moving -- this is exactly the case the per-axis check exists
    to catch."""
    now = 10.0
    amp = imu.GYRO_STATIONARY_MAX_RAD_S * 3.0
    gyro_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05,
                np.array([amp, 0.0, 0.0]) if i % 2 == 0 else np.array([-amp, 0.0, 0.0]))
                for i in range(21)]
    accel_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.0, 0.0, 9.81]))
                 for i in range(21)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is False


def test_is_stationary_window_false_for_handling_accel():
    """A window with flat gyro but accel's z-axis swinging well past
    ACCEL_STATIONARY_MAX_MPS2 -- e.g. the limb being lifted/repositioned
    without much rotation -- must not count as stationary. Scaled relative
    to the actual threshold, same reasoning as the gyro case above."""
    now = 10.0
    half_amp = imu.ACCEL_STATIONARY_MAX_MPS2 * 1.5
    gyro_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.01, -0.01, 0.0]))
                for i in range(21)]
    accel_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05,
                 np.array([0.0, 0.0, 9.81 + half_amp]) if i % 2 == 0
                 else np.array([0.0, 0.0, 9.81 - half_amp]))
                 for i in range(21)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is False


def test_is_stationary_window_false_when_buffer_does_not_span_full_window():
    """A buffer that only covers a fraction of GYRO_BIAS_WINDOW_S must not
    count as stationary regardless of content -- a burst of flat readings a
    few ms apart is not evidence of a full still window."""
    now = 10.0
    gyro_buf = [(now - 0.1 + i * 0.01, np.array([0.0, 0.0, 0.0])) for i in range(10)]
    accel_buf = [(now - 0.1 + i * 0.01, np.array([0.0, 0.0, 9.81])) for i in range(10)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is False


def test_imudevice_accel_hold_buf_populated_by_on_accel():
    """on_accel() must append raw accel samples to _accel_hold_buf, mirroring
    on_gyro()'s existing _gyro_hold_buf maintenance, so is_stationary() has
    real data to check."""
    dev = imu._IMUDevice("12.0.1.1")
    dev.on_accel([0.0, 0.0, 9.81], ts=1000)
    dev.on_accel([0.0, 0.0, 9.80], ts=1010)
    assert len(dev._accel_hold_buf) == 2
    np.testing.assert_allclose(dev._accel_hold_buf[-1][1], [0.0, 0.0, 9.80])


def test_imudevice_is_stationary_reflects_its_own_buffers():
    """_IMUDevice.is_stationary() delegates to _is_stationary_window() using
    this device's own _gyro_hold_buf/_accel_hold_buf."""
    dev = imu._IMUDevice("12.0.1.2")
    now = __import__("time").time()
    for i in range(21):
        t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
        dev._gyro_hold_buf.append((t, np.array([0.0, 0.0, 0.0])))
        dev._accel_hold_buf.append((t, np.array([0.0, 0.0, 9.81])))
    assert dev.is_stationary() is True


def test_module_is_stationary_requires_all_connected_devices_stationary():
    """Module-level is_stationary() must return True only if every connected
    device independently reports stationary -- a half-stationary reading
    (one still, one being handled) must not pass."""
    imu.reset_devices()
    imu.clear_zero()
    now = __import__("time").time()

    def _fill_still(dev):
        for i in range(21):
            t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
            dev._gyro_hold_buf.append((t, np.array([0.0, 0.0, 0.0])))
            dev._accel_hold_buf.append((t, np.array([0.0, 0.0, 9.81])))

    def _fill_handled(dev):
        amp = imu.GYRO_STATIONARY_MAX_RAD_S * 3.0
        for i in range(21):
            t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
            v = np.array([amp, 0.0, 0.0]) if i % 2 == 0 else np.array([-amp, 0.0, 0.0])
            dev._gyro_hold_buf.append((t, v))
            dev._accel_hold_buf.append((t, np.array([0.0, 0.0, 9.81])))

    imu._devices["12.0.1.3"] = imu._IMUDevice("12.0.1.3")
    imu._roles["12.0.1.3"] = imu.ROLE_PROXIMAL
    imu._devices["12.0.1.3"].last_rx = now
    _fill_still(imu._devices["12.0.1.3"])

    imu._devices["12.0.1.4"] = imu._IMUDevice("12.0.1.4")
    imu._roles["12.0.1.4"] = imu.ROLE_DISTAL
    imu._devices["12.0.1.4"].last_rx = now
    _fill_handled(imu._devices["12.0.1.4"])

    assert imu.is_stationary() is False, "one handled device must fail the whole check"

    imu._devices["12.0.1.4"]._gyro_hold_buf = []
    imu._devices["12.0.1.4"]._accel_hold_buf = []
    _fill_still(imu._devices["12.0.1.4"])
    assert imu.is_stationary() is True, "once both are still, the check must pass"

    imu.reset_devices()
    imu.clear_zero()


def test_module_is_stationary_false_with_no_connected_devices():
    imu.reset_devices()
    imu.clear_zero()
    assert imu.is_stationary() is False


def test_imudevice_accel_bias_defaults_to_zero():
    """Accel bias should default to zero (no correction) before calibration."""
    dev = imu._IMUDevice("12.0.1.7")
    np.testing.assert_allclose(dev.accel_bias, np.zeros(3))


def test_imudevice_calibrate_accel_bias_from_stillness():
    """calibrate_accel_bias() should estimate bias as the radial
    (magnitude-only) excess of the measured mean accel over gravity's
    expected magnitude, along the measured direction -- not the mean raw
    accel minus a fixed [0, 0, 9.81] reference. A fixed-axis reference
    can't tell a levelled sensor's true offset apart from a merely-tilted
    hold; see calibrate_accel_bias()'s docstring."""
    dev = imu._IMUDevice("12.0.1.8")
    now = __import__("time").time()

    # A stillness hold whose measured gravity is 5% too strong along its
    # own (level) direction -- a pure magnitude bias, no tilt involved.
    true_dir = np.array([0.0, 0.0, 1.0])
    measured_mag = 9.81 * 1.05
    accel_hold_buf = []
    for i in range(21):
        t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
        accel_hold_buf.append((t, true_dir * measured_mag))

    dev.calibrate_accel_bias(accel_hold_buf)

    expected_bias = true_dir * (measured_mag - 9.81)
    np.testing.assert_allclose(dev.accel_bias, expected_bias, atol=1e-6)


def test_imudevice_calibrate_accel_bias_from_stillness_g_units():
    """Regression test: iOS's CoreMotion reports accel in g's (magnitude ~1),
    not the ~9.81 m/s² Android's SensorManager uses. calibrate_accel_bias()
    must recognise the g-unit case from the data's own scale and calibrate
    against gravity=1.0, not silently apply the m/s²-sized constant -- doing
    so corrupts the bias by ~9.81 per axis, which then disables the AHRS's
    accelerometer correction step for the rest of the session and lets the
    fused angle drift indefinitely even while the phone is held still (the
    real-world symptom this test guards against)."""
    dev = imu._IMUDevice("12.0.1.10")
    now = __import__("time").time()

    true_dir = np.array([0.0, 0.0, 1.0])
    measured_mag = 1.03   # 3% magnitude bias, no tilt
    accel_hold_buf = []
    for i in range(21):
        t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
        accel_hold_buf.append((t, true_dir * measured_mag))

    dev.calibrate_accel_bias(accel_hold_buf)

    expected_bias = true_dir * (measured_mag - 1.0)
    np.testing.assert_allclose(dev.accel_bias, expected_bias, atol=1e-6)


def test_imudevice_calibrate_accel_bias_tilted_hold_not_treated_as_bias():
    """Regression test for the 2026-08-10 gravity-direction fix: a hold
    that isn't level (gravity spread across all three axes, e.g. a
    limb-mounted sensor at rest) must NOT have its off-axis components
    zeroed out as if they were sensor bias -- that was the bug that
    produced a corrupted, drift-then-settle angle curve on real trials
    (Participant_13_left_post Trial_4, Participant_5_left_post_1month
    Trial_3). Only the magnitude should be corrected; the measured
    direction must survive calibration essentially unchanged."""
    dev = imu._IMUDevice("12.0.1.9")
    now = __import__("time").time()

    # Real-world-shaped tilted hold (only 57% of magnitude on Z), no bias.
    tilted = np.array([0.74, -0.34, 0.56])
    tilted = tilted / np.linalg.norm(tilted)   # unit direction
    accel_hold_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, tilted * 1.0)
                       for i in range(21)]

    dev.calibrate_accel_bias(accel_hold_buf)
    corrected = tilted - dev.accel_bias

    # The old Z-forcing code would have produced a bias of roughly
    # tilted - [0,0,1], baking the real X/Y tilt into "bias" and leaving
    # corrected close to [0,0,1] -- i.e. erasing the hold's true
    # orientation. The fix must instead leave direction intact.
    cos_sim = float(np.dot(corrected, tilted) / (
        np.linalg.norm(corrected) * np.linalg.norm(tilted)))
    assert cos_sim > 0.999, (
        f"corrected direction diverged from the measured tilt (cos_sim="
        f"{cos_sim:.4f}) -- calibrate_accel_bias() is forcing gravity "
        f"toward an assumed axis again instead of using the measured one")


def test_calibrate_accel_bias_g_units_keeps_ahrs_correction_gate_open():
    """End-to-end regression test for the reported bug: on a g-unit build,
    _a_est self-calibrates from raw (pre-bias) accel *before* zero() ever
    fires -- exactly like production, where the device streams for a bit
    before the auto-tare hold is confirmed. If calibrate_accel_bias() then
    applies the wrong-unit (m/s²) constant, the post-calibration corrected
    accel norm jumps to ~9.81 while _a_est is still anchored near the true
    ~1.0 g-unit scale, permanently closing the AHRS's accelerometer
    correction gate (_do_correct) -- the filter then free-integrates gyro
    drift even while the phone is held still, which is the reported bug."""
    dev = imu._IMUDevice("12.0.1.11")
    now = __import__("time").time()

    still_g = np.array([0.01, -0.02, 1.0])   # a realistic iPhone "at rest" sample

    # Pre-calibration: device streams raw accel (bias not yet set), same as
    # real usage before the auto-tare hold confirms and zero() fires.
    for _ in range(50):
        dev.accel = still_g - dev.accel_bias   # bias is still zero here
        dev.ahrs._a_est = (float(np.linalg.norm(dev.accel)) if dev.ahrs._a_est == 0.0
                            else 0.999 * dev.ahrs._a_est + 0.001 * float(np.linalg.norm(dev.accel)))

    accel_hold_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, still_g)
                       for i in range(21)]
    dev.calibrate_accel_bias(accel_hold_buf)

    # Post-calibration: the phone is still held still, sending the same
    # raw sample -- now bias-corrected with whatever calibrate_accel_bias()
    # computed.
    dev.accel = still_g - dev.accel_bias
    a_norm = float(np.linalg.norm(dev.accel))
    a_est = dev.ahrs._a_est
    gate_open = a_est > 1e-9 and 0.9 * a_est <= a_norm <= 1.1 * a_est
    assert gate_open, (
        f"AHRS accel-correction gate closed after g-unit calibration "
        f"(a_norm={a_norm:.3f}, a_est={a_est:.3f}) -- drift will not "
        f"self-correct while the phone is held still")


def test_imudevice_calibrate_accel_bias_negative_z_at_rest():
    """Regression test: a phone mounted the other way up reads -g on Z at
    rest (confirmed from real recordings). calibrate_accel_bias() must not
    flip toward +g there -- it now derives both magnitude and direction
    from the measured data itself (see docstring), so a pure magnitude
    bias on a -Z-at-rest mounting must be recovered along -Z, not +Z."""
    dev = imu._IMUDevice("12.0.1.12")
    now = __import__("time").time()

    true_dir = np.array([0.0, 0.0, -1.0])
    measured_mag = 1.03   # 3% magnitude bias, no tilt
    accel_hold_buf = []
    for i in range(21):
        t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
        accel_hold_buf.append((t, true_dir * measured_mag))

    dev.calibrate_accel_bias(accel_hold_buf)

    expected_bias = true_dir * (measured_mag - 1.0)
    np.testing.assert_allclose(dev.accel_bias, expected_bias, atol=1e-6)


def test_calibrate_accel_bias_negative_z_keeps_correction_pointed_right_way():
    """End-to-end regression test: after calibrating against a -g-at-rest
    mounting, the bias-corrected accel for a still phone must still point
    close to its own raw (measured) direction -- not be flipped by an
    erroneous +g assumption, and not have its small real-world tilt
    (0.01, -0.02) zeroed out as if it were bias (the 2026-08-10 fix)."""
    dev = imu._IMUDevice("12.0.1.13")
    now = __import__("time").time()

    still_g = np.array([0.01, -0.02, -1.0])   # a realistic "mounted upside down" rest sample
    accel_hold_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, still_g)
                       for i in range(21)]
    dev.calibrate_accel_bias(accel_hold_buf)

    corrected = still_g - dev.accel_bias
    # Corrected accel should stay close to its own measured direction and
    # magnitude (~1.0) -- not flip toward [0,0,+1] the way the old
    # unconditional-+g code would, and not get forced to exactly [0,0,-1]
    # the way the old unconditional-Z-axis code would (that would erase
    # the genuine 0.01/-0.02 tilt).
    assert corrected[2] < 0, (
        f"corrected accel z={corrected[2]:.3f} flipped positive -- the AHRS "
        f"will steer orientation toward the wrong (opposite) gravity "
        f"reference for a phone that reads -g at rest")
    assert abs(float(np.linalg.norm(corrected)) - 1.0) < 1e-6, (
        "corrected accel magnitude should be exactly g after bias removal")
    cos_sim = float(np.dot(corrected, still_g) / (
        np.linalg.norm(corrected) * np.linalg.norm(still_g)))
    assert cos_sim > 0.999, (
        f"corrected direction diverged from the measured tilt (cos_sim="
        f"{cos_sim:.4f}) -- calibrate_accel_bias() is forcing gravity "
        f"toward an assumed axis again instead of using the measured one")


def test_imudevice_on_accel_subtracts_bias():
    """on_accel() should subtract accel_bias from raw accel before storing
    it as self.accel, so AHRS sees bias-corrected data."""
    dev = imu._IMUDevice("12.0.1.9")
    dev.accel_bias = np.array([0.1, 0.0, 0.0])

    # Call on_accel with raw [1.0, 0.0, 9.81]
    dev.on_accel([1.0, 0.0, 9.81], ts=1000)

    # self.accel should be [0.9, 0.0, 9.81] (bias-subtracted)
    np.testing.assert_allclose(dev.accel, [0.9, 0.0, 9.81], atol=1e-6)
