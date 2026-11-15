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
    # REGRESSION PIN, not a principle: the catch runs undershoot their intended PT7 by the plant's own share
    # (its resonance raises peak velocity), measured at 0.010 to 0.012 at seeds 1, 3, 7, 11 and 42 after the
    # delivered release took the session's share (0.013) out. 0.008 to 0.014 is that measurement +-0.003. It
    # pins "still an edge run": below 0.008 something is flattering the delivery, above 0.014 the session is
    # adding undershoot of its own again. (Being in band, asserted above, is the 0.12 edge; the old
    # `< 0.132` was the same pin written as an absolute number.) Re-baseline it if the scorer or plant changes.
    under = edge.int_pt7 - edge.enc_pt7
    assert ((under > 0.008) & (under < 0.014)).all(), edge[["sid", "int_pt7", "enc_pt7"]].values.tolist()
    # The METHOD, pinned on this plant: the onset-matched release is 27.4 ms after the commanded one here
    # (22.6 to 28.5 by scenario). An onset of 10 deg instead of 2 gives 19.7 ms and passed every test in this
    # file before this assertion existed; a whole-drop cross-correlation gives 14.5 to 19.
    assert 26.4 < sc.enc_release_lag_ms.median() < 28.4, sc.enc_release_lag_ms.tolist()
    assert ((sc.enc_release_lag_ms > 21.5) & (sc.enc_release_lag_ms < 29.5)).all(), sc.enc_release_lag_ms.tolist()


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
    why = "release flagged: lag +130 ms is the edge of the search" if ms == 150 else "release flagged: session lag finding: the session's median lag -30.0 ms is outside the plausible -10 to +80 ms; this run's lag -30.0 ms is with the session"
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
    # Re-review 2026-11-12, 4.2: it is the session's median that is outside the range. A +27 ms run in that
    # session was told that its own lag "is outside the plausible -10 to +80 ms", which is false.
    odd, with_it = SS._lag_verdict(0.027, S), SS._lag_verdict(0.088, S)
    assert "the session's median lag +87.0 ms is outside the plausible -10 to +80 ms" in odd and "median lag +87.0 ms is outside" in with_it
    assert "this run's lag +27.0 ms is -60.0 ms from the session's median" in odd and "odd run" in odd, odd
    assert "this run's lag +88.0 ms is with the session" in with_it, with_it
    assert "lag +27.0 ms is outside" not in odd and "+27.0 ms, with" not in odd, odd
    assert "chirp identification EXPLAINS" in S["finding"] and "still held to the tracking gate" in S["finding"], S["finding"]
    S = SS._session_lag([0.090, 0.120, 0.150, -0.030, 0.105])            # out of range and all over the place: nothing to accept
    assert S["scorable"] and S["reference_s"] is None and "without agreeing" in S["finding"]
    assert "outside the plausible" in SS._lag_verdict(0.120, S) and SS._lag_verdict(0.070, S) == ""
    S = SS._session_lag([0.087, 0.088, 0.086], accept_s=0.090)
    assert S["scorable"] and S["override_s"] == 0.090 and SS._lag_verdict(0.087, S) == "" and SS._lag_verdict(0.060, S) != ""
    assert "still held to the tracking gate" in S["finding"], S["finding"]
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


# ── the phone's gate (review 2026-11-11, required change 3) ────────────────────────────────────────────
def _phone_view(rate_hz, early_ms, sid="A-P0", seed=4, jitter_s=0.0015):
    """A-P0's command as a phone would hand it over: sampled at rate_hz with jitter, and early_ms EARLY (what
    the pulse alignment leaves on a coarse grid). -> the arguments of _delivered_release, bar enc_lag_s."""
    rng = np.random.default_rng(seed)
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", f"{sid}.csv"))
    tq = np.cumsum(1.0 / rate_hz + rng.normal(0, jitter_s, int(t[-1] * rate_hz) - 2)) + rng.uniform(0, 1.0 / rate_hz)
    return t, sp, tq, np.interp(tq + early_ms / 1e3, t, sp), float(t[np.argmax(ph == 2)])


def test_a_phone_trace_is_not_flagged_for_reading_early_when_the_arm_tracks_well():
    """After ILC the arm is level with the scenario (encoder lag about 0) and a 59 Hz phone then reads 8 to
    20 ms EARLY: e2257cc flagged it at the absolute -10 ms limit, so the better the rig tracked, the fewer
    phone traces were scored. The phone is held to the encoder, about where a 59 Hz phone is expected."""
    for early in (8.0, 12.0, 16.0, 20.0):
        rel = SS._delivered_release(*_phone_view(59.0, early), enc_lag_s=0.001)
        assert abs(rel["lag_s"] * 1e3 + early) < LAG_TOL_PHONE_RATE_MS and rel["flag"] == "", (early, rel)


@pytest.mark.parametrize("rate_hz", [30.0, 59.0, 100.0, 200.0])
def test_the_phone_gate_is_centred_on_the_sample_period_offset_and_scales_with_it(rate_hz):
    """Not a wider gate: one that moves with the sample period. The expected offset is PHONE_EARLY_SAMPLES
    periods early; PHONE_SYNC_TOL_SAMPLES periods either side of THAT pass and anything beyond is a sync fault.
    So 20 ms early is expected of a 30 Hz phone and is a fault on a 200 Hz one."""
    dt = 1e3 / rate_hz; centre = SS.PHONE_EARLY_SAMPLES * dt; tol = SS.PHONE_SYNC_TOL_SAMPLES * dt
    enc = 0.020
    def flag(early):          # phone reads `early` ms before the encoder's release
        r = SS._delivered_release(*_phone_view(rate_hz, early - enc * 1e3), enc_lag_s=enc)
        assert abs(r["expected_offset_s"] * 1e3 + centre) < 0.1 * dt, r
        return r["flag"]
    margin = LAG_TOL_PHONE_RATE_MS + 0.1 * dt        # the lag itself is only known this well at this rate
    assert flag(centre) == "" and flag(centre - tol + margin) == "" and flag(centre + tol - margin) == ""
    assert "the sync is off" in flag(centre + tol + margin) and "the sync is off" in flag(centre - tol - margin)
    assert (flag(20.0) == "") == (abs(20.0 - centre) < tol), (rate_hz, flag(20.0))
    if rate_hz == 30.0: assert flag(20.0) == ""
    if rate_hz == 200.0: assert "the sync is off" in flag(20.0)


def test_the_phone_gate_centre_is_pinned_in_milliseconds():
    """Literal numbers, not SS.PHONE_EARLY_SAMPLES (re-review 2026-11-12, 2.3: with the constant set to 0 every
    other phone test still passed, because each computes its expectation from the constant). At 59 Hz the gate
    is 10.2 ms early of the encoder, 25.4 ms either side: -35.6 to +15.3 ms. So a phone 30 ms EARLY of the
    encoder passes and one 20 ms LATE is a sync fault. Centred on 0 (-25.4 to +25.4) it is the other way round."""
    enc = 0.020
    def rel(phone_minus_enc_ms):
        return SS._delivered_release(*_phone_view(59.0, -(enc * 1e3 + phone_minus_enc_ms)), enc_lag_s=enc)
    early, late, on_centre = rel(-30.0), rel(+20.0), rel(-10.0)
    assert abs((early["lag_s"] - enc) * 1e3 + 30.0) < LAG_TOL_PHONE_RATE_MS and abs((late["lag_s"] - enc) * 1e3 - 20.0) < LAG_TOL_PHONE_RATE_MS
    assert early["flag"] == "", early
    assert late["kind"] == "phone" and "the sync is off" in late["flag"], late
    assert on_centre["flag"] == "" and abs(on_centre["expected_offset_s"] * 1e3 + 10.2) < 0.4, on_centre
    assert SS.PHONE_EARLY_SAMPLES == 0.6 and SS.PHONE_SYNC_TOL_SAMPLES == 1.5


