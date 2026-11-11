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
    # The catch run is K-HM1, not M-catch_release_high: on the scorer with #79's HEALTHY_REF the latter
    # sits at PT7 0.139, 0.02 above the 0/1 edge, and the plant delivers it at 0.128 -- in band since the
    # delivered release is used (0.117, band 0, before), but 0.008 from the edge. K-HM1's H was fitted to
    # the middle of its band and holds it through the plant with room to spare (0.175 -> 0.169).
    return DR.run(["A-P0", "A-P1p", "K-HM1"], seed=3, keep=True)


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
    for sid in ("C-f-high", "A-P1p", "M-fall_no_ringdown"):
        t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", f"{sid}.csv"))
        for pre in (0, 1234, 2500):
            log = np.concatenate([np.zeros(pre), sp, np.full(800, sp[-1])])
            assert ilc.locate(sp, log) == pre, (sid, pre, ilc.locate(sp, log))


def test_every_run_delivers_its_intended_band(report):
    sc = report[report.sid != "R-chirp"]
    assert sc.delivered_band_ok.all(), sc[["sid", "int_band", "enc_band"]].values.tolist()


def test_default_session_delivers_every_band_and_the_edge_scenarios_stay_on_their_edge(tmp_path):
    """Was test_default_session_misses_exactly_the_two_edge_scenarios (7 of 9, pinned by #89).

    B-claspknife and M-catch_release_high (one scenario built twice) intend PT7 0.139, 0.02 above the 0/1
    edge, and were delivered at ~0.116, band 0. More than half of that undershoot was the session's own:
    it scored the delivered trace from the COMMANDED release, the hold left inside the record was counted
    as a half-oscillation (N 6.0 -> 6.5), and that alone cost 0.013. Scored from the delivered release N
    is the intended 6.0 and both runs are in band at ~0.128.

    Pinned both ways, so neither half can drift unnoticed: every default run is in its band, AND the two
    catch runs still undershoot by the plant's real share and still sit within 0.012 of the edge. A change
    that takes them back out of band, or one that makes them look comfortably mid-band, fails here."""
    old = SS.SESS
    try:
        SS.SESS = str(tmp_path)
        rep = DR.run(DR.DEFAULT_SIDS, seed=3, keep=False)[0]
    finally:
        SS.SESS = old
    sc = rep[rep.sid != "R-chirp"]
    assert (sc.status == "scored").all(), sc.status.tolist()
    assert sc.delivered_band_ok.astype(bool).all(), sc[["sid", "int_band", "enc_band"]].values.tolist()
    edge = sc[sc.sid.isin(["B-claspknife", "M-catch_release_high"])]
    assert len(edge) == 2 and (edge.enc_N == edge.int_N).all(), edge[["sid", "int_N", "enc_N"]].values.tolist()
    assert ((edge.enc_pt7 > 0.12) & (edge.enc_pt7 < 0.132)).all(), edge[["sid", "int_pt7", "enc_pt7"]].values.tolist()
    assert (edge.int_pt7 - edge.enc_pt7 > 0.005).all()                 # the plant's own undershoot is still there


# ── the delivered release (session._delivered_release) ───────────────────────────────────────────────
# A-P0, B-claspknife and M-tone_L2 are the three the 2026-11-10 diagnosis showed gaining a half-cycle from a
# pure delay scored at the commanded release; C-N-high still rings when the record ends, so it is the one
# that would notice the delivered motion's tail being cut short.
DELAY_SIDS = ["A-P0", "B-claspknife", "M-tone_L2", "C-N-high"]
LAG_TOL_ENCODER_MS = 0.5        # 1 kHz log, pure delay
LAG_TOL_PHONE_RATE_MS = 3.0     # the same trace seen at ~59 Hz with jitter, a 2 % gain error and an offset
#                                 (measured over all 67 scenarios x 4 delays: median 0.13, p95 0.79, max 2.87 ms)


