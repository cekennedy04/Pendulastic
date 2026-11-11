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
    much. -> ({delay_ms: report indexed by sid}, sessions root, {150 | 87 | "mixed": encoder folder kept for
    the tests that score it again}). "mixed" is three runs 20 ms late and one (B-claspknife) 55 ms late."""
    import shutil
    root = tmp_path_factory.mktemp("delay"); sess = str(root / "sessions")
    old = SS.SESS
    try:
        SS.SESS = sess
        d = SS.plan("DELAY", sids=DELAY_SIDS, seed=1)
        key = [l.strip().split(",") for l in open(os.path.join(d, "key.csv"))][1:]
        reps, kept = {}, {}
        for ms in (0, 15, 20, 30, -30, 87, 150, "mixed"):
            enc = root / f"enc{ms}"; enc.mkdir()
            for rid, sid, _dur in key:
                a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
                late = (55 if sid == "B-claspknife" else 20) if ms == "mixed" else ms
                _write_log(str(enc / f"{rid}.bin"), a["setpoint_deg"], a["phase"], a["vel_dps"], late / 1000.0)
            reps[ms] = SS.score("DELAY", str(enc)).set_index("sid")
            if ms in (87, 150, "mixed"): kept[ms] = str(enc)
            else: shutil.rmtree(enc)
    finally:
        SS.SESS = old
    return reps, sess, kept


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
    is flagged with the reason and carries NO delivered score: there is no fallback to the commanded release.
    150 ms is beyond the search, so each run says the search stopped at its edge; -30 ms on every run is a
    session-level finding (the tests below)."""
    got = delayed[0][ms]
    why = "release flagged: lag +130 ms is the edge of the search" if ms == 150 else "release flagged: session lag finding: lag -30.0 ms"
    assert got.status.str.startswith(why).all(), got.status.tolist()
    assert "enc_pt7" not in got.columns or got.enc_pt7.isna().all()
    assert "enc_band" not in got.columns or got.enc_band.isna().all()
    assert not got.delivered_band_ok.astype(bool).any()
    assert np.isfinite(got.cmd_release_s).all() and np.isfinite(got.enc_release_lag_ms).all()      # what was measured is still recorded
    assert len(SS.release_flags(got.reset_index())) == len(got)


def _score_delay(delayed, which, *args, **kw):
    _reps, sess, kept = delayed
    old = SS.SESS
    try:
        SS.SESS = sess
        return SS.score("DELAY", kept[which], *args, **kw).set_index("sid")
    finally:
        SS.SESS = old


def test_score_exits_with_status_1_when_a_release_is_flagged(delayed):
    _reps, sess, kept = delayed
    old = SS.SESS
    try:
        SS.SESS = sess
        with pytest.raises(SystemExit) as ex:
            SS.main(["score", "--name", "DELAY", "--encoder-dir", kept[150]])
    finally:
        SS.SESS = old
    assert ex.value.code == 1


# ── the lag is judged across the session (review 2026-11-11, required change 2) ────────────────────────
def test_a_consistent_out_of_range_session_is_one_rig_finding_not_a_fault_per_run(delayed, capsys):
    """Every run 87 ms late (the review got this from +60 ms of transport delay: 67 of 67 flagged, each with a
    per-run message, nothing to say it was the rig). Still not scored, but said once, as what it is."""
    capsys.readouterr()
    got = _score_delay(delayed, 87); out = capsys.readouterr().out
    assert (np.abs(got.enc_release_lag_ms - 87) < LAG_TOL_ENCODER_MS).all()
    assert got.status.str.startswith("release flagged: session lag finding").all(), got.status.tolist()
    assert "enc_pt7" not in got.columns or got.enc_pt7.isna().all()
    msg = got.session_lag_finding.iloc[0]
    assert (got.session_lag_finding == msg).all() and abs(got.session_lag_median_ms.iloc[0] - 87) < LAG_TOL_ENCODER_MS
    for said in ("consistent across the session", "RIG", "not at one run", "check-log", "chirp", "--accept-session-lag-ms 87", "provisional"):
        assert said in msg, (said, msg)
    assert got.session_lag_override_ms.isna().all()
    assert out.count("SESSION LAG FINDING") == 1 and "RELEASE FLAGGED" not in out, out      # once, and no list of 4 "faults"
    assert "not scored for this reason: 4 run(s)" in out


