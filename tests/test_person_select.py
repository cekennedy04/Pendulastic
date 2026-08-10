# tests/test_person_select.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
from pendulastic_viewer import draw_person_select_overlay, resolve_person_click


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _make_pose(knee_x=0.5, ankle_vis=1.0):
    """A 33-point BlazePose-shaped landmark list with both anatomical sides
    at the same position -- adequate for tests that don't care about
    left/right mirroring, only nearest-pose search and ankle visibility."""
    lm = [_LM(0.5, 0.5) for _ in range(33)]
    lm[23] = _LM(knee_x - 0.02, 0.30)
    lm[25] = _LM(knee_x, 0.55)
    lm[27] = _LM(knee_x, 0.85, ankle_vis)
    lm[24] = _LM(knee_x - 0.02, 0.30)
    lm[26] = _LM(knee_x, 0.55)
    lm[28] = _LM(knee_x, 0.85, ankle_vis)
    return lm


def _make_pose_with_sides(left_knee_x, right_knee_x, ankle_vis=1.0):
    """Distinct anatomical-left vs -right knee x-positions, for testing the
    mirroring-aware leg-to-screen-side mapping."""
    lm = [_LM(0.5, 0.5) for _ in range(33)]
    lm[23] = _LM(left_knee_x - 0.02, 0.30)
    lm[25] = _LM(left_knee_x, 0.55)
    lm[27] = _LM(left_knee_x, 0.85, ankle_vis)
    lm[24] = _LM(right_knee_x - 0.02, 0.30)
    lm[26] = _LM(right_knee_x, 0.55)
    lm[28] = _LM(right_knee_x, 0.85, ankle_vis)
    return lm


def test_draw_person_select_overlay_draws_numbered_badges():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.3), _make_pose(0.7)]
    out = draw_person_select_overlay(frame, poses)
    assert out.shape == frame.shape
    assert not np.array_equal(out, frame)


def test_resolve_person_click_returns_none_for_no_poses():
    assert resolve_person_click([], (100, 100), 640, 480, "right") is None


def test_resolve_person_click_picks_nearest_pose():
    poses = [_make_pose(0.2), _make_pose(0.8)]
    result = resolve_person_click(poses, (0.8 * 640, 0.55 * 480), 640, 480, "right")
    assert result is not None
    hip, knee, ankle = result
    assert abs(float(knee[0]) - 0.8 * 640) < 5.0


def test_resolve_person_click_rejects_low_visibility_ankle_but_keeps_knee():
    poses = [_make_pose(0.5, ankle_vis=0.1)]
    result = resolve_person_click(poses, (0.5 * 640, 0.55 * 480), 640, 480, "right")
    assert result is not None
    hip, knee, ankle = result
    assert knee is not None
    assert ankle is None


def test_resolve_person_click_accepts_valid_ankle():
    poses = [_make_pose(0.5, ankle_vis=0.9)]
    result = resolve_person_click(poses, (0.5 * 640, 0.55 * 480), 640, 480, "right")
    assert result is not None
    hip, knee, ankle = result
    assert ankle is not None
    assert abs(float(ankle[0]) - 0.5 * 640) < 5.0


def test_resolve_person_click_maps_screen_left_when_not_mirrored():
    # anatomical left knee is on the left of the image (0.2 < 0.8).
    poses = [_make_pose_with_sides(left_knee_x=0.2, right_knee_x=0.8)]
    result = resolve_person_click(poses, (0.2 * 640, 0.55 * 480), 640, 480, "left")
    assert result is not None
    hip, knee, ankle = result
    assert abs(float(knee[0]) - 0.2 * 640) < 5.0


def test_resolve_person_click_maps_screen_left_when_mirrored():
    # anatomical left knee is on the RIGHT of the image (0.8 > 0.2) --
    # patient facing the camera, so "screen-left" is the anatomical right leg.
    poses = [_make_pose_with_sides(left_knee_x=0.8, right_knee_x=0.2)]
    result = resolve_person_click(poses, (0.8 * 640, 0.55 * 480), 640, 480, "left")
    assert result is not None
    hip, knee, ankle = result
    assert abs(float(knee[0]) - 0.2 * 640) < 5.0
