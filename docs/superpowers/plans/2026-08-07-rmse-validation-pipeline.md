# Auto-Triggered RMSE Validation Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Pendulastic a shared sweep/scoring module that always reports current IMU-vs-OptiTrack and MediaPipe-vs-OptiTrack RMSE across the existing hand-tuned parameter grids, plus a long-running watcher that re-runs it automatically as new trial data lands — never a stale, hand-run, informally-compared number again.

**Architecture:** Two independent layers, built in that order. **Part 1** (Tasks 1-8) is `rmse_pipeline_common.py`, a pure library module — discovery, IMU/MediaPipe scoring wrappers, a sweep cache, best-config tracking, and report generation — plus `run_rmse_sweep.py`, a thin manual-trigger CLI, matching this repo's existing `pt_report_common.py` → `run_pt_analysis.py` split. Part 1 is a complete, independently useful deliverable: run it by hand after any recording session and get current numbers. **Part 2** (Tasks 9-15) is `rmse_watcher.py`, a long-running service that debounces filesystem events, waits for file stability, and calls into Part 1's `run_full_sweep()` automatically, plus its Windows Scheduled Task deployment. Part 2 depends entirely on Part 1 and adds no new scoring logic of its own.

**Tech Stack:** Python 3, `numpy`/`scipy`/`pandas` (already in requirements.txt), `watchdog` (new dependency, Part 2 only), stdlib `json`/`csv`/`queue`/`logging.handlers`/`os.replace` (atomic writes). `matplotlib` (Agg backend) for report figures, reusing `pendulastic_pt_score`'s existing dark-theme color constants (`_BG="#12172a"`, `_PANEL="#1c2340"`, `_HDR="#252e50"`) for visual consistency with `model_vs_optitrack_eval.py`'s `rmse_heatmap.png`.

## Global Constraints

