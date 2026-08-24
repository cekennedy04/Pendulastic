---
title: Mobile App — Validation, Build, and Test Plan
type: plan
date: 2026-08-24
topic: mobile-app-validation-build-test
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
execution: code
supersedes: nothing
extends: docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md
---

# Mobile App — Validation, Build, and Test Plan

## Goal Capsule

- **Objective:** Get from "two half-built mobile tracks and an unvalidated
  instrument" to "one mobile app a clinician can run alone, whose numbers we
  can defend." Three interlocking workstreams: **validate** (does a phone
  measure the pendulum test well enough to be worth shipping), **build** (the
  app itself), **test** (the permanent quality harness under both).
- **Relationship to the existing plan:** `docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md`
  owns *what the IMU app is* (screens, units U0–U13, requirements R1–R15). This
  plan owns *what has to be true before, during, and after building it* — the
  validation gates it depends on and the test infrastructure it currently
  assumes but does not have. It does not restate or change that plan's scope.
- **Open blockers:** none. Modality order is settled (IMU first, then RGB — §2 D1),
  and an OptiTrack-validated participant corpus already exists, which makes most of
  Gate G0 answerable retrospectively without new lab time (§3).

---

## 1. Where the project actually stands

Stated from the repo and its own reports, not from aspiration. Every claim
below is traceable to a file.

### 1.1 There are two mobile tracks, and they are at very different maturities

| Track | Artifact | State | Tests |
|---|---|---|---|
| RGB / markerless | `mobile/` (React Native + Expo 54, ~3.2k LOC) | Complete 5-screen app shell (record, review, analysis, participant), but a **thin client**: it streams JPEG frames over a WebSocket to a FastAPI backend (`mobile/hooks/useWebSocketStream.ts`, `web/api/routers/ws_stream.py`). Nothing is computed on the phone. Backend host is a hard-coded hotspot IP (`mobile/constants/Config.ts:11`). | **Zero.** No test runner, no `test` script in `mobile/package.json`. |
| IMU / on-device | `mobile-imu-core/` (Rust, 745 LOC) + native harness apps | AHRS fusion, bias calibration, stillness gate, release detector ported (`src/ahrs.rs`, `src/calibration.rs`, `src/stillness.rs`). **Scoring is not ported yet** (plan unit U2). Throwaway iOS/Android capture harnesses exist (`harness-ios/`, `harness-android/`) — that is plan unit U0, already done. | `tests/ahrs_test.rs` only (196 lines). |

Neither track is currently standalone: the RN app cannot work without a laptop
running `uvicorn`, and the Rust core cannot yet produce a score.

### 1.2 The instrument is not validated — and one headline number needs reconciling

`docs/reports/2026-08-19-full-project-analysis-vs-mas.md` is the honest
internal accounting. Its findings define what this plan has to gate on:

| Measure | Value | Source |
|---|---|---|
| IMU trajectory RMSE vs OptiTrack | **14.84° mean / 10.98° median** | §1 |
| MediaPipe trajectory RMSE vs OptiTrack | **36.0° mean / 33.3° median** | §1 |
| Share of IMU RMSE that is a constant per-trial offset | **67.6% mean / 76.0% median** — strip it and residual scatter is 9.71°/7.37° | §3 |
| ICC(2,1) across the 7 PT parameters, IMU | 0.014 – 0.458 (poor–fair) | §1 |
| MAS correlation (R2n, `mas_grade`) | ρ = −0.313, p = 0.014, n = 61 trials / 5 participants | §4 |
| Leave-one-participant-out AUC, all 7 IMU metrics | **0.21** (below chance) | §6 |
| Production `compute_pt_score()`, Control vs MS, naive test | p = 0.5865 (not significant) | §7.1 |

