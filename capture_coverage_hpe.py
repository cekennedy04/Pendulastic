"""
capture_coverage_hpe.py
=======================
The same pre-flight and live coverage check, driven by pose estimation on the
webcam instead of Motive's rigid bodies, for sessions with no mocap connection.

READ THIS FIRST: it is not a substitute for the marker check
------------------------------------------------------------
Pose estimation on the RGB webcam **cannot tell you whether the OptiTrack
markers are visible to the OptiTrack cameras**. They are different sensors, in
different places, seeing by different physics -- retroreflective IR from eight
or more angles against one visible-light view from wherever the tripod is. A
leg perfectly framed in the webcam can have its markers entirely occluded to
the mocap volume, and the reverse is just as possible.

So this does not "correct" or approximate marker coverage. What it does is
check the modality you are actually going to rely on. When Motive is not
connected, the video IS the measurement -- `mediapipe_worker.py` derives the
knee angle from exactly these landmarks -- and the same failure applies to it:
if the leg is not continuously visible, the trial cannot be scored, and that is
only fixable while the participant is still on the plinth.

The mapping to the marker check
-------------------------------
`capture_coverage`'s decision logic is deliberately modality-agnostic: it
consumes a stream of per-frame "was each segment observable" flags and knows
nothing about where they came from. The two segments map onto landmarks the
obvious way, since a segment needs both of its endpoints:

    thigh observable  <->  hip AND knee visible
    shank observable  <->  knee AND ankle visible

which keeps the verdict's "which segment is being lost" message meaningful --
a shank lost to the assessor's arm reads the same either way.

Thresholds follow `mediapipe_worker.py` rather than inventing new ones: its
`--score-thresh` default of 0.5 on per-landmark visibility, and its
biomechanical gate rejecting a shank that is not between 0.4x and 2.5x the
thigh, which is what catches an ankle hallucinated onto the floor when the
therapist's hands cover the real one.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

# MediaPipe Pose landmark indices. Written as integers for the same reason
# src/pendulastic/pose.py now does: mp.solutions was removed in the installed
# 0.10.x, and these are part of the published topology.
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28

# Per-landmark visibility a joint must clear to count as seen. Matches
# mediapipe_worker.py's --score-thresh default; deliberately not re-tuned here,
# so the check and the pipeline it is checking agree on what "visible" means.
MIN_VISIBILITY = 0.5

# A shank must be between these multiples of the thigh's pixel length. From
# mediapipe_worker.py's own gate: it rejects an ankle hallucinated onto the
# floor, which happens when the assessor's hands cover the real one -- exactly
# the situation this check exists to catch, so it must not be scored as a good
# frame.
MIN_SHANK_THIGH_RATIO = 0.4
MAX_SHANK_THIGH_RATIO = 2.5

# Below this the thigh is too small on screen for the ratio test to mean
# anything -- the participant is far away or mostly out of frame.
MIN_THIGH_PIXELS = 20.0


def _xy(landmark) -> Tuple[float, float]:
    return float(getattr(landmark, "x", 0.0)), float(getattr(landmark, "y", 0.0))


def _vis(landmark) -> float:
    return float(getattr(landmark, "visibility", 0.0))


def _dist(a, b) -> float:
    (ax, ay), (bx, by) = _xy(a), _xy(b)
    return math.hypot(ax - bx, ay - by)


def segments_observable(landmarks: Sequence, leg: str = "left",
                        width: float = 1.0, height: float = 1.0,
                        min_visibility: float = MIN_VISIBILITY
                        ) -> Tuple[bool, bool]:
    """(thigh_observable, shank_observable) for one pose.

    `landmarks` is a MediaPipe pose landmark list. `width`/`height` scale the
    normalised coordinates into pixels for the biomechanical gate; leave them
    at 1.0 to work in normalised units, which is fine because the gate is a
    RATIO and MIN_THIGH_PIXELS is then interpreted in the same units.
    """
    if landmarks is None or len(landmarks) <= max(R_ANKLE, R_HIP):
        return False, False
    side = (leg or "left").strip().lower()
    hip_i, knee_i, ank_i = ((L_HIP, L_KNEE, L_ANKLE) if side == "left"
                            else (R_HIP, R_KNEE, R_ANKLE))
    hip, knee, ankle = landmarks[hip_i], landmarks[knee_i], landmarks[ank_i]

    hip_ok = _vis(hip) >= min_visibility
    knee_ok = _vis(knee) >= min_visibility
    ankle_ok = _vis(ankle) >= min_visibility

    thigh_px = _dist(hip, knee) * max(width, height)
    shank_px = _dist(knee, ankle) * max(width, height)

    # A segment needs both endpoints. The ratio gate then applies only to the
    # shank, since that is the one an unseen ankle corrupts, and only when the
    # thigh is big enough on screen for the comparison to mean anything.
    thigh_observable = hip_ok and knee_ok
    shank_observable = knee_ok and ankle_ok
    if shank_observable and thigh_px > MIN_THIGH_PIXELS:
        ratio = shank_px / thigh_px if thigh_px else 0.0
        if not (MIN_SHANK_THIGH_RATIO <= ratio <= MAX_SHANK_THIGH_RATIO):
            shank_observable = False
    return thigh_observable, shank_observable


def pick_patient(poses: Sequence, leg: str = "left") -> Optional[Sequence]:
    """Choose the participant's pose when more than one person is detected.

    The assessor is in frame for most of a trial -- they are holding the ankle.
    `mediapipe_worker.py` resolves this by taking the pose whose knee sits
    furthest LEFT in the image, because the assessor stands to the right of the
    plinth; the same convention is used here so the check and the pipeline
    agree on who is being measured. A session shot from the other side would
    need this inverted, which is why it is one named function rather than a
    condition buried in a loop.
    """
    if not poses:
        return None
    if len(poses) == 1:
        return poses[0]
    side = (leg or "left").strip().lower()
    knee_i = L_KNEE if side == "left" else R_KNEE

    def knee_x(pose):
        if pose is None or len(pose) <= knee_i:
            return float("inf")
        return _xy(pose[knee_i])[0]

    return min(poses, key=knee_x)


def frame_observable(poses: Sequence, leg: str = "left",
                     width: float = 1.0, height: float = 1.0
                     ) -> Tuple[bool, bool]:
    """(thigh, shank) observability for a whole detection result.

    Returns (False, False) when nobody was detected, which is what an empty
    frame means for this purpose and is the same thing `capture_coverage` does
    with a missing rigid body.
    """
    patient = pick_patient(poses, leg=leg)
    if patient is None:
        return False, False
    return segments_observable(patient, leg=leg, width=width, height=height)
