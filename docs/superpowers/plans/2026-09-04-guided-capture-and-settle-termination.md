# Guided Capture and Settle-Based Termination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** End a trial automatically once the limb has been still for five seconds, flag trials the operator ends early, and show the clinician — visually and audibly — how long to hold and how long to wait.

**Architecture:** A post-release stillness accumulator in `TrialSession` mirrors the hold logic already there, gating on the gyro-only `ZERO_CAPTURE_GUARD_RAD_S` bound rather than the stricter gyro+accel check that is documented as not firing on real strapped-sensor data. A fifth `HoldState` variant signals completion; `app.js` reacts by firing the path `Stop` already fires. Quality flags ride the `capture_quality` field that already exists and already exports. Guidance is presentation over that data and adds no new state.

**Tech Stack:** Rust (`mobile-imu-core`) + wasm-bindgen; plain ES modules, canvas-free DOM, and WebAudio in the webapp. No new dependency in either language.

**Spec:** `docs/superpowers/specs/2026-09-04-guided-capture-and-settle-termination-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **No new dependency.** `mobile-imu-core` is dependency-free by design; the webapp has no bundler and no framework.
- **`wasm.rs` is deliberately logic-free.** Its own doc: "anything implemented here is invisible to `cargo test` and therefore unverified." Behaviour goes in a pure module; `wasm.rs` gets a one-line delegation.
- **No scoring maths changes.** This plan changes when recording STOPS, never how a recording is scored.
- **The settle test is gyro-only**, against `ZERO_CAPTURE_GUARD_RAD_S` (0.3 rad/s). Do NOT use `is_stationary_window`: its own doc records it never fires for a meaningful fraction of genuinely fine trials on a strapped sensor, and with no time cap those trials would record indefinitely.
- **Trials are flagged, never rejected or dropped** (the 2026-08-27 rule).
- **Audio is additive only.** Every cue has a visual counterpart; a muted phone must lose nothing. Audio may never be the sole carrier of a state change.
- **`STATES` and `CLASSES` in `app.js` are indexed by `state_code()`.** Both must grow together; a short array renders `undefined` rather than throwing.
- **Any change under `webapp/src/` runs `npm run build:shell` before commit** (`BUILD_ID` is the service-worker cache key). Never hand-edit `src/build-id.js`.
- **Rust sources are CRLF; webapp sources are LF.** Splice with the file's own convention or the file ends up mixed.
- **Count tests before and after every append**, and confirm every mutant actually applied before believing its result.
- Baseline: **307 passing** (`cd webapp && npm test`), **37 passing** (`cargo test --manifest-path mobile-imu-core/Cargo.toml`) at commit `1bea6ca`.

---

## File Structure

**Modified:**
- `mobile-imu-core/src/session.rs` — `Settled` variant, `SETTLE_TARGET_S`, `gyro_settle`, `advance_settle`, `settle_s()`
- `mobile-imu-core/src/wasm.rs` — `settle_s()` delegation
- `webapp/src/app.js` — state arrays, auto-stop, alert, quality classification, audio unlock
- `webapp/src/session-store.js` — `capture_protocol_version`, `settle_target_s` on the trial record
- `webapp/src/export.js` — manifest `v3`, new trial fields
- `webapp/src/trend-import.js` — accept `v3`
- `webapp/src/capture-feedback.js` — **created**: quality classifier, beep scheduler (pure)
- `webapp/src/audio-cues.js` — **created**: WebAudio tone player
- `webapp/index.html`, `webapp/src/app.css` — progress bar, positioning diagram
- `webapp/tests/capture-feedback.test.js` — **created**
- `webapp/tests/app.test.js`, `export.test.js`, `trend-import.test.js`

**Schema:** IndexedDB unchanged (`DB_VERSION` stays 2). New trial fields are additive; older records read `capture_protocol_version` as absent = 1.

---

### Task 1: Settle accumulator and the `Settled` state

**Files:**
- Modify: `mobile-imu-core/src/session.rs`
- Test: `mobile-imu-core/src/session.rs` (inline `#[cfg(test)]`)

