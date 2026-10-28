# Peak fall speed tracks the swing on the emulator, not on real legs

Date: 2026-10-27 · `python -m neuromech.fall_speed` (the cohort half needs the recordings)

## The finding

**Real legs:** peak fall speed does not track the first excursion A0. Each leg was summarised by its median trial and the slope taken across 20 legs from 11 people. The log-log slope of peak on A0 is **−0.01**, with Spearman +0.02.

**Emulator:** peak speed moves *with* A0 along its severity axis.

- **MAS ladder (A-P0 → A-P2):** slope 3.4. Peak speed falls 51% (281 → 138°/s) while A0 falls 19% (55.0° → 44.8°).

| set | unit | n | people | Spearman | log-log slope | peak °/s | A0 ° |
|---|---|---|---|---|---|---|---|
| **Real, per leg** | leg medians | 20 | 11 | +0.02 | **−0.01** | 348 | 39.2 |
| Real, all measurable falls | trials | 73 | 11 | +0.22 | +0.16 | 335 | 42.4 |
| Real, graded by the scorer only | trials | 44 | 9 | +0.14 | +0.03 | 352 | 53.9 |
| Real, Control | trials | 44 | 7 | +0.23 | +0.16 | 346 | 50.8 |
| Real, MS (MINT) | trials | 29 | 4 | +0.23 | +0.18 | 326 | 27.3 |
| **Emulator, MAS ladder (group A)** | scenarios | 4 | – | +1.00 | **+3.44** | 191 | 48.3 |
| Emulator, whole library | scenarios | 62 | – | +0.76 | +1.13 | 198 | 52.7 |

Slope 1.0 means peak speed scales with the swing; 0 means it doesn't.

- **The whole-library slope** depends on which behaviours the library contains. It's shown for completeness; the ladder and the ablation below are the severity-axis comparison.
- **Real data:** main @ 4ea9cb7, 88 baseline OptiTrack trials, 73 with a measurable fall (5° < A0 ≤ 120°).
- **Why not graded trials only:** the scorer's 30° gate is a floor on A0, so it would select on the regressor and drop the short-swing trials (29 of the 73). Both rows are shown, and the per-leg row is the one to read.

## Which part of the model couples them

Each component of the published severity axis was applied alone at the MAS-2 rung (λ 0.757). This is reproduced by `fall_speed.ablation()`, printed at the end of a run.

| model | A0 (deg) | peak (deg/s) |
|---|---|---|
| healthy (λ 0) | 55.0 | 281 |
| rung 2, full severity | 44.8 | 138 |
| tone only | 44.8 | 174 |
| force feedback (k_f) only | 55.0 | 270 |
| derivative feedback (k_df) only | 55.0 | 279 |

**Most of it is tone.** In `model_rt`, `tone` is a constant extensor torque T_b plus short-range stiffness, present from release. A constant torque against gravity slows the whole fall, so on the severity ladder peak speed and A0 fall together. The two reflex gains change the fall by under 4%.

## What it does and does not establish

**Established:**
- On real legs, A0 varies a lot between legs (19° to 68°) while peak fall speed doesn't vary with it. So `omega_max_n` = peak/A0 behaves like a constant over A0, which is the mechanism behind PR #76.
- The emulator's severity axis builds in a coupling between peak speed and A0 that the real legs don't show. That's why `omega_max_n` behaved like a severity measure on the emulator and not on real legs.

**Not established:**
- **How spasticity changes the fall.** The two people with clinical MAS ≥ 1 go opposite ways. P13 falls fast with a short swing (about 330°/s at A0 ≈ 20°, both legs). P14's right leg falls slowly with a long swing (174°/s at 57°). Two people can't say whether real spasticity removes speed, excursion or both. Low A0 isn't specific to impairment either: Control 2's legs also sit near 21°, which is consistent with that participant's known rig defects.
- **Whether real spasticity is tonic.** A resting tonic torque is ruled out only as the *dominant* determinant of fall speed across this cohort.

**The healthy level:** the emulator's healthy peak (281°/s at A0 55°) sits below the real controls' median (346°/s at A0 51°), a factor of about 1.23. Whether the gap is extraction (the smoothing window sets peak velocity; see 62ff25b) or limb inertia is open.

## What it changes

1. **Severity conclusions drawn from the emulator need a caveat.** The "scorer inverts past λ 0.8" result and the A-P ladder rungs rest on a speed–excursion coupling the real legs don't show.
2. **Delivery and instrument tests are unaffected:** phone vs encoder, band delivery and ILC. They test whether the rig plays what it is told, not whether that is realistic. The `USB-2026-10-27` bench session remains a valid rig test, but not a set of realistic MAS presentations.
3. **The check for any revised severity axis is the same measurement.** The ladder's peak-vs-A0 slope should be near the real per-leg value (≈ 0). A delayed, velocity-gated catch acting after peak velocity is one candidate. The existing `M` profiles don't qualify as built (group slope 3.0).
