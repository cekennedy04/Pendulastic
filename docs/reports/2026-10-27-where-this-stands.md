# Where this stands: can the app grade severity, and what is still blocked

Date: 2026-10-27
`main` @ `529141a`

Written overnight while the one remaining step — the zone recalibration —
could not run, because the recordings exist only on the study machine and this
session has no path to it. Nothing below is estimated or filled in.

---

## 1. The answer to the original question

**On simulated limbs from the published model, the scorer grades severity
correctly over the lower half of the range and then inverts.** The severity
axis, scored by the production scorer:

| λ | PT7 | | λ | PT7 |
|---|---|---|---|---|
| 0.00 | 0.089 | | 1.00 | 0.380 |
| 0.36 | 0.200 | | 1.50 | 0.225 |
| 0.64 | 0.360 | | 2.00 | 0.221 |
| 0.76 | 0.520 | | 3.00 | refused |
| **0.80** | **0.595 ← peak** | | 6.00 | 0.333 |

Past λ ≈ 0.8 the score falls as the limb gets worse. λ = 6 — maximally
spastic — scores 0.333, between the calibrated MAS-1 and MAS-1+ rungs. The
mechanism is in the numbers: `A0` collapses from 55° to 26° as tone rises, so
the excursion every ratio is normalised against shrinks faster than the ratios
degrade. **Severity hides itself.**

