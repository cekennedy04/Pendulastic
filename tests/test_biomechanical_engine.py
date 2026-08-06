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
    """collect_landmarks=True returns (angles, landmarks), same length,
    each landmark entry a 3-tuple or None."""
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
    angles, landmarks = engine.run_offline_track(
        video_path, lambda p: None, leg="right", collect_landmarks=True)

    assert len(angles) == 5
    assert len(landmarks) == 5
    for lm in landmarks:
        assert lm is None or len(lm) == 3


def test_run_offline_track_default_returns_list_not_tuple(monkeypatch):
    """collect_landmarks defaults to False -- return type must stay a plain
    list, matching every existing caller's expectation."""
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", False)
    result = BiomechanicalEngine("rgb").run_offline_track(
        "nonexistent.mp4", lambda p: None, leg="right")
    assert result == []
    assert isinstance(result, list)
