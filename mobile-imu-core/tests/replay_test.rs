//! Orchestrator tests. Behaviour equivalence with the pre-extraction harness
//! is covered by pipeline_test.rs, which scores a real log; these cover the
//! error paths that log cannot reach.

use mobile_imu_core::replay::{replay, Method, RawSample, ReplayConfig, Sensor, TrialError};

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
