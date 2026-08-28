# The IMU does not over-read. It measures the wrong quantity.

**2026-08-28** — investigation into the ~1.55x IMU-vs-OptiTrack amplitude gap.
Corpus: 93 IMU/OptiTrack paired trials, 12 participants, scored on branch
`fix/optitrack-trial-quality`.

## The headline

The standing belief, recorded after the 2026-08-25 measurement, was that the
IMU angle pipeline was defective and unusable for validation. **The pipeline is
not the problem.** It contributes 9% of the gap. The remaining error is that a
single phone measures one segment's rotation *in space*, and the pipeline
reports that as the knee angle — which is only the same thing while the other
segment holds still. The thigh does not hold still.

**Do not apply a gain correction.** See "What was NOT done", below.

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
   fixed (commit `6a327d5`, flex-axis estimator). What remains is a geometry
   and protocol issue, not a processing one.
4. The 9% pipeline-over-gyro residual is the only IMU-side item left that is
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
