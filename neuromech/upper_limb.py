"""Upper-limb pendulum tests on the same De Groote core: the elbow (forearm + hand).

Plan: docs/plans/2026-11-10-001-plan-neuromech-elbow-emulator.md (units U1-U3).

The knee core models ONE muscle group, the one the drop stretches: a release
at theta0 falls toward negative theta, and tone, SRS and the delayed reflex all
oppose that fall. The elbow has two published starts (Huang et al. 2016), and
they stretch different muscles:

  start "extension"   forearm released on the extension side of its rest, falls
                      into flexion, stretches the extensors (triceps)
  start "flexion"     forearm released on the flexion side of its rest, falls
                      into extension, stretches the flexors (biceps, brachialis)

Rather than add a sign to the active drive inside the core (which the LabVIEW
Formula Node would then have to carry), each start is simulated in its own
frame, chosen so that the fall is toward negative theta:

  extension start   theta = -q        (q = anatomical elbow flexion, deg, 0 = straight)
  flexion start     theta = +q

The core's tone/SRS/reflex then always belong to the stretched muscle, and its
code is untouched. Results come back as anatomical flexion q.

POSTURE. Posture is given as `rest_q_deg`: the elbow flexion at which the
forearm hangs vertically, i.e. the gravity rest. Gravity's torque on q is then

    tau_g(q) = -m g l_c sin(q - rest_q_deg)

in both frames. Which arm positions give which rest:

  arm at the side (shoulder 0; de Lima et al. 2023)          rest_q_deg = 0, the extension end
  humerus horizontal, with elbow flexion swinging the forearm
    DOWNWARD (e.g. shoulder abducted 90 deg and internally
    rotated 90 deg, or shoulder extended 90 deg)               rest_q_deg = 90, mid-range like the knee
  humerus raised FORWARD (sagittal shoulder flexion) by a     the forearm's hanging angle is -a, past
                                                              full extension: the rest is AT the extension
                                                              wall, not at q = a. Not a mid-range posture.

The core's torque is -m g l_c cos(theta + incline), stable rest where
theta + incline = -90 deg, so extension start: incline = rest_q - 90; flexion
start: incline = -90 - rest_q.

No grade and no PT score are produced here (plan E5): elbow traces are scored
with `neuromech.elbow_score`.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from . import model_rt as rt

# ── Winter (2009) Table 4.1 ─────────────────────────────────────────────────
# Verified 2026-11-18 against the printed 4th edition: Table 4.1 (p. 86) for
# mass, CoM and radius-of-gyration fractions (Dempster via Miller & Nelson /
# Plagenhoef); Figure 4.1 (p. 83, Drillis & Contini) for lengths. Note the
# "forearm and hand" row is normalised to the elbow-to-ulnar-styloid length
# although the segment includes the hand, hence CoM 0.682 and k 0.827 from the
# elbow. Cross-checked against Lin 2007's measured forearm + hand (1.40 kg,
# 0.10 +/- 0.02 kg.m^2, n = 128); see the tests.
#: Forearm + hand mass, fraction of body mass.
WINTER_FAH_MASS_FRAC = 0.022
#: Forearm length (elbow axis -> ulnar styloid), fraction of standing height.
WINTER_FOREARM_LEN_FRAC = 0.146
#: Forearm + hand centre of mass from the elbow, fraction of forearm length.
WINTER_FAH_COM_FRAC = 0.682
#: Forearm + hand radius of gyration about the ELBOW, fraction of forearm length.
WINTER_FAH_KPROX_FRAC = 0.827
#: Hand mass, fraction of body mass (for the wrist, later).
WINTER_HAND_MASS_FRAC = 0.006
#: Hand length (wrist axis -> 2nd knuckle, middle finger), fraction of height.
WINTER_HAND_LEN_FRAC = 0.108
#: Hand centre of mass from the wrist, fraction of hand length.
WINTER_HAND_COM_FRAC = 0.506
#: Hand radius of gyration about the WRIST, fraction of hand length.
WINTER_HAND_KPROX_FRAC = 0.587

#: Elbow range of motion, anatomical flexion, deg.
ELBOW_RANGE_DEG = (0.0, 145.0)

STARTS = ("extension", "flexion")

#: Elbow range-wall steepness, 1/rad (Params.lim_k). The reference knee wall
#: (K_LIM 3) reaches ~55 deg into the range and leaked ~0.17 N.m/rad into the
#: fitted mid-range K. At 20 (e-folding ~3 deg) the wall engages only in the
#: last ~10-15 deg; fitted K reads back within ~1.5 % of the input, and any
#: value 10-30 gives the same fits (plan 11.8), so the exact value is not load-bearing.
ELBOW_WALL_K = 20.0

#: Extension-start tissue scale. Huang 2016 (controls, n = 22, fig 2, read from
#: the bar chart): the 50 deg (extension) start fits K ~4.6 vs ~2.9 and C ~1.2 vs
#: ~0.8 for the 130 deg start, in every group. Applied per START, the way Huang
#: fitted it (one K, C per trajectory), to the tissue spring and tissue damping.
EXTENSOR_SCALE_HUANG = 1.5

# ── Lin apparatus (Lin 2003 pp. 70-71; same rig in 2005, 2007, Huang 2016) ──
#: Counterweight on the shaft's lower end, kg, and its distance below the pivot,
#: m (the 107.5 cm shaft pivots at its midpoint on the elbow axis).
LIN_COUNTERWEIGHT_KG = 1.13
LIN_COUNTERWEIGHT_M = 1.075 / 2
#: Releases: 130 deg (Lin 2003 onward) and its mirror, 50 deg (Huang 2016).
LIN_RELEASE_Q_DEG = {"flexion": 130.0, "extension": 50.0}
#: Apparatus inertia about the elbow, kg.m^2, NOT PRINTED in any paper ("Chen's
#: method"). Bracketed from Lin's own numbers instead of guessed:
#:  * Lin 2003 fig 4 (normal group, read from the chart): eta ~0.15 with
#:    C ~0.45 and K ~2.6, and eta = C / (2 sqrt(K I)) gives I ~0.87;
#:  * the healthy example traces (Lin 2003 fig 3 N4, Lin 2005 fig 1) reach the
#:    first trough ~1.0-1.4 s after release, which with gravity ~3.7 and K ~2.6
#:    N.m/rad needs I ~0.6-1.2.
#: The counterweight alone is 1.13 * 0.5375^2 = 0.33; the shaft, the wrist
#: plate and its slide make up the rest. Lin checks run at both ends, except where
#: the fit is refused at one end (k_v 1.0 at 1.15; test notes say which).
LIN_RIG_J_BRACKET = (0.55, 1.15)
#: Damping the rig adds under load, N.m.s/rad. Lin's healthy C (0.53-0.78) is
#: 2-7x other elbow studies (0.1-0.3, MacKay 1986); Lin 2003 attributes part to
#: the rig (misalignment, friction under the forearm's weight; empty rig 0.008).
#: Tissue keeps the literature's 0.2; this term carries the rest, sized so a
#: 64 kg healthy subject fits C ~0.67 (Lin 2005's weight regression,
#: 0.0089 W + 0.1013). So C in the Lin scenario is CALIBRATED, not validated.
LIN_RIG_B = 0.45


def lin_k_tissue(mass_kg: float) -> float:
    """Lin 2005's healthy elbow stiffness regression on body weight, N.m/rad (n = 192)."""
    return 0.0373 * mass_kg + 0.1796

