import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  metricRows, severityOf, severityStyle, severityLabel, directionNote,
  formatNumber, formatDelta, SCORED_KEYS, METRIC_INFO, DEV_FULL,
} from '../src/metric-rows.js';

const REF = {
  reference: {
    r2n: 1.0321, n: 3.5, phi_max_ratio: 0.6386, omega_max_n: 6.7684,
    omega_min_n: 0.0010, f: 0.9137, area_ratio: 0.0768,
  },
  withdrawn: [],
};
const params = {
  r2n: 0.9, n: 11.5, phi_max_ratio: 0.5, omega_max_n: 5.0,
  omega_min_n: 0.02, f: 0.95, area_ratio: 0.2,
};

test('every scored parameter has a label and a description', () => {
  // A metric with no explanation is the thing this view exists to fix.
  for (const k of SCORED_KEYS) {
    assert.ok(METRIC_INFO[k], `no info for ${k}`);
    assert.ok(METRIC_INFO[k].label.length > 0, `${k} has no label`);
    assert.ok(METRIC_INFO[k].what.length > 30, `${k} has no real description`);
  }
});

test('severity is driven by the composite own per-parameter contribution', () => {
  // Not a second opinion invented here: reusing the breakdown means the
  // colours and the score can never disagree about which way is worse.
  const rows = metricRows(params, REF, [{ key: 'r2n', value: DEV_FULL }, { key: 'n', value: 0 }]);
  assert.equal(rows.find((r) => r.key === 'r2n').severity, 1);
  assert.equal(rows.find((r) => r.key === 'n').severity, 0);
});

test('severity saturates rather than running past full', () => {
  assert.equal(severityOf(DEV_FULL * 5), 1);
  assert.equal(severityOf(0), 0);
  assert.equal(severityOf(-1), 0);
  assert.equal(severityOf(NaN), 0);
  assert.equal(severityOf(undefined), 0);
});

test('the colour ramp stays readable at both ends', () => {
  // The failure this guards: a dark red band with dark text on it, which is
  // exactly where the number stops being legible under glare.
  for (const s of [0, 0.25, 0.5, 0.75, 1]) {
    const st = severityStyle(s);
    assert.match(st.background, /^hsl\(/);
    assert.ok(st.color === '#FFFFFF' || st.color === '#0F172A');
  }
  // Healthy end must be a light tint with dark text; severe end dark with white.
  assert.equal(severityStyle(0).color, '#0F172A');
  assert.equal(severityStyle(1).color, '#FFFFFF');
});

test('more spastic is a darker red, not merely a different hue', () => {
  const light = (s) => Number(severityStyle(s).background.match(/([\d.]+)%\)$/)[1]);
  assert.ok(light(1) < light(0.75), 'the most severe band must be the darkest');
  assert.ok(light(0.75) < light(0.5));
});

test('colour is never the only signal', () => {
  // Read at arm length, outdoors, and by people who cannot separate red from
  // green. Every band carries words too.
  assert.equal(severityLabel(0), 'within reference');
  assert.notEqual(severityLabel(1), severityLabel(0));
  for (const s of [0, 0.3, 0.6, 1]) {
    assert.ok(severityStyle(s).label.length > 0);
  }
});

test('the direction of impairment is stated, not left to a bare sign', () => {
  assert.match(directionNote('n', 3.5), /Lower than this/);
  assert.match(directionNote('area_ratio', 0.0768), /Higher than this/);
  assert.match(directionNote('f', 0.9137), /either direction/);
});

test('n is shown like every other metric, with its reference', () => {
  // The withheld-reference row and its explanatory note were removed on
  // request; n now carries a reference and a colour like the rest.
  const n = metricRows(params, REF, []).find((r) => r.key === 'n');
  assert.equal(n.reference, 3.5);
  assert.equal(n.measured, 11.5);
  assert.ok(Math.abs(n.delta - 8.0) < 1e-9);
});

test('a missing measurement does not fabricate a distance', () => {
  const rows = metricRows({ ...params, f: NaN }, REF, []);
  const f = rows.find((r) => r.key === 'f');
  assert.equal(f.measured, null);
  assert.equal(f.delta, null);
  assert.equal(f.reference, 0.9137, 'the reference is still worth showing');
});

test('nothing to compare against yields no rows', () => {
  assert.deepEqual(metricRows(params, null, []), []);
  assert.deepEqual(metricRows(null, REF, []), []);
});

test('formatting never renders NaN or null', () => {
  assert.equal(formatNumber(null), '—');
  assert.equal(formatDelta(NaN), '—');
  assert.equal(formatDelta(0.5, 2), '+0.50');
  assert.equal(formatDelta(-0.5, 2), '-0.50');
});
