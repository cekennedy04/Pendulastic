//! Orchestrator tests. Behaviour equivalence with the pre-extraction harness
//! is covered by pipeline_test.rs, which scores a real log; these cover the
//! error paths that log cannot reach.

use mobile_imu_core::calibration::FLEX_CAPTURE_THRESHOLD;
use mobile_imu_core::goniometry::{ockendon_deg, OCKENDON_FT_RATIO};
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
    // The default must stay `Relative` -- that is the persisted live default,
    // and silently changing it would re-scale every angle this app reports.
    assert_eq!(ReplayConfig::default().method, Method::Relative);

    // Beyond that, the field has to actually reach the angle mapping. An
    // earlier form of this test only asserted `cfg.method == Method::Ockendon`
    // immediately after setting it, which is a tautology about struct literals
    // and would still have passed if `replay` ignored `cfg.method` entirely.
    // `ema_alpha = 1.0` makes `ema_smooth` an identity pass (`1*v + 0*prev`),
    // leaving `tick_resample`'s zero-order hold -- which only ever SELECTS a
    // raw sample, never averages two -- between the mapping and the output.
    // That is what makes the exact per-tick check below legitimate: the
    // default alpha of 0.3 mixes ticks, and since `ockendon_deg` is
    // non-linear, smoothing then mapping and mapping then smoothing disagree
    // by ~5e-8. Comparing under a loosened tolerance to paper over that would
    // be checking a formula the code does not actually compute.
    let base = ReplayConfig { ema_alpha: 1.0, ..ReplayConfig::default() };
    let log = ramp_release_log(200);
    let relative = replay(&log, &base).expect("the ramp must score");
    let ockendon = replay(&log, &ReplayConfig { method: Method::Ockendon, ..base })
        .expect("the ramp must score under Ockendon too");

    // Method is applied AFTER release detection, so it must not move the
    // release: same instant, same zero pose, different angle mapping.
    assert_eq!(relative.release_idx, ockendon.release_idx);
    assert_eq!(relative.release_quat, ockendon.release_quat);
    assert_eq!(relative.angle_deg.len(), ockendon.angle_deg.len());

    // Index 0 is NaN by TrialResult's contract; compare the rest, and require
    // at least one finite sample so an all-NaN series cannot pass vacuously.
    let mut compared = 0usize;
    for (i, (&r, &o)) in relative
        .angle_deg
        .iter()
        .zip(ockendon.angle_deg.iter())
        .enumerate()
        .skip(1)
    {
        if !r.is_finite() || !o.is_finite() {
            continue;
        }
        compared += 1;
        // `Relative` is exactly `180 - swing`, so the swing that produced this
        // tick is recoverable from it -- which lets the Ockendon tick be
        // checked against the reference formula rather than merely asserted
        // to be "different".
        let swing = 180.0 - r;
        let expected = ockendon_deg(swing, OCKENDON_FT_RATIO);
        assert!(
            (o - expected).abs() < 1e-9,
            "tick {i}: Ockendon angle {o} does not match ockendon_deg({swing}) = {expected}"
        );
    }
    assert!(compared > 0, "no finite ticks to compare; the fixture scored nothing usable");

    // And the two mappings must genuinely disagree on this log -- otherwise
    // the check above would hold even if `Method` were dead.
    let differs = relative
        .angle_deg
        .iter()
        .zip(ockendon.angle_deg.iter())
        .skip(1)
        .any(|(&r, &o)| r.is_finite() && o.is_finite() && (r - o).abs() > 1e-6);
    assert!(differs, "Relative and Ockendon produced identical angles; Method is not reaching replay()");
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
