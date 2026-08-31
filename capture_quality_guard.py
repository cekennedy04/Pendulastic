"""Camera-placement guard for pendulum-test capture.

MediaPipe's stored knee angle is a 2D projected angle (batch_mediapipe.py
computes it from pixel coordinates), so it is only correct when the leg lies
in the image plane. When the camera looks along the thigh, hip-knee-ankle
project as near-collinear and the angle biases toward 180 deg. On P17 that
cost ~53 deg of accuracy and dropped tracking from 88-98% to 26-37%, with
the same patient minutes apart -- purely because the camera was moved.

None of that is recoverable in analysis, so it has to be caught at setup.
Two things are observable live, before a single trial is recorded:

    thigh length in pixels    -- how far away / how small the subject is
    shank/thigh length ratio  -- how square-on the view is

See tests/test_capture_quality_guard.py for the measurements the thresholds
come from.
"""
from __future__ import annotations

import math
from collections import deque, namedtuple

# P17 lateral camera measured 215 px, oblique measured 100 px. Dataset-wide,
# trials that tracked >=85% have median thigh 201 px and those under 40% have
# 113 px. 180 keeps the good population and rejects the failing one.
MIN_THIGH_PX = 180.0

# A true sagittal view puts shank/thigh near the anatomical ~0.95. P17's
# lateral camera measured 0.95, the oblique one 1.50. Either direction is an
# off-axis camera: >1 means the thigh is foreshortened, <1 the shank.
RATIO_LO = 0.75
RATIO_HI = 1.25

GuardResult = namedtuple("GuardResult", ["ok", "thigh_px", "ratio", "reasons"])


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def evaluate_leg_geometry(hip_px, knee_px, ankle_px) -> GuardResult:
    """Judge one frame's camera placement from the tracked leg's geometry.

    Points are (x, y) in PIXELS, not normalised coordinates -- the whole
    point of the scale check is that it is resolution-aware.
    """
    thigh = _dist(hip_px, knee_px)
    shank = _dist(ankle_px, knee_px)
    reasons = []

    if thigh < 1e-6:
        return GuardResult(False, 0.0, float("nan"),
                           ("No thigh visible - move so the hip and knee are "
                            "both in frame.",))

    ratio = shank / thigh

    if thigh < MIN_THIGH_PX:
        reasons.append(
            f"Subject too small ({thigh:.0f} px thigh, want >={MIN_THIGH_PX:.0f}) "
            f"- move the camera closer.")
    if ratio > RATIO_HI:
        reasons.append(
            f"Thigh foreshortened (shank/thigh {ratio:.2f}, want "
            f"{RATIO_LO:.2f}-{RATIO_HI:.2f}) - move to the side of the plinth.")
    elif ratio < RATIO_LO:
        reasons.append(
            f"Shank foreshortened (shank/thigh {ratio:.2f}, want "
            f"{RATIO_LO:.2f}-{RATIO_HI:.2f}) - move to the side of the plinth.")

    return GuardResult(not reasons, thigh, ratio, tuple(reasons))


class GuardSmoother:
    """Majority verdict over recent frames.

    MediaPipe detection flickers frame to frame near its confidence
    threshold, and a HUD that flips GOOD/BAD on single frames is worse than
    no HUD -- the operator cannot act on it. A settled verdict only changes
    when most of the window agrees.
    """

    def __init__(self, window: int = 15):
        self._window = deque(maxlen=window)

    def push(self, result: GuardResult) -> None:
        self._window.append(bool(result.ok))

    def verdict(self) -> str:
        if not self._window:
            return "UNKNOWN"
        good = sum(self._window)
        return "GOOD" if good * 2 >= len(self._window) else "BAD"

    def reset(self) -> None:
        self._window.clear()