**Interfaces:**
- Consumes: `recently_calm`, `SampleBuf`, `ZERO_CAPTURE_GUARD_RAD_S`, `GYRO_BIAS_WINDOW_S` (`crate::stillness`)
- Produces: `HoldState::Settled`; `SETTLE_TARGET_S`; `TrialSession::settle_s() -> f64`

- [ ] **Step 1: Write the failing test**

Append inside `session.rs`'s existing `#[cfg(test)] mod tests`. Use the file's
own helpers for building a session and pushing samples — read the existing
tests first and follow them rather than inventing a fixture.

```rust
    // A limb at rest after release must terminate the trial on its own. The
    // gate is gyro-magnitude only (ZERO_CAPTURE_GUARD_RAD_S), NOT the
    // stricter gyro+accel window, whose own doc records it never firing for
    // a meaningful fraction of genuinely fine strapped-sensor trials.
    #[test]
    fn five_seconds_of_stillness_settles_the_trial() {
        let mut s = released_session();
        push_still(&mut s, 5.2);
        assert_eq!(s.state(), HoldState::Settled);
    }

    #[test]
    fn settling_does_not_fire_before_the_target() {
        let mut s = released_session();
        push_still(&mut s, 4.5);
        assert_eq!(s.state(), HoldState::Released);
        assert!(s.settle_s() > 3.0, "settle_s should be accumulating: {}", s.settle_s());
    }

    // The same reset shape reset_hold() uses. Movement mid-settle must send
    // the accumulator back to zero, not merely pause it.
    #[test]
    fn movement_resets_the_settle_accumulator() {
        let mut s = released_session();
        push_still(&mut s, 3.0);
        assert!(s.settle_s() > 1.0);
        push_moving(&mut s, 0.3);
        assert_eq!(s.settle_s(), 0.0);
        assert_eq!(s.state(), HoldState::Released);
    }

    // The population this app is for. A limb with clonus must never
    // self-terminate -- there is deliberately no time cap, so the operator
    // ends it.
    #[test]
    fn a_limb_that_never_settles_never_terminates() {
        let mut s = released_session();
        push_moving(&mut s, 30.0);
        assert_eq!(s.state(), HoldState::Released);
        assert_eq!(s.settle_s(), 0.0);
    }

    // Settled is terminal: further samples must not walk it back to Released.
    #[test]
    fn settled_is_terminal() {
        let mut s = released_session();
        push_still(&mut s, 5.2);
        assert_eq!(s.state(), HoldState::Settled);
        push_moving(&mut s, 2.0);
        assert_eq!(s.state(), HoldState::Settled);
    }

    // Samples must still be recorded while settling, or the tail median that
    // neutral_deg is computed from would be missing exactly the settled part.
    #[test]
    fn samples_are_still_logged_while_settling() {
        let mut s = released_session();
        let before = s.sample_count();
        push_still(&mut s, 2.0);
        assert!(s.sample_count() > before);
    }
```

Add these helpers beside the existing test fixtures:

```rust
    /// A session driven to Released, so settle behaviour can be exercised
    /// directly without replaying a whole swing.
    fn released_session() -> TrialSession {
        let mut s = TrialSession::new(ReplayConfig::default());
        s.force_state_for_test(HoldState::Released);
        s
    }

    /// Push `secs` of gyro samples at 50 Hz whose magnitude is below
    /// ZERO_CAPTURE_GUARD_RAD_S.
    fn push_still(s: &mut TrialSession, secs: f64) {
        push_at(s, secs, 0.05);
    }

    /// Push `secs` of gyro samples clearly above the guard.
    fn push_moving(s: &mut TrialSession, secs: f64) {
        push_at(s, secs, 1.5);
    }

    fn push_at(s: &mut TrialSession, secs: f64, mag: f64) {
        let dt = 0.02;
        let start = s.last_t_for_test();
        let n = (secs / dt).round() as i32;
        for i in 1..=n {
            let t = start + dt * f64::from(i);
            s.push(RawSample { t, sensor: Sensor::Accel, v: [0.0, 0.0, 9.81] });
            s.push(RawSample { t, sensor: Sensor::Gyro, v: [mag, 0.0, 0.0] });
        }
    }
```

