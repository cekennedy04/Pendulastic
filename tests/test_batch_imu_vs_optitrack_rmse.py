import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import batch_imu_vs_optitrack_rmse as batch


# ── derive_component_paths ──────────────────────────────────────────────────

def test_derive_component_paths_replaces_imu_suffix_with_siblings():
    imu_path = os.path.join("Recordings", "Participant_13_right_post",
                            "Session_post", "Position_1", "Height_Joint-Level",
                            "Trial_3_imu.csv")
    paths = batch.derive_component_paths(imu_path)
    base = os.path.join("Recordings", "Participant_13_right_post",
                        "Session_post", "Position_1", "Height_Joint-Level")
    assert paths["imu"] == imu_path
    assert paths["accel"] == os.path.join(base, "Trial_3_accel.csv")
    assert paths["gyro"] == os.path.join(base, "Trial_3_gyro.csv")
    assert paths["mag"] == os.path.join(base, "Trial_3_mag.csv")


def test_derive_component_paths_rejects_non_imu_anchor():
    with pytest.raises(ValueError):
        batch.derive_component_paths(os.path.join("some", "dir", "Trial_3_accel.csv"))


# ── find_optitrack_match: fully-mirrored directory depth ───────────────────

def test_find_optitrack_match_fully_mirrored_depth(tmp_path):
    """Mirrors the real Participant_13_left_post layout: the OptiTrack CSV
    sits at the exact same relative depth as the IMU anchor."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_13_left_post" / "Session_post" / \
        "Position_1" / "Height_Joint-Level"
    imu_dir.mkdir(parents=True)
    imu_path = imu_dir / "Trial_2_imu.csv"
    imu_path.write_text("")

    opti_dir = opti_root / "Participant_13_left_post" / "Session_post" / \
        "Position_1" / "Height_Joint-Level"
    opti_dir.mkdir(parents=True)
    opti_file = opti_dir / "trial_2_optitrack.csv"
    opti_file.write_text("")

    match = batch.find_optitrack_match(str(imu_path), str(rec_root), str(opti_root))
    assert match == str(opti_file)


# ── find_optitrack_match: shallower directory depth (real right_post case) ─

def test_find_optitrack_match_shallower_depth(tmp_path):
    """Mirrors the real Participant_13_right_post layout: the OptiTrack CSV
    sits one directory level higher than the fully-mirrored guess (directly
    under Session_post/, not Session_post/Position_1/Height_Joint-Level/) --
    confirmed by direct inspection of the real data on disk. The matcher
    must still find it by walking upward toward rec_root."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_13_right_post" / "Session_post" / \
        "Position_1" / "Height_Joint-Level"
    imu_dir.mkdir(parents=True)
    imu_path = imu_dir / "Trial_1_imu.csv"
    imu_path.write_text("")

    opti_dir = opti_root / "Participant_13_right_post" / "Session_post"
    opti_dir.mkdir(parents=True)
    opti_file = opti_dir / "trial_1_optitrack.csv"
    opti_file.write_text("")

    match = batch.find_optitrack_match(str(imu_path), str(rec_root), str(opti_root))
    assert match == str(opti_file)


def test_find_optitrack_match_is_case_insensitive_on_filename(tmp_path):
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_X" / "Session_post"
    imu_dir.mkdir(parents=True)
    imu_path = imu_dir / "Trial_4_imu.csv"
    imu_path.write_text("")

    opti_dir = opti_root / "Participant_X" / "Session_post"
    opti_dir.mkdir(parents=True)
    # Deliberately odd casing -- real Motive exports aren't guaranteed lowercase.
    opti_file = opti_dir / "Trial_4_OptiTrack.csv"
    opti_file.write_text("")

    match = batch.find_optitrack_match(str(imu_path), str(rec_root), str(opti_root))
    assert match == str(opti_file)


def test_find_optitrack_match_returns_none_when_no_match_exists(tmp_path):
    """Mirrors the real right_post Trial_5 case: an IMU trial with no
    OptiTrack counterpart anywhere in the mirrored tree must be reported
    as unmatched, not raise."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_13_right_post" / "Session_post" / \
        "Position_1" / "Height_Joint-Level"
    imu_dir.mkdir(parents=True)
    imu_path = imu_dir / "Trial_5_imu.csv"
    imu_path.write_text("")

    opti_dir = opti_root / "Participant_13_right_post" / "Session_post"
    opti_dir.mkdir(parents=True)
    # Only trial 1 exists -- no trial_5 match anywhere.
    (opti_dir / "trial_1_optitrack.csv").write_text("")

    match = batch.find_optitrack_match(str(imu_path), str(rec_root), str(opti_root))
    assert match is None


def test_find_optitrack_match_does_not_match_numbered_suffix_variant(tmp_path):
    """A retake file like trial_4_optitrack_000.csv must not be mistaken for
    the primary trial_4_optitrack.csv match."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_13_left_post" / "Session_post"
    imu_dir.mkdir(parents=True)
    imu_path = imu_dir / "Trial_4_imu.csv"
    imu_path.write_text("")

    opti_dir = opti_root / "Participant_13_left_post" / "Session_post"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_4_optitrack_000.csv").write_text("")
    exact = opti_dir / "trial_4_optitrack.csv"
    exact.write_text("")

    match = batch.find_optitrack_match(str(imu_path), str(rec_root), str(opti_root))
    assert match == str(exact)


def test_find_optitrack_match_does_not_walk_above_rec_root_scope(tmp_path):
    """A trial_N_optitrack.csv sitting directly at opti_root (no participant
    scoping at all) must not be treated as a match for an unrelated
    participant's trial -- the walk should stay scoped under the matched
    participant subtree, not fall all the way through to opti_root itself."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_13_right_post" / "Session_post"
    imu_dir.mkdir(parents=True)
    imu_path = imu_dir / "Trial_1_imu.csv"
    imu_path.write_text("")

    opti_root.mkdir(parents=True)
    # A same-numbered trial file dumped directly at opti_root, unrelated to
    # this participant -- must NOT be picked up.
    (opti_root / "trial_1_optitrack.csv").write_text("")

    match = batch.find_optitrack_match(str(imu_path), str(rec_root), str(opti_root))
    assert match is None


def test_find_optitrack_match_anchor_not_under_rec_root_returns_none(tmp_path):
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root.mkdir()
    opti_root.mkdir()
    outside = tmp_path / "elsewhere" / "Trial_1_imu.csv"
    outside.parent.mkdir(parents=True)
    outside.write_text("")

    match = batch.find_optitrack_match(str(outside), str(rec_root), str(opti_root))
    assert match is None
