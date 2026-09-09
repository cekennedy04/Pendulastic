# Can the 22 unscorable trials be recovered?

**Answer: no, and one candidate fix was built and measured to establish that.**

23 of 163 corpus trials (14%) returned nothing from the scorer. All four
causes are capture failures. One of them — the largest — had a plausible code
fix, so it was implemented, run against the corpus, and rejected on the
evidence rather than on argument.

## The four causes

Traced through the real call with `sys.settrace`, not inferred. Two earlier
hypotheses died to measurement on the way and are recorded here so nobody
re-derives them:

- **Role assignment.** Identical (`proximal`) on failing and scoring trials.
- **"The leg was never held still."** False. Failed trials have a median
  longest calm run of **3.38 s** against the 1.0 s required; scored trials
  4.07 s. This was inferred from two examples and did not survive the corpus.

| | n | Cause |
|---|---|---|
| A1 | 5 | The release gate never armed — no calm-then-departure ever qualified |
| A2 | 10 | The gate armed, was revoked by a sub-threshold wobble, and the real swing arrived before a fresh calm window could requalify it |
| B | 4 | The accelerometer stream recorded **zero samples** |
| C | 3 | No valid resting angle |

**A2's signature.** Median peak `|ω|` *while the gate was open* was
**0.381 rad/s** on failures against **6.056** on trials that scored — despite
the failing trials reaching 6–7 rad/s overall. The gate shut before the swing.

**B is not recoverable at any price.** The accel file is 49 bytes — a header
and nothing else — while gyro and mag recorded normally at 63–141 KB. Without
a gravity reference the AHRS emitted an all-zero angle series, which is worse
than refusing because it looks like a measurement.

**C splits.** Rest position as a share of excursion: cohort median **0.263**,
p90 0.424, and only 1 of 147 scored trials above 0.85. Two of the three sit at
**0.918 and 0.949** — they ended back at extension, so the settled tail is not
settled and `neutral` is wrong. The third is the mirror image: the recording
began with the leg already at rest.

## The candidate fix, and why it was rejected

A2's revocation requires a fresh full calm window after any settle-back. The
candidate kept eligibility so the next departure re-arms immediately.

**It works, in the narrow sense.** 147 → **159** scored, `no_zero_capture`
17 → 5. Twelve trials recovered.

**And the recoveries look real.** Three checks:

- Only **1 of 147** previously-scoring trials moved (`18_left_pre_T2`,
  R2n 1.219 → 1.009).
- Recovered A0 median **57.6°** against a cohort 56.0°, range 45–77 inside a
  cohort 11–91. A zero captured mid-handling would read wild or near-zero.
- Against group-mates measured normally, `|z|` median **0.74**. Two outliers
  (`20_right_pre_T4` +3.35, `24_right_pre_T4` −2.87).

**But accuracy does not move.** Within-session ICC, 35 → 38 groups:

| parameter | ICC off | ICC on | Δ |
|---|---|---|---|
| N | 0.781 | 0.786 | +0.005 |
| R2n | 0.326 | 0.344 | +0.017 |
| phi_max_ratio | 0.667 | 0.684 | +0.016 |
| omega_max_n | 0.141 | 0.129 | −0.011 |
| A0 | 0.287 | 0.285 | −0.002 |

Every change is inside noise. MDC95 improves slightly on all eight, but that
is the *n* increase: A0's goes 30.0 → 29.5°, still ~54% of its mean.

**So the gain is coverage, not accuracy — and the cost is real.** The
revocation exists to stop a zero being captured while the examiner is still
handling the limb, and this project has A0 = 418° and A0 = 169.8° in its
history from exactly that class of error. Test–retest is also a *weak*
fabrication detector: it catches a noisy fabrication inside a group, not a
consistently wrong one, and 4 of the 12 recovered trials have no scored
siblings to check against at all.

The flag was reverted rather than left off-by-default: no test exercised the
relaxed branch, so keeping it would have left permanently-dead, untested code
in the scoring path for a result already obtained.

## What was kept

`7b53239` — a replay that cannot produce an angle now says why.
`replay_trial_detailed` returns `(t, angle, ReplayQuality)` naming the
refusal; `replay_trial` keeps its two-value contract, mirroring
`load_optitrack` / `load_optitrack_detailed`. A gyro-only log is refused at
source instead of producing a flat curve that looks like a measurement.

The corpus now reports **18 `no_zero_capture` and 4 `no_accel`** in place of
23 undifferentiated `None`s, and all 140 previously-scoring trials are
bit-identical afterwards.

## What would actually move the number

None of this touches why two swings of the same leg disagree by 29°. A0's ICC
is 0.29 with or without the twelve trials. The reliability ceiling is set
somewhere else, and separating instrument noise from genuine trial-to-trial
physiological variability needs a reference with known mechanics — which is
the bench rig's whole purpose.

The three capture failures are, however, addressable at capture time rather
than in analysis: an accelerometer stream that records nothing, a leg that
never comes to rest, and a release that follows repositioning too closely are
all visible while the operator is still in the room.
