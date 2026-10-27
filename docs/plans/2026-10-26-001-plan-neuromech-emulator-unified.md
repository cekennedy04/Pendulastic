# Plan: the knee-spasticity emulator — one program from three bodies of work

**Date:** 2026-10-26 · **Branch:** `feat/neuromech-emulator` (off `bench/retest-diagnostics`)
**Supersedes as the build plan:** `2026-10-25-001-plan-vertical-rig-neuromuscular-model.md` (kept; its
findings stand and are cited here) and the neuromech report published 2026-10-26.

## 1. What this plan reconciles

| Work | Where | What it contributes |
|---|---|---|
| **A. Mechanism model** (claude.ai session) | `rig_neuromuscular_model.py`, `severity_grading_test.py`, `rig_calibration.py`, 10-25 plan + report | Severity *and* mechanism as separate axes; catch-and-hold, end-stop, capsule, adaptation; release-height probe; blind clinician calibration (R3); actuator spec; literature incl. Riener & Burgkart prior art |
| **B. Tone sweep** | `severity_tone_sweep.py` (10-24) | Linearised tone sweep with a sensitivity arm on the rest shift |
| **C. neuromech** | `neuromech/` | De Groote 2018 core checked against the authors' MATLAB; LabVIEW-portable 1 kHz twin; Formula Node verified to 1e-11; 45-scenario library (55 after merge); rig tracking measurement; ILC |

### 1.1 Reconciliation results (run 2026-10-26 on this branch)

**The scorer.** A and C were scored by different scorers. A's branch carried `main`'s stale
constants (HEALTHY_REF ω_max_n 9.57, f 0.884, no 2 Hz flag); C used the validated fix-lineage scorer
(6.77, 0.914). Re-running A's `severity_grading_test.py` unchanged on the validated scorer:

| A's finding | stale scorer | validated scorer | status |
|---|---|---|---|
| healthy limb (tone_L0) | MAS 1 | **MAS 0** | corrected |
| grades across 5 dialled severities | 1 (+refusal) | **1** (0; 0; 0; 0; refused) at the production convention | **holds, stronger** — L0 to L3 all band 0 |
| PT7 spread at one intended severity | 9.2× | **23.0×** at intended MAS 2 (grades 4/2/0); 17.0× at MAS 3 (4/0) | **holds, stronger** |
| PT7 signal ÷ mechanism confound | 0.10 | **0.22** | **holds** (< 1) |
| healthy limb, no ring-down | MAS 4 | **MAS 4** | **holds** |
| unnormalised metrics beat ratios | yes | yes (ω_peak 3.1, plateau 2.1, A0 2.1; every ratio < 1) | **holds** |

**The scoring convention.** A's test called `compute_pt_params` with its default `detrend=True`; production
is `detrend=False`. That flag alone moved tone_L1–L3 between band 1 and band 0. The test now uses the
production convention, and everything else scores through one call, `neuromech.build.score_angle`.

**The physics.** Three models give three answers on PT7 vs severity: C (De Groote, nonlinear,
SRS + delayed force reflex) inverts; B (linear, tone scales B and K) is monotone; A (tone ladder)
is monotone but flat inside one band. That disagreement *is* A's finding: PT7 reads which mechanism
produced the stiffness. It is reported, not resolved.

**The passive standard.** A's audit bounds a real shank at 0.80–0.97 Hz by three routes; the
Bajd & Bowman set (B = K = 0.5, m 5 kg, l 0.6 m) sits at 0.80. C's De Groote limb at leg scale 0.87
swings at **0.915 Hz** against the validated HEALTHY_REF f **0.914 Hz**, inside A's physics band.

**The geometry.** A infers rest 118° (A0 63°), C 125° (A0 55°), and the August per-trial CSV says 142°
(A0 36°, 17 trials, pre-fix). The optical cache cannot settle it (13 control trials, 3 with ≥ 90 % coverage).
Three sources, three rests: it becomes a measured input.

## 2. Decisions taken, with the evidence

- **D1 — One physics core: De Groote 2018, nonlinear gravity.** Checked against the authors' code,
  reproduces all three hallmark features, matches our controls' frequency unfitted, and its
  `mgl·cos` gravity answers Lin & Rymer's point that the linear form is inadequate even for normal
  legs. A's mechanisms (tonic length hold, velocity channel, end-stop, capsule, adaptation) were kept
  as **profiles rendered by A's module** until ported into the core (§5, B9); Mode A played both.
  **Closed 2026-10-26:** B9 is done, so the core is now literally one. The M profiles render from
  `model_rt` through the same call as every other scenario, `build._render_source` no longer forks on
  the source, and `rig_neuromuscular_model.py` is retired. Two things the port added beyond A's
  module: dry bearing friction is off by default (`rig_params()` turns it on), because the rig has
  friction and De Groote's leg does not and a default that quietly differed would make the validated
  reference and the hardware indistinguishable; and SRS stays tone-proportional rather than absolute,
  which is the published form — short-range stiffness comes from bound cross-bridges, so an absolute
  stiffness would let a flaccid limb have a stiff first drop. A's 80 ms reflex delay and 3.0 deg
  critical stretch were NOT carried over: the reference MATLAB's own 50 ms and 1.5 deg win.
