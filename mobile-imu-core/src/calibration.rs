//! Bias calibration and release/zero-point auto-detection — ported from
//! `pendulastic_imu_server.py`'s `calibrate_gyro_bias`/`calibrate_accel_bias`
//! and `imu_calibration_tuner.py`'s two-state `calm_qualified`/
//! `pending_departure` release-detection machine (KTD9).
//!
//! **Scope simplification vs. the Python reference (KTD1):** the reference
//! supports a two-phone (proximal/distal) capture mode; this mobile app is
//! single-segment/solo-only (KTD1's IMU-only, Ockendon-model scope), so the
//! reference's `is_distal`/`is_solo` role gating — which exists only to
//! decide *which* device's motion is allowed to arm/capture — is dropped
//! here: there is always exactly one device, so it always qualifies.

use crate::ahrs::Vec3;
use crate::stillness::{recently_calm, SampleBuf, GYRO_BIAS_MIN_SAMPLES, ZERO_CAPTURE_GUARD_RAD_S};

/// Minimum `|ω|` (rad/s) to register a gyro burst as intentional motion
/// (as opposed to noise) once a release is eligible to be captured.
pub const FLEX_CAPTURE_THRESHOLD: f64 = 1.0;

fn norm3(v: Vec3) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

fn mean3(buf: &SampleBuf) -> Vec3 {
    let mut mean = [0.0; 3];
    for (_, v) in buf {
        for i in 0..3 {
            mean[i] += v[i];
        }
    }
    let n = buf.len() as f64;
    [mean[0] / n, mean[1] / n, mean[2] / n]
}

/// Estimate gyroscope static bias from the trailing hold-buffer mean.
/// Returns `None` when the buffer has too few samples to trust, or when the
/// estimate exceeds `GYRO_BIAS_MAX_RAD_S` (the window held motion) — the
/// caller should leave any previously-calibrated bias unchanged in that case,
/// mirroring `calibrate_gyro_bias()`'s "leaves gyro_bias at its previous
/// value" behavior.
pub fn calibrate_gyro_bias(hold_buf: &SampleBuf) -> Option<Vec3> {
    if hold_buf.len() < GYRO_BIAS_MIN_SAMPLES {
        return None;
    }
    // Per-axis significance gate, ported from the reference 2026-10-27. This
    // returned the window mean outright, which is what the reference did
    // before b37d927: on a trial where the phone lay still for 20.5 s the
    // true bias is 0.00376 rad/s while a 1 s window's mean errs by 0.0119 --
    // three times the quantity being measured. Subtracting such an estimate
    // injected 23 deg of yaw drift where leaving the bias at zero gave 1.8.
    //
    // Lengthening the window does not rescue it (5 s still errs by 0.005):
    // the limit is sensor noise, ~0.05 rad/s per axis, not window length. An
    // axis that fails the gate is left at zero, because subtracting noise is
    // strictly worse than subtracting nothing and the error grows without
    // bound in yaw, which has no other anchor. Roll and pitch are held by
    // gravity regardless.
    let mean = mean3(hold_buf);
    let n = hold_buf.len() as f64;
    let mut out = [0.0_f64; 3];
    for axis in 0..3 {
        // Sample standard deviation (ddof = 1), matching numpy's
        // `std(axis=0, ddof=1)` in the reference.
        let var = hold_buf
            .iter()
            .map(|(_, v)| {
                let d = v[axis] - mean[axis];
                d * d
            })
            .sum::<f64>()
            / (n - 1.0);
        let sem = var.sqrt() / n.sqrt();
        out[axis] = if mean[axis].abs() >= GYRO_BIAS_MIN_SNR * sem {
            mean[axis]
        } else {
            0.0
        };
    }
    // Plausibility bound: see GYRO_BIAS_MAX_RAD_S.
    if norm3(out) > GYRO_BIAS_MAX_RAD_S {
        return None;
    }
    Some(out)
}

/// Unit gravity direction in the sensor frame from a stillness window, or
/// `None` when the window is empty or its mean has no direction.
///
/// During a genuine hold the mean accel IS gravity, so its direction is the
/// vertical expressed in that sensor's own frame. Normalising makes it
/// unit-agnostic, so the g (iOS) versus m/s² (Android) scale difference needs
/// no detection here.
///
/// Ported from the reference's `_gravity_dir_from_hold` 2026-10-27, with the
/// across-gravity projection it feeds in `replay::swing_deg`.
pub fn gravity_dir_from_hold(hold_buf: &SampleBuf) -> Option<Vec3> {
    if hold_buf.is_empty() {
        return None;
    }
    let mean = mean3(hold_buf);
    let n = norm3(mean);
    if n < 1e-9 {
        return None;
    }
    Some([mean[0] / n, mean[1] / n, mean[2] / n])
}

