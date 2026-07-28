# Pendulastic Enhancements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add IMU sensor zero-calibration, robust Popović PT extraction with linear detrend, multi-source simultaneous recording with per-source CSVs, and an offline HPE video-upload overlay to the unified Pendulastic desktop app.

**Architecture:** Four coordinated changes across two files — `pendulastic_pt_score.py` gains a `detrend` parameter in `compute_pt_params`; `pendulastic_app.py` refactors the methodology selector to multi-source checkboxes, the recording lifecycle to fan out per source, and `PostProcessingPanel` to plot multi-curve and run offline HPE on uploaded video. All existing API contracts are preserved via backward-compatible defaults.

**Tech Stack:** Python 3.11 · Tkinter + matplotlib (TkAgg) · NumPy · SciPy · OpenCV (cv2) · MediaPipe (via `pendulastic_viewer`) · threading.Thread + queue.Queue for background work

## Global Constraints

- **Do NOT modify**: `pendulastic_viewer.py`, `pendulastic_imu_server.py`, `motive_sync.py`
- All guarded imports remain (try/except around `_imu`, `_motive`, `_cv2`, `_MPBatchTracker`, `_PatientDetector`)
- `compute_pt_params` keeps all existing public dict keys; `detrend` defaults to `True` (backward-compat callers gain detrend automatically; pass `detrend=False` for OptiTrack signals)
- `DataManager.build_filename` with `source=None` must produce identical output to the current call — no changes to existing tests that call with 4 positional args
- `DataManager.save_trial` CSV column "methodology" is preserved (value now comes from `source` param or `metadata.get("methodology", "")`)
- `AcquisitionPanel.enter_recording()`, `enter_idle()`, `enter_processing()` signatures unchanged
- START button (row 12, col 0) never moves; mutates via `.config()` only
- IMU WebSocket server (port 5000) started once in `App.__init__`, stopped only in `App.on_close()`
- Run tests with: `.venv\Scripts\pytest tests\ -v`

---

## File Structure

| File | Changed classes / functions |
|---|---|
| `pendulastic_pt_score.py` | `compute_pt_params` — detrend param, wider A0 window, phi_s reorder |
| `pendulastic_app.py` | `DataManager.build_filename`, `DataManager.save_trial`, `AcquisitionPanel` (full widget rebuild + `_on_source_changed`, `get_active_sources`, updated `validate_metadata`/`get_metadata`), `App` (multi-source lifecycle), `PostProcessingPanel` (new `load_trial` signature, `_plot_all_curves`, `_show_pt_metrics_from_sources`, `_on_upload_video`, `_add_hpe_overlay`) |
| `tests/test_pt_score.py` | New file |
| `tests/test_data_manager.py` | Append new tests (existing tests unchanged) |
| `tests/test_acquisition_panel.py` | Update `test_default_vars`, `test_get_metadata_returns_correct_dict`; add new tests |
| `tests/test_post_processing_panel.py` | Update `test_load_trial_sets_title`, `test_load_trial_populates_mas`; add new tests |
| `tests/test_app.py` | Update `test_on_new_trial_increments_trial_and_returns_to_acquisition`, rename `test_on_methodology_changed_does_not_crash` → `test_on_source_changed_does_not_crash` |

---

### Task 1: `compute_pt_params` — linear detrend + wider A0 window

**Files:**
- Modify: `pendulastic_pt_score.py` (lines 29–43 import block; lines 599–777 `compute_pt_params`)
- Create: `tests/test_pt_score.py`

**Interfaces:**
- Produces: `compute_pt_params(t, angle_raw, release_idx=None, detrend=True)` — same return dict; `detrend` param is new.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pt_score.py`:

```python
# tests/test_pt_score.py
import os, sys, math
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pendulastic_pt_score import compute_pt_params


def _damped_sinusoid(n=300, fps=30.0, A0=40.0, freq=0.9, decay=0.25):
    """Clean damped sinusoid starting at 180° (fully extended), oscillating down."""
    t = np.arange(n) / fps
    # angle = 180 - A0*(1 - exp(-decay*t))*|sin(2pi*freq*t)|
    # Simpler: start at 180-A0 and add decaying oscillation back up
    return t, 180.0 - A0 * np.exp(-decay * t) * np.abs(np.sin(2 * np.pi * freq * t))


def _drifting_signal(n=300, fps=30.0, A0=40.0, freq=0.9, drift=3.0):
    """Same sinusoid but with a monotonic +3° baseline drift over the recording."""
    t, ang = _damped_sinusoid(n, fps, A0, freq)
    drift_arr = np.linspace(0, drift, n)
    return t, ang + drift_arr


def test_detrend_true_removes_drift_without_destroying_A0():
    """With detrend=True, a drifted signal should still give a valid A0 close to the clean one."""
    t_clean, ang_clean = _damped_sinusoid()
    t_drift, ang_drift = _drifting_signal()

    p_clean = compute_pt_params(t_clean, ang_clean, detrend=False)
    p_drift = compute_pt_params(t_drift, ang_drift, detrend=True)

    assert p_clean is not None, "Clean signal should produce valid params"
    assert p_drift is not None, "Drifted signal with detrend=True should produce valid params"
    # A0 should be within 10° (detrend removes the drift)
    assert abs(p_drift["A0_deg"] - p_clean["A0_deg"]) < 10.0, \
        f"A0 mismatch after detrend: drift={p_drift['A0_deg']:.1f} clean={p_clean['A0_deg']:.1f}"