- Never modify `batch_imu_vs_optitrack_rmse.py`, `sweep_imu_config.py`, `sweep_mediapipe_config.py`, `model_vs_optitrack_eval.py`, `imu_calibration_tuner.py`, or `workbench_engine.py` — wrap and reuse their existing, already-tested logic only. This mirrors the existing `pt_cohort_common.py`-built-on-`pt_report_common.py` pattern in this repo (built on top, original left runnable standalone).
- Never write to `imu_calibration_config.json` or any live config file. `rmse_best_config.json` is a separate, report-only tracking file; a human decides whether to hand-apply a change to the live config. This pipeline never auto-promotes into production config.
- `sweep_imu_config.WIDE_GRID` and any MediaPipe grid must always be imported live from their source modules, never copied/hardcoded — both are actively hand-tuned (confirmed: `sweep_imu_config.py` currently has an uncommitted local diff trimming the grid from 576 to 288 combos).
- **The 2026-08-04 folder-restructure trap:** `evaluate_all_participants.py`'s `DataIndex` and its own docstring are hardcoded to the OLD `Participant_{id}/Position_{pos}/Height*-Level/` layout — confirmed by direct inspection, same class of bug already found and worked around in `pendulastic_pt_score.discover_optitrack()` for the `run_pt_analysis.py` work earlier today. **Do not build trial discovery on `evaluate_all_participants.py`.** `batch_imu_vs_optitrack_rmse.discover_trials()`'s core path-matching (`find_optitrack_match`) is generic (walks the mirrored relative-path tree, not `Position_`-anchored) and works correctly on new-structure participants (P14, P15) — but its `_parse_trial_identifiers()` falls back to `"unknown"` for `position` on new-structure paths, so Task 1 must re-derive `leg`/`condition` itself (same regex approach as `pt_report_common._parse_trial_path()`) rather than trust that field.
- Every new module follows this repo's existing defensive-JSON pattern (`imu_calibration_config.load_config()` / `pt_cohort_common.load_registry()`): missing or malformed file → treated as empty/default, never raises.
- **Shared exclusion registry:** `excluded_trials.json` (repo root, added 2026-08-07 alongside `pt_report_common.load_excluded_trials()`/`trial_key()`) lists non-viable trials — e.g. a trial where the participant actively used their own muscles to stop the pendulum swing instead of a passive release — keyed by the same `f"{participant}_{leg}_{condition}_T{trial}"` format this plan's `discover_scorable_trials()` uses. `pt_report_common.discover_all_trials()` already filters against it. Task 1 below must filter against the identical registry (via `pt_report_common.load_excluded_trials()`, not a duplicate loader) so the sweep's parameter search is never fit against non-passive, physically-invalid motion.
- Tests: plain functions, no test classes, `tmp_path`/`monkeypatch` fixtures, `import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))` header — matching `tests/test_pt_cohort_common.py`'s exact convention. Run via `.venv\Scripts\pytest tests\<file>.py -v`.
- A single trial's or candidate's scoring failure must never abort a sweep — catch, log, continue (matching `run_pt_analysis.py`'s existing try/except-per-cohort-comparison pattern).

---

## Part 1: Core Sweep Pipeline (manually triggered)

### Task 1: `rmse_pipeline_common.py` — trial discovery

**Files:**
- Create: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py`

**Interfaces:**
- Consumes: `pt_report_common.load_excluded_trials() -> dict` and `pt_report_common.trial_key(...)` — already implemented and live in the repo (added 2026-08-07 alongside `excluded_trials.json`, ahead of this plan), not something this task creates.
- Produces: `discover_scorable_trials() -> list[dict]`, each dict:
  `{"key": str, "participant": str, "leg": str, "condition": str, "trial": str, "optitrack_path": str, "imu_paths": Optional[dict], "video_path": Optional[str], "cache_stat_key": str}`
  where `imu_paths` is `{"imu":, "accel":, "gyro":, "mag":}` or `None` if no split IMU CSVs exist for this trial, `video_path` is the `Trial_{n}.avi` path or `None`, and `cache_stat_key` is `f"{size}:{mtime}"` built from the OptiTrack CSV's `os.stat()` (the file that changes least often of the three, and whose presence gates inclusion at all). Trials in `excluded_trials.json` are never included.
  Tasks 2, 3, 4, 5 all consume this exact shape.

- [ ] **Step 1: Write the failing test for leg/condition parsing on new-structure paths**

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import rmse_pipeline_common as rpc


def test_derive_leg_condition_new_structure():
    # Recordings/Participant_15/Right/pre/Trial_3_imu.csv -- no Position_ level
    rel_parts = ["Participant_15", "Right", "pre", "Trial_3_imu.csv"]
    leg, cond = rpc._derive_leg_condition(rel_parts)
    assert leg == "right"
    assert cond == "pre"


def test_derive_leg_condition_old_structure_position_level():
    # Participant_13_right_post/Session_post/Position_1/Height_Joint-Level/Trial_3_imu.csv
    rel_parts = ["Participant_13_right_post", "Session_post", "Position_1",
                 "Height_Joint-Level", "Trial_3_imu.csv"]
    leg, cond = rpc._derive_leg_condition(rel_parts)
    assert leg == "right"
    assert cond == "post"


def test_derive_leg_condition_no_leg_token_returns_unknown():
    rel_parts = ["Participant_9", "default", "Trial_1_imu.csv"]
    leg, cond = rpc._derive_leg_condition(rel_parts)
    assert leg == "unknown"


def test_discover_scorable_trials_skips_excluded_key(monkeypatch):
    import pt_report_common as prc
    monkeypatch.setattr(prc, "load_excluded_trials",
                        lambda: {"15_right_pre_T2": "muscle intervention"})
    monkeypatch.setattr(rpc.imu_discovery, "discover_trials", lambda: [
        {"participant": "15", "trial": "2",
         "imu": r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_2_imu.csv",
         "accel": "a.csv", "gyro": "g.csv", "mag": "m.csv",
         "optitrack_path": r"C:\Users\cladi\Pendulastic\OptiTrack_Recordings\Participant_15\Right\pre\trial_2_optitrack.csv"},
    ])
    assert rpc.discover_scorable_trials() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rmse_pipeline_common'`

- [ ] **Step 3: Write `_derive_leg_condition` and `discover_scorable_trials`**

```python
"""
rmse_pipeline_common.py
========================
Shared discovery/scoring/sweep-orchestration module for the RMSE validation
pipeline. Wraps (never duplicates) batch_imu_vs_optitrack_rmse.py,
sweep_imu_config.py, sweep_mediapipe_config.py's already-tested logic, the
same wrap-don't-rewrite pattern pt_cohort_common.py uses on top of
pt_report_common.py.

Trial discovery deliberately does NOT reuse evaluate_all_participants.py --
its DataIndex is hardcoded to the pre-2026-08-04 Position_/Height_ folder
layout and would silently miss every trial recorded since (see
docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md's
Global Constraints note). Instead this derives leg/condition the same
regex-based way pt_report_common._parse_trial_path() already does.

See docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import batch_imu_vs_optitrack_rmse as imu_discovery
import pt_report_common as prc

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")

_LEG_RE = re.compile(r"(?:^|_)(left|right)(?:_|$)", re.I)


def _derive_leg_condition(rel_parts: list) -> tuple:
    """Given the path parts between Recordings/ and the trial filename
    (inclusive of the filename), return (leg, condition). leg is 'unknown'
    if no left/right token is found anywhere in the path. condition is the
    remaining folder segments with the leg token and structural
    Position_/Height_/Session_ prefixes stripped -- 'unknown' if nothing is
    left. Mirrors pt_report_common._parse_trial_path()'s heuristic so RMSE
    trial keys read consistently with the PT-score side of the codebase."""
    leg = "unknown"
    cond_parts = []
    for part in rel_parts[:-1]:
        m = _LEG_RE.search(part)
        if m:
            leg = m.group(1).lower()
        low = part.lower()
        if low.startswith("position_") or low.startswith("height_"):
            continue
        cleaned = part
        if low.startswith("session_"):
            cleaned = part[len("session_"):]
        elif low.startswith("participant_"):
            cleaned = re.sub(r"^participant_\d+_?", "", part, flags=re.I)
        cleaned = re.sub(r"(left|right)", "", cleaned, flags=re.I).strip("_")
        if cleaned:
            cond_parts.append(cleaned)
    condition = "_".join(dict.fromkeys(cond_parts)) or "unknown"
    return leg, condition


def discover_scorable_trials() -> list:
    """Every trial with an OptiTrack ground-truth counterpart, regardless of
    which source modality (IMU split-CSVs, video) is actually present --
    score_imu_candidate/score_mediapipe_candidate independently no-op per
    trial when their needed modality is missing, matching
    sweep_imu_config.score_config's per-config-not-per-sweep skip pattern.

    Trials listed in excluded_trials.json (non-viable recordings, e.g.
    active muscle intervention during the swing) are dropped here, via the
    same pt_report_common.load_excluded_trials() registry the PT-score
    reporting side uses -- one registry, not a duplicate."""
    excluded = prc.load_excluded_trials()
    out = []
    seen_keys = set()
    for t_info in imu_discovery.discover_trials():
        opti_path = t_info.get("optitrack_path")
        if not opti_path:
            continue
        imu_path = t_info["imu"]
        rel = os.path.relpath(imu_path, REC_ROOT).replace("\\", "/")
        rel_parts = rel.split("/")
        participant = t_info["participant"]
        leg, condition = _derive_leg_condition(rel_parts)
        trial = t_info["trial"]
        key = f"{participant}_{leg}_{condition}_T{trial}"
        if key in seen_keys or key in excluded:
            continue
        seen_keys.add(key)

        imu_paths = None
        if all(os.path.isfile(t_info[c]) for c in ("accel", "gyro", "mag")):
            imu_paths = {"imu": t_info["imu"], "accel": t_info["accel"],
                        "gyro": t_info["gyro"], "mag": t_info["mag"]}

        video_path = os.path.join(os.path.dirname(imu_path), f"Trial_{trial}.avi")
        if not os.path.isfile(video_path):
            video_path = None

        st = os.stat(opti_path)
        out.append({
            "key": key, "participant": participant, "leg": leg,
            "condition": condition, "trial": trial,
            "optitrack_path": opti_path, "imu_paths": imu_paths,
            "video_path": video_path,
            "cache_stat_key": f"{st.st_size}:{st.st_mtime_ns}",
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add RMSE pipeline trial discovery with new-folder-structure leg/condition parsing and excluded-trial filtering"
```

---

### Task 2: `rmse_pipeline_common.py` — `score_imu_candidate`

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py`

**Interfaces:**
- Consumes: `discover_scorable_trials()`'s trial dict (Task 1); `imu_calibration_tuner.replay_trial(raw_samples, params) -> (t, angle_deg)`; `reconstruct_imu_raw_logs.reconstruct_trial(accel_csv, gyro_csv, mag_csv) -> list[sample]`; `workbench_engine.compare_pair(ref_t, ref_y, test_t, test_y) -> dict`; `pendulastic_pt_score.load_optitrack(path) -> (t, angle)`.
- Produces: `score_imu_candidate(trial: dict, params: dict) -> Optional[float]` — RMSE in degrees, or `None` if this trial has no IMU components or the replay/comparison didn't produce a scoreable result. Tasks 5 and 6 call this directly.

- [ ] **Step 1: Write the failing test (monkeypatched dependencies, no real IMU replay)**

```python
def test_score_imu_candidate_returns_none_without_imu_paths():
    trial = {"key": "15_right_pre_T1", "imu_paths": None,
             "optitrack_path": "unused.csv"}
    assert rpc.score_imu_candidate(trial, {"beta": 0.041}) is None


def test_score_imu_candidate_returns_rmse(monkeypatch, tmp_path):
    import numpy as np

    opti_csv = tmp_path / "trial_1_optitrack.csv"
    opti_csv.write_text("dummy")

    trial = {
        "key": "15_right_pre_T1",
        "optitrack_path": str(opti_csv),
        "imu_paths": {"imu": "imu.csv", "accel": "a.csv",
                      "gyro": "g.csv", "mag": "m.csv"},
    }

    monkeypatch.setattr(rpc, "load_optitrack",
                        lambda p: (np.array([0.0, 1.0, 2.0]), np.array([180.0, 150.0, 170.0])))
    monkeypatch.setattr(rpc, "reconstruct_trial",
                        lambda a, g, m: [{"t": 0.0, "role": "accel"}])
    monkeypatch.setattr(rpc, "replay_trial",
                        lambda samples, params: (np.array([0.0, 1.0, 2.0]), np.array([180.0, 148.0, 172.0])))
    monkeypatch.setattr(rpc.engine, "compare_pair",
                        lambda *a, **k: {"status": "ok", "rmse_deg": 3.5})

    rmse = rpc.score_imu_candidate(trial, {"beta": 0.041, "ema_alpha": 0.3,
                                           "flex_axis_capture": True,
                                           "gravity_seed": True, "method": "relative"})
    assert rmse == 3.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'score_imu_candidate'`

- [ ] **Step 3: Implement `score_imu_candidate`**

Add to `rmse_pipeline_common.py`:

```python
import workbench_engine as engine
from imu_calibration_tuner import replay_trial
from pendulastic_pt_score import load_optitrack
from reconstruct_imu_raw_logs import reconstruct_trial


def score_imu_candidate(trial: dict, params: dict) -> Optional[float]:
    """RMSE (deg) of one IMU AHRS/fusion config replayed against this
    trial's OptiTrack ground truth, or None if unscoreable (no IMU
    components, replay produced no motion, or comparison failed) -- never
    raises, matching sweep_imu_config.score_config's per-trial tolerance."""
    imu_paths = trial.get("imu_paths")
    if not imu_paths:
        return None
    try:
        opti_t, opti_ang = load_optitrack(trial["optitrack_path"])
        samples = reconstruct_trial(imu_paths["accel"], imu_paths["gyro"], imu_paths["mag"])
        t_m, ang_m = replay_trial(samples, params)
    except Exception:
        return None
    import numpy as np
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    return result["rmse_deg"] if result.get("status") == "ok" else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add IMU candidate scoring wrapper to RMSE pipeline"
```

---

### Task 3: `rmse_pipeline_common.py` — `score_mediapipe_candidate`

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py`

**Interfaces:**
- Consumes: trial dict (Task 1); `batch_mediapipe.MP_LEG_IDX`, `batch_mediapipe._select_patient_pose`; `workbench_engine.compare_pair`.
- Produces: `score_mediapipe_candidate(trial: dict, model_variant: str, vis_threshold: float) -> Optional[float]`. Internally memoizes the expensive per-(video, model_variant) landmark extraction in a module-level dict, so sweeping several `vis_threshold` values over the same `(trial, model_variant)` costs one MediaPipe inference pass, not one per threshold — same optimization `sweep_mediapipe_config.py` already uses, preserved rather than lost by generalizing. Task 5 calls this directly.

- [ ] **Step 1: Write the failing test**

```python
def test_score_mediapipe_candidate_returns_none_without_video():
    trial = {"key": "15_right_pre_T1", "video_path": None, "leg": "right"}
    assert rpc.score_mediapipe_candidate(trial, "full", 0.4) is None


def test_score_mediapipe_candidate_reuses_cached_extraction(monkeypatch, tmp_path):
    import numpy as np

    video = tmp_path / "Trial_1.avi"
    video.write_text("dummy")
    opti = tmp_path / "trial_1_optitrack.csv"
    opti.write_text("dummy")

    trial = {"key": "15_right_pre_T1", "video_path": str(video),
             "leg": "right", "optitrack_path": str(opti)}

    calls = {"extract": 0}

    def fake_extract(video_path, leg, model_path):
        calls["extract"] += 1
        return [{"t": 0.0, "hip": (0, 0), "knee": (0, 1), "ankle": (0, 2),
                 "hip_v": 0.9, "knee_v": 0.9, "ankle_v": 0.9}]

    monkeypatch.setattr(rpc, "_extract_raw_landmarks", fake_extract)
    monkeypatch.setattr(rpc, "load_optitrack",
                        lambda p: (np.array([0.0]), np.array([180.0])))
    monkeypatch.setattr(rpc.engine, "compare_pair",
                        lambda *a, **k: {"status": "ok", "rmse_deg": 7.2})
    monkeypatch.setattr(rpc.os.path, "isfile", lambda p: True)

    rpc._RAW_LANDMARKS_CACHE.clear()
    r1 = rpc.score_mediapipe_candidate(trial, "full", 0.30)
    r2 = rpc.score_mediapipe_candidate(trial, "full", 0.40)
    assert r1 == 7.2 and r2 == 7.2
    assert calls["extract"] == 1   # second call reused the cached extraction
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'score_mediapipe_candidate'`

- [ ] **Step 3: Implement, reusing `sweep_mediapipe_config`'s extraction/angle logic**

Add to `rmse_pipeline_common.py`:

```python
import numpy as np

import batch_mediapipe as bm
import sweep_mediapipe_config as mp_sweep

MODELS_DIR = os.path.join(BASE_DIR, "models", "mediapipe")
_RAW_LANDMARKS_CACHE: dict = {}


def _extract_raw_landmarks(video_path, leg, model_path):
    """Thin pass-through to sweep_mediapipe_config's already-implemented
    extraction, kept as a separate name so tests can monkeypatch it without
    touching the real MediaPipe runtime."""
    return mp_sweep.extract_raw_landmarks(video_path, leg, model_path)


def score_mediapipe_candidate(trial: dict, model_variant: str, vis_threshold: float) -> Optional[float]:
    """RMSE (deg) of one MediaPipe model+threshold config against this
    trial's OptiTrack ground truth, or None if unscoreable. Landmark
    extraction (the expensive step) is cached per (video_path,
    model_variant) so sweeping multiple vis_threshold values over the same
    model is one inference pass, matching sweep_mediapipe_config.py's own
    raw_by_trial caching."""
    video_path = trial.get("video_path")
    if not video_path:
        return None
    model_path = os.path.join(MODELS_DIR, f"pose_landmarker_{model_variant}.task")
    if not os.path.isfile(model_path):
        return None

    cache_key = (video_path, model_variant)
    if cache_key not in _RAW_LANDMARKS_CACHE:
        try:
            _RAW_LANDMARKS_CACHE[cache_key] = _extract_raw_landmarks(
                video_path, trial["leg"], model_path)
        except Exception:
            _RAW_LANDMARKS_CACHE[cache_key] = []
    frames = _RAW_LANDMARKS_CACHE[cache_key]
    if not frames:
        return None

    try:
        opti_t, opti_ang = load_optitrack(trial["optitrack_path"])
        t_m, ang_m = mp_sweep.angles_from_raw(frames, vis_threshold)
    except Exception:
        return None
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    return result["rmse_deg"] if result.get("status") == "ok" else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add MediaPipe candidate scoring wrapper with cached landmark extraction"
```

---

### Task 4: `rmse_pipeline_common.py` — sweep cache

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py`

**Interfaces:**
- Produces: `_config_key(params: dict) -> str`, `_cache_path(methodology: str) -> str`, `_load_cache(methodology: str) -> dict`, `_save_cache(methodology: str, cache: dict) -> None`, `cached_or_score(methodology: str, trial: dict, config_label: str, params: dict, score_fn) -> Optional[float]`. Task 5 calls `cached_or_score`.

- [ ] **Step 1: Write the failing test**

```python
def test_cached_or_score_writes_and_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path))
    trial = {"key": "15_right_pre_T1", "cache_stat_key": "100:12345"}
    calls = {"n": 0}

    def scorer():
        calls["n"] += 1
        return 4.2

    r1 = rpc.cached_or_score("imu", trial, "beta=0.041", {"beta": 0.041}, scorer)
    r2 = rpc.cached_or_score("imu", trial, "beta=0.041", {"beta": 0.041}, scorer)
    assert r1 == 4.2 and r2 == 4.2
    assert calls["n"] == 1   # second call served from cache, scorer not re-invoked


