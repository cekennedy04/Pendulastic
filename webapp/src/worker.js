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

// Worker entry point. Kept separate from createSession so tests can drive the
// session directly without a Worker host.
if (typeof self !== 'undefined' && typeof self.postMessage === 'function') {
  let session = null;
  self.onmessage = async (e) => {
    const m = e.data;
    if (m.type === 'start') {
      session = await createSession(m.cfg);
      self.postMessage({ type: 'state', ...session.state() });
    } else if (m.type === 'batch') {
      session.pushBatch(new Float64Array(m.buf));
      self.postMessage({ type: 'state', ...session.state() });
    } else if (m.type === 'finish') {
      const params = session.finish();
      self.postMessage(params ? { type: 'result', params }
                              : { type: 'error', reason: 'unscorable' });
    }
  };
}
