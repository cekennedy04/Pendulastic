# tests/test_imu_server.py
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pendulastic_imu_server as imu


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
