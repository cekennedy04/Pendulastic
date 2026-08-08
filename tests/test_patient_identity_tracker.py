import math
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import patient_identity_tracker as pit

# BlazePose indices used across these tests (right leg, matches
# mediapipe_worker.py's MP_R_HIP/MP_R_KNEE/MP_R_ANKLE = 24, 26, 28).
HIP_IDX, KNEE_IDX, ANKLE_IDX = 24, 26, 28
W, H = 640, 480


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _pose(shoulder_mid, hip_mid, knee_pt, ankle_pt, visibility=1.0):
    """33-landmark BlazePose-shaped list. Shoulders (11,12) and hips (23,24)
    are set to the same midpoint (only the midpoint is used for trunk
    orientation, matching batch_mediapipe.py's _select_patient_pose
    convention). hip/knee/ankle at HIP_IDX/KNEE_IDX/ANKLE_IDX are also set
    for the anatomical/visibility scoring under test."""
    pose = [_LM(0.0, 0.0, 0.0)] * 33
    pose[11] = pose[12] = _LM(*shoulder_mid, visibility)
    pose[23] = pose[24] = _LM(*hip_mid, visibility)
    pose[HIP_IDX] = _LM(*hip_mid, visibility)
    pose[KNEE_IDX] = _LM(*knee_pt, visibility)
    pose[ANKLE_IDX] = _LM(*ankle_pt, visibility)
    return pose


PATIENT = _pose(shoulder_mid=(0.75, 0.40), hip_mid=(0.55, 0.42),
                 knee_pt=(0.45, 0.44), ankle_pt=(0.40, 0.60))
ASSESSOR = _pose(shoulder_mid=(0.10, 0.20), hip_mid=(0.12, 0.55),
                  knee_pt=(0.14, 0.75), ankle_pt=(0.16, 0.90))


def test_trunk_horizontal_score_high_for_reclining_patient():
    assert pit._trunk_horizontal_score(PATIENT) > 0.9


def test_trunk_horizontal_score_low_for_standing_assessor():
    assert pit._trunk_horizontal_score(ASSESSOR) < 0.2


def test_trunk_horizontal_score_zero_for_degenerate_zero_length_trunk():
    degenerate = _pose((0.5, 0.5), (0.5, 0.5), (0.5, 0.6), (0.5, 0.7))
    assert pit._trunk_horizontal_score(degenerate) == 0.0


def test_visibility_score_averages_hip_knee_ankle():
    pose = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44), (0.40, 0.60),
                  visibility=0.6)
    assert math.isclose(
        pit._visibility_score(pose, HIP_IDX, KNEE_IDX, ANKLE_IDX), 0.6)


def test_anatomical_penalty_full_score_for_plausible_ratio():
    assert pit._anatomical_penalty(
        PATIENT, HIP_IDX, KNEE_IDX, ANKLE_IDX, W, H) == 1.0


def test_anatomical_penalty_reduced_for_implausible_ratio():
    # Ankle placed 20x the thigh length away from the knee -- an anatomically
    # impossible shank, e.g. a hallucinated detection.
    bad = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44), (0.45, 3.44))
    assert pit._anatomical_penalty(
        bad, HIP_IDX, KNEE_IDX, ANKLE_IDX, W, H) == pit.ANATOMICAL_PENALTY


def test_anatomical_penalty_handles_zero_length_thigh():
    zero_thigh = _pose((0.75, 0.40), (0.55, 0.42), (0.55, 0.42), (0.40, 0.60))
    assert pit._anatomical_penalty(
        zero_thigh, HIP_IDX, KNEE_IDX, ANKLE_IDX, W, H) == pit.ANATOMICAL_PENALTY
