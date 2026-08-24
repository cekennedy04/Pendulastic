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
//! **The orchestration below is deliberately test-local.** Walking the raw
//! log — bias calibration, release capture, per-sample fusion — is U3's
//! `compute_score()` to own; the plan puts it there, not in U2's file list.
//! Writing it here keeps U2 verifiable now without pre-empting U3's API
//! (`TrialError`, `ReleaseNeverDetected`, `set_release_override`). When U3
//! lands, this harness should collapse into a call to it, and the assertions
//! below carry over unchanged — they are the contract U3 has to satisfy.
//!
//! **Not covered:** `flex_axis_capture=True`, the axis-projection variant of
//! the swing-angle computation. It has no Rust port yet (also U3's
//! orchestrator), so the fixture pins the `False` branch only.

#[path = "fixtures/golden.rs"]
mod golden;

use mobile_imu_core::ahrs::{gravity_seed, qconj, qmul, MadgwickAhrs, Quat, Vec3};
use mobile_imu_core::calibration::{calibrate_accel_bias, calibrate_gyro_bias, ReleaseDetector};
use mobile_imu_core::goniometry::{ockendon_deg, OCKENDON_FT_RATIO};
use mobile_imu_core::resample::{ema_smooth, tick_resample, TICK_S};
use mobile_imu_core::scoring::{compute_pt_params, score_waveform, SpasticityType};
use mobile_imu_core::stillness::{is_stationary_window, SampleBuf, GYRO_BIAS_WINDOW_S};

// ---- the raw log, rebuilt from the fixture ------------------------------

#[derive(Clone, Copy, PartialEq)]
enum Sensor {
    Accel,
    Mag,
    Gyro,
}

struct RawSample {
    t: f64,
    ts_ms: i64,
    sensor: Sensor,
    v: Vec3,
}

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

// ---- swing angle from the zero pose -------------------------------------

/// `replay_trial._swing_from_quats`, solo-role / `flex_axis_capture=False`
/// branch: the magnitude of the rotation from the captured zero pose.
///
/// Deliberately a quaternion delta rather than a difference of Euler-extracted
/// pitch angles — the reference switched to this after the Euler form produced
/// 100+ deg RMSE against OptiTrack, because pitch extraction is unreliable
/// near ±90°, exactly where a real pendulum swing's tibia passes.
fn swing_deg(q_zero: Quat, q_cur: Quat) -> f64 {
    let q_delta = qmul(qconj(q_zero), q_cur);
    let dot = q_delta[0].abs().clamp(-1.0, 1.0);
    2.0 * dot.acos().to_degrees()
}

#[derive(Clone, Copy, PartialEq)]
enum Method {
    /// `180 - swing`: the persisted live default.
    Relative,
    /// Ockendon & Gilbert's model applied to the swing as tibial inclination.
    Ockendon,
}

// ---- the pipeline under test --------------------------------------------

