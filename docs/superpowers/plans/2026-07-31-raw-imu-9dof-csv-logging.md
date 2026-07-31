# Raw 9-DOF Split-CSV IMU Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Cross-reference:** `docs/superpowers/plans/2026-07-30-imu-adaptive-calibration.md` (merged into `main`) also added raw accel/gyro/mag logging in `pendulastic_imu_server.py`, via a separate `start_raw_log()`/`stop_raw_log()` pair (JSONL, one file) gated by its own module flag — hooked into `pendulastic_app.py`'s recording lifecycle, which never calls the legacy `start_recording()` this plan targets. These are not competing designs for the same caller: that plan covers `pendulastic_app.py`, this one covers `master_app.py`/`pendulastic_viewer.py` via `start_recording()`. Both touch `on_accel`/`on_gyro`/`on_mag` in the same file, so implementing this plan will need a straightforward manual merge alongside that logging (different guard flags, different output files/formats) rather than a redesign — just don't be surprised by the adjacent hunks when you get there.

**Goal:** When a trial recording starts, log every raw accelerometer/gyroscope/magnetometer packet from both phones into three separate per-sensor CSV files (`<trial_prefix>_accel.csv`, `<trial_prefix>_gyro.csv`, `<trial_prefix>_mag.csv`) in the trial folder, independent of and in addition to the existing fused-angle CSV.

**Architecture:** Three new raw-sensor CSV writers are opened alongside the existing fused-angle CSV inside `pendulastic_imu_server.start_recording()`, using a filename prefix derived automatically from the fused CSV's own path (stripping `.csv` and a trailing `_imu`) — so neither `master_app.py` nor `pendulastic_viewer.py` (the two existing callers) need any code change. `_IMUDevice.on_accel/on_gyro/on_mag` each append one row to their writer as soon as a packet arrives, guarded by the module-level `_recording` flag and independent of the AHRS fusion math that runs afterward in the same methods. All four file handles (fused + 3 raw) share the existing `_rec_lock`; `start_recording()` is restructured so it no longer calls anything that acquires the separate `_lock` while holding `_rec_lock`, which prevents a new deadlock against the dispatch thread (which holds `_lock` while invoking `on_accel`/`on_gyro`/`on_mag`).

**Tech Stack:** Python 3.13, `csv` (stdlib), `threading` (stdlib), pytest. No new dependencies.

## Global Constraints

