"""encoder_pendulum_validation: the estimators on signals with a known answer, the
encoder adapter, the refusals, and the whole pipeline on synthetic fixture trials with
planted clock offset, gain, encoder offset and noise.

Synthetic only: nothing here reads a recording. The tolerances are stated next to each
assertion; they are how well the script recovers what was planted, not acceptance limits.
"""
import csv
import json
import math
import os

import numpy as np
import pytest

import encoder_pendulum_synth as S
import encoder_pendulum_validation as V


# ───────────────────────── estimators on known signals ─────────────────────────
def test_release_onset_agrees_between_a_fast_and_a_slow_clock():
    """The same ramp seen at 1 kHz and at 60 Hz (then on the 200 Hz grid): the two onsets
    agree within 3 ms, and each is within 15 ms of the true corner."""
    f, t_rel = 0.82, 6.0

    def rate(t):
        return np.where(t > t_rel, 3.0 * np.abs(np.sin(2 * np.pi * f * (t - t_rel))) * np.exp(-0.15 * (t - t_rel)), 0.0)

    grid = np.arange(0, 20, 1 / V.ALIGN_FS_HZ)
    fast = V._lowpass(rate(grid), V.ALIGN_FS_HZ, V.RATE_LOWPASS_HZ)
    t60 = np.arange(0, 20, 1 / 60.0)
    slow = V._lowpass(np.interp(grid, t60, rate(t60)), V.ALIGN_FS_HZ, V.RATE_LOWPASS_HZ)
    a, pk, hold = V.release_onset(grid, fast)
    b, _, _ = V.release_onset(grid, slow)
    assert abs(a - b) < 0.003
    assert abs(a - t_rel) < 0.015 and abs(b - t_rel) < 0.015
    assert hold < 0.01 and 2.5 < pk < 3.0


def test_release_is_not_a_brisk_lift_or_an_end_tap():
    """A lift faster than the swing it precedes, then a hold, the swing, and a tap at the
    end: the release is the swing's onset. `near` picks a named candidate instead."""
    grid = np.arange(0, 30, 1 / V.ALIGN_FS_HZ)
    lift = 2.5 * np.sin(np.pi * np.clip((grid - 2.0) / 0.4, 0, 1)) ** 2            # peaks above the swing
    swing = np.where(grid > 8.0, 1.75 * np.abs(np.sin(2 * np.pi * 0.82 * (grid - 8.0))) * np.exp(-0.15 * (grid - 8.0)), 0.0)
    tap = 0.3 * np.exp(-0.5 * ((grid - 27.0) / 0.02) ** 2)
    r = V._lowpass(lift + swing + tap, V.ALIGN_FS_HZ, V.RATE_LOWPASS_HZ)
    t0, pk, _ = V.release_onset(grid, r)
    assert abs(t0 - 8.0) < 0.02 and 1.5 < pk < 1.8
    assert abs(V.release_onset(grid, r, near=2.1)[0] - 2.0) < 0.1
    with pytest.raises(V.ValidationError, match="within 1 s"):
        V.release_onset(grid, r, near=20.0)


def test_xcorr_offset_finds_a_shift_and_a_flipped_polarity():
    fs = 200.0
    t = np.arange(0, 20, 1 / fs)
    sig = lambda tt: np.where(tt > 5, np.sin(2 * np.pi * 0.8 * (tt - 5)) * np.exp(-0.2 * (tt - 5)), 0.0)  # noqa: E731
    off, sign, q = V.xcorr_offset(-sig(t - 0.4375), sig(t), fs)     # a is b, 437.5 ms late, upside down
    assert abs(off - 0.4375) < 0.001
    assert sign == -1 and q > 0.99


def test_best_lag_recovers_a_planted_lag():
    t = np.arange(0, 12, 0.01)
    e = 30 * np.sin(2 * np.pi * 0.85 * t) * np.exp(-0.2 * t)
    p = 30 * np.sin(2 * np.pi * 0.85 * (t - 0.063)) * np.exp(-0.2 * (t - 0.063))   # phone 63 ms late
    lag, rm = V.best_lag(t, p, t[50:-50], e[50:-50])
    assert abs(lag - 0.063) < 0.001 and rm < 0.05


