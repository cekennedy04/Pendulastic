# Oscillation count N depends on release timing to within 10 ms (2026-11-11)

Investigation only. No product code is changed by this report. Everything below was run against
`origin/main` at `e9a3a01` with the scorer imported unmodified; the remedies in section 5 are emulated in an
analysis script that patches the source text of `compute_pt_params` in memory.

Words used here: **release** is the sample the scorer treats as the start of the swing. A **shift** moves
that sample: negative means earlier (the limb is still held), positive means later (the limb is already
falling). The **shoulder** is a small local maximum that the scorer finds within 0.2 s of the release.
**Prod** is the phone convention (`detrend=False`); **detrend** is the desktop default (`detrend=True`).
**MAS band** is `mas_estimate(...)["mas"]` (the Popovic table, edges 0.12 / 0.28 / 0.44 / 0.60 / 0.78);
**zone** is `pt_zone` (edges 0.1709 / 0.3528, which the app does not display while
`ZONE_CLASSIFICATION_CALIBRATED` is false).

## Summary

- **The hypothesis holds.** On the 162 corpus phone trials the scorer can score, 40 (25%) count a shoulder
  as half an oscillation under prod, and 50 (31%) under detrend. I looked at the trace of every one of the
  40: all are shoulders, none is a half-cycle.
- **The cause is the swing-centred second pass** (`b44dc7d`), not the limb. The boxcar centre is padded
  with the release value, so it starts falling at the release sample at a constant rate while the limb,
  starting from rest, has not yet moved. Their difference, which is the signal the second pass searches,
  rises for a few samples and then falls. None of the 40 has a peak in the uncentred first pass.
- **It is a knife edge.** On an ideal pendulum released exactly on time the shoulder prominence sits just
  under the threshold; each 10 ms of still samples before the limb moves adds about 0.3 thresholds (0.4 on
  the ideal pendulum, median 0.27 on the corpus). The corpus shoulders have a median prominence of 1.31
  thresholds, and 37 of 40 are under 2.
- **How far N moves.** Moving the auto-detected release by 10 ms changes N in 34 of 162 trials, by 20 ms in
  73, by 30 ms in 92. The MAS band changes in 6 / 15 / 20 trials and the zone in 3 / 6 / 8. On the 67
  synthetic scenarios, scored at the exact model release, N changes within 10 ms in 28, within 20 ms in 42.
- **It is not what limits test-retest reliability.** Removing the shoulder moves ICC(1,1) of N from 0.696
  to 0.703 and removes 4.7% of the within-session variance of N. PT-score ICC does not move (0.627 to 0.623).
- **It does not bias the healthy reference for N.** 7 of 39 control optical trials carry a shoulder; the
  trial median of N is 6.0 with and without it.
- **Recommended remedy: count from the first trough** (option B). A peak found before the first counted
  trough is the release, not a half-cycle. About five lines in each of Python, Rust and C#. It cuts the
  10 / 20 / 30 ms N changes on the corpus from 34 / 73 / 92 to 4 / 7 / 11 and lowers N by 0.5 on 41 trials.
- **A second, separate release sensitivity remains and is not addressed by any option here:**
  `area_ratio` integrates from the release, and its PT contribution moves by 0.005 or more in 22 of 162
  trials for a 10 ms shift whichever option is used.

## 1. Mechanism (read from source, then confirmed by measurement)

### What the code does

`pendulastic_pt_score.py::compute_pt_params`, in order:

1. Smooth the angle with a 0.10 s Savitzky-Golay window (`_sg`, order 3), cut it at the release, subtract
   the settled-tail median (`neutral`), flip so extension is positive. `phi[0]` is the release-above-rest
   amplitude; `phi_s` is `phi` smoothed again (order 2).
2. `min_amp = max(1.0, 0.05 * A0)`; `min_dist = max(3, fps / 3.5)` samples (0.28 s at 100 Hz).
3. **First pass:** `find_peaks(phi_s, height=min_amp, distance=min_dist, prominence=min_amp)` and the same
   on `-phi_s`. `find_peaks` cannot return index 0, so the release itself is never a peak here.
4. **Second pass (`b44dc7d`), only if the first pass found three or more extrema:** the median spacing of
   those extrema is taken as a half period, `_swing_centre` computes a one-period boxcar average of `phi_s`
   **edge-padded with `phi_s[0]`**, and both `find_peaks` calls are repeated on `phi_s - phi_centre`. The
   second pass replaces the first.
5. Extrema after the active-oscillation window end are dropped (`_active_oscillation_window_end`).
6. `N = (n_pos + n_neg) / 2`, where `n_pos` counts peaks with `phi - phi_centre > min_amp` and `n_neg`
   counts troughs with `phi - phi_centre < -min_amp`.

N enters the PT score only when it is below `HEALTHY_REF["N"] = 6.5`; one half-cycle is then worth
`0.5 / (6 * 6.5) = 0.0128`. N below 2.0 also removes the `f` term and changes the divisor.

### Why a shoulder appears

At the release sample the boxcar window is half padding (the release value `A0`) and half the first half
period of the swing. Each step to the right drops one padded `A0` and admits one sample from about half a
period ahead, which is near the first trough. So the centre falls from the very first sample at about
`(A0 + first-trough depth) / T` per second. The limb leaves from rest, so `phi_s` falls quadratically.
`phi_s - phi_centre` therefore rises first and falls after, giving a local maximum a few hundredths of a
second after the release. Its left base is the release sample, so its prominence is the height of that
initial rise. For an undamped pendulum released exactly on time the rise works out to about
`(1 + r)^2 * A0 / (8 * pi^2)` with `r` the first-trough depth over `A0`: 0.051 `A0` for `r = 1`, against a
threshold of 0.05 `A0`.

Every still sample between the release index and the first movement adds the full centre fall rate to
that rise, because `phi_s` is flat while the centre keeps falling. A release that is late removes it.

### Measured confirmation

Ideal damped pendulum (`A0` 45 deg, 1.0 Hz, 100 Hz grid, 2 s hold, release index given, prod). Each cell is
N, with `*` where the first counted extremum is a peak, followed by the shoulder prominence over the
threshold:

| damping (1/s) | -30 ms | -20 ms | -10 ms | 0 | +10 ms |
|---|---|---|---|---|---|
| 0.15 | 12.0* (1.94) | 12.0* (1.53) | 12.0* (1.14) | 11.5 | 11.5 |
| 0.30 | 10.5* (1.72) | 10.5* (1.33) | 10.0 | 10.0 | 10.0 |
| 0.45 | 7.0* (1.51) | 7.0* (1.13) | 6.5 | 6.5 | 6.5 |
| 0.80 | 4.0* (1.14) | 3.5 | 3.5 | 3.5 | 3.5 |
| 1.50 | 2.0 | 2.0 | 2.0 | 2.0 | 2.0 |

(At damping 1.50 the flip is at -40 ms.) The prominence grows by about 0.4 thresholds per 10 ms. On the 40
corpus shoulders the centre falls at a median 0.27 thresholds per 10 ms over the first 0.1 s (range 0.08
to 0.40).

On the corpus, of the 40 prod trials whose first counted extremum is within 0.2 s of the release:

- 0 have a peak within 0.2 s in the first (uncentred) pass;
- in all 40 the smoothed angle rises less than the threshold above its release value before the first
  trough (largest rise 2.35 deg, 0.83 of the threshold for that trial).

So the extremum exists only in the centred frame.

### Why the shipped release detector sits on the edge

