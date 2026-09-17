//! Batch orchestration: walk a complete raw log, produce a scored trajectory.
//!
//! This is the sequencing the Python reference performs in
//! `imu_calibration_tuner.replay_trial` — bias calibration, release capture,
//! per-sample fusion, tick resample, EMA. It owns no algorithm: every stage is
//! a call into `ahrs`, `calibration`, `stillness`, `resample`, `goniometry`.
//!
//! Ordering is a contract, not a style choice. Accel must be processed before
//! gyro at the same timestamp (the gyro branch reads the stored accel), and
//! `release_quat` is snapshotted BEFORE the firing sample is integrated — the
//! two instants differ by the hold's accumulated drift, measured at 8.7° on a
//! real capture (spec §4.2).

use crate::ahrs::{gravity_seed, qconj, qmul, MadgwickAhrs, Quat, Vec3};
use crate::calibration::{calibrate_accel_bias, calibrate_gyro_bias, ReleaseDetector};
use crate::goniometry::{ockendon_deg, OCKENDON_FT_RATIO};
use crate::resample::{ema_smooth, rescale_ema_alpha, tick_resample, ANALYSIS_TICK_S};
use crate::stillness::{is_stationary_window, SampleBuf, GYRO_BIAS_WINDOW_S};

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Sensor {
    Accel,
    Mag,
    Gyro,
}

