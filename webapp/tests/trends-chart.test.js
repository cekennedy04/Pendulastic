// Chart geometry: the parts that can be wrong without looking wrong.
//
// No DOM and no canvas here -- everything asserted below is a pure function,
// which is the whole point of the split. `drawTrends` only paints what these
// decided.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  niceTicks, formatTick, formatScore, computeLayout, nodeStyle, hitTest,
  tooltipLines, HIT_RADIUS, PADDING,
} from '../src/trends-chart.js';

const ACTIVE = 'aaaaaaaaaaaa';
const LEGACY = 'bbbbbbbbbbbb';

function pt(over = {}) {
  return {
    id: 'p', timestamp: 1_700_000_000_000, total: 0.5, simple: 0.2,
    unmeasuredCount: 0, healthyRefId: ACTIVE, recomputedAcrossReference: false,
    ...over,
  };
}

const SIZE = { width: 320, height: 200 };

// ── ticks at PT-score magnitudes ─────────────────────────────────────────────

test('ticks resolve a range near zero instead of collapsing to one line', () => {
  // PT_HEALTHY_MAX is 0.1709 and PT_BORDERLINE_MAX 0.3528, so a real series
  // sits well under 1. Integer ticks would put the whole plot between 0 and 1
  // with nothing in between.
  const ticks = niceTicks(0.4);
  assert.ok(ticks.length >= 3, `got ${JSON.stringify(ticks)}`);
  assert.equal(ticks[0], 0);
  assert.ok(ticks[ticks.length - 1] >= 0.4);
});

test('tick labels carry decimals when the step is fractional', () => {
  assert.equal(formatTick(0.1, 0.1), '0.10');
  assert.equal(formatTick(0.25, 0.25), '0.25');
  // ...and stop pretending to precision when the step is large.
  assert.equal(formatTick(50, 25), '50');
});

test('ticks are not polluted by floating point drift', () => {
  // Repeated addition of 0.1 gives 0.30000000000000004, which a user can read
  // off the axis.
  for (const v of niceTicks(1.0)) {
    assert.ok(String(v).length <= 6, `tick ${v} is unrounded`);
  }
});

test('a degenerate range still yields an axis rather than throwing', () => {
  assert.deepEqual(niceTicks(0), [0]);
  assert.deepEqual(niceTicks(Number.NaN), [0]);
});

// ── layout ───────────────────────────────────────────────────────────────────

test('the y axis floors at zero, not at the smallest score', () => {
  // The score is a sum of deviations BELOW the healthy reference, so 0 means
  // "matched the reference on every parameter". Flooring at the minimum would
  // make a flat healthy series look like dramatic variation.
  const layout = computeLayout([pt({ total: 0.50 }), pt({ total: 0.52 })], SIZE);
  const bottom = layout.plot.y + layout.plot.h;
  assert.equal(Math.round(layout.yOf(0)), Math.round(bottom));
});

test('the highest point is not clipped by the frame', () => {
  const layout = computeLayout([pt({ total: 2.0 })], SIZE);
  assert.ok(layout.yOf(2.0) > layout.plot.y, 'top node sits inside the plot');
});

test('a single trial is centred rather than dividing by a zero time span', () => {
  const layout = computeLayout([pt()], SIZE);
  assert.equal(layout.nodes.length, 1);
  assert.ok(Number.isFinite(layout.nodes[0].cx));
  assert.equal(Math.round(layout.nodes[0].cx),
               Math.round(layout.plot.x + layout.plot.w / 2));
});

test('trials captured in the same instant do not produce NaN positions', () => {
  const layout = computeLayout([pt({ id: 'a' }), pt({ id: 'b' })], SIZE);
  for (const n of layout.nodes) assert.ok(Number.isFinite(n.cx) && Number.isFinite(n.cy));
});

test('an empty series still yields a usable axis', () => {
  const layout = computeLayout([], SIZE);
  assert.equal(layout.nodes.length, 0);
  assert.ok(layout.ticks.length >= 1);
});

test('time maps left to right across the plot', () => {
  const layout = computeLayout([
    pt({ id: 'early', timestamp: 1000 }),
    pt({ id: 'late', timestamp: 5000 }),
  ], SIZE);
  assert.ok(layout.nodes[0].cx < layout.nodes[1].cx);
});

// ── node classification ──────────────────────────────────────────────────────

test('a recomputed point is hollow and a current one is filled', () => {
  // The only visual difference between the two. Filling the hollow one would
  // erase the distinction the whole reference-stamp design exists to show.
  assert.equal(nodeStyle(pt({ recomputedAcrossReference: true })).fill, 'transparent');
  assert.notEqual(nodeStyle(pt({ recomputedAcrossReference: false })).fill, 'transparent');
});

// ── hit testing ──────────────────────────────────────────────────────────────

test('a tap picks the NEAREST node, not the first one in range', () => {
  // Trials minutes apart overlap at any phone width; returning whichever came
  // first in the array would make the tooltip feel arbitrary.
  const layout = computeLayout([
    pt({ id: 'a', timestamp: 1000, total: 0.5 }),
    pt({ id: 'b', timestamp: 1100, total: 0.5 }),
  ], SIZE);
  const b = layout.nodes[1];
  const hit = hitTest(layout, b.cx + 1, b.cy + 1);
  assert.equal(hit.point.id, 'b');
});

test('a tap on empty space selects nothing', () => {
  const layout = computeLayout([pt()], SIZE);
  assert.equal(hitTest(layout, 5, 5, 4), null);
});

test('the touch target is generous enough for a finger', () => {
  const layout = computeLayout([pt()], SIZE);
  const n = layout.nodes[0];
  assert.ok(hitTest(layout, n.cx + HIT_RADIUS - 2, n.cy), 'near miss should still hit');
});

// ── tooltips ─────────────────────────────────────────────────────────────────

test('a recomputed point says the stored measurements are unchanged', () => {
  const lines = tooltipLines(
    pt({ recomputedAcrossReference: true, healthyRefId: LEGACY }), ACTIVE);
  const joined = lines.join(' ');
  assert.match(joined, /stored measurements are unchanged/);
  assert.match(joined, new RegExp(LEGACY));
  assert.match(joined, new RegExp(ACTIVE));
});

test('a current-reference point names the reference without the caveat', () => {
  const joined = tooltipLines(pt(), ACTIVE).join(' ');
  assert.match(joined, new RegExp(`Reference: ${ACTIVE}`));
  assert.doesNotMatch(joined, /unchanged/);
});

test('an unmeasurable-parameter count is surfaced, not hidden', () => {
  // A score built from fewer than seven measured parameters is not comparable
  // to one built from all of them, and this count is the only thing on screen
  // that says so.
  const joined = tooltipLines(pt({ unmeasuredCount: 2 }), ACTIVE).join(' ');
  assert.match(joined, /2 of 7 parameters not measurable/);
});

test('a fully measured point does not mention unmeasured parameters', () => {
  assert.doesNotMatch(tooltipLines(pt(), ACTIVE).join(' '), /not measurable/);
});

test('scores are formatted with enough digits to tell two trials apart', () => {
  // Real PT scores cluster near zero; one decimal would render 0.171 and 0.164
  // as the same number.
  assert.notEqual(formatScore(0.171), formatScore(0.164));
  assert.equal(formatScore(Number.NaN), '—');
});
