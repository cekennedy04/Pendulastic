# What the app's smoothing filter costs and what it buys (2026-11-25)

Dated note. Analysis only: no filter, scorer, core or app code is changed, and the scoring
footing (`ref-100hz-ema0.5-flexrec`) is untouched. Aggregate results only; nothing here
names a trial or a participant, and nothing is a clinical claim.

Code: `smoothing_filter_cost_benefit.py` (tests in
`tests/test_smoothing_filter_cost_benefit.py`, which need no participant data). It reads
the recordings in place and writes nothing to the repository.

## Recommendation

**Change: propose scoring the replay with no smoothing (`ema_alpha` 1.0). Do not adopt it
from this note; decide it on the encoder bench, as its own versioned footing change.**

- The filter's cost is real and is the same wherever it is measured. It passes 0.91 of a
  1 Hz swing and delays it 65 ms. On the paired corpus the app angle's gain against
  shank-plate rotation is 0.93 with it and 0.99 without, and the error a reader actually
  gets (no extra time shift) is 8.1 deg median with it and 5.2 deg without.
- What it buys is small. On still segments of real recordings it lowers jitter from
  0.05 deg RMS to 0.02 deg. Neither comes near the 1 deg an extremum needs before the
  scorer counts it (largest still-segment wiggle after the scorer's own smoothing:
  0.5 deg unsmoothed), and the scorer already smooths over 0.10 s itself. With synthetic
  sensor noise at the default level, noise alone changes N on 0 of 40 recordings with the
  filter and 2 of 40 without; at 4x, 7 and 12 of 40; the PT score moves by at most 0.02
  either way.
- At the level of the PT score the two are close: removing the filter lowers PT by a
  median 0.017, moves 6 of 143 scores by more than 0.05 and 2 of 136 verdicts. Agreement
  with the gap-filled optical verdict is the same within the two or three trials that
  separate the candidates (115 of 120 with, 114 of 122 without). So the case for a change
  is the tracking, which is the supervisor's first goal, not the score.
- The bench makes it urgent. The free-pendulum plan's proposed criterion A3 is gain 0.97
  to 1.03. Its pendulum swings at 0.81 to 0.88 Hz, where the shipped filter alone gives
  0.93 to 0.94. Signal A fails A3 by construction unless the filter changes or the
  criterion is written around it. That has to be settled before session S1, when the
  limits are fixed. The bench keeps raw exports, so the same swings can be replayed with
  and without the filter at no extra rig time.
- If some smoothing is wanted, the one that costs nothing measurable is a zero-phase
  6 Hz Butterworth in the replay (gain 0.99 or better up to 2 Hz, no lag). It needs new
  filter code in every port; no smoothing needs none. A shorter EMA is a half measure
  (20 ms: inside the gain limit up to 2.0 Hz, 15 ms lag). Compensating the shipped EMA's
  lag keeps its amplitude loss; running it forwards and backwards doubles the loss.

**Found on the way, larger than the question asked (section 5):** the corpus recordings
are replayed on the time each sample was *received* by the desktop, and samples arrive in
bursts about 90 ms apart. So 79 % of the replay's 10 ms ticks inside a swing repeat the
tick before, and the unsmoothed angle is a staircase (3.2 deg about its own smooth curve).
On the corpus the EMA is mostly smoothing that staircase, which the app's own recordings
do not have. Replaying the same recordings on the phone's own timestamps moves the
*shipped* PT score by more than 0.1 on 5 of 143 trials, which is more than removing the
filter does. Not fixed here, and not a proposal yet: it needs its own look.

## What was compared

Each recording is replayed twice by `imu_calibration_tuner.replay_trial` at 10 ms ticks:
once with `REFERENCE_REPLAY_CONFIG` as it stands (the shipped angle), once with only
`ema_alpha` set to 1.0 (the unsmoothed angle). Every candidate is applied to that same
unsmoothed angle, so the AHRS, zero pose and flexion axis are identical across candidates.
This script's own EMA of the unsmoothed replay reproduces the shipped replay to 3e-14 deg
on every corpus trial and every synthetic recording.

