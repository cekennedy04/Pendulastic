"""Markerless pose estimation using MediaPipe Pose."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

import mediapipe as mp
import numpy as np

# MediaPipe landmark indices relevant to the pendulum test.
#
# Written as the documented integers rather than read off mp.solutions.pose.
# That attribute is the LEGACY MediaPipe API and was removed in the 0.10.x
# series -- against the installed 0.10.35 this module raised AttributeError at
# IMPORT time, which took the whole pendulastic package down with it and stopped
# tests/test_pose.py and tests/test_metrics.py even being collected, all for a
# handful of constants. requirements.txt asks for mediapipe>=0.10.14, so the
# breaking version is inside the range the project declares it supports.
#
# The indices themselves are part of MediaPipe's published Pose topology and
# have not changed across the API migration; mediapipe_worker.py hardcodes the
# same values (MP_L_HIP, MP_L_KNEE, MP_L_ANKLE = 23, 25, 27).
HIP_LEFT, KNEE_LEFT, ANKLE_LEFT = 23, 25, 27
HIP_RIGHT, KNEE_RIGHT, ANKLE_RIGHT = 24, 26, 28


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

    legacy_pose = getattr(getattr(mp, "solutions", None), "pose", None)
    if legacy_pose is None:
        raise RuntimeError(
            "This function uses MediaPipe's legacy mp.solutions.pose API, which "
            f"was removed in the installed mediapipe {getattr(mp, '__version__', '?')}. "
            "Use mediapipe_worker.py instead -- it runs the current PoseLandmarker "
            "tasks API against models/mediapipe/*.task. The landmark constants and "
            "LandmarkFrame in this module remain usable."
        )

    with legacy_pose.Pose(
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
