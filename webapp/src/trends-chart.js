// Longitudinal PT-score chart: geometry as pure functions, drawing as a thin
// canvas call.
//
// Unit B Task 2, phase 2. Split the same way trends.js was, and for the same
// reason: the parts that can be WRONG -- the axis domain, which node is a
// legacy-reference node, which point a tap landed on -- are computed by
// functions a test can call, and only the pixels go through a canvas.
//
// Canvas 2D, no charting library: app.js records that as a binding constraint
// at drawWaveform, and this follows its device-pixel-ratio pattern so the plot
// is not blurry on a real phone.
//
// The y-axis is the PT score's OWN scale, continuous and unbounded above. It is
// deliberately not a 0-5 MAS axis: the score is unbounded (the UI says so:
// "Unbounded above -- not a 0-1 scale"), the app refuses to map scores onto
// clinical bands at all ("No healthy/borderline/impaired classification is
// shown", for reasons its own disclaimer gives), and PT7 is non-monotonic in
// severity, so score -> grade is not even order-preserving. A clinician-entered
// MAS could be annotated here; nothing derived from a score will be. No such
// field exists in the capture schema yet, so there is no annotation to draw --
// see the gap note in `nodeStyle`.

/** Touch target radius in CSS px. Generous on purpose: this is a phone. */
export const HIT_RADIUS = 18;

/** Plot padding: room for tick labels without a layout pass. */
export const PADDING = { top: 14, right: 14, bottom: 28, left: 46 };

/**
 * Tick values across [0, hi], chosen so the labels are round numbers.
 *
 * PT scores live near zero -- PT_HEALTHY_MAX is 0.1709 and PT_BORDERLINE_MAX
 * 0.3528 -- so a series is typically 0.05 to ~2, not 0 to 120. Integer ticks
 * would collapse the whole plot onto one gridline, which is why the step
 * search starts below 1 and the label precision follows the step rather than
 * being fixed.
 */
