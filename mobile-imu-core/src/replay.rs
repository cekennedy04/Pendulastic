//! Batch orchestration: walk a complete raw log, produce a scored trajectory.
//!
//! This is the sequencing the Python reference performs in
//! `imu_calibration_tuner.replay_trial` — bias calibration, release capture,
//! per-sample fusion, tick resample, EMA. It owns no algorithm: every stage is
//! a call into `ahrs`, `calibration`, `stillness`, `resample`, `goniometry`,
//! `flex_axis`.
//!
//! Ordering is a contract, not a style choice. Accel must be processed before
//! gyro at the same timestamp (the gyro branch reads the stored accel), and
//! `release_quat` is snapshotted BEFORE the firing sample is integrated — the
//! two instants differ by the hold's accumulated drift, measured at 8.7° on a
//! real capture (spec §4.2).

use crate::ahrs::{gravity_seed, qconj, qmul, MadgwickAhrs, Quat, Vec3};
use crate::calibration::{
    calibrate_accel_bias, calibrate_gyro_bias, gravity_dir_from_hold, ReleaseDetector,
};
use crate::flex_axis::{FlexAxisEstimator, DEFAULT_THRESHOLD};
use crate::goniometry::{ockendon_deg, OCKENDON_FT_RATIO};
use crate::resample::{ema_smooth, rescale_ema_alpha, tick_resample, ANALYSIS_TICK_S, TICK_S};
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
    /// EMA alpha as tuned on the [`TICK_S`] (50 ms) grid. [`replay`] rescales
    /// it to `tick_s` with [`rescale_ema_alpha`], exactly as `replay_trial`
    /// does, so the same value means the same filter at every tick.
    pub ema_alpha: f64,
    pub method: Method,
    /// KTD10: the live path passes no magnetometer even when one is present.
    pub use_mag: bool,
    /// KTD9 `set_release_override`: index into the sample slice.
    pub release_override: Option<usize>,
    /// Output grid, seconds: [`ANALYSIS_TICK_S`] on the reference footing,
    /// [`TICK_S`] on the pre-0.4.0 app footing.
    pub tick_s: f64,
    /// Project the swing onto a flexion axis learnt from the swing itself
    /// (`replay_trial`'s `flex_axis_capture`). Off, the swing is the rotation
    /// across gravity, as the app scored before 0.4.0.
    pub flex_axis_capture: bool,
    /// What the flexion axis is fitted on. Ignored when `flex_axis_capture`
    /// is off.
    pub flex_axis_fit: FlexAxisFit,
}

/// What a replay fits the flexion axis on (`replay_trial`'s `flex_axis_fit`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum FlexAxisFit {
    /// The live rule: the first 25 samples at or above 1 rad/s after the zero,
    /// levelled against the accelerometer. The reference footing up to crate
    /// 0.4.x (`ref-100hz-ema0.5-flex`).
    Opening,
    /// Every sample at or above 1 rad/s from the zero until the swing has
    /// died (a second under that rate) or the recording ends, not levelled (`FlexAxisEstimator::whole_recording`). The
    /// reference footing from crate 0.5.0.
    Recording,
}

/// Name of the footing [`ReplayConfig::reference`] builds, stored on every
/// trial record from crate 0.4.0 (`scoring_footing`). Pinned against the
/// config's own fields by `tests/reference_footing_test.rs`.
///
/// `-flexrec` (crate 0.5.0) replaced `-flex`: the flexion axis is fitted on
/// the whole recording and not levelled, which moves every angle, so trends
/// must not be drawn across the two.
pub const SCORING_FOOTING: &str = "ref-100hz-ema0.5-flexrec";

