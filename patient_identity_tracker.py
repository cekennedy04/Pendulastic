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
# A tracked knee cannot teleport. Expressed as a fraction of the frame
# diagonal so it scales with resolution: 0.15 of an 800 px diagonal is 120 px,
# generous against a swinging knee (tens of px per frame at 30 fps) but well
# under the ~248 px that separates patient from assessor in a typical frame.
DEFAULT_MAX_LOCK_JUMP_FRAC = 0.15
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


SelectionResult = namedtuple("SelectionResult", ["pose", "score", "ambiguous"])


class PatientIdentityTracker:
    """Stateful per-trial identity tracker. One instance per trial video."""

    def __init__(self, hip_idx, knee_idx, ankle_idx,
                 hysteresis_frames: int = DEFAULT_HYSTERESIS_FRAMES,
                 confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
                 max_lock_jump_frac: float = DEFAULT_MAX_LOCK_JUMP_FRAC):
        self._hip_idx = hip_idx
        self._knee_idx = knee_idx
        self._ankle_idx = ankle_idx
        self._hysteresis_frames = hysteresis_frames
        self._confidence_floor = confidence_floor
        self._max_lock_jump_frac = max_lock_jump_frac
        self._locked_knee_px = None
        self._challenger_streak = 0
        self.n_switches = 0
        self.n_ambiguous = 0
        self.n_frames = 0

    def _knee_px(self, pose, w, h):
        lm = pose[self._knee_idx]
        return (lm.x * w, lm.y * h)

    def _geometric_score(self, pose, w, h) -> float:
        horiz = _trunk_horizontal_score(pose)
        vis = _visibility_score(pose, self._hip_idx, self._knee_idx, self._ankle_idx)
        anat = _anatomical_penalty(pose, self._hip_idx, self._knee_idx,
                                    self._ankle_idx, w, h)
        return ((horiz + vis) / 2.0) * anat

    def select(self, poses, w, h) -> "SelectionResult":
        self.n_frames += 1

        if not poses:
            self.n_ambiguous += 1
            return SelectionResult(None, 0.0, True)

        if len(poses) == 1:
            pose = poses[0]
            score = self._geometric_score(pose, w, h)
            if score < self._confidence_floor:
                self.n_ambiguous += 1
                return SelectionResult(None, score, True)
            # A lone detection is not automatically the patient. When the
            # patient goes undetected but the assessor is still found, this
            # branch used to accept the assessor, re-lock onto them, and
            # count no switch -- so the CSV got assessor limbs labelled as
            # patient data and n_switches still read 0. Hold the lone pose to
            # the same continuity + hysteresis rule the two-pose branch uses:
            # it must stay near the lock, or persist long enough to count as a
            # real re-acquisition.
            if self._locked_knee_px is not None:
                knee = self._knee_px(pose, w, h)
                jump = math.hypot(knee[0] - self._locked_knee_px[0],
                                  knee[1] - self._locked_knee_px[1])
                if jump > self._max_lock_jump_frac * math.hypot(w, h):
                    self._challenger_streak += 1
                    if self._challenger_streak < self._hysteresis_frames:
                        self.n_ambiguous += 1
                        return SelectionResult(None, score, True)
                    self.n_switches += 1
            self._challenger_streak = 0
            self._locked_knee_px = self._knee_px(pose, w, h)
            return SelectionResult(pose, score, False)

        # len(poses) == 2: MediaPipe options cap num_poses at 2 upstream.
        if self._locked_knee_px is None:
            scored = sorted(
                ((self._geometric_score(p, w, h), p) for p in poses),
                key=lambda t: t[0], reverse=True,
            )
            best_score, best_pose = scored[0]
            if best_score < self._confidence_floor:
                self.n_ambiguous += 1
                return SelectionResult(None, best_score, True)
            self._challenger_streak = 0
            self._locked_knee_px = self._knee_px(best_pose, w, h)
            return SelectionResult(best_pose, best_score, False)

        dists = sorted(
            ((math.hypot(*(a - b for a, b in
                           zip(self._knee_px(p, w, h), self._locked_knee_px))), p)
             for p in poses),
            key=lambda t: t[0],
        )
        tracked_pose, challenger_pose = dists[0][1], dists[1][1]
        tracked_score = self._geometric_score(tracked_pose, w, h)
        challenger_score = self._geometric_score(challenger_pose, w, h)

        if challenger_score > tracked_score:
            self._challenger_streak += 1
        else:
            self._challenger_streak = 0

        if self._challenger_streak >= self._hysteresis_frames:
            selected, score = challenger_pose, challenger_score
            self._challenger_streak = 0
            self.n_switches += 1
        else:
            selected, score = tracked_pose, tracked_score

        if score < self._confidence_floor:
            self.n_ambiguous += 1
            return SelectionResult(None, score, True)

        self._locked_knee_px = self._knee_px(selected, w, h)
        return SelectionResult(selected, score, False)
