"""live_verdict: the desktop live view's score and label, through mas_estimate.

The live view used to print pt_to_mas(compute_pt_score_simple(p)) gated on
excursion only. These pin that it now gives the canonical score and the same
verdict as every other surface -- including the arrested limb it used to grade.
"""
import math

import pendulastic_pt_score as pt


def _params(**kw):
    p = {"R2n": 0.9, "N": 5.0, "phi_max_ratio": 0.55, "omega_max_n": 6.0,
         "omega_min_n": -6.0, "f": 0.85, "area_ratio": 0.12,
         "A0_deg": 50.0, "A1_deg": 30.0}
    p.update(kw)
    return p


def test_a_gradeable_trial_gets_the_canonical_score_and_its_grade():
    p = _params()
    score, label = pt.live_verdict(p)
    est = pt.mas_estimate(p)
    assert est["interpretable"]
    assert score == pt.compute_pt_score(p)
    assert label == est["mas"] and label in pt.MAS_ORDER


def test_an_arrested_limb_is_refused_where_the_old_path_graded_it():
    # A falls-and-holds limb: good A0, no first swing. A1 = 0 forces the
    # oscillation parameters to zero.
    p = _params(A1_deg=0.0, R2n=0.0, N=0.0, phi_max_ratio=0.0, f=0.0)
    # The old live-view path: excursion passes, so it printed a grade.
    assert pt.excursion_ok(p)
    assert pt.pt_to_mas(pt.compute_pt_score_simple(p)) in pt.MAS_ORDER
    score, label = pt.live_verdict(p)
    assert label.startswith("not interpretable: no measurable oscillation"), label
    assert "A0 50.0 deg" in label
    assert score == pt.compute_pt_score(p)        # the number stays visible


def test_a_short_swing_names_the_excursion_gate():
    p = _params(A0_deg=pt.MIN_INTERPRETABLE_A0_DEG - 2.0)
    score, label = pt.live_verdict(p)
    assert label.startswith("not interpretable: insufficient excursion"), label
    assert math.isfinite(score)


def test_no_params_reads_as_not_interpretable_with_no_score():
    score, label = pt.live_verdict(None)
    assert score is None
    assert label.startswith("not interpretable"), label


def test_the_live_score_does_not_carry_omega_max_n():
    # The canonical score left omega_max_n on 2026-10-27; the live view must not
    # bring it back through the simple score.
    lo, hi = _params(omega_max_n=1.0), _params(omega_max_n=20.0)
    assert pt.live_verdict(lo)[0] == pt.live_verdict(hi)[0]
