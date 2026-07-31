# USB Camera Scan & Select for pendulastic_app.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port master_app.py's working USB camera scan/select capability into pendulastic_app.py, including its "instant start" UX: the camera opens once (on Rescan or when RGB is first checked) and stays open across multiple trials, with a live preview before recording even starts. Recording attaches/detaches a `VideoWriter` to the already-running capture instead of closing and reopening the device.

**Architecture:** A new `camera_utils.py` holds `enumerate_cameras()`/`read_with_warmup()`/the backend-probe constants (moved verbatim out of `master_app.py`, which switches to importing them) plus a new `CameraSession` class that owns one live camera's full lifecycle: enumerate, open + hold a continuous background read loop, and let a caller attach/detach a `VideoWriter` without ever closing/reopening the device. `pendulastic_app.py`'s `AcquisitionPanel` gains a camera dropdown + Rescan button + help button (shown only while "RGB" is checked, reusing the existing `lbl_preview` widget for the live feed) and `App` owns one `CameraSession`, replacing its old open-fresh-per-trial `_rgb_cap`/`_rgb_thread`/`_rgb_record_worker` machinery.

**Tech Stack:** Python 3.13, OpenCV (`cv2`), Tkinter (existing), pytest. No new dependencies.

Full design rationale: `docs/superpowers/specs/2026-07-31-camera-selection-design.md`.

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-07-31-camera-selection-design.md`.
- `camera_utils.py`'s `CAMERA_BACKENDS`, `MAX_CAMERA_INDEX`, `read_with_warmup()`, `enumerate_cameras()` are moved **verbatim** (same defaults, same logic) from `master_app.py` — `master_app.py`'s own camera behavior must not change, only where these four names are defined.
- `CameraSession.__init__` accepts a `capture_factory` parameter defaulting to `cv2.VideoCapture`, so tests can inject a fake without touching real hardware. Production code never passes this argument explicitly.
- `CameraSession.close()` and repeated `CameraSession.open()` calls must be idempotent/safe in every state (never opened, already open, already lost/self-released).
- `CameraSession.attach_writer(writer)`/`detach_writer()` never close or reopen the underlying capture — the same `cv2.VideoCapture` keeps streaming for the live preview regardless of whether a writer is attached. `detach_writer()` returns the writer to the caller, who is responsible for calling `.release()` on it — `CameraSession` never releases a writer itself.
- `on_frame`/`on_status` callbacks passed into `CameraSession` run on its background read thread and must never touch Tkinter directly — callers marshal to the UI thread themselves (existing `self._preview_queue` pattern for frames, `self.after(0, ...)` for status).
- `pendulastic_app.py` must set `os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")` before its own `import cv2 as _cv2`, matching `master_app.py`'s existing mitigation (MSMF can otherwise take 30-120s to open a USB camera) — without this, enumeration/rescan in the new feature could be unusably slow.
- All new camera-dependent code in `pendulastic_app.py` follows the file's existing guarded-import convention: if `cv2`/`camera_utils` fail to import, `_CV2_AVAIL` is `False`, `self._camera` is `None`, and every new method checks that before touching the camera.
- `AcquisitionPanel`'s live-preview area (`lbl_preview` vs `canvas_tele` in grid row 13) is decided by one method, `_refresh_preview_area()`: show `lbl_preview` when `_src_rgb` is checked AND (currently recording OR the camera session reports live); show `canvas_tele` when currently recording and the above doesn't hold; otherwise show neither. This must not change the existing recording-time behavior (`lbl_preview` shown whenever RGB is checked and recording, regardless of a live-camera flag) — existing tests in `tests/test_acquisition_panel.py` encode this and must keep passing unmodified.
- Tests run via `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py tests/test_acquisition_panel.py tests/test_app.py -v` (existing test files extended, one new file for `camera_utils.py`).

---

### Task 1: Extract `camera_utils.py`; switch `master_app.py` to import from it

**Files:**
- Create: `camera_utils.py`
- Modify: `master_app.py:83-132` (remove the four moved definitions, add an import)
- Test: `tests/test_camera_utils.py` (new), `tests/test_master_app_camera_utils.py` (new — thin identity checks, no GUI/hardware)

