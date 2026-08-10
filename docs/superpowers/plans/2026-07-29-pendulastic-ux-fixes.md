# Pendulastic UX Fixes & Live Preview — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix knee angle clinical mapping, add live annotated camera preview during RGB recording, add a file-first video upload acquisition source, and fix root window clipping.

**Architecture:** All changes are isolated to `pendulastic_app.py`. A single `lbl_preview` Tkinter Label widget replaces the sparkline canvas during RGB recording; frames are annotated in the capture worker thread (MediaPipe lite, raw copy written to disk) and delivered to the main thread via a single-slot queue drained in `_tick()`. The file-first source adds a fourth checkbox and a browse row inside the existing `meth_f` frame (no outer row numbers shift). Geometry and grid weights are expanded to eliminate right-side clipping.

**Tech Stack:** Python 3.13, Tkinter, OpenCV (`_cv2`), MediaPipe (`mediapipe`), NumPy, threading, queue

## Global Constraints

- Modify ONLY `pendulastic_app.py` and test files — `pendulastic_viewer.py` and `pendulastic_imu_server.py` must NOT be changed
- MediaPipe overlay is guarded by `_VIEWER_AVAIL` — if unavailable, raw preview still displays
- Raw (unannotated) frames are written to the `.avi` file; overlay is preview-only
- `"video_file"` and `"rgb"` sources are mutually exclusive (validated in `validate_metadata`)
- Root window: `geometry("900x740")`, `minsize(700, 680)`, `resizable(True, True)`
- Colour for `"video_file"` in PostProcessingPanel: `#7C3AED`, dashed line `"--"`
- `base64` import added locally inside `update_preview()` — no new module-level import required

---

### Task 1: Knee Angle Normalization

**Files:**
- Modify: `pendulastic_app.py` lines 135–142 (`BiomechanicalEngine.get_live_angle`)
- Test: `tests/test_app.py` (add one new test)

