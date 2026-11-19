# Why the PT score disagrees between phone and optics (2026-11-18)

Dated note, for the owner's decision. Aggregates only. **No scorer code was changed.**
Nothing here is a clinical claim about the phone.

Question (from `2026-11-18-imu-vs-optitrack-validation.md`): the phone's rotation and
the shank plate's rotation agree to a few degrees, yet A0, R2n and area ratio do not,
and the PT verdict agrees on only about two trials in three. The phone's area ratio
is a median 0.05 against 0.14 for optics. Why?

## Answer

Mostly, the **optical** score is the one that is off, and the cause is **dropped
frames in the OptiTrack curve**. The scorer drops non-finite samples and then treats
what is left as evenly spaced. On these takes a median 28 % of optical frames are
missing (tenth to ninetieth percentile 8 to 40 %), so the optical curve is scored on
a distorted time base. Filling the gaps before scoring, and changing nothing else:

| Sensor rotation against shank rotation (130 well-tracked trials) | As recorded | Optical gaps filled |
|---|---|---|
| PT verdict agreement | 98 of 125, kappa 0.63 | 118 of 123, kappa 0.92 |
| PT score ICC | 0.79 | 0.86 |
| Area ratio: optical median (phone 0.056) | 0.143 | 0.055 |
| Area ratio ICC | 0.48 | 0.81 |
| A0 ICC | 0.58 | 0.76 |
| Optical PT score, median (phone 0.12) | 0.215 | 0.126 |

(ICC over every trial both curves score; verdicts over trials interpretable on both.)

So the phone's low area ratio was the right one, and the optical 0.14 is an artefact.
The second cause is the scorer's **drift correction**, which acts differently on the
two curves. The candidates named beforehand (where each curve starts, release
detection, sample rate) contribute little or nothing.

## Method

`pt_disagreement_diag.py` scores the same two curves of each paired trial under
progressively equalised conditions, with `pendulastic_pt_score` unchanged. The curves
are the shank plate's rotation and the phone's rotation about its own axis (not the
app's angle, so that the app's flexion-axis loss does not confound the question).
Restricted to the 130 trials where those two curves agree to within 4 deg
(offset-removed), which is the situation the question is about. Gaps are filled by
linear interpolation.

| Step | What is equalised | A0 ICC | R2n ICC | Area ratio ICC | N ICC | f ICC | PT score ICC | Verdict kappa |
|---|---|---|---|---|---|---|---|---|
| As recorded | nothing | 0.58 | 0.63 | 0.48 | 0.97 | 0.86 | 0.79 | 0.63 |
| Same time window | where each curve starts and ends | 0.57 | 0.70 | 0.48 | 0.98 | 0.92 | 0.78 | 0.61 |
| + optical gaps filled | dropped optical frames | 0.76 | 0.71 | 0.81 | 0.98 | 0.95 | 0.86 | 0.92 |
| + same 60 Hz grid | sample rate | 0.75 | 0.71 | 0.80 | 0.98 | 0.95 | 0.86 | 0.92 |
| + same release instant | release detection | 0.80 | 0.71 | 0.81 | 0.98 | 0.95 | 0.86 | 0.92 |
| + drift correction off (both) | the scorer's detrend | 0.91 | 0.75 | 0.86 | 0.97 | 0.95 | 0.88 | 0.89 |

With the app's angle as recorded in place of the sensor rotation: A0 ICC 0.35, verdict
kappa 0.52. That gap to the first row is the app's flexion-axis loss, already reported.

## Each candidate

- **Where each curve starts** (phone zero pose against take start): no effect. The
  phone recording starts a median 0.7 s before the take and ends 0.1 s before it;
  cutting both to the shared window changes nothing.
- **Sample rate**: no effect.
- **Release detection**: small. The two releases differ by a median 0.04 s (p90 0.14 s)
  as recorded and 0.02 s once gaps are filled. Forcing the same instant moves A0 ICC
  from 0.75 to 0.80 and nothing else.
- **Dropped optical frames** (not on the original list): the main cause, above. The
  size of the area-ratio shift on a take goes with its share of missing frames
  (Spearman 0.41). Mechanism, read from the code and not separately proven: after
  dropping non-finite samples the scorer sizes its smoothing windows, the swing-centre
  boxcar and the peak spacing in samples, from the median sample interval, so a curve
  with holes is smoothed and centred over the wrong span of time. The phone's stream
  is even, so its score is not affected.
- **Drift correction**: the second cause. The scorer removes a slope fitted to the
  settled tail. The fitted slope differs between the two curves of a take by a median
  0.2 deg/s (p90 about 1 deg/s), which over a ten second swing is 2 to 10 deg of A0.
  The remaining A0 difference follows the difference in rest level between the curves
  (Spearman 0.81) and the difference in fitted slope (0.50). Turning the correction
  off on both raises A0 ICC from 0.80 to 0.91 and area ratio from 0.81 to 0.86. This
  is not a recommendation to turn it off: it was introduced for phone drift and tuned
  on phone trials, and this test does not say which curve's slope is the true one.
- **What is left**: R2n stays near 0.75 with the phone about 0.03 higher, and the
  phone's first swing (A1) is a median 2 deg larger. Not explained here.

## What this changes in the validation report

- "The PT score does not yet match" (verdict kappa 0.49 app angle, 0.60 sensor
  rotation) was measured against optical scores distorted by dropped frames. Against
  gap-filled optical curves the sensor rotation reaches kappa 0.92. The app's angle
  was not re-scored against gap-filled optics here; its flexion-axis loss still
  stands and is expected to keep it lower.
- The "optics against optics" ceiling (kappa 0.78) was measured on two gappy curves
  and is not a ceiling.
- Every OptiTrack-derived PT score in the project computed from a curve with dropped
  frames carries this distortion, including the scorer's optical reference values
  wherever they came from such takes. Not re-run here.

## Options for the owner

Scorer changes need Python, Rust and C# together and the three parity checks. abs()
is untouched by all of these.

1. **Fill short optical gaps before scoring, in the analysis layer only** (the
   validation and comparison scripts), bounded to a stated maximum gap and reported
   with the share filled. No scorer change, no loader change; phone scores do not
   move. Cheapest, and enough to restate the validation's PT section. The loader
   deliberately never fills gaps today; this would not change that.
2. **Make the scorer indifferent to dropped frames** (put the curve on an even time
   base internally before the sample-count steps). Fixes it for every consumer. A
   scorer change in three languages; phone scores should not move because the phone
   stream is already even, which the parity runs would have to confirm.
3. **Leave the scorer; report optical PT scores only for takes under a stated share
   of missing frames.** No code change, but it discards most of the corpus (median
   28 % missing).
4. **Drift correction**: separate question. Worth its own look at which curve's tail
   slope is right before anything is changed.

Recommendation: 1 now, to restate the PT-score validation on a sound reference (and to
re-score the app angle against it); then decide 2 with those numbers in hand. 4 after.

## Caveats

- 130 of 159 trials (those the two curves agree on), 13 participants, one control.
- Linear interpolation across a gap is itself an assumption. Most gaps are short
  (median longest gap in the swing 0.08 s), but 8 takes have one over 0.5 s.
- The mechanism for the gap effect is read from the code, not isolated experimentally.
- Agreement is after per-trial time alignment.
