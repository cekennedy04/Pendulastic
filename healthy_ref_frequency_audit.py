"""Which constant is wrong: HEALTHY_REF's velocity terms, or the passive model?

The contradiction
-----------------
Two numbers this project relies on cannot both be right.

    rig_calibration.passive_envelope(), the Bajd & Bowman (1982) parameter set
    as reported by Popovic & Bajd (2018) -- B = K = 0.5, m = 5 kg, l = 0.6 m,
    J = m*l^2/3 -- predicts a damped frequency of 0.799 Hz.

    pendulastic_pt_score.HEALTHY_REF["omega_max_n"] = 9.5672, measured on 46
    OptiTrack control trials, is peak angular velocity (deg/s) divided by peak
    excursion A0 (deg). For a ring-down that ratio IS the angular frequency in
    rad/s, so it implies 1.523 Hz.

A factor of 2.2. The passive set is what R15 dials the unactuated rig to and
HEALTHY_REF is what every PT7 value ever reported here was scored against, so
the answer decides both a code fix and a hardware spec.

This module answers it by elimination and by inversion, and both halves are
runnable so neither has to be taken on description.

What it establishes
-------------------
PART A -- the frequency is pinned by segment LENGTH, and mass cancels. For a
uniform rod about one end, omega = sqrt(3g/2l). For Winter's foot+leg segment
(mass centre 0.606L from the knee, radius of gyration 0.735L about it),
omega = sqrt(1.122g/L). Mass appears in numerator and denominator both times.
So the prediction is robust to every anthropometric table and to body size:
three independent routes land at 0.80 to 0.97 Hz.

PART B -- inverting, 1.523 Hz is unreachable from either direction. It needs
K between 17 and 40 Nm/rad, against a healthy knee that carries under about
4 Nm through midrange, or a shank 0.12 to 0.16 m long, which is not an adult.

PART C -- four measurement artifacts that could have inflated the measured
ratio, each tested on model traces where the true frequency is known. None
reaches 9.57:

    Gaussian angle noise     4.38 -> 6.01 at 4 deg RMS (an order of magnitude
                             above optical marker noise)
    isolated spikes          4.38 -> 4.45 at 30 spikes of 10 deg, and
                             omega_peak does not move at all
    release step/push/freeze 4.38 -> 5.85, and only under a 600 deg/s push
                             lasting 60 ms, which is not a passive release
    broadband 8 deg RMS      mean 5.84, but individual draws span 1.48 to
                             12.53 -- they STRADDLE the constant, because at
                             that level N jumps around and trials start
                             coming back unscorable. So noise at IMU scale
                             can produce 9.57 on one trial. It cannot produce
                             it as a median over 46, which is what
                             HEALTHY_REF is: that needs systematically high
                             noise, and 8 deg is an IMU figure while these
                             were optical trials

PART D -- the two constants that should agree come from different trial
subsets, and their arithmetic does not close. At the physics-predicted peak
velocity for A0 = 46.6 deg, reading 9.5672 needs A0 = 21.3 deg. At A0 = 46.6,
it needs a peak velocity of 446 deg/s where the physics gives about 204.

What it does NOT establish
--------------------------
Why the measurement came out at 9.5672. Elimination narrows the cause; it does
not name it. And one published number still has to be reconciled: this
project's own notes record Popovic's healthy omega-max as 11-17 rad/s
UNNORMALISED, which is 2.3 to 3.5 times what the same authors' passive
parameter set predicts. If that is really peak angular velocity then an
independent published source disagrees with the physics too, and that needs
explaining before any constant is called wrong. Until it is, this module
reports a contradiction and its arithmetic, not a verdict.

Run:  python healthy_ref_frequency_audit.py
"""
from __future__ import annotations

import math
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import numpy as np

import pendulastic_pt_score as pts
import rig_calibration as rig
import rig_neuromuscular_model as rnm

G = 9.81

#: Disputed constant, and the frequency it implies if it is a frequency.
DISPUTED = pts.HEALTHY_REF["omega_max_n"]

#: Winter's foot+leg segment, as fractions. Mass of body mass; the rest of
#: segment length, where the segment is knee to ankle and length is about
#: 0.246 of stature.
WINTER_MASS_FRAC = 0.061
WINTER_COM_FRAC = 0.606          # from the proximal (knee) end
WINTER_KPROX_FRAC = 0.735        # radius of gyration about the proximal end
WINTER_SHANK_OF_HEIGHT = 0.246


