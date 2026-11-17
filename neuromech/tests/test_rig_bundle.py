"""The rig-PC bundler's own checks (neuromech.rig_bundle): what it lets into library/, and what it refuses to ship.

The full build (library + Designer + zip) is exercised by running `python -m neuromech.rig_bundle`; these are the
pieces that decide safety and privacy, without building anything.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import rig_bundle as RB  # noqa: E402


def _file(sp, fs=1000.0):
    sp = np.asarray(sp, float)
    return np.arange(len(sp)) / fs, sp


def test_raw_check_refuses_nan_peak_and_rate():
    t, sp = _file(np.r_[0, 5 * np.sin(np.linspace(0, 6, 3000))])
    assert RB.raw_verdict(t, sp) is None
    bad = sp.copy(); bad[100] = np.nan
    assert "not finite" in RB.raw_verdict(t, bad)
    assert "over 40" in RB.raw_verdict(t, sp * 9)                      # 45 deg: over the owner's 40
    step = np.r_[np.zeros(500), np.full(500, 7.0)]                       # 7 deg in one 10 ms write = 700 deg/s
    assert "per 10 ms write" in RB.raw_verdict(*_file(step))


def test_sync_pulses_alone_pass_the_rate_limit():
    step = np.r_[np.zeros(500), np.full(80, 5.0), np.zeros(500)]         # motor_reference's 5 deg square pulse
    t, sp = _file(step)
    assert RB.per_write_rate(t, sp) == pytest.approx(500.0)
    assert RB.raw_verdict(t, sp) is None


@pytest.mark.parametrize("ret, want", [
    (None, None), (True, None), ("", None), ("too fast", "too fast"), (False, "refused by rig_guard"),
    ({"ok": True}, None), ({"ok": False, "reasons": ["a", "b"]}, "a; b"),
])
def test_guard_verdict_reads_the_usual_shapes(ret, want):
    assert RB.guard_verdict(lambda t, sp: ret, [0, 1], [0, 0]) == want


def test_guard_verdict_refuses_to_guess_an_unknown_shape():
    with pytest.raises(SystemExit):
        RB.guard_verdict(lambda t, sp: {"peak": 3}, [0, 1], [0, 0])


def test_guard_verdict_records_the_predicted_peak():
    info = {}
    ret = {"ok": False, "reasons": ["predicted 44 deg"], "predicted_peak_deg": 44.04, "scale_needed": 0.9}
    assert RB.guard_verdict(lambda t, sp: ret, [0, 1], [0, 0], info) == "predicted 44 deg"
    assert info == {"predicted_peak_deg": 44.0, "scale_needed": 0.9}


def _preamble_file(a0=55.0):
    import motor_reference as mr
    t, sp = mr.preamble(a0_deg=a0)
    swing = a0 * np.cos(np.linspace(0, 20, 5000)) * np.exp(-np.linspace(0, 3, 5000))
    sp = np.r_[sp, swing]
    return np.arange(len(sp)) / 1000.0, sp


def test_scaled_render_keeps_the_sync_pulses_and_scales_the_rest():
    t, sp = _preamble_file()
    i = RB.sync_end_index()
    out = RB.scaled_render(t, sp, 0.70)
    assert np.array_equal(out[:i], sp[:i]) and np.abs(out[:i]).max() == 5.0     # pulses unchanged
    assert np.allclose(out[i:], 0.70 * sp[i:])
    assert np.abs(out).max() == pytest.approx(0.70 * 55.0)                      # 38.5: under the 40 deg limit
    assert RB.raw_verdict(t, out) is None and "over 40" in RB.raw_verdict(t, sp)
    assert out[0] == 0 and np.abs(np.diff(out[i - 2:i + 2])).max() < 1e-9       # no step where scaling starts


def test_scaled_render_refuses_a_file_without_the_preamble():
    t, sp = _file(np.r_[0, np.full(3000, 10.0)])
    with pytest.raises(ValueError):
        RB.scaled_render(t, sp, 0.70)


def test_privacy_scan_stops_a_personal_path_or_participant_folder(tmp_path):
    (tmp_path / "ok.txt").write_text("copy logs here\r\n")
    RB.privacy_scan(str(tmp_path))
    (tmp_path / "bad.txt").write_text("see C:\\Users\\someone\\Desktop and Control_7\n")
    with pytest.raises(SystemExit):
        RB.privacy_scan(str(tmp_path))


def test_scripts_are_copied_crlf_and_ascii(tmp_path):
    src = tmp_path / "a.bat"; src.write_bytes(b"@echo off\nrem x\r\ncall y\n")
    RB.crlf_copy(str(src), str(tmp_path / "b.bat"))
    b = (tmp_path / "b.bat").read_bytes()
    assert b.count(b"\n") == b.count(b"\r\n") == 3


def test_manifest_lists_every_file_with_its_hash(tmp_path):
    (tmp_path / "sub").mkdir(); (tmp_path / "sub" / "x.csv").write_text("0,0\n")
    files = RB.write_manifest(str(tmp_path), dict(source_commit="abc"))
    assert [f["path"] for f in files] == ["sub/x.csv"]
    assert files[0]["sha256"] == RB.sha256(str(tmp_path / "sub" / "x.csv"))
    assert "abc" in (tmp_path / "MANIFEST.txt").read_text()