**Interfaces:**
- Produces: `BiomechanicalEngine("imu").get_live_angle()` returns `180.0 - pitch` instead of `pitch`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py` (after the existing tests):

```python
def test_get_live_angle_maps_to_180_convention(monkeypatch):
    """Full extension (pitch=0 after zero) must read 180°; flexion increases pitch, lowers clinical angle."""
    import pendulastic_app as _m, types
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "angles": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    from pendulastic_app import BiomechanicalEngine
    engine = BiomechanicalEngine("imu")
    assert engine.get_live_angle() == 180.0, "Horizontal extension (pitch=0) must map to 180°"

    # Flexion: pitch increases → clinical angle decreases
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "angles": {"pitch": 45.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    assert engine.get_live_angle() == 135.0, "45° pitch must map to 135° clinical"
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv\Scripts\pytest tests\test_app.py::test_get_live_angle_maps_to_180_convention -v
```

Expected: FAIL — `180.0 != 0.0`

- [ ] **Step 3: Apply the fix**

In `pendulastic_app.py` line 140, change:

```python
# OLD (line 140)
            return float(_imu.get_state()["angles"]["pitch"])

# NEW
            return 180.0 - float(_imu.get_state()["angles"]["pitch"])
```

- [ ] **Step 4: Run the test to verify it passes**

```
.venv\Scripts\pytest tests\test_app.py::test_get_live_angle_maps_to_180_convention -v
```

Expected: PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_app.py tests\test_pt_score.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "fix: remap IMU get_live_angle to 180-degree clinical convention"
```

---

### Task 2: UI Root Window Scaling

**Files:**
- Modify: `pendulastic_app.py` lines 842–843 (`App.__init__` geometry block)
- Test: `tests/test_app.py` (add one new test)

**Interfaces:**
- Produces: `App` window opens at 900×740, allows resize, minimum size 700×680

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_root_window_is_resizable_and_wide():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        min_w, min_h = app.minsize()
        assert min_w >= 700, f"minsize width should be ≥700, got {min_w}"
        assert min_h >= 680, f"minsize height should be ≥680, got {min_h}"
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv\Scripts\pytest tests\test_app.py::test_root_window_is_resizable_and_wide -v
```

Expected: FAIL — minsize returns (0, 0)

- [ ] **Step 3: Apply the fixes**

In `pendulastic_app.py`, replace lines 842–843:

```python
# OLD (lines 842-843)
        self.geometry("500x740")
        self.resizable(False, True)

# NEW (replace with these 5 lines)
        self.geometry("900x740")
        self.resizable(True, True)
        self.minsize(700, 680)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
```

- [ ] **Step 4: Run the test to verify it passes**

```
.venv\Scripts\pytest tests\test_app.py::test_root_window_is_resizable_and_wide -v
```

Expected: PASS

- [ ] **Step 5: Run full app test suite**

```
.venv\Scripts\pytest tests\test_app.py -v
```

Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "fix: widen root window to 900px, allow resize, add grid weights"
```

---

### Task 3: Live Camera Preview + MediaPipe Landmark Overlay

**Files:**
- Modify: `pendulastic_app.py`
  - Top of file: add guarded mediapipe solution imports (after existing guarded imports)
  - `AcquisitionPanel._build_widgets` (~line 350): add `lbl_preview` widget
  - `AcquisitionPanel.enter_recording` (~line 384): swap canvas/preview based on source
  - `AcquisitionPanel.enter_idle` (~line 374): hide both canvas_tele and lbl_preview
  - `AcquisitionPanel.update_preview` (new method)
  - `App.__init__` (~line 851): add `_preview_queue` and `_pose_estimator`
  - `App._start_rgb_recording` (~line 978): init pose estimator + preview queue
  - `App._rgb_record_worker` (~line 1002): annotate copy → preview queue; write raw to disk
  - `App._tick` (~line 1069): drain preview queue
  - `App.on_stop` (~line 901): close pose estimator
- Test: `tests/test_acquisition_panel.py` (add 2 new tests)

**Interfaces:**
- Consumes: `_cv2` (already guarded at module level), `_VIEWER_AVAIL` flag
- Produces:
  - `AcquisitionPanel.lbl_preview: tk.Label` — 440×330 video preview, hidden at init
  - `AcquisitionPanel.update_preview(frame_bgr: np.ndarray) -> None`
  - `App._preview_queue: queue.Queue(maxsize=1)`
  - `App._pose_estimator: mp.solutions.pose.Pose | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_acquisition_panel.py`:

```python
def test_preview_label_exists():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert hasattr(p, "lbl_preview"), "lbl_preview widget must exist"
        assert p.lbl_preview.winfo_exists()
    finally:
        r.destroy()


def test_rgb_source_swaps_to_preview_during_recording():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_source_changed()
        p.enter_recording()
        r.update()
        # preview label must be gridded; sparkline must be hidden
        assert p.lbl_preview.grid_info() != {}, "lbl_preview should be visible during RGB recording"
        assert p.canvas_tele.grid_info() == {}, "canvas_tele should be hidden during RGB recording"
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_preview_label_exists tests\test_acquisition_panel.py::test_rgb_source_swaps_to_preview_during_recording -v
```

Expected: FAIL — `lbl_preview` does not exist yet

- [ ] **Step 3: Add guarded mediapipe imports**

In `pendulastic_app.py`, find the block of guarded imports near the top (after the `_cv2` guard, before `_IMU_AVAIL` checks). Add:

```python
_mp_pose = _mp_draw = _mp_styles = None
try:
    import mediapipe as _mp
    _mp_pose   = _mp.solutions.pose
    _mp_draw   = _mp.solutions.drawing_utils
    _mp_styles = _mp.solutions.drawing_styles
except ImportError:
    pass
```

- [ ] **Step 4: Add `lbl_preview` to `AcquisitionPanel._build_widgets`**

In `_build_widgets`, immediately after the `canvas_tele` creation (after line 352):

```python
        # row 13 — live telemetry canvas (NOT gridded at init; shown during RECORDING)
        self.canvas_tele = tk.Canvas(
            self, width=440, height=80, bg="#0B1928", highlightthickness=0)

        # row 13 alt — live video preview (shown instead of canvas_tele when RGB is recording)
        self.lbl_preview = tk.Label(self, bg="black")
        # not gridded at init; enter_recording() grids the correct one
```

- [ ] **Step 5: Update `enter_recording` to swap canvas/preview**

Replace the existing `enter_recording` method body (lines 384–390):

```python
    def enter_recording(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="START RECORDING",
                              bg=_GREEN, state="disabled")
        self.btn_stop.config(state="normal")
        if self._src_rgb.get():
            self.lbl_preview.grid(row=13, column=0, columnspan=2,
                                  padx=10, pady=4, sticky="nsew")
            self.canvas_tele.grid_remove()
        else:
            self.canvas_tele.grid(row=13, column=0, columnspan=2,
                                  padx=10, pady=4)
            self.lbl_preview.grid_remove()
        self.status_var.set("RECORDING…")
```

- [ ] **Step 6: Update `enter_idle` to hide both**

In `enter_idle` (line 374), the existing `self.canvas_tele.grid_remove()` line stays. Add one line after it:

```python
        self.canvas_tele.grid_remove()
        self.lbl_preview.grid_remove()   # ADD THIS LINE
```

- [ ] **Step 7: Add `update_preview` method to `AcquisitionPanel`**

Add after `enter_processing` (after line 395):

```python
    def update_preview(self, frame_bgr) -> None:
        """Convert a BGR numpy frame and display it in lbl_preview."""
        if not _CV2_AVAIL:
            return
        import base64
        h, w = frame_bgr.shape[:2]
        scale = min(440 / max(w, 1), 330 / max(h, 1))
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        small = _cv2.resize(frame_bgr, (nw, nh))
        rgb   = _cv2.cvtColor(small, _cv2.COLOR_BGR2RGB)
        ok, buf = _cv2.imencode(".png", rgb)
        if ok:
            b64 = base64.b64encode(buf).decode("utf-8")
            photo = tk.PhotoImage(data=b64)
            self.lbl_preview.config(image=photo)
            self.lbl_preview._photo = photo   # prevent GC
```

- [ ] **Step 8: Add `_preview_queue` and `_pose_estimator` to `App.__init__`**

After the existing `self._pending_review: dict = {}` declaration (line 853), add:

```python
        self._preview_queue:  queue.Queue = queue.Queue(maxsize=1)
        self._pose_estimator              = None
```

- [ ] **Step 9: Update `App._start_rgb_recording` to init pose estimator**

At the end of `_start_rgb_recording` (after line 1000, before starting the thread), add:

```python
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

        self._rgb_stop   = threading.Event()
        self._rgb_thread = threading.Thread(
            target=self._rgb_record_worker, daemon=True)
        self._rgb_thread.start()
```

(Remove the old `self._rgb_stop` and `self._rgb_thread` lines that were at lines 997–1000 — they move here.)

- [ ] **Step 10: Rewrite `App._rgb_record_worker`**

Replace the existing 4-line worker (lines 1002–1006) with:

```python
    def _rgb_record_worker(self) -> None:
        while not self._rgb_stop.is_set():
            ret, frame = self._rgb_cap.read()
            if not ret or frame is None:
                break

            # Write RAW frame to disk — preserves data integrity
            if self._rgb_writer:
                self._rgb_writer.write(frame)

            # Build annotated preview copy (overlay never touches the saved file)
            preview = frame.copy()
            if self._pose_estimator is not None and _mp_draw is not None:
                try:
                    import cv2 as _cv2_local
                    rgb_frame = _cv2_local.cvtColor(preview, _cv2_local.COLOR_BGR2RGB)
                    results   = self._pose_estimator.process(rgb_frame)
                    if results.pose_landmarks:
                        _mp_draw.draw_landmarks(
                            preview,
                            results.pose_landmarks,
                            _mp_pose.POSE_CONNECTIONS,
                            landmark_drawing_spec=_mp_styles.get_default_pose_landmarks_style(),
                        )
                except Exception:
                    pass   # never crash the recording on overlay failure

            # Deliver to UI thread — drop stale frame if queue is full
            try:
                self._preview_queue.put_nowait(preview)
            except queue.Full:
                pass
```

Note: `_cv2_local` inside the worker uses the stdlib import to avoid namespace issues with the module-level `_cv2` alias. If the module already imports as `import cv2 as _cv2`, use `_cv2` directly — verify the alias name at the top of the file before writing.

- [ ] **Step 11: Update `App._tick` to drain the preview queue**

Add before the final `self.after(50, self._tick)` line (after line 1078):

```python
        # Drain preview queue and update acquisition canvas during RGB recording
        if "rgb" in self._active_sources and self._state == "recording":
            try:
                frame = self._preview_queue.get_nowait()
                self._acq.update_preview(frame)
            except queue.Empty:
                pass
```

- [ ] **Step 12: Update `App.on_stop` to close pose estimator**

After the IMU thread join (after line 907, before `meta = self._acq.get_metadata()`), add:

```python
        # Close pose estimator if it was active
        if self._pose_estimator is not None:
            try:
                self._pose_estimator.close()
            except Exception:
                pass
            self._pose_estimator = None
```

- [ ] **Step 13: Run the tests**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_preview_label_exists tests\test_acquisition_panel.py::test_rgb_source_swaps_to_preview_during_recording -v
```

Expected: both PASS

- [ ] **Step 14: Run full suite**

```
.venv\Scripts\pytest tests\ -v
```

Expected: all pass (tkinter singleton flake may appear if all files run together — run suites individually if needed)

- [ ] **Step 15: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: live camera preview with MediaPipe landmark overlay during RGB recording"
```

---

### Task 4: File-First Video Upload Source

**Files:**
- Modify: `pendulastic_app.py`
  - `AcquisitionPanel._build_widgets` (~line 289): restructure `meth_f` to add 4th checkbox + path selector
  - `AcquisitionPanel._on_source_changed` (~line 439): add `"video_file"` label, show/hide path frame
  - `AcquisitionPanel.validate_metadata` (~line 400): two new guards
  - `AcquisitionPanel.get_active_sources` (~line 463): add `"video_file"`
  - `AcquisitionPanel.get_metadata` (~line 411): add `"video_file_path"`
  - `AcquisitionPanel._lockable` (~line 362): add `chk_video`
  - New `AcquisitionPanel` methods: `_on_browse_video`, `get_video_file_path`
  - `App.on_start` (~line 891): handle `"video_file"` source
  - New `App` methods: `_start_video_file_processing`, `_run_video_file_hpe`
  - `PostProcessingPanel._CURVE_STYLES` (line 598): add `"video_file"` entry
  - `PostProcessingPanel._PT_SOURCE_PRIORITY` (line 604): append `"video_file"`
- Test: `tests/test_acquisition_panel.py` (add 4 new tests)

**Interfaces:**
- Consumes:
  - `DataManager.build_filename(..., source="video_file")` — Task 2 of prior session
  - `BiomechanicalEngine("rgb").run_offline_track(path, progress_cb, leg=)` — existing
- Produces:
  - `AcquisitionPanel._src_video_file: tk.BooleanVar`
  - `AcquisitionPanel.get_video_file_path() -> str`
  - `AcquisitionPanel.get_metadata()` now returns `"video_file_path": str | None`
  - `App._start_video_file_processing(meta: dict) -> None`
  - `App._run_video_file_hpe(path: str, meta: dict) -> None`
  - `PostProcessingPanel._CURVE_STYLES["video_file"]` = `{"color": "#7C3AED", "ls": "--", "label": "Video File (HPE)"}`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_acquisition_panel.py`:

```python
def test_video_file_checkbox_exists():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert hasattr(p, "_src_video_file"), "_src_video_file BooleanVar must exist"
        assert p._src_video_file.get() is False, "Video file checkbox must be unchecked by default"
    finally:
        r.destroy()


def test_validate_rejects_video_file_and_rgb_together():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P1")
        p._src_video_file.set(True)
        p._src_rgb.set(True)
        p._stored_video_path = "/fake/video.mp4"
        ok, msg = p.validate_metadata()
        assert not ok
        assert "rgb" in msg.lower() or "video" in msg.lower() or "simultan" in msg.lower()
    finally:
        r.destroy()


def test_validate_rejects_video_file_without_path():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P1")
        p._src_optitrack.set(False)
        p._src_video_file.set(True)
        p._stored_video_path = ""
        ok, msg = p.validate_metadata()
        assert not ok
        assert "file" in msg.lower() or "select" in msg.lower()
    finally:
        r.destroy()


def test_get_metadata_includes_video_file_path():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P2")
        p._src_optitrack.set(False)
        p._src_video_file.set(True)
        p._stored_video_path = "/data/trial.mp4"
        meta = p.get_metadata()
        assert meta["video_file_path"] == "/data/trial.mp4"
        assert "video_file" in meta["sources"]
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_video_file_checkbox_exists tests\test_acquisition_panel.py::test_validate_rejects_video_file_and_rgb_together tests\test_acquisition_panel.py::test_validate_rejects_video_file_without_path tests\test_acquisition_panel.py::test_get_metadata_includes_video_file_path -v
```

Expected: FAIL — `_src_video_file` attribute not found

- [ ] **Step 3: Add the 4th checkbox and path row inside `meth_f`**

In `_build_widgets`, find the block that creates `meth_f` and the three checkboxes (lines 289–305). Restructure it so `meth_f` has two internal rows:

```python
        self._src_optitrack  = tk.BooleanVar(value=True)
        self._src_rgb        = tk.BooleanVar(value=False)
        self._src_imu        = tk.BooleanVar(value=False)
        self._src_video_file = tk.BooleanVar(value=False)   # NEW

        meth_f = tk.Frame(self)
        meth_f.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=2)

        # Inner row 1: the 4 source checkbuttons side-by-side
        chk_row = tk.Frame(meth_f)
        chk_row.pack(side="top", anchor="w")
        chk_opti  = tk.Checkbutton(chk_row, text="OptiTrack",
                                    variable=self._src_optitrack,
                                    command=self._on_source_changed)
        chk_rgb   = tk.Checkbutton(chk_row, text="RGB",
                                    variable=self._src_rgb,
                                    command=self._on_source_changed)
        chk_imu   = tk.Checkbutton(chk_row, text="iPhone IMU",
                                    variable=self._src_imu,
                                    command=self._on_source_changed)
        chk_video = tk.Checkbutton(chk_row, text="📁 Video File",   # NEW
                                    variable=self._src_video_file,
                                    command=self._on_source_changed)
        for chk in (chk_opti, chk_rgb, chk_imu, chk_video):
            chk.pack(side="left", padx=8)

        # Inner row 2: video file path selector (hidden until _src_video_file checked)
        self._video_path_frame = tk.Frame(meth_f)
        self._video_path_frame.pack(side="top", anchor="w", pady=(2, 0))
        self._video_path_var   = tk.StringVar(value="No file selected")
        self._stored_video_path = ""
        tk.Label(self._video_path_frame,
                 textvariable=self._video_path_var,
                 font=("Consolas", 8), fg="gray", width=38,
                 anchor="w").pack(side="left")
        tk.Button(self._video_path_frame, text="Browse…",
                  font=("Segoe UI", 8),
                  command=self._on_browse_video).pack(side="left", padx=4)
        self._video_path_frame.pack_forget()   # hidden until checkbox checked
