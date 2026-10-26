"""Synthetic pendulum-test traces at known spasticity levels.

Model from De Groote F., Blum K.P., Horslen B.C., Ting L.H. (2018), "Interaction
between muscle tone, short-range stiffness and increased sensory feedback gains
explains key kinematic features of the pendulum test in spastic cerebral palsy",
PLOS ONE 13(10): e0205763.

Reference MATLAB implementation (MIT, (c) 2018 De Groote, Blum, Horslen, Ting)
at simtk.org/projects/pendulumtest. This began as an independent Python
re-derivation from the published equations; it has since been CHECKED against
that reference (pendulumStateDerivative.m, _SRS_Ffb.m, main_pendulum_test.m)
on 2026-08-26. Every reconstruction below was confirmed:

  - Eq 1 gravity sign: reference has `-m*g*l*cos(x(1))`, matching the sign
    derived here. The printed equation accelerates a shank released from
    horizontal into hyperextension and is a transcription artifact.
  - Eq 4 coordinate limit: the reference re-centres on the range midpoint,
    `Tlimit = -exp(k*(q-theta_ref)) + exp(k*(-q-theta_ref))` with
    q = theta - midpoint and theta_ref = half-range. Substituting collapses
    that to -exp(k*(theta-hi)) + exp(k*(lo-theta)), exactly the form used
    here. Both give +0.342 N.m at the resting angle.
  - Eq 9 activation: reference has `(u - x(4))/0.01`, i.e. (e_R - T_R)/tau_a.
  - SRS reference angle: reference uses `x(1) - params.theta0`, the release
    angle, as assumed here.
  - Inertial scaling m ~ s^2 and J ~ s^4 is deliberate in the reference
    (`m_scale = l_scale^2`, `I * m_scale * l_scale^2`), not a slip for the
    geometric s^3/s^5.

Nominal parameters below are the reference's own (main_pendulum_test.m).

Purpose: feed `compute_pt_params` a trace whose true spasticity is known, which
no recorded trial can do.

CONVENTION: theta = 0 is full extension, negative is flexion, so a seated shank
hangs near -90 deg. This is NOT Pendulastic's 180-based convention; convert with
`to_pendulastic_deg` and validate that adapter before trusting any metric.

Active torques (T_b, T_SRS, T_R) are implemented, following the reference's
erf-blended formulation. `tone_nm` is baseline tone; `k_x`/`k_dx` are the
force-feedback gains. All zero gives the purely passive model.
"""
from __future__ import annotations

import math

import numpy as np

# ── Generic model parameters (OpenSim gait2392, ankle fixed anatomically) ────
LC0 = 0.256      # m    knee -> shank centre of mass
M0 = 4.96        # kg   shank mass
J0 = 0.446       # kg m^2  inertia about the knee
G = 9.81         # m/s^2

# Coordinate-limit soft wall (Eq 4).
K_LIM = 3.0
THETA_FLEX = np.deg2rad(-110.0)
THETA_EXT = np.deg2rad(0.0)

# Reflex / SRS time constants (Eq 7-9).
TAU_SRS = 0.050  # s
TAU_A = 0.010    # s   activation dynamics, and the SRS build-up constant
GAMMA = 0.050    # s   reflex delay
DTHETA_C = np.deg2rad(1.5)

# ── Reference nominal parameters (main_pendulum_test.m) ─────────────────────
# These are the paper's own values, not fitted here. The paper text gives
# neither the damping nor the leg scale; both come from the reference code.
D_NOMINAL = 0.2          # damping, `inputdata.d`
L_SCALE_NOMINAL = 0.87   # `l_scale`. Independently, matching this repo's
                         # control swing frequency (HEALTHY_REF['f']=0.9137 Hz)
                         # via omega_n ~ s**-0.5 gives s ~ 0.85 -- a 2% agreement
                         # from unrelated evidence.
