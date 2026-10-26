"""Validation for neuromech/pendulum_sim.py (copied from feat/pendulum-sim-validation:tests/).

Stage 1: the numerical engine only. Active torques are not implemented, so
these tests prove the integrator and the delay line are correct BEFORE any
biomechanical parameter is fitted -- a wrong integrator would otherwise be
indistinguishable from a wrong parameter.

Note the two are tested separately on purpose. With the active torques at zero
there is no delayed feedback, so a passive run never touches the delay line;
the analytic-trajectory test says nothing about the buffer.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import pendulum_sim as ps  # noqa: E402


# ── Delay line ───────────────────────────────────────────────────────────────

def test_delay_line_depth_is_exact_multiple():
    dl = ps.DelayLine(delay=0.050, dt=5e-4, initial=0.0)
    assert dl.depth == 100


def test_delay_line_rejects_non_multiple_delay():
    with pytest.raises(ValueError):
        ps.DelayLine(delay=0.050, dt=3e-4, initial=0.0)


def test_delay_line_returns_pre_release_state_before_t0():
    dl = ps.DelayLine(delay=0.050, dt=5e-4, initial=-1.25)
    for step in range(0, 100):
        assert dl.read(step) == -1.25


def test_delay_line_reads_exact_samples_on_step_boundaries():
    """A read at a whole step must return that sample untouched.

    Push and read are interleaved because that is how the integrator uses it --
    the ring keeps only ~depth samples, so reading a long-trimmed step would
    quietly return the pre-release constant instead of failing.
    """
    dt, delay = 5e-4, 0.050
    dl = ps.DelayLine(delay, dt, initial=np.sin(0.0), initial_dot=np.cos(0.0))
    checked = 0
    for k in range(1, 401):
        dl.push(np.sin(k * dt), np.cos(k * dt))
        if k >= dl.depth:
            want = np.sin((k - dl.depth) * dt)
            assert abs(dl.read(k) - want) < 1e-15
            checked += 1
    assert checked > 250


def test_delay_line_hermite_half_step_is_fourth_order():
    """Half-step reads are where linear interpolation would silently cap the
    integrator at O(h^2). Halving dt must cut the error ~16x."""
    errs = {}
    for dt in (2e-3, 1e-3):
        delay = 0.050
        dl = ps.DelayLine(delay, dt, initial=np.sin(0.0), initial_dot=np.cos(0.0))
        n = int(0.4 / dt)
        worst = 0.0
        for k in range(1, n + 1):
            dl.push(np.sin(k * dt), np.cos(k * dt))
            step = k - 1                      # stage read needs sample step+1
            if step >= dl.depth:
                want = np.sin((step + 0.5 - dl.depth) * dt)
                worst = max(worst, abs(dl.read(step, 0.5) - want))
        errs[dt] = worst
    ratio = errs[2e-3] / errs[1e-3]
    assert ratio > 8.0, f"half-step read is not 4th order: error ratio {ratio:.1f}"


# ── Integrator ───────────────────────────────────────────────────────────────

def test_matches_analytic_damped_oscillator():
    """Small oscillations about the vertical hang, where the linearisation is
    valid. The full release from horizontal swings ~90 deg and is nonlinear --
    it is NOT described by the damped-linear solution, so it is not used here.
    """
    kw = dict(theta0_deg=-85.0, omega0=0.0, s=1.0, B=0.1)
    t, theta = ps.simulate(dt=5e-4, duration=5.0, linear_gravity=True,
                           use_limit=False, **kw)
    exact = ps.analytic_linear(t, **kw)
    assert np.max(np.abs(theta - exact)) < 1e-9


def test_integrator_is_fourth_order():
    """A tight tolerance at one step size shows accuracy, not order. Halving dt
    must cut the error ~16x."""
    kw = dict(theta0_deg=-85.0, omega0=0.0, s=1.0, B=0.1)
    errs = {}
    for dt in (8e-3, 4e-3):
        t, theta = ps.simulate(dt=dt, duration=2.0, linear_gravity=True,
                               use_limit=False, **kw)
        errs[dt] = np.max(np.abs(theta - ps.analytic_linear(t, **kw)))
    ratio = errs[8e-3] / errs[4e-3]
    assert ratio > 8.0, f"integrator is not 4th order: error ratio {ratio:.1f}"


def test_undamped_energy_is_conserved():
    """With B=0 and no walls, total energy must not drift."""
    lc, m, J = ps.inertial_params(1.0)
    t, theta_deg = ps.simulate(dt=5e-4, duration=10.0, B=0.0,
                               theta0_deg=-85.0, use_limit=False)
    th = np.deg2rad(theta_deg)
    om = np.gradient(th, t)
    energy = 0.5 * J * om ** 2 + m * ps.G * lc * np.sin(th)
    drift = abs(energy[1:-1].max() - energy[1:-1].min()) / abs(energy[1:-1].mean())
    assert drift < 1e-4, f"energy drift {drift:.2e}"


def test_released_shank_falls_into_flexion():
    """The paper's Eq 1 as printed accelerates a shank released from horizontal
    into HYPEREXTENSION. Gravity must drive it into flexion (negative theta).
    """
    t, theta = ps.simulate(dt=5e-4, duration=0.2, theta0_deg=0.0)
    assert theta[10] < theta[0], "shank must fall into flexion, not extend"


def test_passive_swing_settles_near_vertical():
    t, theta = ps.simulate(dt=5e-4, duration=20.0, B=0.4, theta0_deg=0.0)
    assert -95.0 < theta[-1] < -85.0, f"settled at {theta[-1]:.1f} deg"


def test_natural_frequency_is_near_control_median():
    """Unfitted, the model should land close to the swing frequency actually
    measured in this repo's control legs (HEALTHY_REF['f'] = 0.9137 Hz)."""
    f = ps.natural_frequency_hz(1.0)
    assert 0.80 < f < 0.90
    assert abs(f - 0.9137) / 0.9137 < 0.10


