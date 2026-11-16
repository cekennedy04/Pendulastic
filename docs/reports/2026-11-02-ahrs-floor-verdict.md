# AHRS correction floor: zero effect on 144 paired corpus trials

**Measured 2026-11-02; recorded here 2026-11-16.** Source: PR #88
(`fix(rmse): pair the --compare-floor passes by trial; record the floor verdict`,
merged 2026-11-02 as `aecd288`). Until now the result existed only in the PR
description and in agent memory.

## Result

`batch_imu_vs_optitrack_rmse.py --compare-floor` replays each trial with and without the
correction floor added in `791b0ae`. Of 159 matched corpus trials, 144 evaluated under both
settings. With the pairing fixed in #88 (the first run paired only 56 of the 144, because
`_report_comparison` keyed trials on participant + position + trial number and a participant's
two legs share that key):

- mean delta **+0.0000 degrees**, worst delta **+0.0000 degrees**, unchanged on **144 of 144**;
- not a plumbing fault: on a sampled trial floors of 0, 2e-3 and 1.0 give a bit-identical
  replayed angle, while a floor of 100 moves it by 1.4 degrees, so the floor does reach
  `MadgwickAHRS.update`;
- on real recordings the filter's correction step never falls below 1, so a 2e-3 floor never
  binds. The floor buys synthetic-fixture reproducibility at no accuracy cost on real data.
  Verdict then: keep it.

## Limits of this result

- It was measured **before the 2026-11-16 AHRS gravity-seed fix (#98)**, with the mirrored seed.
  The floor and the seed are different mechanisms and the seed fix was confirmed separately
  (`docs/reports/` seed-fix corpus check and Rust confirmation), but this number has not been
  re-run on the fixed seed.
- The RMSE gate compares only the active swing window, so it could not see the seed bug. Do
  not treat "unchanged on 144 of 144" as a sufficient gate for filter changes.
- Absolute IMU-versus-OptiTrack RMSE was about 141 degrees on `main` at the time because the
  marker reference had no zero after `87d7645`; #81 fixes the headline metric. The floor verdict
  does not depend on it, since the replayed angle traces are identical.
