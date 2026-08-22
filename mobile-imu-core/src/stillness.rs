//! Stillness detection — ported from `pendulastic_imu_server.py`'s
//! `_is_stationary_window` (bias-calibration-grade stillness) and
//! `imu_calibration_tuner.py`'s `_recently_calm` (looser, release-detection-
//! grade calmness). These are two deliberately different bars, not one
//! reused for both purposes — see each function's doc comment for why.

use crate::ahrs::Vec3;

/// Gyroscope static-bias calibration window. `zero()`/`calibrate_gyro_bias()`
/// fire at the exact moment a stable hold is confirmed, so this trailing
/// window is genuinely motionless when it's read.
pub const GYRO_BIAS_WINDOW_S: f64 = 1.0;
/// Below this many samples the mean is too noisy to trust; keep bias at 0.
pub const GYRO_BIAS_MIN_SAMPLES: usize = 5;

/// Stillness gate for `calibrate_gyro_bias()`: a window only counts as
/// "genuinely still" (not examiner handling) if raw gyro AND raw accel both
/// stay within these peak-to-peak bounds over `GYRO_BIAS_WINDOW_S`. Gyro is
/// the primary/more reliable signal — it separates the "genuinely still" and
/// "likely handling" clusters cleanly; accel is a corroborating check only.
pub const GYRO_STATIONARY_MAX_RAD_S: f64 = 0.9;
pub const ACCEL_STATIONARY_MAX_MPS2: f64 = 0.18;

/// A trailing sample buffer: `(timestamp_seconds, raw_vector)` pairs, oldest
/// first. Callers are responsible for pruning entries older than the window
/// they care about (mirrors the Python source's own buffer maintenance).
pub type SampleBuf = Vec<(f64, Vec3)>;

fn max_axis_peak_to_peak(buf: &SampleBuf) -> f64 {
    let mut min = [f64::INFINITY; 3];
    let mut max = [f64::NEG_INFINITY; 3];
    for (_, v) in buf {
        for axis in 0..3 {
            min[axis] = min[axis].min(v[axis]);
            max[axis] = max[axis].max(v[axis]);
        }
    }
    (0..3)
        .map(|axis| max[axis] - min[axis])
        .fold(f64::NEG_INFINITY, f64::max)
}

/// True iff both buffers span the full `GYRO_BIAS_WINDOW_S` and stay within
/// `GYRO_STATIONARY_MAX_RAD_S` / `ACCEL_STATIONARY_MAX_MPS2` peak-to-peak
/// range — checked per-axis (max over x/y/z of that axis's own peak-to-peak),
/// not on the combined vector magnitude. Magnitude alone would miss a signal
/// that oscillates DIRECTION at roughly constant magnitude (e.g. alternating
/// +0.22/-0.22 rad/s on one axis — exactly what examiner handling looks
/// like): its peak-to-peak magnitude is near zero even though the sensor is
/// clearly moving.
///
/// Pure function of two trailing raw-sample buffers so it can be reused
/// verbatim by both the live capture path and any offline replay.
pub fn is_stationary_window(gyro_buf: &SampleBuf, accel_buf: &SampleBuf, now: f64) -> bool {
    for buf in [gyro_buf, accel_buf] {
        match buf.first() {
            None => return false,
            Some((t0, _)) if (now - t0) < GYRO_BIAS_WINDOW_S * 0.95 => return false,
            _ => {}
        }
    }
    max_axis_peak_to_peak(gyro_buf) < GYRO_STATIONARY_MAX_RAD_S
        && max_axis_peak_to_peak(accel_buf) < ACCEL_STATIONARY_MAX_MPS2
}

/// Zero-orientation capture guard: the raw gyro magnitude for the qualifying
/// role must have stayed below this bound for a full trailing
/// `GYRO_BIAS_WINDOW_S` before a `FLEX_CAPTURE_THRESHOLD` crossing is trusted
/// as the true release, not contamination (examiner still positioning/
/// releasing the sensor as the log starts).
///
/// Deliberately gyro-magnitude-only, not [`is_stationary_window`]'s stricter
/// per-axis gyro+accel peak-to-peak check — that check is tuned for
/// bias-grade stillness and, verified empirically across the full real trial
/// corpus, never fires at all for a meaningful fraction of genuinely fine
/// trials (real accel noise from a handheld/strapped sensor commonly exceeds
/// its 0.18 m/s² bound even at rest), so using it as a hard precondition
/// here would silently drop good trials. 0.3 rad/s (30% of
/// `FLEX_CAPTURE_THRESHOLD`) is empirically derived from the reference
/// corpus, not guessed.
pub const ZERO_CAPTURE_GUARD_RAD_S: f64 = 0.3;

/// True if `gyro_hold_buf` (a trailing raw-gyro buffer, already pruned to
/// `GYRO_BIAS_WINDOW_S` by the caller) spans a full window and every sample
/// in it is below [`ZERO_CAPTURE_GUARD_RAD_S`]. Mirrors
/// [`is_stationary_window`]'s "0.95 * window" full-span requirement (not
/// just "has enough entries") but checks only raw gyro magnitude, not accel.
pub fn recently_calm(gyro_hold_buf: &SampleBuf, now: f64) -> bool {
    let Some((oldest_t, _)) = gyro_hold_buf.first() else {
        return false;
    };
    if now - oldest_t < 0.95 * GYRO_BIAS_WINDOW_S {
        return false;
    }
    gyro_hold_buf
        .iter()
        .all(|(_, v)| norm3(*v) < ZERO_CAPTURE_GUARD_RAD_S)
}

fn norm3(v: Vec3) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}
