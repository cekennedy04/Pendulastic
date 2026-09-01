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


def test_relative_angles_baseline_on_the_first_FINITE_value_not_index_zero():
    """A leading NaN is normal: the clusters are often untracked at the start.
    Subtracting a[0] blindly would make the whole curve NaN, and no existing
    test would notice."""
    r = ka.KneeAngleResult(raw_angles=np.array([np.nan, np.nan, 10.0, 25.0]),
                           is_calibrated=False, offset_deg=None,
                           conditioning=0.97, low_freq_ratio=0.1, flags=())
    rel = r.get_relative_angles()
    assert np.isnan(rel[0]) and np.isnan(rel[1])
    assert rel[2] == 0.0, "baseline must be the first FINITE sample"
    assert rel[3] == 15.0


def test_relative_angles_survive_an_all_nan_curve():
    r = ka.KneeAngleResult(raw_angles=np.array([np.nan, np.nan]),
                           is_calibrated=False, offset_deg=None,
                           conditioning=0.5, low_freq_ratio=0.1, flags=())
    rel = r.get_relative_angles()
    assert rel.shape == (2,) and np.isnan(rel).all()


def test_segment_axis_from_plate_tracks_rotation_and_preserves_gaps():
    """Its output feeds signed_knee_angle, so a silently wrong or gap-filled
    axis would corrupt every angle downstream."""
    n = 120
    hinge = np.array([0.0, 0.0, 1.0])
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    tri = np.empty((3, n, 3))
    for i in range(n):
        tri[:, i, :] = base @ _rot(hinge, 0.5 * i).T
    tri[:, 40:45, :] = np.nan                      # untracked gap
    dirs = ka.segment_axis_from_plate(tri, hinge)
    assert np.isnan(dirs[40:45]).all(), "a gap must stay a gap, never be filled"
    assert np.isfinite(dirs[60]).all()
    # the axis must actually rotate with the plate, not sit still
    swept = np.degrees(np.arccos(np.clip(
        float(np.dot(dirs[0], dirs[100])), -1.0, 1.0)))
    assert swept > 30.0, f"axis barely moved ({swept:.1f} deg) with a 50 deg plate rotation"


def test_a_drifting_hold_withholds_the_offset():
    """Patients shift during the hold, which drifts the reference rather than
    stepping it. A drifting hold must not be used to set an absolute zero."""
    ang = np.concatenate([np.linspace(0.0, 9.0, 80), np.linspace(9.0, 60.0, 220)])
    offset, flags = ka.anchor_to_extension(ang, slice(0, 80))
    assert offset is None
    assert "low_confidence_hold" in flags


def test_a_steady_hold_sets_the_offset_so_extension_reads_180():
    ang = np.concatenate([np.full(80, 4.0), np.linspace(4.0, 60.0, 220)])
    offset, flags = ka.anchor_to_extension(ang, slice(0, 80))
    assert offset == pytest.approx(176.0, abs=0.5)
    assert flags == ()


def test_no_hold_at_all_is_reported_not_guessed():
    offset, flags = ka.anchor_to_extension(np.linspace(0, 60, 300), None)
    assert offset is None
    assert "uncalibrated_offset" in flags


def test_orchestrator_recovers_a_known_flexion_from_a_bar_and_triangle():
    try:
        from tests.test_optitrack_marker_angle import _build_trial
    except ImportError:
        # A site-packages "tests" package (unrelated to this repo) shadows
        # the local tests/ namespace package on this machine. Fall back to
        # the top-level module name pytest's own collection already put on
        # sys.path -- same workaround as the _rot import above.
        from test_optitrack_marker_angle import _build_trial
    rows, truth = _build_trial(n=400, hold=80, flex_deg=45.0, thigh_as_bar=True)
    shank = np.stack([r[2] for r in rows], axis=1)     # (3, n, 3)
    thigh = np.stack([r[3] for r in rows], axis=1)
    res = ka.knee_angle_from_clusters(shank, thigh, fps=120.0)
    rel = res.get_relative_angles()
    swept_true = abs(truth[-1] - truth[0])
    swept_got = abs(np.nanmax(rel) - np.nanmin(rel))
    assert swept_got == pytest.approx(swept_true, rel=0.15), (swept_got, swept_true)


