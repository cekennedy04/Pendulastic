# Pendulastic: Full Project Analysis, Evaluated Against One Question

**Can this system produce a spasticity diagnostic that is more accurate than
the Modified Ashworth Scale (MAS)?**

This document synthesizes every workstream from this investigation
(2026-08-17 through 2026-08-19) against that single question, section by
section. It is not the paper — it is the honest internal accounting the
paper's claims need to be built on. Where a section moves the project closer
to "yes," it says so. Where it doesn't, it says that too.

**Headline verdict, stated up front:** not yet — and the reason isn't a
missing engineering trick, it's that the project doesn't yet have enough
independent participants for any accuracy claim (single-metric or combined)
to survive an honest generalization test. Every section below explains a
piece of why, and what closes the gap.

---

## 0. Why MAS is a low bar, and what beating it actually requires

This matters because it sets what "more accurate" has to mean. MAS is a
6-point ordinal scale (0, 1, 1+, 2, 3, 4) scored by manual passive stretch,
and its own literature is candid about its limits: "the MAS remains the most
widely used clinical spasticity assessment tool" despite "subjectivity and
variations in interrater reliability among health-care professionals" (Yeh
et al., 2025). It reflects a clinician's felt resistance, not a measured
kinematic quantity, and its 6 levels can't resolve continuous change the way
a swing-angle trajectory in principle can.

That's the opportunity — a continuous, physically-grounded measurement
*could* out-resolve a 6-point subjective scale. But "could in principle"
is not the same as "does, on this data, today." Beating MAS requires:

1. **The measurement itself has to be accurate** (agreement with a true
   kinematic reference) — Sections 1-3.
2. **It has to actually track the thing MAS is trying to measure**
   (correlate with real clinical grades, in the right direction, across
   more than one modality) — Sections 4-5.
3. **It has to generalize to a person the model has never seen** — this is
   the actual bar "more accurate than MAS" implies, and it's the one this
   project has not cleared yet — Section 6.

---

## 1. Instrument validation: does the system measure the true knee angle?

**Method:** IMU (smartphone, shank-mounted, Madgwick AHRS fusion) and
MediaPipe (markerless video pose estimation) each compared against OptiTrack
optical motion capture as ground truth, across real recorded trials.

**Findings:**

| | Trajectory RMSE (mean/median) | ICC(2,1) range across 7 PT parameters |
|---|---|---|
| IMU | 14.84° / 10.98° | 0.014 – 0.458 (poor–fair) |
| MediaPipe | 36.0° / 33.3° | −0.115 – 0.032 (no measurable agreement) |

**Evaluated against the MAS-replacement goal:** IMU is the only modality
worth pursuing further — MediaPipe's agreement with ground truth is
statistically indistinguishable from noise on every one of the 7 metrics
tested, so it cannot currently contribute to a diagnostic on its own. IMU's
agreement is real but weak (no ICC clears even the "moderate" 0.5 threshold).
14.84° trajectory RMSE is not accurate enough, alone, to trust a raw angle
reading as a diagnostic input — the next two sections explain most of why,
and both point at fixable-in-principle problems rather than a fundamental
IMU-hardware ceiling.

---

## 2. Methodology search: is there a better algorithm already available?

Four alternatives to the current AHRS pipeline were tested against real
OptiTrack ground truth (not a self-consistency heuristic) before concluding
"tune what's already here":

| Approach | Result | Verdict |
|---|---|---|
| Re-tune existing AHRS filter (beta/ema_alpha grid search, 144 combos) | 16.83° → **14.84° mean** (real, ~12% reduction) | **Adopted** — now the live config |
| Ockendon single-segment tibial-inclination model | Best case 28.5° (physiologically real ft_ratio range) | Rejected — a real bug was found and fixed in it (Euler-pitch gimbal lock cut its error in half), but even fixed it never beats the existing method |
| Magnetometer fusion (9-axis, re-enabled) | 14.89° vs. 14.84° — a wash | Rejected — confirms the 2026-08-10 decision to disable it wasn't costing accuracy |
| MediaPipe/vision as primary modality | 36.0° mean, worse on every metric | Rejected — see Section 1 |

