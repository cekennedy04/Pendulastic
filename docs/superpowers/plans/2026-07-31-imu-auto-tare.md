# IMU Countdown Auto-Tare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the manual "Zero Sensor"/"Clear Zero" buttons with automatic, continuously
re-triggering calibration during the existing 5-second pre-recording countdown, closing the
silent-NaN-or-stale-calibration gap where a trial recorded without ever zeroing either produces an
all-`nan` CSV or silently inherits an unrelated earlier trial's stale reference.

**Architecture:** `App._tick()` (already running every 50ms) gains a rolling pitch/roll buffer that
detects a stable hold and calls `pendulastic_imu_server.zero()` on each new stable window, active
only while `AcquisitionPanel`'s countdown is running. `AcquisitionPanel._tick_countdown()` gains an
extend-then-confirm fallback for the case where the sensor never settles. No changes to
`pendulastic_imu_server.py`.

**Tech Stack:** Python 3.13, Tkinter (existing), pytest.

## Global Constraints

- `_CALIB_STABILITY_RANGE_DEG = 2.0` — max peak-to-peak pitch/roll swing over the trailing buffer to
  count as "stable." Both pitch and roll must independently be under this, not just one.
- `_CALIB_BUFFER_SAMPLES = 20` — trailing window size at the existing 50ms `_tick()` cadence (~1s).
- `_MAX_CALIB_EXTENSION_S = 5` — hard cap on extra countdown seconds beyond the base 5, before the
  confirmation dialog appears.
- Auto-tare is edge-triggered: `pendulastic_imu_server.zero()` fires only on the tick where
  stability transitions from not-stable to stable, never on every tick while already stable.
- `is_imu_calibrated()` must return `True` trivially whenever `"imu"` is not an active source — the
  entire feature must be invisible to RGB/OptiTrack-only trials.
- No changes to `pendulastic_imu_server.py`'s `zero()`/`clear_zero()`/flex-axis-capture
  implementation — reused exactly as-is.
- Full spec: `docs/superpowers/specs/2026-07-31-imu-auto-tare-design.md`.

---

### Task 1: Calibration state + `on_countdown_start()` hook

**Files:**
- Modify: `pendulastic_app.py:97` (module constants), `pendulastic_app.py:1113-1114` (`App.__init__`),
  `pendulastic_app.py:593-598` (`AcquisitionPanel._start_countdown`)
- Test: `tests/test_app.py`, `tests/test_acquisition_panel.py`

**Interfaces:**
- Produces: `App.on_countdown_start() -> None`, `App._calib_buffer: list`,
  `App._calib_was_stable: bool`, `App._calib_ever_stable: bool`,
  `AcquisitionPanel._calib_extension_s: int`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_on_countdown_start_resets_calibration_state():
    from pendulastic_app import App
    app = App()
    try:
        app._calib_buffer = [(1.0, 2.0)]
        app._calib_was_stable = True
        app._calib_ever_stable = True
        app.on_countdown_start()
        assert app._calib_buffer == []
        assert app._calib_was_stable is False
        assert app._calib_ever_stable is False
    finally:
        app.destroy()
```

Add to `tests/test_acquisition_panel.py` (the shared `_Ctrl` fake needs the two new controller
methods so every existing test in this file keeps working once `AcquisitionPanel` starts calling
them — add both as no-op/default-true stubs directly on `_Ctrl`):

```python
class _Ctrl:
    """Minimal fake controller."""
    def on_start(self): pass
    def on_stop(self): pass
    def on_source_changed(self, sources): pass
    def on_new_trial(self): pass
    def on_back_to_mode_select(self): pass
    def on_countdown_start(self): pass
    def is_imu_calibrated(self): return True
