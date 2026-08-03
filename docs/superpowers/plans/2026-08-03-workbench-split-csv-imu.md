# Workbench Split-CSV Phone-IMU Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the Pendulastic Workbench to load phone-IMU trials recorded in the older
"split-CSV" format (`Trial_N_imu.csv` / `Trial_N_gyro.csv` / `Trial_N_accel.csv` /
`Trial_N_mag.csv`), re-fusing the raw gyro/accel/mag samples through the existing
Madgwick AHRS pipeline so these historical trials can be compared against OptiTrack and
MediaPipe in the Workbench.

**Architecture:** `workbench_engine.load_imu_trial()` becomes a small format-dispatching
wrapper: `.jsonl` paths go through the existing (extracted-unchanged) JSONL reader,
anything else goes through a new split-CSV reader that derives the three sibling raw
files from whichever one was picked, validates each one's header before parsing, merges
all three sensor streams, and sorts them chronologically. Both paths feed the exact same
`imu_calibration_tuner.replay_trial()` engine and the exact same config-resolution logic
— nothing about the fusion/scoring pipeline changes. `pendulastic_workbench.py` only
needs its "Phone IMU raw log" file picker widened to also accept `.csv`.

**Tech Stack:** Python, `csv` (stdlib), `imu_calibration_tuner.replay_trial`, pytest.

## Global Constraints

- The four known split-CSV suffixes are `_imu.csv`, `_gyro.csv`, `_accel.csv`,
  `_mag.csv`. Sibling derivation must identify which of these four the given anchor
  path actually ends with — never assume a fixed suffix.
- Each raw split-CSV sibling (`_gyro.csv`/`_accel.csv`/`_mag.csv`) has the header
  `timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z`. `Trial_N_imu.csv` is never read for
  data — only ever used as a possible anchor path.
- `sensor_name` values map: `"Gyroscope"` → `"gyro"`, `"Accelerometer"` → `"accel"`,
  `"Magnetometer"` → `"mag"`. Any other value is an error.
- `timestamp_ms` (server epoch milliseconds) → divide by 1000 for `replay_trial`'s `"t"`
  field (server epoch seconds).
- `imu_calibration_tuner.replay_trial()` requires "a chronologically-sorted list" of
  samples — the merged gyro+accel+mag stream must be sorted by `"t"` before it's passed
  in.
- `workbench_engine.load_imu_trial()`'s public signature
  (`load_imu_trial(path, config=None, ft_ratio=None, method=None)`) must not change.
- No changes to `pendulastic_imu_server.py`, `imu_calibration_tuner.replay_trial()`, or
  the live raw-JSONL-logging path.
- Full spec: `docs/superpowers/specs/2026-08-03-workbench-split-csv-imu-design.md`.

---

### Task 1: Extract `_read_jsonl_samples` and `_replay_samples` from `load_imu_trial`

Pure refactor, no behavior change — establishes the two seams later tasks build on.