```

- [ ] **Step 4: Add `chk_video` to `_lockable`**

In `_build_widgets`, change the `_lockable` list (line 362–366):

```python
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,   # add chk_video
            self.btn_zero, self.btn_clear_zero,
        ]
```

- [ ] **Step 5: Add `_on_browse_video` and `get_video_file_path` methods**

Add after `_on_clear_zero` (or any other private method — keep alphabetical if possible):

```python
    def _on_browse_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Select pre-recorded video",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"),
                       ("All files", "*.*")])
        if path:
            self._stored_video_path = path
            self._video_path_var.set(os.path.basename(path))
        # If user cancelled, keep existing path

    def get_video_file_path(self) -> str:
        """Return the currently selected video file path, or empty string."""
        return getattr(self, "_stored_video_path", "")
```

- [ ] **Step 6: Update `_on_source_changed` to handle `"video_file"`**

In `_on_source_changed` (line 439), add to `source_labels` dict and add path frame toggle:

```python
    def _on_source_changed(self) -> None:
        sources = self.get_active_sources()
        # Show/hide Zero Sensor frame
        if self._src_imu.get():
            self._zero_frame.grid()
        else:
            self._zero_frame.grid_remove()
        # Show/hide video file path frame
        if self._src_video_file.get():
            self._video_path_frame.pack(side="top", anchor="w", pady=(2, 0))
        else:
            self._video_path_frame.pack_forget()
        # Build status line
        source_labels = {
            "imu":        "iPhone IMU — waiting for phone" if _IMU_AVAIL else "iPhone IMU — unavailable",
            "rgb":        "RGB / MediaPipe" if _VIEWER_AVAIL else "RGB — MediaPipe unavailable",
            "optitrack":  "OptiTrack (Motive)" if _MOTIVE_AVAIL else "OptiTrack — Motive not found",
            "video_file": f"Video: {os.path.basename(self.get_video_file_path()) or 'no file'}",
        }
        if sources:
            label_parts = [source_labels[s] for s in sources]
            label = "● " + " + ".join(label_parts)
            color = "green"
        else:
            label = "● No source selected"
            color = "red"
        self.lbl_method_status.config(text=label, fg=color)
        self.controller.on_source_changed(sources)
