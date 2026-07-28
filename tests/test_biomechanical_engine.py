# tests/test_biomechanical_engine.py
import math, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pendulastic_app as _app
from pendulastic_app import BiomechanicalEngine


def _make_fake_imu(pitch: float):
    m = types.SimpleNamespace()
    m.get_state = lambda: {
        "distal":   {"pitch": pitch, "roll": 0.0, "yaw": 0.0},
        "proximal": {"pitch": 10.0,  "roll": 0.0, "yaw": 0.0},
    }
    return m


def test_imu_returns_distal_pitch(monkeypatch):
    monkeypatch.setattr(_app, "_IMU_AVAIL", True)
    monkeypatch.setattr(_app, "_imu", _make_fake_imu(42.7))
    assert BiomechanicalEngine("imu").get_live_angle() == 42.7


def test_imu_no_proximal_subtraction(monkeypatch):
    """Shank-only: distal pitch 42.7 is returned regardless of proximal 10.0."""
    monkeypatch.setattr(_app, "_IMU_AVAIL", True)
    monkeypatch.setattr(_app, "_imu", _make_fake_imu(42.7))
    angle = BiomechanicalEngine("imu").get_live_angle()
    assert angle == 42.7          # NOT 42.7 - 10.0


def test_imu_unavailable_returns_nan(monkeypatch):
    monkeypatch.setattr(_app, "_IMU_AVAIL", False)
    assert math.isnan(BiomechanicalEngine("imu").get_live_angle())


def test_optitrack_returns_nan():
    assert math.isnan(BiomechanicalEngine("optitrack").get_live_angle())


def test_rgb_returns_nan():
    assert math.isnan(BiomechanicalEngine("rgb").get_live_angle())


def test_methodology_stored():
    assert BiomechanicalEngine("rgb").methodology == "rgb"
