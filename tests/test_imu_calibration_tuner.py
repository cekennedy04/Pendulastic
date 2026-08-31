import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pytest
import imu_calibration_tuner as tuner
import pendulastic_imu_server as imu


def _solo_hold_then_burst_samples():
    """Synthetic single-phone (distal) raw log: hold still for 1s (seeds AHRS
    to identity via gravity_seed, then holds), then a scripted 0.5s gyro burst
    of exactly 2.0 rad/s around Y — a known, hand-computable rotation. Accel
    streams continuously (not just once at t=0) at gravity, matching a real
    device, since the stillness gate now requires a full accel window too."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    n_hold = 100
    for i in range(n_hold):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    # Deliberate burst: 2.0 rad/s around Y for 0.5s (50 steps) -> 1.0 rad total.
    n_burst = 50
    for i in range(n_burst):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    # Settle: hold again so there's enough trailing data.
    for i in range(100):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
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


def test_replay_trial_defaults_to_no_magnetometer(monkeypatch):
    """Backward compatibility: params without "use_mag" (the pre-existing
    shape every other test/caller uses) must still call ahrs.update() with
    mag=None, unchanged from before use_mag existed."""
    samples = _solo_hold_then_burst_samples()
    seen_mag = []
    orig_update = tuner.MadgwickAHRS.update
    def spy_update(self, gyro, accel, mag, dt):
        seen_mag.append(mag)
        return orig_update(self, gyro, accel, mag, dt)
    monkeypatch.setattr(tuner.MadgwickAHRS, "update", spy_update)

    params = {"beta": 0.041, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    tuner.replay_trial(samples, params)
    assert seen_mag, "ahrs.update() was never called"
    assert all(m is None for m in seen_mag)


def test_replay_trial_use_mag_true_passes_real_magnetometer_data(monkeypatch):
    """params["use_mag"]=True must thread the trial's own mag samples into
    ahrs.update() instead of the default None -- the 2026-08-17 methodology
    comparison's mechanism for measuring whether magnetometer fusion helps
    or hurts real RMSE. Never set True in a persisted/live config."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    mag_reading = [10.0, 5.0, 2.0]
    for i in range(120):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0] if i < 100 else [0.0, 2.0, 0.0],
                        "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "mag",
                        "v": mag_reading, "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    seen_mag = []
    orig_update = tuner.MadgwickAHRS.update
    def spy_update(self, gyro, accel, mag, dt):
        seen_mag.append(mag)
        return orig_update(self, gyro, accel, mag, dt)
    monkeypatch.setattr(tuner.MadgwickAHRS, "update", spy_update)

    params = {"beta": 0.041, "ema_alpha": 1.0, "flex_axis_capture": True,
              "gravity_seed": True, "use_mag": True}
    tuner.replay_trial(samples, params)
    assert seen_mag, "ahrs.update() was never called"
    # First call(s) happen before any mag sample has arrived (mag starts
    # None until the first "mag" sensor sample is processed) -- only assert
    # on calls after mag data has actually landed.
    post_mag_calls = [m for m in seen_mag if m is not None]
    assert post_mag_calls, "use_mag=True never threaded real mag data through"
    for m in post_mag_calls:
        np.testing.assert_allclose(m, mag_reading)