That is not a subtlety of this emulator. It reproduces from committed files on
two independent models and was captured as a known property on
`bench/retest-abs` months ago (`S-lam*`, "severe leg that the composite may
grade milder or refuse").

**Four pathologies still read healthy** out of 62 scenarios: clonus (0.134),
contracture (0.088), guarding (0.098) and hypotonia (0.079). Clasp-knife
improved from healthy to borderline (0.116 → 0.174) with today's fix. And
every single-parameter out-of-range probe still reads healthy, because the
`/6` denominator dilutes one bad parameter below the healthy ceiling — φ_max > 1
(impossible for a passive limb) scores 0.098.

So: the app separates healthy from moderately impaired. It cannot rank the
severe end, and it misses several findings a clinician would record.

## 2. What changed today, and why it matters clinically

**One velocity measurement was being counted twice.** `omega_max_n` is
`max(|ω|)/A0`; `omega_min_n` was `min(ω)/A0`. For any limb dropped from full
extension the fastest motion *is* the first fall, so they are exact mirrors —
51 of 53 scenarios summed to 0.0000. On a healthy passive limb the pair
contributed 0.0665 + 0.0653 of a 0.1417 total: **94% of the score of a limb
with nothing wrong with it**, from one measurement weighted twice against a
reference that is separately disputed. `omega_min_n` is now reported, not
scored, and the healthy floor fell to **0.0892**.

**A limb that falls and arrests was graded MAS 4 off four zeros.** `A1 = 0`
forces `R2n`, `N`, `f` and `phi_max_ratio` to exactly 0.0; four of six scored
parameters penalise only below reference, so all four saturated at once and
the trial scored ~2.0 and was marked interpretable. Three scenarios reach this
and now refuse. A drop-and-hold is a real and severe finding — it is just not
one PT7 can grade.

I probed the new gate for the dangerous direction — refusing trials that
should score — across 144 synthetic trials spanning tone × damping × noise:
**zero false positives**, and no trial with `A1 > 0` was ever refused for
lacking oscillation. 4000 random parameter sets confirm the score still equals
the sum of its breakdown, that `omega_min_n` never leaks back in, and that
`mas` and `zone` are None exactly when the trial is uninterpretable.

## 3. One number you should not trust yet

`PT_HEALTHY_MAX = 0.1709` and `PT_BORDERLINE_MAX = 0.3528` are percentiles of
cohort scores computed under the **7-term** sum. Dropping a term changed every
score, so they no longer mark the boundaries they were derived to mark.

They are not rescaled, and that is deliberate: fitting a single factor to the
old→new mapping across 53 scenarios leaves a **44.6% worst-case residual
concentrated at 0.12–0.14** — the healthy boundary itself — and the error has a
direction. Scores fall by ~0.05 near that boundary, so a stale threshold reads
borderline limbs as **healthy**. A false negative is the wrong way to be wrong.
It is already visible: the emulator's MAS-1 rung now reads healthy.

Nothing clinical renders from them today (`ZONE_CLASSIFICATION_CALIBRATED =
false`), which is the only reason this could land before the recalibration.
`SCORE_FORMULA_ID` and `PT_ZONES_CALIBRATED_FOR` record the mismatch in both
languages, and `tests/test_pt_zone_parity.py` **fails if zone classification is
switched on while they differ**.

## 4. The rig model

`neuromech/` is the single physics core. B9 is done, which closes D1: the
mechanisms that lived in a second model are in `model_rt` and the LabVIEW
Formula Node, the M profiles render through the same call as every other
scenario, and `rig_neuromuscular_model.py` is gone. `Params()` is
bit-identical to before the port on five pre-existing parameter sets across
both integrators, and the Formula Node matches the Python twin at 1.7e-11 —
now with a committed generator for the test vectors, which had none.

The whole chain runs from a clean tree: 62 scenarios → 1 kHz setpoints → dry
run (10 runs, **89% delivered band == intended**, phone vs encoder |ΔPT7|
median 0.004) → ILC (**0.82° → 0.10°** in three iterations, encoder PT7
converging on the intended 0.0892) → 5 of 6 figures → a 57 KB report.

Getting there meant fixing four breaks that made the figures and report
unbuildable by any sequence of commands, three of them mine — including a
figure that read a CSV nothing in the repo ever wrote.

## 5. Blocked on you

**The one command.** On `main`:

```
.venv\Scripts\python.exe recalibrate_pt_zones.py --smoke
.venv\Scripts\python.exe recalibrate_pt_zones.py --opti-root .\OptiTrack_Recordings
```

Paste the output and the thresholds go into Python and Rust together, with the
goldens regenerated, in one commit. `--smoke` now starts instantly: importing
the profile library was costing 12 s of silent solving, which is
indistinguishable from a hang.

**Watch the refused count in that output.** The no-oscillation gate is new, so
trials that previously scored may now refuse. That is correct — an arrested
limb has no PT7 to take a percentile of — but if it refuses much of the MAS≥1
arm, the impaired median shifts and I want to see which trials. The script
refuses to emit below 5 legs per arm.

> **Update, later on 2026-10-27: all three are resolved or handed off.**
> The recordings turned out to be reachable from the analysis machine, so
> the cohort side was measured directly (51 interpretable baseline trials,
> #73's release fix).
>
> 1. **`omega_max_n`: dropped (PR #76).** On real legs, peak velocity does
>    not scale with A0 (Spearman +0.02; ~350 deg/s flat). So the term is
>    ~350/A0, rises as tone shrinks A0, and penalises the loosest legs at
>    any reference: Control_ legs read 6.18, MS and MAS>=1 legs ~12. The
>    zone recalibration still cannot run: the impaired arm is 4 legs from
>    2 people (fewer under #75's 30-degree gate).
> 2. **Thigh incline: needs a goniometer, not a run.** B10 finished (plan
>    section 5c). Phone ~112 deg against cameras ~140 deg is a disagreement
>    between instruments. One goniometer reading of a resting knee after a
>    drop, beside the phone, settles it. The default stays at -35 deg.
> 3. **Signed pair: do not land.** The return velocity inverts on real legs
>    too (AUC 0.32 the wrong way). The emulator's x0.21 dynamic range does
>    not replicate.
>
> The original text follows, unchanged.

**Three decisions, in order of how much they change.**

1. **`omega_max_n = 9.5672` is unreachable.** The model's healthy passive limb
   reads 5.11 and cannot exceed 5.55 at any damping; anthropometry predicts
   ~4.3; the one measured peak-velocity figure (n=50) gives ~4.8. Three
   independent routes say the constant is ~1.9× too high, and it is now the
   *only* velocity term in the score. Options: drop it too (the audit's
   recommendation), or re-derive it from the cohort. Either changes the
   thresholds again, so it should be decided before the recalibration, not
   after.
2. **The thigh incline is off by ~16°.** `incline_deg = -35.0` puts the passive
   rest at 55°; B10 measured −18.9° and rest depths of 65.2 (CHAT), 71.1
   (Control), 65.9 (MINT). The two figures agree with each other. But the three
   groups disagree with *each other*, so there is no single number to adopt
   without deciding whose geometry the emulator represents — and the session
   running B10 stopped on low memory partway, so the sample may not be final.
   Worth finishing B10 first.
3. **The signed-pair upgrade**, measured and recorded but not landed
   (`OMEGA_SIGNED_PAIR_NOTE`). A *signed* max and min separate the fall from
   the return, and the return is the far better discriminator: it spans ×0.21
   across the ladder against the fall's ×0.62 — three times the dynamic range —
   because spasticity destroys the return, not the drop. Needs a cohort
   reference for the redefined `omega_max_n`, so it is the same job as (1).

**And the thing none of this answers.** The cohort arm has still never run on
real recordings. Everything above is the scorer measured against models whose
truth is known, which is the right way to find defects and the wrong way to
claim clinical performance. The AUCs in `docs/reports/2026-10-25-*` are from
synthetic waveforms and should not be quoted.

## 6. State

| | |
|---|---|
| `main` | `529141a`, contains everything |
| branch | `claude/mobile-app-validation-plan-u94fbs`, fully merged into main |
| tests | 485 Python, Rust 16 suites, Formula Node 1.66e-11, webapp 395 |
| not rebuilt here | `webapp/src/wasm/` — gitignored, CI builds it, and it must be rebuilt because `pt_score_from_params` gained an `a1_deg` argument |