**Evaluated against the MAS-replacement goal:** this section's main
contribution is negative-but-valuable — it closes off "try a different
formula" as the path to a MAS-beating diagnostic. The one real, adopted
improvement (re-tuned config) is a genuine step forward, but a 12% RMSE
reduction on an already-weak signal doesn't change the section-1 verdict.
The bigger lever, found next, is where most of the error actually comes
from.

---

## 3. Where the error actually comes from (bias investigation)

**Finding:** RMSE decomposes exactly as `RMSE² = bias² + residual_std²`.
Applied to the 53-trial corpus: **a roughly constant per-trial offset
explains 67.6% (mean) / 76.0% (median) of the total RMSE.** Strip it out,
and the genuine tracking scatter drops to 9.71° mean / 7.37° median — close
to the field-typical 1.3–11.2° range independent literature reports for
real (non-bench) human-subject IMU validation.

**This is the single most important finding in the whole investigation**:
most of what looks like "the IMU can't track spasticity accurately" is
actually "the IMU's zero-reference is inconsistently wrong," a
calibration/protocol problem, not a fundamental sensing-accuracy problem.

**What was tried to fix it, and why none of it worked yet:**
- Live-vs-offline calibration comparison — **blocked**, not falsified: the
  historical dataset (recorded with an older tool) never saved a live
  fused angle to compare against.
- Release-anchored re-zeroing (re-zero IMU and OptiTrack at their own
  independently-detected release instants) — real but small: RMSE roughly
  flat, but the number of trials under the 5° clinical goal tripled (2→6).
- Chasing a generalizable "bad zero-capture" signature across specific
  outlier trials — **did not generalize**. The one deeply-investigated
  outlier turned out to be a one-off data/mounting problem (the phone
  simply wasn't moving during the true drop), not an algorithmic bug, and
  "how late is the first real gyro motion" doesn't distinguish good trials
  from bad ones elsewhere in the dataset.

**Evaluated against the MAS-replacement goal:** this is the finding that
should shape the recruitment/data-collection roadmap. It says the ceiling
isn't ~15°, it's closer to ~7-10° once the bias is controlled — genuinely
competitive with the field. But "controlled" here almost certainly means
protocol/mounting fixes validated with a controlled recording session, not
another software patch — an unresolved lead, not a dead end.

---

## 4. Clinical correlation: does it track MAS at all?

**Finding:** IMU-derived relaxation index (R2n) correlates with MAS grade
in the expected direction — Spearman's ρ = −0.313, p = 0.014 (n = 61
trials, all 5 IMU-validated participants). Real and statistically
significant, but modest: the closest comparable published study (Yeh et
al., 2025, pose-estimation pendulum test in stroke) reports ρ = −0.75 to
−0.78 for an equivalent ratio parameter — with a much larger, single-cohort
sample (n=20 stroke participants) than this project's current n=5 across
multiple diagnoses.

**Evaluated against the MAS-replacement goal:** proof the signal exists,
not proof it's strong enough to replace anything yet. A correlation this
size, on this little data, is consistent with a real underlying
relationship that a larger, less-biased sample would sharpen — but it's
also consistent with a relationship this project can't yet distinguish from
noise at high confidence.

---

## 5. Group and treatment sensitivity: MS vs. control, pre vs. post

