# `compare_pair` orientation rework: independent review

**2026-11-11.** Independent review of branch `fix/compare-pair-orientation`, pinned at commit `a5a5cf4`
(three commits on `origin/main` at `e9a3a01`: `477acde` engine, `735f33e` batch, `a5a5cf4` report).
The author's report is `docs/reports/2026-11-11-compare-pair-orientation.md` on that branch.
The change it replaces was reviewed in `docs/reports/2026-11-11-scorer-ports-independent-review.md` on
`origin/docs/scorer-ports-review`. The reviewer did not write the change.

Trial keys are `<pid>_<leg>_<condition>_T<n>`. Numbers are per trial or counts of pairs.
RMSE is `rmse_deg_offset_removed` in degrees unless it says otherwise.
"Measured" is a number this review produced by running code. "By eye" and "by reading" are marked.

**Update, 2026-11-13.** The six required changes were applied at `5ec06a7` and re-checked by reproduction.
The verdict below is the one given at `a5a5cf4`; the current one is in "Re-review at `5ec06a7`" at the end of
this file: ready for the owner to consider merging.

## Verdict: approve with required changes

The rule's decisions on this corpus are right, and they are not a product of threshold tuning.
That was checked against a judge that uses neither the IMU curve nor the fit (section 2), and under joint
perturbation of all nine constants (section 1). The engine's default path is identical to main.

What has to change before merge is narrower than the rule itself:

- one decision path, exercised by no corpus pair, that mirrors a correctly oriented reference and reports a
  flattering number when the optical hold is occluded;
- how the summary reports the excluded pairs;
- one wrong reading in the author's report, which hides four physically inverted IMU curves;
- tests that do not pin several of the criteria.

### Required changes, in priority order

1. **Do not let the correlation decide a pair alone until its "pinned lag" premise holds (section 4).**
   The capture prior is read from the reference's finite span. An occluded optical hold shortens that span,
   so the prior moves by the occluded time and the "pinned" search is pinned to the wrong place.
   - Synthetic, same-polarity pair, optical hold of 0.5 s occluded: `orientation="ref_mirrored"`,
     `basis="correlation"`, RMSE 2.1 to 4.3, lag -1.15 to -1.35 against a true -0.7.
   - Real curves with the hold removed: 1 wrong mirroring in 12 correlation-only decisions
     (`23_left_pre_T2`, reported 8.1 where the true orientation scores 10.7).
   - Real corpus: `4_right_pre_T4` is "pinned" 1.13 s from where the take's own time base puts it, exactly
     its leading gap.
   - With the prior corrected, the path still decides references with a 1 degree range and gets one
     judged pair the wrong way (`14_right_pre_T1`).

   Either remove the path, or keep it only when (a) the prior is taken from the take's time base, or the
   pin is refused when the reference has an end gap longer than the refine margin, and (b) the reference
   moved (a range floor and a minimum scored overlap). At minimum it must not mirror.
   No corpus mirroring depends on it. Removing it entirely moves 13 "as given" pairs to undetermined and
   changes no reported per-trial number.
2. **Report the excluded pairs so "excluded" cannot be read as "agrees" (section 3).**
   The summary must always print: decided / undetermined / total; undetermined pairs per participant;
   the undetermined pairs' own median; and the bound on the all-pairs median if every undetermined pair
   were the other way up. The all-pairs figure is the headline. The "excluding" figure is never quoted
   alone. Flag rows with `test_inverted=True` in the same summary.
3. **Correct the report on `4_right_june_post_T1`, `14_left_pre_T2` and `23_left_pre_T1` (section 2).**
   They are not "the leg lowered, held, then raised". Markers show the leg raised and held as usual, and
   both the optical curve and the IMU curve are upside down. `15_right_post_1week_T3` is a fourth.
   The pair's relative polarity is right, so the decision stands, but 5.3, 14.0 and 17.5 are agreement
   between two inverted curves. All four recordings open at rest. Raise it with the IMU pipeline's owner.
4. **Add the missing tests (section 6).** 21 of 53 mutations survive. The ones that matter: the hold-fill
   criterion (the one changed after the first corpus run) and the hold-cover criterion can each be deleted;
   the plateau limit can be tripled; the settled level can be anchored on the end of the file instead of
   the end of oscillation, which is the property the report claims a test protects. Add a test with an
   occluded hold inside a normally started take; the existing occluded-hold test starts the take at the
   release instead, which is the one geometry where the prior is right.
5. **Declare the reference arbitrary from what the loader did, not from the method tag (section 5).**
   `method != "rigid_body"` is also true for a precomputed-angle file and for the quaternion fallback, both
   of which have a real sign. No corpus pair takes those paths. A `relative` flag from the loader would
   close it.
6. **Minor (section 5).** Two accidental whitespace edits on lines the change did not otherwise touch;
   `orientation_reason` says "inside margin" when the correlation is undefined; the docstring's "bias stays
   close" is not true on every pair.

### Not required for this change, but owed

- Consumers left on the unoriented path report numbers that are known to be wrong (section 7).
- Ten of the 23 undetermined pairs are loader or coverage failures rather than polarity questions,
  three of them on clean, fully tracked markers (section 3).
- The marker geometry carries the polarity directly (section 2). Orienting the reference at the loader
  would give it a real sign and remove the need for pair evidence. Shown feasible here on 12 of 13
  participants; not a validated method.

## What was done

A throwaway worktree at `a5a5cf4`. Every matched pair was reloaded from the raw files with the branch's
code (179 anchors, 159 matched, 145 ok, 10 error rows, 4 unreadable IMU components), not taken from the
earlier review's cache. Each pair was scored with main's engine, the branch's default call, the reference
negated, and the opted-in call. The raw marker columns were read separately for the judge in section 2.

The author's per-trial rows were then compared with these: same 145 keys, same decision on all 145,
maximum RMSE difference 0.0. Counts reproduced: 67 as given, 55 mirrored, 23 undetermined; bases 51
shape+correlation, 3 shape and 13 correlation for "as given", 55 shape+correlation for mirrored;
medians 21.80 (main), 14.67 (all), 13.95 (decided), 13.86 (lower of the two on every pair).

## 1. Is the result fitted to this corpus?

No, as far as this corpus can show. The decisions that are made do not sit near any threshold; what moves
under perturbation is the boundary between "decided" and "undetermined", never the direction.

### One constant at a time (measured)

Each of the nine constants was moved by -50%, -25%, +25% and +50% and all 145 pairs re-decided.
"Against truth" uses the judge of section 2; "against fit" is the lower-RMSE orientation.

