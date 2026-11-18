"""Tests for optitrack_loader_compare: the optics-only yardsticks and the
loader-against-loader statistics. All synthetic; no recording is read."""
import math
import shutil

import numpy as np
import pytest

import optitrack_knee_axis as ka
import optitrack_loader_compare as lc


def _rot(axis, deg):
    axis = np.asarray(axis, float)
    axis = axis / np.linalg.norm(axis)
    th = math.radians(deg)
    c, s = math.cos(th), math.sin(th)
    K = np.array([[0, -axis[2], axis[1]],
                  [axis[2], 0, -axis[0]],
                  [-axis[1], axis[0], 0]])
    return np.eye(3) * c + s * K + (1 - c) * np.outer(axis, axis)


# Near-isosceles, like the real plates (85.0/85.6/159.2 mm on one rig).
_BASE = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.003, 0.021, 0.0]])
_BAR = np.array([[0.046, 0.0, 0.0], [-0.046, 0.0, 0.0], [0.0, 0.0012, 0.0]])


def _swing(n=600, axis=(0.0, 0.0, 1.0), amp_deg=50.0, fps=120.0):
    """A plate that holds still for 1 s then swings as a damped pendulum.
    Returns (cluster, angle_deg)."""
    t = np.arange(n) / fps
    ang = np.where(t < 1.0, 0.0,
                   amp_deg * (1.0 - np.exp(-(t - 1.0)) * np.cos(2 * np.pi * (t - 1.0))))
    out = np.empty((3, n, 3))
    for i in range(n):
        out[:, i, :] = _BASE @ _rot(axis, ang[i]).T + [0.0, 0.0, 1.0]
    return out, ang


def _noisy(cluster, sd_m=0.0003, seed=1):
    """Marker noise at the level measured on the real rig (0.3 mm).

    The relabel defect needs it to show. Three markers are planar, so on
    noise-free data a swapped frame is fitted by an EXACT 180 degree turn, and
    an exact half turn has no extractable rotation vector: the increment code
    reads it as zero. Any out-of-plane noise makes it 180 minus a hair, and it
    then enters the sum at full size.
    """
    return cluster + np.random.default_rng(seed).normal(0.0, sd_m, cluster.shape)


def _swap(cluster, frames):
    """Swap markers 0 and 1 on the given frames, as Motive does on a re-solve."""
    out = cluster.copy()
    out[[0, 1], frames[:, None], :] = out[[1, 0], frames[:, None], :]
    return out


def test_plate_rotation_recovers_a_known_swing():
    mk, ang = _swing()
    got = lc.plate_rotation_deg(mk)
    assert np.nanmax(np.abs(got - np.abs(ang))) < 1e-6


def test_plate_rotation_does_not_care_which_marker_is_which():
    """A relabelled frame must not read as rotation: these plates are
    near-isosceles, so a 0<->1 swap looks like a 180 degree turn to a
    fixed-order fit."""
    mk, ang = _swing()
    rng = np.random.default_rng(0)
    frames = np.sort(rng.choice(np.arange(5, 600), size=90, replace=False))
    got = lc.plate_rotation_deg(_swap(mk, frames))
    assert np.nanmax(np.abs(got - np.abs(ang))) < 1e-6


def test_plate_rotation_keeps_gaps_as_nan_and_starts_at_the_first_tracked_frame():
    mk, ang = _swing()
    mk[:, :10, :] = np.nan
    mk[1, 300:320, :] = np.nan
    got = lc.plate_rotation_deg(mk)
    assert np.isnan(got[:10]).all() and np.isnan(got[300:320]).all()
    assert got[10] == pytest.approx(0.0, abs=1e-9)
    assert np.isfinite(got[400])


def test_plate_rotation_survives_a_gap_the_shank_swung_more_than_90_degrees_through():
    """Seen on the corpus: the examiner's hands hide the plate through the
    first fall, and a frame whose long edge is carried by continuity comes
    back reversed, reading +/-180."""
    n = 400
    ang = np.concatenate([np.zeros(100), np.linspace(0, 120, 100), np.full(200, 120.0)])
    mk = np.empty((3, n, 3))
    for i in range(n):
        mk[:, i, :] = _BASE @ _rot([0, 0, 1], ang[i]).T
    mk[:, 95:205, :] = np.nan
    got = lc.plate_rotation_deg(_noisy(mk))
    assert np.nanmax(np.abs(got[:95])) < 3.0
    assert np.nanmedian(got[205:]) == pytest.approx(120.0, abs=2.0)


def test_bar_rotation_measures_how_far_the_thigh_line_turned():
    n = 200
    bar = np.empty((3, n, 3))
    for i in range(n):
        bar[:, i, :] = _BAR @ _rot([0, 0, 1], 12.0 * i / (n - 1)).T
    got = lc.bar_rotation_deg(bar)
    assert got[0] == pytest.approx(0.0, abs=1e-9)
    assert got[-1] == pytest.approx(12.0, abs=1e-3)


