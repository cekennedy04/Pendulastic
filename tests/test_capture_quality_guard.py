"""Guards camera placement at capture time.

Thresholds here are not invented: they come from measuring P17, where the
same patient was recorded minutes apart with the camera in two positions.
Left (lateral to the plinth) tracked at 88-98% coverage and put the resting
knee at 110.5 deg against an OptiTrack truth of 119.8. Right (moved to the
foot/oblique side) tracked at 26-37% and reported 171.7 deg against 118.8 --
a ~53 deg projection error, because hip-knee-ankle project as near-collinear
when the camera looks along the thigh.

The two observable symptoms at setup time:
    thigh length   left 215 px   right 100 px
    shank/thigh    left 0.95     right 1.50

Dataset-wide the same split holds: trials tracking <40% have median thigh
113 px, trials tracking >=85% have 201 px. It follows camera placement per
session, not which leg is being tested.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import capture_quality_guard as guard


def _leg(thigh_px, ratio):
    """hip/knee/ankle laid out along one axis with the requested lengths."""
    hip = (100.0, 100.0)
    knee = (100.0, 100.0 + thigh_px)
    ankle = (100.0, 100.0 + thigh_px + thigh_px * ratio)
    return hip, knee, ankle


def test_p17_left_lateral_camera_passes():
    r = guard.evaluate_leg_geometry(*_leg(215.0, 0.95))
    assert r.ok is True
    assert r.reasons == ()


def test_p17_right_oblique_camera_fails_on_both_counts():
    r = guard.evaluate_leg_geometry(*_leg(100.0, 1.50))
    assert r.ok is False
    joined = " ".join(r.reasons).lower()
    assert "closer" in joined or "small" in joined     # scale problem
    assert "foreshorten" in joined or "side" in joined  # projection problem
    assert len(r.reasons) == 2


def test_subject_too_far_is_reported_even_when_the_view_is_square_on():
    r = guard.evaluate_leg_geometry(*_leg(90.0, 1.0))
    assert r.ok is False
    assert len(r.reasons) == 1


def test_foreshortened_view_is_reported_even_at_a_good_working_distance():
    r = guard.evaluate_leg_geometry(*_leg(240.0, 1.6))
    assert r.ok is False
    assert len(r.reasons) == 1


def test_ratio_below_range_also_counts_as_foreshortened():
    """shank/thigh far under 1.0 means the shank is the foreshortened
    segment -- equally an off-axis camera, not a pass."""
    r = guard.evaluate_leg_geometry(*_leg(240.0, 0.45))
    assert r.ok is False


def test_measurements_are_reported_even_when_the_frame_fails():
    r = guard.evaluate_leg_geometry(*_leg(100.0, 1.50))
    assert round(r.thigh_px) == 100
    assert round(r.ratio, 2) == 1.50


def test_degenerate_zero_length_thigh_does_not_raise():
    r = guard.evaluate_leg_geometry((10.0, 10.0), (10.0, 10.0), (10.0, 60.0))
    assert r.ok is False
    assert r.reasons


def test_verdict_is_stable_across_a_short_run_of_frames():
    """The HUD must not flicker between GOOD and BAD on borderline noise, so
    the guard exposes a smoothed verdict over recent frames."""
    s = guard.GuardSmoother(window=5)
    for _ in range(5):
        s.push(guard.evaluate_leg_geometry(*_leg(215.0, 0.95)))
    assert s.verdict() == "GOOD"
    # one bad frame must not flip a settled good verdict
    s.push(guard.evaluate_leg_geometry(*_leg(100.0, 1.5)))
    assert s.verdict() == "GOOD"


def test_sustained_bad_frames_do_flip_the_verdict():
    s = guard.GuardSmoother(window=5)
    for _ in range(5):
        s.push(guard.evaluate_leg_geometry(*_leg(215.0, 0.95)))
    for _ in range(5):
        s.push(guard.evaluate_leg_geometry(*_leg(100.0, 1.5)))
    assert s.verdict() == "BAD"


def test_smoother_reports_unknown_before_any_frames():
    assert guard.GuardSmoother(window=5).verdict() == "UNKNOWN"


# ── Live HUD drawing (must never crash a running capture session) ────────────

def _blank(w=640, h=480):
    import numpy as np
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_guard_panel_draws_for_a_failing_frame_without_raising():
    import live_tracker
    res = guard.evaluate_leg_geometry(*_leg(100.0, 1.5))
    frame = _blank()
    live_tracker._draw_guard_panel(frame, res, "BAD")
    assert frame.any(), "panel drew nothing"


def test_guard_panel_handles_no_detection():
    """No leg detected yet -- the panel still has to render, not blow up."""
    import live_tracker
    frame = _blank()
    live_tracker._draw_guard_panel(frame, None, "UNKNOWN")
    assert frame.any()


def test_guard_panel_handles_degenerate_nan_ratio():
    import live_tracker
    res = guard.evaluate_leg_geometry((10.0, 10.0), (10.0, 10.0), (10.0, 60.0))
    frame = _blank()
    live_tracker._draw_guard_panel(frame, res, "BAD")
    assert frame.any()
