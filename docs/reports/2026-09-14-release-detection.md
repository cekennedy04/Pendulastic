# Release detection: the swing's onset, not the first twitch

**Answer: the release detector was firing before the leg was let go on every
bench trial, and it has been replaced -- identically in Python and Rust -- by
a detector that lands within 22 ms of the raw-gyro onset on 15/15. Scoring
no longer assumes a frequency band; a fast `f` is flagged, never zeroed.
The corpus test-retest is not measurably worse on any parameter and is
better on `f` and `R2n`. What the bench still refuses is not release
detection: it is the tail of a motor trajectory that never rests where the
ring was centred, and it is documented below rather than patched.**

Commits: `d2e2cee`, `05b5a4e`, `c2dfe67` on `fix/optitrack-trial-quality`
(the engine this task named); `3e1ab69`, `6e8b9f3` on
`feat/webapp-workbench-restyle` (the branch the phone app ships from; see
"Two engines" below). Tests: 95 in `tests/test_pt_score.py`, 45 in
`tests/test_imu_calibration_tuner.py`, Rust suite green on both branches,
webapp `node --test` 392/392.

## What was wrong

`_detect_release` / `detect_release` took the median of the first 0.6 s as
the hold level and fired on the first sample 8% of the signal's range away
from it. Replayed through the production Rust path (`mobile-imu-core`
`replay` -> `compute_pt_params`, `examples/score_jsonl.rs` reproduces the
Session_3 manifest to 6 decimals on the deployed revision) the detector
fired before the raw-gyro onset on all 15 bench trials:

| hold length | trials | detector fired |
|---|---|---|
| 1.2-2.6 s | 1, 4, 5, 6, 8, 11, 15 | 0.26-0.51 s early, in the motor's lift into the hold |
| 4.6-12.2 s | 2, 3, 7, 9, 10, 12, 13, 14 | 3.7-10.8 s early, at 0.85-0.95 s |

With the release in the lift, the analysis window starts on the hold, the
tail median lands wherever the trajectory ended, and the oscillation is
measured against the wrong neutral -- `f = 0`, `N = 0`, `R2n` refused.
Trials 1/11 vs 15 got opposite verdicts because the lift's slope differed by
a few tenths of a degree per second, enough to move the 8% crossing.

On the participant corpus (147 scored trials, Python tuner replay) the same
rule fired inside the examiner's lift on ~10 trials (`13_right_post_T2`
at 0.59 s for a release at 7.45 s; `4_left_pre_T2` at 3.39 s for 6.37 s;
`18_left_pre_T2`, `13_right_week_1_post_T5`, ...) and 70-120 ms into the
drop on the rest, which read A0 a few degrees below the held angle.

Two claims in the task brief did not survive measurement:

- `release_idx: 0` on every export is `session-store.js`'s default
  (`releaseIdx = 0`); `app.js` never passes one. It says nothing about
  detection.
- The "frequency ceiling": the 2.7-3.1 Hz Session_4 trials were refused by
  the deployed build because that build replays on the 20 Hz display grid,
  where the smoothing floor (5 samples = 0.25 s) smears a 0.35 s period.
  `fix/optitrack-trial-quality` moved scoring to the 100 Hz analysis grid
  (`5d61ab9`) and those trials score there with either detector. The
  same-sign extremum gap (`fps/3.5`, one full period at 3.5 Hz) *was* a
  real ceiling one octave up -- a synthetic 4 Hz ring scored N 8 for 13
  cycles and 3.7 Hz halved -- and is gone (see "No band, one flag").

## The detector

Both ports, same constants, same comparisons in the same order
(`pendulastic_pt_score._detect_release`, `scoring.rs detect_release`):

1. **Sustained rate.** `v[i]` = |displacement over the 100 ms from sample
   i| / 0.1 s; `vs[i]` = min of `v` over the next 100 ms. A one-sample
   blip or a sub-100 ms pulse never forms a run; tremor nets to nothing
   over the window; drift is far too slow.
2. **First large run.** Candidates are maximal runs with `vs` >= 25% of the
   trial's fastest sustained rate (after the 0.6 s the AHRS needs to
   converge). Relative, so unit-agnostic; *first*, so a handling burst at
   the end of a recording (`14_right_pre_T4`, 23.5 s in) cannot steal the
   release.
3. **Never comes back.** A candidate that departs from a still stretch
   (net displacement over the 0.3 s before its onset under a quarter of its
   own) is accepted only if the signal never crosses back over its hold
   level by more than 5% of range. The examiner's brisk lift into the hold
   (`15_left_pre_T1`: 120 -> 178 deg at 60 deg/s, released 2 s later) is
   rejected by this and the release after it accepted. A candidate that
   follows straight on from motion is the swing continuing. If nothing
   passes, the first run stands.