**MS vs. Control:** mixed-effects model (participant as random effect, to
avoid pseudo-replication across each participant's multiple trials)
estimated MS participants' R2n as 0.17 lower than control — the expected
direction — but not statistically significant (p = 0.404). **Not
interpretable as a null finding**: the control arm is n = 1 participant.
This is a data-availability problem, not a signal-strength problem — 7 of
the current 8 control-cohort participants have video-only recordings, zero
IMU data at all (confirmed directly, not inferred).

**Pre vs. post (treatment sensitivity):** only 1 of 4 MS participants (P15)
has both timepoints in the OptiTrack-matched dataset. Reported as
descriptive/illustrative only — n=1 paired observation supports no
inference.

**Evaluated against the MAS-replacement goal:** both of these are
data-volume gaps, not evidence against the hypothesis. This is why
IMU-recording the 7 existing video-only controls (a data-collection task
requiring zero new recruitment) is the single highest-leverage next action
identified across the whole investigation — it directly fixes both the
group-comparison and, eventually, the classification power problem in
Section 6.

---

## 6. The actual test: does combining metrics beat MAS's resolution?

This is the section that answers the question in this document's title
directly, because it's the only one that tested a genuine
diagnostic-generalization claim rather than an association.

**Method:** all 7 Popović PT parameters computed for IMU, MediaPipe, and
OptiTrack on the same 49 trials. Two things tested:

**(a) Do metrics even agree on *direction* across modalities?** No.
Cohen's d effect size (MAS>0 vs. MAS=0) for φ_max_ratio, ω_max,n, and f is
*positive* under OptiTrack (ground truth) but *negative* under IMU and
MediaPipe on nearly every one of the same metrics. This is a more serious
problem than the Section 1 agreement numbers alone implied — it's not just
noisy, a metric read from IMU or MediaPipe alone can point the wrong way
relative to true severity.

**(b) Does combining all 7 metrics classify MAS=0 vs. MAS>0 better than
any single metric, when tested honestly?** Logistic regression, all 7 IMU
metrics combined, **leave-one-participant-out cross-validated** (the model
never sees the person it's tested on — the actual bar "generalizes to a new
patient" implies): AUC = **0.21**. Every single-metric AUC was also below
0.5 (range 0.0–0.17). Not "no signal" — actively worse than a coin flip on
a held-out person.

**Evaluated against the MAS-replacement goal — the direct answer:**
**no, not with this data.** And the reason is diagnostic on its own: a
classifier trained on 4 people's metric-to-severity relationships can't
generalize to a 5th when Section 6(a) already shows that relationship isn't
even consistently *signed* across people. This is not evidence the
multi-metric hypothesis is wrong — it's evidence that **5 participants is
not enough independent data to test it**, full stop. The qualitative
argument for "we need multiple metrics" (Section 6a's sign-inversion
finding, and the literature precedent that ratio-parameter combinations
outperform any single metric — Whelan et al., 2018, found their best
classification accuracy came from combining F1amp and Plat, not RI alone)
remains intact. What's missing is the participant count to prove it holds
up out-of-sample.

---

## 7. Critical flaws in the PT score itself — not just the measurement

Everything above evaluates whether the *measurement* (angle-vs-time
trajectory) is accurate. This section evaluates the *scoring formula* that
turns that trajectory into a clinical-facing number
(`pendulastic_pt_score.compute_pt_score()`), on the assumption the
measurement were perfect. It isn't a clean pass even so.

### 7.0 There are two different PT-parameter implementations in this codebase

Every figure in Sections 4-6 used `workbench_engine.windowed_pt_params()` —
by its own docstring, "a deliberate simplification" of the real thing.
The actual production scoring engine clinicians would see output from is
`pendulastic_pt_score.compute_pt_params()`, which additionally does
baseline-drift correction (linear detrend fit from the pre-release hold),
more careful release detection, and tail-median neutral estimation. Re-running
the unified 3-modality trial set through the *production* function changed
the usable trial count from **49 → 40** (its stricter quality gates reject
more trials) — meaning Sections 4-6's exact numbers do not necessarily
describe what the shipped scoring pipeline actually outputs. This
investigation should be treated as directionally informative, not as a
literal audit of `compute_pt_score()`, until re-run against it directly
(which the rest of this section now does).

### 7.1 The composite score does not separate Control from MS in this data — even naively

**Figure 8** (`fig8_score_naive_vs_logocv.png`). Computed the actual
production `compute_pt_score()` for every trial in the unified set (IMU,
n=40 trials, 5 participants) and tested Control vs. MS two ways:

- **Naive** (pooled trials, no participant structure — the same shape of
  test the codebase's own `HEALTHY_REF`/`PT_HEALTHY_MAX` comments describe:
  *"Control-vs-MS separation still holds (PT7 median 0.111 vs 0.448,
  Mann-Whitney p=0.0001)"*): **on this dataset, p = 0.5865. Not
  significant, at the most favorable possible test.** The two groups'
  score distributions visibly overlap in the figure — the control
  participant's max (1.22) sits inside the MS group's interquartile range.
- **Honest** (leave-one-participant-out, generalization to an unseen
  person): **could not even be computed.** With 1 control participant,
  holding them out for testing leaves zero controls to train on — the test
  has no valid fold to run.

This directly contradicts the codebase's own documented validation claim.
That doesn't mean the earlier p=0.0001 result was fabricated — it was very
likely computed on a different modality, different trial set, or before
this session's bug fixes (the Ockendon β gimbal-lock fix, the config
re-tune) changed what the pipeline outputs — but as it stands today, **the
number currently written in the code as evidence the score works does not
reproduce.** That comment should not be trusted as a live claim without
re-verification, and this document is that re-verification.

### 7.2 The "healthy" reference the score is computed against rests on n=4, and could not be stress-tested

`HEALTHY_REF` (the values every trial's score is measured as a deviation
from) is explicitly commented as *"control median n=4 (2026-08-10
recalibration)"*, and `PT_HEALTHY_MAX`/`PT_BORDERLINE_MAX` as
*"PROVISIONAL... Same n=7 caveat as HEALTHY_REF... replace with the full
cohort once available"* — the codebase's own author already flagged this
as not-yet-solid. Figure 9 (leave-one-control-out sensitivity, testing how
much the "healthy" threshold moves depending on which controls anchor it)
**could not be run at all**: the current 3-modality-matched trial set has
only **1** control participant (P16) with usable IMU+MediaPipe+OptiTrack
data together, not the 4 the reference was built from. This is the same
n=1-control-arm problem from Section 5, showing up a second time in a more
consequential place — it's not just that the group *comparison* is
underpowered, the reference *every score is computed against* has never
been stress-tested for how much it would move with different control data.

