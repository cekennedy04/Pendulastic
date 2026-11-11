# Scorer ports, stage 1

**2026-11-10.** Follows `2026-11-10-scorer-lineage-comparison.md` (on
`docs/scorer-lineage-comparison`), sections 4.1, 3.4 and 7. The owner's decision
was to port only what `main` is missing, verified. This stage is items 1 and 2 of
that list and nothing else.

## Ports applied

Branch `fix/port-drift-bound-and-compare-orientation`, cut from `origin/main` at
`75109c5`. Python only. Not ported, by instruction: the onset release detector,
the frequency cap, the Rust flex projection, `61d5723`, any `HEALTHY_REF` or
score-formula change. `abs()` in the swing-angle extraction is untouched.

| port | source | commit here | files | what changed |
|---|---|---|---|---|
| bounded drift fallback | `4bd13a2` | `3ca6bf4` | `pendulastic_pt_score.py`, `tests/test_drift_correction_bounds.py` | new `_baseline_drift_slope`, `_credible_drift_slope`, `_assert_amplitude_within_curve`, `AmplitudeExceedsCurveError`; `compute_pt_params` calls them where the unguarded `np.polyfit` was |
| oriented comparison | `fb46178` | `4792020` | `workbench_engine.py`, `tests/test_workbench_engine.py` | new `_orient_release_positive`; `compare_pair` orients both curves before release detection and the lag search, and returns `ref_inverted`, `test_inverted` |

Neither cherry-picked cleanly, so both are re-implementations:

- **Drift bound.** Main fits the fallback over the lift-trimmed hold
  (`[baseline_start, baseline_end)`, from `f1584ae`); September fitted
  `[0, baseline_end)`. `_baseline_drift_slope` therefore takes `baseline_start`
  and judges the still hold only. The three guards are September's, unchanged:
  the hold must span 1.0 s, the slope may not exceed 4 deg/s, and the correction
  over the whole trial may not exceed 25% of the curve's range. As in the
  original, the 25% bound is not applied to the settled-tail estimator.
- **Orientation.** Code as in `fb46178`. Left out: that commit's two unrelated
  test repairs (both tests pass on main as they are) and its edit to a plan
  document main does not have. Its fourth test asserted that
  `windowed_pt_params` orients internally, which is not so on main, so the test
  here pins the rule itself.

### Method

Same corpus, trial list and driver as the lineage comparison (`run_lineage.py`,
179 phone trials, 14 participants, 159 with an OptiTrack match, data and
exclusions from the main checkout, folders resolved through
`participant_paths`). Three runs of this branch's code: before (at `75109c5`),
after the first port, after both. The "before" run reproduced the lineage
comparison's `main` results file byte for byte.

### `detrend=False` (the app and rig convention): results unchanged on the corpus

The code path is not untouched. The two drift estimators are never consulted
with `detrend=False`, but `_assert_amplitude_within_curve` runs after A0 under
both conventions, so `compute_pt_params(detrend=False)` can now raise
`AmplitudeExceedsCurveError` where it used to return. It did not on any corpus
trial. (Corrected 2026-11-11; this heading used to read "unchanged".)

Measured, not inferred:

- All 179 per-trial records are identical before and after (162 scored, 17
  unscored on both), every key.
- Run in one process against the same 179 replay curves, the old and new
  `compute_pt_params(detrend=False)` return bit-identical dictionaries on all 162
  scored trials, arrays included, and `compute_pt_score` is bit-identical on all
  162. The new invariant raised on none.
- Replay curves are array-identical on 179 of 179 (neither port touches the
  replay).

### The four A0 outliers, `detrend=True`

| trial key | hold window fitted | fallback slope | correction / range | A0 before | A0 after | A0 `detrend=False` | refused by |
|---|---|---|---|---|---|---|---|
| `4_left_june_post_T3` | 1.26 s | +5.04 deg/s | 1.32 | 234.8 | 105.3 | 105.3 | slope cap |
| `4_left_june_post_T4` | 2.50 s | +2.91 deg/s | 0.68 | 186.2 | 115.5 | 115.5 | total-correction bound |
| `13_left_post_T4` | 0.12 s | -19.22 deg/s | 6.12 | 155.3 | 27.6 | 27.6 | window under 1.0 s |
| `13_right_post_T2` | 0.23 s | -17.97 deg/s | 3.44 | 139.8 | 35.8 | 35.8 | window under 1.0 s |

