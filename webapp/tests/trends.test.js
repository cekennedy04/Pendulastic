// Trend series: the two provenance dimensions, and the counts that keep a
// chart honest.
//
// The scorer is injected, so these run without wasm -- same reason
// params_json lives outside wasm.rs on the Rust side.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  buildTrendSeries, summaryLine, referenceTooltip, EXCLUDED,
} from '../src/trends.js';

const ACTIVE = 'aaaaaaaaaaaa';
const LEGACY = 'bbbbbbbbbbbb';

function scalars(refId = ACTIVE, over = {}) {
  return {
    r2n: 0.1, n: 0.2, phi_max_ratio: 0.3, omega_max_n: 0.4,
    omega_min_n: 0.5, f: 0.6, area_ratio: 0.7,
    total: 2.8, simple: 1.1, unmeasured_count: 0,
    healthy_ref_id: refId, ...over,
  };
}

function trial(over = {}) {
  return {
    id: over.id || 'trial-1',
    timestamp: over.timestamp ?? 1000,
    side: 'left',
    params: { r2n: 1.0 },
    drift_correction: 'analysis',
    unmeasured: [],
    algorithm_version: '0.1.0+abc.def',
    ...over,
  };
}

const scoreAll = () => scalars();

// ── the convention dimension: filter, never convert ──────────────────────────

test('a trial recorded under the other convention is excluded, not converted', () => {
  // Its params are the other convention's; deriving these would need the raw
  // log and a full AHRS replay. Silently plotting it would compare numbers the
  // codebase persists drift_correction specifically to keep apart.
  const out = buildTrendSeries(
    [trial({ id: 'a' }), trial({ id: 'b', drift_correction: 'live' })],
    { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.equal(out.points.length, 1);
  assert.equal(out.points[0].id, 'a');
  assert.equal(out.excluded.length, 1);
  assert.equal(out.excluded[0].reason, EXCLUDED.convention);
  assert.equal(out.excluded[0].recorded, 'live');
});

test('selecting the live convention flips which trials are shown', () => {
  const trials = [trial({ id: 'a' }), trial({ id: 'b', drift_correction: 'live' })];
  const out = buildTrendSeries(trials,
    { convention: 'live', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.deepEqual(out.points.map((p) => p.id), ['b']);
});

test('an unknown convention is refused rather than silently emptying the chart', () => {
  assert.throws(() => buildTrendSeries([trial()],
    { convention: 'detrended', scoreParams: scoreAll, activeRefId: ACTIVE }),
    /unknown convention/);
});

// ── the reference dimension: recompute, then annotate ────────────────────────

test('a point captured under an older reference is flagged but still plotted', () => {
  const out = buildTrendSeries([trial()], {
    convention: 'analysis',
    scoreParams: () => scalars(LEGACY),
    activeRefId: ACTIVE,
  });
  assert.equal(out.points.length, 1, 'must still be plotted');
  assert.equal(out.points[0].recomputedAcrossReference, true);
  assert.equal(out.summary.recomputedAcrossReference, 1);
});

test('a point captured under the active reference is not flagged', () => {
  const out = buildTrendSeries([trial()],
    { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.equal(out.points[0].recomputedAcrossReference, false);
  assert.equal(referenceTooltip(out.points[0], ACTIVE), null);
});

test('the tooltip says the stored measurements are unchanged', () => {
  // The clinically load-bearing clause. Scores are never persisted -- only
  // params are -- so "the score changed" would invite the conclusion that the
  // patient changed, or that the archive was rewritten.
  const out = buildTrendSeries([trial()], {
    convention: 'analysis', scoreParams: () => scalars(LEGACY), activeRefId: ACTIVE,
  });
  const tip = referenceTooltip(out.points[0], ACTIVE);
  assert.match(tip, /stored measurements are unchanged/);
  assert.match(tip, new RegExp(ACTIVE));
  assert.match(tip, new RegExp(LEGACY));
});

// ── nothing vanishes quietly ─────────────────────────────────────────────────

test('an unparseable record is excluded with its own reason', () => {
  // params_from_json refuses a record missing any field rather than defaulting
  // it, so null here is a real outcome.
  const out = buildTrendSeries([trial()],
    { convention: 'analysis', scoreParams: () => null, activeRefId: ACTIVE });
  assert.equal(out.points.length, 0);
  assert.equal(out.excluded[0].reason, EXCLUDED.unparseable);
});

test('a record missing a required field is excluded, not defaulted', () => {
  const bad = trial();
  delete bad.drift_correction;
  const out = buildTrendSeries([bad],
    { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.equal(out.excluded[0].reason, EXCLUDED.incomplete);
});

test('the summary line names every exclusion rather than just shrinking n', () => {
  const trials = [
    trial({ id: 'a' }),
    trial({ id: 'b', drift_correction: 'live' }),
    trial({ id: 'c', drift_correction: 'live' }),
  ];
  const out = buildTrendSeries(trials,
    { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  const line = summaryLine(out.summary);
  assert.match(line, /Showing 1 of 3 trials/);
  assert.match(line, /2 recorded under the live convention/);
});

test('an empty history says so rather than rendering an empty axis', () => {
  const out = buildTrendSeries([], {
    convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.equal(summaryLine(out.summary), 'No trials yet.');
});

test('a clean history mentions no exclusions', () => {
  const out = buildTrendSeries([trial({ id: 'a' }), trial({ id: 'b' })],
    { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.equal(summaryLine(out.summary),
    'Showing 2 of 2 trials (analysis convention).');
});

// ── ordering and carried fields ──────────────────────────────────────────────

test('points come back in chronological order regardless of input order', () => {
  const out = buildTrendSeries([
    trial({ id: 'late', timestamp: 3000 }),
    trial({ id: 'early', timestamp: 1000 }),
    trial({ id: 'mid', timestamp: 2000 }),
  ], { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.deepEqual(out.points.map((p) => p.id), ['early', 'mid', 'late']);
});

test('unmeasured comes from the record, not from re-deriving it', () => {
  // session-store.js keeps it because it is a property of the MEASUREMENT
  // rather than of the reference, so the archive's own answer wins.
  const out = buildTrendSeries([trial({ unmeasured: ['r2n', 'phi_max_ratio'] })],
    { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.deepEqual(out.points[0].unmeasured, ['r2n', 'phi_max_ratio']);
});

test('per-parameter contributions are carried through for the breakdown chart', () => {
  const out = buildTrendSeries([trial()],
    { convention: 'analysis', scoreParams: scoreAll, activeRefId: ACTIVE });
  assert.equal(out.points[0].contributions.omega_max_n, 0.4);
  assert.equal(out.points[0].total, 2.8);
});