def test_an_encoder_trace_is_still_held_to_the_plausible_range():
    """Only the phone lost the absolute limit. With no encoder lag to compare with, -30 ms is still flagged."""
    t, sp, tq, x, t_rel = _phone_view(59.0, 30.0)
    assert SS._delivered_release(t, sp, tq, x, t_rel)["kind"] == "lag"


def test_the_release_is_recorded_for_every_scored_trace(report):
    """Commanded release, delivered release and lag, for the encoder and for the phone, in report.csv."""
    sc = report[report.sid != "R-chirp"]
    lo, hi = (1e3 * v for v in SS.RELEASE_LAG_PLAUSIBLE_S)
    assert np.isfinite(sc.cmd_release_s).all()
    assert ((sc.enc_release_lag_ms >= lo) & (sc.enc_release_lag_ms <= hi)).all(), sc.enc_release_lag_ms.tolist()
    assert np.allclose(sc.enc_release_s - sc.cmd_release_s, sc.enc_release_lag_ms / 1e3, atol=1e-9)
    assert np.allclose(sc.ph_release_s - sc.cmd_release_s, sc.ph_release_lag_ms / 1e3, atol=1e-9)
    assert np.allclose(sc.ph_minus_enc_lag_ms, sc.ph_release_lag_ms - sc.enc_release_lag_ms, atol=1e-6)
    dt_ms = 1e3 / 59.0                                                       # the dry run's phone
    assert np.allclose(sc.ph_expected_offset_ms, -SS.PHONE_EARLY_SAMPLES * dt_ms, atol=0.5), sc.ph_expected_offset_ms.tolist()
    assert ((sc.ph_minus_enc_lag_ms - sc.ph_expected_offset_ms).abs() <= SS.PHONE_SYNC_TOL_SAMPLES * dt_ms).all(), sc.ph_minus_enc_lag_ms.tolist()
    assert not SS.release_flags(report)


# ── the method is pinned (review 2026-11-11, required change 5) ────────────────────────────────────────
def test_the_onset_method_is_pinned_across_the_whole_library():
    """All 67 scenarios through the dry run's delivery (in memory, seed 11), scored from the delivered release.

    This is what tells the 2 deg onset from a drifted method. The nine default runs and the pure-delay cases
    do not: with RELEASE_ONSET_DEG = 10 they all still pass, while here the median lag falls from 27.4 to
    19.7 ms, 11 scenarios are flagged and 15 of the other 56 get a wrong N. (N alone does not tell 2 deg from
    a CONSTANT +30 to +50 ms on this plant; the lag assertions do, and the plant sweep in the report does.)
    The counts are regression pins measured on this library: re-baseline them when the scorer, the scenario
    library or dryrun.PLANT changes, not otherwise."""
    import pandas as pd
    man = pd.read_csv(os.path.join(SS.OUT, "scenario_manifest.csv"))
    lags, flagged, wrong_N, wrong_band = {}, {}, [], []
    for sid in man.sid:
        d = _deliver(sid, seed=11); rel = _release(d)
        if rel["flag"]: flagged[sid] = rel["kind"]; continue
        lags[sid] = rel["lag_s"] * 1e3
        got = SS._summ(*SS._score_trace(d["t_all"], d["pos_all"], d["rest"], rel["release_s"], d["lead"], d["tail"]))
        if got["N"] != d["int_N"]: wrong_N.append(sid)
        if got["band"] != d["int_band"]: wrong_band.append(sid)
    assert flagged == {"D-wobble": "hold"}, flagged                       # the one scenario whose hold moves; nothing else
    v = np.array(list(lags.values())); med = float(np.median(v))
    assert 26.9 < med < 27.9, med                                         # measured 27.4
    assert v.min() > 21.5 and v.max() < 29.5, (v.min(), v.max())          # measured 22.6 to 28.4
    assert np.abs(v - med).max() < SS.SESSION_LAG_TOL_S * 1e3             # so no good run is far from its session
    assert wrong_band == [], wrong_band                                   # measured 0 of 66
    assert len(wrong_N) <= 10, wrong_N                                    # measured 10 of 66 (44 from the commanded release)


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


# ── tracking error is gated (review 2026-11-11, required change 4) ─────────────────────────────────────
SLOW_SIDS = ["A-P0", "B-claspknife", "M-tone_L2", "R-friction", "B-flexor", "D-short", "M-tone_L4", "A-P1p"]    # the review's eight


def _slow_start(gain0=0.3, over_ms=600):
    """The review's slow start: the arm delivers gain0 of its motion at the release, recovering linearly over
    over_ms. Tracking error 1.6 to 6.8 deg, and e2257cc reported 8 of 8 in band with no flag."""
    def f(y, k, s):
        n = int(over_ms); g = np.ones(len(y) - k); g[:n] = gain0 + (1 - gain0) * (np.arange(n) / n)
        y[k:] = y[k - 5] + (y[k:] - y[k - 5]) * g; return y
    return f


def _write_arm_log(path, sp, phase, vel, seed, fault=None, pre_s=1.5, post_s=1.0, fs=1000.0):
    """dryrun.write_encoder_bin with a fault added to the arm's motion: Player staircase -> identified plant
    (-> fault) -> 0.02 deg encoder noise."""
    rng = np.random.default_rng(seed); pad0, pad1 = int(pre_s * fs), int(post_s * fs)
    held = DR.player_hold(sp, rng, fs); spp = np.concatenate([np.zeros(pad0), held, np.full(pad1, held[-1])])
    y = DR.plant_response(spp, fs); k = int(np.argmax(phase == 2))
    if fault is not None: y = fault(y.copy(), pad0 + k, float(np.sign(sp[k + 300] - sp[k])))
    pos = y + rng.normal(0, 0.02, len(y))
    ch = np.vstack([pos, np.gradient(pos, 1 / fs), spp, np.zeros_like(spp), np.concatenate([-np.ones(pad0), phase, -np.ones(pad1)]),
                    np.zeros_like(spp), np.concatenate([np.zeros(pad0), vel, np.zeros(pad1)])])
    ch.T.astype(">f8").tofile(path)


@pytest.fixture(scope="module")
def slow(tmp_path_factory):
    """The review's eight scenarios through the identified plant, scored three ways: every arm healthy, every
    arm starting slowly, and one arm (B-claspknife) starting slowly among seven healthy ones.
    -> ({"good" | "all" | "one": report indexed by sid}, sessions root, the "all" encoder folder)."""
    root = tmp_path_factory.mktemp("slow"); sess = str(root / "sessions")
    old = SS.SESS
    try:
        SS.SESS = sess
        d = SS.plan("SLOW", sids=SLOW_SIDS, seed=1)
        key = [l.strip().split(",") for l in open(os.path.join(d, "key.csv"))][1:]
        reps = {}
        for which in ("good", "all", "one"):
            enc = root / which; enc.mkdir()
            for j, (rid, sid, _dur) in enumerate(key):
                a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
                faulty = which == "all" or (which == "one" and sid == "B-claspknife")
                _write_arm_log(str(enc / f"{rid}.bin"), a["setpoint_deg"], a["phase"], a["vel_dps"], 11 + j, _slow_start() if faulty else None)
            reps[which] = SS.score("SLOW", str(enc)).set_index("sid")
    finally:
        SS.SESS = old
    return reps, sess, str(root / "all")


