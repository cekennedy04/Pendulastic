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
    lag, rm, pegged = V.best_lag(t, p, t[50:-50], e[50:-50])
    assert abs(lag - 0.063) < 0.001 and rm < 0.05 and not pegged


def test_a_lag_on_the_edge_of_the_search_is_pegged_not_a_lag():
    t = np.arange(0, 12, 0.01)
    e = 30 * np.sin(2 * np.pi * 0.85 * t) * np.exp(-0.2 * t)
    p = 30 * np.sin(2 * np.pi * 0.85 * (t - 0.40)) * np.exp(-0.2 * (t - 0.40))    # 400 ms: outside the 300 ms search
    lag, _, pegged = V.best_lag(t, p, t[50:-50], e[50:-50])
    assert pegged and abs(abs(lag) - V.LAG_SEARCH_S) < 1e-9


def test_the_footings_ema_is_read_as_the_discrete_filter_it_is():
    """At 0.82 Hz on the 10 ms grid: about 64 ms (the continuous formula says 69) and a 6% loss;
    checked against the filter itself run on a sinusoid."""
    gain, lag = V.ema_response(0.82)
    assert 0.062 < lag < 0.066 and 0.93 < gain < 0.95
    t = np.arange(0, 40, V.GRID_S)
    y = V.ema_like_app(np.sin(2 * np.pi * 0.82 * t))[2000:]
    assert abs(np.max(y) - gain) < 0.002
    lag_fit, _, _ = V.best_lag(t[2000:], y / gain, t[2100:-100], np.sin(2 * np.pi * 0.82 * t[2100:-100]))
    assert abs(lag_fit - lag) < 0.002


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
    tr = V.read_encoder(p, "labview_bin", sample_rate_hz=1000.0, n_channels=7)
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
        V.read_encoder(p, "labview_bin", sample_rate_hz=500.0, n_channels=2, position_units="counts")


def test_the_bin_reader_infers_nothing_and_takes_no_misspelt_option(tmp_path):
    """The file carries no sample rate and no channel count: both must be stated."""
    p = S.write_encoder_bin(str(tmp_path / "e.bin"), np.zeros(700))
    with pytest.raises(V.ValidationError, match="sample_rate_hz and n_channels"):
        V.read_encoder(p, "labview_bin", n_channels=7)
    with pytest.raises(V.ValidationError, match="sample_rate_hz and n_channels"):
        V.read_encoder(p, "labview_bin", sample_rate_hz=1000.0)
    with pytest.raises(V.ValidationError, match="not what the"):
        V.read_encoder(p, "labview_bin", sample_rate_hz=1000.0, n_channels=7, sample_rate=1000.0)


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
ENC = dict(sample_rate_hz=1000.0, n_channels=7)


def _trial(tmp, name, radius=0.30, analyse_radius="same", **kw):
    enc = kw.pop("encoder", None) or S.EncoderErrors()
    tr = S.synthesize(name=name, radius_m=radius, encoder=enc, **kw)
    phone = S.write(tr, str(tmp), str(tmp / f"{name}.bin"))
    r = radius if analyse_radius == "same" else analyse_radius
    return tr, phone, str(tmp / f"{name}.bin"), V.analyse_trial(phone, str(tmp / f"{name}.bin"), radius_m=r,
                                                                encoder_opts=ENC)


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
        # no clock-rate error was planted; one swing resolves it only to its standard error
        assert abs(row["clock_rate_ppm"]) < 3 * row["clock_rate_ppm_se"] + 20.0
        assert 10.0 < row["clock_rate_ppm_se"] < 120.0


def test_gyro_gain_is_recovered(base, planted):
    """Signal B reads the planted gyro scale within 0.01; the app's first excursion (pure
    gyro, the accelerometer is gated off) moves by the same 3% within 0.7%."""
    assert abs(base[3]["gain_gyro"] - 1.00) < 0.01
    assert abs(planted[3]["gain_gyro"] - PLANTED["gyro_scale"]) < 0.01
    ratio = lambda r: r["first_excursion_phone_deg"] / r["first_excursion_encoder_deg"]  # noqa: E731
    assert abs(ratio(planted[3]) / ratio(base[3]) - PLANTED["gyro_scale"]) < 0.007
    # The app's whole-swing gain moves the same way but by less: once the arm is slow the
    # fusion corrects towards the accelerometer, which knows nothing of the gyro's scale.
    assert 1.010 < planted[3]["gain_matched"] / base[3]["gain_matched"] < 1.035


def test_encoder_scale_error_is_seen_as_gain(tmp_path):
    """An encoder reading 2% low (a counts-per-revolution mistake) is a 1/0.98 gain."""
    row = _trial(tmp_path, "scale", release_deg=40.0, seed=5, encoder=S.EncoderErrors(scale=0.98))[3]
    assert abs(row["gain_gyro"] - 1 / 0.98) < 0.01


def test_encoder_offset_and_noise_are_recovered(base, planted):
    """The zero error within 0.1 deg (it does not reach the comparison, which works on
    differences); the noise within 25% of what was planted plus quantisation."""
    assert abs(base[3]["encoder_rest_deg"]) < 0.1
    assert abs(planted[3]["encoder_rest_deg"] - PLANTED["offset_deg"]) < 0.1
    q = 360.0 / 8000 / math.sqrt(12)
    assert base[3]["encoder_hold_noise_deg"] < 0.02
    assert abs(planted[3]["encoder_hold_noise_deg"] / math.hypot(PLANTED["noise_deg"], q) - 1) < 0.25
    assert abs(planted[3]["rmse_lagrm"] - base[3]["rmse_lagrm"]) < 0.3
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
    assert abs(row["lag_gyro_ms"]) < 5.0                   # the raw gyro is not late at all
    assert row["rmse_lagrm"] < 2.0 < row["rmse_clock"]
    assert abs(row["lag_minus_expected_ms"]) <= V.PROVISIONAL_LAG_VS_FOOTING_MS and row["pass_lag"] is True
    assert row["rmse_matched"] < row["rmse_clock"]
    # The same EMA lowers the gain on a swing: this is the footing, on a near-perfect sensor.
    assert 0.92 < row["gain_lagrm"] < 0.96 and row["gain_clock"] < row["gain_lagrm"]
    assert 0.97 < row["gain_matched"] < 1.01
    assert row["app_zero_exact"] and 0.0 < row["zero_after_release_ms"] < 150.0
    assert 0.3 < row["zero_pose_fall_deg"] < 3.0


