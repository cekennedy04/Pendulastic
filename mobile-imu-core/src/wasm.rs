//! `#[wasm_bindgen]` veneer. Deliberately logic-free: anything implemented
//! here is invisible to `cargo test` and therefore unverified. All behaviour
//! lives in `session`/`replay`/`params_json`, which are pure Rust and
//! covered.

use wasm_bindgen::prelude::*;

use crate::export_jsonl;
use crate::params_json;
use crate::pt_score::{pt_scalars, pt_score_to_json, PtScalars, HEALTHY_REF};
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

    /// 0 Moving, 1 Holding, 2 Ready, 3 Released, 4 Settled (terminal).
    pub fn state_code(&self) -> u8 {
        match self.inner.state() {
            HoldState::Moving => 0,
            HoldState::Holding { .. } => 1,
            HoldState::Ready => 2,
            HoldState::Released => 3,
            HoldState::Settled => 4,
        }
    }

    /// Seconds of post-release stillness accumulated toward SETTLE_TARGET_S;
    /// freezes once Settled. Drives the settle countdown and the auto-stop.
    pub fn settle_s(&self) -> f64 {
        self.inner.settle_s()
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
    /// set is pinned at exactly 21 scalars by `tests/params_json_test.rs`.
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

    /// Newline-delimited JSON of the raw accel/gyro/mag log, in the exact
    /// wire format `tests/test_web_export_contract.py` (repo root) pins as
    /// the contract into `imu_calibration_tuner.replay_trial` -- this is
    /// what lets a trial captured on the phone be replayed through the
    /// Python reference on a laptop. Pure delegation to
    /// `export_jsonl::export_jsonl`, per this module's own logic-free
    /// contract: all the formatting behaviour lives there, where
    /// `cargo test` can reach it. Available before AND after `finish` --
    /// `TrialSession::finish` takes `&self`, so scoring never consumes the
    /// raw log.
    /// Params under the ANALYSIS convention (`detrend=true`), for the record
    /// that gets stored and exported. `finish` above stays on the live
    /// convention so the on-screen number matches the desktop capture app;
    /// these two deliberately differ, and `TrialSession::finish_with` explains
    /// why picking one globally is not available.
    pub fn finish_detrended(&self) -> Option<String> {
        let (_, p) = self.inner.finish_with(None, true).ok()?;
        Some(params_json::params_to_json(&p))
    }

    /// The composite score under the analysis convention — the number that must
    /// agree with the cohort reports.
    pub fn finish_pt_score_detrended(&self) -> Option<String> {
        let (_, p) = self.inner.finish_with(None, true).ok()?;
        Some(pt_score_to_json(&p, &HEALTHY_REF))
    }

    /// The same numbers as `finish_pt_score`, as scalars rather than JSON.
    ///
    /// Provided for BOTH conventions, mirroring the two JSON accessors above,
    /// because a caller that can only reach one would have to choose between
    /// the on-screen number and the one the cohort reports agree with -- and a
    /// longitudinal view that mixed them would be comparing incomparable
    /// values. `webapp/src/session-store.js` persists `drift_correction` for
    /// exactly this reason: the two disagree on the MAS grade for 63 of 197
    /// real trials.
    pub fn finish_pt_scalars(&self) -> Option<WasmPtScalars> {
        let (_, p) = self.inner.finish(None).ok()?;
        Some(WasmPtScalars { inner: pt_scalars(&p, &HEALTHY_REF) })
    }

    /// Scalars under the analysis convention (`detrend=true`).
    pub fn finish_pt_scalars_detrended(&self) -> Option<WasmPtScalars> {
        let (_, p) = self.inner.finish_with(None, true).ok()?;
        Some(WasmPtScalars { inner: pt_scalars(&p, &HEALTHY_REF) })
    }

    pub fn export_jsonl(&self) -> String {
        export_jsonl::export_jsonl(self.inner.samples())
    }
}