| Smoothing | Live? | Gain 0.8 Hz | 1 Hz | 1.5 Hz | 2 Hz | 3 Hz | Lag at 1 Hz (ms) | Within 0.97-1.03 up to (Hz) |
|---|---|---|---|---|---|---|---|---|
| EMA 72 ms (shipped: alpha 0.5 at 50 ms, 0.129 at 10 ms) | yes | 0.940 | 0.911 | 0.827 | 0.741 | 0.593 | 63 | 0.55 |
| no smoothing | yes | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 | 6+ |
| EMA 50 ms | yes | 0.970 | 0.954 | 0.905 | 0.847 | 0.729 | 44 | 0.75 |
| EMA 36 ms | yes | 0.984 | 0.976 | 0.947 | 0.912 | 0.829 | 31 | 1.10 |
| EMA 20 ms | yes | 0.995 | 0.992 | 0.983 | 0.970 | 0.937 | 15 | 2.00 |
| EMA 10 ms | yes | 0.999 | 0.998 | 0.996 | 0.993 | 0.984 | 6 | 4.15 |
| EMA 72 ms, lag-compensated (read 67 ms later) | replay only | 0.940 | 0.911 | 0.827 | 0.740 | 0.591 | -4 | 0.55 |
| EMA 72 ms, forward-backward | replay only | 0.884 | 0.830 | 0.684 | 0.550 | 0.352 | 0 | 0.35 |
| Butterworth 6 Hz, zero-phase | replay only | 1.000 | 0.999 | 0.996 | 0.988 | 0.943 | 0 | 2.50 |
| Butterworth 10 Hz, zero-phase | replay only | 1.000 | 1.000 | 1.000 | 0.999 | 0.993 | 0 | 4.30 |
| Savitzky-Golay 0.10 s, zero-phase | replay only | 1.000 | 1.000 | 1.000 | 0.999 | 0.996 | 0 | 5.15 |

Measured by passing a sinusoid through each filter and fitting the output; for the EMAs it
matches the transfer function to 1e-4. "Replay only" filters need samples from the future.
The scored path is a replay, so they can run there, but not in a live trace.

Data:

- **Corpus.** 159 paired phone and OptiTrack trials, 13 participants. 145 are evaluated:
  9 never zero in the replay, 4 have an unreadable component file, 1 has no overlap with
  its take. These are the same 145 as PR #130. The reference is shank-plate rotation, by
  `imu_optitrack_validation` unchanged. The shipped row reproduces PR #130's figures
  (gain 0.93, RMSE median 4.8 deg, p90 9.9; 145 aligned, 132 with the hold seen).
- **Synthetic phone.** `neuromech/phone_trial.py`, five swings by eight seeds, at the
  default sensor noise and at 4x (gyro and accelerometer both). The swings: the
  generator's default (0.9 Hz, 55 deg), heavily damped, small (25 deg), and two faster
  ones (1.6 and 2.5 Hz) for a short pendulum and the bench range. Truth is the planted
  angle, scored by the same scorer. Each recording is also made with a noise-free sensor
  and the same seed, so "what sensor noise alone does" is a like-for-like difference.

## 1. Tracking cost

Corpus, app angle against shank-plate rotation. "Aligned" lets each curve shift by its own
best delay (up to 150 ms), as `imu_optitrack_validation` does for the app angle; "not
aligned" leaves it on the trial's time alignment, which is found once from the unsmoothed
sensor rotation. Bias and RMSE are over the 132 trials where both instruments saw the hold.