```

- [ ] **Step 7: Update `get_active_sources`**

```python
    def get_active_sources(self) -> list:
        sources = []
        if self._src_imu.get():        sources.append("imu")
        if self._src_optitrack.get():  sources.append("optitrack")
        if self._src_rgb.get():        sources.append("rgb")
        if self._src_video_file.get(): sources.append("video_file")
        return sorted(sources)
```

- [ ] **Step 8: Update `validate_metadata`**

```python
    def validate_metadata(self) -> tuple:
        pid = self.pid_var.get().strip()
        if not pid:
            return False, "Participant ID cannot be empty."
        illegal = set('<>:"/\\|?*')
        if any(c in illegal for c in pid):
            return False, 'Participant ID contains illegal characters: < > : " / \\ | ? *'
        if not self.get_active_sources():
            return False, "Select at least one recording source."
        if self._src_video_file.get() and self._src_rgb.get():
            return False, "Cannot use 'Video File' and live RGB simultaneously."
        if self._src_video_file.get() and not self.get_video_file_path():
            return False, "Select a video file before starting."
        return True, ""
```

- [ ] **Step 9: Update `get_metadata`**

```python
    def get_metadata(self) -> dict:
        return {
            "pid":             self.pid_var.get().strip(),
            "leg":             self.leg_var.get(),
            "ms_status":       self.ms_var.get(),
            "trial":           int(self.trial_var.get()),
            "sources":         self.get_active_sources(),
            "video_file_path": self.get_video_file_path() if self._src_video_file.get() else None,
        }
