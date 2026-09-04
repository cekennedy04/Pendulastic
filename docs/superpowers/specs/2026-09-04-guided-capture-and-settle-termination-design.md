# Guided Capture and Settle-Based Termination — Design

**Date:** 2026-09-04
**Status:** approved in brainstorming; awaiting spec review
**Applies to:** `mobile-imu-core/` and `webapp/`, branch `feat/webapp-workbench-restyle`
**Predecessors:**
- `docs/superpowers/specs/2026-08-31-mobile-webapp-workbench-restyle-design.md`
- `docs/superpowers/specs/2026-09-02-participant-gate-and-longitudinal-trends-design.md`

---

## 1. Context

Five requests came out of using the capture loop:

1. Visual feedback — where to hold the leg, how long to hold, how long to settle.
2. Audio feedback — ready to release, ready to stop, hold steady.
3. The leg must settle for 5 seconds.
4. Trials must not last longer than some amount.
5. Do not allow drift during hold; re-calibrate on movement.

Reading the capture machine changed that list before any design was done.

### 1.1 Item 5 is already implemented

`mobile-imu-core/src/session.rs` already does exactly this:

```rust
if norm3(omega) >= ZERO_CAPTURE_GUARD_RAD_S { self.reset_hold(); return; }
...
if drift_deg > MAX_HOLD_DRIFT_DEG { self.reset_hold(); return; }   // 5.0 deg
```

`reset_hold()` returns the state to `Moving`, clears `calm_since`, and zeroes
the accumulated drift vector. Movement or >5° of net rotation during a hold
already forces the hold to restart from zero.

**No work. Verify on hardware during the device smoke test, then close it.**

### 1.2 Hold duration is already enforced

`Ready` fires at `calm_s >= 0.95 * GYRO_BIAS_WINDOW_S`, and
`GYRO_BIAS_WINDOW_S = 1.0`. The required hold is therefore ~0.95 s and always
has been. What is missing is not enforcement but **visibility** — the operator
sees a state label, never a countdown.

### 1.3 Item 4 was resolved to a soft alert, not a cap

Item 4 as written ("trials must not last longer than x") was withdrawn during
brainstorming. The decision:

> The trial ends **only** once the leg has not moved for 5 seconds. If the leg
> is still moving the trial does not end; the clinician ends it, and the app
> alerts them that they need to.

So there is **no hard time cap**. `NO_SETTLE_ALERT_S = 30.0` produces a visual
prompt and nothing else. This is deliberate and is recorded here because the
original checklist says the opposite.

---

## 2. Why the settle rule improves the measurement

Not merely workflow consistency. `scoring.rs` computes `neutral_deg` as the
settled-tail median, and at `scoring.rs:413`:

```rust
let mut phi: Vec<f64> = ang_r.iter().map(|a| a - neutral).collect();
```

**Every angle in the trial is expressed relative to `neutral`.** A trial cut
short has fewer settled samples for that median, so a poor neutral shifts the
whole waveform — including `a0_deg`, which is the spasticity grouping variable.

The opposite failure is already guarded. The same file records that an
over-long tail once made `N` read *0.5 with a 3 s tail and 28.5 with a 30 s
tail on the same motion*, which is why `ACTIVE_WINDOW_CAP_SEC = 4.0` exists.
So a 5-second settle target sits between two known failure modes: enough
settled tail for a trustworthy neutral, with `N` already protected from the
long-tail artefact.

---

## 3. Unit A — Settle-based termination

### 3.1 Behaviour

While `Released`, the session accumulates `settle_s` whenever the limb is
still, and **resets it to zero on any movement** — deliberately the same shape
as `reset_hold()`, so `TrialSession` has one settling idiom rather than two.

At `settle_s >= SETTLE_TARGET_S` the session enters a terminal `Settled`
state. `app.js` observes it and fires the identical completion path the `Stop`
button fires today. `Stop` remains live and usable at every moment.

### 3.2 Stillness test