**The 4.19° reconciliation (blocking, cheap).** The project brief cites "manual
annotation has reached an RMSE of 4.19°, satisfying the <6° threshold." That is
not the same quantity as the 36.0° MediaPipe number above: 4.19° is an
*annotation-fidelity* figure (how closely hand-labelled keypoints reproduce the
reference), whereas 36.0° is the *automatic pipeline end-to-end* figure. Both can
be true simultaneously, and if they are, the gap between them is exactly the
error the HPE model contributes. Until someone puts both numbers in one table
produced by one script, any accuracy claim about the RGB track is unsafe to
repeat externally. That is task **V0.1** below.

### 1.3 Infrastructure gaps that will silently corrupt validation if left alone

- **The backend fabricates data when HPE is unavailable.** `web/api/routers/ws_stream.py:143`
  and `:157` fall back to `_synthetic_keypoints()` — a damped-cosine simulation
  returned with `tracking_status: "stable"` and confidences of 0.91–0.97. A
  client cannot distinguish it from a real measurement. Any validation session
  run through the RN app on a machine where the worker import fails produces
  plausible, entirely fictional data. **This must be fixed before any mobile
  data collection** (V0.2).
- **No CI at all.** `.github/` does not exist. The existing plan's Verification
  Contract (`cargo test`, `xcodebuild test`, `./gradlew test`) is manual-only.
- **Backend state is in-memory** (`web/api/store.py`) — trials vanish on
  restart. Fine for a demo, not for a clinic day.
- **Backend hardcodes one developer's machine** (`web/api/services/pipeline_bridge.py:31-33`:
  `C:\Users\cladi\miniconda3\...`). The RGB track cannot currently run anywhere else.
- **Two different PT-parameter implementations** exist (`workbench_engine.windowed_pt_params()`
  vs the production `pendulastic_pt_score.compute_pt_params()`), and they disagree
  on which trials are even scoreable (49 → 40) — report §7.0. The mobile port
  must target the production one, and the test suite must pin that.

---

## 2. Decisions

### D1. IMU first, then RGB. *(settled 2026-08-24, owner-directed)*

**The on-device IMU app is the mobile app. The RGB/markerless track follows it,
rather than being abandoned or run in parallel.**

Rationale, entirely from §1.2: IMU is the only modality with measurable
agreement against ground truth, and its dominant error term is a *fixable
calibration bias*, not a sensing ceiling. MediaPipe's agreement is
"statistically indistinguishable from noise on every one of the 7 metrics" —
building a clinical scoring app on it today would be shipping a number we know
is wrong.

What "then RGB" means concretely, so it doesn't quietly become "never RGB":

- The RN app (`mobile/`) is **not** frozen or deleted. It keeps its capture,
  review, and annotation role throughout Phases 0–3, and carries a
  non-dismissible "research capture only — not a clinical measurement" banner
  until Gate G0-RGB passes.
- Every RGB trial recorded during Phases 0–3 is a **free training/validation
  sample for the camera track**, because the IMU sessions are co-recorded with
  OptiTrack anyway. The RGB track's data problem gets solved as a side effect of
  the IMU track's schedule — provided V0.2 lands first, so none of that video is
  contaminated with fabricated keypoints.
- RGB re-enters the scoring path in **Phase 4** (§9), gated on G0-RGB, reusing the
  same test layers (§7) and the same gate structure. It inherits a working app
  shell, a working export format, and a validated scoring core — which is exactly
  why sequencing it second is cheaper than running it in parallel.

### D2. Validation gates block build work, not the reverse.

Phase 0 and Phase 1 exist to stop the project from building two native app
shells on a core that cannot measure. This mirrors the existing plan's KTD3
shadow gate and extends it backwards: KTD3 asks "does the phone match our own
pipeline"; Phase 0 asks the prior question, "does our own pipeline match
reality." A phone that faithfully reproduces a 14.84°-error pipeline is not a
success.

### D3. Ground truth is OptiTrack, with a bench cross-check.

OptiTrack Motive stays the reference (`optitrack_angle_from_markers.py`,
`motive_sync.py`, `natnet_client.py` already exist). Added: a **mechanical bench
check** (§3, V0.5) — a rigid pendulum of known length swung with the phone
strapped to it — because it separates *sensor* error from *human-mounting* error,
which no human-subject recording can do.

