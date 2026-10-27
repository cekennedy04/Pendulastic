// Per-parameter selection for the PT score panel.
//
// WHY THIS EXISTS. The canonical score sums a fixed set of parameters, and
// which set that should be is an open research question this project has
// changed its mind about three times in two days: omega_min_n left on
// 2026-10-26 for being algebraically -omega_max_n, omega_max_n left and came
// back the same day, and f is under argument now (see
// docs/reports/2026-10-27-dropping-f.md). Every one of those arguments was
// made from aggregate numbers, because nobody could see a single trial's
// score recomputed without a given parameter.
//
// So the app offers all SEVEN as toggles. The point is not to let a clinician
// tune a verdict -- it is to make each parameter's contribution visible on
// real trials, which is the evidence those arguments kept lacking.
//
// WHAT MAKES IT HONEST. mobile-imu-core's divisor follows the selection. With
// a fixed divisor, deselecting ANY parameter would always lower the score, so
// every toggle would read as "less impaired" no matter what it removed, and
// the panel would teach the opposite of the truth. Removing a term now
// redistributes its share across the rest. That rescale landed first, in
// 90776a5, precisely so this panel could exist.
//
// WHAT IT MUST NOT DO. A score from a non-canonical selection is not
// comparable to a calibrated threshold, and must never be presented as a
// verdict. The Rust reply carries `canonical`, and `selectionNotice` below
// turns a non-canonical selection into a visible caveat rather than a silent
// one.

/// Every parameter the app can score, in publication order. Mirrors
/// `pendulastic_pt_score.ALL_PT_KEYS` and `pt_score::ALL_PT_KEYS`;
/// tests/test_pt_zone_parity.py reads this file and pins all three to each
/// other. That claim was false when first written -- the test compared only
/// Python and Rust -- so the list below could have drifted from both.
export const ALL_PT_KEYS = [
  'r2n',
  'n',
  'phi_max_ratio',
  'omega_max_n',
  'omega_min_n',
  'f',
  'area_ratio',
];

/// The canonical scored set -- what `PT_HEALTHY_MAX` and `PT_BORDERLINE_MAX`
/// are (eventually) calibrated against. Deliberately NOT the default
/// selection: the app shows all seven so nothing is hidden, and says so.
///
/// Pinned to `_PARAM_KEYS` and `pt_score::CANONICAL_PT_KEYS` by
/// tests/test_pt_zone_parity.py. A drift here would let the phone call a
/// selection canonical that the scorer does not, which is exactly what the
/// `canonical` flag exists to prevent.
export const CANONICAL_KEYS = [
  'r2n',
  'n',
  'phi_max_ratio',
  'omega_max_n',
  'f',
  'area_ratio',
];

/// Short labels for the toggles. Plain language, because the panel is for
/// reading a trial, not for reading the source.
export const PARAM_LABEL = {
  r2n: 'relaxation index',
  n: 'oscillation count',
  phi_max_ratio: 'return ratio',
  omega_max_n: 'peak speed',
  omega_min_n: 'peak return speed',
  f: 'frequency',
  area_ratio: 'swing asymmetry',
};

/// One line per parameter saying why it might be worth switching off. Shown
/// as a hint, not a recommendation -- the panel exists so the user can decide
/// from their own trials rather than take this on trust.
export const PARAM_CAVEAT = {
  omega_min_n:
    'Not in the scored set: it is -peak speed for any limb dropped from full '
    + 'extension, so scoring both counts one measurement twice.',
  f: 'Under argument. It is the weakest discriminator on the dialled ladder '
    + '(1.12x against the oscillation count’s 3.75x) and it is dropped '
    + 'entirely below two cycles, which makes the score fall as a limb worsens.',
  omega_max_n:
    'Its healthy reference is disputed: the pipeline reads about 1.85x what '
    + 'physics and the one measured peak-velocity study predict.',
  area_ratio:
    'The only term with no upper bound, so it can dominate a score on its own.',
};

/// All seven on. The app deliberately starts from everything visible: a
/// parameter hidden by default is a parameter nobody argues about.
export function defaultSelection() {
  return ALL_PT_KEYS.slice();
}

