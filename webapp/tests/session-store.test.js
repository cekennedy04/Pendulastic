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