def test_agreement_reads_gain_and_bias():
    e = np.linspace(0, 80, 500)
    a = V.agreement(1.05 * e + 2.0, e)
    assert abs(a["gain"] - 1.05) < 1e-9
    assert abs(a["bias"] - (0.05 * 40 + 2.0)) < 1e-9
    assert a["loa_lo"] < a["bias"] < a["loa_hi"] and a["r"] > 0.999999


def test_ringdown_recovers_frequency_and_damping():
    f, zeta, rest = 0.85, 0.03, 38.0
    wn = 2 * np.pi * f / math.sqrt(1 - zeta ** 2)
    tk = np.arange(18) / f + 0.4
    sk = rest + 37.0 * np.exp(-zeta * wn * tk)
    rd = V.ringdown(tk, sk, rest, floor=1.0)
    assert abs(rd["f"] / f - 1) < 1e-6
    assert abs(rd["zeta"] / zeta - 1) < 1e-3
    assert abs(rd["tau"] * zeta * wn - 1) < 1e-3
    assert rd["n_swings"] == 18


def test_whole_period_mean_is_not_fooled_by_a_swing_in_progress():
    t = np.arange(0, 10, 0.01)
    x = 5.0 + 0.6 * np.sin(2 * np.pi * 0.8 * t + 1.0)
    assert abs(V.whole_period_mean(t, x, 9.99, 1 / 0.8) - 5.0) < 0.01
    assert abs(np.median(x[-100:]) - 5.0) > 0.1          # what a 1 s tail median would have said


# ───────────────────────── encoder adapter ─────────────────────────
def test_labview_reader_reads_the_bench_layout(tmp_path):
    deg = np.linspace(0, 40, 2000)
    p = S.write_encoder_bin(str(tmp_path / "pf1126_001.bin"), deg, n_channels=7)
    tr = V.read_encoder(p, "labview_bin", sample_rate_hz=1000.0)
    assert isinstance(tr, V.EncoderTrace) and tr.fs_hz == 1000.0
    np.testing.assert_allclose(tr.angle_deg, deg)
    np.testing.assert_allclose(tr.t_s, np.arange(2000) / 1000.0)


def test_labview_reader_takes_counts_a_channel_count_a_sign_and_a_zero(tmp_path):
    counts = np.round(np.linspace(0, 40, 1500) * 8000 / 360.0)
    p = S.write_encoder_bin(str(tmp_path / "e.bin"), counts, n_channels=2)
    tr = V.read_encoder(p, "labview_bin", sample_rate_hz=500.0, n_channels=2, position_units="counts",
                        counts_per_rev=8000, sign=-1, zero_deg=1.0)
    np.testing.assert_allclose(tr.angle_deg, -counts * 360.0 / 8000 - 1.0)
    assert tr.fs_hz == 500.0 and abs(tr.t_s[1] - 0.002) < 1e-12
    with pytest.raises(V.ValidationError, match="counts_per_rev"):
        V.read_encoder(p, "labview_bin", n_channels=2, position_units="counts")


def test_unknown_formats_are_refused_not_guessed(tmp_path):
    p = S.write_encoder_bin(str(tmp_path / "e.bin"), np.zeros(500))
    with pytest.raises(V.ValidationError, match="unknown encoder format"):
        V.read_encoder(p, "csv")
    with pytest.raises(NotImplementedError, match="not known yet"):
        V.read_encoder(p, "daq_tbd")


def test_a_new_reader_can_be_registered(tmp_path, monkeypatch):
    monkeypatch.setitem(V.ENCODER_READERS, "two_col", lambda path, **o: V.EncoderTrace(
        t_s=np.arange(300) / 100.0, angle_deg=np.zeros(300), fs_hz=100.0, source=str(path)))
    assert V.read_encoder("nowhere", "two_col").fs_hz == 100.0


