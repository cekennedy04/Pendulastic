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

export function buildExportFiles({ session, patient, trials }) {
  if (!trials || trials.length === 0) return [];
  const base = `pendulastic-${patient.clinic_patient_id}-${stamp(session.timestamp)}`;

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
    algorithm_version: trials[0].algorithm_version,
    patient: { clinic_patient_id: patient.clinic_patient_id },
    session: { id: session.id, timestamp: session.timestamp },
    trials: trials.map((t, i) => ({
      file: `${base}-trial${i + 1}.jsonl`,
      side: t.side,
      timestamp: t.timestamp,
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
