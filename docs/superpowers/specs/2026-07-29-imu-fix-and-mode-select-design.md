# IMU Quaternion Fix & Mode Select Landing Screen — Design Spec
**Date:** 2026-07-29
**Status:** Approved

---

## 1. Goal

Two orthogonal improvements addressing runtime failures and workflow friction:

1. **IMU Quaternion Swing Angle** — replace the Euler-pitch-based angle extraction (which suffers from a ±90° asin singularity) with a quaternion rotation-distance calculation that is axis-agnostic, gimbal-lock-free, and mounting-independent. Once the IMU signal correctly sweeps 180°→90°, `compute_pt_params` receives a valid oscillation waveform and the blank PT metrics card resolves automatically.
2. **Mode Selection Landing Screen** — introduce a startup routing layer that splits the application into a Live Recording path (hardware acquisition) and an Upload & Analyze path (file-first processing without touching any hardware setup).

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_imu_server.py` | Add `_IMUDevice.get_quaternion()` · add module-level `_q_zero_prox`, `_q_zero_dist` · update `zero()` and `clear_zero()` · add `swing_angle_deg()` · add `"swing_angle_deg"` key to `get_state()` |
| `pendulastic_app.py` | `BiomechanicalEngine.get_live_angle()` reads `swing_angle_deg` · add `ModeSelectView` · add `UploadMetaView` · update `App.__init__` to show mode select first · add `_run_csv_analysis()` · refactor `_run_video_file_hpe()` progress callback · add Back buttons to `AcquisitionPanel` and `PostProcessingPanel` · add `"upload_csv"` to `_CURVE_STYLES` and `_PT_SOURCE_PRIORITY` |
| `pendulastic_pt_score.py` | No changes — PT parameter extraction works correctly once the angle signal is valid |
| `pendulastic_imu_server.py` | No changes to `pendulastic_viewer.py` or `pendulastic_imu_server.py` beyond what is listed |

---

## 3. Section 1 — IMU Quaternion Swing Angle

### 3A. Root Cause

`euler_deg()` in `MadgwickAHRS` extracts pitch via:
```python
sin_p = max(-1.0, min(1.0, 2 * (q1 * q3 - q4 * q2)))
pitch = math.asin(sin_p)
```
In the ZYX Euler convention, pitch is geometrically bounded to ±90° — not a software clamp but a mathematical property of the decomposition. If the pendulum motion is captured in the `roll` axis (extracted with `atan2`, full ±180° range) rather than `pitch`, the `pitch` signal barely changes and `get_live_angle() = 180 - (~0°) ≈ 180°` — exactly the flatline observed. Fixing the `asin` call alone cannot resolve this; the correct fix is to abandon Euler coordinates entirely for the primary angle measurement.

### 3B. Quaternion Rotation Distance

The angular displacement from a reference pose is computed directly from quaternions:

```
θ = 2 · acos(| dot(q_zero, q_current) |)
```

`dot(q_a, q_b)` between two unit quaternions equals `cos(θ/2)` where θ is the rotation angle between them. Taking `abs()` handles the quaternion double-cover (q and −q represent the same rotation). This formula:
- Captures motion on **any axis** — no assumption about phone mounting orientation
- Spans **0° → 180°** continuously — no gimbal singularity
- Naturally maps to clinical convention: `get_live_angle() = 180° − θ`

For **two phones** (proximal + distal segment), the relative orientation is measured:
```
q_rel = conj(q_prox) ⊗ q_dist
θ = 2 · acos(| dot(q_rel_zero, q_rel_current) |)
```

### 3C. Changes to `pendulastic_imu_server.py`

#### `_IMUDevice.get_quaternion() -> np.ndarray`

New method returning the device's current orientation as a unit quaternion, regardless of data source:

- **AHRS mode** (`from_orientation_stream = False`): returns `self.ahrs.q.copy()`
- **Orientation-stream mode** (`from_orientation_stream = True`): converts stored Euler angles to a quaternion using the standard ZYX formula:

```python
def get_quaternion(self) -> np.ndarray:
    if not self.from_orientation_stream:
        return self.ahrs.q.copy()
    r = math.radians(self.roll)
    p = math.radians(self.pitch)
    y = math.radians(self.yaw)
    cr, cp, cy = math.cos(r / 2), math.cos(p / 2), math.cos(y / 2)
    sr, sp, sy = math.sin(r / 2), math.sin(p / 2), math.sin(y / 2)
    return np.array([
        cy * cp * cr + sy * sp * sr,
        cy * cp * sr - sy * sp * cr,
        sy * cp * sr + cy * sp * cr,
        sy * cp * cr - cy * sp * sr,
    ])
