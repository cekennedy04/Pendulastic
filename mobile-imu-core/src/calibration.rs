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

/// Two-state zero-capture eligibility machine (KTD9). `calm_qualified`: a
/// genuine trailing-window calm period has been confirmed and not yet spent.
/// `pending_departure`: an above-guard excursion is currently in progress
/// following a qualified calm period — only while this is true is a
/// [`FLEX_CAPTURE_THRESHOLD`] crossing trusted as the true release. If that
/// excursion settles back below guard before ever reaching the capture
/// threshold, it's treated as handling, not a release: both flags reset and
/// a fresh full calm window is required before the next excursion can be
/// trusted.
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
            armed: true,
        }
    }

    /// True until a release has been captured; once released, further
    /// samples are ignored (see [`Self::on_gyro_sample`]).
    pub fn is_armed(&self) -> bool {
        self.armed
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
        if !self.armed {
            return false;
        }
        let omega_mag = norm3(omega);
        let is_calm_sample = omega_mag < ZERO_CAPTURE_GUARD_RAD_S;

        // Earn eligibility only while not already mid-departure — reflects
        // the window as of just before now (see doc comment above).
        if !self.pending_departure && recently_calm(gyro_hold_buf, now) {
            self.calm_qualified = true;
        }

        if self.calm_qualified && !self.pending_departure && !is_calm_sample {
            // First above-guard sample after a qualified calm period: a
            // candidate release ramp begins.
            self.pending_departure = true;
            self.excursion_t0 = now;
            self.excursion_peak = omega_mag;
        } else if self.pending_departure && !is_calm_sample {
            self.excursion_peak = self.excursion_peak.max(omega_mag);
        }

        if self.pending_departure && is_calm_sample {
            // Settled back down without reaching the capture threshold. If it
            // was handling, revoke eligibility; if it was too small and brief
            // to be handling, it is sensor noise grazing the guard and the
            // hold stays qualified (see NOISE_EXCURSION_*).
            self.pending_departure = false;
            if !is_noise_excursion(self.excursion_peak, now - self.excursion_t0) {
                self.calm_qualified = false;
            }
        }

        if self.pending_departure && omega_mag >= FLEX_CAPTURE_THRESHOLD {
            self.armed = false;
            return true;
        }
        false
    }
}
