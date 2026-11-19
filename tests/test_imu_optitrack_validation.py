"""Tests for imu_optitrack_validation. All synthetic; no recording is read."""
import math

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

import imu_calibration_tuner as tuner
import imu_optitrack_validation as v

_BASE = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.003, 0.021, 0.0]])
FPS = 120.0


def _pendulum(n=1200, amp=60.0, hold_s=1.5, fps=FPS):
    """Flexion-from-hold of a damped pendulum released after a hold."""
    t = np.arange(n) / fps
    s = np.clip(t - hold_s, 0.0, None)
    flex = np.where(t < hold_s, 0.0,
                    amp * (1.0 - np.exp(-0.6 * s) * np.cos(2 * np.pi * 0.9 * s)))
    return t, flex


def _plate(flex_deg, lateral_deg=None, axis=(0.0, 0.0, 1.0), lateral_axis=(1.0, 0.0, 0.0)):
    """A plate swinging about `axis`, optionally also tilting about
    `lateral_axis` (which is perpendicular to it: out-of-plane motion)."""
    n = len(flex_deg)
    out = np.empty((3, n, 3))
    for i in range(n):
        R = Rotation.from_rotvec(np.radians(flex_deg[i]) * np.asarray(axis, float))
        if lateral_deg is not None:
            R = Rotation.from_rotvec(
                np.radians(lateral_deg[i]) * np.asarray(lateral_axis, float)) * R
        out[:, i, :] = R.apply(_BASE) + [0.0, 0.0, 1.0]
    return out


# ── lateral motion (plan U6 scenarios) ────────────────────────────────────────

def test_a_pure_hinge_has_a_flat_lateral_trace():
    t, flex = _pendulum()
    tri = _plate(flex)
    tilt = v.axis_tilt_deg(v.plate_rotvecs(tri), [0.0, 0.0, 1.0])
    assert np.nanmax(tilt) < 1e-4


def test_known_out_of_plane_motion_gives_a_non_flat_lateral_trace_of_that_size():
    t, flex = _pendulum()
    lateral = 12.0 * np.sin(2 * np.pi * 0.9 * np.clip(t - 1.5, 0, None))
    tri = _plate(flex, lateral)
    tilt = v.axis_tilt_deg(v.plate_rotvecs(tri), [0.0, 0.0, 1.0])
    assert np.nanmax(tilt) == pytest.approx(12.0, abs=0.2)
    assert np.nanstd(tilt) > 2.0


def test_lateral_trace_is_nan_where_the_plate_is_untracked():
    t, flex = _pendulum(n=400)
    tri = _plate(flex)
    tri[0, 100:120, :] = np.nan
    tilt = v.axis_tilt_deg(v.plate_rotvecs(tri), [0.0, 0.0, 1.0])
    assert np.isnan(tilt[100:120]).all() and np.isfinite(tilt[150])


def test_plate_rotvecs_can_be_referenced_to_a_later_frame():
    t, flex = _pendulum(n=400)
    rv = v.plate_rotvecs(_plate(flex), ref_index=300)
    assert np.linalg.norm(rv[300]) == pytest.approx(0.0, abs=1e-9)
    assert np.degrees(np.linalg.norm(rv[0])) == pytest.approx(abs(flex[300]), abs=1e-4)


def test_lateral_summary_reports_peak_and_rms_and_declines_too_few_samples():
    y = np.concatenate([np.zeros(50), np.full(50, 10.0)])
    s = v.lateral_summary(y, np.ones(100, bool))
    assert s["peak"] == pytest.approx(10.0)
    assert s["rms"] == pytest.approx(math.sqrt(50.0))
    assert np.isnan(v.lateral_summary(y, np.arange(100) < 5)["peak"])