`_detect_release` backs off 0.10 s from the 8%-of-range crossing so that `A0` is read from the hold. That
is deliberate and it fixed a -4.92 deg bias in `A0`. Its side effect is that the release index is early: on
the 46 corpus trials tabulated in section 3, the raw angle stays within 1 deg of its release value for a
median 65 ms after the release index. That is the regime where the shoulder is near threshold.

### Rust and C# (read, not run)

`mobile-imu-core/src/scoring.rs` (`swing_centre` at line 233, passes at 741 to 766, count at 800 to 802)
and `neuromech/designer/PtScore.cs` (passes at 225 to 244, `SwingCentre` at 401) carry the same two passes,
the same `phi[0]` edge padding, the same threshold, distance and prominence, and the same count. I read
them side by side with the Python; I did not execute either on shifted releases.

### What `b44dc7d` fixed, so that a remedy does not undo it

`neutral` is the settled-tail median. A limb that keeps sagging into flexion after the swing dies swung
about a higher centre than the angle it ends at, so troughs stopped clearing a threshold measured from
`neutral` and the symmetry integral read a symmetric swing as asymmetric: troughs 7 to 3, `area_ratio`
0.008 to 0.824, PT 0.0713 to 1.2180 across 0 to 20 deg of sag on an unchanged oscillation. The fix moved
extremum detection, the count gates and the symmetry integral into the centred frame. Its guard is
`tests/test_pt_score.py::test_a_limb_that_sags_after_the_swing_still_scores_as_it_swung`. The edge padding
with `phi[0]` rather than zero was chosen so the baseline is not dragged to neutral at the release. Any
remedy has to leave trough detection in the centred frame alone.

## 2. Sensitivity on synthetic truth

67 neuromech scenario traces built on this commit (`python -m neuromech.build`). Each was scored the way
`neuromech.build.score` scores it (180 + model angle, 100 Hz grid, 2 s hold prepended, release index given,
`detrend=False`), with the grid placed so the release sample falls at model time -50 to +50 ms in 5 ms
steps. At shift 0 all 67 reproduce `neuromech.build.score` exactly (N and PT).

| | shipped |
|---|---|
| scenarios that count a shoulder at the exact release | 2 |
| N changes within 10 / 20 / 30 / 50 ms | 28 / 42 / 47 / 52 |
| MAS band changes within 10 / 20 / 30 / 50 ms | 2 / 2 / 3 / 4 |
| zone changes within 10 / 20 / 30 / 50 ms | 0 / 4 / 6 / 7 |
| PT range over the whole sweep | median 0.0113, 90th percentile 0.0236, max 0.2023 |
| N-stable window around 0 | median -15 to +50 ms; 15 scenarios stable over the full sweep; 3 stable over 20 ms or less |

46 scenarios change N somewhere on the early side and 12 somewhere on the late side. Early side, by
distance of the nearest change: 11 scenarios at -5 ms, 14 at -10, 7 at -15, 5 at -20, 9 between -25 and
-40. The 42 scenarios that change N within 20 ms are the same population as the "40 of 67 gain the
extremum" in the band-edge report, measured there at a single 18 ms delay.

Scenarios that change MAS band somewhere in the sweep: B-flexor (1+ to 1 at -10 ms and earlier), D-short
(2 to 1+ at -5 ms and earlier, and at +5 ms), M-tone_L4 (1+ to 2 at +30 ms and later), C-phi-low (3 to 2
or 4 at the ends of the sweep; N does not change there, this is `A0` and `area_ratio`). Scenarios that
change zone: A-P1p, B-mixed, B-habit2, B-habit3, B-habit4, K-HM1, R-reflex-len. B-claspknife and
M-catch_release_high, the two band-edge misses of the dry run, go N 6.0 to 6.5 at -15 ms and earlier and
their PT falls from 0.1394 to as low as 0.1265; that does not cross 0.12 by itself, which agrees with the
band-edge report that it takes the N step and the velocity term together.

Not all N changes are the shoulder. A-P1p and B-mixed alternate between N 2.5 and 3.0 on alternate 5 ms
steps: their last extremum sits on the amplitude threshold and the sub-sample phase of the grid decides
it. A-P1, B-extensor and B-habit1 lose their last half-cycle when the release moves early. Those are the
tail end of the count, not the start, and no start-of-swing remedy touches them (section 5).

Per scenario. Columns `dR2n` to `darea` are max minus min over the sweep; the last column is the N-stable
window with option B applied, for comparison.

