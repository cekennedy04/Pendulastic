# neuromech is the trunk: four mechanisms ported, and what the current scorer says

Date: 2026-10-26
Branch: `claude/mobile-app-validation-plan-u94fbs` @ `259d313`
Supersedes the recommendation in `2026-10-26-neuromech-rig-model-reconciliation.md` §11

`neuromech/` is now on a branch based on current `main`, so for the first time
the emulator is scored by `main`'s V0.4 `HEALTHY_REF`, the signed
`omega_min_n` and the centred frame rather than the pre-recalibration scorer
it was built against. `rig_neuromuscular_model.py` is deleted. Four
mechanisms from it were ported in. This is what the rebuild says.

---

## 1. The ports, and the rule they follow

Every one defaults to zero, so `Params()` is still the validated reference.
That is checked, not asserted: traces are **bit-identical** (max difference
`0.000e+00` deg) to the pre-port model across five pre-existing parameter sets
× both integrators, and `test_defaults_add_no_torque` fails if any default
moves off zero.

| port | what it adds | default |
|---|---|---|
| `coulomb_nm` | dry bearing friction — the amplitude-dependent damping a viscous model lacks | `0.0`, set by `rig_params()` |
| `limb_from_anthropometry()` | Winter's shank+foot fractions → explicit `lc_m`/`m_kg`/`j_kgm2` | `None` (use `s`) |
| `catch_and_hold(arrest_deg)` | the shank falls, stops and **holds** | n/a (a constructor) |
| `k_v` / `k_l` / `reflex_max_nm` | velocity and length drives on the same activation state, plus a ceiling | `0.0` |

Two design points worth stating, because the obvious implementation of each
is wrong:

**Friction is off by default.** The rig has bearing friction; De Groote's leg
does not. A default that quietly differed from the reference would make the
reference and the hardware indistinguishable, which is the one thing a
validated twin exists to prevent. `rig_params()` is the hardware.

**`s` cannot carry anthropometry.** `inertial_params(s)` ties mass to `s²` and
inertia to `s⁴`, so one scalar sets both the period and the torque scale. In a
real person those are independent, because mass cancels:

```
omega_0**2  =  m*g*l_c / J  =  g * (COM / KPROX**2) / l_shank
```

A heavy and a light person of the same height swing at the same rate and need
different torques to arrest. `inertial_params`' own docstring recommends
fitting `s` to a measured frequency — that gets the period right and the
torque wrong by `m_real / m_scaled`. Hence the explicit triple.

The parity contract was maintained rather than assumed. The LabVIEW Formula
Node text gained the same four mechanisms, a third ring buffer (theta, for the
length drive) and five terminals, and still matches the Python twin to
**1.7e-11** over 3 000 ticks on both vectors. Those vectors had **no
generator** before, so neither the model nor the port could be revalidated
after any change; `labview/make_test_vectors.py` is that generator. CI now
gates `neuromech/tests/` (56 tests) and the Formula Node verifier — neither of
which `pytest tests/` would ever have reached.

## 2. Two bugs the rebuild found

**`calibrate_ladder()` returned its own bracket edge as a calibrated rung.**
The first run on `main`'s scorer printed

```
MAS  0: lambda 0.0000 -> PT7 0.142 (target 0.06)
MAS  3: lambda 0.7500 -> PT7 0.533 (target 0.69)
```

Nothing was solved in either case: MAS 0 bisected to the bottom of the bracket
and MAS 3 saturated at the top, and both were written to `ladder.json` and
used to build scenarios. It now measures the rising-branch peak first —
bisection needs a monotone bracket and this axis is not monotone — and reports

```
rising branch: PT7 0.1417 at lambda 0 -> peak 0.6086 at lambda 0.80
MAS  0: UNREACHABLE (target 0.06) -- below the healthy-leg floor PT7 0.1417
MAS  3: UNREACHABLE (target 0.69) -- above the rising-branch peak PT7 0.6086
```

**`build.py` crashed on `P.F_PLAUSIBLE_MAX_HZ`.** That constant and the
`f_out_of_range` flag it drives exist on `fix/optitrack-trial-quality` and
`bench/retest-abs` — threaded through `PtParams`, the JSON, the persisted
record, the export manifest, the result table and the Rust, with its own
parity tests — and **never reached `main`**. Defined locally in `build.py`
for now; the feature itself is a separate merge.

