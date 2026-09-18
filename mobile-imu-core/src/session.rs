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

/// Continuous post-release stillness required before a trial self-terminates.
///
/// Five seconds because `neutral_deg` is the settled-tail median and
/// `scoring.rs` expresses every angle in the trial relative to it, so a short
/// tail shifts the whole waveform -- including `a0_deg`, the spasticity
/// grouping variable. The opposite failure, an over-long tail fabricating
/// oscillations, is already guarded by `ACTIVE_WINDOW_CAP_SEC`.
///
/// There is deliberately NO maximum trial length. A limb with sustained
/// clonus never reaches this and never self-terminates; the operator ends it.
/// That is a clinical finding, not a fault.
pub const SETTLE_TARGET_S: f64 = 5.0;

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
    /// Post-release stillness held for `SETTLE_TARGET_S`. Terminal: further
    /// samples are still logged, but cannot walk the state back.
    Settled,
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
    settle_since: Option<f64>,
    settle_s: f64,
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
            settle_since: None,
            settle_s: 0.0,
        }
    }

    /// Seconds of continuous post-release stillness accumulated so far. Drives
    /// the settle progress bar; the termination decision itself is made below,
    /// so there is one home for the rule.
    pub fn settle_s(&self) -> f64 {
        self.settle_s
    }

    pub fn state(&self) -> HoldState {
        self.state
    }

    pub fn sample_count(&self) -> usize {
        self.samples.len()
    }

    /// The accumulated raw log, in push order. `export_jsonl::export_jsonl`
    /// formats exactly this slice -- accel-before-gyro at each timestamp
    /// holds here because `push` (below) is the only thing that ever
    /// appends, and every caller pushes accel then gyro per sample.
    pub fn samples(&self) -> &[RawSample] {
        &self.samples
    }

    pub fn push(&mut self, s: RawSample) {
        if s.sensor == Sensor::Gyro {
            match self.state {
                // Post-release: accumulate stillness toward self-termination.
                HoldState::Released => self.advance_settle(s.v, s.t),
                // Terminal. Samples are still logged below -- the log is the
                // archive -- but no state transition can follow.
                HoldState::Settled => {}
                _ => self.advance_hold(s.v, s.t),
            }
        }
        self.samples.push(s);
    }

    /// Post-release settling. Deliberately the same shape as `advance_hold`:
    /// accumulate while calm, reset to zero on movement, so `TrialSession` has
    /// one settling idiom rather than two.
    ///
    /// Gates on the per-sample gyro magnitude against
    /// `ZERO_CAPTURE_GUARD_RAD_S` -- the same bound and the same shape
    /// `advance_hold` uses -- and NOT on `is_stationary_window`. That stricter
    /// gyro+accel bound is documented in `stillness.rs` as never firing for a
    /// meaningful fraction of genuinely fine trials, because real accel noise
    /// from a strapped sensor exceeds its 0.18 m/s² bound even at rest. Since
    /// there is no maximum trial length, using it here would leave those
    /// trials recording indefinitely.
    ///
    /// Per-sample rather than `recently_calm`'s trailing window, deliberately.
    /// `recently_calm` reports false until it has 0.95 s of history, so
    /// starting the accumulator from it would have measured 0.95 + 5.0 = 5.95 s
    /// of stillness while claiming five. The requirement is "not moving for
    /// five seconds", so the clock starts at the first still sample. Requiring
    /// five CONTINUOUS seconds already supplies the noise robustness a trailing
    /// window would have added, which is why no buffer is needed here.
    fn advance_settle(&mut self, omega: [f64; 3], t: f64) {
        if norm3(omega) >= ZERO_CAPTURE_GUARD_RAD_S {
            self.settle_since = None;
            self.settle_s = 0.0;
            return;
        }

        let start = *self.settle_since.get_or_insert(t);
        self.settle_s = t - start;
        if self.settle_s >= SETTLE_TARGET_S {
            self.state = HoldState::Settled;
        }
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
        self.finish_with(release_override, false)
    }

    /// `finish`, choosing the drift-correction convention explicitly.
    ///
    /// The repo has two, and they disagree: the live view
    /// (`pendulastic_app.py:1793`) scores with `detrend=false` because its own
    /// truthfulness gate treats the raw IMU signal as authoritative, while the
    /// analysis path (`pt_report_common` -> `run_pt_analysis`) uses the
    /// `detrend=True` default. Measured across 197 real trials the two disagree
    /// on the MAS grade for 63 of them (32%), so which one a number came from
    /// is not a detail.
    ///
    /// Neither is wrong, and this deliberately does not pick: `finish` keeps the
    /// live convention so the on-screen estimate matches the capture app, and a
    /// caller persisting a trial asks for `true` so the stored and exported
    /// numbers match the cohort reports they will be compared against.
    pub fn finish_with(
        &self,
        release_override: Option<usize>,
        detrend: bool,
    ) -> Result<(TrialResult, PtParams), TrialError> {
        let cfg = ReplayConfig { release_override, ..self.cfg };
        let r = replay(&self.samples, &cfg)?;
        let p = compute_pt_params(&r.t, &r.angle_deg, None, detrend)
            .ok_or(TrialError::InsufficientSamples)?;
        Ok((r, p))
    }
}