| scenario | N at 0 | N seen | N-stable window (ms) | PT at 0 | PT min to max | dR2n | dphi | domega | df (Hz) | darea | MAS bands seen | zones seen | N-stable window, option B |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A-P0 | 6.5 | 6.5/7 | -5 to +50 | 0.0600 | 0.0491 to 0.0600 | 0.021 | 0.025 | 0.210 | 0.009 | 0.030 | 0 | healthy | -50 to +50 |
| A-P1 | 5 | 4.5/5 | -5 to +50 | 0.1924 | 0.1829 to 0.2052 | 0.013 | 0.015 | 0.170 | 0.015 | 0.042 | 1 | borderline | -5 to +50 |
| A-P1p | 2.5 | 2.5/3/3.5 | +0 to +0 | 0.3653 | 0.3416 to 0.3656 | 0.005 | 0.006 | 0.131 | 0.040 | 0.078 | 1+ | borderline/impaired | +0 to +0 |
| B-claspknife | 6 | 6/6.5 | -10 to +50 | 0.1394 | 0.1265 to 0.1396 | 0.015 | 0.019 | 0.156 | 0.001 | 0.029 | 1 | healthy | -50 to +50 |
| B-clonus | 5.5 | 5.5/6 | -30 to +50 | 0.0800 | 0.0692 to 0.0804 | 0.005 | 0.028 | 0.258 | 0.011 | 0.025 | 0 | healthy | -50 to +50 |
| B-cogwheel | 1 | 1 | -50 to +50 | 0.3465 | 0.3381 to 0.3474 | 0.014 | 0.010 | 0.163 | 0.000 | 0.156 | 1+ | borderline | -50 to +50 |
| B-contracture | 7 | 7/7.5 | -10 to +50 | 0.0390 | 0.0338 to 0.0394 | 0.020 | 0.029 | 0.221 | 0.014 | 0.027 | 0 | healthy | -50 to +50 |
| B-extensor | 5 | 4.5/5 | -5 to +50 | 0.1924 | 0.1829 to 0.2052 | 0.013 | 0.015 | 0.170 | 0.015 | 0.042 | 1 | borderline | -5 to +50 |
| B-flexor | 3 | 3/3.5 | -5 to +50 | 0.2916 | 0.2741 to 0.2929 | 0.021 | 0.006 | 0.205 | 0.030 | 0.075 | 1/1+ | borderline | -50 to +50 |
| B-guarding | 13.5 | 13.5/14 | +0 to +50 | 0.0425 | 0.0373 to 0.0428 | 0.021 | 0.029 | 0.219 | 0.001 | 0.022 | 0 | healthy | -50 to +50 |
| B-habit1 | 5 | 4.5/5 | -5 to +50 | 0.1924 | 0.1829 to 0.2052 | 0.013 | 0.015 | 0.170 | 0.015 | 0.042 | 1 | borderline | -5 to +50 |
| B-habit2 | 5 | 5/5.5 | -15 to +50 | 0.1854 | 0.1707 to 0.1856 | 0.012 | 0.017 | 0.173 | 0.013 | 0.040 | 1 | borderline/healthy | -50 to +50 |
| B-habit3 | 5 | 5/5.5 | -15 to +50 | 0.1797 | 0.1650 to 0.1799 | 0.012 | 0.017 | 0.176 | 0.013 | 0.038 | 1 | borderline/healthy | -50 to +50 |
| B-habit4 | 5 | 5/5.5 | -10 to +35 | 0.1755 | 0.1523 to 0.1757 | 0.013 | 0.018 | 0.178 | 0.013 | 0.038 | 1 | borderline/healthy | -50 to +35 |
| B-habit5 | 5.5 | 5.5/6 | -15 to +50 | 0.1594 | 0.1448 to 0.1595 | 0.016 | 0.017 | 0.178 | 0.012 | 0.037 | 1 | healthy | -50 to +50 |
| B-hypotonic | 12.5 | 12.5/13 | +0 to +50 | 0.0372 | 0.0315 to 0.0377 | 0.033 | 0.038 | 0.266 | 0.003 | 0.013 | 0 | healthy | -50 to +50 |
| B-leadpipe | 1 | 1 | -50 to +50 | 0.3434 | 0.3348 to 0.3443 | 0.014 | 0.011 | 0.173 | 0.000 | 0.136 | 1+ | borderline | -50 to +50 |
| B-mixed | 2.5 | 2.5/3/3.5 | +0 to +0 | 0.3653 | 0.3416 to 0.3656 | 0.005 | 0.006 | 0.131 | 0.040 | 0.078 | 1+ | borderline/impaired | +0 to +0 |
| B-spasm | 6 | 6/6.5 | -30 to +50 | 0.1946 | 0.1843 to 0.1957 | 0.005 | 0.020 | 0.131 | 0.011 | 0.030 | 1 | borderline | -50 to +50 |
| C-A0-high | 7 | 7/7.5 | +0 to +50 | 0.0417 | 0.0363 to 0.0417 | 0.024 | 0.029 | 0.221 | 0.007 | 0.027 | refused | refused | -50 to +50 |
| C-A0-low | 6.5 | 6.5/7 | -5 to +50 | 0.0388 | 0.0330 to 0.0389 | 0.025 | 0.029 | 0.236 | 0.008 | 0.027 | refused | refused | -50 to +50 |
| C-A0-small-ok | 7 | 7/7.5 | -5 to +50 | 0.0394 | 0.0340 to 0.0395 | 0.024 | 0.029 | 0.232 | 0.008 | 0.027 | refused | refused | -50 to +50 |
| C-N-high | 27.5 | 27/27.5 | -50 to +0 | 0.0310 | 0.0268 to 0.0316 | 0.027 | 0.025 | 0.196 | 0.002 | 0.005 | 0 | healthy | -50 to +50 |
| C-N-zero | 0 | 0 | -50 to +50 | 1.4585 | 1.4193 to 1.4857 | 0.000 | 0.000 | 0.068 | 0.000 | 0.057 | refused | refused | -50 to +50 |
| C-R2n-high | 12.5 | 12.5/13 | +0 to +50 | 0.0401 | 0.0393 to 0.0405 | 0.014 | 0.003 | 0.071 | 0.004 | 0.017 | 0 | healthy | -50 to +50 |
| C-R2n-low | 5 | 5/5.5 | -50 to +15 | 0.2751 | 0.2592 to 0.2756 | 0.007 | 0.009 | 0.109 | 0.045 | 0.027 | 1 | borderline | -50 to +50 |
| C-area-high | 7.5 | 7.5/8 | -50 to +20 | 0.0551 | 0.0479 to 0.0564 | 0.076 | 0.032 | 0.521 | 0.002 | 0.029 | 0 | healthy | -50 to +20 |
| C-f-high | 7 | 7/7.5 | -5 to +45 | 0.2350 | 0.2330 to 0.2407 | 0.149 | 0.192 | 3.709 | 0.022 | 0.056 | 1 | borderline | -50 to +45 |
| C-f-low | 7 | 7/7.5 | -5 to +50 | 0.1875 | 0.1868 to 0.1875 | 0.006 | 0.007 | 0.027 | 0.003 | 0.015 | 1 | borderline | -50 to +50 |
| C-phi-high | 8 | 8/8.5 | -25 to +50 | 0.0550 | 0.0548 to 0.0551 | 0.001 | 0.002 | 0.001 | 0.003 | 0.012 | 0 | healthy | -50 to +50 |
| C-phi-low | 0.5 | 0.5 | -50 to +50 | 0.6804 | 0.5825 to 0.7849 | 0.021 | 0.000 | 0.220 | 0.000 | 0.185 | 2/3/4 | impaired | -50 to +50 |
| C-wmax-high | 7 | 7/7.5 | +0 to +50 | 0.1533 | 0.1533 to 0.1585 | 0.089 | 0.118 | 1.823 | 0.021 | 0.049 | 1 | healthy | -15 to +50 |
| C-wmax-low | 7 | 7/7.5 | -5 to +50 | 0.1875 | 0.1868 to 0.1875 | 0.006 | 0.007 | 0.027 | 0.003 | 0.015 | 1 | borderline | -50 to +50 |
| C-wmin-high | 15 | 14.5/15 | -50 to +0 | 0.0464 | 0.0408 to 0.0470 | 0.024 | 0.036 | 0.243 | 0.001 | 0.010 | 0 | healthy | -50 to +50 |
| D-lowlift | 7 | 7/7.5 | -5 to +50 | 0.0390 | 0.0336 to 0.0394 | 0.024 | 0.029 | 0.233 | 0.008 | 0.027 | refused | refused | -50 to +50 |
| D-push | 7 | 7/7.5 | -30 to +50 | 0.0360 | 0.0192 to 0.0373 | 0.070 | 0.090 | 0.711 | 0.008 | 0.025 | 0 | healthy | -50 to +50 |
| D-short | 2.5 | 2.5/3 | -5 to +50 | 0.4404 | 0.3962 to 0.4547 | 0.009 | 0.029 | 0.136 | 0.022 | 0.048 | 1+/2 | impaired | -50 to +50 |
| D-wobble | 7 | 7/7.5 | -10 to +50 | 0.0424 | 0.0363 to 0.0508 | 0.040 | 0.049 | 0.392 | 0.008 | 0.024 | 0 | healthy | -15 to +50 |
| H-HM1 | 4.5 | 4.5/5 | -20 to +50 | 0.2440 | 0.2277 to 0.2443 | 0.009 | 0.011 | 0.194 | 0.019 | 0.053 | 1 | borderline | -50 to +50 |
| H-HM2 | 0 | 0 | -50 to +50 | 1.5565 | 1.5377 to 1.5658 | 0.000 | 0.000 | 0.180 | 0.000 | 0.020 | refused | refused | -50 to +50 |
| H-HM3 | 2.5 | 2.5 | -50 to +50 | 0.2906 | 0.2903 to 0.3121 | 0.010 | 0.012 | 0.172 | 0.004 | 0.081 | 1+ | borderline | -50 to +50 |
| H-HM4 | 3.5 | 3.5 | -50 to +50 | 0.2247 | 0.2147 to 0.2259 | 0.013 | 0.021 | 0.158 | 0.000 | 0.047 | 1 | borderline | -50 to +50 |
| K-HM1 | 5.5 | 5.5/6 | -15 to +50 | 0.1751 | 0.1609 to 0.1760 | 0.013 | 0.016 | 0.198 | 0.012 | 0.041 | 1 | borderline/healthy | -50 to +50 |
| K-HM2 | 3 | 3/3.5 | -25 to +50 | 0.3269 | 0.3082 to 0.3272 | 0.010 | 0.007 | 0.186 | 0.036 | 0.078 | 1+ | borderline | -50 to +50 |
| M-catch_and_hold_127 | 0 | 0 | -50 to +50 | 1.6700 | 1.6679 to 1.6701 | 0.000 | 0.000 | 0.073 | 0.000 | 0.000 | refused | refused | -50 to +50 |
| M-catch_and_hold_140 | 0 | 0 | -50 to +50 | 1.6724 | 1.6701 to 1.6725 | 0.000 | 0.000 | 0.078 | 0.000 | 0.000 | refused | refused | -50 to +50 |
| M-catch_release_high | 6 | 6/6.5 | -10 to +50 | 0.1394 | 0.1265 to 0.1396 | 0.015 | 0.019 | 0.156 | 0.001 | 0.029 | 1 | healthy | -50 to +50 |
| M-contracture_stop | 7 | 7/7.5 | -10 to +50 | 0.0390 | 0.0338 to 0.0394 | 0.020 | 0.029 | 0.221 | 0.014 | 0.027 | 0 | healthy | -50 to +50 |
| M-fall_no_ringdown | 1 | 1 | -50 to +50 | 0.3434 | 0.3348 to 0.3443 | 0.014 | 0.011 | 0.173 | 0.000 | 0.136 | 1+ | borderline | -50 to +50 |
| M-tone_L0 | 7 | 7/7.5 | +0 to +50 | 0.0417 | 0.0363 to 0.0417 | 0.024 | 0.029 | 0.221 | 0.007 | 0.027 | 0 | healthy | -50 to +50 |
| M-tone_L1 | 5.5 | 5.5/6 | -10 to +5 | 0.1142 | 0.0917 to 0.1144 | 0.016 | 0.021 | 0.195 | 0.011 | 0.033 | 0 | healthy | -50 to +5 |
| M-tone_L2 | 4.5 | 4.5/5 | -15 to +50 | 0.2251 | 0.2100 to 0.2253 | 0.014 | 0.014 | 0.164 | 0.014 | 0.045 | 1 | borderline | -50 to +50 |
| M-tone_L3 | 2.5 | 2.5/3 | -30 to +50 | 0.3729 | 0.3649 to 0.3746 | 0.004 | 0.006 | 0.127 | 0.032 | 0.082 | 1+ | impaired | -50 to +50 |
| M-tone_L4 | 2 | 2/2.5 | -35 to +45 | 0.4199 | 0.4119 to 0.4534 | 0.003 | 0.005 | 0.118 | 0.057 | 0.098 | 1+/2 | impaired | -50 to +45 |
| R-anthro-72kg | 10.5 | 10.5/11 | +0 to +50 | 0.0704 | 0.0667 to 0.0704 | 0.019 | 0.025 | 0.159 | 0.004 | 0.018 | 0 | healthy | -50 to +50 |
| R-anthro-heavy | 11 | 11/11.5 | +0 to +50 | 0.0693 | 0.0655 to 0.0693 | 0.021 | 0.026 | 0.164 | 0.003 | 0.015 | 0 | healthy | -50 to +50 |
| R-friction | 3.5 | 3.5/4 | -5 to +50 | 0.1366 | 0.1214 to 0.1366 | 0.020 | 0.024 | 0.212 | 0.013 | 0.042 | 1 | healthy | -50 to +50 |
| R-friction-heavy | 2.5 | 2.5/3 | -5 to +50 | 0.1906 | 0.1750 to 0.1919 | 0.019 | 0.021 | 0.198 | 0.021 | 0.060 | 1 | borderline | -50 to +50 |
| R-reflex-force | 3.5 | 3.5/4 | -50 to +10 | 0.3277 | 0.3058 to 0.3280 | 0.010 | 0.015 | 0.083 | 0.021 | 0.049 | 1+ | borderline | -50 to +10 |
| R-reflex-len | 14.5 | 14.5/15 | -20 to +50 | 0.1786 | 0.1671 to 0.1801 | 0.013 | 0.022 | 0.219 | 0.006 | 0.010 | 1 | borderline/healthy | -50 to +50 |
| R-reflex-sat | 0 | 0 | -50 to +50 | 1.5739 | 1.5651 to 1.5806 | 0.000 | 0.000 | 0.049 | 0.000 | 0.012 | refused | refused | -50 to +50 |
| R-reflex-vel | 3 | 3 | -50 to +50 | 0.3387 | 0.3347 to 0.3395 | 0.007 | 0.009 | 0.088 | 0.004 | 0.066 | 1+ | borderline | -50 to +50 |
| S-lam1 | 3.5 | 3.5/4 | -50 to +10 | 0.3277 | 0.3058 to 0.3280 | 0.010 | 0.015 | 0.083 | 0.021 | 0.049 | 1+ | borderline | -50 to +10 |
| S-lam1.5 | 4.5 | 4.5 | -50 to +50 | 0.1887 | 0.1793 to 0.1894 | 0.013 | 0.017 | 0.143 | 0.000 | 0.013 | 1 | borderline | -50 to +50 |
| S-lam2 | 4 | 4 | -50 to +50 | 0.1855 | 0.1854 to 0.1855 | 0.002 | 0.001 | 0.001 | 0.000 | 0.002 | 1 | borderline | -50 to +50 |
| S-lam3 | 3 | 3 | -50 to +50 | 0.2514 | 0.2514 to 0.2514 | 0.002 | 0.001 | 0.002 | 0.000 | 0.012 | refused | refused | -50 to +50 |
| S-lam6 | 2.5 | 2.5/3 | +0 to +50 | 0.2514 | 0.2409 to 0.2514 | 0.024 | 0.014 | 0.297 | 0.033 | 0.081 | refused | refused | -50 to +50 |