Each now reads exactly its undetrended value, because a refused fallback means
no correction. Two further trials changed for the same reason
(`17_right_post_T3` 87.1 to 68.4, correction/range 0.26; `13_right_week_1_post_T5`
55.9 to 41.0, correction/range 0.52). The other 156 scored trials are
bit-identical with `detrend=True` as well.

Fallback census on the 162 scored trials: settled-tail slope applied on 123;
the fallback was reached on 39, of which the bounded version applies 33 (largest
slope 1.49 deg/s, largest correction/range 0.244) and refuses 6. A0 above
120 deg: 4 trials before, 0 after. A0 above the curve's own raw range: 11
before, 6 after; the 6 that remain are settled-tail corrections, which the port
deliberately leaves unbounded, and all sit inside the invariant's limit. The
new invariant raised on no corpus trial in either convention.

### Optical curves, `detrend=True`: also affected

The same scorer runs on OptiTrack curves, and the reports that score them use
the `detrend=True` default. On the 155 optical curves the driver saved
(old against new scorer, same arrays):

- `detrend=False`: A0 and neutral unchanged on all 143 scored, unscored on both
  for 12.
- `detrend=True`: unchanged on 131, unscored on both for 8, **changed on 16**.
  The largest: `18_left_pre_T1` 503.4 to 23.1 deg (curve range 70.3),
  `23_right_pre_T4` 168.8 to 26.2 (79.1), `15_left_post_T1` 91.3 to 9.4 (19.1),
  `23_left_pre_T2` 78.7 to 42.7 (70.1). Before, 6 optical curves read an A0
  larger than the curve's whole range; after, none does (145 scored before,
  146 after). Two trials that returned
  nothing now score (`14_left_pre_T3`, `18_left_pre_T4`) and one that scored
  A0 = 4.0 no longer does (`20_right_pre_T1`). Two move up
  (`14_left_pre_T1` 28.4 to 44.7, `13_left_post_T4` 32.6 to 36.5).
- The invariant raised on none, in either convention.

A0 from optical curves is a spasticity grouping input where no MAS grade
exists. The effect of these 16 on leg grouping, and on any cohort table built
from optical A0, was **not measured** here. `4bd13a2` reported one of 13
a0-derived legs changing label on its own lineage.

### A0 test-retest, ICC(1,1), `detrend=True`

Same definition as the lineage comparison (groups of participant, leg and
condition with at least 3 repeats; interval from 2000 bootstrap resamples over
participants).

| trial set | ICC before | ICC after | after minus before [95%] | SEM before | SEM after |
|---|---|---|---|---|---|
| lineage-comparison set, 36 groups, 145 trials, 12 participants | 0.040 | 0.271 | +0.231 [-0.041, +0.450] | 23.8 deg | 12.5 deg |
| every trial this branch scores, 38 groups, 158 trials, 13 participants | 0.059 | 0.298 | +0.239 [-0.042, +0.442] | 22.8 deg | 12.1 deg |

September's figure on the first set was 0.265. The interval on the improvement
still includes zero: the change rests on six trials in a twelve-participant
corpus. The SEM halves. Other parameters on the first set, before to after:
neutral 0.148 to 0.246, A1 0.393 to 0.572, area_ratio 0.278 to 0.361, PT score
0.473 to 0.588; N, f, phi_max_ratio, omega peak and pre-release angle move by
less than 0.03. Under `detrend=False` every ICC is identical (A0 0.291 on the
first set, 0.296 on the second).

### IMU vs OptiTrack RMSE, offset removed

`batch_imu_vs_optitrack_rmse.evaluate_trial`, status ok on 145 of 159 matched
trials before and after.

| trial set | median before | median after | mean before | mean after |
|---|---|---|---|---|
| all 145 ok trials | 21.80 deg | 15.10 deg | 24.69 deg | 14.87 deg |
| lineage-comparison set (134, also ok on September) | 22.04 deg | 14.88 deg | | |

