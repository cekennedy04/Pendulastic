"""Stateful per-trial patient-vs-assessor identity tracking for
batch_mediapipe.py. See docs/superpowers/specs/2026-08-07-mediapipe-patient-
identity-tracking-design.md for the full design rationale.
"""
from __future__ import annotations

import math
from collections import namedtuple

_SHOULDER_IDX = (11, 12)
_HIP_IDX = (23, 24)

DEFAULT_HYSTERESIS_FRAMES = 5
DEFAULT_CONFIDENCE_FLOOR = 0.35
ANATOMICAL_MIN_RATIO = 0.4
ANATOMICAL_MAX_RATIO = 2.5
ANATOMICAL_PENALTY = 0.3


def _trunk_horizontal_score(pose) -> float:
    """1.0 = perfectly horizontal shoulder-to-hip vector (reclining), 0.0 =
    perfectly vertical (standing/sitting) or degenerate (zero-length)."""
    l_sh, r_sh = pose[_SHOULDER_IDX[0]], pose[_SHOULDER_IDX[1]]
    l_hp, r_hp = pose[_HIP_IDX[0]], pose[_HIP_IDX[1]]
    dx = (l_sh.x + r_sh.x) / 2.0 - (l_hp.x + r_hp.x) / 2.0
    dy = (l_sh.y + r_sh.y) / 2.0 - (l_hp.y + r_hp.y) / 2.0
    mag = math.hypot(dx, dy)
    if mag < 1e-6:
        return 0.0
    return abs(dx) / mag


def _visibility_score(pose, hip_idx, knee_idx, ankle_idx) -> float:
    vis = [float(getattr(pose[i], "visibility", 0.0))
           for i in (hip_idx, knee_idx, ankle_idx)]
    return sum(vis) / 3.0


def _anatomical_penalty(pose, hip_idx, knee_idx, ankle_idx, w, h) -> float:
    """1.0 if the shank/thigh pixel-length ratio is human-plausible,
    ANATOMICAL_PENALTY (a soft down-weight, not a hard reject) otherwise."""
    hip = (pose[hip_idx].x * w, pose[hip_idx].y * h)
    knee = (pose[knee_idx].x * w, pose[knee_idx].y * h)
    ankle = (pose[ankle_idx].x * w, pose[ankle_idx].y * h)
    thigh = math.hypot(knee[0] - hip[0], knee[1] - hip[1])
    if thigh < 1e-6:
        return ANATOMICAL_PENALTY
    shank = math.hypot(ankle[0] - knee[0], ankle[1] - knee[1])
    ratio = shank / thigh
    if ANATOMICAL_MIN_RATIO <= ratio <= ANATOMICAL_MAX_RATIO:
        return 1.0
    return ANATOMICAL_PENALTY
