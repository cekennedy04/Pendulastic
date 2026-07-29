# tests/test_biomechanical_engine.py
import math, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pendulastic_app as _app
from pendulastic_app import BiomechanicalEngine


def _make_fake_imu(pitch: float):
    """Create a fake IMU. pitch param now represents swing_angle_deg for the new interface."""
    m = types.SimpleNamespace()
    m.get_state = lambda: {
        "swing_angle_deg": pitch,
        "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        "proximal": {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
        "angles":   {"pitch": pitch, "roll": 0.0, "yaw": 0.0, "paired": False},
    }
    return m


def test_imu_returns_distal_pitch(monkeypatch):
    monkeypatch.setattr(_app, "_IMU_AVAIL", True)
    monkeypatch.setattr(_app, "_imu", _make_fake_imu(42.7))
    assert BiomechanicalEngine("imu").get_live_angle() == 137.3


def test_imu_no_proximal_subtraction(monkeypatch):
    """Shank-only: swing_angle_deg 42.7 maps to 180 - 42.7 = 137.3."""
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