- The first port alone changes no RMSE value (145 of 145 equal); the whole move
  is the second port.
- Lower on 58 trials, higher on 9, equal on 78 (Wilcoxon p = 1.9e-10). The 67
  that changed are exactly the 67 where the optical curve was flagged inverted;
  the IMU curve was flagged on none.
- Trials at or under 10 deg: 28 before, 51 after. 90th percentile 45.7 to
  26.5 deg.
- Against September's own `compare_pair` on the same curves (median 14.68 deg on
  the 134), this branch agrees exactly on 131 trials and differs on 3. The
  remaining 0.2 deg is other harness differences on that lineage, not ported
  here.
- Absolute `rmse_deg` moves the other way (median 143.5 to 158.3 deg). The
  marker path returns a relative curve, so that column carries the missing datum
  rather than agreement, as the lineage comparison notes; mirroring a curve
  changes which side of the datum it sits on.

**Nine trials read higher after the change**, by 2 to 19 deg: six of
participant 15's right leg and one each from participants 4, 13 and 17
(`17_left_post_1_month_T5` 7.3 to 26.1, `15_right_post_1week_T1` 10.3 to 28.7,
`15_right_post_T1` 38.5 to 56.2, `15_right_post_T2` 32.3 to 47.5). September's
harness gives the same values on eight of the nine. On several the optical
curve's start sits only a few degrees below its tail on a range of 150 deg or
more (-7.2 on 186.6, -3.0 on 153.0), so the orientation verdict rests on a small
margin. Whether the rule mis-orients those trials or the optical curves
themselves are poor was not established; nobody has looked at the curves.

### Tests

