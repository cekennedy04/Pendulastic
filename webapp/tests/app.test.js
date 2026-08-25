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

test('a real fault always wins over a subsequent bounced result', () => {
  const first = nextOutcome(false, { type: 'error', reason: 'worker crashed' });
  assert.equal(first.latched, true);
  const second = nextOutcome(first.latched, { type: 'result', params: { f: 3 } });
  assert.equal(second.latched, true);
  assert.equal(second.action, null, 'the fault must not be overwritten by a bounced result');
});