def test_angle_does_not_depend_on_radius_but_the_accelerometer_does(tmp_path):
    """Same swing, phone at 0.10 m and at 0.40 m. The gyro angle is the same (gain within
    0.005) and so is the app's first excursion (within 0.3 deg). The accelerometer terms
    scale with the radius, and the across-arm reading changes sign past L_eq = 0.37 m."""
    near = _trial(tmp_path, "r10", radius=0.10, release_deg=40.0, seed=7)[3]
    far = _trial(tmp_path, "r40", radius=0.40, release_deg=40.0, seed=7)[3]
    assert abs(near["gain_gyro"] - far["gain_gyro"]) < 0.005
    assert abs(near["first_excursion_phone_deg"] - far["first_excursion_phone_deg"]) < 0.3
    assert abs(far["centripetal_peak_m_s2"] / near["centripetal_peak_m_s2"] - 4.0) < 0.1
    assert abs(far["accel_dev_from_g_max_m_s2"] / near["accel_dev_from_g_max_m_s2"] - 4.0) < 0.4
    assert near["tangential_accel_fraction"] > 0.7 and far["tangential_accel_fraction"] < 0.0
    assert near["phone_radius_m"] == 0.10 and near["phone_radius_measured"] is True


def test_an_unmeasured_radius_is_recorded_as_a_placeholder(base):
    _, phone, enc, _ = base
    row = V.analyse_trial(phone, enc, radius_m=None, encoder_opts=ENC)
    assert row["phone_radius_m"] == V.PHONE_RADIUS_PLACEHOLDER_M
    assert row["phone_radius_measured"] is False and "radius_placeholder" in row["flags"]


def test_cross_correlation_alignment_gives_the_same_answer(base):
    _, phone, enc, row = base
    alt = V.analyse_trial(phone, enc, radius_m=0.30, align="xcorr", encoder_opts=ENC)
    assert alt["align"] == "xcorr" and abs(alt["rmse_lagrm"] - row["rmse_lagrm"]) < 0.05
    assert abs(alt["lag_ms"] - row["lag_ms"]) < 3.0


def test_an_end_tap_stays_outside_the_comparison(base, tmp_path):
    """Raw mode ends with a knuckle tap on the still arm. The window closes 3 s after the
    arm settles, so the tap changes nothing: same release (1 ms), same RMSE (0.02 deg)."""
    row = _trial(tmp_path, "tap", release_deg=40.0, seed=3, end_tap=True)[3]
    ref = base[3]
    assert abs(row["release_encoder_s"] - ref["release_encoder_s"]) < 0.001
    assert abs(row["clock_offset_release_s"] - ref["clock_offset_release_s"]) < 0.001
    assert abs(row["rmse_lagrm"] - ref["rmse_lagrm"]) < 0.02 and abs(row["window_s"] - ref["window_s"]) < 0.05
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
    row = V.analyse_trial(phone, str(tmp_path / "pt.bin"), radius_m=0.25, encoder_opts=ENC)
    assert abs(row["clock_offset_release_s"] - start) < 0.005
    assert abs(row["clock_offset_xcorr_s"] - start) < 0.005
    assert abs(row["gain_gyro"] - 1.0) < 0.01
    assert row["rmse_lagrm"] < 2.0


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
    assert len(names) >= 18
    listed = {k for k, _ in V.provisional_constants()}
    assert set(names) <= listed and listed == set(names) | set(V.GATING_THRESHOLD_NAMES)
    # which comparison a criterion is judged on is a named, provisional choice too
    assert V.PROVISIONAL_A3_GAIN_VARIANT == "matched" and V.PROVISIONAL_A1_A2_VARIANT == "lagrm"
    assert "PROPOSED" in V.LIMITS_STATUS and "pending supervisor confirmation" in V.LIMITS_STATUS
    assert V.PROVISIONAL_A1_RMSE_EACH_DEG == 5.0 and V.PROVISIONAL_A3_GAIN == (0.97, 1.03)


def test_judge_trial_applies_the_larger_of_three_degrees_and_five_percent():
    r = dict(rmse_lagrm=1.0, gain_matched=1.0, r_lagrm=0.999, first_excursion_encoder_deg=112.0, first_excursion_error_deg=-5.0,
             f_error_pct=0.5, tau_error_pct=3.0, n_swings_error=0.0, clock_offset_diff_ms=1.0)
    assert V.judge_trial(r)["pass_A4_excursion"] is True       # 5% of 112 is 5.6 deg
    r["first_excursion_encoder_deg"] = 40.0
    assert V.judge_trial(r)["pass_A4_excursion"] is False      # 3 deg applies
    r["tau_error_pct"] = float("nan")
    assert V.judge_trial(r)["pass_all"] is False               # one unassessable criterion does not hide the A4 fail
    r["first_excursion_encoder_deg"] = 112.0
    assert V.judge_trial(r)["pass_all"] is None                # not assessable is not a pass


# ───────────────────────── what a verdict rests on ─────────────────────────
def _altered_app(monkeypatch, change):
    """Run the real replay, then alter what it returned (a faulty app, planted exactly)."""
    real = V.app_angle

    def fake(samples):
        a = real(samples)
        change(a)
        return a

    monkeypatch.setattr(V, "app_angle", fake)


def _again(fx, **kw):
    return V.analyse_trial(fx[1], fx[2], radius_m=0.30, encoder_opts=ENC, **kw)


