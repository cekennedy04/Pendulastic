# Scorer ports, stage 1: independent review

**2026-11-11.** Independent review of branch
`fix/port-drift-bound-and-compare-orientation`, pinned at commit `38b68a9`.
The commits reviewed are `3ca6bf4` (bound the PT scorer's drift-correction
baseline fallback; port of `4bd13a2`) and `4792020` (orient both curves in
`workbench_engine.compare_pair` before aligning; port of `fb46178`). The
author's own report is `docs/reports/2026-11-10-scorer-ports-stage-1.md` on
that branch. The reviewer did not write the change.

Trial keys are `<pid>_<leg>_<condition>_T<n>`. Numbers are per trial or counts
of legs.

## Verdict: approve with required changes

The two commits deserve different answers.

- **`3ca6bf4` (drift bound):** the code is correct and a faithful port. Its
  downstream consequences were not measured by the author and are material, so
  it needs an owner decision before merge.
- **`4792020` (orientation):** a faithful port of a rule that is wrong on a
  specific, identifiable class of trials. It should not merge as is.

### Required changes, in priority order

1. **Fix the orientation rule's start window.** It takes the median of the
   first 5% of finite samples as "the hold". That fails in two cases:
   - **Occluded hold:** the optical curve's first finite sample is at or after
     release, so the window lands in the swing. A synthetic curve starting
     exactly at release is flagged inverted whenever 5% of the recording
     exceeds about a quarter period (a 12 s trial at 0.9 Hz is).
   - **Recording opens at rest:** start is roughly equal to tail, so the sign
     is decided by noise or tail drift. A synthetic rest-lift-hold-release
     curve with +0.3 deg/s drift is flagged inverted, and its mirror with no
     drift is not flagged.

   Orient from the pre-release plateau that `_detect_release` finds, or orient
   the reference relative to the test curve. At minimum, abstain when the
   start window is not a plateau or when |a0| is small relative to range.
2. **Add tests for those shapes.** All five new engine tests use a curve that
   begins on its release peak. None covers a rest or lift lead-in, a missing
   hold, or a pair the rule orients inconsistently.
3. **Get an owner decision on the reference and grouping consequences of the
   drift bound** (numbers in section 4). `derive_healthy_ref.py` no longer
   reproduces the shipped `HEALTHY_REF`, and one a0-derived leg changes label.
   The commit message's "no change to HEALTHY_REF" is true of the constant
   only.
4. **Decide what `AmplitudeExceedsCurveError` should do to callers.** The
   desktop viewer, the workbench, the web API bridge and every batch report
   path have no handler, while the grouping path swallows it silently. Both
   contradict the stated intent of failing loudly without losing the trial.
5. **Correct the report and commit message:**
   - "`detrend=False` never reaches any of this" is wrong for the invariant,
     which runs in both conventions.
   - The optical impact on the population the grouping and reference scripts
     actually use is 24 of 160, not 16 of 155.
   - The nine worse trials are now explained.
6. **Carry `ref_inverted` / `test_inverted` into the batch row.** No caller
   reads them today, so "reported rather than hidden" does not hold at the CSV
   level.
7. **Before relying on it elsewhere, measure the orientation on non-IMU
   consumers.** `compare_pair` is also called by `rmse_pipeline_common.py`,
   `sweep_imu_config.py`, `sweep_mediapipe_*.py`, `pt_report_common.py:1594`
   and the workbench UI. The author measured only IMU versus optical.
8. **Minor:** the 1 deg dead-band is untested (setting it to 0 fails no test).
   `test_orientation_rule_is_start_above_settled_tail` re-implements the
   formula rather than testing behaviour.

## 1. Code correctness

**Drift bound: faithful, no defects found.**

- The helper bodies are line-for-line `4bd13a2`. The only adaptation is
  `baseline_start`, used consistently as `[baseline_start, baseline_end)`.
- Units are right: `|slope| * duration` (deg) against `0.25 * range` (deg),
  with `duration` matching the span the correction is applied over.
- NaN handling is inert in situ because `compute_pt_params` filters to finite
  samples first.
- No off-by-one. The effective minimum hold is about 1.06 s at 100 Hz (1.05 s
  refused, 1.06 s accepted) because of the 0.05 s lag margin and the
  `(N-1)*dt` span. That is conservative, not wrong.
