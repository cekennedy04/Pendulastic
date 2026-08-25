import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSession, createWorkerHandler } from '../src/worker.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/swing.json', import.meta.url)));

// `init()` in the generated `--target web` bindings fetches a URL by
// default, which does not exist under `node --test`. Passing the wasm bytes
// directly exercises the exact artifact that ships, without a second
// `--target nodejs` build (task-4 dispatch Correction 1).
const wasmSource = readFileSync(new URL('../src/wasm/mobile_imu_core_bg.wasm', import.meta.url));

test('a recorded swing scores through the same path as a live capture', async () => {
  const s = await createSession({ beta: 0.041, emaAlpha: 0.3, wasmSource });
  for (const batch of fixture.batches) s.pushBatch(Float64Array.from(batch));
  const result = s.finish();
  assert.ok(result, 'fixture must be scorable');

  // Tolerances mirror tests/test_web_export_contract.py's checks on the
  // identical simulated trial (A0_DEG=45, F_HZ=1.0, neutral=180-A0=135).
  // Deliberately tight enough to catch a wrong-unit regression: gyro fed in
  // deg/s instead of rad/s is off by a factor of ~57 and would blow every
  // one of these gates by more than an order of magnitude (or drive the
  // release detector into firing on the hold-phase static offsets, or
  // never at all) -- not read as a few-percent miss. Bounds were sized
  // against this exact fixture's own observed output (f=0.99999996,
  // a0_deg=44.35, neutral_deg=135.57), with headroom for legitimate
  // simulation/estimation noise, not widened to make the numbers fit.
  assert.ok(Math.abs(result.f - 1.0) <= 0.15,
    `f=${result.f} not within 0.15 Hz of the simulated 1.0 Hz`);
  assert.ok(Math.abs(result.a0_deg - 45.0) <= 2.0,
    `a0_deg=${result.a0_deg} not within 2 deg of the simulated 45 deg`);
  assert.ok(Math.abs(result.neutral_deg - 135.0) <= 3.0,
    `neutral_deg=${result.neutral_deg} not within 3 deg of the expected 180-45=135`);
});

test('an all-calm log reports no release rather than throwing', async () => {
  const s = await createSession({ beta: 0.041, emaAlpha: 0.3, wasmSource });
  const calm = [];
  for (let i = 0; i < 600; i++) calm.push(i * 16.667, 0, 0, 9.81, 0.01, 0, 0);
  s.pushBatch(Float64Array.from(calm));
  assert.equal(s.finish(), undefined);
});

test('a batch arriving before start posts an error rather than staying silent', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  await handle({ type: 'batch', buf: [0, 0, 0, 0, 0, 0, 0] }, (msg) => posted.push(msg));

  assert.equal(posted.length, 1, 'exactly one message must be posted back');
  assert.equal(posted[0].type, 'error');
  assert.notEqual(posted[0].reason, 'unscorable',
    "'unscorable' is reserved for a legitimate finish() with no result");
  assert.ok(posted[0].reason && posted[0].reason.length > 0, 'reason must be a real message');
});

test('finish arriving before start posts an error rather than staying silent', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  await handle({ type: 'finish' }, (msg) => posted.push(msg));

  assert.equal(posted.length, 1, 'exactly one message must be posted back');
  assert.equal(posted[0].type, 'error');
  assert.notEqual(posted[0].reason, 'unscorable');
});

test('a malformed start cfg posts an error instead of throwing unhandled', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  // No beta/emaAlpha at all -- WasmSession's constructor receives undefined
  // for both, which wasm-bindgen coerces in a way that must not crash the
  // handler silently either way.
  await handle({ type: 'start', cfg: { wasmSource } }, (msg) => posted.push(msg));

  assert.equal(posted.length, 1, 'exactly one message must be posted back');
  assert.ok(posted[0].type === 'state' || posted[0].type === 'error',
    'a malformed cfg must resolve to a real protocol message, not silence');
});
