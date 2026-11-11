# Gravity-seed fix (PR #92): corpus check, before versus after

Run 2026-11-10 and 2026-11-11. Before = `origin/main` at `75109c5`. After =
`origin/feat/synthetic-trial-generator` at `76dca49`. The only Python difference
between the two that the replay path executes is the one line in
`pendulastic_imu_server._gravity_seed` (`[1+gz, -gy, gx, 0]` to
`[1+gz, gy, -gx, 0]`). Python reference only; the Rust core was not replayed
(see "Not verified").

## Verdict

**The seed fix is an improvement to the angle trace, and it is not a small or
start-only change: it moves every scored trial.** It should not be merged as a
neutral bug fix. It changes PT scores and verdicts on real data, so anything
calibrated or reported from the old traces needs to be re-derived.

What the data supports, in order of how sure it lets us be:

1. **Measured, unambiguous.** The premise "the error is worked off early" does
   not hold on this corpus. The phone is not near flat at the start of a trial:
   the median start tilt is 100 deg, so the old seed started the filter a
   median 145 deg wrong about gravity, and the filter was still a median
   131 deg wrong when the recording ended. With the new seed that error is a
   median 0.03 deg at the end. The old filter never converged inside a trial.
2. **Measured, unambiguous.** The trace where the limb is at rest is flatter
   with the fix. Tail slope (0 is correct for a settled leg): median 0.92 deg/s
   before, 0.33 deg/s after; smaller on 129 of 161 trials, larger on 31.
3. **Measured, weaker reference.** Parameters that do not depend on the optical
   curve's gain agree better with the optical curve after the fix (f, N, R2n,
   PT score), with one parameter mixed (phi_max_ratio).
4. **Measured, and neutral.** The named gate, IMU-versus-OptiTrack RMSE with
   the offset removed, does not move: median 15.10 to 15.06 deg, paired median
   change +0.02 deg. The gate compares the active swing window, which is where
   the two traces are nearly identical, so it has little power to see this
   change in either direction.
5. **Measured, mixed.** Test-retest ICC improves for most parameters under
   `detrend=False`, but two results go the other way and are listed below.

A regression in the trace is not supported by anything measured here. A
regression in individual scores cannot be ruled out, because there is no
per-trial ground truth for the score: 23 verdicts change under `detrend=False`
and the data can say the trace is physically better, not that each new verdict
is the right one.

## Does the offline gate exercise the change?

Yes. `imu_calibration_tuner.replay_trial` seeds each role's filter from the
first accelerometer sample with the changed function
(`imu_calibration_tuner.py:400-401`, `st.ahrs.q = _gravity_seed(v)`, imported
from `pendulastic_imu_server` at line 21), and the corpus config has
`gravity_seed: true`. The runner asserts the tuner's `_gravity_seed` is the
worktree's own. The measured seed error (angle between the filter's predicted
gravity and the first accelerometer sample) is 25.7 to 179.6 deg before and
0.00 deg after on every trial.

## Method

- Two worktrees, one per side, each importing only its own modules (asserted).
  Study data read in place from the main checkout; nothing there was modified.
- One trial list for both sides, from `batch_imu_vs_optitrack_rmse.discover_trials()`
  (participant folders resolved through `participant_paths`). Both worktrees
  discover the identical list.
- Per trial: `rmse_pipeline_common.reconstruct_trial` then
  `imu_calibration_tuner.replay_trial(samples, config, tick_s=ANALYSIS_TICK_S)`
  with the corpus `imu_calibration_config.json` (beta 0.041, ema_alpha 0.5,
  flex_axis_capture on, gravity_seed on, method relative), then
  `pendulastic_pt_score.compute_pt_params` under `detrend=False` (app and rig
  convention) and `detrend=True`, `compute_pt_score`, and `mas_estimate` for
  the verdict (zone, or refused).
- Start tilt: angle between the mean accelerometer vector over the first 0.5 s
  and the sensor z axis. The first single sample (what the seed uses) gives the
  same distribution.
- Filter gravity error: `MadgwickAHRS.update` wrapped to record, after each
  update, the angle between the filter's predicted gravity and the
  accelerometer vector it was given.
