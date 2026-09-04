// Hand-rolled canvas charts for the trends view. No charting dependency: the
// webapp has no bundler, and three fixed chart types do not justify a generic
// abstraction.
//
// Shared by the on-screen render and the PNG export, so an exported figure
// cannot drift from what was on screen.

import { MAS_ORDER } from './mas-store.js';

const LEG_COLORS = { left: '#1D4ED8', right: '#B45309', unset: '#4B5563' };

// MANDATORY on the PT7 chart, and drawn INTO the canvas rather than into the
// DOM around it: an exported figure travels without its page, and a caveat
// that lives outside the image does not survive being pasted into a slide.
export const PT7_CAPTIONS = [
  'PT7 is non-monotonic in severity: a worsening leg can trend downward.',
  'Recomputed against the current HEALTHY_REF -- recalibration moves this whole curve.',
];

// Maps a value onto a canvas y coordinate. Canvas y grows downward, so the
// largest value maps to the smallest y.
//
// A flat series is widened by half a unit either side rather than left as a
// zero-height range: dividing by (max - min) === 0 puts every point at NaN,
// and a canvas draws nothing at NaN without erroring -- a leg whose A0 never
// moved would silently vanish instead of showing as flat, which is itself the
// clinically interesting case.
//
// The default padding keeps the extreme points off the axis lines, where a
// half-clipped marker reads as a rendering fault rather than a data point.
export function chartScale(values, { height, pad = 0.05 } = {}) {
  const xs = (values || []).filter((v) => Number.isFinite(v));
  let min = xs.length ? Math.min(...xs) : 0;
  let max = xs.length ? Math.max(...xs) : 1;
  if (min === max) { min -= 0.5; max += 0.5; }
  const span = max - min;
  min -= span * pad;
  max += span * pad;
  return {
    min,
    max,
    toY: (v) => height - ((v - min) / (max - min)) * height,
  };
}

export function drawChart(ctx, { series, width, height, kind, captions = [] }) {
  const cs = getComputedStyle(document.documentElement);
  const fg3 = cs.getPropertyValue('--fg3').trim() || '#64748B';
  const border = cs.getPropertyValue('--border').trim() || '#CBD5E1';

  ctx.clearRect(0, 0, width, height);

  // Reserve the caption's WRAPPED height, not one line per caption -- at
  // phone width these wrap to two lines each and a fixed guess would either
  // overlap the plot or leave a gap.
  ctx.font = '10px system-ui, sans-serif';
  const capH = captionHeight(ctx, captions, width - 8);
  const padL = 46;
  const padB = 22;
  const plotH = height - padB - capH;
  const plotW = width - padL - 10;

  ctx.strokeStyle = border;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(padL, 0);
  ctx.lineTo(padL, plotH);
  ctx.lineTo(padL + plotW, plotH);
  ctx.stroke();

  const dates = series.flatMap((s) => s.points.map((p) => p.x));
  const minX = dates.length ? Math.min(...dates) : 0;
  const maxX = dates.length ? Math.max(...dates) : 1;
  const spanX = maxX - minX || 1;
  // A single session would otherwise sit on the y axis; centre it instead.
  const toX = (x) => (dates.length === 1 ? padL + plotW / 2 : padL + ((x - minX) / spanX) * plotW);

  // The y scale comes from chartScale, not a second copy of the same maths.
  // The MAS axis is ORDINAL with a FIXED domain -- 0..5 always -- so a
  // participant who only ever scored 1 and 2 is not drawn as though that
  // two-grade span were the whole scale of spasticity.
  const allY = series.flatMap((s) => s.points.map((p) => p.y)).filter(Number.isFinite);
  const scale = kind === 'mas'
    ? chartScale([0, MAS_ORDER.length - 1], { height: plotH, pad: 0.08 })
    : chartScale(allY, { height: plotH });
  const { toY, min: minY, max: maxY } = scale;

  ctx.fillStyle = fg3;
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  if (kind === 'mas') {
    // Grade labels, not numbers: '1+' is an ordinal position, not a value.
    MAS_ORDER.forEach((g, i) => ctx.fillText(g, padL - 6, toY(i) + 4));
  } else {
    ctx.fillText(maxY.toFixed(1), padL - 6, 10);
    ctx.fillText(minY.toFixed(1), padL - 6, plotH);
  }

  // Points joined by straight segments. No fitted line, no zone shading, no
  // improving/worsening arrow -- each would assert an interpretation the app
  // suppresses everywhere else.
  for (const s of series) {
    const pts = s.points.filter((p) => Number.isFinite(p.y)).sort((a, b) => a.x - b.x);
    ctx.strokeStyle = LEG_COLORS[s.leg] || LEG_COLORS.unset;
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(toX(p.x), toY(p.y)) : ctx.moveTo(toX(p.x), toY(p.y))));
    ctx.stroke();
    for (const p of pts) {
      ctx.beginPath();
      ctx.arc(toX(p.x), toY(p.y), 4, 0, Math.PI * 2);
      // Hollow = thin session or partly unmeasured. Visible as such, not hidden.
      if (p.hollow) ctx.stroke(); else ctx.fill();
    }
  }

  // Captions WRAP to the canvas width. At phone width a single line of this
  // text overruns the canvas and the browser simply clips it -- which on the
  // PT7 chart silently truncates a caveat the spec makes mandatory. Measured
  // rather than guessed at a character count, because the export renders the
  // same text into a much wider canvas.
  ctx.textAlign = 'left';
  ctx.fillStyle = fg3;
  ctx.font = '10px system-ui, sans-serif';
  let capY = plotH + padB + 10;
  for (const line of captions) {
    for (const wrapped of wrapText(ctx, line, width - 8)) {
      ctx.fillText(wrapped, 4, capY);
      capY += 12;
    }
  }
}

