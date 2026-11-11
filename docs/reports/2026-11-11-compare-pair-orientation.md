# `compare_pair` orientation, reworked after review

**2026-11-11.** Branch `fix/compare-pair-orientation`, cut from `origin/main` at `e9a3a01`.
Replaces the orientation rule of `4792020` (a port of `fb46178`), which the independent review rejected
(`docs/reports/2026-11-11-scorer-ports-independent-review.md` on `origin/docs/scorer-ports-review`).

Trial keys are `<pid>_<leg>_<condition>_T<n>`. Numbers are per trial or counts of pairs.
RMSE is `rmse_deg_offset_removed` in degrees throughout, because every corpus reference is a relative marker curve.
"Measured" means a number this work produced by running code. "By eye" and "inferred" are marked as such.

## Summary

- **The rule.** Two kinds of evidence say whether a pair is the same way up:
  1. which side of rest each curve was released from, read from the plateau just before the release that `_detect_release` already finds;
  2. the correlation of the two curves after lag alignment, with the reference as given and mirrored.

  The release sides decide, with the correlation able to confirm or contradict them.
  The correlation decides alone only when the lag was pinned by the capture prior or a manual lag.
  Otherwise the pair is **undetermined**: it is scored exactly as given and the result says so.
- **What is changed.** Only the reference is ever mirrored, never the test curve, and only when the caller declares the reference's sign arbitrary (`ref_polarity_arbitrary=True`).
  `batch_imu_vs_optitrack_rmse.py` declares it for marker-path references and never for rigid-body ones. Every other caller is numerically unchanged.
- **Corpus, 145 ok IMU-versus-OptiTrack pairs (measured).**

  | | median RMSE | pairs |
  |---|---|---|
  | main (curves as given) | 21.80 | 145 |
  | `4792020` | 15.10 | 145 |
  | this rule, undetermined pairs included (scored as given) | 14.67 | 145 |
  | this rule, undetermined pairs excluded | 13.95 | 122 |
  | the same 122 pairs under main / `4792020` | 24.20 / 13.95 | 122 |

- **Decisions:** 67 as given, 55 reference mirrored, 23 undetermined.
  None of the 122 decided pairs is decided against the better-fitting orientation.
  All 55 mirrorings rest on both curves' release sides, with the correlation agreeing.
- **The review's 17 disagreeing decisions:** 1 resolved, 16 undetermined, 0 still wrong.
- **The nine trials whose RMSE rose under `4792020`:** all nine are back at main's number. One is decided "as given"; eight are undetermined.
- **Cost:** seven pairs that `4792020` oriented the better-fitting way are now undetermined. On one of them (`17_right_post_T4`) the mirrored orientation is right by eye and the pair is scored as given, 21.3 where 7.3 is available.
- **On the pairs it decides, this rule agrees with `4792020` on all but one.** Its gain is in declining the pairs where `4792020` was guessing, and in saying so in the output.
- The orientation fields and the undetermined state are in the batch row and CSV.

## 1. What the review found, and what answers it

| Review finding | Answer here |
|---|---|
| First-5% window is not the hold when the optical hold is occluded | The hold is the half-second before the detected release, and must be a plateau. No plateau, no verdict from that curve. |
| ...or when the recording opens at rest | Same window: `_detect_release` already steps past a lift-and-hold. A plateau within the margin of rest gives no verdict. |
| Window is a fraction of recording length | All windows are in seconds, anchored on the release and on the end of oscillation, not on the file. |
| Up to 17 of 145 decisions disagree with the better fit | 1 resolved, 16 undetermined, 0 wrong (section 5). |
| One pair oriented inconsistently | Only the reference is ever mirrored, and only on the pair's relative polarity. That pair (`15_right_post_1week_T3`) is now undetermined, with `test_inverted=True` reported. |
| All five tests use the one shape that works | Every shape the review names has a test, and each asserts that the `4792020` rule fails on that very curve (section 4). |
| 1 degree dead-band untested | The dead-band (now 5 degrees and 25% of range) and both correlation thresholds are pinned from both sides. |
| No caller carries the flags into the batch output | Eight columns in the batch row and CSV, and a stratified summary line. |
| Raw `rmse_deg`, bias and LoA in a mirrored frame; wrong for a rigid-body reference | A reference is mirrored only when declared arbitrary. The batch never declares a rigid-body reference arbitrary. A mirrored reference is mirrored about its own mean, so the offset stays about where it was. |
| Non-IMU consumers not measured | Audited in section 6; their numbers do not change. A MediaPipe-versus-OptiTrack run on 146 pairs is there, and it changed the design. |

## 2. Design

### Candidates

The review named three. Each was measured on the 145 pairs.

| Candidate | Decided | Undetermined | Decided against the better fit |
|---|---|---|---|
| Release plateau of each curve only | 109 | 36 | 0 |
| Correlation of the pair only, with a margin | 123 | 22 | 0 |
| Start-versus-tail with guards | not built | | |
| Plateau, and correlation wherever it speaks | 126 | 19 | 0 |
| **Plateau, and correlation alone only under a pinned lag (shipped)** | **122** | **23** | **0** |

Start-versus-tail with guards was not built. The start of the file is not the hold in a rest-lift-hold recording even when it is a clean plateau, and a guard cannot make the start window mean something it does not.

The plateau alone cannot orient a reference whose hold is occluded. The correlation alone is the same information as the reported error (section 3) and fails when the lag is free (below).
Combined, each covers part of the other's blind spot and each can veto the other.

### Why the correlation may not decide alone under a free lag

This restriction was added after the first corpus run, on evidence from a different modality.

The fourth row of the table was the first design. It made no wrong decision on the IMU corpus.
The same function was then run on 146 MediaPipe-versus-OptiTrack pairs (section 6), which have no capture prior, so their lag search is free.
MediaPipe and the IMU measure the same knee on the same optical file, so the two pairs must have the same relative polarity.

- Where the MediaPipe pair had release sides from both curves, it matched the IMU pair's decision on 13 of 13.
- Where it had only the correlation, it matched on 34 and **contradicted the IMU pair on 16 of 50**. In all 16 the IMU pair's decision rests on both release sides and the correlation.

Two causes are visible in the curves (by eye, five of the 16 plotted). In some the MediaPipe trace itself steps the wrong way at release. In others the search lands on the edge of its window, half a cycle from where the other sense would fit.
Either way, a correlation-only verdict under a free lag is not reliable enough to act on.

So the correlation decides alone only when something that cannot see polarity has pinned the lag to well inside half a cycle: the capture prior (a 0.15 s refinement window) or a manual `lag_override_sec`.
Under a free lag it can still confirm the release sides or contradict them.

The cost on the IMU corpus is four pairs, all with an occluded reference hold. An occluded hold also shortens the optical recording, so the duration-based capture prior declines and the lag is free.
They are listed in section 5.

### Evidence 1: each curve's release side (`_release_side`)

For one curve, on its own:

1. Find the release with `pendulastic_pt_score._detect_release`, unchanged, lift-and-hold logic included. That detector works on absolute deviation throughout, so it finds the same release whichever way up the curve is.
2. Take the 0.5 s ending at that release. It must be a plateau:
   - finite samples spanning at least 0.3 s and filling at least half the window's sample slots;
   - peak-to-peak no more than 16% of the signal range. That is the detector's own "has it moved" band (8% of range either side), not a fitted number.
3. Take the settled level as the median within 1 s either side of the end of active oscillation (`_active_window_end`), or of the end of the record when no swing extremum follows the release.
4. The plateau must sit at least 25% of the signal range and at least 5 degrees from the settled level. Physically a hold is 50 to 100% of the range from rest (`_LIFT_MIN_HOLD_DEV` in the scorer makes the same argument), so 25% is half the physical floor.

