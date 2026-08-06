import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import batch_mediapipe as bm


def test_leg_from_name_force_overrides_everything():
    assert bm._leg_from_name("Participant_13_left_pre", force="right") == "right"


def test_leg_from_name_old_convention_leg_embedded_in_top_folder():
    # e.g. Recordings/Participant_13_left_pre/Session_pre/... -- leg is part
    # of the participant folder name itself.
    assert bm._leg_from_name("Participant_13_left_pre") == "left"
    assert bm._leg_from_name("Participant_13_right_post") == "right"


def test_leg_from_name_new_convention_leg_as_subfolder():
    # e.g. Recordings/Participant_14/Left/pre/ -- leg is its own path
    # segment, not part of the "Participant_14" name -- this is the bug
    # fixed 2026-08-06: process_trial()/the dry-run summary used to pass
    # only the top-level participant folder name here, which contains
    # neither "left" nor "right" for this layout, silently defaulting every
    # trial (both legs) to "right".
    path = os.path.join("Recordings", "Participant_14", "Left", "pre")
    assert bm._leg_from_name(path) == "left"
    path = os.path.join("Recordings", "Participant_14", "Right", "pre")
    assert bm._leg_from_name(path) == "right"


def test_leg_from_name_defaults_to_right_when_no_side_info_present():
    assert bm._leg_from_name("Participant_0_control") == "right"


# ── _select_patient_pose ─────────────────────────────────────────────────────

class _Landmark:
    def __init__(self, x, y):
        self.x, self.y = x, y


def _make_pose(shoulder_mid, hip_mid):
    """33-landmark BlazePose-shaped list with only shoulders (11,12) and
    hips (23,24) populated meaningfully -- both landmarks in each pair sit
    at the same point since _select_patient_pose only uses their midpoint."""
    pose = [_Landmark(0.0, 0.0)] * 33
    pose[11] = pose[12] = _Landmark(*shoulder_mid)
    pose[23] = pose[24] = _Landmark(*hip_mid)
    return pose


def test_select_patient_pose_single_pose_returned_unchanged():
    pose = _make_pose((0.5, 0.3), (0.5, 0.6))
    assert bm._select_patient_pose([pose]) is pose


def test_select_patient_pose_empty_list_returns_none():
    assert bm._select_patient_pose([]) is None


def test_select_patient_pose_picks_horizontal_trunk_regardless_of_frame_side():
    # Reclining patient: trunk vector is mostly horizontal (large dx, small dy).
    patient = _make_pose(shoulder_mid=(0.75, 0.40), hip_mid=(0.55, 0.42))
    # Standing assessor: trunk vector is mostly vertical (small dx, large dy).
    assessor = _make_pose(shoulder_mid=(0.10, 0.20), hip_mid=(0.12, 0.55))

    # Old buggy heuristic ("furthest-left knee") would have picked whichever
    # pose is passed first if it also happens to be leftmost -- verify the
    # fix picks correctly by trunk orientation regardless of list order or
    # which side of the frame each person is on.
    assert bm._select_patient_pose([assessor, patient]) is patient
    assert bm._select_patient_pose([patient, assessor]) is patient


def test_select_patient_pose_ignores_degenerate_zero_length_trunk():
    degenerate = _make_pose((0.5, 0.5), (0.5, 0.5))   # shoulder == hip, mag=0
    patient = _make_pose(shoulder_mid=(0.75, 0.40), hip_mid=(0.55, 0.42))
    assert bm._select_patient_pose([degenerate, patient]) is patient
