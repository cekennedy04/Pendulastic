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


def _damped_pendulum_series(duration_s=12.0, dt=0.05, release_t=1.0,
                            neutral_deg=140.0, decay=0.18, freq=0.9):
    """Damped oscillation centered on a sub-180 resting (neutral) angle,
    starting exactly at 180 and decaying toward `neutral_deg`:

        angle(tau) = neutral + (180 - neutral) * exp(-decay*tau) * cos(2*pi*freq*tau)

    At tau=0: exp(0)*cos(0)=1, so angle=neutral+(180-neutral)=180 exactly —
    already continuous with the pre-release hold, no separate release ramp
    needed. For any tau>0, exp(-decay*tau)*cos(...) < 1 strictly, so
    angle < neutral + (180-neutral)*1 = 180 always — the signal can never
    exceed 180 (physically impossible for this convention) regardless of
    the oscillation's phase, unlike a naive "180 - amplitude*cos(...)"
    formula centered on 180 itself, which swings above 180 whenever
    cos(...) goes negative."""
    t = np.arange(0, duration_s, dt)
    angle = np.full_like(t, 180.0)
    amplitude = 180.0 - neutral_deg
    for i, ti in enumerate(t):
        if ti >= release_t:
            tau = ti - release_t
            angle[i] = (neutral_deg
                       + amplitude * math.exp(-decay * tau) * math.cos(2 * math.pi * freq * tau))
    return t, angle


def test_score_waveform_good_signal_passes():
    t, angle = _damped_pendulum_series()
    result = tuner.score_waveform(t, angle)
    assert result["passes"], result
    assert result["params"] is not None


def test_score_waveform_bad_start_fails():
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    angle[:10] = 140.0   # doesn't start near 180
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]


def test_score_waveform_clipped_step_fails():
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    mid = len(angle) // 2
    angle[mid] = angle[mid - 1] + 60.0   # impossible single-tick jump
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]


def test_score_waveform_plateau_during_active_swing_fails():
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    # Freeze a long run of samples right after release (well inside the
    # active-swing window) -> staircase artifact.
    release_i = int(1.0 / 0.05)
    angle[release_i + 2: release_i + 12] = angle[release_i + 2]
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]


def test_score_waveform_long_resting_tail_after_one_drop_still_passes():
    """Severe-spasticity case: one real drop, then genuinely locked/motionless
    for the rest of a long trial. Must NOT be misclassified as a staircase."""
    t = np.arange(0, 20.0, 0.05)
    angle = np.full_like(t, 180.0)
    release_t = 1.0
    for i, ti in enumerate(t):
        if release_t <= ti < release_t + 1.0:
            tau = ti - release_t
            angle[i] = 180.0 - 35.0 * (tau / 1.0)   # smooth single drop to ~145
        elif ti >= release_t + 1.0:
            angle[i] = 145.0   # locked — flat for the remaining ~18s
    result = tuner.score_waveform(t, angle)
    assert result["passes"], (
        "a genuine single-drop-then-lock severe-spasticity trial must pass, "
        f"got penalty={result['penalty']}, params={result['params']}")


def test_score_waveform_trick_oversmoothed_no_oscillation_fails():
    """A technically plateau-free but physically flat (no real swing) curve
    must be rejected by the truthfulness gate (D), even though A-C alone
    would not catch it."""
    t = np.arange(0, 12.0, 0.05)
    # Tiny monotonic sag with no oscillation and no plateau at all.
    angle = 180.0 - 2.0 * (1.0 - np.exp(-0.05 * t))
    result = tuner.score_waveform(t, angle)
    assert not result["passes"], "a no-oscillation curve must fail the truthfulness gate"


def test_score_waveform_too_short_series_fails():
    t = np.arange(0, 0.5, 0.05)
    angle = np.full_like(t, 180.0)
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]
    assert result["params"] is None
