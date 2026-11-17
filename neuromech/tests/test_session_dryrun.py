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
    # The catch run is K-HM1: its H is fitted to the middle of band 1 (PT7 0.193 on the five-term scorer,
    # refit 2026-11-17, decision O5) and holds it through the plant with room to spare. The clasp-knife
    # (B-claspknife) was the edge case it replaced here, and was refit to the middle of band 1 the same day.
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

    Re-pinned 2026-11-16 for the five-term scorer (pt5trough-ref-2026-11-16, omega_max_n out of the
    canonical score).

    The edge moved. B-claspknife and M-catch_release_high (one scenario built twice) intended PT7 0.139,
    0.02 above the 0/1 edge, on the six-term scorer; on five terms (with #105's HEALTHY_REF) they intended
    0.078, band 0. Decision O5 (refit 2026-11-17) put the clasp-knife in the middle of band 1 (0.200),
    dropped the M copy, and put K-HM1 (refit to 0.193, band 1) in its slot. C-f-high is the edge run: it
    intends 0.2818, 0.0018 above the 1/1+ edge, and is allowlisted as near-edge on purpose
    (scenarios.NEAR_EDGE_ALLOWED). After the refit it was delivered at 0.2801 to 0.2818, band 1+, at seeds
    1, 3, 7, 11 and 42, but that is 0.0001 from the edge, so it is still pinned as an edge run rather than
    held to its band: it must intend within 0.005 above the edge and be delivered within 0.01 of it.

    The catch runs are pinned on their undershoot, which the drop of peak velocity made smaller: the
    plant's share came through peak velocity (its resonance raises it)."""
    old = SS.SESS
    try:
        SS.SESS = str(tmp_path)
        rep = DR.run(DR.DEFAULT_SIDS, seed=3, keep=False)[0]
    finally:
        SS.SESS = old
    sc = rep[rep.sid != "R-chirp"]
    assert (sc.status == "scored").all(), sc.status.tolist()
    on_edge = sc[sc.sid == "C-f-high"]
    rest = sc[sc.sid != "C-f-high"]
    assert rest.delivered_band_ok.astype(bool).all(), rest[["sid", "int_band", "enc_band"]].values.tolist()
    assert len(on_edge) == 1
    assert ((on_edge.int_pt7 > 0.28) & (on_edge.int_pt7 < 0.285)).all(), on_edge.int_pt7.tolist()
    assert ((on_edge.enc_pt7 - 0.28).abs() < 0.01).all(), on_edge.enc_pt7.tolist()
    catch = sc[sc.sid.isin(["B-claspknife", "K-HM1"])]
    assert len(catch) == 2 and (catch.enc_N == catch.int_N).all(), catch[["sid", "int_N", "enc_N"]].values.tolist()
    # REGRESSION PIN, not a principle: the catch runs undershoot their intended PT7 by the plant's own share,
    # measured after the O5 refit at 0.0025 to 0.0029 (B-claspknife, release 36 deg) and 0.0016 to 0.0018
    # (K-HM1) at seeds 1, 3, 7, 11 and 42 (the 25 deg clasp-knife gave 0.0045 to 0.0051; 0.010 to 0.012 on
    # six terms, where peak velocity was scored). 0.001 to 0.006 is that measurement with ~0.0006 below and
    # 0.003 above: below it something is flattering the delivery, above it the session is adding undershoot
    # of its own again. Re-baseline it if the scorer, the plant or these presets change.
    under = catch.int_pt7 - catch.enc_pt7
    assert ((under > 0.001) & (under < 0.006)).all(), catch[["sid", "int_pt7", "enc_pt7"]].values.tolist()
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


def test_the_commanded_release_misreads_a_late_delivery():
    """What the alignment is for, and the proof that the test above can fail: B-claspknife's own command,
    20 ms late, scored from the commanded release reads area_ratio 12% low; from its own release it does not.
    Until 2026-11-16 it also gained half a cycle (6.5 for 6.0). N is now counted from the first trough
    (plan 2026-11-16 section 3.2), which is exactly the fix for that, so N must NOT move any more."""
    import pandas as pd
    man = pd.read_csv(os.path.join(SS.OUT, "scenario_manifest.csv")).set_index("sid")
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", "B-claspknife.csv"))
    t_rel = float(t[np.argmax(ph == 2)]); lead = t_rel - float(t[np.argmax(ph == 1)]) + SS.SCORE_LEAD_S
    late = np.interp(t - 0.020, t, sp); rest = float(man.loc["B-claspknife", "rest_model_deg"])
    S = lambda rel: SS._summ(*SS._score_trace(t, late, rest, rel, lead))  # noqa: E731
    ontime, commanded = S(t_rel + 0.020), S(t_rel)
    assert ontime["N"] == man.loc["B-claspknife", "N"] and commanded["N"] == ontime["N"]
    assert abs(ontime["area_ratio"] - man.loc["B-claspknife", "area_ratio"]) < 1e-3
    assert commanded["area_ratio"] < 0.90 * ontime["area_ratio"], (commanded["area_ratio"], ontime["area_ratio"])


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
    # measured 2 of 66 on the five-term scorer with #105's HEALTHY_REF: C-f-high intends 0.2813, 0.0013 above
    # the 1/1+ edge, and the plant's undershoot crosses it (see the default-session test above); H-HM3 intends
    # 0.2748, 0.0052 below the same edge, and the plant's overshoot crosses it at this seed. Both are edge runs
    # the presets were not refit for (a follow-up on the new scorer). 0 of 66 on six terms.
    assert set(wrong_band) <= {"C-f-high", "H-HM3"}, wrong_band
    assert len(wrong_N) <= 10, wrong_N                                    # measured 9 of 66 on five terms, 10 on six (44 from the commanded release)


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
    for sid in ("A-P0", "D-push", "S-lam2", "C-f-high", "C-wmax-high", "B-claspknife"):
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


def _judge(sid, fit, plant=None, respond=None, fault=None, seed=11, phase_ms=0.0, margin=1.15, hold_gain=True, noise=0.02):
    """One run through the encoder branch of session.score, in memory, judged against `fit` on its own (no
    session rule). hold_gain=False leaves the prediction unscaled, as it was before the hold gain.
    -> dict(kind, time_err_ms, pred_ms, ratio, dlag_ms, gain, why); kind is the release flag ("" = usable)."""
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", f"{sid}.csv"))
    k = int(np.argmax(ph == 2)); sw = ph == 2; t_rel = float(t[k])
    spp, pos = _logged(sp, seed, respond or (lambda u: SS._model_response(u, plant)), fault, k, phase_ms=phase_ms, noise=noise)
    k0 = ilc.locate(sp, spp, 1000.0); pos_all = pos[k0:]; t_all = t[0] + np.arange(len(pos_all)) / 1000.0
    rel = SS._delivered_release(t, sp, t_all, pos_all, t_rel)
    out = dict(kind=rel["kind"], time_err_ms=np.nan, pred_ms=np.nan, ratio=np.nan, dlag_ms=np.nan, gain=np.nan, why=None)
    if rel["kind"] not in ("", "lag"): return out
    rms, terr = SS._tracking(t, sp, sw, t_all, pos_all, rel["lag_s"])
    off = SS._idle_offset(pos, spp, k0); pos0 = pos_all - (off if np.isfinite(off) else 0.0)
    g, g_used = SS._hold_gain(t, sp, t_all, pos0, t_rel)
    g_late = SS._late_gain(t, t_rel, t_all, pos0, spp[k0:], fit); g_app = SS._gain_to_apply(g_used, g_late)
    pred = SS._plant_prediction(t, sp, sw, t_rel, fit, t_all, spp[k0:], gain=g_app if hold_gain else 1.0)
    out.update(time_err_ms=terr, pred_ms=pred["time_err_ms"], ratio=terr / pred["time_err_ms"], gain=g, late=g_late, applied=g_app, offset=off,
               dlag_ms=(rel["lag_s"] - pred["lag_s"]) * 1e3, why=SS._plant_verdict(terr, rms, rel["lag_s"], pred, margin=margin))
    hv = SS._hold_verdict(g, g_late) if hold_gain else ""
    out["hold_why"] = hv
    if hv: out["why"] = hv + ("; and " + out["why"] if out["why"] else "")
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
    # the floor is added in quadrature: sqrt((1.15 x 14)^2 + 4^2) = 16.59, not max(16.1, 4) = 16.1
    assert SS._tracking_limit(14.0) == pytest.approx(16.589, abs=1e-3) and SS._tracking_limit(20.0) == pytest.approx(23.345, abs=1e-3)
    assert SS._tracking_limit(3.6) == pytest.approx(5.757, abs=1e-3) and SS._tracking_limit(0.1) == pytest.approx(4.0017, abs=1e-4)
    assert SS._tracking_limit(14.0, 1.35) == pytest.approx(19.319, abs=1e-3)
    pred = dict(time_err_ms=14.0, lag_s=0.030)
    assert SS._plant_verdict(16.5, 1.5, 0.030, pred) == ""
    why = SS._plant_verdict(16.7, 1.5, 0.030, pred)
    assert why.startswith("tracking error 16.7 ms") and "1.19 times the 14.0 ms" in why, why
    assert "(limit 16.6 ms = sqrt((1.15 x predicted)^2 + 4^2)): more error than the fitted model predicts for this command" in why, why
    assert "did not follow" not in why                       # the sentence claims what was measured, not a cause
    # the margin is the operator's to relax; the session's, when the session has one, and the sentence says which
    assert SS._plant_verdict(19.0, 1.5, 0.030, pred, margin=1.35) == "" and "limit 19.3 ms = sqrt((1.35 x" in SS._plant_verdict(19.5, 1.5, 0.030, pred, margin=1.35)
    stands_out = SS._plant_verdict(21.0, 1.5, 0.030, pred, margin=1.38, session=1.20)
    assert "the margin being the session's median ratio 1.20 x 1.15" in stands_out and stands_out.endswith("and more than the rest of this session shows"), stands_out
    # a fixed limit replaces the tracking half only: the release half is judged against the plant all the same
    assert SS._plant_verdict(24.0, 1.5, 0.030, pred, limit_ms=25.0) == ""
    assert "is over the limit of 25 ms" in SS._plant_verdict(26.0, 1.5, 0.030, pred, limit_ms=25.0)
    assert SS._plant_verdict(24.0, 1.5, 0.050, pred, limit_ms=80.0).startswith("the release is +20.0 ms from where the plant")
    assert SS._plant_verdict(14.0, 1.5, 0.0339, pred) == "" and SS._plant_verdict(14.0, 1.5, 0.0261, pred) == ""
    late, early = SS._plant_verdict(14.0, 1.5, 0.0341, pred), SS._plant_verdict(14.0, 1.5, 0.0259, pred)
    assert late.startswith("the release is +4.1 ms from where the plant") and "late: stiction or a slow start" in late, late
    assert early.startswith("the release is -4.1 ms from where the plant") and "early: a lurch" in early, early
    both = SS._plant_verdict(30.0, 3.0, 0.050, pred)
    assert "; and the release is +20.0 ms" in both and both.startswith("tracking error 30.0 ms"), both
    # the floor: a rig that tracks almost perfectly is predicted under 1 ms, and is not flagged at 3.9
    assert SS._plant_verdict(3.9, 0.3, 0.030, dict(time_err_ms=0.2, lag_s=0.030)) == ""
    assert "limit 4.0 ms" in SS._plant_verdict(4.1, 0.3, 0.030, dict(time_err_ms=0.2, lag_s=0.030))
    # ... and between the two regimes, where max(1.15 x predicted, 4) flagged good runs: predicted 3.8, measured 4.5
    assert SS._plant_verdict(4.5, 0.3, 0.030, dict(time_err_ms=3.8, lag_s=0.030)) == "" and 4.5 > max(1.15 * 3.8, 4.0)
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


def _write_rig_log(path, sp, phase, vel, seed, respond, fault=None, pre_s=1.5, post_s=1.0, fs=1000.0, phase_ms=0.0):
    """A LabVIEW log of one command on a rig given by respond() (+ a fault at the release), as _write_arm_log."""
    k = int(np.argmax(phase == 2)) if (phase == 2).any() else None
    spp, pos = _logged(sp, seed, respond, fault if k is not None else None, k, pad0=int(pre_s * fs), pad1=int(post_s * fs), phase_ms=phase_ms)
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
    and, for the review of 2026-11-14 (the runs are the identified plant's unless said):
      gain098    the arm settles at 0.98 of the setpoint, chirp and runs alike
      chirp110   R000 recorded with 10 % more bandwidth than the runs have
      chirp090   ... with 10 % less
      knocked    chirp110, and B-claspknife is knocked 8 deg, 0.4 s into its swing
      stiction5  every run leaves the hold 5 ms late (the chirp is healthy)
      badchirp   R000 from a drive limited to 150 deg/s; the runs are good
      sat250     a drive limited to 250 deg/s, chirp and runs alike
    and, for the re-review of 2026-11-16:
      relaxed    good, and B-claspknife holds 1.04 of the setpoint, relaxes to 1 over 0.6 s after the release,
                 and leaves the hold 5 ms late
      offset05   good, read by an encoder whose zero is 0.5 deg off the setpoint's, chirp and runs alike
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
    good = lin(DR.PLANT); wider = lin(dict(DR.PLANT, wn_hz=DR.PLANT["wn_hz"] * 1.10))
    # rig -> (the runs' response, a fault at the release or None, R000's response if not the runs', Player tick phase,
    #         the one scenario that gets the fault or None for all)
    rigs = dict(good=(good, None), sluggish=(good, _slow_start(0.5, 300)), lurch=(good, _lurch(6)),
                slower=(lin(PLANTS["30 % slower and 30 % less damped"]), None), saturated=(lambda u: _slew_limited(u, 150.0), None),
                nochirp=(good, None),
                gain098=(lambda u: 0.98 * _rig(u, DR.PLANT), None),
                chirp110=(good, None, wider), chirp090=(good, None, lin(dict(DR.PLANT, wn_hz=DR.PLANT["wn_hz"] * 0.90))),
                knocked=(good, _bump(8, 200, 400), wider, 0.0, "B-claspknife"),
                stiction5=(good, _stiction(5)),
                badchirp=(good, None, lambda u: _slew_limited(u, 150.0)),
                sat250=(lambda u: _slew_limited(u, 250.0), None),
                relaxed=(good, lambda y, k, s: _stiction(5)(_relaxing_hold(1.04, 600)(y, k, s), k, s), None, 0.0, "B-claspknife"),
                offset05=(lambda u: _rig(u, DR.PLANT) + 0.5, None))
    dirs = {}
    for which, spec in rigs.items():
        respond, fault, chirp_respond, phase_ms, only = tuple(spec) + (None, None, None, 0.0, None)[len(spec):]
        enc = root / which; enc.mkdir(); dirs[which] = str(enc)
        for j, (rid, sid, _dur) in enumerate(key):
            if which == "nochirp" and sid == "R-chirp": continue
            a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
            _write_rig_log(str(enc / f"{rid}.bin"), a["setpoint_deg"], a["phase"].astype(int), a["vel_dps"], 11 + j,
                           chirp_respond if (sid == "R-chirp" and chirp_respond is not None) else respond,
                           fault if only in (None, sid) else None, phase_ms=phase_ms or 0.0)
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
    assert "over sqrt((1.15 x predicted)^2 + 4^2) ms or its release is more than 4 ms from predicted" in out
    # the limit is each run's own: what the identified plant predicts for that command, x 1.15, with the 4 ms floor
    # in quadrature; in the report
    assert np.allclose(good.tracking_limit_ms, np.sqrt((1.15 * good.tracking_pred_ms) ** 2 + 4.0 ** 2)) and good.tracking_limit_ms.nunique() == 4
    assert ((good.tracking_limit_ms > 16.0) & (good.tracking_limit_ms < 22.5)).all(), good.tracking_limit_ms.tolist()    # was 25 for all
    # what the session was judged by is on every row, and nothing was found about the session
    assert (good.tracking_margin == 1.15).all() and (good.tracking_margin_source == "default").all() and (good.release_check == "plant").all()
    assert (good.model_mismatch_finding.fillna("") == "").all() and (good.chirp_suspect.fillna("") == "").all()
    assert "MODEL MISMATCH" not in out and "SUSPECT THE CHIRP" not in out
    assert (good.tracking_session_ratio_runs == 4).all() and ((good.tracking_session_ratio > 0.97) & (good.tracking_session_ratio < 1.04)).all()
    assert ((good.enc_hold_gain - 1.0).abs() < 0.002).all(), good.enc_hold_gain.tolist()
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


def test_the_tracking_limit_override_replaces_the_tracking_limit_and_not_the_release_check(chirped, capsys):
    """Review 2026-11-14, item 2. On b0d18c9 --tracking-limit-ms switched BOTH checks off: the half-speed start
    scored 4 of 4 and exited 0 with --tracking-limit-ms 80, and a 6 deg lurch in every run was left scored with 25.
    The override is a number for the tracking error. The release is still held to the plant's."""
    capsys.readouterr(); rep = _score_chirped(chirped, "sluggish", tracking_limit_ms=80.0); out = capsys.readouterr().out
    assert rep.status.str.startswith("tracking flagged: the release is +").all(), rep.status.tolist()
    assert rep.status.str.contains("late: stiction or a slow start", regex=False).all() and not rep.status.str.contains("tracking error").any()
    assert (rep.tracking_limit_ms == 80.0).all() and (rep.tracking_limit_source == "override").all() and (rep.release_check == "plant").all()
    assert "TRACKING LIMIT OVERRIDE USED: every run's tracking error is held to --tracking-limit-ms 80 in place of the plant-derived limit" in out
    assert "The release is still checked against the plant identified from R000 (within 4 ms of predicted)" in out
    assert np.isfinite(rep.tracking_pred_ms).all() and (rep.enc_lag_minus_pred_ms > 4.0).all()
    assert _score_chirped(chirped, "sluggish", "cli", "--tracking-limit-ms", "80") == 1
    # the lurch, under the old fixed number: three runs flagged by the release check, the fourth by the release gate
    lurch = _score_chirped(chirped, "lurch", tracking_limit_ms=25.0)
    assert not (lurch.status == "scored").any() and lurch.status.str.contains("early: a lurch", regex=False).sum() == 3, lurch.status.tolist()
    assert _score_chirped(chirped, "lurch", "cli", "--tracking-limit-ms", "25") == 1
    # a good session is not touched by it, and a tight number still flags for tracking, with its own sentence
    assert (_score_chirped(chirped, "good", tracking_limit_ms=80.0).status == "scored").all()
    assert _score_chirped(chirped, "good", "cli", "--tracking-limit-ms", "80") == 0
    tight = _score_chirped(chirped, "good", tracking_limit_ms=10.0)
    assert tight.status.str.contains("is over the limit of 10 ms: the arm did not follow this command", regex=False).all(), tight.status.tolist()
    # without a trusted plant there is no release check to keep, and the report says so
    capsys.readouterr(); none = _score_chirped(chirped, "nochirp", tracking_limit_ms=80.0); out = capsys.readouterr().out
    assert (none.release_check == "none").all() and (none.tracking_limit_source == "override").all() and (none.status == "scored").all()
    assert "There is no release check against a plant: the chirp run R000 has no encoder log." in out, out


def test_an_untrusted_chirp_falls_back_and_fails_the_exit_status(chirped, capsys):
    """A chirp the drive cannot follow identifies a plant the rig is not. It is not used: the runs are held to
    the fixed limit, the report says why, and `score` exits 1 because a chirp that does not fit is a finding."""
    capsys.readouterr(); rep = _score_chirped(chirped, "saturated"); out = capsys.readouterr().out
    assert (rep.tracking_limit_source == "fixed").all() and (rep.tracking_limit_ms == 25.0).all()
    assert "TRACKING LIMIT IS NOT PLANT-DERIVED: the plant identified from R000 is not trusted: the chirp fit is poor (R2 0.9" in out, out
    assert "CHIRP FINDING" in out
    assert _score_chirped(chirped, "saturated", "cli") == 1


# ── after the review of the chirp-derived limit (2026-11-14) ───────────────────────────────────────────
# docs/reports/2026-11-14-neuromech-chirp-tracking-limit-review.md (branch docs/chirp-tracking-limit-review).
# Simulated, like everything above. The mismatch magnitudes are the reviewer's guesses about a rig nobody has measured.
FAST = dict(wn_hz=11.0, zeta=1.0, delay_ms=0.0)
GAP_SIDS = ["C-N-zero", "R-reflex-force", "R-reflex-sat", "R-reflex-vel", "S-lam1"]


def test_the_floor_is_added_in_quadrature_between_the_plants_that_were_sampled():
    """Item 1. b0d18c9 sampled plants whose predictions were 14 ms and up, or 3 ms and down. Between them, at
    11 Hz and zeta 1.0, these five scenarios are predicted 3.8 to 4.3 ms and measure 4.5 to 4.9 with the Player's
    tick 2.5 or 7.5 ms into the command: over max(1.15 x predicted, 4) on all ten, and nothing is wrong with
    them. The floor is error the prediction does not contain; it does not stop existing at 3.5 ms."""
    fit = _chirp_fit(FAST)[0]
    res = [_judge(sid, fit, FAST, phase_ms=ph) for sid in GAP_SIDS for ph in (2.5, 7.5)]
    assert all(r["kind"] in ("", "lag") for r in res), [r["kind"] for r in res]
    assert all(3.7 < r["pred_ms"] < 4.4 and 4.4 < r["time_err_ms"] < 5.1 for r in res), [(round(r["pred_ms"], 2), round(r["time_err_ms"], 2)) for r in res]
    assert all(r["time_err_ms"] > max(1.15 * r["pred_ms"], 4.0) for r in res)            # every one flagged by b0d18c9
    assert all(r["why"] == "" for r in res), [r["why"] for r in res if r["why"]]
    assert all(abs(r["dlag_ms"]) < 1.0 for r in res), sorted(round(r["dlag_ms"], 2) for r in res)


def _chirped_session(root, name, sids, respond, phase_ms=0.0):
    """plan --chirp and one folder of logs from respond(). -> (sessions root, encoder folder)."""
    sess = str(root / "sessions"); enc = root / "enc"; enc.mkdir()
    old = SS.SESS
    try:
        SS.SESS = sess
        d = SS.plan(name, sids=sids, seed=1, chirp=True)
    finally:
        SS.SESS = old
    for j, (rid, _sid, _dur) in enumerate([l.strip().split(",") for l in open(os.path.join(d, "key.csv"))][1:]):
        a = np.genfromtxt(os.path.join(d, "commands", rid + ".csv"), delimiter=",", names=True)
        _write_rig_log(str(enc / f"{rid}.bin"), a["setpoint_deg"], a["phase"].astype(int), a["vel_dps"], 11 + j, respond, phase_ms=phase_ms)
    return sess, str(enc)


def test_a_real_session_on_a_fast_well_damped_rig_exits_0(tmp_path):
    """The same through plan, .bin files and the CLI, at 12 Hz and zeta 1.0. S-lam1 measures 4.2 ms against a
    prediction of 3.5: over max(1.15 x 3.5, 4) = 4.03, so on b0d18c9 this good session exits 1 (the review's did)."""
    sess, enc = _chirped_session(tmp_path, "FAST", GAP_SIDS, lambda u: _rig(u, dict(FAST, wn_hz=12.0)), phase_ms=2.5)
    old = SS.SESS
    try:
        SS.SESS = sess
        rep = SS.score("FAST", enc)
        with pytest.raises(SystemExit) as ex:
            SS.main(["score", "--name", "FAST", "--encoder-dir", enc, "--tracking-margin", "0"])
        SS.main(["score", "--name", "FAST", "--encoder-dir", enc])                    # returns: exit status 0
    finally:
        SS.SESS = old
    runs = rep[rep.sid != "R-chirp"]
    assert "--tracking-margin must be a positive number" in str(ex.value)
    assert (runs.status == "scored").all() and (runs.tracking_limit_source == "plant").all(), runs.status.tolist()
    lam = runs.set_index("sid").loc["S-lam1"]
    assert 4.1 < lam.tracking_time_err_ms < 4.3 and 3.4 < lam.tracking_pred_ms < 3.6, (lam.tracking_time_err_ms, lam.tracking_pred_ms)
    assert lam.tracking_time_err_ms > max(1.15 * lam.tracking_pred_ms, 4.0) and lam.tracking_limit_ms == pytest.approx(5.67, abs=0.1)
    assert ((runs.tracking_limit_ms > 5.4) & (runs.tracking_limit_ms < 6.2)).all(), runs.tracking_limit_ms.tolist()
    # predictions under 7 ms say nothing about the model: no session ratio is taken from them
    assert (runs.tracking_session_ratio_runs == 0).all() and runs.tracking_session_ratio.isna().all()


def test_the_margin_and_the_floor_are_where_a_small_fault_and_a_noisy_encoder_put_them():
    """Item 5: 1.15 and 4 ms, behaviourally. A release 5 ms late (inside the release check's 4 ms: it reads +3.1)
    costs 21 to 25 % of tracking error on these eight runs and is flagged for tracking alone; a margin of 1.20, or a
    floor of 6 ms, would pass all but one or two of them. And on the 12 Hz plant, where the prediction is 0.3 ms and
    everything measured is the Player and the encoder, a run with 0.05 deg of encoder noise (the dry run has 0.02)
    reads 3.4 ms: a floor of 3 ms would flag it."""
    fit = _fit_of("identified")
    res = [_judge(sid, fit, PLANTS["identified"], fault=_stiction(5)) for sid in SLOW_SIDS]
    assert all(r["why"].startswith("tracking error ") and "the release is" not in r["why"] for r in res), [r["why"][:50] for r in res]
    assert all(1.20 < r["ratio"] < 1.26 and 2.9 < r["dlag_ms"] < 3.7 for r in res), [(round(r["ratio"], 3), round(r["dlag_ms"], 2)) for r in res]
    assert sum(r["time_err_ms"] < np.hypot(1.20 * r["pred_ms"], 4.0) for r in res) >= 6
    assert sum(r["time_err_ms"] < np.hypot(1.15 * r["pred_ms"], 6.0) for r in res) >= 6
    quiet = [_judge("R-reflex-sat", _fit_of("12 Hz, zeta 0.7"), PLANTS["12 Hz, zeta 0.7"], seed=s, noise=0.05) for s in (11, 5)]
    assert all(r["why"] == "" and r["pred_ms"] < 0.5 and 3.2 < r["time_err_ms"] < 3.6 for r in quiet), [(r["pred_ms"], r["time_err_ms"]) for r in quiet]
    assert all(r["time_err_ms"] > np.hypot(1.15 * r["pred_ms"], 3.0) for r in quiet)


def test_the_hold_gain_in_numbers():
    """The model has no gain term. What the arm held, over what it was told to hold, in the 0.3 s before the commanded
    release: applied to the prediction within 5 % of 1, reported and NOT applied beyond that."""
    assert (SS.HOLD_GAIN_MAX_DEV, SS.HOLD_GAIN_MIN_DEG) == (0.05, 5.0)
    t = np.arange(3000) / 1000.0
    g = lambda level, hold=40.0: SS._hold_gain(t, np.full(3000, hold), t, np.full(3000, level), 2.0)      # noqa: E731
    assert g(39.2) == pytest.approx((0.98, 0.98)) and g(38.2) == pytest.approx((0.955, 0.955)) and g(41.8) == pytest.approx((1.045, 1.045))
    assert g(37.8) == pytest.approx((0.945, 1.0)) and g(42.2) == pytest.approx((1.055, 1.0))              # not a scale: left to be seen
    assert g(-39.2, -40.0) == pytest.approx((0.98, 0.98)) and g(4.9, 5.0) == pytest.approx((0.98, 0.98))
    far = g(3.9, 4.0); assert np.isnan(far[0]) and far[1] == 1.0                                          # 4 deg does not measure a gain
    pos = np.full(3000, 39.2); pos[:1700] = 0.0; pos[2000:] = 0.0                                         # only [1.7, 2.0) s counts
    assert SS._hold_gain(t, np.full(3000, 40.0), t, pos, 2.0) == pytest.approx((0.98, 0.98))
    pos[1700:1850] = 0.0
    assert SS._hold_gain(t, np.full(3000, 40.0), t, pos, 2.0)[0] == pytest.approx(0.49)
    # re-review 2026-11-16: applied only when the run shows the same gain after the swing (within 1 %) ...
    assert (SS.HOLD_GAIN_LATE_S, SS.HOLD_GAIN_LATE_TOL, SS.HOLD_GAIN_LATE_MIN_DEG, SS.HOLD_LATE_MAX_DIFF) == (2.5, 0.01, 1.0, 0.025)
    A = SS._gain_to_apply
    assert A(0.98, 0.985) == 0.98 and A(0.98, 0.991) == 1.0 and A(0.98, np.nan) == 1.0 and A(1.0, 1.0) == 1.0
    # ... and a hold more than 2.5 % from where the run settles is flagged; unmeasured is not
    V = SS._hold_verdict
    assert V(1.0, 1.024) == "" and V(1.024, 1.0) == "" and V(np.nan, 1.1) == "" and V(1.1, np.nan) == ""
    assert V(1.04, 1.0).startswith("the arm held 1.040 of the setpoint before the release and settled at 1.000") and V(1.0, 0.974) != ""
    # the encoder's zero, from the idle log before the command: the median of position - setpoint over its last 0.5 s
    assert SS.IDLE_OFFSET_S == 0.5
    idle = np.r_[np.full(1000, 3.0), np.full(500, 0.3)]; sp0 = np.zeros(1500)
    assert SS._idle_offset(idle, sp0, 1500) == pytest.approx(0.3) and SS._idle_offset(idle, sp0, 1000) == pytest.approx(3.0)
    assert np.isnan(SS._idle_offset(idle, sp0, 200)) and np.isnan(SS._idle_offset(idle, np.r_[np.zeros(1400), np.ones(100)], 1500))
    assert (SS.IDLE_MAX_DRIFT_DEG, SS.IDLE_MAX_RMS_DEG) == (0.1, 0.2)

def test_the_encoder_zero_is_taken_only_from_a_still_idle():
    """Re-review of 1ef9198: an idle that drifts, or one before the controller has the arm, is not a zero. There
    _idle_offset gives NaN and the gains are taken through zero, as they were before it. Noise is not motion: 0.1 deg
    rms (five times the dry run's) is still."""
    sp0 = np.zeros(1500)
    rng = np.random.default_rng(3); quiet = 0.3 + rng.normal(0, 0.1, 1500)                   # 0.1 deg rms noise is still
    assert SS._idle_offset(quiet, sp0, 1500) == pytest.approx(0.3, abs=0.02)
    drift = np.r_[np.full(1000, 0.3), 0.3 + np.linspace(0, 0.25, 500)]                         # creeping 0.25 deg in 0.5 s
    assert np.isnan(SS._idle_offset(drift, sp0, 1500)) and not np.isnan(SS._idle_offset(drift - np.r_[np.zeros(1000), np.linspace(0, 0.15, 500)], sp0, 1500))
    ring = np.r_[np.zeros(1000), 0.4 * np.sin(2 * np.pi * 8.0 * np.arange(500) / 1000.0)]      # still swinging (0.28 rms)
    assert np.isnan(SS._idle_offset(ring, sp0, 1500)) and not np.isnan(SS._idle_offset(ring / 2, sp0, 1500))
    late = np.r_[np.full(1250, 5.0), np.full(250, 0.3)]                                        # the controller takes the arm 0.25 s before the command
    assert np.isnan(SS._idle_offset(late, sp0, 1500))


# `alone` (flagged without the hold gain) is a measurement on PLANT_SIDS: at 0.97 it was 12 until the O5 refit
# (2026-11-17) and is 16 since: the four new flags are B-claspknife and B-flexor at both seeds (tracking time error
# 17.5 and 16.8 ms). 0.98 and 1.02 did not move.
@pytest.mark.parametrize("gain, alone", [(0.98, 10), (1.02, 5), (0.97, 16)])
def test_an_arm_that_settles_a_few_percent_off_the_setpoint_is_not_a_tracking_fault(gain, alone):
    """The review's first mismatch: a static gain of 0.98 had 36 % of good runs flagged. 14 scenarios x 2 seeds on the
    identified plant times `gain`, against the plant as the chirp identifies it (the fit has no gain term, so the
    poles are the same with or without the gain): flagged without the hold gain, none with it.
    The gain is applied to the model's release too: without it the release reads 0.34 to 0.67 ms later (0.97, 0.98)
    or 0.41 ms earlier (1.02), on average, than the same runs on the identified plant; with it, within 0.09 ms."""
    rig = lambda u: gain * SS._model_response(u, DR.PLANT)                                                # noqa: E731
    fit = _fit_of("identified")
    with_g = [_judge(sid, fit, respond=rig, seed=s) for sid in PLANT_SIDS for s in (11, 5)]
    without = [_judge(sid, fit, respond=rig, seed=s, hold_gain=False) for sid in PLANT_SIDS for s in (11, 5)]
    assert all(r["kind"] in ("", "lag") for r in with_g) and all(abs(r["gain"] - gain) < 0.002 for r in with_g), [r["gain"] for r in with_g]
    assert sum(bool(r["why"]) for r in without) == alone, [round(r["ratio"], 2) for r in without]
    assert all(r["why"] == "" for r in with_g) and all(0.95 < r["ratio"] < 1.05 for r in with_g), [round(r["ratio"], 3) for r in with_g]
    unit = [_judge(sid, fit, PLANTS["identified"], seed=s) for sid in PLANT_SIDS for s in (11, 5)]
    shift = np.mean([a["dlag_ms"] - b["dlag_ms"] for a, b in zip(with_g, unit)])
    assert abs(shift) < 0.15, shift


def test_a_gain_further_off_than_5_percent_is_not_absorbed():
    """0.90 of the setpoint is not a scale to be divided out. The gain is reported, the prediction is left alone,
    and the runs are flagged."""
    rig = lambda u: 0.90 * SS._model_response(u, DR.PLANT)                                                # noqa: E731
    res = [_judge(sid, _fit_of("identified"), respond=rig) for sid in SLOW_SIDS]
    assert all(abs(r["gain"] - 0.90) < 0.002 for r in res) and sum(bool(r["why"]) for r in res) >= 7, [(r["gain"], r["why"][:30]) for r in res]


@pytest.mark.parametrize("gain", [0.93, 1.07])
def test_a_gain_7_percent_off_kept_through_the_swing_is_flagged_not_absorbed(gain):
    """Re-review item 3. The arm holds 7 % off the setpoint and keeps that gain through the swing (so the late gain
    agrees with the hold gain and the hold verdict has nothing to say): that is past HOLD_GAIN_MAX_DEV, the
    prediction is not scaled, and the runs are flagged for tracking. With the limit at 0.08 they would be absorbed."""
    rig = lambda u: gain * SS._model_response(u, DR.PLANT)                                                # noqa: E731
    res = [_judge(sid, _fit_of("identified"), respond=rig) for sid in SLOW_SIDS]
    assert all(abs(r["gain"] - gain) < 0.002 and abs(r["late"] - gain) < 0.002 and r["applied"] == 1.0 for r in res), [(r["gain"], r["late"]) for r in res]
    assert all(r["hold_why"] == "" for r in res)
    assert all(r["why"].startswith("tracking error ") for r in res), [(round(r["ratio"], 2), r["why"][:40]) for r in res]


def _dipole_rig(fp_hz, x, plant=DR.PLANT):
    """The reviewer's slow mode: the plant times (s/zr + 1) / (s/p + 1), p = 2 pi fp_hz, zr = p / (1 + x). Gain 1 at
    rest, 1 + x through the swing, settling back at fp_hz: a rig that holds where it settles but not on the way."""
    from scipy.signal import cont2discrete, lfilter
    wn = 2 * np.pi * plant["wn_hz"]; p = 2 * np.pi * fp_hz; zr = p / (1 + x)
    num = np.polymul([wn * wn], np.array([1.0, zr]) * p / zr); den = np.polymul([1.0, 2 * plant["zeta"] * wn, wn * wn], [1.0, p])
    bd, ad, _ = cont2discrete((num, den), 1e-3, method="foh")
    return lambda u: lfilter(bd.ravel(), ad, np.asarray(u, float) - u[0]) + u[0]


def test_a_slow_mode_inside_the_hold_tolerance_is_not_a_hold_flag():
    """Re-review item 4 (2026-11-16): why the hold/late tolerance is 2.5 % and not 1 %. A rig with a 0.3 Hz dipole
    worth +2 % (a slow mode the chirp cannot see; good runs) reads 0.014 to 0.020 between its hold gain and its
    late gain on 7 of these 14 runs. None is flagged for its hold; at 1 % those 7 would be. (Some are flagged for
    tracking: that rig is in the README's mismatch table.)"""
    rig = _dipole_rig(0.3, 0.02); fit = _chirp_fit(respond=rig)[0]
    res = [_judge(sid, fit, respond=rig) for sid in PLANT_SIDS]
    d = [abs(r["gain"] - r["late"]) for r in res if r["kind"] in ("", "lag") and np.isfinite(r["gain"]) and np.isfinite(r["late"])]
    assert all(r.get("hold_why", "") == "" for r in res) and max(d) < SS.HOLD_LATE_MAX_DIFF, d
    assert sum(x > 0.01 for x in d) >= 5, d


def _relaxing_hold(g, back_ms):
    """The re-reviewer's relaxing hold: the arm holds g of the setpoint and relaxes linearly to 1 over back_ms
    from the commanded release."""
    def f(y, k, s):
        n = np.arange(len(y)) - k
        return y * np.where(n < 0, g, g + (1 - g) * np.clip(n / back_ms, 0, 1))
    return f


@pytest.mark.parametrize("g, back_ms", [(1.04, 600), (1.03, 2000), (0.97, 600)])
def test_one_run_whose_hold_relaxes_after_the_release_is_flagged_with_stiction_on_it(g, back_ms):
    """Re-review item 1. One run in eight holds g of the setpoint and relaxes to 1 over back_ms after the release,
    and carries 5 ms of stiction; the other seven are good. On 1d0d812 its hold gain scaled the prediction and the
    stiction was flagged in 1 to 10 of 132 such runs; not applying the gain alone (the late-gain check) still left
    the relaxing hold moving the release and the tracking error, and 11 to 22 of 132 flagged. Now the run is flagged
    for its hold in every one of the eight positions, and no good run is."""
    fit = _fit_of("identified"); st = _stiction(5); rx = _relaxing_hold(g, back_ms)
    both = lambda y, k, s: st(rx(y, k, s), k, s)                                                          # noqa: E731
    good = [_judge(sid, fit, PLANTS["identified"]) for sid in SLOW_SIDS]
    assert all(r["why"] == "" and r["hold_why"] == "" for r in good), [r["why"][:40] for r in good]
    for bad in SLOW_SIDS:
        r = _judge(bad, fit, PLANTS["identified"], fault=both)
        assert r["kind"] in ("", "lag") and r["applied"] == 1.0 and r["hold_why"].startswith(f"the arm held {r['gain']:.3f} of the setpoint"), (bad, r["gain"], r.get("late"))
        assert abs(r["gain"] - g) < 0.003 and abs(r["late"] - 1.0) < 0.01, (bad, r["gain"], r["late"])
        assert r["why"].startswith(r["hold_why"]), r["why"]


def test_the_session_ratio_rule_in_numbers():
    """Item 3. Runs that agree with each other on a measured / predicted ratio that is not 1 are ONE finding about the
    model. The rule, as literal numbers: at least 3 runs predicted at 7 ms or more; "agree" is two thirds of them within
    15 % of their median; a finding when that median is outside 0.92 to 1.08; each run is then held to 1.15 x the
    median. Chosen on 66 rig-and-fault cases x 132 runs (report section C.4): fourteen clean plants give session medians
    of 0.96 to 1.06 in 42,000 drawn sessions of 8, 12 and 20 runs, none with a finding. With runs predicted at 6 to 7 ms
    let in, 0.92 to 1.07 and one finding on a good session (10 Hz, zeta 1.0, median 0.919)."""
    assert (SS.MODEL_RATIO_MIN_PRED_MS, SS.MODEL_RATIO_MIN_RUNS, SS.MODEL_RATIO_LOW, SS.MODEL_RATIO_HIGH) == (7.0, 3, 0.92, 1.08)
    assert (SS.MODEL_RATIO_AGREE, SS.MODEL_RATIO_AGREE_SHARE) == (0.15, 2.0 / 3.0)
    R = SS._session_ratio
    none = R([]); assert none["n"] == 0 and not none["mismatch"] and none["margin"] == 1.15 and np.isnan(none["median"])
    assert not R([1.3, 1.3])["mismatch"] and R([1.3, 1.3, 1.3])["mismatch"]                               # three runs make a session
    assert R([1.3, np.nan, 1.3, np.nan])["n"] == 2
    ok = R([1.0, 1.02, 0.98]); assert not ok["mismatch"] and ok["median"] == 1.0 and ok["share"] == 1.0 and ok["margin"] == 1.15
    assert (ok["low"], ok["high"]) == (0.92, 1.08)
    assert not R([1.079] * 3)["mismatch"] and not R([0.921] * 3)["mismatch"]
    hi, lo = R([1.081] * 3), R([0.919] * 3)
    assert hi["mismatch"] and hi["margin"] == pytest.approx(1.15 * 1.081) and lo["mismatch"] and lo["margin"] == pytest.approx(1.15 * 0.919)
    # agreement: four of six within 15 % of the median is two thirds, and is enough; four of seven is not
    assert R([1.2, 1.2, 1.2, 1.0, 1.5, 1.2])["mismatch"] and R([1.2, 1.2, 1.2, 1.0, 1.5, 1.2])["share"] == pytest.approx(4 / 6)
    assert not R([1.2, 1.2, 1.2, 1.0, 1.5, 1.5, 1.2])["mismatch"]
    assert R([1.3, 1.3, 1.3, 1.3 * 1.14, 1.3 * 0.86, 1.3 * 1.14])["share"] == 1.0
    assert R([1.3, 1.3, 1.3, 1.3 * 1.16, 1.3 * 0.84, 1.3 * 1.16])["share"] == 0.5 and not R([1.3, 1.3, 1.3, 1.3 * 1.16, 1.3 * 0.84, 1.3 * 1.16])["mismatch"]
    # an operator who scores with --tracking-margin 1.35 has accepted a session at 1.35 / 1.15 = 1.17 of predicted
    acc = R([1.2] * 3, 1.35)
    assert not acc["mismatch"] and acc["margin"] == 1.35 and acc["low"] == pytest.approx(1.080) and acc["high"] == pytest.approx(1.268, abs=1e-3)
    assert R([1.3] * 3, 1.35)["mismatch"] and R([1.3] * 3, 1.35)["margin"] == pytest.approx(1.495)
    assert R([1.0] * 3, 1.35)["mismatch"] and R([1.0] * 3, 1.35)["margin"] == pytest.approx(1.15)        # looser than the session needs


def test_the_model_mismatch_finding_says_what_was_found_and_what_to_do():
    hi = SS._model_finding(SS._session_ratio([1.2, 1.2, 1.2, 1.0, 1.5, 1.2]))
    assert hi.startswith("MODEL MISMATCH FINDING: measured / predicted tracking error has a session median of 1.20 over 6 run(s), "
                         "67 % of them within 15 % of it; expected 0.92 to 1.08. The runs carry 20 % more tracking error than the plant "
                         "identified from R000 predicts, and they agree on it"), hi
    assert "Each run has been held to the session's ratio (1.38 x predicted) and not to the model's" in hi
    assert "(1) play R000 again and re-score" in hi and "enc_hold_gain" in hi and "(3) do NOT make this session pass with its own ratio (it corresponds to --tracking-margin 1.38)" in hi
    assert "58 to 84 % of sessions" in hi and "a known-good reference session) and carried forward; it is not stored, so pass it with --tracking-margin on every later session" in hi
    assert "The release check against the plant is not affected by any of this. `score` exits with status 1." in hi
    lo = SS._model_finding(SS._session_ratio([0.88] * 4), "R007")
    assert "session median of 0.88 over 4 run(s), 100 % of them" in lo and "The runs carry 12 % less tracking error than the plant identified from R007 predicts" in lo
    assert "Nothing is flagged by that, and that is the problem" in lo and "a Player clock running slow" in lo and "--tracking-margin 1.01" in lo
    loose = SS._model_finding(SS._session_ratio([1.0] * 3, 1.6))
    assert "expected 1.28 to 1.50 (--tracking-margin 1.6 accepts 1.39)" in loose and "the margin is looser than this session needs" in loose, loose
    moved = SS._model_finding(SS._session_ratio([1.3] * 3, 1.2))
    assert "more tracking error than the margin in force accepts (1.30 of predicted against 1.04)" in moved, moved


def test_a_small_fault_in_every_run_is_one_finding_and_the_low_direction_restores_the_limit():
    """What the rule does with runs, in memory. A release 5 ms late in every run: each of the eight is over its own
    limit (b0d18c9: eight tracking flags). They agree (1.21 to 1.25), so it is one finding, and none stands out from
    the session. In the quiet direction (a chirp that makes the plant look 10 % slower than it is) every run reads
    0.88 of predicted and nothing was ever flagged: there the finding tightens each run's limit to the session's."""
    fit = _fit_of("identified")
    res = [_judge(sid, fit, PLANTS["identified"], fault=_stiction(5)) for sid in SLOW_SIDS]
    R = SS._session_ratio([r["ratio"] for r in res])
    assert all(r["why"] for r in res) and R["mismatch"] and R["n"] == 8 and 1.21 < R["median"] < 1.22 and R["share"] == 1.0, R
    assert all(r["time_err_ms"] <= SS._tracking_limit(r["pred_ms"], R["margin"]) for r in res)
    slow_fit = _chirp_fit(dict(DR.PLANT, wn_hz=DR.PLANT["wn_hz"] * 0.90))[0]
    res = [_judge(sid, slow_fit, PLANTS["identified"]) for sid in SLOW_SIDS]
    R = SS._session_ratio([r["ratio"] for r in res])
    assert all(r["why"] == "" for r in res) and R["mismatch"] and 0.87 < R["median"] < 0.89 and R["margin"] == pytest.approx(1.15 * R["median"]), R
    # a run at 1.05 of a 16 ms prediction passes the model's limit (18.8 ms) and not the session's (16.7)
    pred = dict(time_err_ms=16.0, lag_s=0.030)
    assert SS._plant_verdict(16.8, 1.5, 0.030, pred) == "" and SS._plant_verdict(16.8, 1.5, 0.030, pred, margin=R["margin"], session=R["median"]) != ""


def test_a_session_the_model_does_not_describe_is_one_finding_not_a_flag_per_run(chirped, capsys):
    """Through real files. R000 recorded with 10 % more bandwidth than the runs have (a chirp at other gains): every run
    reads 1.13 to 1.22 of predicted. On b0d18c9 S-lam3 is tracking flagged; with five such sessions in the review 42 %
    of good runs were. Now: one finding, four scored runs, exit status 1, and the margin that accepts it."""
    capsys.readouterr(); rep = _score_chirped(chirped, "chirp110"); out = capsys.readouterr().out
    assert (rep.status == "scored").all() and rep.delivered_band_ok.astype(bool).all(), rep.status.tolist()
    s = rep.loc["S-lam3"]; assert s.tracking_time_err_ms > np.hypot(1.15 * s.tracking_pred_ms, 4.0) and s.tracking_time_err_ms < s.tracking_limit_ms
    assert out.count("MODEL MISMATCH FINDING") == 1 and "TRACKING FLAGGED" not in out
    f = rep.model_mismatch_finding.iloc[0]
    assert (rep.model_mismatch_finding == f).all() and f in out and len(SS.model_flags(rep.reset_index())) == 1
    assert "session median of 1.15 over 4 run(s), 100 % of them within 15 % of it; expected 0.92 to 1.08." in f and "15 % more tracking error" in f
    assert "corresponds to --tracking-margin 1.32" in f
    assert ((rep.tracking_session_ratio > 1.14) & (rep.tracking_session_ratio < 1.16)).all() and (rep.tracking_session_ratio_runs == 4).all()
    assert np.allclose(rep.tracking_limit_ms, np.sqrt((1.15 * rep.tracking_session_ratio * rep.tracking_pred_ms) ** 2 + 16.0))
    assert _score_chirped(chirped, "chirp110", "cli") == 1
    # accepted: the operator's margin, recorded, no finding, exit 0 -- and the release check still there
    capsys.readouterr(); acc = _score_chirped(chirped, "chirp110", tracking_margin=1.32); out = capsys.readouterr().out
    assert (acc.status == "scored").all() and (acc.model_mismatch_finding.fillna("") == "").all() and "MODEL MISMATCH" not in out
    assert (acc.tracking_margin == 1.32).all() and (acc.tracking_margin_source == "operator").all() and (acc.release_check == "plant").all()
    assert "TRACKING MARGIN SET BY THE OPERATOR: --tracking-margin 1.32 (the default is 1.15); the release check is unchanged." in out
    assert np.allclose(acc.tracking_limit_ms, np.sqrt((1.32 * acc.tracking_pred_ms) ** 2 + 16.0))
    assert _score_chirped(chirped, "chirp110", "cli", "--tracking-margin", "1.32") == 0


def test_the_quiet_direction_is_a_finding_too(chirped, capsys):
    """R000 recorded with 10 % LESS bandwidth than the runs have: every run reads 0.85 to 0.89 of predicted and b0d18c9
    said nothing at all, while a fault worth 25 % of tracking error would have passed. One finding; each run's limit
    comes down to the session's."""
    capsys.readouterr(); rep = _score_chirped(chirped, "chirp090"); out = capsys.readouterr().out
    assert (rep.status == "scored").all() and out.count("MODEL MISMATCH FINDING") == 1, rep.status.tolist()
    f = rep.model_mismatch_finding.iloc[0]
    assert "session median of 0.88 over 4 run(s)" in f and "12 % less tracking error" in f and "corresponds to --tracking-margin 1.01" in f, f
    assert (rep.tracking_limit_ms < np.sqrt((1.15 * rep.tracking_pred_ms) ** 2 + 16.0) - 1.5).all()       # tighter than the model's
    assert _score_chirped(chirped, "chirp090", "cli") == 1 and _score_chirped(chirped, "chirp090", "cli", "--tracking-margin", "1.01") == 0


def test_a_run_far_from_the_sessions_ratio_is_still_the_run_that_is_flagged(chirped, capsys):
    """The same mismatched session with one arm knocked 8 deg, 0.4 s into its swing (the release is untouched)."""
    capsys.readouterr(); rep = _score_chirped(chirped, "knocked"); out = capsys.readouterr().out
    bad = rep.loc["B-claspknife"].status
    assert bad.startswith("tracking flagged: tracking error ") and "the margin being the session's median ratio 1.18 x 1.15" in bad, bad
    assert bad.endswith("more error than the fitted model predicts for this command, and more than the rest of this session shows")
    assert (rep.drop("B-claspknife").status == "scored").all() and not rep.loc["B-claspknife"].delivered_band_ok
    assert out.count("MODEL MISMATCH FINDING") == 1 and "TRACKING FLAGGED on 1 run(s)" in out


def test_stiction_too_small_for_the_release_check_is_one_finding(chirped, capsys):
    """Every run leaves the hold 5 ms late; the release check reads +1.2 to +3.3 ms and passes. b0d18c9: three
    tracking flags and one run scored. Now one finding that names it among the causes, exit 1; and an operator who
    accepts it with the margin the finding printed has it recorded."""
    capsys.readouterr(); rep = _score_chirped(chirped, "stiction5"); out = capsys.readouterr().out
    assert (rep.status == "scored").all() and (rep.enc_lag_minus_pred_ms.abs() < 4.0).all(), rep.status.tolist()
    assert (rep.tracking_time_err_ms > np.sqrt((1.15 * rep.tracking_pred_ms) ** 2 + 16.0)).sum() == 3
    f = rep.model_mismatch_finding.iloc[0]
    # 1.21 on the five-term scorer (1.22 on six): the session's runs are scored scenarios, so the median moves
    # in the second decimal with the scorer. Re-baseline it when the scorer changes.
    assert "session median of 1.21 over 4 run(s)" in f and "a fault in every run that is too small for the release check (a few ms of stiction" in f
    assert "corresponds to --tracking-margin 1.40" in f and out.count("MODEL MISMATCH FINDING") == 1
    assert _score_chirped(chirped, "stiction5", "cli") == 1 and _score_chirped(chirped, "stiction5", "cli", "--tracking-margin", "1.40") == 0


def test_the_tracking_margin_does_not_turn_the_release_check_off(chirped, capsys):
    """Item 2, the other knob. A margin of 1.6 lets the half-speed start's tracking error through (1.2 to 1.6 of
    predicted); every run is still flagged, by the release check, and the session exits 1. On a good session a margin
    that loose is itself reported: the runs sit at 1.01 of predicted, far below what 1.6 accepts."""
    rep = _score_chirped(chirped, "sluggish", tracking_margin=1.6)
    assert rep.status.str.startswith("tracking flagged: the release is +").all() and not rep.status.str.contains("tracking error").any(), rep.status.tolist()
    assert (rep.tracking_margin == 1.6).all() and (rep.tracking_margin_source == "operator").all() and (rep.release_check == "plant").all()
    assert _score_chirped(chirped, "sluggish", "cli", "--tracking-margin", "1.6") == 1
    ok = _score_chirped(chirped, "good", tracking_margin=1.2)
    assert (ok.status == "scored").all() and (ok.model_mismatch_finding.fillna("") == "").all() and (ok.tracking_margin == 1.2).all()
    assert _score_chirped(chirped, "good", "cli", "--tracking-margin", "1.2") == 0
    loose = _score_chirped(chirped, "good", tracking_margin=1.6)
    assert "the margin is looser than this session needs" in loose.model_mismatch_finding.iloc[0]
    assert np.allclose(loose.tracking_limit_ms, np.sqrt((1.15 * loose.tracking_session_ratio * loose.tracking_pred_ms) ** 2 + 16.0))
    # with the fixed limit, or the override, the margin has nothing to act on, and the report says so
    capsys.readouterr(); none = _score_chirped(chirped, "nochirp", tracking_margin=1.3); out = capsys.readouterr().out
    assert none.tracking_margin.isna().all() and (none.tracking_margin_source.fillna("") == "").all() and "--tracking-margin has no effect without a trusted chirp." in out
    capsys.readouterr(); both = _score_chirped(chirped, "good", tracking_margin=1.3, tracking_limit_ms=30.0); out = capsys.readouterr().out
    assert both.tracking_margin.isna().all() and (both.tracking_limit_ms == 30.0).all() and "--tracking-margin is ignored when --tracking-limit-ms is given." in out


def test_one_run_with_a_relaxing_hold_and_stiction_is_flagged_through_real_files(chirped, capsys):
    """Re-review item 1, through score(): the one run is flagged for its hold, its gain is reported and not applied,
    the other three are scored, and the session has no finding."""
    capsys.readouterr(); rep = _score_chirped(chirped, "relaxed"); out = capsys.readouterr().out
    bad = rep.loc["B-claspknife"]
    assert bad.status.startswith("tracking flagged: the arm held 1.04"), bad.status
    assert abs(bad.enc_hold_gain - 1.04) < 0.003 and abs(bad.enc_late_gain - 1.0) < 0.01 and bad.enc_gain_applied == 1.0
    assert (rep.drop("B-claspknife").status == "scored").all() and (rep.model_mismatch_finding.fillna("") == "").all(), rep.status.tolist()
    assert (rep.tracking_session_ratio_runs == 3).all()                  # a hold the plant does not describe says nothing about the model
    assert "TRACKING FLAGGED on 1 run(s)" in out and _score_chirped(chirped, "relaxed", "cli") == 1


def test_an_encoder_zero_offset_is_not_read_as_a_hold_through_real_files(chirped):
    """Re-review 2026-11-16, item 3: a constant encoder offset taken through zero reads as a gain error over the late
    window's motion (0.5 deg: 27 of 66 good runs hold-flagged). Measured in the idle log and taken out, it is not."""
    rep = _score_chirped(chirped, "offset05")
    assert ((rep.enc_idle_offset_deg - 0.5).abs() < 0.01).all(), rep.enc_idle_offset_deg.tolist()
    assert ((rep.enc_hold_gain - 1.0).abs() < 0.002).all() and ((rep.enc_late_gain - 1.0).abs() < 0.002).all(), (rep.enc_hold_gain.tolist(), rep.enc_late_gain.tolist())
    assert not rep.status.str.contains("the arm held").any(), rep.status.tolist()


def test_a_rig_that_settles_at_98_percent_scores_clean_through_real_files(chirped):
    rep = _score_chirped(chirped, "gain098")
    assert (rep.status == "scored").all() and (rep.model_mismatch_finding.fillna("") == "").all(), rep.status.tolist()
    assert ((rep.enc_hold_gain - 0.98).abs() < 0.002).all(), rep.enc_hold_gain.tolist()
    q = rep.tracking_time_err_ms / rep.tracking_pred_ms
    assert ((q > 0.97) & (q < 1.06)).all() and _score_chirped(chirped, "gain098", "cli") == 0, q.tolist()


def test_the_chirp_suspect_rule_in_numbers():
    """Item 4. Good-looking runs that are all "early" by about the same amount, against a chirp that is trusted but
    not clean: one sentence about the chirp. Releases 3 ms or more early in the median, two thirds of them within
    2.5 ms of it, and a chirp misfit over 5 %."""
    assert (SS.CHIRP_SUSPECT_EARLY_S, SS.CHIRP_SUSPECT_SPREAD_S, SS.CHIRP_SUSPECT_MISFIT) == (0.003, 0.0025, 0.05)
    s = SS._chirp_suspect([-0.005] * 4, 0.06)
    assert s.startswith("SUSPECT THE CHIRP, NOT THE RUNS: across this session the release is -5.0 ms from where the plant identified "
                        "from R000 puts it (median of 4 runs, most of them within 2.5 ms of that), and the model leaves 6.0 % of the chirp unexplained"), s
    assert "look at R000 before the runs" in s
    assert SS._chirp_suspect([-0.005] * 4, 0.049) == "" and SS._chirp_suspect([-0.005] * 4, np.nan) == ""         # a clean chirp is not suspected
    assert SS._chirp_suspect([-0.0029] * 4, 0.06) == "" and SS._chirp_suspect([-0.0031] * 4, 0.06) != ""
    assert SS._chirp_suspect([0.005] * 4, 0.06) == ""                                                              # late is the arm's, not the chirp's
    assert SS._chirp_suspect([-0.005, -0.005], 0.06) == "" and SS._chirp_suspect([-0.005, np.nan, -0.005, -0.005], 0.06) != ""
    assert SS._chirp_suspect([-0.005, -0.005, -0.0076, -0.0024, -0.005, -0.009], 0.06) == ""                       # three of six: they do not agree
    assert SS._chirp_suspect([-0.005, -0.005, -0.0074, -0.0026, -0.005, -0.009], 0.06) != ""                       # five of six


def test_a_chirp_the_drive_only_just_followed_is_said_once_and_about_the_chirp(chirped, capsys):
    """A drive limited to 250 deg/s. The chirp asks for up to 500, the fit is still trusted (R2 0.98, misfit 8 %),
    and it identifies 3.6 ms of delay the rig does not have: good runs are then 4.0 to 4.8 ms "early" and flagged as
    lurches. A-P0 sits at -4.002 ms, 0.002 ms past the 4 ms limit, so whether it is flagged is a matter of
    rounding: three or four of the four runs are flagged (four on main at c48e3a1 already, before the O5 refit, with
    the same per-run values), and the pin takes either. The gate is not redesigned; the session says where to
    look. In memory the same rig at 300 deg/s (misfit
    4.5 %, runs 2.8 to 3.4 ms early, none flagged) says nothing."""
    capsys.readouterr(); rep = _score_chirped(chirped, "sat250"); out = capsys.readouterr().out
    early = rep[rep.status.str.contains("early: a lurch", regex=False)]
    assert len(early) >= 3 and (rep.enc_lag_minus_pred_ms < -3.9).all() and (rep.enc_lag_minus_pred_ms > -4.9).all(), rep.status.tolist()
    s = rep.chirp_suspect.iloc[0]
    assert (rep.chirp_suspect == s).all() and out.count("SUSPECT THE CHIRP, NOT THE RUNS") == 1 and s in out
    assert "the release is -4.1 ms from where the plant identified from R000 puts it (median of 4 runs" in s and "leaves 8.0 % of the chirp unexplained" in s, s
    assert _score_chirped(chirped, "sat250", "cli") == 1                                   # the flags stand; the sentence is where to look
    # runs whose release failed the check say nothing about the model: the session ratio is taken only from the
    # others (scored, release passed, prediction at least MODEL_RATIO_MIN_PRED_MS), whichever runs are early
    kept = rep[~rep.index.isin(early.index) & (rep.tracking_pred_ms >= SS.MODEL_RATIO_MIN_PRED_MS)
               & np.isfinite(rep.tracking_time_err_ms)]
    assert (rep.tracking_session_ratio_runs == len(kept)).all() and len(kept) <= 4 - len(early),         (rep.tracking_session_ratio_runs.tolist(), early.index.tolist())
    assert (rep.model_mismatch_finding.fillna("") == "").all()
    rig = lambda u: _slew_limited(u, 300.0)                                                               # noqa: E731
    fit, why, misfit = _chirp_fit(respond=rig)
    res = [_judge(sid, fit, respond=rig) for sid in SLOW_SIDS]
    assert why == "" and 0.04 < misfit < 0.05 and all(r["why"] == "" and -3.5 < r["dlag_ms"] < -2.6 for r in res), (misfit, [r["dlag_ms"] for r in res])
    assert SS._chirp_suspect([r["dlag_ms"] / 1e3 for r in res], misfit) == ""


def test_the_chirp_gate_in_numbers():
    """Item 5: the gate's three conditions on the function itself. R2 0.95 (0.94 is refused, 0.96 is not), misfit 10 %
    (a 10 % gain error is 9.1 % and passes, 12.5 % is 11.1 % and does not), and a command that moves. The misfit is
    taken only while the chirp command is moving: what the log holds before and after it is not the chirp."""
    t, sp, *_ = SS.chirp_command(); model = SS._model_response(sp, DR.PLANT); fit = dict(DR.PLANT, r2=0.999)
    why, misfit = SS._chirp_trust(fit, sp, model); assert why == "" and misfit < 1e-9
    assert SS._chirp_trust(dict(fit, r2=0.96), sp, model)[0] == ""
    assert SS._chirp_trust(dict(fit, r2=0.94), sp, model)[0] == "the chirp fit is poor (R2 0.940, need 0.95)"
    assert SS._chirp_trust(dict(fit, r2=np.nan), sp, model)[0].startswith("the chirp fit is poor")
    why, misfit = SS._chirp_trust(fit, sp, 1.10 * model); assert why == "" and misfit == pytest.approx(0.0909, abs=1e-3)
    why, misfit = SS._chirp_trust(fit, sp, 1.125 * model)
    assert misfit == pytest.approx(0.1111, abs=1e-3) and why.startswith("the identified model leaves 11 % of the chirp's own motion unexplained (limit 10 %)")
    moving = np.flatnonzero(np.abs(sp - sp[0]) > 1e-6); assert (moving[0], moving[-1], len(sp)) == (200, 29189, 31190)
    junk = model.copy(); junk[:200] += 5.0; junk[29190:] += 5.0
    why, misfit = SS._chirp_trust(fit, sp, junk); assert why == "" and misfit < 1e-9
    junk = model.copy(); junk[10000:12000] += 5.0
    assert "of the chirp's own motion unexplained" in SS._chirp_trust(fit, sp, junk)[0]
    flat = SS._chirp_trust(fit, np.zeros(5000), np.zeros(5000)); assert flat[0] == "the chirp command does not move" and np.isnan(flat[1])
    u = np.zeros(5000); u[1000:1099] = 1.0; assert SS._chirp_trust(fit, u, SS._model_response(u, DR.PLANT))[0] == "the chirp command does not move"
    u[1099] = 1.0; assert SS._chirp_trust(fit, u, SS._model_response(u, DR.PLANT))[0] == ""


def test_score_exits_1_on_a_chirp_finding_alone(chirped, capsys):
    """Item 5. Four good runs and an R000 the drive could not follow: nothing else is wrong with the session, and
    that is still exit status 1."""
    capsys.readouterr(); rep = _score_chirped(chirped, "badchirp"); out = capsys.readouterr().out
    assert (rep.status == "scored").all() and (rep.tracking_limit_source == "fixed").all() and (rep.release_check == "none").all()
    full = rep.reset_index()
    assert not SS.tracking_flags(full) and not SS.release_flags(full) and not SS.error_flags(full) and not SS.model_flags(full)
    assert "CHIRP FINDING: the chirp run R000 is not trusted: the chirp fit is poor (R2 0.911, need 0.95)" in out
    assert _score_chirped(chirped, "badchirp", "cli") == 1 and _score_chirped(chirped, "nochirp", "cli") == 0


def test_a_chirp_that_cannot_be_identified_is_a_finding_and_not_a_crash(chirped, monkeypatch, capsys):
    """Item 5: the try around ilc.identify. The runs are scored against the fixed limit, R000's row says why, and
    `score` exits 1."""
    def boom(*a, **kw): raise ValueError("singular matrix in the fit")
    monkeypatch.setattr(SS.ilc, "identify", boom)
    sess, dirs = chirped
    monkeypatch.setattr(SS, "SESS", sess)
    capsys.readouterr(); rep = SS.score("CH", dirs["good"]); out = capsys.readouterr().out
    r0 = rep[rep.sid == "R-chirp"].iloc[0]; runs = rep[rep.sid != "R-chirp"]
    assert r0.status == "not identified: singular matrix in the fit"
    assert (runs.status == "scored").all() and (runs.tracking_limit_source == "fixed").all() and (runs.tracking_limit_ms == 25.0).all()
    assert SS.chirp_flags(rep) == [("R000", "not identified: singular matrix in the fit")]
    assert "TRACKING LIMIT IS NOT PLANT-DERIVED: the chirp run R000 could not be identified (singular matrix in the fit), so every run" in out
    assert "CHIRP FINDING: the chirp run R000 is not identified: singular matrix in the fit." in out
    with pytest.raises(SystemExit) as ex:
        SS.main(["score", "--name", "CH", "--encoder-dir", dirs["good"]])
    assert ex.value.code == 1


def test_the_model_is_run_past_the_end_of_the_command():
    """Item 5. A recording goes on after the command's last sample and so must the model: on a 3 Hz plant D-short
    ends with the arm still moving at 70 deg/s, and a model cut off at the command's end predicts 18.57 ms where the
    arm's own continuation gives 18.10."""
    plant = PLANTS["3 Hz, zeta 0.7"]
    t, sp, ph = ilc.read_cmd(os.path.join(SS.OUT, "setpoints", "D-short.csv")); sw = ph == 2; t_rel = float(t[np.argmax(sw)])
    assert SS._plant_prediction(t, sp, sw, t_rel, plant)["time_err_ms"] == pytest.approx(18.097, abs=0.02)
    y = SS._model_response(sp, plant)
    cut = SS._tracking(t, sp, sw, t, y, SS._delivered_release(t, sp, t, y, t_rel)["lag_s"])[1]
    assert cut == pytest.approx(18.569, abs=0.02)


def test_a_run_that_was_not_scored_has_no_plant_derived_limit(chirped, slow):
    """Item 5. Under the plant a release-flagged run was never held to a limit: its tracking_limit_ms is empty, not
    25. Under a fixed or overriding number every row carries that number."""
    lurch = _score_chirped(chirped, "lurch")
    assert lurch.loc["S-lam3"].status.startswith("release flagged: ") and np.isnan(lurch.loc["S-lam3"].tracking_limit_ms)
    assert lurch.loc["S-lam3"].tracking_limit_source == "plant" and np.isfinite(lurch.drop("S-lam3").tracking_limit_ms).all()
    assert _score_chirped(chirped, "lurch", tracking_limit_ms=30.0).loc["S-lam3"].tracking_limit_ms == 30.0
    one = slow[0]["one"]
    assert one.loc["B-claspknife"].status.startswith("release flagged: ") and one.loc["B-claspknife"].tracking_limit_ms == 25.0


def test_a_run_the_plant_has_no_prediction_for_is_held_to_the_fixed_limit_and_its_row_says_so(chirped, monkeypatch):
    """Item 5. If the model's own release cannot be found for one command, that run has no prediction. It is held to
    the fixed 25 ms and its row says "fixed", not "plant"; its release is still checked; the other rows are untouched."""
    real = SS._plant_prediction; calls = []
    def one_nan(*a, **kw):
        out = real(*a, **kw); calls.append(1)
        if len(calls) == 2: out["time_err_ms"] = np.nan
        return out
    monkeypatch.setattr(SS, "_plant_prediction", one_nan)
    rep = _score_chirped(chirped, "good")
    odd = rep[rep.tracking_pred_ms.isna()]; rest = rep[rep.tracking_pred_ms.notna()]
    assert len(odd) == 1 and len(rest) == 3 and len(calls) == 4
    assert (odd.status == "scored").all() and (odd.tracking_limit_source == "fixed").all() and (odd.tracking_limit_ms == 25.0).all()
    assert (rest.tracking_limit_source == "plant").all() and (rest.tracking_limit_ms < 19.0).all() and (rep.tracking_session_ratio_runs == 3).all()
    def nan_and_late(*a, **kw):
        out = real(*a, **kw); out["time_err_ms"] = np.nan; out["lag_s"] = out["lag_s"] - 0.010; return out
    monkeypatch.setattr(SS, "_plant_prediction", nan_and_late)
    rep = _score_chirped(chirped, "good")
    assert rep.status.str.startswith("tracking flagged: the release is +1").all() and (rep.tracking_limit_source == "fixed").all(), rep.status.tolist()


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