### D4. Test fixtures are generated from the Python reference, not hand-written.

The Rust core's correctness question is "does it reproduce
`pendulastic_imu_server.py` / `imu_calibration_tuner.py`". So the golden vectors
must be emitted by those modules (§5, L1), regenerable by one command, and
checked into `mobile-imu-core/tests/fixtures/`.

---

## 3. Phase 0 — Instrument validation (blocks everything)

Purpose: establish that a phone-mounted IMU, under a controlled protocol, can
measure knee angle to a defensible accuracy — before any app is built on it.

**An OptiTrack-validated participant corpus already exists** (~53 trials across 5
IMU-validated participants, of which 49 are 3-modality matched and 40 survive the
production scoring gates — report §1, §6, §7.0). That corpus is what produced
every number in §1.2, and it changes this phase's shape considerably: the
majority of Gate G0 is a *re-analysis* task that can start immediately, not a
recording task waiting on lab availability. Phase 0 therefore splits in two.

### 3A. Retrospective — runs today, no lab time, no new participants

| ID | Task | Output |
|---|---|---|
| **V0.1** | Reconcile the accuracy numbers. One script, one table: manual-annotation RMSE, MediaPipe-automatic RMSE, IMU RMSE on the *same* trial set, using the **production** `compute_pt_params()` — not `workbench_engine.windowed_pt_params()`, which produced most of the existing figures and disagrees about which trials are even scoreable. | `docs/reports/YYYY-MM-DD-accuracy-reconciliation.md` |
| **V0.2** | Remove the silent synthetic-keypoint fallback (`web/api/routers/ws_stream.py:143,157`). Opt-in behind `PENDULASTIC_SYNTHETIC_HPE=1`; when active, tag every payload `tracking_status: "synthetic"` and banner it in both clients. Absent the flag, a worker failure closes the socket with an error. **Blocks all further data collection**, IMU or RGB. | PR + regression test |
| **V0.1b** | **Per-participant bias decomposition.** Report §3 established that a constant offset explains 67.6%/76.0% of RMSE corpus-wide. Re-run it *per participant and per session* rather than pooled: if the offset is stable within a participant but varies between them, it is a mounting/zeroing artifact and V0.3's protocol fix will work. If it varies trial-to-trial within one participant, it will not, and V0.3 needs redesigning before it is recorded. **This is the cheapest possible test of V0.3's core assumption, and it should run before V0.3 is scheduled.** | Report section + decision |
| **V0.1c** | **Retrospective repeatability.** Compute within-session CV of the composite PT score across each participant's repeated trials on the existing corpus. Gives G0's repeatability criterion a real baseline instead of "not measured", using data already on disk. | Baseline table |
| **V0.1d** | **Re-run the release-anchored re-zero** across the full corpus under the production pipeline (report §3 tried it and got RMSE roughly flat but trials-under-5° from 2→6). If that tripling holds under production scoring, it is a scoring-path change, not just a diagnostic — and it moves G0's hardest criterion. | Decision: adopt or reject |
| **V0.4** | IMU-record the 7 existing video-only controls (report §8 priority 1 — zero new recruitment). Re-derive `HEALTHY_REF`; re-verify `PT_HEALTHY_MAX`/`PT_BORDERLINE_MAX`; run the control-sensitivity figure that has never once executed (report §7.2, Fig 9, blocked at n=1 control). | Updated constants + figure |

> Note: `Recordings/`, `OptiTrack_Recordings/` and `data/` are gitignored and do
> not exist in a fresh clone — 3A runs on the machine holding the corpus.

### 3B. Prospective — the one thing retrospection cannot answer

