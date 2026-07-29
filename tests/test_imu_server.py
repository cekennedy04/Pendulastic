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