```

- [ ] **Step 10: Update `App.on_start` to handle `"video_file"` source**

In `App.on_start` (find the `for src in sources:` loop), add:

```python
            elif src == "video_file":
                self._start_video_file_processing(meta)
```

- [ ] **Step 11: Add `_start_video_file_processing` and `_run_video_file_hpe` to `App`**

Add these two methods to `App` (near `_start_rgb_recording`):

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

        source_angles = dict(self._pending_review)
        source_angles["video_file"] = angles
        self.after(0, lambda: self._transition_to_review(source_angles, meta))
```

- [ ] **Step 12: Update `PostProcessingPanel._CURVE_STYLES` and `_PT_SOURCE_PRIORITY`**

Find lines 598–604 and update:

```python
    _CURVE_STYLES = {
        "imu":        {"color": "#2563EB", "ls": "-",   "label": "IMU"},
        "rgb":        {"color": "#16A34A", "ls": "-",   "label": "RGB"},
        "optitrack":  {"color": "#D97706", "ls": "--",  "label": "OptiTrack"},
        "hpe_upload": {"color": "#7C3AED", "ls": "--",  "label": "HPE Upload"},
        "video_file": {"color": "#7C3AED", "ls": "--",  "label": "Video File (HPE)"},   # NEW
    }
    _PT_SOURCE_PRIORITY = ["imu", "rgb", "optitrack", "hpe_upload", "video_file"]   # add "video_file"
```

