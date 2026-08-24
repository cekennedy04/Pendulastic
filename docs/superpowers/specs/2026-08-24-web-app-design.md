# Pendulastic IMU App — Web Platform Design Spec

**Date:** 2026-08-24
**Status:** Sections 1–4 settled; Sections 5–8 drafted, not yet reviewed
**Extends:** `docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md` (what the app is)
**Extends:** `docs/plans/2026-08-24-001-plan-mobile-app-validation-build-test.md` (what must be true before building it)
**Supersedes:** KTD2, KTD5, KTD8 of the 2026-08-21 plan (see §1)

---

## 0. What this changes, in one paragraph

The IMU pendulum app ships as a **self-contained iOS Safari web app** rather than
native iOS and Android shells. The phone captures, fuses, scores, and stores a
trial on its own; no laptop is involved. `mobile-imu-core` — already ported,
already tested, zero dependencies — compiles to WebAssembly instead of being
wrapped in UniFFI. This deletes two app shells, the UniFFI binding layer, the
cross-compile pipeline, the cross-platform parity unit, and store distribution.
It does not change the algorithm, the reference it is validated against, or any
of the accuracy questions Gate G0 exists to answer.

---

## 1. How the web pivot reshapes the existing plans *(settled)*

### 1.1 A correction to the validation plan, verified

`2026-08-24-001`'s §1.1 states: *"Throwaway iOS/Android capture harnesses exist
(`harness-ios/`, `harness-android/`) — that is plan unit U0, already done."*

**Neither directory exists in any branch, remote, or commit in this repository.**
Verified by enumerating every ref and by `git log --all --diff-filter=A` over
both paths. U0 is not done; it was never started.

This matters structurally: Phase 1's shadow study depends on U0, and Phase 1
gates Phase 2. The only working phone-capture path that exists today is the
browser page on `main` (`pendulastic_phone_server._IMU_PAGE`) — which is exactly
what U0 was invented to supply. **The web pivot does not need to build U0; it
absorbs it.**

A second staleness note: the same section records "Scoring is not ported yet
(plan unit U2)" and "`tests/ahrs_test.rs` only (196 lines)". As of 2026-08-24,
U2 is ported and the crate carries 45 passing tests across 6 binaries.

### 1.2 Superseded decisions

| Decision | Was | Becomes | Why |
|---|---|---|---|
| **KTD2** | Native shells + UniFFI; "WASM rejected — no viable iOS runtime" | WASM in Safari | The rejection was about embedding a WASM runtime *inside a native iOS app*, where third-party code cannot JIT. Safari **is** the runtime, and it is the one JIT-capable WASM engine on iOS. The stated reason does not apply. KTD2's *other* reason — sensor-timing fidelity — was tested directly and passed (§A). |
| **KTD5** | `CMMotionManager` / `SensorManager` raw listeners | `DeviceMotionEvent` at 60 Hz | Measured, not assumed (§A). |
| **KTD8** | TestFlight + Firebase App Distribution | A URL and Add to Home Screen | No store review was ever wanted; the web removes the question. |

### 1.3 Unit disposition

| Unit | Disposition |
|---|---|
| U0 capture harness | **Absorbed** — the browser capture page already exists |
| U1 fusion + calibration | Unchanged, done |
| U2 angle math + scoring | Unchanged, done |
| U3 UniFFI + cross-compile | **Replaced** by `wasm-bindgen` on a zero-dependency crate |
| U4 iOS app | **Becomes** the web app |
| U5 Android app | **Deleted** — same artifact serves Android Chrome |
| U7 cross-platform parity | **Collapses** — one binary. Replaced by a browser-matrix check (§6) |
| U6, U8–U13 | Retained, re-homed onto the web (§5–§8) |

### 1.4 One conflict to resolve: the capability floor

The validation plan's §6 asks of a budget device: *"does it even sustain 90 Hz?"*
**iOS Safari is hard-capped at 60 Hz.** Under a 90 Hz floor, the chosen platform
is disqualified on the target device by definition.

The floor is the thing that is wrong, and it was never derived from the
algorithm:

- KTD5 itself says the ~100 Hz native range "is already more than the desktop
  pipeline works with."
