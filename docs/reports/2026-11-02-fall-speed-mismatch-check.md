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
3. **Real side re-measured** on the current scorer and pinned as 20 anonymised leg rows in `neuromech/fall_speed_reference.json`.
4. **Profile sets checked.** The ladder A ran first as the known-answer case, then M, H and K.

**What wasn't changed afterwards:** no constant was changed after a result was seen. The one later code change defaults `ablation()` to the ladder's top rung. Rung 2 no longer exists on the current scorer.

## Real reference, re-measured (main @ aef97ff, `pt6rescaled-ref-2026-10-27`)

| set | unit | n | people | Spearman | log-log slope | peak °/s | A0 ° |
|---|---|---|---|---|---|---|---|
| **Real, per leg** | leg medians | 20 | 11 | +0.02 | **−0.00** (95 % CI −0.28, +0.16) | 348 | 39.2 |
| Real, all measurable falls | trials | 73 | 11 | +0.22 | +0.15 | 335 | 40.7 |
| Real, graded only | trials | 44 | 9 | +0.13 | +0.01 | 352 | 53.9 |
| Real, clinical MAS ≥ 1 | trials | 15 | 2 | −0.02 | −0.05 | 290 | 24.5 |

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
- The two people with clinical MAS ≥ 1 in the reference show the opposite pattern. Their median A0 is 24.5°, against 50.8° for controls, while their fall speed is only 16 % lower (290 against 346 °/s).
- Two people can't settle what spasticity does to the fall (2026-10-27 report). But whatever it does there, it is excursion that moves, and K holds excursion fixed.

**Next:** K may go forward to blind clinician grading (§6 step 6) as the first set with no flag. Its realism is still open.

## Known limits

- **The ladder is caught by only one test.** On this scorer it has three rungs, and its A0 span (×1.18) falls just under the slope gate. Only the per-profile test catches it. The gate was not moved after seeing this.
- **The reference is OptiTrack A0.** Per §5b, OptiTrack shrinks angles. Re-pin the reference on B11's settled geometry. Re-pinning changes the file, which is visible in review.
- **Repeated testing.** Each profile is tested at 95 %, so a large set flags more often than 5 % by chance. For a screen that errs toward flagging, that is accepted.
