// Owns the WASM instance. Accepts batches from the live listener or from a
// recorded fixture through the same entry point -- that seam is the only way
// to test capture automatically, because no driver can synthesise a real
// DeviceMotionEvent in Safari.
//
// `init()` (generated with `--target web`) fetches a URL by default, which
// has no meaning under `node --test`. `createSession` therefore accepts an
// optional `wasmSource`: the browser worker calls it with none and lets
// `init()` fetch `mobile_imu_core_bg.wasm` relative to this module as usual;
// the Node test suite passes the wasm bytes directly (read from disk) so the
// exact artifact that ships is also the one under test, without a second
// `--target nodejs` build.
import init, { WasmSession } from './wasm/mobile_imu_core.js';
// Namespace import so a wasm build that predates `healthy_reference` still
// LOADS. A named static import of a missing export is a module-level
// SyntaxError, which would take the whole worker down -- every capture,
// not just the comparison table.
import * as wasmExports from './wasm/mobile_imu_core.js';

let ready = null;

// Which formula and which replay footing the loaded wasm scores with, so a
// stored record names them from the binary that produced it rather than from
// a JS copy. null for a wasm build that predates the exports (crate < 0.4.0):
// such a build scored on the old app footing, and a reader must not guess.
export function scoringProvenance(exports = wasmExports) {
  // try/catch too: new glue over a stale cached .wasm binary throws on the
  // call, and that must cost the provenance, never the trial's result.
  const read = (name) => {
    try { return typeof exports[name] === 'function' ? exports[name]() : null; } catch { return null; }
  };
  return { scoreFormulaId: read('score_formula_id'), scoringFooting: read('scoring_footing') };
}

