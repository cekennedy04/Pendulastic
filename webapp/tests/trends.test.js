import { test } from 'node:test';
import assert from 'node:assert/strict';
import { median, sessionSeries } from '../src/views/trends.js';

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