| ID | Task | Output | Effort |
|---|---|---|---|
| **V0.3** | **Verified-mount arm.** The existing corpus *is* the ordinary-mount arm — it was recorded that way, and it is exactly the data that produced 14.84°. So this records only the other arm: rigid, verified, non-slipping mount with a strict zero-posture protocol, co-recorded with OptiTrack, ≥20 trials. Half the experiment is already done. | Report + protocol decision | 1 session |
| **V0.5** | **Bench pendulum check.** Rigid pendulum of known length ⇒ known natural frequency; phone strapped to it; compare measured `f` and decay against the analytic solution and against OptiTrack. Run on every device model in the fleet (§6). Separates *sensor* error from *human-mounting* error, which no human-subject recording can do. | `docs/reports/…-bench-validation.md` | 1 day |
| **V0.6** | Record `mas_extension` (not just collapsed `mas_grade`) going forward, and switch the clinical-correlation target to it — the pendulum test is an extensor-spasticity probe (report §7.5). Protocol change, not code. | Updated intake form | ongoing |

**The confound to state plainly.** Comparing a newly-recorded verified-mount arm
against a historical ordinary-mount corpus is *not* a clean within-session A/B:
different days, different participants, and the AHRS config was re-tuned between
them (report §2). Two mitigations, in order of preference: (a) re-record a
paired ordinary-mount arm in the same session for at least a subset of
participants, which restores the within-session comparison for a few extra
minutes per participant — **strongly preferred**; (b) failing that, re-run the
historical corpus through the *current* config so at least the algorithm is held
constant, and report the remaining participant/session confound as a stated
limitation rather than letting it sit unnoticed in a delta.

### Gate G0 — IMU (blocking for Phase 1)

| Criterion | Target | Today | Answerable from existing corpus? |
|---|---|---|---|
| Trajectory RMSE vs OptiTrack | ≤ 10.0° mean / 8.0° median | 14.84 / 10.98 | Baseline yes; **target needs V0.3** |
| Bias-removed residual scatter | ≤ 8.0° mean | 9.71 | **Yes** — V0.1b |
| Trials under the 5.0° clinical goal | ≥ 50% | 6 of 53 | Baseline yes; V0.1d may move it; target needs V0.3 |
| ICC(2,1) ≥ 0.75 | on ≥ 4 of 7 parameters | max 0.458 | **Yes** — recomputable on the matched set |
| Within-session score repeatability | CV ≤ 15% | not measured | **Yes** — V0.1c |

Three of the five criteria can be evaluated this week. The two that cannot are
precisely the two the verified-mount protocol is meant to move — which is the
right division of labour: the retrospective work tells you whether V0.3 is worth
booking before you book it.

**If G0 fails:** the failure mode is informative, not fatal. Fail on absolute
RMSE but pass on bias-removed residual ⇒ the problem is protocol/zeroing, iterate
on V0.3's mount and re-run. Fail on both ⇒ stop and reconsider the modality
before spending native-app effort. Fail V0.1b specifically (offset varies
*within* a participant) ⇒ the bias is not a mounting artifact, V0.3 as designed
will not fix it, and the sensor-vs-mount question moves to V0.5's bench rig
before any more human sessions are booked.

### Gate G0-RGB — deferred to Phase 4, not cancelled

The RGB track re-enters the scoring path only if, on the same trial set,
automatic MediaPipe RMSE ≤ 10° **and** its PT-parameter effect-size signs match
OptiTrack's — they are *inverted* today (report §6a), which is a more serious
failure than the RMSE number alone implies. Until then the RN app carries its
"research capture only" banner. See §9.

## 4. Phase 1 — On-device shadow study (the existing plan's KTD3 gate, made concrete)

Prerequisite: G0 passed; `mobile-imu-core` U2 (angle math + Popović scoring)
implemented so the Rust core can produce a score offline.

- **Capture:** the U0 harness apps (already built) record raw timestamped
  accel/gyro/mag on a real phone while the existing desktop pipeline records the
  same trial simultaneously. 20–30 trials, ≥3 participants, both legs.