def test_imu_rotvecs_and_tilt_follow_the_same_definition_as_the_plate():
    """A phone swinging 40 deg about x and leaning 8 deg about y: the swing
    axis is x, and its tilt is the lean."""
    n = 300
    flex = np.linspace(0, 40, n)
    lean = np.linspace(0, 8, n)
    q0 = Rotation.from_euler("zyx", [20, 70, 5], degrees=True)
    rots = [q0 * Rotation.from_rotvec(np.radians([0, l, 0]))
            * Rotation.from_rotvec(np.radians([f, 0, 0])) for f, l in zip(flex, lean)]
    wxyz = lambda r: np.roll(r.as_quat(), 1)            # noqa: E731
    quats = np.array([wxyz(r) for r in rots])
    quats[0] = np.nan
    rv = v.imu_rotvecs(quats, wxyz(q0))
    assert np.isnan(rv[0]).all()
    axis, share = v.principal_axis(rv)
    assert abs(axis[0]) > 0.97 and share > 0.9
    tilt = v.axis_tilt_deg(rv, [1.0, 0.0, 0.0])
    assert tilt[-1] == pytest.approx(8.0, abs=0.05)


def test_axis_elevation_sees_a_lean_out_of_horizontal_but_not_a_turn_about_vertical():
    g = np.array([0.0, 0.0, 1.0])
    axis = np.array([1.0, 0.0, 0.0])
    lean = Rotation.from_rotvec(np.radians([[0, a, 0] for a in (0, 5, 10)])).as_rotvec()
    turn = Rotation.from_rotvec(np.radians([[0, 0, a] for a in (0, 5, 10)])).as_rotvec()
    assert np.abs(v.axis_elevation_change_deg(lean, axis, g)) == pytest.approx([0, 5, 10], abs=1e-6)
    assert np.abs(v.axis_elevation_change_deg(turn, axis, g)) == pytest.approx([0, 0, 0], abs=1e-6)
    assert v.axis_tilt_deg(turn, axis) == pytest.approx([0, 5, 10], abs=1e-6)


def test_euler_roll_change_is_zero_at_the_zero_pose_and_wraps():
    q0 = np.roll(Rotation.from_euler("x", 170, degrees=True).as_quat(), 1)
    q1 = np.roll(Rotation.from_euler("x", -170, degrees=True).as_quat(), 1)
    out = v.euler_roll_change_deg(np.array([q0, q1, [np.nan] * 4]), q0)
    assert out[0] == pytest.approx(0.0, abs=1e-9)
    assert out[1] == pytest.approx(20.0, abs=1e-6)
    assert np.isnan(out[2])


# ── optical curves ────────────────────────────────────────────────────────────

def test_flexion_positive_orients_on_the_largest_excursion():
    y = np.array([5.0, 5.0, 8.0, -55.0, -20.0, np.nan])
    out = v.flexion_positive(y)
    assert out[0] == 0.0 and out[3] == pytest.approx(60.0)
    assert v.flexion_positive(-y)[3] == pytest.approx(60.0)


def test_refit_jumps_counts_impossible_steps_but_allows_motion_across_a_gap():
    t = np.arange(10) / 120.0
    smooth = np.linspace(0, 20, 10)
    assert v.refit_jumps(t, smooth) == 0
    jumped = smooth.copy()
    jumped[5:] += 90.0
    assert v.refit_jumps(t, jumped) == 1
    gap_t = np.array([0.0, 0.01, 0.50, 0.51])
    assert v.refit_jumps(gap_t, np.array([0.0, 1.0, 120.0, 121.0])) == 0


# ── tracking ──────────────────────────────────────────────────────────────────

def test_tracking_recovers_a_known_offset_gain_and_lag():
    t, flex = _pendulum()
    imu_t = np.arange(0, t[-1] + 0.7, 0.01)
    # The phone starts 0.7 s before the take and reads 90 % of the swing.
    imu_flex = 0.9 * np.interp(imu_t - 0.7, t, flex)
    res = v.tracking_agreement(t, flex, imu_t, imu_flex)
    assert res["status"] == "ok"
    assert res["lag_sec"] == pytest.approx(-0.7, abs=0.05)
    assert res["gain"] == pytest.approx(0.9, abs=0.03)
    assert res["r"] > 0.995
    assert res["bias"] < 0 and res["rmse"] > res["rmse_offset_removed"]