#[derive(Clone, Copy, Debug)]
pub struct RawSample {
    /// Seconds. Drives the tick grid.
    pub t: f64,
    /// Milliseconds. Drives `dt`; absent or constant timing silently
    /// fabricates `dt = 0.01` in the reference (spec §3.4).
    pub ts_ms: i64,
    pub sensor: Sensor,
    pub v: Vec3,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Method {
    /// `180 - swing`; the persisted live default.
    Relative,
    /// Ockendon & Gilbert applied to the swing as tibial inclination.
    Ockendon,
}

#[derive(Clone, Copy, Debug)]
pub struct ReplayConfig {
    pub beta: f64,
    pub ema_alpha: f64,
    pub method: Method,
    /// KTD10: the live path passes no magnetometer even when one is present.
    pub use_mag: bool,
    /// KTD9 `set_release_override`: index into the sample slice.
    pub release_override: Option<usize>,
}

impl Default for ReplayConfig {
    fn default() -> Self {
        Self {
            beta: crate::ahrs::BETA,
            ema_alpha: 0.3,
            method: Method::Relative,
            use_mag: false,
            release_override: None,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum TrialError {
    /// Fewer samples than any stage can work with.
    InsufficientSamples,
    /// No qualifying release: recoverable retroactively via
    /// `ReplayConfig::release_override` (KTD9), never by re-recording.
    ReleaseNeverDetected,
}

#[derive(Clone, Debug)]
pub struct TrialResult {
    /// Tick times, relative to the first sample.
    pub t: Vec<f64>,
    /// EMA-smoothed angle on the tick grid. Index 0 is NaN by contract.
    pub angle_deg: Vec<f64>,
    /// Orientation at the release instant — the trial's zero pose.
    pub release_quat: Quat,
    /// Index into the input slice where release fired.
    pub release_idx: usize,
}

fn sub3(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

/// Rotation from the zero pose, in degrees, SIGNED about the trial's swing
/// axis. Quaternion delta rather than differenced Euler angles: pitch
/// extraction is unreliable near ±90°, exactly where a pendulum swing's tibia
/// passes.
///
/// This is the fix for the abs()-fold bug. The magnitude used to be
/// `2*acos(|w|)`, which — being a magnitude — mirrors any swing that crosses
/// the zero reference (the negative half reflected up to positive), corrupting
/// N, A0, area_ratio and f. The magnitude is unchanged; `sign_axis` (from
/// [`fallback_sign_axis`]) restores the sign so a zero-crossing swing keeps its
/// direction. Mirrors `imu_calibration_tuner._swing_from_quats`'s axis-agnostic
/// fallback (the phone core has no captured flexion axis; that is U3's
/// orchestrator). Sign is dropped (magnitude only) when the trial never moved.
fn swing_deg(q_zero: Quat, q_cur: Quat, sign_axis: Option<Vec3>) -> f64 {
    let d = qmul(qconj(q_zero), q_cur);
    let mag = 2.0 * d[0].abs().clamp(-1.0, 1.0).acos().to_degrees();
    match sign_axis {
        Some(a) => {
            // Sign from the RAW quaternion vector part projected onto the
            // reference axis. The AHRS integrates `d` continuously, so this
            // projection is continuous and flips exactly when the swing crosses
            // the zero reference -- not at the w==0 (180 deg) hemisphere seam,
            // where flipping would turn the magnitude's peak into a spurious
            // step. For the physical swings this path sees (well under 180 deg)
            // the raw and canonical vector parts are identical.
            let proj = d[1] * a[0] + d[2] * a[1] + d[3] * a[2];
            if proj < 0.0 {
                -mag
            } else {
                mag
            }
        }
        None => mag,
    }
}

/// Per-trial reference axis that restores the SIGN of [`swing_deg`], so a swing
/// crossing the zero reference is not folded. The mean direction of the raw
/// quaternion vector parts across the trial, each aligned to the DEEPEST swing
/// (the largest rotation from the zero pose) so the dominant motion reads
/// positive. Pinning to the deepest swing rather than the first moving sample
/// matters: the first sample is often the pre-release hold, which sits on the
/// opposite side of a mid-drop zero capture and would otherwise flip the whole
/// trial's sign. Mirrors `imu_calibration_tuner._fallback_sign_axis` op-for-op
/// (an aligned mean, deliberately not an eigen-decomposition, so no eigensolver
/// is needed here). `None` when nothing moved.
fn fallback_sign_axis(q_zero: Quat, sample_q: &[Quat]) -> Option<Vec3> {
    let mut vecs: Vec<Vec3> = Vec::with_capacity(sample_q.len());
    let mut best: Option<Vec3> = None;
    let mut best_n = 0.0_f64;
    for &q in sample_q {
        let d = qmul(qconj(q_zero), q);
        let v = [d[1], d[2], d[3]];
        let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        if n < 1e-6 {
            continue;
        }
        vecs.push(v);
        if n > best_n {
            best_n = n;
            best = Some(v);
        }
    }
    let best = best?;
    let mut acc: Vec3 = [0.0; 3];
    for v in vecs {
        let aligned = if v[0] * best[0] + v[1] * best[1] + v[2] * best[2] >= 0.0 {
            v
        } else {
            [-v[0], -v[1], -v[2]]
        };
        for k in 0..3 {
            acc[k] += aligned[k];
        }
    }
    let n = (acc[0] * acc[0] + acc[1] * acc[1] + acc[2] * acc[2]).sqrt();
    if !n.is_finite() || n < 1e-12 {
        return None;
    }
    Some([acc[0] / n, acc[1] / n, acc[2] / n])
}

pub fn replay(samples: &[RawSample], cfg: &ReplayConfig) -> Result<TrialResult, TrialError> {
    if samples.len() < 40 {
        return Err(TrialError::InsufficientSamples);
    }

    let mut ahrs = MadgwickAhrs::new(cfg.beta);
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
    let mut zero: Option<(Quat, usize)> = None;

    let mut sample_t: Vec<f64> = Vec::with_capacity(samples.len());
    let mut sample_q: Vec<Quat> = Vec::with_capacity(samples.len());

    for (i, s) in samples.iter().enumerate() {
        match s.sensor {
            Sensor::Accel => {
                accel_hold.push((s.t, s.v));
                let cutoff = s.t - GYRO_BIAS_WINDOW_S;
                accel_hold.retain(|(t, _)| *t >= cutoff);
                if !seeded {
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
                        if d > 0.0 && d < 0.5 {
                            d
                        } else {
                            0.01
                        }
                    }
                    _ => 0.01,
                };
                last_ts = Some(s.ts_ms);

                // The override is authoritative: a clinician's post-hoc
                // correction must win even when auto-detection would have
                // fired earlier. `on_gyro_sample` still runs unconditionally
                // so its internal latching state stays correct regardless of
                // which path actually captures the zero pose.
                let fired = detector.on_gyro_sample(s.v, &gyro_hold, s.t);
                let capture = match cfg.release_override {
                    Some(idx) => i == idx,
                    None => fired,
                };
                if capture && zero.is_none() {
                    zero = Some((ahrs.q, i));
                }

                if zero.is_none() {
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
                    let m = if cfg.use_mag { mag } else { None };
                    ahrs.update(sub3(s.v, gyro_bias), a, m, dt);
                }
            }
        }
        sample_t.push(s.t);
        sample_q.push(ahrs.q);
    }

    let (release_quat, release_idx) = zero.ok_or(TrialError::ReleaseNeverDetected)?;

    // Reference axis for the signed swing, computed once from the whole trial
    // (the phone core has no captured flexion axis). See `fallback_sign_axis`.
    let sign_axis = fallback_sign_axis(release_quat, &sample_q);

    let angle_raw: Vec<f64> = sample_q
        .iter()
        .map(|q| {
            let sw = swing_deg(release_quat, *q, sign_axis);
            match cfg.method {
                Method::Relative => 180.0 - sw,
                Method::Ockendon => ockendon_deg(sw, OCKENDON_FT_RATIO),
            }
        })
        .collect();

    // Parameters are derived on the 100 Hz analysis grid, not the 20 Hz
    // display poll -- see `resample::ANALYSIS_TICK_S`. The EMA coefficient is
    // rescaled with it so only the grid moves and the filter stays put.
    let (t, held) = tick_resample(&sample_t, &angle_raw, ANALYSIS_TICK_S);
    Ok(TrialResult {
        t,
        angle_deg: ema_smooth(&held, rescale_ema_alpha(cfg.ema_alpha, ANALYSIS_TICK_S)),
        release_quat,
        release_idx,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Unit quaternion for a rotation of `phi` (rad) about a unit axis.
    fn q_about(axis: Vec3, phi: f64) -> Quat {
        let n = (axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2]).sqrt();
        let a = [axis[0] / n, axis[1] / n, axis[2] / n];
        let h = 0.5 * phi;
        [h.cos(), h.sin() * a[0], h.sin() * a[1], h.sin() * a[2]]
    }

    /// The abs()-fold regression: a swing that crosses the zero reference must
    /// come back SIGNED, not mirrored up to positive.
    #[test]
    fn swing_that_crosses_zero_is_signed_not_folded() {
        let axis = [1.0, 0.0, 0.0];
        let zero = q_about(axis, 0.0);
        let a = 30.0_f64.to_radians();
        let seq: Vec<Quat> = (0..400)
            .map(|i| {
                let phi = a * (i as f64 / 399.0 * 4.0 * std::f64::consts::PI).sin();
                q_about(axis, phi)
            })
            .collect();
        let sign_axis = fallback_sign_axis(zero, &seq);
        assert!(sign_axis.is_some(), "a moving trial must yield a sign axis");
        let out: Vec<f64> = seq.iter().map(|q| swing_deg(zero, *q, sign_axis)).collect();

        let min = out.iter().cloned().fold(f64::INFINITY, f64::min);
        let max = out.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        assert!(min < -27.0, "negative half folded away: min={min}");
        assert!(max > 27.0, "positive half missing: max={max}");
        // Tracks the true signed angle, not its absolute value.
        for (i, &v) in out.iter().enumerate() {
            let phi = (a * (i as f64 / 399.0 * 4.0 * std::f64::consts::PI).sin()).to_degrees();
            assert!((v - phi).abs() < 1e-6, "sample {i}: got {v}, want {phi}");
        }
    }

    /// A monotonic swing that never crosses zero must be UNCHANGED from the old
    /// unsigned magnitude (`2*acos(|w|)`) — this is why the golden E2E fixture
    /// keeps passing after the fix.
    #[test]
    fn monotonic_swing_matches_the_unsigned_magnitude() {
        let axis = [0.0, 1.0, 0.0];
        let zero = q_about(axis, 0.0);
        let seq: Vec<Quat> = (0..200)
            .map(|i| q_about(axis, (i as f64 / 199.0) * 80.0_f64.to_radians()))
            .collect();
        let sign_axis = fallback_sign_axis(zero, &seq);
        for q in &seq {
            let d = qmul(qconj(zero), *q);
            let mag = 2.0 * d[0].abs().clamp(-1.0, 1.0).acos().to_degrees();
            let signed = swing_deg(zero, *q, sign_axis);
            assert!((signed - mag).abs() < 1e-12, "signed {signed} != mag {mag}");
        }
    }

    #[test]
    fn a_still_trial_has_no_sign_axis() {
        let zero = q_about([1.0, 0.0, 0.0], 0.0);
        let seq = vec![zero; 50];
        assert!(fallback_sign_axis(zero, &seq).is_none());
    }
}
