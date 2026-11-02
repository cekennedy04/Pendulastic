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

But **70 are clean trials with no artifact in them at all.** Those are false
rejections, and they are not evenly spread:

| sample rate | false rejections on clean trials | 6 samples = |
|---|---|---|
| 20 Hz | **0** of 36 | 0.300 s |
| 50 Hz | 3 of 36 | 0.120 s |
| 100 Hz | **6** of 36 | 0.060 s |
| 200 Hz | 1 of 36 | 0.030 s |

The mechanism is that **the plateau rule is written in samples, not time**: a
run of 6 consecutive steps under 0.05 deg. Six samples is 0.3 s at 20 Hz but
0.06 s at 100 Hz, and a genuine turning point on a decaying swing is flat to
within 0.05 deg over 0.06 s. On one clean 100 Hz trial, 69 samples inside the
helper window have a step under 0.05 deg, producing 11 "plateau violations" on
a perfectly smooth analytic curve.

This is the same class of defect as the Savitzky-Golay window that was
specified in samples rather than seconds: a threshold that silently changes
meaning with the sample rate. Production captures at **~100 Hz** (see
`pendulastic_imu_server.py`, and Andersson et al.'s 100 Hz), with EMA
smoothing at `ema_alpha` as low as 0.1, which makes per-sample steps *smaller*
— so 100 Hz, the worst row in that table, is the production regime.

I am not claiming a mechanism for the drop back to 1 of 36 at 200 Hz. The
helper's window must come out shorter at that rate, but the script does not
show why, so it is left unexplained rather than guessed at.

**So the window and the plateau rule cannot be decided separately.** Unifying
on the helper rule alone would trade a blind spot for false rejections on
good trials at the rate the app actually samples. The order has to be:

1. Make the plateau rule rate-independent — a duration, not a sample count —
   and re-measure.
2. Then unify the windows, which is also what removes the port divergence.

---

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

## 5. Status

Nothing in the scorer was changed for this report. The diagnostic
(`evaluate_continuity_window.py`) is a one-off research script in the same
category as `evaluate_peak_detection.py`: not pipeline-wired, and it edits
nothing in `pendulastic_pt_score.py`.
