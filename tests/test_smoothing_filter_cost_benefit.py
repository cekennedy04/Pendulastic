"""smoothing_filter_cost_benefit.py: the filter maths, the jitter and extrema
measures, and the corpus plumbing run on a synthetic recording. Needs no
participant data."""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import smoothing_filter_cost_benefit as sf  # noqa: E402


def _loop_ema(x, alpha):
    """imu_calibration_tuner.replay_trial's own smoothing loop."""
    out, state = np.empty(len(x)), None
    for i, a in enumerate(x):
        if math.isnan(a):
            state, out[i] = None, a
        else:
            state = a if state is None else alpha * a + (1.0 - alpha) * state
            out[i] = state
    return out


def test_constants_are_the_replays():
    import imu_calibration_config as cfg
    import imu_calibration_tuner as tuner
    assert sf.TICK_S == tuner.ANALYSIS_TICK_S
    assert sf.TUNED_TICK_S == tuner.TICK_S
    assert sf.SHIPPED_ALPHA_AT_TUNED_TICK == cfg.REFERENCE_REPLAY_CONFIG["ema_alpha"]
    assert sf.shipped_alpha() == pytest.approx(
        tuner._rescale_ema_alpha(cfg.REFERENCE_REPLAY_CONFIG["ema_alpha"], tuner.ANALYSIS_TICK_S),
        abs=1e-15)
    assert sf.shipped_alpha() == pytest.approx(0.1294, abs=1e-4)
    assert sf.SHIPPED_TAU_S == pytest.approx(0.0721, abs=1e-4)
    # The shipped filter is the tau = 72 ms member of the EMA family.
    assert sf.alpha_for_tau(sf.SHIPPED_TAU_S) == pytest.approx(sf.shipped_alpha(), abs=1e-12)


def test_ema_is_the_replays_loop_and_resets_on_nan():
    rng = np.random.default_rng(0)
    x = 150.0 + rng.normal(0, 5, 400)
    x[0] = np.nan
    x[200:203] = np.nan
    a = sf.shipped_alpha()
    mine, ref = sf.ema(x, a), _loop_ema(x, a)
    assert np.array_equal(np.isnan(mine), np.isnan(ref))
    assert np.nanmax(np.abs(mine - ref)) < 1e-10
    assert mine[203] == x[203]                      # reseeded after the gap
    assert np.array_equal(sf.ema(x, 1.0)[1:200], x[1:200])


def test_finite_runs():
    x = np.array([np.nan, 1.0, 2.0, np.nan, np.nan, 3.0])
    assert sf.finite_runs(x) == [slice(1, 3), slice(5, 6)]
    assert sf.finite_runs(np.array([np.nan, np.nan])) == []


@pytest.mark.parametrize("f_hz", [0.8, 1.0, 2.0])
def test_measured_response_matches_the_ema_transfer_function(f_hz):
    a = sf.shipped_alpha()
    g_formula, lag_formula = sf.ema_response_analytic(a, f_hz)
    g_fit, lag_fit = sf.measured_response(lambda x: sf.ema(x, a), f_hz)
    assert g_fit == pytest.approx(g_formula, abs=1e-4)
    assert lag_fit == pytest.approx(lag_formula, abs=1e-4)


def test_shipped_filter_response_is_what_the_status_notes_say():
    a = sf.shipped_alpha()
    assert sf.ema_response_analytic(a, 1.0)[0] == pytest.approx(0.91, abs=0.005)
    assert sf.ema_response_analytic(a, 2.0)[0] == pytest.approx(0.74, abs=0.005)
    assert sf.ema_group_delay_ticks(a) * sf.TICK_S == pytest.approx(0.067, abs=0.002)
    # Out of the provisional bench gain limit at 0.8 Hz.
    assert sf.ema_response_analytic(a, 0.8)[0] < sf.BENCH_GAIN_LIMIT[0]


