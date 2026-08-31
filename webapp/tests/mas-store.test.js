import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  MAS_ORDER, PENDING_MAS_GRADE, STRONGER_LEG_OPTIONS, MAS_FIELDS,
  validateMasForm, makeMasRecord, masIdentity, isPending,
} from '../src/mas-store.js';

// These four constants are transcriptions of the desktop's own definitions.
// A drift here does not fail loudly -- it produces a CSV the desktop's
// append_mas_score() rejects at ingestion time, on the clinician's machine,
// after the phone has already reported a successful export. Pin them exactly.
test('MAS_ORDER matches pendulastic_pt_score.py:531 character for character', () => {
  assert.deepEqual(MAS_ORDER, ['0', '1', '1+', '2', '3', '4']);
});

test('the third grade is the string "1+", never a number', () => {
  assert.equal(MAS_ORDER[2], '1+');
  assert.equal(typeof MAS_ORDER[2], 'string');
  assert.ok(!MAS_ORDER.includes(1.5));
  assert.ok(!MAS_ORDER.includes('1.5'));
});

test('MAS_FIELDS matches mas_validation.DEFAULT_MAS_FIELDS in order', () => {
  assert.deepEqual(MAS_FIELDS, [
    'participant', 'leg', 'condition', 'diagnosis', 'mas_grade',
    'assessed_by', 'assessed_date', 'stronger_leg', 'notes',
    'mas_flexion', 'mas_extension',
  ]);
});

test('STRONGER_LEG_OPTIONS keeps the leading blank meaning "not assessed"', () => {
  assert.deepEqual(STRONGER_LEG_OPTIONS, ['', 'left', 'right', 'equal']);
});

const valid = {
  participant: 'P-014', leg: 'left', condition: '', diagnosis: '',
  mas_grade: '1+', assessed_by: 'CK', assessed_date: '2026-08-31',
  stronger_leg: '', notes: '', mas_flexion: '', mas_extension: '',
};

test('a complete form validates', () => {
  assert.deepEqual(validateMasForm(valid), { ok: true, errors: [] });
});

test('every MAS_ORDER grade is accepted', () => {
  for (const g of MAS_ORDER) {
    assert.equal(validateMasForm({ ...valid, mas_grade: g }).ok, true, g);
  }
});

// The pending sentinel is a supported desktop workflow (flexion/extension
// now, overall grade later) -- see mas_validation.py:63-70.
test('the pending sentinel is accepted for mas_grade', () => {
  assert.equal(validateMasForm({ ...valid, mas_grade: PENDING_MAS_GRADE }).ok, true);
});

// append_mas_score() raises on an empty mas_grade before writing anything,
// so an untouched picker must never reach an export.
test('an unset mas_grade is rejected so -1 can never arrive by default', () => {
  const r = validateMasForm({ ...valid, mas_grade: '' });
  assert.equal(r.ok, false);
  assert.match(r.errors.join(' '), /not yet assessed/);
});

test('a nonsense mas_grade is rejected', () => {
  assert.equal(validateMasForm({ ...valid, mas_grade: '1.5' }).ok, false);
  assert.equal(validateMasForm({ ...valid, mas_grade: '5' }).ok, false);
});

// The inverse rule: blank IS "not assessed" for the optional grades, and the
// sentinel is invalid there -- append_mas_score() raises on both counts.
test('optional grades accept blank and reject the pending sentinel', () => {
  assert.equal(validateMasForm({ ...valid, mas_flexion: '' }).ok, true);
  assert.equal(validateMasForm({ ...valid, mas_flexion: '2' }).ok, true);
  assert.equal(validateMasForm({ ...valid, mas_flexion: PENDING_MAS_GRADE }).ok, false);
  assert.equal(validateMasForm({ ...valid, mas_extension: PENDING_MAS_GRADE }).ok, false);
  assert.equal(validateMasForm({ ...valid, mas_extension: 'x' }).ok, false);
});

test('stronger_leg is a closed enum with blank permitted', () => {
  for (const v of STRONGER_LEG_OPTIONS) {
    assert.equal(validateMasForm({ ...valid, stronger_leg: v }).ok, true, JSON.stringify(v));
  }
  assert.equal(validateMasForm({ ...valid, stronger_leg: 'both' }).ok, false);
  assert.equal(validateMasForm({ ...valid, stronger_leg: PENDING_MAS_GRADE }).ok, false);
});

test('participant and leg are required', () => {
  assert.equal(validateMasForm({ ...valid, participant: '' }).ok, false);
  assert.equal(validateMasForm({ ...valid, leg: '' }).ok, false);
  assert.equal(validateMasForm({ ...valid, leg: 'middle' }).ok, false);
});

test('assessed_date must be ISO yyyy-mm-dd', () => {
  assert.equal(validateMasForm({ ...valid, assessed_date: '31/08/2026' }).ok, false);
  assert.equal(validateMasForm({ ...valid, assessed_date: '' }).ok, false);
});

test('makeMasRecord carries all 11 fields plus its own keys', () => {
  const r = makeMasRecord({ patientId: 'pat-1', form: valid, now: 1700000000000 });
  assert.equal(typeof r.id, 'string');
  assert.equal(r.patient_id, 'pat-1');
  assert.equal(r.updated_at, 1700000000000);
  for (const f of MAS_FIELDS) assert.ok(f in r, `missing ${f}`);
  assert.equal(r.mas_grade, '1+');
});

// A missing key must become '' and never the strings "undefined"/"null",
// which would reach the CSV verbatim.
test('makeMasRecord normalises absent fields to empty strings', () => {
  const r = makeMasRecord({ patientId: 'pat-1', form: { ...valid, notes: undefined, diagnosis: null }, now: 1 });
  assert.equal(r.notes, '');
  assert.equal(r.diagnosis, '');
});

test('makeMasRecord drops keys outside MAS_FIELDS', () => {
  const r = makeMasRecord({ patientId: 'p', form: { ...valid, sneaky: 'x' }, now: 1 });
  assert.equal('sneaky' in r, false);
});

test('masIdentity is the four-part tuple the unique index uses', () => {
  const r = makeMasRecord({ patientId: 'pat-1', form: { ...valid, condition: 'rest' }, now: 1 });
  assert.deepEqual(masIdentity(r), ['pat-1', 'left', 'rest', '2026-08-31']);
});

test('isPending is true only for the sentinel', () => {
  assert.equal(isPending({ mas_grade: PENDING_MAS_GRADE }), true);
  assert.equal(isPending({ mas_grade: '0' }), false);
  assert.equal(isPending({ mas_grade: '' }), false);
});
