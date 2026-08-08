import json
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
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


# ── discover_imu_trials ───────────────────────────────────────────────────

def test_discover_imu_trials_wraps_batch_script(monkeypatch):
    fake_trials = [{
        "participant": "Participant_13_left_post", "position": "Position_1",
        "trial": "Trial_1",
        "imu": os.path.normpath(
            "Recordings/Participant_13_left_post/Session_post/Position_1/"
            "Height_Joint-Level/Trial_1_imu.csv"),
        "accel": "x_accel.csv", "gyro": "x_gyro.csv", "mag": "x_mag.csv",
        "optitrack_path": os.path.normpath(
            "OptiTrack_Recordings/Participant_13_left_post/Session_post/"
            "Position_1/Height_Joint-Level/trial_1_optitrack.csv"),
    }]
    monkeypatch.setattr(rpc.imu_discovery, "discover_trials", lambda: fake_trials)
    result = rpc.discover_imu_trials()
    assert len(result) == 1
    rec = result[0]
    assert rec["participant"] == "13" and rec["leg"] == "left"
    assert rec["position"] == "1" and rec["height"] == "joint-level"
    assert rec["imu_component_paths"]["accel"] == "x_accel.csv"
    assert rec["optitrack_path"] is not None
    assert "trial_key" in rec


def test_discover_imu_trials_unparseable_path_excluded(monkeypatch):
    fake_trials = [{
        "participant": "Participant_9", "position": "unknown", "trial": "Trial_1",
        "imu": os.path.normpath("Recordings/Participant_9/Trial_1_imu.csv"),  # no leg token
        "accel": "a", "gyro": "g", "mag": "m", "optitrack_path": None,
    }]
    monkeypatch.setattr(rpc.imu_discovery, "discover_trials", lambda: fake_trials)
    result = rpc.discover_imu_trials()
    assert result == []


def test_discover_imu_trials_parses_against_imu_discovery_rec_root_not_own(tmp_path, monkeypatch):
    # Regression test: imu_discovery.discover_trials() (batch_imu_vs_
    # optitrack_rmse.py, reused as-is) returns paths rooted under ITS OWN
    # REC_ROOT, which is a hardcoded literal and not guaranteed to equal
    # this module's own __file__-derived REC_ROOT -- e.g. a differently
    # located checkout, another OS, or this module's copy running somewhere
    # other than where the wrapped script's data actually lives. Model that
    # by pointing the two REC_ROOTs at two different tmp_path locations that
    # do NOT share a common ancestor other than tmp_path itself.
    #
    # Before the fix, discover_imu_trials() parsed t["imu"] against this
    # module's own (wrong) REC_ROOT, so os.path.relpath() between two
    # unrelated roots produced a string salted with ".." / root-directory
    # segments that survived, uncaught, into the "condition" field -- which
    # then flows into pt_report_common.trial_key(...), the lookup key into
    # excluded_trials.json. A corrupted key silently never matches a real
    # registry entry, so a trial meant to be excluded (e.g. a non-passive
    # release) would leak into scoring instead of being dropped.
    real_rec_root = tmp_path / "actual_checkout" / "Recordings"
    wrong_rec_root = tmp_path / "unrelated_dir" / "Recordings"
    imu_path = real_rec_root / "Participant_13" / "Left" / "week_1_post" / "Trial_1_imu.csv"

    monkeypatch.setattr(rpc.imu_discovery, "REC_ROOT", str(real_rec_root))
    monkeypatch.setattr(rpc, "REC_ROOT", str(wrong_rec_root))

    fake_trials = [{
        "participant": "Participant_13", "position": "unknown", "trial": "Trial_1",
        "imu": str(imu_path),
        "accel": "a", "gyro": "g", "mag": "m", "optitrack_path": "opti.csv",
    }]
    monkeypatch.setattr(rpc.imu_discovery, "discover_trials", lambda: fake_trials)

    result = rpc.discover_imu_trials()
    assert len(result) == 1
    rec = result[0]
    assert rec["participant"] == "13"
    assert rec["leg"] == "left"
    assert rec["trial_number"] == "1"
    # The whole point of the fix: condition must be the clean folder name,
    # not polluted with ".." or "recordings"/"unrelated_dir" segments from
    # relpath-ing against the wrong root.
    assert rec["condition"] == "week_1_post"
    assert ".." not in rec["condition"]


# ── discover_video_trials ────────────────────────────────────────────────