def _omega(m, d, J, K):
    """Natural angular frequency of a pendulum with joint stiffness K."""
    return math.sqrt((K + m * G * d) / J)


def parameter_sets():
    """The candidate limbs, as (label, m, d, J) with d the mass-centre arm."""
    out = []
    m, l = 5.0, 0.60
    out.append(("Bajd & Bowman / project (rod, l=0.60)", m, l / 2, m * l * l / 3))
    m, l = 5.0, 0.42
    out.append(("same rod form, realistic l=0.42", m, l / 2, m * l * l / 3))
    for M, H in ((70.0, 1.70), (85.0, 1.80)):
        mm = WINTER_MASS_FRAC * M
        L = WINTER_SHANK_OF_HEIGHT * H
        out.append((f"Winter foot+leg, {M:.0f}kg/{H:.2f}m (L={L:.2f})",
                    mm, WINTER_COM_FRAC * L, mm * (WINTER_KPROX_FRAC * L) ** 2))
    return out


def part_a(verbose=True):
    """What each plausible limb predicts, at healthy stiffness."""
    rows = []
    for label, m, d, J in parameter_sets():
        w = _omega(m, d, J, rig.PASSIVE_STIFFNESS_NM_PER_RAD)
        rows.append({"label": label, "m": m, "d": d, "J": J,
                     "omega_rad_s": w, "f_hz": w / (2 * math.pi)})
    if verbose:
        print("A. What frequency does each parameter set predict? "
              "(K = 0.5 Nm/rad)")
        print(f"{'source':38s} {'m kg':>6s} {'d m':>6s} {'J kgm2':>8s} "
              f"{'f Hz':>7s} {'om rad/s':>9s}")
        print("-" * 80)
        for r in rows:
            print(f"{r['label']:38s} {r['m']:6.2f} {r['d']:6.3f} {r['J']:8.3f} "
                  f"{r['f_hz']:7.3f} {r['omega_rad_s']:9.2f}")
        print("  Mass CANCELS -- rod form omega = sqrt(3g/2l), Winter form")
        print("  omega = sqrt(1.122g/L) -- so frequency is set by segment "
              "length alone and")
        print("  the prediction does not depend on which table or which body "
              "size is used.")
        # The closed form this project already ships must agree with row 1.
        env = rig.passive_envelope()
        print(f"  cross-check: rig_calibration.passive_envelope() gives "
              f"{env['damped_freq_hz']:.3f} Hz (damped)")
    return rows


def part_b(target_hz=None, verbose=True):
    """What it would take to reach the disputed frequency."""
    w_t = DISPUTED if target_hz is None else 2 * math.pi * target_hz
    rows = []
    for label, m, d, J in parameter_sets():
        k_need = J * w_t ** 2 - m * G * d
        l_need = (3 * G / (2 * w_t ** 2) if "rod" in label
                  else 1.122 * G / (w_t ** 2))
        rows.append({"label": label, "K_needed_nm_per_rad": k_need,
                     "L_needed_m": l_need})
    if verbose:
        print()
        print(f"B. Inverting: what would reach {w_t / (2 * math.pi):.3f} Hz?")
        for r in rows:
            print(f"  {r['label']:38s} K = {r['K_needed_nm_per_rad']:6.1f} "
                  f"Nm/rad   or L = {r['L_needed_m']:.3f} m")
        print("  A healthy knee carries under about 4 Nm through midrange "
              "(McFaull &")
        print("  Lamontagne); measured plantarflexor stiffness runs 2.8 "
              "typical to 7.1 in")
        print("  CP (Ahblom). 17-40 Nm/rad is not a healthy limb, and a "
              "0.12-0.16 m shank")
        print("  is not an adult. Unreachable from either direction.")
    return rows


