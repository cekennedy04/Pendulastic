# Phone IMU against OptiTrack on the human recordings (2026-11-18)

Dated note. Aggregate results only; the per-trial tables are local
(`Model_Analysis_Outputs/optitrack_loader_compare.csv`,
`Model_Analysis_Outputs/imu_optitrack_validation.csv`, git-ignored).
Nothing here is a clinical claim about the phone, and no MAS matching is made.

Code: `optitrack_loader_compare.py` (Phase 1), `imu_optitrack_validation.py`
(Phases 2 and 3). Both read the recordings in place and write nothing to the repo.

## What was compared

159 paired trials from 13 participants (8 MS, 4 stroke, 1 control; 39 legs) have both a
phone recording and an OptiTrack take. Trials in `excluded_trials.json` were already left
out by `discover_trials()`; nothing else was excluded. The phone is replayed from raw
samples on the reference footing (`REFERENCE_REPLAY_CONFIG`, after the seed fix).

Three optical curves per trial, all from the same markers:

- **Shank rotation**: rotation of the shank plate in the room from its first pose, about
  the plate's own swing axis. This is the quantity a single phone on the shank measures.
  Owner's choice for the tracking reference.
- **Refit knee angle**: the current loader's construction (shank line against thigh
  line) about that same robust swing axis. Owner's choice for the knee angle. Analysis
  code in this PR, not a loader change.
- (Phase 1 only) the **current loader** and the **August loader** (`6ddf649`).

Two phone curves per trial:

- **App angle**: what the app reports (rotation projected on the flexion axis it captures
  at the start of the swing, smoothed, magnitude only).
- **Sensor rotation**: the same filter output read about the axis the phone actually
  turned about over the trial, unsmoothed. This separates "does the sensor and filter
  track the shank" from "does the app's angle pipeline".

## Headline

1. **The sensor tracks the shank.** Sensor rotation against shank rotation: RMSE median
   3.9 deg (p90 6.8), bias -0.4 deg, 95 % limits of agreement -9.9 to +9.1 deg, gain
   median 1.00 (p10 0.95, p90 1.04), correlation median 0.99 (132 trials where both
   instruments saw the hold; 145 aligned).
2. **The app's angle loses amplitude.** App angle against shank rotation: gain median
   0.85 (p10 0.62), RMSE median 8.7 deg (p90 22), bias -8.1 deg, limits -27 to +11 deg,
   correlation median 0.99. The loss follows the error of the captured flexion axis
   (median 21 deg off the axis the phone really turned about; over 30 deg on 34 trials,
   where gain is 0.72 and RMSE median 17 deg). The app curve also trails by a median
   70 ms (smoothing). This is an app-pipeline defect, not a sensor one.
3. **The PT score does not yet match.** IMU against shank rotation: score ICC 0.66 per
   trial and 0.79 per leg; the three-zone verdict agrees on 91 of 129 trials (kappa
   0.49). With the sensor rotation instead of the app angle it is 105 of 137 (kappa
   0.60), ICC 0.67 / 0.81. For scale, the two optical curves of the same take agree on
   121 of 140 (kappa 0.78, ICC 0.83). Cycle count and frequency agree well; A0, R2n and
   area ratio do not.
4. **The current OptiTrack loader is wrong on most trials** (hinge axis captured by
   re-labelled frames). The refit fixes it; proposed as a separate PR.
5. **Lateral motion is measurable by both instruments and they agree moderately**, it
   does not relate to the PT score, and the stored Euler roll is unusable. Recommendation:
   do not add it to the score now; keep it as a reported quality signal.

## Phase 1: the two OptiTrack loaders

Of 159 trials:

- **Current loader** (`optitrack_knee_axis.hinge_axis`): the hinge axis is more than
  30 deg from the plate's swing axis on 130 trials (median 73 deg). The cause is that
  the axis is the principal direction of summed squared frame-to-frame plate rotations,
  with no defence against Motive re-labelling a near-isosceles plate: a re-labelled
  frame reads as a turn of about 180 deg, and a handful of them outweigh the whole swing.
  Steps no shank can make (over 10 deg in one frame) are present on 152 trials and carry
  essentially all of the weight. Consequences, against the August loader on the 145
  trials both read: polarity is mirrored on 66, and amplitude gain is median 0.61 (range
  0.01 to 1.56; 31 within 0.9 to 1.1). This is the arbitrary polarity and per-trial gain
  reported in the earlier root-cause note.
