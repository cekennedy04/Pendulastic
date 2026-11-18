"""Upper-limb pendulum tests on the same De Groote core: the elbow (forearm + hand).

Plan: docs/plans/2026-11-10-001-plan-neuromech-elbow-emulator.md (units U1-U3).

The knee core models ONE muscle group, the one the drop stretches: a release
at theta0 falls toward negative theta, and tone, SRS and the delayed reflex all
oppose that fall. The elbow has two published starts (Huang et al. 2016), and
they stretch different muscles:

  start "extension"   forearm released near full extension, falls into flexion,
                      stretches the extensors (triceps)
  start "flexion"     forearm released in flexion, falls into extension,
                      stretches the flexors (biceps, brachialis)

Rather than add a sign to the active drive inside the core (which the LabVIEW
Formula Node would then have to carry), each start is simulated in its own
frame, chosen so that the fall is toward negative theta:

  extension start   theta = -q        (q = anatomical elbow flexion, deg, 0 = straight)
  flexion start     theta = +q

The core's tone/SRS/reflex then always belong to the stretched muscle, and its
code is untouched. Results come back as anatomical flexion q.

Gravity: the core's torque is -m g l_c cos(theta + incline), stable rest where
theta + incline = -90 deg. Posture is the upper arm's angle from hanging
straight down (shoulder flexion, `upper_arm_deg`); with the forearm hanging
vertically the gravity rest is q_rest = upper_arm_deg (0 = arm at the side,
rest at full extension, i.e. AT the range wall; 90 = upper arm horizontal,
rest at 90 deg of flexion, mid-range like the knee). So:

  extension start   incline = q_rest - 90
  flexion start     incline = -90 - q_rest

No grade and no PT score are produced here (plan E5): elbow traces are scored
with `neuromech.elbow_score`.
"""
from __future__ import annotations

import math
from dataclasses import replace

from . import model_rt as rt

# ── Winter (2009) Table 4.1 ─────────────────────────────────────────────────
# PROVISIONAL: quoted, not yet checked against the printed table (the shank
# fractions model_rt uses come from the same table and are consistent). The
# owner is fetching the book; tests pin these numbers so a correction is one
# visible edit.
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


def _frame(start: str):
    if start not in STARTS:
        raise ValueError(f"start must be one of {STARTS}, got {start!r}")
    return -1.0 if start == "extension" else 1.0     # theta = sign * q


def elbow_params(*, start: str, mass_kg: float = 72.0, height_m: float = 1.75,
                 upper_arm_deg: float = 90.0, accessory_kg: float | None = None,
                 accessory_m: float | None = None, B: float = rt.ref.D_NOMINAL,
                 k_passive: float = 0.0, k_passive_ref_q_deg: float | None = None,
                 **active) -> rt.Params:
    """Params for an elbow drop in the frame of the muscle `start` stretches.

    `active` passes the core's active terms (tone, k_f, k_df, k_v, ...) through
    unchanged; they act on the stretched muscle. `k_passive` is a linear spring
    about `k_passive_ref_q_deg` (default: the gravity rest), the K the elbow
    literature fits. B defaults to the knee reference's 0.2 N.m.s/rad, which is
    a placeholder for the forearm until the literature gate (plan U5) sets it.
    """
    sign = _frame(start)
    if not ELBOW_RANGE_DEG[0] <= upper_arm_deg <= ELBOW_RANGE_DEG[1]:
        raise ValueError(f"upper_arm_deg {upper_arm_deg} puts the gravity rest outside the elbow's range")
    lc, m, J = forearm_from_anthropometry(mass_kg, height_m, accessory_kg=accessory_kg, accessory_m=accessory_m)
    q_rest = upper_arm_deg
    incline = q_rest - 90.0 if start == "extension" else -90.0 - q_rest
    lo, hi = sorted(sign * q for q in ELBOW_RANGE_DEG)
    k_ref_q = q_rest if k_passive_ref_q_deg is None else k_passive_ref_q_deg
    return rt.Params(lc_m=lc, m_kg=m, j_kgm2=J, B=B, incline_deg=incline,
                     lim_lo_deg=lo, lim_hi_deg=hi,
                     k_passive=k_passive, k_passive_ref_deg=sign * k_ref_q, **active)


def simulate_elbow(p: rt.Params, *, start: str, release_q_deg: float,
                   duration: float = 10.0, record_torques: bool = False):
    """Run an elbow drop and return anatomical flexion `q_deg` (and torques, in the frame's sign).

    `release_q_deg` must be on the side of the rest the start names: above the
    rest for a flexion start (it falls toward extension), below it for an
    extension start.
    """
    sign = _frame(start)
    r = rt.simulate(p, theta0_deg=sign * release_q_deg, duration=duration, record_torques=record_torques)
    out = {"t": r["t"], "q_deg": sign * r["theta_deg"], "start": start}
    if record_torques:
        # Torques act on theta; on q they carry the frame's sign.
        out["torques"] = {k: sign * v for k, v in r["torques"].items()}
    return out


def de_lima_bare(start: str = "flexion", **kw) -> tuple[rt.Params, float]:
    """de Lima et al. 2023 posture: arm at the side (shoulder 0), released from 90 deg flexion.

    Returns (params, release_q_deg). The gravity rest is full extension, so the
    swing ends against the extension wall, not at mid-range (plan 2.1).
    """
    return elbow_params(start=start, upper_arm_deg=0.0, **kw), 90.0


def with_active(p: rt.Params, **active) -> rt.Params:
    """Same limb and posture, different active terms."""
    return replace(p, **active)


__all__ = ["forearm_from_anthropometry", "hand_from_anthropometry", "hang_frequency_hz",
           "elbow_params", "simulate_elbow", "de_lima_bare", "with_active", "STARTS", "ELBOW_RANGE_DEG"]
