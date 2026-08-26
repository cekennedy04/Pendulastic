import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextOutcome, resumeOrCreateSession, sessionLockState, invalidateExport } from '../src/app.js';
import { canCloseSession, markExported } from '../src/session-store.js';

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

// resumeOrCreateSession decides, on a fresh page load, which session a
// trial should be attributed to -- resume the patient's still-open (never
// exported) session, or start a brand new one. This is the mechanism that
// keeps a reload mid-session from orphaning already-recorded trials.

test('with no sessions on file, a fresh session is created for the patient', () => {
  const s = resumeOrCreateSession([], 'p1');
  assert.equal(s.patient_id, 'p1');
  assert.equal(s.exported_at, null);
});

test('an existing unexported session for the patient is resumed, not duplicated', () => {
  const open = { id: 'existing', patient_id: 'p1', timestamp: 5, exported_at: null };
  const s = resumeOrCreateSession([open], 'p1');
  assert.equal(s.id, 'existing');
});

test('an exported session is treated as closed and is never resumed', () => {
  const closed = markExported({ id: 'old', patient_id: 'p1', timestamp: 5, exported_at: null }, 999);
  const s = resumeOrCreateSession([closed], 'p1');
  assert.notEqual(s.id, 'old', 'a closed session must not be handed back as the one to keep recording into');
  assert.equal(s.exported_at, null, 'the new session must start unexported');
});

test('a session belonging to a different patient is never resumed', () => {
  const other = { id: 'theirs', patient_id: 'p2', timestamp: 5, exported_at: null };
  const s = resumeOrCreateSession([other], 'p1');
  assert.notEqual(s.id, 'theirs');
  assert.equal(s.patient_id, 'p1');
});

test('when multiple open sessions exist for the patient, the most recently created one is resumed', () => {
  const older = { id: 'older', patient_id: 'p1', timestamp: 10, exported_at: null };
  const newer = { id: 'newer', patient_id: 'p1', timestamp: 20, exported_at: null };
  const s = resumeOrCreateSession([older, newer], 'p1');
  assert.equal(s.id, 'newer');
});

// sessionLockState is the pure decision behind the session-bar UI: whether
// Close is enabled, and whether the unexported-trials warning shows.

test('a brand-new session with zero trials is not closable and shows no warning', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.deepEqual(sessionLockState(s, 0), { closable: false, warningVisible: false });
});

test('with no session at all, the lock state is inert', () => {
  assert.deepEqual(sessionLockState(null, 0), { closable: false, warningVisible: false });
});

test('a session with recorded, unexported trials is not closable and shows the warning', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.deepEqual(sessionLockState(s, 3), { closable: false, warningVisible: true });
});

test('a session with recorded trials that has been exported is closable and shows no warning', () => {
  const s = markExported({ id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null }, 12345);
  assert.deepEqual(sessionLockState(s, 3), { closable: true, warningVisible: false });
});

// invalidateExport is the other half of the export gate: the rule that a
// newly recorded trial must clear exported_at, so a session that gained
// data since its last export can no longer be closed. Without this, the
// gate is cosmetic -- see the doc comment on invalidateExport in app.js.

test('invalidateExport clears exported_at without mutating the session it was given', () => {
  const exported = markExported({ id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null }, 500);
  const invalidated = invalidateExport(exported);
  assert.equal(invalidated.exported_at, null);
  assert.equal(exported.exported_at, 500, 'invalidateExport must return a new record, not mutate the one it was given');
});

test('invalidateExport rejects a null or undefined session rather than silently spreading it', () => {
  // Fix round 1: `{...null}` is `{}`, which drops `id` and turns a
  // programming error into a key-less IndexedDB put() that fails with a
  // misleading error far from its actual cause. This must fail loudly here.
  assert.throws(() => invalidateExport(null));
  assert.throws(() => invalidateExport(undefined));
});

test('recording a trial after export re-locks the session end to end', () => {
  // The exact sequence persistTrial drives: export a session (closable),
  // then record one more trial (must become un-closable again).
  const fresh = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.equal(canCloseSession(fresh), false);
  const exported = markExported(fresh, 100);
  assert.equal(canCloseSession(exported), true, 'sanity check: exporting must make the session closable');
  const afterNewTrial = invalidateExport(exported);
  assert.equal(canCloseSession(afterNewTrial), false, 'a session that gained data since its last export must not be closable');
  assert.deepEqual(sessionLockState(afterNewTrial, 1), { closable: false, warningVisible: true });
});
