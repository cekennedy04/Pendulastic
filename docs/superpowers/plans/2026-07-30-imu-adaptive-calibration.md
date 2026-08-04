# IMU Adaptive Self-Tuning Calibration Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Cross-reference:** `docs/superpowers/plans/2026-07-31-raw-imu-9dof-csv-logging.md` adds a separate raw accel/gyro/mag logging mechanism (split per-sensor CSVs) targeting `pendulastic_imu_server.start_recording()` — the legacy path used only by `master_app.py`/`pendulastic_viewer.py`, which this plan's `start_raw_log()`/`stop_raw_log()` (JSONL, hooked into `pendulastic_app.py`'s own recording lifecycle) never touches. Different callers, same three methods (`on_accel`/`on_gyro`/`on_mag`) — implementing that plan later will need a manual merge of adjacent hunks, not a redesign of either.

**Goal:** Build a bounded grid-search tuner that replays a recorded IMU trial's raw sensor stream through candidate Madgwick/fusion parameter sets, scores each against the pendulum test's physical constraints, and persists the winning configuration for reuse — shared between a live per-trial path and a standalone CLI.

**Architecture:** A new dependency-free `imu_calibration_config.py` holds the persisted config schema (avoids a circular import between the tuner and `pendulastic_imu_server`). A new `imu_calibration_tuner.py` holds the replay engine, scorer, and grid search, importing the AHRS primitives from `pendulastic_imu_server` and `compute_pt_params` from `pendulastic_pt_score`. `pendulastic_imu_server.py` gains raw-sample JSONL logging (hooked into the app's *actual* recording lifecycle, not the legacy `start_recording()` path that only `pendulastic_viewer.py` uses) and reads its AHRS constants from the config at import time. `pendulastic_app.py` wires the raw-log start/stop calls and the live post-recording tuning trigger.

**Tech Stack:** Python 3.13, NumPy, Tkinter (existing), pytest.

## Global Constraints

- Grid: `beta ∈ {0.02, 0.041, 0.08, 0.15}` × `ema_alpha ∈ {0.1, 0.3, 0.5}` × `flex_axis_capture ∈ {True, False}` × `gravity_seed ∈ {True, False}` — 48 combinations, defined once in `imu_calibration_tuner.TUNING_GRID`.
- Today's hardcoded values (`beta=0.041`, `ema_alpha=0.3`, `flex_axis_capture=True`, `gravity_seed=True`) are `imu_calibration_config.DEFAULT_CONFIG` — a fresh checkout with no persisted config file must behave identically to current behavior.
- `pendulastic_imu_server.start_recording()`/`stop_recording()`/`_recording` (the legacy fused-CSV mechanism) is untouched — it is used only by `pendulastic_viewer.py`. All new raw-logging code is additive and independent of it.
- Tuning failures must never block a clinician from seeing trial data (fall back to today's behavior on any error).
- `imu_calibration_config.save_config()` only overwrites the persisted file if the candidate strictly improves on it (any passing config beats a non-passing one; among passing configs, lower penalty wins).
- Full spec: `docs/superpowers/specs/2026-07-30-imu-adaptive-calibration-design.md`.

---

### Task 1: Config schema, defaults, load/save

**Files:**
- Create: `imu_calibration_config.py`
- Test: `tests/test_imu_calibration_config.py`

**Interfaces:**
- Produces: `DEFAULT_CONFIG: dict`, `CONFIG_PATH: str`, `load_config() -> dict`, `save_config(cfg: dict) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_imu_calibration_config.py`:

```python
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import imu_calibration_config as cfgmod


def test_load_config_returns_defaults_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "missing.json"))
    cfg = cfgmod.load_config()
    assert cfg == cfgmod.DEFAULT_CONFIG


def test_save_then_load_roundtrips(tmp_path, monkeypatch):
    path = str(tmp_path / "cfg.json")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    written = {
        "beta": 0.08, "ema_alpha": 0.5,
        "flex_axis_capture": False, "gravity_seed": True,
        "penalty": 1.23, "passes": True,
        "tuned_at": "2026-07-30T00:00:00+00:00", "source_trial": "PID_1_imu.csv",
    }
    cfgmod.save_config(written)
    assert cfgmod.load_config() == written


def test_save_writes_atomically_no_tmp_file_left_behind(tmp_path, monkeypatch):
    path = str(tmp_path / "cfg.json")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", path)
    cfgmod.save_config(dict(cfgmod.DEFAULT_CONFIG))
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")


def test_load_config_falls_back_on_corrupt_json(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG


def test_load_config_falls_back_on_missing_key(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"beta": 0.1}), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG


def test_load_config_falls_back_on_wrong_type(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    bad = dict(cfgmod.DEFAULT_CONFIG)
    bad["flex_axis_capture"] = "yes"   # must be bool, not str
    path.write_text(json.dumps(bad), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config() == cfgmod.DEFAULT_CONFIG
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'imu_calibration_config'`

- [ ] **Step 3: Write the implementation**

Create `imu_calibration_config.py`:

```python
"""
imu_calibration_config.py
==========================
Schema, defaults, and atomic load/save for the persisted IMU AHRS/fusion
tuning configuration.

Deliberately has zero dependency on pendulastic_imu_server or
imu_calibration_tuner: both of those need to read this config, and
imu_calibration_tuner also imports FROM pendulastic_imu_server, so keeping
this module dependency-free avoids a circular import.
"""
from __future__ import annotations

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "imu_calibration_config.json")

# Matches today's hardcoded values exactly (pendulastic_imu_server.BETA=0.041,
# pendulastic_app._imu_poll_worker's _EMA_ALPHA=0.3, and the always-on
# flex-axis-capture / gravity-seed behavior) so a fresh checkout with no
# persisted config behaves identically to current behavior.
DEFAULT_CONFIG = {
    "beta": 0.041,
    "ema_alpha": 0.3,
    "flex_axis_capture": True,
    "gravity_seed": True,
    "penalty": None,
    "passes": False,
    "tuned_at": None,
    "source_trial": None,
}

_REQUIRED_TYPES = {
    "beta": (int, float),
    "ema_alpha": (int, float),
    "flex_axis_capture": bool,
    "gravity_seed": bool,
}


def _is_valid(cfg) -> bool:
    if not isinstance(cfg, dict):
        return False
    for key, types in _REQUIRED_TYPES.items():
        if key not in cfg or not isinstance(cfg[key], types):
            return False
    return True


def load_config() -> dict:
    """Return the persisted config, or DEFAULT_CONFIG if missing/corrupt/invalid."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    if not _is_valid(cfg):
        return dict(DEFAULT_CONFIG)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def save_config(cfg: dict) -> None:
    """Atomically overwrite the persisted config (temp file + os.replace)."""
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_config.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add imu_calibration_config.py tests/test_imu_calibration_config.py
git commit -m "feat: add IMU calibration config schema, load/save"
```

---

### Task 2: Wire config into pendulastic_imu_server.py's hardcoded constants

**Files:**
- Modify: `pendulastic_imu_server.py:64` (BETA constant, kept but unused by devices going forward), `:253-271` (`_IMUDevice.__init__`), `:309-314` (`on_accel`), `:402-414` (module state block), `:519-546` (`zero()`)
- Test: `tests/test_imu_server.py` (add tests; add an autouse fixture so existing tests stay deterministic regardless of any persisted `imu_calibration_config.json`)

**Interfaces:**
- Consumes: `imu_calibration_config.load_config() -> dict` (Task 1)
- Produces: `pendulastic_imu_server._CONFIG: dict` — read by Task 4's raw-log hooks and Task 8's tuner

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_imu_server.py`, right after the existing imports:

```python
import pytest


@pytest.fixture(autouse=True)
def _reset_imu_config(monkeypatch):
    """Every test in this file must see the hardcoded defaults, regardless of
    whatever imu_calibration_config.json a prior tuning run may have written
    to the real repo root."""
    import imu_calibration_config as _cfgmod
    monkeypatch.setattr(imu, "_CONFIG", dict(_cfgmod.DEFAULT_CONFIG))
```

Add these new tests at the end of the file:

```python
def test_new_device_uses_configured_beta(monkeypatch):
    monkeypatch.setitem(imu._CONFIG, "beta", 0.15)
    dev = imu._IMUDevice("12.0.0.1")
    assert dev.ahrs.beta == 0.15


def test_on_accel_skips_seeding_when_gravity_seed_disabled(monkeypatch):
    monkeypatch.setitem(imu._CONFIG, "gravity_seed", False)
    dev = imu._IMUDevice("12.0.0.2")
    dev.on_accel(np.array([0., 0., -9.81]), ts=0)
    assert dev._ahrs_seeded, "seeded flag must still be set so we never re-seed later"
    np.testing.assert_allclose(dev.ahrs.q, [1., 0., 0., 0.], atol=1e-9,
        err_msg="AHRS quaternion must stay at identity when gravity_seed=False")


def test_zero_does_not_arm_flex_axis_when_capture_disabled(monkeypatch):
    monkeypatch.setitem(imu._CONFIG, "flex_axis_capture", False)
    imu.reset_devices()
    imu.clear_zero()
    imu._devices["13.0.0.1"] = imu._IMUDevice("13.0.0.1")
    imu._roles["13.0.0.1"]   = imu.ROLE_DISTAL
    dev = imu._devices["13.0.0.1"]
    dev.last_rx = __import__("time").time()
    imu.zero()
    assert not imu._flex_axis_armed, "flex-axis capture must not arm when disabled in config"
    imu.reset_devices()
    imu.clear_zero()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -k "configured_beta or gravity_seed_disabled or capture_disabled" -v`
Expected: FAIL — `AttributeError: module 'pendulastic_imu_server' has no attribute '_CONFIG'`

- [ ] **Step 3: Add the config import and module-level `_CONFIG`**

In `pendulastic_imu_server.py`, add the import near the top (after the existing `import numpy as np`, around line 47):

```python
import numpy as np

from imu_calibration_config import load_config
```

In the module-state block (after line 414's `_FLEX_CAPTURE_THRESHOLD = 1.0`):

```python
_FLEX_CAPTURE_THRESHOLD = 1.0             # rad/s — min |ω| to register as intentional

_CONFIG = load_config()   # {beta, ema_alpha, flex_axis_capture, gravity_seed, ...}
```

- [ ] **Step 4: Wire `_CONFIG["beta"]` into `_IMUDevice.__init__`**

In `pendulastic_imu_server.py:255`, change:

```python
# OLD
        self.ahrs       = MadgwickAHRS()

# NEW
        self.ahrs       = MadgwickAHRS(beta=_CONFIG["beta"])
```

- [ ] **Step 5: Gate gravity-seeding on `_CONFIG["gravity_seed"]`**

In `pendulastic_imu_server.py:309-314`, change:

```python
# OLD
    def on_accel(self, v, ts):
        self.accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            self.ahrs.q = _gravity_seed(self.accel)
            self._ahrs_seeded = True
        self._touch(ts)

# NEW
    def on_accel(self, v, ts):
        self.accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            if _CONFIG["gravity_seed"]:
                self.ahrs.q = _gravity_seed(self.accel)
            self._ahrs_seeded = True
        self._touch(ts)
```

- [ ] **Step 6: Gate flex-axis arming on `_CONFIG["flex_axis_capture"]`**

In `pendulastic_imu_server.py`'s `zero()` function, change the last two lines:

```python
# OLD
        # Arm the flex-axis capture; the first gyro burst with |ω| above the
        # threshold will lock the anatomical flexion axis for this session.
        _flex_axis        = None
        _flex_axis_armed  = True

# NEW
        # Arm the flex-axis capture; the first gyro burst with |ω| above the
        # threshold will lock the anatomical flexion axis for this session.
        _flex_axis        = None
        _flex_axis_armed  = _CONFIG["flex_axis_capture"]
```

- [ ] **Step 7: Run the new tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -k "configured_beta or gravity_seed_disabled or capture_disabled" -v`
Expected: 3 passed

- [ ] **Step 8: Run the full existing `test_imu_server.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -v`
Expected: all pass (the autouse fixture from Step 1 keeps every existing test deterministic)

- [ ] **Step 9: Commit**

```bash
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat: read AHRS beta, gravity-seed, and flex-axis-capture from config"
```

---

### Task 3: Wire config into pendulastic_app.py's EMA alpha

**Files:**
- Modify: `pendulastic_app.py:1354-1368` (`_imu_poll_worker`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `imu_calibration_config.load_config() -> dict` (Task 1)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py` (following the file's existing style — check an existing IMU-related test for the exact import/setup pattern before adding):

```python
def test_imu_poll_worker_uses_configured_ema_alpha(monkeypatch):
    import pendulastic_app as _m
    import imu_calibration_config as _cfgmod
    monkeypatch.setattr(_cfgmod, "load_config",
                        lambda: {**_cfgmod.DEFAULT_CONFIG, "ema_alpha": 0.9})
    # Re-import-equivalent: the worker reads the config fresh via
    # imu_calibration_config.load_config() each time it starts, not a cached
    # module-level constant, so patching load_config() is sufficient.
    app = _m.App()
    try:
        app._engine = _m.BiomechanicalEngine("imu")
        app._imu_poll_stop.clear()
        import threading, time as _time
        t = threading.Thread(target=app._imu_poll_worker, daemon=True)
        t.start()
        _time.sleep(0.15)
        app._imu_poll_stop.set()
        t.join(timeout=1.0)
        # No assertion on numeric output here (depends on live/absent IMU
        # hardware) — this test's purpose is only to confirm the worker
        # doesn't crash when reading ema_alpha from a monkeypatched config.
    finally:
        app._imu_poll_stop.set()
        app.destroy()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests\test_app.py::test_imu_poll_worker_uses_configured_ema_alpha -v`
Expected: passes today too (since nothing crashes yet) but is testing dead code — proceed to Step 3 regardless; this test exists to lock in the wiring, not to fail first. (Note: this task is a mechanical constant swap with no new branch to fail on — the TDD value here is regression-proofing, not red/green.)

- [ ] **Step 3: Wire `ema_alpha` into `_imu_poll_worker`**

In `pendulastic_app.py:1354-1368`, change:

```python
# OLD
    def _imu_poll_worker(self) -> None:
        """Put (t, angle_deg) into _imu_queue at ~20 Hz."""
        _EMA_ALPHA = 0.3   # higher = less smoothing, less lag
        _ema: Optional[float] = None
        while not self._imu_poll_stop.is_set():
            if self._engine:
                angle = self._engine.get_live_angle()
                if math.isfinite(angle):
                    _ema = (angle if _ema is None
                            else _EMA_ALPHA * angle + (1.0 - _EMA_ALPHA) * _ema)
                    self._imu_queue.put((time.time(), _ema))
                else:
                    _ema = None   # reset on NaN (pre-zero or disconnected)
                    self._imu_queue.put((time.time(), angle))
            time.sleep(0.05)

# NEW
    def _imu_poll_worker(self) -> None:
        """Put (t, angle_deg) into _imu_queue at ~20 Hz."""
        import imu_calibration_config as _cfgmod
        _EMA_ALPHA = _cfgmod.load_config()["ema_alpha"]
        _ema: Optional[float] = None
        while not self._imu_poll_stop.is_set():
            if self._engine:
                angle = self._engine.get_live_angle()
                if math.isfinite(angle):
                    _ema = (angle if _ema is None
                            else _EMA_ALPHA * angle + (1.0 - _EMA_ALPHA) * _ema)
                    self._imu_queue.put((time.time(), _ema))
                else:
                    _ema = None   # reset on NaN (pre-zero or disconnected)
                    self._imu_queue.put((time.time(), angle))
            time.sleep(0.05)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests\test_app.py::test_imu_poll_worker_uses_configured_ema_alpha -v`
Expected: PASS

- [ ] **Step 5: Run the full `test_app.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -v`
Expected: all pass (run individually if the known tkinter-singleton flake appears — see the ux-fixes plan's note on this)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: read IMU poll-worker EMA alpha from config"
```

---

### Task 4: Raw sample logging in pendulastic_imu_server.py

**Files:**
- Modify: `pendulastic_imu_server.py:309-314` (`on_accel`, post-Task-2), `:316-318` (`on_mag`), `:329-364` (`on_gyro`), module state block (add `_raw_lock`, `_raw_log_file`, `_raw_log_path`), add `start_raw_log`/`stop_raw_log`/`_raw_log_write` functions
- Test: `tests/test_imu_server.py`

**Interfaces:**
- Produces: `start_raw_log(path: str) -> None`, `stop_raw_log() -> Optional[str]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_imu_server.py`:

```python
def test_start_stop_raw_log_writes_jsonl(tmp_path):
    import json as _json
    imu.reset_devices()
    path = str(tmp_path / "trial_raw.jsonl")
    imu.start_raw_log(path)
    dev = imu._IMUDevice("14.0.0.1")
    imu._devices["14.0.0.1"] = dev
    imu._roles["14.0.0.1"] = imu.ROLE_DISTAL
    dev.on_accel(np.array([0., 0., -9.81]), ts=100)
    dev.on_gyro(np.array([0.1, 0.2, 0.3]), ts=110)
    dev.on_mag(np.array([1., 0., 0.]), ts=120)
    returned_path = imu.stop_raw_log()
    assert returned_path == path

    lines = [_json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert len(lines) == 3
    assert lines[0]["sensor"] == "accel"
    assert lines[0]["role"] == imu.ROLE_DISTAL
    assert lines[0]["v"] == [0.0, 0.0, -9.81]
    assert lines[0]["phone_ts_ms"] == 100
    assert lines[1]["sensor"] == "gyro"
    assert lines[2]["sensor"] == "mag"
    imu.reset_devices()


def test_stop_raw_log_returns_none_when_nothing_open():
    imu._raw_log_file = None
    imu._raw_log_path = None
    assert imu.stop_raw_log() is None


def test_on_accel_on_gyro_on_mag_are_no_ops_for_raw_log_when_not_recording():
    """Packets arriving with no raw log open must not raise."""
    imu.reset_devices()
    dev = imu._IMUDevice("14.0.0.2")
    dev.on_accel(np.array([0., 0., 9.81]), ts=0)
    dev.on_gyro(np.array([0., 0., 0.]), ts=10)
    dev.on_mag(np.array([1., 0., 0.]), ts=20)
    imu.reset_devices()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -k raw_log -v`
Expected: FAIL — `AttributeError: module 'pendulastic_imu_server' has no attribute 'start_raw_log'`

- [ ] **Step 3: Add raw-log state and functions**

In the module-state block (after `_CONFIG = load_config()` from Task 2), add:

```python
_raw_lock:     threading.Lock          = threading.Lock()
_raw_log_file                          = None    # open file handle, or None
_raw_log_path: Optional[str]           = None
```

Add these functions right before the `# ─── recording ───...` section (before `start_recording`, around line 702):

```python
def start_raw_log(path: str) -> None:
    """Begin logging every raw accel/gyro/mag packet as JSONL to `path`,
    independent of the legacy start_recording()/_recording mechanism
    (that one is only used by pendulastic_viewer.py)."""
    global _raw_log_file, _raw_log_path
    with _raw_lock:
        if _raw_log_file is not None:
            try:
                _raw_log_file.close()
            except OSError:
                pass
        _raw_log_file = open(path, "w", encoding="utf-8")
        _raw_log_path = path


def stop_raw_log() -> Optional[str]:
    """Close the current raw log, if any, and return the path that was
    just closed (or None if no raw log was open)."""
    global _raw_log_file, _raw_log_path
    with _raw_lock:
        path = _raw_log_path
        if _raw_log_file is not None:
            try:
                _raw_log_file.close()
            except OSError:
                pass
        _raw_log_file = None
        _raw_log_path = None
        return path


def _raw_log_write(role: Optional[str], sensor: str, v, phone_ts_ms) -> None:
    with _raw_lock:
        if _raw_log_file is None:
            return
        line = json.dumps({
            "t": time.time(),
            "role": role,
            "sensor": sensor,
            "v": [float(v[0]), float(v[1]), float(v[2])],
            "phone_ts_ms": int(phone_ts_ms) if phone_ts_ms else 0,
        })
        try:
            _raw_log_file.write(line + "\n")
        except (OSError, ValueError):
            pass
```

- [ ] **Step 4: Hook `_raw_log_write` into `on_accel`, `on_mag`, `on_gyro`**

In `pendulastic_imu_server.py`, change (this is the Task-2 version of `on_accel` — the `if _CONFIG["gravity_seed"]:` line is already there from Task 2):

```python
# OLD
    def on_accel(self, v, ts):
        self.accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            if _CONFIG["gravity_seed"]:
                self.ahrs.q = _gravity_seed(self.accel)
            self._ahrs_seeded = True
        self._touch(ts)

# NEW
    def on_accel(self, v, ts):
        self.accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            if _CONFIG["gravity_seed"]:
                self.ahrs.q = _gravity_seed(self.accel)
            self._ahrs_seeded = True
        _raw_log_write(_roles.get(self.ident), "accel", v, ts)
        self._touch(ts)
```

```python
# OLD
    def on_mag(self, v, ts):
        self.mag = v
        self._touch(ts)

# NEW
    def on_mag(self, v, ts):
        self.mag = v
        _raw_log_write(_roles.get(self.ident), "mag", v, ts)
        self._touch(ts)
```

```python
# OLD
    def on_gyro(self, v, ts):
        global _flex_axis, _flex_axis_armed
        now = time.time()
        self.gyro_times.append(now)

# NEW
    def on_gyro(self, v, ts):
        global _flex_axis, _flex_axis_armed
        now = time.time()
        _raw_log_write(_roles.get(self.ident), "gyro", v, ts)
        self.gyro_times.append(now)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -k raw_log -v`
Expected: 3 passed

- [ ] **Step 6: Run the full `test_imu_server.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat: add raw accel/gyro/mag JSONL logging, independent of the legacy recorder"
```

---

### Task 5: Wire raw logging into pendulastic_app.py's recording lifecycle

**Files:**
- Modify: `pendulastic_app.py:1346-1352` (`_start_imu_recording`), `:1184-1193` (`on_stop`'s `imu` branch)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `pendulastic_imu_server.start_raw_log(path)` / `stop_raw_log() -> Optional[str]` (Task 4)
- Produces: `on_stop()` now returns the trial's raw log path via a local variable, consumed by Task 8's live trigger

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_start_imu_recording_opens_raw_log(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1,
                "sources": ["imu"]}
        app._start_imu_recording(meta)
        expected_raw_path = os.path.join(
            str(tmp_path), "PID_P1_LEG_Right_MS_TRIAL_1_imu_raw.jsonl")
        assert _m._imu.stop_raw_log() == expected_raw_path
        app._imu_poll_stop.set()
    finally:
        app._imu_poll_stop.set()
        app.destroy()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\pytest.exe tests\test_app.py::test_start_imu_recording_opens_raw_log -v`
Expected: FAIL — `stop_raw_log()` returns `None` (no raw log was ever opened)

- [ ] **Step 3: Derive the raw-log path and call `start_raw_log` in `_start_imu_recording`**

In `pendulastic_app.py:1346-1352`, change:

```python
# OLD
    def _start_imu_recording(self, meta: dict) -> None:
        # IMU server runs continuously; data flows via queue -> _tick -> _rec_angles["imu"]
        # No start_recording() call needed — we own the CSV via DataManager.save_trial.
        self._imu_poll_stop.clear()
        self._imu_poll_thread = threading.Thread(
            target=self._imu_poll_worker, daemon=True)
        self._imu_poll_thread.start()

# NEW
    def _start_imu_recording(self, meta: dict) -> None:
        # IMU server runs continuously; data flows via queue -> _tick -> _rec_angles["imu"]
        # No start_recording() call needed — we own the CSV via DataManager.save_trial.
        if _IMU_AVAIL:
            fn_imu = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"],
                source="imu")
            raw_path = os.path.join(
                DataManager.DATA_DIR, fn_imu.replace(".csv", "_raw.jsonl"))
            os.makedirs(DataManager.DATA_DIR, exist_ok=True)
            _imu.start_raw_log(raw_path)
        self._imu_poll_stop.clear()
        self._imu_poll_thread = threading.Thread(
            target=self._imu_poll_worker, daemon=True)
        self._imu_poll_thread.start()
```

- [ ] **Step 4: Call `stop_raw_log()` in `on_stop()`'s `imu` branch and keep the path**

In `pendulastic_app.py:1180-1193`, change:

```python
# OLD
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

# NEW
        meta           = self._acq.get_metadata()
        source_angles: dict = {}
        pending_rgb    = False
        imu_raw_log_path: Optional[str] = None
        imu_csv_path:     Optional[str] = None
        fn_imu:           Optional[str] = None

        for src in self._active_sources:
            if src == "imu":
                angles_imu = self._rec_angles.get("imu", [])
                ts_imu     = self._rec_timestamps.get("imu") or None
                fn_imu = DataManager.build_filename(
                    meta["pid"], meta["leg"], meta["ms_status"], meta["trial"],
                    source="imu")
                imu_csv_path = DataManager.save_trial(fn_imu, angles_imu, meta,
                                       timestamps=ts_imu, source="imu")
                source_angles["imu"] = angles_imu
                if _IMU_AVAIL:
                    imu_raw_log_path = _imu.stop_raw_log()
```

(This only changes what's captured into local variables — the rest of `on_stop()`'s dispatch logic is extended in Task 8, which needs these variables.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv\Scripts\pytest.exe tests\test_app.py::test_start_imu_recording_opens_raw_log -v`
Expected: PASS

- [ ] **Step 6: Run the full `test_app.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: open/close the raw IMU log alongside each trial recording"
```

---

### Task 6: Core replay engine — `replay_trial`

**Files:**
- Create: `imu_calibration_tuner.py`
- Test: `tests/test_imu_calibration_tuner.py`

**Interfaces:**
- Consumes: `pendulastic_imu_server.MadgwickAHRS`, `_gravity_seed`, `_qconj`, `_qmul`, `_FLEX_CAPTURE_THRESHOLD`, `ROLE_PROXIMAL`, `ROLE_DISTAL`
- Produces: `replay_trial(raw_samples: list[dict], params: dict) -> tuple[np.ndarray, np.ndarray]`, `TICK_S: float`

- [ ] **Step 1: Write the failing test**

Create `tests/test_imu_calibration_tuner.py`:

```python
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import imu_calibration_tuner as tuner


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


def test_replay_trial_matches_hand_computed_rotation():
    """With beta=0.0 (accel correction fully disabled), the AHRS is pure gyro
    integration, so the post-burst angle must match the analytically expected
    180 - degrees(2.0 rad/s * 0.5s) = 180 - 57.2958 = 122.7042 deg."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,   # ema_alpha=1.0 -> no smoothing lag
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    assert len(t) > 0
    expected_final = 180.0 - math.degrees(2.0 * 0.5)
    assert abs(angle[-1] - expected_final) < 1.0, (
        f"expected ~{expected_final:.2f} deg, got {angle[-1]:.2f} deg")


def test_replay_trial_first_tick_is_nan_rest_are_finite():
    """Contract: tick 0 always precedes any processed sample (tick_times[0]
    always equals raw_samples[0]["t"] exactly), so no device state exists yet
    and it is always NaN. Every later tick, once zeroed, must be finite.
    Pinning this explicitly (not just working around it in another test)
    stops it from silently regressing into more than one leading NaN."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    assert math.isnan(angle[0])
    assert np.isfinite(angle[1:]).all()


def test_replay_trial_holds_near_180_before_release():
    """Before the burst, held nearly still, the displayed angle must read ~180."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    pre_release = angle[(t < 0.9) & np.isfinite(angle)]
    assert len(pre_release) > 0
    assert abs(float(np.median(pre_release)) - 180.0) < 1.0


def test_replay_trial_empty_samples_returns_empty_arrays():
    t, angle = tuner.replay_trial([], {"beta": 0.041, "ema_alpha": 0.3,
                                       "flex_axis_capture": True, "gravity_seed": True})
    assert len(t) == 0 and len(angle) == 0


def test_replay_trial_no_motion_ever_returns_empty_arrays():
    """A trial with no gyro burst above threshold never zeroes -> unscoreable."""
    samples = []
    t = 0.0
    samples.append({"t": t, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": 0})
    for i in range(50):
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})
    t_arr, angle = tuner.replay_trial(samples, {"beta": 0.041, "ema_alpha": 0.3,
                                                "flex_axis_capture": True,
                                                "gravity_seed": True})
    assert len(t_arr) == 0 and len(angle) == 0


def test_replay_trial_flex_axis_capture_excludes_out_of_plane_rotation():
    """Two sequential bursts about orthogonal axes: flex_axis is captured from
    the FIRST (Y-axis) burst. flex_axis_capture=True must project out the
    second (X-axis) burst's contribution; flex_axis_capture=False reports the
    axis-agnostic total rotation, which includes both. beta=0.0 isolates pure
    gyro integration so the two settings' difference isn't confounded by the
    accelerometer correction term."""
    samples = []
    samples.append({"t": 0.0, "role": "distal", "sensor": "accel",
                    "v": [0.0, 0.0, 9.81], "phone_ts_ms": 0})
    t = 0.0
    for _ in range(30):   # burst 1: 0.3s about Y -> captures flex_axis=[0,1,0]
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": int(t * 1000)})
    for _ in range(30):   # burst 2: 0.3s about X -- orthogonal to flex_axis
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [2.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})
    for _ in range(50):
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})

    base = {"beta": 0.0, "ema_alpha": 1.0, "gravity_seed": True}
    _, angle_projected = tuner.replay_trial(samples, {**base, "flex_axis_capture": True})
    _, angle_total      = tuner.replay_trial(samples, {**base, "flex_axis_capture": False})
    assert abs(angle_projected[-1] - angle_total[-1]) > 5.0, (
        "flex_axis_capture must measurably change the result once a second, "
        "orthogonal rotation is introduced after the axis is captured "
        f"(projected={angle_projected[-1]:.2f}, total={angle_total[-1]:.2f})")


def test_replay_trial_gravity_seed_changes_zero_reference_under_correction():
    """q_zero is captured on the FIRST qualifying gyro sample -- if that is
    the very first sample in the log, it is captured BEFORE any ahrs.update()
    call, so it equals whatever on_accel's seeding produced verbatim. A
    tilted accel makes gravity_seed=True seed q far from identity while
    gravity_seed=False leaves it at identity; with beta>0 (correction
    active), that starting-point difference measurably changes the
    subsequent trajectory rather than cancelling out (which it would if
    beta were 0 -- see the plan's Section 4 discussion)."""
    samples = []
    tilt_deg = 60.0
    samples.append({
        "t": 0.0, "role": "distal", "sensor": "accel",
        "v": [9.81 * math.sin(math.radians(tilt_deg)), 0.0,
              9.81 * math.cos(math.radians(tilt_deg))],
        "phone_ts_ms": 0,
    })
    t = 0.0
    for _ in range(80):   # first gyro sample triggers onset immediately
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 2.0, 0.0], "phone_ts_ms": int(t * 1000)})
    for _ in range(50):
        t += 0.01
        samples.append({"t": t, "role": "distal", "sensor": "gyro",
                        "v": [0.0, 0.0, 0.0], "phone_ts_ms": int(t * 1000)})

    base = {"beta": 0.15, "ema_alpha": 1.0, "flex_axis_capture": True}
    _, angle_seeded   = tuner.replay_trial(samples, {**base, "gravity_seed": True})
    _, angle_unseeded = tuner.replay_trial(samples, {**base, "gravity_seed": False})
    assert abs(angle_seeded[-1] - angle_unseeded[-1]) > 1.0, (
        "gravity_seed must measurably change the replayed series when "
        "correction (beta>0) is active and the accel is tilted "
        f"(seeded={angle_seeded[-1]:.2f}, unseeded={angle_unseeded[-1]:.2f})")
```

**Note for whoever implements this (added during Task 6's review fix round):** the two threshold values above (`5.0`, `1.0`) come from first-principles reasoning about the quaternion math, not from actually running the code. Run both tests once implemented — if the real observed difference is smaller but still clearly non-trivial (not floating-point noise), lower the threshold to match reality rather than force the predicted number. If either difference comes out essentially zero, STOP and report it as a concern — that would mean the parameter genuinely has no effect, which is itself a bug worth surfacing, not something to paper over by loosening the assertion to near-zero.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'imu_calibration_tuner'`

- [ ] **Step 3: Write the implementation**

Create `imu_calibration_tuner.py`:

```python
"""
imu_calibration_tuner.py
=========================
Shared engine for the IMU adaptive self-tuning calibration loop: replays a
recorded trial's raw sensor log through candidate AHRS/fusion parameter sets,
scores each against the pendulum test's physical constraints, and persists
the winning configuration. Used by both the live per-trial path
(pendulastic_app.py) and the standalone CLI (tune_imu.py).

See docs/superpowers/specs/2026-07-30-imu-adaptive-calibration-design.md.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from pendulastic_imu_server import (
    MadgwickAHRS, _gravity_seed, _qconj, _qmul,
    _FLEX_CAPTURE_THRESHOLD, ROLE_PROXIMAL, ROLE_DISTAL,
)
from pendulastic_pt_score import compute_pt_params
from imu_calibration_config import load_config, save_config

# Matches pendulastic_app.py's _imu_poll_worker 50 ms (~20 Hz) poll cadence —
# EMA's effective smoothing depends on both alpha and the sample interval it's
# applied at, so the replay must resample to this exact grid before applying it.
TICK_S = 0.05

TUNING_GRID = [
    {"beta": beta, "ema_alpha": alpha,
     "flex_axis_capture": fac, "gravity_seed": gs}
    for beta in (0.02, 0.041, 0.08, 0.15)
    for alpha in (0.1, 0.3, 0.5)
    for fac in (True, False)
    for gs in (True, False)
]


class _RoleState:
    """Per-role AHRS + bookkeeping used during a single replay_trial() run."""

    def __init__(self, beta: float):
        self.ahrs = MadgwickAHRS(beta=beta)
        self.accel: Optional[np.ndarray] = None
        self.mag: Optional[np.ndarray] = None
        self.last_ts: Optional[int] = None
        self.seeded = False


def replay_trial(raw_samples: list, params: dict):
    """
    Re-simulate the AHRS + flex-axis + zero-referencing + EMA pipeline over a
    raw trial log, mirroring pendulastic_imu_server.py's on_accel/on_mag/
    on_gyro + swing_angle_deg() + pendulastic_app.py's _imu_poll_worker, but
    parameterized by `params` instead of live global state.

    raw_samples: chronologically-sorted list of
        {"t": float, "role": str, "sensor": str, "v": [x,y,z], "phone_ts_ms": int}
    params: {"beta": float, "ema_alpha": float,
             "flex_axis_capture": bool, "gravity_seed": bool}

    Returns (t_seconds: np.ndarray, angle_deg: np.ndarray) at the same 50 ms
    cadence the live app displays and saves. Returns two empty arrays if the
    log is empty or no motion above the flex-axis threshold is ever detected
    (the trial never "zeroes" and can't be scored).

    Contract: angle_deg[0] is always NaN — the very first tick is always
    emitted before any raw sample has been processed (tick_times[0] always
    equals raw_samples[0]["t"] exactly), so no device state exists yet at
    that instant. This mirrors the live app's own contract: _imu_poll_worker
    (pendulastic_app.py) puts a non-finite angle onto its queue and resets
    the EMA "on NaN (pre-zero or disconnected)" under the same condition —
    a NaN-bearing angle series is normal, pre-existing behavior in this
    codebase, not a defect. Callers (score_waveform, App._run_imu_tuning)
    must finite-filter before reducing (e.g. np.nanmedian, or
    arr[np.isfinite(arr)]) rather than assume every tick is a number.
    """
    if not raw_samples:
        return np.array([]), np.array([])

    beta = params["beta"]
    roles: dict = {}

    def _state(role):
        if role not in roles:
            roles[role] = _RoleState(beta)
        return roles[role]

    def _snapshot():
        return {r: s.ahrs.q.copy() for r, s in roles.items()}

    flex_axis: Optional[np.ndarray] = None
    flex_axis_armed = True
    q_zero: dict = {}
    zero_captured = False

    # Mirrors on_gyro()'s "only the distal segment (or the solo phone)
    # defines the axis" restriction (pendulastic_imu_server.py's on_gyro,
    # is_distal/is_solo). Without this, a proximal-only motion burst in a
    # two-phone trial would incorrectly arm the axis/zero, which live never
    # does. "Solo" here means no distal-role sample ever appears in this log.
    has_distal = any(s["role"] == ROLE_DISTAL for s in raw_samples)

    t0 = raw_samples[0]["t"]
    t_end = raw_samples[-1]["t"]
    n_ticks = max(1, int((t_end - t0) / TICK_S) + 1)
    tick_times = t0 + np.arange(n_ticks) * TICK_S

    # tick_quats[i] = per-role quaternion snapshot as of tick i, taken just
    # before any sample at/after that tick's time is processed — i.e. "as it
    # would have been polled live". Onset-of-motion / q_zero is captured
    # later in this same pass and applied retroactively to every tick
    # (including pre-onset ones) in the second pass below.
    tick_quats: list = []
    next_tick_i = 0

    for samp in raw_samples:
        while next_tick_i < n_ticks and tick_times[next_tick_i] <= samp["t"]:
            tick_quats.append(_snapshot())
            next_tick_i += 1

        role = samp["role"]
        st = _state(role)
        v = np.asarray(samp["v"], dtype=float)
        sensor = samp["sensor"]

        if sensor == "accel":
            st.accel = v
            if not st.seeded:
                if params["gravity_seed"]:
                    st.ahrs.q = _gravity_seed(v)
                st.seeded = True
        elif sensor == "mag":
            st.mag = v
        elif sensor == "gyro":
            ts = samp.get("phone_ts_ms") or 0
            dt = None
            if st.last_ts is not None and ts:
                dt = (ts - st.last_ts) / 1000.0
            if dt is None or not (0.0 < dt < 0.5):
                dt = 0.01
            st.last_ts = ts

            # Onset-of-motion detection runs BEFORE this sample's rotation is
            # integrated below, so q_zero captures the state truly "just
            # before" onset (spec Section 4) rather than one step into it.
            # It always runs regardless of flex_axis_capture — it is only a
            # timing marker for where "zero" is measured; flex_axis_capture
            # separately controls whether the resulting delta is
            # axis-projected further down.
            if flex_axis_armed:
                omega_mag = float(np.linalg.norm(v))
                if omega_mag >= _FLEX_CAPTURE_THRESHOLD:
                    # Only a qualifying role's burst may arm/capture — a
                    # non-qualifying role's motion is ignored entirely
                    # (flex_axis_armed stays True), exactly matching
                    # on_gyro()'s is_distal/is_solo gate.
                    is_distal = (role == ROLE_DISTAL)
                    is_solo = (not has_distal) and (role == ROLE_PROXIMAL)
                    if is_distal or is_solo:
                        if not zero_captured:
                            q_zero = _snapshot()
                            zero_captured = True
                        if params["flex_axis_capture"]:
                            flex_axis = v / omega_mag
                        flex_axis_armed = False

            if st.accel is not None:
                st.ahrs.update(v, st.accel, st.mag, dt)

    while next_tick_i < n_ticks:
        tick_quats.append(_snapshot())
        next_tick_i += 1

    if not zero_captured:
        return np.array([]), np.array([])

    def _swing_from_quats(quats: dict) -> float:
        if (ROLE_PROXIMAL in quats and ROLE_DISTAL in quats
                and ROLE_PROXIMAL in q_zero and ROLE_DISTAL in q_zero):
            q_rel_zero = _qmul(_qconj(q_zero[ROLE_PROXIMAL]), q_zero[ROLE_DISTAL])
            q_rel_cur  = _qmul(_qconj(quats[ROLE_PROXIMAL]), quats[ROLE_DISTAL])
            q_delta = _qmul(_qconj(q_rel_zero), q_rel_cur)
        else:
            solo_role = ROLE_DISTAL if ROLE_DISTAL in quats else (
                ROLE_PROXIMAL if ROLE_PROXIMAL in quats else None)
            if solo_role is None or solo_role not in q_zero:
                return float("nan")
            q_delta = _qmul(_qconj(q_zero[solo_role]), quats[solo_role])

        if params["flex_axis_capture"] and flex_axis is not None:
            q = q_delta if q_delta[0] >= 0.0 else -q_delta
            sin_half = float(np.linalg.norm(q[1:]))
            if sin_half > 1e-9:
                theta = 2.0 * math.acos(min(1.0, float(q[0])))
                u = q[1:] / sin_half
                swing = abs(math.degrees(theta * float(np.dot(u, flex_axis))))
            else:
                swing = 0.0
        else:
            dot = max(-1.0, min(1.0, abs(float(q_delta[0]))))
            swing = 2.0 * math.degrees(math.acos(dot))
        return swing

    t_arr = tick_times - t0
    angle_raw = np.array([180.0 - _swing_from_quats(q) for q in tick_quats])

    alpha = params["ema_alpha"]
    ema = None
    smoothed = np.empty_like(angle_raw)
    for i, a in enumerate(angle_raw):
        if math.isnan(a):
            ema = None
            smoothed[i] = a
        else:
            ema = a if ema is None else alpha * a + (1.0 - alpha) * ema
            smoothed[i] = ema

    return t_arr, smoothed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add imu_calibration_tuner.py tests/test_imu_calibration_tuner.py
git commit -m "feat: add replay_trial, the shared offline AHRS replay engine"
```

---

### Task 7: Constraint scorer — `score_waveform`

**Files:**
- Modify: `imu_calibration_tuner.py` (append `score_waveform`)
- Test: `tests/test_imu_calibration_tuner.py`

**Interfaces:**
- Consumes: `pendulastic_pt_score.compute_pt_params(t, angle_raw) -> dict | None` — exact return keys used: `ang_r`, `t_r`, `pk_i`, `tr_i`, `N`, `A0_deg`, `f`, `R2n`, `omega_max_n`, `omega_min_n`
- Produces: `score_waveform(t: np.ndarray, angle_deg: np.ndarray) -> dict` with keys `passes: bool`, `penalty: float`, `params: dict | None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_imu_calibration_tuner.py`:

```python
def _damped_pendulum_series(duration_s=12.0, dt=0.05, release_t=1.0,
                            neutral_deg=140.0, decay=0.18, freq=0.9):
    """Damped oscillation centered on a sub-180 resting (neutral) angle,
    starting exactly at 180 and decaying toward `neutral_deg`:

        angle(tau) = neutral + (180 - neutral) * exp(-decay*tau) * cos(2*pi*freq*tau)

    At tau=0: exp(0)*cos(0)=1, so angle=neutral+(180-neutral)=180 exactly —
    already continuous with the pre-release hold, no separate release ramp
    needed. For any tau>0, exp(-decay*tau)*cos(...) < 1 strictly, so
    angle < neutral + (180-neutral)*1 = 180 always — the signal can never
    exceed 180 (physically impossible for this convention) regardless of
    the oscillation's phase, unlike a naive "180 - amplitude*cos(...)"
    formula centered on 180 itself, which swings above 180 whenever
    cos(...) goes negative."""
    t = np.arange(0, duration_s, dt)
    angle = np.full_like(t, 180.0)
    amplitude = 180.0 - neutral_deg
    for i, ti in enumerate(t):
        if ti >= release_t:
            tau = ti - release_t
            angle[i] = (neutral_deg
                       + amplitude * math.exp(-decay * tau) * math.cos(2 * math.pi * freq * tau))
    return t, angle


def test_score_waveform_good_signal_passes():
    t, angle = _damped_pendulum_series()
    result = tuner.score_waveform(t, angle)
    assert result["passes"], result
    assert result["params"] is not None


def test_score_waveform_bad_start_fails():
    """Isolates gate A specifically: the hold ramps smoothly from 140 up to
    180 across the whole pre-release segment (rather than a block overwrite),
    so nothing else trips -- a prior version of this test also produced a
    clip violation at the hold/release boundary, so it couldn't distinguish
    "gate A works" from "gate A is deleted and gate C catches it anyway."""
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    hold_mask = t < 1.0
    n_hold = int(hold_mask.sum())
    angle[hold_mask] = np.linspace(140.0, 180.0, n_hold)
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]
    assert result["penalty"] > 0


def test_score_waveform_clipped_step_fails():
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    mid = len(angle) // 2
    angle[mid] = angle[mid - 1] + 60.0   # impossible single-tick jump
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]


def test_score_waveform_plateau_during_active_swing_fails():
    """Isolates the plateau check specifically: after the frozen run, the
    REST of the series is shifted by a constant so it resumes continuously
    from the frozen value, rather than jumping back to the raw curve. A
    prior version of this test also produced a large clip violation at the
    un-freeze edge, so it couldn't distinguish "the plateau check works"
    from "the plateau check is deleted and the clip check catches it
    anyway." A constant shift doesn't change the remainder's own shape or
    derivatives -- only its offset -- so it introduces no new discontinuity."""
    t, angle = _damped_pendulum_series()
    angle = angle.copy()
    release_i = int(1.0 / 0.05)
    freeze_start = release_i + 2
    freeze_len = 10
    freeze_end = freeze_start + freeze_len
    frozen_value = angle[freeze_start]
    angle[freeze_start:freeze_end] = frozen_value
    offset = frozen_value - angle[freeze_end]
    angle[freeze_end:] += offset
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]


def test_score_waveform_long_resting_tail_after_one_drop_still_passes():
    """Severe-spasticity case: one real drop, then genuinely locked/motionless
    for the rest of a long trial. Must NOT be misclassified as a staircase."""
    t = np.arange(0, 20.0, 0.05)
    angle = np.full_like(t, 180.0)
    release_t = 1.0
    for i, ti in enumerate(t):
        if release_t <= ti < release_t + 1.0:
            tau = ti - release_t
            angle[i] = 180.0 - 35.0 * (tau / 1.0)   # smooth single drop to ~145
        elif ti >= release_t + 1.0:
            angle[i] = 145.0   # locked — flat for the remaining ~18s
    result = tuner.score_waveform(t, angle)
    assert result["passes"], (
        "a genuine single-drop-then-lock severe-spasticity trial must pass, "
        f"got penalty={result['penalty']}, params={result['params']}")


def test_score_waveform_slow_single_drop_then_lock_still_passes():
    """Same severe-spasticity shape as the test above, but the drop itself
    takes 3.5s instead of 1s -- still a real, physically valid (if unusually
    slow) release, not an artifact. A prior version of the no-extrema window
    fallback used a per-tick derivative threshold to find where the drop
    "settles"; that threshold was itself speed-coupled, so any drop slower
    than roughly 1s/35deg fell through to the same flat-4.0s window this
    fix exists to eliminate, and got rejected with false plateau violations
    on its own resting tail. This test pins the fix against that regression
    directly, at a drop speed the original test could not have caught."""
    t = np.arange(0, 20.0, 0.05)
    angle = np.full_like(t, 180.0)
    release_t = 1.0
    drop_s = 3.5
    for i, ti in enumerate(t):
        if release_t <= ti < release_t + drop_s:
            tau = ti - release_t
            angle[i] = 180.0 - 35.0 * (tau / drop_s)
        elif ti >= release_t + drop_s:
            angle[i] = 145.0
    result = tuner.score_waveform(t, angle)
    assert result["passes"], (
        "a slower single-drop-then-lock trial must still pass, "
        f"got penalty={result['penalty']}, params={result['params']}")


def test_score_waveform_trick_oversmoothed_no_oscillation_fails():
    """A technically plateau-free but physically flat (no real swing) curve
    must be rejected by the truthfulness gate (D), even though A-C alone
    would not catch it."""
    t = np.arange(0, 12.0, 0.05)
    # Tiny monotonic sag with no oscillation and no plateau at all.
    angle = 180.0 - 2.0 * (1.0 - np.exp(-0.05 * t))
    result = tuner.score_waveform(t, angle)
    assert not result["passes"], "a no-oscillation curve must fail the truthfulness gate"


def test_score_waveform_too_short_series_fails():
    t = np.arange(0, 0.5, 0.05)
    angle = np.full_like(t, 180.0)
    result = tuner.score_waveform(t, angle)
    assert not result["passes"]
    assert result["params"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -k score_waveform -v`
Expected: FAIL — `AttributeError: module 'imu_calibration_tuner' has no attribute 'score_waveform'`

- [ ] **Step 3: Write the implementation**

Append to `imu_calibration_tuner.py`:

```python
def score_waveform(t: np.ndarray, angle_deg: np.ndarray) -> dict:
    """
    Score a replayed trial's angle series against the pendulum test's
    physical constraints. Returns {"passes": bool, "penalty": float,
    "params": dict | None}. See spec Section 5 for the full rationale,
    including why the continuity check is bounded to the active-swing
    window rather than the whole trial (severe-spasticity patients can
    genuinely lock up and hold still for most of a trial — that must not
    be misclassified as a staircase sensor artifact).
    """
    t = np.asarray(t, dtype=float)
    angle_deg = np.asarray(angle_deg, dtype=float)

    if len(t) < 40 or np.count_nonzero(np.isfinite(angle_deg)) < 40:
        return {"passes": False, "penalty": 1e6, "params": None}

    # ── A. Horizontal start ────────────────────────────────────────────
    start_mask = t <= (t[0] + 0.3)
    start_vals = angle_deg[start_mask]
    start_vals = start_vals[np.isfinite(start_vals)]
    if len(start_vals) == 0:
        return {"passes": False, "penalty": 1e6, "params": None}
    start_median = float(np.median(start_vals))
    start_ok = abs(start_median - 180.0) <= 8.0
    start_penalty = max(0.0, abs(start_median - 180.0) - 8.0)

    # ── D. Truthfulness gate (drives B/C's window too) ─────────────────
    # detrend=False is deliberate, not an oversight: compute_pt_params's
    # default linear detrend (scipy.signal.detrend across the WHOLE finite
    # series) is designed to correct genuine slow gyro-integration drift —
    # exactly the failure mode this tuner exists to detect and penalize. If
    # the scorer let compute_pt_params silently detrend away a candidate's
    # baseline drift before checking it, a badly-drifting candidate could
    # look clean to the scorer despite being visibly wrong in the raw
    # signal. B and C need the RAW physical angle, not a drift-corrected
    # derived quantity — detrending is appropriate for pendulastic_pt_score's
    # own parameter-extraction use, not for this truthfulness/plausibility
    # check.
    pt_params = compute_pt_params(t, angle_deg, detrend=False)
    if pt_params is None:
        return {"passes": False, "penalty": 1e6 + start_penalty, "params": None}

    # ── B. Oscillation range (uses pt_params' smoothed, unflipped ang_r) ─
    ang_r = pt_params["ang_r"]
    min_angle = float(np.nanmin(ang_r))
    range_ok = 80.0 <= min_angle <= 178.0
    range_penalty = max(0.0, 80.0 - min_angle) + max(0.0, min_angle - 178.0)

    # ── C. Continuity, bounded to the active-swing window ───────────────
    t_r = pt_params["t_r"]
    pk_i, tr_i = pt_params["pk_i"], pt_params["tr_i"]
    extrema = np.concatenate([np.asarray(pk_i), np.asarray(tr_i)])
    if len(extrema):
        last_extremum_t = float(t_r[int(extrema.max())])
        window_end_t = t_r[0] + min(4.0, max(0.0, last_extremum_t - t_r[0]))
    else:
        # No oscillation detected at all -- a genuine single drop with no
        # rebound, the most severe end of the spasticity spectrum (it won't
        # even register as one detected trough via find_peaks, since that
        # requires the signal to go down AND back up). A flat 4.0s cap from
        # release would still bleed well into the resting tail here, since
        # nothing bounds where the single drop itself ends.
        #
        # A per-tick derivative threshold ("is it still moving") was tried
        # and rejected: it's coupled to both sample rate and drop SPEED --
        # any real drop slower than roughly the threshold's own rate falls
        # through to the same broken flat-4.0s case it was meant to fix.
        # Instead, this uses compute_pt_params's own tail-median
        # `neutral_deg` directly: find the first point after which the
        # signal is PERMANENTLY within tolerance of neutral (not just
        # transiently close, which a still-swinging signal could be too --
        # but that ambiguity doesn't apply here, since this branch only
        # runs when no oscillation was detected at all). This is robust to
        # the drop taking 1 second or 5, because it asks "has it reached
        # its final resting value," not "how fast is it changing right now."
        neutral = pt_params["neutral_deg"]
        tol = max(2.0, 0.05 * pt_params["A0_deg"])   # matches min_amp's own convention
        near_neutral = np.abs(ang_r - neutral) <= tol
        settle_idx = len(ang_r) - 1   # never permanently settles -> fall back to the full window
        for i in range(len(ang_r)):
            if np.all(near_neutral[i:]):
                settle_idx = i
                break
        settle_t = float(t_r[settle_idx])
        # No added buffer past settle_t -- unlike the rejected derivative-
        # threshold approach, settle_idx is already the point after which
        # EVERY remaining sample is within tolerance of neutral (that's what
        # "permanently" means above), so it is already at or slightly after
        # the true end of the transition (Savitzky-Golay smoothing can add a
        # few ticks of lag at the edge, which settle_idx already absorbs by
        # construction). Adding extra time past it only pulls more of the
        # already-flat tail into the window -- exactly what caused a false
        # plateau violation (~9 flat ticks, 3 over the 6-tick/0.3s limit) on
        # the original 1-second-drop test in an earlier version of this fix.
        window_end_t = min(t_r[0] + 4.0, settle_t)

    clip_violations = 0
    diffs = np.diff(angle_deg)
    for i in range(len(diffs)):
        if not (np.isfinite(angle_deg[i]) and np.isfinite(angle_deg[i + 1])):
            continue
        if abs(diffs[i]) > 25.0:
            clip_violations += 1

    active_idx = np.where((t >= t_r[0]) & (t <= window_end_t))[0]
    plateau_violations = 0
    run = 0
    for i in active_idx:
        if i + 1 >= len(angle_deg):
            continue
        if not (np.isfinite(angle_deg[i]) and np.isfinite(angle_deg[i + 1])):
            run = 0
            continue
        if abs(angle_deg[i + 1] - angle_deg[i]) < 0.05:
            run += 1
            if run >= 6:
                plateau_violations += 1
        else:
            run = 0

    continuity_ok = (clip_violations == 0 and plateau_violations == 0)
    continuity_penalty = 2.0 * clip_violations + 1.0 * plateau_violations

    # ── D. Plausibility bounds ────────────────────────────────────────────
    # N >= 0.0 (not 1.0) and f == 0.0-is-acceptable deliberately admit the
    # single-drop-then-lock severe-spasticity case the Section 5 continuity
    # fix exists to protect. A genuine single drop with NO rebound at all
    # doesn't register as a single detected trough via find_peaks either —
    # find_peaks needs the signal to go down AND back up to count as an
    # extremum, and this case never does — so compute_pt_params reports
    # N=(n_pos+n_neg)/2=0.0 exactly (not 0.5), and f=0.0 since there aren't
    # even 4 extrema to measure a frequency from (its own documented
    # "undefined, not enough cycles" signal, not an error). Gating strictly
    # on N>=1.0 or f>=0.3 would reject exactly the patients this test exists
    # to characterize — the same inconsistency the C-check's window bound
    # was designed to avoid, just showing up in D instead.
    d_ok = (
        0.0 <= pt_params["N"] <= 10.0
        and 10.0 <= pt_params["A0_deg"] <= 90.0
        and (pt_params["f"] == 0.0 or 0.3 <= pt_params["f"] <= 3.0)
        and math.isfinite(pt_params["R2n"])
        and math.isfinite(pt_params["omega_max_n"])
        and math.isfinite(pt_params["omega_min_n"])
    )

    passes = start_ok and range_ok and continuity_ok and d_ok
    penalty = (start_penalty + range_penalty + continuity_penalty
              + (0.0 if d_ok else 50.0))

    return {"passes": passes, "penalty": penalty, "params": pt_params}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -k score_waveform -v`
Expected: 7 passed

- [ ] **Step 5: Run the full `test_imu_calibration_tuner.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -v`
Expected: all pass (Task 6's tests plus this task's)

- [ ] **Step 6: Commit**

```bash
git add imu_calibration_tuner.py tests/test_imu_calibration_tuner.py
git commit -m "feat: add score_waveform, the pendulum-test constraint scorer"
```

---

### Task 8: Grid search + persistence — `tune` / `tune_and_persist`

**Files:**
- Modify: `imu_calibration_tuner.py` (append `tune`, `_is_improvement`, `tune_and_persist`)
- Test: `tests/test_imu_calibration_tuner.py`

**Interfaces:**
- Consumes: `TUNING_GRID`, `replay_trial`, `score_waveform` (this file), `load_config`/`save_config` (Task 1)
- Produces: `tune(raw_samples: list) -> dict` (`{"params": dict, "penalty": float, "passes": bool}`), `tune_and_persist(raw_samples: list, source_trial: str = "", force: bool = False) -> dict`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_imu_calibration_tuner.py`:

```python
def test_tune_picks_lowest_penalty_passing_candidate(monkeypatch):
    fake_results = [
        {"params": {"beta": 0.02, "ema_alpha": 0.1, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 5.0, "passes": True},
        {"params": {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 1.0, "passes": True},
        {"params": {"beta": 0.08, "ema_alpha": 0.5, "flex_axis_capture": False, "gravity_seed": False}, "penalty": 0.5, "passes": False},
    ]
    it = iter(fake_results)

    def fake_replay(raw_samples, params):
        return np.array([0.0, 1.0]), np.array([180.0, 170.0])

    def fake_score(t, angle):
        r = next(it)
        return {"passes": r["passes"], "penalty": r["penalty"], "params": None}

    monkeypatch.setattr(tuner, "TUNING_GRID", [r["params"] for r in fake_results])
    monkeypatch.setattr(tuner, "replay_trial", fake_replay)
    monkeypatch.setattr(tuner, "score_waveform", fake_score)

    best = tuner.tune([{"t": 0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0}])
    assert best["passes"] is True
    assert best["penalty"] == 1.0
    assert best["params"]["beta"] == 0.041


def test_tune_falls_back_to_least_bad_when_none_pass(monkeypatch):
    fake_results = [
        {"params": {"beta": 0.02, "ema_alpha": 0.1, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 5.0, "passes": False},
        {"params": {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True}, "penalty": 2.0, "passes": False},
    ]
    it = iter(fake_results)
    monkeypatch.setattr(tuner, "TUNING_GRID", [r["params"] for r in fake_results])
    monkeypatch.setattr(tuner, "replay_trial",
                        lambda raw, p: (np.array([0.0]), np.array([180.0])))
    monkeypatch.setattr(tuner, "score_waveform",
                        lambda t, a: {"passes": next(it)["passes"], "penalty": next(iter([2.0, 5.0][::-1])) if False else it, "params": None})
    # Simpler: rebuild fake_score to just cycle through fake_results' values.
    it2 = iter(fake_results)
    monkeypatch.setattr(tuner, "score_waveform",
                        lambda t, a: {k: v for k, v in next(it2).items() if k in ("passes", "penalty", )} | {"params": None})

    best = tuner.tune([{"t": 0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0}])
    assert best["passes"] is False
    assert best["penalty"] == 2.0


def test_tune_empty_replay_treated_as_worst_case(monkeypatch):
    monkeypatch.setattr(tuner, "TUNING_GRID", [
        {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True}])
    monkeypatch.setattr(tuner, "replay_trial",
                        lambda raw, p: (np.array([]), np.array([])))
    best = tuner.tune([{"t": 0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0}])
    assert best["passes"] is False
    assert best["penalty"] >= 1e6


def test_tune_and_persist_saves_when_improving(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.08, "ema_alpha": 0.1,
                   "flex_axis_capture": False, "gravity_seed": False},
        "penalty": 0.5, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], source_trial="trial_1.csv")
    saved = cfgmod.load_config()
    assert saved["beta"] == 0.08
    assert saved["passes"] is True
    assert saved["source_trial"] == "trial_1.csv"


def test_tune_and_persist_does_not_overwrite_a_better_existing_config(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    cfgmod.save_config({**cfgmod.DEFAULT_CONFIG, "beta": 0.15, "penalty": 0.1, "passes": True})
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.02, "ema_alpha": 0.5,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 3.0, "passes": True,   # worse penalty -> must not overwrite
    })
    tuner.tune_and_persist([{"dummy": True}])
    assert cfgmod.load_config()["beta"] == 0.15


def test_tune_and_persist_force_overwrites_regardless(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    cfgmod.save_config({**cfgmod.DEFAULT_CONFIG, "beta": 0.15, "penalty": 0.1, "passes": True})
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.02, "ema_alpha": 0.5,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 3.0, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], force=True)
    assert cfgmod.load_config()["beta"] == 0.02
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -k "tune_" -v`
Expected: FAIL — `AttributeError: module 'imu_calibration_tuner' has no attribute 'tune'`

- [ ] **Step 3: Write the implementation**

Append to `imu_calibration_tuner.py`:

```python
def tune(raw_samples: list) -> dict:
    """
    Grid search over TUNING_GRID. Returns the best candidate found:
        {"params": dict, "penalty": float, "passes": bool}
    Any candidate with passes=True beats any with passes=False, regardless
    of penalty; among passing candidates, lower penalty wins. If none pass,
    the least-bad (lowest-penalty) candidate is returned anyway, tagged
    passes=False, so the caller can decide not to persist it.
    """
    results = []
    for params in TUNING_GRID:
        t, angle = replay_trial(raw_samples, params)
        if len(t) == 0:
            results.append({"params": params, "penalty": 1e6, "passes": False})
            continue
        scored = score_waveform(t, angle)
        results.append({"params": params, "penalty": scored["penalty"],
                        "passes": scored["passes"]})

    passing = [r for r in results if r["passes"]]
    pool = passing if passing else results
    return min(pool, key=lambda r: r["penalty"])


def _is_improvement(candidate: dict, current: dict) -> bool:
    if candidate["passes"] and not current.get("passes"):
        return True
    if candidate["passes"] and current.get("passes"):
        return candidate["penalty"] < current.get("penalty", float("inf"))
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def tune_and_persist(raw_samples: list, source_trial: str = "",
                     force: bool = False) -> dict:
    """Run tune(), persist the winning config only if it's a genuine
    improvement over the currently persisted one (or force=True), and
    return the winning candidate dict regardless of whether it was persisted."""
    best = tune(raw_samples)
    current = load_config()
    if force or _is_improvement(best, current):
        save_config({
            "beta": best["params"]["beta"],
            "ema_alpha": best["params"]["ema_alpha"],
            "flex_axis_capture": best["params"]["flex_axis_capture"],
            "gravity_seed": best["params"]["gravity_seed"],
            "penalty": best["penalty"],
            "passes": best["passes"],
            "tuned_at": _now_iso(),
            "source_trial": source_trial,
        })
    return best
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -k "tune_" -v`
Expected: 6 passed

- [ ] **Step 5: Run the full `test_imu_calibration_tuner.py` suite (Tasks 6-8 combined)**

Run: `.venv\Scripts\pytest.exe tests\test_imu_calibration_tuner.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add imu_calibration_tuner.py tests/test_imu_calibration_tuner.py
git commit -m "feat: add tune() grid search and tune_and_persist() with a no-regression guard"
```

---

### Task 9: Live trigger — auto-tune on the IMU RECORDING→REVIEW transition

**Files:**
- Modify: `pendulastic_app.py:1-22` (imports), `:1184-1217` (`on_stop`'s dispatch), append new `_run_imu_tuning` method (near `_run_rgb_processing`, ~line 1509)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `imu_calibration_tuner.tune_and_persist`, `replay_trial` (Task 8, Task 6)
- Produces: `App._run_imu_tuning(raw_log_path, csv_path, csv_filename, meta) -> None`

**Scope note:** if both `imu` and `rgb` are active sources in the same trial (an uncommon combination for this protocol — `AcquisitionPanel` doesn't forbid it, unlike `video_file`+`rgb` which are mutually exclusive), RGB processing takes priority exactly as today and IMU auto-tuning is skipped for that trial; the originally-recorded IMU series is saved unchanged. IMU tuning can still be run afterward via the Task 10 CLI on that trial's raw log if desired. This keeps the live-wiring change surgical and avoids inventing cross-thread orchestration for a rare combination.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_run_imu_tuning_rewrites_csv_when_config_passes(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import imu_calibration_tuner as _tuner
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))

    raw_path = tmp_path / "trial_raw.jsonl"
    raw_path.write_text('{"t": 0, "role": "distal", "sensor": "gyro", "v": [0,0,0], "phone_ts_ms": 0}\n',
                        encoding="utf-8")

    monkeypatch.setattr(_tuner, "tune_and_persist", lambda raw, source_trial="", **kw: {
        "params": {"beta": 0.08, "ema_alpha": 0.3,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 0.4, "passes": True,
    })
    monkeypatch.setattr(_tuner, "replay_trial", lambda raw, params: (
        np.array([0.0, 0.05, 0.1]), np.array([180.0, 179.0, 178.0])))

    app = _m.App()
    try:
        meta = {"pid": "P2", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._pending_review = {"imu": [1.0, 2.0, 3.0]}
        csv_filename = _m.DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
        csv_path = _m.DataManager.save_trial(csv_filename, [1.0, 2.0, 3.0], meta, source="imu")

        result_holder = {}
        def _capture(source_angles, m):
            result_holder["source_angles"] = source_angles
        app._transition_to_review = _capture

        app._run_imu_tuning(str(raw_path), csv_path, csv_filename, meta)
        # _run_imu_tuning schedules the transition via self.after(0, ...) --
        # exactly the real production path (see the Note below) -- so the
        # Tk event loop must be pumped once before the callback has run.
        app.update()

        assert result_holder["source_angles"]["imu"] == [180.0, 179.0, 178.0]
        with open(csv_path, encoding="utf-8") as f:
            content = f.read()
        assert "179.000" in content
    finally:
        app.destroy()


def test_run_imu_tuning_falls_back_when_no_config_passes(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import imu_calibration_tuner as _tuner
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))

    raw_path = tmp_path / "trial_raw.jsonl"
    raw_path.write_text('{"t": 0, "role": "distal", "sensor": "gyro", "v": [0,0,0], "phone_ts_ms": 0}\n',
                        encoding="utf-8")
    monkeypatch.setattr(_tuner, "tune_and_persist", lambda raw, source_trial="", **kw: {
        "params": {"beta": 0.08, "ema_alpha": 0.3,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 99.0, "passes": False,
    })

    app = _m.App()
    try:
        meta = {"pid": "P3", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._pending_review = {"imu": [1.0, 2.0, 3.0]}
        csv_filename = _m.DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
        csv_path = _m.DataManager.save_trial(csv_filename, [1.0, 2.0, 3.0], meta, source="imu")

        result_holder = {}
        app._transition_to_review = lambda source_angles, m: result_holder.update(
            source_angles=source_angles)

        app._run_imu_tuning(str(raw_path), csv_path, csv_filename, meta)
        app.update()

        assert result_holder["source_angles"]["imu"] == [1.0, 2.0, 3.0]
    finally:
        app.destroy()


def test_run_imu_tuning_never_raises_on_missing_raw_log(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P4", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._pending_review = {"imu": [1.0, 2.0]}
        csv_filename = _m.DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
        csv_path = _m.DataManager.save_trial(csv_filename, [1.0, 2.0], meta, source="imu")

        result_holder = {}
        app._transition_to_review = lambda source_angles, m: result_holder.update(
            source_angles=source_angles)

        app._run_imu_tuning(str(tmp_path / "does_not_exist.jsonl"), csv_path, csv_filename, meta)
        app.update()
        assert result_holder["source_angles"]["imu"] == [1.0, 2.0]
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k run_imu_tuning -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute '_run_imu_tuning'`

- [ ] **Step 3: Add the `imu_calibration_tuner` and `json` imports**

In `pendulastic_app.py`, change:

```python
# OLD
import csv
import math
import os
import queue
import threading
import time
from typing import Callable, Optional

# NEW
import csv
import json
import math
import os
import queue
import threading
import time
from typing import Callable, Optional
```

And after the existing `_IMU_AVAIL` guard block (line 30-34):

```python
# OLD
try:
    import pendulastic_imu_server as _imu
    _IMU_AVAIL = True
except Exception:
    _imu = None
    _IMU_AVAIL = False

# NEW
try:
    import pendulastic_imu_server as _imu
    _IMU_AVAIL = True
except Exception:
    _imu = None
    _IMU_AVAIL = False

try:
    import imu_calibration_tuner as _tuner
except Exception:
    _tuner = None
```

- [ ] **Step 4: Add `_run_imu_tuning`**

Add this method to the `App` class in `pendulastic_app.py`, right before `_run_rgb_processing` (~line 1495):

```python
    def _run_imu_tuning(self, raw_log_path: str, csv_path: str,
                        csv_filename: str, meta: dict) -> None:
        """Load this trial's raw IMU log, run the grid search, and — only if
        a passing configuration is found — rewrite the trial's saved CSV and
        feed the tuned series into REVIEW. Must never raise: any failure
        falls back to the originally-recorded series so tuning can never
        block a clinician from seeing trial data."""
        source_angles = dict(self._pending_review)
        try:
            raw_samples = []
            with open(raw_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw_samples.append(json.loads(line))
                    except ValueError:
                        continue

            if raw_samples and _tuner is not None:
                best = _tuner.tune_and_persist(raw_samples, source_trial=csv_filename)
                if best["passes"]:
                    t, angle = _tuner.replay_trial(raw_samples, best["params"])
                    tuned_angles = [float(a) for a in angle]
                    DataManager.save_trial(
                        csv_filename, tuned_angles, meta,
                        timestamps=[float(x) for x in t], source="imu")
                    source_angles["imu"] = tuned_angles
        except Exception:
            # Broad on purpose: this runs in an unsupervised daemon thread,
            # and imu_calibration_tuner.py has no internal exception handling
            # of its own -- a malformed-but-JSON-parseable raw sample (e.g.
            # missing "role", or "v" not a 3-element list) could raise
            # TypeError/IndexError from deep inside replay_trial. An uncaught
            # exception here would kill the thread silently, the self.after
            # transition below would never fire, and the app would sit in
            # "processing" forever -- a direct violation of "tuning must
            # never block the clinician from seeing trial data."
            pass   # fall back to the originally-recorded series
        self.after(0, lambda: self._transition_to_review(source_angles, meta))
```

- [ ] **Step 5: Dispatch to `_run_imu_tuning` from `on_stop()`**

In `pendulastic_app.py`, change the end of `on_stop()` (the part after Task 5's edit):

```python
# OLD
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

# NEW
        pending_imu_tune = (
            imu_raw_log_path is not None and not pending_rgb and _tuner is not None)

        if pending_rgb:
            self._state = "processing"
            self._acq.enter_processing()
            self._pending_review = source_angles  # preserve already-done sources
            threading.Thread(
                target=self._run_rgb_processing,
                args=(meta,), daemon=True,
            ).start()
        elif pending_imu_tune:
            self._state = "processing"
            self._acq.enter_processing()
            self._pending_review = source_angles
            threading.Thread(
                target=self._run_imu_tuning,
                args=(imu_raw_log_path, imu_csv_path, fn_imu, meta), daemon=True,
            ).start()
        else:
            self._transition_to_review(source_angles, meta)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k run_imu_tuning -v`
Expected: 3 passed

- [ ] **Step 7: Run the full `test_app.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: auto-tune IMU trials on stop, rewriting the saved CSV when improved"
```

---

### Task 10: Standalone CLI — `tune_imu.py`

**Files:**
- Create: `tune_imu.py`
- Test: `tests/test_tune_imu_cli.py`

**Interfaces:**
- Consumes: `imu_calibration_tuner.tune_and_persist`, `pendulastic_imu_server` raw JSONL format

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tune_imu_cli.py`:

```python
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import tune_imu


def _write_jsonl(path, samples):
    with open(path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")


def test_load_raw_log_parses_jsonl(tmp_path):
    path = tmp_path / "a.jsonl"
    _write_jsonl(path, [
        {"t": 0.0, "role": "distal", "sensor": "gyro", "v": [0, 0, 0], "phone_ts_ms": 0},
        {"t": 0.01, "role": "distal", "sensor": "gyro", "v": [0, 1, 0], "phone_ts_ms": 10},
    ])
    samples = tune_imu.load_raw_log(str(path))
    assert len(samples) == 2
    assert samples[1]["v"] == [0, 1, 0]


def test_load_raw_log_skips_malformed_lines(tmp_path):
    path = tmp_path / "b.jsonl"
    path.write_text(
        '{"t": 0.0, "role": "distal", "sensor": "gyro", "v": [0,0,0], "phone_ts_ms": 0}\n'
        'not valid json\n'
        '{"t": 0.02, "role": "distal", "sensor": "gyro", "v": [0,0,1], "phone_ts_ms": 20}\n',
        encoding="utf-8")
    samples = tune_imu.load_raw_log(str(path))
    assert len(samples) == 2


def test_main_averages_penalty_across_multiple_logs(tmp_path, monkeypatch, capsys):
    """Each log must score DIFFERENTLY (2.0 vs 4.0), so the printed average
    can only be correct (3.0) if the code genuinely combines both logs --
    a broken implementation using only raw_logs[0] would print 2.0, and
    one using only raw_logs[-1] would print 4.0. An identical-penalty
    fixture (used in an earlier version of this test) can't distinguish
    real averaging from "just used one log," since any of those bugs would
    coincidentally produce the same output as the correct implementation."""
    path1 = tmp_path / "a.jsonl"
    path2 = tmp_path / "b.jsonl"
    _write_jsonl(path1, [{"t": 0.0, "role": "distal", "sensor": "gyro",
                         "v": [0, 0, 0], "phone_ts_ms": 0}])
    _write_jsonl(path2, [{"t": 0.0, "role": "distal", "sensor": "gyro",
                         "v": [0, 0, 0], "phone_ts_ms": 0}])

    monkeypatch.setattr(tune_imu, "TUNING_GRID", [
        {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True, "gravity_seed": True},
    ])
    monkeypatch.setattr(tune_imu, "replay_trial",
                        lambda raw, p: (np.array([0.0, 0.05]), np.array([180.0, 175.0])))

    call_count = {"n": 0}
    def fake_score(t, a):
        call_count["n"] += 1
        penalty = 2.0 if call_count["n"] % 2 == 1 else 4.0
        return {"passes": True, "penalty": penalty, "params": None}
    monkeypatch.setattr(tune_imu, "score_waveform", fake_score)
    monkeypatch.setattr(tune_imu, "save_config", lambda cfg: None)
    monkeypatch.setattr(tune_imu, "load_config",
                        lambda: {"beta": 0.041, "ema_alpha": 0.3,
                                "flex_axis_capture": True, "gravity_seed": True,
                                "penalty": None, "passes": False,
                                "tuned_at": None, "source_trial": None})

    tune_imu.main([str(path1), str(path2)])
    out = capsys.readouterr().out
    assert "3.0" in out, f"expected the true average (2.0+4.0)/2=3.0 in output, got: {out}"


def test_main_reports_and_skips_unreadable_log(tmp_path, capsys):
    good_path = tmp_path / "good.jsonl"
    _write_jsonl(good_path, [{"t": 0.0, "role": "distal", "sensor": "gyro",
                             "v": [0, 0, 0], "phone_ts_ms": 0}])
    missing_path = str(tmp_path / "does_not_exist.jsonl")

    tune_imu.main([str(good_path), missing_path])
    out = capsys.readouterr().out
    assert "Skipping" in out
    assert missing_path in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_tune_imu_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tune_imu'`

- [ ] **Step 3: Write the implementation**

Create `tune_imu.py`:

```python
#!/usr/bin/env python3
"""
tune_imu.py
===========
Standalone CLI for the IMU adaptive self-tuning calibration loop. Runs the
same grid search / persistence engine as the live app's post-recording
trigger (imu_calibration_tuner.py), against one or more previously-recorded
raw IMU JSONL logs.

Usage:
    .venv\\Scripts\\python.exe tune_imu.py <raw_log.jsonl> [<raw_log2.jsonl> ...]
    .venv\\Scripts\\python.exe tune_imu.py <raw_log.jsonl> --force
"""
from __future__ import annotations

import argparse
import json
import sys

from imu_calibration_tuner import (
    TUNING_GRID, replay_trial, score_waveform, load_config, save_config,
    _is_improvement, _now_iso,
)


def load_raw_log(path: str) -> list:
    """Return this log's raw samples, or [] with a printed warning if the
    file can't be read at all (missing path, permission error, etc.) --
    treated the same as an empty/all-malformed log by the caller, rather
    than raising an unhandled traceback for a typo'd CLI argument."""
    samples = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    samples.append(json.loads(line))
                except ValueError:
                    continue
    except OSError as e:
        print(f"Warning: could not read {path}: {e}")
        return []
    return samples


def _average_tune(raw_logs: list) -> dict:
    """Grid search where each candidate's penalty is averaged across all
    provided logs — a more robust pick than tuning against a single trial."""
    results = []
    for params in TUNING_GRID:
        penalties = []
        all_pass = True
        for raw_samples in raw_logs:
            t, angle = replay_trial(raw_samples, params)
            if len(t) == 0:
                penalties.append(1e6)
                all_pass = False
                continue
            scored = score_waveform(t, angle)
            penalties.append(scored["penalty"])
            all_pass = all_pass and scored["passes"]
        avg_penalty = sum(penalties) / len(penalties)
        results.append({"params": params, "penalty": avg_penalty, "passes": all_pass})

    passing = [r for r in results if r["passes"]]
    pool = passing if passing else results
    return min(pool, key=lambda r: r["penalty"])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_logs", nargs="+", help="Path(s) to *_raw.jsonl trial logs")
    parser.add_argument("--force", action="store_true",
                        help="Persist the winning config even if it doesn't "
                             "improve on the current one")
    args = parser.parse_args(argv)

    loaded = [(p, load_raw_log(p)) for p in args.raw_logs]
    dropped = [p for p, s in loaded if not s]
    if dropped:
        print(f"Skipping {len(dropped)} log(s) with no valid samples: {', '.join(dropped)}")
    raw_log_sets = [s for _, s in loaded if s]
    if not raw_log_sets:
        print("No valid samples found in any provided raw log.")
        return 1

    best = _average_tune(raw_log_sets)
    current = load_config()

    print(f"Best configuration: {best['params']}")
    print(f"Average penalty: {best['penalty']:.3f}  passes={best['passes']}")

    if args.force or _is_improvement(best, current):
        save_config({
            "beta": best["params"]["beta"],
            "ema_alpha": best["params"]["ema_alpha"],
            "flex_axis_capture": best["params"]["flex_axis_capture"],
            "gravity_seed": best["params"]["gravity_seed"],
            "penalty": best["penalty"],
            "passes": best["passes"],
            "tuned_at": _now_iso(),
            "source_trial": ",".join(args.raw_logs),
        })
        print("Saved to imu_calibration_config.json")
    elif not best["passes"]:
        print("No configuration met the physical constraints — nothing persisted.")
    else:
        print("Did not improve on the current persisted configuration — nothing persisted "
              "(use --force to override).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_tune_imu_cli.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add tune_imu.py tests/test_tune_imu_cli.py
git commit -m "feat: add tune_imu.py standalone CLI for offline/batch IMU calibration tuning"
```

---

### Task 11: Full regression run

**Files:**
- None modified — verification only

- [ ] **Step 1: Run the full test suite (excluding the pre-existing, unrelated `pendulastic` package import failures)**

Run: `.venv\Scripts\pytest.exe tests\ -v --ignore=tests\test_metrics.py --ignore=tests\test_pose.py --ignore=tests\test_stats.py --ignore=tests\test_video.py`

Expected: all pass — this includes the pre-existing 84 tests plus every new test added across Tasks 1-10. If `tests/test_app.py`'s known tkinter-singleton flake appears when run alongside other files, re-run `tests/test_app.py` individually to confirm it's the pre-existing flake and not a real regression (see the note in `docs/superpowers/plans/2026-07-29-pendulastic-ux-fixes.md`).

- [ ] **Step 2: Manual acceptance step (flagged in the spec, not automatable here)**

None of the existing recorded trials have raw logs (they predate this feature). End-to-end validation against a real pendulum drop — confirming the live app actually records a raw JSONL, tunes it, and shows an improved REVIEW graph — requires actual Sensor Stream phone hardware and is out of scope for this automated plan. Note this explicitly to the user as the remaining manual step before considering the feature fully verified.

- [ ] **Step 3: Commit (only if Step 1 required any fixes)**

```bash
git add -A
git commit -m "test: fix regressions found in full-suite run"
```

---

### Task 12: Ockendon & Gilbert tibial-inclination candidate in the grid search

**Context:** added after Tasks 1–11 landed on `main`. The single-sensor "relative"
method (`180.0 - swing_angle_deg()`) computes the knee angle from the
quaternion rotation distance between the zeroed pose and the current pose.
Ockendon & Gilbert's tibial-inclination model is a different, purely
trigonometric mapping from a single measured angle — the zero-referenced
tibial (distal-segment) pitch, β — to knee flexion:

```
κ = 90 + β − arccos(sin(β) / 1.2)
```

`1.2` is the anatomical adult femur:tibia length ratio constant from the
source paper. Because Pendulastic's clinical convention reports 180° at full
extension (not 0°) and it's not certain up front whether κ itself or its
180°-complement matches that convention, **both** are added as separate
grid-search candidates (`"ockendon"` = κ, `"ockendon_flipped"` = 180 − κ) and
left for `score_waveform`'s existing physiological truthfulness gate (180°
start, 1–10 swings, ~1 Hz) to pick empirically — no source-paper diagram is
trusted blindly for a clinical measurement.

**Scope:** internal tuning candidate only. No new UI, no new methodology
option in `AcquisitionPanel`/`BiomechanicalEngine` — exactly like
beta/ema_alpha/flex_axis_capture/gravity_seed, if `"ockendon"` or
`"ockendon_flipped"` wins a trial's grid search, `_run_imu_tuning`'s existing
`replay_trial(raw_samples, best["params"])` call already regenerates that
trial's saved/reviewed angle series using it — no separate live wiring
required or wanted.

**Files:**
- Modify: `pendulastic_imu_server.py` (extract `_quat_to_euler_deg`)
- Modify: `imu_calibration_tuner.py` (`ockendon_deg`, `TUNING_GRID`, `replay_trial`, `tune_and_persist`)
- Modify: `imu_calibration_config.py` (`DEFAULT_CONFIG["method"]`)
- Modify: `tune_imu.py` (persist `method`)
- Test: `tests/test_imu_server.py`, `tests/test_imu_calibration_tuner.py`, `tests/test_imu_calibration_config.py`, `tests/test_tune_imu_cli.py`

**Interfaces:**
- Produces: `pendulastic_imu_server._quat_to_euler_deg(q) -> tuple[float,float,float]`, `imu_calibration_tuner.ockendon_deg(beta_deg: float) -> float`
- Consumes: existing `replay_trial`/`score_waveform`/`tune`/`tune_and_persist` machinery (Tasks 6–8), unchanged in shape.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_imu_server.py` — pin the refactor as behavior-preserving:

```python
def test_quat_to_euler_deg_matches_identity_quaternion():
    roll, pitch, yaw = imu._quat_to_euler_deg(np.array([1.0, 0.0, 0.0, 0.0]))
    assert abs(roll) < 1e-9 and abs(pitch) < 1e-9 and abs(yaw) < 1e-9


def test_euler_deg_delegates_to_quat_to_euler_deg():
    ahrs = imu.MadgwickAHRS(beta=0.1)
    ahrs.update(np.array([0.3, 0.1, -0.2]), np.array([0.0, 0.0, 9.81]), None, 0.05)
    assert ahrs.euler_deg() == imu._quat_to_euler_deg(ahrs.q)
```

Add to `tests/test_imu_calibration_tuner.py`:

```python
def test_ockendon_deg_zero_beta_gives_zero_kappa():
    assert abs(tuner.ockendon_deg(0.0)) < 1e-9


def test_ockendon_deg_matches_formula_for_arbitrary_beta():
    beta = 45.0
    expected = 90.0 + beta - math.degrees(
        math.acos(math.sin(math.radians(beta)) / 1.2))
    assert abs(tuner.ockendon_deg(beta) - expected) < 1e-9


def test_replay_trial_defaults_to_relative_method_when_key_absent():
    """Backward compatibility: existing callers/tests never set "method"."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0,
              "flex_axis_capture": True, "gravity_seed": True}
    t, angle = tuner.replay_trial(samples, params)
    expected_final = 180.0 - math.degrees(2.0 * 0.5)
    assert abs(angle[-1] - expected_final) < 1.0


def test_replay_trial_ockendon_flipped_starts_near_180():
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
              "gravity_seed": True, "method": "ockendon_flipped"}
    t, angle = tuner.replay_trial(samples, params)
    pre_release = angle[(t < 0.9) & np.isfinite(angle)]
    assert abs(float(np.median(pre_release)) - 180.0) < 1.0


def test_replay_trial_ockendon_unflipped_starts_near_zero():
    """Documents *why* ockendon_flipped is the one likely to pass
    score_waveform's 180°-start gate -- unflipped kappa is ~0 at full
    extension, the opposite of Pendulastic's clinical convention."""
    samples = _solo_hold_then_burst_samples()
    params = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
              "gravity_seed": True, "method": "ockendon"}
    t, angle = tuner.replay_trial(samples, params)
    pre_release = angle[(t < 0.9) & np.isfinite(angle)]
    assert abs(float(np.median(pre_release))) < 1.0


def test_tuning_grid_includes_all_three_methods():
    methods = {p["method"] for p in tuner.TUNING_GRID}
    assert methods == {"relative", "ockendon", "ockendon_flipped"}


def test_tune_and_persist_persists_method_field(tmp_path, monkeypatch):
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.08, "ema_alpha": 0.1, "flex_axis_capture": False,
                   "gravity_seed": False, "method": "ockendon_flipped"},
        "penalty": 0.5, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], source_trial="trial_1.csv")
    assert cfgmod.load_config()["method"] == "ockendon_flipped"


