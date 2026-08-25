//! Live capture session: accumulates a raw log while reporting the hold state
//! the UI needs, then scores through `replay` when the trial ends.

use crate::calibration::ReleaseDetector;
use crate::replay::{replay, RawSample, ReplayConfig, Sensor, TrialError, TrialResult};
use crate::scoring::{compute_pt_params, PtParams};
use crate::stillness::{SampleBuf, GYRO_BIAS_WINDOW_S, ZERO_CAPTURE_GUARD_RAD_S};

/// Maximum accumulated pose rotation permitted across the pre-release hold.
///
/// **Not calibrated.** 5° is a starting value that would have caught the 8.7°
/// reference capture; one trial is not a calibration. This carries the same
/// status KTD11 gives its attachment-stability and swing-range thresholds:
/// derived from shadow-study data, not chosen here (spec §4.2).
pub const MAX_HOLD_DRIFT_DEG: f64 = 5.0;

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum HoldState {
    /// Rate gate unsatisfied, or drift exceeded and the hold was revoked.
    Moving,
    /// Calm and accumulating. Both figures are for display: a clinician needs
    /// to know *which* gate is unsatisfied, because the corrective action for
    /// motion and for drift are different.
    Holding { calm_s: f64, drift_deg: f64 },
    /// Both gates satisfied; a release now will be trusted.
    Ready,
    /// Release fired.
    Released,
}

pub struct TrialSession {
    cfg: ReplayConfig,
    samples: Vec<RawSample>,
    state: HoldState,
    calm_since: Option<f64>,
    drift: [f64; 3],
    last_gyro_t: Option<f64>,
    /// Live release detection, so the UI can show RELEASED during the swing.
    /// `replay` runs its own detector when scoring; both call the same ported
    /// type, so this duplicates usage, never logic.
    detector: ReleaseDetector,
    gyro_hold: SampleBuf,
}

fn norm3(v: [f64; 3]) -> f64 {
    (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt()
}

impl TrialSession {
    pub fn new(cfg: ReplayConfig) -> Self {
        Self {
            cfg,
            samples: Vec::new(),
            state: HoldState::Moving,
            calm_since: None,
            drift: [0.0; 3],
            last_gyro_t: None,
            detector: ReleaseDetector::new(),
            gyro_hold: Vec::new(),
        }
    }

    pub fn state(&self) -> HoldState {
        self.state
    }

    pub fn sample_count(&self) -> usize {
        self.samples.len()
    }

    pub fn push(&mut self, s: RawSample) {
        if s.sensor == Sensor::Gyro && self.state != HoldState::Released {
            self.advance_hold(s.v, s.t);
        }
        self.samples.push(s);
    }

    fn advance_hold(&mut self, omega: [f64; 3], t: f64) {
        let dt = match self.last_gyro_t {
            Some(prev) if t > prev && t - prev < 0.5 => t - prev,
            _ => 0.0,
        };
        self.last_gyro_t = Some(t);

        // Release detection reads the buffer as of just BEFORE this sample, so
        // a genuine release's own ramp-up cannot poison the calm check gating
        // it. Same ordering contract `replay` observes.
        let fired = self.detector.on_gyro_sample(omega, &self.gyro_hold, t);
        self.gyro_hold.push((t, omega));
        let cutoff = t - GYRO_BIAS_WINDOW_S;
        self.gyro_hold.retain(|(tt, _)| *tt >= cutoff);
        if fired {
            self.state = HoldState::Released;
            return;
        }

        if norm3(omega) >= ZERO_CAPTURE_GUARD_RAD_S {
            self.reset_hold();
            return;
        }

        let start = *self.calm_since.get_or_insert(t);
        // Net vector rotation, not path length: what offsets the zero pose is
        // where the sensor ENDED UP, and opposing wobble genuinely cancels.
        for k in 0..3 {
            self.drift[k] += omega[k] * dt;
        }
        let drift_deg = norm3(self.drift).to_degrees();

        if drift_deg > MAX_HOLD_DRIFT_DEG {
            self.reset_hold();
            return;
        }

        let calm_s = t - start;
        self.state = if calm_s >= 0.95 * GYRO_BIAS_WINDOW_S {
            HoldState::Ready
        } else {
            HoldState::Holding { calm_s, drift_deg }
        };
    }

    fn reset_hold(&mut self) {
        self.state = HoldState::Moving;
        self.calm_since = None;
        self.drift = [0.0; 3];
    }

    /// Score the accumulated log. Consumes nothing — a caller may re-finish
    /// with a different `release_override` to honour a clinician's scrub.
    pub fn finish(&self, release_override: Option<usize>) -> Result<(TrialResult, PtParams), TrialError> {
        let cfg = ReplayConfig { release_override, ..self.cfg };
        let r = replay(&self.samples, &cfg)?;
        let p = compute_pt_params(&r.t, &r.angle_deg, None, false)
            .ok_or(TrialError::InsufficientSamples)?;
        Ok((r, p))
    }
}