## 3. The healthy-leg floor is the dominant term in PT7

A passive leg with no pathology scores **0.1417** against a healthy ceiling of
0.1709. Where that comes from:

| parameter | model, healthy passive | `HEALTHY_REF` | penalty |
|---|---|---|---|
| `omega_max_n` | 5.1136 | 9.5672 | **0.0665** |
| `omega_min_n` | −5.1136 | −9.4152 | **0.0653** |
| `phi_max_ratio` | 0.6430 | 0.6667 | 0.0051 |
| `f` | 0.9142 | 0.8839 | 0.0049 |
| `R2n` | 1.1235 | 1.0 | 0 |
| `N` | 7.0 | 6.5 | 0 |
| `area_ratio` | 0.0527 | 0.0945 | 0 |
| | | **total** | **0.1417** |

**94% of a healthy leg's score comes from two parameters**, and they are the
two already flagged. They are also near-perfectly collinear: for a pendulum
the signed minimum velocity is the negative of the peak, and the model returns
exactly `±5.1136`. So after `a23606a` fixed the extraction, PT7's seven
parameters contain a duplicated pair, and the same reference error is counted
twice.

The consequence is that the healthy band is 0.029 wide in practice.
**Nothing** in a 53-scenario library grades MAS 0 on the Popović table any
more, because its MAS-0 band (≤ 0.12) sits *below* the floor.

## 4. The severity axis on the current scorer

| λ | PT7 | zone | | λ | PT7 | zone |
|---|---|---|---|---|---|---|
| 0.00 | 0.1417 | healthy | | 0.90 | 0.554 | impaired |
| 0.20 | 0.1868 | borderline | | 1.00 | 0.4362 | impaired |
| 0.50 | 0.3071 | borderline | | 1.10 | 0.3404 | borderline |
| 0.60 | 0.3606 | impaired | | 1.50 | 0.2765 | borderline |
| **0.80** | **0.6086** | impaired | | 3.00 | 0.3197 | refused |
| | | | | 6.00 | 0.3404 | borderline |

Worse than the stale manifest suggested, in a specific way: **λ 1.1 through
6.0 all read `borderline`**, the same zone as λ 0.2. A maximally spastic leg
and a barely-affected one produce the same printed verdict. The usable
dynamic range is λ ∈ [0, 0.8].

## 5. The rig's own bearing friction can manufacture an impairment

The single most actionable result for the build:

| scenario | `coulomb_nm` | PT7 | zone | N | R2n |
|---|---|---|---|---|---|
| healthy passive | 0.0 | 0.1417 | healthy | 7.0 | 1.12 |
| `R-friction` | 0.15 | **0.2249** | **borderline** | 3.5 | 1.10 |
| `R-friction-heavy` | 0.4 | **0.2867** | **borderline** | 2.5 | 1.07 |

0.15 N·m of dry friction — a plausible bearing — halves `N` from 7.0 to 3.5
and moves a healthy leg out of the healthy band with no pathology present at
all. At 0.4 N·m, `N` drops to 2.5. Every emulated grade is read through this,
so **`coulomb_nm` must be identified before any rig run is scored**. The
method is in the code: Coulomb decay is arithmetic in amplitude where viscous
decay is geometric, so fitting both terms to one passive drop separates them.
`R-friction` is the trace to fit.

## 6. The three reflex channels are distinguishable

| scenario | PT7 | zone | A0 | N | R2n |
|---|---|---|---|---|---|
| `R-reflex-force` (published) | 0.4362 | impaired | 41.5 | 3.5 | 0.835 |
| `R-reflex-vel` | 0.4986 | impaired | 41.5 | 3.0 | 0.854 |
| `R-reflex-len` | 0.2769 | borderline | 32.0 | 14.5 | 0.815 |

At matched tone the three channels give different PT7, different `A0` and
very different `N` (3.5 / 3.0 / 14.5). The length drive is the clearest
outlier: resistance that grows with excursion rather than speed produces a
*shorter* first swing and far more small oscillations. So a rig that can play
all three can ask a measurement which channel it is looking at — which was
the point of the split.

## 7. The arrest archetype renders, and exposes a grading hole