- IMU versus OptiTrack: `batch_imu_vs_optitrack_rmse.evaluate_trial`, with the
  curves it compared captured and re-scored by **one harness for both sides**.
  The headline uses `workbench_engine.compare_pair` from commit `4792020`
  (the port that orients both curves before aligning). The same curves through
  main's own `compare_pair` (no orientation step; identical code in both
  worktrees) are reported alongside. The optical curve is identical on both
  sides for every trial where it loads.
- Test-retest: one-way random ICC(1,1) over participant-leg-session groups with
  at least 3 repeats, same trials on both sides; interval from a 2000-draw
  bootstrap over groups.
- "Changed" means an absolute difference above 1e-9.

## Trial counts

| | count |
|---|---|
| Phone trials discovered (14 participants) | 179 |
| with an OptiTrack match | 159 |
| replay yields no trace on either side (motion onset never captured) | 12 |
| no accelerometer stream, so no seed; traces bit-identical on both sides, unscored on both | 4 |
| seeded trace on both sides | 163 |
| `compute_pt_params` returns parameters, `detrend=False`: before / after / both | 162 / 161 / 161 |
| `compute_pt_params` returns parameters, `detrend=True`: before / after / both | 162 / 162 / 161 |
| OptiTrack comparison status ok: before / after / paired | 145 / 146 / 145 |

All 179 recordings are single-phone.

## Results

### 1. Start tilt and filter state

| (deg) | min | p10 | median | p90 | max |
|---|---|---|---|---|---|
| Start tilt, n=175 | 12.4 | 64.2 | 100.5 | 120.1 | 159.1 |
| Seed gravity error, before | 25.7 | 109.5 | 145.0 | 172.3 | 179.6 |
| Seed gravity error, after | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Filter gravity error 1 s in, before (n=163) | 21.7 | 108.8 | 145.4 | 172.4 | 178.0 |
| Filter gravity error 1 s in, after | 0.0 | 0.0 | 0.2 | 0.6 | 4.7 |
| Filter gravity error, last 0.5 s, before | 0.0 | 90.2 | 130.7 | 166.9 | 178.7 |
| Filter gravity error, last 0.5 s, after | 0.0 | 0.0 | 0.0 | 1.9 | 13.6 |

Tilt bins: under 30 deg 2 trials, 30-60 13, 60-90 36, 90-120 105, 120 and over
19. The seed error is twice the tilt folded at 180 deg, so most trials start
near the worst case. The old filter's error fell at a median 1.0 deg/s over a
median 13 s recording; 158 of 163 trials were still more than 45 deg wrong at
the end. The start is not perfectly still on every trial (gyro above 0.2 rad/s
within the first 0.5 s on 46 trials), which is why the after-side error is not
exactly zero later on.

### 2. Where the two traces differ

The output angle is measured relative to the orientation captured at motion
onset, so the two traces are pinned together at release and drift apart
afterwards. The effect is the reverse of "confined to the start".

| absolute difference, after minus before (deg) | n | median | p90 | max |
|---|---|---|---|---|
| first 5 s of recording, max | 167 | 1.49 | 4.61 | 35.6 |
| first 5 s of recording, RMS | 167 | 0.55 | 2.20 | 5.7 |
| pre-release, max | 162 | 1.47 | 4.69 | 35.6 |
| at release | 161 | 0.02 | 0.07 | 6.8 |
| release + 1 s | 161 | 0.00 | 0.32 | 6.7 |
| release + 2 s | 161 | 0.00 | 0.43 | 5.7 |
| release + 5 s | 158 | 0.57 | 4.16 | 13.8 |
| release + 8 s | 129 | 1.65 | 7.76 | 23.8 |
| release + 10 s | 86 | 2.46 | 10.54 | 24.0 |
| swing (release to end), max | 162 | 5.41 | 15.1 | 64.5 |
| swing, RMS | 162 | 2.29 | 6.73 | 30.6 |

Swing maximum above 1 deg on 154 of 162 trials, above 5 deg on 86. A straight
line fitted to the difference over the swing has a median absolute slope of
0.42 deg/s (p90 1.80). Release is a median 2.6 s into the recording. The size
of the difference does not track tilt across trials (Spearman -0.04), which is
expected when almost every trial is near the worst case.

Slope of the trace where the limb should be at rest (0 is correct):

