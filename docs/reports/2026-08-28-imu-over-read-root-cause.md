# The 1.55x IMU over-read is two defects, not one

**2026-08-28** — investigation into the IMU-vs-OptiTrack amplitude gap.
Corpus: 93-100 IMU/OptiTrack paired trials (the count grew mid-investigation as
P17 was recovered), 12-13 participants, branch `fix/optitrack-trial-quality`.

One is a drift-correction bug in the scoring code and is now fixed. The other
is that a single sensor cannot measure a joint angle, and no analysis change
fixes that.

## The headline

The standing belief, recorded after the 2026-08-25 measurement, was that the
IMU angle pipeline was defective and unusable for validation. That was one
claim covering **two separate causes**, and only one of them is in the angle at
all:

1. **A drift-correction defect (FIXED).** The IMU curve sinks ~0.8 deg/s
   through the trial, which drags down the tail median that A0 is measured
   against. The existing correction fit its slope from the pre-release hold —
   the one window where gyro bias had just been calibrated, so it was flat by
   construction and the correction removed nothing. Fixing it took the corpus
   from 54.9% to **36.0%** median error. See the update section below.
2. **Single-sensor geometry (NOT fixed, and not fixable in analysis).** One
   phone measures a segment's rotation *in space*; the pipeline reports that as
   the knee angle, which holds only while the other segment is still. The thigh
   sweeps a median 16.7 deg. This needs two-sensor capture.

**Do not apply a gain correction for (2).** See "What was NOT done", below.

## Decomposition

| ratio | value | reading |
|---|---|---|
| pipeline / gyro-integral | **1.09** | everything the pipeline itself adds |
| gyro-integral / OptiTrack | **1.41** | present before fusion |
| pipeline / OptiTrack | **1.55** | 1.09 x 1.41, consistent |

The pipeline adds 9%. That is the whole of its contribution.

## The IMU measures its own segment correctly

Two independent measures of the same rotation, over the same window, on the 89
trials with a usable static hold and settle:

| measure | how | vs OptiTrack |
|---|---|---|
| gravity-direction change | no integration, no axis assumption | 1.255 |
| integrated gyro | integrates rate about the principal axis | 1.294 |

**accel / gyro = 0.976.** These two fail in unrelated ways — one cannot drift
because it never integrates; one cannot suffer projection error because it uses
no axis. They agree to 3% and both sit ~25% above OptiTrack. They are not
independently wrong in the same direction.

A further constraint pins the direction: projecting a gyro onto any unit axis
can only *reduce* the recovered angle (`|w . u| <= |w|`). So a wrong flex axis
makes the gyro integral an under-estimate. It cannot explain a number that is
already too high. Confirmed by test, not asserted:
`test_gyro_projection_under_reads_on_a_wrong_axis_never_over_reads`.

## Root cause: every trial is single-sensor

**93/93 trials carry no `ROLE_DISTAL` sample.** The lone phone is labelled
`proximal` because `ROLE_PROXIMAL` goes to whichever phone connects first,
regardless of where it is mounted. On that path the pipeline takes the sensor's
absolute rotation as the knee angle.

Measured from the same labeled-marker plates the reference angle is built from:

| plate | median sweep |
|---|---|
| thigh | **16.7 deg** (89/93 trials above 10 deg) |
| shank | **70.8 deg** |

`70.8 - 16.7 = 54.1`, an absolute-to-relative ratio of **1.31**, against a
measured 1.255-1.294. The magnitude matches. Thigh motion is being counted as
knee flexion.

## What did NOT hold up

Reported because a negative result that took work is worth the same as a
positive one, and because someone will otherwise re-derive it.

**OptiTrack marker dropout does not explain the gap.** The obvious theory —
markers are lost at release, so the reference misses the peak and reads low —
predicts that the ratio rises with dropout. It falls:

| OptiTrack dropout | n | median ratio |
|---|---|---|
| 0-5% | 10 | 1.635 |
| 5-20% | 8 | 1.556 |
| >20% | 75 | 1.525 |

The correlation is weak and the wrong way round for the theory to survive
(Pearson r = +0.236 overall, but the binned medians decline). Dropped as an
explanation.

**Thigh motion explains the size of the gap but not its trial-to-trial
variation.** Per trial, predicted ratio `shank / (shank - thigh)` against the
measured ratio: **Spearman rho = -0.037, p = 0.73, n = 87.** No relationship.
Kabsch sweep angles are magnitudes of 3-D rotations, so subtracting them is
only valid when both segments move in a common plane, which is not guaranteed
per trial. The mechanism is established at the right magnitude; it is not
established as a per-trial predictor.

## UPDATE, same day: a second cause found and fixed

The above stands, but it was not the whole story, and the part that was
missing turned out to be fixable in code.

**A0 is hostage to the tail.** `A0 = (angle at release) - (median of the last
25% of the trial)`. A sensor whose curve sinks through the trial drags that
median down and inflates A0 with no error in the swing at all.

