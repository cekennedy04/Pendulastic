# Can the app grade severity? No, and the reason is structural

**Date:** 2026-10-25
**Code:** `severity_grading_test.py`, `rig_neuromuscular_model.py`
**Run:** `python severity_grading_test.py`

## The question and why it could not be answered before

The app computes seven Popovic parameters, sums them into PT7, and maps PT7 to
a MAS grade. Whether that mapping ranks severity had never been tested, because
this cohort cannot test it: every clinically graded leg on file sits at MAS 0 or
1, so there is no severe group to compare a mild one against. Published data
cannot test it either -- Whelan et al. graded severity across 131 spastic knees
and every metric landed at AUC 0.59-0.71, which they judged unacceptable.

So the severity axis had to be produced. `rig_neuromuscular_model.py` produces
it, and produces it in a way a single severity knob cannot: parameterised by
MECHANISM, so that two limbs at the same intended severity can have completely
different goniograms. That distinction turns out to be the entire finding.

## What was run

Every waveform goes through the SHIPPED path -- `compute_pt_params` ->
`compute_pt_score` -> `mas_estimate` -- so what follows is a statement about the
app, not about a reimplementation of it. `mas_estimate` rather than `pt_to_mas`
because the refusal is part of the answer: a grader that declines precisely on
the most affected limbs is a healthy-leg detector with a disclaimer.

Ten profiles, all on the rig's settled geometry (thigh inclined, passive rest
at a knee angle of 118 degrees). Five are a graded tone ladder, L0 (healthy) to
L4, varying only baseline muscle tone and reflex gain on the published
decomposition. Five are distinct mechanisms: a catch that releases, two holds at
different plateaus, a critically damped fall with no ring-down, and a mechanical
end-stop.

## Finding 1 -- across the whole severity range, the app prints one grade

| profile | A0 | plateau | first trough | N | PT7 | grade |
|---|---|---|---|---|---|---|
| tone_L0 (healthy) | 63.0 | 116.7 | 46.7 | 5.50 | 0.204 | **1** |
| tone_L1 | 53.8 | 126.0 | 39.7 | 5.50 | 0.205 | **1** |
| tone_L2 | 41.6 | 138.3 | 29.8 | 5.50 | 0.212 | **1** |
| tone_L3 | 29.3 | 150.6 | 18.7 | 5.50 | 0.234 | **1** |
| tone_L4 | 19.3 | 160.7 | 6.8 | 4.00 | 0.371 | **REFUSED** |

Peak excursion falls by 70 per cent. The resting plateau moves 44 degrees. The
first flexion amplitude falls by 85 per cent. The app's output does not change.
Levels 0 through 3 all come out "1" -- the healthy limb and the MAS-3-intent
limb get the same verdict -- and level 4 is refused by the excursion gate.

PT7 is not non-monotone here. Over this ladder it is perfectly monotone,
rho = 1.00. It is simply **flat**: 0.204 to 0.371, entirely inside the single
threshold band that reports "1" (the bands are 0.12 / 0.28 / 0.44 / 0.60 /
0.78). A correlation of 1.00 and a constant output are not in tension; a
monotone metric compressed inside one band produces exactly this.

## Finding 2 -- PT7 responds 8x more to the KIND of impairment than to its amount

At one intended severity, changing only the mechanism:

| intent 2 | PT7 | grade |
|---|---|---|
| catch_and_hold_125 (falls to 125, held, no ring-down) | 1.950 | **4** |
| catch_release_high (caught at 150.6, keeps swinging) | 0.316 | **1+** |
| tone_L2 (same intended severity, raised tone) | 0.212 | **1** |

| intent 3 | PT7 | grade |
|---|---|---|
| catch_and_hold_140 | 1.925 | **4** |
| tone_L3 | 0.234 | **1** |

A 9.2x spread and three different grades at one intended severity. Expressed as
a ratio of the two effects -- the range PT7 covers across the entire severity
ladder, divided by its largest spread among profiles at one severity:

**0.167 / 1.738 = 0.10.** PT7 moves ten times further when the kind of
impairment changes than when its amount goes from none to severe.

## Finding 3 -- every metric that works is unnormalised; every ratio fails

The same signal-to-confound ratio, computed for every quantity the swing
produces, scored and diagnostic alike:

| metric | severity range | mechanism spread | ratio | verdict |
|---|---|---|---|---|
| `P_plus` (first-half goniometric area) | 36.03 | 7.16 | **5.03** | severity dominates |
| `neutral_deg` (plateau) | 43.96 | 20.55 | **2.14** | severity dominates |
| `A0_deg` | 43.74 | 20.70 | **2.11** | severity dominates |
| `omega_peak_deg_s` | 223.19 | 107.91 | **2.07** | severity dominates |
| `P_total` (total goniometric area) | 76.35 | 38.41 | 1.99 | comparable |
| `first_trough_depth` (F1amp) | 39.83 | 29.83 | 1.34 | comparable |
| `P_minus` | 40.33 | 31.26 | 1.29 | comparable |
| `A1_deg` | 83.57 | 88.20 | 0.95 | CONFOUND DOMINATES |
| `omega_max_n` | 1.65 | 2.36 | 0.70 | CONFOUND DOMINATES |
| `omega_min_n` | 1.65 | 2.36 | 0.70 | CONFOUND DOMINATES |
| `phi_max_ratio` | 0.29 | 0.58 | 0.50 | CONFOUND DOMINATES |
| `N` | 1.50 | 5.50 | 0.27 | CONFOUND DOMINATES |
| `R2n` | 0.24 | 1.07 | 0.22 | CONFOUND DOMINATES |
| **`pt7`** | 0.17 | 1.74 | **0.10** | CONFOUND DOMINATES |
| `area_ratio` | 0.01 | 0.94 | **0.01** | CONFOUND DOMINATES |
| `f` | 0.00 | 0.81 | 0.00 | CONFOUND DOMINATES |

These numbers moved when the rig geometry was settled and a third mechanism was
added at intended severity 2, and the direction is worth recording: `A1_deg`
fell from 1.74 to 0.95 and `first_trough_depth` from 2.13 to 1.34. Both looked
safe when only two mechanisms were sampled. Four metrics still clear the line;
only `P_plus` clears it with real margin. An estimate of the confound built on
two mechanisms is optimistic, and an estimate built on five is still a lower
bound.

The split is exact and it is not about which parameter: **every metric where
severity dominates is an unnormalised amplitude, velocity or area. Every metric
normalised on the swing itself is confound-dominated.** Four of the seven PT7
parameters divide by A0; the other three are shape ratios. There is no scored
parameter above the line.

### The cleanest version of the same fact

`P_plus` and `P_minus` are the two halves of the goniometric area. Across the
ladder:

| level | P_plus | P_minus | area_ratio | PT7 |
|---|---|---|---|---|
| L0 | 43.33 | 48.67 | 0.058 | 0.204 |
| L1 | 36.94 | 41.46 | 0.058 | 0.205 |
| L2 | 27.87 | 31.26 | 0.057 | 0.212 |
| L3 | 17.76 | 20.05 | 0.061 | 0.234 |
| L4 | 7.30 | 8.35 | 0.067 | 0.371 |

Both halves fall by a factor of six, monotonically, rho = -1.00. Their ratio is
flat to within 0.01 and non-monotone, and it is the ratio that PT7 scores. Two
quantities carrying the best severity signal in the panel, combined into the
worst.

This is not a new idea, it is a measurement of a published one. Whelan et al.
found their best classifier used first-flexion amplitude and plateau angle
**independently** rather than the relaxation index, which is their ratio. Hernot
and Le Cavorzin et al., computing reflex torque in 15 spastic and 10 healthy
subjects, found the relaxation index showed "an unsuitable U-shaped pattern of
variation with increasing reflex-mediated torque" while "the area under the
curve revealed a linear regression", and recommended replacing RI with "the area
under the goniometric curve". `P_total` and `P_plus` are that area, they are
already computed by `compute_pt_params`, and nothing scores them.

## Finding 4 -- the healthy standard and the healthy reference disagree by 2.2x

`tone_L0` is zero tone, zero short-range stiffness, zero reflex gain: the Bajd &
Bowman passive standard that `rig_calibration.passive_envelope` describes and
that R15 dials the unactuated rig to. It scores PT7 0.204 and is graded **"1"**.
`PT_HEALTHY_MAX` is 0.1709 and the MAS-0 band ends at 0.12. So the app does not
call our own healthy standard healthy.

The per-parameter breakdown says where that comes from, and it is not where it
looks like it should be:

| parameter | contribution | model | `HEALTHY_REF` |
|---|---|---|---|
| `omega_max_n` | 0.0775 | 4.378 | 9.567 |
| `omega_min_n` | 0.0764 | -4.378 | -9.415 |
| `N` | 0.0220 | 5.50 | 6.5 |
| `phi_max_ratio` | 0.0148 | 0.598 | 0.667 |
| `f` | 0.0138 | 0.799 | 0.884 |
| `R2n` | 0.0000 | 1.088 | 1.0 |
| `area_ratio` | 0.0000 | 0.058 | 0.095 |

Three quarters of the penalty is the two velocity terms, and they are off by a
factor of 2.2 in the same direction. `omega_max_n` is peak angular velocity
divided by A0, which for a ring-down is essentially the damped angular
frequency. So this is a FREQUENCY disagreement:

- Bajd & Bowman's parameters (B = K = 0.5, m = 5 kg, l = 0.6 m, J = ml^2/3)
  give a damped frequency of **0.799 Hz**, which the integrator reproduces to
  0.63 per cent. That is just the rod-pendulum result, sqrt(mgl/2J) with the
  mass centre at l/2.
- `HEALTHY_REF["omega_max_n"] = 9.5672`, measured on 46 OptiTrack control
  trials, implies 9.57 rad/s, i.e. **1.52 Hz**.

**ANSWERED 2026-10-26. `omega_max_n` and `omega_min_n` are the wrong
constants.** See `healthy_ref_frequency_audit.py`, which runs the whole chain.
Three independent lines agree:

1. *The physics is robust and mass cancels.* For a rod about its end
   omega = sqrt(3g/2l); for Dempster/Winter's shank-foot segment (mass centre
   0.606L from the knee, radius of gyration 0.735L about it)
   omega = sqrt(1.122g/L). Mass appears top and bottom both times, so the
   prediction does not depend on the table or the body size: 0.79-0.97 Hz.
   Reaching 1.52 Hz needs K = 17-40 Nm/rad against a healthy knee carrying
   under ~4 Nm through midrange, or a shank of 0.12-0.16 m.
2. *Two independent measured velocities agree with the physics, not the
   constant.* **CONFIRMED 2026-10-26 from the papers themselves**, once network
   access was opened and the pages could be read rather than summarised:

   | quantity | value | source |
   |---|---|---|
   | peak first-swing velocity | **241.7 +/- 17.7 deg/s** | PMC3544505, 10 control adults, unnormalised, EMG-monitored |
   | peak first-swing velocity | 221.9 +/- 35.6 deg/s | Kim 1996, n = 50 |
   | mean velocity, first flexion | 157.6 +/- 11.4 deg/s | PMC3544505 controls |
   | relaxation index | 1.91 +/- 0.16 / 1.678 +/- 0.090 | PMC3544505 / Kim |
   | first swing, onset to first flexion peak | **735 +/- 87 ms** | PMC3544505 |
   | damping B, absolute | 0.18 +/- 0.08 flexion, -0.31 +/- 0.09 extension Nms/rad | PMC3544505 controls |

   That 735 ms is a HALF period -- the paper states the time axis is "the
   interval time between movement onset and the first flexion peak" -- so the
   full period is 1.47 s and **the measured healthy frequency is 0.68 Hz**.
   `omega_max_n` from these numbers is **~4.3**, against this model's 4.38 and
   `HEALTHY_REF`'s 9.5672. The constant needs 446 deg/s at A0 = 46.6, which is
   **+11.5 SD** from the measured 241.7.

   **One paper appeared to vindicate the constant, and it is wrong by a factor
   of two.** Casabona et al.'s rheumatoid-arthritis pendulum paper reports
   control stiffness 1.26-1.34 N/rad m^4 normalised by stature^5; denormalised
   at their controls' 1.625 m that is **14.3-15.2 Nm/rad**, twenty-nine times
   Bajd & Bowman's 0.5, which would put the healthy frequency near 1.36 Hz and
   make the constant defensible. The denormalisation is sound -- checked
   against the same group's unnormalised viscosity, 0.113-0.601 denormalised
   against 0.18-0.31 measured, overlapping. But their equation
   `omega = sqrt(K'/J) = 2*pi/T` defines T as "the period of one cycle" and
   their methods then apply it per HALF-cycle. Evaluating it on the half-cycle
   doubles omega, so `K' = J*omega^2` is four times too large -- and
   reconstructing it that way reproduces **14.27 Nm/rad against their published
   14.3**. With the correct full period, K lands at or below zero, i.e. 0.5
   within the error of the anthropometry.