- U2 resamples to fixed 50 ms ticks (20 Hz) *before* Savitzky-Golay, peak
  detection, or any parameter is computed. Nothing numerical sees the raw rate.
- Measured iOS Safari delivery is 60.00 Hz with zero pathological `dt` (§A).

**Proposed floor, stated in terms of what scoring demands:** ≥ 50 Hz sustained
delivery, with zero inter-sample `dt` outside `(0, 500) ms` and zero `dt == 0`,
over a full trial. That is a number with a reason attached, and iOS Safari meets
it with margin.

### 1.5 Sequencing: a proposed amendment to D2

D2 states that validation gates block build work. That rule was priced for
*native*: two app shells, a UniFFI layer, a five-device fleet, and store
distribution — an expensive bet on an instrument that has not passed G0.

A web app is one artifact built on a crate that already exists and is tested.
**Proposal:** the web build proceeds **in parallel with Phase 0**, under a hard
rule that no clinical claim is made and no participant-facing use occurs until
G0 passes. G0 keeps its authority over conclusions; it stops charging native
prices for native caution.

This does not soften G0. Its current standing is unchanged and unflattering:

| Criterion | Target | Today |
|---|---|---|
| Trajectory RMSE vs OptiTrack | ≤ 10.0° mean / 8.0° median | 14.84 / 10.98 |
| Bias-removed residual scatter | ≤ 8.0° mean | 9.71 |
| Trials under the 5.0° clinical goal | ≥ 50% | 6 of 53 |
| ICC(2,1) ≥ 0.75 | on ≥ 4 of 7 parameters | max 0.458 |
| Within-session repeatability | CV ≤ 15% | not measured |
| Leave-one-participant-out AUC | — | 0.21 (below chance) |

Building the app faster does not make the instrument work. It stops the
platform from being the reason you cannot find out.

---

## 2. Execution architecture: the WASM boundary *(settled)*

### 2.1 Three constraints that shape everything

**`DeviceMotionEvent` is not available in a Web Worker.** The API is exposed
only on `window`; workers have no `window`, and there is no worker-side
equivalent. `DeviceMotionEvent.requestPermission()` must additionally be called
from a user-gesture handler on the main thread, on iOS. Sensor capture
therefore *must* live on the main thread. Any design that captures in a worker
cannot be built.

**`dt` fidelity does not come from the thread choice.** `event.timeStamp` is
assigned when the browser creates the event, not when the handler runs — so main
-thread contention delays *delivery* without corrupting the timestamp. Measured:
16.667 ± 0.813 ms while a stats table re-rendered on the same thread (§A). The
reasons to use a worker are different and still sound: the end-of-trial scoring
burst must not block the UI, and a minimal handler drops fewer events.

**The transfer is small.** Six floats plus a timestamp at 60 Hz is 56 bytes per
sample = **3.4 KB/s**; a full 15-second trial is **50 KB**. `SharedArrayBuffer`
would require COOP/COEP cross-origin isolation permanently, to optimise
something below the noise floor. The main thread instead writes into a
preallocated `Float64Array` and hands it over with `postMessage(buf, [buf])` —
an ownership **transfer**, not a copy, requiring no isolation headers. The
existing production `_IMU_PAGE` already batches at exactly 50 ms, so the cadence
is proven.

### 2.2 What runs where

| Component | Runs on | Why |
|---|---|---|
| UI, canvas, live waveform | JS main thread | GPU-accelerated compositing |
| `devicemotion` capture + permission | **JS main thread** | *Forced* — window-only API; iOS gesture requirement |
| Batch + transfer at 50 ms | main → worker | `postMessage(buf, [buf])`, zero-copy, no COOP/COEP |
| Madgwick fusion, bias calibration, stillness | WASM in worker | `ahrs.rs`, `calibration.rs`, `stillness.rs` — ported |
| Release detection (calm/departure) | WASM in worker | `ReleaseDetector` — ported |
| Tick resample + EMA | WASM in worker | `resample.rs` — ported |
| Savitzky-Golay, peaks, 7-param score | WASM in worker | `signal.rs`, `scoring.rs` — end-of-trial burst |
| 20 Hz trajectory push to UI | worker → main | Matches the 50 ms tick the algorithm already uses |
| HPE keypoints | **out of scope** | RGB path — Phase 4, gated by G0-RGB |

