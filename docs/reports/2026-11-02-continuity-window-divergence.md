# The two continuity windows, measured

**2026-11-02.** Reproduce: `python3 evaluate_continuity_window.py [quick|full|clean]`

On 2026-10-29 I recorded that Python's and Rust's `score_waveform` bound their
continuity windows with different code, and left "which rule is right" as a
decision for you. That divergence was read off the source and never measured,
so there was nothing to decide against. This measures it.

---

## 1. First, a correction

I wrote on 2026-10-29, in the commit message, the status note and my summary,
that **"the desktop tuner and the shipped app apply different quality gates to
the same waveform."** That is wrong, and it overstated the stakes.

Rust's `score_waveform` is called from **its own tests and nowhere else**. It
is not exported through `wasm.rs`, and nothing in `webapp/` calls it:

```
$ grep -rn "score_waveform(" mobile-imu-core/ --include=*.rs
  src/scoring.rs:948          the definition
  tests/scoring_test.rs:243   test
  tests/scoring_test.rs:257   test
  tests/pipeline_test.rs:177  test
```

So the divergence has **no runtime consequence today**. It is a ported function
that has drifted from its reference while nothing depends on it — a parity and
maintenance problem, not a live defect in the app. That is a much smaller
claim than the one I made, and it should drop down your priority list
accordingly.

What stays true: Python's `score_waveform` *is* live — it is the desktop
calibration tuner's quality gate — and the 4 s cap in it is a real blind spot.

---

## 2. What the two rules do differently

| | rule |
|---|---|
| `RULE_INLINE` | Python, `imu_calibration_tuner.score_waveform`: two or more extrema → the last extremum, **capped at release + 4.0 s**; otherwise the first point the signal is permanently within tolerance of `neutral`, also capped. |
| `RULE_HELPER` | Rust, `scoring.rs::score_waveform`: `active_oscillation_window_end` — stop at the first gap larger than `_OSCILLATION_GAP_FACTOR` times the extrema's own median spacing. **No cap at all.** |

A second correction while I am here: I said the parity tests "pin those six
and nothing else." They pinned **two** — `TRIAL_SWING` and
`TRIAL_SINGLE_DROP`. The `*_SW_PASSES` / `*_SW_PENALTY` constants for
`TRIAL_NOISY_TAIL`, `TRIAL_LOW_AMP`, `TRIAL_LIFT_HOLD` and `TRIAL_E2E` were
generated into `golden.rs` and asserted **nowhere**. That is now fixed (§5),
and it is the same lesson as the ungated test file: a generated constant with
no assertion behind it is not coverage.

Measured over 10,836 generated trials (length, damping, frequency, sample
rate, neutral angle, resting tremor, and sensor freezes at three offsets and
three lengths):

| | |
|---|---|
| windows differ | **6,523** (60.2%) |
| and the helper's window is longer | **6,523 of 6,523** — in every single case |
| median window after release | inline **4.00 s** (always the cap), helper **7.45 s**, max 29.04 s |
| continuity verdict differs | 988 (9.1%) |
| **overall pass/fail differs** | **268 (2.5%)** |
| and the helper rejects what inline accepts | **268 of 268** — never the reverse |

Those are the figures **before** the plateau rule was fixed (§5). Re-measured
over the same corpus afterwards, the window comparison is unchanged — it is
the same rule — but the damage is almost gone:

| | before the plateau fix | after |
|---|---|---|
| windows differ | 6,523 (60.2%) | 6,523 (60.2%) |
| continuity verdict differs | 988 (9.1%) | 809 (7.5%) |
| overall pass/fail differs | 268 (2.5%) | **219 (2.0%)** |
| …of those, trials with a sensor freeze | 198 | **218** |
| …of those, **clean** trials | **70** | **1** |

So dropping the cap now costs **one** clean trial in 10,835 instead of 70,
and buys 218 caught dropouts. That is a much easier decision than it was this
morning, and it is the main reason the plateau rule had to be fixed first.

