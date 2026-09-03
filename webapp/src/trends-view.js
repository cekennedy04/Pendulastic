// Mounting the trends chart: the DOM half, kept deliberately thin.
//
// Unit B Task 2.3. Everything decidable already lives in trends.js (provenance
// rules), trends-loader.js (storage and wasm) and trends-chart.js (geometry).
// What is left here is genuinely untestable surface -- element lookups,
// pointer coordinates, show/hide -- so there is as little of it as possible and
// it holds no rules of its own.
//
// There is no router to integrate with: the app is one page that toggles
// `hidden` on sections (see app.js's use of `el('result').hidden = ...`), so
// this follows that pattern rather than introducing routing for one view.

import { drawTrends, hitTest, tooltipLines } from './trends-chart.js';
import { summaryLine } from './trends.js';
import { loadTrendSeries } from './trends-loader.js';

/**
 * Render a series into an already-fetched view.
 *
 * Separated from `openTrends` so a caller holding a series (a test, or a
 * re-render on resize) does not have to touch storage again.
 */
export function renderTrends({ canvas, header, tooltip }, series, activeRefId) {
  header.textContent = summaryLine(series.summary);
  const layout = drawTrends(canvas, series, activeRefId);

  // Re-registered per render: the closure captures this layout, and a stale
  // one would map taps to where the nodes used to be after a resize.
  const onPoint = (ev) => {
    const rect = canvas.getBoundingClientRect();
    const hit = hitTest(layout, ev.clientX - rect.left, ev.clientY - rect.top);
    if (!hit) {
      tooltip.hidden = true;
      return;
    }
    tooltip.textContent = tooltipLines(hit.point, activeRefId).join('\n');
    tooltip.hidden = false;
  };
  canvas.onpointerdown = onPoint;
  // Dismiss on leave rather than latching: a tooltip left open over a node the
  // user has moved away from reads as a label for whatever is under it now.
  canvas.onpointerleave = () => { tooltip.hidden = true; };
  return layout;
}

/**
 * Fetch, score and draw one patient's trend, then show the section.
 *
 * Errors are surfaced in the header rather than thrown into the console: this
 * runs on a phone at a bedside, where a blank chart with no explanation is
 * indistinguishable from a patient with no trials.
 */
export async function openTrends(els, db, patientId, { convention = 'analysis', wasmSource } = {}) {
  const { section, header, canvas, tooltip } = els;
  section.hidden = false;
  tooltip.hidden = true;
  header.textContent = 'Loading trials…';
  try {
    const series = await loadTrendSeries(db, patientId, { convention, wasmSource });
    return renderTrends({ canvas, header, tooltip }, series, series.activeRefId);
  } catch (err) {
    header.textContent = `Could not load trends: ${err && err.message ? err.message : err}`;
    return null;
  }
}
