# OptiTrack loader: hinge axis fix, quality flags, sensitivity (2026-11-18)

Dated note. Aggregates only; per-trial tables are local and git-ignored. Follows
`2026-11-18-imu-vs-optitrack-validation.md`, which found the loader's hinge axis wrong
on 130 of 159 paired trials.

## What changed

- `optitrack_knee_axis.hinge_axis` now takes its axis from the plate's rotation away
  from its first tracked pose, read off a plate frame that does not depend on marker
  labels (`plate_frames`, `swing_axis`). This is the "refit" of the validation report,
  moved from analysis code into the loader. The sign pin, the angle construction
  (`segment_axis_from_plate`, `signed_knee_angle`) and the relative, uncalibrated
  output are unchanged.
- Two quality flags on `KneeAngleResult`, surfaced as loader warnings. Flags only:
  nothing is refused or excluded.
  - `low_marker_coverage`: both clusters fully tracked on under 60 % of frames.
  - `knee_curve_disagrees_with_plate`: correlation between the knee curve and the
    shank plate's own rotation about the hinge under 0.95.
- `conditioning` changed scale (see below).
- `imu_optitrack_validation.py` carries the loader's flags and reports the headline
  figures again without flagged takes.

## Before and after, 159 paired trials

"Before" is the pre-fix axis estimator, kept in `optitrack_loader_compare.increment_axis`
so the comparison stays reproducible. The August loader (`6ddf649`) reads 145 of the
159.

| | Before | After |
|---|---|---|
| Hinge axis more than 30 deg from the plate's swing axis | 130 | 0 (it is that axis) |
| Mirrored relative to the August loader (of 145) | 55 | 3 |
| Amplitude gain against August, median (p10, p90) | 0.61 (0.22, 1.04) | 1.07 (1.01, 1.18) |
| ... within 0.9 to 1.1 (of 145) | 31 | 94 |
| Gain against the shank plate's own rotation, median (p10, p90) | 0.49 (0.13, 0.80) | 0.85 (0.73, 0.92) |
| Correlation with the plate's rotation, median (p10) | 0.96 (0.59) | 1.00 (0.99) |
| Against the phone: offset-removed RMSE, median (p90) | 14.5 (26.5) deg | 5.0 (15.1) deg |
| Against the phone: polarity as given / mirrored / undetermined | 77 / 44 / 25 | 123 / 2 / 21 |

The mirrored count before is 55 here and 66 in the validation report: the pre-fix
loader's sign pin is not reproduced by `increment_axis`, so the two differ on trials
where the pin flipped the sign. Everything else matches the earlier run.

The gain against the plate is below one after the fix because the thigh moves (median
14 deg): knee angle and shank rotation are different quantities. The gain above one
against August is the August loader under-reading (validation report, Phase 1).

All 159 trials still load. The existing OptiTrack tests pass; two of them were
re-pitched for the conditioning scale (below).

## Flags, and what coverage can and cannot tell

The owner asked for a coverage-based flag because the one take where the refit was
found wrong by hand was not caught by `refit_jumps`. Marker coverage turns out to be
a weak detector on this corpus, and the report should say so plainly:

- Both clusters are fully tracked on a median 72 % of frames (tenth percentile 60 %).
  Dropped frames are the norm, mostly as short flicker (median longest gap in the
  swing 0.08 s).
- The takes that are actually wrong sit in the middle of that distribution. The five
  poorly agreeing takes of the validation report have swing coverage 64 to 80 %; the
  refit failure has 59 %. No coverage threshold separates them from the 100 or so
  ordinary takes with the same coverage. (The earlier statement that those five "all
  have low coverage" was true and uninformative.)
- What does separate them is whether the knee curve follows the plate it was computed
  from. Measured over the swing in the comparison script, correlation under 0.95 marks
  12 takes, including the refit failure and three of the five. The loader's own flag
  computes it over the whole take and marks 6.

Counts on the 159: `low_marker_coverage` 21, `knee_curve_disagrees_with_plate` 6,
either 26.

## Sensitivity: headline figures without flagged takes

All takes stay in every main figure. These rows only show how much the flagged ones
move it. Sensor rotation and app angle are against shank rotation, as in the
validation report; RMSE, bias and limits over takes where both instruments saw the
hold.

| | All | Without low coverage | Without plate disagreement | Without either |
|---|---|---|---|---|
| Paired trials | 159 | 138 | 153 | 133 |
| Sensor: RMSE median / p90 (deg) | 3.9 / 6.8 | 4.0 / 6.8 | 3.8 / 6.8 | 3.9 / 6.7 |
| Sensor: bias; 95 % LoA (deg) | -0.4; -9.9, +9.1 | -0.5; -9.7, +8.8 | -0.3; -9.7, +9.1 | -0.3; -9.3, +8.8 |
| Sensor: pooled offset-removed RMSE (deg) | 14.3 | 3.0 | 3.0 | 2.9 |
| Sensor: pooled correlation | 0.83 | 0.89 | 0.90 | 0.90 |
| App angle: RMSE median; bias (deg) | 8.7; -8.1 | 8.8; -8.1 | 8.7; -8.1 | 8.8; -8.1 |
| App angle: gain median | 0.85 | 0.85 | 0.85 | 0.85 |
| PT score vs shank: ICC trial / leg | 0.66 / 0.79 | 0.63 / 0.65 | 0.68 / 0.79 | 0.64 / 0.65 |
| PT verdict vs shank | 91 of 129, kappa 0.49 | 81 of 111, 0.52 | 90 of 125, 0.51 | 80 of 107, 0.55 |
| PT score vs knee angle: ICC trial / leg | 0.58 / 0.81 | 0.45 / 0.47 | 0.58 / 0.77 | 0.45 / 0.47 |
| PT verdict vs knee angle | 79 of 120, kappa 0.42 | 70 of 102, 0.46 | 79 of 119, 0.43 | 70 of 102, 0.46 |

Reading: the medians and limits do not move. The only figures that move are the
sample-weighted pooled ones, which a single take reading a half turn dominates
(offset-removed RMSE 14.3 to 3.0 deg). Leaving flagged takes out does not rescue the
PT-score agreement; its cause is elsewhere
(`2026-11-18-pt-score-disagreement-diagnosis.md`). The leg-level ICC falls when
low-coverage takes are removed because a few legs lose most of their trials; it is
not evidence that those takes were better.

## Conditioning changed scale

`conditioning` was the hinge axis's share of summed squared frame-to-frame rotation.
On real data that measured something else: before the fix it was inflated by
re-labelled frames, which all turn about one in-plane axis (median 0.95, 45 of 159
flagged); with those removed it is dominated by per-frame marker noise (median 0.49,
158 of 159 would be flagged, for swings that are plainly hinges). It is now the
axis's share of the plate's rotation from its first pose: median 1.00, tenth
percentile 0.99, minimum 0.54 on the corpus.

Consequence: `ill_conditioned_axis` / `out_of_plane_motion` now mark only gross
departures at the unchanged threshold of 0.90 (a 9 deg RMS wobble on a 60 deg swing
reads about 0.98). The threshold was never derived and still is not. Two synthetic
tests were re-pitched to wobbles large enough to cross it on the new scale. Whether
the threshold should move is a question for the owner; out-of-plane motion is
measured directly in the validation report (lateral tilt).

## Not verified

- The flag thresholds are descriptive of this corpus, not validated detectors.
- Consumers of OptiTrack curves outside the paired set (figure pipeline, corpus
  reports) were not re-run. Every OptiTrack-derived amplitude and polarity they hold
  was computed with the pre-fix axis and is stale.
