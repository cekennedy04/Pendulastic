"""
Tests for spasticity_grouping.py.

The behaviour that matters here is the PRECEDENCE and the PROVENANCE: a
clinical MAS must always beat a derived guess, and no leg may ever be silently
dropped or silently upgraded from "derived" to "assessed".
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import spasticity_grouping as sg


# ── mas_level ────────────────────────────────────────────────────────────────

def test_mas_zero_is_non_spastic():
    assert sg.mas_level("0") == sg.NON_SPASTIC


@pytest.mark.parametrize("grade", ["1", "1+", "2", "3", "4"])
def test_any_positive_mas_grade_is_spastic(grade):
    assert sg.mas_level(grade) == sg.SPASTIC


@pytest.mark.parametrize("grade", ["", "   ", None, sg.PENDING_MAS_GRADE])
def test_unassessed_grades_carry_no_verdict(grade):
    """Blank and the pending sentinel are "not assessed yet". Neither is
    evidence of absent spasticity, so neither may return NON_SPASTIC."""
    assert sg.mas_level(grade) is None


# ── precedence ───────────────────────────────────────────────────────────────

def test_clinical_mas_beats_the_a0_proxy():
    """The proxy is a stopgap. Where a clinician has assessed the leg, the
    assessment wins even when the amplitude would have said otherwise."""
    lab = sg.classify_leg("13", "left", arm="MS",
                          mas_by_leg={("13", "left"): "1"},
                          a0_deg=65.0)          # amplitude looks normal
    assert lab.level == sg.SPASTIC
    assert lab.source == sg.SRC_CLINICAL
    assert "MAS 1" in lab.detail


def test_clinical_mas_zero_beats_a_low_amplitude():
    lab = sg.classify_leg("9", "right", arm="MS",
                          mas_by_leg={("9", "right"): "0"},
                          a0_deg=10.0)          # amplitude looks awful
    assert (lab.level, lab.source) == (sg.NON_SPASTIC, sg.SRC_CLINICAL)


def test_control_without_mas_is_non_spastic_by_recruitment():
    lab = sg.classify_leg("23", "left", arm="Control", mas_by_leg={}, a0_deg=None)
    assert (lab.level, lab.source) == (sg.NON_SPASTIC, sg.SRC_CONTROL)


def test_control_still_defers_to_a_real_mas_score():
    """Recruitment is an assumption; an actual assessment outranks it."""
    lab = sg.classify_leg("6", "left", arm="Control",
                          mas_by_leg={("6", "left"): "1"}, a0_deg=None)
    assert (lab.level, lab.source) == (sg.SPASTIC, sg.SRC_CLINICAL)


# ── the A0 proxy ─────────────────────────────────────────────────────────────

def test_low_amplitude_leg_without_mas_is_derived_spastic():
    """P21 right measures ~21.5 deg and has no MAS -- the case this exists for."""
    lab = sg.classify_leg("21", "right", arm="Stroke", mas_by_leg={}, a0_deg=21.5)
    assert (lab.level, lab.source) == (sg.SPASTIC, sg.SRC_A0)
    assert "21.5" in lab.detail


def test_normal_amplitude_leg_without_mas_is_not_claimed_to_be_mas_zero():
    """Above the threshold means "no MARKED amplitude loss found", not "MAS 0".
    The detail string must say so, because the proxy provably misses mild
    spasticity (3 of 5 known MAS>=1 legs sit inside the healthy range)."""
    lab = sg.classify_leg("18", "left", arm="MS", mas_by_leg={}, a0_deg=58.4)
    assert (lab.level, lab.source) == (sg.NON_SPASTIC, sg.SRC_A0)
    assert "mild spasticity is not detectable" in lab.detail


def test_threshold_is_inclusive_at_the_boundary():
    at = sg.classify_leg("x", "left", arm="MS", mas_by_leg={},
                         a0_deg=sg.A0_SPASTIC_MAX_DEG)
    assert at.level == sg.SPASTIC


def test_nan_amplitude_is_not_treated_as_a_measurement():
    lab = sg.classify_leg("x", "left", arm="MS", mas_by_leg={},
                          a0_deg=float("nan"))
    assert (lab.level, lab.source) == (sg.UNKNOWN, sg.SRC_NONE)


# ── nothing is ever silently dropped ─────────────────────────────────────────

def test_diagnosed_leg_with_no_mas_and_no_swing_is_unknown_not_guessed():
    lab = sg.classify_leg("19", "left", arm="Stroke", mas_by_leg={}, a0_deg=None)
    assert (lab.level, lab.source) == (sg.UNKNOWN, sg.SRC_NONE)


def test_summarise_always_reports_the_unknown_row():
    """A table that omits the unknown count reads as full coverage."""
    counts = sg.summarise({("1", "left"): sg.SpasticityLabel(
        sg.NON_SPASTIC, sg.SRC_CONTROL, "")})
    assert counts == {sg.NON_SPASTIC: 1, sg.SPASTIC: 0, sg.UNKNOWN: 0}


# ── mas_scores.csv loading ───────────────────────────────────────────────────

def test_load_mas_by_leg_skips_unassessed_rows(tmp_path):
    p = tmp_path / "mas.csv"
    p.write_text(
        "participant,leg,condition,mas_grade\n"
        "13,left,pre,1\n"
        "17,right,pre,-1\n"          # pending sentinel
        "18,left,pre,\n"             # blank
        "5,left,post,0\n",           # wrong timepoint
        encoding="utf-8")
    got = sg.load_mas_by_leg(str(p), condition_prefix="pre")
    assert got == {("13", "left"): "1"}, got


def test_load_mas_by_leg_missing_file_is_empty_not_an_error():
    assert sg.load_mas_by_leg("/nonexistent/mas.csv") == {}


def test_real_mas_csv_parses_and_finds_the_known_split_leg():
    """P4 is MAS 0 left / 1+ right -- the case that forces per-LEG grouping."""
    by_leg = sg.load_mas_by_leg()
    if not by_leg:
        pytest.skip("mas_scores.csv not present in this checkout")
    assert by_leg.get(("4", "left")) == "0"
    assert by_leg.get(("4", "right")) == "1+"
    assert sg.classify_leg("4", "left", arm="MS", mas_by_leg=by_leg).level == sg.NON_SPASTIC
    assert sg.classify_leg("4", "right", arm="MS", mas_by_leg=by_leg).level == sg.SPASTIC


# ── both legs are always enumerated ──────────────────────────────────────────

def test_both_legs_are_returned_even_with_no_data_at_all():
    """P7 left and both P16 legs were INVISIBLE while the leg list was built
    from whatever data existed, which reads as full coverage when it is not.
    Enumerate from LEGS, always."""
    got = sg.classify_participant_legs("7", arm="Control", mas_by_leg={}, a0_by_leg={})
    assert set(got) == {"left", "right"}


def test_a_diagnosed_leg_with_no_data_is_unknown_not_absent():
    got = sg.classify_participant_legs("22", arm="Stroke", mas_by_leg={},
                                       a0_by_leg={"right": 42.7})
    assert got["left"].level == sg.UNKNOWN
    assert got["right"].level == sg.NON_SPASTIC


def test_a0_by_leg_accepts_either_key_shape():
    plain = sg.classify_participant_legs("21", arm="Stroke", mas_by_leg={},
                                         a0_by_leg={"left": 27.0, "right": 21.5})
    tupled = sg.classify_participant_legs("21", arm="Stroke", mas_by_leg={},
                                          a0_by_leg={("21", "left"): 27.0,
                                                     ("21", "right"): 21.5})
    assert plain["left"].level == tupled["left"].level == sg.SPASTIC


# ── participant-level rollup ─────────────────────────────────────────────────

def test_participant_is_spastic_if_either_leg_is():
    """Hemiparesis is unilateral. Requiring both legs would erase exactly the
    participants this grouping exists to find."""
    legs = sg.classify_participant_legs("4", arm="MS",
                                        mas_by_leg={("4", "left"): "0",
                                                    ("4", "right"): "1+"})
    got = sg.participant_level(legs)
    assert got.level == sg.SPASTIC
    assert "right" in got.detail


def test_participant_is_non_spastic_when_one_leg_is_known_and_clean():
    """P22: left unassessable, right non-spastic. The PARTICIPANT is still
    characterised -- and the note has to admit the left leg was not."""
    legs = sg.classify_participant_legs("22", arm="Stroke", mas_by_leg={},
                                        a0_by_leg={"right": 42.7})
    got = sg.participant_level(legs)
    assert got.level == sg.NON_SPASTIC
    assert "left" in got.detail


def test_participant_is_unknown_only_when_no_leg_could_be_labelled():
    legs = sg.classify_participant_legs("99", arm="MS", mas_by_leg={}, a0_by_leg={})
    assert sg.participant_level(legs).level == sg.UNKNOWN


def test_control_participant_is_characterised_without_any_recordings():
    """The whole point of the control-by-recruitment rule: P7 has no left-leg
    data anywhere, and must still come out characterised."""
    legs = sg.classify_participant_legs("7", arm="Control", mas_by_leg={}, a0_by_leg={})
    assert sg.participant_level(legs).level == sg.NON_SPASTIC


# ── MAS components fill in for a pending overall grade ───────────────────────

def test_components_are_used_when_the_overall_grade_is_pending():
    """P17: overall grade pending, right-leg flexion scored 1. That is real
    clinical evidence and leaves the participant characterised instead of
    unknown."""
    lab = sg.classify_leg("17", "right", arm="MS", mas_by_leg={},
                          mas_components={("17", "right"): ("1", "0")})
    assert lab.level == sg.SPASTIC
    assert lab.source == sg.SRC_CLINICAL_COMPONENT
    assert "pending" in lab.detail


def test_components_all_zero_are_non_spastic():
    lab = sg.classify_leg("17", "left", arm="MS", mas_by_leg={},
                          mas_components={("17", "left"): ("0", "0")})
    assert (lab.level, lab.source) == (sg.NON_SPASTIC, sg.SRC_CLINICAL_COMPONENT)


def test_components_never_override_an_overall_grade():
    """P15 left is graded 0 overall with flexion 1+. The clinician's summary
    judgement stands; the components must not quietly flip it."""
    lab = sg.classify_leg("15", "left", arm="MS",
                          mas_by_leg={("15", "left"): "0"},
                          mas_components={("15", "left"): ("1+", "0")})
    assert (lab.level, lab.source) == (sg.NON_SPASTIC, sg.SRC_CLINICAL)


def test_components_outrank_the_a0_proxy():
    lab = sg.classify_leg("17", "right", arm="MS", mas_by_leg={},
                          mas_components={("17", "right"): ("1", "0")},
                          a0_deg=70.0)
    assert lab.source == sg.SRC_CLINICAL_COMPONENT


def test_either_component_positive_means_spastic():
    """Tone in extension only is still tone."""
    assert sg.component_level(("0", "2")) == sg.SPASTIC
    assert sg.component_level(("1+", "")) == sg.SPASTIC


def test_blank_components_carry_no_verdict():
    assert sg.component_level(("", "")) is None
    assert sg.component_level(None) is None
    assert sg.component_level((sg.PENDING_MAS_GRADE, "")) is None


def test_load_mas_components_skips_rows_with_no_components(tmp_path):
    p = tmp_path / "mas.csv"
    p.write_text(
        "participant,leg,condition,mas_grade,mas_flexion,mas_extension\n"
        "17,right,pre,-1,1,0\n"
        "13,left,pre,1,,\n",
        encoding="utf-8")
    got = sg.load_mas_components_by_leg(str(p))
    assert got == {("17", "right"): ("1", "0")}, got


# ── diagnosis must not drive the label ───────────────────────────────────────

def test_ms_participant_with_mas_zero_is_non_spastic():
    """An MS diagnosis does not imply spasticity. 11 of this cohort's MS legs
    have none, and they belong in the non-spastic group beside the controls --
    grouping on the chart instead of on tone is the thing this module exists
    to stop."""
    lab = sg.classify_leg("5", "left", arm="MS", mas_by_leg={("5", "left"): "0"})
    assert lab.level == sg.NON_SPASTIC


def test_ms_participant_with_normal_amplitude_is_non_spastic():
    """Same, via the derived path: no clinical MAS, healthy swing."""
    lab = sg.classify_leg("18", "left", arm="MS", mas_by_leg={}, a0_deg=58.4)
    assert lab.level == sg.NON_SPASTIC


@pytest.mark.parametrize("arm", ["MS", "Stroke", "Excluded", "Unclassified", None])
def test_only_control_gets_a_label_from_diagnosis_alone(arm):
    """Control is the ONE diagnosis that supplies a label by itself, and only
    because unaffected controls are non-spastic by recruitment. Every other
    diagnosis with no MAS and no swing must come back UNKNOWN rather than
    inheriting a level from its chart."""
    lab = sg.classify_leg("x", "left", arm=arm, mas_by_leg={}, a0_deg=None)
    assert lab.level == sg.UNKNOWN, f"{arm} leaked a label from diagnosis"


def test_a_control_with_a_spastic_mas_is_not_forced_non_spastic():
    """Recruitment is an assumption, and an assessment overrides it in both
    directions."""
    lab = sg.classify_leg("6", "right", arm="Control",
                          mas_by_leg={("6", "right"): "2"})
    assert lab.level == sg.SPASTIC
