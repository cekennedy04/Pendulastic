# Workbench Raw-Sensor PT-Score Cross-Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two supplementary, non-blocking diagnostic cross-checks — peak raw gyro
angular velocity, and an independent release-event time estimate from raw accel —
computed directly from raw sensor data (bypassing AHRS fusion), and display them in the
Workbench's comparison view alongside (never replacing) the existing fused-angle
Popović PT-score.

**Architecture:** New pure functions in `workbench_engine.py` reuse the existing
split-CSV sibling-derivation/reading helpers to get raw gyro/accel samples directly,
independent of `load_imu_trial`'s fusion path. `App.on_load_trial()` calls the new
top-level function in its own isolated try/except (never blocking the primary trial
load), and `WorkbenchView` renders the result as an additional section in its existing
metrics readout.

**Tech Stack:** Python, NumPy, SciPy (`scipy.signal.butter`/`filtfilt`), pytest.

## Global Constraints

- `load_imu_trial()`, `replay_trial()`, and the existing fused-angle Popović PT-score
  computation must not change — this feature is purely additive.
- Peak gyro velocity is a `max()` over the whole raw gyro stream — no active-window
  masking (a max is not distorted by a resting tail the way an integral/mean would be).
- The accel release-time filter's cutoff must be normalized against the **actual
  effective sample rate computed from that file's own timestamps**
  (`fs_eff = 1.0 / median(diff(t))`), never a hardcoded assumption.
- `_MIN_FS_FOR_5HZ_CUTOFF_HZ = 20.0`: if `fs_eff` is below this, return `None` rather
  than design an invalid/misleading filter.
- The accel release-time threshold uses **only** the percentile-based adaptive
  component (`0.08 * signal_range`) — no fixed absolute floor (unlike
  `pendulastic_pt_score._detect_release`'s `max(2.0, ...)`, which is calibrated for
  degree-scale angle signals and doesn't generalize to accel-magnitude data in
  unspecified units).
- A failure computing either cross-check must never block or interfere with loading
  the primary IMU trace / fused PT score.
- Full spec: `docs/superpowers/specs/2026-08-03-workbench-raw-sensor-crosschecks-design.md`.

---

### Task 1: `_peak_raw_gyro_velocity`