## 3. Prevalence on real data

179 phone trials from the replay cache of 2026-11-10 (`run_lineage.py`, `out/main`). The scorer and replay
code are unchanged between that run and `e9a3a01` (the merges since touched reports, a plan, an evaluation
script and neuromech build and session files), and re-scoring the cached curves reproduces the cached
parameters on all 324 scorings. One trial belongs to a test participant and is dropped. Of the 178, 162
score under each convention and 143 are interpretable. Excluded trials are not in the list.

Release: `_detect_release`, the scorer default. Shifts are whole samples at 100 Hz (10 ms each), passed as
`release_idx`; passing the auto-detected index back reproduces the auto result on 162 of 162.

### How many carry the shoulder

| | prod | detrend |
|---|---|---|
| scored trials | 162 | 162 |
| first counted extremum within 0.2 s of release | 40 | 50 |
| ... of which a peak | 40 | 50 |
| first counted extremum is a peak that precedes the first trough (any time) | 46 | 55 |
| shoulder prominence over threshold: median (5th to 95th percentile) | 1.31 (1.05 to 2.07) | 1.27 (1.04 to 2.65) |
| shoulder prominence under 1.5 / under 2 thresholds | 28 / 37 | 33 / 44 |
| shoulder prominence in degrees: median (5th to 95th) | 4.08 (3.48 to 5.89) | 3.95 (2.60 to 6.34) |
| shoulder prominence over the prominence of the first trough: median (5th to 95th) | 0.07 (0.05 to 0.17) | 0.08 (0.05 to 0.20) |
| shoulder prominence over the prominence of the next true peak: median (5th to 95th) | 0.09 (0.06 to 0.33) | 0.10 (0.05 to 0.37) |
| shoulder trials where N is below 6.5, so the half-cycle moves the PT score | 23 | 29 |

The 40 matches the count in the release-detector report. All 40 are interpretable trials.

