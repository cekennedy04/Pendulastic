---
title: Instrument Validation via Benchtop Rig - Plan
type: feat
date: 2026-09-04
topic: instrument-validation
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-brainstorm
execution: code
---

# Instrument Validation via Benchtop Rig - Plan

## Goal Capsule

- **Objective.** Establish what the pendulum instrument measures, and over what range it is entitled to speak, by validating it against a benchtop leg with dialled-in stiffness that clinicians assess blind.
- **Product authority.** This plan owns instrument integrity only. The manuscript, the clinical pilot, and the capture-protocol UX work are not active scope; see How This Work Fits Together.
- **Settled.** Clinical claims are reported at two tiers, with the clinician-examined cohort as the only primary path. No interim cohort anchor is derived; the rig supplies the zero point, and until it exists the app reports kinematics without a severity index.

---

## Product Contract

### Summary

Validate the instrument against a physical leg whose stiffness is dialled to span MAS 0 through 3, with clinicians blind-assessing the same rig to supply the stiffness-to-grade mapping. Replace the composite PT score with an amplitude-derived severity index that rises without bound as swing collapses. Refuse trials whose capture conditions invalidate them rather than correcting them.

### Problem Frame

The instrument cannot currently support a between-person claim, and each supporting piece fails for a different reason.

The healthy reference the composite score is measured against cannot be reproduced from the data it is documented as coming from. Its documented basis is four controls, and under leave-one-out the healthy threshold ranges 0.068 to 0.274 against a fixed `PT_HEALTHY_MAX` of 0.1709 — a fourfold spread around the value in production.

The composite score is non-monotonic in severity. Measured on `tests/fixtures/pendulum_sim.py` at dialled tone, PT7 peaks at tone 3 and falls away; a leg at tone 6 reads 0.347 against 0.293 for a leg at tone 1. All seven scored parameters are ratios normalised on the swing itself, so when the swing collapses the ratios renormalise on a small clean motion and a barely-moving leg reads as healthy. The 2026-08-30 excursion gate refuses readings below 25 degrees of excursion, which catches tone 8 and 10 but not tone 6 at 35.4 degrees.

Leave-one-participant-out AUC has sat at or below chance since 2026-08-19, and MAS is constant within a participant, so every held-out fold is single-class.

Underneath all three sits one cause: there is no severity data. Only 10 legs across 5 participants were examined by a clinician, and their grades span MAS 0 to 1. The range where the score is known to invert has never been measured on a human.

### Key Decisions