And two test-only accessors on `TrialSession`, marked so their purpose is
unambiguous:

```rust
    /// Test-only: drive the state machine directly, so settle behaviour can
    /// be exercised without replaying a full swing through the detector.
    #[cfg(test)]
    pub fn force_state_for_test(&mut self, s: HoldState) {
        self.state = s;
    }

    #[cfg(test)]
    pub fn last_t_for_test(&self) -> f64 {
        self.samples.last().map_or(0.0, |s| s.t)
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path mobile-imu-core/Cargo.toml settle`
Expected: FAIL — no `HoldState::Settled`, no `settle_s`.

- [ ] **Step 3: Write the implementation**

In `session.rs`:

```rust
/// Continuous post-release stillness required before a trial self-terminates.
///
/// Five seconds because `neutral_deg` is the settled-tail median and every
/// angle in the trial is expressed relative to it (`scoring.rs`), so a short
/// tail shifts the whole waveform including `a0_deg`. The opposite failure --
/// an over-long tail fabricating oscillations -- is already guarded by
/// `ACTIVE_WINDOW_CAP_SEC`.
pub const SETTLE_TARGET_S: f64 = 5.0;
```

Extend the enum:

```rust
    /// Post-release stillness held for `SETTLE_TARGET_S`. Terminal: further
    /// samples are logged but cannot walk the state back.
    Settled,
```

Extend the struct and `new()`:

```rust
    /// Trailing gyro window for post-release settle detection. Separate from
    /// `gyro_hold` on purpose: that buffer stops being maintained once the
    /// state is Released, and the release detector reads it as of just BEFORE
    /// the current sample -- an ordering contract settling has no part in.
    gyro_settle: SampleBuf,
    settle_since: Option<f64>,
    settle_s: f64,
```

Route post-release samples:

```rust
    pub fn push(&mut self, s: RawSample) {
        if s.sensor == Sensor::Gyro {
            match self.state {
                HoldState::Released => self.advance_settle(s.v, s.t),
                HoldState::Settled => {}
                _ => self.advance_hold(s.v, s.t),
            }
        }
        self.samples.push(s);
    }
```

And the accumulator, deliberately the same shape as `advance_hold`:

```rust
    /// Post-release settling. Mirrors `advance_hold`: accumulate while calm,
    /// reset to zero on movement.
    ///
    /// Gates on `recently_calm` -- gyro magnitude below
    /// `ZERO_CAPTURE_GUARD_RAD_S`, empirically derived from the reference
    /// corpus -- and NOT on `is_stationary_window`, whose own doc records
    /// that its stricter gyro+accel bound never fires for a meaningful
    /// fraction of genuinely fine trials on a strapped sensor. With no time
    /// cap, using it here would leave those trials recording indefinitely.
    fn advance_settle(&mut self, omega: [f64; 3], t: f64) {
        self.gyro_settle.push((t, omega));
        let cutoff = t - GYRO_BIAS_WINDOW_S;
        self.gyro_settle.retain(|(tt, _)| *tt >= cutoff);

        if !recently_calm(&self.gyro_settle, t) {
            self.settle_since = None;
            self.settle_s = 0.0;
            return;
        }

        let start = *self.settle_since.get_or_insert(t);
        self.settle_s = t - start;
        if self.settle_s >= SETTLE_TARGET_S {
            self.state = HoldState::Settled;
        }
    }

    /// Seconds of continuous post-release stillness accumulated so far.
    pub fn settle_s(&self) -> f64 {
        self.settle_s
    }
```

Extend the `use` line: `use crate::stillness::{recently_calm, SampleBuf, GYRO_BIAS_WINDOW_S, ZERO_CAPTURE_GUARD_RAD_S};`