# ── Coordinate limit ─────────────────────────────────────────────────────────

def test_coordinate_limit_is_small_inside_the_range():
    """The printed Eq 4 puts ~108 N.m at the resting angle against a 12.5 N.m
    gravity scale. The reconstruction must stay far below gravity in-range."""
    for deg in (-100.0, -90.0, -55.0, -10.0):
        assert abs(ps.coordinate_limit_torque(np.deg2rad(deg))) < 2.0


def test_coordinate_limit_grows_outside_the_range():
    beyond = ps.coordinate_limit_torque(np.deg2rad(-130.0))
    inside = ps.coordinate_limit_torque(np.deg2rad(-90.0))
    assert beyond > inside * 5


def test_thigh_incline_reproduces_the_target_resting_angle():
    """Round-trip the coordinate adapter.

    The published model uses gravity ~ cos(theta), so it rests at 90 deg in our
    convention -- it assumes a HORIZONTAL thigh. Real control trials here rest
    near 129 deg, so the thigh is inclined and the model needs the phase shift.
    Simulate with the inferred inclination, convert, and the settled angle must
    come back where we asked.

    This is a self-consistency check of the adapter, NOT independent
    validation of the inclination: the angle was inferred from the same
    resting value. Confirming ~39 deg needs the recorded thigh angle from the
    protocol.
    """
    target = 129.37                     # P8 left, pre -- control leg
    incline = ps.thigh_incline_from_resting_deg(target)
    assert -45.0 < incline < -30.0
    t, theta = ps.simulate(dt=1e-3, duration=25.0, B=0.6, theta0_deg=0.0,
                           thigh_incline_deg=incline)
    settled = float(np.median(ps.to_pendulastic_deg(theta)[t > t[-1] - 2.0]))
    assert abs(settled - target) < 1.0, f"settled at {settled:.2f}, wanted {target}"


def test_horizontal_thigh_rests_at_ninety_without_the_wall():
    """incline=0 and no soft wall is the pure published gravity model: the
    shank hangs vertical, which is exactly 90 deg in our convention."""
    t, theta = ps.simulate(dt=1e-3, duration=25.0, B=0.6, theta0_deg=0.0,
                           use_limit=False)
    settled = float(np.median(ps.to_pendulastic_deg(theta)[t > t[-1] - 2.0]))
    assert abs(settled - 90.0) < 0.05, f"settled at {settled:.2f}, wanted 90"


def test_coordinate_wall_biases_the_resting_angle():
    """[VERIFY] The soft wall is NOT negligible where the leg rests: it adds
    ~0.34 N.m at the vertical hang and pushes the equilibrium ~1.5 deg toward
    extension. Resting angle is one of the three spasticity features this whole
    model exists to reproduce, so a systematic 1.5 deg bias is not cosmetic.
    Pinned here so a change in the Eq 4 reading is caught rather than absorbed.
    """
    kw = dict(dt=1e-3, duration=25.0, B=0.6, theta0_deg=0.0)
    t, free = ps.simulate(use_limit=False, **kw)
    _, walled = ps.simulate(use_limit=True, **kw)
    a = float(np.median(ps.to_pendulastic_deg(free)[t > t[-1] - 2.0]))
    b = float(np.median(ps.to_pendulastic_deg(walled)[t > t[-1] - 2.0]))
    assert 1.0 < (b - a) < 2.0, f"wall shifted rest by {b - a:.2f} deg"


