"""Elbow literature gate (plan U5). See neuromech/elbow_validation.py for which checks
are inputs, which are calibrated, and which are predictions."""
from functools import lru_cache

import pytest

from neuromech import upper_limb as ul
from neuromech import elbow_validation as ev

BOUNDS = ul.LIN_RIG_J_BRACKET


@lru_cache(maxsize=None)
def _run(start, rig_j, k_v=0.0):
    return ev.run_lin(start, rig_j, k_v=k_v)


def _band(key, k=2.0):
    """Union of the Lin groups' mean +/- k SD."""
    vals = ev.LIN_HEALTHY[key].values()
    return min(m - k * s for m, s in vals), max(m + k * s for m, s in vals)


# ── builder ───────────────────────────────────────────────────────────────
def test_lin_rig_gravity_is_counterweight_minus_forearm():
    p, q0, info = ul.lin_apparatus(start="flexion", rig_j=0.8, mass_kg=64.0, height_m=1.65)
    lf, mf, jf = ul.forearm_from_anthropometry(64.0, 1.65)
    g = 9.81
    assert info["k_gravity"] == pytest.approx(1.13 * g * 0.5375 - mf * g * lf, rel=1e-3)
    assert info["J"] == pytest.approx(jf + 0.8)
    assert q0 == 130.0 and ul.lin_apparatus(start="extension", rig_j=0.8)[1] == 50.0
    assert p.B == pytest.approx(0.2 + ul.LIN_RIG_B)
    assert info["k_tissue"] == pytest.approx(ul.lin_k_tissue(64.0))


def test_lin_rig_refuses_a_forearm_that_outweighs_the_counterweight():
    with pytest.raises(ValueError, match="tip"):
        ul.lin_apparatus(start="flexion", rig_j=0.8, mass_kg=150.0, height_m=2.0)


def test_extensor_scale_applies_to_extension_start_tissue_only():
    pf = ul.elbow_params(start="flexion", k_passive=2.0, B=0.2, extensor_scale=1.5)
    pe = ul.elbow_params(start="extension", k_passive=2.0, B=0.2, extensor_scale=1.5)
    assert (pf.k_passive, pf.B) == (2.0, 0.2)
    assert (pe.k_passive, pe.B) == pytest.approx((3.0, 0.3))
    with pytest.raises(ValueError):
        ul.elbow_params(start="extension", extensor_scale=0.0)


# ── healthy, both inertia bounds ──────────────────────────────────────────
@pytest.mark.parametrize("rig_j", BOUNDS)
def test_healthy_lin_rig_inputs_and_calibration_read_back(rig_j):
    """K is an input and C is calibrated: this checks they come back, not that they are right."""
    s = _run("flexion", rig_j)["score"]
    assert not s.refusals, s.refusals
    lo, hi = _band("K", 1.0)
    assert lo <= s.K_tissue_fit <= hi
    assert 0.53 <= s.B_fit <= 0.78
    assert s.eta_lin < ev.LIN_CUT_ETA and s.B_fit < ev.LIN_CUT_C


@pytest.mark.parametrize("rig_j", BOUNDS)
def test_healthy_first_turn_inside_lin_traces(rig_j):
    """Lin 2003 fig 3 (N4) and Lin 2005 fig 1 reach the first trough ~1.0-1.4 s after release
    (read from the plots). The bracket was derived from this, so it is a consistency check."""
    assert 0.85 <= _run("flexion", rig_j)["first_turn_s"] <= 1.5


@pytest.mark.parametrize("rig_j", BOUNDS)
def test_healthy_swings_inside_lin_band(rig_j):
    """PREDICTED. Counted at 3 deg (module note)."""
    lo, hi = _band("swings")
    assert max(lo, 1) <= _run("flexion", rig_j)["score"].swings <= hi


@pytest.mark.parametrize("rig_j", BOUNDS)
def test_healthy_ri_within_one_and_a_half_sd_of_lin(rig_j):
    """PREDICTED. Inside the wide band, outside the narrow one (next test)."""
    lo, hi = _band("RI", 1.5)
    assert lo <= _run("flexion", rig_j)["score"].ri <= hi


@pytest.mark.xfail(strict=True, reason="known mismatch (plan 11.6/11.7): RI 1.60-1.70 vs Lin's 1.43-1.51; "
                                        "Lin's averages are not all reachable by a linear model")
@pytest.mark.parametrize("rig_j", BOUNDS)
def test_healthy_ri_matches_lin_means(rig_j):
    assert _run("flexion", rig_j)["score"].ri <= 1.55


# ── Huang 2016: the extension start is stiffer and more damped ─────────────
@pytest.mark.parametrize("rig_j", BOUNDS)
def test_extension_start_fits_higher_k_and_c(rig_j):
    """Reads back the 1.5x extensor scale (an INPUT, Huang 2016), not a prediction."""
    f = _run("flexion", rig_j)["score"]; e = _run("extension", rig_j)["score"]
    assert e.K_tissue_fit > 1.3 * f.K_tissue_fit
    assert e.B_fit > f.B_fit


# ── Lin 2003: a velocity-dependent drive raises C and eta, not K ───────────
@pytest.mark.parametrize("rig_j", BOUNDS)
def test_velocity_drive_raises_damping_past_lin_cutoff_while_k_moves_little(rig_j):
    h = _run("flexion", rig_j)["score"]; s = _run("flexion", rig_j, k_v=0.5)["score"]
    assert s.B_fit > ev.LIN_CUT_C > h.B_fit
    assert s.eta_lin > h.eta_lin
    assert abs(s.K_tissue_fit / h.K_tissue_fit - 1) < 0.15     # Lin: K not significant (SD ~40%)
    assert s.swings < h.swings and s.ri < h.ri


def test_strong_velocity_drive_crosses_lin_eta_cutoff():
    """J 0.55 only: at 1.15 the fit is refused (logdec, fit), see the report table."""
    s = _run("flexion", BOUNDS[0], k_v=1.0)["score"]
    assert s.eta_lin > ev.LIN_CUT_ETA and s.B_fit > ev.LIN_CUT_C


def test_report_renders():
    assert "healthy, flexion start" in ev.report()