def test_tracking_error_is_in_the_report_and_a_good_run_is_well_inside_the_limit(slow):
    good = slow[0]["good"]
    assert (good.status == "scored").all(), good.status.tolist()
    assert (good.tracking_limit_ms == SS.TRACKING_TIME_ERR_MAX_MS).all()
    assert np.isfinite(good.tracking_rmse_deg).all() and np.isfinite(good.tracking_aligned_rmse_deg).all()
    # measured on these eight in memory: 13.9 to 14.3 ms; slow start 32.4 to 38.5 (all 67, three seeds: at most 19.2)
    assert (good.tracking_time_err_ms > 9).all() and (good.tracking_time_err_ms < 0.8 * SS.TRACKING_TIME_ERR_MAX_MS).all(), good.tracking_time_err_ms.tolist()


def test_a_slow_start_is_flagged_for_tracking_and_is_not_a_delivery(slow):
    """The review's case, all eight. e2257cc: 8 of 8 scored and in band. The release is fine (one clean
    minimum, lag consistent across the session at about 49 ms, inside the plausible range), and the scorer
    does not see the fault, so this is the only gate that can."""
    bad = slow[0]["all"]
    assert bad.status.str.startswith("tracking flagged: tracking error ").all(), bad.status.tolist()
    assert (bad.tracking_time_err_ms > SS.TRACKING_TIME_ERR_MAX_MS).all(), bad.tracking_time_err_ms.tolist()
    assert not bad.delivered_band_ok.astype(bool).any()
    assert bad.enc_band.notna().all() and np.isfinite(bad.enc_pt7).all()          # what the arm did is still scored and kept
    assert (bad.enc_band == bad.int_band).sum() >= 6                              # ... and it IS in band: the reason this gate exists
    assert len(SS.tracking_flags(bad.reset_index())) == len(bad) and not SS.release_flags(bad.reset_index())
    assert (bad.tracking_rmse_deg > 1.5).all() and (slow[0]["good"].tracking_rmse_deg.max() > bad.tracking_rmse_deg.min())   # degrees alone do not separate them


def test_one_slow_start_in_a_good_session_is_caught(slow):
    """Alone among good runs it is caught twice over; the session's lag check sees it first (49 ms at 27)."""
    one = slow[0]["one"]
    assert (one.drop("B-claspknife").status == "scored").all(), one.status.tolist()
    assert one.loc["B-claspknife"].status.startswith("release flagged: lag ") and "from the session median" in one.loc["B-claspknife"].status
    assert not one.loc["B-claspknife"].delivered_band_ok


def test_score_exits_with_status_1_on_a_tracking_flag_and_takes_another_limit(slow, capsys):
    _reps, sess, enc_all = slow
    old = SS.SESS
    try:
        SS.SESS = sess
        with pytest.raises(SystemExit) as ex:
            SS.main(["score", "--name", "SLOW", "--encoder-dir", enc_all])
        out = capsys.readouterr().out
        rep = SS.score("SLOW", enc_all, tracking_limit_ms=80.0)
    finally:
        SS.SESS = old
    assert ex.value.code == 1 and "TRACKING FLAGGED on 8 run(s)" in out
    assert (rep.status == "scored").all() and (rep.tracking_limit_ms == 80.0).all()      # the limit used is in the report


# ── the session's own plant as the yardstick (re-review 2026-11-12, sections 3d and 3e) ───────────────
# With a chirp run in the session, a run is held to what the plant identified from that chirp does with the
# run's own command (tracking) and with the setpoint its log recorded (release). Everything here is the
# simulated plant, Player and encoder; no rig log has been through it.
PLANTS = {
    "3 Hz, zeta 0.7": dict(wn_hz=3.0, zeta=0.7, delay_ms=0.0),
    "identified": dict(wn_hz=6.35, zeta=0.23, delay_ms=0.0),
    "8 Hz": dict(wn_hz=8.0, zeta=0.23, delay_ms=0.0),
    "30 % slower and 30 % less damped": dict(wn_hz=6.35 * 0.7, zeta=0.23 * 0.7, delay_ms=0.0),
    "identified + 40 ms": dict(wn_hz=6.35, zeta=0.23, delay_ms=40.0),
    "12 Hz, zeta 0.7": dict(wn_hz=12.0, zeta=0.7, delay_ms=0.0),
}
# the review's eight, plus the six nearest a limit on some plant: the three with the largest tracking error
# (B-clonus, S-lam3, R-reflex-sat), the fastest and the slowest command (C-f-high, C-f-low) and C-N-high
PLANT_SIDS = SLOW_SIDS + ["B-clonus", "S-lam3", "R-reflex-sat", "C-f-high", "C-f-low", "C-N-high"]
_fits = {}


def _logged(sp, seed, respond, fault=None, k_fault=None, noise=0.02, pad0=1500, pad1=1000, phase_ms=0.0):
    """One log, in memory, as dryrun.write_encoder_bin makes it: the Player's staircase (with idle time either
    side) -> respond() -> (a fault on the arm's motion) -> encoder noise. -> (logged setpoint, position)."""
    rng = np.random.default_rng(seed)
    held = DR.player_hold(sp, rng, phase_ms=phase_ms); spp = np.concatenate([np.zeros(pad0), held, np.full(pad1, held[-1])])
    y = respond(spp)
    if fault is not None: y = fault(y.copy(), pad0 + k_fault, float(np.sign(sp[k_fault + 300] - sp[k_fault])))
    return spp, y + rng.normal(0, noise, len(y))


def _rig(u, plant):
    """A linear rig, written out here so that the logs the real-file tests score do not come from the code
    under test: wn^2 / (s^2 + 2 zeta wn s + wn^2) held first-order between samples, then the delay."""
    from scipy.signal import cont2discrete, lfilter
    wn = 2 * np.pi * plant["wn_hz"]; u = np.asarray(u, float)
    bd, ad, _ = cont2discrete(([wn * wn], [1.0, 2 * plant["zeta"] * wn, wn * wn]), 1e-3, method="foh")
    y = lfilter(bd.ravel(), ad, u - u[0]) + u[0]; n = np.arange(len(u))
    return np.interp(n - plant.get("delay_ms", 0.0), n, y)


def _chirp_fit(plant=None, respond=None, seed=7):
    """The session's R000 in memory, found in its log and identified as session.score does it.
    -> (fit, why it is not trusted or "", misfit)."""
    t, sp, *_ = SS.chirp_command()
    spp, pos = _logged(sp, seed, respond or (lambda u: SS._model_response(u, plant)))
    k0 = ilc.locate(sp, spp, 1000.0); pos = pos[k0:k0 + len(sp)]
    fit = ilc.identify(sp, pos, 1000.0)
    return (fit,) + SS._chirp_trust(fit, sp, pos)


def _fit_of(name):
    if name not in _fits: _fits[name] = _chirp_fit(PLANTS[name])[0]
    return _fits[name]