def _solo_hold_with_bias_then_burst_samples(bias):
    """Like _solo_hold_then_burst_samples, but every gyro sample -- hold,
    burst, and settle alike -- carries a constant additive bias, as a real
    stationary MEMS gyro would report (it doesn't only appear while still).
    The hold phase is what the gyro-bias calibration should measure from;
    if correctly subtracted, the burst should still integrate to the same
    true rotation as the zero-bias case. Accel streams continuously at
    gravity throughout, matching a real device."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    bx, by, bz = bias
    n_hold = 100
    for i in range(n_hold):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, by, bz], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    n_burst = 50
    for i in range(n_burst):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, 2.0 + by, bz], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    for i in range(100):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, by, bz], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    return samples


def test_replay_trial_subtracts_calibrated_gyro_bias():
    """A constant gyro bias present throughout the log (not just while still)
    must be measured from a genuinely stable pre-burst hold and subtracted
    from the burst too, so the biased trial's final angle matches a zero-bias
    control run -- not the leftover discrepancy an uncorrected bias would
    accumulate over the ~1.5s trial. Uses a realistic nonzero beta (accel
    correction must be active for the stability check itself to hold pitch/
    roll flat during the injected-bias hold) and compares against a
    zero-bias control rather than a hand-derived constant, so the same
    stale-accel-during-burst artifact common to both runs cancels out."""
    params = {"beta": 0.041, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    clean_samples = _solo_hold_with_bias_then_burst_samples((0.0, 0.0, 0.0))
    bias = (0.05, -0.08, 0.03)   # rad/s, comparable to a real MEMS offset
    biased_samples = _solo_hold_with_bias_then_burst_samples(bias)

    t_clean, angle_clean = tuner.replay_trial(clean_samples, params)
    t_biased, angle_biased = tuner.replay_trial(biased_samples, params)

    assert abs(angle_biased[-1] - angle_clean[-1]) < 1.0, (
        f"bias-corrected run ({angle_biased[-1]:.2f} deg) should match the "
        f"zero-bias control ({angle_clean[-1]:.2f} deg) once the injected "
        f"bias is properly measured and subtracted")


def _solo_handling_then_hold_then_burst_samples():
    """Synthetic single-phone (distal) raw log with a REALISTIC contamination
    scenario: 1.5s of examiner handling (gyro OSCILLATING DIRECTION on one
    axis, at 3x GYRO_STATIONARY_MAX_RAD_S -- comparable to the 12.7 deg/s
    case that motivated this fix -- with accel also swinging past
    ACCEL_STATIONARY_MAX_MPS2, i.e. NOT genuinely still), then a genuine
    1.0s still hold, then the same scripted burst as
    _solo_hold_then_burst_samples(). The bias calibration must fire from the
    genuine hold, never from the handling window. Scaled relative to the
    actual thresholds (not a hardcoded literal) so this test stays correct
    regardless of what Task 1 picked. Oscillating direction, not just
    varying magnitude, matches how _is_stationary_window's per-axis check
    actually works (see Task 2). gyro_amp is also capped below (a margin
    under) _FLEX_CAPTURE_THRESHOLD: that threshold gates a SEPARATE,
    pre-existing onset/flex-axis-capture mechanism keyed off a single
    sample's raw magnitude (not this test's target, the stillness gate's
    peak-to-peak check) -- with the current constants (0.9 rad/s stationary
    bound vs. 1.0 rad/s capture threshold) an uncapped 3x multiplier would
    itself exceed the capture threshold and trip onset on the handling
    window's very first sample, corrupting flex_axis before the real burst
    and making the test fail for an unrelated reason."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    gyro_amp = min(imu.GYRO_STATIONARY_MAX_RAD_S * 3.0,
                   imu._FLEX_CAPTURE_THRESHOLD * 0.7)
    accel_half_amp = imu.ACCEL_STATIONARY_MAX_MPS2 * 1.5

    n_handling = 150
    for i in range(n_handling):
        gv = [gyro_amp, 0.0, 0.0] if i % 2 == 0 else [-gyro_amp, 0.0, 0.0]
        av = [0.0, 0.0, 9.81 + accel_half_amp] if i % 2 == 0 else [0.0, 0.0, 9.81 - accel_half_amp]
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": av, "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": gv, "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    # 2.5s (not the bias check's own 1.0s minimum): _recently_calm's
    # zero-capture guard (2026-08-07 fix) needs its OWN full trailing
    # window of samples entirely below _ZERO_CAPTURE_GUARD_RAD_S, which
    # only starts accumulating once the handling window's higher-magnitude
    # tail has fully aged out of the trailing buffer -- a 1.0s hold leaves
    # no margin for that on top of the bias check's own requirement.
    n_hold = 250
    for i in range(n_hold):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    n_burst = 50
    for i in range(n_burst):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    for i in range(100):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    return samples


