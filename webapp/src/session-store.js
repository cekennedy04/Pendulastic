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

/// The capture protocol a trial was physically recorded under.
///
/// 1 is every recording made before settle-based termination existed, and is
/// signalled BY ABSENCE -- those records carry no such field. Bump this
/// whenever a change alters what a trial physically IS, not merely how it is
/// scored; algorithm_version already covers the latter.
export const CAPTURE_PROTOCOL_VERSION = 2;

/// The participant a quick test records against.
///
/// A real id, not null. Recording with `patient_id: null` would re-create
/// exactly the unanchored rows db.js's backfill exists to prevent, and the
/// trends join would skip them silently rather than visibly. This way a quick
/// test is a labelled path, not a hole in the schema.
export const QUICK_TEST_PATIENT_ID = '__quick_test__';

/// What a quick-test participant is called wherever a name is shown or
/// exported. Deliberately not a plausible clinic id.
export const QUICK_TEST_PATIENT_LABEL = 'QUICK-TEST';

export function isQuickTest(record) {
  return Boolean(record && (record.quick_test || record.patient_id === QUICK_TEST_PATIENT_ID));
}

/// The built-in participant a quick test records against. A fixed id, so
/// repeated quick tests reuse one row instead of littering the participant
/// list, and `legacy: false` so db.js's legacy patches never touch it.
export function makeQuickTestPatient() {
  return {
    id: QUICK_TEST_PATIENT_ID,
    clinic_patient_id: QUICK_TEST_PATIENT_LABEL,
    created_at: Date.now(),
    legacy: false,
    quick_test: true,
  };
}

export function isExcluded(trial) {
  return Boolean(trial && trial.excluded_at != null);
}

/// Exclude a trial from the counts, WITHOUT deleting it.
///
/// A timestamp rather than a boolean so the record says when the call was
/// made, not merely that it was. Note the honest limit: `includeTrial` clears
/// both fields, so on-device the exclusion is current state, not an audit
/// log. The durable record of an exclusion is the exported bundle, which
/// carries it.
export function excludeTrial(trial, reason = '', at = Date.now()) {
  return { ...trial, excluded_at: at, excluded_reason: String(reason || '') };
}

export function includeTrial(trial) {
  return { ...trial, excluded_at: null, excluded_reason: null };
}

/// Trials that count: toward the session's trial count, and toward the export
/// gate. Excluded trials are still stored, still listed and still exported.
export function activeTrials(trials) {
  return (trials || []).filter((t) => !isExcluded(t));
}

/// Trials a longitudinal series may use. Stricter than `activeTrials`: a quick
/// test is excluded unconditionally and cannot be un-excluded into a trend,
/// because it was never recorded against a real participant.
export function trendEligible(trials) {
  return (trials || []).filter((t) => !isExcluded(t) && !isQuickTest(t));
}

export function makeTrialRecord({
  sessionId, side, params, trajectory, rawJsonl, algorithmVersion,
  captureQuality = 'clean', releaseIdx = 0, releaseOverrideIdx = null,
  unmeasured = [], driftCorrection = 'live', settleTargetS = 5.0,
  quickTest = false,
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
    // Which capture protocol physically produced this trial -- distinct from
    // algorithm_version, which says how it was SCORED. Trials terminated by
    // the settle rule are not directly comparable with the earlier
    // operator-terminated ones, because tail length is exactly what
    // neutral_deg is computed from. Absent means 1: everything recorded
    // before this existed carries no such field, and a consumer must read
    // that as version 1 rather than as an error.
    capture_protocol_version: CAPTURE_PROTOCOL_VERSION,
    settle_target_s: settleTargetS,
    release_idx: releaseIdx,
    // Which of the seven scored parameters were placeholders rather than
    // measurements, straight from mobile-imu-core's unmeasured_params(). The
    // composite score is deliberately NOT persisted -- it is derived at read
    // time because HEALTHY_REF moves -- but this is a property of the
    // MEASUREMENT, not of the reference, so it belongs in the archive. Carried
    // through from Rust rather than recomputed here, so the rule has one home.
    unmeasured,
    // Which drift-correction convention produced `params`: 'analysis'
    // (detrend=true, matching pt_report_common -> run_pt_analysis and the
    // cohort reports) or 'live' (detrend=false, matching pendulastic_app's
    // live view). The two disagree on the MAS grade for 63 of 197 real
    // trials, so a stored number without its convention cannot be compared
    // to anything later.
    drift_correction: driftCorrection,
    release_override_idx: releaseOverrideIdx,
    // Exclusion is a RECORD, not an erasure: an excluded trial keeps its
    // params, its raw log and its place in the list, and still exports. It
    // only stops counting. Nothing in this app deletes trial data -- on a
    // phone the record is the only copy until it has been exported.
    excluded_at: null,
    excluded_reason: null,
    // Recorded outside a real participant. Kept on the TRIAL as well as being
    // derivable from patient_id, because an exported bundle is read without
    // the patient row beside it.
    quick_test: Boolean(quickTest),
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