def _judge(sid, fit, plant=None, respond=None, fault=None, seed=11, phase_ms=0.0):
    """One run through the encoder branch of session.score, in memory, judged against `fit`.
    -> dict(kind, time_err_ms, pred_ms, ratio, dlag_ms, why); kind is the release flag ("" = usable)."""
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", f"{sid}.csv"))
    k = int(np.argmax(ph == 2)); sw = ph == 2; t_rel = float(t[k])
    spp, pos = _logged(sp, seed, respond or (lambda u: SS._model_response(u, plant)), fault, k, phase_ms=phase_ms)
    k0 = ilc.locate(sp, spp, 1000.0); pos_all = pos[k0:]; t_all = t[0] + np.arange(len(pos_all)) / 1000.0
    rel = SS._delivered_release(t, sp, t_all, pos_all, t_rel)
    out = dict(kind=rel["kind"], time_err_ms=np.nan, pred_ms=np.nan, ratio=np.nan, dlag_ms=np.nan, why=None)
    if rel["kind"] not in ("", "lag"): return out
    rms, terr = SS._tracking(t, sp, sw, t_all, pos_all, rel["lag_s"])
    pred = SS._plant_prediction(t, sp, sw, t_rel, fit, t_all, spp[k0:])
    out.update(time_err_ms=terr, pred_ms=pred["time_err_ms"], ratio=terr / pred["time_err_ms"],
               dlag_ms=(rel["lag_s"] - pred["lag_s"]) * 1e3, why=SS._plant_verdict(terr, rms, rel["lag_s"], pred))
    return out


def _lurch(deg, over_ms=150, from_ms=30, decay_ms=200):
    """The re-reviewer's forward lurch: extra motion in the fall direction, a half-cosine rise of deg over
    over_ms starting from_ms after the commanded release, then decaying with time constant decay_ms."""
    def f(y, k, s):
        n = np.arange(len(y)) - (k + int(from_ms))
        b = np.where(n < 0, 0.0, np.where(n < over_ms, 0.5 * (1 - np.cos(np.pi * np.clip(n, 0, over_ms) / over_ms)), 1.0))
        return y + s * deg * b * np.where(n > over_ms, np.exp(-(n - over_ms) / decay_ms), 1.0)
    return f


def _bump(amp, width_ms, at_ms=0):
    """The first review's forward lurch: a sin-squared pulse in the fall direction, no tail."""
    def f(y, k, s):
        n = np.arange(len(y)) - (k + int(at_ms)); m = (n >= 0) & (n < width_ms)
        y[m] += s * amp * np.sin(np.pi * n[m] / width_ms) ** 2; return y
    return f


def _stiction(ms, catch_ms=200):
    """The re-reviewer's stiction: the arm's motion from the commanded release on is ms late, the delay
    closing linearly over catch_ms once it has moved."""
    def f(y, k, s):
        n = np.arange(len(y) - k).astype(float); dly = ms * np.clip(1 - (n - ms) / catch_ms, 0, 1)
        y[k:] = np.interp(k + n - dly, np.arange(len(y)), y); return y
    return f


def test_the_model_is_the_dry_runs_plant():
    """The yardstick has to be the plant the dry run delivers through, or a clean run would not sit on its own
    prediction. _model_response is a difference equation (lsim takes seconds per record); same numbers."""
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", "A-P0.csv"))
    held = DR.player_hold(sp, np.random.default_rng(1))
    for plant in (DR.PLANT, dict(wn_hz=4.445, zeta=0.161, delay_ms=20.0)):
        assert np.max(np.abs(SS._model_response(held, plant) - DR.plant_response(held, 1000.0, plant))) < 1e-6
        assert np.max(np.abs(_rig(held, plant) - DR.plant_response(held, 1000.0, plant))) < 1e-6      # the tests' own rig
    # a fractional delay moves the response by that much: half a millisecond on a 100 deg/s ramp is 0.05 deg
    ramp = np.concatenate([np.zeros(500), 0.1 * np.arange(3000.0)])
    d = SS._model_response(ramp, dict(wn_hz=6.35, zeta=0.23, delay_ms=0.5)) - SS._model_response(ramp, dict(wn_hz=6.35, zeta=0.23, delay_ms=0.0))
    assert abs(d[2500] + 0.05) < 0.002, d[2500]


def test_the_plant_derived_limit_in_numbers():
    """The limits as literal numbers. A test that takes them from the module follows them wherever they go."""
    assert (SS.TRACKING_PRED_MARGIN, SS.TRACKING_PRED_FLOOR_MS, SS.RELEASE_PRED_TOL_S) == (1.15, 4.0, 0.004)
    assert SS._tracking_limit(14.0) == pytest.approx(16.1) and SS._tracking_limit(20.0) == pytest.approx(23.0)
    assert SS._tracking_limit(3.4) == 4.0 and SS._tracking_limit(0.1) == 4.0 and SS._tracking_limit(3.6) == pytest.approx(4.14)
    pred = dict(time_err_ms=14.0, lag_s=0.030)
    assert SS._plant_verdict(16.0, 1.5, 0.030, pred) == ""
    why = SS._plant_verdict(16.2, 1.5, 0.030, pred)
    assert why.startswith("tracking error 16.2 ms") and "1.16 times the 14.0 ms" in why and "limit 16.1 ms" in why, why
    assert SS._plant_verdict(14.0, 1.5, 0.0339, pred) == "" and SS._plant_verdict(14.0, 1.5, 0.0261, pred) == ""
    late, early = SS._plant_verdict(14.0, 1.5, 0.0341, pred), SS._plant_verdict(14.0, 1.5, 0.0259, pred)
    assert late.startswith("the release is +4.1 ms from where the plant") and "late: stiction or a slow start" in late, late
    assert early.startswith("the release is -4.1 ms from where the plant") and "early: a lurch" in early, early
    both = SS._plant_verdict(30.0, 3.0, 0.050, pred)
    assert "; and the release is +20.0 ms" in both and both.startswith("tracking error 30.0 ms"), both
    # the floor: a rig that tracks almost perfectly is predicted under 1 ms, and is not flagged at 3.9
    assert SS._plant_verdict(3.9, 0.3, 0.030, dict(time_err_ms=0.2, lag_s=0.030)) == ""
    assert "limit 4.0 ms" in SS._plant_verdict(4.1, 0.3, 0.030, dict(time_err_ms=0.2, lag_s=0.030))
    # no logged setpoint to predict the release from: the tracking half still judges
    assert SS._plant_verdict(14.0, 1.5, 0.090, dict(time_err_ms=14.0, lag_s=np.nan)) == ""