- A 400-case synthetic sweep through the unbounded settled-tail path (A0 5 to
  40 deg, drift +/-0.5 to +/-3.5 deg/s) never raised the invariant.
- On real data the maximum A0/span after the port is 0.946 (160 optical
  curves) and 1.004 (54 IMU replays), against a limit of 1.27.
- The tests test behaviour. In-memory mutations each fail tests: disabling the
  25% bound (4 fail), the 1.0 s window (3), the invariant (2),
  `_credible_drift_slope` (3), and forcing `baseline_start=0` (1).

**Orientation: faithful to `fb46178`, including the sign.** The code is
identical apart from comments. The defect is in the rule itself, not the port.

## 2. The nine trials that got worse

All 145 ok pairs were recomputed with the pre-port engine fed the optical curve
as-is and negated, and these nine were plotted. The recomputed numbers match
the author's before/after on all 145.

| trial | rule a0 / range | RMSE as-is (r) | RMSE flipped (r) | finding |
|---|---|---|---|---|
| `17_left_post_1_month_T5` | -20.7 / 129.7 | 7.3 (+0.95) | 26.1 (+0.36) | **Wrong flip.** Optical hold is occluded; first finite samples are in the swing (start-window span 99% of range). |
| `4_right_pre_T4` | -11.8 / 28.1 | 2.6 (+0.92) | 10.0 (-0.25) | **Wrong flip.** Same cause (start span 100% of range). |
| `15_right_post_1week_T1` | -24.0 / 59.5 | 10.3 (+0.84) | 28.7 (+0.44) | **Wrong flip.** Same cause; optical gappy (36% missing). |
| `15_right_post_1month_T1` | -10.8 / 73.5 | 9.7 (+0.74) | 14.4 (+0.71) | **Wrong flip.** Optical starts at the first trough; the as-is curve tracks the IMU's first drop. |
| `15_right_post_T1` | -7.2 / 186.6 | 38.5 (+0.42) | 56.2 (-0.25) | **Wrong flip, and optical unusable.** Hold sits 7 deg from tail where the IMU hold is 71 deg; the curve is narrow 130 to 170 deg spikes. |
| `15_right_post_T2` | -3.0 / 153.0 | 32.3 (+0.45) | 47.5 (-0.30) | Same as above (hold 3 deg from tail). |
| `15_right_post_1month_T4` | -5.4 / 21.1 | 14.2 (+0.54) | 21.9 (+0.49) | **Optical unusable.** Its range is 21 deg against the IMU's 98; orientation is not decidable. |
| `15_right_post_1week_T3` | -44.4 / 64.5 | 22.8 (+0.43) | 27.0 (+0.04) | **Rule made the pair inconsistent.** Optical starts on its hold (flipped); the IMU starts at rest with its hold 60 deg below rest (a0 +2.5, not flipped). Optical is also 60% gaps. This IMU curve looks upside down and the rule missed it. |
| `13_left_post_T4` | -37.4 / 51.6 | 13.4 (+0.55) | 15.4 (+0.36) | **Flipped correctly by the rule's convention; genuinely poor pair.** Optical releases at about 1.2 s, the IMU plateau lasts to about 3.5 s with a 23 deg range against 52. The 13.4 before was coincidental. |

So six are wrong flips (two of them on optical curves that are unusable
anyway), one is an unusable reference, one is an inconsistent pair, and one is
a correct flip exposing a bad pair.

### How many of the 67 flipped trials sit near the fragile condition

- 9 of 67 are either marginal (|a0| under 10% of range: 4) or have a
  non-plateau start window (first-5% span over 25% of range: 6). In 7 of those
  9 the other orientation fits the IMU better.
- Of the remaining 58, only 2 disagree (`13_left_post_T4`,
  `15_right_post_1week_T3`).
- The same condition holds for 20 of the 78 unflipped trials, and 7 of those
  fit better the other way. Examples are `15_left_post_T1/T3/T4/T5` and
  `15_right_post_1week_T2`, all with start-window span of 57 to 92% of range.
- In total, 17 of 145 decisions disagree with the better-fitting orientation,
  and 14 of those 17 meet the condition.

Lower RMSE alone is a weak oracle on poor curves, so read the 17 as an upper
bound, not a count of confirmed errors.

## 3. The 58 that improved

These are genuine.

- 56 of 58 had negative correlation with the IMU before and positive after; 51
  have r above 0.8 after.
