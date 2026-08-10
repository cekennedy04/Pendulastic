import os, sys, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine


# ── Real-data regression: 2026-08-07 zero-capture-contamination fix ────────
#
# Root cause: two real trials' raw gyro logs started already in motion
# (examiner still positioning/releasing the sensor) instead of from a
# genuine still hold. imu_calibration_tuner.replay_trial's zero-orientation
# capture (_swing_from_quats' q_zero) armed on the very first sample above
# _FLEX_CAPTURE_THRESHOLD with no stillness precondition, so it zeroed on
# that contamination -- offsetting every downstream angle by a large,
# roughly-constant bias (26.9/28.4 deg bias, 32.8/33.0 deg RMSE vs
# OptiTrack, both nearly double the corpus median). Fixed by
# imu_calibration_tuner._recently_calm + _RoleState.ever_calm, gating the
# capture on a confirmed trailing window of low gyro magnitude. See
# tests/test_imu_calibration_tuner.py::
# test_replay_trial_ignores_pre_release_contamination_when_capturing_zero
# for the synthetic unit-level regression test of the same fix.
#
# Skipped when the real Recordings/ tree isn't present (large, gitignored,
# machine-local participant data -- see CLAUDE.md) so this suite still
# passes in a fresh checkout; it only runs, and only matters, on the
# machine holding the actual recordings.

RECORDINGS_PRESENT = os.path.isdir(batch.REC_ROOT) and os.path.isdir(batch.OPTI_ROOT)
skip_without_real_recordings = pytest.mark.skipif(
    not RECORDINGS_PRESENT,
    reason="Real Recordings_/OptiTrack_Recordings trees not present on this machine")


def _score_real_trial(imu_path):
    paths = batch.derive_component_paths(imu_path)
    opti_path = batch.find_optitrack_match(imu_path, batch.REC_ROOT, batch.OPTI_ROOT)
    assert opti_path is not None, f"no OptiTrack match found for {imu_path!r}"
    return batch.evaluate_trial(paths["imu"], paths["accel"], paths["gyro"],
                                paths["mag"], opti_path)


@skip_without_real_recordings
def test_contaminated_trial_no_longer_has_extreme_bias():
    """Participant_13_left_post Trial_4: 32.8 deg RMSE / 26.9 deg bias
    before the fix (see Model_Analysis_Outputs/imu_vs_optitrack_rmse.csv,
    captured pre-fix). Post-fix this trial's zero now captures from the
    genuine hold instead of pre-release contamination, cutting RMSE to
    ~21.7 deg -- a real, large improvement, though NOT yet under the
    project's 10 deg target (other, separate causes of RMSE noted in the
    2026-08-07 investigation still apply: e.g. this trial's own remaining
    bias, and unrelated sync/reference-frame issues on other trials). This
    pins the improvement as a floor so it can't silently regress back
    toward the pre-fix value, without overclaiming a target this one fix
    doesn't reach on its own."""
    imu_path = os.path.join(
        batch.REC_ROOT, "Participant_13_left_post", "Session_post",
        "Position_1", "Height_Joint-Level", "Trial_4_imu.csv")
    row = _score_real_trial(imu_path)
    assert row["status"] == "ok", row.get("error")
    # Measured post-fix value is 21.7 deg; 23.0 leaves headroom for benign
    # numeric noise (library version drift, float rounding) without
    # tolerating a slide back toward the pre-fix 32.8 deg.
    assert row["rmse_deg"] < 23.0, (
        f"rmse_deg={row['rmse_deg']:.2f} -- expected ~21.7 deg (the measured "
        f"post-fix value); this catches a regression toward the pre-fix "
        f"32.8 deg, not just any improvement")


