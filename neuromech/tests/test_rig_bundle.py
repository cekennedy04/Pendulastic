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


def test_the_merged_guard_is_used_at_the_owners_limit():
    fn, line = RB.guard_status()
    assert fn is not None and "check_trajectory(limit_deg=40)" in line
    t, sp = _preamble_file()
    assert RB.guard_verdict(fn, t, RB.scaled_render(t, sp, 0.70)) is None      # 38.5 deg, slow swing
    info = {}
    why = RB.guard_verdict(fn, t, sp, info)                                     # 55 deg at 1:1
    assert why and info["raw_peak_deg"] == 55.0 and info["predicted_peak_deg"] >= 55.0
    nan = sp.copy(); nan[3000] = np.nan
    assert RB.guard_verdict(fn, t, nan)


def test_verify_library_reads_back_and_refuses_a_bad_file(tmp_path):
    lib = tmp_path / "library_x0.70"; lib.mkdir()
    t, sp = _preamble_file()
    np.savetxt(lib / "ok.csv", np.column_stack([t, RB.scaled_render(t, sp, 0.70)]), fmt="%.4f", delimiter=",")
    (lib / "INDEX.csv").write_text("file\r\n")
    assert RB.verify_library(str(tmp_path)) == 1
    np.savetxt(lib / "bad.csv", np.column_stack([t, sp]), fmt="%.4f", delimiter=",")
    with pytest.raises(SystemExit):
        RB.verify_library(str(tmp_path))


def test_start_here_formats_both_ways():
    for motor_ok in (True, False):
        txt = RB.START_HERE.format(date="d", sha="s" * 40, sha7="s" * 7, score_id="x", model_hash="m", n_lib=1,
                                   n_out=2, n_sc=3, n_sc_out=4, scale=0.70, n_rc=3, guard="g",
                                   guard_banner=RB.GUARD_TEXT[motor_ok][0].format(scale=0.70),
                                   unvalidated_guard=RB.GUARD_TEXT[motor_ok][1])
        assert "RIG_CHECKLIST.txt" in txt and "Ctrl+Break" in txt and "HOLDING.flag" in txt
        assert ("DO NOT PLAY" in txt) is (not motor_ok)
        assert all(ord(c) < 128 for c in txt) and "{" not in txt


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


def test_the_chirp_is_left_out_with_the_guards_numbers():
    from neuromech import session as SS
    t, sp, _, _, _ = SS.chirp_command()
    speed = RB.predicted_arm_speed(t, sp)
    assert speed > 900                                    # rings up at the 6.35 Hz resonance
    info = {}
    RB.guard_verdict(RB.guard_status()[0], t, sp, info)
    why = RB.LEFT_OUT_BY_OWNER["R-chirp"].format(peak=float(np.abs(sp).max()), pred=info["predicted_peak_deg"], speed=speed)
    assert why.startswith("owner 2026-11-17: left out; commands 8 deg") and "deg/s predicted arm speed" in why


def test_predicted_arm_speed_is_small_for_a_slow_file():
    t = np.arange(0, 20.0005, 0.001); sp = 5 * np.sin(2 * np.pi * 0.2 * t) * np.sin(np.pi * t / 20) ** 2
    assert RB.predicted_arm_speed(t, sp) < 20


def test_bench_log_records_the_loop_settings():
    assert {"P", "I", "D", "output_range", "encoder_file"} <= set(RB.BENCH_LOG_COLS)


# ── the re-centred 1:1 render (owner decision 2026-11-18) ─────────────────────────────────────────────
def _axis_file(lam):
    """(t, sp, rest, release_s, expected prm, expected est) of the severity axis at lam, as the library builds it."""
    from neuromech import build as B, scenarios as S
    sc = S.Scenario(f"S-lam{lam:g}", "S", "", S.severity(lam))
    t, th, rest, _ = B.render(sc)
    prm, est = B.score(t, th, sc)
    ts, sp, _, _, _, rel = B.rig_setpoint(t, th, rest, sc)
    return ts, sp, rest, rel, prm, est


def test_recentred_render_keeps_the_pulses_and_shifts_the_hold_and_swing_by_a_constant():
    import motor_reference as mr
    t, sp = _preamble_file()
    i = RB.sync_end_index(); n_pre = len(mr.preamble(a0_deg=55.0)[1]); lift_end = n_pre - int(mr.PRE_ROLL_S * 1000)
    out = RB.recentred_render(t, sp, 28.0)
    assert np.array_equal(out[:i], sp[:i]) and np.abs(out[:i]).max() == 5.0 and out[0] == 0     # pulses, zero start
    assert np.allclose(out[lift_end:], sp[lift_end:] - 28.0, atol=1e-12)                        # a constant after the lift
    assert out[n_pre - 1] == pytest.approx(27.0)                                                # the hold: 55 - 28
    assert np.abs(np.diff(out)[i:]).max() <= np.abs(np.diff(sp)[i:]).max() + 1e-9               # the offset adds no step
    assert np.allclose(RB.undo_recentre(t, out, 28.0), sp, atol=1e-12)
    with pytest.raises(ValueError):
        RB.recentred_render(*_file(np.r_[0, np.full(12000, 10.0)]), 28.0)