def test_replay_trial_ignores_handling_window_when_calibrating_bias():
    """The core regression test for this fix: a pre-burst window with real
    (not fused-angle-smoothed) raw gyro/accel handling motion must not be
    averaged into gyro_bias. Only the genuine still hold that follows it
    should be used -- so the final swing angle must match the clean,
    no-handling control run (_solo_hold_then_burst_samples), not be distorted
    by treating the handling window's motion as "bias."""
    params = {"beta": 0.041, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    clean_samples = _solo_hold_then_burst_samples()
    handled_samples = _solo_handling_then_hold_then_burst_samples()

    t_clean, angle_clean = tuner.replay_trial(clean_samples, params)
    t_handled, angle_handled = tuner.replay_trial(handled_samples, params)

    # Tolerance loosened from the original 1.0 to 2.0 deg: now that
    # zero-capture (not just gyro_bias) is also gated on st.ever_calm /
    # _recently_calm (2026-08-07 fix), the handling window's tail delays
    # exactly when the trailing window first reads "calm" by a few ticks
    # versus the no-handling control, shifting q_zero's capture point by
    # that much and producing a small residual difference unrelated to
    # what this test guards against (bias contamination distorting the
    # swing by tens of degrees, not ~1 deg).
    assert abs(angle_handled[-1] - angle_clean[-1]) < 2.0, (
        f"handling-contaminated run ({angle_handled[-1]:.2f} deg) should match "
        f"the clean control ({angle_clean[-1]:.2f} deg) -- the handling window "
        f"must be rejected by the stillness gate, not averaged into gyro_bias")


def _solo_immediate_contamination_then_hold_then_burst_samples():
    """Synthetic single-phone (distal) raw log where the very first samples
    are ALREADY in motion above _FLEX_CAPTURE_THRESHOLD -- no preceding
    stillness at all -- e.g. the examiner still positioning/releasing the
    sensor as the recording starts. Comparable to two real trials found on
    disk (Participant_15/Left/pre/Trial_4, Participant_13_left_post/
    Session_post/Position_1/Height_Joint-Level/Trial_4) whose raw gyro
    magnitude was already >0.7 rad/s by t=0.002s -- well above the 1.0
    rad/s capture threshold -- before any genuine still hold, and which
    scored 28-33 deg RMSE / 27-28 deg bias against OptiTrack, versus a
    ~7-11 deg RMSE floor for trials whose gyro stayed under ~0.2 rad/s for
    the first full second. Then a genuine still hold, then the same
    scripted burst as _solo_hold_then_burst_samples(): 2.0 rad/s around Y
    for 0.5s -- a known, hand-computable rotation.

    n_hold=250 (not the minimum ~100 that would just clear
    GYRO_BIAS_WINDOW_S): at exactly 100, the contamination's last sample
    only falls outside _recently_calm's trailing window by a hair of
    floating-point rounding on t -- real margin, not an off-by-one on a
    razor's edge, is what should make this test robust."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    contam_amp = imu._FLEX_CAPTURE_THRESHOLD * 1.5
    n_contam = 20
    for i in range(n_contam):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [contam_amp, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    n_hold = 250
    for i in range(n_hold):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    n_burst = 50
    for i in range(n_burst):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    for i in range(100):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    return samples


def test_replay_trial_ignores_pre_release_contamination_when_capturing_zero():
    """Root-cause regression test for the still-very-high RMSE-vs-OptiTrack
    bug found 2026-08-07: unlike gyro_bias calibration (already gated on
    genuine raw-signal stillness by the 2026-08-04 fix), the zero-
    orientation capture (q_zero, in _swing_from_quats) still arms on the
    very FIRST gyro sample that crosses _FLEX_CAPTURE_THRESHOLD, with no
    stillness precondition. When a raw log starts already in motion
    (contamination, not a genuine still hold), q_zero gets captured from
    that garbage orientation instead of the true pre-release pose, and
    every angle for the rest of the trial is offset by a large, roughly-
    constant bias. This must not happen: q_zero should wait for a
    confirmed trailing window of calm raw gyro magnitude
    (_recently_calm / st.ever_calm), a dedicated, looser primitive than
    the bias calibration's own _is_stationary_window / calib_was_stable
    (see _ZERO_CAPTURE_GUARD_RAD_S's derivation in imu_calibration_tuner.py
    for why the two checks are deliberately different)."""
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    clean_samples = _solo_hold_then_burst_samples()
    contaminated_samples = _solo_immediate_contamination_then_hold_then_burst_samples()

    t_clean, angle_clean = tuner.replay_trial(clean_samples, params)
    t_contam, angle_contam = tuner.replay_trial(contaminated_samples, params)

    assert len(angle_contam) > 0 and np.isfinite(angle_contam[-1]), (
        "contaminated run must still zero and produce a scoreable angle "
        "series once genuine stillness is reached")
    # Tolerance of 2.0 deg (not sub-degree): the contamination tail still
    # shifts exactly when the trailing window first reads "stable" by a few
    # ticks versus the no-contamination control (same effect noted in
    # test_replay_trial_ignores_handling_window_when_calibrating_bias),
    # which is immaterial next to the ~40 deg error this gate closes.
    assert abs(angle_contam[-1] - angle_clean[-1]) < 2.0, (
        f"contaminated run ({angle_contam[-1]:.2f} deg) should match the "
        f"clean control ({angle_clean[-1]:.2f} deg) -- zero-capture must "
        f"wait for genuine stillness, not fire on pre-release contamination")


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
    accelerometer correction term. A leading 1.1s still hold is required so
    zero-capture's st.ever_calm gate (2026-08-07 fix) arms before burst 1
    -- without it the log would never zero (burst 1 starts "in motion" from
    the gate's point of view, same as the contamination case it guards
    against)."""
    samples = []
    t = 0.0
    dt = 0.01
    for _ in range(110):   # genuine still hold, satisfies st.ever_calm
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": int(t * 1000)})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})
        t += dt
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
    """q_zero is captured on the first gyro sample that fires once genuine
    calm has been confirmed (st.ever_calm / _recently_calm, 2026-08-07
    fix) -- not literally the first sample in the log anymore (that was
    the exact behavior responsible for a large real-world bias when a log
    started already in motion; see
    test_replay_trial_ignores_pre_release_contamination_when_capturing_zero).
    A tilted-but-motionless accel throughout the leading hold still counts
    as "calm" (the gate checks gyro magnitude, not accel at all), so
    gravity_seed=True seeds q far from identity while gravity_seed=False
    leaves it at identity, and with beta>0 (correction active) that
    starting-point difference must still measurably survive to zero-capture
    and change the subsequent trajectory (it would fully converge away, and
    correctly show no difference, if beta were 0 -- see the plan's Section 4
    discussion)."""
    samples = []
    tilt_deg = 60.0
    tilted_accel = [9.81 * math.sin(math.radians(tilt_deg)), 0.0,
                    9.81 * math.cos(math.radians(tilt_deg))]
    t = 0.0
    dt = 0.01
    for _ in range(110):   # genuine still hold (tilted but unchanging accel)
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": tilted_accel, "phone_ts_ms": int(t * 1000)})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})
        t += dt
    for _ in range(80):
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
    """Isolates gate A specifically: the hold ramps smoothly from 140 up to
    180 across the whole pre-release segment (rather than a block overwrite),
    so nothing else trips -- a prior version of this test also produced a
    clip violation at the hold/release boundary, so it couldn't distinguish
    "gate A works" from "gate A is deleted and gate C catches it anyway."""
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    hold_mask = t < 1.0
    n_hold = int(hold_mask.sum())
    angle[hold_mask] = np.linspace(140.0, 180.0, n_hold)
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]
    assert result["penalty"] > 0