```

```python
def test_start_countdown_calls_on_countdown_start():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def on_countdown_start(self): calls.append("countdown_start")
        p = AcquisitionPanel(r, C()); p.pack()
        p.pid_var.set("P1")
        p.countdown_var.set(True)
        p._on_start_clicked()
        r.update()
        assert "countdown_start" in calls
        assert p._calib_extension_s == 0
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_app.py::test_on_countdown_start_resets_calibration_state tests\test_acquisition_panel.py::test_start_countdown_calls_on_countdown_start -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'on_countdown_start'` (and the
`AcquisitionPanel` test fails the same way once `_Ctrl` gains the new methods but `_start_countdown`
doesn't call them yet — `assert "countdown_start" in calls` fails with an empty list)

- [ ] **Step 3: Add the new module constants**

In `pendulastic_app.py`, change:

```python
# OLD
_GREEN = "#1e7d34"
_RED   = "#a31515"
_BLUE  = "#1f3a93"
_AMBER = "#c07000"

# NEW
_GREEN = "#1e7d34"
_RED   = "#a31515"
_BLUE  = "#1f3a93"
_AMBER = "#c07000"

_CALIB_STABILITY_RANGE_DEG = 2.0   # max peak-to-peak pitch/roll swing to count as "stable"
_CALIB_BUFFER_SAMPLES = 20         # ~1s of samples at the 50ms _tick() cadence
_MAX_CALIB_EXTENSION_S = 5         # extra seconds beyond the base 5s countdown before asking
```

- [ ] **Step 4: Add calibration state to `App.__init__`**

In `pendulastic_app.py`, change:

```python
# OLD
        self._preview_queue:  queue.Queue = queue.Queue(maxsize=1)
        self._pose_estimator               = None

# NEW
        self._preview_queue:  queue.Queue = queue.Queue(maxsize=1)
        self._pose_estimator               = None
        self._calib_buffer:      list = []     # trailing (pitch, roll) samples during countdown
        self._calib_was_stable:  bool = False   # edge-trigger state for auto-tare
        self._calib_ever_stable: bool = False   # True once calibrated this countdown
```

- [ ] **Step 5: Add `App.on_countdown_start()`**

Add this method to the `App` class, near `on_source_changed`:

```python
    def on_countdown_start(self) -> None:
        """Called by AcquisitionPanel at the start of each countdown; resets
        the auto-tare stability tracking for this fresh countdown window."""
        self._calib_buffer = []
        self._calib_was_stable = False
        self._calib_ever_stable = False
```

- [ ] **Step 6: Wire `_start_countdown()` to call the new hook**

In `pendulastic_app.py`, change:

```python
# OLD
    def _start_countdown(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="CANCEL",
                              command=self._cancel_countdown, bg=_AMBER)
        self.btn_stop.config(state="disabled")
        self._tick_countdown(5)