// Greedy word wrap against the measured pixel width.
export function wrapText(ctx, text, maxWidth) {
  const words = String(text).split(' ');
  const lines = [];
  let line = '';
  for (const w of words) {
    const next = line ? `${line} ${w}` : w;
    if (line && ctx.measureText(next).width > maxWidth) {
      lines.push(line);
      line = w;
    } else {
      line = next;
    }
  }
  if (line) lines.push(line);
  return lines;
}

// How tall the caption block will be, so the plot can be shortened to make
// room for it instead of the two overlapping.
export function captionHeight(ctx, captions, maxWidth) {
  if (!captions.length) return 0;
  const n = captions.reduce((acc, c) => acc + wrapText(ctx, c, maxWidth).length, 0);
  return n * 12 + 8;
}

export function seriesFor(points, key) {
  const legs = [...new Set(points.map((p) => p.leg))];
  return legs.map((leg) => ({
    leg,
    points: points
      .filter((p) => p.leg === leg)
      .map((p) => ({ x: p.date, y: p[key], hollow: p.thin || p.anyUnmeasured })),
  }));
}

// A pending row carries rank null and is filtered out here, which is what
// leaves a GAP rather than a point at zero.
export function masChartSeries(history) {
  const legs = [...new Set(history.mas.map((m) => m.leg))];
  return legs.map((leg) => ({
    leg,
    points: history.mas
      .filter((m) => m.leg === leg && m.rank !== null)
      .map((m) => ({ x: m.date, y: m.rank, hollow: false })),
  }));
}

function fit(canvas, scale = 1) {
  const w = canvas.clientWidth || 320;
  const h = canvas.clientHeight || 200;
  canvas.width = w * scale;
  canvas.height = h * scale;
  const ctx = canvas.getContext('2d');
  ctx.scale(scale, scale);
  return { ctx, width: w, height: h };
}

export function chartSpecs(history) {
  return [
    ['chart-mas', masChartSeries(history), 'mas', []],
    ['chart-a0', seriesFor(history.points, 'a0'), 'a0', []],
    ['chart-pt7', seriesFor(history.points, 'pt7'), 'pt7', PT7_CAPTIONS],
  ];
}

export function renderCharts(el, history) {
  for (const [id, series, kind, captions] of chartSpecs(history)) {
    const canvas = el(id);
    if (!canvas) continue;
    const { ctx, width, height } = fit(canvas, window.devicePixelRatio || 1);
    drawChart(ctx, { series, width, height, kind, captions });
  }
}
