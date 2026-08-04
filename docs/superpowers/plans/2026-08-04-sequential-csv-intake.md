# Guided Sequential 4-Component CSV Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Workbench's anchor-derived split-CSV auto-fill with an explicit, per-component guided intake that validates each of the four phone CSV streams (Accelerometer, Gyroscope, Magnetometer, raw IMU) independently before binding them into the dataset that feeds Madgwick fusion and the Popović PT-score pipeline.

**Architecture:** `workbench_engine.py` gains a pure-function validation/bind layer (`validate_component_csv`, `bind_split_csv_components`, `load_imu_trial_from_components`) replacing the old anchor-derivation code; `pendulastic_workbench.py`'s `TrialLoadPanel` gains a format toggle and a 4-slot picker UI that calls the new validation function per-slot with live status readouts; both `App.on_load_trial()` copies (standalone Workbench app and the main app's embedded Workbench mode) branch on the selected format.

**Tech Stack:** Python, Tkinter, NumPy, pytest (existing project stack — no new dependencies).

## Global Constraints

- `_MIN_FS_FOR_FUSION_HZ = 10.0` — hard floor on `fs_eff = 1.0 / median(diff(t))` for every one of the four component kinds (including the raw IMU reference file), distinct from the unrelated `_MIN_FS_FOR_5HZ_CUTOFF_HZ = 20.0` used elsewhere.
- Component headers (exact-match required):
  - `accel`/`gyro`/`mag`: `["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"]`
  - `imu`: `["t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned", "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg", "prox_roll", "prox_pitch", "prox_yaw", "dist_roll", "dist_pitch", "dist_yaw", "paired"]`
- `_COMPONENT_SENSOR_NAME = {"accel": "Accelerometer", "gyro": "Gyroscope", "mag": "Magnetometer"}` — every row's `sensor_name` in that slot's file must equal this exactly; `imu` has no `sensor_name` column, so this check doesn't apply to it.
- `validate_component_csv()` never raises — always returns `{"ok", "error", "n_samples", "fs_eff", "rows"}`.
- `load_imu_trial()` becomes `.jsonl`-only; a non-`.jsonl` path raises `ValueError` directing the caller to `load_imu_trial_from_components()`.
- No cross-file validation (e.g. overlapping time ranges) — each of the four files is validated independently, per spec Section 8.
- No new UI surface for `imu_reference` — it is attached to trial data only, never displayed, per spec Section 8.
- No auto-suggestion of sibling paths between slots — each of the 4 slots is browsed independently, per spec Section 7.

Reference: `docs/superpowers/specs/2026-08-04-sequential-csv-intake-design.md`

---

### Task 1: `validate_component_csv()` in `workbench_engine.py`

**Files:**
- Modify: `workbench_engine.py` (add near the existing split-CSV code, around line 322)
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Produces: `validate_component_csv(path: str, kind: str) -> dict` where `kind` is one of `"accel"`, `"gyro"`, `"mag"`, `"imu"`. Return shape: `{"ok": bool, "error": Optional[str], "n_samples": int, "fs_eff": Optional[float], "rows": list}`. For `kind in ("accel", "gyro", "mag")`, each entry of `rows` is `{"t": float, "role": str, "sensor": kind, "v": [x, y, z], "phone_ts_ms": int}` (same shape `replay_trial()` already consumes). For `kind == "imu"`, each entry of `rows` is a dict of every column in the `imu` header, with `"t_epoch"` coerced to `float` and all other values left as the raw string read from the CSV.
- Produces (module-level, reusable by later tasks): `_COMPONENT_HEADERS`, `_COMPONENT_SENSOR_NAME`, `_MIN_FS_FOR_FUSION_HZ`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`, near the existing split-CSV tests (after line 106, before `test_derive_split_csv_siblings_from_non_imu_anchor` — that test and its two neighbors get removed in Task 3, not here):

```python
_IMU_REFERENCE_HEADER = ("t_epoch,t_rel,phone_ts_ms,t_phone_aligned,"
                         "hip_roll_deg,hip_pitch_deg,hip_yaw_deg,"
                         "prox_roll,prox_pitch,prox_yaw,"
                         "dist_roll,dist_pitch,dist_yaw,paired\n")


def _write_component_csv(path, kind, rows):
    """rows: list of tuples matching engine._COMPONENT_HEADERS[kind]'s column order."""
    header = ",".join(engine._COMPONENT_HEADERS[kind]) + "\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(header)
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def _accel_rows_100hz(n=60, sensor_name="Accelerometer", role="proximal"):
    """100 Hz cadence -- well above the 10 Hz fusion floor."""
    rows = []
    for i in range(n):
        t_ms = i * 10.0
        rows.append((t_ms, int(t_ms), role, sensor_name, 0.0, 0.0, 9.81))
    return rows


def _imu_reference_rows_100hz(n=60):
    rows = []
    for i in range(n):
        t_epoch = 1_700_000_000.0 + i * 0.01
        rows.append((t_epoch, i * 0.01, int(i * 10), t_epoch,
                     0.0, 180.0, 0.0, 0.0, 90.0, 0.0, 0.0, 90.0, 0.0, True))
    return rows


def test_validate_component_csv_happy_path_accel(tmp_path):
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", _accel_rows_100hz())
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is True
    assert result["error"] is None
    assert result["n_samples"] == 60
    assert result["fs_eff"] == pytest.approx(100.0, rel=0.05)
    assert result["rows"][0] == {
        "t": 0.0, "role": "proximal", "sensor": "accel",
        "v": [0.0, 0.0, 9.81], "phone_ts_ms": 0,
    }


def test_validate_component_csv_happy_path_imu_reference(tmp_path):
    path = tmp_path / "Trial_1_imu.csv"
    _write_component_csv(path, "imu", _imu_reference_rows_100hz())
    result = engine.validate_component_csv(str(path), "imu")
    assert result["ok"] is True
    assert result["n_samples"] == 60
    assert result["fs_eff"] == pytest.approx(100.0, rel=0.05)
    assert result["rows"][0]["hip_pitch_deg"] == "180.0"
    assert result["rows"][0]["t_epoch"] == pytest.approx(1_700_000_000.0)