# NEW
    def _start_countdown(self) -> None:
        self.controller.on_countdown_start()
        self._calib_extension_s = 0
        self._lock_form(True)
        self.btn_start.config(text="CANCEL",
                              command=self._cancel_countdown, bg=_AMBER)
        self.btn_stop.config(state="disabled")
        self._tick_countdown(5)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_app.py::test_on_countdown_start_resets_calibration_state tests\test_acquisition_panel.py::test_start_countdown_calls_on_countdown_start -v`
Expected: both PASS

- [ ] **Step 8: Run the full `test_acquisition_panel.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_acquisition_panel.py -v`
Expected: all pass (the `_Ctrl` update in Step 1 must not break any existing test — every existing
test constructs `AcquisitionPanel` with `_Ctrl()` or a subclass, and the two new methods are
no-ops/defaults that don't change any existing behavior)

- [ ] **Step 9: Commit**

```bash
git add pendulastic_app.py tests/test_app.py tests/test_acquisition_panel.py
git commit -m "feat: add auto-tare calibration state and on_countdown_start hook"
```

---

### Task 2: Stability detection in `App._tick()`

**Files:**
- Modify: `pendulastic_app.py` (`App._tick`, the flex-axis/rate-warning block area)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `pendulastic_imu_server.get_state()["angles"]["pitch"/"roll"]`,
  `pendulastic_imu_server.zero()`, `App._calib_buffer`/`_calib_was_stable`/`_calib_ever_stable`
  (Task 1), `AcquisitionPanel._countdown_id` (existing)
- Produces: edge-triggered `_imu.zero()` calls during an active countdown

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_tick_fires_zero_once_when_stable_during_countdown(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._acq._countdown_id = "sentinel"   # any non-None value marks countdown active
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "angles": {"pitch": 10.0, "roll": 0.0},
        })
        for _ in range(_m._CALIB_BUFFER_SAMPLES + 5):
            app._tick_calibration_check()
        assert len(zero_calls) == 1
        assert app._calib_ever_stable is True
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_refires_after_drift_then_restabilizing(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._acq._countdown_id = "sentinel"
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        state = {"pitch": 10.0, "roll": 0.0}
        monkeypatch.setattr(_m._imu, "get_state", lambda: {"angles": dict(state)})

        for _ in range(_m._CALIB_BUFFER_SAMPLES + 2):
            app._tick_calibration_check()
        assert len(zero_calls) == 1

        # Drift: feed a swinging pitch that exceeds the stability range so the
        # buffer's peak-to-peak range fails, resetting the edge-trigger.
        for i in range(_m._CALIB_BUFFER_SAMPLES):
            state["pitch"] = 10.0 + (i % 2) * 10.0   # alternates 10/20 -> 10 deg swing
            app._tick_calibration_check()
        assert len(zero_calls) == 1, "must not re-fire while still unstable"

        # Re-stabilize at a new position.
        state["pitch"] = 45.0
        for _ in range(_m._CALIB_BUFFER_SAMPLES + 2):
            app._tick_calibration_check()
        assert len(zero_calls) == 2, "must re-fire on the next new stable window"
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_skipped_outside_countdown(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._acq._countdown_id = None   # no countdown running
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "angles": {"pitch": 10.0, "roll": 0.0},
        })
        for _ in range(_m._CALIB_BUFFER_SAMPLES + 5):
            app._tick_calibration_check()
        assert zero_calls == []
        assert app._calib_buffer == []
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k tick_calibration -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute '_tick_calibration_check'`

- [ ] **Step 3: Extract the stability check into its own method and call it from `_tick()`**

Splitting this into its own method (rather than inlining in `_tick()`) is what makes it testable
without pumping the Tk event loop through `self.after(50, self._tick)`'s recursive scheduling — the
tests above call it directly.

In `pendulastic_app.py`, change:

```python
# OLD
        # Flip label when flex axis transitions from armed → captured
        if (_IMU_AVAIL and "imu" in self._active_sources
                and self._state in ("idle", "recording")):
            try:
                st = _imu.get_state()
                # Low gyro rate makes AHRS integration unreliable regardless of
                # flex-axis state -- surface it first. Same threshold/message
                # pattern already used in pendulastic_viewer.py.
                slow = [d for d in (st["proximal"], st["distal"])
                        if d["connected"] and 0 < d.get("hz", 0) < _imu.MIN_USABLE_HZ]
                if slow:
                    hz = min(d["hz"] for d in slow)
                    self._acq.lbl_method_status.config(
                        text=f"⚠ gyro only {hz:.0f} Hz — set the app's update "
                             f"interval to 10 ms (≥{_imu.MIN_USABLE_HZ:.0f} Hz needed)",
                        fg="#D97706")
                elif st.get("flex_axis_captured"):
                    self._acq.lbl_method_status.config(
                        text="● Axis locked — sagittal tracking", fg="green")
                elif st.get("flex_axis_armed"):
                    self._acq.lbl_method_status.config(
                        text="⚡ Flex once to capture axis...", fg="#B36B00")
            except Exception:
                pass

        self.after(50, self._tick)

# NEW
        # Flip label when flex axis transitions from armed → captured
        if (_IMU_AVAIL and "imu" in self._active_sources
                and self._state in ("idle", "recording")):
            try:
                st = _imu.get_state()
                # Low gyro rate makes AHRS integration unreliable regardless of
                # flex-axis state -- surface it first. Same threshold/message
                # pattern already used in pendulastic_viewer.py.
                slow = [d for d in (st["proximal"], st["distal"])
                        if d["connected"] and 0 < d.get("hz", 0) < _imu.MIN_USABLE_HZ]
                if slow:
                    hz = min(d["hz"] for d in slow)
                    self._acq.lbl_method_status.config(
                        text=f"⚠ gyro only {hz:.0f} Hz — set the app's update "
                             f"interval to 10 ms (≥{_imu.MIN_USABLE_HZ:.0f} Hz needed)",
                        fg="#D97706")
                elif st.get("flex_axis_captured"):
                    self._acq.lbl_method_status.config(
                        text="● Axis locked — sagittal tracking", fg="green")
                elif st.get("flex_axis_armed"):
                    self._acq.lbl_method_status.config(
                        text="⚡ Flex once to capture axis...", fg="#B36B00")
            except Exception:
                pass

        self._tick_calibration_check()

        self.after(50, self._tick)

    def _tick_calibration_check(self) -> None:
        """Countdown auto-tare: continuously watch for a stable hold and
        re-tare (edge-triggered) each time a new stable window begins.
        Active only while AcquisitionPanel's countdown is running."""
        if not (_IMU_AVAIL and "imu" in self._active_sources
                and self._acq._countdown_id is not None):
            return
        try:
            st = _imu.get_state()
            ang = st.get("angles", {})
            pitch, roll = ang.get("pitch"), ang.get("roll")
            if pitch is None or roll is None or not (math.isfinite(pitch) and math.isfinite(roll)):
                return
            self._calib_buffer.append((pitch, roll))
            if len(self._calib_buffer) > _CALIB_BUFFER_SAMPLES:
                self._calib_buffer.pop(0)
            if len(self._calib_buffer) < _CALIB_BUFFER_SAMPLES:
                self._calib_was_stable = False
                return
            pitches = [p for p, _ in self._calib_buffer]
            rolls   = [r for _, r in self._calib_buffer]
            stable = (max(pitches) - min(pitches) < _CALIB_STABILITY_RANGE_DEG
                     and max(rolls) - min(rolls) < _CALIB_STABILITY_RANGE_DEG)
            if stable and not self._calib_was_stable:
                _imu.zero()
                self._calib_ever_stable = True
            self._calib_was_stable = stable
        except Exception:
            pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k tick_calibration -v`
