# `compare_pair` orientation, reworked after review

**2026-11-11.** Branch `fix/compare-pair-orientation`, cut from `origin/main` at `e9a3a01`.
Replaces the orientation rule of `4792020` (a port of `fb46178`), which the independent review rejected
(`docs/reports/2026-11-11-scorer-ports-independent-review.md` on `origin/docs/scorer-ports-review`).

Trial keys are `<pid>_<leg>_<condition>_T<n>`. Numbers are per trial or counts of pairs.
RMSE is `rmse_deg_offset_removed` in degrees throughout, because every corpus reference is a relative marker curve.
"Measured" means a number this work produced by running code. "By eye" and "inferred" are marked as such.

**Revised 2026-11-12** after a second independent review, of this branch at `a5a5cf4`
(`docs/reports/2026-11-11-compare-pair-orientation-review.md` on `origin/docs/compare-pair-orientation-review`; "the second review" below).
Section 0 lists what that review required and what was done. Every number in this document is for the revised code unless it says "at `a5a5cf4`".

## Summary

- **The rule.** Two kinds of evidence say whether a pair is the same way up:
  1. which side of rest each curve was released from, read from the plateau just before the release that `_detect_release` already finds;
  2. the correlation of the two curves after lag alignment, with the reference as given and mirrored.

  The release sides decide, with the correlation able to confirm or contradict them.
  The correlation alone never mirrors anything. It may confirm a pair "as given" only when the capture prior pinned the lag, neither take has a gap at its ends, and the reference moved.
  Otherwise the pair is **undetermined**: it is scored exactly as given and the result says so.
- **What is changed.** Only the reference is ever mirrored, never the test curve, and only when the caller declares the reference's sign arbitrary (`ref_polarity_arbitrary=True`).
  `batch_imu_vs_optitrack_rmse.py` declares it only for a curve the optical loader itself calls relative. Every other caller is numerically unchanged.
- **Corpus, 145 ok IMU-versus-OptiTrack pairs (measured).** The all-pairs median is the result. The decided-pairs figure is a subset and is given only beside what it leaves out.

  | | median RMSE | pairs |
  |---|---|---|
  | main (curves as given) | 21.80 | 145 |
  | `4792020` | 15.10 | 145 |
  | **this rule, all pairs** (undetermined ones scored as given) | **14.67** | 145 |
  | bound on that median, the 24 undetermined pairs at their better / worse fit | 13.86 / 15.30 | 145 |
  | subset: the 121 decided pairs | 14.04 | 121 |
  | subset: the 24 undetermined pairs, scored as given | 16.66 | 24 |
  | the same 121 decided pairs under main / `4792020` | 24.48 / 14.04 | 121 |

- **Decisions:** 66 as given, 55 reference mirrored, 24 undetermined.
  None of the 121 decided pairs is decided against the better-fitting orientation.
  All 55 mirrorings rest on both curves' release sides, with the correlation agreeing.