def test_validate_component_csv_missing_file(tmp_path):
    result = engine.validate_component_csv(str(tmp_path / "nope.csv"), "accel")
    assert result["ok"] is False
    assert "nope.csv" in result["error"]
    assert result["rows"] == []


def test_validate_component_csv_wrong_header(tmp_path):
    path = tmp_path / "bad_header.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write("wrong,header,columns\n1,2,3\n")
    result = engine.validate_component_csv(str(path), "gyro")
    assert result["ok"] is False
    assert "bad_header.csv" in result["error"]


def test_validate_component_csv_sensor_name_mismatch(tmp_path):
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", _accel_rows_100hz(sensor_name="Gyroscope"))
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "Gyroscope" in result["error"]
    assert "Accelerometer" in result["error"]


def test_validate_component_csv_sensor_name_mismatch_second_pairing(tmp_path):
    """A second slot/sensor pairing, distinct from the accel/Gyroscope case
    above -- confirms the mismatch check isn't accidentally hardcoded to
    one slot."""
    path = tmp_path / "Trial_1_mag.csv"
    _write_component_csv(path, "mag", _accel_rows_100hz(sensor_name="Accelerometer"))
    result = engine.validate_component_csv(str(path), "mag")
    assert result["ok"] is False
    assert "Accelerometer" in result["error"]
    assert "Magnetometer" in result["error"]


def test_validate_component_csv_non_monotonic_timestamps(tmp_path):
    rows = _accel_rows_100hz(n=5)
    rows[3] = (5.0, 5, "proximal", "Accelerometer", 0.0, 0.0, 9.81)   # earlier than row 2's 20.0
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "row 5" in result["error"]


def test_validate_component_csv_fs_eff_below_floor(tmp_path):
    rows = [(i * 500.0, int(i * 500), "proximal", "Accelerometer", 0.0, 0.0, 9.81)
           for i in range(5)]   # 500ms spacing == 2 Hz, below the 10 Hz floor
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "2.00 Hz" in result["error"]


def test_validate_component_csv_fs_eff_at_floor_is_not_a_false_positive(tmp_path):
    rows = [(i * 80.0, int(i * 80), "proximal", "Accelerometer", 0.0, 0.0, 9.81)
           for i in range(5)]   # 80ms spacing == 12.5 Hz, above the 10 Hz floor
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is True


def test_validate_component_csv_too_few_rows(tmp_path):
    rows = [(0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 9.81)]
    path = tmp_path / "Trial_1_accel.csv"
    _write_component_csv(path, "accel", rows)
    result = engine.validate_component_csv(str(path), "accel")
    assert result["ok"] is False
    assert "1 data row" in result["error"]
```

`tests/test_workbench_engine.py` does not currently import `pytest` (only `os, sys, math, json, numpy, workbench_engine` at lines 1-5). Add `import pytest` as a new line 5, right after `import numpy as np`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k validate_component_csv -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'validate_component_csv'` (and no `_COMPONENT_HEADERS`).

- [ ] **Step 3: Implement `validate_component_csv()`**

Add to `workbench_engine.py`, directly above the existing `_SPLIT_CSV_SUFFIXES = {...}` line (~322) — the old constants/functions below it are removed in Task 3, so this new code sits alongside them for now:

```python
_COMPONENT_HEADERS = {
    "accel": ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "gyro":  ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "mag":   ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "imu":   ["t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned",
              "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg",
              "prox_roll", "prox_pitch", "prox_yaw",
              "dist_roll", "dist_pitch", "dist_yaw", "paired"],
}
_COMPONENT_SENSOR_NAME = {"accel": "Accelerometer", "gyro": "Gyroscope", "mag": "Magnetometer"}
_MIN_FS_FOR_FUSION_HZ = 10.0


def _empty_component_validation(error: str) -> dict:
    return {"ok": False, "error": error, "n_samples": 0, "fs_eff": None, "rows": []}


def validate_component_csv(path: str, kind: str) -> dict:
    """Validate one phone-IMU component CSV (kind: "accel"/"gyro"/"mag"/"imu")
    independently of the other three: header shape, per-row sensor_name
    consistency (accel/gyro/mag only -- imu has no sensor_name column),
    timestamp monotonicity, and fs_eff against _MIN_FS_FOR_FUSION_HZ.

    Never raises -- always returns {"ok", "error", "n_samples", "fs_eff",
    "rows"}, since the guided-intake UI needs a result for any slot, valid
    or not, to drive that slot's status readout (design spec Section 4)."""
    header = _COMPONENT_HEADERS[kind]
    if not os.path.exists(path):
        return _empty_component_validation(f"{path!r} does not exist.")

    rows = []
    prev_t = None
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        try:
            actual_header = next(reader)
        except StopIteration:
            return _empty_component_validation(
                f"{path!r} is empty (expected header {header}).")
        if actual_header != header:
            return _empty_component_validation(
                f"{path!r} has an unexpected header {actual_header!r}; expected {header}.")

        for row_num, row in enumerate(reader, start=2):
            if len(row) != len(header):
                return _empty_component_validation(
                    f"{path!r} row {row_num} has {len(row)} columns; expected {len(header)}.")
            record = dict(zip(header, row))

            if kind in _COMPONENT_SENSOR_NAME:
                expected_sensor = _COMPONENT_SENSOR_NAME[kind]
                actual_sensor = record["sensor_name"]
                if actual_sensor != expected_sensor:
                    return _empty_component_validation(
                        f"{path!r} row {row_num} has sensor_name {actual_sensor!r}; "
                        f"expected {expected_sensor!r} for the {kind} slot.")
                t = float(record["timestamp_ms"]) / 1000.0
                sample = {
                    "t": t, "role": record["role"], "sensor": kind,
                    "v": [float(record["x"]), float(record["y"]), float(record["z"])],
                    "phone_ts_ms": int(float(record["phone_ts_ms"])),
                }
            else:
                t = float(record["t_epoch"])
                sample = dict(record)
                sample["t_epoch"] = t

            if prev_t is not None and t < prev_t:
                return _empty_component_validation(
                    f"{path!r} row {row_num} has timestamp {t} which is earlier than "
                    f"the previous row's {prev_t} -- timestamps must be non-decreasing.")
            prev_t = t
            rows.append(sample)

    if len(rows) < 2:
        return _empty_component_validation(
            f"{path!r} has only {len(rows)} data row(s); at least 2 are needed to "
            f"compute an effective sample rate.")

    times = [r["t"] if kind in _COMPONENT_SENSOR_NAME else r["t_epoch"] for r in rows]
    fs_eff = 1.0 / float(np.median(np.diff(times)))
    if fs_eff < _MIN_FS_FOR_FUSION_HZ:
        return _empty_component_validation(
            f"{path!r} has an effective sample rate of {fs_eff:.2f} Hz, below the "
            f"{_MIN_FS_FOR_FUSION_HZ} Hz floor required for fusion.")

    return {"ok": True, "error": None, "n_samples": len(rows), "fs_eff": fs_eff, "rows": rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k validate_component_csv -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add validate_component_csv for per-file split-CSV validation"
```