- KD1. **Validate against a physical rig rather than the human cohort.** The corpus has nothing above MAS 1 and the rig can produce 2 and 3 on demand. (session-settled: user-directed — chosen over re-deriving from the existing 15 scoreable MAS-0 legs: that set cannot cover the range where the instrument fails.) Governs R1, R2, R3, R4, R5.
- KD2. **A0 carries the severity scale, not PT7.** A0 falls monotonically from 78.3 to 11.2 degrees across the full simulated tone range and is not one of the seven scored parameters. (session-settled: user-directed — chosen over banding PT7: no threshold fixes a U-shaped function.) Governs R6.
- KD3. **The index is floored at zero and unbounded above.** One marks the normative boundary; larger means more affected. (session-settled: user-directed — chosen over a clamped 0-to-1 scale: clamping converts an unsupported extreme reading into a confident one.) Governs R6, R9.
- KD4. **PT7 classification stays suppressed.** Re-deriving the reference addresses reproducibility, not non-monotonicity or discrimination. (session-settled: user-directed — chosen over re-enabling provisional bands.) Governs R8.
- KD5. **A trial whose hold drifts is refused, not corrected.** Correcting a hold that cannot be trusted is what injected 185 degrees of drift into a 70-degree curve on MINT_18 Left trial_1. Governs R10.
- KD6. **The IMU is the sole claim path, because HPE-derived PT7 does not reproduce - not because MediaPipe tracks the angle badly.** The decision stands and its stated reason has been narrowed by measurement. MediaPipe recovers knee ANGLE about as well as the phone: on 108 paired trials against clean references, 20.93 degrees against the IMU 22.14, and filling its undetected frames to make a continuous trace costs 0.00 median, so the parity is not an artefact of scoring it only where it succeeds. What fails is the DERIVATION: PT7 from that angle correlates 0.364, and agrees with a clinician exactly on 31.5 percent of the 92 trials where both channels can be graded. The raw curve is fine and the score built on it is not, which is why better tracking is not a route back to a claim path. (session-settled: user-directed.) Governs R12.
- KD7. **Human MAS correlation is reported at two tiers, and only the examined tier carries a clinical claim.** Correlating against a clinical scale requires that a clinician performed the examination; 15 of the 44 legs carry assumed grades and 2 are operator-set. The pooled figure is retained as an exploratory sensitivity analysis with its composition disclosed. (session-settled: user-directed - chosen over reporting the larger pool as a single number.) Governs R14, R17.
- KD8. **No interim cohort anchor is derived. The rig's lowest profile is the zero point.** Restricting to examined grades leaves 4 MAS-0 legs across 3 participants, which cannot carry a dispersion-based boundary; widening the pool to 21 legs reintroduces exactly the assumed grades KD7 excludes. A provisional anchor would be discarded the moment rig data lands, after having rewritten every historical score once. (session-settled: user-directed.) Governs R7, R16.
- KD9. **The rig's passive behaviour is validated against human controls before any active torque is layered on.** Otherwise the rig could mimic tone without mimicking a limb, and the instrument would be validated against the wrong thing. Governs R15.
- KD10. **The excursion gate is re-derived on the corpus before the index is built, not assumed to have survived the grid change.** The gate decides whether a trial is scored at all, so an unverified threshold on A0 puts every downstream severity value on a moving baseline. (session-settled: user-directed - chosen over carrying it as a deferred question.) Governs R18.
- KD11. **The gate floor is not rescaled by a measurement-grid ratio.** 25.0 was never a control-derived bound; the provenance in `pendulastic_pt_score.py` states it is a conservative floor set as high as it could go while clearing the lowest observed spastic leg at 28.7 degrees, with roughly 13 percent of margin. Scaling it by an IMU grid ratio would have cut that margin to 2.4 percent and brought the gate to the edge of refusing a grade on a study case. Governs R18, R19.
- KD12. **The floor holds at 25 degrees; only its justification changes.** Every proposed move is worse than standing still: 28.0 and 26.7 refuse more viable trials, and a floor clearing the new spastic minimum by the documented margin would sit at 8.57 and catch nothing. Refusing a PT7 grade on a collapsed swing is the gate working, not failing, because KD2 puts severity on A0. (session-settled: user-directed - chosen over moving the floor in either direction.) Governs R18, R20.
- KD13. **A grade assessed in one condition is not evidence about another.** Carrying a leg label across pre, post and month-post is what put a 6.76 degree month-post trial forward as the lowest spastic reading when its MAS was assessed at pre; the condition-matched value is 9.85. Tone changes over an intervention, which is the point of measuring it more than once. Governs R21.
- KD15. **87d7645 changed the optical reference SHAPE, not only its datum, so offset-removal does not neutralise it.** Bisected across the loader revisions with the offset removed: flat at 0.00 through 6ddf649, then 8.84 degrees median at 87d7645 and unchanged after. Its own message argues nothing is lost because every scored parameter is invariant to a constant offset and to a mirror - but allowing a sign flip changes nothing on 12 of 12 trials, so this is neither. Any figure calibrated against OptiTrack before 2026-09-01 is not comparable with one calibrated after, including the stored `imu_vs_optitrack_rmse.csv`. Governs R23, R24.
- KD14. **A cross-modality figure must read the offset-removed agreement, not raw RMSE, against a relative reference.** `compare_pair` already handles this: it returns `rmse_deg` and `rmse_deg_offset_removed` side by side, and its comment names the same commit - since 87d7645 the marker loader returns a relative curve and refuses to invent a zero, so raw RMSE mostly reports the missing origin. On MINT_13 Left post trial_4 that is rmse 143.90 with bias 142.93, 99.5 percent of the "error", against 16.69 offset-removed. The comparator is sound; the defect is callers reading the raw field. `batch_imu_vs_optitrack_rmse.py` was already corrected on 2026-09-02. Still raw: `rmse_pipeline_common.py` line 551, which feeds both sweeps, and `pt_report_common.py` line 1614, which reaches the clinician report. Governs R22, R23.
- KD13b. **A cross-modality agreement figure is meaningless without its convention pairing.** Measured over 129 paired trials, detrending moves optical PT7 by 0.050 and phone PT7 by 0.363 - a within-sensor comparison, same curve at two flag values, so it does not depend on any cross-modality alignment. The cross-modality figures it was first reported with (0.447 production against 0.247 convention-matched, and 0.460 for MediaPipe 2D) are PROVISIONAL: they were produced through a hand-rolled comparator rather than compare_pair, and are being re-derived. The decision does not rest on their exact values. Governs R22.

### Requirements

**Severity reference from the rig**

- R1. The rig produces repeatable stiffness profiles spanning at least MAS 0, 1, 1+, 2 and 3, each reproducible from a recorded setting.
- R2. Clinicians assess the rig blind to the dialled setting, with profile order randomised independently per assessor.
- R3. The stiffness-to-grade mapping is derived from those blind assessments alone. Instrument output is never an input to the mapping it will be validated against.
- R4. More than one clinician assesses each profile, and inter-rater agreement is reported alongside the mapping.
- R5. The instrument's reading is compared against the dialled stiffness and against the clinician grade separately, so a disagreement between the two is visible rather than averaged away.
- R15. Before any active spasticity torque is applied, the rig runs unactuated at the passive stiffness and damping taken as the healthy human standard, and its swing envelope is compared against human healthy-control trials. The comparison is reported whatever it shows.

**The severity index**