impl ReplayConfig {
    /// The reference footing: `imu_calibration_config.REFERENCE_REPLAY_CONFIG`
    /// in Python. (Python tools that replay with the local config file or a
    /// hard-coded config are NOT on it; from 0.5.0 they differ in the axis
    /// fit.) — 10 ms ticks, EMA 0.5 per 50 ms, flex projection on. The
    /// app scores on it from crate 0.4.0 so a trial scores the same on the
    /// phone and on the desktop. Pinned here, not passed in from JS, so
    /// `ALGORITHM_VERSION` (crate version, git revision, wasm hash) changes
    /// whenever it does.
    pub fn reference() -> Self {
        Self {
            beta: crate::ahrs::BETA,
            ema_alpha: 0.5,
            method: Method::Relative,
            use_mag: false,
            release_override: None,
            tick_s: ANALYSIS_TICK_S,
            flex_axis_capture: true,
            flex_axis_fit: FlexAxisFit::Recording,
        }
    }

    /// The app footing before crate 0.4.0: 50 ms ticks, EMA 0.3, no flex
    /// projection. Kept for the fixtures that pin the AHRS, the hold and the
    /// tick stage, which the footing change does not alter, and for
    /// re-scoring a pre-0.4.0 record the way it was scored.
    pub fn app_pre_v0_4() -> Self {
        Self {
            ema_alpha: 0.3,
            tick_s: TICK_S,
            flex_axis_capture: false,
            ..Self::reference()
        }
    }
}

