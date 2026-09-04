---
title: Instrument Validation via Benchtop Rig - Plan
type: feat
date: 2026-09-04
topic: instrument-validation
artifact_contract: ce-unified-plan/v1
artifact_readiness: requirements-only
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
- KD6. **The IMU is the sole claim path.** MediaPipe detection fails on a reclined participant and HPE-derived PT7 does not reproduce; it is reported as a negative result rather than used. (session-settled: user-directed.) Governs R12.
- KD7. **Human MAS correlation is reported at two tiers, and only the examined tier carries a clinical claim.** Correlating against a clinical scale requires that a clinician performed the examination; 15 of the 44 legs carry assumed grades and 2 are operator-set. The pooled figure is retained as an exploratory sensitivity analysis with its composition disclosed. (session-settled: user-directed - chosen over reporting the larger pool as a single number.) Governs R14, R17.
- KD8. **No interim cohort anchor is derived. The rig's lowest profile is the zero point.** Restricting to examined grades leaves 4 MAS-0 legs across 3 participants, which cannot carry a dispersion-based boundary; widening the pool to 21 legs reintroduces exactly the assumed grades KD7 excludes. A provisional anchor would be discarded the moment rig data lands, after having rewritten every historical score once. (session-settled: user-directed.) Governs R7, R16.
- KD9. **The rig's passive behaviour is validated against human controls before any active torque is layered on.** Otherwise the rig could mimic tone without mimicking a limb, and the instrument would be validated against the wrong thing. Governs R15.
- KD10. **The excursion gate is re-derived on the corpus before the index is built, not assumed to have survived the grid change.** The gate decides whether a trial is scored at all, so an unverified threshold on A0 puts every downstream severity value on a moving baseline. (session-settled: user-directed - chosen over carrying it as a deferred question.) Governs R18.

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
- R18. The excursion gate threshold is re-derived against the 100 Hz analysis grid, measured across the trial corpus rather than synthetic fixtures, before R6 computes an index or R16 is lifted. The count of trials that change side is reported.

**Capture validity**

- R10. A trial whose pre-release hold drifts is marked invalid and excluded from scoring, with the drift reported.
- R11. Lateral motion is derived from the IMU and plotted per trial.
- R12. Trials are scored from the IMU alone.

**Reporting limits**

- R13. Every claim about the instrument states the MAS range over which it was measured.
- R14. Any correlation against human MAS reports the provenance of those grades, distinguishing clinician examinations from assumed and operator-set values.
- R17. Construct validity against human MAS is claimed only on the clinician-examined cohort. A figure computed over a wider pool is labelled exploratory, and the assumed and operator-set legs it contains are named where it appears.

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
- AE9. **Covers R18.** Given the gate re-derived on the 100 Hz grid, when it is applied to the corpus, then the number of trials moving from scored to refused, and from refused to scored, is stated.

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

---

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