- **Compare:** Rust core score vs desktop Python score on the *same raw stream*
  (algorithmic fidelity), and phone-captured vs desktop-captured stream on the
  same physical trial (capture fidelity). These are two distinct questions and
  the study must report them separately — a green overall number can hide a port
  bug cancelling a capture bug.
- **Thresholds (Gate G1, blocks U3 and all native app work):**
  - Same-input algorithmic parity: every one of the 7 parameters within **1e-3
    relative**, and **sign-identical** on all trials (including a
    negative-first-flexion severe-hypertonia fixture, per U7).
  - Cross-capture agreement: composite PT score Bland-Altman **bias ≤ 0.05**,
    95% limits of agreement within **±0.15**; per-parameter ICC(2,1) **≥ 0.80**.
  - Sample-rate audit: sustained effective rate **≥ 90 Hz** with **no gap > 100 ms**
    on every device in the fleet (this is also the data source for the plan's
    KTD11 `capture_quality` completeness check).
- **Deliverable:** the numeric thresholds that the existing plan leaves as an
  Outstanding Question — U13's attachment-stability residual and swing-range
  plausibility bounds — get *set here*, from this data, and written back into
  the U13 spec.

---

## 5. Phase 2 — Build

No new units are invented here; the existing plan's U1–U13 stand. This section
records only sequencing, and the four things that plan assumes but does not
provide.

### 5.1 Sequence

```
DONE      U0 harness ──────────────────────────────────┐
DONE(pt)  U1 fusion/calibration/stillness/release      │→ Phase 1 shadow study → G1
TODO      U2 angle math + Popović scoring (production  │
          compute_pt_params parity, not workbench)     ┘
                    │ G1 passes
                    ▼
          U3 UniFFI bindings + cross-compile  ──►  U9 mounting guide
                    │                              U8 participant model
          ┌─────────┴─────────┐
          ▼                   ▼
      U4 iOS app          U5 Android app
          └─────────┬─────────┘
                    ▼
       U6 export ─► U13 quality gate ─► U10 3-trial protocol
                    ▼
       U7 parity test ─► U11 trend view ─► U12 clinical PDF
```

### 5.2 The four missing prerequisites

- **P1. Golden fixtures.** `tools/gen_core_fixtures.py` emits, from the Python
  reference, a set of `(raw sample stream → expected quaternion trace → expected
  angle trace → expected 7 parameters)` JSON fixtures into
  `mobile-imu-core/tests/fixtures/`. Regenerating them is a reviewed diff, so an
  intentional algorithm change is visible and an accidental one is a failing test.
- **P2. Device fleet + capability floor.** Named, procured, and sample-rate
  audited *before* U4/U5 (§6). Resolves the TODOS item "define a
  hardware-capability fallback policy" with data instead of a guess.
- **P3. Distribution accounts.** Apple Developer team + provisioning, Firebase
  App Distribution project (or signed-APK channel), a macOS/Xcode build box.
  This is the TODOS "KTD8 credentials" item; it has organizational lead time and
  should be started at the beginning of Phase 0, not when the first build is ready.
- **P4. CI.** §5.3.

### 5.3 CI (new — the repo has none)

`.github/workflows/ci.yml`, triggered on PR:

| Job | Runner | Runs |
|---|---|---|
| `rust-core` | ubuntu | `cargo test`, `cargo clippy -- -D warnings`, `cargo fmt --check` |
| `python` | ubuntu | `pytest tests/` (the existing ~49 test modules), plus the export round-trip test |
| `mobile-rn` | ubuntu | `npm ci && npx tsc --noEmit && npm test` in `mobile/` |
| `android` | ubuntu | `./gradlew test` (JVM unit tests + Robolectric) |
| `ios` | macos | `xcodebuild test` against the simulator for pure-logic targets |

Physical-device tests (§5, L5) stay manual/nightly — deliberately, because the
simulator has no IMU. CI's job is to make *the port* trustworthy; only devices
make *the app* trustworthy.

---

## 6. Device fleet and capability floor