def test_the_override_scores_such_a_session_and_is_written_into_the_report(delayed):
    import pandas as pd
    ref = delayed[0][0]
    got = _score_delay(delayed, 87, accept_session_lag_ms=87.0)
    assert (got.status == "scored").all(), got.status.tolist()
    assert got.enc_N.tolist() == ref.enc_N.tolist() and got.enc_band.tolist() == ref.enc_band.tolist()     # and scored right
    assert (got.session_lag_override_ms == 87.0).all()
    assert got.session_lag_finding.str.startswith("SESSION LAG OVERRIDE USED").all()
    assert "--accept-session-lag-ms 87" in got.session_lag_finding.iloc[0]
    back = pd.read_csv(os.path.join(delayed[1], "DELAY", "report.csv"))                                    # it is in the file
    assert (back.session_lag_override_ms == 87.0).all() and back.session_lag_finding.str.contains("OVERRIDE USED").all()


def test_the_override_must_name_the_lag_this_session_has(delayed):
    """Accepting 40 ms does not accept 87: the override is a statement about a measured value, not a switch."""
    got = _score_delay(delayed, 87, accept_session_lag_ms=40.0)
    assert got.status.str.startswith("release flagged: session lag finding").all(), got.status.tolist()
    assert got.session_lag_override_ms.isna().all() and "was not used" in got.session_lag_finding.iloc[0]


def test_an_override_that_is_not_needed_changes_nothing(delayed):
    got = _score_delay(delayed, "mixed", accept_session_lag_ms=87.0)
    assert got.session_lag_override_ms.isna().all() and "not needed" in got.session_lag_finding.iloc[0]
    assert got.status.tolist() == delayed[0]["mixed"].status.tolist()


def test_the_cli_takes_the_override(delayed):
    _reps, sess, kept = delayed
    old = SS.SESS
    try:
        SS.SESS = sess
        SS.main(["score", "--name", "DELAY", "--encoder-dir", kept[87], "--accept-session-lag-ms", "87"])   # exit status 0
        with pytest.raises(SystemExit) as ex:
            SS.main(["score", "--name", "DELAY", "--encoder-dir", kept[87]])
    finally:
        SS.SESS = old
    assert ex.value.code == 1


def test_one_run_far_from_the_session_median_is_the_run_that_is_flagged(delayed):
    """Three runs 20 ms late and one 55 ms late. 55 ms is inside the plausible range, so e2257cc scored it; a
    slow start looks like this (49 ms in a session at 27). The run is flagged, the session is not."""
    got = delayed[0]["mixed"]
    odd = got.loc["B-claspknife"]; rest = got.drop("B-claspknife")
    assert (rest.status == "scored").all(), rest.status.tolist()
    assert odd.status.startswith("release flagged: lag +55.0 ms is +35.0 ms from the session median +20.0 ms"), odd.status
    assert "this run, not the rig" in odd.status and not odd.delivered_band_ok
    assert abs(got.session_lag_median_ms.iloc[0] - 20) < LAG_TOL_ENCODER_MS and got.session_lag_runs.iloc[0] == 4
    assert (got.session_lag_finding.fillna("") == "").all()


def test_session_lag_rules():
    """_session_lag on its own: what counts as a session, as consistent, and as accepted."""
    tol = SS.SESSION_LAG_TOL_S
    S = SS._session_lag([0.027, 0.028, 0.026, 0.049])
    assert S["scorable"] and abs(S["reference_s"] - 0.0275) < 1e-9 and S["finding"] == ""
    assert SS._lag_verdict(0.049, S) != "" and SS._lag_verdict(0.027 + tol - 1e-4, S) == "" and SS._lag_verdict(0.027, S) == ""
    S = SS._session_lag([0.027, 0.150])                                  # two runs are not a session: each on its own
    assert S["reference_s"] is None and S["scorable"]
    assert SS._lag_verdict(0.027, S) == "" and "outside the plausible" in SS._lag_verdict(0.150, S)
    S = SS._session_lag([0.087, 0.088, 0.086, 0.087, 0.027])             # consistent enough: 4 of 5
    assert not S["scorable"] and "consistent across the session" in S["finding"]
    assert SS._lag_verdict(0.087, S).startswith("session lag finding") and SS._lag_verdict(0.027, S) != ""
    S = SS._session_lag([0.090, 0.120, 0.150, -0.030, 0.105])            # out of range and all over the place: nothing to accept
    assert S["scorable"] and S["reference_s"] is None and "without agreeing" in S["finding"]
    assert "outside the plausible" in SS._lag_verdict(0.120, S) and SS._lag_verdict(0.070, S) == ""
    S = SS._session_lag([0.087, 0.088, 0.086], accept_s=0.090)
    assert S["scorable"] and S["override_s"] == 0.090 and SS._lag_verdict(0.087, S) == "" and SS._lag_verdict(0.060, S) != ""
    assert not np.isfinite(SS._session_lag([])["median_s"])


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