**Files:**
- Modify: `workbench_engine.py:291-323` (`load_imu_trial`)
- Test: `tests/test_workbench_engine.py` (existing `load_imu_trial` tests only — no new
  tests needed for this task; they're the regression guard)

**Interfaces:**
- Produces: `_read_jsonl_samples(jsonl_path: str) -> list[dict]`,
  `_replay_samples(samples: list[dict], config: Optional[dict], ft_ratio: Optional[float], method: Optional[str]) -> tuple[np.ndarray, np.ndarray]`

- [ ] **Step 1: Run the existing `load_imu_trial` tests to confirm the baseline passes**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k load_imu_trial -v`
Expected: 4 passed (`test_load_imu_trial_reproduces_hand_computed_rotation`,
`test_load_imu_trial_ft_ratio_override_changes_ockendon_output`,
`test_load_imu_trial_method_override_forces_ockendon`,
`test_load_imu_trial_skips_malformed_lines`)

- [ ] **Step 2: Extract the two helpers, keeping `load_imu_trial`'s body as a thin wrapper**

In `workbench_engine.py`, replace the current `load_imu_trial` (lines 291-323) with:

```python
def _read_jsonl_samples(jsonl_path: str) -> list:
    samples = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except ValueError:
                continue
    return samples


def _replay_samples(samples: list, config: Optional[dict],
                    ft_ratio: Optional[float], method: Optional[str]):
    if config is None:
        config = imu_calibration_config.load_config()
    params = dict(config)
    if method is not None:
        params["method"] = method
    if ft_ratio is not None:
        params["ft_ratio"] = ft_ratio

    t, angle = imu_calibration_tuner.replay_trial(samples, params)
    finite = np.isfinite(t) & np.isfinite(angle)
    return t[finite], angle[finite]


def load_imu_trial(jsonl_path: str, config: Optional[dict] = None,
                   ft_ratio: Optional[float] = None,
                   method: Optional[str] = None):
    """Load a phone's raw accel/gyro/mag JSONL and run it through the
    Madgwick AHRS replay engine (imu_calibration_tuner.replay_trial),
    returning the finite-filtered (t, angle) knee-angle series.

    config defaults to the currently-persisted imu_calibration_config;
    ft_ratio/method optionally override the config's own values for this
    call only (the Ockendon-personalization workflow, design spec Section
    3a) without touching the persisted config file."""
    samples = _read_jsonl_samples(jsonl_path)
    return _replay_samples(samples, config, ft_ratio, method)
```

- [ ] **Step 3: Run the same tests again to confirm nothing changed**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k load_imu_trial -v`
Expected: same 4 passed

- [ ] **Step 4: Commit**

```bash
git add workbench_engine.py
git commit -m "refactor: extract _read_jsonl_samples/_replay_samples from load_imu_trial"
```

---

### Task 2: Add `_read_split_csv_samples` (sibling derivation, validation, parsing, merge)

**Files:**
- Modify: `workbench_engine.py` (add imports, add the new function and its helpers,
  near `load_imu_trial`)
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: nothing from Task 1 yet (this task's tests call
  `engine._read_split_csv_samples` directly, not through `load_imu_trial`)
- Produces: `_read_split_csv_samples(anchor_path: str) -> list[dict]` — same
  `{"t", "role", "sensor", "v", "phone_ts_ms"}` shape as `_read_jsonl_samples`'s output,
  chronologically sorted by `"t"`.

- [ ] **Step 1: Write the failing tests**

Add near the top of `tests/test_workbench_engine.py` (after the existing imports), a
helper for writing synthetic split-CSV fixtures:

```python
_SPLIT_CSV_HEADER = "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"


def _write_split_csv(path, rows):
    """rows: list of (timestamp_ms, phone_ts_ms, role, sensor_name, x, y, z) tuples."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(_SPLIT_CSV_HEADER)
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def _write_solo_split_csv_trial(tmp_path, prefix="Trial_1"):
    """Three well-formed sibling CSVs (gyro/accel/mag), solo/proximal-only
    role: hold still for 1s, a scripted 2.0 rad/s gyro burst around Y for
    0.5s, then hold again -- the same shape (and ~2.5s total duration) as
    this file's own _solo_hold_then_burst_samples(). The duration matters:
    replay_trial ticks at a 50ms cadence, so a too-short fixture (e.g. a
    handful of samples spanning only a few milliseconds) ticks zero times
    and every test below would pass vacuously on empty arrays rather than
    actually exercising anything -- verified empirically against the real
    engine before writing this plan (254 samples in, 51 ticks out, 50/51
    finite, matching _solo_hold_then_burst_samples()'s own expected final
    angle of 180 - degrees(2.0 * 0.5) = 122.7 deg)."""
    gyro_path  = tmp_path / f"{prefix}_gyro.csv"
    accel_path = tmp_path / f"{prefix}_accel.csv"
    mag_path   = tmp_path / f"{prefix}_mag.csv"
    imu_path   = tmp_path / f"{prefix}_imu.csv"

    gyro_rows = []
    t_ms = 0.0
    for _ in range(100):
        t_ms += 10.0
        gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 0.0, 0.0))
    for _ in range(50):
        t_ms += 10.0
        gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 2.0, 0.0))
    for _ in range(100):
        t_ms += 10.0
        gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 0.0, 0.0))
    _write_split_csv(gyro_path, gyro_rows)

    _write_split_csv(accel_path, [
        (0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 9.81),
        (t_ms, int(t_ms), "proximal", "Accelerometer", 0.0, 0.0, 9.81),
    ])
    _write_split_csv(mag_path, [
        (1.0, 1, "proximal", "Magnetometer", -50.0, 20.0, 30.0),
        (t_ms, int(t_ms), "proximal", "Magnetometer", -50.0, 20.0, 30.0),
    ])
    imu_path.write_text("# participant,test\n", encoding="utf-8")
    return {"gyro": gyro_path, "accel": accel_path, "mag": mag_path, "imu": imu_path}


def test_read_split_csv_samples_merges_and_sorts_chronologically(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    samples = engine._read_split_csv_samples(str(paths["gyro"]))
    assert len(samples) == 254   # 250 gyro + 2 accel + 2 mag
    ts = [s["t"] for s in samples]
    assert ts == sorted(ts)
    assert {s["sensor"] for s in samples} == {"gyro", "accel", "mag"}
    assert all(s["role"] == "proximal" for s in samples)
    first_accel = next(s for s in samples if s["sensor"] == "accel")
    assert first_accel["v"] == [0.0, 0.0, 9.81]
    assert first_accel["t"] == 0.0
    assert first_accel["phone_ts_ms"] == 0


def test_read_split_csv_samples_missing_sibling_names_the_file(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    os.remove(paths["accel"])
    try:
        engine._read_split_csv_samples(str(paths["gyro"]))
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as e:
        assert "accel" in str(e)


def test_read_split_csv_samples_malformed_header_names_the_file(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    with open(paths["mag"], "w", encoding="utf-8") as f:
        f.write("wrong,header,columns\n")
        f.write("1,2,3\n")
    try:
        engine._read_split_csv_samples(str(paths["gyro"]))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "mag" in str(e).lower() or str(paths["mag"]) in str(e)


def test_read_split_csv_samples_unrecognized_sensor_name(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    _write_split_csv(paths["accel"], [
        (999.0, 999, "proximal", "Barometer", 0.0, 0.0, 9.81),
    ])
    try:
        engine._read_split_csv_samples(str(paths["gyro"]))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Barometer" in str(e)


def test_derive_split_csv_siblings_from_non_imu_anchor(tmp_path):
    """A _gyro.csv/_accel.csv/_mag.csv anchor must not be treated as if it
    were _imu.csv -- the derivation must identify the anchor's actual
    suffix, not assume a fixed one."""
    paths = _write_solo_split_csv_trial(tmp_path)
    derived = engine._derive_split_csv_siblings(str(paths["accel"]))
    assert derived["gyro"] == str(paths["gyro"])
    assert derived["accel"] == str(paths["accel"])
    assert derived["mag"] == str(paths["mag"])
    assert derived["imu"] == str(paths["imu"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k "split_csv" -v`
Expected: FAIL — `AttributeError: module 'workbench_engine' has no attribute '_read_split_csv_samples'` (and `_derive_split_csv_siblings`)

- [ ] **Step 3: Add the imports and the new functions**

At the top of `workbench_engine.py`, add to the existing import block:

```python
import csv
import os
```

Then add, near `load_imu_trial` (after the `_read_jsonl_samples`/`_replay_samples`
helpers from Task 1):

```python
_SPLIT_CSV_SUFFIXES = {"imu": "_imu.csv", "gyro": "_gyro.csv",
                       "accel": "_accel.csv", "mag": "_mag.csv"}
_SPLIT_CSV_HEADER = ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"]
_SENSOR_NAME_MAP = {"Gyroscope": "gyro", "Accelerometer": "accel", "Magnetometer": "mag"}


def _derive_split_csv_siblings(anchor_path: str) -> dict:
    """Given any one of the four sibling paths (_imu/_gyro/_accel/_mag.csv),
    identify which suffix the anchor actually ends with and derive the
    other three from the recovered trial prefix. Never assumes a fixed
    suffix -- a _gyro.csv/_accel.csv/_mag.csv anchor must derive correctly
    too, not just an _imu.csv one."""
    matched_key = None
    matched_suffix = None
    for key, suffix in _SPLIT_CSV_SUFFIXES.items():
        if anchor_path.endswith(suffix):
            matched_key = key
            matched_suffix = suffix
            break
    if matched_key is None:
        raise ValueError(
            f"{anchor_path!r} does not match any known split-CSV suffix "
            f"({', '.join(_SPLIT_CSV_SUFFIXES.values())}).")
    prefix = anchor_path[:-len(matched_suffix)]
    return {key: prefix + suffix for key, suffix in _SPLIT_CSV_SUFFIXES.items()}


def _read_one_split_csv(path: str, sensor_kind: str) -> list:
    """Read one raw split-CSV sibling (gyro/accel/mag), validating its
    header before parsing any rows -- a file that doesn't match the
    expected shape fails immediately with a clear message instead of an
    obscure crash inside replay_trial()."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {sensor_kind} sibling file: expected at {path!r}")
    samples = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"{path!r} is empty (expected header {_SPLIT_CSV_HEADER}).")
        if header != _SPLIT_CSV_HEADER:
            raise ValueError(
                f"{path!r} has an unexpected header {header!r}; "
                f"expected {_SPLIT_CSV_HEADER}.")
        for row_num, row in enumerate(reader, start=2):
            if len(row) != len(_SPLIT_CSV_HEADER):
                raise ValueError(
                    f"{path!r} row {row_num} has {len(row)} columns; "
                    f"expected {len(_SPLIT_CSV_HEADER)}.")
            timestamp_ms, phone_ts_ms, role, sensor_name, x, y, z = row
            sensor = _SENSOR_NAME_MAP.get(sensor_name)
            if sensor is None:
                raise ValueError(
                    f"{path!r} row {row_num} has unrecognized sensor_name "
                    f"{sensor_name!r}; expected one of {list(_SENSOR_NAME_MAP)}.")
            samples.append({
                "t": float(timestamp_ms) / 1000.0,
                "role": role,
                "sensor": sensor,
                "v": [float(x), float(y), float(z)],
                "phone_ts_ms": int(float(phone_ts_ms)),
            })
    return samples


def _read_split_csv_samples(anchor_path: str) -> list:
    """Read the three raw split-CSV siblings (gyro/accel/mag) for the
    trial anchored by anchor_path (any one of the four sibling files),
    merge them, and sort chronologically -- satisfying replay_trial's
    "chronologically-sorted list" contract."""
    paths = _derive_split_csv_siblings(anchor_path)
    samples = []
    for kind in ("gyro", "accel", "mag"):
        samples.extend(_read_one_split_csv(paths[kind], kind))
    samples.sort(key=lambda s: s["t"])
    return samples
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k "split_csv" -v`
Expected: 5 passed

- [ ] **Step 5: Run the full engine test suite to confirm no regressions**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add _read_split_csv_samples for split-CSV phone-IMU trials"
```

---

### Task 3: Wire the split-CSV path into `load_imu_trial`'s dispatch

**Files:**
- Modify: `workbench_engine.py` (`load_imu_trial`)
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `_read_jsonl_samples` and `_replay_samples` (Task 1), `_read_split_csv_samples`
  (Task 2)
- Produces: `load_imu_trial(path, config=None, ft_ratio=None, method=None)` now accepts
  either a `.jsonl` path or any one of the four split-CSV sibling paths — signature
  unchanged from before this plan.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`:

```python
def test_load_imu_trial_dispatches_to_split_csv_for_non_jsonl_path(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t, angle = engine.load_imu_trial(str(paths["gyro"]), config=config)
    # Explicit non-empty checks, not just np.isfinite(t).all() -- that's
    # vacuously True on an empty array, so it wouldn't fail against the
    # pre-fix code (a CSV path fed through the JSONL reader silently
    # yields zero samples via its per-line `except ValueError: continue`,
    # and replay_trial([]) returns two EMPTY arrays rather than raising).
    assert len(t) > 0
    assert len(angle) > 0
    assert np.isfinite(t).all()
    assert np.isfinite(angle).all()


def test_load_imu_trial_same_result_regardless_of_which_sibling_is_the_anchor(tmp_path):
    paths = _write_solo_split_csv_trial(tmp_path)
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    results = [engine.load_imu_trial(str(paths[k]), config=config)
              for k in ("gyro", "accel", "mag", "imu")]
    assert len(results[0][0]) > 0, "sanity check: the fixture must actually produce samples"
    for t, angle in results[1:]:
        assert list(t) == list(results[0][0])
        assert list(angle) == list(results[0][1])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k "dispatch or same_result" -v`
Expected: FAIL — `load_imu_trial` currently always treats its argument as a JSONL path.
Feeding it a CSV path means every line fails `json.loads` and is silently skipped by
the existing `except ValueError: continue`, so `_read_jsonl_samples` returns an empty
list; `replay_trial([])` then returns two empty arrays (its own documented behavior
for an empty log) rather than raising. Both tests' `assert len(t) > 0` catch this.

- [ ] **Step 3: Change `load_imu_trial`'s body to dispatch on file extension**

In `workbench_engine.py`, replace `load_imu_trial`'s body:

```python
def load_imu_trial(jsonl_path: str, config: Optional[dict] = None,
                   ft_ratio: Optional[float] = None,
                   method: Optional[str] = None):
    """Load a phone's raw accel/gyro/mag samples -- either a JSONL raw log
    (start_raw_log()'s format) or the older split-CSV sibling format
    (_imu/_gyro/_accel/_mag.csv) -- and run them through the Madgwick AHRS
    replay engine (imu_calibration_tuner.replay_trial), returning the
    finite-filtered (t, angle) knee-angle series.

    config defaults to the currently-persisted imu_calibration_config;
    ft_ratio/method optionally override the config's own values for this
    call only (the Ockendon-personalization workflow, design spec Section
    3a) without touching the persisted config file."""
    if jsonl_path.endswith(".jsonl"):
        samples = _read_jsonl_samples(jsonl_path)
    else:
        samples = _read_split_csv_samples(jsonl_path)
    return _replay_samples(samples, config, ft_ratio, method)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -k "dispatch or same_result" -v`
Expected: 2 passed

- [ ] **Step 5: Run the full engine test suite to confirm no regressions**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_workbench_engine.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: dispatch load_imu_trial to the split-CSV reader for non-.jsonl paths"
```

---

### Task 4: Widen the Workbench's IMU file picker to accept split-CSV files

**Files:**
- Modify: `pendulastic_workbench.py:53-63` (`TrialLoadPanel.__init__`),
  `pendulastic_workbench.py:65-76` (`_build_widgets`), `pendulastic_workbench.py:99-110`
  (`_file_row`)
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks (this task is UI-only; the format detection
  happens entirely inside `workbench_engine.load_imu_trial`, called unchanged by
  `App.on_load_trial`)
- Produces: `TrialLoadPanel._browse_buttons: dict[str, tk.Button]` (new, for test
  access to the row-specific Browse buttons)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pendulastic_workbench.py`:

```python
def test_imu_browse_button_accepts_csv_and_jsonl(monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()

    captured = {}
    def fake_askopenfilename(**kwargs):
        captured.update(kwargs)
        return ""
    monkeypatch.setattr(_m.filedialog, "askopenfilename", fake_askopenfilename)

    p._browse_buttons["imu"].invoke()

    exts = " ".join(pattern for _label, pattern in captured["filetypes"])
    assert "*.jsonl" in exts
    assert "*.csv" in exts
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_pendulastic_workbench.py -k browse_button -v`
Expected: FAIL — `AttributeError: 'TrialLoadPanel' object has no attribute '_browse_buttons'`

- [ ] **Step 3: Store each row's Browse button and widen the IMU row's filetypes**

In `pendulastic_workbench.py`, in `TrialLoadPanel.__init__` (after the existing
`tk.StringVar`/`tk.BooleanVar` assignments, before `self._build_widgets()`), add:

```python
        self._browse_buttons: dict = {}
```

In `_build_widgets`, change the three `_file_row` calls:

```python
        self._file_row(1, "Phone IMU raw log (.jsonl or split CSV)", self._imu_path,
                       [("IMU log", "*.jsonl *.csv"), ("All files", "*.*")], name="imu")
        self._file_row(2, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")], name="video")
        self._file_row(3, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")], name="optitrack")
```

Change `_file_row` itself to accept and store the name:

```python
    def _file_row(self, row: int, label: str, var: tk.StringVar, filetypes,
                  name: str) -> None:
        tk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(self, textvariable=var, width=48, state="readonly").grid(
            row=row, column=1, sticky="we", padx=4)
        btn = tk.Button(self, text="Browse...",
                       command=lambda: self._browse(var, filetypes))
        btn.grid(row=row, column=2, sticky="w", padx=4)
        self._browse_buttons[name] = btn
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_pendulastic_workbench.py -k browse_button -v`
Expected: 1 passed

- [ ] **Step 5: Run the full Workbench test suite to confirm no regressions**

Run: `..\..\.venv\Scripts\python.exe -m pytest tests\test_pendulastic_workbench.py tests\test_workbench_engine.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: accept split-CSV phone-IMU files in the Workbench's IMU file picker"
```

---
