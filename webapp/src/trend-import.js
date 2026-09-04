// Reads back the artifacts this app exports, so one device can hold a
// participant's whole history rather than only the visits it happened to
// record.
//
// Additive only: an import never deletes or overwrites a local record. The
// worst outcome of a mistaken import is a duplicate refused, not data lost.

import { MAS_FIELDS } from './mas-store.js';

const ACCEPTED = new Set([
  'pendulastic/session-export/v2',
  // v1 predates the mas block. Its trials are still importable, and refusing
  // them would strand every session exported before MAS entry existed.
  'pendulastic/session-export/v1',
]);

export function parseManifest(text) {
  let m;
  try {
    m = JSON.parse(text);
  } catch {
    throw new Error('That file is not a Pendulastic manifest (expected JSON).');
  }
  if (!ACCEPTED.has(m && m.schema)) {
    // Named, not generic: the operator needs to tell an old export from a
    // corrupt file, and only the version string distinguishes them.
    throw new Error(`Unsupported export schema "${m && m.schema}". This app reads v1 and v2.`);
  }
  return {
    schema: m.schema,
    patient: m.patient || {},
    session: m.session,
    trials: m.trials || [],
    mas: m.mas || [],
  };
}

// RFC4180 reader, the inverse of mas-csv.js's writer. Hand-rolled for the same
// reason the writer is: no dependency, and the field set is fixed. A quoted
// field may contain commas, doubled quotes and newlines -- all three occur in
// a clinician's free-text notes, and all three are round-tripped by a test
// that writes with the app's own writer rather than a hand-typed string.
export function parseMasCsv(text) {
  const rows = [];
  let row = [];
  let cur = '';
  let quoted = false;
  let sawAny = false;

  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cur += '"'; i += 1; }
      else if (c === '"') quoted = false;
      else cur += c;
    } else if (c === '"') { quoted = true; sawAny = true; }
    else if (c === ',') { row.push(cur); cur = ''; sawAny = true; }
    else if (c === '\r') { /* the \n that follows ends the record */ }
    else if (c === '\n') { row.push(cur); rows.push(row); row = []; cur = ''; sawAny = false; }
    else { cur += c; sawAny = true; }
  }
  if (sawAny || cur !== '' || row.length) { row.push(cur); rows.push(row); }

  if (rows.length < 2) return [];
  const header = rows[0];
  return rows.slice(1)
    .filter((r) => r.length === header.length)
    .map((r) => {
      const o = Object.fromEntries(header.map((h, i) => [h, r[i]]));
      // Projected through MAS_FIELDS so an imported row has exactly the shape
      // a locally entered one does -- a stray extra column cannot ride along
      // into the store, and a missing one reads as blank rather than absent.
      return Object.fromEntries(MAS_FIELDS.map((f) => [f, o[f] ?? '']));
    });
}

// The identity a MAS row dedupes on -- the same tuple db.js's unique
// by_identity index is built over, minus patient_id, which is resolved at
// import time by clinic_patient_id (a bundle from another device carries a
// different patients.id for the same person).
export function masIdentityKey(row) {
  return `${row.leg}|${row.condition}|${row.assessed_date}`;
}

export function planImport(bundle, existing) {
  const skipped = { trials: 0, mas: 0 };
  const trials = [];
  const mas = [];

  for (const t of bundle.trials || []) {
    if (t.id && existing.trialIds.has(t.id)) { skipped.trials += 1; continue; }
    trials.push(t);
  }
  for (const r of bundle.mas || []) {
    if (existing.masIdentities.has(masIdentityKey(r))) { skipped.mas += 1; continue; }
    mas.push(r);
  }
  const sessions = existing.sessionIds.has(bundle.session.id) ? [] : [bundle.session];
  return { sessions, trials, mas, skipped };
}

// Silence after an import is not acceptable: the operator must be able to tell
// an import that did nothing from one that worked, and "nothing new" is the
// expected outcome of re-importing a bundle already on the device.
export function importSummary({ trials, mas, skipped }) {
  if (trials === 0 && mas === 0) {
    return `Nothing new — ${skipped.trials} trial(s) and ${skipped.mas} assessment(s) were already on this device.`;
  }
  return `Imported ${trials} trial(s) and ${mas} assessment(s); skipped ${skipped.trials} duplicate trial(s) and ${skipped.mas} duplicate assessment(s).`;
}
