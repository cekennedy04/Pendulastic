# IMU Stillness-Gated Gyro-Bias Calibration & Drift Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every stillness check that gates gyro-bias calibration (both live acquisition and
offline replay) with a direct check on raw gyro variance and raw accel-magnitude stability instead
of a derived-signal proxy; investigate whether accelerometer drift contributes to the RMSE problem;
and extend the OptiTrack validation harness to prove RMSE improves, with no trial regressing.

**Architecture:** A new pure function, `_is_stationary_window()`, in `pendulastic_imu_server.py`
becomes the single source of truth for "is the sensor genuinely still," reused by both the live
`_IMUDevice`/`App` path and the offline `imu_calibration_tuner.py` replay path. A standalone script
double-integrates raw accel to characterize drift. `evaluate_all_participants.py` gains reliability
metrics (extracted from the currently-broken `validate_controls.py` into a new
`reliability_stats.py`) alongside its existing RMSE leaderboard.

**Tech Stack:** Python 3.13, NumPy, pytest. No new dependencies.

## Global Constraints

- Raw gyro samples (`v` passed to `on_gyro()`/`replay_trial()`'s gyro branch) are in **rad/s**,
  matching `_FLEX_CAPTURE_THRESHOLD = 1.0` rad/s (`pendulastic_imu_server.py:476`) — never deg/s.
- Raw accel samples are in **m/s²** (gravity ≈9.81).
- `GYRO_STATIONARY_MAX_RAD_S` / `ACCEL_STATIONARY_MAX_MPS2` are determined empirically in Task 1
  from real recorded data, not guessed — every later task's threshold values come from Task 1's
  output, not from this plan's author.
- Both raw gyro AND raw accel stability are required for `_is_stationary_window()` to return
  `True` — neither alone is sufficient (gyro alone misses linear-acceleration handling; accel alone
  misses rotational handling).
- `App`'s countdown only tares when **every connected** IMU device (proximal and/or distal,
  whichever are active) independently reports stationary — never on a partial reading.
- `load_imu_trial()`, `_RoleState`'s existing bias-subtraction mechanics (edge-triggering, the
  gyro hold buffer, `gyro_bias` subtraction before `ahrs.update()`), and `zero()`'s
  `calibrate_gyro_bias()` calls are correct and unchanged — only the *stability signal* that gates
  them changes.
- Component B (`analyze_accel_drift.py`) is a standalone script: not wired into any pipeline, not
  covered by the automated pytest suite.
- Component C leaves `validate_controls.py` untouched (it cannot currently run — separate,
  unrelated problem). Only its 3 self-contained stats functions are extracted into
  `reliability_stats.py`.
- Full spec: `docs/superpowers/specs/2026-08-04-imu-stillness-gyro-bias-design.md`.

---

### Task 1: Empirically determine stationarity thresholds