def test_swing_axis_recovers_the_axis_despite_relabelled_frames():
    axis_true = np.array([0.2, 0.1, 1.0])
    axis_true = axis_true / np.linalg.norm(axis_true)
    mk, _ = _swing(axis=axis_true)
    frames = np.arange(150, 600, 37)
    axis, share = lc.swing_axis(_swap(_noisy(mk), frames))
    assert abs(float(axis @ axis_true)) > 0.999
    assert share > 0.99


def test_hinge_diagnostics_is_quiet_on_a_clean_swing():
    mk, _ = _swing()
    bar = np.repeat(_BAR[:, None, :], mk.shape[1], axis=1)
    d = lc.hinge_diagnostics(mk, bar)
    assert d["hinge_axis_error_deg"] < 2.0
    assert d["impossible_steps"] == 0
    assert d["impossible_step_energy_share"] == 0.0


def test_hinge_diagnostics_exposes_an_axis_captured_by_relabelled_frames():
    """The defect this script reports on the real corpus, reproduced: a handful
    of relabelled frames outweigh the whole swing in hinge_axis's sum of
    squared increments, and its axis lands on the plate's swap axis, roughly
    perpendicular to the hinge."""
    mk, _ = _swing()
    bar = np.repeat(_BAR[:, None, :], mk.shape[1], axis=1)
    d = lc.hinge_diagnostics(_swap(_noisy(mk), np.arange(150, 600, 37)), bar)
    assert d["impossible_steps"] > 0
    assert d["impossible_step_energy_share"] > 0.9
    assert d["hinge_axis_error_deg"] > 60.0


def test_current_with_axis_reproduces_the_swing_about_the_true_axis():
    mk, ang = _swing()
    bar = np.repeat(_BAR[:, None, :], mk.shape[1], axis=1)
    got = lc.current_with_axis(mk, bar, np.array([0.0, 0.0, 1.0]))
    fit = lc.shape_fit(ang, got)
    assert abs(fit["gain"] - 1.0) < 0.01
    assert fit["resid_fitted"] < 0.5


def test_shape_fit_reads_sign_gain_and_residual():
    x = np.sin(np.linspace(0, 20, 500)) * 40.0
    fit = lc.shape_fit(x, -0.5 * x + 17.0)
    assert fit["sign"] == -1
    assert fit["gain"] == pytest.approx(0.5, abs=1e-9)
    assert fit["resid_fitted"] == pytest.approx(0.0, abs=1e-9)
    assert fit["resid_gain1"] == pytest.approx(float(np.std(0.5 * x)), rel=1e-6)


def test_shape_fit_ignores_nan_and_declines_a_flat_curve():
    x = np.linspace(0, 1, 100)
    y = 2.0 * x
    y[::7] = np.nan
    assert lc.shape_fit(x, y)["gain"] == pytest.approx(2.0, abs=1e-9)
    flat = lc.shape_fit(x, np.zeros(100))
    assert flat["sign"] == 0 and np.isnan(flat["gain"])


def test_span_is_robust_to_a_single_spike():
    y = np.concatenate([np.linspace(0, 60, 1000), [900.0]])
    assert 55.0 < lc.span_deg(y) < 62.0
    assert np.isnan(lc.span_deg(np.array([np.nan, 1.0])))


def test_summary_counts_and_never_carries_a_participant_id():
    rows = [
        {"participant": "901", "legacy_status": "ok", "current_status": "ok",
         "current_mirrored_vs_legacy": True, "gain_current_over_legacy": 0.5,
         "hinge_axis_error_deg": 85.0, "impossible_steps": 4,
         "legacy_imu_orientation": "as_given"},
        {"participant": "902", "legacy_status": "ok", "current_status": "ok",
         "current_mirrored_vs_legacy": False, "gain_current_over_legacy": 1.0,
         "hinge_axis_error_deg": 5.0, "impossible_steps": 0,
         "legacy_imu_orientation": "as_given"},
        {"participant": "902", "legacy_status": "error", "current_status": "ok"},
    ]
    s = lc.summarise(rows)
    assert s["n_trials"] == 3 and s["n_participants"] == 2
    assert s["both_ok"] == 2 and s["current_mirrored_vs_legacy"] == 1
    assert s["gain_current_over_legacy_in_band"] == 1
    assert s["hinge_axis_error_over_30"] == 1
    assert s["trials_with_impossible_steps"] == 1
    assert s["legacy_imu_orientation"] == {"as_given": 2}
    text = "\n".join(lc.format_summary(s))
    assert "901" not in text and "902" not in text


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git history")
def test_legacy_module_is_the_seeded_loader_and_leaves_main_untouched():
    import pendulastic_pt_score as pt
    try:
        mod = lc.load_legacy_module()
    except Exception as exc:                            # shallow clone in CI
        pytest.skip(f"legacy revision not available: {exc}")
    assert hasattr(mod, "_angle_from_labeled_markers")
    assert not hasattr(pt, "_angle_from_labeled_markers")
    assert mod is lc.load_legacy_module()
