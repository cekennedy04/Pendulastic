# IMU Quaternion Fix & Mode Select Landing Screen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Euler-pitch IMU angle (clamped ±90°) with a quaternion rotation-distance signal, and add a mode-select landing screen that routes the user to either live recording or file-first upload analysis.

**Architecture:** Two independent changes in two files. `pendulastic_imu_server.py` gains quaternion helpers and a `swing_angle_deg()` function; `pendulastic_app.py` reads that key for the live angle, and gains `ModeSelectView` + `UploadMetaView` classes that replace the direct-to-AcquisitionPanel startup flow.

**Tech Stack:** Python 3.11 · Tkinter · NumPy (already a dependency) · pytest · .venv at repo root

## Global Constraints

- Python interpreter: `.venv\Scripts\python.exe` (never `freemocap-env`)
- Run tests with: `.venv\Scripts\python.exe -m pytest tests/ -x -q`
- Pre-existing failures in `test_metrics.py`, `test_pose.py`, `test_stats.py`, `test_video.py` (bad imports from non-existent `pendulastic.*` module) are **not our defects** — do not fix them; do not count them as failures
- Pre-existing Tkinter Tcl init flakiness: 2 tests (`test_panel_instantiates`, `test_clear_telemetry_removes_all_items`) fail intermittently with `TclError` when multiple `tk.Tk()` instances exist in one pytest session — not our defect
- No new files except `tests/test_imu_server.py`; all changes go into `pendulastic_imu_server.py` and `pendulastic_app.py`
- `import math` is NOT at module level in `pendulastic_app.py` (only inside `_draw_sparkline`); Task 2 must add it to the top-level imports
- `DataManager.save_trial()` writes columns `frame, time_s, knee_angle_deg, pid, leg, ms_status, trial, methodology` — `_run_csv_analysis` must accept both `time_s`/`t_rel` for time and `knee_angle_deg`/`angle` for angle

---

### Task 1: IMU Server Quaternion Infrastructure

**Files:**
- Modify: `pendulastic_imu_server.py` (lines 44, 197–199, 224, 317, 422–435, 493–519)
- Create: `tests/test_imu_server.py`

**Interfaces:**
- Produces: `pendulastic_imu_server.swing_angle_deg() -> float` — returns NaN before `zero()`, degrees thereafter
- Produces: `pendulastic_imu_server.get_state()["swing_angle_deg"]` — float (NaN or degrees)
- Produces: `_IMUDevice.get_quaternion() -> np.ndarray` — shape (4,) unit quaternion [w, x, y, z]

- [ ] **Step 1: Write failing tests**

Create `tests/test_imu_server.py`:

```python
# tests/test_imu_server.py
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import pendulastic_imu_server as imu


def test_qconj_negates_vector_part():
    q = np.array([0.9, 0.1, 0.2, 0.3])
    c = imu._qconj(q)
    assert c[0] == 0.9
    assert c[1] == -0.1
    assert c[2] == -0.2
    assert c[3] == -0.3


def test_qmul_identity():
    """q * identity = q."""
    q    = np.array([0.7071, 0.7071, 0.0, 0.0])
    iden = np.array([1.0, 0.0, 0.0, 0.0])
    result = imu._qmul(q, iden)
    np.testing.assert_allclose(result, q, atol=1e-6)


def test_qmul_self_conj_is_near_identity():
    """q * conj(q) should equal [1,0,0,0] for a unit quaternion."""
    q = np.array([0.6, 0.2, -0.7, 0.3])
    q /= np.linalg.norm(q)
    result = imu._qmul(q, imu._qconj(q))
    np.testing.assert_allclose(result, [1., 0., 0., 0.], atol=1e-6)


def test_imudevice_get_quaternion_ahrs_mode():
    """AHRS mode returns ahrs.q directly."""
    dev = imu._IMUDevice("1.2.3.4")
    dev.from_orientation_stream = False
    dev.ahrs.q = np.array([0.9, 0.1, 0.2, 0.3])
    q = dev.get_quaternion()
    np.testing.assert_allclose(q, [0.9, 0.1, 0.2, 0.3])


def test_imudevice_get_quaternion_orientation_stream_mode():
    """Orientation-stream mode converts stored Euler angles to a unit quaternion."""
    dev = imu._IMUDevice("1.2.3.5")
    dev.from_orientation_stream = True
    dev.roll = 0.0
    dev.pitch = 0.0
    dev.yaw = 0.0
    q = dev.get_quaternion()
    # Identity pose → [1,0,0,0]
    np.testing.assert_allclose(q, [1., 0., 0., 0.], atol=1e-6)


def test_imudevice_get_quaternion_orientation_stream_is_unit():
    """Quaternion from Euler angles must be unit length."""
    dev = imu._IMUDevice("1.2.3.6")
    dev.from_orientation_stream = True
    dev.roll = 30.0
    dev.pitch = 45.0
    dev.yaw = 10.0
    q = dev.get_quaternion()
    assert abs(np.linalg.norm(q) - 1.0) < 1e-6


def test_swing_angle_deg_returns_nan_before_zero():
    """swing_angle_deg() must return NaN before zero() is called."""
    imu.reset_devices()
    imu.clear_zero()
    angle = imu.swing_angle_deg()
    assert math.isnan(angle)


def test_get_state_contains_swing_angle_deg_key():
    """get_state() always returns the 'swing_angle_deg' key."""
    st = imu.get_state()
    assert "swing_angle_deg" in st


def test_swing_angle_zero_returns_zero():
    """Immediately after zero(), swing_angle_deg() should return ~0°."""
    imu.reset_devices()
    # Inject a fake proximal device at a known quaternion
    imu._devices["10.0.0.1"] = imu._IMUDevice("10.0.0.1")
    imu._roles["10.0.0.1"] = imu.ROLE_DISTAL
    dev = imu._devices["10.0.0.1"]
    dev.from_orientation_stream = False
    dev.ahrs.q = np.array([1.0, 0.0, 0.0, 0.0])
    dev.last_rx = __import__("time").time()
    # zero() captures this quaternion
    imu.zero()
    # Same pose → 0° swing
    angle = imu.swing_angle_deg()
    assert abs(angle) < 1e-4, f"Expected ~0°, got {angle}"
    imu.reset_devices()
    imu.clear_zero()
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -x -q
```

