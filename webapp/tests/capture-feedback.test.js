import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { captureQualityOf, SETTLE_TARGET_S, HOLD_TARGET_S, progressOf } from '../src/capture-feedback.js';

const T = 5.0;

test('a self-terminated trial is clean', () => {
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T, endedManually: false }), 'clean');
});

// Stopped after settling began: a partial settled tail, so neutral_deg has
// something to work with but less than the protocol asks for.
test('stopped mid-settle is short', () => {
  assert.equal(captureQualityOf({ settleS: 2.4, settleTargetS: T, endedManually: true }), 'short');
});

// Stopped with no settling at all: no settled tail, so the neutral estimate is
// the weakest of the three. Distinct from short on purpose.
test('stopped with no settling at all is unsettled', () => {
  assert.equal(captureQualityOf({ settleS: 0, settleTargetS: T, endedManually: true }), 'unsettled');
});

// The data is what the protocol asks for regardless of who ended the trial.
test('a manual stop at or past the target is still clean', () => {
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T, endedManually: true }), 'clean');
});

// Defaults must not flatter a trial nothing is known about.
test('defaults are pessimistic rather than optimistic', () => {
  assert.equal(captureQualityOf({}), 'unsettled');
  assert.equal(captureQualityOf(), 'unsettled');
});

test('the boundary is inclusive', () => {
  assert.equal(captureQualityOf({ settleS: 4.999, settleTargetS: T }), 'short');
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T }), 'clean');
});

// The JS constant mirrors mobile-imu-core's. Nothing else would catch a drift
// between them: the app would keep classifying against a stale target while
// the core terminated on the real one, so every trial would silently be
// labelled 'short'.
test('the settle target mirrors the Rust constant', async () => {
  const rs = await readFile(new URL('../../mobile-imu-core/src/session.rs', import.meta.url), 'utf8');
  const m = rs.match(/pub const SETTLE_TARGET_S: f64 = ([0-9.]+);/);
  assert.ok(m, 'SETTLE_TARGET_S not found in session.rs');
  assert.equal(Number(m[1]), SETTLE_TARGET_S);
});

// ---- progress ------------------------------------------------------------
// The bar answers "how much longer", which a state label alone cannot.
test('holding fills toward the hold target', () => {
  const p = progressOf({ stateCode: 1, calmS: HOLD_TARGET_S / 2, settleS: 0 });
  assert.ok(Math.abs(p.fraction - 0.5) < 0.02, JSON.stringify(p));
  assert.match(p.label, /hold/i);
});

test('released fills toward the settle target', () => {
  const p = progressOf({ stateCode: 3, calmS: 0, settleS: 2.5 });
  assert.ok(Math.abs(p.fraction - 0.5) < 0.01, JSON.stringify(p));
  assert.match(p.label, /settl/i);
});

// A reset must be visible as an empty bar, not a paused one -- the operator
// needs to see WHY they are still waiting.
test('a reset settle shows an empty bar', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: 0 }).fraction, 0);
});

// An empty bar in MOVING would imply progress that is not happening.
test('states with nothing to count toward show no bar', () => {
  assert.equal(progressOf({ stateCode: 0, calmS: 0, settleS: 0 }), null);
  assert.equal(progressOf({ stateCode: 2, calmS: 0, settleS: 0 }), null);
});

test('a settled trial shows a full bar', () => {
  assert.equal(progressOf({ stateCode: 4, calmS: 0, settleS: 5 }).fraction, 1);
});

test('fraction never exceeds one', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: 99 }).fraction, 1);
  assert.equal(progressOf({ stateCode: 1, calmS: 99, settleS: 0 }).fraction, 1);
});

test('fraction is never negative', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: -1 }).fraction, 0);
});

// The hold target mirrors session.rs's Ready threshold, which is
// 0.95 * GYRO_BIAS_WINDOW_S. A drift would leave the bar full before the core
// armed, or still filling after it had.
test('the hold target mirrors the Rust ready threshold', async () => {
  const rs = await readFile(new URL('../../mobile-imu-core/src/stillness.rs', import.meta.url), 'utf8');
  const m = rs.match(/pub const GYRO_BIAS_WINDOW_S: f64 = ([0-9.]+);/);
  assert.ok(m, 'GYRO_BIAS_WINDOW_S not found');
  assert.ok(Math.abs(HOLD_TARGET_S - 0.95 * Number(m[1])) < 1e-9);
});
