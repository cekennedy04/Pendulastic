import { test } from 'node:test';
import assert from 'node:assert/strict';
import { median, sessionSeries, masSeries } from '../src/views/trends.js';
import { chartScale, wrapText, captionHeight, PT7_CAPTIONS, figureName } from '../src/trend-charts.js';

test('median of an odd count is the middle value', () => {
  assert.equal(median([3, 1, 2]), 2);
});

test('median of an even count averages the two middle values', () => {
  assert.equal(median([1, 2, 3, 4]), 2.5);
});

// The median, not the mean, precisely so one artifact-laden trial cannot drag
// a session's point (spec 2.2).
test('median ignores an outlier the mean would follow', () => {
  assert.equal(median([10, 11, 12, 900]), 11.5);
});

test('median of nothing is null, never NaN', () => {
  assert.equal(median([]), null);
  assert.equal(median(undefined), null);
});

test('median skips non-finite values rather than poisoning the result', () => {
  assert.equal(median([1, NaN, 3, Infinity]), 2);
});

const P = { r2n: 0.6, n: 4, phi_max_ratio: 0.3, omega_max_n: 5, omega_min_n: -3, f: 0.9, area_ratio: 0.2, first_trough_depth: 7 };
const sessions = [
  { id: 's2', timestamp: Date.UTC(2026, 7, 20) },
  { id: 's1', timestamp: Date.UTC(2026, 7, 10) },
];
const trialsBySession = {
  s1: [
    { id: 't1', side: 'left', params: { ...P, a0_deg: 40 }, unmeasured: [] },
    { id: 't2', side: 'left', params: { ...P, a0_deg: 44 }, unmeasured: [] },
  ],
  s2: [
    { id: 't3', side: 'right', params: { ...P, a0_deg: 50 }, unmeasured: ['f'] },
  ],
};
const scoreOf = () => 1.25;

test('sessions are ordered oldest first regardless of input order', () => {
  const s = sessionSeries(sessions, trialsBySession, { scoreOf });
  assert.deepEqual(s.map((p) => p.sessionId), ['s1', 's2']);
});

test('a session point is the median across its trials for that leg', () => {
  const s = sessionSeries(sessions, trialsBySession, { scoreOf });
  assert.equal(s.find((p) => p.sessionId === 's1').a0, 42);
});

test('each leg gets its own point', () => {
  const s = sessionSeries(sessions, trialsBySession, { scoreOf });
  assert.deepEqual(s.map((p) => p.leg), ['left', 'right']);
});

// Spec 2.2: a thin session still plots, but is marked so it reads as thin.
test('a session with fewer than two trials is flagged thin', () => {
  const s = sessionSeries(sessions, trialsBySession, { scoreOf });
  assert.equal(s.find((p) => p.sessionId === 's2').thin, true);
  assert.equal(s.find((p) => p.sessionId === 's1').thin, false);
});

test('a session whose trials had unmeasured parameters is flagged', () => {
  const s = sessionSeries(sessions, trialsBySession, { scoreOf });
  assert.equal(s.find((p) => p.sessionId === 's2').anyUnmeasured, true);
  assert.equal(s.find((p) => p.sessionId === 's1').anyUnmeasured, false);
});

test('the composite comes from the injected scorer, never recomputed here', () => {
  const s = sessionSeries(sessions, trialsBySession, { scoreOf: () => 9.5 });
  assert.equal(s[0].pt7, 9.5);
});

// A trial the scorer cannot score (missing parameters) must not poison the
// session's point -- median already skips non-finite, and null is what the
// scorer returns for an unscorable trial.
test('an unscorable trial does not poison its session point', () => {
  const s = sessionSeries(
    sessions, trialsBySession,
    { scoreOf: (t) => (t.id === 't1' ? null : 2.0) },
  );
  assert.equal(s.find((p) => p.sessionId === 's1').pt7, 2.0);
});

test('a session with no trials produces no point rather than an empty one', () => {
  assert.deepEqual(sessionSeries([{ id: 'sX', timestamp: 1 }], { sX: [] }, { scoreOf }), []);
});