@pytest.mark.parametrize("name", list(PLANTS))
def test_a_good_run_is_not_flagged_against_its_own_plant(name):
    """14 scenarios x 2 seeds per plant, each plant identified from its own chirp. No flag on any of them, with
    room: measured tracking is 0.90 to 1.12 of predicted (limit 1.15) and the release within 1.2 ms of
    predicted (limit 4). Measured on these runs: ratio 0.92 to 1.11, release -0.7 to +0.8 ms."""
    fit = _fit_of(name); plant = PLANTS[name]
    assert abs(fit["wn_hz"] / plant["wn_hz"] - 1) < 0.04 and abs(fit["zeta"] - plant["zeta"]) < 0.03, fit
    res = [_judge(sid, fit, plant, seed=seed) for sid in PLANT_SIDS for seed in (11, 5)]
    ok = [r for r in res if r["kind"] in ("", "lag")]
    # on the 3 Hz plant the release itself is uncertain for some scenarios (A.5: "ambiguous"); those runs are
    # release flagged and never reach this check
    assert len(ok) >= (18 if name == "3 Hz, zeta 0.7" else 28), [r["kind"] for r in res]
    assert all(r["why"] == "" for r in ok), [r["why"] for r in ok if r["why"]]
    assert all(abs(r["dlag_ms"]) < 1.2 for r in ok), sorted(round(r["dlag_ms"], 2) for r in ok)
    if name == "12 Hz, zeta 0.7":
        # predicted 0.03 to 2.8 ms; what is measured is the Player's staircase and the encoder's noise, which
        # the model does not contain. Without the floor 26 of these 28 good runs would be flagged.
        assert all(r["time_err_ms"] < 3.5 for r in ok), sorted(round(r["time_err_ms"], 2) for r in ok)
        assert sum(r["ratio"] > 1.15 for r in ok) >= 15, sorted(round(r["ratio"], 2) for r in ok)
    else:
        assert all(0.90 < r["ratio"] < 1.12 for r in ok), sorted(round(r["ratio"], 3) for r in ok)
    if name == "30 % slower and 30 % less damped":
        # the plant on which the fixed 25 ms flagged 49 of 66 good runs: 19 of these 28 are over 25 ms
        assert sum(r["time_err_ms"] > 25.0 for r in ok) >= 10, sorted(round(r["time_err_ms"], 1) for r in ok)


def test_where_the_players_tick_falls_does_not_move_the_release_check():
    """The Player's 10 ms tick falls anywhere against the command, and ilc.locate then finds the command 3 to 8
    samples into the log depending on where. A release predicted from the COMMAND carries that offset (clean
    runs -4.5 to +4.8 ms from predicted over the library: no limit under 5 ms holds). Predicted from the setpoint
    the log recorded it does not: the same eight runs at ten tick phases stay within 1 ms, and unflagged."""
    fit = _fit_of("identified"); rng = np.random.default_rng(3)
    res = [_judge(sid, fit, PLANTS["identified"], seed=100 + i, phase_ms=float(ph))
           for sid in SLOW_SIDS for i, ph in enumerate(list(np.arange(0.0, 10.0, 2.0)) + list(rng.uniform(0, 10, 5)))]
    assert len(res) == 80 and all(r["kind"] in ("", "lag") for r in res)
    assert all(r["why"] == "" for r in res), [r["why"] for r in res if r["why"]]
    assert all(abs(r["dlag_ms"]) < 1.0 for r in res), sorted(round(r["dlag_ms"], 2) for r in res)
    assert all(0.93 < r["ratio"] < 1.08 for r in res), sorted(round(r["ratio"], 3) for r in res)
    # and the command-driven prediction on the same logs does move with the tick: more than 3 ms of spread
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", "A-P0.csv")); k = int(np.argmax(ph == 2)); d = []
    for phase in (0.0, 2.0, 4.0, 6.0, 8.0):
        spp, pos = _logged(sp, 7, lambda u: SS._model_response(u, DR.PLANT), phase_ms=phase); k0 = ilc.locate(sp, spp, 1000.0)
        rel = SS._delivered_release(t, sp, t[0] + np.arange(len(pos) - k0) / 1000.0, pos[k0:], float(t[k]))
        d.append((rel["lag_s"] - SS._delivered_release(t, sp, t, SS._model_response(sp, fit), float(t[k]))["lag_s"]) * 1e3)
    assert max(d) - min(d) > 3.0, d


# fault -> (constructor, what must appear in the reason on every run, the smallest |release - predicted| in ms)
FAULTS = {
    "sluggish start, 50 % over 0.3 s": (lambda: _slow_start(0.5, 300), "late: stiction or a slow start", 7.0),
    "slow start, 30 % over 0.6 s": (lambda: _slow_start(0.3, 600), "late: stiction or a slow start", 15.0),
    "forward lurch, 6 deg over 150 ms from +30 ms": (lambda: _lurch(6), "early: a lurch", 7.0),
    "forward lurch, sin-squared 6 deg, 150 ms wide, from +30 ms": (lambda: _bump(6, 150, 30), "early: a lurch", 15.0),
    "stiction 20 ms": (lambda: _stiction(20), "late: stiction or a slow start", 9.0),
    "stiction 60 ms": (lambda: _stiction(60), "late: stiction or a slow start", 30.0),
}


@pytest.mark.parametrize("name", ["identified", "8 Hz", "30 % slower and 30 % less damped", "identified + 40 ms", "12 Hz, zeta 0.7"])
@pytest.mark.parametrize("fault", list(FAULTS))
def test_a_fault_in_every_run_is_flagged_against_the_plant(fault, name):
    """The review's eight scenarios, every run with the fault, the chirp from the same (healthy) plant. Every
    run whose release is usable is flagged, and the release check names the direction. These are the faults
    comparing runs with each other cannot see; the first and third are the re-review's admitted limits (b)
    and (a), scored without a flag on 1505fb8."""
    make, says, far = FAULTS[fault]
    res = [_judge(sid, _fit_of(name), PLANTS[name], fault=make()) for sid in SLOW_SIDS]
    ok = [r for r in res if r["kind"] in ("", "lag")]
    assert len(ok) >= 6, [r["kind"] for r in res]                 # the rest are release flagged, which is also a flag
    assert all(r["why"] and says in r["why"] for r in ok), [(round(r["dlag_ms"], 1), r["why"][:60]) for r in ok]
    assert all(abs(r["dlag_ms"]) > far for r in ok), sorted(round(r["dlag_ms"], 1) for r in ok)


def test_what_the_fixed_limit_and_the_tracking_ratio_alone_would_have_missed():
    """Why there are two checks. On the identified plant the 50 % sluggish start is under the old fixed 25 ms
    on all eight runs (19.4 to 22.6 ms) and 1.39 to 1.60 times its prediction: the ratio sees it. The forward
    lurch does not make tracking worse at all on half the runs (0.89 to 2.35 of predicted): only the release
    against the plant's sees it."""
    fit = _fit_of("identified")
    slug = [_judge(sid, fit, PLANTS["identified"], fault=_slow_start(0.5, 300)) for sid in SLOW_SIDS]
    assert all(r["time_err_ms"] < 25.0 for r in slug) and all(r["ratio"] > 1.3 for r in slug), [(round(r["time_err_ms"], 1), round(r["ratio"], 2)) for r in slug]
    assert all("tracking error" in r["why"] and "; and the release is +" in r["why"] for r in slug)
    lurch = [_judge(sid, fit, PLANTS["identified"], fault=_lurch(6)) for sid in SLOW_SIDS]
    assert sum(r["ratio"] <= 1.15 for r in lurch) >= 3 and sum(r["time_err_ms"] < 25.0 for r in lurch) >= 6, [(round(r["time_err_ms"], 1), round(r["ratio"], 2)) for r in lurch]
    assert all(r["why"] for r in lurch) and all(r["dlag_ms"] < -7.0 for r in lurch)