- **August loader** (`6ddf649`): fails on 14 trials that have no hold in the first 60
  frames, and reads an unsigned 3-D angle (folds at 180).
- **Refit** against August: gain median 1.07, RMS difference 2.1 deg after sign and
  offset.

Twelve trials differ by more than 5 deg between refit and August. Each was examined
curve by curve against the optics-only yardstick (shank plate rotation minus thigh bar
rotation at rest):

| Finding | Trials |
|---|---|
| August under-reads; refit matches the yardstick within about 2 deg (gain 1.16 to 1.40) | 8 |
| Same pattern, but optical coverage 44 % and no settled tail, so not decidable | 1 |
| August folds at 180 on takes that start before the lift (refit shows extension first) | 2 |
| Refit wrong (reads far above the yardstick; coverage 63 %) | 1 |

So the refit is right or the better of the two on 10 of 12, undecidable on 1 and wrong
on 1. The one refit failure is not caught by the step flag in this code
(`refit_jumps` flags no trial); low marker coverage is the common factor.

The thigh bar moves a median 14 deg (p10 9, p90 19) over the swing, so the knee angle
and the shank rotation are different quantities by that much.

## Phase 2: tracking

Method. One time lag per trial, found as the lag of smallest residual at unit gain
between shank rotation and sensor rotation, searched around `compare_pair`'s lag and
the release-aligned lag (135 trials stayed at `compare_pair`'s, 3 at the release lag, 7
elsewhere). Correlation alone was not used for this: a damped swing one period late
has the same shape at a different size and correlates almost as well. The optical
curve's sign is set to the phone's (both are conventions). Optical gaps over 50 ms are
left out, not bridged. Offsets (RMSE, bias, limits of agreement) are reported only
where both instruments saw a still hold in the half second before release (132 of 145
aligned trials); shape figures (correlation, gain, offset-removed RMSE) are over all
145. Limits of agreement are repeated-measures (between-trial variance of the bias
plus mean within-trial variance). Every figure is agreement after best time alignment,
not proof of synchrony.

Of the 159 trials, 13 have no usable phone curve (9 never zeroed, 4 unreadable
component files) and 1 has recordings that do not overlap over the swing.

| IMU curve against shank rotation | Trials (hold seen) | RMSE median / p90 | Bias | 95 % LoA | Gain median (p10, p90) | r median | Offset-removed RMSE median / p90 |
|---|---|---|---|---|---|---|---|
| Sensor rotation, pooled | 145 (132) | 3.9 / 6.8 | -0.4 | -9.9, +9.1 | 1.00 (0.95, 1.04) | 0.99 | 2.6 / 4.1 |
| MS (8 participants) | 109 (96) | 4.1 / 7.0 | -0.8 | -10.7, +9.0 | 1.00 (0.92, 1.03) | 0.99 | 2.4 / 4.2 |
| Stroke (4) | 28 (28) | 3.1 / 5.2 | +0.5 | -6.6, +7.6 | 1.01 (0.97, 1.04) | 0.99 | 2.6 / 3.3 |
| Control (1) | 8 (8) | 4.9 / 7.6 | +1.5 | -9.9, +12.8 | 1.02 (0.99, 1.06) | 0.98 | 3.9 / 4.4 |
| App angle, pooled | 145 (132) | 8.7 / 22.3 | -8.1 | -27.2, +11.0 | 0.85 (0.62, 0.94) | 0.99 | 3.2 / 7.9 |
| MS | 109 (96) | 9.5 / 23.6 | -9.4 | -29.6, +10.8 | 0.84 (0.60, 0.94) | 0.99 | 3.5 / 8.4 |
| Stroke | 28 (28) | 5.7 / 10.4 | -5.4 | -17.0, +6.2 | 0.86 (0.77, 0.93) | 0.99 | 2.6 / 4.1 |
| Control | 8 (8) | 6.9 / 12.9 | -1.8 | -20.5, +17.0 | 0.85 (0.61, 0.93) | 0.99 | 3.6 / 10.2 |