- **D2 — Validated scorer only.** Every expected value, every rig run, every report.
- **D3 — Position-controlled playback first (Mode A), on whichever rig exists.** It is model-agnostic
  (it plays a file), works on the horizontal rig today, and A's constraint 1 — short-range stiffness
  is a step no torque source reproduces — makes position control the right primary mode on the
  vertical rig too. Torque mode (Mode C) is for active-torque-only rendering on a real shank.
- **D4 — The encoder is the truth on every run.** The command is a target; delivered parameters are
  scored from the encoder and reported against intended.
- **D5 — R3 blindness.** No mapping from rig setting to grade is ever fitted on instrument output.
  The scenario "MAS ladder" is renamed **PT7-band presets** (P0–P3): they are calibrated to the
  scorer's own bands, which is right for phone-vs-encoder testing and circular for validating grades.
- **D6 — Do not specify MAS 1 vs 1+ as distinct torques** (A, constraint 2). Bands are grades the rig
  can be *assessed* at, not settings it is *specified* to produce.
- **D7 — Rest geometry is an input, measured twice:** the rig's unactuated rest (first rig task) and
  the corpus rest by one defined method (`neutral_deg` from the validated scorer on controls).
  Both models take it as a parameter.
- **D8 — Novelty claim narrowed** to the Wartenberg pendulum *test* emulation and the
  phone-validation use; the programmable pendulum-parameterised knee simulator is prior art
  (Riener & Burgkart, US8108190B2, expired).

## 3. Decisions for the owner

| # | Decision | Options and what each costs |
|---|---|---|
| O1 | Arm range | ±65° full scale (measure mechanical limit) **or** 0.75× amplitude render (A0 ≈ 41°, ratios invariant) |
| O2 | Actuator for Mode C | C's estimate: 0.8–3.7 N·m (P0–P3), 15 severe, 26 clonus. A's: 18 N·m tone ladder, 62 N·m @ 1854 N·m/s for a hard catch. Pei's elbow ladder 0.9–9.8 N·m. → ≥ 20 N·m with a gearbox covers all but A's hard catch |
| O3 | Vertical rig timing | Mode A/B run now on the horizontal rig (sensor channel only); fused-angle validation waits for it |
| O4 | Clinician access | Two blind assessors per profile (A §validation step 2) to calibrate grades |
| O5 | Clasp-knife adaptation | Faithful (traces drift across drops) **or** rest between drops; habituation series exists either way |

## 4. The program

```
OFFLINE (Python, this branch)                         HOST VI (StretchExperiment is not edited)
 model  : neuromech.model_rt (+ A's profiles)  ─┐
 library: neuromech.scenarios  ─────────────────┼─> session plan ──> player/<run>.csv ──> Pendulum Player
 build  : expected params (validated scorer)  ──┘     (blinded run ids,                   writes M1JointAngleDesired
                                                       randomised, reps, peak_deg)        every 10 ms -> RT PID
 check-log: does the log record under the Player?  <── one .bin + the file played (once, before session 1)
 session score: encoder .bin + phone JSONL ──> delivered vs intended, phone vs encoder <── logs named on the run sheet
 ILC / identify: next command + its player file, plant fit  <── chirp run, any run   (ILC = the feedforward)
 Formula Node (Mode B): verified step function, steppable inside the Player
```

## 5. Build list

