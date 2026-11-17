// The research raw recorder's Stop path, end to end-ish: capture.js's real
// startCapture/stopAndExportRaw, driving worker.js's real message handler and
// the real wasm, with only the two browser globals capture.js cannot get under
// Node (a Worker and a window to receive devicemotion) stood in for.
//
// The bug this pins (bench audit 2026-11-17): researchStop called
// handle.stop(), which posted `finish`, then handle.exportJsonl(). On a still
// recording -- the common bench case -- the worker answered `finish` with
// {type:'error', reason:'unscorable'}, capture.js handed that error to the
// pending export, the UI showed "NOT saved: unscorable", and the real
// exportResult that followed was dropped. Every unscorable raw recording was
// lost.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const wasmDir = new URL('../src/wasm/', import.meta.url);
if (!existsSync(new URL('mobile_imu_core_bg.wasm', wasmDir))) {
  throw new Error('webapp/src/wasm/ is missing -- run `npm run build:wasm` first (see worker.test.js).');
}
const { createWorkerHandler } = await import('../src/worker.js');
const { startCapture, stopAndExportRaw } = await import('../src/capture.js');
const wasmSource = readFileSync(new URL('mobile_imu_core_bg.wasm', wasmDir));

// A Worker stand-in: the real handler, with replies delivered asynchronously
// (as a real worker's are) and the wasm bytes injected into `start`, since
// Node cannot fetch the module by URL. Records every request it receives.
function fakeWorkerFactory(log) {
  return () => {
    const handle = createWorkerHandler();
    const w = {
      onmessage: null,
      terminated: false,
      postMessage(m) {
        log.push(m.type);
        const msg = m.type === 'start' ? { ...m, cfg: { ...m.cfg, wasmSource } } : m;
        handle(msg, (reply) => setTimeout(() => w.onmessage?.({ data: reply }), 0));
      },
      terminate() { w.terminated = true; },
    };
    log.worker = w;
    return w;
  };
}

// A phone lying still on the bench: gravity on z, no rotation.
function stillEvent(tMs) {
  const ev = new Event('devicemotion');
  Object.defineProperty(ev, 'timeStamp', { value: tMs });
  ev.accelerationIncludingGravity = { x: 0, y: 0, z: 9.81 };
  ev.rotationRate = { alpha: 0, beta: 0.2, gamma: 0 };
  return ev;
}

async function recordStill(target, chunks = 4, perChunk = 40) {
  let t = 0;
  for (let c = 0; c < chunks; c++) {
    for (let i = 0; i < perChunk; i++) { target.dispatchEvent(stillEvent(t)); t += 16.667; }
    await new Promise((r) => setTimeout(r, 70)); // let the 50 ms flush run
  }
}

function withMotionGlobals(fn) {
  return async () => {
    const hadDME = 'DeviceMotionEvent' in globalThis;
    const prev = globalThis.DeviceMotionEvent;
    globalThis.DeviceMotionEvent = class {}; // desktop/Android shape: no requestPermission
    try { await fn(); } finally {
      if (hadDME) globalThis.DeviceMotionEvent = prev; else delete globalThis.DeviceMotionEvent;
    }
  };
}

test('the raw recorder saves a still (unscorable) recording', withMotionGlobals(async () => {
  const log = [];
  const errors = [];
  const target = new EventTarget();
  const handle = await startCapture({
    onState() {}, onResult() {}, onError: (r) => errors.push(r),
    createWorker: fakeWorkerFactory(log), target,
  });
  await recordStill(target);

  const jsonl = await stopAndExportRaw(handle);

  assert.equal(typeof jsonl, 'string');
  const lines = jsonl.trim().split('\n');
  assert.ok(lines.length > 50, `expected the recorded samples in the log, got ${lines.length} lines`);
  assert.ok(!log.includes('finish'), 'the raw recorder must not ask the worker to score');
  assert.deepEqual(errors, [], 'nothing about a still recording is an error');
  assert.equal(log.worker.terminated, true, 'the worker is released once the log is out');
}));

test('even WITH finish, an unscorable reply can no longer swallow the export', withMotionGlobals(async () => {
  // The exact pre-fix call sequence (stop() posts finish, then export). The
  // routing fix alone must keep the recording: 'unscorable' goes to onError,
  // and the export resolves with the log.
  const log = [];
  const errors = [];
  const target = new EventTarget();
  const handle = await startCapture({
    onState() {}, onResult() {}, onError: (r) => errors.push(r),
    createWorker: fakeWorkerFactory(log), target,
  });
  await recordStill(target, 2);

  handle.stop();
  const jsonl = await handle.exportJsonl();

  assert.ok(log.includes('finish'));
  assert.ok(jsonl.trim().length > 0, 'the export must resolve with the raw log');
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(errors, ['unscorable'], 'the unscorable reply is a trial outcome, routed to onError');
}));

test('an immediate Stop (no samples) saves nothing and still releases the worker', withMotionGlobals(async () => {
  const log = [];
  const target = new EventTarget();
  const handle = await startCapture({
    onState() {}, onResult() {}, onError() {},
    createWorker: fakeWorkerFactory(log), target,
  });
  assert.equal(await stopAndExportRaw(handle), null);
  assert.ok(!log.includes('export'), 'nothing to export, so nothing is asked of the worker');
  assert.ok(!log.includes('finish'));
  assert.equal(log.worker.terminated, true);
}));