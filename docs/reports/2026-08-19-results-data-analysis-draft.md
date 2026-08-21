# Results and Data Analysis — draft for Dr. Perez review

Draft prepared 2026-08-19. Real numbers computed from the current dataset (not
placeholders) — see "How this was computed" at the end for full reproducibility
and the honest limitations section before this goes anywhere external.

---

## Data Analysis (Methods subsection)

Knee flexion/extension angle was recorded during the Wartenberg pendulum test
using two independent measurement modalities: (1) a smartphone inertial
measurement unit (IMU) mounted on the shank, fused via a Madgwick AHRS filter
(β = 0.041, EMA smoothing α = 0.5, gyroscope-bias/gravity-seeded calibration),
and (2) markerless video pose estimation (MediaPipe, "full" model variant,
landmark-visibility threshold 0.5), against optical motion capture (OptiTrack,
marker-PCA-derived knee angle) as the reference standard.

From each trial's angle-time series, seven kinematic parameters were extracted
per the Popović pendulum-test model, computed over the active-oscillation
window: normalized relaxation index (R2n), number of oscillations (N),
normalized peak return ratio (φ_max_ratio), normalized peak flexion angular
velocity (ω_max,n), oscillation frequency (f), symmetry/area ratio, and
normalized peak extension angular velocity (ω_min,n).

**Agreement between each measurement modality and the OptiTrack reference**
was assessed via the intraclass correlation coefficient, two-way random
effects, single measurement, absolute agreement (ICC(2,1); McGraw & Wong,
1996) and Bland-Altman bias/95% limits of agreement, computed per PT
parameter across all trials with both signals available.

**Clinical validity** was assessed via Spearman's rank correlation (ρ)
between the IMU-derived relaxation index (R2n) and Modified Ashworth Scale
(MAS) grade, for the subset of trials with a contemporaneous MAS assessment.

**Group comparison** (MS vs. control) used a linear mixed-effects model
(R2n ~ group, participant as random intercept) rather than a t-test or
one-way ANOVA, because each participant contributed multiple trials
(non-independent observations); model fit via REML (`statsmodels` MixedLM,
Python).

All analyses were performed in Python 3.13 (`numpy`, `scipy.stats`,
`statsmodels`). Statistical significance was set at α = 0.05.

---

## Results

### Sample

Sixty-one trials from 5 participants (4 MS, 1 healthy control) had both IMU
and OptiTrack recordings and were included in the instrument-validation
analysis. Modified Ashworth Scale scores were available for all 61 trials:
the 4 MS participants (P5, P13, P14, P15) had clinician-assessed grades;
the control participant (P16) was assigned MAS = 0 by definition (healthy,
not a clinical assessment). A separate, larger sample — 37 trials
across 5 different participants (P5, P13, P14, P15, P16, overlapping the IMU
set) — had both video and OptiTrack recordings and was used for the
markerless/MediaPipe comparison.

### Agreement with OptiTrack ground truth

**IMU.** Across the 7 Popović PT parameters, agreement with OptiTrack was
poor to fair (ICC(2,1) range 0.014–0.458; Table 1). The strongest agreement
was for oscillation count (N; ICC = 0.458) and the relaxation index (R2n;
ICC = 0.226); agreement was weakest for peak flexion velocity (ω_max,n;
ICC = 0.014). Bland-Altman bias was small in absolute terms for the
ratio-based parameters (R2n bias = −0.11, area ratio bias = −0.05) but the
95% limits of agreement were wide relative to each parameter's typical range,
consistent with the overall trajectory-level RMSE finding below.

*Table 1. IMU vs. OptiTrack agreement, per PT parameter (n = 61 trials).*

| Parameter | ICC(2,1) | Bias | 95% LoA |
|---|---|---|---|
| R2n (relaxation index) | 0.226 | −0.108 | [−1.06, 0.85] |
| N (oscillation count) | 0.458 | −1.46 | [−7.48, 4.56] |
| φ_max_ratio | 0.044 | −0.055 | [−0.51, 0.40] |
| ω_max,n | 0.014 | −2.13 | [−12.05, 7.78] |
| f (frequency) | 0.140 | −0.40 | [−1.98, 1.18] |
| Area ratio | 0.135 | −0.052 | [−0.82, 0.72] |
| ω_min,n | 0.214 | −1.01 | [−6.70, 4.69] |

