# Onset release detector port: NOT applied, because the faithful port fails main's lift-and-hold acceptance tests and cannot match September without changes outside the detector

**2026-11-10.** Findings only. No scorer code was changed on `main`. The task was to port September's
sustained-rate onset release detector (`d2e2cee`, `35d3192`, `05b5a4e`, from
`origin/feat/rust-abs-flex-projection`) to `main`, alone, in Python, Rust and C#. The port was stopped at the
Python step, as the brief required, when the detector turned out to conflict with `f1584ae`'s tests. The
rejected Python port is recorded beside this file as `2026-11-10-release-detector-port.patch`; it is a record,
not something to apply.

Everything below was measured on that Python port, uncommitted, against `origin/main` @ `75109c5`. Rust and
C# were not ported and not run. Statements are marked *measured*, *read from source* or *inferred*.

Background: `docs/reports/2026-11-10-scorer-lineage-comparison.md` on `origin/docs/scorer-lineage-comparison`,
sections 4.2, 5, 7 and 8.

## Short version

- The onset detector is right when the pre-release hold is the curve **maximum** and wrong when it is the
  **minimum**. Its rule 3 requires the release to depart from the trial's highest angle.
- It fixes 12 phone trials where main fires early, and it fixes the rig-lift misfire on rig recordings cropped
  after the sync pulses.
- It breaks 2 phone trials, most of the optical control cohort `HEALTHY_REF` is derived from (39 interpretable
  trials become 19; three participants lose every trial), and the whole-file rig case.
- Ported alone it also shifts N by +0.5 on 40 of 139 otherwise-unchanged phone trials, through an interaction
  with main-only code (`b44dc7d`), and it hides a sensor freeze just after release from the continuity check.
- With it, 10 tests in the CI Python list fail, against 0 on unmodified main.

## 1. Scope finding (read from source)

- **Offline and live release detection are two different things.**
  - Live: `mobile-imu-core/src/calibration.rs::ReleaseDetector`, used by `session.rs` (hold, release, settle
    state machine) and `replay.rs`. It works on gyro magnitude and captures the zero pose.
  - Offline: `scoring.rs::detect_release`, the mirror of Python `_detect_release`. It works on the angle curve
    inside `compute_pt_params` when no release index is given. `session.rs::finish_with` passes `None`, so the
    phone's score does use it.
- **The three September commits changed only the offline detector.** They touch `pendulastic_pt_score.py` and
  `scoring.rs` (plus tests, goldens and an example), not `session.rs`, `calibration.rs` or `replay.rs`.
- **C# has no release detector to port.** `neuromech/designer/PtScore.cs` ports only the path where
  `release_idx` is given and `detrend=False`; its header says `_detect_release` is not ported. The designer's
  release time comes from the model, not from detection. A C# detector would be new, uncalled code the parity
  harness does not exercise. None was written.
- **The September commits carry changes outside the detector**, all left out of the port: `d2e2cee` raises the
  same-sign extremum gap to one period at 5 Hz; `35d3192` adds `_extremum_gap_samples` and the `+1e-9`
  area_ratio tail fix.

## 2. What the rejected port was

`_RELEASE_BACKOFF_S`, the 8%-of-range `_detect_release`, `_lift_hold_end` and the `_LIFT_*` constants replaced
by September's `_RELEASE_*` constants and `_detect_release`, verbatim. Nothing else in the file was touched
(`compute_pt_params`, the drift fallback, peak search, score formula, `HEALTHY_REF` and zones unchanged).

## 3. The ten test conflicts (measured)

CI Python file list on unmodified main: 618 passed, 28 skipped, 3 xfailed, 0 failed. With the port, the
scoring files (`test_pt_score`, `test_pt_report_common`, `test_pt_zone_parity`, `test_imu_calibration_tuner`,
`test_mas_validation`, `test_spasticity_grouping`, `test_rmse_compare_floor`): 384 passed, 10 failed.