### 2.3 The filter chain is fixed

The core uses **Madgwick** fusion and **Savitzky-Golay** smoothing. There is no
Mahony, Kalman, or Butterworth anywhere in the crate, and none may be
substituted. The port's entire value is byte-faithfulness to the desktop
reference: Rust reproduces Python to **below 1e-13 degrees** across a full
pipeline run, pinned by `mobile-imu-core/tests/pipeline_test.rs`. Swapping a
filter voids that equivalence, voids the golden fixtures, and voids every
accuracy claim traceable to the desktop corpus.

If a filter change is ever wanted: change the **reference** first, validate it
there, then re-port.

### 2.4 On-device computation limits

The WASM core performs **feedforward, low-dimensional kinematics and scoring
only**. No trajectory optimisation, no model scaling, no muscle-torque solving,
no differentiable simulation. This is already true — `mobile-imu-core` is a port
of a fixed algorithm with an empty `[dependencies]` table — and is stated here so
it stays true.

---

## 3. Data model and durability *(settled)*

### 3.1 The eviction problem

WebKit caps **all script-writable storage** — IndexedDB, localStorage, Cache API,
service worker registrations — at **7 days without user interaction with the
site**. Participant records, raw logs, and trial history would silently
disappear. The documented exemption is sites added to the **Home Screen**.

This makes installation load-bearing, not cosmetic:

- Onboarding presents a **blocking gate** until the app runs standalone
  (`window.navigator.standalone === true` on iOS; `display-mode: standalone`
  elsewhere), with visual install instructions.
- `navigator.storage.persist()` is called as a free secondary helper for
  Chrome/Android. **It is close to a no-op on iOS and must not be relied on.**
- Export is the physical fail-safe (§3.4).

**This is the single assumption in this spec that has not been verified.** It
requires an installed PWA left untouched on a real iPhone for 8+ days, with data
confirmed intact afterwards. Until that runs, treat durability as unproven.

### 3.2 Stores

```
patients ──1:*──> sessions ──1:*──> trials
```

```typescript
// Store A — patients
{ id: string,                 // UUIDv4
  clinic_patient_id: string,  // pseudonymous site identifier
  created_at: number }        // Unix ms

// Store B — sessions   (no calibration data: zeroing is per-trial, see §4)
{ id: string, patient_id: string, timestamp: number }
```

```typescript
// Store C — trials
{
  id: string,
  session_id: string,
  side: 'left' | 'right',
  timestamp: number,
  algorithm_version: string,      // crate version that produced `params`

  release_quat: [number, number, number, number],   // see §4.3
  release_idx: number,
  release_override_idx: number | null,              // KTD9 set_release_override

  capture_quality: 'clean' | { low_confidence:
      'stream_gap' | 'attachment' | 'swing_range' | 'hold_drift' },

  raw_imu_data: ArrayBuffer,        // [ts, ax, ay, az, gx, gy, gz] f64, 60 Hz
  resampled_trajectory: ArrayBuffer,// [t, angle_deg] on 50 ms ticks

  params: { /* §3.3 */ },
  // composite PT score and severity zone are DERIVED on read, never stored
}
```

### 3.3 `params` — generated from `scoring.rs`, not hand-written

The field names below are mechanically derived from the `PtParams` struct.
Renaming any of them breaks traceability with the desktop corpus and the golden
fixtures. The `Vec<f64>` members (`phi`, `ang_r`, `t_r`, `omega_s`, `pk_i`,
`tr_i`) are waveform arrays and live in the binary blob, not the record.