def test_score_waveform_clipped_step_fails():
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    mid = len(angle) // 2
    angle[mid] = angle[mid - 1] + 60.0   # impossible single-tick jump
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]


def test_score_waveform_plateau_during_active_swing_fails():
    """Isolates the plateau check specifically: after the frozen run, the
    REST of the series is shifted by a constant so it resumes continuously
    from the frozen value, rather than jumping back to the raw curve. A
    prior version of this test also produced a large clip violation at the
    un-freeze edge, so it couldn't distinguish "the plateau check works"
    from "the plateau check is deleted and the clip check catches it
    anyway." A constant shift doesn't change the remainder's own shape or
    derivatives -- only its offset -- so it introduces no new discontinuity."""
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    release_i = int(1.0 / 0.05)
    freeze_start = release_i + 2
    freeze_len = 10
    freeze_end = freeze_start + freeze_len
    frozen_value = angle[freeze_start]
    angle[freeze_start:freeze_end] = frozen_value
    offset = frozen_value - angle[freeze_end]
    angle[freeze_end:] += offset
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


def test_score_waveform_slow_single_drop_then_lock_still_passes():
    """Same severe-spasticity shape as the test above, but the drop itself
    takes 3.5s instead of 1s -- still a real, physically valid (if unusually
    slow) release, not an artifact. A prior version of the no-extrema window
    fallback used a per-tick derivative threshold to find where the drop
    "settles"; that threshold was itself speed-coupled, so any drop slower
    than roughly 1s/35deg fell through to the same flat-4.0s window this
    fix exists to eliminate, and got rejected with false plateau violations
    on its own resting tail. This test pins the fix against that regression
    directly, at a drop speed the original test could not have caught."""
    t = np.arange(0, 20.0, 0.05)
    angle = np.full_like(t, 180.0)
    release_t = 1.0
    drop_s = 3.5
    for i, ti in enumerate(t):
        if release_t <= ti < release_t + drop_s:
            tau = ti - release_t
            angle[i] = 180.0 - 35.0 * (tau / drop_s)
        elif ti >= release_t + drop_s:
            angle[i] = 145.0
    result = tuner.score_waveform(t, angle)
    assert result["passes"], (
        "a slower single-drop-then-lock trial must still pass, "
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


def test_tune_picks_lowest_penalty_passing_candidate(monkeypatch):
    fake_results = [
        {"params": {"beta": 0.02, "ema_alpha": 0.1, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 5.0, "passes": True},
        {"params": {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 1.0, "passes": True},
        {"params": {"beta": 0.08, "ema_alpha": 0.5, "flex_axis_capture": False, "gravity_seed": False}, "penalty": 0.5, "passes": False},
    ]
    it = iter(fake_results)

    def fake_replay(raw_samples, params):
        return np.array([0.0, 1.0]), np.array([180.0, 170.0])

    def fake_score(t, angle):
        r = next(it)
        return {"passes": r["passes"], "penalty": r["penalty"], "params": None}

    monkeypatch.setattr(tuner, "TUNING_GRID", [r["params"] for r in fake_results])
    monkeypatch.setattr(tuner, "replay_trial", fake_replay)
    monkeypatch.setattr(tuner, "score_waveform", fake_score)

    best = tuner.tune([{"t": 0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0}])
    assert best["passes"] is True
    assert best["penalty"] == 1.0
    assert best["params"]["beta"] == 0.041


def test_tune_falls_back_to_least_bad_when_none_pass(monkeypatch):
    fake_results = [
        {"params": {"beta": 0.02, "ema_alpha": 0.1, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 5.0, "passes": False},
        {"params": {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 2.0, "passes": False},
    ]
    it = iter(fake_results)
    monkeypatch.setattr(tuner, "TUNING_GRID", [r["params"] for r in fake_results])
    monkeypatch.setattr(tuner, "replay_trial",
                        lambda raw, p: (np.array([0.0]), np.array([180.0])))
    # Simpler: rebuild fake_score to just cycle through fake_results' values.
    it2 = iter(fake_results)
    monkeypatch.setattr(tuner, "score_waveform",
                        lambda t, a: {k: v for k, v in next(it2).items() if k in ("passes", "penalty", )} | {"params": None})

    best = tuner.tune([{"t": 0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0}])
    assert best["passes"] is False
    assert best["penalty"] == 2.0


def test_tune_empty_replay_treated_as_worst_case(monkeypatch):
    monkeypatch.setattr(tuner, "TUNING_GRID", [
        {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True}])
    monkeypatch.setattr(tuner, "replay_trial",
                        lambda raw, p: (np.array([]), np.array([])))
    best = tuner.tune([{"t": 0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0}])
    assert best["passes"] is False
    assert best["penalty"] >= 1e6


def test_tune_and_persist_saves_when_improving(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.08, "ema_alpha": 0.1,
                   "flex_axis_capture": False, "gravity_seed": False},
        "penalty": 0.5, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], source_trial="trial_1.csv")
    saved = cfgmod.load_config()
    assert saved["beta"] == 0.08
    assert saved["passes"] is True
    assert saved["source_trial"] == "trial_1.csv"


def test_is_improvement_never_lets_a_failing_candidate_beat_a_passing_current():
    """The no-regression persistence ratchet's single most safety-critical
    branch: if the current persisted config already passes, a failing
    candidate must never be considered an improvement -- no matter how low
    its penalty is. The `passes` gate must dominate `penalty` comparisons
    entirely; a failing candidate isn't just "worse", it's not eligible to
    be compared on penalty at all."""
    current = {"beta": 0.041, "penalty": 5.0, "passes": True}

    candidate_low_penalty = {"beta": 0.02, "penalty": 0.001, "passes": False}
    assert tuner._is_improvement(candidate_low_penalty, current) is False

    candidate_high_penalty = {"beta": 0.15, "penalty": 1e6, "passes": False}
    assert tuner._is_improvement(candidate_high_penalty, current) is False


def test_tune_and_persist_does_not_overwrite_a_better_existing_config(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    cfgmod.save_config({**cfgmod.DEFAULT_CONFIG, "beta": 0.15, "penalty": 0.1, "passes": True})
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.02, "ema_alpha": 0.5,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 3.0, "passes": True,   # worse penalty -> must not overwrite
    })
    tuner.tune_and_persist([{"dummy": True}])
    assert cfgmod.load_config()["beta"] == 0.15


def test_tune_and_persist_force_overwrites_regardless(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    cfgmod.save_config({**cfgmod.DEFAULT_CONFIG, "beta": 0.15, "penalty": 0.1, "passes": True})
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.02, "ema_alpha": 0.5,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 3.0, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], force=True)
    assert cfgmod.load_config()["beta"] == 0.02


def test_ockendon_deg_zero_beta_gives_zero_kappa():
    assert abs(tuner.ockendon_deg(0.0)) < 1e-9


def test_ockendon_deg_matches_formula_for_arbitrary_beta():
    beta = 45.0
    expected = 90.0 + beta - math.degrees(
        math.acos(math.sin(math.radians(beta)) / 1.2))
    assert abs(tuner.ockendon_deg(beta) - expected) < 1e-9


def test_replay_trial_defaults_to_relative_method_when_key_absent():
    """Backward compatibility: existing callers/tests never set "method"."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    expected_final = 180.0 - math.degrees(2.0 * 0.5)
    assert abs(angle[-1] - expected_final) < 1.0


def test_replay_trial_ockendon_flipped_starts_near_180():
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
              "gravity_seed": True, "method": "ockendon_flipped"}
    t, angle = tuner.replay_trial(samples, params)
    pre_release = angle[(t < 0.9) & np.isfinite(angle)]
    assert len(pre_release) > 0
    assert abs(float(np.median(pre_release)) - 180.0) < 1.0


def test_replay_trial_ockendon_unflipped_starts_near_zero():
    """Documents *why* ockendon_flipped is the one likely to pass
    score_waveform's 180-start gate -- unflipped kappa is ~0 at full
    extension, the opposite of Pendulastic's clinical convention."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
              "gravity_seed": True, "method": "ockendon"}
    t, angle = tuner.replay_trial(samples, params)
    pre_release = angle[(t < 0.9) & np.isfinite(angle)]
    assert len(pre_release) > 0
    assert abs(float(np.median(pre_release))) < 1.0


