# tests/test_camera_utils.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import cv2
import camera_utils


def test_camera_backends_lists_msmf_then_dshow():
    names = [name for name, _flag in camera_utils.CAMERA_BACKENDS]
    assert names == ["MSMF", "DSHOW"]


def test_max_camera_index_is_five():
    assert camera_utils.MAX_CAMERA_INDEX == 5


def test_read_with_warmup_returns_first_good_frame():
    class _Cap:
        def __init__(self):
            self.reads = 0
        def read(self):
            self.reads += 1
            if self.reads < 3:
                return False, None
            return True, "a-frame"
    cap = _Cap()
    ok, frame = camera_utils.read_with_warmup(cap, attempts=5, delay=0.0)
    assert ok is True
    assert frame == "a-frame"
    assert cap.reads == 3


def test_read_with_warmup_gives_up_after_attempts():
    class _Cap:
        def read(self):
            return False, None
    ok, frame = camera_utils.read_with_warmup(_Cap(), attempts=3, delay=0.0)
    assert ok is False
    assert frame is None


def test_enumerate_cameras_returns_list_of_dicts_or_empty():
    # No real hardware assumed in CI — just confirm the function runs and
    # returns the documented shape when it does find something.
    found = camera_utils.enumerate_cameras()
    assert isinstance(found, list)
    for cam in found:
        assert set(cam.keys()) == {"index", "backend", "backend_name", "label"}
        assert cam["backend_name"] in ("MSMF", "DSHOW")