- R6. The index derives from A0 alone, rises as swing amplitude falls, is floored at zero, and is unbounded above with one marking the normative boundary.
- R7. Both index anchors derive from rig profiles, and each is published with the spread it shows across repeated trials at the same dialled setting.
- R8. The app displays no healthy, borderline or impaired classification derived from PT7.
- R9. A reading beyond the normative boundary is displayed as measured rather than clamped to the boundary.
- R16. Until rig-derived anchors exist, no severity index and no categorical zone is displayed. The app reports excursion and the continuous kinematic parameters as measured.
- R18. The excursion floor holds at 25 degrees and the ceiling at 120. Their derivation is recorded in the source as void, alongside the measured values that void it, rather than left looking valid. The reference statistics are left unchanged for the same reason the floor is: no re-derivation this corpus can support would be truer than the one it replaces. A real derivation waits for the rig.
- R20. Documentation states the dual-variable model plainly: A0 reports severity continuously across the whole range, and PT7 classification applies only above the floor. A refused grade is a suppressed classification, never a missing measurement.
- R19. A0 measured from the IMU and A0 measured from OptiTrack are shown to be on the same scale, or the gate stops applying one constant to both. The 100 Hz move raised IMU A0 while leaving optical A0 untouched, so a trial's verdict currently depends in part on which instrument measured it.

**Capture validity**

- R10. A trial whose pre-release hold drifts is marked invalid and excluded from scoring, with the drift reported.
- R11. Lateral motion is derived from the IMU and plotted per trial.
- R12. Trials are scored from the IMU alone.

**Reporting limits**

- R13. Every claim about the instrument states the MAS range over which it was measured.
- R14. Any correlation against human MAS reports the provenance of those grades, distinguishing clinician examinations from assumed and operator-set values.
- R17. Construct validity against human MAS is claimed only on the clinician-examined cohort. A figure computed over a wider pool is labelled exploratory, and the assumed and operator-set legs it contains are named where it appears.
- R21. A MAS grade is applied only to trials recorded in the condition it was assessed in, or the carry across conditions is stated wherever the grade is used. Of 39 clinician-examined spastic trials, only 9 were recorded in the condition their grade was assessed in.
- R22. Any figure comparing the IMU and optical channels states which detrend convention each side was scored under, and is produced through the production comparator rather than a hand-rolled alignment. The production pairing scores the phone undetrended against optical detrended, and that choice moves PT7 more than the sensor difference does.
- R23. The optical reference curve is shown to be free of discontinuity before any accuracy figure is computed against it, and every caller comparing against it reads the offset-removed agreement. A curve containing a jump no knee can make between consecutive samples is not a reference, and raw RMSE against a curve with no origin is not a measure of agreement.
- R24. Every OptiTrack-calibrated figure states which side of 87d7645 its reference came from. A number measured before it and one measured after are not comparable, whatever the offset handling.

### Key Flows

- F1. Blind rig assessment
  - **Trigger:** A calibration session begins with the rig assembled and a clinician available.
  - **Steps:** The operator dials a profile from the randomised sequence; the clinician assesses the limb and records a grade without sight of the setting; the instrument records a trial on the same profile; the pair is stored against the setting.
  - **Outcome:** One (setting, clinician grade, instrument reading) triple, repeated across profiles and assessors.
  - **Covered by:** R1, R2, R3, R4, R5

- F2. Trial capture with validity gating
  - **Trigger:** A trial is recorded, on the rig or on a participant.
  - **Steps:** The hold is checked for drift; lateral motion is derived and plotted; a trial passing both is scored, and one failing either is retained and marked invalid.
  - **Outcome:** Either a scored trial or a trial preserved with a named reason it was not scored.
  - **Covered by:** R10, R11, R12

### Acceptance Examples

- AE1. **Covers R10.** Given a trial whose pre-release hold drifts, when it is processed, then it is marked invalid and no index value is produced for it, and the drift magnitude is recorded.
- AE2. **Covers R6, R9.** Given a leg whose amplitude falls below the normative boundary, when the index is computed, then it reports a value above one rather than clamping, and the value rises as amplitude falls further.
- AE3. **Covers R6.** Given a leg whose amplitude exceeds the healthy anchor, when the index is computed, then it reports zero rather than a negative value.
- AE4. **Covers R3.** Given a clinician's grade for a profile, when the stiffness-to-grade mapping is built, then no instrument-derived quantity contributes to it.
- AE5. **Covers R14.** Given a reported correlation against human MAS, when it is published, then the count of clinician-examined grades is stated separately from assumed and operator-set ones.
- AE6. **Covers R15.** Given the rig set to its passive profile with no active torque, when a swing is recorded and compared against human healthy-control trials, then the envelope comparison is reported whether or not it matches.
- AE7. **Covers R16.** Given no rig-derived anchor exists, when a trial is scored in the app, then excursion and kinematic parameters are shown and no index value or zone label appears.
- AE8. **Covers R17.** Given a construct-validity claim against human MAS, when it is stated, then it rests on the clinician-examined cohort, and any wider figure alongside it is marked exploratory.
- AE9. **Covers R18.** Given the gate constants, when a reader consults the derivation beside them, then it names which of its own reasoning measurement has since falsified, and the constants are unchanged.
- AE10. **Covers R19.** Given the same physical swing measured by both instruments, when each is scored, then the two A0 values agree within a stated tolerance, or the gate applies a separate constant per modality.
- AE11. **Covers R18.** Given a refused trial, when the operator reads the refusal, then it does not present reference statistics measured on a superseded reconstruction as though they described the current one.
- AE12. **Covers R20.** Given a leg whose swing has collapsed below the floor, when its result is presented, then a severity value derived from A0 is shown and the absence of a PT7 grade is stated as a suppression rather than a failure to measure.
- AE13. **Covers R21.** Given a MAS grade assessed in one condition and a trial recorded in another, when the pair is used in any reported figure, then the carry is stated rather than presented as a matched observation.
- AE14. **Covers R23.** Given an optical reference curve, when it is used as ground truth, then it contains no sample-to-sample jump beyond what the joint can physically move at the capture rate, or it is excluded from the comparison and reported.
- AE15. **Covers R24.** Given two accuracy figures calibrated against OptiTrack, when they are compared, then either both references postdate 87d7645 or the comparison is refused.

