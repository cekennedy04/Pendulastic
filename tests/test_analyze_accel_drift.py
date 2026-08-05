import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np

import analyze_accel_drift as drift


def test_double_integrate_drift_zero_accel_gives_zero_velocity_and_displacement():
    t = np.arange(0, 1.0, 0.01)
    accel_world = np.zeros((len(t), 3))
    stationary_mask = np.ones(len(t), dtype=bool)
    vel, disp = drift.double_integrate_drift(t, accel_world, stationary_mask)
    np.testing.assert_allclose(vel, 0.0, atol=1e-9)
    np.testing.assert_allclose(disp, 0.0, atol=1e-9)


def test_double_integrate_drift_constant_accel_matches_kinematics():
    """With a genuinely constant 1.0 m/s^2 in x over 1.0s, starting and
    ending at rest is NOT the physical scenario here -- this checks the raw
    (uncorrected) double integration matches high-school kinematics
    (v = a*t, x = 0.5*a*t^2) before any zero-velocity correction is applied,
    i.e. with an all-False stationary_mask (no correction reference points)."""
    t = np.arange(0, 1.0, 0.001)
    accel_world = np.zeros((len(t), 3))
    accel_world[:, 0] = 1.0
    stationary_mask = np.zeros(len(t), dtype=bool)
    vel, disp = drift.double_integrate_drift(t, accel_world, stationary_mask)
    np.testing.assert_allclose(vel[-1, 0], 1.0 * t[-1], atol=0.02)
    np.testing.assert_allclose(disp[-1, 0], 0.5 * 1.0 * t[-1] ** 2, atol=0.02)


def test_double_integrate_drift_zupt_correction_pulls_velocity_to_zero_at_still_points():
    """A small constant accel offset (simulating uncorrected sensor drift)
    integrated over a window that starts and ends in a verified-stationary
    region: naive integration would leave nonzero velocity at the second
    still point; the ZUPT-style correction should report ~zero drift there
    since that's exactly what it's designed to null out at each stationary
    checkpoint."""
    t = np.arange(0, 2.0, 0.01)
    accel_world = np.zeros((len(t), 3))
    accel_world[:, 0] = 0.05   # small constant drift-like offset
    stationary_mask = np.zeros(len(t), dtype=bool)
    stationary_mask[:20] = True     # still for the first 0.2s
    stationary_mask[-20:] = True    # still again for the last 0.2s
    vel, disp = drift.double_integrate_drift(t, accel_world, stationary_mask)
    assert abs(vel[-1, 0]) < 1e-6, (
        "velocity at a verified-stationary checkpoint must be corrected to zero")
