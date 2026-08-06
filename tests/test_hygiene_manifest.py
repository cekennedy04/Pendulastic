import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from pendulastic.hygiene.manifest import render_manifest, render_low_confidence_section
from pendulastic.hygiene.models import Category, Finding


def _finding(category, desc="desc", cmd="cmd"):
    return Finding(category=category, description=desc, command=cmd, source="test")


def test_render_manifest_numbers_items_sequentially():
    findings = [
        _finding(Category.SAFE_TO_DELETE, "first"),
        _finding(Category.NEEDS_REVIEW, "second"),
    ]

    rendered = render_manifest(findings, report_date="2026-08-06")

    assert "1. `[Safe to Delete]` first" in rendered.markdown
    assert "2. `[Needs Review]` second" in rendered.markdown
    assert rendered.next_item_number == 3


def test_render_manifest_inserts_whitelist_item_first_when_missing():
    findings = [_finding(Category.SAFE_TO_DELETE, "first")]

    rendered = render_manifest(findings, report_date="2026-08-06", whitelist_missing=True)

    assert "1. `[Needs Review]` No `.vulture_whitelist.py`" in rendered.markdown
    assert "2. `[Safe to Delete]` first" in rendered.markdown
    assert rendered.next_item_number == 3


def test_render_manifest_empty_findings_no_whitelist_gap():
    rendered = render_manifest([], report_date="2026-08-06")
    assert rendered.next_item_number == 1


def test_render_low_confidence_section_lists_bullets():
    findings = [_finding(Category.NEEDS_REVIEW, "maybe unused")]
    section = render_low_confidence_section(findings)
    assert "## Low-confidence dead-code findings" in section
    assert "- maybe unused" in section


def test_render_low_confidence_section_empty_is_blank():
    assert render_low_confidence_section([]) == ""
