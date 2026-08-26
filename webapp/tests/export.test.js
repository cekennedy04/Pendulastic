import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildExportFiles } from '../src/export.js';
import { PARAM_FIELDS } from '../src/session-store.js';

const params = Object.fromEntries(PARAM_FIELDS.map((k, i) => [k, i]));
const trial = (id, raw) => ({
  id, session_id: 's1', side: 'left', timestamp: 1, algorithm_version: '0.1.0',
  capture_quality: 'clean', release_idx: 3, release_override_idx: null,
  params, raw_jsonl: raw,
});

test('one jsonl file per trial plus one manifest', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', 'a\n'), trial('t2', 'b\n')],
  });
  const names = files.map((f) => f.name);
  assert.equal(files.filter((f) => f.name.endsWith('.jsonl')).length, 2);
  assert.equal(files.filter((f) => f.name.endsWith('.json')).length, 1);
  assert.ok(names.every((n) => n.includes('ANON-7')), `names should carry the participant id: ${names}`);
});

test('each trial file is the raw log verbatim, not re-serialised', () => {
  // The raw JSONL was produced by the Rust exporter against a contract pinned
  // in tests/test_web_export_contract.py. Re-encoding it here would put a
  // second, untested implementation of that contract in the path.
  const raw = '{"t":0.1,"role":"distal","sensor":"accel","v":[0,0,9.81],"phone_ts_ms":100}\n';
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', raw)],
  });
  assert.equal(files.find((f) => f.name.endsWith('.jsonl')).text, raw);
});

test('the manifest carries params but no composite score', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [trial('t1', 'a\n')],
  });
  const manifest = JSON.parse(files.find((f) => f.name.endsWith('.json')).text);
  assert.deepEqual(Object.keys(manifest.trials[0].params).sort(), PARAM_FIELDS.slice().sort());
  assert.ok(!('pt_score' in manifest.trials[0]), 'composite must be derived at read time, never exported as fact');
  assert.equal(manifest.algorithm_version, '0.1.0');
});

test('an empty session produces no files rather than an empty archive', () => {
  const files = buildExportFiles({
    session: { id: 's1', patient_id: 'p1', timestamp: 1, exported_at: null },
    patient: { id: 'p1', clinic_patient_id: 'ANON-7', created_at: 1 },
    trials: [],
  });
  assert.equal(files.length, 0);
});
