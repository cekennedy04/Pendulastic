# Plan: the vertical rig's neuromuscular model and how to validate it

**Date:** 2026-10-25
**Status:** model implemented and self-checked; rig build decisions open
**Code:** `rig_neuromuscular_model.py`, `severity_grading_test.py`
**Companion report:** `docs/reports/2026-10-25-can-the-app-grade-severity.md`

## Why a model and not a waveform library

The rig has to produce severities this cohort does not contain, and it has to
produce *kinds* of severity: a limb caught high that keeps swinging, and a limb
that falls and is then held, at the same clinical grade. A library of drawn
waveforms would produce those two traces and nothing else, and would answer no
question about why they differ. A model parameterised by mechanism produces
them as consequences, and -- the part that turned out to matter -- it lets the
scoring be tested against a severity axis and a mechanism axis separately.
That separation is what showed PT7 responds ten times more to mechanism than to
severity. A waveform library could not have shown it.

## What is implemented and verified

`J*q'' + B*q' + (K + mgl/2)*q = -T_tone - T_SRS - T_reflex(t-tau) - T_endstop - T_capsule`

| term | what it sets | status |
|---|---|---|
| passive `B`, `K`, `m`, `l` | frequency, decay | Bajd & Bowman via `rig_calibration`, integrator agrees with the closed form to 0.63% (frequency) and 0.31% (decay) |
| `T_tone`, constant | the resting plateau, and A0 through it | measured: tone 0 to 13.5 Nm moves the plateau 116.7 -> 168.6 deg and A0 63.0 -> 11.3, with N unchanged at 5.50 |
| `T_SRS`, transient, yields | first-swing truncation | implemented; does NOT reproduce the published claim that it collapses A0 (see below) |
| `T_reflex`, force channel | the number of oscillations | measured: N falls 5.50 -> 4.00 monotonically |
| `T_reflex`, velocity channel | first-swing amplitude | measured: first trough 29.6 -> 2.3, and N NON-monotone |
| `T_reflex`, length channel | a tonic hold at a solved angle | `catch_and_hold(140)` lands the plateau on 140.0 |
| `T_endstop`, exponential | a mechanical arrest | implemented |
| `T_capsule` | hyperextension limit | always on; added after the tone ladder settled at a knee angle of 185 |

Nine profiles ship, and `selftest()` gates every one of them on staying inside
the anatomical range.

## The literature basis, and where we disagree with it

The model was rewritten once after a literature check, because the first draft
was built on velocity feedback and the field says that is the wrong signal.

**Taken and reproduced.** De Groote et al. report that models driven by
muscle-velocity feedback "fail to explain the key pendulum test kinematic
outcomes", and that tone, transient short-range stiffness and delayed *force*
feedback together do -- "the reduction in the number of oscillations was best
reproduced by simulating stretch reflex activity in terms of force, and not
velocity, feedback". Falisse et al. quantify it: force-plus-force-rate feedback
explains R^2 = 0.73 of hamstring activity against 0.46 for velocity-and-length.
`channel_ablation()` runs both channels over the same gain range on the same
limb and reproduces the claim on our own traces: force gives N = 5.50, 5.50,
5.00, 5.00, 4.00, velocity gives 5.50, 5.00, 4.50, 4.50, 5.00. The force
channel is monotone and the velocity channel turns back up. We did not take
this on trust and we did not have to.

**Taken on structure, not on values.** He, Norling & Wang's three-parameter
reflex loop; Le Cavorzin et al.'s threshold / gain / time-lag fit on 8 healthy
and 15 spastic subjects; Riener & Edrich's double-exponential passive moment.
The parameter values in all three are published and were not retrievable, so
nothing here is copied from them.

**Disagreements we are carrying rather than resolving.**

- He finds that "individual changes in the static stretch reflex threshold, in
  the dynamic threshold, or in the gain can not stimulate the whole spectrum of
  spasticity severity". Le Cavorzin finds the opposite: "variation of the
  threshold coefficient alone allowed an overall reproduction of experimental
  data... no subsequent variation of the reflex gain was required."
  `graded_tone` moves tone and gain together, which is He's recipe, and that is
  a choice.