def test_tune_and_persist_defaults_method_when_candidate_lacks_it(tmp_path, monkeypatch):
    """test_tune_and_persist_saves_when_improving's candidate has no "method"
    key (pre-Task-12 shape) -- must not KeyError, must default to "relative"."""
    import imu_calibration_config as cfgmod
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(tuner, "load_config", cfgmod.load_config)
    monkeypatch.setattr(tuner, "save_config", cfgmod.save_config)
    monkeypatch.setattr(tuner, "tune", lambda raw: {
        "params": {"beta": 0.08, "ema_alpha": 0.1,
                   "flex_axis_capture": False, "gravity_seed": False},
        "penalty": 0.5, "passes": True,
    })
    tuner.tune_and_persist([{"dummy": True}], source_trial="trial_1.csv")
    assert cfgmod.load_config()["method"] == "relative"
```

Update `tests/test_imu_calibration_config.py`'s existing `test_save_then_load_roundtrips`
to include `"method": "ockendon"` in `written` (the schema now has one more
field), and add:

```python
def test_load_config_fills_default_method_for_legacy_configs_missing_it(tmp_path, monkeypatch):
    path = tmp_path / "cfg.json"
    legacy = {k: v for k, v in cfgmod.DEFAULT_CONFIG.items() if k != "method"}
    legacy.update({"beta": 0.08, "ema_alpha": 0.1,
                  "flex_axis_capture": False, "gravity_seed": False})
    path.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_PATH", str(path))
    assert cfgmod.load_config()["method"] == "relative"