### 7.3 Two of the "7 independent parameters" are not independent

**Figure 10** (`fig10_param_correlation.png`). Correlation matrix across
the 7 parameters `compute_pt_score()` weights **equally** (each contributes
exactly 1/7 of its own normalized deviation — an unweighted sum, not a
fitted or empirically-validated weighting). R2n and ω_max,n correlate at
**r = 0.93** — nearly redundant. Equal-weighting a near-duplicate pair
means that shared signal gets counted at roughly double its intended
1/7 weight, while genuinely distinct information (e.g., area_ratio, which
correlates weakly-to-negatively with everything else, r = −0.03 to −0.36)
is diluted relative to what a properly-decorrelated weighting scheme would
give it. This is a second, independent explanation (on top of Section 6's
sign-inversion finding) for why the combined score doesn't outperform
individual metrics: naive equal-weighting over a collinear feature set
doesn't behave like combining 7 independent pieces of evidence, closer to
combining ~5-6.

Worth noting: the codebase already has a partial acknowledgment of this in
`compute_pt_score_simple()`, a 4-parameter variant that explicitly drops
`area_ratio` ("unreliable for marker-based angles") and `f` ("adds only
small discriminative power") — i.e., the code's own author already
identified 2 of the 7 as weak, yet the "full" 7-parameter score in
production use still weights them equally with the other 5.

### 7.4 The score-to-MAS-label mapping is borrowed, not derived from this project's data

`pt_to_mas()` converts a continuous PT score into an MAS-equivalent label
via fixed thresholds `_MAS = [(0.12,"0"),(0.28,"1"),(0.44,"1+"),(0.60,"2"),(0.78,"3")]`,
commented **"Popovic 2018, kept for historical comparison only."** These
were not fit to this project's own instrument (a smartphone IMU, not
Popovic's original pendulum apparatus), population, or scoring formula
variant — they're carried over from a different study's calibration. Using
them to report an MAS-equivalent label is exactly the kind of unvalidated
cross-study transfer that would need its own agreement study (Bland-Altman,
ICC — the same tools Section 1 applied to the raw angle) before it could be
trusted, and that study doesn't exist yet.

### 7.5 The score was likely validated against the wrong clinical target

This is new, and changes what "does it correlate with MAS" (Section 4)
actually means. `mas_scores.csv` was extended today with granular
**`mas_flexion`** and **`mas_extension`** columns, alongside the existing
collapsed `mas_grade`. The Wartenberg pendulum test's own literature is
specific about what it measures: *"the pendulum test is commonly used to
quantify **knee extensor spasticity**"* (Whelan et al., 2018, title and
throughout) — it's an extensor-spasticity instrument by design.