def test_discover_video_trials_finds_matching_video(tmp_path, monkeypatch):
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    opti_dir = opti_root / "Participant_14" / "Left" / "pre"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_3_optitrack.csv").write_text("t,angle\n", encoding="utf-8")
    video_dir = rec_root / "Participant_14" / "Left" / "pre"
    video_dir.mkdir(parents=True)
    (video_dir / "Trial_3.avi").write_bytes(b"fake video")

    monkeypatch.setattr(rpc, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(rpc, "REC_ROOT", str(rec_root))
    result = rpc.discover_video_trials()
    assert len(result) == 1
    rec = result[0]
    assert rec["participant"] == "14" and rec["leg"] == "left"
    assert rec["trial_number"] == "3"
    assert rec["video_path"] == str(video_dir / "Trial_3.avi")
    assert rec["optitrack_path"] == str(opti_dir / "trial_3_optitrack.csv")


def test_discover_video_trials_no_video_excluded(tmp_path, monkeypatch):
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    opti_dir = opti_root / "Participant_9" / "right" / "pre"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_1_optitrack.csv").write_text("t,angle\n", encoding="utf-8")
    rec_root.mkdir(parents=True)

    monkeypatch.setattr(rpc, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(rpc, "REC_ROOT", str(rec_root))
    assert rpc.discover_video_trials() == []


def test_discover_video_trials_checks_opti_side_video_too(tmp_path, monkeypatch):
    # batch_mediapipe.discover_new_trials's convention: the video may sit
    # alongside the OptiTrack CSV itself, not only under the mirrored
    # Recordings/ tree.
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    opti_dir = opti_root / "Participant_6" / "left" / "post"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_2_optitrack.csv").write_text("t,angle\n", encoding="utf-8")
    (opti_dir / "Trial_2.mp4").write_bytes(b"fake video")
    rec_root.mkdir(parents=True)

    monkeypatch.setattr(rpc, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(rpc, "REC_ROOT", str(rec_root))
    result = rpc.discover_video_trials()
    assert len(result) == 1
    assert result[0]["video_path"] == str(opti_dir / "Trial_2.mp4")


# ── discover_scorable_trials ─────────────────────────────────────────────

def _imu_trial(trial_key="k1", optitrack_path="opti.csv", **overrides):
    base = {"trial_key": trial_key, "participant": "13", "leg": "left",
           "condition": "post", "session": "post", "position": "1",
           "height": "joint-level", "trial_number": "1",
           "imu_anchor_path": "anchor.csv",
           "imu_component_paths": {"imu": "a", "accel": "b", "gyro": "c", "mag": "d"},
           "optitrack_path": optitrack_path}
    base.update(overrides)
    return base


def _video_trial(trial_key="k1", optitrack_path="opti.csv", **overrides):
    base = {"trial_key": trial_key, "participant": "13", "leg": "left",
           "condition": "post", "session": "post", "position": "1",
           "height": "joint-level", "trial_number": "1",
           "video_path": "vid.avi", "optitrack_path": optitrack_path}
    base.update(overrides)
    return base


def test_discover_scorable_trials_merges_by_trial_key(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [_video_trial()])
    result = rpc.discover_scorable_trials()
    assert len(result) == 1
    rec = result[0]
    assert rec["has_imu_rmse"] is True
    assert rec["has_mediapipe_rmse"] is True
    assert rec["exclusion_reasons"] == []


def test_discover_scorable_trials_imu_only_capability(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    result = rpc.discover_scorable_trials()
    assert result[0]["has_imu_rmse"] is True
    assert result[0]["has_mediapipe_rmse"] is False
    assert result[0]["video_path"] is None


def test_discover_scorable_trials_no_optitrack_excluded(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial(optitrack_path=None)])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    assert rpc.discover_scorable_trials() == []


def test_discover_scorable_trials_conflicting_optitrack_path_excluded_as_ambiguous(monkeypatch):
    # Same trial_key from both sides, but disagreeing on which OptiTrack
    # file it maps to -- design spec §4: never heuristically resolved,
    # excluded instead.
    monkeypatch.setattr(rpc, "discover_imu_trials",
                        lambda: [_imu_trial(optitrack_path="opti_A.csv")])
    monkeypatch.setattr(rpc, "discover_video_trials",
                        lambda: [_video_trial(optitrack_path="opti_B.csv")])
    result = rpc.discover_scorable_trials()
    assert result == []


# ── excluded_trials.json filtering (Global Constraints -- added after the
# post-plan Codex consult found this repo's shared exclusion registry) ────

def test_discover_scorable_trials_filters_excluded_trial(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    # _imu_trial()'s defaults are participant=13, leg=left, condition=post,
    # trial_number=1 -- the legacy key pt_report_common.trial_key builds
    # from those same fields.
    legacy_key = "13_left_post_T1"
    monkeypatch.setattr(rpc.pt_report_common, "load_excluded_trials",
                        lambda: {legacy_key: "operator-confirmed: active swing"})
    assert rpc.discover_scorable_trials() == []


def test_discover_scorable_trials_keeps_non_excluded_trial(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    monkeypatch.setattr(rpc.pt_report_common, "load_excluded_trials",
                        lambda: {"99_right_pre_T9": "unrelated trial"})
    result = rpc.discover_scorable_trials()
    assert len(result) == 1


def test_discover_scorable_trials_empty_registry_excludes_nothing(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    monkeypatch.setattr(rpc.pt_report_common, "load_excluded_trials", lambda: {})
    result = rpc.discover_scorable_trials()
    assert len(result) == 1


# ── sha256_file / fingerprints ───────────────────────────────────────────

def test_sha256_file_deterministic(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    h1 = rpc.sha256_file(str(f), cache)
    h2 = rpc.sha256_file(str(f), cache)
    assert h1 == h2 and len(h1) == 64


def test_sha256_file_reuses_cache_when_stat_unchanged(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    h1 = rpc.sha256_file(str(f), cache)
    # Overwrite with different content but don't touch the cache -- since
    # sha256_file only re-hashes when stat (size/mtime) changes, and we're
    # not asserting content correctness here, just that the cache path is
    # taken (returns the same digest without re-reading).
    stat_key = list(cache.keys())[0]
    cache[stat_key] = (cache[stat_key][0], "STALE_DIGEST_MARKER")
    h2 = rpc.sha256_file(str(f), cache)
    assert h2 == "STALE_DIGEST_MARKER"


def test_sha256_file_force_bypasses_cache(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    rpc.sha256_file(str(f), cache)
    stat_key = list(cache.keys())[0]
    cache[stat_key] = (cache[stat_key][0], "STALE_DIGEST_MARKER")
    h = rpc.sha256_file(str(f), cache, force=True)
    assert h != "STALE_DIGEST_MARKER"


def test_sha256_file_rehashes_when_stat_changes(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    h1 = rpc.sha256_file(str(f), cache)
    f.write_text("hello world, much longer content now", encoding="utf-8")
    h2 = rpc.sha256_file(str(f), cache)
    assert h1 != h2


def test_compute_input_fingerprints_imu(tmp_path):
    paths = {}
    for name in ("imu", "accel", "gyro", "mag"):
        p = tmp_path / f"{name}.csv"
        p.write_text(name, encoding="utf-8")
        paths[name] = str(p)
    opti = tmp_path / "opti.csv"
    opti.write_text("opti", encoding="utf-8")
    trial = {"imu_component_paths": paths, "optitrack_path": str(opti), "video_path": None}
    fps = rpc.compute_input_fingerprints(trial, "imu", {})
    assert set(fps["imu"].keys()) == {"imu", "accel", "gyro", "mag"}
    assert "optitrack" in fps
    assert "video" not in fps


def test_compute_input_fingerprints_mediapipe(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    opti = tmp_path / "opti.csv"
    opti.write_text("opti", encoding="utf-8")
    model = tmp_path / "pose_landmarker_full.task"
    model.write_bytes(b"model-weights-A")
    trial = {"imu_component_paths": None, "optitrack_path": str(opti), "video_path": str(video)}
    fps = rpc.compute_input_fingerprints(trial, "mediapipe", {}, model_path=str(model))
    assert "video" in fps and "optitrack" in fps
    assert "imu" not in fps
    # Design spec §7.1 lists model_file among the mediapipe branch's hashed
    # inputs -- swapping the .task weights changes every MediaPipe RMSE
    # without touching a single trial file.
    assert "model_file" in fps
    assert len(fps["model_file"]) == 64


def test_compute_input_fingerprints_mediapipe_differs_on_model_file(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    opti = tmp_path / "opti.csv"
    opti.write_text("opti", encoding="utf-8")
    trial = {"imu_component_paths": None, "optitrack_path": str(opti), "video_path": str(video)}
    model_a = tmp_path / "a" / "pose_landmarker_full.task"
    model_b = tmp_path / "b" / "pose_landmarker_full.task"
    model_a.parent.mkdir()
    model_b.parent.mkdir()
    model_a.write_bytes(b"model-weights-A")
    model_b.write_bytes(b"model-weights-B -- retrained, different bytes")

    fps_a = rpc.compute_input_fingerprints(trial, "mediapipe", {}, model_path=str(model_a))
    fps_b = rpc.compute_input_fingerprints(trial, "mediapipe", {}, model_path=str(model_b))
    assert fps_a["model_file"] != fps_b["model_file"]


def test_compute_input_fingerprints_mediapipe_requires_model_path(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    opti = tmp_path / "opti.csv"
    opti.write_text("opti", encoding="utf-8")
    trial = {"imu_component_paths": None, "optitrack_path": str(opti), "video_path": str(video)}
    try:
        rpc.compute_input_fingerprints(trial, "mediapipe", {})
    except ValueError as e:
        assert "model_path" in str(e)
    else:
        raise AssertionError("expected ValueError when model_path is omitted for mediapipe")


def test_compute_implementation_fingerprint_stable():
    assert rpc.compute_implementation_fingerprint() == rpc.compute_implementation_fingerprint()


def test_compute_implementation_fingerprint_changes_with_grid(monkeypatch):
    fp1 = rpc.compute_implementation_fingerprint()
    import sweep_imu_config
    monkeypatch.setattr(sweep_imu_config, "WIDE_GRID", [{"beta": 0.99}])
    fp2 = rpc.compute_implementation_fingerprint()
    assert fp1 != fp2


# ── score_imu_candidate ──────────────────────────────────────────────────

def test_score_imu_candidate_returns_rmse(monkeypatch):
    trial = {"imu_component_paths": {"imu": "i", "accel": "a", "gyro": "g", "mag": "m"},
            "optitrack_path": "o"}
    monkeypatch.setattr(rpc, "reconstruct_trial", lambda a, g, m: [{"t": 0.0}])
    monkeypatch.setattr(rpc.imu_calibration_tuner, "replay_trial",
                        lambda samples, params: (
                            __import__("numpy").array([0.0] * 10),
                            __import__("numpy").array([1.0] * 10)))
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (__import__("numpy").array([0.0] * 10),
                                      __import__("numpy").array([1.0] * 10)))
    monkeypatch.setattr(rpc.engine, "compare_pair",
                        lambda *a, **k: {"status": "ok", "rmse_deg": 3.5, "n_samples": 20})
    result = rpc.score_imu_candidate(trial, {"beta": 0.041})
    assert result == 3.5


def test_score_imu_candidate_returns_none_when_too_few_finite_samples(monkeypatch):
    import numpy as np
    trial = {"imu_component_paths": {"imu": "i", "accel": "a", "gyro": "g", "mag": "m"},
            "optitrack_path": "o"}
    monkeypatch.setattr(rpc, "reconstruct_trial", lambda a, g, m: [{"t": 0.0}])
    monkeypatch.setattr(rpc.imu_calibration_tuner, "replay_trial",
                        lambda samples, params: (np.array([0.0]), np.array([float("nan")])))
    result = rpc.score_imu_candidate(trial, {"beta": 0.041})
    assert result is None


def test_score_imu_candidate_returns_none_on_compare_pair_error(monkeypatch):
    import numpy as np
    trial = {"imu_component_paths": {"imu": "i", "accel": "a", "gyro": "g", "mag": "m"},
            "optitrack_path": "o"}
    monkeypatch.setattr(rpc, "reconstruct_trial", lambda a, g, m: [{"t": 0.0}])
    monkeypatch.setattr(rpc.imu_calibration_tuner, "replay_trial",
                        lambda samples, params: (
                            np.array([0.0] * 20), np.array([1.0] * 20)))
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (np.array([0.0] * 20), np.array([1.0] * 20)))
    monkeypatch.setattr(rpc.engine, "compare_pair",
                        lambda *a, **k: {"status": "error", "error": "no overlap"})
    result = rpc.score_imu_candidate(trial, {"beta": 0.041})
    assert result is None


# ── extract_landmarks_cached / score_mediapipe_candidate ────────────────

def _landmark_cache_fixture(tmp_path, monkeypatch, impl_fp="impl1"):
    """Shared setup for the landmark-cache tests: an isolated cache dir, a
    real video file (extract_landmarks_cached content-hashes it), a real
    .task model file (it content-hashes that too now), and a counting stub
    for the expensive extraction call. compute_implementation_fingerprint
    is pinned to a constant rather than stubbed out of the call path, so
    the cache key genuinely includes it."""
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    monkeypatch.setattr(rpc, "compute_implementation_fingerprint", lambda: impl_fp)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    model = tmp_path / "models" / "pose_landmarker_full.task"
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes(b"model-weights-A")
    calls = []
    monkeypatch.setattr(rpc.mediapipe_sweep, "extract_raw_landmarks",
                        lambda vp, leg, mp_: (calls.append(1) or [{"t": 0.0}]))
    trial = {"trial_key": "k1", "leg": "left", "video_path": str(video)}
    return trial, video, model, calls


def test_extract_landmarks_cached_calls_extraction_once(tmp_path, monkeypatch):
    trial, _video, model, calls = _landmark_cache_fixture(tmp_path, monkeypatch)
    rpc.extract_landmarks_cached(trial, "full", str(model))
    rpc.extract_landmarks_cached(trial, "full", str(model))
    assert len(calls) == 1


def test_extract_landmarks_cached_re_extracts_on_video_change(tmp_path, monkeypatch):
    trial, video, model, calls = _landmark_cache_fixture(tmp_path, monkeypatch)
    rpc.extract_landmarks_cached(trial, "full", str(model))
    video.write_bytes(b"different content, changes the hash")
    rpc.extract_landmarks_cached(trial, "full", str(model))
    assert len(calls) == 2


def test_extract_landmarks_cached_re_extracts_on_model_file_change(tmp_path, monkeypatch):
    # Final-review finding (critical): the cache filename keyed only on
    # (trial_key, model_variant, video fingerprint) -- 3 of the 5 components
    # design spec §7.1 requires. Two *different* .task files with the same
    # basename and the same model_variant label therefore collided, so
    # swapping the weights returned the OLD landmarks while the RMSE-level
    # cache correctly re-scored -- a "fresh" RMSE silently computed from
    # superseded extraction output.
    trial, _video, model_a, calls = _landmark_cache_fixture(tmp_path, monkeypatch)
    model_b = tmp_path / "models_v2" / "pose_landmarker_full.task"
    model_b.parent.mkdir(parents=True, exist_ok=True)
    model_b.write_bytes(b"model-weights-B -- retrained, different bytes")

    rpc.extract_landmarks_cached(trial, "full", str(model_a))
    rpc.extract_landmarks_cached(trial, "full", str(model_b))
    assert len(calls) == 2

    # And each model's landmarks live in their own cache entry -- neither
    # overwrote the other, so going back to model A is still a cache hit.
    rpc.extract_landmarks_cached(trial, "full", str(model_a))
    assert len(calls) == 2
    cache_dir = os.path.join(str(tmp_path / "sweep_cache"), "landmarks")
    assert len([n for n in os.listdir(cache_dir) if n.endswith(".pkl")]) == 2


def test_extract_landmarks_cached_re_extracts_on_implementation_fingerprint_change(
        tmp_path, monkeypatch):
    # The other missing §7.1 component: if the extraction code itself
    # changes (e.g. sweep_mediapipe_config.extract_raw_landmarks is fixed),
    # the landmark cache must miss too -- otherwise the RMSE-level cache's
    # correct miss just re-scores stale landmarks.
    trial, _video, model, calls = _landmark_cache_fixture(tmp_path, monkeypatch,
                                                          impl_fp="impl1")
    rpc.extract_landmarks_cached(trial, "full", str(model))
    monkeypatch.setattr(rpc, "compute_implementation_fingerprint", lambda: "impl2")
    rpc.extract_landmarks_cached(trial, "full", str(model))
    assert len(calls) == 2


def test_score_mediapipe_candidate_returns_rmse(monkeypatch):
    trial = {"trial_key": "k1", "leg": "left", "video_path": "v.mp4", "optitrack_path": "o.csv"}
    monkeypatch.setattr(rpc, "extract_landmarks_cached", lambda t, mv, mp_: [{"t": 0.0}])
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (np.array([0.0]), np.array([1.0])))
    monkeypatch.setattr(rpc.mediapipe_sweep, "score_frames",
                        lambda frames, opti_t, opti_ang, vis_thresh: 4.2)
    result = rpc.score_mediapipe_candidate(trial, "full", "model.task", 0.4)
    assert result == 4.2


def test_score_mediapipe_candidate_returns_none_when_unscoreable(monkeypatch):
    trial = {"trial_key": "k1", "leg": "left", "video_path": "v.mp4", "optitrack_path": "o.csv"}
    monkeypatch.setattr(rpc, "extract_landmarks_cached", lambda t, mv, mp_: [{"t": 0.0}])
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (np.array([0.0]), np.array([1.0])))
    monkeypatch.setattr(rpc.mediapipe_sweep, "score_frames",
                        lambda frames, opti_t, opti_ang, vis_thresh: None)
    result = rpc.score_mediapipe_candidate(trial, "full", "model.task", 0.4)
    assert result is None


# ── cache key + manifest persistence ─────────────────────────────────────

def test_compute_cache_key_deterministic():
    trial = {"trial_key": "k1"}
    candidate = {"beta": 0.041}
    key1 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl1")
    key2 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl1")
    assert key1 == key2


def test_compute_cache_key_differs_on_implementation_fingerprint():
    trial = {"trial_key": "k1"}
    candidate = {"beta": 0.041}
    key1 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl1")
    key2 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl2")
    assert key1 != key2


def test_compute_cache_key_differs_on_candidate():
    trial = {"trial_key": "k1"}
    key1 = rpc.compute_cache_key("imu", trial, {"beta": 0.041}, {"optitrack": "h1"}, "impl1")
    key2 = rpc.compute_cache_key("imu", trial, {"beta": 0.08}, {"optitrack": "h1"}, "impl1")
    assert key1 != key2


def test_save_and_load_sweep_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    rpc.save_sweep_cache({"key1": 3.5, "key2": 4.1})
    assert rpc.load_sweep_cache() == {"key1": 3.5, "key2": 4.1}


def test_load_sweep_cache_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "does_not_exist"))
    assert rpc.load_sweep_cache() == {}


def test_load_sweep_cache_malformed_json_treated_as_empty(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "sweep_cache"
    cache_dir.mkdir()
    (cache_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(cache_dir))
    assert rpc.load_sweep_cache() == {}


# ── rank_candidates ───────────────────────────────────────────────────────

def _cohort_and_participants(n_trials=5, n_participants=3):
    cohort = [f"t{i}" for i in range(n_trials)]
    # distribute trials across participants round-robin
    participant_of = {t: f"p{i % n_participants}" for i, t in enumerate(cohort)}
    return cohort, participant_of


def test_rank_candidates_full_coverage_wins_lower_median():
    cohort, participant_of = _cohort_and_participants()
    scores = {
        '{"beta": 0.041}': {t: 5.0 for t in cohort},
        '{"beta": 0.08}': {t: 2.0 for t in cohort},
    }
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    assert ranked[0]["candidate_key"] == '{"beta": 0.08}'
    assert ranked[0]["median_rmse"] == 2.0
    assert ranked[0]["low_coverage"] is False


def test_rank_candidates_excludes_candidate_missing_required_cohort_trial():
    cohort, participant_of = _cohort_and_participants(n_trials=5, n_participants=3)
    scores = {
        # scores only 3 of 5 cohort trials -- coverage must be full (100%
        # of the cohort), so this candidate cannot win by having an
        # easier denominator even with a very low RMSE on those it did
        # score (design spec §7.2).
        '{"beta": 0.01}': {cohort[0]: 0.1, cohort[1]: 0.1, cohort[2]: 0.1},
        '{"beta": 0.08}': {t: 3.0 for t in cohort},
    }
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    winner = [r for r in ranked if not r["low_coverage"]]
    assert len(winner) == 1
    assert winner[0]["candidate_key"] == '{"beta": 0.08}'
    low_cov = [r for r in ranked if r["low_coverage"]]
    assert low_cov[0]["candidate_key"] == '{"beta": 0.01}'


def test_rank_candidates_requires_full_coverage_not_fractional_floor():
    # Regression test for a task-review finding: a candidate that skips
    # only the hardest cohort trial (4 of 5 -- 80%) must NOT be allowed
    # to win just because it clears some fractional coverage floor. Full
    # cohort coverage is required for ranking eligibility. Here the
    # 4/5-scoring candidate has a much lower RMSE on the trials it did
    # score, but it must still lose to the fully-covering candidate.
    cohort, participant_of = _cohort_and_participants(n_trials=5, n_participants=3)
    scores = {
        '{"beta": 0.01}': {t: 0.1 for t in cohort[:4]},  # skips cohort[4]
        '{"beta": 0.08}': {t: 3.0 for t in cohort},       # full coverage
    }
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    winner = [r for r in ranked if not r["low_coverage"]]
    assert len(winner) == 1
    assert winner[0]["candidate_key"] == '{"beta": 0.08}'
    low_cov = [r for r in ranked if r["low_coverage"]]
    assert len(low_cov) == 1
    assert low_cov[0]["candidate_key"] == '{"beta": 0.01}'
    assert low_cov[0]["n_trials"] == 4


def test_rank_candidates_reports_n_trials_and_n_participants():
    cohort, participant_of = _cohort_and_participants(n_trials=5, n_participants=3)
    scores = {'{"beta": 0.08}': {t: 3.0 for t in cohort}}
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    assert ranked[0]["n_trials"] == 5
    assert ranked[0]["n_participants"] == 3


def test_rank_candidates_cohort_below_minimum_participants_returns_empty():
    cohort, participant_of = _cohort_and_participants(n_trials=2, n_participants=2)
    scores = {'{"beta": 0.08}': {t: 3.0 for t in cohort}}
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    assert ranked == []


# ── load_best_config / record_sweep_result ───────────────────────────────

def test_load_best_config_missing_file_returns_empty_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "missing.json"))
    cfg = rpc.load_best_config()
    assert cfg == {"mediapipe": None, "imu": None, "history": []}


def test_record_sweep_result_promotes_first_valid_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    ranked = [{"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
              "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    result = rpc.record_sweep_result("imu", ranked, "ds1", "impl1")
    assert result["promoted"] is True
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.041}'
    assert cfg["imu"]["rmse"] == 5.0
    assert len(cfg["history"]) == 1
    assert cfg["history"][0]["dataset_fingerprint"] == "ds1"
    assert cfg["history"][0]["implementation_fingerprint"] == "impl1"


def test_record_sweep_result_does_not_promote_within_epsilon(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    first = [{"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
             "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    rpc.record_sweep_result("imu", first, "ds1", "impl1")
    # Incumbent re-scored at 5.0 again, challenger only 0.05 better -- below
    # the default 0.1 epsilon, must not promote.
    second = [
        {"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
         "n_trials": 5, "n_participants": 3, "low_coverage": False},
        {"candidate_key": '{"beta": 0.08}', "median_rmse": 4.95,
         "n_trials": 5, "n_participants": 3, "low_coverage": False},
    ]
    result = rpc.record_sweep_result("imu", second, "ds2", "impl1")
    assert result["promoted"] is False
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.041}'
    assert len(cfg["history"]) == 1  # no new entry on a non-promotion


def test_record_sweep_result_incumbent_unrankable_promotes_best_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    first = [{"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
             "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    rpc.record_sweep_result("imu", first, "ds1", "impl1")
    # Design spec §5's edge case: the incumbent's exact config is no longer
    # in this sweep's ranked results at all (e.g. dropped from a hand-edited
    # grid) -- must not keep the stale RMSE, must promote the best valid
    # candidate from this sweep instead.
    second = [{"candidate_key": '{"beta": 0.08}', "median_rmse": 6.0,
              "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    result = rpc.record_sweep_result("imu", second, "ds2", "impl1")
    assert result["promoted"] is True
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.08}'


def test_record_sweep_result_no_valid_candidate_sets_unavailable(tmp_path, monkeypatch):
    # No incumbent was ever recorded, and this sweep has no valid candidate
    # either -- stays unavailable (None). Nothing to protect here since
    # there was never a good value to lose.
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    ranked = [{"candidate_key": '{"beta": 0.041}', "median_rmse": None,
              "n_trials": 1, "n_participants": 1, "low_coverage": True}]
    result = rpc.record_sweep_result("imu", ranked, "ds1", "impl1")
    assert result["promoted"] is False
    assert result["reason"] == "no_valid_candidate"
    cfg = rpc.load_best_config()
    assert cfg["imu"] is None


def test_record_sweep_result_no_valid_candidate_does_not_wipe_existing_incumbent(tmp_path, monkeypatch):
    # Design fix (confirmed after task review): full cohort coverage means
    # a single trial going unscoreable can knock every candidate --
    # including the incumbent -- out of contention for one sweep. That is
    # inconclusive, not a demotion: a previously-recorded incumbent must
    # survive untouched, never wiped to None over a transient failure.
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    first = [{"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
             "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    rpc.record_sweep_result("imu", first, "ds1", "impl1")

    second = [{"candidate_key": '{"beta": 0.041}', "median_rmse": None,
              "n_trials": 4, "n_participants": 3, "low_coverage": True}]
    result = rpc.record_sweep_result("imu", second, "ds2", "impl1")
    assert result["promoted"] is False
    assert result["reason"] == "no_valid_candidate"
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.041}'
    assert cfg["imu"]["rmse"] == 5.0
    assert len(cfg["history"]) == 1  # no new history entry for an inconclusive sweep


# ── run_full_sweep orchestration ─────────────────────────────────────────

def _stub_pipeline(monkeypatch, tmp_path, trials, imu_rmse_by_config, mp_rmse_by_config):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    monkeypatch.setattr(rpc, "RMSE_TRACKING_DIR", str(tmp_path / "RMSE_Tracking"))
    monkeypatch.setattr(rpc, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(rpc, "discover_scorable_trials", lambda: trials)
    monkeypatch.setattr(rpc, "compute_implementation_fingerprint", lambda: "impl1")
    monkeypatch.setattr(
        rpc, "compute_input_fingerprints",
        lambda trial, methodology, cache, force=False, model_path=None: {"optitrack": "h"})

    import sweep_imu_config
    import sweep_mediapipe_config
    monkeypatch.setattr(sweep_imu_config, "WIDE_GRID", [{"beta": 0.041}, {"beta": 0.08}])
    monkeypatch.setattr(sweep_mediapipe_config, "MODEL_VARIANTS", ["full"])
    monkeypatch.setattr(sweep_mediapipe_config, "VIS_THRESH_CANDIDATES", [0.4])

    # run_full_sweep derives a real per-variant model file path under
    # BASE_DIR/models/mediapipe and skips a variant whose file is missing
    # (task-11-review fix) -- create the default "full" variant's
    # placeholder so these stub-driven tests keep exercising the scored
    # path rather than the skip path.
    model_dir = tmp_path / "models" / "mediapipe"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "pose_landmarker_full.task").write_bytes(b"")

    def fake_score_imu(trial, params):
        return imu_rmse_by_config.get((trial["trial_key"], json.dumps(params, sort_keys=True)))
    monkeypatch.setattr(rpc, "score_imu_candidate", fake_score_imu)

    def fake_score_mp(trial, model_variant, model_path, vis_thresh):
        key = json.dumps({"model_variant": model_variant, "vis_thresh": vis_thresh}, sort_keys=True)
        return mp_rmse_by_config.get((trial["trial_key"], key))
    monkeypatch.setattr(rpc, "score_mediapipe_candidate", fake_score_mp)
    monkeypatch.setattr(rpc, "_make_figures", lambda *a, **k: None)


def _trial(key, participant, has_imu=True, has_mp=True):
    return {"trial_key": key, "participant": participant, "leg": "left",
           "condition": "post", "session": "post", "position": "1", "height": "none",
           "trial_number": "1", "imu_anchor_path": "a", "imu_component_paths": {"imu": "i"},
           "video_path": "v.mp4" if has_mp else None,
           "optitrack_path": "o.csv", "has_imu_rmse": has_imu, "has_mediapipe_rmse": has_mp,
           "exclusion_reasons": []}


def test_run_full_sweep_ranks_and_promotes(tmp_path, monkeypatch):
    trials = [_trial(f"k{i}", f"p{i % 3}") for i in range(5)]
    imu_scores = {}
    mp_scores = {}
    for t in trials:
        imu_scores[(t["trial_key"], '{"beta": 0.041}')] = 5.0
        imu_scores[(t["trial_key"], '{"beta": 0.08}')] = 3.0
        mp_scores[(t["trial_key"], '{"model_variant": "full", "vis_thresh": 0.4}')] = 6.0
    _stub_pipeline(monkeypatch, tmp_path, trials, imu_scores, mp_scores)

    result = rpc.run_full_sweep()

    assert result["imu"]["promoted"] is True
    assert result["mediapipe"]["promoted"] is True
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.08}'
    assert os.path.isfile(os.path.join(str(tmp_path / "RMSE_Tracking"), "rmse_sweep_results.csv"))


def test_run_full_sweep_handles_no_trials(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, tmp_path, [], {}, {})
    result = rpc.run_full_sweep()
    # With zero trials, rank_candidates returns [] for both cohorts (below
    # min_participants), so record_sweep_result has no valid candidate to
    # promote -- must distinguish "nothing to rank" from "challenger lost"
    # (within_epsilon), not just report promoted=False by coincidence.
    assert result["imu"]["promoted"] is False
    assert result["imu"]["reason"] == "no_valid_candidate"
    assert result["mediapipe"]["promoted"] is False
    assert result["mediapipe"]["reason"] == "no_valid_candidate"


def test_run_full_sweep_isolates_per_trial_scoring_failure(tmp_path, monkeypatch, capsys):
    trials = [_trial(f"k{i}", f"p{i % 3}") for i in range(5)]
    imu_scores = {}
    mp_scores = {}
    for t in trials:
        imu_scores[(t["trial_key"], '{"beta": 0.08}')] = 3.0
        mp_scores[(t["trial_key"], '{"model_variant": "full", "vis_thresh": 0.4}')] = 6.0
    _stub_pipeline(monkeypatch, tmp_path, trials, imu_scores, mp_scores)

    def raising_score_imu(trial, params):
        if params == {"beta": 0.041}:
            raise ValueError("corrupt CSV")
        return imu_scores.get((trial["trial_key"], json.dumps(params, sort_keys=True)))
    monkeypatch.setattr(rpc, "score_imu_candidate", raising_score_imu)

    result = rpc.run_full_sweep()   # must not raise
    assert result["imu"]["promoted"] is True


def test_run_full_sweep_mediapipe_scores_each_variant_against_its_own_model_file(tmp_path, monkeypatch):
    # Task-11-review bug: model_path used to be computed once outside the
    # per-variant loop, so every variant ("lite"/"full"/"heavy") was
    # secretly scored using the "full" model's weights. Verify each variant
    # is scored with a model_path that actually points at that variant's
    # own file, and that a variant with no model file on disk is skipped
    # rather than crashing the sweep.
    trials = [_trial(f"k{i}", f"p{i % 3}") for i in range(5)]
    imu_scores = {}
    mp_scores = {}
    for t in trials:
        imu_scores[(t["trial_key"], '{"beta": 0.08}')] = 3.0
        mp_scores[(t["trial_key"], '{"model_variant": "lite", "vis_thresh": 0.4}')] = 6.0
        # "heavy" would score better if it ran, but its model file is
        # deliberately absent below -- it must never be scored at all.
        mp_scores[(t["trial_key"], '{"model_variant": "heavy", "vis_thresh": 0.4}')] = 1.0
    _stub_pipeline(monkeypatch, tmp_path, trials, imu_scores, mp_scores)

    import sweep_mediapipe_config
    monkeypatch.setattr(sweep_mediapipe_config, "MODEL_VARIANTS", ["lite", "heavy"])

    model_paths_seen = []

    def fake_score_mp(trial, model_variant, model_path, vis_thresh):
        model_paths_seen.append((model_variant, model_path))
        key = json.dumps({"model_variant": model_variant, "vis_thresh": vis_thresh}, sort_keys=True)
        return mp_scores.get((trial["trial_key"], key))
    monkeypatch.setattr(rpc, "score_mediapipe_candidate", fake_score_mp)

    # _stub_pipeline already created pose_landmarker_full.task; only add
    # "lite"'s file, leaving "heavy" missing on purpose.
    model_dir = tmp_path / "models" / "mediapipe"
    (model_dir / "pose_landmarker_lite.task").write_bytes(b"")

    result = rpc.run_full_sweep()

    assert result["mediapipe"]["promoted"] is True
    cfg = rpc.load_best_config()
    assert cfg["mediapipe"]["config"] == '{"model_variant": "lite", "vis_thresh": 0.4}'

    variants_scored = {v for v, _ in model_paths_seen}
    assert variants_scored == {"lite"}  # "heavy" skipped, never scored
    lite_model_paths = {p for v, p in model_paths_seen if v == "lite"}
    assert lite_model_paths == {str(model_dir / "pose_landmarker_lite.task")}


def test_make_figures_writes_three_png_files_without_stubbing(tmp_path, monkeypatch):
    # Task-11-review bug: _savefig_atomic passed a ".tmp"-suffixed path
    # straight to fig.savefig() with no format= kwarg, so matplotlib could
    # not infer the output format and every real call raised
    # ValueError: Format 'tmp' is not supported. Every run_full_sweep test
    # stubs _make_figures out entirely, so this never executed in the
    # existing suite -- this test calls it for real.
    monkeypatch.setattr(rpc, "RMSE_TRACKING_DIR", str(tmp_path / "RMSE_Tracking"))
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    os.makedirs(str(tmp_path / "RMSE_Tracking"), exist_ok=True)

    trials = [_trial("k0", "p0"), _trial("k1", "p1")]
    imu_ranked = [{"candidate_key": '{"beta": 0.08}', "median_rmse": 3.0,
                  "n_trials": 2, "n_participants": 2, "low_coverage": False}]
    mp_ranked = [{"candidate_key": '{"model_variant": "full", "vis_thresh": 0.4}',
                 "median_rmse": 6.0, "n_trials": 2, "n_participants": 2, "low_coverage": False}]

    rpc._make_figures(imu_ranked, mp_ranked, trials, ["k0", "k1"], ["k0", "k1"])

    out_dir = str(tmp_path / "RMSE_Tracking")
    for name in ("rmse_trend.png", "sweep_heatmap.png", "imu_vs_mediapipe_rmse.png"):
        path = os.path.join(out_dir, name)
        assert os.path.isfile(path)
        assert os.path.getsize(path) > 0
        assert not os.path.isfile(path + ".tmp")  # no leftover temp file