impl Default for ReplayConfig {
    fn default() -> Self {
        Self::reference()
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

impl TrialError {
    /// Stable machine-readable code, for the wasm boundary and stored records.
    /// These strings are a persisted contract: do not rename them.
    pub fn code(&self) -> &'static str {
        match self {
            TrialError::InsufficientSamples => "insufficient_samples",
            TrialError::ReleaseNeverDetected => "release_never_detected",
        }
    }
}

#[derive(Clone, Debug)]
pub struct TrialResult {
    /// Tick times, relative to the first sample.
    pub t: Vec<f64>,
    /// EMA-smoothed angle on the tick grid. Index 0 is NaN by contract.
    pub angle_deg: Vec<f64>,
    /// The trial's zero pose: the orientation at the release instant. For a
    /// release that stalled and resumed (`calibration::STALL_*`) this is the
    /// pose where the departure BEGAN, before the first movement, not where
    /// the capture threshold was finally crossed.
    pub release_quat: Quat,
    /// Index into the input slice of the sample the zero pose was taken at:
    /// where release fired, or, for a release that stalled and resumed, where
    /// its departure began (up to `STALL_DEPARTURE_MAX_S` earlier).
    pub release_idx: usize,
    /// The flexion axis the swing was projected onto (sensor frame, unit),
    /// or `None` when `flex_axis_capture` is off or no sample after the zero
    /// cleared the capture threshold.
    pub flex_axis: Option<Vec3>,
}

fn sub3(a: Vec3, b: Vec3) -> Vec3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

/// Longest gap still treated as a real gyro sample interval. Beyond this the
/// gap is a reconnect or a clock jump rather than a sample rate.
///
/// Ported from the reference 2026-10-27. This bound was 0.5 s here, which
/// silently substituted [`NOMINAL_GYRO_DT_S`] for any stream slower than
/// 2 Hz. Sensor Stream Pro delivers gyro at 1.0 Hz while accel runs at 100 Hz
/// -- measured on a live trial, 16 gyro samples in 15.9 s, the fallback taken
/// on all 16 -- so every sample integrated 10 ms of rotation where 1000 ms had
/// elapsed. That pinned the reported angle at 4.9 deg through a ~90 deg
/// physical rotation. A real interval is now used as measured, however long.
pub const MAX_GYRO_DT_S: f64 = 2.0;

/// dt used only when the true interval is unknowable: the first sample after a
/// connect, or a phone that sends no usable timestamp.
pub const NOMINAL_GYRO_DT_S: f64 = 0.01;

/// Rotation magnitude from the zero pose, in degrees. Quaternion delta rather
/// than differenced Euler angles: pitch extraction is unreliable near ±90°,
/// exactly where a pendulum swing's tibia passes.
///
/// Three tiers, in `replay_trial`'s order (`_swing_from_quats`): a captured
/// flexion axis, else the rotation across gravity, else the total rotation.
/// Every tier reports a magnitude (`abs()`): the clinical corpus is scored on
/// it, and a signed variant regressed its test-retest, so it stays out.
fn swing_deg(q_zero: Quat, q_cur: Quat, g_zero: Option<Vec3>, flex_axis: Option<Vec3>) -> f64 {
    let d = qmul(qconj(q_zero), q_cur);

    // With a flexion axis, report only the rotation about it. The axis is
    // learnt from the swing's own gyro (`FlexAxisEstimator`), so rotation
    // about any other axis -- the yaw drift the gravity tier removes, and
    // also any abduction or wobble across the knee -- is discarded.
    if let Some(a) = flex_axis {
        let q = if d[0] >= 0.0 {
            d
        } else {
            [-d[0], -d[1], -d[2], -d[3]]
        };
        let sin_half = (q[1] * q[1] + q[2] * q[2] + q[3] * q[3]).sqrt();
        if sin_half <= 1e-9 {
            return 0.0;
        }
        let theta = 2.0 * q[0].min(1.0).acos();
        let along = (q[1] * a[0] + q[2] * a[1] + q[3] * a[2]) / sin_half;
        return (theta * along).to_degrees().abs();
    }

    // With a gravity reference, report rotation ACROSS the vertical only and
    // discard rotation about it. Ported from the reference 2026-10-27.
    //
    // Yaw is unobservable in this configuration: the magnetometer correction
    // is deliberately off and gravity carries no heading, so yaw
    // free-integrates whatever gyro error remains, without bound. Taking the
    // total quaternion distance -- which is all this function used to do --
    // counts that drift as knee flexion. Measured on a live trial, a phone
    // lying still whose gravity vector moved 0.19 deg reported 86.9 deg of
    // flexion, decomposing as roll 0.01, pitch 0.11, yaw 22.83.
    //
    // It assumes flexion is not about the vertical, which holds for a pendulum
    // test and is why a captured anatomical axis is preferred when there is
    // one.
    if let Some(g) = g_zero {
        // Canonical hemisphere before decomposing to axis-angle.
        let q = if d[0] >= 0.0 {
            d
        } else {
            [-d[0], -d[1], -d[2], -d[3]]
        };
        let sin_half = (q[1] * q[1] + q[2] * q[2] + q[3] * q[3]).sqrt();
        if sin_half <= 1e-9 {
            return 0.0;
        }
        let theta = 2.0 * q[0].min(1.0).acos();
        let u = [q[1] / sin_half, q[2] / sin_half, q[3] / sin_half];
        let along = u[0] * g[0] + u[1] * g[1] + u[2] * g[2];
        let across = [
            u[0] - along * g[0],
            u[1] - along * g[1],
            u[2] - along * g[2],
        ];
        let mag = (across[0] * across[0] + across[1] * across[1] + across[2] * across[2]).sqrt();
        return (theta * mag).to_degrees().abs();
    }

    // No gravity reference: total rotation distance, axis-agnostic. The
    // reference reaches this for the two-phone case, where common-mode yaw
    // drift largely cancels in the relative rotation.
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
    let mut zero: Option<(Quat, usize, Option<Vec3>)> = None;
    // The hold pose as of the start of the departure in progress, kept for a
    // release that stalls and resumes (see `calibration::STALL_MAX_S`).
    let mut departure_zero: Option<(Quat, usize, Option<Vec3>)> = None;
    let mut flex = match cfg.flex_axis_fit {
        FlexAxisFit::Opening => FlexAxisEstimator::default(),
        FlexAxisFit::Recording => FlexAxisEstimator::whole_recording(DEFAULT_THRESHOLD),
    };

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
                        if d > 0.0 && d <= MAX_GYRO_DT_S {
                            d
                        } else {
                            NOMINAL_GYRO_DT_S
                        }
                    }
                    _ => NOMINAL_GYRO_DT_S,
                };
                last_ts = Some(s.ts_ms);

                // The override is authoritative: a clinician's post-hoc
                // correction must win even when auto-detection would have
                // fired earlier. `on_gyro_sample` still runs unconditionally
                // so its internal latching state stays correct regardless of
                // which path actually captures the zero pose.
                let fired = detector.on_gyro_sample(s.v, &gyro_hold, s.t);
                if detector.departure_began() {
                    departure_zero = Some((ahrs.q, i, gravity_dir_from_hold(&accel_hold)));
                }
                let capture = match cfg.release_override {
                    Some(idx) => i == idx,
                    None => fired,
                };
                if capture && zero.is_none() {
                    // The gravity direction is snapshotted at the SAME instant
                    // as the zero pose, from the accel hold buffer as it
                    // stands then -- mirroring the reference's
                    // `_gravity_snapshot()` inside the capture branch. Taking
                    // it later would read a buffer the swing has already
                    // polluted, and gravity would no longer be the mean.
                    //
                    // A release that stalled and resumed fires with the leg
                    // already several degrees into its fall and the accel
                    // buffer already disturbed, so its zero is the pose and
                    // gravity snapshotted when that departure began. A direct
                    // release is zeroed here, as it always was.
                    let here = (ahrs.q, i, gravity_dir_from_hold(&accel_hold));
                    zero = Some(match departure_zero {
                        Some(z) if cfg.release_override.is_none() && detector.resumed() => z,
                        _ => here,
                    });
                }

                // The flexion axis is learnt from the zero-capture sample
                // onward, as `replay_trial` feeds it: raw gyro, with the
                // bias-corrected accel as gravity, before this sample is
                // integrated. The estimator ignores sub-threshold samples, and
                // under `FlexAxisFit::Opening` stops once committed, so it is
                // fed unconditionally after the zero; under
                // `FlexAxisFit::Recording` it takes every sample to the end. Under a release override the feed starts at the
                // override's sample, because the axis belongs to the swing the
                // zero pose starts; the reference has no override to compare.
                //
                // Time under the capture rate counts towards closing a
                // whole-recording fit (`FlexAxisEstimator::quiet`), so motion
                // after the swing has died cannot reach the axis.
                if cfg.flex_axis_capture && zero.is_some() {
                    let rate = (s.v[0] * s.v[0] + s.v[1] * s.v[1] + s.v[2] * s.v[2]).sqrt();
                    if rate < DEFAULT_THRESHOLD {
                        flex.quiet(dt);
                    } else {
                        flex.update(s.v, accel);
                    }
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

    let (release_quat, release_idx, g_zero) = zero.ok_or(TrialError::ReleaseNeverDetected)?;
    // The final estimate projects every sample, pre-release ones included, as
    // `replay_trial` projects every tick with the axis it ends on. Under
    // `Opening`, a swing that never commits keeps the provisional axis, as
    // there; under `Recording` the fit is committed here on whatever it has.
    if cfg.flex_axis_fit == FlexAxisFit::Recording {
        flex.finalize();
    }
    let flex_axis = if cfg.flex_axis_capture { flex.axis() } else { None };

    let angle_raw: Vec<f64> = sample_q
        .iter()
        .map(|q| {
            let sw = swing_deg(release_quat, *q, g_zero, flex_axis);
            match cfg.method {
                Method::Relative => 180.0 - sw,
                Method::Ockendon => ockendon_deg(sw, OCKENDON_FT_RATIO),
            }
        })
        .collect();

    let (t, held) = tick_resample(&sample_t, &angle_raw, cfg.tick_s);
    Ok(TrialResult {
        t,
        angle_deg: ema_smooth(&held, rescale_ema_alpha(cfg.ema_alpha, cfg.tick_s)),
        release_quat,
        release_idx,
        flex_axis,
    })
}
