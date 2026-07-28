# Unified Pendulastic App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `pendulastic_app.py` — a single Tkinter desktop app that replaces running `master_app.py` and `pendulastic_viewer.py` as separate tools, combining acquisition, live telemetry, and post-processing into one seamless panel-switched workflow.

**Architecture:** A thin `App(tk.Tk)` host owns shared state (IMU server port, camera, metadata) and switches between two full-screen panels: `AcquisitionPanel` (record) and `PostProcessingPanel` (analyze). Five top-level classes are implemented in dependency order: `DataManager` → `BiomechanicalEngine` → `AcquisitionPanel` → `PostProcessingPanel` → `App`. No existing files are modified.

**Tech Stack:** Python 3.10+, Tkinter, OpenCV (`cv2`), `_MPBatchTracker` + `_PatientDetector` from `pendulastic_viewer.py`, `compute_pt_params` / `compute_pt_score_simple` / `pt_to_mas` from `pendulastic_pt_score.py`, Matplotlib (`FigureCanvasTkAgg`), `pendulastic_imu_server`, `motive_sync`.

## Global Constraints

- New file: `pendulastic_app.py` in project root `C:\Users\cladi\Pendulastic\`
- Do NOT modify: `pendulastic_viewer.py`, `pendulastic_imu_server.py`, `pendulastic_pt_score.py`, `motive_sync.py`
- All trial CSVs written to flat `data/` folder — no subfolder hierarchy
- Filename format: `PID_{pid}_LEG_{leg}_{ms_underscored}_TRIAL_{trial}.csv`
- IMU knee angle: `θk = shank.pitch` only — no proximal subtraction (reads `imu_server.get_state()["distal"]["pitch"]`)
- Port 5000 (IMU WebSocket): owned by `App`, started once at `__init__`, released at `on_close()`
- Port 8888 (UDP goniometer): started when IMU methodology selected, stopped on switch or close
- START button stays at `(row=12, col=0)` always — mutates text/command/bg in-place via `.config()`
- Python environment: `.venv` in project root
- Correct tracker API (verified from source): `.init(frame, hip, knee, ankle)` → `.step(frame) → (hip, knee, ankle, angle_deg)`
- Correct PT params API: `compute_pt_params(t: np.ndarray, angle_raw: np.ndarray) → dict | None` — keys include `A1_deg`, `omega_peak_deg_s`, `N`, `f`, `R2n`
- `load_optitrack` is in `pendulastic_pt_score`, NOT `pendulastic_viewer`

---

### Task 1: DataManager — filename builder and CSV writer

**Files:**
- Create: `pendulastic_app.py` (stub header + `DataManager` only)
- Create: `tests/test_data_manager.py`

**Interfaces:**
- Produces:
  - `DataManager.DATA_DIR: str` — `<project_root>/data/`
  - `DataManager.build_filename(pid, leg, ms_status, trial) -> str`
  - `DataManager.save_trial(filename, angles, metadata, timestamps=None, fps=30.0) -> str`

- [ ] **Step 1: Create `tests/test_data_manager.py` with failing tests**

```python
# tests/test_data_manager.py
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pendulastic_app import DataManager


def test_build_filename_basic():
    assert DataManager.build_filename("P1", "right", "MS", 1) == \
        "PID_P1_LEG_Right_MS_TRIAL_1.csv"


def test_build_filename_spaces_become_underscores():
    assert DataManager.build_filename("P2", "left", "Unaffected Control", 3) == \
        "PID_P2_LEG_Left_Unaffected_Control_TRIAL_3.csv"


def test_build_filename_leg_capitalised():
    assert DataManager.build_filename("P3", "LEFT", "Stroke", 2) == \
        "PID_P3_LEG_Left_Stroke_TRIAL_2.csv"


def test_save_trial_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "imu"}
    path = DataManager.save_trial("test.csv", [170.0, 165.0], meta, fps=30.0)
    assert os.path.isfile(path)


def test_save_trial_csv_headers(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "rgb"}
    DataManager.save_trial("test.csv", [170.0, 165.0], meta, fps=30.0)
    with open(tmp_path / "test.csv") as f:
        header = next(csv.reader(f))
    assert header == ["frame", "time_s", "knee_angle_deg",
                      "pid", "leg", "ms_status", "trial", "methodology"]


def test_save_trial_fps_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "rgb"}
    DataManager.save_trial("test.csv", [170.0, 165.0], meta, fps=10.0)
    with open(tmp_path / "test.csv") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "0.0000"   # frame 0: 0/10
    assert rows[2][1] == "0.1000"   # frame 1: 1/10


def test_save_trial_explicit_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "methodology": "imu"}
    DataManager.save_trial("test.csv", [170.0, 165.0], meta,
                           timestamps=[1000.0, 1000.5])
    with open(tmp_path / "test.csv") as f:
        rows = list(csv.reader(f))
    assert rows[1][1] == "0.0000"   # t[0] - t[0] = 0
    assert rows[2][1] == "0.5000"   # t[1] - t[0] = 0.5
```

- [ ] **Step 2: Run to confirm failure**

```
.venv\Scripts\pytest tests\test_data_manager.py -v
```
Expected: `ModuleNotFoundError: No module named 'pendulastic_app'`

- [ ] **Step 3: Create `pendulastic_app.py` with stub header and `DataManager`**

```python
"""
pendulastic_app.py  —  Unified Pendulastic Desktop App
=======================================================
Single-window Tkinter app combining acquisition and post-processing.

Run:
    .venv\\Scripts\\python.exe pendulastic_app.py
"""
from __future__ import annotations

import csv
import os
import queue
import threading
import time
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Guarded imports — failures must not crash the app at startup
# ---------------------------------------------------------------------------
try:
    import pendulastic_imu_server as _imu
    _IMU_AVAIL = True
except Exception:
    _imu = None
    _IMU_AVAIL = False

try:
    import motive_sync as _motive
    _MOTIVE_AVAIL = True
except Exception:
    _motive = None
    _MOTIVE_AVAIL = False

try:
    import cv2 as _cv2
    _CV2_AVAIL = True
except ImportError:
    _cv2 = None
    _CV2_AVAIL = False

try:
    from pendulastic_viewer import _MPBatchTracker, _PatientDetector
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _VIEWER_AVAIL = False

try:
    from pendulastic_pt_score import (
        compute_pt_params, compute_pt_score_simple, pt_to_mas,
        HEALTHY_REF, load_optitrack,
    )
    _PT_AVAIL = True
except Exception:
    compute_pt_params = compute_pt_score_simple = pt_to_mas = None
    HEALTHY_REF = load_optitrack = None
    _PT_AVAIL = False

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    _MPL_AVAIL = True
except Exception:
    FigureCanvasTkAgg = Figure = None
    _MPL_AVAIL = False

_GREEN = "#1e7d34"
_RED   = "#a31515"
_BLUE  = "#1f3a93"
_AMBER = "#c07000"

# ---------------------------------------------------------------------------
# DataManager
# ---------------------------------------------------------------------------