- `tests/test_drift_correction_bounds.py`: 23 tests (18 from `4bd13a2`, 5 new
  for main's window). Against the unmodified scorer 21 fail; with the port 23
  pass when the corpus is reachable and 19 pass, 4 skip when it is not (the four
  that read OptiTrack files; a worktree has no data).
- `tests/test_workbench_engine.py`: 5 new tests, all failing against the
  unmodified `compare_pair`; 96 pass with the port (91 before).
- The CI python list (`tests/test_web_export_contract.py`, the twelve scoring
  and reporting files, `neuromech/tests`) plus
  `tests/test_rmse_pipeline_common.py`, run in a worktree with no study data:
  718 passed, 28 skipped, 3 xfailed at `75109c5`; the same list on this branch
  with the two files above added: 833 passed, 32 skipped, 3 xfailed, no
  failures. The difference is the new tests (19 passed and 4 skipped in the
  drift file, 96 in the engine file).
- `pytest neuromech/tests -q` on its own: 87 passed, 23 skipped.
- Other files that exercise `compare_pair` or the batch RMSE path
  (`test_batch_imu_vs_optitrack_rmse.py`, `test_ahrs_residual_floor.py`,
  `test_figures_by_spasticity.py`, `test_mediapipe_preprocessing.py`): 60
  passed.
- `node neuromech/labview/verify_formula_node.js`: pass.

### Rust and C#

Read from source; neither was run or changed.

- **C# (`neuromech/designer/PtScore.cs`): no change needed.** It implements
  only `detrend=False` with a given release index, and has no drift correction
  of either kind.
- **Rust (`mobile-imu-core/src/scoring.rs`): has the same unguarded fallback,
  and it is live.** `compute_pt_params` (lines 623-660) falls back to
  `polyfit1` over `[baseline_start, baseline_end)` with only the 10-sample
  minimum, exactly as Python did. It is reached through
  `TrialSession::finish_with(_, true)`, which `wasm.rs` exposes as
  `finish_detrended` and `finish_pt_score_detrended`; `webapp/src/app.js`
  `persistTrial` stores those as the trial record when they are available. So
  the on-screen estimate (`detrend=false`) is unaffected, but the **stored and
  exported** web-app record uses the unbounded fallback and, from this branch
  on, can differ from the Python reference on the kind of trial listed above.
  A matching change would need:
  1. `baseline_drift_slope(t, ang, baseline_start, baseline_end)` in
     `scoring.rs`: finite samples only, at least 10 of them, spanning at least
     1.0 s (`TAIL_MIN_SECONDS`), slope within `MAX_DRIFT_DEG_S`.
  2. `credible_drift_slope(slope, t, ang)`: refuse when
     `|slope| * duration > 0.25 * (max - min)` of the finite curve.
  3. The call site (the `or_else` branch at line 648) to use both, the
     tail estimator left unbounded.
  4. A decision on the A0 invariant: Python raises; the Rust function returns
     `Option`, and a panic inside wasm would take the trial down, so it would
     need to be an error value or be left out.
  5. New golden fixtures from `gen_fixtures.py` for a refused fallback (short
     hold, over-cap slope, over-bound correction) and tests beside
     `lift_ramp_stays_out_of_the_baseline_drift_fit`; then the wasm rebuild.
  The existing golden for this path (`TRIAL_LIFT_RING_DETREND_*`, a flat 2 s
  hold) is unaffected: the ported Python still reproduces it to 4e-14.

### Not run

- Rust (`cargo test`) and the web app: not run, nothing there changed.
- The C# scorer: not run.
- The motor bench.
- No look at the nine trials whose RMSE rose, beyond the numbers above.

## Rust parity

**2026-11-11.** The Rust change the section above lists as needed, on the same
branch, after merging `origin/main` (PRs #87, #86, #91; no conflicts). The
"Not run" list above describes stage 1 as it stood; Rust, the web app and the
C# scorer were run for this section.

### What changed

`mobile-imu-core/src/scoring.rs` (commit `c11f3c0`), each a line-for-line
counterpart of the Python:

| Rust | Python | behaviour |
|---|---|---|
| `baseline_drift_slope(t, ang, baseline_end, baseline_start)` | `_baseline_drift_slope` | finite samples of `[baseline_start, baseline_end)`; `None` under 10 of them, under 1.0 s of span, or with a slope over 4 deg/s |
| `credible_drift_slope(slope, t, ang)` | `_credible_drift_slope` | `None` when `abs(slope) * duration` exceeds 25% of the finite curve's range |
| `amplitude_within_curve(a0, angle_raw)` | `_assert_amplitude_within_curve` | `false` when A0 exceeds `span * (1 + 0.25 + 0.02)` |

Constants: `BASELINE_MIN_SECONDS = TAIL_MIN_SECONDS` (1.0),
`MAX_TOTAL_CORRECTION_FRAC = 0.25`, `MIN_BASELINE = 10` (was a local),
`AMPLITUDE_SLACK_FRAC = 0.02`; `MAX_DRIFT_DEG_S = 4.0` already existed. The
fallback branch calls the first two; the settled-tail estimator is still not
bounded by the 25% rule.

**The A0 invariant.** Python raises `AmplitudeExceedsCurveError`. Rust does not
panic: `compute_pt_params` returns `None`, the refusal it already uses, which
`TrialSession::finish_with` turns into `Err` and `wasm.rs` into `undefined`.
Three consequences worth knowing:

- The refusal carries no reason of its own. It surfaces as
  `TrialError::InsufficientSamples`, the same as every other `None`.
- `persistTrial` falls back to the live-convention params when the detrended
  call returns nothing, and labels the record `driftCorrection: 'live'`. So a
  trial refused only under `detrend=true` is stored with its undetrended
  numbers, honestly labelled, where Python offline has no number at all.
- As in Python, the check runs under both conventions, so `score_waveform`
  (which scores with `detrend=false`) rejects where the Python `score_waveform`
  would raise.

The invariant is reachable without a defect. The settled-tail estimator is
deliberately unbounded, and on a slow sag largely cancelled by drift (tau 4 s,
+2.5 deg/s, 14 s) it applies 1.37 deg/s and A0 reads 43.6 deg against a raw
span of 32.1 deg and a limit of 40.7. That curve is now the golden fixture
`TRIAL_A0_OVER_CURVE`. It is synthetic; no corpus trial does this.

### Agreement, measured

800 synthetic curves (creeping holds, lift-hold-drops and drift-masked sags at
20, 50 and 100 Hz, some with noise and NaN samples; seed fixed), scored by the
Python reference and by the Rust core, both conventions. Among them the tail
answered on 195, the fallback was applied on 168 and refused on 437, and 3
raised.

| | Rust before (`8d9bd5e`) | Rust after |
|---|---|---|
| `detrend=true`: scored-or-not disagrees with Python | 28 of 800 | 0 |
| `detrend=true`: A0 differs by more than 1e-6 deg, of those scored by both | 329 of 739 (worst 244 deg) | 0 of 752 (worst 7.7e-13) |
| `detrend=false`: scored-or-not disagrees | 0 | 0 |
| `detrend=false`: A0 differs by more than 1e-6 deg | 0 of 763 | 0 of 763 |

Where Python raised (3 curves), Rust returned `None`. Neutral, N, R2n and f
agree to 1.5e-12 or better after the change.

**One discrepancy that is not from this change:** `area_ratio` differs between
Rust and Python by up to 1.2e-3 on 34 of the 763 `detrend=false` curves, before
and after alike, with every other compared field agreeing. Not investigated.

### Fixtures

`gen_fixtures.py` gains five cases and `scoring_test.rs` nine tests (commit
`55fbe73`). Each fallback case is a creeping hold and a swing still ringing at
the end, so the tail refuses on its own; the generator asserts that exactly one
guard decides each.

| fixture | hold fitted | slope | correction / range | verdict | A0 detrended | A0 undetrended | A0 with the old fallback |
|---|---|---|---|---|---|---|---|
| `TRIAL_FALLBACK_OK` | 1.45 s | 1.0 deg/s | 0.046 | applied | 73.85 | 71.27 | 73.85 |
| `TRIAL_FALLBACK_SHORT_HOLD` | 0.75 s | 2.0 deg/s | 0.077 | window under 1.0 s | 71.27 | 71.27 | 76.43 |
| `TRIAL_FALLBACK_SLOPE_CAP` | 1.48 s | 5.0 deg/s | 0.228 | slope over 4 deg/s | 70.44 | 70.44 | 84.16 |
| `TRIAL_FALLBACK_OVER_BOUND` | 1.46 s | 3.0 deg/s | 0.289 | correction over 25% | 46.24 | 46.24 | 66.97 |

With the old fallback branch put back, the three refusal tests fail and the
rest pass. `TRIAL_LIFT_RING_DETREND_*` is still reproduced (its hold is flat,
so it reads the same with or without the guards).

`golden.rs` was regenerated with the project venv (numpy 2.4.6, scipy 1.17.1);
the committed file had been written with numpy 2.5.3, scipy 1.18.1. 115
existing numeric constants move, none by more than 7.3e-12
(`GRADIENT_NONUNIFORM`); every `TRIAL_*` scalar moves by under 1e-12, and
`TRIAL_LIFT_RING_DETREND_A0_DEG` by 2.8e-14. Regenerating before and after the
generator edit gives identical values for every existing constant.

### Python callers of `compute_pt_params`

Every call site outside the tests was checked for an enclosing handler (64
sites in 33 files, lexically; second-level callers of the main ones were
checked the same way). Handled in commit `67bb1fe`, as an unscorable trial with
the reason kept: `pt_report_common.trial_candidates` (status `unscoreable`,
reason on the row), `collect_participant` and `_row5_source_pt7` (left out,
named on stderr), and `web/api/services/pt_score_bridge.py` (`ValueError` with
the reason, as for a `None`). `score_trial` itself still raises.

Still unhandled, and left so on purpose or for the owner to decide:

- **Fail closed today, and dropping a trial there is a decision, not a fix:**
  `derive_healthy_ref.control_trials` and `recalibrate_pt_zones.leg_scores`
  derive `HEALTHY_REF` and the zone thresholds. Both already stop on a trial
  they cannot load. They would stop on this too, with a traceback that does not
  name the trial; wrapping the call to add the trial key is all that is
  clearly right.
- **Interactive:** `pendulastic_viewer` (`_compute_and_show_pt`, `_analyse`),
  `pendulastic_workbench.get_metrics_snapshot`, `pendulastic_app` (the
  `detrend=False` live view). `workbench_engine.compute_optitrack_quality_signals`
  is unhandled itself but its one caller catches `Exception`.
- **Inside a broad `except Exception`, so it does not propagate (what each
  handler then does with the trial was not read):**
  `generate_figures_by_spasticity.a0_by_leg_from_optitrack` and
  `imu_leg_params`, `generate_paper_results_analysis.a0_by_leg_from_optitrack`
  (these three feed A0-based leg grouping), `evaluate_healthy_ref_v04`,
  `evaluate_pt7_discrimination`, `evaluate_flex_axis_methods`,
  `evaluate_peak_detection`, `evaluate_settled_tail_gyro_bias.referee`,
  `hpe_mas_evaluation.hpe_pt_score`, `model_vs_optitrack_eval` (OptiTrack
  side), `pendulastic_app._table_worker`, `pendulastic_viewer._release_time`.
  `neuromech/corpus_rest.py` and the C# parity runner also catch it but record
  the error text.
- **`detrend=False` callers** (`imu_calibration_tuner.score_waveform` and what
  calls it, `neuromech/build.py` `score` and `score_angle`,
  `evaluate_capture_bias`, `evaluate_continuity_window`): no drift
  correction runs, so only smoothing overshoot could trip the check. None did
  on 763 synthetic curves or on the corpus; not proven impossible.
- **One-off scripts with `detrend=True` and no handler:**
  `generate_pt_score_critique`, `healthy_ref_frequency_audit`,
  `model_vs_optitrack_eval` (model side), `motor_bench` (`_a0_of`,
  `_a0_of_detrended`), `p13_leg_session_comparison`,
  `plot_comprehensive_parameter_matrix`,
  `plot_multisource_trials`, `run_p5_post_again`, `severity_grading_test`,
  `sweep_mediapipe_config`, `validate_controls`, and `pendulastic_pt_score`'s
  own `main` and `load_hpe_model_data`.

### C#

`neuromech/designer/PtScore.cs` was not changed and has no drift correction.
It also has no A0 check, which Python now runs under `detrend=False` as well;
the parity run compares errors exactly and found none on its 355 cases.

### Gates

Run on this Windows checkout, rustc 1.98.0 (CI pins 1.99.0), Node 24.16.0.

| gate | result |
|---|---|
| `cargo test` | 195 passed, 0 failed (`scoring_test` 24, of which 9 new) |
| `cargo clippy --all-targets -- -D warnings` | clean |
| `cargo build --release --target wasm32-unknown-unknown` | built |
| `npm run build:wasm`, `npm test` | 426 of 426 passed |
| `tests/test_web_export_contract.py` | 5 passed |
| CI python list + `test_drift_correction_bounds.py` + `test_workbench_engine.py` | 740 passed, 32 skipped, 3 xfailed (run before `67bb1fe`) |
| `tests/test_amplitude_refusal_callers.py` + `tests/test_pt_report_common.py`, at `67bb1fe` | 87 passed (7 new) |
| `tests/test_pt_zone_parity.py` | 20 passed, 1 xfailed |
| `neuromech/designer/parity/run_parity.py` | PARITY OK, 355 cases, every field |

`BUILD_ID` is restamped to `0b80def4a471`. Computed from the committed blobs
alone it comes out the same, and the same method reproduces `origin/main`'s
`f2b92312b3b9`; this worktree has no CRLF copies of the hashed files. CI has
not run on it. If `sw-shell.test.js` disagrees, replace `BUILD_ID` with the
value its failure message expects.

### Not verified

- CI itself, including clippy under the pinned 1.99.0 toolchain.
- The corpus through the Rust core: the agreement table is synthetic curves.
  The four phone trials in the table above were not replayed through Rust.
- The web app in a browser, and a stored record from a real capture.
- The full CI python list after `67bb1fe`; only the two files that commit
  touches were rerun.
- The `area_ratio` difference between Rust and Python noted above.
- Whether any real trial can trip the A0 check through the tail estimator.
