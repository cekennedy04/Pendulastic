# Plan: upper-limb tests for the neuromech emulator — elbow pendulum, then wrist imposed stretch (model first)

**Date:** 2026-11-10, revised 2026-11-18 · **Branch:** `feat/neuromech-upper-limb` (off `main`) · **Status:** U1–U4 implemented on this branch; U5 waits on EO1
**Extends:** `docs/plans/2026-10-26-001-plan-neuromech-emulator-unified.md` (its decisions D1–D8 stand; this plan adds E1–E7)
**Scope confirmed by the owner (2026-11-10):** the model comes first; scoring follows the published literature for each joint, not the knee PT score.
**Revised by the owner (2026-11-18):** elbow AND wrist; the Designer UI must take the upper limb; EO1 = the owner fetches the full texts; EO2 = model both protocols, Lin as reference; wrist = motor-imposed stretch (NeuroFlexor-style), not a gravity drop.

---

## 1. Summary

Add an elbow limb to `neuromech/` beside the knee: a forearm model on the existing De Groote core, the elbow outcome measures the literature actually uses, a small elbow scenario library with expected values, and a check that the model reproduces published healthy numbers. Rig playback, the LabVIEW port and any app change are deferred until the model passes that check.

## 2. Why this is not a parameter change

The knee emulator rests on a test with decades of data, a validated scorer and a healthy reference. The elbow has none of the three in usable form. The published elbow pendulum work is one group's apparatus (Lin, Ju, Huang; 2003–2016) plus one small bare-forearm IMU study (de Lima 2023, n = 7, **all stroke patients, MAS 0–2: no healthy controls**).

### 2.1 Knee versus elbow: the pendulum

| | Knee (what `neuromech` has) | Elbow (literature) | Consequence |
|---|---|---|---|
| Swinging segment | shank + foot, about 4.4 kg, J about 0.44 kg·m² (72 kg, 1.75 m, Winter) | forearm + hand, about 1.6 kg, J about 0.07 kg·m² (same person, Winter fractions, to be verified in U1) | inertia about 6× smaller, gravity torque about 4× smaller |
| Hang frequency | 0.80–0.97 Hz; model 0.915 Hz | about 1.0 Hz bare forearm from Winter; de Lima's worked subject (a patient): T about 1.02 s | similar period, far less energy |
| Does gravity produce a fast enough stretch? | yes; the reason 93 % of pendulum studies are on the knee (Rahimi 2020) | not reliably: "small inertia of the forearm" (Lin 2003) | Lin adds a weighted "clock pendulum" accessory; the accessory is part of the test |
| Posture | seated, thigh supported, shank hangs mid-range | not standardised. de Lima: seated, shoulder 0°, elbow 90°, forearm supinated, falls into extension. Lin: apparatus posture not recovered | with the upper arm vertical the gravity rest is full extension, the end of the range, not mid-range as at the knee |
| Start position | one: full extension, stretching the quadriceps | two: from flexion and from extension (Huang 2016); the extended start reads higher tone in every group | the stretched muscle depends on the start; the model needs both |
| Equation used | nonlinear (Lin & Rymer; De Groote) | linear stiffness + linear damping + gravity only (Lin 2003, Huang 2016) | no published nonlinear or reflex elbow pendulum model; ours would be the first, so it must reduce to theirs |

### 2.2 Knee versus elbow: the scoring

| | Knee | Elbow |
|---|---|---|
| Measures | seven kinematic ratios normalised on A0 (`pendulastic_pt_score.py`) | number of swings, relaxation index, and fitted stiffness K, damping B, damping ratio ζ |
| What separates patients | the composite PT score | **B and ζ** (Lin 2003; ζ marginally better), plus swings and RI (Huang 2016). **K does not** separate in any study |
| Relaxation index | A1 / (1.6·A0) | de Lima: F1Amp / plateau amplitude, no 1.6. Lin-group formula not recovered |
| Healthy reference | `HEALTHY_REF`, calibrated on our controls | K 2.62 N·m/rad men, 2.26 women (Lin 2005, n = 192, with apparatus). B and ζ values not recovered |
| Link to MAS | bands in `mas_estimate` | none. ζ "tended to increase with severity" (Lin 2003), no cut-offs; de Lima found no significant correlation; Rahimi 2020 says the mapping "is really missing" |
| Disease specificity | n/a | cannot tell stroke from Parkinson (Huang 2016; Valle 2025) |

**Reading:** the elbow score is a fitted damping ratio with supporting swing count and relaxation index, reported raw. There is no published basis for an elbow PT score or an elbow MAS band, and this plan does not invent one.

## 3. Requirements

- **R1.** An elbow limb is selectable in the model without changing any knee output (`Params()` stays bit-identical, as B9 required).
- **R2.** The elbow model reduces exactly to the literature's linear model (J, B, K, gravity) when tone and reflex terms are zero.
- **R3.** Both published start positions can be simulated, each stretching the correct muscle group.
- **R4.** The accessory (added mass at a set distance) is a model input, and bare forearm is the zero case.
- **R5.** Elbow traces are scored by the literature's measures only: swings, relaxation index, ζ, B, K, natural frequency. No PT score, no MAS band.
- **R6.** Every literature number used is cited with a DOI and marked verified from full text, or second-hand.
- **R7.** The model reproduces published healthy values before any scenario is called expected (gate, §6 U5).