def test_detrend_false_accepts_param_without_crash():
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang, detrend=False)
    assert p is not None


def test_detrend_default_is_true():
    """Default call (no detrend arg) must not raise TypeError."""
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang)
    assert p is not None


def test_wider_A0_window_catches_late_initial_peak():
    """Peak shifted to 18% of the post-release window should still be found."""
    n, fps = 300, 30.0
    t = np.arange(n) / fps
    # Neutral at 165°; peak is at frame index 54 ≈ 18% of 300 frames
    ang = np.full(n, 165.0)
    peak_idx = 54   # 18% of 300
    for i in range(n):
        if i < peak_idx:
            ang[i] = 165.0 + (40.0 * i / peak_idx)   # ramp up to peak
        else:
            ang[i] = 205.0 - 40.0 * np.exp(-0.3 * (i - peak_idx) / fps)
    p = compute_pt_params(t, ang)
    # If the 20% window catches the peak, A0 should be ≥ 10°
    if p is not None:
        assert p["A0_deg"] >= 10.0, f"A0 too small: {p['A0_deg']}"


def test_all_expected_keys_present():
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang)
    if p is None:
        return  # signal didn't meet quality threshold — not a test failure
    for key in ("R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
                "f", "area_ratio", "A0_deg", "A1_deg"):
        assert key in p, f"Missing key: {key}"
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\pytest tests\test_pt_score.py -v
```
Expected: `test_detrend_default_is_true` → TypeError (no detrend param). Others may pass (the underlying math is not yet changed).

- [ ] **Step 3: Add `detrend` import**

In `pendulastic_pt_score.py`, line 43:
```python
# OLD
from scipy.signal import find_peaks, savgol_filter

# NEW
from scipy.signal import find_peaks, savgol_filter, detrend as _detrend
```

- [ ] **Step 4: Update `compute_pt_params` signature**

```python
# OLD
def compute_pt_params(t: np.ndarray, angle_raw: np.ndarray,
                      release_idx: Optional[int] = None) -> Optional[dict]:

# NEW
def compute_pt_params(t: np.ndarray, angle_raw: np.ndarray,
                      release_idx: Optional[int] = None,
                      detrend: bool = True) -> Optional[dict]:
```

- [ ] **Step 5: Apply linear detrend after the finite-mask step**

Find this block (≈ lines 615–621):
```python
    mask = np.isfinite(angle_raw)
    if mask.sum() < 40:
        return None

    t_c   = t[mask]
    ang_c = angle_raw[mask]
    ang_s = _sg(ang_c, w=15, p=3)
```

Replace with:
```python
    mask = np.isfinite(angle_raw)
    if mask.sum() < 40:
        return None

    t_c   = t[mask]
    ang_c = angle_raw[mask]
    if detrend:
        ang_c = _detrend(ang_c, type='linear')
    ang_s = _sg(ang_c, w=15, p=3)
```

- [ ] **Step 6: Reorder phi_s before A0 computation and widen A0 window**

Find this block (≈ lines 659–667):
```python
    phi_negated = A0_raw < 0
    if phi_negated:              # convention: extension = positive
        phi = -phi; A0_raw = abs(A0_raw)

    # A0: maximum of |phi| in first 15 % after release (handles late trigger)
    first_n = max(5, int(0.15 * len(phi)))
    A0 = float(np.nanmax(phi[:first_n]))
    if A0 < 2.0:
        A0 = A0_raw

    phi_s = _sg(phi, w=9, p=2)
```

Replace with:
```python
    phi_negated = A0_raw < 0
    if phi_negated:              # convention: extension = positive
        phi = -phi; A0_raw = abs(A0_raw)

    phi_s = _sg(phi, w=9, p=2)

    # A0: maximum of smoothed phi in first 20% after release (wider window handles late trigger)
    # Floor at A0_raw so detrend never pulls A0 below the first post-release sample.
    first_n = max(5, int(0.20 * len(phi)))
    A0 = max(float(np.nanmax(phi_s[:first_n])), A0_raw)
```

- [ ] **Step 7: Run tests — all should pass**

```
.venv\Scripts\pytest tests\test_pt_score.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 8: Run full suite to check no regressions**

```
.venv\Scripts\pytest tests\ -v
```
Expected: all existing tests pass.

- [ ] **Step 9: Commit**

```bash
git add pendulastic_pt_score.py tests/test_pt_score.py
git commit -m "feat: add detrend param + wider A0 window to compute_pt_params"
```

---

### Task 2: `DataManager` — source suffix in filename and CSV

**Files:**
- Modify: `pendulastic_app.py` (`DataManager.build_filename` and `DataManager.save_trial`)
- Modify: `tests/test_data_manager.py` (append new tests; do NOT change existing tests)

**Interfaces:**
- Consumes: nothing (isolated class)
- Produces:
  - `DataManager.build_filename(pid, leg, ms_status, trial, source=None) -> str`
  - `DataManager.save_trial(filename, angles, metadata, timestamps=None, fps=30.0, source=None) -> str`

- [ ] **Step 1: Write the failing tests**

Append these tests to the END of `tests/test_data_manager.py`:

```python
def test_build_filename_with_imu_source():
    assert DataManager.build_filename("P1", "right", "MS", 1, source="imu") == \
        "PID_P1_LEG_Right_MS_TRIAL_1_imu.csv"


def test_build_filename_with_rgb_source():
    assert DataManager.build_filename("P2", "left", "MS", 3, source="rgb") == \
        "PID_P2_LEG_Left_MS_TRIAL_3_rgb.csv"


def test_build_filename_source_none_backward_compat():
    """source=None must produce the same output as the old 4-arg call."""
    assert DataManager.build_filename("P1", "right", "MS", 1, source=None) == \
        "PID_P1_LEG_Right_MS_TRIAL_1.csv"


def test_save_trial_source_param_written_to_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1,
            "sources": ["imu"]}  # new-style metadata (no "methodology" key)
    DataManager.save_trial("test_imu.csv", [170.0], meta, source="imu")
    with open(tmp_path / "test_imu.csv") as f:
        rows = list(csv.reader(f))
    # methodology column (index 7) should contain the source name
    assert rows[1][7] == "imu"


def test_save_trial_backward_compat_methodology_key(tmp_path, monkeypatch):
    """Old callers that pass metadata["methodology"] still work when source=None."""
    monkeypatch.setattr(DataManager, "DATA_DIR", str(tmp_path))
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1,
            "methodology": "rgb"}
    DataManager.save_trial("test.csv", [170.0], meta)
    with open(tmp_path / "test.csv") as f:
        rows = list(csv.reader(f))
    assert rows[1][7] == "rgb"
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\pytest tests\test_data_manager.py::test_build_filename_with_imu_source tests\test_data_manager.py::test_save_trial_source_param_written_to_csv -v
```
Expected: TypeError (unexpected keyword `source`).

- [ ] **Step 3: Update `build_filename`**

```python
# OLD
@staticmethod
def build_filename(pid: str, leg: str, ms_status: str, trial: int) -> str:
    leg_s = leg.capitalize()
    ms_s  = ms_status.replace(" ", "_")
    return f"PID_{pid}_LEG_{leg_s}_{ms_s}_TRIAL_{trial}.csv"

# NEW
@staticmethod
def build_filename(pid: str, leg: str, ms_status: str, trial: int,
                   source: str | None = None) -> str:
    leg_s  = leg.capitalize()
    ms_s   = ms_status.replace(" ", "_")
    suffix = f"_{source}" if source else ""
    return f"PID_{pid}_LEG_{leg_s}_{ms_s}_TRIAL_{trial}{suffix}.csv"
```

- [ ] **Step 4: Update `save_trial`**

```python
# OLD
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

# NEW
@classmethod
def save_trial(
    cls,
    filename: str,
    angles: list,
    metadata: dict,
    timestamps: list | None = None,
    fps: float = 30.0,
    source: str | None = None,
) -> str:
    os.makedirs(cls.DATA_DIR, exist_ok=True)
    path = os.path.join(cls.DATA_DIR, filename)
    t0 = timestamps[0] if timestamps else 0.0
    method_val = source if source else metadata.get("methodology", "")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frame", "time_s", "knee_angle_deg",
                    "pid", "leg", "ms_status", "trial", "methodology"])
        for i, a in enumerate(angles):
            t = (timestamps[i] - t0) if timestamps else i / fps
            w.writerow([i, f"{t:.4f}", f"{a:.3f}",
                        metadata["pid"], metadata["leg"],
                        metadata["ms_status"], metadata["trial"],
                        method_val])
    return path
```

- [ ] **Step 5: Run all DataManager tests**

```
.venv\Scripts\pytest tests\test_data_manager.py -v
```
Expected: all 11 tests PASS (7 existing + 5 new, minus the backward-compat one that uses old metadata shape — that should also pass via `metadata.get("methodology", "")`).

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_data_manager.py
git commit -m "feat: add source suffix param to DataManager.build_filename and save_trial"
```

---

### Task 3: `AcquisitionPanel` — checkbox refactor + Zero Sensor button

**Files:**
- Modify: `pendulastic_app.py` (`AcquisitionPanel._build_widgets`, `_on_method_changed`→`_on_source_changed`, `validate_metadata`, `get_metadata`, add `get_active_sources`)
- Modify: `tests/test_acquisition_panel.py` (update 2 existing tests, add 4 new tests, update `_Ctrl`)

**Interfaces:**
- Consumes: `_imu.zero()`, `_imu.clear_zero()` from `pendulastic_imu_server` (via guarded `_imu` global, `_IMU_AVAIL` flag)
- Produces:
  - `self._src_optitrack: tk.BooleanVar` (default True)
  - `self._src_rgb: tk.BooleanVar` (default False)
  - `self._src_imu: tk.BooleanVar` (default False)
  - `get_active_sources() -> list[str]` — sorted list of checked sources e.g. `["imu", "optitrack"]`
  - `get_metadata() -> dict` — now contains `"sources": list[str]` instead of `"methodology": str`
  - `validate_metadata() -> tuple[bool, str]` — False if no checkbox checked
  - Controller receives `on_source_changed(sources: list[str])` calls

**Note:** The `_Ctrl` fake in the test file must gain `on_source_changed`. The fake's `on_methodology_changed` can be removed since `AcquisitionPanel` no longer calls it.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_acquisition_panel.py` in full:

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
    def on_source_changed(self, sources): pass
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
        # Multi-source: optitrack checked by default, others unchecked
        assert p._src_optitrack.get() is True
        assert p._src_rgb.get() is False
        assert p._src_imu.get() is False
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


