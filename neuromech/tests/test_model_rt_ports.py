"""The four mechanisms ported into model_rt.py on 2026-10-26.

The governing requirement is in the first test: `Params()` must still be the
validated reference. Everything added here is additive and off by default, so
a regression that changed the published model would show up there first, not
as a subtly different severity curve six scenarios later.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import model_rt as M  # noqa: E402
from neuromech import pendulum_sim as ref  # noqa: E402


# ── the reference must be untouched ──────────────────────────────────────────

def test_defaults_add_no_torque():
    """Every ported parameter is zero at Params(), so no new term can fire."""
    p = M.Params()
    assert p.coulomb_nm == 0.0
    assert p.k_v == 0.0 and p.k_l == 0.0 and p.reflex_max_nm == 0.0
    assert p.lc_m is None and p.m_kg is None and p.j_kgm2 is None


def test_default_geometry_is_the_reference_scaling():
    assert M.inertia(M.Params()) == ref.inertial_params(ref.L_SCALE_NOMINAL)


@pytest.mark.parametrize("integrator", ["rk4", "euler4"])
def test_passive_run_records_zero_friction(integrator):
    r = M.simulate(M.Params(), duration=4.0, record_torques=True, integrator=integrator)
    assert np.all(r["torques"]["fric"] == 0.0)


# ── 1. Coulomb friction ─────────────────────────────────────────────────────

def _half_swing_amplitudes(p, duration=14.0, n=6):
    from scipy.signal import find_peaks
    r = M.simulate(p, duration=duration)
    rel = r["theta_deg"] - M.passive_rest_deg(p)
    pk, _ = find_peaks(np.abs(rel), prominence=0.5)
    return np.abs(rel)[pk][:n]


def test_coulomb_friction_removes_energy():
    """Same leg, friction on: every half-swing after the first is smaller."""
    a = _half_swing_amplitudes(M.Params())
    b = _half_swing_amplitudes(M.Params(coulomb_nm=0.15))
    assert len(a) == len(b)
    assert np.all(b < a), f"friction did not damp: {b} vs {a}"


def test_coulomb_decay_is_more_linear_than_viscous():
    """The signature that identifies it: viscous decay is geometric (constant
    RATIO between successive swings), dry friction is arithmetic (constant
    DIFFERENCE). Friction must move the trace toward the arithmetic end."""
    def ratio_spread(amps):
        r = amps[1:] / amps[:-1]
        return float(np.std(r) / np.mean(r))
    visc = _half_swing_amplitudes(M.Params(B=0.2))
    fric = _half_swing_amplitudes(M.Params(B=0.2, coulomb_nm=0.3))
    # A purely viscous swing has a near-constant ratio; adding friction makes
    # the ratio fall swing over swing, so its spread grows.
    assert ratio_spread(fric) > 3.0 * ratio_spread(visc)


def test_rig_params_turns_friction_on():
    assert M.rig_params().coulomb_nm == M.RIG_COULOMB_NM
    assert M.rig_params(coulomb_nm=0.4).coulomb_nm == 0.4


# ── 2. Anthropometry ────────────────────────────────────────────────────────

def test_anthropometric_frequency_is_mass_free():
    """Mass cancels in m*g*lc/J, which is the whole reason the explicit triple
    exists: one `s` cannot set both the period and the torque scale."""
    lc1, m1, j1 = M.limb_from_anthropometry(55.0, 1.75)
    lc2, m2, j2 = M.limb_from_anthropometry(95.0, 1.75)
    f1 = math.sqrt(m1 * ref.G * lc1 / j1)
    f2 = math.sqrt(m2 * ref.G * lc2 / j2)
    assert f1 == pytest.approx(f2, rel=1e-12)
    assert m2 > m1 and j2 > j1          # but the torque scale does differ


def test_anthropometric_frequency_matches_the_closed_form():
    want = M.anthropometric_frequency_hz(1.75)
    lc, m, J = M.limb_from_anthropometry(72.0, 1.75)
    got = math.sqrt(m * ref.G * lc / J) / (2 * math.pi)
    assert got == pytest.approx(want, rel=1e-12)


def test_taller_leg_swings_slower():
    assert M.anthropometric_frequency_hz(1.90) < M.anthropometric_frequency_hz(1.60)


def test_explicit_inertia_must_be_complete():
    with pytest.raises(ValueError, match="all be set together"):
        M.inertia(M.Params(m_kg=4.0))


def test_anthropometry_rejects_nonsense():
    for bad in ((0.0, 1.75), (72.0, 0.0), (-5.0, 1.75)):
        with pytest.raises(ValueError):
            M.limb_from_anthropometry(*bad)


def test_rig_params_needs_both_or_neither():
    with pytest.raises(ValueError, match="both"):
        M.rig_params(mass_kg=72.0)


def test_explicit_limb_drives_the_simulation():
    """A heavier shank at the same height keeps the period and raises the
    torque needed to hold it -- the property `s` cannot express."""
    light = M.Params(**dict(zip(("lc_m", "m_kg", "j_kgm2"), M.limb_from_anthropometry(55.0, 1.75))))
    heavy = M.Params(**dict(zip(("lc_m", "m_kg", "j_kgm2"), M.limb_from_anthropometry(95.0, 1.75))))
    assert M.passive_rest_deg(light) == pytest.approx(M.passive_rest_deg(heavy), abs=0.05)
    t_light = M.catch_and_hold(-30.0, base=light).tone
    t_heavy = M.catch_and_hold(-30.0, base=heavy).tone
    assert t_heavy > 1.5 * t_light


# ── 3. catch_and_hold ───────────────────────────────────────────────────────

@pytest.mark.parametrize("target", [-10.0, -25.0, -40.0, -50.0])
def test_catch_and_hold_settles_where_asked(target):
    p = M.catch_and_hold(target)
    assert M.settled_deg(p) == pytest.approx(target, abs=0.1)


def test_catch_and_hold_holds_rather_than_oscillates():
    """Critically damped by construction: no overshoot past the arrest angle."""
    p = M.catch_and_hold(-30.0)
    th = M.simulate(p, duration=20.0)["theta_deg"]
    assert th.min() > -32.0, f"overshot to {th.min():.2f} deg"


def test_catch_and_hold_refuses_past_the_passive_rest():
    rest = M.passive_rest_deg(M.Params())
    with pytest.raises(ValueError, match="passive rest"):
        M.catch_and_hold(rest - 5.0)


def test_catch_and_hold_refuses_above_the_release():
    with pytest.raises(ValueError, match="passive rest"):
        M.catch_and_hold(5.0)


def test_catch_and_hold_tone_rises_toward_extension():
    """Holding the leg higher costs more tone, monotonically."""
    tones = [M.catch_and_hold(a).tone for a in (-50.0, -40.0, -25.0, -10.0)]
    assert tones == sorted(tones), tones


# ── 4. Reflex channels and saturation ───────────────────────────────────────

def _peak_reflex(p):
    return float(M.simulate(p, duration=12.0, record_torques=True)["torques"]["reflex"].max())


def test_velocity_channel_produces_reflex_torque():
    assert _peak_reflex(M.Params(tone=2.0, k_v=3.0, k_v_thr=0.5)) > 1.0


def test_length_channel_produces_reflex_torque():
    assert _peak_reflex(M.Params(tone=2.0, k_l=3.0, k_l_thr_deg=5.0)) > 1.0


def test_channels_are_distinguishable():
    """Velocity and length drives must not be the same thing wearing two
    names -- if they were, the rig could not tell a measurement which channel
    it is looking at, which is the point of having both."""
    v = M.simulate(M.Params(tone=2.0, k_v=3.0, k_v_thr=0.5), duration=12.0)["theta_deg"]
    l = M.simulate(M.Params(tone=2.0, k_l=3.0, k_l_thr_deg=5.0), duration=12.0)["theta_deg"]
    assert float(np.max(np.abs(v - l))) > 1.0


def test_saturation_caps_the_excitation():
    cap = 2.0
    assert _peak_reflex(M.Params(tone=2.0, k_v=8.0, k_v_thr=0.2, reflex_max_nm=cap)) == pytest.approx(cap, abs=1e-6)


def test_saturation_off_by_default_is_unbounded():
    assert _peak_reflex(M.Params(tone=2.0, k_v=8.0, k_v_thr=0.2)) > 2.0


def test_thresholds_gate_the_channels():
    """Above the threshold nothing fires, so a threshold the swing never
    reaches is indistinguishable from a zero gain."""
    quiet = M.Params(tone=2.0, k_v=3.0, k_v_thr=50.0)
    assert _peak_reflex(quiet) == pytest.approx(_peak_reflex(M.Params(tone=2.0)), abs=1e-9)


def test_gain_scale_reaches_the_new_channels():
    """Habituation must fade the new drives too, or a habituation series would
    silently stop fading once a scenario used them."""
    full = _peak_reflex(M.Params(tone=2.0, k_v=3.0, k_v_thr=0.5))
    half = _peak_reflex(M.Params(tone=2.0, k_v=3.0, k_v_thr=0.5, gain_scale=0.5))
    assert half < full


# ── Importing the profile library must not run a numerical solve ─────────────

def test_importing_profiles_does_not_build_them():
    """Two profiles come from catch_and_hold, which bisects on a simulated
    settle. Built at module scope that was 12 s of silent work on every
    import, paid by callers that never touch PROFILES -- the cohort arm and
    the zone recalibration both score recordings and need no profile at all.

    Run in a subprocess because this process has almost certainly imported
    the module already, and the cache would hide the regression.
    """
    import subprocess
    import textwrap
    src = textwrap.dedent("""
        import sys, time
        sys.path.insert(0, %r)
        t0 = time.perf_counter()
        from neuromech import profiles
        print(time.perf_counter() - t0)
    """) % os.path.join(os.path.dirname(__file__), "..", "..")
    out = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    elapsed = float(out.stdout.strip().splitlines()[-1])
    # Generous: the point is 0.1 s versus 12 s, not a benchmark.
    assert elapsed < 3.0, f"importing neuromech.profiles took {elapsed:.2f} s -- is PROFILES eager again?"


def test_profiles_are_still_reachable_and_cached():
    from neuromech import profiles as PR
    first = PR.PROFILES
    assert len(first) == 11
    assert first is PR.PROFILES, "PROFILES must be built once, not per access"
    from neuromech.profiles import PROFILES as direct
    assert direct is first, "`from ... import PROFILES` must resolve to the same dict"


def test_a_missing_profiles_attribute_still_raises():
    """__getattr__ must not swallow typos into None."""
    from neuromech import profiles as PR
    with pytest.raises(AttributeError):
        PR.PROFLIES          # noqa: B018  (deliberate typo)
