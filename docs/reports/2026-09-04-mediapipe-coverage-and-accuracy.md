# MediaPipe: an OptiTrack-gated discovery bug hid 28 trials, and the metrics were never accurate anyway

**2026-09-04** — full audit of the MediaPipe HPE corpus: what exists, what was
missing and why, how accurate it is against OptiTrack, and whether the clinical
recordings can be used to train a better model.

## The coverage gap

285 trial videos are on disk across `Recordings/` and `OptiTrack_Recordings/`.
Before this pass:

| | count |
|---|---|
| videos with a MediaPipe CSV on the current schema | 136 |
| videos with a MediaPipe CSV on a stale schema | 112 |
| videos with **no** MediaPipe CSV at all | **37** |

"Current schema" means the CSV carries `knee_angle_world_deg` — the marker
`reextract_all_mediapipe.py` uses to tell a completed re-extraction from one that
predates the 2026-08-31 identity and world-angle corrections.

The 37 split into two causes.

**Nine** were simply never reached: the 2026-08-31 `reextract_all_mediapipe.py`
run died at trial 147 of 240 with a `MemoryError` and was not restarted. (This
machine has 8 GB of RAM; that failure recurs under load and is the reason this
pass runs under a restart supervisor.) One of the nine,
`CHAT_21/Left/pre/Trial_3.avi`, is 5,686 bytes — a truncated AVI header with no
decodable frames (`cv2` opens it and reports 0). A dead recording, not a
processing failure, and it cannot be recovered. It is the one trial of 285 that
still has no MediaPipe CSV, and it has left an orphaned
`Participant_21_T_3_mediapipe_full_0.5_annotated.mp4` behind from an earlier
partial run.

**Twenty-eight** were structurally invisible. `discover_new_trials()` walked
`OptiTrack_Recordings/**/Trial_{n}_optitrack.csv` and looked for a video beside
each one, which made an OptiTrack export a precondition for running pose
estimation at all:

| participant | trials | why no OptiTrack |
|---|---|---|
| MINT_17 Left/pre, Right/pre | 10 | Motive errored out on 2026-08-18; no take saved (already in `excluded_trials.json`) |
| MINT_14 Left/post, Right/post | 8 | no export |
| MINT_18 Right/pre | 4 | no export |
| MINT_13 Right (two timepoints) | 2 | no export |
| Control_6, MINT_4, MINT_5, Participant_test | 4 | no export |

These are exactly the trials where MediaPipe is the *only* quantitative channel
available, and they were the ones the pipeline could not see.

Discovery is now video-first (`batch_mediapipe.py`). A trial with no OptiTrack
reference still gets tracked; it just cannot be RMSE-scored afterwards.

### A naming trap that came with the 2026-09-01 rename

Recording folders were renamed `Participant_{pid}` → `Control_/CHAT_/MINT_{pid}`
on 2026-09-01, but the output filename was derived from the folder
(`rel.parts[0]`). A fresh run would therefore have written
`CHAT_19_T_1_mediapipe_full_0.5.csv` *beside* the existing
`Participant_19_T_1_mediapipe_full_0.5.csv` rather than replacing it — and
`load_hpe_model_curves` discovers HPE curves by globbing `*_T_{n}_*.csv`, so
every re-extracted trial would then have been scored twice, once off the stale
copy. `_output_participant()` now resolves the legacy name through
`participant_paths`, so re-extraction overwrites in place.

The same duplication already exists for P13 from an earlier layout change:
`MINT_13/**/Height_Joint-Level/` holds both `Participant_13_T_{n}_…` and
`Participant_13_left_pre_T_{n}_…` CSVs, and both are picked up as
`mediapipe_full_0.5` candidates for the same trial. Not fixed here.

## Accuracy

MediaPipe tracks the knee-angle waveform qualitatively and loses it completely
in the derived parameters.

| measure | result | source |
|---|---|---|
| knee-angle RMSE vs OptiTrack, median over 159 trials | **26.0°** | `MediaPipe_Sweep/preprocessing_sweep_results.csv` |
| trials under 10° RMSE | **3.1%** | same |
| ICC(2,1) vs OptiTrack, all seven PT parameters (n=49) | **−0.115 to +0.032** | `mediapipe_pt_agreement_output.txt` |
| PT correlation, `mediapipe_full` (n=30) | **0.054** | `HPE_Comparison/hpe_accuracy_summary.csv` |
| PT correlation, IMU path, for comparison (n=42) | 0.467 | same |
| MAS agreement, `mediapipe_full` | 43.3% exact, 83.3% adjacent | same |

