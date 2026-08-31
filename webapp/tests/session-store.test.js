import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeTrialRecord, canCloseSession, markExported, PARAM_FIELDS } from '../src/session-store.js';

const params = Object.fromEntries(PARAM_FIELDS.map((k, i) => [k, i]));

test('a trial record carries exactly the 20 PtParams fields, no more', () => {
  const r = makeTrialRecord({ sessionId: 's1', side: 'left', params, trajectory: new ArrayBuffer(8), rawJsonl: 'x', algorithmVersion: '0.1.0' });
  assert.deepEqual(Object.keys(r.params).sort(), PARAM_FIELDS.slice().sort());
});

test('the composite score and zone are never stored', () => {
  const r = makeTrialRecord({ sessionId: 's1', side: 'left', params: { ...params, pt_score: 0.4, zone: 'impaired' }, trajectory: new ArrayBuffer(8), rawJsonl: 'x', algorithmVersion: '0.1.0' });
  // HEALTHY_REF is still being recalibrated; a persisted composite would let a
  // trend line silently compare scores from different scorers.
  assert.ok(!('pt_score' in r.params), 'pt_score must not be persisted');
  assert.ok(!('zone' in r.params), 'zone must not be persisted');
  assert.ok(!('pt_score' in r), 'pt_score must not be persisted at the record level either');
});

test('a session cannot close until it has been exported', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.equal(canCloseSession(s), false);
  assert.equal(canCloseSession(markExported(s, 12345)), true);
});

test('markExported does not mutate the session it was given', () => {
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  markExported(s, 12345);
  assert.equal(s.exported_at, null, 'markExported must return a new record, not mutate');
});

test('exporting at epoch zero still counts as exported', () => {
  // Date.now() === 0 is a legitimate (if rare) instant. canCloseSession must
  // treat "exported_at is present" as the gate, not "exported_at is truthy" --
  // a truthiness check would make 0 indistinguishable from null and leave an
  // actually-exported session permanently un-closable.
  const s = { id: 's1', patient_id: 'p1', timestamp: 0, exported_at: null };
  assert.equal(canCloseSession(markExported(s, 0)), true);
});

test('a trial keeps its raw log, which is the archive of record', () => {
  const r = makeTrialRecord({ sessionId: 's1', side: 'left', params, trajectory: new ArrayBuffer(8), rawJsonl: 'line\n', algorithmVersion: '0.1.0' });
  assert.equal(r.raw_jsonl, 'line\n');
});


// -- unmeasured survives into the archive (2026-08-31) ---------------------
// The composite score is deliberately never persisted (it is derived at read
// time because HEALTHY_REF moves). `unmeasured` is different: it describes the
// MEASUREMENT, not the reference, so a trial where two parameters were never
// measurable must say so in the export rather than leaving a reader to infer
// it from first_trough_depth being 0.

test('a trial records which parameters were never measured', () => {
  const r = makeTrialRecord({
    sessionId: 's1', side: null, params, trajectory: new ArrayBuffer(8),
    rawJsonl: 'x', algorithmVersion: '0.1.0',
    unmeasured: ['r2n', 'phi_max_ratio'],
  });
  assert.deepEqual(r.unmeasured, ['r2n', 'phi_max_ratio']);
});

test('a fully measured trial records an empty list, not a missing key', () => {
  const r = makeTrialRecord({
    sessionId: 's1', side: null, params, trajectory: new ArrayBuffer(8),
    rawJsonl: 'x', algorithmVersion: '0.1.0',
  });
  assert.ok('unmeasured' in r, 'the key must always be present');
  assert.deepEqual(r.unmeasured, []);
});
