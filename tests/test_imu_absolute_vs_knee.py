"""Tests for imu_absolute_vs_knee.

These are the diagnostics the 2026-08-28 over-read investigation rests on, so
they are checked against ANALYTICALLY KNOWN rotations rather than merely
exercised: a synthetic trial is rotated by a chosen number of degrees and each
measure has to recover that number. A diagnostic that cannot recover a known
answer cannot be trusted to characterise a real one.
"""
import numpy as np
import pytest

import imu_absolute_vs_knee as m


def _rot(axis, deg):
    """Rodrigues rotation matrix for `deg` about `axis`."""
    u = np.asarray(axis, dtype=float)
    u = u / np.linalg.norm(u)
    th = np.radians(deg)
    k = np.array([[0, -u[2], u[1]], [u[2], 0, -u[0]], [-u[1], u[0], 0]])
    return np.eye(3) + np.sin(th) * k + (1 - np.cos(th)) * (k @ k)


def _synthetic_trial(sweep_deg, fs=500.0, hold_s=1.0, settle_s=1.0,
                     rate_rad_s=3.0, axis=(0.0, 0.0, 1.0), g_mag=1.0):
    """A still hold, a constant-rate rotation of `sweep_deg`, then a still settle.

    The swing duration is derived from `rate_rad_s` rather than fixed, so every
    sweep -- including the small ones -- turns fast enough to clear
    MOTION_THRESHOLD_RAD_S. A fixed 2 s swing put a 15 deg sweep at 0.13 rad/s,
    well under the 1.0 rad/s gate, and motion_window correctly saw no motion.

    Gravity is expressed in the SENSOR frame, so it rotates opposite to the
    sensor. Accel is in g (|a| ~= 1.0) by default, matching the real feed --
    assuming m/s^2 is exactly the bug that made the first corpus pass reject
    all 93 trials.
    """
    n_hold, n_settle = int(hold_s * fs), int(settle_s * fs)
    n_swing = max(4, int(round(np.radians(sweep_deg) / rate_rad_s * fs)))
    t = np.arange(n_hold + n_swing + n_settle) / fs
    u = np.asarray(axis, dtype=float) / np.linalg.norm(axis)
    # Rate set from the sample count actually used, so the integral of the
    # synthetic gyro equals sweep_deg rather than approximately equalling it.
    rate = np.radians(sweep_deg) / ((n_swing - 1) / fs)

    gyro = np.zeros((len(t), 3))
    gyro[n_hold:n_hold + n_swing] = u * rate

    g_world = np.array([0.0, -g_mag, 0.0])
    accel = np.empty((len(t), 3))
    for i in range(len(t)):
        if i < n_hold:
            done = 0.0
        elif i < n_hold + n_swing:
            done = sweep_deg * (i - n_hold) / (n_swing - 1)
        else:
            done = sweep_deg
        accel[i] = _rot(u, -done) @ g_world
    return t, accel, gyro, u


@pytest.mark.parametrize("sweep", [15.0, 40.0, 70.0, 110.0])
def test_gravity_and_gyro_both_recover_a_known_rotation(sweep):
    """Both measures must land on the true angle, and so on each other.

    This is the property the investigation leans on: an integration-free
    measure agreeing with an integrated one is strong evidence that neither is
    wrong, since they fail in unrelated ways.
    """
    t, accel, gyro, axis = _synthetic_trial(sweep)
    win = m.motion_window(t, gyro)
    assert win is not None
    t0, t1 = win

    from_g = m.net_rotation_from_gravity(t, accel, t0, t1)
    from_w = m.net_rotation_from_gyro(t, gyro, axis, t0, t1)

    assert from_g == pytest.approx(sweep, abs=1.5), from_g
    assert from_w == pytest.approx(sweep, abs=1.5), from_w
    assert from_g / from_w == pytest.approx(1.0, abs=0.03)


def test_gravity_measure_needs_no_axis_and_survives_a_tilted_one():
    """The gravity measure takes no axis argument, so a tilted swing plane
    costs it nothing -- that independence is why it can arbitrate the gyro."""
    t, accel, gyro, _ = _synthetic_trial(60.0, axis=(0.3, 0.0, 0.95))
    t0, t1 = m.motion_window(t, gyro)
    assert m.net_rotation_from_gravity(t, accel, t0, t1) == pytest.approx(60.0, abs=1.5)


