// The mount seam. Node has no DOM and no canvas, so the elements are minimal
// stand-ins honouring exactly the contract renderTrends uses -- the same
// approach db.test.js takes for IndexedDB.
//
// What is worth testing here is not the pixels. It is that the header always
// says what the chart is showing, that a tap maps to the right point, and that
// a tooltip does not latch open over a node the user has left.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderTrends } from '../src/trends-view.js';

const ACTIVE = 'aaaaaaaaaaaa';
const LEGACY = 'bbbbbbbbbbbb';

function fakeCanvas(w = 320, h = 200) {
  const calls = [];
  const ctx = new Proxy({}, {
    get: (_t, prop) => {
      if (prop === 'setTransform' || prop === 'clearRect' || prop === 'beginPath'
          || prop === 'moveTo' || prop === 'lineTo' || prop === 'stroke'
          || prop === 'fill' || prop === 'arc' || prop === 'fillText') {
        return (...a) => calls.push([prop, ...a]);
      }
      return undefined;
    },
    set: () => true,
  });
  return {
    calls,
    clientWidth: w,
    clientHeight: h,
    width: w,
    height: h,
    getContext: () => ctx,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: w, height: h }),
  };
}

const els = () => ({
  canvas: fakeCanvas(),
  header: { textContent: '' },
  tooltip: { textContent: '', hidden: true },
});

function series(points, over = {}) {
  return {
    points,
    excluded: [],
    summary: {
      convention: 'analysis', shown: points.length, total: points.length,
      excludedByReason: {}, recomputedAcrossReference: 0,
    },
    ...over,
  };
}

function pt(over = {}) {
  return {
    id: 'p', timestamp: 1_700_000_000_000, total: 0.5, simple: 0.2,
    unmeasuredCount: 0, healthyRefId: ACTIVE, recomputedAcrossReference: false,
    ...over,
  };
}

test('the header always states what the chart is showing', () => {
  const e = els();
  renderTrends(e, series([pt()]), ACTIVE);
  assert.match(e.header.textContent, /Showing 1 of 1 trials \(analysis convention\)/);
});

test('the header reports exclusions rather than just a smaller n', () => {
  // A chart that quietly drops trials still looks like a full history.
  const e = els();
  const s = series([pt()], {
    excluded: [{ id: 'x', reason: 'convention-mismatch' }],
    summary: {
      convention: 'analysis', shown: 1, total: 2,
      excludedByReason: { 'convention-mismatch': 1 }, recomputedAcrossReference: 0,
    },
  });
  renderTrends(e, s, ACTIVE);
  assert.match(e.header.textContent, /1 recorded under the live convention/);
});

test('a tap on a node shows that point\'s tooltip', () => {
  const e = els();
  const layout = renderTrends(e, series([pt()]), ACTIVE);
  const n = layout.nodes[0];
  e.canvas.onpointerdown({ clientX: n.cx, clientY: n.cy });
  assert.equal(e.tooltip.hidden, false);
  assert.match(e.tooltip.textContent, /PT score/);
});

test('a tap on empty space hides the tooltip rather than leaving a stale one', () => {
  const e = els();
  const layout = renderTrends(e, series([pt()]), ACTIVE);
  const n = layout.nodes[0];
  e.canvas.onpointerdown({ clientX: n.cx, clientY: n.cy });
  assert.equal(e.tooltip.hidden, false);
  e.canvas.onpointerdown({ clientX: 2, clientY: 2 });
  assert.equal(e.tooltip.hidden, true);
});

test('leaving the canvas dismisses the tooltip', () => {
  // Latching it open would leave a label attached to whatever is under the
  // pointer now, which is not the trial it describes.
  const e = els();
  const layout = renderTrends(e, series([pt()]), ACTIVE);
  e.canvas.onpointerdown({ clientX: layout.nodes[0].cx, clientY: layout.nodes[0].cy });
  e.canvas.onpointerleave();
  assert.equal(e.tooltip.hidden, true);
});

test('a legacy-reference point carries the unchanged-measurements clause', () => {
  const e = els();
  const layout = renderTrends(e,
    series([pt({ recomputedAcrossReference: true, healthyRefId: LEGACY })]), ACTIVE);
  e.canvas.onpointerdown({ clientX: layout.nodes[0].cx, clientY: layout.nodes[0].cy });
  assert.match(e.tooltip.textContent, /stored measurements are unchanged/);
});

test('re-rendering rebinds the hit test to the NEW layout', () => {
  // The handler closes over its layout. A stale one maps taps to where the
  // nodes used to be, which after a resize is silently the wrong trial.
  const e = els();
  renderTrends(e, series([pt({ id: 'only', timestamp: 1000 })]), ACTIVE);
  const second = renderTrends(e, series([
    pt({ id: 'a', timestamp: 1000, total: 0.2 }),
    pt({ id: 'b', timestamp: 9000, total: 0.9 }),
  ]), ACTIVE);
  const b = second.nodes[1];
  e.canvas.onpointerdown({ clientX: b.cx, clientY: b.cy });
  assert.match(e.tooltip.textContent, /PT score: 0\.900/);
});

test('an empty history renders without throwing and says so', () => {
  const e = els();
  renderTrends(e, series([], {
    summary: { convention: 'analysis', shown: 0, total: 0,
               excludedByReason: {}, recomputedAcrossReference: 0 },
  }), ACTIVE);
  assert.equal(e.header.textContent, 'No trials yet.');
});