def test_replay_only_candidates_remove_lag_as_described():
    by_key = {c.key: c for c in sf.candidates()}
    g_ship, lag_ship = sf.measured_response(by_key["ema72"].apply, 1.0)
    g_adv, lag_adv = sf.measured_response(by_key["ema72_adv"].apply, 1.0)
    g_zp, lag_zp = sf.measured_response(by_key["ema72_zp"].apply, 1.0)
    assert lag_ship == pytest.approx(0.063, abs=0.003)
    assert abs(lag_adv) < 0.006 and g_adv == pytest.approx(g_ship, abs=2e-3)   # lag gone, loss kept
    assert abs(lag_zp) < 1e-3 and g_zp == pytest.approx(g_ship ** 2, abs=2e-3)  # loss squared
    for key in ("butter6_zp", "butter10_zp", "sg100", "none"):
        g, lag = sf.measured_response(by_key[key].apply, 1.0)
        assert g == pytest.approx(1.0, abs=2e-3) and abs(lag) < 1e-3
    assert [c.key for c in sf.candidates()][0] == "ema72"
    assert not by_key["ema72_adv"].live and by_key["ema20"].live


def test_response_table_names_where_each_candidate_leaves_the_gain_limit():
    table = sf.response_table()
    assert table["ema72"]["in_gain_limit_up_to_hz"] < 0.8
    assert table["ema20"]["in_gain_limit_up_to_hz"] == pytest.approx(2.0, abs=0.06)
    assert table["none"]["in_gain_limit_up_to_hz"] >= 6.0
    assert table["ema72"]["1"]["gain"] == pytest.approx(0.911, abs=1e-3)


def test_filters_keep_nan_gaps_and_short_runs():
    x = np.concatenate([[np.nan], np.linspace(170, 120, 300), [np.nan], [150.0, 151.0]])
    for c in sf.candidates():
        y = c.apply(x)
        assert np.array_equal(np.isnan(y), np.isnan(x)), c.key


def test_match_extrema():
    assert sf.match_extrema([1.0, 2.0, 3.0], [1.05, 2.9, 4.0]) == (2, 1, 1)
    assert sf.match_extrema([], [1.0]) == (0, 0, 1)
    assert sf.match_extrema([1.0], []) == (0, 1, 0)
    # One candidate extremum cannot stand for two reference extrema.
    assert sf.match_extrema([1.0, 1.1], [1.05]) == (1, 1, 0)


def test_count_extrema_respects_prominence():
    t = np.arange(0, 2, 0.01)
    y = 2.0 * np.sin(2 * np.pi * 2 * t) + 0.3 * np.sin(2 * np.pi * 20 * t)
    assert sf.count_extrema(y, 1.0) == 8            # the 2 Hz swing: 4 maxima, 4 minima
    assert sf.count_extrema(y, 0.2) > 8


def test_still_windows_take_flat_stretches_only():
    t = np.arange(0, 6, sf.TICK_S)
    y = np.where(t < 3, 150.0, 150.0 + 20.0 * (t - 3))
    y = y + np.random.default_rng(1).normal(0, 0.05, len(t))
    w = sf.still_windows(y)
    assert w and all(s.stop <= 300 for s in w)
    assert sf.still_windows(y, lo=350) == []


def test_jitter_stats_see_the_ema_reduce_white_noise():
    rng = np.random.default_rng(2)
    y = 150.0 + rng.normal(0, 0.1, 1000)
    windows = [slice(a, a + 100) for a in range(100, 900, 100)]
    raw = sf.jitter_stats(y, windows)
    smooth = sf.jitter_stats(sf.ema(y, sf.shipped_alpha()), windows)
    assert raw["still_s"] == 8.0
    assert raw["rms_deg"] == pytest.approx(0.1, abs=0.02)
    # White noise through an EMA: variance times alpha / (2 - alpha).
    a = sf.shipped_alpha()
    assert smooth["rms_deg"] < raw["rms_deg"] * math.sqrt(a / (2 - a)) * 1.3
    assert raw["extrema_ge_1"] == 0 and raw["extrema_ge_0.2"] > 0
    assert sf.jitter_stats(y, []) == {"still_s": 0.0}