- **Whose pairs are undetermined:** 14 of the 24 are participant 15 (14 of that participant's 24 pairs), and 2 are participant 20 (2 of 3). Six other participants have 1 or 2 each.
- **The review's 17 disagreeing decisions:** 17 undetermined, 0 still wrong. None is positively resolved.
- **The nine trials whose RMSE rose under `4792020`:** all nine are back at main's number, and all nine are undetermined.
- **Cost of abstaining:** seven pairs that `4792020` oriented the better-fitting way are undetermined. The second review read the 24 curves against the markers and found six that a careful reader can decide; on two of those the as-given score is not the right orientation's (`17_right_post_T4`: 21.3 where 7.3 is right; `13_left_post_T4`: 13.4 where the right orientation scores 15.4).
- **On the pairs it decides, this rule agrees with `4792020` on every one.** Its gain is in declining the pairs where `4792020` was guessing, and in saying so in the output.
- **Four corpus pairs are two inverted curves agreeing** (`4_right_june_post_T1`, `14_left_pre_T2`, `23_left_pre_T1`, `15_right_post_1week_T3`). The pair's relative polarity is right; the IMU curve itself is upside down. Open issue for the IMU pipeline's owner (section 5).
- The orientation fields and the undetermined state are in the batch row and CSV, and the batch summary prints the counts, the bound and the per-participant losses.

## 0. Revision after the second review

The second review approved the rule's decisions on this corpus and required six changes. All six are applied. Nothing in it was disputed.

| # | Required | Done |
|---|---|---|
| 1 | The correlation-only path must never mirror, and must not decide alone unless the lag really is pinned. Add an amplitude floor for the reference. | The correlation alone can now only confirm "as given". It needs: the capture prior applied; no more than 0.15 s (the refine margin) missing at the ends of the two takes; a reference range of at least 10 degrees over at least 2 s of scored overlap. A manual lag no longer counts as a pin. Section 2. |
| 2 | Never print the excluding-undetermined median alone. | `compute_orientation_summary` and `format_orientation_summary` in the batch. The stratified table no longer prints an `excluding_orientation_undetermined` line. Section 5 shows the block. |
| 3 | Correct the reading of four rest-lift-hold trials. | Corrected in section 5 and raised as an open issue. Nothing in the IMU pipeline was changed. |
| 4 | Kill the 21 surviving mutations. | 21 of 21 killed (section 4). |
| 5 | `method != "rigid_body"` is too broad. | New `load_optitrack_reference` returns the loader's own "relative" declaration; the batch passes that. A precomputed-angle file or quaternion fallback is never declared arbitrary. |
| 6 | Whitespace on two lines; "inside margin" when the correlation is undefined; "bias stays close"; the `opposite_not_mirrored` mislabel. | All four fixed (section 2, "What `compare_pair` returns now"). |

What it changed on the corpus (measured): one pair, `4_right_pre_T4`, moves from "as given (correlation)" to undetermined. Its reference has a 1.13 s leading gap, so its "pinned" lag was 1.13 s from where the take's own time base puts it. Its RMSE (2.6) is unchanged because undetermined pairs are scored as given.
No other decision and no per-trial RMSE changed. 67 / 55 / 23 became 66 / 55 / 24.

The second review's occlusion experiments, re-run on the revised code (measured):

| Experiment | at `a5a5cf4` (the review's figures) | revised |
|---|---|---|
| Synthetic take started on time, hold occluded, both polarities, 0.8 to 1.1 Hz, three hold lengths, ten occlusion lengths (240 cases) | same-sense pairs mirrored at 2.1 to 4.3 degrees | 240 undetermined, 0 decided |
| 104 clean corpus pairs with the reference hold blanked to the release plus 0 to 0.5 s (520 cases) | 59 right, 1 wrong, 460 undetermined | 48 right (all on release sides that survived the blanking), 0 wrong, 472 undetermined |
| The same 520 with the gap cut off before the call, so the time base cannot show it | not run | 55 right (7 by correlation), 0 wrong, 465 undetermined |

In the synthetic set both guards are needed. Of the 240 cases, 71 have a clear mirrored fit and are refused because the correlation alone never mirrors; 38 of those are same-sense pairs, which mirroring would have got wrong. Another 44 have a clear as-given fit and are refused by the end-gap check; 18 of those are opposed pairs, which "as given" would have got wrong. The rest are refused by the margin (98) or have no capture prior at all (27).

One limit is stated in the code and here: the end-gap check reads the reference's time base. A caller who drops the NaN samples before calling `compare_pair` hides the gap from it. The batch passes the loader's full time base.

## 1. What the review found, and what answers it

| Review finding | Answer here |
|---|---|
| First-5% window is not the hold when the optical hold is occluded | The hold is the half-second before the detected release, and must be a plateau. No plateau, no verdict from that curve. |
| ...or when the recording opens at rest | Same window: `_detect_release` already steps past a lift-and-hold. A plateau within the margin of rest gives no verdict. |
| Window is a fraction of recording length | All windows are in seconds, anchored on the release and on the end of oscillation, not on the file. |
| Up to 17 of 145 decisions disagree with the better fit | 17 undetermined, 0 wrong (section 5). |
| One pair oriented inconsistently | Only the reference is ever mirrored, and only on the pair's relative polarity. That pair (`15_right_post_1week_T3`) is now undetermined, with `test_inverted=True` reported. |
| All five tests use the one shape that works | Every shape the review names has a test, and each asserts that the `4792020` rule fails on that very curve (section 4). |
| 1 degree dead-band untested | The dead-band (now 5 degrees and 25% of range) and both correlation thresholds are pinned from both sides. |
| No caller carries the flags into the batch output | Ten columns in the batch row and CSV, and a polarity block in the printed summary. |
| Raw `rmse_deg`, bias and LoA in a mirrored frame; wrong for a rigid-body reference | A reference is mirrored only when declared arbitrary. The batch declares only a loader-declared relative curve arbitrary. A mirrored reference is mirrored about its own mean, not negated; the offset still moves (section 2). |
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
| Plateau, and correlation alone under a pinned lag (shipped at `a5a5cf4`) | 122 | 23 | 0 |
| **Plateau, and correlation alone only to confirm "as given" under a verified pin (shipped now)** | **121** | **24** | **0** |

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

So at `a5a5cf4` the correlation decided alone only when the lag was "pinned": by the capture prior (a 0.15 s refinement window) or a manual `lag_override_sec`.
Under a free lag it can still confirm the release sides or contradict them.

The cost on the IMU corpus is four pairs, all with an occluded reference hold. An occluded hold also shortens the optical recording, so the duration-based capture prior declines and the lag is free.
They are listed in section 5.

### Why the correlation may only confirm, and only under a verified pin

The second review showed the "pinned" premise false in the very case the path was written for.
The capture prior is the difference of the two recordings' durations, and `compare_pair` measures those after dropping non-finite samples.
An optical take whose first g seconds are occluded is therefore g seconds short, and the prior moves by g.
The same occlusion removes the reference's release side, so the correlation was deciding alone on a search pinned to the wrong place, where a mirrored swing fits.

The path is now closed on three sides.

1. **It never mirrors.** Opposition on the correlation alone is `undetermined` (`correlation_alone_never_mirrors`). So the correlation alone cannot move a reported number.
2. **It confirms "as given" only under a verified pin.** The capture prior must have applied, and the two curves together may have at most 0.15 s of their own time base missing before the first and after the last finite sample. That tolerance is the prior's refine margin: a shift the search can still absorb. A manual lag does not count, because an operator sets one by matching the curves.
3. **The reference must have moved.** At least 10 degrees between its 3rd and 97th percentile over the scored overlap, and at least 2 s of overlap. Pearson's r has no scale, and the corpus has hold-only overlaps with a reference range of 1 and 6 degrees.

The prior itself is not corrected. That would change the alignment and so every default-path number; this change keeps the default path identical to main.
The second review measured that correcting it would decide five of the six pairs a reader can decide, and would let in a wrong decision on a reference that barely moved. It is left as future work (section 8).

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

| Release sides (both curves) | Correlation | Result | `orientation_basis` or reason |
|---|---|---|---|
| both known | agrees | decided | `shape+correlation` |
| both known | says nothing | decided by the sides | `shape` |
| both known | contradicts | **undetermined** | `shape_correlation_conflict` |
| one or both unknown | together, verified pin, reference moved | as given | `correlation` |
| one or both unknown | together, no capture prior | **undetermined** | `correlation_alone_with_unpinned_lag (...)` |
| one or both unknown | together, manual lag | **undetermined** | `correlation_alone_with_a_manual_lag (...)` |
| one or both unknown | together, prior applied but a take has an end gap | **undetermined** | `correlation_alone_with_unverified_pin (...)` |
| one or both unknown | together, under 2 s scored | **undetermined** | `correlation_alone_on_too_short_an_overlap (...)` |
| one or both unknown | together, reference range under 10 degrees | **undetermined** | `correlation_alone_on_a_reference_that_barely_moved (...)` |
| one or both unknown | in opposition | **undetermined** | `correlation_alone_never_mirrors (...)` |
| one or both unknown | inside the margin | **undetermined** | `correlation_inside_margin (...)` |
| one or both unknown | not defined (a flat curve, under 4 samples) | **undetermined** | `correlation_undefined (...)` |

### What "undetermined" does

The comparison still runs, on the curves exactly as given, and returns `orientation="undetermined"` with the reason and both correlations.
It never falls back to the better-fitting orientation.
The batch keeps such rows in the all-pairs figures, which are the headline, and prints the decided-pairs figure only beside the undetermined pairs' own (section 5, "What the batch prints").
`compare_pair` also returns `rmse_deg_offset_removed_other_polarity`, what the pair would have scored the other way up. It exists to bound a summary and is never used to decide.

### What is mirrored, and when

- **Only the reference, never the test curve.** For RMSE only the relative polarity matters. An upside-down test curve may be a real defect in the model path; it is reported (`test_inverted=True`) and left alone.
- **Only when the caller says the reference's sign is arbitrary.** The default is off. A rigid-body reference, a model trace, or anything with a real zero is never mirrored. When the evidence says such a pair is in opposition, the result is `orientation="opposite_not_mirrored"` and the numbers are for the curves as given.
- **The batch says so only on the loader's word.** `load_optitrack_reference` returns `relative=True` when the optical loader attached its own "these angles are RELATIVE" declaration, which only the labelled-marker knee-axis path does. The `"marker_pca"` tag is not used for this: it also covers a precomputed-angle file and the quaternion fallback, which have a real sign. All 145 corpus references are the labelled-marker path, so no corpus number depends on the difference.
- **About the mean of the reference's active window, not by negation.** The offset between the curves is arbitrary before and after, and it does move: on the 55 mirrored corpus pairs `bias_deg` changes by a median 1.7 degrees and by up to 44.8, raw `rmse_deg` falls by a median 5.9, and the limits of agreement narrow (the second review's measurement). None of `rmse_deg`, `mae_deg`, `bias_deg` or the limits means anything against a relative reference either way. A reader diffing those columns between a CSV from main and one from this branch will see 55 rows move, and `ref_mirrored=True` is the only label for it.

One limit follows from the first point. Against a reference whose sign is arbitrary, the RMSE cannot see an inverted test curve at all: the reference is mirrored to match it.
`test_inverted` is then the only signal, and it is `None` when the test curve has no release side of its own.

### What `compare_pair` returns now

`orientation` (`as_given`, `ref_mirrored`, `opposite_not_mirrored`, `undetermined`), `orientation_basis`, `orientation_reason`, `ref_mirrored`,
`ref_inverted` and `test_inverted` (each `True`, `False`, or `None` when that curve cannot say), `polarity_corr_as_given`, `polarity_corr_mirrored`, `rmse_deg_offset_removed_other_polarity`.
All existing keys are unchanged. With the default arguments every existing number is unchanged (measured on the revised code against `origin/main`'s engine: every pre-existing key identical on 145 of 145 pairs under each of the three alignment branches, capture prior, free search and a manual lag of -0.7; maximum difference 0.0).

Two labels were corrected after the second review:

- A pair whose correlation is not defined is `correlation_undefined`, not `correlation_inside_margin`.
- A pair that is opposed, declared arbitrary, and whose mirrored alignment fails is `undetermined` (`mirrored_alignment_failed`). It used to fall through to `opposite_not_mirrored`, which means "not declared arbitrary". Both alignments fail or succeed together in practice; the case is reached in a test by forcing the second alignment to fail.

`ref_inverted` and `test_inverted` are each curve's reading of its own release side, not a finding that the curve is wrong.
One shape is known to read inverted when it is not: after rest, lift and hold, a limb that sinks back slowly without swinging (time constant 1.5 s in the synthetic). `_detect_release` takes the lift for the release there, and the plateau before it is the rest position. Both curves of a pair read it the same way, so the pair's relative polarity is still right (pinned by a test). Whether any corpus trial has this shape was not measured.

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
| `_POLARITY_MIN_REF_RANGE_DEG` | 10 deg | added after the second review; twice the 5 degree dead-band |
| `_POLARITY_MIN_OVERLAP_S` | 2.0 s | added after the second review; two cycles at about 1 Hz |
| end-gap tolerance | 0.15 s | the capture prior's own refine margin, reused |

All of the first eight were set before the first corpus run, with two changes after it. Both are stated here because a rule adjusted on its own test corpus deserves the suspicion.
The last three came with the second review. They were chosen from the argument in the table, not by trying values on the corpus: on these 145 pairs neither floor changes any decision between 0 and 20 degrees or between 0 and 4 s (section 5, sensitivity), so the corpus could not have tuned them. It also cannot validate them. Their evidence is the synthetic and blanked-hold experiments in section 0.

1. **Hold fill.** The first version required 0.3 s worth of samples in the hold window. Seven corpus references have a clean hold thinned evenly by dropouts (33 to 35 of 60 samples across the full half-second) and were refused. The requirement became "half the window's sample slots". Those seven pairs moved from `correlation` to `shape+correlation`. No final decision changed.
2. **Pinned lag for correlation-only decisions.** Described above. It was prompted by the MediaPipe run, not by the IMU corpus, and it makes the rule decide fewer IMU pairs, not more.

## 3. Why the decision is not the metric optimising itself

Stated plainly first: **at a fixed lag, the sign of the correlation and "which orientation has the lower offset-removed RMSE" are the same decision.**
Offset-removed RMSE squared is `var(ref) + var(test) - 2 r sd(ref) sd(test)`, and mirroring the reference only changes the sign of `r`.
So the correlation is not independent of the reported error. What keeps the reported error from choosing itself is the following.

1. **No mirroring can rest on the correlation alone, by construction.** Since the second review this is a property of the code, not of the corpus: opposition on the correlation alone is undetermined. All 55 mirrored corpus pairs have a release-side verdict from both curves, each made without looking at the other curve.
2. **The 12 correlation-only decisions are all "as given", as they must be.** Their reported number is main's number. The correlation only moves pairs from "undetermined" to "decided", which changes the decided-subset median and nothing else.
3. **Where both kinds of evidence exist, they were compared (measured).** Of the 109 pairs with both release sides, the correlation agrees on 106, says nothing on 3 and contradicts on 0. On the 17 MediaPipe pairs with both release sides: 16, 1 and 0.
4. **The correlation cannot override the release sides.** A contradiction makes the pair undetermined. It does not pick the better fit.
5. **Undetermined pairs are scored as given, not at the better orientation.** The lower of two numbers is never selected. The row for that would be 13.86 (section 5), and the rule reports 14.67.
6. **Under a free lag, a manual lag, or a pin moved by a gap, the correlation may not decide at all**, because that is where it was measured to fail.

The second review checked the decisions against a judge that uses neither the IMU curve nor the fit: the marker geometry. It speaks on 114 of the decided pairs at `a5a5cf4` and agrees with the rule on all 114. That is the reviewer's measurement, not this work's. The one decision the revision withdrew (`4_right_pre_T4`) was among the eight the judge was silent on.

Two caveats remain.

- The decided subset is not a random sample. Leaving out the undetermined pairs removes mostly poor references and halves participant 15's weight (24 of 145 pairs, 10 of 121), so the subset's median is lower for those reasons as well as for orientation. With participant 15 left out altogether the all-pairs median is 12.66 (121 pairs). The like-for-like comparison is on the same 121 decided pairs: 24.48 on main, 14.04 under `4792020`, 14.04 here.
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
| Dead-band in degrees | `..._dead_band_in_degrees` | n/a | 4.8 no, 5.2 yes |
| Margin against range | `..._margin_relative_to_range` | n/a | 0.18 of range no, 0.29 yes |
| Correlation thresholds | `test_polarity_from_correlation_margin_boundaries` | n/a | in numbers, both sides |
| Decision table | `test_decide_relative_polarity_table` | n/a | every row, blocked and not |
| Upside-down relative reference | `test_compare_pair_mirrors_an_upside_down_relative_reference` | right | mirrored, RMSE under 0.5 |
| Same sense | `..._leaves_a_pair_of_the_same_sense_alone` | right | every number identical to default |
| Test curve is the inverted one | `..._when_it_is_the_test_curve_that_is_inverted` | right | reference mirrored, `test_inverted=True`; not mirrored by default |
| Take that starts at the release, capture prior, upright and mirrored | `..._with_a_take_that_starts_at_the_release` | flips the upright one (asserted) | upright: as given by correlation. Mirrored: undetermined, scored as given |
| Same pair, free lag, then a manual lag | `test_correlation_alone_does_not_decide_when_the_lag_is_not_pinned` | n/a | undetermined both times |
| Mirror fits half a cycle away | `..._abstains_when_the_mirror_fits_half_a_cycle_away` | n/a | undetermined, scored as given |
| Noisy low-amplitude reference | `..._abstains_on_a_noisy_low_amplitude_reference` | n/a | undetermined, scored as given |
| Release sides contradict correlation | `..._abstains_when_shape_and_correlation_contradict` | n/a | undetermined |
| Default never mirrors | `..._never_mirrors_unless_the_reference_is_declared_arbitrary` | n/a | `opposite_not_mirrored` |
| NaN gap in the reference | `..._polarity_survives_nan_gaps_in_the_reference` | n/a | mirrored |

Batch tests (`tests/test_batch_imu_vs_optitrack_rmse.py`): the row and CSV carry the fields; only a loader-declared relative reference is declared arbitrary (not a rigid-body one, and not a `marker_pca`-tagged curve the loader did not call relative); error rows leave the fields empty; the stratified dict breaks out undetermined pairs; the polarity summary's counts, medians, bounds, per-participant losses, by-basis lines and inverted-test-curve count; and `main()` prints that block and no bare "excluding" line.

### Added after the second review

The second review's occlusion cases, through the public function:

| Case | Test | Result required |
|---|---|---|
| Same-sense pair, take started on time, hold occluded 0, 0.1, 0.2 s past the release, at 0.8, 0.9, 1.0, 1.1 Hz (12 cases; `a5a5cf4` mirrored these) | `test_an_occluded_hold_in_a_normally_started_take_is_never_mirrored` | undetermined, scored as given; the test first asserts that the mirror does fit and that the lag is over 0.3 s from the truth |
| The same take with the reference upside down (the as-given fit is the half-cycle alias, r 0.99, RMSE under 3) | `test_an_occluded_hold_does_not_let_the_correlation_confirm_a_pair` | undetermined, `..._with_unverified_pin` |
| The fixture with the hold seen | `test_rig_take_fixture_is_decided_when_the_hold_is_seen` | decided both ways at the true lag |
| 0.2 s and 0.1 s missing from the end of either curve | `test_a_gap_at_the_end_of_either_take_unverifies_the_pin` | 0.2: undetermined. 0.1: confirmed |
| Reference swinging 7 and 11 degrees end to end | `test_correlation_alone_needs_a_reference_that_moved` | 7: undetermined. 11: confirmed |
| Floors in numbers | `test_correlation_alone_floors_in_degrees_and_seconds`, `test_end_gap_tolerance_is_the_refine_margin` | both sides of 10 degrees, 2 s and 0.15 s |
| Manual lag with both release sides present | `test_a_manual_lag_does_not_stop_the_release_sides_deciding` | still mirrored on `shape+correlation` |

One test per release-side criterion, each on a curve where that criterion is what decides:

| Criterion | Test | Curve |
|---|---|---|
| Hold cover (0.3 s) | `test_release_side_needs_the_hold_window_covered_in_time` | markers back for the last 0.27 s (no) and 0.36 s (yes) |
| Hold fill (half) | `..._needs_the_hold_window_half_filled`, `..._accepts_a_hold_thinned_evenly_by_dropouts` | one sample in three (no); three in five (yes) |
| Plateau limit (0.16) | `..._does_not_call_a_wobbling_hold_a_plateau` | hold wobbling 8 degrees either way (no) and 3 (yes) |
| Hold window (0.5 s) | `..._reads_the_half_second_before_release_not_the_second` | rest, lift, 0.6 s hold |
| Settled level at the end of oscillation | `..._settles_where_the_swing_ends_not_where_the_file_ends` | a minute of 0.3 deg/s drift after a 10 degree release; a leg picked up 8 s after release |
| Rest half-window (1 s) | `..._settled_level_averages_a_swing_cycle` | lightly damped limb still swinging when the record stops |
| Arrested-limb fallback | `..._of_an_arrested_limb_settles_at_the_end_of_the_record` | rest, lift, hold, sinks back without a swing |
| Rest read after the release only | `..._rest_is_read_after_the_release_only` | record stops 0.4 s after the release |
| Range by percentile | `..._range_ignores_a_spike` | four samples at -150 on a 20 degree release |
| Sample floor (40) | `..._sample_floor_is_forty` | 195 samples (yes), 39 (no) |
| Pearson's 4-sample floor | `test_pearson_r_needs_four_samples` | three points |

### Mutations (measured)

The second review ran 53 mutations against the tests at `a5a5cf4`; 21 survived. The same list was re-run here against the revised tests, with the lines the revision rewrote re-expressed (the three "pinned" mutations, the mirroring branch, the batch's declaration and the count line), plus 32 mutations of the code added in this revision. Each is applied in memory to `workbench_engine` or the batch module and the two test files are run.

| | Mutations | Killed | Survive |
|---|---|---|---|
| The second review's list | 53 | 53 | 0 |
| of which survived at `a5a5cf4` | 21 | 21 | 0 |
| New code in this revision (correlation-alone guards, end gap, floors, labels, loader flag, batch summary) | 32 | 32 | 0 |

With no mutation applied the two files pass (the control run). A mutation counts as killed when at least one test fails under it.
The first run of this list against the revised tests left one of the new mutations alive (the batch counting `test_inverted=None` as inverted); a fixture row was added for it.
Limits of this measure: the mutations are a hand-written list, not generated, and "killed" says a test noticed, not that the test is the right one. Several of the constants are still pinned through private helpers (`_polarity_from_correlation`, `_correlation_alone_block`, `_pearson_r`), in numbers.

## 5. Corpus evidence

### Method

The 145 pairs are the reviewer's: the cached IMU and optical curves behind `q23_rows_ext.json`.
For each pair `compare_pair(..., capture_skew_prior=True)` was run four ways with this branch's engine: curves as given; reference negated; both curves oriented by the `4792020` rule; and `ref_polarity_arbitrary=True`.
The first three reproduce the reviewer's numbers on all 145 (maximum difference 0.0).

The real batch was then run end to end from raw files with this branch's code (`batch_imu_vs_optitrack_rmse.main`, with the study checkout's `imu_calibration_config.json` and registries).
At `a5a5cf4` it gave 145 ok rows, all `marker_pca`, with the same decision and the same RMSE as the cached-curve run on every one (maximum difference 0.0).
It was run again on the revised code: 179 anchors discovered, 20 skipped for want of an OptiTrack file, 14 errors, 145 evaluated; 66 as given, 55 mirrored, 24 undetermined; every one of the 145 references is declared relative by the loader; orientation and offset-removed RMSE equal the cached-curve run on 145 of 145 pairs (0 differing).
(A first end-to-end attempt ran without the calibration config, which the worktree does not have. Its numbers are not used.)

### Medians (measured)

| | median | mean | pairs |
|---|---|---|---|
| main, as given | 21.80 | 24.69 | 145 |
| `4792020` | 15.10 | 14.87 | 145 |
| **this rule, all pairs** | **14.67** | 14.31 | 145 |
| bound: undetermined pairs at their better fit / worse fit | 13.86 / 15.30 | | 145 |
| subset: decided pairs | 14.04 | 13.79 | 121 |
| subset: undetermined pairs, scored as given | 16.66 | 16.95 | 24 |
| lower of the two orientations on every pair (not a result: what a self-optimising rule would report) | 13.86 | | 145 |

The better-fit bound equals the last row because no decided pair is decided against the lower RMSE.
Under the 5 degree goal: 18 of 145; 14 of the 121 decided; 4 of the 24 undetermined. Three of those four are hold-only overlaps (`14_right_pre_T1`, `20_right_pre_T1`, `20_right_pre_T3`) and the fourth is `4_right_pre_T4`, whose 2.6 is one cycle late (the second review's measurement: 6.4 at the lag the take's time base gives).

By basis of decision:

| | Pairs | Median |
|---|---|---|
| Both release sides and the correlation | 106 | 12.69 |
| Release sides alone | 3 | 27.33 |
| Correlation alone (all "as given") | 12 | 21.44 |

### Decisions (measured)

| Decision | Pairs | Basis |
|---|---|---|
| as given | 66 | 51 shape+correlation, 3 shape, 12 correlation |
| reference mirrored | 55 | 55 shape+correlation |
| undetermined | 24 | 18 correlation inside the margin, 3 correlation alone under a free lag, 1 correlation alone would mirror, 1 pin unverified, 1 correlation undefined; no conflicts |

`4792020` flipped 67 references and no test curve.
None of the 121 decided pairs is decided against the lower-RMSE orientation, so no decided pair had to be adjudicated against the fit.
The capture prior applied on 126 of the 145 pairs; all 12 correlation-only decisions are among them, each with under 0.04 s of end gap.
18 pairs have more than 0.15 s missing at the ends of a take. On 17 of them the prior had already declined; `4_right_pre_T4` is the one where it applied.
Of the 19 pairs with a free lag, 2 are decided (both release sides, the correlation agreeing) and 17 are undetermined: a reference too poor to have a release side is usually too poor to have a plausible duration as well.

### What the batch prints

The summary block for this corpus, built by `format_orientation_summary` from the cached-curve rows (the participant IDs are the trial keys' own prefixes):

```
Pair polarity (RMSE in deg, offset removed where the reference has no zero):
  decided 121 / undetermined 24 / total 145   (as_given=66, ref_mirrored=55, undetermined=24)
  ALL PAIRS (headline)      n=145  median= 14.67  mean= 14.31  18/145 under 5 deg
  bound on the all-pairs median, undetermined pairs at their better / worse fit: 13.86 .. 15.30
  decided pairs (a SUBSET)  n=121  median= 14.04  mean= 13.79  14/121 under 5 deg
  undetermined pairs        n= 24  median= 16.66  mean= 16.95  4/24 under 5 deg   <- scored as given; quote the subset only with this line
  decided on correlation       n= 12  median= 21.44  mean= 19.71  0/12 under 5 deg
  decided on shape             n=  3  median= 27.33  mean= 19.17  1/3 under 5 deg
  decided on shape+correlation n=106  median= 12.69  mean= 12.97  13/106 under 5 deg
  undetermined by participant (of that participant's pairs): 4: 1/16, 13: 1/25, 14: 2/8, 15: 14/24*, 17: 2/24, 20: 2/3*, 21: 1/9, 22: 1/4
    * over half of this participant's pairs are undetermined: 15, 20
  undetermined by reason: correlation_alone_never_mirrors=1, correlation_alone_with_unpinned_lag=3, correlation_alone_with_unverified_pin=1, correlation_inside_margin=18, correlation_undefined=1
  test_inverted=True on 2 pair(s): 15_right_post_1week_T3, 4_right_june_post_T1   <- the IMU curve reads upside down on its own release side: check it against the markers. Not corrected, and invisible to the RMSE
```

Undetermined pairs by participant, of that participant's pairs: 4: 1 of 16; 13: 1 of 25; 14: 2 of 8; 15: 14 of 24; 17: 2 of 24; 20: 2 of 3; 21: 1 of 9; 22: 1 of 4. Five participants have none.

Release sides, per curve: the reference has one on 112 of 145 (57 down, 55 up); the IMU curve on 141 (139 up, 2 down).

### The review's 17

The 17 are the `4792020` decisions that disagree with the better-fitting orientation (reproduced: the same 17 keys).

| Outcome | Count | Trials |
|---|---|---|
| Resolved (decided, and the better-fitting way) | 0 | (`4_right_pre_T4` was decided at `a5a5cf4`, on a pin 1.13 s off; it is undetermined now) |
| Undetermined | 17 | `4_right_pre_T4`, `13_left_post_T4`, `14_right_pre_T1`, `15_left_post_T1`, `15_left_post_T3`, `15_left_post_T4`, `15_left_post_T5`, `15_right_post_1month_T1`, `15_right_post_1month_T4`, `15_right_post_1week_T1`, `15_right_post_1week_T2`, `15_right_post_1week_T3`, `15_right_post_T1`, `15_right_post_T2`, `17_left_post_1_month_T5`, `20_right_pre_T1`, `20_right_pre_T3` |
| Still decided the other way | 0 | |

### The nine trials whose RMSE rose under `4792020`

| Trial | main | `4792020` | this rule | Decision | Why |
|---|---|---|---|---|---|
| `17_left_post_1_month_T5` | 7.3 | 26.1 | 7.3 | undetermined | Reference hold occluded; lag free. r 0.95 against 0.36. By eye as given is right: it overlays cycle for cycle at a lag of -0.75 s, the usual capture skew, and the mirror fits half a cycle later. |
| `4_right_pre_T4` | 2.6 | 10.0 | 2.6 | undetermined | Reference hold occluded: a 1.13 s leading gap. The capture prior still applied, 1.13 s off (-1.92 where the take's time base gives -0.79), so the pin is not verified. r 0.92 against -0.25 at that lag. The 2.6 is one cycle late. |
| `15_right_post_1week_T1` | 10.3 | 28.7 | 10.3 | undetermined | Reference hold occluded; lag free. r 0.84 against 0.44. By eye as given is right. |
| `15_right_post_1month_T1` | 9.7 | 14.4 | 9.7 | undetermined | Reference starts at the first trough. r 0.74 against 0.71: the mirror fits half a cycle away. By eye as given is right. |
| `15_right_post_T1` | 38.5 | 56.2 | 38.5 | undetermined | Reference hold 7 deg from rest on a 145 deg range; narrow spikes. r 0.42. |
| `15_right_post_T2` | 32.3 | 47.5 | 32.3 | undetermined | Same shape. Hold 3 deg from rest. r 0.45. |
| `15_right_post_1month_T4` | 14.2 | 21.9 | 14.2 | undetermined | Reference range 12 deg against the IMU's 79. r 0.54 against 0.49. |
| `15_right_post_1week_T3` | 22.8 | 27.0 | 22.8 | undetermined | Reference is mostly gaps with no plateau. `test_inverted=True` is reported, and it is right: the markers show a raised hold and the IMU curve goes 60 degrees below rest (second review). r 0.43. |
| `13_left_post_T4` | 13.4 | 15.4 | 13.4 | undetermined | Reference release side is clear (down). The IMU curve has no pre-release plateau. r 0.55 against 0.36. |

All nine are scored as given, which is the better-fitting orientation for each. The rule does not claim to know that for any of them.
For `13_left_post_T4` the second review's marker judge says the better fit is the wrong one: mirrored is right and scores 15.4.

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
The first is a real loss: a pair that is decidable by eye, left undetermined and scored the wrong way up.
It is not the only pair a reader can decide. The second review, reading all the undetermined curves against the marker arc, found six: `15_right_post_1month_T1`, `15_right_post_1week_T1`, `15_right_post_1week_T4` and `17_left_post_1_month_T5` (as given, which is how they are scored), and `17_right_post_T4` and `13_left_post_T4` (mirrored, which is not).
So two or three undetermined pairs are scored at an orientation the eye says is wrong: those two, and `15_left_post_T3`, which the markers alone decide as mirrored. This report's first version said one.

### The 24 undetermined pairs, by reason

| Class | Count | Trials |
|---|---|---|
| Reference has no pre-release plateau; correlation inside the margin | 10 | `15_left_post_T1`, `15_left_post_T3`, `15_left_post_T4`, `15_left_post_T5`, `15_right_post_1month_T1`, `15_right_post_1month_T3`, `15_right_post_1month_T4`, `15_right_post_1week_T2`, `15_right_post_1week_T3`, `20_right_pre_T3` |
| Reference plateau not clear of rest; correlation inside the margin | 7 | `14_left_pre_T4`, `14_right_pre_T1`, `15_right_post_T1`, `15_right_post_T2`, `15_right_post_T3`, `21_right_pre_T4`, `22_right_pre_T3` |
| Reference plateau not clear of rest; correlation undefined (6 scored samples) | 1 | `20_right_pre_T1` |
| IMU curve has no pre-release plateau; correlation inside the margin | 1 | `13_left_post_T4` |
| Reference has no pre-release plateau; correlation says "together" but the lag is free | 3 | `15_right_post_1week_T1`, `15_right_post_1week_T4`, `17_left_post_1_month_T5` |
| Reference has no pre-release plateau; correlation says "opposed", and the correlation alone never mirrors (the lag is free as well) | 1 | `17_right_post_T4` |
| Reference has no pre-release plateau; correlation says "together" under a capture prior moved by a 1.13 s leading gap | 1 | `4_right_pre_T4` |

14 of the 24 are participant 15.

The 23 that were undetermined at `a5a5cf4` were plotted (raw curves, and the aligned overlay both ways); `4_right_pre_T4` was plotted then as a decided pair. What the curves show, by eye:

- **Reference unusable or mostly gaps:** `15_left_post_T1`, `T3`, `T4`, `T5` (square-edged switching, 14 to 27 deg range against 76 to 91); `15_right_post_1week_T2` and `T3` (long gaps).
- **Reference ends at or before the release, so the scored overlap is the hold only:** `14_right_pre_T1`, `20_right_pre_T1`, `20_right_pre_T3`. Their RMSE of 0.6 to 4.3 is not a swing comparison in either orientation.
- **Reference a fraction of the IMU's amplitude:** `14_left_pre_T4`, `15_right_post_1month_T4`, `21_right_pre_T4`, `22_right_pre_T3`.
- **Reference is narrow spikes:** `15_right_post_T1`, `T2`, `T3`. The spikes line up with the IMU's troughs as given.
- **Mirror fits half a cycle away:** `15_right_post_1month_T1`, `15_right_post_1month_T3`.
- **Poor IMU curve:** `13_left_post_T4`.
- **Decidable by eye, lag free:** `15_right_post_1week_T1`, `15_right_post_1week_T4`, `17_left_post_1_month_T5` (as given is right) and `17_right_post_T4` (mirrored is right).

The second review plotted the same pairs with the marker arc beside them and classes them differently in two respects, and its reading is the better informed one:

- `15_right_post_T1`, `T2`, `T3` are not a capture problem. The markers are tracked on 94 to 100% of frames and show a clean trial; it is the loader's curve that is flat through the hold with spikes.
- Ten of the 24 are loader or coverage failures with no orientation to find, and in several the loader's curve has a fraction of the marker arc's range. That is a reference gain problem, on main as well, and outside this change.

Eleven of the 24 have a lower RMSE with the reference negated: `17_right_post_T4`, and ten others (`14_right_pre_T1`, `15_left_post_T1`, `T3`, `T4`, `T5`, `15_right_post_1week_T2`, `20_right_pre_T1`, `20_right_pre_T3`, `21_right_pre_T4`, `22_right_pre_T3`).
In none of those ten could the negated orientation be confirmed by eye, and none has a usable reference.

### Decided pairs that were also plotted

13 pairs decided at `a5a5cf4` were plotted: the three shape-only decisions and 10 of the 13 correlation-only ones.
In all 13 the decided orientation is the one the curves show. (The second review plotted 36 decided pairs against the marker arc, 18 mirrored and 18 as given; in all 36 the relative polarity is the one the markers show.)

- Shape-only `4_left_pre_T4` (r 0.24): both curves are released from above. The low correlation is a lag failure, the optical drop sitting 2 s after the IMU's. Fit would have been uninformative here; the release sides are not.
- Shape-only `21_right_pre_T2`: the scored overlap is the hold only (RMSE 0.7). The decision is right; the number is not a swing comparison.
- `4_right_june_post_T1`, `14_left_pre_T2`, `23_left_pre_T1`: see the next subsection. This report's first version misread them.

### Four pairs where both curves are upside down (correction; open issue)

The first version of this report said of `4_right_june_post_T1`, `14_left_pre_T2` and `23_left_pre_T1`: "The leg is lowered, held for seconds, then raised and oscillates ... the per-curve flags describe the manoeuvre, not a defect." **That reading was wrong.** It was made from the two angle curves alone, which agree with each other.

The second review read the raw marker positions for these trials. They are ordinary trials that open at rest: the leg is raised 70 to 88 degrees, held for 2 to 6 s, released, and oscillates. The loader's optical curve shows that hold below rest, **and so does the IMU curve.** Other trials in the same sessions that open on the hold have the IMU hold above rest. `15_right_post_1week_T3` is a fourth: the markers show a raised hold and the IMU curve goes to 60 degrees below rest.

What follows, and what does not:

- **The pair's relative polarity is right** on the three decided ones (as given, r 0.95 to 0.97), so the decisions stand.
- **Their RMSE is agreement between two inverted curves:** 14.0, 5.3 and 17.5. It says the two curves match each other. It does not say the IMU curve is the right way up, and against a reference of arbitrary sign no RMSE can.
- **`test_inverted` catches two of the four** (`4_right_june_post_T1`, `15_right_post_1week_T3`) and is `None` on the other two, where `_detect_release` takes the lift for the release and the plateau before it is the rest position.
- All four recordings open at rest. 4 of 145 pairs.

**Open issue for the IMU pipeline's owner:** on recordings that open at rest, the IMU angle trace can come out inverted. Why, whether it is deterministic, and whether it is a defect or a convention were not investigated here, and nothing in the IMU pipeline was changed. PT parameters are mirror-invariant; an angle trace shown to a clinician is not. The batch summary now prints the `test_inverted=True` count and keys so the two that are detectable are not silent.

### Sensitivity (measured; one constant moved at a time, all 145 re-decided)

| Change | as given / mirrored / undetermined | Decided against the better fit |
|---|---|---|
| shipped | 66 / 55 / 24 | 0 |
| min correlation 0.3 or 0.4 | 70 / 55 / 20 | 0 |
| min correlation 0.6 | 62 / 55 / 28 | 0 |
| min correlation 0.7 | 59 / 55 / 31 | 0 |
| margin 0.1, 0.2 or 0.5 | 66 / 55 / 24 | 0 |
| hold margin 0.15, 0.20 or 0.35 of range | 66 / 55 / 24 | 0 |
| hold margin 0.50 of range | 66 / 47 / 32 | 0 |
| plateau limit 0.10 | 66 / 54 / 25 | 0 |
| plateau limit 0.25 or 0.40 | 67 / 55 / 23 | 0 |
| dead-band 2 deg | 66 / 55 / 24 | 0 |
| dead-band 10 deg | 66 / 53 / 26 | 0 |
| hold window 0.3 s | 63 / 0 / 82 | 0 |
| hold window 1.0 s | 66 / 53 / 26 | 0 |
| hold fill 0.3 | 66 / 55 / 24 | 0 |
| hold fill 0.6 | 66 / 48 / 31 | 0 |
| hold fill 0.8 | 64 / 33 / 48 | 0 |
| rest window 0.5 or 2.0 s | 66 / 55 / 24 | 0 |
| reference range floor 0 or 5 deg | 66 / 55 / 24 | 0 |
| reference range floor 20 deg | 64 / 55 / 26 | 0 |
| reference range floor 40 deg | 61 / 55 / 29 | 0 |
| overlap floor 0 or 1 s | 66 / 55 / 24 | 0 |
| overlap floor 4 s | 65 / 55 / 25 | 0 |
| overlap floor 8 s | 60 / 55 / 30 | 0 |
| correlation never consulted | 54 / 55 / 36 | 0 |

This table was re-run on the revised code, with the full `compare_pair` for every pair at every setting. No setting makes a decision against the better fit.

It reads differently from the table at `a5a5cf4` in one respect, and the difference is a consequence of the revision. Tightening a release-side criterion now costs mirrored decisions outright: hold fill 0.6 turns 7 mirrored pairs undetermined, 0.8 turns 22, a hold margin of half the range turns 8.
At `a5a5cf4` the same settings left the counts almost unchanged, because a pair that lost its release side was picked up and mirrored by the correlation alone. That path is closed.
So every mirroring now depends on both curves' release sides, and the hold-fill value of 0.5, which was changed after the first corpus run to admit holds thinned to 33 to 35 of 60 samples, carries more weight than it did. Moving it never produced a wrong decision here, only more abstentions.
A hold window of 0.3 s is a different rule, not a nearby one: the window is then no longer than the 0.3 s cover requirement, almost no curve has a release side, and with the correlation unable to mirror nothing is mirrored at all.

The margin moves nothing here because under a pinned lag the two correlations are near negatives of each other. It matters under a free lag, where it now only confirms or contradicts.
Before the pinned-lag restriction, a minimum correlation of 0.3 or 0.4 or a margin of 0.1 each produced one decision against the better fit, on an unusable reference.

The second review went further on the code at `a5a5cf4` (its measurements): all nine constants perturbed jointly, 1000 configurations within 25% and 3000 within 50%, and leave-one-participant-out. No configuration made a decision against its marker judge; what moves is the boundary between decided and undetermined, never the direction. That was not repeated on the revised code.

## 6. Consumers of `compare_pair`

With the default `ref_polarity_arbitrary=False` nothing is mirrored and every pre-existing key is unchanged. So no consumer's numbers move unless it opts in.
Each call also pays for a second alignment and two release-side reads (not timed).

| Consumer | Reference | Reads | Changes? | Correct for its reference? |
|---|---|---|---|---|
| `batch_imu_vs_optitrack_rmse.evaluate_trial` | `load_optitrack_reference` (method and the loader's "relative" declaration) | all keys | **Yes**: opts in only for a curve the loader calls relative | Yes. All 145 ok pairs are the labelled-marker path. A rigid-body reference, a precomputed-angle file and the quaternion fallback are never mirrored (unit-tested, the precomputed-angle case on a real file through the real loaders; none in the corpus). `optical_absolute_zero` still uses the method tag, as on main. |
| `rmse_pipeline_common.score_imu_candidate` | `pt_score.load_optitrack` | raw `rmse_deg` only | No | Unchanged, and not correct today either: see below. |
| `rmse_pipeline_common.score_mediapipe_candidate` and `sweep_mediapipe_config.score_frames` | same | raw `rmse_deg` | No | Same. Must not opt in as things stand: see the MediaPipe run. |
| `sweep_imu_config.score_config` | `pt.load_optitrack` | raw `rmse_deg` | No | Same as `score_imu_candidate`. |
| `sweep_mediapipe_preprocessing.score_candidate` | passed in | raw `rmse_deg` | No | Same as the MediaPipe sweep. |
| `pt_report_common._lag_align_candidate` (line 1594) | the trial's own optical curve | `lag_sec`, raw `rmse_deg` | No | The lag is polarity-dependent: against an upside-down reference the search peaks on the wrong half-cycle. Not changed here. |
| `pendulastic_workbench.get_metrics_snapshot` | any visible trace the user picks | `rmse_deg`, `mae_deg`, `lag_sec` | No. The new keys appear in the snapshot and session export; the CSV export has a fixed field list. | Correct not to mirror: the reference may be a rigid-body curve or a model trace. |
| `generate_paper_figures.py:198` | optical | `lag_sec` only | No | Same lag caveat as `pt_report_common`. |
| `compare_2d_vs_world_angle.py`, `evaluate_*.py`, `prototype_opensim_ik.py`, `sweep_p16_mask_assessor.py` | optical | raw `rmse_deg` | No | Not examined beyond the call site. |

The second review measured what leaving the other consumers alone costs (its numbers, on the 55 pairs whose reference is upside down): `pt_report_common._lag_align_candidate`'s free-search lag differs from the oriented call's by a median 1.72 s, and with the capture prior the two lags differ by more than 0.2 s on 14 of the 55. Those consumers are no worse than on main and are known wrong on main. They need an owner and an issue; they are not changed here.

Three things found while reading (none changed in this branch):

- **`rmse_pipeline_common.py` writes no per-trial row.** `score_imu_candidate` returns one float and the sweep CSV holds per-candidate medians, so there was no row there to carry the flags into.
- **The sweep cache will miss.** `workbench_engine` is in `rmse_pipeline_common._FINGERPRINTED_MODULES`, so the next full sweep re-scores every candidate. The landmark cache has its own, narrower fingerprint and survives.
- **The sweeps read raw `rmse_deg` against a relative reference.** That number is dominated by the missing zero whichever way up the reference is. The MediaPipe run below measures it: the median raw `rmse_deg` is 141.3 where the offset-removed median is 18.9.

### MediaPipe versus OptiTrack (measured)

This run is from `a5a5cf4` and was not repeated. The revision cannot add a MediaPipe decision: it only removes correlation-alone decisions, and this run had none (no capture prior, no manual lag).

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
| Undetermined: correlation alone under a free lag (at `a5a5cf4`; the run was not repeated, and the revised rule would name some of these "never mirrors") | 54 |
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
| `tests/test_workbench_engine.py` | 160 passed (91 on main; 117 at `a5a5cf4`) |
| `tests/test_batch_imu_vs_optitrack_rmse.py` | 31 passed (20 on main; 24 at `a5a5cf4`). Includes two real-recording tests, which ran. |
| `tests/test_rmse_pipeline_common.py` | 95 passed |
| `tests/test_rmse_compare_floor.py` | 7 passed |
| `tests/test_pendulastic_workbench.py` (not in CI; uses `compare_pair`) | 65 passed |
| `tests/test_web_export_contract.py` (CI step) | 5 passed |
| CI python file list (`tests/test_pt_score.py` through `neuromech/tests`) | 625 passed, 28 skipped, 3 xfailed (same counts as at a5a5cf4) |
| `node neuromech/labview/verify_formula_node.js` (CI step) | PASS on all vectors at `a5a5cf4`; not re-run, no file it reads changed |
| Rust jobs | not run; no Rust file changed |
| Mutations against the two test files | 85 of 85 killed (section 4) |
| Default path against `origin/main`'s engine | identical on 145 of 145 pairs, three alignment branches |
| Line endings | `git diff --stat` and `--ignore-cr-at-eol` agree; all files LF as on `origin/main` |

## 8. Not verified

- **Another corpus.** The thresholds are validated on these 145 pairs only: one lab, one phone mount, marker-path references. Zero wrong decisions here is not a claim about other data.
- **A rigid-body reference.** The corpus has none. That such a reference is never mirrored is unit-tested at the engine and at the batch call, not observed on real data.
- **Every decided pair by eye.** 36 of 145 pairs were plotted (all 23 undetermined and 13 decided). The other 109 decided pairs were checked against the lower-RMSE orientation and, for 106 of them, by the two kinds of evidence agreeing. None of those 106 was plotted here; the review plotted 16 of the pairs `4792020` improved.
- **A correlation-only mirroring** can no longer happen. A correlation-only confirmation happens on 12 corpus pairs, all under a capture prior with under 0.04 s of end gap; none of the 12 was checked against the markers by this work. The second review's judge was silent on six of them.
- **The two floors and the end-gap tolerance on real data.** No corpus pair is decided by the reference-range floor or the overlap floor, and one by the end-gap check. Their evidence is synthetic and blanked-hold curves.
- **A caller that pre-filters NaNs.** The end-gap check cannot see a gap that was cut off before the call. On the blanked-hold experiment run that way no decision was wrong (0 of 55 decided), but nothing prevents it in principle. Only the batch was checked to pass the full time base.
- **The second review's judge, joint perturbation and leave-one-participant-out** were not re-run on the revised code. They are quoted as that review's measurements on `a5a5cf4`.
- **The four inverted IMU curves.** That they are inverted is the second review's reading of the markers. This work did not read the markers and did not investigate the cause.
- **The slow arrested-limb shape** that reads inverted on both curves (section 2) is shown on a synthetic curve only. Whether any corpus trial has it was not measured.
- **The MediaPipe run** was not repeated on the revised code.
- **Why the 16 MediaPipe verdicts differ.** Five were plotted. The split between wrong-way MediaPipe traces and lag aliases was not measured.
- **The MediaPipe run uses stored CSVs.** They were not regenerated, and their provenance (model version, preprocessing) was not checked.
- **The sweeps.** `sweep_imu_config.py`, `sweep_mediapipe_config.py`, `sweep_mediapipe_preprocessing.py` and `rmse_pipeline_common.run_full_sweep` were read, not run.
- **The workbench UI.** Not launched. Its unit tests pass; the new keys in its snapshot were not looked at on screen.
- **`pt_report_common` reports.** Not regenerated. The lag caveat in section 6 is from reading, not from a measured before and after.
- **`--compare-floor`.** The paired floor gate was not re-run. A pair whose orientation verdict differs between its two passes would show up as a large paired delta; whether any does was not measured.
- **Run time.** Not timed here. The second review measured 155 default calls at 0.6 s on main and 1.3 s at `a5a5cf4`: every caller pays for the second alignment whether or not it opts in. Not changed.
- **Lag failures and hold-only overlaps.** `4_left_pre_T4` (optical drop 2 s after the IMU's) and the pairs whose scored overlap is the hold only (`14_right_pre_T1`, `20_right_pre_T1`, `20_right_pre_T3`, `21_right_pre_T2`) are status `ok` with numbers that are not swing comparisons. Seen, not fixed, and present on main.
- **A capture prior that survives an occluded hold.** The free-lag pairs lose the prior because the optical recording's finite span is shortened by the occlusion, and `4_right_pre_T4` keeps a wrong one. A prior read from the file's full time base would fix the alignment (the second review: it decides five of the six reader-decidable pairs, and moves `4_right_pre_T4` from 2.6 to 6.4). Not done: it changes main's default-path numbers, which this change keeps identical.
- **Orienting the reference at the loader from the marker geometry**, which the second review showed feasible on 12 of 13 participants, would give the reference a real sign and make pair evidence unnecessary. Not attempted.
- **`optical_absolute_zero` still uses the method tag**, so a precomputed-angle file is reported as having no absolute zero. On main; not changed. No corpus pair takes that path.
- **CI on GitHub.** The gates above are local.
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
| `4_right_pre_T4` | 2.6 | 10.0 | 10.0 (flip) | 2.6 | undetermined | - | +0.92 / -0.25 | ? / up |
| `4_right_pre_T5` | 32.0 | 12.0 | 12.0 (flip) | 12.0 | ref_mirrored | shape+correlation | -0.85 / +0.95 | down / up |
| `5_left_post_1month_T2` | 48.8 | 7.2 | 7.2 (flip) | 7.2 | ref_mirrored | shape+correlation | -0.84 / +0.98 | down / up |
| `5_left_post_1month_T3` | 37.8 | 5.7 | 5.7 (flip) | 5.7 | ref_mirrored | shape+correlation | -0.32 / +0.99 | down / up |
| `5_left_post_1month_T4` | 43.8 | 6.7 | 6.7 (flip) | 6.7 | ref_mirrored | shape+correlation | -0.74 / +0.98 | down / up |
| `5_left_post_1month_T5` | 50.7 | 7.8 | 7.8 (flip) | 7.8 | ref_mirrored | shape+correlation | -0.88 / +0.98 | down / up |
| `5_right_post_1month_T1` | 4.3 | 48.2 | 4.3 (keep) | 4.3 | as_given | shape+correlation | +0.99 / -0.85 | up / up |
| `5_right_post_1month_T2` | 5.9 | 55.6 | 5.9 (keep) | 5.9 | as_given | shape+correlation | +1.00 / -0.91 | up / up |
| `5_right_post_1month_T3` | 4.8 | 52.9 | 4.8 (keep) | 4.8 | as_given | shape+correlation | +0.99 / -0.90 | up / up |
| `5_right_post_1month_T4` | 4.5 | 49.2 | 4.5 (keep) | 4.5 | as_given | shape+correlation | +0.99 / -0.81 | up / up |