The reported index is the run's onset: walk back to the last sample whose
100 ms rate was under 20% of the run's peak or under 3x the hold's own
median rate over the preceding second, whichever is larger. Snapping that
forward to the first *moving* sample was tried and reverted: it put the SG
derivative's edge fit on the steepest part of the drop and cost
`omega_max_n` its test-retest (ICC 0.158 -> 0.115). The onset is therefore
left a few still samples early; A0 reads the held angle, and on 1 of 150
corpus trials the hold's noise forms a "peak" that N counts once.

Against the raw-gyro onset (first 100 ms of |omega| > 30 deg/s) on
Session_3: -22 to -1 ms on 15/15. Explicit `release_idx` still bypasses
detection (tested in both ports). A still, drifting trial is still refused.

## No band, one flag

Per the owner's rule (measure everything, exclude nothing, flag what is
unusual), `compute_pt_params` assumes no frequency band: find_peaks'
distance is savgol's 3-sample floor, prominence is the only guard against
counting noise, and a synthetic ring at 3, 4 and 6 Hz scores at its own
frequency with its full cycle count in both engines. `f` above
**`F_PLAUSIBLE_MAX_HZ = 2.0 Hz`** is reported as measured and flagged
`f_out_of_range` -- a new boolean beside `quality_warn` in `PtParams`, the
JSON, the persisted trial record, the export manifest and the result table.
2 Hz is the soft bound because Popovic's healthy knees swing at 0.9-1.1 Hz
and spastic ones slower; nothing in the 150-trial corpus crosses it, and it
is a display constant, not a gate. `unmeasured` keeps its old meaning: no
measurable oscillation (`f = 0`, or fewer than two cycles).

Session_4 (manual, 18 trials; deployed build scored 12): on the shipping
engine all 18 score, `f` 1.03-1.10 Hz on the twelve ~1 Hz swings, and
trials 3, 4, 7, 11, 12, 14 come back at 2.71-2.87 Hz, N 15-18, flagged.
On `fix/optitrack-trial-quality`'s engine the six fast ones score
2.66-2.80 Hz, N 14.5-17.5, flagged; six small-A0 slow ones (2, 6, 13, 16,
17, 18) stay `f` unmeasured there for the drift reason below.

## Bench

Session_3 on `fix/optitrack-trial-quality`'s engine, live convention:

| | old detector | new detector |
|---|---|---|
| release vs gyro onset | -0.26 s .. -10.8 s | -22 .. -1 ms |
| trials with `f` measured (N >= 2, f >= 0.1) | 11 | 12 |
| repeatability block 1/6/11/15: `f` | 1.37 / 1.36 / 1.37 / 1.36 | 1.70 / 1.70 / 1.70 / 1.69 |
| `N` | 2.5 x4 | 2.0 x4 |
| `A0` (SEM, MDC95) | 16.4 (0.29, 1.62) | 16.2 (0.29, 1.61) |
| `R2n` (SEM, MDC95) | 1.37 (0.02, 0.11) | 1.38 (0.02, 0.12) |

Same swing now gives the same score on all four, on both engines' branches
(the deployed engine had scored 1/6/11 and refused 15).

**What still fails, and why it is not release detection.** Trials 3, 7, 12
(and 2, 10, 14, whose `f = 0.17` is a motor sweep counted as a cycle) are
the 19 s recordings: the arm drops, rings for ~1.5 s, and the motor then
drives it back to the top and sweeps it down and up again. The tail median
is not the ring's centre, so the whole ring sits on one side of neutral,
only troughs are counted, and `f` and `N` halve. The same mechanism is why
every Session_3 `f` reads ~1.7 Hz against a 3.2 Hz raw-gyro ring, and why
the `f = 1.64` Session_2 trials are 3.2 Hz too. The `swing_centre` boxcar on
`feat/webapp-workbench-restyle` corrects exactly this: on that engine with
the new detector, Session_3 trials 4/5/8/9/13 score `f` 3.11-3.19,
`N` 11-12, and Session_4 scores 18/18 with `f` 1.03-1.10 Hz on the slow
swings and 2.71-2.87 on the fast ones (deployed build: 12/18). Fixing `f`
for a missing polarity on the other branch means changing the `f` formula
(period from same-sign spacing), which this task ruled out; it is a
one-line change when wanted.