def test_gyro_projection_under_reads_on_a_wrong_axis_never_over_reads():
    """Projection can only shed magnitude. This is load-bearing: it means the
    gyro integral sitting ABOVE OptiTrack cannot be blamed on a bad flex axis."""
    t, _accel, gyro, axis = _synthetic_trial(60.0)
    t0, t1 = m.motion_window(t, gyro)
    true = m.net_rotation_from_gyro(t, gyro, axis, t0, t1)
    for wrong in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.7, 0.7, 0.2), (1.0, 1.0, 1.0)):
        got = m.net_rotation_from_gyro(t, gyro, wrong, t0, t1)
        assert got <= true + 1e-6, (wrong, got, true)


def test_accel_in_g_units_is_accepted():
    """Regression: the static gate compared against 9.81 and threw away every
    trial in the corpus, because this feed is in g (|a| ~= 1.00)."""
    t, accel, gyro, _ = _synthetic_trial(50.0, g_mag=1.0)
    t0, t1 = m.motion_window(t, gyro)
    assert m.net_rotation_from_gravity(t, accel, t0, t1) is not None
    t2, accel2, gyro2, _ = _synthetic_trial(50.0, g_mag=9.81)
    t0b, t1b = m.motion_window(t2, gyro2)
    assert m.net_rotation_from_gravity(t2, accel2, t0b, t1b) is not None


def test_gravity_measure_refuses_a_non_static_window():
    """If the window is not still, the accelerometer is reading gravity PLUS
    motion and the answer would be quietly wrong. None beats wrong."""
    t, accel, gyro, _ = _synthetic_trial(50.0)
    t0, t1 = m.motion_window(t, gyro)
    # Shake the settle WINDOW itself -- the 0.5 s immediately after motion
    # stops. Perturbing the trial's last samples does nothing, because the
    # window closes long before the recording does.
    shaking = (t > t1) & (t <= t1 + m.DEFAULT_WINDOW_S)
    assert shaking.sum() > 10, "settle window should be populated"
    accel[shaking] += np.array([4.0, 4.0, 4.0])
    assert m.net_rotation_from_gravity(t, accel, t0, t1) is None


def test_motion_window_returns_none_for_a_trial_that_never_moves():
    t = np.arange(300) / 100.0
    assert m.motion_window(t, np.zeros((300, 3))) is None


def test_plate_sweep_recovers_a_known_plate_rotation():
    plate = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.08, 0.0]])
    t = np.arange(300) / 100.0
    pts = np.empty((300, 3, 3))
    for i in range(300):
        deg = 0.0 if i < 100 else (35.0 if i >= 200 else 35.0 * (i - 100) / 100.0)
        pts[i] = plate @ _rot((0, 0, 1), deg).T
    assert m.plate_sweep(pts, np.ones(300, bool), t) == pytest.approx(35.0, abs=1.0)


def test_plate_sweep_skips_untracked_frames_rather_than_interpolating():
    """Dropped frames must not be invented. The recovered angle should come
    only from frames the cameras saw."""
    plate = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.08, 0.0]])
    t = np.arange(300) / 100.0
    pts = np.empty((300, 3, 3))
    for i in range(300):
        deg = 0.0 if i < 100 else (35.0 if i >= 200 else 35.0 * (i - 100) / 100.0)
        pts[i] = plate @ _rot((0, 0, 1), deg).T
    tracked = np.ones(300, bool)
    tracked[120:180] = False              # markers lost mid-swing
    pts[120:180] = np.nan
    assert m.plate_sweep(pts, tracked, t) == pytest.approx(35.0, abs=1.0)


def test_plate_sweep_rejects_a_degenerate_cluster():
    t = np.arange(60) / 100.0
    pts = np.zeros((60, 2, 3))            # only two markers: not a plate
    assert m.plate_sweep(pts, np.ones(60, bool), t) is None


def test_is_single_sensor_detects_a_missing_distal_role():
    solo = [{"role": "proximal", "sensor": "gyro"}] * 5
    paired = solo + [{"role": "distal", "sensor": "gyro"}]
    assert m.is_single_sensor(solo) is True
    assert m.is_single_sensor(paired) is False


def test_measures_reject_malformed_input_instead_of_guessing():
    t = np.arange(50) / 100.0
    assert m.motion_window(t, np.zeros((50, 2))) is None            # not 3-vectors
    assert m.motion_window(t[:10], np.zeros((50, 3))) is None       # length mismatch
    # A zero gyro is not malformed -- 0.0 degrees is the right answer, and
    # returning None there would hide a genuinely motionless trial.
    assert m.net_rotation_from_gyro(t, np.zeros((50, 3)), (0, 0, 1), 0.0, 0.1) == 0.0
    assert m.net_rotation_from_gyro(t, np.zeros((50, 2)), (0, 0, 1), 0.0, 0.1) is None
    assert m.net_rotation_from_gravity(t, np.zeros((50, 2)), 0.1, 0.2) is None
