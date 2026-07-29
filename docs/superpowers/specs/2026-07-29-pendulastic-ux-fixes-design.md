# Pendulastic UX Fixes & Live Preview — Design Spec
**Date:** 2026-07-29
**Status:** Approved

---

## 1. Goal

Five targeted fixes and additions to the Pendulastic platform:

1. **Knee angle normalization** — remap IMU output so full horizontal extension = 180°, flexion → 90°.
2. **Camera live preview** — route annotated OpenCV frames to the AcquisitionPanel canvas during RGB recording.
3. **Landmark overlays** — MediaPipe hip/knee/ankle skeleton drawn on the preview frames in real time.
4. **File-first video upload source** — new acquisition source checkbox that processes a pre-recorded video file through the HPE pipeline without a live camera.
5. **UI scaling** — widen root window and add grid weights to eliminate right-side clipping.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_app.py` | `BiomechanicalEngine.get_live_angle`: apply 180° remap · `AcquisitionPanel`: add `_src_video_file` checkbox + path selector row + `canvas_preview` swap · `App._start_rgb_recording` / `_rgb_record_worker`: MediaPipe overlay + preview queue · `App._tick`: drain preview queue · `App.__init__`: root geometry + grid weights · add `_start_video_file_processing` + `_run_video_file_hpe` |
| `pendulastic_viewer.py` | No changes — landmark drawing already implemented in `_draw()` |
| `pendulastic_imu_server.py` | No changes — fix applied in app layer |

---

## 3. Knee Angle Normalization

### Root Cause

`BiomechanicalEngine.get_live_angle()` returns `_imu.get_state()["angles"]["pitch"]` directly. The server's `relative_angles()` already subtracts the zero offset, so a correctly zeroed horizontal leg reads ≈ 0°. The Popović protocol requires full extension = 180°, flexion toward bench = 90°.

### Fix

One line change in `BiomechanicalEngine.get_live_angle()`:

```python
# OLD
return float(_imu.get_state()["angles"]["pitch"])

# NEW
return 180.0 - float(_imu.get_state()["angles"]["pitch"])
```

**Mapping table:**

| Posture | Raw pitch (post-zero) | Clinical angle |
|---|---|---|
| Horizontal, fully extended | 0° | 180° |
| 45° below horizontal | +45° | 135° |
| Knee at 90° flexion | +90° | 90° |

Because `_tick()` reads `get_live_angle()` and appends to `_rec_angles["imu"]`, all stored trial data automatically carries the correct 180°-based scale that `compute_pt_params` expects. No changes to `pendulastic_imu_server.py`.

---

## 4. Camera Live Preview + Landmark Overlay

### 4A. Canvas Swap in `AcquisitionPanel`

`_build_widgets` adds a second widget at row 13:

```python
self.lbl_preview = tk.Label(self, bg="black", width=440, height=330)
self.lbl_preview.grid(row=13, column=0, columnspan=2, sticky="nsew",
                      padx=12, pady=4)
self.lbl_preview.grid_remove()   # hidden until RGB recording starts
```

`_on_source_changed()` manages mutual exclusivity:
- `_src_rgb.get() is True` → `lbl_preview.grid()`, `canvas_tele.grid_remove()`
- `_src_rgb.get() is False` → `canvas_tele.grid()`, `lbl_preview.grid_remove()`

`enter_recording()` / `enter_idle()` show/hide the correct widget based on which sources are active (no change to existing logic needed — `_on_source_changed` already sets the right state).

`update_preview(frame_bgr: np.ndarray)` method added to `AcquisitionPanel`:

```python
def update_preview(self, frame_bgr: np.ndarray) -> None:
    h, w = frame_bgr.shape[:2]
    target_w, target_h = 440, 330
    scale = min(target_w / w, target_h / h)
    nw, nh = int(w * scale), int(h * scale)
    resized = _cv2.resize(frame_bgr, (nw, nh))
    rgb = _cv2.cvtColor(resized, _cv2.COLOR_BGR2RGB)
    # Encode to PNG bytes → tk.PhotoImage (no PIL dependency)
    ok, buf = _cv2.imencode(".png", rgb)
    if ok:
        photo = tk.PhotoImage(data=buf.tobytes().__class__(
            __import__("base64").b64encode(buf.tobytes())))
        self.lbl_preview.config(image=photo)
        self.lbl_preview._photo = photo   # prevent GC