---

### Task 2: `bind_split_csv_components()` and `load_imu_trial_from_components()`

**Files:**
- Modify: `workbench_engine.py` (directly below the code added in Task 1)
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `validate_component_csv(path, kind) -> dict` (Task 1), `_replay_samples(samples, config, ft_ratio, method)` (existing, line 307).
- Produces: `bind_split_csv_components(validations: dict) -> dict` returning `{"fusion_samples": list, "imu_reference": list}`. `load_imu_trial_from_components(validations: dict, config=None, ft_ratio=None, method=None) -> (t, angle, imu_reference)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py`, after Task 1's tests:

```python
def _write_full_component_set(tmp_path, prefix="Trial_1", fs=100.0, n=150):
    """All four component files, well-formed, at a consistent fs above the
    fusion floor -- unlike the old _write_solo_split_csv_trial fixture
    (kept for now, removed in Task 3), whose 2-row accel/mag files were
    fine for fusion (which only needs occasional accel/mag correction
    samples) but would fail this feature's own fs_eff floor if reused
    here, since fs_eff is now computed per-file, not just at fusion time."""
    dt_ms = 1000.0 / fs
    gyro_rows, accel_rows, mag_rows, imu_rows = [], [], [], []
    for i in range(n):
        t_ms = i * dt_ms
        gyro_rows.append((t_ms, int(t_ms), "proximal", "Gyroscope", 0.0, 0.0, 0.0))
        accel_rows.append((t_ms, int(t_ms), "proximal", "Accelerometer", 0.0, 0.0, 9.81))
        mag_rows.append((t_ms, int(t_ms), "proximal", "Magnetometer", -50.0, 20.0, 30.0))
        t_epoch = 1_700_000_000.0 + i / fs
        imu_rows.append((t_epoch, i / fs, int(t_ms), t_epoch,
                         0.0, 180.0, 0.0, 0.0, 90.0, 0.0, 0.0, 90.0, 0.0, True))

    paths = {
        "gyro": tmp_path / f"{prefix}_gyro.csv", "accel": tmp_path / f"{prefix}_accel.csv",
        "mag": tmp_path / f"{prefix}_mag.csv", "imu": tmp_path / f"{prefix}_imu.csv",
    }
    _write_component_csv(paths["gyro"], "gyro", gyro_rows)
    _write_component_csv(paths["accel"], "accel", accel_rows)
    _write_component_csv(paths["mag"], "mag", mag_rows)
    _write_component_csv(paths["imu"], "imu", imu_rows)
    return {kind: engine.validate_component_csv(str(p), kind) for kind, p in paths.items()}


def test_bind_split_csv_components_merges_and_sorts_fusion_samples(tmp_path):
    validations = _write_full_component_set(tmp_path)
    bound = engine.bind_split_csv_components(validations)
    ts = [s["t"] for s in bound["fusion_samples"]]
    assert ts == sorted(ts)
    assert {s["sensor"] for s in bound["fusion_samples"]} == {"gyro", "accel", "mag"}
    assert len(bound["fusion_samples"]) == 450   # 150 rows x 3 sensors


def test_bind_split_csv_components_keeps_imu_reference_separate(tmp_path):
    validations = _write_full_component_set(tmp_path)
    bound = engine.bind_split_csv_components(validations)
    assert len(bound["imu_reference"]) == 150
    assert all(s["sensor"] != "imu" for s in bound["fusion_samples"])
    assert bound["imu_reference"][0]["hip_pitch_deg"] == "180.0"


def test_bind_split_csv_components_raises_on_incomplete_set(tmp_path):
    validations = _write_full_component_set(tmp_path)
    del validations["mag"]
    try:
        engine.bind_split_csv_components(validations)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "mag" in str(e)


def test_bind_split_csv_components_raises_when_one_kind_not_ok(tmp_path):
    validations = _write_full_component_set(tmp_path)
    validations["gyro"] = {"ok": False, "error": "bad", "n_samples": 0,
                           "fs_eff": None, "rows": []}
    try:
        engine.bind_split_csv_components(validations)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "gyro" in str(e)


def test_load_imu_trial_from_components_produces_finite_angle_series(tmp_path):
    validations = _write_full_component_set(tmp_path)
    config = {"beta": 0.0, "ema_alpha": 1.0, "flex_axis_capture": True,
             "gravity_seed": True, "method": "relative"}
    t, angle, imu_reference = engine.load_imu_trial_from_components(validations, config=config)
    assert len(t) > 0
    assert len(angle) > 0
    assert np.isfinite(t).all()
    assert np.isfinite(angle).all()
    assert len(imu_reference) == 150
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k "bind_split_csv or load_imu_trial_from_components" -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'bind_split_csv_components'`

- [ ] **Step 3: Implement**

Add to `workbench_engine.py`, directly below `validate_component_csv()`:

```python
def bind_split_csv_components(validations: dict) -> dict:
    """Merge four independently-validated component results (Task 1's
    validate_component_csv, one call per kind) into the chronologically-
    sorted fusion sample list replay_trial() expects, plus a separate
    imu_reference list that is never merged into it (design spec Section 5
    -- the raw IMU file stays a cross-check field, not a fusion input).

    Defensive re-check: raises ValueError naming any kind that's missing
    or not ok. The intended caller (the guided-intake UI) only reaches
    this once all four slots are green, so this should never trigger in
    normal use."""
    not_ready = [kind for kind in ("accel", "gyro", "mag", "imu")
                if not validations.get(kind, {}).get("ok")]
    if not_ready:
        raise ValueError(
            f"Cannot bind split-CSV components: not yet validated: {', '.join(not_ready)}.")

    fusion_samples = []
    for kind in ("accel", "gyro", "mag"):
        fusion_samples.extend(validations[kind]["rows"])
    fusion_samples.sort(key=lambda s: s["t"])

    return {"fusion_samples": fusion_samples, "imu_reference": validations["imu"]["rows"]}


def load_imu_trial_from_components(validations: dict, config: Optional[dict] = None,
                                   ft_ratio: Optional[float] = None,
                                   method: Optional[str] = None):
    """Split-CSV counterpart to load_imu_trial(): binds four independently-
    validated component results and runs the merged accel/gyro/mag samples
    through the same Madgwick AHRS replay engine used everywhere else in
    this project. Returns (t, angle, imu_reference) -- imu_reference is
    the raw IMU file's parsed rows, attached for cross-check purposes
    only; it is never fed into fusion (design spec Section 1)."""
    bound = bind_split_csv_components(validations)
    t, angle = _replay_samples(bound["fusion_samples"], config, ft_ratio, method)
    return t, angle, bound["imu_reference"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k "bind_split_csv or load_imu_trial_from_components" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add bind_split_csv_components and load_imu_trial_from_components"
```

---

### Task 3: Restrict `load_imu_trial()` to JSONL and remove obsolete anchor-derivation code