3. *The quantity is not reproducible in the real pipeline.* The `_sg` window
   note in `pendulastic_pt_score.py` records that decimating real 120 Hz
   OptiTrack to 20 Hz -- changing the sampling and nothing else about the
   motion -- moved the median `omega_max_n` from **8.50 to 3.19** across 205
   trials. A quantity that halves when the same motion is resampled cannot be
   a property of a leg, whatever causes it.

**A mechanism claim here is RETRACTED.** An earlier version of this section
blamed the frame rate and the differentiated noise floor. Tested inside the
model, sampling rate does almost nothing: `omega_max_n` reads 4.324 at 20 Hz
and 4.381 at 240 Hz, and with 2 deg of noise only spans 4.29-5.18. The model
cannot reproduce the real pipeline's 2.7x move, so **the mechanism is
unidentified** -- `healthy_ref_frequency_audit.py` part_c only bounds what it
is not (noise, spikes, release steps and marker freezes all fall short). That
matters beyond this constant: until the cause is named, nobody can say which
other parameters it touched.

Nor does the obvious rescue work. Fowler defines the relaxation-index
denominator as starting angle minus resting angle -- exactly this project's
A0 -- and notes the first-swing excursion exceeds it, healthy RI being above
1. True, and the model already reproduces it (RI 1.74 against a measured
1.678 +/- 0.090); with that built in `omega_max_n` is still 4.38, because for
a limb released from rest the peak-velocity-to-A0 ratio is a shade *under* the
damped angular frequency, not RI times it.

And Popovic's 11-17 rad/s, flagged as unreconciled in the first write-up, is
no longer a puzzle worth carrying: that healthy range traces to **n = 6**
healthy subjects in a retrospective study whose authors state it was
underpowered. It is not a normative range, and an independent n = 50 sample
measured 3.873 +/- 0.622 rad/s.

So the Bajd & Bowman passive set survives and the rig should be built to it.
Two consequences follow immediately. These two parameters supplied 0.154 of the
0.204 PT7 the healthy limb is penalised -- three quarters of Finding 4's
original symptom. And because `omega_max_n` rises with noise while being
penalised only when BELOW the reference, **a noisier trace scores healthier**,
which is the mechanism behind the non-monotone noise sensitivity measured on
this branch and a direct problem for phone-against-optical agreement.

The replacement values are NOT settled, and the audit argues they should not
be: a quantity that cannot be stated without also stating the frame rate is
not a property of a leg, which is a reason to stop scoring these two rather
than recalibrate them.

The original three explanations, kept because the first is now the answer:

1. The shank parameters are wrong for these participants. 1.52 Hz needs
   `mgl_c / J` about 3.7x larger -- a shorter or lighter shank-foot complex, or
   a mass centre further from the knee than l/2.
2. `omega_max_n` is not what it is assumed to be here, in units or in
   definition, in which case the 9.5672 constant is sound and this paragraph is
   the error.
3. The constant is right and the standard is wrong for a reason specific to how
   the controls were recorded.

**This is the first thing to check in the morning, and the check now runs
itself.** `severity_grading_test.py --cohort` measures the ring-down frequency
of every scored trial, reports the non-spastic median against the standard's
0.799 Hz, and says which of the two is wrong. On the synthetic fixture (whose
waveforms come from the standard) it reports 0.786 Hz, a ratio of 0.98, and
concludes the standard describes those knees -- so the machinery is known to
give the right answer when the answer is known. Run it on the real recordings
and it gives the real one.

If the controls come back near 0.8 Hz, `HEALTHY_REF`'s two velocity constants
are wrong by 2.2x and every PT7 value this project has ever reported carries
that error. If they come back near 1.5 Hz, the shank parameters are wrong and
the rig must not be built to them.

It matters well beyond PT7's thresholds. R15 checks the unactuated rig's
measured envelope against `passive_envelope`'s prediction before any tone is
dialled. If real control knees ring at 1.5 Hz and the rig is built to ring at
0.8 Hz, that check fails and it should -- the rig would be imitating the wrong
limb, and every severity grade obtained on it afterwards would be a grade of
something that is not a leg.

## Finding 5 -- a healthy limb with no tone at all is graded "4"

