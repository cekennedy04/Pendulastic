//! `#[wasm_bindgen]` veneer. Deliberately logic-free: anything implemented
//! here is invisible to `cargo test` and therefore unverified. All behaviour
//! lives in `session`/`replay`/`params_json`, which are pure Rust and
//! covered.

use wasm_bindgen::prelude::*;

use crate::params_json;
use crate::pt_score::{pt_score_to_json, HEALTHY_REF};
use crate::replay::{RawSample, ReplayConfig, Sensor};
use crate::session::{HoldState, TrialSession};
use crate::trajectory_json;

#[wasm_bindgen]
pub struct WasmSession {
    inner: TrialSession,
}

#[wasm_bindgen]
impl WasmSession {
    #[wasm_bindgen(constructor)]
    pub fn new(beta: f64, ema_alpha: f64) -> WasmSession {
        let cfg = ReplayConfig { beta, ema_alpha, ..ReplayConfig::default() };
        WasmSession { inner: TrialSession::new(cfg) }
    }

    /// Flat batch, 7 doubles per sample: `[t_ms, ax, ay, az, gx, gy, gz]`.
    /// Accel is pushed before gyro for each sample, matching the ordering
    /// contract the whole pipeline depends on. Gyro must already be rad/s.
    pub fn push_batch(&mut self, buf: &[f64]) {
        for c in buf.chunks_exact(7) {
            let t = c[0] / 1000.0;
            let ts_ms = c[0].round() as i64;
            self.inner.push(RawSample { t, ts_ms, sensor: Sensor::Accel, v: [c[1], c[2], c[3]] });
            self.inner.push(RawSample { t, ts_ms, sensor: Sensor::Gyro, v: [c[4], c[5], c[6]] });
        }
    }

    /// 0 Moving, 1 Holding, 2 Ready, 3 Released.
    pub fn state_code(&self) -> u8 {
        match self.inner.state() {
            HoldState::Moving => 0,
            HoldState::Holding { .. } => 1,
            HoldState::Ready => 2,
            HoldState::Released => 3,
        }
    }

    pub fn calm_s(&self) -> f64 {
        match self.inner.state() {
            HoldState::Holding { calm_s, .. } => calm_s,
            HoldState::Ready => 0.95,
            _ => 0.0,
        }
    }

    pub fn drift_deg(&self) -> f64 {
        match self.inner.state() {
            HoldState::Holding { drift_deg, .. } => drift_deg,
            _ => 0.0,
        }
    }

    pub fn sample_count(&self) -> usize {
        self.inner.sample_count()
    }

    /// JSON of the full `PtParams` payload, or `undefined` when unscorable.
    /// Returning `undefined` rather than throwing keeps `TrialError` a value
    /// the UI branches on, per KTD7 (never a panic across the boundary).
    pub fn finish(&self) -> Option<String> {
        let (_, p) = self.inner.finish(None).ok()?;
        Some(params_json::params_to_json(&p))
    }

    /// JSON of the full trial waveform — tick series, release point, and the
    /// accepted peaks/troughs — for the result-screen plot. `undefined` when
    /// unscorable, same contract as `finish`. Deliberately a separate call
    /// rather than added fields on `finish`'s payload: `finish`'s JSON key
    /// set is pinned at exactly 20 scalars by `tests/params_json_test.rs`.
    pub fn finish_trajectory(&self) -> Option<String> {
        let (r, p) = self.inner.finish(None).ok()?;
        Some(trajectory_json::trajectory_to_json(&r, &p))
    }

    /// JSON of the composite Popović PT score: `{score, zone, breakdown}`,
    /// scored against the current `HEALTHY_REF`. `undefined` when unscorable,
    /// same contract as `finish`/`finish_trajectory`.
    ///
    /// Deliberately a separate call, computed at read time from `finish`'s
    /// own `PtParams` rather than folded into `finish`'s JSON or persisted
    /// anywhere: `HEALTHY_REF` is still being recalibrated (three times in
    /// one week as of this writing, with validation task V0.4 still to come),
    /// so a stored composite would go stale the next time it moves. This
    /// score is PROVISIONAL — see `pt_score`'s module doc and the "research
    /// capture only" banner the UI always shows alongside it.
    pub fn finish_pt_score(&self) -> Option<String> {
        let (_, p) = self.inner.finish(None).ok()?;
        Some(pt_score_to_json(&p, &HEALTHY_REF))
    }
}