Whether a trial carries the shoulder is itself a function of the release. Shipped scorer, prod, number of
the 162 trials with a counted peak inside 0.2 s when the release is moved:

| shift | -30 ms | -20 ms | -10 ms | 0 | +10 ms | +20 ms | +30 ms |
|---|---|---|---|---|---|---|---|
| trials with a shoulder | 89 | 78 | 56 | 40 | 26 | 12 | 8 |

Only 71 of 162 trials have no shoulder anywhere in that range, and 6 have one everywhere.

### What moves when the release moves (shipped scorer)

Trials, of 162, whose value differs from the value at the auto-detected release. The last three columns
count a trial if it differs at either sign within that distance.

| prod | -30 ms | -20 ms | -10 ms | +10 ms | +20 ms | +30 ms | within 10 | within 20 | within 30 |
|---|---|---|---|---|---|---|---|---|---|
| N | 54 | 41 | 20 | 14 | 31 | 37 | 34 | 73 | 92 |
| PT score by 0.005 or more | 57 | 45 | 31 | 26 | 60 | 83 | 37 | 79 | 100 |
| MAS band | 7 | 4 | 3 | 3 | 11 | 13 | 6 | 15 | 20 |
| zone | 3 | 2 | 1 | 2 | 4 | 5 | 3 | 6 | 8 |
| interpretable | 0 | 0 | 0 | 0 | 1 | 1 | 0 | 1 | 1 |

| detrend | -30 ms | -20 ms | -10 ms | +10 ms | +20 ms | +30 ms | within 10 | within 20 | within 30 |
|---|---|---|---|---|---|---|---|---|---|
| N | 62 | 45 | 22 | 23 | 34 | 48 | 45 | 79 | 103 |
| PT score by 0.005 or more | 54 | 39 | 26 | 27 | 46 | 72 | 38 | 68 | 93 |
| MAS band | 6 | 4 | 4 | 3 | 7 | 12 | 7 | 11 | 17 |
| zone | 6 | 5 | 3 | 0 | 1 | 2 | 3 | 6 | 7 |

Largest PT change within 30 ms, prod: median 0.01, 90th percentile 0.03, max 0.09. N never moves by more
than 1.0.

The PT movement is not all N. Mean absolute change in the PT contribution of each term for a 10 ms shift
(units of 0.001), then the number of trials where that term moves by 0.005 or more:

| prod, shipped | R2n | N | phi_max_ratio | omega_max_n | f | area_ratio |
|---|---|---|---|---|---|---|
| -10 ms | 0.16 / 1 | 0.98 / 10 | 0.43 / 1 | 0.35 / 0 | 1.16 / 3 | 1.44 / 22 |
| +10 ms | 0.12 / 0 | 0.32 / 4 | 0.39 / 0 | 0.42 / 0 | 0.17 / 1 | 1.78 / 23 |

`N` and `f` move through the shoulder (the extra extremum changes the spacing `f` is computed from).
`area_ratio` moves for a different reason: the symmetry integral starts at the release, so every extra
still sample adds positive area. That is a continuous effect, it is the largest single contributor, and
none of the remedies in section 5 changes it.

### The traces

I plotted the release region of all 46 trials whose first counted extremum is a peak before the first
trough (angle about the settled value, the swing centre, and the centred signal the second pass searches)
and read each one.

- **40 (first peak 0.04 to 0.17 s after release): shoulders.** The angle leaves the hold on a smooth
  monotone fall, sometimes after a short plateau or a kink of under 2.4 deg. The counted peak is in the
  centred signal only, 3.5 to 6 deg prominent against true swing extrema of 20 to 60 deg. Four of them
  (13_right_week_1_post_T4, 21_left_pre_T5, 17_right_post_1_week_T2, 13_left_month_post_T1, at 0.11 to
  0.17 s) show a visible hesitation in the angle itself about 0.1 s into the fall; the centred frame turns
  that hesitation into a countable peak. None of the four is a half-cycle.
- **6 (first peak 0.37 s or later): not shoulders.** Two are release-detection failures that put the
  release 0.89 s and 1.65 s before the limb moves, so the true release shows up as a peak. Two are real
  rebounds on heavily damped limbs whose troughs never pass below the settled angle, so no trough is
  counted before them. One is a single drop with a small late extremum. One is a small bump followed by a
  catch during the fall.

"N at -30..+30 ms" is the shipped N with the release moved by -30, -20, -10, 0, +10, +20, +30 ms.

