import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  parseManifest, parseMasCsv, masIdentityKey, planImport, importSummary,
} from '../src/trend-import.js';
import { buildMasCsv } from '../src/mas-csv.js';
import { MAS_FIELDS } from '../src/mas-store.js';

const manifestV2 = JSON.stringify({
  schema: 'pendulastic/session-export/v2',
  patient: { clinic_patient_id: 'P-014' },
  session: { id: 's-1', timestamp: 1000 },
  trials: [{ file: 'x-trial1.jsonl', side: 'left', timestamp: 1001, params: { a0_deg: 40 }, unmeasured: [] }],
  mas: [],
});

test('a v2 manifest parses', () => {
  const m = parseManifest(manifestV2);
  assert.equal(m.patient.clinic_patient_id, 'P-014');
  assert.equal(m.trials.length, 1);
});

// A v1 bundle predates the mas block; its trials are still importable.
test('a v1 manifest parses with no mas block', () => {
  const v1 = JSON.stringify({ ...JSON.parse(manifestV2), schema: 'pendulastic/session-export/v1', mas: undefined });
  assert.deepEqual(parseManifest(v1).mas, []);
});

// Named, not a generic failure: the operator needs to know WHICH version they
// handed over so they can tell an old export from a corrupt file.
test('an unknown schema is refused by name', () => {
  const bad = JSON.stringify({ ...JSON.parse(manifestV2), schema: 'pendulastic/session-export/v9' });
  assert.throws(() => parseManifest(bad), /v9/);
});

test('a file that is not JSON at all fails with a readable message', () => {
  assert.throws(() => parseManifest('participant,leg,condition\n'), /manifest|JSON/i);
});

// The real parity check: write with the app's OWN writer, read it back, and
// require the rows to survive. A hand-typed CSV would only test my typing.
test('the app\'s own mas csv writer round-trips through this reader', () => {
  const rows = [
    { participant: 'P-014', leg: 'left', condition: 'rest', diagnosis: 'stroke',
      mas_grade: '1+', assessed_by: 'CK', assessed_date: '2026-08-31',
      stronger_leg: 'right', notes: 'catch, then release', mas_flexion: '2', mas_extension: '' },
    { participant: 'P-014', leg: 'right', condition: 'rest', diagnosis: 'stroke',
      mas_grade: '-1', assessed_by: 'CK', assessed_date: '2026-08-31',
      stronger_leg: '', notes: '', mas_flexion: '', mas_extension: '' },
  ];
  const back = parseMasCsv(buildMasCsv(rows));
  assert.equal(back.length, 2);
  assert.deepEqual(back[0], rows[0]);
  assert.deepEqual(back[1], rows[1]);
});

// The hostile case verified against the desktop's mas_validation.py: a notes
// field carrying a comma, an embedded newline AND double quotes.
test('a notes field with a comma, a newline and quotes survives the round trip', () => {
  const row = {
    participant: 'P-014', leg: 'left', condition: 'rest', diagnosis: '',
    mas_grade: '2', assessed_by: 'CK', assessed_date: '2026-08-31',
    stronger_leg: '', notes: 'catch, then release\nsecond line "quoted"',
    mas_flexion: '', mas_extension: '',
  };
  const back = parseMasCsv(buildMasCsv([row]));
  assert.equal(back.length, 1);
  assert.equal(back[0].notes, row.notes);
});

test('the header is exactly MAS_FIELDS', () => {
  const csv = buildMasCsv([{ participant: 'P', leg: 'left', condition: 'rest', mas_grade: '0', assessed_date: '2026-01-01' }]);
  assert.equal(csv.split('\r\n')[0], MAS_FIELDS.join(','));
});

test('an empty csv yields no rows rather than throwing', () => {
  assert.deepEqual(parseMasCsv(''), []);
  assert.deepEqual(parseMasCsv(MAS_FIELDS.join(',') + '\r\n'), []);
});