def part_c(verbose=True, reps=10):
    """Four artifacts, each tested where the true frequency is known."""
    sim = rnm.simulate(rnm.PROFILES["tone_L0"])
    t, clean = sim["t"], sim["angle_deg"]
    fs = sim["fs_out_hz"]
    rel = int(sim["release_t_s"] * fs)
    out = []

    def measure(label, maker):
        vals = []
        for rep in range(reps):
            p = pts.compute_pt_params(t, maker(np.random.default_rng(97 * rep + 1)))
            if p:
                vals.append((p["omega_max_n"], p["omega_peak_deg_s"]))
            if maker is _clean:
                break
        if not vals:
            out.append({"artifact": label, "omega_max_n": float("nan"),
                        "omega_max_n_lo": float("nan"),
                        "omega_max_n_hi": float("nan"),
                        "omega_peak": float("nan"), "n_scorable": 0})
            return
        a = np.array(vals)
        out.append({"artifact": label, "omega_max_n": float(a[:, 0].mean()),
                    "omega_max_n_lo": float(a[:, 0].min()),
                    "omega_max_n_hi": float(a[:, 0].max()),
                    "omega_peak": float(a[:, 1].mean()),
                    "n_scorable": len(vals)})

    def _clean(_rng):
        return clean
    measure("clean model trace", _clean)
    for sd in (1.0, 2.0, 4.0, 8.0):
        measure(f"gaussian noise {sd:.0f} deg RMS",
                lambda r, sd=sd: clean + r.normal(0.0, sd, len(clean)))

    def spikes(r):
        a = clean.copy()
        idx = r.choice(np.arange(int(0.35 * len(a)), len(a) - 2), size=30,
                       replace=False)
        a[idx] += r.choice([-1, 1], size=30) * 10.0
        return a
    measure("30 spikes of 10 deg", spikes)

    def step(r):
        a = clean.copy(); a[rel:rel + 2] -= 10.0
        return a
    measure("10 deg step at release", step)

    def push(r):
        a = clean.copy()
        n = 6
        a[rel:rel + n] -= 600.0 * np.arange(n) / fs
        return a
    measure("600 deg/s push for 60 ms", push)

    def freeze(r):
        a = clean.copy(); a[rel + 3:rel + 9] = a[rel + 2]
        return a
    measure("6-frame marker freeze", freeze)

    if verbose:
        print()
        print("C. Artifacts that could inflate the measured ratio, each tested")
        print(f"{'artifact':30s} {'omega_max_n':>12s} {'range':>15s} "
              f"{'om_peak':>9s} {'ok':>4s}")
        print("-" * 74)
        for r in out:
            rng = (f"{r['omega_max_n_lo']:.2f}-{r['omega_max_n_hi']:.2f}"
                   if r["n_scorable"] > 1 else "single run")
            print(f"{r['artifact']:30s} {r['omega_max_n']:12.3f} {rng:>15s} "
                  f"{r['omega_peak']:9.1f} {r['n_scorable']:4d}")
        print(f"  disputed constant {DISPUTED:.4f}.")
        print("  The RANGE column is why the mean alone will not do. At 8 deg "
              "RMS the ratio")
        print("  is wildly seed-dependent -- two runs of this harness gave "
              "means of 5.84 and")
        print("  7.90 -- because at that noise level N jumps around and trials "
              "start coming")
        print("  back unscorable. Reporting a single figure there would be "
              "reporting one")
        print("  draw -- individual draws at 8 deg span 1.48 to 12.53, which "
              "STRADDLES the")
        print("  constant. So noise at IMU scale can produce 9.57 on a single "
              "trial.")
        print("  What it cannot do is produce it as a MEDIAN: the mean sits at "
              "5.84, and")
        print("  HEALTHY_REF is a median over 46 trials, so landing at 9.57 "
              "needs the noise")
        print("  to be systematically high rather than merely variable -- and "
              "8 deg is an")
        print("  IMU figure, while these were optical trials.")
    return out


def part_d(verbose=True):
    """Whether the two constants' arithmetic closes. It does not."""
    a0_ref = pts.EXCURSION_REF_MEAN_DEG
    sim = rnm.simulate(rnm.PROFILES["tone_L0"])
    p = pts.compute_pt_params(sim["t"], sim["angle_deg"])
    # Peak velocity scales with amplitude at fixed frequency.
    pred_peak = p["omega_peak_deg_s"] * a0_ref / p["A0_deg"]
    res = {"a0_ref_deg": a0_ref,
           "predicted_peak_deg_s": pred_peak,
           "required_peak_deg_s": DISPUTED * a0_ref,
           "required_a0_deg": pred_peak / DISPUTED,
           "n_healthy_ref_trials": 46,
           "n_excursion_ref_trials": pts.EXCURSION_REF_N}
    if verbose:
        print()
        print("D. Do the two constants' arithmetic close?")
        print(f"  HEALTHY_REF came from 46 control trials; "
              f"EXCURSION_REF_MEAN_DEG from {pts.EXCURSION_REF_N}.")
        print(f"  At A0 = {a0_ref:.1f} deg and 0.8 Hz the physics gives a peak "
              f"of {pred_peak:.0f} deg/s.")
        print(f"  Reading {DISPUTED:.4f} at that peak needs A0 = "
              f"{res['required_a0_deg']:.1f} deg, not {a0_ref:.1f}.")
        print(f"  Reading {DISPUTED:.4f} at A0 = {a0_ref:.1f} needs a peak of "
              f"{res['required_peak_deg_s']:.0f} deg/s, not {pred_peak:.0f}.")
        print("  Both cannot hold, so at least one constant is measuring a "
              "different thing.")
    return res