```typescript
params: {
  r2n: number,               // A1 / (1.6 * A0) — first-swing peak-to-peak, normalised
  n: number,                 // significant full oscillation cycles
  phi_max_ratio: number,     // A2_max / A0 — a RATIO, not degrees
  omega_max_n: number,       // peak angular velocity, NORMALISED by A0
  omega_min_n: number,       // minimum in-swing angular velocity, normalised by A0
  f: number,                 // Hz; 0.0 means "not enough cycles", a value not an error
  area_ratio: number,        // |P+ - P-| / P_total — symmetry index
  omega_peak_deg_s: number,  // peak angular velocity in deg/s, UN-normalised
  a0_deg: number,            // first-flexion amplitude (deg)
  a1_deg: number,            // first oscillation peak-to-peak (deg), Bajd & Bowman
  first_trough_depth: number,// depth of first trough below neutral (deg)
  neutral_deg: number,       // settled resting angle (deg), tail median
  neutral_deg_raw: number,   // same tail median in raw (undetrended) space
  pre_release_deg: number,   // held leg position just before release (deg)
  quality_warn: boolean,     // STRICTLY area_ratio > AREA_RATIO_WARN (0.55)
  phi_negated: boolean,      // angle convention flipped so extension reads positive
  spasticity_type: 'Flexion' | 'Extension' | 'Balanced',
  p_plus: number,            // positive (extension) area
  p_minus: number,           // negative (flexion) area
  p_total: number,
}
```

Two fields are routinely confused and must not be merged:

- **`quality_warn`** is a scoring-symmetry flag: `area_ratio > 0.55`. It has
  nothing to do with jitter, gaps, or dropped frames.
- **`capture_quality`** (§3.2) is KTD11's capture-acceptance gate, and carries a
  *reason*. A boolean would discard the only part that is actionable.

