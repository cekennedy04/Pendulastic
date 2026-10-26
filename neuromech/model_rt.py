"""Real-time (LabVIEW-portable) knee pendulum-test model with programmable spasticity.

This is the model the rig runs. It is the published De Groote et al. (2018) model
(see pendulum_sim.py, the validated RK4 reference) rewritten under the constraints
of a LabVIEW RT loop, plus modules for behaviours the paper does not model.

Portability constraints, each deliberate:
  - Fixed step DT = 1 ms, the RT loop period. No adaptive stepping.
  - The 50 ms reflex delay is an integer ring buffer of past samples, read with
    zero-order hold across RK4 stages. (The reference uses cubic-Hermite reads to
    keep 4th order; at 1 ms that refinement is worth < 0.1 deg -- see the test.)
  - Delayed derivatives are backward differences of the buffer.
  - erf() blends become 0.5*(1+tanh(1.2*x)): LabVIEW's Formula Node has tanh,
    not erf. The blend sharpness constants are smoothing choices, not physiology.
  - Every state is an explicit scalar so the step function maps 1:1 onto a
    Formula Node with shift registers (see labview/model_step_formula_node.txt).

Convention (same as the reference): theta = 0 is full extension, negative is
flexion, gravity -mgl*cos(theta + incline). A knee released from 0 falls into
flexion (omega < 0), which STRETCHES THE QUADRICEPS; returning toward extension
(omega > 0) stretches the hamstrings.

Every module added here defaults to zero, so `Params()` reproduces the
validated reference (pendulum_sim.py) exactly and any divergence from the
published model is something a caller asked for by name. That is why the rig's
own bearing friction is `coulomb_nm = 0.0` by default and lives in
`rig_params()` instead: the rig has friction, De Groote's leg does not, and a
default that quietly differed from the reference would make the two
indistinguishable.

Torque modules (all default off; parameters in Params):
  base      gravity, passive damping B, soft range walls           (reference)
  tone      baseline extensor tone T_b                              (reference)
  srs       short-range stiffness on the first drop                 (reference)
  reflex    delayed force-feedback extensor reflex k_f, k_df        (reference)
  flexor    velocity-threshold hamstring reflex (flexor dominance)  NEW
  catch     clasp-knife: velocity catch with length-dependent release  NEW
  friction  dry (Coulomb) bearing friction of the RIG itself            NEW
  vel/len   delayed velocity and length drives on the extensor reflex,
            and a saturation ceiling on its summed excitation           NEW
  clonus    triggered decaying 5-8 Hz burst, sized in degrees       NEW
  rigidity  lead-pipe (velocity-independent) resistance, + cogwheel  NEW
  contract  unilateral elastic stop: contracture / limited flexion  NEW
  guard     non-relaxation: band-limited voluntary torque           NEW
  spasm     a single mid-swing torque pulse                         NEW
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict, replace

import numpy as np

from neuromech import pendulum_sim as ref

DT = 1e-3                    # s, RT loop period
DELAY_STEPS = int(round(ref.GAMMA / DT))   # 50 samples = 50 ms reflex delay

#: Velocity scale over which a dry-friction torque reverses, rad/s. Coulomb
#: friction is a signum, which an ODE solver cannot integrate; tanh(w/BAND)
#: is the usual regularisation and is what `rigid_nm` already uses, so both
#: dry-friction terms share one band and one LabVIEW-portable expression.
#: 0.05 rad/s is ~2.9 deg/s -- far below the ~250 deg/s of a swing, so the
#: band is invisible in motion and only smooths the reversal.
COULOMB_BAND = 0.05


def blend(x: float) -> float:
    """Smooth 0..1 switch, the portable stand-in for 0.5*(1+erf(x))."""
    return 0.5 * (1.0 + math.tanh(1.2 * x))


@dataclass(frozen=True)
class Params:
    # ── leg and passive tissue (reference) ───────────────────────────────
    s: float = ref.L_SCALE_NOMINAL        # relative leg length -> inertia, mass, lever
    B: float = ref.D_NOMINAL              # passive viscous damping, N.m.s/rad
    incline_deg: float = -35.0            # thigh inclination; rest knee angle ~125 deg (Pendulastic)
    use_limit: bool = True                # soft walls at 0 and -110 deg
    # Explicit inertia, overriding the `s` scaling when all three are set.
    # `s` cannot carry both the swing frequency and the torque scale at once
    # (it ties m to s**2), so anthropometry writes these directly -- see
    # limb_from_anthropometry(). None = derive from `s` as the reference does.
    lc_m: float | None = None             # m,       knee -> shank+foot centre of mass
    m_kg: float | None = None             # kg,      shank+foot mass
    j_kgm2: float | None = None           # kg.m^2,  inertia about the knee
    # ── dry friction of the RIG, not of a leg (default off: see module note) ──
    coulomb_nm: float = 0.0               # N.m, bearing friction magnitude
    # ── reference spasticity (De Groote 2018) ─────────────────────────────
    tone: float = 0.0                     # baseline extensor tone T_b, N.m
    k_f: float = 0.0                      # force-feedback gain (dimensionless)
    k_df: float = 0.0                     # derivative force-feedback gain, s
    k_srs: float = ref.KSRS_NOMINAL       # SRS stiffness per unit tone, 1/rad
    gain_scale: float = 1.0               # multiplies k_f, k_df, k_v, k_l (habituation series)
    # ── extra extensor drives: velocity and length, both delayed by GAMMA ─────
    # De Groote's reflex is force feedback alone. Velocity and length drives
    # are the two classical alternatives, and De Groote's own figures separate
    # them: force feedback inverts the first-swing/second-swing ordering where
    # velocity feedback never does, so a rig that can emulate both can tell a
    # measurement which channel it is looking at. They feed the SAME activation
    # state t_r -- one muscle has one activation, and keeping the state count
    # fixed keeps the Formula Node port 1:1.
    # Distinct from `catch_*`, which is a velocity drive that SHUTS OFF past a
    # stretch (clasp-knife); these two do not let go.
    k_v: float = 0.0                      # N.m per rad/s of stretch velocity above k_v_thr
    k_v_thr: float = 0.0                  # rad/s
    k_l: float = 0.0                      # N.m per rad of stretch above k_l_thr_deg
    k_l_thr_deg: float = 0.0              # deg of flexion past the release angle
    #: Ceiling on the summed extensor excitation, N.m. A muscle cannot produce
    #: unbounded force, and the published model has no limit because its gains
    #: are small; at rig-scale gains the force-feedback loop can run away
    #: (loop gain > 1), which is a numerical artefact, not a severe patient.
    #: 0 = unbounded, i.e. the published behaviour.
    reflex_max_nm: float = 0.0
    # ── flexor (hamstring) reflex: opposes EXTENSION velocity ─────────────
    flex_gain: float = 0.0                # N.m per rad/s above threshold
    flex_vth: float = 1.0                 # rad/s
    # ── clasp-knife catch on the first drop ───────────────────────────────
    catch_gain: float = 0.0               # N.m per rad/s of flexion velocity above threshold
    catch_vth: float = 1.5                # rad/s
    catch_release_deg: float = 25.0       # catch shuts off after this much flexion stretch
    # ── clonus burst ───────────────────────────────────────────────────────
    clonus_deg: float = 0.0               # target beat amplitude, deg (torque derived: A*J*(2*pi*f)^2)
    clonus_hz: float = 6.0
    clonus_tau: float = 0.8               # s, decay of the burst envelope
    # ── lead-pipe / cogwheel rigidity ─────────────────────────────────────
    rigid_nm: float = 0.0                 # N.m, velocity-independent resistance magnitude
    cog_depth: float = 0.0                # 0..1 modulation depth (cogwheel)
    cog_hz: float = 5.0
    # ── contracture: unilateral stop when flexed past an angle ────────────
    contract_k: float = 0.0               # N.m/rad
    contract_deg: float = -60.0           # model-convention angle where it engages
    # ── non-relaxation (voluntary guarding) ───────────────────────────────
    guard_nm: float = 0.0                 # N.m RMS-ish amplitude
    guard_seed: int = 0
    # ── single spasm pulse ────────────────────────────────────────────────
    spasm_nm: float = 0.0                 # N.m, signed (+ extends, - flexes)
    spasm_t: float = 1.5                  # s after release
    spasm_w: float = 0.15                 # s, half-sine width

    def to_dict(self):
        return asdict(self)


def inertia(p: Params):
    """(l_c, m, J) for these Params: the explicit triple if set, else `s` scaling.

    All three overrides must be set together -- a half-specified limb would
    silently mix one measured segment with two scaled ones.
    """
    exp = (p.lc_m, p.m_kg, p.j_kgm2)
    if all(v is not None for v in exp):
        return float(p.lc_m), float(p.m_kg), float(p.j_kgm2)
    if any(v is not None for v in exp):
        raise ValueError("lc_m, m_kg and j_kgm2 must all be set together, or all left None")
    return ref.inertial_params(p.s)


# ── Winter (2009) Table 4.1, shank+foot about the knee ───────────────────────
#: Segment mass as a fraction of body mass: shank 0.0465 + foot 0.0145.
WINTER_MASS_FRAC = 0.0610
#: Shank length (knee -> ankle) as a fraction of standing height.
WINTER_SHANK_LEN_FRAC = 0.246
#: Shank+foot centre of mass, as a fraction of shank length from the knee.
WINTER_COM_FRAC = 0.606
#: Shank+foot radius of gyration about the KNEE, same normalisation.
WINTER_KPROX_FRAC = 0.735


def limb_from_anthropometry(mass_kg: float, height_m: float):
    """(l_c, m, J) for a real person's shank+foot, from body mass and height.

    Feed straight into Params:  Params(**dict(zip(("lc_m","m_kg","j_kgm2"),
    limb_from_anthropometry(72.0, 1.75))))  -- or use `rig_params`.

    Why this exists rather than fitting `s`: `s` ties mass to s**2 and inertia
    to s**4, so ONE scalar sets both the swing frequency and the torque scale.
    Those are independent in a real person. Frequency depends only on height,
    because mass cancels:

        omega_0**2 = m*g*l_c / J = g * (COM/KPROX**2) / l_shank

    so a heavy and a light person of the same height swing at the same rate but
    need different torques to arrest. Fitting `s` to a measured frequency --
    which `inertial_params`' docstring describes -- therefore gets the period
    right and the torque wrong, by (m_real / m_scaled). Setting all three
    directly is the only way to have both.
    """
    if mass_kg <= 0 or height_m <= 0:
        raise ValueError(f"mass_kg and height_m must be positive, got {mass_kg}, {height_m}")
    l_shank = WINTER_SHANK_LEN_FRAC * height_m
    m = WINTER_MASS_FRAC * mass_kg
    lc = WINTER_COM_FRAC * l_shank
    J = m * (WINTER_KPROX_FRAC * l_shank) ** 2
    return lc, m, J


def anthropometric_frequency_hz(height_m: float) -> float:
    """Linearised hang frequency Winter's fractions predict, in Hz. Mass-free.

    The one anthropometric prediction that is testable against a recording
    without knowing the participant's mass.
    """
    l_shank = WINTER_SHANK_LEN_FRAC * height_m
    w0 = math.sqrt(ref.G * WINTER_COM_FRAC / (WINTER_KPROX_FRAC ** 2 * l_shank))
    return w0 / (2.0 * math.pi)


#: Dry friction measured at the rig's knee bearing, N.m. PROVISIONAL -- not
#: yet identified from a run. Carried as a named default so every caller that
#: wants "the rig, not De Groote's leg" gets the same number and a change to
#: it is one edit. Identify it from a passive drop: Coulomb friction makes the
#: peak-to-peak decay LINEAR in amplitude where viscous damping makes it
#: exponential, so fitting both terms to one passive trace separates them.
RIG_COULOMB_NM = 0.15


def rig_params(mass_kg: float | None = None, height_m: float | None = None, **kw) -> Params:
    """Params for THIS rig: dry friction on, optionally a named person's limb.

    `Params()` is De Groote's leg and reproduces the paper. This is the
    hardware -- the difference is the bearing. Pass mass and height to drive
    the inertia from anthropometry instead of the reference leg scale.
    """
    if (mass_kg is None) != (height_m is None):
        raise ValueError("pass both mass_kg and height_m, or neither")
    geo = {}
    if mass_kg is not None:
        lc, m, J = limb_from_anthropometry(mass_kg, height_m)
        geo = dict(lc_m=lc, m_kg=m, j_kgm2=J)
    return Params(coulomb_nm=kw.pop("coulomb_nm", RIG_COULOMB_NM), **geo, **kw)


def _guard_components(seed: int):
    """Four fixed sinusoids, 0.6-2.4 Hz, seeded phases: deterministic so LabVIEW can reproduce it."""
    rng = np.random.default_rng(seed)
    freqs = np.array([0.6, 1.1, 1.7, 2.4])
    phases = rng.uniform(0, 2 * np.pi, 4)
    return freqs, phases


@dataclass
class State:
    theta: float
    omega: float
    t_srs: float = 0.0
    t_r: float = 0.0          # extensor reflex torque (reference)
    t_flex: float = 0.0       # hamstring reflex torque
    t_catch: float = 0.0      # clasp-knife torque
    srs_active: bool = True
    clonus_t0: float = -1.0   # time the clonus burst was triggered (-1 = not yet)
    swing_reversed: bool = False


def simulate(p: Params, *, theta0_deg: float = 0.0, omega0: float = 0.0,
             duration: float = 12.0, record_torques: bool = False, integrator: str = "rk4",
             trace_ticks: int = 0):
    """Integrate from release. Returns dict(t, theta_deg[, torques]).

    integrator="rk4"    one RK4 step per 1 ms tick (the Python twin's default)
    integrator="euler4" four explicit-Euler substeps of 0.25 ms per tick -- the form the
                        LabVIEW Formula Node implements (labview/model_step_formula_node.txt)
    """
    lc, m, J = inertia(p)
    mgl = m * ref.G * lc
    inc = math.radians(p.incline_deg)
    theta0 = math.radians(theta0_deg)
    n = int(round(duration / DT))
    st = State(theta=theta0, omega=float(omega0))
    # Ring buffers of omega and T_SRS: the 50 ms-delayed feedback inputs. Length
    # DELAY_STEPS + 2 so the sample one step OLDER than the delayed one still
    # exists for the backward-difference derivative (with +1, that index wraps
    # onto the newest sample and the reflex runs away).
    L = DELAY_STEPS + 2
    buf_w = np.full(L, float(omega0))
    buf_s = np.zeros(L)
    buf_t = np.full(L, theta0)   # delayed ANGLE, for the length drive (k_l)
    head = 0
    g_freqs, g_phases = _guard_components(p.guard_seed)
    k_f = p.k_f * p.gain_scale
    k_df = p.k_df * p.gain_scale
    k_v = p.k_v * p.gain_scale
    k_l = p.k_l * p.gain_scale
    k_l_thr = math.radians(p.k_l_thr_deg)
    contract_th = math.radians(p.contract_deg)
    catch_rel = math.radians(p.catch_release_deg)
    clonus_nm = math.radians(p.clonus_deg) * J * (2 * math.pi * p.clonus_hz) ** 2

    out = np.empty(n + 1); out[0] = theta0_deg
    ticks = [] if trace_ticks else None   # per-tick Formula Node inputs/outputs for test vectors
    tq = {k: np.zeros(n + 1) for k in ("grav", "damp", "fric", "tone", "srs", "reflex", "flexor", "catch", "clonus", "rigid", "contract", "guard", "spasm")} if record_torques else None

    def derivs(th, w, ts, tr, tf, tc, t, w_del, w_del_dot, s_del, s_del_dot, th_del, rec=None):
        grav = -mgl * math.cos(th + inc)
        damp = -p.B * w
        # Dry friction of the rig's bearing: magnitude-constant, sign of the
        # velocity, regularised over COULOMB_BAND. This is the amplitude-
        # dependent damping a purely viscous model lacks (Lin & Rymer), and
        # without it a measured N and a linear fit cannot both be right.
        fric = -p.coulomb_nm * math.tanh(w / COULOMB_BAND) if p.coulomb_nm else 0.0
        wall = ref.coordinate_limit_torque(th) if p.use_limit else 0.0
        # SRS (reference Eq 6/7), first drop only
        k = p.k_srs * p.tone
        dth = th - theta0
        s_vneg = blend(-10.0 * w)
        if st.srs_active and s_vneg > 1e-3:
            s_lt = blend(50.0 * (dth + ref.DTHETA_C)); s_gt = blend(50.0 * (-dth - ref.DTHETA_C))
            drive = (s_lt * (-ts - k * dth) + s_gt * (-ts + k * ref.DTHETA_C)) / ref.TAU_A
            d_ts = s_vneg * drive + blend(10.0 * w) * (-ts) / ref.TAU_SRS
        else:
            d_ts = -ts / ref.TAU_SRS
        # delayed extensor excitation. Force feedback is the reference
        # (Eq 8/9); the velocity and length drives are additive and zero by
        # default, so this line is bit-identical to the reference at
        # Params() and only diverges when a caller sets k_v or k_l.
        e_r = k_f * (s_del - p.B * w_del) + k_df * (s_del_dot - p.B * w_del_dot)
        if k_v:
            e_r += k_v * max(0.0, -w_del - p.k_v_thr)       # stretch VELOCITY
        if k_l:
            e_r += k_l * max(0.0, (theta0 - th_del) - k_l_thr)   # stretch LENGTH
        e_r = max(0.0, e_r)
        if p.reflex_max_nm:
            e_r = min(e_r, p.reflex_max_nm)
        d_tr = (e_r - tr) / ref.TAU_A
        # NEW: hamstring reflex, opposes extension velocity (delayed velocity drive)
        e_f = p.flex_gain * max(0.0, w_del - p.flex_vth)
        d_tf = (e_f - tf) / ref.TAU_A
        # NEW: clasp-knife catch -- quadriceps velocity catch that lets go past a stretch length
        stretch = theta0 - th
        inhibit = blend(20.0 * (catch_rel - stretch))
        e_c = p.catch_gain * max(0.0, -w_del - p.catch_vth) * inhibit
        d_tc = (e_c - tc) / ref.TAU_A
        # NEW: clonus burst, triggered at the end of the first drop
        clon = 0.0
        if p.clonus_deg and st.clonus_t0 >= 0.0:
            tt = t - st.clonus_t0
            clon = clonus_nm * math.exp(-tt / p.clonus_tau) * math.sin(2 * math.pi * p.clonus_hz * tt)
        # NEW: lead-pipe / cogwheel rigidity (velocity-independent magnitude)
        rigid = -p.rigid_nm * math.tanh(w / 0.05) * (1.0 + p.cog_depth * math.sin(2 * math.pi * p.cog_hz * t))
        # NEW: contracture stop -- resists flexion past contract_deg
        contract = p.contract_k * max(0.0, contract_th - th)
        # NEW: guarding -- deterministic band-limited voluntary torque, on after 0.3 s
        guard = 0.0
        if p.guard_nm and t > 0.3:
            guard = p.guard_nm * 0.5 * float(np.sum(np.sin(2 * math.pi * g_freqs * t + g_phases)))
        # NEW: single spasm pulse
        spasm = 0.0
        if p.spasm_nm and p.spasm_t <= t <= p.spasm_t + p.spasm_w:
            spasm = p.spasm_nm * math.sin(math.pi * (t - p.spasm_t) / p.spasm_w)
        tau = grav + damp + fric + wall + p.tone + ts + tr - tf + tc + clon + rigid + contract + guard + spasm
        if rec is not None:
            for kk, vv in (("grav", grav), ("damp", damp), ("fric", fric), ("tone", p.tone), ("srs", ts), ("reflex", tr), ("flexor", -tf),
                           ("catch", tc), ("clonus", clon), ("rigid", rigid), ("contract", contract), ("guard", guard), ("spasm", spasm)):
                rec[kk] = vv
        return w, tau / J, d_ts, d_tr, d_tf, d_tc

    for i in range(n):
        t = i * DT
        if st.srs_active and st.omega > 0.0 and t > 0.05:
            st.srs_active = False
        if not st.swing_reversed and st.omega > 0.0 and t > 0.05:
            st.swing_reversed = True
            if p.clonus_deg:
                st.clonus_t0 = t
        # delayed inputs (zero-order hold over the step)
        idx = (head - DELAY_STEPS) % L
        idx_prev = (head - DELAY_STEPS - 1) % L
        w_del, s_del, th_del = buf_w[idx], buf_s[idx], buf_t[idx]
        w_del_dot = (buf_w[idx] - buf_w[idx_prev]) / DT if i > DELAY_STEPS else 0.0
        s_del_dot = (buf_s[idx] - buf_s[idx_prev]) / DT if i > DELAY_STEPS else 0.0
        y = (st.theta, st.omega, st.t_srs, st.t_r, st.t_flex, st.t_catch)
        if ticks is not None and i < trace_ticks:
            ticks.append([t, *y, int(st.srs_active), int(st.swing_reversed), st.clonus_t0, w_del, buf_w[idx_prev], s_del, buf_s[idx_prev], th_del])
        dl = (w_del, w_del_dot, s_del, s_del_dot, th_del)
        if integrator == "euler4":
            new = list(y); h = DT / 4.0
            for q in range(4):
                kq = derivs(*new, t + q * h, *dl)
                new = [a + h * b for a, b in zip(new, kq)]
        else:
            k1 = derivs(*y, t, *dl)
            k2 = derivs(*[a + 0.5 * DT * b for a, b in zip(y, k1)], t + 0.5 * DT, *dl)
            k3 = derivs(*[a + 0.5 * DT * b for a, b in zip(y, k2)], t + 0.5 * DT, *dl)
            k4 = derivs(*[a + DT * b for a, b in zip(y, k3)], t + DT, *dl)
            new = [a + DT / 6.0 * (b1 + 2 * b2 + 2 * b3 + b4) for a, b1, b2, b3, b4 in zip(y, k1, k2, k3, k4)]
        st.theta, st.omega, st.t_srs, st.t_r, st.t_flex, st.t_catch = new
        if ticks is not None and i < trace_ticks:
            ticks[-1] += list(new)
        head = (head + 1) % L
        buf_w[head] = st.omega; buf_s[head] = st.t_srs; buf_t[head] = st.theta
        out[i + 1] = math.degrees(st.theta)
        if tq is not None:
            rec = {}
            derivs(*new, t + DT, *dl, rec=rec)
            for kk, vv in rec.items(): tq[kk][i + 1] = vv

    res = {"t": np.arange(n + 1) * DT, "theta_deg": out}
    if ticks is not None:
        res["ticks"] = np.array(ticks)
        res["geometry"] = dict(J=J, mgl=mgl, inc=inc, th0=theta0, clon_nm=clonus_nm, guard_phases=list(g_phases))
    if tq is not None:
        res["torques"] = tq
    return res


def settled_deg(p: Params, *, theta0_deg: float = 0.0, duration: float = 25.0) -> float:
    """Where the leg actually ends up, by simulation: median of the last 2 s."""
    r = simulate(p, theta0_deg=theta0_deg, duration=duration)
    return float(np.median(r["theta_deg"][-2000:]))


def catch_and_hold(arrest_deg: float, *, base: Params | None = None, zeta: float = 1.0,
                   tol_deg: float = 0.05, max_iter: int = 40) -> Params:
    """Params for a leg that FALLS and then STOPS at `arrest_deg`, and holds.

    The second of the two severe archetypes the rig has to render. The first --
    a high first-stop angle with the swing continuing afterwards -- is the
    reflex ladder (`scenarios.severity`); this one is the case where the shank
    drops and simply arrests, with no oscillation about the arrest point.

    Mechanism: a tonic extensor torque that balances gravity AT the target
    angle, plus the damping that makes the approach critical rather than
    oscillatory. Not a stop the leg bounces off (`contract_k`) and not a catch
    that lets go (`catch_gain`) -- a hold.

    `arrest_deg` is in model convention (0 = full extension, negative =
    flexion) and must lie strictly between the release angle and the passive
    rest. Past the passive rest there is nothing to hold against: gravity is
    already carrying the leg there, and arresting it FURTHER would need a
    flexor torque, which this is not. That raises rather than silently
    returning an unreachable set.

    The analytic tone is exact for the equilibrium and then refined against the
    simulated settle, because the soft wall and the SRS decay both shift it
    slightly. Raises if the refinement does not converge, so the returned
    Params always settles within `tol_deg` of the target.
    """
    base = base or Params()
    lc, m, J = inertia(base)
    mgl = m * ref.G * lc
    inc = math.radians(base.incline_deg)
    th = math.radians(arrest_deg)
    rest = math.radians(passive_rest_deg(base))
    if not (rest < th <= 0.0):
        raise ValueError(
            f"arrest_deg={arrest_deg:.1f} is not between the release angle (0.0) and the "
            f"passive rest ({math.degrees(rest):.1f}); holding past the rest needs a flexor "
            f"torque, which catch_and_hold does not model")

    # Equilibrium: -mgl*cos(th+inc) + tone + wall(th) = 0
    wall = ref.coordinate_limit_torque(th) if base.use_limit else 0.0
    tone = mgl * math.cos(th + inc) - wall
    if tone <= 0.0:   # unreachable in practice given the guard above, kept explicit
        raise ValueError(f"arrest_deg={arrest_deg:.1f} needs a non-positive tone ({tone:.3f} N.m)")

    # Restoring stiffness there, so the approach can be made critical.
    k = -mgl * math.sin(th + inc)
    if base.use_limit:
        k += ref.K_LIM * (math.exp(ref.K_LIM * (th - ref.THETA_EXT))
                          + math.exp(ref.K_LIM * (ref.THETA_FLEX - th)))
    B = 2.0 * zeta * math.sqrt(J * max(k, 1e-9))

    # Refine `tone` against the SIMULATED settle: the wall is nonlinear and the
    # SRS leaves the leg a fraction of a degree off the analytic equilibrium.
    lo, hi = 0.5 * tone, 2.0 * tone
    cand = replace(base, tone=tone, B=B)
    for _ in range(max_iter):
        got = settled_deg(cand)
        if abs(got - arrest_deg) <= tol_deg:
            return cand
        # more tone holds the leg HIGHER (less negative)
        if got < arrest_deg:
            lo = cand.tone
        else:
            hi = cand.tone
        cand = replace(cand, tone=0.5 * (lo + hi))
    raise RuntimeError(
        f"catch_and_hold did not converge on {arrest_deg:.1f} deg in {max_iter} iterations "
        f"(last settle {settled_deg(cand):.2f} deg, tone {cand.tone:.3f} N.m)")


def passive_rest_deg(p: Params) -> float:
    """Model-convention angle where a PASSIVE leg with this geometry hangs.

    The rig's zero. Tone and contracture make the leg settle away from it, which
    is the clinically meaningful 'non-vertical rest' -- so it must not be re-zeroed.
    """
    pp = Params(s=p.s, B=p.B, incline_deg=p.incline_deg, use_limit=p.use_limit)
    r = simulate(pp, duration=25.0)
    return float(np.median(r["theta_deg"][-2000:]))