```

#### Module-level quaternion zero storage

```python
_q_zero_prox: Optional[np.ndarray] = None
_q_zero_dist: Optional[np.ndarray] = None
```

#### Helper functions

```python
def _qconj(q: np.ndarray) -> np.ndarray:
    return np.array([q[0], -q[1], -q[2], -q[3]])

def _qmul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.array([
        a[0]*b[0] - a[1]*b[1] - a[2]*b[2] - a[3]*b[3],
        a[0]*b[1] + a[1]*b[0] + a[2]*b[3] - a[3]*b[2],
        a[0]*b[2] - a[1]*b[3] + a[2]*b[0] + a[3]*b[1],
        a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + a[3]*b[0],
    ])
```

#### `zero()` update

Existing zero() captures the Euler offset. It also now captures the current quaternion state:

```python
def zero():
    global _q_zero_prox, _q_zero_dist
    with _lock:
        cur = _raw_relative()
        for k in ("roll", "pitch", "yaw"):
            if math.isfinite(cur[k]):
                _offset[k] = cur[k]
        prox = _by_role(ROLE_PROXIMAL)
        dist = _by_role(ROLE_DISTAL)
        if prox is not None and prox.connected:
            _q_zero_prox = prox.get_quaternion()
        if dist is not None and dist.connected:
            _q_zero_dist = dist.get_quaternion()
        elif _q_zero_dist is None:
            # Single-phone fallback: store whichever phone is connected
            solo = next((d for d in (dist, prox)
                         if d is not None and d.connected), None)
            if solo is not None:
                _q_zero_dist = solo.get_quaternion()
```

#### `clear_zero()` update

```python
def clear_zero():
    global _q_zero_prox, _q_zero_dist
    with _lock:
        for k in _offset:
            _offset[k] = 0.0
        _q_zero_prox = None
        _q_zero_dist = None
```

#### New `swing_angle_deg() -> float`

```python
def swing_angle_deg() -> float:
    """Quaternion rotation distance from zeroed reference pose.

    Returns NaN before zero() is called (operator must calibrate first).
    Two-phone: measures relative joint angle change from the zeroed pose.
    Single-phone: measures absolute segment rotation from the zeroed pose."""
    with _lock:
        if _q_zero_dist is None:
            return float("nan")

        prox = _by_role(ROLE_PROXIMAL)
        dist = _by_role(ROLE_DISTAL)

        if (prox is not None and dist is not None
                and prox.connected and dist.connected
                and _q_zero_prox is not None):
            # Two-phone: relative quaternion at zero vs current
            q_rel_zero = _qmul(_qconj(_q_zero_prox), _q_zero_dist)
            q_rel_cur  = _qmul(_qconj(prox.get_quaternion()),
                                dist.get_quaternion())
            dot = float(np.dot(q_rel_zero, q_rel_cur))
        else:
            # Single-phone fallback
            solo = next(
                (d for d in (dist, prox) if d is not None and d.connected),
                None)
            if solo is None:
                return float("nan")
            q_zero = _q_zero_dist if (dist is not None and dist.connected) \
                     else _q_zero_prox
            if q_zero is None:
                return float("nan")
            dot = float(np.dot(q_zero, solo.get_quaternion()))

        dot = max(-1.0, min(1.0, abs(dot)))
        return 2.0 * math.degrees(math.acos(dot))
```

#### `get_state()` addition

Add one key to the existing dict returned by `get_state()`:

```python
"swing_angle_deg": swing_angle_deg(),
```

### 3D. Changes to `pendulastic_app.py`

#### `BiomechanicalEngine.get_live_angle()`

Replace the current Euler-pitch extraction:

```python
# OLD
return 180.0 - float(_imu.get_state()["angles"]["pitch"])

# NEW
swing = _imu.get_state().get("swing_angle_deg", float("nan"))
if math.isfinite(swing):
    return 180.0 - swing
