# Capture Comprehension, Trial Management and Measurement Fixes — Design

**Date:** 2026-09-08
**Status:** approved in brainstorming; awaiting spec review
**Applies to:** `mobile-imu-core/` and `webapp/`, branch `feat/webapp-workbench-restyle`
**Predecessor:** `docs/superpowers/specs/2026-09-04-guided-capture-and-settle-termination-design.md`

---

## 1. Context

Nine requests came out of using the guided-capture build. Reading the code
changed three of them and closed one before any design was done.

| # | Request | Outcome |
| --- | --- | --- |
| 1 | Image: leg position, phone placement, hand position | Unit A |
| 2 | Explain what hold (motion) and drift (pose) measure | Unit A |
| 3 | Audio for holding steady at the start | **Already shipped** — see §1.1 |
| 4 | Delete trials | Unit B, as **exclude** not delete |
| 5 | Choose which metrics feed the PT score | Unit C, as **contribution** not subsetting |
| 6 | Record without a participant ID | Unit B, as a labelled quick-test mode |
| 7 | Misses the last peak before settling | Unit D — **cause found (§2.2), deferred (§2.3)** |
| 8 | Healthy range per metric, coloured | Unit C, as **distance** not a verdict |
| 9 | Lateral motion present, and how much | Unit D — requires a Rust port |

### 1.1 Item 3 is already implemented

`app.js` beeps once per completed second of stability for `code === 1`
(HOLDING) as well as `code === 3` (settling):

```js
const stability = code === 1 ? calm_s : (code === 3 ? settle_s : 0);
if (code === 1 || code === 3) { ...beepsDue... }
```

If it does not fire on the device, that is a bug to diagnose — most likely the
`AudioContext` failing to unlock — not a feature to build. **No work until the
device test says otherwise.**

### 1.2 Three requests were redirected, on the record

Each was put to the user with the conflict stated, and each was decided
toward the option that does not overstate what the instrument knows.

- **Delete → exclude.** Deleting contradicts the 2026-08-27 instruction that
  code flags data quality and never drops it, and on a phone the record is the
  only copy until export.
- **Metric subsetting → contribution.** PT7 is a fixed seven-parameter
  Popović composite. Subsetting makes the number incomparable to
  `HEALTHY_REF`, to the literature, and between two trials.
- **Healthy bands → distance.** `ZONE_CLASSIFICATION_CALIBRATED = false`,
  `HEALTHY_REF` is marked *"PROVISIONAL and expected to move again"*, and the
  shipped disclaimer says the reference "cannot be reproduced from the data it
  is documented as coming from". Colour-coded bands would re-instate exactly
  the classification the app suppresses, per parameter, on that reference.

---

## 2. Item 7 — the missed last peak, diagnosed

> **Corrected 2026-09-08.** The original diagnosis below blamed `min_amp`.
> That was measured and is **wrong**. The binding gate is the
> active-oscillation window's fixed 4-second cap. The corrected finding is
> §2.2; the superseded reasoning is kept as §2.1 because the fix it proposed
> was designed against the wrong cause and should not be revived from the
> summary table alone.

### 2.1 Superseded: the `min_amp` hypothesis

`scoring.rs` gates every extremum on height and prominence:

```rust
let min_amp = 1.0_f64.max(0.05 * a0);
```

At A0 = 45° that is 2.25°, and the last peak of a decaying oscillation is by
definition the smallest, so it looked like the obvious culprit. It is not.
Measured against three synthetic swings, `min_amp` would have admitted 6, 9
and 12 peaks respectively — it is not the constraint that binds.

### 2.2 The actual cause: the 4-second window cap

`_active_oscillation_window_end` bounds extremum counting at
`min(_ACTIVE_WINDOW_CAP_SEC, last_extremum)` past release, and
`_ACTIVE_WINDOW_CAP_SEC` is 4.0 s. At a ~1 Hz swing that is four cycles, so
`N` reads 4.0 for any leg still oscillating after four seconds — which is any
healthy leg. Measured at 20 Hz, A0 = 45°, 1 Hz, varying only damping:

| damping | true cycles above `min_amp` | `N` today | `N` with the window disabled |
| --- | --- | --- | --- |
| 0.25 | 12 | **4.0** | 11.5 |
| 0.35 | 9 | **4.0** | 8.5 |
| 0.50 | 6 | **4.0** | 5.5 |

`N` is identical across a 2x range in how many oscillations physically occur.
It is reporting the cap, not the patient. This is worse than a dropped final
peak: in that regime `N` carries **no damping information at all**, and the
project's own findings call `N` the best metric in the set. It also matches
the standing finding that `HEALTHY_REF["N"] = 3.5` *is* the cap.

### 2.3 Why this is not being fixed here

The cap exists for a real reason: without it a long resting tail lets sensor
noise cross `min_amp` repeatedly and be counted as oscillation, and the file
records `N` reading **0.5 with a 3 s tail and 28.5 with a 30 s tail on the
same motion**.

A candidate fix was built and measured: use the permanent-settle bound the
function's *second* branch already implements — "the first point after which
the signal is permanently within tolerance of neutral" — in **both** branches
instead of the fixed cap. It recovers the true cycle count across the whole
damping range above.

