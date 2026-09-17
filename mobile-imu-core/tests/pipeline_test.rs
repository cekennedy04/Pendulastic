//! U2's final test scenario: the whole chain, not one stage of it.
//!
//! `ahrs_test`, `resample_test`, `goniometry_test`, `signal_test` and
//! `scoring_test` each pin a single stage against the Python reference's
//! output for that stage. All of them can pass while the pipeline is still
//! wrong, because none of them exercises a *boundary* — which units cross it,
//! what order samples arrive in, or when the zero pose is captured relative to
//! the fusion step that consumes it. This file feeds a raw sensor log in at
//! one end and compares a scored trial at the other, against what
//! `imu_calibration_tuner.replay_trial` + `score_waveform` produce for the
//! same log.
//!
//! The raw-log walk itself — bias calibration, release capture, per-sample
//! fusion — lives in `mobile_imu_core::replay`, so WASM (and any other
//! caller) can drive it directly. This file only rebuilds the fixture's raw
//! log and hands it to `replay::replay`.
//!
//! **Not covered:** `flex_axis_capture=True`, the axis-projection variant of
//! the swing-angle computation. It has no Rust port yet (also U3's
//! orchestrator), so the fixture pins the `False` branch only.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::ahrs::Vec3;
use mobile_imu_core::replay::{replay, Method, RawSample, ReplayConfig, Sensor, TrialResult};
use mobile_imu_core::scoring::{compute_pt_params, score_waveform, SpasticityType};

// ---- the raw log, rebuilt from the fixture ------------------------------

fn xyz(flat: &[f64], i: usize) -> Vec3 {
    [flat[3 * i], flat[3 * i + 1], flat[3 * i + 2]]
}

/// Rebuild the chronological sample stream `gen_fixtures.py` handed to
/// `replay_trial`. The interleaving is part of the fixture's contract and is
/// documented identically on both sides: per step, accel first (the gyro
/// branch reads the stored accel, so it must already be there), then a mag
/// sample on every `E2E_MAG_STRIDE`-th step, then gyro.
fn raw_log() -> Vec<RawSample> {
    let n = golden::E2E_RAW_T.len();
    assert_eq!(
        golden::E2E_RAW_ACCEL.len(),
        3 * n,
        "accel/step-count mismatch"
    );
    assert_eq!(
        golden::E2E_RAW_GYRO.len(),
        3 * n,
        "gyro/step-count mismatch"
    );

    let mut out = Vec::with_capacity(2 * n + n / golden::E2E_MAG_STRIDE + 1);
    let mut mag_i = 0usize;
    for i in 0..n {
        let (t, ts_ms) = (golden::E2E_RAW_T[i], golden::E2E_RAW_TS_MS[i]);
        out.push(RawSample {
            t,
            ts_ms,
            sensor: Sensor::Accel,
            v: xyz(golden::E2E_RAW_ACCEL, i),
        });
        if i % golden::E2E_MAG_STRIDE == 0 {
            out.push(RawSample {
                t,
                ts_ms,
                sensor: Sensor::Mag,
                v: xyz(golden::E2E_RAW_MAG, mag_i),
            });
            mag_i += 1;
        }
        out.push(RawSample {
            t,
            ts_ms,
            sensor: Sensor::Gyro,
            v: xyz(golden::E2E_RAW_GYRO, i),
        });
    }
    assert_eq!(
        3 * mag_i,
        golden::E2E_RAW_MAG.len(),
        "mag stride disagrees with the fixture"
    );
    out
}

// ---- the pipeline under test --------------------------------------------

/// Walk the fixture's raw log through `replay::replay` with the golden run's
/// config (beta, EMA alpha), varying only method/use_mag per test.
fn run(method: Method, use_mag: bool) -> TrialResult {
    let cfg = ReplayConfig {
        beta: golden::E2E_BETA,
        ema_alpha: golden::E2E_EMA_ALPHA,
        method,
        use_mag,
        release_override: None,
    };
    replay(&raw_log(), &cfg).expect("release must be detected")
}

// ---- assertions ----------------------------------------------------------

