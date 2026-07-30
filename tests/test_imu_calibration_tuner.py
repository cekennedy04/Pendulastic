import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import imu_calibration_tuner as tuner


def _solo_hold_then_burst_samples():
    """Synthetic single-phone (distal) raw log: hold still for 1s (seeds AHRS
    to identity via gravity_seed, then holds), then a scripted 0.5s gyro burst
    of exactly 2.0 rad/s around Y — a known, hand-computable rotation."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    # Seed once, then hold (gyro ~0) for 1.0s.
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
    n_hold = 100
    for i in range(n_hold):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    # Deliberate burst: 2.0 rad/s around Y for 0.5s (50 steps) -> 1.0 rad total.
    n_burst = 50
    for i in range(n_burst):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
    # Settle: hold again so there's enough trailing data.
    for i in range(100):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    return samples


def test_replay_trial_matches_hand_computed_rotation():
    """With beta=0.0 (accel correction fully disabled), the AHRS is pure gyro
    integration, so the post-burst angle must match the analytically expected
    180 - degrees(2.0 rad/s * 0.5s) = 180 - 57.2958 = 122.7042 deg."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,   # ema_alpha=1.0 -> no smoothing lag
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    assert len(t) > 0
    expected_final = 180.0 - math.degrees(2.0 * 0.5)
    assert abs(angle[-1] - expected_final) < 1.0, (
        f"expected ~{expected_final:.2f} deg, got {angle[-1]:.2f} deg")


def test_replay_trial_first_tick_is_nan_rest_are_finite():
    """Contract: tick 0 always precedes any processed sample (tick_times[0]
    always equals raw_samples[0]["t"] exactly), so no device state exists yet
    and it is always NaN. Every later tick, once zeroed, must be finite.
    Pinning this explicitly (not just working around it in another test)
    stops it from silently regressing into more than one leading NaN."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    assert math.isnan(angle[0])
    assert np.isfinite(angle[1:]).all()


def test_replay_trial_holds_near_180_before_release():
    """Before the burst, held nearly still, the displayed angle must read ~180."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    pre_release = angle[(t < 0.9) & np.isfinite(angle)]
    assert len(pre_release) > 0
    assert abs(float(np.median(pre_release)) - 180.0) < 1.0


def test_replay_trial_empty_samples_returns_empty_arrays():
    t, angle = tuner.replay_trial([], {"beta": 0.041, "ema_alpha": 0.3,
                                       "flex_axis_capture": True, "gravity_seed": True})
    assert len(t) == 0 and len(angle) == 0


def test_replay_trial_no_motion_ever_returns_empty_arrays():
    """A trial with no gyro burst above threshold never zeroes -> unscoreable."""
    samples = []
    t = 0.0
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": 0})
    for i in range(50):
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})
    t_arr, angle = tuner.replay_trial(samples, {"beta": 0.041, "ema_alpha": 0.3,
                                                "flex_axis_capture": True,
                                                "gravity_seed": True})
    assert len(t_arr) == 0 and len(angle) == 0


def test_replay_trial_flex_axis_capture_excludes_out_of_plane_rotation():
    """Two sequential bursts about orthogonal axes: flex_axis is captured from
    the FIRST (Y-axis) burst. flex_axis_capture=True must project out the
    second (X-axis) burst's contribution; flex_axis_capture=False reports the
    axis-agnostic total rotation, which includes both. beta=0.0 isolates pure
    gyro integration so the two settings' difference isn't confounded by the
    accelerometer correction term."""
    samples = []
    samples.append({"t": 0.0, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": 0})
    t = 0.0
    for _ in range(30):   # burst 1: 0.3s about Y -> captures flex_axis=[0,1,0]
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": int(t * 1000)})
    for _ in range(30):   # burst 2: 0.3s about X -- orthogonal to flex_axis
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [2.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})
    for _ in range(50):
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})

    base = {"beta": 0.0, "ema_alpha": 1.0, "gravity_seed": True}
    _, angle_projected = tuner.replay_trial(samples, {**base, "flex_axis_capture": True})
    _, angle_total      = tuner.replay_trial(samples, {**base, "flex_axis_capture": False})
    assert abs(angle_projected[-1] - angle_total[-1]) > 5.0, (
        "flex_axis_capture must measurably change the result once a second, "
        "orthogonal rotation is introduced after the axis is captured "
        f"(projected={angle_projected[-1]:.2f}, total={angle_total[-1]:.2f})")


def test_replay_trial_gravity_seed_changes_zero_reference_under_correction():
    """q_zero is captured on the FIRST qualifying gyro sample -- if that is
    the very first sample in the log, it is captured BEFORE any ahrs.update()
    call, so it equals whatever on_accel's seeding produced verbatim. A
    tilted accel makes gravity_seed=True seed q far from identity while
    gravity_seed=False leaves it at identity; with beta>0 (correction
    active), that starting-point difference measurably changes the
    subsequent trajectory rather than cancelling out (which it would if
    beta were 0 -- see the plan's Section 4 discussion)."""
    samples = []
    tilt_deg = 60.0
    samples.append({
        "t": 0.0, "role": "distal", "sensor": "accel",
        "v": [9.81 * math.sin(math.radians(tilt_deg)), 0.0,
              9.81 * math.cos(math.radians(tilt_deg))],
        "phone_ts_ms": 0,
    })
    t = 0.0
    for _ in range(80):   # first gyro sample triggers onset immediately
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": int(t * 1000)})
    for _ in range(50):
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})

    base = {"beta": 0.15, "ema_alpha": 1.0, "flex_axis_capture": True}
    _, angle_seeded   = tuner.replay_trial(samples, {**base, "gravity_seed": True})
    _, angle_unseeded = tuner.replay_trial(samples, {**base, "gravity_seed": False})
    assert abs(angle_seeded[-1] - angle_unseeded[-1]) > 1.0, (
        "gravity_seed must measurably change the replayed series when "
        "correction (beta>0) is active and the accel is tilted "
        f"(seeded={angle_seeded[-1]:.2f}, unseeded={angle_unseeded[-1]:.2f})")