### Scope Boundaries

**Deferred for later**

- Distinguishing severity above the boundary into grades. The index reports position, not a grade.
- Capture-protocol UX: the stop control, visual and audio level-and-hold feedback, and a maximum trial duration.
- Clonus and spasm detection as a product feature.
- Rig construction detail, including gearbox, pulley and printed components.

**Outside this plan**

- The conference submission and the manuscript.
- The clinical pilot and its protocol.
- The PhD proposal on neuromechanical modelling.

### Dependencies and Assumptions

- The rig must exist, be repeatable, and hold a dialled setting across a session. Nothing in this plan validates the instrument without it.
- Clinician time is required for R2 and R4, and the mapping's strength is bounded by how many assessors are available.
- The rig's stiffness range is assumed to map onto human MAS closely enough for the mapping to transfer. R15 tests the passive end of that assumption directly; the actuated end remains assumed, because no human above MAS 1 has been measured to compare against.
- Assumed: the corrected flex-axis reconstruction is the one all rig data is captured under. Amplitude moved 1.52 times its previous median when that changed, so data spanning both conventions would not be comparable.
- Assumed: rig data is captured on the 100 Hz analysis grid. Scoring moved off the 20 Hz display poll on 2026-09-04, which changed A0's filtered scale, so this is a second convention boundary that rig data must sit wholly on one side of.

### Outstanding Questions

**Deferred to planning**

- The passive stiffness and damping the rig is set to for R15. Supervisor notes cite 0.5 Nm/rad and 0.5 Nms/rad from the Popovic and Stein pendulum literature; neither constant appears anywhere in this repository, so both need confirming against the primary source before the rig is dialled to them.
- Which human control trials R15 compares against, given that the same provenance rule that produced KD8 leaves 4 examined MAS-0 legs across 3 participants.
- The drift threshold that makes a hold invalid, and whether it is expressed as degrees, degrees per second, or a fraction of the swing.
- The lateral-motion threshold, and whether exceeding it invalidates a trial or only annotates it.
- How many assessors and repetitions the mapping needs to be worth reporting.

### Sources and Research

- `docs/reports/2026-08-30-pt7-non-monotonic-and-the-excursion-gate.md` — the tone-by-tone table showing PT7 peaking at tone 3, A0 monotonic across the range, and the statement that no choice of thresholds fixes a U-shaped function.
- `docs/reports/2026-08-19-full-project-analysis-vs-mas.md` — leave-one-participant-out AUC at or below chance.
- `Model_Analysis_Outputs/Spasticity_Stratified/leg_labels.csv` — 44 legs across 22 participants, by provenance: 10 clinician-examined across 5 participants, 15 assumed, 14 withheld as a0-derived, 3 control-by-recruitment, 2 operator-set. Of the 21 legs carrying MAS 0, only 4 are clinician-examined, across 3 participants.
- `mobile-imu-core/src/pt_score.rs` — `HEALTHY_REF`, `PT_HEALTHY_MAX`, and the seven scored parameters.
- `tests/fixtures/pendulum_sim.py` — the simulator the non-monotonicity was established on.
- Supervisor notes, 2026-09-04 — the rig, blind clinician assessment, drift-during-hold invalidity, lateral-motion hinge lock, and IMU-only direction.
- Coverage-matched channel comparison, 2026-09-06, 108 paired trials on strict-clean references. MediaPipe scored with gaps skipped 20.93 degrees, with gaps filled 21.44, against the phone IMU at 22.14; the IMU is better on 45 percent of trials either way. The detection-rate bands invert - below 50 percent detection the IMU leads 22.33 to 25.60, above 80 percent MediaPipe leads 22.83 to 28.98 - but those bands are different sessions and participants rather than the same trials under different conditions, so the inversion is between-trial and must not be read as good video making the IMU worse.
- `docs/reports/2026-09-04-mediapipe-coverage-and-accuracy.md`, with per-trial data in `Model_Analysis_Outputs/HPE_Comparison/mediapipe_pt_metrics.csv` - the corpus-wide HPE measurement behind KD6 and R12. On 284 trials the excursion gate passes 166 for the MediaPipe 2D angle and 156 for OptiTrack; where both are gradeable, n=92, exact MAS agreement is 31.5 percent and adjacent 44.6 percent, with PT7 at r=0.364. Those agreement figures are restricted to trials both instruments can grade, which is stricter than the 83.3 percent in `hpe_accuracy_summary.csv`, computed on a different subset without the gate. The same pass tested the world-angle column corpus-wide for the first time, n=178: PT7 r=0.236 against 0.364 for the 2D angle, so world landmarks are not a rescue. Gate pass rate by detection-rate bucket is non-monotonic - 24 percent below 25 percent detection, then 74, 59 and 67 - so the failure is in the derivation, not the tracking.
- Grid re-measurement, 2026-09-04, 130 trials replayed at both grids on the production path. A0 rose: median 50.02 to 56.46 degrees, ratio 1.1185, p10 1.0363, p90 1.2346, down on only 7 of 130. One trial changed side of the gate, from refused to scored; none went the other way. The lowest new A0 among trials the old floor passed is 31.58, so a floor of 28.0 refuses the same three trials that 25.0 does. Ceiling headroom fell rather than grew: max A0 79.8 to 91.5 against the 120 ceiling, p99 85.5, nothing above 100.
- CAVEAT ON EVERY OPTICAL NUMBER BELOW, added 2026-09-06: they were measured after 87d7645, which changed the reference curve shape by a median 8.84 degrees (KD15). They are internally consistent and are the right basis for decisions taken now, but they are not comparable with anything calibrated before 2026-09-01, and the 9.85 spastic minimum in particular has not been re-derived on the pre-87d7645 reconstruction.
- Gate re-derivation, 2026-09-04, 124 kept optical trials on the scorer own path, cross-checked against an independent 253-trial pass by a parallel session that reached the same trial by a different route. Both bounds in the derivation block are void. The floor is documented as clearing the lowest observed spastic leg at 28.7 degrees by roughly 13 percent; the lowest clinician-examined spastic trial whose MAS was assessed in the same condition as the trial is 9.85 (14_left_pre_T4), and a floor keeping the documented margin below it would sit at 8.57. The ceiling comment claims 89.8 as the largest value ever measured with ~34 percent of headroom; the largest is 141.21 (16_left_control_T4), above the 120 ceiling, though p99 is 63.88 so the ceiling is catching one apparent reconstruction failure rather than routinely refusing real swings. Under the filter the shipped constants name, the non-spastic reference is mean 31.0, sd 9.9, n 18 against the shipped 46.6, 11.1, 53. The workbench and scorer loaders were checked against each other and agree exactly: all 137 trials take marker_pca and the 124 paired A0 values are identical to zero decimal places.