# ── an ambiguous onset must fail (review 2026-11-11, required change 1) ───────────────────────────────
# Every delivery below was given a release, inside the plausible range and under the residual limit, by
# e2257cc. The faults are the review's (hold sag, hold ringing at the plant's resonance, D-wobble on a plant
# other than the identified one) plus a stall after the first degree, which is the two-minima case with a
# hold that IS still.
def _deliver(sid, seed=11, plant=None, fault=None, noise=0.02):
    """The encoder branch of session.score, in memory: command -> Player staircase -> plant (-> a fault added
    to the arm's true motion) -> encoder noise -> ilc.locate. fault(y, k, s) gets the log-length motion, the
    index of the commanded release in it and the direction of the fall."""
    import pandas as pd
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", f"{sid}.csv"))
    rng = np.random.default_rng(seed); pad0, pad1 = 1500, 1000
    held = DR.player_hold(sp, rng)
    spp = np.concatenate([np.zeros(pad0), held, np.full(pad1, held[-1])])
    y = DR.plant_response(spp, 1000.0, plant or DR.PLANT)
    k = int(np.argmax(ph == 2))
    if fault is not None: y = fault(y.copy(), pad0 + k, float(np.sign(sp[k + 300] - sp[k])))
    pos = y + rng.normal(0, noise, len(y))
    k0 = ilc.locate(sp, spp, 1000.0)
    pos_all = pos[k0:]; t_all = t[0] + np.arange(len(pos_all)) / 1000.0
    t_rel = float(t[k]); t_hold = float(t[np.argmax(ph == 1)])
    man = pd.read_csv(os.path.join(SS.OUT, "scenario_manifest.csv")).set_index("sid").loc[sid]
    return dict(t=t, sp=sp, ph=ph, t_all=t_all, pos_all=pos_all, pos=pos[k0:k0 + len(t)], t_rel=t_rel, lead=t_rel - t_hold + SS.SCORE_LEAD_S,
                tail=float(t[-1]) - t_rel, rest=float(man["rest_model_deg"]), int_N=float(man["N"]),
                int_band=man["mas"] if isinstance(man["mas"], str) else "refused")


def _release(d):
    return SS._delivered_release(d["t"], d["sp"], d["t_all"], d["pos_all"], d["t_rel"])


def _sag(deg, over_ms=300):
    """The hold creeps deg towards the fall over its last over_ms (the review's fault)."""
    def f(y, k, s):
        n = int(over_ms); y[k - n:k] += s * deg * (np.arange(n) / n) ** 2
        y[k:] += s * deg * np.exp(-np.arange(len(y) - k) / 150.0); return y
    return f


def _ringing(amp, f_hz, phase):
    """A decaying oscillation the command did not ask for, present through the hold (the review's fault)."""
    def f(y, k, s):
        n = np.arange(len(y)) - k
        env = np.where(n < 0, np.clip((n + 2000) / 500.0, 0, 1), np.exp(-np.clip(n, 0, None) / 250.0))
        return y + amp * env * np.sin(2 * np.pi * f_hz * n / 1000.0 + phase)
    return f


def _stall(at_deg, for_ms):
    """The arm leaves a still hold, stops for for_ms once it has moved at_deg, and carries on that much late."""
    def f(y, k, s):
        j = k + int(np.argmax(np.abs(y[k:] - y[k - 5]) >= at_deg)); n = int(for_ms)
        rest = y[j:].copy(); y[j:j + n] = y[j]; y[j + n:] = rest[:len(y) - j - n]; return y
    return f