export async function createSession({ beta, emaAlpha, wasmSource }) {
  // The positional `init(wasmSource)` form is deprecated by wasm-bindgen (it
  // warns on every run) and is slated for removal; the object form is the
  // supported spelling. `{module_or_path: undefined}` still takes the
  // browser's default path -- the generated `__wbg_init` destructures the
  // object first and then falls back to fetching `mobile_imu_core_bg.wasm`
  // relative to the module -- so the no-argument browser call keeps working.
  ready ??= init({ module_or_path: wasmSource });
  await ready;
  const inner = new WasmSession(beta, emaAlpha);
  return {
    pushBatch: (buf) => inner.push_batch(buf),
    state: () => ({
      code: inner.state_code(),
      calm_s: inner.calm_s(),
      drift_deg: inner.drift_deg(),
      settle_s: inner.settle_s(),
    }),
    finish: () => {
      const json = inner.finish();
      return json === undefined ? undefined : JSON.parse(json);
    },
    // The full tick series, release point, and accepted peaks/troughs for
    // the result-screen plot -- a separate wasm call from `finish` because
    // `finish`'s JSON key set is pinned at exactly 20 scalars
    // (mobile-imu-core/tests/params_json_test.rs). Same undefined-on-
    // unscorable contract as `finish`.
    finishTrajectory: () => {
      const json = inner.finish_trajectory();
      return json === undefined ? undefined : JSON.parse(json);
    },
    // The composite Popović PT score -- `{score, zone, breakdown}` -- derived
    // at read time from the same underlying finish() computation, never
    // persisted (mobile-imu-core/src/pt_score.rs's module doc: HEALTHY_REF is
    // still being recalibrated). A separate wasm call for the same reason
    // finishTrajectory is: it keeps `finish`'s pinned 20-key payload
    // untouched.
    finishPtScore: () => {
      const json = inner.finish_pt_score();
      return json === undefined ? undefined : JSON.parse(json);
    },
    // Capture quality: how much of the trial happened OUT of the flexion
    // plane. Parses to `null` when it could not be measured -- no axis
    // committed, or nothing above the rate threshold -- which is NOT the
    // same as {lateral_fraction: 0} ("measured, and perfectly planar").
    // Older wasm builds lack this export, so the call is guarded: a stale
    // cached worker must degrade to "not measured", not throw on finish.
    lateralMotion: () => {
      if (typeof inner.lateral_motion !== "function") return null;
      return JSON.parse(inner.lateral_motion());
    },
    // Capture quality: how the scored replay's flexion-axis fit ended and
    // what it left out ({n_fit, closed, late_n, late_energy_ratio,
    // max_span_s, flags: [...]}). Flags only: it changes no angle and no
    // score. `null` = the core had no report (the fit took no sample);
    // `undefined` = an older wasm without the export, which the record keeps
    // distinct from "reported, no flags".
    axisFit: () => {
      if (typeof inner.axis_fit !== 'function') return undefined;
      return JSON.parse(inner.axis_fit());
    },
    // Newline-delimited JSON of the raw accel/gyro/mag log (mobile-imu-core's
    // export_jsonl(), the contract `tests/test_web_export_contract.py` pins).
    // Unlike `finish`/`finishTrajectory`/`finishPtScore`, this has no
    // undefined-on-unscorable case: `TrialSession::finish` takes `&self`, so
    // the raw log survives scoring and this is available whether or not the
    // trial was scorable at all -- an operator debugging exactly the "why
    // didn't this score" question needs the log most when scoring failed.
    // The ANALYSIS convention (detrend=true). `finish`/`finishPtScore` above
    // stay on the LIVE convention, matching pendulastic_app.py's live view;
    // these match pt_report_common -> run_pt_analysis, which is what the
    // cohort reports are built from. The two disagree on the MAS grade for
    // 63 of 197 real trials, so the stored record uses these and the
    // on-screen estimate uses those -- see TrialSession::finish_with.
    // Feature-detected, not assumed. A service worker can serve new JS against
    // a wasm binary already in the cache, and an unconditional call would then
    // throw inside the finish handler and take the WHOLE result down -- the
    // trial would read as unscorable rather than merely un-detrended. Degrading
    // to undefined keeps the live result intact and costs only the analysis
    // convention, which is the strictly better failure.
    finishDetrended: () => {
      if (typeof inner.finish_detrended !== 'function') return undefined;
      const json = inner.finish_detrended();
      return json === undefined ? undefined : JSON.parse(json);
    },
    // Why the analysis convention produced nothing, when it did not: a stable
    // code from the core ("insufficient_samples", "release_never_detected"),
    // null when it scored, and 'not_available' when this wasm build predates
    // the export -- so a stored record can tell an unscorable analysis pass
    // from a stale cached wasm.
    detrendedRefusal: () => {
      if (typeof inner.refusal_reason !== 'function') return 'not_available';
      return inner.refusal_reason(true) ?? null;
    },
    // How a convention's score was reached: {drift_correction,
    // drift_slope_deg_s, drift_correction_rejected, quality_flags}. Neither
    // drift bound nor the A0 bound refuses a trial; they skip a correction and
    // flag it, and this is where the flag travels. null when unscorable or when
    // this wasm build predates the export (feature-detected, as above).
    scoringQuality: (detrend) => {
      if (typeof inner.scoring_quality !== 'function') return null;
      const json = inner.scoring_quality(Boolean(detrend));
      return json === undefined ? null : JSON.parse(json);
    },
    finishPtScoreDetrended: () => {
      if (typeof inner.finish_pt_score_detrended !== 'function') return undefined;
      const json = inner.finish_pt_score_detrended();
      return json === undefined ? undefined : JSON.parse(json);
    },
    exportJsonl: () => inner.export_jsonl(),
  };
}