#: Active-module parameters given as an ABSOLUTE model-convention angle. They
#: would not follow the frame mirroring, so elbow_params refuses them rather
#: than apply them on the wrong side. (Stretch-relative angles -- the HESS
#: angles, catch_release_deg, k_l_thr_deg -- are frame-invariant and pass.)
ABSOLUTE_ANGLE_PARAMS = ("contract_deg", "contract_k")


def forearm_from_anthropometry(mass_kg: float, height_m: float, *,
                               accessory_kg: float | None = None,
                               accessory_m: float | None = None):
    """(l_c, m, J) for forearm + hand about the elbow, plus an optional point mass.

    The accessory is the weighted attachment Lin's apparatus adds because the
    bare forearm is light; a point mass `accessory_kg` at `accessory_m` from the
    elbow. Bare forearm is the default. Give both or neither.
    """
    if mass_kg <= 0 or height_m <= 0:
        raise ValueError(f"mass_kg and height_m must be positive, got {mass_kg}, {height_m}")
    if (accessory_kg is None) != (accessory_m is None):
        raise ValueError("pass both accessory_kg and accessory_m, or neither")
    l_fa = WINTER_FOREARM_LEN_FRAC * height_m
    m = WINTER_FAH_MASS_FRAC * mass_kg
    lc = WINTER_FAH_COM_FRAC * l_fa
    J = m * (WINTER_FAH_KPROX_FRAC * l_fa) ** 2
    if accessory_kg:
        if accessory_kg < 0 or accessory_m < 0:
            raise ValueError("accessory mass and distance must be non-negative")
        lc = (m * lc + accessory_kg * accessory_m) / (m + accessory_kg)
        m = m + accessory_kg
        J = J + accessory_kg * accessory_m ** 2
    return lc, m, J