Check whether `state_code()` lives in `session.rs` or `wasm.rs` and add code
`4` for `Settled` wherever the existing mapping is.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --manifest-path mobile-imu-core/Cargo.toml`
Expected: PASS, 43 tests (37 + 6).

- [ ] **Step 5: Mutation sweep**

Apply each by LINE INDEX or an exact-match check that throws when the needle
is absent — a mutant that silently fails to apply looks identical to one that
was not caught.

| Mutant | Expected |
| --- | --- |
| `settle_s = 0.0` reset removed on movement | fail — the reset test |
| `>= SETTLE_TARGET_S` becomes `> 0.0` | fail — fires before target |
| `HoldState::Settled => {}` arm removed | fail — terminal test |
| `recently_calm` inverted | fail — never-settles test |

- [ ] **Step 6: Commit**

```bash
git add mobile-imu-core/src/session.rs
git commit -m "feat(core): terminate a trial after five seconds of stillness"
```

---

### Task 2: Expose settling to the app and auto-stop

**Files:**
- Modify: `mobile-imu-core/src/wasm.rs`, `webapp/src/app.js`, `webapp/tests/app.test.js`

**Interfaces:**
- Produces: wasm `settle_s()`; state code `4`; auto-stop wiring

- [ ] **Step 1: Add the wasm accessor**

In `wasm.rs`, beside `calm_s()` / `drift_deg()`:

```rust
    /// Seconds of continuous post-release stillness. Drives the settle
    /// progress bar; the termination decision itself is made in `session`.
    pub fn settle_s(&self) -> f64 {
        self.inner.settle_s()
    }
```

- [ ] **Step 2: Write the failing JS test**

Append to `webapp/tests/app.test.js`:

```js
// STATES and CLASSES are indexed by state_code(), which now reaches 4 for
// Settled. A short array renders `undefined` into the guide rather than
// throwing, so nothing else would catch a mismatch.
test('the state arrays cover every HoldState code including Settled', () => {
  assert.equal(STATES.length, 5);
  assert.equal(CLASSES.length, 5);
  assert.match(STATES[4], /done|complete|settled/i);
});
```

Export `STATES` and `CLASSES` from `app.js` if they are not already exported;
they are module-level consts today.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd webapp && node --test tests/app.test.js`
Expected: FAIL — length 4.

- [ ] **Step 4: Grow the arrays and wire auto-stop**

In `webapp/src/app.js`:

```js
export const STATES = ['MOVING\nhold still', 'HOLDING', 'READY\nrelease now', 'RELEASED\nlet it settle', 'SETTLED\ntrial complete'];
export const CLASSES = ['moving', 'holding', 'ready', 'fired', 'settled'];
```

In `onState`, when the code is 4, fire the same completion path `Stop` fires.
Read the Stop handler first: it captures the export handle via
`retainExportHandle` BEFORE nulling `session`, and that ordering is
load-bearing (see its comment). Extract that body into a named function and
call it from both places rather than duplicating it — two paths that both end
a trial will drift.

Add `.settled` to `app.css` beside the other state classes, using
`--ready-bg` (the trial completed successfully).

**`Stop` must still end a trial at any moment, including mid-settle** (spec
§3.5 criterion 5). This task is where that would regress: it changes `push()`'s
routing and factors the Stop body out for reuse. After extracting, confirm by
inspection that the extracted function still captures the export handle
*before* nulling `session` — that ordering is the C1 fix and its comment
explains why reversing it loses the handle. Task 8's walk exercises the path
end to end; this step is the reading that catches it earlier and cheaper.

- [ ] **Step 5: Run test to verify it passes, then the full suite**

Run: `cd webapp && npm run build:shell && npm test`
Expected: PASS, 308 (307 + 1).

- [ ] **Step 6: Rebuild the wasm and confirm the export**

Run: `cd webapp && npm run build:wasm`
Then: `node -e "import('./src/wasm/mobile_imu_core.js').then(m => console.log(typeof m.WasmSession.prototype.settle_s))"`
Expected: `function`. `ALGORITHM_VERSION` will bump; record before/after in the commit.

- [ ] **Step 7: Commit**