def test_tuning_grid_includes_all_three_methods():
    methods = {p["method"] for p in tuner.TUNING_GRID}
    assert methods == {"relative", "ockendon", "ockendon_flipped"}


def test_tune_and_persist_persists_method_field(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.08, "ema_alpha": 0.1, "flex_axis_capture": False,
                   "gravity_seed": False, "method": "ockendon_flipped"},
        "penalty": 0.5, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], source_trial="trial_1.csv")
    assert cfgmod.load_config()["method"] == "ockendon_flipped"


def test_tune_and_persist_defaults_method_when_candidate_lacks_it(tmp_path, monkeypatch):
    """test_tune_and_persist_saves_when_improving's candidate has no "method"
    key (pre-Task-12 shape) -- must not KeyError, must default to "relative"."""
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.08, "ema_alpha": 0.1,
                   "flex_axis_capture": False, "gravity_seed": False},
        "penalty": 0.5, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], source_trial="trial_1.csv")
    assert cfgmod.load_config()["method"] == "relative"


def test_ockendon_deg_custom_ratio_differs_from_default():
    beta = 45.0
    default = tuner.ockendon_deg(beta)
    custom = tuner.ockendon_deg(beta, ft_ratio=1.5)
    assert abs(custom - default) > 0.5


def test_ockendon_deg_default_ratio_matches_explicit_constant():
    beta = 30.0
    assert tuner.ockendon_deg(beta) == tuner.ockendon_deg(beta, ft_ratio=tuner.OCKENDON_FT_RATIO)


