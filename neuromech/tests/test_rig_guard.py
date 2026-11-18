"""The motor playback safety guard (labview/player/RigGuard.cs, neuromech/rig_guard.py) and both players.

* rig_guard on its own: every refusal rule, the sync preamble passing, the plant numbers, scale_to_fit;
* the cross-player test: the same fixture files through Python, the C# guard the Designer compiles
  (GuardTest.exe check) and play_setpoints.ps1 (dry run) must give the same verdict, and C# and Python the
  same predicted peak;
* RigPlayer (C#) and play_setpoints.ps1 against a fake DataSocket: setpoint-read failure, operator limit
  above 40, the per-write backstop stopping a run, failed writes;
* the Designer's scale-down offer: every MAS preset at the default scale is over the limit, and the scaled
  design it offers passes the guard and scores (by the Python scorer) what the Designer says;
* check-log flags a sent log with guard events.
C#/PowerShell parts skip where csc v3.5 / Windows PowerShell are missing.
"""
import csv
import glob
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import rig_guard as G  # noqa: E402

NM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PLAYER = os.path.join(NM, "labview", "player")
DESIGNER = os.path.join(NM, "designer")
CSC = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Microsoft.NET", "Framework", "v3.5", "csc.exe")
POWERSHELL = shutil.which("powershell")
need_csc = pytest.mark.skipif(not os.path.exists(CSC), reason="needs the .NET 3.5 C# compiler (Windows)")
need_ps = pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell")


# ── fixtures: name -> (text, expected kind or "" for playable) ──────────────────
def _rows(t, sp):
    return "".join(f"{a:.4f},{b:.4f}\n" for a, b in zip(t, sp))


def _slow(amp, up=1.0, lead=0.2, fs=1000.0):
    t = np.arange(0, lead + 2 * up + 1e-9, 1 / fs)
    sp = np.where(t < lead, 0.0, np.where(t < lead + up, amp * (t - lead) / up, amp * np.clip((lead + 2 * up - t) / up, 0, None)))
    return t, sp


def _preamble_file(swing_amp=8.0):
    """The library's shape at 1 kHz: 0.2 s lead, 3 x 5 deg sync pulses 80 ms wide (0.25 s gaps), 1 s still,
    a 4 s minimum-jerk lift to 30 deg, a short hold, then a slow 1 Hz swing that decays."""
    fs = 1000.0; seg = [np.zeros(200)]
    for _ in range(3):
        seg += [np.full(80, 5.0), np.zeros(250)]
    seg.append(np.zeros(1000))
    u = np.linspace(0, 1, 4000); seg.append(30 * (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5)); seg.append(np.full(500, 30.0))
    ts = np.arange(3000) / fs; seg.append(30 - swing_amp + swing_amp * np.cos(2 * np.pi * ts) * np.exp(-ts))
    sp = np.concatenate(seg); return np.arange(len(sp)) / fs, sp


def _overshoot_file():
    """Inside +/-40 commanded and under 600 deg/s, but a 5.5 Hz wobble on a 34 deg hold rides past 40 on the rig."""
    fs = 1000.0; u = np.linspace(0, 1, 3000)
    lift = 34 * (10 * u ** 3 - 15 * u ** 4 + 6 * u ** 5)
    tw = np.arange(2000) / fs
    wob = 34 + 4.0 * np.sin(2 * np.pi * 5.5 * tw) * np.sin(np.pi * tw / 2.0)
    sp = np.concatenate([np.zeros(100), lift, wob, np.full(500, 34.0)]); return np.arange(len(sp)) / fs, sp


