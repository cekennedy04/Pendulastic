// MAS assessment records, mirroring the desktop's MasEntryPanel form and
// mas_validation.py's schema exactly.
//
// The four constants below are TRANSCRIPTIONS of Python definitions, not
// independent choices. Drift does not fail here -- it produces a CSV that
// append_mas_score() rejects on the clinician's machine, after the phone has
// already reported the export as successful. tests/mas-store.test.js pins
// each one; update both sides together or not at all.

// pendulastic_pt_score.py:531. Strings, and the third grade is the literal
// two-character "1+" -- _valid_grade() is a dict membership test, so any
// numeric form (1.5, 1) raises on ingestion.
export const MAS_ORDER = ['0', '1', '1+', '2', '3', '4'];

// mas_validation.py:71. "Overall grade not yet assessed" -- a supported
// workflow (flexion/extension at the bedside, grade later), deliberately
// kept out of MAS_RANK so pair_pt_and_mas() skips such a row from every
// statistic instead of coding it as an ordinal value.
export const PENDING_MAS_GRADE = '-1';

// mas_validation.py:75. The leading blank is "not assessed" and is valid.
export const STRONGER_LEG_OPTIONS = ['', 'left', 'right', 'equal'];

export const LEG_OPTIONS = ['left', 'right'];

// mas_validation.py:DEFAULT_MAS_FIELDS, in order. This order is the CSV
// column order; see mas-csv.js.
export const MAS_FIELDS = [
  'participant', 'leg', 'condition', 'diagnosis', 'mas_grade',
  'assessed_by', 'assessed_date', 'stronger_leg', 'notes',
  'mas_flexion', 'mas_extension',
];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function str(v) {
  return v === null || v === undefined ? '' : String(v);
}

// Mirrors append_mas_score()'s three validation checks (mas_validation.py:
// 260-277) plus the two fields it takes on trust (participant, leg) and the
// date format MasEntryPanel produces. Returns every error rather than the
// first, so a long form reports all its problems in one pass.
export function validateMasForm(form = {}) {
  const errors = [];

  if (!str(form.participant).trim()) errors.push('Participant ID is required.');

  const leg = str(form.leg);
  if (!LEG_OPTIONS.includes(leg)) errors.push('Choose a leg (left or right).');

  // The asymmetry with the optional grades below is deliberate and load-
  // bearing: append_mas_score() rejects '' here outright, and accepts the
  // sentinel. Requiring an explicit choice is what stops '-1' from being
  // what an untouched picker yields.
  const grade = str(form.mas_grade);
  if (grade === '') {
    errors.push('Choose a MAS grade, or "not yet assessed".');
  } else if (!MAS_ORDER.includes(grade) && grade !== PENDING_MAS_GRADE) {
    errors.push(`MAS grade must be one of ${MAS_ORDER.join(', ')} (or "not yet assessed").`);
  }

  // Inverse rule: blank means "not assessed" and is always valid; the
  // sentinel is NOT valid here -- append_mas_score() raises on any non-blank
  // value that is not a real grade, and '-1' is non-blank.
  for (const f of ['mas_flexion', 'mas_extension']) {
    const v = str(form[f]);
    if (v !== '' && !MAS_ORDER.includes(v)) {
      errors.push(`${f.replace('_', ' ')} must be blank or one of ${MAS_ORDER.join(', ')}.`);
    }
  }

  if (!STRONGER_LEG_OPTIONS.includes(str(form.stronger_leg))) {
    errors.push('Stronger leg must be blank, left, right, or equal.');
  }

  if (!ISO_DATE.test(str(form.assessed_date))) {
    errors.push('Assessed date must be yyyy-mm-dd.');
  }

  return { ok: errors.length === 0, errors };
}

// Copies only MAS_FIELDS, coercing absent values to ''. Anything else the
// caller passes is dropped rather than persisted -- the same discipline
// makeTrialRecord applies, and the reason no field can reach the CSV as the
// string "undefined".
export function makeMasRecord({ patientId, form, now = Date.now() }) {
  const kept = {};
  for (const f of MAS_FIELDS) kept[f] = str(form[f]);
  return {
    id: crypto.randomUUID(),
    patient_id: patientId,
    updated_at: now,
    ...kept,
  };
}

// The tuple the `by_identity` unique index is built over, in index order.
// db.js owns the keyPath itself (MAS_IDENTITY_KEYPATH) because it owns the
// schema; this must produce values in that same order or a lookup silently
// misses. tests/db.test.js cross-checks the two -- see "masIdentity agrees
// with the index keyPath" there.
export function masIdentity(record) {
  return [record.patient_id, record.leg, record.condition, record.assessed_date];
}

export function isPending(record) {
  return str(record && record.mas_grade) === PENDING_MAS_GRADE;
}
