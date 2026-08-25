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
  ready ??= init(wasmSource);
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
  return async function handle(m, post) {
    try {
      if (m.type === 'start') {
        session = await createSession(m.cfg);
        post({ type: 'state', ...session.state() });
      } else if (m.type === 'batch') {
        if (!session) throw new Error('batch received before start');
        session.pushBatch(new Float64Array(m.buf));
        post({ type: 'state', ...session.state() });
      } else if (m.type === 'finish') {
        if (!session) throw new Error('finish received before start');
        const params = session.finish();
        post(params ? { type: 'result', params }
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