```bash
git add mobile-imu-core/src/wasm.rs webapp/src/app.js webapp/src/app.css webapp/tests/app.test.js webapp/src/build-id.js
git commit -m "feat: auto-stop a trial when the limb has settled"
```

---

### Task 3: The no-settle alert

**Files:**
- Modify: `webapp/src/app.js`, `webapp/src/app.css`, `webapp/tests/app.test.js`

**Interfaces:**
- Produces: `noSettleAlert({ sinceReleaseS, settleS }) -> string | null`

- [ ] **Step 1: Write the failing test**

```js
// Advisory only. This must never end a trial -- a limb with clonus is a real
// clinical finding, not a fault, and the decision to stop stays with the
// clinician.
test('no alert before the threshold', () => {
  assert.equal(noSettleAlert({ sinceReleaseS: 20, settleS: 0 }), null);
});

test('an alert once a limb has not settled for the threshold', () => {
  assert.match(noSettleAlert({ sinceReleaseS: 31, settleS: 0 }), /stop/i);
});

// A limb that IS settling is on its way to auto-stop; nagging would be wrong.
test('no alert while settling is in progress', () => {
  assert.equal(noSettleAlert({ sinceReleaseS: 31, settleS: 2.5 }), null);
});
```

- [ ] **Step 2: Run to verify it fails, then implement**

```js
/// Advisory prompt for a limb that will not settle. UI ONLY: it has no
/// protocol meaning, changes no stored value, and must never end a trial.
/// A limb with sustained clonus is a clinical finding, not a fault.
export const NO_SETTLE_ALERT_S = 30;

export function noSettleAlert({ sinceReleaseS, settleS }) {
  if (settleS > 0) return null;
  if (sinceReleaseS < NO_SETTLE_ALERT_S) return null;
  return 'The leg is still moving. Tap Stop when you are ready to end the trial.';
}
```

Render it into `#nav-blocked`'s amber style (`.nav-blocked` already exists) or
a sibling element; do not reuse `#guide`, whose text is the state label.

- [ ] **Step 3: Run the suite and commit**

```bash
cd webapp && npm run build:shell && npm test
git add webapp/src/app.js webapp/src/app.css webapp/index.html webapp/tests/app.test.js webapp/src/build-id.js
git commit -m "feat: prompt when a limb will not settle"
```

Expected: 311 (308 + 3).

---

### Task 4: Capture quality classification

**Files:**
- Create: `webapp/src/capture-feedback.js`, `webapp/tests/capture-feedback.test.js`
- Modify: `webapp/src/app.js`

**Interfaces:**
- Produces: `captureQualityOf({ settleS, settleTargetS, endedManually }) -> 'clean' | 'short' | 'unsettled'`

- [ ] **Step 1: Write the failing test**

Create `webapp/tests/capture-feedback.test.js`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { captureQualityOf } from '../src/capture-feedback.js';

const T = 5.0;

test('a self-terminated trial is clean', () => {
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T, endedManually: false }), 'clean');
});

// Stopped after settling began: a partial settled tail, so neutral_deg has
// something to work with but less than the protocol asks for.
test('stopped mid-settle is short', () => {
  assert.equal(captureQualityOf({ settleS: 2.4, settleTargetS: T, endedManually: true }), 'short');
});

// Stopped with no settling at all: no settled tail, so the neutral estimate
// is the weakest of the three. Distinct from short on purpose.
test('stopped with no settling at all is unsettled', () => {
  assert.equal(captureQualityOf({ settleS: 0, settleTargetS: T, endedManually: true }), 'unsettled');
});

// A manual stop that happens to land on or past the target is still clean --
// the data is what the protocol asks for regardless of who ended it.
test('a manual stop at or past the target is still clean', () => {
  assert.equal(captureQualityOf({ settleS: 5.0, settleTargetS: T, endedManually: true }), 'clean');
});