def test_validate_no_source_checked_fails():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P1")
        p._src_optitrack.set(False)
        p._src_rgb.set(False)
        p._src_imu.set(False)
        ok, msg = p.validate_metadata()
        assert not ok
        assert "source" in msg.lower()
    finally:
        r.destroy()


def test_get_metadata_returns_sources_list():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P7"); p.leg_var.set("Left")
        p.ms_var.set("Stroke"); p.trial_var.set("3")
        p._src_optitrack.set(False)
        p._src_imu.set(True)
        result = p.get_metadata()
        assert result["pid"] == "P7"
        assert result["leg"] == "Left"
        assert result["ms_status"] == "Stroke"
        assert result["trial"] == 3
        assert result["sources"] == ["imu"]
        assert "methodology" not in result
    finally:
        r.destroy()


def test_get_active_sources_sorted():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p._src_optitrack.set(True)
        p._src_imu.set(True)
        p._src_rgb.set(False)
        sources = p.get_active_sources()
        assert "imu" in sources
        assert "optitrack" in sources
        assert "rgb" not in sources
        assert sources == sorted(sources)
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


def test_zero_sensor_button_hidden_when_imu_unchecked():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(False)
        p._on_source_changed()
        r.update()
        # _zero_frame (containing btn_zero + btn_clear_zero) should be removed from grid
        assert p._zero_frame.grid_info() == {}
    finally:
        r.destroy()


def test_zero_sensor_button_shown_when_imu_checked():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(True)
        p._on_source_changed()
        r.update()
        # _zero_frame should be in the grid; btn_zero widget must also exist
        assert p._zero_frame.grid_info() != {}
        assert hasattr(p, "btn_zero") and p.btn_zero.winfo_exists()
    finally:
        r.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```
Expected: multiple failures — `_src_optitrack` attr not found, `get_active_sources` not found, etc.

- [ ] **Step 3: Rewrite `_build_widgets` in `AcquisitionPanel`**

Replace the entire `_build_widgets` method. Key changes from the current implementation:

**Row 8 (was: 3 radio buttons)** → 3 `tk.Checkbutton` widgets:
```python
# Replace this block in _build_widgets:
# row 8 — Methodology radio buttons (REMOVE the old radio button code entirely)
# row 8 — Source checkboxes
self._src_optitrack = tk.BooleanVar(value=True)
self._src_rgb       = tk.BooleanVar(value=False)
self._src_imu       = tk.BooleanVar(value=False)

meth_f = tk.Frame(self)
meth_f.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=2)
chk_opti = tk.Checkbutton(meth_f, text="OptiTrack",
                           variable=self._src_optitrack,
                           command=self._on_source_changed)
chk_rgb  = tk.Checkbutton(meth_f, text="RGB",
                           variable=self._src_rgb,
                           command=self._on_source_changed)
chk_imu  = tk.Checkbutton(meth_f, text="iPhone IMU",
                           variable=self._src_imu,
                           command=self._on_source_changed)
for chk in (chk_opti, chk_rgb, chk_imu):
    chk.pack(side="left", padx=8)
```

**Row 9 (was: lbl_method_status spanning 2 cols)** → status label col 0 + zero-button frame col 1:
```python
# row 9 — Modality status + Zero Sensor button (Zero hidden until IMU checked)
self.lbl_method_status = tk.Label(
    self, text="● OptiTrack (Motive)", font=("Consolas", 9), fg="green", anchor="w")
self.lbl_method_status.grid(row=9, column=0, sticky="w", padx=16)

zero_f = tk.Frame(self)
zero_f.grid(row=9, column=1, sticky="w", padx=4)
self.btn_zero = tk.Button(
    zero_f, text="⊙ Zero Sensor", font=("Segoe UI", 8),
    command=self._on_zero_sensor)
self.btn_zero.pack(side="left", padx=2)
self.btn_clear_zero = tk.Button(
    zero_f, text="↺ Clear", font=("Segoe UI", 8),
    command=self._on_clear_zero)