All in degrees. Group rows are descriptive: one control participant, four stroke.

App angle by how far its captured flexion axis is from the axis the phone turned about:

| Axis error | Trials | Gain median | RMSE median | Bias |
|---|---|---|---|---|
| under 15 deg | 36 | 0.90 | 6.4 | -2.6 |
| 15 to 30 deg | 75 | 0.86 | 7.5 | -6.0 |
| over 30 deg | 34 | 0.72 | 17.1 | -17.2 |

App angle against the refit knee angle: RMSE median 6.9 deg (p90 15.0), bias +2.4 deg,
limits -19.6 to +24.3, gain median 0.93 (p10 0.62, p90 1.11). The smaller bias is two
errors partly cancelling: the app under-reads the shank, and the knee angle is smaller
than the shank rotation because the thigh moves.

The poor tail. Five aligned trials have sensor-against-shank gain off by more than
0.15, correlation under 0.9 or offset-removed RMSE over 8 deg. All five have low
optical coverage (15 to 68 % of frames), four of them without the hold seen, and one
still shows the plate reading a half turn. They look like optical failures, and they
are why the pooled (sample-weighted) correlation over all 145 trials is 0.83 while
the median is 0.99. They are kept in every figure above; none was excluded. Whether to
exclude low-coverage takes is the owner's call.

## Phase 3a: PT score

Each curve is scored by `pendulastic_pt_score` unchanged. Agreement is over trials
interpretable by both sources; leg level is the median over a leg's trials (37 legs).
ICC is two-way, absolute agreement.

IMU (app angle) against each optical reference:

| | vs shank, trial (n=129) | vs shank, leg | vs refit, trial (n=120) | vs refit, leg |
|---|---|---|---|---|
| PT score ICC | 0.66 (0.58 to 0.76) | 0.79 | 0.58 (0.46 to 0.70) | 0.81 |
| PT score bias, LoA | -0.05, -0.37 to +0.28 | -0.04, -0.30 to +0.22 | -0.05, -0.42 to +0.32 | -0.04, -0.35 to +0.27 |
| N (cycles) ICC | 0.92 | 0.93 | 0.77 | 0.96 |
| phi_max ratio ICC | 0.81 | 0.85 | 0.70 | 0.82 |
| A0 ICC; bias (deg) | 0.37; -4.3 | 0.48; -4.3 | 0.42; +6.3 | 0.53; +6.1 |
| R2n ICC | 0.44 | 0.51 | 0.19 | 0.34 |
| f ICC | 0.35 | 0.39 | 0.19 | 0.40 |
| area ratio ICC; bias | 0.15; -0.10 | 0.18; -0.10 | 0.13; -0.11 | 0.37; -0.11 |
| Verdict agreement | 91 of 129, kappa 0.49 | | 79 of 120, kappa 0.42 | |

Where the verdicts differ against shank rotation, the IMU is the milder reading on 29
of 38 (borderline to healthy 14, impaired to healthy 9, impaired to borderline 6).

With the sensor rotation in place of the app angle, against shank: score ICC 0.67
trial / 0.81 leg, verdict 105 of 137 (kappa 0.60), N 0.97, f 0.92 (0.99 per leg),
phi_max ratio 0.89, A0 0.53 (bias +2.3 deg), R2n 0.47, area ratio 0.14 (bias -0.12).
So fixing the axis recovers frequency and some verdicts, but A0, R2n and area ratio
still disagree although the curves agree to a few degrees. The area ratio is
systematically lower on the phone (median 0.05 against 0.14). The cause is not
established here; it is not tracking error. Candidates to check next are how each
curve starts (the phone curve starts at its zero pose, the optical curve wherever the
take starts), release detection, and sample rate. Until that is understood, the "same
PT score" half of the validation goal is **not met**.

Optics against optics (refit against shank, same markers, n=140): score ICC 0.83,
verdict 121 of 140 (kappa 0.78), A0 bias -11.6 deg. That is the ceiling a phone on the
shank could reach against a knee-angle reference with this scorer.

