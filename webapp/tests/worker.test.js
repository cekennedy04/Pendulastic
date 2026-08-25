import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createSession } from '../src/worker.js';

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
  assert.ok(result.n >= 0, 'n present');
  assert.ok(Number.isFinite(result.a0_deg), 'a0_deg finite');
});

test('an all-calm log reports no release rather than throwing', async () => {
  const s = await createSession({ beta: 0.041, emaAlpha: 0.3, wasmSource });
  const calm = [];
  for (let i = 0; i < 600; i++) calm.push(i * 16.667, 0, 0, 9.81, 0.01, 0, 0);
  s.pushBatch(Float64Array.from(calm));
  assert.equal(s.finish(), undefined);
});