// A trial recorded before the side selector existed carries side null. It
// must not be silently filed under a leg it was never attributed to.
test('a trial with no side is grouped as unset, not dropped and not guessed', () => {
  const s = sessionSeries(
    [{ id: 'sN', timestamp: 1 }],
    { sN: [{ id: 'tn', side: null, params: { a0_deg: 30 }, unmeasured: [] }] },
    { scoreOf },
  );
  assert.equal(s.length, 1);
  assert.equal(s[0].leg, 'unset');
});

// ---- MAS grades over time -------------------------------------------------
const masRows = [
  { leg: 'left', condition: 'rest', assessed_date: '2026-08-20', mas_grade: '2' },
  { leg: 'left', condition: 'rest', assessed_date: '2026-08-10', mas_grade: '1+' },
  { leg: 'right', condition: 'rest', assessed_date: '2026-08-10', mas_grade: '-1' },
];

test('mas rows are ordered oldest first', () => {
  assert.deepEqual(masSeries(masRows).map((p) => p.assessed_date),
    ['2026-08-10', '2026-08-10', '2026-08-20']);
});

// The ordinal position, not the label, is what a y-axis can plot. '1+' sits
// between '1' and '2' -- it is not 1.5 and it is not a number.
test('a grade carries its ordinal rank', () => {
  const s = masSeries([{ leg: 'left', assessed_date: '2026-08-10', mas_grade: '1+' }]);
  assert.equal(s[0].rank, 2);
  assert.equal(s[0].grade, '1+');
});

// A pending assessment is an owed observation, not a grade of zero. Plotting
// it at 0 would read as "no spasticity", the opposite of what it means.
test('a pending grade is marked pending and carries no rank', () => {
  const s = masSeries([{ leg: 'right', assessed_date: '2026-08-10', mas_grade: '-1' }]);
  assert.equal(s[0].pending, true);
  assert.equal(s[0].rank, null);
});

// Grade 0 is a real, meaningful assessment -- no spasticity. It must carry
// rank 0, not be mistaken for "absent" by a falsy check somewhere downstream.
test('grade 0 carries rank 0 and is not treated as missing', () => {
  const s = masSeries([{ leg: 'left', assessed_date: '2026-08-10', mas_grade: '0' }]);
  assert.equal(s[0].rank, 0);
  assert.equal(s[0].pending, false);
});

// A grade the app does not know must not silently plot at the bottom of the
// scale as though it were 0.
test('an unrecognised grade carries no rank rather than plotting as zero', () => {
  const s = masSeries([{ leg: 'left', assessed_date: '2026-08-10', mas_grade: '1.5' }]);
  assert.equal(s[0].rank, null);
});

test('legs are kept distinct', () => {
  assert.deepEqual([...new Set(masSeries(masRows).map((p) => p.leg))].sort(), ['left', 'right']);
});

test('no rows produce no series rather than throwing', () => {
  assert.deepEqual(masSeries([]), []);
  assert.deepEqual(masSeries(undefined), []);
});

// validateMasForm requires a leg, so a row SAVED by this app always has one.
// An IMPORTED row need not: a hand-edited or third-party mas_scores.csv can
// carry a blank. It must group as 'unset' like a trial does, not as the
// string "undefined" and not silently under a real leg.
test('a mas row with no leg groups as unset', () => {
  const s = masSeries([{ assessed_date: '2026-08-10', mas_grade: '2' }]);
  assert.equal(s[0].leg, 'unset');
  assert.equal(masSeries([{ leg: '', assessed_date: '2026-08-10', mas_grade: '2' }])[0].leg, 'unset');
});

// ---- axis scaling ---------------------------------------------------------
test('the scale spans the data', () => {
  const s = chartScale([10, 20], { height: 100, pad: 0 });
  assert.equal(s.min, 10);
  assert.equal(s.max, 20);
});

// Canvas y grows downward: the largest value must map to the smallest y.
test('larger values map to smaller y', () => {
  const s = chartScale([0, 10], { height: 100, pad: 0 });
  assert.ok(s.toY(10) < s.toY(0));
});