Expected: 3 passed

- [ ] **Step 5: Run the full `test_app.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -v`
Expected: all pass (the known pre-existing tkinter-singleton flake may appear when run together —
re-run individually to confirm if so)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add edge-triggered stability detection driving auto-tare"
```

---

### Task 3: `is_imu_calibrated()` + countdown extend-then-confirm fallback

**Files:**
- Modify: `pendulastic_app.py` (`App`, near `on_source_changed`), `pendulastic_app.py:600-607`
  (`AcquisitionPanel._tick_countdown`)
- Test: `tests/test_app.py`, `tests/test_acquisition_panel.py`

**Interfaces:**
- Consumes: `App._active_sources`, `App._calib_ever_stable` (Task 1), `AcquisitionPanel._calib_extension_s` (Task 1)
- Produces: `App.is_imu_calibrated() -> bool`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_is_imu_calibrated_true_when_imu_not_active():
    from pendulastic_app import App
    app = App()
    try:
        app._active_sources = []
        app._calib_ever_stable = False
        assert app.is_imu_calibrated() is True
    finally:
        app.destroy()


def test_is_imu_calibrated_reflects_ever_stable_when_imu_active():
    from pendulastic_app import App
    app = App()
    try:
        app._active_sources = ["imu"]
        app._calib_ever_stable = False
        assert app.is_imu_calibrated() is False
        app._calib_ever_stable = True
        assert app.is_imu_calibrated() is True
    finally:
        app.destroy()
```

Add to `tests/test_acquisition_panel.py`:

```python
def test_tick_countdown_extends_when_not_calibrated():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return False
            def on_start(self): calls.append("start")
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = 0
        p._tick_countdown(0)
        r.update()
        assert "start" not in calls
        assert p._calib_extension_s == 1
        assert "Hold steady" in p.status_var.get()
    finally:
        r.destroy()


def test_tick_countdown_proceeds_when_calibrated():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return True
            def on_start(self): calls.append("start")
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = 0
        p._tick_countdown(0)
        r.update()
        assert "start" in calls
    finally:
        r.destroy()


def test_tick_countdown_confirm_dialog_accept_proceeds(monkeypatch):
    from pendulastic_app import AcquisitionPanel
    import pendulastic_app as _m
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return False
            def on_start(self): calls.append("start")
        monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **kw: True)
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = _m._MAX_CALIB_EXTENSION_S
        p._tick_countdown(0)
        r.update()
        assert "start" in calls
    finally:
        r.destroy()


def test_tick_countdown_confirm_dialog_decline_cancels(monkeypatch):
    from pendulastic_app import AcquisitionPanel
    import pendulastic_app as _m
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return False
            def on_start(self): calls.append("start")
        monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **kw: False)
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = _m._MAX_CALIB_EXTENSION_S
        p._tick_countdown(0)
        r.update()
        assert "start" not in calls
        assert p.btn_start.cget("text") == "START RECORDING"
    finally:
        r.destroy()


def test_countdown_status_shows_stabilizing_then_calibrated():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        class C(_Ctrl):
            calibrated = False
            def is_imu_calibrated(self): return self.calibrated
        ctrl = C()
        p = AcquisitionPanel(r, ctrl); p.pack()
        p._src_imu.set(True)
        p._tick_countdown(3)
        r.update()
        assert "stabilizing" in p.status_var.get()
        ctrl.calibrated = True
        p._tick_countdown(2)
        r.update()
        assert "calibrated" in p.status_var.get()
    finally:
        if p._countdown_id:
            p.after_cancel(p._countdown_id)
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k is_imu_calibrated -v`
Run: `.venv\Scripts\pytest.exe tests\test_acquisition_panel.py -k "tick_countdown or countdown_status" -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'is_imu_calibrated'` and
`AttributeError: 'C' object has no attribute 'is_imu_calibrated'` / unexpected behavior since
`_tick_countdown` doesn't call it yet

- [ ] **Step 3: Add `App.is_imu_calibrated()`**

Add this method to the `App` class, right after `on_countdown_start` (Task 1):

```python
    def is_imu_calibrated(self) -> bool:
        """True if calibration isn't required (imu not an active source) or
        has already succeeded at least once this countdown."""
        if "imu" not in self._active_sources:
            return True
        return self._calib_ever_stable
```

- [ ] **Step 4: Rewrite `_tick_countdown` with the extend-then-confirm fallback**

In `pendulastic_app.py`, change:

```python
# OLD
    def _tick_countdown(self, n: int) -> None:
        if n == 0:
            self.btn_start.config(text="START RECORDING",
                                  command=self._on_start_clicked, bg=_GREEN)
            self.controller.on_start()
            return
        self.status_var.set(f"Starting in {n}…")
        self._countdown_id = self.after(1000, lambda: self._tick_countdown(n - 1))

# NEW
    def _tick_countdown(self, n: int) -> None:
        if n == 0:
            if self.controller.is_imu_calibrated():
                self.btn_start.config(text="START RECORDING",
                                      command=self._on_start_clicked, bg=_GREEN)
                self.controller.on_start()
                return
            if self._calib_extension_s < _MAX_CALIB_EXTENSION_S:
                self._calib_extension_s += 1
                self.status_var.set("Hold steady…")
                self._countdown_id = self.after(1000, lambda: self._tick_countdown(0))
                return
            if messagebox.askyesno(
                    "Sensor Not Stable",
                    "The IMU sensor hasn't settled to a stable reading. "
                    "Start recording anyway?"):
                self.btn_start.config(text="START RECORDING",
                                      command=self._on_start_clicked, bg=_GREEN)
                self.controller.on_start()
            else:
                self._cancel_countdown()
            return
        if "imu" in self.get_active_sources():
            calib_suffix = (" — ✓ calibrated" if self.controller.is_imu_calibrated()
                           else " — stabilizing…")
        else:
            calib_suffix = ""
        self.status_var.set(f"Starting in {n}…{calib_suffix}")
        self._countdown_id = self.after(1000, lambda: self._tick_countdown(n - 1))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k is_imu_calibrated -v`
Run: `.venv\Scripts\pytest.exe tests\test_acquisition_panel.py -k "tick_countdown or countdown_status" -v`
Expected: all pass