def test_an_app_reading_five_percent_high_fails_A3_and_an_unscaled_one_passes(base, monkeypatch):
    """A3 is judged on the gain against the EMA-matched encoder (a provisional choice,
    printed with the verdict). The app's output x1.00 passes; x1.05 fails. Against the
    unsmoothed encoder it is the other way round, which is why all three gains are reported."""
    good = base[3]
    assert good["pass_A3_gain"] is True and abs(good["gain_matched"] - 1.0) < 0.015
    assert "EMA-matched" in good["judged_on"]
    _altered_app(monkeypatch, lambda a: a.update(swing_deg=1.05 * a["swing_deg"]))
    high = _again(base)
    assert abs(high["gain_matched"] / good["gain_matched"] - 1.05) < 0.002
    assert high["pass_A3_gain"] is False and high["pass_all"] is False
    lo, hi = V.PROVISIONAL_A3_GAIN
    assert good["gain_lagrm"] < lo < high["gain_lagrm"] < hi
    assert abs(high["gain_gyro"] - good["gain_gyro"]) < 1e-9          # the sensor itself did not change


def test_a_constant_error_on_the_app_reaches_the_bias_of_every_comparison(base, monkeypatch):
    good = base[3]
    _altered_app(monkeypatch, lambda a: a.update(swing_deg=a["swing_deg"] + 3.0))
    off = _again(base)
    for tag in ("abs", "clock", "matched"):
        assert abs(off[f"bias_{tag}"] - good[f"bias_{tag}"] - 3.0) < 0.01
    assert abs(off["bias_rest_abs"] - good["bias_rest_abs"] - 3.0) < 0.01


def test_gain_rmse_and_bias_are_over_the_swing_with_hold_and_rest_apart(base):
    """The swing window is release to settled. Moving the zero instant by +/-20 ms moves
    the gain by under 0.015 (and would not change the A3 verdict)."""
    row = base[3]
    assert abs(row["swing_window_s"] - row["settle_after_release_s"]) < 1e-9
    assert abs(row["n_grid_swing"] - row["swing_window_s"] / V.GRID_S) <= 1
    assert row["window_s"] > row["swing_window_s"] + V.PRE_RELEASE_S
    for tag in ("abs", "clock", "lagrm", "matched"):
        for k in ("rmse", "bias", "gain", "r", "loa_lo", "loa_hi", "rmse_hold", "rmse_rest", "bias_hold", "bias_rest"):
            assert np.isfinite(row[f"{k}_{tag}"])
    assert row["gain_matched_zero_pm20ms_span"] < 0.015
    lo, hi = V.PROVISIONAL_A3_GAIN
    assert lo < row["gain_matched"] - row["gain_matched_zero_pm20ms_span"]
    assert row["gain_matched"] + row["gain_matched_zero_pm20ms_span"] < hi
    # the app's zero pose is late; only the absolute comparison carries it
    assert row["bias_abs"] < row["bias_clock"] - 0.5 and row["rmse_abs"] > row["rmse_lagrm"] + 1.0
    assert abs((row["bias_clock"] - row["bias_abs"]) - row["zero_pose_fall_deg"]) < 0.3
    assert row["pass_zero_fall"] is (row["zero_pose_fall_deg"] <= V.PROVISIONAL_ZERO_POSE_FALL_MAX_DEG)


_PASSING = dict(rmse_lagrm=1.0, gain_matched=1.0, r_lagrm=0.999, first_excursion_encoder_deg=60.0,
                first_excursion_error_deg=-1.0, f_error_pct=0.5, tau_error_pct=3.0, n_swings_error=0.0,
                clock_offset_diff_ms=1.0, lag_minus_expected_ms=2.0, zero_pose_fall_deg=0.5)


def test_every_quality_flag_stops_a_swing_being_passed():
    assert V.judge_trial(dict(_PASSING, flags="radius_placeholder"))["pass_all"] is True
    for flag in V.GATING_FLAGS:
        j = V.judge_trial(dict(_PASSING, flags=f"radius_placeholder;{flag}"))
        assert j["not_assessed_because"] == flag
        assert all(v is None for k, v in j.items() if k.startswith("pass_")), flag
        assert not V.is_assessed(dict(j))
    for flag in ("hold_not_still", "phone_gap", "pairing_suspect", "not_settled", "zero_instant_approximate"):
        assert flag in V.GATING_FLAGS


def test_an_inexact_zero_instant_is_looked_for_near_the_release_only_and_is_not_assessed(base, monkeypatch):
    """No tick holds the zero quaternion, and the nearest one anywhere is 5 s before the
    release (in the lift): the instant used stays within half a second of the release, the
    swing is flagged and nothing is passed."""
    def lose_zero(a):
        k = int(np.argmin(np.abs(a["tick_t"] - (base[3]["release_phone_s"] - 5.0))))
        a["tick_dist"] = a["tick_dist"].copy()
        a["tick_dist"][k] = 0.0
        a["t_zero"] = None

    _altered_app(monkeypatch, lose_zero)
    row = _again(base)
    assert row["app_zero_exact"] is False and "zero_instant_approximate" in row["flags"]
    assert abs(row["zero_after_release_ms"]) <= 500.0
    assert row["pass_all"] is None and row["pass_A1_rmse"] is None
    assert np.isfinite(row["rmse_lagrm"])                              # reported all the same


def test_a_zero_pose_taken_where_the_arm_was_still_is_the_apps_fault_and_is_failed(base, monkeypatch):
    """The app zeroes 4 s before the release, in the hold, where the encoder shows the arm
    still. Nothing on the fixture explains it: the swing is judged, and failed."""
    _altered_app(monkeypatch, lambda a: a.update(t_zero=a["t_zero"] - 4.0))
    row = _again(base)
    assert "zero_not_at_release" in row["flags"] and "zero_misplaced_by_app" in row["flags"]
    assert row["zero_after_release_ms"] < -3500.0
    assert row["not_assessed_because"] == "" and V.is_assessed(row)
    assert row["app_fault"] == "zero_misplaced_by_app"
    assert row["pass_no_app_fault"] is False and row["pass_all"] is False
    assert V._verdict(row).startswith("FAIL")