#: Measured peak first-swing knee angular velocity in healthy adults.
#: Kim, Jeon, Park & Choi (1996), J Korean Acad Rehabil Med 20(3):735-740:
#: 50 healthy supine adults, 21-67, electrogoniometer, passive release.
#: PROVENANCE IS WEAK AND MUST TRAVEL WITH THE NUMBER: the full text could not
#: be read (host returned 403, mirrors failed DNS) and this was recovered from
#: a search index of the English abstract, reproduced identically across two
#: queries. It is also a single ~30-year-old study, and the posture is SUPINE
#: while this project's rig is thigh-inclined. Treat it as corroboration of an
#: independently derived prediction, not as the prediction's source.
MEASURED_PEAK_DEG_S = (221.9, 35.6)
MEASURED_PEAK_N = 50


def part_e(verbose=True):
    """The verdict, and a mechanism claim RETRACTED.

    WHAT THIS PART ORIGINALLY SAID, AND WHY IT WAS WRONG. It attributed the
    inflation to the frame rate and the differentiated noise floor, on the
    strength of the _sg window note in pendulastic_pt_score (quoted below),
    which records the median omega_max_n moving 8.50 -> 3.19 when real 120 Hz
    OptiTrack is decimated to 20 Hz. Tested inside this model, sampling rate
    does ESSENTIALLY NOTHING: omega_max_n reads 4.324 at 20 Hz and 4.381 at
    240 Hz, and even with 2 deg of added noise it only spans 4.29 to 5.18. The
    model cannot reproduce the 2.7x move, so whatever causes it in the real
    pipeline is not sampling rate as such and is not white noise either. The
    mechanism is UNIDENTIFIED and `part_c` only bounds what it is not.

    Fowler's definition point does not rescue the constant either. Fowler
    defines the relaxation-index denominator as starting angle minus resting
    angle -- exactly this project's A0 -- and notes the first-swing excursion
    EXCEEDS it, since healthy RI is conventionally above 1. That is true and
    this model already reproduces it: RI comes out 1.74 against a measured
    healthy 1.678 +/- 0.090. And with that built in, omega_max_n is still
    4.38, because for a limb released from rest the peak-velocity-to-A0 ratio
    is a shade under the damped angular frequency, not RI times it.

    WHAT STILL STANDS, on three legs that do not need each other:

      physics      mass cancels, so 0.79-0.97 Hz across every anthropometric
                   table and body size; 1.52 Hz needs K = 17-40 Nm/rad or a
                   0.12-0.16 m shank
      measurement  221.9 +/- 35.6 deg/s peak first-swing velocity in 50
                   healthy supine adults, against the 446 deg/s the constant
                   requires at A0 = 46.6: +6.3 SD
      this model   reproduces the measured RI and still reads 4.38

      and the project's own 8.50 -> 3.19 experiment is disqualifying on its
      own terms whatever causes it: a quantity that halves when you resample
      the same motion cannot be a property of a leg, even if this module
      cannot say why it halves.

    The original text, kept because it is the claim being retracted:

    `omega_max_n` is `max|d(phi)/dt| / A0`, a differentiated quantity divided
    by an amplitude. Differentiation amplifies high-frequency content, so the
    numerator is set partly by the reconstruction's noise floor and by the
    smoothing window -- and `pendulastic_pt_score` already records, at the
    _sg window note, the experiment that proves it:

        "Decimating real 120 Hz OptiTrack to 20 Hz, which changes the sampling
         and nothing else about the motion, moved the median omega_max_n from
         8.50 to 3.19 and N from 3.5 to 2.5 across 205 trials."

    A quantity that moves 2.7x when only the sampling rate changes is not
    measuring the limb. HEALTHY_REF was measured on 120 Hz OptiTrack, which is
    the end of that range that reads high, and the median there was 8.50
    against a physics prediction of about 4.4.

    part_c's noise sweep closes the loop from the other direction: omega_max_n
    climbs monotonically with injected angle noise on a trace whose true
    frequency is fixed and known, landing in the 5-9 band that real
    reconstructions occupy. Two independent routes, same conclusion.
    """
    sim = rnm.simulate(rnm.PROFILES["tone_L0"])
    p = pts.compute_pt_params(sim["t"], sim["angle_deg"])
    meas, sd = MEASURED_PEAK_DEG_S
    a0 = pts.EXCURSION_REF_MEAN_DEG
    required = DISPUTED * a0
    res = {"model_omega_max_n": p["omega_max_n"],
           "required_peak_deg_s": required,
           "measured_peak_deg_s": meas,
           "sd_away": (required - meas) / sd}
    if verbose:
        print()
        print("E. The mechanism, and the verdict")
        print(f"  omega_max_n = max|dphi/dt| / A0: a DIFFERENTIATED numerator "
              f"over an amplitude.")
        print(f"  pendulastic_pt_score's own _sg note records that decimating "
              f"real 120 Hz")
        print(f"  OptiTrack to 20 Hz -- changing the sampling and nothing else "
              f"about the")
        print(f"  motion -- moved the MEDIAN omega_max_n from 8.50 to 3.19 "
              f"across 205 trials.")
        print(f"  A quantity that moves 2.7x on sampling rate alone is not "
              f"measuring a limb.")
        print()
        print(f"  clean model trace, true 0.80 Hz:        "
              f"omega_max_n = {p['omega_max_n']:.3f}")
        print(f"  real 120 Hz OptiTrack, median of 205:   omega_max_n = 8.50")
        print(f"  real 20 Hz equivalent, same motion:     omega_max_n = 3.19")
        print(f"  HEALTHY_REF, 46 120-Hz control trials:  "
              f"omega_max_n = {DISPUTED:.4f}")
        print()
        print(f"  Peak velocity the constant requires at A0 = {a0:.1f} deg: "
              f"{required:.0f} deg/s")
        print(f"  Measured in {MEASURED_PEAK_N} healthy supine adults:        "
              f"{meas:.1f} +/- {sd:.1f} deg/s")
        print(f"  The requirement is {res['sd_away']:+.1f} SD from the "
              f"measurement.")
        print()
        print("  RETRACTION: this part first blamed the frame rate. Tested in")
        print("  this model, sampling rate does almost nothing -- omega_max_n")
        print("  reads 4.324 at 20 Hz and 4.381 at 240 Hz. The model cannot")
        print("  reproduce the real pipeline's 2.7x move, so the mechanism is")
        print("  UNIDENTIFIED. part_c bounds what it is not: noise, spikes,")
        print("  release steps and marker freezes all fall short.")
        print()
        print("  VERDICT, which does not depend on the mechanism:")
        print("  omega_max_n and omega_min_n cannot be properties of a healthy")
        print("  limb. The Bajd & Bowman passive set survives -- three")
        print("  anthropometric routes agree with it, the one measured")
        print("  peak-velocity figure is within 1 SD of it, and this model")
        print("  reproduces the measured relaxation index while still reading")
        print("  4.38. And a quantity that halves when the same motion is")
        print("  resampled is disqualified whatever the cause.")
    return res


def main() -> int:
    part_a(); part_b(); part_c(); part_d(); part_e()
    print()
    print("WHAT THIS DOES NOT SETTLE")
    print("  The replacement VALUES. The physics puts a healthy limb's")
    print("  omega_max_n near 4.3-4.4, and this model holds there from 20 Hz to")
    print("  240 Hz. But the REAL pipeline does not: it reads 8.50 at 120 Hz")
    print("  and 3.19 at 20 Hz on the same motion. Until that is explained, a")
    print("  recalibrated constant would be fitted to whatever is causing it,")
    print("  so the recommendation is to stop scoring these two rather than to")
    print("  give them new numbers.")
    print()
    print("  Popovic's 11-17 rad/s is no longer a puzzle worth carrying: that")
    print("  healthy range traces to n = 6 healthy subjects in a retrospective")
    print("  study whose authors state it was underpowered, with group")
    print("  differences not reaching significance for want of sample size. It")
    print("  is not a normative range. An independent n = 50 normative sample")
    print("  measured 3.873 +/- 0.622 rad/s and does not reproduce it.")
    print()
    print("  WHAT THE MECHANISM IS. Still open, and it matters: until it is")
    print("  named, nobody can say which other parameters it touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