- **SRS does not collapse A0 here.** At 400 Nm/rad with a 3-degree yield, A0
  moves 63.0 -> 62.7: a few degrees of yield can only absorb about 6% of the
  fall's energy. In this model the tone term does that work, because A0 is
  release minus plateau. Reported as a disagreement rather than tuned away --
  for SRS to carry the first swing its yield would have to be far above the few
  degrees the mechanism is described with.
- **SRS is still necessary, for a reason only visible with it switched off.**
  The force channel cannot fire without it: a constant tone gives a constant
  extensor torque, a threshold above the tone is never crossed, and force gains
  from 0.0 to 0.03 produced byte-identical traces. SRS supplies the transient
  the force channel detects. The two terms are coupled, which no source we
  found states.

**Numbers we flagged as thin and did not build on.** The 40.5 ms
electromechanical delay in spasticity rests on a single study of 12 children
and is universally cited; it is the default `delay_s` and nothing depends on
it. Riener & Edrich's knee coefficients could only be obtained second-hand from
two sources that disagree on a sign, so the capsule term uses McFaull &
Lamontagne's directly measured magnitudes instead (under 4 Nm midrange, rising
to 5-86 Nm at 140 degrees of flexion) and says so at the definition.

## Three hard constraints on the rig, from this work

**1. Short-range stiffness cannot be played back in torque mode.** It is a
clutch releasing. `motor_command` reports `torque_step_nm` separately from
slew for exactly this: the tone ladder carries 2.4 to 11.4 Nm instantaneous
steps and the contracture profile carries 16.8 Nm. No torque source reproduces
a step. A profile with a large step needs position control or a physical
element, not a bigger amplifier.

**2. Do not claim to render MAS 1 against 1+.** Measured resistance to passive
movement shows no significant difference between MAS 1, 1+ and 2, and reviews
recommend treating MAS as nominal until that ambiguity is resolved.
`rig_calibration.RIG_GRADES` currently includes "1+". The rig can be graded at
it; it should not be *specified* to produce it as a distinct torque.

**3. Repeated drops will not give identical traces, correctly.** The
clasp-knife inhibition is reported to adapt to repeated stretch. A faithful
catch-and-release profile therefore drifts across trials, and the protocol has
to either model that or rest between drops. Bohannon's four consecutive trials
gave reversal angles of 27.2, 27.0, 26.3, 25.6 degrees -- a 6.1 degree
within-subject spread at ICC 0.96, which is the trial-to-trial noise floor the
rig has to beat to be credible.

## The actuator spec, as the model currently stands

Against a rig dialled to healthy passive mechanics (B = K = 0.5), what the
motor must supply:

| profile | peak torque (Nm) | command slew (Nm/s) | torque step (Nm) | peak speed (deg/s) |
|---|---|---|---|---|
| tone_L0 | 0.0 | 0 | 0.0 | 276 |
| tone_L1 | 3.6 | 122 | 2.4 | 235 |
| tone_L2 | 8.5 | 283 | 5.6 | 178 |
| tone_L3 | 13.5 | 443 | 8.8 | 116 |
| tone_L4 | 18.1 | 573 | 11.4 | 53 |
| catch_release_high | 61.8 | 1854 | 1.2 | 221 |
| catch_and_hold_140 | 12.5 | 185 | 0.1 | 92 |
| contracture_stop | 16.8 | 830 | 16.8 | 276 |

The tone ladder's 0 / 3.6 / 8.5 / 13.5 / 18.1 Nm sits about 2x above Pei et
al.'s per-grade *elbow* ladder (0.9 / 3.5 / 4.2 / 6.9 / 9.8 Nm for MAS 0-4),
which is the right direction and roughly the right factor for a knee carrying a
5 kg shank. That is the only independent check on these magnitudes that exists,
and it is a concordance rather than a calibration.

`catch_release_high` is the demanding one: 62 Nm at 1854 Nm/s. A brief hard
catch is what makes the first stop angle high while leaving energy in the swing,
and there is no cheaper way to get that trace. If the actuator cannot do it,
that waveform family is unavailable and the rig cannot produce the pair the
whole exercise is about.

## The mechanism discriminator, and a protocol proposal