/// [`PtScalars`] across the wasm boundary: getters only, no arithmetic.
///
/// Exists because `pt_score_to_json` hands JS a string. That is right for one
/// trial -- the capture screen parses it once -- and wrong for a longitudinal
/// view, which redraws every trial a patient has and would parse a string per
/// point to reach seven floats. Getters return `f64` directly.
///
/// `healthy_ref_id` is on this object rather than a separate export, so a
/// caller cannot pair this run's scores with a previously fetched reference.
/// The score and the reference it was computed against are one fact; the
/// composite score is not persisted with a trial (see
/// webapp/src/session-store.js: "derived at read time because HEALTHY_REF
/// moves"), so this stamp is the only thing that says which reference a
/// redrawn history was scored against.
#[wasm_bindgen]
pub struct WasmPtScalars {
    inner: PtScalars,
}

#[wasm_bindgen]
impl WasmPtScalars {
    #[wasm_bindgen(getter)]
    pub fn r2n(&self) -> f64 { self.inner.r2n }
    #[wasm_bindgen(getter)]
    pub fn n(&self) -> f64 { self.inner.n }
    #[wasm_bindgen(getter)]
    pub fn phi_max_ratio(&self) -> f64 { self.inner.phi_max_ratio }
    #[wasm_bindgen(getter)]
    pub fn omega_max_n(&self) -> f64 { self.inner.omega_max_n }
    #[wasm_bindgen(getter)]
    pub fn omega_min_n(&self) -> f64 { self.inner.omega_min_n }
    #[wasm_bindgen(getter)]
    pub fn f(&self) -> f64 { self.inner.f }
    #[wasm_bindgen(getter)]
    pub fn area_ratio(&self) -> f64 { self.inner.area_ratio }
    #[wasm_bindgen(getter)]
    pub fn total(&self) -> f64 { self.inner.total }
    #[wasm_bindgen(getter)]
    pub fn simple(&self) -> f64 { self.inner.simple }
    #[wasm_bindgen(getter)]
    pub fn unmeasured_count(&self) -> u32 { self.inner.unmeasured_count }
    /// Identity of the [`HEALTHY_REF`] these were scored against. A `String`
    /// crosses the boundary as a JS string; it is 12 hex chars, and the one
    /// non-numeric field here on purpose -- see the struct doc.
    #[wasm_bindgen(getter)]
    pub fn healthy_ref_id(&self) -> String { self.inner.healthy_ref_id.clone() }
}


/// Score a STORED trial's params against the current [`HEALTHY_REF`].
///
/// This is what makes a longitudinal view possible. The composite score is not
/// persisted with a trial -- `webapp/src/session-store.js`: "derived at read
/// time because HEALTHY_REF moves" -- so a trend line has to recompute every
/// point it draws. The alternative, replaying each trial's raw log through the
/// AHRS, costs seconds per trial and is not available to a view that redraws on
/// load.
///
/// Takes the JSON `params_to_json` wrote, which is exactly what the trial
/// record stores. Returns `None` if the record cannot be parsed rather than
/// scoring a partially-understood one: `params_from_json` requires every field,
/// because a defaulted `first_trough_depth` would mark `r2n` and
/// `phi_max_ratio` unmeasured on a trial that measured them.
///
/// The returned scalars carry `healthy_ref_id`, so a caller can tell a point
/// recomputed against today's reference from one recorded under an older one --
/// which is the whole reason a redrawn history is honest rather than merely
/// consistent.
#[wasm_bindgen]
pub fn pt_scalars_from_params(params_json_str: &str) -> Option<WasmPtScalars> {
    let p = params_json::params_from_json(params_json_str).ok()?;
    Some(WasmPtScalars { inner: pt_scalars(&p, &HEALTHY_REF) })
}

/// Identity of the reference `pt_scalars_from_params` scores against.
///
/// Exposed so a caller can ask "which reference is current?" without scoring a
/// trial first -- a trend view needs it to label the axis and to decide which
/// stored points were recorded under a different one.
#[wasm_bindgen]
pub fn active_healthy_ref_id() -> String {
    crate::pt_score::healthy_ref_id(&HEALTHY_REF)
}