# ───────────────────────── synthetic trials ─────────────────────────
def _trial(tmp, name, radius=0.30, analyse_radius="same", **kw):
    enc = kw.pop("encoder", None) or S.EncoderErrors()
    tr = S.synthesize(name=name, radius_m=radius, encoder=enc, **kw)
    phone = S.write(tr, str(tmp), str(tmp / f"{name}.bin"))
    r = radius if analyse_radius == "same" else analyse_radius
    return tr, phone, str(tmp / f"{name}.bin"), V.analyse_trial(phone, str(tmp / f"{name}.bin"), radius_m=r)


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """40 deg release, nothing planted but the phone model's own bias, noise and jitter."""
    return _trial(tmp_path_factory.mktemp("base"), "base", release_deg=40.0, seed=3)


PLANTED = dict(clock_offset_s=0.437, gyro_scale=1.03, offset_deg=1.5, noise_deg=0.05)


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    """The same swing with a clock offset, a 3% gyro gain, an encoder zero error and noise."""
    enc = S.EncoderErrors(start_s=PLANTED["clock_offset_s"], offset_deg=PLANTED["offset_deg"],
                          noise_deg=PLANTED["noise_deg"])
    return _trial(tmp_path_factory.mktemp("planted"), "planted", release_deg=40.0, seed=3,
                  gyro_scale=PLANTED["gyro_scale"], encoder=enc)


def test_clock_offset_is_recovered_by_the_release_and_by_cross_correlation(base, planted):
    """Within 5 ms of the planted offset by each method, and of each other."""
    for (_, _, _, row), want in ((base, 0.0), (planted, PLANTED["clock_offset_s"])):
        assert abs(row["clock_offset_release_s"] - want) < 0.005
        assert abs(row["clock_offset_xcorr_s"] - want) < 0.005
        assert abs(row["clock_offset_diff_ms"]) < 5.0
        assert row["xcorr_quality"] > 0.99 and row["pass_sync"] is True
        assert abs(row["clock_rate_ppm"]) < 200.0          # no clock-rate error was planted


def test_gyro_gain_is_recovered(base, planted):
    """Signal B reads the planted gyro scale within 0.01; the app's first excursion (pure
    gyro, the accelerometer is gated off) moves by the same 3% within 0.7%."""
    assert abs(base[3]["gyro_gain"] - 1.00) < 0.01
    assert abs(planted[3]["gyro_gain"] - PLANTED["gyro_scale"]) < 0.01
    ratio = lambda r: r["first_excursion_phone_deg"] / r["first_excursion_encoder_deg"]  # noqa: E731
    assert abs(ratio(planted[3]) / ratio(base[3]) - PLANTED["gyro_scale"]) < 0.007
    # The app's whole-swing gain moves the same way but by less: once the arm is slow the
    # fusion corrects towards the accelerometer, which knows nothing of the gyro's scale.
    assert 1.010 < planted[3]["gain"] / base[3]["gain"] < 1.035


def test_encoder_scale_error_is_seen_as_gain(tmp_path):
    """An encoder reading 2% low (a counts-per-revolution mistake) is a 1/0.98 gain."""
    row = _trial(tmp_path, "scale", release_deg=40.0, seed=5, encoder=S.EncoderErrors(scale=0.98))[3]
    assert abs(row["gyro_gain"] - 1 / 0.98) < 0.01


def test_encoder_offset_and_noise_are_recovered(base, planted):
    """The zero error within 0.1 deg (it does not reach the comparison, which works on
    differences); the noise within 25% of what was planted plus quantisation."""
    assert abs(base[3]["encoder_rest_deg"]) < 0.1
    assert abs(planted[3]["encoder_rest_deg"] - PLANTED["offset_deg"]) < 0.1
    q = 360.0 / 8000 / math.sqrt(12)
    assert base[3]["encoder_hold_noise_deg"] < 0.02
    assert abs(planted[3]["encoder_hold_noise_deg"] / math.hypot(PLANTED["noise_deg"], q) - 1) < 0.25
    assert abs(planted[3]["rmse"] - base[3]["rmse"]) < 0.3
    assert abs(planted[3]["release_from_rest_deg"] - 40.0) < 0.15


