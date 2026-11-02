"""The USB kit for the rig PC, and the PowerShell player inside it (neuromech.usb_kit).

The player test runs the real script in dry-run mode, so it needs Windows PowerShell; skipped elsewhere.
"""
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import session as SS  # noqa: E402
from neuromech import usb_kit as UK  # noqa: E402

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(SS.OUT, "scenario_manifest.csv")),
                                reason="scenario library not built (python -m neuromech.build)")
POWERSHELL = shutil.which("powershell")


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    SS.SESS = str(tmp_path_factory.mktemp("sessions"))
    SS.plan("K1", sids=["C-A0-low"], chirp=True)
    return UK.build("K1", str(tmp_path_factory.mktemp("usb")))


def test_kit_has_what_the_bench_needs_and_not_the_key(kit):
    have = set(os.listdir(kit))
    assert set(UK.SCRIPTS) | {"README.txt", "smoke_5deg.csv", "runsheet.csv", "player", "logs"} <= have
    assert "key.csv" not in have                     # the bench grades blind (R3)
    assert sorted(os.listdir(os.path.join(kit, "player"))) == ["R000.csv", "R001.csv"]


def test_smoke_file_is_gentle_and_playable(kit):
    a = np.loadtxt(os.path.join(kit, "smoke_5deg.csv"), delimiter=",")
    assert a[0, 0] == 0 and a[0, 1] == 0 and abs(a[-1, 1]) < 1e-3
    assert np.abs(a[:, 1]).max() <= 5.0 + 1e-9
    assert np.abs(np.diff(a[:, 1])).max() * 1000 < 10    # deg/s: slow enough for first contact


def test_scripts_survive_an_old_windows(kit):
    # Windows PowerShell 2-5 reads a BOM-less .ps1 as ANSI and cmd needs CRLF: keep both plain
    for f in UK.SCRIPTS:
        b = open(os.path.join(kit, f), "rb").read()
        assert all(c < 128 for c in b), f
        assert b.count(b"\n") == b.count(b"\r\n"), f


@pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell")
def test_player_dry_run_sends_the_file_on_its_clock_and_refuses_a_bad_one(kit, tmp_path):
    ps1 = os.path.join(kit, "play_setpoints.ps1")
    run = lambda f, *extra: subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
                                            "-File", f, "-DryRun", "-NoPrompt", *extra],
                                           capture_output=True, text=True, timeout=120)
    smoke = os.path.join(kit, "smoke_5deg.csv")
    r = run(smoke)
    assert r.returncode == 0, r.stdout + r.stderr
    sent = [os.path.join(kit, "sent", f) for f in os.listdir(os.path.join(kit, "sent"))]
    d = np.genfromtxt(sent[0], delimiter=",", names=True, dtype=None, encoding="utf-8")
    play = d[d["phase"] == "play"]
    f = np.loadtxt(smoke, delimiter=",")
    assert np.abs(np.interp(play["t_file_s"], f[:, 0], f[:, 1]) - play["setpoint_deg"]).max() < 1e-3
    gaps = np.diff(play["t_wall_s"]) * 1000
    assert np.median(gaps) < 15, np.median(gaps)    # ~10 ms; generous for a loaded test machine

    big = tmp_path / "big.csv"; big.write_text("0,0\n0.001,50\n")
    r = run(str(big))
    assert r.returncode == 1 and "limit is 45.0" in r.stdout