def test_replay_trial_ft_ratio_changes_ockendon_output():
    """Confirms replay_trial actually threads params["ft_ratio"] through to
    ockendon_deg, not just that the function itself accepts the parameter."""
    samples = _solo_hold_then_burst_samples()
    base_params = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
                   "gravity_seed": True, "method": "ockendon"}
    t1, angle1 = tuner.replay_trial(samples, base_params)
    t2, angle2 = tuner.replay_trial(samples, {**base_params, "ft_ratio": 1.5})
    assert abs(angle1[-1] - angle2[-1]) > 0.5


def test_replay_trial_estimates_accel_bias_during_stillness():
    """During the initial stillness hold, if the window is verified
    stationary, accel_bias should be estimated as the mean raw accel minus
    [0, 0, 9.81]. This test confirms the code runs without error and the
    result has finite values."""
    params = {"beta": 0.041, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}

    # Synthetic log with accel bias
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    accel_bias_true = np.array([0.1, -0.05, 0.2])

    n_hold = 100
    for i in range(n_hold):
        raw_accel = np.array([0.0, 0.0, 9.81]) + accel_bias_true
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": list(raw_accel), "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    n_burst = 50
    for i in range(n_burst):
        raw_accel = np.array([0.0, 0.0, 9.81]) + accel_bias_true
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": list(raw_accel), "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    for i in range(100):
        raw_accel = np.array([0.0, 0.0, 9.81]) + accel_bias_true
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": list(raw_accel), "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    t_result, angle_result = tuner.replay_trial(samples, params)
    # Code should run without error and produce finite angle values
    assert len(t_result) > 0, "replay_trial should produce output with accel bias correction"
    assert np.isfinite(angle_result[-1]), "final angle should be finite"


