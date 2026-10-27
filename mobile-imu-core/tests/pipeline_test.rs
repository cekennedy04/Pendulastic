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

fn spasticity_str(t: SpasticityType) -> &'static str {
    match t {
        SpasticityType::Extension => "extension",
        SpasticityType::Flexion => "flexion",
        SpasticityType::Balanced => "balanced",
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
/// of f64 operations, and through the SWING measured agreement on this
/// fixture is about 1e-12 deg. The headroom is for the platforms this crate
/// is going to be cross-compiled onto (U3): a libm whose acos or sqrt differs
/// by an ULP would otherwise turn a correct port into a red test. It is still
/// tight enough to catch real defects — dropping the gyro-bias correction, for
/// one, moves the series by ~4e-2 deg, and a bias error integrates from the
/// first tick, so it shows up inside the swing where this tolerance applies.
///
/// CORRECTED 2026-10-27. This comment used to claim agreement "below 1e-13
/// deg across all ~180 ticks". That is true only through roughly the first
/// 60% of them. The settled tail diverges exponentially: a one-ULP change in
/// the FIRST accelerometer sample, which `_gravity_seed` builds the initial
/// quaternion from, grows about 15% per tick, passes this 1e-6 at t ≈ 7.25 s
/// and reaches ~1.3e-2 deg by the last tick. The same perturbation applied to
/// any later accel sample, or any gyro sample, changes the output by exactly
/// zero, so it is the initial condition being amplified rather than a general
/// fragility.
///
/// So applying 1e-6 to the whole series was passing by luck: it holds for the
/// committed fixture because that fixture was generated where the arithmetic
/// happened to match, and regenerating golden.rs in another container turns
/// this test red on the tail alone. The split below asserts each region at
/// what is actually reproducible there.
///
/// The instability itself is a defect and is NOT what this split papers over:
/// the limb is at rest through the diverging region, so the replay's state
/// runs away while its input is quiet. tests/test_replay_reproducibility.py
/// characterises it and carries the open xfails.
const ANGLE_TOL_DEG: f64 = 1e-6;

/// Tail tolerance. Above the ~1.3e-2 deg a single ULP in the initial
/// condition reaches by the last tick, and deliberately not far above it: a
/// dropped gyro-bias correction moves the series ~4e-2 deg, so leaving this
/// loose would start masking real defects. The swing assertion is what
/// actually guards against those, since a bias error integrates from tick one.
const TAIL_TOL_DEG: f64 = 2.5e-2;

/// The fraction of ticks that are still swinging. Past this the limb has
/// settled and the series is not reproducible across implementations.
const SWING_FRACTION: f64 = 0.6;

/// Tolerances for quantities derived from `neutral_deg`, which is the median of
/// the settled tail and therefore inherits the tail's irreproducibility.
///
/// Each is set from MEASUREMENT, not from the one gap that happened to show up
/// here. tests/test_replay_reproducibility.py perturbs the initial condition
/// by a single ULP and reports how far each parameter moves in the reference
/// alone; these are those figures with roughly a 4x margin:
///
///   neutral_deg   1.8e-2      A0_deg  1.3e-2      A1_deg  1.3e-3
///   area_ratio    5.5e-4      R2n     2.7e-4      omega_max_n 1.3e-3
///   f             0           N       0
///
/// Setting them from the observed Rust/Python gap instead would have left R2n
/// at 1e-4 while one ULP of libm difference moves it 2.7e-4 -- passing here
/// and failing on the next machine, which is the failure this file already
/// made once.
///
/// These are secondary checks on the derivation. The primary guard is the
/// swing assertion at ANGLE_TOL_DEG: anything that shifts the series, such as
/// a dropped gyro-bias correction, moves it there and is caught before it
/// reaches a parameter.
const TOL_NEUTRAL_DEG: f64 = 5e-2;
const TOL_A_DEG: f64 = 5e-2;
const TOL_A1_DEG: f64 = 5e-3;
const TOL_RATIO: f64 = 2e-3;
const TOL_OMEGA_N: f64 = 5e-3;

#[test]
fn full_pipeline_reproduces_the_python_replay() {
    let r = run(Method::Relative, false);
    assert_series_close(&r.t, golden::TRIAL_E2E_T, 1e-12, "tick times");

    let n = r.angle_deg.len().min(golden::TRIAL_E2E_ANG.len());
    let split = (SWING_FRACTION * n as f64) as usize;
    assert_series_close(
        &r.angle_deg[..split],
        &golden::TRIAL_E2E_ANG[..split],
        ANGLE_TOL_DEG,
        "replayed angle (swing)",
    );
    assert_series_close(
        &r.angle_deg[split..n],
        &golden::TRIAL_E2E_ANG[split..n],
        TAIL_TOL_DEG,
        "replayed angle (settled tail)",
    );
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
    // r2n is A1/(1.6*A0) and A0 is measured against neutral_deg, which is the
    // MEDIAN OF THE SETTLED TAIL -- the one region that does not reproduce
    // across implementations (tests/test_replay_reproducibility.py). So this
    // inherits the tail at ~1.3e-5, measured, and 1e-6 here was asserting a
    // precision the pipeline does not have rather than catching anything.
    // TAIL_DERIVED_TOL_DEG is an order above what was measured and still far
    // below any defect worth catching in a ratio of order 1.
    close(p.r2n, golden::TRIAL_E2E_R2N, TOL_RATIO, "R2n");
    close(p.n, golden::TRIAL_E2E_N, 1e-9, "N");
    close(p.f, golden::TRIAL_E2E_F, 1e-6, "f");
    close(
        p.area_ratio,
        golden::TRIAL_E2E_AREA_RATIO,
        TOL_RATIO,
        "area_ratio",
    );
    // omega_max_n and omega_min_n were not asserted here at all, which left
    // two of the six scored parameters unchecked in the port. Both are
    // peak velocity over A0, so they inherit the tail through A0.
    close(
        p.omega_max_n,
        golden::TRIAL_E2E_OMEGA_MAX_N,
        TOL_OMEGA_N,
        "omega_max_n",
    );
    close(
        p.omega_min_n,
        golden::TRIAL_E2E_OMEGA_MIN_N,
        TOL_OMEGA_N,
        "omega_min_n",
    );
    close(p.a0_deg, golden::TRIAL_E2E_A0_DEG, TOL_A_DEG, "A0_deg");
    close(p.a1_deg, golden::TRIAL_E2E_A1_DEG, TOL_A1_DEG, "A1_deg");
    close(
        p.neutral_deg,
        golden::TRIAL_E2E_NEUTRAL_DEG,
        TOL_NEUTRAL_DEG,
        "neutral_deg",
    );
    // Read from the golden rather than hardcoded. This was the one literal in
    // an otherwise golden-driven test, and it went stale the moment the
    // symmetry integral moved to the swing centre: a symmetric decaying
    // oscillation is BALANCED, and the previous "extension" verdict came from
    // the baseline asymmetry the centred frame removes.
    assert_eq!(
        spasticity_str(p.spasticity_type),
        golden::TRIAL_E2E_SPASTICITY_TYPE,
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
    // A0 is checked in two parts, because a single bound against the true
    // amplitude conflates two independent error sources and hid a bug in one
    // of them. Fusion and EMA cost a little peak before scoring ever runs, so
    // the angle series handed to compute_pt_params only CONTAINS a held
    // amplitude of ~43.15 deg against the motion's true 45. A single
    // `|A0 - 45| < 2.0` therefore demanded A0 >= 43.0 — within 0.15 deg of
    // what the series can physically deliver — and the previous scoring
    // passed it by reporting 43.73, i.e. ABOVE that ceiling, on savgol
    // boundary overshoot rather than on accuracy.
    //
    // Part 1: what the SCORER does with the series it was given. Measured at
    // 0.85 deg below the held amplitude.
    let held_amplitude = p.pre_release_deg - p.neutral_deg;
    close(
        p.a0_deg,
        held_amplitude,
        1.0,
        "A0 vs the held amplitude the series actually contains",
    );

    // Part 2: what the FUSION chain costs before scoring sees it. This is the
    // assertion that would catch the fusion losing peak, which part 1 cannot
    // see because part 1 measures against the degraded series itself.
    close(
        held_amplitude,
        golden::E2E_TRUE_A0_DEG,
        2.5,
        "held amplitude surviving fusion + EMA, vs the true motion",
    );
}

#[test]
fn ockendon_method_reproduces_the_python_replay() {
    // The only path that runs ockendon_deg inside the pipeline rather than as
    // a standalone function, so it is the only place a wrong argument (e.g.
    // feeding it 180-swing instead of swing) would show up.
    let r = run(Method::Ockendon, false);
    // Split for the same reason as the relative replay above: the settled tail
    // is not reproducible across implementations, and ockendon_deg's own
    // nonlinearity carries the tail divergence through at a similar size
    // (measured: 4.4e-6 deg at tick 141 of ~180, where the swing region holds
    // to ANGLE_TOL_DEG).
    let n = r.angle_deg.len().min(golden::E2E_OCK_ANG.len());
    let split = (SWING_FRACTION * n as f64) as usize;
    assert_series_close(
        &r.angle_deg[..split],
        &golden::E2E_OCK_ANG[..split],
        ANGLE_TOL_DEG,
        "ockendon angle (swing)",
    );
    assert_series_close(
        &r.angle_deg[split..n],
        &golden::E2E_OCK_ANG[split..n],
        TAIL_TOL_DEG,
        "ockendon angle (settled tail)",
    );
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