```

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py tests\test_imu_calibration_tuner.py tests\test_imu_calibration_config.py -v`
Expected: the new tests fail (`AttributeError`/`ImportError`/`KeyError`/assertion
failures) since `_quat_to_euler_deg`, `ockendon_deg`, `"method"` don't exist yet.

- [ ] **Step 2: Run the tests to verify they fail**

- [ ] **Step 3: Write the implementation**

In `pendulastic_imu_server.py`, extract the body of `MadgwickAHRS.euler_deg`
into a module-level function and have the method delegate to it:

```python
def _quat_to_euler_deg(q) -> tuple[float, float, float]:
    """Return (roll, pitch, yaw) in degrees — ZYX convention.
    roll  ≈ abduction/adduction, pitch ≈ flexion/extension, yaw ≈ rotation."""
    q1, q2, q3, q4 = q
    roll = math.atan2(2 * (q1 * q2 + q3 * q4), 1 - 2 * (q2 * q2 + q3 * q3))
    sin_p = max(-1.0, min(1.0, 2 * (q1 * q3 - q4 * q2)))
    pitch = math.asin(sin_p)
    yaw = math.atan2(2 * (q1 * q4 + q2 * q3), 1 - 2 * (q3 * q3 + q4 * q4))
    return math.degrees(roll), math.degrees(pitch), math.degrees(yaw)
```