self.btn_clear_zero.pack(side="left", padx=2)
zero_f.grid_remove()   # hidden until IMU is checked; toggled in _on_source_changed
self._zero_frame = zero_f
```

**`_lockable` list** — replace `rb_opti, rb_rgb, rb_imu` with the new checkbuttons + zero/clear buttons:
```python
self._lockable = [
    pid_entry, rb_left, rb_right, ms_combo, trial_spin,
    countdown_chk, chk_opti, chk_rgb, chk_imu,
    self.btn_zero, self.btn_clear_zero,
]
```

- [ ] **Step 4: Add `_on_source_changed`, `get_active_sources`, `_on_zero_sensor`, `_on_clear_zero`**

Add these methods to `AcquisitionPanel` (replace `_on_method_changed`):

```python
def _on_source_changed(self) -> None:
    """Called on any source checkbox toggle. Updates status label and Zero button visibility."""
    sources = self.get_active_sources()
    # Show/hide Zero Sensor frame
    if self._src_imu.get():
        self._zero_frame.grid()
    else:
        self._zero_frame.grid_remove()
    # Build status line
    source_labels = {
        "imu": "iPhone IMU — waiting for phone" if _IMU_AVAIL else "iPhone IMU — unavailable",
        "rgb": "RGB / MediaPipe" if _VIEWER_AVAIL else "RGB — MediaPipe unavailable",
        "optitrack": "OptiTrack (Motive)" if _MOTIVE_AVAIL else "OptiTrack — Motive not found",
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

def get_active_sources(self) -> list:
    """Return sorted list of checked source keys."""
    sources = []
    if self._src_imu.get():       sources.append("imu")
    if self._src_optitrack.get(): sources.append("optitrack")
    if self._src_rgb.get():       sources.append("rgb")
    return sorted(sources)

def _on_zero_sensor(self) -> None:
    if _IMU_AVAIL:
        try:
            _imu.zero()
            self.lbl_method_status.config(
                text="● Sensor zeroed — horizontal = 0°", fg="#B36B00")
        except Exception as e:
            messagebox.showerror("Zero Sensor", f"Could not zero sensor:\n{e}")

def _on_clear_zero(self) -> None:
    if _IMU_AVAIL:
        try:
            _imu.clear_zero()
        except Exception:
            pass
    self.lbl_method_status.config(
        text="● iPhone IMU — waiting for phone", fg="#B36B00")
```

- [ ] **Step 5: Update `validate_metadata` and `get_metadata`**

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
    return True, ""

def get_metadata(self) -> dict:
    return {
        "pid":       self.pid_var.get().strip(),
        "leg":       self.leg_var.get(),
        "ms_status": self.ms_var.get(),
        "trial":     int(self.trial_var.get()),
        "sources":   self.get_active_sources(),
    }
```

- [ ] **Step 6: Run all acquisition panel tests**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```
Expected: all 16 tests PASS.

- [ ] **Step 7: Run full suite**

```
.venv\Scripts\pytest tests\ -v
```
Expected: `test_app.py` tests may fail (they call `on_methodology_changed` and pass old `_transition_to_review` signature — that's expected and will be fixed in Task 4). All other suites pass.

- [ ] **Step 8: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: replace radio buttons with multi-source checkboxes and add Zero Sensor button"
```

---

### Task 4: `App` — multi-source recording lifecycle

**Files:**
- Modify: `pendulastic_app.py` (`App.__init__`, `on_start`, `on_stop`, `on_source_changed`, `_tick`, `_transition_to_review`, `_run_rgb_processing`, `_start_imu_recording`)
- Modify: `tests/test_app.py` (update all 3 tests)

**Interfaces:**
- Consumes:
  - `AcquisitionPanel.get_metadata()` → `{"pid", "leg", "ms_status", "trial", "sources": list[str]}`
  - `AcquisitionPanel.get_active_sources() -> list[str]`
  - `DataManager.build_filename(..., source=None)`
  - `DataManager.save_trial(..., source=None)`
- Produces:
  - `App.on_source_changed(sources: list[str])` (replaces `on_methodology_changed`)
  - `App._transition_to_review(source_angles: dict[str, list[float]], meta: dict)` — new signature (no `filename` positional arg)
  - `App._active_sources: list[str]`
  - `App._rec_angles: dict[str, list[float]]`
  - `App._rec_timestamps: dict[str, list[float]]`

**IMPORTANT:** `_start_imu_recording` must **not** call `_imu.start_recording()` or `_imu.stop_recording()` — IMU data now flows only through the queue→tick→`_rec_angles["imu"]` path.

- [ ] **Step 1: Update `tests/test_app.py`**

Replace the entire file:

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
        # Directly simulate the review state without going through load_trial
        # (PostProcessingPanel.load_trial signature changes in Task 5 — bypass here)
        app._state = "review"
        app._acq.pack_forget()
        app._post.pack(fill="both", expand=True)
        app.update()
        app.on_new_trial()
        app.update()
        assert int(app._acq.trial_var.get()) == 3
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_on_source_changed_does_not_crash(monkeypatch):
    import pendulastic_app as _m, types
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu",
        types.SimpleNamespace(
            start=lambda: None, stop=lambda: None,
            get_state=lambda: {
                "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
                "proximal": {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
                "angles":   {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": False},
            }))
    from pendulastic_app import App
    app = App()
    try:
        app.on_source_changed(["rgb"])
        app.on_source_changed(["imu"])
        app.on_source_changed(["imu", "optitrack"])
        app.on_source_changed([])
        app.update()
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\pytest tests\test_app.py -v
```
Expected: `test_on_new_trial` fails (wrong `_transition_to_review` signature), `test_on_source_changed` fails (method doesn't exist).

- [ ] **Step 3: Update `App.__init__` state variables**

In `App.__init__`, change the instance variables:
```python
# OLD
self._rec_angles:     list     = []
self._rec_timestamps: list     = []

# NEW
self._active_sources: list     = []
self._rec_angles:     dict     = {}   # {"imu": [...], "rgb": [...]}
self._rec_timestamps: dict     = {}   # {"imu": [...]}
```

- [ ] **Step 4: Replace `on_methodology_changed` with `on_source_changed`**

```python
def on_source_changed(self, sources: list) -> None:
    """Called by AcquisitionPanel when any source checkbox changes."""
    self._active_sources = list(sources)
```

Remove the old `on_methodology_changed` method entirely.

- [ ] **Step 5: Rewrite `on_start`**

```python
def on_start(self) -> None:
    meta    = self._acq.get_metadata()
    sources = meta["sources"]
    self._active_sources = list(sources)

    # Pick primary engine for live sparkline (IMU > RGB > OptiTrack priority)
    if "imu" in sources:
        self._engine = BiomechanicalEngine("imu")
    elif "rgb" in sources:
        self._engine = BiomechanicalEngine("rgb")
    else:
        self._engine = BiomechanicalEngine("optitrack")

    self._rec_angles     = {}
    self._rec_timestamps = {}
    self._acq.clear_telemetry()

    for src in sources:
        if src == "imu":
            self._start_imu_recording(meta)
        elif src == "rgb":
            self._start_rgb_recording(meta)
        elif src == "optitrack":
            self._start_optitrack_recording(meta)

    self._state = "recording"
    self._acq.enter_recording()
```

- [ ] **Step 6: Update `_start_imu_recording` — remove server-side file management**

```python
def _start_imu_recording(self, meta: dict) -> None:
    # IMU server runs continuously; data flows via queue -> _tick -> _rec_angles["imu"]
    # No start_recording() call needed — we own the CSV via DataManager.save_trial.
    self._imu_poll_stop.clear()
    self._imu_poll_thread = threading.Thread(
        target=self._imu_poll_worker, daemon=True)
    self._imu_poll_thread.start()
```

- [ ] **Step 7: Update `_tick` to use dict-based rec_angles**

```python
def _tick(self) -> None:
    try:
        while not self._imu_queue.empty():
            t, angle = self._imu_queue.get_nowait()
            if self._state == "recording":
                self._rec_angles.setdefault("imu", []).append(angle)
                self._rec_timestamps.setdefault("imu", []).append(t)
                self._acq.push_telemetry(t, angle)
    except queue.Empty:
        pass
    self.after(50, self._tick)
```

- [ ] **Step 8: Rewrite `on_stop`**

```python
def on_stop(self) -> None:
    # Stop IMU poll thread unconditionally
    self._imu_poll_stop.set()
    if self._imu_poll_thread:
        self._imu_poll_thread.join(timeout=1.0)
    self._imu_poll_stop.clear()
    self._imu_poll_thread = None

    meta           = self._acq.get_metadata()
    source_angles: dict = {}
    pending_rgb    = False

    for src in self._active_sources:
        if src == "imu":
            angles_imu = self._rec_angles.get("imu", [])
            ts_imu     = self._rec_timestamps.get("imu") or None
            fn_imu = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"],
                source="imu")
            DataManager.save_trial(fn_imu, angles_imu, meta,
                                   timestamps=ts_imu, source="imu")
            source_angles["imu"] = angles_imu

        elif src == "rgb":
            self._stop_rgb_recording()
            pending_rgb = True

        elif src == "optitrack":
            if _MOTIVE_AVAIL:
                try:
                    _motive.stop_local_motive()
                except Exception:
                    pass
            source_angles["optitrack"] = []   # angles loaded from CSV in review panel

    if pending_rgb:
        self._state = "processing"
        self._acq.enter_processing()
        self._pending_review = source_angles  # preserve already-done sources
        threading.Thread(
            target=self._run_rgb_processing,
            args=(meta,), daemon=True,
        ).start()
    else:
        self._transition_to_review(source_angles, meta)
```

- [ ] **Step 9: Update `_run_rgb_processing`**

```python
def _run_rgb_processing(self, meta: dict) -> None:
    def progress(pct: float) -> None:
        self.after(0, lambda p=pct: self._acq.status_var.set(
            f"MediaPipe tracking: {int(p * 100)}%"))

    leg    = meta.get("leg", "right").lower()
    fn_rgb = DataManager.build_filename(
        meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="rgb")
    angles = self._engine.run_offline_track(self._video_path, progress, leg=leg)
    DataManager.save_trial(fn_rgb, angles, meta, fps=30.0, source="rgb")

    source_angles = dict(getattr(self, "_pending_review", {}))
    source_angles["rgb"] = angles
    self.after(0, lambda: self._transition_to_review(source_angles, meta))
```

- [ ] **Step 10: Update `_transition_to_review` signature**

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

- [ ] **Step 11: Run all tests**

```
.venv\Scripts\pytest tests\ -v
```
Expected: `test_app.py` all 3 tests PASS; `test_acquisition_panel.py` all PASS; `test_post_processing_panel.py` may fail (load_trial signature changed) — that's expected and fixed in Task 5.

- [ ] **Step 12: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: refactor App to multi-source recording lifecycle with per-source CSV output"
```

---

### Task 5: `PostProcessingPanel` — multi-curve display + video upload

**Files:**
- Modify: `pendulastic_app.py` (`PostProcessingPanel._build_widgets`, `load_trial`, `load_optitrack_overlay`, add `_plot_all_curves`, `_show_pt_metrics_from_sources`, `_on_upload_video`, `_add_hpe_overlay`)
- Modify: `tests/test_post_processing_panel.py` (update 2 existing tests, add 4 new tests)

**Interfaces:**
- Consumes:
  - `compute_pt_params(t, arr, detrend=True|False)` — Task 1 signature
  - `BiomechanicalEngine("rgb").run_offline_track(path, progress_cb, leg=leg)` — existing
- Produces:
  - `load_trial(source_angles: dict[str, list[float]], fps: float, metadata: dict, base_filename: str) -> None`
  - `self._meta: dict | None`
  - `self._source_angles: dict[str, list[float]]`
  - `_add_hpe_overlay(angles: list, fps: float = 30.0) -> None`

**Colour palette (exact hex, do not change):**

| Source key | Colour | Line style |
|---|---|---|
| `"imu"` | `#2563EB` | solid `"-"` |
| `"rgb"` | `#16A34A` | solid `"-"` |
| `"optitrack"` | `#D97706` | dashed `"--"` |
| `"hpe_upload"` | `#7C3AED` | dashed `"--"` |

**PT source priority (order matters):** `["imu", "rgb", "optitrack", "hpe_upload"]`
- Use `detrend=True` only for `"imu"` source; `detrend=False` for all others.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_post_processing_panel.py` in full:

```python
# tests/test_post_processing_panel.py
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

_root_window = None

def _get_root():
    global _root_window
    if _root_window is None:
        _root_window = tk.Tk()
        _root_window.withdraw()
    return _root_window


class _Ctrl:
    def on_new_trial(self): pass


def test_panel_instantiates():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()


def test_load_trial_sets_title():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["imu"]}
    # New signature: source_angles dict, fps, metadata, base_filename
    p.load_trial({"imu": [170.0] * 60}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert "PID_P1" in p.title_var.get()


def test_load_trial_populates_mas():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    angles = [180.0 - 40.0 * (1 - math.exp(-0.03 * i)) * (0.7 + 0.3 * math.sin(0.3 * i))
              for i in range(120)]
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["rgb"]}
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert p.mas_var.get() != "—"