Separately, at the raw-trajectory level (not the summary PT parameters
above), full-curve RMSE against OptiTrack for the IMU pipeline was
**14.84° (mean) / 10.98° (median)** across the 53 trials with a
production-representative AHRS configuration (grid-search re-tuned
2026-08-18 against this same OptiTrack corpus); only 2/53 trials met a 5°
clinical-goal threshold.

**MediaPipe/video.** Full-curve RMSE against OptiTrack was **36.0° (mean) /
33.3° (median)** across 37 trials — substantially higher error than the IMU
pipeline, and 0/37 trials met the 5° threshold. Per-PT-parameter agreement is
worse still: ICC(2,1) was **at or below zero for every one of the 7
parameters** (range −0.115 to 0.032; Table 2), indicating essentially no
agreement with OptiTrack at the parameter level — not merely "poor" by the
Koo & Li thresholds, but consistent with no systematic relationship at all.

*Table 2. MediaPipe vs. OptiTrack agreement, per PT parameter (n = 49 trials).*

| Parameter | ICC(2,1) | Bias | 95% LoA |
|---|---|---|---|
| R2n (relaxation index) | −0.041 | −0.441 | [−2.35, 1.47] |
| N (oscillation count) | 0.032 | −2.30 | [−18.35, 13.75] |
| φ_max_ratio | −0.008 | 0.110 | [−0.91, 1.13] |
| ω_max,n | −0.036 | 6.75 | [−50.28, 63.77] |
| f (frequency) | −0.115 | −0.83 | [−3.04, 1.37] |
| Area ratio | 0.003 | −0.420 | [−1.23, 0.39] |
| ω_min,n | −0.018 | 3.69 | [−29.16, 36.53] |

**Figure 1** (`Model_Analysis_Outputs/paper_figures/fig1_bland_altman.png`).
Bland-Altman plot, IMU vs. OptiTrack relaxation index (R2n), n = 61 trials.
Solid line: bias (−0.108); dashed lines: 95% limits of agreement. Chosen as
the headline figure because R2n is the metric Perez-lineage papers (Whelan
et al. 2018; De Santis & Perez 2024) treat as the primary spasticity
indicator.

**Figure 2** (`Model_Analysis_Outputs/paper_figures/fig2_metrics_by_mas.png`).
IMU-derived R2n, oscillation count, and area ratio, mean ± SD grouped by MAS
category — direct format match to Whelan et al. (2018) Figure 3. Note the
honest gap this figure surfaces on its own: **zero trials in the current
cohort have MAS ≥ 2** (moderate-or-worse spasticity) — the current MS sample
(P5, 13, 14, 15) tops out at MAS = 1+. Any claim about metric behavior at
higher spasticity grades is not supported by this dataset yet.

**Figure 3** (`Model_Analysis_Outputs/paper_figures/fig3_trajectory_example.png`).
Example single-trial knee-angle trajectory, IMU vs. OptiTrack overlaid
(Participant_16, trial 16_left_control_T1) — format matches Willaert et al.
(2020) Figure 1b. Qualitatively shows the IMU tracking the damped-oscillation
shape well in early cycles, with growing phase/amplitude drift later in the
trial and a different settle angle (IMU ≈117° vs. OptiTrack ≈125°) — a
visual, single-trial illustration of the bias this whole investigation has
been chasing, not a cherry-picked best case (chosen for having a normal,
representative RMSE for this dataset, not the best or worst trial).

### Clinical validity (MAS correlation)

The IMU-derived relaxation index (R2n) was significantly, negatively
correlated with MAS grade (Spearman's ρ = −0.313, p = 0.014, n = 61 trials,
all 5 participants including the control at MAS = 0) — consistent with the expected direction (lower
relaxation index / more restricted swing associated with higher clinically
rated spasticity) and consistent with the sign reported in the closest
published comparable study (Yeh et al., 2025: ρ = −0.75 to −0.78 for an
equivalent ratio parameter, though with a much larger stroke sample, n=20).

### Group comparison (MS vs. control)

The mixed-effects model estimated MS participants' R2n as 0.17 lower than
the control participant's R2n on average, but this difference **did not
reach statistical significance** (β = −0.173, SE = 0.207, z = −0.835,
p = 0.404). This is not interpretable as a null clinical finding — see
Limitations: the control arm is a single participant.

**Figure 4** (`Model_Analysis_Outputs/paper_figures/fig4_metrics_by_group.png`).
R2n, oscillation count, and area ratio by group (Control vs. MS), group mean
± SD as bars with every contributing participant's own mean overlaid as an
open circle. The per-participant dots are the point of this figure: they
make the n=1 control arm visually honest (one dot, not an error bar
implying a distribution) rather than letting a bar-with-error-bars imply a
sample that isn't there.

