import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from pendulastic.hygiene.deadcode import (
    DEFAULT_EXCLUDE_PATTERNS,
    VULTURE_TIMEOUT_SECONDS,
    parse_vulture_output,
    run_vulture,
)

try:
    import vulture as _vulture_module  # noqa: F401
    _VULTURE_AVAILABLE = True
except ImportError:
    _VULTURE_AVAILABLE = False

SAMPLE_OUTPUT = (
    "align_and_calibrate.py:42: unused function 'old_helper' (90% confidence)\n"
    "calibrate.py:10: unused variable 'debug_flag' (65% confidence)\n"
    "not a vulture line, ignore me\n"
)


def test_parse_vulture_output_splits_by_confidence():
    high, low = parse_vulture_output(SAMPLE_OUTPUT, min_confidence=80)

    assert len(high) == 1
    assert high[0].confidence == 90
    assert "old_helper" in high[0].description

    assert len(low) == 1
    assert low[0].confidence == 65
    assert "debug_flag" in low[0].description


def test_parse_vulture_output_empty_string_yields_no_findings():
    high, low = parse_vulture_output("", min_confidence=80)
    assert high == []
    assert low == []


def test_run_vulture_passes_whitelist_when_present(tmp_path: Path):
    (tmp_path / ".vulture_whitelist.py").write_text("_.dummy\n")
    captured_cmd = {}

    def fake_runner(cmd):
        captured_cmd["cmd"] = cmd
        return ""

    result = run_vulture(tmp_path, runner=fake_runner)

    assert result.whitelist_missing is False
    assert captured_cmd["cmd"] == [
        "vulture",
        ".",
        ".vulture_whitelist.py",
        "--exclude",
        DEFAULT_EXCLUDE_PATTERNS,
    ]


def test_run_vulture_flags_missing_whitelist(tmp_path: Path):
    captured_cmd = {}

    def fake_runner(cmd):
        captured_cmd["cmd"] = cmd
        return SAMPLE_OUTPUT

    result = run_vulture(tmp_path, runner=fake_runner)

    assert result.whitelist_missing is True
    assert captured_cmd["cmd"] == ["vulture", ".", "--exclude", DEFAULT_EXCLUDE_PATTERNS]
    assert len(result.high_confidence) == 1
    assert len(result.low_confidence) == 1
    assert result.failed is False
    assert result.error is None


def test_default_runner_invokes_sys_executable_module_vulture(tmp_path: Path):
    """
    Regression test: the default runner must shell out to
    `sys.executable -m vulture`, not a bare `vulture` on PATH (which may not
    resolve inside the venv). This patches subprocess.run itself so it can't
    be fooled by a test that only checks the abstract cmd-list construction.
    """
    captured = {}

    class FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeCompletedProcess()

    with patch("subprocess.run", side_effect=fake_run):
        run_vulture(tmp_path)

    assert captured["argv"][:3] == [sys.executable, "-m", "vulture"]
    assert "--exclude" in captured["argv"]
    assert DEFAULT_EXCLUDE_PATTERNS in captured["argv"]
    assert captured["kwargs"].get("timeout") == VULTURE_TIMEOUT_SECONDS


@pytest.mark.skipif(not _VULTURE_AVAILABLE, reason="vulture not installed")
def test_default_runner_real_vulture_accepts_whitelist_and_exclude_together(tmp_path: Path):
    """
    Regression test for a real bug: vulture's argparse requires all
    positional PATH arguments (the scan target and, when present, the
    whitelist file) to appear together, before any options.
    `vulture . --exclude X whitelist.py` fails with "unrecognized
    arguments: whitelist.py" even though `vulture . whitelist.py --exclude
    X` parses fine. The fake-runner tests above never invoke the real
    vulture CLI, so they can't catch an argparse ordering bug like this -
    this test does, against a real tiny file, exercising the actual
    default runner and the real vulture binary end to end.
    """
    (tmp_path / ".vulture_whitelist.py").write_text("_.dummy\n")
    (tmp_path / "sample.py").write_text("def unused_function():\n    pass\n")

    result = run_vulture(tmp_path)

    assert result.failed is False, result.error
    assert result.error is None


def test_default_runner_marks_failed_on_timeout(tmp_path: Path):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 0))

    with patch("subprocess.run", side_effect=fake_run):
        result = run_vulture(tmp_path)

    assert result.failed is True
    assert result.high_confidence == []
    assert result.low_confidence == []
    assert "timed out" in result.error


def test_default_runner_marks_failed_on_nonzero_error_exit(tmp_path: Path):
    class FakeCompletedProcess:
        returncode = 1
        stdout = ""
        stderr = "vulture: error: something went wrong\n"

    def fake_run(argv, **kwargs):
        return FakeCompletedProcess()

    with patch("subprocess.run", side_effect=fake_run):
        result = run_vulture(tmp_path)

    assert result.failed is True
    assert "something went wrong" in result.error
    assert result.high_confidence == []
    assert result.low_confidence == []


def test_default_runner_treats_dead_code_found_exit_code_as_success(tmp_path: Path):
    """vulture exits 3 (ExitCode.DeadCode) when it finds unused code - this is
    a successful run, not a failure."""
    class FakeCompletedProcess:
        returncode = 3
        stdout = "align_and_calibrate.py:42: unused function 'old_helper' (90% confidence)\n"
        stderr = ""

    def fake_run(argv, **kwargs):
        return FakeCompletedProcess()

    with patch("subprocess.run", side_effect=fake_run):
        result = run_vulture(tmp_path)

    assert result.failed is False
    assert result.error is None
    assert len(result.high_confidence) == 1