| trial | first peak after release (s) | prominence / threshold | rise above release value, uncentred (deg) | threshold (deg) | N shipped | N at -30..+30 ms | reading |
|---|---|---|---|---|---|---|---|
| 4_left_pre_T3 | 0.04 | 1.11 | 1.52 | 3.36 | 7 | 7/7/7/7/6.5/6.5/6.5 | shoulder |
| 17_right_post_1_week_T1 | 0.05 | 1.20 | 0.51 | 4.00 | 7.5 | 7.5/7.5/7.5/7.5/7/7/7 | shoulder |
| 17_right_post_T3 | 0.05 | 1.27 | 0.96 | 3.42 | 6.5 | 6.5/6.5/6.5/6.5/6.5/6/6 | shoulder |
| 19_left_pre_T3 | 0.05 | 1.17 | 0.98 | 3.49 | 5.5 | 5.5/5.5/5.5/5.5/5/5/5 | shoulder |
| 23_left_pre_T2 | 0.05 | 1.15 | 0.64 | 3.50 | 7 | 7/7/7/7/6.5/6.5/6.5 | shoulder |
| 23_right_pre_T1 | 0.05 | 1.08 | 0.82 | 4.05 | 6.5 | 6.5/6.5/6.5/6.5/6/6/6 | shoulder |
| 13_left_week_1_post_T4 | 0.06 | 1.21 | 0.30 | 2.92 | 4.5 | 4.5/4.5/4.5/4.5/4.5/4/4 | shoulder |
| 14_right_pre_T1 | 0.06 | 1.32 | 0.75 | 3.10 | 4.5 | 4.5/4.5/4.5/4.5/4.5/4/4 | shoulder |
| 15_left_post_1week_T2 | 0.06 | 1.30 | 1.07 | 3.34 | 10 | 10/10/10/10/9.5/9.5/9.5 | shoulder |
| 15_left_post_1week_T3 | 0.06 | 1.05 | 0.71 | 3.70 | 9 | 9/9/9/9/8.5/8.5/8.5 | shoulder |
| 17_left_post_1_month_T3 | 0.06 | 1.16 | 0.21 | 3.20 | 7 | 7/7/7/7/6.5/6.5/6.5 | shoulder |
| 17_left_post_T3 | 0.06 | 2.13 | 2.35 | 2.83 | 7 | 7/7/7/7/7/7/6.5 | shoulder |
| 17_right_post_1_month_T2 | 0.06 | 1.02 | 0.07 | 3.85 | 7.5 | 7.5/7.5/7.5/7.5/7/7/7 | shoulder |
| 17_right_post_1_week_T3 | 0.06 | 1.31 | 0.91 | 3.99 | 7.5 | 7.5/7.5/7.5/7.5/7/7/7 | shoulder |
| 17_right_post_1_week_T4 | 0.06 | 1.10 | 0.43 | 3.89 | 7.5 | 7.5/7.5/7.5/7.5/7/7/7 | shoulder |
| 19_left_pre_T1 | 0.06 | 1.74 | 1.73 | 3.38 | 4.5 | 4.5/4.5/4.5/4.5/4.5/4.5/4.5 | shoulder |
| 19_left_pre_T2 | 0.06 | 1.34 | 0.45 | 3.19 | 5 | 5/5/5/5/5/4.5/4.5 | shoulder |
| 21_left_pre_T2 | 0.06 | 1.37 | 0.87 | 2.68 | 2.5 | 2.5/2.5/2.5/2.5/2.5/2/2 | shoulder |
| 22_right_pre_T1 | 0.06 | 1.84 | 1.31 | 2.94 | 3 | 3/3/3/3/3/2.5/2.5 | shoulder |
| 22_right_pre_T2 | 0.06 | 1.63 | 0.61 | 2.78 | 3 | 3/3/3/3/3/2.5/2.5 | shoulder |
| 4_left_pre_T4 | 0.06 | 1.43 | 1.59 | 3.03 | 6.5 | 6.5/6.5/6.5/6.5/6.5/6/6 | shoulder |
| 4_right_pre_T5 | 0.06 | 1.63 | 0.73 | 2.25 | 6 | 5.5/6/6/6/6/6/6 | shoulder |
| 14_right_pre_T2 | 0.06 | 1.11 | 0.82 | 2.99 | 3 | 3/3/3/3/2.5/2.5/2.5 | shoulder |
| 17_left_post_T1 | 0.06 | 1.76 | 1.47 | 3.27 | 6 | 6/6/6/6/6/6/5.5 | shoulder |
| 22_right_pre_T4 | 0.06 | 1.41 | 0.62 | 3.13 | 3 | 3/3/3/3/3/2.5/2.5 | shoulder |
| 15_right_post_1month_T2 | 0.07 | 1.40 | 0.40 | 2.96 | 5 | 5/5/5/5/5/5/5 | shoulder |
| 15_right_post_1month_T3 | 0.07 | 1.45 | 0.15 | 2.53 | 4.5 | 4.5/4.5/4.5/4.5/4.5/4.5/4 | shoulder |
| 15_right_post_1week_T1 | 0.07 | 1.12 | 0.00 | 3.10 | 3.5 | 3.5/3.5/3.5/3.5/3/3/3 | shoulder |
| 15_right_post_1week_T2 | 0.07 | 1.06 | 0.14 | 3.57 | 7.5 | 7.5/7.5/7.5/7.5/7/7/7 | shoulder |
| 21_left_pre_T4 | 0.07 | 2.07 | 0.67 | 2.40 | 2.5 | 2.5/2.5/2.5/2.5/2.5/2.5/2 | shoulder |
| 23_right_pre_T4 | 0.07 | 1.11 | 0.07 | 3.27 | 8 | 8/8/8/8/8/7.5/7.5 | shoulder |
| 4_left_june_post_T2 | 0.07 | 1.79 | 0.33 | 2.16 | 6 | 6/6/6/6/6/6/6 | shoulder |
| 13_right_month_post_T2 | 0.08 | 1.62 | 0.66 | 2.65 | 3 | 3/3/3/3/3/3/2.5 | shoulder |
| 17_right_post_T1 | 0.08 | 1.05 | 0.00 | 3.49 | 6 | 6/6/6/6/6/5.5/5.5 | shoulder |
| 20_right_pre_T1 | 0.09 | 1.33 | 0.00 | 2.43 | 8 | 8/8/8/8/8/8/8 | shoulder |
| 22_right_pre_T3 | 0.09 | 1.16 | 0.00 | 3.09 | 3.5 | 3.5/3.5/3.5/3.5/3.5/3/3 | shoulder |
| 13_right_week_1_post_T4 | 0.11 | 1.16 | 0.74 | 3.03 | 3 | 3/3/3/3/3/2.5/2.5 | shoulder |
| 21_left_pre_T5 | 0.12 | 1.72 | 0.00 | 2.67 | 2 | 2/2/2/2/2/1.5/1.5 | shoulder |
| 17_right_post_1_week_T2 | 0.15 | 2.21 | 0.00 | 3.41 | 7.5 | 7.5/7.5/7.5/7.5/7.5/7.5/7.5 | shoulder |
| 13_left_month_post_T1 | 0.17 | 1.75 | 0.00 | 2.16 | 3.5 | 3.5/3.5/3.5/3.5/3.5/3.5/3.5 | shoulder |
| 13_right_week_1_post_T2 | 0.37 | 2.72 | 1.70 | 4.52 | 1 | 1/1/1/1/1/1/1 | small bump, then a catch during the fall; not a shoulder |
| 21_left_pre_T3 | 0.69 | 4.15 | 0.00 | 3.11 | 1 | 1/1/1/1/1/1/1 | real rebound; troughs never pass below the settled angle, so no trough is counted |
| 14_left_pre_T3 | 0.84 | 7.49 | 2.95 | 3.33 | 2 | 2/2/2/2/2/2/2 | release detected 0.89 s early; the peak is the true release |
| 13_right_month_post_T4 | 0.88 | 6.85 | 0.26 | 3.10 | 1 | 1/1/1/1/1/1/1 | real rebound; troughs never pass below the settled angle, so no trough is counted |
| 14_left_pre_T1 | 1.58 | 1.84 | 5.31 | 2.89 | 1 | 0/1/1/1/1/1/1 | release detected 1.65 s early; the peak is the true release |
| 21_right_pre_T3 | 2.86 | 1.12 | 0.00 | 1.92 | 0.5 | 0/0/0/0.5/0.5/0.5/0.5 | single drop with a small late extremum |

## 4. Consequence

### Test-retest scatter

Groups are participant x leg x condition with at least two scored phone trials: 40 groups, 162 trials.
ICC is `reliability_stats.icc_one_way` (ICC(1,1)). "Shoulder excluded" is option B of section 5, which
drops a peak found before the first counted trough. This is an analysis, not a re-scoring of record.

| prod | ICC(1,1) of N | within-group SD of N | ICC(1,1) of PT | within-group SD of PT |
|---|---|---|---|---|
| shoulder included (shipped) | 0.696 [0.569, 0.794] | 1.507 | 0.627 [0.485, 0.742] | 0.2050 |
| shoulder excluded | 0.703 [0.578, 0.799] | 1.479 | 0.623 [0.481, 0.739] | 0.2046 |

| prod, interpretable trials only (37 groups, 141 trials) | ICC(1,1) of N | SEM of N | ICC(1,1) of PT | SEM of PT |
|---|---|---|---|---|
| shoulder included (shipped) | 0.718 [0.588, 0.815] | 1.078 | 0.626 [0.475, 0.747] | 0.0915 |
| shoulder excluded | 0.729 [0.602, 0.822] | 1.055 | 0.629 [0.478, 0.749] | 0.0878 |

| detrend | ICC(1,1) of N | within-group SD of N | ICC(1,1) of PT | within-group SD of PT |
|---|---|---|---|---|
| shoulder included (shipped) | 0.670 [0.538, 0.775] | 1.548 | 0.474 [0.316, 0.617] | 0.2339 |
| shoulder excluded | 0.684 [0.554, 0.785] | 1.486 | 0.454 [0.296, 0.600] | 0.2363 |

Share of the within-group sum of squares removed by excluding the shoulder: N 4.7% (prod) and 6.7%
(detrend); PT 0.5% (prod) and -1.8% (detrend, i.e. slightly worse).

So the shoulder is real and frequent, but it accounts for about a twentieth of the trial-to-trial variance
of N and for none of the variance of the PT score. The intervals overlap almost entirely. In 22 of the 40
groups some trials have a peak before the first trough and others do not, which is where it adds scatter;
the within-group SD of N is 1.5 cycles, and a 0.5 step on a quarter of trials is small against that.
**Inference:** the corpus test-retest scatter is dominated by real trial-to-trial differences in the swing
and by other scorer sensitivities, not by this one. The cases where the shoulder matters are single-trial
decisions near a band edge, and bench comparisons where the same motion is scored twice with releases 10
to 20 ms apart, which is exactly where the three earlier reports met it.

