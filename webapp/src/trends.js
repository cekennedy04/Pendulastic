// Longitudinal trend data: turning stored trials into a comparable series.
//
// Unit B Task 2. Pure functions over plain records -- no IndexedDB, no DOM, no
// wasm import. The wasm scorer arrives as an injected function so this module
// is testable under `node --test`, which is the same reason params_json lives
// outside wasm.rs on the Rust side.
//
// Two provenance dimensions, and they are NOT the same kind of problem:
//
//   healthy_ref_id     recoverable.   The composite score is not persisted --
//                                     session-store.js: "derived at read time
//                                     because HEALTHY_REF moves" -- so every
//                                     point is recomputed against the active
//                                     reference and annotated where the trial
//                                     was captured under an older one.
//
//   drift_correction   NOT recoverable. A record holds params from ONE
//                                     convention (app.js: `storedParams =
//                                     useDetrended ? paramsDetrended :
//                                     params`). Deriving the other needs the
//                                     raw log and a full AHRS replay, seconds
//                                     per trial. So a mismatched trial is
//                                     genuinely absent from the series, not
//                                     annotatable.
//
// Conflating them would produce a line that silently drops points while the
// caption still claims the full n -- the same failure shape as scoring an
// unmeasurable trial as zero, which is what this project spent a week undoing.

export const CONVENTIONS = ['analysis', 'live'];

/** Fields a trial record must carry to be plottable at all. */
const REQUIRED = ['id', 'timestamp', 'params', 'drift_correction'];

/**
 * Why a trial is not on the line. Named rather than boolean, because
 * "we cannot compare this" and "this record is broken" want different
 * sentences in front of a clinician.
 */
export const EXCLUDED = {
  convention: 'convention-mismatch',
  unparseable: 'unparseable-params',
  incomplete: 'incomplete-record',
};

/**
 * Build a trend series from stored trial records.
 *
 * @param {object[]} trials      records as session-store.makeTrialRecord wrote them
 * @param {object}   opts
 * @param {string}   opts.convention      'analysis' or 'live' -- the series is
 *                                        built from trials matching it, and
 *                                        ONLY those. See the module note.
 * @param {Function} opts.scoreParams     (paramsJson) => scalars | null, i.e.
 *                                        wasm `pt_scalars_from_params`.
 * @param {string}   opts.activeRefId     wasm `active_healthy_ref_id()`.
 * @returns {{points: object[], excluded: object[], summary: object}}
 */
export function buildTrendSeries(trials, { convention, scoreParams, activeRefId }) {
  if (!CONVENTIONS.includes(convention)) {
    throw new Error(`unknown convention: ${convention}`);
  }
  const points = [];
  const excluded = [];

  for (const t of (trials || [])) {
    if (!t || REQUIRED.some((k) => t[k] === undefined || t[k] === null)) {
      excluded.push({ id: t && t.id, reason: EXCLUDED.incomplete });
      continue;
    }
    // Filter, not convert. The other convention's params are not in this
    // record and cannot be derived from it.
    if (t.drift_correction !== convention) {
      excluded.push({ id: t.id, reason: EXCLUDED.convention,
                      recorded: t.drift_correction });
      continue;
    }
    const scalars = scoreParams(JSON.stringify(t.params));
    if (!scalars) {
      // params_from_json refuses a record missing any field rather than
      // defaulting it, so this is a real outcome and not a theoretical one.
      excluded.push({ id: t.id, reason: EXCLUDED.unparseable });
      continue;
    }
    const storedRefId = scalars.healthy_ref_id;
    points.push({
      id: t.id,
      timestamp: t.timestamp,
      side: t.side,
      total: scalars.total,
      simple: scalars.simple,
      contributions: {
        r2n: scalars.r2n,
        n: scalars.n,
        phi_max_ratio: scalars.phi_max_ratio,
        omega_max_n: scalars.omega_max_n,
        omega_min_n: scalars.omega_min_n,
        f: scalars.f,
        area_ratio: scalars.area_ratio,
      },
      unmeasuredCount: scalars.unmeasured_count,
      // Carried through rather than recomputed here: it is a property of the
      // MEASUREMENT (session-store.js says so) and the stored list is the
      // archive's own answer.
      unmeasured: Array.isArray(t.unmeasured) ? t.unmeasured : [],
      healthyRefId: storedRefId,
      // Every point IS recomputed; this only says whether doing so moved it
      // onto a different reference than the trial was captured under.
      recomputedAcrossReference: storedRefId !== activeRefId,
      algorithmVersion: t.algorithm_version,
    });
  }

  points.sort((a, b) => a.timestamp - b.timestamp);
  return { points, excluded, summary: summarise(points, excluded, convention) };
}

function summarise(points, excluded, convention) {
  const byReason = {};
  for (const e of excluded) byReason[e.reason] = (byReason[e.reason] || 0) + 1;
  return {
    convention,
    shown: points.length,
    total: points.length + excluded.length,
    excludedByReason: byReason,
    // How many plotted points were captured under a different reference. Not
    // an error -- they are recomputed and comparable -- but the count belongs
    // on screen, because "recalibrated halfway through" is a real thing to
    // know about a trajectory.
    recomputedAcrossReference: points.filter((p) => p.recomputedAcrossReference).length,
  };
}

/**
 * One sentence for the view header. Silence about exclusions is the failure
 * this exists to prevent: a chart that quietly drops 8 of 20 trials still
 * looks like a complete history.
 */
export function summaryLine(summary) {
  const { shown, total, convention, excludedByReason } = summary;
  if (total === 0) return 'No trials yet.';
  let line = `Showing ${shown} of ${total} trials (${convention} convention)`;
  const parts = [];
  const mismatch = excludedByReason[EXCLUDED.convention] || 0;
  const unparseable = excludedByReason[EXCLUDED.unparseable] || 0;
  const incomplete = excludedByReason[EXCLUDED.incomplete] || 0;
  if (mismatch) {
    const other = convention === 'analysis' ? 'live' : 'analysis';
    parts.push(`${mismatch} recorded under the ${other} convention`);
  }
  if (unparseable) parts.push(`${unparseable} unreadable`);
  if (incomplete) parts.push(`${incomplete} incomplete`);
  if (parts.length) line += `; ${parts.join(', ')} excluded`;
  return `${line}.`;
}

/**
 * Tooltip for a point captured under a different reference.
 *
 * The final clause is the clinically important one. Nothing stored was
 * modified -- scores are never persisted, only params are -- and a clinician
 * reading "the score changed" would reasonably conclude the patient did.
 */
export function referenceTooltip(point, activeRefId) {
  if (!point.recomputedAcrossReference) return null;
  return `Recomputed against reference ${activeRefId}. This trial was captured `
       + `when reference ${point.healthyRefId} was active; the stored `
       + `measurements are unchanged.`;
}
