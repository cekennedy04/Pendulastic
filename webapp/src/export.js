// Session export. These files are the archive of record -- IndexedDB is a
// cache that the platform may erase.
//
// One .jsonl per trial, byte-for-byte as the Rust exporter produced it, plus
// one .json manifest. No zip: navigator.share takes an array of files, and a
// loose .jsonl replays through the desktop pipeline directly, where an archive
// would have to be extracted first.

import { PARAM_FIELDS } from './session-store.js';

function stamp(ms) {
  return new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\..+/, '');
}

// clinic_patient_id is unconstrained free text -- db.js keys patients by a
// UUID and never validates the field, and there is no app.js form yet to add
// its own constraint. This module is the only place that turns it into a
// filename, and that filename reaches new File(...), an <a download>
// attribute, and an iOS share sheet, each of which treats "/", a leading
// ".", and non-ASCII differently. Collapse anything outside [A-Za-z0-9_-]
// into a single "_" so the result is safe everywhere, and never let it
// sanitise away to nothing -- an export must never end up with a nameless or
// empty-stemmed file.
function sanitizeForFilename(raw) {
  const cleaned = String(raw ?? '').replace(/[^A-Za-z0-9_-]+/g, '_');
  return cleaned || 'unknown-patient';
}

export function buildExportFiles({ session, patient, trials }) {
  if (!trials || trials.length === 0) return [];
  const patientPart = sanitizeForFilename(patient.clinic_patient_id);
  const base = `pendulastic-${patientPart}-${stamp(session.timestamp)}`;

  const files = trials.map((t, i) => ({
    name: `${base}-trial${i + 1}.jsonl`,
    type: 'application/x-ndjson',
    // Verbatim. Re-encoding would introduce a second implementation of a
    // contract whose every failure mode is silent.
    text: t.raw_jsonl,
  }));

  const manifest = {
    schema: 'pendulastic/session-export/v1',
    exported_at: new Date().toISOString(),
    // A session-level default; the trial-level value below is the one that
    // is actually true if the app updated mid-session.
    algorithm_version: trials[0].algorithm_version,
    patient: { clinic_patient_id: patient.clinic_patient_id },
    session: { id: session.id, timestamp: session.timestamp },
    trials: trials.map((t, i) => ({
      file: `${base}-trial${i + 1}.jsonl`,
      side: t.side,
      timestamp: t.timestamp,
      // Each trial record carries its own algorithm_version -- a session is
      // not guaranteed to complete under a single app version, so this must
      // not be assumed to match the session-level field above.
      algorithm_version: t.algorithm_version,
      capture_quality: t.capture_quality,
      release_idx: t.release_idx,
      release_override_idx: t.release_override_idx,
      // The 20 scalars only. The composite score and zone are derived at read
      // time against the current HEALTHY_REF, which is still being
      // recalibrated -- exporting one would freeze a moving reference.
      params: Object.fromEntries(PARAM_FIELDS.map((k) => [k, t.params[k]])),
    })),
  };
  files.push({ name: `${base}-manifest.json`, type: 'application/json', text: JSON.stringify(manifest, null, 2) });
  return files;
}

export async function shareFiles(files, { navigatorRef = navigator } = {}) {
  // Safety-critical: task 6 gates session-close on export succeeding. A
  // no-op that returned 'downloaded' would let a session be marked exported
  // with no byte ever having left the device, defeating the durability
  // design. Throw rather than return a value a caller could mistake for
  // success -- do not depend on the call site's own `files.length === 0`
  // guard being remembered forever.
  if (!files || files.length === 0) {
    throw new Error('shareFiles called with no files to share');
  }
  const fileObjs = files.map((f) => new File([f.text], f.name, { type: f.type }));
  if (navigatorRef.canShare && navigatorRef.canShare({ files: fileObjs })) {
    await navigatorRef.share({ files: fileObjs, title: 'Pendulastic session' });
    return 'shared';
  }
  // showSaveFilePicker does not exist in Safari; a download anchor is the
  // only fallback that works there.
  for (const f of files) {
    const url = URL.createObjectURL(new Blob([f.text], { type: f.type }));
    const a = document.createElement('a');
    a.href = url; a.download = f.name; a.click();
    URL.revokeObjectURL(url);
  }
  return 'downloaded';
}
