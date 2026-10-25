// Per-metric presentation for the result screen: what the metric measures,
// how far it sits from the healthy reference, and a colour for how impaired
// that distance looks.
//
// The colour is a DELIBERATE reversal of the earlier distance-only design,
// made on the user's explicit instruction after seeing the numbers on a
// device. Recorded here rather than argued: the reference these colours are
// measured against is still the provisional one, so the bands inherit its
// uncertainty. Colour is never the only signal -- every row also carries a
// text severity label, because the app is read at arm's length under outdoor
// glare and by people who cannot distinguish red from green.

/// The seven scored parameters, in scoring.rs order.
export const SCORED_KEYS = [
  'r2n', 'n', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio',
];

/// Human labels and what each one actually measures, in plain clinical terms.
export const METRIC_INFO = {
  r2n: {
    label: 'R2n — first swing size',
    what: 'How big the first full swing is, compared with the height the leg '
      + 'was released from. A leg that swings freely travels far on the first '
      + 'pass; a stiff one is checked early and travels less.',
    lowIsImpaired: true,
  },
  n: {
    label: 'N — number of swings',
    what: 'How many oscillations the leg makes before it comes to rest. A '
      + 'free-swinging leg keeps going for several cycles; increased tone '
      + 'damps the motion out quickly.',
    lowIsImpaired: true,
  },
  phi_max_ratio: {
    label: 'Return height',
    what: 'How high the leg comes back up on the first rebound, as a share of '
      + 'the release height. Resistance absorbs the swing, so the leg returns '
      + 'lower.',
    lowIsImpaired: true,
  },
  omega_max_n: {
    label: 'Peak speed',
    what: 'The fastest the shank travels during the swing, scaled to the '
      + 'release height so legs released from different angles compare. A '
      + 'restrained limb never reaches full speed.',
    lowIsImpaired: true,
  },
  omega_min_n: {
    label: 'Slowest point',
    what: 'How much the leg is still moving at the turning points of the '
      + 'swing. A free limb momentarily stops; a catch keeps it from fully '
      + 'decelerating, so this rises.',
    lowIsImpaired: false,
  },
  f: {
    label: 'Swing frequency',
    what: 'How many swings per second. Set mostly by the length of the limb, '
      + 'so it should sit near the reference in either direction — a value '
      + 'far off in EITHER direction is the finding.',
    lowIsImpaired: null,
  },
  area_ratio: {
    label: 'Swing symmetry',
    what: 'How unevenly the swing is split between flexion and extension. A '
      + 'free swing is close to even; resistance on one side pushes this up.',
    lowIsImpaired: false,
  },
};

/// The per-parameter contribution at which a metric is treated as fully
/// impaired. compute_pt_score_breakdown divides each deviation by
/// `7 * reference`, so one parameter contributing its whole share of a
/// severe composite is 1/7. Above that the colour is simply pinned.
export const DEV_FULL = 1 / 7;

export function severityOf(dev) {
  if (typeof dev !== 'number' || !Number.isFinite(dev) || dev <= 0) return 0;
  return Math.min(1, dev / DEV_FULL);
}

/// Words, so the colour is never carrying the meaning on its own.
export function severityLabel(sev) {
  if (sev <= 0.02) return 'within reference';
  if (sev < 0.3) return 'slightly off';
  if (sev < 0.6) return 'moderately off';
  return 'far off';
}

/// A green-to-red ramp with the text colour chosen for contrast, so a dark
/// red band stays readable instead of becoming dark-on-dark.
export function severityStyle(sev) {
  const s = Math.max(0, Math.min(1, sev));
  // Hue runs green (140) -> amber (45) -> red (0); lightness falls only on the
  // red half, so "more spastic" reads as DARKER red while the healthy end
  // stays a light tint that black text sits on comfortably.
  const hue = s < 0.5 ? 140 - (s / 0.5) * 95 : 45 - ((s - 0.5) / 0.5) * 45;
  const light = s < 0.5 ? 88 - (s / 0.5) * 8 : 80 - ((s - 0.5) / 0.5) * 38;
  const sat = s < 0.5 ? 55 + (s / 0.5) * 25 : 80 - ((s - 0.5) / 0.5) * 15;
  return {
    background: `hsl(${hue.toFixed(0)} ${sat.toFixed(0)}% ${light.toFixed(0)}%)`,
    // WCAG-ish switch: below ~55% lightness a dark foreground stops being
    // legible, so flip to white rather than letting the darkest bands go muddy.
    color: light < 55 ? '#FFFFFF' : '#0F172A',
    label: severityLabel(s),
  };
}

/// One row per scored parameter: the measurement, the reference, the signed
/// distance, and how impaired that distance looks.
///
/// `breakdown` is mobile-imu-core's per-parameter contribution list, which
/// already encodes each parameter's penalty DIRECTION -- some penalise only
/// below the reference, some only above, `f` in both. Reusing it means the
/// colours and the composite can never disagree about which way is worse.
export function metricRows(params, refPayload, breakdown) {
  if (!params || !refPayload || !refPayload.reference) return [];
  const ref = refPayload.reference;
  const dev = new Map((breakdown || []).map((b) => [b.key, b.value]));

  return SCORED_KEYS.map((key) => {
    const measured = params[key];
    const hasMeasured = typeof measured === 'number' && Number.isFinite(measured);
    const refValue = ref[key];
    const hasRef = typeof refValue === 'number' && Number.isFinite(refValue);
    const sev = severityOf(dev.get(key));
    const info = METRIC_INFO[key] || { label: key, what: '', lowIsImpaired: null };
    return {
      key,
      label: info.label,
      what: info.what,
      measured: hasMeasured ? measured : null,
      reference: hasRef ? refValue : null,
      delta: hasMeasured && hasRef ? measured - refValue : null,
      severity: sev,
      style: severityStyle(sev),
    };
  });
}

/// Which side of the reference counts as impaired, in words, so the expanded
/// row explains the direction rather than leaving a bare signed number.
export function directionNote(key, reference) {
  const info = METRIC_INFO[key];
  if (!info || reference === null) return null;
  const r = formatNumber(reference);
  if (info.lowIsImpaired === true) return `Reference ${r}. Lower than this suggests more resistance.`;
  if (info.lowIsImpaired === false) return `Reference ${r}. Higher than this suggests more resistance.`;
  return `Reference ${r}. A value far from this in either direction is the finding.`;
}

export function formatNumber(v, digits = 4) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return v.toFixed(digits);
}

export function formatDelta(v, digits = 4) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  return `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`;
}