| # | Failing test | Cause |
|---|---|---|
| 1 | `test_pt_score.py::test_release_found_at_drop_not_lift_when_trial_starts_at_rest` | Rule 3: the release must depart from the trial's highest angle. This `f1584ae` trial rests at 0 and holds at -55, the trial **minimum**. The detector returns the lift (1.50 s; the drop is at 4.20 s) and `compute_pt_params` returns `None`. |
| 2 | `test_pt_score.py::test_lift_hold_drop_on_imperfect_hold_jitter` | Same. |
| 3 | `test_pt_score.py::test_lift_does_not_enter_the_baseline_drift_fit` | Same (trial discarded before the fit is reached). |
| 4 | `test_pt_score.py::test_oscillation_count_is_the_same_at_20_hz_and_120_hz` | N is 6.5 at 20 Hz and 7.0 at 120 Hz. See section 6. |
| 5 | `test_imu_calibration_tuner.py::test_score_waveform_plateau_during_active_swing_fails` | A sensor freeze shortly after release reads as a twitch followed by a later release, so the plateau sits before the release and the continuity scan, which starts at the release, never sees it. September loosened one such test for this reason (`d2e2cee`); main now has five. |
| 6, 7 | `...::test_score_waveform_rejects_a_mid_swing_freeze_of_any_length[6-0.3]`, `[10-0.5]` | Same. |
| 8, 9 | `...::test_score_waveform_rejects_a_very_long_mid_swing_freeze[80]`, `[120]` | Same. |
| 10 | `test_pt_score.py::test_release_backoff_survives_quantisation_at_clinical_capture_rates` | Expected: it pins the removed back-off constant. |

The polarity conflict, on the `f1584ae` synthetic (drop at 4.200 s):

| trial | main | port |
|---|---|---|
| rest 0, hold -55 (as the test has it) | release 4.175 s, A0 58.0, N 6.0 | release 1.500 s, unscored |
| the same trial mirrored (hold on top) | 4.175 s, A0 58.0 | 4.167 s, A0 58.1, N 6.0 |
| `hold_deg=+60` (the test's other direction) | 4.175 s, A0 63.3 | 4.167 s, A0 63.4 |
| with 0.4 deg hold jitter, hold -55 | 4.175 s, A0 57.8 | 1.500 s, unscored |

*Inferred, not run:* the Rust golden `TRIAL_LIFT_HOLD` has the same -55 polarity, so the Rust test
`release_is_the_drop_not_the_lift_when_a_trial_starts_at_rest` would fail the same way.

## 4. The two "late-firing" trials are inverted trials (measured traces, inferred cause)

`4_right_june_post_T1` and `15_right_post_1week_T3`, which the lineage comparison describes as main firing
late, mid-swing (pre-release angle 95 and 114 deg). That reading is wrong.

- **Shape (measured):** flat near 180 for 2 s, a smooth 1.5 s move down to 95 / 114 deg, a 4 to 5 s still hold
  there, then an oscillation that settles back near 172 to 175.
- **Cause (inferred):** the zero pose was captured at rest, before the examiner's lift, so the curve is
  inverted and the hold is the trial minimum. This is the `f1584ae` case on the phone path.
- **Main:** its lift-and-hold rule finds the real drop (8.03 s and 5.63 s). The score is still not valid: the
  swing passes through the zero pose and is folded by the unsigned angle (alternating trough depths about
  0.55 s apart). Main reports f of 1.38 / 1.25 Hz and N of 8.5 / 10; the true frequency looks nearer 0.9 Hz.
- **September's detector:** takes the lift as the release (2.07 s and 2.00 s; A0 7.3 and 5.0).
- **Proposal, not implemented:** no offline detector can make these trials valid; the defect is where the zero
  pose is captured. Either flag and refuse a trial whose hold sits at the curve minimum, or make rule 3
  polarity-agnostic, which would also resolve the `f1584ae` conflict.

## 5. Corpus before and after: Python, `detrend=False`, 179 phone trials (measured)

Trial list from `batch_imu_vs_optitrack_rmse.discover_trials()` with the checkout's `excluded_trials.json`;
byte-identical to the lineage comparison's list. Curves replayed once (`reconstruct_trial` then `replay_trial`
at `ANALYSIS_TICK_S`); 12 replays came back empty. "Before" is `origin/main`'s scorer, "after" the port; both
scored the same cached curves with release auto-detected.

- **Trials scored:** 162 before, 162 after, none lost or gained. Under `detrend=True`: 162 and 162, one lost
  (`15_right_post_1week_T3`) and one gained (`23_left_pre_T1`).
- **Release time:** of 167 trials with a detector result, 139 agree within 0.05 s, 28 move by more than 0.05 s
  and 17 by more than 0.25 s. Median shift -0.020 s.