**The IMU curve never settles.** Measured over 93 trials:

| tail slope | value |
|---|---|
| IMU | **-0.735 deg/s** (sinking) |
| OptiTrack | **+0.009 deg/s** (settled) |

Tail slope predicts the IMU/OptiTrack A0 ratio at **Spearman rho = -0.641,
p = 8e-12** — a real per-trial relationship, unlike the thigh model.

**The drift correction was fitting the one window that cannot show the
problem.** It fit the slope from the pre-release baseline and extrapolated.
But `pendulastic_imu_server.zero()` recalibrates gyro bias from that exact hold
window, so the baseline is flat by construction:

| slope | value |
|---|---|
| pre-release baseline (what was fitted) | +0.193 deg/s |
| settled tail (what actually drifts) | -0.833 deg/s |

`|tail| > |baseline|` in **84%** of trials. The correction removed nothing.

**Fixed** by estimating drift from the settled tail instead — the other region
that is physically at rest, where any slope belongs to the sensor. The swing
still never enters the fit, which was the original and valid reason for not
using a whole-trial least-squares detrend.

Corpus effect, 100 paired trials:

| | median err | ratio IQR | median ratio | beyond 2x |
|---|---|---|---|---|
| before | 54.9% | 1.32-1.76 | 1.536 | 13/100 |
| after | **36.0%** | **1.17-1.71** | **1.356** | **11/100** |

**Two guards this needed, one of which a first attempt got wrong.** A decaying
oscillation is linear over less than one period, so a short tail fits a steep
slope with a tiny residual and looks exactly like drift. On a 0.32 Hz synthetic
whose tail covered 0.62 of a period, the fit returned -4.13 deg/s with a
residual of 2.17 deg, and applying it ate 29 degrees of real swing (A0 45.6 ->
16.8). A fixed 3 s minimum fixed that case but rejected most real tails and gave
back most of the benefit. The requirement is PERIODS, not seconds, measured from
each trial's own dominant frequency.

The guard is deliberately conservative: **32%** of IMU trials are corrected
(median applied slope -0.528 deg/s) and the rest fall back to the previous
behaviour, so no trial is made worse. On OptiTrack it is a no-op by
construction (+0.009 deg/s).

**This also explains why the thigh model failed per-trial.** Drift was the
larger, uncorrelated term swamping it. With drift removed the geometry
prediction lands almost exactly on the measured value — predicted 1.265 vs
measured 1.309, residual **1.035** — while per-trial correlation stays null
(rho = -0.007). The geometry term is real and now correctly sized; it is still
not a per-trial model, which is why it is still not applied as a correction.

## What was NOT done, deliberately

**No gain correction was applied.** A 1/1.55 (or 1/1.31) factor would make the
aggregate numbers look right and would be calibration against a model that
fails per-trial (rho = -0.037). It would bake a fixed error into every future
trial and hide the real defect, which is a capture-protocol problem. The Iron
Law applies: the root cause is identified, but the per-trial mechanism is not
confirmed, so no correction is warranted yet.

## What the corpus cannot answer

Three gaps, all capture-side, none fixable in analysis:

- **0/93** OptiTrack files contain Thigh/Shank rigid bodies, so the strict
  rotation-quaternion reference never runs; every trial falls back to the
  labeled-marker Kabsch path.
- **0/93** trials have a second IMU, so there is no direct measurement of thigh
  motion from the IMU side to difference against.
- **75/93** trials exceed 20% optical dropout.

## What to do

1. **Two-sensor capture** is the fix. One phone on the thigh, one on the shank,
   so the knee angle is a difference of two measured rotations rather than one
   measured rotation and one assumption. Nothing in analysis substitutes for it.
2. **Treat single-sensor IMU amplitude as absolute segment rotation**, not knee
   flexion, wherever it is reported. It runs roughly 1.25-1.31x the knee angle.
3. **Retire "the IMU pipeline is incoherent."** The 26x scatter was real and is
   fixed (commit `6a327d5`, flex-axis estimator); the drift-correction defect
   is fixed too. What remains is a geometry and protocol issue.
4. **Widen the drift correction's coverage.** It currently applies to 32% of
   IMU trials; the rest fail the settled-tail test and fall back. Trials that
   end before the leg settles cannot be rescued in analysis, but a longer tail
   window might admit more of them safely. Worth measuring before assuming.
5. The 9% pipeline-over-gyro residual is the only IMU-side item left that is
   both real and fixable in code. It has not been chased.

## Reproducing

`imu_absolute_vs_knee.py` holds the measurement primitives, each tested against
analytically known rotations in `tests/test_imu_absolute_vs_knee.py` (14 tests)
— a diagnostic that cannot recover a known answer cannot characterise an
unknown one.

One trap worth naming: **the accelerometer feed is in g, not m/s^2** (median
`|a|` = 1.0027). A static-window gate written against 9.81 silently rejects
every trial in the corpus. Pinned by
`test_accel_in_g_units_is_accepted`.
