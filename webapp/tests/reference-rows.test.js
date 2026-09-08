import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  referenceRows, withdrawnNote, formatCell, formatDelta, SCORED_KEYS,
} from '../src/reference-rows.js';

const REF = {
  reference: {
    r2n: 1.0321, n: 3.5, phi_max_ratio: 0.6386, omega_max_n: 6.7684,
    omega_min_n: 0.0010, f: 0.9137, area_ratio: 0.0768,
  },
  withdrawn: ['n'],
};
const params = {
  r2n: 0.9, n: 11.5, phi_max_ratio: 0.5, omega_max_n: 5.0,
  omega_min_n: 0.02, f: 0.95, area_ratio: 0.2,
};

test('one row per scored parameter, in scoring order', () => {
  const rows = referenceRows(params, REF);
  assert.deepEqual(rows.map((r) => r.key), SCORED_KEYS);
  assert.equal(rows.length, 7);
});

test('the reference is carried as a visible number, not just a verdict', () => {
  const r = referenceRows(params, REF).find((x) => x.key === 'r2n');
  assert.equal(r.measured, 0.9);
  assert.equal(r.reference, 1.0321);
  assert.ok(Math.abs(r.delta - (0.9 - 1.0321)) < 1e-12);
});

test('the distance is signed and in the parameter own units', () => {
  const rows = referenceRows(params, REF);
  const below = rows.find((x) => x.key === 'r2n');
  const above = rows.find((x) => x.key === 'area_ratio');
  assert.ok(below.delta < 0, 'below the reference must read negative');
  assert.ok(above.delta > 0, 'above the reference must read positive');
});

test('a withdrawn reference keeps the measurement and drops the comparison', () => {
  // N is withdrawn: 3.5 was measured under the old 4 s cap, so it describes
  // the cap. Showing a +8.0 distance against it would read as a huge finding.
  const n = referenceRows(params, REF).find((x) => x.key === 'n');
  assert.equal(n.measured, 11.5, 'the measurement must survive');
  assert.equal(n.reference, null, 'a withdrawn reference must not be shown');
  assert.equal(n.delta, null, 'a withdrawn reference must not yield a distance');
  assert.equal(n.withdrawn, true);
});

test('the withdrawal is explained, naming the parameter', () => {
  const note = withdrawnNote(referenceRows(params, REF));
  assert.match(note, /\bn\b/);
  assert.match(note, /4-second swing cap/);
  assert.match(note, /measurement itself is unaffected/);
});

test('no withdrawal means no note', () => {
  const rows = referenceRows(params, { ...REF, withdrawn: [] });
  assert.equal(withdrawnNote(rows), null);
});

test('nothing to compare against yields no rows rather than dashes', () => {
  assert.deepEqual(referenceRows(params, null), []);
  assert.deepEqual(referenceRows(params, {}), []);
  assert.deepEqual(referenceRows(null, REF), []);
});

test('a missing or non-finite measurement does not fabricate a distance', () => {
  const rows = referenceRows({ ...params, f: NaN, r2n: undefined }, REF);
  const f = rows.find((x) => x.key === 'f');
  const r2n = rows.find((x) => x.key === 'r2n');
  assert.equal(f.measured, null);
  assert.equal(f.delta, null, 'NaN measurement produced a distance');
  assert.equal(f.reference, 0.9137, 'the reference is still worth showing');
  assert.equal(r2n.delta, null);
});

test('formatting renders an em dash for absent values, never NaN or null', () => {
  assert.equal(formatCell(null), '—');
  assert.equal(formatCell(NaN), '—');
  assert.equal(formatDelta(null), '—');
  assert.equal(formatCell(1.23456, 2), '1.23');
});

test('a distance always carries its sign, so direction reads without colour', () => {
  assert.equal(formatDelta(0.5, 2), '+0.50');
  assert.equal(formatDelta(-0.5, 2), '-0.50');
  assert.equal(formatDelta(0, 2), '+0.00');
});