def test_frequency_and_damping_match_the_planted_pendulum(base):
    """Encoder side against the model (zeta 0.03, L_eq 0.37 m): zeta within 5%, L_eq
    within 3% (the swing is not small-angle); phone against encoder: f within 0.6%."""
    tr, _, _, row = base
    assert abs(row["zeta_encoder"] / tr.truth["zeta"] - 1) < 0.05
    assert abs(row["l_eq_small_angle_m"] / tr.truth["l_eq_m"] - 1) < 0.03
    assert abs(row["f_error_pct"]) < 0.6
    assert abs(row["n_swings_error"]) <= 1
    assert row["n_peaks"] >= 10


def test_the_app_angle_lags_by_its_own_smoothing_and_nothing_else(base):
    """The reference footing's EMA: about 63 ms at 0.82 Hz. Against the encoder through
    the same EMA nothing is left (within 10 ms). Removing the lag is what makes the
    comparison a tracking comparison: RMSE under 2 deg, against about 3 at the clock."""
    row = base[3]
    assert row["footing"] == "ref-100hz-ema0.5-flexrec"
    assert 45.0 < row["lag_ms"] < 80.0
    assert abs(row["lag_ms"] - row["ema_expected_lag_ms"]) < 15.0
    assert abs(row["lag_matched_ms"]) < 10.0
    assert abs(row["gyro_lag_ms"]) < 5.0                   # the raw gyro is not late at all
    assert row["rmse"] < 2.0 < row["rmse_clock"]
    assert row["rmse_matched"] < row["rmse_clock"]
    # The same EMA lowers the gain on a swing: this is the footing, on a near-perfect sensor.
    assert 0.93 < row["gain"] < 0.98
    assert 0.97 < row["gain_matched"] < 1.01
    assert row["app_zero_exact"] and 0.0 < row["zero_after_release_ms"] < 150.0
    assert 0.3 < row["zero_pose_fall_deg"] < 3.0


def test_angle_does_not_depend_on_radius_but_the_accelerometer_does(tmp_path):
    """Same swing, phone at 0.10 m and at 0.40 m. The gyro angle is the same (gain within
    0.005) and so is the app's first excursion (within 0.3 deg). The accelerometer terms
    scale with the radius, and the across-arm reading changes sign past L_eq = 0.37 m."""
    near = _trial(tmp_path, "r10", radius=0.10, release_deg=40.0, seed=7)[3]
    far = _trial(tmp_path, "r40", radius=0.40, release_deg=40.0, seed=7)[3]
    assert abs(near["gyro_gain"] - far["gyro_gain"]) < 0.005
    assert abs(near["first_excursion_phone_deg"] - far["first_excursion_phone_deg"]) < 0.3
    assert abs(far["centripetal_peak_m_s2"] / near["centripetal_peak_m_s2"] - 4.0) < 0.1
    assert abs(far["accel_dev_from_g_max_m_s2"] / near["accel_dev_from_g_max_m_s2"] - 4.0) < 0.4
    assert near["tangential_accel_fraction"] > 0.7 and far["tangential_accel_fraction"] < 0.0
    assert near["phone_radius_m"] == 0.10 and near["phone_radius_measured"] is True


def test_an_unmeasured_radius_is_recorded_as_a_placeholder(base):
    _, phone, enc, _ = base
    row = V.analyse_trial(phone, enc, radius_m=None)
    assert row["phone_radius_m"] == V.PHONE_RADIUS_PLACEHOLDER_M
    assert row["phone_radius_measured"] is False and "radius_placeholder" in row["flags"]


def test_cross_correlation_alignment_gives_the_same_answer(base):
    _, phone, enc, row = base
    alt = V.analyse_trial(phone, enc, radius_m=0.30, align="xcorr")
    assert alt["align"] == "xcorr" and abs(alt["rmse"] - row["rmse"]) < 0.05
    assert abs(alt["lag_ms"] - row["lag_ms"]) < 3.0