### The 28 changed trials

`rel` is the detected release time (s), `pre` the pre-release angle (deg). A dash means unscored on both sides.

| trial | rel before -> after | pre | A0 | N | f | PT | zone | group |
|---|---|---|---|---|---|---|---|---|
| 20_left_pre_T1 | 7.65 -> 0.62 | - | - | - | - | - | - | flat |
| 20_left_pre_T2 | 15.93 -> 0.62 | - | - | - | - | - | - | flat |
| 20_left_pre_T3 | 14.09 -> 0.62 | - | - | - | - | - | - | flat |
| 20_left_pre_T4 | 11.12 -> 0.62 | - | - | - | - | - | - | flat |
| 4_right_june_post_T1 | 8.03 -> 2.07 | 95.3 -> 179.6 | 75.4 -> 7.3 | 8.5 -> 1.0 | 1.38 -> 0.00 | 0.233 -> 0.369 | borderline -> impaired | worse |
| 15_right_post_1week_T3 | 5.63 -> 2.00 | 114.3 -> 177.3 | 60.0 -> 5.0 | 10.0 -> 1.0 | 1.25 -> 0.00 | 0.162 -> 0.369 | healthy -> impaired | worse |
| 13_right_post_T2 | 0.52 -> 7.45 | 175.1 -> 177.7 | 35.8 -> 41.7 | 0.0 -> 4.0 | 0.00 -> 0.97 | 0.654 -> 0.226 | impaired -> borderline | fixed |
| 4_left_pre_T2 | 3.32 -> 6.38 | 174.9 -> 179.3 | 60.7 -> 60.3 | 0.0 -> 4.0 | 0.00 -> 0.92 | 0.611 -> 0.367 | impaired -> impaired | fixed |
| 13_left_post_T4 | 0.52 -> 3.41 | 158.5 -> 179.1 | 27.6 -> 28.5 | 1.0 -> 2.5 | 0.00 -> 1.11 | 0.325 -> 0.284 | borderline -> borderline | fixed |
| 13_right_month_post_T1 | 0.52 -> 3.20 | 167.1 -> 178.6 | 53.5 -> 54.0 | 0.0 -> 1.0 | 0.00 -> 0.00 | 0.668 -> 1.481 | impaired -> impaired | fixed |
| 14_left_pre_T2 | 3.70 -> 6.00 | 170.0 -> 177.3 | 16.1 -> 16.6 | 0.0 -> 1.0 | 0.00 -> 0.00 | 0.600 -> 0.369 | impaired -> impaired | fixed |
| 18_left_pre_T2 | 3.53 -> 5.70 | 175.2 -> 179.5 | 73.6 -> 72.9 | 0.0 -> 7.0 | 0.00 -> 0.91 | 0.740 -> 0.198 | impaired -> borderline | fixed |
| 4_left_june_post_T3 | 1.46 -> 3.15 | 172.9 -> 179.1 | 105.3 -> 102.2 | 0.0 -> 4.5 | 0.00 -> 0.88 | 0.723 -> 0.188 | impaired -> borderline | fixed |
| 13_right_week_1_post_T5 | 1.37 -> 3.00 | 176.1 -> 178.7 | 41.0 -> 41.3 | 0.0 -> 2.5 | 0.00 -> 1.10 | 0.600 -> 0.262 | impaired -> borderline | fixed |
| 14_left_pre_T1 | 1.97 -> 3.50 | 173.7 -> 178.7 | 57.8 -> 58.3 | 1.0 -> 0.5 | 0.00 -> 0.00 | 1.530 -> 1.516 | impaired -> impaired | fixed |
| 14_left_pre_T4 | 1.71 -> 3.09 | 174.3 -> 178.8 | 64.0 -> 64.1 | 0.0 -> 0.5 | 0.00 -> 0.00 | 0.668 -> 0.770 | impaired -> impaired | fixed |
| 14_left_pre_T3 | 2.72 -> 3.51 | 176.4 -> 178.8 | 66.6 -> 66.7 | 2.0 -> 1.5 | 0.77 -> 0.00 | 0.423 -> 0.762 | impaired -> impaired | fixed |
| 13_right_week_1_post_T4 | 2.83 -> 2.89 | 178.7 -> 178.8 | 60.7 -> 60.9 | 3.0 -> 2.5 | 1.22 -> 1.20 | 0.382 -> 0.460 | impaired -> impaired | fixed |
| 17_right_post_1_week_T2 | 2.40 -> 2.28 | 179.8 -> 179.8 | 68.2 -> 71.8 | 7.5 -> 7.5 | 0.96 -> 0.96 | 0.025 -> 0.033 | healthy -> healthy | small |
| 4_left_june_post_T2 | 2.05 -> 1.93 | 179.6 -> 179.8 | 43.3 -> 45.6 | 6.0 -> 5.5 | 0.92 -> 0.91 | 0.113 -> 0.131 | healthy -> healthy | small |
| 23_left_pre_T1 | 9.36 -> 9.26 | - | - | - | - | - | - | small |
| 13_left_month_post_T1 | 2.88 -> 2.79 | 179.4 -> 179.4 | 43.2 -> 43.4 | 3.5 -> 3.5 | 0.95 -> 0.95 | 0.239 -> 0.241 | borderline -> borderline | small |
| 21_right_pre_T4 | 2.99 -> 2.92 | 179.7 -> 179.7 | 49.0 -> 49.9 | 0.0 -> 0.0 | 0.00 -> 0.00 | 1.575 -> 1.579 | impaired -> impaired | small |
| 14_right_pre_T4 | 2.09 -> 2.02 | 179.5 -> 179.5 | 103.4 -> 106.0 | 1.0 -> 0.5 | 0.00 -> 0.00 | 0.487 -> 0.506 | impaired -> impaired | small |
| 24_right_pre_T3 | 2.14 -> 2.08 | 179.0 -> 179.0 | 64.7 -> 65.0 | 6.0 -> 6.5 | 0.90 -> 0.89 | 0.179 -> 0.169 | borderline -> healthy | small |
| 23_right_pre_T2 | 2.44 -> 2.38 | 179.7 -> 179.7 | 66.8 -> 67.2 | 6.5 -> 7.0 | 0.88 -> 0.88 | 0.079 -> 0.081 | healthy -> healthy | small |
| 5_left_post_1month_T5 | 3.97 -> 3.91 | 178.4 -> 178.3 | 66.5 -> 67.7 | 7.0 -> 7.5 | 0.91 -> 0.91 | 0.123 -> 0.127 | healthy -> healthy | small |
| 14_right_pre_T3 | 4.15 -> 4.10 | 178.9 -> 178.8 | 55.9 -> 55.6 | 2.0 -> 2.5 | 1.06 -> 0.93 | 0.425 -> 0.395 | impaired -> impaired | small |

