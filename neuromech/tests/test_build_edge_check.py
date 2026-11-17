"""Owner decision O5 (2026-11-16): presets sit in the middle of their bands, and the build flags any preset
within build.EDGE_MARGIN_PT7 (0.03) of a band edge unless scenarios.NEAR_EDGE_ALLOWED says why it is there.

The check itself needs nothing built. The refit presets are rendered and scored here (a few seconds each);
the tests that read the built library skip when it is absent or was built by other code.
"""
import csv
import dataclasses
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import build as B  # noqa: E402
from neuromech import profiles as PR  # noqa: E402
from neuromech import scenarios as S  # noqa: E402

#: How far from its band's middle a refit preset may score. PT7 is a step function of each knob, so the
#: middle itself is not always on a plateau (B-clonus cannot get nearer than 0.024: scenarios.BAND_MIDDLE).
MID_BAND_TOL = 0.025

#: The one knob each refit preset was fitted on (build.fit_param), for the +-5 % robustness check.
REFIT_FIELD = {"B-claspknife": "catch_release_deg", "K-HM1": "hess_H", "B-spasm": "spasm_nm",
               "B-flexor": "flex_vth", "B-clonus": "clonus_deg"}


# ── the check ──────────────────────────────────────────────────────────────────────────────────────────

def test_band_edges_are_the_scorers():
    assert B.BAND_EDGES == (0.12, 0.28, 0.44, 0.60, 0.78)


@pytest.mark.parametrize("pt7, dist, edge", [(0.2818, 0.0018, 0.28), (0.2788, -0.0012, 0.28), (0.06, -0.06, 0.12),
                                             (0.20, 0.08, 0.12), (0.43, -0.01, 0.44), (1.74, 0.96, 0.78)])
def test_edge_distance_is_signed_to_the_nearest_edge(pt7, dist, edge):
    d, e = B.edge_distance(pt7)
    assert e == edge and d == pytest.approx(dist)


def _rows(**pts):
    return [dict(sid=sid, pt7=pt) for sid, pt in pts.items()]


def test_a_preset_near_an_edge_is_flagged_and_fails_the_report(capsys):
    res = B.edge_check(_rows(**{"X-mid": 0.20, "X-near": 0.125, "X-below": 0.2751}), allowed={})
    assert [f[0] for f in res["flagged"]] == ["X-near", "X-below"]
    assert not B.report_edge_check(res)
    out = capsys.readouterr().out
    assert "NEAR EDGE   X-near" in out and "0.0050 above the 0.12 edge" in out
    assert "0.0049 below the 0.28 edge" in out


def test_the_margin_is_exclusive_and_tunable():
    assert B.edge_check(_rows(a=0.15), allowed={})["flagged"] == []            # exactly 0.03 away: not near
    assert [f[0] for f in B.edge_check(_rows(a=0.15), allowed={}, margin=0.031)["flagged"]] == ["a"]


def test_an_allowlisted_preset_passes_and_is_still_listed(capsys):
    res = B.edge_check(_rows(**{"C-x": 0.2818}), allowed={"C-x": "probes the edge"})
    assert res["flagged"] == [] and [a[0] for a in res["allowed"]] == ["C-x"]
    assert B.report_edge_check(res)
    assert "allowlisted C-x" in capsys.readouterr().out


def test_a_stale_allowlist_entry_fails_and_one_that_moved_away_is_noted(capsys):
    res = B.edge_check(_rows(**{"C-x": 0.20}), allowed={"C-x": "was near", "C-gone": "not built"})
    assert res["unknown"] == ["C-gone"] and res["not_near"] == ["C-x"]
    assert not B.report_edge_check(res)
    out = capsys.readouterr().out
    assert "STALE       C-gone" in out and "C-x is allowlisted but no longer within" in out


def test_rows_without_a_pt7_are_not_judged():
    res = B.edge_check([dict(sid="u", pt7=None), dict(sid="n", pt7=float("nan"))], allowed={})
    assert res["flagged"] == [] and res["allowed"] == []


# ── fit_param, on a stand-in scorer (no rendering) ─────────────────────────────────────────────────────

def _stand_in(monkeypatch, f, field="catch_release_deg"):
    """PT7 as f(value of `field`), in place of rendering and scoring; returns the preset to fit."""
    monkeypatch.setattr(B, "pt7_of", lambda sc: f(getattr(sc.params, field)))
    return S.Scenario("X-fit", "B", "", S.Params(catch_gain=4.0, catch_vth=1.2))