test('defaults are safe rather than optimistic', () => {
  assert.equal(captureQualityOf({}), 'unsettled');
});
```

- [ ] **Step 2: Run to verify it fails, then implement**

```js
// How a completed trial's tail quality is labelled. Flags, never rejects --
// code marks data quality and never drops a capture (2026-08-27 rule).
//
// The three values fail differently and must stay distinct:
//   clean     -- the full settled tail neutral_deg is meant to be taken from
//   short     -- a partial tail; neutral is weaker but present
//   unsettled -- no settled tail at all; neutral is weakest
export function captureQualityOf({ settleS = 0, settleTargetS = 5.0, endedManually = true } = {}) {
  if (settleS >= settleTargetS) return 'clean';
  if (settleS > 0) return 'short';
  return 'unsettled';
}
```

Note `endedManually` is accepted but not branched on: a trial reaching the
target is `clean` however it ended. It stays in the signature because the call
site has it and a future rule may need it — document that, do not silently
drop the parameter.

- [ ] **Step 3: Wire it in `app.js`**

At the point `makeTrialRecord` is called, pass
`captureQuality: captureQualityOf({ settleS, settleTargetS: SETTLE_TARGET_S, endedManually })`.
`settleS` is read from the session before it is nulled — the same ordering
trap the Stop handler already documents.

- [ ] **Step 4: Mutation sweep, suite, commit**

Mutants: collapse `short` into `unsettled`; make `>=` a `>`; default
`settleS` to the target. Each must fail.

```bash
cd webapp && npm run build:shell && npm test
git add webapp/src/capture-feedback.js webapp/tests/capture-feedback.test.js webapp/src/app.js webapp/src/build-id.js
git commit -m "feat: classify capture quality from the settled tail"
```

Expected: 316 (311 + 5).

---

### Task 5: Protocol versioning and manifest v3

**Files:**
- Modify: `webapp/src/session-store.js`, `webapp/src/export.js`, `webapp/src/trend-import.js`
- Test: `webapp/tests/session-store.test.js`, `export.test.js`, `trend-import.test.js`

**Interfaces:**
- Produces: `CAPTURE_PROTOCOL_VERSION = 2` on every trial record and manifest entry; `pendulastic/session-export/v3`

- [ ] **Step 1: Write the failing tests**

In `session-store.test.js`:

```js
// Trials captured under the settle rule are not comparable with the earlier
// operator-terminated recordings: tail length is exactly what neutral_deg is
// computed from. Without this tag a future longitudinal analysis would
// silently mix the two cohorts.
test('a trial records the capture protocol it was captured under', () => {
  const r = makeTrialRecord({ sessionId: 's', side: 'left', params: {}, trajectory: {}, rawJsonl: '', algorithmVersion: 'x' });
  assert.equal(r.capture_protocol_version, 2);
  assert.equal(r.settle_target_s, 5.0);
});
```

In `export.test.js`:

```js
test('the manifest schema is v3', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials });
  const m = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  assert.equal(m.schema, 'pendulastic/session-export/v3');
});

test('each manifest trial carries its capture protocol and quality', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials });
  const m = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  assert.equal(m.trials[0].capture_protocol_version, 2);
  assert.ok('settle_target_s' in m.trials[0]);
});

// A session-level default beside algorithm_version, which already works this
// way: the per-trial value is the one that is true if the app updated
// mid-session.
test('the manifest carries a session-level protocol default', () => {
  const files = buildExportFiles({ session: masSession, patient: masPatient, trials: masTrials });
  const m = JSON.parse(files.find((f) => f.name.endsWith('-manifest.json')).text);
  assert.equal(m.capture_protocol_version, 2);
});
```

In `trend-import.test.js`:

```js
test('a v3 manifest parses', () => {
  const v3 = JSON.stringify({ ...JSON.parse(manifestV2), schema: 'pendulastic/session-export/v3' });
  assert.equal(parseManifest(v3).schema, 'pendulastic/session-export/v3');
});