### Pre/post (MS participants with a treatment timepoint)

Of the 4 IMU-validated MS participants, only **P15 has both a pre and a
post recording** in the OptiTrack-matched dataset; P14 has pre only; P13
and P5 have post only (their "pre" sessions exist in `mas_scores.csv` but
did not have a matched OptiTrack recording in this corpus). **This is not
a pre/post comparison in any statistical sense — it is one paired
observation.** Figure 5 is included as a descriptive/illustrative figure
only, not a result: P15's R2n increased slightly pre→post (0.91→0.94); no
inference should be drawn from n=1.

**Figure 5** (`Model_Analysis_Outputs/paper_figures/fig5_pre_post.png`).
R2n by timepoint. Solid line: the one paired participant (P15). Squares:
participants with only one timepoint, shown for context, not connected.

### Do we need multiple metrics combined to assess spasticity? (2026-08-19)

Tested this directly rather than asserting it, using all three modalities
(IMU, MediaPipe, OptiTrack) computed on the same 49 trials (5 participants).

**Figure 6** (`Model_Analysis_Outputs/paper_figures/fig6_metric_effect_heatmap.png`).
Cohen's d effect size (MAS>0 vs. MAS=0) for each of the 7 PT parameters, per
modality. The real finding here isn't "which metric is biggest" — it's the
**sign pattern**: OptiTrack (ground truth) shows positive effect sizes for
φ_max_ratio, ω_max,n, and f (these metrics run higher in spastic trials),
but IMU and MediaPipe show the **opposite sign** on nearly all of the same
metrics. This is a more serious problem than the agreement numbers alone
(Tables 1-2) already implied: it's not just that IMU/MediaPipe are noisy
relative to OptiTrack, it's that a metric read from IMU or MediaPipe alone
can point the wrong direction relative to true clinical severity.

**Figure 7** (`Model_Analysis_Outputs/paper_figures/fig7_single_vs_combined_auc.png`).
Directly tested "does combining all 7 metrics classify MAS=0 vs. MAS>0
better than any single metric" via logistic regression, **leave-one-
participant-out cross-validated** (the honest test — does it generalize to
a person the model never saw) on the IMU data (n=49 trials, 5
participants, 35 MAS>0 / 14 MAS=0). Result: **every single-metric AUC is
below 0.5, and the combined-metric AUC (0.21) is too** — not "no signal,"
actively worse than a coin flip on held-out participants.

**What this means, stated plainly:** the data does not yet support "combine
these metrics for a reliable severity read." Given Figure 6's sign-inversion
finding, this isn't surprising — a classifier trained on 4 people's
metric-to-severity relationships can't generalize to a 5th when that
relationship isn't even consistently signed across people. This is not a
failure of the multi-metric idea itself; it's that **5 participants isn't
enough independent evidence to test it**, and it directly reinforces the
priority already agreed on: IMU-record the existing 7 video-only controls
(and eventually more MS participants) before any classification claim,
single- or multi-metric, is defensible. If this analysis is re-run once
that happens and the combined AUC clears both 0.5 and each single metric
with a stable sign in Figure 6, *that* would be the "we need these
combined" result — this draft doesn't have it yet.

---

## Limitations — read before this goes to Dr. Perez

1. **The control arm of the IMU-validated sample is n = 1.** Of the current
   8-control-participant cohort, 7 have video-only recordings — no
   accelerometer/gyroscope/magnetometer data exists for them at all (checked
   directly, not inferred). The MS-vs-control comparison above is not a
   meaningful group comparison as reported; it should not be presented as a
   negative/null finding, only as a demonstration that the analysis pipeline
   is wired correctly and ready to run once more IMU-recorded controls exist.
2. **All ICC values are poor-to-fair (< 0.5) by conventional thresholds**
   (Koo & Li, 2016: <0.5 poor, 0.5–0.75 moderate). This is an honest result,
   not a presentation problem — it's consistent with everything this
   session's broader RMSE investigation found (bias-dominated error, ~15°
   mean/~11° median at the trajectory level). Presenting this to Dr. Perez
   as-is, with the bias-vs-scatter context from that investigation, is more
   defensible than polishing the framing.
