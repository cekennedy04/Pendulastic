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
