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

    Gaussian angle noise     5.16 -> 6.40 at 4 deg RMS (an order of magnitude
                             above optical marker noise)
    isolated spikes          5.16 -> 5.11 at 30 spikes of 10 deg, and
                             omega_peak does not move at all
    release step/push/freeze 5.16 -> 6.76, and only under a 600 deg/s push
                             lasting 60 ms, which is not a passive release
    broadband 8 deg RMS      mean 7.88, but individual draws span 1.39 to
                             21.37 -- they STRADDLE the constant, because at
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


MODEL CHANGE, 2026-10-26. Every model figure in this module was recomputed
when `rig_neuromuscular_model` was retired in favour of `neuromech`, whose
engine is checked against De Groote's own MATLAB. The clean-trace
`omega_max_n` moved 4.38 -> 5.16 and the relaxation index 1.74 -> 1.81. Both
verdicts are unchanged and one is stronger: the resampling spread this part
retracts is now 1.02x (5.052 at 20 Hz to 5.164 at 240 Hz) against the real
pipeline's 2.7x, and 5.16 is still 1.85x below the disputed 9.5672 while
sitting within 1 SD of the one measured peak-velocity figure.
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
from neuromech import profiles as rnm   # was rig_neuromuscular_model (retired 2026-10-26)

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
    """The verdict, after two rounds of correcting this part.

    ROUND 1, RETRACTED. This part first attributed the inflation to the frame
    rate and the differentiated noise floor, on the strength of the _sg window
    note in pendulastic_pt_score, which records the median omega_max_n moving
    8.50 -> 3.19 when real 120 Hz OptiTrack is decimated to 20 Hz. Tested
    inside this model, sampling rate does ESSENTIALLY NOTHING: omega_max_n
    reads 5.052 at 20 Hz and 5.164 at 240 Hz -- a 1.02x spread -- and even
    with 2 deg of added noise it only spans 4.89 to 5.90.

    ROUND 2, 2026-10-27: the conclusion drawn from that -- "the mechanism is
    UNIDENTIFIED" -- was wrong, and it was wrong because this part swept the
    WRONG VARIABLE. It varied sampling rate with the smoothing window already
    expressed in SECONDS, which is the behaviour AFTER the 2026-08-31 fix, and
    therefore measured the fix working. The pre-fix code took a fixed SAMPLE
    COUNT, so the window itself moved with the rate: 15 samples is 0.125 s at
    120 Hz and 0.750 s at 20 Hz. `part_g` now sweeps that, and the divergence
    reproduces here -- omega_max_n 1.47x and N 1.25x against the real
    pipeline's 2.66x and 1.40x, same direction, N almost exactly.

    So the mechanism was identified, attributed and FIXED before this audit was
    written. The project's own re-run attributed it at the time: 120 Hz data
    re-processed with 20 Hz-EQUIVALENT durations reproduced the 20 Hz values,
    closing the omega_max_n gap 153.8% -> 7.2% and N's 25.0% -> 0.0%. And
    9.5672 did not inherit the bug: no commit in this repository contains a
    sample-count window (the file arrives in 63db1b5, 2026-09-04, already
    smoothing over seconds) and 9.5672 was written in 7256784, 2026-09-09.

    THE LEG THIS COSTS. "A quantity that halves when you resample the same
    motion cannot be a property of a leg, whatever causes it" was the one leg
    of the verdict that needed no model and no literature. It is GONE: post-fix
    the real pipeline's residual rate gap is 7.2% and this model's is 1.02x.
    Nothing halves any more.

    Fowler's definition point does not rescue the constant either. Fowler
    defines the relaxation-index denominator as starting angle minus resting
    angle -- exactly this project's A0 -- and notes the first-swing excursion
    EXCEEDS it, since healthy RI is conventionally above 1. That is true and
    this model already reproduces it: RI (= A1/A0 = 1.6*R2n) comes out 1.81
    against a measured healthy 1.678 +/- 0.090 and 1.91 +/- 0.16, i.e. inside
    the published range. And with that built in, omega_max_n is still 5.16,
    because for a limb released from rest the peak-velocity-to-A0 ratio is a
    shade under the damped angular frequency, not RI times it.

    WHAT STILL STANDS, on three legs that do not need each other:

      physics      mass cancels, so 0.79-0.97 Hz across every anthropometric
                   table and body size; 1.52 Hz needs K = 17-40 Nm/rad or a
                   0.12-0.16 m shank
      measurement  221.9 +/- 35.6 deg/s peak first-swing velocity in 50
                   healthy supine adults, against the 446 deg/s the constant
                   requires at A0 = 46.6: +6.3 SD -- but see the caveat below,
                   which is real and was not stated before
      this model   reproduces the measured RI and still reads 5.16

    THE CAVEAT ON THE MEASUREMENT LEG. Peak angular velocity has no asymptote
    as the smoothing window shrinks, and the _sg note records what that costs
    on real trials: the median reads 186 deg/s at a 0.75 s window, 343 at
    0.25 s and 433 at 0.05 s on 40 trials at 120 Hz. The 1996 electrogoniometer
    study does not report its filtering. So the +6.3 SD distance is partly a
    difference of filter definition and must not be read as a 6.3 SD PHYSICAL
    conflict. What survives that caveat is the comparison between two SMOOTH
    signals, where the window barely matters: this model's geometry gives 281
    deg/s at 0.10 s and 232 at 0.75 s, against the measured 221.9 +/- 35.6 --
    1.7 SD, not 6.3. The constant's 446 deg/s is outside both.

    WHAT IS ACTUALLY LEFT, and it is narrower and rate-invariant. At the
    shipped 0.10 s window the pipeline reads 1.85x this model (9.5672 against
    5.16), and `part_g` decomposes that into ~1.59x in peak velocity and ~1.17x
    in A0 (this model's 54.5 deg against the cohort's 46.6). 1.59 * 1.17 =
    1.86: the whole gap, with nothing left over for sampling. The live question
    is whether the pipeline's peak velocity is inflated or this reference is
    stale, and the cohort distinguishes them.

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
    # Resample the SAME motion and re-measure. Computed, not quoted: the
    # figures here used to be literals carried over from the retired
    # rig_neuromuscular_model, which would have gone stale silently the
    # moment the model behind them changed.
    rates = {}
    for fs_hz in (20.0, 60.0, 120.0, 240.0):
        tg = np.arange(0.0, sim["t"][-1], 1.0 / fs_hz)
        ang = np.interp(tg, sim["t"], sim["angle_deg"])
        pr = pts.compute_pt_params(tg, ang)
        rates[fs_hz] = pr["omega_max_n"] if pr else float("nan")
    res = {"model_omega_max_n": p["omega_max_n"],
           "model_peak_deg_s": p.get("omega_peak_deg_s", float("nan")),
           "required_peak_deg_s": required,
           "measured_peak_deg_s": meas,
           "sd_away": (required - meas) / sd,
           "by_rate": rates}
    if verbose:
        print()
        print("E. The verdict, and what two rounds of correction cost it")
        print(f"  omega_max_n = max|dphi/dt| / A0: a DIFFERENTIATED numerator "
              f"over an amplitude.")
        print()
        print(f"  clean model trace, true 0.80 Hz:        "
              f"omega_max_n = {p['omega_max_n']:.3f}")
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
        print("  ROUND 1, RETRACTED: this part first blamed the frame rate.")
        print("  Tested in this model, sampling rate does almost nothing:")
        print("    " + "   ".join(f"{int(k)} Hz: {v:.3f}" for k, v in rates.items()))
        _lo, _hi = min(rates.values()), max(rates.values())
        print(f"  a spread of {_hi / _lo:.2f}x against the real pipeline's 2.7x.")
        print()
        print("  ROUND 2, 2026-10-27: that sweep varied the WRONG VARIABLE. It")
        print("  moved the rate with the window already fixed in SECONDS, which")
        print("  is the behaviour AFTER the 2026-08-31 fix -- so it measured the")
        print("  fix working. The pre-fix window was a SAMPLE COUNT and moved")
        print("  with the rate. part_g sweeps that, and it reproduces here.")
        print("  The mechanism was identified, attributed and FIXED before this")
        print("  audit was written; 9.5672 was measured after the fix (7256784,")
        print("  2026-09-09; no commit here has a sample-count window).")
        print()
        print("  COST: the leg that needed neither model nor literature -- 'a")
        print("  quantity that halves when the same motion is resampled cannot")
        print("  be a property of a leg' -- is GONE. Post-fix the real")
        print("  pipeline's residual rate gap is 7.2%. Nothing halves.")
        print()
        print("  VERDICT, on the two legs that survive:")
        print("  omega_max_n and omega_min_n cannot be properties of a healthy")
        print("  limb. The Bajd & Bowman passive set survives -- three")
        print("  anthropometric routes agree with it, the one measured")
        print("  peak-velocity figure is within 1 SD of it, and this model")
        print(f"  reproduces the measured relaxation index while still reading")
        print(f"  {p['omega_max_n']:.2f}.")
        print()
        print("  CAVEAT now stated on the measurement leg: peak velocity has no")
        print("  asymptote as the window shrinks (the _sg note reads 186 deg/s")
        print("  at 0.75 s, 343 at 0.25 s, 433 at 0.05 s on 40 real trials), and")
        print("  the 1996 study does not report its filtering. So +6.3 SD is")
        print("  partly a filter-definition difference. Smooth signal against")
        print(f"  smooth signal it is 1.7 SD: this model's {res['model_peak_deg_s']:.0f} deg/s against")
        print(f"  {meas:.1f} +/- {sd:.1f}. The constant's {required:.0f} deg/s is outside both.")
    return res


#: MEASURED healthy-adult values, read from the papers' own text (network access
#: opened 2026-10-26, pages fetched and read rather than summarised).
#:
#: Casabona et al., J Appl Physiol 2013, PMC3544505 -- 10 control adults,
#: Wartenberg test, UNNORMALISED, EMG-monitored for relaxation:
MEASURED = {
    "peak_velocity_deg_s":   (241.7, 17.7, "PMC3544505 controls, n=10"),
    "mean_velocity_deg_s":   (157.6, 11.4, "PMC3544505 controls, n=10"),
    "relaxation_index":      (1.91,  0.16, "PMC3544505 controls, n=10"),
    "first_swing_ms":        (735.0, 87.0, "PMC3544505, onset to first flexion peak"),
    "damping_flex_nms_rad":  (0.18,  0.08, "PMC3544505 controls, F1->F2"),
    "damping_ext_nms_rad":   (-0.31, 0.09, "PMC3544505 controls, E1->E2"),
    # Kim et al. 1996, n=50 healthy supine adults -- independent cohort.
    "peak_velocity_kim":     (221.9, 35.6, "Kim 1996, n=50"),
    "relaxation_index_kim":  (1.678, 0.090, "Kim 1996, n=50"),
}


def part_f(verbose=True):
    """Measured values, and a factor-of-two error in a published paper.

    This part replaces guesswork with read numbers, and it had to resolve a
    conflict that nearly overturned the verdict.

    THE CONFLICT. Casabona et al.'s rheumatoid-arthritis pendulum paper
    (PMC1693559) reports control knee stiffness as 1.26-1.34 N/rad m^4,
    normalised by the fifth power of stature. Denormalised at their controls'
    1.625 m that is 14.3-15.2 Nm/rad -- twenty-nine times Bajd & Bowman's
    K = 0.5. Taken at face value it raises the healthy frequency to about
    1.36 Hz and makes omega_max_n near 7.5 defensible, which would have sunk
    the verdict.

    THE DENORMALISATION IS RIGHT. Checked against the same group's Down-
    syndrome paper (PMC3544505), which reports the same quantities
    UNNORMALISED: viscosity 0.18 +/- 0.08 Nms/rad in flexion and
    -0.31 +/- 0.09 in extension for controls. The RA paper's normalised
    viscosity denormalises to 0.113-0.601 Nms/rad. Overlapping, so the
    stature^5 conversion is sound and the 14.3 Nm/rad figure is what that
    paper really claims.

    THE PAPER IS WRONG, AND BY EXACTLY A FACTOR OF TWO IN T. The same group
    measured the first swing -- onset to first flexion peak, which is a HALF
    period -- at 735 +/- 87 ms. That gives a full period of 1.47 s and
    omega = 4.27 rad/s. Their equation (2) is omega = sqrt(K'/J) = 2*pi/T with
    T "the period of one cycle", but their methods then apply it per
    half-cycle. Evaluate 2*pi/T with T = the half-cycle and omega comes out
    8.55 rad/s, twice too high, so K' = J*omega^2 is four times too high --
    and that reproduces their reported stiffness at 14.27 Nm/rad against a
    published 14.3. With the correct full period, K lands at or below zero,
    which is K = 0.5 within the error of the anthropometry.

    So the one piece of evidence that appeared to vindicate the constant is an
    arithmetic artifact, and the measured timing from the same group refutes
    it.

    THE DIRECT NUMBER. PMC3544505 reports control peak first-swing angular
    velocity as 241.7 +/- 17.7 deg/s, unnormalised and EMG-monitored. Kim's
    independent n=50 cohort gives 221.9 +/- 35.6. The constant needs 446 deg/s
    at A0 = 46.6, which is 11.5 SD from the first figure.
    """
    import math as _m
    pv, pv_sd, pv_src = MEASURED["peak_velocity_deg_s"]
    hs, hs_sd, _ = MEASURED["first_swing_ms"]
    T = 2.0 * hs / 1000.0
    w = 2.0 * _m.pi / T
    a0_implied = pv / (w * 180.0 / _m.pi) * (180.0 / _m.pi)
    required = DISPUTED * pts.EXCURSION_REF_MEAN_DEG
    res = {"period_s": T, "freq_hz": 1.0 / T, "omega_rad_s": w,
           "omega_max_n_implied": pv / a0_implied,
           "required_peak_deg_s": required,
           "sd_away": (required - pv) / pv_sd}
    if verbose:
        print()
        print("F. Measured values, read from the papers")
        print(f"  first swing (onset -> first flexion peak) {hs:.0f} +/- "
              f"{hs_sd:.0f} ms, a HALF period")
        print(f"    -> full period {T:.3f} s, {1.0/T:.3f} Hz, "
              f"omega {w:.2f} rad/s")
        print(f"  peak first-swing velocity   {pv:.1f} +/- {pv_sd:.1f} deg/s  "
              f"({pv_src})")
        pk, pk_sd, pk_src = MEASURED["peak_velocity_kim"]
        print(f"                              {pk:.1f} +/- {pk_sd:.1f} deg/s  "
              f"({pk_src})")
        ri, ri_sd, _ = MEASURED["relaxation_index"]
        rk, rk_sd, _ = MEASURED["relaxation_index_kim"]
        print(f"  relaxation index            {ri:.2f} +/- {ri_sd:.2f} and "
              f"{rk:.3f} +/- {rk_sd:.3f}")
        bf, bf_sd, _ = MEASURED["damping_flex_nms_rad"]
        be, be_sd, _ = MEASURED["damping_ext_nms_rad"]
        print(f"  damping B, absolute         {bf:.2f} +/- {bf_sd:.2f} "
              f"(flexion), {be:.2f} +/- {be_sd:.2f} (extension) Nms/rad")
        print()
        print(f"  omega_max_n from these numbers: ~{w:.2f}")
        print(f"  this model:                     "
              f"{pts.compute_pt_params(*[rnm.simulate(rnm.PROFILES['tone_L0'])[k] for k in ('t','angle_deg')])['omega_max_n']:.2f}")
        print(f"  HEALTHY_REF:                    {DISPUTED:.4f}")
        print(f"  peak velocity the constant needs at A0 = "
              f"{pts.EXCURSION_REF_MEAN_DEG:.1f}: {required:.0f} deg/s, "
              f"{res['sd_away']:+.1f} SD from measurement")
        print()
        print("  A published paper's 14.3 Nm/rad control stiffness LOOKED like")
        print("  it vindicated the constant. It is a factor-of-two error in T:")
        print("  omega = 2*pi/T evaluated on the half-cycle, so K' = J*omega^2")
        print("  comes out 4x high. Reconstructing it that way reproduces 14.27")
        print("  against their published 14.3. With the full period, K ~ 0.")
    return res


#: The real pipeline's pre-fix divergence, from the _sg window note in
#: pendulastic_pt_score: median omega_max_n and N on the SAME 205 trials, read
#: at 120 Hz and after decimation to 20 Hz, while the smoothing window was a
#: fixed sample count.
REAL_PREFIX = {"omega_max_n": (8.50, 3.19), "N": (3.5, 2.5)}


def _with_sg_window(win_s, fn):
    """Run fn() with _sg's default window set to win_s seconds.

    This reaches into _sg.__defaults__ rather than setting the module's
    _SG_WINDOW_S, because the window is captured as a DEFAULT ARGUMENT at
    definition time -- rebinding the global does nothing, which is how the
    first version of this probe silently measured one window five times.
    """
    d = list(pts._sg.__defaults__)
    was = d[0]
    d[0] = win_s
    pts._sg.__defaults__ = tuple(d)
    try:
        return fn()
    finally:
        d = list(pts._sg.__defaults__)
        d[0] = was
        pts._sg.__defaults__ = tuple(d)


def part_g(verbose=True):
    """The mechanism, swept on the variable that actually moved.

    part_e's first retraction swept SAMPLING RATE and found a 1.02x spread,
    then concluded the mechanism was unidentified. The window was already
    expressed in seconds by then, so that sweep measured the post-2026-08-31
    behaviour -- it measured the fix working.

    The pre-fix code took a fixed SAMPLE COUNT (15 at the omega call sites), so
    the physical window moved with the rate: 0.125 s at 120 Hz, 0.750 s at
    20 Hz. Sweeping THAT reproduces the divergence here, in the same direction
    and -- for N -- at nearly the same size. The residual (1.47x here against
    the pipeline's 2.66x) goes the way the mechanism predicts: a real marker
    reconstruction carries content above the swing band that a clean analytic
    trace does not, so a 0.750 s window has more to take away from it.

    It also decomposes what is LEFT once sampling is out of the way, which is
    rate-invariant and is the live question: the pipeline reads 1.85x this
    model at the shipped window, as ~1.59x in peak velocity times ~1.17x in A0.
    """
    sim = rnm.simulate(rnm.PROFILES["tone_L0"])
    t, ang = sim["t"], sim["angle_deg"]

    def at(fs_hz, win_s):
        tg = np.arange(0.0, t[-1], 1.0 / fs_hz)
        a = np.interp(tg, t, ang)
        return _with_sg_window(win_s, lambda: pts.compute_pt_params(tg, a))

    SAMPLES = 15.0          # the sample count the pre-fix omega call sites used
    pre = {fs: at(fs, SAMPLES / fs) for fs in (120.0, 20.0)}
    post = {fs: at(fs, 0.10) for fs in (20.0, 60.0, 120.0, 240.0)}

    res = {"pre": pre, "post": post}
    for k in ("omega_max_n", "N"):
        res[f"pre_spread_{k}"] = pre[120.0][k] / pre[20.0][k]
    vals = [v["omega_max_n"] for v in post.values()]
    res["post_spread"] = max(vals) / min(vals)

    model = post[120.0]
    a0_ref = pts.EXCURSION_REF_MEAN_DEG
    res["peak_factor"] = (DISPUTED * a0_ref) / model["omega_peak_deg_s"]
    res["a0_factor"] = model["A0_deg"] / a0_ref
    res["gap"] = DISPUTED / model["omega_max_n"]

    if verbose:
        print()
        print("G. The window, which is the variable part_e should have swept")
        print("  PRE-FIX: _sg took 15 SAMPLES, so the window moved with the rate.")
        print(f"  {'rate':>7} {'window':>9} {'omega_max_n':>12} {'N':>6} "
              f"{'peak deg/s':>11} {'A0 deg':>8}")
        for fs in (120.0, 20.0):
            q = pre[fs]
            print(f"  {int(fs):6d}H {SAMPLES / fs:8.3f}s {q['omega_max_n']:12.3f} "
                  f"{q['N']:6.2f} {q['omega_peak_deg_s']:11.1f} {q['A0_deg']:8.2f}")
        print()
        for k in ("omega_max_n", "N"):
            hi, lo = REAL_PREFIX[k]
            print(f"  {k:<12} this model {res[f'pre_spread_{k}']:.2f}x   "
                  f"real pipeline {hi / lo:.2f}x   ({hi} -> {lo} across 205 trials)")
        print("  Same direction both times, and N almost exactly. The mechanism")
        print("  is the window, it was attributed in 2026-08-31's own re-run")
        print("  (omega_max_n gap 153.8% -> 7.2%, N 25.0% -> 0.0%), and it was")
        print("  FIXED there. part_e's 'UNIDENTIFIED' is withdrawn.")
        print()
        print("  POST-FIX: 0.10 s at every rate.")
        print("    " + "   ".join(f"{int(k)} Hz: {v['omega_max_n']:.3f}"
                                  for k, v in post.items()))
        print(f"  spread {res['post_spread']:.2f}x. Nothing halves.")
        print()
        print("  WHAT IS LEFT, rate-invariant, at the shipped window:")
        print(f"    pipeline / this model        {res['gap']:.2f}x "
              f"({DISPUTED:.4f} vs {model['omega_max_n']:.3f})")
        print(f"    of which peak velocity      {res['peak_factor']:.2f}x "
              f"({DISPUTED * a0_ref:.0f} deg/s required vs "
              f"{model['omega_peak_deg_s']:.0f} measured here)")
        print(f"    of which A0                 {res['a0_factor']:.2f}x "
              f"({model['A0_deg']:.1f} deg here vs {a0_ref:.1f} deg cohort)")
        print(f"    product                     "
              f"{res['peak_factor'] * res['a0_factor']:.2f}x -- the whole gap, "
              f"nothing left for sampling")
        print("  So: is the pipeline's peak velocity inflated, or is the")
        print("  reference stale? The cohort separates them; this module cannot.")
    return res


#: What the 2026-10-27 control cohort measured for f, QUOTED from that run's
#: output rather than recomputed: this module has no access to the recordings.
#: median, [min, max], n trials. The 0.000 is the estimator declining, not a
#: limb with no frequency.
COHORT_F = (0.9189, 0.000, 1.067, 30)


def _spearman(a, b):
    """Rank correlation, with average ranks for ties.

    Written out rather than imported: scipy is not a dependency of this module
    and the figures here are quoted in a report, so they should not depend on
    whether it happens to be installed.
    """
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    ra, rb = ranks(list(a)), ranks(list(b))
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = math.sqrt(sum((x - ma) ** 2 for x in ra))
    db = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return num / (da * db) if da and db else float("nan")


def part_h(verbose=True):
    """Every figure docs/reports/2026-10-27-dropping-f.md cites about f.

    This part exists because that report was written and merged with its
    central evidence produced by throwaway scripts, while its own Reproduce
    line pointed here -- at a module that computed none of it. A report whose
    numbers no committed code reproduces is exactly what this project's
    standard is against, and it was my own report.

    Computed here, on the dialled tone ladder and the graded profile set:

      the ladder table      f, N, their terms, and the total per rung
      discriminative weight f's range and term travel against N's
      rank order           Spearman of each against dialled severity
      f's share            of the composite, across every graded profile
      removal              the ladder's dynamic range with and without f,
                           and whether either is monotone
      mechanism spread     f across other mechanisms, with and without the
                           profile whose f is SKIPPED -- the distinction that
                           turned an 8.84x "confound" into 0.16x

    The cohort figures are QUOTED (see COHORT_F), since the recordings are not
    reachable from here. Everything else is computed.
    """
    ladder = ["tone_L0", "tone_L1", "tone_L2", "tone_L3", "tone_L4"]
    rows = []
    for name in ladder:
        sim = rnm.simulate(rnm.PROFILES[name])
        pr = pts.compute_pt_params(sim["t"], sim["angle_deg"])
        bd = pts.compute_pt_score_breakdown(pr)
        rows.append((name, pr, bd, sum(bd.values())))

    f_vals = [r[1]["f"] for r in rows]
    n_vals = [r[1]["N"] for r in rows]
    f_term = [r[2]["f"] for r in rows]
    n_term = [r[2]["N"] for r in rows]
    tot6 = [r[3] for r in rows]
    rank = list(range(len(rows)))

    # The five-term score: drop f and let the divisor follow, which is what
    # compute_pt_score_breakdown now does when handed a selection.
    keys5 = [k for k in pts._PARAM_KEYS if k != "f"]
    tot5 = [sum(pts.compute_pt_score_breakdown(r[1], keys=keys5).values()) for r in rows]

    # f's share of the composite, over every profile the app grades.
    shares = []
    for name in sorted(rnm.PROFILES):
        sim = rnm.simulate(rnm.PROFILES[name])
        pr = pts.compute_pt_params(sim["t"], sim["angle_deg"])
        if not pr or not pts.mas_estimate(pr)["interpretable"]:
            continue
        bd = pts.compute_pt_score_breakdown(pr)
        t = sum(bd.values())
        if t > 0:
            shares.append((name, pr["f"], 100.0 * bd["f"] / t))

    # Mechanism spread, with and without the profile whose f was never
    # estimated. Including it was the error; it is kept here so the error
    # cannot be made again silently.
    mech = [(n, f) for n, f, _ in shares if n not in ladder]
    mech_ok = [(n, f) for n, f in mech if f >= 0.1]
    sev_spread = max(f_vals) - min(f_vals)
    mech_all = (max(f for _, f in mech) - min(f for _, f in mech)) if mech else float("nan")
    mech_est = (max(f for _, f in mech_ok) - min(f for _, f in mech_ok)) if mech_ok else float("nan")

    def monotone(v):
        return all(b >= a for a, b in zip(v, v[1:]))

    res = {
        "f_range": (min(f_vals), max(f_vals)),
        "n_range": (min(n_vals), max(n_vals)),
        "f_term_travel": max(f_term) - min(f_term),
        "n_term_travel": max(n_term) - min(n_term),
        "spearman_f": _spearman(rank, f_vals),
        "spearman_n": _spearman(rank, n_vals),
        "share_min": min(s for _, _, s in shares),
        "share_max": max(s for _, _, s in shares),
        "range6": max(tot6) / min(tot6),
        "range5": max(tot5) / min(tot5),
        "monotone6": monotone(tot6),
        "monotone5": monotone(tot5),
        "sev_spread": sev_spread,
        "mech_spread_all": mech_all,
        "mech_spread_estimated": mech_est,
    }

    if verbose:
        print()
        print("H. The evidence behind docs/reports/2026-10-27-dropping-f.md")
        print(f"  {'profile':10} {'f (Hz)':>8} {'N':>6} {'f term':>8} {'N term':>8} "
              f"{'pt6':>8} {'pt5':>8}")
        for (name, pr, bd, t), t5 in zip(rows, tot5):
            print(f"  {name:10} {pr['f']:8.3f} {pr['N']:6.2f} {bd['f']:8.4f} "
                  f"{bd['N']:8.4f} {t:8.4f} {t5:8.4f}")
        print()
        print(f"  f spans {res['f_range'][0]:.3f}-{res['f_range'][1]:.3f} Hz, "
              f"{res['f_range'][1] / res['f_range'][0]:.2f}x; its term travels "
              f"{res['f_term_travel']:.4f}")
        print(f"  N spans {res['n_range'][1]:.2f}-{res['n_range'][0]:.2f},     "
              f"{res['n_range'][1] / res['n_range'][0]:.2f}x; its term travels "
              f"{res['n_term_travel']:.4f}")
        print(f"  Spearman vs dialled severity: f {res['spearman_f']:+.3f}, "
              f"N {res['spearman_n']:+.3f}")
        print(f"  f's share of the composite across {len(shares)} graded profiles: "
              f"{res['share_min']:.1f}% to {res['share_max']:.1f}%")
        print()
        print(f"  ladder dynamic range WITH f:    {res['range6']:.2f}x  "
              f"({min(tot6):.4f} -> {max(tot6):.4f})   monotone {res['monotone6']}")
        print(f"  ladder dynamic range WITHOUT f: {res['range5']:.2f}x  "
              f"({min(tot5):.4f} -> {max(tot5):.4f})   monotone {res['monotone5']}")
        print()
        print("  MECHANISM SPREAD, and the error that nearly went into the report:")
        print(f"    across {len(ladder)} dialled severities:        "
              f"{res['sev_spread']:.3f} Hz")
        print(f"    across {len(mech)} other mechanisms, ALL:       "
              f"{res['mech_spread_all']:.3f} Hz  "
              f"({res['mech_spread_all'] / res['sev_spread']:.2f}x)")
        print(f"    across {len(mech_ok)} where f was ESTIMATED:    "
              f"{res['mech_spread_estimated']:.3f} Hz  "
              f"({res['mech_spread_estimated'] / res['sev_spread']:.2f}x)")
        skipped = [n for n, f in mech if f < 0.1]
        print(f"    the whole difference is {skipped}, whose f is SKIPPED, not")
        print("    measured. f is NOT mechanism-confounded; it is weak.")
        print()
        med, lo, hi, n = COHORT_F
        at_med = pts.compute_pt_score_breakdown(
            {"R2n": 1.0, "N": 6.5, "phi_max_ratio": 0.6667,
             "omega_max_n": 9.5672, "f": med, "area_ratio": 0.0945})["f"]
        print(f"  QUOTED from the 2026-10-27 cohort run, not computed here:")
        print(f"    median f over {n} control trials {med:.4f} Hz against the "
              f"{pts.HEALTHY_REF['f']:.4f} reference")
        print(f"    range [{lo:.3f} - {hi:.3f}]; the {lo:.3f} is the estimator declining")
        print(f"    at that median f contributes {at_med:.4f} of a possible "
              f"{1.0 / pts._N_PARAMS:.4f}")
    return res


def main() -> int:
    part_a(); part_b(); part_c(); part_d(); part_e(); part_f(); part_g(); part_h()
    print()
    print("WHAT THIS DOES NOT SETTLE")
    print("  The replacement VALUES. The physics puts a healthy limb's")
    print("  omega_max_n near 4.3-4.4 and this model holds there across rates.")
    print("  The REAL pipeline reads 8.50-9.57 and -- since 2026-08-31 -- reads")
    print("  it rate-invariantly, so the old reason for not recalibrating (an")
    print("  unexplained 8.50 -> 3.19 move) is withdrawn: see part_g. What")
    print("  replaces it is a narrower reason. The two numbers disagree by")
    print("  1.85x at the same smoothing window, as ~1.59x in peak velocity")
    print("  times ~1.17x in A0, and nothing in the pipeline's rate behaviour")
    print("  explains either factor. Setting the constant from physics while")
    print("  the pipeline still reads high would make the term inert; setting")
    print("  it from the pipeline accepts whatever inflates the peak. Which of")
    print("  the two is wrong is a COHORT question, not a model question.")
    print()
    print("  HALF ACTED ON. omega_min_n left the scored set 2026-10-26: it")
    print("  is ALGEBRAICALLY -omega_max_n for a limb dropped from full")
    print("  extension, and no reference value fixes a duplicate.")
    print()
    print("  omega_max_n was dropped the same day and then RESTORED, because")
    print("  the argument for dropping it was wrong. It looked like a pedestal")
    print("  -- 0.0776 of a healthy limb's 0.0892 score, growing only 0.038")
    print("  across the ladder -- but that is the one-sided penalty charging")
    print("  every limb for a reference set far above what the trace being")
    print("  scored produces. At a reference of 4.76 the pedestal is 0.0000 and")
    print("  the signal nearly doubles to 0.0644.")
    print()
    print("  CORRECTED 2026-10-27: those figures are all from EMULATOR traces,")
    print("  and the reference is the median of REAL control trials. So the")
    print("  pedestal is a cross-pipeline artifact -- a real healthy leg sits")
    print("  at the reference by construction and pays ~0. The pedestal is")
    print("  therefore not evidence against 9.5672. The case against it is now")
    print("  only the 1.85x gap to physics and literature, which is open.")
    print()
    print("  So no new value is guessed here. 9.5672 is the median this")
    print("  pipeline measures (8.50 across 205 mixed trials), so a physics")
    print("  value of 4.76 would sit BELOW every real reading and make the")
    print("  term inert -- the same silent failure, by the opposite route.")
    print("  Note also that the pedestal computed on this emulator is a")
    print("  CROSS-PIPELINE artifact: the reference is the median of real")
    print("  control trials, so real healthy legs sit at it by construction")
    print("  and pay ~0, while every emulator profile sits 1.85x below it and")
    print("  pays the full gap. Emulator PT scores are therefore not")
    print("  comparable to cohort-calibrated zone thresholds on this term.")
    print("  recalibrate_pt_zones.py now reports what the control cohort")
    print("  actually measures for it, which is the number that settles it.")
    print()
    print("  SETTLED 2026-10-27 on the cohort, and the answer was neither")
    print("  option above: omega_max_n left the canonical set. Over 51 real")
    print("  baseline trials peak velocity does not scale with A0 at all")
    print("  (Spearman +0.02, ~350 deg/s flat), so omega_max_n is ~350/A0 and")
    print("  RISES as tone shrinks A0. A penalise-below term on it charges the")
    print("  loosest legs most at ANY reference -- Control_ legs read 6.18,")
    print("  MS and MAS>=1 legs ~12. Inflated peak or stale constant no longer")
    print("  matters for the score. See pendulastic_pt_score._PARAM_KEYS.")
    print()
    print("  Popovic's 11-17 rad/s is no longer a puzzle worth carrying: that")
    print("  healthy range traces to n = 6 healthy subjects in a retrospective")
    print("  study whose authors state it was underpowered, with group")
    print("  differences not reaching significance for want of sample size. It")
    print("  is not a normative range. An independent n = 50 normative sample")
    print("  measured 3.873 +/- 0.622 rad/s and does not reproduce it.")
    print()
    print("  WHAT THE MECHANISM IS. SETTLED, and not by this module: the")
    print("  pre-fix _sg window was a sample count, the project's own re-run")
    print("  attributed the divergence to it (153.8% -> 7.2%), and part_g")
    print("  reproduces it here. What is still open is the narrower,")
    print("  rate-invariant 1.59x in peak velocity, which the cohort answers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