Every PT parameter has an intraclass correlation of approximately zero. The
Bland-Altman limits of agreement on `N` are −18.3 to +13.8, on a parameter whose
healthy value is around 5. This is not a calibration offset that a better
threshold fixes; the parameters are not measuring the same thing OptiTrack is.

The configuration itself is already the best-validated one. The preprocessing
sweep ranks `identity_tracker` (26.04° median) above `baseline` (26.22°),
`rotate_+90` (26.87°), `rotate_-90` (28.24°) and `crop` (31.89°), and the
identity tracker is what production runs. The `heavy` model wins one four-trial
model sweep at 27.65° but loses on the 39-trial MAS comparison (76.9% adjacent
agreement vs `full`'s 83.3%, PT correlation 0.01 vs 0.054), so `full` stays.

### PT metrics and composite scores over the completed corpus

With every trial re-extracted, `mediapipe_pt_metrics.py` scores all 286 HPE CSVs
(284 of them `full_0.5`) through the production path. Against the OptiTrack
reference for the same trial:

| | 2D angle | world angle |
|---|---|---|
| PT7 correlation with OptiTrack (n=178) | **0.364** | 0.236 |
| median \|ΔPT7\| vs OptiTrack (n=178) | 0.460 | 0.470 |
| mean \|ΔPT7\| vs OptiTrack | 0.609 | 0.571 |
| trials passing the excursion gate (of 284) | 166 | 165 |
| MAS exact agreement, both gradeable (n=92) | **31.5%** | 29.3% |
| MAS adjacent agreement (n=92) | **44.6%** | 43.5% |

OptiTrack itself passes the excursion gate on only 156 of the same 284 trials.

A median absolute PT7 error of 0.46 is most of the usable scale — PT7 maps to
MAS across roughly 0 to 1. Adjacent-grade agreement of 44.6% is near what
chance gives on a six-point ordinal scale. This is a stricter and lower number
than the 83.3% adjacent agreement in `HPE_Comparison/hpe_accuracy_summary.csv`,
and the difference is methodological, not new data: that table calls
`pt_to_mas()` directly, so it prints a grade for trials the excursion gate
refuses, and it scores a different trial subset. Restricting to trials where
both channels are gradeable is the honest comparison, and it is much worse.

#### What the newly-covered trials actually look like

MINT_17's pre session is the clearest case for why coverage and usefulness are
different things. Those ten trials have no OptiTrack at all (Motive saved no
take), so MediaPipe is the only quantitative channel that will ever exist for
them. They now have one — and it does not carry a verdict:

| leg | trial | detection | N | A0 (deg) | PT7 | MAS |
|---|---|---|---|---|---|---|
| left | 1 | 15.5% | 2.0 | **157.9** | 0.604 | refused (impossible excursion) |
| left | 2 | 15.7% | 1.5 | 6.6 | 1.143 | refused (excursion) |
| left | 3 | 24.1% | 0.5 | 25.6 | 1.680 | 4 |
| left | 4 | 8.3% | — | — | — | refused (unscoreable) |
| left | 5 | 21.9% | — | — | — | refused (unscoreable) |
| right | 1 | 6.1% | — | — | — | refused (unscoreable) |
| right | 2 | 55.7% | 7.5 | 30.9 | 0.771 | 3 |
| right | 3 | 42.0% | 5.0 | 26.3 | 1.040 | 4 |
| right | 4 | 47.1% | 13.5 | 6.8 | 0.834 | refused (excursion) |
| right | 5 | 52.7% | 5.5 | 34.0 | 0.978 | 4 |

Four of ten produce a grade, and all four land at MAS 3 or 4 on excursions of
25–34°. The gate is doing its job on the other six — a 157.9° A0 is not a knee.
But the four that pass sit exactly in PT7's known non-monotonic region, where a
barely-moving leg and a severely spastic one score alike. Report these as
tracked, not as graded.

**The world angle is not the fix.** This is the first corpus-wide test of the
`knee_angle_world_deg` column — the earlier check ran on a handful of P14 trials.
Across 178 paired trials it is indistinguishable from the projected 2D angle:
marginally better on mean \|ΔPT7\| (0.571 vs 0.609), worse on median, worse on
PT7 correlation (0.236 vs 0.364), and worse on both MAS agreement measures.
Keeping both columns remains right — the trade is per-trial and camera-dependent
— but there is no corpus-level winner, and switching the production column to
the world angle would make the composite worse.

**The excursion gate is not selecting for detection quality.** Trials with under
25% detection pass the gate 24% of the time, but the three higher buckets pass at
74%, 59% and 67% — non-monotonic. A well-tracked trial is not meaningfully more
likely to yield an interpretable A0 than a poorly tracked one, which is another
way of seeing that the failure is in the derivation rather than in the tracking.

### Coverage is set by camera framing, not by the model

Per-participant median detection rate spans the whole range:

| participant | median detection | |
|---|---|---|
| MINT_18 | **5.5%** | patient's head and shoulders cropped off frame, seated not supine, assessor fully visible |
| MINT_14 | 33.3% | |
| MINT_15 | 42.9% | |
| Control_12 | 43.7% | |
| … | | |
| Control_8 | **99.6%** | lateral view, whole supine body in frame |
| MINT_4 | 99.6% | |

The patient/assessor tracker keys on trunk horizontality measured from the
shoulder-to-hip vector. When the patient's shoulders are outside the frame and a
standing assessor is fully visible, that heuristic has nothing to work with and
the assessor wins. Nothing downstream recovers this: MINT_18 needs a re-shoot
with the torso in frame, not a re-run.

Re-extraction confirms coverage is not the lever. Across the first 13
re-extracted trials, median detection was unchanged (64.7% → 64.7%) and the
ambiguous-frame fraction rose slightly — which is the corrected behaviour: the
lone-detection continuity check now refuses frames where only the assessor was
found, which the old code wrote as the patient's limb.

## Can the recordings train a better model?

Three routes have been tried against this corpus. None reaches usable accuracy.

**Fine-tuning MediaPipe's pose landmarker is not available.**
`mediapipe_model_maker` 0.2.1.4 does not support pose-landmarker fine-tuning
(`train_angle_regressor.py` header). There is no path to "teach BlazePose what
to look for" with this toolchain.

**A direct knee-angle regressor was trained on these recordings and failed.**
MobileNetV3-Small on 224×224 knee crops, 23,799 training and 11,085 validation
frames drawn from the clinical videos, labelled with OptiTrack angles. Best
validation RMSE **23.4°** (`models/angle_regressor_metrics.json`), against a 3°
target, and it overfit after epoch 7 (23.4 → 25.0 → 31.0 → …). For scale,
`gen_training_data.py` records that the first-generation model scored 25–28°
where a constant-mean-angle predictor scores **27.1°** — the trained model beats
predicting a single number by under four degrees.

The reason is structural, not a hyperparameter choice. Labels come from
OptiTrack, so trainable data is capped at the trials that have an OptiTrack
export — and that ground truth carries its own documented defects (collinear
thigh bar, duo sessions with `Shank == Thigh`, coverage collapse). Training a
vision model against a noisy label is bounded by the label.

**Post-hoc calibration is the only thing that helped, and it is small.** A
degree-3 polynomial fitted to MediaPipe's angle error improves held-out RMSE from
13.6° to **12.3°** (`models/calibration/angle_calibration_stats.json`). The bias
it corrects is strongly angle-dependent — mean bias 30.3° in the 80–90° bin — so
it is a genuine systematic error, not noise. It is currently loaded only by
`pendulastic_viewer.py` and is **not applied** in the batch extraction or the PT
scoring path.

That is the one open, cheap improvement on the table: applying the existing
calibration in the scoring path. It would not rescue the PT parameters — a 1.3°
gain against a 26° error does not move an ICC of zero — but it is free and
already validated.

## A provenance defect

Every CSV in the corpus is named `mediapipe_full_0.5`, and every consumer
(`batch_pendulastic.py`, `gen_training_data.py`'s `HPE_PREFERENCE`,
`model_vs_optitrack_eval.py`'s label map) reads that as visibility threshold 0.5.
The extractor uses `VIS_THRESH = 0.40` (`batch_mediapipe.py:50`) and hardcodes
`0.5` into the output filename. The *data* is fine — the sweep ranks 0.4 slightly
ahead of 0.5 (31.13° vs 31.85° median) — but the filename asserts a threshold that
was never used. Left unchanged here: renaming the corpus is a separate decision,
and changing the threshold would invalidate every existing CSV.

## What this pass did

- Fixed `discover_new_trials()` to be video-first, and pinned output naming to
  the legacy `Participant_{pid}` form so re-extraction overwrites in place.
- Re-extracted 149 trials (37 that had no CSV, of which 36 are readable, plus
  112 on the stale schema) under a restart supervisor, so every trial carries
  `knee_angle_world_deg`,
  `identity_score` and `identity_ambiguous`, from one extractor version.
- Added `mediapipe_pt_metrics.py`, which scores every HPE CSV on disk through
  the production path (`compute_pt_params` → `mas_estimate`, so the excursion
  gate is respected rather than bypassed as in `hpe_mas_evaluation.py`), from
  both the 2D and the world angle column, against the OptiTrack reference where
  one exists. Output:
  `Model_Analysis_Outputs/HPE_Comparison/mediapipe_pt_metrics.csv`.
- Moved 18 superseded duplicate CSVs (P13 ×17 from the pre-2026-08 folder
  layout, P001 ×1) into `_superseded_2026-09-04/` subfolders. Each had a
  current counterpart for the same trial and variant in the same directory, and
  `load_hpe_model_curves` globs `*_T_{n}_*.csv` non-recursively — so both copies
  were being evaluated as `mediapipe_full_0.5` candidates for one trial and the
  lower-RMSE one kept, cherry-picking between a stale and a fresh extraction.
  Moved rather than deleted; one level down is out of discovery.
  **P13 is one of the two reference participants, so its comparison figures will
  shift when regenerated — that is the correction, not a regression.**
- Made MediaPipe **advisory** on the clinician-facing surface: the per-source
  table in `pt_report_common.make_report_figure` now shows MediaPipe's PT7 and
  waveform but prints `[advisory]` in place of a MAS grade, with the reason in
  the table caveat. The IMU keeps its grade. Four tests pin the behaviour,
  including that the suppression is channel-level rather than a second
  excursion gate — an optical trial can pass the gate cleanly and still be
  graded wrongly, so the gate cannot stand in for this judgement.
- Backed up the pre-run CSVs to
  `C:\Users\cladi\pendulastic_backups\mediapipe_csv_2026-09-04\` (the recording
  trees are gitignored, so an overwrite is not otherwise recoverable).

## Where the IMU sits, and a confound worth more than the headline

Adding the phone IMU to the same table — replayed through the live fusion
pipeline at `ANALYSIS_TICK_S`, so on the 100 Hz analysis grid rather than the
2026-08-28 `imu_vs_optitrack_rmse.csv` snapshot that predates it — gives a
like-for-like three-way comparison, every channel scored by the same
`compute_pt_params` at HEAD.

The first number is a trap:

| comparison | \|ΔPT7\| median | r |
|---|---|---|
| phone IMU (production, undetrended) vs OptiTrack (detrended) | 0.447 | +0.438 |
| MediaPipe 2D vs OptiTrack | 0.460 | +0.364 |

Read naively that says the IMU is barely better than MediaPipe, which would
undercut the whole claim path. It is an artefact. The phone scores undetrended
(`imu_calibration_tuner:552`, `pendulastic_app:1795`) and OptiTrack is scored
detrended, so that row compares two different conventions as well as two
different sensors. Controlling for it:

| | \|ΔPT7\| median | r |
|---|---|---|
| IMU vs OptiTrack, **both detrended** | **0.247** | **+0.630** |
| IMU vs OptiTrack, both undetrended | 0.453 | +0.479 |
| convention effect on the IMU alone | 0.363 | — |
| convention effect on OptiTrack alone | **0.050** | — |

Detrending moves OptiTrack's PT7 by 0.050 and the IMU's by **0.363**. OptiTrack
does not drift; the phone does, and the production convention leaves that drift
in the score. Match the conventions and IMU–OptiTrack agreement roughly doubles:
error 0.447 → 0.247, correlation 0.438 → 0.630. MediaPipe does not improve under
any convention, which is the separation the claim path needs.

**This is a measurement, not a recommendation to flip the flag.** `detrend=False`
on the phone was a deliberate decision, and `compute_pt_params`' own drift
correction fits the pre-release baseline, so detrending on top may double-correct
in a way this comparison cannot see. The finding is that the single largest term
in the IMU→PT7 error budget is currently the detrend convention rather than the
sensor, and that deserves a deliberate resolution by whoever owns the scoring
path. Raised with `pendulastic-b` as input to KD6/R12.

## Figures

`Model_Analysis_Outputs/Pipeline_Boundary/`, generated by
`generate_pipeline_boundary_figures.py`. All three are stamped **DRAFT**: they
depend on `MIN_INTERPRETABLE_A0_DEG`, which is under re-derivation (25.0 → 28.0
proposed), and that constant decides which trials appear gradeable.

| figure | what it shows |
|---|---|
| `imu_vs_optitrack_baseline.png` | PT7 agreement with OptiTrack for the IMU and both MediaPipe columns, with the detrend-convention control as its own panel |
| `mediapipe_gate_nonmonotonic.png` | gate pass rate **and** PT7 error against tracking quality — neither improves with better tracking |
| `mint17_case_study.png` | the ten recovered MINT_17 pre trials, on a log A0 axis spanning a 157.9° artefact and a 6.6° collapsed swing |

They are deliberately framed as pipeline boundaries. None of them is a case for
using MediaPipe as a diagnostic.

### Sensitivity to the pending floor change

The proposed 25.0 → 28.0 move costs denominator but not conclusion on this
corpus. It is *not* the "costs nothing" seen on the IMU corpus — optical A0
values pile up right at the boundary, with no clean gap:

| floor | MediaPipe 2D gradeable | world | OptiTrack | both-gradeable n | 2D exact / adjacent |
|---|---|---|---|---|---|
| 25.0 | 166 | 165 | 156 | 92 | 31.5% / 44.6% |
| 28.0 | 162 | 152 | 144 | 82 | 32.9% / 45.1% |

Agreement stays at roughly a third exact and under half adjacent either way, so
the figures' conclusions hold. Only the reported *n* changes.

## Caveat on the composite scores

Every PT7 and MAS value here was computed against `pendulastic_pt_score.py` as
of commit `5d61ab9` — the 100 Hz analysis grid and `HEALTHY_REF["N"]` 5.50. That
work was uncommitted when this pass started and is now committed and clean in the
working tree, so these numbers **are** reproducible at HEAD, contrary to the
provisional labelling this report originally carried.

What remains open is `MIN_INTERPRETABLE_A0_DEG`, still 25.0 in both
`pendulastic_pt_score.py:461` and `mobile-imu-core/src/pt_score.rs:279`, with
28.0 proposed. Its effect on this corpus is measured in the sensitivity table
above: it changes *n*, not the conclusion. Re-run `mediapipe_pt_metrics.py` and
`generate_pipeline_boundary_figures.py` when it lands and drop the DRAFT stamp —
the OptiTrack loads are cached under
`Model_Analysis_Outputs/_cache/mediapipe_pt_metrics/`, so a re-run is a couple of
minutes rather than a quarter of an hour.

### Superseded: the OptiTrack reference did not degrade

An earlier draft of this report treated the 2026-09-01 reference change
(`87d7645`) as a possible regression, on the grounds that IMU-vs-OptiTrack
agreement worsened from 6.05 deg to 21.88 deg across it. **That framing was
wrong and is withdrawn.** Scored against synthetic trials with a known knee
angle, the new loader is better on 3 cases and worse on 0, winning exactly where
the old seeded-axis bug bit — trials that do not start held and extended — and
is regression-guarded at `920f159`. The reference improved.

It has since been validated externally as well: on the 2026-09-08 MINT_4
session, where a clinician graded both legs MAS 0, OptiTrack agrees exactly on
6 of 7 gradeable trials and within one grade on 7 of 7. That is the first time
the reference has been checked against a human rather than another instrument.

Absolute RMSE figures generated **before** 2026-09-01 are therefore the ones not
comparable, not these. Why agreement worsened across a reference that improved
is still unexplained; the untested candidate is that the old seeded axis
anchored to the same pose the IMU's own gravity seed anchors to, making the old
6.05 deg agreement between two correlated errors rather than accuracy.

See `docs/reports/2026-09-08-methodology-comparison-and-the-imu-drift-artefact.md`
for the clinical anchor and for why the IMU's own PT7 grades do not survive it.

## Final state

| | |
|---|---|
| trial videos | 285 |
| with a current-schema MediaPipe CSV | **284** |
| on a stale schema | **0** |
| with no CSV | 1 (`CHAT_21/Left/pre/Trial_3.avi`, unrecoverable) |

## Recommendation

Keep the app IMU-based. This audit reproduces, on the full corpus rather than a
subset, the conclusion already recorded for HPE: the optical angle is fine to
look at and unusable to score. MediaPipe's remaining value is (a) a visual check
via the annotated videos, (b) the only quantitative channel for the 28 trials
with no OptiTrack, understood as indicative rather than diagnostic, and (c)
keypoints for training-data generation.

Two capture-side actions would raise the ceiling more than any modelling change:
frame the whole supine patient laterally (Control_8 is the reference shot, MINT_18
the counterexample), and keep the assessor out of frame during the swing.