**Amended 2026-09-04, before implementation.** The original text specified
`is_stationary_window(gyro_buf, accel_buf, now)`. That would have been a
serious defect, for two independent reasons found while planning.

**It would not fire.** `ZERO_CAPTURE_GUARD_RAD_S`'s own doc comment records
that `is_stationary_window` is

> tuned for bias-grade stillness and, verified empirically across the full
> real trial corpus, never fires at all for a meaningful fraction of genuinely
> fine trials (real accel noise from a handheld/strapped sensor commonly
> exceeds its 0.18 m/s² bound even at rest)

Using it as the settle test would mean a large fraction of trials never reach
`Settled`. Combined with the no-cap decision in §1.3, those trials would
record **indefinitely** — the worst available outcome, and one that only shows
up on real hardware.

**The session has no accel buffer.** `TrialSession` keeps `gyro_hold:
SampleBuf` and nothing else; accel samples are appended to `samples` but never
buffered for analysis. `is_stationary_window` needs both.

**Resolution:** use the gyro-magnitude-only test the codebase already uses for
exactly this class of decision —

```rust
recently_calm(&self.gyro_settle, t)   // all samples < ZERO_CAPTURE_GUARD_RAD_S
```

`ZERO_CAPTURE_GUARD_RAD_S` (0.3 rad/s) is documented as "empirically derived
from the reference corpus, not guessed". No new threshold is introduced and no
new sensor buffer is needed.

**One new buffer is required regardless.** `push()` only calls `advance_hold`
while `state != Released`, and `gyro_hold` is maintained inside it — so after
release the existing buffer stops being updated. Settling therefore needs its
own trailing buffer, `gyro_settle`, maintained by a new `advance_settle`.
Keeping it separate from `gyro_hold` also avoids entangling settle detection
with the release detector's ordering contract, which reads that buffer as of
just *before* the current sample.

### 3.3 State machine

`HoldState` gains one variant:

```rust
pub enum HoldState {
    Moving,
    Holding { calm_s: f64, drift_deg: f64 },
    Ready,
    Released,
    /// Post-release stillness held for SETTLE_TARGET_S. Terminal.
    Settled,
}
```

`state_code()` therefore gains code **4**. `webapp/src/app.js`'s `STATES` and
`CLASSES` arrays are both indexed by that code and must both grow — they are
documented as matching `HoldState` exactly, and a length mismatch would render
`undefined` rather than fail.

New constants, both in `session.rs` beside `MAX_HOLD_DRIFT_DEG`:

```rust
/// Continuous post-release stillness required before a trial self-terminates.
pub const SETTLE_TARGET_S: f64 = 5.0;
```

New wasm accessor, following the existing `calm_s()` / `drift_deg()` pattern:

```rust
pub fn settle_s(&self) -> f64
```

`wasm.rs` stays logic-free per its own contract; the accumulator and the
threshold live in `session.rs`, where `cargo test` sees them.

### 3.4 What stays in JavaScript

`NO_SETTLE_ALERT_S = 30.0` is **UI only** and lives in `app.js`. It has no
protocol meaning, changes no stored value, and must not be able to end a
trial. Putting an advisory timer in the Rust core would imply it is part of
the measurement, which it is not.

### 3.5 Acceptance criteria

1. A trial self-terminates after 5 s of continuous post-release stillness.
2. Movement during settling resets `settle_s` to zero, visibly.
3. A leg that never settles never self-terminates.
4. After 30 s without reaching the target, the guide turns amber and prompts;
   the trial keeps recording.
5. `Stop` ends a trial at any moment, including during settling.
6. The existing hold behaviour (§1.1, §1.2) is unchanged and still tested.

---

## 4. Unit B — Guidance: visual and audio

### 4.1 Visual

`#guide` gains a progress bar beneath the state label:

| Phase | Fills |
| --- | --- |
| `Holding` | `calm_s` → 0.95 s |
| `Released` | `settle_s` → 5.0 s |

