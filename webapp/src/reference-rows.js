// Per-parameter comparison against the healthy reference, as NUMBERS.
//
// Deliberately not a verdict. `ZONE_CLASSIFICATION_CALIBRATED` is false, the
// reference is marked "PROVISIONAL and expected to move again", and the app's
// own disclaimer says it "cannot be reproduced from the data it is documented
// as coming from". Colour-coded in-range/out-of-range bands would re-instate,
// per parameter, exactly the classification the composite already suppresses
// -- on a reference the app itself says it cannot defend.
//
// What is shown instead: the measured value, the reference it is being
// compared against as a visible number, and the signed distance between them.
// A clinician can weigh that. A red badge would be inventing a cutoff.

/// The seven scored parameters, in scoring.rs order.
export const SCORED_KEYS = [
  'r2n', 'n', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio',
];

/// Build one row per scored parameter.
///
/// `refPayload` is mobile-imu-core's `healthy_reference()`:
/// `{reference: {...}, withdrawn: [...]}`. A withdrawn key keeps its measured
/// value and gets `reference: null, delta: null` -- the comparison is
/// withheld, not the measurement. `n` is withdrawn because its 3.5 was
/// measured under the old 4-second oscillation cap, so it describes the cap
/// rather than control legs.
///
/// Returns [] when there is nothing to compare against, so a caller hides the
/// table rather than rendering seven rows of dashes.
export function referenceRows(params, refPayload) {
  if (!params || !refPayload || !refPayload.reference) return [];
  const ref = refPayload.reference;
  const withdrawn = new Set(refPayload.withdrawn || []);

  return SCORED_KEYS.map((key) => {
    const measured = params[key];
    const hasMeasured = typeof measured === 'number' && Number.isFinite(measured);
    const refValue = ref[key];
    const hasRef = !withdrawn.has(key)
      && typeof refValue === 'number'
      && Number.isFinite(refValue);
    return {
      key,
      measured: hasMeasured ? measured : null,
      reference: hasRef ? refValue : null,
      // Signed, in the parameter's own units: negative is below the
      // reference, positive above. Which direction counts as impaired differs
      // per parameter and is NOT encoded here -- that is the composite's job,
      // and folding it in would turn a distance back into a verdict.
      delta: hasMeasured && hasRef ? measured - refValue : null,
      withdrawn: withdrawn.has(key),
    };
  });
}

/// Why a withdrawn parameter shows no comparison. Returned separately from the
/// row so the table stays data and the explanation stays prose.
export function withdrawnNote(rows) {
  const names = (rows || []).filter((r) => r.withdrawn).map((r) => r.key);
  if (names.length === 0) return null;
  return `No reference shown for ${names.join(', ')}: the stored value was `
    + 'measured under the old 4-second swing cap, so it describes that cap '
    + 'rather than a control leg. The measurement itself is unaffected.';
}

export function formatCell(v, digits = 4) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

/// Signed, so the direction is readable at a glance without colour.
export function formatDelta(v, digits = 4) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`;
}