// Everything captured before this change is protocol 1 BY ABSENCE. A
// consumer must read a missing field as 1, never as an error.
test('a trial with no protocol version reads as version 1', () => {
  const m = parseManifest(manifestV2);
  assert.equal(m.trials[0].capture_protocol_version ?? 1, 1);
});
```

- [ ] **Step 2: Run to verify they fail, then implement**

`session-store.js`:

```js
/// The capture protocol a trial was recorded under. 1 is every recording made
/// before settle-based termination existed, and is signalled BY ABSENCE --
/// those records have no such field. Bump this whenever a change alters what
/// a trial physically is, not merely how it is scored.
export const CAPTURE_PROTOCOL_VERSION = 2;
```

Add both fields to the object `makeTrialRecord` returns, taking
`settleTargetS` as a parameter defaulted to 5.0 rather than importing the Rust
constant into JS.

`export.js`: bump the schema string to `v3`, add
`capture_protocol_version` at manifest top level, and add
`capture_protocol_version` + `settle_target_s` to each trial entry beside
`capture_quality`.

`trend-import.js`: add `'pendulastic/session-export/v3'` to `ACCEPTED`.

- [ ] **Step 3: Suite and commit**

```bash
cd webapp && npm run build:shell && npm test
git add webapp/src/session-store.js webapp/src/export.js webapp/src/trend-import.js webapp/tests webapp/src/build-id.js
git commit -m "feat: tag trials with their capture protocol and bump the manifest to v3"
```

Expected: 322 (316 + 6).

---

### Task 6: Visual progress

**Files:**
- Modify: `webapp/index.html`, `webapp/src/app.css`, `webapp/src/app.js`
- Test: `webapp/tests/capture-feedback.test.js`

**Interfaces:**
- Produces: `progressOf({ stateCode, calmS, settleS }) -> { fraction, label } | null`

- [ ] **Step 1: Write the failing test**

```js
// The bar answers "how much longer", which the state label alone cannot.
test('holding fills toward the hold target', () => {
  const p = progressOf({ stateCode: 1, calmS: 0.475, settleS: 0 });
  assert.ok(Math.abs(p.fraction - 0.5) < 0.05, JSON.stringify(p));
  assert.match(p.label, /hold/i);
});

test('released fills toward the settle target', () => {
  const p = progressOf({ stateCode: 3, calmS: 0, settleS: 2.5 });
  assert.ok(Math.abs(p.fraction - 0.5) < 0.01);
  assert.match(p.label, /settl/i);
});

// A reset must be visible, not merely a paused bar.
test('a reset settle shows an empty bar, not a full one', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: 0 }).fraction, 0);
});

test('states with nothing to count down show no bar', () => {
  assert.equal(progressOf({ stateCode: 0, calmS: 0, settleS: 0 }), null);
  assert.equal(progressOf({ stateCode: 2, calmS: 0, settleS: 0 }), null);
});

test('a settled trial shows a full bar', () => {
  assert.equal(progressOf({ stateCode: 4, calmS: 0, settleS: 5 }).fraction, 1);
});

test('fraction never exceeds one', () => {
  assert.equal(progressOf({ stateCode: 3, calmS: 0, settleS: 99 }).fraction, 1);
});
```

- [ ] **Step 2: Implement**

```js
export const HOLD_TARGET_S = 0.95;   // 0.95 * GYRO_BIAS_WINDOW_S, mirrored from session.rs
export const SETTLE_TARGET_S = 5.0;  // mirrored from session.rs

// What the progress bar should show. Pure, so the arithmetic is tested
// without a DOM. Returns null for states where there is nothing to count
// toward -- an empty bar in MOVING would imply progress that is not happening.
export function progressOf({ stateCode, calmS = 0, settleS = 0 }) {
  const clamp = (x) => Math.max(0, Math.min(1, x));
  if (stateCode === 1) return { fraction: clamp(calmS / HOLD_TARGET_S), label: 'hold steady' };
  if (stateCode === 3) return { fraction: clamp(settleS / SETTLE_TARGET_S), label: 'let it settle' };
  if (stateCode === 4) return { fraction: 1, label: 'trial complete' };
  return null;
}
```

Markup beneath `#guide`:

```html
      <div id="guide-progress" hidden><span id="guide-progress-fill"></span></div>
```