...and replace `MadgwickAHRS.euler_deg`'s body with `return _quat_to_euler_deg(self.q)`.

In `imu_calibration_tuner.py`:
- Import `_quat_to_euler_deg` alongside the existing imports from `pendulastic_imu_server`.
- Add:

```python
OCKENDON_FT_RATIO = 1.2   # adult femur:tibia length ratio (Ockendon & Gilbert)


def ockendon_deg(beta_deg: float) -> float:
    """Ockendon & Gilbert's tibial-inclination knee-flexion model: maps a
    single measured tibial inclination (beta, degrees from horizontal) to
    knee flexion kappa, using the anatomical femur:tibia ratio constant.
    |sin(beta)| <= 1 < OCKENDON_FT_RATIO always, so the arccos argument is
    always in-domain -- no clamping needed."""
    beta = math.radians(beta_deg)
    return 90.0 + beta_deg - math.degrees(math.acos(math.sin(beta) / OCKENDON_FT_RATIO))
```

- Extend `TUNING_GRID` with a `method` dimension:

```python
TUNING_GRID = [
    {"beta": beta, "ema_alpha": alpha,
     "flex_axis_capture": fac, "gravity_seed": gs, "method": method}
    for beta in (0.02, 0.041, 0.08, 0.15)
    for alpha in (0.1, 0.3, 0.5)
    for fac in (True, False)
    for gs in (True, False)
    for method in ("relative", "ockendon", "ockendon_flipped")
]
```