| Smoothing | Gain, aligned (p10-p90) | Lag (ms) | RMSE aligned, median / p90 (deg) | RMSE not aligned, median / p90 (deg) | Bias (deg) |
|---|---|---|---|---|---|
| EMA 72 ms (shipped) | 0.93 (0.84-0.98) | 65 | 4.8 / 9.9 | 8.1 / 11.7 | -3.4 |
| no smoothing | 0.99 (0.90-1.04) | 0 | 5.2 / 10.0 | 5.2 / 10.1 | -3.5 |
| EMA 50 ms | 0.96 (0.87-1.00) | 40 | 4.8 / 9.8 | 6.8 / 10.8 | -3.4 |
| EMA 36 ms | 0.98 (0.88-1.02) | 30 | 4.8 / 9.7 | 5.9 / 10.3 | -3.5 |
| EMA 20 ms | 0.99 (0.89-1.03) | 15 | 4.9 / 9.8 | 5.3 / 10.0 | -3.5 |
| EMA 10 ms | 0.99 (0.90-1.04) | 5 | 5.1 / 10.0 | 5.2 / 10.0 | -3.5 |
| EMA 72 ms, lag-compensated | 0.93 (0.85-0.97) | -5 | 4.8 / 9.8 | 4.8 / 10.6 | -3.4 |
| EMA 72 ms, forward-backward | 0.87 (0.77-0.91) | 5 | 5.2 / 10.2 | 5.2 / 10.6 | -3.3 |
| Butterworth 6 Hz, zero-phase | 0.99 (0.90-1.03) | 0 | 4.8 / 9.7 | 4.8 / 9.7 | -3.5 |
| Butterworth 10 Hz, zero-phase | 0.99 (0.90-1.04) | 0 | 4.9 / 9.8 | 4.9 / 10.7 | -3.5 |
| Savitzky-Golay 0.10 s, zero-phase | 0.99 (0.90-1.04) | 0 | 5.0 / 9.9 | 5.0 / 10.7 | -3.5 |

- Gain and lag are where the candidates differ. Median of the 13 participants' median
  gains: 0.93 shipped, 0.99 unsmoothed, so no one participant carries it.
- Aligned RMSE does not separate them. It is set by the -3.5 deg bias and the flexion-axis
  error, which no smoothing touches.
- Unsmoothed, the aligned RMSE is slightly *worse* than shipped (5.2 against 4.8). That is
  the burst staircase of section 5, not sensor noise: on the phone's clock every candidate
  reads 5.3 to 5.5.
- The amplitude loss, three ways: predicted from each trial's own swing frequency, 0.919;
  tracking gain over the unsmoothed tracking gain, 0.934; first rebound (first counted
  trough to the next peak) over the unsmoothed one, 0.895. They differ for a reason. The
  regression gain includes the fall from the hold to the resting angle, which is slow and
  passes unattenuated; the rebound is the oscillation alone.

Synthetic phone against the planted motion, default noise (4x is the same to the second
decimal). Median over the eight seeds of each swing:

| Swing | Gain shipped / none | Lag shipped / none (ms) | RMSE shipped / none (deg) | First rebound over true, shipped / none | PT minus true, shipped / none |
|---|---|---|---|---|---|
| default 0.9 Hz, 55 deg | 0.927 / 0.999 | 65 / -2 | 6.1 / 1.4 | 0.92 / 1.00 | +0.008 / -0.006 |
| heavily damped | 0.927 / 0.994 | 69 / -3 | 3.0 / 1.4 | 0.88 / 0.99 | +0.009 / -0.002 |
| small, 25 deg | 0.897 / 0.982 | 64 / -3 | 3.3 / 2.1 | 0.91 / 1.00 | +0.013 / +0.001 |
| fast 1.6 Hz | 0.816 / 1.001 | 61 / -2 | 5.8 / 0.6 | 0.80 / 1.00 | +0.027 / +0.001 |
| fast 2.5 Hz | 0.667 / 1.000 | 53 / -2 | 7.4 / 1.0 | 0.64 / 1.00 | +0.043 / 0.000 |

The filter raises the PT score, more the faster the swing; on the 2.5 Hz swing it moves
the verdict on all 8 recordings (none without it). EMA 20 ms: gain 0.99 to 0.95 across the
five swings, lag 15 ms. Zero-phase Butterworth 6 Hz: 1.00 to 0.97, lag 0.

## 2. PT score and verdict

Against the shipped smoothing, same trials, by `pendulastic_pt_score` unchanged. "Stored"
is drift correction on (what the app stores), "on screen" is off.

