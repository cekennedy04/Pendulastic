import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from pendulastic.hygiene.models import Category, Finding


def test_finding_stores_all_fields():
    finding = Finding(
        category=Category.SAFE_TO_DELETE,
        description="stale worktree",
        command="git worktree remove foo",
        source="Phase 1: Worktrees",
    )
    assert finding.category == Category.SAFE_TO_DELETE
    assert finding.description == "stale worktree"
    assert finding.command == "git worktree remove foo"
    assert finding.source == "Phase 1: Worktrees"
    assert finding.confidence is None


def test_category_values_match_report_tags():
    assert Category.SAFE_TO_DELETE.value == "Safe to Delete"
    assert Category.GITIGNORE_CANDIDATE.value == "Gitignore Candidate"
    assert Category.NEEDS_REVIEW.value == "Needs Review"
    assert Category.DOC_DRIFT_FIX.value == "Doc Drift Fix"
