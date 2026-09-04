"""The web API's scoring bridge -- U1.

This module had no test file, which is how it kept a defect on both sides of
one field: `AnalysisResponse.mas_estimate` was typed `float` and annotated
"integer MAS value", while `pt_score_bridge.compute()` filled it by calling the
UNGATED `pt_to_mas`. So the endpoint served grades on swings too collapsed to
support one, and would have failed validation outright on any trial grading
"1+", which is neither an integer nor a float.

These are about the contract, not the arithmetic -- the scoring itself is
covered by tests/test_pt_score.py.
"""
import pytest

pytest.importorskip("pydantic", reason="web API models need pydantic")


def _mas_field():
    from web.api.models.trial import AnalysisResponse
    fields = getattr(AnalysisResponse, "model_fields", None) or \
        getattr(AnalysisResponse, "__fields__")
    return fields["mas_estimate"]


def test_the_grade_field_can_hold_the_labels_the_scale_actually_has():
    # "1+" is a real MAS grade and is not a number. Typed as float, this raised
    # at response time on exactly the mid-range trials the study cares about.
    # Checked on the annotation rather than by building a whole response, so
    # this does not break when a sibling sub-model gains a field.
    import pendulastic_pt_score as p
    ann = str(getattr(_mas_field(), "annotation", _mas_field()))
    assert "str" in ann, f"mas_estimate must be a string label, got {ann}"
    assert "float" not in ann, f"mas_estimate must not be numeric, got {ann}"
    assert "1+" in {label for _thresh, label in p._MAS}


def test_a_refused_grade_is_representable_without_inventing_one():
    # A refusal is not an error and not a grade. The field has to hold
    # "no verdict", so it must be optional.
    f = _mas_field()
    ann = str(getattr(f, "annotation", f))
    assert "Optional" in ann or "None" in ann, (
        f"mas_estimate must be able to say 'refused', got {ann}")

    from web.api.models.trial import AnalysisResponse
    fields = getattr(AnalysisResponse, "model_fields", None) or \
        getattr(AnalysisResponse, "__fields__")
    assert "mas_refusal_reason" in fields, (
        "a refusal has to carry why, or the caller shows a blank")


def test_the_bridge_grades_through_the_gate_not_around_it():
    # The field was always named mas_estimate; it just did not call it.
    # Looks for the CALL, not the word: the docstring names pt_to_mas to say
    # why it is not used, and that explanation is worth keeping.
    import inspect
    import re
    from web.api.services import pt_score_bridge
    src = inspect.getsource(pt_score_bridge)
    assert re.search(r"\bmas_estimate\s*\(", src), (
        "the bridge must grade through the gated entry point")
    assert not re.search(r"\bpt_to_mas\s*\(", src), (
        "the bridge must not call the ungated mapping -- a grade served from "
        "this endpoint reaches a person")