| scenario | arrest | PT7 | A0 | N | R2n | f | verdict |
|---|---|---|---|---|---|---|---|
| `R-hold-90` | 160° Pendulastic | 1.9535 | 20.1 | 0.0 | 0.0 | 0.0 | refused (insufficient excursion) |
| `R-hold-40` | 140° Pendulastic | 1.9492 | 40.0 | 0.0 | 0.0 | 0.0 | **"MAS 4"** |

`catch_and_hold` hits its target to within 0.04° and holds without
overshooting. Both cases score ~1.95, correctly extreme.

But look at `R-hold-40`: `N`, `R2n` and `f` are all **exactly zero**, because
the leg never oscillates — and it is graded MAS 4 and marked *interpretable*,
off three degenerate zeros that passed the excursion gate on amplitude alone.
`C-N-zero` (1.687) and `R-reflex-sat` (1.824) do the same. A leg that does not
oscillate has no relaxation index, no cycle count and no frequency; grading it
on their absence happens to give the right answer here, for the wrong reason.
**A non-oscillating trace should refuse, not score 4.**

## 8. Still healthy, still wrong

16 of 53 scenarios land in the healthy zone. Three are pathologies:

| scenario | PT7 |
|---|---|
| `B-contracture` | 0.1408 |
| `B-guarding` | 0.1493 |
| `B-hypotonic` | 0.1298 |

`B-claspknife` improved — 0.1157 on the old scorer, **0.2356** now, out of the
healthy band. But contracture and non-relaxation still read healthy, and all
nine `C-*` single-parameter probes still do, including `C-phi-high` (φ_max > 1,
impossible for a passive limb) and `C-A0-high` (126° excursion). The `/7`
denominator still dilutes any one-parameter failure below the 0.029-wide
healthy margin.

## 9. Anthropometry validates

| scenario | mass | height | f | N | PT7 |
|---|---|---|---|---|---|
| `R-anthro-72kg` | 72 kg | 1.75 m | 0.8117 | 10.5 | 0.156 |
| `R-anthro-heavy` | 95 kg | 1.75 m | 0.8083 | 11.0 | 0.1546 |

The two differ in mass by 32% and in frequency by **0.4%**, confirming the
mass-free prediction empirically. Winter's closed form predicts 0.8047 Hz
against 0.8117 measured — 0.9%. Both land healthy, which is correct.

They do score slightly *worse* than the reference leg (0.156 vs 0.1417), and
`f` is why: it is the one bidirectional parameter, so a deviation either way is
penalised. The reference leg reads 0.9142 against `HEALTHY_REF`'s 0.8839 — a
0.030 miss — while Winter's limb reads 0.8117, a 0.072 miss in the other
direction. Winter's prediction sits ~10% below both the reference leg scale
and the cohort median. Whether that gap is the thigh incline, the
ankle-fixed assumption or the cohort's own `f` is open.

## 10. The rig still cannot play the library

**50 of 53** scenarios exceed `RIG_LIMIT_DEG = 45`, up from 42 of 45 — the new
`R` scenarios all release from full extension too. Peak demand is unchanged at
704 °/s. The library specifies a rig with roughly ±60° and ~700 °/s.

## 11. What is now open

1. **`omega_max_n` / `omega_min_n`.** Two independent methods and now a third
   (the penalty breakdown) say 9.5672 is wrong, and the pair is collinear so
   the error lands twice. Dropping both from the score, or re-deriving them,
   is a decision about shipped behaviour — not made here.
2. **Identify `coulomb_nm`** from a passive drop before scoring any rig run.
   `RIG_COULOMB_NM = 0.15` is a placeholder, labelled as one.
3. **Refuse non-oscillating traces** rather than scoring them on zeros.
4. **Re-point `MAS_TARGETS`** at the zone boundaries (0.1709 / 0.3528), or
   keep five rungs deliberately and report what the three-zone output
   discards. Two of five targets are currently unreachable.
5. **Merge the `f_out_of_range` flag**, or decide it is not wanted — right now
   it exists fully built on two branches and not at all on `main`.
6. **`tests/test_swing_sign.py` needs `61d5723`.** The guard and the
   `abs()`-fold fix it guards must land together; the fix is still unmerged.
