import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  encodeSample, neverStartedHandle, startCapture, createReplyRouter,
  requestMotionPermission, MOTION_DENIED_MESSAGE, stopAndExportRaw,
  RAW_EXPORT_TIMEOUT_MESSAGE,
} from '../src/capture.js';

// encodeSample is the pure arithmetic core of capture.js: axis mapping and
// deg/s->rad/s conversion. It is exercised here with plain objects standing
// in for a DeviceMotionEvent -- no DOM, no worker, no globals required.

test('axis mapping: beta->x, gamma->y, alpha->z land in the right slots', () => {
  const out = new Float64Array(7);
  const event = {
    timeStamp: 123.456,
    accelerationIncludingGravity: { x: 1, y: 2, z: 3 },
    rotationRate: { alpha: 30, beta: 10, gamma: 20 },
  };
  encodeSample(out, 0, event);

  assert.equal(out[1], 1, 'ax');
  assert.equal(out[2], 2, 'ay');
  assert.equal(out[3], 3, 'az');
  // gx must come from beta, gy from gamma, gz from alpha -- not positional.
  assert.equal(out[4], 10 * (Math.PI / 180), 'gx must be beta (10 deg/s), not gamma or alpha');
  assert.equal(out[5], 20 * (Math.PI / 180), 'gy must be gamma (20 deg/s), not beta or alpha');
  assert.equal(out[6], 30 * (Math.PI / 180), 'gz must be alpha (30 deg/s), not beta or gamma');
});

test('gyro conversion: 180 deg/s converts to exactly pi rad/s', () => {
  const out = new Float64Array(7);
  const event = {
    timeStamp: 0,
    accelerationIncludingGravity: { x: 0, y: 0, z: 0 },
    rotationRate: { alpha: 180, beta: 180, gamma: 180 },
  };
  encodeSample(out, 0, event);

  assert.equal(out[4], Math.PI, 'beta (gx): 180 deg/s must equal pi rad/s exactly');
  assert.equal(out[5], Math.PI, 'gamma (gy): 180 deg/s must equal pi rad/s exactly');
  assert.equal(out[6], Math.PI, 'alpha (gz): 180 deg/s must equal pi rad/s exactly');
});

test('timeStamp passes through unchanged, in milliseconds, at a nonzero offset', () => {
  const out = new Float64Array(14);
  const event = {
    timeStamp: 98765.4321,
    accelerationIncludingGravity: { x: 0, y: 0, z: 9.81 },
    rotationRate: { alpha: 0, beta: 0, gamma: 0 },
  };
  // Write into the second sample slot to also confirm `offset` is honored
  // and the first slot is left untouched.
  encodeSample(out, 7, event);

  assert.equal(out[0], 0, 'first sample slot must be untouched');
  assert.equal(out[7], 98765.4321, 'timeStamp must pass through unchanged (no unit conversion)');
});

// startCapture's two early exits (no DeviceMotionEvent, permission denied)
// return a handle that never captured anything. app.js keeps that handle in
// `exportSession` and later calls `exportSession.exportJsonl()` inside a
// promise chain whose `.finally()` decrements `sessionBusyCount`. A handle
// missing `exportJsonl` throws a TypeError SYNCHRONOUSLY, before that
// `.finally()` is attached -- so the count never comes back down, the
// export-lock UI takes its busy branch forever, and both session buttons are
// wedged with no recovery short of a page reload. The session then cannot be
// exported at all, and the exported files are the archive of record.
//
// This is why the stub must be shape-compatible with the real handle, not
// merely "something with a stop()".

test('a handle for a capture that never started still exposes exportJsonl', () => {
  const handle = neverStartedHandle('capture never started: motion permission denied');
  assert.equal(typeof handle.stop, 'function');
  assert.equal(typeof handle.exportJsonl, 'function', 'a missing exportJsonl throws synchronously at the call site and leaks the busy count');
});

test('that handle REJECTS rather than throwing synchronously', async () => {
  const handle = neverStartedHandle('capture never started: motion permission denied');
  const pending = handle.exportJsonl();
  assert.ok(
    typeof pending?.then === 'function',
    'exportJsonl must return a promise even on a stub -- a synchronous throw escapes past the caller\'s .finally() and leaks the busy count',
  );
  await assert.rejects(() => pending, /never started/);
});

test('stop() on a never-started handle is a harmless no-op', () => {
  // app.js calls session?.stop() on the Stop button and again from onError.
  assert.doesNotThrow(() => neverStartedHandle('nope').stop());
});