def test_a_zero_pose_taken_on_a_brisk_lift_is_the_operators_fault_and_is_not_assessed(tmp_path, monkeypatch):
    """The arm is lifted at 2.5 rad/s (the run sheet says slowly) and the app zeroes on the
    lift. The encoder shows the arm really did depart there, so the app did what it is
    built to do: a fixture fault, reported and not assessed, and not held against the app."""
    monkeypatch.setattr(S, "LIFT_PEAK_RAD_S", 2.5)
    tr = S.synthesize(40.0, name="brisk", seed=3)
    phone = S.write(tr, str(tmp_path), str(tmp_path / "brisk.bin"))
    _altered_app(monkeypatch, lambda a: a.update(t_zero=1.62))            # in the lift (1.5 to 2.0 s)
    row = V.analyse_trial(phone, str(tmp_path / "brisk.bin"), radius_m=0.30, encoder_opts=ENC)
    assert abs(row["release_phone_s"] - tr.truth["release_t_phone_s"]) < 0.05
    assert "zero_not_at_release" in row["flags"] and "zero_on_other_motion" in row["flags"]
    assert "zero_on_other_motion" in row["not_assessed_because"] and row["app_fault"] == ""
    assert row["pass_all"] is None and V._verdict(row).startswith("NOT ASSESSED")


def test_a_lag_pegged_at_the_search_bound_is_flagged_not_reported(base, monkeypatch):
    _altered_app(monkeypatch, lambda a: a.update(t=a["t"] + 0.40))
    row = _again(base)
    assert "lag_pegged" in row["flags"] and np.isnan(row["lag_ms"])
    assert abs(row["lag_pegged_at_ms"] - 1e3 * V.LAG_SEARCH_S) < 1e-6
    # The clocks are aligned on the phone's own gyro, so an angle more than the search away
    # from it is the app's doing: judged and failed, not excused as "not assessed".
    assert row["not_assessed_because"] == "" and row["app_fault"] == "lag_pegged"
    assert row["pass_lag"] is False and row["pass_all"] is False


def test_a_lag_off_the_footings_is_failed(base, monkeypatch):
    """120 ms late on top of the EMA: inside the search, so it is a lag, and it is judged."""
    _altered_app(monkeypatch, lambda a: a.update(t=a["t"] + 0.12))
    row = _again(base)
    assert abs(row["lag_minus_expected_ms"] - 120.0 - base[3]["lag_minus_expected_ms"]) < 5.0
    assert row["pass_lag"] is False and "lag_pegged" not in row["flags"]


def test_the_swing_count_is_made_on_each_source(base, monkeypatch):
    """An app reading half the angle loses its small swings under the floor: it counts fewer
    than the encoder, and A5 fails on the count (the count is not the app scorer's N)."""
    assert abs(base[3]["n_swings_error"]) <= 1
    _altered_app(monkeypatch, lambda a: a.update(swing_deg=0.5 * a["swing_deg"]))
    row = _again(base)
    assert row["n_swings_phone"] <= row["n_swings_encoder"] - 2 and row["pass_A5_n"] is False


def test_a_swing_paired_with_another_swings_encoder_log_is_flagged(base, tmp_path):
    """The 40 deg phone export against a 20 deg encoder log. The cross-correlation is above
    0.9 (it does not discriminate); the release angle and the peak speed do."""
    other = _trial(tmp_path, "twenty", release_deg=20.0, seed=9)
    row = V.analyse_trial(base[1], other[2], radius_m=0.30, encoder_opts=ENC, nominal_release_deg=40.0)
    assert row["xcorr_quality"] > 0.9
    assert "release_angle_mismatch" in row["flags"] and "pairing_suspect" in row["flags"]
    assert row["pass_all"] is None
    # the phone's own accelerometer says it was held 40 deg from where it came to rest
    assert abs(row["phone_tilt_release_deg"] - 40.0) < 1.5 and row["phone_tilt_minus_encoder_deg"] > 15.0
    assert "UNRESOLVED" in V._verdict(row)
    assert "release_angle_mismatch" in _again(base, nominal_release_deg=20.0)["flags"]
    assert _again(base, nominal_release_deg=40.0)["flags"] == ""


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
        assert abs(r["release_angle_minus_nominal_deg"]) < 0.2 and r["flags"] == ""
        assert r["platform"] == "iphone" and r["phone_radius_measured"] is True
    assert {r["added_mass_kg"] for r in rows} == {0.0, 3.0, 6.0}


def test_session_report_matches_the_run_sheet_and_says_what_every_number_is(session, tmp_path):
    folder, truths, rows, sess = session
    md = V.write_report(rows, [sess], str(tmp_path / "report"))
    assert V.LIMITS_STATUS in md and "SYNTHETIC DATA" in md
    assert "| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file | App result | Stopped by |" in md
    for tid in truths:
        assert f"`{tid}`" in md
    for crit in ("| A1 |", "| A2 |", "| A3 |", "| A4 |", "| A5 |", "| A7 |", "| A9* |", "| L* |", "| Z* |"):
        assert crit in md
    assert "NOT EVALUATED" in md and "REFERENCE_REPLAY_CONFIG" in md
    assert "| W0 |" in md and "20 deg" in md and "40 deg" in md
    # every figure names its comparison; the three gains sit side by side
    for label in ("RMSE absolute", "RMSE clock-aligned", "RMSE lag-removed", "RMSE EMA-matched", "Gain raw gyro",
                  "Gain app vs unsmoothed encoder (lag-removed)", "Gain app vs EMA-matched encoder",
                  "| # | Criterion | Judged on |"):
        assert label in md
    assert "6/6 swings assessed" in md
    assert "wrong polarity" in md and "not observable" in md
    assert "does not prove the pairing" in md and "Counts agree" not in md
    assert "## Provisional constants awaiting the supervisor" in md
    for name, _ in V.provisional_constants():
        assert f"`{name}`" in md
    with open(tmp_path / "report" / "trials.csv", encoding="utf-8", newline="") as fh:
        table = list(csv.DictReader(fh))
    assert len(table) == len(rows) and table[0]["footing"] == V.REFERENCE_FOOTING
    assert table[0]["limits_status"] == V.LIMITS_STATUS and "EMA-matched" in table[0]["judged_on"]


