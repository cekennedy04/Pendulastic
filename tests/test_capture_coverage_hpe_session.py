"""
Tests for capture_coverage_hpe_session.py.

No camera and no MediaPipe: the detector and the frame mailbox are both
injected, which is the reason they are constructor arguments. What is under test
is the plumbing -- that frames reach the shared state machine, that a stalled
camera is not mistaken for a still one, and that a broken detector reads as
"could not see the leg" rather than as a clean session.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import capture_coverage as cc
import capture_coverage_hpe as hpe
import capture_coverage_hpe_session as hs


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _pose(ankle_v=1.0):
    lms = [_LM(0.0, 0.0, 0.0) for _ in range(33)]
    lms[hpe.L_HIP] = _LM(0.5, 0.40)
    lms[hpe.L_KNEE] = _LM(0.5, 0.60)
    lms[hpe.L_ANKLE] = _LM(0.5, 0.80, ankle_v)
    return lms


class _Result:
    def __init__(self, poses):
        self.pose_landmarks = poses


class _FakeDetector:
    """Stands in for a PoseLandmarker. Records how often it was asked."""

    def __init__(self, poses_for=lambda n: [_pose()]):
        self.poses_for = poses_for
        self.calls = 0
        self.closed = False

    def detect(self, _image):
        self.calls += 1
        return _Result(self.poses_for(self.calls))

    def close(self):
        self.closed = True


class _Mailbox:
    """The one-slot frame handoff master_app's preview loop writes into."""

    def __init__(self):
        self._lock = threading.Lock()
        self._slot = None
        self.seq = 0

    def publish(self, frame):
        with self._lock:
            self.seq += 1
            self._slot = (frame, self.seq)

    def read(self):
        with self._lock:
            return self._slot


def _frame(h=64, w=48):
    np = pytest.importorskip("numpy")
    return np.zeros((h, w, 3), dtype=np.uint8)


def _session(detector, mailbox, **kw):
    return hs.PoseCoverageSession(frame_source=mailbox.read,
                                  detector_factory=lambda: detector,
                                  target_fps=kw.pop("target_fps", 200.0), **kw)


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _pump(mailbox, detector, n, timeout=5.0):
    """Publish n frames, waiting for each to actually be consumed.

    The mailbox holds ONE frame by design, so publishing in a tight loop drops
    most of them and leaves any count assertion racy. Waiting on the detector's
    call count makes these tests deterministic without weakening what they
    check -- a real camera publishes far slower than the worker samples.
    """
    for _ in range(n):
        before = detector.calls
        mailbox.publish(_frame())
        assert _wait_for(lambda: detector.calls > before, timeout),             f"worker stopped consuming after {detector.calls} frames"


# -- lifecycle ---------------------------------------------------------------

def test_a_missing_detector_fails_to_start_rather_than_raising():
    """No MediaPipe or no .task asset is a degraded session, not a failed one:
    it must never stop a recording from happening."""
    session = hs.PoseCoverageSession(frame_source=lambda: None,
                                     detector_factory=lambda: None)
    assert session.start() is False
    assert session.running is False


def test_start_then_stop_closes_the_detector():
    det = _FakeDetector()
    mb = _Mailbox()
    session = _session(det, mb)
    assert session.start() is True
    assert session.running is True
    session.stop()
    assert session.running is False
    assert det.closed is True


def test_stopping_is_safe_when_it_never_started():
    session = hs.PoseCoverageSession(frame_source=lambda: None,
                                     detector_factory=lambda: None)
    session.stop()          # must not raise
    assert session.running is False


# -- frames reach the shared state machine -----------------------------------

def test_published_frames_are_measured():
    det = _FakeDetector()
    mb = _Mailbox()
    session = _session(det, mb)
    session.start()
    try:
        _pump(mb, det, 10)
        stats = session.live.stats()
        assert stats.n_frames == 10, stats
        assert stats.coverage == 1.0
    finally:
        session.stop()


def test_an_occluded_ankle_shows_up_as_lost_shank():
    det = _FakeDetector(poses_for=lambda n: [_pose(ankle_v=0.05)])
    mb = _Mailbox()
    session = _session(det, mb)
    session.start()
    try:
        _pump(mb, det, 10)
        stats = session.live.stats()
        assert stats.thigh_coverage == 1.0
        assert stats.shank_coverage == 0.0
    finally:
        session.stop()


def test_a_frozen_camera_contributes_nothing():
    """The mailbox keeps handing back the same frame when the preview loop has
    stalled. Re-measuring it would read as flawless coverage of a still image --
    the most dangerous possible false PASS, because the operator would be told
    the setup is fine at exactly the moment the camera has died."""
    det = _FakeDetector()
    mb = _Mailbox()
    session = _session(det, mb)
    session.start()
    try:
        mb.publish(_frame())            # one frame, then nothing ever again
        assert _wait_for(lambda: det.calls >= 1)
        time.sleep(0.3)
        assert det.calls == 1, det.calls
        # One sample is not two, so there are no statistics to report -- which
        # surfaces as NO_DATA rather than as a pass.
        assert session.live_verdict().status == cc.NO_DATA
    finally:
        session.stop()


