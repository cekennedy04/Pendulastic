# Fall-speed mismatch check (plan B13): the rule, the re-measurement, and group K

Date: 2026-11-02 · `python -m neuromech.fall_speed --emulator-only --check A,M,H,K`

## Order of events

1. **Rule written and committed first** (982394c), before group K was measured:
   - the constants and reasoning are in `neuromech/fall_speed.py`, at the top of the check;
   - group K's A0 (≈ 55°, equal to the anchor's) was already known from an older manifest. Its fall speed was not.
2. **Codex review.** A Codex review was run on the rule before it was used. Three changes were adopted:
   - **No PASS verdict.** A clean result is "NO FLAG", or "NO FLAG (slope not judged)" when the set slope can't be judged.
   - **Bootstrapped band.** The per-profile band comes from a bootstrap over people. It carries the uncertainty in the slope, it accounts for each person contributing two legs, and it doesn't assume normal residuals. It replaces ±1.96 residual SDs.
   - **Smallest flagged change.** Every report states the smallest change in fall speed the band would flag.
3. **Real side re-measured** on the current scorer and pinned as 20 leg rows in `neuromech/fall_speed_reference.json`. A row holds a person-grouping index, A0 and peak fall speed, and nothing else (see the 2026-11-11 note at the end).
4. **Profile sets checked.** The ladder A ran first as the known-answer case, then M, H and K.

**What wasn't changed afterwards:** no constant was changed after a result was seen. The one later code change defaults `ablation()` to the ladder's top rung. Rung 2 no longer exists on the current scorer.

## Real reference, re-measured (main @ aef97ff, `pt6rescaled-ref-2026-10-27`)

| set | unit | n | people | Spearman | log-log slope | peak °/s | A0 ° |
|---|---|---|---|---|---|---|---|
| **Real, per leg** | leg medians | 20 | 11 | +0.02 | **−0.00** (95 % CI −0.28, +0.16) | 348 | 39.2 |
| Real, all measurable falls | trials | 73 | 11 | +0.22 | +0.15 | 335 | 40.7 |
| Real, graded only | trials | 44 | 9 | +0.13 | +0.01 | 352 | 53.9 |

**Agreement with 10-27:** the 2026-10-27 per-leg result (−0.01) holds on the current scorer.

**Width of the band:** at equal A0 the band flags only a change in fall speed beyond **−44 % / +30 %**. It measures the spread between real legs, not a clinical tolerance. A smaller slowing goes through unflagged.

## Results (anchor A-P0)

| set | verdict | what decides it |
|---|---|---|
| A, PT7-band ladder (3 rungs on this scorer) | **MISMATCH** | A-P1p's fall is 45 % slower at A0 45°. The set slope isn't judged: the A0 span is ×1.18, below the ×1.2 gate. |
| M, mechanism profiles | **MISMATCH** | The set slope is +3.13 over A0 ×1.38. tone L3 and L4, and both catch-and-hold profiles, are also flagged. |
| H, HESS elbow values on the knee | NO FLAG (slope not judged) | Peak falls 5–23 % with A0 unchanged. |
| **K, HESS on the knee (MAS 1, 1+)** | **NO FLAG (slope not judged)** | K-HM1 peaks at 252°/s and K-HM2 at 233°/s, against about 263°/s for A-P0. Both have A0 55.0°, the same as the anchor. |

The ablation at the top rung (1+, λ 0.722) reads the same as on 10-27, and tone still dominates:

| model | A0 ° | peak °/s |
|---|---|---|
| healthy | 55.0 | 281 |
| full severity | 45.3 | 146 |
| tone only | 45.3 | 179 |
| force feedback (k_f) only | 55.0 | 271 |
| derivative feedback (k_df) only | 55.0 | 279 |

## What group K's result means

**What it shows:** the velocity-gated catch does not bring back the tone ladder's coupling, where speed and excursion collapse together. It slows the fall by 4–12 % relative to the anchor, which is well inside the real spread.

**What it doesn't show:** that K falls like a spastic leg.
- K never changes A0, so the set slope can't be judged. The check is silent on coupling for this set.
- The few people with clinical MAS ≥ 1 in the real cohort show the opposite pattern as a group: their excursion is much shorter than the controls' while their fall speed is only modestly lower. (Subgroup figures for so few people are not published here; see the 2026-11-11 note.)
- So few people can't settle what spasticity does to the fall (2026-10-27 report). But whatever it does there, it is excursion that moves, and K holds excursion fixed.

**Next:** K may go forward to blind clinician grading (§6 step 6) as the first set with no flag. Its realism is still open.

## Known limits

- **The ladder is caught by only one test.** On this scorer it has three rungs, and its A0 span (×1.18) falls just under the slope gate. Only the per-profile test catches it. The gate was not moved after seeing this.
- **The reference is OptiTrack A0.** Per §5b, OptiTrack shrinks angles. Re-pin the reference on B11's settled geometry. Re-pinning changes the file, which is visible in review.
- **Repeated testing.** Each profile is tested at 95 %, so a large set flags more often than 5 % by chance. For a screen that errs toward flagging, that is accepted.

## 2026-11-11: clinical fields removed from the pinned reference

The reference as first pinned carried each leg's study group and clinical MAS beside its A0 and peak, and numbered people in participant-ID order. The check reads none of that, and this repository is public, so it was removed:

- **Rows** now hold `person`, `A0` and `peak` only. No study group, clinical grade, leg side or trial count.
- **`person`** only says which legs share a body, which the bootstrap needs. Rows are written in A0 order and people numbered by first appearance in it, so the index says nothing about participant IDs.
- **The file was transformed from its own contents.** No recording was re-read; the 20 (A0, peak) pairs and their grouping into 11 people are unchanged.
- **This report** no longer gives the two-person MAS ≥ 1 subgroup's figures, and the 2026-10-27 report no longer describes individual participants.

**Effect on the numbers.** The fitted slope and residual SD are identical (−0.0035 and 0.2275). The bootstrap is seeded, but which person each draw picks depends on how people are numbered, so renumbering gives a different, equally valid set of 2000 draws:

| | as first pinned | now |
|---|---|---|
| slope 95 % CI | −0.28, +0.16 | −0.26, +0.16 |
| smallest flagged change at equal A0 | −44 % / +30 % | −43 % / +30 % |

Across 60 random renumberings of the same legs the CI's lower end ranged from −0.29 to −0.23 and the band's lower edge from −0.60 to −0.52 (log units), so both columns sit inside the bootstrap's own Monte Carlo noise. No constant of the rule was changed. The verdicts above are unchanged: the band's lower edge moved inward, so every profile flagged for a slow fall is still flagged; M's set slope is far outside either CI; and H and K sit well inside either band. The order of rows is now fixed by the data, so a later re-pin of the same legs cannot move the thresholds again.