@pytest.mark.parametrize("fault, make, lo, hi", [
    ("start at 90 % over 0.3 s", lambda: _slow_start(0.9, 300), 0.5, 3.0),
    ("forward lurch of 1 deg", lambda: _lurch(1), -3.5, -0.5),
    ("stiction of 2 ms", lambda: _stiction(2), 0.5, 2.5),
])
def test_faults_below_the_limits_are_not_caught_and_that_is_stated(fault, make, lo, hi):
    """DOCUMENTED AS NOT CAUGHT. The checks have a resolution: these three move the release by less than 4 ms
    and tracking by less than 15 %, on all eight runs, and pass. The release offset is real and has the
    fault's sign; it is in report.csv (enc_lag_minus_pred_ms) for whoever looks."""
    res = [_judge(sid, _fit_of("identified"), PLANTS["identified"], fault=make()) for sid in SLOW_SIDS]
    assert all(r["why"] == "" for r in res), [(round(r["dlag_ms"], 1), round(r["ratio"], 2), r["why"]) for r in res]
    assert all(lo < r["dlag_ms"] < hi for r in res), sorted(round(r["dlag_ms"], 2) for r in res)


# A rig whose fault is a property of the RIG, so that it acts on the chirp as it acts on every run.
def _stick_slip(u, breakaway_deg, plant=None, sub=4):
    """The identified loop with stiction: the arm at rest does not move until the setpoint is breakaway_deg
    from it, and sticks again whenever its velocity crosses zero inside that band."""
    plant = plant or DR.PLANT; wn = 2 * np.pi * plant["wn_hz"]; z = plant["zeta"]; dt = 1e-3 / sub
    y = float(u[0]); v = 0.0; stuck = True; out = np.empty(len(u))
    for i, ui in enumerate(np.asarray(u, float).tolist()):
        for _ in range(sub):
            e = ui - y
            if stuck:
                if abs(e) <= breakaway_deg: continue
                stuck = False
            v_new = v + (wn * wn * e - 2 * z * wn * v) * dt
            if v != 0.0 and v_new * v < 0 and abs(e) < breakaway_deg:
                stuck = True; v = 0.0; continue
            v = v_new; y += v * dt
        out[i] = y
    return out


def _slew_limited(u, rate_dps, plant=None):
    """The identified loop behind a drive that cannot move the setpoint faster than rate_dps."""
    step = rate_dps / 1000.0; ue = np.empty(len(u)); x = float(u[0])
    for i, ui in enumerate(np.asarray(u, float).tolist()):
        x += min(max(ui - x, -step), step); ue[i] = x
    return _rig(ue, plant or DR.PLANT)


def test_the_chirp_still_identifies_the_plant_on_a_rig_with_stiction():
    """A.7's open question, in simulation. Stick-slip with a 1 deg breakaway is in the chirp as it is in every
    run: the arm sticks at each of the chirp's turning points. The identification does not absorb it (6.35 Hz,
    zeta 0.234, against a healthy 6.34, 0.234), the fit is still trusted (misfit 7 %, limit 10), and the
    rig's runs are flagged against it: tracking 1.35 to 1.47 times predicted, release 4.4 to 4.8 ms late."""
    rig = lambda u: _stick_slip(u, 1.0)                                                            # noqa: E731
    fit, why, misfit = _chirp_fit(respond=rig)
    assert abs(fit["wn_hz"] - 6.35) < 0.10 and abs(fit["zeta"] - 0.23) < 0.015 and abs(fit["delay_ms"]) < 2.0, fit
    assert why == "" and 0.05 < misfit < 0.10, (why, misfit)
    res = [_judge(sid, fit, respond=rig) for sid in ("A-P0", "B-claspknife", "M-tone_L2", "D-short")]
    assert all(r["why"] and "tracking error" in r["why"] for r in res), [r["why"] for r in res]
    assert all(r["ratio"] > 1.25 and r["dlag_ms"] > 3.5 for r in res), [(round(r["ratio"], 2), round(r["dlag_ms"], 1)) for r in res]


def test_a_chirp_the_plant_cannot_follow_is_not_trusted():
    """The other answer to the same question. A drive that saturates at 150 deg/s is slower than the chirp
    asks for above 3 Hz (8 deg at 10 Hz is 500 deg/s) and not slower than a fall's onset. The chirp then
    identifies a plant the rig is not (5.7 Hz, zeta 0.32, 9 ms of delay; R2 0.91), every release would be
    10 ms "early" against it, and a good arm would be flagged in every run. It is refused instead, and so is
    stick-slip coarse enough to stop the model reproducing the chirp."""
    fit, why, misfit = _chirp_fit(respond=lambda u: _slew_limited(u, 150.0))
    assert fit["wn_hz"] < 6.0 and fit["delay_ms"] > 5.0 and fit["r2"] < 0.95, fit
    assert why.startswith("the chirp fit is poor (R2 0.9"), why
    fit, why, misfit = _chirp_fit(respond=lambda u: _stick_slip(u, 2.0))
    assert fit["r2"] > 0.99 and 0.12 < misfit < 0.22 and "of the chirp's own motion unexplained (limit 10 %)" in why, (fit, why, misfit)
    # the gate in numbers: a healthy plant's chirp is at R2 0.995 or more and a misfit of 1 to 6 %
    assert (SS.CHIRP_MIN_R2, SS.CHIRP_MAX_MISFIT) == (0.95, 0.10)
    fit, why, misfit = _chirp_fit(PLANTS["identified"])
    assert why == "" and fit["r2"] > 0.995 and misfit < 0.03, (fit, why, misfit)


# The same through real files: plan --chirp, .bin logs, session.score.
CHIRP_SIDS = ["A-P0", "B-claspknife", "M-tone_L2", "S-lam3"]


def _write_rig_log(path, sp, phase, vel, seed, respond, fault=None, pre_s=1.5, post_s=1.0, fs=1000.0):
    """A LabVIEW log of one command on a rig given by respond() (+ a fault at the release), as _write_arm_log."""
    k = int(np.argmax(phase == 2)) if (phase == 2).any() else None
    spp, pos = _logged(sp, seed, respond, fault if k is not None else None, k, pad0=int(pre_s * fs), pad1=int(post_s * fs))
    pad0, pad1 = int(pre_s * fs), int(post_s * fs)
    ch = np.vstack([pos, np.gradient(pos, 1 / fs), spp, np.zeros_like(spp), np.concatenate([-np.ones(pad0), phase, -np.ones(pad1)]),
                    np.zeros_like(spp), np.concatenate([np.zeros(pad0), vel, np.zeros(pad1)])])
    ch.T.astype(">f8").tofile(path)


@pytest.fixture(scope="module")
def chirped(tmp_path_factory):
    """One planned session with a chirp run (R000 + four scenarios) and one folder of logs per rig:
      good       the identified plant
      sluggish   the same, every run starting at 50 % over 0.3 s (the chirp is healthy: the fault is at the release)
      lurch      the same, every run lurching forward 6 deg over 150 ms from +30 ms
      slower     a plant 30 % slower and 30 % less damped, nothing wrong with it
      saturated  the identified plant behind a drive limited to 150 deg/s (good runs, a chirp it cannot follow)
      nochirp    the good logs without R000.bin
    -> (sessions root, {rig: encoder folder})."""
    root = tmp_path_factory.mktemp("chirped"); sess = str(root / "sessions")
    old = SS.SESS
    try:
        SS.SESS = sess
        d = SS.plan("CH", sids=CHIRP_SIDS, seed=1, chirp=True)
    finally:
        SS.SESS = old
    key = [l.strip().split(",") for l in open(os.path.join(d, "key.csv"))][1:]
    lin = lambda plant: (lambda u: _rig(u, plant))                                                  # noqa: E731
    rigs = dict(good=(lin(DR.PLANT), None), sluggish=(lin(DR.PLANT), _slow_start(0.5, 300)), lurch=(lin(DR.PLANT), _lurch(6)),
                slower=(lin(PLANTS["30 % slower and 30 % less damped"]), None), saturated=(lambda u: _slew_limited(u, 150.0), None),
                nochirp=(lin(DR.PLANT), None))
    dirs = {}
    for which, (respond, fault) in rigs.items():
        enc = root / which; enc.mkdir(); dirs[which] = str(enc)
        for j, (rid, sid, _dur) in enumerate(key):
            if which == "nochirp" and sid == "R-chirp": continue
            a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
            _write_rig_log(str(enc / f"{rid}.bin"), a["setpoint_deg"], a["phase"].astype(int), a["vel_dps"], 11 + j, respond, fault)
    return sess, dirs


