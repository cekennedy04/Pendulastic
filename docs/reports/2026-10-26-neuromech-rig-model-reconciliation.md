# The rig model already existed: reconciling `neuromech/` with this session's work

Date: 2026-10-26
Branch under review: `bench/retest-abs` @ `fa3d651` (pushed today, never merged)
Compared against: `main` @ `845fe6a`, and `rig_neuromuscular_model.py` on
`claude/mobile-app-validation-plan-u94fbs`

Committing the `bench/retest-abs` working tree surfaced a tracked, complete
`neuromech/` package that is not on `main` and was not visible from it. It
changes three conclusions reached earlier in this session and duplicates part
of the model written today. Nothing here has been merged or edited; this
report is the read-only finding.

---

## 1. The fixture I said was missing is present

Throughout this session I flagged the 2026-08-30 non-monotonicity finding as
unreproducible because `pendulum_sim.py` was absent from the repository — a
claim that went into a commit message on `main`. It is present, at
`neuromech/pendulum_sim.py` (470 lines), with `neuromech/tests/
test_pendulum_sim_reference.py` beside it.

Its docstring records that it is a Python re-derivation of De Groote et al.
(2018) **checked against the reference MATLAB** (`pendulumStateDerivative.m`,
`_SRS_Ffb.m`, `main_pendulum_test.m`) on 2026-08-26, with five specific
reconstructions confirmed: the Eq 1 gravity sign, the Eq 4 coordinate limit,
the Eq 9 activation form, the SRS reference angle, and the inertial scaling
(m ~ s², J ~ s⁴). Its constants are read from the MATLAB `inputdata`:
`GAMMA = 0.050 s`, `DTHETA_C = 1.5°`, `D_NOMINAL = 0.2`, `TAU_SRS = 0.050`,
`TAU_A = 0.010`, `L_SCALE_NOMINAL = 0.87`.

That retraction stands on its own: the earlier statement was wrong, and the
finding it called unreproducible is reproducible, directly, from the files
below.

## 2. The grading inverts above λ ≈ 0.85 — reproduced from committed output

`neuromech/out/severity_sweep.csv` walks the published severity axis
(`tone`, `k_f`, `k_df` scaled together) and scores each run with the
production scorer:

| λ | PT7 | λ | PT7 |
|---|---|---|---|
| 0.00 | 0.035 | 0.85 | **1.220** ← peak |
| 0.40 | 0.224 | 1.00 | 0.980 |
| 0.60 | 0.547 | 1.50 | 0.514 |
| 0.70 | 0.845 | 2.50 | 0.644 |
| 0.80 | 1.209 | 3.00 | refused (A0 22.2°) |
| | | 6.00 | **0.235** |

PT7 rises monotonically to λ = 0.85, then **falls by 58%** as the leg gets
worse, bottoms at λ = 1.5, refuses at λ = 3 for insufficient excursion, and
reads 0.235 at λ = 6 — a maximally spastic leg scored in the *borderline*
zone, milder than the calibrated MAS 1+ rung.

This is the direct answer to the question this session opened with. On
simulated limbs from the published model, the scorer grades severity
correctly over roughly the lower half of the range and **inverts above it**.
The mechanism is visible in the same table: `A0_deg` falls from 55° to 26°
as tone rises, so the excursion the ratios are normalised against shrinks
faster than the ratios degrade. Severity hides itself.

The scenario set carries the same behaviour as explicit probes — the `S-lam*`
scenarios are annotated "severe leg that the composite may grade milder or
refuse (U-shape)", so this was a known, deliberately-captured property, not
an accident of the sweep.

## 3. Four pathologies and every single-parameter probe grade healthy

Re-grading `neuromech/out/scenario_manifest.csv` (45 scenarios) under the
data-derived zones now on `main` (`PT_HEALTHY_MAX = 0.1709`,
`PT_BORDERLINE_MAX = 0.3528`):

Behaviours that land in the **healthy** zone:

| scenario | PT7 | what it is |
|---|---|---|
| `B-claspknife` | 0.116 | clasp-knife catch then release on the first drop |
| `B-guarding`   | 0.101 | non-relaxation / voluntary guarding torque |
| `B-contracture`| 0.056 | contracture, flexion stopped 25° past rest |
| `B-hypotonic`  | 0.034 | hypotonic / flaccid limb |

