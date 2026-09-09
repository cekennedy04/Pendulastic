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


# ── R15: the passive profile the rig is dialled to ──────────────────────────


def test_the_passive_model_reproduces_the_published_derivation():
    """Popović & Bajd (2018) give B = 0.5 Nms/rad and K = 0.5 Nm/rad, from
    Bajd & Bowman's (1982) linearised model with m = 5 kg, l = 0.6 m. Their
    stated intermediates are J ~ 0.6 kg m^2 and beta ~ 0.417 1/s. If this
    module cannot reproduce those, the rig would be dialled to the wrong
    numbers and every profile above them would inherit the error."""
    e = rc.passive_envelope()
    assert e["inertia_kg_m2"] == pytest.approx(0.6, abs=1e-6)
    assert e["damping_factor_per_s"] == pytest.approx(0.417, abs=5e-4)


def test_the_predicted_swing_is_underdamped_and_therefore_oscillates():
    """A pendulum test needs a limb that swings. If the healthy constants
    predicted critical damping there would be nothing to measure."""
    e = rc.passive_envelope()
    assert 0.0 < e["damping_ratio"] < 1.0
    assert e["damped_freq_hz"] > 0.5


def test_the_prediction_lands_near_the_corpus_healthy_reference():
    """Two independent routes to the same quantity: this is a 1982 model fitted
    to goniograms, and HEALTHY_REF is a median over this project's own control
    trials. They should not be far apart, and if they diverge sharply one of
    them is wrong. Measured: predicted 0.799 Hz against 0.914 measured (14%),
    and 5.21 predicted cycles against N = 5.50.

    Loose bounds on purpose. m and l are population averages for a shank-foot
    complex, so a real limb differing from them moves the frequency without
    anything being wrong. This catches an order-of-magnitude error, not a
    physiological difference."""
    e = rc.passive_envelope()
    assert 0.6 < e["damped_freq_hz"] < 1.2, "predicted frequency is not limb-like"
    assert 3.0 < e["cycles_to_stop"] < 9.0, "predicted cycle count is not limb-like"
    assert e["damped_freq_hz"] == pytest.approx(pts.HEALTHY_REF["f"], rel=0.25)


def test_stiffer_settings_swing_less_and_stop_sooner():
    """The rig dials K upward to simulate tone. The direction must be right, or
    the profiles would be labelled backwards before a clinician ever saw them."""
    base = rc.passive_envelope()
    stiff = rc.passive_envelope(K=20.0)
    damped = rc.passive_envelope(B=3.0)
    assert stiff["damped_freq_hz"] > base["damped_freq_hz"], "more K, faster"
    assert damped["cycles_to_stop"] < base["cycles_to_stop"], "more B, fewer cycles"
