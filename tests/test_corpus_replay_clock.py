"""corpus_replay_clock: the clock arithmetic, on synthetic samples only.

No participant recording is read here. The phone is simulated: it stamps a
sample every 10 ms on its own clock and the "desktop" receives them in bursts.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import corpus_replay_clock as crc
import imu_calibration_config as cfg
import imu_calibration_tuner as tuner
import imu_optitrack_validation as v

PHONE_EPOCH_MS = 1_700_000_000_000
CLOCK_OFFSET_S = 1.25        # desktop clock ahead of the phone's
TRANSIT_S = 0.004            # fastest packet


def _bursty_swing(burst=9, n=800, drift_ppm=0.0, mag=True):
    """A solo phone held still for 2 s then swung about x, 100 Hz on its own
    clock, received by the desktop `burst` samples at a time (gyro first, then
    accel, then mag inside a burst, as the server logs them)."""
    samples = []
    angle = 0.0
    per_sensor = {"gyro": [], "accel": [], "mag": []}
    for i in range(n):
        tp = i * 0.01
        rate = 0.0 if tp < 2.0 else 3.0 * math.sin(2 * math.pi * 1.0 * (tp - 2.0))
        angle += rate * 0.01
        ts = PHONE_EPOCH_MS + int(round(tp * 1000))
        # The burst leaves the phone when its last sample is taken.
        sent = (i // burst * burst + burst - 1) * 0.01
        recv = (PHONE_EPOCH_MS / 1000.0 + sent) * (1.0 + drift_ppm * 1e-6) + CLOCK_OFFSET_S + TRANSIT_S
        k = i % burst
        per_sensor["gyro"].append({"t": recv + k * 1e-5, "role": "proximal", "sensor": "gyro",
                                   "v": [rate, 0.0, 0.0], "phone_ts_ms": ts})
        per_sensor["accel"].append({"t": recv + 1e-3 + k * 1e-5, "role": "proximal", "sensor": "accel",
                                    "v": [0.0, -9.81 * math.sin(angle), 9.81 * math.cos(angle)],
                                    "phone_ts_ms": ts + 1})
        if mag:
            per_sensor["mag"].append({"t": recv + 2e-3 + k * 1e-5, "role": "proximal", "sensor": "mag",
                                      "v": [20.0, 0.0, -40.0], "phone_ts_ms": ts - 4})
    for kind in ("accel", "gyro", "mag"):
        samples.extend(per_sensor[kind])
    samples.sort(key=lambda s: s["t"])
    return samples


# ── clocks ────────────────────────────────────────────────────────────────────

def test_clock_offset_is_the_fastest_packet_and_latency_is_the_buffering():
    off = crc.clock_offset(_bursty_swing(burst=9))
    assert off["offset_s"] == pytest.approx(CLOCK_OFFSET_S + TRANSIT_S, abs=2e-3)
    # A sample waits between 0 and 8 sample intervals for its burst to leave.
    assert 0.03 < off["latency_median_s"] < 0.05
    assert off["latency_max_s"] == pytest.approx(0.08, abs=0.006)


def test_clock_offset_sees_a_rate_difference_between_the_clocks():
    off = crc.clock_offset(_bursty_swing(burst=9, drift_ppm=0.0))
    assert abs(off["drift_ppm"]) < 300
    # 2000 ppm of an epoch is far more than a trial lasts, so scale the test
    # to the span instead: the envelope must slope the right way.
    samples = _bursty_swing(burst=9)
    t_first = samples[0]["t"]
    skewed = [dict(s, t=s["t"] + 0.002 * (s["t"] - t_first)) for s in samples]
    assert crc.clock_offset(skewed)["drift_ppm"] == pytest.approx(2000, rel=0.25)


def test_clock_offset_is_none_without_phone_stamps():
    assert crc.clock_offset([{"t": 1.0, "sensor": "gyro", "phone_ts_ms": 0}]) is None


def test_restamp_receive_is_the_input_untouched():
    samples = _bursty_swing()
    assert crc.restamp(samples, "receive") is samples


def test_restamp_phone_puts_samples_on_the_phones_intervals_in_the_desktop_epoch():
    samples = _bursty_swing(burst=9)
    before = [dict(s) for s in samples]
    fed = crc.restamp(samples, "phone")
    assert samples == before, "the input must not be modified"
    assert len(fed) == len(samples)
    t = np.array([s["t"] for s in fed])
    assert np.all(np.diff(t) >= 0)
    gyro = np.array([s["t"] for s in fed if s["sensor"] == "gyro"])
    np.testing.assert_allclose(np.diff(gyro), 0.01, atol=1e-6)
    # Never later than it was received, and the fastest packet is unmoved.
    recv = {(s["sensor"], s["phone_ts_ms"]): s["t"] for s in samples}
    late = np.array([recv[(s["sensor"], s["phone_ts_ms"])] - s["t"] for s in fed])
    assert late.min() == pytest.approx(0.0, abs=1e-6) and late.max() < 0.09
    # The sensors interleave by their own stamps: mag (-4 ms), gyro, accel (+1 ms).
    assert [s["sensor"] for s in fed[:6]] == ["mag", "gyro", "accel", "mag", "gyro", "accel"]


def test_restamp_phone_refuses_a_recording_with_an_unstamped_sample():
    samples = _bursty_swing()
    samples[10] = dict(samples[10], phone_ts_ms=0)
    assert crc.restamp(samples, "phone") is None
    with pytest.raises(ValueError):
        crc.restamp(samples, "desktop")


# ── inventory ─────────────────────────────────────────────────────────────────

def test_stamp_stats_reads_the_bursts_on_one_clock_and_the_rate_on_the_other():
    st = crc.stamp_stats(_bursty_swing(burst=9), "gyro")
    assert st["n"] == 800 and st["n_no_phone_stamp"] == 0
    assert st["burst_gap_median_s"] == pytest.approx(0.09, abs=0.002)
    assert st["samples_per_burst"] == pytest.approx(9.0, abs=0.2)
    assert st["recv_share_under_2ms"] > 0.85
    assert st["phone_interval_median_s"] == pytest.approx(0.01)
    assert st["phone_rate_hz"] == pytest.approx(100.0, rel=1e-3)
    assert st["phone_duplicates"] == st["phone_backwards"] == st["phone_dropped"] == 0
    assert st["phone_breaks"] == 0


def test_stamp_stats_counts_duplicates_reversals_drops_and_breaks():
    gyro = [s for s in _bursty_swing(burst=9, mag=False) if s["sensor"] == "gyro"]
    gyro[100] = dict(gyro[100], phone_ts_ms=gyro[99]["phone_ts_ms"])          # duplicate
    gyro[200] = dict(gyro[200], phone_ts_ms=gyro[199]["phone_ts_ms"] - 30)    # backwards
    del gyro[300:303]                                                         # three lost
    for i in range(500, len(gyro)):                                           # the clock jumps
        gyro[i] = dict(gyro[i], phone_ts_ms=gyro[i]["phone_ts_ms"] + 5000)
    gyro[600] = dict(gyro[600], phone_ts_ms=0)                                # unstamped
    st = crc.stamp_stats(gyro, "gyro")
    assert st["n_no_phone_stamp"] == 1
    assert st["phone_duplicates"] == 1
    assert st["phone_backwards"] == 1
    assert st["phone_breaks"] == 1
    # the lost samples, the step back's recovery, and the hole the unstamped one left
    assert st["phone_dropped"] >= 2


def test_empty_tick_share_from_timestamps_alone():
    # One sample per 10 ms: no empty tick. Nine samples every 90 ms: 8 in 9.
    even = np.arange(0.0, 10.0, 0.01) + 0.003
    assert crc.empty_tick_share(even) == pytest.approx(0.0, abs=0.01)
    burst = np.repeat(np.arange(0.0, 10.0, 0.09), 9) + np.tile(np.arange(9) * 1e-5, 112)[:1008]
    assert crc.empty_tick_share(burst) == pytest.approx(8.0 / 9.0, abs=0.01)
    assert math.isnan(crc.empty_tick_share([1.0]))


def test_inventory_reports_roles_sensor_offsets_and_the_clock():
    inv = crc.inventory(_bursty_swing(burst=9))
    assert inv["roles"] == ["proximal"]
    assert set(inv["sensors"]) == {"accel", "gyro", "mag"}
    assert inv["accel_minus_gyro_stamp_median_ms"] == pytest.approx(1.0)
    assert inv["sensor_first_stamp_spread_s"] == pytest.approx(0.005, abs=1e-6)
    assert inv["clock"]["offset_s"] == pytest.approx(CLOCK_OFFSET_S + TRANSIT_S, abs=2e-3)


def test_accel_is_stale_on_the_receive_order_and_fresh_on_the_phone_order():
    samples = _bursty_swing(burst=9)
    recv = crc.accel_staleness(samples)
    phone = crc.accel_staleness(crc.restamp(samples, "phone"))
    # In a burst the gyro samples are processed before that burst's accel.
    assert recv["median_s"] > 0.03 and recv["max_s"] > 0.08
    assert phone["max_s"] <= 0.0091


# ── the staircase, through the real replay ────────────────────────────────────

def _replayed(samples):
    cap = {}
    t, _angle = tuner.replay_trial(samples, dict(cfg.REFERENCE_REPLAY_CONFIG),
                                   tick_s=tuner.ANALYSIS_TICK_S, capture=cap)
    assert len(t) > 0, "the synthetic trial must zero for this test to mean anything"
    rv = v.imu_rotvecs(cap["quats"], cap["q_zero"])
    axis, _share = v.principal_axis(rv)
    return t, v.flexion_positive(np.degrees(rv @ axis)), cap


def test_bursts_make_a_staircase_on_the_receive_clock_and_none_on_the_phone_clock():
    samples = _bursty_swing(burst=9)
    _t, flex_r, cap_r = _replayed(samples)
    _t, flex_p, cap_p = _replayed(crc.restamp(samples, "phone"))
    recv = crc.staircase(cap_r["quats"], flex_r)
    phone = crc.staircase(cap_p["quats"], flex_p)
    assert recv["repeat_share"] == pytest.approx(8.0 / 9.0, abs=0.03)
    assert phone["repeat_share"] < 0.02
    assert recv["rough_deg"] > 5 * phone["rough_deg"]
    assert recv["rough_deg"] > 1.0
    # The swing itself is the same size on both: only its timing was staircased.
    assert np.nanmax(flex_r) == pytest.approx(np.nanmax(flex_p), abs=1.0)


def test_in_swing_is_false_during_the_hold():
    samples = _bursty_swing(burst=1)
    t, flex, _cap = _replayed(samples)
    moving = crc.in_swing(flex)
    assert not moving[(t > 0.3) & (t < 1.7)].any()
    assert moving[t > 2.1].mean() > 0.5


# ── the variant switch ────────────────────────────────────────────────────────

def test_replay_variant_restores_everything_it_substituted():
    import workbench_engine as engine
    before = (engine.bind_split_csv_components, cfg.REFERENCE_REPLAY_CONFIG,
              tuner._is_stationary_window, v.imu_curves)
    reference = dict(cfg.REFERENCE_REPLAY_CONFIG)
    with pytest.raises(RuntimeError):
        with crc.replay_variant("phone", "opening", {}):
            assert cfg.REFERENCE_REPLAY_CONFIG["flex_axis_fit"] == "opening"
            assert {k: val for k, val in cfg.REFERENCE_REPLAY_CONFIG.items() if k != "flex_axis_fit"} \
                == {k: val for k, val in reference.items() if k != "flex_axis_fit"}
            raise RuntimeError("boom")
    assert (engine.bind_split_csv_components, cfg.REFERENCE_REPLAY_CONFIG,
            tuner._is_stationary_window, v.imu_curves) == before
    assert cfg.REFERENCE_REPLAY_CONFIG == reference


def test_replay_variant_recording_is_the_reference_config_itself():
    reference = dict(cfg.REFERENCE_REPLAY_CONFIG)
    with crc.replay_variant("receive", "recording", {}):
        assert cfg.REFERENCE_REPLAY_CONFIG == reference


def test_no_bias_variant_answers_not_still_but_keeps_the_reference_config():
    still_gyro = [(i * 0.01, np.zeros(3)) for i in range(120)]
    still_accel = [(i * 0.01, np.array([0.0, 0.0, 9.81])) for i in range(120)]
    reference = dict(cfg.REFERENCE_REPLAY_CONFIG)
    for fit, expected in (("recording", True), (crc.NO_BIAS, False)):
        probe = {}
        with crc.replay_variant("receive", fit, probe):
            assert cfg.REFERENCE_REPLAY_CONFIG == reference
            assert tuner._is_stationary_window(still_gyro, still_accel, 1.19) is expected
        # What the window really was is still recorded.
        assert probe["still"][0][1] is True and probe["still"][0][2] == pytest.approx(0.0)


def test_replay_variant_feeds_the_binder_restamped_samples_and_finds_the_zero():
    import workbench_engine as engine
    samples = _bursty_swing(burst=9)
    validations = {k: {"ok": True, "rows": [s for s in samples if s["sensor"] == k]}
                   for k in ("accel", "gyro", "mag")}
    validations["imu"] = {"ok": True, "rows": []}
    zeros = {}
    for clock in crc.CLOCKS:
        probe = {}
        with crc.replay_variant(clock, "recording", probe):
            fed = engine.bind_split_csv_components(validations)["fusion_samples"]
            t, _a = tuner.replay_trial(fed, dict(cfg.REFERENCE_REPLAY_CONFIG),
                                       tick_s=tuner.ANALYSIS_TICK_S)
        assert len(t) > 0
        assert probe["samples"] is fed
        zeros[clock] = crc.zero_summary(probe)
    gyro_t = np.diff([s["t"] for s in fed if s["sensor"] == "gyro"])
    np.testing.assert_allclose(gyro_t, 0.01, atol=1e-6)        # the last pass was the phone clock
    for z in zeros.values():
        # The swing starts 2.0 s into the phone's stamps and the detector
        # needs the rate to reach 1 rad/s.
        assert 2000 <= z["zero_phone_ms"] - PHONE_EPOCH_MS <= 2120
    assert abs(zeros["phone"]["zero_phone_ms"] - zeros["receive"]["zero_phone_ms"]) <= 20


# ── aggregates ────────────────────────────────────────────────────────────────

def _row(pt, n, zone="normal", opt_pt=0.5, opt_n=5, zero_ms=1000, q=(1.0, 0.0, 0.0, 0.0), fires=1):
    row = {"imu_status": "ok", "imu_scored": True, "imu_interpretable": True,
           "imu_pt": pt, "imu_N": n, "imu_zone": zone,
           "screen_scored": True, "screen_interpretable": True,
           "screen_pt": pt, "screen_N": n, "screen_zone": zone,
           "track_shank_lag_sec": 0.0, "track_fit_sd": 2.0,
           "zero": {"zero_phone_ms": zero_ms, "q_zero": list(q), "bias_fires": fires}}
    for ref, test in (("shank", "imu"), ("gfopt", "gfimu")):
        row.update({f"{ref}_pt": opt_pt, f"{ref}_N": opt_n, f"{ref}_scored": True,
                    f"{ref}_interpretable": True, f"{ref}_zone": "normal",
                    f"{test}_pt": pt, f"{test}_N": n, f"{test}_scored": True,
                    f"{test}_interpretable": True, f"{test}_zone": zone})
    return row


def _trial(recv, phone):
    return {"paired": True, "variants": {"receive/recording": recv, "phone/recording": phone}}


def test_compare_clocks_counts_moves_and_their_direction_against_optics():
    half = math.sqrt(0.5)
    trials = [
        _trial(_row(0.50, 5), _row(0.50, 5)),                           # nothing moves
        _trial(_row(0.30, 4), _row(0.42, 5, zero_ms=1010)),             # +0.12, towards optics
        _trial(_row(0.50, 5), _row(0.90, 6, zone="mild", zero_ms=1500,  # +0.40, away
                                   q=(half, half, 0.0, 0.0), fires=0)),
        _trial(_row(0.56, 5), _row(0.50, 5)),                           # -0.06, towards
    ]
    res = crc.compare_clocks(trials, "recording")
    assert res["n_trials"] == 4
    s = res["stored"]
    assert (s["pt_moved_over_0.05"], s["pt_moved_over_0.1"], s["pt_moved_over_0.3"]) == (3, 2, 1)
    assert s["verdict_changed"] == 1
    assert (s["N_changed"], s["N_up"], s["N_down"]) == (2, 2, 0)
    for key in ("towards_optics_gap_filled", "towards_optics_plain"):
        t = res[key]
        assert (t["pt_closer"], t["pt_further"], t["pt_same"]) == (2, 1, 1)
        assert t["pt_moved_over_0.1_closer_further"] == [1, 1]
        assert (t["N_closer"], t["N_further"], t["N_same"]) == (1, 1, 2)
    z = res["zero"]
    assert z["zero_same_sample"] == 2 and z["zero_within_20ms"] == 3 and z["zero_over_100ms"] == 1
    assert z["zero_pose_diff_over_5deg"] == 1
    assert z["zero_pose_diff_deg"]["max"] == pytest.approx(90.0, abs=1e-6)
    assert z["trials_with_different_bias_fire_count"] == 1
    assert res["zeroed"] == {"both": 4, "receive_only": 0, "phone_only": 0, "neither": 0}


def test_compare_clocks_counts_a_trial_that_zeroes_on_one_clock_only():
    lost = {"imu_status": "error", "zero": {"zero_phone_ms": None, "bias_fires": 0}}
    res = crc.compare_clocks([_trial(_row(0.5, 5), lost), _trial(_row(0.5, 5), _row(0.5, 5))])
    assert res["zeroed"] == {"both": 1, "receive_only": 1, "phone_only": 0, "neither": 0}
    assert res["stored"]["n_pt_both"] == 1
    assert res["zero"]["n_zero_time_both"] == 1


def test_alignment_moved_flags_a_lag_that_left_the_usual_shift():
    def pair(recv_lag, phone_lag):
        return ({"track_shank_lag_sec": recv_lag}, {"track_shank_lag_sec": phone_lag})
    pairs = [pair(-0.70, -0.74), pair(-0.60, -0.63), pair(-0.80, -0.85),
             pair(-0.68, -1.87),                       # a swing period away
             pair(-0.70, None)]                        # no alignment on one clock
    assert list(crc.alignment_moved(pairs)) == [False, False, False, True, False]
    assert crc.lag_shift(pairs)[0] == pytest.approx(-0.04)


def test_agreement_is_reported_on_the_same_trials_for_both_clocks():
    trials = [_trial(_row(0.50, 5), _row(0.52, 5)),
              _trial(_row(0.20, 4, opt_pt=0.2), _row(0.21, 5, opt_pt=0.2)),
              _trial(_row(0.90, 5, opt_pt=0.8), _row(0.80, 5, opt_pt=0.8)),
              _trial(_row(0.10, 5, opt_pt=0.1), _row(0.10, 5, zone="mild", opt_pt=0.1))]
    lost = _row(0.4, 5)
    lost["imu_interpretable"] = lost["gfimu_interpretable"] = False
    trials.append(_trial(_row(0.4, 5), lost))          # interpretable on one clock only
    res = crc.compare_clocks(trials, "recording")["agreement_same_trials"]["plain"]
    assert res["n"] == 4
    assert res["receive"]["zone_agree"] == 4 and res["phone"]["zone_agree"] == 3
    assert res["receive"]["N_equal"] == 3 and res["phone"]["N_equal"] == 4
    assert res["phone"]["pt_abs_diff_max"] == pytest.approx(0.02)
    assert res["receive"]["pt_abs_diff_max"] == pytest.approx(0.1)


def test_paired_change_counts_steps_each_way():
    pairs = [({"x": 1.0}, {"x": 2.0}), ({"x": 1.0}, {"x": 0.2}), ({"x": 1.0}, {"x": 1.1}),
             ({"x": 1.0}, {})]
    res = crc._paired_change(pairs, "x", 0.5)
    assert (res["n"], res["lower_by_step"], res["higher_by_step"]) == (3, 1, 1)
    assert crc._paired_change([({}, {})], "x", 0.5) is None


def test_inventory_summary_numbers_sessions_without_dates():
    def trial(pid, day, burst):
        samples = _bursty_swing(burst=burst, n=300)
        return {"pid": pid, "paired": False, "variants": {}, "day": day, "month": day[:7],
                "inventory": crc.inventory(samples),
                "stale_accel": {"receive": crc.accel_staleness(samples),
                                "phone": crc.accel_staleness(crc.restamp(samples, "phone"))},
                "empty_tick_share": {"receive": 0.8, "phone": 0.0}}
    trials = [trial("7", "2031-03-02", 9), trial("7", "2031-03-02", 9), trial("7", "2031-01-15", 5),
              trial("12", "2031-02-01", 5),
              {"pid": "12", "paired": True, "variants": {}, "error": "ValueError",
               "component_errors": {"gyro": "<file> row N has N columns", "imu": "ignored"}}]
    inv = crc.summarise_inventory(trials)
    assert (inv["n_trials"], inv["n_loaded"], inv["n_load_errors"]) == (5, 4, 1)
    assert inv["load_error_reasons"] == {"gyro: <file> row N has N columns": 1}
    # one participant's two days in date order, then the next participant
    assert list(inv["by_session"]) == ["session 1", "session 2", "session 3"]
    assert inv["by_session"]["session 2"]["trials"] == 2
    assert inv["by_session"]["session 1"]["samples_per_burst"] == pytest.approx(5.0, abs=0.2)
    assert inv["n_participants"] == 2 and "by_participant" not in inv
    assert inv["sessions_by_burst_gap"] == {"all_trials_under_70ms": 2,
                                            "all_trials_70ms_or_more": 1, "mixed": 0}
    assert inv["trials_by_burst_gap"] == {"under_70ms": 2, "70ms_or_more": 2}
    assert inv["trials_with_usable_phone_clock"] == 4
    assert "2031" not in str({k: v for k, v in inv.items() if k != "by_month"})


def test_loader_errors_are_stripped_of_paths_and_numbers():
    msg = crc._reason(r"'C:\data\Somebody_7\Trial_3_gyro.csv' row 412 has 6 columns; expected 7.")
    assert msg == "<file> row N has N columns; expected N."


def test_quat_angle_is_sign_blind():
    assert crc.quat_angle_deg([1, 0, 0, 0], [-1, 0, 0, 0]) == pytest.approx(0.0)
    assert crc.quat_angle_deg([1, 0, 0, 0], [0, 1, 0, 0]) == pytest.approx(180.0)


# ── the cache ─────────────────────────────────────────────────────────────────

def test_the_cache_may_not_live_inside_the_repository(tmp_path):
    with pytest.raises(SystemExit):
        crc.check_cache_dir(os.path.join(crc.REPO_DIR, "Model_Analysis_Outputs", "clock_cache"))
    assert crc.check_cache_dir(None) is None
    out = crc.check_cache_dir(str(tmp_path / "cache"))
    assert os.path.isdir(out)


def test_cache_names_carry_no_path_component(tmp_path):
    name = os.path.basename(crc._cache_path(str(tmp_path), {"imu": "/some/where/Trial_3_imu.csv"}))
    assert name.startswith("clock_") and "Trial" not in name and "where" not in name