/// Estimate accelerometer static bias from a verified-stillness window.
/// During true stillness, raw accel should equal gravity (magnitude g,
/// direction wherever the hold actually pointed) plus a small sensor offset;
/// any excess magnitude beyond g is bias.
///
/// g's magnitude is picked from the measured data's own scale rather than
/// hardcoded (iOS CoreMotion reports g's, magnitude ~1; Android SensorManager
/// reports m/s², magnitude ~9.81 — KTD10). The reference *direction* is taken
/// from the data too, never forced onto +Z: a hold that isn't level has real
/// gravity components off-axis, and forcing those onto Z would bake them
/// into "bias" instead, actively steering the AHRS toward a wrong
/// orientation. Correcting only the magnitude along the measured direction
/// leaves direction to [`crate::ahrs::gravity_seed`] and the AHRS's own
/// continuous correction step.
pub fn calibrate_accel_bias(hold_buf: &SampleBuf) -> Option<Vec3> {
    if hold_buf.len() < 2 {
        return None;
    }
    let mean = mean3(hold_buf);
    let mag = norm3(mean);
    if mag < 1e-9 {
        return None;
    }
    // ~9.81 (m/s²) vs ~1 (g) builds are separated by nearly an order of
    // magnitude; 3.0 sits comfortably in the gap clear of realistic
    // bias/noise on either side.
    let g = if mag > 3.0 { 9.81 } else { 1.0 };
    let scale = g / mag;
    Some([mean[0] - mean[0] * scale, mean[1] - mean[1] * scale, mean[2] - mean[2] * scale])
}

/// An above-guard excursion that settles without reaching the capture
/// threshold revokes a qualified hold -- unless it is small and brief enough to
/// be sensor noise grazing the guard. Mirrors `imu_calibration_tuner.py`'s
/// `_NOISE_EXCURSION_*`; see there for the corpus sweep behind the values.
pub const NOISE_EXCURSION_PEAK_RAD_S: f64 = 0.5;
pub const NOISE_EXCURSION_MAX_S: f64 = 0.2;

/// A gyro-bias estimate is applied per-axis only when the axis mean is at
/// least this many standard errors from zero. Mirrors the reference's
/// `GYRO_BIAS_MIN_SNR`; see `calibrate_gyro_bias` for the measurement behind
/// it and why 3.0 rather than 2.0 (swept over a still trial, a 2 sigma gate
/// accepts 8.7% of axes on pure noise and 3 sigma accepts 0.3%).
pub const GYRO_BIAS_MIN_SNR: f64 = 3.0;

/// Largest gyro-bias estimate (rad/s, vector magnitude) accepted as bias
/// rather than motion — `GYRO_BIAS_MAX_RAD_S` in the reference. The stillness
/// gate allows 0.9 rad/s peak-to-peak, so a window cut from a decaying swing's
/// tail can pass it, and its mean (real rotation over an incomplete cycle)
/// would be subtracted for the rest of the trial. On the phone bench, trials
/// starting mid-swing gave 0.11-0.17 rad/s "biases" and read A0 at 50-76°
/// against a commanded 30°; quiet holds measured < 0.02 rad/s.
pub const GYRO_BIAS_MAX_RAD_S: f64 = 0.05;

fn is_noise_excursion(peak_rad_s: f64, duration_s: f64) -> bool {
    peak_rad_s < NOISE_EXCURSION_PEAK_RAD_S && duration_s < NOISE_EXCURSION_MAX_S
}