test('startCapture returns that shape when no motion sensors exist, and reports why', async () => {
  // Node has no DeviceMotionEvent, so this exercises the real first early
  // exit rather than a stand-in.
  assert.equal(typeof DeviceMotionEvent, 'undefined', 'precondition: this test drives the no-sensor early exit');
  let reported = null;
  const handle = await startCapture({ onState() {}, onResult() {}, onError: (r) => { reported = r; } });
  assert.match(reported, /motion sensors/, 'the operator must be told why, not just left with a dead Start button');
  assert.equal(typeof handle.exportJsonl, 'function');
  await assert.rejects(() => handle.exportJsonl(), /never started/);
});

// ---- reply routing (createReplyRouter) -------------------------------------
function routerHarness() {
  const calls = { errors: [], states: 0, results: 0 };
  const router = createReplyRouter({
    onState: () => { calls.states++; },
    onResult: () => { calls.results++; },
    onError: (r) => calls.errors.push(r),
  });
  const pendingExport = () => {
    const p = { settled: null };
    router.awaitExport({ resolve: (v) => { p.settled = ['ok', v]; }, reject: (e) => { p.settled = ['err', e.message]; } });
    return p;
  };
  return { calls, router, pendingExport };
}

test('a finish error never settles a pending export (the raw-recorder data loss)', () => {
  const { calls, router, pendingExport } = routerHarness();
  const exp = pendingExport();
  router.route({ type: 'error', reason: 'unscorable', request: 'finish' });
  assert.equal(exp.settled, null, 'the export must still be waiting for its own reply');
  assert.deepEqual(calls.errors, ['unscorable']);
  router.route({ type: 'exportResult', jsonl: '{"t":0}\n' });
  assert.deepEqual(exp.settled, ['ok', '{"t":0}\n']);
});

test('an unscorable reply from a stale worker (no request field) is still trial-level', () => {
  const { calls, router, pendingExport } = routerHarness();
  const exp = pendingExport();
  router.route({ type: 'error', reason: 'unscorable' });
  assert.equal(exp.settled, null);
  assert.deepEqual(calls.errors, ['unscorable']);
});

test('an export error rejects the export, not the trial', () => {
  const { calls, router, pendingExport } = routerHarness();
  const exp = pendingExport();
  router.route({ type: 'error', reason: 'export received before start', request: 'export' });
  assert.deepEqual(exp.settled, ['err', 'export received before start']);
  assert.deepEqual(calls.errors, []);
});

test('a reference error resolves the reference to null and leaves the export alone', () => {
  const { calls, router, pendingExport } = routerHarness();
  const exp = pendingExport();
  let ref = 'pending';
  router.awaitReference({ resolve: (v) => { ref = v; } });
  router.route({ type: 'error', reason: 'boom', request: 'reference' });
  assert.equal(ref, null);
  assert.equal(exp.settled, null);
  assert.deepEqual(calls.errors, []);
});

test('a batch error mid-trial is a trial fault even while an export is pending', () => {
  const { calls, router, pendingExport } = routerHarness();
  const exp = pendingExport();
  router.route({ type: 'error', reason: 'worker crashed', request: 'batch' });
  assert.equal(exp.settled, null);
  assert.deepEqual(calls.errors, ['worker crashed']);
});

// ---- iOS motion permission ---------------------------------------------------
function withDME(dme, fn) {
  return async () => {
    const had = 'DeviceMotionEvent' in globalThis;
    const prev = globalThis.DeviceMotionEvent;
    globalThis.DeviceMotionEvent = dme;
    try { await fn(); } finally {
      if (had) globalThis.DeviceMotionEvent = prev; else delete globalThis.DeviceMotionEvent;
    }
  };
}

test('requestMotionPermission calls iOS synchronously, inside the gesture', withDME(
  class { static calls = 0; static requestPermission() { this.calls++; return Promise.resolve('granted'); } },
  async () => {
    const p = requestMotionPermission();
    // Counted BEFORE any await: this is the whole point of the fix.
    assert.equal(globalThis.DeviceMotionEvent.calls, 1);
    assert.equal(await p, 'granted');
  },
));

test('a rejected permission request reads as denied, never as an unhandled throw', withDME(
  class { static requestPermission() { return Promise.reject(new Error('NotAllowedError')); } },
  async () => { assert.equal(await requestMotionPermission(), 'denied'); },
));

test('a synchronously throwing permission request reads as denied', withDME(
  class { static requestPermission() { throw new Error('must be handling a user gesture'); } },
  async () => { assert.equal(await requestMotionPermission(), 'denied'); },
));

