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
/// Returns `None` when the buffer has too few samples to trust — the caller
/// should leave any previously-calibrated bias unchanged in that case,
/// mirroring `calibrate_gyro_bias()`'s "leaves gyro_bias at its previous
/// value" behavior.
pub fn calibrate_gyro_bias(hold_buf: &SampleBuf) -> Option<Vec3> {
    if hold_buf.len() < GYRO_BIAS_MIN_SAMPLES {
        return None;
    }
    Some(mean3(hold_buf))
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
        }

        if self.pending_departure && is_calm_sample {
            // Settled back down without reaching the capture threshold:
            // handling, not a release. Revoke eligibility.
            self.pending_departure = false;
            self.calm_qualified = false;
        }

        if self.pending_departure && omega_mag >= FLEX_CAPTURE_THRESHOLD {
            self.armed = false;
            return true;
        }
        false
    }
}