**Files:**
- Modify: `workbench_engine.py:293-419` (the `_read_jsonl_samples` through `load_imu_trial` region)
- Test: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: `_read_jsonl_samples`, `_replay_samples` (existing, unchanged).
- Produces: `load_imu_trial(jsonl_path, config=None, ft_ratio=None, method=None) -> (t, angle)`, now `.jsonl`-only.
- Removes: `_derive_split_csv_siblings`, `_read_one_split_csv`, `_read_split_csv_samples`, `_SPLIT_CSV_SUFFIXES`, `_SPLIT_CSV_HEADER`, `_SENSOR_NAME_MAP` (all superseded by Tasks 1-2's `validate_component_csv`/`bind_split_csv_components`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_workbench_engine.py`, near the other `load_imu_trial` tests:

```python
def test_load_imu_trial_rejects_non_jsonl_path(tmp_path):
    path = tmp_path / "Trial_1_accel.csv"
    path.write_text("timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n", encoding="utf-8")
    try:
        engine.load_imu_trial(str(path))
        assert False, "expected ValueError"
    except ValueError as e:
        assert "load_imu_trial_from_components" in str(e)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k rejects_non_jsonl -v`
Expected: FAIL — today's `load_imu_trial` silently dispatches non-`.jsonl` paths to the old split-CSV reader instead of raising.

- [ ] **Step 3: Replace `load_imu_trial()` and delete obsolete code**

In `workbench_engine.py`, replace the entire block from `_SPLIT_CSV_SUFFIXES = {...}` (line 322) through the end of `load_imu_trial()` (line 419) — i.e. everything Tasks 1-2 left untouched alongside the new code — keeping only `_read_jsonl_samples` (293-304) and `_replay_samples` (307-319) as they are, and Tasks 1-2's new functions exactly as added. The old `_derive_split_csv_siblings`, `_read_one_split_csv`, `_read_split_csv_samples`, `_SPLIT_CSV_SUFFIXES`, `_SPLIT_CSV_HEADER`, `_SENSOR_NAME_MAP` are deleted entirely. Replace the old `load_imu_trial` with:

```python
def load_imu_trial(jsonl_path: str, config: Optional[dict] = None,
                   ft_ratio: Optional[float] = None,
                   method: Optional[str] = None):
    """Load a phone's raw accel/gyro/mag samples from a JSONL raw log
    (start_raw_log()'s format) and run them through the Madgwick AHRS
    replay engine (imu_calibration_tuner.replay_trial), returning the
    finite-filtered (t, angle) knee-angle series.

    Split-CSV trials no longer go through this function -- use
    load_imu_trial_from_components() with four validate_component_csv()
    results instead (design spec 2026-08-04-sequential-csv-intake).

    config defaults to the currently-persisted imu_calibration_config;
    ft_ratio/method optionally override the config's own values for this
    call only (the Ockendon-personalization workflow, design spec Section
    3a) without touching the persisted config file."""
    if not jsonl_path.endswith(".jsonl"):
        raise ValueError(
            f"load_imu_trial() only accepts a .jsonl path; got {jsonl_path!r}. "
            f"Split-CSV trials must go through load_imu_trial_from_components().")
    samples = _read_jsonl_samples(jsonl_path)
    return _replay_samples(samples, config, ft_ratio, method)
```

- [ ] **Step 4: Remove the now-obsolete anchor-derivation tests**

In `tests/test_workbench_engine.py`, delete these five tests entirely (they exercise `_derive_split_csv_siblings`/`_read_split_csv_samples`/anchor-based `load_imu_trial` dispatch, all removed above):
`test_read_split_csv_samples_merges_and_sorts_chronologically`,
`test_read_split_csv_samples_missing_sibling_names_the_file`,
`test_read_split_csv_samples_malformed_header_names_the_file`,
`test_read_split_csv_samples_unrecognized_sensor_name`,
`test_derive_split_csv_siblings_from_non_imu_anchor`,
`test_load_imu_trial_dispatches_to_split_csv_for_non_jsonl_path`,
`test_load_imu_trial_same_result_regardless_of_which_sibling_is_the_anchor`.

Also delete the now-unused `_write_split_csv` and `_write_solo_split_csv_trial` helper functions (lines 10-57) — Tasks 1-2 added their own fixture writers (`_write_component_csv`, `_accel_rows_100hz`, `_write_full_component_set`, etc.) that fully replace them, and no remaining test references the old ones.

- [ ] **Step 5: Run the full engine test suite to verify everything passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -v`
Expected: PASS, no failures, no errors about missing fixtures/functions.

- [ ] **Step 6: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "refactor: restrict load_imu_trial to JSONL, remove anchor-based split-CSV path"
```

---

### Task 4: `TrialLoadPanel` guided 4-slot UI in `pendulastic_workbench.py`

**Files:**
- Modify: `pendulastic_workbench.py:46-149` (`TrialLoadPanel`)
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: `engine.validate_component_csv(path, kind) -> dict` (Task 1).
- Produces: `TrialLoadPanel.get_selection() -> dict` now includes `"imu_format"` (`"jsonl"` or `"split_csv"`), `"imu_components"` (`{kind: validate_component_csv result + "path" key}`, only populated when `imu_format == "split_csv"`), alongside the existing `"imu_path"`, `"video_path"`, `"optitrack_path"`, `"models"`, `"femur_length_cm"`, `"tibia_length_cm"`. New attributes: `self._imu_format` (`tk.StringVar`), `self._component_paths`/`self._component_status` (`dict[str, tk.StringVar]`, keys `"accel"`/`"gyro"`/`"mag"`/`"imu"`), `self._component_validations` (`dict[str, dict]`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pendulastic_workbench.py`, after `test_imu_browse_button_accepts_csv_and_jsonl` — and **replace that test's body** (the jsonl-mode picker no longer accepts `.csv`, since split-CSV now goes through the 4-slot picker):

```python
def test_imu_jsonl_browse_button_only_accepts_jsonl(monkeypatch):
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
    assert "*.csv" not in exts


def test_split_csv_format_hides_jsonl_row_and_shows_component_rows():
    from pendulastic_workbench import TrialLoadPanel
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()
    r.update()
    assert p._imu_jsonl_frame.winfo_ismapped()
    assert not p._imu_split_frame.winfo_ismapped()

    p._imu_format.set("split_csv")
    p._on_imu_format_changed()
    r.update()
    assert not p._imu_jsonl_frame.winfo_ismapped()
    assert p._imu_split_frame.winfo_ismapped()


def test_component_browse_validates_and_updates_status(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()

    path = tmp_path / "Trial_1_accel.csv"
    path.write_text(
        "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"
        "0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n"
        "10.0,10,proximal,Accelerometer,0.0,0.0,9.81\n",
        encoding="utf-8")
    monkeypatch.setattr(_m.filedialog, "askopenfilename", lambda **kw: str(path))

    p._browse_component("accel")

    assert p._component_paths["accel"].get() == str(path)
    assert p._component_validations["accel"]["ok"] is True
    assert "100.0 Hz" in p._component_status["accel"].get()
    assert p._component_validations["accel"]["path"] == str(path)


def test_component_browse_shows_error_status_on_invalid_file(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()

    path = tmp_path / "bad.csv"
    path.write_text("wrong,header\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(_m.filedialog, "askopenfilename", lambda **kw: str(path))

    p._browse_component("gyro")

    assert p._component_validations["gyro"]["ok"] is False
    assert p._component_status["gyro"].get().startswith("✗")


def test_load_trial_blocks_on_incomplete_split_csv_slots(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_load_trial(self, selection):
            calls.append(selection)
    p = TrialLoadPanel(r, C())
    p.pack()
    p._imu_format.set("split_csv")
    p._on_imu_format_changed()

    path = tmp_path / "Trial_1_accel.csv"
    path.write_text(
        "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"
        "0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n"
        "10.0,10,proximal,Accelerometer,0.0,0.0,9.81\n",
        encoding="utf-8")
    monkeypatch.setattr(_m.filedialog, "askopenfilename", lambda **kw: str(path))
    p._browse_component("accel")   # only 1 of 4 filled

    errors = []
    monkeypatch.setattr(_m.messagebox, "showerror", lambda title, msg: errors.append(msg))
    p._on_load_clicked()

    assert calls == []
    assert len(errors) == 1
    assert "gyro" in errors[0] and "mag" in errors[0] and "imu" in errors[0]


def test_load_trial_proceeds_when_all_four_split_csv_slots_are_valid(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_load_trial(self, selection):
            calls.append(selection)
    p = TrialLoadPanel(r, C())
    p.pack()
    p._imu_format.set("split_csv")
    p._on_imu_format_changed()

    csv_bodies = {
        "accel": "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n10.0,10,proximal,Accelerometer,0.0,0.0,9.81\n",
        "gyro":  "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n0.0,0,proximal,Gyroscope,0.0,0.0,0.0\n10.0,10,proximal,Gyroscope,0.0,0.0,0.0\n",
        "mag":   "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n0.0,0,proximal,Magnetometer,-50.0,20.0,30.0\n10.0,10,proximal,Magnetometer,-50.0,20.0,30.0\n",
        "imu":   "t_epoch,t_rel,phone_ts_ms,t_phone_aligned,hip_roll_deg,hip_pitch_deg,hip_yaw_deg,prox_roll,prox_pitch,prox_yaw,dist_roll,dist_pitch,dist_yaw,paired\n"
                 "1700000000.0,0.0,0,1700000000.0,0.0,180.0,0.0,0.0,90.0,0.0,0.0,90.0,0.0,True\n"
                 "1700000000.01,0.01,10,1700000000.01,0.0,180.0,0.0,0.0,90.0,0.0,0.0,90.0,0.0,True\n",
    }
    paths = {}
    for kind, body in csv_bodies.items():
        path = tmp_path / f"Trial_1_{kind}.csv"
        path.write_text(body, encoding="utf-8")
        paths[kind] = path

    _next_kind = ["accel"]
    monkeypatch.setattr(_m.filedialog, "askopenfilename",
                        lambda **kw: str(paths[_next_kind[0]]))
    for kind in ("accel", "gyro", "mag", "imu"):
        _next_kind[0] = kind
        p._browse_component(kind)

    p._on_load_clicked()

    assert len(calls) == 1
    selection = calls[0]
    assert selection["imu_format"] == "split_csv"
    assert all(selection["imu_components"][k]["ok"] for k in ("accel", "gyro", "mag", "imu"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "split_csv or component_browse or imu_jsonl_browse or load_trial_blocks or load_trial_proceeds" -v`
Expected: FAIL — `TrialLoadPanel` has no `_imu_format`/`_imu_jsonl_frame`/`_imu_split_frame`/`_browse_component`/`_component_paths`/`_component_status`/`_component_validations` yet, and the jsonl browse button still advertises `.csv`.

- [ ] **Step 3: Implement**

In `pendulastic_workbench.py`, replace `TrialLoadPanel.__init__` (lines 53-64):

```python
    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._imu_path = tk.StringVar(value="")
        self._imu_format = tk.StringVar(value="jsonl")
        self._component_paths = {k: tk.StringVar(value="")
                                 for k in ("accel", "gyro", "mag", "imu")}
        self._component_status = {k: tk.StringVar(value="")
                                  for k in ("accel", "gyro", "mag", "imu")}
        self._component_validations: dict = {}
        self._video_path = tk.StringVar(value="")
        self._optitrack_path = tk.StringVar(value="")
        self._femur_cm = tk.StringVar(value="")
        self._tibia_cm = tk.StringVar(value="")
        self._model_vars = {name: tk.BooleanVar(value=False)
                            for name in analysis_pipeline.MODEL_FUNCTIONS}
        self._browse_buttons: dict = {}
        self._build_widgets()
```

Replace `_build_widgets` (lines 66-103) — the row indices for video/OptiTrack/HPE models/femur/tibia/Load Trial shift down by 1 (row 3 -> row 4, etc.) to make room for the new format toggle at row 2, and the old single `_file_row(2, "Phone IMU raw log...", ...)` call is replaced by the toggle + two swappable sub-frames both gridded at row 3:

```python
    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        self._back_button = tk.Button(
            self, text="← Back to Main Menu",
            command=lambda: self.controller.on_back_to_mode_select())
        self._back_button.grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 0))

        tk.Label(self, text="Pendulastic Workbench", font=("Segoe UI", 14, "bold")
                ).grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        tk.Label(self, text="IMU format:").grid(row=2, column=0, sticky="w", **pad)
        format_frame = tk.Frame(self)
        format_frame.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        tk.Radiobutton(format_frame, text="Single raw log (.jsonl)", variable=self._imu_format,
                      value="jsonl", command=self._on_imu_format_changed).pack(side="left")
        tk.Radiobutton(format_frame, text="Split CSV (4 files)", variable=self._imu_format,
                      value="split_csv", command=self._on_imu_format_changed
                      ).pack(side="left", padx=(12, 0))

        self._imu_jsonl_frame = tk.Frame(self)
        self._file_row(self._imu_jsonl_frame, 0, "Phone IMU raw log (.jsonl)", self._imu_path,
                       [("IMU log", "*.jsonl"), ("All files", "*.*")], name="imu")
        self._imu_jsonl_frame.grid(row=3, column=0, columnspan=3, sticky="we")

        self._imu_split_frame = tk.Frame(self)
        self._build_split_csv_rows(self._imu_split_frame)
        self._imu_split_frame.grid(row=3, column=0, columnspan=3, sticky="we")

        self._on_imu_format_changed()

        self._file_row(self, 4, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")], name="video")
        self._file_row(self, 5, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")], name="optitrack")

        tk.Label(self, text="HPE models to run:").grid(
            row=6, column=0, sticky="nw", **pad)
        model_frame = tk.Frame(self)
        model_frame.grid(row=6, column=1, columnspan=2, sticky="w", **pad)
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name]
                          ).grid(row=i // 3, column=i % 3, sticky="w", padx=4)

        tk.Label(self, text="Femur length (cm, optional):").grid(
            row=7, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._femur_cm, width=10).grid(
            row=7, column=1, sticky="w", **pad)

        tk.Label(self, text="Tibia length (cm, optional):").grid(
            row=8, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._tibia_cm, width=10).grid(
            row=8, column=1, sticky="w", **pad)

        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=9, column=0, columnspan=3, pady=16)

    _COMPONENT_LABELS = {"accel": "Accelerometer", "gyro": "Gyroscope",
                         "mag": "Magnetometer", "imu": "Raw IMU"}
    _COMPONENT_FILETYPES = [("CSV", "*.csv"), ("All files", "*.*")]

    def _build_split_csv_rows(self, parent) -> None:
        for i, kind in enumerate(("accel", "gyro", "mag", "imu")):
            tk.Label(parent, text=self._COMPONENT_LABELS[kind]).grid(
                row=i, column=0, sticky="w", padx=12, pady=4)
            tk.Entry(parent, textvariable=self._component_paths[kind], width=36,
                    state="readonly").grid(row=i, column=1, sticky="we", padx=4)
            tk.Button(parent, text="Browse...",
                     command=lambda k=kind: self._browse_component(k)
                     ).grid(row=i, column=2, sticky="w", padx=4)
            tk.Label(parent, textvariable=self._component_status[kind], anchor="w", width=32
                    ).grid(row=i, column=3, sticky="w", padx=(8, 12))

    def _on_imu_format_changed(self) -> None:
        if self._imu_format.get() == "split_csv":
            self._imu_jsonl_frame.grid_remove()
            self._imu_split_frame.grid()
        else:
            self._imu_split_frame.grid_remove()
            self._imu_jsonl_frame.grid()

    def _browse_component(self, kind: str) -> None:
        path = filedialog.askopenfilename(filetypes=self._COMPONENT_FILETYPES)
        if not path:
            return
        self._component_paths[kind].set(path)
        result = engine.validate_component_csv(path, kind)
        self._component_validations[kind] = dict(result, path=path)
        if result["ok"]:
            self._component_status[kind].set(
                f"✓ {result['n_samples']} samples @ {result['fs_eff']:.1f} Hz")
        else:
            self._component_status[kind].set(f"✗ {result['error']}")
```

Replace `_file_row` (lines 105-113) to take an explicit `parent`:

```python
    def _file_row(self, parent, row: int, label: str, var: tk.StringVar, filetypes,
                  name: str) -> None:
        tk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(parent, textvariable=var, width=48, state="readonly").grid(
            row=row, column=1, sticky="we", padx=4)
        btn = tk.Button(parent, text="Browse...",
                       command=lambda: self._browse(var, filetypes))
        btn.grid(row=row, column=2, sticky="w", padx=4)
        self._browse_buttons[name] = btn
```

Replace `get_selection` and `_on_load_clicked` (lines 120-149):

```python
    def get_selection(self) -> dict:
        """Snapshot of the current form state. Numeric fields left blank
        parse to None (Section 3a: leaving them blank keeps the default
        1.2 femur:tibia ratio unchanged)."""
        def _parse_float(s: str) -> Optional[float]:
            s = s.strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        imu_format = self._imu_format.get()
        return {
            "imu_format": imu_format,
            "imu_path": self._imu_path.get() or None,
            "imu_components": dict(self._component_validations) if imu_format == "split_csv" else {},
            "video_path": self._video_path.get() or None,
            "optitrack_path": self._optitrack_path.get() or None,
            "models": [name for name, var in self._model_vars.items() if var.get()],
            "femur_length_cm": _parse_float(self._femur_cm.get()),
            "tibia_length_cm": _parse_float(self._tibia_cm.get()),
        }

    def _on_load_clicked(self) -> None:
        selection = self.get_selection()

        if selection["imu_format"] == "split_csv":
            has_any = any(self._component_paths[k].get() for k in ("accel", "gyro", "mag", "imu"))
            missing_or_invalid = [k for k in ("accel", "gyro", "mag", "imu")
                                  if not selection["imu_components"].get(k, {}).get("ok")]
            if has_any and missing_or_invalid:
                messagebox.showerror(
                    "Incomplete IMU intake",
                    "The following component(s) still need a valid file before the IMU "
                    "trace can be bound: " + ", ".join(missing_or_invalid))
                return
            imu_ready = has_any and not missing_or_invalid
        else:
            imu_ready = bool(selection["imu_path"])

        if not any([imu_ready, selection["video_path"], selection["optitrack_path"]]):
            messagebox.showerror("No trial data",
                                 "Select at least one of: IMU log, video, OptiTrack CSV.")
            return
        self.controller.on_load_trial(selection)
```

- [ ] **Step 4: Update the existing `_browse` calls that already reference `self` as parent**

`_browse` itself (unchanged) is fine as-is since it only takes `var`/`filetypes`, not a parent — no change needed there.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: PASS, full file (existing `WorkbenchView`/back-button/load-another tests must still pass unchanged).

- [ ] **Step 6: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: add guided 4-slot split-CSV picker to TrialLoadPanel"
```

---

### Task 5: Wire the split-CSV path into `App.on_load_trial()` (`pendulastic_workbench.py`)

**Files:**
- Modify: `pendulastic_workbench.py:539-583` (`App.on_load_trial`)
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: `TrialLoadPanel.get_selection()`'s new `"imu_format"`/`"imu_components"` keys (Task 4), `engine.load_imu_trial_from_components(validations, ft_ratio=None, method=None) -> (t, angle, imu_reference)` (Task 2).
- Produces: `self._trial_meta["imu_paths"]` (`dict[str, str]`, split-CSV case) and `self._trial_meta["imu_reference"]` (`list`, split-CSV case), alongside the existing `self._trial_meta["imu_path"]` (jsonl case, unchanged).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pendulastic_workbench.py`:

```python
def test_on_load_trial_split_csv_binds_and_stores_imu_reference(tmp_path, monkeypatch):
    from pendulastic_workbench import App
    import pendulastic_workbench as _m
    import numpy as np

    fake_validations = {
        "accel": {"ok": True, "rows": [], "path": "a.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "gyro":  {"ok": True, "rows": [], "path": "g.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "mag":   {"ok": True, "rows": [], "path": "m.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "imu":   {"ok": True, "rows": [{"hip_pitch_deg": "180.0"}], "path": "i.csv",
                  "error": None, "n_samples": 1, "fs_eff": 100.0},
    }
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial_from_components": staticmethod(
            lambda validations, ft_ratio=None, method=None:
                (np.array([0.0, 0.05]), np.array([180.0, 170.0]), validations["imu"]["rows"]))
    })()
    monkeypatch.setattr(_m, "engine", fake_engine)

    app = App()
    try:
        app.update()
        app.on_load_trial({
            "imu_format": "split_csv", "imu_path": None, "imu_components": fake_validations,
            "video_path": None, "optitrack_path": None, "models": [],
            "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert "imu" in app._workbench_view._traces
        assert app._trial_meta["imu_paths"] == {
            "accel": "a.csv", "gyro": "g.csv", "mag": "m.csv", "imu": "i.csv"}
        assert app._trial_meta["imu_reference"] == [{"hip_pitch_deg": "180.0"}]
    finally:
        app.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k split_csv_binds -v`
Expected: FAIL — `on_load_trial` currently does `selection["imu_path"]` unconditionally and never calls `load_imu_trial_from_components`.

- [ ] **Step 3: Implement**

Replace `App.on_load_trial` in `pendulastic_workbench.py` (lines 539-583):

```python
    def on_load_trial(self, selection: dict) -> None:
        """Loads whichever of the three modalities were selected (design
        spec Section 2: 2-of-3 is valid) and switches to WorkbenchView.
        Video HPE model inference runs on a background thread since it's
        the slow step (design spec Section 3); IMU/OptiTrack loading is
        fast enough to run inline. IMU input is either a single JSONL raw
        log or four independently-validated split-CSV components (design
        spec 2026-08-04-sequential-csv-intake) -- TrialLoadPanel.get_selection()
        distinguishes the two via selection["imu_format"]."""
        traces = {}
        imu_format = selection.get("imu_format", "jsonl")
        self._trial_meta = {
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        ft_ratio = None
        method_override = None
        if selection["femur_length_cm"] and selection["tibia_length_cm"]:
            ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
            method_override = "ockendon_flipped"

        if imu_format == "split_csv":
            components = selection.get("imu_components", {})
            if all(components.get(k, {}).get("ok") for k in ("accel", "gyro", "mag", "imu")):
                try:
                    t, angle, imu_reference = engine.load_imu_trial_from_components(
                        components, ft_ratio=ft_ratio, method=method_override)
                    traces["imu"] = (t, angle)
                    self._trial_meta["imu_paths"] = {k: components[k]["path"] for k in components}
                    self._trial_meta["imu_reference"] = imu_reference
                except Exception as e:
                    messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")
        elif selection["imu_path"]:
            self._trial_meta["imu_path"] = selection["imu_path"]
            try:
                t, angle = engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

        if selection["optitrack_path"]:
            try:
                t, angle, method = engine.load_optitrack_trial(selection["optitrack_path"])
                traces["optitrack"] = (t, angle)
                self._trial_meta["optitrack_method"] = method
            except Exception as e:
                messagebox.showerror("OptiTrack load error", f"{type(e).__name__}: {e}")

        self._load_panel.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: PASS, full file (the jsonl-path tests earlier in the file lack `imu_format`/`imu_components` keys but `selection.get("imu_format", "jsonl")` defaults them to the unchanged jsonl branch — verify none of those break).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: bind split-CSV components in Workbench App.on_load_trial"
```

---

### Task 6: Wire the split-CSV path into `App.on_load_trial()` (`pendulastic_app.py`)

**Files:**
- Modify: `pendulastic_app.py:1356-1396` (`App.on_load_trial`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: same as Task 5, but via `_wb_engine` (this file's alias for `workbench_engine`, per its guarded import at line 96) and `self._workbench_trial_meta` (this file's name for the trial-meta dict, distinct from `pendulastic_workbench.py`'s `self._trial_meta`).
- Produces: `self._workbench_trial_meta["imu_paths"]`/`["imu_reference"]`, mirroring Task 5.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`, after `test_get_trial_meta_reflects_last_loaded_selection`:

```python
def test_on_load_trial_split_csv_binds_and_stores_imu_reference(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)

    fake_validations = {
        "accel": {"ok": True, "rows": [], "path": "a.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "gyro":  {"ok": True, "rows": [], "path": "g.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "mag":   {"ok": True, "rows": [], "path": "m.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "imu":   {"ok": True, "rows": [{"hip_pitch_deg": "180.0"}], "path": "i.csv",
                  "error": None, "n_samples": 1, "fs_eff": 100.0},
    }
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial_from_components": staticmethod(
            lambda validations, ft_ratio=None, method=None:
                (np.array([0.0, 0.05]), np.array([180.0, 170.0]), validations["imu"]["rows"]))
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        app.on_load_trial({
            "imu_format": "split_csv", "imu_path": None, "imu_components": fake_validations,
            "video_path": None, "optitrack_path": None, "models": [],
            "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert "imu" in app._workbench_view._traces
        meta = app.get_trial_meta()
        assert meta["imu_paths"] == {"accel": "a.csv", "gyro": "g.csv", "mag": "m.csv", "imu": "i.csv"}
        assert meta["imu_reference"] == [{"hip_pitch_deg": "180.0"}]
    finally:
        app.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k split_csv_binds -v`
Expected: FAIL — same reason as Task 5, in this file's copy of `on_load_trial`.

- [ ] **Step 3: Implement**

Replace `App.on_load_trial` in `pendulastic_app.py` (lines 1356-1396):

```python
    def on_load_trial(self, selection: dict) -> None:
        """Loads whichever of the three modalities were selected (design
        spec Section 2: 2-of-3 is valid) and switches to WorkbenchView.
        Video HPE model inference runs on a background thread since it's
        the slow step (design spec Section 3); IMU/OptiTrack loading is
        fast enough to run inline. IMU input is either a single JSONL raw
        log or four independently-validated split-CSV components (design
        spec 2026-08-04-sequential-csv-intake)."""
        traces = {}
        imu_format = selection.get("imu_format", "jsonl")
        self._workbench_trial_meta = {
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        ft_ratio = None
        method_override = None
        if selection["femur_length_cm"] and selection["tibia_length_cm"]:
            ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
            method_override = "ockendon_flipped"

        if imu_format == "split_csv":
            components = selection.get("imu_components", {})
            if all(components.get(k, {}).get("ok") for k in ("accel", "gyro", "mag", "imu")):
                try:
                    t, angle, imu_reference = _wb_engine.load_imu_trial_from_components(
                        components, ft_ratio=ft_ratio, method=method_override)
                    traces["imu"] = (t, angle)
                    self._workbench_trial_meta["imu_paths"] = {k: components[k]["path"] for k in components}
                    self._workbench_trial_meta["imu_reference"] = imu_reference
                except Exception as e:
                    messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")
        elif selection["imu_path"]:
            self._workbench_trial_meta["imu_path"] = selection["imu_path"]
            try:
                t, angle = _wb_engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

        if selection["optitrack_path"]:
            try:
                t, angle, method = _wb_engine.load_optitrack_trial(selection["optitrack_path"])
                traces["optitrack"] = (t, angle)
                self._workbench_trial_meta["optitrack_method"] = method
            except Exception as e:
                messagebox.showerror("OptiTrack load error", f"{type(e).__name__}: {e}")

        self._workbench_load.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)

        if selection["video_path"]:
            self._workbench_view.load_video(selection["video_path"])
            if selection["models"]:
                self._load_workbench_video_models_async(
                    selection["video_path"], selection["models"], traces)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: PASS, full file (`test_on_load_trial_imu_only_switches_to_workbench_view` and `test_get_trial_meta_reflects_last_loaded_selection` lack `imu_format`/`imu_components` but default to the unchanged jsonl branch via `.get(..., "jsonl")` — verify both still pass).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: bind split-CSV components in main app's App.on_load_trial"
```

---

### Task 7: Manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py tests/test_pendulastic_workbench.py tests/test_app.py -v`
Expected: PASS, no failures.

- [ ] **Step 2: Launch the standalone Workbench and exercise the guided intake with real files**

Run: `.venv\Scripts\python.exe pendulastic_workbench.py`

- Switch the IMU format toggle to "Split CSV (4 files)"; confirm the single JSONL row disappears and four labeled slots (Accelerometer, Gyroscope, Magnetometer, Raw IMU) appear.
- Browse an existing split-CSV trial's sibling files one at a time (e.g. any `*_accel.csv`/`*_gyro.csv`/`*_mag.csv`/`*_imu.csv` set under `data/`, if present, or synthetic files matching the schemas in this plan's Global Constraints). Confirm each slot shows a green ✓ status with sample count and Hz after being browsed.
- Deliberately browse a mismatched file into one slot (e.g. a `_gyro.csv` into the Accelerometer slot) and confirm that slot shows a red ✗ status naming the sensor mismatch, and clicking "Load Trial" with only 3 of 4 slots green shows the "Incomplete IMU intake" error naming the still-invalid slot.
- Fix the mismatched slot, confirm all four go green, click "Load Trial", and confirm the Workbench view opens with an "imu" trace plotted.

- [ ] **Step 3: Repeat inside the main app's Workbench mode**

Run: `.venv\Scripts\python.exe pendulastic_app.py`, enter Workbench mode, repeat the same check — confirms the duplicated `on_load_trial` in `pendulastic_app.py` behaves identically to the standalone app's.

No commit for this task — it's verification, not a code change.