The verdict is `+1` (released from above rest), `-1` (from below: upside down by the codebase's convention) or none, with a reason:
`no_prerelease_plateau` (occluded or gappy hold, or the record starts in the swing) or `hold_not_clear_of_rest` (opens at rest with no detected lift, or never swung).

### Evidence 2: the pair's correlation

The reference is aligned to the test curve twice, as given and mirrored, each with its own lag search inside the same bound.
The bound (release times, capture prior, active windows) does not depend on polarity; only the peak inside it does.
The pair "moves together" when the as-given Pearson correlation is at least 0.5 **and** at least 0.3 ahead of the mirrored one; "in opposition" for the reverse; otherwise the correlation says nothing.

The margin exists because a mirrored, lightly damped swing also fits half a cycle away. On a synthetic with a free lag search the mirror reaches r = 0.98 against 1.00 and an RMSE of 4.4 degrees.

### Combining them

| Release sides (both curves) | Correlation | Lag pinned | Result | `orientation_basis` |
|---|---|---|---|---|
| both known | agrees | either | decided | `shape+correlation` |
| both known | says nothing | either | decided by the sides | `shape` |
| both known | contradicts | either | **undetermined** | none (`shape_correlation_conflict`) |
| one or both unknown | speaks | yes | decided by the correlation | `correlation` |
| one or both unknown | speaks | no | **undetermined** | none (`correlation_alone_with_unpinned_lag (...)`) |
| one or both unknown | says nothing | either | **undetermined** | none (`correlation_inside_margin (...)`) |

### What "undetermined" does

The comparison still runs, on the curves exactly as given, and returns `orientation="undetermined"` with the reason and both correlations.
It never falls back to the better-fitting orientation.
The batch keeps such rows in the "all" figures and also reports the figures with them excluded (`excluding_orientation_undetermined`).

### What is mirrored, and when

- **Only the reference, never the test curve.** For RMSE only the relative polarity matters. An upside-down test curve may be a real defect in the model path; it is reported (`test_inverted=True`) and left alone.
- **Only when the caller says the reference's sign is arbitrary.** The default is off. A rigid-body reference, a model trace, or anything with a real zero is never mirrored. When the evidence says such a pair is in opposition, the result is `orientation="opposite_not_mirrored"` and the numbers are for the curves as given.
- **About the reference's own mean, not by negation.** The offset between the curves is as arbitrary after as before, but it does not jump by twice the reference's level.

One limit follows from the first point. Against a reference whose sign is arbitrary, the RMSE cannot see an inverted test curve at all: the reference is mirrored to match it.
`test_inverted` is then the only signal, and it is `None` when the test curve has no release side of its own.

### What `compare_pair` returns now

`orientation` (`as_given`, `ref_mirrored`, `opposite_not_mirrored`, `undetermined`), `orientation_basis`, `orientation_reason`, `ref_mirrored`,
`ref_inverted` and `test_inverted` (each `True`, `False`, or `None` when that curve cannot say), `polarity_corr_as_given`, `polarity_corr_mirrored`.
All existing keys are unchanged. With the default arguments every existing number is unchanged (measured: 145 of 145 pairs identical to the `75109c5` engine, maximum difference 0.0).

### Constants

| Constant | Value | Where it comes from |
|---|---|---|
| `_ORIENT_HOLD_WINDOW_S` | 0.5 s | the scorer's shortest hold (`_LIFT_HOLD_MIN_S`) |
| `_ORIENT_PLATEAU_MAX_FRAC` | 0.16 | twice `_detect_release`'s 8% threshold |
| `_ORIENT_MIN_A0_FRAC` | 0.25 | half the physical floor of 50% of range |
| `_ORIENT_MIN_A0_DEG` | 5 deg | same order as `_active_window_end`'s 2 deg floor |
| `_ORIENT_REST_HALF_WINDOW_S` | 1.0 s | about one swing period |
| `_POLARITY_MIN_CORR` | 0.5 | a quarter of the variance shared |
| `_POLARITY_CORR_MARGIN` | 0.3 | chosen; see sensitivity |
| `_ORIENT_HOLD_MIN_FILL` | 0.5 | changed after the first corpus run |

All were set before the first corpus run, with two changes after it. Both are stated here because a rule adjusted on its own test corpus deserves the suspicion.

1. **Hold fill.** The first version required 0.3 s worth of samples in the hold window. Seven corpus references have a clean hold thinned evenly by dropouts (33 to 35 of 60 samples across the full half-second) and were refused. The requirement became "half the window's sample slots". Those seven pairs moved from `correlation` to `shape+correlation`. No final decision changed.
2. **Pinned lag for correlation-only decisions.** Described above. It was prompted by the MediaPipe run, not by the IMU corpus, and it makes the rule decide fewer IMU pairs, not more.

## 3. Why the decision is not the metric optimising itself

Stated plainly first: **at a fixed lag, the sign of the correlation and "which orientation has the lower offset-removed RMSE" are the same decision.**
Offset-removed RMSE squared is `var(ref) + var(test) - 2 r sd(ref) sd(test)`, and mirroring the reference only changes the sign of `r`.
So the correlation is not independent of the reported error. What keeps the reported error from choosing itself is the following.

1. **No mirroring in the corpus rests on the correlation alone (measured).** All 55 mirrored pairs have a release-side verdict from both curves, each made without looking at the other curve.
2. **The 13 correlation-only decisions are all "as given" (measured).** Their reported number is main's number. So no reported number in this corpus was moved by a fit-based choice. The correlation only moved 13 pairs from "undetermined" to "decided", which affects the excluded-set median and nothing else.
3. **Where both kinds of evidence exist, they were compared (measured).** Of the 109 pairs with both release sides, the correlation agrees on 106, says nothing on 3 and contradicts on 0. On the 17 MediaPipe pairs with both release sides: 16, 1 and 0.
4. **The correlation cannot override the release sides.** A contradiction makes the pair undetermined. It does not pick the better fit.
5. **Undetermined pairs are scored as given, not at the better orientation.** The lower of two numbers is never selected. The row for that would be 13.86 (section 5), and the rule reports 14.67.
6. **Under a free lag the correlation may not decide at all**, because that is where it was measured to fail.

Two caveats remain.

- Excluding undetermined pairs removes mostly poor references, so the "excluded" median is lower for that reason as well as for orientation. The like-for-like comparison is on the same 122 pairs: 24.20 on main, 13.95 under `4792020`, 13.95 here.
- A caller who wants no fit-based decision at all can filter on `orientation_basis != "correlation"`: 109 pairs, median 12.72.

## 4. Tests

Order of work, as it happened: the three helper functions were written first, then all the tests, then `compare_pair` was wired.
First run of the new tests, before wiring: 9 failed (`KeyError` on the new result keys) and 19 passed (the helper tests).
After wiring, 5 still failed, on synthetic geometry or tolerances in the tests and not on the rule: an occluded-reference pair whose free lag search reached the half-cycle alias (kept as its own test), a contradiction case that did not contradict strongly enough, and two tolerances.
The pinned-lag tests were added with the pinned-lag change.

`_pendulum_trial` builds rest, lift, hold, release and a decaying swing, with optional drift and an optional start after the release.
`_rule_4792020` in the test file is the rejected rule, verbatim. Where the review says that rule is wrong, the test asserts it is wrong **on the same curve** before asserting the new behaviour.

| Shape | Test | `4792020` on this curve | New rule |
|---|---|---|---|
| Starts on the hold | `test_release_side_reads_a_curve_that_starts_on_its_hold` | right | up; mirror down |
| Rest, lift, hold, release | `..._reads_rest_lift_hold_release` | misses the mirror (asserted) | up; mirror down |
| Opens at rest, +0.3 deg/s drift | `..._ignores_slow_drift_on_a_recording_that_opens_at_rest` | flips the upright curve (asserted) | up; mirror down |
| Resting trace that only drifts | `..._abstains_on_a_resting_trace_that_only_drifts` | flips on drift (asserted) | no verdict |
| Hold missing (starts 0, 0.15, 0.55 s after release) | `..._abstains_when_the_hold_is_missing` | flips the upright curve at 0 s (asserted) | no verdict |
| Hold mostly a gap | `..._abstains_when_the_hold_is_mostly_a_gap` | not asserted | no verdict |
| Short versus long tail, same swing | `..._does_not_depend_on_how_long_the_tail_was_recorded` | changes its mind (asserted) | same verdict |
| Dead-band in degrees | `..._dead_band_in_degrees` | n/a | pinned both sides |
| Margin against range | `..._margin_relative_to_range` | n/a | pinned both sides |
| Correlation thresholds | `test_polarity_from_correlation_margin_boundaries` | n/a | pinned both sides |
| Decision table | `test_decide_relative_polarity_table` | n/a | every row, pinned and free |
| Upside-down relative reference | `test_compare_pair_mirrors_an_upside_down_relative_reference` | right | mirrored, RMSE under 0.5 |
| Same sense | `..._leaves_a_pair_of_the_same_sense_alone` | right | every number identical to default |
| Test curve is the inverted one | `..._when_it_is_the_test_curve_that_is_inverted` | right | reference mirrored, `test_inverted=True`; not mirrored by default |
| Occluded reference hold, capture prior, upright and mirrored | `..._with_an_occluded_reference_hold` | flips the upright one (asserted) | decided by correlation, both right |
| Same pair, free lag | `test_correlation_alone_does_not_decide_when_the_lag_is_not_pinned` | n/a | undetermined; decided again under a manual lag |
| Mirror fits half a cycle away | `..._abstains_when_the_mirror_fits_half_a_cycle_away` | n/a | undetermined, scored as given |
| Noisy low-amplitude reference | `..._abstains_on_a_noisy_low_amplitude_reference` | n/a | undetermined, scored as given |
| Release sides contradict correlation | `..._abstains_when_shape_and_correlation_contradict` | n/a | undetermined |
| Default never mirrors | `..._never_mirrors_unless_the_reference_is_declared_arbitrary` | n/a | `opposite_not_mirrored` |
| NaN gap in the reference | `..._polarity_survives_nan_gaps_in_the_reference` | n/a | mirrored |

Batch tests (`tests/test_batch_imu_vs_optitrack_rmse.py`): the row and CSV carry the eight fields; a marker reference is declared arbitrary and a rigid-body one is not; error rows leave the fields empty; the stratified summary breaks out undetermined pairs.

## 5. Corpus evidence

### Method

The 145 pairs are the reviewer's: the cached IMU and optical curves behind `q23_rows_ext.json`.
For each pair `compare_pair(..., capture_skew_prior=True)` was run four ways with this branch's engine: curves as given; reference negated; both curves oriented by the `4792020` rule; and `ref_polarity_arbitrary=True`.
The first three reproduce the reviewer's numbers on all 145 (maximum difference 0.0).

The real batch was then run end to end from raw files with this branch's code (`batch_imu_vs_optitrack_rmse.main`, with the study checkout's `imu_calibration_config.json` and registries).
It gives 145 ok rows, all `marker_pca`, with the same decision and the same RMSE as the cached-curve run on every one (maximum difference 0.0): 67 as given, 55 mirrored, 23 undetermined; median 14.67, and 13.95 with undetermined pairs excluded.
(A first end-to-end attempt ran without the calibration config, which the worktree does not have. Its numbers are not used.)

