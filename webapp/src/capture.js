// Runs on the MAIN THREAD, and must: DeviceMotionEvent is exposed only on
// window, and requestPermission() must be called from a user gesture on iOS.
// The handler stays minimal so UI work never delays or drops sensor events.
//
// The module is split in two: `encodeSample` below is pure (no DOM, no
// worker, no globals) so the deg/s->rad/s conversion and the beta/gamma/alpha
// -> x/y/z axis mapping -- the highest-risk arithmetic in this file -- can be
// unit tested under `node --test` (see tests/capture.test.js). Everything
// past it is browser-only plumbing (permission prompt, devicemotion
// listener, wake lock, worker construction/transfer) that no test runner can
// exercise without a real device, and is left untested per task-5 dispatch.

const BATCH_MS = 50;          // matches TICK_S; the production _IMU_PAGE cadence
const FLOATS_PER_SAMPLE = 7;  // [t_ms, ax, ay, az, gx, gy, gz]
const CAP = 64;               // 50ms at 60Hz is ~3 samples; ample headroom
const DEG2RAD = Math.PI / 180;

// Pure: encodes one motion event into the 7-float wire layout the worker
// expects, writing into `out` starting at `offset`. Takes only the plain
// values a DeviceMotionEvent carries (event.timeStamp,
// event.accelerationIncludingGravity, event.rotationRate) -- not the event
// class itself -- so it can be driven with a plain object in tests.
//
// event.timeStamp is stamped at event CREATION by the browser, so it
// survives main-thread contention. Using a handler-time clock
// (Date.now()/performance.now()) instead is what collapsed dt to zero in the
// 2026-08-17 defect; callers must pass event.timeStamp through unchanged.
//
// Axis mapping: beta->x, gamma->y, alpha->z. The browser reports
// rotationRate in deg/s; the worker/wasm core expects rad/s.
export function encodeSample(out, offset, event) {
  const a = event.accelerationIncludingGravity;
  const r = event.rotationRate;
  out[offset] = event.timeStamp;
  out[offset + 1] = a.x;
  out[offset + 2] = a.y;
  out[offset + 3] = a.z;
  out[offset + 4] = r.beta * DEG2RAD;
  out[offset + 5] = r.gamma * DEG2RAD;
  out[offset + 6] = r.alpha * DEG2RAD;
  return out;
}

export async function startCapture({ onState, onResult, onError }) {
  if (typeof DeviceMotionEvent === 'undefined') {
    onError('This browser does not expose motion sensors.');
    return { stop() {} };
  }
  if (typeof DeviceMotionEvent.requestPermission === 'function') {
    const granted = await DeviceMotionEvent.requestPermission();
    if (granted !== 'granted') {
      onError('Motion permission denied. Reload the tab and tap Start to retry.');
      return { stop() {} };
    }
  }

  let wakeLock = null;
  try { wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* best effort */ }

  const worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === 'state') onState(m);
    else if (m.type === 'result') onResult(m.params);
    else if (m.type === 'error') onError(m.reason);
  };
  worker.postMessage({ type: 'start', cfg: { beta: 0.041, emaAlpha: 0.3 } });

  let buf = new Float64Array(CAP * FLOATS_PER_SAMPLE);
  let n = 0;

  const onMotion = (event) => {
    const a = event.accelerationIncludingGravity;
    const r = event.rotationRate;
    if (!a || a.x === null || !r || r.beta === null) return;
    if (n >= CAP) return;                    // dropped rather than reallocating mid-handler
    encodeSample(buf, n * FLOATS_PER_SAMPLE, event);
    n++;
  };

  const flush = () => {
    if (n === 0) return;
    const out = buf.subarray(0, n * FLOATS_PER_SAMPLE).slice();
    n = 0;
    // Transfer ownership -- no copy, and no COOP/COEP isolation required.
    // `out.buffer` is detached by this call; it must not be read again.
    worker.postMessage({ type: 'batch', buf: out.buffer }, [out.buffer]);
  };

  window.addEventListener('devicemotion', onMotion);
  const timer = setInterval(flush, BATCH_MS);

  return {
    stop() {
      clearInterval(timer);
      window.removeEventListener('devicemotion', onMotion);
      flush();
      worker.postMessage({ type: 'finish' });
      wakeLock?.release?.().catch(() => {});
    },
  };
}