## 4. Decisions proposed (E-series, for the manager to adopt or amend)

- **E1. Same core, new limb.** De Groote's structure (gravity, passive damping, soft range limits, tone, short-range stiffness, delayed reflex) is kept; the forearm gets its own inertia, range and passive K. Rationale: one verified integrator and one Formula Node later, per D1.
- **E2. Passive stiffness is explicit for the elbow.** The knee model has no linear K (gravity centres it). The elbow literature fits a linear K of 2–3 N·m/rad that is comparable to the forearm's gravity stiffness, so it cannot be left out. Default 0 for the knee.
- **E3. Posture and accessory are inputs, not constants** (the D7 rule). Upper-arm inclination, accessory mass and accessory distance are parameters; scenarios name the protocol they follow ("de Lima bare", "Lin apparatus").
- **E4. The primary elbow score is ζ**, with B, K, swings and RI reported beside it. Two estimators are implemented because the literature uses both: log decrement (Valle, de Lima) and least-squares fit of the linear model (Lin).
- **E5. No grade is assigned.** The D5 blindness rule extends to the elbow, and here the literature offers no bands to calibrate to anyway. Severity in the library is a dialled ζ ladder, labelled as such.
- **E6. Severity is rendered through damping-like mechanisms first** (velocity-dependent drive `k_v`, tone-proportional SRS), because B and ζ are what the elbow data show changing while K stays flat. A scenario whose K moves with severity is flagged as outside the published picture.
- **E7. Knee-borrowed elbow work stays where it is.** Groups H and K (HESS catch on the knee) are untouched. A HESS catch on the elbow limb is a later scenario, since HESS models an imposed stretch, not a drop.

## 5. Decisions for the owner

| # | Decision | Options |
|---|---|---|
| EO1 | Full texts | Lin 2003, Lin 2005, Lin 2007 and Huang 2016 are paywalled. U2, U3 and U5 need their apparatus dimensions, start angles, RI formula and per-group B and ζ. Obtain through the library, or accept second-hand values marked provisional |
| EO2 | Which protocol is canonical | Lin's weighted apparatus (the only one with patient-versus-control separation) **or** de Lima's bare forearm (what a phone on the forearm could do with no hardware). Recommended: model both, treat Lin as the reference |
| EO3 | Rig | The knee rig's arm and motor are sized for a shank. An elbow run needs a forearm-scale arm or a scaled playback. Not needed until the model gate passes |

## 6. Implementation units

### U1. Forearm limb from anthropometry
**Goal:** (l_c, m, J) for forearm + hand about the elbow, from body mass and height. **Requirements:** R1, R4.
**Files:** `neuromech/model_rt.py` (extend `limb_from_anthropometry` with a segment argument; knee default unchanged), `neuromech/tests/test_elbow_limb.py`.
**Approach:** Winter (2009) Table 4.1 forearm-and-hand fractions (mass 0.022 body mass, COM 0.682 and radius of gyration about the elbow 0.827 of elbow-to-wrist length, length 0.146 height). These are from memory and must be checked against the table before use. Accessory adds a point mass at a distance.
**Test scenarios:**
- 72 kg, 1.75 m gives a hang frequency near 1.0 Hz, and it is independent of body mass.
- Knee call with no segment argument returns exactly today's values.
- An accessory of mass m at distance d raises J by m·d² and lowers the frequency for d beyond the bare limb's equivalent length.
- A half-specified accessory (mass without distance) raises an error.
**Note for the implementer:** de Lima's inertia formula uses 0.318 × length, which reads as a radius of gyration about the centre of mass rather than about the elbow; their I of about 0.009 kg·m² is roughly 7× below Winter's value about the elbow. That would explain why their K and B sit 4–7× below Lin's. Confirm against the paper; if it holds, de Lima's K and B are not usable as reference values, but their period and ζ are.