TB_NOMINAL = 2.0         # baseline tone torque, N.m
KF_NOMINAL = 0.75        # force-feedback gain
KDF_NOMINAL = 0.025      # derivative force-feedback gain
# kSRS = 1/delta_theta_crit, so kSRS*Tb*delta_theta_crit == Tb: at critical
# stretch the SRS torque equals the baseline, i.e. total is twice isometric.
KSRS_NOMINAL = 1.0 / DTHETA_C


def inertial_params(s: float = 1.0):
    """Scale by relative leg length (Eq 2). Returns (l_c, m, J).

    Natural frequency about the vertical hang goes as s**-0.5, so s is
    identifiable from a measured swing frequency:
        s = (f_model_at_s1 / f_measured) ** 2
    At s=1 that frequency is 0.841 Hz, against a control median of 0.914 Hz in
    this repo's HEALTHY_REF, implying s ~ 0.85 for our adults.
    """
    return s * LC0, s ** 2 * M0, s ** 4 * J0


def natural_frequency_hz(s: float = 1.0) -> float:
    """Linearised frequency of small oscillations about the vertical hang."""
    lc, m, J = inertial_params(s)
    return float(np.sqrt(m * G * lc / J) / (2.0 * np.pi))


def coordinate_limit_torque(theta: np.ndarray | float) -> np.ndarray | float:
    """Eq 4: near-zero inside [THETA_FLEX, THETA_EXT], steep outside.

    CONFIRMED against the reference (pendulumStateDerivative.m); kept for
    the record because the printed equation disagrees. The paper reads
        -exp(k*(th - th_flex)) + exp(k*(-th - th_ext))
    which puts +108 N.m at the resting angle against a gravity scale of only
    12.5 N.m -- nine times gravity holding the leg up where it should hang
    freely, and -316 N.m at release. Unphysical, and it contradicts both the
    surrounding text and Fig 1C. Read here as a soft wall penalising travel
    past each limit, which gives ~0.3 N.m at rest -- and which the reference
    implementation confirms.
    """
    return -np.exp(K_LIM * (theta - THETA_EXT)) + np.exp(K_LIM * (THETA_FLEX - theta))


