import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { captureQualityOf, SETTLE_TARGET_S } from '../src/capture-feedback.js';

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