def test_tracking_does_not_care_which_way_up_the_optical_curve_is():
    t, flex = _pendulum()
    imu_t = np.arange(0, t[-1], 0.01)
    imu_flex = np.interp(imu_t, t, flex)
    a = v.tracking_agreement(t, flex, imu_t, imu_flex)
    b = v.tracking_agreement(t, -flex, imu_t, imu_flex)
    assert a["status"] == b["status"] == "ok"
    assert b["rmse"] == pytest.approx(a["rmse"], abs=0.5)
    assert a["rmse"] < 1.5


def test_tracking_leaves_optical_gaps_out_instead_of_bridging_them():
    t, flex = _pendulum()
    gappy = flex.copy()
    gappy[300:420] = np.nan                              # 1 s unseen mid-swing
    imu_t = np.arange(0, t[-1], 0.01)
    imu_flex = np.interp(imu_t, t, flex)
    full = v.tracking_agreement(t, flex, imu_t, imu_flex)
    part = v.tracking_agreement(t, gappy, imu_t, imu_flex)
    assert part["status"] == "ok"
    assert part["n"] < full["n"] - 40
    assert part["rmse"] < 1.5


def test_tracking_withholds_bias_when_the_optical_hold_was_not_seen():
    """Hands over the plate through the hold: the optical zero is a mid-swing
    frame, so an offset means nothing. Shape agreement is still reported."""
    t, flex = _pendulum()
    unseen = flex.copy()
    unseen[:int(1.7 * FPS)] = np.nan
    imu_t = np.arange(0, t[-1], 0.01)
    imu_flex = np.interp(imu_t, t, flex)
    res = v.tracking_agreement(t, unseen - 37.0, imu_t, imu_flex)
    assert res["status"] == "ok"
    assert res["zero_on_hold"] is False
    assert np.isnan(res["bias"]) and np.isnan(res["rmse"])
    assert res["rmse_offset_removed"] < 1.5 and res["r"] > 0.99


def test_find_lag_is_not_fooled_by_a_whole_period_of_a_damped_swing():
    """A damped swing one period late is the same shape, smaller: it correlates
    almost perfectly. Residual at unit gain tells them apart."""
    t, flex = _pendulum()
    unseen = flex.copy()
    unseen[:int(1.7 * FPS)] = np.nan                    # plate hidden through the first fall
    imu_t = np.arange(0, t[-1] + 2.0, 0.01)
    imu_flex = np.interp(imu_t - 0.4, t, flex)
    lag = v.find_lag(t, unseen, imu_t, imu_flex)
    assert lag["status"] == "ok"
    assert lag["lag_sec"] == pytest.approx(-0.4, abs=0.02)


def test_one_lag_can_be_shared_by_every_curve_of_a_trial():
    t, flex = _pendulum()
    imu_t = np.arange(0, t[-1] + 1.0, 0.01)
    own_axis = np.interp(imu_t - 0.6, t, flex)
    app = 0.5 * own_axis                                 # the app lost half the swing
    lag = v.find_lag(t, flex, imu_t, own_axis)
    res = v.tracking_agreement(t, flex, imu_t, app, lag)
    assert res["lag_sec"] == pytest.approx(-0.6, abs=0.02)
    assert res["gain"] == pytest.approx(0.5, abs=0.02)
    assert v.tracking_agreement(t, flex, imu_t, app, {"status": "error"})["status"] == "error"


