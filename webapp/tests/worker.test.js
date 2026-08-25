import { test } from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/swing.json', import.meta.url)));

// webapp/src/wasm/ is GENERATED and deliberately not committed (webapp/README.md
// explains why: the artifact embeds the building machine's registry path, its
// path separators, and a rustc version string, so it is not reproducible
// across machines). Checked explicitly, and before `../src/worker.js` is
// imported, because a static import of a missing module fails with an
// ERR_MODULE_NOT_FOUND stack that names a path and nothing else -- it does not
// tell a new contributor that there is a build step, or what it is called.
const wasmDir = new URL('../src/wasm/', import.meta.url);
const missing = ['mobile_imu_core.js', 'mobile_imu_core_bg.wasm']
  .filter((f) => !existsSync(new URL(f, wasmDir)));
if (missing.length > 0) {
  throw new Error(
    `webapp/src/wasm/ is missing: ${missing.join(', ')}.\n\n` +
      'These files are generated from mobile-imu-core and are not committed.\n' +
      'Build them before running the tests:\n\n' +
      '    npm run build:wasm\n\n' +
      '(run from webapp/; needs cargo, the wasm32-unknown-unknown target, and a\n' +
      'wasm-bindgen CLI matching mobile-imu-core/Cargo.lock -- the script checks\n' +
      'and tells you the exact install command.)',
  );
}

// Dynamic, so the check above runs first: static imports are hoisted and
// would otherwise throw before a single line of this file executed.
const { createSession, createWorkerHandler } = await import('../src/worker.js');

// `init()` in the generated `--target web` bindings fetches a URL by
// default, which does not exist under `node --test`. Passing the wasm bytes
// directly exercises the exact artifact that ships, without a second
// `--target nodejs` build (task-4 dispatch Correction 1).
const wasmSource = readFileSync(new URL('mobile_imu_core_bg.wasm', wasmDir));

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

// A batch of `n` calm samples in the 7-float wire layout, as an ArrayBuffer --
// the exact shape capture.js transfers.
function calmBatchBuffer(n) {
  const flat = [];
  for (let i = 0; i < n; i++) flat.push(i * 16.667, 0, 0, 9.81, 0.01, 0, 0);
  return Float64Array.from(flat).buffer;
}

test('a batch posted while start is still initialising waits for it instead of erroring', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  const post = (msg) => posted.push(msg);

  // This is the real cold-load sequence, and it must be reproduced without an
  // `await` between the two calls. capture.js posts `{type:'start'}` and then
  // starts its 50 ms flush interval immediately -- it does not, and cannot,
  // wait for the worker to fetch and instantiate the ~134 KB wasm module.
  // An earlier version of this test awaited each handle() call separately,
  // which serialised the handler inside the TEST and made the race invisible:
  // it passed against a handler that failed on every real first trial.
  const startP = handle(
    { type: 'start', cfg: { beta: 0.041, emaAlpha: 0.3, wasmSource } },
    post,
  );
  const batchP = handle({ type: 'batch', buf: calmBatchBuffer(120) }, post);
  await Promise.all([startP, batchP]);

  assert.deepEqual(
    posted.filter((m) => m.type === 'error'),
    [],
    'an in-flight start must not make the first batch look like a protocol violation',
  );
  assert.equal(posted.length, 2, 'one state for start, one for the batch');
  assert.equal(posted[0].type, 'state', 'start must still answer first');
  assert.equal(posted[1].type, 'state');
  // Not merely "no error": the samples have to have reached the session.
  // 120 calm samples at ~16.667 ms is ~2 s of hold, so the calm timer must
  // have advanced off zero -- a silently dropped batch would leave it at 0.
  assert.ok(
    posted[1].calm_s > 0,
    `batch was not actually ingested: calm_s stayed ${posted[1].calm_s}`,
  );
});

test('a batch arriving when no start was ever sent posts an error rather than staying silent', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  // No `start` at all, ever -- distinct from the in-flight case above. This
  // one is a genuine protocol violation and must keep erroring.
  await handle({ type: 'batch', buf: [0, 0, 0, 0, 0, 0, 0] }, (msg) => posted.push(msg));

  assert.equal(posted.length, 1, 'exactly one message must be posted back');
  assert.equal(posted[0].type, 'error');
  assert.notEqual(posted[0].reason, 'unscorable',
    "'unscorable' is reserved for a legitimate finish() with no result");
  assert.ok(posted[0].reason && posted[0].reason.length > 0, 'reason must be a real message');
});

test('finish arriving when no start was ever sent posts an error rather than staying silent', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  await handle({ type: 'finish' }, (msg) => posted.push(msg));

  assert.equal(posted.length, 1, 'exactly one message must be posted back');
  assert.equal(posted[0].type, 'error');
  assert.notEqual(posted[0].reason, 'unscorable');
});