- 16 were plotted across participants 4, 5, 13, 14, 15, 17, 21, 22, 23 and 24.
  In 11 the as-is optical curve is a clean mirror of the IMU and the flipped
  curve overlays it cycle for cycle. Examples: `14_right_pre_T3` 53.7 to 1.5,
  `13_right_post_T1` 48.9 to 3.6, `4_right_june_post_T4` 40.4 to 4.2,
  `24_right_pre_T3` 47.7 to 6.0.
- Nothing is being forced into agreement: the only free parameter is the lag,
  and it stays inside the capture-prior window (median |lag| 0.77 s, max
  1.27 s).

Two caveats:

- `17_right_post_T4` (21.3 to 7.3) came out right by luck. Its a0 is -2.6 on a
  range of 69, with the optical starting mid-swing.
- `22_right_pre_T3`, `21_right_pre_T4`, `14_left_pre_T3` and
  `17_right_post_1_week_T2` improve by only 0.5 to 7.5 deg. Their optical
  curves have 15 to 30% of the IMU's amplitude, so they are poor references
  whichever way up.

## 4. Optical A0 impact under `detrend=True`

The author's 155 curves are the phone-paired set. The grouping and reference
scripts score every baseline optical trial, so the repo's own functions were
run twice (75109c5 scorer and 38b68a9 scorer) over all 160 loadable live
baseline curves (7 more are unloadable; no archive trials were discovered).

### Per trial

- 24 of 160 change: 19 change value, 3 stop scoring (`10_right_pre_T4`,
  `13_left_pre_T2`, `20_right_pre_T1`), 2 newly score (`14_left_pre_T3`,
  `18_left_pre_T4`).
- All 24 now equal their `detrend=False` value.
- A0 above the curve's own span: maximum ratio 7.16 before, 0.946 after.
- Six trials cross the 38.1 deg grouping threshold.
- Interpretability changes on 11 trials: 8 lose it (including
  `10_right_pre_T4`, which stops scoring) and 3 gain it. One more changes
  zone.
- Within-leg A0 range narrows on 12 of the 14 affected legs and widens on 2.
  Examples: `23_right_pre` T1 to T4 go from 32.0 / 35.4 / 57.4 / 168.8 to
  27.1 / 32.9 / 29.4 / 26.2; `18_left_pre_T1` goes from 503.4 to 23.1. The
  direction of the change is credible.

### Grouping

Path: `generate_figures_by_spasticity.a0_by_leg_from_optitrack` into
`spasticity_grouping.classify_leg`; `generate_paper_results_analysis.py` uses
the same path.

- Median A0 changes on 12 of 37 legs.
- 4 of the 14 a0-derived legs have a changed median.
- **1 of 14 a0-derived legs changes label.** Its median moves 48.2 to 27.4
  across the threshold, driven by `18_left_pre_T1` (503.4 to 23.1),
  `18_left_pre_T2` (48.2 to 17.3) and `18_left_pre_T4` (none to 42.5). This
  matches the one-leg change `4bd13a2` reported on its lineage.
- Two other a0-derived legs sit within 5 deg of the threshold after the port
  (-3.4 and +1.1).
- No leg changes label source.

### `derive_healthy_ref.py`

The pre-port scorer reproduces the shipped `HEALTHY_REF` exactly; the
post-port scorer does not. Interpretable control trials go 39 to 37, legs 12
to 11.

| parameter | shipped / before | after | change |
|---|---|---|---|
| phi_max_ratio | 0.6407 | 0.6264 | -2.2% |
| omega_max_n | 6.4839 | 6.6702 | +2.9% |
| omega_min_n | -6.3624 | -6.4839 | -1.9% |
| area_ratio | 0.1766 | 0.1678 | -5.0% |
| f | 0.9511 | 0.9511 | none |
| N | 6.0 | 6.0 | none |
| R2n (literature anchor kept) | 0.9512 | 0.9312 | -0.020 |

### `recalibrate_pt_zones.py`

No threshold moves, because `derive` refuses both before and after (too few
legs in the impaired arm). The inputs do move:

- Legs in the healthy arm 13 to 12 (one leg loses its only interpretable
  trial).
- Interpretable trials 44 to 45.
- Healthy-arm 75th percentile 0.1362 to 0.1434.
- Impaired-arm median 0.356 to 0.312.
- Five legs' median PT7 change.

### Other consumers

