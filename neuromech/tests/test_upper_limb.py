"""Elbow limb on the De Groote core (plan 2026-11-10, U1-U3)."""
import math

import numpy as np
import pytest

from neuromech import model_rt as rt
from neuromech import upper_limb as ul


# ── U1: forearm from anthropometry ────────────────────────────────────────
def test_winter_fractions_pinned():
    # Provisional values (see module note). A correction must be a visible edit here.
    assert (ul.WINTER_FAH_MASS_FRAC, ul.WINTER_FOREARM_LEN_FRAC,
            ul.WINTER_FAH_COM_FRAC, ul.WINTER_FAH_KPROX_FRAC) == (0.022, 0.146, 0.682, 0.827)


def test_forearm_hang_frequency_near_1hz_and_mass_free():
    f72 = ul.hang_frequency_hz(*ul.forearm_from_anthropometry(72.0, 1.75))
    f95 = ul.hang_frequency_hz(*ul.forearm_from_anthropometry(95.0, 1.75))
    assert 0.9 < f72 < 1.1
    assert f72 == pytest.approx(f95, rel=1e-12)


def test_forearm_inertia_about_six_times_below_shank():
    J_arm = ul.forearm_from_anthropometry(72.0, 1.75)[2]
    J_leg = rt.limb_from_anthropometry(72.0, 1.75)[2]
    assert 5.0 < J_leg / J_arm < 7.5


def test_accessory_adds_m_d_squared():
    lc0, m0, J0 = ul.forearm_from_anthropometry(72.0, 1.75)
    lc, m, J = ul.forearm_from_anthropometry(72.0, 1.75, accessory_kg=1.0, accessory_m=0.30)
    assert m == pytest.approx(m0 + 1.0)
    assert J == pytest.approx(J0 + 1.0 * 0.30 ** 2)
    assert lc == pytest.approx((m0 * lc0 + 0.30) / (m0 + 1.0))
    # far enough out, the accessory slows the swing
    assert ul.hang_frequency_hz(lc, m, J) < ul.hang_frequency_hz(lc0, m0, J0)


def test_half_specified_accessory_raises():
    with pytest.raises(ValueError):
        ul.forearm_from_anthropometry(72.0, 1.75, accessory_kg=1.0)


# ── core stays the knee ───────────────────────────────────────────────────
def test_knee_params_unchanged_by_new_fields():
    a = rt.simulate(rt.Params(tone=2.0, k_f=0.75, k_df=0.025), duration=4.0)["theta_deg"]
    b = rt.simulate(rt.Params(tone=2.0, k_f=0.75, k_df=0.025, lim_lo_deg=None, lim_hi_deg=None,
                              k_passive=0.0), duration=4.0)["theta_deg"]
    assert np.array_equal(a, b)


def test_custom_walls_at_knee_limits_match_reference_walls():
    a = rt.simulate(rt.Params(), duration=4.0)["theta_deg"]
    b = rt.simulate(rt.Params(lim_lo_deg=-110.0, lim_hi_deg=0.0), duration=4.0)["theta_deg"]
    assert np.max(np.abs(a - b)) < 1e-9


def test_half_specified_walls_raise():
    with pytest.raises(ValueError):
        rt.simulate(rt.Params(lim_lo_deg=-145.0), duration=0.1)


# ── U2: passive elbow reduces to the linear model ─────────────────────────
@pytest.mark.parametrize("start", ul.STARTS)
def test_passive_small_swing_matches_closed_form(start):
    """Tone, reflex and walls off; 3 deg release about a mid-range rest."""
    K = 2.5
    p = ul.elbow_params(start=start, upper_arm_deg=90.0, k_passive=K, use_limit=False)
    lc, m, J = rt.inertia(p)
    q0 = 93.0 if start == "flexion" else 87.0
    r = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=4.0)
    k_eff = K + m * rt.ref.G * lc               # spring + linearised gravity
    wn = math.sqrt(k_eff / J); zeta = p.B / (2 * math.sqrt(k_eff * J)); wd = wn * math.sqrt(1 - zeta ** 2)
    t = r["t"]; x0 = q0 - 90.0
    exact = 90.0 + x0 * np.exp(-zeta * wn * t) * (np.cos(wd * t) + zeta * wn / wd * np.sin(wd * t))
    assert np.max(np.abs(r["q_deg"] - exact)) < 0.02 * abs(x0)