---


---

## Planning Contract

### Approach

The work splits along a line the codebase already draws but does not enforce: `mas_estimate()` is the gated entry point and `pt_to_mas()` is the raw mapping beneath it, and the module's own docstring says "Prefer this over calling `pt_to_mas(compute_pt_score(params))` directly." Fourteen call sites across eight modules do exactly that. U1 makes the preference a rule where a grade reaches a person, and leaves it a choice where the caller is doing analysis.

Everything downstream depends on the two languages agreeing on four constants that are currently independent literals with no test between them, so the parity test in U2 comes before any constant is touched in U3.

The rig-dependent requirements (R1-R5, R15) are not codeable until hardware exists and are carried as plan scope without units.

### Product Contract preservation

Product Contract restructured, no scope change. R18 was rewritten in place after measurement invalidated its original instruction, and R19, R20 were split out of it as their own concerns; KD11 and KD12 record why. No requirement was weakened or dropped.

---

## Implementation Units

### U1. One gate in front of every rendered grade

- **Goal.** No path that shows a MAS grade to a person can bypass the excursion gate, and no module carries its own copy of the zone thresholds.
- **Requirements.** R8, R18, R20. Instantiates KD4 and KD12.
- **Dependencies.** None.
- **Files.**
  - `pendulastic_viewer.py` - delete the fallback `PT_HEALTHY_MAX = 0.09` / `PT_BORDERLINE_MAX = 0.20` at lines 77-78; route the grade at line 4275 and the comparison table at lines 8806-8807 through `mas_estimate`
  - `pendulastic_workbench.py` - line 668, the live metrics panel
  - `web/api/services/pt_score_bridge.py` - `compute()`, whose response field is already named `mas_estimate`
  - `web/api/models/trial.py` line 92 - `mas_estimate: float`, annotated "integer MAS value returned by pt_to_mas()". `pt_to_mas` returns `str`, and one of its labels is `"1+"`, which is not coercible to a float at all
  - `pendulastic_pt_score.py` - the gated helper the callers move onto
  - `tests/test_pt_score.py`, `tests/test_pendulastic_workbench.py`
  - `tests/test_pt_score_bridge.py` (new - no test file covers this module today)
- **Approach.**
  0. One site is a defect rather than a gating gap and is fixed as such: the API response model types a string label as a float, so a trial grading "1+" fails validation. `pt_report_common`'s no-A0 branch looks like a second one and is not - two tests pin it deliberately, and its docstring says why: absent evidence is not evidence. `excursion_ok`'s "a missing A0 counts as NOT ok" is about a trial whose A0 failed to compute; that branch is about a source that never carries A0. Leave it.
    1. Separate the two populations of caller. A grade rendered to a person must be gated. A grade computed for analysis - `fit_mas_thresholds.py`, `mas_validation.py`, `hpe_mas_evaluation.py`, `model_vs_optitrack_eval.py`, `p13_leg_session_comparison.py` - must not be silently gated, because gating changes which trials enter a correlation and that interacts with R14 and R17. Leave the analysis callers alone in this unit and record the question in Open Questions.
  2. Move the three viewer/workbench sites onto `mas_estimate`, rendering the returned `reason` where the grade used to appear rather than a blank or a dash.
  3. Fix `pt_score_bridge.compute()`. Its response field is `mas_estimate` but it calls `pt_to_mas` - the contract already claims the gated function's output, so this is a correctness fix, not a behaviour change to negotiate.
  4. Delete the viewer's fallback constants. They are only live when the import fails, but lines 2753, 2754, 2879 and 2880 use them with no `_PT_AVAIL` guard, so a failed import silently redraws the gauge boundaries and prints a legend claiming healthy means PT <= 0.09. If the import fails the viewer cannot score anything, so the honest behaviour is to draw no zones at all.
