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
# If the largest connected motion component still covers more than this
# fraction of the frame area, treat it the same as "no clear motion region
# found" -- a component that big is not a localized moving leg, it's most of
# the frame (e.g. camera shake, lighting flicker, or a genuinely degenerate
# input), and cropping to it would not meaningfully isolate anything.
MOTION_BBOX_MAX_FRAME_FRACTION = 0.80


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


def _find_motion_bbox(frames):
    """Bounding box (x, y, w, h) in pixel coordinates of the largest
    connected region of motion across `frames` (BGR or grayscale numpy
    arrays, len(frames) >= 2 required). A pixel counts as "moving" when its
    mean absolute frame-to-frame grayscale difference exceeds
    MOTION_DIFF_THRESHOLD.

    This is a genuine connected-component selection, not the global
    min/max extent of every above-threshold pixel: a small morphological
    open first rejects isolated noise pixels, then
    cv2.connectedComponentsWithStats() labels the remaining thresholded
    mask and the single largest-area foreground component (by
    cv2.CC_STAT_AREA) is used to build the box, via its
    CC_STAT_LEFT/TOP/WIDTH/HEIGHT stats -- so a small, isolated moving leg
    is not conflated with unrelated scattered motion (an assessor's hand,
    background clutter, sensor noise) elsewhere in the frame.

    Returns None if no pixel exceeds the threshold, fewer than 2 frames are
    given, or the largest connected component still covers more than
    MOTION_BBOX_MAX_FRAME_FRACTION of the frame area -- that last case is
    treated the same as "no clear motion region found" (never guesses a
    box)."""
    if len(frames) < 2:
        return None
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f
            for f in frames]
    accum = np.zeros(gray[0].shape, dtype=np.float64)
    for a, b in zip(gray[:-1], gray[1:]):
        accum += np.abs(a.astype(np.float64) - b.astype(np.float64))
    mean_motion = accum / (len(frames) - 1)
    mask = (mean_motion > MOTION_DIFF_THRESHOLD).astype(np.uint8)
    if not np.any(mask):
        return None

    kernel = np.ones((3, 3), np.uint8)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    if not np.any(opened):
        return None

    n_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(opened)
    if n_labels < 2:
        # Only the background label (0) survived -- no foreground component.
        return None

    # Label 0 is always background; pick the largest-area label from 1..N-1.
    areas = stats[1:, cv2.CC_STAT_AREA]
    largest_label = int(np.argmax(areas)) + 1
    x = int(stats[largest_label, cv2.CC_STAT_LEFT])
    y = int(stats[largest_label, cv2.CC_STAT_TOP])
    w = int(stats[largest_label, cv2.CC_STAT_WIDTH])
    h = int(stats[largest_label, cv2.CC_STAT_HEIGHT])

    frame_area = mask.shape[0] * mask.shape[1]
    if (w * h) > MOTION_BBOX_MAX_FRAME_FRACTION * frame_area:
        return None

    return (x, y, w, h)


def crop_to_moving_leg(frames, fps):
    """Crop every frame in `frames` (BGR numpy arrays, one full trial's
    worth) to a padded bounding box around the region with the most motion,
    skipping a fixed CROP_BASELINE_SEC leading window (a fixed time-based
    skip on raw pixels, NOT a call into pt._detect_release() -- that
    function needs an already-extracted scalar angle signal, which doesn't
    exist yet at this stage). Falls back to the original, uncropped frames
    (never raises) when the trial is shorter than the baseline window or no
    clear motion region is found."""
    if not frames:
        return list(frames)
    baseline_skip = int(round(CROP_BASELINE_SEC * fps))
    working = frames[baseline_skip:]
    if len(working) < 2:
        return list(frames)
    bbox = _find_motion_bbox(working)
    if bbox is None:
        return list(frames)
    x, y, w, h = bbox
    frame_h, frame_w = frames[0].shape[:2]
    pad_x = int(round(w * CROP_PAD_FRACTION))
    pad_y = int(round(h * CROP_PAD_FRACTION))
    x0 = max(0, x - pad_x)
    y0 = max(0, y - pad_y)
    x1 = min(frame_w, x + w + pad_x)
    y1 = min(frame_h, y + h + pad_y)
    return [f[y0:y1, x0:x1] for f in frames]
