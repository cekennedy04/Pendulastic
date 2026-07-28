# Pendulastic Enhancements — Design Spec
**Date:** 2026-07-28
**Status:** Approved

---

## 1. Goal

Four targeted enhancements to the Pendulastic unified app (`pendulastic_app.py`) and its scoring backend (`pendulastic_pt_score.py`):

1. **Angle offset calibration** — expose the IMU server's existing `zero()` function via a "Zero Sensor" button so "parallel to ground" maps to 0°.
2. **Robust PT extraction** — fix baseline drift and A0/A1 miscalculation in `compute_pt_params` via linear detrending and a wider initial-peak window.
3. **Multi-source sync** — replace single-select radio buttons with multi-select checkboxes; record OptiTrack + IMU + RGB simultaneously, each to its own CSV.
4. **Video upload for HPE** — add a "🎥 Upload Video for HPE" button to `PostProcessingPanel` that runs MediaPipe offline on a user-selected file and overlays the result on the angle plot.

Existing files that must **not** be modified: `pendulastic_viewer.py`, `pendulastic_imu_server.py`, `motive_sync.py`.

---

## 2. File Impact

| File | Nature of change |
|---|---|
| `pendulastic_pt_score.py` | `compute_pt_params`: add `detrend` param, linear detrend step, wider A0 window |
| `pendulastic_app.py` | `DataManager.build_filename`: add `source` param · `AcquisitionPanel`: checkboxes, Zero button, `_on_source_changed` · `PostProcessingPanel`: multi-curve plot, video upload button · `App`: multi-source recording lifecycle |

---

## 3. Signal Pipeline

### 3A. Angle Offset Calibration

**Root cause:** The IMU sensor reads ~4° when the participant's leg is horizontal due to sensor mounting geometry. `pendulastic_imu_server.py` already provides `zero()` and `clear_zero()` — the app simply needs to expose them.

**Calibration posture:** The clinician holds the participant's leg fully extended and horizontal (the test starting position), then presses "Zero". This makes the pre-release extended position read 0°, and subsequent oscillations are measured relative to it.

**No cross-session persistence:** The offset is held in `_imu._offset` for the lifetime of the server process. On each new session, the clinician re-zeros. This is intentional — sensor placement may shift between visits.

**UI additions to `AcquisitionPanel`:**

- A `"⊙ Zero Sensor"` button added to row 9 (the methodology status row), visible and **enabled only when**:
  - `_src_imu.get() is True` (IMU checkbox is checked), AND
  - Current state is `IDLE`.
- Pressing it calls `_imu.zero()` and updates `lbl_method_status` to `"● Sensor zeroed — horizontal = 0°"`.
- A compact `"↺ Clear"` text-button beside it calls `_imu.clear_zero()` and resets the status line to `"● iPhone IMU — waiting for phone"`.
- Both buttons are added to `self._lockable` so they disable during recording.

### 3B. Robust PT Extraction

**Root cause:** IMU gyro integration error causes 1–3° of monotonic baseline drift over a 10–15 s trial. `phi = angle − neutral` inherits this drift: the tail-median neutral estimate is pulled off-centre, shrinking A0 and misaligning zero-crossings for late oscillations. A0 can also be missed if the initial peak falls just outside the 15% detection window.

**Changes to `compute_pt_params(t, angle_raw, release_idx=None, detrend=True)`:**

1. **New parameter:** `detrend: bool = True`. When `True`, applies `scipy.signal.detrend(ang_c, type='linear')` to the finite-compressed angle array immediately after the finite-mask step, before the Savitzky-Golay pass. Set `detrend=False` for OptiTrack signals, which are marker-based and do not exhibit IMU drift.

2. **Wider A0 window:** First-swing detection window expands from 15% → 20% of post-release samples (`first_n = max(5, int(0.20 * len(phi)))`).

3. **A0 floor guard:** `A0 = max(float(np.nanmax(phi_s[:first_n])), A0_raw)` — prevents the detrend from shifting the first peak slightly below `A0_raw` and replacing a valid initial amplitude with a smaller value.

No other changes to `compute_pt_params`. All existing public keys in the returned dict are preserved.

---

## 4. Multi-Source Recording

### 4A. AcquisitionPanel — Methodology selector refactor

**Before:** `method_var: tk.StringVar` + three `tk.Radiobutton` (mutually exclusive).