Both **reset visibly** when the limb moves, so the operator can see *why* they
are still waiting rather than only that they are.

**"Where to hold the leg" is static instructional content**, not feedback. The
phone is strapped to the shank and has no way to sense where the clinician's
hands are; rendering a "correct" hand position from shank IMU data would be
fabrication. A labelled diagram on the capture view, shown before Start and
collapsible, is the honest form of this request.

### 4.2 Audio

WebAudio oscillator. No asset files, no dependency, consistent with the
project-wide no-dependency constraint.

| Cue | Sound |
| --- | --- |
| Each completed second of stability, during hold **and** settle | one short beep |
| Trial self-terminates on settle | one longer, lower sustained tone |

The per-second beep restarts its count when stillness breaks, so the audio
carries the same reset the progress bar shows.

Two constraints, both non-negotiable:

- **Audio is additive only.** Every cue has an existing visual counterpart. A
  muted phone, a phone in a pocket, or a clinician who is deaf must lose no
  information. Audio may never be the sole carrier of a state change.
- **iOS requires a user gesture** before an `AudioContext` may start. The
  `Start` tap unlocks it. If unlocking fails the app continues silently rather
  than blocking capture.

### 4.3 Acceptance criteria

1. The hold bar fills over ~0.95 s and resets on movement.
2. The settle bar fills over 5 s and resets on movement.
3. One beep per completed second of stability; the count restarts on movement.
4. A distinct sustained tone on self-termination.
5. Muting the device changes nothing visible or recorded.
6. Capture works normally if the `AudioContext` cannot start.

---

## 5. Unit C — Quality flags and protocol versioning

### 5.1 `capture_quality`

The field already exists on every trial record (`makeTrialRecord`, always
`'clean'` today) and already exports (`export.js:73`). It becomes the carrier:

| Value | Meaning |
| --- | --- |
| `clean` | self-terminated after the full 5 s of stillness |
| `short` | operator stopped after settling began but before the target |
| `unsettled` | operator stopped while the limb was still moving (`settle_s == 0`) |

`short` and `unsettled` are distinct because their neutral estimates fail
differently: `short` has a partial settled tail, `unsettled` has none.

**Trials are flagged, never rejected and never dropped** — the 2026-08-27
rule. The flag rides into the manifest through a field that already exports.

The classification is a pure function of `(settleS, settleTargetS,
endedManually)`, so it is testable without a DOM or a device.

### 5.2 Protocol versioning

Trials captured under this rule are not directly comparable with the existing
recordings, which were operator-terminated at arbitrary lengths. Tail length
is exactly what `neutral_deg` is computed from, so a silent change would
confound any longitudinal comparison across the boundary.

Each trial record and each manifest trial entry gains:

```json
{
  "capture_protocol_version": 2,
  "settle_target_s": 5.0,
  "capture_quality": "clean"
}
```

`capture_protocol_version` also appears at manifest top level as the session
default, mirroring how `algorithm_version` is already handled there: a
session-level default, with the per-trial value as the one that is actually
true if the app updated mid-session.

Everything captured before this change is **protocol version 1** by absence.
Consumers must read a missing field as 1, not as an error.

### 5.3 The manifest bumps to v3

Adding fields to the trial entries changes the manifest shape. The v2 spec's
own rule was that a consumer "must not be handed a different shape under an
unchanged version string", so:

- `buildExportFiles` emits `pendulastic/session-export/v3`.
- `trend-import.js`'s `ACCEPTED` set widens to `{v1, v2, v3}`. v1 and v2
  bundles remain importable; their trials are protocol version 1.
- `tests/export.test.js` and `tests/trend-import.test.js` both move with it.

### 5.4 Acceptance criteria

1. A self-terminated trial records `capture_quality: 'clean'`.
2. A manually stopped trial mid-settle records `'short'`.
3. A manually stopped trial with no settling records `'unsettled'`.
4. Every new trial records `capture_protocol_version: 2` and
   `settle_target_s: 5.0`.