class DelayLine:
    """Fixed-step history with cubic-Hermite reads.

    RK4 needs the delayed value at stage times t, t+h/2 and t+h. With the delay
    an exact multiple of h, t-gamma lands on a stored sample, but t+h/2-gamma
    falls BETWEEN samples. Linear interpolation there is O(h^2) and would
    quietly cap the whole integrator at second order, so each sample carries its
    derivative and reads use cubic Hermite, which is O(h^4).

    Before t=0 the history is the constant pre-release state.
    """

    def __init__(self, delay: float, dt: float, initial: float, initial_dot: float = 0.0):
        if delay < 0 or dt <= 0:
            raise ValueError("delay must be >= 0 and dt > 0")
        self.dt = float(dt)
        self.delay = float(delay)
        steps = delay / dt
        if abs(steps - round(steps)) > 1e-9:
            raise ValueError(f"delay {delay} is not an integer multiple of dt {dt}")
        self.depth = int(round(steps))
        # _v[k] is the sample for ABSOLUTE step (_base + k). Tracking the base
        # explicitly keeps the index arithmetic readable -- relative offsets
        # into a trimmed list are exactly where off-by-one errors hide.
        self._base = 0
        self._v = [float(initial)]
        self._d = [float(initial_dot)]
        self._pre = float(initial)

    def push(self, value: float, derivative: float) -> None:
        """Record the sample for the next absolute step."""
        self._v.append(float(value))
        self._d.append(float(derivative))
        keep = self.depth + 4          # a little slack for the t+h/2 stage read
        if len(self._v) > keep:
            drop = len(self._v) - keep
            del self._v[:drop]
            del self._d[:drop]
            self._base += drop

    def read(self, step: int, offset: float = 0.0) -> float:
        """Value at absolute time (step + offset - depth) steps.

        `offset` is in steps (RK4 uses 0.0 and 0.5). Reads before t=0 return
        the constant pre-release state.
        """
        target = step + offset - self.depth
        if target <= 0.0:
            return self._pre
        i = int(np.floor(target))
        frac = target - i
        idx = i - self._base
        if idx < 0:
            # The sample existed and the ring already dropped it. That is a
            # caller bug, not a pre-release read, and returning _pre here would
            # hand back a plausible-looking wrong number instead of failing --
            # a corrupt trajectory rather than a crash, in exactly the signal
            # this fixture exists to generate. Only target <= 0 is pre-release.
            raise IndexError(
                f"delay-line read {target:.3f} is older than stored history "
                f"(oldest is {self._base}); push and read must interleave")
        if idx >= len(self._v):
            raise IndexError(
                f"delay-line read {target:.3f} is ahead of stored history "
                f"(have up to {self._base + len(self._v) - 1})")
        if frac == 0.0:
            return self._v[idx]
        if idx + 1 >= len(self._v):
            raise IndexError(
                "delay-line Hermite read needs the next sample; history is short")
        y0, y1 = self._v[idx], self._v[idx + 1]
        m0, m1 = self._d[idx] * self.dt, self._d[idx + 1] * self.dt
        t2, t3 = frac * frac, frac * frac * frac
        return ((2 * t3 - 3 * t2 + 1) * y0 + (t3 - 2 * t2 + frac) * m0
                + (-2 * t3 + 3 * t2) * y1 + (t3 - t2) * m1)

    def read_derivative(self, step: int, offset: float = 0.0) -> float:
        """d/dt of the interpolated value at the same delayed time.

        The force-feedback reflex needs both x(t-gamma) and xdot(t-gamma) (the
        reference's Z and dZ). Differentiating the same Hermite polynomial the
        value read uses keeps the two exactly consistent -- taking the stored
        derivative alone would be a different (piecewise-constant) function and
        would reintroduce the interpolation-order problem this class exists to
        avoid.
        """
        target = step + offset - self.depth
        if target <= 0.0:
            return 0.0                      # constant pre-release history
        i = int(np.floor(target))
        frac = target - i
        idx = i - self._base
        if idx < 0:
            raise IndexError(
                f"delay-line derivative read {target:.3f} is older than stored "
                f"history (oldest is {self._base})")
        if idx >= len(self._v):
            raise IndexError("delay-line derivative read is ahead of history")
        if idx + 1 >= len(self._v):
            return self._d[idx]
        y0, y1 = self._v[idx], self._v[idx + 1]
        m0, m1 = self._d[idx] * self.dt, self._d[idx + 1] * self.dt
        f2 = frac * frac
        dp = ((6 * f2 - 6 * frac) * y0 + (3 * f2 - 4 * frac + 1) * m0
              + (-6 * f2 + 6 * frac) * y1 + (3 * f2 - 2 * frac) * m1)
        return dp / self.dt


def thigh_incline_from_resting_deg(neutral_deg: float) -> float:
    """Infer thigh inclination from an observed resting knee angle.

    The published model uses gravity ~ cos(theta) with theta the KNEE angle,
    which puts zero gravity moment at theta = -90 -- i.e. it silently assumes a
    HORIZONTAL THIGH, so the leg must rest at 90 deg in our convention. Real
    trials here rest near 129 deg (only ~51 deg of knee flexion), so the thigh
    is inclined and the model needs the phase shift or every simulated trace
    sits ~39 deg off.

    At rest the shank hangs vertical, so theta_rest = -(90 + incline) and
    Pendulastic's angle is 180 + theta_rest.
    """
    return -(90.0 + (neutral_deg - 180.0))


