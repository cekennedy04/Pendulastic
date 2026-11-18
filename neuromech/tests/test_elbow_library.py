"""Elbow scenario library, group E (plan U6) and the free-rest-angle diagnostic."""
from functools import lru_cache

import numpy as np
import pytest

from neuromech import elbow_library as el
from neuromech import elbow_validation as ev
from neuromech import scenarios as S

LIB = el.build_elbow()
BY_ID = {sc.sid: sc for sc in LIB}


@lru_cache(maxsize=None)
def _row(sid):
    return el.score_row(BY_ID[sid])


def _ladder(family, start="flexion"):
    return sorted((sc for sc in LIB if sc.family == family and sc.start == start), key=lambda sc: sc.level)


def test_ids_unique_and_all_group_e():
    assert len(BY_ID) == len(LIB)
    assert all(sc.sid.startswith("E-") for sc in LIB)


def test_elbow_library_is_separate_from_the_knee_library():
    """No knee scenario id is reused and nothing in the knee library is group E."""
    knee = S.build({})
    assert not {sc.sid for sc in knee} & set(BY_ID)
    assert all(sc.group != "E" for sc in knee)


@pytest.mark.parametrize("sid", sorted(BY_ID))
def test_every_row_has_elbow_measures_and_no_knee_grade(sid):
    r = _row(sid)
    for k in ("swings", "first_turn_s", "first_excursion_deg", "peak_fall_dps", "passive_rest_q_deg"):
        assert r[k] is not None, k
    assert not {"pt7", "mas", "band", "interpretable"} & set(r)
    # a fitted measure is either all there or refused, never half
    fitted = [r[k] is None for k in ("C_fit", "K_tissue_fit", "zeta_fit")]
    assert all(fitted) or not any(fitted)
    assert r["settled_q_deg"] is not None
    if r["C_fit"] is None:
        assert "fit" in r["refusals"] or "ri" in r["refusals"] or "logdec" in r["refusals"]


@pytest.mark.parametrize("family", ["V", "G"])
def test_each_drive_ladder_is_monotonic_in_model_free_measures(family):
    rows = [_row(sc.sid) for sc in [BY_ID["E-H-flex"]] + _ladder(family)]
    exc = [r["first_excursion_deg"] for r in rows]
    vpk = [r["peak_fall_dps"] for r in rows]
    sw = [r["swings"] for r in rows]
    ri = [r["ri"] for r in rows if r["ri"] is not None]
    assert all(a > b for a, b in zip(exc, exc[1:])), exc
    assert all(a > b for a, b in zip(vpk, vpk[1:])), vpk
    assert all(a >= b for a, b in zip(sw, sw[1:])), sw
    assert all(a > b for a, b in zip(ri, ri[1:])), ri


def test_first_swing_skips_a_flat_hold_and_handles_no_turn():
    t = np.linspace(0, 2, 2001)
    q = np.where(t < 0.5, 130.0, 90.0 + 40.0 * np.cos(4.0 * (t - 0.5)))
    t1, exc, vpk = el.first_swing(t, q)
    assert t1 == pytest.approx(0.5 + np.pi / 4.0, abs=0.01) and exc == pytest.approx(80.0, abs=0.5)
    t1, exc, _ = el.first_swing(t, 130.0 - 10.0 * t)          # falls, never turns
    assert t1 == t[-1] and exc == pytest.approx(20.0)
    with pytest.raises(ValueError):
        el.first_swing(t, np.full_like(t, 130.0))


def test_de_groote_tone_moves_the_settled_rest_toward_the_stretched_muscle():
    """The passive rest is 90 on every row; the settled rest is where tone puts it."""
    g = [_row(sc.sid)["settled_q_deg"] for sc in _ladder("G")]
    assert all(a < b for a, b in zip(g, g[1:])) and g[-1] > 93.0
    assert _row("E-G3-ext")["settled_q_deg"] < 89.0
    assert _row("E-H-flex")["settled_q_deg"] == pytest.approx(90.0, abs=0.1)


def test_healthy_reads_back_its_inputs():
    r = _row("E-H-flex")
    assert r["C_fit"] == pytest.approx(0.2, rel=0.03)
    assert r["K_tissue_fit"] == pytest.approx(ev.ul.lin_k_tissue(el.MASS_KG), rel=0.03)


def test_low_damping_swings_more_and_reads_low_c():
    lo, h = _row("E-lowB"), _row("E-H-flex")
    assert lo["swings"] > 2 * h["swings"] and lo["ri"] > h["ri"]
    assert lo["C_fit"] == pytest.approx(0.05, rel=0.05)


def test_de_groote_drops_are_refused_by_the_scorer():
    """Expected value, not a failure: the tone shifts the rest (plan 11.8)."""
    for sc in _ladder("G")[1:]:
        assert _row(sc.sid)["C_fit"] is None, sc.sid


def test_free_rest_fit_scores_what_the_scorer_refuses():
    """Lin's whole-trajectory fit with q_e free: a close fit, q_e shifted toward
    the stretched muscle, and higher C than healthy. Diagnostic only."""
    def fr(sid):
        sc = BY_ID[sid]
        p = el.elbow_params_for(sc)
        lc, m, J = ev.ul.rt.inertia(p)
        r = el.render_elbow(sc)
        return ev.fit_free_rest(r["t"], r["q_deg"], J=J, k_gravity=m * ev.ul.rt.ref.G * lc)
    h, g = fr("E-H-flex"), fr("E-G3")
    assert h["ok"] and g["ok"]
    assert h["r2"] > 0.999 and abs(h["q_e_deg"] - 90.0) < 0.5
    assert g["r2"] > 0.95 and g["q_e_deg"] > 92.0
    assert g["C"] > 1.5 * h["C"]
