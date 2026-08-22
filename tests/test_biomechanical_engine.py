# tests/test_biomechanical_engine.py
import math, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pendulastic_app as _app
from pendulastic_app import BiomechanicalEngine


def _make_fake_imu(swing_angle_deg: float):
    """Create a fake IMU state dict with quaternion swing_angle_deg interface."""
    m = types.SimpleNamespace()
    m.get_state = lambda: {
        "swing_angle_deg": swing_angle_deg,
        "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        "proximal": {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
        "angles":   {},  # Stub: old Euler angles no longer used by get_live_angle()
    }
    return m


def test_imu_returns_distal_pitch(monkeypatch):
    """swing_angle_deg 42.7 maps to 180 - 42.7 = 137.3 (clinical convention)."""
    monkeypatch.setattr(_app, "_IMU_AVAIL", True)
    monkeypatch.setattr(_app, "_imu", _make_fake_imu(42.7))
    assert BiomechanicalEngine("imu").get_live_angle() == 137.3


def test_imu_no_proximal_subtraction(monkeypatch):
    """Shank-only: swing_angle_deg 42.7 maps to 180 - 42.7 = 137.3 (no proximal involved)."""
    monkeypatch.setattr(_app, "_IMU_AVAIL", True)
    monkeypatch.setattr(_app, "_imu", _make_fake_imu(42.7))
    angle = BiomechanicalEngine("imu").get_live_angle()
    assert angle == 137.3


def test_imu_unavailable_returns_nan(monkeypatch):
    monkeypatch.setattr(_app, "_IMU_AVAIL", False)
    assert math.isnan(BiomechanicalEngine("imu").get_live_angle())


def test_optitrack_returns_nan():
    assert math.isnan(BiomechanicalEngine("optitrack").get_live_angle())


def test_rgb_returns_nan():
    assert math.isnan(BiomechanicalEngine("rgb").get_live_angle())


def test_methodology_stored():
    assert BiomechanicalEngine("rgb").methodology == "rgb"


