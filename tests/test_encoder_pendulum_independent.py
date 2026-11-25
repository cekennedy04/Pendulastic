"""encoder_pendulum_validation against a ground truth that shares nothing with it.

`encoder_pendulum_synth` and `neuromech.phone_trial` were written alongside the analysis,
so a test built on them can only show the two agree with each other. Here the pendulum,
the sensors and both files are made from first principles in this file: an adaptive ODE
solver (not the fixed-step integrator), the swing running to rest on its own (no frozen
tail), no lift, a 100 Hz phone in an arbitrary mount with the pivot along a different
body axis, and an encoder that counts the other way from a non-zero zero. The files are
written byte by byte as the formats are documented, not through the repo's writers.

Synthetic only. Tolerances are how well the planted truth is recovered, not acceptance limits.
"""
import json
import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

import encoder_pendulum_validation as V

G = 9.81
T0_PHONE = 512.25            # the phone's first timestamp, s (arbitrary)
ENC = dict(sample_rate_hz=1000.0, n_channels=7)


def _mount():
    """A fixed rotation of the phone on the arm: 0.7 rad about (1, 2, -1) (Rodrigues)."""
    k = np.array([1.0, 2.0, -1.0]) / math.sqrt(6.0)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(0.7) * K + (1 - math.cos(0.7)) * (K @ K)


def make_trial(folder, name, *, release_deg=40.0, l_eq=0.41, zeta=0.025, radius=0.22, hold_s=4.0,
               hand_s=0.0, total_s=48.0, gyro_scale=1.0, enc_start=0.613, phone_fs=100.0, seed=11):
    """Arm held still at `release_deg`, let go at phone time `hold_s` (the hand's support
    fading over `hand_s`), swinging as theta'' = -(g/L) sin(theta) - 2 zeta wn theta'.

    Arm frame: e1 along the arm away from the pivot, e2 the pivot axis, e3 = e1 x e2 (the
    direction the phone moves when theta grows). There, with the phone at radius r:
      angular velocity   (0, -theta', 0)
      specific force     (-g cos(theta) - r theta'^2, 0, g sin(theta) + r theta'')
    which is +g along "up" at rest. -> (phone path, encoder path, truth)."""
    rng = np.random.default_rng(seed)
    th0 = math.radians(release_deg)
    wn = math.sqrt(G / l_eq)
    c = 2 * zeta * wn

    def rhs(t, y):
        support = 1.0 if hand_s <= 0 else min(max(t / hand_s, 0.0), 1.0)
        return [y[1], -support * wn * wn * math.sin(y[0]) - c * y[1]]

    sol = solve_ivp(rhs, (0.0, total_s), [th0, 0.0], method="DOP853", dense_output=True,
                    rtol=1e-10, atol=1e-12, max_step=0.02)

    def state(t_from_release):
        t = np.asarray(t_from_release, float)
        y = sol.sol(np.clip(t, 0.0, total_s))
        th = np.where(t < 0, th0, y[0])
        om = np.where(t < 0, 0.0, y[1])
        support = 1.0 if hand_s <= 0 else np.clip(t / hand_s, 0.0, 1.0)
        al = np.where(t < 0, 0.0, -support * wn * wn * np.sin(y[0]) - c * y[1])
        return th, om, al

    # the phone
    tp = np.arange(0.0, hold_s + total_s, 1.0 / phone_fs)
    tp[1:] += np.clip(rng.normal(0, 0.0004, len(tp) - 1), -0.003, 0.003)
    th, om, al = state(tp - hold_s)
    gyro_arm = np.column_stack([np.zeros_like(om), -om, np.zeros_like(om)])
    acc_arm = np.column_stack([-G * np.cos(th) - radius * om ** 2, np.zeros_like(th), G * np.sin(th) + radius * al])
    M = _mount()
    gyro = gyro_scale * gyro_arm @ M.T + np.array([0.004, -0.003, 0.002]) + rng.normal(0, 0.002, gyro_arm.shape)
    accel = acc_arm @ M.T + rng.normal(0, 0.03, acc_arm.shape)
    phone = folder / f"{name}.jsonl"
    with open(phone, "w", encoding="utf-8") as fh:
        for t, a, w in zip(tp, accel, gyro):
            for sensor, v in (("accel", a), ("gyro", w)):
                fh.write(json.dumps({"t": T0_PHONE + float(t), "role": "distal", "sensor": sensor,
                                     "v": [float(x) for x in v],
                                     "phone_ts_ms": int(round((T0_PHONE + float(t)) * 1000))}) + "\n")

    # the encoder: its own clock from `enc_start`, counting the other way from 12.3 deg, 16384 counts/rev
    te = np.arange(0.0, hold_s + total_s - enc_start, 1e-3)
    th_e, _, _ = state(te + enc_start - hold_s)
    step = 360.0 / 16384
    logged = np.round((12.3 - np.degrees(th_e)) / step) * step
    frame = np.zeros((len(te), ENC["n_channels"]))
    frame[:, 0] = logged
    enc = folder / f"{name}.bin"
    frame.astype(">f8").tofile(enc)

    # truth, measured on the solution itself
    fine = np.arange(0.0, total_s, 5e-4)
    th_f, om_f, _ = state(fine)
    up = np.nonzero((om_f[:-1] < 0) & (om_f[1:] >= 0))[0]            # one far-side turning point per cycle
    t_turn = fine[up]
    amp = np.abs(np.degrees(th_f[up]))
    big = amp >= max(1.0, 0.05 * amp[0])
    sigma = -np.polyfit(t_turn[big], np.log(amp[big]), 1)[0]
    truth = dict(release_phone_s=hold_s, clock_offset_s=enc_start, period_s=float(np.median(np.diff(t_turn[big]))),
                 tau_s=float(1.0 / sigma), release_deg=release_deg, gyro_scale=gyro_scale,
                 angle_from_hold_deg=lambda t_phone: np.abs(np.degrees(state(np.asarray(t_phone) - hold_s)[0]) - release_deg))
    return str(phone), str(enc), truth


