import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  ALL_PT_KEYS,
  CANONICAL_KEYS,
  defaultSelection,
  isCanonical,
  selectionToCsv,
  toggleKey,
  selectionNotice,
  selectionRows,
} from '../src/param-select.js';

test('all seven are offered and the canonical five are a strict subset', () => {
  assert.equal(ALL_PT_KEYS.length, 7);
  assert.equal(CANONICAL_KEYS.length, 5);
  for (const k of CANONICAL_KEYS) assert.ok(ALL_PT_KEYS.includes(k), k);
  // omega_min_n is selectable but not scored -- that is the whole point of
  // having two lists rather than one.
  assert.ok(ALL_PT_KEYS.includes('omega_min_n'));
  assert.ok(!CANONICAL_KEYS.includes('omega_min_n'));
  assert.ok(!CANONICAL_KEYS.includes('omega_max_n'));
});

test('the app starts with everything visible', () => {
  assert.deepEqual(defaultSelection(), ALL_PT_KEYS);
  // ...and that default is deliberately NOT canonical, so the panel always
  // opens saying so rather than implying a verdict.
  assert.equal(isCanonical(defaultSelection()), false);
});

test('canonicity ignores order, because the UI emits toggle order', () => {
  assert.equal(isCanonical(CANONICAL_KEYS), true);
  assert.equal(isCanonical([...CANONICAL_KEYS].reverse()), true);
  assert.equal(isCanonical(['n']), false);
  assert.equal(isCanonical(ALL_PT_KEYS), false);
  assert.equal(isCanonical(null), false);
});

test('toggling refuses to empty the selection', () => {
  // A score over nothing would come back as zeros from a divisor of 1, which
  // reads as a perfectly healthy limb. The last key stays on.
  const one = ['n'];
  assert.deepEqual(toggleKey(one, 'n'), one);
  // Removing and re-adding returns to ALL_PT_KEYS order, not click order.
  const without = toggleKey(ALL_PT_KEYS, 'f');
  assert.ok(!without.includes('f'));
  assert.deepEqual(toggleKey(without, 'f'), ALL_PT_KEYS);
  // Unknown keys are inert.
  assert.deepEqual(toggleKey(ALL_PT_KEYS, 'nope'), ALL_PT_KEYS);
});

test('the csv drops unknown names rather than trusting the scorer to ignore them', () => {
  assert.equal(selectionToCsv(['n', 'f']), 'n,f');
  assert.equal(selectionToCsv(['n', 'bogus', 'f']), 'n,f');
  assert.equal(selectionToCsv(null), '');
});

test('a non-canonical selection always carries a caveat', () => {
  const n = selectionNotice({ canonical: false, nScored: 7, unscorable: [] });
  assert.ok(n);
  assert.match(n.text, /Exploratory selection/);
  assert.equal(n.canonical, false);
});

test('a canonical selection with everything measurable needs no caveat', () => {
  assert.equal(
    selectionNotice({ canonical: true, nScored: 6, unscorable: [], zonesEnabled: true }),
    null,
  );
});

test('gated bands are stated once, not left as an absence', () => {
  const n = selectionNotice({
    canonical: true, nScored: 6, unscorable: [], zonesEnabled: false,
  });
  assert.ok(n);
  assert.match(n.text, /a number, not a grade/);
});

test('an unmeasurable term is named, and said to be out of the divisor', () => {
  const n = selectionNotice({ canonical: true, nScored: 5, unscorable: ['f'] });
  assert.ok(n);
  // The distinction the whole gate argument turned on: not measured is not
  // the same as measured as zero.
  assert.match(n.text, /left out of the divisor rather than counted as zero/);
  assert.match(n.text, /frequency/);
});

test('rows carry each term’s share, which is the fact the panel exists to show', () => {
  const rows = selectionRows({
    selection: ['r2n', 'n', 'f'],
    breakdown: [
      { key: 'r2n', value: 0.1 },
      { key: 'n', value: 0.3 },
      { key: 'f', value: 0.0 },
    ],
    unscorable: [],
    total: 0.4,
  });
  assert.equal(rows.length, 7, 'every parameter gets a row, selected or not');
  const by = new Map(rows.map((r) => [r.key, r]));
  // Tolerance, not equality: 0.3 / 0.4 is 0.7499999999999999 in f64, and a
  // share is a ratio of two sums -- pinning it to a decimal literal tests
  // float representation rather than the code.
  const near = (got, want) => assert.ok(
    Math.abs(got - want) < 1e-12, `${got} !~ ${want}`,
  );
  near(by.get('n').share, 0.75);
  near(by.get('r2n').share, 0.25);
  // Deselected keys have no value and no share, rather than a misleading zero.
  assert.equal(by.get('area_ratio').selected, false);
  assert.equal(by.get('area_ratio').value, null);
  assert.equal(by.get('area_ratio').share, null);
  // And the panel can mark which are in the scored set.
  assert.equal(by.get('omega_min_n').inCanonical, false);
  assert.equal(by.get('n').inCanonical, true);
});

test('an unmeasurable term reads as unmeasured, not as a perfect zero', () => {
  const rows = selectionRows({
    selection: ALL_PT_KEYS,
    breakdown: [{ key: 'f', value: 0.0 }, { key: 'n', value: 0.2 }],
    unscorable: ['f'],
    total: 0.2,
  });
  const f = rows.find((r) => r.key === 'f');
  assert.equal(f.selected, true);
  assert.equal(f.unmeasured, true);
  assert.equal(f.value, null, 'a 0.0 contribution must not render as a measured 0');
  assert.equal(f.share, null);
});

test('a zero total gives undefined shares, not 0%', () => {
  // A limb at or above reference on every term. A share of nothing is not 0%.
  const rows = selectionRows({
    selection: CANONICAL_KEYS,
    breakdown: CANONICAL_KEYS.map((key) => ({ key, value: 0 })),
    unscorable: [],
    total: 0,
  });
  for (const r of rows.filter((x) => x.selected)) {
    assert.equal(r.share, null, r.key);
  }
});
