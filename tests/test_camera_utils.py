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
        # One frame may already have been snapshotted out of the lock right
        # as detach_writer() ran, but writing must not continue past that.
        assert len(writer.frames) <= n_at_detach + 1, \
            "writer kept receiving frames after detach_writer()"
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
    try:
        assert sess.open(_FAKE_CAM)
        assert got_lost.wait(timeout=5.0), "expected a 'lost' status after repeated read failures"
        assert sess.active is None
        assert caps[0].released is True
    finally:
        sess.close()   # still safe afterward; also prevents a leaked thread on timeout


class _BlockingFakeCap:
    """cv2.VideoCapture-like stub whose read() blocks on an Event until the
    test releases it — used to simulate a stalled MSMF read that outlives
    close()'s join timeout."""
    def __init__(self, release_evt):
        self._release_evt = release_evt
        self.released = False
        self._reads = 0

    def isOpened(self):
        return True

    def read(self):
        self._reads += 1
        if self._reads <= 3:
            # First few reads succeed immediately: one is consumed by
            # open()'s warm-up (read_with_warmup, called on the caller's
            # thread before the read loop even starts), and at least one
            # more happens on the read-loop thread so on_frame actually
            # fires before the read that blocks.
            return True, f"frame-{self._reads}"
        self._release_evt.wait()   # blocks "forever" until the test lets go
        return True, "frame-blocked"

    def release(self):
        self.released = True

    def get(self, prop_id):
        return 640.0 if prop_id == cv2.CAP_PROP_FRAME_WIDTH else 480.0


def test_close_detaches_from_a_stalled_read_without_use_after_release_race():
    """If the read thread is blocked inside cap.read() past close()'s join
    timeout, close() must not release the capture out from under it (a
    native use-after-release crash) and must not leave the session's state
    pointing at the old capture — it detaches immediately instead."""
    release_evt = threading.Event()
    cap = _BlockingFakeCap(release_evt)
    got_frame = threading.Event()
    sess = camera_utils.CameraSession(
        on_frame=lambda f: got_frame.set(),
        capture_factory=lambda idx, backend: cap)
    try:
        assert sess.open(_FAKE_CAM) is True
        assert got_frame.wait(timeout=2.0), "on_frame was never called"

        # The read thread is now blocked inside its second cap.read(). close()
        # should give up after its 2s join timeout rather than hang forever
        # or release the capture while the thread is still using it.
        start = time.time()
        sess.close()
        elapsed = time.time() - start
        assert elapsed < 4.0, "close() should return promptly after its join timeout"

        # Session state must already be cleared, immediately on return from
        # close() — not waiting for the orphaned thread to ever finish.
        assert sess.active is None
        assert sess._cap is None
        # The orphaned thread must NOT have released the capture yet — it's
        # still blocked inside read(). This is the crux of the race: close()
        # must not release out from under an in-flight read().
        assert cap.released is False, \
            "close() (or anything else) released the capture while the " \
            "orphaned thread was still blocked inside cap.read()"
    finally:
        # Let the orphan's blocked read() return so it can exit its loop and
        # release its own capture — don't leak a hung thread into other tests.
        release_evt.set()
        deadline = time.time() + 2.0
        while not cap.released and time.time() < deadline:
            time.sleep(0.01)
        assert cap.released is True, "orphaned thread must release its own capture on exit"


def test_open_twice_releases_first_capture_and_routes_frames_from_second_only():
    caps = []
    frames_by_cap_index = {}

    def factory(idx, backend):
        c = _FakeCap()
        caps.append(c)
        return c

    frame_log = []

    def on_frame(f):
        frame_log.append(f)

    sess = camera_utils.CameraSession(on_frame=on_frame, capture_factory=factory)
    try:
        assert sess.open(_FAKE_CAM) is True
        # Let the first capture stream a bit.
        time.sleep(0.1)
        first_cap = caps[0]

        assert sess.open(_FAKE_CAM) is True
        # open()'s internal close() joins the (fast, non-blocking) first
        # read thread within its timeout, so by the time open() returns the
        # first capture must already be released.
        assert first_cap.released is True, \
            "first capture must be released by the time the second open() returns"

        second_cap = caps[1]
        assert second_cap.released is False

        # Record how many frames have arrived so far, then confirm all
        # further frames originate from the second capture only.
        n_before = len(frame_log)
        time.sleep(0.1)
        assert len(frame_log) > n_before, "no frames arrived from the second capture"
        # _FakeCap frames are tagged "frame-N" using that instance's own
        # read counter, so we can't distinguish by content alone here — the
        # authoritative check is that the first capture's read() count froze
        # once it was released.
        first_reads_at_release = first_cap._reads
        time.sleep(0.1)
        assert first_cap._reads == first_reads_at_release, \
            "first (closed) capture kept being read from after the second open()"
    finally:
        sess.close()
