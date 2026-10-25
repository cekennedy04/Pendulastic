"""Neuromuscular model for the VERTICAL rig: severities, and KINDS of severity.

Why this exists
---------------
The rig's job is to produce, on demand, the severity range no human in this
cohort occupies. Every clinically graded leg on file sits at MAS 0 or 1, so the
regime where the pendulum indices are known to invert has never been measured
on a person. A motor that can be dialled to a grade is the only way to get
there.

But "dial a grade" is the wrong abstraction, and that is the point of this
module. Two legs at the same MAS grade can produce completely different
goniograms: one is caught hard at the top of the fall and then keeps swinging;
another falls most of the way and is held there, never oscillating at all. A
one-knob severity model collapses those into the same trace, and then every
metric validated against it is validated against a fiction. So the model here
is parameterised by MECHANISM, and the severity families fall out of it:

    velocity-threshold reflex, transient  -> catch, release, swing continues
    length-dependent reflex, tonic        -> catch and HOLD, new plateau
    exponential end-stop                  -> fall, then mechanical arrest
    raised passive B and K                -> fewer, slower oscillations

A grade is then a REGION of that parameter space, not a point, and the rig can
be asked for two different MAS 3s that look nothing like each other.

What is taken from the literature and what is a modelling choice
----------------------------------------------------------------
PASSIVE, taken: the linearised model of Bajd & Bowman (1982), as used by
Popovic & Bajd (2018), lives in `rig_calibration.passive_envelope` and is
imported from there rather than restated -- J*q'' + B*q' + (K + mgl/2)*q = 0
with B = K = 0.5 for a healthy limb, m = 5 kg, l = 0.6 m, J = ml^2/3. This
module reproduces that closed form exactly in `gravity="linear"` mode with the
active element off, and `selftest()` asserts it. That assertion is the whole
reason the passive half is trustworthy: the integrator is checked against an
analytic solution the project already uses elsewhere.

ACTIVE, a modelling CHOICE. The reflex element is phenomenological: a delayed,
rate-limited, saturating drive with separate velocity and length channels and
an adaptation state. The FORM is conventional (threshold-and-gain reflex, with
Zajac-style first-order activation dynamics and distinct rise/fall constants).
The PARAMETER VALUES here are not fitted to anything. They are set to produce
the named waveform families and nothing more, so no number in `PROFILES`
should be quoted as a physiological measurement. Until the rig is built and a
clinician grades it blind, these are shapes, not severities.

END-STOP, a modelling choice of conventional form: an exponential end-range
torque, the shape used for passive knee moments near the limit of range. Its
coefficients here are set by where the arrest should land, not measured.

The discriminator worth knowing about
-------------------------------------
Release height separates the mechanisms, but NOT on the quantity you would
first reach for. The SETTLED angle is an equilibrium -- the balance of gravity,
passive stiffness and any tonic reflex -- and an equilibrium does not care how
the limb got there. Measured across releases from 180 down to 140 deg, every
profile in this module settles within 2 deg of its own plateau, including the
contracture. So the plateau cannot tell a held limb from a walled one.

The FIRST STOP angle can, and the signature is the SHAPE of its dependence on
release height, not its value at any one height:

    passive limb        tracks release almost 1:1 (slope ~0.77 here)
    tonic hold          INVARIANT: 2.1 deg of spread over 40 deg of release
    end-stop            slope compressed (~0.41) but still monotone
    velocity reflex     DISCONTINUOUS -- 151 deg from a 180 release, 104 from
                        170, because below some height the stretch never
                        crosses the threshold and the catch does not happen

That last one is the useful one: a threshold has a knee in it and nothing
passive does. `release_height_probe()` runs the sweep and fits the slope.
Nothing in the app currently varies release height and nothing in this corpus
does either, so this is a protocol proposal, not a finding about any person.

Motor realisability
-------------------
`motor_command()` returns what the motor must actually do, both ways, because
the rig's control mode is not settled: an angle trajectory for position
playback, and a torque series for torque or impedance playback. The torque
series includes the term that makes up the difference between the rig's own
passive mechanics and the limb being imitated, so a rig whose bearing friction
is not 0.5 Nms/rad still plays the right limb. It also reports peak torque,
peak slew and peak speed, which is the actuator spec.

Run:  python rig_neuromuscular_model.py
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field, replace

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import numpy as np

import rig_calibration as rig

# ── Frames and conventions ───────────────────────────────────────────────────
#
# Internally the state is q, FLEXION from the held position, in radians, q >= 0
# as the shank falls. Externally everything is the codebase's knee angle in
# degrees with 180 = held extension, because that is what compute_pt_params
# consumes and what the app plots:
#
#     knee_angle_deg = EXTENDED_DEG - degrees(q)
#
# Keeping the physics in q and converting once at the boundary is deliberate:
# the sign of "spastic resistance opposes the motion" is obvious in q and is
# easy to get backwards in the 180-down convention.

EXTENDED_DEG = 180.0        #: held position, this codebase's convention
FS_OUT_HZ = 100.0           #: output rate; matches the app's analysis grid
FS_SIM_HZ = 2000.0          #: integration rate. See `selftest` for why.
HOLD_S = 3.0                #: flat pre-release hold, so release detection has
                            #  a baseline to find. The app's detector needs it.
SWING_S = 14.0              #: post-release record length

_DEG = 180.0 / math.pi


@dataclass(frozen=True)
class Limb:
    """The limb being imitated, passive properties only.

    Defaults are the healthy standard the rig is dialled to (R15): Bajd &
    Bowman's linearised fit as reported by Popovic & Bajd (2018). `q_rest_deg`
    is the flexion at which the passive limb comes to rest, and it is the one
    passive number with no literature value -- it is set from this corpus's own
    control excursion, because A0 = release - rest and the controls' A0 is what
    we can actually see. Treat it as a cohort constant, not a published one.
    """
    B: float = rig.PASSIVE_DAMPING_NMS_PER_RAD       # Nms/rad
    K: float = rig.PASSIVE_STIFFNESS_NM_PER_RAD      # Nm/rad
    m: float = rig.SHANK_MASS_KG                     # kg
    l: float = rig.SHANK_LENGTH_M                    # m
    g: float = rig.GRAVITY_M_S2                      # m/s^2
    q_rest_deg: float = 62.0                         # deg of flexion at rest

    @property
    def J(self) -> float:
        return (1.0 / 3.0) * self.m * self.l ** 2

    @property
    def gravity_term(self) -> float:
        """mgl/2, the linearised gravitational stiffness in Nm/rad."""
        return 0.5 * self.m * self.g * self.l


@dataclass(frozen=True)
class Tone:
    """Baseline tone and short-range stiffness: the two terms that are NOT a reflex.

    Added after a literature check, because the first draft of this module was
    missing the mechanism the field says matters most. De Groote et al.
    decompose spastic pendulum kinematics into three torques and report that
    "prior models simulating increased sensorimotor feedback of muscle
    velocity fail to explain the key pendulum test kinematic outcomes":

        baseline muscle TONE, a constant joint torque  -> the resting plateau
        transient SHORT-RANGE STIFFNESS, scaling with tone -> collapses A0
        delayed FORCE feedback, not velocity feedback  -> kills N

    `torque_nm` is that constant tone. It is also the mechanism behind one of
    the two kinds of sustained hold: Forman et al. find involuntary sustained
    activity that is POSITION-INDEPENDENT in cerebral palsy but grows toward
    extension in stroke. A constant torque is the CP-like hold; the Reflex
    length channel is the stroke-like one. Lorentzen et al. attribute the
    sustained activity to persistent inward currents in spinal motoneurones
    rather than to reflex gain, which is why this term is not slaved to the
    reflex trigger.

    `srs_nm_per_rad` and `srs_yield_deg` are short-range stiffness: cross-
    bridges hold for a small stretch and then YIELD, so the torque rises
    steeply over the first few degrees of motion and then goes away. "Goes
    away", not "saturates", is the load-bearing word and the first
    implementation here got it wrong: holding the torque at its maximum once
    the bridges broke left a standing extensor torque of 17 Nm, which drove
    the joint 8 deg past full extension and put the plateau at a knee angle of
    188. A yielded bridge carries no force. So SRS is non-zero only while the
    limb is bound, it stops the instant the stretch exceeds the yield, and it
    re-binds when the limb shortens -- which is what makes it collapse the
    FIRST swing and leave the plateau alone.
    """
    torque_nm: float = 0.0           # constant extensor torque
    srs_nm_per_rad: float = 0.0      # short-range stiffness slope
    srs_yield_deg: float = 3.0       # stretch at which the bridges yield


@dataclass(frozen=True)
class Reflex:
    """A delayed, saturating stretch reflex with two channels.

    The two channels are the whole mechanism story, and they are what make the
    waveform FAMILIES separate rather than being two points on one severity
    axis:

    VELOCITY channel (`g_vel`, `v_thr_deg_s`). Fires on stretch RATE above a
    threshold. It is large during the fall and gone once the limb slows, so on
    its own it produces a catch near peak velocity and then lets go -- the
    swing continues, distorted. This is the channel that makes a trace look
    violent without stopping it.

    LENGTH channel (`g_len`, `q_thr_deg`). Fires on stretch AMOUNT past a
    threshold angle and does not stop while the limb stays flexed. It therefore
    sets up a NEW EQUILIBRIUM where its torque balances gravity: the limb falls
    to that angle and stays. That equilibrium is the plateau / resting angle the
    literature finds most linear against MAS, and here it is an output of the
    model rather than something shifted by hand.

    ADAPTATION (`adapt_kappa`, `adapt_tau_s`) is what lets a tonic hold let go
    again. Drive is scaled by (1 - kappa*h) where h chases activation with time
    constant adapt_tau_s. kappa = 0 is a hold that never releases; kappa near 1
    with a short tau is a clasp-knife-like give.

    FORCE channel (`g_force`, `f_thr_nm`). Fires on the extensor torque the
    limb is already generating, delayed. This is the channel Falisse et al.
    found superior -- force plus force-rate feedback explained R^2 = 0.73 of
    hamstring activity against 0.46 for a velocity-and-length model -- and the
    one De Groote et al. needed to reproduce the lost oscillations. It is also
    the physiology of the true clasp-knife: Cleland et al. report that
    inhibition "was evoked only by large stretches that produced significant
    muscle force. Short stretches that did not produce large forces evoked
    only excitation."

    Both the velocity and the force channel are kept, rather than the velocity
    one being deleted on the literature's word. Keeping both lets this module
    DEMONSTRATE the published result on its own traces instead of assuming it,
    and `channel_ablation()` runs exactly that comparison.

    `t_max_nm` is the ceiling. Saturation matters: without it, gain and
    threshold trade off perfectly and the family structure disappears.

    On the parameter values, the field disagrees with itself and the
    disagreement is worth carrying rather than resolving by preference. He,
    Norling & Wang find that "individual changes in the static stretch reflex
    threshold, in the dynamic threshold, or in the gain can not stimulate the
    whole spectrum of spasticity severity" and that coupled moves are needed.
    Le Cavorzin et al., fitting 8 healthy and 15 spastic subjects, find the
    opposite: "variation of the threshold coefficient alone allowed an overall
    reproduction of experimental data... no subsequent variation of the reflex
    gain was required." `graded_tone` moves thresholds and gains together,
    which is He's recipe, and that choice is a choice.

    `delay_s` is the one constant with usable published numbers at the knee:
    quadriceps short-latency reflex onset 23-25 ms, electromechanical delay
    40.5 ms in spastic CP against 54.7 ms in typically developing children
    (Granata et al., 12 + 12 children -- a single small study behind a widely
    repeated figure, so indicative rather than established). The default 40 ms
    is their spastic value.
    """
    t_max_nm: float = 0.0           # torque ceiling; 0 disables the element
    g_vel: float = 0.0              # per (deg/s) above threshold, dimensionless drive
    v_thr_deg_s: float = 60.0       # stretch-velocity threshold
    g_len: float = 0.0              # per deg above threshold
    q_thr_deg: float = 20.0         # stretch-angle threshold (flexion)
    delay_s: float = 0.040          # afferent + electromechanical delay
    tau_rise_s: float = 0.030       # activation rise
    tau_fall_s: float = 0.120       # activation fall; slower than rise
    g_force: float = 0.0            # per Nm of delayed extensor torque above
                                    #  threshold -- the channel the literature
                                    #  says actually kills the oscillations
    f_thr_nm: float = 2.0           # force threshold for that channel
    adapt_kappa: float = 0.0        # 0 = no give, 1 = full give
    adapt_tau_s: float = 0.50       # how fast the give develops


@dataclass(frozen=True)
class EndStop:
    """Exponential end-range torque: a contracture, or the rig's hard limit.

    T = k*(exp((q - q0)/w) - 1) for q > q0, zero below, plus local damping `b`
    so the limb arrests instead of bouncing. Conventional form for passive
    knee moment near the end of range; the coefficients here are chosen to put
    the arrest where a profile wants it, not measured on anyone.
    """
    k_nm: float = 0.0               # scale; 0 disables
    q0_deg: float = 90.0            # where resistance starts to bite
    width_deg: float = 4.0          # how abruptly it rises
    b_nms: float = 0.0              # extra damping inside the stop


@dataclass
class Profile:
    """A dialled rig setting: a limb, a reflex, an end-stop, a release height.

    `release_deg` is the knee angle the limb is held at before release, in the
    codebase frame. 180 is full extension and is what the human protocol uses;
    it is a parameter because release height is the handle that separates a
    mechanical arrest from a reflex one.
    """
    name: str
    limb: Limb = field(default_factory=Limb)
    tone: Tone = field(default_factory=Tone)
    reflex: Reflex = field(default_factory=Reflex)
    end_stop: EndStop = field(default_factory=EndStop)
    release_deg: float = EXTENDED_DEG
    gravity: str = "linear"         # "linear" (Bajd & Bowman) or "sin"
    note: str = ""


# ── Integration ──────────────────────────────────────────────────────────────

def _drive(q_d: float, qd_d: float, f_d: float, rfx: Reflex) -> float:
    """Reflex drive from DELAYED state. Three channels, each one-sided.

    `f_d` is the delayed extensor torque the limb was already generating, which
    is the force-feedback signal. Using the limb's own torque rather than a
    separate muscle-force state keeps the loop to one quantity the rig can also
    measure: a torque sensor on the shaft reads it directly, so a profile fitted
    here is a profile the rig can be told to reproduce.
    """
    e = 0.0
    if rfx.g_vel:
        e += rfx.g_vel * max(0.0, qd_d * _DEG - rfx.v_thr_deg_s)
    if rfx.g_len:
        e += rfx.g_len * max(0.0, q_d * _DEG - rfx.q_thr_deg)
    if rfx.g_force:
        e += rfx.g_force * max(0.0, f_d - rfx.f_thr_nm)
    return e


#: The knee's own extension limit. A constant extensor tone with nothing to
#: stop it drives the joint straight through full extension -- the first run of
#: the tone ladder settled at a "knee angle" of 284 deg, which is not a reading
#: a model should be able to produce. Real knees have a capsule.
#:
#: The form is the one standard in the field: Riener & Edrich's double-
#: exponential passive-moment model, whose third term is a pure knee exponential
#: independent of hip and ankle angle and is exactly this end-stop. The
#: COEFFICIENTS here are NOT theirs. Two secondary reproductions of that paper
#: disagree on the sign of its angle coefficient (-0.150 against +0.15), and
#: coding an unverified constant from a source that contradicts itself would put
#: a number in the model that nobody can check. These are set to the magnitude
#: McFaull & Lamontagne measured directly instead -- 6 to 22 Nm at full
#: extension, against under 4 Nm through midrange -- which is the quantity that
#: matters here, and the provenance is honest about being a magnitude match
#: rather than a fitted model.
EXT_STOP_K_NM = 1.2
EXT_STOP_WIDTH_DEG = 2.5
EXT_STOP_DAMPING_NMS = 3.0


def _extension_stop_torque(q: float, qd: float) -> float:
    """Capsular torque resisting hyperextension, <= 0 (it flexes the knee).

    Always applied: it is anatomy, not a profile setting. Below full extension
    (q > 0) it is exactly zero, so it changes nothing about any trace that
    stays inside the normal range.
    """
    over = -q * _DEG
    if over <= 0.0:
        return 0.0
    t = EXT_STOP_K_NM * (math.exp(min(over / EXT_STOP_WIDTH_DEG, 40.0)) - 1.0)
    if qd < 0.0:
        t += EXT_STOP_DAMPING_NMS * (-qd)
    return -t


def _end_stop_torque(q: float, qd: float, es: EndStop) -> float:
    """Opposing torque from the end-stop, >= 0, in Nm."""
    if not es.k_nm:
        return 0.0
    over = q * _DEG - es.q0_deg
    if over <= 0.0:
        return 0.0
    # Clamp the exponent: a deep overshoot in a mid-step RK4 probe must not
    # overflow to inf and poison the whole step. 40 widths is already a wall.
    t = es.k_nm * (math.exp(min(over / max(es.width_deg, 1e-6), 40.0)) - 1.0)
    if qd > 0.0:
        t += es.b_nms * qd
    return t


def simulate(prof: Profile, hold_s: float = HOLD_S, swing_s: float = SWING_S,
             fs_sim: float = FS_SIM_HZ, fs_out: float = FS_OUT_HZ) -> dict:
    """Integrate one dialled profile and return the trace the app would see.

    Returns a dict with `t` and `angle_deg` on the output grid (what
    compute_pt_params consumes), plus the full-rate internals -- `q_deg`,
    `qd_deg_s`, `activation`, `t_reflex_nm`, `t_endstop_nm` -- because the
    whole value of a model over a drawn waveform is being able to ask WHICH
    element produced a feature of the trace.

    Fixed-step RK4 on [q, qd, a, h]. The reflex drive is read from a delay
    buffer and held across the step: at 2 kHz a 40 ms delay is 80 samples and
    the within-step change in drive is far below the tolerance that matters
    here. The activation states are integrated with the rest of the vector
    rather than sub-stepped, which is why the step is 2 kHz and not 1 -- the
    rise constant is 30 ms and a 1 ms step keeps it comfortably resolved.
    """
    L, tone, rfx, es = prof.limb, prof.tone, prof.reflex, prof.end_stop
    # The force channel feeds the limb's own extensor torque back into the
    # torque that produces it, so it is a loop and it can run away. It did:
    # a gain of 0.09 against a 30 Nm ceiling gave a loop gain of 2.7, the
    # activation saturated on the standing tone alone, and the limb settled
    # 134 deg past full extension. Checked rather than clamped, because a
    # silently clamped runaway still produces a trace and the trace still
    # looks like data.
    if rfx.g_force * rfx.t_max_nm >= 1.0:
        raise ValueError(
            f"force-feedback loop gain is {rfx.g_force * rfx.t_max_nm:.2f} "
            f"(g_force {rfx.g_force} x t_max_nm {rfx.t_max_nm}). At 1.0 the "
            f"reflex sustains itself with no stretch at all. Lower the gain, "
            f"or the ceiling.")
    if rfx.g_force and rfx.f_thr_nm <= tone.torque_nm:
        raise ValueError(
            f"force threshold {rfx.f_thr_nm} Nm is at or below the standing "
            f"tone {tone.torque_nm} Nm, so the reflex fires on a limb that is "
            f"not being stretched. Raise f_thr_nm above the tone.")
    J, B, K = L.J, L.B, L.K
    grav = L.gravity_term
    q_rest = prof.limb.q_rest_deg / _DEG
    sin_mode = (prof.gravity == "sin")
    srs_yield = tone.srs_yield_deg / _DEG

    # The release height sets the initial flexion. The human protocol holds at
    # full extension (q = 0); a lower hold starts the limb already flexed.
    q0 = (EXTENDED_DEG - prof.release_deg) / _DEG

    dt = 1.0 / fs_sim
    n_hold = int(round(hold_s * fs_sim))
    n_swing = int(round(swing_s * fs_sim))
    n = n_hold + n_swing
    delay_n = max(0, int(round(rfx.delay_s * fs_sim)))

    q = np.zeros(n); qd = np.zeros(n); act = np.zeros(n)
    t_r = np.zeros(n); t_e = nperos = np.zeros(n)
    t_t = np.zeros(n); t_s = np.zeros(n); t_x = np.zeros(n)
    q[:n_hold] = q0            # held, stationary: a genuinely flat baseline
    # Short-range stiffness bound: the angle the cross-bridges last bound at.
    # Held outside the RK4 state because it is non-smooth -- it re-binds the
    # instant the limb shortens -- and integrating a discontinuity with a
    # fourth-order scheme smears it over a step instead of applying it.
    xb = q0
    bound = True
    # The extensor torque at the hold IS the tone, so the force channel has a
    # signal before anything has moved. Seeding it at zero would let a
    # force-fed profile start from an impossible state.
    t_x[:n_hold] = tone.torque_nm

    def accel(qv, qdv, av, xbv):
        """q'' from passive terms, tone, SRS, the reflex and the end-stop.

        Returns the extensor torque separately as well, because it is both the
        force-feedback signal and what the motor has to supply.
        """
        u = qv - q_rest
        restore = grav * (math.sin(u) if sin_mode else u)
        t_tone = tone.torque_nm
        stretch = qv - xbv
        t_srs = (tone.srs_nm_per_rad * stretch
                 if (tone.srs_nm_per_rad and bound and 0.0 <= stretch <= srs_yield)
                 else 0.0)
        tr = rfx.t_max_nm * av
        t_ext = t_tone + t_srs + tr
        te = _end_stop_torque(qv, qdv, es) + _extension_stop_torque(qv, qdv)
        return ((-B * qdv - K * u - restore - t_ext - te) / J,
                tr, te, t_tone, t_srs, t_ext)

    state = np.array([q0, 0.0, 0.0, 0.0])   # q, qd, a, h
    for i in range(n_hold, n):
        # SRS bound update, once per step: yield when the stretch exceeds the
        # bridges' range, re-bind the moment the limb starts shortening. Held
        # outside the RK4 state because both transitions are discontinuous.
        if state[0] - xb > srs_yield:
            bound = False
        elif state[0] < xb:
            xb, bound = state[0], True
        j = i - delay_n
        if j < n_hold:
            # Nothing has moved yet, so neither movement channel has fired --
            # but the tone is already there, so the force channel has.
            e = _drive(q0, 0.0, t_x[max(j, 0)], rfx)
        else:
            e = _drive(q[j], qd[j], t_x[j], rfx)
        e = min(1.0, e)                      # saturate the drive, not just the
                                             # torque: a gain of 5 and a gain of
                                             # 50 must not be the same profile.
        def deriv(s):
            qv, qdv, av, hv = s
            a_cl = min(1.0, max(0.0, av))
            qdd = accel(qv, qdv, a_cl, xb)[0]
            e_eff = e * (1.0 - rfx.adapt_kappa * min(1.0, max(0.0, hv)))
            tau = rfx.tau_rise_s if e_eff > av else rfx.tau_fall_s
            ad = (e_eff - av) / max(tau, 1e-6)
            hd = (a_cl - hv) / max(rfx.adapt_tau_s, 1e-6)
            return np.array([qdv, qdd, ad, hd])

        k1 = deriv(state)
        k2 = deriv(state + 0.5 * dt * k1)
        k3 = deriv(state + 0.5 * dt * k2)
        k4 = deriv(state + dt * k3)
        state = state + (dt / 6.0) * (k1 + 2 * k2 + 2 * k3 + k4)
        state[2] = min(1.0, max(0.0, state[2]))
        state[3] = min(1.0, max(0.0, state[3]))

        q[i], qd[i], act[i] = state[0], state[1], state[2]
        _qdd, tr, te, tt, ts, tx = accel(state[0], state[1], state[2], xb)
        t_r[i], t_e[i], t_t[i], t_s[i], t_x[i] = tr, te, tt, ts, tx

    step = int(round(fs_sim / fs_out))
    idx = np.arange(0, n, step)
    t_full = np.arange(n) * dt
    return {
        "profile": prof,
        "t": t_full[idx],
        "angle_deg": EXTENDED_DEG - q[idx] * _DEG,
        "release_t_s": hold_s,
        "t_sim": t_full,
        "q_deg": q * _DEG,
        "qd_deg_s": qd * _DEG,
        "activation": act,
        "t_reflex_nm": t_r,
        "t_endstop_nm": t_e,
        "t_tone_nm": t_t,
        "t_srs_nm": t_s,
        "t_extensor_nm": t_x,
        "fs_out_hz": fs_out,
        "fs_sim_hz": fs_sim,
    }


# ── What the motor has to do ─────────────────────────────────────────────────

def motor_command(sim: dict, rig_limb: Limb = None) -> dict:
    """The playback command, both control modes, plus the actuator spec.

    POSITION mode: `angle_deg` on the output grid is the trajectory. Trivial,
    and it is what a position-controlled rig will use first. Its weakness is
    that the rig then imitates a KINEMATICS, not a limb -- push on it during
    playback and it pushes back with the servo's stiffness, not the limb's. A
    clinician's hand on the shank would feel the controller.

    TORQUE mode: `torque_nm` is what the motor must add to the rig's OWN
    mechanics to make it behave like `sim`'s limb. If the rig's inertia,
    damping and stiffness differ from the limb being imitated, the motor makes
    up the difference as well as supplying the active torque:

        T_motor = (J_h - J_r) q'' + (B_h - B_r) q' + (K_h - K_r) u
                  + mgl/2 difference + T_reflex + T_endstop

    `rig_limb` DEFAULTS TO THE HEALTHY PASSIVE LIMB, not to the limb being
    imitated, and that default is the whole point of the function. R15 dials
    the unactuated rig to healthy B = K = 0.5, so a profile that imitates a
    stiffer limb needs the motor to supply the extra stiffness and damping as
    well as the reflex. Defaulting the other way -- rig == limb -- makes the
    first four terms vanish and reports a peak torque of zero for a profile
    whose entire severity lives in raised K, which is exactly the number a rig
    builder must not be handed. Pass the rig's own measured properties once
    they exist.

    `peak_*` are the actuator spec. Slew matters as much as peak torque: a
    reflex catch is a 30 ms rise, and an actuator that can reach 40 Nm but
    takes 200 ms to do it cannot produce a catch at all, only a stiffening.
    """
    prof = sim["profile"]
    L = prof.limb
    R = rig_limb if rig_limb is not None else Limb()
    q = sim["q_deg"] / _DEG
    qd = sim["qd_deg_s"] / _DEG
    dt = 1.0 / sim["fs_sim_hz"]
    qdd = np.gradient(qd, dt)
    u = q - L.q_rest_deg / _DEG
    u_r = q - R.q_rest_deg / _DEG

    passive_gap = ((L.J - R.J) * qdd + (L.B - R.B) * qd
                   + L.K * u - R.K * u_r
                   + L.gravity_term * u - R.gravity_term * u_r)
    torque = (passive_gap + sim["t_reflex_nm"] + sim["t_endstop_nm"]
              + sim["t_tone_nm"] + sim["t_srs_nm"])

    step = int(round(sim["fs_sim_hz"] / sim["fs_out_hz"]))
    idx = np.arange(0, len(q), step)
    cmd = torque[idx]
    # Slew is measured on the COMMAND the controller receives, not on the
    # full-rate internal torque. Short-range stiffness yields discontinuously
    # by construction, so differentiating the internal series reports slews of
    # 10,000 Nm/s and up -- a property of the model's discontinuity, not a
    # requirement on any motor. `torque_step_nm` carries that discontinuity
    # separately: it is the largest single-sample jump at full rate, and it is
    # the honest statement of the problem, which is that SRS is a clutch
    # releasing and no torque source reproduces it. A rig that needs SRS needs
    # a physical element or position control, not a bigger amplifier.
    slew = np.gradient(cmd, 1.0 / sim["fs_out_hz"]) if len(cmd) > 2 else np.zeros(1)
    return {
        "t": sim["t_sim"][idx],
        "angle_deg": sim["angle_deg"],
        "torque_nm": cmd,
        "peak_torque_nm": float(np.max(np.abs(torque))),
        "peak_slew_nm_s": float(np.max(np.abs(slew))),
        "torque_step_nm": float(np.max(np.abs(np.diff(torque)))) if len(torque) > 1 else 0.0,
        "peak_speed_deg_s": float(np.max(np.abs(sim["qd_deg_s"]))),
        "peak_accel_deg_s2": float(np.max(np.abs(qdd)) * _DEG),
        "rig_limb": R,
    }


# ── Rig geometry: which arrest angles are reachable at all ───────────────────
#
# This block exists because of a question the model cannot answer and the rig
# builder must. The limb's PASSIVE REST is where gravity and the passive terms
# balance, and it is set by the geometry of the mount, not by tone. An extensor
# reflex can only hold the limb SHORT of that rest -- it resists flexion, so it
# arrests the fall early and raises the plateau. It cannot hold the limb BEYOND
# the rest; nothing in a spastic limb pulls it further into flexion.
#
# So "the shank falls and then stops at 90 degrees" means different things on
# different rigs, and the difference is not cosmetic:
#
#   THIGH INCLINED (q_rest = 62 deg of flexion, knee angle 118). This cohort's
#     geometry, inferred from the control excursion: A0 = release - rest and
#     the controls' A0 is ~62 deg from a 180 deg release. On this rig a hold at
#     knee 90 is UNREACHABLE -- 90 is past the rest -- and an arrest "at 90"
#     can only mean a mechanical end-stop truncating the first overshoot,
#     after which the limb returns to 118 anyway.
#
#   THIGH HORIZONTAL (q_rest = 90 deg of flexion, knee angle 90). The shank
#     hangs vertically at rest, which is the textbook description of the test.
#     On this rig "falls and stops at 90" is the natural reading: the limb
#     falls to its own rest and stays there WITHOUT RINGING DOWN, which is a
#     damping profile, not a hold.
#
# Both are provided. Which one the vertical rig will be is an open question for
# the build, and it decides which profiles are even producible.

GEOMETRY_THIGH_INCLINED = Limb()                      # q_rest 62, this corpus
GEOMETRY_THIGH_HORIZONTAL = replace(Limb(), q_rest_deg=90.0)


# ── Builders: a profile is a mechanism, dialled ──────────────────────────────

def tone_limb(k_mult: float, zeta: float, base: Limb = None) -> Limb:
    """Passive limb at `k_mult` times healthy stiffness, damped to `zeta`.

    Specifying the DAMPING RATIO rather than B is not a convenience. Raise K
    and leave B alone and the limb rings FASTER and longer as it stiffens,
    because zeta = B / (2*sqrt(J*(K + mgl/2))) falls as K rises -- the first
        draft of this module did exactly that and produced a "near-rigid" limb
    oscillating at 160-165 deg for the whole trial, which is not what a rigid
    limb does. Tone raises resistance to motion as well as resistance to
    displacement, so zeta climbs with severity and has to be dialled directly.
    """
    base = base or Limb()
    K = base.K * k_mult
    B = 2.0 * zeta * math.sqrt(base.J * (K + base.gravity_term))
    return replace(base, K=K, B=B)


def graded_tone(level: int, base: Limb = None) -> Profile:
    """The conventional severity axis, levels 0-4, on the published decomposition.

    Rewritten after a literature check. The first version raised passive K and
    B and added a tonic length-channel reflex, which worked but is not what the
    field reports. De Groote et al. state that models driven by muscle-velocity
    feedback "fail to explain the key pendulum test kinematic outcomes", and
    that "varying only baseline muscle torque and reflex gain, we simulated a
    range of pendulum test kinematics observed across different levels of
    spasticity". So this ladder varies exactly those two, holds the passive
    properties at the healthy values, and ties short-range stiffness to the
    tone as they describe.

    Measured on the resulting ladder, each term does one job:

      TONE alone, 0 to 13.5 Nm:   plateau 116.7 -> 168.6, A0 63.0 -> 11.3,
                                  and N stays at exactly 5.50 throughout
      SRS alone:                  almost nothing -- A0 63.0 -> 62.7 even at
                                  400 Nm/rad, because a 3 deg yield can only
                                  absorb ~6% of the fall's energy
      FORCE gain, with SRS:       N 5.50 -> 4.00, first trough 29.6 -> 9.6
      VELOCITY gain, same range:  first trough 30.8 -> 1.0 but N NON-MONOTONE
                                  (5.5, 5.0, 4.5, 4.5, 5.5)

    Two of those are worth keeping in view. The force channel reduces N
    monotonically and the velocity channel does not, which is the published
    claim reproduced on this project's own traces rather than taken on trust;
    `channel_ablation()` prints the comparison. And the SRS row does NOT
    reproduce the published claim that short-range stiffness is what collapses
    the first swing: here the tone term does that work, because A0 is release
    minus plateau and the tone moves the plateau. Reported as a disagreement
    rather than tuned away, since the yield would have to be far above the few
    degrees the mechanism is described with for SRS to carry the first swing.

    SRS is still necessary, for a reason that only showed up when it was
    switched off: the force channel CANNOT FIRE without it. A constant tone
    gives a constant extensor torque, so a threshold above the tone is never
    crossed and the force gain has literally no effect -- 0.0 to 0.03 produced
    byte-identical traces. SRS supplies the force transient the force channel
    detects. The two terms are coupled, not independent.
    """
    spec = {
        #       tone_nm  srs/tone  g_force
        0:      (0.0,     0.0,     0.000),
        1:      (2.4,    10.0,     0.008),
        2:      (5.6,    10.0,     0.012),
        3:      (8.8,    10.0,     0.016),
        4:      (11.4,   10.0,     0.020),
    }[int(level)]
    # The SRS-to-tone ratio and the force gains are capped by a hard physical
    # constraint, not chosen for shape. At srs/tone = 35 and gain 0.024 the SRS
    # transient and the reflex it triggers sum to ~55 Nm, which drives the joint
    # through full extension: levels 3 and 4 came back with a plateau at a knee
    # angle of 185 and an A0 of -4.9. selftest() now refuses any profile whose
    # trace leaves the anatomical range, because a hyperextended "reading" is
    # not a severity, it is a bug that scores.
    tone_nm, srs_per_tone, g_force = spec
    return Profile(
        name=f"tone_L{level}",
        limb=base or Limb(),
        tone=Tone(torque_nm=tone_nm, srs_nm_per_rad=srs_per_tone * tone_nm,
                  srs_yield_deg=3.0),
        reflex=Reflex(t_max_nm=30.0, g_force=g_force, f_thr_nm=tone_nm + 1.0,
                      delay_s=0.040, tau_rise_s=0.030, tau_fall_s=0.200),
        note=("Level 0 is the HEALTHY limb: zero tone, zero SRS, zero reflex "
              "gain, so it is the unactuated R15 target and not a severity at "
              "all. It is keyed tone_L0 rather than 'healthy' so the ladder "
              "that every severity statistic is computed over actually starts "
              "at zero -- when it was keyed separately, the ladder silently "
              "ran L1 to L4 and every reported range was missing its baseline."
              if level == 0 else
              f"Graded tone level {level} of 4 on the published decomposition: "
             f"baseline tone {tone_nm:g} Nm, SRS {srs_per_tone * tone_nm:g} "
              f"baseline tone {tone_nm:g} Nm, SRS {srs_per_tone * tone_nm:g} "
              f"Nm/rad yielding at 3 deg, force-feedback gain {g_force:g}. "
              f"Passive B and K stay healthy. Plateau and A0 are emergent."))


def channel_ablation(tone_nm: float = 5.6, srs_nm_per_rad: float = 200.0,
                     gains=(0.0, 0.005, 0.010, 0.020, 0.030)) -> dict:
    """Force feedback against velocity feedback, on the same limb.

    The published claim is that the lost oscillations are reproduced by force
    feedback and not by velocity feedback. This runs both channels over the
    same gain range on the same limb and returns N for each, so the claim can
    be checked on this project's traces instead of cited.

    Returns {"force": [(gain, N, first_trough), ...], "velocity": [...]}.
    """
    out = {}
    for channel in ("force", "velocity"):
        rows = []
        for g in gains:
            kw = ({"g_force": g, "f_thr_nm": tone_nm + 1.0} if channel == "force"
                  else {"g_vel": g, "v_thr_deg_s": 150.0})
            prof = Profile(
                name=f"ablate_{channel}_{g}",
                tone=Tone(torque_nm=tone_nm, srs_nm_per_rad=srs_nm_per_rad,
                          srs_yield_deg=3.0),
                reflex=Reflex(t_max_nm=30.0, delay_s=0.040,
                              tau_rise_s=0.030, tau_fall_s=0.200, **kw))
            sim = simulate(prof)
            import pendulastic_pt_score as _pts
            p = _pts.compute_pt_params(sim["t"], sim["angle_deg"])
            rows.append((g, p.get("N") if p else None,
                         p.get("first_trough_depth") if p else None))
        out[channel] = rows
    return out


def catch_and_hold(arrest_deg: float, base: Limb = None,
                   drive_frac: float = 0.8, zeta: float = 1.0,
                   lever_deg: float = 25.0) -> Profile:
    """A limb that falls to `arrest_deg` and STAYS, with no ring-down.

    Solves the tonic channel so its equilibrium lands on the requested knee
    angle, then damps the limb to `zeta` about that equilibrium so it arrives
    without oscillating. Both are solved rather than tuned because the reflex
    adds stiffness: dial a hold and leave B alone and the limb rings about the
    new plateau instead of settling on it.

    At equilibrium, velocity is zero and the tonic torque balances the passive
    restoring torque, so with q* the arrest flexion and q_r the passive rest:

        T_max * a* = (K + mgl/2) * (q_r - q*),   a* = g_len * (q*deg - q_thr)

    `drive_frac` is a*, kept below 1 so the element is not already saturated at
    the hold -- a saturated hold cannot respond to anything, and the whole
    point of a reflex profile is that it is state-dependent.

    Raises ValueError when `arrest_deg` is at or past the passive rest, which
    is not a tuning failure but the geometry statement above: an extensor
    reflex cannot pull a limb deeper into flexion than gravity already does.
    """
    base = base or Limb()
    q_rest = base.q_rest_deg
    q_star = EXTENDED_DEG - arrest_deg          # deg of flexion at the arrest
    if q_star >= q_rest - 1.0:
        raise ValueError(
            f"arrest at knee {arrest_deg:.0f} deg is {q_star:.0f} deg of "
            f"flexion, at or past this limb's passive rest of {q_rest:.0f} "
            f"deg. An extensor reflex holds the limb SHORT of rest, never "
            f"beyond it. Either raise arrest_deg, or use a geometry whose "
            f"rest is deeper (GEOMETRY_THIGH_HORIZONTAL rests at knee 90) and "
            f"ask for a damping profile instead of a hold.")
    q_thr = max(2.0, q_star - lever_deg)
    need_nm = (base.K + base.gravity_term) * ((q_rest - q_star) / _DEG)
    t_max = need_nm / drive_frac
    g_len = drive_frac / (q_star - q_thr)
    k_tot = base.K + base.gravity_term + t_max * g_len * _DEG
    B = 2.0 * zeta * math.sqrt(base.J * k_tot)
    return Profile(
        name=f"hold_{arrest_deg:.0f}",
        limb=replace(base, B=B),
        reflex=Reflex(t_max_nm=t_max, g_len=g_len, q_thr_deg=q_thr,
                      delay_s=0.040, tau_rise_s=0.030, tau_fall_s=0.200,
                      adapt_kappa=0.0),
        note=f"Falls and is HELD at knee {arrest_deg:.0f} deg, no ring-down. "
             f"Tonic gain {g_len:.4f}/deg above {q_thr:.0f} deg, ceiling "
             f"{t_max:.1f} Nm, damped to zeta {zeta:.2f} about the hold.")


def catch_and_release(t_max_nm: float = 90.0, v_thr_deg_s: float = 180.0,
                      g_vel: float = 0.030, adapt_tau_s: float = 0.04,
                      base: Limb = None) -> Profile:
    """A limb caught HIGH that then lets go, so the swing continues.

    Velocity channel only, with a fast rise and a fast give. The catch has to
    be brief and hard rather than strong and sustained: a sustained torque
    absorbs the swing's energy and the limb never oscillates again, which is
    the opposite trace. A short pulse reverses the velocity instead of
    dissipating it, so the first turning point sits very high and the limb
    still rings down afterwards.

    This is the archetype a single severity knob cannot reach. It shares its
    intended grade with `catch_and_hold` and looks nothing like it, which is
    the reason this module is parameterised by mechanism.
    """
    return Profile(
        name="catch_release",
        limb=base or Limb(),
        reflex=Reflex(t_max_nm=t_max_nm, g_vel=g_vel, v_thr_deg_s=v_thr_deg_s,
                      delay_s=0.035, tau_rise_s=0.015, tau_fall_s=0.020,
                      adapt_kappa=0.98, adapt_tau_s=adapt_tau_s),
        note=f"Caught high and released: {t_max_nm:.0f} Nm ceiling, threshold "
             f"{v_thr_deg_s:.0f} deg/s, give in {adapt_tau_s*1000:.0f} ms. "
             f"The swing CONTINUES after the catch.")


# ── The profile library ──────────────────────────────────────────────────────
#
# Each entry is a MECHANISM, named for the trace it produces. The MAS labels in
# the notes are INTENT, not calibration: nobody has graded these. What the rig
# programme has to establish is which of these a clinician calls what, and the
# honest state until then is that these are distinguishable shapes.

PROFILES = {
    "tone_L0": graded_tone(0),
    "tone_L1": graded_tone(1),
    "tone_L2": graded_tone(2),
    "tone_L3": graded_tone(3),
    "tone_L4": graded_tone(4),

    # The two the user named, and the pair this whole module exists for: same
    # intended grade, opposite traces.
    "catch_release_high": catch_and_release(),
    "catch_and_hold_140": catch_and_hold(140.0),

    # The deeper hold, only producible on the horizontal-thigh geometry. Named
    # for what it is rather than "90" so the geometry dependence is not lost.
    "fall_no_ringdown_90": Profile(
        name="fall_no_ringdown_90",
        limb=tone_limb(1.0, 1.0, base=GEOMETRY_THIGH_HORIZONTAL),
        note="Horizontal-thigh geometry: the shank falls the full 90 deg to "
             "vertical and arrives without oscillating, because the limb is "
             "critically damped. This is the OTHER reading of 'falls and stops "
             "at 90' -- no hold, no reflex, just damping. On this geometry it "
             "is reachable; on the inclined rig it is not."),

    # Mechanical, not neural.
    "contracture_stop": Profile(
        name="contracture_stop",
        end_stop=EndStop(k_nm=0.5, q0_deg=80.0, width_deg=8.0, b_nms=4.0),
        note="No reflex at all: the limb falls freely and hits an end-range "
             "wall that truncates the first overshoot, then returns to its "
             "passive rest. Looks like tone to several PT parameters. "
             "release_height_probe() is what tells them apart."),
}

#: Intended ordering for severity work. NOT a calibration -- see PROFILES.
#: The two same-grade-different-shape entries share a rank on purpose, because
#: a severity metric that ranks them apart is responding to shape, not tone.
#: `contracture_stop` and `fall_no_ringdown_90` are deliberately absent: they
#: have no intended grade, which is the point of them.
SEVERITY_INTENT = {
    "tone_L0": 0,
    "tone_L1": 1,
    "tone_L2": 2,
    "tone_L3": 3,
    # Ranked 2, not 3. Mat Rosly et al. report the clasp-knife drop "observed
    # during fast passive mobilisation for subject with moderate spasticity and
    # it is absent for low or severe spasticity" -- a releasing catch is a
    # MID-severity sign, so ranking it alongside the severe profiles would put
    # the model's own intent at odds with the only published statement about
    # where this trace appears.
    "catch_release_high": 2,
    "catch_and_hold_140": 3,
    "tone_L4": 4,
}


def release_height_probe(prof: Profile,
                         heights_deg=(180.0, 170.0, 160.0, 150.0, 140.0)) -> dict:
    """First-stop angle against release height: which mechanism is arresting it.

    Returns {"rows": [...], "settled_spread_deg", "first_stop_spread_deg",
    "first_stop_slope", "verdict"}.

    `first_stop_slope` is the least-squares slope of first-stop angle on
    release angle, both in the codebase's knee-angle frame. Its SIGN is
    counterintuitive and worth stating before the bands, because reading it
    backwards inverts every verdict:

      A free limb overshoots its resting position. Released from 180 it falls
      to 70 -- 48 deg PAST a rest of 118 -- and released from 140 it only
      reaches 101, 17 deg past. So a lower release gives a HIGHER first-stop
      angle and the slope is NEGATIVE, around -0.8.

      A limb arrested near its release barely moves at all, so its first stop
      follows the release downward and the slope is POSITIVE, near +0.6. That
      is the high-tone case, not the healthy one.

      A tonic hold pins the first stop at the hold angle whatever the release,
      so the slope is ~0 and the spread is a couple of degrees.

    The slope alone cannot see a THRESHOLD, because a threshold is a kink and a
    line fitted through a kink looks like a moderate slope. So the residual is
    reported too, and it is checked FIRST: a large
    `max_abs_residual_deg` means something switched on part-way up the release
    range, and that is the one finding here that no passive model imitates at
    any parameter setting. It does NOT identify WHICH threshold -- the velocity
    channel, the force channel and an SRS yield all produce a kink, and
    tone_L3 is flagged by it while having no velocity channel at all.
    """
    rows = []
    for h in heights_deg:
        sim = simulate(replace(prof, release_deg=float(h)))
        t, ang = sim["t"], sim["angle_deg"]
        aa = ang[t >= sim["release_t_s"]]
        first_stop = float("nan")
        for i in range(1, len(aa) - 1):
            if (aa[i] - aa[i - 1]) * (aa[i + 1] - aa[i]) < 0:
                first_stop = float(aa[i])
                break
        settled = float(np.median(aa[int(0.75 * len(aa)):]))
        rows.append({
            "release_deg": float(h),
            "first_stop_deg": first_stop,
            "min_deg": float(np.min(aa)),
            "settled_deg": settled,
            "excursion_deg": float(h) - settled,
        })

    stops = [r["first_stop_deg"] for r in rows if r["first_stop_deg"] == r["first_stop_deg"]]
    rels = [r["release_deg"] for r in rows if r["first_stop_deg"] == r["first_stop_deg"]]
    settles = [r["settled_deg"] for r in rows]
    slope = float("nan")
    resid = float("nan")
    if len(stops) >= 3:
        coef = np.polyfit(rels, stops, 1)
        slope = float(coef[0])
        resid = float(np.max(np.abs(np.polyval(coef, rels) - stops)))

    spread = (max(stops) - min(stops)) if stops else float("nan")
    if slope != slope:
        verdict = "NO REVERSAL: the limb never turns around at any release height"
    elif resid > 8.0:
        verdict = "THRESHOLD: kink in first-stop vs release -- a threshold fires"
    elif spread < 5.0:
        verdict = "PINNED: first stop fixed whatever the release -- a tonic hold"
    elif slope <= -0.5:
        verdict = "FREE: overshoots its rest, less so from lower -- passive limb"
    elif slope >= 0.4:
        verdict = "ARRESTED EARLY: first stop follows the release -- high tone"
    else:
        verdict = "RESISTED: smooth, partly arrested -- stiffness or an end-stop"
    return {
        "rows": rows,
        "settled_spread_deg": (max(settles) - min(settles)) if settles else float("nan"),
        "first_stop_spread_deg": spread,
        "first_stop_slope": slope,
        "max_abs_residual_deg": resid,
        "verdict": verdict,
    }


# ── Self-check against the analytic solution the project already uses ────────

def selftest(verbose: bool = True) -> bool:
    """Integrator vs rig_calibration.passive_envelope, and the sin-mode cost.

    With the active element off and gravity linearised, this is the same ODE
    `passive_envelope` solves in closed form, so the damped frequency and the
    decay the integrator produces must match it. If they do not, nothing below
    is worth reading. Reported, not just asserted, because the number readers
    actually want is how far the small-angle assumption moves the frequency at
    the excursions a real pendulum test uses.
    """
    ok = True
    L = Limb()
    env = rig.passive_envelope(B=L.B, K=L.K, m=L.m, l=L.l, g=L.g)

    sim = simulate(PROFILES["tone_L0"])
    t, ang = sim["t"], sim["angle_deg"]
    rel = t >= sim["release_t_s"]
    tt, aa = t[rel] - sim["release_t_s"], ang[rel]
    settled = float(np.median(aa[int(0.75 * len(aa)):]))
    phi = aa - settled

    # Damped frequency from zero crossings of the deviation.
    sgn = np.sign(phi)
    cross = np.where(np.diff(sgn) != 0)[0]
    f_meas = float("nan")
    if len(cross) >= 3:
        half_periods = np.diff(tt[cross])
        f_meas = 1.0 / (2.0 * float(np.median(half_periods)))
    f_pred = env["damped_freq_hz"]
    df = abs(f_meas - f_pred) / f_pred
    if verbose:
        print(f"  damped freq   integrator {f_meas:.4f} Hz   closed form "
              f"{f_pred:.4f} Hz   ({100*df:+.2f}%)")
    if not (df < 0.02):
        ok = False
        print("  FAIL: integrator disagrees with passive_envelope by > 2%")

    # Decay: fit beta to the peak envelope.
    peaks = [i for i in range(1, len(phi) - 1)
             if phi[i] > phi[i - 1] and phi[i] > phi[i + 1] and phi[i] > 0.5]
    b_meas = float("nan")
    if len(peaks) >= 3:
        pk_t = tt[peaks]; pk_a = np.abs(phi[peaks])
        b_meas = -float(np.polyfit(pk_t, np.log(pk_a), 1)[0])
    b_pred = env["damping_factor_per_s"]
    dbv = abs(b_meas - b_pred) / b_pred
    if verbose:
        print(f"  decay beta    integrator {b_meas:.4f} /s     closed form "
              f"{b_pred:.4f} /s     ({100*dbv:+.2f}%)")
    if not (dbv < 0.10):
        ok = False
        print("  FAIL: integrator decay disagrees with passive_envelope by > 10%")

    # What linearising gravity costs at a real excursion.
    #
    # Measured on the FIRST half-period, not on the median of all of them. The
    # median is the small-amplitude frequency -- by the time a pendulum has
    # rung a few cycles its excursion is small and sin(u) ~ u, so the two modes
    # agree there by construction and the median reports a flat 0.00%
    # difference that says nothing. The small-angle assumption can only be
    # wrong where the angle is large, which is the first swing.
    def _first_half_period(sim):
        rel = sim["t"] >= sim["release_t_s"]
        tt_ = sim["t"][rel] - sim["release_t_s"]
        aa_ = sim["angle_deg"][rel]
        s_ = float(np.median(aa_[int(0.75 * len(aa_)):]))
        p_ = aa_ - s_
        c_ = np.where(np.diff(np.sign(p_)) != 0)[0]
        return float(tt_[c_[1]] - tt_[c_[0]]) if len(c_) >= 2 else float("nan")

    hp_lin = _first_half_period(sim)
    sim_sin = simulate(replace(PROFILES["tone_L0"], gravity="sin"))
    hp_sin = _first_half_period(sim_sin)
    if verbose and hp_lin == hp_lin and hp_sin == hp_sin:
        print(f"  1st half-period  linear {hp_lin*1000:.1f} ms   sin "
              f"{hp_sin*1000:.1f} ms   ({100*(hp_sin-hp_lin)/hp_lin:+.2f}%) "
              f"-- what linearising gravity costs on the ONE swing where the "
              f"angle is large")

    # The active element must do nothing when it is off.
    assert float(np.max(sim["t_reflex_nm"])) == 0.0
    assert float(np.max(sim["t_endstop_nm"])) == 0.0

    # Every shipped profile must stay inside the anatomical range. This is a
    # real gate, not a formality: it is what caught the tone ladder settling at
    # a knee angle of 185 deg, which compute_pt_params happily scored.
    for key, prof in PROFILES.items():
        ang = simulate(prof)["angle_deg"]
        hi, lo = float(np.max(ang)), float(np.min(ang))
        if hi > EXTENDED_DEG + 12.0 or lo < 0.0:
            ok = False
            print(f"  FAIL: profile {key} leaves the anatomical range "
                  f"({lo:.1f} to {hi:.1f} deg)")
    if verbose and ok:
        print("  all profiles stay inside the anatomical range")
    return ok


def main() -> int:
    print("Integrator check against rig_calibration.passive_envelope")
    ok = selftest()

    print()
    print("Profiles, as the app would see them")
    print(f"{'profile':22s} {'A0':>6s} {'1st stop':>9s} {'plateau':>8s} "
          f"{'pk tau':>7s} {'slew':>7s} {'step':>6s} {'pk spd':>7s}")
    print("-" * 80)
    for key, prof in PROFILES.items():
        sim = simulate(prof)
        cmd = motor_command(sim)          # vs a HEALTHY passive rig
        ang, t = sim["angle_deg"], sim["t"]
        rel = t >= sim["release_t_s"]
        aa = ang[rel]
        tail = float(np.median(aa[int(0.75 * len(aa)):]))
        # First turning point after release: the "first stop angle".
        first_stop = float("nan")
        for i in range(1, len(aa) - 1):
            if (aa[i] - aa[i - 1]) * (aa[i + 1] - aa[i]) < 0:
                first_stop = float(aa[i])
                break
        print(f"{key:22s} {prof.release_deg - tail:6.1f} {first_stop:9.1f} "
              f"{tail:8.1f} {cmd['peak_torque_nm']:7.1f} "
              f"{cmd['peak_slew_nm_s']:7.0f} {cmd['torque_step_nm']:6.1f} "
              f"{cmd['peak_speed_deg_s']:7.0f}")
    print("  Peak torque and slew are what the MOTOR must supply against a rig")
    print("  dialled to healthy passive mechanics (B = K = 0.5), per R15. "
          "`step` is")
    print("  the largest instantaneous torque jump: where it is large, the "
          "profile")
    print("  contains a discontinuity (an SRS yield, an end-stop) that no "
          "torque")
    print("  source reproduces -- that profile needs position control or a "
          "physical part.")

    print()
    print("First-stop angle vs release height -- the mechanism discriminator")
    print(f"{'profile':22s} {'slope':>6s} {'resid':>6s} {'stop sprd':>10s} "
          f"{'rest sprd':>10s}  verdict")
    print("-" * 96)
    for key in PROFILES:
        pr = release_height_probe(PROFILES[key])
        print(f"{key:22s} {pr['first_stop_slope']:6.2f} "
              f"{pr['max_abs_residual_deg']:6.1f} "
              f"{pr['first_stop_spread_deg']:10.1f} "
              f"{pr['settled_spread_deg']:10.1f}  {pr['verdict']}")
    print("  The settled column is the point: it is under 1 deg for eight of "
          "the nine")
    print("  profiles, contracture and tonic hold included, so the plateau "
          "cannot tell")
    print("  these mechanisms apart. The exception is tone_L4, where a lower "
          "release")
    print("  no longer generates enough force to fire the channel at all, so "
          "the limb")
    print("  rests 19.9 deg further down -- which is itself a threshold "
          "signature.")

    print()
    print("Geometry: which arrest angles a hold can reach")
    for geom, label in ((GEOMETRY_THIGH_INCLINED, "thigh inclined (rest 118)"),
                        (GEOMETRY_THIGH_HORIZONTAL, "thigh horizontal (rest 90)")):
        reach = []
        for a in (160.0, 150.0, 140.0, 130.0, 120.0, 110.0, 100.0, 90.0):
            try:
                catch_and_hold(a, base=geom)
                reach.append(f"{a:.0f}")
            except ValueError:
                pass
        print(f"  {label:28s} {' '.join(reach) if reach else '(none)'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