### Healthy reference

`HEALTHY_REF["N"] = 6.5` is literature-anchored, not a cohort statistic. To see whether the cohort number
it is compared against is biased, I reproduced the selection in `derive_healthy_ref.py` (baseline control
optical trials, auto release, default `detrend=True`, interpretable only): 63 baseline control optical
trials, 11 unloadable or unscorable, 39 interpretable across 7 participants and 12 legs. The trial medians
of `f` (0.9511) and `area_ratio` (0.1766) reproduce the shipped `HEALTHY_REF` values to four places, which
is the check that this is the same population.

| control optical trials (39) | trial median N | mean N | leg median N | leave-one-participant-out range | trials with N under 6.5 |
|---|---|---|---|---|---|
| shipped | 6.0 | 6.24 | 6.25 | 6.0 to 6.5 | 20 |
| shoulder excluded (option B) | 6.0 | 6.08 | 6.25 | 6.0 to 6.5 | 20 |

7 of the 39 carry a shoulder (5 with prominence 1.1 to 2.0 thresholds, 2 at 4.5). The median, the leg
median and the leave-one-out range do not move; the mean drops by 0.17. Trial median `f` goes 0.9511 to
0.9501 and `area_ratio` does not change. **The healthy reference for N is not biased by the shoulder.**
The optical curves are 120 Hz, so the shifts there are 1, 2 and 4 samples of 8.3 ms: N changes within
those in 3 / 10 / 17 of the 39 shipped, 0 / 2 / 6 with option B.

## 5. Options

All five were emulated by patching `compute_pt_params` in memory; with no option selected the patched
function reproduces the shipped one exactly on all 324 corpus scorings.

- **A. Minimum fraction of the first swing.** Drop any extremum earlier than 0.5 x the first-pass half
  period after the release.
- **B. Count from the first trough.** After the second pass and before the window bound, drop any peak
  whose index is below the first trough that clears the count gate. If no trough is counted, drop nothing.
- **C. Do not extrapolate the centre into the release.** For detection and the count gates only, hold the
  boxcar centre at its first full-window value over the first half period; the symmetry integral keeps
  the shipped centre.
- **D. Higher prominence floor.** `prominence = max(min_amp, 0.10 * A0)` in both passes.
- **E. Continuous N.** `N = (time of last counted extremum - release) / (2 x median extremum spacing)`,
  using the shipped extrema.

### Measured effect

Synthetic, 67 scenarios, release moved within 10 / 20 / 30 / 50 ms:

| | shipped | A | B | C | D | E |
|---|---|---|---|---|---|---|
| N changes | 28 / 42 / 47 / 52 | 8 / 13 / 14 / 20 | 6 / 10 / 11 / 14 | 9 / 11 / 12 / 16 | 1 / 4 / 9 / 30 | 8 / 12 / 13 / 16 |
| MAS band changes | 2 / 2 / 3 / 4 | 2 / 2 / 3 / 5 | 1 / 1 / 2 / 3 | 1 / 2 / 2 / 4 | 1 / 1 / 2 / 3 | 1 / 1 / 2 / 3 |
| zone changes | 0 / 4 / 6 / 7 | 1 / 1 / 3 / 8 | 0 / 0 / 2 / 6 | 5 / 5 / 7 / 10 | 1 / 2 / 2 / 5 | 0 / 0 / 0 / 5 |
| stable over the full sweep | 15 | 47 | 53 | 51 | 37 | 51 |
| scenarios whose expected value at the exact release changes | - | 4 | 6 | 44 | 37 | 60 |
| ... of which a MAS band change | - | 2 | 1 | 3 | 1 | 0 |

(For E, "N changes" means by more than 0.25.)

Corpus, 162 prod trials, release moved within 10 / 20 / 30 ms:

| | shipped | A | B | C | D | E |
|---|---|---|---|---|---|---|
| N changes | 34 / 73 / 92 | 10 / 13 / 20 | 4 / 7 / 11 | 5 / 8 / 12 | 8 / 17 / 23 | 4 / 7 / 11 |
| PT changes by 0.005 or more | 37 / 79 / 100 | 29 / 51 / 76 | 26 / 50 / 74 | 25 / 51 / 78 | 22 / 50 / 79 | 29 / 52 / 75 |
| MAS band changes | 6 / 15 / 20 | 5 / 13 / 16 | 5 / 12 / 15 | 8 / 16 / 19 | 3 / 11 / 14 | 2 / 13 / 16 |
| zone changes | 3 / 6 / 8 | 7 / 9 / 12 | 1 / 3 / 4 | 1 / 3 / 5 | 1 / 4 / 4 | 2 / 3 / 5 |
| first counted extremum within 0.2 s, at the auto release | 40 | 0 | 0 | 1 | 4 | 40 (not counted in N) |

The PT row falls by about a third, not to zero, because of `area_ratio` (section 3).

### What each would change in reported scores (corpus, prod, at the auto-detected release)

| | A | B | C | D | E |
|---|---|---|---|---|---|
| trials whose N changes | 62 | 43 | 42 | 97 | 146 |
| ... by exactly -0.5 | 58 | 41 | 41 | 69 | - |
| ... by -1.0 or more | 4 | 0 | 0 | 26 | 0 |
| mean change in N | -0.24 | -0.12 | -0.12 | -0.40 | -0.06 |
| trials whose PT changes by 0.001 or more | 53 | 35 | 68 | 89 | 72 |
| mean change in PT | +0.0164 | -0.0005 | +0.0007 | +0.0657 | +0.0013 |
| largest PT change | 0.370 | 0.212 | 0.237 | 1.159 | 0.101 |
| MAS band changes | 13 | 7 | 7 | 16 | 5 |
| zone changes | 8 | 2 | 2 | 10 | 2 |
| interpretable flips | 4 | 1 | 0 | 7 | 0 |
| trials whose `f` changes | 59 | 42 | 111 | 93 | 0 |
| ICC(1,1) of N / of PT (shipped 0.696 / 0.627) | 0.721 / 0.634 | 0.703 / 0.623 | 0.702 / 0.630 | 0.740 / 0.715 | 0.683 / 0.627 |

For B, on the 41 trials that lose half a cycle the PT score rises by a median 0.008 (range -0.063 to
+0.062; 13 of them by about the plain 0.0128 step, the rest differ because `f` moves with the spacing,
because N was already at or above 6.5, or because N crosses 2.0 and the `f` term appears or disappears).
The MAS-band and zone changes for B, by trial: 4_left_june_post_T2 and 4_right_pre_T5 (band 0 to 1, PT
0.113 to 0.128 and 0.116 to 0.129), 14_right_pre_T1 (1 to 1+), 19_left_pre_T3 (zone healthy to borderline,
PT 0.167 to 0.176), 21_left_pre_T5 (2 to 1+), 14_left_pre_T3 (1+ to 2; the release-detection failure),
13_right_week_1_post_T2 (3 to 2; N 1.0 to 2.0 because removing the early bump changes the window anchor),
and 14_left_pre_T4 (N 0.0 to 0.5, from refused to interpretable, for the same reason). The last two are
side effects through `_active_oscillation_window_end` and need a look before B lands.

Healthy reference under each option, 39 control optical trials: trial median N is 6.0 for A, B, C and D
and 6.34 for E; `f` 0.9511 (A, C, E), 0.9501 (B), 0.9424 (D).

