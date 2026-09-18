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
use crate::flex_axis::FlexAxisEstimator;
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

/// Swing angle from the zero pose, in degrees, matching the validated Python
/// `_swing_from_quats` / `swing_angle_deg` three-tier extraction op-for-op:
///   (a) projected onto the captured flex axis: `abs(theta * dot(u, flex_axis))`
///   (b) solo across-gravity (no flex axis yet): `abs(theta * |u - (u.g)g|)`
///   (c) axis-agnostic total-rotation fallback: `2*acos(|w|)`
/// Previously the Rust core always used (c), which counts free-integrating yaw
/// drift as flexion (86.9° reported on a still phone) — 20-53° off the Python
/// projection on ~20% of real trials. The magnitude is `abs()` (the clinical,
/// corpus-validated convention; the signed variant regressed corpus test-retest).
fn swing_deg(q_zero: Quat, q_cur: Quat, flex_axis: Option<Vec3>, g_zero: Option<Vec3>) -> f64 {
    let d = qmul(qconj(q_zero), q_cur);
    // Canonical hemisphere (w >= 0), exactly as the Python mirror does before
    // decomposing to axis-angle.
    let q = if d[0] >= 0.0 { d } else { [-d[0], -d[1], -d[2], -d[3]] };
    let qv = [q[1], q[2], q[3]];
    let sin_half = (qv[0] * qv[0] + qv[1] * qv[1] + qv[2] * qv[2]).sqrt();

    if let Some(axis) = flex_axis {
        if sin_half <= 1e-9 {
            return 0.0;
        }
        let theta = 2.0 * q[0].min(1.0).acos();
        let u = [qv[0] / sin_half, qv[1] / sin_half, qv[2] / sin_half];
        let proj = u[0] * axis[0] + u[1] * axis[1] + u[2] * axis[2];
        return (theta * proj).abs().to_degrees();
    }
    if let Some(g) = g_zero {
        if sin_half <= 1e-9 {
            return 0.0;
        }
        let theta = 2.0 * q[0].min(1.0).acos();
        let u = [qv[0] / sin_half, qv[1] / sin_half, qv[2] / sin_half];
        let along = u[0] * g[0] + u[1] * g[1] + u[2] * g[2];
        let across = [u[0] - along * g[0], u[1] - along * g[1], u[2] - along * g[2]];
        let n = (across[0] * across[0] + across[1] * across[1] + across[2] * across[2]).sqrt();
        return (theta * n).abs().to_degrees();
    }
    2.0 * d[0].abs().clamp(-1.0, 1.0).acos().to_degrees()
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
    // Flex-axis capture, matching the Python replay: fed raw gyro from the
    // release onward, its own threshold/commit logic does the rest. `g_zero` is
    // the sensor-frame gravity direction at release, for the solo across-gravity
    // fallback when no flex axis commits.
    let mut flex = FlexAxisEstimator::new();
    let mut g_zero: Option<Vec3> = None;

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
                    // Gravity direction at release (for the across-gravity tier).
                    if let Some(a) = accel {
                        let n = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]).sqrt();
                        if n > 1e-6 {
                            g_zero = Some([a[0] / n, a[1] / n, a[2] / n]);
                        }
                    }
                }
                // Feed the flex-axis estimator from the release onward, on the
                // RAW gyro (as the Python reference does); its threshold gate and
                // 25-sample commit select the swing burst. Idempotent once
                // committed.
                if zero.is_some() {
                    flex.update(s.v, accel);
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

    // Committed (or provisional) flex axis for the whole trial, matching the
    // Python replay which projects every tick onto the final captured axis.
    let flex_axis = flex.axis();

    let angle_raw: Vec<f64> = sample_q
        .iter()
        .map(|q| {
            let sw = swing_deg(release_quat, *q, flex_axis, g_zero);
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
