# tests/test_run_hygiene_audit.py
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pytest

from pendulastic.hygiene.deadcode import DeadCodeResult
from run_hygiene_audit import build_mechanical_report, detect_default_branch


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


def test_build_mechanical_report_combines_all_phases(repo: Path, tmp_path: Path):
    worktree_path = tmp_path / "wt-merged"
    _git(repo, "worktree", "add", "-b", "merged-branch", str(worktree_path), "main")
    (repo / "calibrate_out.txt").write_text("log\n")

    def fake_vulture_runner(cmd):
        return "align_and_calibrate.py:42: unused function 'old_helper' (90% confidence)\n"

    report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "=== MECHANICAL FINDINGS (Phases 1-2) ===" in report
    assert "merged-branch" in report
    assert "calibrate_out.txt" in report
    assert "old_helper" in report
    assert "No `.vulture_whitelist.py` found" in report
    assert "=== NEXT_ITEM_NUMBER: 5 ===" in report


def test_build_mechanical_report_empty_repo_has_only_whitelist_prompt(repo: Path):
    def fake_vulture_runner(cmd):
        return ""

    report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "No `.vulture_whitelist.py` found" in report
    assert "=== NEXT_ITEM_NUMBER: 2 ===" in report


def test_detect_default_branch_falls_back_to_local_master(tmp_path: Path):
    """
    Regression test: repos whose default branch is "master" (no remote
    configured) must be detected correctly rather than assuming "main".
    """
    repo_root = tmp_path / "master-repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "master")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "README.md").write_text("hello\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")

    assert detect_default_branch(repo_root) == "master"


def test_build_mechanical_report_works_on_master_default_branch_repo(tmp_path: Path):
    """
    Regression test: prior to the fix, main_branch defaulted to the literal
    string "main" with no detection, so `git log main..branch` raised
    CalledProcessError (uncaught) on any repo using "master" - crashing the
    whole script with no report produced at all. This exercises the full
    build_mechanical_report path against a master-only repo end to end.
    """
    repo_root = tmp_path / "master-repo"
    repo_root.mkdir()
    _git(repo_root, "init", "-b", "master")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Test")
    (repo_root / "README.md").write_text("hello\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-m", "initial commit")

    worktree_path = tmp_path / "wt-merged"
    _git(repo_root, "worktree", "add", "-b", "merged-branch", str(worktree_path), "master")

    def fake_vulture_runner(cmd):
        return ""

    report = build_mechanical_report(repo_root, vulture_runner=fake_vulture_runner)

    assert "Phase 1 INCOMPLETE" not in report
    assert "merged-branch" in report


def test_build_mechanical_report_marks_phase2_incomplete_on_deadcode_failure(repo: Path):
    """
    Regression test: a vulture failure (timeout, crash, not installed) must
    not be silently swallowed as "zero findings" - the report must clearly
    mark Phase 2 incomplete with the error, per the design spec.
    """
    failed_result = DeadCodeResult(
        high_confidence=[],
        low_confidence=[],
        whitelist_missing=True,
        failed=True,
        error="vulture timed out after 300s",
    )

    with patch("run_hygiene_audit.run_vulture", return_value=failed_result):
        report = build_mechanical_report(repo)

    assert "Phase 2 INCOMPLETE: vulture timed out after 300s" in report


def test_build_mechanical_report_marks_phase1_incomplete_on_worktree_failure(repo: Path):
    """
    Regression test: an unexpected git failure during worktree/cruft
    classification must not crash the whole script and produce NO report -
    it must degrade gracefully to a report with a "Phase 1 INCOMPLETE" line.
    """
    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["git", "worktree", "list"])

    def fake_vulture_runner(cmd):
        return ""

    with patch("run_hygiene_audit.classify_worktrees", side_effect=boom):
        report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "Phase 1 INCOMPLETE" in report
    # The report must still be produced (not raise), and still include the
    # standard sections.
    assert "=== MECHANICAL FINDINGS (Phases 1-2) ===" in report
    assert "=== NEXT_ITEM_NUMBER:" in report


def test_build_mechanical_report_marks_phase1_incomplete_on_cruft_failure(repo: Path):
    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["git", "status"])

    def fake_vulture_runner(cmd):
        return ""

    with patch("run_hygiene_audit.classify_untracked", side_effect=boom):
        report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "Phase 1 INCOMPLETE" in report
    assert "=== NEXT_ITEM_NUMBER:" in report


def test_build_mechanical_report_marks_phase1_incomplete_on_worktree_value_error(repo: Path):
    """
    Regression test: worktrees.last_commit_age_days does int(output.strip())
    on git's `log --format=%ct` output, which raises ValueError on
    unexpected/empty output - this was actually hit once during
    development. Phase 1's try/except previously caught only
    subprocess.CalledProcessError, so a ValueError here would have crashed
    the whole report instead of degrading gracefully.
    """
    def boom(*args, **kwargs):
        raise ValueError("invalid literal for int() with base 10: ''")

    def fake_vulture_runner(cmd):
        return ""

    with patch("run_hygiene_audit.classify_worktrees", side_effect=boom):
        report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "Phase 1 INCOMPLETE" in report
    assert "=== NEXT_ITEM_NUMBER:" in report


def test_build_mechanical_report_marks_phase1_incomplete_on_cruft_os_error(repo: Path):
    """
    Regression test: if git is missing from PATH, subprocess.run raises
    FileNotFoundError (an OSError subclass), not CalledProcessError.
    Phase 1's try/except previously didn't catch this either.
    """
    def boom(*args, **kwargs):
        raise FileNotFoundError("git not found")

    def fake_vulture_runner(cmd):
        return ""

    with patch("run_hygiene_audit.classify_untracked", side_effect=boom):
        report = build_mechanical_report(repo, vulture_runner=fake_vulture_runner)

    assert "Phase 1 INCOMPLETE" in report
    assert "=== NEXT_ITEM_NUMBER:" in report