def test_the_apps_smoothing_delay_is_found_and_reported_not_scored_as_error():
    t, flex = _pendulum()
    imu_t = np.arange(0, t[-1] + 1.0, 0.01)
    own_axis = np.interp(imu_t - 0.6, t, flex)
    app = np.interp(imu_t - 0.6 - 0.06, t, flex)         # trails by 60 ms
    lag = v.find_lag(t, flex, imu_t, own_axis)
    fixed = v.tracking_agreement(t, flex, imu_t, app, lag)
    freed = v.tracking_agreement(t, flex, imu_t, app, lag, delay_search_s=0.15)
    assert fixed["delay_sec"] == 0.0
    assert freed["delay_sec"] == pytest.approx(0.06, abs=0.011)
    assert freed["rmse_offset_removed"] < 1.5 < fixed["rmse_offset_removed"]


def test_tracking_finds_the_lag_from_the_release_when_the_takes_are_seconds_apart():
    t, flex = _pendulum()
    imu_t = np.arange(0, t[-1] + 4.0, 0.01)
    imu_flex = np.interp(imu_t - 3.0, t, flex)           # phone started 3 s early
    res = v.tracking_agreement(t, flex, imu_t, imu_flex)
    assert res["status"] == "ok"
    assert res["lag_sec"] == pytest.approx(-3.0, abs=0.05)
    assert res["rmse"] < 1.5


def test_tracking_reports_an_error_rather_than_raising_on_a_flat_reference():
    t = np.arange(600) / 120.0
    res = v.tracking_agreement(t, np.full(600, np.nan), t, np.zeros(600))
    assert res["status"] == "error" and res["error"]


def test_pooled_tracking_widens_the_limits_for_between_trial_bias():
    def row(bias):
        return {"participant": "901", "track_shank_status": "ok", "track_shank_n": 100,
                "track_shank_zero_on_hold": True, "track_shank_delay_sec": 0.0,
                "track_shank_bias": bias, "track_shank_sd": 2.0,
                "track_shank_rmse": math.hypot(bias, 2.0), "track_shank_r": 0.99,
                "track_shank_gain": 1.0, "track_shank_rmse_offset_removed": 2.0,
                "track_shank_span_ref": 60.0, "track_shank_sum_x": 3000.0,
                "track_shank_sum_y": 3000.0 + 100 * bias, "track_shank_sum_xx": 130000.0,
                "track_shank_sum_yy": 130000.0, "track_shank_sum_xy": 129000.0}
    same = v.pooled_tracking([row(1.0), row(1.0), row(1.0)])
    spread = v.pooled_tracking([row(-5.0), row(1.0), row(7.0)])
    assert same["bias"] == pytest.approx(1.0) and spread["bias"] == pytest.approx(1.0)
    assert same["loa_hi"] - same["loa_lo"] == pytest.approx(2 * 1.96 * 2.0)
    assert spread["loa_hi"] - spread["loa_lo"] > 3 * (same["loa_hi"] - same["loa_lo"])
    assert v.pooled_tracking([row(1.0)]) is None


# ── PT score ──────────────────────────────────────────────────────────────────

def test_score_curve_scores_a_pendulum_and_is_blind_to_offset():
    t, flex = _pendulum()
    a = v.score_curve(t, flex)
    b = v.score_curve(t, flex + 25.0)
    assert a["scored"] and a["interpretable"]
    assert a["zone"] in ("healthy", "borderline", "impaired")
    assert b["pt"] == pytest.approx(a["pt"], abs=1e-6)
    assert a["A0_deg"] == pytest.approx(60.0, rel=0.2)


def test_score_curve_refuses_without_numbers_when_nothing_moves():
    t = np.arange(1200) / 120.0
    out = v.score_curve(t, np.zeros(1200))
    assert not out["interpretable"]
    assert "pt" not in out or out.get("zone") is None


def test_reason_class_carries_no_trial_numbers():
    for text in ("A0 of 412.3 deg exceeds the joint", "excursion 2.1 deg is too small",
                 "no oscillation after release (A0 31.0)"):
        cls = v._reason_class(text)
        assert cls and not any(ch.isdigit() for ch in cls)


def test_cohen_kappa_known_values():
    assert v.cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == pytest.approx(1.0)
    assert v.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == pytest.approx(0.0)
    assert np.isnan(v.cohen_kappa([], []))