The first three are hallmark findings a clinician would record. The fourth is
arguably correct for a severity score — flaccidity is not spasticity — but it
is reported as *healthy*, not as *not-spastic-but-abnormal*, which is a
different claim.

More striking: **all nine `C-*` out-of-range probes grade healthy**, including
`C-A0-high` (126° excursion, physically impossible for a leg), `C-phi-high`
(φ_max > 1, energy injected on the return — impossible for a passive limb),
and `C-wmin-high` (velocity never stops at reversals). These scenarios exist
precisely to push one of the seven parameters past its bounds while holding
the rest. They all score healthy because the `/7` denominator dilutes a
single-parameter excursion below the healthy threshold. PT7 cannot see a
one-parameter failure — the refusal gates catch two of them (`C-A0-low`,
`C-A0-high`) and the score itself catches none.

## 4. The reference model independently corroborates the `omega_max_n` verdict

Earlier this session I concluded, from anthropometric prediction, that
`omega_max_n = 9.5672` is inconsistent with every measured and predicted
healthy value (+11.5 SD). The De Groote model reaches the same verdict from a
completely different direction — mechanism rather than anthropometry.

The model's healthy passive leg (`D-lowlift`, `Params()` defaults):

| parameter | model, healthy passive | `main` HEALTHY_REF | agrees? |
|---|---|---|---|
| `R2n`           | 1.129  | 1.0    | yes |
| `N`             | 7.0    | 6.5    | yes |
| `phi_max_ratio` | 0.650  | 0.6667 | yes |
| `f`             | 0.918  | 0.8839 | yes |
| `area_ratio`    | 0.012  | 0.0945 | yes (model has no sensor noise or drift) |
| `omega_max_n`   | **5.18** | **9.5672** | **no — 1.85× apart** |
| `omega_min_n`   | 0.0002 | −9.4152 | not comparable (see below) |

Five of the seven agree. The one that does not is the one already flagged.
And it is not a tuning disagreement: `B-hypotonic` (B = 0.03, essentially
frictionless) reaches only **5.30**, and `C-N-high` (B = 0.01) only **5.55**.
The model **cannot produce `omega_max_n` above ~5.6 at any damping**, so
9.5672 is not merely high — it is unreachable by a passive limb under the
published model. Two independent methods now say the same thing.

`omega_min_n` is not comparable across this table and must not be read as
corroboration either way: the manifest was scored before `a23606a` fixed the
extraction from `min(|omega|)` to Popović's signed minimum. The model's
0.0002 and `bench/retest-abs`'s reference of 0.0010 are two *old-extraction*
numbers agreeing with each other, which says nothing about `main`'s −9.4152.

## 5. Overlap with `rig_neuromuscular_model.py`

Both models solve the same problem — a programmable knee pendulum with graded
spasticity for the vertical rig — and arrive independently at the same
geometry: `neuromech` uses `incline_deg = -35.0` giving a rest knee angle of
~125°, which is the thigh-inclined geometry settled on earlier today and the
same 125° rest.

What `neuromech/` has that today's model does not:

- **Validation against the reference MATLAB**, with a test file.
- **A LabVIEW RT port**: `model_rt.py` under a fixed 1 ms step, integer ring
  buffer for the delay, `tanh` in place of `erf`, every state an explicit
  scalar, plus `labview/model_step_formula_node.txt`, a JS verifier and two
  test-vector CSVs.
- **Rig setpoint export**: 1 kHz command files with sync pulses, a min-jerk
  lift and a hold (`motor_reference.py`), one CSV per scenario.
- **Iterative learning control** (`ilc.py`) against an identified plant —
  2nd order, 6.35 Hz, ζ = 0.23, measured on the P4/I0.3/D0.03 tune
  *2026-10-26*, i.e. today. The rig is being tuned right now.
- **45 scenarios** including clonus, cogwheel and lead-pipe rigidity,
  contracture, hypotonia, guarding, a mid-swing spasm, a five-drop
  habituation series, nine single-parameter out-of-range probes and four
  operator artefacts (push release, low lift, pre-release wobble, truncated
  recording).
- **Closed-loop MAS calibration**: `build.calibrate_ladder()` bisects λ
  against PT7 targets instead of specifying mechanism magnitudes by hand.