return float("nan")
```

`math` is already imported at module level. No other changes — the angle value flows into `_tick()`, `_rec_angles["imu"]`, and ultimately `compute_pt_params` unchanged.

### 3E. PT Parameter Restoration

No changes to `pendulastic_pt_score.py`. Once `get_live_angle()` returns values sweeping 180°→~90° over the pendulum swing, `compute_pt_params` receives a valid oscillation waveform. The `_sg` Savitzky-Golay smoother, `find_peaks`, and zero-crossing logic all operate correctly on this range. The blank PT metrics card is a downstream symptom that resolves automatically.

### 3F. Pre-zero Behaviour

Before `zero()` is called, `swing_angle_deg()` returns `nan`. `get_live_angle()` propagates `nan`. `_tick()` skips appending `nan` to `_rec_angles["imu"]`. The telemetry sparkline shows no trace. The `AcquisitionPanel` status label and the Zero Sensor button guide the operator to calibrate before recording.

---

## 4. Section 2 — Mode Selection Landing Screen

### 4A. Architecture Overview

`App.__init__` creates all three panels (`ModeSelectView`, `AcquisitionPanel`, `PostProcessingPanel`) but only packs `ModeSelectView` on startup. Panel switching is handled by `pack_forget()` / `pack()` — the same pattern used between `AcquisitionPanel` and `PostProcessingPanel` today. No structural refactor needed.

`App._state` gains three new values:

| State | Visible panel |
|---|---|
| `"mode_select"` | `ModeSelectView` |
| `"upload_meta"` | `UploadMetaView` (embedded in `ModeSelectView` or separate frame) |
| `"upload_processing"` | `UploadMetaView` (progress label active) |
| `"idle"`, `"recording"`, `"processing"`, `"review"` | unchanged |

### 4B. `ModeSelectView(tk.Frame)`

Displayed on application launch. Contains:

- **Title row:** `"Pendulastic"` heading + `"Clinical Pendulum Test Platform"` subtitle
- **Button row** (two large side-by-side buttons, equal width):
  - `"🔴  Live Recording Session"` with sub-label `"IMU · RGB · OptiTrack"` → calls `App._enter_live_mode()`
  - `"📁  Upload & Analyze"` with sub-label `"Video or CSV file"` → calls `App._enter_upload_mode()`

`App._enter_live_mode()`:
1. Sets `_state = "idle"`
2. `_mode_select.pack_forget()`
3. `_acq.pack(fill="both", expand=True)`

`App._enter_upload_mode()`:
1. Opens `filedialog.askopenfilename(filetypes=[("Video/CSV", "*.mp4 *.avi *.mov *.mkv *.csv"), ("All", "*.*")])`
2. If no file selected: returns immediately (stays on mode select)
3. If file selected: stores path, sets `_state = "upload_meta"`, shows `UploadMetaView`

### 4C. `UploadMetaView(tk.Frame)`

Compact metadata form shown after file selection. Created once in `App.__init__`, hidden until needed.

**Layout (top to bottom):**

```
┌─────────────────────────────────────────────┐
│ ← Back    Upload & Analyze                  │
│ File: video_trial_03.mp4                    │
├────────────────┬────────────────────────────┤
│ Participant ID │ Leg:  ● Left  ● Right       │
│ [____________] │                             │
├────────────────┼────────────────────────────┤
│ MS Status      │ Trial                       │
│ [MAS 0 ▼]      │ [1   ▲▼]                   │
├────────────────┴────────────────────────────┤
│              [ Analyze → ]                  │
│ Status: Ready                               │
└─────────────────────────────────────────────┘
```

**`← Back` button:** `App._upload_back_to_select()` — calls `_upload_meta.pack_forget()`, `_mode_select.pack(fill="both", expand=True)`, sets `_state = "mode_select"`. Only enabled when `_state in ("upload_meta",)` (disabled while processing).

**`Analyze →` button:** Calls `App._start_upload_analysis()`:
1. Reads metadata from `UploadMetaView` widgets — same `pid_var`, `leg_var`, `ms_var`, `trial_var` as `AcquisitionPanel`
2. Validates: pid non-empty, file path set
3. Sets `_state = "upload_processing"`, disables Back + Analyze buttons, sets status label
4. Dispatches background thread: `_run_video_file_hpe(path, meta)` or `_run_csv_analysis(path, meta)` based on file extension
5. Thread completion calls `self.after(0, lambda: self._transition_to_review(source_angles, meta))`

**File type routing** (by `os.path.splitext(path)[1].lower()`):
- `.mp4 .avi .mov .mkv` → `_run_video_file_hpe(path, meta)` (existing method, progress callback writes to `UploadMetaView.status_var`)
- `.csv` → new `_run_csv_analysis(path, meta)`

### 4D. `_run_csv_analysis(path, meta)`

Reads a CSV produced by `DataManager.save_trial()`. Expected columns: `t_rel` (seconds, float) and `angle` (degrees, float). Rows with non-numeric values are skipped silently.

```python
def _run_csv_analysis(self, path: str, meta: dict) -> None:
    import csv as _csv_mod
    t_vals, angle_vals = [], []
    with open(path, newline="", encoding="utf-8") as f:
        reader = _csv_mod.DictReader(
            (row for row in f if not row.startswith("#")))
        for row in reader:
            try:
                t_vals.append(float(row["t_rel"]))
                angle_vals.append(float(row["angle"]))
            except (KeyError, ValueError):
                pass
    if not angle_vals:
        self.after(0, lambda: self._upload_meta.status_var.set(
            "Error: no valid angle data found in CSV"))
        return
    source_angles = {"upload_csv": angle_vals}
    fn = DataManager.build_filename(
        meta["pid"], meta["leg"], meta["ms_status"],
        meta["trial"], source="upload_csv")
    DataManager.save_trial(fn, angle_vals, meta,
                           timestamps=t_vals, source="upload_csv")
    self.after(0, lambda: self._transition_to_review(source_angles, meta))