// Worker message handler, factored out of the `self.onmessage` binding so
// tests can drive the protocol directly without a real Worker host (`self`
// does not exist on Node's main thread, and worker_threads' global scope has
// no `self`/`postMessage` either -- both are Web Worker-only globals).
//
// Errors are values across this boundary, never a silent hang -- the same
// discipline `mobile-imu-core/src/wasm.rs` already holds at the wasm/JS
// edge, carried into the worker's own JS. `{type:'error',reason:'unscorable'}`
// is reserved for the one legitimate no-result case (`finish()` returned
// `undefined`); every other throw -- a message arriving out of order, a
// malformed `cfg`, a `JSON.parse` failure -- is caught and posted back as
// `{type:'error', reason: <message>}` instead of leaving the caller waiting
// on a response that will never come.
export function createWorkerHandler() {
  let session = null;
  // The promise created by the `start` branch, retained so later messages can
  // wait on it. `start` is genuinely slow -- it fetches and instantiates a
  // ~134 KB wasm module -- while `capture.js` begins its 50 ms flush interval
  // the instant it has posted `start`. Without this, the first batch of every
  // cold load lands while `start` is still suspended, hits the
  // "before start" guard, and `app.js` latches that as a genuine fault: the
  // clinician's first trial fails deterministically, not intermittently.
  //
  // `starting === null` therefore means something different from
  // `session === null`, and the difference is exactly what must be preserved:
  //   - no `start` was ever sent  -> `starting` is null, `await null` is a
  //     no-op, `session` is still null, and the guard below fires. That is a
  //     real protocol violation and must stay an error.
  //   - `start` is still in flight -> `starting` is a pending promise, the
  //     message waits for it, `session` is set by the time the guard runs,
  //     and the batch is processed normally.
  let starting = null;
  return async function handle(m, post) {
    try {
      if (m.type === 'start') {
        starting = createSession(m.cfg);
        session = await starting;
        post({ type: 'state', ...session.state() });
      } else if (m.type === 'batch') {
        await starting;
        if (!session) throw new Error('batch received before start');
        session.pushBatch(new Float64Array(m.buf));
        post({ type: 'state', ...session.state() });
      } else if (m.type === 'finish') {
        await starting;
        if (!session) throw new Error('finish received before start');
        const params = session.finish();
        // `{type:'result', params}` is the existing, still-supported shape;
        // `trajectory` and `ptScore` ride alongside it. All three come from
        // the same underlying finish() computation in the Rust core, so a
        // scorable `params` implies both are defined -- but the fallback to
        // `null` keeps the message well-formed even if that ever stops
        // being true (structured-clone would otherwise just drop an
        // `undefined` property, which is harder to notice on the far end).
        post(params
          ? { type: 'result', params, trajectory: session.finishTrajectory() ?? null,
              ptScore: session.finishPtScore() ?? null,
              // Analysis-convention pair, for the stored/exported record.
              // Null rather than absent when unavailable, so the far end can
              // tell "not computed" from "dropped by structured clone".
              paramsDetrended: session.finishDetrended() ?? null,
              ptScoreDetrended: session.finishPtScoreDetrended() ?? null,
              // Why paramsDetrended is null, when it is (null when it scored).
              detrendedRefusal: session.detrendedRefusal(),
              // How each convention's score was reached (flags, correction).
              scoringQuality: session.scoringQuality(false),
              scoringQualityDetrended: session.scoringQuality(true),
              // Null here means NOT MEASURED, and the record keeps that
              // distinction rather than defaulting it to zero.
              lateralMotion: session.lateralMotion(),
              // undefined = an older wasm without the export. The far end
              // carries it as undefined and the record omits the key.
              axisFit: session.axisFit(),
              provenance: scoringProvenance() }
          : { type: 'error', reason: 'unscorable', request: 'finish' });
      } else if (m.type === 'reference') {
        // The healthy reference the result screen shows BESIDE each measured
        // value. A message rather than a constant copied into the UI: the
        // numbers live in mobile-imu-core next to their provenance note, and
        // a second copy here would go stale the next time HEALTHY_REF moves
        // -- which it has, three times in one week.
        // Tolerant on purpose: a reference request can arrive before any
        // session has initialised the wasm module. null means 'not
        // available', and the UI hides the comparison rather than showing
        // seven rows of dashes.
        await starting;
        let payload = null;
        try {
          payload = typeof wasmExports.healthy_reference === 'function'
            ? JSON.parse(wasmExports.healthy_reference())
            : null;
        } catch { payload = null; }
        post({ type: 'reference', payload });
      } else if (m.type === 'export') {
        // Deliberately does not `await starting`+require a result the way
        // `finish` does: the raw log is what an operator needs to diagnose
        // *why* a trial didn't score, so export must work on an unscorable
        // trial too. It only requires that a session exists at all.
        await starting;
        if (!session) throw new Error('export received before start');
        post({ type: 'exportResult', jsonl: session.exportJsonl() });
      }
    } catch (err) {
      // `request` names the message that failed, so capture.js can route the
      // error to THAT request's consumer. Without it, an error from `finish`
      // could settle a pending `export` (the research-recorder data loss
      // capture.js's createReplyRouter documents).
      post({ type: 'error', reason: err instanceof Error ? err.message : String(err),
             request: m && typeof m.type === 'string' ? m.type : 'unknown' });
    }
  };
}

// Worker entry point: a thin adapter binding the handler above to the real
// Web Worker globals.
if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
  const handle = createWorkerHandler();
  self.onmessage = (e) => handle(e.data, (msg) => self.postMessage(msg));
}