5. A manifest missing `capture_protocol_version` is read as version 1.
6. v1, v2 and v3 bundles all import.

---

## 6. Quality validation

The bar already set on this branch, unchanged.

1. **Rust unit tests** for the settle accumulator: it accumulates while still,
   resets on movement, reaches `Settled` at exactly the target and not before,
   and never self-terminates on a limb that keeps moving.
2. **JS pure-function tests** for the quality classifier and the beep
   scheduler (which second boundaries a given `settle_s` sequence crosses).
3. **Mutation sweep on every new pure function**, and each mutant confirmed to
   have actually applied before its result is believed. Three times this
   session an edit that silently did not apply looked exactly like a passing
   test.
4. **Test-count check before and after every append.**
5. **Headless browser verification** driven from a single `browse eval`
   script, with a replayed fixture standing in for the sensor stream.
6. **`npm run build:shell` before every commit** that touches `webapp/src/`.

### 6.1 What cannot be verified off-device

The settle path depends on real `devicemotion` data. A fixture replay proves
the state machine; it cannot prove that a real limb at rest passes
`is_stationary_window` with the phone strapped to a shank. **The 5-second
target is a hypothesis until it is tried on a real leg**, and it is the first
thing the device test should exercise.

---

## 7. Deployment readiness

Unchanged from the previous spec: full suite green, `build:dist` succeeds,
every new module in `SHELL`, headless walk clean against `dist/`, and a
**physical-device smoke test performed by the user** before production is
touched. `pendulastic-app.vercel.app` is not updated by this work.

The device test for this change specifically must confirm:
- the hold bar and beeps behave with a real limb,
- 5 s of stillness genuinely terminates a trial,
- a spastic limb that does not settle keeps recording and raises the alert,
- audio does not fire before the Start gesture.

### 7.1 Carried-forward gaps

- **No icons** (`manifest.json` has no `icons`, no `apple-touch-icon`). With
  multiple builds installed side by side and all named "Pendulastic", this is
  now a hazard rather than a cosmetic gap.
- **`ALGORITHM_VERSION` names the commit before the source it describes** —
  `build-wasm.mjs` stamps HEAD at build time. Unit A rebuilds the wasm, so it
  will bump again.

---

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| 5 s of stillness is unreachable with a real strapped phone | Uses the gyro-only ZERO_CAPTURE_GUARD_RAD_S bound, which is empirically derived from the real corpus, precisely because the stricter gyro+accel test is documented as not firing for many genuinely fine trials (§3.2). Verify first on device; the constant is one line and named |
| A spastic limb records indefinitely | Accepted by decision; the 30 s alert prompts, and `Stop` is always live |
| Audio becomes the sole carrier of a cue | Every cue has a visual counterpart; muted must lose nothing |
| Protocol change silently confounds longitudinal analysis | `capture_protocol_version` on every trial and in the manifest |
| A fifth `HoldState` desynchronises the JS arrays | Two tests, because neither language can see the other: a Rust test asserts `state_code()` reaches 4 and no further, and a JS test asserts `STATES.length === CLASSES.length === 5`. A short array renders `undefined` rather than throwing, so nothing else would catch it |

---

## 9. Out of scope

- Any change to how PT7, `neutral_deg` or any parameter is computed. This
  spec changes when recording **stops**, not how the recording is scored.
- A hard maximum trial length. Withdrawn by decision (§1.3).
- Inferring hand or limb position from IMU data. The sensor cannot support it.
- Re-processing the existing recordings under the new protocol.
- Speech synthesis. Tones only.

---

## 10. Implementation order

1. **Unit A** — settle accumulator, `Settled` state, wasm accessor, auto-stop.
   Everything else depends on `settle_s` existing.
2. **Unit C** — quality flags and protocol versioning. Small, and it makes A's
   effect visible in the exported record.
3. **Unit B** — visual progress and audio. Pure presentation over A's data,
   and the only unit whose acceptance is genuinely subjective.