| Smoothing | A0 change, median (deg) | PT change, median | Stored: PT moves > 0.05 / 0.1 / 0.3 (of 143) | Stored: verdict changes (of 136) | On screen: PT moves > 0.05 / 0.1 / 0.3 (of 143) | On screen: verdict changes (of 138) | N changes (of 143) |
|---|---|---|---|---|---|---|---|
| no smoothing | +0.47 | -0.017 | 6 / 2 / 0 | 2 | 8 / 3 / 1 | 3 | 52 |
| EMA 50 ms | +0.14 | -0.008 | 3 / 1 / 0 | 1 | 4 / 4 / 1 | 2 | 24 |
| EMA 36 ms | +0.24 | -0.012 | 4 / 1 / 0 | 3 | 4 / 4 / 1 | 4 | 36 |
| EMA 20 ms | +0.35 | -0.015 | 5 / 3 / 0 | 3 | 6 / 4 / 1 | 4 | 47 |
| EMA 10 ms | +0.39 | -0.017 | 7 / 3 / 0 | 3 | 8 / 4 / 1 | 3 | 51 |
| EMA 72 ms, lag-compensated | +0.02 | +0.000 | 1 / 1 / 0 | 0 | 1 / 1 / 1 | 0 | 7 |
| EMA 72 ms, forward-backward | -1.78 | +0.012 | 5 / 2 / 1 | 4 | 4 / 3 / 1 | 5 | 26 |
| Butterworth 6 Hz, zero-phase | +0.47 | -0.016 | 3 / 2 / 0 | 2 | 3 / 3 / 1 | 2 | 50 |
| Butterworth 10 Hz, zero-phase | +0.46 | -0.017 | 6 / 2 / 0 | 2 | 7 / 3 / 1 | 2 | 51 |
| Savitzky-Golay 0.10 s, zero-phase | +0.43 | -0.017 | 6 / 2 / 0 | 2 | 7 / 3 / 1 | 2 | 52 |

Two of the 145 evaluated trials are not scored under either smoothing. One more is scored
with the filter and not without it (and, for the forward-backward EMA, two the other way).

N is the sensitive parameter: it changes on about a third of trials as soon as the filter
is lightened. The direction is that the filter removes small real extrema. Against the
unsmoothed curve, the shipped curve is missing 66 of 1467 counted extrema and has 4 that
the unsmoothed curve lacks, over 57 trials.

Against the gap-filled optical score (shank-plate rotation, gaps filled by linear
interpolation, both curves cut to the time they share, as `pt_disagreement_diag.py`'s
`v1b_gaps_filled`), on the 130 trials where sensor rotation is within 4 deg of the plate:

| Smoothing | Verdict agreement | Kappa | PT ICC | PT bias | A0 ICC | A0 minus optical, median (deg) | N equal | Optical extrema missed / invented (of) |
|---|---|---|---|---|---|---|---|---|
| EMA 72 ms (shipped) | 115 of 120 | 0.92 | 0.81 | +0.017 | 0.69 | -2.7 | 71 of 127 | 130 / 86 (1255) |
| no smoothing | 114 of 122 | 0.87 | 0.81 | -0.002 | 0.71 | -2.8 | 82 of 128 | 77 / 96 (1268) |
| EMA 50 ms | 115 of 120 | 0.92 | 0.81 | +0.008 | 0.69 | -2.8 | 83 of 127 | 100 / 84 (1255) |
| EMA 36 ms | 114 of 120 | 0.90 | 0.81 | +0.003 | 0.71 | -2.8 | 83 of 127 | 91 / 80 (1255) |
| EMA 20 ms | 114 of 121 | 0.89 | 0.81 | -0.000 | 0.71 | -3.0 | 84 of 128 | 87 / 97 (1268) |
| EMA 10 ms | 115 of 122 | 0.89 | 0.81 | -0.002 | 0.71 | -2.8 | 81 of 128 | 83 / 95 (1268) |
| EMA 72 ms, lag-compensated | 115 of 120 | 0.92 | 0.81 | +0.015 | 0.69 | -3.2 | 74 of 127 | 99 / 59 (1255) |
| EMA 72 ms, forward-backward | 112 of 119 | 0.89 | 0.80 | +0.034 | 0.57 | -5.3 | 56 of 127 | 122 / 44 (1252) |
| Butterworth 6 Hz, zero-phase | 114 of 121 | 0.89 | 0.81 | -0.001 | 0.71 | -3.0 | 84 of 128 | 82 / 93 (1268) |
| Butterworth 10 Hz, zero-phase | 114 of 121 | 0.89 | 0.81 | -0.002 | 0.71 | -2.9 | 86 of 128 | 79 / 93 (1268) |
| Savitzky-Golay 0.10 s, zero-phase | 115 of 122 | 0.89 | 0.81 | -0.003 | 0.71 | -2.8 | 83 of 128 | 80 / 95 (1268) |