Thigh movement at release. Over the 119 trials all three sources score, thigh bar
movement explains 46 % of the variance of the refit-minus-shank A0 difference
(r = -0.68; median difference -12 deg) and almost none of the score difference
(r² 0.03). It explains little of the IMU's differences: r² 0.07 for A0 against shank,
0.05 for the score against refit, under 0.03 elsewhere. So thigh movement is the main
reason knee angle and shank rotation differ in amplitude, and it is not the reason the
phone's score differs.

Trials that cannot be scored (not interpretable), by source and scorer reason:

| Source | Count | Reasons |
|---|---|---|
| IMU (of 159) | 26 | never zeroed 9, no oscillation 8, unreadable component files 4, excursion too small 4, no scorable swing 1 |
| Refit knee angle | 18 | no scorable swing 7, no oscillation 5, excursion too small 5, excursion beyond joint range 1 |
| Shank rotation | 6 | no oscillation 2, no scorable swing 2, excursion too small 1, beyond joint range 1 |

119 trials are interpretable by all three; 2 by none.

## Phase 3b: lateral (out-of-plane) motion

Definition: the tilt of the swing axis carried by the segment's rotation, relative to
the pose at release (how far the swing leaves its own plane), from the robust swing
axis on the optical side and from the replayed orientation on the phone. Also the part
of it the phone observes without heading (change in the axis's elevation against
gravity), and the Euler roll recomputed from raw with the fixed filter.

- Size: optical tilt peak median 15.5 deg (IQR 12.3 to 19.1), RMS 6.0 deg; phone tilt
  peak 14.2 deg, RMS 5.2 deg; elevation RMS 4.1 deg.
- Phone against optical, per trial (n=146): Spearman 0.65 for tilt RMS, 0.64 for
  elevation RMS, 0.60 for peaks; phone minus optical tilt RMS -1.2 deg, limits -9.5 to
  +7.0 deg. Moderate agreement in ranking, wide in absolute terms relative to a 6 deg
  quantity.
- Euler roll: peaks median 72 deg, no relation to the optical measure (rho 0.01 and
  -0.03). It is a gimbal artefact of the Euler decomposition near this pose. The stored
  `hip_roll_deg` should not be used as lateral motion, with either filter.
- By group (participant medians): stroke lower than MS on all three measures (optical
  RMS 4.9 against 6.3 deg; Kruskal p 0.03 optical, 0.06 phone tilt, 0.04 phone
  elevation). Four against eight participants and one control: descriptive only.
- Against the PT score, per leg (n=37 to 39): no relation (absolute rho 0.17 or less,
  p over 0.3), for optical or phone, against either score.

Recommendation: do not make lateral motion a PT-score parameter now. It carries no
information about the score in this data, the phone measures it only moderately, and
the group difference rests on too few participants. It is worth keeping as a reported
trial-quality signal (a swing far out of plane is also where the app's flexion-axis
capture is most at risk), computed from orientation, not from Euler roll. The scorer
was not changed.

## Caveats

- 13 participants, one control. Group comparisons are descriptive.
- Time alignment, sign and (for the app angle) a small delay are fitted per trial, so
  agreement is after best alignment.
- The optical reference is itself imperfect: low-coverage takes fail, and the plate's
  axis and zero are estimated from the same data.
- Sensor rotation uses an axis fitted over the whole trial, which the live app cannot
  do as is. It shows what the sensor and filter deliver, not what the app shows today.
- All phone figures are after the seed fix (algorithm 0.2.0); earlier IMU numbers are
  stale.

## Proposed follow-ups (not done here)

1. Loader fix PR: make `optitrack_knee_axis.hinge_axis` robust to re-labelled frames
   (label-proof plate frame, as `optitrack_loader_compare.plate_frames`), and decide
   which reference downstream reports use. Owner decides.
2. App flexion-axis capture: the largest single source of app error found here. Any
   change moves every app angle and PT score, so it needs the scorer-change procedure.
3. Find why A0, R2n and area ratio differ between curves that agree to a few degrees.
4. Owner decision: whether low-coverage OptiTrack takes are excluded from validation.
5. Wire the phone's lateral trace into `plot_multisource_trials.discover_multisource_trials`
   (the optical trace and the figure row are in this PR).