@skip_without_real_recordings
def test_trial_with_no_genuine_pre_release_calm_is_flagged_not_silently_wrong():
    """Participant_15/Left/pre/Trial_4: 33.0 deg RMSE / 28.4 deg bias
    before the fix, and unlike Trial_4 above, this raw log has NO
    contiguous stretch of low gyro magnitude anywhere before its original
    (contaminated) zero-capture point -- there is no genuine still baseline
    to recover. Post-fix this trial correctly comes back as unscoreable
    (status="error", "Need at least 4 finite samples...") rather than
    silently reporting a ~30 deg-wrong angle: an honest "cannot score this"
    is the right outcome here, matching the project's accuracy-over-
    coverage goal, not a regression to guard against."""
    imu_path = os.path.join(
        batch.REC_ROOT, "Participant_15", "Left", "pre", "Trial_4_imu.csv")
    row = _score_real_trial(imu_path)
    assert row["status"] == "error"
    # Must fail for the specific reason this test is about (replay_trial
    # never zeroing -> compare_pair's "too few finite samples" error), not
    # any other failure -- an unrelated error (bad path, validation
    # failure, mag-rate floor) would make this assertion vacuously true.
    assert "finite samples" in (row.get("error") or ""), (
        f"expected the 'too few finite samples' error from an unzeroed "
        f"replay, got: {row.get('error')!r}")


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


# ── find_optitrack_match: position-collision ambiguity guard ───────────────

def test_find_optitrack_match_flags_ambiguous_multi_position_shared_ancestor(tmp_path):
    """Regression test for the latent collision risk found in review: if a
    future participant dumps OptiTrack CSVs for TWO positions into a shared
    shallow ancestor (mirroring the real right_post Session_post-level
    dump), and both positions have an IMU trial with the same number, the
    matcher must not silently pick one -- it must report unmatched/
    ambiguous and warn, not guess."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"

    # Two positions, both with a Trial_3_imu.csv.
    pos1_dir = rec_root / "Participant_99_right_post" / "Session_post" / \
        "Position_1" / "Height_Joint-Level"
    pos2_dir = rec_root / "Participant_99_right_post" / "Session_post" / \
        "Position_2" / "Height_Joint-Level"
    pos1_dir.mkdir(parents=True)
    pos2_dir.mkdir(parents=True)
    pos1_imu = pos1_dir / "Trial_3_imu.csv"
    pos2_imu = pos2_dir / "Trial_3_imu.csv"
    pos1_imu.write_text("")
    pos2_imu.write_text("")

    # A single OptiTrack CSV dumped directly under Session_post -- above
    # both Position_* directories, exactly like the real right_post case.
    opti_dir = opti_root / "Participant_99_right_post" / "Session_post"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_3_optitrack.csv").write_text("")

    with pytest.warns(UserWarning, match="Ambiguous OptiTrack match"):
        match_pos1 = batch.find_optitrack_match(
            str(pos1_imu), str(rec_root), str(opti_root))
    assert match_pos1 is None

    with pytest.warns(UserWarning, match="Ambiguous OptiTrack match"):
        match_pos2 = batch.find_optitrack_match(
            str(pos2_imu), str(rec_root), str(opti_root))
    assert match_pos2 is None


def test_find_optitrack_match_shared_ancestor_not_ambiguous_when_only_one_position_has_trial(tmp_path):
    """Sanity check on the guard's precision: a shared-ancestor match above
    the Position_* level is still trusted (no warning, real match returned)
    when only ONE position actually has that trial number -- this is the
    real right_post scenario (single Position_1) and must keep working
    exactly as before."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"

    pos1_dir = rec_root / "Participant_99_right_post" / "Session_post" / \
        "Position_1" / "Height_Joint-Level"
    pos2_dir = rec_root / "Participant_99_right_post" / "Session_post" / \
        "Position_2" / "Height_Joint-Level"
    pos1_dir.mkdir(parents=True)
    pos2_dir.mkdir(parents=True)
    pos1_imu = pos1_dir / "Trial_3_imu.csv"
    # Position_2 only has Trial_4, not Trial_3 -- no collision possible.
    pos2_imu = pos2_dir / "Trial_4_imu.csv"
    pos1_imu.write_text("")
    pos2_imu.write_text("")

    opti_dir = opti_root / "Participant_99_right_post" / "Session_post"
    opti_dir.mkdir(parents=True)
    opti_file = opti_dir / "trial_3_optitrack.csv"
    opti_file.write_text("")

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning here should fail the test
        match = batch.find_optitrack_match(
            str(pos1_imu), str(rec_root), str(opti_root))
    assert match == str(opti_file)


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