| window | n | median abs slope before | after | after smaller / larger | Wilcoxon p |
|---|---|---|---|---|---|
| last 25% of post-release | 161 | 0.92 deg/s | 0.33 | 129 / 31 | 1e-15 |
| last 2 s of recording | 161 | 0.98 | 0.27 | 129 / 28 | 1e-14 |
| last 2 s, quiet tails only (under 2 deg peak-to-peak once detrended) | 118 | 0.88 | 0.17 | 107 / 7 | 2e-19 |
| hold, 1.5 to 0.2 s before release | 161 | 0.59 | 0.36 | 93 / 66 | 0.001 |

The signed median tail slope goes from -0.73 to -0.07 deg/s: the old trace
drifts in a consistent direction after the swing, the new one does not.

### 3. Scored parameters

Every trial with parameters on both sides changes at least one scored
parameter and its PT score (161 of 161, both conventions).

`detrend=False`, n=161, change = after minus before:

| parameter | median before | median after | median change | median abs | p90 abs | max abs | changed |
|---|---|---|---|---|---|---|---|
| release time (s) | 2.59 | 2.60 | 0 | 0.01 | 0.04 | 6.73 | 104 |
| pre-release angle (deg) | 179.30 | 179.33 | -0.03 | 0.08 | 0.39 | 6.92 | 160 |
| A0 (deg) | 63.97 | 62.60 | -1.71 | 4.37 | 12.2 | 52.2 | 161 |
| A1 (deg) | 89.11 | 87.51 | -0.01 | 0.23 | 14.8 | 78.9 | 154 |
| N | 5.5 | 5.5 | 0 | 0 | 1 | 8 | 59 |
| f (Hz) | 0.936 | 0.946 | +0.003 | 0.004 | 0.078 | 1.89 | 130 |
| R2n | 0.842 | 0.877 | +0.015 | 0.043 | 0.184 | 2.02 | 155 |
| phi_max_ratio | 0.470 | 0.451 | -0.006 | 0.021 | 0.121 | 0.92 | 147 |
| omega_max_n | 4.72 | 4.93 | +0.13 | 0.27 | 1.09 | 12.2 | 161 |
| area_ratio | 0.0654 | 0.0642 | -0.002 | 0.005 | 0.185 | 0.85 | 161 |
| neutral (deg) | 115.3 | 117.0 | +2.03 | 4.20 | 11.7 | 51.6 | 161 |
| PT score | 0.158 | 0.146 | -0.005 | 0.009 | 0.186 | 0.995 | 161 |

Counts above a practical size, `detrend=False`: A0 by more than 1 deg on 135
trials, 5 deg on 69, 10 deg on 28. PT score by more than 0.01 on 78, 0.05 on
31, 0.1 on 23; down on 110, up on 51. Release time by more than 0.5 s on 8.
N by more than 1 on 8. f by more than 0.05 Hz on 22.

`detrend=True`, n=161: A0 median change +1.68 deg (median abs 4.52, p90 17.6,
max 188); pre-release angle median abs 1.93 deg (p90 7.9); PT score median
change +0.002 (median abs 0.011, p90 0.181, max 1.29), more than 0.01 on 86,
0.05 on 30, 0.1 on 19; up on 95, down on 66. Release time is detected on the
raw trace and is the same as above.

The mechanism is the tail: the settled value (neutral) moves by a median
4.2 deg, and A0 and the ratio parameters are measured against it. The release
transient and the first swings are nearly unchanged.

Verdict transitions over all 179 trials (zone from `mas_estimate`; "refused" is
an explicit refusal; "no params" is `compute_pt_params` returning nothing):

| | `detrend=False` | `detrend=True` |
|---|---|---|
| unchanged | 155 | 159 |
| refused to scored | 10 (5 impaired, 4 borderline, 1 healthy) | 10 (4 impaired, 4 borderline, 2 healthy) |
| scored to refused | 0 | 3 |
| refused to no params | 1 | 1 |
| no params to refused | 0 | 1 |
| impaired to borderline | 9 | 2 |
| borderline to healthy | 3 | 0 |
| healthy to borderline | 1 | 2 |
| borderline to impaired | 0 | 1 |
| totals before: healthy / borderline / impaired / refused | 86 / 35 / 22 / 19 | 92 / 36 / 15 / 19 |
| totals after | 89 / 46 / 18 / 8 | 92 / 41 / 17 / 12 |

Under the app convention the refusals drop from 19 to 8 and the net movement
of zones is toward less severe (12 trials one zone milder, 1 one zone more
severe).