Expected: multiple failures (`AttributeError: module 'pendulastic_imu_server' has no attribute '_qconj'`, etc.)

- [ ] **Step 3: Add module-level quaternion zero storage**

In `pendulastic_imu_server.py`, after line 317 (`_offset = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}`), add:

```python
_q_zero_prox: Optional[np.ndarray] = None
_q_zero_dist: Optional[np.ndarray] = None
```

- [ ] **Step 4: Add `_qconj` and `_qmul` helpers**

In `pendulastic_imu_server.py`, after the `wrap180` function (after line 199), add:

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

- [ ] **Step 5: Add `_IMUDevice.get_quaternion()` method**

In the `_IMUDevice` class, add this method after the `on_orientation` method (around line 300):

```python
def get_quaternion(self) -> np.ndarray:
    """Return current orientation as a unit quaternion [w, x, y, z].

    AHRS mode: returns the filter's output directly.
    Orientation-stream mode: converts stored ZYX Euler angles to quaternion."""
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

- [ ] **Step 6: Update `zero()` to capture quaternion zeros**

Replace the existing `zero()` function (lines 422–429) with:

```python
def zero():
    """Capture the current pose as the 0° reference.
    Stores both Euler offsets (for relative_angles() backward compat) and
    quaternion snapshots (for swing_angle_deg())."""
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
            solo = next((d for d in (dist, prox)
                         if d is not None and d.connected), None)
            if solo is not None:
                _q_zero_dist = solo.get_quaternion()
```

- [ ] **Step 7: Update `clear_zero()` to reset quaternion zeros**

Replace the existing `clear_zero()` function (lines 432–435) with:

```python
def clear_zero():
    global _q_zero_prox, _q_zero_dist
    with _lock:
        for k in _offset:
            _offset[k] = 0.0
        _q_zero_prox = None
        _q_zero_dist = None
```

- [ ] **Step 8: Add `swing_angle_deg()` function**

After `clear_zero()`, add:

```python
def swing_angle_deg() -> float:
    """Quaternion rotation distance from zeroed reference pose, in degrees.

    Returns NaN before zero() is called.
    Two-phone: relative joint angle change from zeroed pose.
    Single-phone: absolute segment rotation from zeroed pose."""
    with _lock:
        if _q_zero_dist is None:
            return float("nan")

        prox = _by_role(ROLE_PROXIMAL)
        dist = _by_role(ROLE_DISTAL)

        if (prox is not None and dist is not None
                and prox.connected and dist.connected
                and _q_zero_prox is not None):
            q_rel_zero = _qmul(_qconj(_q_zero_prox), _q_zero_dist)
            q_rel_cur  = _qmul(_qconj(prox.get_quaternion()),
                                dist.get_quaternion())
            dot = float(np.dot(q_rel_zero, q_rel_cur))
        else:
            solo = next(
                (d for d in (dist, prox) if d is not None and d.connected),
                None)
            if solo is None:
                return float("nan")
            q_zero = (_q_zero_dist
                      if (dist is not None and dist.connected)
                      else _q_zero_prox)
            if q_zero is None:
                return float("nan")
            dot = float(np.dot(q_zero, solo.get_quaternion()))

        dot = max(-1.0, min(1.0, abs(dot)))
        return 2.0 * math.degrees(math.acos(dot))
