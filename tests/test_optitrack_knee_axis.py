import math

import numpy as np
import pytest
import optitrack_knee_axis as ka

try:
    from tests.test_optitrack_marker_angle import _rot
except ImportError:
    def _rot(axis, deg):
        axis = np.asarray(axis, float); axis = axis / np.linalg.norm(axis)
        th = math.radians(deg); c, s = math.cos(th), math.sin(th)
        K = np.array([[0, -axis[2], axis[1]],
                      [axis[2], 0, -axis[0]],
                      [-axis[1], axis[0], 0]])
        return np.eye(3) * c + s * K + (1 - c) * np.outer(axis, axis)


def _tri(n=100):
    """(3, n, 3) triangle cluster: real out-of-plane extent."""
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    return np.repeat(base[:, None, :], n, axis=1)


def _bar(n=100):
    """(3, n, 3) near-collinear cluster, 1.2 mm out of line."""
    base = np.array([[0.046, 0.0, 0.0], [-0.046, 0.0, 0.0], [0.0, 0.0012, 0.0]])
    return np.repeat(base[:, None, :], n, axis=1)


def test_classify_detects_by_planar_extent_not_marker_count():
    """Both clusters have THREE markers. Counting them would misclassify every
    real trial, because the thigh bar is a 3-marker cluster 1.5 mm out of line."""
    tri, bar, which = ka.classify_clusters(_tri(), _bar())
    assert which == "a_is_triangle"
    assert tri.shape == bar.shape == (3, 100, 3)


def test_classify_handles_the_reversed_rig_automatically():
    """15 of 254 trials are shank-bar / thigh-triangle. No caller should have
    to know that."""
    _tri_out, _bar_out, which = ka.classify_clusters(_bar(), _tri())
    assert which == "b_is_triangle"


def test_classify_refuses_when_neither_cluster_is_a_triangle():
    with pytest.raises(ka.GeometryError) as exc:
        ka.classify_clusters(_bar(), _bar())
    assert "collinear" in str(exc.value).lower()


def test_line_direction_is_sign_continuous_through_an_index_swap():
    """SVD returns +/-v arbitrarily per frame, and Motive permutes marker
    indices on re-solve. Without continuity the direction flips 180 deg and
    the angle spikes. This is the transient that must NOT spike."""
    n = 120
    mk = np.zeros((3, n, 3))
    for i in range(n):
        mk[0, i] = [0.046, 0.0, 0.0]
        mk[1, i] = [-0.046, 0.0, 0.0]
        mk[2, i] = [0.0, 0.0012, 0.0]
    mk[[0, 1], 60] = mk[[1, 0], 60]          # 1-frame index swap
    dirs = ka.segment_line_direction(mk)
    steps = np.degrees(np.arccos(np.clip(
        np.sum(dirs[1:] * dirs[:-1], axis=1), -1, 1)))
    assert np.nanmax(steps) < 5.0, f"direction flipped: max step {np.nanmax(steps)}"


def test_line_direction_is_nan_where_untracked():
    mk = np.zeros((3, 50, 3))
    mk[0, :] = [0.046, 0.0, 0.0]; mk[1, :] = [-0.046, 0.0, 0.0]
    mk[2, :] = [0.0, 0.0012, 0.0]
    mk[:, 20:25] = np.nan
    dirs = ka.segment_line_direction(mk)
    assert np.isnan(dirs[20:25]).all()
    assert np.isfinite(dirs[30]).all()


def _rotating_triangle(n, axis, deg_per_frame, wobble_deg=0.0, wobble_hz=0.0, fps=120.0):
    """Triangle rotating about `axis`, optionally wobbling about a perpendicular."""
    from pendulastic_pt_score import _shortest_arc_rotation
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    axis = np.asarray(axis, float); axis = axis / np.linalg.norm(axis)
    # Cross against a fixed reference degenerates when `axis` IS that
    # reference (true for every hinge test here, which rotates about z).
    # Pick whichever fixed axis is least parallel to `axis` instead.
    ref_vec = [0.0, 0.0, 1.0] if abs(axis[2]) < 0.9 else [1.0, 0.0, 0.0]
    perp = np.cross(axis, ref_vec)
    perp = perp / np.linalg.norm(perp)
    out = np.empty((3, n, 3))
    for i in range(n):
        R = _rot(axis, deg_per_frame * i)
        if wobble_deg:
            R = _rot(perp, wobble_deg * np.sin(2 * np.pi * wobble_hz * i / fps)) @ R
        out[:, i, :] = base @ R.T
    return out