def test_orchestrator_refuses_an_ill_conditioned_trial_with_a_named_reason():
    n = 400
    rng = np.random.default_rng(1)
    tumbling = rng.normal(scale=0.05, size=(3, n, 3))
    bar = np.repeat(np.array([[0.046, 0, 0], [-0.046, 0, 0], [0, 0.0012, 0]])[:, None, :],
                    n, axis=1)
    with pytest.raises(ka.GeometryError) as exc:
        ka.knee_angle_from_clusters(tumbling, bar, fps=120.0)
    assert "conditioned" in str(exc.value).lower() or "hinge" in str(exc.value).lower()


def test_orchestrator_emits_and_flags_out_of_plane_motion_instead_of_refusing():
    """Real non-sagittal limb motion is a finding, not a defect: the angle must
    still be produced, flagged as a lower bound. Refusing here would discard
    exactly the trials with unusual movement, which is where spasticity lives."""
    import numpy as np
    n = 600
    hinge = np.array([0.0, 0.0, 1.0])
    perp = np.array([1.0, 0.0, 0.0])
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    tri = np.empty((3, n, 3))
    for i in range(n):
        # a hinge sweep plus a SLOW out-of-plane wobble: low-frequency, so it
        # is real limb motion rather than marker jitter
        R = _rot(perp, 9.0 * np.sin(2 * np.pi * 1.0 * i / 120.0)) @ _rot(hinge, 0.25 * i)
        tri[:, i, :] = base @ R.T
    bar = np.repeat(np.array([[0.046, 0.0, 0.0], [-0.046, 0.0, 0.0],
                              [0.0, 0.0012, 0.0]])[:, None, :], n, axis=1)
    res = ka.knee_angle_from_clusters(tri, bar, fps=120.0)   # must NOT raise
    assert "out_of_plane_motion" in res.flags, res.flags
    assert "OUT_OF_PLANE_AMPLITUDE_UNDERREPORTED" in res.flags, res.flags
    assert np.isfinite(res.get_relative_angles()).any(), "angles must still be produced"


def test_offset_invariance_of_every_scored_parameter():
    """The claim the whole design rests on, tested rather than argued.

    If this fails, an unknown offset DOES reach the score and the decision to
    demote 180-is-extended to presentation was wrong."""
    import pendulastic_pt_score as pt
    t = np.arange(1200) / 120.0
    ts = np.maximum(t - 1.0, 0.0)
    ang = 130.0 + 50.0 * np.exp(-ts / 3.0) * np.cos(2 * np.pi * 0.9 * ts)
    base = pt.compute_pt_params(t, ang)
    for off in (-37.0, -5.0, 12.5, 88.0):
        shifted = pt.compute_pt_params(t, ang + off)
        assert shifted is not None
        for k in pt._PARAM_KEYS:
            assert shifted[k] == pytest.approx(base[k], rel=1e-6), (k, off)
        assert shifted["A0_deg"] == pytest.approx(base["A0_deg"], rel=1e-6)


def test_mirror_invariance_of_every_scored_parameter():
    """The second claim the design rests on, and since 2026-09-01 a
    load-bearing one rather than an incidental measurement.

    The hinge axis is an eigenvector, so its sign is arbitrary: the same trial
    can reconstruct as +40 deg or -40 deg depending on numerical noise. That is
    tolerable ONLY because a mirrored curve scores identically. If this fails,
    the decision to emit a relative curve of arbitrary polarity was wrong and
    the sign must be pinned before any optical angle is scored."""
    import pendulastic_pt_score as pt
    t = np.arange(1200) / 120.0
    ts = np.maximum(t - 1.0, 0.0)
    ang = 130.0 + 50.0 * np.exp(-ts / 3.0) * np.cos(2 * np.pi * 0.9 * ts)
    base = pt.compute_pt_params(t, ang)
    assert base is not None

    for label, mirrored in (("about the baseline", 2.0 * ang[0] - ang),
                            ("negated", -ang),
                            ("180 - ang", 180.0 - ang)):
        got = pt.compute_pt_params(t, mirrored)
        assert got is not None, label
        for k in pt._PARAM_KEYS:
            assert got[k] == pytest.approx(base[k], rel=1e-6), (k, label)
        assert got["A0_deg"] == pytest.approx(base["A0_deg"], rel=1e-6), label

    # The assertion must be capable of failing: a curve that is NOT a mirror
    # of the original has to score differently, or this proves nothing.
    other = pt.compute_pt_params(t, 130.0 + 50.0 * np.exp(-ts / 8.0)
                                 * np.cos(2 * np.pi * 0.5 * ts))
    assert other is not None
    assert other["f"] != pytest.approx(base["f"], rel=1e-6)


