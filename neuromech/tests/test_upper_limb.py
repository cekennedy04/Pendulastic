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
#: theta_deg at samples 250, 1000, 2500, 4000, computed on origin/main (80d2b26)
#: before the per-limb walls and spring existed.
GOLDEN_MAIN = {
    "reference_spastic": (dict(tone=2.0, k_f=0.75, k_df=0.025),
                          (-17.94607272293997, -46.44658403617845, -42.47133716928954, -39.74940939236602)),
    "passive": (dict(), (-43.348638109775834, -26.531421764970307, -55.5204314832306, -62.95586914099706)),
}


@pytest.mark.parametrize("name", GOLDEN_MAIN)
def test_knee_matches_main_before_upper_limb_fields(name):
    kw, want = GOLDEN_MAIN[name]
    r = rt.simulate(rt.Params(**kw), duration=4.0)["theta_deg"]
    got = [r[i] for i in (250, 1000, 2500, 4000)]
    assert got == pytest.approx(want, abs=1e-9)


def test_knee_passive_rest_refuses_upper_limb_params():
    with pytest.raises(ValueError):
        rt.passive_rest_deg(ul.elbow_params(start="flexion"))


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
    p = ul.elbow_params(start=start, rest_q_deg=90.0, k_passive=K, use_limit=False)
    lc, m, J = rt.inertia(p)
    q0 = 93.0 if start == "flexion" else 87.0
    r = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=4.0)
    k_eff = K + m * rt.ref.G * lc               # spring + linearised gravity
    wn = math.sqrt(k_eff / J); zeta = p.B / (2 * math.sqrt(k_eff * J)); wd = wn * math.sqrt(1 - zeta ** 2)
    t = r["t"]; x0 = q0 - 90.0
    exact = 90.0 + x0 * np.exp(-zeta * wn * t) * (np.cos(wd * t) + zeta * wn / wd * np.sin(wd * t))
    assert np.max(np.abs(r["q_deg"] - exact)) < 0.02 * abs(x0)


def test_arm_at_side_has_no_mid_range_rest():
    """The arm-at-side rest is at the extension END. (How close to 0 it sits is the
    open wall question, tracked by the xfail below, not asserted here.)"""
    p, q0 = ul.de_lima_bare()
    r = ul.simulate_elbow(p, start="flexion", release_q_deg=q0, duration=10.0)
    assert np.median(r["q_deg"][-1000:]) < 45.0
    assert r["rest_q_deg"] == pytest.approx(np.median(r["q_deg"][-1000:]), abs=0.2)


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
    p = ul.elbow_params(start="extension", rest_q_deg=90.0)
    r = ul.simulate_elbow(p, start="extension", release_q_deg=10.0, duration=15.0)
    assert np.median(r["q_deg"][-1000:]) == pytest.approx(90.0, abs=1.0)


@pytest.mark.parametrize("start,q0", [("extension", 95.0), ("flexion", 85.0)])
def test_release_on_wrong_side_of_rest_is_refused(start, q0):
    p = ul.elbow_params(start=start, rest_q_deg=90.0)
    with pytest.raises(ValueError):
        ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=0.3)


@pytest.mark.parametrize("start", ul.STARTS)
def test_passive_rest_solver_matches_simulation_with_spring(start):
    p = ul.elbow_params(start=start, rest_q_deg=90.0, k_passive=2.0, k_passive_ref_q_deg=60.0)
    rest = ul.passive_rest_q_deg(p, start=start)
    q0 = 140.0 if start == "flexion" else 5.0
    r = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=20.0)
    assert 60.0 < rest < 90.0
    assert np.median(r["q_deg"][-2000:]) == pytest.approx(rest, abs=0.05)


@pytest.mark.parametrize("zeta_target", [0.9, 1.5, 2.0])
@pytest.mark.parametrize("q0", [100.0, 115.0, 130.0])
def test_heavily_damped_elbow_scores_from_any_release_with_gravity_known(zeta_target, q0):
    """The patient end (high zeta) must not be scored or refused by release angle.
    Gravity known -> fit over the large-amplitude start, where the fast mode lives.
    Walls off, so the truth is exactly the spring and the damping: this tests the
    scorer, not the (open) wall model, whose stiffening near 145 deg would read
    as extra K on a large release."""
    from neuromech.elbow_score import score_elbow
    K = 2.5
    p0 = ul.elbow_params(start="flexion", rest_q_deg=90.0, k_passive=K, use_limit=False)
    lc, m, J = rt.inertia(p0)
    kg = m * rt.ref.G * lc
    B = zeta_target * 2 * math.sqrt((K + kg) * J)
    p = ul.elbow_params(start="flexion", rest_q_deg=90.0, k_passive=K, B=B, use_limit=False)
    r = ul.simulate_elbow(p, start="flexion", release_q_deg=q0, duration=15.0)
    s = score_elbow(r["t"], r["q_deg"], J=J, k_gravity=kg)
    assert s.fit_mode == "gravity", s.refusals
    assert s.B_fit == pytest.approx(B, rel=0.03), s.refusals
    assert s.K_tissue_fit == pytest.approx(K, rel=0.05)