def test_cached_or_score_different_config_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path))
    trial = {"key": "15_right_pre_T1", "cache_stat_key": "100:12345"}
    calls = {"n": 0}

    def scorer():
        calls["n"] += 1
        return 4.2

    rpc.cached_or_score("imu", trial, "beta=0.041", {"beta": 0.041}, scorer)
    rpc.cached_or_score("imu", trial, "beta=0.02", {"beta": 0.02}, scorer)
    assert calls["n"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'cached_or_score'`

- [ ] **Step 3: Implement the cache**

Add to `rmse_pipeline_common.py`:

```python
import json

SWEEP_CACHE_DIR = os.path.join(BASE_DIR, "sweep_cache")


def _config_key(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


def _cache_path(methodology: str) -> str:
    return os.path.join(SWEEP_CACHE_DIR, f"{methodology}_cache.json")


def _load_cache(methodology: str) -> dict:
    """Missing or malformed cache file -> empty dict, never raises --
    matching imu_calibration_config.load_config()'s defensive pattern."""
    path = _cache_path(methodology)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(methodology: str, cache: dict) -> None:
    os.makedirs(SWEEP_CACHE_DIR, exist_ok=True)
    path = _cache_path(methodology)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp_path, path)


def cached_or_score(methodology: str, trial: dict, config_label: str,
                    params: dict, score_fn) -> Optional[float]:
    """Look up (trial key, file stat, config) in the on-disk cache; on miss,
    call score_fn() (a zero-arg thunk so the caller controls what actually
    gets scored) and persist the result -- including None, so a
    known-unscoreable (trial, config) pair isn't retried every sweep."""
    cache = _load_cache(methodology)
    key = f"{trial['key']}|{trial['cache_stat_key']}|{config_label}|{_config_key(params)}"
    if key in cache:
        return cache[key]
    result = score_fn()
    cache[key] = result
    _save_cache(methodology, cache)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: 10 passed

- [ ] **Step 5: Add `sweep_cache/` to `.gitignore`**

Append to `.gitignore`:
```
sweep_cache/
```

- [ ] **Step 6: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py .gitignore
git commit -m "feat: add per-(trial, config) sweep cache to RMSE pipeline"
```

---

### Task 5: `rmse_pipeline_common.py` — `run_full_sweep`

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py`

**Interfaces:**
- Consumes: `discover_scorable_trials()` (Task 1), `score_imu_candidate`/`score_mediapipe_candidate` (Tasks 2-3), `cached_or_score` (Task 4), `sweep_imu_config.WIDE_GRID` (imported live), `sweep_mediapipe_config.MODEL_VARIANTS`/`VIS_THRESH_CANDIDATES` (imported live).
- Produces: `run_full_sweep() -> dict` shaped `{"mediapipe": [ranked candidate dicts], "imu": [ranked candidate dicts]}`, each candidate dict `{"config": dict, "config_label": str, "median_rmse_deg": float, "mean_rmse_deg": float, "n_scored": int, "n_total": int}`, sorted ascending by `median_rmse_deg`. Tasks 6 and 7 consume this.

- [ ] **Step 1: Write the failing test**

```python
def test_run_full_sweep_ranks_candidates_by_median_rmse(monkeypatch):
    trial = {"key": "15_right_pre_T1", "cache_stat_key": "1:1", "leg": "right",
             "imu_paths": {"a": 1}, "video_path": "v.avi", "optitrack_path": "o.csv"}
    monkeypatch.setattr(rpc, "discover_scorable_trials", lambda: [trial])
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", "unused")
    monkeypatch.setattr(rpc, "_load_cache", lambda m: {})
    monkeypatch.setattr(rpc, "_save_cache", lambda m, c: None)

    import sweep_imu_config as sic
    monkeypatch.setattr(sic, "WIDE_GRID", [{"beta": 0.01}, {"beta": 0.02}])

    import sweep_mediapipe_config as smc
    monkeypatch.setattr(smc, "MODEL_VARIANTS", ["full"])
    monkeypatch.setattr(smc, "VIS_THRESH_CANDIDATES", [0.4])

    def fake_imu_score(t, params):
        return 3.0 if params["beta"] == 0.01 else 9.0

    def fake_mp_score(t, model, vis):
        return 6.0

    monkeypatch.setattr(rpc, "score_imu_candidate", fake_imu_score)
    monkeypatch.setattr(rpc, "score_mediapipe_candidate", fake_mp_score)

    result = rpc.run_full_sweep()
    assert result["imu"][0]["config"] == {"beta": 0.01}
    assert result["imu"][0]["median_rmse_deg"] == 3.0
    assert result["mediapipe"][0]["median_rmse_deg"] == 6.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'run_full_sweep'`

- [ ] **Step 3: Implement `run_full_sweep`**

Add to `rmse_pipeline_common.py`:

```python
import sweep_imu_config
import sweep_mediapipe_config


def _rank(rows: list) -> list:
    return sorted(rows, key=lambda r: r["median_rmse_deg"])


def run_full_sweep() -> dict:
    """Discover every scorable trial, sweep both grids over all of them
    (via the cache so unchanged (trial, config) pairs aren't recomputed),
    and return each methodology's candidates ranked best-first."""
    trials = discover_scorable_trials()
    n_total = len(trials)

    imu_rows = []
    for params in sweep_imu_config.WIDE_GRID:
        label = _config_key(params)
        rmses = []
        for trial in trials:
            r = cached_or_score("imu", trial, label, params,
                                lambda t=trial, p=params: score_imu_candidate(t, p))
            if r is not None:
                rmses.append(r)
        if rmses:
            imu_rows.append({
                "config": params, "config_label": label,
                "median_rmse_deg": float(np.median(rmses)),
                "mean_rmse_deg": float(np.mean(rmses)),
                "n_scored": len(rmses), "n_total": n_total,
            })

    mp_rows = []
    for variant in sweep_mediapipe_config.MODEL_VARIANTS:
        for vis_thresh in sweep_mediapipe_config.VIS_THRESH_CANDIDATES:
            config = {"model": variant, "vis_thresh": vis_thresh}
            label = _config_key(config)
            rmses = []
            for trial in trials:
                r = cached_or_score("mediapipe", trial, label, config,
                                    lambda t=trial, v=variant, vt=vis_thresh:
                                        score_mediapipe_candidate(t, v, vt))
                if r is not None:
                    rmses.append(r)
            if rmses:
                mp_rows.append({
                    "config": config, "config_label": label,
                    "median_rmse_deg": float(np.median(rmses)),
                    "mean_rmse_deg": float(np.mean(rmses)),
                    "n_scored": len(rmses), "n_total": n_total,
                })

    return {"imu": _rank(imu_rows), "mediapipe": _rank(mp_rows)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add run_full_sweep aggregating IMU and MediaPipe grids over every scorable trial"
```

---

### Task 6: `rmse_pipeline_common.py` — best-config tracking

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py`

**Interfaces:**
- Consumes: `run_full_sweep()`'s ranked output (Task 5).
- Produces: `load_best_config() -> dict`, `record_sweep_result(sweep_result: dict) -> dict` (returns the updated best-config dict, and appends to `"history"` only when a methodology's best candidate improves on the recorded best by more than `PROMOTION_EPSILON_DEG`). Task 8 (CLI) and Task 13 (watcher consumer) call `record_sweep_result`.

- [ ] **Step 1: Write the failing test**

```python
def test_record_sweep_result_promotes_on_first_run(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_PATH", str(tmp_path / "rmse_best_config.json"))
    sweep = {"imu": [{"config": {"beta": 0.041}, "median_rmse_deg": 4.9, "n_scored": 10}],
            "mediapipe": [{"config": {"model": "full", "vis_thresh": 0.4},
                          "median_rmse_deg": 8.2, "n_scored": 12}]}
    best = rpc.record_sweep_result(sweep)
    assert best["imu"]["rmse"] == 4.9
    assert len(best["history"]) == 2   # one promotion per methodology


def test_record_sweep_result_does_not_promote_within_epsilon(tmp_path, monkeypatch):
    path = str(tmp_path / "rmse_best_config.json")
    monkeypatch.setattr(rpc, "BEST_CONFIG_PATH", path)
    rpc.record_sweep_result({"imu": [{"config": {"beta": 0.041}, "median_rmse_deg": 4.9, "n_scored": 10}],
                             "mediapipe": []})
    best = rpc.record_sweep_result(
        {"imu": [{"config": {"beta": 0.02}, "median_rmse_deg": 4.85, "n_scored": 10}], "mediapipe": []})
    assert best["imu"]["config"] == {"beta": 0.041}   # 0.05 deg improvement < epsilon, not promoted
    assert len(best["history"]) == 1


def test_record_sweep_result_promotes_beyond_epsilon(tmp_path, monkeypatch):
    path = str(tmp_path / "rmse_best_config.json")
    monkeypatch.setattr(rpc, "BEST_CONFIG_PATH", path)
    rpc.record_sweep_result({"imu": [{"config": {"beta": 0.041}, "median_rmse_deg": 4.9, "n_scored": 10}],
                             "mediapipe": []})
    best = rpc.record_sweep_result(
        {"imu": [{"config": {"beta": 0.02}, "median_rmse_deg": 4.5, "n_scored": 10}], "mediapipe": []})
    assert best["imu"]["config"] == {"beta": 0.02}
    assert len(best["history"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'record_sweep_result'`

- [ ] **Step 3: Implement best-config load/save**

Add to `rmse_pipeline_common.py`:

```python
from datetime import datetime

BEST_CONFIG_PATH = os.path.join(BASE_DIR, "rmse_best_config.json")
PROMOTION_EPSILON_DEG = 0.1   # avoid promoting on measurement noise

DEFAULT_BEST_CONFIG = {"mediapipe": None, "imu": None, "history": []}


def load_best_config() -> dict:
    """Missing or malformed file -> DEFAULT_BEST_CONFIG, never raises --
    matching imu_calibration_config.load_config()'s pattern."""
    try:
        with open(BEST_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_BEST_CONFIG.items()}
    if not isinstance(data, dict):
        return {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_BEST_CONFIG.items()}
    merged = {**DEFAULT_BEST_CONFIG, **data}
    merged["history"] = list(data.get("history", []))
    return merged


def _save_best_config(cfg: dict) -> None:
    tmp_path = BEST_CONFIG_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, BEST_CONFIG_PATH)


def record_sweep_result(sweep_result: dict) -> dict:
    """Compare each methodology's top-ranked candidate against the current
    best; promote (update "current best" + append a history entry) only
    when it beats the recorded best by more than PROMOTION_EPSILON_DEG, or
    there is no recorded best yet. Report-only: never writes to
    imu_calibration_config.json or any live config."""
    best = load_best_config()
    now = datetime.now().isoformat(timespec="seconds")

    for methodology in ("imu", "mediapipe"):
        candidates = sweep_result.get(methodology) or []
        if not candidates:
            continue
        top = candidates[0]
        current = best.get(methodology)
        improved = current is None or (current["rmse"] - top["median_rmse_deg"]) > PROMOTION_EPSILON_DEG
        if not improved:
            continue
        entry = {"config": top["config"], "rmse": top["median_rmse_deg"],
                 "updated_at": now, "n_trials": top["n_scored"]}
        best[methodology] = entry
        best["history"].append({"methodology": methodology, **entry})

    _save_best_config(best)
    return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: 14 passed

- [ ] **Step 5: Add `rmse_best_config.json` handling note to `.gitignore` decision**

`rmse_best_config.json` should be tracked in git (it's a small, meaningful history file, same category as `imu_calibration_config.json`) — no `.gitignore` change needed here. Confirm `imu_calibration_config.json` is currently tracked:

Run: `git ls-files imu_calibration_config.json`
Expected: prints `imu_calibration_config.json` (confirms the precedent; if it prints nothing, note this in the commit message instead of assuming)

- [ ] **Step 6: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add report-only best-config tracking with epsilon-gated promotion"
```

---

### Task 7: `rmse_pipeline_common.py` — report generation

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py`

**Interfaces:**
- Consumes: `run_full_sweep()`'s output (Task 5), `load_best_config()` (Task 6).
- Produces: `write_report_outputs(sweep_result: dict, best_config: dict) -> list` — writes `rmse_sweep_results.csv`, `rmse_trend.png`, `sweep_heatmap.png`, `imu_vs_mediapipe_rmse.png` to `Model_Analysis_Outputs/RMSE_Tracking/` and returns the list of written paths. Task 8 calls this.

- [ ] **Step 1: Write the failing test (CSV content, not pixel-level figure content)**

```python
def test_write_report_outputs_writes_csv_with_both_methodologies(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "RMSE_TRACKING_DIR", str(tmp_path))
    sweep = {"imu": [{"config": {"beta": 0.041}, "config_label": "x",
                      "median_rmse_deg": 4.9, "mean_rmse_deg": 5.1, "n_scored": 10, "n_total": 12}],
            "mediapipe": [{"config": {"model": "full", "vis_thresh": 0.4}, "config_label": "y",
                          "median_rmse_deg": 8.2, "mean_rmse_deg": 8.5, "n_scored": 12, "n_total": 12}]}
    best = {"imu": {"config": {"beta": 0.041}, "rmse": 4.9, "updated_at": "now", "n_trials": 10},
           "mediapipe": {"config": {"model": "full", "vis_thresh": 0.4}, "rmse": 8.2,
                        "updated_at": "now", "n_trials": 12},
           "history": []}
    paths = rpc.write_report_outputs(sweep, best)
    csv_path = [p for p in paths if p.endswith("rmse_sweep_results.csv")][0]
    content = open(csv_path, encoding="utf-8").read()
    assert "imu" in content and "mediapipe" in content
    assert "4.9" in content and "8.2" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'write_report_outputs'`

- [ ] **Step 3: Implement report generation**

Add to `rmse_pipeline_common.py`:

```python
import csv as csv_mod

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pendulastic_pt_score import _BG, _PANEL, _HDR

RMSE_TRACKING_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "RMSE_Tracking")


def _write_sweep_csv(sweep_result: dict) -> str:
    path = os.path.join(RMSE_TRACKING_DIR, "rmse_sweep_results.csv")
    rows = []
    for methodology in ("imu", "mediapipe"):
        for row in sweep_result.get(methodology, []):
            rows.append({"methodology": methodology, "config": json.dumps(row["config"]),
                        "median_rmse_deg": row["median_rmse_deg"], "mean_rmse_deg": row["mean_rmse_deg"],
                        "n_scored": row["n_scored"], "n_total": row["n_total"]})
    if rows:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv_mod.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return path


def _write_best_config_trend(best_config: dict) -> str:
    """Best-known RMSE over time, one line per methodology, from
    rmse_best_config.json's history."""
    path = os.path.join(RMSE_TRACKING_DIR, "rmse_trend.png")
    fig, ax = plt.subplots(figsize=(10, 5), facecolor=_BG)
    ax.set_facecolor(_PANEL)
    for methodology, color in (("imu", "#FF9000"), ("mediapipe", "#AA44FF")):
        entries = [h for h in best_config.get("history", []) if h["methodology"] == methodology]
        if not entries:
            continue
        xs = list(range(len(entries)))
        ys = [e["rmse"] for e in entries]
        ax.plot(xs, ys, marker="o", color=color, label=methodology, linewidth=1.8)
    ax.set_title("Best-Known RMSE Over Time", color="#C8D8F0")
    ax.set_ylabel("RMSE (deg)", color="#C8D8F0")
    ax.tick_params(colors="#C8D8F0")
    ax.legend()
    fig.savefig(path, dpi=150, facecolor=_BG)
    plt.close(fig)
    return path


def _write_sweep_heatmap(sweep_result: dict) -> str:
    """Same imshow/RdYlGn_r style as model_vs_optitrack_eval.make_rmse_heatmap,
    one row per methodology's top-10 candidates."""
    path = os.path.join(RMSE_TRACKING_DIR, "sweep_heatmap.png")
    rows_labels, values = [], []
    for methodology in ("imu", "mediapipe"):
        for row in sweep_result.get(methodology, [])[:10]:
            rows_labels.append(f"{methodology}: {row['config_label'][:40]}")
            values.append(row["median_rmse_deg"])
    if not values:
        return path
    fig, ax = plt.subplots(figsize=(10, max(4, len(values) * 0.4)), facecolor=_BG)
    ax.set_facecolor(_PANEL)
    im = ax.imshow(np.array(values).reshape(-1, 1), aspect="auto", cmap="RdYlGn_r", vmin=0, vmax=25)
    ax.set_yticks(range(len(rows_labels)))
    ax.set_yticklabels(rows_labels, color="#C8D8F0", fontsize=7)
    ax.set_xticks([])
    fig.colorbar(im, ax=ax).set_label("RMSE (deg)", color="#C8D8F0")
    fig.savefig(path, dpi=150, facecolor=_BG)
    plt.close(fig)
    return path


def _write_imu_vs_mediapipe_bar(best_config: dict) -> str:
    """Current-best RMSE for both methodologies, computed from the
    identical, same-day trial set -- closes the stale-comparison gap the
    design spec's Background section names."""
    path = os.path.join(RMSE_TRACKING_DIR, "imu_vs_mediapipe_rmse.png")
    fig, ax = plt.subplots(figsize=(6, 5), facecolor=_BG)
    ax.set_facecolor(_PANEL)
    labels, values, colors = [], [], []
    for methodology, color in (("imu", "#FF9000"), ("mediapipe", "#AA44FF")):
        entry = best_config.get(methodology)
        if entry:
            labels.append(methodology)
            values.append(entry["rmse"])
            colors.append(color)
    if values:
        ax.bar(labels, values, color=colors)
    ax.set_ylabel("Best RMSE (deg)", color="#C8D8F0")
    ax.tick_params(colors="#C8D8F0")
    ax.set_title("IMU vs MediaPipe -- Current Best (same trial set)", color="#C8D8F0")
    fig.savefig(path, dpi=150, facecolor=_BG)
    plt.close(fig)
    return path


def write_report_outputs(sweep_result: dict, best_config: dict) -> list:
    os.makedirs(RMSE_TRACKING_DIR, exist_ok=True)
    return [
        _write_sweep_csv(sweep_result),
        _write_best_config_trend(best_config),
        _write_sweep_heatmap(sweep_result),
        _write_imu_vs_mediapipe_bar(best_config),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py -v`
Expected: 15 passed

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add RMSE_Tracking report generation (CSV, trend, heatmap, comparison bar)"
```

---

### Task 8: `run_rmse_sweep.py` — manual-trigger CLI

**Files:**
- Create: `run_rmse_sweep.py`

**Interfaces:**
- Consumes: `rmse_pipeline_common.run_full_sweep`, `record_sweep_result`, `write_report_outputs`.
- Produces: nothing further consumed by later tasks (Part 2's watcher calls `rmse_pipeline_common.run_full_sweep()` etc. directly, not this CLI).

- [ ] **Step 1: Write the script**

```python
"""
run_rmse_sweep.py
==================
Manual trigger for the RMSE validation pipeline: sweeps the IMU and
MediaPipe parameter grids over every trial with OptiTrack ground truth,
updates rmse_best_config.json (report-only -- never touches
imu_calibration_config.json), and regenerates
Model_Analysis_Outputs/RMSE_Tracking/.

This is the Part 1 entry point -- run it by hand after any recording
session. Part 2 (rmse_watcher.py) calls the same
rmse_pipeline_common.run_full_sweep() automatically instead of requiring
this to be run by hand.

Run:
    .venv\\Scripts\\python.exe run_rmse_sweep.py
"""
from __future__ import annotations

import rmse_pipeline_common as rpc


def main():
    print("Discovering scorable trials and sweeping IMU + MediaPipe grids...")
    sweep_result = rpc.run_full_sweep()
    n_imu = len(sweep_result["imu"])
    n_mp = len(sweep_result["mediapipe"])
    print(f"  IMU: {n_imu} scoreable config(s).  MediaPipe: {n_mp} scoreable config(s).")

    best = rpc.record_sweep_result(sweep_result)
    for methodology in ("imu", "mediapipe"):
        entry = best.get(methodology)
        if entry:
            print(f"  Best {methodology}: {entry['config']} -- "
                 f"{entry['rmse']:.2f} deg over {entry['n_trials']} trial(s)")

    paths = rpc.write_report_outputs(sweep_result, best)
    for p in paths:
        print(f"-> {p}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it against the real dataset**

Run: `.venv\Scripts\python.exe run_rmse_sweep.py`
Expected: prints discovered-trial counts, best config per methodology, and 4 output paths under `Model_Analysis_Outputs/RMSE_Tracking/`. This is a real full-grid sweep (288 IMU configs x every IMU trial, 9 MediaPipe configs x every video trial) — expect several minutes on first run; subsequent runs are fast for unchanged trials via the Task 4 cache.

- [ ] **Step 3: Commit**

```bash
git add run_rmse_sweep.py
git commit -m "feat: add manual-trigger CLI for the RMSE validation pipeline"
```

**Part 1 checkpoint:** `run_rmse_sweep.py` is now a complete, independently useful deliverable — run it after any recording session and get current, non-stale IMU/MediaPipe RMSE numbers and a tracked best-known config. Part 2 automates *triggering* this; it adds no new scoring logic.

---

## Part 2: Automated Watcher

### Task 9: `rmse_watcher.py` — debounce core

**Files:**
- Create: `rmse_watcher.py`
- Test: `tests/test_rmse_watcher.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `Watcher` class with `__init__(self, clock=time.time)`, `on_file_event(self, path: str) -> None`, `tick(self) -> list` (returns trial keys that just became due). Task 10 extends `tick()`; Task 13 wires it to a real consumer.

- [ ] **Step 1: Add `watchdog` to requirements.txt**

Append to `requirements.txt`:
```
watchdog>=4.0.0
```

Run: `.venv\Scripts\pip install -r requirements.txt`
Expected: `watchdog` installs successfully.

- [ ] **Step 2: Write the failing test (fake clock, no real files or sleeps)**

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import rmse_watcher


def test_on_file_event_debounces_same_key_within_window():
    t = [0.0]
    w = rmse_watcher.Watcher(clock=lambda: t[0])
    w.on_file_event(r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_1_imu.csv")
    t[0] = 3.0
    w.on_file_event(r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_1_accel.csv")
    # still within the 8s debounce window from the SECOND event
    t[0] = 10.0
    assert w.tick() == []
    t[0] = 11.5
    assert w.tick() == ["15_right_pre_T1"]


def test_tick_only_fires_once_per_key():
    t = [0.0]
    w = rmse_watcher.Watcher(clock=lambda: t[0])
    w.on_file_event(r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_1_imu.csv")
    t[0] = 9.0
    assert w.tick() == ["15_right_pre_T1"]
    assert w.tick() == []   # already fired, not re-armed without a new event
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rmse_watcher'`

- [ ] **Step 4: Implement the debounce core**

```python
"""
rmse_watcher.py
================
Long-running service: watches Recordings/ and OptiTrack_Recordings/ for new
or changed trial data, debounces per-trial-key, waits for file stability,
then calls rmse_pipeline_common.run_full_sweep() automatically. See
docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md.

Run (foreground, for testing):
    .venv\\Scripts\\python.exe rmse_watcher.py

Deployment: docs/rmse_pipeline/deployment.md (Windows Scheduled Task,
registered manually -- requires interactive password entry, not scripted).
"""
from __future__ import annotations

import os
import re
import time
from typing import Callable

import rmse_pipeline_common as rpc

DEBOUNCE_SECONDS = 8.0

_LEG_RE = re.compile(r"(?:^|_)(left|right)(?:_|$)", re.I)
_TRIAL_RE = re.compile(r"[Tt]rial[_ ]?(\d+)")


class Watcher:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._due: dict = {}      # trial_key -> due_time
        self._fired: set = set()  # trial_keys already pushed since their last event

    def _trial_key_for_path(self, path: str) -> str:
        rel = os.path.relpath(path, rpc.REC_ROOT).replace("\\", "/")
        parts = rel.split("/")
        m = re.search(r"Participant_(\d+)", rel, re.I)
        participant = m.group(1) if m else "unknown"
        leg, condition = rpc._derive_leg_condition(parts)
        m_trial = _TRIAL_RE.search(os.path.basename(path))
        trial = m_trial.group(1) if m_trial else "0"
        return f"{participant}_{leg}_{condition}_T{trial}"

    def on_file_event(self, path: str) -> None:
        try:
            key = self._trial_key_for_path(path)
        except ValueError:
            return   # path outside REC_ROOT
        self._due[key] = self._clock() + DEBOUNCE_SECONDS
        self._fired.discard(key)

    def tick(self) -> list:
        now = self._clock()
        due_now = [k for k, due_at in self._due.items()
                  if due_at <= now and k not in self._fired]
        for k in due_now:
            self._fired.add(k)
        return due_now
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add rmse_watcher.py tests/test_rmse_watcher.py requirements.txt
git commit -m "feat: add RMSE watcher debounce core with clock-injected testing"
```

---

### Task 10: `rmse_watcher.py` — file-stability + completeness gating

**Files:**
- Modify: `rmse_watcher.py`
- Test: `tests/test_rmse_watcher.py`

**Interfaces:**
- Consumes: `Watcher.tick()` from Task 9; `rmse_pipeline_common.discover_scorable_trials()` (Task 1) for completeness.
- Produces: `Watcher.tick()` now returns only trial keys that are BOTH due AND file-stable AND complete (have an OptiTrack counterpart per discovery); unstable keys are re-armed. `Watcher._is_stable(self, key)`, `Watcher._is_complete(self, key)` become the extension points Task 13's consumer loop relies on indirectly through `tick()`.

- [ ] **Step 1: Write the failing test**

```python
def test_tick_defers_unstable_key(monkeypatch):
    t = [0.0]
    w = rmse_watcher.Watcher(clock=lambda: t[0])
    monkeypatch.setattr(w, "_is_stable", lambda key: False)
    w.on_file_event(r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_1_imu.csv")
    t[0] = 9.0
    assert w.tick() == []          # unstable -> deferred, not fired
    assert "15_right_pre_T1" in w._due   # re-armed, still pending


def test_tick_skips_incomplete_key(monkeypatch):
    t = [0.0]
    w = rmse_watcher.Watcher(clock=lambda: t[0])
    monkeypatch.setattr(w, "_is_stable", lambda key: True)
    monkeypatch.setattr(w, "_is_complete", lambda key: False)
    w.on_file_event(r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_1_imu.csv")
    t[0] = 9.0
    assert w.tick() == []
    assert "15_right_pre_T1" not in w._fired


def test_tick_fires_stable_complete_key(monkeypatch):
    t = [0.0]
    w = rmse_watcher.Watcher(clock=lambda: t[0])
    monkeypatch.setattr(w, "_is_stable", lambda key: True)
    monkeypatch.setattr(w, "_is_complete", lambda key: True)
    w.on_file_event(r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_1_imu.csv")
    t[0] = 9.0
    assert w.tick() == ["15_right_pre_T1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: FAIL — unstable/incomplete keys still fire (no gating implemented yet)

- [ ] **Step 3: Implement stability + completeness gating**

Replace `tick()` in `rmse_watcher.py`:

```python
    def _is_stable(self, key: str) -> bool:
        """True if every file tied to this trial key has had a stable size
        across two ~1.5s-apart stat() polls and opens in shared-read mode
        without a lock error. Real filesystem check -- Task 14 wires the
        real watchdog Observer; this method is called directly in tests via
        monkeypatch, matching the spec's clock-injected testability
        requirement (no real sleeps in tests)."""
        matches = [t for t in rpc.discover_scorable_trials() if t["key"] == key]
        if not matches:
            return True   # nothing on disk yet for this key -- not a stability question
        trial = matches[0]
        paths = [trial["optitrack_path"]]
        if trial.get("video_path"):
            paths.append(trial["video_path"])
        if trial.get("imu_paths"):
            paths.extend(trial["imu_paths"].values())
        try:
            sizes_1 = [os.path.getsize(p) for p in paths if os.path.isfile(p)]
            time.sleep(1.5)
            sizes_2 = [os.path.getsize(p) for p in paths if os.path.isfile(p)]
        except OSError:
            return False
        return sizes_1 == sizes_2

    def _is_complete(self, key: str) -> bool:
        return any(t["key"] == key for t in rpc.discover_scorable_trials())

    def tick(self) -> list:
        now = self._clock()
        due_now = [k for k, due_at in self._due.items()
                  if due_at <= now and k not in self._fired]
        ready = []
        for k in due_now:
            if not self._is_stable(k):
                self._due[k] = now + DEBOUNCE_SECONDS   # re-arm, bounded retry is Task 12's concern
                continue
            if not self._is_complete(k):
                continue   # source data present but no OptiTrack counterpart yet -- wait for reconciliation
            self._fired.add(k)
            ready.append(k)
        return ready
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add rmse_watcher.py tests/test_rmse_watcher.py
git commit -m "feat: gate RMSE watcher triggers on file stability and OptiTrack completeness"
```

---

### Task 11: `rmse_watcher.py` — bounded retry + reconciliation pass

**Files:**
- Modify: `rmse_watcher.py`
- Test: `tests/test_rmse_watcher.py`

**Interfaces:**
- Consumes: `_load_cache`/`SWEEP_CACHE_DIR` (Task 4), `discover_scorable_trials()` (Task 1).
- Produces: `Watcher.reconciliation_pass(self) -> list` (returns trial keys pushed that weren't already cached), bounded retry (max ~60s) on `tick()`'s unstable path.

- [ ] **Step 1: Write the failing test**

```python
def test_unstable_key_gives_up_after_bounded_retries(monkeypatch):
    t = [0.0]
    w = rmse_watcher.Watcher(clock=lambda: t[0])
    monkeypatch.setattr(w, "_is_stable", lambda key: False)
    w.on_file_event(r"C:\Users\cladi\Pendulastic\Recordings\Participant_15\Right\pre\Trial_1_imu.csv")
    for _ in range(20):
        t[0] += rmse_watcher.DEBOUNCE_SECONDS + 0.1
        w.tick()
    assert "15_right_pre_T1" not in w._due   # gave up, deferred to reconciliation
    assert "15_right_pre_T1" in w._deferred


def test_reconciliation_pass_pushes_uncached_trials(monkeypatch):
    w = rmse_watcher.Watcher()
    monkeypatch.setattr(rpc, "discover_scorable_trials",
                        lambda: [{"key": "15_right_pre_T1", "cache_stat_key": "1:1"},
                                {"key": "14_left_pre_T2", "cache_stat_key": "2:2"}])
    monkeypatch.setattr(rpc, "_load_cache", lambda m: {"14_left_pre_T2|2:2|x|y": 4.0})
    pushed = w.reconciliation_pass()
    assert pushed == ["15_right_pre_T1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: FAIL — no bounded-retry giveup, no `reconciliation_pass`

- [ ] **Step 3: Implement bounded retry and reconciliation**

Modify `Watcher.__init__` and `tick()`, add `reconciliation_pass()`:

```python
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._due: dict = {}
        self._fired: set = set()
        self._retry_started_at: dict = {}
        self._deferred: set = set()

    ...

    def tick(self) -> list:
        now = self._clock()
        due_now = [k for k, due_at in self._due.items()
                  if due_at <= now and k not in self._fired]
        ready = []
        for k in due_now:
            if not self._is_stable(k):
                started = self._retry_started_at.setdefault(k, now)
                if now - started > 60.0:
                    self._due.pop(k, None)
                    self._retry_started_at.pop(k, None)
                    self._deferred.add(k)
                else:
                    self._due[k] = now + DEBOUNCE_SECONDS
                continue
            self._retry_started_at.pop(k, None)
            if not self._is_complete(k):
                continue
            self._fired.add(k)
            ready.append(k)
        return ready

    def reconciliation_pass(self) -> list:
        """Cheap stat-only diff of every discoverable trial against the
        sweep cache's existing keys -- the safety net for watchdog events
        genuinely dropped at the OS level (Windows file-system event
        coalescing). Anything not yet cached under ANY config for either
        methodology is pushed."""
        pushed = []
        imu_cache = rpc._load_cache("imu")
        mp_cache = rpc._load_cache("mediapipe")
        cached_keys = {k.split("|", 1)[0] for k in list(imu_cache) + list(mp_cache)}
        for trial in rpc.discover_scorable_trials():
            if trial["key"] not in cached_keys:
                pushed.append(trial["key"])
        return pushed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add rmse_watcher.py tests/test_rmse_watcher.py
git commit -m "feat: add bounded-retry giveup and reconciliation pass to RMSE watcher"
```

---

### Task 12: `rmse_watcher.py` — single consumer loop + failure isolation + logging

**Files:**
- Modify: `rmse_watcher.py`
- Test: `tests/test_rmse_watcher.py`

**Interfaces:**
- Consumes: `Watcher.tick()`/`reconciliation_pass()` (Tasks 9-11), `rpc.run_full_sweep`/`record_sweep_result`/`write_report_outputs` (Part 1).
- Produces: `Watcher.push(self, key: str) -> None` (adds to internal queue), `Watcher.consume_one(self) -> bool` (pops and processes one queued trigger via a full sweep; returns False if queue was empty). A single `run_full_sweep()` call serves every queued key at once (it always sweeps the whole dataset), so multiple queued keys collapse into one sweep — documented explicitly since it's a deliberate simplification vs. per-trial sweeping.

- [ ] **Step 1: Write the failing test**

```python
def test_consume_one_processes_queued_trigger_and_survives_failure(monkeypatch):
    w = rmse_watcher.Watcher()
    calls = {"n": 0}

    def failing_sweep():
        calls["n"] += 1
        raise RuntimeError("corrupt CSV")

    monkeypatch.setattr(rpc, "run_full_sweep", failing_sweep)
    w.push("15_right_pre_T1")
    assert w.consume_one() is True    # caught internally, did not raise
    assert calls["n"] == 1
    assert w.consume_one() is False   # queue now empty


def test_consume_one_calls_full_pipeline_on_success(monkeypatch):
    w = rmse_watcher.Watcher()
    order = []
    monkeypatch.setattr(rpc, "run_full_sweep", lambda: order.append("sweep") or {"imu": [], "mediapipe": []})
    monkeypatch.setattr(rpc, "record_sweep_result", lambda s: order.append("record") or {})
    monkeypatch.setattr(rpc, "write_report_outputs", lambda s, b: order.append("report") or [])
    w.push("15_right_pre_T1")
    w.consume_one()
    assert order == ["sweep", "record", "report"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: FAIL — no `push`/`consume_one`

- [ ] **Step 3: Implement the consumer**

Add to `rmse_watcher.py`:

```python
import logging
import logging.handlers
import queue

LOG_DIR = os.path.join(rpc.BASE_DIR, "docs", "rmse_pipeline")
LOG_PATH = os.path.join(LOG_DIR, "watcher-runtime.log")


def _make_logger() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("rmse_watcher")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger
```

Add to `Watcher.__init__`:
```python
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._logger = _make_logger()
```

Add methods:
```python
    def push(self, key: str) -> None:
        self._queue.put(key)

    def consume_one(self) -> bool:
        """Pop and process exactly one queued trigger. A single
        run_full_sweep() covers the whole dataset regardless of which key
        triggered it, so this drains the queue's head but the sweep itself
        serves every currently-queued key. Any exception (per-trial
        failures are already caught inside run_full_sweep/its candidate
        scorers; this catches anything unhandled) is logged, not raised --
        one bad input can't take down the watcher process."""
        try:
            self._queue.get_nowait()
        except queue.Empty:
            return False
        try:
            sweep_result = rpc.run_full_sweep()
            best = rpc.record_sweep_result(sweep_result)
            rpc.write_report_outputs(sweep_result, best)
            self._logger.info("Sweep complete: imu=%d mediapipe=%d candidates",
                             len(sweep_result.get("imu", [])), len(sweep_result.get("mediapipe", [])))
        except Exception as exc:
            self._logger.exception("Sweep failed: %s", exc)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_rmse_watcher.py -v`
Expected: 9 passed

- [ ] **Step 5: Add `docs/rmse_pipeline/` log directory to `.gitignore`**

Append to `.gitignore`:
```
docs/rmse_pipeline/watcher-runtime.log*
```

- [ ] **Step 6: Commit**

```bash
git add rmse_watcher.py tests/test_rmse_watcher.py .gitignore
git commit -m "feat: add RMSE watcher consumer loop with failure isolation and rotating log"
```

---

### Task 13: `rmse_watcher.py` — real `watchdog.Observer` wiring + `main()`

**Files:**
- Modify: `rmse_watcher.py`

**Interfaces:**
- Consumes: `Watcher` (Tasks 9-12), `watchdog.observers.Observer`, `watchdog.events.FileSystemEventHandler`.
- Produces: `main()` — the process entry point Task 14's deployment doc points Task Scheduler at.

- [ ] **Step 1: Implement the thin watchdog adapter and main loop**

Add to `rmse_watcher.py`:

```python
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

TICK_INTERVAL_SECONDS = 2.0
RECONCILIATION_INTERVAL_SECONDS = 600.0


class _EventAdapter(FileSystemEventHandler):
    """No logic of its own -- every event forwards straight to
    watcher.on_file_event(path), matching the design spec's testability
    requirement that all debounce/stability logic live in plain,
    directly-callable Watcher methods, not here."""

    def __init__(self, watcher: Watcher):
        self._watcher = watcher

    def on_created(self, event):
        if not event.is_directory:
            self._watcher.on_file_event(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._watcher.on_file_event(event.src_path)


def main():
    watcher = Watcher()
    observer = Observer()
    handler = _EventAdapter(watcher)
    observer.schedule(handler, rpc.REC_ROOT, recursive=True)
    observer.schedule(handler, rpc.OPTI_ROOT, recursive=True)
    observer.start()
    watcher._logger.info("RMSE watcher started, watching %s and %s", rpc.REC_ROOT, rpc.OPTI_ROOT)

    last_reconciliation = time.time()
    try:
        while True:
            for key in watcher.tick():
                watcher.push(key)
            while watcher.consume_one():
                pass
            if time.time() - last_reconciliation > RECONCILIATION_INTERVAL_SECONDS:
                for key in watcher.reconciliation_pass():
                    watcher.push(key)
                while watcher.consume_one():
                    pass
                last_reconciliation = time.time()
            time.sleep(TICK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        watcher._logger.info("RMSE watcher stopping (KeyboardInterrupt)")
    finally:
        observer.stop()
        observer.join()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Manually verify the process starts and shuts down cleanly**

Run: `.venv\Scripts\python.exe rmse_watcher.py`
Expected: no traceback, `docs/rmse_pipeline/watcher-runtime.log` is created with a "watching ..." line. Press Ctrl+C.
Expected: logs "stopping (KeyboardInterrupt)" and the process exits without hanging.

- [ ] **Step 3: Commit**

```bash
git add rmse_watcher.py
git commit -m "feat: wire real watchdog Observer and main loop for RMSE watcher"
```

---

### Task 14: Deployment documentation (manual step — not scripted)

**Files:**
- Create: `docs/rmse_pipeline/deployment.md`

**Interfaces:** none — this is a runbook for the user, not code.

- [ ] **Step 1: Write the deployment runbook**

```markdown
# RMSE Watcher — Windows Scheduled Task Deployment

This must be registered manually by you, interactively — the account
password goes into Task Scheduler's credential store, which cannot be
scripted or passed via a command Claude runs (it would land in shell
history/logs). This is a deliberate one-time manual step, not an
oversight.

## Steps

1. Open Task Scheduler (`taskschd.msc`).
2. Create Task (not "Create Basic Task" — need the full properties dialog).
3. **General tab:**
   - Name: `Pendulastic RMSE Watcher`
   - "Run whether user is logged on or not" (NOT "Run only when user is
     logged on" — chosen so the watcher survives logout/reboot; this is
     what requires storing your password).
4. **Triggers tab:** New -> "At startup".
5. **Actions tab:** New ->
   - Program/script: `C:\Users\cladi\Pendulastic\.venv\Scripts\python.exe`
   - Arguments: `C:\Users\cladi\Pendulastic\rmse_watcher.py`
   - Start in: `C:\Users\cladi\Pendulastic`
6. **Settings tab:**
   - Check "If the task fails, restart every" -> 1 minute, up to 3 attempts
     (per-trial failures are already caught inside the watcher; this only
     covers a genuinely unhandled crash in its own loop).
   - Uncheck "Stop the task if it runs longer than" (this is a
     long-running service, not a bounded job).
7. Save. You will be prompted for your Windows account password at this
   point — that's the credential store step described above.
8. Verify: right-click the task -> Run. Then check
   `docs\rmse_pipeline\watcher-runtime.log` for a "watching ..." line.

## Verifying it's alive later

```
schtasks /Query /TN "Pendulastic RMSE Watcher" /V /FO LIST
Get-Content docs\rmse_pipeline\watcher-runtime.log -Tail 20
```

## Stopping it

```
schtasks /End /TN "Pendulastic RMSE Watcher"
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/rmse_pipeline/deployment.md
git commit -m "docs: add manual Windows Scheduled Task deployment runbook for RMSE watcher"
```

- [ ] **Step 3: Hand off to the user**

Registering the task itself is out of scope for this step (requires the interactive password prompt) — tell the user the runbook is ready at `docs\rmse_pipeline\deployment.md` and ask if they want to register it now.

---

### Task 15: Full regression pass

**Files:** none (verification only).

- [ ] **Step 1: Run the new test files together**

Run: `.venv\Scripts\pytest tests\test_rmse_pipeline_common.py tests\test_rmse_watcher.py -v`
Expected: all pass.

- [ ] **Step 2: Run the full existing suite to confirm no cross-module regressions**

Run: `.venv\Scripts\pytest tests\ -v`
Expected: all pass, including `tests\test_pt_cohort_common.py` and `tests\test_pt_report_common.py` (matching the verification step used for the MS-vs-Control cohort work per the design spec's Testing section).

- [ ] **Step 3: Confirm no modification to the wrapped standalone scripts**

Run: `git diff --stat main -- batch_imu_vs_optitrack_rmse.py sweep_imu_config.py sweep_mediapipe_config.py model_vs_optitrack_eval.py imu_calibration_tuner.py workbench_engine.py evaluate_all_participants.py`
Expected: no output (zero changes) — confirms the Global Constraints wrap-don't-modify rule held throughout. (`sweep_imu_config.py`'s pre-existing uncommitted local diff from before this plan started is a separate, already-known exception — not introduced by this plan.)

- [ ] **Step 4: Final review against the design spec**

Read back `docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md` section by section and confirm every requirement has a corresponding task: discovery (Task 1), IMU/MediaPipe scoring wrappers (Tasks 2-3), sweep cache (Task 4), `run_full_sweep` (Task 5), best-config promotion (Task 6), report outputs (Task 7), manual CLI (Task 8), watcher debounce/stability/reconciliation/consumer (Tasks 9-12), real Observer wiring (Task 13), deployment (Task 14). No further commit needed — this is a review checkpoint.