def test_track_against_recovers_a_known_gain_and_lag():
    t = np.arange(0, 8, 0.01)
    ref = 40.0 * np.exp(-0.4 * t) * np.cos(2 * np.pi * 1.0 * t)
    y = 0.9 * np.interp(t - 0.060, t, ref) + 2.0
    res = sf.track_against(t, ref, t, y, (1.0, 6.0))
    assert res["gain"] == pytest.approx(0.9, abs=0.01)
    assert res["lag_s"] == pytest.approx(0.060, abs=0.002)
    assert res["bias"] == pytest.approx(2.0, abs=0.5)
    assert sf.track_against(t, ref, t[:10], y[:10], (1.0, 6.0)) is None


def _swing(freq_hz=0.9, zeta=0.08, depth=50.0, hold_s=1.5, dur=12.0):
    t = np.arange(0, hold_s + dur, sf.TICK_S)
    u = np.clip(t - hold_s, 0, None)
    wn = 2 * np.pi * freq_hz
    wd = wn * math.sqrt(1 - zeta ** 2)
    flex = depth * (1 - np.exp(-zeta * wn * u) * (np.cos(wd * u) + zeta * wn / wd * np.sin(wd * u)))
    return t, flex


def test_score_and_extrema_against():
    t, flex = _swing()
    ref = sf.score(t, flex, detrend=False)
    assert ref is not None and ref["interp"] and ref["N"] >= 3
    assert ref["A0"] == pytest.approx(50.0, abs=2.0)
    assert 0 < ref["rebound"] < 2 * 50.0
    same = sf.extrema_against(ref, ref)
    assert same["missed"] == 0 and same["invented"] == 0 and same["matched"] == same["ref"] > 4
    # The shipped EMA lowers the first rebound by about its gain at the swing frequency.
    smooth = sf.score(t, 180.0 - sf.ema(180.0 - flex, sf.shipped_alpha()), detrend=False)
    ratio = smooth["rebound"] / ref["rebound"]
    assert ratio == pytest.approx(sf.ema_response_analytic(sf.shipped_alpha(), 0.9)[0], abs=0.03)
    assert sf.extrema_against(None, ref) is None
    assert sf.score(t[:50], flex[:50]) is None


@pytest.fixture(scope="module")
def synthetic_replay():
    from neuromech import phone_trial as ph
    t_sw, th_sw = ph.pendulum(duration=8.0)
    trial = ph.synthesize(t_sw, th_sw, seed=3, tail_s=3.0)
    t, shipped, raw, capture = sf.replay_both(sf.trial_samples(trial))
    return trial, t_sw, th_sw, t, shipped, raw, capture


def test_own_ema_of_the_unsmoothed_replay_is_the_shipped_replay(synthetic_replay):
    _trial, _t_sw, _th_sw, t, shipped, raw, _cap = synthetic_replay
    assert len(t) > 500
    assert sf.ema_reproduces_shipped(raw, shipped) < 1e-9
    # And the unsmoothed replay really is unsmoothed: it differs from the shipped one.
    assert np.nanmax(np.abs(raw - shipped)) > 1.0
    assert sf.ema_reproduces_shipped(raw, raw) == float("inf") or \
        sf.ema_reproduces_shipped(raw, raw) > 1.0


def test_synthetic_run_reports_every_candidate():
    run = sf.synthetic_run(0.9, 0.08, 55.0, 1.0, seed=0)
    assert run["zeroed"] and run["ema_check_deg"] < 1e-9
    assert set(run["cands"]) == {c.key for c in sf.candidates()}
    ship, none = run["cands"]["ema72"], run["cands"]["none"]
    assert ship["still_s"] > 3
    assert ship["rms_deg"] < none["rms_deg"]                 # what the filter buys
    assert ship["trk_gain"] < none["trk_gain"] - 0.03        # what it costs
    assert none["trk_gain"] == pytest.approx(1.0, abs=0.03)
    assert ship["trk_lag_s"] == pytest.approx(0.065, abs=0.015)
    assert abs(none["trk_lag_s"]) < 0.015
    agg = sf._synthetic_agg([r for r in [ship]])
    assert agg["runs"] == 1 and agg["extrema_true"] > 0