| Constant (shipped) | x0.5 | x0.75 | x1.25 | x1.5 | against truth | against fit |
|---|---|---|---|---|---|---|
| shipped | 67 / 55 / 23 | | | | 0 | 0 |
| hold window (0.5 s) | 64 / 53 / 28 | same | same | same | 0 | 0 |
| hold cover (0.3 s) | same | same | same | same | 0 | 0 |
| hold fill (0.5) | same | same | same | 65 / 54 / 26 | 0 | 0 |
| plateau limit (0.16) | same | same | 68 / 55 / 22 | 68 / 55 / 22 | 0 | 0 |
| hold margin (0.25 of range) | same | same | same | same | 0 | 0 |
| dead-band (5 deg) | same | same | same | same | 0 | 0 |
| rest half-window (1.0 s) | same | same | same | same | 0 | 0 |
| minimum correlation (0.5) | 71 / 56 / 18 | 71 / 55 / 19 | 61 / 55 / 29 | 59 / 55 / 31 | 0 | 0 |
| correlation margin (0.3) | same | same | same | same | 0 | 0 |

Cells are as given / mirrored / undetermined. The hold window at 0.25 s is a different rule, not a nearby
one: the window is then shorter than the 0.3 s cover requirement, no curve has a release side, and all 117
decisions (64 as given, 53 mirrored) are the correlation's alone under a pinned lag. None is against the
judge. So on the corpus as recorded the correlation is reliable where the release sides already decide.
Section 4 is about where they do not.

### All nine at once (measured)

| Perturbation | Configurations | With a decision against truth | With a decision against fit | Undetermined, min / median / max | Pairs ever decided opposite to shipped |
|---|---|---|---|---|---|
| each constant uniform in +/-25% | 1000 | 0 | 0 | 18 / 23 / 30 | none |
| each constant uniform in +/-50% | 3000 | 0 | 2 | 16 / 23 / 42 | none |

The two "against fit" cases are both `13_left_post_T4` decided "mirrored". The judge says mirrored is
right there, and the fit is the one that is wrong (13.4 as given, 15.4 mirrored).

Pairs whose state changes in at least 10% of the +/-50% configurations: 19, all moving between decided
and undetermined. The most mobile are `14_left_pre_T4` and `21_left_pre_T1` (47% each),
`15_right_post_T3` (44%), `4_left_june_post_T2` (43%) and `15_right_post_T2` (39%).
Those five are correlation-only decisions or undetermined pairs with a poor reference.

### Leave one participant out (measured)

For each of the 13 participants: keep the configurations (of the 3000) that make no error on the other 12,
then score those on the held-out participant.

| Judge used to choose and to score | Participants with any error under the most-deciding surviving configuration | Surviving configurations with any held-out error |
|---|---|---|
| geometric judge | 0 of 13 | 0 of 3000, for every participant |
| better fit | 0 of 13 | 2 of 3000, participant 13 only (`13_left_post_T4`, see above) |

The most-deciding surviving configuration decides more than the shipped one on participants 14 (7 against
6), 15 (13 against 10) and 21 (9 against 8), with no error.

### What this does and does not show

- The seven release-side thresholds and two correlation thresholds are not on a knife edge here.
- It shows nothing about thresholds that were not perturbed: `_detect_release`'s own, the capture prior's
  plausible band and refine margin, the 40-sample floor.
- The two criteria changed after the first corpus run behave differently. The hold-fill criterion moves
  nothing between 0.25 and 0.625 and has no test (section 6). The pinned-lag condition is where the real
  defect is (section 4).
- It is one lab, one marker set, 13 participants. "No wrong decision" is a statement about these 145 pairs.

## 2. An independent judge

### The judge

"Better-fitting orientation" is the reported metric again. The judge used here reads the raw marker
positions and nothing else: no IMU, no release detection, no correlation with the test curve, none of the
rule's constants.

In thigh-cluster-relative coordinates the shank cluster's centroid travels on a circular arc about the
knee. Fit the circle. The position along the arc is the shank's rotation, with an arbitrary sign.
Two physical facts fix the sign, each on its own:

- **Anatomy (A).** The thigh cluster is proximal to the knee. Extending the knee swings the shank towards
  the direction the thigh points (thigh cluster to pivot); flexing swings it away. So the thigh direction
  lies on the extension side of the resting shank. Needs the rest position only, not the hold.
- **Energy (E).** A passive limb released from a held position never swings further past rest than it was
  held. So the end of the arc farther from rest is the held end. Needs the hold or the start of the drop.

The verdict is the sign of the correlation between the loader's curve and the extension-positive arc angle.

### Is the judge any good? (measured)

- The two cues are independent and agree on 107 of the 113 files where E is confident. Five of the six
  disagreements are participant 21, whose thigh direction lies within a few degrees of the resting shank
  (median margin 6 degrees, against 15 to 46 for every other participant). A is not used for participant
  21. The sixth is `13_left_post_T1`, where E decides.
- A is used for a participant only if it agrees with E on every one of that participant's E-confident
  files and there are at least three: participants 4, 5, 14, 15, 17, 19, 22, 23 and 24.
- The judge is silent when the loader's curve does not follow the marker arc (|r| under 0.5) or when the
  cues conflict. It speaks on 124 of 145 pairs.
- It is the same physical principle as the rule's release side (held away from rest), reached by a
  different route. Cue A is the part that owes nothing to the hold. It is a reviewer's instrument built
  today, not a validated method.

### Result (measured, then by eye)

| | Pairs | Judge speaks | Rule's relative polarity right | Wrong |
|---|---|---|---|---|
| Reference mirrored | 55 | 55 | 55 | 0 |
| As given | 67 | 59 | 59 | 0 |
| All decided | 122 | 114 | 114 | 0 |

The 114 are spread over 12 participants (4: 14, 5: 8, 13: 23, 14: 6, 15: 9, 17: 22, 19: 7, 20: 1, 21: 7,
22: 3, 23: 8, 24: 6); the 55 mirrored over 10. Participant 18's two pairs are not judged.
The eight decided pairs the judge is silent on are all "as given", and all correlation-only or
shape-only: `13_right_month_post_T2`, `15_right_post_1month_T5`, `18_left_pre_T2`, `18_left_pre_T4`,
`19_left_pre_T1`, `21_left_pre_T1`, `4_left_june_post_T2`, `4_right_pre_T4`.

Per curve, with no IMU involved at all: the rule's own verdict on the reference agrees with the judge on
111 of 111 references where both speak (57 down, 54 up).

