"""
camera_utils.py — shared USB camera enumeration and live-capture lifecycle.

Used by both master_app.py (enumerate_cameras()/read_with_warmup() only —
master_app manages its own capture/preview loop directly) and
pendulastic_app.py (also uses CameraSession — see below).
"""
import threading
import time

import cv2

CAMERA_BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)]
MAX_CAMERA_INDEX = 5       # Probe indices 0..MAX_CAMERA_INDEX.


def read_with_warmup(cap, attempts=15, delay=0.1):
    """
    Try to read a frame, retrying to absorb MSMF/USB warm-up latency.

    The MSMF backend often fails the first read() right after opening a camera
    (it returns before the stream is flowing). Returns (ok, frame).
    """
    for _ in range(attempts):
        ret, frame = cap.read()
        if ret and frame is not None:
            return True, frame
        time.sleep(delay)
    return False, None


def enumerate_cameras():
    """
    Probe for working cameras across the preferred backends.

    Returns a list of dicts: {"index", "backend", "backend_name", "label"}.
    A camera index already found on an earlier (preferred) backend is not
    re-listed for a later backend, so the Logitech shows up once.
    """
    found = []
    seen_indices = set()
    for backend_name, backend_flag in CAMERA_BACKENDS:
        for idx in range(MAX_CAMERA_INDEX + 1):
            if idx in seen_indices:
                continue
            cap = cv2.VideoCapture(idx, backend_flag)
            ok = cap.isOpened()
            ret = False
            if ok:
                # Warm-up read so a flaky first frame doesn't hide a good camera.
                ret, _ = read_with_warmup(cap, attempts=8, delay=0.05)
            cap.release()
            if ok and ret:
                seen_indices.add(idx)
                found.append({
                    "index": idx,
                    "backend": backend_flag,
                    "backend_name": backend_name,
                    "label": f"Camera {idx} ({backend_name})",
                })
    return found


class CameraSession:
    """Owns the lifecycle of one live camera: enumerate, open + hold for
    continuous background reading, and let a caller attach a VideoWriter so
    the same already-open, already-warmed capture also gets written to disk
    during recording — no close/reopen between preview and recording (MSMF
    backends can take 30-120s to reopen, which is exactly what this avoids).
    """

    _LOSS_THRESHOLD = 30   # consecutive failed reads before treating as lost

    def __init__(self, on_frame, on_status=None, capture_factory=None):
        """on_frame(frame_bgr): called on the background read thread for
        every frame read. on_status(msg): called on the background read
        thread on lifecycle events ("live", "lost", or an error/failure
        message). Neither callback may touch Tkinter directly — the caller
        marshals to the UI thread. capture_factory(index, backend) -> a
        cv2.VideoCapture-like object; defaults to cv2.VideoCapture, override
        only in tests. open()/close() are intended to be called from a
        single thread (the Tk UI thread in production) — not safe to call
        concurrently from multiple threads."""
        self._on_frame = on_frame
        self._on_status = on_status or (lambda msg: None)
        self._capture_factory = capture_factory or cv2.VideoCapture
        self._cap = None
        self._thread = None
        self._stop_evt = None          # created fresh per open(); never reused
        self._writer = None
        self._writer_lock = threading.Lock()
        self._frame_size = None
        self.active = None

    @property
    def frame_size(self):
        return self._frame_size

    def rescan(self):
        """Enumerate available cameras. Does not open or change the active
        one — but note enumerate_cameras() briefly probes every index on
        every backend, including whichever index this session currently has
        open, so the active camera can transiently appear busy/missing in
        its own rescan result. This call blocks for the full probe; do not
        call it on the UI thread without expecting a multi-second pause."""
        return enumerate_cameras()

    def open(self, cam: dict) -> bool:
        """Close any current capture, open + warm `cam`, start the read loop.
        Returns False (capture released, on_status called) if the camera
        can't be opened or fails its warm-up read."""
        self.close()
        cap = self._capture_factory(cam["index"], cam["backend"])
        ok = cap.isOpened()
        if ok:
            ok, _ = read_with_warmup(cap, attempts=20, delay=0.1)
        if not ok:
            try:
                cap.release()
            except Exception:
                pass
            self._safe_status(f"Could not open {cam['label']}.")
            return False

        self._cap = cap
        self.active = cam
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
        self._frame_size = (w, h) if w and h else None

        stop_evt = threading.Event()
        self._stop_evt = stop_evt
        thread = threading.Thread(target=self._read_loop, args=(cap, stop_evt), daemon=True)
        self._thread = thread
        thread.start()
        self._safe_status("live")
        return True

    def close(self) -> None:
        """Stop the read loop and clear this session's state. Idempotent —
        safe to call when nothing is open, and safe to call twice. If the
        read thread doesn't stop within the timeout (e.g. a stalled MSMF
        read), this session detaches from it rather than releasing the
        capture out from under an in-flight cap.read() call (a native
        use-after-release crash) — the abandoned thread releases its own
        capture itself, in its own finally block, once the blocking call
        eventually returns. Does not touch an attached writer's file — if
        a writer is attached, detach_writer() it and release() it yourself
        before calling close(), or the video file is only finalized whenever
        Python eventually garbage-collects the writer object."""
        if self._stop_evt is not None:
            self._stop_evt.set()
        thread = self._thread
        self._thread = None
        self._stop_evt = None
        with self._writer_lock:
            self._writer = None
        self._cap = None
        self.active = None
        self._frame_size = None
        if thread is not None:
            thread.join(timeout=2.0)
            if thread.is_alive():
                self._safe_status("camera did not respond to close(); abandoning it")

    def attach_writer(self, writer) -> None:
        """Frames read from here on are also passed to writer.write() until
        detach_writer() is called. The capture keeps streaming either way —
        this never closes or reopens the device."""
        with self._writer_lock:
            self._writer = writer

    def detach_writer(self):
        """Stop writing. Returns the writer that was attached (caller is
        responsible for releasing it) or None if none was attached. Camera
        capture keeps running for the live preview."""
        with self._writer_lock:
            w, self._writer = self._writer, None
        return w

    def _safe_status(self, msg: str) -> None:
        try:
            self._on_status(msg)
        except Exception:
            pass

    def _read_loop(self, cap, stop_evt) -> None:
        """Operates only on the `cap`/`stop_evt` passed in at thread start —
        never on self._cap/self._stop_evt — so a thread that outlives its
        session's close()/open() call can never be resurrected by, or
        interfere with, a later session. cap.release() happens here,
        unconditionally, on every exit path — close()/open() never call it."""
        miss = 0
        lost = False
        try:
            while not stop_evt.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    miss += 1
                    if miss > self._LOSS_THRESHOLD:   # more than 30 consecutive failures
                        lost = True
                        break
                    time.sleep(0.01)
                    continue
                miss = 0
                with self._writer_lock:
                    w = self._writer
                if w is not None:
                    try:
                        w.write(frame)
                    except Exception:
                        pass
                try:
                    self._on_frame(frame)
                except Exception:
                    pass
        except Exception as e:
            lost = True
            self._safe_status(f"error: {type(e).__name__}: {e}")
        finally:
            try:
                cap.release()
            except Exception:
                pass
            # Only touch session-level state if this thread is still the
            # session's current thread — an abandoned orphan (after a
            # close()/open() timeout) must never clobber a newer session.
            if lost and self._thread is threading.current_thread():
                self._cap = None
                self.active = None
                self._frame_size = None
                self._safe_status("lost")
