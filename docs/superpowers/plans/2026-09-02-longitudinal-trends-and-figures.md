# Longitudinal Trends and Figure Export Implementation Plan (Units B + C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a participant a longitudinal view — MAS grade, A0 and the PT7 composite per session over time — fed by on-device history plus imported export bundles, with each chart exportable as a print-DPI PNG.

**Architecture:** A new wasm veneer exposes the composite score for stored parameters, since the existing entry point only works on a live session. Everything above it is pure JavaScript: a sessions→trials join, three series builders, and axis maths, all testable without a DOM. A sixth view renders three hand-rolled canvas charts and re-renders them at 3× into an offscreen canvas for export.

**Tech Stack:** Rust (`mobile-imu-core`) + wasm-bindgen for the score veneer; plain ES modules and canvas 2D in the webapp. No new dependency in either language.

**Spec:** `docs/superpowers/specs/2026-09-02-participant-gate-and-longitudinal-trends-design.md` (Units B and C, §4 and §5)

## Global Constraints

Every task's requirements implicitly include this section.

- **No new dependency.** `mobile-imu-core` is dependency-free by design (it cross-compiles to iOS/Android via UniFFI); the webapp has no bundler and no framework.
- **`wasm.rs` is deliberately logic-free.** Its own doc comment: "anything implemented here is invisible to `cargo test` and therefore unverified." Any behaviour goes in a pure module and `wasm.rs` gets a one-line delegation.
- **No scoring maths may be added, duplicated, or changed.** This plan exposes the existing composite; it does not reimplement it.
- **The PT7 chart's captions are mandatory** (spec §2.1): non-monotonic in severity, and recomputed against the current `HEALTHY_REF` so recalibration moves the whole curve. **No zone colouring, no band shading, no improving/worsening arrow, no fitted trend line.**
- **`ZONE_CLASSIFICATION_CALIBRATED` stays `false`.** Nothing in this plan may surface a zone label.
- **Views use `.view` / `.view.active`,** never the `hidden` attribute. `#banner` stays visible; `#install-gate` outranks everything.
- **DOM-touching code must not run on import.** View modules export factories.
- **Any change under `webapp/src/` runs `npm run build:shell` before commit** (`BUILD_ID` is the service-worker cache key). Never hand-edit `src/build-id.js`.
- **Task 1 rebuilds the wasm, which bumps `ALGORITHM_VERSION` once.** This is expected and accepted (spec §4.2). Do not try to suppress it, and do not use `build:shell` to avoid it — the wasm genuinely changed.
- **Count tests before and after every append.** A heredoc that never ran reports the file's original count and looks like success.
- Baseline: **251 passing** (`cd webapp && npm test`) at commit `1852e58`; `cargo test --manifest-path mobile-imu-core/Cargo.toml` green.

---

## File Structure

**Created:**
- `webapp/src/views/trends.js` — series builders, axis maths, the view factory
- `webapp/src/trend-charts.js` — canvas rendering, shared by screen and PNG export
- `webapp/src/trend-import.js` — pure bundle parsing, dedupe and summary
- `webapp/tests/trends.test.js` — series and axis tests
- `webapp/tests/trend-import.test.js` — import tests

**Modified:**
- `mobile-imu-core/src/pt_score.rs` — `pt_score_from_scalars`
- `mobile-imu-core/src/wasm.rs` — one-line `#[wasm_bindgen]` delegation
- `mobile-imu-core/tests/` — a test pinning the two score paths together
- `webapp/index.html` — the sixth view, its home tile
- `webapp/src/router.js` — `VIEWS` grows to six
- `webapp/src/app.css` — chart and trends styling
- `webapp/src/app.js` — register the trends view, wire history/import/export
- `webapp/README.md` — document the view

**Schema:** unchanged. `DB_VERSION` stays 2 (spec §4.1).

---

### Task 1: Expose the composite score for stored parameters

**Files:**
- Modify: `mobile-imu-core/src/pt_score.rs`
- Modify: `mobile-imu-core/src/wasm.rs`
- Test: `mobile-imu-core/src/pt_score.rs` (inline `#[cfg(test)]`)

**Interfaces:**
- Consumes: `PtParams`, `SpasticityType` (`crate::scoring`), `pt_score_to_json`, `HEALTHY_REF`
- Produces: `pt_score_from_scalars(...) -> String` (Rust); `pt_score_from_params(...) -> String` (wasm)

- [ ] **Step 1: Write the failing test**

Append to `mobile-imu-core/src/pt_score.rs`, inside its existing `#[cfg(test)] mod tests`:

```rust
    // The trends view scores stored trials, which carry only scalars -- the
    // trajectory vectors are not persisted. This pins that the scalar path
    // produces byte-identical JSON to the live path for the same trial, so
    // the two can never drift into scoring the same trial differently.
    #[test]
    fn scalar_path_matches_the_full_params_path() {
        let full = PtParams {
            r2n: 0.62, n: 4.25, phi_max_ratio: 0.31, omega_max_n: 5.1,
            omega_min_n: -3.2, f: 0.87, area_ratio: 0.19,
            omega_peak_deg_s: 210.0, a0_deg: 41.2, a1_deg: 33.0,
            first_trough_depth: 7.4, neutral_deg: 2.1, neutral_deg_raw: 2.4,
            pre_release_deg: 60.0, quality_warn: false, phi_negated: false,
            spasticity_type: SpasticityType::Balanced,
            p_plus: 1.0, p_minus: 0.8, p_total: 1.8,
            phi: vec![1.0, 2.0], ang_r: vec![1.0], t_r: vec![0.0, 0.1],
            omega_s: vec![0.5], pk_i: vec![1], tr_i: vec![0],
        };
        let from_full = pt_score_to_json(&full, &HEALTHY_REF);
        let from_scalars = pt_score_from_scalars(
            0.62, 4.25, 0.31, 5.1, -3.2, 0.87, 0.19, 7.4, 41.2,
        );
        assert_eq!(from_full, from_scalars);
    }

    // The vectors are provably unread by the scoring path; this pins that,
    // so a future change that starts reading them fails here rather than
    // silently mis-scoring every stored trial.
    #[test]
    fn trajectory_vectors_do_not_affect_the_score() {
        let mut a = PtParams {
            r2n: 0.5, n: 3.0, phi_max_ratio: 0.2, omega_max_n: 4.0,
            omega_min_n: -2.0, f: 0.9, area_ratio: 0.1,
            omega_peak_deg_s: 100.0, a0_deg: 45.0, a1_deg: 30.0,
            first_trough_depth: 5.0, neutral_deg: 0.0, neutral_deg_raw: 0.0,
            pre_release_deg: 55.0, quality_warn: false, phi_negated: false,
            spasticity_type: SpasticityType::Balanced,
            p_plus: 1.0, p_minus: 1.0, p_total: 2.0,
            phi: vec![], ang_r: vec![], t_r: vec![], omega_s: vec![],
            pk_i: vec![], tr_i: vec![],
        };
        let empty = pt_score_to_json(&a, &HEALTHY_REF);
        a.phi = vec![9.0; 50];
        a.ang_r = vec![9.0; 50];
        a.t_r = vec![9.0; 50];
        a.omega_s = vec![9.0; 50];
        a.pk_i = vec![7; 5];
        a.tr_i = vec![3; 5];
        assert_eq!(empty, pt_score_to_json(&a, &HEALTHY_REF));
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path mobile-imu-core/Cargo.toml scalar_path`
Expected: FAIL — `cannot find function pt_score_from_scalars`.