test('finish posted while start is still initialising waits for it instead of erroring', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  const post = (msg) => posted.push(msg);

  // Stop tapped immediately after Start, before the wasm has instantiated:
  // capture.js's stop() flushes and then posts `finish` with no regard for
  // whether `start` has completed. The trial has no usable swing, so
  // 'unscorable' is the correct answer -- what must NOT happen is a
  // "finish received before start" fault, which app.js would latch.
  const startP = handle(
    { type: 'start', cfg: { beta: 0.041, emaAlpha: 0.3, wasmSource } },
    post,
  );
  const batchP = handle({ type: 'batch', buf: calmBatchBuffer(600) }, post);
  const finishP = handle({ type: 'finish' }, post);
  await Promise.all([startP, batchP, finishP]);

  assert.equal(posted.length, 3);
  assert.equal(posted[0].type, 'state');
  assert.equal(posted[1].type, 'state');
  assert.equal(posted[2].type, 'error');
  assert.equal(posted[2].reason, 'unscorable',
    `an all-calm trial must finish as 'unscorable', not as a protocol fault (got ${posted[2].reason})`);
});

test('finish posts a trajectory alongside the existing {type:"result", params} shape', async () => {
  // Same fixture and drive path as the "recorded swing" test above, but
  // through createWorkerHandler's message protocol (not createSession
  // directly), so this exercises the exact `result` message capture.js/
  // app.js receive: `{type:'result', params, trajectory}`. Task-N dispatch:
  // the existing shape must keep working, with trajectory riding alongside.
  const handle = createWorkerHandler();
  const posted = [];
  const post = (msg) => posted.push(msg);

  await handle({ type: 'start', cfg: { beta: 0.041, emaAlpha: 0.3, wasmSource } }, post);
  for (const batch of fixture.batches) {
    await handle({ type: 'batch', buf: Float64Array.from(batch).buffer }, post);
  }
  await handle({ type: 'finish' }, post);

  const result = posted.find((m) => m.type === 'result');
  assert.ok(result, 'the fixture must still be scorable');
  assert.ok(result.params && typeof result.params === 'object',
    'the pre-existing params shape must still be present');

  const traj = result.trajectory;
  assert.ok(traj, 'a trajectory must ride alongside params');
  assert.ok(Array.isArray(traj.t) && traj.t.length > 0, 't must be the full non-empty tick series');
  assert.ok(Array.isArray(traj.angle_deg), 'angle_deg must be an array');
  assert.equal(traj.t.length, traj.angle_deg.length, 't and angle_deg must be the same length');
  // Tick 0 is NaN by contract (mobile-imu-core/src/resample.rs) -> JSON
  // null -> parses back as null, never as the illegal `NaN` token.
  assert.equal(traj.angle_deg[0], null, 'tick 0 must parse back as null, not NaN');
  assert.ok(traj.t.every((v) => typeof v === 'number'), 'every tick time must be finite');

  assert.equal(typeof traj.release_idx, 'number');
  assert.ok(traj.release_idx >= 0 && traj.release_idx < traj.t.length,
    'release_idx must index into the full tick series');
  assert.ok(Array.isArray(traj.peak_idx));
  assert.ok(Array.isArray(traj.trough_idx));
  for (const i of [...traj.peak_idx, ...traj.trough_idx]) {
    assert.ok(Number.isInteger(i) && i >= 0 && i < traj.t.length,
      `extremum index ${i} must index into the full tick series`);
    assert.ok(typeof traj.angle_deg[i] === 'number',
      `extremum index ${i} must land on a finite angle_deg tick`);
  }
  assert.equal(typeof traj.neutral_deg, 'number');
});

test('a malformed start cfg resolves to a real protocol message, not silence', async () => {
  const handle = createWorkerHandler();
  const posted = [];
  // No beta/emaAlpha at all -- WasmSession's constructor receives undefined
  // for both, which wasm-bindgen coerces to NaN for an f64 parameter.
  await handle({ type: 'start', cfg: { wasmSource } }, (msg) => posted.push(msg));

  assert.equal(posted.length, 1, 'exactly one message must be posted back');
  // Pinned to the one thing that actually happens rather than "state OR
  // error": the previous either/or form accepted both outcomes, so it could
  // not fail and told the reader nothing. What the wasm boundary does today
  // is construct the session with NaN gains and report an ordinary opening
  // state; if that ever becomes a throw (or a hang), this must say so.
  assert.equal(posted[0].type, 'state',
    'undefined gains are currently coerced to NaN and accepted; a change here needs a decision, not a silent pass');
  assert.equal(posted[0].code, 0, 'a fresh session opens in Moving');
});