def _smoothstep(x: float) -> float:
    """0.5*(erf(x)+1): the reference's smooth 0/1 indicator.

    The paper writes Eq 6 as a hard piecewise switch on sign(theta_dot) and on
    |delta_theta| vs the critical stretch. The reference implementation does NOT
    -- it blends with erf. That is not cosmetic: a hard switch makes the right
    hand side non-smooth, and a non-smooth RHS silently drops RK4 from O(h^4)
    to first order near the switch. The O(h^4) convergence this module verifies
    only survives because the switching is smooth.
    """
    return 0.5 * (math.erf(x) + 1.0)


def _derivs_active(theta, omega, t_srs, t_r, *, J, mgl, B, incline, use_limit,
                   tone, k_f, k_df, k_srs, srs_active,
                   omega_delayed, omega_dot_delayed,
                   t_srs_delayed, t_srs_dot_delayed, theta0):
    """Full model: passive + baseline tone + short-range stiffness + reflex.

    State is [theta, omega, T_SRS, T_R], mirroring the reference's
    x = [q qdot TSRS Tref] (pendulumStateDerivative_SRS_Ffb.m).

    Force-based reflex: the delayed feedback signal is T_SRS + T_d, not joint
    angle/velocity. The paper found this variant -- not the velocity-based one
    -- reproduces the reduced oscillation COUNT, which is the metric this
    repo's own data disputes against Whelan, so it is the one implemented here.
    """
    grav = -mgl * np.cos(theta + np.deg2rad(incline))
    t_d = -B * omega
    tau = grav + t_d + tone + t_srs + t_r
    if use_limit:
        tau = tau + coordinate_limit_torque(theta)

    # ── Short-range stiffness (Eq 6/7) ────────────────────────────────────
    # Driven toward its target with TAU_A, not assigned algebraically: this is
    # what the reference does, and it keeps T_SRS a differentiable state.
    k = k_srs * tone
    dtheta = theta - theta0
    s_velneg = _smoothstep(-10.0 * omega)
    if srs_active and s_velneg > 1e-3:
        s_lt = _smoothstep(50.0 * (dtheta + DTHETA_C))
        s_gt = _smoothstep(50.0 * (-dtheta - DTHETA_C))
        drive = (s_lt * (-t_srs - k * dtheta)
                 + s_gt * (-t_srs + k * DTHETA_C)) / TAU_A
        d_t_srs = s_velneg * drive + _smoothstep(10.0 * omega) * (-t_srs) / TAU_SRS
    else:
        d_t_srs = -t_srs / TAU_SRS

    # ── Delayed force feedback (Eq 8/9) ───────────────────────────────────
    td_delayed = -B * omega_delayed
    td_dot_delayed = -B * omega_dot_delayed
    e_r = max(0.0, k_f * (t_srs_delayed + td_delayed)
              + k_df * (t_srs_dot_delayed + td_dot_delayed))
    d_t_r = (e_r - t_r) / TAU_A

    return omega, tau / J, d_t_srs, d_t_r


def _derivs(theta, omega, *, J, mgl, B, linear_gravity, use_limit, incline=0.0):
    """Passive dynamics. Returns (dtheta, domega).

    CONFIRMED against the reference: `-m*g*l*cos(x(1))`. As printed in the paper, J*thdd + T_pas + T_act = +m*g*lc*cos(th)
    accelerates a shank released from horizontal into HYPEREXTENSION
    (+27.9 rad/s^2). Gravity must drive the shank into flexion, so the working
    form is J*thdd = -m*g*lc*cos(th) - T_pas - T_act.
    """
    if linear_gravity:
        # Linearised about the vertical hang: theta = -pi/2 + phi, cos(theta) =
        # sin(phi) ~ phi. Used only by the analytic-comparison test.
        grav = -mgl * (theta + np.pi / 2.0)
    else:
        # cos(theta + incline): the gravity moment vanishes when the SHANK is
        # vertical, which is theta = -(90 + incline), not theta = -90. With
        # incline=0 this is the published equation and a horizontal thigh.
        grav = -mgl * np.cos(theta + np.deg2rad(incline))
    tau = grav - B * omega
    if use_limit:
        tau = tau + coordinate_limit_torque(theta)
    return omega, tau / J


