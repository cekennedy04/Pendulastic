# The case for dropping `f` from the scored set

**Date:** 2026-10-27
**Code:** `pendulastic_pt_score.compute_pt_score_breakdown`, `mobile-imu-core/src/pt_score.rs`
**Status:** NOT ACTED ON. The divisor rescale was taken instead (`90776a5`). This
document is the argument for the further step, with the counter-arguments that
were raised against it, so the decision can be revisited on evidence rather
than re-derived.
**Reproduce:** `python healthy_ref_frequency_audit.py`;
`python -m pytest tests/test_pt_zone_parity.py -k f_skip`

---

## What is being proposed

```diff
-_PARAM_KEYS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "f", "area_ratio"]
+_PARAM_KEYS = ["R2n", "N", "phi_max_ratio", "omega_max_n", "area_ratio"]
```

Nothing else. `f` would still be computed, still be reported, still be
displayed, and still be selectable in the app's parameter panel. It would stop
being summed into the canonical score.

This would be the **second** departure from Popović-Maneski's published
parameter set, after `omega_min_n` on 2026-10-26. That is the main reason it
has not been taken, and the reason this document exists rather than a commit.

---

## 1. `f` carries the only discontinuity in the score

`f` is bidirectional and cannot be estimated below two cycles, so the scorer
drops it when `f < 0.1 or N < 2.0`. Until `90776a5` the divisor stayed at six
regardless, which meant crossing that boundary **deleted** a penalty term
instead of redistributing it. The score fell as the limb worsened.

On a sweep where `f` degrades with `N`, as a real limb's would:

| N | f (Hz) | before `90776a5` | after (divisor rescaled) | without `f` |
|---|---|---|---|---|
| 6.5 | 0.88 | 0.0007 | 0.0007 | 0.0000 |
| 5.0 | 0.80 | 0.0543 | 0.0543 | 0.0462 |
| 4.0 | 0.70 | 0.0988 | 0.0988 | 0.0769 |
| 3.0 | 0.60 | 0.1433 | 0.1433 | 0.1077 |
| 2.5 | 0.55 | 0.1655 | 0.1655 | 0.1231 |
| 2.0 | 0.50 | **0.1878** | **0.1878** | 0.1385 |
| 1.5 | 0.45 | **0.1282** ↓ | **0.1538** ↓ | 0.1538 |
| 1.0 | 0.40 | 0.1410 | 0.1692 | 0.1692 |
| 0.5 | 0.00 | 0.1538 | 0.1846 | 0.1846 |
| | | 1 inversion, **32%** | 1 inversion, **18%** | **monotone** |

The rescale removed the deleted-term bias. It did not remove the
discontinuity, because `f`'s own contribution still vanishes at the boundary:
0.0724 at `N = 2.0`, zero at `N = 1.5`. Dropping `f` removes the boundary, and
is the only one of the three candidates that is monotone across the sweep.

This matters beyond tidiness. The inversion fires on the **low-oscillation
end**, which is the impaired arm, and it is therefore a candidate cause of the
inverted thresholds the 2026-10-27 cohort run produced: a MAS-0 75th percentile
of 0.2979 above a MAS≥1 median of 0.1892, a gap of 0.1087 against an f term
worth up to 0.1667. `derive()` now refuses such a pair outright
(`recalibrate_pt_zones.py`), so the cohort cannot be calibrated at all while
this stands.

## 2. It is the weakest discriminator in the set, by a wide margin

Measured on the dialled tone ladder — the only ordered severity axis this
project has, five rungs of one mechanism through `neuromech/`, whose engine is
checked against De Groote's own MATLAB:

| | range across the ladder | its term's travel | Spearman vs dialled severity |
|---|---|---|---|
| `N` | 7.50 → 2.00, **3.75x** | 0.0000 → 0.1154 (**0.1154**) | **−1.000** (perfectly ordered) |
| `f` | 0.921 → 1.020 Hz, **1.12x** | 0.0059 → 0.0257 (**0.0198**) | **+0.700** (not ordered) |

`f`'s term travels 5.8x less than `N`'s across the whole severity range, and
its rank correlation with dialled severity is not monotone. Across every
profile the app grades, `f` contributes between **2.4% and 9.6%** of the total.

On real trials it is quieter still. The control cohort's median `f` is
0.9189 Hz against a 0.8839 reference — a ratio of 1.04, which contributes
**0.0066** of a possible 0.1667. For half the control legs, `f` is doing
essentially nothing.