### U2. Elbow joint: range, posture, passive stiffness
**Goal:** the passive elbow that reduces to the literature's linear model. **Requirements:** R1, R2, R3. **Depends on:** U1; EO1 for apparatus posture.
**Files:** `neuromech/model_rt.py` (`Params`: linear `k_passive`, range limits as parameters instead of the knee's fixed 0 and −110°), `neuromech/pendulum_sim.py` only if the limit constants live there, `neuromech/tests/test_elbow_limb.py`.
**Approach:** elbow range 0–145° flexion; upper-arm inclination reuses `incline_deg`; `k_passive` about a stated neutral angle; an `elbow_params()` constructor mirroring `rig_params()`.
**Test scenarios:**
- With tone, reflex and limits off, the simulated trace matches the closed-form linear solution for small swings.
- `Params()` (knee) is bit-identical before and after.
- Upper arm vertical, release from 90° flexion: the trace is stopped by the extension limit, not by a mid-range rest (documents the posture problem in §2.1).
- Upper arm horizontal: the forearm rests near 90° flexion and rings about it.

### U3. Stretch direction and the muscle drive
**Goal:** a start from flexion stretches the flexors, a start from extension stretches the extensors. **Requirements:** R3. **Depends on:** U2.
**Files:** `neuromech/model_rt.py` (a sign on the active drive, default the knee's), `neuromech/tests/test_elbow_limb.py`.
**Approach:** the core has one muscle activation state (kept, to keep the Formula Node port 1:1). Which muscle it represents is set per scenario by the start position; tone may differ between the two.
**Test scenarios:**
- Same tone, both starts: the active torque opposes the first swing in each.
- Knee scenarios render identically with the default sign.
- Higher tone on the extension start reproduces Huang 2016's direction (fewer swings, higher fitted B from the extended start).

### U4. Elbow outcome measures
**Goal:** score an elbow trace the way the literature does. **Requirements:** R5, R6. **Depends on:** none (works on any angle trace).
**Files:** `neuromech/elbow_score.py` (new), `neuromech/tests/test_elbow_score.py`.
**Approach:** swings; relaxation index (de Lima's definition now, Lin's when EO1 supplies it, each labelled); ζ, ω, B, K by log decrement (Valle 2025, with K = J·ω², correcting the review's printed "K = J·ω") and by least-squares fit of the linear model. Returns raw values and a refusal reason; never a grade.
**Test scenarios:**
- A synthetic linear trace with known J, B, K: both estimators recover ζ within 2 % and K within 5 %.
- An overdamped trace with no second peak: log decrement refuses, the fit still returns ζ ≥ 1.
- A trace cut before it settles (de Lima's 4–8 s records): flagged as unsettled, plateau-dependent measures withheld.
- The same trace scored with and without gravity in the fit shows how much of "K" is gravity (the two published K scales differ on exactly this).

### U5. Literature gate
**Goal:** show the model matches published healthy numbers before anything is called expected. **Requirements:** R7. **Depends on:** U1–U4, EO1.
**Files:** `neuromech/elbow_validation.py` (new), `neuromech/tests/test_elbow_validation.py`, `docs/reports/` report.
**Approach:** a table of every published value with DOI and verified/second-hand status; simulate the matching protocol; report model versus paper.
**Test scenarios:**
- ~~Bare forearm, healthy passive: period within 10 % of de Lima's worked subject (about 1.02 s).~~ Withdrawn 2026-11-18: de Lima's participants were all stroke patients, so no de Lima value is a healthy reference. de Lima stays a patient comparison only.
- Bare forearm, linearised hang frequency from Winter about 1.0 Hz (implemented, `test_forearm_hang_frequency_near_1hz_and_mass_free`).
- Lin apparatus, healthy: fitted K inside Lin 2005's 2.26–2.62 N·m/rad band (needs apparatus inertia from EO1; skipped with a stated reason until then).
- Raising the velocity-dependent drive raises fitted ζ and B while fitted K moves by less than a stated bound (Lin 2003's central finding).
**Gate:** if the third scenario fails, E6 is wrong and the plan returns to the manager before U6.

### U6. Elbow scenario group
**Goal:** a small library with expected elbow measures. **Requirements:** R4, R5. **Depends on:** U5 passing.
**Files:** `neuromech/scenarios.py` (group `E`), `neuromech/build.py` (score group E with `elbow_score`, never `score_angle`), `neuromech/tests/test_build.py` or the existing scenario tests, `neuromech/README.md`.
**Approach:** healthy; a ζ ladder; flexion and extension starts; bare versus accessory; low damping (Lin 2007's neuropathy finding); rigidity. The knee manifest columns that do not apply are left empty, not zero.
**Test scenarios:**
- Building the library leaves all 67 knee scenarios' manifest rows unchanged.
- Every group E row has the elbow measures and no PT score or band.
- The ζ ladder is monotonic in fitted ζ.

## 7. Deferred to follow-up work

- LabVIEW Formula Node port of the new terms (`k_passive`, range parameters, drive sign) and re-verification to 1e-11.
- Rig playback: arm, range and speed an elbow swing needs; `session.py` and the Player are limb-agnostic already, but the run sheet's 45° guard and the dry run assume the knee.
- Phone on the forearm through `neuromech/phone_trial.py`: the app's release gate (1 rad/s) and settle rule against a faster, smaller swing; whether the app should refuse or report raw measures.
- C# designer and the Rust scorer: no elbow scoring until the Python measures are settled.
- An elbow healthy reference from our own controls (needs data collection; out of scope).
- HESS catch rendered on the elbow limb, in its native imposed-stretch form.

## 8. Risks

- **Thin evidence.** One group's apparatus and one n = 7 study. The plan's gate (U5) can pass on very few numbers. Mitigation: R6's provenance column, and EO1.
- **Protocol ambiguity.** Without Lin's posture and accessory dimensions the "reference" protocol is a guess. U2 and U5 carry explicit skips rather than assumed values.
- **Knee regressions.** Every unit touches `model_rt.py`. Mitigation: the bit-identical `Params()` test in U2 and the unchanged-manifest test in U6.
- **Scorer drift.** Workstream 3 is still porting scorer behaviour. This plan does not call the PT scorer, so it does not depend on those ports.

## 9. Coordination

- This plan adds no entry to `STATUS.md`; the manager session should add an elbow workstream there if it adopts the plan.
- It conflicts with no open PR: #86, #87, #90 and #92 do not touch the limb definition. `neuromech/band-edge-misses` may edit `scenarios.py`; U6 lands after it.
- Suggested order: EO1 (owner) in parallel with U1 and U4, then U2, U3, U5, and U6 only after the gate.

## 10. Sources

Verified from full text unless marked.

- Lin CC, Ju MS, Lin CW. The pendulum test for evaluating spasticity of the elbow joint. Arch Phys Med Rehabil 2003;84:69–74. doi:10.1053/apmr.2003.50066 (full text, 2026-11-18)
- Lin CC, Ju MS, Huang HW. Gender and age effects on elbow joint stiffness in healthy subjects. Arch Phys Med Rehabil 2005;86:82–5. doi:10.1016/j.apmr.2003.12.027 (full text, 2026-11-18)
- Lin CC, Ju MS, Huang HW. Muscle tone in diabetic polyneuropathy evaluated by the quantitative pendulum test. Arch Phys Med Rehabil 2007;88:368–73. doi:10.1016/j.apmr.2006.11.020 (full text, 2026-11-18)
- Huang HW, Ju MS, Lin CC. Flexor and extensor muscle tone evaluated using the quantitative pendulum test in stroke and parkinsonian patients. J Clin Neurosci 2016;27:48–52. doi:10.1016/j.jocn.2015.07.031 (full text, 2026-11-18)
- de Lima MSN, et al. Assessment of passive upper limb stiffness … during the pendulum test. Sensors 2023;23:3487. doi:10.3390/s23073487 (full text, 2026-11-18)
- Valle MS, et al. Applicability of the instrumented pendulum test … a narrative review. Life 2025;15:535. doi:10.3390/life15040535 (full text)
- Rahimi F, et al. Systematic review of the pendulum test. Biomed Eng Online 2020. doi:10.1186/s12938-020-00826-8 (full text)
- Doheny EP, et al. Elbow torque estimated using a nonlinear observer. Acta Mechanica Slovaca 2017. doi:10.21496/ams.2017.003 (abstract only)
- Winter DA. Biomechanics and Motor Control of Human Movement, 4th ed., Table 4.1 p. 86 and Figure 4.1 p. 83 (full text, 2026-11-18; all fractions used match)
- De Groote F, et al. PLOS ONE 2018;13:e0205763 (the existing core)

## 11. Revision 2026-11-18: wrist, Designer, status

### 11.1 What is built on this branch

- **Core** (`model_rt.py`): optional per-limb range walls `lim_lo_deg`/`lim_hi_deg` and a linear passive spring `k_passive` about `k_passive_ref_deg`, recorded as its own `spring` torque (excluded from active torque in `build.py`). All default off; `Params()` is unchanged (tested).
- **U1, U2** (`upper_limb.py`): forearm + hand and hand from Winter fractions (PROVISIONAL, pinned by a test, to check against the printed Table 4.1); accessory point mass; posture as the gravity rest `rest_q_deg` (the elbow flexion at which the forearm hangs vertically; arm at the side = 0; a humerus horizontal with flexion swinging the forearm DOWN, e.g. abducted and internally rotated 90°, = 90; a humerus raised forward in the sagittal plane puts the rest at the extension wall, not mid-range); `elbow_params`, `passive_rest_q_deg` (direct solve), `simulate_elbow` (refuses a release on the wrong side of the rest), `de_lima_bare`. Absolute-angle modules (`contract_*`) are refused because they would not follow the frame mirroring.
- **U3:** instead of a sign on the active drive, each start runs in its own frame (extension start theta = -q, flexion start theta = +q) so the core's tone, SRS and reflex always act on the muscle the start stretches. The Formula Node needs no change for this.
- **U4** (`elbow_score.py`): swings (reversals with hysteresis on a smoothed trace), RI (de Lima form), zeta and wn by log decrement and by a least-squares fit restricted to small amplitudes after Savitzky-Golay smoothing, B and K from J, K_tissue when gravity's stiffness is given. Refuses on a noise floor near the swing threshold, peaks that do not decay at every cycle, a first-order (collinear) decay -- which includes a heavily overdamped swing (zeta about 1.5 or more, U4's original 'fit still returns zeta >= 1' holds only for mildly overdamped, about 1.2) -- a fitted zeta above 5, non-positive fitted K or B, a poor fit (R² < 0.9), and an unsettled trace (two consecutive quiet windows required). Trims any hold before release. No grade.
- **U4 fit modes (review rounds 3-4).** LINEAR mode (|x| <= 15°) refuses an arm that never swings past its rest when it was released outside that range: only the slow mode reaches the window, and the fit read B -47..-73% and K -54..-79% at R² > 0.9999 before this rule. GRAVITY mode (`J`, `k_gravity` = m·g·l_c, and `gravity_zero_deg`, the elbow angle where the forearm hangs vertically) fits |x| <= 60° with gravity removed in its true form about its own zero, so the rest may sit off the vertical; it scores those arms (B, K within 1%, tissue K within 2%, ζ 0.9-2.0, releases 10-40° out) and refuses when the window's two halves disagree on B or K by more than 10% (a stiffening range wall: 0.8). Tissue K is refused when it would be negative. Limits: tissue K is only as good as `k_gravity` (x1.5 too large reads 1.13 for a true 2.5, undetectable); gravity mode refuses heavily damped traces at phone-level noise (≥ 0.02°) on fit quality, so in practice they are refused on noise, not scored.
- **Review round 5 (2026-11-18).** No input found that passes a confidently wrong B or K. Costs and limits, stated plainly:
  - *Refuses some good linear-mode fits.* ζ about 0.75-1.0 released 16-40° out overshoots by less than the 1° swing threshold, so it has no reversal and is refused although its fit is within 2-3%. Kept deliberately: the reviewer's alternative (gate on fitted ζ > 1.05) is unsafe, because with the rest held off the vertical the wrong fits read their own ζ anywhere from 0.89 to 1.04 (ζ 1.5 arm, B -67%, read ζ 1.03), and even underdamped off-vertical arms carry 5-10% K errors in linear mode. Gravity mode scores these arms.
  - *`gravity_zero_deg` must be right to a few degrees on a lightly damped arm.* At ζ 0.3 a 20° error (70 for 90) is accepted with tissue K +7%, B -3%. On a heavily damped arm even a 5° error is refused (split 1.33).
  - *The noise limit hits exactly the target group.* Gravity mode was added for heavily damped (patient-like) arms, and at phone noise (≥ 0.02°) it refuses ζ 1.5 every time and ζ 0.9 from 0.05°. Until a recorded trace shows otherwise, heavily damped patient arms will be refused, not scored, on real sensors. The fix is upstream (sensor noise, filtering before scoring), not a looser gate.
- `model_rt.passive_rest_deg` refuses upper-limb Params (its cache key is knee-only). Pre-existing, knee: that key also ignores an explicit inertia triple, so the anthropometric-leg scenario's rest is computed with the reference leg's inertia. Fixing it changes a knee manifest value, so it is left for an owner decision.

### 11.2 Finding from U2

The reference range wall puts about 1 N·m at the range end, sized for a leg. With the arm at the side (de Lima posture) a forearm released from 90° overshoots about 17° into hyperextension and rests about 12° flexed. Recorded as a strict xfail (`test_arm_at_side_stays_within_physiological_hyperextension`). The same soft wall also adds about 0.17 N·m/rad of stiffness at mid-range (rest 90°, wall at 145°), which a fitted K picks up as if it were tissue. The elbow wall needs its own stiffness, set from a relaxed-hang angle (literature or our own data) at U5.

### 11.3 Wrist (new units W1–W3)

The fact check found no published gravity pendulum test at the wrist (PubMed; Rahimi 2020 covers knee, elbow and ankle only). Wrist tone is measured by moving the hand with a motor: NeuroFlexor (Lindberg PG et al. Neurorehabil Neural Repair 2011;25:617–25, doi:10.1177/1545968311403494). Its model splits the resisting force into inertial, elastic, viscous and neural components; the neural component falls after an ischaemic nerve block, tracks EMG, is velocity dependent, and correlates (with total force) with modified Ashworth.

- **W1. Imposed-stretch simulation.** Given a prescribed wrist angle q(t) (NeuroFlexor: slow and fast passive extensions), return the resisting torque from the same terms: inertia J·q'', damping, linear passive spring (plus a stiffening term if the paper needs it), and the delayed reflex driven by the stretch. Needs a prescribed-motion path through the core's torque function; the active terms must not be duplicated (refactor `derivs` out of `simulate` first, knee bit-identical).
- **W2. Component split.** Recover IC, EC, VC and NC from simulated slow and fast stretches by NeuroFlexor's method, and check the model's own parameters come back.
- **W3. Gate.** Reproduce Lindberg 2011's healthy-versus-patient pattern (needs the full text: stretch amplitudes, speeds, published component values).

### 11.4 Designer (D-units, after U5 and W3)

A limb selector (Knee / Elbow / Wrist) in the C# Designer swaps the segment, the test (drop or imposed stretch) and the score panel. Knee keeps its MAS presets. Elbow dials zeta and shows swings, RI, B, K. Wrist dials the neural component and shows the four components. No MAS grade for either upper-limb joint, because none is published. The C# port of `elbow_score` needs a parity check like `designer/parity`.

### 11.5 Corrections to sources (fact check 2026-11-18)

- de Lima 2023: n = 7 stroke patients (MAS 0–2), no controls.
- Lin & Rymer 1991 (IEEE TBME 38:906–918) model amplitude-dependent passive tissue, not bearing friction; the rig's Coulomb term is our own model.
- He et al. 1997 (IEEE TBME 44:175–184) varied thresholds and reflex gain, not tone; cite De Groote 2018 for scaling tone and reflex gain together (follow-up: `profiles.py` and `scenarios.py` comments).
- Winter Table 4.1: obtained and verified (§11.6).

### 11.6 What the full texts settle (EO1, 2026-11-18)

PDFs are local only (not committed: the repo is public). Values below are as printed.

**Lin apparatus (Lin 2003, p. 70–71; same in 2005, 2007, 2016).** Supine, upper arm on the bed, forearm pointing UP (vertical at 90° flexion). A 107.5 cm steel shaft pivots at its midpoint on the elbow axis; a 1.13 kg weight is fixed to its LOWER end "to increase the total inertia and counterbalance the weight of the forearm"; the wrist is strapped to the upper part through a sliding-rotary joint. Gravity (Eq 3): τg = m_a·g·L_a·sin(θ−π/2) − m_f·g·L_f·sin(θ−π/2): the forearm is an inverted pendulum, the counterweight a hanging one, gravity zero at 90°. Model (Eq 1): I·θ'' = −τg − K(θ−θe) − C·θ', I = I_a + I_f, with θe (tissue rest) fitted. Fitting: whole trajectory by SQP on z = [θe K C]; 10 Hz 4th-order Butterworth; 6 trials, out-of-1-SD trials dropped, rest averaged; 600 Hz, 15–25 s. Release from 130° after a 2-minute hold (Huang 2016 adds a mirror start at 50°). Apparatus alone: K 0, C 0.008 N·m·s/rad. **Not printed anywhere:** the apparatus's total mass m_a and inertia I_a ("estimated by Chen's method"). Lin's damping ratio is η = C / (2√(K·I)) with **tissue K only**, not K + gravity.

**Consequence for our model.** `forearm_from_anthropometry`'s accessory is a point mass on the forearm's own side; Lin's counterweight is on the opposite side and the forearm is inverted. The Lin scenario needs: forearm gravity destabilising, counterweight stabilising, net gravity stiffness m_a·g·L_a − m_f·g·L_f (about 6.0 − 2.2 ≈ 3.8 N·m/rad for 1.13 kg at 0.5375 m and a 64 kg subject), and I dominated by the apparatus (≥ 0.33 kg·m² from the weight alone, plus the shaft, vs about 0.09 for the forearm). I_a must be bracketed (unknown shaft mass), not guessed.

**Healthy reference.**

| Source | n | K (N·m/rad) | C (N·m·s/rad) | Swings | RI |
|---|---|---|---|---|---|
| Lin 2003 (men, mean 59.5 y) | 11 | 1.01–4.42 (range) | 0.11–0.79 (range); η ≈ 0.15 (fig 4D) | ~4 (N4: 4 in ~7 s) | – |
| Lin 2005 men / women | 111 / 81 | 2.62±1.04 / 2.26±0.87 | 0.73±0.40 / 0.53±0.40 | 4.09±1.94 / 5.67±2.41 | 1.45±0.20 / 1.51±0.18 |
| Lin 2005 per body weight W | 192 | K = 0.0373·W + 0.1796 | C = 0.0089·W + 0.1013 | | |
| Lin 2007 controls | 128 | 2.64±0.88 | 0.78±0.44 | 4.10±1.92 | 1.43±0.17 |
| Huang 2016 controls, 130° / 50° start | 22 | ≈2.9 / ≈4.6 (bar chart) | ≈0.8 / ≈1.2 (bar chart) | ≈4.1 / ≈3.1 | ≈1.42 / ≈1.40 |

Lin's C is 2–7× other elbow studies (0.1–0.3, MacKay 1986); the authors attribute part of it to the apparatus (misalignment, friction). So our model's healthy damping cannot be matched to both: an owner decision.

**Measures, definitions recovered.** Swings = number of peaks and troughs of the averaged trace (no threshold printed). RI = maximal swing angle / final steady angle, both measured from the initial position (Lin 2005 fig 1; the 2005 text swaps the S and R definitions, the figures and the 2007/2016 text do not) — the same form as our RI. Lin 2003's spasticity cut-offs (fig 6): C > 0.9 or η > 0.4.

**Patients.** Lin 2003: C and η rise with MAS (r .80, .78), K does not separate. Lin 2007 (diabetic polyneuropathy, n 53): C lower (0.59±0.29), K and swings unchanged, RI higher (1.56). Huang 2016 (stroke 14, PD 21, MAS 0–2): S, R, C separate patients from controls, K does not, stroke and PD not separable; **the 50° (extension) start gives larger K and C in every group, controls included**. Our model mirrors the two starts with identical tissue, so it cannot reproduce that asymmetry: an owner decision.

**de Lima 2023.** n 7 stroke, MAS 0–2, seated, shoulder 0°, elbow 90°, IMU at the wrist, records 4–8 s, no sampling rate printed. No equation of motion: closed-form ω = 2π/T, ζ from the log decrement of the first two flexion reversals, K = I·ω², B = 2ζωI. Inertia printed as I = m·(L × 0.318)², a point mass at 0.318 L: 0.318 is Winter's DISTAL CoM fraction (proximal is 0.682) and there is no radius-of-gyration term, so I is about 7–10× low (0.009 vs about 0.07–0.09 kg·m²). Their worked B also uses an unexplained factor (53.1). **Confirmed: de Lima's K and B are not usable as references; their period and ζ are.** Pendulum variables did not correlate with MAS.

**Winter 4th ed.** Table 4.1 p. 86, Figure 4.1 p. 83: every fraction in `upper_limb.py` matches (forearm + hand 0.022, 0.682, 0.827, length 0.146H; hand 0.006, 0.506, 0.587, 0.108H). The forearm-and-hand row is normalised to elbow-to-ulnar-styloid length. Cross-check: Lin 2007's measured controls (63.9 kg, forearm 30.3 cm) give 1.40 kg / 0.10±0.02 kg·m²; Winter with their length gives 1.41 kg / 0.088 kg·m² (test).

**Lindberg 2011 (the wrist source; file `Biomech_model_spasticity.pdf`).** Wrist extension on a motor platform, about 20° flexion to 30° extension, 5 (slow) and 71, 142, 236 °/s, 400 Hz; 31 stroke, 13 controls. Not an ODE: a force decomposition from three time points, F = elastic + viscous + neural + inertial, with IC = m·a (hand mass 0.6 % body weight), EC at 1 s after the slow stretch (includes the non-linear end-range stiffness), VC at P1 = total − IC and at P2 = 0.2 × that, NC = total at P2 − (EC + VC); gravity corrected for hand and platform. NC falls under nerve block, tracks EMG (r .58–.86), rises with speed in patients only, and with total force correlates with MAS (r > .6); EC and VC do not. Enough to start W1–W3.

**Still open.** Elbow range-wall stiffness (no source gives a relaxed resting angle or end-range stiffness for the elbow); Lin's apparatus mass and inertia; individual-subject traces (only plotted).

### 11.7 U5 result: the Lin-rig gate (2026-11-18)

Owner decisions (2026-11-18): model Lin's rig with the unprinted inertia **bracketed**; damping **split** into tissue (0.2, non-pendulum literature) and a rig term (`LIN_RIG_B` 0.45, sized to Lin's healthy C); **per-start** tissue scale for the extension start (`EXTENSOR_SCALE_HUANG` 1.5, Huang 2016's controls); after the result below, **accept with the limits recorded** and go on to U6.

Built: `upper_limb.lin_apparatus` (inverted forearm + counterweight below the pivot, gravity = counterweight minus forearm, inertia forearm + `rig_j`, releases 130° / 50°), `lin_k_tissue` (Lin 2005's weight regression), `elbow_params(extensor_scale=...)` (default 1: the knee-style mirror model is unchanged), `ElbowScore.eta_lin` (Lin 2003 eq 4, tissue K only), `neuromech/elbow_validation.py` (`python -m neuromech.elbow_validation` prints the table) and its tests.

`LIN_RIG_J_BRACKET` = 0.55–1.15 kg·m², from Lin 2003 fig 4 (η ≈ 0.15 with C ≈ 0.45, K ≈ 2.6 gives I ≈ 0.87) and the plotted healthy traces (first trough ≈ 1.0–1.4 s). 64 kg, 1.65 m subject:

| case | rig J | swings (3°) | RI | first turn s | C fit | K tissue fit | ζ | η (Lin) | refused |
|---|---|---|---|---|---|---|---|---|---|
| healthy, flexion start | 0.55 | 4 | 1.60 | 0.98 | 0.63 | 2.74 | 0.16 | 0.25 | – |
| healthy, flexion start | 1.15 | 7 | 1.70 | 1.37 | 0.63 | 2.75 | 0.11 | 0.17 | – |
| healthy, extension start | 0.55 | 4 | 1.57 | 0.90 | 0.75 | 4.04 | 0.17 | 0.24 | – |
| healthy, extension start | 1.15 | 6 | 1.67 | 1.26 | 0.75 | 4.05 | 0.12 | 0.17 | – |
| k_v 0.2, flexion start | 0.55 | 4 | 1.50 | 0.98 | – | – | – | – | logdec, fit |
| k_v 0.2, flexion start | 1.15 | 6 | 1.62 | 1.37 | 0.77 | 2.80 | 0.14 | 0.21 | – |
| k_v 0.5, flexion start | 0.55 | 3 | 1.38 | 0.98 | 1.08 | 3.02 | 0.27 | 0.40 | logdec |
| k_v 0.5, flexion start | 1.15 | 4 | 1.52 | 1.37 | 1.01 | 2.87 | 0.18 | 0.27 | – |
| k_v 1.0, flexion start | 0.55 | 2 | 1.22 | 1.01 | 1.70 | 3.56 | 0.41 | 0.58 | logdec |
| k_v 1.0, flexion start | 1.15 | 3 | 1.37 | 1.39 | – | – | – | – | logdec, fit |

What each row tests: K is an input (read back 0.17 high, the range wall's leak), C is calibrated, the first-turn time fixed the bracket; **swings and RI are the predictions**, and the k_v ladder tests Lin 2003's central finding.

- **Passes:** healthy swings 4–7 at a 3° count (Lin 4.1–5.7, SD ≈ 2; Lin counted plotted peaks with no printed threshold, our default 1° counts 7–9); η and C below Lin's cut-offs when healthy and above them with a velocity drive (k_v 0.5: C 1.0–1.1 > 0.9; k_v 1.0: η 0.58 > 0.4) while fitted K moves 5–10 % (Lin: K not significant, SD ≈ 40 %); swings and RI fall with the drive; the extension start fits higher K and C (an input, Huang).
- **Mismatch, recorded (strict xfail):** healthy RI 1.57–1.70 vs Lin 1.43–1.51 (SD ≈ 0.2), inside 1.5 SD. Lin's averages cannot all hold in a linear model: RI 1.45 needs a total damping ratio ≈ 0.25, η ≈ 0.15 (tissue K only, so ≥ the total ratio) needs ≤ 0.15: η ≥ ζ whenever gravity stiffness ≥ 0, so η ≤ 0.15 forces RI ≥ 1.62 whatever I and C are (independent review: a grid over I 0.3–2, K 0.5–6, C 0.1–2.5 found no solution without a destabilising gravity term, impossible in this rig). Also, with K 2.6 and the first trough at 1.0–1.4 s, RI 1.45 would need C ≈ 1.0–1.3, above Lin's own 0.9 cut-off. The η ≈ 0.15 (Lin 2003 fig 4, C ≈ 0.45) and the C ≈ 0.73 the rig damping is calibrated to (Lin 2005) are different cohorts, and cohort means need not hold jointly for any subject, so this is a limit of comparing averages as much as of the model. Rig dry friction (probed, `coulomb_nm` 0.1–0.3) fixes swings but barely moves RI, and the scorer correctly refuses those traces as non-linear. Options left open: amplitude-dependent tissue damping, or raw traces (ours or Lin's).
- **De Groote tone + reflex ladder** (tone scaled 0.25 of the knee's): swings 7 → 2 and RI 1.60 → 1.09 in the right direction, but the fit is refused (the rest shifts and the motion is non-linear), so it gives no K or C to compare with Lin. U6 must decide which drive represents elbow spasticity.
- **Still open:** elbow range-wall stiffness; Lin's real rig inertia; whether the elbow's spastic drive is velocity (k_v) or De Groote force feedback.

### 11.8 U6: two drive ladders, hard-onset wall, free-rest probe (2026-11-18)

Owner decisions (after a contrarian review, Gemini unavailable): **two parallel drive ladders** (velocity `k_v`, De Groote), neither primary; a **hard-onset range wall**; a **free-rest-angle probe**, diagnostic only. RI mismatch stays recorded. Not taken: an owner relaxed-hang trial (would pin the wall's end-range value); collapsing the inertia bracket to a fitted point (that would turn swings, the one real prediction, into a calibration).

- **Wall.** `Params.lim_k` (None = the reference K_LIM 3, so the knee is unchanged); the elbow uses `ELBOW_WALL_K` 20/rad (engages in the last ~10–15°). Lin-rig K reads back 1.1–1.6 % low (was +7 % high); any value 10–30 gives the same fits. The arm-at-side overshoot falls 17° → 8° (was a strict xfail, now a test). Scorer-safety tests keep the soft wall.
- **k_v is not cleanly linear.** With the soft wall gone, `k_v` fit refusals turn out erratic in rig inertia (split check 0.02–0.36 for the same drive): it is delayed, rectified and one-sided. The Lin cut-off test now checks every accepted fit over three inertias. On the bare forearm, an accepted `k_v` 0.2 fit reads K 32 % high (the drive leaks into stiffness on a light limb; in Lin's heavy rig 5–10 %).
- **Free-rest probe** (`elbow_validation.fit_free_rest`, Lin's whole-trajectory fit with q_e free). It fits every trace the scorer refuses (R² 0.97–0.997, RMS 0.5–1.2°): the scorer is stricter than Lin, and the De Groote refusal is the rest shift (q_e +3–10°), not gross non-linearity. In Lin's rig it **separates the drives only at full strength**: `k_v` raises C with K +2–23 % (Lin's finding); De Groote at λ 1 raises K +45–70 % (Lin would read it as stiffness) and C to ~2.9, more than `k_v` does. At λ 0.5 De Groote reads K +7–12 %, C 1.04–1.10, overlapping `k_v` 0.5–1.0, so a Lin-style fit cannot tell the families apart at moderate strength. `fit_free_rest` reports `ok` (converged and R² ≥ 0.9); a non-converged fit is not flagged otherwise. Diagnostic only; the scorer is unchanged.
- **Library** (`neuromech/elbow_library.py`, 15 rows, `out/elbow_manifest.csv`): both ladders are monotonic in excursion, peak fall speed, swings and RI; both brake the fall (peak speed 280 → 225 °/s for `k_v`, → 162 °/s for De Groote), so fall speed does not separate them here. Most spastic rows are refused by the scorer (expected; only E-V3, E-V3-ext and E-G1 are accepted). `settled_q_deg` is the tone-shifted rest (E-G4 ~95°); `passive_rest_q_deg` is 90 on every row.
- **Knee provenance:** `model_rt.py` changed, so `model_hash` changes and the next knee build recalibrates the MAS ladder; sessions and rig bundles planned under the old hash are refused by `session.score` without `--allow-provenance-mismatch`. The knee physics is unchanged (`lim_k` None takes the reference wall).
- **Owner decision pending:** the elbow scorer cannot grade most spastic drops in either family. Either keep it strict (model-free measures only for spastic arms) or add a Lin-style free-rest mode as a second, labelled fit.