It was not adopted, for a stated reason: **the 0.5-vs-28.5 pathology could not
be reproduced.** A single-drop synthetic with tails from 3 s to 30 s and noise
from 0.3° to 1.0° returns `N = 0.0` under both the current and the candidate
window. The regression the cap exists to prevent is therefore the one the
candidate is *untested* against, and adopting it on that evidence would be
trading a known-wrong constant for an unknown one.

Reproducing that pathology is the prerequisite for revisiting this. Until
then `N` is wrong but stable, which is the safer of the two for a metric
already recorded against existing trials.

**Scope note.** Changing extremum detection changes scored values on every
trial, and `N` feeds PT7, the `HEALTHY_REF` comparison and the MAS grade. It
would be an `algorithm_version` change — already tracked against the wasm and
stamped into every trial — and NOT a `capture_protocol_version` change: what a
trial physically is would not change, only how it is scored.

---

## 3. Unit A — Comprehension

### 3.1 Placement diagram

An inline SVG side-view schematic on the capture view, inside the existing
`#hold-guide` block: plinth edge, supported thigh, hanging shank, phone
outline on the shank with a label, and a marked hand position.

Inline SVG rather than a raster asset: it costs no file, scales on any screen,
and themes with the palette. It is **schematic, not anatomical** — a labelled
line drawing, not an illustration pretending to clinical authority.

The existing "not yet confirmed against the study protocol" note stays until
the wording and the diagram are both reviewed.

### 3.2 Explaining the two gates

`#gates` shows `hold (motion)` and `drift (pose)` as bare numbers. Each gains
a one-line explanation, because the corrective action differs and that is
precisely why the app surfaces them separately:

- **hold (motion)** — how long the limb has been still. Resets on movement.
  *Fix: stop moving the leg.*
- **drift (pose)** — how far the limb has rotated away from where the hold
  started, even slowly. Resets the hold past 5°. *Fix: return the leg to its
  starting position.*

Rendered as help text revealed on tap, not permanently, so the numbers stay
readable one-handed mid-procedure.

---

## 4. Unit B — Trial management

### 4.1 Exclude, never delete

A trial gains `excluded_at` and `excluded_reason`. An excluded trial:

- stops counting toward the session's trial count and the export gate,
- is omitted from the trends view's series,
- **is still stored, still listed (visibly struck through), and still
  exports**, carrying its exclusion and reason.

This mirrors the desktop's `excluded_trials.json` exactly: the exclusion is a
record, not an erasure. Un-excluding is possible and is the reason the field
is a timestamp rather than a boolean — a trial excluded by mistake leaves a
trace.

### 4.2 Quick-test mode

A **Quick test** action records against a built-in participant with a fixed
id. Such trials:

- carry `quick_test: true`,
- are excluded from the trends view unconditionally,
- export with the participant named `QUICK-TEST` rather than a clinic id,
- are visually labelled wherever they appear.

Chosen over recording with a null `patient_id`, which would re-create exactly
the unanchored rows `db.js`'s backfill exists to prevent and which the trends
join would silently skip. The gate shipped in the participant-first work is
unchanged for real trials; quick test is a separate, labelled path, not a way
around it.

---

## 5. Unit C — Metric presentation

### 5.1 Contribution, not subsetting

`pt_score_to_json` already emits `breakdown` — each of the seven parameters'
contribution to the total — and `app.js` already renders it. Unit C surfaces
it properly: each parameter's contribution as a share of the total, sorted
largest first, so "which metric is driving this score" is answerable without
making the score itself variable.

The score always uses all seven. A row may be hidden from the **display**;
hiding never changes the number.

### 5.2 Distance, not a verdict

For each of the seven scored parameters, show:

- the measured value,
- the reference median it is being compared against, as a **visible number**,
- the signed distance between them, in the parameter's own units.

**No red/green. No in-range/out-of-range language. No band shading.** The
reference is shown so the clinician can judge it; the app does not assert a
verdict it cannot defend, and `ZONE_CLASSIFICATION_CALIBRATED` stays `false`.

The reference's provisional status is stated on the same screen as the
numbers, not in a separate note — a caveat one tap away from the value it
qualifies is a caveat that will not be read.

---

## 6. Unit D — Measurement

Heaviest unit, and the only one that changes measured values. It should be
its own implementation cycle.

### 6.1 Last-peak recovery (item 7) — DEFERRED

The envelope-relative threshold this section originally specified was designed
against a cause that measurement disproved. See §2.2: the binding gate is the
4-second window cap, not `min_amp`, so an amplitude-threshold change would not
have fixed the reported symptom. Deferred per §2.3, pending a reproduction of
the `N = 0.5 vs 28.5` tail pathology.

### 6.1a Rate-dependent scoring — LANDED

Not in the original spec; found while investigating §2 and fixed because it
was a live accuracy defect rather than a design choice.