def _write_log(path, sp, phase, vel, delay_s, pre_s=1.5, post_s=1.0, fs=1000.0):
    """A LabVIEW log whose setpoint channel is the command and whose position is that command delayed by
    delay_s and otherwise untouched: a delivery whose release is known exactly."""
    pad0, pad1 = int(pre_s * fs), int(post_s * fs)
    spp = np.concatenate([np.zeros(pad0), sp, np.full(pad1, sp[-1])]); tt = np.arange(len(spp)) / fs
    pos = np.interp(tt - delay_s, tt, spp)
    ch = np.vstack([pos, np.gradient(pos, 1 / fs), spp, np.zeros_like(spp), np.concatenate([-np.ones(pad0), phase, -np.ones(pad1)]),
                    np.zeros_like(spp), np.concatenate([np.zeros(pad0), vel, np.zeros(pad1)])])
    ch.T.astype(">f8").tofile(path)


@pytest.fixture(scope="module")
def delayed(tmp_path_factory):
    """One planned session, scored once per delay with every run delivered as its own command, late by that
    much. -> ({delay_ms: report indexed by sid}, sessions root, the 150 ms encoder folder)."""
    import shutil
    root = tmp_path_factory.mktemp("delay"); sess = str(root / "sessions")
    old = SS.SESS
    try:
        SS.SESS = sess
        d = SS.plan("DELAY", sids=DELAY_SIDS, seed=1)
        rids = [l.split(",")[0] for l in open(os.path.join(d, "key.csv"))][1:]
        reps = {}
        for ms in (0, 15, 20, 30, -30, 150):
            enc = root / f"enc{ms}"; enc.mkdir()
            for rid in rids:
                a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
                _write_log(str(enc / f"{rid}.bin"), a["setpoint_deg"], a["phase"], a["vel_dps"], ms / 1000.0)
            reps[ms] = SS.score("DELAY", str(enc)).set_index("sid")
            if ms != 150: shutil.rmtree(enc)
    finally:
        SS.SESS = old
    return reps, sess, str(root / "enc150")


def test_an_undelayed_command_scores_as_the_scenario(delayed):
    """The reference the delay tests compare with: the command itself, read back as a log, is the scenario."""
    ref = delayed[0][0]
    assert (ref.status == "scored").all(), ref.status.tolist()
    assert ref.enc_N.tolist() == ref.int_N.tolist() and ref.enc_band.tolist() == ref.int_band.tolist()


@pytest.mark.parametrize("ms", [15, 20, 30])
def test_a_pure_delay_changes_neither_N_nor_the_band(delayed, ms):
    """A delivery that is only late is the same motion. Scored from the commanded release it was not: the
    hold left inside the record was counted as a half-oscillation (the next test)."""
    ref, got = delayed[0][0], delayed[0][ms]
    assert (got.status == "scored").all(), got.status.tolist()
    assert got.enc_N.tolist() == ref.enc_N.tolist(), (got.enc_N.tolist(), ref.enc_N.tolist())
    assert got.enc_band.tolist() == ref.enc_band.tolist()
    assert np.max(np.abs(got.enc_pt7 - ref.enc_pt7)) < 1e-3, (got.enc_pt7 - ref.enc_pt7).tolist()


def test_the_commanded_release_counts_a_late_delivery_as_an_extra_half_cycle():
    """What the alignment is for, and the proof that the test above can fail: B-claspknife's own command,
    20 ms late, scored from the commanded release gains half a cycle; from its own release it does not."""
    import pandas as pd
    man = pd.read_csv(os.path.join(SS.OUT, "scenario_manifest.csv")).set_index("sid")
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", "B-claspknife.csv"))
    t_rel = float(t[np.argmax(ph == 2)]); lead = t_rel - float(t[np.argmax(ph == 1)]) + SS.SCORE_LEAD_S
    late = np.interp(t - 0.020, t, sp); rest = float(man.loc["B-claspknife", "rest_model_deg"])
    N = lambda rel: SS._summ(*SS._score_trace(t, late, rest, rel, lead))["N"]
    assert N(t_rel) == man.loc["B-claspknife", "N"] + 0.5
    assert N(t_rel + 0.020) == man.loc["B-claspknife", "N"]


@pytest.mark.parametrize("ms", [0, 15, 20, 30])
def test_the_reported_lag_is_the_injected_lag(delayed, ms):
    got = delayed[0][ms]
    assert (np.abs(got.enc_release_lag_ms - ms) < LAG_TOL_ENCODER_MS).all(), got.enc_release_lag_ms.tolist()
    assert np.allclose(got.enc_release_s - got.cmd_release_s, got.enc_release_lag_ms / 1e3, atol=1e-9)   # the audit trail adds up