def test_linear_gravity_is_refused_with_active_torques():
    """linear_gravity linearises about the vertical hang for the analytic
    comparison; combining it with active torques would silently validate
    against a solution that does not describe the model being run."""
    with pytest.raises(ValueError):
        ps.simulate(tone_nm=2.0, linear_gravity=True)


def _swing(tone, kf, **kw):
    t, th = ps.simulate(tone_nm=tone, k_x=kf,
                        k_dx=ps.KDF_NOMINAL if kf else 0.0,
                        s=ps.L_SCALE_NOMINAL, B=ps.D_NOMINAL,
                        dt=1e-3, duration=10.0, theta0_deg=0.0, **kw)
    first = float(th[0] - th.min())
    rest = float(np.median(th[t > t[-1] - 2.0]))
    return first, rest


def test_tone_reduces_the_first_swing_excursion():
    """The paper's first key feature, and the one prior models could not
    reproduce. Reference nominal tone is 2.0 N.m."""
    passive, _ = _swing(0.0, 0.0)
    mild, _ = _swing(1.0, 0.0)
    strong, _ = _swing(4.0, ps.KF_NOMINAL)
    assert passive > mild > strong, (passive, mild, strong)
    assert strong < 0.75 * passive


def test_tone_makes_the_resting_angle_less_vertical():
    """The paper's third key feature. A passive leg settles near -90 deg
    (vertical); tone holds it away from vertical."""
    _, passive = _swing(0.0, 0.0)
    _, strong = _swing(6.0, 1.5)
    assert passive < -85.0, passive
    assert strong > passive + 20.0, (passive, strong)


def test_active_model_converges_and_is_accurate_enough():
    """The ACTIVE model is deliberately not held to the passive model's O(h^4).

    The SRS switches are erf-blended (as in the reference), but two hard
    non-smoothnesses remain and are faithful to it: the reflex rectifier
    `max(0, ...)` in Eq 8, and the once-per-trial end of the first swing
    excursion. A fixed-step RK4 cannot reach fourth order on a right-hand side
    that is not C1 -- the reference sidesteps this with ode45, which shrinks
    its step across a discontinuity. Measured ratios here are ~2.4x then ~4.8x
    per halving, improving as dt falls, rather than 16x.

    That is a property of the model, not a defect, and it does not matter in
    practice: at dt = 0.5 ms the error is ~0.008 deg, three orders below the
    ~1 deg differences this fixture exists to resolve. What IS worth pinning is
    that the error keeps falling and stays small -- a regression that broke the
    delay line or the history slopes would show up as a stalled ratio.
    """
    kw = dict(tone_nm=2.0, k_x=ps.KF_NOMINAL, k_dx=ps.KDF_NOMINAL,
              s=ps.L_SCALE_NOMINAL, B=ps.D_NOMINAL, duration=2.0,
              theta0_deg=0.0)
    ref_t, ref = ps.simulate(dt=1.25e-4, **kw)
    errs = {}
    for dt in (1e-3, 5e-4):
        t, th = ps.simulate(dt=dt, **kw)
        errs[dt] = float(np.max(np.abs(np.interp(t, ref_t, ref) - th)))
    ratio = errs[1e-3] / max(errs[5e-4], 1e-15)
    assert ratio > 2.0, f"convergence stalled: ratio {ratio:.1f} (errs={errs})"
    assert errs[5e-4] < 0.05, f"error at dt=0.5ms is {errs[5e-4]:.4f} deg"


def test_srs_only_acts_on_the_first_swing():
    """`hist` in the reference: short-range stiffness contributes to the first
    excursion only, because prior movement abolishes it. With tone but no
    reflex, the trace must still oscillate afterwards rather than lock up."""
    t, th = ps.simulate(tone_nm=2.0, k_x=0.0, k_dx=0.0, s=ps.L_SCALE_NOMINAL,
                        B=ps.D_NOMINAL, dt=1e-3, duration=10.0, theta0_deg=0.0)
    late = th[t > 3.0]
    assert late.max() - late.min() > 1.0, "trace froze after the first swing"