`golden.rs` was generated at `f56a5fe` (2026-08-25) and the Rust core had
since drifted two deliberate Python fixes behind — the phone and the desktop
were scoring differently. Porting them surfaced a defect in the reference
itself: `_RELEASE_BACKOFF_S = 2.0/120` still quantises to whole samples, so it
rounded to **zero back-off at every rate at or below 40 Hz**, the 20 Hz phone
stream included. A0 is read at the release sample, so A0 was being sampled
after the limb had already fallen — a 9.7° under-read of a known 45° swing,
with a systematic −4.92° bias across 243 synthetics.

Fixed to 0.10 s, which is the same physical duration as the smoothing window
because it compensates the same physical effect. `golden.rs` regenerated: 106
constants moved, all of them `TRIAL_*`, the other 127 byte-identical.

The `full_pipeline_recovers_the_motion_it_was_given` ground-truth test is what
caught the reference defect, and it now asserts in two parts — the scorer
against the series it was given, and the fusion chain against the true motion
— because a single bound conflated them and the previous scoring had been
passing it by overshooting the series' own physical ceiling.

### 6.2 Flex axis and lateral motion (item 9)

Port `imu_flex_axis.py`'s `FlexAxisEstimator` to Rust: an online
principal-axis estimator accumulating a 3×3 scatter matrix of above-threshold
gyro vectors, committing after `MIN_COMMIT_SAMPLES`, with optional gravity
levelling.

No dependency is needed — the dominant eigenvector of a symmetric 3×3 matrix
is obtainable by power iteration, and the crate is dependency-free by design.
The port must be numerically faithful to the Python, pinned by golden fixtures
generated from it, exactly as `tests/fixtures/golden.rs` already does for the
scoring path.

Lateral motion is then the fraction of angular-velocity magnitude
perpendicular to the committed axis, reported per trial as:

- `lateral_fraction` — mean perpendicular share over the scored window,
- `lateral_peak_deg_s` — the largest perpendicular component.

**Reported as capture quality, not as a clinical finding.** It answers "was
this trial clean" — whether the limb swung in one plane — not anything about
the patient. It joins `capture_quality` in the record and the manifest.

---

## 7. Quality validation

Unchanged from the previous specs, and non-negotiable on Unit D:

1. Pure-function unit tests; `node --test` and `cargo test`.
2. **Mutation sweep on every new pure function**, each mutant confirmed to
   have actually applied before its result is believed.
3. **Test-count check before and after every append.**
4. Headless browser verification driven from a single `browse eval`.
5. `npm run build:shell` before every commit touching `webapp/src/`.
6. **Unit D additionally:** the full existing fixture corpus must still pass,
   and the `N = 0.5 vs 28.5` tail case must be an explicit regression test.
   Any change in a golden value is a finding to explain, never a fixture to
   update.

---

## 8. Deployment readiness

Unchanged: full suites green, `build:dist` succeeds, headless walk clean
against `dist/`, and a **physical-device smoke test performed by the user**.
`pendulastic-app.vercel.app` is not updated by this work.

Unit D changes scored values, so `ALGORITHM_VERSION` will bump and trials
scored either side of it will differ. That is what the field is for, and it is
already stamped into every trial and manifest.

### 8.1 Carried forward, still open

- The settle-termination build has **not yet had its device test**. The
  5-second target and the 0.3 rad/s stillness bound remain hypotheses.
- The hold-the-leg wording is still unconfirmed against the study protocol.
- No icons; `manifest.json` has no `icons` and `index.html` no
  `apple-touch-icon`.

---

## 9. Risks

| Risk | Mitigation |
| --- | --- |
| A window change reopens the noise-counted-as-cycles failure | Not taken: the 0.5-vs-28.5 pathology could not be reproduced, so the guard is untestable today and the change is deferred (§2.3) |
| The Rust flex-axis port drifts from the Python | Golden fixtures generated from the Python, as the scoring port already does |
| Lateral motion read as a clinical finding | Reported under capture quality, worded as trial cleanliness |
| Quick-test trials pollute real data | Separate participant id, `quick_test` flag, excluded from trends, labelled in the export |
| Distance display read as a verdict anyway | No colour, no band language, reference shown as a number, provisional status on the same screen |
| Excluding a trial is mistaken for deleting it | The trial stays listed and still exports; the field is a timestamp so the act is itself recorded |

---

## 10. Out of scope

- Deleting trial data. Declined; exclusion is the mechanism.
- Making the PT score's parameter set configurable.
- Colour-coded healthy/impaired bands, until `HEALTHY_REF` is recalibrated
  (validation task V0.4).
- Recalibrating `HEALTHY_REF` itself.
- Any change to how the seven parameters are computed. The extremum-detection
  fix this section used to carve out is deferred (§2.3); the rate-dependence
  fix that did land (§6.1a) was a defect, not a redesign.

---

## 11. Implementation order

1. **Unit A** — comprehension. No data change, immediately useful, and the
   diagram is the item most likely to need a review round.
2. **Unit B** — trial management. Small schema addition, no measurement change.
3. **Unit C** — metric presentation. Surfaces data that already exists.
4. **Unit D** — measurement. Own cycle. Changes scored values, touches the
   Rust core, and is the only unit whose regression risk reaches existing data.