def fixtures():
    t, sp = _slow(10.0)
    ok_small = _rows(t, sp)
    fx = {
        "ok_small": (ok_small, ""),
        "ok_preamble": (_rows(*_preamble_file()), ""),
        "ok_blank_lines": ("0,0\n\n0.5,1.0\n1.0,0\n\n", ""),
        "nan": ("0,0\n0.5,NaN\n1,0\n", "nonfinite"),
        "nan_lower": ("0,0\n0.5,nan\n1,0\n", "nonfinite"),
        "inf": ("0,0\n0.5,Infinity\n1,0\n", "nonfinite"),
        "inf_neg": ("0,0\n0.5,-inf\n1,0\n", "nonfinite"),
        "nan_time": ("0,0\nNaN,1\n1,0\n", "nonfinite"),
        "header": ("t_s,setpoint_deg\n0,0\n1,0\n", "header"),
        "parse": ("0,0\n0.5,abc\n1,0\n", "parse"),
        "one_column": ("0,0\n0.5\n1,0\n", "parse"),
        "one_row": ("0,0\n", "rows"),
        "start_angle": ("0,2\n1,0\n", "start_angle"),
        "start_time": ("0.5,0\n1,0\n", "start_time"),
        "time_order": ("0,0\n0.5,1\n0.5,2\n1,0\n", "time_order"),
        "over_limit": (_rows(*_slow(41.0)), "peak"),
        "over_limit_neg": (_rows(*_slow(-40.5)), "peak"),
        # a smooth bump just under the limit plays; a triangle to exactly 40 does not (its corner overshoots ~0.2 deg)
        "near_limit_smooth": (_rows(np.arange(0, 4.0005, 0.001), 39.5 * np.sin(np.pi * np.arange(0, 4.0005, 0.001) / 4) ** 2), ""),
        "at_limit_corner": (_rows(*_slow(40.0, up=2.0)), "predicted"),
        "over_speed": ("0,0\n0.5,0\n0.505,7\n1.5,7\n2,0\n", "speed"),
        "pulse_6deg_ok": ("0,0\n0.2,0\n0.201,6\n0.28,6\n0.281,0\n1,0\n", ""),
        "predicted_overshoot": (_rows(*_overshoot_file()), "predicted"),
        # content between the 10 ms grid points (PR #117 review): reads ~0 at every k*10 ms, but a late tick
        # writes on any millisecond
        "alias_50hz_5deg": (_rows(*_alias50()), "speed"),
        "spike_1ms_offgrid": ("0,0\n0.504,0\n0.505,30\n0.506,0\n1,0\n", "speed"),
        "three_columns": ("0,0\n0.5,1,99\n1,0\n", "parse"),
        "bom_ok": ("﻿" + ok_small, ""),
    }
    return fx


def _alias50(amp=5.0):
    t = np.arange(0, 2.0005, 0.001)
    return t, amp * np.sin(2 * np.pi * 50 * t)


def write_fixtures(d):
    os.makedirs(d, exist_ok=True)
    exp = {}
    for name, (text, kind) in fixtures().items():
        with open(os.path.join(d, name + ".csv"), "w", newline="", encoding="utf-8") as fh:
            fh.write(text)
        exp[name] = kind
    return exp


# ── rig_guard on its own ──────────────────────────────────────────────────────
def test_every_fixture_gets_its_expected_verdict(tmp_path):
    exp = write_fixtures(str(tmp_path))
    for name, kind in exp.items():
        r = G.check_file(str(tmp_path / f"{name}.csv"))
        assert r["ok"] == (kind == ""), (name, r["reason"])
        assert r["kind"] == kind, (name, r["kind"], r["reason"])
        if kind:
            assert r["reason"] and r["reasons"]


def test_refusal_messages_name_the_time_and_the_value(tmp_path):
    write_fixtures(str(tmp_path))
    r = G.check_file(str(tmp_path / "nan.csv")); assert "t = 0.500 s" in r["reason"] and "nan" in r["reason"].lower()
    r = G.check_file(str(tmp_path / "over_limit.csv")); assert "41.00 deg" in r["reason"] and "limit is 40.0" in r["reason"]
    r = G.check_file(str(tmp_path / "over_speed.csv")); assert "7.00 deg" in r["reason"] and "600 deg/s" in r["reason"] and "t = 0.495 s" in r["reason"]
    r = G.check_file(str(tmp_path / "alias_50hz_5deg.csv")); assert "within 10 ms" in r["reason"] and r["max_step_deg"] > 9
    r = G.check_file(str(tmp_path / "three_columns.csv")); assert "3 columns" in r["reason"]
    r = G.check_file(str(tmp_path / "predicted_overshoot.csv"))
    assert r["raw_peak_deg"] <= 40 and r["max_step_deg"] <= 6 and r["predicted_peak_deg"] > 40
    assert "predicted to reach" in r["reason"]


def test_content_between_grid_points_is_seen():
    # the 0/10/20 ms grid alone sees nothing of a 50 Hz wiggle; the players' 1 ms clock can land anywhere
    t, sp = _alias50()
    assert np.abs(G.stream(t, sp)).max() < 1e-9
    r = G.check_trajectory(t, sp)
    assert not r["ok"] and r["kind"] == "speed" and abs(r["max_step_deg"] - 10.0) < 1e-6
    # the prediction is the worst of the ten 1 ms phases, never below phase 0
    t2, sp2 = _overshoot_file()
    p0, _ = G.predicted_peak(G.stream(t2, sp2))
    pall, _ = G.predicted_peak_all_phases(t2, sp2)
    assert pall >= p0


def test_the_hard_limit_can_only_be_lowered():
    t, sp = _slow(35.0)
    assert G.check_trajectory(t, sp, 40.0)["ok"]
    assert G.check_trajectory(t, sp, 30.0)["kind"] == "peak"
    for bad in (45.0, 40.01, 0.0, -5.0, float("nan")):
        r = G.check_trajectory(t, sp, bad)
        assert not r["ok"] and r["kind"] == "operator_limit", bad