def test_the_orchestrator_never_reports_a_calibrated_result():
    """find_hold/anchor_to_extension are retained but disconnected: their gate,
    abs(ang - 180) <= 25, is an ABSOLUTE test on a quantity this module's own
    docstrings call arbitrary. Measured: a 5e-7 m CSV rounding difference
    flipped one trial from (uncalibrated, 0 -> -40) to (calibrated, 180 ->
    +220) against a truth of 180 -> 140."""
    n = 600
    hinge = np.array([0.0, 0.0, 1.0])
    base = np.array([[0.06, 0.0, 0.0], [-0.06, 0.0, 0.0], [0.0, 0.021, 0.0]])
    tri = np.empty((3, n, 3))
    for i in range(n):
        tri[:, i, :] = base @ _rot(hinge, 0.05 * i).T
    bar = np.repeat(np.array([[0.046, 0.0, 0.0], [-0.046, 0.0, 0.0],
                              [0.0, 0.0012, 0.0]])[:, None, :], n, axis=1)
    res = ka.knee_angle_from_clusters(tri, bar, fps=120.0)
    assert res.is_calibrated is False
    assert res.offset_deg is None
    assert "uncalibrated_offset" in res.flags, res.flags
    with pytest.raises(ka.UncalibratedOffsetError):
        res.get_absolute_angles()
    # the helpers themselves still work, so a future sign fix can re-enable them
    off, flags = ka.anchor_to_extension(np.full(80, 4.0), slice(0, 80))
    assert off == pytest.approx(176.0, abs=0.5) and flags == ()


def test_out_of_plane_branch_is_reachable():
    """Two real positives cannot keep a branch honest. A dialled-in
    out-of-plane trial must actually reach out_of_plane_motion, or the branch
    is dead code the way the quadriceps-catch merge was."""
    fps, n = 120.0, 600
    t = np.arange(n) / fps
    slow_pc2 = np.sin(2 * np.pi * 1.0 * t)
    assert ka.conditioning_verdict(0.80, slow_pc2, fps) == "out_of_plane_motion"
    assert ka.low_freq_ratio(slow_pc2, fps) >= ka.OUT_OF_PLANE_MIN_LF_RATIO


def test_a_blackout_at_release_does_not_spike_the_angle():
    try:
        from tests.test_optitrack_marker_angle import _build_trial
    except ImportError:
        from test_optitrack_marker_angle import _build_trial
    rows, _truth = _build_trial(n=400, hold=80, thigh_as_bar=True,
                                drop_from=80, drop_to=90)
    shank = np.stack([r[2] if not r[4] else np.full((3, 3), np.nan)
                      for r in rows], axis=1)
    thigh = np.stack([r[3] if not r[4] else np.full((3, 3), np.nan)
                      for r in rows], axis=1)
    res = ka.knee_angle_from_clusters(shank, thigh, fps=120.0)
    rel = res.get_relative_angles()
    steps = np.abs(np.diff(rel[np.isfinite(rel)]))
    assert np.nanmax(steps) < 20.0, f"spiked {np.nanmax(steps)} deg across the gap"


def test_a_marker_index_swap_at_peak_velocity_does_not_spike_the_angle():
    """The swap must land where the limb is moving FASTEST.

    Motive permutes Marker1/2/3 when it re-solves a cluster, and SVD's sign is
    arbitrary; a flip there is a 180 deg error. At low velocity the continuity
    check has an easy job, so a swap in the settled tail proves almost nothing.
    """
    try:
        from tests.test_optitrack_marker_angle import _build_trial
    except ImportError:
        from test_optitrack_marker_angle import _build_trial
    SWAP = 85                      # just after release at hold=80: peak velocity
    rows, truth = _build_trial(n=400, hold=80, thigh_as_bar=True, swap_frame=SWAP)

    # The fixture must actually be near peak velocity at SWAP, or this test
    # quietly becomes the easy case again.
    step = np.abs(np.diff(truth))
    assert step[SWAP - 1] > 0.5 * step.max(), (
        f"frame {SWAP} is not near peak velocity: "
        f"{step[SWAP - 1]:.3f} deg/frame vs peak {step.max():.3f}")

    shank = np.stack([r[2] for r in rows], axis=1)
    thigh = np.stack([r[3] for r in rows], axis=1)
    res = ka.knee_angle_from_clusters(shank, thigh, fps=120.0)
    rel = res.get_relative_angles()
    steps = np.abs(np.diff(rel[np.isfinite(rel)]))
    assert np.nanmax(steps) < 20.0, f"index swap spiked {np.nanmax(steps):.1f} deg"