def test_large_release_reads_wall_stiffening_as_extra_k():
    """Documents the open wall question's effect on the scorer: with the reference
    wall on, a release 40 deg out reads tissue K well above the spring."""
    from neuromech.elbow_score import score_elbow
    K = 2.5
    p = ul.elbow_params(start="flexion", rest_q_deg=90.0, k_passive=K, B=1.5)
    lc, m, J = rt.inertia(p)
    r = ul.simulate_elbow(p, start="flexion", release_q_deg=130.0, duration=15.0)
    s = score_elbow(r["t"], r["q_deg"], J=J, k_gravity=m * rt.ref.G * lc)
    assert s.K_tissue_fit > 1.2 * K


def test_absolute_angle_modules_are_refused():
    with pytest.raises(ValueError):
        ul.elbow_params(start="flexion", contract_k=5.0, contract_deg=-60.0)


@pytest.mark.parametrize("start,q0", [("extension", 70.0), ("flexion", 110.0)])
def test_scorer_recovers_simulated_passive_elbow_stiffness(start, q0):
    """Score an emulator trace. The model's stiffness at rest is spring + gravity +
    the reference range wall, which still contributes ~0.17 N.m/rad at mid-range
    (part of the open wall question); the scorer must recover that total."""
    from neuromech.elbow_score import score_elbow
    K = 2.5
    p = ul.elbow_params(start=start, rest_q_deg=90.0, k_passive=K)
    lc, m, J = rt.inertia(p)
    sign = -1.0 if start == "extension" else 1.0
    th = math.radians(sign * ul.passive_rest_q_deg(p, start=start)); h = 1e-6
    k_model = -(ul._passive_torque_theta(p, th + h) - ul._passive_torque_theta(p, th - h)) / (2 * h)
    assert 0.1 < k_model - K - m * rt.ref.G * lc < 0.25          # the wall's share
    r = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=15.0)
    s = score_elbow(r["t"], r["q_deg"], J=J)
    assert s.K_fit == pytest.approx(k_model, rel=0.015)
    assert s.K_logdec == pytest.approx(k_model, rel=0.05)
    zeta = p.B / (2 * math.sqrt(k_model * J))
    assert s.zeta_fit == pytest.approx(zeta, rel=0.05)


# ── U3: each start stretches its own muscle ───────────────────────────────
@pytest.mark.parametrize("start,q0", [("extension", 5.0), ("flexion", 140.0)])
def test_active_torque_opposes_the_first_swing(start, q0):
    p = ul.elbow_params(start=start, rest_q_deg=90.0, tone=0.5, k_f=0.75, k_df=0.025)
    r = ul.simulate_elbow(p, start=start, release_q_deg=q0, duration=0.6, record_torques=True)
    tq = r["torques"]
    active = tq["tone"] + tq["srs"] + tq["reflex"]
    v = np.gradient(r["q_deg"], r["t"])
    moving = np.abs(v) > 20.0                                     # deg/s, inside the first swing
    first = moving & (np.sign(v) == np.sign(90.0 - q0))           # still heading toward the rest
    assert first.sum() > 50
    assert np.all(np.sign(active[first]) == -np.sign(v[first]))   # muscle resists the stretch


def test_extension_start_at_knee_style_frame_is_negative_theta():
    p = ul.elbow_params(start="extension", rest_q_deg=90.0)
    assert p.lim_lo_deg == -145.0 and p.lim_hi_deg == 0.0
    p = ul.elbow_params(start="flexion", rest_q_deg=90.0)
    assert p.lim_lo_deg == 0.0 and p.lim_hi_deg == 145.0


def test_more_tone_on_extension_start_means_fewer_swings():
    """Huang 2016's direction: higher tone reads as fewer swings."""
    from neuromech.elbow_score import score_elbow
    swings = []
    for tone in (0.0, 0.6):
        p = ul.elbow_params(start="extension", rest_q_deg=90.0, tone=tone, k_f=0.75 * tone, k_df=0.025 * tone)
        r = ul.simulate_elbow(p, start="extension", release_q_deg=10.0, duration=15.0)
        swings.append(score_elbow(r["t"], r["q_deg"], J=rt.inertia(p)[2]).swings)
    assert swings[1] < swings[0]


def test_bad_start_and_posture_raise():
    with pytest.raises(ValueError):
        ul.elbow_params(start="sideways")
    with pytest.raises(ValueError):
        ul.elbow_params(start="flexion", rest_q_deg=170.0)