**Interfaces:**
- Produces: `camera_utils.CAMERA_BACKENDS: list[tuple[str, int]]`, `camera_utils.MAX_CAMERA_INDEX: int`, `camera_utils.read_with_warmup(cap, attempts=15, delay=0.1) -> tuple[bool, object]`, `camera_utils.enumerate_cameras() -> list[dict]` (each dict: `{"index", "backend", "backend_name", "label"}`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_camera_utils.py`:

```python
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
```

Create `tests/test_master_app_camera_utils.py`:

```python
# tests/test_master_app_camera_utils.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import camera_utils
import master_app


def test_master_app_reuses_camera_utils_enumerate_cameras():
    assert master_app.enumerate_cameras is camera_utils.enumerate_cameras


def test_master_app_reuses_camera_utils_read_with_warmup():
    assert master_app.read_with_warmup is camera_utils.read_with_warmup


def test_master_app_reuses_camera_utils_constants():
    assert master_app.CAMERA_BACKENDS is camera_utils.CAMERA_BACKENDS
    assert master_app.MAX_CAMERA_INDEX == camera_utils.MAX_CAMERA_INDEX
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py tests/test_master_app_camera_utils.py -v`
Expected: `test_camera_utils.py` FAILS with `ModuleNotFoundError: No module named 'camera_utils'`. `test_master_app_camera_utils.py` FAILS the same way (imports `camera_utils` too).

- [ ] **Step 3: Create `camera_utils.py`**

```python
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
```

(`CameraSession` is added in Task 2 — this file grows, it is not replaced.)

- [ ] **Step 4: Switch `master_app.py` to import from `camera_utils.py`**

In `master_app.py`, replace lines 79-132 (the comment block, `CAMERA_BACKENDS`, `MAX_CAMERA_INDEX`, `PREVIEW_WINDOW`, `read_with_warmup()`, `enumerate_cameras()`) with:

```python
# Capture backends to probe, in order. On Windows 11, MSMF often enumerates USB
# UVC webcams (e.g. Logitech) that the older DSHOW backend misses; DSHOW is kept
# as a fallback. The selected camera carries its own backend so recording opens
# it exactly the way the probe found it.
from camera_utils import CAMERA_BACKENDS, MAX_CAMERA_INDEX, read_with_warmup, enumerate_cameras

PREVIEW_WINDOW = "Pendulastic Camera"   # Fixed window name for the live preview.
```

Every other line in `master_app.py` is unchanged — `CAMERA_BACKENDS`/`MAX_CAMERA_INDEX`/`read_with_warmup`/`enumerate_cameras` are used exactly as before, now imported rather than defined locally.

- [ ] **Step 5: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py tests/test_master_app_camera_utils.py -v`
Expected: PASS, 8 tests.

Also run the full existing suite to confirm `master_app.py`'s import switch didn't break anything else importable:
Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -c "import master_app"`
Expected: no output, exit code 0 (import-only, does not open a GUI — `MasterApp` is only instantiated inside `main()`, guarded by `if __name__ == '__main__':`).

- [ ] **Step 6: Commit**

```bash
git add camera_utils.py master_app.py tests/test_camera_utils.py tests/test_master_app_camera_utils.py
git commit -m "refactor: extract camera enumeration into camera_utils.py, shared by master_app.py"
```

---

### Task 2: `CameraSession` — continuous live capture with attach/detach writer

**Files:**
- Modify: `camera_utils.py` (append `CameraSession`)
- Test: `tests/test_camera_utils.py` (append)

**Interfaces:**
- Consumes: `read_with_warmup()`, `enumerate_cameras()` (Task 1)
- Produces: `CameraSession(on_frame, on_status=None, capture_factory=None)` with `.active: dict | None`, `.frame_size: tuple[int,int] | None`, `.rescan() -> list[dict]`, `.open(cam: dict) -> bool`, `.close() -> None`, `.attach_writer(writer) -> None`, `.detach_writer() -> object | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_camera_utils.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py -v -k "camera_session or attach_writer or close_is_idempotent or repeated_read_failures"`
Expected: FAIL with `AttributeError: module 'camera_utils' has no attribute 'CameraSession'`.

- [ ] **Step 3: Implement `CameraSession`**

Append to `camera_utils.py`:

```python
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
        only in tests."""
        self._on_frame = on_frame
        self._on_status = on_status or (lambda msg: None)
        self._capture_factory = capture_factory or cv2.VideoCapture
        self._cap = None
        self._thread = None
        self._stop_evt = threading.Event()
        self._writer = None
        self._writer_lock = threading.Lock()
        self._frame_size = None
        self.active = None

    @property
    def frame_size(self):
        return self._frame_size

    def rescan(self):
        """Enumerate available cameras. Does not open or change the active one."""
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
            self._on_status(f"Could not open {cam['label']}.")
            return False

        self._cap = cap
        self.active = cam
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or None
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or None
        self._frame_size = (w, h) if w and h else None

        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._on_status("live")
        return True

    def close(self) -> None:
        """Stop the read loop and release the capture. Idempotent — safe to
        call when nothing is open, and always fully releases the hardware
        handle even if the read loop already exited on its own (e.g. after
        a detected camera loss)."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        with self._writer_lock:
            self._writer = None
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self.active = None
        self._frame_size = None

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

    def _read_loop(self) -> None:
        miss = 0
        lost = False
        try:
            while not self._stop_evt.is_set():
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    miss += 1
                    if miss > self._LOSS_THRESHOLD:
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
            self._on_status(f"error: {type(e).__name__}: {e}")
        finally:
            if lost:
                if self._cap is not None:
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    self._cap = None
                self.active = None
                self._frame_size = None
                self._on_status("lost")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py -v`
Expected: PASS, all 13 tests.

- [ ] **Step 5: Commit**

```bash
git add camera_utils.py tests/test_camera_utils.py
git commit -m "feat: add CameraSession — continuous live capture with attach/detach writer"
```

---

### Task 3: `AcquisitionPanel` — camera dropdown, Rescan, help button, preview-area reuse

**Files:**
- Modify: `pendulastic_app.py` — `AcquisitionPanel._build_widgets` (`pendulastic_app.py:313-370`), `enter_idle`/`enter_recording` (`pendulastic_app.py:425-449`)
- Test: `tests/test_acquisition_panel.py`

**Interfaces:**
- Consumes: nothing new from other tasks (pure UI + controller-call plumbing; Task 4 implements the controller side)
- Produces: `AcquisitionPanel.cam_var`, `.drop_cam`, `._cam_frame`, `.set_camera_list(cams: list[dict])`, `.set_camera_live(is_live: bool)`; controller calls `on_rescan_cameras()`, `on_camera_selected(label: str)`, `on_camera_disabled()` (new methods the controller must implement — added to `App` in Task 4)

- [ ] **Step 1: Write the failing tests**

In `tests/test_acquisition_panel.py`, extend the shared `_Ctrl` fake (top of file) with the three new controller methods and a call log:

```python
class _Ctrl:
    """Minimal fake controller."""
    def __init__(self):
        self.calls = []
    def on_start(self): pass
    def on_stop(self): pass
    def on_source_changed(self, sources): pass
    def on_new_trial(self): pass
    def on_back_to_mode_select(self): pass
    def on_rescan_cameras(self): self.calls.append("rescan")
    def on_camera_selected(self, label): self.calls.append(("selected", label))
    def on_camera_disabled(self): self.calls.append("disabled")
```

Append new tests to `tests/test_acquisition_panel.py`:

```python
def test_camera_frame_hidden_by_default():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p._cam_frame.winfo_ismapped() is False
    finally:
        r.destroy()


def test_checking_rgb_shows_camera_frame_and_rescans():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_rgb_checkbox_toggled()
        r.update()
        assert p._cam_frame.winfo_ismapped() is True
        assert "rescan" in ctrl.calls
    finally:
        r.destroy()


def test_unchecking_rgb_hides_camera_frame_and_disables_camera():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_rgb_checkbox_toggled()
        p._src_rgb.set(False)
        p._on_rgb_checkbox_toggled()
        r.update()
        assert p._cam_frame.winfo_ismapped() is False
        assert "disabled" in ctrl.calls
    finally:
        r.destroy()


def test_set_camera_list_populates_dropdown_and_keeps_selection():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        cams = [
            {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"},
            {"index": 1, "backend": 700, "backend_name": "MSMF", "label": "Camera 1 (MSMF)"},
        ]
        p.set_camera_list(cams)
        assert list(p.drop_cam["values"]) == ["Camera 0 (MSMF)", "Camera 1 (MSMF)"]
        assert p.cam_var.get() == "Camera 0 (MSMF)"

        p.cam_var.set("Camera 1 (MSMF)")
        p.set_camera_list(cams)   # a rescan that still finds both keeps the selection
        assert p.cam_var.get() == "Camera 1 (MSMF)"
    finally:
        r.destroy()


def test_set_camera_list_empty_shows_none_detected():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_list([])
        assert list(p.drop_cam["values"]) == ["(none detected)"]
        assert p.cam_var.get() == "(none detected)"
    finally:
        r.destroy()


def test_selecting_camera_from_dropdown_notifies_controller():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        cams = [{"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"}]
        p.set_camera_list(cams)
        p.cam_var.set("Camera 0 (MSMF)")
        p._on_cam_selected()
        assert ("selected", "Camera 0 (MSMF)") in ctrl.calls
    finally:
        r.destroy()


def test_rescan_button_notifies_controller():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        p._on_rescan_clicked()
        assert "rescan" in ctrl.calls
    finally:
        r.destroy()


def test_camera_help_button_exists_and_opens_dialog(monkeypatch):
    from pendulastic_app import AcquisitionPanel
    import pendulastic_app as _m
    r = _root()
    try:
        shown = []
        monkeypatch.setattr(_m.messagebox, "showinfo",
                            lambda title, msg: shown.append((title, msg)))
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._on_camera_help()
        assert len(shown) == 1
        assert "camera" in shown[0][0].lower() or "connect" in shown[0][0].lower()
    finally:
        r.destroy()


def test_set_camera_live_shows_preview_while_idle_with_rgb_checked():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_rgb_checkbox_toggled()
        p.set_camera_live(True)
        r.update()
        assert p.lbl_preview.grid_info() != {}
        assert p.canvas_tele.grid_info() == {}
    finally:
        r.destroy()


def test_set_camera_live_false_hides_preview_while_idle():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_rgb_checkbox_toggled()
        p.set_camera_live(True)
        p.set_camera_live(False)
        r.update()
        assert p.lbl_preview.grid_info() == {}
    finally:
        r.destroy()


def test_rgb_recording_preview_unaffected_by_camera_live_flag():
    """Regression: enter_recording()'s existing lbl_preview-during-RGB-recording
    behavior must be unchanged even though set_camera_live() was never called
    (this is exactly what the existing test_rgb_source_swaps_to_preview_during_recording
    exercises — this test additionally pins that it holds with _camera_live at
    its default False)."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p._camera_live is False
        p._src_rgb.set(True)
        p._on_source_changed()
        p.enter_recording()
        r.update()
        assert p.lbl_preview.grid_info() != {}
        assert p.canvas_tele.grid_info() == {}
    finally:
        r.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_acquisition_panel.py -v`
Expected: the new tests FAIL (`AttributeError: 'AcquisitionPanel' object has no attribute '_cam_frame'`, etc.). All pre-existing tests in this file must still PASS unchanged — confirms they're valid regression guards before you touch `AcquisitionPanel`.

- [ ] **Step 3: Implement**

In `pendulastic_app.py`, change the RGB checkbutton's command (`pendulastic_app.py:328-330`) from the shared handler to a dedicated one:

```python
        chk_rgb   = tk.Checkbutton(chk_row, text="RGB",
                                    variable=self._src_rgb,
                                    command=self._on_rgb_checkbox_toggled)
```

Immediately after the existing video-file-path frame block (`pendulastic_app.py:340-352`, ends with `self._video_path_frame.pack_forget()`), add the camera frame:

```python
        # Inner row 3: camera selector (hidden until RGB is checked)
        self._cam_frame = tk.Frame(meth_f)
        self.cam_var = tk.StringVar(value="")
        self.drop_cam = ttk.Combobox(self._cam_frame, textvariable=self.cam_var,
                                     width=18, state="readonly")
        self.drop_cam.pack(side="left")
        self.drop_cam.bind("<<ComboboxSelected>>", self._on_cam_selected)
        tk.Button(self._cam_frame, text="Rescan", font=("Segoe UI", 8),
                  command=self._on_rescan_clicked).pack(side="left", padx=4)
        tk.Button(self._cam_frame, text="🛜 Can't connect?", font=("Segoe UI", 8),
                  command=self._on_camera_help).pack(side="left", padx=4)
        self._cam_frame.pack_forget()   # hidden until RGB is checked
        self._camera_live = False       # updated via set_camera_live()
```

Replace `enter_idle`/`enter_recording` (`pendulastic_app.py:425-449`) to route through a shared preview-area method instead of deciding directly:

```python
    def enter_idle(self) -> None:
        self._cancel_countdown()
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked,
                              bg=_GREEN, state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_form(False)
        self._is_recording = False
        self._refresh_preview_area()
        self.status_var.set("Idle — ready to record.")

    def enter_recording(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="START RECORDING",
                              bg=_GREEN, state="disabled")
        self.btn_stop.config(state="normal")
        self._is_recording = True
        self._refresh_preview_area()
        self.status_var.set("RECORDING…")

    def _refresh_preview_area(self) -> None:
        """Row 13 shows lbl_preview whenever RGB is checked and either
        currently recording or the pre-open camera session is live;
        canvas_tele only while recording and that doesn't hold; otherwise
        neither. Recording-time behavior is unchanged from before this
        feature — _camera_live only extends what's shown while idle."""
        show_preview = self._src_rgb.get() and (self._is_recording or self._camera_live)
        if show_preview:
            self.lbl_preview.grid(row=13, column=0, columnspan=2,
                                  padx=10, pady=4, sticky="nsew")
            self.canvas_tele.grid_remove()
        elif self._is_recording:
            self.canvas_tele.grid(row=13, column=0, columnspan=2,
                                  padx=10, pady=4)
            self.lbl_preview.grid_remove()
        else:
            self.lbl_preview.grid_remove()
            self.canvas_tele.grid_remove()
```

`AcquisitionPanel.__init__` (`pendulastic_app.py:246-251`) must initialize `self._is_recording = False` before `_build_widgets()` runs, since `_build_widgets()` no longer sets it and nothing else does yet:

```python
    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._countdown_id: Optional[str] = None
        self._tele_buf: list = []
        self._is_recording = False
        self._build_widgets()
```

Add the new handler methods (near `_on_source_changed`, `pendulastic_app.py:520` area):

```python
    def _on_rgb_checkbox_toggled(self) -> None:
        if self._src_rgb.get():
            self._cam_frame.pack(side="top", anchor="w", pady=(2, 0))
            self.controller.on_rescan_cameras()
        else:
            self._cam_frame.pack_forget()
            self.controller.on_camera_disabled()
        self._on_source_changed()

    def _on_cam_selected(self, event=None) -> None:
        label = self.cam_var.get()
        if label and label != "(none detected)":
            self.controller.on_camera_selected(label)

    def _on_rescan_clicked(self) -> None:
        self.controller.on_rescan_cameras()

    def _on_camera_help(self) -> None:
        messagebox.showinfo(
            "Can't connect to a camera?",
            "If no cameras are detected:\n\n"
            "1. Make sure the USB webcam is plugged in and not in use by "
            "another app (Zoom, Teams, Camera).\n"
            "2. Check Windows camera privacy settings: Settings > Privacy & "
            "security > Camera, and make sure camera access is turned on "
            "for desktop apps.\n"
            "3. Click Rescan after making changes.")

    def set_camera_list(self, cams: list) -> None:
        """Populate the camera dropdown. Keeps the current selection if it's
        still present in `cams`, else selects the first one (or shows
        '(none detected)' if the list is empty)."""
        labels = [c["label"] for c in cams]
        self.drop_cam["values"] = labels if labels else ["(none detected)"]
        if labels:
            prev = self.cam_var.get()
            self.cam_var.set(prev if prev in labels else labels[0])
        else:
            self.cam_var.set("(none detected)")

    def set_camera_live(self, is_live: bool) -> None:
        """Called by the controller when the pre-open camera session's
        live/lost state changes."""
        self._camera_live = is_live
        self._refresh_preview_area()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_acquisition_panel.py -v`
Expected: PASS, all tests (existing ones unmodified in behavior + 11 new).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: add camera dropdown/Rescan/help UI to AcquisitionPanel, reusing lbl_preview"
```

---

### Task 4: `App` — wire `CameraSession` for the pre-recording live preview

**Files:**
- Modify: `pendulastic_app.py` — imports (`pendulastic_app.py:25-54`), `App.__init__` (`pendulastic_app.py:1094-1130`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `camera_utils.CameraSession` (Task 2), `AcquisitionPanel.set_camera_list`/`set_camera_live` (Task 3)
- Produces: `App._camera: CameraSession | None`, `App.on_rescan_cameras()`, `App.on_camera_selected(label)`, `App.on_camera_disabled()` (satisfies the controller interface `AcquisitionPanel` now calls)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_app_creates_camera_session_when_cv2_available():
    import pendulastic_app as _m
    app = _m.App()
    try:
        if _m._CV2_AVAIL:
            assert app._camera is not None
        else:
            assert app._camera is None
    finally:
        app.destroy()


def test_on_rescan_cameras_populates_dropdown_and_opens_first(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        fake_cams = [
            {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"},
        ]
        opened = []
        monkeypatch.setattr(app._camera, "rescan", lambda: fake_cams)
        monkeypatch.setattr(app._camera, "open", lambda cam: opened.append(cam) or True)
        app.on_rescan_cameras()
        assert list(app._acq.drop_cam["values"]) == ["Camera 0 (MSMF)"]
        assert opened == [fake_cams[0]]
    finally:
        app.destroy()


def test_on_rescan_cameras_with_no_cameras_found(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        monkeypatch.setattr(app._camera, "rescan", lambda: [])
        app.on_rescan_cameras()
        assert list(app._acq.drop_cam["values"]) == ["(none detected)"]
        assert app._acq._camera_live is False
    finally:
        app.destroy()


def test_on_camera_selected_opens_the_matching_camera(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        fake_cams = [
            {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"},
            {"index": 1, "backend": 700, "backend_name": "MSMF", "label": "Camera 1 (MSMF)"},
        ]
        app._known_cameras = fake_cams
        opened = []
        monkeypatch.setattr(app._camera, "open", lambda cam: opened.append(cam) or True)
        app.on_camera_selected("Camera 1 (MSMF)")
        assert opened == [fake_cams[1]]
    finally:
        app.destroy()


def test_on_camera_disabled_closes_session_and_clears_live_flag(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        closed = []
        monkeypatch.setattr(app._camera, "close", lambda: closed.append(True))
        app._acq.set_camera_live(True)
        app.on_camera_disabled()
        assert closed == [True]
        assert app._acq._camera_live is False
    finally:
        app.destroy()


def test_camera_status_callback_updates_panel_live_flag():
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._on_camera_status("live")
        app.update()   # process the self.after(0, ...) callback
        assert app._acq._camera_live is True
        app._on_camera_status("lost")
        app.update()
        assert app._acq._camera_live is False
    finally:
        app.destroy()


def test_camera_frame_callback_queues_preview_frame():
    import pendulastic_app as _m
    import numpy as np
    app = _m.App()
    try:
        app._pose_estimator = None
        frame = np.zeros((4, 4, 3), dtype="uint8")
        app._on_camera_frame(frame)
        queued = app._preview_queue.get_nowait()
        assert queued is frame
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v -k "camera"`
Expected: FAIL — `App` has no `_camera` attribute, no `on_rescan_cameras`/`on_camera_selected`/`on_camera_disabled`/`_on_camera_status`/`_on_camera_frame` methods yet.

- [ ] **Step 3: Implement**

In `pendulastic_app.py`, add the MSMF mitigation and switch the guarded `cv2` import to also pull in `CameraSession` (`pendulastic_app.py:49-54`):

```python
# On Windows, the MSMF backend can hang for 30-120 seconds opening a USB
# camera because of hardware Media Foundation Transforms. Disabling them
# makes camera open near-instant. This MUST be set before OpenCV (cv2) is
# imported. (Same mitigation as master_app.py.)
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

try:
    import cv2 as _cv2
    from camera_utils import CameraSession
    _CV2_AVAIL = True
except ImportError:
    _cv2 = None
    CameraSession = None
    _CV2_AVAIL = False
```

In `App.__init__` (`pendulastic_app.py:1094-1122`), after the existing state initialization (right after `self._pose_estimator = None` at `pendulastic_app.py:1114`), add:

```python
        self._camera = (
            CameraSession(on_frame=self._on_camera_frame, on_status=self._on_camera_status)
            if _CV2_AVAIL else None
        )
        self._known_cameras: list = []
```

Add the new controller/callback methods (near `on_source_changed`, `pendulastic_app.py:1248` area):

```python
    def on_rescan_cameras(self) -> None:
        if self._camera is None:
            return
        self._known_cameras = self._camera.rescan()
        self._acq.set_camera_list(self._known_cameras)
        if self._known_cameras:
            label = self._acq.cam_var.get()
            cam = next((c for c in self._known_cameras if c["label"] == label),
                       self._known_cameras[0])
            self._camera.open(cam)
        else:
            self._acq.set_camera_live(False)

    def on_camera_selected(self, label: str) -> None:
        if self._camera is None:
            return
        cam = next((c for c in self._known_cameras if c["label"] == label), None)
        if cam is None:
            return
        if self._camera.active is not None and self._camera.active["label"] == label:
            return   # already using this camera
        self._camera.open(cam)

    def on_camera_disabled(self) -> None:
        if self._camera is not None:
            self._camera.close()
        self._acq.set_camera_live(False)

    def _on_camera_frame(self, frame_bgr) -> None:
        """Runs on CameraSession's background read thread. Applies the same
        pose-overlay logic _rgb_record_worker used to apply during recording;
        passes the frame through unchanged otherwise. Never touches Tkinter —
        hands off via the existing preview queue."""
        preview = frame_bgr
        if self._pose_estimator is not None and _mp_draw is not None:
            try:
                preview = frame_bgr.copy()
                rgb_frame = _cv2.cvtColor(preview, _cv2.COLOR_BGR2RGB)
                results = self._pose_estimator.process(rgb_frame)
                if results.pose_landmarks:
                    _mp_draw.draw_landmarks(
                        preview, results.pose_landmarks, _mp_pose.POSE_CONNECTIONS,
                        landmark_drawing_spec=_mp_styles.get_default_pose_landmarks_style(),
                    )
            except Exception:
                pass
        try:
            self._preview_queue.put_nowait(preview)
        except queue.Full:
            pass

    def _on_camera_status(self, msg: str) -> None:
        """Runs on CameraSession's background read thread — marshal to Tk."""
        self.after(0, lambda m=msg: self._acq.set_camera_live(m == "live"))
```

Widen `_tick()`'s preview-drain condition (`pendulastic_app.py:1660-1666`) from recording-only to "camera is live":

```python
        # Drain preview queue and update acquisition canvas whenever the
        # camera session is live (idle pre-open preview, or recording).
        if self._state in ("idle", "recording") and self._camera is not None \
                and self._camera.active is not None:
            try:
                frame = self._preview_queue.get_nowait()
                self._acq.update_preview(frame)
            except queue.Empty:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v`
Expected: PASS, all tests (existing + 7 new).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: wire CameraSession into App for a pre-recording live camera preview"
```

---

### Task 5: Rewire RGB recording to attach/detach instead of open/close; camera stays live across trials

**Files:**
- Modify: `pendulastic_app.py` — `_start_rgb_recording`/`_stop_rgb_recording`/`_rgb_record_worker` (`pendulastic_app.py:1451-1535`), `on_close` (`pendulastic_app.py:1698-1709`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `App._camera` (Task 4)
- Produces: `_start_rgb_recording`/`_stop_rgb_recording` no longer open/close a capture, only attach/detach a `VideoWriter`; `_rgb_cap`, `_rgb_thread`, `_rgb_stop`, `_rgb_record_worker` are removed

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_start_rgb_recording_attaches_writer_without_opening_new_capture(tmp_path, monkeypatch):
    import pendulastic_app as _m
    if not _m._CV2_AVAIL:
        return   # nothing to test without OpenCV installed
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        # Simulate an already-open, pre-warmed camera (as Rescan would leave it).
        app._camera.active = {"index": 0, "backend": 700, "backend_name": "MSMF",
                              "label": "Camera 0 (MSMF)"}
        app._camera._frame_size = (64, 48)
        attached = []
        monkeypatch.setattr(app._camera, "attach_writer", lambda w: attached.append(w))
        opened_new_capture = []
        monkeypatch.setattr(_m._cv2, "VideoCapture",
                            lambda *a, **kw: opened_new_capture.append(a) or None)
        # Fake the writer too, so this test never depends on a real codec
        # being available in the environment.
        created_writers = []
        class _FakeWriter:
            pass
        monkeypatch.setattr(_m._cv2, "VideoWriter",
                            lambda *a, **kw: created_writers.append(a) or _FakeWriter())
        monkeypatch.setattr(_m._cv2, "VideoWriter_fourcc", lambda *a: None)

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._start_rgb_recording(meta)

        assert len(attached) == 1, "must attach a writer to the existing CameraSession"
        assert isinstance(attached[0], _FakeWriter)
        assert opened_new_capture == [], "must NOT open a new cv2.VideoCapture"
    finally:
        app.destroy()


def test_start_rgb_recording_errors_when_no_camera_selected(monkeypatch):
    import pendulastic_app as _m
    if not _m._CV2_AVAIL:
        return
    app = _m.App()
    shown = []
    monkeypatch.setattr(_m.messagebox, "showerror",
                        lambda title, msg: shown.append((title, msg)))
    try:
        assert app._camera.active is None
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._start_rgb_recording(meta)
        assert shown, "must surface an error when no camera is active"
        assert not hasattr(app, "_rgb_writer") or app._rgb_writer is None
    finally:
        app.destroy()


def test_stop_rgb_recording_detaches_writer_but_leaves_camera_live(monkeypatch):
    import pendulastic_app as _m
    if not _m._CV2_AVAIL:
        return
    app = _m.App()
    try:
        class _FakeWriter:
            def __init__(self):
                self.released = False
            def release(self):
                self.released = True

        writer = _FakeWriter()
        monkeypatch.setattr(app._camera, "detach_writer", lambda: writer)
        closed = []
        monkeypatch.setattr(app._camera, "close", lambda: closed.append(True))

        app._stop_rgb_recording()

        assert writer.released is True
        assert closed == [], "camera capture must stay open/live across trials"
    finally:
        app.destroy()


def test_rgb_cap_and_rgb_thread_attributes_no_longer_exist():
    """Regression: the old open-fresh-per-trial machinery must be fully removed."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        assert not hasattr(app, "_rgb_cap")
        assert not hasattr(app, "_rgb_thread")
        assert not hasattr(app, "_rgb_stop")
    finally:
        app.destroy()


def test_on_close_detaches_and_releases_writer_before_closing_camera(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    order = []
    if app._camera is not None:
        class _FakeWriter:
            def release(self):
                order.append("writer_released")
        writer = _FakeWriter()
        monkeypatch.setattr(app._camera, "detach_writer", lambda: order.append("detach") or writer)
        monkeypatch.setattr(app._camera, "close", lambda: order.append("camera_closed"))
    monkeypatch.setattr(_m, "_IMU_AVAIL", False)
    app.on_close()
    if app._camera is not None:
        assert order == ["detach", "writer_released", "camera_closed"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v -k "rgb_recording or rgb_cap_and_rgb_thread or on_close_detaches"`
Expected: FAIL — `_start_rgb_recording` still opens `cv2.VideoCapture(0)` directly (test expecting no new capture opened fails), `_rgb_cap`/`_rgb_thread`/`_rgb_stop` still exist, `on_close` doesn't detach/release a writer.

- [ ] **Step 3: Implement**

Replace `_start_rgb_recording`, `_rgb_record_worker`, and `_stop_rgb_recording` (`pendulastic_app.py:1451-1535`) with:

```python
    def _start_rgb_recording(self, meta: dict) -> None:
        if not _CV2_AVAIL:
            messagebox.showerror("RGB", "OpenCV (cv2) is not installed.")
            return
        if self._camera is None or self._camera.active is None \
                or self._camera.frame_size is None:
            messagebox.showerror(
                "RGB", "No camera selected. Click Rescan and pick a camera first.")
            return
        fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        os.makedirs(DataManager.DATA_DIR, exist_ok=True)
        self._video_path = os.path.join(
            DataManager.DATA_DIR, fn.replace(".csv", ".avi"))
        w, h = self._camera.frame_size
        self._rgb_writer = _cv2.VideoWriter(
            self._video_path, _cv2.VideoWriter_fourcc(*"XVID"), 30.0, (w, h))

        # Drain any stale frames from a previous recording
        while not self._preview_queue.empty():
            try:
                self._preview_queue.get_nowait()
            except queue.Empty:
                break

        # Init lightweight pose estimator for live overlay (guarded)
        if _mp_pose is not None:
            self._pose_estimator = _mp_pose.Pose(
                model_complexity=0,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        else:
            self._pose_estimator = None

        self._camera.attach_writer(self._rgb_writer)

    def _stop_rgb_recording(self) -> None:
        writer = self._camera.detach_writer() if self._camera is not None else None
        if writer is not None:
            writer.release()
        self._rgb_writer = None
```

(`_rgb_record_worker` is deleted entirely — `CameraSession`'s own read loop, already running continuously since the camera was opened for preview, replaces it. Note pose-estimator cleanup stays where it already lives, at the top of `on_stop()` — `_stop_rgb_recording()` never touched it before this change either.)

Update `on_close()` (`pendulastic_app.py:1698-1709`):

```python
    def on_close(self) -> None:
        self._imu_poll_stop.set()
        if self._imu_poll_thread:
            self._imu_poll_thread.join(timeout=0.5)
        if self._camera is not None:
            writer = self._camera.detach_writer()
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            self._camera.close()
        if _IMU_AVAIL:
            try:
                _imu.stop()
            except Exception:
                pass
        self.destroy()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_app.py -v`
Expected: PASS, all tests.

Then run the complete set touched by this plan:
Run: `"C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe" -m pytest tests/test_camera_utils.py tests/test_master_app_camera_utils.py tests/test_acquisition_panel.py tests/test_app.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "refactor: rewire RGB recording to attach/detach a writer, camera stays live across trials"
```
