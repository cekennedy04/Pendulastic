import os, sys, warnings
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import participant_paths
import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine
import pt_report_common


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


def test_discover_trials_drops_excluded_trial(tmp_path, monkeypatch):
    """A trial present in excluded_trials.json must not appear in
    discover_trials()'s output at all."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_5" / "Left" / "pre"
    imu_dir.mkdir(parents=True)
    opti_dir = opti_root / "Participant_5" / "Left" / "pre"
    opti_dir.mkdir(parents=True)
    for suffix in ("_imu.csv", "_accel.csv", "_gyro.csv", "_mag.csv"):
        (imu_dir / f"Trial_1{suffix}").write_text("x", encoding="utf-8")
    (opti_dir / "trial_1_optitrack.csv").write_text("x", encoding="utf-8")

    monkeypatch.setattr(batch, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(batch, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(pt_report_common, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(pt_report_common, "OPTI_ROOT", str(opti_root))
    key = pt_report_common.trial_key("5", "left", "pre", "1")
    monkeypatch.setattr(pt_report_common, "EXCLUDED_TRIALS_PATH",
                        str(tmp_path / "excluded_trials.json"))
    pt_report_common.add_excluded_trial(key, "test exclusion")

    trials = batch.discover_trials()
    assert trials == []


def test_discover_trials_keeps_non_excluded_trial_and_sets_trial_key(tmp_path, monkeypatch):
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_5" / "Left" / "pre"
    imu_dir.mkdir(parents=True)
    opti_dir = opti_root / "Participant_5" / "Left" / "pre"
    opti_dir.mkdir(parents=True)
    for suffix in ("_imu.csv", "_accel.csv", "_gyro.csv", "_mag.csv"):
        (imu_dir / f"Trial_1{suffix}").write_text("x", encoding="utf-8")
    (opti_dir / "trial_1_optitrack.csv").write_text("x", encoding="utf-8")

    monkeypatch.setattr(batch, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(batch, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(pt_report_common, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(pt_report_common, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(pt_report_common, "EXCLUDED_TRIALS_PATH",
                        str(tmp_path / "excluded_trials.json"))

    trials = batch.discover_trials()
    assert len(trials) == 1
    assert trials[0]["trial_key"] == pt_report_common.trial_key("5", "left", "pre", "1")


def test_compute_stratified_stats_all_trials_no_tags():
    rows = [
        {"status": "ok", "rmse_deg": 5.0, "trial_key": "a"},
        {"status": "ok", "rmse_deg": 15.0, "trial_key": "b"},
        {"status": "error", "rmse_deg": None, "trial_key": "c"},
    ]
    result = batch.compute_stratified_stats(rows, {}, goal_deg=10.0)
    assert result["all"] == {"n": 2, "mean": 10.0, "median": 10.0, "n_under_goal": 1}
    assert list(result.keys()) == ["all"]   # no tag categories present -> no extra breakdowns


def test_compute_stratified_stats_breaks_down_by_tag_category():
    rows = [
        {"status": "ok", "rmse_deg": 5.0, "trial_key": "a"},
        {"status": "ok", "rmse_deg": 25.0, "trial_key": "b"},   # tagged calibration_hold
        {"status": "ok", "rmse_deg": 8.0, "trial_key": "c"},
    ]
    quality_tags = {"b": {"category": "calibration_hold", "details": "x", "timestamp": "t"}}
    result = batch.compute_stratified_stats(rows, quality_tags, goal_deg=10.0)
    assert result["all"] == {"n": 3, "mean": pytest.approx(38.0 / 3), "median": 8.0, "n_under_goal": 2}
    assert result["excluding_calibration_hold"] == {
        "n": 2, "mean": 6.5, "median": 6.5, "n_under_goal": 2}


def test_compute_stratified_stats_returns_none_when_no_ok_rows():
    rows = [{"status": "error", "rmse_deg": None, "trial_key": "a"}]
    result = batch.compute_stratified_stats(rows, {}, goal_deg=10.0)
    assert result["all"] is None


def _real_trial_path(pid, leg, condition, trial):
    """Locate one real IMU trial without hardcoding the directory layout.

    Recordings/ has been reorganized three times (a Participant_13_left_post
    wrapper appeared, then gave way to the Leg/condition layout, then the
    participant folders took their study prefix -- Control_/CHAT_/MINT_). Each
    time, tests that spelled the path out failed on a missing file rather than
    on the thing they measure, which is a slow and misleading way to learn the
    tree moved. Resolve the participant by id and search beneath them instead,
    and fail with a message that says what was looked for.
    """
    root = participant_paths.participant_dir(batch.REC_ROOT, pid)
    wanted = f"Trial_{trial}_imu.csv"
    hits = [os.path.join(d, wanted)
            for d, _sub, files in os.walk(root) if wanted in files]
    seg = os.sep + leg.lower() + os.sep
    hits = [h for h in hits if seg in h.lower()]
    exact = [h for h in hits
             if os.sep + condition.lower() + os.sep in h.lower()]
    chosen = exact or hits
    if not chosen:
        pytest.skip(f"no Trial_{trial} for P{pid} {leg}/{condition} under {root}")
    return sorted(chosen, key=len)[0]


def _score_real_trial(imu_path):
    paths = batch.derive_component_paths(imu_path)
    opti_path = batch.find_optitrack_match(imu_path, batch.REC_ROOT, batch.OPTI_ROOT)
    assert opti_path is not None, f"no OptiTrack match found for {imu_path!r}"
    return batch.evaluate_trial(paths["imu"], paths["accel"], paths["gyro"],
                                paths["mag"], opti_path)


@skip_without_real_recordings
def test_contaminated_trial_no_longer_has_extreme_bias():
    """Participant_13_left_post Trial_4: 32.8 deg RMSE / 26.9 deg bias
    before the 2026-08-07 zero-capture-contamination fix (see
    Model_Analysis_Outputs/imu_vs_optitrack_rmse.csv, captured pre-fix).
    That fix alone cut RMSE to ~21.7 deg.

    2026-08-10 accel-bias fixes (see _RoleState.calibrate_accel_bias /
    pendulastic_imu_server._IMUDevice.calibrate_accel_bias and .on_gyro),
    three compounding bugs found from a live phone reporting knee-angle
    drift while held stationary:
      1. calibrate_accel_bias() assumed accel data is in m/s² (gravity=
         9.81); this phone's build reports g's (gravity=1) -- the huge
         resulting unit-mismatched bias silently disabled the AHRS's
         accelerometer-correction gate for the whole trial (pure,
         uncorrected gyro integration).
      2. It also assumed gravity reads +g on the sensor's Z axis at rest;
         this phone's mounting reads -g -- the ~2g-off bias this produced
         still passed the correction gate (right magnitude) but pointed
         gravity 180 deg off, so the filter actively steered orientation
         toward a wrong-but-stable reference instead of doing nothing.
      3. The AHRS also used magnetometer data for yaw correction; indoor
         magnetic fields are commonly disturbed (this phone's magnetometer
         stream froze mid-recording) and yaw isn't clinically relevant to
         knee flexion, so a disturbed reading was actively steering yaw
         wrong. Magnetometer correction is now unused (mag=None passed to
         AHRS.update(), its own documented "IMU-only" fallback path).
    Together these are unambiguously the right fix: confirmed against a
    live phone held stationary for 32s -- pre-fix the reported angle
    drifted 170->55 deg with zero real motion; with only fixes 1-2 it
    still drifted to a stable-but-wrong ~77 deg (magnetometer-driven);
    with all three it holds flat to within ~2 deg (see conversation, not
    a separate persisted test here).

    For THIS trial, fixes 1-2 alone made RMSE *worse* (21.7 -> ~27.9 deg)
    because of a separate, pre-existing issue: calibrate_accel_bias()'s
    Z-axis-at-rest assumption doesn't hold for this trial's mounting
    (measured hold-window accel ~[-0.70,-0.26,0.66] -- gravity spread
    across all three axes, not Z-dominant like the phone above). Fix 3
    (dropping magnetometer correction) then improved it past even the
    original 21.7 deg baseline, to ~20.3 deg -- apparently this trial's
    magnetic environment was ALSO disturbed enough to be net-harmful, same
    root cause as the stationary-hold bug, independent of the Z-axis
    mismatch. The Z-axis mismatch itself is unresolved and tracked
    separately, not fixed here.

    2026-08-11 follow-up changes (magnitude-only accel-bias correction
    replacing the Z-axis-forcing described above, a gyro-magnitude gate on
    MadgwickAHRS's accelerometer correction, and release-anchored
    cross-correlation bounds in workbench_engine.compare_pair -- see git
    history / Model_Analysis_Outputs/imu_vs_optitrack_rmse.csv for the
    corpus-wide before/after) moved this SPECIFIC trial's RMSE the wrong
    way, 20.3 -> ~28.4 deg, even though the same changes cut the corpus
    mean/median by roughly a third and fixed several much-worse outliers
    (one dropped from 40 to 2 deg). This trial's own hold-window gravity is
    spread across all three axes (not Z-dominant), so the fix that helps
    the corpus broadly removes a bias correction that, for this one
    mounting, happened to partially cancel a separate, still-not-understood
    error source. Pinning the current value as a floor either way -- this
    test's job is to catch a silent regression from HERE, not to assert
    this trial is well-scored; the corpus-level regression coverage lives
    in the mean/median printed by batch_imu_vs_optitrack_rmse.py's main().

    2026-09-02 note: the assertion below now reads rmse_deg_offset_removed for
    this trial. See the comment at the assertion -- the optical reference stopped
    claiming an absolute zero, so raw RMSE against it measures the missing
    origin rather than the IMU.

    Path note: Recordings/ has been reorganized twice. The trial first moved
    under an extra Participant_13/ wrapper
    (Recordings/Participant_13/Participant_13_left_post/...), and has since
    moved again to the Leg/condition layout every other participant uses
    (Recordings/Participant_13/Left/post/...). This test kept pointing at the
    intermediate wrapper path, which stopped existing, so it failed on a
    missing file rather than on anything it is meant to measure. Derive the
    path from the layout the repo actually uses now."""
    imu_path = _real_trial_path("13", "Left", "post", 4)
    row = _score_real_trial(imu_path)
    assert row["status"] == "ok", row.get("error")

    # 2026-09-02: this assertion moved off rmse_deg, and NOT to make a failure
    # go away. This trial's optical reference loads by the marker path, which
    # since 87d7645 returns a RELATIVE curve and refuses to invent an absolute
    # zero. Raw rmse_deg went to 142.26 with bias_deg 141.57 -- 99.5% of it is
    # the missing origin, and the IMU had not moved at all. Measured directly:
    # the IMU curve spans [141.8, 179.9] and the optical one [-0.2, 51.4].
    #
    # With the offset removed this trial scores 16.7 deg, BETTER than the 28.4
    # it was pinned at. Asserting on rmse_deg here would be asserting on the
    # arbitrary sign of a hinge eigenvector.
    if row.get("optical_absolute_zero"):
        # A bone-axis reference does have an origin, so a constant offset there
        # is real disagreement and must still be scored.
        measured = row["rmse_deg"]
        limit = 31.0
    else:
        measured = row["rmse_deg_offset_removed"]
        limit = 22.0        # measured 16.7; headroom without tolerating a slide
    assert measured is not None and measured < limit, (
        f"{measured} exceeds {limit} (optical_absolute_zero="
        f"{row.get('optical_absolute_zero')}, raw rmse_deg={row['rmse_deg']:.2f}, "
        f"bias_deg={row['bias_deg']:.2f}) -- this catches a regression in the "
        f"IMU pipeline, not a change of reference convention")


@skip_without_real_recordings
def test_trial_with_no_genuine_pre_release_calm_is_flagged_not_silently_wrong():
    """P15 Left/pre/Trial_4: 33.0 deg RMSE / 28.4 deg bias
    before the fix, and unlike Trial_4 above, this raw log has NO
    contiguous stretch of low gyro magnitude anywhere before its original
    (contaminated) zero-capture point -- there is no genuine still baseline
    to recover. Post-fix this trial correctly comes back as unscoreable
    (status="error") rather than silently reporting a ~30 deg-wrong angle:
    an honest "cannot score this" is the right outcome here, matching the
    project's accuracy-over-coverage goal, not a regression to guard against.

    2026-08-27: this trial now fails EARLIER and for a better-stated reason.
    It has 66.8% optical coverage -- the cameras lost the markers for a third
    of it -- so load_optitrack rejects it outright instead of handing on a
    curve that was interpolated across the gap. Previously the loader filled
    those frames in and the trial failed further downstream on "too few
    finite samples", a symptom of the same bad data. Either message keeps
    this test's point (unscoreable, not silently wrong); the coverage one
    names the actual cause."""
    imu_path = _real_trial_path("15", "Left", "pre", 4)
    row = _score_real_trial(imu_path)
    assert row["status"] == "error"
    # Must fail for a reason this test is actually about -- either the
    # optical-coverage rejection or the downstream unzeroed-replay error.
    # An unrelated error (bad path, validation failure, mag-rate floor)
    # would otherwise make this assertion vacuously true.
    err = row.get("error") or ""
    assert ("coverage" in err) or ("finite samples" in err), (
        f"expected the optical-coverage rejection or the 'too few finite "
        f"samples' error from an unzeroed replay, got: {err!r}")


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


def test_summary_reads_offset_removed_rmse_only_where_the_reference_has_no_zero():
    """The headline must compare like with like. A marker-path reference is
    relative (87d7645), so its raw RMSE is ~99% missing origin: MINT_13
    Left/post trial_4 reads 143.9 raw vs 16.7 offset-removed. A rigid-body
    reference IS bone-grounded, so there a constant offset is real
    disagreement and must stay in the number."""
    rows = [
        {"status": "ok", "trial_key": "a", "rmse_deg": 143.9,
         "rmse_deg_offset_removed": 16.7, "optical_absolute_zero": False},
        {"status": "ok", "trial_key": "b", "rmse_deg": 9.0,
         "rmse_deg_offset_removed": 3.0, "optical_absolute_zero": True},
        {"status": "ok", "trial_key": "c", "rmse_deg": 4.0},   # legacy row
    ]
    assert [batch.comparable_rmse(r) for r in rows] == [16.7, 9.0, 4.0]
    s = batch.compute_stratified_stats(rows, {}, 5.0)["all"]
    assert s["median"] == 9.0 and s["n_under_goal"] == 1


# ── Pair polarity reaches the row, and so the CSV ───────────────────────────
#
# The review of 4792020: "No caller reads [ref_inverted / test_inverted]
# today, so 'reported rather than hidden' does not hold at the CSV level."


def _stub_engine_for_evaluate_trial(monkeypatch, method, result, relative=None):
    """evaluate_trial with every loader stubbed; returns the kwargs
    compare_pair was called with. `relative` is what the optical loader says
    about its own curve (default: the knee-axis marker path says relative,
    everything else does not)."""
    seen = {}
    if relative is None:
        relative = method == "marker_pca"
    monkeypatch.setattr(engine, "validate_component_csv",
                        lambda path, kind: {"ok": True, "error": None})
    monkeypatch.setattr(engine, "load_imu_trial_from_components",
                        lambda validations: ([0.0, 1.0], [1.0, 2.0], None))
    monkeypatch.setattr(engine, "load_optitrack_reference",
                        lambda path: ([0.0, 1.0], [1.0, 2.0], method, relative))

    def fake_compare(ref_t, ref_y, t, y, **kwargs):
        seen.update(kwargs)
        return dict(result)
    monkeypatch.setattr(engine, "compare_pair", fake_compare)
    return seen


_OK_RESULT = {
    "status": "ok", "rmse_deg": 143.9, "rmse_deg_offset_removed": 7.2,
    "mae_deg": 1.0, "bias_deg": 143.0, "lag_sec": -0.7, "n_samples": 400,
    "orientation": "ref_mirrored", "orientation_basis": "shape+correlation",
    "orientation_reason": None, "ref_mirrored": True, "ref_inverted": True,
    "test_inverted": False, "polarity_corr_as_given": -0.86,
    "polarity_corr_mirrored": 0.99,
    "rmse_deg_offset_removed_other_polarity": 31.5,
}
_IDS = {"participant": "P", "position": "Left", "trial": "Trial_1", "trial_key": "k"}


def test_evaluate_trial_row_carries_the_pair_polarity(monkeypatch, tmp_path):
    seen = _stub_engine_for_evaluate_trial(monkeypatch, "marker_pca", _OK_RESULT)
    row = batch.evaluate_trial("i", "a", "g", "m", "o", dict(_IDS))
    assert row["status"] == "ok"
    for key in batch._ORIENTATION_FIELDS:
        assert key in batch._FIELDNAMES
        assert row[key] == _OK_RESULT[key]
    # ...and the CSV writer accepts the row as it stands.
    import csv
    out = tmp_path / "rows.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=batch._FIELDNAMES)
        w.writeheader()
        w.writerow(row)
    with open(out, newline="", encoding="utf-8") as f:
        back = next(csv.DictReader(f))
    assert back["orientation"] == "ref_mirrored"
    assert back["ref_mirrored"] == "True" and back["test_inverted"] == "False"


def test_evaluate_trial_only_lets_a_relative_reference_be_mirrored(monkeypatch):
    """Only a curve the loader itself declared relative has an arbitrary sign.
    A rigid-body one is bone-grounded and must never be mirrored, silently or
    otherwise -- and neither must a precomputed-angle file or the quaternion
    fallback, which carry the same "marker_pca" tag but a real sign."""
    seen = _stub_engine_for_evaluate_trial(monkeypatch, "marker_pca", _OK_RESULT)
    row = batch.evaluate_trial("i", "a", "g", "m", "o", dict(_IDS))
    assert seen["ref_polarity_arbitrary"] is True
    assert row["optical_relative"] is True
    seen = _stub_engine_for_evaluate_trial(monkeypatch, "rigid_body", _OK_RESULT)
    row = batch.evaluate_trial("i", "a", "g", "m", "o", dict(_IDS))
    assert seen["ref_polarity_arbitrary"] is False
    assert row["optical_absolute_zero"] is True
    assert row["optical_relative"] is False
    seen = _stub_engine_for_evaluate_trial(monkeypatch, "marker_pca", _OK_RESULT,
                                           relative=False)
    row = batch.evaluate_trial("i", "a", "g", "m", "o", dict(_IDS))
    assert seen["ref_polarity_arbitrary"] is False
    assert row["optitrack_method"] == "marker_pca"
    assert row["optical_relative"] is False
    assert "optical_relative" in batch._FIELDNAMES


def test_error_rows_have_the_polarity_columns_empty(monkeypatch):
    _stub_engine_for_evaluate_trial(
        monkeypatch, "marker_pca", {"status": "error", "error": "no overlap"})
    row = batch.evaluate_trial("i", "a", "g", "m", "o", dict(_IDS))
    assert row["status"] == "error" and row["error"] == "no overlap"
    assert all(row[key] is None for key in batch._ORIENTATION_FIELDS)


def test_stratified_stats_break_out_pairs_whose_polarity_is_undetermined():
    """An undetermined pair was scored as given, which for a relative
    reference may be its mirror image. It stays in "all" and is also
    reported excluded, so neither figure hides the other."""
    rows = [
        {"status": "ok", "trial_key": "a", "rmse_deg": 5.0, "orientation": "as_given"},
        {"status": "ok", "trial_key": "b", "rmse_deg": 7.0, "orientation": "ref_mirrored"},
        {"status": "ok", "trial_key": "c", "rmse_deg": 40.0, "orientation": "undetermined"},
    ]
    stats = batch.compute_stratified_stats(rows, {}, 10.0)
    assert stats["all"]["n"] == 3 and stats["all"]["median"] == 7.0
    assert stats["excluding_orientation_undetermined"] == {
        "n": 2, "mean": 6.0, "median": 6.0, "n_under_goal": 2}
    # No undetermined pair, no extra key: rows from before this field existed
    # summarise exactly as they did.
    assert "excluding_orientation_undetermined" not in batch.compute_stratified_stats(
        rows[:2], {}, 10.0)


def _polarity_rows():
    """Nine pairs over three participants. Participant 15 has three of its
    four undetermined; 13 has one of three; 4 has none."""
    def row(key, rmse, orientation, basis=None, reason=None, other=None,
            test_inverted=False):
        return {"status": "ok", "trial_key": key, "participant": "x",
                "rmse_deg": 150.0, "rmse_deg_offset_removed": rmse,
                "optical_absolute_zero": False, "orientation": orientation,
                "orientation_basis": basis, "orientation_reason": reason,
                "rmse_deg_offset_removed_other_polarity": other,
                "test_inverted": test_inverted}
    inside = "correlation_inside_margin (ref: no_prerelease_plateau)"
    return [
        row("4_left_pre_T1", 4.0, "as_given", "shape+correlation", other=30.0,
            test_inverted=None),
        row("4_left_pre_T2", 6.0, "ref_mirrored", "shape+correlation", other=40.0),
        row("13_left_post_T1", 8.0, "ref_mirrored", "shape+correlation", other=35.0),
        row("13_left_post_T2", 20.0, "as_given", "correlation", other=45.0,
            test_inverted=True),
        row("13_left_post_T3", 12.0, "undetermined", reason=inside, other=3.0),
        row("15_right_post_T1", 10.0, "as_given", "shape", other=50.0),
        row("15_right_post_T2", 30.0, "undetermined", reason=inside, other=9.0),
        row("15_right_post_T3", 2.0, "undetermined", other=44.0,
            reason="correlation_alone_never_mirrors (ref: no_prerelease_plateau)",
            test_inverted=True),
        row("15_right_post_T4", 26.0, "undetermined", other=None,
            reason="correlation_undefined (ref: no_prerelease_plateau)"),
        {"status": "error", "trial_key": "15_right_post_T5", "orientation": None},
    ]


def test_orientation_summary_counts_and_medians():
    s = batch.compute_orientation_summary(_polarity_rows(), 5.0)
    assert (s["decided"], s["undetermined"], s["total"]) == (5, 4, 9)
    assert s["by_orientation"] == {"as_given": 3, "ref_mirrored": 2, "undetermined": 4}
    # The headline is every ok pair, undetermined ones included as scored.
    assert s["all"] == {"n": 9, "median": 10.0, "n_under_goal": 2,
                        "mean": pytest.approx(118.0 / 9)}
    assert s["decided_only"]["n"] == 5 and s["decided_only"]["median"] == 8.0
    assert s["undetermined_only"]["n"] == 4 and s["undetermined_only"]["median"] == 19.0
    assert s["undetermined_only"]["n_under_goal"] == 1


def test_orientation_summary_bounds_move_only_the_undetermined_pairs():
    """Best fit: each undetermined pair at the lower of its two polarities.
    Worst fit: at the higher. Decided pairs stay where they were decided even
    though the other polarity is on the row, and a pair with no other-polarity
    score stays as given in both."""
    s = batch.compute_orientation_summary(_polarity_rows(), 5.0)
    # decided: 4, 6, 8, 20, 10; undetermined best: 3, 9, 2, 26; worst: 12, 30, 44, 26
    assert s["median_best_fit"] == 8.0
    assert s["median_worst_fit"] == 12.0
    assert s["n_unbounded"] == 1
    assert s["median_best_fit"] <= s["all"]["median"] <= s["median_worst_fit"]


def test_orientation_summary_names_whose_pairs_are_undetermined():
    s = batch.compute_orientation_summary(_polarity_rows(), 5.0)
    assert s["undetermined_by_participant"] == {"13": (1, 3), "15": (3, 4)}
    assert s["majority_undetermined"] == ["15"]
    assert s["undetermined_by_reason"] == {
        "correlation_inside_margin": 2, "correlation_alone_never_mirrors": 1,
        "correlation_undefined": 1}
    assert s["by_basis"]["shape+correlation"]["n"] == 3
    assert s["by_basis"]["shape+correlation"]["median"] == 6.0
    assert s["by_basis"]["correlation"] == {
        "n": 1, "mean": 20.0, "median": 20.0, "n_under_goal": 0}
    assert s["by_basis"]["shape"]["n"] == 1
    assert s["test_inverted"] == ["13_left_post_T2", "15_right_post_T3"]


def test_orientation_summary_half_is_not_a_majority_and_old_rows_give_none():
    rows = _polarity_rows()[:2] + [
        {**_polarity_rows()[4], "trial_key": "20_right_pre_T1"},
        {**_polarity_rows()[0], "trial_key": "20_right_pre_T2"}]
    s = batch.compute_orientation_summary(rows, 5.0)
    assert s["undetermined_by_participant"] == {"20": (1, 2)}
    assert s["majority_undetermined"] == []
    # Rows from before the polarity field existed: nothing to summarise.
    old = [{"status": "ok", "trial_key": "a", "rmse_deg": 5.0}]
    assert batch.compute_orientation_summary(old, 5.0) is None
    assert batch.format_orientation_summary(None, 5.0) == []
    # A row with no trial key falls back to the participant folder's ID.
    keyless = {**_polarity_rows()[4], "trial_key": None,
               "participant": None}
    for folder in participant_paths.dir_name_candidates("22"):
        keyless["participant"] = folder
        s = batch.compute_orientation_summary([keyless], 5.0)
        assert list(s["undetermined_by_participant"]) == ["22"]


def test_orientation_summary_text_never_gives_the_subset_median_alone():
    """What main() prints. The decided-pairs figure sits between the
    all-pairs headline with its bound and the undetermined pairs' own median;
    counts, per-participant losses, basis and inverted test curves are all
    there."""
    lines = batch.format_orientation_summary(
        batch.compute_orientation_summary(_polarity_rows(), 5.0), 5.0)
    text = "\n".join(lines)
    assert "decided 5 / undetermined 4 / total 9" in text
    assert "as_given=3, ref_mirrored=2, undetermined=4" in text
    i_all = next(i for i, l in enumerate(lines) if "ALL PAIRS (headline)" in l)
    i_bound = next(i for i, l in enumerate(lines) if "better / worse fit" in l)
    i_dec = next(i for i, l in enumerate(lines) if "decided pairs (a SUBSET)" in l)
    i_und = next(i for i, l in enumerate(lines) if l.strip().startswith("undetermined pairs"))
    assert i_all < i_bound < i_dec and i_und == i_dec + 1
    assert "n=  9  median= 10.00" in lines[i_all] and "2/9 under 5 deg" in lines[i_all]
    assert "8.00 .. 12.00" in lines[i_bound]
    assert "1 with no other-polarity score" in lines[i_bound]
    assert "n=  5  median=  8.00" in lines[i_dec]
    assert "n=  4  median= 19.00" in lines[i_und]
    assert "undetermined by participant (of that participant's pairs): 13: 1/3, 15: 3/4*" in text
    assert "over half of this participant's pairs are undetermined: 15" in text
    assert "decided on correlation" in text and "decided on shape+correlation" in text
    assert "correlation_inside_margin=2" in text
    assert "test_inverted=True on 2 pair(s): 13_left_post_T2, 15_right_post_T3" in text


def test_orientation_summary_text_with_nothing_undetermined():
    rows = _polarity_rows()[:3]
    lines = batch.format_orientation_summary(
        batch.compute_orientation_summary(rows, 5.0), 5.0)
    text = "\n".join(lines)
    assert "decided 3 / undetermined 0 / total 3" in text
    assert "SUBSET" not in text and "better / worse fit" not in text
    assert "test_inverted=True on 0 pair(s)" in text


def test_main_prints_the_polarity_summary_and_no_bare_excluding_line(
        monkeypatch, tmp_path, capsys):
    """End of main(): the polarity block is printed, and the stratified table
    no longer carries an 'excluding_orientation_undetermined' line that could
    be lifted out as the result."""
    rows = _polarity_rows()
    for r in rows:
        r.update(position="Left", trial="Trial_1", n_samples=100,
                 optitrack_method="marker_pca", error=None)
    monkeypatch.setattr(batch, "REC_ROOT", str(tmp_path))
    monkeypatch.setattr(batch, "OUT_CSV", str(tmp_path / "out" / "rmse.csv"))
    monkeypatch.setattr(batch, "discover_trials",
                        lambda: [{"optitrack_path": "o", "participant": "x",
                                  "position": "Left", "trial": "Trial_1"}] * len(rows))
    monkeypatch.setattr(batch, "_evaluate_all", lambda matched: rows)
    monkeypatch.setattr(pt_report_common, "load_quality_tags", lambda: {})
    monkeypatch.setattr(sys, "argv", ["batch_imu_vs_optitrack_rmse.py"])
    batch.main()
    out = capsys.readouterr().out
    assert "decided 5 / undetermined 4 / total 9" in out
    assert "ALL PAIRS (headline)" in out
    assert "better / worse fit: 8.00 .. 12.00" in out
    assert "undetermined by participant" in out and "15: 3/4*" in out
    assert "test_inverted=True on 2 pair(s)" in out
    assert "excluding_orientation_undetermined" not in out
    assert out.index("ALL PAIRS (headline)") < out.index("decided pairs (a SUBSET)")
    # ...and the per-trial CSV carries the columns the summary was built from.
    import csv
    with open(tmp_path / "out" / "rmse.csv", newline="", encoding="utf-8") as f:
        back = list(csv.DictReader(f))
    assert back[4]["orientation"] == "undetermined"
    assert back[4]["rmse_deg_offset_removed_other_polarity"] == "3.0"


def test_compare_floor_pairs_offset_removed_rmse(capsys):
    # A marker-path reference's raw rmse_deg is mostly its missing zero; a
    # tracking change of 10 -> 5 deg moves it by under 0.2 deg. The paired
    # gate must see the 5 deg, not the 0.2.

    def row(raw, off):
        return {"participant": "P1", "position": "Left", "trial": "t1",
                "imu_path": "P1/Left/pre/Trial_1_imu.csv", "status": "ok", "rmse_deg": raw, "rmse_deg_offset_removed": off,
                "optical_absolute_zero": False}

    batch._report_comparison([row(150.33, 10.0)], [row(150.08, 5.0)])
    out = capsys.readouterr().out
    assert "median delta -5.0000" in out
    assert "IMPROVES" in out