- Without the filter the PT bias against optics goes from +0.017 to zero, N matches the
  optical N on 82 trials instead of 71, and 53 more of the optical curve's extrema are
  found for 10 more that it does not have.
- The verdict does not get better: 5 disagreements of 120 with the filter, 8 of 122
  without. The kappa difference (0.92 against 0.87) is those three trials, and it is the
  one figure here that favours the shipped filter. On the phone's clock it disappears
  (125 of 130 against 126 of 132; section 5).
- Over all 145 trials the picture is the same (126 of 133 against 126 of 135).

## 3. What the filter buys: noise

**Still segments of real recordings.** One-second windows in which the angle's slow part
(under 3 Hz) moves less than 0.5 deg: 404 s over 110 of the 145 trials. Jitter is what is
left above 3 Hz. "After the scorer" is after the scorer's own 0.10 s Savitzky-Golay, which
is what its peak finder is handed.

| Smoothing | Jitter RMS, median / p90 (deg) | After the scorer, median / p90 (deg) | Largest peak-to-peak after the scorer (deg) | Still-segment extrema of 1.0 deg or more / 0.2 deg or more |
|---|---|---|---|---|
| EMA 72 ms (shipped) | 0.019 / 0.030 | 0.019 / 0.029 | 0.23 | 0 / 186 |
| no smoothing | 0.052 / 0.075 | 0.042 / 0.064 | 0.54 | 0 / 708 |
| EMA 50 ms | 0.024 / 0.038 | 0.024 / 0.037 | 0.27 | 0 / 301 |
| EMA 36 ms | 0.029 / 0.045 | 0.028 / 0.043 | 0.31 | 0 / 410 |
| EMA 20 ms | 0.038 / 0.058 | 0.034 / 0.054 | 0.39 | 0 / 525 |
| EMA 10 ms | 0.045 / 0.068 | 0.040 / 0.060 | 0.48 | 0 / 642 |
| Butterworth 6 Hz, zero-phase | 0.030 / 0.048 | 0.029 / 0.047 | 0.51 | 0 / 437 |
| Butterworth 10 Hz, zero-phase | 0.039 / 0.061 | 0.037 / 0.059 | 0.53 | 0 / 591 |
| Savitzky-Golay 0.10 s, zero-phase | 0.042 / 0.064 | 0.039 / 0.061 | 0.53 | 0 / 627 |

The scorer counts an extremum only at a prominence of `max(1.0, 0.05 * A0)` degrees. No
candidate, including no smoothing, produced one still-segment wiggle of 1 deg in 404 s.
So jitter cannot invent an extremum on its own. It can only tip one that is already
within a few tenths of a degree of the threshold, and a decaying swing always has one of
those near its end. The lag-compensated and forward-backward rows are left out of this
table: their largest values come from edge effects next to motion.

**Synthetic phone.** Still segments are the hold and the tail, where the planted angle is
constant. "By sensor noise alone" compares each recording with its noise-free twin under
the same smoothing.

| Smoothing | Jitter RMS, 1x / 4x (deg) | Largest peak-to-peak after the scorer, 1x / 4x (deg) | Counted extrema changed by sensor noise alone, 1x / 4x | N changed by sensor noise alone, 1x / 4x (of 40) | Largest PT change from noise alone, 1x / 4x |
|---|---|---|---|---|---|
| EMA 72 ms (shipped) | 0.013 / 0.016 | 0.09 / 0.11 | 0 / 7 | 0 / 7 | 0.002 / 0.020 |
| no smoothing | 0.041 / 0.047 | 0.23 / 0.26 | 2 / 12 | 2 / 12 | 0.016 / 0.021 |
| EMA 50 ms | 0.017 / 0.021 | 0.11 / 0.14 | 0 / 9 | 0 / 9 | 0.003 / 0.020 |
| EMA 36 ms | 0.021 / 0.025 | 0.14 / 0.17 | 1 / 12 | 1 / 12 | 0.015 / 0.021 |
| EMA 20 ms | 0.028 / 0.033 | 0.18 / 0.22 | 1 / 13 | 1 / 13 | 0.015 / 0.020 |
| EMA 10 ms | 0.035 / 0.040 | 0.21 / 0.25 | 0 / 11 | 0 / 12 | 0.014 / 0.021 |
| Butterworth 6 Hz, zero-phase | 0.019 / 0.024 | 0.14 / 0.18 | 1 / 13 | 1 / 13 | 0.010 / 0.021 |
| Butterworth 10 Hz, zero-phase | 0.027 / 0.033 | 0.20 / 0.24 | 2 / 13 | 2 / 13 | 0.016 / 0.020 |
| Savitzky-Golay 0.10 s, zero-phase | 0.030 / 0.036 | 0.22 / 0.25 | 1 / 12 | 1 / 13 | 0.016 / 0.020 |

