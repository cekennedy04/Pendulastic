# tests/test_motor_bench.py
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_bench as mb
import motor_reference as mr
import imu_calibration_config as imu_cfg


NOMINAL = {"f_hz": 0.9, "tau_s": 1.6}


def _run(a0):
    return {"a0_deg": float(a0), **NOMINAL}


# ---------------------------------------------------------------- pulse edges


def test_the_three_pulse_edges_are_found_in_a_clean_preamble():
    t, th = mr.preamble(fs_hz=1000.0)
    edges = mb.find_pulse_edges(t, th)
    assert len(edges) == 3
    assert edges[0] == pytest.approx(mr.LEAD_S, abs=0.005)
    gap = mr.PULSE_WIDTH_S + mr.PULSE_GAP_S
    assert edges[1] - edges[0] == pytest.approx(gap, abs=0.01)


def test_the_edges_survive_noise():
    """The gyro sees these through sensor noise, not as a clean square."""
    rng = np.random.default_rng(0)
    t, th = mr.preamble(fs_hz=1000.0)
    noisy = th + rng.normal(0.0, 0.3, size=len(th))
    assert len(mb.find_pulse_edges(t, noisy)) == 3


def test_too_few_pulses_raises_rather_than_guessing():
    """A missed pulse means the alignment is unknown. Guessing it is how a
    whole session becomes silently wrong.

    The pulse to erase is derived from the constants, not from a sample index:
    LEAD_S puts all three pulses inside the first 0.94 s of a 2.19 s preamble,
    so anything that zeroes "the back half" erases only stillness and the test
    would pass without ever exercising the refusal.
    """
    fs = 1000.0
    t, th = mr.preamble(fs_hz=fs)
    maimed = th.copy()
    start = int(mr.LEAD_S * fs)
    maimed[start:start + int(mr.PULSE_WIDTH_S * fs)] = 0.0
    assert (maimed > mr.PULSE_DEG / 2).sum() < (th > mr.PULSE_DEG / 2).sum(), (
        "the test must actually remove a pulse, or it proves nothing")
    with pytest.raises(mb.SyncError, match="expected 3"):
        mb.find_pulse_edges(t, maimed)


def test_edges_are_found_on_a_full_trial_not_just_an_isolated_preamble():
    """The input align() actually passes. Deriving thresholds from the whole
    trace scales them to the 45 deg swing, so hi lands at 27 deg, the 5 deg
    pulses never cross it, and the function finds ONE edge and refuses every
    real trial. Isolated-preamble tests cannot see this: with no swing present,
    span IS the pulse height."""
    t, th, _rel = mr.full_trace(45.0, 0.9, 1.6, duration_s=12.0, fs_hz=1000.0)
    assert len(mb.find_pulse_edges(t, th)) == 3


def test_a_trace_that_starts_already_high_does_not_count_a_bogus_first_edge():
    """The one failure mode here that fails OPEN: recording t[0] as an edge
    returns the right COUNT with one bogus member, which no downstream check
    can catch.

    Constructed so it reaches the armed-flag guard. An earlier version simply
    cut into pulse 1, which made the first 5% of the window all pulse-height,
    so baseline == max, span == 0, and it exited through "signal never rises"
    -- a different path entirely. It passed while proving nothing.
    """
    fs = 1000.0
    _t, th = mr.preamble(fs_hz=fs)
    lead = np.full(30, mr.PULSE_DEG)            # starts high, but only briefly
    sig = np.concatenate([lead, th])
    t = np.arange(len(sig)) / fs
    # Baseline must still resolve low, or this exercises the wrong branch.
    win = t <= mr.pulse_search_window_s()
    assert np.median(sig[win][: int(win.sum() * 0.05)]) == 0.0, (
        "test setup is wrong: baseline is not low, so the armed guard is not reached")
    assert len(mb.find_pulse_edges(t, sig)) == 3, (
        "the leading high block must not be counted as a fourth edge")


@pytest.mark.parametrize("fs", [1000.0, 500.0, 250.0, 200.0, 104.72, 100.0, 60.0])
def test_detection_survives_sample_rates_that_are_not_1_khz(fs):
    """A phone samples near 100-200 Hz and irregularly, not at 1 kHz.

    Deriving the search window from the preamble's nominal length failed here:
    preamble() rounds each segment per fs, so the real preamble runs short at
    250 Hz (-0.006 s) and 104.72 Hz (-0.012765 s), the window overran into the
    swing, and detection failed with "found 1 pulse edges".

    This also pins the debounce: the minimum dwell is a fraction of
    PULSE_WIDTH_S in SECONDS, so at 60 Hz a 5-sample pulse must still clear it.
    """
    t, th, _rel = mr.full_trace(90.0, 0.9, 1.6, duration_s=12.0, fs_hz=fs)
    assert len(mb.find_pulse_edges(t, th)) == 3