## 3. Its physics works against the penalty's shape

A stiffer limb oscillates **faster**, not slower:

```
ω₀² = (m·g·l_c + K) / J
```

So tone *raises* `f` — which the ladder confirms, 0.921 Hz at `tone_L0` rising
to 1.020 Hz at `tone_L4`. But the penalty is bidirectional around a healthy
mean, so that rise is charged as impairment at exactly the rate a slowing would
be. The quantity has a real, physically-grounded severity signal and the
scoring form discards its direction.

It is also small, because `K` stays modest next to `m·g·l_c` across the
healthy-to-moderate range: a 1.12x frequency change against `N`'s 3.75x. The
clinically dominant effect of tone on a pendulum test is that the limb stops
swinging sooner, and that is `N`, already in the set.

## 4. Removing it costs no discrimination — it gains a little

| | ladder dynamic range | monotone? |
|---|---|---|
| with `f` | 6.16x (0.0905 → 0.5577) | no |
| without `f` | **6.37x** (0.1002 → 0.6384) | **yes** |

This is the argument's strongest point and the easiest to check: dropping the
parameter does not blunt the instrument. The score separates the ladder
slightly *better* without it, because removing a near-constant term removes
something that was near-constant across severity too.

## 5. No published healthy value is being discarded

Popović-Maneski introduced `f` themselves — "We introduced two additional
parameters in this study" — and published no healthy value for it. The
reference the scorer uses, 0.8839 Hz, is **this project's own** cohort median.

So dropping `f` discards a number we measured, not a number from the
literature. That makes it a materially weaker departure from the published
method than `omega_min_n`'s removal was, where a published range did exist. The
remaining five parameters all keep their published definitions.

---

## The counter-arguments, and which of them hold

**"It is a second departure from Popović, and the parameter set is the
published method."** This holds, and it is why the rescale was taken instead.
A paper reporting a five-parameter PT score has to defend the set as well as
the result, and each removal makes that defence longer. Against it: the set has
already changed once, the thresholds are uncalibrated regardless, and §5 limits
how much of Popović is actually at stake.

**"`f` is mechanism-confounded."** This does **not** hold, and it was nearly
used here. Across four other mechanisms `f` spans 0.931 Hz against the
ladder's 0.105 Hz, an 8.84x ratio that looks damning — but the entire spread
comes from one profile (`fall_no_ringdown`) whose `f` is *skipped*, not
measured. Among profiles where `f` is actually estimated the mechanism spread
is 0.017 Hz, a ratio of **0.16x**. `f` is not a confound. It is simply weak.

**"The discontinuity is a bug in the estimator, not in the parameter."** This
holds, and it is the best reason to keep `f`. `f` could be estimated from fewer
extrema — with two turning points there is one half-period — which would remove
the boundary without removing the parameter. That is a change to
`compute_pt_params`, needs its own validation against the 120 Hz corpus, and
would change every stored `f`. It is the right fix if someone has the appetite
for it, and it makes this proposal unnecessary.

**"Five parameters is thin."** Partly holds. After dropping `omega_min_n` and
`f`, two of the remaining five have known problems: `omega_max_n`'s reference
is disputed by a factor of 1.85, and `area_ratio` is the only unbounded term
and can dominate a score by itself (0.649 of 1.109 on one synthetic trace).
A five-parameter score where two parameters are under question is not obviously
better than a six-parameter one, and that is an argument for fixing references
rather than for trimming the set further.

---

## What was done instead, and what would settle it

`90776a5` rescaled the divisor to the number of terms a trial can actually
score. That removed the deleted-term bias and left ~18% of the inversion.

Two pieces of evidence would settle `f`'s fate, and neither needs a decision
now:

1. **How often the boundary fires in the real cohort.** The control range
   `[0.000 – 1.067]` shows it firing at least once in 30 trials, but the run
   predates the per-arm attrition report. If `N < 2` is common on the MAS≥1
   arm and rare on the MAS-0 arm, the inversion is this boundary and `f` has
   to go or be re-estimated. If it is rare on both, the inversion is something
   else and `f` is merely weak — which is not on its own a reason to remove a
   published parameter.
2. **Whether `f` from two extrema reproduces `f` from four.** If it does, fix
   the estimator and keep the parameter. That is the outcome this document
   would prefer to its own proposal.

Until then `f` stays in the scored set, and the app's parameter panel
(`webapp/src/param-select.js`) exists so that the next version of this argument
can be made from single trials rather than from aggregates — which is what
every previous round of it lacked.
