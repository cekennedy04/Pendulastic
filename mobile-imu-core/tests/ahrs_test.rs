//! U1 test scenarios (see the plan's U1 entry). These are known-answer tests
//! against the ported formulas' documented behavior. The cross-check against
//! Python-computed values lives in `pipeline_test.rs`, which runs this
//! module's fusion + calibration over a full raw log and compares the result
//! to what `imu_calibration_tuner.replay_trial` produces for it. What remains
//! outstanding is validation against *real* trial data — a separate offline
//! step once that data exists (KTD3's shadow study).

use mobile_imu_core::ahrs::{gravity_seed, MadgwickAhrs, BETA};
use mobile_imu_core::calibration::{calibrate_accel_bias, calibrate_gyro_bias, ReleaseDetector};
use mobile_imu_core::stillness::{is_stationary_window, recently_calm, SampleBuf};

fn buf_of(samples: &[(f64, [f64; 3])]) -> SampleBuf {
    samples.to_vec()
}

// ---- calibrate_gyro_bias / calibrate_accel_bias --------------------------

#[test]
fn gyro_bias_matches_static_hold_mean() {
    // 6 samples (>= GYRO_BIAS_MIN_SAMPLES=5), constant vector -> mean == input.
    let buf = buf_of(&[
        (0.00, [0.02, -0.01, 0.03]),
        (0.02, [0.02, -0.01, 0.03]),
        (0.04, [0.02, -0.01, 0.03]),
        (0.06, [0.02, -0.01, 0.03]),
        (0.08, [0.02, -0.01, 0.03]),
        (0.10, [0.02, -0.01, 0.03]),
    ]);
    let bias = calibrate_gyro_bias(&buf).expect("enough samples to calibrate");
    assert!((bias[0] - 0.02).abs() < 1e-12);
    assert!((bias[1] - (-0.01)).abs() < 1e-12);
    assert!((bias[2] - 0.03).abs() < 1e-12);
}

#[test]
fn gyro_bias_insufficient_samples_returns_none() {
    let buf = buf_of(&[(0.0, [0.0, 0.0, 0.0]); 3]);
    // Too-short hold buffer must not panic and must signal "leave bias
    // unchanged" via None, per calibrate_gyro_bias's Python doc contract.
    assert!(calibrate_gyro_bias(&buf).is_none());
}

#[test]
fn accel_bias_normalizes_both_ios_and_android_unit_scales() {
    // iOS CoreMotion: magnitude ~1 (g's). A held phone tilted so gravity
    // isn't purely on one axis, plus a small sensor offset.
    let ios_buf = buf_of(&[
        (0.0, [0.10, 0.05, 0.99]),
        (0.02, [0.10, 0.05, 0.99]),
    ]);
    // Android SensorManager: the same tilt, magnitude ~9.81 (m/s²), same
    // relative offset scaled up.
    let android_buf = buf_of(&[
        (0.0, [0.981, 0.4905, 9.7119]),
        (0.02, [0.981, 0.4905, 9.7119]),
    ]);

    let ios_bias = calibrate_accel_bias(&ios_buf).expect("2+ samples");
    let android_bias = calibrate_accel_bias(&android_buf).expect("2+ samples");

    // Bias-corrected accel should land at magnitude g in EACH platform's own
    // unit scale (1.0 for iOS, 9.81 for Android) — proving the g-detection
    // (KTD10's ">3.0" split) picked the right constant for both.
    let ios_corrected = sub3(ios_buf[0].1, ios_bias);
    let android_corrected = sub3(android_buf[0].1, android_bias);
    assert!((norm3(ios_corrected) - 1.0).abs() < 1e-9);
    assert!((norm3(android_corrected) - 9.81).abs() < 1e-9);
}

fn sub3(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}
fn norm3(v: [f64; 3]) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

// ---- stillness --------------------------------------------------------

#[test]
fn stationary_window_true_for_a_genuinely_still_hold() {
    let calm: SampleBuf = (0..60)
        .map(|i| (i as f64 * 0.02, [0.01, -0.01, 0.02]))
        .collect(); // spans 1.18s > 0.95 * GYRO_BIAS_WINDOW_S
    assert!(is_stationary_window(&calm, &calm, 1.18));
}

#[test]
fn stationary_window_false_when_buffer_too_short() {
    let short: SampleBuf = vec![(0.0, [0.0, 0.0, 0.0]), (0.1, [0.0, 0.0, 0.0])];
    assert!(!is_stationary_window(&short, &short, 0.1));
}

#[test]
fn recently_calm_false_during_an_active_burst() {
    let mut buf: SampleBuf = (0..50).map(|i| (i as f64 * 0.02, [0.01, 0.0, 0.0])).collect();
    buf.push((1.0, [2.0, 0.0, 0.0])); // well above ZERO_CAPTURE_GUARD_RAD_S
    assert!(!recently_calm(&buf, 1.0));
}

// ---- ReleaseDetector (KTD9's calm_qualified/pending_departure machine) ---