def test_a_single_sample_spike_is_not_counted_as_a_pulse_edge():
    """The debounce. Phone edges are EMA-smoothed and lag about 40 ms while
    thresholds sit at 2-3 deg on a 5 deg pulse, so a one-sample excursion above
    hi is a detector artefact, not a pulse. A measured mis-fire of this kind
    made align() report a 168% "rate error" -- a number no clock produces.

    The spike is placed at the midpoint of a GAP, after the detector has had
    time to re-arm, so it reaches the edge-recording branch rather than being
    swallowed by the hysteresis state.
    """
    fs = 1000.0
    t, th = mr.preamble(fs_hz=fs)
    spiked = th.copy()
    spike_at = mr.LEAD_S + mr.PULSE_WIDTH_S + mr.PULSE_GAP_S / 2.0
    i = int(round(spike_at * fs))
    spiked[i] = mr.PULSE_DEG
    assert spiked[i] > 0.6 * mr.PULSE_DEG, (
        "the spike must clear the hi threshold, or the test proves nothing")
    assert (spiked > 0.6 * mr.PULSE_DEG).sum() == (th > 0.6 * mr.PULSE_DEG).sum() + 1
    edges = mb.find_pulse_edges(t, spiked)
    assert len(edges) == 3, "a one-sample spike must not become a fourth edge"
    assert edges[0] == pytest.approx(mr.LEAD_S, abs=0.005)


# --------------------------------------------------------------------- align


def _rig_pair(lag_s=0.37, a0=45.0, fs=1000.0):
    """An encoder trace and a phone trace of the same motion, offset in time.

    Duration comes from run_duration_s rather than a flat 8 s: the tail-drift
    gate in score_trial needs the tail to span 5*tau past release plus two
    full periods, and at tau=1.6/f=0.9 an 8 s swing is shorter than the 5*tau
    settle alone, so the gate would always fail and tail_drift_deg_s would
    always be NaN.
    """
    run = _run(a0)
    t, th, rel = mr.full_trace(a0_deg=a0, f_hz=run["f_hz"], tau_s=run["tau_s"],
                               duration_s=mr.run_duration_s(run), fs_hz=fs)
    return t, th, t + lag_s, th.copy(), rel


def _phone_convention(enc_theta, a0):
    """The phone's PRODUCTION angle for the same motion.

    imu_calibration_tuner emits `180 - |rotation from the zero pose|`, and the
    zero pose is captured at release, i.e. at full commanded amplitude. So a
    45 deg run reads 180 at release and 135 at rest -- a completely different
    datum from the encoder's signed deviation about zero.
    """
    return 180.0 - np.abs(np.asarray(enc_theta, float) - a0)


def test_alignment_recovers_a_known_clock_offset():
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.37)
    out = mb.align(et, eth, it, ith)
    assert out["lag_s"] == pytest.approx(0.37, abs=0.01)


def test_alignment_puts_the_two_traces_on_one_clock():
    """Resampled onto ONE grid using the CORRECTED phone clock.

    The previous version differenced imu_theta against enc_theta index by
    index. _rig_pair passes `th` and `th.copy()` and align() never touches
    imu_theta, so that difference was identically zero for any lag whatsoever
    -- including a completely broken align(). The teeth assertion below exists
    so this can never silently become true by construction again.
    """
    et, eth, it, ith, _rel = _rig_pair(lag_s=-0.22)
    out = mb.align(et, eth, it, ith)

    ct, cth = out["imu_t"], out["imu_theta"]
    lo = max(out["enc_t"][0], ct[0])
    hi = min(out["enc_t"][-1], ct[-1])
    grid = out["enc_t"][(out["enc_t"] >= lo) & (out["enc_t"] <= hi)]
    ref = np.interp(grid, out["enc_t"], out["enc_theta"])
    test = np.interp(grid, ct, cth)
    assert np.nanmax(np.abs(test - ref)) < 0.5, "aligned traces should nearly coincide"

    off_by_half_a_second = np.interp(grid, ct + 0.5, cth)
    assert np.nanmax(np.abs(off_by_half_a_second - ref)) > 5.0, (
        "the assertion above must be able to fail on a wrong lag")


