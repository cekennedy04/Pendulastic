import math
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import cv2

import mediapipe_preprocessing as mp_pre


def test_rotate_to_upright_zero_degrees_returns_frame_unchanged():
    frame = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    result = mp_pre.rotate_to_upright(frame, 0)
    assert result is frame


def test_rotate_to_upright_plus_90_matches_cv2_clockwise():
    frame = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    result = mp_pre.rotate_to_upright(frame, 90)
    expected = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    assert result.shape == (4, 2, 3)
    assert np.array_equal(result, expected)


def test_rotate_to_upright_minus_90_matches_cv2_counterclockwise():
    frame = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    result = mp_pre.rotate_to_upright(frame, -90)
    expected = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    assert result.shape == (4, 2, 3)
    assert np.array_equal(result, expected)


def test_rotate_to_upright_rejects_invalid_angle():
    frame = np.zeros((2, 4, 3), dtype=np.uint8)
    try:
        mp_pre.rotate_to_upright(frame, 45)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_knee_angle_from_points_right_angle():
    hip = np.array([0.0, 1.0])
    knee = np.array([0.0, 0.0])
    ankle = np.array([1.0, 0.0])
    angle = mp_pre.knee_angle_from_points(hip, knee, ankle)
    assert math.isclose(angle, 90.0, abs_tol=1e-6)


def test_knee_angle_from_points_straight_leg():
    hip = np.array([0.0, 1.0])
    knee = np.array([0.0, 0.0])
    ankle = np.array([0.0, -1.0])
    angle = mp_pre.knee_angle_from_points(hip, knee, ankle)
    assert math.isclose(angle, 180.0, abs_tol=1e-6)


def test_knee_angle_from_points_degenerate_zero_length_vector():
    hip = np.array([0.0, 0.0])
    knee = np.array([0.0, 0.0])
    ankle = np.array([1.0, 0.0])
    angle = mp_pre.knee_angle_from_points(hip, knee, ankle)
    assert math.isnan(angle)


def test_knee_angle_rotation_invariant_under_arbitrary_rotation():
    """Regression test for the design spec's rotation-invariance claim: the
    angle between two vectors sharing the knee as a common vertex is
    unchanged under any rotation of all three points -- checked with an
    arbitrary (non-90-degree) rotation so this isn't sensitive to getting
    cv2's specific 90-degree direction convention right or wrong."""
    hip = np.array([0.2, 0.3])
    knee = np.array([0.5, 0.5])
    ankle = np.array([0.6, 0.9])
    original = mp_pre.knee_angle_from_points(hip, knee, ankle)

    theta = math.radians(37.0)
    R = np.array([[math.cos(theta), -math.sin(theta)],
                  [math.sin(theta), math.cos(theta)]])
    hip_r, knee_r, ankle_r = R @ hip, R @ knee, R @ ankle
    rotated = mp_pre.knee_angle_from_points(hip_r, knee_r, ankle_r)

    assert math.isclose(original, rotated, abs_tol=1e-6)


def _solid_frame(h, w, value):
    return np.full((h, w, 3), value, dtype=np.uint8)


def test_find_motion_bbox_locates_high_motion_region():
    h, w = 20, 30
    base = _solid_frame(h, w, 50)
    moving = base.copy()
    moving[2:6, 22:28] = 200
    frames = [base, moving, base, moving]
    bbox = mp_pre._find_motion_bbox(frames)
    assert bbox == (22, 2, 6, 4)


def test_find_motion_bbox_none_for_static_input():
    h, w = 20, 30
    frames = [_solid_frame(h, w, 50) for _ in range(4)]
    assert mp_pre._find_motion_bbox(frames) is None


def test_find_motion_bbox_none_for_fewer_than_two_frames():
    assert mp_pre._find_motion_bbox([_solid_frame(10, 10, 0)]) is None


def test_crop_to_moving_leg_crops_around_motion_region():
    h, w = 40, 60
    fps = 10.0
    n_baseline = int(mp_pre.CROP_BASELINE_SEC * fps)  # 30 static frames
    frames = [_solid_frame(h, w, 50) for _ in range(n_baseline)]
    for i in range(10):
        frame = _solid_frame(h, w, 50)
        if i % 2 == 1:
            frame[5:15, 45:58] = 200
        frames.append(frame)

    result = mp_pre.crop_to_moving_leg(frames, fps)

    assert len(result) == len(frames)
    out_h, out_w = result[0].shape[:2]
    assert out_h < h and out_w < w


def test_crop_to_moving_leg_falls_back_when_shorter_than_baseline():
    h, w = 20, 30
    fps = 10.0
    frames = [_solid_frame(h, w, 50) for _ in range(5)]  # < CROP_BASELINE_SEC * fps
    result = mp_pre.crop_to_moving_leg(frames, fps)
    assert len(result) == len(frames)
    assert result[0].shape == frames[0].shape


def test_crop_to_moving_leg_falls_back_when_no_motion_found():
    h, w = 20, 30
    fps = 10.0
    frames = [_solid_frame(h, w, 50) for _ in range(60)]  # all static
    result = mp_pre.crop_to_moving_leg(frames, fps)
    assert len(result) == len(frames)
    assert result[0].shape == frames[0].shape


import batch_mediapipe as bm
import patient_identity_tracker as pit
import sweep_mediapipe_preprocessing as smp


class _StubTracker:
    def __init__(self, pose_to_return):
        self.calls = []
        self._pose = pose_to_return

    def select(self, poses, w, h):
        self.calls.append((poses, w, h))
        return pit.SelectionResult(self._pose, 1.0, False)


def test_select_pose_for_candidate_uses_identity_tracker_when_requested():
    poses = ["pose_a", "pose_b"]
    tracker = _StubTracker(pose_to_return="pose_b")
    result = smp._select_pose_for_candidate(
        {"key": "identity_tracker"}, tracker, poses, 640, 480)
    assert result == "pose_b"
    assert tracker.calls == [(poses, 640, 480)]


def test_select_pose_for_candidate_uses_stateless_selector_for_other_candidates(monkeypatch):
    poses = ["pose_a", "pose_b"]
    calls = []

    def _stub_select_patient_pose(p):
        calls.append(p)
        return "pose_a"

    monkeypatch.setattr(bm, "_select_patient_pose", _stub_select_patient_pose)
    tracker = _StubTracker(pose_to_return="pose_b")  # must NOT be used for this candidate

    result = smp._select_pose_for_candidate({"key": "baseline"}, tracker, poses, 640, 480)

    assert result == "pose_a"
    assert calls == [poses]
    assert tracker.calls == []