/// Walk the raw log exactly as `replay_trial` does, then resample, smooth,
/// and hand the result to the scorer.
///
/// Returns `None` when the log never zeroed — no qualifying release was
/// detected — matching `replay_trial`'s empty-array return. That is the
/// condition U3 will surface as `ReleaseNeverDetected`.
fn replay(method: Method, use_mag: bool) -> Option<(Vec<f64>, Vec<f64>)> {
    let log = raw_log();

    let mut ahrs = MadgwickAhrs::new(golden::E2E_BETA);
    let mut detector = ReleaseDetector::new();
    let mut gyro_hold: SampleBuf = Vec::new();
    let mut accel_hold: SampleBuf = Vec::new();
    let mut gyro_bias: Vec3 = [0.0; 3];
    let mut accel_bias: Vec3 = [0.0; 3];
    let mut accel: Option<Vec3> = None;
    let mut mag: Option<Vec3> = None;
    let mut last_ts: Option<i64> = None;
    let mut seeded = false;
    let mut calib_was_stable = false;
    let mut q_zero: Option<Quat> = None;

    // Per-sample quaternion, recorded AFTER that sample is processed. That
    // offset is what makes `tick_resample`'s zero-order hold equivalent to
    // the reference's tick snapshot, which is taken just *before* the first
    // sample at or after the tick — hold at tick i yields the value from
    // sample i-1, i.e. the state as of just before sample i. Pointwise
    // functions commute with a zero-order hold, so converting to an angle
    // per sample and then holding equals holding quaternions and then
    // converting, which is what lets `q_zero` be applied retroactively.
    let mut sample_t: Vec<f64> = Vec::with_capacity(log.len());
    let mut sample_q: Vec<Quat> = Vec::with_capacity(log.len());

    for s in &log {
        match s.sensor {
            Sensor::Accel => {
                // The buffer holds RAW accel: bias is estimated from it, so
                // storing bias-corrected values would only ever measure the
                // residual of a stale correction.
                accel_hold.push((s.t, s.v));
                let cutoff = s.t - GYRO_BIAS_WINDOW_S;
                accel_hold.retain(|(t, _)| *t >= cutoff);
                if !seeded {
                    // gravity_seed=True, matching the persisted live config.
                    ahrs.q = gravity_seed(s.v);
                    seeded = true;
                }
                accel = Some(sub3(s.v, accel_bias));
            }
            Sensor::Mag => mag = Some(s.v),
            Sensor::Gyro => {
                let dt = match last_ts {
                    Some(prev) if s.ts_ms != 0 => {
                        let d = (s.ts_ms - prev) as f64 / 1000.0;
                        // A dt outside this range means a dropped or
                        // duplicated timestamp, not a real interval.
                        if d > 0.0 && d < 0.5 {
                            d
                        } else {
                            0.01
                        }
                    }
                    _ => 0.01,
                };
                last_ts = Some(s.ts_ms);

                // Release detection runs BEFORE this sample's rotation is
                // integrated, so the zero pose is captured truly "just
                // before" onset rather than one step into it. It also reads
                // `gyro_hold` before this sample joins it, so a genuine
                // release's own ramp-up cannot poison the calm check gating
                // it.
                if detector.on_gyro_sample(s.v, &gyro_hold, s.t) {
                    q_zero = Some(ahrs.q);
                }

                // Bias calibration only runs pre-release, on the rising edge
                // of a verified-stillness window — mirroring live's
                // countdown-only gating.
                if q_zero.is_none() {
                    let stable = is_stationary_window(&gyro_hold, &accel_hold, s.t);
                    if stable && !calib_was_stable {
                        if let Some(b) = calibrate_gyro_bias(&gyro_hold) {
                            gyro_bias = b;
                        }
                        if let Some(b) = calibrate_accel_bias(&accel_hold) {
                            accel_bias = b;
                        }
                        calib_was_stable = true;
                    } else {
                        calib_was_stable = stable;
                    }
                }

                gyro_hold.push((s.t, s.v));
                let cutoff = s.t - GYRO_BIAS_WINDOW_S;
                gyro_hold.retain(|(t, _)| *t >= cutoff);

                if let Some(a) = accel {
                    // KTD10: the live path passes None here even though the
                    // magnetometer stream is present in the log.
                    let m = if use_mag { mag } else { None };
                    ahrs.update(sub3(s.v, gyro_bias), a, m, dt);
                }
            }
        }
        sample_t.push(s.t);
        sample_q.push(ahrs.q);
    }

    let q_zero = q_zero?;

    let angle_raw: Vec<f64> = sample_q
        .iter()
        .map(|q| {
            let swing = swing_deg(q_zero, *q);
            match method {
                Method::Relative => 180.0 - swing,
                Method::Ockendon => ockendon_deg(swing, OCKENDON_FT_RATIO),
            }
        })
        .collect();

    let (t_ticks, held) = tick_resample(&sample_t, &angle_raw, TICK_S);
    Some((t_ticks, ema_smooth(&held, golden::E2E_EMA_ALPHA)))
}

fn sub3(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
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
    let (t, ang) = replay(Method::Relative, false).expect("release must be detected");
    assert_series_close(&t, golden::TRIAL_E2E_T, 1e-12, "tick times");
    assert_series_close(&ang, golden::TRIAL_E2E_ANG, ANGLE_TOL_DEG, "replayed angle");
}

#[test]
fn full_pipeline_reproduces_the_python_score() {
    let (t, ang) = replay(Method::Relative, false).expect("release must be detected");
    let sw = score_waveform(&t, &ang);

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
    assert_eq!(
        p.spasticity_type,
        SpasticityType::Extension,
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
    let (t, ang) = replay(Method::Relative, false).expect("release must be detected");
    let p = compute_pt_params(&t, &ang, None, false).expect("trial must be scorable");

    close(p.f, 1.0, 0.05, "recovered swing frequency (Hz)");
    // Neutral is where the swing settles: 180 deg minus the true flexion.
    close(
        p.neutral_deg,
        180.0 - golden::E2E_TRUE_A0_DEG,
        1.5,
        "recovered resting angle",
    );
    // A0 lags the true amplitude slightly — EMA smoothing and the fusion's
    // own response both cost a little peak — but not by much.
    close(
        p.a0_deg,
        golden::E2E_TRUE_A0_DEG,
        2.0,
        "recovered first-swing amplitude",
    );
}

#[test]
fn ockendon_method_reproduces_the_python_replay() {
    // The only path that runs ockendon_deg inside the pipeline rather than as
    // a standalone function, so it is the only place a wrong argument (e.g.
    // feeding it 180-swing instead of swing) would show up.
    let (_, ang) = replay(Method::Ockendon, false).expect("release must be detected");
    assert_series_close(&ang, golden::E2E_OCK_ANG, ANGLE_TOL_DEG, "ockendon angle");
}

#[test]
fn the_magnetometer_stream_is_present_and_deliberately_unused() {
    // Two halves, and both are needed. That the no-mag run matches the golden
    // (asserted above) shows mag is excluded; on its own that is also what a
    // log with no mag samples in it would show. Feeding the same stream in
    // and getting a different answer proves the samples are real and carry
    // signal — so KTD10's exclusion is an active choice, not a fixture that
    // happens to have nothing to exclude.
    let (_, without) = replay(Method::Relative, false).expect("release must be detected");
    let (_, with) = replay(Method::Relative, true).expect("release must be detected");

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
