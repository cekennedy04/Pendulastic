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

let ready = null;

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
              ptScore: session.finishPtScore() ?? null }
          : { type: 'error', reason: 'unscorable' });
      }
    } catch (err) {
      post({ type: 'error', reason: err instanceof Error ? err.message : String(err) });
    }
  };
}

// Worker entry point: a thin adapter binding the handler above to the real
// Web Worker globals.
if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
  const handle = createWorkerHandler();
  self.onmessage = (e) => handle(e.data, (msg) => self.postMessage(msg));
}
