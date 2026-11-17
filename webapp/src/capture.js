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

// The handle returned by startCapture's two early exits (no motion sensors,
// permission denied). It must be SHAPE-COMPATIBLE with the real handle
// below, not merely "something with a stop()".
//
// Why: app.js's onResult/onError keep the finished handle in `exportSession`
// and later call `exportSession.exportJsonl()` inside a promise chain whose
// `.finally()` decrements `sessionBusyCount`. A stub missing `exportJsonl`
// throws a TypeError SYNCHRONOUSLY -- before any `.finally()` is attached --
// so the count is incremented and never decremented, `refreshExportLock`
// takes its busy branch forever, and both session buttons are wedged with no
// recovery short of a reload. Returning a REJECTED PROMISE instead keeps the
// failure on the async path every caller already handles.
//
// Reachable in practice: tap Start, deny the iOS motion permission prompt.
export function neverStartedHandle(reason) {
  return {
    stop() {},
    exportJsonl: () => Promise.reject(new Error(reason)),
    requestReference: () => Promise.resolve(null),
    close() {},
  };
}

// The user-facing line for a refused/failed motion permission. One string so
// the clinical and research paths say the same thing.
export const MOTION_DENIED_MESSAGE =
  'Motion access was not granted, so nothing was recorded. On iPhone: close '
  + 'this tab (or the app), reopen it, tap Start and choose Allow.';

// Asks iOS for motion access. MUST be called synchronously inside the tap
// handler, before the handler's first `await`: iOS only honours
// DeviceMotionEvent.requestPermission() inside the user gesture, and an await
// (even an IndexedDB read) ends that window -- the call then rejects or is
// silently refused. The caller starts the request here and hands the PROMISE
// to startCapture, which awaits it after whatever async setup it needs.
//
// Never rejects: a throw (sync or async) reads as 'denied', so the caller has
// one failure path, not an unhandled rejection that leaves Stop on screen.
export function requestMotionPermission() {
  if (typeof DeviceMotionEvent === 'undefined'
      || typeof DeviceMotionEvent.requestPermission !== 'function') {
    return Promise.resolve('granted');
  }
  try {
    return Promise.resolve(DeviceMotionEvent.requestPermission()).catch(() => 'denied');
  } catch {
    return Promise.resolve('denied');
  }
}

// Routes worker replies to the right consumer. Pure (no Worker, no DOM) so the
// routing can be tested directly.
//
// Every `{type:'error'}` from worker.js names the request that failed
// (`request: 'finish' | 'export' | 'reference' | 'batch' | 'start'`). Errors
// are routed BY THAT FIELD, never by "whichever queue happens to have a
// waiter": the 2026-11 bench audit found the research recorder's
// stop()->exportJsonl() sequence losing every still recording, because the
// `finish` request's `{type:'error', reason:'unscorable'}` arrived while an
// export was pending and was handed to the export promise -- "NOT saved:
// unscorable" -- while the real exportResult arrived a moment later and was
// dropped. An error from one request can no longer settle another's promise.
//
// A reply with no `request` field can only come from a worker.js older than
// this file (a stale service-worker cache). It is treated as a trial-level
// error unless an export/reference is pending -- the pre-2026-11 behaviour --
// EXCEPT 'unscorable', which only `finish` ever produced and so is always
// trial-level.
export function createReplyRouter({ onState, onResult, onError }) {
  const exportWaiters = [];
  const referenceWaiters = [];
  function routeError(m) {
    const req = m.request;
    if (req === 'reference') {
      // Resolved, not rejected: no comparison table is not an operator error.
      referenceWaiters.shift()?.resolve(null);
    } else if (req === 'export') {
      const w = exportWaiters.shift();
      if (w) w.reject(new Error(m.reason));
      else onError(m.reason);
    } else if (req !== undefined || m.reason === 'unscorable') {
      onError(m.reason);
    } else if (referenceWaiters.length > 0) {
      referenceWaiters.shift().resolve(null);
    } else if (exportWaiters.length > 0) {
      exportWaiters.shift().reject(new Error(m.reason));
    } else {
      onError(m.reason);
    }
  }
  return {
    route(m) {
      if (m.type === 'state') onState(m);
      else if (m.type === 'result') onResult(m.params, m.trajectory, m.ptScore);
      else if (m.type === 'exportResult') exportWaiters.shift()?.resolve(m.jsonl);
      else if (m.type === 'reference') referenceWaiters.shift()?.resolve(m.payload);
      else if (m.type === 'error') routeError(m);
    },
    awaitExport(waiter) { exportWaiters.push(waiter); },
    awaitReference(waiter) { referenceWaiters.push(waiter); },
  };
}