# -- native-rate replay grid ----------------------------------------------

def test_replay_trial_defaults_to_the_live_50ms_display_grid():
    # TICK_S stays the app's poll cadence. Analysis opts out explicitly, so
    # nothing silently changes under the live path.
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 0.3,
              "flex_axis_capture": True, "gravity_seed": True}
    t, _ang = tuner.replay_trial(samples, params)
    assert float(np.median(np.diff(t))) == pytest.approx(tuner.TICK_S, rel=1e-6)


def test_replay_trial_can_emit_on_the_native_100hz_grid():
    # The phone streams accel, gyro and mag at ~100 Hz each; the 20 Hz grid
    # is the app's DISPLAY poll, not a sensor limit. Resampling metrics down
    # to 20 Hz is what pushed compute_pt_params' 0.10 s smoothing window
    # below savgol's floor, so the analysis path asks for the native rate.
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 0.3,
              "flex_axis_capture": True, "gravity_seed": True}
    t, ang = tuner.replay_trial(samples, params, tick_s=0.01)
    assert float(np.median(np.diff(t))) == pytest.approx(0.01, rel=1e-6)
    assert len(ang) == len(t)


def test_ema_alpha_is_rescaled_to_preserve_its_time_constant():
    # An EMA's smoothing depends on alpha AND the interval it steps at.
    # Reusing alpha on a 5x finer grid makes it a 5x LIGHTER filter, passing
    # through the jitter the tuned value was chosen to reject -- a change to
    # the signal disguised as a change to the sampling.
    assert tuner._rescale_ema_alpha(0.3, tick_s=tuner.TICK_S) == pytest.approx(0.3)
    fine = tuner._rescale_ema_alpha(0.3, tick_s=tuner.TICK_S / 5.0)
    assert fine < 0.3
    # Five steps of the rescaled filter must decay exactly as one old step.
    assert (1.0 - fine) ** 5 == pytest.approx(1.0 - 0.3)