```

### 4B. MediaPipe in the RGB Capture Worker

`App._start_rgb_recording()` initializes a MediaPipe Pose estimator (lite model) before starting the thread:

```python
import mediapipe as mp
_mp_pose   = mp.solutions.pose
_mp_draw   = mp.solutions.drawing_utils
_mp_styles = mp.solutions.drawing_styles

self._pose_estimator = _mp_pose.Pose(
    model_complexity=0,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5,
)
self._preview_queue = queue.Queue(maxsize=1)
```

`_rgb_record_worker` frame loop (annotated-preview, raw-to-disk):

```python
while not self._rgb_stop.is_set():
    ok, frame = self._rgb_cap.read()
    if not ok:
        break

    # --- Write RAW frame to disk (preserves data integrity) ---
    self._rgb_writer.write(frame)

    # --- Annotate a copy for the preview only ---
    preview = frame.copy()
    rgb_frame = _cv2.cvtColor(preview, _cv2.COLOR_BGR2RGB)
    results   = self._pose_estimator.process(rgb_frame)
    if results.pose_landmarks:
        _mp_draw.draw_landmarks(
            preview,
            results.pose_landmarks,
            _mp_pose.POSE_CONNECTIONS,
            landmark_drawing_spec=_mp_styles.get_default_pose_landmarks_style(),
        )

    # --- Deliver to UI thread via single-slot queue (drop stale frames) ---
    try:
        self._preview_queue.put_nowait(preview)
    except queue.Full:
        pass
```

### 4C. Main Thread Preview Drain

`App._tick()` gains a preview drain step:

```python
if "rgb" in self._active_sources and self._state == "recording":
    try:
        frame = self._preview_queue.get_nowait()
        self._acq.update_preview(frame)
    except queue.Empty:
        pass
```

`App.on_stop()` closes the pose estimator after stopping the RGB thread:

```python
if hasattr(self, "_pose_estimator") and self._pose_estimator:
    self._pose_estimator.close()
    self._pose_estimator = None
```

### Guard

The entire MediaPipe overlay block is wrapped in `if _VIEWER_AVAIL:` (the existing flag that guards `pendulastic_viewer` / MediaPipe availability). If MediaPipe is not importable, the worker still runs but skips pose estimation — raw frames are still captured and raw preview frames still display.

---

## 5. File-First Video Upload Source

### 5A. New Checkbox in `AcquisitionPanel`

Row 8 gains a fourth checkbutton:

```python
self._src_video_file = tk.BooleanVar(value=False)
chk_video = tk.Checkbutton(meth_f, text="📁 Video File",
                            variable=self._src_video_file,
                            command=self._on_source_changed)
```

`_on_source_changed()` shows/hides a path-selector row (row 8.5, packed inside a subframe in row 8):

```python
self._video_path_frame = tk.Frame(self)
self._video_path_frame.grid(row=10, column=0, columnspan=2,
                             sticky="w", padx=12, pady=2)
self._video_path_var = tk.StringVar(value="No file selected")
tk.Label(self._video_path_frame,
         textvariable=self._video_path_var,
         font=("Consolas", 8), fg="gray", width=38,
         anchor="w").pack(side="left")
tk.Button(self._video_path_frame, text="Browse…",
          font=("Segoe UI", 8),
          command=self._on_browse_video).pack(side="left", padx=4)
self._video_path_frame.grid_remove()   # hidden until checkbox checked
```

`_on_source_changed()` shows `_video_path_frame` when `_src_video_file.get() is True`, hides it otherwise.

`_on_browse_video()`:

```python
def _on_browse_video(self) -> None:
    path = filedialog.askopenfilename(
        title="Select pre-recorded video",
        filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"),
                   ("All files", "*.*")])
    if path:
        self._video_path_var.set(os.path.basename(path))
        self._stored_video_path = path
    else:
        self._stored_video_path = getattr(self, "_stored_video_path", "")