// The research raw recorder's Stop: flush the last batch WITHOUT asking the
// worker to score, export the raw log, then release the worker. Scoring is
// deliberately skipped -- a bench recording is often a still or partial log
// that cannot score, and that must never cost the recording (see
// createReplyRouter for the bug this replaced).
export async function stopAndExportRaw(handle) {
  handle.stop({ finish: false });
  try {
    return await handle.exportJsonl();
  } finally {
    handle.close?.();
  }
}

function defaultCreateWorker() {
  return new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
}

// `onSamples` is optional and used only by the research recorder: it is handed
// each flushed batch (the same Float64Array that is about to be transferred to
// the worker) so a live strip chart can read it. The clinical capture path
// passes no `onSamples`, so its behaviour is byte-for-byte unchanged.
//
// `permission` is the promise from requestMotionPermission(), started by the
// caller inside its tap handler (see that function). Omitted, startCapture
// asks itself -- correct only when nothing has been awaited since the tap.
// `createWorker` and `target` exist for tests (a Node test has no Worker and
// no window); the browser uses the defaults.
export async function startCapture({
  onState, onResult, onError, onSamples, permission,
  createWorker = defaultCreateWorker,
  target = typeof window !== 'undefined' ? window : undefined,
}) {
  if (typeof DeviceMotionEvent === 'undefined') {
    onError('This browser does not expose motion sensors.');
    return neverStartedHandle('capture never started: this browser does not expose motion sensors');
  }
  let granted;
  try {
    granted = await (permission ?? requestMotionPermission());
  } catch {
    granted = 'denied';
  }
  if (granted !== 'granted') {
    onError(MOTION_DENIED_MESSAGE);
    return neverStartedHandle('capture never started: motion permission denied');
  }

  let wakeLock = null;
  try { wakeLock = await globalThis.navigator?.wakeLock?.request('screen'); } catch { /* best effort */ }

  const router = createReplyRouter({ onState, onResult, onError });
  const worker = createWorker();
  worker.onmessage = (e) => router.route(e.data);
  worker.postMessage({ type: 'start', cfg: { beta: 0.041, emaAlpha: 0.5 } });

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
    // Read by the live-chart hook BEFORE the transfer below detaches the
    // buffer -- `onSamples` must consume it synchronously (foldGyroBatch does).
    // Wrapped so a throwing chart handler can never break the capture pipeline.
    if (onSamples) { try { onSamples(out); } catch { /* chart is best-effort */ } }
    // Transfer ownership -- no copy, and no COOP/COEP isolation required.
    // `out.buffer` is detached by this call; it must not be read again.
    worker.postMessage({ type: 'batch', buf: out.buffer }, [out.buffer]);
  };

  target.addEventListener('devicemotion', onMotion);
  const timer = setInterval(flush, BATCH_MS);
  let stopped = false;

  // Ends capture: stops listening and flushes the final batch. `finish` (the
  // default) also asks the worker to SCORE, which the clinical path needs.
  // The research raw recorder passes `{ finish: false }`: it never scores, and
  // a `finish` on a still/unscorable log only produces an 'unscorable' error
  // it has no use for. Idempotent: a second call does nothing.
  function stop({ finish = true } = {}) {
    if (stopped) return;
    stopped = true;
    clearInterval(timer);
    target.removeEventListener('devicemotion', onMotion);
    flush();
    if (finish) worker.postMessage({ type: 'finish' });
    wakeLock?.release?.().catch(() => {});
  }

  return {
    stop,
    // Releases the worker (and its wasm memory). Only for callers that are
    // completely done with the handle -- after close(), exportJsonl() cannot
    // answer. The clinical path keeps its handle for a later export and does
    // not call this.
    close() {
      stop({ finish: false });
      worker.terminate?.();
    },
    // Raw-log export (KTD4): resolves with the newline-delimited JSON of the
    // whole trial. Callable any time the worker's session is still alive --
    // in particular after `finish`, since `TrialSession::finish` takes
    // `&self` and never consumes the raw log -- which is what lets the
    // result screen's Export control fire after the trial has already
    // scored (or failed to).
    exportJsonl() {
      return new Promise((resolve, reject) => {
        router.awaitExport({ resolve, reject });
        worker.postMessage({ type: 'export' });
      });
    },
    // Ask the worker for the healthy reference the result screen shows
    // beside each measured value. Fire-and-forget: the answer arrives as a
    // normal `{type:"reference"}` message, and until it does the comparison
    // table stays hidden rather than showing dashes.
    requestReference() {
      return new Promise((resolve) => {
        // Settled either way. A cached worker.js from before this message
        // type existed falls through every branch of its handler and posts
        // NOTHING, so without this the promise hangs forever, the caller
        // keeps re-requesting, and referenceWaiters grows for the life of
        // the session. Resolving null means "no comparison available",
        // which the UI already renders as a hidden table.
        let settled = false;
        const done = (v) => { if (!settled) { settled = true; resolve(v); } };
        router.awaitReference({ resolve: done });
        worker.postMessage({ type: 'reference' });
        setTimeout(() => done(null), 3000);
      });
    },
  };
}