- CSV column order for all three raw files, exactly: `timestamp_ms, phone_ts_ms, role, sensor_name, x, y, z`.
- `sensor_name` values are exactly `"Accelerometer"`, `"Gyroscope"`, `"Magnetometer"` (matches the incoming `SensorName` field and the existing `_SENSOR_ALIASES` classification already in `pendulastic_imu_server.py`).
- `role` is whatever `_roles` currently assigns to that phone's IP — `"proximal"` or `"distal"` (the codebase's existing role vocabulary; it does not currently use `"thigh"`/`"shank"` anywhere) — or the raw IP address string when no role has been assigned yet (third/unrecognized phone).
- Raw logging must be guarded by the module-level `_recording` flag, checked in each of `on_accel`/`on_gyro`/`on_mag` before doing any work, and must never alter what those methods feed into `MadgwickAHRS`/`get_state()`/`swing_angle_deg()`.
- `motive_mobile_sync.py` is a separate module and is never imported or touched by this plan — its Motive sync triggers are untouched by construction.
- The existing fused-angle CSV (`start_recording()`'s primary `csv_path`, its header row, its `# sync_state`/`# sync_offset_s`/`# sync_jitter_s` meta rows, and `_log_sample()`) must be byte-for-byte unchanged in format.
- `master_app.py` and `pendulastic_viewer.py` — the only two callers of `imu_server.start_recording()` — both already pass a `..._imu.csv` path; this plan requires zero edits to either file (verified in Task 6, not assumed).
- Tests live in `tests/test_imu_server.py` (existing file, plain pytest functions operating directly on `pendulastic_imu_server` module globals — no mocking framework), run with `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -v`.

---

### Task 1: Raw-CSV filename derivation and file-open helper

**Files:**
- Modify: `pendulastic_imu_server.py:450-456` (module globals block) and the `─── recording ───` section (after `stop_recording()`, originally ending `pendulastic_imu_server.py:754`, before `_log_sample()` at `pendulastic_imu_server.py:757`)
- Test: `tests/test_imu_server.py`

**Interfaces:**
- Produces: `_RAW_SENSOR_SUFFIX: dict[str, str]` (`{"Accelerometer": "accel", "Gyroscope": "gyro", "Magnetometer": "mag"}`), `_raw_files: dict[str, object]`, `_raw_writers: dict[str, object]`, `_raw_csv_prefix(csv_path: str) -> str`, `_open_raw_csv(path: str) -> tuple`

- [ ] **Step 1: Write the failing tests**

Add `import csv` to the top of `tests/test_imu_server.py` (currently `import os, sys, math`), then append:

```python
def test_raw_csv_prefix_strips_imu_suffix():
    assert imu._raw_csv_prefix("C:/x/Trial_4_imu.csv") == "C:/x/Trial_4"


def test_raw_csv_prefix_without_imu_suffix():
    assert imu._raw_csv_prefix("C:/x/Trial_4.csv") == "C:/x/Trial_4"


def test_raw_csv_prefix_case_insensitive():
    assert imu._raw_csv_prefix("C:/x/Trial_4_IMU.CSV") == "C:/x/Trial_4"


def test_open_raw_csv_writes_header(tmp_path):
    path = str(tmp_path / "trial_accel.csv")
    f, w = imu._open_raw_csv(path)
    assert f is not None and w is not None
    f.close()
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["timestamp_ms", "phone_ts_ms", "role",
                        "sensor_name", "x", "y", "z"]


def test_open_raw_csv_returns_none_on_unwritable_path():
    f, w = imu._open_raw_csv("Z:/definitely/not/a/real/drive/trial_accel.csv")
    assert f is None and w is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k "raw_csv_prefix or open_raw_csv" -v`
Expected: FAIL with `AttributeError: module 'pendulastic_imu_server' has no attribute '_raw_csv_prefix'` (and `_open_raw_csv`).

- [ ] **Step 3: Implement the helpers**

In `pendulastic_imu_server.py`, immediately after the existing recording-globals block:

```python
_rec_lock   = threading.Lock()
_rec_file   = None
_rec_writer = None
_rec_t0     = 0.0
_rec_offset: Optional[float] = None   # clock offset captured at record start
_recording  = False
```

add:

```python
# Raw 9-DOF logging: one CSV per sensor, opened alongside the fused CSV in
# start_recording() and sharing _rec_lock with it.
_RAW_SENSOR_SUFFIX = {
    "Accelerometer": "accel",
    "Gyroscope":     "gyro",
    "Magnetometer":  "mag",
}
_raw_files:   dict[str, object] = {k: None for k in _RAW_SENSOR_SUFFIX}
_raw_writers: dict[str, object] = {k: None for k in _RAW_SENSOR_SUFFIX}
```

Then, in the `─── recording ───` section, immediately after `stop_recording()` (before `def _log_sample():`), add:

```python
def _raw_csv_prefix(csv_path: str) -> str:
    """Derive the shared '<trial>_accel/gyro/mag.csv' prefix from the fused
    angle CSV path, e.g. '.../Trial_4_imu.csv' -> '.../Trial_4'."""
    prefix = csv_path[:-4] if csv_path.lower().endswith(".csv") else csv_path
    if prefix.lower().endswith("_imu"):
        prefix = prefix[:-len("_imu")]
    return prefix


def _open_raw_csv(path: str):
    """Open one raw-sensor CSV and write its header row.
    Returns (file, writer), or (None, None) if the file could not be opened —
    raw logging is best-effort and must never block the fused CSV."""
    try:
        f = open(path, "w", newline="", encoding="utf-8")
    except OSError as e:
        print(f"[IMU] Could not open raw CSV {path}: {e}")
        return None, None
    w = csv.writer(f)
    w.writerow(["timestamp_ms", "phone_ts_ms", "role", "sensor_name",
                "x", "y", "z"])
    return f, w
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k "raw_csv_prefix or open_raw_csv" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat(imu): add raw-CSV filename derivation and file-open helpers"
```

---

### Task 2: Wire raw CSV creation into `start_recording()`; fix lock-ordering hazard

**Files:**
- Modify: `pendulastic_imu_server.py:704-742` (`start_recording()`)
- Test: `tests/test_imu_server.py`

**Interfaces:**
- Consumes: `_RAW_SENSOR_SUFFIX`, `_raw_files`, `_raw_writers`, `_raw_csv_prefix`, `_open_raw_csv` (Task 1)
- Produces: `start_recording(csv_path, meta=None) -> bool` now also opens the three raw CSVs beside the fused one.

**Why the lock-ordering fix is required:** Task 4 will make `on_accel`/`on_gyro`/`on_mag` call a new `_log_raw()` that acquires `_rec_lock` — and those methods run on the dispatch thread while it already holds `_lock` (an `RLock`), giving the order `_lock → _rec_lock`. Today's `start_recording()` acquires `_rec_lock` first and then calls `sync_status()` from *inside* that lock, and `sync_status()` internally acquires `_lock` — the reverse order, `_rec_lock → _lock`. Two threads using opposite lock orders concurrently is a classic deadlock. Fixing this now (before Task 4 introduces the second order) removes the hazard at its source.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_imu_server.py`:

```python
def test_start_recording_creates_three_raw_csvs(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_4_imu.csv")
    assert imu.start_recording(path)
    try:
        for suffix in ("accel", "gyro", "mag"):
            p = tmp_path / f"Trial_4_{suffix}.csv"
            assert p.exists(), f"missing {p}"
            with open(p, newline="", encoding="utf-8") as fh:
                header = next(csv.reader(fh))
            assert header == ["timestamp_ms", "phone_ts_ms", "role",
                               "sensor_name", "x", "y", "z"]
    finally:
        imu.stop_recording()


def test_start_recording_fused_csv_header_unchanged(tmp_path):
    """Regression: the existing fused-angle CSV format must not change."""
    imu.reset_devices()
    path = str(tmp_path / "Trial_5_imu.csv")
    assert imu.start_recording(path)
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        header_row = next(r for r in rows if r and r[0] == "t_epoch")
        assert header_row == [
            "t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned",
            "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg",
            "prox_roll", "prox_pitch", "prox_yaw",
            "dist_roll", "dist_pitch", "dist_yaw", "paired",
        ]
    finally:
        imu.stop_recording()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k "start_recording_creates_three or fused_csv_header_unchanged" -v`
Expected: `test_start_recording_creates_three_raw_csvs` FAILS (raw CSVs don't exist yet). `test_start_recording_fused_csv_header_unchanged` should already PASS (no behavior changed yet) — confirms it's a valid regression guard before you touch the function.

- [ ] **Step 3: Implement**

Replace `start_recording()` (`pendulastic_imu_server.py:704-742`) with:

```python
def start_recording(csv_path: str, meta: Optional[dict] = None) -> bool:  # noqa: C901
    """Open a CSV and begin logging every fused sample, plus one raw CSV per
    sensor (accel/gyro/mag) beside it.

    Timestamps are written in three bases so the trace can be aligned with the
    other modalities: `t_epoch` (time.time(), the same base motive_mobile_sync
    and the viewer's video recorder use), `t_rel` (seconds since this call),
    and `phone_ts_ms` (the app's own clock, for inter-phone alignment).

    sync_status() is deliberately computed BEFORE _rec_lock is acquired: it
    takes _lock internally, and on_accel/on_gyro/on_mag (which run on the
    dispatch thread while already holding _lock) acquire _rec_lock to log raw
    samples. Acquiring _rec_lock here and then reaching for _lock would be the
    reverse order and can deadlock against that thread."""
    global _rec_file, _rec_writer, _rec_t0, _recording, _rec_offset
    _sy = sync_status()
    with _rec_lock:
        if _recording:
            return False
        try:
            f = open(csv_path, "w", newline="", encoding="utf-8")
        except OSError:
            return False
        w = csv.writer(f)
        if meta:
            for k, v in meta.items():
                w.writerow([f"# {k}", v])
        # Record the clock alignment used for t_phone_aligned so the mapping
        # stays reproducible after the fact.
        w.writerow(["# sync_state", _sy["state"]])
        w.writerow(["# sync_offset_s",
                    f"{_sy['offset_s']:.6f}" if _sy["offset_s"] is not None else ""])
        w.writerow(["# sync_jitter_s",
                    f"{_sy['jitter_s']:.6f}" if _sy["jitter_s"] is not None else ""])
        w.writerow([
            "t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned",
            "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg",
            "prox_roll", "prox_pitch", "prox_yaw",
            "dist_roll", "dist_pitch", "dist_yaw",
            "paired",
        ])

        prefix = _raw_csv_prefix(csv_path)
        for sensor_name, suffix in _RAW_SENSOR_SUFFIX.items():
            rf, rw = _open_raw_csv(f"{prefix}_{suffix}.csv")
            _raw_files[sensor_name]   = rf
            _raw_writers[sensor_name] = rw

        _rec_file, _rec_writer = f, w
        _rec_t0 = time.time()
        _rec_offset = _sy["offset_s"]
        _recording = True
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -v`
Expected: PASS, all tests (26 total so far).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat(imu): open raw accel/gyro/mag CSVs in start_recording(), fix lock ordering"
```

---

### Task 3: `_log_raw()` writer and robust `stop_recording()` teardown

**Files:**
- Modify: `pendulastic_imu_server.py:745-754` (`stop_recording()`)
- Test: `tests/test_imu_server.py`

**Interfaces:**
- Consumes: `_raw_writers`, `_raw_files`, `_rec_lock` (Task 1/2)
- Produces: `_log_raw(role: str, sensor_name: str, v, ts, now: float) -> None`; `stop_recording()` flushes/closes all four handles (fused + 3 raw) and resets all four sets of globals to `None`, safe to call repeatedly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_imu_server.py`:

```python
def test_log_raw_appends_row_to_correct_csv(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_6_imu.csv")
    assert imu.start_recording(path)
    imu._log_raw("proximal", "Gyroscope", [0.018327, -0.023543, 0.002843],
                 1785500950869, 1000.25)
    imu.stop_recording()
    with open(tmp_path / "Trial_6_gyro.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 2
    _, phone_ts, role, sensor_name, x, y, z = rows[1]
    assert phone_ts == "1785500950869"
    assert role == "proximal"
    assert sensor_name == "Gyroscope"
    assert x == "0.018327" and y == "-0.023543" and z == "0.002843"


def test_log_raw_noop_when_no_writer_open():
    """No CSV is open (never recorded) — must not raise."""
    imu.reset_devices()
    imu._log_raw("distal", "Accelerometer", [0.0, 0.0, 9.81], 0, 0.0)


def test_stop_recording_closes_and_resets_all_handles(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_7_imu.csv")
    assert imu.start_recording(path)
    imu.stop_recording()
    assert imu._rec_file is None and imu._rec_writer is None
    assert all(f is None for f in imu._raw_files.values())
    assert all(w is None for w in imu._raw_writers.values())


def test_stop_recording_is_idempotent(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_8_imu.csv")
    assert imu.start_recording(path)
    imu.stop_recording()
    imu.stop_recording()   # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k "log_raw or stop_recording_closes or stop_recording_is_idempotent" -v`
Expected: `test_log_raw_appends_row_to_correct_csv` and `test_log_raw_noop_when_no_writer_open` FAIL with `AttributeError: module 'pendulastic_imu_server' has no attribute '_log_raw'`. The two `stop_recording` tests should already PASS (existing behavior) — confirms they're valid regression guards.

- [ ] **Step 3: Implement**

Add `_log_raw()` directly after `_open_raw_csv()` (added in Task 1):

```python
def _log_raw(role: str, sensor_name: str, v, ts, now: float):
    """Append one raw-sensor sample to its CSV. Called from on_accel/on_gyro/
    on_mag while _recording is True, independent of AHRS fusion."""
    with _rec_lock:
        w = _raw_writers.get(sensor_name)
        if w is None:
            return
        try:
            w.writerow([
                f"{now * 1000.0:.3f}", ts, role, sensor_name,
                f"{float(v[0]):.6f}", f"{float(v[1]):.6f}", f"{float(v[2]):.6f}",
            ])
        except (ValueError, OSError, IndexError, TypeError):
            pass
```

Replace `stop_recording()` (`pendulastic_imu_server.py:745-754`) with:

```python
def stop_recording():
    global _rec_file, _rec_writer, _recording
    with _rec_lock:
        _recording = False
        try:
            if _rec_file is not None:
                _rec_file.flush()
                _rec_file.close()
        except OSError:
            pass
        finally:
            _rec_file = _rec_writer = None

        for sensor_name in list(_raw_files.keys()):
            rf = _raw_files[sensor_name]
            try:
                if rf is not None:
                    rf.flush()
                    rf.close()
            except OSError:
                pass
            finally:
                _raw_files[sensor_name]   = None
                _raw_writers[sensor_name] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -v`
Expected: PASS, all tests (30 total so far).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat(imu): add _log_raw() writer and try/finally teardown in stop_recording()"
```

---

### Task 4: Hook `_log_raw()` into `on_accel`, `on_gyro`, `on_mag`

**Files:**
- Modify: `pendulastic_imu_server.py:309-318` (`on_accel`, `on_mag`), `pendulastic_imu_server.py:329-333` (start of `on_gyro`)
- Test: `tests/test_imu_server.py`

**Interfaces:**
- Consumes: `_log_raw()` (Task 3), `_roles` (existing module dict)
- Produces: raw sample logging fully wired into the live packet path; AHRS/fusion output is unaffected (verified by regression test below).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_imu_server.py`:

```python
def test_on_accel_logs_raw_row_while_recording(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_9_imu.csv")
    assert imu.start_recording(path)
    try:
        dev = imu._IMUDevice("10.0.0.9")
        imu._devices["10.0.0.9"] = dev
        imu._roles["10.0.0.9"] = imu.ROLE_PROXIMAL
        dev.on_accel(np.array([-0.030792, -0.551956, -0.825500]), 1785500949800)
    finally:
        imu.stop_recording()
    with open(tmp_path / "Trial_9_accel.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) == 2
    _, phone_ts, role, sensor_name, x, y, z = rows[1]
    assert phone_ts == "1785500949800"
    assert role == "proximal"
    assert sensor_name == "Accelerometer"
    assert x == "-0.030792" and y == "-0.551956" and z == "-0.825500"
    imu.reset_devices()


def test_on_gyro_logs_raw_row_while_recording(tmp_path):
    imu.reset_devices()
    path = str(tmp_path / "Trial_10_imu.csv")
    assert imu.start_recording(path)
    try:
        dev = imu._IMUDevice("10.0.0.10")
        imu._devices["10.0.0.10"] = dev
        imu._roles["10.0.0.10"] = imu.ROLE_DISTAL
        dev.accel = np.array([0.0, 0.0, 9.81])
        dev.on_gyro(np.array([0.018327, -0.023543, 0.002843]), 1785500950869)
    finally:
        imu.stop_recording()
    with open(tmp_path / "Trial_10_gyro.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[1][2] == "distal"
    assert rows[1][3] == "Gyroscope"
    imu.reset_devices()


def test_on_mag_logs_raw_row_using_ip_when_role_unassigned(tmp_path):
    """A third/unassigned phone has no _roles entry — role falls back to IP."""
    imu.reset_devices()
    path = str(tmp_path / "Trial_11_imu.csv")
    assert imu.start_recording(path)
    try:
        dev = imu._IMUDevice("10.0.0.11")
        imu._devices["10.0.0.11"] = dev   # deliberately no _roles entry
        dev.on_mag(np.array([-23.497269, -29.110579, -33.166870]), 1785500954175)
    finally:
        imu.stop_recording()
    with open(tmp_path / "Trial_11_mag.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[1][2] == "10.0.0.11"
    assert rows[1][3] == "Magnetometer"
    imu.reset_devices()


def test_raw_logging_skipped_when_not_recording():
    imu.reset_devices()
    assert not imu._recording
    dev = imu._IMUDevice("10.0.0.12")
    imu._devices["10.0.0.12"] = dev
    imu._roles["10.0.0.12"] = imu.ROLE_PROXIMAL
    # Must not raise, and there is no open writer to touch.
    dev.on_accel(np.array([0.0, 0.0, 9.81]), 0)
    dev.on_gyro(np.array([0.0, 0.0, 0.0]), 0)
    dev.on_mag(np.array([0.0, 0.0, 0.0]), 0)
    imu.reset_devices()


def test_ahrs_fusion_unaffected_by_raw_logging(tmp_path):
    """Regression (requirement: non-breaking integration): the fused AHRS
    quaternion after accel+gyro must be identical whether or not raw logging
    is active."""
    imu.reset_devices()
    imu.clear_zero()
    dev1 = imu._IMUDevice("10.0.0.13")
    imu._devices["10.0.0.13"] = dev1
    imu._roles["10.0.0.13"] = imu.ROLE_DISTAL
    dev1.last_rx = __import__("time").time()
    dev1.on_accel(np.array([0.0, 0.0, 9.81]), 0)
    dev1.on_gyro(np.array([0.2, 0.0, 0.0]), 10)

    imu.reset_devices()
    imu.clear_zero()
    dev2 = imu._IMUDevice("10.0.0.13")
    imu._devices["10.0.0.13"] = dev2
    imu._roles["10.0.0.13"] = imu.ROLE_DISTAL
    dev2.last_rx = __import__("time").time()
    path = str(tmp_path / "Trial_12_imu.csv")
    assert imu.start_recording(path)
    try:
        dev2.on_accel(np.array([0.0, 0.0, 9.81]), 0)
        dev2.on_gyro(np.array([0.2, 0.0, 0.0]), 10)
    finally:
        imu.stop_recording()

    np.testing.assert_allclose(dev1.ahrs.q, dev2.ahrs.q, atol=1e-12)
    imu.reset_devices()
    imu.clear_zero()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k "on_accel_logs or on_gyro_logs or on_mag_logs or raw_logging_skipped" -v`
Expected: the three `*_logs_raw_row*` tests FAIL (rows are never written — the callbacks don't call `_log_raw` yet). `test_raw_logging_skipped_when_not_recording` should already PASS.

- [ ] **Step 3: Implement**

Replace `on_accel` and `on_mag` (`pendulastic_imu_server.py:309-318`):

```python
    def on_accel(self, v, ts):
        self.accel = np.asarray(v, float)
        if not self._ahrs_seeded:
            self.ahrs.q = _gravity_seed(self.accel)
            self._ahrs_seeded = True
        now = time.time()
        if _recording:
            _log_raw(_roles.get(self.ident, self.ident), "Accelerometer", v, ts, now)
        self._touch(ts, now)

    def on_mag(self, v, ts):
        self.mag = v
        now = time.time()
        if _recording:
            _log_raw(_roles.get(self.ident, self.ident), "Magnetometer", v, ts, now)
        self._touch(ts, now)
```

In `on_gyro` (`pendulastic_imu_server.py:329-333`), change:

```python
    def on_gyro(self, v, ts):
        global _flex_axis, _flex_axis_armed
        now = time.time()
        self.gyro_times.append(now)
        cutoff = now - 3.0
```

to:

```python
    def on_gyro(self, v, ts):
        global _flex_axis, _flex_axis_armed
        now = time.time()
        if _recording:
            _log_raw(_roles.get(self.ident, self.ident), "Gyroscope", v, ts, now)
        self.gyro_times.append(now)
        cutoff = now - 3.0
```

(The rest of `on_gyro` — dt computation, AHRS update, flex-axis capture, and the final `self._touch(ts, now)` — is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -v`
Expected: PASS, all tests (35 total so far).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_imu_server.py tests/test_imu_server.py
git commit -m "feat(imu): log raw accel/gyro/mag samples from on_accel/on_gyro/on_mag"
```

---

### Task 5: Deadlock regression test for the `_lock`/`_rec_lock` ordering fix

**Files:**
- Test: `tests/test_imu_server.py`

**Interfaces:**
- Consumes: `start_recording`, `stop_recording` (Task 2), `_IMUDevice.on_gyro` (Task 4), `_lock` (existing)

- [ ] **Step 1: Write the test**

Append to `tests/test_imu_server.py`:

```python
def test_concurrent_start_recording_and_gyro_callbacks_do_not_deadlock(tmp_path):
    """Regression for the _lock/_rec_lock ordering hazard fixed in Task 2:
    on_gyro() acquires _rec_lock while its caller already holds _lock (as
    _dispatch() does in production), so start_recording()/stop_recording()
    must never acquire _lock while holding _rec_lock. Both worker threads are
    daemons and joined with a timeout, so a regression fails this assertion
    instead of hanging the test suite."""
    import threading

    imu.reset_devices()
    imu._devices["10.0.0.20"] = imu._IMUDevice("10.0.0.20")
    imu._roles["10.0.0.20"] = imu.ROLE_DISTAL
    dev = imu._devices["10.0.0.20"]
    dev.accel = np.array([0.0, 0.0, 9.81])

    stop_evt = threading.Event()

    def gyro_loop():
        i = 0
        while not stop_evt.is_set():
            with imu._lock:
                dev.on_gyro(np.array([0.01, 0.0, 0.0]), i)
            i += 1

    def rec_loop():
        for n in range(50):
            path = str(tmp_path / f"Trial_deadlock_{n}_imu.csv")
            imu.start_recording(path)
            imu.stop_recording()

    t_gyro = threading.Thread(target=gyro_loop, daemon=True)
    t_rec = threading.Thread(target=rec_loop, daemon=True)
    t_gyro.start()
    t_rec.start()
    t_rec.join(timeout=10.0)
    stop_evt.set()
    t_gyro.join(timeout=5.0)

    assert not t_rec.is_alive(), \
        "start_recording()/stop_recording() hung — lock-order regression"
    assert not t_gyro.is_alive(), \
        "gyro callback loop hung — lock-order regression"
    imu.reset_devices()
    imu.clear_zero()
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k concurrent_start_recording -v`
Expected: PASS in well under the 15s combined timeout (typically under 2s).

- [ ] **Step 3: Confirm the test actually catches the regression it targets**

Temporarily re-indent `start_recording()`'s `_sy = sync_status()` line back inside the `with _rec_lock:` block (undoing Task 2's fix) and rerun the same command. Expected: the test now FAILS (either an assertion failure after both timeouts elapse, or an observable hang that the timeouts bound to ~15s). Then revert the temporary change so the Task 2 fix is back in place.

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k concurrent_start_recording -v`
Expected: PASS again, confirming the fix is restored.

- [ ] **Step 4: Commit**

```bash
git add tests/test_imu_server.py
git commit -m "test(imu): add deadlock regression test for start_recording lock ordering"
```

---

### Task 6: End-to-end `_dispatch()` integration test and non-breaking verification

**Files:**
- Test: `tests/test_imu_server.py`
- Verify (read-only, no edits expected): `master_app.py:387-419`, `pendulastic_viewer.py:5843-5850`

**Interfaces:**
- Consumes: `_dispatch()` (existing), `start_recording`/`stop_recording` (Task 2/3)

- [ ] **Step 1: Write the end-to-end test**

Append to `tests/test_imu_server.py`:

```python
def test_dispatch_end_to_end_writes_all_three_raw_csvs(tmp_path):
    """Full pipeline: _dispatch() receiving the exact Sensor Stream JSON shapes
    from the spec must produce three populated raw CSVs plus the fused CSV."""
    imu.reset_devices()
    imu.clear_zero()
    path = str(tmp_path / "Trial_20_imu.csv")
    assert imu.start_recording(path)
    try:
        ip = "192.168.1.50"
        imu._dispatch("/accelerometer",
            '{"SensorName":"Accelerometer","Timestamp":1785500949800,'
            '"x":"-0.030792","y":"-0.551956","z":"-0.825500"}', ip)
        imu._dispatch("/gyroscope",
            '{"SensorName":"Gyroscope","Timestamp":1785500950869,'
            '"x":"0.018327","y":"-0.023543","z":"0.002843"}', ip)
        imu._dispatch("/magnetometer",
            '{"SensorName":"Magnetometer","Timestamp":1785500954175,'
            '"x":"-23.497269","y":"-29.110579","z":"-33.166870"}', ip)
    finally:
        imu.stop_recording()

    for suffix, sensor_name, ts, xyz in (
        ("accel", "Accelerometer", "1785500949800",
         ("-0.030792", "-0.551956", "-0.825500")),
        ("gyro", "Gyroscope", "1785500950869",
         ("0.018327", "-0.023543", "0.002843")),
        ("mag", "Magnetometer", "1785500954175",
         ("-23.497269", "-29.110579", "-33.166870")),
    ):
        with open(tmp_path / f"Trial_20_{suffix}.csv", newline="",
                  encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == ["timestamp_ms", "phone_ts_ms", "role",
                            "sensor_name", "x", "y", "z"]
        assert len(rows) == 2
        _, phone_ts, role, name, x, y, z = rows[1]
        assert phone_ts == ts
        assert role == "proximal"          # first phone seen -> proximal
        assert name == sensor_name
        assert (x, y, z) == xyz

    imu.reset_devices()
    imu.clear_zero()
```

- [ ] **Step 2: Run the test and verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_imu_server.py -k dispatch_end_to_end -v`
Expected: PASS (all groundwork from Tasks 1-4 already implements this).

- [ ] **Step 3: Verify `master_app.py` and `pendulastic_viewer.py` need no changes**

Read `master_app.py:387-419` (`_start_imu`/`_stop_imu`) and `pendulastic_viewer.py:5843-5850`. Confirm both still call `imu_server.start_recording(path, meta)` with `path` ending in `Trial_<n>_imu.csv`, unchanged from before this plan — `start_recording()`'s new `_raw_csv_prefix()` derives the raw filenames automatically from that same path, so neither file requires an edit. If either call site has changed shape since this plan was written, re-derive `_raw_csv_prefix()`'s expected input from the new call site instead of editing it to match the plan.

- [ ] **Step 4: Run the full test suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: PASS, no failures introduced elsewhere (in particular `tests/test_app.py`, which exercises `pendulastic_app.py`'s IMU wiring, and `tests/test_imu_server.py` in full).

- [ ] **Step 5: Commit**

```bash
git add tests/test_imu_server.py
git commit -m "test(imu): add end-to-end dispatch test for raw 9-DOF CSV logging"
```