def test_arm_at_side_ends_near_extension_not_mid_range():
    p, q0 = ul.de_lima_bare()
    r = ul.simulate_elbow(p, start="flexion", release_q_deg=q0, duration=10.0)
    assert np.median(r["q_deg"][-1000:]) < 15.0   # the knee-like mid-range rest does not exist here


@pytest.mark.xfail(strict=True, reason=(
    "OPEN (plan U5): the reference wall puts ~1 N.m at the range end, sized for a "
    "leg. A forearm hanging at the extension end overshoots ~17 deg into "
    "hyperextension and rests ~12 deg flexed. Wall stiffness for the elbow needs "
    "a relaxed-hang angle from the literature or our own data."))
def test_arm_at_side_stays_within_physiological_hyperextension():
    p, q0 = ul.de_lima_bare()
    q = ul.simulate_elbow(p, start="flexion", release_q_deg=q0, duration=10.0)["q_deg"]
    assert q.min() > -10.0


def test_upper_arm_horizontal_rests_mid_range():
    p = ul.elbow_params(start="extension", upper_arm_deg=90.0)
    r = ul.simulate_elbow(p, start="extension", release_q_deg=10.0, duration=15.0)
    assert np.median(r["q_deg"][-1000:]) == pytest.approx(90.0, abs=1.0)


@pytest.mark.parametrize("start,q0", [("extension", 87.0), ("flexion", 87.0)])
def test_release_on_wrong_side_of_rest_still_runs_but_falls_toward_rest(start, q0):
    p = ul.elbow_params(start=start, upper_arm_deg=90.0)
    q = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=0.3)["q_deg"]
    assert abs(q[-1] - 90.0) < abs(q0 - 90.0) + 1e-6


# ── U3: each start stretches its own muscle ───────────────────────────────
@pytest.mark.parametrize("start,q0", [("extension", 5.0), ("flexion", 140.0)])
def test_active_torque_opposes_the_first_swing(start, q0):
    p = ul.elbow_params(start=start, upper_arm_deg=90.0, tone=0.5, k_f=0.75, k_df=0.025)
    r = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=0.6, record_torques=True)
    tq = r["torques"]
    active = tq["tone"] + tq["srs"] + tq["reflex"]
    v = np.gradient(r["q_deg"], r["t"])
    moving = np.abs(v) > 20.0                                     # deg/s, inside the first swing
    first = moving & (np.sign(v) == np.sign(90.0 - q0))           # still heading toward the rest
    assert first.sum() > 50
    assert np.all(np.sign(active[first]) == -np.sign(v[first]))   # muscle resists the stretch


def test_extension_start_at_knee_style_frame_is_negative_theta():
    p = ul.elbow_params(start="extension", upper_arm_deg=90.0)
    assert p.lim_lo_deg == -145.0 and p.lim_hi_deg == 0.0
    p = ul.elbow_params(start="flexion", upper_arm_deg=90.0)
    assert p.lim_lo_deg == 0.0 and p.lim_hi_deg == 145.0


def test_more_tone_on_extension_start_means_fewer_swings():
    """Huang 2016's direction: higher tone reads as fewer swings."""
    from neuromech.elbow_score import score_elbow
    swings = []
    for tone in (0.0, 0.6):
        p = ul.elbow_params(start="extension", upper_arm_deg=90.0, tone=tone, k_f=0.75 * tone, k_df=0.025 * tone)
        r = ul.simulate_elbow(p, start="extension", release_q_deg=10.0, duration=15.0)
        swings.append(score_elbow(r["t"], r["q_deg"], J=rt.inertia(p)[2]).swings)
    assert swings[1] < swings[0]


def test_bad_start_and_posture_raise():
    with pytest.raises(ValueError):
        ul.elbow_params(start="sideways")
    with pytest.raises(ValueError):
        ul.elbow_params(start="flexion", upper_arm_deg=170.0)
