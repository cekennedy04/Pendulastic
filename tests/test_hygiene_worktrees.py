import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pytest

from pendulastic.hygiene.models import Category
from pendulastic.hygiene.worktrees import classify_worktrees


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "main")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "README.md").write_text("hello\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")
    return repo_root


def test_merged_worktree_is_safe_to_delete(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-merged"
    _git(repo, "worktree", "add", "-b", "merged-branch", str(worktree_path), "main")

    findings = classify_worktrees(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.SAFE_TO_DELETE
    assert "merged-branch" in findings[0].description


def test_stale_unmerged_worktree_needs_review(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-stale"
    _git(repo, "worktree", "add", "-b", "stale-branch", str(worktree_path), "main")
    (worktree_path / "new_file.txt").write_text("wip\n")
    _git(worktree_path, "add", "new_file.txt")
    _git(worktree_path, "commit", "-m", "wip commit")
    commit_ts = int(_git(repo, "log", "-1", "--format=%ct", "stale-branch").strip())

    findings = classify_worktrees(repo, now=commit_ts + 20 * 86400)

    assert len(findings) == 1
    assert findings[0].category == Category.NEEDS_REVIEW
    assert "stale-branch" in findings[0].description


def test_active_unmerged_worktree_is_skipped(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-active"
    _git(repo, "worktree", "add", "-b", "active-branch", str(worktree_path), "main")
    (worktree_path / "new_file.txt").write_text("wip\n")
    _git(worktree_path, "add", "new_file.txt")
    _git(worktree_path, "commit", "-m", "wip commit")
    commit_ts = int(_git(repo, "log", "-1", "--format=%ct", "active-branch").strip())

    findings = classify_worktrees(repo, now=commit_ts + 1 * 86400)

    assert findings == []


def test_merged_worktree_with_slashed_branch_name(repo: Path, tmp_path: Path):
    """Regression test: branch names with slashes (e.g. feature/foo) should not be truncated."""
    worktree_path = tmp_path / "wt-feature-foo"
    _git(repo, "worktree", "add", "-b", "feature/foo", str(worktree_path), "main")

    findings = classify_worktrees(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.SAFE_TO_DELETE
    assert "feature/foo" in findings[0].description
    assert "feature/foo" in findings[0].command


def test_stale_unmerged_worktree_with_slashed_branch_name(repo: Path, tmp_path: Path):
    """Regression test: stale branches with slashes should be correctly identified."""
    worktree_path = tmp_path / "wt-bugfix-bar"
    _git(repo, "worktree", "add", "-b", "bugfix/bar", str(worktree_path), "main")
    (worktree_path / "new_file.txt").write_text("wip\n")
    _git(worktree_path, "add", "new_file.txt")
    _git(worktree_path, "commit", "-m", "wip commit")
    commit_ts = int(_git(repo, "log", "-1", "--format=%ct", "bugfix/bar").strip())

    findings = classify_worktrees(repo, now=commit_ts + 20 * 86400)

    assert len(findings) == 1
    assert findings[0].category == Category.NEEDS_REVIEW
    assert "bugfix/bar" in findings[0].description
    assert "bugfix/bar" in findings[0].command


def test_main_branch_worktree_never_flagged_when_running_from_linked_worktree(
    repo: Path, tmp_path: Path
):
    """
    Critical safety regression test: when classify_worktrees is called with a
    linked worktree as repo_root (not the main checkout), the actual main-branch
    worktree must never be flagged as Safe to Delete.

    Simulates: running the audit tool from a non-main worktree, as happened
    during live validation.
    """
    # Create a linked worktree on a non-main branch
    linked_wt_path = tmp_path / "wt-other"
    _git(repo, "worktree", "add", "-b", "other-branch", str(linked_wt_path), "main")
    (linked_wt_path / "file.txt").write_text("content\n")
    _git(linked_wt_path, "add", "file.txt")
    _git(linked_wt_path, "commit", "-m", "work on other-branch")

    # Call classify_worktrees from the linked worktree's path (not the main repo)
    findings = classify_worktrees(linked_wt_path)

    # Main-branch worktree must NEVER appear in findings, regardless of merge status
    branch_names = [
        finding.description
        for finding in findings
        if "branch main" in finding.description
    ]
    assert (
        len(branch_names) == 0
    ), f"Main-branch worktree must not be classified. Found: {branch_names}"

    # Verify the main repo path is not in any command suggestions either
    for finding in findings:
        assert (
            str(repo.resolve()) not in finding.command
        ), f"Main repo path must not appear in commands. Found in: {finding.command}"