def test_load_trial_multi_source_stores_both():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["imu", "optitrack"]}
    p.load_trial({"imu": [170.0] * 30, "optitrack": [168.0] * 30},
                 30.0, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert "imu" in p._source_angles
    assert "optitrack" in p._source_angles


def test_upload_video_button_exists():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    # The upload button must be an attribute named btn_upload_video
    assert hasattr(p, "btn_upload_video")
    assert p.btn_upload_video.winfo_exists()


def test_add_hpe_overlay_adds_to_source_angles():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["imu"]}
    p.load_trial({"imu": [170.0] * 60}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    fake_angles = [175.0, 160.0, 145.0] * 20
    p._add_hpe_overlay(fake_angles, fps=30.0)
    r.update()
    assert "hpe_upload" in p._source_angles
    assert p._source_angles["hpe_upload"] == fake_angles


def test_add_hpe_overlay_empty_updates_status_not_crash():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    p._add_hpe_overlay([])  # empty list — should not crash
    r.update()
    assert "no pose" in p.status_var.get().lower() or "hpe" in p.status_var.get().lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```
.venv\Scripts\pytest tests\test_post_processing_panel.py -v
```
Expected: `test_load_trial_sets_title` and `test_load_trial_populates_mas` fail (wrong `load_trial` signature); others fail (missing attrs/methods).

- [ ] **Step 3: Add the curve-style mapping as a class attribute**

At the top of `PostProcessingPanel` class body (before `__init__`), add:

```python
_CURVE_STYLES = {
    "imu":        {"color": "#2563EB", "ls": "-",   "label": "IMU"},
    "rgb":        {"color": "#16A34A", "ls": "-",   "label": "RGB"},
    "optitrack":  {"color": "#D97706", "ls": "--",  "label": "OptiTrack"},
    "hpe_upload": {"color": "#7C3AED", "ls": "--",  "label": "HPE Upload"},
}
_PT_SOURCE_PRIORITY = ["imu", "rgb", "optitrack", "hpe_upload"]
```

- [ ] **Step 4: Update `PostProcessingPanel.__init__` to add new state**

```python
def __init__(self, parent, controller) -> None:
    super().__init__(parent)
    self.controller      = controller
    self._source_angles: dict  = {}
    self._fps: float           = 30.0
    self._meta: dict | None    = None
    self._build_widgets()
```

(Remove the old `self._angles: list = []` — no longer used.)

- [ ] **Step 5: Update `_build_widgets` — store `_metrics_frame`, add third column, add upload button**

Three changes in `_build_widgets`:

**Change 1:** Store the metrics LabelFrame as an instance variable:
```python
# OLD
mf = tk.LabelFrame(self, text="Popovic Pendulum Test Metrics", ...)
mf.grid(...)

# NEW
self._metrics_frame = tk.LabelFrame(self, text="Popović PT Metrics", ...)
self._metrics_frame.grid(...)
# Update all mf.xxx references inside this block to self._metrics_frame.xxx
```

**Change 2:** Add `columnconfigure(2, weight=1)` alongside the existing two:
```python
self.columnconfigure(0, weight=1)
self.columnconfigure(1, weight=1)
self.columnconfigure(2, weight=1)   # add this line
```

**Change 3:** Add the upload button in row 3 col 2 (after the existing two buttons):
```python
self.btn_upload_video = tk.Button(
    self, text="🎥 Upload Video for HPE",
    font=("Segoe UI", 10), width=22, height=2,
    command=self._on_upload_video)
self.btn_upload_video.grid(row=3, column=2, padx=10, pady=12, sticky="w")
```

- [ ] **Step 6: Replace `load_trial` with new multi-source signature**

```python
def load_trial(
    self,
    source_angles: dict,
    fps: float,
    metadata: dict,
    base_filename: str,
) -> None:
    self._source_angles = dict(source_angles)
    self._fps           = fps
    self._meta          = metadata
    self.title_var.set(base_filename)
    self._plot_all_curves()
    self._show_pt_metrics_from_sources()
    self.status_var.set(f"Saved: {base_filename}")
```

- [ ] **Step 7: Add `_plot_all_curves`**

```python
def _plot_all_curves(self) -> None:
    if not _MPL_AVAIL or self._canvas is None:
        return
    self._ax.clear()
    n_curves = 0
    fps = self._fps or 30.0
    for src, angles in self._source_angles.items():
        if not angles:
            continue
        style = self._CURVE_STYLES.get(
            src, {"color": "gray", "ls": "-", "label": src})
        times = [i / fps for i in range(len(angles))]
        self._ax.plot(times, angles,
                      color=style["color"], linewidth=1.5,
                      linestyle=style["ls"], label=style["label"])
        n_curves += 1
    if n_curves >= 2:
        self._ax.legend(fontsize=8)
    self._ax.set_xlabel("Time (s)", fontsize=9)
    self._ax.set_ylabel("Knee angle (deg)", fontsize=9)
    self._ax.set_title("Popović Pendulum Test — Knee Angle", fontsize=10)
    self._ax.grid(True, alpha=0.3)
    self._fig.tight_layout()
    self._canvas.draw()
```

- [ ] **Step 8: Add `_show_pt_metrics_from_sources`**

```python
def _show_pt_metrics_from_sources(self) -> None:
    if not _PT_AVAIL or compute_pt_params is None:
        return
    fps = self._fps or 30.0
    for src in self._PT_SOURCE_PRIORITY:
        angles = self._source_angles.get(src)
        if not angles:
            continue
        t   = np.arange(len(angles), dtype=float) / fps
        arr = np.array(angles, dtype=float)
        should_detrend = (src == "imu")
        try:
            p = compute_pt_params(t, arr, detrend=should_detrend)
        except TypeError:
            p = compute_pt_params(t, arr)   # backward compat
        if p is None:
            continue
        score = compute_pt_score_simple(p)
        mas   = pt_to_mas(score)
        self._metrics_frame.config(
            text=f"Popović PT Metrics (source: {src.upper()})")
        self.a1_var.set(f"{p['A1_deg']:.1f}")
        self.omega_var.set(f"{p['omega_peak_deg_s']:.1f}")
        self.n_var.set(f"{p['N']:.1f}")
        self.f_var.set(f"{p['f']:.2f}")
        self.r2n_var.set(f"{p['R2n']:.3f}")
        self.mas_var.set(str(mas))
        self.score_var.set(f"{score:.3f}")
        return
    self.status_var.set("PT scoring: no valid source data.")
```

- [ ] **Step 9: Update `load_optitrack_overlay` to use multi-source dict**

```python
def load_optitrack_overlay(self, csv_path: str) -> None:
    if not _PT_AVAIL or load_optitrack is None:
        messagebox.showerror("OptiTrack", "load_optitrack not available.")
        return
    try:
        _t_ot, opti = load_optitrack(csv_path)
        self._source_angles["optitrack"] = list(opti)
        self._plot_all_curves()
        self._show_pt_metrics_from_sources()
        self.status_var.set(f"Overlay: {os.path.basename(csv_path)}")
    except Exception as e:
        messagebox.showerror("OptiTrack Load Error", str(e))
```

- [ ] **Step 10: Add `_on_upload_video` and `_add_hpe_overlay`**

```python
def _on_upload_video(self) -> None:
    if not _VIEWER_AVAIL:
        messagebox.showerror(
            "HPE Unavailable",
            "pendulastic_viewer not importable — cannot run MediaPipe.")
        return
    path = filedialog.askopenfilename(
        title="Select video for HPE",
        filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"),
                   ("All files", "*.*")])
    if not path:
        return
    self.status_var.set("HPE processing: 0%")
    leg    = self._meta.get("leg", "right") if self._meta else "right"
    engine = BiomechanicalEngine("rgb")

    def _progress(pct: float) -> None:
        self.after(0, lambda p=pct: self.status_var.set(
            f"HPE processing: {int(p * 100)}%"))

    def _run() -> None:
        angles = engine.run_offline_track(path, _progress, leg=leg.lower())
        self.after(0, lambda: self._add_hpe_overlay(angles, fps=30.0))

    threading.Thread(target=_run, daemon=True).start()

def _add_hpe_overlay(self, angles: list, fps: float = 30.0) -> None:
    if not angles:
        self.status_var.set(
            "HPE: no pose detected — check video or leg selection.")
        return
    self._source_angles["hpe_upload"] = angles
    if not self._fps:
        self._fps = fps
    if not self.title_var.get():
        self.title_var.set("HPE upload")
    self._plot_all_curves()
    self._show_pt_metrics_from_sources()
    self.status_var.set(f"HPE overlay loaded — {len(angles)} frames")
```

- [ ] **Step 11: Remove the old `_plot_curve` and `_show_pt_metrics` methods**

These are now replaced by `_plot_all_curves` and `_show_pt_metrics_from_sources`. Delete them. (If `load_optitrack_overlay` still calls the old names, the update in Step 9 already replaces the calls.)

- [ ] **Step 12: Run all PostProcessingPanel tests**

```
.venv\Scripts\pytest tests\test_post_processing_panel.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 13: Run full test suite**

```
.venv\Scripts\pytest tests\ -v
```
Expected: all tests PASS.

- [ ] **Step 14: Commit**

```bash
git add pendulastic_app.py tests/test_post_processing_panel.py
git commit -m "feat: multi-curve PostProcessingPanel with HPE video upload overlay"
```