import pytest
try:
    import cv2 as _cv2_test
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_returns_angle_per_frame(tmp_path, monkeypatch):
    """run_offline_track returns one float per video frame via mocked tracker."""
    import numpy as np, types

    # Write a tiny 5-frame video to disk
    video_path = str(tmp_path / "test.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(5):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    # Fake patient detector returns a 17x2 COCO keypoints array
    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]    # right hip
    kps[14] = [160, 120]   # right knee
    kps[16] = [160, 200]   # right ankle

    class FakeDetector:
        def detect(self, frame):
            return kps, None  # (patient_kps, assessor_kps)

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0  # hip, knee, ankle, angle

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    progress = []
    angles = engine.run_offline_track(video_path,
                                      lambda p: progress.append(p),
                                      leg="right")

    assert len(angles) == 5
    assert all(a == 160.0 for a in angles)
    assert progress and progress[-1] == 1.0


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_collect_landmarks_returns_tuple(tmp_path, monkeypatch):
    """collect_landmarks=True returns (angles, landmarks, fps, detected),
    angles/landmarks/detected all the same length, each landmark entry a
    3-tuple or None, and fps matching the source video's true frame rate."""
    import numpy as np

    video_path = str(tmp_path / "test2.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(5):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]
    kps[14] = [160, 120]
    kps[16] = [160, 200]

    class FakeDetector:
        def detect(self, frame):
            return kps, None

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    angles, landmarks, fps, detected = engine.run_offline_track(
        video_path, lambda p: None, leg="right", collect_landmarks=True)

    assert len(angles) == 5
    assert len(landmarks) == 5
    assert len(detected) == 5
    for lm in landmarks:
        assert lm is None or len(lm) == 3
    assert abs(fps - 30.0) < 1.0
    # FakeTracker has no last_detected attribute -- defaults to True so
    # trackers that don't implement the flag keep behaving as before.
    assert all(detected)


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_detected_false_when_tracker_loses_person(tmp_path, monkeypatch):
    """When the tracker's last_detected flips to False (no pose found this
    frame -- e.g. _MPBatchTracker.step() fell through to a frozen prior
    position), run_offline_track must report detected[i] == False for that
    frame even though angles/landmarks stay populated (frozen value) for
    curve continuity. This is the only reliable signal callers have for
    "no person detected here" -- angles alone can't distinguish a real
    track from a frozen one."""
    import numpy as np

    video_path = str(tmp_path / "lost_person.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(4):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]
    kps[14] = [160, 120]
    kps[16] = [160, 200]

    class FakeDetector:
        def detect(self, frame):
            return kps, None

    class FakeTracker:
        """Simulates a real _MPBatchTracker that finds the person on frame 0
        (last_detected True) but loses them on every frame after (frozen
        angle, last_detected False) -- exactly the silent-freeze scenario
        that used to produce an all-finite curve with no failure signal."""
        def __init__(self, side, fps):
            self.last_detected = True
            self._n = 0
        def init(self, frame, hip, knee, ankle):
            pass
        def step(self, frame):
            self.last_detected = (self._n == 0)
            self._n += 1
            return kps[12], kps[14], kps[16], 160.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    angles, landmarks, fps, detected = engine.run_offline_track(
        video_path, lambda p: None, leg="right", collect_landmarks=True)

    assert len(detected) == 4
    assert detected == [True, False, False, False]
    # angles/landmarks stay populated (frozen) even on undetected frames --
    # detected is the only signal distinguishing real tracking from frozen.
    assert all(math.isfinite(a) for a in angles)


def test_run_offline_track_default_returns_list_not_tuple(monkeypatch):
    """collect_landmarks defaults to False -- return type must stay a plain
    list, matching every existing caller's expectation."""
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", False)
    result = BiomechanicalEngine("rgb").run_offline_track(
        "nonexistent.mp4", lambda p: None, leg="right")
    assert result == []
    assert isinstance(result, list)


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_detect_people_at_frame_returns_poses(tmp_path, monkeypatch):
    import numpy as np
    video_path = str(tmp_path / "people_test.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    for _ in range(3):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    fake_poses = [["pose1_landmarks"], ["pose2_landmarks"]]

    class _FakeDetector:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def detect(self, image):
            return types.SimpleNamespace(pose_landmarks=fake_poses)

    class _FakePoseLandmarker:
        @staticmethod
        def create_from_options(opts):
            return _FakeDetector()

    class _FakeRunningMode:
        IMAGE = "IMAGE"

    class _FakeVision:
        PoseLandmarkerOptions = staticmethod(lambda **kw: kw)
        PoseLandmarker = _FakePoseLandmarker
        RunningMode = _FakeRunningMode

    class _FakeTasks:
        vision = _FakeVision
        BaseOptions = staticmethod(lambda **kw: kw)

    fake_mp = types.SimpleNamespace(
        tasks=_FakeTasks,
        Image=lambda **kw: kw,
        ImageFormat=types.SimpleNamespace(SRGB="SRGB"),
    )

    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)
    monkeypatch.setattr(_app, "_mp", fake_mp)
    monkeypatch.setattr(_app, "_MP_MODEL", "fake_model_path")

    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame(video_path, frame_index=0)

    assert frame is not None
    assert frame.shape == (240, 320, 3)
    assert poses == fake_poses


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_detect_people_at_frame_returns_empty_for_bad_video():
    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame("nonexistent_file.mp4")
    assert frame is None
    assert poses == []


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_detect_people_at_frame_returns_empty_past_end_of_clip(tmp_path):
    import numpy as np
    video_path = str(tmp_path / "short.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame(video_path, frame_index=999)
    assert frame is None
    assert poses == []


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_detect_people_at_frame_catches_detection_exception(tmp_path, monkeypatch):
    import numpy as np
    video_path = str(tmp_path / "people_test2.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    class _RaisingOptions:
        def __init__(self, **kw):
            raise RuntimeError("model load failed")

    class _RaisingVision:
        PoseLandmarkerOptions = _RaisingOptions

    class _RaisingTasks:
        vision = _RaisingVision
        BaseOptions = staticmethod(lambda **kw: kw)

    fake_mp = types.SimpleNamespace(tasks=_RaisingTasks)

    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)
    monkeypatch.setattr(_app, "_mp", fake_mp)
    monkeypatch.setattr(_app, "_MP_MODEL", "fake_model_path")

    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame(video_path)

    assert frame is not None    # frame WAS read successfully
    assert poses == []          # detection failure -> empty poses, no raise


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_manual_seed_skips_patient_detector(tmp_path, monkeypatch):
    """When manual_seed is given, tracker.init is called with exactly that
    seed on the first frame -- _PatientDetector.detect must never be
    called at all."""
    import numpy as np
    video_path = str(tmp_path / "seed_test.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    for _ in range(4):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    detector_called = {"count": 0}

    class FakeDetector:
        def detect(self, frame):
            detector_called["count"] += 1
            return None, None   # would normally block initialisation

    init_calls = []

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle):
            init_calls.append((hip, knee, ankle))
        def step(self, frame):
            return (10, 20), (30, 40), (50, 60), 155.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    seed = ((100.0, 60.0), (100.0, 120.0), (100.0, 200.0))
    engine = BiomechanicalEngine("rgb")
    angles = engine.run_offline_track(
        video_path, lambda p: None, leg="right", manual_seed=seed)

    assert detector_called["count"] == 0
    assert len(init_calls) == 1
    assert init_calls[0] == seed
    assert len(angles) == 4
    assert all(a == 155.0 for a in angles)


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_none_seed_unaffected(monkeypatch):
    """manual_seed defaulting to None must not change the existing
    auto-detect behavior or return type for any pre-existing caller."""
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", False)
    result = BiomechanicalEngine("rgb").run_offline_track(
        "nonexistent.mp4", lambda p: None, leg="right")
    assert result == []
    assert isinstance(result, list)


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_start_frame_seeks_and_returns_suffix_only(tmp_path, monkeypatch):
    """start_frame=N seeks the video and returns only the N..end suffix,
    with progress still reaching 1.0."""
    import numpy as np

    video_path = str(tmp_path / "test_start_frame.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(10):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]
    kps[14] = [160, 120]
    kps[16] = [160, 200]

    class FakeDetector:
        def detect(self, frame):
            return kps, None

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    progress = []
    seed = (kps[12], kps[14], kps[16])
    angles, landmarks, fps, detected = engine.run_offline_track(
        video_path, lambda p: progress.append(p), leg="right",
        collect_landmarks=True, manual_seed=seed, start_frame=4)

    assert len(angles) == 6          # 10 total frames - start_frame=4
    assert len(landmarks) == 6
    assert len(detected) == 6
    assert progress and progress[-1] == 1.0


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_start_frame_zero_matches_existing_behavior(tmp_path, monkeypatch):
    """start_frame=0 (the default) must return the same length as before
    this parameter existed -- a pure regression guard."""
    import numpy as np

    video_path = str(tmp_path / "test_start_frame_zero.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(7):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]
    kps[14] = [160, 120]
    kps[16] = [160, 200]

    class FakeDetector:
        def detect(self, frame):
            return kps, None

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    progress = []
    angles = engine.run_offline_track(
        video_path, lambda p: progress.append(p), leg="right")

    assert len(angles) == 7
    assert progress[-1] == 1.0