def test_the_sync_pulses_pass_the_speed_rule_at_the_10ms_stream():
    t, sp = _preamble_file()
    r = G.check_trajectory(t, sp)
    assert r["ok"], r["reason"]
    assert abs(r["max_step_deg"] - 5.0) < 1e-9          # 5 deg in one write = 500 deg/s
    assert 5.0 < r["predicted_peak_deg"] - 30 + 30      # sanity: the prediction is a position, not an error


def test_plant_reproduces_the_identified_tracking_gains():
    # README sec. 4 / LABVIEW spec: |encoder/setpoint| 1.26 at 3 Hz, ~2.0 at 5 Hz (nominal plant, no detuning)
    for f, gain, tol in ((1.0, 1.01, 0.02), (3.0, 1.26, 0.03), (5.0, 1.9, 0.1)):
        t = np.arange(0, 12, 0.001); sp = np.sin(2 * np.pi * f * t)
        y = G.sim_response(G.stream(t, sp))
        g = np.abs(y[6000:11000]).max()
        assert abs(g - gain) < tol, (f, g)


def test_the_prediction_is_never_below_the_nominal_plant():
    t, sp = _overshoot_file()
    st = G.stream(t, sp)
    nominal = np.abs(G.sim_response(st)).max()
    pred, _ = G.predicted_peak(st)
    assert pred >= nominal and pred > np.abs(sp).max()


def test_scale_to_fit_makes_a_refused_stream_pass_with_margin():
    for t, sp in (_slow(55.0), _overshoot_file(), (np.array([0, .5, .505, 1.5, 2]), np.array([0, 0, 9, 9, 0.]))):
        r = G.check_trajectory(t, sp)
        assert not r["ok"] and r["scale_to_fit"] is not None
        s = r["scale_to_fit"]
        r2 = G.check_trajectory(t, sp * s)
        assert r2["ok"] and r2["predicted_peak_deg"] <= G.HARD_LIMIT_DEG - G.SCALE_MARGIN_DEG + 1e-9, (s, r2)
        r3 = G.check_trajectory(t, sp * (s + 0.02))      # the factor is the largest: 0.02 more misses the margin
        assert not r3["ok"] or r3["predicted_peak_deg"] > G.HARD_LIMIT_DEG - G.SCALE_MARGIN_DEG, (s, r3)
    assert G.check_trajectory(*_slow(10.0))["scale_to_fit"] is None
    assert G.check_trajectory([0, 1], [np.nan, 0])["scale_to_fit"] is None


def test_check_trajectory_is_the_bundlers_interface():
    r = G.check_trajectory(*_slow(10.0))
    for k in ("ok", "reasons", "predicted_peak_deg", "scale_to_fit"):
        assert k in r
    assert isinstance(r["reasons"], list) and isinstance(r["predicted_peak_deg"], float)


def test_index_sidecar(tmp_path):
    exp = write_fixtures(str(tmp_path / "fx"))
    rows = G.index_rows(sorted(glob.glob(str(tmp_path / "fx" / "*.csv"))))
    p = G.write_index(rows, str(tmp_path / "guard.csv"))
    got = {r["file"][:-4]: r for r in csv.DictReader(open(p, newline=""))}
    assert set(got) == set(exp)
    for name, kind in exp.items():
        assert (got[name]["ok"] == "True") == (kind == "")
    assert b"\r\n" not in open(p, "rb").read()


@pytest.mark.skipif(not G.library_paths(), reason="scenario library not built (python -m neuromech.build)")
def test_library_preambles_pass_the_speed_rule():
    # the deliberate 5 deg sync pulses must pass; whatever a library swing does is reported, not loosened
    for p in G.library_paths():
        t, sp = G.read_any(p)
        st = G.stream(t, sp)
        pre = np.abs(np.diff(np.concatenate([[0.0], st[:200]]))).max()
        assert abs(pre - 5.0) < 1e-6, (os.path.basename(p), pre)