```

`get_video_file_path() -> str`:

```python
def get_video_file_path(self) -> str:
    return getattr(self, "_stored_video_path", "")
```

### 5B. Validation

`validate_metadata()` gets two new guards:

```python
# Mutual exclusion: video file and live RGB cannot both be active
if self._src_video_file.get() and self._src_rgb.get():
    return False, "Cannot use 'Video File' and live RGB simultaneously."

# Require a path when video file is selected
if self._src_video_file.get() and not self.get_video_file_path():
    return False, "Select a video file before starting."
```

### 5C. `get_active_sources()` and `get_metadata()`

`get_active_sources()` adds `"video_file"` when checked:

```python
if self._src_video_file.get(): sources.append("video_file")
```

`get_metadata()` adds the path:

```python
"video_file_path": self.get_video_file_path() if self._src_video_file.get() else None,
```

### 5D. App Lifecycle for Video File Source

`App.on_start()` dispatches the new path when `"video_file"` is in sources:

```python
elif src == "video_file":
    self._start_video_file_processing(meta)
```

`_start_video_file_processing(meta)`:

```python
def _start_video_file_processing(self, meta: dict) -> None:
    path = self._acq.get_video_file_path()
    if not path:
        messagebox.showerror("Video File", "No video file selected.")
        return
    self._state = "processing"
    self._acq.enter_processing()
    threading.Thread(
        target=self._run_video_file_hpe,
        args=(path, meta), daemon=True,
    ).start()
```

`_run_video_file_hpe(path, meta)`:

```python
def _run_video_file_hpe(self, path: str, meta: dict) -> None:
    def progress(pct: float) -> None:
        self.after(0, lambda p=pct: self._acq.status_var.set(
            f"HPE processing: {int(p * 100)}%"))

    leg    = meta.get("leg", "right").lower()
    engine = BiomechanicalEngine("rgb")
    angles = engine.run_offline_track(path, progress, leg=leg)

    fn = DataManager.build_filename(
        meta["pid"], meta["leg"], meta["ms_status"],
        meta["trial"], source="video_file")
    DataManager.save_trial(fn, angles, meta, fps=30.0, source="video_file")

    source_angles = dict(getattr(self, "_pending_review", {}))
    source_angles["video_file"] = angles
    self.after(0, lambda: self._transition_to_review(source_angles, meta))
```

### 5E. PostProcessingPanel Colour Entry

`_CURVE_STYLES` in `PostProcessingPanel` gains a `"video_file"` entry:

```python
"video_file": {"color": "#7C3AED", "ls": "--", "label": "Video File (HPE)"},
```

`_PT_SOURCE_PRIORITY` gains `"video_file"` after `"hpe_upload"`:

```python
_PT_SOURCE_PRIORITY = ["imu", "rgb", "optitrack", "hpe_upload", "video_file"]
```

---

## 6. UI Scaling

### Root Window

In `App.__init__`:

```python
# OLD
self.geometry("500x740")
self.resizable(False, True)

# NEW
self.geometry("900x740")
self.minsize(700, 680)
self.resizable(True, True)
self.columnconfigure(0, weight=1)
self.rowconfigure(0, weight=1)
```

These four lines are the only changes needed. All panels already use `pack(fill="both", expand=True)` so they scale automatically once the root allows it.

---

## 7. Thread Safety Summary

| Resource | Threads | Guard |
|---|---|---|
| `_preview_queue` (video frames) | RGB worker → `_tick()` (main) | `queue.Queue(maxsize=1)` — put_nowait drops stale frames |
| `_pose_estimator` | RGB worker only (initialized before thread start, closed after join) | Single-thread access; no lock needed |
| `lbl_preview._photo` | Main thread only (via `self.after`) | `self.after(0, ...)` marshal in `_tick()` |
| `_stored_video_path` | Main thread only | Set by UI callback; read by `on_start()` on same thread |

---

## 8. Out of Scope

- Displaying live angle readout from MediaPipe landmarks during RGB recording (deferred; IMU sparkline serves this role)
- Scrollable/zoomable video preview in the acquisition canvas
- Multiple simultaneous camera indices
- Changing the server-side `relative_angles()` return convention