### 4. IMU versus OptiTrack, offset removed

Paired, n=145, same optical curve both sides.

| harness | median before | median after | mean before | mean after | median paired change | improved | worsened | within 0.01 deg | Wilcoxon p |
|---|---|---|---|---|---|---|---|---|---|
| `compare_pair` at `4792020` (orients both curves) | 15.10 | 15.06 | 14.87 | 15.19 | +0.02 | 62 | 77 | 6 | 0.13 |
| main's `compare_pair` (no orientation) | 21.80 | 21.63 | 24.69 | 24.78 | +0.02 | 64 | 76 | 5 | 0.40 |

Absolute paired change: median 0.18 deg, p90 0.63 deg. Changes above 1 deg,
oriented harness: 2 improve (largest -3.66), 4 worsen. The four that worsen:

| trial | before | after | note |
|---|---|---|---|
| 14_left_pre_T2 | 5.32 | 46.46 | The after trace settles above its starting value, so the harness's orientation rule mirrors it. Main's harness gives 5.32 to 5.70. Not a pendular trial: refused before, unscorable after. |
| 21_right_pre_T2 | 0.75 | 6.59 | The compared window changes from 116 to 205 samples at 60 Hz, so the two numbers do not cover the same stretch. Before covers mostly the hold. In the tail the optical curve rises about 3 deg, the after trace rises about 5 deg, the before trace falls about 12 deg. |
| 14_left_pre_T4 | 23.79 | 25.43 | Same window, same lag. |
| 14_left_pre_T1 | 8.91 | 9.98 | 23.93 to 33.67 on main's harness, with the lag moving 0.3 s. |

The compared window changes by more than half a second on 23 of the 145
trials, because the comparison trims each pair to its active oscillation
window. On the 122 where it does not, the median paired change is still
+0.02 deg.

Why the gate reads neutral: the comparison window is the active swing, which
is the stretch just after release where the two IMU traces differ by a median
0.00 deg at 1 to 2 s. The difference lives in the tail the window discards.

Gain-free check against the same optical curves, scored with the same scorer
(`detrend=False`), absolute difference between the IMU parameter and the
optical parameter, n=133:

| parameter | median before | median after | after closer | after farther | Wilcoxon p |
|---|---|---|---|---|---|
| f (Hz) | 0.035 | 0.026 | 76 | 30 | 3e-06 |
| N | 1.5 | 1.0 | 35 | 10 | 9e-05 |
| R2n | 0.182 | 0.137 | 85 | 43 | 9e-05 |
| area_ratio | 0.086 | 0.058 | 70 | 63 | 0.04 |
| phi_max_ratio | 0.160 | 0.155 | 54 | 69 | 0.17 |
| PT score | 0.097 | 0.083 | 84 | 49 | 2e-04 |

On the 77 trials where the optical curve itself is interpretable, f, N, R2n
and PT score still move closer; phi_max_ratio moves farther (30 closer, 44
farther, p 0.03). The IMU verdict equals the optical verdict on 41 of 77
before and 48 of 77 after.

### 5. Test-retest ICC(1,1)

38 groups with at least 3 repeats, 157 trials, 13 participants.

`detrend=False`:

| parameter | ICC before | ICC after | change [95% bootstrap] | SEM before | SEM after |
|---|---|---|---|---|---|
| A0 (deg) | 0.312 | 0.508 | +0.196 [+0.044, +0.324] | 11.5 | 8.8 |
| A1 (deg) | 0.522 | 0.585 | +0.064 [-0.106, +0.268] | 22.6 | 17.6 |
| N | 0.720 | 0.736 | +0.016 [-0.045, +0.105] | 1.33 | 1.19 |
| f | 0.265 | 0.468 | +0.203 [-0.120, +0.565] | 0.324 | 0.262 |
| R2n | 0.450 | 0.381 | -0.070 [-0.318, +0.224] | 0.254 | 0.160 |
| phi_max_ratio | 0.573 | 0.699 | +0.127 [+0.003, +0.258] | 0.140 | 0.106 |
| omega_max_n | 0.460 | 0.583 | +0.123 [-0.052, +0.272] | 1.17 | 0.65 |
| area_ratio | 0.516 | 0.290 | -0.226 [-0.513, +0.413] | 0.147 | 0.117 |
| PT score | 0.639 | 0.440 | -0.198 [-0.366, +0.158] | 0.209 | 0.183 |

