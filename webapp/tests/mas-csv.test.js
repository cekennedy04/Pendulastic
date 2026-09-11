import { test } from 'node:test';
import assert from 'node:assert/strict';
import { csvField, buildMasCsv } from '../src/mas-csv.js';
import {
  MAS_FIELDS, MAS_ORDER, PENDING_MAS_GRADE, STRONGER_LEG_OPTIONS,
} from '../src/mas-store.js';

const row = {
  participant: 'P-014', leg: 'left', condition: 'rest', diagnosis: 'stroke',
  mas_grade: '1+', assessed_by: 'CK', assessed_date: '2026-08-31',
  stronger_leg: 'right', notes: 'none', mas_flexion: '2', mas_extension: '',
};

test('the header is DEFAULT_MAS_FIELDS in order', () => {
  const [header] = buildMasCsv([row]).split('\r\n');
  assert.equal(header,
    'participant,leg,condition,diagnosis,mas_grade,assessed_by,' +
    'assessed_date,stronger_leg,notes,mas_flexion,mas_extension');
  assert.equal(header.split(',').length, MAS_FIELDS.length);
});

test('a row emits its columns in header order', () => {
  const [, first] = buildMasCsv([row]).split('\r\n');
  assert.equal(first, 'P-014,left,rest,stroke,1+,CK,2026-08-31,right,none,2,');
});

test('the file ends with a terminating CRLF', () => {
  assert.ok(buildMasCsv([row]).endsWith('\r\n'));
});

test('an empty record list still emits the header', () => {
  assert.equal(buildMasCsv([]), MAS_FIELDS.join(',') + '\r\n');
});

// RFC4180. `notes` is free text typed at the bedside, so all four of these
// are reachable in practice.
test('fields containing a comma are quoted', () => {
  assert.equal(csvField('a,b'), '"a,b"');
});

test('embedded double quotes are doubled inside a quoted field', () => {
  assert.equal(csvField('he said "hi"'), '"he said ""hi"""');
});

test('newlines and carriage returns force quoting', () => {
  assert.equal(csvField('a\nb'), '"a\nb"');
  assert.equal(csvField('a\r\nb'), '"a\r\nb"');
});

test('ordinary values are not quoted', () => {
  assert.equal(csvField('plain'), 'plain');
  assert.equal(csvField('1+'), '1+');
  assert.equal(csvField(''), '');
});

test('null and undefined become empty, never the strings null/undefined', () => {
  assert.equal(csvField(null), '');
  assert.equal(csvField(undefined), '');
  const line = buildMasCsv([{ ...row, notes: undefined, diagnosis: null }]).split('\r\n')[1];
  assert.ok(!line.includes('undefined'));
  assert.ok(!line.includes('null'));
});

test('a notes field with every hostile character round-trips through one row', () => {
  const nasty = 'quote " comma , newline \n done';
  const line = buildMasCsv([{ ...row, notes: nasty }]).split('\r\n').slice(1).join('\r\n');
  assert.ok(line.includes('"quote "" comma , newline \n done"'));
});

// ---- Round-trip against append_mas_score()'s own rules -------------------
// A JS transcription of mas_validation.py:260-277. Its purpose is to fail
// HERE, in CI, rather than on a clinician's laptop after the phone has
// already reported a successful export.
function appendMasScoreWouldAccept(r) {
  const grade = r.mas_grade ?? '';
  if (!(MAS_ORDER.includes(grade) || grade === PENDING_MAS_GRADE)) return false;
  if (!STRONGER_LEG_OPTIONS.includes(r.stronger_leg ?? '')) return false;
  for (const f of ['mas_flexion', 'mas_extension']) {
    const v = r[f] ?? '';
    if (v && !MAS_ORDER.includes(v)) return false;
  }
  return true;
}

test('every grade this app can emit is one append_mas_score accepts', () => {
  for (const g of [...MAS_ORDER, PENDING_MAS_GRADE]) {
    assert.equal(appendMasScoreWouldAccept({ ...row, mas_grade: g }), true, g);
  }
});

test('an empty mas_grade would be rejected by the desktop', () => {
  assert.equal(appendMasScoreWouldAccept({ ...row, mas_grade: '' }), false);
});

test('the pending sentinel in an optional grade would be rejected', () => {
  assert.equal(appendMasScoreWouldAccept({ ...row, mas_flexion: PENDING_MAS_GRADE }), false);
  assert.equal(appendMasScoreWouldAccept({ ...row, stronger_leg: PENDING_MAS_GRADE }), false);
});

test('a pending row survives the whole pipeline as -1, not as blank', () => {
  const line = buildMasCsv([{ ...row, mas_grade: PENDING_MAS_GRADE }]).split('\r\n')[1];
  assert.equal(line.split(',')[MAS_FIELDS.indexOf('mas_grade')], '-1');
});