def test_the_lag_is_recovered_at_the_phones_sample_rate():
    """The phone sees the release through ~59 Hz samples with jitter, and its angle carries a mount gain and
    an integration offset. Neither may move the release by more than LAG_TOL_PHONE_RATE_MS."""
    rng = np.random.default_rng(4)
    for sid in ("A-P0", "B-claspknife", "C-f-high"):
        t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", f"{sid}.csv"))
        t_rel = float(t[np.argmax(ph == 2)])
        for ms in (0.0, 8.0, 19.0, 33.0):
            tq = np.cumsum(1 / 59.0 + rng.normal(0, 0.0015, int(t[-1] * 59) - 2)) + rng.uniform(0, 1 / 59.0)
            x = 0.98 * np.interp(tq - ms / 1e3, t, sp) + 1.7
            rel = SS._delivered_release(t, sp, tq, x, t_rel)
            assert rel["flag"] == "" and abs(rel["lag_s"] * 1e3 - ms) < LAG_TOL_PHONE_RATE_MS, (sid, ms, rel)


@pytest.mark.parametrize("ms", [-30, 150])
def test_an_implausible_lag_flags_the_run_and_does_not_score_it(delayed, ms):
    """An arm that moves before its command, or 150 ms after it, is not a delivery of that command. The run
    is flagged with the reason and carries NO delivered score: there is no fallback to the commanded release."""
    got = delayed[0][ms]
    assert got.status.str.startswith("release flagged: lag ").all(), got.status.tolist()
    assert "enc_pt7" not in got.columns or got.enc_pt7.isna().all()
    assert "enc_band" not in got.columns or got.enc_band.isna().all()
    assert not got.delivered_band_ok.astype(bool).any()
    assert np.isfinite(got.cmd_release_s).all() and np.isfinite(got.enc_release_lag_ms).all()      # what was measured is still recorded
    assert len(SS.release_flags(got.reset_index())) == len(got)


def test_score_exits_with_status_1_when_a_release_is_flagged(delayed):
    _reps, sess, enc150 = delayed
    old = SS.SESS
    try:
        SS.SESS = sess
        with pytest.raises(SystemExit) as ex:
            SS.main(["score", "--name", "DELAY", "--encoder-dir", enc150])
    finally:
        SS.SESS = old
    assert ex.value.code == 1


def test_a_delivery_that_is_not_the_command_is_flagged():
    """Inside the plausible lag range the onset must still look like the command's: an arm that never left
    the hold, and one that went the wrong way, are flagged rather than given a release."""
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", "A-P0.csv"))
    k = int(np.argmax(ph == 2)); t_rel = float(t[k])
    stuck = sp.copy(); stuck[k:] = sp[k]
    wrong_way = sp.copy(); wrong_way[k:] = 2 * sp[k] - sp[k:]
    for x in (stuck, wrong_way):
        assert SS._delivered_release(t, sp, t, x, t_rel)["flag"] != ""
    assert SS._delivered_release(t, sp, t, sp, t_rel)["flag"] == ""


def test_a_phone_that_disagrees_with_the_encoder_is_flagged():
    """Both sensed one arm. A phone release more than PHONE_SYNC_TOL_S from the encoder's means the sync is
    off, and the phone trace is not scored on it."""
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", "A-P0.csv"))
    t_rel = float(t[np.argmax(ph == 2)]); late = np.interp(t - 0.045, t, sp)
    assert "sync is off" in SS._delivered_release(t, sp, t, late, t_rel, enc_lag_s=0.045 - SS.PHONE_SYNC_TOL_S - 0.003)["flag"]
    assert SS._delivered_release(t, sp, t, late, t_rel, enc_lag_s=0.045 - SS.PHONE_SYNC_TOL_S + 0.003)["flag"] == ""


