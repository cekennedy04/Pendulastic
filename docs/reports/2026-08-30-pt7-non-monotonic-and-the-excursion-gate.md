# PT7 scores the most severe legs as mild

**2026-08-30** — reproduction of the PT7 severity curve against simulator
ground truth, and the gate now shipped against its unsafe tail.

## The finding

PT7 is not monotonic in severity. Measured with the shipped scorer against
`tests/fixtures/pendulum_sim.py` at dialled-in muscle tone, force-feedback gain
`k_x = 0`, 2 s pre-release hold:

| tone | PT7 | MAS | A0 | R2n | phi_ratio | area_ratio |
|---|---|---|---|---|---|---|
| 0.0 | 0.036 | 0 | 78.3 | 1.084 | 0.700 | 0.076 |
| 1.0 | 0.293 | 1+ | 75.4 | 0.965 | 0.548 | 0.225 |
| 2.0 | 0.420 | 1+ | 68.7 | 0.849 | 0.346 | 0.262 |
| 3.0 | **0.989** | **4** | 62.4 | 0.668 | 0.107 | 0.558 |
| 4.0 | 0.777 | 3 | 55.1 | 0.823 | 0.271 | 0.438 |
| 5.0 | 0.304 | 1+ | 48.1 | 1.014 | 0.596 | 0.215 |
| 6.0 | 0.347 | 1+ | 35.4 | 1.162 | 0.580 | 0.247 |
| 8.0 | 0.246 | **1** | 19.0 | 1.045 | 0.579 | 0.129 |
| 10.0 | 0.322 | 1+ | **11.2** | 1.149 | 0.570 | 0.190 |

The score peaks at tone 3 and falls away. At tone 10 the leg has almost stopped
moving — 11.2 deg of excursion against 78.3 for a passive leg — and is reported
as **MAS 1+**, mild.

**A0 is monotonic across the entire range (78.3 → 11.2) and is not one of the
seven scored parameters.**

## Why, structurally

All seven scored parameters are ratios normalised on the swing itself. Watch
R2n and phi_max_ratio in the table: they fall to 0.668 / 0.107 at tone 3, then
climb back to 1.149 / 0.570 at tone 10 — values indistinguishable from healthy.
When the swing collapses, the ratios renormalise on a tiny, clean, near-
symmetric motion, and a barely-moving leg looks pristine.

This is not a calibration problem. No choice of thresholds fixes a U-shaped
function of severity.

## What shipped

**A gate, not a new score.** `pendulastic_pt_score.mas_estimate()` returns
`mas=None` with a reason, instead of a grade, when A0 falls below the
interpretability floor. The PT7 value is still reported. The report's row-5
table no longer prints a grade in that regime.

**What the gate does NOT do.** It does not fix the non-monotonicity. PT7
already turns over near A0 60 deg, far above any defensible threshold. It
closes the unsafe tail — a leg that barely moved being reported as mild — and
nothing more. Between roughly tone 3 and tone 6 the score still falls while
severity rises, and no gate addresses that.

## Threshold derivation

From the **control distribution**, never from the spastic legs.

There are 7 spastic legs with a usable reference. That cannot support fitting a
diagnostic cutoff, and every one of them sits at A0 >= 28.7 deg — the
collapsed-excursion regime is not represented in this corpus at all.

Over **53 non-spastic OptiTrack trials** passing the quality filter (coverage
>= 80%, no area-ratio warning): **mean 46.6 deg, sd 11.1**. Two SD below the
healthy mean is 24.5, rounded to **25 deg** — the conventional "outside the
normal range" bound, which is the honest claim, rather than an operating point
tuned to make the simulator look good.

An unfiltered pass was tried first and discarded: it put control A0 anywhere
between 7 and **418 deg**. A 418 deg knee excursion is impossible, so that
distribution measures tracking failure, not physiology, and any threshold from
it would be meaningless.

### Correction: the two-SD derivation is circular

Found the same day, after the seed-window bug in `_angle_from_labeled_markers`
surfaced. **Do not quote "two SD below the control mean" as the justification.**

The only two trials pulling the control distribution down are P9 left and right
at A0 9.0 — precisely the two the gate then catches.

| control set | n | mean | sd | 2-SD floor |
|---|---|---|---|---|
| all (used for the original derivation) | 53 | 46.5 | 11.1 | 24.4 |
| excluding the two suspect trials | 51 | 48.0 | 8.3 | **31.5** |

And 31.5 is unusable: the lowest spastic leg sits at **28.7**, so a clean
two-SD control bound lands *above* the spastic range and would refuse grades on
the very cases the study exists to measure. **No single threshold satisfies
both "two SD below controls" and "clears every spastic leg" on this data.**

So 25 is not a control-derived bound. It is a conservative floor **constrained
by the spastic minimum** — as high as it can go while staying clear of 28.7
(~13% margin). It catches the collapsed tail and claims nothing about where the
healthy range ends.

**Re-derive once the seed-window bug is fixed.** That bug anchors the zero to
whatever pose fills the first 60 frames, yields a convincing ~180 baseline
either way, and passes the coverage and area-ratio filters untouched (P9 Left
trial_3: A0 418 deg at 97.3% coverage). Until the reconstruction is
trustworthy, no threshold derived from these A0 values is either.

## Effect on the corpus

58 trials graded exactly as before. **2 refused** — P9 left and right, both at
A0 9.0 deg, both previously reported as **MAS 1**.

Those two trials are independently broken — P9's left and right exports are
identical, and P9 is also named in the seed-window finding. The gate's first
real catch is a trial that was already bad, which is the intended behaviour.
Note this cuts both ways: because those same two trials set the control floor,
the derivation had to be corrected (above).

## The wording is deliberate

The refusal talks about the measurement, not the patient, and a test enforces
it. Low excursion also comes from poor positioning, an incomplete release,
guarding or pain, mechanical obstruction, and sensor failure. Reporting "severe
spasticity" on any of those would trade one wrong answer for another. The
message says to repeat the trial and check positioning.

## Second opinion

The design was checked against an independent model (codex), which argued for
this shape unprompted — keep PT7 as published, gate the verdict, never fold
amplitude into the score while still calling it PT7 — and corrected two things
in the plan: derive the threshold from controls rather than from the spastic
legs, and word the refusal as a protocol failure rather than a severity claim.
Its blunt list of what not to do is worth keeping:

> Do not tune PT7 weights or MAS cutoffs to make this simulator look monotonic.
> Do not add raw A0 to PT7 and still market it as PT7.
> Do not infer a clinical threshold from eight spastic legs.
> Do not return MAS 0/1/1+ when excursion is below the gate.

## Still open

- **The non-monotonic middle.** The gate does not touch it. A monotonic
  severity index is the real fix, and it needs a cohort enriched for severe
  spasticity before it can be defined, let alone validated.
- **Unwired consumers.** `mas_validation.py`, `model_vs_optitrack_eval.py`,
  `hpe_mas_evaluation.py` and `p13_leg_session_comparison.py` still call
  `pt_to_mas` directly. Research tooling rather than clinical output, but they
  will disagree with the report until wired.
- **Prospective validation.** The threshold is locked and should be revised
  only against a cohort that actually contains collapsed-excursion legs.