| # | Item | State |
|---|---|---|
| B1 | Unified branch with A, B, C; all tests green | done (29 + 23 tests, A's selftest, Formula Node) |
| B2 | Re-score A's profiles on the validated scorer | done (§1.1) |
| B3 | One library: A's 10 profiles rendered into the scenario format (group M), PT7-band rename | **done** — 55 scenarios |
| B4 | Session planner: blinded run ids, randomised order, reps, run sheet, LabVIEW files (CSV + raw float64 .dbl) | **done** — `python -m neuromech.session plan` |
| B5 | Session scorer: encoder `.bin` + phone JSONL → delivered vs intended, phone vs encoder, flags | **done** — `python -m neuromech.session score` |
| B6 | End-to-end dry run without hardware | **done** — `python -m neuromech.dryrun`; 9/9 runs in band, phone \|ΔPT7\| median 0.009 |
| B7 | Chirp scenario + `ilc --identify` | **done** — recovers 6.35 Hz / ζ 0.232 / R² 0.999 |
| B8 | LabVIEW for Mode A | **Pendulum Player** host VI (StretchExperiment is not edited): build guide ready, program adapted to it (headerless player files, peak guard, logs paired by run-sheet name, 100 Hz staircase in the dry run, ILC outputs playable, `session check-log` for the logging bench check). Building the VI is a bench task; interactive Mode B′ needs RT access |
| B9 | Port A's tonic-hold, velocity channel, end-stop, capsule into `model_rt` (+ Formula Node) for Mode B | **done** — `coulomb_nm`, `k_v`/`k_l`/`reflex_max_nm`, `catch_and_hold()`, `limb_from_anthropometry()`; Formula Node re-verified at 1.7e-11; `Params()` bit-identical to before; A's module retired |
| B10 | Corpus rest geometry by the validated scorer | **measured** — §5c; the disagreement is between instruments, not estimates |

## 5a. What the dry run established (2026-10-26)

It caught three bugs that would have broken the first bench session, all fixed and now under test:
the sync-pulse search assumed a trace starting at the command (no real log or phone recording does);
whole-record cross-correlation let a large swing outscore the preamble (now normalised, against the
preamble only); and `ilc.locate` found the command 1–2 s off on short fast runs (now minimum-SSD, exact).

Two results that change expectations. **Uncorrected playback already delivers the intended band** on the
identified plant (9/9; median tracking 0.57°); ILC then takes physiological scenarios below 0.1° in three
runs. And **the phone's agreement is bias-limited**: estimating gyro bias only from stillness the phone
can see before release (what the app does) leaves ~0.01–0.02 °/s, which through the settled tail and
area ratio moves PT7 by up to ~0.15 — one band at bands 2–3. Recommendation for the app: re-estimate bias
at the settled tail before scoring (the settle feature already detects it).

## 5c. Where real legs rest (B10, 2026-10-26)

`python -m neuromech.corpus_rest`: every phone trial (206) replayed and scored by the validated
scorer, production convention; rest depth = pre-release angle − settled neutral, per participant
median, then per cohort. Run on the corpus-safe **abs-fold** extraction (`bench/retest-abs` @ fa3d651,
via `PENDULASTIC_PIPELINE_DIR`); this branch's signed extraction mirrors the rest across 180° on
trials that cross the zero reference (resting "at 250°"), so it is reported for comparison only.

| Cohort | People (trials) | Rest depth | Rest knee (released at 180°) |
|---|---|---|---|
| Controls | 2 (11) | 67.7° (66, 70) | **112.3°** |
| Stroke | 4 (25) | 62.4° (52–69) | 117.6° |
| MS | 8 (124) | 62.5° (45–75) | 117.5° |

Signed extraction (magnitude): controls 71.1° → 108.9°. Detrend default moves each cohort 1–3°.
Only Control_16 and Control_23 have raw phone sensor files; controls 2, 6, 7, 8, 9, 10, 12 were
recorded as video only and cannot enter this measurement.

**The instruments disagree, by about 30°.** OptiTrack A0 on the same trials is 28.8° smaller than
the phone's (median; n = 104; 4 % within 5°), i.e. a rest near 140°, which is where the 142° came
from. It cannot referee: 10 of 104 trials have ≥ 90 % camera coverage and every one of those carries
a poorly-conditioned-hinge warning, which shrinks a projected angle. MediaPipe video scatters too
far to use (control medians 3–62°; Control_16 at 3° against the phone's 66°). The phone's rotation
magnitude is the measurement verified on the bench (0.58°).

So the three rests were never three estimates of one number: phone ≈ 112°, cameras ≈ 140°.
**Decision:** the model default stays at 125° (incline −35°) for now; `Params.incline_deg` sets it
per scenario (`pendulum_sim.thigh_incline_from_resting_deg(112.3)` = −22.3°). **What settles it:** a
goniometer reading of the resting knee after a drop on one or two people, alongside the phone, in
the same session (minutes, not a study). Until then, report A0 by instrument, never pooled.

## 6. Validation, in order

1. **Rig identification** (chirp) → plant, feedforward; refuse if resonance moved > 10 %.
2. **Delivery**: every scenario, encoder-scored, inside its band; ILC where not; tracking RMSE < 1°.
3. **Repeatability**: P2 × 10; between-day ICC ≥ 0.84 (the field's between-session bar, not
   Bohannon's within-session 0.96).
4. **Phone vs encoder** per run on the 7 parameters (+ A0, plateau, F1amp, which carry severity).
5. **Blind clinician grading** (R3, ≥ 2 assessors) → the grade mapping; benchmarks κ 0.63–0.65,
   agreement 76–86 % from the elbow simulators.
6. **Re-run `severity_grading_test.py` on rig-delivered profiles** — the falsification becomes a calibration.