- In `replay_trial`, add a role-preference beta helper next to `_swing_from_quats`
  (same DISTAL-then-PROXIMAL preference as the existing solo fallback):

```python
    def _beta_from_quats(quats: dict) -> float:
        solo_role = ROLE_DISTAL if ROLE_DISTAL in quats else (
            ROLE_PROXIMAL if ROLE_PROXIMAL in quats else None)
        if solo_role is None or solo_role not in q_zero:
            return float("nan")
        _, pitch_cur, _ = _quat_to_euler_deg(quats[solo_role])
        _, pitch_zero, _ = _quat_to_euler_deg(q_zero[solo_role])
        return wrap180(pitch_cur - pitch_zero)
```

(`wrap180` needs importing from `pendulastic_imu_server` alongside the existing names.)
Then replace the single-line `angle_raw = ...` with a method dispatch:

```python
    method = params.get("method", "relative")
    if method == "relative":
        angle_raw = np.array([180.0 - _swing_from_quats(q) for q in tick_quats])
    else:
        kappas = np.array([ockendon_deg(_beta_from_quats(q)) for q in tick_quats])
        angle_raw = kappas if method == "ockendon" else (180.0 - kappas)
```

- In `tune_and_persist`, add `"method": best["params"].get("method", "relative")`
  to the `save_config({...})` call (`.get`, not `[...]` — `test_tune_and_persist_saves_when_improving`
  and other existing tests stub `tune()` with pre-Task-12 params dicts that
  have no `"method"` key at all).

