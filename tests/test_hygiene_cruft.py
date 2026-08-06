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
    experiments = repo / "experiments"
    experiments.mkdir()
    training_data = experiments / "training_data"
    training_data.mkdir()
    (training_data / "run_out.txt").write_text("log\n")

    findings = classify_untracked(repo)

    assert len(findings) == 1
    assert findings[0].category == Category.GITIGNORE_CANDIDATE
    assert Category.SAFE_TO_DELETE not in [f.category for f in findings]
