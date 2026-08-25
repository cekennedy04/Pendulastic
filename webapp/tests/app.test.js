import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextOutcome } from '../src/app.js';

// nextOutcome is app.js's pure fault-latch reducer over the onResult/onError
// message stream for one trial -- no DOM, no worker, no globals required
// (mirrors the createWorkerHandler / encodeSample split used in earlier
// tasks). It exists because `onError`'s call to `session.stop()` posts a
// second `finish` to the worker, whose idempotent reply must not be allowed
// to silently overwrite a fault the clinician already saw (fix-round-1
// finding).

test('a normal result displays and does not latch', () => {
  const { latched, action } = nextOutcome(false, { type: 'result', params: { f: 1 } });
  assert.equal(latched, false);
  assert.deepEqual(action, { kind: 'result', params: { f: 1 } });
});

test('an unscorable outcome displays and does not latch', () => {
  const { latched, action } = nextOutcome(false, { type: 'error', reason: 'unscorable' });
  assert.equal(latched, false, "'unscorable' is an expected clinical outcome, not a fault");
  assert.deepEqual(action, { kind: 'unscorable' });
});

test('a genuine fault displays and latches', () => {
  const { latched, action } = nextOutcome(false, { type: 'error', reason: 'worker crashed' });
  assert.equal(latched, true);
  assert.deepEqual(action, { kind: 'fault', reason: 'worker crashed' });
});

test('once latched, a bounced real result is ignored rather than overwriting the fault', () => {
  const { latched, action } = nextOutcome(true, { type: 'result', params: { f: 1 } });
  assert.equal(latched, true, 'the latch must stay set');
  assert.equal(action, null, 'no display action once a fault has latched this trial');
});

test('once latched, a bounced unscorable is ignored rather than overwriting the fault', () => {
  const { latched, action } = nextOutcome(true, { type: 'error', reason: 'unscorable' });
  assert.equal(latched, true);
  assert.equal(action, null);
});

test('an unscorable outcome does not itself latch out a later genuine result', () => {
  // 'unscorable' must never swallow anything that follows it in the same
  // trial -- only a real fault is allowed to latch.
  const first = nextOutcome(false, { type: 'error', reason: 'unscorable' });
  assert.equal(first.latched, false);
  const second = nextOutcome(first.latched, { type: 'result', params: { f: 2 } });
  assert.equal(second.latched, false);
  assert.deepEqual(second.action, { kind: 'result', params: { f: 2 } });
});

test('a real fault always wins over a subsequent unscorable bounce', () => {
  const first = nextOutcome(false, { type: 'error', reason: 'worker crashed' });
  assert.equal(first.latched, true);
  const second = nextOutcome(first.latched, { type: 'error', reason: 'unscorable' });
  assert.equal(second.latched, true);
  assert.equal(second.action, null, 'the fault must not be overwritten by the bounce');
});

test('a result event carrying a trajectory passes it through on the action', () => {
  const trajectory = { t: [0, 0.05], angle_deg: [null, 180], release_idx: 0, peak_idx: [], trough_idx: [], neutral_deg: 180 };
  const { latched, action } = nextOutcome(false, { type: 'result', params: { f: 1 }, trajectory });
  assert.equal(latched, false);
  assert.deepEqual(action, { kind: 'result', params: { f: 1 }, trajectory });
});

test('a result event with no trajectory key omits it from the action rather than adding undefined', () => {
  // worker.js's message always carries a trajectory (falling back to null
  // when finish_trajectory() itself returns nothing), but nextOutcome must
  // not silently invent the key for any caller that omits it -- see the
  // very first test in this file, which relies on exactly this shape.
  const { action } = nextOutcome(false, { type: 'result', params: { f: 1 } });
  assert.deepEqual(action, { kind: 'result', params: { f: 1 } });
  assert.ok(!('trajectory' in action), 'no trajectory key should appear when the event carried none');
});

test('a result event carrying a ptScore passes it through on the action', () => {
  const ptScore = { score: 0.42, zone: 'borderline', breakdown: [{ key: 'area_ratio', value: 0.3 }] };
  const { latched, action } = nextOutcome(false, { type: 'result', params: { f: 1 }, ptScore });
  assert.equal(latched, false);
  assert.deepEqual(action, { kind: 'result', params: { f: 1 }, ptScore });
});

test('a result event with no ptScore key omits it from the action rather than adding undefined', () => {
  const { action } = nextOutcome(false, { type: 'result', params: { f: 1 } });
  assert.ok(!('ptScore' in action), 'no ptScore key should appear when the event carried none');
});

test('a result event can carry both a trajectory and a ptScore together', () => {
  const trajectory = { t: [0, 0.05], angle_deg: [null, 180], release_idx: 0, peak_idx: [], trough_idx: [], neutral_deg: 180 };
  const ptScore = { score: 0.05, zone: 'healthy', breakdown: [] };
  const { action } = nextOutcome(false, { type: 'result', params: { f: 1 }, trajectory, ptScore });
  assert.deepEqual(action, { kind: 'result', params: { f: 1 }, trajectory, ptScore });
});

test('a real fault always wins over a subsequent bounced result', () => {
  const first = nextOutcome(false, { type: 'error', reason: 'worker crashed' });
  assert.equal(first.latched, true);
  const second = nextOutcome(first.latched, { type: 'result', params: { f: 3 } });
  assert.equal(second.latched, true);
  assert.equal(second.action, null, 'the fault must not be overwritten by a bounced result');
});