By eye: 36 decided pairs were plotted as marker arc, loader curve and IMU curve, 18 mirrored and 18 as
given, drawn at random within participants (all 13 for "as given", all 10 that have one for mirrored).
In all 36 the relative polarity the rule chose is the one the markers show.

### The three pairs where the judge and the rule differ at first sight

The judge says the reference is upside down on `4_right_june_post_T1`, `14_left_pre_T2` and
`23_left_pre_T1`. The rule says "as given" (correlation, r 0.95 to 0.97). Both are right.

Plotted, the markers show an ordinary trial that opens at rest: the leg is raised 70 to 88 degrees, held
for 2 to 6 s, released, and oscillates. The loader's curve shows that hold below rest, and so does the IMU
curve. Other trials in the same sessions that open on the hold have the IMU hold above rest.
So in these three the IMU curve is physically inverted, the reference happens to be inverted too, and the
pair is the same way up.

- The author's report reads these as "the leg is lowered, held for seconds, then raised and oscillates"
  and says the flags "describe the manoeuvre, not a defect". The markers say otherwise.
- `15_right_post_1week_T3` (undetermined) is a fourth: markers show a raised hold, the IMU curve goes to
  60 below rest. The earlier review said this IMU curve "looks upside down"; it is.
- The rule reports `test_inverted=True` on two of the four and `None` on the other two, where
  `_detect_release` takes the lift for the release and the plateau before it is the rest position.
- Against a reference of arbitrary sign this cannot be seen in the RMSE. The author says so. What the
  report does not say is that it happens in this corpus, on 4 of 145 pairs, all recordings that open at
  rest. Whether that is a defect or a convention is the IMU pipeline owner's call. PT parameters are
  mirror-invariant; an angle trace shown to a clinician is not.

### MediaPipe cross-check

From the author's `mp_rows.json`, not re-run: 13 of 13 MediaPipe pairs decided by both release sides match
the IMU pair's decision on the same optical file (reproduced from the rows), and 13 of 13 match the
geometric judge. Of the correlation-only MediaPipe verdicts the rule refuses, 34 match the judge and 17 do
not. The author's 34 / 16 against the IMU pair is reproduced.

## 3. The 23 undetermined pairs

### Are they undecidable?

All 23 were plotted with the marker arc beside the loader's curve and the IMU curve.

