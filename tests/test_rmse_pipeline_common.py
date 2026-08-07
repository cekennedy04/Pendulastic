import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import rmse_pipeline_common as rpc


# ── parse_structural_fields ──────────────────────────────────────────────

def test_parse_structural_fields_full_path():
    path = os.path.normpath(
        "OptiTrack_Recordings/Participant_13_left_post/Session_post/"
        "Position_1/Height_Joint-Level/trial_1_optitrack.csv")
    root = "OptiTrack_Recordings"
    fields = rpc.parse_structural_fields(path, root)
    assert fields["participant"] == "13"
    assert fields["leg"] == "left"
    assert fields["session"] == "post"
    assert fields["position"] == "1"
    assert fields["height"] == "joint-level"
    assert fields["trial_number"] == "1"


def test_parse_structural_fields_missing_position_and_height():
    # Real observed case: Participant_13_right_post's OptiTrack CSVs sit one
    # directory level higher than left_post's, with no Position_/Height_
    # segment at all -- must not fail to parse, must default those two
    # fields to a stable placeholder rather than raising or returning None.
    path = os.path.normpath(
        "OptiTrack_Recordings/Participant_13_right_post/Session_post/trial_1_optitrack.csv")
    root = "OptiTrack_Recordings"
    fields = rpc.parse_structural_fields(path, root)
    assert fields["participant"] == "13"
    assert fields["leg"] == "right"
    assert fields["session"] == "post"
    assert fields["position"] == "none"
    assert fields["height"] == "none"
    assert fields["trial_number"] == "1"


def test_parse_structural_fields_no_session_segment():
    path = os.path.normpath("Recordings/Participant_14/Left/pre/Trial_3.avi")
    root = "Recordings"
    fields = rpc.parse_structural_fields(path, root)
    assert fields["participant"] == "14"
    assert fields["leg"] == "left"
    assert fields["condition"] == "pre"
    assert fields["session"] == "none"
    assert fields["trial_number"] == "3"


def test_parse_structural_fields_no_leg_returns_none():
    path = os.path.normpath("OptiTrack_Recordings/Participant_9/trial_1_optitrack.csv")
    fields = rpc.parse_structural_fields(path, "OptiTrack_Recordings")
    assert fields is None


def test_parse_structural_fields_ambiguous_participant_returns_none():
    # Archived data can nest a stray folder from a different participant --
    # pt_report_common._parse_trial_path already treats this as unparseable;
    # match that behavior rather than guessing.
    path = os.path.normpath(
        "OptiTrack_Recordings/Participant_5/Participant_0_control/left/trial_1_optitrack.csv")
    fields = rpc.parse_structural_fields(path, "OptiTrack_Recordings")
    assert fields is None


def test_parse_structural_fields_case_insensitive_and_normalized():
    path = os.path.normpath(
        "OptiTrack_Recordings/PARTICIPANT_13_LEFT_post/SESSION_Post/Trial_2_optitrack.csv")
    fields = rpc.parse_structural_fields(path, "OptiTrack_Recordings")
    assert fields["participant"] == "13"
    assert fields["leg"] == "left"
    assert fields["session"] == "post"


# ── compute_trial_key ────────────────────────────────────────────────────

def test_compute_trial_key_deterministic():
    fields = {"participant": "13", "leg": "left", "condition": "post",
             "session": "post", "position": "1", "height": "joint-level",
             "trial_number": "1"}
    assert rpc.compute_trial_key(fields) == rpc.compute_trial_key(dict(fields))


def test_compute_trial_key_differs_on_position():
    base = {"participant": "13", "leg": "left", "condition": "post",
           "session": "post", "position": "1", "height": "joint-level",
           "trial_number": "1"}
    other = {**base, "position": "2"}
    assert rpc.compute_trial_key(base) != rpc.compute_trial_key(other)


def test_compute_trial_key_stable_under_key_order():
    fields = {"participant": "13", "leg": "left", "condition": "post",
             "session": "post", "position": "1", "height": "joint-level",
             "trial_number": "1"}
    reordered = dict(reversed(list(fields.items())))
    assert rpc.compute_trial_key(fields) == rpc.compute_trial_key(reordered)