def hand_from_anthropometry(mass_kg: float, height_m: float):
    """(l_c, m, J) for the hand about the wrist (for the imposed-stretch wrist mode)."""
    if mass_kg <= 0 or height_m <= 0:
        raise ValueError(f"mass_kg and height_m must be positive, got {mass_kg}, {height_m}")
    l_h = WINTER_HAND_LEN_FRAC * height_m
    m = WINTER_HAND_MASS_FRAC * mass_kg
    return WINTER_HAND_COM_FRAC * l_h, m, m * (WINTER_HAND_KPROX_FRAC * l_h) ** 2


def hang_frequency_hz(lc: float, m: float, J: float) -> float:
    """Linearised gravity-only frequency about a vertical hang (no spring, no damping)."""
    return math.sqrt(m * rt.ref.G * lc / J) / (2.0 * math.pi)


def _frame(start: str) -> float:
    if start not in STARTS:
        raise ValueError(f"start must be one of {STARTS}, got {start!r}")
    return -1.0 if start == "extension" else 1.0     # theta = sign * q


def elbow_params(*, start: str, mass_kg: float = 72.0, height_m: float = 1.75,
                 rest_q_deg: float = 90.0, accessory_kg: float | None = None,
                 accessory_m: float | None = None, B: float = rt.ref.D_NOMINAL,
                 k_passive: float = 0.0, k_passive_ref_q_deg: float | None = None,
                 extensor_scale: float = 1.0, wall_k: float | None = ELBOW_WALL_K,
                 **active) -> rt.Params:
    """Params for an elbow drop in the frame of the muscle `start` stretches.

    `rest_q_deg` is the gravity rest (see POSTURE in the module note). `active`
    passes the core's active terms (tone, k_f, k_df, k_v, ...) through
    unchanged; they act on the stretched muscle. `k_passive` is a linear spring
    about `k_passive_ref_q_deg` (default: the gravity rest), the K the elbow
    literature fits. B is the TISSUE damping; it defaults to 0.2 N.m.s/rad,
    inside the 0.1-0.3 non-pendulum elbow range (MacKay 1986, via Lin 2003).
    `extensor_scale` multiplies the tissue spring and damping on an extension
    start (Huang 2016's controls: about EXTENSOR_SCALE_HUANG); default 1, the
    mirror-symmetric model. `wall_k` is the range-wall steepness (None = the
    reference knee wall, K_LIM 3, kept for scorer-safety tests).
    """
    if extensor_scale <= 0:
        raise ValueError(f"extensor_scale must be positive, got {extensor_scale}")
    if start == "extension":
        k_passive *= extensor_scale
        B *= extensor_scale
    sign = _frame(start)
    if not ELBOW_RANGE_DEG[0] <= rest_q_deg <= ELBOW_RANGE_DEG[1]:
        raise ValueError(f"rest_q_deg {rest_q_deg} is outside the elbow's range {ELBOW_RANGE_DEG}")
    bad = [k for k in ABSOLUTE_ANGLE_PARAMS if active.get(k)]
    if bad:
        raise ValueError(f"{bad} are absolute knee-convention angles and are not mapped to the elbow frame")
    lc, m, J = forearm_from_anthropometry(mass_kg, height_m, accessory_kg=accessory_kg, accessory_m=accessory_m)
    incline = rest_q_deg - 90.0 if start == "extension" else -90.0 - rest_q_deg
    lo, hi = sorted(sign * q for q in ELBOW_RANGE_DEG)
    k_ref_q = rest_q_deg if k_passive_ref_q_deg is None else k_passive_ref_q_deg
    return rt.Params(lc_m=lc, m_kg=m, j_kgm2=J, B=B, incline_deg=incline,
                     lim_lo_deg=lo, lim_hi_deg=hi, lim_k=wall_k,
                     k_passive=k_passive, k_passive_ref_deg=sign * k_ref_q, **active)


