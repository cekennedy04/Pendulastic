# tests/test_run_hygiene_audit.py
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pytest

from run_hygiene_audit import build_mechanical_report


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