Session_4's six small-A0 slow trials (2, 6, 13, 16, 17, 18) are refused for
`f` on `fix/optitrack-trial-quality` because a 4.5 deg/s upward drift after
the swing puts the neutral above it -- the drift artefact, not the release
(the release is found at 1.84 s, where the drop is). They score on the
`swing_centre` engine.

## Corpus

147 trials scored by both detectors, ICC(1,1) over 38 participant-legs,
formula as `test_retest_reliability.py`; 95% CIs from 2000 bootstrap
resamples of participants. Analysis convention (`detrend=True`):

| parameter | ICC old | ICC new | dICC [95% CI] | MDC95/mean old | new | dMDC% [95% CI] |
|---|---|---|---|---|---|---|
| R2n | 0.345 | 0.400 | +0.055 [-0.09, +0.18] | 109% | 103% | **-6.0 [-13.4, -1.7]** |
| N | 0.783 | 0.770 | -0.011 [-0.05, +0.02] | 62% | 64% | +2.4 [-2.1, +8.4] |
| phi_max_ratio | 0.684 | 0.670 | -0.014 [-0.04, +0.01] | 95% | 96% | +0.7 [-3.5, +5.3] |
| omega_max_n | 0.167 | 0.158 | -0.010 [-0.10, +0.20] | 111% | 108% | -3.1 [-35, +10] |
| omega_min_n | -0.008 | 0.008 | +0.016 [-0.40, +0.12] | 2148% | 612% | (metric is noise either way) |
| f | 0.232 | 0.285 | **+0.047 [+0.001, +0.131]** | 91% | 91% | -0.8 [-4.2, +2.7] |
| area_ratio | 0.516 | 0.575 | +0.060 [-0.02, +0.14] | 154% | 147% | -7.5 [-26, +11] |
| A0_deg | 0.329 | 0.330 | +0.001 [-0.03, +0.05] | 53.0% | 53.0% | -0.0 [-4.0, +4.1] |

Live convention (`detrend=False`): `omega_max_n` +0.116 [-0.06, +0.25],
`f` +0.040 [+0.002, +0.088], R2n MDC -17% [-39, +1]; nothing worse outside
its interval. A0 rises ~12% on ordinary trials (the old crossing lag read it
below the hold), so its absolute MDC95 grows with the scale and MDC95/mean
does not move; the 0.5-cycle N drops on ~25 trials are the amplitude floor
(`0.05*A0`) rising with it and dropping each trial's last, marginal
extremum. Newly scored: `15_left_pre_T1` (lift rejected, A0 63.9) and
`23_left_pre_T1` (release right at 9.25 s, but a second violent event at
12.5 s leaves the tail at 178 deg and A0 = 4.5 -- a tail problem, worth an
exclusion tag).

## Python and Rust agree

Session_3 through both engines on the identical replayed series: A0, R2n,
omega_max_n, omega_min_n to < 1e-6; area_ratio to 1.6e-4; one pre-existing
divergence on trial 14 (N 2.0 vs 1.5, a marginal extremum -- present before
this change). Golden fixtures regenerated from the updated reference; on
`fix/optitrack-trial-quality` the E2E replay series were pinned to what the
Rust replay produces, because the Python replay moved after `751d1a3` and
the port did not follow (pre-existing, separate).

## Two engines, one deploy

The task named `fix/optitrack-trial-quality`'s engine (its line numbers
match). The phone app is built from `feat/webapp-workbench-restyle`
(`.claude/worktrees/webapp-restyle`), whose `scoring.rs` has diverged by
~440 lines (`swing_centre`, a 0.10 s release back-off, the 20 Hz grid). The
detector was ported there as `3e1ab69`, its own golden regenerated, and
`webapp/dist` rebuilt: algorithm version `0.1.0+3e1ab6968c4e.a9fce64e9c9d`.
The production upload was not run from this session; the command is the one
in the deploy notes:

```
cd C:\Users\cladi\Pendulastic\.claude\worktrees\webapp-restyle\webapp
npx vercel deploy --prod --yes --project pendulastic-app "C:\Users\cladi\Pendulastic\.claude\worktrees\webapp-restyle\webapp\dist"
```

## Provenance

`mobile-imu-core/examples/score_jsonl.rs` (both branches) scores exported
JSONL through the production path; `--dump` prints the replayed series.
The bench and corpus scripts (`bench_eval.py`, `corpus_eval.py`,
`paired.py`, `boot.py`) ran from this session's scratch space against a
pickle of the 150 corpus replays; corpus trial discovery via
`batch_imu_vs_optitrack_rmse.discover_trials`, replay via
`imu_calibration_tuner.replay_trial_detailed` at `ANALYSIS_TICK_S`.
