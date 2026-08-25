//! Orchestrator tests. Behaviour equivalence with the pre-extraction harness
//! is covered by pipeline_test.rs, which scores a real log; these cover the
//! error paths that log cannot reach.

use mobile_imu_core::calibration::FLEX_CAPTURE_THRESHOLD;
use mobile_imu_core::replay::{replay, Method, RawSample, ReplayConfig, Sensor, TrialError};
use mobile_imu_core::stillness::ZERO_CAPTURE_GUARD_RAD_S;

/// A log that never leaves the calm band: the release detector can never fire.
fn calm_log(n: usize) -> Vec<RawSample> {
    let mut out = Vec::new();
    for i in 0..n {
        let t = i as f64 / 60.0;
        let ts_ms = (t * 1000.0).round() as i64;
        out.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: [0.0, 0.0, 9.81] });
        out.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: [0.01, 0.0, 0.0] });
    }
    out
}

/// A log that genuinely earns an auto-detected release: `CALM_STEPS` steps
/// well inside the calm band (long enough for `recently_calm`'s trailing
/// window to qualify), then a monotonic gyro ramp starting right at
/// `ZERO_CAPTURE_GUARD_RAD_S` (so `pending_departure` latches on the very
/// first ramp sample) and climbing past `FLEX_CAPTURE_THRESHOLD` a few steps
/// later. Unlike `calm_log`, `ReleaseDetector::on_gyro_sample` fires on its
/// own partway through this log — this is what lets a test tell the
/// override-authoritative fix apart from the brief's original `||` form,
/// which would let this exact auto-fire win regardless of any override.
fn ramp_release_log(n: usize) -> Vec<RawSample> {
    const CALM_STEPS: usize = 150;
    let mut out = Vec::new();
    for i in 0..n {
        let t = i as f64 / 60.0;
        let ts_ms = (t * 1000.0).round() as i64;
        out.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: [0.0, 0.0, 9.81] });
        let omega_x = if i < CALM_STEPS {
            0.01
        } else {
            ZERO_CAPTURE_GUARD_RAD_S + 0.15 * (i - CALM_STEPS) as f64
        };
        out.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: [omega_x, 0.0, 0.0] });
    }
    // Sanity check on the fixture itself (not on replay()): the ramp must
    // actually clear the capture threshold before the log ends, or the
    // "auto-detection fires first" premise this test depends on is false.
    let ramp_steps = n - CALM_STEPS;
    let peak = ZERO_CAPTURE_GUARD_RAD_S + 0.15 * (ramp_steps.saturating_sub(1)) as f64;
    debug_assert!(
        peak >= FLEX_CAPTURE_THRESHOLD,
        "ramp_release_log(n={n}) never reaches FLEX_CAPTURE_THRESHOLD; grow n"
    );
    out
}

#[test]
fn a_log_with_no_release_reports_release_never_detected() {
    let cfg = ReplayConfig::default();
    let err = replay(&calm_log(600), &cfg).unwrap_err();
    assert_eq!(err, TrialError::ReleaseNeverDetected);
}

#[test]
fn a_log_too_short_to_score_is_rejected_before_release_detection() {
    let cfg = ReplayConfig::default();
    let err = replay(&calm_log(3), &cfg).unwrap_err();
    assert_eq!(err, TrialError::InsufficientSamples);
}

#[test]
fn the_method_selects_between_relative_and_ockendon() {
    // Both must be reachable through the public config; pipeline_test pins
    // their numeric output against Python.
    let cfg = ReplayConfig { method: Method::Ockendon, ..ReplayConfig::default() };
    assert_eq!(cfg.method, Method::Ockendon);
    assert_eq!(ReplayConfig::default().method, Method::Relative);
}

#[test]
fn an_explicit_override_wins_even_when_auto_detection_fires_earlier() {
    let log = ramp_release_log(200);

    // First, let auto-detection do its thing: this pins the raw-sample
    // index the detector genuinely fires on, on its own, for this log.
    let auto = replay(&log, &ReplayConfig::default()).expect("auto-detection must fire on this ramp");

    // Override to a LATER gyro sample than the auto-fire. The log
    // interleaves accel then gyro per step, so the next raw index after any
    // gyro sample's index is that step's accel sample, and the one after
    // that is the next step's gyro sample — landing back on `Sensor::Gyro`,
    // which is required for the override's capture branch to ever trigger.
    let override_idx = auto.release_idx + 2;
    assert!(
        override_idx < log.len(),
        "fixture too short to place a later override index; grow ramp_release_log's n"
    );
    assert_eq!(
        log[override_idx].sensor,
        Sensor::Gyro,
        "override index must land on a Gyro record or replay() can never capture on it"
    );

    let cfg = ReplayConfig { release_override: Some(override_idx), ..ReplayConfig::default() };
    let overridden = replay(&log, &cfg).expect("an overridden release must still be captured");

    // The override must win: this is exactly the case the brief's `||` form
    // got backwards (auto-detection, firing earlier, would have won).
    assert_eq!(
        overridden.release_idx, override_idx,
        "release_override must be authoritative over an earlier auto-detected fire"
    );
    // And it must be a REAL behaviour difference, not a no-op: the two runs
    // integrated a different number of samples before zeroing, so their
    // snapshotted orientations must differ.
    assert_ne!(
        overridden.release_quat, auto.release_quat,
        "auto-fire and the later override produced the same release_quat - \
         this test isn't actually distinguishing the two capture points"
    );
}