- [ ] **Step 13: Run the failing tests to verify they now pass**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_video_file_checkbox_exists tests\test_acquisition_panel.py::test_validate_rejects_video_file_and_rgb_together tests\test_acquisition_panel.py::test_validate_rejects_video_file_without_path tests\test_acquisition_panel.py::test_get_metadata_includes_video_file_path -v
```

Expected: all 4 PASS

- [ ] **Step 14: Run full test suite**

```
.venv\Scripts\pytest tests\ -v
```

Expected: all pass

- [ ] **Step 15: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: add file-first video upload source checkbox with HPE pipeline"
```

---

## Self-Review

**Spec coverage:**
- §3 Knee angle fix → Task 1 ✓
- §6 UI scaling → Task 2 ✓
- §4A–4C Live preview canvas + MediaPipe worker + tick drain → Task 3 ✓
- §5A–5E File-first upload, validation, lifecycle, colours → Task 4 ✓

**Placeholder scan:** No TBDs, all code blocks are complete.

**Type consistency:**
- `get_video_file_path() -> str` used in Task 4 Step 10 (`_start_video_file_processing`) → defined in Task 4 Step 5 ✓
- `_src_video_file` used in Steps 6–9 → defined in Step 3 ✓
- `update_preview(frame_bgr)` called in Task 3 Step 11 → defined in Task 3 Step 7 ✓
- `_preview_queue` used in Steps 10–12 → declared in Step 8 ✓
- `_stored_video_path` initialised in Step 3, read in Step 5 → consistent ✓