Why each group changes:

- **flat (4):** the replayed curve is 180.0 throughout. The old detector fired on floating-point noise late in
  the recording; the new one falls back to the end of the baseline window. Unscored both ways.
- **worse (2):** the two inverted trials of section 4. The release moves from the real drop to the lift, and
  the excursion gate then refuses the trial.
- **fixed (12):** the old detector fired 0.06 to 6.9 s early, during a slow upward creep in the hold (for
  example 170 rising to 179 over 3 s). The new release is at the visible drop, within 2 deg of the curve top
  (5 deg on `4_left_june_post_T3`). Seven gain an oscillation (N 0 to between 1.0 and 7.0); four change zone
  from impaired to borderline. PT rises on `13_right_month_post_T1`, `14_left_pre_T3` and `14_left_pre_T4`:
  from the traces the new release is the right one and the trials are heavily damped. *Inferred from traces;
  there is no ground-truth release time.*
- **small (10):** 0.05 to 0.12 s earlier, onto the hold. A0 rises by 0.2 to 3.6 deg.

**Trials that got worse:** the two inverted trials, and nothing else among the 28.

### The 139 trials within 0.05 s are not unchanged

| parameter | median change | median abs change | p90 abs | max abs | trials changed |
|---|---|---|---|---|---|
| A0 (deg) | +0.130 | 0.149 | 0.723 | 1.431 | 117 |
| A1 (deg) | +0.121 | 0.130 | 0.699 | 1.419 | 111 |
| N | 0 | 0 | 0.5 | 0.5 | 42 |
| f (Hz) | 0 | 0 | 0.010 | 1.014 | 45 |
| R2n | -0.0004 | 0.0004 | 0.004 | 0.019 | 111 |
| phi_max_ratio | -0.0008 | 0.0009 | 0.006 | 0.012 | 107 |
| omega_max_n | -0.009 | 0.010 | 0.056 | 0.173 | 117 |
| area_ratio | -0.005 | 0.005 | 0.013 | 0.051 | 117 |
| PT score | 0 | 0.0015 | 0.011 | 0.062 | 117 |
| pre-release angle (deg) | 0 | 0.005 | 0.125 | 1.045 | 78 |