### Medians (measured)

| | median | mean | pairs |
|---|---|---|---|
| main, as given | 21.80 | 24.69 | 145 |
| `4792020` | 15.10 | 14.87 | 145 |
| this rule, undetermined included | 14.67 | 14.31 | 145 |
| this rule, undetermined excluded | 13.95 | 13.70 | 122 |
| lower of the two orientations on every pair (not a result: what a self-optimising rule would report) | 13.86 | | 145 |

### Decisions (measured)

| Decision | Pairs | Basis |
|---|---|---|
| as given | 67 | 51 shape+correlation, 3 shape, 13 correlation |
| reference mirrored | 55 | 55 shape+correlation |
| undetermined | 23 | 19 correlation inside the margin, 4 correlation alone under a free lag; no conflicts |

`4792020` flipped 67 references and no test curve.
None of the 122 decided pairs is decided against the lower-RMSE orientation, so no decided pair had to be adjudicated against the fit.
The capture prior applied on 126 of the 145 pairs; all 13 correlation-only decisions are among them.
Of the 19 pairs with a free lag, 2 are decided (both release sides, the correlation agreeing) and 17 are undetermined: a reference too poor to have a release side is usually too poor to have a plausible duration as well.

Release sides, per curve: the reference has one on 112 of 145 (57 down, 55 up); the IMU curve on 141 (139 up, 2 down).

### The review's 17

The 17 are the `4792020` decisions that disagree with the better-fitting orientation (reproduced: the same 17 keys).

| Outcome | Count | Trials |
|---|---|---|
| Resolved (decided, and the better-fitting way) | 1 | `4_right_pre_T4` |
| Undetermined | 16 | `13_left_post_T4`, `14_right_pre_T1`, `15_left_post_T1`, `15_left_post_T3`, `15_left_post_T4`, `15_left_post_T5`, `15_right_post_1month_T1`, `15_right_post_1month_T4`, `15_right_post_1week_T1`, `15_right_post_1week_T2`, `15_right_post_1week_T3`, `15_right_post_T1`, `15_right_post_T2`, `17_left_post_1_month_T5`, `20_right_pre_T1`, `20_right_pre_T3` |
| Still decided the other way | 0 | |

### The nine trials whose RMSE rose under `4792020`

| Trial | main | `4792020` | this rule | Decision | Why |
|---|---|---|---|---|---|
| `17_left_post_1_month_T5` | 7.3 | 26.1 | 7.3 | undetermined | Reference hold occluded; lag free. r 0.95 against 0.36. By eye as given is right: it overlays cycle for cycle at a lag of -0.75 s, the usual capture skew, and the mirror fits half a cycle later. |
| `4_right_pre_T4` | 2.6 | 10.0 | 2.6 | as given (correlation) | Reference hold occluded; lag pinned by the capture prior. r 0.92 against -0.25. |
| `15_right_post_1week_T1` | 10.3 | 28.7 | 10.3 | undetermined | Reference hold occluded; lag free. r 0.84 against 0.44. By eye as given is right. |
| `15_right_post_1month_T1` | 9.7 | 14.4 | 9.7 | undetermined | Reference starts at the first trough. r 0.74 against 0.71: the mirror fits half a cycle away. By eye as given is right. |
| `15_right_post_T1` | 38.5 | 56.2 | 38.5 | undetermined | Reference hold 7 deg from rest on a 145 deg range; narrow spikes. r 0.42. |
| `15_right_post_T2` | 32.3 | 47.5 | 32.3 | undetermined | Same shape. Hold 3 deg from rest. r 0.45. |
| `15_right_post_1month_T4` | 14.2 | 21.9 | 14.2 | undetermined | Reference range 12 deg against the IMU's 79. r 0.54 against 0.49. |
| `15_right_post_1week_T3` | 22.8 | 27.0 | 22.8 | undetermined | Reference is mostly gaps with no plateau. `test_inverted=True` is reported: the IMU plateau is below its settled level. r 0.43. |
| `13_left_post_T4` | 13.4 | 15.4 | 13.4 | undetermined | Reference release side is clear (down). The IMU curve has no pre-release plateau. r 0.55 against 0.36. |

All nine are scored as given, which is the better-fitting orientation for each. For eight of them the rule does not claim to know that.

### Pairs `4792020` oriented the better-fitting way that are now undetermined (measured: 7; none is now decided the other way)

| Trial | as given | negated | `4792020` did | Why undetermined |
|---|---|---|---|---|
| `17_right_post_T4` | 21.3 | 7.3 | flipped | Reference hold occluded; lag free. r 0.50 against 0.94. **By eye the mirrored curve is right**; the review called the `4792020` result here "right by luck". |
| `15_right_post_1week_T4` | 11.1 | 30.4 | kept | Reference hold occluded; lag free. r 0.80 against 0.21. By eye as given is right. |
| `14_left_pre_T4` | 23.8 | 27.4 | kept | Reference range 11 deg against the IMU's 62; hold 2 deg from rest. r 0.49. |
| `15_right_post_1month_T3` | 23.9 | 25.5 | kept | No reference plateau. r 0.39 against 0.41. |
| `15_right_post_T3` | 28.3 | 39.3 | kept | Spike-shaped reference; hold 2 deg from rest. r 0.48. |
| `21_right_pre_T4` | 18.0 | 17.1 | flipped | Reference range 10 deg against 52. r -0.21 against 0.30. |
| `22_right_pre_T3` | 30.0 | 29.5 | flipped | Reference range 12 deg against 73. r 0.05 against 0.33. |

