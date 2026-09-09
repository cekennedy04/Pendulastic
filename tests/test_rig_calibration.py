"""The rig's software half: blind mapping, anchors, and the severity index.

No rig exists yet, so every test here builds its session inline. That is the
point: the code has to be correct before the hardware arrives, and the default
state -- no anchors, no index -- has to be the one that ships until it does.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pendulastic_pt_score as pts  # noqa: E402
import rig_calibration as rc  # noqa: E402


def _session(profiles=("s0", "s1"), grades=(("0", "0", "0"), ("1+", "2", "1+")),
             a0s=((60.0, 61.0, 59.0), (30.0, 31.0, 29.0))):
    return {
        "profiles": [{"setting_id": s} for s in profiles],
        "assessments": [
            {"setting_id": s, "assessor": f"c{i}", "grade": g}
            for s, gs in zip(profiles, grades) for i, g in enumerate(gs)
        ],
        "readings": [
            {"setting_id": s, "a0_deg": v}
            for s, vs in zip(profiles, a0s) for v in vs
        ],
    }


# ── R3: the mapping never sees the instrument ───────────────────────────────


def test_an_assessment_carrying_an_instrument_field_is_refused():
    """A mapping fitted partly on the instrument agrees with the instrument by
    construction, and that agreement reads as validation to anyone who was not
    there when it was built. It has to fail loudly."""
    s = _session()
    s["assessments"][0]["a0_deg"] = 55.0
    with pytest.raises(rc.BlindnessError, match="blind clinical assessment"):
        rc.stiffness_to_grade(s)


def test_the_mapping_is_the_assessors_middle_grade_not_an_average():
    """"1+" sits between 1 and 2. Averaging grades invents readings nobody gave."""
    s = _session(grades=(("0", "0", "0"), ("1", "1+", "2")))
    assert rc.stiffness_to_grade(s)["s1"] == "1+"


def test_a_profile_with_one_assessor_does_not_enter_the_mapping():
    s = _session(grades=(("0", "0", "0"), ("2",)))
    assert "s1" not in rc.stiffness_to_grade(s)


# ── R4: agreement is reported with the mapping ──────────────────────────────


def test_inter_rater_agreement_reports_disagreement_rather_than_hiding_it():
    s = _session(grades=(("0", "0", "0"), ("1", "3", "1")))
    a = rc.inter_rater_agreement(s, "s1")
    assert a["n_assessors"] == 3
    assert a["exact"] == pytest.approx(1 / 3)
    assert a["rank_spread"] == 3
    assert a["sufficient"]


def test_one_assessor_is_reported_as_insufficient_not_as_perfect_agreement():
    s = _session(grades=(("0",), ("2",)))
    a = rc.inter_rater_agreement(s, "s0")
    assert a["exact"] is None and not a["sufficient"]


# ── R7/R16: anchors, and the default of having none ─────────────────────────


def test_no_rig_data_means_no_anchors():
    assert rc.derive_anchors({"profiles": [], "assessments": [], "readings": []}) is None
    assert rc.derive_anchors(rc.load_session("/nonexistent/rig.json")) is None


def test_anchors_come_from_the_graded_profiles_and_carry_their_spread():
    a = rc.derive_anchors(_session())
    assert a["healthy_a0_deg"] == 60.0 and a["boundary_a0_deg"] == 30.0
    assert a["healthy_spread"] == 2.0 and a["boundary_spread"] == 2.0
    assert a["n_healthy"] == 3 and a["n_boundary"] == 3


def test_too_few_trials_at_a_setting_yields_no_anchors():
    """A spread across two trials is a gap between two numbers."""
    assert rc.derive_anchors(_session(a0s=((60.0, 61.0), (30.0, 29.0)))) is None


def test_anchors_that_run_backwards_are_refused():
    """A0 falls as severity rises. If the healthy profile swings less than the
    affected one, the rig or the mapping is wrong and the index would invert."""
    assert rc.derive_anchors(_session(a0s=((30.0, 31.0, 29.0),
                                           (60.0, 61.0, 59.0)))) is None


# ── R6/R9/KD3: the index itself ─────────────────────────────────────────────

_A = {"healthy_a0_deg": 60.0, "boundary_a0_deg": 30.0}


def test_the_healthy_anchor_scores_exactly_zero():
    assert pts.severity_index(60.0, _A) == 0.0


def test_the_boundary_anchor_scores_exactly_one():
    assert pts.severity_index(30.0, _A) == 1.0


def test_swinging_further_than_healthy_scores_zero_not_a_negative():
    assert pts.severity_index(90.0, _A) == 0.0


def test_below_the_boundary_is_unbounded_and_not_clamped():
    """Clamping turns a reading the instrument cannot support into a confident
    one -- the same failure the excursion gate exists to prevent at the top."""
    assert pts.severity_index(15.0, _A) == pytest.approx(1.5)
    assert pts.severity_index(0.0, _A) == pytest.approx(2.0)


def test_the_index_rises_monotonically_as_amplitude_falls():
    vals = [pts.severity_index(a, _A) for a in (90, 60, 50, 40, 30, 20, 10, 0)]
    assert vals == sorted(vals)


def test_no_anchors_means_no_index_and_there_is_no_default():
    assert pts.severity_index(45.0, None) is None
    with pytest.raises(TypeError):
        pts.severity_index(45.0)


# ── R16/R25: the gates in front of it ───────────────────────────────────────


def test_without_the_rig_the_report_says_so_rather_than_returning_a_number():
    r = pts.severity_report({"A0_deg": 45.0}, None)
    assert r["available"] is False and r["index"] is None
    assert "rig" in r["reason"]


def test_a_phone_capture_is_refused_an_index_with_the_reason_stated():
    """R25. Phone A0 does not track the reference (Spearman 0.05 over 100
    paired trials), so an index computed on it is not the quantity R6 names."""
    r = pts.severity_report({"A0_deg": 45.0}, _A, source="imu")
    assert r["available"] is False
    assert "phone" in r["reason"] and "0.05" in r["reason"]


def test_an_optical_capture_with_anchors_produces_the_index():
    r = pts.severity_report({"A0_deg": 45.0}, _A, source="optitrack")
    assert r["available"] is True and r["index"] == pytest.approx(0.5)


def test_a_refused_excursion_yields_no_index_even_with_anchors():
    r = pts.severity_report({"A0_deg": 9.0}, _A, source="optitrack")
    assert r["available"] is False
    assert "excursion" in r["reason"].lower()