`mas_validation.py` and `pt_cohort_common.py` reach the same scorer through
`pt_report_common.collect_participant` / `score_trial` with detrending on, so
their per-leg PT7 medians move on the same trials. Those two reports were not
rerun.

## 5. `AmplitudeExceedsCurveError`

It fired on nothing: 160 optical and 54 IMU curves, both conventions, plus the
synthetic sweep. Nothing in the repo catches it by name. If it did fire:

**Unhandled, aborts the whole run or action:**

- `pt_report_common.score_trial` (called at lines 582 and 1247), and through
  it `collect_participant`. That takes down `mas_validation.py`,
  `pt_cohort_common.py`, `run_pt_analysis.py`, `p5_full_report.py`,
  `p13_full_report.py`, and the app's report tab
  (`pendulastic_app.py:2658-2674`). One trial kills the participant's or
  cohort's report.
- `derive_healthy_ref.control_trials` and `recalibrate_pt_zones.leg_scores`.
- `pendulastic_viewer.py:4259/4266/8716/8717` and
  `pendulastic_workbench.get_metrics_snapshot:665` (Tk callbacks; the metrics
  table or export fails for that view).
- `workbench_engine.compute_optitrack_quality_signals:942`.
- `web/api/services/pt_score_bridge.py:52` (reached from
  `routers/trials.py:228`; whether the router maps exceptions was not
  checked).
- `pendulastic_pt_score.main` (4530/4556), `generate_pt_score_critique.py`,
  `plot_multisource_trials.py`, `validate_controls.py:227`,
  `model_vs_optitrack_eval.py:706`, `severity_grading_test.py`,
  `motor_bench.py`.
- `pendulastic_app.py:1795` catches only `TypeError`; it uses
  `detrend=False`, where the invariant still runs.

**Silently swallowed by `except Exception`, so the trial vanishes:**

- `generate_figures_by_spasticity.a0_by_leg_from_optitrack:105` and
  `generate_paper_results_analysis.py:175`. This is the grouping input, where
  the error is meant to be loudest.
- `imu_leg_params:242`, `pendulastic_app._table_worker:2347`,
  `hpe_mas_evaluation.py`, `neuromech/corpus_rest.py`, and several
  `evaluate_*` scripts.

## 6. "`detrend=False` is bit-identical": true on the sample

54 replay curves were regenerated from the raw CSVs with the reviewed code: all
six trials the bound changed, plus 48 drawn at random.

- All 54 curves are byte-identical to the author's saved ones.
- `detrend=False`: 47 dictionaries bit-identical on every key including
  arrays, with `compute_pt_score` identical; 7 are None on both; 0 differ.
- `detrend=True`: 41 identical, 7 None on both, 6 differ. The 6 are exactly
  the author's six, with the same A0 values (for example
  `4_left_june_post_T3` 234.8 to 105.3).
- All 144 scored baseline optical curves are also unchanged under
  `detrend=False`.

## 7. Other points

- The first 5% window is a fraction of recording length, so the rule's
  behaviour depends on how long the tail was recorded.
- Raw `rmse_deg`, `bias_deg` and the limits of agreement are now in a mirrored
  frame on flipped pairs. That is harmless for this corpus (all 155 references
  are `marker_pca`, with no absolute zero) but wrong for a rigid-body
  reference if the rule ever flips one.
- The Rust scorer still has the unbounded fallback on the stored web-app
  record. The author flagged it; the in-progress Rust commits on the branch
  were not reviewed.

## What was verified, and how

- **By running:**
  - both-orientation comparison on all 145 pairs, and plots of 25 trials;
  - the 54-trial bitwise check from raw CSVs;
  - old versus new scorer on 160 baseline optical curves, through the repo's
    own grouping, healthy-reference and zone functions;
  - the new test files (19 passed and 4 skipped; 96 passed) and nine in-memory
    mutations;
  - synthetic probes of the invariant, the hold-window boundary and the
    orientation rule;
  - an AST scan of every `compute_pt_params` call site.
- **By reading:** both diffs against the September originals, the author's
  report, and the handler context at the main call sites.
- **Not verified:**
  - the lineage-comparison document (not read);
  - the ICC table;
  - the full CI suite;
  - `mas_validation.py` and `pt_cohort_common.py` end-to-end outputs;
  - MediaPipe or other non-IMU `compare_pair` consumers;
  - Rust, C# and the web app;
  - whether the FastAPI router converts the exception.