- This is the filter's measurable benefit: fewer N flips from noise (0 against 2 of 40 at
  the default noise, 7 against 12 at 4x). Each flip is half an oscillation on a marginal
  extremum and is worth at most 0.02 PT, with or without the filter.
- Against the *planted* motion the filter does not make N more right. N differs from the
  true N on 24 of 40 recordings with the filter and 23 without (default noise). The
  shipped curve misses 39 of 488 true extrema and invents none; the unsmoothed curve
  misses 8 and invents 15. Almost all of both are the same marginal extremum per swing,
  on one side of the threshold or the other, and are the same on every seed. That is a
  property of a hard threshold on a decaying swing, not of noise.
- No still-segment extremum of 1 deg at either noise level for any candidate.
- The jitter floor barely moves from 1x to 4x sensor noise and is the size seen on real
  recordings (0.04 to 0.05 deg unsmoothed). Why is not investigated here.

## 4. How the optical reference limits this

- **Dropped frames.** A median 28 % of optical frames are missing (p90 41 %). Tracking
  uses only grid points within 50 ms of a tracked frame, on a 60 Hz grid, with linear
  interpolation between frames. The optical PT score is computed on gap-filled curves, and
  a linear fill across a turning point clips it: the unsmoothed phone's first rebound
  reads 6 % larger than the gap-filled optical one, while its regression gain on tracked
  frames is 0.99. So the gap-filled optical amplitude cannot settle a difference of a few
  per cent, which is the difference between "no smoothing" and "EMA 36 ms".
- **Time alignment.** The instruments share no clock. The trial's lag is found from the
  phone's own unsmoothed rotation in 5 ms steps, and each candidate's extra delay in 5 ms
  steps by best correlation. Lags are good to about 5 ms, and "aligned" figures are best
  case by construction.
- **Its own filtering.** This code applies no smoothing to the plate rotation. Whatever
  Motive applied when reconstructing and labelling the markers is not known here. The
  optical scores go through the same 0.10 s scorer smoothing as the phone's.
- **What dominates the error.** Bias (-3.5 deg) and flexion-axis error set the RMSE
  (4.8 to 5.2 aligned for every candidate), so RMSE against optics is a weak test of a
  smoothing change. Gain, lag and the unaligned error are the usable ones.
- **Resolution.** The candidates differ by one to three trials in verdict agreement. The
  optical score's own agreement with the phone (PT ICC 0.81) is far too loose to rank
  them on that. 13 participants, trials within a participant are not independent, and no
  confidence intervals are given.
- **Range.** The corpus swings near 0.9 Hz. Nothing in it tests a faster swing; the
  synthetic rows do.

## 5. Found: the corpus is replayed on the receive clock

Each corpus sample carries two times: `t`, when the desktop received it, and
`phone_ts_ms`, the phone's own stamp. The replay integrates the gyro on the phone stamp
but places samples on its 10 ms ticks by `t`. The phone's 100 Hz stream reaches the
desktop in bursts: median 90 ms apart (p10-p90 46 to 99), about 9 samples each. So a tick
sees the orientation change only when a burst lands, and 79 % of in-swing ticks repeat the
tick before. The synthetic phone and the app's own recordings stamp `t` on the phone, and
have no such staircase.

A sensitivity, not a proposal: the same replay with each sample's `t` taken from its phone
stamp before it is fed in (`restamp_on_phone_clock`; the replay code is not touched).