def test_pooled_criteria_use_one_number_per_swing(session):
    rows = session[2]
    P = V.pooled(rows)
    assert P["n"] == P["n_total"] == 6 and "bias_pooled" in P and "mass_slope" in P
    assert P["loa_lo_bound"] < P["loa_lo"] < P["bias_pooled"] < P["loa_hi"] < P["loa_hi_bound"]
    # the clock rate is judged on the session's fit: tighter than any one swing's
    assert P["clock_ppm_n"] == 6 and P["clock_ppm_se"] < min(r["clock_rate_ppm_se"] for r in rows)
    assert abs(P["clock_ppm_mean"]) < 3 * P["clock_ppm_se"] + 20.0
    v = {x["criterion"]: x for x in V.verdicts(rows)}
    assert v["A1"]["verdict"] == "PASS" and v["A3"]["verdict"] == "PASS" and v["A9*"]["verdict"] == "PASS"
    assert v["L*"]["verdict"] == "PASS"
    assert "EMA-matched" in v["A3"]["judged_on"] and "lag removed" in v["A1"]["judged_on"]
    assert v["A6"]["verdict"].startswith("NOT EVALUATED") and v["A8"]["verdict"].startswith("NOT EVALUATED")
    assert V.verdicts(rows[:2])[1]["verdict"] == "NOT ASSESSABLE"      # A2 needs three swings


def _copy_session(src, dst):
    import shutil
    shutil.copytree(src, dst)
    return str(dst)


