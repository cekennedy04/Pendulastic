"""mediapipe_preprocessing.py
=============================
Pure frame/array preprocessing helpers for the MediaPipe HPE preprocessing
experiment (see docs/superpowers/specs/2026-08-11-mediapipe-hpe-preprocessing-
design.md). No MediaPipe or video I/O here -- every function takes
already-loaded frame arrays / landmark points and returns transformed
arrays, so this module is unit-testable with synthetic numpy data alone.
"""
from __future__ import annotations

import cv2
import numpy as np

CROP_BASELINE_SEC = 3.0
CROP_PAD_FRACTION = 0.20
MOTION_DIFF_THRESHOLD = 15.0


def rotate_to_upright(frame, angle_deg):
    """Rotate a BGR frame (H, W, 3) by a fixed angle before MediaPipe
    inference, so a reclined patient's torso reads closer to the upright
    orientation BlazePose is mostly trained on. angle_deg must be 0, 90, or
    -90 -- 0 returns the frame unchanged (no copy); +/-90 dispatch to
    cv2.rotate with the matching direction constant."""
    if angle_deg == 0:
        return frame
    if angle_deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if angle_deg == -90:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"angle_deg must be 0, 90, or -90, got {angle_deg!r}")


def knee_angle_from_points(hip_px, knee_px, ankle_px):
    """Knee-flexion angle in degrees: the angle at the knee vertex between
    the hip->knee and ankle->knee vectors, computed in pixel space (not
    MediaPipe's per-axis-normalized [0,1] coordinates) so the metric is
    genuinely rotation-invariant -- normalized coordinates are scaled
    independently by frame width and height, which would NOT be invariant
    under a 90-degree frame rotation that swaps width and height. Returns
    nan if either vector is degenerate (zero length)."""
    hip_px = np.asarray(hip_px, dtype=float)
    knee_px = np.asarray(knee_px, dtype=float)
    ankle_px = np.asarray(ankle_px, dtype=float)
    v1 = hip_px - knee_px
    v2 = ankle_px - knee_px
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    cos_a = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_a)))