| | Receive clock (the footing) | Phone clock |
|---|---|---|
| In-swing ticks that repeat the tick before | 79 % | 0 % |
| Unsmoothed angle about its own smooth curve, in swing (deg) | 3.18 | 0.15 |
| Shipped angle, the same (deg) | 0.61 | 0.04 |
| Unsmoothed, after the scorer's own smoothing (deg) | 1.79 | 0.10 |
| Sensor rotation against the plate, offset removed, median (deg) | 2.6 | 1.4 |
| Trials with sensor rotation within 4 deg of the plate | 130 | 139 |
| Gain, shipped / unsmoothed | 0.93 / 0.99 | 0.93 / 1.00 |
| RMSE not aligned, shipped / unsmoothed (deg) | 8.1 / 5.2 | 8.3 / 5.5 |
| Verdict agreement with gap-filled optics, shipped / unsmoothed | 115 of 120 / 114 of 122 | 125 of 130 / 126 of 132 |
| PT ICC against gap-filled optics, shipped / unsmoothed | 0.81 / 0.81 | 0.85 / 0.90 |
| N equal to optical N, shipped / unsmoothed | 71 of 127 / 82 of 128 | 86 of 136 / 94 of 137 |

What this means for the question asked:

- The filter's cost is the same on both clocks (gain 0.93, lag 65 ms). The conclusion
  about cost does not depend on this.
- On the corpus as logged, the EMA is doing a job it was not chosen for: it takes the
  staircase from 3.2 deg to 0.6. The zero-phase 6 Hz Butterworth does that better (0.41)
  with no amplitude loss. On the phone clock there is nothing left to do (0.15 unsmoothed),
  as on the synthetic phone at its 60 Hz (0.66 unsmoothed, 0.15 after the scorer).
- So the corpus as logged overstates what the filter is good for in the app.

What it means beyond this question:

- With the shipped filter on both sides, moving from the receive clock to the phone clock
  moves the stored PT score by more than 0.05 on 7 of 143 trials, more than 0.1 on 5 and
  more than 0.3 on 1, changes 1 of 135 verdicts and N on 27 trials. On screen: 11, 7 and
  4 trials, and 4 verdicts. That is more than removing the filter does (section 2).
- The sensor tracks the plate closer than the 2026-11-18 validation reported, because
  that residual included the staircase (2.6 deg against 1.4 here, offset removed).
- The PT ICC step from 0.85 to 0.90 on the phone clock sits between two candidates that
  differ by one scored trial. Do not read it as more than that.

## 6. If a candidate were adopted

This would be a scoring-footing change under the Quality bar: it lands alone, with the
corpus before and after and every changed trial explained, the Rust core run on real
trials, parity across the ports, an independent review and a version bump.

- **Footing id.** `ref-100hz-ema0.5-flexrec` names the EMA. A new id is needed in
  `REFERENCE_REPLAY_CONFIG` (Python), `ReplayConfig::reference()` and `SCORING_FOOTING`
  (Rust), the mirror in `neuromech/phone_trial.py` (`APP_EMA_ALPHA`, which sets each
  synthetic trial's expected values), and the pins in `tests/test_pt_zone_parity.py`.
- **`HEALTHY_REF`.** Derived from control curves on this footing. A0 rises (median
  +0.47 deg) and N rises on a third of trials, so it has to be re-derived, in the same PR,
  with a new `SCORE_FORMULA_ID`, in Python, Rust and C#. The PT changes in section 2 are
  against today's reference; a re-derived one would absorb part of the -0.017.
- **Three-port parity.** No smoothing is already expressible in Python and Rust
  (`alpha` 1.0 is passed through unchanged by both `rescale_ema_alpha`s), so it needs no
  new filter code; the C# scorer has no replay and only takes the new reference. A
  zero-phase filter is new code in Python and Rust with its own edge handling to hold in
  parity, and the live trace cannot run it, so the live and scored curves would differ by
  more than they already do.
- **Stored records.** Every stored record is on the EMA footing. The trends view already
  breaks a series where `scoring_footing` changes; it would break again. Records can be
  re-scored only where the raw export was kept.
- **Corpus and reports.** Every phone-derived corpus number moves, including PR #130's
  table and the 2026-11-18 validation. neuromech's intended bands and near-edge allowlist
  are judged on the app footing through `phone_trial` and would need re-running; the
  smoothing is the documented gap between the app's score and the 100 Hz kinematic score
  there, so that gap should shrink.
- **Bench plan.** A3 (gain) and, as I read it, A1 (whole-swing RMSE) and A9 (fitted lag
  against tap lag) all see the filter's 65 ms and 0.93. They should say which smoothing
  signal A is on before the limits are fixed.

## 7. Re-derived a second way

Before opening the PR I checked a sample of the numbers by a different route:

1. The shipped row reproduces PR #130's published figures (gain 0.93, RMSE median 4.8 deg,
   p90 9.9, 145 aligned, 132 with the hold seen), which were produced by other code paths
   in `imu_optitrack_validation.evaluate_trial`.
