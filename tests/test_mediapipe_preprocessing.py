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