test('the extremes map to the edges of the height', () => {
  const s = chartScale([0, 10], { height: 100, pad: 0 });
  assert.equal(s.toY(10), 0);
  assert.equal(s.toY(0), 100);
});

// Without widening, (max - min) is 0, every point maps to NaN, and a canvas
// silently draws nothing at NaN -- a flat series would vanish rather than
// showing as flat.
test('a flat series still produces a usable scale', () => {
  const s = chartScale([5, 5, 5], { height: 100, pad: 0 });
  assert.ok(Number.isFinite(s.toY(5)));
  assert.notEqual(s.min, s.max);
});

test('non-finite values are ignored when finding the range', () => {
  const s = chartScale([1, NaN, 9, null], { height: 100, pad: 0 });
  assert.equal(s.min, 1);
  assert.equal(s.max, 9);
});

test('an empty series produces a scale rather than throwing', () => {
  const s = chartScale([], { height: 100, pad: 0 });
  assert.ok(Number.isFinite(s.toY(0)));
});

// Padding keeps a point from being drawn exactly on the axis line, where it
// is half-clipped and reads as a rendering fault.
test('padding widens the range beyond the data', () => {
  const s = chartScale([0, 10], { height: 100 });
  assert.ok(s.min < 0);
  assert.ok(s.max > 10);
});

// ---- caption wrapping -----------------------------------------------------
// The PT7 captions are mandatory, and at phone width a single line of that
// text overruns the canvas -- the browser then clips it silently, truncating
// a caveat the spec requires. Measured, not guessed at a character count,
// because the PNG export renders the same text into a much wider canvas.
const fakeCtx = { measureText: (t) => ({ width: t.length * 5 }) };

test('a line that fits is not wrapped', () => {
  assert.deepEqual(wrapText(fakeCtx, 'short line', 1000), ['short line']);
});

test('a long line wraps on word boundaries', () => {
  const lines = wrapText(fakeCtx, 'aaa bbb ccc ddd', 40);
  assert.ok(lines.length > 1);
  assert.equal(lines.join(' '), 'aaa bbb ccc ddd');
});

// A word longer than the whole width must still be emitted, not dropped and
// not loop forever.
test('an unbreakable word is emitted rather than dropped', () => {
  const lines = wrapText(fakeCtx, 'HEALTHY_REFERENCE_CONSTANT', 10);
  assert.deepEqual(lines, ['HEALTHY_REFERENCE_CONSTANT']);
});

test('caption height grows with the number of wrapped lines', () => {
  const one = captionHeight(fakeCtx, ['aaa'], 1000);
  const many = captionHeight(fakeCtx, ['aaa bbb ccc ddd eee fff'], 40);
  assert.ok(many > one);
});

test('no captions reserve no height', () => {
  assert.equal(captionHeight(fakeCtx, [], 100), 0);
});

// The real PT7 captions must wrap to something readable at phone width.
test('the mandatory PT7 captions wrap rather than clip at phone width', () => {
  const lines = PT7_CAPTIONS.flatMap((c) => wrapText(fakeCtx, c, 320));
  assert.ok(lines.length >= 2);
  for (const l of lines) assert.ok(fakeCtx.measureText(l).width <= 320, `overruns: ${l}`);
  assert.ok(lines.join(' ').includes('whole curve'), 'the caption must not lose its ending');
});

// ---- figure naming --------------------------------------------------------
test('a figure filename shares the session export stem', () => {
  const n = figureName('P-014', 'mas', new Date(Date.UTC(2026, 8, 4)));
  assert.equal(n, 'pendulastic-P-014-20260904-trend-mas.png');
});

// A clinician types the participant id freehand; it reaches a filename.
test('a participant id with unsafe characters is sanitised for the filename', () => {
  const n = figureName('P 014/left', 'a0', new Date(Date.UTC(2026, 8, 4)));
  assert.ok(!/[^A-Za-z0-9_.-]/.test(n.replace('.png', '')), n);
});

test('a missing participant id still yields a usable filename', () => {
  assert.match(figureName(null, 'pt7', new Date(Date.UTC(2026, 8, 4))), /unknown-patient/);
});