N moves on 42 of them: +0.5 on 40, -0.5 on 2 (`21_right_pre_T3`, `13_right_week_1_post_T1`). See section 6.

### Zone changes: 9

| trial | zone before -> after | PT before -> after | why |
|---|---|---|---|
| 4_right_june_post_T1 | borderline -> impaired | 0.233 -> 0.369 | inverted trial, got worse |
| 15_right_post_1week_T3 | healthy -> impaired | 0.162 -> 0.369 | inverted trial, got worse |
| 13_right_post_T2 | impaired -> borderline | 0.654 -> 0.226 | early fire fixed |
| 13_right_week_1_post_T5 | impaired -> borderline | 0.600 -> 0.262 | early fire fixed |
| 18_left_pre_T2 | impaired -> borderline | 0.740 -> 0.198 | early fire fixed |
| 4_left_june_post_T3 | impaired -> borderline | 0.723 -> 0.188 | early fire fixed |
| 24_right_pre_T3 | borderline -> healthy | 0.179 -> 0.169 | N +0.5 only |
| 14_left_post_T4 | borderline -> healthy | 0.174 -> 0.165 | N +0.5 only |
| 14_right_post_T3 | borderline -> healthy | 0.180 -> 0.169 | N +0.5 only |

### Test-retest ICC(1,1), `detrend=False`

Grouping and bootstrap as in the lineage comparison: participant + leg + condition, groups with at least 3
repeats, trials scored both before and after; 38 groups, 158 trials, 13 participants; 2000 resamples over
participants.

| parameter | ICC before | ICC after | difference [95%] | SEM before | SEM after |
|---|---|---|---|---|---|
| A0 (deg) | 0.296 | 0.255 | -0.041 [-0.116, +0.013] | 12.0 | 13.5 |
| A1 (deg) | 0.538 | 0.603 | +0.065 [-0.145, +0.190] | 22.5 | 18.8 |
| N | 0.726 | 0.745 | +0.018 [-0.043, +0.088] | 1.32 | 1.24 |
| R2n | 0.467 | 0.172 | -0.296 [-0.499, +0.063] | 0.254 | 0.635 |
| phi_max_ratio | 0.583 | 0.621 | +0.039 [-0.057, +0.106] | 0.140 | 0.128 |
| omega_max_n | 0.317 | 0.092 | -0.225 [-0.440, +0.052] | 1.48 | 5.56 |
| omega_min_n | 0.450 | 0.153 | -0.297 [-0.381, +0.027] | 1.19 | 2.89 |
| omega peak (deg/s) | 0.671 | 0.671 | +0.000 [-0.000, +0.000] | 38.1 | 38.1 |
| f (Hz) | 0.288 | 0.406 | +0.117 [-0.017, +0.236] | 0.323 | 0.289 |
| area_ratio | 0.504 | 0.605 | +0.102 [-0.015, +0.437] | 0.148 | 0.139 |
| pre-release angle (deg) | 0.066 | 0.500 | +0.433 [-0.210, +0.619] | 8.38 | 0.54 |
| PT score | 0.637 | 0.644 | +0.006 [-0.017, +0.082] | 0.209 | 0.213 |

Every interval spans zero. The R2n and omega point estimates fall and their SEM rises; these were not traced
to specific trials (the two inverted trials, whose A0 collapses to 5 to 7 deg, are the obvious candidates,
since R2n and omega_n divide by A0; *inferred*).

## 6. The N +0.5 interaction with `b44dc7d` (measured count, cause read from source)

The onset detector deliberately leaves the release a few still samples before the leg moves (`05b5a4e`, which
puts the cost at "about 1 in 150 corpus trials"). On main it is far more than that:

- N rises by 0.5 on 40 of the 139 phone trials whose release moved by 0.05 s or less.
- The number of scored trials whose first counted peak falls within 0.2 s of the release goes from 40 to 87.
- On those 40 the extra peak sits 0.06 to 0.12 s after the release, on the hold.
- On the CI synthetic at 120 Hz: main N 6.5, port N 7.0 (first "peak" 0.075 s after release, on the hold),
  full September scorer N 6.5.

