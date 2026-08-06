import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

from pendulastic.hygiene.deadcode import parse_vulture_output, run_vulture

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
    assert captured_cmd["cmd"] == ["vulture", ".", ".vulture_whitelist.py"]


def test_run_vulture_flags_missing_whitelist(tmp_path: Path):
    captured_cmd = {}

    def fake_runner(cmd):
        captured_cmd["cmd"] = cmd
        return SAMPLE_OUTPUT

    result = run_vulture(tmp_path, runner=fake_runner)

    assert result.whitelist_missing is True
    assert captured_cmd["cmd"] == ["vulture", "."]
    assert len(result.high_confidence) == 1
    assert len(result.low_confidence) == 1
