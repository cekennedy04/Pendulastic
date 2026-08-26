// Record shapes and the export gate.
//
// The gate is the whole durability design: IndexedDB can be evicted by the
// platform, so a session that has never been exported has exactly one copy of
// its data, on a device that may erase it. Refusing to close such a session is
// what keeps that from happening quietly.

// Exactly the scalar fields of PtParams, in scoring.rs order. Renaming any of
// them breaks traceability with the desktop corpus and the golden fixtures.
export const PARAM_FIELDS = [
  'r2n', 'n', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio',
  'omega_peak_deg_s', 'a0_deg', 'a1_deg', 'first_trough_depth', 'neutral_deg',
  'neutral_deg_raw', 'pre_release_deg', 'quality_warn', 'phi_negated',
  'spasticity_type', 'p_plus', 'p_minus', 'p_total',
];

function uuid() {
  return crypto.randomUUID();
}

export function makeTrialRecord({
  sessionId, side, params, trajectory, rawJsonl, algorithmVersion,
  captureQuality = 'clean', releaseIdx = 0, releaseOverrideIdx = null,
}) {
  // Copy only the known fields. Anything else the caller passes -- notably a
  // composite score -- is dropped rather than persisted.
  const kept = {};
  for (const k of PARAM_FIELDS) kept[k] = params[k];
  return {
    id: uuid(),
    session_id: sessionId,
    side,
    timestamp: Date.now(),
    algorithm_version: algorithmVersion,
    capture_quality: captureQuality,
    release_idx: releaseIdx,
    release_override_idx: releaseOverrideIdx,
    params: kept,
    // Plain object `{t, angle_deg, release_idx, peak_idx, trough_idx,
    // neutral_deg}` from worker.js's `result` message -- not an ArrayBuffer
    // (a prior version of this comment said otherwise; the shape below was
    // always what app.js actually stores). `release_idx`/`peak_idx`/
    // `trough_idx` are indices into the same `t`/`angle_deg` arrays, not a
    // second time base -- drawWaveform needs them to mark the release point
    // and accepted peaks/troughs, and stripping them here would force a
    // future replay view to recompute them from scratch.
    trajectory,
    raw_jsonl: rawJsonl,
  };
}

export function makeSessionRecord({ patientId }) {
  return { id: uuid(), patient_id: patientId, timestamp: Date.now(), exported_at: null };
}

export function canCloseSession(session) {
  return Boolean(session && session.exported_at != null);
}

export function markExported(session, at = Date.now()) {
  return { ...session, exported_at: at };
}