- **Patterns to follow.** `pendulastic_app.py:1806-1807` is the one call site that already does this correctly, including the comment explaining the `mas=None` contract. `mediapipe_pt_metrics.py:195` is a second, newer example.
- **Test scenarios.**
  - A trial whose A0 is below the floor renders the refusal reason, not a grade, in the viewer's score label.
  - A trial whose A0 is below the floor renders no grade in the comparison table, and the cell says why rather than showing an em dash indistinguishable from missing data.
  - `pt_score_bridge.compute()` on a collapsed-swing trial returns no grade in `mas_estimate` and carries the refusal reason.
  - `pt_score_bridge.compute()` on a normal trial returns the same grade it returned before, so the fix is proven not to move interpretable results.
  - A trial above the ceiling is refused by the same path as one below the floor.
  - A trial grading "1+" survives the API response model rather than failing validation on a type that cannot hold it.
  - Importing `pendulastic_viewer` with `pendulastic_pt_score` unavailable does not define zone thresholds, and the gauge draws no zone bands.
  - No module other than `pendulastic_pt_score` defines `PT_HEALTHY_MAX` or `PT_BORDERLINE_MAX` - asserted by grep in the test, so a future copy fails.
- **Verification.** Every user-facing grade path shows a refusal where the gate refuses. The analysis callers are untouched and their outputs are byte-identical.

### U2. Pin the four constants across the language boundary

- **Goal.** Python and Rust cannot silently disagree about the gate or the zones.
- **Requirements.** R18, R19.
- **Dependencies.** None. Must land before U3.
- **Files.** `mobile-imu-core/tests/fixtures/gen_fixtures.py`, `mobile-imu-core/tests/fixtures/golden.rs`, `mobile-imu-core/tests/pt_score_test.rs`
- **Approach.** The golden-fixture mechanism already generates Rust constants from the live Python module for numeric parity, but emits none of `MIN_INTERPRETABLE_A0_DEG`, `MAX_INTERPRETABLE_A0_DEG`, `PT_HEALTHY_MAX`, `PT_BORDERLINE_MAX`. Add them to the generator's output and assert equality in the Rust test. The port's own doc comment claims byte-identical parity confirmed manually with `diff` at port time - this converts a one-time claim into a standing check.
- **Execution note.** Write the failing assertion first with a deliberately wrong value, to prove the generator is actually feeding the test rather than the test asserting a Rust literal against itself.
- **Test scenarios.**
  - Each of the four constants matches between the generated fixture and the Rust literal.
  - Changing the Python value without regenerating fails the Rust test rather than passing quietly.
- **Verification.** `cargo test` fails when the two languages disagree.

### U3. Stop the refusal message asserting a superseded reference

- **Goal.** A clinician reading a refusal is not told something measurement has shown to be false.
- **Requirements.** R18. Instantiates KD12.
- **Dependencies.** U2.
- **Files.** `pendulastic_pt_score.py` (`INSUFFICIENT_EXCURSION` and the constants it formats), `mobile-imu-core/src/pt_score.rs`, `tests/test_pt_score.py`
- **Approach.** The comment half of this landed in b995cfa: both derivations now record what measurement falsified, and no value moved. What remains is the message itself. `INSUFFICIENT_EXCURSION` renders "control mean 46.6, sd 11.1, n=53" as a plain statement of fact, and those statistics describe a reconstruction that no longer exists - the same filter now yields 31.0, 9.9 and n=18. Under KD12 the numbers are not updated, because a re-derivation this corpus can support is not truer than the one it replaces. So the clause either goes, or it says which corpus it came from. Dropping it is the smaller change and loses little: the sentence's work is done by the floor and the measured A0, not by a control mean the reader cannot act on.
- **Execution note.** This changes text a clinician reads. Decide between dropping and dating the clause before writing either, rather than picking in the edit.
- **Test scenarios.**
  - A refusal message states the floor and the measured excursion without asserting a control statistic as current.
  - The refusal still distinguishes a swing below the floor from one above the ceiling.
  - The floor is unchanged at 25.0 and the ceiling at 120, asserted explicitly so a future edit has to be deliberate.
- **Verification.** Nothing in the refusal claims to describe the current corpus unless it does.

### U4a. Normalise curve polarity before any cross-modality comparison