| Class | Count | Trials | What the rule lacks |
|---|---|---|---|
| Decidable by eye from the loader's curve and the IMU curve | 6 | `15_right_post_1month_T1`, `15_right_post_1week_T1`, `15_right_post_1week_T4`, `17_left_post_1_month_T5` (as given); `17_right_post_T4`, `13_left_post_T4` (mirrored) | Five: the optical hold is occluded (leading gap 1.2 to 2.6 s), so no reference release side and no capture prior. `13_left_post_T4`: no IMU release side; the reference's own is clear. |
| Same sense by eye, but the reference is not a usable curve | 3 | `15_right_post_T1`, `T2`, `T3` | Nothing a rule should supply. Markers are tracked on 94 to 100% of frames and show a clean trial that the IMU follows cycle for cycle. The loader's curve is flat through the hold with spikes to -150. r is 0.42 to 0.48 with the lag pinned. |
| Decidable only from the markers | 4 | `14_right_pre_T1`, `15_right_post_1week_T2` (as given); `15_left_post_T3` (mirrored, cue A only); `15_right_post_1week_T3` (both curves inverted) | The loader's curve covers 16 to 57% of the take or only the hold. |
| No orientation to find | 10 | `14_left_pre_T4`, `21_right_pre_T4`, `22_right_pre_T3`, `15_right_post_1month_T4` (loader curve a fifth of the IMU's range or less); `20_right_pre_T1`, `20_right_pre_T3` (curve ends at the release); `15_left_post_T1`, `T4`, `T5`, `15_right_post_1month_T3` (gaps, square-edged tail) | A reference. |

So the rule abstains on six pairs a careful reader can decide, and the judge decides ten of the 23.
On those ten the better-fitting orientation is wrong three times (`13_left_post_T4`, `14_right_pre_T1`,
`15_right_post_1week_T2`). A rule that fell back to the better fit would have been wrong there, and the
abstention is the right behaviour.

The cost of abstaining, per trial: `17_right_post_T4` is scored 21.3 where 7.3 is right.
`13_left_post_T4` is scored 13.4 where the right orientation scores 15.4, at a lag that is wrong either way.

What would decide the six without touching the thresholds: a capture prior taken from the take's time base
(section 4 shows it decides five of them, all the right way, and what else it lets in), or the marker
geometry.

In `14_left_pre_T4`, `21_right_pre_T4`, `22_right_pre_T3` and the three `15_right_post` trials the capture
is fine and the loader's curve is not. Across decided pairs the loader's curve moves a median 0.43 degrees
per degree of marker arc (5th to 95th percentile 0.12 to 0.82), while the IMU's range matches the arc on
the pairs plotted. That is a reference gain problem, not a polarity one, and not part of this change.
It does bear on what a 14 degree median means.

### Participant 15

14 of the 23 are participant 15: 10 with a leading optical gap over 1 s, 3 loader failures on clean
markers, 1 with both curves inverted.

| | Pairs | Participant 15's share | Median |
|---|---|---|---|
| All ok pairs | 145 | 24 (17%) | 14.67 |
| Decided pairs | 122 | 10 (8%) | 13.95 |
| Undetermined pairs, scored as given | 23 | 14 (61%) | 18.03 |
| All pairs without participant 15 | 121 | 0 | 12.66 |
| Participant 15 alone, all / decided | 24 / 10 | | 19.00 / 19.00 |

The rule does not remove participant 15, and the ten pairs it keeps have the same median as the 24.
It does halve that participant's weight, and part of the step from 14.67 to 13.95 is that reweighting:
with participant 15 left out altogether the all-pairs median is 12.66. The author's caveat that excluded
pairs are "mostly poor references" is true and does not name the participant.

Other strata the summary does not show (measured):

| | Pairs | Median |
|---|---|---|
| Decided on both release sides and the correlation | 106 | 12.69 |
| Decided on release sides alone | 3 | 27.33 |
| Decided on the correlation alone | 13 | 21.28 |
| All 145, undetermined at their worse / better orientation | 145 | 15.30 / 13.86 |
| Median of the 13 participants' medians, all / decided | 13 | 14.76 / 14.76 |
| Under the 5 degree goal: all / decided / undetermined | | 18 of 145 / 15 of 122 / 3 of 23 |

The three undetermined pairs under 5 degrees are `14_right_pre_T1` (2.2), `20_right_pre_T1` (4.3, on 6
scored samples) and `20_right_pre_T3` (0.6, 56 samples). Their overlap is the hold. `21_right_pre_T2`
(0.7, 116 samples) is the same and is in the decided set. This is on main too; the new breakdown makes it
visible and does nothing about it.

### What the summary should report

1. Three counts on every line that carries a median: decided, undetermined, total.
2. The all-pairs median as the headline, with the bound for the undetermined pairs being the other way up
   (here 13.86 to 15.30).
3. The decided-pairs median labelled as a subset, with its n, and next to it the undetermined pairs' own
   median (18.03) so the reader sees which way the exclusion cuts.
4. Undetermined pairs per participant, and any participant that loses more than half its pairs named
   (here participant 15: 14 of 24; participant 20: 2 of 3).
5. The decided pairs by basis, since the 13 correlation-only decisions are a different kind of evidence.
6. A count of `test_inverted=True` rows.
7. Never "excluding undetermined" on its own in a commit message, report headline or figure caption.

Today the script prints the counts on one line and the two medians on separate lines under "Stratified".
The stratified key is present only when there are undetermined pairs, which is fine.

## 4. Circularity, and the path the corpus does not exercise

### The author's two counts (verified from per-trial rows, the author's and this review's)

- 55 of 55 mirrored pairs have `orientation_basis="shape+correlation"`, with a release side from both
  curves. True.
- The 13 correlation-only decisions are all "as given". True. All 13 have the lag pinned by the capture
  prior.
- So no reported number in this corpus was moved by a correlation-only choice. True.
- "At a fixed lag the correlation sign and the lower offset-removed RMSE are the same decision." True by
  the algebra given, and under a manual lag the two correlations are exact negatives (measured).

### The untested path: correlation alone, lag "pinned", verdict "opposite"

`lag_pinned` is true whenever the capture prior applied. The prior is `-(imu_duration - optical_duration) +
stop_skew`, and `compare_pair` computes it after dropping non-finite reference samples. An optical take
whose first g seconds are occluded therefore has its prior moved by g. The prior still "applies" as long
as the duration difference stays inside (0.15, 1.75) s, which for a typical 0.5 s difference means g up to
about 1.2 s. The reference has no release side in exactly this case, so the correlation decides alone.

**Synthetic (measured).** IMU: hold, release, decaying swing at 0.8 to 1.1 Hz. Optical: the same curve,
same polarity, take started 0.7 s into the IMU log as on the rig, first g seconds NaN.

| IMU hold | Occluded past the release by | Result (0.9 Hz) |
|---|---|---|
| 1.2 s | 0 to 0.2 s | `ref_mirrored`, basis correlation, RMSE 2.4 to 2.9, lag -1.23 to -1.27 |
| 1.2 s | 0.45 to 0.65 s | `as_given`, basis correlation, RMSE 4.2 to 4.4, lag about -1.8 (a full cycle late) |
| 1.5 s | 0.1 to 0.3 s | `as_given`, basis correlation, lag about -1.8 |
| 2.0 s | any | undetermined (prior out of band) |

The first row is a correctly oriented reference mirrored, with a better number than a correct comparison
of a damped swing usually gets. The same happens at 0.8, 1.0 and 1.1 Hz (RMSE 2.1 to 4.3). Nothing in the output distinguishes
it from a right answer except `orientation_basis`.

**Real curves (measured).** 104 corpus pairs decided on both release sides and the correlation, lag
pinned, judge concurring. The reference hold was removed up to the reference's own release plus 0, 0.1,
0.2, 0.3 and 0.5 s: 520 cases.

| Capture prior | Undetermined | Right | Wrong | Median lag error on decided |
|---|---|---|---|---|
| As shipped (from the finite span) | 460 | 59 (11 correlation-only) | 1 correlation-only: `23_left_pre_T2`, mirrored, 8.1 reported, 10.7 true | 0.88 s |
| From the take's full time base | 17 | 499 (449 correlation-only) | 4 correlation-only, all `4_left_june_post_T4`: 7.7 to 13.3 reported, 21.3 true | 0.01 s |

Most real holds are long enough to push the shipped prior out of its band, which is why the shipped path
abstains 460 times. When it does not abstain its lag is most of a cycle off, and one decision in 12 is
wrong. With the prior corrected, the path is right on 103 of 104 pairs and wrong on one, where the reference
is poor both ways up and the wrong sense scores 8 to 14 degrees better. That is the metric choosing itself.

**The real corpus (measured).** One ok pair has a leading reference gap and a pinned lag:
`4_right_pre_T4`. Leading gap 1.13 s; prior used -1.92; prior from the take's time base -0.79; lag
returned -1.84; r 0.92; RMSE 2.6. At a manual lag of -0.79 it is r 0.87 and RMSE 6.4. The decision
("as given") survives. The 2.6 is one cycle late.

Applying the corrected prior to the corpus as it stands changes nine decisions, all from undetermined:

| Trial | Becomes | Judge |
|---|---|---|
| `15_right_post_1month_T1`, `15_right_post_1week_T1`, `15_right_post_1week_T4`, `17_left_post_1_month_T5` | as given | agrees |
| `17_right_post_T4` | mirrored, 7.5 | agrees |
| `14_right_pre_T1` | mirrored, 1.1 | **disagrees** (hold-only overlap, reference range 6) |
| `20_right_pre_T3` | mirrored, 3.6 | silent (reference range 1) |
| `20_right_pre_T1` | as given, 2.0 | silent (6 scored samples) |
| `15_left_post_T1` | mirrored, 8.2 | silent |

### Should the path exist?

Not as it is. Its premise, a lag pinned by something that cannot see polarity, is false in the one
situation the path was written for, because the same occlusion that removes the release side moves the pin.
Pearson's r has no scale, so nothing stops it deciding a reference that barely moved.
Fixed on both counts it would be useful: it decides five of the six pairs a reader can decide.
Until then it should be removed, or at least barred from mirroring. The prior's bias on a gappy reference
is on main as an alignment error; this change is what turns it into a sign error.

A manual lag also counts as pinned. Half a cycle off, a same-sense pair read r -0.41 and stayed
undetermined in the one case tried; with a lightly damped swing it would not. The workbench does not opt
in, so today that is a label only.

## 5. Code

Read line by line; the points below are the ones that came to something.

**Default path identical to main (measured).** 155 of 155 loadable pairs (145 ok, 10 error rows), every
pre-existing key and the error string, under all three alignment branches: capture prior, free search, and
a manual lag of -0.7. Also identical on eleven degenerate synthetic inputs (flat reference, flat test, 3,
10 and 39 samples, 2 s reference, all-NaN, NaN hold, 60% random NaN, NaN in the time base, empty).
Duplicate time stamps raise `ZeroDivisionError` on main and on the branch alike.

**Edge cases (measured unless marked).**

| Case | Behaviour | Comment |
|---|---|---|
| NaN gaps mid-swing | decided, mirrored correctly | fine |
| NaN through the hold, capture prior on | section 4 | **defect** |
| Fewer than 4 finite samples | error, as main | fine |
| 4 to 39 samples in a curve | no release side; correlation may still decide under a pin | fine |
| Reference shorter than the test | decided when it has a hold | fine |
| Flat reference or flat test | undetermined; both correlations `None` | reason text says `correlation_inside_margin`; it is undefined, not inside a margin |
| Manual lag | counts as pinned; the two correlations are exact negatives | see section 4 |
| Declared arbitrary, mirrored alignment fails | would be labelled `opposite_not_mirrored`, whose documented meaning is "not declared arbitrary" | by reading; both alignments fail or succeed together, so not reachable in practice |
| Rigid-body reference declared arbitrary by mistake | mirrored like any other; an inverted test curve then scores 0.0 | no guard in the engine; caller convention only |

**The caller's convention is looser than it reads.** `batch` passes
`ref_polarity_arbitrary=(method != "rigid_body")`. `load_optitrack_trial` returns `"marker_pca"` for
everything its strict rigid-body path rejects, which by its own docstring includes a precomputed-angle file
(`180 - flexion`, a real sign and zero) and the legacy quaternion path. Those would be declared arbitrary.
All 145 corpus references are the labelled-marker path (measured: labelled marker columns for both
clusters in every file, and the strict rigid-body loader refuses all 145), so no number here is affected.
`optical_absolute_zero` uses the same proxy and has the same gap, on main.

**Mirroring about the mean.** The reference is mirrored about the mean of its own finite, active-window
samples, not about the scored window's mean. On the 55 mirrored corpus pairs (measured):

- `bias_deg` moves by a median 1.7 and at most 44.8;
- raw `rmse_deg` falls by a median 5.9 (0.6 to 46.3);
- the limits-of-agreement width goes from a median 160.9 to 47.2.

None of these means anything against a relative reference, before or after, and the docstring says so.
The row labels the change with `ref_mirrored=True` and nothing else. Two consequences: the docstring's
"bias_deg stays close to what it was" is not true of every pair, and a reader diffing `rmse_deg`,
`mae_deg` or `bias_deg` between a CSV from main and one from this branch will see 55 rows move for a
reason only the new column explains. Say so in the CSV's consumers' notes, or blank those three columns on
mirrored rows.

**CSV columns.** The eight columns sit before `error` in `_FIELDNAMES`. Error rows carry them empty
(by reading and by the unit test). Undetermined rows carry `orientation`, `orientation_reason`,
`ref_mirrored=False`, both per-curve flags and both correlations.
Checked on the real batch run end to end from raw files with the branch's code (measured): 159 rows, 145 ok,
14 error; all eight columns empty on every error row; every undetermined row has a reason and an empty
basis; every decided row has an empty reason; decisions and RMSE equal to the cached-curve run on all 145.
Two things to know when reading the CSV. A per-curve flag that is `None` (the curve cannot say) is written
as an empty cell, the same as on an error row: 33 ok rows have an empty `ref_inverted` and 4 an empty
`test_inverted`. And `20_right_pre_T1` is an ok row with both correlations empty, because its 6 scored
samples have no defined correlation.
The console prints `excluding_orientation_undetermined` in a 20-character field, so that line's columns
do not line up with the others.

**Whitespace.** `workbench_engine.py` line 595 is now `finite =np.isfinite(...)` and
`batch_imu_vs_optitrack_rmse.py` line 653 is `quality_tags =pt_report_common...`. Both were correctly
spaced on main and nothing else on those lines changed. CI does not lint, so nothing catches it.

**Cost (measured).** 155 default calls: 0.6 s on main, 1.3 s on the branch. Every caller pays for the
second alignment and two release-side reads whether or not it opts in.

**Things read and found sound.** Release sides are read before active-window truncation, for the reason
the release times are. The settled level excludes pre-release samples. Only the reference is ever
mirrored. `undetermined` never falls back to the better fit. The mirrored alignment gets its own lag
search inside the same bound.

## 6. Tests

26 new engine cases (21 functions, four parametrised) and 4 batch cases. They are mostly behavioural:
synthetic curves through the public function, with the rejected rule asserted wrong on the same curve
first. Two are tests of the implementation. `test_decide_relative_polarity_table` and
`test_polarity_from_correlation_margin_boundaries` call the private helpers, and the second reads its
thresholds from the module, so it still passes with the minimum at 0.4 or the margin at 0.2.

### Mutations (measured)

53 mutations applied in memory, one at a time, to `workbench_engine` or the batch module; the two test
files run against each. 32 are caught. 21 survive:

| Survives | What it means |
|---|---|
| Fill criterion deleted; fill 0.5 to 0.0 or 0.9 | The criterion changed after the first corpus run has no test. |
| Cover criterion deleted; cover 0.3 to 0.0 or 0.45 | Same. Each of the two hides the other's absence in the one gap test. |
| Plateau limit 0.16 to 0.5 | Only disabling it outright (10.0) or 0.02 fails. |
| Settled level taken at the end of the file instead of the end of oscillation | The report says the tail-length test protects this. It does not. |
| Arrested-limb fallback deleted | No test has an arrested limb. |
| Settled-level window allowed to include pre-release samples | Untested. |
| Rest half-window 1.0 to 0.25 or 3.0 | Untested. |
| Hold window 0.5 to 1.0 | Untested (0.25 fails, through the cover requirement). |
| Hold margin 0.25 to 0.35; dead-band 5 to 4.6; minimum correlation 0.5 to 0.4; margin 0.3 to 0.2 | The "pinned from both sides" tests are written relative to the constants, or pass over a band. |
| Range as max minus min instead of 97th minus 3rd percentile | Untested. |
| 40-sample floor raised to 400; Pearson's 4-sample floor removed | Untested. |
| Polarity count line removed from `main()` | Printing only. |

Caught, and worth saying so: the release-side sign; the hold window placed after the release; every row of
the decision table; correlation deciding under a free lag; a manual lag not pinning; the prior not
pinning; mirroring without the declaration; never mirroring; mirroring by negation; reading the mirrored
correlation off the as-given alignment; undetermined falling back to the better fit; both batch
declarations; the stratified breakdown; the row fields.

The test that matters most is missing for a different reason. `test_compare_pair_with_an_occluded_
reference_hold` builds an optical take that starts at the release, with its duration set so the prior is
exactly right. An occluded hold in a take that started on time is the real case, and on that the branch
returns `ref_mirrored` for a same-sense pair (section 4).

## 7. Consumers

By reading, with two measurements. The diff touches `workbench_engine.py`, `batch_imu_vs_optitrack_rmse.py`
and their tests only. Every other caller uses the default `ref_polarity_arbitrary=False`, and the default
path is identical to main (section 5), so their numbers do not move.

| Consumer | Call | Reads | Unchanged | Left with a known-wrong number? |
|---|---|---|---|---|
| `rmse_pipeline_common.score_imu_candidate`, `sweep_imu_config.score_config` | capture prior, default | raw `rmse_deg` | yes | Yes, on main already. Measured on the 145: median raw `rmse_deg` 143.5 where the offset-removed median is 21.8 and the oriented one 14.7. The sweep's objective is the reference's missing zero. |
| `rmse_pipeline_common.score_mediapipe_candidate`, `sweep_mediapipe_config`, `sweep_mediapipe_preprocessing` | free lag, default | raw `rmse_deg` | yes | Same. Must not opt in as things stand: 129 of 146 would be undetermined (author's run). |
| `pt_report_common._lag_align_candidate` | free lag, default | `lag_sec`, raw `rmse_deg` | yes | Yes. On the 55 pairs with an upside-down reference the free-search lag of the unoriented call differs from the oriented call's by a median 1.72 s (all 55 by more than 0.2 s), and the raw `rmse_deg` it returns has a median of 121. The report's shifted curve and RMSE bar inherit both. Not regenerated here. |
| `generate_paper_figures.py` Figure 3 | capture prior, default | `lag_sec` | yes | Possibly. With the prior the two lags differ by more than 0.2 s on 14 of the 55. It is one example trial; check which. |
| `pendulastic_workbench.get_metrics_snapshot` | manual or free lag, default | `rmse_deg`, `mae_deg`, `lag_sec` | yes | No. Correct not to mirror; the new keys ride along in the snapshot. |
| `compare_2d_vs_world_angle.py`, `evaluate_*.py`, `prototype_opensim_ik.py`, `sweep_p16_mask_assessor.py` | default | raw `rmse_deg` | yes | Not examined beyond the call. |

Leaving them is acceptable for this change: it makes none of them worse and says which are wrong.
It is not acceptable as a resting state. The IMU sweep ranks configurations on a number that is mostly
offset, and the PT report aligns half its optical overlays on the wrong lag. Both need an owner and an
issue. The sweep cache will also miss once, because `workbench_engine` is fingerprinted (by reading).

## 8. Gates

Local, Windows, project `.venv`, at `a5a5cf4`.

| Gate | Result |
|---|---|
| `tests/test_workbench_engine.py` | 117 passed |
| `tests/test_batch_imu_vs_optitrack_rmse.py` | 24 passed, none skipped |
| `tests/test_rmse_pipeline_common.py` | 95 passed |

## 9. Verified by running, by reading, not at all

**By running.**
- The three gates.
- All 159 matched pairs reloaded from raw files; 145 ok pairs scored four ways; the author's counts,
  bases, medians and per-trial decisions reproduced exactly.
- Default path against main's engine, 155 pairs by three alignment branches, and eleven synthetic inputs.
- One-at-a-time sensitivity (36 settings), joint random search (4000 configurations),
  leave-one-participant-out. The fast re-decision used for the joint search was checked equal to
  `_release_side` on all 290 curves at seven configurations.
- The geometric judge on all 155 loadable optical files; its two cues against each other.
- 64 pairs plotted and read: 36 sampled decided, all 23 undetermined, and five more around the
  inverted-IMU trials.
- The occluded-hold experiments, synthetic and on 104 real pairs, with the shipped and a corrected prior.
- 53 mutations.
- Lag and raw RMSE of the unoriented call on the 55 mirrored pairs.
- The real batch end to end (`batch_imu_vs_optitrack_rmse.main`) with the branch's code, and its CSV.

**By reading only.**
- The consumers in section 7, other than the two measurements.
- The MediaPipe figures, recomputed from the author's rows but not re-run from the stored CSVs.
- That `opposite_not_mirrored` under a failed mirrored alignment is unreachable.
- That CI has no lint step.

**Not at all.**
- CI on GitHub for this branch.
- The sweeps, the workbench UI, `pt_report_common` reports, the paper figures: none run or regenerated.
- `--compare-floor`.
- Test files other than the three gates.
- Why the IMU curve is inverted on recordings that open at rest, and whether it is deterministic.
- Why the loader's curve fails on clean markers, and its gain. Seen, measured roughly, not investigated.
- The judge's anatomy cue on participants 18, 20 and 21, and the judge as a method: it was built for this
  review and checked only against itself and the plots.
- Whether the oriented free-search lag is itself right; only that it differs from the unoriented one.
- Any corpus other than these 145 pairs, and any rigid-body reference: the corpus has none.
- Thresholds outside the nine perturbed.

Reviewer's drivers, per-trial rows and plots are in the scratch folder `review_orient`, beside the author's
`orient_rework`. Nothing was pushed to the branch under review.

---

## Re-review at `5ec06a7`

**2026-11-13.** The author reports all six required changes applied in three commits on top of `a5a5cf4`:
`0c4ae85` (engine), `47bc131` (batch), `5ec06a7` (report). Everything below was re-run by this reviewer in a
detached worktree at `5ec06a7`, from raw files, with the drivers of the first review plus new ones.
The same conventions apply: trial keys, per-trial numbers and counts only; RMSE is `rmse_deg_offset_removed`.

### Verdict: ready for the owner to consider merging

All six required changes are confirmed by reproduction. Every figure the author gives for the revision was
reproduced exactly. Three things remain open; none of them blocks, and each is listed with its evidence under
"Still open".

| # | Required change | Status | How confirmed |
|---|---|---|---|
| 1 | Correlation alone must not mirror, and must not decide unless the pin holds | **Confirmed**, with two documented limits | Occlusion experiments re-run; guard boundaries probed; mutations |
| 2 | Never print the excluding-undetermined median alone | **Confirmed** | Real batch run end to end; mutations |
| 3 | Correct the reading of the four both-inverted trials | **Confirmed** | Read against this review's section 2 |
| 4 | Kill the 21 surviving mutations | **Confirmed** (21 of 21) | Re-run: 53 of 53 killed |
| 5 | Use the loader's relative flag, not the method tag | **Confirmed** | Corpus load; reading the loader; mutations |
| 6 | Minor items | **Confirmed**, all four | Reading; synthetic cases; mutations |

### Corpus (measured, 145 scored pairs of 159 matched)

| | Author | This re-review |
|---|---|---|
| As given / mirrored / undetermined | 66 / 55 / 24 | 66 / 55 / 24 |
| Median, all pairs | 14.67 | 14.67 |
| Median, decided (121) | 14.04 | 14.04 |
| Median, undetermined (24) | 16.66 | 16.66 |
| Bound on the all-pairs median | 13.86 to 15.30 | 13.86 to 15.30 |
| Median on main | 21.80 | 21.80 |

- **One decision changed from `a5a5cf4`:** `4_right_pre_T4` goes from "as given (correlation)" to undetermined,
  reason `correlation_alone_with_unverified_pin`. That is the pair the first review found pinned 1.13 s off.
  Its RMSE (2.6) is unchanged.
- **No per-trial RMSE changed** on any of the 145 pairs (largest difference 0.0), opted in or default.
- Basis: 106 on release sides and correlation (median 12.69), 3 on release sides alone (27.33), 12 on correlation
  alone (21.44). All 55 mirrorings rest on both release sides. No decided pair goes against the better fit.
- All 12 correlation-only pairs have under 0.03 s missing at the ends of the reference.
- The new field `rmse_deg_offset_removed_other_polarity` equals this review's own negated-reference run on all
  145 pairs (largest difference 0.000), and is never empty on the corpus.
- The loader declares all 145 scored references relative; none is a precomputed-angle or quaternion curve, so
  item 5 changes no corpus number.
- Undetermined pairs by participant: 15 has 14 of 24; 20 has 2 of 3; 14 has 2 of 8; 17 has 2 of 24;
  4, 13, 21 and 22 have one each.

**Default path against main (measured):** identical on 155 of 155 loadable pairs, on each of the three
alignment branches (capture prior, free search, manual lag). `origin/main` is still `e9a3a01`.

**Marker judge against the new decisions (measured):** of 121 decided pairs the judge speaks on 114, and the
pair's relative polarity is right on 114 of 114 (55 of 55 mirrored, 59 of 59 as given). Seven decided pairs are
beyond the judge; all seven are "as given". Nothing the judge could check has changed.

### Item 1: the correlation-alone path

**The author's three figures, reproduced (measured):**

| Experiment | Author | This re-review |
|---|---|---|
| Synthetic, hold occluded, both senses (240 cases) | 0 decided | 0 decided: 98 inside the margin, 71 "never mirrors", 44 "unverified pin", 27 no prior |
| 104 corpus pairs, reference hold blanked to release + 0 to 0.5 s (520 cases) | 48 right, 0 wrong, 472 undetermined | 48 right, 0 wrong, 472 undetermined |
| Same 520, gap cut off before the call | 55 right, 0 wrong, 465 undetermined | 55 right (7 by correlation), 0 wrong, 465 undetermined |

The two failures the first review found are gone. `23_left_pre_T2` with its hold blanked is now undetermined;
the synthetic same-sense pair is no longer mirrored at any of the 240 settings.

**The guards, probed one at a time (measured, synthetic take that starts at the release so only the correlation
can speak):**

| Guard | Confirms | Refuses |
|---|---|---|
| End gap, reference leading | 0.00, 0.10, 0.14 s | 0.16, 0.20, 0.30 s |
| End gap, reference trailing | 0.10, 0.14 s | 0.16, 0.30 s |
| End gap, test curve leading | 0.10 s | 0.16 s |
| End gap, 0.09 s on each curve (sum 0.18) | | refused |
| Reference range (release amplitude) | 8 deg and up | 6 deg and under |
| Scored overlap | 2.5 s of swing and up | 2.0 s |
| No capture prior | | refused |
| Manual lag, with or without the prior | | refused |
| Opposite-sense pair on a clean pin | | refused ("never mirrors") |

An interior gap in the reference (0.3 to 1.5 s after release) does not block, which is right: it does not
move the prior.

**On the corpus the range and overlap floors are inert (measured).** Setting either to zero changes no
decision. Raising the range floor to 12.5 drops one pair (`21_left_pre_T1`); to 30, three. Raising the overlap
floor to 5 s drops two. Removing the end-gap check brings back only `4_right_pre_T4`. No setting produced a
decision against the judge.

**What can still go wrong, and how far it goes.** A correlation-only verdict can now only be "as given", so it
can never move a number: the RMSE reported is the one main reports. What it can still do is label an
opposite-sense pair "as given", which puts a wrong-way score into the decided subset. Two ways to get there
were found:

1. **The admitted hole (measured).** If the caller removes the NaN samples before the call, the end gap is
   invisible. On the synthetic grid that gives 18 wrong labels in 240 (all opposite-sense pairs called "as
   given", RMSE 2.8 to 6.1), where the supported call gives none. On the 520 blanked-hold corpus cases with the
   reference also negated, it gives 1 wrong in 520 (`23_left_pre_T2`, 8.1 as labelled, 21.6 the other way up).
   **The same hole opens if the occluded samples carry NaN time stamps** as well as NaN angles (18 of 240
   again): `_end_gap_sec` discards non-finite times before measuring. The docstring names the first form and
   not the second. The batch passes the loader's full time base, so neither form is reachable from the batch.
2. **A stop skew the prior does not expect (measured, synthetic).** The prior is read off the two durations
   and assumes a fixed stop skew. With no gap anywhere, an IMU log stopped about 0.4 s earlier or 0.55 s later
   than assumed puts the pin half a cycle off, and an opposite-sense pair whose reference has no hold is
   labelled "as given" at all four swing frequencies tried. No guard can see this. The engine's own comment
   puts the prior's residual on this rig at 0.043 s, so it would take an operator or firmware change to get
   there. It is a property of the prior, not of this change.

Neither produces a wrong mirror. Both are bounded by the fact that the summary prints the correlation-only
pairs as their own line.

### Item 2: the summary

**Confirmed on the real batch, end to end (measured).** The batch's own `main()` was run on all 159 matched trials
(145 scored, 14 errors), with the three file loaders served from this review's cache of their output at the
same commit, because a full reload was taking over an hour on the review machine. Row building, the CSV and
every printed line are the batch's own. It prints:

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
```

followed by the per-participant counts (two participants flagged as majority undetermined), the reasons, and
the two `test_inverted` keys. The word "excluding" appears nowhere in the output. The stratified table prints
its "all trials" line only. The decided-subset median appears once, between the headline with its bound and
the undetermined pairs' own median. Everything the first review asked for is there: counts, per-participant
losses, the undetermined pairs' own median, the bound, the breakdown by basis, the `test_inverted` count.
The CSV has the two new columns (`optical_relative`, `rmse_deg_offset_removed_other_polarity`); the latter is
filled on all 145 scored rows.

Mutations that put the bare line back, compute the headline on the decided subset, drop the bound line or drop
the undetermined-pairs line are all killed.

By reading: `compute_stratified_stats` still returns `excluding_orientation_undetermined` in its dict, and
`main()` skips it when printing. Nothing else in the repository reads that key. The by-basis lines are subset
medians on their own lines, but they sit under the headline and each carries its `n`.

### Item 3: the four both-inverted trials

**Confirmed (by reading against this review's section 2).** The author's report now says that
`4_right_june_post_T1`, `14_left_pre_T2`, `23_left_pre_T1` and `15_right_post_1week_T3` are ordinary trials
that open at rest, that both the optical and the IMU curve show the raised hold below rest, that the three
decided ones are right as a pair, and that their RMSE (14.0, 5.3, 17.5) is agreement between two inverted
curves. It withdraws the earlier reading in so many words and raises the IMU inversion as an open issue
without touching the IMU pipeline. `test_inverted=True` is reported on two of the four, as the report says
(measured: `4_right_june_post_T1`, `15_right_post_1week_T3`).

### Item 4: mutations (measured)

The first review's 53 mutations were re-run against the revised tests. Ten had to be re-expressed because
the lines they touched were rewritten; each still makes the same change in behaviour. 48 new ones were added on
the code this revision introduced. These are this reviewer's own list, written without sight of the author's 32.

| | Mutations | Killed | Survive |
|---|---|---|---|
| The first review's list | 53 | 53 | 0 |
| of which survived at `a5a5cf4` | 21 | 21 | 0 |
| New: correlation-alone guards, end gap, floors, labels, other-polarity score, loader flag, batch summary | 48 | 47 | 1 |

The author's 85 of 85 is not contradicted: the 53 agree, and the one survivor here is not in the author's list.

**The survivor:** the end gap taken as the larger of the two curves' gaps instead of their sum. All 191 tests
pass under it. The engine does refuse 0.09 s on each curve (probed above), so the code is right and the
behaviour is unpinned. One test with a gap under the margin on each curve and over it in sum closes it.

Kills worth naming, because they are the ones the first review asked for: correlation alone mirroring; a
blocked correlation confirming anyway; each guard removed; each floor moved 15 to 20 percent either way; the
method tag deciding again; the bare "excluding" line printed again; the headline computed on the decided
subset.

### Item 5: the relative flag

**Confirmed.** `engine.load_optitrack_reference` returns `relative=True` only when the loader's quality
warnings carry its own "these angles are relative" declaration. By reading `load_optitrack_detailed`, that
declaration is attached in exactly one place, the labelled-marker knee-axis path, and unconditionally there.
The precomputed-angle path and the quaternion fallback never attach it. The batch passes
`ref_polarity_arbitrary=bool(relative)` and records `optical_relative` in the row and CSV. Mutations that make
every `marker_pca` curve relative, none relative, a rigid body relative, or put the method tag back are all
killed. Not exercised on real data: the corpus has no precomputed-angle, quaternion or rigid-body reference.

### Item 6: minor items

- **Whitespace:** both lines fixed; `git diff --check a5a5cf4 5ec06a7` is clean.
- **Undefined correlation:** now reported as `correlation_undefined` (one corpus pair, `20_right_pre_T1`).
  Mutation killed.
- **"Bias stays close":** the docstring now gives the measured change (median 1.7, up to 44.8) and says not to
  compare those columns across a mirror.
- **`opposite_not_mirrored`:** a declared-arbitrary reference whose mirrored alignment fails is now
  undetermined with reason `mirrored_alignment_failed`. The label is kept for the default path, where it is
  accurate. Mutation killed.

### Gates (measured at `5ec06a7`)

| File | Author | This re-review |
|---|---|---|
| `tests/test_workbench_engine.py` | 160 passed | 160 passed |
| `tests/test_batch_imu_vs_optitrack_rmse.py` | 31 passed | 31 passed |
| `tests/test_rmse_pipeline_common.py` | 95 passed | 95 passed |

### Still open (none blocks)

1. **One unpinned behaviour:** end gap as a sum, not a maximum (item 4). A one-test follow-up.
2. **The end-gap check trusts the time base.** Dropped samples and NaN time stamps both hide the gap, and the
   docstring names only the first. The result is a wrong "as given" label on an opposite-sense pair, never a
   wrong mirror and never a changed number. Not reachable from the batch. Worth one sentence in the docstring;
   a stronger fix would be to take the take's duration from the file rather than from the samples.
3. **The prior's fixed stop skew** (item 1, second point). Out of scope here; belongs with the capture prior.
4. **Carried over from the first review, unchanged and still owed:** the IMU inversion on recordings that open
   at rest; the sweeps and report generators that score raw RMSE or a free-search lag against a relative
   reference; the loader failures on three clean-marker trials of participant 15.

### Verified by running, by reading, not at all

- **By running:** the three test files; all 145 pairs from raw files (decisions, medians, bounds, identity to
  main on three alignment branches, the other-polarity field); the marker judge against the new decisions; 240
  synthetic occlusion cases in four forms; 520 blanked-hold cases in four forms; guard boundaries; the two new
  floors and the end-gap tolerance moved on the corpus; 101 mutations; the batch's `main()` on every matched trial, with cached loader output.
- **By reading:** the full diff `a5a5cf4..5ec06a7` of the engine and the batch; the loader paths that set the
  relative declaration; the two tests that pin the summary text; the revised sections of the author's report.
- **Not at all:** GitHub CI and the wider Python test list; the first review's joint random threshold search
  and leave-one-participant-out, which were not repeated because the nine constants they vary and all 121
  decisions they judged are unchanged; the MediaPipe figures; the sweeps, workbench UI and report generators;
  any reference that is not a marker-path curve; the author's 32 new mutations as such; the batch with its
  loaders reading the files in the same process (started, stopped after 25 minutes at a quarter done).