Minimum viable fleet — two iOS, three Android spanning the tier range, because
Android sensor-rate behaviour is the known variable:

| Slot | Example | Why |
|---|---|---|
| iOS current | iPhone 15/16 | Baseline `CMMotionManager` behaviour |
| iOS oldest supported | iPhone SE (2nd/3rd gen) | Smallest screen — R9 dynamic-type/clipping risk; oldest sensor stack |
| Android flagship | Pixel 8/9 | Reference `SensorManager` behaviour |
| Android mid-tier | Samsung A-series | Where OEM sensor-rate throttling actually shows up |
| Android budget | any ≤ $200 device | Capability-floor probe: does it even sustain 90 Hz? |

Each device runs the V0.5 bench check and the Phase 1 sample-rate audit. A
device that cannot sustain the floor is not a bug to fix — it is an entry on a
supported-device list, and the app must detect and refuse it at capture time
(this is the concrete resolution of the TODOS hardware-fallback item).

---

## 7. Test strategy

Eight layers. L1–L4 run in CI on every PR; L5–L7 are device-gated and run per
release candidate; L8 is science, not software, and runs per data-collection wave.

### L1 — Rust core unit + golden-vector tests (`cargo test`)
- Existing `ahrs_test.rs` extended to: quaternion identities, gimbal-lock
  boundary (the Ockendon β bug found in report §2 gets a permanent regression test),
  gyro/accel bias estimation on synthetic still windows, release-detector state
  machine (calm→pending→fired, and the never-fires path that must raise
  `ReleaseNeverDetected`).
- Golden vectors from P1: angle trace within **1e-6**, each of the 7 parameters
  within **1e-3 relative** and sign-exact.
- Property tests: score is invariant to sample-buffer reordering *iff* fusion is
  batch (this test is how the plan's open "timestamp/ingestion policy" question
  gets settled rather than deferred); out-of-order and delayed-callback inputs
  return a typed error, never a panic (KTD7).

