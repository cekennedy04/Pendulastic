"""Markerless pose estimation using MediaPipe Pose."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

import mediapipe as mp
import numpy as np

# MediaPipe landmark indices relevant to the pendulum test
_POSE = mp.solutions.pose
HIP_LEFT = _POSE.PoseLandmark.LEFT_HIP.value
HIP_RIGHT = _POSE.PoseLandmark.RIGHT_HIP.value
KNEE_LEFT = _POSE.PoseLandmark.LEFT_KNEE.value
KNEE_RIGHT = _POSE.PoseLandmark.RIGHT_KNEE.value
ANKLE_LEFT = _POSE.PoseLandmark.LEFT_ANKLE.value
ANKLE_RIGHT = _POSE.PoseLandmark.RIGHT_ANKLE.value


@dataclass
class LandmarkFrame:
    """Normalised [0, 1] landmark coordinates for a single frame."""

    frame_index: int
    hip: np.ndarray        # shape (2,) — (x, y)
    knee: np.ndarray
    ankle: np.ndarray
    visibility: dict[str, float] = field(default_factory=dict)


def extract_landmarks(
    frames: Generator[np.ndarray, None, None],
    side: str = "right",
    min_detection_confidence: float = 0.5,
    min_tracking_confidence: float = 0.5,
) -> list[LandmarkFrame | None]:
    """Run MediaPipe Pose on each frame and extract lower-limb landmarks.

    Args:
        frames: Generator of RGB frames.
        side: Which leg to track — "left" or "right".
        min_detection_confidence: MediaPipe detection confidence threshold.
        min_tracking_confidence: MediaPipe tracking confidence threshold.

    Returns:
        List of LandmarkFrame objects (or None where pose was not detected).
    """
    if side not in ("left", "right"):
        raise ValueError(f"side must be 'left' or 'right', got '{side}'")

    hip_idx = HIP_LEFT if side == "left" else HIP_RIGHT
    knee_idx = KNEE_LEFT if side == "left" else KNEE_RIGHT
    ankle_idx = ANKLE_LEFT if side == "left" else ANKLE_RIGHT

    results: list[LandmarkFrame | None] = []

    with _POSE.Pose(
        static_image_mode=False,
        model_complexity=1,
        min_detection_confidence=min_detection_confidence,
        min_tracking_confidence=min_tracking_confidence,
    ) as pose:
        for i, frame in enumerate(frames):
            result = pose.process(frame)
            if result.pose_landmarks is None:
                results.append(None)
                continue

            lm = result.pose_landmarks.landmark
            results.append(
                LandmarkFrame(
                    frame_index=i,
                    hip=np.array([lm[hip_idx].x, lm[hip_idx].y]),
                    knee=np.array([lm[knee_idx].x, lm[knee_idx].y]),
                    ankle=np.array([lm[ankle_idx].x, lm[ankle_idx].y]),
                    visibility={
                        "hip": lm[hip_idx].visibility,
                        "knee": lm[knee_idx].visibility,
                        "ankle": lm[ankle_idx].visibility,
                    },
                )
            )

    return results
