#!/usr/bin/env python3
"""
Quick integration test for Task 10: Verify accel bias correction is wired
correctly in both live (pendulastic_imu_server.py) and offline (imu_calibration_tuner.py) paths.
"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pendulastic_imu_server as imu_server
import imu_calibration_tuner as tuner

print("=" * 70)
print("INTEGRATION TEST: Task 10 - Accel Bias Correction")
print("=" * 70)

# Test 1: Live path - verify accel_bias attribute exists and defaults to zero
print("\n[TEST 1] Live path: _IMUDevice.accel_bias initialization")
dev = imu_server._IMUDevice("test.device.1.2.3")
assert hasattr(dev, 'accel_bias'), "accel_bias attribute missing"
assert np.allclose(dev.accel_bias, np.zeros(3)), "accel_bias should default to zero"
print("✓ PASS: _IMUDevice.accel_bias initialized to [0, 0, 0]")

# Test 2: Live path - verify calibrate_accel_bias method exists
print("\n[TEST 2] Live path: calibrate_accel_bias() method")
assert hasattr(dev, 'calibrate_accel_bias'), "calibrate_accel_bias method missing"
test_buf = [(1.0, np.array([0.1, -0.05, 10.0])),
            (1.05, np.array([0.1, -0.05, 10.0]))]
dev.calibrate_accel_bias(test_buf)
expected_bias = np.array([0.1, -0.05, 0.19])  # mean minus gravity
assert np.allclose(dev.accel_bias, expected_bias, atol=0.01), \
    f"accel_bias estimation failed: got {dev.accel_bias}, expected {expected_bias}"
print(f"✓ PASS: calibrate_accel_bias() estimates bias = {dev.accel_bias}")

# Test 3: Live path - verify on_accel subtracts bias
print("\n[TEST 3] Live path: on_accel() subtracts bias")
dev2 = imu_server._IMUDevice("test.device.2.2.3")
dev2.accel_bias = np.array([0.1, 0.0, 0.0])
raw_accel = np.array([1.0, 0.0, 9.81])
dev2.on_accel(raw_accel, ts=1000)
expected_bias_corrected = raw_accel - dev2.accel_bias
assert np.allclose(dev2.accel, expected_bias_corrected, atol=1e-6), \
    f"on_accel bias correction failed: got {dev2.accel}, expected {expected_bias_corrected}"
print(f"✓ PASS: on_accel() subtracts bias correctly: raw={raw_accel} -> corrected={dev2.accel}")

# Test 4: Offline path - verify _RoleState.accel_bias exists
print("\n[TEST 4] Offline path: _RoleState.accel_bias initialization")
state = tuner._RoleState(beta=0.041)
assert hasattr(state, 'accel_bias'), "_RoleState.accel_bias attribute missing"
assert np.allclose(state.accel_bias, np.zeros(3)), "_RoleState.accel_bias should default to zero"
print("✓ PASS: _RoleState.accel_bias initialized to [0, 0, 0]")

# Test 5: Offline path - verify calibrate_accel_bias method
print("\n[TEST 5] Offline path: _RoleState.calibrate_accel_bias() method")
assert hasattr(state, 'calibrate_accel_bias'), "_RoleState.calibrate_accel_bias method missing"
state.accel_hold_buf = [(1.0, np.array([0.1, -0.05, 10.0])),
                        (1.05, np.array([0.1, -0.05, 10.0]))]
state.calibrate_accel_bias()
expected_bias = np.array([0.1, -0.05, 0.19])
assert np.allclose(state.accel_bias, expected_bias, atol=0.01), \
    f"_RoleState accel_bias estimation failed: got {state.accel_bias}, expected {expected_bias}"
print(f"✓ PASS: _RoleState.calibrate_accel_bias() estimates bias = {state.accel_bias}")

# Test 6: Integration - verify replay_trial doesn't crash with accel bias
print("\n[TEST 6] Integration: replay_trial() with accel bias correction")
samples = []
t, dt, ts_ms = 0.0, 0.01, 0
n_hold = 100
for i in range(n_hold):
    raw_accel = np.array([0.05, -0.02, 9.81])  # accel with bias
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": list(raw_accel), "phone_ts_ms": ts_ms})
    samples.append({"t": t, "role": "distal", "sensor": "gyro",
                    "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    t += dt; ts_ms += 10

n_burst = 50
for i in range(n_burst):
    raw_accel = np.array([0.05, -0.02, 9.81])
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": list(raw_accel), "phone_ts_ms": ts_ms})
    samples.append({"t": t, "role": "distal", "sensor": "gyro",
                    "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
    t += dt; ts_ms += 10

for i in range(100):
    raw_accel = np.array([0.05, -0.02, 9.81])
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": list(raw_accel), "phone_ts_ms": ts_ms})
    samples.append({"t": t, "role": "distal", "sensor": "gyro",
                    "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    t += dt; ts_ms += 10

params = {"beta": 0.041, "ema_alpha": 1.0,
          "flex_axis_capture": True, "gravity_seed": True}
try:
    t_result, angle_result = tuner.replay_trial(samples, params)
    assert len(t_result) > 0, "replay_trial should produce output"
    assert np.isfinite(angle_result[-1]), "final angle should be finite"
    print(f"✓ PASS: replay_trial() completed successfully with accel bias correction")
    print(f"  Result: {len(t_result)} time points, final angle={angle_result[-1]:.2f}°")
except Exception as e:
    print(f"✗ FAIL: replay_trial() crashed: {e}")
    sys.exit(1)

print("\n" + "=" * 70)
print("ALL INTEGRATION TESTS PASSED ✓")
print("=" * 70)
print("\nTask 10 Accel Bias Correction:")
print("  ✓ Live path (pendulastic_imu_server.py) working correctly")
print("  ✓ Offline path (imu_calibration_tuner.py) working correctly")
print("  ✓ Integration verified - no crashes or regressions detected")
print("=" * 70)
