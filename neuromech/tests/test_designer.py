"""The Pendulum Designer's movements, rescored by the Python production scorer (neuromech.build.score_angle).

Compiles the headless self-test (neuromech/designer/selftest) with the .NET 3.5 compiler the rig PC's
Windows ships, runs every MAS preset and a parameter target, and checks that:
  * what the designer reports is what Python's scorer says about the same knee trace;
  * each preset lands in its own grade's PT7 band;
  * the rig file it would play starts at 0, stays inside +/-45 deg, and -- read back the way an encoder
    log is (match_trials.delivered) -- scores the same as the design.
"""
import os
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import build as B  # noqa: E402
from neuromech import match_trials as MT  # noqa: E402

D = os.path.join(os.path.dirname(__file__), "..", "designer")
CSC = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Microsoft.NET", "Framework", "v3.5", "csc.exe")
pytestmark = pytest.mark.skipif(not os.path.exists(CSC), reason="needs the .NET 3.5 C# compiler (Windows)")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    out = tmp_path_factory.mktemp("designer")
    exe = str(out / "selftest.exe")
    src = [os.path.join(D, f) for f in ("PendulumModel.cs", "Designer.cs", "Scoring.cs", "PtScore.cs")] + \
          [os.path.join(D, "selftest", "SelfTest.cs")]
    r = subprocess.run([CSC, "-nologo", "-optimize+", "-out:" + exe, "-r:System.Core.dll"] + src, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([exe, str(out / "st")], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    return out / "st", pd.read_csv(out / "st" / "summary.csv", dtype={"mas": str})


def test_the_designer_reports_what_the_python_scorer_says(run):
    d, s = run
    for _, r in s.iterrows():
        k = np.loadtxt(d / f"{r['name']}_knee.csv", delimiter=",")
        prm, est = B.score_angle(k[:, 0], k[:, 1], 3.0)
        assert abs(est["pt7"] - r.pt7) < 1e-9, (r["name"], est["pt7"], r.pt7)
        assert abs(prm["R2n"] - r.R2n) < 1e-9 and prm["N"] == r.N
        assert est["mas"] == r.mas and est["interpretable"] == bool(r.interpretable)


def test_each_mas_preset_lands_in_its_own_band(run):
    _, s = run
    for g in ("0", "1", "1+", "2", "3", "4"):
        r = s[s.name == "mas_" + g.replace("+", "p")].iloc[0]
        assert r.interpretable == 1 and r.mas == g, (g, r.pt7, r.mas)
    pts = [s[s.name == "mas_" + g].pt7.iloc[0] for g in ("0", "1", "1p", "2", "3", "4")]
    assert pts == sorted(pts)


def test_the_rig_file_is_playable_and_scores_like_the_design_when_read_back(run, tmp_path):
    d, s = run
    for _, r in s.iterrows():
        rig = np.loadtxt(d / f"{r['name']}_rig.csv", delimiter=",")
        assert rig[0, 1] == 0 and np.abs(rig[:, 1]).max() <= 45.0
        assert abs(r.release_t - MT.PREAMBLE_S) < 1e-9
        # a perfect encoder: the log's position IS the command; read it back as match_trials does
        n = len(rig); z = np.zeros(n)
        np.vstack([rig[:, 1], z, rig[:, 1], z, z, z, z]).T.astype(">f8").tofile(tmp_path / "log.bin")
        os.makedirs(tmp_path / "trial_files", exist_ok=True)
        np.savetxt(tmp_path / "trial_files" / "T.csv", rig, delimiter=",", fmt="%.4f")
        got = MT.delivered(str(tmp_path), os.path.join("trial_files", "T.csv"), str(tmp_path / "log.bin"))
        assert abs(got["enc_pt7"] - r.pt7) < 5e-3, (r["name"], got["enc_pt7"], r.pt7)   # 4-decimal file rounding