/// True when `selection` is exactly the canonical scored set, order-insensitive
/// (the UI emits toggle order, which is not the scored order).
export function isCanonical(selection) {
  if (!Array.isArray(selection)) return false;
  const s = new Set(selection);
  return s.size === CANONICAL_KEYS.length && CANONICAL_KEYS.every((k) => s.has(k));
}

/// The selection as the comma-separated form `pt_score_from_params_selected`
/// takes. Order is preserved so the breakdown comes back in toggle order;
/// unknown names are dropped here rather than relied on being ignored there.
export function selectionToCsv(selection) {
  if (!Array.isArray(selection)) return '';
  return selection.filter((k) => ALL_PT_KEYS.includes(k)).join(',');
}

/// Toggle one key, returning a new selection in ALL_PT_KEYS order.
///
/// Refuses to empty the selection: a score over nothing is not a score, and
/// the Rust would fall back to a divisor of 1 and return zeros, which reads
/// as a perfectly healthy limb. Returns the selection unchanged instead, so
/// the caller can leave the last checkbox visibly checked.
export function toggleKey(selection, key) {
  if (!ALL_PT_KEYS.includes(key)) return selection;
  const s = new Set(selection);
  if (s.has(key)) {
    if (s.size === 1) return selection;
    s.delete(key);
  } else {
    s.add(key);
  }
  return ALL_PT_KEYS.filter((k) => s.has(k));
}

/// How the number from this selection may be read.
///
/// `canonical` comes from the Rust reply rather than being recomputed here,
/// so the UI and the scorer cannot disagree about which set is canonical.
/// Returns null only when the selection is canonical AND every selected term
/// was scorable -- the one case needing no caveat.
export function selectionNotice({ canonical, nScored, unscorable, zonesEnabled }) {
  const missing = Array.isArray(unscorable) ? unscorable : [];
  const parts = [];

  if (!canonical) {
    parts.push(
      'Exploratory selection: this total is not the scored set, so it cannot be '
      + 'compared with another trial’s score or with any band.',
    );
  } else if (zonesEnabled === false) {
    // Canonical, but the bands are gated off anyway -- say so once rather
    // than let the absence of a band read as "no verdict available".
    parts.push(
      'The scored set. Bands are switched off until the thresholds are '
      + 'recalibrated, so this is a number, not a grade.',
    );
  }

  if (missing.length) {
    const names = missing.map((k) => PARAM_LABEL[k] || k);
    parts.push(
      `Not measurable on this trial, and left out of the divisor rather than `
      + `counted as zero: ${names.join(', ')}.`,
    );
  }

  if (!parts.length) return null;
  return {
    text: parts.join(' '),
    canonical: Boolean(canonical),
    nScored: typeof nScored === 'number' ? nScored : missing.length ? null : undefined,
  };
}

/// Rows for the selection table: one per key in ALL_PT_KEYS order, carrying
/// everything the renderer needs and no DOM.
///
/// `share` is the term's fraction of the total, which is the number the panel
/// exists to show -- "frequency is 4% of this score" is the fact that settles
/// an argument about whether to keep it. Null when the total is zero (a limb
/// at or above reference on every term), because a share of nothing is not 0%,
/// it is undefined.
export function selectionRows({ selection, breakdown, unscorable, total }) {
  const sel = new Set(Array.isArray(selection) ? selection : []);
  const miss = new Set(Array.isArray(unscorable) ? unscorable : []);
  const contrib = new Map(
    (Array.isArray(breakdown) ? breakdown : []).map((b) => [b.key, b.value]),
  );
  const denom = typeof total === 'number' && total > 0 ? total : null;

  return ALL_PT_KEYS.map((key) => {
    const selected = sel.has(key);
    const unmeasured = selected && miss.has(key);
    const value = selected && !unmeasured ? contrib.get(key) : null;
    return {
      key,
      label: PARAM_LABEL[key] || key,
      caveat: PARAM_CAVEAT[key] || null,
      inCanonical: CANONICAL_KEYS.includes(key),
      selected,
      unmeasured,
      value: typeof value === 'number' ? value : null,
      share:
        denom !== null && typeof value === 'number' ? value / denom : null,
    };
  });
}