def _passive_torque_theta(p: rt.Params, th: float) -> float:
    """Gravity + spring + range wall at frame angle th (rad), the passive torque at rest."""
    lc, m, _ = rt.inertia(p)
    tau = -m * rt.ref.G * lc * math.cos(th + math.radians(p.incline_deg))
    if p.k_passive:
        tau -= p.k_passive * (th - math.radians(p.k_passive_ref_deg))
    if p.use_limit:
        lo, hi = math.radians(p.lim_lo_deg), math.radians(p.lim_hi_deg)
        k = rt.ref.K_LIM if p.lim_k is None else p.lim_k
        tau += -math.exp(k * (th - hi)) + math.exp(k * (lo - th))
    return tau


def passive_rest_q_deg(p: rt.Params, *, start: str) -> float:
    """Where the PASSIVE forearm rests (gravity, spring, walls), anatomical q, deg.

    Solved directly (bisection on the passive torque over the range, walls
    included), not by simulation. The root in the bracket is the stable one:
    gravity's unstable balance point is 180 deg from the rest, outside the
    145 deg range plus the 0.5 rad margin for every rest_q_deg the module
    accepts, and the spring and walls only add restoring torque.
    """
    sign = _frame(start)
    a, b = math.radians(p.lim_lo_deg) - 0.5, math.radians(p.lim_hi_deg) + 0.5
    fa, fb = _passive_torque_theta(p, a), _passive_torque_theta(p, b)
    if not (fa > 0 > fb):
        raise ValueError("passive torque does not bracket a single rest over the range")
    for _ in range(200):
        c = 0.5 * (a + b); fc = _passive_torque_theta(p, c)
        if fc > 0:
            a = c
        else:
            b = c
    return sign * math.degrees(0.5 * (a + b))


def simulate_elbow(p: rt.Params, *, start: str, release_q_deg: float,
                   duration: float = 10.0, record_torques: bool = False):
    """Run an elbow drop and return anatomical flexion `q_deg` (and torques on q).

    Refuses a release on the wrong side of the PASSIVE rest for `start`: there
    the limb would fall the other way, shortening the muscle the frame models,
    and its tone and reflex would act on the wrong side. Limitation: tone
    shifts the actual rest toward the stretched muscle's side, so a release
    between the passive and the tone-shifted rest is accepted; release well
    clear of the rest (as both published protocols do).
    """
    sign = _frame(start)
    rest = passive_rest_q_deg(p, start=start)
    if start == "extension" and not release_q_deg < rest:
        raise ValueError(f"extension start must release below the rest ({rest:.1f} deg), got {release_q_deg}")
    if start == "flexion" and not release_q_deg > rest:
        raise ValueError(f"flexion start must release above the rest ({rest:.1f} deg), got {release_q_deg}")
    r = rt.simulate(p, theta0_deg=sign * release_q_deg, duration=duration, record_torques=record_torques)
    out = {"t": r["t"], "q_deg": sign * r["theta_deg"], "start": start, "rest_q_deg": rest}
    if record_torques:
        # Torques act on theta; on q they carry the frame's sign.
        out["torques"] = {k: sign * np.asarray(v) for k, v in r["torques"].items()}
    return out