@pytest.mark.parametrize("sid", ["A-P0", "B-claspknife", "R-friction"])
@pytest.mark.parametrize("deg", [1.0, 2.0])
def test_a_hold_that_sags_before_the_release_is_flagged(sid, deg):
    """e2257cc took the release 0.9 to 1.7 deg into the sag (residual 0.06 to 0.12 deg) and scored it in band."""
    rel = _release(_deliver(sid, fault=_sag(deg)))
    assert rel["kind"] == "hold" and "the hold is not still" in rel["flag"], rel
    assert rel["hold_rms_deg"] > SS.RELEASE_HOLD_STILL_MAX_DEG


@pytest.mark.parametrize("sid", ["A-P0", "B-claspknife", "M-tone_L2", "R-friction", "D-short"])
def test_a_hold_that_rings_is_flagged_at_every_phase(sid):
    """Half a degree at the plant's own 6.35 Hz moved e2257cc's release by up to 21 ms and changed the band in
    4 of 40 runs with nothing flagged. All 40 are flagged now, whatever the phase at the release."""
    for phase in np.arange(8) * np.pi / 4:
        rel = _release(_deliver(sid, fault=_ringing(0.5, DR.PLANT["wn_hz"], phase)))
        assert rel["kind"] == "hold", (sid, phase, rel)


@pytest.mark.parametrize("plant", [None, dict(wn_hz=6.35 * 0.85, zeta=0.23, delay_ms=20.0), dict(wn_hz=6.35 * 0.8, zeta=0.23, delay_ms=30.0)])
def test_d_wobble_is_reported_as_release_uncertain_on_any_plant(plant):
    """D-wobble commands a hold that moves 3 deg at 2.5 Hz. e2257cc scored it 6 to 10 ms late on the identified
    plant and 60 ms EARLY (lag -9.7 to +0.6 ms, inside the plausible range) on a slower plant with 20 to 30 ms
    of delay. Its release is not knowable from the onset; it says so instead."""
    for seed in (11, 3, 42):
        rel = _release(_deliver("D-wobble", seed=seed, plant=plant))
        assert rel["flag"].startswith("release uncertain"), (seed, rel)


def test_an_onset_with_two_candidate_releases_is_flagged():
    """A still hold, a lag inside the plausible range and a residual under the limit, and still not a release:
    the arm stalls for 30 ms after its first degree, so a release 10 ms or more away fits nearly as well."""
    for sid in ("A-P0", "M-tone_L2"):
        rel = _release(_deliver(sid, fault=_stall(1.0, 30)))
        lo, hi = SS.RELEASE_LAG_PLAUSIBLE_S
        assert lo <= rel["lag_s"] <= hi and rel["resid_deg"] < SS.RELEASE_RESID_MAX_DEG, rel
        assert rel["hold_rms_deg"] < SS.RELEASE_HOLD_STILL_MAX_DEG, rel
        assert rel["kind"] == "ambiguous" and rel["rival_ratio"] < SS.RELEASE_UNIQUE_MIN_RATIO, rel


@pytest.mark.parametrize("plant", [None, dict(wn_hz=6.35 * 0.7, zeta=0.23, delay_ms=0.0), dict(wn_hz=6.35 * 1.3, zeta=0.23 * 0.7, delay_ms=0.0)])
def test_the_ambiguity_checks_pass_a_clean_delivery_with_room_to_spare(plant):
    """The other half: the checks must not flag a good run. These are the scenarios nearest each limit (D-push
    and C-f-high have the largest hold rms, S-lam2 and D-push the weakest minimum), on the identified plant
    and at both ends of +-30 % bandwidth."""
    for sid in ("A-P0", "D-push", "S-lam2", "C-f-high", "C-wmax-high", "M-catch_release_high"):
        rel = _release(_deliver(sid, plant=plant))
        assert rel["flag"] == "", (sid, rel)
        assert rel["hold_rms_deg"] < 0.5 * SS.RELEASE_HOLD_STILL_MAX_DEG, (sid, rel)
        assert rel["rival_ratio"] > 1.2 * SS.RELEASE_UNIQUE_MIN_RATIO, (sid, rel)


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