### Conflicts with `b44dc7d` and with existing tests

I ran 469 tests against each emulation by replacing `pendulastic_pt_score.compute_pt_params` from a pytest
plugin: `tests/test_pt_score.py` (which holds the `b44dc7d` guard, the resting-tail and noisy-tail guards and
the sample-rate tests), `test_pt_zone_parity.py`, `test_derive_healthy_ref.py`,
`test_imu_calibration_tuner.py`, `test_web_export_contract.py`, `test_pt_report_common.py`,
`test_recalibrate_pt_zones.py`, `test_mas_validation.py` and all of `neuromech/tests`.

Shipped scorer: 465 passed, 3 xfailed, 1 failed. The failure is
`test_importing_profiles_does_not_build_them`, an import-time limit (3.53 s against 3.0 s) on a loaded
machine; it passed in every other run. The patched function with no option selected passes the same set.

| | fails in `tests/` | fails in `neuromech/tests` |
|---|---|---|
| A | none | `test_default_session_misses_exactly_the_two_edge_scenarios` |
| B | none | `test_default_session_misses_exactly_the_two_edge_scenarios` |
| C | none | the same, plus `test_the_designer_reports_what_the_python_scorer_says` and `test_the_rig_file_is_playable_and_scores_like_the_design_when_read_back` |
| D | none | as C |
| E | `test_oscillation_count_is_the_same_at_20_hz_and_120_hz` | as C |

- **None of A to D conflicts with `b44dc7d`.** Its guard
  (`test_a_limb_that_sags_after_the_swing_still_scores_as_it_swung`) and every other test in
  `tests/test_pt_score.py` pass under A, B, C and D.
- **The dry-run test fails because the finding it pins goes away.** It asserts that the default session
  misses its band on exactly B-claspknife and M-catch_release_high. Under B (and C) the same session, seed
  3, delivers the intended band on 9 of 9: encoder PT 0.1294 and 0.1284 for those two, band 1, where the
  shipped scorer gives about 0.116, band 0. That is the half-cycle the band-edge report traced. The test
  would need its expectation changed from two misses to none.
- **The two designer tests** compare scores written by the C# designer with the Python scorer. They fail
  under C, D and E because only the Python side was emulated; they pass under B because B does not change
  any of those designs.
- **`test_csharp_port_matches_python_scorer`** passes with no option and fails under B, as it must while
  the C# port is unchanged: of 355 cases the differences are N and `f` on traces where B drops a peak
  (for example S-lam2, N 3.5 in Python against 4.0 in C#). It is the test that would confirm a real port.
  It could not compile while six runs shared the machine, so it was run alone, and only for no option and B.
- E fails the sample-rate test because a continuous N of 6.94 is not the 7.0 the test requires.

### Size of the change

| | Python | Rust | C# | other |
|---|---|---|---|---|
| A | about 6 lines after the second pass; has to carry the first-pass half period out of the `if` | same | same | as B |
| B | about 5 lines after the second pass, before `_active_oscillation_window_end` | about 6 lines after the second pass in `scoring.rs` | about 8 lines after the second pass in `PtScore.cs` | Rust fixtures regenerated (`mobile-imu-core/tests/fixtures/gen_fixtures.py`), wasm rebuilt and `BUILD_ID` restamped, neuromech library rebuilt (6 expected values move), `SCORE_FORMULA_ID` bumped so stored scores are not mixed |
| C | about 8 lines: a second centre array, and three uses switched to it | same, plus the `phi_g` gate | same | as B, but 44 neuromech expected values move |
| D | one expression in four `find_peaks` calls | same | same | as B; the cohort median of `f` moves 0.9511 to 0.9424 |
| E | about 10 lines; N becomes a float with a different meaning | same; the JSON contract and the display of N in the app change | same | `HEALTHY_REF["N"]` needs re-anchoring (cohort median 6.0 to 6.34); every stored N is incomparable |

Line counts are estimates from the emulation, not from a written patch.

## 6. Recommendation

**Option B: count N from the first trough.**

- It states the definition the scorer already assumes. The count excludes the release (the first pass
  cannot return index 0); a peak before the first trough is the release seen through the centred frame, or
  the true release after an early detection. Neither is a half-cycle.
- It is the most effective of the small changes on both data sets (corpus N changes within 10 / 20 / 30 ms:
  34 / 73 / 92 to 4 / 7 / 11; synthetic 28 / 42 / 47 to 6 / 10 / 11) and moves the fewest reported scores
  (35 trials by 0.001 or more, mean PT change -0.0005).
- It leaves the centred frame, trough detection, `A0` and the symmetry integral exactly as `b44dc7d` left
  them.
- It has no time constant.

Why not the others. C removes the artifact at its source and is the more principled fix of the edge
padding, but it moves `f` on 111 trials and 44 of 67 synthetic expected values, and it changes the frame
`b44dc7d` introduced for more than the shoulder; it is a reasonable second step if the `area_ratio`
release sensitivity is taken up, since that lives in the same edge. A drops real first troughs when the
first-pass half period is wrong (4 trials lose a full cycle or more; largest PT change 0.37). D is not a
shoulder fix: it deletes real small oscillations on 97 trials and takes the worst PT change to 1.16.
E removes the step in principle but redefines N, breaks comparison with every stored value and with the
literature anchor, and does no better than B on the corpus.

Before B is implemented: decide whether a peak before the first trough should also be dropped when no
trough is ever counted (B as emulated keeps it, which leaves the heavily damped rebounds alone); look at
the two trials where B changes N upward through the window anchor; and add a regression test that scores
one synthetic at release shifts of -30 to +30 ms and requires a single N.

What B does not fix, measured above: the last half-cycle sitting on the amplitude threshold (6 of 67
synthetic scenarios still change N within 10 ms; 4 of 162 corpus trials), and the `area_ratio` dependence
on the release (22 of 162 trials move by 0.005 or more in PT for a 10 ms shift).

## Not verified

- Rust and C# were compared with the Python by reading. Neither port was run on shifted releases, and no
  option was implemented in either.
- The phone corpus numbers come from the cached replay curves of 2026-11-10, not a fresh replay. The code
  that produces them is unchanged since, and re-scoring reproduces the cache, but the replay itself was not
  rerun.
- `derive_healthy_ref.py` itself was not run. Its trial selection was reproduced in a separate driver; the
  agreement of `f` and `area_ratio` with `HEALTHY_REF` to four places is the evidence that the population
  matches.
- Optical release shifts are in whole 8.3 ms samples, not exact 10 ms steps.
- The ICC comparison uses option B as "shoulder excluded". B also drops the non-shoulder early peaks where
  a trough follows them, so it is not a pure shoulder-only exclusion.
- The explanation of why `area_ratio` moves with the release is read from the code and from the per-term
  table; it was not isolated by experiment.
- The closed-form prominence `(1 + r)^2 * A0 / (8 * pi^2)` is a small-angle estimate without smoothing; the
  measured table is the evidence, the formula is the explanation.
- No option was checked on the rig recordings (encoder or phone) from the bench sessions.
- The zone thresholds are recorded in the source as stale for the current score formula; zone counts here
  are counts of crossings of those stale edges.
- The Rust test suite (`cargo test`), the webapp tests and the C# parity test under options A, C, D and E
  were not run. The Python test run covered the nine files and one directory named in section 5, not the
  whole suite.
- The 0.116 quoted for the shipped dry run is from the band-edge report and the pinned test, not from a
  rerun here; the 9 of 9 under B and C is from a rerun here (seed 3 only).