For the one participant with the granular breakdown recorded (P15):

| Leg | `mas_grade` | `mas_flexion` | `mas_extension` |
|---|---|---|---|
| Left | 0 | 1+ | 0 |
| Right | 1 | 1 | 0 |

**`mas_extension` is 0 on both legs** — no variance at all — while
`mas_grade` and `mas_flexion` both vary. Section 4's reported correlation
(ρ = −0.313, p = 0.014) was computed against `mas_grade`, the collapsed
field, because that's the only one with enough participants recording it.
But if the pendulum test is specifically an extensor-spasticity probe, and
the one case with extension-specific data shows *zero* extensor spasticity
regardless of what the overall grade says, **it's a live, unresolved
question whether that earlier correlation reflects extensor spasticity (what
the instrument is designed to detect) or is actually tracking something
else the collapsed grade folds in.** This can't be resolved with current
data — it needs `mas_extension` recorded across enough participants to
correlate against directly, replacing `mas_grade` as Section 4's target
variable.

A second, independent participant makes the same point from a different
angle: **P17**, a brand-new MS participant whose data appeared mid-session,
has `mas_grade = -1` with the note *"Overall MAS Grade not yet assessed"* —
but `mas_flexion` and `mas_extension` **are** recorded for both legs. If a
clinician can score flexion/extension sub-components before committing to
an overall grade, that's further evidence the sub-components are the more
fundamental, reliably-available clinical data — not an edge case to work
around.

### 7.6 What this section changes about the priority list

Sections 1-6 already argued the path forward is "more participants, fix the
calibration bias." This section adds three items that are true regardless
of sample size:

1. **Re-derive `HEALTHY_REF` and re-verify `PT_HEALTHY_MAX`/`PT_BORDERLINE_MAX`**
   against the production `compute_pt_params()` pipeline once more controls
   have IMU data (Section 5's recommended action does double duty here).
2. **Either fit the 7 parameter weights from data, or explicitly decorrelate
   /reduce them** (R2n and ω_max,n at r=0.93 should not both carry full
   weight) — equal-weighting a collinear set is a design choice, not a
   neutral default, and it's one of the two identified reasons the combined
   score underperforms.
3. **Switch Section 4's clinical-correlation target from `mas_grade` to
   `mas_extension`** once enough participants have the granular field
   recorded, since that's what the pendulum test actually probes — and
   treat the current ρ=−0.313 correlation as provisional until that switch
   is possible.
4. **Derive or validate `_MAS`'s score-to-grade thresholds against this
   project's own data** before reporting any PT-score-derived MAS-equivalent
   label externally — the current thresholds are Popovic 2018's, unverified
   for this instrument.

---

## 8. Overall verdict

| Question | Answer |
|---|---|
| Does the IMU measure knee angle accurately enough to trust in isolation? | Not yet — 14.84° RMSE, but ~70% of that is a fixable calibration bias, not sensing noise |
| Is there a better algorithm already sitting unused? | No — searched and ruled out (Ockendon model, magnetometer, MediaPipe); the one real win (re-tuned config) is already adopted |
| Does it correlate with MAS at all? | Yes, real and significant (ρ=−0.313, p=0.014), but weaker than the closest comparable published study |
| Does combining metrics produce a diagnostic more accurate than MAS today? | **No** — under honest cross-validation, neither single nor combined metrics generalize to a new participant (AUC 0.0–0.21, all below chance) |
| Does the actual production PT score (not just raw metrics) separate Control from MS? | **No, not even naively** — p=0.5865 on the real `compute_pt_score()` output, contradicting the codebase's own documented p=0.0001 claim (Section 7.1) |
| Is the score formula itself sound, independent of measurement error? | **No** — equal-weights two near-redundant parameters (r=0.93), rests on an n=4 reference never stress-tested, and uses score-to-MAS thresholds borrowed from a different study's instrument (Section 7.2-7.4) |
| Was the earlier MAS correlation even measuring the right clinical target? | **Unresolved** — the pendulum test targets extensor spasticity specifically, but the correlation used the collapsed `mas_grade`; the one participant with granular data shows zero extensor spasticity while the overall grade varies (Section 7.5) |
| Is that a dead end for the "more accurate than MAS" goal? | **No** — it's a statement about sample size (n=5) and unresolved measurement bias, not about whether the underlying idea is sound |

**What would change the answer, in priority order (Sections 1-6 and Section 7 combined):**

1. **IMU-record the 7 existing video-only controls** (Section 5's finding)
   — zero new recruitment, directly triples the usable control arm, and is
   the fastest way to get LOGO-CV testing a real n instead of n=5. Also
   the only way to re-derive `HEALTHY_REF` and stress-test
   `PT_HEALTHY_MAX` (Section 7.2), which currently can't even be tested
   with 1 control in the matched set.
2. **Resolve the calibration bias** (Section 3) via a small controlled
   validation recording (verified mount vs. ordinary mount, matched to
   OptiTrack) — if successful, moves the effective error ceiling from ~15°
   toward the field-typical ~7-10° residual already measured, which
   directly strengthens every metric in Section 6's heatmap and AUC test.
3. **Recruit participants across the full MAS severity range** — the
   current MS cohort tops out at MAS=1+; there are zero trials at MAS≥2 in
   this dataset, so no claim about the diagnostic's behavior at moderate-
   to-severe spasticity is possible yet, regardless of sample size.
4. **Record `mas_extension` (not just the collapsed `mas_grade`) across
   the full cohort** and switch the clinical-correlation target to it
   (Section 7.5) — a data-collection-protocol fix, not an analysis fix,
   and cheap to do going forward since flexion/extension sub-scores are
   apparently already assessed per-session (P17 has them without an
   overall grade).
5. **Fix the score formula's internal issues independent of more data**
   (Section 7.6): decorrelate or re-weight the 7 parameters instead of
   equal-weighting a collinear set, and stop reporting an MAS-equivalent
   label from `_MAS`'s borrowed thresholds until they're re-derived from
   this project's own data.
6. Only after 1-5: re-run Section 6 and Section 7.1's exact analyses. If
   the combined AUC clears both 0.5 and every single metric, with a
   heatmap showing consistent signs across modalities, *and* the composite
   score separates Control from MS under honest cross-validation — that is
   the "more accurate than MAS" result this document set out to test for,
   and doesn't have yet.

---

## Source documents and reproducibility

- `docs/reports/2026-08-17-imu-methodology-comparison.md` — Sections 1-3
  (agreement, methodology search, bias investigation) source data and full
  investigation trail, including the corrected reasoning where an earlier
  root-cause read was found to be wrong and walked back.
- `docs/reports/2026-08-19-results-data-analysis-draft.md` — Sections 4-6
  source data, all 7 figures, and the Monica Perez-lineage literature
  citations (De Santis & Perez 2024 JNER; Whelan et al. 2018 JNER; Yeh et
  al. 2025) the figure conventions and clinical-correlation benchmarks are
  drawn from.
- Scripts: `evaluate_ockendon_methodology.py`, `evaluate_tuning_grid_methodology.py`,
  `evaluate_mediapipe_vs_imu_rmse.py`, `evaluate_release_anchored_rezero.py`,
  `generate_paper_results_analysis.py`, `generate_mediapipe_pt_agreement.py`,
  `generate_group_condition_figures.py`, `generate_multimetric_analysis.py` —
  none pipeline-wired; all are standalone, re-runnable diagnostics.
- **Section 7** (PT-score-formula critique) is from `generate_pt_score_critique.py`
  (2026-08-19), the only script in this investigation to call the actual
  production `pendulastic_pt_score.compute_pt_params()`/`compute_pt_score()`
  rather than `workbench_engine.windowed_pt_params()`. Figures 8-10 saved to
  `Model_Analysis_Outputs/paper_figures/`. Figure 9 (HEALTHY_REF sensitivity)
  did not run — noted as a finding in its own right (Section 7.2), not a
  script bug.