The replication is self-checked: every trial computes the inline verdict twice,
once by calling the real `score_waveform` and once through the script's copy of
it, asserting both `passes` and `penalty` agree. It matched on all 10,836, so
the helper column differs only in the window.

---

## 3. The answer is not "adopt one rule"

Of the 268 verdict flips, **198 are trials with a sensor freeze** — artifacts
the 4 s cap hides and the helper catches. That is the case for dropping the
cap, and it is the same hole the 2026-10-29 fix could not reach: a freeze at
t = 6.0 s in a 12 s trial is simply outside the inline window.

But **70 are clean trials with no artifact in them at all** — false
rejections, and not evenly spread:

| sample rate | false rejections on clean trials | with the plateau rule fixed (§5) |
|---|---|---|
| 20 Hz | **0** of 36 | 0 |
| 50 Hz | 3 of 36 | 0 |
| 100 Hz | **6** of 36 | 0 |
| 200 Hz | 1 of 36 | 0 |

The one clean trial that still disagrees across the whole corpus is worth
naming, because it is **not** the plateau rule: a 30 s trial at 20 Hz with a
2 deg resting tremor, where the helper's window runs out to 28.90 s and finds
4 violations in the tail. That is the limitation
`_active_oscillation_window_end`'s own docstring already records — a long
regular tremor can outvote a handful of real swing extrema and set the gap
limit to its own cadence. Dropping the cap inherits that, and it is bounded to
long trials with tremor.

### The mechanism, measured rather than asserted

My first explanation of this — "a turning point at 100 Hz is flat over
0.06 s" — was incomplete, and the correction matters because it changes the
fix. A turning point at *full* amplitude is flat to within 0.05 deg for only
about 2 samples. The runs that trip the check are turning points in the
**decaying tail**, which flatten as the amplitude falls. On one clean 12 s /
100 Hz / 0.6 Hz swing, every extremum produces a flat run and the runs grow
monotonically:

| t | amplitude | run length | predicted | violations |
|---|---|---|---|---|
| 1.81 s | 18.75 deg | 4 | 3.8 | 0 |
| 5.97 s | 13.44 deg | 5 | 5.2 | 0 |
| 6.80 s | 12.58 deg | 6 | 5.6 | 1 |
| 8.46 s | 11.01 deg | 7 | 6.4 | 2 |
| 10.12 s | 9.64 deg | 8 | 7.3 | 3 |

Predicted from `|Δangle| = A·ω·|sin(ωτ)|·dt < 0.05`, matching to within a
sample throughout, and the 11 violations reconcile exactly with what
`score_waveform` reports. **The first run long enough to trip the rule appears
at t = 6.80 s** — past the 4 s cap. So the cap was not only hiding dropouts;
it was also, accidentally, what kept these false violations out.

### There are TWO rate dependencies, not one

| | as written | what it means at 20 Hz | at 200 Hz |
|---|---|---|---|
| run length | `run >= 6` samples | 0.30 s | 0.03 s |
| step test | `< 0.05` deg between samples | 1.0 deg/s | 10 deg/s |

