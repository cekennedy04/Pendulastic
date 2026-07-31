# USB Camera Scan & Select for pendulastic_app.py — Design Spec
**Date:** 2026-07-31
**Status:** Approved

---

## 1. Goal

`master_app.py` already lets a user scan for connected USB cameras and switch between them (`enumerate_cameras()`, a "Camera" dropdown, a "Rescan" button, live status). `pendulastic_app.py` has no such thing — its RGB source is hardcoded to `cv2.VideoCapture(0)`, opened fresh at every "START RECORDING" and fully released at every "STOP".

Port the scan/select capability into `pendulastic_app.py`, reusing master_app's working enumeration logic rather than re-deriving it, and — per explicit direction — also port master_app's "instant start" UX: the camera opens once (on Rescan or when RGB is first checked) and **stays open across multiple trials**, with a live preview shown before recording even starts. Recording attaches/detaches a `VideoWriter` to the already-running capture instead of closing and reopening the device (MSMF backends can take 30-120s to reopen — this is the whole reason master_app never closes its camera between trials, and this spec adopts the same approach).

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `camera_utils.py` (new) | `CAMERA_BACKENDS`, `MAX_CAMERA_INDEX`, `read_with_warmup()`, `enumerate_cameras()` — moved verbatim out of `master_app.py`. New `CameraSession` class (used only by `pendulastic_app.py`). |
| `master_app.py` | Delete its inline copies of the four moved names; add `from camera_utils import CAMERA_BACKENDS, MAX_CAMERA_INDEX, read_with_warmup, enumerate_cameras`. No other line changes — behavior-preserving. |
| `pendulastic_app.py` | `AcquisitionPanel`: new camera dropdown + Rescan button + "🛜 Can't connect?" help button, shown/hidden with the RGB checkbox, reusing the existing `lbl_preview` widget for the live feed. `App`: owns one `CameraSession`; `_start_rgb_recording`/`_stop_rgb_recording` rewritten to attach/detach a writer instead of opening/closing a capture; `_rgb_cap`/`_rgb_thread`/`_rgb_stop`/`_rgb_record_worker` removed (their job moves into `CameraSession`); `_tick()`'s preview-drain condition widens from recording-only to "camera is live". |

---

## 3. `camera_utils.py`

```python
CAMERA_BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)]
MAX_CAMERA_INDEX = 5

def read_with_warmup(cap, attempts=8, delay=0.05) -> tuple[bool, object]: ...
def enumerate_cameras() -> list[dict]: ...
    # unchanged from master_app.py — {"index", "backend", "backend_name", "label"}
```

### `CameraSession`

Owns the lifecycle of one live camera: enumerate, open + hold for continuous reading, and let a caller attach a `VideoWriter` so the *same already-open, already-warmed* capture also gets written to disk during recording — no close/reopen.

```python
class CameraSession:
    def __init__(self, on_frame: Callable[[np.ndarray], None],
                 on_status: Optional[Callable[[str], None]] = None):
        """on_frame: called on the background read thread with every BGR frame.
        on_status: called on the background read thread on lifecycle events
        ("live", "lost", "error: <msg>"). Both callbacks must not touch Tkinter
        directly — the caller is responsible for marshaling to the UI thread."""

    @property
    def active(self) -> Optional[dict]:
        """The currently open camera's enumerate_cameras() dict, or None."""

    @property
    def frame_size(self) -> Optional[tuple[int, int]]:
        """(width, height) of the open capture, or None if nothing is open."""

    def rescan(self) -> list[dict]:
        """Enumerate available cameras. Does not open or change the active one."""

    def open(self, cam: dict) -> bool:
        """Close any current capture, open + warm `cam`, start the read loop.
        Returns False (capture released, on_status called) if the camera
        can't be opened or fails its warm-up read."""

    def close(self) -> None:
        """Stop the read loop and release the capture. Idempotent — safe to
        call when nothing is open, and always fully releases the hardware
        handle even if the read loop already exited on its own (e.g. after
        a detected camera loss)."""

    def attach_writer(self, writer) -> None:
        """Frames read from here on are also passed to writer.write() until
        detach_writer() is called. The capture keeps streaming either way —
        this never closes or reopens the device."""

    def detach_writer(self):
        """Stop writing. Returns the writer that was attached (caller releases
        it) or None if none was attached. Camera capture keeps running."""
```

**Read loop** (private `_read_loop`, one per `open()` call, on a daemon thread): mirrors `master_app.py`'s `_stream_loop` — read continuously; a run of more than 30 consecutive failed reads is treated as camera loss (same threshold master_app uses), at which point the loop releases the capture itself, clears `active`, and calls `on_status("lost")`. A thread that exits this way leaves `CameraSession` in the same state `close()` would — a subsequent `close()` call is still safe (nothing left to release, still joins the finished thread).

---

## 4. `pendulastic_app.py` — `AcquisitionPanel`

New widgets in a `cam_frame`, gridded directly under the RGB checkbox row, shown only when `_src_rgb` is checked (same visibility pattern as the existing `_video_path_frame`):