`fall_no_ringdown` is a healthy limb: zero tone, zero short-range stiffness,
zero reflex gain, no end-stop. The only thing changed from `tone_L0` is the
damping, raised to critical so the limb falls to its rest and arrives without
oscillating. It is not a severity and it is not in `SEVERITY_INTENT`.

**It scores PT7 1.952 and the app grades it "4"** -- the most severe category on
the scale -- alongside `catch_and_hold_125` at 1.950 and `catch_and_hold_140` at
1.925, which are the actual severe profiles.

The mechanism is the same one behind every other finding here. With no
oscillations there is no second peak and no return velocity, so the ratios built
on them collapse to zero and every one-sided penalty maxes out. The score is
responding to the ABSENCE of a ring-down, and a ring-down can be absent because
a limb is rigid or because it is damped, which are not the same clinical object
and are not even the same organ -- damping at the knee is as much a property of
the mount, the cushion and the trouser as of the leg.

This is the single result that should stop a severity grade being shown to an
assessor, independently of everything above. A score that awards its maximum to
a healthy limb for a reason outside the limb is not conservative in one
direction; it is wrong in the direction that matters, and the error is invisible
on the trace because the trace looks calm.

## Finding 6 -- the refusal lands where it hurts

One of ten profiles gets no grade: `tone_L4`, at 33 per cent of the profiles at
intended severity 3 or 4. The excursion gate is doing what it was designed to do
and it is the right behaviour -- the alternative is reporting a barely-moving leg
as mild. But it means the app's severity coverage has a ceiling built into it,
and the ceiling sits exactly where severity assessment matters.

## What this does and does not establish

It establishes a falsification, which stands on its own: the shipped scoring
cannot rank these severities and cannot separate this healthy limb from this
severe one. A falsification does not need the severities to be clinician-graded,
only to be different, and they are different by construction.

It does not establish that the replacement metrics work on people. The severity
labels here are intended, not graded. `omega_peak_deg_s` at 6.24 and `P_plus` at
6.85 are promising against a model, and a model is not a limb.

It also does not test the cohort. The cohort arm of the harness runs only where
`OptiTrack_Recordings/` is, reports a stage-by-stage funnel, and can test healthy
against affected but never severity -- for that, every graded leg would have to
not be MAS 0 or 1.

## Recommended changes, in order of confidence

1. ~~Settle the 2.2x frequency disagreement~~ **SETTLED: drop `omega_max_n`
   and `omega_min_n` from the scored set.** They are frame-rate artifacts, not
   limb properties (Finding 4). This also removes three quarters of the
   healthy limb's PT7 penalty and the "noisier scores healthier" failure mode.
   Run `python healthy_ref_frequency_audit.py` for the chain, and the cohort
   arm's frequency block to confirm it on the real trials.
2. **Report `P_plus` and `P_total` alongside PT7.** Already computed, highest
   signal-to-confound in the panel, and recommended in print for this exact
   purpose. Costs nothing to surface.
3. **Add `first_trough_depth` and `neutral_deg` as scored parameters.** They are
   the literature's two best discriminators (AUC 0.807 and 0.691, and their
   best classifier is those two independently), and both clear the
   severity-dominates line here.
4. **Stop scoring `area_ratio` and `f`.** Ratios 0.01 and 0.00. `area_ratio` is
   the ratio of the panel's two best metrics and destroys both.
5. **Do not report a severity grade from PT7 at all** until the rig has been
   graded blind. The current output is one grade across the full range, a
   single grade presented as a measurement is worse than no grade, and
   Finding 5 is on its own sufficient: the scale's maximum is awarded to a
   healthy limb for a property of the mount.

Items 1 and 2 are safe now. Items 3 to 5 change what the app prints and should
wait for a decision.

## Open questions

- ~~Is the vertical rig's thigh horizontal or inclined?~~ **Settled
  2026-10-25: inclined, matching the corpus.** Holds are producible only
  between knee angles of 125 and 160, and "falls and stops near 90" is not
  producible as a hold at all. See the plan document's settled-geometry
  section.
- Does the rig's motor do position control, torque control, or impedance
  control? Three profiles contain genuine discontinuities (a short-range
  stiffness yield, an end-stop) that no torque source reproduces.
- Are there ungraded severe cases in the existing recordings? The cohort arm
  cannot test severity without at least one leg above MAS 1.