### L2 — Python reference + interop (`pytest`)
- The existing 49 test modules stay green (they are the reference's own guarantee).
- **New:** `tests/test_mobile_export_roundtrip.py` — a trial exported by the app
  is consumed by `imu_calibration_tuner.replay_trial()` with **no format
  conversion** (plan R6/U6), and the replayed score equals the on-device score.
  This is the single most load-bearing interop test in the project.
- **New:** `tests/test_ws_stream_no_silent_synthetic.py` — locks V0.2 in place so
  the fabricated-data hazard cannot regress.

### L3 — Cross-binding parity (plan U7, strengthened)
The existing plan's U7 proves the *same Rust code* returns the same answer through
Swift and Kotlin — which the TODOS file itself flags as "weak to the point of
being misleading." Strengthened here: parity is asserted on **device-captured**
streams, not only on a desktop fixture, and covers unit normalization (iOS g's
vs Android m/s², KTD10), timestamp-domain conversion (`CMTime` vs
`SystemClock.elapsedRealtimeNanos`), and orientation-change survival mid-trial.

### L4 — App-level unit/component tests
- `mobile/` (RGB): add Jest + `@testing-library/react-native` + a `test` script —
  currently absent entirely. Cover the Zustand protocol gate (`mobile/store/index.ts`),
  the WS framing header (`useWebSocketStream.ts` — an 8-byte little-endian
  header is exactly the kind of thing that silently breaks), and screen
  render/empty/error states.
- iOS: XCTest on view models and the capture state machine.
- Android: JUnit + Robolectric on the equivalent.

### L5 — Instrumented device tests (XCUITest / Espresso)
Made deterministic by a **recorded-sensor replay seam**: the capture layer reads
from an injectable sample source, so a device test can replay a fixture stream
instead of requiring a human to swing a leg. Without this seam, device tests are
unrepeatable and will be abandoned within a month. Scenarios: full
calibrate→record→score→export flow; permission denied; permission granted
mid-flow; app backgrounded mid-trial; storage full at export.

### L6 — End-to-end clinical protocol test (manual, scripted, per RC)
The 3-trial protocol (R11) run start to finish on a physical device by someone
who did not write the code, against a written script, with one trial deliberately
ended early and one deliberately mounted loose (must be caught by the U13
`capture_quality` flag, not silently scored). Result recorded as pass/fail per
step, not as a vibe.

### L7 — Non-functional
| Concern | Check | Bar |
|---|---|---|
| Sustained sample rate | 60 s capture, per device | ≥ 90 Hz, no gap > 100 ms |
| Thermal / long session | 20 consecutive trials | No rate degradation > 5% |
| Battery | full clinic-day simulation (~40 trials) | < 25% drain |
| Accessibility (R9) | VoiceOver/TalkBack full pass, largest dynamic type, contrast audit | No clipped control, no color-only indicator, chart has a table alternative |
| Data-at-rest | participant records + raw streams | Matches whatever the TODOS data-handling decision lands on — this test exists to force that decision to be made |

### L8 — Statistical validation (per data wave, not per PR)
Bland-Altman + ICC(2,1) vs OptiTrack; test–retest reliability; minimal
detectable change (MDC95) — because a clinician needs to know what score change
is real; and leave-one-participant-out re-runs of report §6 and §7.1 as the
cohort grows. **Explicitly:** no MAS-equivalent label is displayed in the app
until `_MAS`'s thresholds are re-derived from this project's own data
(report §7.4). The app shows parameters and the composite score; it does not
claim an Ashworth grade.

---

## 8. Phase 3 — Clinical readiness (after the app runs)

- Usability sessions with ≥3 clinicians who are not the developer, on the real
  3-trial protocol, timed. Target: a full participant assessment in under 5
  minutes including mounting.
- Re-run Gate G0's statistics using **app-captured** data rather than
  desktop-captured — the closing loop of the whole plan.
- Resolve the TODOS items this plan has been feeding: trial history/recovery
  model, participant-name disambiguation, data-handling policy, hardware-floor
  policy (now answered by §6's audit).
- `/design-consultation` for the visual system (KTD6, still deferred and still
  unstarted).
- **Recruit across the full MAS severity range** (report §8 priority 3). The
  current MS cohort tops out at MAS 1+ and contains *zero* trials at MAS ≥ 2, so
  no claim about behaviour at moderate-to-severe spasticity is possible at any
  sample size. This is the one gap neither the existing corpus nor a better
  protocol can close — only recruitment can.

---

## 9. Phase 4 — RGB re-entry (the "then RGB" half of D1)

Runs after the IMU app ships. It inherits a validated scoring core, a working
export format, a device fleet, and CI — which is the whole argument for
sequencing it second rather than in parallel.

- **R4.1 — Harvest the co-recorded video.** Every Phase 0–3 IMU session was
  co-recorded with OptiTrack and, where the RN app was used, with video. That
  corpus is the camera track's validation set, obtained for free — provided V0.2
  landed first, so no frame of it is contaminated with synthetic keypoints.
- **R4.2 — Attack the 36° directly.** V0.1's reconciliation will have already
  separated annotation error from model error. Whichever term dominates decides
  the work: annotation-dominated ⇒ labelling protocol and IK constraints;
  model-dominated ⇒ model selection and preprocessing, where the repo already has
  substantial sweep tooling (`sweep_mediapipe_preprocessing.py`,
  `run_new_models_evaluate.py`, the 8-model benchmark).
- **R4.3 — Fix the sign inversion before the RMSE.** Report §6a found IMU and
  MediaPipe PT parameters pointing the *wrong direction* relative to OptiTrack on
  nearly every metric. A camera pipeline that reads a severe leg as mild is worse
  than one that is merely imprecise, and no amount of RMSE improvement addresses
  it. This is the real G0-RGB blocker.
- **R4.4 — Decide the deployment shape.** The RN app is a thin client today; an
  on-device camera pipeline is a different architecture. Whether RGB ships as
  on-device inference or keeps a backend is a decision for this phase, informed by
  what the IMU app's on-device experience actually taught us.
- **R4.5 — Then the same gates.** G0-RGB, then an RGB shadow study mirroring
  Phase 1, then the same eight test layers. No new plan structure required.

---

## 10. Verification Contract

| Scope | Command | Proves |
|---|---|---|
| Rust core | `cargo test` in `mobile-imu-core/` | L1 — fusion, calibration, scoring, golden-vector parity with Python |
| Python reference + interop | `pytest tests/` | L2 — reference intact, export round-trips through `replay_trial()` |
| RN app | `npx tsc --noEmit && npm test` in `mobile/` | L4 — types and component behaviour (test runner to be added) |
| Android | `./gradlew test` / `connectedAndroidTest` | L4/L5 |
| iOS | `xcodebuild test` | L4/L5 |
| Accuracy | `python evaluate_all_participants.py` + the V0.1 reconciliation script | G0 thresholds |

## 11. Definition of Done

- G0 and G1 both passed, each with a dated report in `docs/reports/` carrying the
  actual numbers against the thresholds in §3 and §4 — not a narrative summary.
- The existing plan's U1–U13 complete, with U13's deferred numeric thresholds
  filled in from Phase 1 data.
- CI green on all five jobs; `mobile/` has a real test suite where it has none today.
- A trial captured on a physical iOS device and a physical Android device
  round-trips through `replay_trial()` unmodified and reproduces its on-device score.
- The synthetic-keypoint fallback can no longer fire silently, and a test enforces it.
- No MAS-equivalent label is shown anywhere in the app.
- Every claim in the README and the project brief matches a number in a dated
  report, including the 4.19° / 36.0° reconciliation.

## 12. Risks

| Risk | Mitigation |
|---|---|
| **G0 fails** — the bias is not fixable by protocol | Fail-fast by design, and now cheaper still: §3A's retrospective work tests V0.3's core assumption on data already on disk, so a doomed protocol experiment is caught before it is booked. If it is booked and still fails, the cost is one session and two Rust modules, not two native app shells. V0.5's bench rig separates sensor error from mounting error so we don't blame the wrong one. |
| Participant supply gates the science, not the code | §3A needs no recruitment at all (existing OptiTrack-validated corpus), and V0.4 needs none either (7 controls already enrolled, video-only). Only the MAS ≥ 2 severity gap (§8) genuinely requires new recruitment, and build work proceeds in parallel with it once G1 passes. |
| Rust + UniFFI is new to this all-Python repo | Confined to a 745-line core with golden fixtures pinning it to a reference implementation that already works. |
| Device fleet unavailable | §6's fleet is the floor; a two-device (one iOS, one Android) start is workable if the supported-device list is correspondingly narrow and stated. |
| The RGB track quietly rots — "then RGB" becomes "never RGB" | Phase 4 (§9) is a real phase with real tasks, not a footnote, and it is fed continuously: every IMU session in Phases 0–3 is co-recorded video, so the camera track's validation corpus grows on the IMU track's schedule. Its banner and capture role hold in the meantime. |

## 13. Open questions for the owner

*Resolved 2026-08-24:* modality order (**D1** — IMU first, then RGB) and ground-truth
availability (**an OptiTrack-validated corpus already exists**, restructuring Phase 0
into §3A retrospective and §3B prospective).

Still open:

1. Which devices are actually in hand today (§6)? The fleet list is a
   recommendation, not an inventory — and V0.5's bench check needs to run on each
   of them.
2. Apple Developer / Firebase account ownership (P3) — who holds it, and is the
   lead time started? This is the item most likely to block a build at the
   moment the build is finally ready.
3. For V0.3: can a paired ordinary-mount arm be recorded in the same session as
   the verified-mount arm (§3B's mitigation (a))? It costs a few extra minutes
   per participant and removes the historical-baseline confound entirely.