@pytest.mark.parametrize("lam, offset", [(1.0, 30.0), (1.2, 28.0), (1.4, 28.0)])
def test_recentred_severity_axis_passes_the_unchanged_guard_and_scores_the_1_to_1_value(lam, offset, tmp_path):
    """Lambda 1.0, 1.2 and 1.4 at FULL SIZE: refused 1:1 (55 deg release), inside the same 40 deg guard once the
    hold and swing are shifted by the whole-degree offset that centres them. The score does not depend on the
    offset: scored as a rig trace WITHOUT undoing it, with it undone, and through match_trials.delivered (a
    perfect encoder), the PT7, band and A0 are the 1:1 library's expected values -- graded, where the 0.70 render
    is refused for excursion."""
    from neuromech import match_trials as MT, rig_guard as G, session as SS
    t, sp, rest, rel, prm, est = _axis_file(lam)
    fn, _ = RB.guard_status()
    assert RB.guard_verdict(fn, t, sp) is not None
    assert RB.recentre_offset(t, sp) == offset
    rc = RB.recentred_render(t, sp, offset)
    info = {}
    assert RB.guard_verdict(fn, t, rc, info) is None and info["predicted_peak_deg"] < 30.0
    assert G.HARD_LIMIT_DEG == 40.0 and RB.LIMIT_DEG == 40.0                                    # the guard is the same guard
    fs = 1.0 / float(t[1] - t[0])
    body = rc[RB.recentre_shape(len(rc), fs) == 1.0]
    # the lift is 25-27 deg in 4 s: UNDER the app's 0.3 rad/s stillness guard (stillness.rs), where the 1:1 lift is
    # not. Known problem, in the folder's README and the results note; this holds the number they quote.
    lift = (RB.recentre_shape(len(rc), fs) > 0) & (RB.recentre_shape(len(rc), fs) < 1)
    assert 0.20 < np.radians(np.abs(np.diff(rc[lift])).max() * fs) < 0.23 < 0.3 < np.radians(np.abs(np.diff(sp[lift])).max() * fs)
    assert abs(body.max() + body.min()) <= 1.0 and 24.0 < body.max() < 28.0                     # centred, about +/-27
    assert est["interpretable"] and est["mas"] == "1"
    for trace in (rc, RB.undo_recentre(t, rc, offset), sp):
        p, e = SS._score_trace(t, trace, rest, rel, 3.0)
        assert e["interpretable"] and e["mas"] == est["mas"]
        assert abs(e["pt7"] - est["pt7"]) < 2e-3 and abs(p["A0_deg"] - prm["A0_deg"]) < 0.05
    p0, e0 = SS._score_trace(t, sp, rest, rel, 3.0); p1, e1 = SS._score_trace(t, rc, rest, rel, 3.0)
    assert abs(e0["pt7"] - e1["pt7"]) < 1e-9 and abs(p0["A0_deg"] - p1["A0_deg"]) < 1e-9        # offset-invariant
    # the bench path for a library file: the player file as written, a log whose position IS the command
    from neuromech import ilc
    os.makedirs(tmp_path / "trial_files")
    ilc.write_player(str(tmp_path / "trial_files" / "T.csv"), t, rc)
    rig = np.loadtxt(tmp_path / "trial_files" / "T.csv", delimiter=",")
    z = np.zeros(len(rig))
    np.vstack([rig[:, 1], z, rig[:, 1], z, z, z, z]).T.astype(">f8").tofile(tmp_path / "log.bin")
    got = MT.delivered(str(tmp_path), os.path.join("trial_files", "T.csv"), str(tmp_path / "log.bin"))
    assert abs(got["enc_pt7"] - est["pt7"]) < 5e-3 and abs(got["enc_A0_deg"] - prm["A0_deg"]) < 0.1
    assert G.check_file(str(tmp_path / "trial_files" / "T.csv"), limit_deg=40.0)["ok"]


def _library_built():
    from neuromech import build as B
    return (os.path.exists(os.path.join(B.OUT, "scenario_manifest.csv"))
            and not B.provenance_mismatch(B.library_provenance(), B.provenance()))


@pytest.mark.skipif(not _library_built(), reason="neuromech/out not built for this code (python -m neuromech.build)")
def test_export_recentred_ships_only_the_owners_three_and_lists_what_else_would_fit(tmp_path):
    fn, _ = RB.guard_status()
    rows = RB.export_recentred(str(tmp_path / RB.RECENTRED_DIR), fn)
    by = {r["sid"]: r for r in rows}
    shipped = sorted(r["sid"] for r in rows if r["included"] == "yes")
    assert shipped == sorted(RB.RECENTRED_SIDS) == ["S-lam1", "S-lam1.2", "S-lam1.4"]
    files = sorted(f for f in os.listdir(tmp_path / RB.RECENTRED_DIR) if f not in ("INDEX.csv", "README.txt"))
    assert files == sorted(s + "_recentred.csv" for s in RB.RECENTRED_SIDS)
    assert [by[s]["offset_deg"] for s in ("S-lam1", "S-lam1.2", "S-lam1.4")] == ["30.0", "28.0", "28.0"]
    assert RB.verify_library(str(tmp_path)) == 3                                                # read back as the players do
    assert by["A-P0"]["fits_recentred"] == "no" and by["A-P0"]["included"].startswith("NO: ")   # a passive swing spans 84 deg
    assert by["A-P1p"]["fits_recentred"] == "yes" and "not shipped" in by["A-P1p"]["included"]
    label = open(tmp_path / RB.RECENTRED_DIR / "README.txt").read()
    assert "RE-CENTRED RENDER" in label and "offset_deg" in label and all(ord(c) < 128 for c in label)
    assert "START THE PHONE CAPTURE DURING THE 3 s HOLD" in label and "align the phone to the encoder by the release" in label
    assert "Until this is settled" not in label and "28 deg or more" in label
    assert all(0.20 < float(by[s]["lift_peak_rad_s"]) < 0.23 for s in RB.RECENTRED_SIDS)