export function niceTicks(hi, maxTicks = 5) {
  if (!Number.isFinite(hi) || hi <= 0) return [0];
  const raw = hi / Math.max(1, maxTicks - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out = [];
  for (let v = 0; v <= hi + step / 2; v += step) {
    // Re-round each tick: repeated addition of 0.1 drifts, and a label
    // reading 0.30000000000000004 is a bug the user can see.
    out.push(Number(v.toFixed(10)));
  }
  return out;
}

/** Label a tick with precision that follows the step, not a fixed guess. */
export function formatTick(value, step) {
  if (!Number.isFinite(value)) return '';
  const decimals = step >= 1 ? 0 : Math.min(3, Math.ceil(-Math.log10(step)) + 1);
  return value.toFixed(decimals);
}

/** Score text for a tooltip: enough digits to distinguish two trials. */
export function formatScore(value) {
  if (!Number.isFinite(value)) return '—';
  return Math.abs(value) >= 10 ? value.toFixed(1) : value.toFixed(3);
}

/**
 * Pixel geometry for a set of points.
 *
 * The y domain floors at ZERO rather than at the smallest score, because zero
 * is meaningful here: the score is a sum of per-parameter deviations BELOW the
 * healthy reference, so 0 means "matched the reference on every parameter".
 * Starting the axis at the minimum would make a flat healthy series look like
 * dramatic variation.
 */
export function computeLayout(points, { width, height, padding = PADDING } = {}) {
  const plot = {
    x: padding.left,
    y: padding.top,
    w: Math.max(1, width - padding.left - padding.right),
    h: Math.max(1, height - padding.top - padding.bottom),
  };
  const scores = points.map((p) => p.total).filter(Number.isFinite);
  const times = points.map((p) => p.timestamp).filter(Number.isFinite);
  // 15% headroom so the topmost node is not clipped by the frame.
  const yHi = scores.length ? Math.max(...scores) * 1.15 : 1;
  const tMin = times.length ? Math.min(...times) : 0;
  const tMax = times.length ? Math.max(...times) : 1;
  // A single trial, or several captured in the same minute, would give a zero
  // width span and divide by zero. Centre it instead.
  const span = tMax - tMin;

  const xOf = (t) => (span > 0
    ? plot.x + ((t - tMin) / span) * plot.w
    : plot.x + plot.w / 2);
  const yOf = (v) => plot.y + plot.h - (Math.max(0, v) / yHi) * plot.h;

  const ticks = niceTicks(yHi);
  const step = ticks.length > 1 ? ticks[1] - ticks[0] : yHi;
  return {
    plot,
    yHi,
    tMin,
    tMax,
    xOf,
    yOf,
    ticks,
    tickStep: step,
    nodes: points.map((p) => ({ point: p, cx: xOf(p.timestamp), cy: yOf(p.total) })),
  };
}

/**
 * How a node is drawn. Filled for the active reference, hollow for one
 * recomputed across a reference change.
 *
 * GAP, deliberate: a clinician-entered MAS badge belongs here, and there is no
 * field to read. Nothing in the trial record, PARAM_FIELDS or the object
 * stores carries a MAS grade, so inventing a name here would only guarantee it
 * disagrees with whatever capture eventually writes. When the field lands, this
 * is where the badge goes -- and it must come from that field, never from a
 * score.
 */
export function nodeStyle(point) {
  return point.recomputedAcrossReference
    ? { fill: 'transparent', stroke: '#1d6fa5', lineWidth: 2, radius: 5 }
    : { fill: '#1d6fa5', stroke: '#1d6fa5', lineWidth: 1, radius: 4.5 };
}

/**
 * The node nearest (x, y) within HIT_RADIUS, or null.
 *
 * Nearest rather than first-within: two trials captured minutes apart overlap
 * at any sane chart width, and returning whichever happened to be earlier in
 * the array would make the tooltip feel arbitrary.
 */
export function hitTest(layout, x, y, radius = HIT_RADIUS) {
  let best = null;
  let bestD2 = radius * radius;
  for (const n of layout.nodes) {
    const dx = n.cx - x;
    const dy = n.cy - y;
    const d2 = dx * dx + dy * dy;
    if (d2 <= bestD2) {
      bestD2 = d2;
      best = n;
    }
  }
  return best;
}

/**
 * Tooltip lines for a node.
 *
 * The reference line for a recomputed point ends by saying the stored
 * measurements are unchanged. That clause is the clinically load-bearing one:
 * scores are never persisted, only params are, so a reader told "the score
 * changed" could reasonably conclude the patient changed or the archive was
 * rewritten.
 */
export function tooltipLines(point, activeRefId) {
  const when = new Date(point.timestamp);
  const stamp = Number.isFinite(point.timestamp)
    ? `${when.toISOString().slice(0, 10)} ${when.toISOString().slice(11, 16)}`
    : 'unknown date';
  const lines = [`Trial: ${stamp}`, `PT score: ${formatScore(point.total)}`];
  if (point.unmeasuredCount > 0) {
    // Surfaced because a score built from fewer measured parameters is not
    // comparable to one built from all seven, and the count is the only thing
    // on screen that says so.
    lines.push(`${point.unmeasuredCount} of 7 parameters not measurable`);
  }
  lines.push(point.recomputedAcrossReference
    ? `Recomputed against reference ${activeRefId}. This trial was captured `
      + `when reference ${point.healthyRefId} was active; the stored `
      + `measurements are unchanged.`
    : `Reference: ${activeRefId}`);
  return lines;
}

/**
 * Draw the series. Everything decidable was decided above; this only paints.
 */
export function drawTrends(canvas, series, activeRefId, { padding = PADDING } = {}) {
  const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
  const cssW = canvas.clientWidth || 320;
  const cssH = canvas.clientHeight || 200;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  const layout = computeLayout(series.points, { width: cssW, height: cssH, padding });

  ctx.font = '10px system-ui, sans-serif';
  ctx.fillStyle = '#666';
  ctx.strokeStyle = '#e2e2e2';
  ctx.lineWidth = 1;
  for (const tick of layout.ticks) {
    const y = layout.yOf(tick);
    ctx.beginPath();
    ctx.moveTo(layout.plot.x, y);
    ctx.lineTo(layout.plot.x + layout.plot.w, y);
    ctx.stroke();
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(formatTick(tick, layout.tickStep), layout.plot.x - 6, y);
  }

  if (layout.nodes.length > 1) {
    ctx.strokeStyle = '#1d6fa5';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    layout.nodes.forEach((n, i) => (i ? ctx.lineTo(n.cx, n.cy) : ctx.moveTo(n.cx, n.cy)));
    ctx.stroke();
  }

  for (const n of layout.nodes) {
    const s = nodeStyle(n.point);
    ctx.beginPath();
    ctx.arc(n.cx, n.cy, s.radius, 0, Math.PI * 2);
    // Hollow means "recomputed across a reference change". Filling it would
    // erase the only visual difference between the two.
    if (s.fill !== 'transparent') {
      ctx.fillStyle = s.fill;
      ctx.fill();
    }
    ctx.strokeStyle = s.stroke;
    ctx.lineWidth = s.lineWidth;
    ctx.stroke();
  }
  return layout;
}