def de_lima_bare(**kw) -> tuple[rt.Params, float]:
    """de Lima et al. 2023 posture: arm at the side (shoulder 0), released from 90 deg flexion.

    Returns (params, release_q_deg); simulate with start="flexion" (a fall into
    extension, the only start this posture allows). The gravity rest is full
    extension, so the swing ends against the extension wall, not at mid-range
    (plan 2.1). de Lima's participants were stroke patients: a patient
    comparison, not a healthy reference.
    """
    return elbow_params(start="flexion", rest_q_deg=0.0, **kw), 90.0


def lin_apparatus(*, start: str, rig_j: float, mass_kg: float = 64.0, height_m: float = 1.65,
                  k_tissue: float | None = None, B_tissue: float = rt.ref.D_NOMINAL,
                  rig_B: float = LIN_RIG_B, extensor_scale: float = EXTENSOR_SCALE_HUANG,
                  **active) -> tuple[rt.Params, float, dict]:
    """Lin's clock-pendulum rig: supine, upper arm on the bed, forearm pointing UP.

    The forearm is an inverted pendulum about 90 deg flexion (gravity pushes it
    away), the counterweight below the pivot a hanging one; net gravity
    stiffness is m_cw*g*L_cw - m_f*g*l_f (Lin 2003 eq 3), positive here.
    Inertia is forearm + `rig_j` (unprinted: pass LIN_RIG_J_BRACKET's ends).
    Damping is tissue + rig. K_tissue defaults to Lin 2005's weight regression.

    Returns (params, release_q_deg, info) with info = J, k_gravity (m*g*l_c of
    the net gravity, about gravity_zero_deg = 90) and the parts, for scoring.
    """
    if rig_j <= 0:
        raise ValueError(f"rig_j must be positive, got {rig_j}")
    lf, mf, jf = forearm_from_anthropometry(mass_kg, height_m)
    g = rt.ref.G
    mgl = LIN_COUNTERWEIGHT_KG * g * LIN_COUNTERWEIGHT_M - mf * g * lf
    if mgl <= 0:
        raise ValueError(f"forearm outweighs the counterweight (net m*g*l {mgl:.2f} N.m): the rig would tip")
    k_t = lin_k_tissue(mass_kg) if k_tissue is None else k_tissue
    p = elbow_params(start=start, mass_kg=mass_kg, height_m=height_m, rest_q_deg=90.0,
                     B=B_tissue, k_passive=k_t, extensor_scale=extensor_scale, **active)
    J = jf + rig_j
    m_eff = mf + LIN_COUNTERWEIGHT_KG          # only m*l_c enters gravity; J is set directly
    p = replace(p, lc_m=mgl / (m_eff * g), m_kg=m_eff, j_kgm2=J, B=p.B + rig_B)
    info = dict(J=J, k_gravity=mgl, gravity_zero_deg=90.0, forearm_J=jf, rig_j=rig_j,
                k_tissue=p.k_passive, B_tissue=p.B - rig_B, B_rig=rig_B)
    return p, LIN_RELEASE_Q_DEG[start], info


__all__ = ["forearm_from_anthropometry", "hand_from_anthropometry", "hang_frequency_hz",
           "elbow_params", "passive_rest_q_deg", "simulate_elbow", "de_lima_bare",
           "lin_apparatus", "lin_k_tissue", "LIN_RIG_J_BRACKET", "LIN_RIG_B", "EXTENSOR_SCALE_HUANG",
           "STARTS", "ELBOW_RANGE_DEG"]