/// Compares NaN-bearing series positionally: a NaN where the reference has a
/// number (or the reverse) is a failure, not a skipped element. Tick 0 is NaN
/// by contract, and silently tolerating NaN would let a pipeline that emits
/// nothing at all pass.
fn assert_series_close(got: &[f64], want: &[f64], tol: f64, what: &str) {
    assert_eq!(
        got.len(),
        want.len(),
        "{what}: length {} != {}",
        got.len(),
        want.len()
    );
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        if w.is_nan() || g.is_nan() {
            assert_eq!(g.is_nan(), w.is_nan(), "{what}[{i}]: got {g}, want {w}");
            continue;
        }
        assert!(
            (g - w).abs() <= tol,
            "{what}[{i}]: got {g}, want {w} (delta {:.3e})",
            (g - w).abs()
        );
    }
}

fn close(got: f64, want: f64, tol: f64, what: &str) {
    assert!(
        (got - want).abs() <= tol,
        "{what}: got {got}, want {want} (delta {:.3e})",
        (got - want).abs()
    );
}

/// The tolerance is not a fudge factor. Both sides run the identical sequence
/// of f64 operations, and measured agreement on this fixture is actually
/// below 1e-13 deg across all ~180 ticks. The headroom is for the platforms
/// this crate is going to be cross-compiled onto (U3): a libm whose acos or
/// sqrt differs by an ULP would otherwise turn a correct port into a red
/// test. It is still tight enough to catch real defects — dropping the
/// gyro-bias correction, for one, moves the series by ~4e-2 deg.
const ANGLE_TOL_DEG: f64 = 1e-6;

#[test]
fn full_pipeline_reproduces_the_python_replay() {
    let r = run(Method::Relative, false);
    assert_series_close(&r.t, golden::TRIAL_E2E_T, 1e-12, "tick times");
    assert_series_close(&r.angle_deg, golden::TRIAL_E2E_ANG, ANGLE_TOL_DEG, "replayed angle");
}

#[test]
fn full_pipeline_reproduces_the_python_score() {
    let r = run(Method::Relative, false);
    let sw = score_waveform(&r.t, &r.angle_deg);

    assert_eq!(
        sw.passes,
        golden::TRIAL_E2E_SW_PASSES,
        "score_waveform verdict"
    );
    close(sw.penalty, golden::TRIAL_E2E_SW_PENALTY, 1e-6, "penalty");

    let p = sw.params.expect("a passing trial must carry params");
    close(p.r2n, golden::TRIAL_E2E_R2N, 1e-6, "R2n");
    close(p.n, golden::TRIAL_E2E_N, 1e-9, "N");
    close(p.f, golden::TRIAL_E2E_F, 1e-6, "f");
    close(
        p.area_ratio,
        golden::TRIAL_E2E_AREA_RATIO,
        1e-6,
        "area_ratio",
    );
    close(p.a0_deg, golden::TRIAL_E2E_A0_DEG, 1e-5, "A0_deg");
    close(p.a1_deg, golden::TRIAL_E2E_A1_DEG, 1e-5, "A1_deg");
    close(
        p.neutral_deg,
        golden::TRIAL_E2E_NEUTRAL_DEG,
        1e-5,
        "neutral_deg",
    );
    // Read from the golden rather than pinned to a literal. This assertion
    // hardcoded `Extension`, which was what the fixture produced back when it
    // was replayed on the 20 Hz display grid; on the 100 Hz analysis grid the
    // reference reports `balanced` (P+ 13.87 / P- 11.84 = 1.172, under the
    // 1.25 threshold) and the literal silently became a stale expectation.
    // Every numeric assertion above passes at 1e-6..1e-9, so the two engines
    // agree -- only this hand-written constant had drifted.
    assert_eq!(
        p.spasticity_type,
        spasticity_from_golden(golden::TRIAL_E2E_SPASTICITY_TYPE),
        "spasticity_type"
    );
    assert_eq!(
        p.quality_warn,
        golden::TRIAL_E2E_QUALITY_WARN,
        "quality_warn"
    );
}

