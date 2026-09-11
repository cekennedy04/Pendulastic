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


# ── PatientIdentityTracker ───────────────────────────────────────────────────

def _tracker(hysteresis_frames=3, confidence_floor=pit.DEFAULT_CONFIDENCE_FLOOR):
    return pit.PatientIdentityTracker(
        HIP_IDX, KNEE_IDX, ANKLE_IDX,
        hysteresis_frames=hysteresis_frames, confidence_floor=confidence_floor)


def _low_confidence_pose():
    # Near-zero horizontal score, low visibility -- geometric score well
    # below any reasonable confidence floor.
    return _pose((0.50, 0.50), (0.505, 0.90), (0.51, 0.95), (0.515, 0.99),
                  visibility=0.05)


def test_init_locks_to_higher_scoring_candidate():
    t = _tracker()
    result = t.select([ASSESSOR, PATIENT], W, H)
    assert result.pose is PATIENT
    assert result.ambiguous is False
    assert t.n_switches == 0


def test_init_order_independent():
    t = _tracker()
    result = t.select([PATIENT, ASSESSOR], W, H)
    assert result.pose is PATIENT


def test_no_poses_marks_ambiguous_without_touching_lock():
    t = _tracker()
    t.select([PATIENT, ASSESSOR], W, H)  # establish lock on PATIENT
    result = t.select([], W, H)
    assert result.pose is None
    assert result.ambiguous is True
    assert t.n_ambiguous == 1
    # Lock survived: next normal frame still tracks PATIENT's position, not
    # reset to an init-style highest-score pick.
    result2 = t.select([ASSESSOR, PATIENT], W, H)
    assert result2.pose is PATIENT


def test_single_pose_accepted_when_above_confidence_floor():
    t = _tracker()
    t.select([ASSESSOR, PATIENT], W, H)  # establish lock on PATIENT
    result = t.select([PATIENT], W, H)
    assert result.pose is PATIENT
    assert result.ambiguous is False


def test_single_low_confidence_pose_marked_ambiguous():
    t = _tracker()
    result = t.select([_low_confidence_pose()], W, H)
    assert result.ambiguous is True
    assert result.pose is None


def test_both_candidates_below_confidence_floor_marks_ambiguous():
    # No prior lock -- this exercises the init branch's ambiguous handling.
    # Two distinct low-scoring poses (same shape as _low_confidence_pose(),
    # translated so they're distinguishable objects at different positions);
    # verified score ~0.031 each, well below the default 0.35 floor.
    t = _tracker()
    low_a = _low_confidence_pose()
    low_b = _pose((0.20, 0.20), (0.205, 0.60), (0.21, 0.65), (0.215, 0.69),
                   visibility=0.05)
    result = t.select([low_a, low_b], W, H)
    assert result.ambiguous is True
    assert result.pose is None
    assert t.n_ambiguous == 1


def test_single_contradictory_frame_does_not_flip_lock():
    t = _tracker(hysteresis_frames=3)
    t.select([ASSESSOR, PATIENT], W, H)  # lock on PATIENT

    # Same positions, but visibility flipped so the pose nearest the lock
    # (still PATIENT's position) now scores lower than the challenger.
    # Verified scores: patient_weak (vis=0.0) ~0.498, assessor_strong
    # (vis=1.0) ~0.529 -- challenger genuinely outscores tracked here.
    patient_weak = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44),
                          (0.40, 0.60), visibility=0.0)
    assessor_strong = _pose((0.10, 0.20), (0.12, 0.55), (0.14, 0.75),
                             (0.16, 0.90), visibility=1.0)

    result = t.select([patient_weak, assessor_strong], W, H)
    assert result.pose is patient_weak  # still the tracked (nearest) candidate
    assert t.n_switches == 0


def test_n_minus_one_contradictory_frames_do_not_flip_lock():
    t = _tracker(hysteresis_frames=3)
    t.select([ASSESSOR, PATIENT], W, H)  # lock on PATIENT

    patient_weak = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44),
                          (0.40, 0.60), visibility=0.0)
    assessor_strong = _pose((0.10, 0.20), (0.12, 0.55), (0.14, 0.75),
                             (0.16, 0.90), visibility=1.0)

    t.select([patient_weak, assessor_strong], W, H)   # streak 1
    result = t.select([patient_weak, assessor_strong], W, H)  # streak 2
    assert result.pose is patient_weak
    assert t.n_switches == 0


def test_n_consecutive_contradictory_frames_flip_lock():
    t = _tracker(hysteresis_frames=3)
    t.select([ASSESSOR, PATIENT], W, H)  # lock on PATIENT

    patient_weak = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44),
                          (0.40, 0.60), visibility=0.0)
    assessor_strong = _pose((0.10, 0.20), (0.12, 0.55), (0.14, 0.75),
                             (0.16, 0.90), visibility=1.0)

    t.select([patient_weak, assessor_strong], W, H)   # streak 1
    t.select([patient_weak, assessor_strong], W, H)   # streak 2
    result = t.select([patient_weak, assessor_strong], W, H)  # streak 3 -> switch
    assert result.pose is assessor_strong
    assert t.n_switches == 1

    # Lock is now on the (former) assessor's position; a subsequent frame
    # with the same two poses continues tracking it without re-switching.
    result2 = t.select([patient_weak, assessor_strong], W, H)
    assert result2.pose is assessor_strong
    assert t.n_switches == 1


def test_frame_counter_increments_every_call():
    t = _tracker()
    t.select([ASSESSOR, PATIENT], W, H)
    t.select([], W, H)
    t.select([PATIENT], W, H)
    assert t.n_frames == 3


# ── Lone-detection continuity (the silent wrong-person path) ─────────────────
# When MediaPipe returns exactly one pose, the len(poses)==1 branch used to
# accept it unconditionally, re-lock onto it, and never count a switch. On
# P17 Right/post the patient goes undetected in 66% of frames while the
# assessor is still found, so 31 lone-assessor frames were accepted as the
# patient (11 of them cleared the confidence floor and were written to the
# CSV), and n_switches stayed 0 -- a false negative, not a clean run.

def test_lone_pose_far_from_lock_is_not_silently_accepted():
    t = _tracker(hysteresis_frames=3)
    t.select([PATIENT, ASSESSOR], W, H)          # lock onto the patient
    r = t.select([ASSESSOR], W, H)               # patient undetected this frame
    assert r.ambiguous is True
    assert r.pose is None


def test_lone_pose_far_from_lock_does_not_move_the_lock():
    t = _tracker(hysteresis_frames=3)
    t.select([PATIENT, ASSESSOR], W, H)
    locked = t._locked_knee_px
    t.select([ASSESSOR], W, H)
    assert t._locked_knee_px == locked


def test_lone_pose_near_the_lock_is_still_accepted():
    """The common case -- only the patient detected -- must keep working."""
    t = _tracker(hysteresis_frames=3)
    t.select([PATIENT, ASSESSOR], W, H)
    r = t.select([PATIENT], W, H)
    assert r.ambiguous is False
    assert r.pose is PATIENT


def test_persistent_lone_pose_far_from_lock_eventually_takes_over_and_counts_a_switch():
    """A genuine re-acquisition must still be possible, but it must be
    counted, not silent."""
    t = _tracker(hysteresis_frames=3)
    t.select([PATIENT, ASSESSOR], W, H)
    for _ in range(2):
        assert t.select([ASSESSOR], W, H).ambiguous is True
    r = t.select([ASSESSOR], W, H)
    assert r.ambiguous is False
    assert t.n_switches == 1