#[test]
fn release_detector_fires_once_on_a_genuine_release() {
    let mut detector = ReleaseDetector::new();
    let mut hold_buf: SampleBuf = Vec::new();

    // 1.0s of calm samples at 50Hz (spans the full GYRO_BIAS_WINDOW_S), then
    // a burst that crosses FLEX_CAPTURE_THRESHOLD.
    let calm_samples: Vec<[f64; 3]> = (0..50).map(|_| [0.05, 0.0, 0.0]).collect();
    let release_sample = [1.5, 0.0, 0.0]; // >= FLEX_CAPTURE_THRESHOLD (1.0)

    for (i, v) in calm_samples.iter().enumerate() {
        let t = i as f64 * 0.02;
        let fired = detector.on_gyro_sample(*v, &hold_buf, t);
        assert!(!fired, "must not fire during the calm hold");
        hold_buf.push((t, *v));
    }

    let t = calm_samples.len() as f64 * 0.02;
    let fired = detector.on_gyro_sample(release_sample, &hold_buf, t);
    assert!(fired, "must fire on the qualifying release burst");
    assert!(!detector.is_armed());

    // A second burst after release must never fire again.
    hold_buf.push((t, release_sample));
    let fired_again = detector.on_gyro_sample(release_sample, &hold_buf, t + 0.02);
    assert!(!fired_again, "release must latch -- a second burst cannot re-fire");
}

#[test]
fn release_detector_treats_a_settled_excursion_as_handling_not_release() {
    let mut detector = ReleaseDetector::new();
    let mut hold_buf: SampleBuf = Vec::new();

    let calm = [0.05, 0.0, 0.0];
    let handling_burst = [0.5, 0.0, 0.0]; // above ZERO_CAPTURE_GUARD_RAD_S (0.3)...
                                          // ...but below FLEX_CAPTURE_THRESHOLD (1.0)

    for i in 0..50 {
        let t = i as f64 * 0.02;
        detector.on_gyro_sample(calm, &hold_buf, t);
        hold_buf.push((t, calm));
    }
    // Excursion begins (arms pending_departure) then settles back to calm
    // without ever reaching FLEX_CAPTURE_THRESHOLD.
    let t1 = 50.0 * 0.02;
    assert!(!detector.on_gyro_sample(handling_burst, &hold_buf, t1));
    hold_buf.push((t1, handling_burst));
    let t2 = t1 + 0.02;
    assert!(!detector.on_gyro_sample(calm, &hold_buf, t2));
    // Eligibility was revoked — detector is still armed, waiting for a fresh
    // full calm window before trusting the next excursion.
    assert!(detector.is_armed());
}

// ---- MadgwickAhrs -------------------------------------------------------

#[test]
fn ahrs_produces_continuous_trajectory_with_no_nan_or_discontinuity() {
    let mut ahrs = MadgwickAhrs::new(BETA);
    ahrs.q = gravity_seed([0.0, 0.0, 1.0]);

    let dt = 0.01;
    for i in 0..500 {
        // A gentle, low-rate synthetic swing plus a constant-direction
        // gravity reading (magnetometer intentionally omitted, per KTD10).
        let gyro = [0.05 * (i as f64 * 0.05).sin(), 0.0, 0.0];
        let accel = [0.0, 0.0, 1.0];
        ahrs.update(gyro, accel, None, dt);

        assert!(ahrs.q.iter().all(|c| c.is_finite()), "quaternion went non-finite at step {i}");
        let n = (ahrs.q[0].powi(2) + ahrs.q[1].powi(2) + ahrs.q[2].powi(2) + ahrs.q[3].powi(2)).sqrt();
        assert!((n - 1.0).abs() < 1e-6, "quaternion lost unit norm at step {i}: {n}");
    }
}

#[test]
fn ahrs_magnetometer_path_is_reachable_but_opt_in() {
    // Confirms the mag-correction branch is live code (not dead/removed),
    // while KTD10's contract is that real callers pass None. Same starting
    // state, same accel/gyro, different mag argument -> different quaternion
    // proves the branch executes when a caller opts in.
    //
    // The mag vector is not arbitrary. Madgwick derives the earth-frame
    // reference `b` from the measurement itself, flattened into the XZ plane,
    // so a mag reading that *already* lies in the current frame's XZ plane
    // (e.g. [1,0,0] or [0,0,1] at identity) has a residual of exactly zero by
    // construction: the branch executes and correctly contributes nothing,
    // which leaves the assertion below unable to tell a live branch from a
    // dead one. [0,1,0] has a nonzero earth-frame Y component, which is
    // precisely what the magnetic correction exists to null out.
    let mut without_mag = MadgwickAhrs::new(BETA);
    let mut with_mag = MadgwickAhrs::new(BETA);
    without_mag.q = [1.0, 0.0, 0.0, 0.0];
    with_mag.q = [1.0, 0.0, 0.0, 0.0];

    let gyro = [0.0, 0.0, 0.0]; // no rotation -> correction step dominates
    let accel = [0.0, 0.0, 1.0];
    let dt = 0.01;
    without_mag.update(gyro, accel, None, dt);
    with_mag.update(gyro, accel, Some([0.0, 1.0, 0.0]), dt);

    assert_ne!(without_mag.q, with_mag.q);

    // The accel residual is zero here (identity IS the equilibrium for
    // accel=[0,0,1]), so the entire difference is the magnetic term, and it
    // must be a pure yaw (Z) correction of one normalized gradient-descent
    // step: beta * dt. Asserting the shape and magnitude — not merely "these
    // differ" — is what keeps this test honest if the mag Jacobian is ever
    // mis-transcribed into something that still happens to move q.
    assert_eq!(
        without_mag.q,
        [1.0, 0.0, 0.0, 0.0],
        "no-mag arm should sit at equilibrium"
    );
    assert!(with_mag.q[1].abs() < 1e-12, "mag correction leaked into x (roll)");
    assert!(with_mag.q[2].abs() < 1e-12, "mag correction leaked into y (pitch)");
    assert!(
        (with_mag.q[3].abs() - BETA * dt).abs() < 1e-9,
        "expected a single beta*dt yaw step, got {}",
        with_mag.q[3]
    );
}