def _analyse(folder, name, **kw):
    phone, enc, truth = make_trial(folder, name, **kw)
    row = V.analyse_trial(phone, enc, encoder_opts=ENC, radius_m=kw.get("radius", 0.22),
                          nominal_release_deg=kw.get("release_deg", 40.0))
    return phone, enc, truth, row


@pytest.fixture(scope="module")
def perfect(tmp_path_factory):
    return _analyse(tmp_path_factory.mktemp("gt"), "perfect")


@pytest.fixture(scope="module")
def high(tmp_path_factory):
    """The same swing through a gyro that reads 5% high."""
    return _analyse(tmp_path_factory.mktemp("gt"), "high", gyro_scale=1.05)


def test_clock_offset_is_recovered_both_ways(perfect, high):
    """Within 5 ms of the planted 613 ms by the release and by cross-correlation."""
    for _, _, truth, row in (perfect, high):
        assert abs(row["clock_offset_release_s"] - truth["clock_offset_s"]) < 0.005
        assert abs(row["clock_offset_xcorr_s"] - truth["clock_offset_s"]) < 0.005
        assert abs(row["release_phone_s"] - truth["release_phone_s"]) < 0.02
        assert row["flags"] == "" and row["not_assessed_because"] == ""


def test_the_pendulum_itself_is_recovered_from_the_encoder(perfect):
    """Period within 0.3%, decay time within 5%, release angle within 0.2 deg of the ODE's."""
    _, _, truth, row = perfect
    assert abs(row["period_encoder"] / truth["period_s"] - 1) < 0.003
    assert abs(row["tau_encoder"] / truth["tau_s"] - 1) < 0.05
    assert abs(row["release_from_rest_deg"] - truth["release_deg"]) < 0.2
    assert abs(row["release_angle_minus_nominal_deg"]) < 0.2


def test_the_raw_gyro_gain_is_the_planted_scale(perfect, high):
    assert abs(perfect[3]["gain_gyro"] - 1.00) < 0.01
    assert abs(high[3]["gain_gyro"] - 1.05) < 0.01


