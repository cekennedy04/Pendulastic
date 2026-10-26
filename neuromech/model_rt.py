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

Torque modules (all default off; parameters in Params):
  base      gravity, passive damping B, soft range walls           (reference)
  tone      baseline extensor tone T_b                              (reference)
  srs       short-range stiffness on the first drop                 (reference)
  reflex    delayed force-feedback extensor reflex k_f, k_df        (reference)
  flexor    velocity-threshold hamstring reflex (flexor dominance)  NEW
  catch     clasp-knife: velocity catch with length-dependent release  NEW
  clonus    triggered decaying 5-8 Hz burst, sized in degrees       NEW
  rigidity  lead-pipe (velocity-independent) resistance, + cogwheel  NEW
  contract  unilateral elastic stop: contracture / limited flexion  NEW
  guard     non-relaxation: band-limited voluntary torque           NEW
  spasm     a single mid-swing torque pulse                         NEW
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field, asdict, replace

import numpy as np

from neuromech import pendulum_sim as ref

DT = 1e-3                    # s, RT loop period
DELAY_STEPS = int(round(ref.GAMMA / DT))   # 50 samples = 50 ms reflex delay


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
    # ── reference spasticity (De Groote 2018) ─────────────────────────────
    tone: float = 0.0                     # baseline extensor tone T_b, N.m
    k_f: float = 0.0                      # force-feedback gain (dimensionless)
    k_df: float = 0.0                     # derivative force-feedback gain, s
    k_srs: float = ref.KSRS_NOMINAL       # SRS stiffness per unit tone, 1/rad
    gain_scale: float = 1.0               # multiplies k_f, k_df (habituation series)
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
    lc, m, J = ref.inertial_params(p.s)
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
    head = 0
    g_freqs, g_phases = _guard_components(p.guard_seed)
    k_f = p.k_f * p.gain_scale
    k_df = p.k_df * p.gain_scale
    contract_th = math.radians(p.contract_deg)
    catch_rel = math.radians(p.catch_release_deg)
    clonus_nm = math.radians(p.clonus_deg) * J * (2 * math.pi * p.clonus_hz) ** 2

    out = np.empty(n + 1); out[0] = theta0_deg
    ticks = [] if trace_ticks else None   # per-tick Formula Node inputs/outputs for test vectors
    tq = {k: np.zeros(n + 1) for k in ("grav", "damp", "tone", "srs", "reflex", "flexor", "catch", "clonus", "rigid", "contract", "guard", "spasm")} if record_torques else None

    def derivs(th, w, ts, tr, tf, tc, t, w_del, w_del_dot, s_del, s_del_dot, rec=None):
        grav = -mgl * math.cos(th + inc)
        damp = -p.B * w
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
        # delayed force feedback (reference Eq 8/9)
        e_r = max(0.0, k_f * (s_del - p.B * w_del) + k_df * (s_del_dot - p.B * w_del_dot))
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
        tau = grav + damp + wall + p.tone + ts + tr - tf + tc + clon + rigid + contract + guard + spasm
        if rec is not None:
            for kk, vv in (("grav", grav), ("damp", damp), ("tone", p.tone), ("srs", ts), ("reflex", tr), ("flexor", -tf),
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
        w_del, s_del = buf_w[idx], buf_s[idx]
        w_del_dot = (buf_w[idx] - buf_w[idx_prev]) / DT if i > DELAY_STEPS else 0.0
        s_del_dot = (buf_s[idx] - buf_s[idx_prev]) / DT if i > DELAY_STEPS else 0.0
        y = (st.theta, st.omega, st.t_srs, st.t_r, st.t_flex, st.t_catch)
        if ticks is not None and i < trace_ticks:
            ticks.append([t, *y, int(st.srs_active), int(st.swing_reversed), st.clonus_t0, w_del, buf_w[idx_prev], s_del, buf_s[idx_prev]])
        dl = (w_del, w_del_dot, s_del, s_del_dot)
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
        buf_w[head] = st.omega; buf_s[head] = st.t_srs
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


def passive_rest_deg(p: Params) -> float:
    """Model-convention angle where a PASSIVE leg with this geometry hangs.

    The rig's zero. Tone and contracture make the leg settle away from it, which
    is the clinically meaningful 'non-vertical rest' -- so it must not be re-zeroed.
    Cached by geometry: it depends on nothing else, and it costs a 25 s simulation.
    """
    return _passive_rest_cached(p.s, p.B, p.incline_deg, p.use_limit)


@functools.lru_cache(maxsize=64)
def _passive_rest_cached(s, B, incline_deg, use_limit) -> float:
    r = simulate(Params(s=s, B=B, incline_deg=incline_deg, use_limit=use_limit), duration=25.0)
    return float(np.median(r["theta_deg"][-2000:]))