class DataManager:
    DATA_DIR = os.path.join(BASE_DIR, "data")

    @staticmethod
    def build_filename(pid: str, leg: str, ms_status: str, trial: int) -> str:
        leg_s = leg.capitalize()
        ms_s  = ms_status.replace(" ", "_")
        return f"PID_{pid}_LEG_{leg_s}_{ms_s}_TRIAL_{trial}.csv"

    @classmethod
    def save_trial(
        cls,
        filename: str,
        angles: list,
        metadata: dict,
        timestamps: list | None = None,
        fps: float = 30.0,
    ) -> str:
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        path = os.path.join(cls.DATA_DIR, filename)
        t0 = timestamps[0] if timestamps else 0.0
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["frame", "time_s", "knee_angle_deg",
                        "pid", "leg", "ms_status", "trial", "methodology"])
            for i, a in enumerate(angles):
                t = (timestamps[i] - t0) if timestamps else i / fps
                w.writerow([i, f"{t:.4f}", f"{a:.3f}",
                            metadata["pid"], metadata["leg"],
                            metadata["ms_status"], metadata["trial"],
                            metadata["methodology"]])
        return path
```

- [ ] **Step 4: Run tests to confirm pass**

```
.venv\Scripts\pytest tests\test_data_manager.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```
git add pendulastic_app.py tests/test_data_manager.py
git commit -m "feat: DataManager — filename builder and CSV writer"
```

---

### Task 2: BiomechanicalEngine — skeleton and IMU path

**Files:**
- Modify: `pendulastic_app.py` — append `BiomechanicalEngine` class after `DataManager`
- Create: `tests/test_biomechanical_engine.py`

**Interfaces:**
- Consumes: `_IMU_AVAIL`, `_imu.get_state()` returning `{"distal": {"pitch": float, ...}, "proximal": {...}}`
- Produces:
  - `BiomechanicalEngine(methodology: str)` — `"imu"` | `"rgb"` | `"optitrack"`
  - `BiomechanicalEngine.get_live_angle() -> float` — NaN if methodology is not IMU or server unavailable
  - `BiomechanicalEngine.run_offline_track(video_path, progress_cb) -> list[float]` — stub, implemented in Task 3

- [ ] **Step 1: Write failing tests**

```python
# tests/test_biomechanical_engine.py
import math, os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pendulastic_app as _app
from pendulastic_app import BiomechanicalEngine


def _make_fake_imu(pitch: float):
    m = types.SimpleNamespace()
    m.get_state = lambda: {
        "distal":   {"pitch": pitch, "roll": 0.0, "yaw": 0.0},
        "proximal": {"pitch": 10.0,  "roll": 0.0, "yaw": 0.0},
    }
    return m


def test_imu_returns_distal_pitch(monkeypatch):
    monkeypatch.setattr(_app, "_IMU_AVAIL", True)
    monkeypatch.setattr(_app, "_imu", _make_fake_imu(42.7))
    assert BiomechanicalEngine("imu").get_live_angle() == 42.7


def test_imu_no_proximal_subtraction(monkeypatch):
    """Shank-only: distal pitch 42.7 is returned regardless of proximal 10.0."""
    monkeypatch.setattr(_app, "_IMU_AVAIL", True)
    monkeypatch.setattr(_app, "_imu", _make_fake_imu(42.7))
    angle = BiomechanicalEngine("imu").get_live_angle()
    assert angle == 42.7          # NOT 42.7 - 10.0


def test_imu_unavailable_returns_nan(monkeypatch):
    monkeypatch.setattr(_app, "_IMU_AVAIL", False)
    assert math.isnan(BiomechanicalEngine("imu").get_live_angle())


def test_optitrack_returns_nan():
    assert math.isnan(BiomechanicalEngine("optitrack").get_live_angle())


def test_rgb_returns_nan():
    assert math.isnan(BiomechanicalEngine("rgb").get_live_angle())


def test_methodology_stored():
    assert BiomechanicalEngine("rgb").methodology == "rgb"
```

- [ ] **Step 2: Run to confirm failure**

```
.venv\Scripts\pytest tests\test_biomechanical_engine.py -v
```
Expected: `ImportError` — `BiomechanicalEngine` not defined.

- [ ] **Step 3: Append `BiomechanicalEngine` to `pendulastic_app.py`**

Add this class after `DataManager`:

```python
# ---------------------------------------------------------------------------
# BiomechanicalEngine
# ---------------------------------------------------------------------------

class BiomechanicalEngine:
    """Angle pipeline — three code paths dispatched by methodology string."""

    def __init__(self, methodology: str) -> None:
        self.methodology = methodology  # "imu" | "rgb" | "optitrack"

    def get_live_angle(self) -> float:
        """Return current knee angle (degrees) or NaN if unavailable."""
        if self.methodology != "imu" or not _IMU_AVAIL:
            return float("nan")
        try:
            return float(_imu.get_state()["distal"]["pitch"])
        except Exception:
            return float("nan")

    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
    ) -> list:
        """
        Run offline MediaPipe tracking on a recorded video file.
        Implemented in Task 3. Returns list of float angles (one per frame).
        """
        raise NotImplementedError("Implemented in Task 3")
```

- [ ] **Step 4: Run tests to confirm pass**

```
.venv\Scripts\pytest tests\test_biomechanical_engine.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```
git add pendulastic_app.py tests/test_biomechanical_engine.py
git commit -m "feat: BiomechanicalEngine — skeleton and IMU shank-only angle"
```

---

### Task 3: BiomechanicalEngine — RGB offline track

**Files:**
- Modify: `pendulastic_app.py` — replace `run_offline_track` stub
- Modify: `tests/test_biomechanical_engine.py` — add offline-track test

**Interfaces:**
- Consumes (verified from `pendulastic_viewer.py`):
  - `_PatientDetector().detect(frame) -> (patient_kps, assessor_kps)` where each is a `(17, 2)` COCO numpy array or `None`. COCO indices: 11=left_hip, 12=right_hip, 13=left_knee, 14=right_knee, 15=left_ankle, 16=right_ankle.
  - `_MPBatchTracker(side: str, fps: float)` — `side` is `"left"` or `"right"`
  - `tracker.init(frame_bgr, hip, knee, ankle)` — numpy (x, y) pixel coords
  - `tracker.step(frame_bgr) -> (hip, knee, ankle, angle_deg: float)`
- Produces: `BiomechanicalEngine.run_offline_track(video_path, progress_cb, leg="right") -> list[float]`

- [ ] **Step 1: Add offline-track test**

Append to `tests/test_biomechanical_engine.py`:

```python
import pytest
try:
    import cv2 as _cv2_test
    _CV2_OK = True
except ImportError:
    _CV2_OK = False


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_returns_angle_per_frame(tmp_path, monkeypatch):
    """run_offline_track returns one float per video frame via mocked tracker."""
    import numpy as np, types

    # Write a tiny 5-frame video to disk
    video_path = str(tmp_path / "test.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(5):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    # Fake patient detector returns a 17×2 COCO keypoints array
    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]    # right hip
    kps[14] = [160, 120]   # right knee
    kps[16] = [160, 200]   # right ankle

    class FakeDetector:
        def detect(self, frame):
            return kps, None  # (patient_kps, assessor_kps)

    class FakeTracker:
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0  # hip, knee, ankle, angle

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    progress = []
    angles = engine.run_offline_track(video_path,
                                      lambda p: progress.append(p),
                                      leg="right")

    assert len(angles) == 5
    assert all(a == 160.0 for a in angles)
    assert progress and progress[-1] == 1.0
