"""The whole rig-session program, end to end, without hardware (neuromech.dryrun).

Needs the built scenario library (python -m neuromech.build); skipped if it is absent.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import session as SS  # noqa: E402
from neuromech import dryrun as DR  # noqa: E402
from neuromech import ilc  # noqa: E402

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(SS.OUT, "scenario_manifest.csv")),
                                reason="scenario library not built (python -m neuromech.build)")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    SS.SESS = str(tmp_path_factory.mktemp("sessions"))
    return DR.run(["A-P0", "A-P2", "M-catch_release_high"], seed=3, keep=True)


@pytest.fixture(scope="module")
def report(session):
    return session[0]


def test_chirp_recovers_the_plant(report):
    r = report[report.sid == "R-chirp"].iloc[0]
    assert abs(r.plant_wn_hz - DR.PLANT["wn_hz"]) / DR.PLANT["wn_hz"] < 0.05, r.plant_wn_hz
    assert abs(r.plant_zeta - DR.PLANT["zeta"]) < 0.05, r.plant_zeta


def test_every_run_is_found_and_scored(report):
    sc = report[report.sid != "R-chirp"]
    assert (sc.status == "scored").all(), sc.status.tolist()


def test_reversed_phone_mount_is_detected(report):
    sc = report[report.sid != "R-chirp"]
    assert sc.phone_status.str.startswith("ok (sign -1").all(), sc.phone_status.tolist()


def test_phone_agrees_with_encoder(report):
    """Bias-limited, by construction: a 0.0015 rad/s gyro bias is estimated from ~5 s of commanded stillness
    (SE ~0.01-0.02 deg/s) and the residual drifts the settled tail into area_ratio. The bound reflects that
    floor; tightening it needs a better bias estimate, not a looser pipeline."""
    sc = report[report.sid != "R-chirp"]
    d = (sc.ph_pt7 - sc.enc_pt7).abs()
    # Measured floor (dry runs, several seeds): |dPT7| 0.00-0.14, one band at worst. Bias is taken only from
    # stillness the phone itself could detect before release -- what the app can do -- never from the encoder.
    assert d.max() < 0.2, d.tolist()
    assert ((sc.ph_A0_deg - sc.enc_A0_deg).abs() < 2.0).all()


def test_bias_is_recovered(report):
    sc = report[report.sid != "R-chirp"]
    b = sc.phone_status.str.extract(r"bias ([+-][0-9.]+)")[0].astype(float)
    assert (abs(b - 0.0859) < 0.05).all(), b.tolist()        # true bias 0.0015 rad/s = 0.0859 deg/s


def test_identify_on_a_known_plant():
    rng = np.random.default_rng(0)
    t, sp, *_ = SS.chirp_command()
    y = DR.plant_response(sp) + rng.normal(0, 0.02, len(sp))
    fit = ilc.identify(sp, y)
    assert abs(fit["wn_hz"] - 6.35) < 0.15 and abs(fit["zeta"] - 0.23) < 0.03 and fit["r2"] > 0.98


def test_locate_finds_the_exact_offset():
    """The log's setpoint channel is the command; locate must return the true offset for every scenario
    shape, including short fast ones (the unnormalised cross-correlation got C-f-high wrong by >1 s)."""
    for sid in ("C-f-high", "A-P2", "M-fall_no_ringdown"):
        t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", f"{sid}.csv"))
        for pre in (0, 1234, 2500):
            log = np.concatenate([np.zeros(pre), sp, np.full(800, sp[-1])])
            assert ilc.locate(sp, log) == pre, (sid, pre, ilc.locate(sp, log))


def test_every_run_delivers_its_intended_band(report):
    sc = report[report.sid != "R-chirp"]
    assert sc.delivered_band_ok.all(), sc[["sid", "int_band", "enc_band"]].values.tolist()


def test_ilc_converges_through_the_real_tools(session):
    rep, d = session
    rid = rep[rep.sid == "A-P2"].run_id.iloc[0]
    r = DR.ilc_demo(d, [rid], iters=3)
    first, last = r.tracking_rmse_deg.iloc[0], r.tracking_rmse_deg.iloc[-1]
    assert last < 0.4 * first and last < 0.2, r.tracking_rmse_deg.tolist()


def test_player_files_are_what_the_pendulum_player_reads(session):
    """Two columns, no header, starting at 0 deg: the Player derives dt from rows 0 and 1, so a header
    row (read as zeros) would make dt = 0 and end playback at once."""
    d = session[1]
    for rid in [l.split(",")[0] for l in open(os.path.join(d, "key.csv"))][1:]:
        path = os.path.join(d, "player", rid + ".csv")
        first = open(path).readline()
        assert all(c.isdigit() or c in ".,-\n" for c in first), first      # numeric, so not a header
        x = np.loadtxt(path, delimiter=",")
        assert x.shape[1] == 2 and x[0, 1] == 0.0
        assert abs((x[1, 0] - x[0, 0]) - 0.001) < 1e-9                      # the dt the Player computes
        cmd = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
        assert np.max(np.abs(x[:, 1] - cmd["setpoint_deg"])) < 1e-3


def test_player_refuses_a_command_that_does_not_start_at_zero(tmp_path):
    with pytest.raises(ValueError):
        SS._write_player(str(tmp_path / "R001.csv"), np.arange(3) / 1000.0, np.array([5.0, 5.0, 5.0]))


def test_logs_are_paired_by_the_name_on_the_run_sheet(session, tmp_path):
    """StretchExperiment names its own logs; the operator writes the real name on the run sheet."""
    import shutil
    import pandas as pd
    d = session[1]; name = os.path.basename(d)
    enc = tmp_path / "enc"; shutil.copytree(os.path.join(d, "synthetic_encoder"), enc)
    sheet = pd.read_csv(os.path.join(d, "runsheet.csv"), dtype=str)
    orig = sheet.copy()
    for k, rid in enumerate(sheet.run_id):
        os.rename(enc / f"{rid}.bin", enc / f"ck_StretchTrial{70 + k}Fast.bin")
        sheet.loc[sheet.run_id == rid, "encoder_file"] = f"ck_StretchTrial{70 + k}Fast.bin"
    sheet.to_csv(os.path.join(d, "runsheet.csv"), index=False)
    try:
        rep = SS.score(name, str(enc))
    finally:
        orig.to_csv(os.path.join(d, "runsheet.csv"), index=False)
    assert (rep.status.isin(["scored", "identified"])).all(), rep.status.tolist()