"Oriented the better-fitting way" means only "matched the lower RMSE". In the last five the two orientations differ by 0.5 to 11 degrees on a reference that is poor whichever way up.
The first is the one real loss: a pair that is decidable by eye, left undetermined and scored the wrong way up.

### The 23 undetermined pairs, by reason

| Class | Count | Trials |
|---|---|---|
| Reference has no pre-release plateau; correlation inside the margin | 10 | `15_left_post_T1`, `15_left_post_T3`, `15_left_post_T4`, `15_left_post_T5`, `15_right_post_1month_T1`, `15_right_post_1month_T3`, `15_right_post_1month_T4`, `15_right_post_1week_T2`, `15_right_post_1week_T3`, `20_right_pre_T3` |
| Reference plateau not clear of rest; correlation inside the margin | 8 | `14_left_pre_T4`, `14_right_pre_T1`, `15_right_post_T1`, `15_right_post_T2`, `15_right_post_T3`, `20_right_pre_T1`, `21_right_pre_T4`, `22_right_pre_T3` |
| IMU curve has no pre-release plateau; correlation inside the margin | 1 | `13_left_post_T4` |
| Reference has no pre-release plateau; correlation clear but lag free | 4 | `15_right_post_1week_T1`, `15_right_post_1week_T4`, `17_left_post_1_month_T5`, `17_right_post_T4` |

14 of the 23 are participant 15.

All 23 were plotted (raw curves, and the aligned overlay both ways). What the curves show, by eye:

- **Reference unusable or mostly gaps:** `15_left_post_T1`, `T3`, `T4`, `T5` (square-edged switching, 14 to 27 deg range against 76 to 91); `15_right_post_1week_T2` and `T3` (long gaps).
- **Reference ends at or before the release, so the scored overlap is the hold only:** `14_right_pre_T1`, `20_right_pre_T1`, `20_right_pre_T3`. Their RMSE of 0.6 to 4.3 is not a swing comparison in either orientation.
- **Reference a fraction of the IMU's amplitude:** `14_left_pre_T4`, `15_right_post_1month_T4`, `21_right_pre_T4`, `22_right_pre_T3`.
- **Reference is narrow spikes:** `15_right_post_T1`, `T2`, `T3`. The spikes line up with the IMU's troughs as given.
- **Mirror fits half a cycle away:** `15_right_post_1month_T1`, `15_right_post_1month_T3`.
- **Poor IMU curve:** `13_left_post_T4`.
- **Decidable by eye, lag free:** the four in the last row of the table. As given is right for three; mirrored is right for `17_right_post_T4`.

Eleven of the 23 have a lower RMSE with the reference negated: `17_right_post_T4`, and ten others (`14_right_pre_T1`, `15_left_post_T1`, `T3`, `T4`, `T5`, `15_right_post_1week_T2`, `20_right_pre_T1`, `20_right_pre_T3`, `21_right_pre_T4`, `22_right_pre_T3`).
In none of those ten could the negated orientation be confirmed by eye, and none has a usable reference.

### Decided pairs that were also plotted

13 decided pairs were plotted: the three shape-only decisions and 10 of the 13 correlation-only ones.
In all 13 the decided orientation is the one the curves show.

- Shape-only `4_left_pre_T4` (r 0.24): both curves are released from above. The low correlation is a lag failure, the optical drop sitting 2 s after the IMU's. Fit would have been uninformative here; the release sides are not.
- Shape-only `21_right_pre_T2`: the scored overlap is the hold only (RMSE 0.7). The decision is right; the number is not a swing comparison.
- `4_right_june_post_T1`, `14_left_pre_T2`, `23_left_pre_T1`: not standard releases. The leg is lowered, held for seconds, then raised and oscillates. Both curves show it, the pair is as given (r 0.95 to 0.97), and the per-curve flags (`test_inverted=True` on the first) describe the manoeuvre, not a defect.

### Sensitivity (measured; one constant moved at a time, all 145 re-decided)

| Change | as given / mirrored / undetermined | Decided against the better fit |
|---|---|---|
| shipped | 67 / 55 / 23 | 0 |
| min correlation 0.3 | 71 / 56 / 18 | 0 |
| min correlation 0.4 | 71 / 55 / 19 | 0 |
| min correlation 0.6 | 63 / 55 / 27 | 0 |
| min correlation 0.7 | 60 / 55 / 30 | 0 |
| margin 0.1, 0.2 or 0.5 | 67 / 55 / 23 | 0 |
| hold margin 0.15, 0.20, 0.35 or 0.50 of range | 67 / 55 / 23 | 0 |
| plateau limit 0.10 | 67 / 55 / 23 | 0 |
| plateau limit 0.25 or 0.40 | 68 / 55 / 22 | 0 |
| dead-band 2 or 10 deg | 67 / 55 / 23 | 0 |
| hold window 0.3 s | 64 / 53 / 28 | 0 |
| hold window 1.0 s | 67 / 55 / 23 | 0 |
| hold fill 0.3 or 0.6 | 67 / 55 / 23 | 0 |
| hold fill 0.8 | 65 / 54 / 26 | 0 |
| rest window 0.5 or 2.0 s | 67 / 55 / 23 | 0 |
| correlation never consulted | 54 / 55 / 36 | 0 |
| correlation alone allowed under a free lag (the first design) | 70 / 56 / 19 | 0 |

The margin moves nothing here because under a pinned lag the two correlations are near negatives of each other. It matters under a free lag, where it now only confirms or contradicts.
Before the pinned-lag restriction, a minimum correlation of 0.3 or 0.4 or a margin of 0.1 each produced one decision against the better fit, on an unusable reference.

## 6. Consumers of `compare_pair`

With the default `ref_polarity_arbitrary=False` nothing is mirrored and every pre-existing key is unchanged. So no consumer's numbers move unless it opts in.
Each call also pays for a second alignment and two release-side reads (not timed).

| Consumer | Reference | Reads | Changes? | Correct for its reference? |
|---|---|---|---|---|
| `batch_imu_vs_optitrack_rmse.evaluate_trial` | `load_optitrack_trial` (method known) | all keys | **Yes**: opts in for marker references only | Yes. All 145 ok pairs are `marker_pca`. A rigid-body reference is never mirrored (unit-tested; none in the corpus). |
| `rmse_pipeline_common.score_imu_candidate` | `pt_score.load_optitrack` | raw `rmse_deg` only | No | Unchanged, and not correct today either: see below. |
| `rmse_pipeline_common.score_mediapipe_candidate` and `sweep_mediapipe_config.score_frames` | same | raw `rmse_deg` | No | Same. Must not opt in as things stand: see the MediaPipe run. |
| `sweep_imu_config.score_config` | `pt.load_optitrack` | raw `rmse_deg` | No | Same as `score_imu_candidate`. |
| `sweep_mediapipe_preprocessing.score_candidate` | passed in | raw `rmse_deg` | No | Same as the MediaPipe sweep. |
| `pt_report_common._lag_align_candidate` (line 1594) | the trial's own optical curve | `lag_sec`, raw `rmse_deg` | No | The lag is polarity-dependent: against an upside-down reference the search peaks on the wrong half-cycle. Not changed here. |
| `pendulastic_workbench.get_metrics_snapshot` | any visible trace the user picks | `rmse_deg`, `mae_deg`, `lag_sec` | No. The new keys appear in the snapshot and session export; the CSV export has a fixed field list. | Correct not to mirror: the reference may be a rigid-body curve or a model trace. |
| `generate_paper_figures.py:198` | optical | `lag_sec` only | No | Same lag caveat as `pt_report_common`. |
| `compare_2d_vs_world_angle.py`, `evaluate_*.py`, `prototype_opensim_ik.py`, `sweep_p16_mask_assessor.py` | optical | raw `rmse_deg` | No | Not examined beyond the call site. |