def test_an_end_tap_stays_outside_the_comparison(base, tmp_path):
    """Raw mode ends with a knuckle tap on the still arm. The window closes 3 s after the
    arm settles, so the tap changes nothing: same release (1 ms), same RMSE (0.02 deg)."""
    row = _trial(tmp_path, "tap", release_deg=40.0, seed=3, end_tap=True)[3]
    ref = base[3]
    assert abs(row["release_encoder_s"] - ref["release_encoder_s"]) < 0.001
    assert abs(row["clock_offset_release_s"] - ref["clock_offset_release_s"]) < 0.001
    assert abs(row["rmse"] - ref["rmse"]) < 0.02 and abs(row["window_s"] - ref["window_s"]) < 0.05
    assert row["settled"] and row["encoder_duration_s"] - (row["release_encoder_s"] + row["settle_after_release_s"]) > 6.0


def test_the_repos_own_synthesiser_runs_through_too(tmp_path):
    """neuromech.phone_trial's damped pendulum (shank held level, lever 0.25 m) with the
    planted angle as the encoder: clock offset within 5 ms, gyro gain within 0.01."""
    from neuromech import phone_trial as PT

    t, th = PT.pendulum()
    tr = PT.synthesize(t, th, name="pt", lift=False, hold_s=3.0, seed=2)
    phone = PT.write(tr, str(tmp_path))
    sw = np.interp(np.arange(0, t[-1], 1e-3), t, th)
    full = np.concatenate([np.zeros(3000), sw, np.full(7000, sw[-1])])
    start = 0.25                                           # the encoder log starts 250 ms late
    S.write_encoder_bin(str(tmp_path / "pt.bin"), full[int(start * 1000):])
    row = V.analyse_trial(phone, str(tmp_path / "pt.bin"), radius_m=0.25)
    assert abs(row["clock_offset_release_s"] - start) < 0.005
    assert abs(row["clock_offset_xcorr_s"] - start) < 0.005
    assert abs(row["gyro_gain"] - 1.0) < 0.01
    assert row["rmse"] < 2.0


# ───────────────────────── the footing, and refusals ─────────────────────────
def test_the_replay_is_called_with_the_reference_config_on_the_analysis_grid(base, monkeypatch):
    import imu_calibration_tuner as tuner
    from imu_calibration_config import DEFAULT_CONFIG, REFERENCE_REPLAY_CONFIG

    seen = {}
    real = tuner.replay_trial

    def spy(samples, params, tick_s=tuner.TICK_S, capture=None):
        seen.update(params=dict(params), tick_s=tick_s)
        return real(samples, params, tick_s=tick_s, capture=capture)

    monkeypatch.setattr(tuner, "replay_trial", spy)
    V.app_angle(V.load_phone_export(base[1]))
    assert seen["params"] == dict(REFERENCE_REPLAY_CONFIG) != dict(DEFAULT_CONFIG)
    assert seen["params"]["ema_alpha"] == 0.5 and seen["params"]["flex_axis_fit"] == "recording"
    assert seen["tick_s"] == tuner.ANALYSIS_TICK_S == V.GRID_S


def test_a_moved_reference_config_is_refused(base, monkeypatch):
    monkeypatch.setattr(V, "REFERENCE_REPLAY_CONFIG", dict(V.REFERENCE_REPLAY_CONFIG, ema_alpha=0.3))
    with pytest.raises(V.ValidationError, match="REFERENCE_REPLAY_CONFIG"):
        V.app_angle(V.load_phone_export(base[1]))


def test_a_recording_with_no_release_has_no_app_angle(tmp_path):
    """A phone lying still: the replay takes no zero pose, and nothing is substituted."""
    p = tmp_path / "still.jsonl"
    with open(p, "w", encoding="utf-8") as fh:
        for i in range(600):
            t = 1.0 + i / 60.0
            for sensor, v in (("accel", [0.0, -9.81, 0.0]), ("gyro", [0.001, 0.0, 0.0])):
                fh.write(json.dumps({"t": t, "role": "distal", "sensor": sensor, "v": v,
                                     "phone_ts_ms": int(round(t * 1000))}) + "\n")
    with pytest.raises(V.ValidationError, match="no zero pose"):
        V.app_angle(V.load_phone_export(str(p)))