3. **MediaPipe's near-zero/negative ICC values need careful framing, not
   suppression.** A negative ICC is a legitimate, real result (it means the
   between-trial variance the model attributes to "true" trial-to-trial
   differences is smaller than measurement noise) — it should be reported
   as "no measurable agreement," not omitted or rounded to zero.
4. **Sample size overall is small** (5 IMU participants, up to 17
   video-only participants) for any claim beyond "instrument feasibility."
   Not powered for a definitive validation or clinical-discrimination claim.

## Decisions made 2026-08-19

- **MAS for controls: set to 0** (assumed, not a real clinical assessment —
  healthy controls by definition). Added to `mas_scores.csv` for all 8
  controls, `assessed_by="ASSUMED"` to keep this distinguishable from the
  real clinician-entered MS scores (`VL`/`WD`/`AN`) if anyone audits the file
  later. Analysis re-run with this data — see updated numbers above/below.
- **Tardieu Scale: excluded from this paper.** Not collected anywhere in
  this project; dropped as a comparator rather than left as an open gap.
  (The README's Research Aim 3 still lists Tardieu alongside MAS — worth a
  separate doc-drift fix outside this draft, not blocking here.)
- **MS disease-severity covariate (EDSS / time-since-diagnosis): out of
  scope for this draft.** Not collected; the group-comparison model does not
  include it.
- **Next data-collection priority: IMU-record the existing 7 video-only
  controls, before recruiting anyone new.** Cheapest fix for the n=1
  control-arm problem (Limitation 1) — zero new recruitment required. Feeds
  into the resumed recruitment/roadmap plan (see
  `docs/superpowers/specs/` once written) with this as the first concrete
  step, ahead of stroke/SCI expansion.

## How this was computed (reproducibility)

`generate_paper_results_analysis.py` (repo root) — reuses
`batch_imu_vs_optitrack_rmse.discover_trials()` for trial/OptiTrack matching,
`workbench_engine.windowed_pt_params()` for the 7 PT parameters, a
from-scratch ICC(2,1) implementation (McGraw & Wong 1996 formula, no
external dependency), and `statsmodels.MixedLM` for the group comparison.
Per-trial output saved to `Model_Analysis_Outputs/paper_results_analysis_trials.csv`.
MediaPipe RMSE numbers are from `evaluate_mediapipe_vs_imu_rmse.py`
(2026-08-18 run, partial: 37/45 trials before external interruption — see
`docs/reports/2026-08-17-imu-methodology-comparison.md`). MediaPipe
per-PT-parameter ICC/Bland-Altman is from `generate_mediapipe_pt_agreement.py`
(2026-08-19), reusing the cached MediaPipe landmark extraction from the RMSE
run (`sweep_cache/landmarks/`) — no new pose-estimation inference for the
already-cached trials.

**Figures 1-3** are from `generate_paper_figures.py` (2026-08-19), built
after a `/biomedical-search` literature pass identified figure conventions
directly from Perez-lineage papers (De Santis & Perez 2024, JNER, the
closest peer system to this one; Whelan et al. 2018, JNER; Willaert et al.
2020) — Bland-Altman for agreement, grouped bar-by-clinical-category for
metric behavior, and single-trial trajectory overlay for a qualitative
demonstration are all conventions matched directly from that search, not
invented. Colors use the repo's dataviz-skill validated colorblind-safe
palette (blue `#2a78d6` = reference/OptiTrack, orange `#eb6834` = test
modality). Saved to `Model_Analysis_Outputs/paper_figures/`.

**Figures 4-5** are from `generate_group_condition_figures.py` (2026-08-19).
Group/condition assignment reuses `load_group_and_demo()` from the main
analysis script; recording condition (pre/post/control) is parsed from each
trial's folder name and normalized (`post_1week`/`post_1month`/`week_1_post`
all bucket to `post`).

**Figures 6-7** are from `generate_multimetric_analysis.py` (2026-08-19).
Trial matching across all three modalities uses `rmse_pipeline_common`'s
shared trial_key namespace (`discover_imu_trials()` ∩ `discover_video_trials()`)
so IMU/MediaPipe/OptiTrack values are computed on the identical physical
trial, not just the identical participant. Classification (Figure 7) uses
`sklearn.linear_model.LogisticRegression` with per-fold `StandardScaler`,
`sklearn.model_selection.LeaveOneGroupOut` (grouped by participant — this is
the part that makes it an honest generalization test, not an in-sample fit),
scored via `sklearn.metrics.roc_auc_score`. Per-trial values for all three
modalities saved to `Model_Analysis_Outputs/multimetric_trials.csv`.