**Files:**
- Modify: `workbench_engine.py` (new function, near the split-CSV helpers)
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `_derive_split_csv_siblings`, `_read_one_split_csv` (existing)
- Produces: `_peak_raw_gyro_velocity(anchor_path: str) -> float`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_workbench_engine.py`:

```python
def test_peak_raw_gyro_velocity_finds_known_burst(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_split_csv(str(prefix) + "_gyro.csv", [
        (0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
        (10.0, 10, "proximal", "Gyroscope", 3.0, 4.0, 0.0),   # magnitude 5.0
        (20.0, 20, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
    ])
    _write_split_csv(str(prefix) + "_accel.csv", [
        (0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 1.0),
        (20.0, 20, "proximal", "Accelerometer", 0.0, 0.0, 1.0),
    ])
    _write_split_csv(str(prefix) + "_mag.csv", [
        (0.0, 0, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (20.0, 20, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])

    peak = engine._peak_raw_gyro_velocity(str(prefix) + "_gyro.csv")
    assert peak == 5.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k peak_raw_gyro_velocity -v`
Expected: FAIL — `AttributeError: module 'workbench_engine' has no attribute '_peak_raw_gyro_velocity'`

- [ ] **Step 3: Add the function**

In `workbench_engine.py`, add near `_read_split_csv_samples`:

```python
def _peak_raw_gyro_velocity(anchor_path: str) -> float:
    """Maximum raw gyro vector magnitude over the whole trial -- no AHRS
    fusion, no differentiation of a filtered signal. A simple max() is not
    distorted by a long resting tail the way an integral/mean would be
    (see _active_window_end's own rationale), so no active-window masking
    is needed here."""
    paths = _derive_split_csv_siblings(anchor_path)
    samples = _read_one_split_csv(paths["gyro"], "gyro")
    magnitudes = [math.sqrt(s["v"][0] ** 2 + s["v"][1] ** 2 + s["v"][2] ** 2)
                 for s in samples]
    return max(magnitudes)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k peak_raw_gyro_velocity -v`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add _peak_raw_gyro_velocity raw-sensor cross-check"
```

---

### Task 2: `_accel_release_time` and `compute_raw_sensor_diagnostics`

**Files:**
- Modify: `workbench_engine.py` (new imports, new functions)
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `_derive_split_csv_siblings`, `_read_one_split_csv` (existing),
  `_peak_raw_gyro_velocity` (Task 1)
- Produces: `_accel_release_time(anchor_path: str) -> Optional[float]`,
  `compute_raw_sensor_diagnostics(anchor_path: str) -> dict` — the public entrypoint
  Task 3 calls, returning
  `{"peak_gyro_velocity_dps": float, "accel_release_time_sec": Optional[float]}`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def _write_accel_release_csv(path, fs_hz, baseline=1.0, step=1.5,
                             release_t_sec=1.0, duration_sec=3.0):
    """Synthetic single-axis accel signal (y=z=0, so magnitude == |x|):
    steady baseline until release_t_sec, then steps to a new level and
    stays there -- a clean, unambiguous magnitude change for the release
    detector to find. Verified empirically before writing this plan: this
    exact shape detects within ~0.1s of release_t_sec at 50/100/200 Hz."""
    dt = 1.0 / fs_hz
    n = int(duration_sec / dt)
    rows = []
    for i in range(n):
        t_ms = i * dt * 1000.0
        x = step if (i * dt) >= release_t_sec else baseline
        rows.append((t_ms, int(t_ms), "proximal", "Accelerometer", x, 0.0, 0.0))
    _write_split_csv(path, rows)


def test_accel_release_time_detects_known_step(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=100.0, release_t_sec=1.0)
    _write_split_csv(str(prefix) + "_gyro.csv", [
        (0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
        (2900.0, 2900, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
    ])
    _write_split_csv(str(prefix) + "_mag.csv", [
        (0.0, 0, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (2900.0, 2900, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])

    release_t = engine._accel_release_time(str(prefix) + "_accel.csv")
    assert release_t is not None
    assert abs(release_t - 1.0) < 0.15


def test_accel_release_time_adapts_to_actual_sample_rate(tmp_path):
    """Same release shape at two different sample rates -- both must detect
    near the same known release time, proving the filter design adapts to
    each file's own fs_eff rather than assuming one fixed rate."""
    for fs_hz in (50.0, 200.0):
        prefix = tmp_path / f"Trial_{int(fs_hz)}"
        _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=fs_hz, release_t_sec=1.0)
        release_t = engine._accel_release_time(str(prefix) + "_accel.csv")
        assert release_t is not None
        assert abs(release_t - 1.0) < 0.15, f"fs_hz={fs_hz}: got {release_t}"


def test_accel_release_time_returns_none_below_nyquist_guard(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=5.0, release_t_sec=1.0)
    release_t = engine._accel_release_time(str(prefix) + "_accel.csv")
    assert release_t is None


def test_compute_raw_sensor_diagnostics_returns_both_keys(tmp_path):
    prefix = tmp_path / "Trial_1"
    _write_accel_release_csv(str(prefix) + "_accel.csv", fs_hz=100.0, release_t_sec=1.0)
    _write_split_csv(str(prefix) + "_gyro.csv", [
        (0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
        (1000.0, 1000, "proximal", "Gyroscope", 3.0, 4.0, 0.0),
        (2900.0, 2900, "proximal", "Gyroscope", 0.0, 0.0, 0.0),
    ])
    _write_split_csv(str(prefix) + "_mag.csv", [
        (0.0, 0, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (2900.0, 2900, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])

    diagnostics = engine.compute_raw_sensor_diagnostics(str(prefix) + "_imu.csv")
    assert diagnostics["peak_gyro_velocity_dps"] == 5.0
    assert diagnostics["accel_release_time_sec"] is not None
    assert abs(diagnostics["accel_release_time_sec"] - 1.0) < 0.15
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k "accel_release_time or compute_raw_sensor_diagnostics" -v`
Expected: FAIL — `AttributeError: module 'workbench_engine' has no attribute '_accel_release_time'`

- [ ] **Step 3: Add the scipy imports and the two functions**

In `workbench_engine.py`, change the existing scipy import line:

```python
# OLD
from scipy.signal import find_peaks, savgol_filter

# NEW
from scipy.signal import find_peaks, savgol_filter, butter, filtfilt
```

Add these near `_peak_raw_gyro_velocity` (Task 1):

```python
_MIN_FS_FOR_5HZ_CUTOFF_HZ = 20.0
_ACCEL_LOWPASS_CUTOFF_HZ = 5.0
_ACCEL_RELEASE_BASELINE_SEC = 0.6


def _accel_release_time(anchor_path: str) -> Optional[float]:
    """Independent release-event estimate from raw accelerometer
    magnitude, low-pass filtered to separate genuine limb-drop change from
    linear-acceleration noise (muscle twitches, sensor jolts). Returns
    None if the file's actual sample rate can't support a meaningful 5 Hz
    cutoff, or if no release is ever detected -- never fabricates a value
    from data that can't support it."""
    paths = _derive_split_csv_siblings(anchor_path)
    samples = _read_one_split_csv(paths["accel"], "accel")
    t = np.array([s["t"] for s in samples], dtype=float)
    mag = np.array([math.sqrt(s["v"][0] ** 2 + s["v"][1] ** 2 + s["v"][2] ** 2)
                    for s in samples])

    if len(t) < 2:
        return None
    fs_eff = 1.0 / float(np.median(np.diff(t)))
    if fs_eff < _MIN_FS_FOR_5HZ_CUTOFF_HZ:
        return None

    b, a = butter(4, _ACCEL_LOWPASS_CUTOFF_HZ, btype="low", fs=fs_eff)
    filtered = filtfilt(b, a, mag)

    bi = max(3, int(np.searchsorted(t, t[0] + _ACCEL_RELEASE_BASELINE_SEC)))
    bi = min(bi, len(t) - 1)
    baseline = float(np.median(filtered[:bi]))
    signal_range = float(np.percentile(filtered, 97) - np.percentile(filtered, 3))
    thresh = 0.08 * signal_range
    for i in range(bi, len(t)):
        if abs(filtered[i] - baseline) > thresh:
            return float(t[max(0, i - 2)])
    return None


def compute_raw_sensor_diagnostics(anchor_path: str) -> dict:
    """Two supplementary, non-blocking cross-checks computed directly from
    raw gyro/accel data (bypassing AHRS fusion entirely) -- see design
    spec Sections 3-4. Never touches load_imu_trial's fused-angle
    PT-score path."""
    return {
        "peak_gyro_velocity_dps": _peak_raw_gyro_velocity(anchor_path),
        "accel_release_time_sec": _accel_release_time(anchor_path),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k "accel_release_time or compute_raw_sensor_diagnostics" -v`
Expected: 4 passed

- [ ] **Step 5: Run the full engine test suite to confirm no regressions**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add _accel_release_time and compute_raw_sensor_diagnostics"
```

---

### Task 3: Wire non-blocking diagnostics into `App.on_load_trial`

**Files:**
- Modify: `pendulastic_app.py` (`App.__init__`, `App.on_load_trial`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `_wb_engine.compute_raw_sensor_diagnostics` (Task 2)
- Produces: `App._workbench_raw_diagnostics: Optional[dict]`, populated by
  `on_load_trial` — Task 4 reads this and passes it to `WorkbenchView`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_on_load_trial_populates_raw_diagnostics_when_imu_selected(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0]), np.array([180.0]))),
        "compute_raw_sensor_diagnostics": staticmethod(
            lambda path: {"peak_gyro_velocity_dps": 42.0, "accel_release_time_sec": 1.23}),
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.on_load_trial({
            "imu_path": str(tmp_path / "trial.jsonl"), "video_path": None,
            "optitrack_path": None, "models": [],
            "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert app._workbench_raw_diagnostics == {
            "peak_gyro_velocity_dps": 42.0, "accel_release_time_sec": 1.23}
    finally:
        app.destroy()


def test_on_load_trial_raw_diagnostics_failure_does_not_block_trial_load(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)

    def raise_error(path):
        raise ValueError("synthetic failure")

    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0]), np.array([180.0]))),
        "compute_raw_sensor_diagnostics": staticmethod(raise_error),
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.on_load_trial({
            "imu_path": str(tmp_path / "trial.jsonl"), "video_path": None,
            "optitrack_path": None, "models": [],
            "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert app._workbench_view.winfo_ismapped()
        assert "imu" in app._workbench_view._traces
        assert app._workbench_raw_diagnostics is None
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_app.py -k "populates_raw_diagnostics or raw_diagnostics_failure" -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute '_workbench_raw_diagnostics'`

- [ ] **Step 3: Initialize the attribute in `App.__init__`**

In `pendulastic_app.py`, in `App.__init__`, change:

```python
# OLD
        self._workbench_trial_meta: dict = {}
        self._workbench_status_var = tk.StringVar(value="")

# NEW
        self._workbench_trial_meta: dict = {}
        self._workbench_raw_diagnostics: Optional[dict] = None
        self._workbench_status_var = tk.StringVar(value="")
```

- [ ] **Step 4: Populate it in `on_load_trial`**

In `pendulastic_app.py`, in `on_load_trial`, change the IMU-loading block:

```python
# OLD
        if selection["imu_path"]:
            ft_ratio = None
            method_override = None
            if selection["femur_length_cm"] and selection["tibia_length_cm"]:
                ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
                method_override = "ockendon_flipped"
            try:
                t, angle = _wb_engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

# NEW
        self._workbench_raw_diagnostics = None
        if selection["imu_path"]:
            ft_ratio = None
            method_override = None
            if selection["femur_length_cm"] and selection["tibia_length_cm"]:
                ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
                method_override = "ockendon_flipped"
            try:
                t, angle = _wb_engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

            try:
                self._workbench_raw_diagnostics = _wb_engine.compute_raw_sensor_diagnostics(
                    selection["imu_path"])
            except Exception:
                pass   # supplementary cross-check only -- never blocks the trial load
```

(This is a separate try/except from the existing IMU-load call, per the Global
Constraints: a cross-check failure produces no dialog and no error, it simply leaves
`_workbench_raw_diagnostics` as `None`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_app.py -k "populates_raw_diagnostics or raw_diagnostics_failure" -v`
Expected: 2 passed

- [ ] **Step 6: Run the full `test_app.py` suite to confirm no regressions**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_app.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: wire non-blocking raw-sensor diagnostics into on_load_trial"
```

---

### Task 4: Display the cross-checks in `WorkbenchView`

**Files:**
- Modify: `pendulastic_app.py` (`App.on_load_trial`, to pass diagnostics to the view)
- Modify: `pendulastic_workbench.py` (`WorkbenchView.__init__`, new
  `WorkbenchView.set_raw_diagnostics`, `WorkbenchView._recompute_metrics`)
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: `App._workbench_raw_diagnostics` (Task 3)
- Produces: `WorkbenchView.set_raw_diagnostics(diagnostics: Optional[dict]) -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pendulastic_workbench.py`:

```python
def test_set_raw_diagnostics_renders_both_values():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv.set_raw_diagnostics({"peak_gyro_velocity_dps": 245.3, "accel_release_time_sec": 1.02})
    r.update()
    text = wv._metrics_text.get("1.0", "end")
    assert "Raw Sensor Cross-Checks" in text
    assert "245.3" in text
    assert "1.02" in text


def test_set_raw_diagnostics_shows_unavailable_when_release_time_is_none():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv.set_raw_diagnostics({"peak_gyro_velocity_dps": 245.3, "accel_release_time_sec": None})
    r.update()
    text = wv._metrics_text.get("1.0", "end")
    assert "unavailable" in text.lower()


def test_set_raw_diagnostics_none_omits_section():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv.set_raw_diagnostics(None)
    r.update()
    text = wv._metrics_text.get("1.0", "end")
    assert "Raw Sensor Cross-Checks" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_pendulastic_workbench.py -k raw_diagnostics -v`
Expected: FAIL — `AttributeError: 'WorkbenchView' object has no attribute 'set_raw_diagnostics'`

- [ ] **Step 3: Initialize the attribute in `WorkbenchView.__init__`**

In `pendulastic_workbench.py`, in `WorkbenchView.__init__`, change:

```python
# OLD
        self._annotations: dict = {}     # {label: (frame_index, t_sec)}
        self._pending_milestone = tk.StringVar(value=MILESTONE_LABELS[0])
        self._build_widgets()

# NEW
        self._annotations: dict = {}     # {label: (frame_index, t_sec)}
        self._pending_milestone = tk.StringVar(value=MILESTONE_LABELS[0])
        self._raw_diagnostics: Optional[dict] = None
        self._build_widgets()
```

- [ ] **Step 4: Add `set_raw_diagnostics` and update `_recompute_metrics`**

In `pendulastic_workbench.py`, add near `get_annotations`:

```python
    def set_raw_diagnostics(self, diagnostics: Optional[dict]) -> None:
        self._raw_diagnostics = diagnostics
        self._recompute_metrics()
```

In `pendulastic_workbench.py`, change `_recompute_metrics`'s tail:

```python
# OLD
        for label, result in snapshot["vs_reference"].items():
            if result["status"] == "ok":
                jitter_str = (f"{result['timing_offset_sec']:.3f}s"
                             if result["timing_offset_sec"] is not None else "n/a")
                line = (f"{label} vs {ref_label}: RMSE={result['rmse_deg']:.1f} deg  "
                       f"MAE={result['mae_deg']:.1f} deg  lag={result['lag_sec']:.2f}s  "
                       f"jitter={jitter_str}\n")
            else:
                line = f"{label} vs {ref_label}: {result['error']}\n"
            self._metrics_text.insert("end", line)
        self._metrics_text.configure(state="disabled")

# NEW
        for label, result in snapshot["vs_reference"].items():
            if result["status"] == "ok":
                jitter_str = (f"{result['timing_offset_sec']:.3f}s"
                             if result["timing_offset_sec"] is not None else "n/a")
                line = (f"{label} vs {ref_label}: RMSE={result['rmse_deg']:.1f} deg  "
                       f"MAE={result['mae_deg']:.1f} deg  lag={result['lag_sec']:.2f}s  "
                       f"jitter={jitter_str}\n")
            else:
                line = f"{label} vs {ref_label}: {result['error']}\n"
            self._metrics_text.insert("end", line)

        if self._raw_diagnostics is not None:
            self._metrics_text.insert(
                "end", "\nRaw Sensor Cross-Checks (independent of PT score fusion):\n")
            peak_vel = self._raw_diagnostics["peak_gyro_velocity_dps"]
            self._metrics_text.insert(
                "end", f"  Peak angular velocity (raw gyro): {peak_vel:.1f} deg/s\n")
            release_t = self._raw_diagnostics["accel_release_time_sec"]
            if release_t is not None:
                self._metrics_text.insert(
                    "end", f"  Release detected (raw accel, 5Hz low-pass): t={release_t:.2f}s\n")
            else:
                self._metrics_text.insert(
                    "end", "  Release detected (raw accel, 5Hz low-pass): "
                          "unavailable (sample rate too low)\n")

        self._metrics_text.configure(state="disabled")
```

(Only the trailing portion of `_recompute_metrics` is shown modified — everything
before the `for label, result in snapshot["vs_reference"].items():` loop is unchanged.)

- [ ] **Step 5: Wire `App.on_load_trial` to pass diagnostics to the view**

In `pendulastic_app.py`, in `on_load_trial`, change:

```python
# OLD
        self._workbench_load.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)

# NEW
        self._workbench_load.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)
        self._workbench_view.set_raw_diagnostics(self._workbench_raw_diagnostics)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_pendulastic_workbench.py -k raw_diagnostics -v`
Expected: 3 passed

- [ ] **Step 7: Run the full Workbench test suite to confirm no regressions**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_pendulastic_workbench.py tests\test_workbench_engine.py tests\test_app.py -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add pendulastic_app.py pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: display raw-sensor cross-checks in the Workbench metrics readout"
```

---

### Task 5: Full regression run

**Files:**
- None modified — verification only

- [ ] **Step 1: Run the full test suite**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\ -v --ignore=tests\test_metrics.py --ignore=tests\test_pose.py --ignore=tests\test_stats.py --ignore=tests\test_video.py`

Expected: all pass. If the known pre-existing tkinter-singleton flake appears when run
alongside other files, re-run the specific failing test(s) individually to confirm it's
the pre-existing flake and not a real regression (documented in this repo's other
plans — a different test fails each run, never the same one twice, and always passes
in isolation).

- [ ] **Step 2: Manual acceptance step (not automatable here)**

Load a real split-CSV trial through the Workbench (e.g. one of the
`Participant_13_left` trials) with the IMU source selected, and confirm the "Raw
Sensor Cross-Checks" section appears in the metrics readout with a plausible peak
angular velocity and release time. This requires an interactive Tkinter session and is
out of scope for the automated test suite — flag it as the remaining manual
verification step.

- [ ] **Step 3: Commit (only if Step 1 required any fixes)**

```bash
git add -A
git commit -m "test: fix regressions found in full-suite run"
```

---