CSS: a 6px track using `--border`, fill using the current state colour so the
bar and the guide agree without a second colour vocabulary.

**The positioning diagram.** Add a collapsible block above `#start`, shown
only before the first trial of a session:

```html
      <details id="hold-guide">
        <summary>How to hold the leg</summary>
        <p>Support the thigh so the knee stays at the edge of the plinth.
           Hold the shank horizontal, wait for READY, then release cleanly and
           let go completely — do not follow the leg down.</p>
      </details>
```

Static instruction, not feedback: the phone is on the shank and cannot sense
where the clinician's hands are. Wording is placeholder-free but should be
confirmed with the clinician before the device test.

- [ ] **Step 3: Suite and commit**

Expected: 328 (322 + 6).

---

### Task 7: Audio cues

**Files:**
- Create: `webapp/src/audio-cues.js`
- Modify: `webapp/src/app.js`, `webapp/tests/capture-feedback.test.js`

**Interfaces:**
- Produces: `beepsDue(prevWholeSeconds, nextValue) -> number`; `createAudioCues({ ctxFactory }) -> { unlock, tick, complete }`

- [ ] **Step 1: Write the failing test**

```js
// One beep per completed second of stability, during hold and settle alike.
test('crossing a second boundary is due one beep', () => {
  assert.equal(beepsDue(0, 1.02), 1);
});

test('no boundary crossed is due none', () => {
  assert.equal(beepsDue(1, 1.9), 0);
});

// Sample batches can jump; the count must not silently swallow seconds.
test('a jump across two boundaries is due two beeps', () => {
  assert.equal(beepsDue(1, 3.05), 2);
});

// A reset must not fire a beep, and must not go negative.
test('a reset to zero is due no beeps', () => {
  assert.equal(beepsDue(3, 0), 0);
});
```

- [ ] **Step 2: Implement**

```js
export function beepsDue(prevWholeSeconds, nextValue) {
  const next = Math.floor(Math.max(0, nextValue));
  return Math.max(0, next - prevWholeSeconds);
}
```

`audio-cues.js` wraps a WebAudio oscillator: a short high blip for `tick()`,
a longer lower tone for `complete()`. `ctxFactory` is injected so the module
imports safely under `node --test` and can be exercised with a fake.

Two constraints from the spec, restated at the call site:

- **Additive only.** Every cue has a visual counterpart. Never gate a state
  change on audio succeeding.
- **iOS needs a user gesture.** `unlock()` is called from the `Start` click
  handler. If it throws, log and continue — capture must not depend on it.

- [ ] **Step 3: Suite and commit**

Expected: 332 (328 + 4).

---

### Task 8: Build, browser walk, README

- [ ] **Step 1:** `cd webapp && npm run build:dist`; confirm `capture-feedback.js` and `audio-cues.js` reach `SHELL`.

- [ ] **Step 2: Browser walk.** Drive from ONE `browse eval` script (the page
drops to `about:blank` between CLI calls and `eval` does not await a returned
promise). Replay a fixture through the worker so the state machine runs
without sensors. Verify: the hold bar fills and resets; the settle bar fills;
a settled trial auto-stops and records `capture_quality: 'clean'`; a manual
stop mid-settle records `'short'`; the alert appears after 30 s and does not
stop the trial.

- [ ] **Step 3: Audio smoke.** Assert `unlock()` is only called from a click
handler, and that the app records normally with the `AudioContext` constructor
stubbed to throw.

- [ ] **Step 4: README.** Document the settle rule, the three quality values,
the protocol version, and that audio is additive.

- [ ] **Step 5: Commit.**

---

## Deployment

Not deployment-ready until the spec's §7 gate passes, including the user's
physical-device smoke test. `pendulastic-app.vercel.app` is not updated by
this plan.

**The 5-second target is a hypothesis until a real leg tries it** (spec §6.1).
A fixture replay proves the state machine; only hardware proves that a limb at
rest, with the phone strapped to the shank, actually clears
`ZERO_CAPTURE_GUARD_RAD_S` for five continuous seconds. Exercise that first.