/// A departure that settles below the guard before reaching the capture
/// threshold is not always handling. A leg with an early catch falls a few
/// degrees, is arrested for a moment, and then swings: its first drop peaks
/// under [`FLEX_CAPTURE_THRESHOLD`] and stalls, and the swing that follows a
/// tenth of a second later had no qualified hold behind it, so the trial came
/// back "release not found" (synthetic severity axis, lambda 1.2 to 1.5,
/// 2026-11-18). The same shape is a slow release whose rate dips under the
/// guard for one sample on its way up.
///
/// Such a departure is held open instead of revoked when it has already
/// turned the segment by at least [`STALL_MIN_ROTATION_DEG`]: for up to
/// [`STALL_MAX_S`] the hold stays qualified, and motion that resumes in the
/// same direction (cosine of at least [`STALL_RESUME_MIN_COS`] between the new
/// rate and the rotation so far) continues the same departure. Motion the
/// other way, or no motion in time, revokes the hold exactly as before.
///
/// A departure that has stalled must reach the capture threshold within
/// [`STALL_DEPARTURE_MAX_S`] of when it began, and every above-guard sample
/// after a resume must keep turning the same way, or the hold is revoked. So
/// a leg lowered in small steps cannot be chained into one long "departure"
/// and zeroed on a pose from seconds earlier.
///
/// WHAT THIS ADMITS. The gyro cannot tell an early catch from an examiner who
/// lowers the leg a few degrees, pauses under half a second and lets go, or
/// who gives it a second push the same way: both are now one release, zeroed
/// before the first movement. The old rule refused the first (no release
/// found) and, if a calm second followed, zeroed the second on the later pose.
/// Handling in another direction, a pause over [`STALL_MAX_S`], or a
/// departure longer than [`STALL_DEPARTURE_MAX_S`] is refused as before.
///
/// Swept with the Python mirror on 179 corpus phone trials: every trial that
/// fired before fires on the same sample and one slow release that never
/// fired now does. That is a statement about those trials, not a guarantee.
/// Mirrors `imu_calibration_tuner.py`'s `_STALL_*`.
pub const STALL_MAX_S: f64 = 0.5;
pub const STALL_MIN_ROTATION_DEG: f64 = 2.0;
pub const STALL_RESUME_MIN_COS: f64 = 0.7;
pub const STALL_DEPARTURE_MAX_S: f64 = 1.0;

/// Longest sample interval integrated into a departure's rotation. A longer
/// gap is a dropout, not motion, and contributes nothing.
pub const STALL_MAX_DT_S: f64 = 0.1;

/// Two-state zero-capture eligibility machine (KTD9). `calm_qualified`: a
/// genuine trailing-window calm period has been confirmed and not yet spent.
/// `pending_departure`: an above-guard excursion is currently in progress
/// following a qualified calm period — only while this is true is a
/// [`FLEX_CAPTURE_THRESHOLD`] crossing trusted as the true release. If that
/// excursion settles back below guard before ever reaching the capture
/// threshold, it's treated as handling, not a release: both flags reset and
/// a fresh full calm window is required before the next excursion can be
/// trusted. Two exceptions: an excursion small and brief enough to be sensor
/// noise leaves the hold qualified (`NOISE_EXCURSION_*`), and one that has
/// already turned the segment is held open briefly in case it resumes
/// (`STALL_*`).
///
/// This deliberately replaces a single permanent "ever calm" latch, which a
/// calm hold followed by an unrelated later handling burst could fool into
/// zeroing on that burst instead of the true release.
pub struct ReleaseDetector {
    calm_qualified: bool,
    pending_departure: bool,
    /// Start time and peak |omega| of the current above-guard excursion, so
    /// one that settles can be told apart as noise or handling.
    excursion_t0: f64,
    excursion_peak: f64,
    /// Rotation vector (rad) integrated from the raw gyro over the above-guard
    /// samples of the current departure, across any stalls: roughly how far
    /// and which way the segment has turned since the hold. Calm samples and
    /// dropouts are not integrated, and no bias is removed.
    excursion_rot: Vec3,
    /// The departure settled after real rotation and may still resume (see
    /// [`STALL_MAX_S`]); `stall_t0` is when it settled.
    stalled: bool,
    stall_t0: f64,
    /// The current departure has resumed after a stall at least once.
    resumed: bool,
    /// This sample began a departure from a qualified hold.
    began: bool,
    last_t: Option<f64>,
    /// Becomes `false` forever once a release is captured — only the first
    /// qualifying crossing matters for a given trial (mirrors
    /// `flex_axis_armed` in the Python source).
    armed: bool,
}

impl Default for ReleaseDetector {
    fn default() -> Self {
        Self::new()
    }
}

impl ReleaseDetector {
    pub fn new() -> Self {
        Self {
            calm_qualified: false,
            pending_departure: false,
            excursion_t0: 0.0,
            excursion_peak: 0.0,
            excursion_rot: [0.0; 3],
            stalled: false,
            stall_t0: 0.0,
            resumed: false,
            began: false,
            last_t: None,
            armed: true,
        }
    }

    /// Whether `omega` turns the way the current departure has turned so far.
    fn same_way(&self, omega: Vec3, omega_mag: f64) -> bool {
        let rot_mag = norm3(self.excursion_rot);
        let cos = (omega[0] * self.excursion_rot[0]
            + omega[1] * self.excursion_rot[1]
            + omega[2] * self.excursion_rot[2])
            / (omega_mag * rot_mag + 1e-12);
        cos >= STALL_RESUME_MIN_COS
    }

    /// The departure in progress was handling: a fresh calm window is needed
    /// before the next one can be trusted.
    fn revoke(&mut self) {
        self.stalled = false;
        self.pending_departure = false;
        self.resumed = false;
        self.calm_qualified = false;
    }