- **Feasibility flags**: rig range, peak speed and an `exceeds_rig_limit`
  column per scenario.

What today's model has that `neuromech/` does not:

- **Coulomb (dry) friction in the passive baseline** (`coulomb_nm = 0.15`).
  `neuromech` has the same mathematical term — `rigid_nm * tanh(w/0.05)` —
  but exposes it as *lead-pipe rigidity*, a pathology knob defaulting to 0.
  Its passive limb therefore has viscous damping only, which is the
  discrepancy that made measured `N` and the linear model disagree (Lin &
  Rymer's amplitude-dependent damping). The rig has real bearing friction;
  this term belongs in the baseline, not only in the pathology set.
- **Anthropometric limb construction** (`limb_from_anthropometry()`, Winter
  fractions from body mass and height). `neuromech` scales inertia by a
  single relative leg-length scalar `s`.
- **`catch_and_hold(arrest_deg)`** — an inverse solve for "the shank falls
  and then stops at 90°", which was the specific severity archetype asked
  for. `neuromech` has a fixed elastic stop (`contract_k`/`contract_deg`) and
  a clasp-knife catch, but no solver for a target arrest angle.
- **Separate velocity, length and force feedback channels** with saturation
  and adaptation. `neuromech` has force feedback for the extensor (`k_f`,
  `k_df`, the published form) and velocity for the flexor and the catch, no
  saturation, and habituation as a scenario-level `gain_scale` series.
- **`channel_ablation()`** — the force-vs-velocity discriminator built from
  De Groote's own first-swing numbers.

Neither supersedes the other. `neuromech/` is the deployable half and should
be the trunk; the four items above are additive and belong in it.

## 6. Constants that disagree between the two models

These must not both stay in the repository:

| quantity | `neuromech` (from the MATLAB) | today's model (from Willaert 2024) |
|---|---|---|
| reflex delay | 50 ms (`GAMMA`) | 80 ms |
| SRS critical stretch | 1.5° (`DTHETA_C`) | 3.0° |
| passive damping | 0.2 | 0.25 + 0.15 N·m Coulomb |
| SRS stiffness | `k_srs · tone`, i.e. proportional to baseline tone | absolute, 60–400 N·m/rad |

On the first two, `neuromech` is traceable to the reference implementation and
should win. On the fourth, `neuromech` is also right on the physiology: SRS
scales with active muscle force, so making it proportional to tone is the
published form and an absolute value is not. On the third, today's model is
right that dry friction must be present.

## 7. Every number in the manifest is stale

`bench/retest-abs` branched from a `main` predating the V0.4 recalibration, so
the whole manifest was scored against different constants and a different
extraction:

| | `bench/retest-abs` | `main` |
|---|---|---|
| `R2n` | 1.0321 | 1.0 |
| `N` | 5.50 | 6.5 |
| `phi_max_ratio` | 0.6386 | 0.6667 |
| `omega_max_n` | 6.7684 | 9.5672 |
| `omega_min_n` | 0.0010 | **−9.4152** |
| `f` | 0.9137 | 0.8839 |
| `area_ratio` | 0.0768 | 0.0945 |

All seven moved, `omega_min_n` changed sign, and the extraction behind it
changed too (`a23606a`). `main` also carries the centred scoring frame
(`03a8cba`, `b44dc7d`) that `retest-abs` does not. So the PT7 column, the
MAS column, the calibrated `ladder.json` and the zone re-grading in §3 above
are all computed on a superseded scorer. The qualitative findings — the
inversion, the healthy-grading pathologies, the dilution of single-parameter
probes — are robust to the constants because they are large effects with
clear mechanisms. The specific numbers are not. `python -m neuromech.build
--recalibrate` on current `main` is the only way to get real ones.

## 8. The ladder targets the superseded threshold table

`neuromech/scenarios.py` sets `MAS_TARGETS` to the centres of the Popović
`_MAS` bands (≤ .12/.28/.44/.60/.78) and bisects λ against them. That table
is now marked "kept for historical comparison only", and today's change made
the data-derived three-zone verdict primary. Under the zones, the five-rung
ladder collapses:

| rung | PT7 | zone |
|---|---|---|
| `A-MAS0`  | 0.060 | healthy |
| `A-MAS1`  | 0.200 | borderline |
| `A-MAS1p` | 0.360 | impaired |
| `A-MAS2`  | 0.520 | impaired |
| `A-MAS3`  | 0.690 | impaired |

Three of the five rungs are indistinguishable in the output the app now
reports. If the ladder is meant to exercise the app's verdict, its targets
should be re-pointed at the zone boundaries (0.1709 and 0.3528) rather than
at MAS band centres — or it should keep five rungs deliberately, to show that
the three-zone output discards severity the score still carries.

## 9. The rig cannot render the library at current limits

`build.py` sets `RIG_LIMIT_DEG = 45.0` and flags each scenario against it.
**42 of 45 exceed it.** Only `C-A0-low` (18°), `C-A0-small-ok` (28°) and
`D-lowlift` (25°) fit. The typical scenario needs −44° to +55° — 99° of
travel — because release is from full extension while the passive leg hangs
~55° away from it. Peak speeds run to 704 °/s (`C-f-high`) and 648 °/s
(`C-A0-high`).

So the scenario set as built is a specification for a rig with roughly ±60°
of travel and ~700 °/s, not for the one the software currently permits.
Either the limits open up, or the library is re-cut around a lower release
height (which changes `A0` and therefore every normalised ratio — the
`D-lowlift` scenario exists to measure exactly that).

## 10. Hygiene problems in `fa3d651`

The commit is 138 files and 1,071,024 insertions. Four things in it want
attention before it goes anywhere near `main`:

1. **50.1 MB of generated output in 100 files** (27 MB packed): every
   `neuromech/out/setpoints/*.csv` (the largest, `C-N-high.csv`, is 1.2 MB),
   `neuromech/out/traces/*.npz`, and `neuromech/report/out/fig/*.png` (one
   873 KB). All regenerable by `python -m neuromech.build`. These are
   `[Gitignore Candidate]` by the project's own hygiene taxonomy.
2. **`tests/test_swing_sign.py` was deleted** inside the WIP commit. It was
   added by `61d5723`, the commit that fixed the `abs()`-fold bug in the
   swing-angle extraction, and it is its regression guard. It is not on
   `main`, so the guard for a shipped fix now exists nowhere.
3. **`mobile-imu-core/tests/fixtures/golden.rs` was re-baselined** — 24
   lines, every `TRIAL_E2E_*` and `E2E_OCK_ANG` constant — bundled into an
   unrelated WIP commit. `main` has since moved this file independently
   (135/123 lines apart). A careless merge resolution here would silently
   roll the Rust↔Python parity fixtures back to pre-centred-frame values.
4. **A garbage file**: `` is_Outputs_cachetest_retest_params_115e959bc4b9.json ` ``
   (note the leading `is_` and the trailing backtick-space). It is the
   artefact of a malformed PowerShell example I supplied in an earlier
   session; it should be deleted, not committed.

Nothing is merged, so `git reset --soft fa3d651^` splits this freely.

## 11. Recommendation

1. Delete the garbage file, restore `tests/test_swing_sign.py`, drop
   `golden.rs` out of this commit, and gitignore `neuromech/out/**` and
   `neuromech/report/out/**`. Re-commit the source as its own commit.
2. Rebase `bench/retest-abs` onto current `main` (or merge `main` into it)
   and re-run `python -m neuromech.build --recalibrate`. Until that happens
   no number in `out/` means anything.
3. Make `neuromech/` the trunk. Port four things into it from
   `rig_neuromuscular_model.py`: Coulomb friction in the passive baseline,
   `limb_from_anthropometry()`, `catch_and_hold(arrest_deg)`, and the
   velocity/length channel split with saturation. Adopt `neuromech`'s 50 ms
   delay and 1.5° critical stretch over today's 80 ms and 3.0°, and its
   tone-proportional SRS over an absolute stiffness.
4. Re-point `MAS_TARGETS` at the zone boundaries, or keep the five rungs and
   report what the three-zone output loses.
5. Treat §2 and §3 as the validation result they are. The scorer inverts
   above λ ≈ 0.85, and clasp-knife, guarding and contracture all read
   healthy. Both are reproducible from committed files today, and neither is
   an artefact of the stale constants.