- **Goal.** A cross-modality comparison aligns two curves, not a curve and its mirror image.
- **Requirements.** R19, R22.
- **Dependencies.** None. Blocks U4.
- **Files.** `workbench_engine.py` (`compare_pair`), `tests/test_workbench_engine.py`
- **Approach.** Since 87d7645 the optical angle is relative and its sign is effectively arbitrary per trial: across 60 trials OptiTrack runs 26 negative and 34 positive, MediaPipe 38 and 22, the IMU 59 and 1, so OptiTrack disagrees with MediaPipe on 26 of 60 and with the IMU on 25 of 60. `windowed_pt_params` normalises this at line 426 with `inverted = A0 < 0.0`; `compare_pair` does not handle it at all. Normalise both sides to the same release direction before aligning, mirroring the rule `windowed_pt_params` already applies rather than inventing a second one. This is a real inconsistency to close, NOT an explanation for any observed error: normalising was measured against the cross-modality RMSE and moved MediaPipe 142.4 to 141.0 and the IMU 149.3 to 163.3, so it explains none of it.
- **Test scenarios.**
  - A curve and its exact negation compare as identical after normalisation, not as maximally different.
  - Two genuinely different curves of the same polarity are unaffected.
  - A curve with no detectable release direction is refused rather than assigned one.
  - The normalisation matches `windowed_pt_params`, so the two cannot disagree about which way up a trial is.
- **Verification.** The two functions agree about which way up a trial is.

### U4. Establish whether the two instruments are on one scale

- **Goal.** Settle whether one gate constant can serve both modalities.
- **Requirements.** R19. Instantiates KD11.
- **Dependencies.** U2.
- **Files.** `gate_regrid.py`, `tests/test_pt_score.py`, `docs/reports/` (a new measurement report)
- **Approach.** The 100 Hz move raised IMU A0 by 7-12 percent depending on method and left optical A0 untouched, and `mas_estimate` applies one floor to both. Measure paired trials that have IMU and OptiTrack for the same swing and report the agreement. The plan does not presume the outcome: if the two agree within a stated tolerance, one constant is defensible and this unit ends with the evidence recorded; if they do not, the gate takes a per-modality constant.
- **Execution note.** This is a measurement whose result determines the code change. Do not write the per-modality branch before the measurement says it is needed.
- **Test scenarios.**
  - Paired IMU and optical A0 for the same trial are compared and the spread reported.
  - If a per-modality constant is introduced, a trial scored from each modality is gated by its own constant.
- **Verification.** A recorded answer either way, with the evidence.

### U5. Refuse a drifting hold rather than correcting it

- **Goal.** A trial whose pre-release hold cannot be trusted is marked invalid and keeps its drift on the record.
- **Requirements.** R10. Instantiates KD5.
- **Dependencies.** None.
- **Files.** `pendulastic_pt_score.py` (`_baseline_drift_slope`, `_credible_drift_slope`, `compute_pt_params`), `pt_report_common.py` (the quality-tag registry), `tests/test_drift_correction_bounds.py`, `tests/test_pt_report_common.py`
- **Approach.** Today both estimators return `None` on an untrustworthy hold, which means no correction is applied and the trial scores anyway - it is never flagged. The change is to record the refusal rather than swallow it. Follow the project's standing rule that code flags and never drops: the trial gains a quality tag carrying the measured drift, and only `excluded_trials.json` removes anything. The `calibration_hold` category already exists in `QUALITY_TAG_CATEGORIES`.
- **Test scenarios.**
  - A hold that drifts beyond the threshold produces a quality tag naming the measured drift.
  - The trial remains discoverable and is not removed from `discover_trials()`.
  - A clean hold produces no tag.
  - A trial with no usable hold at all is distinguished from one whose hold drifted.
- **Verification.** Drift becomes visible evidence rather than a silent non-correction.

### U6. Derive and plot lateral motion

- **Goal.** Out-of-plane motion is visible per trial.
- **Requirements.** R11.
- **Dependencies.** None.
- **Files.** `plot_multisource_trials.py` (`make_trial_figure`), `pendulastic_pt_score.py`, `tests/` (new)
- **Approach.** The signal already exists and is discarded. The IMU writes `hip_roll_deg` into every trial CSV and nothing downstream reads it; OptiTrack derives `pc2_series`, the out-of-hinge-plane rotation, inside `optitrack_knee_axis.hinge_axis()` and throws it away after `low_freq_ratio()` reduces it to a scalar. Surface both: return `pc2_series` rather than discarding it, and add a lateral-motion row to the existing two-row gridspec in `make_trial_figure`.
- **Test scenarios.**
  - A trial with known out-of-plane motion produces a non-flat lateral trace.
  - A trial with none produces a flat one.
  - A trial missing the roll column plots the remaining rows rather than failing.
- **Verification.** Every per-trial figure shows what the limb did outside the swing plane.

### U7. The A0 severity index, built but not shown

- **Goal.** The index exists, is tested against its anchors, and displays nothing until rig data supplies them.
- **Requirements.** R6, R7, R9, R16. Instantiates KD2, KD3, KD8.
- **Dependencies.** U3.
- **Files.** `pendulastic_pt_score.py`, `mobile-imu-core/src/pt_score.rs`, `webapp/src/app.js`, `tests/test_pt_score.py`, `mobile-imu-core` inline tests, `webapp/tests/app.test.js`
- **Approach.** A linear map from A0 onto the index with two injected anchors: the amplitude scoring zero and the amplitude scoring one. Floored at zero, unbounded above. The anchors are parameters with no default - absent anchors mean the index is unavailable, which is R16's state until the rig exists, rather than a silently-defaulted number. Follow the suppression pattern already in `webapp/src/app.js:298`, where `ZONE_CLASSIFICATION_CALIBRATED = false` withholds the PT7 band with its reasoning recorded beside it.
- **Test scenarios.**
  - Amplitude at the healthy anchor scores exactly zero.
  - Amplitude above the healthy anchor scores zero, not a negative number.
  - Amplitude at the boundary anchor scores exactly one.
  - Amplitude below the boundary anchor scores above one and is not clamped.
  - The index falls monotonically as amplitude rises across the full range.
  - With no anchors configured, no index value and no zone label is produced.
  - The webapp renders excursion and kinematic parameters while the index is unavailable.