def test_the_offset_spread_is_reported_even_when_it_passes():
    """The spread bound only catches a 7.6% disagreement over this lever arm,
    so a phone whose edges are 2% further apart -- 240 ms across a 12 s trial
    if it were a rate effect -- passes. That is acceptable for a crystal-based
    phone clock, but the number must be VISIBLE rather than merely under a
    threshold, or nobody can tell a good sync from a barely-passing one.

    The key is `offset_spread_rate`, not `clock_rate_error`: it is an observed
    spread per unit lever arm, and naming a cause in it sent one investigation
    to the wrong place."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0)
    out = mb.align(et, eth, it * 1.02 + 0.3, ith)
    assert out["offset_spread_rate"] == pytest.approx(0.02, abs=0.005)
    assert out["pulse_offset_spread_s"] > 0.0
    assert "clock_rate_error" not in out, "a cause must not be baked into a key name"


def test_the_alignment_refusal_reports_the_edges_and_names_no_cause():
    """A measured case reported 168% rate error, which no clock does; the real
    cause was edge mis-detection. The refusal must hand the operator the
    evidence -- the observed edge times and their spread -- and stop there."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0)
    with pytest.raises(mb.SyncError) as excinfo:
        mb.align(et, eth, it * 1.10 + 0.3, ith)
    msg = str(excinfo.value)
    assert "encoder edges at" in msg and "phone edges at" in msg
    assert "per-pulse offsets" in msg
    assert "clock" not in msg.lower(), "the message must not diagnose a cause"
    assert "rate error" not in msg.lower()


def test_the_measured_edges_are_returned_so_a_misdetection_is_visible():
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.25)
    out = mb.align(et, eth, it, ith)
    assert len(out["enc_edges_s"]) == mr.PULSE_COUNT
    assert len(out["imu_edges_s"]) == mr.PULSE_COUNT
    assert out["enc_edges_s"][0] == pytest.approx(mr.LEAD_S, abs=0.005)
    assert out["pulse_offsets_s"] == pytest.approx([0.25] * mr.PULSE_COUNT, abs=0.01)


def test_release_time_is_reported_on_the_common_clock():
    et, eth, it, ith, rel = _rig_pair(lag_s=0.1)
    out = mb.align(et, eth, it, ith)
    assert out["t_release_s"] == pytest.approx(rel, abs=0.02)


def test_release_comes_from_the_shared_definition_not_a_second_copy():
    """align() re-derived the release as PULSE_COUNT*(WIDTH+GAP) + PRE_ROLL_S
    and went 5 s wrong the moment POST_PULSE_S and the RAMP_S lift were added
    between the pulses and the release. Every score still came back 0.93-0.99,
    because the extractor finds a peak wherever it is told to start.

    Two things are pinned here. First, the value must equal the shared
    definition exactly, not approximately. Second, it must be the offset from
    the FIRST PULSE EDGE (release_offset_s(), 8.99 s) and not full_trace's
    t_release_s (9.19 s, measured from trace start): the two differ by exactly
    LEAD_S, and because the arm HOLDS at amplitude through PRE_ROLL the encoder
    reads the same 45.0 deg at both instants, so the error is invisible in A0.
    """
    et, eth, it, ith, rel = _rig_pair(lag_s=0.0)
    out = mb.align(et, eth, it, ith)
    edge0 = out["enc_edges_s"][0]
    assert out["t_release_s"] == pytest.approx(edge0 + mr.release_offset_s(), abs=1e-9)
    assert out["t_release_s"] == pytest.approx(rel, abs=1e-9)
    assert out["t_release_s"] != pytest.approx(edge0 + rel, abs=1e-6), (
        "adding full_trace's t_release_s to an edge time lands LEAD_S late")


# ---------------------------------------------------------------- score_trial


def test_a_perfect_phone_recovers_the_commanded_amplitude():
    """The ceiling case. If A0 is wrong when the phone trace IS the encoder
    trace, the defect is in extraction, not in the sensor.

    The lag is deliberately NON-ZERO. At lag 0 the two traces share a time
    base, so rmse_deg was 0 by construction and asserted nothing at all.
    """
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.19, a0=45.0)
    aligned = mb.align(et, eth, it, ith)
    out = mb.score_trial(aligned, _run(45.0))
    assert out["a0_true"] == 45.0
    assert out["a0_ratio"] == pytest.approx(1.0, abs=0.15)
    assert abs(out["rmse_deg"]) < 0.5

    unaligned = dict(aligned)
    unaligned["imu_t"] = aligned["imu_t"] + 0.19       # undo the lag correction
    assert abs(mb.score_trial(unaligned, _run(45.0))["rmse_deg"]) > 2.0, (
        "rmse_deg must respond to a wrong lag, or it is measuring nothing")