- [ ] **Step 3: Write the implementation**

Add to `mobile-imu-core/src/pt_score.rs` (NOT `wasm.rs` — that file is
deliberately logic-free and invisible to `cargo test`):

```rust
/// The composite score for a trial reconstructed from its STORED scalars.
///
/// Stored trials keep the 20 scalar parameters but not the trajectory
/// vectors, so a full `PtParams` cannot be rebuilt from them. It does not
/// need to be: the whole scoring path reads only nine scalars and touches no
/// vector field --
///
///   `pt_score_breakdown` : r2n, n, phi_max_ratio, omega_max_n, omega_min_n,
///                          f, area_ratio
///   `unmeasured_params`  : f, first_trough_depth, n
///   `excursion_reason`   : a0_deg
///
/// -- so the remaining fields are filled with values that cannot influence the
/// result, and `scalar_path_matches_the_full_params_path` pins that claim.
///
/// Arguments are NAMED rather than a positional slice on purpose: a
/// mis-ordered call must be a compile error, not a silently wrong score.
#[allow(clippy::too_many_arguments)]
pub fn pt_score_from_scalars(
    r2n: f64,
    n: f64,
    phi_max_ratio: f64,
    omega_max_n: f64,
    omega_min_n: f64,
    f: f64,
    area_ratio: f64,
    first_trough_depth: f64,
    a0_deg: f64,
) -> String {
    let params = PtParams {
        r2n,
        n,
        phi_max_ratio,
        omega_max_n,
        omega_min_n,
        f,
        area_ratio,
        first_trough_depth,
        a0_deg,
        // Unread by pt_score_breakdown, unmeasured_params and
        // excursion_reason. See this function's doc comment.
        omega_peak_deg_s: 0.0,
        a1_deg: 0.0,
        neutral_deg: 0.0,
        neutral_deg_raw: 0.0,
        pre_release_deg: 0.0,
        quality_warn: false,
        phi_negated: false,
        spasticity_type: SpasticityType::Balanced,
        p_plus: 0.0,
        p_minus: 0.0,
        p_total: 0.0,
        phi: Vec::new(),
        ang_r: Vec::new(),
        t_r: Vec::new(),
        omega_s: Vec::new(),
        pk_i: Vec::new(),
        tr_i: Vec::new(),
    };
    pt_score_to_json(&params, &HEALTHY_REF)
}
```

Ensure `pt_score.rs` imports `SpasticityType`:

```rust
use crate::scoring::{PtParams, SpasticityType};
```

(adjust the existing `use crate::scoring::...` line rather than adding a second one).

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --manifest-path mobile-imu-core/Cargo.toml`
Expected: PASS, whole suite green.

- [ ] **Step 5: Add the wasm veneer**

Add to `mobile-imu-core/src/wasm.rs`:

```rust
/// The composite score for a trial's STORED parameters, for the trends view.
/// `finish_pt_score` only works on a live session with samples pushed into
/// it; a stored trial has scalars and nothing else.
///
/// Logic-free by this module's contract -- the construction and the argument
/// audit live in `pt_score::pt_score_from_scalars`, where `cargo test` sees
/// them.
#[wasm_bindgen]
#[allow(clippy::too_many_arguments)]
pub fn pt_score_from_params(
    r2n: f64,
    n: f64,
    phi_max_ratio: f64,
    omega_max_n: f64,
    omega_min_n: f64,
    f: f64,
    area_ratio: f64,
    first_trough_depth: f64,
    a0_deg: f64,
) -> String {
    crate::pt_score::pt_score_from_scalars(
        r2n, n, phi_max_ratio, omega_max_n, omega_min_n, f, area_ratio,
        first_trough_depth, a0_deg,
    )
}
```

Extend the existing `use crate::pt_score::{...}` line if needed.

- [ ] **Step 6: Rebuild the wasm and confirm the export reached JS**

Run: `cd webapp && npm run build:wasm`
Then: `cd webapp && node -e "import('./src/wasm/mobile_imu_core.js').then(m => console.log(typeof m.pt_score_from_params))"`
Expected: `function`.

`ALGORITHM_VERSION` will have changed. That is expected — record its old and
new values in the commit message.

- [ ] **Step 7: Run the JS suite**

Run: `cd webapp && npm test`
Expected: PASS, 251 (no JS behaviour changed yet).

- [ ] **Step 8: Commit**

```bash
git add mobile-imu-core/src/pt_score.rs mobile-imu-core/src/wasm.rs webapp/src/build-id.js
git commit -m "feat(core): score a trial from its stored scalars"
```

---

### Task 2: `median` and the sessions-to-trials join

**Files:**
- Create: `webapp/src/views/trends.js`
- Test: `webapp/tests/trends.test.js`

**Interfaces:**
- Consumes: nothing
- Produces: `median(values) -> number | null`; `sessionSeries(sessions, trialsBySession, { scoreOf }) -> [{ sessionId, date, leg, a0, pt7, n, thin, anyUnmeasured }]`

- [ ] **Step 1: Write the failing test**

Create `webapp/tests/trends.test.js`:

```js
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