def _score_chirped(chirped, which, *args, **kw):
    sess, dirs = chirped
    old = SS.SESS
    try:
        SS.SESS = sess
        if args and args[0] == "cli":
            try:
                SS.main(["score", "--name", "CH", "--encoder-dir", dirs[which]] + list(args[1:]))
            except SystemExit as ex:
                return ex.code
            return 0
        rep = SS.score("CH", dirs[which], **kw)
    finally:
        SS.SESS = old
    return rep[rep.sid != "R-chirp"].set_index("sid")


def test_with_a_chirp_each_run_is_held_to_its_own_plants_prediction(chirped, capsys):
    capsys.readouterr(); good = _score_chirped(chirped, "good"); out = capsys.readouterr().out
    assert (good.status == "scored").all(), good.status.tolist()
    assert (good.tracking_limit_source == "plant").all()
    assert "tracking is judged against the plant identified from R000 (6.3" in out and "NOT PLANT-DERIVED" not in out, out
    assert "max(1.15 x predicted, 4 ms) or its release is more than 4 ms from predicted" in out
    # the limit is each run's own: 1.15 x what the identified plant predicts for that command, in the report
    assert np.allclose(good.tracking_limit_ms, 1.15 * good.tracking_pred_ms) and good.tracking_limit_ms.nunique() == 4
    assert ((good.tracking_limit_ms > 15.5) & (good.tracking_limit_ms < 22.5)).all(), good.tracking_limit_ms.tolist()    # was 25 for all
    q = good.tracking_time_err_ms / good.tracking_pred_ms
    assert ((q > 0.95) & (q < 1.06)).all(), q.tolist()
    assert (good.enc_lag_minus_pred_ms.abs() < 1.0).all(), good.enc_lag_minus_pred_ms.tolist()
    assert np.allclose(good.enc_lag_minus_pred_ms, good.enc_release_lag_ms - good.enc_release_lag_pred_ms)
    assert _score_chirped(chirped, "good", "cli") == 0


@pytest.mark.parametrize("which, says", [("sluggish", "late: stiction or a slow start"), ("lurch", "early: a lurch")])
def test_a_fault_in_every_run_of_a_chirped_session_is_flagged(chirped, which, says):
    """The re-review's admitted limits (b) and (a): on 1505fb8 the runs flagged here for tracking are "scored".
    The lurch on S-lam3 (a slow scenario) is caught by the release gate instead ("release uncertain"), as it
    was before; every other run is flagged against the plant, and no run of either session is left "scored"."""
    bad = _score_chirped(chirped, which)
    trk = bad[bad.status.str.startswith("tracking flagged: ")]
    assert len(trk) == (4 if which == "sluggish" else 3) and trk.status.str.contains(says, regex=False).all(), bad.status.tolist()
    assert bad.status.str.contains(" flagged: ", regex=False).all() and not (bad.status == "scored").any(), bad.status.tolist()
    assert not bad.delivered_band_ok.astype(bool).any() and np.isfinite(trk.enc_pt7).all()       # scored, kept, not deliveries
    assert (trk.enc_lag_minus_pred_ms.abs() > 4.0).all() and (trk.enc_lag_minus_pred_ms.abs() > 9.0).sum() >= 3, trk.enc_lag_minus_pred_ms.tolist()
    assert len(SS.tracking_flags(bad.reset_index())) == len(trk) and _score_chirped(chirped, which, "cli") == 1
    if which == "sluggish":                                    # under the old fixed limit, every one of them
        assert (bad.tracking_time_err_ms < 25.0).all(), bad.tracking_time_err_ms.tolist()


def test_a_slower_rig_is_judged_as_the_rig_it_is(chirped):
    """30 % slower and 30 % less damped. Under the fixed 25 ms three of these four good runs are flagged
    (25.2, 25.3 and 30.7 ms; 1505fb8). Against their own plant's prediction none is."""
    rep = _score_chirped(chirped, "slower")
    assert (rep.status == "scored").all(), rep.status.tolist()
    assert (rep.tracking_time_err_ms > 25.0).sum() == 3 and rep.tracking_time_err_ms.max() > 30.0, rep.tracking_time_err_ms.tolist()
    assert (rep.tracking_time_err_ms < rep.tracking_limit_ms).all() and rep.tracking_limit_ms.max() > 34.0, rep.tracking_limit_ms.tolist()


def test_without_a_chirp_the_limit_is_the_fixed_one_and_says_so(chirped, slow, capsys):
    """No R000 in the plan (the SLOW session), or R000 planned and its log missing: the fixed 25 ms, loudly."""
    good = slow[0]["good"]
    assert (good.tracking_limit_source == "fixed").all() and (good.tracking_limit_ms == 25.0).all()
    assert good.tracking_limit_note.iloc[0].startswith("TRACKING LIMIT IS NOT PLANT-DERIVED: there is no chirp run in this session"), good.tracking_limit_note.iloc[0]
    assert "tracking_pred_ms" not in good
    capsys.readouterr(); rep = _score_chirped(chirped, "nochirp"); out = capsys.readouterr().out
    assert (rep.status == "scored").all() and (rep.tracking_limit_source == "fixed").all() and (rep.tracking_limit_ms == 25.0).all()
    assert "TRACKING LIMIT IS NOT PLANT-DERIVED: the chirp run R000 has no encoder log, so every run is held to the fixed 25 ms" in out, out
    assert rep.tracking_limit_note.str.contains("cannot see a fault that is in every run").all()


def test_the_tracking_limit_override_still_works_with_a_chirp_and_is_recorded(chirped, capsys):
    """--tracking-limit-ms judges every run by that number alone; the plant's prediction stays in the report."""
    capsys.readouterr(); rep = _score_chirped(chirped, "sluggish", tracking_limit_ms=80.0); out = capsys.readouterr().out
    assert (rep.status == "scored").all(), rep.status.tolist()
    assert (rep.tracking_limit_ms == 80.0).all() and (rep.tracking_limit_source == "override").all()
    assert "TRACKING LIMIT OVERRIDE USED: every run is held to --tracking-limit-ms 80" in out
    assert np.isfinite(rep.tracking_pred_ms).all() and (rep.enc_lag_minus_pred_ms > 4.0).all()   # still reported
    tight = _score_chirped(chirped, "good", tracking_limit_ms=10.0)
    assert tight.status.str.contains("is over the limit of 10 ms", regex=False).all(), tight.status.tolist()
    assert _score_chirped(chirped, "sluggish", "cli", "--tracking-limit-ms", "80") == 0