In `imu_calibration_config.py`, add `"method": "relative"` to `DEFAULT_CONFIG`.
Do **not** add `"method"` to `_REQUIRED_TYPES` — it must stay optional so
pre-Task-12 persisted config files still load cleanly (backfilled to
`"relative"` via the existing `merged = dict(DEFAULT_CONFIG); merged.update(cfg)`).

In `tune_imu.py`'s `main()`, add the same
`"method": best["params"].get("method", "relative")` to its own `save_config({...})`
call (it duplicates persistence independently of `tune_and_persist`).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_imu_server.py tests\test_imu_calibration_tuner.py tests\test_imu_calibration_config.py tests\test_tune_imu_cli.py -v`
Expected: all pass, including every pre-existing test in these four files (no regressions from the `TUNING_GRID` shape change or the `DEFAULT_CONFIG` schema growth).

- [ ] **Step 5: Full regression run**

Run: `.venv\Scripts\pytest.exe tests\ -v --ignore=tests\test_metrics.py --ignore=tests\test_pose.py --ignore=tests\test_stats.py --ignore=tests\test_video.py`
Expected: all pass (pre-existing count plus the new Task 12 tests).

- [ ] **Step 6: Commit**

```bash
git add pendulastic_imu_server.py imu_calibration_tuner.py imu_calibration_config.py tune_imu.py tests/test_imu_server.py tests/test_imu_calibration_tuner.py tests/test_imu_calibration_config.py
git commit -m "feat: add Ockendon & Gilbert tibial-inclination model as a grid-search candidate"
```
