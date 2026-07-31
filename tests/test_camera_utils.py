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


import threading
import time


class _FakeCap:
    """Minimal cv2.VideoCapture-like stub. `fail_after=N` makes read()
    return (False, None) starting on the (N+1)th call; None means never."""
    def __init__(self, opens=True, fail_after=None):
        self._opens = opens
        self._fail_after = fail_after
        self._reads = 0
        self.released = False

    def isOpened(self):
        return self._opens

    def read(self):
        self._reads += 1
        if self._fail_after is not None and self._reads > self._fail_after:
            return False, None
        return True, f"frame-{self._reads}"

    def release(self):
        self.released = True

    def get(self, prop_id):
        return 640.0 if prop_id == cv2.CAP_PROP_FRAME_WIDTH else 480.0


_FAKE_CAM = {"index": 0, "backend": 0, "backend_name": "FAKE", "label": "Fake Camera"}


def test_camera_session_open_streams_frames_to_on_frame():
    got_frame = threading.Event()
    frames = []
    def on_frame(f):
        frames.append(f)
        got_frame.set()
    sess = camera_utils.CameraSession(
        on_frame=on_frame, capture_factory=lambda idx, backend: _FakeCap())
    try:
        assert sess.open(_FAKE_CAM) is True
        assert sess.active == _FAKE_CAM
        assert sess.frame_size == (640, 480)
        assert got_frame.wait(timeout=2.0), "on_frame was never called"
        assert len(frames) >= 1
    finally:
        sess.close()


def test_camera_session_open_returns_false_when_capture_wont_open():
    statuses = []
    sess = camera_utils.CameraSession(
        on_frame=lambda f: None, on_status=statuses.append,
        capture_factory=lambda idx, backend: _FakeCap(opens=False))
    assert sess.open(_FAKE_CAM) is False
    assert sess.active is None
    assert statuses, "on_status must be called on a failed open"


def test_attach_writer_writes_frames_without_closing_capture():
    got_frame = threading.Event()
    caps = []
    def factory(idx, backend):
        c = _FakeCap()
        caps.append(c)
        return c
    sess = camera_utils.CameraSession(
        on_frame=lambda f: got_frame.set(), capture_factory=factory)
    try:
        assert sess.open(_FAKE_CAM)
        assert got_frame.wait(timeout=2.0)

        class _FakeWriter:
            def __init__(self):
                self.frames = []
            def write(self, f):
                self.frames.append(f)

        writer = _FakeWriter()
        sess.attach_writer(writer)
        time.sleep(0.1)
        assert len(writer.frames) > 0, "attached writer never received a frame"
        assert caps[0].released is False, "capture must not close while writer is attached"

        detached = sess.detach_writer()
        assert detached is writer
        n_at_detach = len(writer.frames)
        time.sleep(0.1)
        assert caps[0].released is False, "capture must keep streaming after detach"
        assert sess.active is not None
    finally:
        sess.close()


def test_close_is_idempotent_and_releases_capture():
    caps = []
    def factory(idx, backend):
        c = _FakeCap()
        caps.append(c)
        return c
    sess = camera_utils.CameraSession(on_frame=lambda f: None, capture_factory=factory)
    assert sess.open(_FAKE_CAM)
    sess.close()
    assert caps[0].released is True
    assert sess.active is None
    sess.close()   # idempotent — must not raise

    never_opened = camera_utils.CameraSession(on_frame=lambda f: None)
    never_opened.close()   # also safe when nothing was ever opened


def test_repeated_read_failures_trigger_lost_status_and_self_release():
    statuses = []
    got_lost = threading.Event()
    def on_status(msg):
        statuses.append(msg)
        if msg == "lost":
            got_lost.set()
    caps = []
    def factory(idx, backend):
        c = _FakeCap(fail_after=2)
        caps.append(c)
        return c
    sess = camera_utils.CameraSession(
        on_frame=lambda f: None, on_status=on_status, capture_factory=factory)
    assert sess.open(_FAKE_CAM)
    assert got_lost.wait(timeout=5.0), "expected a 'lost' status after repeated read failures"
    assert sess.active is None
    assert caps[0].released is True
    sess.close()   # still safe afterward
