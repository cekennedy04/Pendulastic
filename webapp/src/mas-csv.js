// Writes mas_scores.csv in exactly the shape mas_validation.append_mas_score()
// ingests: DEFAULT_MAS_FIELDS as the header, in order, RFC4180-quoted.
//
// The desktop reads this file with Python's csv module and appends rows to
// the clinician's existing mas_scores.csv. Getting the quoting wrong does not
// throw -- it shifts columns, so a `notes` field containing a comma silently
// becomes a `notes` value plus a bogus `mas_flexion`. That is why `notes` is
// the field most of tests/mas-csv.test.js is about.

import { MAS_FIELDS } from './mas-store.js';

// RFC4180 section 2: a field is quoted if it contains a comma, a double
// quote, CR or LF; inside a quoted field a double quote is escaped by
// doubling it.
export function csvField(value) {
  const s = value === null || value === undefined ? '' : String(value);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// CRLF line endings, per RFC4180. Python's csv.DictReader accepts both, but
// the spec's ending is the one to emit. The trailing terminator matters:
// without it, appending to this file concatenates the last row onto the
// first appended one.
export function buildMasCsv(records = []) {
  const lines = [MAS_FIELDS.join(',')];
  for (const r of records) lines.push(MAS_FIELDS.map((f) => csvField(r[f])).join(','));
  return lines.join('\r\n') + '\r\n';
}