The within-group spread (SEM) falls for every parameter. PT-score ICC falls
all the same, with an interval that includes zero. A likely reason, not
isolated: before, a set of refused trials sat near a score of 1.5 and carried
much of the between-group variance, and after the fix most of them score
lower. Restricted to the 139 trials (36 groups) that are
interpretable on both sides, PT-score ICC is 0.628 before and 0.608 after
(change -0.020 [-0.167, +0.131]), SEM 0.092 to 0.080, and R2n (+0.223
[+0.067, +0.358]) and omega_max_n (+0.171 [+0.035, +0.334]) improve with
intervals clear of zero. area_ratio ICC still falls there (0.611 to 0.340,
[-0.596, +0.213]) while its SEM falls.

`detrend=True`, all 157 trials: A0 0.057 to 0.162, A1 0.405 to 0.600, N 0.697
to 0.739, f 0.355 to 0.359, R2n 0.272 to 0.360, phi_max_ratio 0.587 to 0.703,
omega_max_n 0.210 to 0.392, area_ratio 0.362 to 0.350, PT score 0.499 to 0.452;
every interval includes zero. Restricted to the 133 trials interpretable on
both sides, PT score 0.496 to 0.599 [-0.042, +0.178], but **R2n falls from
0.603 to 0.270 (change -0.333 [-0.459, -0.143]) with its SEM rising 0.086 to
0.134**. That is the one result here whose interval excludes zero in the wrong
direction.

## Trials that got worse

- OptiTrack RMSE, above 1 deg worse: the four in the table in section 4. Two
  of the four are artefacts of the comparison (orientation flip, different
  window).
- Lost a score: 14_left_pre_T2 goes from an explicit refusal to no parameters
  under both conventions. Under `detrend=True` three trials go from a zone to a
  refusal (21_right_pre_T4, 13_right_month_post_T3, 13_right_month_post_T4).
  None do under `detrend=False`.
- Tail slope larger after on 31 of 161 trials.
- Zone more severe: 1 trial under `detrend=False`, 3 under `detrend=True`.

## Inference, not measurement

- That the new verdicts are the correct ones. The traces are physically
  better; there is no per-trial truth for the score in this check.
- That the old trace's tail drift is the filter's leftover gravity correction
  leaking onto the flexion axis. It fits everything measured (the filter is
  far from converged throughout, the drift is one-signed, and it vanishes with
  the fix) but the leak itself was not isolated.
- That the `detrend=True` R2n result comes from the drift correction having
  been tuned against traces that drifted. Not tested.
- That reference values and zone thresholds derived from old IMU traces need
  re-deriving. This check did not establish which stored numbers came from IMU
  traces and which from optical curves.

## Not verified

- **The Rust core** (`mobile-imu-core/src/ahrs.rs`), the wasm build and the
  app. Only the Python reference was replayed on the corpus. Python and Rust
  parity on real trials after the change was not checked.
- **The live path** (`pendulastic_imu_server` on a streaming phone). Only
  offline replay of recorded logs.
- **Agreement with clinical grades.** No comparison of before and after scores
  against MAS, and no re-run of `recalibrate_pt_zones.py`.
- **Two-phone trials.** The corpus has none; the relative-quaternion path was
  not exercised.
- **The filter's gravity error at the moment of release.** It was recorded at
  1 s and over the last 0.5 s, which bracket release; the at-release value was
  not extracted.
- **The 12 trials with no trace and the 4 with no accelerometer stream** carry
  no information about the fix.
- **Optical reference quality.** The optical curves were taken as loaded. The
  median offset-removed RMSE of 15 deg is far larger than the 2 to 5 deg the
  seed changes, so the RMSE gate could not have detected a regression of that
  size either.
- **Sensitivity to config.** One config only (the corpus
  `imu_calibration_config.json`); no sweep of beta, which sets how fast the old
  filter converged.
- **The synthetic-trial generator and the other changes in PR #92.** Out of
  scope here.
- No test suite was run on either side for this check.

## Reproducing

Scripts and per-trial outputs are outside the repo (they read study data). The
drivers are `run_seed.py` (per-side replay and scoring), `cross_seed.py` (one
harness over both sides' curves), `grav_seed.py` (filter gravity error),
`analyze_seed.py` and `extra_seed.py` (the tables above).