def test_ema_alpha_of_one_means_no_smoothing_at_every_rate():
    # alpha == 1.0 is "no smoothing"; rescaling must not turn it into a
    # filter. Several existing tests replay with ema_alpha=1.0 precisely to
    # remove smoothing lag from their assertions.
    for tick in (tuner.TICK_S, 0.01, 0.001):
        assert tuner._rescale_ema_alpha(1.0, tick_s=tick) == 1.0


def test_native_rate_replay_describes_the_same_motion_as_the_display_grid():
    # Behavioural check on the rescale: the same trial replayed on both grids
    # must describe the same motion, not a noisier version of it. Both bounds
    # discriminate -- measured on this fixture, dropping the rescale and
    # reusing alpha on the finer grid gives 10.317 deg peak / 0.367 deg
    # settled against 2.069 / 0.058 with it.
    #
    # The remaining peak is a transient: the fixture slews at 2.0 rad/s
    # between t=1.0 and t=1.5 s, and the two grids necessarily sample the EMA
    # at different instants through it. The settled bound is taken over the
    # last 0.5 s, which is clear of that burst -- a 1.0 s window is NOT, and
    # would be asserting on the slew while claiming to measure the tail.
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 0.3,
              "flex_axis_capture": True, "gravity_seed": True}
    t_slow, a_slow = tuner.replay_trial(samples, params)
    t_fast, a_fast = tuner.replay_trial(samples, params, tick_s=0.01)
    on_slow = np.interp(t_slow, t_fast, a_fast)
    ok = np.isfinite(a_slow) & np.isfinite(on_slow)
    assert ok.sum() > 10
    assert float(np.nanmax(np.abs(a_slow[ok] - on_slow[ok]))) < 2.5
    settled = ok & (t_slow > t_slow[ok][-1] - 0.5)
    assert float(np.nanmax(np.abs(a_slow[settled] - on_slow[settled]))) < 0.15


def test_analysis_grid_is_the_native_sensor_rate():
    # The phone streams each sensor at ~100 Hz. Analysis derives parameters
    # on that grid; TICK_S stays the app's 20 Hz display poll.
    assert tuner.ANALYSIS_TICK_S == 0.01
    assert tuner.ANALYSIS_TICK_S < tuner.TICK_S