2. A hand-written loop EMA over the unsmoothed replay equals the shipped replay on all
   145 trials (largest difference 0.0 deg), so "unsmoothed" really is the shipped curve
   minus only its EMA.
3. Tracking on every fifth trial (28) with a separately written comparison
   (`track_against`: real optical frames only, no 60 Hz grid, no gap interpolation, its
   own 1 ms delay search): gain 0.925 shipped and 0.991 unsmoothed against 0.934 and 1.002
   from `tracking_agreement` on the same trials; lag 65 and 0 ms from both; shipped over
   unsmoothed 0.940 against 0.936.
4. The amplitude cost from the swing frequency alone (0.919) against the measured ratios
   (0.934 tracking, 0.895 first rebound), with the reason they differ in section 1.
5. PT scores on that sample through `imu_optitrack_validation.score_curve` instead of this
   script's scorer call: 58 of 58 identical.
6. Still-segment jitter from a periodogram of the unsmoothed windows with the EMA applied
   as its transfer function: 0.061 to 0.025 deg, against 0.052 to 0.019 from the
   time-domain high-pass. The two cut the 3 Hz boundary differently; the ratio agrees.

These checks share the same replay and the same optical loader. They test this script's
arithmetic, not those.

## Not verified

- **No other port was run.** Every candidate exists only in this Python script. The Rust
  core, the wasm and the app were not built or run; nothing here shows a candidate would
  hold parity.
- **No `HEALTHY_REF` re-derivation** under any candidate, and the zone bands are the
  provisional ones as they stand. Verdict counts are against today's reference and bands.
- **Test-retest reliability** under each candidate was not computed. It is the measure
  earlier scorer decisions were made on, and a smoother curve could plausibly help it.
- **The app's own recordings.** None is paired with truth, so none was analysed. What the
  filter buys in the app rests on the synthetic phone and on the re-stamped corpus.
- **The re-stamped replay** is a sensitivity. The replay's hold and release logic was not
  reviewed on the phone clock, Rust was not run that way, and one trial's three sensors
  are 54 ms apart on it. Whether the corpus should be replayed on the phone clock is an
  open question this note raises and does not answer.
- **The synthetic noise model** is white gyro and accelerometer noise with a fixed bias:
  no strap wobble, soft tissue or vibration. 4x is an arbitrary multiple. A real phone
  with a worse gyro, or a loose mount, was not represented.
- **Still segments of real recordings** are picked by a rule on the measured curve itself
  and include tremor; 35 of the 145 trials have none.
- **Motive's own filtering** of the marker data.
- **The live path** (the on-screen trace and its display smoothing) was not looked at.
- **Uncertainty.** No confidence intervals; trials within a participant are not
  independent; candidates that differ by a few trials are not distinguished.
- **The bench reading** of A1 and A9 is mine from the plan text; A3 is arithmetic.
- **No independent review** of this analysis. The second-way checks are my own.
- 14 of the 159 paired trials are not in any figure (9 never zero, 4 unreadable, 1 no
  overlap).

## Regenerating

With the project `.venv`, from the repository root:

    python smoothing_filter_cost_benefit.py --part response
    python smoothing_filter_cost_benefit.py --part synthetic
    python smoothing_filter_cost_benefit.py --part corpus --cache-dir <a folder outside the repo>

The corpus part needs the recordings (found through `batch_imu_vs_optitrack_rmse`, about
20 minutes). It prints every table here, for both clocks; `--out` writes the aggregates as
JSON. The by-swing synthetic table is in that JSON under `by_swing`.
