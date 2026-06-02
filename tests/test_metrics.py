"""Tests for pendulum metric extraction."""

import numpy as np
import pytest

from pendulastic.metrics import compute_knee_angles, extract_pendulum_metrics
from pendulastic.pose import LandmarkFrame


def _straight_leg_landmarks(n: int = 60) -> list[LandmarkFrame]:
    """Landmarks for a near-fully extended, still leg — near 180 degrees."""
    return [
        LandmarkFrame(
            frame_index=i,
            hip=np.array([0.5, 0.2]),
            knee=np.array([0.5, 0.5]),
            ankle=np.array([0.5, 0.8]),
        )
        for i in range(n)
    ]


def _oscillating_landmarks(n: int = 120, fps: float = 30.0) -> list[LandmarkFrame]:
    """Landmarks producing a damped oscillation in knee angle."""
    t = np.linspace(0, n / fps, n)
    # Knee y oscillates around midpoint to simulate pendulum motion
    knee_y = 0.5 + 0.1 * np.exp(-0.5 * t) * np.cos(2 * np.pi * 1.5 * t)
    return [
        LandmarkFrame(
            frame_index=i,
            hip=np.array([0.5, 0.2]),
            knee=np.array([0.5, float(knee_y[i])]),
            ankle=np.array([0.5, 0.8]),
        )
        for i in range(n)
    ]


def test_compute_knee_angles_shape():
    landmarks = _straight_leg_landmarks(60)
    angles = compute_knee_angles(landmarks, smooth=False)
    assert angles.shape == (60,)


def test_compute_knee_angles_no_nans():
    landmarks = _straight_leg_landmarks(30)
    angles = compute_knee_angles(landmarks, smooth=False)
    assert not np.isnan(angles).any()


def test_compute_knee_angles_with_none_frames():
    landmarks = _straight_leg_landmarks(30)
    landmarks[5] = None
    landmarks[10] = None
    angles = compute_knee_angles(landmarks, smooth=False)
    assert not np.isnan(angles).any()


def test_extract_pendulum_metrics_returns_dataclass():
    landmarks = _oscillating_landmarks()
    angles = compute_knee_angles(landmarks, smooth=False)
    metrics = extract_pendulum_metrics(angles, fps=30.0)
    assert metrics.num_oscillations >= 0
    assert 0.0 <= metrics.relaxation_index <= 1.0


def test_plateau_angle_sensible():
    landmarks = _straight_leg_landmarks(90)
    angles = compute_knee_angles(landmarks, smooth=False)
    metrics = extract_pendulum_metrics(angles, fps=30.0)
    # Near-straight leg should give plateau close to 180°
    assert 150.0 < metrics.plateau_angle_deg <= 180.0