**After:** Three independent `tk.BooleanVar` + three `tk.Checkbutton`:

```python
self._src_optitrack = tk.BooleanVar(value=True)   # default: OptiTrack checked
self._src_rgb       = tk.BooleanVar(value=False)
self._src_imu       = tk.BooleanVar(value=False)
```

Row 8 layout:
```
☑ OptiTrack    ☐ RGB    ☐ iPhone IMU
```

All three checkbuttons are added to `self._lockable`.

**`_on_source_changed()`** replaces `_on_method_changed()`. Called by any checkbox toggle. Rebuilds `lbl_method_status` to list all active sources, e.g.:
```
● OptiTrack (Motive) + iPhone IMU — waiting for phone
```
Also shows/hides the Zero Sensor button based on `_src_imu.get()`.
Calls `self.controller.on_source_changed(self.get_active_sources())`.

**`get_active_sources() -> list[str]`** returns the sorted list of checked sources, e.g. `["imu", "optitrack"]`.

**`validate_metadata()`** updated: returns `(False, "Select at least one recording source.")` when no checkbox is checked.

**`get_metadata()`** updated: returns `"sources": list[str]` (replaces `"methodology": str`).

### 4B. DataManager — source suffix

`build_filename` gains an optional `source: str | None = None` parameter:

```python
@staticmethod
def build_filename(pid, leg, ms_status, trial, source=None) -> str:
    leg_s  = leg.capitalize()
    ms_s   = ms_status.replace(" ", "_")
    suffix = f"_{source}" if source else ""
    return f"PID_{pid}_LEG_{leg_s}_{ms_s}_TRIAL_{trial}{suffix}.csv"
```

Examples:
- `source=None` → `PID_P1_LEG_Right_MS_TRIAL_1.csv` (backward-compat; used for OptiTrack post-hoc load)
- `source="imu"` → `PID_P1_LEG_Right_MS_TRIAL_1_imu.csv`
- `source="rgb"` → `PID_P1_LEG_Right_MS_TRIAL_1_rgb.csv`

### 4C. App — multi-source recording lifecycle

`App` gains:
```python
self._active_sources: list[str] = []
```

**`on_source_changed(sources: list[str])`** (new controller method): updates `self._active_sources` preview and status line.

**`on_start()`:**
```python
meta    = self._acq.get_metadata()
sources = meta["sources"]
self._active_sources = sources
self._engine = BiomechanicalEngine("imu" if "imu" in sources else "rgb" if "rgb" in sources else "optitrack")
self._rec_angles     = {}   # dict[str, list[float]]
self._rec_timestamps = {}   # dict[str, list[float]]
self._acq.clear_telemetry()

for src in sources:
    if src == "imu":       self._start_imu_recording(meta)
    elif src == "rgb":     self._start_rgb_recording(meta)
    elif src == "optitrack": self._start_optitrack_recording(meta)

self._state = "recording"
self._acq.enter_recording()
```

**`_tick()`:** now appends to `self._rec_angles.setdefault("imu", [])` and `self._rec_timestamps.setdefault("imu", [])` (dict instead of flat list).

**`on_stop()`:**
- Stop IMU poll thread (unconditional).
- For each source in `_active_sources`:
  - `"imu"` → `_imu.stop_recording()`, `DataManager.save_trial(fn_imu, angles["imu"], meta, timestamps=ts["imu"], source="imu")`
  - `"rgb"` → `_stop_rgb_recording()`, spawn MediaPipe background thread
  - `"optitrack"` → `_motive.stop_local_motive()` (non-fatal)
- If `"rgb"` in `_active_sources` → `PROCESSING` state (MediaPipe runs offline)
- Else → `_transition_to_review(source_angles, meta)`

**`_run_rgb_processing(meta)`:** on completion, calls `self._after(0, lambda: self._add_rgb_angles_to_review(fn_rgb, angles, meta))` which merges into the existing review if other sources already transitioned.

**`_transition_to_review(source_angles: dict[str, list[float]], meta: dict)`:**
- Sets `_state = "review"`
- Calls `self._post.load_trial(source_angles, fps=30.0, metadata=meta, base_filename=base_fn)`
- Swaps panels

**State machine:** unchanged. PROCESSING triggers only when `"rgb"` is in `_active_sources`.

---

## 5. PostProcessingPanel Upgrades

### 5A. Multi-curve display