- [ ] **Step 6: Run the full suites to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py tests\test_acquisition_panel.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_app.py tests/test_acquisition_panel.py
git commit -m "feat: add is_imu_calibrated() and countdown extend-then-confirm fallback"
```

---

### Task 4: Remove manual Zero Sensor/Clear Zero UI, force countdown for IMU trials

**Files:**
- Modify: `pendulastic_app.py:354-370` (`AcquisitionPanel._build_widgets`, the zero-button row),
  `pendulastic_app.py:377-381` (countdown checkbox — store as `self.countdown_chk`),
  `pendulastic_app.py:413-417` (`self._lockable`), `pendulastic_app.py:520-548`
  (`_on_source_changed`), `pendulastic_app.py:425-434` (`enter_idle`), removes
  `_on_zero_sensor`/`_on_clear_zero`
- Test: `tests/test_acquisition_panel.py`

**Interfaces:**
- Produces: `AcquisitionPanel.countdown_chk` (was a local variable, now stored on `self`)
- Removes: `AcquisitionPanel.btn_zero`, `AcquisitionPanel.btn_clear_zero`,
  `AcquisitionPanel._zero_frame`, `AcquisitionPanel._on_zero_sensor`,
  `AcquisitionPanel._on_clear_zero`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_acquisition_panel.py`:

```python
def test_zero_sensor_ui_removed():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert not hasattr(p, "btn_zero")
        assert not hasattr(p, "btn_clear_zero")
        assert not hasattr(p, "_zero_frame")
        assert not hasattr(p, "_on_zero_sensor")
        assert not hasattr(p, "_on_clear_zero")
    finally:
        r.destroy()


def test_countdown_locked_checked_when_imu_active():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(True)
        p._on_source_changed()
        r.update()
        assert p.countdown_var.get() is True
        assert str(p.countdown_chk.cget("state")) == "disabled"
        p._src_imu.set(False)
        p._on_source_changed()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "normal"
    finally:
        r.destroy()


def test_enter_idle_reapplies_countdown_lock():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(True)
        p._on_source_changed()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "disabled"
        # _lock_form(False) inside enter_idle would otherwise re-enable every
        # lockable widget including the countdown checkbox -- it must be
        # re-applied afterward so an IMU trial can't slip the countdown.
        p.enter_idle()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "disabled"
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_acquisition_panel.py -k "zero_sensor_ui_removed or countdown_locked or enter_idle_reapplies" -v`
Expected: FAIL — `btn_zero` etc. still exist; `countdown_chk` attribute doesn't exist yet

- [ ] **Step 3: Remove the Zero Sensor/Clear Zero widgets**

In `pendulastic_app.py`, change:

```python
# OLD
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

# NEW
        # row 9 — Modality status (calibration is now automatic during the
        # countdown -- see App._tick_calibration_check / AcquisitionPanel's
        # forced-on countdown checkbox below)
        self.lbl_method_status = tk.Label(
            self, text="● OptiTrack (Motive)", font=("Consolas", 9), fg="green", anchor="w")
        self.lbl_method_status.grid(row=9, column=0, sticky="w", padx=16)
```

- [ ] **Step 4: Store the countdown checkbox on `self`**

In `pendulastic_app.py`, change:

```python
# OLD
        # row 11 — countdown checkbox
        self.countdown_var = tk.BooleanVar(value=False)
        countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var)
        countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)

# NEW
        # row 11 — countdown checkbox (forced on/locked while IMU is an
        # active source -- it's the only calibration path now)
        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var)
        self.countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)
```

- [ ] **Step 5: Update `self._lockable`**

In `pendulastic_app.py`, change:

```python
# OLD
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            self.btn_zero, self.btn_clear_zero, self.btn_back,
        ]

# NEW
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            self.countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            self.btn_back,
        ]
```

- [ ] **Step 6: Rewrite `_on_source_changed` to force the countdown instead of toggling the zero frame**

In `pendulastic_app.py`, change:

```python
# OLD
    def _on_source_changed(self) -> None:
        """Called on any source checkbox toggle. Updates status label and Zero button visibility."""
        sources = self.get_active_sources()
        # Show/hide Zero Sensor frame
        if self._src_imu.get():
            self._zero_frame.grid()
        else:
            self._zero_frame.grid_remove()
        # Show/hide video file path frame

# NEW
    def _on_source_changed(self) -> None:
        """Called on any source checkbox toggle. Updates status label and
        forces the countdown on (IMU trials have no other calibration path
        now that the manual Zero Sensor button is gone)."""
        sources = self.get_active_sources()
        if self._src_imu.get():
            self.countdown_var.set(True)
            self.countdown_chk.config(state="disabled")
        else:
            self.countdown_chk.config(state="normal")
        # Show/hide video file path frame
```

(The rest of `_on_source_changed` — the video-file-path toggle, the status-label build, and the
final `self.controller.on_source_changed(sources)` call — is unchanged.)

- [ ] **Step 7: Remove `_on_zero_sensor` and `_on_clear_zero`**

In `pendulastic_app.py`, delete both methods entirely:

```python
# DELETE
    def _on_zero_sensor(self) -> None:
        if _IMU_AVAIL:
            try:
                _imu.zero()
                self.lbl_method_status.config(
                    text="⚡ Flex once to capture axis...", fg="#B36B00")
            except Exception as e:
                messagebox.showerror("Zero Sensor", f"Could not zero sensor:\n{e}")

    def _on_clear_zero(self) -> None:
        if _IMU_AVAIL:
            try:
                _imu.clear_zero()
            except Exception:
                pass
        self._on_source_changed()
```

- [ ] **Step 8: Re-apply the countdown lock in `enter_idle()`**

`_lock_form(False)` (called inside `enter_idle`) re-enables every widget in `self._lockable`,
including the countdown checkbox, regardless of whether IMU is still an active source — so it must
be re-applied afterward.

In `pendulastic_app.py`, change:

```python
# OLD
    def enter_idle(self) -> None:
        self._cancel_countdown()
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked,
                              bg=_GREEN, state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_form(False)
        self.canvas_tele.grid_remove()
        self.lbl_preview.grid_remove()   # hide live preview if it was shown
        self.status_var.set("Idle — ready to record.")

# NEW
    def enter_idle(self) -> None:
        self._cancel_countdown()
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked,
                              bg=_GREEN, state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_form(False)
        self.canvas_tele.grid_remove()
        self.lbl_preview.grid_remove()   # hide live preview if it was shown
        self.status_var.set("Idle — ready to record.")
        self._on_source_changed()   # re-apply the IMU-forced countdown lock
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_acquisition_panel.py -k "zero_sensor_ui_removed or countdown_locked or enter_idle_reapplies" -v`
Expected: 3 passed

- [ ] **Step 10: Run the full `test_acquisition_panel.py` and `test_app.py` suites**

Run: `.venv\Scripts\pytest.exe tests\test_acquisition_panel.py tests\test_app.py -v`
Expected: all pass

- [ ] **Step 11: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: remove manual Zero Sensor/Clear Zero UI, force countdown for IMU trials"
```

---

### Task 5: Full regression run

**Files:**
- None modified — verification only

- [ ] **Step 1: Run the full test suite**

Run: `.venv\Scripts\pytest.exe tests\ -v --ignore=tests\test_metrics.py --ignore=tests\test_pose.py --ignore=tests\test_stats.py --ignore=tests\test_video.py`

Expected: all pass. If the known pre-existing tkinter-singleton flake in `test_acquisition_panel.py`
appears when run alongside other files, re-run `tests/test_acquisition_panel.py` individually to
confirm it's the pre-existing flake and not a real regression (documented in this repo's other
plans — a different test fails each run, never the same one twice, and always passes in isolation).

- [ ] **Step 2: Manual acceptance step (not automatable here)**

Record a real trial with the countdown running: confirm the status text transitions
"stabilizing…" → "✓ calibrated" while holding the limb steady, and that the CSV starts at a
plausible ~180° rather than `nan`. This requires actual Sensor Stream hardware and is out of scope
for the automated test suite — flag it as the remaining manual verification step.

- [ ] **Step 3: Commit (only if Step 1 required any fixes)**

```bash
git add -A
git commit -m "test: fix regressions found in full-suite run"
```