Cause: main's second peak pass searches in a swing-centred frame (`b44dc7d`, not in September). The boxcar
centre is already falling while the still hold samples are flat, so in that frame the hold rises and then
falls: a real local maximum with prominence. Three trials change zone from this alone (section 5).

So the detector, alone, does not reproduce September's N on main. Making it do so means changing the peak
search, which is outside the release detector.

## 7. `HEALTHY_REF` knock-on (measured, not applied)

`derive_healthy_ref.py` derives the reference from OptiTrack baseline control curves, scored with
`compute_pt_params(t, ang)` and auto-detected release. Those curves have arbitrary polarity: of the 56 loaded
baseline control trials, 39 hold at the curve minimum.

Run here with the script's own population and statistics, one loader, both scorers. The "before" column
reproduces the script's documented result (39 trials, 12 legs, 7 people) and the current values exactly.

| | before | after |
|---|---|---|
| interpretable trials / legs / people | 39 / 12 / 7 (P6, 7, 8, 9, 10, 12, 23) | 19 / 7 / 5 (P2, 9, 10, 12, 23) |
| R2n (trial median) | 0.9512 | 0.9520 |
| N | 6.0 | 7.0 |
| phi_max_ratio | 0.6407 | 0.6541 |
| omega_max_n | 6.4839 | 6.6389 |
| omega_min_n | -6.3624 | -6.6269 |
| f | 0.9511 | 0.8955 |
| area_ratio | 0.1766 | 0.2453 |

- P6, P7 and P8 lose every interpretable trial. These are the trials `f1584ae` rescued: on 18 of their 20
  loaded trials the release moves earlier, from 3.7 to 7.1 s back to 1.4 to 3.2 s, and most are then unscored.
- P2 enters the cohort with A0 of 66.3 and 90.5 deg on `2_right_pre_solo_T1` and `T2`, which read 20.9 and
  15.2 before.
- 37 of the 56 loaded trials change release time by more than 0.05 s or change interpretability.

The reference cannot be honestly re-derived on this port, and no zone threshold question arises until the
polarity problem is settled. Zone changes on the phone corpus are in section 5.

## 8. Rig-lift behaviour (measured with a Python stand-in)

Question from `docs/reports/2026-11-10-rig-phone-angle-vs-app.md` (`origin/eval/rig-phone-angle-vs-app`): on
rig recordings the app's offline scorer places the release 6.0 to 6.1 s early, inside the 4 s rig lift, on
A-P1p and M-tone_L4; and on the whole recording the app takes the first sync pulse as the release.

Tested with the Python reference only: the true rig motion (`build.rig_setpoint` through
`dryrun.plant_response`) put on the app's footing (`phone_trial.app_ticks`, 50 ms ticks, EMA 0.3), scored with
auto-detected release. Not the wasm and not the AHRS. Run from a scratch copy of that branch; none of it is
here.

After the sync pulses (zero pose = the hold, as that study measured the live release to be correct):

| scenario | main: release error, A0, N, PT | port: release error, A0, N, PT |
|---|---|---|
| A-P0 | +0.11 s, 51.8, 6.0, 0.123 | +0.06 s, 53.2, 6.0, 0.131 |
| A-P1 | +0.11 s, 47.4, 4.5, 0.258 | +0.06 s, 48.7, 4.5, 0.263 |
| A-P1p | **-6.04 s**, 6.2, 0.0, 0.600 (not interpretable) | +0.06 s, 45.1, 2.5, 0.399 |
| M-tone_L2 | +0.11 s, 46.8, 4.0, 0.287 | +0.06 s, 48.1, 4.0, 0.292 |
| M-tone_L4 | **-6.04 s**, 7.2, 0.0, 0.600 (not interpretable) | +0.06 s, 44.1, 0.0, 1.408 (not interpretable) |
| B-claspknife | +0.11 s, 53.2, 5.5, 0.196 | +0.06 s, 54.8, 5.5, 0.203 |
| B-clonus | +0.11 s, 43.9, 5.0, 0.227 | +0.06 s, 45.1, 5.0, 0.233 |
| pendulum | +0.11 s, 53.3, 5.5, 0.134 | +0.06 s, 54.9, 5.5, 0.141 |