def test_verdict_agreement_separates_interpretability_from_zone():
    rows = [
        {"imu_interpretable": True, "imu_zone": "healthy",
         "refit_interpretable": True, "refit_zone": "healthy"},
        {"imu_interpretable": True, "imu_zone": "borderline",
         "refit_interpretable": True, "refit_zone": "healthy"},
        {"imu_interpretable": False, "imu_zone": None,
         "refit_interpretable": True, "refit_zone": "healthy"},
        {"imu_interpretable": True, "imu_zone": "healthy"},      # optical never scored
    ]
    out = v.verdict_agreement(rows, "refit")
    assert out["n_compared"] == 3
    assert out["interpretability"] == {"both": 2, "refit_only": 1, "imu_only": 0, "neither": 0}
    assert out["n_zone"] == 2 and out["zone_agree"] == 1
    assert out["cross"] == {"healthy->borderline": 1, "healthy->healthy": 1}


def test_param_agreement_and_leg_medians():
    rows = [{"leg_key": f"9{i // 3}_left_pre", "participant": f"9{i // 3}", "group": "control",
             "imu_A0_deg": 50.0 + i, "refit_A0_deg": 52.0 + i} for i in range(9)]
    out = v.param_agreement(rows, "A0_deg", "refit")
    assert out["n"] == 9 and out["bias"] == pytest.approx(-2.0)
    legs = v.leg_medians(rows, ["imu_A0_deg", "refit_A0_deg"])
    assert [l["imu_A0_deg"] for l in legs] == [51.0, 54.0, 57.0]
    assert v.param_agreement(rows[:2], "A0_deg", "refit") is None


def test_thigh_effect_finds_a_difference_that_goes_with_thigh_movement():
    rng = np.random.default_rng(3)
    thigh = rng.uniform(2, 20, 40)
    rows = [{"thigh_span": th, "shank_A0_deg": 70.0, "refit_A0_deg": 70.0 - th,
             "imu_A0_deg": 70.0 + rng.normal(0, 0.5)} for th in thigh]
    out = v.thigh_effect(rows, "A0_deg")
    assert out["imu_minus_refit_vs_thigh"]["r2"] > 0.95
    assert out["imu_minus_shank_vs_thigh"]["r2"] < 0.2
    assert out["median_abs_imu_minus_shank"] < out["median_abs_imu_minus_refit"]


def test_unscorable_counts_reasons_without_naming_a_trial():
    rows = [
        {"participant": "901", "trial_key": "901_left_pre_T1", "optical_status": "error",
         "optical_error": "GeometryError: Plate not tracked long enough.",
         "imu_status": "ok", "imu_scored": True, "imu_interpretable": True},
        {"participant": "901", "trial_key": "901_left_pre_T2", "optical_status": "ok",
         "imu_status": "error",
         "imu_error": "ValueError: unreadable IMU component file(s): accel",
         "shank_scored": True, "shank_interpretable": False, "shank_reason": "no oscillation",
         "refit_scored": True, "refit_interpretable": True},
    ]
    out = v.unscorable(rows)
    assert out["by_source"]["optical"] == {"Plate not tracked long enough.": 1}
    assert out["by_source"]["shank"] == {"no oscillation": 1}
    assert out["interpretable_by_all"] == 0 and out["interpretable_by_none"] == 0
    assert "901" not in str(out)


# ── replay capture ────────────────────────────────────────────────────────────