def test_a_phone_reading_high_is_reported_as_a_ratio_above_one():
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    out = mb.score_trial(mb.align(et, eth, it * 1.0, ith * 1.5), _run(45.0))
    assert out["a0_ratio"] > 1.2
    assert out["a0_error_deg"] > 0


def test_the_encoder_control_ratio_is_reported():
    """a0_enc was computed and thrown away. It is the control that separates
    the extractor from the sensor: if the encoder itself does not come back at
    1.00, nothing the phone reports can be read as a sensor property."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    out = mb.score_trial(mb.align(et, eth, it, ith * 1.5), _run(45.0))
    assert out["a0_enc_ratio"] == pytest.approx(1.0, abs=0.05)
    assert out["a0_ratio"] > 1.2, "the phone ratio must move while the control does not"


def test_a_release_placed_late_is_refused_by_the_reference_self_check():
    """The harness never validated its own reference. A t_release 0.2 s late
    truncates the encoder's own A0 to 22.6 of a commanded 45 -- and without
    this check every phone score in the session would still have looked fine.
    """
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    aligned = mb.align(et, eth, it, ith)
    aligned["t_release_s"] = aligned["t_release_s"] + 0.2
    with pytest.raises(mb.SyncError) as excinfo:
        mb.score_trial(aligned, _run(45.0))
    assert "the reference failed its own check" in str(excinfo.value)


def test_a_wrong_encoder_log_is_refused_rather_than_scored():
    """The other half of the same check: the release is right and the ENCODER
    is wrong. Either way every derived number would be wrong and plausible."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    aligned = mb.align(et, eth, it, ith)
    with pytest.raises(mb.SyncError, match="reference failed its own check"):
        mb.score_trial(aligned, _run(30.0))   # commanded 30, encoder swung 45


# --------------------------------------------------------- angle conventions


def test_a_phone_convention_trace_is_converted_before_it_is_compared():
    """The gap that let C2 survive: no test ever fed a trace in the datum the
    phone actually emits.

    Unconverted, a 45 deg run reports about 135 deg of "RMSE" -- the offset
    between the two datums, which GROWS as the swing shrinks. Converted to
    deviation from the phone's own settled neutral it lands at 0.001 deg.
    """
    a0 = 45.0
    et, eth, _it, _ith, rel = _rig_pair(lag_s=0.0, a0=a0)
    phone = _phone_convention(eth, a0)
    assert phone.min() > 100.0 and phone.max() == pytest.approx(180.0), (
        "the fixture must actually be in the phone's datum")

    post = et >= rel
    raw = float(np.sqrt(np.mean((phone[post] - eth[post]) ** 2)))
    assert raw > 100.0, (
        f"the unconverted comparison must be badly wrong ({raw:.1f} deg), "
        "or this test is not exercising the defect")

    out = mb.score_trial(mb.align(et, eth, et, phone), _run(a0))
    assert out["rmse_deg"] < 0.5, (
        f"converted RMSE should be near zero, got {out['rmse_deg']}")
    assert out["rmse_neutral_deg"] == pytest.approx(180.0 - a0, abs=1.0), (
        "the neutral actually used must be the phone's datum, not zero")
    assert out["a0_ratio"] == pytest.approx(1.0, abs=0.15)


def test_a_units_bug_is_refused_rather_than_reported_as_accuracy():
    """An error larger than the whole commanded motion is not an accuracy
    figure. Reporting one is how "the phone is 135 deg out" survived as a
    plausible-looking result for a whole session."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    with pytest.raises(mb.ConventionError) as excinfo:
        mb.score_trial(mb.align(et, eth, it, ith * 10.0), _run(45.0))
    msg = str(excinfo.value)
    assert "convention or units bug" in msg
    assert "45.00 deg" in msg, "the refusal must name the commanded amplitude"


def test_a_convention_refusal_is_distinguishable_from_a_sync_refusal():
    assert issubclass(mb.ConventionError, mb.BenchRefusal)
    assert issubclass(mb.SyncError, mb.BenchRefusal)
    assert not issubclass(mb.ConventionError, mb.SyncError)


# ---------------------------------------------------------------- tail drift


def test_tail_drift_is_measured_not_assumed():
    """The corpus estimate is -0.735 deg/s from indirect evidence. Here it is
    a fitted slope on a trace whose true drift is known."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    drifted = ith + 0.4 * (it - it[0])
    out = mb.score_trial(mb.align(et, eth, it, drifted), _run(45.0))
    assert out["tail_drift_deg_s"] == pytest.approx(0.4, abs=0.15)