```

- [ ] **Step 9: Add `"swing_angle_deg"` to `get_state()`**

In `get_state()` (around line 493), in the returned dict, add one key after `"angles": ang,`:

```python
"swing_angle_deg": swing_angle_deg(),
```

The final returned dict should look like:
```python
return {
    "running":   _running,
    "bind_error": _bind_error,
    "recording": _recording,
    "port":      PORT,
    "proximal":  {...},
    "distal":    {...},
    "angles":    ang,
    "swing_angle_deg": swing_angle_deg(),
    "sync":      sync_status(),
    "conns":     _conn_active,
    "endpoints": {p: dict(v) for p, v in _seen_paths.items()},
    "last_drop": ...,
}
```

- [ ] **Step 10: Run tests**

```
.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -x -q
```

Expected: all 9 tests pass.

- [ ] **Step 11: Run the full suite to check no regressions**

```
.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_metrics.py --ignore=tests/test_pose.py --ignore=tests/test_stats.py --ignore=tests/test_video.py
```

Expected: same pass/fail counts as before this task (no new failures).

- [ ] **Step 12: Commit**

```
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat: add quaternion swing_angle_deg to IMU server"
```

---

### Task 2: `BiomechanicalEngine.get_live_angle()` Quaternion Fix

**Files:**
- Modify: `pendulastic_app.py` (lines 9–21 imports, lines 144–151)
- Modify: `tests/test_app.py`

**Interfaces:**
- Consumes: `_imu.get_state()["swing_angle_deg"]` → `float` (from Task 1)
- Produces: `BiomechanicalEngine.get_live_angle()` → `float` — `180.0 - swing` when finite, `float("nan")` otherwise

**Important:** `import math` is NOT currently at module level in `pendulastic_app.py`. It must be added.

- [ ] **Step 1: Update `test_get_live_angle_maps_to_180_convention` to use new key**

In `tests/test_app.py`, replace the existing `test_get_live_angle_maps_to_180_convention` function (lines 62–85) with:

```python
def test_get_live_angle_maps_to_180_convention(monkeypatch):
    """swing_angle_deg=0 (no rotation from zero) must read 180° (full extension)."""
    import pendulastic_app as _m, types, math
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "swing_angle_deg": 0.0,
            "angles": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    from pendulastic_app import BiomechanicalEngine
    engine = BiomechanicalEngine("imu")
    assert engine.get_live_angle() == 180.0, "No swing from zero must map to 180°"

    # 90° of swing from zero → 90° clinical angle
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "swing_angle_deg": 90.0,
            "angles": {"pitch": 90.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    assert engine.get_live_angle() == 90.0, "90° swing must map to 90° clinical angle"


def test_get_live_angle_returns_nan_before_zero(monkeypatch):
    """Before zero() is called, swing_angle_deg is NaN → get_live_angle returns NaN."""
    import pendulastic_app as _m, types, math
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "swing_angle_deg": float("nan"),
            "angles": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": False},
            "distal":   {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    from pendulastic_app import BiomechanicalEngine
    engine = BiomechanicalEngine("imu")
    result = engine.get_live_angle()
    assert math.isnan(result), f"Expected NaN before zero, got {result}"
```

- [ ] **Step 2: Run tests to verify the updated test fails**

```
.venv\Scripts\python.exe -m pytest tests/test_app.py::test_get_live_angle_maps_to_180_convention tests/test_app.py::test_get_live_angle_returns_nan_before_zero -x -q
```

Expected: both fail (implementation still reads `angles.pitch`).

- [ ] **Step 3: Add `import math` to module-level imports in `pendulastic_app.py`**

In `pendulastic_app.py`, in the stdlib imports block (lines 9–16), add `import math`:

```python
import csv
import math
import os
import queue
import threading
import time
from typing import Callable, Optional
```

- [ ] **Step 4: Replace `get_live_angle()` implementation**

Replace lines 144–151 of `pendulastic_app.py`:

```python
    def get_live_angle(self) -> float:
        """Return current knee angle (degrees) or NaN if unavailable."""
        if self.methodology != "imu" or not _IMU_AVAIL:
            return float("nan")
        try:
            return 180.0 - float(_imu.get_state()["angles"]["pitch"])
        except Exception:
            return float("nan")
```

With:

```python
    def get_live_angle(self) -> float:
        """Return current knee angle (degrees) or NaN if unavailable."""
        if self.methodology != "imu" or not _IMU_AVAIL:
            return float("nan")
        try:
            swing = _imu.get_state().get("swing_angle_deg", float("nan"))
            if math.isfinite(swing):
                return 180.0 - swing
            return float("nan")
        except Exception:
            return float("nan")
```

- [ ] **Step 5: Run the two updated tests**

```
.venv\Scripts\python.exe -m pytest tests/test_app.py::test_get_live_angle_maps_to_180_convention tests/test_app.py::test_get_live_angle_returns_nan_before_zero -x -q
```

Expected: both pass.

- [ ] **Step 6: Run the full suite**

```
.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_metrics.py --ignore=tests/test_pose.py --ignore=tests/test_stats.py --ignore=tests/test_video.py
```

Expected: no new failures vs Task 1 baseline.

- [ ] **Step 7: Commit**

```
git add pendulastic_app.py tests/test_app.py
git commit -m "fix: replace Euler pitch with quaternion swing_angle_deg in get_live_angle"
```

---

### Task 3: Mode Select Landing Screen + Upload Flow

**Files:**
- Modify: `pendulastic_app.py` (many sections — listed below)
- Modify: `tests/test_app.py` (update 1 broken test, add mode-select tests)

**Interfaces:**
- Consumes (from Task 1): `_imu.get_state()["swing_angle_deg"]` — already wired by Task 2
- Produces: `ModeSelectView(tk.Frame)` — landing screen
- Produces: `UploadMetaView(tk.Frame)` — compact metadata form; exposes `status_var: tk.StringVar`, `set_file(path)`, `get_metadata() -> dict`, `set_processing(active: bool)`
- Produces: `App._mode_select`, `App._upload_meta` — new panel instances
- Produces: `App._enter_live_mode()`, `App._enter_upload_mode()`, `App._upload_back_to_select()`, `App.on_back_to_mode_select()`, `App._start_upload_analysis()`, `App._run_csv_analysis()`

**`on_new_trial()` behaviour:** After this task, `on_new_trial()` increments the trial, hides mode_select (safety), and returns to `AcquisitionPanel` directly (fast repeat-trial path). The existing test for this behaviour continues to pass.

**Test that will break:** `test_app_starts_with_acquisition_visible` — must be replaced by `test_app_starts_with_mode_select_visible` because `App.__init__` now shows `_mode_select`.

#### 3-A. Add `ModeSelectView` class

Insert the following class in `pendulastic_app.py` **between** the `AcquisitionPanel` class and the `PostProcessingPanel` class (after the last line of `AcquisitionPanel`, before the `# PostProcessingPanel` comment block):

```python
# ---------------------------------------------------------------------------
# ModeSelectView
# ---------------------------------------------------------------------------

class ModeSelectView(tk.Frame):
    """Startup landing screen — routes to live recording or file upload."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        self._build_widgets()

    def _build_widgets(self) -> None:
        tk.Label(self, text="Pendulastic",
                 font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(60, 4))
        tk.Label(self, text="Clinical Pendulum Test Platform",
                 font=("Segoe UI", 11), fg="#555").grid(
            row=1, column=0, columnspan=2, pady=(0, 40))

        tk.Button(
            self,
            text="Live Recording Session\nIMU · RGB · OptiTrack",
            font=("Segoe UI", 12, "bold"),
            bg=_GREEN, fg="white",
            width=24, height=4,
            command=self.controller._enter_live_mode,
        ).grid(row=2, column=0, padx=40, pady=16, sticky="n")

        tk.Button(
            self,
            text="Upload & Analyze\nVideo or CSV file",
            font=("Segoe UI", 12, "bold"),
            bg=_BLUE, fg="white",
            width=24, height=4,
            command=self.controller._enter_upload_mode,
        ).grid(row=2, column=1, padx=40, pady=16, sticky="n")
```

#### 3-B. Add `UploadMetaView` class

Insert immediately after `ModeSelectView`, before `PostProcessingPanel`:

```python
# ---------------------------------------------------------------------------
# UploadMetaView
# ---------------------------------------------------------------------------

class UploadMetaView(tk.Frame):
    """Compact metadata form for file-first upload analysis."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller  = controller
        self._file_path  = ""
        self.status_var  = tk.StringVar(value="Ready")
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        # Header: back button + title
        hdr = tk.Frame(self)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew",
                 padx=12, pady=(16, 4))
        self.btn_back = tk.Button(hdr, text="<- Back",
                                  font=("Segoe UI", 10),
                                  command=self.controller._upload_back_to_select)
        self.btn_back.pack(side="left", padx=(0, 12))
        tk.Label(hdr, text="Upload & Analyze",
                 font=("Segoe UI", 13, "bold")).pack(side="left")

        # Selected file name
        self._file_label_var = tk.StringVar(value="No file selected")
        tk.Label(self, textvariable=self._file_label_var,
                 font=("Consolas", 9), fg="gray", anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

        # Participant ID
        tk.Label(self, text="Participant ID:").grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        tk.Entry(self, textvariable=self.pid_var, width=22).grid(
            row=2, column=1, sticky="w", **pad)

        # Leg
        tk.Label(self, text="Leg:").grid(row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self)
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var,
                       value="Left").pack(side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var,
                       value="Right").pack(side="left", padx=4)

        # MS Status
        tk.Label(self, text="MS Status:").grid(row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ttk.Combobox(self, textvariable=self.ms_var, width=22, state="readonly",
                     values=["MS", "Stroke", "Control", "Other"]).grid(
            row=4, column=1, sticky="w", **pad)

        # Trial number
        tk.Label(self, text="Trial Number:").grid(row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6).grid(
            row=5, column=1, sticky="w", **pad)

        # Analyze button
        self.btn_analyze = tk.Button(
            self, text="Analyze ->",
            bg=_BLUE, fg="white", font=("Segoe UI", 11, "bold"),
            width=16, height=2,
            command=self.controller._start_upload_analysis)
        self.btn_analyze.grid(row=6, column=0, columnspan=2, pady=20)

        # Status bar
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w", fg="#333").grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_file(self, path: str) -> None:
        self._file_path = path
        self._file_label_var.set(f"File: {os.path.basename(path)}")

    def get_metadata(self) -> dict:
        return {
            "pid":        self.pid_var.get().strip(),
            "leg":        self.leg_var.get(),
            "ms_status":  self.ms_var.get(),
            "trial":      int(self.trial_var.get()),
            "sources":    ["upload_csv"
                           if self._file_path.lower().endswith(".csv")
                           else "video_file"],
        }

    def set_processing(self, active: bool) -> None:
        state = "disabled" if active else "normal"
        self.btn_back.config(state=state)
        self.btn_analyze.config(state=state)
```

#### 3-C. Add `"upload_csv"` to `PostProcessingPanel`

In `PostProcessingPanel`, replace the two class-level attributes:

```python
_CURVE_STYLES = {
    "imu":        {"color": "#2563EB", "ls": "-",   "label": "IMU"},
    "rgb":        {"color": "#16A34A", "ls": "-",   "label": "RGB"},
    "optitrack":  {"color": "#D97706", "ls": "--",  "label": "OptiTrack"},
    "hpe_upload": {"color": "#7C3AED", "ls": "--",  "label": "HPE Upload"},
    "video_file": {"color": "#7C3AED", "ls": "--",  "label": "Video File (HPE)"},
}
_PT_SOURCE_PRIORITY = ["imu", "rgb", "optitrack", "hpe_upload", "video_file"]
```

With:

```python
_CURVE_STYLES = {
    "imu":        {"color": "#2563EB", "ls": "-",   "label": "IMU"},
    "rgb":        {"color": "#16A34A", "ls": "-",   "label": "RGB"},
    "optitrack":  {"color": "#D97706", "ls": "--",  "label": "OptiTrack"},
    "hpe_upload": {"color": "#7C3AED", "ls": "--",  "label": "HPE Upload"},
    "video_file": {"color": "#7C3AED", "ls": "--",  "label": "Video File (HPE)"},
    "upload_csv": {"color": "#0891B2", "ls": "--",  "label": "CSV Upload"},
}
_PT_SOURCE_PRIORITY = ["imu", "rgb", "optitrack", "hpe_upload", "video_file", "upload_csv"]
```

#### 3-D. Add `"← Mode Select"` button to `PostProcessingPanel`

In `PostProcessingPanel._build_widgets()`, replace the `# row 0 — title` block:

```python
        # row 0 — title (trial filename)
        self.title_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="ew", padx=12, pady=(12, 4))
```

With:

```python
        # row 0 — header: mode-select back button + trial filename
        hdr0 = tk.Frame(self)
        hdr0.grid(row=0, column=0, columnspan=3, sticky="ew",
                  padx=12, pady=(12, 4))
        tk.Button(hdr0, text="<- Mode Select",
                  font=("Segoe UI", 9),
                  command=controller.on_back_to_mode_select).pack(
            side="left", padx=(0, 12))
        self.title_var = tk.StringVar(value="")
        tk.Label(hdr0, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")
```

#### 3-E. Add `"← Mode Select"` button to `AcquisitionPanel`

In `AcquisitionPanel._build_widgets()`, replace the `# row 0 — title` block:

```python
        # row 0 — title
        tk.Label(self, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(16, 4))
```

With:

```python
        # row 0 — header: mode-select back button + title
        hdr0 = tk.Frame(self)
        hdr0.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(16, 4))
        self.btn_back = tk.Button(hdr0, text="<- Mode Select",
                                  font=("Segoe UI", 9),
                                  command=self.controller.on_back_to_mode_select)
        self.btn_back.pack(side="left", padx=(0, 8))
        tk.Label(hdr0, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
```

Then in the `_lockable` list (near end of `_build_widgets`), add `self.btn_back`:

```python
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            self.btn_zero, self.btn_clear_zero, self.btn_back,
        ]
```

#### 3-F. Update `App.__init__`

Replace the current `App.__init__` panel-creation block:

```python
        self._state           = "idle"
        ...
        self._acq  = AcquisitionPanel(self, controller=self)
        self._post = PostProcessingPanel(self, controller=self)
        self._acq.pack(fill="both", expand=True)
```

With:

```python
        self._state           = "mode_select"
        ...
        self._mode_select = ModeSelectView(self, controller=self)
        self._upload_meta = UploadMetaView(self, controller=self)
        self._acq  = AcquisitionPanel(self, controller=self)
        self._post = PostProcessingPanel(self, controller=self)
        self._mode_select.pack(fill="both", expand=True)
```

The exact lines to change in `App.__init__` (currently lines 935 and 955–957):
- Line 935: `self._state = "idle"` → `self._state = "mode_select"`
- After `self._post = PostProcessingPanel(...)`: add `self._mode_select = ModeSelectView(...)` and `self._upload_meta = UploadMetaView(...)` before it
- Replace `self._acq.pack(fill="both", expand=True)` with `self._mode_select.pack(fill="both", expand=True)`

#### 3-G. Update `on_new_trial()` — remove window-shrink, add mode_select safety hide

Replace the current `on_new_trial()`:

```python
    def on_new_trial(self) -> None:
        self._acq.increment_trial()
        self._post.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._acq.enter_idle()
        self.geometry("500x740")
        self._state = "idle"
```

With:

```python
    def on_new_trial(self) -> None:
        self._acq.increment_trial()
        self._post.pack_forget()
        self._mode_select.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._acq.enter_idle()
        self._state = "idle"
```

#### 3-H. Update `_transition_to_review()` — hide `_upload_meta`

Replace the current `_transition_to_review()`:

```python
    def _transition_to_review(self, source_angles: dict, meta: dict) -> None:
        self._state = "review"
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        self._post.load_trial(source_angles, self._fps_for(meta), meta, base_fn)
        self._acq.pack_forget()
        self._post.pack(fill="both", expand=True)
        try:
            self.state("zoomed")
        except Exception:
            pass
```

With:

```python
    def _transition_to_review(self, source_angles: dict, meta: dict) -> None:
        self._state = "review"
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        self._post.load_trial(source_angles, self._fps_for(meta), meta, base_fn)
        self._acq.pack_forget()
        self._upload_meta.pack_forget()
        self._post.pack(fill="both", expand=True)
        try:
            self.state("zoomed")
        except Exception:
            pass
```

#### 3-I. Add new `App` methods

Add the following methods to the `App` class (after `on_source_changed` and before `_start_imu_recording`):

```python
    # ------------------------------------------------------------------
    # Mode-select routing
    # ------------------------------------------------------------------
    def _enter_live_mode(self) -> None:
        self._mode_select.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._state = "idle"

    def _enter_upload_mode(self) -> None:
        path = filedialog.askopenfilename(
            title="Select file to analyze",
            filetypes=[
                ("Video / CSV", "*.mp4 *.avi *.mov *.mkv *.csv"),
                ("All files", "*.*"),
            ])
        if not path:
            return
        self._mode_select.pack_forget()
        self._upload_meta.set_file(path)
        self._upload_meta.status_var.set("Ready")
        self._upload_meta.set_processing(False)
        self._upload_meta.pack(fill="both", expand=True)
        self._state = "upload_meta"

    def _upload_back_to_select(self) -> None:
        if self._state == "upload_processing":
            return
        self._upload_meta.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state = "mode_select"

    def on_back_to_mode_select(self) -> None:
        self._acq.pack_forget()
        self._post.pack_forget()
        self._upload_meta.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state        = "mode_select"
        self._active_sources  = []
        self._rec_angles      = {}
        self._rec_timestamps  = {}
        self._pending_review  = {}

    def _start_upload_analysis(self) -> None:
        meta = self._upload_meta.get_metadata()
        if not meta.get("pid", "").strip():
            messagebox.showerror("Metadata", "Participant ID cannot be empty.")
            return
        path = self._upload_meta._file_path
        if not path:
            messagebox.showerror("Metadata", "No file selected.")
            return
        self._state = "upload_processing"
        self._upload_meta.set_processing(True)
        self._upload_meta.status_var.set("Processing…")
        ext = os.path.splitext(path)[1].lower()
        if ext in (".mp4", ".avi", ".mov", ".mkv"):
            threading.Thread(
                target=self._run_video_file_hpe,
                args=(path, meta),
                kwargs={"progress_target": self._upload_meta.status_var},
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._run_csv_analysis,
                args=(path, meta),
                daemon=True,
            ).start()

    def _run_csv_analysis(self, path: str, meta: dict) -> None:
        import csv as _csv_mod
        target = self._upload_meta.status_var
        t_vals: list = []
        angle_vals: list = []
        try:
            with open(path, newline="", encoding="utf-8") as f:
                lines = (row for row in f if not row.startswith("#"))
                reader = _csv_mod.DictReader(lines)
                for row in reader:
                    try:
                        t_key = next(
                            (k for k in ("time_s", "t_rel") if k in row), None)
                        a_key = next(
                            (k for k in ("knee_angle_deg", "angle") if k in row),
                            None)
                        if t_key is None or a_key is None:
                            continue
                        t_vals.append(float(row[t_key]))
                        angle_vals.append(float(row[a_key]))
                    except (KeyError, ValueError):
                        pass
        except OSError as e:
            self.after(0, lambda: target.set(f"Error reading file: {e}"))
            return
        if not angle_vals:
            self.after(0, lambda: target.set(
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

#### 3-J. Refactor `_run_video_file_hpe()` progress callback

Replace the current `_run_video_file_hpe` method:

```python
    def _run_video_file_hpe(self, path: str, meta: dict) -> None:
        def progress(pct: float) -> None:
            self.after(0, lambda p=pct: self._acq.status_var.set(
                f"HPE processing: {int(p * 100)}%"))
        ...
```

With:

```python
    def _run_video_file_hpe(self, path: str, meta: dict,
                             progress_target: Optional[tk.StringVar] = None) -> None:
        target = progress_target or self._acq.status_var
        def progress(pct: float) -> None:
            self.after(0, lambda p=pct: target.set(
                f"HPE processing: {int(p * 100)}%"))
        ...
```

Only the signature and `target` lines change; the body (leg, engine, run_offline_track, save_trial, after) stays the same.

#### 3-K. Write tests

In `tests/test_app.py`:

1. **Replace** `test_app_starts_with_acquisition_visible` with:

```python
def test_app_starts_with_mode_select_visible():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        assert app._mode_select.winfo_ismapped(), "ModeSelectView must be visible on startup"
        assert not app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()
```

2. **Add** new tests after the existing tests:

```python
def test_enter_live_mode_shows_acquisition():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_live_mode()
        app.update()
        assert app._acq.winfo_ismapped()
        assert not app._mode_select.winfo_ismapped()
        assert app._state == "idle"
    finally:
        app.destroy()


def test_upload_back_to_select_restores_mode_select(monkeypatch):
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        # Simulate being in upload_meta state
        app._mode_select.pack_forget()
        app._upload_meta.pack(fill="both", expand=True)
        app._state = "upload_meta"
        app.update()
        app._upload_back_to_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._upload_meta.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()


def test_upload_back_to_select_blocked_during_processing():
    from pendulastic_app import App
    app = App()
    try:
        app._state = "upload_processing"
        app._upload_back_to_select()
        # Should not change state
        assert app._state == "upload_processing"
    finally:
        app.destroy()


def test_on_back_to_mode_select_resets_state():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_live_mode()
        app.update()
        app._rec_angles    = {"imu": [1.0, 2.0]}
        app._active_sources = ["imu"]
        app.on_back_to_mode_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._acq.winfo_ismapped()
        assert app._state == "mode_select"
        assert app._rec_angles == {}
        assert app._active_sources == []
    finally:
        app.destroy()


def test_upload_csv_curve_style_exists():
    from pendulastic_app import PostProcessingPanel
    assert "upload_csv" in PostProcessingPanel._CURVE_STYLES
    assert "upload_csv" in PostProcessingPanel._PT_SOURCE_PRIORITY
    style = PostProcessingPanel._CURVE_STYLES["upload_csv"]
    assert style["color"] == "#0891B2"
    assert style["label"] == "CSV Upload"


def test_run_csv_analysis_reads_datamanager_format(tmp_path, monkeypatch):
    """_run_csv_analysis must parse time_s + knee_angle_deg columns."""
    import csv as _csv_mod, os
    from pendulastic_app import App, DataManager
    # Write a minimal DataManager-format CSV
    p = tmp_path / "test_trial.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = _csv_mod.writer(f)
        w.writerow(["frame", "time_s", "knee_angle_deg",
                    "pid", "leg", "ms_status", "trial", "methodology"])
        w.writerow([0, "0.0000", "170.000", "P1", "Right", "MS", "1", "upload_csv"])
        w.writerow([1, "0.0333", "165.000", "P1", "Right", "MS", "1", "upload_csv"])
        w.writerow([2, "0.0667", "160.000", "P1", "Right", "MS", "1", "upload_csv"])

    captured = {}

    app = App()
    try:
        # Patch _transition_to_review to capture what was passed
        def fake_transition(source_angles, meta):
            captured["source_angles"] = source_angles
            captured["meta"] = meta
        monkeypatch.setattr(app, "_transition_to_review", fake_transition)
        # Patch DataManager.save_trial to avoid writing files during test
        monkeypatch.setattr(DataManager, "save_trial",
                            classmethod(lambda cls, *a, **kw: ""))

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._run_csv_analysis(str(p), meta)
        app.update()  # process the after(0, ...) callback
    finally:
        app.destroy()

    assert "upload_csv" in captured.get("source_angles", {})
    angles = captured["source_angles"]["upload_csv"]
    assert len(angles) == 3
    assert abs(angles[0] - 170.0) < 0.01
```

- [ ] **Step 1: Run failing tests first**

```
.venv\Scripts\python.exe -m pytest tests/test_app.py::test_app_starts_with_mode_select_visible -x -q
```

Expected: `AttributeError: 'App' object has no attribute '_mode_select'`

- [ ] **Step 2: Apply all changes 3-A through 3-J**

Apply in this order to avoid forward-reference issues:
1. 3-A: `ModeSelectView` class
2. 3-B: `UploadMetaView` class
3. 3-C: `PostProcessingPanel._CURVE_STYLES` + `_PT_SOURCE_PRIORITY`
4. 3-D: `PostProcessingPanel` Back button in `_build_widgets()`
5. 3-E: `AcquisitionPanel` Back button + `_lockable` update
6. 3-F: `App.__init__` panel creation
7. 3-G: `App.on_new_trial()`
8. 3-H: `App._transition_to_review()`
9. 3-I: New `App` methods
10. 3-J: `_run_video_file_hpe()` signature

- [ ] **Step 3: Apply test changes (3-K)**

In `tests/test_app.py`:
- Replace `test_app_starts_with_acquisition_visible` with `test_app_starts_with_mode_select_visible`
- Add the 6 new tests

- [ ] **Step 4: Run the new tests**

```
.venv\Scripts\python.exe -m pytest tests/test_app.py -x -q -k "mode_select or upload or csv_curve"
```

Expected: all new tests pass.

- [ ] **Step 5: Run the full relevant suite**

```
.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_metrics.py --ignore=tests/test_pose.py --ignore=tests/test_stats.py --ignore=tests/test_video.py
```

Expected: all tests pass that were passing after Task 2. The renamed `test_app_starts_with_mode_select_visible` replaces the old test; `test_on_new_trial_increments_trial_and_returns_to_acquisition` still passes because `on_new_trial()` still goes to `_acq`.

- [ ] **Step 6: Commit**

```
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add ModeSelectView, UploadMetaView, and CSV upload analysis"
```