#[test]
fn full_pipeline_recovers_the_motion_it_was_given() {
    // The fixture's own ground truth, which no single-stage test can check:
    // the log was forward-simulated from a known 1.0 Hz swing settling into
    // E2E_TRUE_A0_DEG of flexion. Matching Python exactly while both are
    // wrong is a real failure mode for a port validated only against its
    // reference, and this is what rules it out.
    let r = run(Method::Relative, false);
    let p = compute_pt_params(&r.t, &r.angle_deg, None, false).expect("trial must be scorable");

    close(p.f, 1.0, 0.05, "recovered swing frequency (Hz)");
    // Neutral is where the swing settles: 180 deg minus the true flexion.
    close(
        p.neutral_deg,
        180.0 - golden::E2E_TRUE_A0_DEG,
        1.5,
        "recovered resting angle",
    );
    // A0 comes back at ~44.8 of the 45 deg input -- about 0.4% attenuation from
    // the EMA and the fusion (Python reference and this port agree to 1e-6 on
    // the same replayed series).
    //
    // It read ~43.2 (4%) until the abs()-fold fix (2026-09-17). Most of that
    // gap was the fold, not the filter: the swing angle was reported as
    // |rotation from the zero pose|, which mirrored the pre-release hold (the
    // leg held ABOVE the mid-drop release capture) from the correct hyper-
    // extension side down onto the flexion side, dragging the detrend baseline
    // and shaving ~1.6 deg off the recovered first swing. Signing the rotation
    // (replay::swing_deg) puts the hold back where it belongs and the amplitude
    // returns almost whole. An earlier ~40.2 (10.7%) era was a release detector
    // that fired ~90 ms into the drop (fixed 2026-09-14).
    const A0_FILTERED_DEG: f64 = 44.83;
    close(p.a0_deg, A0_FILTERED_DEG, 1.0, "recovered first-swing amplitude");

    // The attenuation itself, asserted as a system invariant rather than left
    // as an unexplained gap. If a future change to the fusion or the EMA moves
    // the pipeline's gain, this is what says so.
    let attenuation = p.a0_deg / golden::E2E_TRUE_A0_DEG;
    assert!(
        (0.98..=1.00).contains(&attenuation),
        "pipeline amplitude gain {attenuation:.4} outside the documented          0.98-1.00 band (A0 {} deg from a {} deg input)",
        p.a0_deg,
        golden::E2E_TRUE_A0_DEG
    );
}

#[test]
fn ockendon_method_reproduces_the_python_replay() {
    // The only path that runs ockendon_deg inside the pipeline rather than as
    // a standalone function, so it is the only place a wrong argument (e.g.
    // feeding it 180-swing instead of swing) would show up.
    let r = run(Method::Ockendon, false);
    assert_series_close(&r.angle_deg, golden::E2E_OCK_ANG, ANGLE_TOL_DEG, "ockendon angle");
}

#[test]
fn the_magnetometer_stream_is_present_and_deliberately_unused() {
    // Two halves, and both are needed. That the no-mag run matches the golden
    // (asserted above) shows mag is excluded; on its own that is also what a
    // log with no mag samples in it would show. Feeding the same stream in
    // and getting a different answer proves the samples are real and carry
    // signal — so KTD10's exclusion is an active choice, not a fixture that
    // happens to have nothing to exclude.
    let without = run(Method::Relative, false).angle_deg;
    let with = run(Method::Relative, true).angle_deg;

    let diff = without
        .iter()
        .zip(&with)
        .filter(|(a, b)| a.is_finite() && b.is_finite())
        .map(|(a, b)| (a - b).abs())
        .fold(0.0_f64, f64::max);
    assert!(
        diff > ANGLE_TOL_DEG,
        "mag stream changed nothing (max delta {diff:.3e}) - the fixture's mag samples carry no signal"
    );
}


/// The reference names these in lower case (`pendulastic_pt_score`'s own
/// strings); `params_json` parses the capitalised wire form. The golden
/// carries the reference's spelling, so the test maps it here rather than
/// duplicating a literal that can drift.
fn spasticity_from_golden(s: &str) -> SpasticityType {
    match s {
        "extension" => SpasticityType::Extension,
        "flexion" => SpasticityType::Flexion,
        "balanced" => SpasticityType::Balanced,
        other => panic!("golden carries an unknown spasticity_type: {other:?}"),
    }
}
