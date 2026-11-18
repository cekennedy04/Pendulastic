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
    src = [os.path.join(D, f) for f in ("PendulumModel.cs", "DeGroote.cs", "Designer.cs", "Scoring.cs", "PtScore.cs")] + \
          [os.path.join(D, "selftest", "SelfTest.cs"), os.path.join(D, "..", "labview", "player", "RigGuard.cs")]
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


# ── the published severity axis by lambda (DeGroote.cs), added 2026-11-18 for lambda 1.4 ──────────────
AXIS = (1.0, 1.2, 1.4)


def _axis_scenario(lam):
    from neuromech import scenarios as S
    return S.Scenario(f"S-lam{lam:g}", "S", "", S.severity(lam))


def test_the_designers_severity_axis_is_the_python_model(run):
    """DeGroote.cs against neuromech.model_rt at lambda 1.0, 1.2 and 1.4, played 1:1: the knee trace the
    Designer scores is the trace build.score scores (to 1e-6 deg), and it reports the library's expected PT7,
    band, R2n and N for S-lam1, S-lam1.2 and S-lam1.4."""
    d, _ = run
    ax = pd.read_csv(d / "axis.csv", dtype={"mas": str}).set_index("name")
    for lam in AXIS:
        sc = _axis_scenario(lam)
        t, th, rest, _ = B.render(sc)
        prm, est = B.score(t, th, sc)
        tg = np.arange(0, t[-1], 1 / B.FS_SCORE)
        want = np.concatenate([np.full(int(B.HOLD_S * B.FS_SCORE), 180.0 + th[0]), 180.0 + np.interp(tg, t, th)])
        k = np.loadtxt(d / f"axis_{lam:.1f}_knee.csv", delimiter=",")
        assert len(k) == len(want), (lam, len(k), len(want))
        assert np.abs(k[:, 1] - want).max() < 1e-6, (lam, np.abs(k[:, 1] - want).max())
        r = ax.loc[f"axis_{lam:.1f}"]
        assert abs(r.pt7 - est["pt7"]) < 1e-6 and r.mas == est["mas"] and bool(r.interpretable) == est["interpretable"]
        assert abs(r.R2n - prm["R2n"]) < 1e-6 and r.N == prm["N"]


def test_the_designer_finds_the_same_pt7_peak_and_lambda_1p4_is_past_it(run):
    """Designer.RisingBranchPeak is build.rising_branch_peak's scan: the same lambda, the same PT7, and both
    neighbours on the 0.05 grid score lower. Lambda 1.0 to 1.4 are past it, and each scores below the peak."""
    d, _ = run
    f = pd.read_csv(d / "axis_facts.csv").iloc[0]
    assert abs(B.pt7_at(f.peak_lam) - f.peak_pt7) < 1e-6
    assert B.pt7_at(f.peak_lam - 0.05) < f.peak_pt7 and B.pt7_at(f.peak_lam + 0.05) < f.peak_pt7
    from neuromech.model_rt import Params, passive_rest_deg
    assert abs(f.rest_deg - passive_rest_deg(Params())) < 1e-9
    ax = pd.read_csv(d / "axis.csv")
    ax = ax[ax.name.str.startswith("axis_")]
    assert min(AXIS) > f.peak_lam and (ax.pt7 < f.peak_pt7).all()
    assert list(ax.pt7) == sorted(ax.pt7, reverse=True)          # still falling from 1.0 to 1.4


def test_lambda_1p4_plays_at_the_default_scale_and_not_at_1_to_1(run):
    """Lambda 1.4 releases 55 deg from the passive rest: over the 40 deg limit 1:1 (refused, scalable), inside
    it at the Designer's default 0.70 scale, and the scale-down the Designer would offer is 0.70. The C# guard's
    predicted delivered peak is neuromech.rig_guard's on the same rig file.

    The playable swing is NOT gradable: 0.70 x the 36.1 deg excursion is 25.3 deg, under the scorer's 30 deg
    floor, so both scorers refuse it (the PT7 underneath is unchanged, 0.170). Grading needs a scale of 0.84,
    a 46 deg release, which the guard refuses. Neither the guard nor the floor is this PR's to move."""
    from neuromech import rig_guard as G
    d, s = run
    f = pd.read_csv(d / "axis_facts.csv").iloc[0]
    assert f.ok_x070 == 1 and f.ok_x1 == 0 and abs(f.scaled_to - 0.70) < 1e-9
    rig = np.loadtxt(d / "lam_1.4_rig.csv", delimiter=",")
    g = G.check_trajectory(rig[:, 0], rig[:, 1], limit_deg=G.HARD_LIMIT_DEG)
    assert g["ok"] and abs(g["predicted_peak_deg"] - f.pred_x070) < 1e-3 and g["predicted_peak_deg"] < 39.0
    assert abs(np.abs(rig[:, 1]).max() - 0.70 * 55.0) < 0.01
    r = pd.read_csv(d / "axis.csv", dtype={"mas": str}).set_index("name").loc["lam_1.4"]
    k = np.loadtxt(d / "lam_1.4_knee.csv", delimiter=",")
    prm, est = B.score_angle(k[:, 0], k[:, 1], 3.0)
    assert r.interpretable == 0 and not est["interpretable"] and est["reason"].startswith("Insufficient excursion")
    assert abs(prm["A0_deg"] - 0.70 * 36.0823) < 0.01 and abs(est["pt7"] - r.pt7) < 1e-9
    one = pd.read_csv(d / "axis.csv").set_index("name").loc["axis_1.4"]
    assert one.interpretable == 1 and abs(one.pt7 - r.pt7) < 1e-6          # scaling moves A0, not the ratios