def _rewrite(src, dst, change):
    with open(src, encoding="utf-8") as fh, open(dst, "w", encoding="utf-8") as out:
        for line in fh:
            rec = json.loads(line)
            change(rec)
            out.write(json.dumps(rec) + "\n")
    return str(dst)


def test_wrong_units_in_the_export_are_refused(base, tmp_path):
    src = base[1]

    def deg_s(r):
        if r["sensor"] == "gyro":
            r["v"] = [math.degrees(x) for x in r["v"]]

    def in_g(r):
        if r["sensor"] == "accel":
            r["v"] = [x / 9.81 for x in r["v"]]

    with pytest.raises(V.ValidationError, match="deg/s"):
        V.load_phone_export(_rewrite(src, tmp_path / "a.jsonl", deg_s))
    with pytest.raises(V.ValidationError, match="seconds"):
        V.load_phone_export(_rewrite(src, tmp_path / "b.jsonl", lambda r: r.update(t=r["t"] * 1000.0)))
    with pytest.raises(V.ValidationError, match="gravity"):
        V.load_phone_export(_rewrite(src, tmp_path / "c.jsonl", in_g))
    with pytest.raises(V.ValidationError, match="phone_ts_ms"):
        V.load_phone_export(_rewrite(src, tmp_path / "d.jsonl", lambda r: r.pop("phone_ts_ms")))


def test_limits_are_named_and_marked_provisional():
    names = [n for n in dir(V) if n.startswith("PROVISIONAL_")]
    assert len(names) >= 12
    assert "PROPOSED" in V.LIMITS_STATUS and "pending supervisor confirmation" in V.LIMITS_STATUS
    assert V.PROVISIONAL_A1_RMSE_EACH_DEG == 5.0 and V.PROVISIONAL_A3_GAIN == (0.97, 1.03)


def test_judge_trial_applies_the_larger_of_three_degrees_and_five_percent():
    r = dict(rmse=1.0, gain=1.0, r=0.999, first_excursion_encoder_deg=112.0, first_excursion_error_deg=-5.0,
             f_error_pct=0.5, tau_error_pct=3.0, n_swings_error=0.0, clock_offset_diff_ms=1.0)
    assert V.judge_trial(r)["pass_A4_excursion"] is True       # 5% of 112 is 5.6 deg
    r["first_excursion_encoder_deg"] = 40.0
    assert V.judge_trial(r)["pass_A4_excursion"] is False      # 3 deg applies
    r["tau_error_pct"] = float("nan")
    assert V.judge_trial(r)["pass_all"] is None                # not assessable is not a pass


# ───────────────────────── a session ─────────────────────────
@pytest.fixture(scope="module")
def session(tmp_path_factory):
    folder = str(tmp_path_factory.mktemp("sess") / "2026-11-26_S1_iphone")
    truths = S.write_session(folder, conditions=("W0", "W2", "W4"), angles=(20, 40))
    rows, sess = V.analyse_session(folder)
    return folder, truths, rows, sess


def test_session_pairs_by_the_rig_log_and_recovers_every_clock_offset(session):
    folder, truths, rows, sess = session
    assert [r["trial_id"] for r in rows] == list(truths)
    assert not any("error" in r for r in rows)
    for r in rows:
        assert abs(r["clock_offset_release_s"] - truths[r["trial_id"]]["clock_offset_s"]) < 0.005
        assert abs(r["release_from_rest_deg"] - r["release_nominal_deg"]) < 0.2
        assert r["platform"] == "iphone" and r["phone_radius_measured"] is True
    assert {r["added_mass_kg"] for r in rows} == {0.0, 3.0, 6.0}


