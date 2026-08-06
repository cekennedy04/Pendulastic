import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import pytest

from pendulastic.hygiene.models import Category
from pendulastic.hygiene.cruft import classify_untracked


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


def test_data_directory_files_are_never_deletable(repo: Path):
    data_dir = repo / "data"
    data_dir.mkdir()
    (data_dir / "trial1_imu.csv").write_text("t,x,y,z\n")
    optitrack_dir = repo / "OptiTrack_Recordings"
    optitrack_dir.mkdir()
    (optitrack_dir / "run1.csv").write_text("frame,x\n")

    findings = classify_untracked(repo)

    assert all(f.category == Category.GITIGNORE_CANDIDATE for f in findings)
    assert not any(f.category == Category.SAFE_TO_DELETE for f in findings)


def test_regenerable_junk_is_safe_to_delete(repo: Path):
    (repo / "calibrate_out.txt").write_text("log\n")
    (repo / "app_pid.txt").write_text("1234\n")
    pycache = repo / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.cpython-313.pyc").write_bytes(b"\x00")

    findings = classify_untracked(repo)

    assert len(findings) == 3
    assert all(f.category == Category.SAFE_TO_DELETE for f in findings)


def test_vendored_zip_and_deprecated_need_review(repo: Path):
    (repo / "STCFormer-main.zip").write_bytes(b"PK\x03\x04")
    deprecated = repo / "_deprecated"
    deprecated.mkdir()
    (deprecated / "old_script.py").write_text("# old\n")

    findings = classify_untracked(repo)

    assert len(findings) == 2
    assert all(f.category == Category.NEEDS_REVIEW for f in findings)


def test_unknown_pattern_defaults_to_needs_review(repo: Path):
    (repo / "mystery_file.dat").write_bytes(b"\x00\x01")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.NEEDS_REVIEW


def test_nested_protected_directory_never_deletable(repo: Path):
    # "experiments/" itself must already be tracked, otherwise git collapses
    # the whole wholly-untracked subtree to a single "experiments/" entry
    # under --untracked-files=normal, and training_data/ never appears as its
    # own path segment.
    experiments = repo / "experiments"
    experiments.mkdir()
    (experiments / "notes.md").write_text("tracked placeholder\n")
    _git(repo, "add", "experiments/notes.md")
    _git(repo, "commit", "-m", "add experiments dir")

    training_data = experiments / "training_data"
    training_data.mkdir()
    (training_data / "run_out.txt").write_text("log\n")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.GITIGNORE_CANDIDATE
    assert Category.SAFE_TO_DELETE not in [f.category for f in findings]


def test_wholly_untracked_directory_with_many_files_yields_one_finding(repo: Path):
    """
    Regression test: a directory with hundreds/thousands of untracked files
    (e.g. training_data/ with 19,840 files in the real repo) must collapse to
    ONE finding, not one per file. This is what --untracked-files=normal
    buys us over --untracked-files=all.
    """
    training_data = repo / "training_data"
    training_data.mkdir()
    for i in range(50):
        (training_data / f"sample_{i}.csv").write_text("t,x,y,z\n")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.GITIGNORE_CANDIDATE
    assert "training_data" in findings[0].command


def test_nested_protected_directory_command_targets_matched_segment_not_top(repo: Path):
    """
    Regression test: the generated gitignore command must target the segment
    that actually matched a protected prefix (training_data/), not blindly
    the first path segment (experiments/) - gitignoring the wrong, much
    broader directory would hide unrelated experiment files too.
    """
    experiments = repo / "experiments"
    experiments.mkdir()
    (experiments / "notes.md").write_text("tracked placeholder\n")
    _git(repo, "add", "experiments/notes.md")
    _git(repo, "commit", "-m", "add experiments dir")

    training_data = experiments / "training_data"
    training_data.mkdir()
    (training_data / "run.csv").write_text("t,x\n")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].command == "echo experiments/training_data/ >> .gitignore"


def test_top_level_file_matching_protected_prefix_has_no_trailing_slash(repo: Path):
    """
    Regression test: when the protected-prefix match is a top-level FILE
    (not a directory), the generated gitignore command must NOT append a
    trailing slash - a trailing slash makes the gitignore entry directory-only
    syntax, so it would never actually match the file and the finding would
    recur every night forever.
    """
    (repo / "OptiTrack_summary.csv").write_text("frame,x\n")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.GITIGNORE_CANDIDATE
    assert findings[0].command == "echo OptiTrack_summary.csv >> .gitignore"


def test_exact_match_prefixes_do_not_over_match(repo: Path):
    """
    Regression test: 'data', 'models', 'Recordings', 'training_data' must be
    exact-match only (not startswith) so names like 'database' or
    'models_v2_notes.md' aren't swept up as protected. OptiTrack_ remains a
    genuine prefix match.
    """
    (repo / "database").mkdir()
    (repo / "database" / "cache.db").write_text("x\n")
    (repo / "models_v2_notes.md").write_text("notes\n")

    findings = classify_untracked(repo)

    assert all(f.category != Category.GITIGNORE_CANDIDATE for f in findings)


def test_command_quotes_paths_with_apostrophes(repo: Path):
    import shlex

    (repo / "mystery's_file.dat").write_bytes(b"\x00\x01")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.NEEDS_REVIEW
    quoted = shlex.quote("mystery's_file.dat")
    assert quoted in findings[0].command
    # Naive single-quoting (f"'{path}'") would break here - make sure that's
    # not what's happening.
    assert f"'mystery's_file.dat'" not in findings[0].command