def simulate(tone_nm: float = 0.0, k_x: float = 0.0, k_dx: float = 0.0, *,
             s: float = 1.0, B: float = 0.1, dt: float = 5e-4,
             duration: float = 10.0, theta0_deg: float = 0.0,
             omega0: float = 0.0, linear_gravity: bool = False,
             use_limit: bool = True, thigh_incline_deg: float = 0.0,
             k_srs: float | None = None):
    """Integrate the pendulum test. Returns (t, theta_deg).

    tone_nm is the baseline muscle tone (N.m); k_x/k_dx are the force-feedback
    gains. All three zero gives the purely passive model. Reference nominals for
    a spastic simulation are TB_NOMINAL / KF_NOMINAL / KDF_NOMINAL.

    `linear_gravity` and `use_limit` exist for the validation tests and should
    be left alone otherwise.
    """
    if tone_nm or k_x or k_dx:
        if linear_gravity:
            raise ValueError("linear_gravity is a validation-only mode and "
                             "cannot be combined with active torques")
        return _simulate_active(
            tone_nm, k_x, k_dx, s=s, B=B, dt=dt, duration=duration,
            theta0_deg=theta0_deg, omega0=omega0, use_limit=use_limit,
            thigh_incline_deg=thigh_incline_deg, k_srs=k_srs)

    lc, m, J = inertial_params(s)
    mgl = m * G * lc
    n = int(round(duration / dt))
    theta = np.deg2rad(theta0_deg)
    omega = float(omega0)

    out_theta = np.empty(n + 1)
    out_theta[0] = theta
    kw = dict(J=J, mgl=mgl, B=B, linear_gravity=linear_gravity,
              use_limit=use_limit, incline=thigh_incline_deg)

    for i in range(n):
        k1t, k1o = _derivs(theta, omega, **kw)
        k2t, k2o = _derivs(theta + 0.5 * dt * k1t, omega + 0.5 * dt * k1o, **kw)
        k3t, k3o = _derivs(theta + 0.5 * dt * k2t, omega + 0.5 * dt * k2o, **kw)
        k4t, k4o = _derivs(theta + dt * k3t, omega + dt * k3o, **kw)
        theta += (dt / 6.0) * (k1t + 2 * k2t + 2 * k3t + k4t)
        omega += (dt / 6.0) * (k1o + 2 * k2o + 2 * k3o + k4o)
        out_theta[i + 1] = theta

    t = np.arange(n + 1) * dt
    return t, np.rad2deg(out_theta)


