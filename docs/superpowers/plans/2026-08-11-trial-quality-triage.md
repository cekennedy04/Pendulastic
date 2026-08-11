# Trial Quality Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a researcher tag a trial's quality problem (compromised calibration hold, OptiTrack marker occlusion, mounting slip, release contamination) while reviewing it in the Workbench, with computed signals pre-filling a suggested category, and see the RMSE report stratified by "all trials" vs. "excluding each tag category" so algorithm capability is visible separately from known hardware/capture noise.

**Architecture:** Two new JSON registries in `pt_report_common.py` (`trial_quality_tags.json` for stratification-only tags, a new writer added for the existing `excluded_trials.json` hard-exclusion list), a new signal-computation layer in `workbench_engine.py` that reads raw sensor CSVs and the already-loaded OptiTrack trace to produce quality signals and a suggested tag, and a single Workbench dialog that writes to one or both registries in one action. `batch_imu_vs_optitrack_rmse.py` gets wired to respect exclusions (it doesn't today) and to print the stratified breakdown.

**Tech Stack:** Python, Tkinter (Workbench UI), pytest, numpy, existing project modules (`pt_report_common`, `workbench_engine`, `pendulastic_imu_server`, `pendulastic_pt_score`, `batch_imu_vs_optitrack_rmse`, `pendulastic_workbench`, `pendulastic_app`).

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-08-11-trial-quality-triage-design.md` — every requirement below traces to a section there.
- Category enum is exactly: `calibration_hold`, `marker_occlusion`, `mounting_slip`, `release_contamination`, `other` (spec Section 3).
- All new JSON writes use the temp-file-then-`os.replace()` atomic pattern already established by `imu_calibration_config.save_config()` — no other write strategy.
- `trial_quality_tags.json` never removes a trial from `discover_trials()`/`discover_all_trials()` — only `excluded_trials.json` does that (spec Section 3).
- Threshold constants for auto-suggestion are explicitly seed values pending corpus validation — do not present them as tuned.
- Tkinter dialog interaction itself is not unit-tested, matching this codebase's existing practice for `_on_save_trial_clicked` — verified by manual smoke test instead (spec Section 7).
- `tests/test_pt_report_common.py` currently has unresolved git merge-conflict markers and fails to import — pre-existing, unrelated to this work. New tests for `pt_report_common.py` additions go in a new file, `tests/test_trial_quality_tags.py`, so this plan's test suite isn't blocked by that pre-existing breakage.

---

## Task 1: Quality-tag and exclusion-writer registries in `pt_report_common.py`

**Files:**
- Modify: `pt_report_common.py`
- Create: `tests/test_trial_quality_tags.py`

**Interfaces:**
- Produces: `pt_report_common.REC_ROOT: str`, `pt_report_common.TRIAL_QUALITY_TAGS_PATH: str`, `pt_report_common.QUALITY_TAG_CATEGORIES: tuple[str, ...]`, `pt_report_common.load_quality_tags() -> dict`, `pt_report_common.save_quality_tag(key: str, category: str, details: str = "", timestamp: str | None = None) -> None` (raises `ValueError` on invalid category), `pt_report_common.clear_quality_tag(key: str) -> None`, `pt_report_common.add_excluded_trial(key: str, reason: str) -> None`, `pt_report_common.clear_excluded_trial(key: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trial_quality_tags.py`:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import pt_report_common


@pytest.fixture(autouse=True)
def _isolated_registries(tmp_path, monkeypatch):
    """Every test gets its own empty registry files so tests never read/
    write the real trial_quality_tags.json or excluded_trials.json."""
    monkeypatch.setattr(pt_report_common, "TRIAL_QUALITY_TAGS_PATH",
                        str(tmp_path / "trial_quality_tags.json"))
    monkeypatch.setattr(pt_report_common, "EXCLUDED_TRIALS_PATH",
                        str(tmp_path / "excluded_trials.json"))
    yield


def test_load_quality_tags_missing_file_returns_empty_dict():
    assert pt_report_common.load_quality_tags() == {}


def test_save_quality_tag_then_load_round_trips():
    pt_report_common.save_quality_tag("5_left_pre_T1", "calibration_hold",
                                      "hold tilted", timestamp="2026-08-11T00:00:00+00:00")
    tags = pt_report_common.load_quality_tags()
    assert tags == {
        "5_left_pre_T1": {
            "category": "calibration_hold",
            "details": "hold tilted",
            "timestamp": "2026-08-11T00:00:00+00:00",
        }
    }


def test_save_quality_tag_defaults_timestamp_when_not_given():
    pt_report_common.save_quality_tag("5_left_pre_T1", "other", "misc note")
    tags = pt_report_common.load_quality_tags()
    assert tags["5_left_pre_T1"]["timestamp"]  # non-empty, auto-filled


def test_save_quality_tag_rejects_invalid_category():
    with pytest.raises(ValueError, match="invalid category"):
        pt_report_common.save_quality_tag("5_left_pre_T1", "not_a_real_category", "x")
    assert pt_report_common.load_quality_tags() == {}


def test_save_quality_tag_overwrites_existing_entry_for_same_key():
    pt_report_common.save_quality_tag("5_left_pre_T1", "calibration_hold", "first",
                                      timestamp="2026-08-11T00:00:00+00:00")
    pt_report_common.save_quality_tag("5_left_pre_T1", "marker_occlusion", "second",
                                      timestamp="2026-08-11T01:00:00+00:00")
    tags = pt_report_common.load_quality_tags()
    assert len(tags) == 1
    assert tags["5_left_pre_T1"]["category"] == "marker_occlusion"
    assert tags["5_left_pre_T1"]["details"] == "second"


def test_clear_quality_tag_removes_entry():
    pt_report_common.save_quality_tag("5_left_pre_T1", "other", "x")
    pt_report_common.clear_quality_tag("5_left_pre_T1")
    assert pt_report_common.load_quality_tags() == {}


def test_clear_quality_tag_is_noop_when_key_not_tagged():
    pt_report_common.clear_quality_tag("does_not_exist")  # must not raise
    assert pt_report_common.load_quality_tags() == {}


def test_add_excluded_trial_then_load_round_trips():
    pt_report_common.add_excluded_trial("5_left_pre_T1", "mounting slip visible on video")
    excluded = pt_report_common.load_excluded_trials()
    assert excluded == {"5_left_pre_T1": "mounting slip visible on video"}


def test_add_excluded_trial_preserves_other_existing_entries():
    pt_report_common.add_excluded_trial("13_right_post_T2", "muscle intervention")
    pt_report_common.add_excluded_trial("5_left_pre_T1", "mounting slip")
    excluded = pt_report_common.load_excluded_trials()
    assert excluded == {
        "13_right_post_T2": "muscle intervention",
        "5_left_pre_T1": "mounting slip",
    }


def test_clear_excluded_trial_removes_entry_and_preserves_others():
    pt_report_common.add_excluded_trial("13_right_post_T2", "muscle intervention")
    pt_report_common.add_excluded_trial("5_left_pre_T1", "mounting slip")
    pt_report_common.clear_excluded_trial("5_left_pre_T1")
    excluded = pt_report_common.load_excluded_trials()
    assert excluded == {"13_right_post_T2": "muscle intervention"}


def test_clear_excluded_trial_is_noop_when_key_not_excluded():
    pt_report_common.clear_excluded_trial("does_not_exist")  # must not raise
    assert pt_report_common.load_excluded_trials() == {}


def test_quality_tag_write_uses_atomic_replace_not_direct_write(tmp_path, monkeypatch):
    """Confirms the temp-file-then-os.replace pattern: after a save, no
    leftover .tmp file exists, and the real file exists."""
    monkeypatch.setattr(pt_report_common, "TRIAL_QUALITY_TAGS_PATH",
                        str(tmp_path / "tags.json"))
    pt_report_common.save_quality_tag("5_left_pre_T1", "other", "x")
    assert os.path.exists(str(tmp_path / "tags.json"))
    assert not os.path.exists(str(tmp_path / "tags.json.tmp"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trial_quality_tags.py -v`
Expected: FAIL — `AttributeError: module 'pt_report_common' has no attribute 'load_quality_tags'` (and similar for the other new names).

- [ ] **Step 3: Add the imports and constants**

In `pt_report_common.py`, add to the imports near the top of the file (after the existing `import sys` on line 17):

```python
from datetime import datetime, timezone
```

Immediately after the existing `OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")` line, add:

```python
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
```

Immediately after the existing `EXCLUDED_TRIALS_PATH = os.path.join(BASE_DIR, "excluded_trials.json")` line (currently line 239), add:

```python
TRIAL_QUALITY_TAGS_PATH = os.path.join(BASE_DIR, "trial_quality_tags.json")

# Stratification-only tag categories (design spec
# docs/superpowers/specs/2026-08-11-trial-quality-triage-design.md Section
# 3) -- distinct from EXCLUDED_TRIALS_PATH's free-text reasons, which stay
# unvalidated (existing behavior, unchanged).
QUALITY_TAG_CATEGORIES = ("calibration_hold", "marker_occlusion", "mounting_slip",
                          "release_contamination", "other")
```

- [ ] **Step 4: Implement the atomic-write helper and the six new functions**

Immediately after `load_excluded_trials()`'s closing (currently ending at line 264, before `def trial_candidates(...)`), add:

```python
def _atomic_write_json(path, data):
    """Temp-file-then-os.replace atomic write, matching
    imu_calibration_config.save_config()'s existing pattern."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def load_quality_tags():
    """{trial_key: {"category","details","timestamp"}} for trials tagged
    with a quality concern. Drives stratified reporting only -- presence
    here never removes a trial from discover_all_trials()/discover_trials()
    (design spec Section 3). Missing or malformed file -> {}, matching
    load_excluded_trials()'s convention."""
    try:
        with open(TRIAL_QUALITY_TAGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_quality_tag(key, category, details="", timestamp=None):
    """Record (or overwrite) one trial's quality tag. Raises ValueError (no
    write attempted) if category isn't one of QUALITY_TAG_CATEGORIES,
    mirroring mas_validation._valid_grade()'s existing gate on mas_grade."""
    if category not in QUALITY_TAG_CATEGORIES:
        raise ValueError(
            f"invalid category {category!r} (must be one of {QUALITY_TAG_CATEGORIES})")
    tags = load_quality_tags()
    tags[key] = {
        "category": category,
        "details": details,
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(TRIAL_QUALITY_TAGS_PATH, tags)


def clear_quality_tag(key):
    """Remove key's tag if present. No-op (not an error) if key isn't tagged."""
    tags = load_quality_tags()
    if key in tags:
        del tags[key]
        _atomic_write_json(TRIAL_QUALITY_TAGS_PATH, tags)


def add_excluded_trial(key, reason):
    """Add (or overwrite) key's hard-exclusion reason. No validation on
    reason -- exclusion reasons have always been free text (see this file's
    existing muscle-intervention entries); only quality tags (above) get a
    validated category."""
    excluded = load_excluded_trials()
    excluded[key] = reason
    _atomic_write_json(EXCLUDED_TRIALS_PATH, excluded)


def clear_excluded_trial(key):
    """Remove key's exclusion entry if present. No-op if key isn't excluded."""
    excluded = load_excluded_trials()
    if key in excluded:
        del excluded[key]
        _atomic_write_json(EXCLUDED_TRIALS_PATH, excluded)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trial_quality_tags.py -v`
Expected: PASS (13 tests).

- [ ] **Step 6: Run the full existing suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_imu_vs_optitrack_rmse.py tests/test_workbench_engine.py tests/test_imu_server.py tests/test_imu_calibration_tuner.py tests/test_trial_quality_tags.py -q`
Expected: PASS, no failures (this touches `pt_report_common.py`, imported by `batch_imu_vs_optitrack_rmse.py`, so re-running its suite guards against an import-time break).

- [ ] **Step 7: Commit**

```bash
git add pt_report_common.py tests/test_trial_quality_tags.py
git commit -m "feat: add trial quality tag and exclusion-writer registries"
```

---

## Task 2: Quality-signal computation in `workbench_engine.py`

**Files:**
- Modify: `workbench_engine.py`
- Modify: `tests/test_workbench_engine.py`

**Interfaces:**
- Consumes: nothing new from Task 1 (this task's functions don't call `pt_report_common` — the caller in Task 4 is responsible for passing the suggested category string into `save_quality_tag`, which validates it).
- Produces: `workbench_engine.compute_raw_sensor_diagnostics(anchor_path: str) -> dict` (existing function, return dict gains two new keys `hold_gravity_z_frac: float | None` and `hold_stillness_ok: bool | None`), `workbench_engine.compute_optitrack_quality_signals(ref_t: np.ndarray, ref_angle: np.ndarray) -> dict` (new: `{"optitrack_dropout_frac": float, "optitrack_area_ratio_warn": bool}`), `workbench_engine.suggest_quality_tag(raw_diagnostics: dict, optitrack_signals: dict) -> dict` (new, pure: `{"category": str | None, "details": str}`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workbench_engine.py` (after the existing `_imu_reference_rows_100hz` helper, before the first `def test_` function):

```python
def test_compute_raw_sensor_diagnostics_hold_gravity_z_frac_level_hold(tmp_path):
    """A hold where gravity reads (0,0,9.81) -- perfectly level -- must
    report hold_gravity_z_frac close to 1.0."""
    base = tmp_path / "Trial_1"
    _write_component_csv(str(base) + "_accel.csv", "accel", _accel_rows_100hz(n=150))
    gyro_rows = [(i * 10.0, int(i * 10), "proximal", "Gyroscope", 0.0, 0.0, 0.0)
                for i in range(150)]
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    assert diag["hold_gravity_z_frac"] == pytest.approx(1.0, abs=1e-6)
    assert diag["hold_stillness_ok"] is True


def test_compute_raw_sensor_diagnostics_hold_gravity_z_frac_tilted_hold(tmp_path):
    """A hold whose measured gravity is spread across X/Y as well as Z
    (a real tilted-hold shape, e.g. ~[5,5,8]) must report a fraction well
    below 1.0."""
    base = tmp_path / "Trial_1"
    accel_rows = [(i * 10.0, int(i * 10), "proximal", "Accelerometer", 5.0, 5.0, 8.0)
                 for i in range(150)]
    _write_component_csv(str(base) + "_accel.csv", "accel", accel_rows)
    gyro_rows = [(i * 10.0, int(i * 10), "proximal", "Gyroscope", 0.0, 0.0, 0.0)
                for i in range(150)]
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    expected = 8.0 / (5.0 ** 2 + 5.0 ** 2 + 8.0 ** 2) ** 0.5
    assert diag["hold_gravity_z_frac"] == pytest.approx(expected, abs=1e-6)


def test_compute_raw_sensor_diagnostics_hold_stillness_ok_false_when_handled(tmp_path):
    """A hold with real gyro motion (examiner still handling the sensor)
    must report hold_stillness_ok=False, not silently pass."""
    base = tmp_path / "Trial_1"
    _write_component_csv(str(base) + "_accel.csv", "accel", _accel_rows_100hz(n=150))
    gyro_rows = [(i * 10.0, int(i * 10), "proximal", "Gyroscope",
                 1.5 if i % 2 == 0 else -1.5, 0.0, 0.0)
                for i in range(150)]   # oscillating +/-1.5 rad/s, above GYRO_STATIONARY_MAX_RAD_S
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    assert diag["hold_stillness_ok"] is False


def test_compute_raw_sensor_diagnostics_hold_fields_none_when_too_few_samples(tmp_path):
    """Fewer than GYRO_BIAS_MIN_SAMPLES in the hold window must report None
    (unknown), never a misleadingly-computed value from too little data."""
    base = tmp_path / "Trial_1"
    accel_rows = [(0.0, 0, "proximal", "Accelerometer", 0.0, 0.0, 9.81)]
    _write_component_csv(str(base) + "_accel.csv", "accel", accel_rows)
    gyro_rows = [(0.0, 0, "proximal", "Gyroscope", 0.0, 0.0, 0.0)]
    _write_component_csv(str(base) + "_gyro.csv", "gyro", gyro_rows)

    diag = engine.compute_raw_sensor_diagnostics(str(base) + "_accel.csv")
    assert diag["hold_gravity_z_frac"] is None
    assert diag["hold_stillness_ok"] is None


def test_compute_optitrack_quality_signals_no_dropout_no_warn():
    t = np.linspace(0, 5, 200)
    angle = 180.0 - 10.0 * np.sin(t)   # small, smooth oscillation -> low area_ratio
    signals = engine.compute_optitrack_quality_signals(t, angle)
    assert signals["optitrack_dropout_frac"] == pytest.approx(0.0)
    assert signals["optitrack_area_ratio_warn"] is False


def test_compute_optitrack_quality_signals_reports_dropout_fraction():
    t = np.linspace(0, 5, 200)
    angle = 180.0 - 10.0 * np.sin(t)
    angle[:80] = np.nan   # 40% missing
    signals = engine.compute_optitrack_quality_signals(t, angle)
    assert signals["optitrack_dropout_frac"] == pytest.approx(0.40, abs=1e-6)


def test_suggest_quality_tag_no_signal_returns_none_category():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion == {"category": None, "details": ""}


def test_suggest_quality_tag_tilted_hold_suggests_calibration_hold():
    raw = {"hold_gravity_z_frac": 0.57, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "calibration_hold"
    assert "57%" in suggestion["details"]


def test_suggest_quality_tag_unstable_hold_suggests_calibration_hold():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": False}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "calibration_hold"


def test_suggest_quality_tag_high_dropout_suggests_marker_occlusion():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.45, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "marker_occlusion"
    assert "45%" in suggestion["details"]


def test_suggest_quality_tag_area_ratio_warn_suggests_marker_occlusion():
    raw = {"hold_gravity_z_frac": 0.99, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.01, "optitrack_area_ratio_warn": True}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "marker_occlusion"


def test_suggest_quality_tag_calibration_hold_takes_priority_over_dropout():
    """When both a tilted hold and high dropout are present, calibration
    hold wins -- it's checked first (design spec Section 4's ordered rule
    list)."""
    raw = {"hold_gravity_z_frac": 0.50, "hold_stillness_ok": True}
    opti = {"optitrack_dropout_frac": 0.60, "optitrack_area_ratio_warn": False}
    suggestion = engine.suggest_quality_tag(raw, opti)
    assert suggestion["category"] == "calibration_hold"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -k "quality_signals or suggest_quality_tag or hold_gravity or hold_stillness" -v`
Expected: FAIL — `AttributeError: module 'workbench_engine' has no attribute 'compute_optitrack_quality_signals'` (and similar), plus the `hold_gravity_z_frac`/`hold_stillness_ok` assertions failing with `KeyError` since `compute_raw_sensor_diagnostics` doesn't return those keys yet.

- [ ] **Step 3: Add the import and threshold constants**

In `workbench_engine.py`, after the existing `import pendulastic_pt_score` line (currently line 25), add:

```python
from pendulastic_imu_server import _is_stationary_window, GYRO_BIAS_WINDOW_S, GYRO_BIAS_MIN_SAMPLES
```

After the existing `_ACCEL_RELEASE_BASELINE_SEC = 0.6` line (currently line 643), add:

```python
# Auto-suggestion thresholds (design spec
# docs/superpowers/specs/2026-08-11-trial-quality-triage-design.md Section
# 4) -- SEED VALUES, not validated against the real corpus. Tune these
# empirically (same sweep-and-measure approach used for
# ACCEL_CORRECTION_GYRO_MAX_RAD_S and _RELEASE_ANCHOR_MARGIN_SEC) before
# trusting the suggestions in production.
_HOLD_GRAVITY_Z_FRAC_WARN = 0.85
_OPTITRACK_DROPOUT_FRAC_WARN = 0.30
```

- [ ] **Step 4: Extend `compute_raw_sensor_diagnostics` with the hold-tilt/stillness signals**

Replace the existing `compute_raw_sensor_diagnostics` function (currently lines 679-687):

```python
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

with:

```python
def compute_raw_sensor_diagnostics(anchor_path: str) -> dict:
    """Supplementary, non-blocking cross-checks computed directly from raw
    gyro/accel data (bypassing AHRS fusion entirely) -- see design spec
    Sections 3-4. Never touches load_imu_trial's fused-angle PT-score path.

    hold_gravity_z_frac / hold_stillness_ok (2026-08-11 addition, trial-
    quality-triage design spec Section 4): approximate the pre-release
    calibration hold as the FIRST GYRO_BIAS_WINDOW_S seconds of the raw
    log -- simpler than replay_trial's full calm/pending-departure state
    machine (imu_calibration_tuner.py), which is appropriate here since
    this is a diagnostic signal for a researcher to read, not a correction
    fed back into fusion. Both are None when there are too few raw samples
    in that window to compute a value -- None means "unknown", never a
    silent signal that the hold was fine."""
    paths = _derive_split_csv_siblings(anchor_path)
    accel_samples = _read_one_split_csv(paths["accel"], "accel")
    gyro_samples = _read_one_split_csv(paths["gyro"], "gyro")

    hold_gravity_z_frac = None
    hold_stillness_ok = None
    if accel_samples and gyro_samples:
        t0 = min(accel_samples[0]["t"], gyro_samples[0]["t"])
        hold_accel = [(s["t"], np.asarray(s["v"], float)) for s in accel_samples
                     if s["t"] - t0 < GYRO_BIAS_WINDOW_S]
        hold_gyro = [(s["t"], np.asarray(s["v"], float)) for s in gyro_samples
                    if s["t"] - t0 < GYRO_BIAS_WINDOW_S]
        if len(hold_accel) >= GYRO_BIAS_MIN_SAMPLES:
            mean_accel = np.mean([v for _, v in hold_accel], axis=0)
            mag = float(np.linalg.norm(mean_accel))
            if mag > 1e-9:
                hold_gravity_z_frac = abs(float(mean_accel[2])) / mag
        if hold_accel and hold_gyro:
            now = max(hold_accel[-1][0], hold_gyro[-1][0])
            hold_stillness_ok = _is_stationary_window(hold_gyro, hold_accel, now)

    return {
        "peak_gyro_velocity_dps": _peak_raw_gyro_velocity(anchor_path),
        "accel_release_time_sec": _accel_release_time(anchor_path),
        "hold_gravity_z_frac": hold_gravity_z_frac,
        "hold_stillness_ok": hold_stillness_ok,
    }
```

- [ ] **Step 5: Add `compute_optitrack_quality_signals` and `suggest_quality_tag`**

Immediately after the (now-extended) `compute_raw_sensor_diagnostics` function, add:

```python
def compute_optitrack_quality_signals(ref_t: np.ndarray, ref_angle: np.ndarray) -> dict:
    """OptiTrack-side quality signals computed from ref_t/ref_angle, which
    callers MUST pass as the exact arrays already loaded via
    load_optitrack_trial() and already fed into compare_pair() for
    scoring -- no separate reload, so these can't drift from what the
    scorer sees (design spec Section 4)."""
    ref_t = np.asarray(ref_t, dtype=float)
    ref_angle = np.asarray(ref_angle, dtype=float)
    n = len(ref_angle)
    dropout_frac = (1.0 - float(np.sum(np.isfinite(ref_angle))) / n) if n else 1.0

    pt_params = pendulastic_pt_score.compute_pt_params(ref_t, ref_angle)
    area_ratio_warn = bool(pt_params["quality_warn"]) if pt_params is not None else False

    return {
        "optitrack_dropout_frac": dropout_frac,
        "optitrack_area_ratio_warn": area_ratio_warn,
    }


def suggest_quality_tag(raw_diagnostics: dict, optitrack_signals: dict) -> dict:
    """Pure suggestion rule combining compute_raw_sensor_diagnostics()'s
    hold_* fields with compute_optitrack_quality_signals()'s output.
    Checked in priority order: a tilted or unstable calibration hold wins
    over an OptiTrack-side signal, since it's detected closer to the raw
    source. Never pre-selects a category with confidence it doesn't have --
    returns category=None when no threshold fires, so the caller (the Flag
    Trial Quality dialog) shows an explicit neutral placeholder rather than
    a silently-wrong pre-fill (design spec Section 4).
    mounting_slip and release_contamination have no computable signal at
    all and are never suggested here -- always manual-only."""
    hold_z = raw_diagnostics.get("hold_gravity_z_frac")
    hold_ok = raw_diagnostics.get("hold_stillness_ok")
    if hold_z is not None and hold_z < _HOLD_GRAVITY_Z_FRAC_WARN:
        return {"category": "calibration_hold",
               "details": f"Hold-window gravity only {hold_z * 100:.0f}% on Z axis (tilted hold)."}
    if hold_ok is False:
        return {"category": "calibration_hold",
               "details": "Calibration hold did not pass the stillness gate (handling/motion detected)."}

    dropout = optitrack_signals.get("optitrack_dropout_frac", 0.0)
    if dropout > _OPTITRACK_DROPOUT_FRAC_WARN:
        return {"category": "marker_occlusion",
               "details": f"OptiTrack marker dropout: {dropout * 100:.0f}% of samples missing."}
    if optitrack_signals.get("optitrack_area_ratio_warn"):
        return {"category": "marker_occlusion",
               "details": "OptiTrack area_ratio exceeds the marker-based-angle reliability threshold."}

    return {"category": None, "details": ""}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py -v`
Expected: PASS, all tests including the pre-existing ones in this file (68 previously + 13 new = 81).

- [ ] **Step 7: Run the broader IMU/workbench suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workbench_engine.py tests/test_imu_server.py tests/test_imu_calibration_tuner.py tests/test_batch_imu_vs_optitrack_rmse.py -q`
Expected: PASS (the new `pendulastic_imu_server` import in `workbench_engine.py` must not create a circular import — this run confirms it doesn't).

- [ ] **Step 8: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add trial quality signal computation and suggestion rule"
```

---

## Task 3: Wire exclusions and stratified reporting into `batch_imu_vs_optitrack_rmse.py`

**Files:**
- Modify: `batch_imu_vs_optitrack_rmse.py`
- Modify: `tests/test_batch_imu_vs_optitrack_rmse.py`

**Interfaces:**
- Consumes: `pt_report_common.load_excluded_trials() -> dict`, `pt_report_common.load_quality_tags() -> dict`, `pt_report_common._parse_trial_path(path: str, root: str) -> dict | None`, `pt_report_common.trial_key(participant, leg, condition, trial) -> str` (all from Task 1/pre-existing).
- Produces: `discover_trials()`'s returned dicts gain a `"trial_key": str | None` key; `evaluate_trial()`'s returned row gains a `"trial_key"` key; new `compute_stratified_stats(rows: list, quality_tags: dict, goal_deg: float) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_batch_imu_vs_optitrack_rmse.py` (after the existing imports, near the top-level helper functions):

```python
def test_discover_trials_drops_excluded_trial(tmp_path, monkeypatch):
    """A trial present in excluded_trials.json must not appear in
    discover_trials()'s output at all."""
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_5" / "Left" / "pre"
    imu_dir.mkdir(parents=True)
    opti_dir = opti_root / "Participant_5" / "Left" / "pre"
    opti_dir.mkdir(parents=True)
    for suffix in ("_imu.csv", "_accel.csv", "_gyro.csv", "_mag.csv"):
        (imu_dir / f"Trial_1{suffix}").write_text("x", encoding="utf-8")
    (opti_dir / "trial_1_optitrack.csv").write_text("x", encoding="utf-8")

    monkeypatch.setattr(batch, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(batch, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(pt_report_common, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(pt_report_common, "OPTI_ROOT", str(opti_root))
    key = pt_report_common.trial_key("5", "left", "pre", "1")
    monkeypatch.setattr(pt_report_common, "EXCLUDED_TRIALS_PATH",
                        str(tmp_path / "excluded_trials.json"))
    pt_report_common.add_excluded_trial(key, "test exclusion")

    trials = batch.discover_trials()
    assert trials == []


def test_discover_trials_keeps_non_excluded_trial_and_sets_trial_key(tmp_path, monkeypatch):
    rec_root = tmp_path / "Recordings"
    opti_root = tmp_path / "OptiTrack_Recordings"
    imu_dir = rec_root / "Participant_5" / "Left" / "pre"
    imu_dir.mkdir(parents=True)
    opti_dir = opti_root / "Participant_5" / "Left" / "pre"
    opti_dir.mkdir(parents=True)
    for suffix in ("_imu.csv", "_accel.csv", "_gyro.csv", "_mag.csv"):
        (imu_dir / f"Trial_1{suffix}").write_text("x", encoding="utf-8")
    (opti_dir / "trial_1_optitrack.csv").write_text("x", encoding="utf-8")

    monkeypatch.setattr(batch, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(batch, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(pt_report_common, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(pt_report_common, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(pt_report_common, "EXCLUDED_TRIALS_PATH",
                        str(tmp_path / "excluded_trials.json"))

    trials = batch.discover_trials()
    assert len(trials) == 1
    assert trials[0]["trial_key"] == pt_report_common.trial_key("5", "left", "pre", "1")


def test_compute_stratified_stats_all_trials_no_tags():
    rows = [
        {"status": "ok", "rmse_deg": 5.0, "trial_key": "a"},
        {"status": "ok", "rmse_deg": 15.0, "trial_key": "b"},
        {"status": "error", "rmse_deg": None, "trial_key": "c"},
    ]
    result = batch.compute_stratified_stats(rows, {}, goal_deg=10.0)
    assert result["all"] == {"n": 2, "mean": 10.0, "median": 10.0, "n_under_goal": 1}
    assert list(result.keys()) == ["all"]   # no tag categories present -> no extra breakdowns


def test_compute_stratified_stats_breaks_down_by_tag_category():
    rows = [
        {"status": "ok", "rmse_deg": 5.0, "trial_key": "a"},
        {"status": "ok", "rmse_deg": 25.0, "trial_key": "b"},   # tagged calibration_hold
        {"status": "ok", "rmse_deg": 8.0, "trial_key": "c"},
    ]
    quality_tags = {"b": {"category": "calibration_hold", "details": "x", "timestamp": "t"}}
    result = batch.compute_stratified_stats(rows, quality_tags, goal_deg=10.0)
    assert result["all"] == {"n": 3, "mean": pytest.approx(38.0 / 3), "median": 8.0, "n_under_goal": 2}
    assert result["excluding_calibration_hold"] == {
        "n": 2, "mean": 6.5, "median": 6.5, "n_under_goal": 2}


def test_compute_stratified_stats_returns_none_when_no_ok_rows():
    rows = [{"status": "error", "rmse_deg": None, "trial_key": "a"}]
    result = batch.compute_stratified_stats(rows, {}, goal_deg=10.0)
    assert result["all"] is None
```

Add the required new import at the top of `tests/test_batch_imu_vs_optitrack_rmse.py`, immediately after the existing `import workbench_engine as engine` line (`pytest` is already imported by this file — the `pytest.approx` calls in the test bodies above use that existing import, nothing further needed for it):

```python
import pt_report_common
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_imu_vs_optitrack_rmse.py -k "discover_trials_drops_excluded or discover_trials_keeps_non_excluded or compute_stratified_stats" -v`
Expected: FAIL — `AttributeError: module 'batch_imu_vs_optitrack_rmse' has no attribute 'compute_stratified_stats'`, and the two `discover_trials` tests fail because `trial_key` isn't in the returned dicts / exclusion isn't applied yet.

- [ ] **Step 3: Import `pt_report_common` and wire exclusion + trial_key into `discover_trials()`**

In `batch_imu_vs_optitrack_rmse.py`, after the existing `import workbench_engine as engine` line (currently line 42), add:

```python
import pt_report_common
```

Replace the existing `discover_trials()` function (currently lines 224-241):

```python
def discover_trials() -> list:
    """Glob Recordings/**/Trial_*_imu.csv, derive each trial's 4 component
    sibling paths and matching OptiTrack path. Trials with no OptiTrack
    match are still returned (optitrack_path=None) so main() can count and
    log them as skipped rather than silently dropping them from the
    discovery output."""
    trials = []
    pattern = os.path.join(REC_ROOT, "**", "Trial_*_imu.csv")
    for imu_path in sorted(glob.glob(pattern, recursive=True)):
        ids = _parse_trial_identifiers(imu_path)
        component_paths = derive_component_paths(imu_path)
        optitrack_path = find_optitrack_match(imu_path, REC_ROOT, OPTI_ROOT)
        trials.append({
            **ids,
            **component_paths,
            "optitrack_path": optitrack_path,
        })
    return trials
```

with:

```python
def _trial_key_for(imu_path: str, optitrack_path):
    """Derive the pt_report_common.trial_key() for one discovered trial,
    reusing pt_report_common._parse_trial_path -- the same
    participant/leg/condition/trial parser excluded_trials.json's and
    trial_quality_tags.json's existing entries were authored against.
    Prefers the matched OptiTrack path (the convention this parser is most
    proven against), falling back to the IMU path under REC_ROOT when
    there's no OptiTrack match. Returns None if neither path parses --
    e.g. an archived-data nesting collision (see _parse_trial_path's own
    docstring) -- in which case this trial is simply never exclude-able by
    key (matches current behavior: it was never checked against
    excluded_trials.json at all before this change)."""
    parsed = None
    if optitrack_path:
        parsed = pt_report_common._parse_trial_path(optitrack_path, OPTI_ROOT)
    if parsed is None:
        parsed = pt_report_common._parse_trial_path(imu_path, REC_ROOT)
    if parsed is None:
        return None
    return pt_report_common.trial_key(
        parsed["participant"], parsed["leg"], parsed["condition"], parsed["trial"])


def discover_trials() -> list:
    """Glob Recordings/**/Trial_*_imu.csv, derive each trial's 4 component
    sibling paths and matching OptiTrack path. Trials with no OptiTrack
    match are still returned (optitrack_path=None) so main() can count and
    log them as skipped rather than silently dropping them from the
    discovery output. Trials present in pt_report_common.load_excluded_trials()
    are dropped entirely (2026-08-11 addition) -- this pipeline previously
    did not respect that registry at all, unlike pt_report_common's own
    discover_all_trials()."""
    excluded = pt_report_common.load_excluded_trials()
    trials = []
    pattern = os.path.join(REC_ROOT, "**", "Trial_*_imu.csv")
    for imu_path in sorted(glob.glob(pattern, recursive=True)):
        ids = _parse_trial_identifiers(imu_path)
        component_paths = derive_component_paths(imu_path)
        optitrack_path = find_optitrack_match(imu_path, REC_ROOT, OPTI_ROOT)
        trial_key = _trial_key_for(imu_path, optitrack_path)
        if trial_key is not None and trial_key in excluded:
            continue
        trials.append({
            **ids,
            **component_paths,
            "optitrack_path": optitrack_path,
            "trial_key": trial_key,
        })
    return trials
```

- [ ] **Step 4: Propagate `trial_key` into `evaluate_trial()`'s row and `_FIELDNAMES`**

In `evaluate_trial()`, in the `row = {...}` dict literal (currently starting at line 254), add `"trial_key"` as the second key (after `"participant"`):

```python
    row = {
        "participant": ids["participant"],
        "trial_key": ids.get("trial_key"),
        "position": ids["position"],
        "trial": ids["trial"],
        "imu_path": imu_path,
        "optitrack_path": opti_path,
```

(Leave every other key in that dict exactly as-is — this only inserts one new line.)

Update `_FIELDNAMES` (currently lines 309-311) to include it:

```python
_FIELDNAMES = ["participant", "trial_key", "position", "trial", "imu_path", "optitrack_path",
              "status", "rmse_deg", "mae_deg", "bias_deg", "lag_sec",
              "n_samples", "optitrack_method", "error"]
```

- [ ] **Step 5: Add `compute_stratified_stats` and call it from `main()`**

Immediately before the `def main():` line (currently line 314), add:

```python
def compute_stratified_stats(rows: list, quality_tags: dict, goal_deg: float) -> dict:
    """Given evaluate_trial() rows (each must have 'status', 'rmse_deg',
    'trial_key') and trial_quality_tags.json's loaded dict, compute RMSE
    stats over all successfully-scored rows, plus the same stats
    recomputed with each present tag category's trials excluded. Returns
    {"all": {...} | None, "excluding_<category>": {...} | None, ...} --
    each present value is {"n", "mean", "median", "n_under_goal"}, or None
    if n==0 after filtering. Pure function (no I/O) so it's testable
    independent of a real corpus (design spec Section 6)."""
    ok_rows = [r for r in rows if r["status"] == "ok"]

    def _stats(subset):
        vals = [r["rmse_deg"] for r in subset]
        if not vals:
            return None
        return {
            "n": len(vals),
            "mean": statistics.mean(vals),
            "median": statistics.median(vals),
            "n_under_goal": sum(1 for v in vals if v < goal_deg),
        }

    result = {"all": _stats(ok_rows)}
    categories_present = {
        quality_tags[r["trial_key"]]["category"]
        for r in ok_rows
        if r.get("trial_key") and r["trial_key"] in quality_tags
    }
    for category in sorted(categories_present):
        subset = [
            r for r in ok_rows
            if not (r.get("trial_key") and r["trial_key"] in quality_tags
                   and quality_tags[r["trial_key"]]["category"] == category)
        ]
        result[f"excluding_{category}"] = _stats(subset)
    return result
```

In `main()`, immediately after the existing summary block (the `if ok_rmse:` block that ends with the `print(f"RMSE (deg): mean=...")` line — currently ending around line 355 based on the surrounding structure you already have open), add:

```python
    quality_tags = pt_report_common.load_quality_tags()
    strat = compute_stratified_stats(rows, quality_tags, RMSE_GOAL_DEG)
    if strat["all"] is not None:
        print(f"\nStratified (RMSE < {RMSE_GOAL_DEG} deg goal):")
        s = strat["all"]
        print(f"  all trials           n={s['n']:3d}  mean={s['mean']:6.2f}  "
              f"median={s['median']:6.2f}  {s['n_under_goal']}/{s['n']} under goal")
        for key in sorted(k for k in strat if k != "all"):
            s = strat[key]
            if s is None:
                continue
            print(f"  {key:<20s} n={s['n']:3d}  mean={s['mean']:6.2f}  "
                  f"median={s['median']:6.2f}  {s['n_under_goal']}/{s['n']} under goal")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_imu_vs_optitrack_rmse.py -v`
Expected: PASS, all tests (the pre-existing suite plus the 4 new ones; the one pre-existing failure from the stale `Participant_13_left_post` path in `test_contaminated_trial_no_longer_has_extreme_bias`, if still present from earlier this session's work, is unrelated to this task and should be unaffected either way).

- [ ] **Step 7: Manually verify against the real corpus**

Run: `.venv\Scripts\python.exe batch_imu_vs_optitrack_rmse.py`
Expected: Output includes the existing summary line, followed by a new `Stratified (RMSE < 5.0 deg goal):` block showing `all trials` (since `trial_quality_tags.json` doesn't exist yet on a fresh checkout, there are no `excluding_*` lines yet — that's expected and correct, not a bug, until Task 4's UI is used to tag some real trials).

- [ ] **Step 8: Commit**

```bash
git add batch_imu_vs_optitrack_rmse.py tests/test_batch_imu_vs_optitrack_rmse.py
git commit -m "feat: respect excluded_trials.json and add stratified RMSE reporting"
```

---

## Task 4: Workbench "Flag Trial Quality" UI

**Files:**
- Modify: `pendulastic_app.py`
- Modify: `pendulastic_workbench.py`
- Modify: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: `pt_report_common.QUALITY_TAG_CATEGORIES`, `pt_report_common.trial_key`, `pt_report_common._parse_trial_path`, `pt_report_common.save_quality_tag`, `pt_report_common.clear_quality_tag`, `pt_report_common.add_excluded_trial`, `pt_report_common.clear_excluded_trial`, `pt_report_common.load_quality_tags`, `pt_report_common.load_excluded_trials` (Task 1); `workbench_engine.compute_raw_sensor_diagnostics`, `workbench_engine.compute_optitrack_quality_signals`, `workbench_engine.suggest_quality_tag` (Task 2).
- Produces: `WorkbenchView._current_trial_key() -> str | None` (new method, testable independent of the dialog).

- [ ] **Step 1: Extend raw-diagnostics computation to the split-CSV load path**

In `pendulastic_app.py`, inside `on_load_trial()`'s `if imu_format == "split_csv":` block, immediately after the existing line `self._workbench_imu_reference = imu_reference` (currently line 2898, still inside the `try:` block), add:

```python
                    try:
                        self._workbench_raw_diagnostics = _wb_engine.compute_raw_sensor_diagnostics(
                            components["accel"]["path"])
                    except Exception:
                        pass   # supplementary cross-check only -- never blocks the trial load
```

This mirrors the existing jsonl-format branch's identical pattern at lines 2910-2914 exactly, just anchored on the split-CSV accel path instead of the single jsonl path. Without this, `self._workbench_raw_diagnostics` stays `None` for every split-CSV trial (the common case in the current corpus), and the raw-diagnostics label added in Step 4 below would never show anything for those trials.

- [ ] **Step 2: Write the failing test for trial-key derivation**

Add to `tests/test_pendulastic_workbench.py`, after the existing `class _Ctrl:` definition:

```python
class _CtrlWithMeta:
    def __init__(self, meta):
        self._meta = meta

    def get_trial_meta(self):
        return dict(self._meta)


def test_current_trial_key_derives_from_optitrack_path(tmp_path):
    from pendulastic_workbench import WorkbenchView
    import pt_report_common

    opti_path = tmp_path / "OptiTrack_Recordings" / "Participant_5" / "Left" / "pre" / "trial_1_optitrack.csv"
    opti_path.parent.mkdir(parents=True)
    opti_path.write_text("x", encoding="utf-8")

    r = _get_root()
    wv = WorkbenchView(r, _CtrlWithMeta({"optitrack_path": str(opti_path)}))
    key = wv._current_trial_key(opti_root=str(tmp_path / "OptiTrack_Recordings"),
                                rec_root=str(tmp_path / "Recordings"))
    assert key == pt_report_common.trial_key("5", "left", "pre", "1")


def test_current_trial_key_falls_back_to_imu_paths_when_no_optitrack(tmp_path):
    from pendulastic_workbench import WorkbenchView
    import pt_report_common

    imu_dir = tmp_path / "Recordings" / "Participant_5" / "Left" / "pre"
    imu_dir.mkdir(parents=True)
    accel_path = imu_dir / "Trial_1_accel.csv"
    accel_path.write_text("x", encoding="utf-8")

    r = _get_root()
    wv = WorkbenchView(r, _CtrlWithMeta(
        {"optitrack_path": None, "imu_paths": {"accel": str(accel_path)}}))
    key = wv._current_trial_key(opti_root=str(tmp_path / "OptiTrack_Recordings"),
                                rec_root=str(tmp_path / "Recordings"))
    assert key == pt_report_common.trial_key("5", "left", "pre", "1")


def test_current_trial_key_returns_none_when_nothing_parses():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _CtrlWithMeta({}))
    assert wv._current_trial_key() is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "current_trial_key" -v`
Expected: FAIL — `AttributeError: 'WorkbenchView' object has no attribute '_current_trial_key'`.

- [ ] **Step 4: Add the `pt_report_common` import and `_current_trial_key` method**

In `pendulastic_workbench.py`, after the existing `import pendulastic_pt_score` line (currently line 34), add:

```python
import pt_report_common
```

Immediately after the existing `_meta_ids` method (currently lines 750-752), add:

```python
    def _current_trial_key(self, opti_root=None, rec_root=None) -> Optional[str]:
        """Derive the pt_report_common.trial_key() for the currently-loaded
        trial, for the Flag Trial Quality dialog (Task 4). Prefers the
        OptiTrack path (the convention pt_report_common._parse_trial_path
        is most proven against), falling back to any one of the loaded
        IMU component paths under Recordings/. Returns None if neither is
        available or parses -- the dialog disables saving in that case.
        opti_root/rec_root default to pt_report_common.OPTI_ROOT/REC_ROOT;
        overridable for tests."""
        opti_root = opti_root or pt_report_common.OPTI_ROOT
        rec_root = rec_root or pt_report_common.REC_ROOT
        meta = self.controller.get_trial_meta()

        parsed = None
        optitrack_path = meta.get("optitrack_path")
        if optitrack_path:
            parsed = pt_report_common._parse_trial_path(optitrack_path, opti_root)
        if parsed is None:
            imu_paths = meta.get("imu_paths") or {}
            anchor = imu_paths.get("accel") or meta.get("imu_path")
            if anchor:
                parsed = pt_report_common._parse_trial_path(anchor, rec_root)
        if parsed is None:
            return None
        return pt_report_common.trial_key(
            parsed["participant"], parsed["leg"], parsed["condition"], parsed["trial"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -k "current_trial_key" -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Add the raw-diagnostics label's quality-signal line**

In `pendulastic_workbench.py`, find the `_recompute_metrics` method's raw-diagnostics block (the `if self._raw_diagnostics is not None:` block that sets `self._raw_diag_label`'s text). Replace it:

```python
        if self._raw_diagnostics is not None:
            peak_vel = self._raw_diagnostics["peak_gyro_velocity_dps"]
            release_t = self._raw_diagnostics["accel_release_time_sec"]
            release_str = (f"t={release_t:.2f}s" if release_t is not None
                           else "unavailable (sample rate too low)")
            self._raw_diag_label.configure(
                text=f"Peak angular velocity (raw gyro): {peak_vel:.1f} deg/s   |   "
                     f"Release detected (raw accel, 5Hz low-pass): {release_str}")
        else:
            self._raw_diag_label.configure(
                text="(independent of PT score fusion -- none loaded)")
```

with:

```python
        if self._raw_diagnostics is not None:
            peak_vel = self._raw_diagnostics["peak_gyro_velocity_dps"]
            release_t = self._raw_diagnostics["accel_release_time_sec"]
            release_str = (f"t={release_t:.2f}s" if release_t is not None
                           else "unavailable (sample rate too low)")
            hold_z = self._raw_diagnostics.get("hold_gravity_z_frac")
            hold_ok = self._raw_diagnostics.get("hold_stillness_ok")
            hold_z_str = f"{hold_z * 100:.0f}% on Z" if hold_z is not None else "unavailable"
            hold_ok_str = ("n/a" if hold_ok is None
                           else "passed" if hold_ok else "FAILED (handling detected)")
            self._raw_diag_label.configure(
                text=f"Peak angular velocity (raw gyro): {peak_vel:.1f} deg/s   |   "
                     f"Release detected (raw accel, 5Hz low-pass): {release_str}   |   "
                     f"Hold gravity: {hold_z_str}   |   Hold stillness: {hold_ok_str}")
        else:
            self._raw_diag_label.configure(
                text="(independent of PT score fusion -- none loaded)")
```

- [ ] **Step 7: Run the workbench test suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: PASS, all tests (pre-existing suite plus the 3 new ones from Step 2).

- [ ] **Step 8: Commit the trial-key derivation and raw-diagnostics label changes**

```bash
git add pendulastic_app.py pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: derive trial quality key and surface hold/dropout signals in Workbench"
```

- [ ] **Step 9: Add the "Flag Trial Quality" button and dialog**

In `pendulastic_workbench.py`, in `_build_widgets`, immediately after the existing "Save Trial to Dashboard" button (currently lines 361-362):

```python
        ws.secondary_button(annot_toolbar, "Save Trial to Dashboard",
                            self._on_save_trial_clicked).pack(side="right", padx=6)
```

add:

```python
        ws.secondary_button(annot_toolbar, "Flag Trial Quality",
                            self._on_flag_quality_clicked).pack(side="right", padx=6)
```

Immediately after the existing `_on_save_trial_clicked` method's closing (after its final `tk.Button(button_row, text="Cancel", command=dialog.destroy).pack(side="left", padx=6)` line), add:

```python
    def _on_flag_quality_clicked(self) -> None:
        trial_key = self._current_trial_key()
        if trial_key is None:
            messagebox.showerror(
                "Cannot Flag Trial",
                "Could not determine this trial's identity (participant/leg/"
                "condition/trial number) from its OptiTrack or IMU paths -- "
                "cannot save a quality tag or exclusion for it.")
            return

        raw_diag = self._raw_diagnostics or {}
        opti_trace = self._traces.get("optitrack")
        opti_signals = {"optitrack_dropout_frac": 0.0, "optitrack_area_ratio_warn": False}
        if opti_trace is not None:
            ref_t, ref_angle = opti_trace
            try:
                opti_signals = engine.compute_optitrack_quality_signals(ref_t, ref_angle)
            except Exception:
                pass   # supplementary signal only -- dialog still opens without it
        suggestion = engine.suggest_quality_tag(raw_diag, opti_signals)

        existing_tags = pt_report_common.load_quality_tags()
        existing_tag = existing_tags.get(trial_key)
        existing_excluded = trial_key in pt_report_common.load_excluded_trials()

        dialog = tk.Toplevel(self)
        dialog.title("Flag Trial Quality")
        dialog.transient(self)

        hold_z = raw_diag.get("hold_gravity_z_frac")
        hold_ok = raw_diag.get("hold_stillness_ok")
        signals_text = (
            f"Hold gravity on Z: {f'{hold_z * 100:.0f}%' if hold_z is not None else 'unavailable'}\n"
            f"Hold stillness gate: {'n/a' if hold_ok is None else ('passed' if hold_ok else 'FAILED')}\n"
            f"OptiTrack dropout: {opti_signals['optitrack_dropout_frac'] * 100:.0f}%\n"
            f"OptiTrack area-ratio warning: {'yes' if opti_signals['optitrack_area_ratio_warn'] else 'no'}"
        )
        tk.Label(dialog, text=signals_text, justify="left", anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))

        tk.Label(dialog, text="Category:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        _NEUTRAL = "No automated suggestion -- select category..."
        category_options = [_NEUTRAL] + list(pt_report_common.QUALITY_TAG_CATEGORIES)
        default_category = existing_tag["category"] if existing_tag else (
            suggestion["category"] or _NEUTRAL)
        category_var = tk.StringVar(value=default_category)
        ttk.OptionMenu(dialog, category_var, default_category, *category_options).grid(
            row=1, column=1, sticky="w", padx=8, pady=4)

        tk.Label(dialog, text="Details:").grid(row=2, column=0, sticky="nw", padx=8, pady=4)
        details_text = tk.Text(dialog, height=3, width=40, wrap="word")
        details_text.insert(
            "1.0", existing_tag["details"] if existing_tag else suggestion["details"])
        details_text.grid(row=2, column=1, padx=8, pady=4)

        exclude_var = tk.BooleanVar(value=existing_excluded)
        tk.Checkbutton(dialog, text="Also exclude from all analysis",
                      variable=exclude_var).grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8, pady=4)

        status_var = tk.StringVar(value="")
        tk.Label(dialog, textvariable=status_var, fg="#B45309").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 4))

        def on_save() -> None:
            category = category_var.get()
            if category == _NEUTRAL:
                status_var.set("Select a category before saving.")
                return
            details = details_text.get("1.0", "end").strip()
            try:
                pt_report_common.save_quality_tag(trial_key, category, details)
            except ValueError as e:
                status_var.set(str(e))
                return
            if exclude_var.get():
                pt_report_common.add_excluded_trial(trial_key, f"{category}: {details}")
            else:
                pt_report_common.clear_excluded_trial(trial_key)
            dialog.destroy()
            messagebox.showinfo("Flagged", f"Saved quality tag for {trial_key}.")

        def on_clear() -> None:
            pt_report_common.clear_quality_tag(trial_key)
            pt_report_common.clear_excluded_trial(trial_key)
            dialog.destroy()
            messagebox.showinfo("Cleared", f"Cleared quality tag/exclusion for {trial_key}.")

        button_row = tk.Frame(dialog)
        button_row.grid(row=5, column=0, columnspan=2, pady=8)
        tk.Button(button_row, text="Save", command=on_save).pack(side="left", padx=6)
        tk.Button(button_row, text="Clear", command=on_clear).pack(side="left", padx=6)
        tk.Button(button_row, text="Cancel", command=dialog.destroy).pack(side="left", padx=6)
```

- [ ] **Step 10: Run the full workbench test suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py -v`
Expected: PASS, all tests (this step adds no new automated tests for the dialog itself, per this plan's Global Constraints — verified manually in Step 11).

- [ ] **Step 11: Manual smoke test**

Launch the app (`.venv\Scripts\python.exe pendulastic_app.py`), load any real trial with both IMU and OptiTrack data into the Workbench, and confirm:
1. The raw-diagnostics label now shows "Hold gravity" and "Hold stillness" alongside the existing peak-velocity/release-time text.
2. Clicking "Flag Trial Quality" opens a dialog showing the four computed signals, a category dropdown (pre-filled with a suggestion if the loaded trial's signals cross a threshold, otherwise the neutral placeholder), and an editable details field.
3. Selecting a category, optionally checking "Also exclude from all analysis," and clicking Save closes the dialog with a confirmation, and re-opening the dialog for the same trial shows the saved category/details/checkbox state.
4. Clicking "Clear" removes both the tag and any exclusion, confirmed by re-opening the dialog and seeing the neutral placeholder and unchecked box again.
5. Run `.venv\Scripts\python.exe batch_imu_vs_optitrack_rmse.py` again and confirm the stratified block now shows an `excluding_<category>` line for whichever category was saved in step 3.

- [ ] **Step 12: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: add Flag Trial Quality dialog to the Workbench"
```

---

## Self-Review Notes

- **Spec coverage:** Section 3 (data model) → Task 1. Section 4 (signal computation) → Task 2. Section 5 (Workbench UI) → Task 4. Section 6 (stratified reporting) → Task 3. Section 7 (testing) → each task's test steps; dialog exemption honored explicitly in Task 4 and the Global Constraints. Section 8 (out of scope) → no task attempts threshold tuning, file locking, or a separate triage screen.
- **Placeholder scan:** no TBD/TODO; every step has real, complete code.
- **Type consistency:** `trial_key` is a `str | None` everywhere it's threaded (discover_trials → evaluate_trial → compute_stratified_stats → Workbench `_current_trial_key`); `QUALITY_TAG_CATEGORIES` is the same tuple referenced in Task 1 (definition), Task 2 (not directly used, decoupled by design), and Task 4 (dialog dropdown) — no renaming drift between tasks.