def test_hinge_axis_recovers_a_known_rotation_axis():
    mk = _rotating_triangle(300, [0.0, 0.0, 1.0], 0.4)
    axis, cond, _pc2 = ka.hinge_axis(mk)
    assert abs(abs(float(np.dot(axis, [0, 0, 1]))) - 1.0) < 0.02, axis
    assert cond > 0.95, cond


def test_hinge_conditioning_falls_when_the_plate_tumbles():
    """Rotation spread across axes is not a hinge, and conditioning must say so."""
    mk = _rotating_triangle(300, [0.0, 0.0, 1.0], 0.4, wobble_deg=12.0, wobble_hz=2.0)
    _axis, cond, _pc2 = ka.hinge_axis(mk)
    assert cond < 0.95, cond


def test_low_freq_ratio_separates_slow_motion_from_jitter():
    fps, n = 120.0, 600
    t = np.arange(n) / fps
    slow = np.sin(2 * np.pi * 1.0 * t)
    fast = np.random.default_rng(0).normal(size=n)
    assert ka.low_freq_ratio(slow, fps) > 0.9
    assert ka.low_freq_ratio(fast, fps) < 0.4


def test_verdict_refuses_jitter_but_keeps_real_out_of_plane_motion():
    """A single conditioning cut would refuse 9 of 30 measured trials, and 2 of
    those are real limb motion -- biased toward unusual movement, which is
    where spasticity lives."""
    fps, n = 120.0, 600
    t = np.arange(n) / fps
    slow = np.sin(2 * np.pi * 1.0 * t)
    fast = np.random.default_rng(0).normal(size=n)
    assert ka.conditioning_verdict(0.97, fast, fps) == "ok"
    assert ka.conditioning_verdict(0.70, fast, fps) == "ill_conditioned_axis"
    assert ka.conditioning_verdict(0.70, slow, fps) == "out_of_plane_motion"


def test_verdict_refuses_a_series_too_short_to_have_a_spectrum():
    short = np.sin(np.arange(50) / 5.0)
    assert ka.conditioning_verdict(0.70, short, 120.0) == "ill_conditioned_axis"


def test_signed_angle_does_not_fold_past_180():
    """The defect this replaces: an unsigned arccos mirrors at 180, so an
    angle continuing past it reads as coming back down."""
    n = 200
    hinge = np.array([0.0, 0.0, 1.0])
    thigh = np.repeat(np.array([[1.0, 0.0, 0.0]]), n, axis=0)
    sweep = np.linspace(170.0, 200.0, n)
    shank = np.stack([_rot(hinge, a) @ np.array([1.0, 0.0, 0.0]) for a in sweep])
    ang = ka.signed_knee_angle(thigh, shank, hinge)
    assert np.all(np.diff(ang) > 0), "angle folded instead of continuing"
    assert ang[-1] - ang[0] == pytest.approx(30.0, abs=1.0)


def test_signed_angle_is_nan_where_either_direction_is_missing():
    n = 40
    hinge = np.array([0.0, 0.0, 1.0])
    thigh = np.repeat(np.array([[1.0, 0.0, 0.0]]), n, axis=0)
    shank = np.repeat(np.array([[0.0, 1.0, 0.0]]), n, axis=0)
    shank[10:15] = np.nan
    ang = ka.signed_knee_angle(thigh, shank, hinge)
    assert np.isnan(ang[10:15]).all()
    assert np.isfinite(ang[20])


def test_absolute_angles_raise_when_the_offset_was_never_established():
    r = ka.KneeAngleResult(raw_angles=np.array([10.0, 20.0, 30.0]),
                           is_calibrated=False, offset_deg=None,
                           conditioning=0.97, low_freq_ratio=0.1,
                           flags=("uncalibrated_offset",))
    with pytest.raises(ka.UncalibratedOffsetError):
        r.get_absolute_angles()
    rel = r.get_relative_angles()
    assert rel[0] == 0.0 and rel[2] == 20.0


def test_absolute_angles_also_refuse_a_low_confidence_hold():
    r = ka.KneeAngleResult(raw_angles=np.array([1.0, 2.0]), is_calibrated=True,
                           offset_deg=5.0, conditioning=0.97, low_freq_ratio=0.1,
                           flags=("low_confidence_hold",))
    with pytest.raises(ka.UncalibratedOffsetError):
        r.get_absolute_angles()


def test_result_has_no_innocuous_angles_attribute():
    """A plain .angles would let a consumer reach an absolute curve without
    saying so. The escape hatch is named raw_angles, so its use is visible in
    a diff."""
    r = ka.KneeAngleResult(raw_angles=np.array([1.0]), is_calibrated=True,
                           offset_deg=0.0, conditioning=0.97,
                           low_freq_ratio=0.1, flags=())
    assert not hasattr(r, "angles")
    assert r.get_absolute_angles()[0] == 1.0