- Camera `ttk.Combobox` (readonly) + "Rescan" button — mirrors master_app's row.
- "🛜 Can't connect?" button next to Rescan — opens a help dialog covering the most common zero-cameras-detected cause on Windows (OS-level camera privacy permission blocking desktop apps), matching the existing IMU "🛜 Can't connect?" button's pattern (`pendulastic_viewer.py:2585`).
- The existing `lbl_preview` widget is reused for the live feed: today it's only gridded inside `enter_recording()`; it also grids whenever RGB is checked and `CameraSession.active` is not None, so the same preview area serves both the pre-recording live view and the in-recording view.

`_on_source_changed()` gains: show/hide `cam_frame` on the RGB checkbox, and calls a new `self.controller.on_rgb_toggled(enabled: bool)`.

New `AcquisitionPanel` methods: `set_camera_list(cams: list[dict])` (populate the dropdown, keep the current selection if it's still present, else default to the first) and the dropdown's `<<ComboboxSelected>>` binding calls `self.controller.on_camera_selected(label)`; the Rescan button calls `self.controller.on_rescan_cameras()`.

---

## 5. `pendulastic_app.py` — `App`

- `__init__`: `self._camera = CameraSession(on_frame=self._on_camera_frame, on_status=self._on_camera_status)`; `self._known_cameras: list = []`.
- `on_rgb_toggled(enabled)`: `enabled` → `self.on_rescan_cameras()` (auto-opens a camera so the preview is live as soon as RGB is checked); not `enabled` → `self._camera.close()`.
- `on_rescan_cameras()`: `self._known_cameras = self._camera.rescan()`; `self._acq.set_camera_list(self._known_cameras)`; open the kept/first camera via `self._camera.open(...)` if any were found, else surface "no camera detected" on the status line.
- `on_camera_selected(label)`: look up the matching dict in `self._known_cameras`, `self._camera.open(cam)`.
- `_on_camera_frame(frame_bgr)` (background thread): if `self._pose_estimator is not None` (set only while recording — see `_start_rgb_recording`/`_stop_rgb_recording` below), build the pose-overlay preview copy exactly as `_rgb_record_worker` does today (that logic moves here unchanged); otherwise pass the frame through as-is. Either way, `put_nowait` onto the existing `self._preview_queue` (existing drop-if-full behavior) — never touches Tkinter directly. Disk-writing itself is not this callback's job: `CameraSession`'s own read loop already calls `writer.write(frame)` internally whenever a writer is attached.
- `_on_camera_status(msg)` (background thread): `self.after(0, ...)` to update a small camera status indicator; never called directly from the read thread.
- `_start_rgb_recording(meta)`: if `self._camera.active is None`, error and return (no camera selected). Otherwise: build the output path (unchanged), create the `cv2.VideoWriter` using `self._camera.frame_size`, drain any stale preview frames (unchanged), create `self._pose_estimator` if MediaPipe is available (unchanged), then `self._camera.attach_writer(self._rgb_writer)`. No `cv2.VideoCapture(0)`, no new thread.
- `_stop_rgb_recording()`: `writer = self._camera.detach_writer()`; if `writer`, `writer.release()`; `self._pose_estimator = None`. Camera capture is untouched — it keeps streaming for the next trial's preview.
- `on_close()`: stop any active recording first (existing path), *then* `self._camera.close()` — recording's own writer must already be detached/released before the capture goes away.
- `_tick()`: the preview-drain condition widens from `"rgb" in self._active_sources and self._state == "recording"` to `self._state in ("idle", "recording") and self._camera.active is not None` — so the live preview updates before recording starts too, not just during it.
- `_rgb_cap`, `_rgb_thread`, `_rgb_stop`, `_rgb_record_worker` are removed — `CameraSession`'s own read loop replaces them.

---

## 6. Error Handling

- No cameras found on rescan → dropdown shows "(none detected)", status line explains it, "🛜 Can't connect?" button available for the Windows-permission explanation.
- Camera open fails (`CameraSession.open()` returns `False`) → status message, dropdown stays on the prior selection if any.
- Camera drops mid-stream (30+ consecutive failed reads, live or recording) → `CameraSession` releases itself and reports `on_status("lost")`; if this happens *during* a recording, the app must not silently produce a truncated file with no explanation — surfacing this clearly is a plan-time detail, but the requirement is: same visibility master_app gives a mid-trial camera loss (`_on_camera_lost`), not a silent failure.
- `CameraSession.close()` is unconditional and idempotent, called from `on_close()`, so the process never exits holding a live camera handle.

---

## 7. Testing

- `enumerate_cameras()`/`read_with_warmup()` need real hardware and aren't practically unit-testable — unchanged from today, no new tests there.
- `CameraSession`'s lifecycle *is* testable against a fake capture object (stub with a scripted `read()`/`isOpened()`/`release()`): frames reach `on_frame`; `attach_writer`/`detach_writer` toggle disk-writes without ever calling the stub's `release()`; `close()` stops the thread and is safe to call twice; a simulated read-failure streak triggers `on_status("lost")` and self-releases. This lifecycle is the real regression risk this feature introduces, so it's where test coverage concentrates.
- `master_app.py`'s existing behavior is unchanged (only its imports move) — no new tests required there beyond confirming its existing camera flow still works after the import switch.