```

- [ ] **Step 2: Run to confirm failure**

```
.venv\Scripts\pytest tests\test_biomechanical_engine.py::test_run_offline_track_returns_angle_per_frame -v
```
Expected: `NotImplementedError`.

- [ ] **Step 3: Replace the stub in `pendulastic_app.py`**

Replace `run_offline_track` inside `BiomechanicalEngine`:

```python
    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
    ) -> list:
        """
        Offline MediaPipe tracking on a recorded video.
        Called on a background thread immediately after STOP (RGB methodology).

        Tracker API (from pendulastic_viewer.py):
          _PatientDetector().detect(frame) -> (patient_kps: ndarray(17,2) | None, _)
          _MPBatchTracker(side, fps).init(frame, hip, knee, ankle)
          tracker.step(frame) -> (hip, knee, ankle, angle_deg)

        COCO indices used: 11=L-hip, 12=R-hip, 13=L-knee, 14=R-knee,
                           15=L-ankle, 16=R-ankle
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return []

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps_v  = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        total  = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 1

        # COCO column offsets: right leg offset=1, left leg offset=0
        col    = 1 if leg.lower() == "right" else 0
        hip_i  = 11 + col   # 12 (right) or 11 (left)
        knee_i = 13 + col   # 14 (right) or 13 (left)
        ank_i  = 15 + col   # 16 (right) or 15 (left)

        detector     = _PatientDetector()
        tracker      = _MPBatchTracker(leg.lower(), fps=fps_v)
        initialised  = False
        angles: list = []

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if not initialised:
                    patient_kps, _ = detector.detect(frame)
                    if patient_kps is not None and patient_kps.shape[0] >= 17:
                        hip   = patient_kps[hip_i].astype(float)
                        knee  = patient_kps[knee_i].astype(float)
                        ankle = patient_kps[ank_i].astype(float)
                        tracker.init(frame, hip, knee, ankle)
                        initialised = True

                if initialised:
                    try:
                        _, _, _, angle = tracker.step(frame)
                        angles.append(float(angle) if angle is not None
                                      else float("nan"))
                    except Exception:
                        angles.append(float("nan"))
                else:
                    angles.append(float("nan"))

                progress_cb(len(angles) / total)
        finally:
            cap.release()

        progress_cb(1.0)
        return angles
```

- [ ] **Step 4: Run all biomechanical engine tests**

```
.venv\Scripts\pytest tests\test_biomechanical_engine.py -v
```
Expected: all tests PASS (offline-track test skips if cv2 unavailable).

- [ ] **Step 5: Commit**

```
git add pendulastic_app.py tests/test_biomechanical_engine.py
git commit -m "feat: BiomechanicalEngine — RGB offline track via MPBatchTracker"
```

---

### Task 4: AcquisitionPanel — widget construction

**Files:**
- Modify: `pendulastic_app.py` — append `AcquisitionPanel(tk.Frame)`
- Create: `tests/test_acquisition_panel.py`

**Interfaces:**
- Produces (public):
  - `AcquisitionPanel(parent, controller)` — `controller` is the `App` instance
  - `self.pid_var`, `self.leg_var`, `self.ms_var`, `self.trial_var` — `tk.StringVar`
  - `self.method_var` — `tk.StringVar`, default `"optitrack"`
  - `self.countdown_var` — `tk.BooleanVar`, default `False`
  - `self.btn_start`, `self.btn_stop` — `tk.Button` at `(row=12, col=0/1)`
  - `self.canvas_tele` — `tk.Canvas(240×80)`, not gridded at init
  - `self.lbl_status`, `self.status_var` — status bar at row 14
  - `self.lbl_method_status` — Consolas coloured status at row 9
  - `self._lockable` — `list[tk.Widget]` of all form widgets that must be disabled during recording

- [ ] **Step 1: Write smoke tests**

```python
# tests/test_acquisition_panel.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk


def _root():
    r = tk.Tk(); r.withdraw(); return r


class _Ctrl:
    """Minimal fake controller."""
    def on_start(self): pass
    def on_stop(self): pass
    def on_methodology_changed(self, m): pass
    def on_new_trial(self): pass


def test_panel_instantiates():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pack()
        r.update()
    finally:
        r.destroy()


def test_default_vars():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        assert p.leg_var.get()      == "Right"
        assert p.method_var.get()   == "optitrack"
        assert p.countdown_var.get() is False
        assert int(p.trial_var.get()) == 1
    finally:
        r.destroy()


def test_telemetry_canvas_not_gridded_at_init():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p.canvas_tele.grid_info() == {}
    finally:
        r.destroy()
```

- [ ] **Step 2: Run to confirm failure**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```
Expected: `ImportError` — `AcquisitionPanel` not defined.

- [ ] **Step 3: Append `AcquisitionPanel` to `pendulastic_app.py`**

```python
# ---------------------------------------------------------------------------
# AcquisitionPanel
# ---------------------------------------------------------------------------

class AcquisitionPanel(tk.Frame):
    """
    2-column, 14-row acquisition panel (480 px wide).
    controller: App instance — receives on_start(), on_stop(),
                on_methodology_changed(method), on_new_trial().
    """

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._countdown_id: Optional[str] = None
        self._tele_buf: list = []
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 5}
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)

        # row 0 — title
        tk.Label(self, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(16, 4))

        # row 1 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 2 — Participant ID
        tk.Label(self, text="Participant ID:").grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        pid_entry = tk.Entry(self, textvariable=self.pid_var, width=22)
        pid_entry.grid(row=2, column=1, sticky="w", **pad)

        # row 3 — Leg
        tk.Label(self, text="Leg:").grid(row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self)
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        rb_left  = tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var, value="Left")
        rb_right = tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right")
        rb_left.pack(side="left", padx=4)
        rb_right.pack(side="left", padx=4)

        # row 4 — MS Status
        tk.Label(self, text="MS Status:").grid(row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ms_combo = ttk.Combobox(self, textvariable=self.ms_var, width=22,
                                state="readonly",
                                values=["MS", "Stroke", "Control", "Other"])
        ms_combo.grid(row=4, column=1, sticky="w", **pad)

        # row 5 — Trial Number
        tk.Label(self, text="Trial Number:").grid(row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        trial_spin = tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6)
        trial_spin.grid(row=5, column=1, sticky="w", **pad)

        # row 6 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 7 — Methodology header
        tk.Label(self, text="Methodology",
                 font=("Segoe UI", 10, "bold")).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=12)

        # row 8 — Methodology radio buttons
        self.method_var = tk.StringVar(value="optitrack")
        meth_f = tk.Frame(self)
        meth_f.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=2)
        rb_opti = tk.Radiobutton(meth_f, text="OptiTrack",  variable=self.method_var,
                                  value="optitrack", command=self._on_method_changed)
        rb_rgb  = tk.Radiobutton(meth_f, text="RGB",         variable=self.method_var,
                                  value="rgb",       command=self._on_method_changed)
        rb_imu  = tk.Radiobutton(meth_f, text="iPhone IMU",  variable=self.method_var,
                                  value="imu",       command=self._on_method_changed)
        for rb in (rb_opti, rb_rgb, rb_imu):
            rb.pack(side="left", padx=8)

        # row 9 — Modality status
        self.lbl_method_status = tk.Label(
            self, text="● Ready", font=("Consolas", 9), fg="green", anchor="w")
        self.lbl_method_status.grid(row=9, column=0, columnspan=2, sticky="w", padx=16)

        # row 10 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 11 — countdown checkbox
        self.countdown_var = tk.BooleanVar(value=False)
        countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var)
        countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        # row 12 — START / STOP (START never moves from col 0)
        self.btn_start = tk.Button(
            self, text="START RECORDING",
            bg=_GREEN, fg="white", font=("Segoe UI", 13, "bold"),
            width=16, height=2, command=self._on_start_clicked)
        self.btn_start.grid(row=12, column=0, padx=10, pady=12)

        self.btn_stop = tk.Button(
            self, text="STOP",
            bg=_RED, fg="white", font=("Segoe UI", 13, "bold"),
            width=16, height=2, state="disabled",
            command=self._on_stop_clicked)
        self.btn_stop.grid(row=12, column=1, padx=10, pady=12)

        # row 13 — live telemetry canvas (NOT gridded at init; shown during RECORDING)
        self.canvas_tele = tk.Canvas(
            self, width=440, height=80, bg="#0B1928", highlightthickness=0)

        # row 14 — status bar
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w", fg="#333")
        self.lbl_status.grid(row=14, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))

        # Track every form widget that must be locked during recording
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            countdown_chk, rb_opti, rb_rgb, rb_imu,
        ]

    def _on_method_changed(self) -> None:
        self.controller.on_methodology_changed(self.method_var.get())

    def _on_start_clicked(self) -> None:
        pass   # implemented in Task 5

    def _on_stop_clicked(self) -> None:
        pass   # implemented in Task 5
```

- [ ] **Step 4: Run smoke tests**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: AcquisitionPanel — widget construction"
```

---

### Task 5: AcquisitionPanel — state machine

**Files:**
- Modify: `pendulastic_app.py` — fill in state machine methods, replacing the stubs from Task 4
- Modify: `tests/test_acquisition_panel.py` — add transition tests

**Interfaces:**
- Produces (called by `App` and internally):
  - `AcquisitionPanel.enter_idle() -> None`
  - `AcquisitionPanel.enter_recording() -> None`
  - `AcquisitionPanel.enter_processing() -> None`
  - `AcquisitionPanel.validate_metadata() -> tuple[bool, str]`
  - `AcquisitionPanel.get_metadata() -> dict`  — `{"pid", "leg", "ms_status", "trial": int, "methodology"}`
  - `AcquisitionPanel.increment_trial() -> None`

- [ ] **Step 1: Add state machine tests**

Append to `tests/test_acquisition_panel.py`:

```python
def test_start_without_countdown_calls_on_start():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def on_start(self): calls.append("start")
        p = AcquisitionPanel(r, C()); p.pack()
        p.pid_var.set("P1")
        p.countdown_var.set(False)
        p._on_start_clicked()
        r.update()
        assert "start" in calls
    finally:
        r.destroy()


def test_start_with_countdown_shows_cancel():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack()
        p.pid_var.set("P1")
        p.countdown_var.set(True)
        p._on_start_clicked()
        r.update()
        assert p.btn_start.cget("text") == "CANCEL"
    finally:
        r.destroy()


def test_enter_recording_shows_telemetry():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.enter_recording(); r.update()
        assert p.canvas_tele.grid_info() != {}
    finally:
        r.destroy()


def test_enter_idle_hides_telemetry():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.enter_recording()
        p.enter_idle(); r.update()
        assert p.canvas_tele.grid_info() == {}
    finally:
        r.destroy()


def test_validate_empty_pid_fails():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("")
        ok, msg = p.validate_metadata()
        assert not ok and "Participant ID" in msg
    finally:
        r.destroy()


def test_get_metadata_returns_correct_dict():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P7"); p.leg_var.set("Left")
        p.ms_var.set("Stroke"); p.trial_var.set("3")
        p.method_var.set("imu")
        assert p.get_metadata() == {
            "pid": "P7", "leg": "Left", "ms_status": "Stroke",
            "trial": 3, "methodology": "imu"}
    finally:
        r.destroy()


def test_increment_trial():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.trial_var.set("4")
        p.increment_trial()
        assert int(p.trial_var.get()) == 5
    finally:
        r.destroy()
```

- [ ] **Step 2: Run to confirm failures**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```
Expected: 7 new tests FAIL.

- [ ] **Step 3: Replace stubs and add state machine methods in `AcquisitionPanel`**

Replace `_on_start_clicked` and `_on_stop_clicked` and add the following methods inside `AcquisitionPanel` (after `_build_widgets`):

```python
    # ------------------------------------------------------------------
    # Public state transitions (called by App)
    # ------------------------------------------------------------------
    def enter_idle(self) -> None:
        self._cancel_countdown()
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked,
                              bg=_GREEN, state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_form(False)
        self.canvas_tele.grid_remove()
        self.status_var.set("Idle — ready to record.")

    def enter_recording(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="START RECORDING",
                              bg=_GREEN, state="disabled")
        self.btn_stop.config(state="normal")
        self.canvas_tele.grid(row=13, column=0, columnspan=2, padx=10, pady=4)
        self.status_var.set("RECORDING…")

    def enter_processing(self) -> None:
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.status_var.set("Running MediaPipe tracking…")

    # ------------------------------------------------------------------
    # Validation and metadata
    # ------------------------------------------------------------------
    def validate_metadata(self) -> tuple:
        pid = self.pid_var.get().strip()
        if not pid:
            return False, "Participant ID cannot be empty."
        illegal = set('<>:"/\\|?*')
        if any(c in illegal for c in pid):
            return False, 'Participant ID contains illegal characters: < > : " / \\ | ? *'
        return True, ""

    def get_metadata(self) -> dict:
        return {
            "pid":        self.pid_var.get().strip(),
            "leg":        self.leg_var.get(),
            "ms_status":  self.ms_var.get(),
            "trial":      int(self.trial_var.get()),
            "methodology": self.method_var.get(),
        }

    def increment_trial(self) -> None:
        self.trial_var.set(str(int(self.trial_var.get()) + 1))

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        ok, msg = self.validate_metadata()
        if not ok:
            messagebox.showerror("Cannot Start", msg)
            return
        if self.countdown_var.get():
            self._start_countdown()
        else:
            self.controller.on_start()

    def _on_stop_clicked(self) -> None:
        self.controller.on_stop()

    def _on_method_changed(self) -> None:
        self.controller.on_methodology_changed(self.method_var.get())

    # ------------------------------------------------------------------
    # Countdown
    # ------------------------------------------------------------------
    def _start_countdown(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="CANCEL",
                              command=self._cancel_countdown, bg=_AMBER)
        self.btn_stop.config(state="disabled")
        self._tick_countdown(5)

    def _tick_countdown(self, n: int) -> None:
        if n == 0:
            self.btn_start.config(text="START RECORDING",
                                  command=self._on_start_clicked, bg=_GREEN)
            self.controller.on_start()
            return
        self.status_var.set(f"Starting in {n}…")
        self._countdown_id = self.after(1000, lambda: self._tick_countdown(n - 1))

    def _cancel_countdown(self) -> None:
        if self._countdown_id:
            self.after_cancel(self._countdown_id)
            self._countdown_id = None
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked,
                              bg=_GREEN, state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_form(False)
        self.status_var.set("Countdown cancelled — ready to record.")

    # ------------------------------------------------------------------
    # Form lock
    # ------------------------------------------------------------------
    def _lock_form(self, locked: bool) -> None:
        for w in self._lockable:
            cls = w.winfo_class()
            if cls == "TCombobox":
                w.config(state="disabled" if locked else "readonly")
            else:
                w.config(state="disabled" if locked else "normal")
```

- [ ] **Step 4: Run all acquisition panel tests**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: AcquisitionPanel — state machine and countdown"
```

---

### Task 6: AcquisitionPanel — IMU telemetry sparkline

**Files:**
- Modify: `pendulastic_app.py` — add `push_telemetry` / `clear_telemetry` / `_draw_sparkline` to `AcquisitionPanel`
- Modify: `tests/test_acquisition_panel.py` — add telemetry tests

**Interfaces:**
- Produces:
  - `AcquisitionPanel.push_telemetry(t: float, angle_deg: float) -> None`
  - `AcquisitionPanel.clear_telemetry() -> None`

- [ ] **Step 1: Add telemetry tests**

Append to `tests/test_acquisition_panel.py`:

```python
def test_push_telemetry_draws_items_on_canvas():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack()
        p.enter_recording(); r.update()
        p.push_telemetry(0.0, 160.0)
        p.push_telemetry(0.05, 155.0)
        r.update()
        assert len(p.canvas_tele.find_all()) > 0
    finally:
        r.destroy()


def test_clear_telemetry_removes_all_items():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack()
        p.enter_recording()
        p.push_telemetry(0.0, 160.0)
        p.clear_telemetry()
        r.update()
        assert len(p.canvas_tele.find_all()) == 0
    finally:
        r.destroy()
```

- [ ] **Step 2: Run to confirm failure**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_push_telemetry_draws_items_on_canvas tests\test_acquisition_panel.py::test_clear_telemetry_removes_all_items -v
```
Expected: `AttributeError` — `push_telemetry` not defined.

- [ ] **Step 3: Add telemetry methods to `AcquisitionPanel`**

Add these methods inside `AcquisitionPanel` (after `_lock_form`):

```python
    # ------------------------------------------------------------------
    # Live telemetry sparkline (driven by App._tick every 50 ms)
    # ------------------------------------------------------------------
    _TELE_MAX = 120   # rolling window ≈ 6 s at 20 Hz

    def push_telemetry(self, t: float, angle_deg: float) -> None:
        self._tele_buf.append((t, angle_deg))
        if len(self._tele_buf) > self._TELE_MAX:
            self._tele_buf.pop(0)
        self._draw_sparkline()

    def clear_telemetry(self) -> None:
        self._tele_buf.clear()
        self.canvas_tele.delete("all")

    def _draw_sparkline(self) -> None:
        import math
        c = self.canvas_tele
        c.delete("all")
        if not self._tele_buf:
            return

        W, H    = 440, 80
        NUM_W   = 110
        GRAPH_W = W - NUM_W - 8
        last_a  = self._tele_buf[-1][1]

        # Numeric readout on the right
        if math.isnan(last_a):
            txt, col = "—", "gray"
        else:
            txt, col = f"{last_a:.1f}°", "#22c55e"
        cx = W - NUM_W // 2
        c.create_text(cx, H // 2 - 6, text=txt,
                      fill="white", font=("Consolas", 18, "bold"), anchor="center")
        c.create_text(cx, H // 2 + 14, text="knee",
                      fill="#5A8AB0", font=("Consolas", 8), anchor="center")

        # Sparkline
        valid = [(t, a) for t, a in self._tele_buf if not math.isnan(a)]
        if len(valid) < 2:
            return
        vals  = [a for _, a in valid]
        lo, hi = min(vals), max(vals)
        if hi - lo < 5:
            mid = (lo + hi) / 2; lo, hi = mid - 2.5, mid + 2.5

        def px(i, a):
            x = int(8 + (i / (len(valid) - 1)) * (GRAPH_W - 16))
            y = int(H - 8 - ((a - lo) / (hi - lo)) * (H - 16))
            return x, y

        pts = [px(i, a) for i, (_, a) in enumerate(valid)]
        for i in range(len(pts) - 1):
            c.create_line(*pts[i], *pts[i + 1], fill=col, width=1.5)
        lx, ly = pts[-1]
        c.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=col, outline="")
```

- [ ] **Step 4: Run all acquisition panel tests**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: AcquisitionPanel — IMU telemetry sparkline"
```

---

### Task 7: PostProcessingPanel

**Files:**
- Modify: `pendulastic_app.py` — append `PostProcessingPanel(tk.Frame)`
- Create: `tests/test_post_processing_panel.py`

**Interfaces:**
- Consumes (verified key names from `pendulastic_pt_score.py`):
  - `compute_pt_params(t: np.ndarray, angle_raw: np.ndarray) -> dict | None`
    - Keys used: `A1_deg` (float), `omega_peak_deg_s` (float), `N` (float), `f` (float), `R2n` (float)
  - `compute_pt_score_simple(params: dict) -> float`
  - `pt_to_mas(score: float) -> str`
  - `load_optitrack(csv_path: str) -> list[float]`
- Produces:
  - `PostProcessingPanel(parent, controller)`
  - `PostProcessingPanel.load_trial(angles, fps, metadata, filename) -> None`
  - `PostProcessingPanel.load_optitrack_overlay(csv_path) -> None`
  - `self.title_var`, `self.mas_var`, `self.score_var` — `tk.StringVar` (readable in tests)

- [ ] **Step 1: Write smoke tests**

```python
# tests/test_post_processing_panel.py
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

def _root():
    r = tk.Tk(); r.withdraw(); return r

class _Ctrl:
    def on_new_trial(self): pass


def test_panel_instantiates():
    from pendulastic_app import PostProcessingPanel
    r = _root()
    try:
        p = PostProcessingPanel(r, _Ctrl())
        p.pack(fill="both", expand=True); r.update()
    finally:
        r.destroy()


def test_load_trial_sets_title():
    from pendulastic_app import PostProcessingPanel
    r = _root()
    try:
        p = PostProcessingPanel(r, _Ctrl())
        p.pack(fill="both", expand=True)
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
                "trial": 1, "methodology": "imu"}
        p.load_trial([170.0] * 60, 30.0, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
        r.update()
        assert "PID_P1" in p.title_var.get()
    finally:
        r.destroy()


def test_load_trial_populates_mas():
    from pendulastic_app import PostProcessingPanel
    r = _root()
    try:
        p = PostProcessingPanel(r, _Ctrl())
        p.pack(fill="both", expand=True)
        # Generate a damped sinusoid that compute_pt_params can score
        angles = [160.0 + 20.0 * math.sin(i * 0.2) * math.exp(-i * 0.04)
                  for i in range(120)]
        meta   = {"pid": "P1", "leg": "Right", "ms_status": "MS",
                  "trial": 1, "methodology": "rgb"}
        p.load_trial(angles, 30.0, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
        r.update()
        # MAS should be populated (not the placeholder "—")
        assert p.mas_var.get() != "—"
    finally:
        r.destroy()
```

- [ ] **Step 2: Run to confirm failure**

```
.venv\Scripts\pytest tests\test_post_processing_panel.py -v
```
Expected: `ImportError` — `PostProcessingPanel` not defined.

- [ ] **Step 3: Append `PostProcessingPanel` to `pendulastic_app.py`**

```python
# ---------------------------------------------------------------------------
# PostProcessingPanel
# ---------------------------------------------------------------------------

class PostProcessingPanel(tk.Frame):
    """
    Full-window post-processing panel: angle curve + PT metrics (rows 0–4).
    rowconfigure(1, weight=1) lets the matplotlib figure expand to fill height.
    """

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller   = controller
        self._angles: list = []
        self._fps: float   = 30.0
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        # row 0 — title (trial filename)
        self.title_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 4))

        # row 1 — matplotlib figure
        if _MPL_AVAIL:
            self._fig    = Figure(figsize=(10, 4), dpi=96, facecolor="#EEF2F7")
            self._ax     = self._fig.add_subplot(111)
            self._canvas = FigureCanvasTkAgg(self._fig, master=self)
            self._canvas.get_tk_widget().grid(
                row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)
        else:
            tk.Label(self, text="matplotlib not available — install it in .venv",
                     fg="red").grid(row=1, column=0, columnspan=2)
            self._canvas = None

        # row 2 — PT Metrics LabelFrame
        mf = tk.LabelFrame(self, text="Popović Pendulum Test Metrics",
                           font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        mf.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        self.a1_var    = tk.StringVar(value="—")
        self.omega_var = tk.StringVar(value="—")
        self.n_var     = tk.StringVar(value="—")
        self.f_var     = tk.StringVar(value="—")
        self.r2n_var   = tk.StringVar(value="—")
        self.mas_var   = tk.StringVar(value="—")
        self.score_var = tk.StringVar(value="—")

        for col, (lbl, var) in enumerate([
            ("A1 (°)",    self.a1_var),
            ("ω (°/s)",   self.omega_var),
            ("N",         self.n_var),
            ("f (Hz)",    self.f_var),
            ("R₂N",       self.r2n_var),
            ("MAS",       self.mas_var),
            ("Score",     self.score_var),
        ]):
            tk.Label(mf, text=lbl, font=("Segoe UI", 8), fg="#555").grid(
                row=0, column=col, padx=10, pady=1)
            tk.Label(mf, textvariable=var,
                     font=("Segoe UI", 11, "bold")).grid(
                row=1, column=col, padx=10)

        # row 3 — action buttons
        tk.Button(self, text="← New Trial",
                  bg=_BLUE, fg="white", font=("Segoe UI", 11, "bold"),
                  width=14, height=2,
                  command=self._on_new_trial).grid(
            row=3, column=0, padx=10, pady=12, sticky="e")
        tk.Button(self, text="📂 Load OptiTrack CSV",
                  font=("Segoe UI", 10), width=20, height=2,
                  command=self._on_load_optitrack).grid(
            row=3, column=1, padx=10, pady=12, sticky="w")

        # row 4 — status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w", fg="#333").grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_trial(self, angles: list, fps: float,
                   metadata: dict, filename: str) -> None:
        self._angles = angles
        self._fps    = fps
        self.title_var.set(filename)
        self._plot_curve(angles, fps)
        self._show_pt_metrics(angles, fps)
        self.status_var.set(f"Saved: {filename}")

    def load_optitrack_overlay(self, csv_path: str) -> None:
        if not _PT_AVAIL or load_optitrack is None:
            messagebox.showerror("OptiTrack", "load_optitrack not available.")
            return
        try:
            opti = load_optitrack(csv_path)
            self._plot_curve(self._angles, self._fps, overlay=opti)
            self.status_var.set(f"Overlay: {os.path.basename(csv_path)}")
        except Exception as e:
            messagebox.showerror("OptiTrack Load Error", str(e))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _plot_curve(self, angles: list, fps: float,
                    overlay: list | None = None) -> None:
        if not _MPL_AVAIL or self._canvas is None:
            return
        self._ax.clear()
        times = [i / fps for i in range(len(angles))]
        self._ax.plot(times, angles, color="#2563EB", linewidth=1.5,
                      label="Knee angle")
        if overlay:
            t_ot = [i / fps for i in range(len(overlay))]
            self._ax.plot(t_ot, overlay, color="#16A34A", linewidth=1.5,
                          linestyle="--", label="OptiTrack")
            self._ax.legend(fontsize=8)
        self._ax.set_xlabel("Time (s)", fontsize=9)
        self._ax.set_ylabel("Knee angle (°)", fontsize=9)
        self._ax.set_title("Popović Pendulum Test — Knee Angle", fontsize=10)
        self._ax.grid(True, alpha=0.3)
        self._fig.tight_layout()
        self._canvas.draw()

    def _show_pt_metrics(self, angles: list, fps: float) -> None:
        if not _PT_AVAIL or compute_pt_params is None:
            return
        try:
            t   = np.arange(len(angles), dtype=float) / fps
            arr = np.array(angles, dtype=float)
            p   = compute_pt_params(t, arr)
            if p is None:
                self.status_var.set("PT scoring: insufficient data (need ≥ 40 finite frames).")
                return
            score = compute_pt_score_simple(p)
            mas   = pt_to_mas(score)
            self.a1_var.set(f"{p['A1_deg']:.1f}")
            self.omega_var.set(f"{p['omega_peak_deg_s']:.1f}")
            self.n_var.set(f"{p['N']:.1f}")
            self.f_var.set(f"{p['f']:.2f}")
            self.r2n_var.set(f"{p['R2n']:.3f}")
            self.mas_var.set(str(mas))
            self.score_var.set(f"{score:.3f}")
        except Exception as e:
            self.status_var.set(f"PT scoring error: {e}")

    def _on_new_trial(self) -> None:
        self.controller.on_new_trial()

    def _on_load_optitrack(self) -> None:
        path = filedialog.askopenfilename(
            title="Select OptiTrack CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.load_optitrack_overlay(path)
```

- [ ] **Step 4: Run all post-processing panel tests**

```
.venv\Scripts\pytest tests\test_post_processing_panel.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```
git add pendulastic_app.py tests/test_post_processing_panel.py
git commit -m "feat: PostProcessingPanel — angle plot and PT metrics"
```

---

### Task 8: App host — ports, panel switching, tick loop, entry point

**Files:**
- Modify: `pendulastic_app.py` — append `App(tk.Tk)` and `if __name__ == "__main__"` block
- Create: `tests/test_app.py`

**Interfaces:**
- Consumes: `AcquisitionPanel`, `PostProcessingPanel`, `BiomechanicalEngine`, `DataManager`
- Consumes: `_imu.start()`, `_imu.stop()`, `_imu.start_recording(path, meta)`, `_imu.stop_recording()`
- Consumes: `_motive.start_local_motive(msg)`, `_motive.stop_local_motive()`

**Controller interface** (called by panels, implemented on `App`):
- `on_start()`, `on_stop()`, `on_new_trial()`, `on_methodology_changed(method)`

- [ ] **Step 1: Write integration smoke tests**

```python
# tests/test_app.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk


def test_app_starts_with_acquisition_visible():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_on_new_trial_increments_trial_and_returns_to_acquisition():
    from pendulastic_app import App
    app = App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("2")
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
                "trial": 2, "methodology": "imu"}
        app._transition_to_review("PID_P1_LEG_Right_MS_TRIAL_2.csv",
                                   [170.0] * 30, meta)
        app.update()
        app.on_new_trial()
        app.update()
        assert int(app._acq.trial_var.get()) == 3
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_on_methodology_changed_does_not_crash(monkeypatch):
    import pendulastic_app as _m, types
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu",
        types.SimpleNamespace(
            start=lambda: None, stop=lambda: None,
            get_state=lambda: {
                "distal":   {"pitch": 0.0, "roll": 0.0, "yaw": 0.0},
                "proximal": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0},
            }))
    from pendulastic_app import App
    app = App()
    try:
        app.on_methodology_changed("rgb")
        app.on_methodology_changed("imu")
        app.on_methodology_changed("optitrack")
        app.update()
    finally:
        app.destroy()
```

- [ ] **Step 2: Run to confirm failure**

```
.venv\Scripts\pytest tests\test_app.py -v
```
Expected: `ImportError` — `App` not defined.

- [ ] **Step 3: Append `App` class and entry point to `pendulastic_app.py`**

```python
# ---------------------------------------------------------------------------
# App  (thin host)
# ---------------------------------------------------------------------------

class App(tk.Tk):
    """
    Owns: IMU server lifecycle, UDP port 8888 lifecycle, panel switching,
    IMU poll thread → queue → sparkline tick.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("Pendulastic")
        self.geometry("500x740")
        self.resizable(False, True)

        self._state           = "idle"
        self._engine: Optional[BiomechanicalEngine] = None
        self._imu_queue: queue.Queue   = queue.Queue()
        self._imu_poll_stop            = threading.Event()
        self._imu_poll_thread: Optional[threading.Thread] = None
        self._rec_angles:     list     = []
        self._rec_timestamps: list     = []
        self._video_path:     str      = ""

        # Start IMU WebSocket server (port 5000) once for this process
        if _IMU_AVAIL:
            try:
                _imu.start()
            except Exception:
                pass

        self._acq  = AcquisitionPanel(self, controller=self)
        self._post = PostProcessingPanel(self, controller=self)
        self._acq.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._tick()

    # ------------------------------------------------------------------
    # Controller interface (called by AcquisitionPanel / PostProcessingPanel)
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        meta   = self._acq.get_metadata()
        method = meta["methodology"]
        self._engine         = BiomechanicalEngine(method)
        self._rec_angles     = []
        self._rec_timestamps = []
        self._acq.clear_telemetry()

        if method == "imu":
            self._start_imu_recording(meta)
        elif method == "rgb":
            self._start_rgb_recording(meta)
        elif method == "optitrack":
            self._start_optitrack_recording(meta)

        self._state = "recording"
        self._acq.enter_recording()

    def on_stop(self) -> None:
        # Stop IMU poll thread unconditionally
        self._imu_poll_stop.set()
        if self._imu_poll_thread:
            self._imu_poll_thread.join(timeout=1.0)
        self._imu_poll_stop.clear()
        self._imu_poll_thread = None

        meta   = self._acq.get_metadata()
        method = meta["methodology"]

        if method == "imu":
            if _IMU_AVAIL:
                try:
                    _imu.stop_recording()
                except Exception:
                    pass
            fn   = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
            DataManager.save_trial(
                fn, self._rec_angles, meta,
                timestamps=self._rec_timestamps or None)
            self._transition_to_review(fn, self._rec_angles, meta)

        elif method == "rgb":
            self._stop_rgb_recording()
            self._state = "processing"
            self._acq.enter_processing()
            threading.Thread(
                target=self._run_rgb_processing,
                args=(meta,), daemon=True,
            ).start()

        elif method == "optitrack":
            if _MOTIVE_AVAIL:
                try:
                    _motive.stop_local_motive()
                except Exception:
                    pass
            fn = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
            # No angles available live — user loads OptiTrack CSV in the panel
            self._transition_to_review(fn, [], meta)

    def on_new_trial(self) -> None:
        self._acq.increment_trial()
        self._post.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._acq.enter_idle()
        self.geometry("500x740")
        self._state = "idle"

    def on_methodology_changed(self, method: str) -> None:
        # Stop any running poll thread (safe to call even if not running)
        self._imu_poll_stop.set()
        if self._imu_poll_thread:
            self._imu_poll_thread.join(timeout=0.5)
        self._imu_poll_stop.clear()
        self._imu_poll_thread = None

        if method == "imu":
            label = ("● iPhone IMU — waiting for phone" if _IMU_AVAIL
                     else "● IMU module unavailable")
            color = "#B36B00" if _IMU_AVAIL else "red"
        elif method == "rgb":
            label = ("● RGB / MediaPipe ready" if _VIEWER_AVAIL
                     else "● MediaPipe unavailable (pendulastic_viewer not importable)")
            color = "green" if _VIEWER_AVAIL else "red"
        else:
            label = ("● OptiTrack (Motive)" if _MOTIVE_AVAIL
                     else "● OptiTrack — Motive not found (will skip live sync)")
            color = "green" if _MOTIVE_AVAIL else "#B36B00"
        self._acq.lbl_method_status.config(text=label, fg=color)

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------
    def _start_imu_recording(self, meta: dict) -> None:
        fn   = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        path = os.path.join(DataManager.DATA_DIR, fn)
        os.makedirs(DataManager.DATA_DIR, exist_ok=True)
        if _IMU_AVAIL:
            try:
                _imu.start_recording(path, meta)
            except Exception as e:
                messagebox.showwarning(
                    "IMU Recording",
                    f"IMU CSV could not be opened:\n{e}\n\nContinuing without IMU CSV.")
        # Start background poll thread for live sparkline
        self._imu_poll_stop.clear()
        self._imu_poll_thread = threading.Thread(
            target=self._imu_poll_worker, daemon=True)
        self._imu_poll_thread.start()

    def _imu_poll_worker(self) -> None:
        """Put (t, angle_deg) into _imu_queue at ~20 Hz."""
        while not self._imu_poll_stop.is_set():
            if self._engine:
                angle = self._engine.get_live_angle()
                self._imu_queue.put((time.time(), angle))
            time.sleep(0.05)

    def _start_rgb_recording(self, meta: dict) -> None:
        if not _CV2_AVAIL:
            messagebox.showerror("RGB", "OpenCV (cv2) is not installed.")
            return
        fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        os.makedirs(DataManager.DATA_DIR, exist_ok=True)
        self._video_path = os.path.join(
            DataManager.DATA_DIR, fn.replace(".csv", ".avi"))
        cap = _cv2.VideoCapture(0)
        w   = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH))
        h   = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))
        self._rgb_cap    = cap
        self._rgb_writer = _cv2.VideoWriter(
            self._video_path, _cv2.VideoWriter_fourcc(*"XVID"), 30.0, (w, h))
        self._rgb_stop   = threading.Event()
        self._rgb_thread = threading.Thread(
            target=self._rgb_record_worker, daemon=True)
        self._rgb_thread.start()

    def _rgb_record_worker(self) -> None:
        while not self._rgb_stop.is_set():
            ret, frame = self._rgb_cap.read()
            if ret and frame is not None and self._rgb_writer:
                self._rgb_writer.write(frame)

    def _stop_rgb_recording(self) -> None:
        if hasattr(self, "_rgb_stop"):
            self._rgb_stop.set()
        if hasattr(self, "_rgb_thread"):
            self._rgb_thread.join(timeout=2.0)
        if hasattr(self, "_rgb_writer") and self._rgb_writer:
            self._rgb_writer.release()
            self._rgb_writer = None
        if hasattr(self, "_rgb_cap") and self._rgb_cap:
            self._rgb_cap.release()
            self._rgb_cap = None

    def _run_rgb_processing(self, meta: dict) -> None:
        def progress(pct: float) -> None:
            self.after(0, lambda p=pct: self._acq.status_var.set(
                f"MediaPipe tracking: {int(p * 100)}%"))

        leg    = meta.get("leg", "right").lower()
        angles = self._engine.run_offline_track(self._video_path, progress, leg=leg)
        fn     = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        DataManager.save_trial(fn, angles, meta, fps=30.0)
        self.after(0, lambda: self._transition_to_review(fn, angles, meta))

    def _start_optitrack_recording(self, meta: dict) -> None:
        if _MOTIVE_AVAIL:
            try:
                msg = (f"START|id={meta['pid']}|leg={meta['leg']}|"
                       f"trial={meta['trial']}")
                _motive.start_local_motive(msg)
            except Exception as e:
                messagebox.showwarning(
                    "Motive Sync",
                    f"Could not trigger Motive:\n{type(e).__name__}: {e}\n\n"
                    "Recording will continue without OptiTrack sync.")

    # ------------------------------------------------------------------
    # Panel switching
    # ------------------------------------------------------------------
    def _transition_to_review(self, filename: str,
                               angles: list, meta: dict) -> None:
        self._state = "review"
        self._post.load_trial(angles, self._fps_for(meta), meta, filename)
        self._acq.pack_forget()
        self._post.pack(fill="both", expand=True)
        self.state("zoomed")

    @staticmethod
    def _fps_for(meta: dict) -> float:
        return 30.0   # RGB and OptiTrack; IMU timestamps are explicit

    # ------------------------------------------------------------------
    # 50 ms tick — drain IMU queue → sparkline
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        try:
            while not self._imu_queue.empty():
                t, angle = self._imu_queue.get_nowait()
                if self._state == "recording":
                    self._rec_angles.append(angle)
                    self._rec_timestamps.append(t)
                    self._acq.push_telemetry(t, angle)
        except queue.Empty:
            pass
        self.after(50, self._tick)

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def on_close(self) -> None:
        self._imu_poll_stop.set()
        if self._imu_poll_thread:
            self._imu_poll_thread.join(timeout=0.5)
        if hasattr(self, "_rgb_stop"):
            self._rgb_stop.set()
        if _IMU_AVAIL:
            try:
                _imu.stop()
            except Exception:
                pass
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    App().mainloop()
```

- [ ] **Step 4: Run full test suite**

```
.venv\Scripts\pytest tests\ -v
```
Expected: all tests PASS (offline-track test skips if cv2 unavailable).

- [ ] **Step 5: Manual golden-path test**

```
.venv\Scripts\python.exe pendulastic_app.py
```

Walk through:
1. Enter Participant ID `P1`, Leg `Right`, MS Status `MS`, Trial `1`
2. Select methodology `iPhone IMU`
3. Click **START RECORDING** — verify form locks, telemetry canvas appears at row 13
4. Observe sparkline updating (if phone is streaming) or static `—` (no phone)
5. Click **STOP** — verify instant transition to PostProcessingPanel
6. Verify PT metrics panel shows values or `—` (no data = expected without real swing)
7. Verify `data/PID_P1_LEG_Right_MS_TRIAL_1.csv` exists in the project root
8. Click **← New Trial** — verify trial number incremented to `2`, form unlocked
9. Repeat with methodology `RGB` (requires webcam) — verify MediaPipe progress bar appears after STOP before panel switches

- [ ] **Step 6: Commit**

```
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: App host — port lifecycle, panel switching, tick, entry point"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task covering it |
|---|---|
| DataManager filename + CSV | Task 1 |
| BiomechanicalEngine IMU shank-only | Task 2 |
| BiomechanicalEngine RGB offline | Task 3 |
| AcquisitionPanel widgets (14 rows, 2 cols) | Task 4 |
| State machine (IDLE→COUNTDOWN→RECORDING) | Task 5 |
| Countdown bypass when checkbox unchecked | Task 5 — `_on_start_clicked` reads `countdown_var` |
| START button mutation in-place (no jitter) | Task 5 — confirmed: `.config()` only, never `.grid()` again |
| Live telemetry sparkline + queue | Task 6 |
| PostProcessingPanel (plot + PT metrics) | Task 7 |
| App host + port lifecycle + panel switch | Task 8 |
| "← New Trial" increments trial# | Task 8 `on_new_trial()` |
| Load OptiTrack CSV overlay | Task 7 `load_optitrack_overlay()` |
| Optional Motive sync (warn, don't fail) | Task 8 `_start_optitrack_recording()` |
| Flat `data/` folder, no hierarchy | Task 1 `DataManager.DATA_DIR` + Task 8 |

**Placeholder scan:** No TBDs, no "implement later", no vague steps. All code blocks are runnable.

**Type consistency:**
- `DataManager.save_trial(filename, angles, metadata, timestamps=None, fps=30.0)` — consistent across Tasks 1, 8.
- `BiomechanicalEngine.run_offline_track(video_path, progress_cb, leg="right")` — consistent across Tasks 2, 3, 8.
- `App._transition_to_review(filename, angles, meta)` — consistent across Tasks 7 test (`load_trial`) and Task 8 callers.
- `compute_pt_params(t, arr)` takes two numpy arrays — correctly used in Task 7 `_show_pt_metrics`.
- Tracker API: `.init(frame, hip, knee, ankle)` + `.step(frame) → (hip, knee, ankle, angle)` — verified from source and used correctly in Task 3.