```

The `"#"` prefix filter skips the metadata comment rows written by `DataManager.save_trial()` and by `pendulastic_imu_server.start_recording()`.

### 4E. `_run_video_file_hpe` Progress Callback

The existing method writes progress to `self._acq.status_var`. When called from the upload path, progress must go to `self._upload_meta.status_var` instead. Refactor the progress callback to be passed in rather than hardcoded:

```python
def _run_video_file_hpe(self, path: str, meta: dict,
                         progress_target: Optional[tk.StringVar] = None) -> None:
    target = progress_target or self._acq.status_var
    def progress(pct: float) -> None:
        self.after(0, lambda p=pct: target.set(
            f"HPE processing: {int(p * 100)}%"))
    ...
```

`_start_video_file_processing()` (live AcquisitionPanel path) passes `progress_target=None` (defaults to `_acq.status_var`). `_start_upload_analysis()` passes `progress_target=self._upload_meta.status_var`.

### 4F. Back Navigation

**`AcquisitionPanel` header:** Add a `"← Mode Select"` button to row 0 of `AcquisitionPanel._build_widgets()`. Calls `controller.on_back_to_mode_select()`. Only enabled when `_state == "idle"` (locked out during recording and processing via `_lockable`).

**`PostProcessingPanel` header:** Add `"← Mode Select"` button next to existing New Trial button. Calls `controller.on_back_to_mode_select()`.

**`App.on_back_to_mode_select()`:**
```python
def on_back_to_mode_select(self) -> None:
    self._acq.pack_forget()
    self._post.pack_forget()
    self._upload_meta.pack_forget()
    self._mode_select.pack(fill="both", expand=True)
    self._state = "mode_select"
    self._active_sources = []
    self._rec_angles = {}
    self._rec_timestamps = {}
    self._pending_review = {}
```

### 4G. `PostProcessingPanel` — `"upload_csv"` Curve Style

```python
"upload_csv": {"color": "#0891B2", "ls": "--", "label": "CSV Upload"},
```

Added to `_CURVE_STYLES` and appended to `_PT_SOURCE_PRIORITY` after `"video_file"`.

### 4H. New `App._state` Values in `_tick()`

`_tick()` currently guards the IMU poll path with `self._state == "recording"`. The upload processing states (`"upload_meta"`, `"upload_processing"`, `"mode_select"`) should not trigger IMU polling or sparkline updates. No changes needed — the existing guard `if self._state not in ("recording",):` already handles new states by falling through.

---

## 5. Thread Safety

| Resource | Threads | Guard |
|---|---|---|
| `_q_zero_prox`, `_q_zero_dist` | IMU WS thread (write in `zero()`) + main thread (read in `swing_angle_deg()`) | `_lock` (existing RLock) |
| `UploadMetaView.status_var` | Background analysis thread (via `self.after(0, ...)`) | `self.after(0, ...)` marshal |
| `_run_csv_analysis` file I/O | Daemon background thread, no shared state | N/A |

---

## 6. Out of Scope

- Live sparkline preview in `ModeSelectView` during standby
- Multi-file batch upload
- Quaternion logging to the IMU CSV (existing CSV columns unchanged)
- Axis-swap controls for custom phone mounting configurations