def test_fit_param_finds_the_target_rising_and_falling(monkeypatch):
    sc = _stand_in(monkeypatch, lambda v: 0.01 * v)                      # rising: 0.20 at 20
    assert B.fit_param(sc, "catch_release_deg", 10.0, 40.0, 0.20) == pytest.approx(20.0, abs=1e-4)
    sc = _stand_in(monkeypatch, lambda v: 0.5 - 0.01 * v)                # falling: 0.20 at 30
    assert B.fit_param(sc, "catch_release_deg", 10.0, 40.0, 0.20) == pytest.approx(30.0, abs=1e-4)


def test_fit_param_lands_on_the_edge_of_a_step(monkeypatch):
    sc = _stand_in(monkeypatch, lambda v: 0.15 if v < 25.0 else 0.25)    # no value gives 0.20
    assert B.fit_param(sc, "catch_release_deg", 10.0, 40.0, 0.20) == pytest.approx(25.0, abs=1e-4)


def test_fit_param_refuses_a_target_outside_the_bracket(monkeypatch):
    sc = _stand_in(monkeypatch, lambda v: 0.01 * v)
    with pytest.raises(ValueError, match="outside the bracket"):
        B.fit_param(sc, "catch_release_deg", 10.0, 15.0, 0.20)          # PT7 0.10..0.15: would have returned 15


def test_fit_param_does_not_read_a_refusal_as_pt7_zero(monkeypatch):
    sc = _stand_in(monkeypatch, lambda v: None if v < 12.0 else 0.01 * v)
    with pytest.raises(ValueError, match="no PT7 at the bracket end 10.0"):
        B.fit_param(sc, "catch_release_deg", 10.0, 40.0, 0.20)
    sc = _stand_in(monkeypatch, lambda v: None if 24.0 < v < 26.0 else 0.01 * v)
    with pytest.raises(ValueError, match="inside the bracket"):
        B.fit_param(sc, "catch_release_deg", 10.0, 40.0, 0.25)          # the first midpoint is 25: refused


# ── the library's own lists ────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ladder():
    p = os.path.join(B.OUT, "ladder.json")
    if not os.path.exists(p):
        pytest.skip("scenario library not built (python -m neuromech.build)")
    return json.load(open(p, encoding="utf-8"))


@pytest.fixture(scope="module")
def library(ladder):
    return {sc.sid: sc for sc in S.build(ladder)}


def test_every_allowlist_entry_is_built_and_says_why(library):
    for sid, why in S.NEAR_EDGE_ALLOWED.items():
        assert sid in library, sid
        assert len(why) > 20, sid
        assert library[sid].group in ("S", "C", "D", "R", "H"), (sid, "only probes may sit on an edge")
    assert not set(S.NEAR_EDGE_ALLOWED) & set(S.BAND_MIDDLE)


def test_the_m_copy_of_the_clasp_knife_is_dropped_and_the_profile_matches_b(library):
    assert "M-catch_release_high" not in library
    assert PR.PROFILES["catch_release_high"] == library["B-claspknife"].params
    assert library["B-claspknife"].params.catch_release_deg == S.CLASP_RELEASE_DEG


def _pt7_mas(sc):
    t, th, _, _ = B.render(sc)
    _, est = B.score(t, th, sc)
    return est["pt7"], est["mas"]


@pytest.mark.parametrize("sid", sorted(S.BAND_MIDDLE))
def test_each_refit_preset_scores_near_its_band_middle(library, sid):
    pt, mas = _pt7_mas(library[sid])
    band = S.BAND_MIDDLE[sid]
    assert mas == band, (sid, pt, mas)
    assert abs(pt - S.MAS_TARGETS[band]) <= MID_BAND_TOL, (sid, pt)
    assert abs(B.edge_distance(pt)[0]) >= B.EDGE_MARGIN_PT7, (sid, pt)


@pytest.mark.parametrize("sid", sorted(REFIT_FIELD))
@pytest.mark.parametrize("factor", (0.95, 1.05))
def test_each_refit_preset_holds_its_band_with_its_knob_5_percent_off(library, sid, factor):
    sc = library[sid]
    f = REFIT_FIELD[sid]
    sc = dataclasses.replace(sc, params=dataclasses.replace(sc.params, **{f: getattr(sc.params, f) * factor}))
    pt, mas = _pt7_mas(sc)
    assert mas == S.BAND_MIDDLE[sid], (sid, factor, pt, mas)


def test_the_built_library_passes_the_edge_check():
    man = os.path.join(B.OUT, "scenario_manifest.csv")
    if not os.path.exists(man) or B.provenance_mismatch(B.library_provenance(), B.provenance()):
        pytest.skip("scenario library not built by this code (python -m neuromech.build)")
    rows = [dict(sid=r["sid"], pt7=float(r["pt7"]) if r["pt7"] else None)
            for r in csv.DictReader(open(man, encoding="utf-8"))]
    res = B.edge_check(rows)
    assert res["flagged"] == [] and res["unknown"] == [], res
    assert {r["sid"] for r in rows} >= set(S.BAND_MIDDLE)
