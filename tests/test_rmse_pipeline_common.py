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
    trial = {"imu_component_paths": None, "optitrack_path": str(opti), "video_path": str(video)}
    fps = rpc.compute_input_fingerprints(trial, "mediapipe", {})
    assert "video" in fps and "optitrack" in fps
    assert "imu" not in fps


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

def test_extract_landmarks_cached_calls_extraction_once(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(rpc.mediapipe_sweep, "extract_raw_landmarks",
                        lambda vp, leg, mp_: (calls.append(1) or [{"t": 0.0}]))
    trial = {"trial_key": "k1", "leg": "left", "video_path": str(video)}
    rpc.extract_landmarks_cached(trial, "full", "model.task")
    rpc.extract_landmarks_cached(trial, "full", "model.task")
    assert len(calls) == 1


def test_extract_landmarks_cached_re_extracts_on_video_change(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(rpc.mediapipe_sweep, "extract_raw_landmarks",
                        lambda vp, leg, mp_: (calls.append(1) or [{"t": 0.0}]))
    trial = {"trial_key": "k1", "leg": "left", "video_path": str(video)}
    rpc.extract_landmarks_cached(trial, "full", "model.task")
    video.write_bytes(b"different content, changes the hash")
    rpc.extract_landmarks_cached(trial, "full", "model.task")
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
