"""Tests for pose estimation utilities."""

import numpy as np
import pytest

from pendulastic.pose import LandmarkFrame


def test_landmark_frame_fields():
    frame = LandmarkFrame(
        frame_index=0,
        hip=np.array([0.5, 0.3]),
        knee=np.array([0.5, 0.5]),
        ankle=np.array([0.5, 0.7]),
        visibility={"hip": 0.99, "knee": 0.98, "ankle": 0.97},
    )
    assert frame.frame_index == 0
    assert frame.hip.shape == (2,)
    assert frame.visibility["knee"] == pytest.approx(0.98)


def test_extract_landmarks_invalid_side():
    from pendulastic.pose import extract_landmarks
    with pytest.raises(ValueError, match="side must be"):
        extract_landmarks(iter([]), side="center")