There is no `neutral_depeak_deg_s`, and `spasticity_type` has no `'none'`
variant — the third value is `Balanced` ("neither side dominates: healthy or
mild").

### 3.4 Export — the executable contract

KTD4 requires the raw stream be consumable by `imu_calibration_tuner.replay_trial()`
directly. `replay_trial` dispatches on `sensor == "accel" | "mag" | "gyro"`; an
unrecognised sensor name matches **no branch and is skipped without raising**, so
a malformed export is indistinguishable from an unscorable trial.

```jsonl
{"t":12.345,"role":"distal","sensor":"accel","v":[0.142,0.015,9.812],"phone_ts_ms":12345}
{"t":12.345,"role":"distal","sensor":"gyro","v":[0.052,-0.104,0.012],"phone_ts_ms":12345}
```

Required, all of them:

| Rule | Reason |
|---|---|
| `t` in **seconds** | `replay_trial` computes `n_ticks = (t_end - t0)/TICK_S`. Milliseconds inflate tick count 1000× |
| `v` exactly **3 elements** | one vector per sensor; there is no combined 6-axis record |
| `sensor` ∈ `accel` \| `gyro` \| `mag` | any other value is silently dropped |
| **accel before gyro** at the same `t` | the gyro branch guards `if st.accel is not None`; a leading gyro sample is dropped from fusion |
| `phone_ts_ms` **present** | `dt` derives from it; absent, `replay_trial` fabricates `dt = 0.01` |
| gyro in **rad/s** | browsers deliver deg/s |
| accel in **m/s²** | measured median 9.878 (§A) |
| `role` = `"distal"` | single-segment solo path |

**Verified by execution**, not assertion. `tests/test_web_export_contract.py`
pins every rule above against the real `replay_trial`, five tests, and was first
confirmed against a live Safari capture (§A):

| Variant | Result on the live capture |
|---|---|
| `sensor:"phone"`, 6-element `v`, `t` in ms | **EMPTY** — silent failure |
| This contract | round-trips: penalty 6.093, A0 69.51, N 3.5, f 0.937 |
| This contract minus `phone_ts_ms` | round-trips, **wrong trial**: A0 collapses 69.51 → 42.33, penalty "improves" to 0.401 |

The third row is the one that matters. Dropping one field does not error and
does not return empty — it returns a plausible, clean-looking, **wrong** answer
that makes a spastic limb look healthier than it is. That is the 2026-08-17
`dt`-collapse defect, reachable again through schema alone.

The committed tests use a forward-simulated trial rather than the capture, so
they are deterministic, self-contained, and carry no participant data.

On iOS the export is delivered by **`navigator.share({ files: [...] })`** (Web
Share API Level 2). `showSaveFilePicker` is Chromium-only and does not exist in
Safari. Fallback is a classic `<a download>` anchor.

### 3.5 Version lifecycle

Stored `params` are only valid while the scorer that produced them is the
running scorer — `a1ca2b5` changed oscillation counting, which changes `n`
itself.

- **At save:** store `raw_imu_data` **and** derived `params`, tagged with
  `algorithm_version`.
- **At read:** if `algorithm_version` ≠ the running crate version, the worker
  re-runs `raw_imu_data` through the current pipeline, rewrites `params` and the
  version, and renders the corrected result. Never re-score from stale `params`.
- The **composite score and severity zone are always computed at read time**
  against the running build's `HEALTHY_REF`. They are never persisted:
  `HEALTHY_REF`, `PT_HEALTHY_MAX`, and `PT_BORDERLINE_MAX` were recalibrated
  three times in the week of 2026-08-24 (`278b6f1`, `dc9b36c`, `ea24843`), and
  validation task V0.4 will move `HEALTHY_REF` again. Persisting a composite
  would let U11's trend line silently plot scores from different scorers against
  each other.

---

## 4. Capture: stillness, release, and zeroing *(settled)*

### 4.1 Sequence

```
Hold prompt (2.0 s) ─► peak-to-peak gate ─► hold-drift gate (≤5°)
                                                   │
   release_quat locked ◄── t0 departure ◄── release trigger (≥1.0 rad/s)
```

### 4.2 The two stillness gates

**Peak-to-peak, not variance.** `is_stationary_window` checks **per-axis
peak-to-peak** amplitude over the trailing window. Its own documentation
explains why: a signal that oscillates in *direction* at roughly constant
magnitude — precisely what examiner handling looks like — has near-zero variance
in magnitude and would pass a variance check.

Thresholds are the reference's: `GYRO_STATIONARY_MAX_RAD_S` 0.9,
`ACCEL_STATIONARY_MAX_MPS2` 0.18, over `GYRO_BIAS_WINDOW_S` 1.0 s (× 0.95 span
requirement). The UI prompts for **2.0 s** as deliberate margin over the 1.0 s
algorithmic minimum; the margin is not to be optimised away.

**Hold-drift gate — new, from measured data.** The calm gate bounds angular
*rate*, not accumulated displacement. `ZERO_CAPTURE_GUARD_RAD_S` = 0.3 rad/s
over a 2.6 s hold permits **45° of pose drift** while continuously reporting
"calm". A slow steady creep sails through. In the reference capture (§A) the
phone's net pose rotated **8.7°** during a hold that passed the calm gate
throughout — enough to push the trial's start angle outside `score_waveform`'s
horizontal-start tolerance and fail it.

The gate therefore also tracks **cumulative angular displacement** across the
hold window and fails if it exceeds threshold, forcing a re-hold.

> **Threshold status: not calibrated.** 5° is a reasonable starting value, and
> would have caught the 8.7° reference trial. One trial is not a calibration.
> This carries the same status KTD11 already assigns its attachment-stability
> and swing-range thresholds: derived from shadow-study data, not chosen here.

### 4.3 `release_quat` — the instant matters

The zero pose is captured **at the moment release fires**, inside the gyro
branch, *before that sample is integrated* — not at the end of the stillness
hold. The two differ by exactly the hold drift measured in §4.2 (8.7° in the
reference capture). Locking at hold-end would reintroduce the entire error that
release-anchored zeroing exists to remove.

It is per-trial. There is no session-level calibration, and no second
(thigh/proximal) sensor: this is a single-phone, single-segment design, and
`replay_trial` has a solo-role path for it.

### 4.4 Release detection and override

Auto-detection is the ported `ReleaseDetector` calm/departure machine (KTD9): a
`FLEX_CAPTURE_THRESHOLD` (1.0 rad/s) crossing is trusted as release **only**
after a full calm window has been earned, and an above-guard excursion that
settles back without reaching threshold revokes eligibility as handling.

`ReleaseNeverDetected` is recovered **retroactively**, never by re-recording: the
buffer already exists, so the clinician scrubs the recorded waveform, taps the
release point, and that resolves to a `sample_id` passed to
`set_release_override()`, which deterministically recomputes the score against
the same buffer.

### 4.5 Trajectory storage

`resampled_trajectory` stores **`[t, angle_deg]` only**. Angular velocity is
never stored: `tick_resample` runs across the whole trial while `omega_s` is
post-release only on the `t_r` base, so a combined buffer would mix two time
bases. Velocity is derived at read time.

---

## 5. UI state machine *(draft)*

```
Idle ─► Positioning ─► Holding ─► Ready ─► Released ─► Settling
                          ▲          │                     │
                          └──fail────┘                     ▼
                       (drift / motion)                  Scoring
                                                            │
                          Export ◄── Result ◄───────────────┘
                                       │  ▲
                                       ▼  │
                                     Scrub (set_release_override)
```

| State | Shows | Exits when |
|---|---|---|
| Idle | participant, side, trial number | clinician taps Start |
| Positioning | "hold the limb horizontal" | any calm sample arrives |
| Holding | **live** calm-window progress *and* accumulated drift | both gates satisfied → Ready; either fails → Positioning |
| Ready | unambiguous green "release now" | `ReleaseDetector` fires |
| Released / Settling | live 20 Hz waveform | motion settles or max duration |
| Scoring | brief; worker burst | score returns |
| Result | 7 parameters, derived composite + zone, `capture_quality` banner | — |
| Scrub | waveform with draggable t₀; **shows before/after parameters as t₀ moves** | override applied or cancelled |
| Export | share sheet | shared or dismissed |

Two requirements carried from §4: Holding must surface **both** gates
separately — a clinician needs to know whether they failed for motion or for
drift, because the corrective action differs. And Scrub must show what changed
when t₀ moves, or the clinician is adjusting a number they cannot evaluate.

The in-page banner prototype from the spike (`MOVING` / `HOLDING 0.6s` /
`READY` / `RELEASE DETECTED`) validated that this state machine runs in real
time at 60 Hz in Safari.

*Open:* U10's three-trial self-auditing protocol wraps this loop; how it gates
session completion on export (Approach B) is not yet specified.

---

## 6. Test strategy *(draft)*

Remapping the validation plan's L1–L8 off native onto web:

| Layer | Was | Becomes | Status |
|---|---|---|---|
| **L1** Rust core unit + golden vectors | `cargo test` | unchanged | **exists** — 45 tests, Rust ≡ Python < 1e-13 |
| **L2** Python reference + interop | `pytest` | + **the JSONL export round-trip test** (§3.4) | partly exists |
| **L3** Cross-binding parity (U7) | Swift vs Kotlin vs Rust | **collapses** — one binary. Replaced by a **browser matrix**: iOS Safari, Android Chrome, macOS Safari, all running the same golden vectors through WASM | new |
| **L4** App-level unit/component | XCTest / JUnit | worker protocol, IndexedDB layer, state machine — plain JS test runner | new |
| **L5** Instrumented device tests | XCUITest / Espresso | Playwright/WebDriver. **`devicemotion` cannot be synthesised in real Safari**, so device tests drive a **replay harness** that injects recorded sample streams through the same worker entry point | new |
| **L6** E2E clinical protocol | manual, scripted | unchanged | — |
| **L7** Non-functional | perf, battery | + **the 8-day eviction soak test** (§3.1) — the one unverified assumption | new, blocking |
| **L8** Statistical validation | per data wave | unchanged, platform-independent | — |

L5's note is the substantive one: no browser automation can fabricate real
`DeviceMotionEvent` streams in Safari, so the honest design is a seam — the
worker accepts sample batches from either the live listener or a replay source,
and tests drive the replay source. That seam is also what makes the spike's
captures reusable as regression fixtures.

---

## 7. Build, CI, and distribution *(draft)*

**Build.** `crate-type = ["cdylib", "rlib"]`, `wasm-bindgen`, target
`wasm32-unknown-unknown`. The crate has an **empty `[dependencies]` table**, so
nothing can fail to compile for WASM — this is the cheapest part of the whole
plan. Neither the wasm32 target nor `wasm-pack` is installed on the current dev
machine yet.

**CI.** The repo has no `.github/` directory at all. Minimum viable pipeline:
`cargo test`, `cargo clippy`, the WASM build, and the L2 export-contract test.
That last one is the highest-value CI check in this spec, because the failure it
guards is silent.

**Distribution.** An HTTPS static host plus Add to Home Screen. No store review,
no TestFlight, no Firebase — KTD8 dissolves. A service worker caches the app
shell and WASM for offline use, which is also what makes the install exemption
(§3.1) meaningful.

*Open:* hosting location, and whether the app is served from a domain the site
controls. This intersects the data-governance gap the 2026-08-21 plan already
tracks in TODOS.md and does not resolve.

---

## 8. What stays gated behind G0 *(draft)*

Per §1.5, the build proceeds in parallel with Phase 0 validation. What does
**not** proceed:

1. No clinical claim, internal or external, about what a score means.
2. No participant-facing use.
3. The app carries a visible **"research capture only — not validated"** banner
   until G0 passes, mirroring the treatment the RN app already gets under
   G0-RGB.
4. U11's longitudinal trend view renders trajectories but **no severity
   classification** until `HEALTHY_REF` settles (V0.4).
5. U12's clinical PDF export is built last and gated hardest — a PDF is the
   artifact most likely to escape into a chart.

The shadow study (Phase 1 / KTD3) now uses the **web capture path**, not a
throwaway native harness. This is strictly better: it validates the code that
will actually ship rather than a harness that will be discarded.

---

## 9. Open questions

1. **The 8-day eviction soak test has not run.** §3.1's entire durability
   argument rests on it. Highest priority.
2. **Hold-drift threshold is uncalibrated** (§4.2). Needs shadow-study data.
3. **Hosting and data governance** (§7) — unresolved, and inherited.
4. **U10's export gating** (§5) — how a session refuses to close unexported.
5. **Android/Chrome sample rate is unmeasured.** iOS is pinned at 60 Hz; Android
   varies by OEM, which is what the validation plan's fleet section was right to
   worry about even though its 90 Hz number was wrong.

---

## Appendix A — Measured platform data

Spike, 2026-08-24. iPhone, **iOS 18.7, Safari 26.6**. Throwaway harness; two
captures. Raw values recorded unconverted — `alpha`/`beta`/`gamma` under their
own spec names, no deg→rad applied at capture — so unit and axis conventions
were tested rather than assumed.

**Timing** (clean capture, 799 samples / 13.30 s):

| | |
|---|---|
| measured rate | **60.00 Hz** |
| `event.interval` | 0.01666666753590107 |
| dt p01 / p50 / p99 | 16 / 17 / 18 ms |
| dt mean | 16.667 ± 0.813 ms |
| dt == 0 | **0** |
| dt outside (0, 500) ms | **0** |
| dt min / max | 4 / 29 ms (two outliers, both in band) |

**A spec violation worth recording:** the `DeviceMotionEvent` specification
defines `interval` in **milliseconds**. iOS reports it in **seconds**
(0.0166… = 60 Hz). Code written as `if (event.interval < 20)` expecting
milliseconds will be wrong by 1000×.

**Units:** accel median magnitude **9.878 m/s²** (not g's — feeds `_IMUDevice`'s
`>3.0` split correctly). `rotationRate` peaked at **243.38**, i.e. deg/s; in
rad/s that would be physically impossible.

**Scored trial** (`method: relative`, β 0.041, EMA α 0.3, `flex_axis_capture`
false):

| | |
|---|---|
| calm run before release | 2.48 s (≥ 0.95 s required) ✓ |
| N / f | 3.5 cycles / 0.9375 Hz |
| R2n / area_ratio | 0.8608 / 0.4347 |
| A0 / A1 / neutral | 69.51° / 95.74° / 107.88° |
| `spasticity_type` / `quality_warn` | Extension / false |
| `score_waveform` | passes=**false**, penalty 6.093 |

The single failing check is horizontal-start (165.9°, needs 180 ± 8) — caused by
the 8.7° hold drift of §4.2, not by the platform. Trimming the recording to the
hold start recovered only ~3° of it (penalty 6.09 → 3.25) while leaving swing
parameters unchanged (A0 69.5 → 69.4, N 3.5, R2n 0.861 → 0.864), which is what
isolated drift as the cause.

**Reproducibility note:** trimming 0.2 s from the front moved `f` from 0.937 →
1.071 Hz — ~14%, from tick-grid and peak-index alignment. Relevant to U11, where
a participant is compared against their own earlier sessions.

**Hold-window drift**, over the 2.62 s qualifying hold:

| | |
|---|---|
| \|omega\| median / mean / max | 0.0712 / 0.1021 / 0.6905 rad/s |
| ∫\|omega\| dt (total path) | 14.7° |
| **net vector rotation** | **8.7°** |
| drift the 0.3 rad/s guard permits over this window | **45°** |
