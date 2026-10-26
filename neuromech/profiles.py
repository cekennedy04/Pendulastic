"""Named severity archetypes with a MAS intent, on the Pendulastic angle grid.

This is the trunk's answer to "what should a MAS-3 leg look like on the rig".
It replaces `rig_neuromuscular_model.PROFILES` (retired 2026-10-26, superseded
by `neuromech/`) and keeps that module's three-name contract --
`PROFILES`, `SEVERITY_INTENT`, `simulate` -- so `severity_grading_test.py`
needed no rewrite.

Two differences from the retired module, both deliberate:

  - Mechanisms come from `model_rt`, which is checked against the reference
    MATLAB and mirrored 1:1 by the LabVIEW Formula Node. The retired module
    used a 40 ms reflex delay and a 3 deg critical stretch taken from a
    different paper; the reference's own 50 ms and 1.5 deg are used here.
  - SRS is proportional to baseline tone (`k_srs * tone`), the published form,
    rather than an absolute N.m/rad. Short-range stiffness arises from
    already-bound cross-bridges, so it scales with active force by
    construction; an absolute stiffness lets a flaccid limb have a stiff
    first drop, which is not a thing.

Angle convention OUT of here is Pendulastic's (180 = full extension,
decreasing into flexion), after a still pre-release hold, which is what
`compute_pt_params` consumes.
"""
from __future__ import annotations

import numpy as np

from neuromech import pendulum_sim as ref
from neuromech.model_rt import (DT, Params, catch_and_hold, passive_rest_deg,
                                rig_params, simulate as simulate_model)

HOLD_S = 3.0          # still hold before release, s -- matches motor_reference.PRE_ROLL_S
SWING_S = 14.0        # recorded swing, s
FS_OUT_HZ = 100.0     # the scorer's grid

#: Tone ladder. `tone` is the baseline extensor torque in N.m and `k_f`/`k_df`
#: the delayed force-feedback gains, scaled together along the one severity
#: axis the literature supports (He et al. 1997: no single reflex parameter
#: spans the range). Lambda values are the rungs `build.calibrate_ladder`
#: lands on; held literal here so this library does not move when HEALTHY_REF
#: does -- an archetype that silently redefined itself with every
#: recalibration could not be used to test a recalibration.
_LADDER_LAMBDA = {0: 0.0, 1: 0.25, 2: 0.50, 3: 0.74, 4: 0.80}


def _tone(level: int) -> Params:
    lam = _LADDER_LAMBDA[level]
    return Params(tone=2.0 * lam, k_f=0.75 * lam, k_df=0.025 * lam)


#: The arrest angles, in Pendulastic degrees, that `catch_and_hold` targets.
#: 140 deg = 40 deg of flexion from full extension; 125 deg is at the passive
#: hang, so 127 is the closest arrest that is still a hold rather than a
#: free fall (see catch_and_hold's guard).
_HOLD_PENDULASTIC = {"catch_and_hold_140": 140.0, "catch_and_hold_127": 127.0}


def _hold(pendulastic_deg: float) -> Params:
    return catch_and_hold(pendulastic_deg - 180.0)


PROFILES: dict[str, Params] = {
    "tone_L0": _tone(0),
    "tone_L1": _tone(1),
    "tone_L2": _tone(2),
    "tone_L3": _tone(3),
    "tone_L4": _tone(4),
    # A velocity catch that LETS GO past a stretch length: the clasp-knife.
    "catch_release_high": Params(catch_gain=4.0, catch_vth=1.2, catch_release_deg=25.0),
    # Falls, then arrests and holds -- no oscillation about the arrest point.
    "catch_and_hold_140": _hold(_HOLD_PENDULASTIC["catch_and_hold_140"]),
    "catch_and_hold_127": _hold(_HOLD_PENDULASTIC["catch_and_hold_127"]),
    # Drops to the hang and stops dead: no ring-down at all.
    "fall_no_ringdown": Params(rigid_nm=0.9),
    # A unilateral elastic stop 25 deg past the hang.
    "contracture_stop": Params(contract_k=25.0, contract_deg=-80.0),
    # The rig itself, healthy limb, bearing friction on. Not a pathology --
    # carried here so a grading test can see what the hardware contributes.
    "rig_passive": rig_params(),
}

#: Clinician-intended MAS grade per profile, for rank agreement. Profiles
#: without an entry are deliberately ungraded: they are measurement artefacts
#: or non-spastic findings, and giving them a MAS rank would assert a
#: severity ordering the literature does not support.
SEVERITY_INTENT: dict[str, int] = {
    "tone_L0": 0,
    "tone_L1": 1,
    "tone_L2": 2,
    "tone_L3": 3,
    "tone_L4": 4,
    # Ranked 2, not 3. Mat Rosly et al. report the clasp-knife drop "observed
    # during fast passive mobilisation for subject with moderate spasticity
    # and it is absent for low or severe spasticity" -- a releasing catch is a
    # MID-severity sign, so ranking it with the severe profiles would put this
    # library's own intent at odds with the only published statement about
    # where the trace appears.
    "catch_release_high": 2,
    "catch_and_hold_140": 3,
    "catch_and_hold_127": 2,
}


#: What each profile is designed to exercise. Carried here rather than in
#: scenarios.py so the profile and the reason for it stay together.
PROBES: dict[str, str] = {
    "tone_L0": "the healthy end of the ladder: the reference limb, nothing added",
    "tone_L1": "the mildest rung the scorer can separate from healthy",
    "tone_L2": "mid-ladder; the rung the repeatability block is played at",
    "tone_L3": "severe but still oscillating, so every parameter is measurable",
    "tone_L4": "at the rising-branch peak, past which PT7 inverts",
    "catch_release_high": "R2n, omega_min_n, A1 shape: a catch that LETS GO part-way down",
    "catch_and_hold_140": "falls and arrests at 140 deg Pendulastic with no return swing; "
                          "the no-oscillation refusal path",
    "catch_and_hold_127": "the same arrest 13 deg deeper, just short of the passive hang",
    "fall_no_ringdown": "lead-pipe resistance: linear rather than exponential decay, N -> 0",
    "contracture_stop": "a unilateral elastic stop 25 deg past the hang: asymmetric swing, area_ratio",
    "rig_passive": "the hardware's own bearing friction on a healthy limb -- not a pathology, "
                   "carried so a grading test can see what the rig contributes",
}


def simulate(prof: Params, hold_s: float = HOLD_S, swing_s: float = SWING_S,
             fs_out: float = FS_OUT_HZ) -> dict:
    """One archetype, on the grid the scorer consumes.

    Returns `t`, `angle_deg` (Pendulastic convention, still for `hold_s`
    before release), `release_idx` and `release_t_s`, plus the
    model-convention `theta_deg` and the torque components so a caller can ask
    WHICH element produced a feature of the trace rather than only that the
    trace has it.
    """
    r = simulate_model(prof, duration=swing_s, record_torques=True)
    tg = np.arange(0.0, r["t"][-1], 1.0 / fs_out)
    swing = ref.to_pendulastic_deg(np.interp(tg, r["t"], r["theta_deg"]))
    pre = np.full(int(round(hold_s * fs_out)), swing[0])
    angle = np.concatenate([pre, swing])
    return {
        "t": np.arange(len(angle)) / fs_out,
        "angle_deg": angle,
        "release_idx": len(pre),
        "release_t_s": len(pre) / fs_out,
        "theta_deg": r["theta_deg"],
        "t_sim": r["t"],
        "torques": r["torques"],
        "rest_deg": passive_rest_deg(prof),
        "fs_out_hz": fs_out,
        "profile": prof,
    }