def _simulate_active(tone_nm, k_f, k_df, *, s, B, dt, duration, theta0_deg,
                     omega0, use_limit, thigh_incline_deg, k_srs=None):
    """RK4 over [theta, omega, T_SRS, T_R] with the 50 ms delayed reflex."""
    lc, m, J = inertial_params(s)
    mgl = m * G * lc
    if k_srs is None:
        k_srs = KSRS_NOMINAL
    n = int(round(duration / dt))
    theta = float(np.deg2rad(theta0_deg))
    omega = float(omega0)
    t_srs = 0.0
    t_r = 0.0
    theta0 = theta

    # Two histories: omega (for T_d and its rate) and T_SRS. Both are read at
    # RK4 stage times, so both need the Hermite interpolation.
    hist_w = DelayLine(GAMMA, dt, omega, 0.0)
    hist_s = DelayLine(GAMMA, dt, 0.0, 0.0)

    out = np.empty(n + 1)
    out[0] = theta
    # SRS contributes only to the first swing excursion. The reference flips a
    # global inside the derivative; here it is decided ONCE per step instead,
    # because RK4 calls the derivative four times per step and a flag flipped
    # mid-step would make the four stages disagree about which model they are
    # integrating -- a silent order-of-accuracy loss.
    srs_active = True

    for i in range(n):
        if srs_active and omega > 0.0 and i * dt > 0.05:
            srs_active = False          # velocity reversed: first swing is over

        base = dict(J=J, mgl=mgl, B=B, incline=thigh_incline_deg,
                    use_limit=use_limit, tone=tone_nm, k_f=k_f, k_df=k_df,
                    k_srs=k_srs, srs_active=srs_active, theta0=theta0)

        def stage(dth, dom, ds, dr, off):
            return _derivs_active(
                theta + dth, omega + dom, t_srs + ds, t_r + dr,
                omega_delayed=hist_w.read(i, off),
                omega_dot_delayed=hist_w.read_derivative(i, off),
                t_srs_delayed=hist_s.read(i, off),
                t_srs_dot_delayed=hist_s.read_derivative(i, off), **base)

        k1 = stage(0.0, 0.0, 0.0, 0.0, 0.0)
        k2 = stage(*[0.5 * dt * v for v in k1], 0.5)
        k3 = stage(*[0.5 * dt * v for v in k2], 0.5)
        k4 = stage(*[dt * v for v in k3], 1.0)

        d_omega = (dt / 6.0) * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
        theta += (dt / 6.0) * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
        omega += d_omega
        t_srs += (dt / 6.0) * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2])
        t_r += (dt / 6.0) * (k1[3] + 2 * k2[3] + 2 * k3[3] + k4[3])

        # Store the derivative AT the accepted new state, not a stage value.
        # k4 is evaluated at (t+h, y+h*k3), which is only O(h) from the state
        # actually accepted, and d_omega/dt is the step-average slope. Either
        # makes the stored slope O(h)-accurate, which degrades the Hermite
        # reads and drags the whole integrator off fourth order -- measured
        # ratio 2.8 instead of ~16 before this was corrected. One extra
        # derivative evaluation per step buys the order back.
        k_end = _derivs_active(
            theta, omega, t_srs, t_r,
            omega_delayed=hist_w.read(i + 1, 0.0),
            omega_dot_delayed=hist_w.read_derivative(i + 1, 0.0),
            t_srs_delayed=hist_s.read(i + 1, 0.0),
            t_srs_dot_delayed=hist_s.read_derivative(i + 1, 0.0), **base)
        hist_w.push(omega, k_end[1])
        hist_s.push(t_srs, k_end[2])
        out[i + 1] = theta

    return np.arange(n + 1) * dt, np.rad2deg(out)


def analytic_linear(t, *, theta0_deg, omega0, s=1.0, B=0.1):
    """Damped linear oscillator about the vertical hang, in degrees.

    Only valid for SMALL deviations from -90 deg. The full pendulum-test
    release from horizontal swings ~90 deg and is strongly nonlinear, so it is
    NOT described by this solution.
    """
    lc, m, J = inertial_params(s)
    wn2 = m * G * lc / J
    beta = B / (2.0 * J)
    wd = np.sqrt(wn2 - beta ** 2)
    phi0 = np.deg2rad(theta0_deg) + np.pi / 2.0
    D = phi0
    C = (omega0 + beta * phi0) / wd
    phi = np.exp(-beta * t) * (C * np.sin(wd * t) + D * np.cos(wd * t))
    return np.rad2deg(phi - np.pi / 2.0)


def to_pendulastic_deg(theta_deg):
    """Model convention -> Pendulastic's 180-based angle.

    [VERIFY - ours, not the paper's] Trials hold at exactly 180.0 before release and the model releases
    from 0, so 180 + theta is the natural mapping. Confirm by simulating a
    healthy leg and checking `compute_pt_params` reports a resting neutral_deg
    near the ~124 deg median of this repo's control legs. If it does not,
    suspect this adapter before the model.
    """
    return 180.0 + np.asarray(theta_deg, dtype=float)