def _edit_log(folder, change):
    log = os.path.join(folder, "rig_log.csv")
    with open(log, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    cols = list(rows[0])
    cols, rows = change(cols, rows) or (cols, rows)
    with open(log, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def glob_one(folder, sub, pat):
    import glob
    (hit,) = glob.glob(os.path.join(folder, sub, pat))
    return hit


def test_a_count_mismatch_stops_the_session(session, tmp_path):
    folder = _copy_session(session[0], tmp_path / "s")
    _edit_log(folder, lambda cols, rows: (cols, rows[:-1]))
    with pytest.raises(V.ValidationError, match="COUNT MISMATCH: 5 rig-log rows without ERROR against 6"):
        V.analyse_session(folder)
    rows, sess = V.analyse_session(folder, allow_count_mismatch=True)
    assert "COUNT MISMATCH" in sess["pairing_note"]
    # the sixth phone trial has no rig-log row: it is in the table, not dropped
    assert len(rows) == 7 and "unpaired" in rows[5]["error"] and rows[5]["phone_file"].endswith(".jsonl")
    assert "unpaired" in rows[6]["error"] and rows[6]["encoder_file"].endswith("_006.bin")    # and so is its encoder file
    vs = {v["criterion"]: v for v in V.verdicts(rows)}
    assert not any(v["verdict"] == "PASS" for v in vs.values())
    assert vs["A1"]["verdict"] == "INCOMPLETE" and "5/7 swings assessed, 2 of them unpaired leftovers" in vs["A1"]["value"]
    md = V.summary_markdown(rows, [sess])
    assert "unpaired" in md and rows[5]["phone_file"] in md


def test_the_encoder_files_are_counted_and_a_reused_number_is_refused(session, tmp_path):
    import shutil
    extra = _copy_session(session[0], tmp_path / "extra")
    shutil.copy(glob_one(extra, "encoder", "*_001.bin"), os.path.join(extra, "encoder", "pf1126_009.bin"))
    with pytest.raises(V.ValidationError, match="6 rig-log rows name an encoder file against 7 encoder files"):
        V.analyse_session(extra)
    twice = _copy_session(session[0], tmp_path / "twice")
    shutil.copy(glob_one(twice, "encoder", "*_002.bin"), os.path.join(twice, "encoder", "retake_001.bin"))
    with pytest.raises(V.ValidationError, match="two files end in the number 1"):
        V.analyse_session(twice, allow_count_mismatch=True)

    def same_number(cols, rows):
        rows[1]["encoder_trial"] = rows[0]["encoder_trial"]

    _edit_log(extra, same_number)
    with pytest.raises(V.ValidationError, match="more than one row"):
        V.analyse_session(extra, allow_count_mismatch=True)


def test_a_pairing_shifted_by_one_with_equal_counts_is_flagged_and_passes_nothing(session, tmp_path):
    """Rows 1 and 2 (20 and 40 deg) name each other's encoder file. Every count agrees; the
    encoder's release angle does not match the log's, so both swings are not assessed and
    no criterion can print PASS."""
    folder = _copy_session(session[0], tmp_path / "s")

    def swap(cols, rows):
        rows[0]["encoder_trial"], rows[1]["encoder_trial"] = rows[1]["encoder_trial"], rows[0]["encoder_trial"]

    _edit_log(folder, swap)
    rows, sess = V.analyse_session(folder)
    assert "pairing_note" not in sess
    for r in rows[:2]:
        assert "release_angle_mismatch" in r["flags"] and r["pass_all"] is None
    assert all(r["flags"] == "" for r in rows[2:])
    vs = V.verdicts(rows)
    assert not any(v["verdict"] == "PASS" for v in vs)
    assert all("4/6 swings assessed" in v["value"] for v in vs if v["criterion"] in ("A1", "A3", "A4", "A5", "A9*"))
    assert "NOT ASSESSED" in V.summary_markdown(rows, [sess])


def test_a_swing_that_could_not_be_analysed_stops_every_criterion_passing(session, tmp_path):
    """An ERROR attempt in the log is dropped from the pairing; a swing whose encoder log
    cannot be read stays in the table as not analysed, and no criterion prints PASS."""
    folder = _copy_session(session[0], tmp_path / "s")

    def add_error(cols, rows):
        rows.append(dict(seq="7", trial_id="PF-S1-W4-A60-Q-07", encoder_trial="", condition="W4",
                         release_angle_deg="60", mode="quicktest", app_result="ERROR: no release", stopped_by="auto"))

    _edit_log(folder, add_error)
    with open(glob_one(folder, "encoder", "*_003.bin"), "r+b") as fh:
        fh.truncate(7 * 8 * 50)                                        # 50 samples: not a recording
    rows, sess = V.analyse_session(folder)
    assert len(rows) == 6 and len(sess["log"]) == 7 and len(sess["usable"]) == 6
    assert "too short" in rows[2]["error"] and rows[2]["trial_id"].endswith("-03")
    vs = {v["criterion"]: v for v in V.verdicts(rows)}
    assert vs["A1"]["verdict"] == "INCOMPLETE" and "5/6 swings assessed" in vs["A1"]["value"]
    assert not any(v["verdict"] == "PASS" for v in vs.values())
    md = V.summary_markdown(rows, [sess])
    assert "not analysed" in md and "1 marked ERROR" in md and "INCOMPLETE" in md


def test_a_rig_log_that_cannot_carry_the_pairing_is_refused(session, tmp_path):
    gone = _copy_session(session[0], tmp_path / "gone")
    os.remove(os.path.join(gone, "rig_log.csv"))
    with pytest.raises(V.ValidationError, match="no rig_log.csv"):
        V.analyse_session(gone)
    bare = _copy_session(session[0], tmp_path / "bare")
    _edit_log(bare, lambda cols, rows: ([c for c in cols if c != "app_result"], rows))
    with pytest.raises(V.ValidationError, match="app_result"):
        V.analyse_session(bare)


def test_session_json_must_say_what_the_encoder_is_and_takes_no_unknown_key(session, tmp_path):
    folder = _copy_session(session[0], tmp_path / "s")
    sj = os.path.join(folder, "session.json")
    with open(sj, encoding="utf-8") as fh:
        good = json.load(fh)

    def put(info):
        with open(sj, "w", encoding="utf-8") as fh:
            json.dump(info, fh)

    for key in ("sample_rate_hz", "n_channels"):
        put(dict(good, encoder={k: v for k, v in good["encoder"].items() if k != key}))
        with pytest.raises(V.ValidationError, match=f"encoder.{key}"):
            V.analyse_session(folder)
    put(dict(good, phone_radius=0.3))                                   # misspelt
    with pytest.raises(V.ValidationError, match="unknown key"):
        V.analyse_session(folder)
    put(dict(good, encoder=dict(good["encoder"], samplerate=1000.0)))   # misspelt reader option
    rows, _ = V.analyse_session(folder)
    assert all("not what the" in r["error"] for r in rows)
    # a radius with no statement that it was measured is the placeholder, and says so
    info = {k: v for k, v in good.items() if k != "phone_radius_measured"}
    info["encoder"] = dict(good["encoder"], model="")
    put(info)
    rows, sess = V.analyse_session(folder)
    assert "encoder.model" in sess["info"]["_to_fill"]
    assert all("radius_placeholder" in r["flags"] and r["phone_radius_measured"] is False for r in rows)
    assert all(r["phone_radius_m"] == V.PHONE_RADIUS_PLACEHOLDER_M for r in rows)
    assert "PLACEHOLDER, not recorded as measured" in V.summary_markdown(rows, [sess])


def test_the_session_template_is_one_the_loader_accepts(tmp_path):
    """Every key of docs/bench's session.json template is known, and it is refused only for
    what is still to fill (the sample rate), not for its shape."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "docs", "bench", "2026-11-18-free-pendulum-session-template.json"), encoding="utf-8") as fh:
        tpl = json.load(fh)
    assert set(tpl) <= V.SESSION_KEYS and set(tpl["phone"]) <= V.PHONE_KEYS
    (tmp_path / "session.json").write_text(json.dumps(tpl), encoding="utf-8")
    with pytest.raises(V.ValidationError, match="encoder.sample_rate_hz"):
        V.load_session(str(tmp_path))


# ───────────────────────── review findings: a PASS that should not be one ─────────────────────────
def _cut_phone(src, dst, t_end_s):
    """The export with every record later than t_end_s (from its first sample) removed."""
    with open(src, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    t0 = min(r["t"] for r in recs)
    with open(dst, "w", encoding="utf-8") as out:
        for r in recs:
            if r["t"] - t0 <= t_end_s:
                out.write(json.dumps(r) + "\n")
    return str(dst)


def test_a_phone_record_that_ends_mid_swing_is_not_passed(base, tmp_path):
    """40 deg, zeta 0.03, 60 Hz phone; the phone export stops 4 s after the release while
    the encoder runs on to rest. The arm was never seen to settle by BOTH records: the swing
    is flagged and not assessed (it used to be PASS with settled=True and no flag)."""
    tr, phone, enc, full = base
    assert full["settled"] is True and full["pass_all"] is True
    cut = _cut_phone(phone, tmp_path / "cut.jsonl", tr.truth["release_t_phone_s"] + 4.0)
    row = V.analyse_trial(cut, enc, radius_m=0.30, encoder_opts=ENC, nominal_release_deg=40.0)
    assert row["swing_window_s"] < 4.0 < row["settle_after_release_s"]
    assert row["settled"] is False
    assert "not_settled" in row["flags"] and "phone_ends_before_settled" in row["flags"]
    assert "phone_ends_before_settled" in row["not_assessed_because"]
    assert row["pass_all"] is None and row["pass_A1_rmse"] is None
    assert V._verdict(row).startswith("NOT ASSESSED")
    assert np.isfinite(row["rmse_lagrm"])                              # reported all the same


@pytest.mark.parametrize("scale", [0.78, 1.30])
def test_a_grossly_wrong_gyro_is_judged_and_failed_not_called_a_pairing_problem(tmp_path, scale):
    """A phone whose gyro reads 22% low or 30% high. Its peak speed is outside the pairing
    window, but its accelerometer says it was held at the encoder's release angle and the
    rates have one shape: the pair is right, the phone is wrong. A3 FAIL, not NOT ASSESSED."""
    row = _trial(tmp_path, "gross", release_deg=40.0, seed=3, gyro_scale=scale)[3]
    lo, hi = V.PAIRING_PEAK_RATE_RATIO
    assert not (lo <= row["peak_rate_gain"] <= hi) and row["xcorr_quality"] > 0.99
    assert abs(row["phone_tilt_release_deg"] - 40.0) < 1.5 and abs(row["phone_tilt_minus_encoder_deg"]) < 1.5
    assert "pairing_suspect" not in row["flags"] and "phone_rate_off" in row["flags"]
    assert row["not_assessed_because"] == "" and V.is_assessed(row)
    assert abs(row["gain_gyro"] - scale) < 0.01
    assert row["pass_A3_gain"] is False and row["pass_all"] is False
    assert V._verdict(row) == "FAIL"


def test_a_session_with_a_grossly_wrong_gyro_fails_A3(tmp_path):
    folder = str(tmp_path / "S1")
    S.write_session(folder, conditions=("W2",), angles=(20, 40, 60), gyro_scale=0.78)
    rows, sess = V.analyse_session(folder)
    assert all(V.is_assessed(r) and "phone_rate_off" in r["flags"] for r in rows)
    vs = {v["criterion"]: v for v in V.verdicts(rows)}
    assert vs["A3"]["verdict"] == "FAIL" and "3/3 swings assessed" in vs["A3"]["value"]


def test_a_wrong_rate_with_nothing_to_corroborate_the_pair_is_unresolved_and_says_so(session, base, monkeypatch):
    """When the peak speeds disagree and the phone's static tilt cannot vouch for the pair,
    a wrong pairing and a faulty phone cannot be told apart. The swing is not assessed, and
    the session says UNRESOLVED on every line it would have called INCOMPLETE."""
    monkeypatch.setattr(V, "PAIRING_PEAK_RATE_RATIO", (1.05, 1.25))    # the base swing is now "off"
    monkeypatch.setattr(V, "PAIRING_RELEASE_ANGLE_TOL_DEG", 0.0)       # and its tilt vouches for nothing
    row = _again(base)
    assert "pairing_suspect" in row["flags"] and row["pass_all"] is None
    assert "UNRESOLVED" in V._verdict(row)
    monkeypatch.undo()
    rows = [dict(r) for r in session[2]]
    rows[1]["flags"] = "pairing_suspect"
    rows[1].update(V.judge_trial(rows[1]))
    vs = {v["criterion"]: v for v in V.verdicts(rows)}
    assert vs["A1"]["verdict"].startswith("UNRESOLVED") and "INCOMPLETE" not in vs["A1"]["verdict"]
    assert "wrong pairing or a faulty phone" in vs["A1"]["verdict"]
    assert not any(v["verdict"] == "PASS" for v in vs.values())
    md = V.summary_markdown(rows, [session[3]])
    assert "UNRESOLVED" in md and "cannot be told apart" in md


def test_a_restless_hold_is_the_fixtures_only_when_the_encoder_saw_it(base, monkeypatch):
    """Phone gyro over the limit during a hold the encoder shows still (base: 0.006 against
    0.003 rad/s): a noisy gyro or a loose clamp, not a moving arm. Judged, with a flag.
    Both over the limit: the arm moved; not assessed."""
    monkeypatch.setattr(V, "HOLD_STILL_MAX_RAD_S", 0.0045)
    row = _again(base)
    assert row["hold_rms_encoder_rad_s"] < 0.0045 < row["hold_rms_phone_rad_s"]
    assert "phone_hold_not_still" in row["flags"] and "hold_not_still" not in row["flags"].split(";")
    assert row["not_assessed_because"] == "" and row["pass_all"] is True
    monkeypatch.setattr(V, "HOLD_STILL_MAX_RAD_S", 0.001)
    row = _again(base)
    assert "hold_not_still" in row["flags"].split(";") and row["pass_all"] is None


def test_an_app_fault_is_a_session_line_that_fails(session):
    rows = [dict(r) for r in session[2]]
    rows[2]["flags"] = "zero_not_at_release;zero_misplaced_by_app"
    rows[2].update(V.judge_trial(rows[2]))
    assert rows[2]["pass_all"] is False and V.is_assessed(rows[2])
    vs = {v["criterion"]: v for v in V.verdicts(rows)}
    assert vs["APP*"]["verdict"] == "FAIL" and rows[2]["trial_id"] in vs["APP*"]["value"]
    assert vs["A7"]["verdict"] == "FAIL"                               # "every cell passes" no longer holds
    assert {v["criterion"]: v for v in V.verdicts(session[2])}["APP*"]["verdict"] == "PASS"
    md = V.summary_markdown(rows, [session[3]])
    assert "FAIL (app fault: zero_misplaced_by_app)" in md


def test_one_failing_swing_fails_the_criterion_whatever_else_is_unassessable(session):
    """`allof`: a None beside a False used to give NOT ASSESSABLE."""
    rows = [dict(r) for r in session[2]]
    assert {v["criterion"]: v for v in V.verdicts(rows)}["A1"]["verdict"] == "PASS"
    rows[0]["pass_A1_rmse"] = False
    rows[1]["pass_A1_rmse"] = None
    assert {v["criterion"]: v for v in V.verdicts(rows)}["A1"]["verdict"] == "FAIL"
    rows[0]["pass_A1_rmse"] = True
    assert {v["criterion"]: v for v in V.verdicts(rows)}["A1"]["verdict"] == "NOT ASSESSABLE"
    rows = [dict(r) for r in session[2]]
    rows[3]["pass_A5_tau"], rows[4]["pass_A5_f"] = None, False
    assert {v["criterion"]: v for v in V.verdicts(rows)}["A5"]["verdict"] == "FAIL"


def test_the_thresholds_that_decide_whether_a_swing_is_judged_are_listed_as_proposed(session):
    consts = dict(V.provisional_constants())
    for name in ("HOLD_STILL_MAX_RAD_S", "PAIRING_MIN_XCORR", "PAIRING_PEAK_RATE_RATIO",
                 "PAIRING_RELEASE_ANGLE_TOL_DEG", "ZERO_WINDOW_S", "SETTLED_RATE_RAD_S", "SETTLED_FOR_S",
                 "LAG_SEARCH_S"):
        assert name in V.GATING_THRESHOLD_NAMES and consts[name] == getattr(V, name)
    md = V.summary_markdown(session[2], [session[3]])
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "docs", "bench", "FREE_PENDULUM_ENCODER_ANALYSIS.md"), encoding="utf-8") as fh:
        doc = fh.read()
    for name in V.GATING_THRESHOLD_NAMES:
        (line,) = [ln for ln in md.splitlines() if ln.startswith(f"| `{name}` |")]
        assert "PROPOSED" in line and "gating" in line
        assert f"`{name}`" in doc
    assert "gating threshold" in doc and "PROPOSED" in doc


def test_allowing_a_count_mismatch_never_prints_pass(session, tmp_path):
    """A stray encoder file, counts allowed to differ: every rig-log row still pairs, but the
    stray file is in the table and no criterion prints PASS."""
    import shutil
    extra = _copy_session(session[0], tmp_path / "extra")
    shutil.copy(glob_one(extra, "encoder", "*_001.bin"), os.path.join(extra, "encoder", "pf1126_009.bin"))
    rows, sess = V.analyse_session(extra, allow_count_mismatch=True)
    assert len(rows) == 7 and "pf1126_009.bin" in rows[6]["encoder_file"] and "unpaired" in rows[6]["error"]
    vs = V.verdicts(rows)
    assert not any(v["verdict"] == "PASS" for v in vs)
    assert {v["criterion"]: v for v in vs}["A1"]["verdict"] == "INCOMPLETE"
    # even when nothing is left over to show as a row, an allowed mismatch is not a PASS
    clean = [dict(r, pairing_count_mismatch=True) for r in session[2]]
    assert {v["criterion"]: v for v in V.verdicts(clean)}["A1"]["verdict"] == "INCOMPLETE"


def test_a_row_with_no_release_angle_anywhere_says_the_check_was_not_made(session, tmp_path):
    folder = _copy_session(session[0], tmp_path / "s")

    def blank(cols, rows):
        rows[1]["release_angle_deg"], rows[1]["trial_id"] = "", "second swing"

    _edit_log(folder, blank)
    rows, _ = V.analyse_session(folder)
    assert "release_angle_not_checked" in rows[1]["flags"]
    assert "release_angle_not_checked" in rows[1]["not_assessed_because"] and rows[1]["pass_all"] is None
    assert all("release_angle_not_checked" not in r["flags"] for r in rows[:1] + rows[2:])
    # blank, but the trial ID carries it: read from the ID, checked as before
    again = _copy_session(session[0], tmp_path / "t")
    _edit_log(again, lambda cols, rows: rows[1].update(release_angle_deg=""))
    rows, _ = V.analyse_session(again)
    assert rows[1]["flags"] == "" and rows[1]["release_nominal_deg"] == 40.0


def test_a_release_angle_that_is_not_a_number_is_refused(session, tmp_path, capsys):
    folder = _copy_session(session[0], tmp_path / "s")
    _edit_log(folder, lambda cols, rows: rows[2].update(release_angle_deg="forty"))
    with pytest.raises(V.ValidationError, match="release_angle_deg 'forty'"):
        V.load_session(folder)
    assert V.main(["session", folder]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "release_angle_deg" in err and "Traceback" not in err


def _edit_manifest(folder, change):
    path = glob_one(folder, "phone", "*manifest*.json")
    with open(path, encoding="utf-8") as fh:
        man = json.load(fh)
    change(man)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(man, fh)


def test_a_manifest_without_timestamps_is_paired_in_manifest_order_and_says_so(session, tmp_path, capsys):
    """One trial with no timestamp used to sort to the front and shift the pairing. Now the
    manifest's own order is used for all, every swing carries a flag and the report says it."""
    folder = _copy_session(session[0], tmp_path / "s")
    _edit_manifest(folder, lambda man: man["trials"][3].pop("timestamp"))
    rows, sess = V.analyse_session(folder)
    assert "manifest order" in sess["order_note"] and "1 of 6" in sess["order_note"]
    assert "WARNING" in capsys.readouterr().err
    assert [r["trial_id"] for r in rows] == list(session[1])
    for r in rows:
        assert "manifest_order_unverified" in r["flags"] and "release_angle_mismatch" not in r["flags"]
        assert abs(r["clock_offset_release_s"] - session[1][r["trial_id"]]["clock_offset_s"]) < 0.005
    assert sess["order_note"] in V.summary_markdown(rows, [sess])
    assert "order_note" not in session[3]


def test_a_wrong_channel_count_is_refused_with_its_reason(base, tmp_path, capsys):
    _, phone, enc, _ = base
    size = os.path.getsize(enc)
    ragged = next(n for n in (6, 8, 5, 9) if size % (n * 8))          # not a whole number of samples
    args = ["trial", "--phone", phone, "--encoder", enc, "--encoder-fs", "1000", "--out", str(tmp_path / "r")]
    assert V.main(args + ["--encoder-channels", str(ragged)]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "n_channels" in err and "Traceback" not in err
    # one channel always divides: every channel is then read as position, which no arm can do
    assert V.main(args + ["--encoder-channels", "1"]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "n_channels" in err and "not continuous" in err
    assert V.main(args + ["--encoder-channels", "7"]) == 0


def test_a_non_finite_phone_sample_is_refused_with_its_reason(base, tmp_path, capsys):
    _, phone, enc, _ = base
    seen = {"n": 0}

    def one_nan(r):
        if r["sensor"] == "gyro":
            seen["n"] += 1
            if seen["n"] == 500:
                r["v"] = [float("nan"), r["v"][1], r["v"][2]]

    bad = _rewrite(phone, tmp_path / "nan.jsonl", one_nan)
    with pytest.raises(V.ValidationError, match="non-finite gyro sample"):
        V.load_phone_export(bad)
    assert V.main(["trial", "--phone", bad, "--encoder", enc, "--encoder-fs", "1000", "--encoder-channels", "7",
                   "--out", str(tmp_path / "r")]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "non-finite gyro sample" in err and "Traceback" not in err
    (tmp_path / "junk.jsonl").write_text('{"t": 1.0, "role": "distal"\n', encoding="utf-8")
    with pytest.raises(V.ValidationError, match="not JSON"):
        V.load_phone_export(str(tmp_path / "junk.jsonl"))