test('without requestPermission (Android/desktop) permission is granted', withDME(
  class {},
  async () => { assert.equal(await requestMotionPermission(), 'granted'); },
));

test('startCapture uses the permission it was handed and does not ask again', withDME(
  class { static calls = 0; static requestPermission() { this.calls++; return Promise.resolve('granted'); } },
  async () => {
    let reported = null;
    let workerMade = false;
    const handle = await startCapture({
      onState() {}, onResult() {}, onError: (r) => { reported = r; },
      permission: Promise.resolve('denied'),
      createWorker: () => { workerMade = true; return {}; },
      target: new EventTarget(),
    });
    assert.equal(globalThis.DeviceMotionEvent.calls, 0, 'asking after an await is the iOS bug');
    assert.equal(reported, MOTION_DENIED_MESSAGE);
    assert.equal(workerMade, false, 'nothing starts when access is refused');
    await assert.rejects(() => handle.exportJsonl(), /permission denied/);
    assert.doesNotThrow(() => handle.close());
  },
));

test('a permission promise that rejects is reported, not thrown out of startCapture', withDME(
  class {},
  async () => {
    let reported = null;
    const handle = await startCapture({
      onState() {}, onResult() {}, onError: (r) => { reported = r; },
      permission: Promise.reject(new Error('boom')),
      target: new EventTarget(),
    });
    assert.equal(reported, MOTION_DENIED_MESSAGE);
    assert.equal(typeof handle.stop, 'function');
  },
));

test('the denied message tells the operator what to do', () => {
  assert.match(MOTION_DENIED_MESSAGE, /Allow/);
});
// ---- worker crash, export timeout, empty recording (review of #116) --------
test('a worker crash rejects every pending export and reports once', () => {
  const { calls, router, pendingExport } = routerHarness();
  const a = pendingExport();
  const b = pendingExport();
  let ref = 'pending';
  router.awaitReference({ resolve: (v) => { ref = v; } });
  router.failAll('capture worker crashed: boom');
  router.failAll('capture worker crashed: again');
  assert.deepEqual(a.settled, ['err', 'capture worker crashed: boom']);
  assert.deepEqual(b.settled, ['err', 'capture worker crashed: boom']);
  assert.equal(ref, null);
  assert.deepEqual(calls.errors, ['capture worker crashed: boom'], 'one report, not one per call');
  // A request made after the crash fails at once instead of hanging.
  const c = pendingExport();
  assert.deepEqual(c.settled, ['err', 'capture worker crashed: boom']);
});

test('startCapture wires worker.onerror and onmessageerror to fail pending requests', withDME(
  class {},
  async () => {
    for (const fire of [(w) => w.onerror({ message: 'module failed', preventDefault() {} }),
                        (w) => w.onmessageerror({})]) {
      let worker = null;
      const errors = [];
      const handle = await startCapture({
        onState() {}, onResult() {}, onError: (r) => errors.push(r),
        createWorker: () => (worker = { postMessage() {}, terminate() {} }),
        target: new EventTarget(),
      });
      const pending = handle.exportJsonl();
      fire(worker);
      await assert.rejects(() => pending, /capture worker/);
      assert.equal(errors.length, 1);
      assert.match(errors[0], /capture worker/);
      handle.close();
    }
  },
));

test('stopAndExportRaw times out with a loggable message instead of hanging', async () => {
  let closed = false;
  const handle = {
    stop() {}, sampleCount: () => 10, close() { closed = true; },
    exportJsonl: () => new Promise(() => {}), // a worker that never answers
  };
  await assert.rejects(() => stopAndExportRaw(handle, { timeoutMs: 20 }),
    (err) => err.message === RAW_EXPORT_TIMEOUT_MESSAGE);
  assert.match(RAW_EXPORT_TIMEOUT_MESSAGE, /timeout.*log as ERROR/);
  assert.equal(closed, true, 'the worker is released even on timeout');
});

test('stopAndExportRaw skips an empty recording', async () => {
  let exported = false;
  let stoppedWith = null;
  let closed = false;
  const handle = {
    stop(opts) { stoppedWith = opts; }, sampleCount: () => 0, close() { closed = true; },
    exportJsonl: () => { exported = true; return Promise.resolve(''); },
  };
  assert.equal(await stopAndExportRaw(handle), null);
  assert.deepEqual(stoppedWith, { finish: false });
  assert.equal(exported, false);
  assert.equal(closed, true);
});