const sessions = [
  { id: 's2', timestamp: Date.UTC(2026, 7, 20) },
  { id: 's1', timestamp: Date.UTC(2026, 7, 10) },
];
const trialsBySession = {
  s1: [
    { id: 't1', side: 'left', params: { a0_deg: 40, r2n: 0.6, n: 4, phi_max_ratio: 0.3, omega_max_n: 5, omega_min_n: -3, f: 0.9, area_ratio: 0.2, first_trough_depth: 7 }, unmeasured: [] },
    { id: 't2', side: 'left', params: { a0_deg: 44, r2n: 0.6, n: 4, phi_max_ratio: 0.3, omega_max_n: 5, omega_min_n: -3, f: 0.9, area_ratio: 0.2, first_trough_depth: 7 }, unmeasured: [] },
  ],
  s2: [
    { id: 't3', side: 'right', params: { a0_deg: 50, r2n: 0.6, n: 4, phi_max_ratio: 0.3, omega_max_n: 5, omega_min_n: -3, f: 0.9, area_ratio: 0.2, first_trough_depth: 7 }, unmeasured: ['f'] },
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/trends.test.js`
Expected: FAIL — `Cannot find module '../src/views/trends.js'`.

- [ ] **Step 3: Write the implementation**

Create `webapp/src/views/trends.js`:

```js
// A participant's history over time. This is the app's first cross-session
// read: every other query is scoped to one session.

// Median, not mean. One unscorable or artifact-laden trial must not drag a
// session's point -- see the spec's "why per-session and not per-trial".
// Non-finite values are skipped rather than poisoning the result, because a
// parameter that could not be measured is stored as a placeholder.
export function median(values) {
  const xs = (values || []).filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

// One point per (session, leg). `scoreOf(trial) -> number | null` is injected
// so this module never reaches for the wasm, and the whole join stays
// testable with plain objects.
//
// A trial with no `side` is grouped as 'unset' rather than dropped or guessed:
// trials recorded before the side selector existed carry null, and filing them
// under a leg they were never attributed to would invent a clinical fact.
export function sessionSeries(sessions, trialsBySession, { scoreOf }) {
  const out = [];
  const ordered = [...(sessions || [])].sort((a, b) => a.timestamp - b.timestamp);

  for (const session of ordered) {
    const trials = (trialsBySession || {})[session.id] || [];
    const byLeg = new Map();
    for (const t of trials) {
      const leg = t.side || 'unset';
      if (!byLeg.has(leg)) byLeg.set(leg, []);
      byLeg.get(leg).push(t);
    }
    for (const [leg, group] of byLeg) {
      out.push({
        sessionId: session.id,
        date: session.timestamp,
        leg,
        a0: median(group.map((t) => t.params && t.params.a0_deg)),
        pt7: median(group.map((t) => scoreOf(t))),
        n: group.length,
        thin: group.length < 2,
        anyUnmeasured: group.some((t) => (t.unmeasured || []).length > 0),
      });
    }
  }
  return out;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd webapp && node --test tests/trends.test.js`
Expected: PASS, 12 tests.

- [ ] **Step 5: Mutation-sweep**

Confirm each mutant ACTUALLY APPLIES before believing its result.

| Mutant | Expected |
| --- | --- |
| `median` returns the mean instead | fail — the outlier test |
| drop the `Number.isFinite` filter | fail — the non-finite test |
| drop the `sort` on sessions | fail — the ordering test |
| `thin: group.length < 1` | fail — the thin test |
| `t.side \|\| 'unset'` becomes `t.side` | fail — the null-side test |

- [ ] **Step 6: Run the full suite and commit**

```bash
cd webapp && npm run build:shell && npm test
git add webapp/src/views/trends.js webapp/tests/trends.test.js webapp/src/build-id.js
git commit -m "feat: session-level aggregation for the trends view"
```

Expected: 263 (251 + 12).

---

### Task 3: `masSeries`

**Files:**
- Modify: `webapp/src/views/trends.js`
- Test: `webapp/tests/trends.test.js`

**Interfaces:**
- Consumes: `MAS_ORDER`, `isPending` (`../mas-store.js`)
- Produces: `masSeries(masRecords) -> [{ date, leg, grade, rank, pending }]`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/trends.test.js`:

```js
const masRows = [
  { leg: 'left', condition: 'rest', assessed_date: '2026-08-20', mas_grade: '2' },
  { leg: 'left', condition: 'rest', assessed_date: '2026-08-10', mas_grade: '1+' },
  { leg: 'right', condition: 'rest', assessed_date: '2026-08-10', mas_grade: '-1' },
];

test('mas rows are ordered oldest first', () => {
  assert.deepEqual(masSeries(masRows).map((p) => p.assessed_date ?? p.date),
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
// it as 0 would read as "no spasticity", the opposite of what it means.
test('a pending grade is marked pending and carries no rank', () => {
  const s = masSeries([{ leg: 'right', assessed_date: '2026-08-10', mas_grade: '-1' }]);
  assert.equal(s[0].pending, true);
  assert.equal(s[0].rank, null);
});

test('legs are kept distinct', () => {
  assert.deepEqual([...new Set(masSeries(masRows).map((p) => p.leg))].sort(), ['left', 'right']);
});

test('no rows produce no series rather than throwing', () => {
  assert.deepEqual(masSeries([]), []);
  assert.deepEqual(masSeries(undefined), []);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/trends.test.js`
Expected: FAIL — `masSeries is not a function`. Count up by exactly 5.

- [ ] **Step 3: Write the implementation**

Append to `webapp/src/views/trends.js`:

```js
import { MAS_ORDER, isPending } from '../mas-store.js';

// Clinician assessments over time. The rank is the grade's ORDINAL POSITION,
// not a numeric reading of it: '1+' sits between '1' and '2' and is neither
// 1.5 nor a number. A pending row carries rank null so the chart can leave a
// gap -- plotting it at zero would read as "no spasticity", the opposite of
// "not yet assessed".
export function masSeries(masRecords) {
  return [...(masRecords || [])]
    .map((r) => ({
      date: Date.parse(`${r.assessed_date}T00:00:00Z`),
      assessed_date: r.assessed_date,
      leg: r.leg || 'unset',
      grade: r.mas_grade,
      rank: isPending(r) ? null : (MAS_ORDER.indexOf(r.mas_grade) >= 0 ? MAS_ORDER.indexOf(r.mas_grade) : null),
      pending: isPending(r),
    }))
    .sort((a, b) => a.date - b.date);
}
```

- [ ] **Step 4: Run test to verify it passes, then commit**

```bash
cd webapp && node --test tests/trends.test.js && npm run build:shell && npm test
git add webapp/src/views/trends.js webapp/tests/trends.test.js webapp/src/build-id.js
git commit -m "feat: MAS grade series with pending rows as gaps"
```

Expected: 268 (263 + 5).

---

### Task 4: `chartScale`

**Files:**
- Modify: `webapp/src/views/trends.js`
- Test: `webapp/tests/trends.test.js`

**Interfaces:**
- Produces: `chartScale(values, { height, pad }) -> { min, max, toY(v) }`

- [ ] **Step 1: Write the failing test**

Append to `webapp/tests/trends.test.js`:

```js
test('the scale spans the data with padding', () => {
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

// A flat series would divide by zero and put every point at NaN.
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/trends.test.js`
Expected: FAIL — `chartScale is not a function`. Count up by exactly 6.

- [ ] **Step 3: Write the implementation**

Append to `webapp/src/views/trends.js`:

```js
// Maps a value onto a canvas y coordinate. Canvas y grows downward, so the
// largest value maps to the smallest y.
//
// A flat series is widened by one unit rather than left as a zero-height
// range: dividing by (max - min) === 0 puts every point at NaN, and a canvas
// silently draws nothing at NaN.
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
```

- [ ] **Step 4: Run test to verify it passes, then commit**

```bash
cd webapp && node --test tests/trends.test.js && npm run build:shell && npm test
git add webapp/src/views/trends.js webapp/tests/trends.test.js webapp/src/build-id.js
git commit -m "feat: chart axis scaling for the trends view"
```

Expected: 274 (268 + 6).

---

### Task 5: The trends view shell

**Files:**
- Modify: `webapp/index.html`, `webapp/src/router.js`, `webapp/src/app.css`, `webapp/src/app.js`
- Modify: `webapp/src/views/trends.js` (add `createTrendsView`)
- Test: `webapp/tests/router.test.js`

**Interfaces:**
- Produces: `createTrendsView({ el, context, loadHistory, importBundle, exportFigure }) -> { onEnter() }`; DOM ids `view-trends`, `trend-empty`, `trend-source`, `chart-mas`, `chart-a0`, `chart-pt7`, `trend-import`, `trend-import-status`

- [ ] **Step 1: Update the router test**

In `webapp/tests/router.test.js`, change:

```js
test('the five views are the ones index.html defines', () => {
  assert.deepEqual(VIEWS, ['home', 'capture', 'trials', 'mas', 'session']);
});
```

to:

```js
test('the six views are the ones index.html defines', () => {
  assert.deepEqual(VIEWS, ['home', 'capture', 'trials', 'mas', 'session', 'trends']);
});
```

Run: `cd webapp && node --test tests/router.test.js` — expect FAIL.

- [ ] **Step 2: Grow `VIEWS`**

In `webapp/src/router.js`:

```js
export const VIEWS = ['home', 'capture', 'trials', 'mas', 'session', 'trends'];
```

Run the router test again — expect PASS.

- [ ] **Step 3: Add the home tile**

In `webapp/index.html`, inside `.tile-grid`, after the Session tile:

```html
        <button class="tile" data-nav="trends">
          <svg class="tile-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 19V5M4 19h16M7 15l4-5 3 3 5-7" fill="none" stroke="currentColor"
                  stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span class="tile-text">
            <span class="tile-title">Trends</span>
            <span class="tile-sub">Over time &amp; figures</span>
          </span>
        </button>
```

- [ ] **Step 4: Add the view section**

In `webapp/index.html`, after `#view-session`'s closing `</section>`:

```html
    <section class="view" id="view-trends">
      <div class="view-head"><button class="btn btn--secondary" data-nav="home">&larr; Back</button>
        <h2>Trends</h2></div>
      <p id="trend-source" class="field-status"></p>
      <p id="trend-empty" class="empty" hidden></p>

      <div class="card">
        <p class="card-label">MAS grade</p>
        <canvas id="chart-mas" class="chart"></canvas>
        <button class="btn btn--secondary chart-export" data-figure="mas">Export PNG</button>
      </div>

      <div class="card">
        <p class="card-label">A0 &mdash; first-flexion amplitude (deg)</p>
        <canvas id="chart-a0" class="chart"></canvas>
        <button class="btn btn--secondary chart-export" data-figure="a0">Export PNG</button>
      </div>

      <div class="card">
        <p class="card-label">PT7 composite</p>
        <canvas id="chart-pt7" class="chart"></canvas>
        <button class="btn btn--secondary chart-export" data-figure="pt7">Export PNG</button>
      </div>

      <div class="card">
        <p class="card-label">Import a session bundle</p>
        <input id="trend-import" type="file" accept=".json,.csv" multiple>
        <p id="trend-import-status" class="field-status"></p>
      </div>
    </section>
```

- [ ] **Step 5: Add the CSS**

Append to `webapp/src/app.css`:

```css
.chart { display: block; width: 100%; height: 200px; }
.chart-export { margin-top: 8px; width: 100%; }
#trend-source { font-family: var(--mono); }
```

- [ ] **Step 6: Add the view factory**

Append to `webapp/src/views/trends.js`:

```js
// The view. Every dependency is injected, so this module stays import-safe
// under `node --test` and the pure functions above can be tested with plain
// objects.
export function createTrendsView({ el, context, loadHistory, importBundle, exportFigure }) {
  let ready = false;
  let latest = null;

  function initOnce() {
    if (ready) return;
    el('trend-import').addEventListener('change', async (e) => {
      const files = [...e.target.files];
      if (files.length === 0) return;
      el('trend-import-status').textContent = 'Importing…';
      const summary = await importBundle(files);
      el('trend-import-status').textContent = summary;
      e.target.value = '';
      await render();
    });
    for (const btn of document.querySelectorAll('.chart-export')) {
      btn.addEventListener('click', () => exportFigure(btn.dataset.figure, latest));
    }
    ready = true;
  }

  async function render() {
    const { participantLabel } = context();
    const history = await loadHistory();
    latest = history;
    const empty = el('trend-empty');
    // Three distinct empty states, because they have three distinct remedies.
    if (!history) {
      empty.textContent = 'No participant selected. Choose one in Session first.';
      empty.hidden = false;
    } else if (history.points.length === 0 && history.mas.length === 0) {
      empty.textContent = `No sessions or assessments recorded for ${participantLabel} on this device. Import a session bundle below to add history from another device.`;
      empty.hidden = false;
    } else {
      empty.hidden = true;
    }
    el('trend-source').textContent = history
      ? `${history.deviceSessions} session(s) on this device · ${history.importedSessions} imported`
      : '';
    if (history) renderCharts(el, history);
  }

  return {
    async onEnter() {
      initOnce();
      await render();
    },
  };
}
```

Add `import { renderCharts } from '../trend-charts.js';` at the top of
`trends.js`. Task 6 creates that module; until then the import fails, which is
why Task 5 and Task 6 are committed together at the end of Task 6.

- [ ] **Step 7: Register the view in `app.js`**

Beside the other `router.register` calls:

```js
  router.register('trends', createTrendsView({
    el,
    context: () => ({
      patientId: currentPatient?.id ?? null,
      participantLabel: currentPatient?.clinic_patient_id ?? '',
    }),
    loadHistory: async () => {
      await ensureSessionReady();
      if (!currentPatient) return null;
      const sessions = await getAll(db, STORES.sessions, 'by_patient', currentPatient.id);
      const trialsBySession = {};
      // N+1 by design (spec 4.1): trials carry no patient_id and N is the
      // number of visits for one participant.
      for (const s of sessions) {
        trialsBySession[s.id] = await getAll(db, STORES.trials, 'by_session', s.id);
      }
      const mas = await getAll(db, STORES.mas, 'by_patient', currentPatient.id);
      return {
        points: sessionSeries(sessions, trialsBySession, { scoreOf: scoreStoredTrial }),
        mas: masSeries(mas),
        deviceSessions: sessions.filter((s) => !s.imported).length,
        importedSessions: sessions.filter((s) => s.imported).length,
      };
    },
    importBundle: (files) => importFiles(files),
    exportFigure: (which, history) => exportTrendFigure(which, history),
  }));
```

`scoreStoredTrial`, `importFiles` and `exportTrendFigure` arrive in Tasks 6–9;
add them as they land. Import `createTrendsView`, `sessionSeries` and
`masSeries` from `./views/trends.js`.

- [ ] **Step 8: Commit after Task 6**

Task 5 does not stand alone — `trends.js` imports `trend-charts.js`, which
Task 6 creates. Run `npm test` only after Task 6 and commit both together.

---

### Task 6: Canvas chart rendering

**Files:**
- Create: `webapp/src/trend-charts.js`
- Test: manual, plus the headless browser walk in Task 10

**Interfaces:**
- Consumes: `MAS_ORDER` (`./mas-store.js`)
- Produces: `renderCharts(el, history)`; `drawChart(ctx, { series, width, height, kind, captions })`

- [ ] **Step 1: Write the renderer**

Create `webapp/src/trend-charts.js`:

```js
// Hand-rolled canvas charts for the trends view. No charting dependency: the
// webapp has no bundler and three fixed chart types do not justify a generic
// abstraction (spec 8).
//
// Shared by the on-screen render and the PNG export, so an exported figure
// cannot drift from what was on screen.

import { MAS_ORDER } from './mas-store.js';
import { chartScale } from './views/trends.js';

const LEG_COLORS = { left: '#1D4ED8', right: '#B45309', unset: '#4B5563' };

// The PT7 captions are MANDATORY (spec 2.1) and are drawn INTO the canvas,
// not into the DOM around it: an exported figure travels without its page,
// and a caveat that lives outside the image does not survive being pasted
// into a slide.
export const PT7_CAPTIONS = [
  'PT7 is non-monotonic in severity: a worsening leg can trend downward.',
  'Recomputed against the current HEALTHY_REF -- recalibration moves this whole curve.',
];

export function drawChart(ctx, { series, width, height, kind, captions = [] }) {
  const cs = getComputedStyle(document.documentElement);
  const fg3 = cs.getPropertyValue('--fg3').trim() || '#6B7280';
  const border = cs.getPropertyValue('--border').trim() || '#D1D5DB';

  ctx.clearRect(0, 0, width, height);

  const capH = captions.length * 14 + (captions.length ? 8 : 0);
  const padL = 44;
  const padB = 26;
  const plotH = height - padB - capH;
  const plotW = width - padL - 10;

  // Axes.
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
  const toX = (x) => padL + ((x - minX) / spanX) * plotW;

  // The y scale comes from chartScale (Task 4), not a second copy of the same
  // maths here. The MAS axis is ORDINAL with a fixed domain -- 0..5 always, so
  // a participant who only ever scored 1 and 2 is not drawn as if that span
  // were the whole scale -- hence the explicit domain and zero padding.
  const allY = series.flatMap((s) => s.points.map((p) => p.y)).filter(Number.isFinite);
  const scale = kind === 'mas'
    ? chartScale([0, MAS_ORDER.length - 1], { height: plotH, pad: 0 })
    : chartScale(allY, { height: plotH });
  const { toY, min: minY, max: maxY } = scale;

  // y labels: the MAS axis is ordinal, so it prints grade labels, not numbers.
  ctx.fillStyle = fg3;
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'right';
  if (kind === 'mas') {
    MAS_ORDER.forEach((g, i) => ctx.fillText(g, padL - 6, toY(i) + 4));
  } else {
    ctx.fillText(maxY.toFixed(1), padL - 6, 10);
    ctx.fillText(minY.toFixed(1), padL - 6, plotH);
  }

  // Series: points joined by straight segments. No fitted line, no zone
  // shading, no arrows -- spec 2.1 mitigation 3.
  for (const s of series) {
    const pts = s.points.filter((p) => Number.isFinite(p.y));
    ctx.strokeStyle = LEG_COLORS[s.leg] || LEG_COLORS.unset;
    ctx.fillStyle = ctx.strokeStyle;
    ctx.lineWidth = 2;
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(toX(p.x), toY(p.y)) : ctx.moveTo(toX(p.x), toY(p.y))));
    ctx.stroke();
    for (const p of pts) {
      ctx.beginPath();
      ctx.arc(toX(p.x), toY(p.y), 4, 0, Math.PI * 2);
      // Hollow means thin or partly unmeasured -- visible as such, not hidden.
      if (p.hollow) { ctx.lineWidth = 2; ctx.stroke(); } else { ctx.fill(); }
    }
  }

  // Captions, drawn into the image.
  ctx.textAlign = 'left';
  ctx.fillStyle = fg3;
  ctx.font = '10px system-ui, sans-serif';
  captions.forEach((line, i) => ctx.fillText(line, 4, plotH + padB + 12 + i * 14));
}

function seriesFor(points, key) {
  const legs = [...new Set(points.map((p) => p.leg))];
  return legs.map((leg) => ({
    leg,
    points: points
      .filter((p) => p.leg === leg)
      .map((p) => ({ x: p.date, y: p[key], hollow: p.thin || p.anyUnmeasured })),
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

export function renderCharts(el, history) {
  const masLegs = [...new Set(history.mas.map((m) => m.leg))];
  const masSeriesData = masLegs.map((leg) => ({
    leg,
    // A pending row carries rank null and is filtered out here, which is what
    // leaves a GAP rather than a point at zero.
    points: history.mas
      .filter((m) => m.leg === leg && m.rank !== null)
      .map((m) => ({ x: m.date, y: m.rank, hollow: false })),
  }));

  const specs = [
    ['chart-mas', masSeriesData, 'mas', []],
    ['chart-a0', seriesFor(history.points, 'a0'), 'a0', []],
    ['chart-pt7', seriesFor(history.points, 'pt7'), 'pt7', PT7_CAPTIONS],
  ];

  for (const [id, series, kind, captions] of specs) {
    const canvas = el(id);
    if (!canvas) continue;
    const { ctx, width, height } = fit(canvas, window.devicePixelRatio || 1);
    drawChart(ctx, { series, width, height, kind, captions });
  }
}
```

- [ ] **Step 2: Run the suite and commit Tasks 5 and 6 together**

```bash
cd webapp && npm run build:shell && npm test
git add webapp/index.html webapp/src/router.js webapp/src/app.css webapp/src/app.js \
        webapp/src/views/trends.js webapp/src/trend-charts.js \
        webapp/tests/router.test.js webapp/src/build-id.js
git commit -m "feat: trends view with MAS, A0 and PT7 charts"
```

Expected: 274 (the router test changed in place, no net count change).

---

### Task 7: Import parsing, dedupe and summary

**Files:**
- Create: `webapp/src/trend-import.js`
- Test: `webapp/tests/trend-import.test.js`

**Interfaces:**
- Consumes: `MAS_FIELDS` (`./mas-store.js`)
- Produces: `parseManifest(text) -> { schema, patient, session, trials }`; `parseMasCsv(text) -> rows[]`; `planImport(bundle, existing) -> { sessions, trials, mas, skipped }`; `importSummary(result) -> string`

- [ ] **Step 1: Write the failing test**

Create `webapp/tests/trend-import.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseManifest, parseMasCsv, planImport, importSummary } from '../src/trend-import.js';

const manifest = JSON.stringify({
  schema: 'pendulastic/session-export/v2',
  patient: { clinic_patient_id: 'P-014' },
  session: { id: 's-1', timestamp: 1000 },
  trials: [{ file: 'x-trial1.jsonl', side: 'left', timestamp: 1001, params: { a0_deg: 40 }, unmeasured: [] }],
  mas: [],
});

test('a v2 manifest parses', () => {
  const m = parseManifest(manifest);
  assert.equal(m.patient.clinic_patient_id, 'P-014');
  assert.equal(m.trials.length, 1);
});

// A v1 bundle predates the mas block; its trials are still importable.
test('a v1 manifest parses with no mas block', () => {
  const v1 = JSON.stringify({ ...JSON.parse(manifest), schema: 'pendulastic/session-export/v1', mas: undefined });
  assert.deepEqual(parseManifest(v1).mas, []);
});

test('an unknown schema is refused by name, not silently', () => {
  const bad = JSON.stringify({ ...JSON.parse(manifest), schema: 'pendulastic/session-export/v9' });
  assert.throws(() => parseManifest(bad), /v9/);
});

test('a mas csv round-trips through the parser', () => {
  const csv = 'participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date,stronger_leg,notes,mas_flexion,mas_extension\r\n'
    + 'P-014,left,rest,stroke,1+,CK,2026-08-31,right,"catch, then release",2,\r\n';
  const rows = parseMasCsv(csv);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].mas_grade, '1+');
  assert.equal(rows[0].notes, 'catch, then release');
});

// Import is additive. A re-import must be a no-op, not a duplicate.
test('a trial already present is skipped, not duplicated', () => {
  const plan = planImport(
    { patient: { clinic_patient_id: 'P-014' }, session: { id: 's-1', timestamp: 1000 },
      trials: [{ id: 't-1', side: 'left', timestamp: 1 }], mas: [] },
    { trialIds: new Set(['t-1']), masIdentities: new Set(), sessionIds: new Set(['s-1']) },
  );
  assert.equal(plan.trials.length, 0);
  assert.equal(plan.skipped.trials, 1);
});

test('a mas row already present by identity is skipped', () => {
  const row = { participant: 'P-014', leg: 'left', condition: 'rest', assessed_date: '2026-08-31', mas_grade: '2' };
  const plan = planImport(
    { patient: { clinic_patient_id: 'P-014' }, session: { id: 's-2', timestamp: 1 }, trials: [], mas: [row] },
    { trialIds: new Set(), masIdentities: new Set(['left|rest|2026-08-31']), sessionIds: new Set() },
  );
  assert.equal(plan.mas.length, 0);
  assert.equal(plan.skipped.mas, 1);
});

test('an import that adds nothing says so rather than staying silent', () => {
  const s = importSummary({ trials: 0, mas: 0, skipped: { trials: 3, mas: 2 } });
  assert.match(s, /nothing new/i);
  assert.match(s, /3/);
});

test('an import that adds something reports the counts', () => {
  const s = importSummary({ trials: 4, mas: 1, skipped: { trials: 0, mas: 0 } });
  assert.match(s, /4/);
  assert.match(s, /1/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd webapp && node --test tests/trend-import.test.js`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the implementation**

Create `webapp/src/trend-import.js`:

```js
// Reads back the artifacts this app exports, so one device can hold a
// participant's whole history. Additive only: an import never deletes or
// overwrites a local record.

import { MAS_FIELDS } from './mas-store.js';

const ACCEPTED = new Set([
  'pendulastic/session-export/v2',
  'pendulastic/session-export/v1', // trials only; predates the mas block
]);

export function parseManifest(text) {
  const m = JSON.parse(text);
  if (!ACCEPTED.has(m.schema)) {
    throw new Error(`Unsupported export schema "${m.schema}". This app reads v1 and v2.`);
  }
  return { schema: m.schema, patient: m.patient, session: m.session, trials: m.trials || [], mas: m.mas || [] };
}

// RFC4180 reader, the inverse of mas-csv.js's writer. Hand-rolled for the same
// reason the writer is: no dependency, and the field set is fixed.
export function parseMasCsv(text) {
  const cells = [];
  let row = [];
  let cur = '';
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { cur += '"'; i += 1; }
      else if (c === '"') quoted = false;
      else cur += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(cur); cur = ''; }
    else if (c === '\r') { /* handled by \n */ }
    else if (c === '\n') { row.push(cur); cells.push(row); row = []; cur = ''; }
    else cur += c;
  }
  if (cur !== '' || row.length) { row.push(cur); cells.push(row); }
  if (cells.length < 2) return [];
  const header = cells[0];
  return cells.slice(1)
    .filter((r) => r.length === header.length)
    .map((r) => Object.fromEntries(header.map((h, i) => [h, r[i]])))
    .map((o) => Object.fromEntries(MAS_FIELDS.map((f) => [f, o[f] ?? ''])));
}

// The identity a MAS row dedupes on -- the same tuple db.js's unique
// by_identity index is built over, minus patient_id, which is resolved at
// import time by clinic_patient_id.
export function masIdentityKey(row) {
  return `${row.leg}|${row.condition}|${row.assessed_date}`;
}

export function planImport(bundle, existing) {
  const skipped = { trials: 0, mas: 0 };
  const trials = [];
  const mas = [];

  for (const t of bundle.trials || []) {
    if (t.id && existing.trialIds.has(t.id)) { skipped.trials += 1; continue; }
    trials.push(t);
  }
  for (const r of bundle.mas || []) {
    if (existing.masIdentities.has(masIdentityKey(r))) { skipped.mas += 1; continue; }
    mas.push(r);
  }
  const sessions = existing.sessionIds.has(bundle.session.id) ? [] : [bundle.session];
  return { sessions, trials, mas, skipped };
}

// Silence after an import is not acceptable: the operator must be able to tell
// an import that did nothing from one that worked.
export function importSummary({ trials, mas, skipped }) {
  if (trials === 0 && mas === 0) {
    return `Nothing new — ${skipped.trials} trial(s) and ${skipped.mas} assessment(s) were already on this device.`;
  }
  return `Imported ${trials} trial(s) and ${mas} assessment(s); skipped ${skipped.trials} duplicate trial(s) and ${skipped.mas} duplicate assessment(s).`;
}
```

- [ ] **Step 4: Run test to verify it passes, mutation-sweep, commit**

Mutants (confirm each applies): remove the `ACCEPTED` check; drop the
`trialIds` dedupe; make `importSummary` return the same string regardless of
counts.

```bash
cd webapp && npm run build:shell && npm test
git add webapp/src/trend-import.js webapp/tests/trend-import.test.js webapp/src/build-id.js
git commit -m "feat: parse and dedupe imported session bundles"
```

Expected: 282 (274 + 8).

---

### Task 8: Wire import and scoring into `app.js`

**Files:**
- Modify: `webapp/src/app.js`

**Interfaces:**
- Consumes: `parseManifest`, `parseMasCsv`, `planImport`, `importSummary`, `masIdentityKey`; `pt_score_from_params` (wasm)
- Produces: `scoreStoredTrial(trial)`, `importFiles(files)`

- [ ] **Step 1: Add the scorer**

In `webapp/src/app.js`, beside the other helpers inside the DOM guard:

```js
  // Scores a STORED trial. The composite is deliberately never persisted --
  // HEALTHY_REF moves -- so it is recomputed here through the wasm entry
  // point added for exactly this, never reimplemented in JS.
  function scoreStoredTrial(trial) {
    const p = trial.params || {};
    const need = ['r2n', 'n', 'phi_max_ratio', 'omega_max_n', 'omega_min_n', 'f', 'area_ratio', 'first_trough_depth', 'a0_deg'];
    if (need.some((k) => !Number.isFinite(p[k]))) return null;
    try {
      return JSON.parse(wasm.pt_score_from_params(
        p.r2n, p.n, p.phi_max_ratio, p.omega_max_n, p.omega_min_n,
        p.f, p.area_ratio, p.first_trough_depth, p.a0_deg,
      )).score;
    } catch {
      return null;
    }
  }
```

Import the wasm module the same way `worker.js` already does; follow that
file's existing init pattern rather than inventing a second one.

- [ ] **Step 2: Add the importer**

```js
  async function importFiles(files) {
    await ensureSessionReady();
    if (!currentPatient) return 'Select a participant in Session before importing.';
    try {
      const texts = await Promise.all(files.map((f) => f.text()));
      const manifestText = texts.find((t) => t.trimStart().startsWith('{'));
      const csvText = texts.find((t) => t.startsWith('participant,'));
      if (!manifestText) return 'No manifest found. Select the -manifest.json file (and optionally the -mas.csv).';

      const bundle = parseManifest(manifestText);
      if (csvText) bundle.mas = parseMasCsv(csvText);

      const sessions = await getAll(db, STORES.sessions, 'by_patient', currentPatient.id);
      const trialIds = new Set();
      for (const s of sessions) {
        for (const t of await getAll(db, STORES.trials, 'by_session', s.id)) trialIds.add(t.id);
      }
      const masRows = await getAll(db, STORES.mas, 'by_patient', currentPatient.id);
      const existing = {
        trialIds,
        sessionIds: new Set(sessions.map((s) => s.id)),
        masIdentities: new Set(masRows.map(masIdentityKey)),
      };

      const plan = planImport(bundle, existing);
      // Imported rows are attributed to the ACTIVE participant, matched by
      // clinic_patient_id at the call site: a bundle from another device
      // carries a different patients.id for the same person.
      for (const s of plan.sessions) {
        await put(db, STORES.sessions, { ...s, patient_id: currentPatient.id, imported: true });
      }
      for (const t of plan.trials) await put(db, STORES.trials, { ...t, session_id: bundle.session.id });
      for (const r of plan.mas) {
        await put(db, STORES.mas, makeMasRecord({ patientId: currentPatient.id, form: r }));
      }
      return importSummary({ trials: plan.trials.length, mas: plan.mas.length, skipped: plan.skipped });
    } catch (err) {
      return `Import failed: ${err instanceof Error ? err.message : String(err)}`;
    }
  }
```

- [ ] **Step 3: Guard the participant mismatch**

Before `planImport`, refuse a bundle for a different person rather than
silently re-attributing it:

```js
      const bundleId = bundle.patient && bundle.patient.clinic_patient_id;
      if (bundleId && bundleId !== currentPatient.clinic_patient_id) {
        return `That bundle is for ${bundleId}, but ${currentPatient.clinic_patient_id} is selected. Switch participant first.`;
      }
```

- [ ] **Step 4: Run the suite and commit**

```bash
cd webapp && npm run build:shell && npm test
git add webapp/src/app.js webapp/src/build-id.js
git commit -m "feat: import session bundles and score stored trials"
```

Expected: 282.

---

### Task 9: PNG figure export (Unit C)

**Files:**
- Modify: `webapp/src/trend-charts.js`, `webapp/src/app.js`

**Interfaces:**
- Consumes: `shareFiles` (`./export.js`), `drawChart`, `PT7_CAPTIONS`
- Produces: `renderFigure(history, which, { scale }) -> Promise<Blob>`

- [ ] **Step 1: Add the offscreen renderer**

Append to `webapp/src/trend-charts.js`:

```js
// Re-runs the SAME renderer into an offscreen canvas at print scale, so an
// exported figure cannot drift from what was on screen. 3x because a phone
// screenshot of a 200px chart is unusable in a slide or a paper.
export function renderFigure(history, which, { scale = 3, width = 900, height = 420 } = {}) {
  const canvas = document.createElement('canvas');
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext('2d');
  ctx.scale(scale, scale);
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--surface').trim() || '#fff';
  ctx.fillRect(0, 0, width, height);

  const spec = {
    mas: [masChartSeries(history), 'mas', []],
    a0: [seriesFor(history.points, 'a0'), 'a0', []],
    pt7: [seriesFor(history.points, 'pt7'), 'pt7', PT7_CAPTIONS],
  }[which];
  if (!spec) throw new Error(`unknown figure "${which}"`);

  drawChart(ctx, { series: spec[0], width, height, kind: spec[1], captions: spec[2] });
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
}

// Extracted from renderCharts so the screen and the figure build the MAS
// series identically.
export function masChartSeries(history) {
  const legs = [...new Set(history.mas.map((m) => m.leg))];
  return legs.map((leg) => ({
    leg,
    points: history.mas
      .filter((m) => m.leg === leg && m.rank !== null)
      .map((m) => ({ x: m.date, y: m.rank, hollow: false })),
  }));
}
```

Refactor `renderCharts` to call `masChartSeries(history)` rather than keeping
its own copy of that mapping.

- [ ] **Step 2: Wire the export in `app.js`**

```js
  async function exportTrendFigure(which, history) {
    if (!history) return;
    const blob = await renderFigure(history, which);
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const name = `pendulastic-${sanitizeForFilename(currentPatient.clinic_patient_id)}-${stamp}-trend-${which}.png`;
    await shareFiles([{ name, type: 'image/png', blob }]);
  }
```

If `shareFiles` takes `{name, type, text}` rather than a blob, extend it to
accept a blob rather than adding a second sharing path — one share
implementation, as with the trial export.

- [ ] **Step 3: Run the suite and commit**

```bash
cd webapp && npm run build:shell && npm test
git add webapp/src/trend-charts.js webapp/src/app.js webapp/src/export.js webapp/src/build-id.js
git commit -m "feat: export trend charts as print-DPI PNGs"
```

---

### Task 10: Build, shell verification, browser walk, README

**Files:**
- Modify: `webapp/README.md`

- [ ] **Step 1: Build**

Run: `cd webapp && npm run build:dist`
Expected: succeeds; `SHELL` lists `./src/views/trends.js`, `./src/trend-charts.js`, `./src/trend-import.js`.

- [ ] **Step 2: Confirm the shell picked them up**

Run:
```bash
cd webapp && node -e "import('./src/build-id.js').then(m => console.log(m.SHELL.filter(s => /trend/.test(s)).join('\n')))"
```
Expected: three paths.

- [ ] **Step 3: Browser walk**

Serve `dist/` over plain HTTP on localhost. Drive the whole walk from ONE
`browse eval` script, stashing the result on `window` (eval does not await a
returned promise; the page drops to `about:blank` between separate CLI calls).
Verify: the Trends tile opens the view; with no participant the empty state
names that remedy; with a participant and no history the empty state names
import; the three canvases have non-zero dimensions; the console is clean.

Screenshot at 390×844 and read it back.

- [ ] **Step 4: Round-trip test**

Export a session from the app, then import that bundle into a database with
the trials deleted, and assert the trend renders the same points. This is
Unit B's equivalent of Task 11's `mas_validation.py` parity check and is the
most valuable single test here.

- [ ] **Step 5: Document in the README**

Add after the MAS export section:

```markdown
## Trends

`view-trends` shows one point per session per leg: MAS grade, A0, and the PT7
composite. Points are the MEDIAN of that session's trials -- single-trial
discrimination for this instrument has been measured at worse than chance, so
a per-trial scatter would plot noise. A session with fewer than two trials, or
with unmeasured parameters, draws a hollow point.

The PT7 chart carries two captions drawn INTO the canvas, so they survive
export: the score is non-monotonic in severity, and it is recomputed against
the current `HEALTHY_REF`, so recalibrating that reference moves the whole
historical curve. There is deliberately no fitted trend line, no zone shading
and no improving/worsening indicator.

The composite is not stored (it would go stale as `HEALTHY_REF` moves); it is
recomputed from each trial's stored scalars through
`pt_score_from_params`, the wasm entry point added for this.

## Importing a session bundle

Trends can ingest a `-manifest.json` (v1 or v2) plus its optional `-mas.csv`.
Import is additive: trials dedupe on `id`, assessments on
(leg, condition, assessed_date), so re-importing the same bundle is a no-op.
A bundle whose `clinic_patient_id` does not match the selected participant is
refused rather than re-attributed.
```

- [ ] **Step 6: Commit**

```bash
git add webapp/README.md webapp/src/build-id.js
git commit -m "docs: describe the trends view and bundle import"
```

---

## Deployment

Not deployment-ready until the spec's §7 gate passes, including the user's
physical-device smoke test on a preview deployment. `pendulastic-app.vercel.app`
is not updated by this plan.

Task 1 bumps `ALGORITHM_VERSION`. Record the before and after values in that
commit so a later reader can tell which exported trials came from which binary.