def test_a_detector_that_throws_counts_as_not_seeing_the_leg():
    """Swallowing the error and skipping the frame would let a broken detector
    report a clean session."""
    class _Broken(_FakeDetector):
        def detect(self, _image):
            self.calls += 1
            raise RuntimeError("inference failed")

    det = _Broken()
    mb = _Mailbox()
    session = _session(det, mb)
    session.start()
    try:
        _pump(mb, det, 10)
        assert session.live.stats().coverage == 0.0
    finally:
        session.stop()


def test_a_frame_source_that_throws_does_not_kill_the_worker():
    det = _FakeDetector()
    mb = _Mailbox()
    state = {"fail": True}

    def source():
        if state["fail"]:
            raise RuntimeError("mailbox not ready yet")
        return mb.read()

    session = hs.PoseCoverageSession(frame_source=source,
                                     detector_factory=lambda: det,
                                     target_fps=200.0)
    session.start()
    try:
        time.sleep(0.1)
        state["fail"] = False
        _pump(mb, det, 3)
    finally:
        session.stop()


# -- it is the same check, wearing the right words ---------------------------

def test_the_pose_session_reports_itself_as_a_camera():
    det = _FakeDetector()
    mb = _Mailbox()
    session = _session(det, mb)
    assert session.modality is cc.POSE
    v = session.live_verdict()
    assert v.status == cc.NO_DATA
    assert "Motive" not in v.detail
    assert "camera" in v.detail


def test_the_mocap_session_still_reports_itself_as_motive():
    import capture_coverage_session as ccs
    assert ccs.CoverageSession.modality is cc.MOCAP
    assert "Motive" in ccs.CoverageSession().live_verdict().detail


def test_the_preflight_watch_is_inherited_whole():
    """The point of subclassing: an operator gets the same watch, the same
    thresholds and the same messages whichever sensor is available."""
    det = _FakeDetector()
    mb = _Mailbox()
    session = _session(det, mb)
    session.start()
    try:
        watch_s = cc.PREFLIGHT_MIN_CONTINUOUS_S + 0.3
        session.begin_preflight(duration_s=watch_s)
        assert session.preflight_done() is False
        while not session.preflight_done():
            _pump(mb, det, 1)
            time.sleep(0.01)
        v = session.finish_preflight()
        assert v.status == cc.PASS
        assert "marker" not in (v.headline + v.detail).lower()
    finally:
        session.stop()


def test_a_bad_preflight_names_the_shank_without_naming_markers():
    det = _FakeDetector(poses_for=lambda n: [_pose(ankle_v=1.0 if n % 2 else 0.05)])
    mb = _Mailbox()
    session = _session(det, mb)
    session.start()
    try:
        session.begin_preflight(duration_s=cc.PREFLIGHT_MIN_CONTINUOUS_S + 0.3)
        while not session.preflight_done():
            _pump(mb, det, 1)
            time.sleep(0.01)
        v = session.finish_preflight()
        assert v.status == cc.FAIL
        assert "shank" in v.detail
        assert "pose landmarks" in v.detail
    finally:
        session.stop()


# -- the model asset ---------------------------------------------------------

def test_the_lite_model_is_preferred_when_several_are_present(tmp_path):
    """It runs alongside a live recording, and it is a visibility check rather
    than the measurement, so speed beats accuracy here."""
    for name in ("pose_landmarker_heavy.task", "pose_landmarker_full.task",
                 "pose_landmarker_lite.task"):
        (tmp_path / name).write_bytes(b"")
    assert os.path.basename(hs.resolve_task_path(str(tmp_path))) == \
        "pose_landmarker_lite.task"


def test_a_missing_models_directory_resolves_to_nothing(tmp_path):
    assert hs.resolve_task_path(str(tmp_path / "nope")) is None


# -- the intake boundary -----------------------------------------------------

def test_feed_flags_takes_booleans_and_feed_frame_takes_rigid_bodies():
    """These were one method, and the pose session silently measured 0% because
    a plain True has no .tracking_valid. It failed as a clean, plausible
    "the leg was never visible" rather than as an error, which is the shape of
    bug that reaches an operator: they would have been told to move, in a room
    where nothing was wrong.
    """
    import capture_coverage_session as ccs

    class _Body:
        tracking_valid = True

    flags = ccs.CoverageSession()
    bodies = ccs.CoverageSession()
    for i in range(10):
        flags.feed_flags(i, i / 30.0, True, True)
        bodies.feed_frame(i, i / 30.0, _Body(), _Body())
    assert flags.live.stats().coverage == 1.0
    assert bodies.live.stats().coverage == 1.0


def test_feed_frame_still_reads_tracking_valid():
    """The NatNet path must keep unwrapping: a rigid body present in the frame
    but not tracked is Motive saying it lost it."""
    import capture_coverage_session as ccs

    class _Body:
        def __init__(self, valid):
            self.tracking_valid = valid

    session = ccs.CoverageSession()
    for i in range(10):
        session.feed_frame(i, i / 30.0, _Body(True), _Body(False))
    stats = session.live.stats()
    assert stats.thigh_coverage == 1.0
    assert stats.shank_coverage == 0.0