def test_an_untrusted_chirp_falls_back_and_fails_the_exit_status(chirped, capsys):
    """A chirp the drive cannot follow identifies a plant the rig is not. It is not used: the runs are held to
    the fixed limit, the report says why, and `score` exits 1 because a chirp that does not fit is a finding."""
    capsys.readouterr(); rep = _score_chirped(chirped, "saturated"); out = capsys.readouterr().out
    assert (rep.tracking_limit_source == "fixed").all() and (rep.tracking_limit_ms == 25.0).all()
    assert "TRACKING LIMIT IS NOT PLANT-DERIVED: the plant identified from R000 is not trusted: the chirp fit is poor (R2 0.9" in out, out
    assert "CHIRP FINDING" in out
    assert _score_chirped(chirped, "saturated", "cli") == 1


# ── small fixes in the rewritten code (review 2026-11-11, change 6) ────────────────────────────────────
def test_the_phone_is_scored_over_the_commands_span_like_the_encoder(tmp_path):
    """D-short is a short record. The phone was scored to the end of its own recording, 0.9 s past the
    command's end, and read PT7 0.142 (band 1) where the encoder, scored over the command's span, read 0.446
    (band 2, the intended one). Same span, same answer."""
    old = SS.SESS
    try:
        SS.SESS = str(tmp_path)
        rep = DR.run(["D-short", "A-P0", "K-HM1"], seed=11, keep=False)[0].set_index("sid")
    finally:
        SS.SESS = old
    r = rep.loc["D-short"]
    assert r.status == "scored" and r.phone_status.startswith("ok"), (r.status, r.phone_status)
    assert r.enc_band == r.int_band == r.ph_band, (r.int_band, r.enc_band, r.ph_band)
    assert abs(r.ph_pt7 - r.enc_pt7) < 0.02 and r.ph_N == r.enc_N, (r.ph_pt7, r.enc_pt7, r.ph_N, r.enc_N)


def test_the_printed_percentage_counts_the_flagged_runs(delayed, capsys):
    """Three scored and one flagged printed "100% of 3 runs"; four flagged printed no percentage at all."""
    capsys.readouterr(); _score_delay(delayed, "mixed"); out = capsys.readouterr().out
    assert "delivered band == intended band: 3 of 4 runs (75%); scored 3, release flagged 1, tracking flagged 0" in out, out
    _score_delay(delayed, 150); out = capsys.readouterr().out
    assert "delivered band == intended band: 0 of 4 runs (0%); scored 0, release flagged 4, tracking flagged 0" in out, out


def _rescore(session, monkeypatch, **patched):
    rep0, d = session
    for k, v in patched.items(): monkeypatch.setattr(SS, k, v)
    return SS.score(os.path.basename(d), os.path.join(d, "synthetic_encoder"), os.path.join(d, "synthetic_phone", "dryrun-manifest.json"))


def test_a_phone_scoring_error_is_called_a_scoring_error(session, monkeypatch):
    """It was reported as "sync refused", which sends the reader to the sync pulses."""
    real = SS._score_trace
    def boom(t, *a, **k):
        if len(t) < 5000: raise ValueError("boom in scoring")           # the phone trace (~59 Hz); the encoder's is 1 kHz
        return real(t, *a, **k)
    rep = _rescore(session, monkeypatch, _score_trace=boom)
    sc = rep[rep.sid != "R-chirp"]
    assert (sc.status == "scored").all() and sc.phone_status.str.startswith("scoring error: boom in scoring").all(), sc.phone_status.tolist()
    assert np.isfinite(sc.ph_release_lag_ms).all()                       # the release it did measure is still reported


def test_a_phone_release_error_is_called_a_release_error(session, monkeypatch):
    real = SS._delivered_release
    def boom(*a, enc_lag_s=None, **k):
        if enc_lag_s is not None: raise RuntimeError("boom in release")
        return real(*a, **k)
    sc = _rescore(session, monkeypatch, _delivered_release=boom)
    sc = sc[sc.sid != "R-chirp"]
    assert (sc.status == "scored").all() and sc.phone_status.str.startswith("release error: boom in release").all(), sc.phone_status.tolist()


def test_a_real_sync_failure_is_still_a_sync_refusal(session, monkeypatch):
    def boom(*a, **k): raise SS.mb.SyncError("pulse offsets disagree by 0.300 s")
    sc = _rescore(session, monkeypatch, _align_phone=boom)
    sc = sc[sc.sid != "R-chirp"]
    assert (sc.status == "scored").all() and sc.phone_status.str.startswith("sync refused: pulse offsets disagree").all(), sc.phone_status.tolist()


def test_an_encoder_scoring_error_is_reported_and_does_not_stop_the_session(session, monkeypatch, capsys):
    real = SS._score_trace; seen = []
    def boom(t, *a, **k):
        if len(t) >= 5000 and not seen: seen.append(1); raise ValueError("the trace starts after the scored hold")
        return real(t, *a, **k)
    capsys.readouterr(); rep = _rescore(session, monkeypatch, _score_trace=boom); out = capsys.readouterr().out
    sc = rep[rep.sid != "R-chirp"]
    assert sc.status.tolist()[0].startswith("scoring error: the trace starts after") and (sc.status.iloc[1:] == "scored").all(), sc.status.tolist()
    assert "delivered band == intended band: 2 of 3 runs (67%); scored 2, release flagged 0, tracking flagged 0, scoring error 1" in out, out
    assert [(f[1], f[2][:14]) for f in SS.error_flags(rep)] == [("encoder", "scoring error:")] and "NOT SCORED BECAUSE OF AN ERROR on 1 trace" in out


def _score_cli_status(session, monkeypatch, **patched):
    """Exit status of `python -m neuromech.session score` on the dry-run session with something patched."""
    _rep0, d = session
    for k, v in patched.items(): monkeypatch.setattr(SS, k, v)
    monkeypatch.setattr(SS, "SESS", os.path.dirname(d))
    try:
        SS.main(["score", "--name", os.path.basename(d), "--encoder-dir", os.path.join(d, "synthetic_encoder"),
                 "--phone-manifest", os.path.join(d, "synthetic_phone", "dryrun-manifest.json")])
    except SystemExit as ex:
        return ex.code
    return 0


def test_score_exits_with_status_1_when_a_trace_ends_in_an_error(session, monkeypatch):
    """Re-review 2026-11-12, 4.1. A scoring error does not stop the session (change 6), and it was then in
    neither release_flags nor tracking_flags: `score` exited 0 with a run that had no score. An encoder
    scoring error, a phone scoring error and a phone release error each fail the exit status; a clean
    session still exits 0."""
    assert _score_cli_status(session, monkeypatch) == 0
    real_score, real_rel = SS._score_trace, SS._delivered_release
    seen = []
    def enc_boom(t, *a, **k):
        if len(t) >= 5000 and not seen: seen.append(1); raise ValueError("boom in the encoder's scoring")
        return real_score(t, *a, **k)
    def phone_boom(t, *a, **k):
        if len(t) < 5000: raise ValueError("boom in the phone's scoring")
        return real_score(t, *a, **k)
    def rel_boom(*a, enc_lag_s=None, **k):
        if enc_lag_s is not None: raise RuntimeError("boom in the phone's release")
        return real_rel(*a, **k)
    assert _score_cli_status(session, monkeypatch, _score_trace=enc_boom) == 1
    assert _score_cli_status(session, monkeypatch, _score_trace=phone_boom) == 1
    monkeypatch.setattr(SS, "_score_trace", real_score)
    assert _score_cli_status(session, monkeypatch, _delivered_release=rel_boom) == 1


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