- **Verification.** The index is correct and invisible, and becomes visible only when anchors arrive.

### U8. Report MAS correlation at two tiers

- **Goal.** A construct-validity claim rests on examined grades, and any wider figure is labelled.
- **Requirements.** R13, R14, R17, R21. Instantiates KD7 and KD13.
- **Dependencies.** None.
- **Files.** `mas_validation.py`, `src/pendulastic/stats.py`, `spasticity_grouping.py`, `tests/test_mas_validation.py`, `tests/test_spasticity_grouping.py`
- **Approach.** The provenance already exists in `spasticity_grouping.py` as `SRC_CLINICAL`, `SRC_MAS_ASSUMED`, `SRC_MAS_OPERATOR` and the rest, and `CLINICIAN_ASSESSED_SOURCES` already names the examined set. The correlation path does not use any of it - `mas_validation.py` and `src/pendulastic/stats.py` read `mas_scores.csv` directly and know nothing about how a grade was obtained. Join the provenance in and report both tiers with their counts, the examined tier as the claim.
- **Test scenarios.**
  - A correlation over a mixed population reports the examined-only figure and the pooled figure with distinct counts.
  - The pooled figure carries its composition, naming the assumed and operator-set legs.
  - A population with no examined grades reports no primary claim rather than falling back to the pooled figure.
  - A grade assessed in one condition and applied to a trial from another is reported as a carry, not a matched observation.
- **Verification.** No reported correlation is ambiguous about which grades produced it.

### U9. State the dual-variable model where it is read

- **Goal.** A refused grade reads as a suppressed classification, not a failed measurement.
- **Requirements.** R12, R20, R22. Instantiates KD6, KD12 and KD13b. Instantiates KD12.
- **Dependencies.** U1, U7.
- **Files.** `pendulastic_pt_score.py` (the `INSUFFICIENT_EXCURSION` template), `webapp/index.html`, `docs/`
- **Approach.** The refusal message currently explains that PT7 cannot be interpreted and advises repeating the trial. That is right for a positioning failure and wrong for a genuinely collapsed limb, which is a measurement the instrument made correctly. Distinguish the two, and say that A0 continues to carry severity where PT7 does not.
- **Test scenarios.**
  - A refusal names A0 as the severity variable rather than implying nothing was measured.
  - The HPE negative result is recorded where a reader would look for it, with its measured values, rather than left as an absence.
  - A published IMU-versus-optical agreement figure names the detrend convention on each side.
  - The existing advice about positioning and release survives for the cases it fits.
- **Verification.** A clinician reading a refusal knows whether to repeat the trial.

---

## Verification Contract

- `cargo test --manifest-path mobile-imu-core/Cargo.toml` - includes the new cross-language parity assertions from U2.
- `cargo clippy --manifest-path mobile-imu-core/Cargo.toml --all-targets` - the crate is kept at zero warnings deliberately; a32c9f2 removed the first one it had picked up.
- `pytest tests/test_pt_score.py tests/test_drift_correction_bounds.py tests/test_pt_report_common.py tests/test_mas_validation.py tests/test_spasticity_grouping.py`
- `npm test` in `webapp/`, against the discovery floor rather than the exact count.
- `pytest tests/test_web_export_contract.py -q` - the only Python file CI gates today.

CI runs one Python test file. Every Python change in this plan lands outside that gate, so the Python suite must be run locally and the result stated in the change that makes it.

## Definition of Done

- No path that renders a MAS grade to a person bypasses the excursion gate, and only `pendulastic_pt_score` defines the zone thresholds.
- The four shared constants are asserted equal across Python and Rust by a test that fails when one side moves.
- The refusal message quotes statistics measured on the current reconstruction under the filter it names, and the floor is still 25.0.
- Whether one gate constant can serve both modalities is answered with evidence, either way.
- A drifting hold produces a quality tag carrying its drift, and removes no trial.
- Every per-trial figure carries a lateral-motion trace.
- The severity index is implemented, tested against both anchors, and produces nothing while anchors are absent.
- Every reported MAS correlation names the provenance of the grades behind it.

<!-- ce-section: work-relationships -->
## How This Work Fits Together

This plan owns instrument integrity: what the instrument measures and over what range it may claim to. The breakdown below is the current understanding, not a committed roadmap.

- Manuscript and conference submission
  - Depends on this plan for the range and provenance limits it must report.
  - Still to decide: whether the rig calibration is a result in the paper or a methods section.
- Clinical pilot at a partner site
  - Depends on this plan for a defensible index and on the capture-protocol work for a usable session.
  - Can proceed independently of the manuscript.
- Capture-protocol UX
  - Shares the validity gates in R10 and R11; the gates are here, the operator-facing feedback is there.
  - Still to decide: whether a maximum trial duration is a validity gate or a usability one.
- Neuromechanical modelling and the PhD proposal
  - Enabled by the rig this plan depends on, and by the stiffness-to-grade mapping it produces.