    /// True until a release has been captured; once released, further
    /// samples are ignored (see [`Self::on_gyro_sample`]).
    pub fn is_armed(&self) -> bool {
        self.armed
    }

    /// True when the sample just fed began a departure from a qualified hold.
    /// The pose just before that sample is the hold pose, and a caller that
    /// honours [`Self::resumed`] snapshots it here.
    pub fn departure_began(&self) -> bool {
        self.began
    }

    /// True when the departure in progress (or the one that just fired)
    /// stalled and resumed on its way to the capture threshold. The segment
    /// had then already left the hold pose by the time the release fired, so
    /// the zero pose is the one snapshotted at [`Self::departure_began`].
    pub fn resumed(&self) -> bool {
        self.resumed
    }

    /// Feed one gyro sample. `gyro_hold_buf` must be the trailing buffer as
    /// of just *before* this sample is appended to it — matching the Python
    /// source's exact ordering, which reads eligibility from the
    /// not-yet-updated buffer to avoid the sample's own ramp-up poisoning
    /// the check that gates it.
    ///
    /// Returns `true` exactly once per detector instance: on the sample that
    /// should be captured as the release point. The caller is responsible
    /// for snapshotting whatever state (AHRS quaternion, flex axis) needs to
    /// be pinned at that instant — this type only tracks *when*, not *what*.
    pub fn on_gyro_sample(&mut self, omega: Vec3, gyro_hold_buf: &SampleBuf, now: f64) -> bool {
        self.began = false;
        if !self.armed {
            return false;
        }
        let omega_mag = norm3(omega);
        let is_calm_sample = omega_mag < ZERO_CAPTURE_GUARD_RAD_S;
        let dt = match self.last_t {
            Some(prev) if now > prev && now - prev <= STALL_MAX_DT_S => now - prev,
            _ => 0.0,
        };
        self.last_t = Some(now);

        // A stalled departure that did not resume in time, or one that has
        // stalled and still not reached the capture threshold long after it
        // began, was handling after all: revoke, as an excursion that settles
        // always did.
        let stall_expired = self.stalled && now - self.stall_t0 > STALL_MAX_S;
        let departure_expired = (self.stalled || (self.pending_departure && self.resumed))
            && now - self.excursion_t0 > STALL_DEPARTURE_MAX_S;
        if stall_expired || departure_expired {
            self.revoke();
        }

        // Earn eligibility only while not already mid-departure — reflects
        // the window as of just before now (see doc comment above).
        if !self.pending_departure && !self.stalled && recently_calm(gyro_hold_buf, now) {
            self.calm_qualified = true;
        }

        if self.stalled && !is_calm_sample {
            // Motion again within the stall window: the same departure if it
            // turns the way the segment was already going, handling if not.
            self.stalled = false;
            if self.same_way(omega, omega_mag) {
                self.pending_departure = true;
                self.resumed = true;
                self.excursion_peak = self.excursion_peak.max(omega_mag);
            } else {
                self.revoke();
            }
        } else if self.pending_departure && self.resumed && !is_calm_sample && !self.same_way(omega, omega_mag) {
            // A resumed departure has to KEEP turning the same way: one sample
            // the right way followed by a burst about another axis is handling.
            self.revoke();
        } else if self.calm_qualified && !self.pending_departure && !is_calm_sample {
            // First above-guard sample after a qualified calm period: a
            // candidate release ramp begins.
            self.pending_departure = true;
            self.excursion_t0 = now;
            self.excursion_peak = omega_mag;
            self.excursion_rot = [0.0; 3];
            self.resumed = false;
            self.began = true;
        } else if self.pending_departure && !is_calm_sample {
            self.excursion_peak = self.excursion_peak.max(omega_mag);
        }

        if self.pending_departure && !is_calm_sample {
            for (r, w) in self.excursion_rot.iter_mut().zip(omega) {
                *r += w * dt;
            }
        }

        if self.pending_departure && is_calm_sample {
            // Settled back down without reaching the capture threshold. Too
            // small and brief to be handling: sensor noise grazing the guard,
            // and the hold stays qualified (see NOISE_EXCURSION_*). Real
            // rotation: held open in case it resumes (see STALL_MAX_S).
            // Otherwise handling, and eligibility is revoked.
            self.pending_departure = false;
            if !is_noise_excursion(self.excursion_peak, now - self.excursion_t0) {
                if norm3(self.excursion_rot).to_degrees() >= STALL_MIN_ROTATION_DEG {
                    self.stalled = true;
                    self.stall_t0 = now;
                } else {
                    self.resumed = false;
                    self.calm_qualified = false;
                }
            }
        }

        if self.pending_departure && omega_mag >= FLEX_CAPTURE_THRESHOLD {
            self.armed = false;
            return true;
        }
        false
    }
}