def test_a_perfect_phone_passes_A3_and_one_reading_five_percent_high_fails(perfect, high):
    """The fault the review found: against the unsmoothed encoder the footing's EMA lowers
    the gain by about 6%, so a phone reading 5% high lands in the window and a perfect one
    falls out of it. Judged on the EMA-matched gain, the verdicts are the right way round."""
    assert V.PROVISIONAL_A3_GAIN_VARIANT == "matched"
    good, bad = perfect[3], high[3]
    assert abs(good["gain_matched"] - 1.00) < 0.015 and good["pass_A3_gain"] is True
    assert bad["gain_matched"] > V.PROVISIONAL_A3_GAIN[1] and bad["pass_A3_gain"] is False
    assert bad["pass_all"] is False
    # ... and what the other reading would have said, which is why it is reported beside it:
    assert good["gain_lagrm"] < V.PROVISIONAL_A3_GAIN[0] < bad["gain_lagrm"] < V.PROVISIONAL_A3_GAIN[1]


def test_the_absolute_comparison_is_the_app_against_the_true_angle_from_the_hold(perfect):
    """`_abs` recomputed here from the ODE's own angle and the planted clock offset (no
    alignment, no lag, no zero mapping): the script's figure is within 0.25 deg of it, and
    it is larger than the lag-removed figure, which forgives the app's late zero pose."""
    phone, _, truth, row = perfect
    app = V.app_angle(V.load_phone_export(phone))
    t, s = app["t"], app["swing_deg"]
    m = (t >= truth["release_phone_s"]) & (t < truth["release_phone_s"] + row["swing_window_s"]) & np.isfinite(s)
    d = s[m] - truth["angle_from_hold_deg"](t[m])
    assert abs(row["rmse_abs"] - float(np.sqrt(np.mean(d ** 2)))) < 0.25
    assert abs(row["bias_abs"] - float(np.mean(d))) < 0.25
    assert row["rmse_abs"] > row["rmse_lagrm"] + 1.0
    fall = float(truth["angle_from_hold_deg"](row["app_zero_phone_s"]))
    assert abs(row["zero_pose_fall_deg"] - fall) < 0.15
    assert row["pass_zero_fall"] is (fall <= V.PROVISIONAL_ZERO_POSE_FALL_MAX_DEG)


def test_the_lag_is_the_footings_and_is_judged(perfect):
    """The app's angle trails its own gyro by the footing's EMA: about 64 ms on the 10 ms grid."""
    row = perfect[3]
    assert 60.0 < row["ema_expected_lag_ms"] < 68.0
    assert abs(row["lag_minus_expected_ms"]) < 10.0 and row["pass_lag"] is True
    assert abs(row["lag_matched_ms"]) < 10.0 and abs(row["lag_gyro_ms"]) < 5.0


def test_moving_the_zero_instant_by_20_ms_does_not_move_the_gain(perfect):
    """The swing-window gain moves by under 0.015 for +/-20 ms on the zero instant (it moved
    by 0.05 when the hold was inside the regression), and no verdict changes."""
    row = perfect[3]
    assert row["gain_matched_zero_pm20ms_span"] < 0.015
    lo, hi = V.PROVISIONAL_A3_GAIN
    assert lo < row["gain_matched"] - row["gain_matched_zero_pm20ms_span"]
    assert row["gain_matched"] + row["gain_matched_zero_pm20ms_span"] < hi


def test_a_slow_hand_release_is_never_passed_on_a_zero_pose_from_somewhere_else(tmp_path):
    """The hand's support fading over 400 ms. Wherever the app takes its zero pose, a swing
    is only assessed if that pose is exact and within half a second of the release."""
    _, _, _, row = _analyse(tmp_path, "hand", hand_s=0.4)
    exact_and_near = row["app_zero_exact"] and abs(row["zero_after_release_ms"]) <= 500.0
    if not exact_and_near:
        assert row["pass_all"] is not True and (row["not_assessed_because"] or row["app_fault"])
    assert abs(row["zero_after_release_ms"]) <= 500.0 or "zero_not_at_release" in row["flags"]
    # A slow release lets the arm fall further before the app zeroes; that is judged, not absorbed.
    assert row["zero_pose_fall_deg"] > 1.0 and row["pass_zero_fall"] is False
