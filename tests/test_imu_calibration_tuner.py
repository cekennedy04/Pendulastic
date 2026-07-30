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


def test_replay_trial_holds_near_180_before_release():
    """Before the burst, held nearly still, the displayed angle must read ~180."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    pre_release = angle[t < 0.9]
    assert len(pre_release) > 0
    assert abs(float(np.nanmedian(pre_release)) - 180.0) < 1.0


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