def test_the_encoder_tail_is_fitted_as_the_null_control():
    """Fitted on the phone alone the drift number has nothing to be read
    against. The encoder is known-still in the same window and through the same
    estimator, so if it shows the same slope the drift belongs to the window,
    not the sensor."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    drifted = ith + 0.4 * (it - it[0])
    out = mb.score_trial(mb.align(et, eth, it, drifted), _run(45.0))
    assert abs(out["tail_drift_enc_deg_s"]) < 0.05, (
        "the encoder tail must come back flat, or the estimator is inventing drift")
    assert out["tail_drift_deg_s"] - out["tail_drift_enc_deg_s"] > 0.25


# --------------------------------------------------------- refusals from replay


def test_a_phone_that_produced_nothing_is_refused_by_name():
    """Below about 10 deg the peak swing rate a0*2*pi*f falls under the phone's
    57.3 deg/s flex-capture threshold, it never captures a zero, and
    replay_trial returns empty arrays. That is the EXPECTED outcome and the
    single most interesting finding this bench can produce -- it must not
    arrive as a bare numpy ValueError from et.min()."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    aligned = mb.align(et, eth, it, ith)
    aligned["imu_t"] = np.array([])
    aligned["imu_theta"] = np.array([])
    aligned["imu_refusal_reason"] = "flex_axis_never_captured"
    aligned["imu_refusal_detail"] = "never reached 1.0 rad/s"
    with pytest.raises(mb.SyncError) as excinfo:
        mb.score_trial(aligned, _run(45.0))
    msg = str(excinfo.value)
    assert "flex_axis_never_captured" in msg
    assert "never reached 1.0 rad/s" in msg


@pytest.mark.skipif(
    not hasattr(__import__("imu_calibration_tuner"), "replay_trial_detailed"),
    reason="needs imu_calibration_tuner.replay_trial_detailed, which lands with "
           "bench/retest-diagnostics; score_four_ways replays raw samples through it")
def test_a_refused_replay_becomes_a_recorded_refusal_not_a_crash():
    """score_four_ways must use the detailed replay so the reason survives, and
    must still return all four convention keys so summarise can count it."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    aligned = mb.align(et, eth, it, ith)
    out = mb.score_four_ways([], aligned, _run(45.0))   # [] replays to nothing
    assert set(out) == set(mb.CONVENTIONS)
    for key, res in out.items():
        assert res["refused"] is True, key
        assert res["refusal_reason"] == "no_samples", key
        assert res["refusal_detail"], key
        assert not np.isfinite(res["a0_ratio"]), key


# ------------------------------------------------------------ four conventions


def test_all_four_conventions_are_scored():
    """The phone ships detrend=False on a 100 Hz grid; analysis uses True. The
    flag moves phone PT7 by 0.363 against optical's 0.050, and nothing has ever
    said which is closer to TRUTH. This is where that gets answered."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    aligned = mb.align(et, eth, it, ith)
    out = mb.score_four_ways(None, aligned, _run(45.0))
    assert set(out) == set(mb.CONVENTIONS)
    for key, res in out.items():
        # Asserting the KEY exists is not a test. An earlier version did
        # exactly that and passed while all four ratios were NaN.
        r = res["a0_ratio"]
        assert np.isfinite(r), f"{key} returned {r}"
        assert 0.9 < r < 1.1, f"{key} recovered {r:.3f} of a clean known amplitude"