def test_session_report_matches_the_run_sheet_and_says_the_limits_are_provisional(session, tmp_path):
    folder, truths, rows, sess = session
    md = V.write_report(rows, [sess], str(tmp_path / "report"))
    assert V.LIMITS_STATUS in md and "SYNTHETIC DATA" in md
    assert "| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file | App result | Stopped by |" in md
    for tid in truths:
        assert f"`{tid}`" in md
    for crit in ("| A1 |", "| A2 |", "| A3 |", "| A4 |", "| A5 |", "| A7 |", "| A9* |"):
        assert crit in md
    assert "NOT EVALUATED" in md and "REFERENCE_REPLAY_CONFIG" in md
    assert "| W0 |" in md and "20 deg" in md and "40 deg" in md
    with open(tmp_path / "report" / "trials.csv", encoding="utf-8", newline="") as fh:
        table = list(csv.DictReader(fh))
    assert len(table) == len(rows) and table[0]["footing"] == V.REFERENCE_FOOTING
    assert table[0]["limits_status"] == V.LIMITS_STATUS


def test_pooled_criteria_use_one_number_per_swing(session):
    rows = session[2]
    P = V.pooled(rows)
    assert P["n"] == 6 and "bias_pooled" in P and "mass_slope" in P
    assert P["loa_lo_bound"] < P["loa_lo"] < P["bias_pooled"] < P["loa_hi"] < P["loa_hi_bound"]
    v = {x["criterion"]: x["verdict"] for x in V.verdicts(rows)}
    assert v["A1"] == "PASS" and v["A9*"] == "PASS"
    assert v["A6"].startswith("NOT EVALUATED") and v["A8"].startswith("NOT EVALUATED")
    assert V.verdicts(rows[:2])[1]["verdict"] == "NOT ASSESSABLE"      # A2 needs three swings


def _copy_session(src, dst):
    import shutil
    shutil.copytree(src, dst)
    return str(dst)


def test_a_count_mismatch_stops_the_session(session, tmp_path):
    folder = _copy_session(session[0], tmp_path / "s")
    log = os.path.join(folder, "rig_log.csv")
    lines = open(log, encoding="utf-8").read().splitlines()
    open(log, "w", encoding="utf-8").write("\n".join(lines[:-1]) + "\n")
    with pytest.raises(V.ValidationError, match="COUNT MISMATCH"):
        V.analyse_session(folder)
    rows, sess = V.analyse_session(folder, allow_count_mismatch=True)
    assert len(rows) == 5 and "COUNT MISMATCH" in sess["pairing_note"]


def test_error_rows_are_dropped_and_a_missing_encoder_file_is_reported_not_skipped(session, tmp_path):
    folder = _copy_session(session[0], tmp_path / "s")
    log = os.path.join(folder, "rig_log.csv")
    text = open(log, encoding="utf-8").read().rstrip("\n")
    open(log, "w", encoding="utf-8").write(
        text + "\n7,PF-S1-W4-A60-Q-07,7,W4,60,quicktest,ERROR: no release,auto,\n")
    os.remove(glob_one(folder, "encoder", "*_003.bin"))
    rows, sess = V.analyse_session(folder)
    assert len(rows) == 6 and len(sess["log"]) == 7 and len(sess["usable"]) == 6
    assert "no encoder file" in rows[2]["error"] and rows[2]["trial_id"].endswith("-03")
    md = V.summary_markdown(rows, [sess])
    assert "not analysed" in md and "1 marked ERROR" in md


def glob_one(folder, sub, pat):
    import glob
    (hit,) = glob.glob(os.path.join(folder, sub, pat))
    return hit


def test_session_json_must_say_what_the_encoder_is(session, tmp_path):
    folder = _copy_session(session[0], tmp_path / "s")
    sj = os.path.join(folder, "session.json")
    info = json.load(open(sj, encoding="utf-8"))
    del info["encoder"]["sample_rate_hz"]
    info["encoder"]["model"] = ""
    json.dump(info, open(sj, "w", encoding="utf-8"))
    with pytest.raises(V.ValidationError, match="encoder.sample_rate_hz"):
        V.analyse_session(folder)
    info["encoder"]["sample_rate_hz"] = 1000.0
    info["phone_radius_measured"] = False
    json.dump(info, open(sj, "w", encoding="utf-8"))
    rows, sess = V.analyse_session(folder)
    assert "encoder.model" in sess["info"]["_to_fill"]
    assert all("radius_placeholder" in r["flags"] for r in rows)
    assert "PLACEHOLDER, not measured" in V.summary_markdown(rows, [sess])