def _raw_swing():
    """A solo phone held still for 2 s then swung about its x axis."""
    samples = []
    for i in range(600):
        t = i * 0.01
        rate = 0.0 if t < 2.0 else 3.0 * math.sin(2 * math.pi * 1.0 * (t - 2.0))
        ts = int(t * 1000)
        samples.append({"t": t, "role": "proximal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts})
        samples.append({"t": t, "role": "proximal", "sensor": "gyro",
                        "v": [rate, 0.0, 0.0], "phone_ts_ms": ts})
    return samples


def test_replay_capture_changes_nothing_and_exposes_the_orientation():
    import imu_calibration_config as cfg
    params = dict(cfg.REFERENCE_REPLAY_CONFIG)
    samples = _raw_swing()
    t0, a0 = tuner.replay_trial(samples, params, tick_s=tuner.ANALYSIS_TICK_S)
    cap = {}
    t1, a1 = tuner.replay_trial(samples, params, tick_s=tuner.ANALYSIS_TICK_S, capture=cap)
    assert len(t0) > 0, "the synthetic trial must zero for this test to mean anything"
    np.testing.assert_array_equal(t0, t1)
    np.testing.assert_array_equal(a0, a1)
    assert cap["quats"].shape == (len(t1), 4)
    assert cap["q_zero"].shape == (4,)
    rv = v.imu_rotvecs(cap["quats"], cap["q_zero"])
    axis, share = v.principal_axis(rv)
    assert abs(axis[0]) > 0.98 and share > 0.95


def test_replay_capture_stays_empty_when_the_trial_never_zeroes():
    cap = {}
    still = [s for s in _raw_swing() if s["t"] < 1.5]
    import imu_calibration_config as cfg
    t, a = tuner.replay_trial(still, dict(cfg.REFERENCE_REPLAY_CONFIG), capture=cap)
    assert len(t) == 0 and cap == {}


# ── app angle against sensor rotation ─────────────────────────────────────────

def test_tracking_is_split_by_how_far_the_captured_flex_axis_is_off():
    def row(err, gain):
        return {"participant": "901", "imu_flex_axis_error_deg": err,
                "track_shank_status": "ok", "track_shank_n": 100,
                "track_shank_zero_on_hold": True, "track_shank_delay_sec": 0.0, "track_shank_bias": 0.0,
                "track_shank_sd": 2.0, "track_shank_rmse": 2.0, "track_shank_r": 0.99,
                "track_shank_gain": gain, "track_shank_span_ref": 60.0,
                "track_shank_sum_x": 3000.0, "track_shank_sum_y": 3000.0,
                "track_shank_sum_xx": 130000.0, "track_shank_sum_yy": 130000.0,
                "track_shank_sum_xy": 129000.0}
    rows = [row(5.0, 1.0), row(8.0, 0.98), row(40.0, 0.7), row(50.0, 0.6),
            row(float("nan"), 0.1)]
    got = v._tracking_by_axis_error(rows)
    assert got["under_15"]["n_trials"] == 2 and got["over_30"]["n_trials"] == 2
    assert got["under_15"]["gain_median"] > 0.95 > got["over_30"]["gain_median"]
    assert got["15_to_30"] is None


# ── sensitivity to flagged takes ──────────────────────────────────────────────

def test_sensitivity_drops_only_flagged_takes_and_counts_them():
    def row(flags, zone):
        return {"participant": "901", "group": "MS", "leg_key": "901_L", "optical_flags": flags,
                "imu_interpretable": True, "shank_interpretable": True,
                "refit_interpretable": True, "imu_pt": 0.2, "shank_pt": 0.2,
                "refit_pt": 0.2, "imu_zone": zone, "shank_zone": "healthy",
                "refit_zone": "healthy"}
    rows = [row("", "healthy"), row("", "healthy"), row("low_marker_coverage", "impaired"),
            row("knee_curve_disagrees_with_plate|low_marker_coverage", "impaired")]
    got = v.sensitivity(rows)
    assert got["flag_counts"] == {"low_marker_coverage": 2,
                                  "knee_curve_disagrees_with_plate": 1}
    assert got["any_flag"] == 2
    assert got["without_any_flag"]["n_trials"] == 2
    assert got["without_knee_curve_disagrees_with_plate"]["n_trials"] == 3
    assert got["without_any_flag"]["pt"]["shank"]["verdict"]["zone_agree"] == 2