Three things found while reading (none changed in this branch):

- **`rmse_pipeline_common.py` writes no per-trial row.** `score_imu_candidate` returns one float and the sweep CSV holds per-candidate medians, so there was no row there to carry the flags into.
- **The sweep cache will miss.** `workbench_engine` is in `rmse_pipeline_common._FINGERPRINTED_MODULES`, so the next full sweep re-scores every candidate. The landmark cache has its own, narrower fingerprint and survives.
- **The sweeps read raw `rmse_deg` against a relative reference.** That number is dominated by the missing zero whichever way up the reference is. The MediaPipe run below measures it: the median raw `rmse_deg` is 141.3 where the offset-removed median is 18.9.

### MediaPipe versus OptiTrack (measured)

Run: every corpus trial with a stored `*_mediapipe_full_*.csv` beside its IMU files; `knee_angle_deg` against `load_optitrack_trial`; `compare_pair` with the default arguments (as every MediaPipe consumer calls it) and again with `ref_polarity_arbitrary=True`.
147 pairs, 146 ok, all `marker_pca`. 134 of them share their optical file with one of the 145 IMU pairs.

| | |
|---|---|
| Default call, median raw `rmse_deg` | 141.3 |
| Default call, median offset-removed | 18.9 |
| Default numbers changed by this branch | none |
| Opted in: as given / mirrored / undetermined | 3 / 14 / 129 |
| Opted in: basis of the 17 decisions | 16 shape+correlation, 1 shape |
| Undetermined: correlation inside the margin | 75 |
| Undetermined: correlation alone under a free lag | 54 |
| MediaPipe curve has a release side of its own | 18 of 146 |
| The 17 decided pairs, offset-removed median, default to opted in | 29.1 to 14.3 |

Against the IMU pair's decision on the same optical file:

| MediaPipe verdict | Same as the IMU pair | Differs |
|---|---|---|
| Shipped decisions (release sides from both curves) | 13 | 0 |
| Correlation-alone verdicts, which the rule refuses | 34 | 16 |

Reading of this, part measured and part inferred:

- The release-side evidence carries across modalities: 13 of 13.
- The correlation-alone evidence does not under a free lag: 16 of 50 wrong-way. This is what the pinned-lag restriction is for.
- MediaPipe curves rarely have a release side (18 of 146), so only 17 pairs can be decided. For a MediaPipe consumer, opting in as things stand would change little and leave 129 pairs flagged undetermined.
- Inferred, by eye on five of the 16: some stored MediaPipe traces step the wrong way at release. Mirroring an arbitrary-sign reference to match such a trace would hide a real defect, and `test_inverted` would be `None`.
- So the MediaPipe consumers should stay on the default. Orienting their reference properly needs a source the test curve cannot influence, for example the IMU pair's verdict on the same optical file, or a reference loader that returns a consistent sign.

## 7. Gates

Local runs on Windows with the project `.venv`, on the final code.

| Gate | Result |
|---|---|
| `tests/test_workbench_engine.py` | 117 passed (91 on main; 26 new cases) |
| `tests/test_batch_imu_vs_optitrack_rmse.py` | 24 passed (20 on main; 4 new). Includes two real-recording tests, which ran. |
| `tests/test_rmse_pipeline_common.py` | 95 passed |
| `tests/test_rmse_compare_floor.py` | 7 passed |
| `tests/test_pendulastic_workbench.py` (not in CI; uses `compare_pair`) | 65 passed |
| `tests/test_web_export_contract.py` (CI step) | 5 passed |
| CI python file list (`tests/test_pt_score.py` through `neuromech/tests`) | 625 passed, 28 skipped, 3 xfailed |
| `node neuromech/labview/verify_formula_node.js` (CI step) | PASS on all vectors |
| Rust jobs | not run; no Rust file changed |
| Line endings | `git diff --stat` and `--ignore-cr-at-eol` agree; all files LF as on `origin/main` |

## 8. Not verified