**`load_trial` new signature:**
```python
def load_trial(
    self,
    source_angles: dict[str, list[float]],  # {"imu": [...], "rgb": [...]}
    fps: float,
    metadata: dict,
    base_filename: str,                      # stem without source suffix
) -> None:
```

`self._meta: dict` stored for downstream use (leg for HPE track, etc.). `self._fps` and `self._source_angles` stored for re-plot on overlay additions.

**Source colour palette** (fixed, no user configuration):

| Source key | Colour | Line style |
|---|---|---|
| `"imu"` | `#2563EB` (blue) | solid |
| `"rgb"` | `#16A34A` (green) | solid |
| `"optitrack"` | `#D97706` (amber) | dashed |
| `"hpe_upload"` | `#7C3AED` (violet) | dashed |

Legend rendered only when ≥ 2 curves are present (avoids visual clutter for single-source trials).

**PT metrics source priority:** `imu → rgb → optitrack → hpe_upload`. The `LabelFrame` title shows the source: `"Popović PT Metrics (source: IMU)"`. `detrend=True` for IMU source, `detrend=False` for RGB/OptiTrack/HPE.

### 5B. Layout change — row 3 gains third column

```
row 3 │ col 0: ← New Trial  │ col 1: 📂 Load OptiTrack CSV  │ col 2: 🎥 Upload Video for HPE
```

`columnconfigure(2, weight=1)` added. Both existing buttons remain in their cells.

### 5C. Video upload for HPE

**`_on_upload_video()`:**

1. Guard: if `not _VIEWER_AVAIL`: `messagebox.showerror(...)`, return.
2. `path = filedialog.askopenfilename(title="Select video for HPE", filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("All files", "*.*")])` — return if empty.
3. `status_var.set("HPE processing: 0%")`
4. Spawn background thread: `BiomechanicalEngine("rgb").run_offline_track(path, progress_cb, leg=self._meta.get("leg", "right") if self._meta else "right")`
5. `progress_cb(pct)` marshalled to main thread via `self.after(0, ...)` → updates status bar.
6. On completion → main thread calls `_add_hpe_overlay(angles, fps)`:
   - Adds `"hpe_upload"` to `self._source_angles`, re-plots all curves.
   - If PT source priority reaches `"hpe_upload"`, recalculates metrics.
   - `status_var.set(f"HPE overlay loaded — {len(angles)} frames")`
7. If returned `angles` list is empty: `status_var.set("HPE: no pose detected — check video or leg selection.")` — no crash, no messagebox.

**Empty-panel behaviour:** If `load_trial` has never been called (`self._meta` is `None`), the upload still works — it plots the HPE overlay on an otherwise empty axis. `base_filename` title label shows the video filename stem.

---

## 6. Thread Safety

| Resource | Threads | Guard |
|---|---|---|
| `_rec_angles["imu"]` / `_rec_timestamps["imu"]` | IMU poll thread → `_tick()` (main) | `queue.Queue` (existing) |
| `_rec_angles["rgb"]` / video file | RGB frame thread + main thread | `threading.Event` (existing `_rgb_stop`) |
| IMU server `_offset` | App main thread (Zero button) | `_lock` inside `pendulastic_imu_server.zero()` |
| HPE overlay computation | Background thread → main thread | `self.after(0, ...)` marshal |

---

## 7. Testing

New/updated test files:

| Test file | What it covers |
|---|---|
| `tests/test_data_manager.py` | `build_filename` with `source` param; backward-compat (`source=None`) |
| `tests/test_pt_score.py` | `compute_pt_params` with `detrend=True` on a drifting signal; wider A0 window; `detrend=False` preserves OptiTrack signal |
| `tests/test_acquisition_panel.py` | Multi-source checkbox validation; `get_active_sources()`; Zero button visibility; `get_metadata()` returns `"sources"` list |
| `tests/test_post_processing_panel.py` | `load_trial` with multi-source dict; multi-curve plot labels; HPE upload button exists; `_add_hpe_overlay` adds curve |

---

## 8. Out of Scope

- Merging multi-source angles into a single unified CSV (deferred — sources are async)
- Cross-session persistence of the IMU zero offset
- Modifying `pendulastic_imu_server.py` or `pendulastic_viewer.py`
- Changing the Madgwick AHRS filter parameters in the IMU server
- Batch processing of multiple video files