def test_the_release_is_recorded_for_every_scored_trace(report):
    """Commanded release, delivered release and lag, for the encoder and for the phone, in report.csv."""
    sc = report[report.sid != "R-chirp"]
    lo, hi = (1e3 * v for v in SS.RELEASE_LAG_PLAUSIBLE_S)
    assert np.isfinite(sc.cmd_release_s).all()
    assert ((sc.enc_release_lag_ms >= lo) & (sc.enc_release_lag_ms <= hi)).all(), sc.enc_release_lag_ms.tolist()
    assert np.allclose(sc.enc_release_s - sc.cmd_release_s, sc.enc_release_lag_ms / 1e3, atol=1e-9)
    assert np.allclose(sc.ph_release_s - sc.cmd_release_s, sc.ph_release_lag_ms / 1e3, atol=1e-9)
    assert np.allclose(sc.ph_minus_enc_lag_ms, sc.ph_release_lag_ms - sc.enc_release_lag_ms, atol=1e-6)
    assert (sc.ph_minus_enc_lag_ms.abs() <= 1e3 * SS.PHONE_SYNC_TOL_S).all(), sc.ph_minus_enc_lag_ms.tolist()
    assert not SS.release_flags(report)


def test_plan_refuses_an_unknown_repeat_scenario_before_writing(tmp_path):
    old = SS.SESS
    try:
        SS.SESS = str(tmp_path)
        with pytest.raises(SystemExit, match="unknown scenario ids"):
            SS.plan("REPEAT-CHECK", sids=["A-P0"], repeat="A-NOPE:3")
        assert not os.listdir(tmp_path)
    finally:
        SS.SESS = old


def test_ilc_converges_through_the_real_tools(session):
    rep, d = session
    rid = rep[rep.sid == "A-P1p"].run_id.iloc[0]
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


def _one_run(session):
    rep, d = session
    rid = rep[rep.sid == "A-P1p"].run_id.iloc[0]
    return d, rid, os.path.join(d, "player", rid + ".csv")


def test_check_log_passes_a_log_that_recorded_the_player(session):
    d, rid, player = _one_run(session)
    r = SS.check_log(os.path.join(d, "synthetic_encoder", rid + ".bin"), player, verbose=False)
    assert r["ok"], r["reason"]
    assert 9.0 <= r["update_ms"] <= 14.0, r["update_ms"]        # the Player's ~10 ms writes, seen in the log


def test_check_log_fails_a_log_that_never_saw_the_player(session, tmp_path):
    d, rid, player = _one_run(session)
    n = len(np.loadtxt(player, delimiter=","))
    flat = str(tmp_path / "flat.bin")
    DR.write_encoder_bin(flat, np.zeros(n), np.zeros(n), np.zeros(n), 0, np.random.default_rng(0))
    r = SS.check_log(flat, player, verbose=False)
    assert not r["ok"] and "did not see" in r["reason"], r["reason"]


def test_check_log_fails_the_wrong_file_and_a_short_log(session, tmp_path):
    rep, d = session
    d, rid, player = _one_run(session)
    other = rep[rep.sid == "K-HM1"].run_id.iloc[0]
    r = SS.check_log(os.path.join(d, "synthetic_encoder", other + ".bin"), player, verbose=False)
    assert not r["ok"], r["reason"]
    sp = np.loadtxt(player, delimiter=",")[:, 1]; k = int(0.6 * len(sp))
    short = str(tmp_path / "short.bin")
    DR.write_encoder_bin(short, sp[:k], np.gradient(sp[:k], 0.001), np.zeros(k), 0, np.random.default_rng(1), post_s=0.0)
    r = SS.check_log(short, player, verbose=False)
    assert not r["ok"] and "stopped" in r["reason"], r["reason"]


def test_ilc_output_is_playable_and_finds_its_scenario_from_a_run_id(session, tmp_path):
    """A session command is named by run id; ILC resolves the scenario through key.csv and writes the
    Player's file into the session's player/ folder."""
    d, rid, _player = _one_run(session)
    import shutil
    work = tmp_path / "S"; shutil.copytree(d, work)
    cmd = str(work / "commands" / f"{rid}.csv")
    ilc.main([cmd, str(work / "synthetic_encoder" / f"{rid}.bin")])
    nxt = work / "player" / f"{rid}_ilc1.csv"
    x = np.loadtxt(nxt, delimiter=",")
    assert x.shape[1] == 2 and x[0, 1] == 0.0
    assert ilc.desired_for(cmd, rid).endswith("A-P1p.csv")