- **Another corpus.** The thresholds are validated on these 145 pairs only: one lab, one phone mount, marker-path references. Zero wrong decisions here is not a claim about other data.
- **A rigid-body reference.** The corpus has none. That such a reference is never mirrored is unit-tested at the engine and at the batch call, not observed on real data.
- **Every decided pair by eye.** 36 of 145 pairs were plotted (all 23 undetermined and 13 decided). The other 109 decided pairs were checked against the lower-RMSE orientation and, for 106 of them, by the two kinds of evidence agreeing. None of those 106 was plotted here; the review plotted 16 of the pairs `4792020` improved.
- **A correlation-only mirroring.** None occurs in the corpus under the shipped rule, so that path is exercised only by synthetic tests.
- **Why the 16 MediaPipe verdicts differ.** Five were plotted. The split between wrong-way MediaPipe traces and lag aliases was not measured.
- **The MediaPipe run uses stored CSVs.** They were not regenerated, and their provenance (model version, preprocessing) was not checked.
- **The sweeps.** `sweep_imu_config.py`, `sweep_mediapipe_config.py`, `sweep_mediapipe_preprocessing.py` and `rmse_pipeline_common.run_full_sweep` were read, not run.
- **The workbench UI.** Not launched. Its unit tests pass; the new keys in its snapshot were not looked at on screen.
- **`pt_report_common` reports.** Not regenerated. The lag caveat in section 6 is from reading, not from a measured before and after.
- **`--compare-floor`.** The paired floor gate was not re-run. A pair whose orientation verdict differs between its two passes would show up as a large paired delta; whether any does was not measured.
- **Run time.** The second alignment and the release-side reads were not timed.
- **Lag failures and hold-only overlaps.** `4_left_pre_T4` (optical drop 2 s after the IMU's) and the pairs whose scored overlap is the hold only (`14_right_pre_T1`, `20_right_pre_T1`, `20_right_pre_T3`, `21_right_pre_T2`) are status `ok` with numbers that are not swing comparisons. Seen, not fixed, and present on main.
- **A capture prior that survives an occluded hold.** The four free-lag pairs lose the prior because the optical recording's finite span is shortened by the occlusion. A prior read from the file's full time base might pin them. Not tried: it changes the alignment, which is outside this change.
- **CI on GitHub.** The branch was pushed without a pull request; the gates above are local.
- **The full `pytest tests/` tree.** Only the files listed in section 7 were run.
- **Rust core, C#, the web app.** Not touched and not checked for their own optical comparisons.

## Appendix: all 145 pairs

RMSE is offset-removed, in degrees. "ref / test side" is each curve's own release-side verdict (`up`, `down`, or `?` for none).

| trial | as given | ref negated | `4792020` | this rule | decision | basis | r as given / mirrored | ref / test side |
|---|---|---|---|---|---|---|---|---|
| `13_left_month_post_T1` | 6.6 | 44.8 | 6.6 (keep) | 6.6 | as_given | shape+correlation | +0.99 / -0.93 | up / up |
| `13_left_month_post_T2` | 37.7 | 12.4 | 12.4 (flip) | 12.4 | ref_mirrored | shape+correlation | -0.79 / +0.95 | down / up |
| `13_left_month_post_T3` | 40.7 | 17.5 | 17.5 (flip) | 17.5 | ref_mirrored | shape+correlation | -0.75 / +0.95 | down / up |
| `13_left_month_post_T4` | 42.1 | 18.3 | 18.3 (flip) | 18.3 | ref_mirrored | shape+correlation | -0.76 / +0.96 | down / up |
| `13_left_post_T1` | 41.2 | 7.2 | 7.2 (flip) | 7.2 | ref_mirrored | shape+correlation | -0.86 / +0.99 | down / up |
| `13_left_post_T2` | 41.0 | 9.8 | 9.8 (flip) | 9.8 | ref_mirrored | shape+correlation | -0.81 / +0.98 | down / up |
| `13_left_post_T3` | 30.4 | 5.2 | 5.2 (flip) | 5.2 | ref_mirrored | shape+correlation | -0.88 / +0.97 | down / up |
| `13_left_post_T4` | 13.4 | 15.4 | 15.4 (flip) | 13.4 | undetermined | - | +0.55 / +0.36 | down / ? |
| `13_left_week_1_post_T1` | 45.7 | 8.9 | 8.9 (flip) | 8.9 | ref_mirrored | shape+correlation | -0.94 / +0.98 | down / up |
| `13_left_week_1_post_T2` | 34.7 | 15.4 | 15.4 (flip) | 15.4 | ref_mirrored | shape+correlation | -0.47 / +0.86 | down / up |
| `13_left_week_1_post_T3` | 41.1 | 18.1 | 18.1 (flip) | 18.1 | ref_mirrored | shape+correlation | -0.72 / +0.90 | down / up |
| `13_left_week_1_post_T4` | 35.9 | 15.7 | 15.7 (flip) | 15.7 | ref_mirrored | shape+correlation | -0.67 / +0.89 | down / up |
| `13_right_month_post_T1` | 9.7 | 27.6 | 9.7 (keep) | 9.7 | as_given | correlation | +0.90 / -0.78 | up / ? |
| `13_right_month_post_T2` | 21.8 | 26.0 | 21.8 (keep) | 21.8 | as_given | correlation | +0.56 / -0.26 | ? / up |
| `13_right_month_post_T3` | 16.2 | 31.5 | 16.2 (keep) | 16.2 | as_given | shape+correlation | +0.90 / -0.73 | up / up |
| `13_right_month_post_T4` | 15.7 | 35.5 | 15.7 (keep) | 15.7 | as_given | shape+correlation | +0.91 / -0.81 | up / up |
| `13_right_month_post_T5` | 12.7 | 33.2 | 12.7 (keep) | 12.7 | as_given | shape+correlation | +0.94 / -0.85 | up / up |
| `13_right_post_T1` | 48.9 | 3.6 | 3.6 (flip) | 3.6 | ref_mirrored | shape+correlation | -0.84 / +1.00 | down / up |
| `13_right_post_T3` | 50.2 | 5.7 | 5.7 (flip) | 5.7 | ref_mirrored | shape+correlation | -0.86 / +1.00 | down / up |
| `13_right_post_T4` | 49.0 | 7.6 | 7.6 (flip) | 7.6 | ref_mirrored | shape+correlation | -0.81 / +0.99 | down / up |
| `13_right_post_T5` | 45.8 | 7.6 | 7.6 (flip) | 7.6 | ref_mirrored | shape+correlation | -0.82 / +0.99 | down / up |
| `13_right_week_1_post_T1` | 32.8 | 8.0 | 8.0 (flip) | 8.0 | ref_mirrored | shape+correlation | -0.90 / +0.96 | down / up |
| `13_right_week_1_post_T3` | 40.4 | 3.4 | 3.4 (flip) | 3.4 | ref_mirrored | shape+correlation | -0.93 / +0.99 | down / up |
| `13_right_week_1_post_T4` | 9.2 | 40.3 | 9.2 (keep) | 9.2 | as_given | shape+correlation | +0.97 / -0.91 | up / up |
| `13_right_week_1_post_T5` | 30.3 | 5.4 | 5.4 (flip) | 5.4 | ref_mirrored | shape+correlation | -0.87 / +0.95 | down / up |
| `14_left_pre_T1` | 23.9 | 8.9 | 8.9 (flip) | 8.9 | ref_mirrored | shape+correlation | -0.88 / +0.91 | down / up |
| `14_left_pre_T2` | 5.3 | 46.8 | 5.3 (keep) | 5.3 | as_given | correlation | +0.97 / -0.96 | down / ? |
| `14_left_pre_T3` | 32.1 | 26.7 | 26.7 (flip) | 26.7 | ref_mirrored | shape+correlation | -0.53 / +0.61 | down / up |
| `14_left_pre_T4` | 23.8 | 27.4 | 23.8 (keep) | 23.8 | undetermined | - | +0.49 / -0.43 | ? / up |
| `14_right_pre_T1` | 2.2 | 1.9 | 2.2 (keep) | 2.2 | undetermined | - | -0.24 / +0.47 | ? / up |
| `14_right_pre_T2` | 1.9 | 56.7 | 1.9 (keep) | 1.9 | as_given | shape+correlation | +1.00 / -0.97 | up / up |
| `14_right_pre_T3` | 53.7 | 1.5 | 1.5 (flip) | 1.5 | ref_mirrored | shape+correlation | -0.98 / +1.00 | down / up |
| `14_right_pre_T4` | 27.7 | 7.1 | 7.1 (flip) | 7.1 | ref_mirrored | shape+correlation | -0.29 / +0.95 | down / up |
| `15_left_post_1month_T3` | 17.8 | 32.7 | 17.8 (keep) | 17.8 | as_given | shape+correlation | +0.75 / -0.54 | up / up |
| `15_left_post_1month_T4` | 18.7 | 39.4 | 18.7 (keep) | 18.7 | as_given | shape+correlation | +0.89 / -0.67 | up / up |
| `15_left_post_1month_T5` | 19.0 | 40.6 | 19.0 (keep) | 19.0 | as_given | shape+correlation | +0.78 / -0.62 | up / up |
| `15_left_post_1week_T1` | 35.7 | 19.0 | 19.0 (flip) | 19.0 | ref_mirrored | shape+correlation | -0.40 / +0.82 | down / up |
| `15_left_post_1week_T2` | 14.7 | 33.4 | 14.7 (keep) | 14.7 | as_given | shape+correlation | +0.88 / -0.68 | up / up |
| `15_left_post_1week_T3` | 38.5 | 18.3 | 18.3 (flip) | 18.3 | ref_mirrored | shape+correlation | -0.77 / +0.91 | down / up |
| `15_left_post_1week_T4` | 19.2 | 41.1 | 19.2 (keep) | 19.2 | as_given | shape+correlation | +0.93 / -0.85 | up / up |
| `15_left_post_T1` | 21.3 | 18.3 | 21.3 (keep) | 21.3 | undetermined | - | +0.24 / +0.30 | ? / up |
| `15_left_post_T3` | 15.3 | 11.9 | 15.3 (keep) | 15.3 | undetermined | - | +0.52 / +0.33 | ? / up |
| `15_left_post_T4` | 18.8 | 14.1 | 18.8 (keep) | 18.8 | undetermined | - | +0.27 / -0.10 | ? / up |
| `15_left_post_T5` | 15.3 | 12.9 | 15.3 (keep) | 15.3 | undetermined | - | +0.31 / +0.13 | ? / up |
| `15_right_post_1month_T1` | 9.7 | 14.4 | 14.4 (flip) | 9.7 | undetermined | - | +0.74 / +0.71 | ? / up |
| `15_right_post_1month_T2` | 19.0 | 46.5 | 19.0 (keep) | 19.0 | as_given | shape+correlation | +0.83 / -0.67 | up / up |
| `15_right_post_1month_T3` | 23.9 | 25.5 | 23.9 (keep) | 23.9 | undetermined | - | +0.39 / +0.41 | ? / up |
| `15_right_post_1month_T4` | 14.2 | 21.9 | 21.9 (flip) | 14.2 | undetermined | - | +0.54 / +0.49 | ? / up |
| `15_right_post_1month_T5` | 21.6 | 41.7 | 21.6 (keep) | 21.6 | as_given | correlation | +0.67 / -0.47 | ? / up |
| `15_right_post_1week_T1` | 10.3 | 28.7 | 28.7 (flip) | 10.3 | undetermined | - | +0.84 / +0.44 | ? / up |
| `15_right_post_1week_T2` | 21.8 | 13.6 | 21.8 (keep) | 21.8 | undetermined | - | +0.49 / +0.15 | ? / up |
| `15_right_post_1week_T3` | 22.8 | 27.0 | 27.0 (flip) | 22.8 | undetermined | - | +0.43 / +0.04 | ? / down |
| `15_right_post_1week_T4` | 11.1 | 30.4 | 11.1 (keep) | 11.1 | undetermined | - | +0.80 / +0.21 | ? / up |
| `15_right_post_T1` | 38.5 | 56.2 | 56.2 (flip) | 38.5 | undetermined | - | +0.42 / -0.25 | ? / up |
| `15_right_post_T2` | 32.3 | 47.5 | 47.5 (flip) | 32.3 | undetermined | - | +0.45 / -0.30 | ? / up |
| `15_right_post_T3` | 28.3 | 39.3 | 28.3 (keep) | 28.3 | undetermined | - | +0.48 / -0.35 | ? / up |
| `15_right_post_T4` | 25.8 | 35.9 | 25.8 (keep) | 25.8 | as_given | shape+correlation | +0.57 / -0.45 | up / up |
| `17_left_post_1_month_T1` | 9.3 | 66.8 | 9.3 (keep) | 9.3 | as_given | shape+correlation | +0.99 / -0.90 | up / up |
| `17_left_post_1_month_T3` | 44.1 | 9.0 | 9.0 (flip) | 9.0 | ref_mirrored | shape+correlation | -0.81 / +0.99 | down / up |
| `17_left_post_1_month_T4` | 17.7 | 49.3 | 17.7 (keep) | 17.7 | as_given | shape+correlation | +0.79 / -0.62 | up / up |
| `17_left_post_1_month_T5` | 7.3 | 26.1 | 26.1 (flip) | 7.3 | undetermined | - | +0.95 / +0.36 | ? / up |
| `17_left_post_1_week_T1` | 26.8 | 34.3 | 26.8 (keep) | 26.8 | as_given | correlation | +0.61 / -0.39 | ? / up |
| `17_left_post_1_week_T2` | 46.4 | 18.7 | 18.7 (flip) | 18.7 | ref_mirrored | shape+correlation | -0.88 / +0.97 | down / up |
| `17_left_post_1_week_T3` | 36.8 | 17.4 | 17.4 (flip) | 17.4 | ref_mirrored | shape+correlation | -0.83 / +0.97 | down / up |
| `17_left_post_1_week_T4` | 40.2 | 23.1 | 23.1 (flip) | 23.1 | ref_mirrored | shape+correlation | -0.63 / +0.92 | down / up |
| `17_left_post_T1` | 50.2 | 8.8 | 8.8 (flip) | 8.8 | ref_mirrored | shape+correlation | -0.86 / +0.98 | down / up |
| `17_left_post_T2` | 43.6 | 10.2 | 10.2 (flip) | 10.2 | ref_mirrored | shape+correlation | -0.83 / +0.98 | down / up |
| `17_left_post_T3` | 42.6 | 7.7 | 7.7 (flip) | 7.7 | ref_mirrored | shape+correlation | -0.82 / +0.98 | down / up |
| `17_left_post_T4` | 50.0 | 11.9 | 11.9 (flip) | 11.9 | ref_mirrored | shape+correlation | -0.86 / +0.98 | down / up |
| `17_right_post_1_month_T1` | 51.9 | 16.3 | 16.3 (flip) | 16.3 | ref_mirrored | shape+correlation | -0.68 / +0.90 | down / up |
| `17_right_post_1_month_T2` | 48.9 | 15.1 | 15.1 (flip) | 15.1 | ref_mirrored | shape+correlation | -0.68 / +0.91 | down / up |
| `17_right_post_1_month_T3` | 39.5 | 12.8 | 12.8 (flip) | 12.8 | ref_mirrored | shape+correlation | -0.72 / +0.97 | down / up |
| `17_right_post_1_month_T4` | 42.6 | 20.1 | 20.1 (flip) | 20.1 | ref_mirrored | shape+correlation | -0.75 / +0.96 | down / up |
| `17_right_post_1_week_T1` | 44.9 | 30.3 | 30.3 (flip) | 30.3 | ref_mirrored | shape+correlation | -0.64 / +0.83 | down / up |
| `17_right_post_1_week_T2` | 37.4 | 29.9 | 29.9 (flip) | 29.9 | ref_mirrored | shape+correlation | -0.62 / +0.79 | down / up |
| `17_right_post_1_week_T3` | 27.8 | 36.8 | 27.8 (keep) | 27.8 | as_given | shape+correlation | +0.70 / -0.55 | up / up |
| `17_right_post_1_week_T4` | 53.0 | 9.7 | 9.7 (flip) | 9.7 | ref_mirrored | shape+correlation | -0.80 / +0.96 | down / up |
| `17_right_post_T1` | 14.7 | 47.7 | 14.7 (keep) | 14.7 | as_given | shape+correlation | +0.93 / -0.77 | up / up |
| `17_right_post_T2` | 31.2 | 18.0 | 18.0 (flip) | 18.0 | ref_mirrored | shape+correlation | -0.72 / +0.92 | down / up |
| `17_right_post_T3` | 36.2 | 25.1 | 25.1 (flip) | 25.1 | ref_mirrored | shape+correlation | -0.71 / +0.89 | down / up |
| `17_right_post_T4` | 21.3 | 7.3 | 7.3 (flip) | 21.3 | undetermined | - | +0.50 / +0.94 | ? / up |
| `18_left_pre_T2` | 21.3 | 38.1 | 21.3 (keep) | 21.3 | as_given | correlation | +0.74 / -0.66 | ? / up |
| `18_left_pre_T4` | 27.3 | 36.6 | 27.3 (keep) | 27.3 | as_given | shape | +0.42 / -0.23 | up / up |
| `19_left_pre_T1` | 30.6 | 58.3 | 30.6 (keep) | 30.6 | as_given | correlation | +0.60 / -0.44 | ? / up |
| `19_left_pre_T2` | 3.4 | 52.8 | 3.4 (keep) | 3.4 | as_given | shape+correlation | +0.99 / -0.92 | up / up |
| `19_left_pre_T3` | 5.2 | 61.2 | 5.2 (keep) | 5.2 | as_given | shape+correlation | +0.99 / -0.92 | up / up |
| `19_left_pre_T4` | 27.8 | 50.1 | 27.8 (keep) | 27.8 | as_given | correlation | +0.56 / -0.42 | ? / up |
| `19_right_pre_T1` | 13.9 | 36.3 | 13.9 (keep) | 13.9 | as_given | shape+correlation | +0.97 / -0.85 | up / up |
| `19_right_pre_T2` | 11.6 | 38.2 | 11.6 (keep) | 11.6 | as_given | shape+correlation | +0.97 / -0.87 | up / up |
| `19_right_pre_T3` | 9.8 | 35.4 | 9.8 (keep) | 9.8 | as_given | shape+correlation | +0.98 / -0.86 | up / up |
| `19_right_pre_T4` | 14.7 | 41.2 | 14.7 (keep) | 14.7 | as_given | shape+correlation | +0.99 / -0.90 | up / up |
| `20_right_pre_T1` | 4.3 | 2.7 | 4.3 (keep) | 4.3 | undetermined | - | n/a / +0.62 | ? / up |
| `20_right_pre_T2` | 10.7 | 38.3 | 10.7 (keep) | 10.7 | as_given | shape+correlation | +0.98 / -0.92 | up / up |
| `20_right_pre_T3` | 0.6 | 0.5 | 0.6 (keep) | 0.6 | undetermined | - | +0.12 / -0.06 | ? / up |
| `21_left_pre_T1` | 22.2 | 24.4 | 22.2 (keep) | 22.2 | as_given | correlation | +0.52 / -0.28 | ? / up |
| `21_left_pre_T2` | 15.1 | 32.8 | 15.1 (keep) | 15.1 | as_given | shape+correlation | +0.98 / -0.88 | up / up |
| `21_left_pre_T3` | 16.2 | 35.8 | 16.2 (keep) | 16.2 | as_given | shape+correlation | +0.98 / -0.90 | up / up |
| `21_left_pre_T4` | 15.8 | 29.6 | 15.8 (keep) | 15.8 | as_given | shape+correlation | +0.96 / -0.80 | up / up |
| `21_left_pre_T5` | 16.7 | 33.8 | 16.7 (keep) | 16.7 | as_given | shape+correlation | +0.97 / -0.86 | up / up |
| `21_right_pre_T2` | 0.7 | 0.8 | 0.7 (keep) | 0.7 | as_given | shape | +0.18 / +0.11 | up / up |
| `21_right_pre_T3` | 5.6 | 29.2 | 5.6 (keep) | 5.6 | as_given | shape+correlation | +0.99 / -0.96 | up / up |
| `21_right_pre_T4` | 18.0 | 17.1 | 17.1 (flip) | 18.0 | undetermined | - | -0.21 / +0.30 | ? / up |
| `21_right_pre_T5` | 23.5 | 11.9 | 11.9 (flip) | 11.9 | ref_mirrored | shape+correlation | -0.93 / +0.96 | down / up |
| `22_right_pre_T1` | 37.1 | 21.2 | 21.2 (flip) | 21.2 | ref_mirrored | shape+correlation | -0.85 / +0.97 | down / up |
| `22_right_pre_T2` | 19.8 | 37.4 | 19.8 (keep) | 19.8 | as_given | shape+correlation | +0.97 / -0.86 | up / up |
| `22_right_pre_T3` | 30.0 | 29.5 | 29.5 (flip) | 30.0 | undetermined | - | +0.05 / +0.33 | ? / up |
| `22_right_pre_T4` | 17.2 | 42.2 | 17.2 (keep) | 17.2 | as_given | shape+correlation | +0.99 / -0.86 | up / up |
| `23_left_pre_T1` | 17.5 | 32.5 | 17.5 (keep) | 17.5 | as_given | correlation | +0.95 / -0.92 | ? / ? |
| `23_left_pre_T2` | 10.7 | 42.3 | 10.7 (keep) | 10.7 | as_given | shape+correlation | +0.96 / -0.77 | up / up |
| `23_left_pre_T3` | 12.9 | 50.6 | 12.9 (keep) | 12.9 | as_given | shape+correlation | +0.98 / -0.77 | up / up |
| `23_left_pre_T4` | 45.7 | 16.5 | 16.5 (flip) | 16.5 | ref_mirrored | shape+correlation | -0.83 / +0.96 | down / up |
| `23_right_pre_T1` | 24.5 | 44.5 | 24.5 (keep) | 24.5 | as_given | shape+correlation | +0.72 / -0.51 | up / up |
| `23_right_pre_T2` | 17.3 | 41.5 | 17.3 (keep) | 17.3 | as_given | shape+correlation | +0.85 / -0.65 | up / up |
| `23_right_pre_T3` | 16.2 | 40.9 | 16.2 (keep) | 16.2 | as_given | shape+correlation | +0.83 / -0.61 | up / up |
| `23_right_pre_T4` | 17.5 | 42.4 | 17.5 (keep) | 17.5 | as_given | shape+correlation | +0.90 / -0.72 | up / up |
| `24_left_pre_T1` | 4.6 | 45.2 | 4.6 (keep) | 4.6 | as_given | shape+correlation | +0.99 / -0.94 | up / up |
| `24_left_pre_T2` | 4.2 | 47.3 | 4.2 (keep) | 4.2 | as_given | shape+correlation | +1.00 / -0.95 | up / up |
| `24_left_pre_T3` | 6.5 | 54.3 | 6.5 (keep) | 6.5 | as_given | shape+correlation | +0.99 / -0.94 | up / up |
| `24_left_pre_T4` | 4.5 | 53.1 | 4.5 (keep) | 4.5 | as_given | shape+correlation | +0.99 / -0.95 | up / up |
| `24_right_pre_T2` | 6.0 | 52.0 | 6.0 (keep) | 6.0 | as_given | shape+correlation | +1.00 / -0.91 | up / up |
| `24_right_pre_T3` | 47.7 | 6.0 | 6.0 (flip) | 6.0 | ref_mirrored | shape+correlation | -0.90 / +0.99 | down / up |
| `4_left_june_post_T1` | 41.8 | 24.1 | 24.1 (flip) | 24.1 | ref_mirrored | shape+correlation | -0.44 / +0.52 | down / up |
| `4_left_june_post_T2` | 17.7 | 25.9 | 17.7 (keep) | 17.7 | as_given | correlation | +0.54 / -0.40 | ? / up |
| `4_left_june_post_T3` | 41.8 | 15.5 | 15.5 (flip) | 15.5 | ref_mirrored | shape+correlation | -0.70 / +0.76 | down / up |
| `4_left_june_post_T4` | 35.3 | 21.3 | 21.3 (flip) | 21.3 | ref_mirrored | shape+correlation | -0.39 / +0.58 | down / up |
| `4_left_june_post_T5` | 18.1 | 34.0 | 18.1 (keep) | 18.1 | as_given | shape+correlation | +0.75 / -0.60 | up / up |
| `4_left_pre_T1` | 11.2 | 40.1 | 11.2 (keep) | 11.2 | as_given | shape+correlation | +0.96 / -0.89 | up / up |
| `4_left_pre_T3` | 8.6 | 44.5 | 8.6 (keep) | 8.6 | as_given | shape+correlation | +0.97 / -0.85 | up / up |
| `4_left_pre_T4` | 29.4 | 35.3 | 29.4 (keep) | 29.4 | as_given | shape | +0.24 / -0.15 | up / up |
| `4_left_pre_T5` | 4.3 | 40.8 | 4.3 (keep) | 4.3 | as_given | shape+correlation | +0.98 / -0.81 | up / up |
| `4_right_june_post_T1` | 14.0 | 58.4 | 14.0 (keep) | 14.0 | as_given | correlation | +0.97 / -0.94 | ? / down |
| `4_right_june_post_T2` | 12.7 | 51.9 | 12.7 (keep) | 12.7 | as_given | shape+correlation | +0.93 / -0.82 | up / up |
| `4_right_june_post_T4` | 40.4 | 4.2 | 4.2 (flip) | 4.2 | ref_mirrored | shape+correlation | -0.87 / +0.98 | down / up |
| `4_right_pre_T1` | 38.4 | 20.1 | 20.1 (flip) | 20.1 | ref_mirrored | shape+correlation | -0.84 / +0.94 | down / up |
| `4_right_pre_T3` | 34.9 | 17.6 | 17.6 (flip) | 17.6 | ref_mirrored | shape+correlation | -0.59 / +0.85 | down / up |
| `4_right_pre_T4` | 2.6 | 10.0 | 10.0 (flip) | 2.6 | as_given | correlation | +0.92 / -0.25 | ? / up |
| `4_right_pre_T5` | 32.0 | 12.0 | 12.0 (flip) | 12.0 | ref_mirrored | shape+correlation | -0.85 / +0.95 | down / up |
| `5_left_post_1month_T2` | 48.8 | 7.2 | 7.2 (flip) | 7.2 | ref_mirrored | shape+correlation | -0.84 / +0.98 | down / up |
| `5_left_post_1month_T3` | 37.8 | 5.7 | 5.7 (flip) | 5.7 | ref_mirrored | shape+correlation | -0.32 / +0.99 | down / up |
| `5_left_post_1month_T4` | 43.8 | 6.7 | 6.7 (flip) | 6.7 | ref_mirrored | shape+correlation | -0.74 / +0.98 | down / up |
| `5_left_post_1month_T5` | 50.7 | 7.8 | 7.8 (flip) | 7.8 | ref_mirrored | shape+correlation | -0.88 / +0.98 | down / up |
| `5_right_post_1month_T1` | 4.3 | 48.2 | 4.3 (keep) | 4.3 | as_given | shape+correlation | +0.99 / -0.85 | up / up |
| `5_right_post_1month_T2` | 5.9 | 55.6 | 5.9 (keep) | 5.9 | as_given | shape+correlation | +1.00 / -0.91 | up / up |
| `5_right_post_1month_T3` | 4.8 | 52.9 | 4.8 (keep) | 4.8 | as_given | shape+correlation | +0.99 / -0.90 | up / up |
| `5_right_post_1month_T4` | 4.5 | 49.2 | 4.5 (keep) | 4.5 | as_given | shape+correlation | +0.99 / -0.81 | up / up |