def test_the_detrend_flag_changes_the_answer_on_a_drifting_trace():
    """On a clean trace the two conventions should nearly agree; on a drifting
    one they must not, or the comparison could not detect the defect it exists
    for."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    drifted = ith + 0.6 * (it - it[0])
    out = mb.score_four_ways(None, mb.align(et, eth, it, drifted), _run(45.0))
    a = out["100hz_detrend_false"]["a0_imu"]
    b = out["100hz_detrend_true"]["a0_imu"]
    assert abs(a - b) > 1.0, "detrend should matter on a drifting trace"


def test_the_replay_config_comes_from_the_loader_and_is_recorded(monkeypatch):
    """The config was transcribed into a literal here, which made the bench
    measure settings that stopped existing the moment the auto-tuner rewrote
    imu_calibration_config.json. Load it, and record what was resolved."""
    sentinel = dict(imu_cfg.DEFAULT_CONFIG)
    sentinel["beta"] = 0.0777
    sentinel["ema_alpha"] = 0.123
    monkeypatch.setattr(imu_cfg, "load_config", lambda: dict(sentinel))

    assert mb._replay_config()["beta"] == 0.0777

    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0, a0=45.0)
    out = mb.score_four_ways(None, mb.align(et, eth, it, ith), _run(45.0))
    for key, res in out.items():
        assert res["replay_config"]["beta"] == 0.0777, key
        assert res["replay_config"]["ema_alpha"] == 0.123, key


def test_the_replay_config_is_not_a_hardcoded_literal():
    live = imu_cfg.load_config()
    resolved = mb._replay_config()
    for k in ("beta", "ema_alpha", "flex_axis_capture", "gravity_seed"):
        assert resolved[k] == live[k], f"{k} must track the persisted config"


# ------------------------------------------------------------------ summarise


def _score(a0_true, ratio, drift=0.0, enc_ratio=1.0, refused=False, reason=""):
    return {"a0_true": a0_true,
            "a0_imu": a0_true * ratio,
            "a0_ratio": ratio,
            "a0_enc": a0_true * enc_ratio,
            "a0_enc_ratio": enc_ratio,
            "a0_error_deg": a0_true * (ratio - 1),
            "tail_drift_deg_s": drift,
            "tail_drift_enc_deg_s": 0.0,
            "rmse_deg": 0.1,
            "refused": refused,
            "refusal_reason": reason,
            "refusal_detail": ""}


def _row(a0_true, ratio, drift=0.0, enc_ratio=1.0, refused=False, reason="",
         overrides=None):
    a0_true = float(a0_true)
    scores = {k: _score(a0_true, ratio, drift, enc_ratio, refused, reason)
              for k in mb.CONVENTIONS}
    for k, v in (overrides or {}).items():
        scores[k].update(v)
    return {"run": {"a0_deg": a0_true}, "scores": scores}


def test_the_summary_finds_where_extraction_breaks():
    """Good above 25 deg, bad below: the breakdown point must be reported, and
    it is the number the excursion floor has never had."""
    rows = [_row(a0, 1.0) for a0 in (90, 50, 30, 28, 25)] + \
           [_row(a0, 2.5) for a0 in (22, 20, 15, 10, 5)]
    s = mb.summarise(rows)
    assert s["n"] == 10
    assert 22.0 <= s["breakdown_a0_deg"] <= 25.0


def test_no_breakdown_is_reported_as_none_not_as_zero():
    rows = [_row(a0, 1.0) for a0 in (90, 50, 25, 10, 5)]
    assert mb.summarise(rows)["breakdown_a0_deg"] is None


def test_amplitudes_that_extracted_nothing_are_not_counted_as_recovery():
    """np.median propagates NaN and abs(nan - 1.0) > tol is False, so three
    amplitudes that returned NaN on every repeat were reported as
    breakdown_a0_deg = None -- "never broke down" -- when extraction had failed
    completely."""
    rows = [_row(a0, 1.0) for a0 in (90, 50, 25)] + \
           [_row(a0, float("nan")) for a0 in (20, 15, 10)]
    s = mb.summarise(rows)
    assert s["breakdown_a0_deg"] == 25.0, "NaN is a failure, not a pass"
    for a0 in (20.0, 15.0, 10.0):
        d = s["a0_detail_by_amplitude"][a0]
        assert d["n_finite"] == 0 and d["n_total"] == 1
        assert d["finite_fraction"] == 0.0
        assert s["failed_by_amplitude"][a0] is True


def test_an_amplitude_that_mostly_returned_nothing_is_the_breakdown():
    """Half the repeats NaN is not a passing amplitude with a small n; the
    finite fraction IS the signal."""
    rows = [_row(a0, 1.0) for a0 in (90, 50, 25)]
    rows += [_row(20, float("nan")), _row(20, float("nan")), _row(20, 1.0)]
    s = mb.summarise(rows)
    d = s["a0_detail_by_amplitude"][20.0]
    assert d["n_finite"] == 1 and d["n_total"] == 3
    assert d["ratio_median"] == pytest.approx(1.0), (
        "the surviving repeat still looks perfect -- which is the trap")
    assert s["failed_by_amplitude"][20.0] is True
    assert s["breakdown_a0_deg"] == 25.0


def test_one_bad_row_at_the_top_is_not_reported_as_the_breakdown():
    """Scanning down from the top and stopping at the first failure reported
    "breaks at 90" for a single noisy 90 deg row. A breakdown is a FLOOR:
    the largest amplitude below which every amplitude fails."""
    rows = [_row(90, 2.5)] + [_row(a0, 1.0) for a0 in (50, 30, 25, 20, 10)]
    s = mb.summarise(rows)
    assert s["breakdown_a0_deg"] is None
    assert s["failed_by_amplitude"][90.0] is True, "the bad row is still visible"
    assert s["a0_ratio_by_amplitude"][90.0] == pytest.approx(2.5)


def test_a_non_monotone_curve_still_finds_the_real_floor():
    rows = [_row(90, 2.5)] + [_row(a0, 1.0) for a0 in (50, 30, 25)] + \
           [_row(a0, 2.5) for a0 in (20, 10, 5)]
    s = mb.summarise(rows)
    assert s["breakdown_a0_deg"] == 25.0
    curve = s["a0_ratio_by_amplitude"]
    assert sorted(curve) == [5.0, 10.0, 20.0, 25.0, 30.0, 50.0, 90.0], (
        "the full ratio curve is the headline result and must be exposed")


def test_the_summary_names_the_convention_closest_to_truth():
    """One convention is made clearly best and the other three clearly worse,
    so the assertion can only pass for the right reason."""
    rows = [_row(a0, 1.0) for a0 in (90, 50, 25)]
    for r in rows:
        r["scores"]["100hz_detrend_true"]["a0_ratio"] = 1.02   # nearly exact
        r["scores"]["100hz_detrend_false"]["a0_ratio"] = 1.40
        r["scores"]["20hz_detrend_true"]["a0_ratio"] = 1.55
        r["scores"]["20hz_detrend_false"]["a0_ratio"] = 1.80
    s = mb.summarise(rows)
    assert s["best_convention"] == "100hz_detrend_true"
    assert s["best_convention_n"] == 3


def test_a_convention_cannot_win_by_being_absent_from_the_hard_trials():
    """Compared over their own subsets, a convention that returns NaN on the
    hard trials is judged only on the easy ones. Here 100hz_detrend_true is
    scored on 2 trials and wins with a median error of 0.10 against
    100hz_detrend_false's 0.20 over all 6 -- while on the trials where BOTH
    actually ran, false is better by 0.03 to 0.10."""
    easy = []
    for a0 in (90, 50):
        r = _row(a0, 1.50, overrides={
            "100hz_detrend_true": {"a0_ratio": 1.10},
            "100hz_detrend_false": {"a0_ratio": 1.03}})
        easy.append(r)
    hard = []
    for a0 in (30, 25, 20, 15):
        r = _row(a0, 1.50, overrides={
            "100hz_detrend_true": {"a0_ratio": float("nan")},
            "100hz_detrend_false": {"a0_ratio": 1.20}})
        hard.append(r)
    s = mb.summarise(easy + hard)
    assert s["best_convention_n"] == 2, "only the trials all four scored count"
    assert s["best_convention"] == "100hz_detrend_false"
    errs = s["convention_median_abs_error"]
    assert errs["100hz_detrend_false"] == pytest.approx(0.03, abs=1e-6)
    assert errs["100hz_detrend_true"] == pytest.approx(0.10, abs=1e-6)


def test_refusals_are_reported_as_a_finding_per_amplitude():
    """"The phone refused 3 of 3 repeats at 10 deg" is a result. Dropping those
    rows would make the sweep look like it merely thinned out."""
    rows = [_row(a0, 1.0) for a0 in (90, 50, 25)]
    rows += [_row(10, float("nan"), refused=True,
                  reason="flex_axis_never_captured") for _ in range(3)]
    s = mb.summarise(rows)
    assert s["n_refused"] == 3
    assert s["refusals_by_amplitude"][10.0] == 3
    assert s["failed_by_amplitude"][10.0] is True
    assert any("refused 3 of 3 repeats at 10 deg" in line
               for line in s["refusal_summary"]), s["refusal_summary"]
    assert "flex_axis_never_captured" in " ".join(s["refusal_summary"])


def test_the_summary_surfaces_the_encoder_control_beside_the_phone():
    """Question 1 of the experiment is whether a bad number is the extractor or
    the sensor. That is unanswerable unless both curves are reported."""
    rows = [_row(a0, 1.4, enc_ratio=1.0) for a0 in (90, 50, 25)]
    s = mb.summarise(rows)
    assert s["a0_enc_ratio_by_amplitude"][25.0] == pytest.approx(1.0)
    assert s["a0_ratio_by_amplitude"][25.0] == pytest.approx(1.4)


def test_the_summary_reports_the_drift_count_and_the_null_control():
    """A drift figure with no n and no null control cannot be read at all."""
    rows = [_row(a0, 1.0, drift=0.5) for a0 in (90, 50, 25)]
    rows += [_row(20, 1.0, drift=float("nan"))]
    s = mb.summarise(rows)
    assert s["n_drift"] == 3, "the trial too short to fit must not be counted"
    assert s["drift_median_deg_s"] == pytest.approx(0.5)
    assert s["n_drift_enc"] == 4
    assert s["drift_enc_median_deg_s"] == pytest.approx(0.0)


# ------------------------------------------- controller fixes after re-review


def _resample(t, th, dt, phase):
    """Put a 1 kHz trace onto a coarser grid at a chosen sampling phase.

    Regenerating the trajectory at a lower fs_hz is NOT the same thing: it
    rebuilds a perfect square with round(PULSE_WIDTH_S * fs) full-height
    samples every time. A real phone samples an already-continuous signal, so
    where the samples LAND relative to a pulse edge varies with phase, and that
    is the case the dwell got wrong.
    """
    grid = np.arange(t[0] + phase * dt, t[-1], dt)
    return grid, np.interp(grid, t, th)


def test_edges_are_found_on_the_phones_own_20_hz_grid_at_every_phase():
    """The debounce must not make the production grid unalignable.

    imu_calibration_tuner.TICK_S is 0.05 s and PULSE_WIDTH_S is 0.08 s, so a
    pulse lands on one or two samples depending on phase. Charging a one-sample
    run zero dwell made this 0 of 25 phases -- a regression against the
    pre-debounce detector's 25 of 25 -- on the grid the app actually saves.
    """
    t, th, _ = mr.full_trace(45.0, 0.9, 1.6)
    for dt, name in ((0.05, "20 Hz"), (0.02, "50 Hz"), (0.01, "100 Hz")):
        found = 0
        for k in range(25):
            g, x = _resample(t, th, dt, k / 25.0)
            try:
                found += len(mb.find_pulse_edges(g, x)) == 3
            except mb.SyncError:
                pass
        assert found == 25, f"{name}: only {found}/25 sampling phases aligned"


def test_a_single_sample_spike_is_still_rejected_at_1_khz():
    """The credit must not undo the debounce where the grid can resolve it.

    Guards the fix above from being "fixed" by dropping the dwell entirely: at
    1 kHz one sample is 0.001 s against a 0.032 s dwell and must not count.
    """
    t, th, _ = mr.full_trace(45.0, 0.9, 1.6)
    x = th.copy()
    # INSIDE pulse_search_window_s() (1.69 s) and in the quiet gap after the
    # third pulse, at pulse height so it does not move the hi/lo thresholds.
    # An earlier version of this test put the spike at 5.0 s, outside the
    # search window, where the detector never saw it -- the test passed with
    # the dwell removed entirely and proved nothing.
    spike = int(1.20 * 1000.0)
    assert spike < len(x) and 1.20 < mb.mr.pulse_search_window_s()
    x[spike] = mr.PULSE_DEG
    assert len(mb.find_pulse_edges(t, x)) == 3


@pytest.mark.parametrize("tau_s,f_hz,frac", [
    (1.6, 0.6, 0.50), (3.2, 0.6, 0.35), (1.6, 1.2, 0.35), (3.2, 0.9, 0.35)])
def test_the_neutral_refuses_wherever_the_drift_fit_refuses(tau_s, f_hz, frac):
    """Two readings of one settled tail must agree that it exists.

    The fallbacks this replaced returned a confident wrong neutral in exactly
    the trials _tail_drift refused: against a true 0.0 on a 45 deg run they
    gave +1.6511, -1.1187 and -0.6885 deg. A wrong neutral is subtracted from
    every RMSE sample and never appears in the result.
    """
    run = {"a0_deg": 45.0, "f_hz": f_hz, "tau_s": tau_s}
    t, th = mr.swing(45.0, f_hz, tau_s, duration_s=mr.run_duration_s(run))
    n = int(len(t) * frac)
    tt, xx = t[:n], th[:n]
    neutral = mb._settled_neutral(tt, xx, run)
    drift = mb._tail_drift(tt, xx, run)
    assert not np.isfinite(drift), "fixture no longer truncates past the gate"
    assert not np.isfinite(neutral), (
        f"neutral returned {neutral:.4f} where the drift fit refused; a true "
        f"0.0 tail cannot be established from this trace")


def test_a_full_length_trial_still_establishes_its_neutral():
    """The gate must refuse short traces WITHOUT refusing the designed run.

    run_duration_s guarantees 5*tau + 4/f + 2 s of post-release span precisely
    so the primary branch always wins; if this ever refuses, the gate is wrong
    rather than the trial.
    """
    run = {"a0_deg": 45.0, **NOMINAL}
    t, th, _ = mr.full_trace(45.0, 0.9, 1.6)
    phone = 180.0 - np.abs(th - 45.0)
    scores = mb.score_trial(mb.align(t, th, t, phone), run)
    assert np.isfinite(scores["rmse_neutral_deg"])
    assert scores["rmse_deg"] < 1.0