def test_corpus_plumbing_on_a_synthetic_pair(synthetic_replay):
    """evaluate_corpus_trial, corpus_summary and render, with the planted
    motion standing in for the optical curve (dropped frames included)."""
    import imu_optitrack_validation as v
    trial, t_sw, th_sw, t, shipped, raw, capture = synthetic_replay
    rv = v.imu_rotvecs(capture["quats"], capture["q_zero"])
    axis, _ = v.principal_axis(rv)
    flex_fit = v.flexion_positive(np.degrees(rv @ axis))
    t_rel = trial.truth["release_t_s"]
    ot = np.arange(0.5, t[-1] - 0.2, 1 / 120.0) + 0.4          # another clock
    truth = -np.where(ot - 0.4 < t_rel, 0.0,
                      np.interp(ot - 0.4 - t_rel, t_sw, th_sw, right=float(th_sw[-1])))
    truth[300:306] = np.nan                                    # dropped frames
    lag = v.find_lag(ot, truth, t, flex_fit)
    assert lag["status"] == "ok" and lag["lag_sec"] == pytest.approx(0.4, abs=0.03)
    samples = sf.trial_samples(trial)
    opt = {"t": ot, "shank_flex": truth}
    variant = sf._clock_variant(samples, opt)
    assert np.array_equal(variant["raw"], raw, equal_nan=True)
    assert variant["lag"]["lag_sec"] == pytest.approx(lag["lag_sec"])
    # The synthetic phone stamps "t" on its own clock, so re-stamping only rounds it to 1 ms.
    again = sf._clock_variant(sf.restamp_on_phone_clock(samples), opt)
    assert np.nanmedian(np.abs(again["raw"] - raw)) < 0.2
    loaded = {"pid": "0", "status": "ok", "opt_t": ot, "opt_flex": truth,
              "opt_gap_share": 0.01, "opt_fps": 120.0, "bursts": sf.burst_stats(samples),
              "clocks": {"receive": variant, "phone": {"error": "ValueError: not replayed"}}}
    row = sf.evaluate_corpus_trial(loaded, "receive")
    row["i"] = 0
    assert row["good"] and row["opt"] is not None
    ship, none = row["cands"]["ema72"], row["cands"]["none"]
    assert none["trk_gain"] == pytest.approx(1.0, abs=0.04)
    assert ship["trk_gain"] < none["trk_gain"] - 0.03
    assert ship["trk_delay_sec"] > none["trk_delay_sec"] + 0.03
    assert ship["trk0_sd"] > none["trk0_sd"]                   # the lag left in costs more
    assert ship["stored"] is not None and ship["screen"] is not None and ship["win"] is not None
    assert 0 < ship["rough_deg"] < none["rough_deg"]
    failed = {"pid": "1", "status": "error", "error": "ValueError: no time alignment: x",
              "clocks": {}}
    summary = sf.corpus_summary([row], [loaded, failed])
    assert summary["n_paired_trials"] == 2 and summary["n_evaluated"] == 1
    assert summary["bursts"]["phone_interval_ms_median"] == pytest.approx(1000 / 60, abs=1.5)
    opt_block = summary["cands"]["none"]["vs_optical_all"]
    assert opt_block["extrema_ref"] > 4 and opt_block["extrema_missed"] <= 1
    # The other clock: nothing evaluated, the reason counted, and scores set
    # against the footing's rows when there are any.
    other = sf.corpus_summary([], [loaded, failed], "phone", footing_rows=[row])
    assert other["n_evaluated"] == 0 and sum(other["not_evaluated"].values()) == 2
    same = sf.corpus_summary([row], [loaded, failed], "receive", footing_rows=[row])
    assert same["cands"]["ema72"]["vs_footing_stored"]["pt_moves_over_0.05"] == 0
    assert same["cands"]["none"]["vs_footing_stored"] == same["cands"]["none"]["vs_shipped_stored"]
    assert sum(summary["not_evaluated"].values()) == 1
    assert summary["ema_check_max_deg"] < 1e-9
    shipped_moves = summary["cands"]["ema72"]["vs_shipped_stored"]
    assert shipped_moves["pt_moves_over_0.05"] == 0 and shipped_moves["verdict_changes"] == 0
    assert summary["cands"]["none"]["vs_none_extrema"]["trials_differing"] == 0
    check = summary["predicted_gain_check"]["ema72"]
    assert check["rebound_ratio_median"] == pytest.approx(check["predicted_median"], abs=0.04)
    text = sf.render({"response": sf.response_table(), "corpus": summary,
                      "corpus_phone_clock": same})
    assert "EMA 72 ms (shipped)" in text and "gap-filled optical" in text
    assert "re-stamped on the phone clock: PT score against the footing" in text
    assert "pid" not in text