# ── the C# guard and RigPlayer ────────────────────────────────────────────────
@pytest.fixture(scope="module")
def guardtest(tmp_path_factory):
    if not os.path.exists(CSC):
        pytest.skip("needs the .NET 3.5 C# compiler (Windows)")
    out = tmp_path_factory.mktemp("guardtest")
    exe = str(out / "GuardTest.exe")
    src = [os.path.join(PLAYER, "RigGuard.cs")] + [os.path.join(DESIGNER, f) for f in
           ("RigPlayer.cs", "PendulumModel.cs", "Designer.cs", "Scoring.cs", "PtScore.cs", os.path.join("selftest", "GuardTest.cs"))]
    r = subprocess.run([CSC, "-nologo", "-optimize+", "-out:" + exe, "-r:System.Core.dll"] + src, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return exe


@need_csc
def test_rigguard_is_csharp_2_for_powershell_2(tmp_path):
    # Windows PowerShell 2.0's Add-Type compiles C# 2.0
    r = subprocess.run([CSC, "-nologo", "-langversion:ISO-2", "-target:library", "-out:" + str(tmp_path / "g.dll"),
                        os.path.join(PLAYER, "RigGuard.cs")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    b = open(os.path.join(PLAYER, "RigGuard.cs"), "rb").read()
    assert all(c < 128 for c in b)


@need_csc
def test_csharp_and_python_agree_on_every_fixture(guardtest, tmp_path):
    exp = write_fixtures(str(tmp_path))
    for limit in (40.0, 30.0):
        r = subprocess.run([guardtest, "check", str(tmp_path), str(limit)], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr
        got = {}
        for line in r.stdout.strip().splitlines():
            c = line.split(",", 7)
            got[c[0][:-4]] = c
        assert set(got) == set(exp)
        for name in exp:
            py = G.check_file(str(tmp_path / f"{name}.csv"), limit)
            c = got[name]
            assert (c[1] == "ok") == py["ok"], (limit, name, c, py["reason"])
            assert c[2] == py["kind"], (limit, name, c[2], py["kind"])
            if np.isfinite(py["predicted_peak_deg"]):
                assert abs(float(c[3]) - py["predicted_peak_deg"]) < 1e-6 * max(1.0, py["predicted_peak_deg"]), name
                assert abs(float(c[4]) - py["max_step_deg"]) < 1e-9, name
            if py["scale_to_fit"] is not None:
                assert abs(float(c[6]) - py["scale_to_fit"]) < 1e-9, name


@need_csc
def test_csharp_player_refusals_and_backstop(guardtest, tmp_path):
    r = subprocess.run([guardtest, "player", str(tmp_path)], capture_output=True, text=True, timeout=300)
    lines = r.stdout.strip().splitlines()
    assert lines and all(",PASS," in l for l in lines), r.stdout + r.stderr
    assert r.returncode == 0
    names = {l.split(",")[0] for l in lines}
    for must in ("operator_limit_above_hard_limit", "refuse_nan", "refuse_inf", "refuse_over_limit", "refuse_over_speed",
                 "refuse_bad_start_angle", "refuse_setpoint_read_failure", "ramp_in_from_current",
                 "backstop_clamps_and_stops_at_the_limit", "backstop_never_sends_nan", "backstop_limits_step_and_stops",
                 "bypass_refused_against_the_rig", "write_failures_try_to_return_then_say_the_rig_holds",
                 "intermittent_write_failures_never_jump_the_rig", "rig_holding_leaves_the_flag",
                 "holding_flag_unacknowledged_refuses", "holding_flag_ignored_by_a_dry_run",
                 "holding_flag_acknowledged_plays_and_is_deleted", "unit_backstop_acked_limits_a_recovery_jump",
                 "unit_ramp_step_from_acked", "refuse_alias_between_grid_points", "refuse_1ms_spike_off_grid",
                 "refuse_return_speed_0", "refuse_return_speed_1000", "period_is_not_settable",
                 "refuse_encoder_equal_to_setpoint", "refuse_three_columns", "stop_during_ramp_in_cancels_the_play",
                 "lock_refuses_a_second_player", "lock_released_plays", "watchdog_off_is_said_and_logged",
                 "watchdog_on_tracking_arm_plays", "watchdog_stops_an_arm_that_does_not_follow",
                 "watchdog_stops_when_the_encoder_goes_silent", "watchdog_configured_but_unreadable_refuses",
                 "watchdog_wrong_variable_refuses"):
        assert must in names, must
    refused = glob.glob(str(tmp_path / "sent" / "*_refused.csv"))
    assert refused
    for p in refused:
        rows = list(csv.DictReader(open(p, newline="")))
        assert rows and all(r["phase"] in ("refused",) for r in rows), p


@need_csc
def test_default_designs_play_without_the_scale_down_prompt(guardtest, tmp_path):
    # owner decision 2026-11-17: default rig scale 0.70, so every default MAS design (55 deg release -> 38.5 deg)
    # passes the guard as designed
    r = subprocess.run([guardtest, "designs", str(tmp_path), "40"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    rows = list(csv.DictReader(r.stdout.strip().splitlines()))
    assert len(rows) == 6
    for row in rows:
        assert float(row["scale0"]) == 0.70 and row["ok0"] == "1", row
        assert float(row["pred0"]) <= G.HARD_LIMIT_DEG and float(row["raw0"]) == 38.5, row
    print({row["name"]: round(float(row["pred0"]), 3) for row in rows})


@need_csc
def test_designer_scale_down_offer_is_guarded_and_rescored(guardtest, tmp_path):
    from neuromech import build as B
    r = subprocess.run([guardtest, "designs", str(tmp_path), "40", "0.75"], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr
    rows = list(csv.DictReader(r.stdout.strip().splitlines()))
    assert len(rows) == 6
    for row in rows:
        # at the old 0.75 a 55 deg release renders at 41.25: refused, a scale-down is offered
        assert row["ok0"] == "0" and row["kind0"] == "peak", row
        assert row["ok1"] == "1" and float(row["pred1"]) <= G.HARD_LIMIT_DEG - G.SCALE_MARGIN_DEG + 1e-9
        assert float(row["scale1"]) < float(row["scale0"])
        rig = np.loadtxt(tmp_path / f"{row['name']}_scaled_rig.csv", delimiter=",")
        py = G.check_trajectory(rig[:, 0], rig[:, 1])
        assert py["ok"] and abs(py["predicted_peak_deg"] - float(row["pred1"])) < 1e-3     # the file is rounded to 4 decimals
        # the score the Designer shows for the scaled swing is the scaled swing's
        k = np.loadtxt(tmp_path / f"{row['name']}_scaled_knee.csv", delimiter=",")
        _, est = B.score_angle(k[:, 0], k[:, 1], 3.0)
        assert abs(est["pt7"] - float(row["pt7_1"])) < 1e-9 and est["mas"] == row["mas1"]


# ── play_setpoints.ps1 ────────────────────────────────────────────────────────
@pytest.fixture()
def ps(tmp_path):
    if POWERSHELL is None:
        pytest.skip("needs Windows PowerShell")
    d = tmp_path / "kit"; d.mkdir()
    for f in ("play_setpoints.ps1", "RigGuard.cs"):
        shutil.copy(os.path.join(PLAYER, f), d)

    def run(f, *extra, timeout=180, input=None):
        return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(d / "play_setpoints.ps1"),
                               "-File", str(f), "-NoPrompt", *extra], capture_output=True, text=True, timeout=timeout,
                              input=input)
    run.dir = d
    return run


WRITE_PHASES = ("ramp_in", "play", "hold", "return", "abort_return")


def _writes(rows):
    """The values the player wrote (a sent log's write rows; encoder/guard/watchdog/refused/error rows are events)."""
    return [float(x["setpoint_deg"]) for x in rows if x["phase"] in WRITE_PHASES]


def _sent(d, pattern="*.csv"):
    return sorted(glob.glob(str(d / "sent" / pattern)), key=os.path.getmtime)


@need_ps
def test_powershell_and_python_agree_on_every_fixture(ps, tmp_path):
    exp = write_fixtures(str(tmp_path / "fx"))
    for name, kind in exp.items():
        r = ps(tmp_path / "fx" / f"{name}.csv", "-DryRun")
        assert r.returncode == (0 if kind == "" else 1), (name, r.stdout[-800:], r.stderr[-800:])
        if kind:
            log = _sent(ps.dir, f"{name}_*_refused.csv")[-1]
            row = list(csv.DictReader(open(log, newline="")))[0]
            assert row["phase"] == "refused" and row["note"].startswith(kind + ":"), (name, row)


@need_ps
def test_powershell_operator_limit_above_40_is_refused(ps, tmp_path):
    write_fixtures(str(tmp_path))
    r = ps(tmp_path / "ok_small.csv", "-DryRun", "-MaxDeg", "45")
    assert r.returncode == 1 and "hard limit of 40 deg" in r.stdout
    r = ps(tmp_path / "ok_small.csv", "-DryRun", "-MaxDeg", "8")
    assert r.returncode == 1 and "limit is 8.0" in r.stdout
    r = ps(tmp_path / "ok_small.csv", "-DryRun", "-MaxDeg", "20")
    assert r.returncode == 0, r.stdout


@need_ps
def test_powershell_refuses_when_the_setpoint_cannot_be_read(ps, tmp_path):
    write_fixtures(str(tmp_path))
    for spec, text in (("read=fail", "could not read the current setpoint"), ("read=nan", "reads NaN"),
                       ("read=45", "beyond the 40.0 deg limit")):
        r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", spec)
        assert r.returncode == 1 and text in r.stdout, (spec, r.stdout)
        assert "Playing." not in r.stdout
    assert len(_sent(ps.dir, "*_refused.csv")) == 3
    assert not [p for p in _sent(ps.dir) if not p.endswith("_refused.csv")]     # nothing was played


@need_ps
def test_powershell_ramps_in_from_a_readable_setpoint(ps, tmp_path):
    write_fixtures(str(tmp_path))
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=3")
    assert r.returncode == 0, r.stdout + r.stderr
    rows = list(csv.DictReader(open(_sent(ps.dir)[-1], newline="")))
    sent = _writes(rows)
    assert [x["phase"] for x in rows if x["phase"] in WRITE_PHASES][0] == "ramp_in" and abs(sent[0] - 3) < 0.2
    assert max(abs(np.diff(sent))) <= 6 and sent[-1] == 0
    assert not [x for x in rows if x["phase"] == "guard"]


@need_ps
def test_powershell_backstop_stops_and_returns_to_zero(ps, tmp_path):
    f = tmp_path / "steep.csv"; f.write_text(_rows(*_slow(45.0)))   # passes only the parser
    r = ps(f, "-TestBypassLoadChecks")                               # no -DryRun, no fake: refused
    assert r.returncode == 1 and "tests only" in r.stdout
    r = ps(f, "-DryRun", "-TestBypassLoadChecks")
    assert r.returncode == 5, r.stdout + r.stderr
    assert "STOPPED BY THE GUARD" in r.stdout
    log = _sent(ps.dir, "*_guard.csv")[-1]
    rows = list(csv.DictReader(open(log, newline="")))
    sent = _writes(rows)
    assert max(abs(v) for v in sent) <= 40 + 1e-9 and max(abs(np.diff(sent))) <= 6 + 1e-9 and sent[-1] == 0
    assert any(x["phase"] == "guard" for x in rows) and any(x["phase"] == "abort_return" for x in rows)
    nan = tmp_path / "nanmid.csv"; nan.write_text("0,0\n0.3,2\n0.6,NaN\n0.9,0\n")
    r = ps(nan, "-DryRun", "-TestBypassLoadChecks")
    assert r.returncode == 5
    rows = list(csv.DictReader(open(_sent(ps.dir, "nanmid_*_guard.csv")[-1], newline="")))
    assert all(np.isfinite(v) for v in _writes(rows)) and all(np.isfinite(float(x["setpoint_deg"])) for x in rows if x["phase"] == "guard")


@need_ps
def test_powershell_failed_writes_try_a_return_then_say_the_rig_holds(ps, tmp_path):
    write_fixtures(str(tmp_path))
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0,write=fail")
    assert r.returncode == 4 and "Trying to ramp the setpoint to 0" in r.stdout and "RIG HOLDING LAST SETPOINT" in r.stdout, r.stdout
    rows = list(csv.DictReader(open(_sent(ps.dir, "*_error.csv")[-1], newline="")))
    assert any(x["phase"] == "abort_return" for x in rows) and any("USE THE E-STOP" in x["note"] for x in rows)
    assert all("write failed" in x["note"] for x in rows if x["phase"] in WRITE_PHASES)
    assert (ps.dir / "sent" / "HOLDING.flag").exists()


@need_ps
def test_powershell_intermittent_write_failures_never_jump_the_rig(ps, tmp_path):
    # the rig takes writes 1..59, misses 60..79 mid-rise of a 35 deg swing, takes them again from 80. The play
    # ends at the first failure and the return steps from the last value the rig TOOK: no jump when it recovers
    f = tmp_path / "rise35.csv"; f.write_text(_rows(*_slow(35.0)))
    r = ps(f, "-TestFakeDataSocket", "read=0,write=fail@60-79")
    assert r.returncode == 4 and "RIG HOLDING" not in r.stdout and "ramped back to 0" in r.stdout, r.stdout + r.stderr
    rows = list(csv.DictReader(open(_sent(ps.dir, "rise35_*_error.csv")[-1], newline="")))
    w = [x for x in rows if x["phase"] in WRITE_PHASES]
    taken = [float(x["setpoint_deg"]) for x in w if "write failed" not in x["note"]]
    failed = [i for i, x in enumerate(w) if "write failed" in x["note"]]
    assert len(failed) == 20 and w[failed[0]]["phase"] == "play", [x["phase"] for x in w[55:62]]
    assert not [x for x in w[failed[0] + 1:] if x["phase"] == "play"]          # play stopped at the first failure
    assert max(abs(np.diff(taken))) <= 6 + 1e-9 and taken[-1] == 0
    assert max(abs(v) for v in taken) < 35
    assert not (ps.dir / "sent" / "HOLDING.flag").exists()


@need_ps
def test_powershell_rig_holding_must_be_acknowledged(ps, tmp_path):
    write_fixtures(str(tmp_path))
    flag = ps.dir / "sent" / "HOLDING.flag"
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0,write=fail")
    assert r.returncode == 4 and flag.exists() and "RIG HOLDING" in flag.read_text()
    for answer in ("no\n", "yes\n", ""):                                  # anything but YES, or no input at all
        r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", input=answer)
        assert r.returncode == 1 and "HOLDING" in r.stdout and "not acknowledged" in r.stdout and "Playing." not in r.stdout, (answer, r.stdout)
        assert flag.exists()
    log = _sent(ps.dir, "*_refused.csv")[-1]
    assert list(csv.DictReader(open(log, newline="")))[0]["note"].startswith("holding_unacknowledged:")
    r = ps(tmp_path / "ok_small.csv", "-DryRun")                              # a dry run sends nothing: not asked
    assert r.returncode == 0 and flag.exists()
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", input="YES\n")
    assert r.returncode == 0 and "Acknowledged" in r.stdout and not flag.exists(), r.stdout


@need_ps
def test_powershell_removed_and_unknown_options_are_refused(ps, tmp_path):
    write_fixtures(str(tmp_path))
    for extra in (("-PeriodMs", "1"), ("-NoReturn",), ("-Typo",)):
        r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", *extra)
        assert r.returncode == 1 and "unknown option" in r.stdout and "Playing." not in r.stdout, (extra, r.stdout)


@need_ps
def test_powershell_return_speed_is_bounded(ps, tmp_path):
    write_fixtures(str(tmp_path))
    for v in ("0", "0.5", "60.5", "1000"):
        r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", "-ReturnDps", v)
        assert r.returncode == 1 and "return speed" in r.stdout and "Playing." not in r.stdout, (v, r.stdout)
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=3", "-ReturnDps", "60")
    assert r.returncode == 0, r.stdout
    sent = _writes(list(csv.DictReader(open(_sent(ps.dir)[-1], newline=""))))
    assert sent[-1] == 0 and max(abs(np.diff(sent))) <= 6


@need_ps
def test_powershell_rereads_the_setpoint_after_enter(ps, tmp_path):
    write_fixtures(str(tmp_path))
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", "-TestSetpointAfterEnter", "3")
    assert r.returncode == 1 and "moved from 0.00 to 3.00" in r.stdout and "Playing." not in r.stdout, r.stdout
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", "-TestSetpointAfterEnter", "fail")
    assert r.returncode == 1 and "could not read the setpoint again" in r.stdout
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", "-TestSetpointAfterEnter", "0.4")
    assert r.returncode == 0, r.stdout                                 # within 0.5 deg: ramps from the fresh value
    rows = list(csv.DictReader(open(_sent(ps.dir)[-1], newline="")))
    assert abs(_writes(rows)[0] - 0.4) < 1e-9
    r = ps(tmp_path / "ok_small.csv", "-TestSetpointAfterEnter", "3")   # test switches never reach a rig
    assert r.returncode == 1 and "tests only" in r.stdout


@need_ps
def test_powershell_refuses_the_setpoint_as_its_own_encoder(ps, tmp_path):
    write_fixtures(str(tmp_path))
    r = ps(tmp_path / "ok_small.csv", "-TestFakeDataSocket", "read=0", "-TestFakeEncoder", "track", "-EncoderUrl", "fake://setpoint")
    assert r.returncode == 1 and "is the setpoint variable" in r.stdout, r.stdout


@need_ps
@pytest.mark.parametrize("how", ["signal", "key"])
def test_powershell_ctrl_c_stops_through_the_return_to_zero(ps, tmp_path, how):
    # in its own console (the Ctrl+C must not reach pytest): a console signal, and a keystroke in its input.
    # signal@ runs with TreatControlCAsInput left off (the console only delivers the signal then): the case
    # the CancelKeyPress handler exists for; key@ is the normal bench setting
    f = tmp_path / "slow.csv"; f.write_text(_rows(*_slow(10.0)))
    r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps.dir / "play_setpoints.ps1"),
                        "-File", str(f), "-NoPrompt", "-TestFakeDataSocket", "read=0", "-TestCtrlC", f"{how}@0.6"],
                       creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=120)
    assert r.returncode == 1
    rows = list(csv.DictReader(open(_sent(ps.dir, "slow_*.csv")[-1], newline="")))
    stop = [x for x in rows if x["phase"] == "stop"]
    assert stop and "Ctrl+C" in stop[0]["note"], rows[-5:]
    sent = _writes(rows)
    assert sent[-1] == 0 and max(sent) < 10 and rows[-1]["phase"] == "abort_return"


@need_ps
def test_powershell_encoder_watchdog(ps, tmp_path):
    f = tmp_path / "slow.csv"; f.write_text(_rows(*_slow(10.0)))
    r = ps(f, "-TestFakeDataSocket", "read=0")                      # nothing picked: plays, says OFF
    assert r.returncode == 0 and "encoder watchdog OFF" in r.stdout
    assert "encoder watchdog OFF" in open(_sent(ps.dir)[-1]).read()
    r = ps(f, "-TestFakeDataSocket", "read=0", "-TestFakeEncoder", "track")
    assert r.returncode == 0 and "Watchdog  ON" in r.stdout, r.stdout
    rows = list(csv.DictReader(open(_sent(ps.dir)[-1], newline="")))
    assert any(x["phase"] == "encoder" for x in rows) and not any(x["phase"] == "watchdog" for x in rows)
    r = ps(f, "-TestFakeDataSocket", "read=0", "-TestFakeEncoder", "0")   # the arm does not follow
    assert r.returncode == 6 and "STOPPED BY THE ENCODER WATCHDOG" in r.stdout, r.stdout
    rows = list(csv.DictReader(open(_sent(ps.dir, "*_watchdog.csv")[-1], newline="")))
    sent = _writes(rows)
    assert any(x["phase"] == "watchdog" for x in rows) and sent[-1] == 0 and max(sent) < 10
    r = ps(f, "-TestFakeDataSocket", "read=0", "-TestFakeEncoder", "failafter")
    assert r.returncode == 6 and "could not be read 5 times" in r.stdout
    r = ps(f, "-TestFakeDataSocket", "read=0", "-TestFakeEncoder", "fail")
    assert r.returncode == 1 and "cannot read it" in r.stdout
    r = ps(f, "-TestFakeDataSocket", "read=0", "-TestFakeEncoder", "12")
    assert r.returncode == 1 and "actual position" in r.stdout


@need_ps
@need_csc
def test_one_writer_at_a_time_both_directions(ps, guardtest, tmp_path):
    f = tmp_path / "slow.csv"; f.write_text(_rows(*_slow(10.0, up=3.0)))     # ~6 s of play + return
    # the Designer's player holds the lock -> PLAY refuses
    h = subprocess.Popen([guardtest, "hold", "20"], stdout=subprocess.PIPE, text=True)
    try:
        assert h.stdout.readline().strip() == "HELD"
        r = ps(f, "-TestFakeDataSocket", "read=0")
        assert r.returncode == 1 and "another player" in r.stdout, r.stdout
        assert "Playing." not in r.stdout
    finally:
        h.kill(); h.wait()
    # PLAY holds the lock -> the Designer's player refuses
    pl = subprocess.Popen([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps.dir / "play_setpoints.ps1"),
                           "-File", str(f), "-NoPrompt", "-TestFakeDataSocket", "read=0"], stdout=subprocess.PIPE, text=True)
    try:
        r = subprocess.run([guardtest, "probe", str(tmp_path / "probe"), "30"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0 and r.stdout.startswith("REFUSED_WHILE_HELD"), r.stdout
    finally:
        out, _ = pl.communicate(timeout=120)
    assert pl.returncode == 0, out                      # the PowerShell run itself was not disturbed
    r = ps(f, "-TestFakeDataSocket", "read=0")          # and the lock is free again afterwards
    assert r.returncode == 0


# ── check-log sees guard events ───────────────────────────────────────────────
def test_check_log_fails_a_run_whose_sent_log_has_guard_events(tmp_path):
    from neuromech import session as SS
    t = np.arange(0, 8, 0.001); sp = 5 * np.sin(2 * np.pi * 0.5 * t) * np.sin(np.pi * t / 8) ** 2
    player = tmp_path / "p.csv"; np.savetxt(player, np.column_stack([t, sp]), fmt="%.4f", delimiter=",")
    n = len(sp); z = np.zeros(n)
    full = np.concatenate([np.zeros(500), sp, np.zeros(500)]); zz = np.zeros(len(full))
    np.vstack([full, zz, full, zz, zz, zz, zz]).T.astype(">f8").tofile(tmp_path / "log.bin")
    clean = tmp_path / "clean.csv"
    clean.write_text("t_wall_s,t_file_s,phase,setpoint_deg,note\n0.0,0.0,play,0.0,\n0.01,0.01,play,0.1,\n")
    dirty = tmp_path / "dirty.csv"
    dirty.write_text("t_wall_s,t_file_s,phase,setpoint_deg,note\n0.0,0.0,play,0.0,\n1.0,1.0,guard,40.0,play: setpoint 40.05 deg beyond the limit\n")
    old = tmp_path / "old.csv"; old.write_text("t_wall_s,t_file_s,phase,setpoint_deg\n0.0,0.0,play,0.0\n")
    assert SS.check_log(str(tmp_path / "log.bin"), str(player), verbose=False)["ok"]
    assert SS.check_log(str(tmp_path / "log.bin"), str(player), verbose=False, sent=[str(clean), str(old)])["ok"]
    r = SS.check_log(str(tmp_path / "log.bin"), str(player), verbose=False, sent=[str(dirty)])
    assert not r["ok"] and "guard event" in r["reason"] and r["guard_events"][0][0] == "guard"
    assert SS.guard_events(str(old)) == []