The second one is the subtler and the worse: an absolute step *between
samples* is a velocity of `0.05/dt`, so the threshold gets **laxer the faster
you sample** — more of a real swing counts as motionless exactly where there
is more data. Fixing only the run length bore this out: the clean-trial
rejections went 0/3/6/1 → 0/0/1/**5**, better at 100 Hz and worse at 200.

### It already bites in production, on one of this repo's own fixtures

The 70 above are all under the *helper* window. Under the **production**
window my clean corpus shows none — the cap protects it. But my corpus used
A0 of 20–40 deg, and the rule bites on **low-amplitude** trials, where the
whole swing is slow:

`TRIAL_LOW_AMP` — a committed fixture whose generator comment calls it "a
genuinely low-amplitude swing (A0 ~ 11 deg)" — scored `passes = false`,
penalty 14.0, entirely from plateau violations. At its 100 Hz the old
threshold is 5.00 deg/s and the trial's **median** speed is 4.06 deg/s, so
268 of its 499 samples counted as "not moving". It sat in the fixtures that
way, and nothing failed, because nothing asserted it.

## 4. The one measurement I could not make

Whether those false rejections happen on **real** data depends on the
per-sample noise floor of the replayed angle, and they disappear as noise
rises. On the clean 100 Hz trial above, adding white noise:

| noise sd | verdict still differs |
|---|---|
| 0.000 deg | 8 of 8 seeds |
| 0.005 | 8 of 8 |
| 0.010 | 7 of 8 |
| 0.020 | 5 of 8 |
| 0.050 | 1 of 8 |
| 0.100 | **0 of 8** |

So if the real replayed angle moves by more than ~0.05 deg per sample through
the swing, the false rejections are a synthetic artifact and only the blind
spot is real. If it moves by less — which EMA smoothing at alpha 0.1 makes
plausible — both are real.

I cannot measure it here: every real `*_imu_raw.jsonl` is participant data and
is not in the repo, and the `TRIAL_E2E` fixture is synthetic
(`gen_fixtures.py` says so explicitly). It is a cheap check on your machine —
the median and 10th percentile of `abs(diff(angle_deg))` over the post-release
window of a handful of real replayed trials, against the 0.05 deg threshold.

---

## 5. What was changed, and what was not

**Changed: the plateau rule is now rate-independent**, in both ports —
`_PLATEAU_MIN_S = 0.30` (a duration, not 6 samples) and
`_PLATEAU_MAX_DEG_S = 1.0` (a velocity, not 0.05 deg between samples). That
pair *is* the old rule at 20 Hz, the rate every existing test runs at, so it
changes nothing there and only makes the higher rates mean the same thing.

Both halves are kept, and each is pinned by a test, because neither is
redundant on the axis it covers — though it took a measurement to establish
that. Either half alone removes the false violation on a low-amplitude swing,
so that case pins neither. What only the duration half delivers is
consistency: with the run length left as a sample count, a dropout of
0.10–0.20 s is **caught at 100 and 200 Hz and missed at 20 and 50 Hz** — the
same physical artifact judged differently by how fast the phone sampled.

Three golden values moved, all of them this fix and none of them noise:

| | before | after |
|---|---|---|
| `TRIAL_LOW_AMP_SW_PASSES` | false | **true** |
| `TRIAL_LOW_AMP_SW_PENALTY` | 14.0 | **0.0** |
| `TRIAL_NOISY_TAIL_SW_PENALTY` | 192.0 | **39.0** |

(`golden.rs` also carries a `GENERATED_WITH` bump and five last-bit float
changes from this container's newer numpy/scipy — environment, not behaviour.)

**Changed: the parity test now covers five fixtures instead of two**, and the
sixth is asserted on its own, because `TRIAL_NOISY_TAIL` is where the two
windows actually bite: Python scores it 39, Rust 80. Driving the Python
reference with the helper's window reproduces 80 exactly, so the whole
41-point gap is the window and nothing else. Both ports still reach the same
*verdict* on every fixture — but a penalty gap is not cosmetic, because
`tune()` picks the lowest-penalty passing candidate, so the two can prefer
different calibrations. That test is written to fail if the windows are ever
unified, which is the right prompt to update it.

**Not changed: the windows.** That is still your call, and §3 is why it is not
a one-liner. The order is: plateau rule (done), re-measure (done — the clean
corpus is now 0 false rejections at every rate), then unify.

The diagnostic (`evaluate_continuity_window.py`) remains a one-off research
script in the same category as `evaluate_peak_detection.py`: not
pipeline-wired, and it edits nothing in `pendulastic_pt_score.py`.