**Files:**
- Create: `find_stationarity_thresholds.py` (standalone analysis script — not part of the pytest
  suite, matching `evaluate_all_participants.py`'s existing standalone-script convention)

**Interfaces:**
- Produces: printed output only (this task has no downstream code dependents — its output is the
  two threshold *values* a human reads off and hardcodes into Task 2's constants, not a callable)

- [ ] **Step 1: Write the script**

```python
"""
find_stationarity_thresholds.py
================================
For every data/*_imu_raw.jsonl recording, finds the onset of deliberate
motion (the first gyro burst whose magnitude crosses _FLEX_CAPTURE_THRESHOLD
-- the same "arm the flex axis" burst pendulastic_imu_server.on_gyro() and
imu_calibration_tuner.replay_trial() already detect) and prints the raw
gyro/accel peak-to-peak magnitude range over two 1.0s windows relative to it:

  "last-1s"  : the 1.0s immediately before onset -- the best candidate for
               "examiner just released, genuinely still."
  "minus-4s" : the 1.0s window starting 4s before onset -- more likely still
               mid-handling (gripping/positioning the limb).

Printed side-by-side across every recording, these two columns should
visibly separate: "last-1s" should cluster low, "minus-4s" should cluster
higher (or at least contain the outliers) if raw gyro/accel variance is a
usable stillness signal. Read the printed table and pick
GYRO_STATIONARY_MAX_RAD_S / ACCEL_STATIONARY_MAX_MPS2 as values comfortably
above the "last-1s" cluster and below the "minus-4s" outliers.

Usage:
    .venv\\Scripts\\python.exe find_stationarity_thresholds.py
"""
from __future__ import annotations

import glob
import json
import math

import numpy as np

from pendulastic_imu_server import _FLEX_CAPTURE_THRESHOLD, ROLE_DISTAL, ROLE_PROXIMAL

WINDOW_S = 1.0


def _load_raw_log(path: str) -> list:
    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except ValueError:
                continue
    return samples


def _find_onset_t(samples: list) -> float | None:
    """First gyro sample (any role) whose magnitude crosses
    _FLEX_CAPTURE_THRESHOLD, mirroring the flex-axis-arm burst detection
    already used live and in replay_trial()."""
    for s in samples:
        if s.get("sensor") != "gyro":
            continue
        v = s.get("v")
        if v is None:
            continue
        if math.sqrt(sum(c * c for c in v)) >= _FLEX_CAPTURE_THRESHOLD:
            return s["t"]
    return None


def _peak_to_peak_in_window(samples: list, sensor: str, t_start: float, t_end: float) -> float | None:
    mags = [math.sqrt(sum(c * c for c in s["v"]))
            for s in samples
            if s.get("sensor") == sensor and t_start <= s.get("t", -1) <= t_end]
    if len(mags) < 2:
        return None
    return max(mags) - min(mags)


def main():
    paths = sorted(glob.glob("data/*_imu_raw.jsonl"))
    if not paths:
        print("No data/*_imu_raw.jsonl files found.")
        return

    header = f"{'file':<55} {'gyro last-1s':>13} {'gyro minus-4s':>14} {'accel last-1s':>14} {'accel minus-4s':>15}"
    print(header)
    print("-" * len(header))
    for path in paths:
        samples = _load_raw_log(path)
        onset_t = _find_onset_t(samples)
        if onset_t is None:
            print(f"{path:<55} {'no onset found':>13}")
            continue
        rows = []
        for sensor in ("gyro", "accel"):
            last1 = _peak_to_peak_in_window(samples, sensor, onset_t - WINDOW_S, onset_t)
            minus4 = _peak_to_peak_in_window(samples, sensor, onset_t - 4 * WINDOW_S, onset_t - 3 * WINDOW_S)
            rows.append((last1, minus4))
        (g_last1, g_minus4), (a_last1, a_minus4) = rows
        fmt = lambda x: f"{x:.4f}" if x is not None else "n/a"
        print(f"{path:<55} {fmt(g_last1):>13} {fmt(g_minus4):>14} {fmt(a_last1):>14} {fmt(a_minus4):>15}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the real recordings**

Run: `.venv\Scripts\python.exe find_stationarity_thresholds.py`

- [ ] **Step 3: Manual step — pick and record the thresholds**

Read the printed table. Pick `GYRO_STATIONARY_MAX_RAD_S` as a value comfortably above the "gyro
last-1s" column's cluster and below the "gyro minus-4s" column's outliers (a gap of at least 2x is
a good sign the signal separates cleanly; if the columns overlap heavily, note that in a comment in
Task 2's constants rather than picking an arbitrary number — it means raw-gyro variance alone isn't
cleanly separating still-vs-handled for this dataset, which is itself a useful finding). Do the same
for `ACCEL_STATIONARY_MAX_MPS2` from the accel columns. Write both chosen values down — Task 2 hard
-codes them as named constants with a comment citing this script and the run's output.

- [ ] **Step 4: Commit**

```bash
git add find_stationarity_thresholds.py
git commit -m "feat: add script to empirically derive stationarity thresholds from real trials"
```

---

### Task 2: Shared `_is_stationary_window` primitive + live device wiring

**Files:**
- Modify: `pendulastic_imu_server.py` (constants area, `_IMUDevice.__init__`, `_IMUDevice.on_accel`,
  new `_IMUDevice.is_stationary`, new module-level `is_stationary`)
- Test: `tests/test_imu_server.py`

**Interfaces:**
- Consumes: `GYRO_BIAS_WINDOW_S` (existing, `pendulastic_imu_server.py:96`), the threshold values
  chosen in Task 1
- Produces: `_is_stationary_window(gyro_buf: list[tuple[float, np.ndarray]], accel_buf:
  list[tuple[float, np.ndarray]], now: float) -> bool` (module-level, importable),
  `_IMUDevice.is_stationary(self) -> bool`, `_IMUDevice._accel_hold_buf: list[tuple[float,
  np.ndarray]]`, module-level `is_stationary() -> bool`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_imu_server.py`:

```python
def test_is_stationary_window_true_for_flat_gyro_and_accel():
    """A window with near-zero gyro variance and accel magnitude pinned near
    gravity, spanning the full GYRO_BIAS_WINDOW_S, is stationary."""
    now = 10.0
    gyro_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.01, -0.01, 0.0]))
                for i in range(21)]
    accel_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.0, 0.0, 9.81]))
                 for i in range(21)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is True


def test_is_stationary_window_false_for_handling_gyro():
    """A window whose raw gyro OSCILLATES DIRECTION on one axis, at a
    magnitude well past GYRO_STATIONARY_MAX_RAD_S -- e.g. an examiner
    gripping/repositioning the limb -- must not count as stationary, even
    with a flat accel signal. Scaled relative to the actual (empirically
    -determined, per Task 1) threshold rather than a hardcoded literal, so
    this test stays correct regardless of what Task 1 picked. Oscillating
    direction (not just varying magnitude) is deliberate: a peak-to-peak-of
    -magnitude check would see ~0 range here even though the sensor is
    clearly moving -- this is exactly the case the per-axis check exists
    to catch."""
    now = 10.0
    amp = imu.GYRO_STATIONARY_MAX_RAD_S * 3.0
    gyro_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05,
                np.array([amp, 0.0, 0.0]) if i % 2 == 0 else np.array([-amp, 0.0, 0.0]))
                for i in range(21)]
    accel_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.0, 0.0, 9.81]))
                 for i in range(21)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is False


def test_is_stationary_window_false_for_handling_accel():
    """A window with flat gyro but accel's z-axis swinging well past
    ACCEL_STATIONARY_MAX_MPS2 -- e.g. the limb being lifted/repositioned
    without much rotation -- must not count as stationary. Scaled relative
    to the actual threshold, same reasoning as the gyro case above."""
    now = 10.0
    half_amp = imu.ACCEL_STATIONARY_MAX_MPS2 * 1.5
    gyro_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05, np.array([0.01, -0.01, 0.0]))
                for i in range(21)]
    accel_buf = [(now - imu.GYRO_BIAS_WINDOW_S + i * 0.05,
                 np.array([0.0, 0.0, 9.81 + half_amp]) if i % 2 == 0
                 else np.array([0.0, 0.0, 9.81 - half_amp]))
                 for i in range(21)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is False


def test_is_stationary_window_false_when_buffer_does_not_span_full_window():
    """A buffer that only covers a fraction of GYRO_BIAS_WINDOW_S must not
    count as stationary regardless of content -- a burst of flat readings a
    few ms apart is not evidence of a full still window."""
    now = 10.0
    gyro_buf = [(now - 0.1 + i * 0.01, np.array([0.0, 0.0, 0.0])) for i in range(10)]
    accel_buf = [(now - 0.1 + i * 0.01, np.array([0.0, 0.0, 9.81])) for i in range(10)]
    assert imu._is_stationary_window(gyro_buf, accel_buf, now) is False


def test_imudevice_accel_hold_buf_populated_by_on_accel():
    """on_accel() must append raw accel samples to _accel_hold_buf, mirroring
    on_gyro()'s existing _gyro_hold_buf maintenance, so is_stationary() has
    real data to check."""
    dev = imu._IMUDevice("12.0.1.1")
    dev.on_accel([0.0, 0.0, 9.81], ts=1000)
    dev.on_accel([0.0, 0.0, 9.80], ts=1010)
    assert len(dev._accel_hold_buf) == 2
    np.testing.assert_allclose(dev._accel_hold_buf[-1][1], [0.0, 0.0, 9.80])


def test_imudevice_is_stationary_reflects_its_own_buffers():
    """_IMUDevice.is_stationary() delegates to _is_stationary_window() using
    this device's own _gyro_hold_buf/_accel_hold_buf."""
    dev = imu._IMUDevice("12.0.1.2")
    now = __import__("time").time()
    for i in range(21):
        t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
        dev._gyro_hold_buf.append((t, np.array([0.0, 0.0, 0.0])))
        dev._accel_hold_buf.append((t, np.array([0.0, 0.0, 9.81])))
    assert dev.is_stationary() is True


def test_module_is_stationary_requires_all_connected_devices_stationary():
    """Module-level is_stationary() must return True only if every connected
    device independently reports stationary -- a half-stationary reading
    (one still, one being handled) must not pass."""
    imu.reset_devices()
    imu.clear_zero()
    now = __import__("time").time()

    def _fill_still(dev):
        for i in range(21):
            t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
            dev._gyro_hold_buf.append((t, np.array([0.0, 0.0, 0.0])))
            dev._accel_hold_buf.append((t, np.array([0.0, 0.0, 9.81])))

    def _fill_handled(dev):
        amp = imu.GYRO_STATIONARY_MAX_RAD_S * 3.0
        for i in range(21):
            t = now - imu.GYRO_BIAS_WINDOW_S + i * 0.05
            v = np.array([amp, 0.0, 0.0]) if i % 2 == 0 else np.array([-amp, 0.0, 0.0])
            dev._gyro_hold_buf.append((t, v))
            dev._accel_hold_buf.append((t, np.array([0.0, 0.0, 9.81])))

    imu._devices["12.0.1.3"] = imu._IMUDevice("12.0.1.3")
    imu._roles["12.0.1.3"] = imu.ROLE_PROXIMAL
    imu._devices["12.0.1.3"].last_rx = now
    _fill_still(imu._devices["12.0.1.3"])

    imu._devices["12.0.1.4"] = imu._IMUDevice("12.0.1.4")
    imu._roles["12.0.1.4"] = imu.ROLE_DISTAL
    imu._devices["12.0.1.4"].last_rx = now
    _fill_handled(imu._devices["12.0.1.4"])

    assert imu.is_stationary() is False, "one handled device must fail the whole check"

    imu._devices["12.0.1.4"]._gyro_hold_buf = []
    imu._devices["12.0.1.4"]._accel_hold_buf = []
    _fill_still(imu._devices["12.0.1.4"])
    assert imu.is_stationary() is True, "once both are still, the check must pass"

    imu.reset_devices()
    imu.clear_zero()


def test_module_is_stationary_false_with_no_connected_devices():
    imu.reset_devices()
    imu.clear_zero()
    assert imu.is_stationary() is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -k "is_stationary or accel_hold_buf" -v`
Expected: FAIL — `AttributeError: module 'pendulastic_imu_server' has no attribute
'_is_stationary_window'` (and similar for the device/module methods)

- [ ] **Step 3: Add the threshold constants**

In `pendulastic_imu_server.py`, change the gyro-bias constants block:

```python
# OLD
GYRO_BIAS_WINDOW_S = 1.0
GYRO_BIAS_MIN_SAMPLES = 5   # below this the mean is too noisy to trust; keep bias at 0

# NEW
GYRO_BIAS_WINDOW_S = 1.0
GYRO_BIAS_MIN_SAMPLES = 5   # below this the mean is too noisy to trust; keep bias at 0

# Stillness gate for calibrate_gyro_bias(): a window only counts as
# "genuinely still" (not examiner handling) if raw gyro AND raw accel both
# stay within these peak-to-peak bounds over GYRO_BIAS_WINDOW_S. Values
# chosen from find_stationarity_thresholds.py's output against real
# recordings -- see docs/superpowers/specs/2026-08-04-imu-stillness-gyro-bias-design.md
# Section 3.2 for the methodology. <FILL IN FROM TASK 1'S RUN>
GYRO_STATIONARY_MAX_RAD_S = <FILL IN FROM TASK 1'S RUN>
ACCEL_STATIONARY_MAX_MPS2 = <FILL IN FROM TASK 1'S RUN>
```

(The implementer replaces both `<FILL IN FROM TASK 1'S RUN>` placeholders with the actual numeric
values recorded in Task 1, Step 3 — this is the one place in this plan a value is deliberately not
pre-filled, because Task 1 must run against real data first to produce it.)

- [ ] **Step 4: Add `_is_stationary_window`**

In `pendulastic_imu_server.py`, add near `calibrate_gyro_bias` (after the `_IMUDevice` class, or as
a module-level function above it — place it directly above `class _IMUDevice:` so both the class
and the module-level `is_stationary()` below can reference it):

```python
def _is_stationary_window(gyro_buf: list[tuple[float, np.ndarray]],
                          accel_buf: list[tuple[float, np.ndarray]],
                          now: float) -> bool:
    """True iff both buffers span the full GYRO_BIAS_WINDOW_S and stay within
    GYRO_STATIONARY_MAX_RAD_S / ACCEL_STATIONARY_MAX_MPS2 peak-to-peak range
    -- checked per-axis (max over x/y/z of that axis's own peak-to-peak),
    not on the combined vector magnitude. Magnitude alone would miss a
    signal that oscillates DIRECTION at roughly constant magnitude (e.g.
    alternating +0.22/-0.22 rad/s on one axis -- exactly what examiner
    handling looks like): its peak-to-peak magnitude is near zero even
    though the sensor is clearly moving. This mirrors why the fused-angle
    check this replaces required pitch AND roll independently under
    threshold, not one combined angle. Pure function of two trailing raw
    -sample buffers so it can be reused verbatim by both the live
    _IMUDevice and the offline replay's per-role state."""
    for buf in (gyro_buf, accel_buf):
        if not buf or (now - buf[0][0]) < GYRO_BIAS_WINDOW_S * 0.95:
            return False

    def _max_axis_peak_to_peak(buf):
        vals = np.array([v for _, v in buf])   # shape (N, 3)
        ranges = vals.max(axis=0) - vals.min(axis=0)   # per-axis peak-to-peak
        return float(np.max(ranges))

    return (_max_axis_peak_to_peak(gyro_buf) < GYRO_STATIONARY_MAX_RAD_S
            and _max_axis_peak_to_peak(accel_buf) < ACCEL_STATIONARY_MAX_MPS2)
```

- [ ] **Step 5: Add `_accel_hold_buf` and wire it into `on_accel`**

In `pendulastic_imu_server.py`, in `_IMUDevice.__init__`, change:

```python
# OLD
        self.gyro_bias: np.ndarray = np.zeros(3)
        self._gyro_hold_buf: list[tuple[float, np.ndarray]] = []

# NEW
        self.gyro_bias: np.ndarray = np.zeros(3)
        self._gyro_hold_buf: list[tuple[float, np.ndarray]] = []
        # Trailing raw-accel buffer for is_stationary()'s accel-magnitude
        # check -- mirrors _gyro_hold_buf, maintained the same way in
        # on_accel().
        self._accel_hold_buf: list[tuple[float, np.ndarray]] = []
```

In `pendulastic_imu_server.py`, in `on_accel` (`pendulastic_imu_server.py:334`), change:

```python
# OLD
    def on_accel(self, v, ts):
        self.accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            if _CONFIG["gravity_seed"]:
                self.ahrs.q = _gravity_seed(self.accel)
            self._ahrs_seeded = True
        now = time.time()
        _raw_log_write(_roles.get(self.ident), "accel", v, ts)
        if _recording:
            _log_raw_csv(_roles.get(self.ident, self.ident), "Accelerometer", v, ts, now)
        self._touch(ts, now)

# NEW
    def on_accel(self, v, ts):
        self.accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            if _CONFIG["gravity_seed"]:
                self.ahrs.q = _gravity_seed(self.accel)
            self._ahrs_seeded = True
        now = time.time()
        _raw_log_write(_roles.get(self.ident), "accel", v, ts)
        if _recording:
            _log_raw_csv(_roles.get(self.ident, self.ident), "Accelerometer", v, ts, now)

        # Trailing raw-accel buffer for is_stationary()'s accel-magnitude
        # check. Mirrors on_gyro()'s _gyro_hold_buf maintenance.
        self._accel_hold_buf.append((now, self.accel.copy()))
        bias_cutoff = now - GYRO_BIAS_WINDOW_S
        self._accel_hold_buf = [(t, vv) for t, vv in self._accel_hold_buf
                                if t >= bias_cutoff]

        self._touch(ts, now)
```

- [ ] **Step 6: Add `_IMUDevice.is_stationary` and module-level `is_stationary`**

In `pendulastic_imu_server.py`, add to the `_IMUDevice` class, near `calibrate_gyro_bias`:

```python
    def is_stationary(self) -> bool:
        """True iff this device's own trailing raw gyro/accel buffers show a
        genuinely still hold -- see _is_stationary_window()."""
        return _is_stationary_window(self._gyro_hold_buf, self._accel_hold_buf, time.time())
```

In `pendulastic_imu_server.py`, add a module-level function near `zero()`
(`pendulastic_imu_server.py:597`):

```python
def is_stationary() -> bool:
    """True iff every currently connected device (proximal and/or distal,
    whichever are active) independently reports a genuinely still hold. A
    half-stationary reading -- one device still, one being handled -- must
    not pass. False if no device is connected."""
    with _lock:
        devices = [d for d in _devices.values() if d.connected]
        if not devices:
            return False
        return all(d.is_stationary() for d in devices)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -k "is_stationary or accel_hold_buf" -v`
Expected: all pass

- [ ] **Step 8: Run the full `test_imu_server.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -v`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat: add raw-signal is_stationary() stillness check, live-side"
```

---

### Task 3: Wire live `App._tick_calibration_check()` to the new stillness check

**Files:**
- Modify: `pendulastic_app.py` (module constants, `App.__init__`, `App._tick_calibration_check`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `pendulastic_imu_server.is_stationary()` (Task 2)
- Produces: no new public interface — `_tick_calibration_check()`'s external behavior (calls
  `_imu.zero()` edge-triggered, sets `_calib_ever_stable`) is unchanged; only its internal signal
  changes.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_tick_calibration_check_fires_zero_when_imu_reports_stationary(monkeypatch):
    """_tick_calibration_check() must now gate on _imu.is_stationary() directly,
    not on a fused pitch/roll buffer it maintains itself."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._acq._countdown_id = "sentinel"
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        monkeypatch.setattr(_m._imu, "is_stationary", lambda: True)
        app._tick_calibration_check()
        assert len(zero_calls) == 1
        assert app._calib_ever_stable is True
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_check_does_not_fire_when_imu_reports_not_stationary(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._acq._countdown_id = "sentinel"
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        monkeypatch.setattr(_m._imu, "is_stationary", lambda: False)
        app._tick_calibration_check()
        assert zero_calls == []
        assert app._calib_ever_stable is False
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_check_refires_after_drift_then_restabilizing(monkeypatch):
    """Edge-trigger behavior must be preserved: False->True fires once, stays
    latched while True, then re-fires on the next False->True transition."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._acq._countdown_id = "sentinel"
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))

        monkeypatch.setattr(_m._imu, "is_stationary", lambda: True)
        app._tick_calibration_check()
        app._tick_calibration_check()
        app._tick_calibration_check()
        assert len(zero_calls) == 1, "must not re-fire every tick while continuously stationary"

        monkeypatch.setattr(_m._imu, "is_stationary", lambda: False)
        app._tick_calibration_check()
        assert len(zero_calls) == 1

        monkeypatch.setattr(_m._imu, "is_stationary", lambda: True)
        app._tick_calibration_check()
        assert len(zero_calls) == 2, "must re-fire on the next stable window"
    finally:
        app._acq._countdown_id = None
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k tick_calibration_check -v`
Expected: FAIL — the existing implementation still reads `_imu.get_state()["angles"]`, so
monkeypatching `_imu.is_stationary` has no effect and the assertions on `zero_calls` fail.

- [ ] **Step 3: Replace the internal fused-angle logic with the new check**

In `pendulastic_app.py`, change `_tick_calibration_check` (`pendulastic_app.py:1858-1898`):

```python
# OLD
    def _tick_calibration_check(self) -> None:
        """Countdown auto-tare: continuously watch for a stable hold and
        re-tare (edge-triggered) each time a new stable window begins.
        Active only while AcquisitionPanel's countdown is running."""
        if not (_IMU_AVAIL and "imu" in self._active_sources
                and self._state == "idle"
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
                # Don't touch _calib_was_stable here: after a fire clears the
                # buffer, it's latched True and must stay latched while the
                # buffer refills with post-tare samples -- otherwise this
                # branch un-latches it every tick, and the moment the buffer
                # is full again (still genuinely stable) the edge falsely
                # re-triggers, re-taring every ~1s for one continuous hold.
                # on_countdown_start() already resets it to False at the
                # start of every countdown, so the cold-start case is fine.
                return
            pitches = [p for p, _ in self._calib_buffer]
            rolls   = [r for _, r in self._calib_buffer]
            stable = (max(pitches) - min(pitches) < _CALIB_STABILITY_RANGE_DEG
                     and max(rolls) - min(rolls) < _CALIB_STABILITY_RANGE_DEG)
            if stable and not self._calib_was_stable:
                _imu.zero()
                self._calib_ever_stable = True
                self._calib_buffer = []      # post-tare readings jump toward 0;
                                              # don't compare across the tare
                self._calib_was_stable = True
                return
            self._calib_was_stable = stable
        except Exception:
            pass

# NEW
    def _tick_calibration_check(self) -> None:
        """Countdown auto-tare: continuously watch for a stable hold and
        re-tare (edge-triggered) each time a new stable window begins.
        Active only while AcquisitionPanel's countdown is running.

        Stability is read directly from _imu.is_stationary() -- a raw
        gyro-variance + accel-magnitude check computed in
        pendulastic_imu_server.py from each connected device's own trailing
        raw-sample buffers -- rather than a fused pitch/roll buffer
        maintained here. See docs/superpowers/specs/2026-08-04-imu-stillness
        -gyro-bias-design.md Section 3.3."""
        if not (_IMU_AVAIL and "imu" in self._active_sources
                and self._state == "idle"
                and self._acq._countdown_id is not None):
            return
        try:
            stable = _imu.is_stationary()
            if stable and not self._calib_was_stable:
                _imu.zero()
                self._calib_ever_stable = True
                self._calib_was_stable = True
                return
            self._calib_was_stable = stable
        except Exception:
            pass
```

- [ ] **Step 4: Remove the now-dead fused-angle state and constants**

In `pendulastic_app.py`, remove `self._calib_buffer: list = []` from `App.__init__`
(`pendulastic_app.py:1155`) and the reset of it in `on_countdown_start`
(`pendulastic_app.py:1307`) — `_calib_was_stable`/`_calib_ever_stable` stay (still used by the new
logic and by `is_imu_calibrated()`):

```python
# OLD (App.__init__)
        self._calib_buffer:      list = []     # trailing (pitch, roll) samples during countdown
        self._calib_was_stable:  bool = False   # edge-trigger state for auto-tare
        self._calib_ever_stable: bool = False   # True once calibrated this countdown

# NEW (App.__init__)
        self._calib_was_stable:  bool = False   # edge-trigger state for auto-tare
        self._calib_ever_stable: bool = False   # True once calibrated this countdown
```

```python
# OLD (on_countdown_start)
        self._calib_buffer = []
        self._calib_was_stable = False
        self._calib_ever_stable = False

# NEW (on_countdown_start)
        self._calib_was_stable = False
        self._calib_ever_stable = False
```

In `pendulastic_app.py`, remove the now-unused module constants
(`pendulastic_app.py:108-109`):

```python
# OLD
_CALIB_STABILITY_RANGE_DEG = 2.0   # max peak-to-peak pitch/roll swing to count as "stable"
_CALIB_BUFFER_SAMPLES = 20         # ~1s of samples at the 50ms _tick() cadence
_MAX_CALIB_EXTENSION_S = 5         # extra seconds beyond the base 5s countdown before asking

# NEW
_MAX_CALIB_EXTENSION_S = 5         # extra seconds beyond the base 5s countdown before asking
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k tick_calibration_check -v`
Expected: all pass

- [ ] **Step 6: Run the full `test_app.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -v`
Expected: all pass (any test that referenced `App._calib_buffer` or `_CALIB_STABILITY_RANGE_DEG`
directly will need updating — search for `_calib_buffer` in `tests/test_app.py` first and remove/
adapt any such references)

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: gate live auto-tare on raw gyro/accel stillness, not fused pitch/roll"
```

---

### Task 4: Offline wiring — `imu_calibration_tuner.py`'s `replay_trial()`

**Files:**
- Modify: `imu_calibration_tuner.py` (`_RoleState`, `replay_trial`)
- Test: `tests/test_imu_calibration_tuner.py`

**Interfaces:**
- Consumes: `_is_stationary_window` (Task 2), imported from `pendulastic_imu_server` alongside the
  existing `GYRO_BIAS_WINDOW_S`/`GYRO_BIAS_MIN_SAMPLES` imports
- Produces: no new public interface — `replay_trial()`'s signature and return shape are unchanged;
  only its internal calibration-gating signal changes.

- [ ] **Step 1: Update the shared test helpers to include continuous accel samples**

The existing `_solo_hold_then_burst_samples()` and `_solo_hold_with_bias_then_burst_samples()`
(`tests/test_imu_calibration_tuner.py:8-35, 52-80`) each inject exactly **one** accel sample, at
`t=0`. Once `replay_trial()`'s stability gate also requires the accel buffer to span the full
`GYRO_BIAS_WINDOW_S` (Step 3 below), a single accel sample can never satisfy that — real devices
stream accel continuously, and this was a simplification for testing hand-computed rotation, not a
realistic signal. Both helpers must inject continuous accel samples at the same cadence as gyro.

In `tests/test_imu_calibration_tuner.py`, change:

```python
# OLD
def _solo_hold_then_burst_samples():
    """Synthetic single-phone (distal) raw log: hold still for 1s (seeds AHRS
    to identity via gravity_seed, then holds), then a scripted 0.5s gyro burst
    of exactly 2.0 rad/s around Y — a known, hand-computable rotation."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    # Seed once, then hold (gyro ~0) for 1.0s.
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
    n_hold = 100
    for i in range(n_hold):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    # Deliberate burst: 2.0 rad/s around Y for 0.5s (50 steps) -> 1.0 rad total.
    n_burst = 50
    for i in range(n_burst):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
    # Settle: hold again so there's enough trailing data.
    for i in range(100):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
    return samples

# NEW
def _solo_hold_then_burst_samples():
    """Synthetic single-phone (distal) raw log: hold still for 1s (seeds AHRS
    to identity via gravity_seed, then holds), then a scripted 0.5s gyro burst
    of exactly 2.0 rad/s around Y — a known, hand-computable rotation. Accel
    streams continuously (not just once at t=0) at gravity, matching a real
    device, since the stillness gate now requires a full accel window too."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    n_hold = 100
    for i in range(n_hold):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    # Deliberate burst: 2.0 rad/s around Y for 0.5s (50 steps) -> 1.0 rad total.
    n_burst = 50
    for i in range(n_burst):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    # Settle: hold again so there's enough trailing data.
    for i in range(100):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    return samples
```

```python
# OLD
def _solo_hold_with_bias_then_burst_samples(bias):
    """Like _solo_hold_then_burst_samples, but every gyro sample -- hold,
    burst, and settle alike -- carries a constant additive bias, as a real
    stationary MEMS gyro would report (it doesn't only appear while still).
    The hold phase is what the gyro-bias calibration should measure from;
    if correctly subtracted, the burst should still integrate to the same
    true rotation as the zero-bias case."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    bx, by, bz = bias
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
    n_hold = 100
    for i in range(n_hold):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, by, bz], "phone_ts_ms": ts_ms})
    n_burst = 50
    for i in range(n_burst):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, 2.0 + by, bz], "phone_ts_ms": ts_ms})
    for i in range(100):
        t += dt; ts_ms += 10
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, by, bz], "phone_ts_ms": ts_ms})
    return samples

# NEW
def _solo_hold_with_bias_then_burst_samples(bias):
    """Like _solo_hold_then_burst_samples, but every gyro sample -- hold,
    burst, and settle alike -- carries a constant additive bias, as a real
    stationary MEMS gyro would report (it doesn't only appear while still).
    The hold phase is what the gyro-bias calibration should measure from;
    if correctly subtracted, the burst should still integrate to the same
    true rotation as the zero-bias case. Accel streams continuously at
    gravity throughout, matching a real device."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    bx, by, bz = bias
    n_hold = 100
    for i in range(n_hold):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, by, bz], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    n_burst = 50
    for i in range(n_burst):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, 2.0 + by, bz], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    for i in range(100):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [bx, by, bz], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10
    return samples
```

- [ ] **Step 2: Run the existing tests to confirm the helper change alone doesn't break anything**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -v`
Expected: all pass (still using the *old* fused-angle stability gate at this point — this step only
proves the helper rewrite is behavior-preserving before the gate itself changes in Step 4)

- [ ] **Step 3: Write the new failing tests**

Add to `tests/test_imu_calibration_tuner.py`. This test needs the actual
`GYRO_STATIONARY_MAX_RAD_S`/`ACCEL_STATIONARY_MAX_MPS2` values (Task 1's output) to scale its
synthetic handling motion relative to them rather than hardcoding a literal that might land on
either side of whatever Task 1 picked — add `import pendulastic_imu_server as imu` to this test
file's existing imports (`tests/test_imu_calibration_tuner.py:1-5`) alongside the existing `import
imu_calibration_tuner as tuner`:

```python
def _solo_handling_then_hold_then_burst_samples():
    """Synthetic single-phone (distal) raw log with a REALISTIC contamination
    scenario: 1.5s of examiner handling (gyro OSCILLATING DIRECTION on one
    axis, at 3x GYRO_STATIONARY_MAX_RAD_S -- comparable to the 12.7 deg/s
    case that motivated this fix -- with accel also swinging past
    ACCEL_STATIONARY_MAX_MPS2, i.e. NOT genuinely still), then a genuine
    1.0s still hold, then the same scripted burst as
    _solo_hold_then_burst_samples(). The bias calibration must fire from the
    genuine hold, never from the handling window. Scaled relative to the
    actual thresholds (not a hardcoded literal) so this test stays correct
    regardless of what Task 1 picked. Oscillating direction, not just
    varying magnitude, matches how _is_stationary_window's per-axis check
    actually works (see Task 2)."""
    samples = []
    t = 0.0
    dt = 0.01
    ts_ms = 0
    gyro_amp = imu.GYRO_STATIONARY_MAX_RAD_S * 3.0
    accel_half_amp = imu.ACCEL_STATIONARY_MAX_MPS2 * 1.5

    n_handling = 150
    for i in range(n_handling):
        gv = [gyro_amp, 0.0, 0.0] if i % 2 == 0 else [-gyro_amp, 0.0, 0.0]
        av = [0.0, 0.0, 9.81 + accel_half_amp] if i % 2 == 0 else [0.0, 0.0, 9.81 - accel_half_amp]
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": av, "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": gv, "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    n_hold = 100
    for i in range(n_hold):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    n_burst = 50
    for i in range(n_burst):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    for i in range(100):
        samples.append({"t": t, "role": "distal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts_ms})
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts_ms})
        t += dt; ts_ms += 10

    return samples


def test_replay_trial_ignores_handling_window_when_calibrating_bias():
    """The core regression test for this fix: a pre-burst window with real
    (not fused-angle-smoothed) raw gyro/accel handling motion must not be
    averaged into gyro_bias. Only the genuine still hold that follows it
    should be used -- so the final swing angle must match the clean,
    no-handling control run (_solo_hold_then_burst_samples), not be distorted
    by treating the handling window's motion as "bias.\""""
    params = {"beta": 0.041, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    clean_samples = _solo_hold_then_burst_samples()
    handled_samples = _solo_handling_then_hold_then_burst_samples()

    t_clean, angle_clean = tuner.replay_trial(clean_samples, params)
    t_handled, angle_handled = tuner.replay_trial(handled_samples, params)

    assert abs(angle_handled[-1] - angle_clean[-1]) < 1.0, (
        f"handling-contaminated run ({angle_handled[-1]:.2f} deg) should match "
        f"the clean control ({angle_clean[-1]:.2f} deg) -- the handling window "
        f"must be rejected by the stillness gate, not averaged into gyro_bias")
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -k handling_window -v`
Expected: FAIL — the current fused-pitch/roll stability gate in `replay_trial()` treats the
oscillating-but-fused-angle-smoothed handling window as stable (or at least does not reject it on
raw-signal grounds), so `angle_handled[-1]` diverges from `angle_clean[-1]` by more than the
assertion's tolerance.

- [ ] **Step 5: Replace the stability-gate imports and block**

In `imu_calibration_tuner.py`, change the imports (`imu_calibration_tuner.py:20-24`):

```python
# OLD
from pendulastic_imu_server import (
    MadgwickAHRS, _gravity_seed, _qconj, _qmul, _quat_to_euler_deg, wrap180,
    _FLEX_CAPTURE_THRESHOLD, ROLE_PROXIMAL, ROLE_DISTAL,
    GYRO_BIAS_WINDOW_S, GYRO_BIAS_MIN_SAMPLES,
)

# NEW
from pendulastic_imu_server import (
    MadgwickAHRS, _gravity_seed, _qconj, _qmul, _quat_to_euler_deg, wrap180,
    _FLEX_CAPTURE_THRESHOLD, ROLE_PROXIMAL, ROLE_DISTAL,
    GYRO_BIAS_WINDOW_S, GYRO_BIAS_MIN_SAMPLES, _is_stationary_window,
)
```

Remove the now-unused fused-angle stability constants (added just above `TUNING_GRID`):

```python
# OLD
CALIB_STABILITY_RANGE_DEG = 2.0
CALIB_STABILITY_WINDOW_S = 1.0

TUNING_GRID = [

# NEW
TUNING_GRID = [
```

In `_RoleState.__init__`, change:

```python
# OLD
        self.gyro_bias: np.ndarray = np.zeros(3)
        self.gyro_hold_buf: list = []   # [(t, raw_v), ...]
        # Mirrors pendulastic_app.py's _calib_buffer / _calib_was_stable:
        # trailing (t, pitch, roll) samples used to edge-trigger gyro-bias
        # (re-)calibration only on a genuinely still hold, not merely
        # "below the flex-axis motion threshold".
        self.stability_buf: list = []   # [(t, pitch_deg, roll_deg), ...]
        self.calib_was_stable = False

# NEW
        self.gyro_bias: np.ndarray = np.zeros(3)
        self.gyro_hold_buf: list = []   # [(t, raw_v), ...]
        # Trailing raw-accel buffer for _is_stationary_window()'s accel
        # -magnitude check. Mirrors gyro_hold_buf.
        self.accel_hold_buf: list = []   # [(t, raw_v), ...]
        self.calib_was_stable = False
```

In `replay_trial()`, change the stability-gate block (`imu_calibration_tuner.py:208-267`):

```python
# OLD
            # Gyro-bias calibration, gated on genuine stillness rather than
            # "below the (much coarser) flex-axis motion threshold" — the
            # pre-release window is the examiner actively gripping and
            # positioning the limb, which routinely hits tens of deg/s, well
            # under _FLEX_CAPTURE_THRESHOLD's ~57 deg/s but nowhere near
            # actually still. Averaging that whole window measured handling
            # motion, not sensor bias (confirmed: one trial's "bias" came out
            # 12.7 deg/s, an order of magnitude above a real MEMS offset, and
            # subtracting it distorted the swing instead of correcting it).
            #
            # Mirrors pendulastic_app.py's _tick_calibration_check exactly:
            # a trailing ~1s buffer of fused pitch/roll must have <2 deg
            # peak-to-peak range in BOTH before a window counts as "stable";
            # calibration (re-)fires edge-triggered, only on the tick
            # stability is newly confirmed, off a trailing raw-gyro buffer
            # covering that same still window. Only runs pre-onset, matching
            # live's countdown-only gating (_tick_calibration_check no-ops
            # once _state != "idle", i.e. once recording/swinging starts).
            if not zero_captured:
                roll_deg, pitch_deg, _yaw_deg = _quat_to_euler_deg(st.ahrs.q)
                st.stability_buf.append((samp["t"], pitch_deg, roll_deg))
                stab_cutoff = samp["t"] - CALIB_STABILITY_WINDOW_S
                st.stability_buf = [(t, p, r) for t, p, r in st.stability_buf
                                    if t >= stab_cutoff]
                # Require the buffer to actually SPAN the full window, not
                # just contain some samples — a burst of readings a few ms
                # apart could otherwise satisfy a bare "has entries" check.
                spans_window = (st.stability_buf and
                                (samp["t"] - st.stability_buf[0][0])
                                >= CALIB_STABILITY_WINDOW_S * 0.95)
                if spans_window:
                    pitches = [p for _, p, _ in st.stability_buf]
                    rolls = [r for _, _, r in st.stability_buf]
                    stable = (max(pitches) - min(pitches) < CALIB_STABILITY_RANGE_DEG
                             and max(rolls) - min(rolls) < CALIB_STABILITY_RANGE_DEG)
                    if stable and not st.calib_was_stable:
                        import os as _os
                        if _os.environ.get("IMU_DEBUG_BIAS"):
                            print(f"[DEBUG] calib fire role={role} t={samp['t']:.3f} "
                                  f"n_hold_buf={len(st.gyro_hold_buf)} "
                                  f"range=({max(pitches)-min(pitches):.2f},"
                                  f"{max(rolls)-min(rolls):.2f})")
                        if len(st.gyro_hold_buf) >= GYRO_BIAS_MIN_SAMPLES:
                            st.gyro_bias = np.mean(
                                [vv for _, vv in st.gyro_hold_buf], axis=0)
                            if _os.environ.get("IMU_DEBUG_BIAS"):
                                print(f"[DEBUG]   -> gyro_bias={st.gyro_bias}")
                        st.stability_buf = []   # don't compare across the tare
                        st.calib_was_stable = True
                    else:
                        st.calib_was_stable = stable

            # Trailing raw-gyro buffer the calibration above reads from.
            # Appended for every tick regardless of stability state (it must
            # hold RAW samples, or a stale bias would only ever measure its
            # own residual) so the window is ready whenever stability fires.
            st.gyro_hold_buf.append((samp["t"], v))
            bias_cutoff = samp["t"] - GYRO_BIAS_WINDOW_S
            st.gyro_hold_buf = [(t, vv) for t, vv in st.gyro_hold_buf
                                if t >= bias_cutoff]

            if st.accel is not None:
                st.ahrs.update(v - st.gyro_bias, st.accel, st.mag, dt)

# NEW
            # Gyro-bias calibration, gated on genuine raw-signal stillness --
            # low raw gyro variance AND stable raw accel magnitude over the
            # trailing window -- rather than a fused-angle proxy or the
            # (much coarser) flex-axis motion threshold. The pre-release
            # window is often the examiner actively gripping and positioning
            # the limb; averaging that whole window measured handling
            # motion, not sensor bias (confirmed: one trial's "bias" came
            # out 12.7 deg/s, an order of magnitude above a real MEMS
            # offset, and subtracting it distorted the swing instead of
            # correcting it). See _is_stationary_window() in
            # pendulastic_imu_server.py, shared verbatim with the live path.
            # Only runs pre-onset, matching live's countdown-only gating
            # (_tick_calibration_check no-ops once _state != "idle").
            if not zero_captured:
                stable = _is_stationary_window(st.gyro_hold_buf, st.accel_hold_buf, samp["t"])
                if stable and not st.calib_was_stable:
                    import os as _os
                    if _os.environ.get("IMU_DEBUG_BIAS"):
                        print(f"[DEBUG] calib fire role={role} t={samp['t']:.3f} "
                              f"n_hold_buf={len(st.gyro_hold_buf)}")
                    if len(st.gyro_hold_buf) >= GYRO_BIAS_MIN_SAMPLES:
                        st.gyro_bias = np.mean(
                            [vv for _, vv in st.gyro_hold_buf], axis=0)
                        if _os.environ.get("IMU_DEBUG_BIAS"):
                            print(f"[DEBUG]   -> gyro_bias={st.gyro_bias}")
                    st.calib_was_stable = True
                else:
                    st.calib_was_stable = stable

            # Trailing raw-gyro/accel buffers the calibration above reads
            # from. Appended for every tick regardless of stability state
            # (they must hold RAW samples, or a stale bias would only ever
            # measure its own residual) so the window is ready whenever
            # stability fires.
            st.gyro_hold_buf.append((samp["t"], v))
            bias_cutoff = samp["t"] - GYRO_BIAS_WINDOW_S
            st.gyro_hold_buf = [(t, vv) for t, vv in st.gyro_hold_buf
                                if t >= bias_cutoff]

            if st.accel is not None:
                st.ahrs.update(v - st.gyro_bias, st.accel, st.mag, dt)
```

Also add the accel-branch buffer maintenance. In `replay_trial()`'s `elif sensor == "accel":` branch
(the `if sensor == "accel": st.accel = v; ...` block near the top of the per-sample loop), change:

```python
# OLD
        if sensor == "accel":
            st.accel = v
            if not st.seeded:
                if params["gravity_seed"]:
                    st.ahrs.q = _gravity_seed(v)
                st.seeded = True

# NEW
        if sensor == "accel":
            st.accel = v
            st.accel_hold_buf.append((samp["t"], v))
            bias_cutoff = samp["t"] - GYRO_BIAS_WINDOW_S
            st.accel_hold_buf = [(t, vv) for t, vv in st.accel_hold_buf
                                 if t >= bias_cutoff]
            if not st.seeded:
                if params["gravity_seed"]:
                    st.ahrs.q = _gravity_seed(v)
                st.seeded = True
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -v`
Expected: all pass, including the pre-existing `test_replay_trial_subtracts_calibrated_gyro_bias`
(now exercised against the updated helpers from Step 1) and the new
`test_replay_trial_ignores_handling_window_when_calibrating_bias`

- [ ] **Step 7: Commit**

```bash
git add imu_calibration_tuner.py tests/test_imu_calibration_tuner.py
git commit -m "feat: gate offline gyro-bias calibration on raw stillness, add contamination regression test"
```

---

### Task 5: Component B — accelerometer drift investigation

**Files:**
- Create: `analyze_accel_drift.py` (standalone script — not part of the pytest suite)
- Test: `tests/test_analyze_accel_drift.py` (covers only the pure double-integration math, not the
  file-loading/plotting/reporting driver)

**Interfaces:**
- Consumes: `tune_imu.py`'s `load_raw_log(path: str) -> list`, `imu_calibration_tuner.replay_trial`,
  `pendulastic_imu_server._is_stationary_window` (Task 2)
- Produces: `double_integrate_drift(t: np.ndarray, accel_world: np.ndarray, stationary_mask:
  np.ndarray) -> tuple[np.ndarray, np.ndarray]` (velocity, displacement arrays)

- [ ] **Step 1: Write the failing test for the core math**

Create `tests/test_analyze_accel_drift.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np

import analyze_accel_drift as drift


def test_double_integrate_drift_zero_accel_gives_zero_velocity_and_displacement():
    t = np.arange(0, 1.0, 0.01)
    accel_world = np.zeros((len(t), 3))
    stationary_mask = np.ones(len(t), dtype=bool)
    vel, disp = drift.double_integrate_drift(t, accel_world, stationary_mask)
    np.testing.assert_allclose(vel, 0.0, atol=1e-9)
    np.testing.assert_allclose(disp, 0.0, atol=1e-9)


def test_double_integrate_drift_constant_accel_matches_kinematics():
    """With a genuinely constant 1.0 m/s^2 in x over 1.0s, starting and
    ending at rest is NOT the physical scenario here -- this checks the raw
    (uncorrected) double integration matches high-school kinematics
    (v = a*t, x = 0.5*a*t^2) before any zero-velocity correction is applied,
    i.e. with an all-False stationary_mask (no correction reference points)."""
    t = np.arange(0, 1.0, 0.001)
    accel_world = np.zeros((len(t), 3))
    accel_world[:, 0] = 1.0
    stationary_mask = np.zeros(len(t), dtype=bool)
    vel, disp = drift.double_integrate_drift(t, accel_world, stationary_mask)
    np.testing.assert_allclose(vel[-1, 0], 1.0 * t[-1], atol=0.02)
    np.testing.assert_allclose(disp[-1, 0], 0.5 * 1.0 * t[-1] ** 2, atol=0.02)


def test_double_integrate_drift_zupt_correction_pulls_velocity_to_zero_at_still_points():
    """A small constant accel offset (simulating uncorrected sensor drift)
    integrated over a window that starts and ends in a verified-stationary
    region: naive integration would leave nonzero velocity at the second
    still point; the ZUPT-style correction should report ~zero drift there
    since that's exactly what it's designed to null out at each stationary
    checkpoint."""
    t = np.arange(0, 2.0, 0.01)
    accel_world = np.zeros((len(t), 3))
    accel_world[:, 0] = 0.05   # small constant drift-like offset
    stationary_mask = np.zeros(len(t), dtype=bool)
    stationary_mask[:20] = True     # still for the first 0.2s
    stationary_mask[-20:] = True    # still again for the last 0.2s
    vel, disp = drift.double_integrate_drift(t, accel_world, stationary_mask)
    assert abs(vel[-1, 0]) < 1e-6, (
        "velocity at a verified-stationary checkpoint must be corrected to zero")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests\test_analyze_accel_drift.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analyze_accel_drift'`

- [ ] **Step 3: Write the script**

Create `analyze_accel_drift.py`:

```python
"""
analyze_accel_drift.py
=======================
One-off diagnostic: for each raw IMU trial log, double-integrates raw
accelerometer data (world-frame, gravity-subtracted) into velocity and
displacement, using verified-stationary windows (the same raw-signal
stillness check gyro-bias calibration uses) as zero-velocity-update (ZUPT)
reference points, to directly measure how much drift accumulates between
them.

This is a diagnostic only -- it does not feed back into or correct the
fused-angle pipeline. It exists to characterize whether/how much
accelerometer drift contributes to the RMSE-vs-OptiTrack problem.

Usage:
    .venv\\Scripts\\python.exe analyze_accel_drift.py <raw_log.jsonl> [<raw_log2.jsonl> ...]
"""
from __future__ import annotations

import sys

import numpy as np

from imu_calibration_tuner import replay_trial
from pendulastic_imu_server import _is_stationary_window, GYRO_BIAS_WINDOW_S


def double_integrate_drift(t: np.ndarray, accel_world: np.ndarray,
                            stationary_mask: np.ndarray) -> tuple:
    """Double-integrate world-frame linear accel into velocity and
    displacement, applying a zero-velocity-update correction at each
    verified-stationary sample: velocity is reset to 0 there, and the
    reset amount is linearly redistributed backward over the preceding
    non-stationary run so the correction doesn't appear as a discontinuous
    jump. This makes the velocity AT each stationary checkpoint directly
    readable as "how much drift accumulated since the previous checkpoint"
    -- the diagnostic quantity this script reports."""
    n = len(t)
    vel = np.zeros((n, 3))
    disp = np.zeros((n, 3))
    run_start = 0
    for i in range(1, n):
        dt = t[i] - t[i - 1]
        vel[i] = vel[i - 1] + accel_world[i] * dt
        if stationary_mask[i]:
            drift_amount = vel[i].copy()
            run_len = i - run_start
            if run_len > 0:
                for j in range(run_start, i + 1):
                    frac = (j - run_start) / run_len
                    vel[j] = vel[j] - frac * drift_amount
            run_start = i
    for i in range(1, n):
        dt = t[i] - t[i - 1]
        disp[i] = disp[i - 1] + vel[i] * dt
    return vel, disp


def _world_frame_linear_accel(raw_samples: list, params: dict) -> tuple:
    """Replay raw_samples to get per-tick orientation, then rotate each
    raw accel sample into the world frame and subtract gravity. Returns
    (t, accel_world, stationary_mask) for the distal role."""
    # replay_trial() gives the final swing angle series, not per-sample
    # orientation -- for this diagnostic we only need a rough per-sample
    # rotation, so we run the same accel/gyro/mag stream through a fresh
    # MadgwickAHRS instance directly here rather than modifying
    # replay_trial()'s return contract.
    from pendulastic_imu_server import MadgwickAHRS, _gravity_seed, _qconj, _qmul

    ahrs = MadgwickAHRS(beta=params["beta"])
    seeded = False
    gyro_bias = np.zeros(3)
    gyro_hold_buf, accel_hold_buf = [], []
    calib_was_stable = False
    last_ts = None

    ts_list, accel_world_list, stationary_list = [], [], []
    for samp in raw_samples:
        if samp["role"] != "distal":
            continue
        v = np.asarray(samp["v"], dtype=float)
        if samp["sensor"] == "accel":
            accel_hold_buf.append((samp["t"], v))
            accel_hold_buf[:] = [(t, vv) for t, vv in accel_hold_buf
                                 if t >= samp["t"] - GYRO_BIAS_WINDOW_S]
            if not seeded:
                ahrs.q = _gravity_seed(v)
                seeded = True
            q = ahrs.q
            qv = np.array([0.0, *v])
            world = _qmul(_qmul(q, qv), _qconj(q))[1:]
            gravity = np.array([0.0, 0.0, 9.81])
            ts_list.append(samp["t"])
            accel_world_list.append(world - gravity)
            stable = _is_stationary_window(gyro_hold_buf, accel_hold_buf, samp["t"])
            stationary_list.append(stable)
        elif samp["sensor"] == "gyro":
            ts = samp.get("phone_ts_ms") or 0
            dt = (ts - last_ts) / 1000.0 if (last_ts is not None and ts) else None
            if dt is None or not (0.0 < dt < 0.5):
                dt = 0.01
            last_ts = ts
            gyro_hold_buf.append((samp["t"], v))
            gyro_hold_buf[:] = [(t, vv) for t, vv in gyro_hold_buf
                                if t >= samp["t"] - GYRO_BIAS_WINDOW_S]
            stable = _is_stationary_window(gyro_hold_buf, accel_hold_buf, samp["t"])
            if stable and not calib_was_stable and len(gyro_hold_buf) >= 5:
                gyro_bias = np.mean([vv for _, vv in gyro_hold_buf], axis=0)
            calib_was_stable = stable
            ahrs.update(v - gyro_bias, None, None, dt)

    return (np.array(ts_list), np.array(accel_world_list), np.array(stationary_list))


def analyze_file(path: str) -> None:
    from tune_imu import load_raw_log

    samples = load_raw_log(path)
    if not samples:
        print(f"{path}: no samples, skipping")
        return
    params = {"beta": 0.041, "ema_alpha": 1.0,
             "flex_axis_capture": True, "gravity_seed": True}
    t, accel_world, stationary_mask = _world_frame_linear_accel(samples, params)
    if len(t) < 2:
        print(f"{path}: not enough accel samples, skipping")
        return
    vel, disp = double_integrate_drift(t, accel_world, stationary_mask)
    peak_disp = float(np.max(np.linalg.norm(disp, axis=1)))
    peak_vel_drift = float(np.max(np.linalg.norm(vel, axis=1)))
    print(f"{path}: peak displacement={peak_disp:.4f} m, "
          f"peak inter-checkpoint velocity drift={peak_vel_drift:.4f} m/s, "
          f"n_stationary_checkpoints={int(stationary_mask.sum())}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for path in sys.argv[1:]:
        analyze_file(path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests\test_analyze_accel_drift.py -v`
Expected: all pass

- [ ] **Step 5: Manual step — run against real recordings**

Run: `.venv\Scripts\python.exe analyze_accel_drift.py data\*_imu_raw.jsonl`

(On Windows PowerShell, glob expansion may need
`Get-ChildItem data\*_imu_raw.jsonl | ForEach-Object { .venv\Scripts\python.exe
analyze_accel_drift.py $_.FullName }` instead.)

Read the printed peak-displacement/peak-velocity-drift numbers. A pendulum-test knee swing has a
known, bounded physical displacement (the lower leg's arc); compare the printed peak displacement
against that expectation. Record the finding (in a commit message or a short note) — this is the
deliverable: whether accel drift plausibly explains part of the RMSE problem, and roughly how much.
No pipeline change follows from this automatically; a large finding is a candidate for a future,
separate task.

- [ ] **Step 6: Commit**

```bash
git add analyze_accel_drift.py tests/test_analyze_accel_drift.py
git commit -m "feat: add one-off accelerometer double-integration drift investigation"
```

---

### Task 6: Component C — extract `reliability_stats.py`

**Files:**
- Create: `reliability_stats.py`
- Test: `tests/test_reliability_stats.py`

**Interfaces:**
- Produces: `bland_altman(x: np.ndarray, y: np.ndarray) -> dict`, `icc_one_way(groups: list) ->
  dict`, `icc_two_way(x: np.ndarray, y: np.ndarray) -> dict` (same names as `validate_controls.py`'s
  private `_bland_altman`/`_icc_one_way`/`_icc_two_way`, made public since this module's whole
  purpose is being imported elsewhere)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reliability_stats.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np

import reliability_stats as rs


def test_bland_altman_zero_diff_gives_zero_bias_and_loa():
    x = np.array([10.0, 20.0, 30.0, 40.0])
    y = x.copy()
    result = rs.bland_altman(x, y)
    assert result["bias"] == 0.0
    assert result["sd"] == 0.0
    assert result["loa_lo"] == 0.0
    assert result["loa_hi"] == 0.0


def test_bland_altman_constant_offset_gives_that_bias():
    x = np.array([10.0, 20.0, 30.0, 40.0])
    y = x + 5.0
    result = rs.bland_altman(x, y)
    assert abs(result["bias"] - 5.0) < 1e-9
    assert result["sd"] == 0.0


def test_bland_altman_drops_nonfinite_pairs():
    x = np.array([10.0, np.nan, 30.0])
    y = np.array([10.0, 20.0, np.nan])
    result = rs.bland_altman(x, y)
    assert len(result["diffs"]) == 1


def test_icc_one_way_identical_groups_gives_high_icc():
    groups = [[10.0, 10.1, 9.9], [20.0, 20.2, 19.8], [30.0, 30.1, 29.9]]
    result = rs.icc_one_way(groups)
    assert result["icc"] > 0.9


def test_icc_one_way_pure_noise_gives_low_icc():
    rng = np.random.RandomState(0)
    groups = [list(rng.normal(0, 10, 5)) for _ in range(5)]
    result = rs.icc_one_way(groups)
    assert result["icc"] < 0.5


def test_icc_one_way_too_few_groups_returns_nan():
    result = rs.icc_one_way([[1.0, 2.0]])
    assert np.isnan(result["icc"])


def test_icc_two_way_identical_series_gives_icc_near_one():
    x = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
    y = x.copy()
    result = rs.icc_two_way(x, y)
    assert result["icc"] > 0.95
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_reliability_stats.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'reliability_stats'`

- [ ] **Step 3: Create the module, copying the 3 functions from `validate_controls.py`**

Create `reliability_stats.py`. Copy `_bland_altman` (`validate_controls.py:157-167`),
`_icc_one_way` (`validate_controls.py:63-107`), and `_icc_two_way` (`validate_controls.py:110-154`)
verbatim, renamed without the leading underscore (this module's whole purpose is being imported, so
these are its public API), with the file's own imports:

```python
"""
reliability_stats.py
=====================
Concurrent-validity and reliability statistics (Bland-Altman, ICC), extracted
from validate_controls.py so they can be reused without importing that file
-- which cannot currently run: three of its own module-level imports
(gen_figures.py, gen_fig_best_trials.py, gen_fig_A_all_participants.py) do
not exist anywhere in the repo. These three functions have no dependency on
those missing files; this module exists purely to make them importable
again. See docs/superpowers/specs/2026-08-04-imu-stillness-gyro-bias-design.md
Section 5.
"""
from __future__ import annotations

import numpy as np
from scipy import stats


def icc_one_way(groups: list) -> dict:
    """
    ICC(1,1): one-way random effects model.
    groups: list of per-subject arrays, each with >= 2 observations.
    Returns dict: icc, ci_lo, ci_hi, sem, mdc95, n_subjects, n_obs
    """
    groups = [np.asarray(g, float) for g in groups if len(g) >= 2]
    n_s = len(groups)
    if n_s < 2:
        return dict(icc=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    sem=np.nan, mdc95=np.nan, n_subjects=n_s, n_obs=0)

    all_v  = np.concatenate(groups)
    N      = len(all_v)
    grand  = np.mean(all_v)

    SS_b = float(sum(len(g) * (np.mean(g) - grand) ** 2 for g in groups))
    SS_w = float(sum(np.sum((g - np.mean(g)) ** 2) for g in groups))
    df_b, df_w = n_s - 1, N - n_s

    if df_b <= 0 or df_w <= 0:
        return dict(icc=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    sem=np.nan, mdc95=np.nan, n_subjects=n_s, n_obs=N)

    MS_b = SS_b / df_b
    MS_w = SS_w / df_w

    k0 = (N - sum(len(g) ** 2 for g in groups) / N) / (n_s - 1)

    denom   = MS_b + (k0 - 1) * MS_w
    icc_val = max(0.0, (MS_b - MS_w) / denom) if denom > 0 else np.nan

    F0  = MS_b / (MS_w + 1e-12)
    F_L = F0 / stats.f.ppf(0.975, df_b, df_w)
    F_U = F0 * stats.f.ppf(0.975, df_b, df_w)
    ci_lo = max(0.0, (F_L - 1) / (F_L + k0 - 1)) if (F_L + k0 - 1) > 0 else 0.0
    ci_hi = (F_U - 1) / (F_U + k0 - 1) if (F_U + k0 - 1) > 0 else np.nan

    sem = float(np.sqrt(MS_w))
    mdc95 = sem * 1.96 * np.sqrt(2)

    return dict(icc=icc_val, ci_lo=ci_lo, ci_hi=ci_hi, sem=sem, mdc95=mdc95,
               n_subjects=n_s, n_obs=N)


def icc_two_way(x: np.ndarray, y: np.ndarray) -> dict:
    """
    ICC(2,1): two-way mixed effects, single measure, absolute agreement.
    Returns dict: icc, ci_lo, ci_hi, sem, sdc95, n
    """
    x, y = np.asarray(x, float), np.asarray(y, float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 2:
        return dict(icc=np.nan, ci_lo=np.nan, ci_hi=np.nan, sem=np.nan, sdc95=np.nan, n=n)

    data = np.stack([x, y], axis=1)
    grand = data.mean()
    subj_means = data.mean(axis=1)
    rater_means = data.mean(axis=0)

    SS_total = float(np.sum((data - grand) ** 2))
    SS_subj  = float(2 * np.sum((subj_means - grand) ** 2))
    SS_rater = float(n * np.sum((rater_means - grand) ** 2))
    SS_err   = SS_total - SS_subj - SS_rater

    df_subj, df_rater, df_err = n - 1, 1, n - 1
    if df_subj <= 0 or df_err <= 0:
        return dict(icc=np.nan, ci_lo=np.nan, ci_hi=np.nan, sem=np.nan, sdc95=np.nan, n=n)

    MS_subj  = SS_subj / df_subj
    MS_rater = SS_rater / df_rater
    MS_err   = SS_err / df_err if df_err > 0 else 0.0

    denom = MS_subj + (MS_rater - MS_err) / n
    icc_val = max(0.0, (MS_subj - MS_err) / denom) if denom > 0 else np.nan

    F0 = MS_subj / (MS_err + 1e-12)
    F_L = F0 / stats.f.ppf(0.975, df_subj, df_err)
    F_U = F0 * stats.f.ppf(0.975, df_subj, df_err)
    ci_lo = max(0.0, (F_L - 1) / (F_L + 1)) if (F_L + 1) > 0 else 0.0
    ci_hi = (F_U - 1) / (F_U + 1) if (F_U + 1) > 0 else np.nan

    sem = float(np.sqrt(MS_err))
    sdc95 = sem * 1.96 * np.sqrt(2)

    return dict(icc=icc_val, ci_lo=ci_lo, ci_hi=ci_hi, sem=sem, sdc95=sdc95, n=n)


def bland_altman(x: np.ndarray, y: np.ndarray) -> dict:
    x, y = np.asarray(x, float), np.asarray(y, float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y  = x[mask], y[mask]
    diffs = y - x
    means = (x + y) / 2.0
    bias  = float(np.mean(diffs)) if len(diffs) else float("nan")
    sd    = float(np.std(diffs, ddof=1)) if len(diffs) > 1 else 0.0
    return dict(bias=bias, sd=sd,
                loa_lo=bias - 1.96 * sd, loa_hi=bias + 1.96 * sd,
                means=means, diffs=diffs)
```

(Note: `icc_two_way`'s body is reconstructed from `validate_controls.py:110-154`'s described
ICC(2,1) two-way mixed model per its own module docstring and the earlier design-spec research —
whoever implements this task must actually open `validate_controls.py:110-154` and copy its exact
current body rather than retyping from this plan, since this plan's transcription is for structural
reference. Same applies to `icc_one_way`/`bland_altman` above — copy verbatim from the live file,
this plan's versions are close but the implementer must diff against the real source.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_reliability_stats.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add reliability_stats.py tests/test_reliability_stats.py
git commit -m "feat: extract reliability_stats.py from unrunnable validate_controls.py"
```

---

### Task 7: Component C — wire `reliability_stats` into `evaluate_all_participants.py`

**Files:**
- Modify: `evaluate_all_participants.py` (`PendulasticEvaluator`, near `_save_leaderboard`)
- Test: `tests/test_evaluate_all_participants_reliability.py`

**Interfaces:**
- Consumes: `reliability_stats.icc_one_way` (Task 6), `self.all_records: List[dict]` (existing,
  populated by `run()` before `_save_leaderboard()` — `evaluate_all_participants.py:767, 832, 849`
  — each record has keys including `family` (str), `participant` (str), `position` (str), `trial`
  (str), `abs_err` (float, one per matched peak/pit extremum, not a pre-aggregated per-trial RMSE)
- Produces: `PendulasticEvaluator._save_reliability_report(self) -> None`, writing
  `<self.output_root>/reliability_report.csv`

- [ ] **Step 1: Write the failing test**

Create `tests/test_evaluate_all_participants_reliability.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np

import evaluate_all_participants as eap


def test_save_reliability_report_writes_icc_per_family(tmp_path):
    """Given fake per-extrema abs_err records spanning >=2 trials for a
    participant/family, _save_reliability_report() must compute a per-trial
    RMSE (sqrt(mean(abs_err**2)) grouped by family+participant+position+
    trial), then ICC(1,1) across each participant's trials, and write it --
    not fail or silently skip it."""
    ev = eap.PendulasticEvaluator.__new__(eap.PendulasticEvaluator)
    ev.output_root = str(tmp_path)
    ev.all_records = [
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "1", "abs_err": 4.0},
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "1", "abs_err": 4.4},
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "2", "abs_err": 4.8},
        {"family": "pendulastic", "participant": "P001", "position": "1", "trial": "2", "abs_err": 4.9},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "1", "abs_err": 5.0},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "1", "abs_err": 5.2},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "2", "abs_err": 4.9},
        {"family": "pendulastic", "participant": "P002", "position": "1", "trial": "2", "abs_err": 5.0},
    ]

    ev._save_reliability_report()

    out_path = os.path.join(str(tmp_path), "reliability_report.csv")
    assert os.path.isfile(out_path)
    with open(out_path) as f:
        header = f.readline().strip().split(",")
    assert "family" in header
    assert "icc_rmse" in header


def test_save_reliability_report_skips_family_with_no_repeat_trials():
    """A family where every participant has exactly 1 trial has no repeat
    -measures data for ICC -- must not crash, and reports icc_rmse as blank
    rather than a fabricated value."""
    ev = eap.PendulasticEvaluator.__new__(eap.PendulasticEvaluator)
    import tempfile
    ev.output_root = tempfile.mkdtemp()
    ev.all_records = [
        {"family": "hrnet", "participant": "P001", "position": "1", "trial": "1", "abs_err": 3.0},
        {"family": "hrnet", "participant": "P002", "position": "1", "trial": "1", "abs_err": 3.5},
    ]
    ev._save_reliability_report()   # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_evaluate_all_participants_reliability.py -v`
Expected: FAIL — `AttributeError: 'PendulasticEvaluator' object has no attribute
'_save_reliability_report'`

- [ ] **Step 3: Implement `_save_reliability_report`**

In `evaluate_all_participants.py`, add near `_save_leaderboard` (`evaluate_all_participants.py:878`):

```python
    def _save_reliability_report(self) -> None:
        """For each model family, compute per-trial RMSE
        (sqrt(mean(abs_err**2)) over that trial's matched peak/pit records,
        the same formula _save_leaderboard() uses per-variant) grouped by
        (participant, position, trial), then ICC(1,1) across each
        participant's trials with >=2 -- repeat-measures reliability of that
        family's tracking quality -- using reliability_stats, extracted from
        validate_controls.py since that file cannot currently import.
        Writes reliability_report.csv alongside global_model_leaderboard.csv.
        A family with no participant having >=2 trials gets icc_rmse left
        blank rather than a fabricated value."""
        import csv
        from collections import defaultdict
        import reliability_stats

        # (family, participant, position, trial) -> list of abs_err values
        by_trial = defaultdict(list)
        for rec in self.all_records:
            key = (rec["family"], rec["participant"], rec["position"], rec["trial"])
            by_trial[key].append(rec["abs_err"])

        # family -> participant -> [per-trial RMSE, ...]
        by_family_participant = defaultdict(lambda: defaultdict(list))
        for (family, participant, position, trial), errs in by_trial.items():
            trial_rmse = float(np.sqrt(np.mean(np.array(errs) ** 2)))
            by_family_participant[family][participant].append(trial_rmse)

        out_path = os.path.join(self.output_root, "reliability_report.csv")
        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["family", "n_participants_with_repeats", "icc_rmse",
                            "icc_ci_lo", "icc_ci_hi"])
            for family, by_participant in sorted(by_family_participant.items()):
                groups = [v for v in by_participant.values() if len(v) >= 2]
                if not groups:
                    writer.writerow([family, 0, "", "", ""])
                    continue
                result = reliability_stats.icc_one_way(groups)
                writer.writerow([family, len(groups),
                                f"{result['icc']:.4f}" if not np.isnan(result["icc"]) else "",
                                f"{result['ci_lo']:.4f}" if not np.isnan(result["ci_lo"]) else "",
                                f"{result['ci_hi']:.4f}" if not np.isnan(result["ci_hi"]) else ""])
```

Wire it into `run()` (`evaluate_all_participants.py:770-777`): add `self._save_reliability_report()`
immediately after the existing `self._save_leaderboard()` call at line 777.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_evaluate_all_participants_reliability.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add evaluate_all_participants.py tests/test_evaluate_all_participants_reliability.py
git commit -m "feat: add per-family ICC reliability report to evaluate_all_participants.py"
```

---

### Task 8: Manual step — capture the pre-fix baseline

**Files:**
- None modified — verification only

- [ ] **Step 1: Confirm this runs before Task 2's live/offline changes are relied upon for real data**

This step's baseline is only meaningful if run against the pre-fix behavior. If Tasks 2-7 are
already merged by the time this runs, check out the commit immediately before Task 2's first commit
to capture the true "before" state, or note in the diff (Task 9) which commits separate the two
runs.

- [ ] **Step 2: Set the harness to cover every participant**

In `evaluate_all_participants.py`, change `TARGET_PARTICIPANTS: List[str] = ["2_"]`
(`evaluate_all_participants.py:74`) to `TARGET_PARTICIPANTS: List[str] = []` (evaluate everyone —
per the file's own comment at line 67, `[]` means "everyone"). Do not commit this change; it is
local-only for this baseline run (revert it after, or note that Task 9's post-fix run must use the
same setting for a fair diff).

- [ ] **Step 3: Run the harness and save the output**

Run: `.venv\Scripts\python.exe evaluate_all_participants.py`

Copy `<BASE_DIR>/pendulastic model analysis/all_participants/global_model_leaderboard.csv` and
`reliability_report.csv` to a baseline location, e.g.
`Model_Analysis_Outputs/baseline_pre_stillness_fix_leaderboard.csv` and
`Model_Analysis_Outputs/baseline_pre_stillness_fix_reliability.csv`, so Task 9 has something to diff
against.

- [ ] **Step 4: Commit the baseline snapshot**

```bash
git add "Model_Analysis_Outputs/baseline_pre_stillness_fix_leaderboard.csv" "Model_Analysis_Outputs/baseline_pre_stillness_fix_reliability.csv"
git commit -m "docs: capture pre-fix RMSE/reliability baseline for the stillness-gate fix"
```

---

### Task 9: Full regression run and post-fix comparison

**Files:**
- None modified — verification only

- [ ] **Step 1: Run the full automated test suite**

Run: `.venv\Scripts\pytest.exe tests\ -v --ignore=tests\test_metrics.py --ignore=tests\test_pose.py
--ignore=tests\test_stats.py --ignore=tests\test_video.py`

Expected: all pass. If the known pre-existing tkinter-singleton flake appears when run alongside
other files (documented in this repo's other plans — a different test fails each run, never the
same one twice, and always passes in isolation), re-run the specific failing test(s) individually to
confirm it's the pre-existing flake and not a real regression.

- [ ] **Step 2: Re-run the combined validation harness (post-fix)**

Ensure `TARGET_PARTICIPANTS = []` in `evaluate_all_participants.py` (same setting as Task 8, for a
fair comparison). Run: `.venv\Scripts\python.exe evaluate_all_participants.py`

- [ ] **Step 3: Diff against the Task 8 baseline**

Compare the new `global_model_leaderboard.csv`'s `pendulastic` family rows (and any other IMU
-derived family) against `Model_Analysis_Outputs/baseline_pre_stillness_fix_leaderboard.csv`, per
trial (`variant`/participant/position/trial). Confirm `rmse_deg` improves (decreases) or stays flat
for every such trial — **no trial may regress**. If any trial's RMSE increases, that is a plan
failure requiring investigation before this task can be considered complete — do not silently
accept a regression.

- [ ] **Step 4: Manual acceptance — real hardware**

Record a real trial through the live app with the countdown running, holding genuinely still: 
confirm the status text transitions from "stabilizing…" to "✓ calibrated" as it did before this
change, and that the resulting CSV starts at a plausible ~180° rather than `nan` or a visibly
distorted swing. This requires actual Sensor Stream hardware and is out of scope for the automated
test suite — flag it as the remaining manual verification step.

- [ ] **Step 5: Commit the post-fix comparison**

```bash
git add "Model_Analysis_Outputs/"
git commit -m "test: capture post-fix RMSE/reliability comparison confirming no regressions"
```