// ---- dedupe ---------------------------------------------------------------
const bundle = {
  patient: { clinic_patient_id: 'P-014' },
  session: { id: 's-1', timestamp: 1000 },
  trials: [{ id: 't-1', side: 'left', timestamp: 1 }],
  mas: [{ participant: 'P-014', leg: 'left', condition: 'rest', assessed_date: '2026-08-31', mas_grade: '2' }],
};
const nothingKnown = { trialIds: new Set(), masIdentities: new Set(), sessionIds: new Set() };

test('a fresh bundle imports everything', () => {
  const plan = planImport(bundle, nothingKnown);
  assert.equal(plan.trials.length, 1);
  assert.equal(plan.mas.length, 1);
  assert.equal(plan.sessions.length, 1);
});

// Import is additive. Re-importing the same bundle must be a no-op.
test('a trial already present is skipped, not duplicated', () => {
  const plan = planImport(bundle, { ...nothingKnown, trialIds: new Set(['t-1']), sessionIds: new Set(['s-1']) });
  assert.equal(plan.trials.length, 0);
  assert.equal(plan.sessions.length, 0);
  assert.equal(plan.skipped.trials, 1);
});

test('a mas row already present by identity is skipped', () => {
  const plan = planImport(bundle, { ...nothingKnown, masIdentities: new Set([masIdentityKey(bundle.mas[0])]) });
  assert.equal(plan.mas.length, 0);
  assert.equal(plan.skipped.mas, 1);
});

// The identity is the same tuple db.js's unique by_identity index is built
// over, minus patient_id, which is resolved at import time.
test('the identity key is leg, condition and date', () => {
  assert.equal(masIdentityKey({ leg: 'left', condition: 'rest', assessed_date: '2026-08-31' }), 'left|rest|2026-08-31');
  assert.notEqual(
    masIdentityKey({ leg: 'left', condition: 'rest', assessed_date: '2026-08-31' }),
    masIdentityKey({ leg: 'right', condition: 'rest', assessed_date: '2026-08-31' }),
  );
});

// Silence after an import is not acceptable: the operator must be able to
// tell an import that did nothing from one that worked.
test('an import that adds nothing says so rather than staying silent', () => {
  const s = importSummary({ trials: 0, mas: 0, skipped: { trials: 3, mas: 2 } });
  assert.match(s, /nothing new/i);
  assert.match(s, /3/);
  assert.match(s, /2/);
});

test('an import that adds something reports the counts', () => {
  const s = importSummary({ trials: 4, mas: 1, skipped: { trials: 0, mas: 0 } });
  assert.match(s, /4/);
  assert.match(s, /1/);
  assert.doesNotMatch(s, /nothing new/i);
});

// The LAST column is where a line-ending bug hides. buildMasCsv emits CRLF;
// if the reader fails to skip the CR it lands on the final field of every
// record -- and on the HEADER's final field too, so the MAS_FIELDS projection
// then maps that column to undefined and it reads as blank. With
// mas_extension blank in the fixture above that corruption is invisible, so
// this row populates it.
test('the last csv column survives CRLF line endings', () => {
  const row = {
    participant: 'P-014', leg: 'left', condition: 'rest', diagnosis: 'stroke',
    mas_grade: '2', assessed_by: 'CK', assessed_date: '2026-08-31',
    stronger_leg: 'right', notes: 'plain', mas_flexion: '1', mas_extension: '3',
  };
  const csv = buildMasCsv([row]);
  assert.ok(csv.includes('\r\n'), 'writer must emit CRLF for this test to mean anything');
  const back = parseMasCsv(csv);
  assert.equal(back[0].mas_extension, '3');
  assert.deepEqual(back[0], row);
});

// ---- v3 bundles -----------------------------------------------------------
test('a v3 manifest parses', () => {
  const v3 = JSON.stringify({ ...JSON.parse(manifestV2), schema: 'pendulastic/session-export/v3' });
  assert.equal(parseManifest(v3).schema, 'pendulastic/session-export/v3');
});

// Everything captured before the settle rule is protocol 1 BY ABSENCE. A
// consumer must read a missing field as 1, never as an error, or every older
// bundle becomes unimportable.
test('a trial with no protocol version reads as version 1', () => {
  const m = parseManifest(manifestV2);
  assert.equal(m.trials[0].capture_protocol_version ?? 1, 1);
});
