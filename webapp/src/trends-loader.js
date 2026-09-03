// Storage and wasm wiring for the trends view.
//
// Unit B Task 2, phase 1. `trends.js` holds the provenance rules and stays
// pure; this module is the only place that touches IndexedDB or the wasm
// scorer, so the rules remain testable without either.
//
// Two queries rather than one, deliberately. `STORES.trials` is indexed
// `by_session` only -- a trial record carries `session_id`, never a patient id
// -- so a per-patient trend walks sessions `by_patient` first and then trials
// `by_session`. The alternative is a new index and a DB_VERSION bump, which
// means a migration over storage that already holds captured trials on a
// clinician's device. Two indexed reads are cheap; a migration that goes wrong
// on a device holding the only copy of a session is not.
//
// `init()` is invoked the same way worker.js does it, with an injectable
// `wasmSource`: the browser lets it fetch the .wasm relative to this module,
// and the Node suite passes bytes read from disk so the artifact under test is
// the one that ships.

import init, {
  pt_scalars_from_params,
  active_healthy_ref_id,
} from './wasm/mobile_imu_core.js';
import { getAll, STORES } from './db.js';
import { buildTrendSeries } from './trends.js';

let ready = null;

/**
 * The scorer `trends.js` expects, bound to the wasm module.
 *
 * Returns `null` when a record cannot be parsed. `pt_scalars_from_params`
 * refuses a record missing any field rather than defaulting it -- a defaulted
 * `first_trough_depth` would mark `r2n` and `phi_max_ratio` unmeasured on a
 * trial that measured them -- so this is a real outcome and the caller reports
 * it as an exclusion rather than plotting a guess.
 */
export async function createScorer({ wasmSource } = {}) {
  ready ??= init({ module_or_path: wasmSource });
  await ready;
  return {
    activeRefId: active_healthy_ref_id(),
    // The wasm getters are read here, in the boundary layer, so `trends.js`
    // never holds a live wasm object. A WasmPtScalars is owned by the wasm
    // heap; copying the scalars out at the seam keeps the pure module free of
    // anything that has to be freed.
    scoreParams: (paramsJson) => {
      const s = pt_scalars_from_params(paramsJson);
      if (!s) return null;
      const out = {
        r2n: s.r2n,
        n: s.n,
        phi_max_ratio: s.phi_max_ratio,
        omega_max_n: s.omega_max_n,
        omega_min_n: s.omega_min_n,
        f: s.f,
        area_ratio: s.area_ratio,
        total: s.total,
        simple: s.simple,
        unmeasured_count: s.unmeasured_count,
        healthy_ref_id: s.healthy_ref_id,
      };
      // wasm-bindgen structs own heap memory; without this the trends view
      // leaks one per trial per redraw, and a longitudinal view redraws.
      if (typeof s.free === 'function') s.free();
      return out;
    },
  };
}

/**
 * Every trial belonging to one patient, across all their sessions.
 *
 * Ordering is left to `buildTrendSeries`, which sorts by timestamp: session
 * order is not trial order, because a session can be reopened.
 */
export async function loadPatientTrials(db, patientId) {
  const sessions = await getAll(db, STORES.sessions, 'by_patient', patientId);
  const perSession = await Promise.all(
    (sessions || []).map((s) => getAll(db, STORES.trials, 'by_session', s.id)),
  );
  return perSession.flat();
}

/**
 * The scored, filtered, annotated series for one patient.
 *
 * @param {IDBDatabase} db
 * @param {string} patientId
 * @param {object} opts
 * @param {'analysis'|'live'} opts.convention  which drift convention to plot.
 *        Trials recorded under the other one are EXCLUDED, not converted --
 *        their params are the other convention's and deriving these would need
 *        the raw log and a full AHRS replay. See trends.js.
 * @param {BufferSource} [opts.wasmSource]  test seam; the browser omits it.
 */
export async function loadTrendSeries(db, patientId, { convention = 'analysis', wasmSource } = {}) {
  const [{ scoreParams, activeRefId }, trials] = await Promise.all([
    createScorer({ wasmSource }),
    loadPatientTrials(db, patientId),
  ]);
  // activeRefId is returned alongside the series rather than left for the
  // caller to re-derive. buildTrendSeries only needs it to compare, so it does
  // not report it; a view that wants to LABEL the reference would otherwise
  // have to infer it from a point that happens not to be flagged -- which is
  // unavailable exactly when every trial predates the current reference.
  return { ...buildTrendSeries(trials, { convention, scoreParams, activeRefId }), activeRefId };
}