`release_height_probe` sweeps release height and classifies the arrest. The
useful negative result first: the SETTLED angle does not discriminate. It is an
equilibrium, and eight of nine profiles -- contracture and tonic hold included
-- settle within 1 degree of their own plateau however high they were released
from. The plateau cannot tell these mechanisms apart.

The FIRST STOP angle can, on the shape of its dependence on release height:

| profile | slope | residual | verdict |
|---|---|---|---|
| tone_L0 | -0.77 | 0.0 | free, overshoots its rest |
| tone_L2 | -0.79 | 1.0 | free |
| tone_L3 | -0.67 | 9.3 | a threshold fires |
| tone_L4 | -0.48 | 6.9 | resisted |
| catch_release_high | 1.10 | 19.4 | a threshold fires |
| catch_and_hold_140 | 0.01 | 0.4 | pinned by a tonic hold |
| fall_no_ringdown_90 | n/a | n/a | never reverses |
| contracture_stop | -0.40 | 0.6 | resisted, smooth |

A threshold is a kink, and nothing passive makes a kink. Nothing in the app
varies release height and nothing in this corpus does either, so this is a
protocol proposal: drop from several heights and fit the first-stop slope. It
would separate contracture from hyperreflexia on kinematics alone, which no
single trace does.

## SETTLED: the thigh is inclined, and what that costs

`RIG_GEOMETRY = GEOMETRY_THIGH_INCLINED`. Passive rest at a knee angle of 118
degrees, i.e. 62 degrees of flexion, inferred from this cohort's control
excursion (A0 = release minus rest, and the controls' A0 is about 62 degrees
from a 180 degree release). All ten shipped profiles are built on it.

### Consequence 1 -- holds are producible only between 125 and 160 degrees

An extensor reflex arrests the limb SHORT of its rest. It cannot hold it
beyond, because nothing in a spastic limb pulls further into flexion than
gravity already does. `catch_and_hold` solves the tonic gain for a requested
plateau and now refuses outside the reachable band, with the geometry as the
reason rather than a tuning failure:

| hold at | tonic torque | A0 | N | |
|---|---|---|---|---|
| 165 | 15.6 Nm | 15.1 | 0.50 | too high: A0 is below the app's own 25 deg interpretable floor |
| 160 | 13.9 Nm | 20.1 | 0.00 | |
| 150 | 10.6 Nm | 30.1 | 0.00 | |
| 140 | 7.3 Nm | 40.1 | 0.00 | |
| 130 | 4.0 Nm | 50.0 | 0.00 | |
| 125 | 2.3 Nm | 55.0 | 0.00 | deepest hold worth dialling |
| 120 | 0.7 Nm | 60.0 | 0.00 | too close to the rest: a 0.7 Nm hold is a healthy swing |
| 119 and below | -- | -- | -- | past the passive rest: nothing to hold against |

The band is narrow at both ends and for opposite reasons. Above 160 the limb
barely falls and the app refuses the trial on excursion. Below 125 the hold
costs under 2 Nm and leaves A0 near the healthy value, so it is a hold
arithmetically and a normal swing clinically. `HOLD_RANGE_DEG = (125, 160)`.

### Consequence 2 -- "falls and stops near 90" is not producible as a hold

It was the second of the two waveform families asked for, and on this geometry
no hold reaches it. Two producible traces are near it and both now ship, with
the distinction kept visible rather than one of them quietly relabelled:

- **`fall_no_ringdown`** -- critically damped, falls to its rest at 118 and
  arrives without a single oscillation. This is the "falls and then stops"
  shape. The angle is 118 because the mount says so, not 90.
- **`contracture_stop`** -- free fall into an end-range wall that truncates the
  first overshoot, then back to the rest.

If a hold at 90 is genuinely wanted, the mount has to change, and that is a
mechanical decision with a measurement attached, not a parameter.

### Consequence 3 -- `q_rest_deg = 62` is inferred and must be measured

It comes from control A0, not from the rig. Every hold angle in the table above
and the refusal boundary itself move with it, so the first thing to do once the
rig exists is measure its unactuated rest and update the constant. It is one
line and everything downstream re-derives.

### What settling it did to the findings

Adding `catch_and_hold_125` gave intent level 2 a third mechanism, and the
companion report's central number got worse, not better:

| | before | after |
|---|---|---|
| PT7 spread at one intended severity | 8.2x | **9.2x** |
| PT7 signal-to-confound ratio | 0.10 | 0.10 |
| metrics where severity dominates | 7 | 4 |

`A1_deg` and `first_trough_depth` dropped below the line once a third mechanism
was sampled, which is the right lesson about both: they looked safe on two.

And the new profile that is not a severity at all exposed the worst single
result in this work. **`fall_no_ringdown` -- a healthy limb, zero tone, zero
reflex gain, nothing but damping -- scores PT7 1.952 and is graded "4".** The
app's most severe category, awarded to a limb with no spasticity, because the
trace has no oscillations in it. Nothing about tone produced that grade; the
damping did. Whatever else is decided about PT7, a score that reads a mount
property as maximal spasticity cannot be shown to an assessor.

## Validation protocol, following the only precedent that exists

There is no published motorised pendulum-test or Wartenberg simulator. Every
spasticity simulator in print is an elbow, built for manual MAS or Tardieu
training. So the rig is novel in both respects, and the validation design
should borrow from the elbow work rather than invent one.

1. **Dial the unactuated rig to the passive standard and check it first** (R15).
   Compare the measured envelope against `passive_envelope`. Settle Finding 4 of
   the companion report before this: if control knees ring at 1.5 Hz and the rig
   is built to ring at 0.8 Hz, this check fails and should.
2. **Blind clinician grading**, at least two assessors per profile, per the
   existing `rig_calibration` machinery, which refuses to build a mapping that
   has seen an instrument reading (R3). Benchmarks to beat, from the elbow
   simulators: percent agreement 76-86%, Fleiss kappa 0.63-0.65, disclosed
   realism about 7.6/10.
3. **Grade the severity axis on what clinicians actually use.** A neural net
   trained on 28 raters x 34 patients found angle of catch, maximum stretching
   speed and maximum resistance were the parameters driving MAS decisions. The
   rig's three knobs should be those three.
4. **Then, and only then, re-run `severity_grading_test.py`** with the rig's
   graded profiles in place of the model's intended ones. That converts the
   companion report's falsification into a calibration.

## What is NOT in the literature -- gaps this programme could fill

- A motorised pendulum-test simulator. Does not exist.
- A threshold angle (TSRT) for knee extensors or flexors. Never measured;
  the technique exists only for elbow, ankle and wrist.
- A reflex gain for the spastic knee in transferable units. Every model
  optimises it per subject in internal units.
- A hybrid or switching formalism for the pendulum test. Not published.
- A morphological taxonomy of pendulum traces with prevalence data. Not
  published; the nearest review classifies metrics, not shapes. The rig can
  generate the families, which makes "how often does each appear clinically"
  answerable for the first time.
- End-stop contact modelled inside a pendulum trace. Not published, though the
  elbow simulator hardware includes a ROM-reduction setting.

## Decisions needed before the build

1. ~~Thigh inclination.~~ **SETTLED 2026-10-25: inclined, matching the
   corpus.** See the section below; `RIG_GEOMETRY` is that decision and every
   profile is built from it.
2. **Control mode.** Position, torque, or impedance. It decides whether the
   SRS and end-stop profiles are producible at all, and whether a clinician's
   hand on the shank feels a limb or a servo.
3. **Actuator envelope.** 62 Nm at 1854 Nm/s buys the full profile set. Less
   than that drops `catch_release_high`, which is half the point.
4. **Whether to implement clasp-knife adaptation.** Faithful means traces that
   drift across repeated drops. It is physiologically correct and it fights the
   6.1 degree reproducibility target.

## Papers to obtain, in order

The literature pass was done without publisher access, so parameter tables were
unreachable. In priority order: Willaert et al. 2024 (per-subject tone, reflex
gain, SRS and damping for 15 CP + 5 TD children); De Groote et al. (equations
and sweep ranges -- code is on SimTK at `simtk.org/projects/pendulumtest`);
Gonzalez & Marsden et al. 2026, "Modeling spasticity: a systematic review"
(the teaching-simulator gap, 8 months old and directly on target); Pei et al.
2021 (the per-grade torque ladder); He 1997 and Le Cavorzin 2001 (threshold,
gain and time-lag values); Riener & Edrich 1999 (verify the knee coefficients
and the sign).