- The stand-in reproduces that study's numbers on main (6.0 to 6.1 s early with A0 6 to 7.5 on the two
  scenarios; 0.108 to 0.110 s late elsewhere), so the misfire is in the reference algorithm, not only in the
  Rust.
- **The onset detector fixes the rig lift.** All 8 land +0.06 s from the planted release. A-P1p scores.
  M-tone_L4 has the right release and is still refused (N = 0), as that study's release-given footing is.
- The 2.5 s reach-limit explanation given there is consistent with these numbers; `lift_hold_end` was not
  stepped to confirm it.

Whole file with sync pulses. The zero pose is modelled at rest, as that study measured (live release on the
first pulse), so the app's unsigned angle is 180 minus the rotation from rest and the curve is inverted. *The
model of the zero pose is an assumption*; it reproduces that study's whole-file table on main.

| scenario | main | port |
|---|---|---|
| A-P1 | +0.11 s, scored, PT 0.273 | **-9.04 s**, A0 6.4, not interpretable |
| M-tone_L2 | +0.11 s, scored, PT 0.301 | **-9.04 s**, A0 7.1, not interpretable |
| A-P0 | -6.04 s, unscored | -9.04 s, unscored |
| A-P1p, M-tone_L4, B-clonus | -6.04 s, N 0, not interpretable | -9.04 s, not interpretable |
| B-claspknife, pendulum | -6.04 s, N 0, not interpretable | -9.04 s, unscored |

- **The whole-file case gets worse.** The onset detector takes the first sync pulse (a departure from the
  "top", which is rest here) on 8 of 8, and loses the two scenarios main scores. Same polarity failure as
  sections 3, 4 and 7.

## 9. Decision for the owner

The onset detector fixes real failures and introduces others of the same size; which way to go is not a
porting question. Options, none implemented:

1. **Make rule 3 polarity-agnostic** (the release departs from whichever extreme the hold is at). New
   behaviour, not September's; needs its own validation, because "the return out of the first trough starts at
   the minimum" is exactly what rule 3 exists to reject.
2. **Orient the curve before detection**, so the hold is always on top (the scorer-side counterpart of
   `fb46178`).
3. **Keep main's lift-and-hold rule as the fallback** when no run passes rule 3.
4. **Limit the onset detector to the IMU path**, where the hold is the maximum unless the zero pose was
   captured at rest.

Whichever is chosen, two things need their own decision: the N +0.5 interaction with `b44dc7d` (section 6),
and the five freeze tests (section 3), where the port lets a sensor freeze just after release pass the
continuity check.

## Not verified

- **Rust: not ported, not run.** No `cargo test`, no `cargo clippy`, no wasm build, no `npm test`, no replay of
  the 12 disagreeing and 12 agreeing trials through the Rust core.
- **C#: nothing changed, `run_parity.py` not run.**
- **Golden fixtures not regenerated; `BUILD_ID` and `ALGORITHM_VERSION` untouched.**
- `pytest neuromech/tests` and the non-scoring CI files (`test_optitrack_marker_angle`,
  `test_labview_bin_reader`, `test_motor_bench`, `test_motor_reference`, `test_camera_utils`) were not re-run
  against the port.
- The baseline CI run (618 passed) overlapped the edit in wall time. The scorer module was imported before the
  edit, so it should reflect unmodified main, but it was not rerun on a clean tree.
- Seven baseline control optical trials, all participant 2 (`2_left_pre_duo_T1`, `T2`; `2_right_pre_duo_T1`
  to `T3`; `2_right_pre_solo_T4`, `T5`), could not be loaded and are absent from both columns of section 7.
- The causes given for the two inverted trials and for the 12 "fixed" trials are read from traces. No corpus
  trial has a ground-truth release time.
- The rig check is a Python stand-in on the true motion; the whole-file case rests on a modelled zero pose.
- The fall in the R2n and omega ICC point estimates was not traced to specific trials.
- `detrend=True` was run only for the headline counts; its per-trial table and ICC are not reported here.
- The motor bench was not re-run.

## Reproducing

Driver scripts and raw outputs were kept in a scratch directory and are not committed. The patch beside this
file, applied to `origin/main` @ `75109c5`, gives the scorer every "after" number here came from.
