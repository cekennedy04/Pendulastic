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

Nine profiles. Five are a graded tone ladder, L0 (healthy) to L4, varying only
baseline muscle tone and reflex gain on the published decomposition. Four are
distinct mechanisms: a catch that releases, a catch that holds, a free fall with
no ring-down, and a mechanical end-stop.

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

| intent 3 | PT7 | grade |
|---|---|---|
| catch_and_hold_140 (falls to 140, held, no ring-down) | 1.925 | **4** |
| tone_L3 (same intended severity, raised tone) | 0.234 | **1** |

Three grades apart, same intended severity. Expressed as a ratio of the two
effects -- the range PT7 covers across the entire severity ladder, divided by
its largest spread among profiles at one severity:

**0.167 / 1.691 = 0.10.** PT7 moves ten times further when the kind of
impairment changes than when its amount goes from none to severe.

## Finding 3 -- every metric that works is unnormalised; every ratio fails

The same signal-to-confound ratio, computed for every quantity the swing
produces, scored and diagnostic alike:

| metric | severity range | mechanism spread | ratio | verdict |
|---|---|---|---|---|
| `P_plus` (first-half goniometric area) | 36.03 | 5.26 | **6.85** | severity dominates |
| `omega_peak_deg_s` | 223.19 | 35.77 | **6.24** | severity dominates |
| `P_total` (total goniometric area) | 76.35 | 25.25 | **3.02** | severity dominates |
| `neutral_deg` (plateau) | 43.96 | 20.55 | 2.14 | severity dominates |
| `first_trough_depth` (F1amp) | 39.83 | 18.68 | 2.13 | severity dominates |
| `A0_deg` | 43.74 | 20.70 | 2.11 | severity dominates |
| `P_minus` | 40.33 | 19.99 | 2.02 | severity dominates |
| `A1_deg` | 83.57 | 47.94 | 1.74 | comparable |
| `omega_max_n` | 1.65 | 1.66 | 0.99 | CONFOUND DOMINATES |
| `omega_min_n` | 1.65 | 1.66 | 0.99 | CONFOUND DOMINATES |
| `phi_max_ratio` | 0.29 | 0.52 | 0.55 | CONFOUND DOMINATES |
| `N` | 1.50 | 5.50 | 0.27 | CONFOUND DOMINATES |
| `R2n` | 0.24 | 1.02 | 0.24 | CONFOUND DOMINATES |
| **`pt7`** | 0.17 | 1.69 | **0.10** | CONFOUND DOMINATES |
| `area_ratio` | 0.01 | 0.93 | **0.01** | CONFOUND DOMINATES |
| `f` | 0.00 | 0.80 | 0.00 | CONFOUND DOMINATES |

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

One of those describes this cohort's control knees and the other does not, and
the gap is almost exactly a factor of two. Three explanations fit and they are
distinguishable:

1. The shank parameters are wrong for these participants. 1.52 Hz needs
   `mgl_c / J` about 3.7x larger -- a shorter or lighter shank-foot complex, or
   a mass centre further from the knee than l/2.
2. `omega_max_n` is not what it is assumed to be here, in units or in
   definition, in which case the 9.5672 constant is sound and this paragraph is
   the error.
3. The constant is right and the standard is wrong for a reason specific to how
   the controls were recorded.

**This is the first thing to check in the morning and it is cheap.**
`_dominant_frequency_hz` is already in `pendulastic_pt_score.py`: run it over
the control trials and compare the answer to 0.799 Hz. One number settles it.

It matters well beyond PT7's thresholds. R15 checks the unactuated rig's
measured envelope against `passive_envelope`'s prediction before any tone is
dialled. If real control knees ring at 1.5 Hz and the rig is built to ring at
0.8 Hz, that check fails and it should -- the rig would be imitating the wrong
limb, and every severity grade obtained on it afterwards would be a grade of
something that is not a leg.

## Finding 5 -- the refusal lands where it hurts

One of nine profiles gets no grade: `tone_L4`, at 33 per cent of the profiles at
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

1. **Settle the 2.2x frequency disagreement** (Finding 4) by running
   `_dominant_frequency_hz` over the control trials against 0.799 Hz. Every
   PT7 threshold depends on it, and so does whether the rig is being built to
   imitate the right limb.
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
   graded blind. The current output is one grade across the full range, and a
   single grade presented as a measurement is worse than no grade.

Items 1 and 2 are safe now. Items 3 to 5 change what the app prints and should
wait for a decision.

## Open questions

- Is the vertical rig's thigh horizontal or inclined? It decides where the
  passive rest sits and therefore which arrest angles are producible at all --
  see `rig_neuromuscular_model.GEOMETRY_THIGH_INCLINED` and
  `GEOMETRY_THIGH_HORIZONTAL`. On the inclined geometry this corpus implies, a
  hold at a knee angle of 90 degrees is unreachable.
- Does the rig's motor do position control, torque control, or impedance
  control? Three profiles contain genuine discontinuities (a short-range
  stiffness yield, an end-stop) that no torque source reproduces.
- Are there ungraded severe cases in the existing recordings? The cohort arm
  cannot test severity without at least one leg above MAS 1.