def test_restamp_on_phone_clock_undoes_burst_arrival():
    """Samples taken 10 ms apart that arrive five at a time."""
    samples = []
    for i in range(20):
        arrive = 100.0 + 0.05 * (i // 5) + 0.0002 * (i % 5)
        for sensor in ("accel", "gyro"):
            samples.append({"t": arrive, "role": "distal", "sensor": sensor,
                            "v": [0.0, 0.0, float(i)], "phone_ts_ms": 5000 + 10 * i})
    stats = sf.burst_stats(samples)
    assert stats["burst_gap_s"] == pytest.approx(0.05, abs=0.002)
    assert stats["samples_per_burst"] == pytest.approx(20 / 3)
    assert stats["phone_interval_s"] == pytest.approx(0.010)
    out = sf.restamp_on_phone_clock(samples)
    assert samples[10]["t"] == pytest.approx(100.05)              # input untouched
    gyro_t = [s["t"] for s in out if s["sensor"] == "gyro"]
    assert gyro_t[0] == 100.0 and np.allclose(np.diff(gyro_t), 0.010)
    assert [s["sensor"] for s in out[:4]] == ["accel", "gyro", "accel", "gyro"]   # order kept
    assert sf.restamp_on_phone_clock(samples[:3] + [dict(samples[3], phone_ts_ms=0)]) is None
    assert sf.restamp_on_phone_clock([]) is None


def test_roughness_sees_a_staircase_and_not_a_smooth_swing():
    t = np.arange(0, 6, sf.TICK_S)
    smooth = 150.0 + 30.0 * np.cos(2 * np.pi * 1.0 * t)
    held = smooth[(np.arange(len(t)) // 10) * 10]                 # updated every 100 ms
    moving = sf.in_swing(smooth)
    assert 0.5 < moving.mean() < 1.0
    assert sf.roughness_deg(smooth, moving) < 0.05
    assert sf.roughness_deg(held, moving) > 2.0
    assert math.isnan(sf.roughness_deg(smooth, np.zeros(len(t), bool)))


def test_cache_dir_inside_the_repository_is_refused(monkeypatch):
    import batch_imu_vs_optitrack_rmse as batch
    monkeypatch.setattr(batch, "discover_trials",
                        lambda: [{"optitrack_path": "x", "participant": "p"}])
    with pytest.raises(SystemExit, match="outside the repository"):
        sf.run_corpus(cache_dir=os.path.join(sf.REPO_DIR, "Model_Analysis_Outputs", "c"))


def test_run_corpus_without_paired_trials_exits(monkeypatch):
    import batch_imu_vs_optitrack_rmse as batch
    monkeypatch.setattr(batch, "discover_trials", lambda: [])
    with pytest.raises(SystemExit, match="No paired trials"):
        sf.run_corpus()
