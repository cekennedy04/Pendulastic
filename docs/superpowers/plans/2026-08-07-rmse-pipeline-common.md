# RMSE Pipeline Common Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `rmse_pipeline_common.py` — the discovery, scoring, caching, ranking, and
promotion engine for continuous IMU-vs-OptiTrack and MediaPipe-vs-OptiTrack RMSE validation —
as a standalone, manually-triggerable module, independent of the watcher process that will
call it automatically (separate follow-up plan).

**Architecture:** One new flat module at the repo root, following this repo's existing
convention (`pt_report_common.py`, `pt_cohort_common.py`). It wraps and generalizes four
existing one-off scripts (`batch_imu_vs_optitrack_rmse.py`, `sweep_imu_config.py`,
`sweep_mediapipe_config.py`, `batch_mediapipe.py`) without modifying any of them, using their
proven scoring/discovery primitives (`workbench_engine.compare_pair`,
`imu_calibration_tuner.replay_trial`, `reconstruct_imu_raw_logs.reconstruct_trial`,
`batch_mediapipe.discover_new_trials`'s video-finding pattern) rather than reimplementing them.

**Tech Stack:** Python, numpy, scipy (via `workbench_engine`), OpenCV + MediaPipe (via the
existing landmark-extraction code), hashlib (sha256 content fingerprints), pytest
(`monkeypatch`/`tmp_path`, plain functions, no test classes — matches
`tests/test_pt_cohort_common.py`).

## Global Constraints

- Run tests and scripts with `.venv\Scripts\python.exe` (this repo's working environment).
- This module never modifies `batch_imu_vs_optitrack_rmse.py`, `sweep_imu_config.py`,
  `sweep_mediapipe_config.py`, `batch_mediapipe.py`, `reconstruct_imu_raw_logs.py`,
  `imu_calibration_tuner.py`, `imu_calibration_config.py`, or `workbench_engine.py` — it imports
  and calls into them. It also never writes to `imu_calibration_config.json` or
  `participant_groups.json`.
- All new persisted files (`sweep_cache/`, `rmse_best_config.json`) use atomic writes
  (temp file + `os.replace`), matching `imu_calibration_config.py`'s existing `save_config()`
  pattern.
- `sweep_imu_config.WIDE_GRID` and `sweep_mediapipe_config.MODEL_VARIANTS`/
  `VIS_THRESH_CANDIDATES` are always imported live from those modules, never copied — both grids
  are actively hand-tuned (confirmed via `sweep_imu_config.py`'s own uncommitted local edits
  during this design's investigation).
- Reference: `docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md` (all section
  numbers below, e.g. "§4", refer to this file — read its Revision note first, it records three
  rounds of Codex review fixes already folded into the spec text).
- **Do not build any discovery logic on `evaluate_all_participants.DataIndex`.** Confirmed against
  current code (post-plan Codex consult, 2026-08-07): `_pid_pos_from_path()` requires both a
  `Participant_<id>` and a `Position_<pos>` path segment to resolve a trial; newer participants
  (P14, P15) don't reliably have a `Position_*` segment, so `DataIndex`-based discovery silently
  drops them. This plan's Tasks 1-4 already avoid this (fresh structural parser, not
  `DataIndex`) — this constraint exists to keep it that way through every later task too.
- **`excluded_trials.json`** (repo root, `pt_report_common.EXCLUDED_TRIALS_PATH`) is a
  hand-maintained registry of trials to drop from every discovery/report/sweep — e.g. a trial
  where the participant used their own muscles to stop the pendulum swing instead of a passive
  release, which would let a candidate config spuriously score well against non-passive motion.
  `discover_scorable_trials()` (Task 4) must filter against it via
  `pt_report_common.load_excluded_trials()` and `pt_report_common.trial_key(participant, leg,
  condition, trial_number)` — the same legacy string-key format `pt_report_common.py` and
  `pt_cohort_common.py` already use for this registry. This is a *different* key from this
  module's own SHA-256 structural `trial_key` (§4) — never conflate the two; the legacy string key
  is only ever used as a lookup into the exclusion registry, never as cache or ranking identity.
- `trial_key` is a hash of the **structural** tuple `(participant, leg, condition, session,
  position, height, trial_number)` — never of which source files exist (§4's fix, corrected
  twice during review — see the spec's revision note for why).
- Ranking/promotion always operates on a **frozen cohort** per methodology per sweep — a
  candidate that fails a required cohort trial is excluded from ranking, never aggregated over a
  smaller/easier subset (§7.2).
- The `sweep_cache/`'s stat-pre-filter (skip re-hashing an unchanged file) is a speed
  optimization for the frequently-retriggered path only. It has no place in this plan's
  correctness-critical reconciliation logic — that logic belongs to the watcher plan, not this
  one, but any code this plan writes that's reused there (the fingerprint functions) must support
  being called with the pre-filter *disabled* (force full rehash), since the watcher plan's
  reconciliation pass needs that mode (§7.1's third-round correction).

---

### Task 1: Structural trial identity — path parsing and `trial_key`

**Files:**
- Create: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (new)

**Interfaces:**
- Produces: `rmse_pipeline_common.parse_structural_fields(path: str, root: str) -> dict | None`,
  `rmse_pipeline_common.compute_trial_key(fields: dict) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rmse_pipeline_common.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import rmse_pipeline_common as rpc


# ── parse_structural_fields ──────────────────────────────────────────────

def test_parse_structural_fields_full_path():
    path = os.path.normpath(
        "OptiTrack_Recordings/Participant_13_left_post/Session_post/"
        "Position_1/Height_Joint-Level/trial_1_optitrack.csv")
    root = "OptiTrack_Recordings"
    fields = rpc.parse_structural_fields(path, root)
    assert fields["participant"] == "13"
    assert fields["leg"] == "left"
    assert fields["session"] == "post"
    assert fields["position"] == "1"
    assert fields["height"] == "joint-level"
    assert fields["trial_number"] == "1"


def test_parse_structural_fields_missing_position_and_height():
    # Real observed case: Participant_13_right_post's OptiTrack CSVs sit one
    # directory level higher than left_post's, with no Position_/Height_
    # segment at all -- must not fail to parse, must default those two
    # fields to a stable placeholder rather than raising or returning None.
    path = os.path.normpath(
        "OptiTrack_Recordings/Participant_13_right_post/Session_post/trial_1_optitrack.csv")
    root = "OptiTrack_Recordings"
    fields = rpc.parse_structural_fields(path, root)
    assert fields["participant"] == "13"
    assert fields["leg"] == "right"
    assert fields["session"] == "post"
    assert fields["position"] == "none"
    assert fields["height"] == "none"
    assert fields["trial_number"] == "1"


def test_parse_structural_fields_no_session_segment():
    path = os.path.normpath("Recordings/Participant_14/Left/pre/Trial_3.avi")
    root = "Recordings"
    fields = rpc.parse_structural_fields(path, root)
    assert fields["participant"] == "14"
    assert fields["leg"] == "left"
    assert fields["condition"] == "pre"
    assert fields["session"] == "none"
    assert fields["trial_number"] == "3"


def test_parse_structural_fields_no_leg_returns_none():
    path = os.path.normpath("OptiTrack_Recordings/Participant_9/trial_1_optitrack.csv")
    fields = rpc.parse_structural_fields(path, "OptiTrack_Recordings")
    assert fields is None


def test_parse_structural_fields_ambiguous_participant_returns_none():
    # Archived data can nest a stray folder from a different participant --
    # pt_report_common._parse_trial_path already treats this as unparseable;
    # match that behavior rather than guessing.
    path = os.path.normpath(
        "OptiTrack_Recordings/Participant_5/Participant_0_control/left/trial_1_optitrack.csv")
    fields = rpc.parse_structural_fields(path, "OptiTrack_Recordings")
    assert fields is None


def test_parse_structural_fields_case_insensitive_and_normalized():
    path = os.path.normpath(
        "OptiTrack_Recordings/PARTICIPANT_13_LEFT_post/SESSION_Post/Trial_2_optitrack.csv")
    fields = rpc.parse_structural_fields(path, "OptiTrack_Recordings")
    assert fields["participant"] == "13"
    assert fields["leg"] == "left"
    assert fields["session"] == "post"


# ── compute_trial_key ────────────────────────────────────────────────────

def test_compute_trial_key_deterministic():
    fields = {"participant": "13", "leg": "left", "condition": "post",
             "session": "post", "position": "1", "height": "joint-level",
             "trial_number": "1"}
    assert rpc.compute_trial_key(fields) == rpc.compute_trial_key(dict(fields))


def test_compute_trial_key_differs_on_position():
    base = {"participant": "13", "leg": "left", "condition": "post",
           "session": "post", "position": "1", "height": "joint-level",
           "trial_number": "1"}
    other = {**base, "position": "2"}
    assert rpc.compute_trial_key(base) != rpc.compute_trial_key(other)


def test_compute_trial_key_stable_under_key_order():
    fields = {"participant": "13", "leg": "left", "condition": "post",
             "session": "post", "position": "1", "height": "joint-level",
             "trial_number": "1"}
    reordered = dict(reversed(list(fields.items())))
    assert rpc.compute_trial_key(fields) == rpc.compute_trial_key(reordered)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rmse_pipeline_common'`

- [ ] **Step 3: Create `rmse_pipeline_common.py` with the structural parser**

```python
"""
rmse_pipeline_common.py
========================
Discovery, scoring, caching, ranking, and promotion engine for continuous
IMU-vs-OptiTrack and MediaPipe-vs-OptiTrack RMSE validation.

Wraps and generalizes batch_imu_vs_optitrack_rmse.py, sweep_imu_config.py,
sweep_mediapipe_config.py, and batch_mediapipe.py's proven discovery/scoring
primitives without modifying any of them. See
docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md for the
full design (three rounds of Codex review folded into the spec text -- read
its revision note first).
"""
from __future__ import annotations

import hashlib
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT = os.path.join(BASE_DIR, "OptiTrack_Recordings")
SWEEP_CACHE_DIR = os.path.join(BASE_DIR, "sweep_cache")
RMSE_TRACKING_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "RMSE_Tracking")
BEST_CONFIG_JSON = os.path.join(BASE_DIR, "rmse_best_config.json")

_PLACEHOLDER = "none"


def _norm(s):
    return s.strip().lower() if s else _PLACEHOLDER


def parse_structural_fields(path, root):
    """Extract the seven canonical structural identity fields from a trial
    path (design spec §4). Models pt_report_common._parse_trial_path's
    proven participant/leg extraction, extended to also capture session,
    position, and height as independent fields (not merged into one
    "condition" string -- design spec §4, tightened in the third Codex
    review round to keep condition and session distinct).

    position and height are frequently absent from the real folder
    structure (e.g. Participant_13_right_post's OptiTrack CSVs sit one
    level higher than left_post's, with no Position_/Height_ segment at
    all) -- both default to the "none" placeholder rather than causing a
    parse failure. participant, leg, and trial_number are required; a path
    that can't resolve all three, or that matches more than one distinct
    participant number (a known archived-data nesting issue), returns None
    rather than guessing."""
    rel = os.path.relpath(path, root).replace("\\", "/")

    pids = sorted(set(m.group(1) for m in re.finditer(r"Participant_(\d+)", rel, re.I)))
    if len(pids) != 1:
        return None
    participant = pids[0]

    m_leg = re.search(r"(?:^|[_/])(left|right)(?:[_/]|$)", rel, re.I)
    if not m_leg:
        return None
    leg = m_leg.group(1).lower()

    m_trial = re.search(r"trial[_\s]*(\d+)", os.path.basename(path), re.I)
    if not m_trial:
        return None
    trial_number = m_trial.group(1)

    session = _PLACEHOLDER
    m_session = re.search(r"(?:^|/)Session_([^/]+)", rel, re.I)
    if m_session:
        session = _norm(m_session.group(1))

    position = _PLACEHOLDER
    m_position = re.search(r"(?:^|/)Position_([^/]+)", rel, re.I)
    if m_position:
        position = _norm(m_position.group(1))

    height = _PLACEHOLDER
    m_height = re.search(r"(?:^|/)Height_([^/]+)", rel, re.I)
    if m_height:
        height = _norm(m_height.group(1))

    # condition: same folder-name-cleanup approach as
    # pt_report_common._parse_trial_path -- strip the participant prefix,
    # the leg token, and any Session_/Position_/Height_ segments, keep
    # whatever's left of the parent-directory chain, deduplicated.
    parts = rel.split("/")[:-1]
    cond_parts = []
    for part in parts:
        low = part.lower()
        if low.startswith("session_") or low.startswith("position_") or low.startswith("height_"):
            continue
        cleaned = part
        if low.startswith("participant_"):
            cleaned = re.sub(r"^participant_\d+_?", "", cleaned, flags=re.I)
        cleaned = re.sub(r"(left|right)", "", cleaned, flags=re.I).strip("_")
        if cleaned:
            cond_parts.append(_norm(cleaned))
    condition = "_".join(dict.fromkeys(cond_parts)) or _PLACEHOLDER

    return {
        "participant": participant, "leg": leg, "condition": condition,
        "session": session, "position": position, "height": height,
        "trial_number": trial_number,
    }


_TRIAL_KEY_FIELDS = ("participant", "leg", "condition", "session",
                    "position", "height", "trial_number")


def compute_trial_key(fields):
    """Deterministic hash of the canonical structural tuple (design spec
    §4, position bug fixed in the third Codex review round). Never hashes
    which source files exist -- that would break identity stability across
    a capability change (e.g. a video added later for a previously
    IMU-only capture)."""
    canonical = {"v": 1, **{k: fields[k] for k in _TRIAL_KEY_FIELDS}}
    blob = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add structural trial identity parsing and trial_key"
```

---

### Task 2: IMU/OptiTrack trial discovery

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: `parse_structural_fields`, `compute_trial_key` (Task 1);
  `batch_imu_vs_optitrack_rmse.discover_trials()` (existing, reused as-is)
- Produces: `rmse_pipeline_common.discover_imu_trials() -> list[dict]` — each dict has
  `trial_key`, the 7 structural fields, `imu_anchor_path`, `imu_component_paths` (dict with
  `imu`/`accel`/`gyro`/`mag` keys), `optitrack_path` (nullable)

- [ ] **Step 1: Write the failing tests**

```python
# ── discover_imu_trials ───────────────────────────────────────────────────

def test_discover_imu_trials_wraps_batch_script(monkeypatch):
    fake_trials = [{
        "participant": "Participant_13_left_post", "position": "Position_1",
        "trial": "Trial_1",
        "imu": os.path.normpath(
            "Recordings/Participant_13_left_post/Session_post/Position_1/"
            "Height_Joint-Level/Trial_1_imu.csv"),
        "accel": "x_accel.csv", "gyro": "x_gyro.csv", "mag": "x_mag.csv",
        "optitrack_path": os.path.normpath(
            "OptiTrack_Recordings/Participant_13_left_post/Session_post/"
            "Position_1/Height_Joint-Level/trial_1_optitrack.csv"),
    }]
    monkeypatch.setattr(rpc.imu_discovery, "discover_trials", lambda: fake_trials)
    result = rpc.discover_imu_trials()
    assert len(result) == 1
    rec = result[0]
    assert rec["participant"] == "13" and rec["leg"] == "left"
    assert rec["position"] == "1" and rec["height"] == "joint-level"
    assert rec["imu_component_paths"]["accel"] == "x_accel.csv"
    assert rec["optitrack_path"] is not None
    assert "trial_key" in rec


def test_discover_imu_trials_unparseable_path_excluded(monkeypatch):
    fake_trials = [{
        "participant": "Participant_9", "position": "unknown", "trial": "Trial_1",
        "imu": os.path.normpath("Recordings/Participant_9/Trial_1_imu.csv"),  # no leg token
        "accel": "a", "gyro": "g", "mag": "m", "optitrack_path": None,
    }]
    monkeypatch.setattr(rpc.imu_discovery, "discover_trials", lambda: fake_trials)
    result = rpc.discover_imu_trials()
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k discover_imu_trials -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'discover_imu_trials'`

- [ ] **Step 3: Implement**

Add near the top of `rmse_pipeline_common.py` (with the other imports):

```python
import batch_imu_vs_optitrack_rmse as imu_discovery
```

Append:

```python
def discover_imu_trials():
    """Every IMU trial with matched split-CSV components, via
    batch_imu_vs_optitrack_rmse.discover_trials() (reused as-is -- its
    component-path derivation and OptiTrack matching are already correct
    and tested). Re-parsed through parse_structural_fields() for the
    canonical trial_key rather than trusting the source script's own
    participant/position labels, which don't capture session/height."""
    out = []
    for t in imu_discovery.discover_trials():
        fields = parse_structural_fields(t["imu"], REC_ROOT)
        if fields is None:
            continue
        out.append({
            **fields,
            "trial_key": compute_trial_key(fields),
            "imu_anchor_path": t["imu"],
            "imu_component_paths": {"imu": t["imu"], "accel": t["accel"],
                                    "gyro": t["gyro"], "mag": t["mag"]},
            "optitrack_path": t["optitrack_path"],
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k discover_imu_trials -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add IMU trial discovery wrapping batch_imu_vs_optitrack_rmse"
```

---

### Task 3: Video/MediaPipe trial discovery (generalized, not P14-only)

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: `parse_structural_fields`, `compute_trial_key` (Task 1)
- Produces: `rmse_pipeline_common.discover_video_trials() -> list[dict]` — each dict has
  `trial_key`, the 7 structural fields, `video_path`, `optitrack_path`

**Design note:** `sweep_mediapipe_config.discover_p14_trials()` is hardcoded to Participant 14's
exact folder layout (spec §2's correction — this is real generalization work, not a parameter
swap). `batch_mediapipe.discover_new_trials()` already generalizes this correctly for its own
purpose (walks every `*_optitrack.csv` under `OPTI_ROOT`, checks 8 candidate video filenames in
both the OptiTrack-side and Recordings-side mirrored directory) but yields CSV/annotated-video
existence flags irrelevant here and has print() side effects meant for its own batch pipeline.
This task reimplements just the video-candidate-matching convention (credited from
`batch_mediapipe.discover_new_trials`), not the whole generator.

- [ ] **Step 1: Write the failing tests**

```python
# ── discover_video_trials ────────────────────────────────────────────────

def test_discover_video_trials_finds_matching_video(tmp_path, monkeypatch):
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    opti_dir = opti_root / "Participant_14" / "Left" / "pre"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_3_optitrack.csv").write_text("t,angle\n", encoding="utf-8")
    video_dir = rec_root / "Participant_14" / "Left" / "pre"
    video_dir.mkdir(parents=True)
    (video_dir / "Trial_3.avi").write_bytes(b"fake video")

    monkeypatch.setattr(rpc, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(rpc, "REC_ROOT", str(rec_root))
    result = rpc.discover_video_trials()
    assert len(result) == 1
    rec = result[0]
    assert rec["participant"] == "14" and rec["leg"] == "left"
    assert rec["trial_number"] == "3"
    assert rec["video_path"] == str(video_dir / "Trial_3.avi")
    assert rec["optitrack_path"] == str(opti_dir / "trial_3_optitrack.csv")


def test_discover_video_trials_no_video_excluded(tmp_path, monkeypatch):
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    opti_dir = opti_root / "Participant_9" / "right" / "pre"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_1_optitrack.csv").write_text("t,angle\n", encoding="utf-8")
    rec_root.mkdir(parents=True)

    monkeypatch.setattr(rpc, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(rpc, "REC_ROOT", str(rec_root))
    assert rpc.discover_video_trials() == []


def test_discover_video_trials_checks_opti_side_video_too(tmp_path, monkeypatch):
    # batch_mediapipe.discover_new_trials's convention: the video may sit
    # alongside the OptiTrack CSV itself, not only under the mirrored
    # Recordings/ tree.
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    opti_dir = opti_root / "Participant_6" / "left" / "post"
    opti_dir.mkdir(parents=True)
    (opti_dir / "trial_2_optitrack.csv").write_text("t,angle\n", encoding="utf-8")
    (opti_dir / "Trial_2.mp4").write_bytes(b"fake video")
    rec_root.mkdir(parents=True)

    monkeypatch.setattr(rpc, "OPTI_ROOT", str(opti_root))
    monkeypatch.setattr(rpc, "REC_ROOT", str(rec_root))
    result = rpc.discover_video_trials()
    assert len(result) == 1
    assert result[0]["video_path"] == str(opti_dir / "Trial_2.mp4")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k discover_video_trials -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'discover_video_trials'`

- [ ] **Step 3: Implement**

Add `import glob` to the imports. Append:

```python
def discover_video_trials():
    """Every trial with an OptiTrack CSV and a matching video, walking
    OPTI_ROOT the same way batch_mediapipe.discover_new_trials() does
    (credited convention, not a call into that generator -- its
    CSV/annotated-video existence flags and print() side effects are
    specific to its own batch-processing pipeline, not relevant here).
    Video may sit beside the OptiTrack CSV itself, or under the mirrored
    Recordings/ tree -- both are checked, matching the real observed
    layout variance across participants."""
    out = []
    pattern = os.path.join(OPTI_ROOT, "**", "*_optitrack.csv")
    for opti_path in sorted(glob.glob(pattern, recursive=True)):
        m = re.match(r"trial_(\d+)_optitrack\.csv", os.path.basename(opti_path), re.I)
        if not m:
            continue
        trial_n = m.group(1)
        opti_dir = os.path.dirname(opti_path)
        rel = os.path.relpath(opti_dir, OPTI_ROOT)
        rec_dir = os.path.join(REC_ROOT, rel)
        candidates = [
            os.path.join(opti_dir, f"trial_{trial_n}.mp4"),
            os.path.join(opti_dir, f"Trial_{trial_n}.mp4"),
            os.path.join(opti_dir, f"trial_{trial_n}.avi"),
            os.path.join(opti_dir, f"Trial_{trial_n}.avi"),
            os.path.join(rec_dir, f"trial_{trial_n}.mp4"),
            os.path.join(rec_dir, f"Trial_{trial_n}.mp4"),
            os.path.join(rec_dir, f"trial_{trial_n}.avi"),
            os.path.join(rec_dir, f"Trial_{trial_n}.avi"),
        ]
        video_path = next((p for p in candidates if os.path.isfile(p)), None)
        if video_path is None:
            continue

        fields = parse_structural_fields(opti_path, OPTI_ROOT)
        if fields is None:
            continue
        out.append({
            **fields,
            "trial_key": compute_trial_key(fields),
            "video_path": video_path,
            "optitrack_path": opti_path,
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k discover_video_trials -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add generalized video/MediaPipe trial discovery"
```

---

### Task 4: Merge into `TrialRecord`s with capability flags, ambiguity exclusion, and the
shared exclusion registry

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: `discover_imu_trials`, `discover_video_trials` (Tasks 2-3);
  `pt_report_common.load_excluded_trials()`, `pt_report_common.trial_key()` (existing, Global
  Constraints)
- Produces: `rmse_pipeline_common.discover_scorable_trials() -> list[dict]` — the `TrialRecord`
  list from design spec §4

- [ ] **Step 1: Write the failing tests**

```python
# ── discover_scorable_trials ─────────────────────────────────────────────

def _imu_trial(trial_key="k1", optitrack_path="opti.csv", **overrides):
    base = {"trial_key": trial_key, "participant": "13", "leg": "left",
           "condition": "post", "session": "post", "position": "1",
           "height": "joint-level", "trial_number": "1",
           "imu_anchor_path": "anchor.csv",
           "imu_component_paths": {"imu": "a", "accel": "b", "gyro": "c", "mag": "d"},
           "optitrack_path": optitrack_path}
    base.update(overrides)
    return base


def _video_trial(trial_key="k1", optitrack_path="opti.csv", **overrides):
    base = {"trial_key": trial_key, "participant": "13", "leg": "left",
           "condition": "post", "session": "post", "position": "1",
           "height": "joint-level", "trial_number": "1",
           "video_path": "vid.avi", "optitrack_path": optitrack_path}
    base.update(overrides)
    return base


def test_discover_scorable_trials_merges_by_trial_key(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [_video_trial()])
    result = rpc.discover_scorable_trials()
    assert len(result) == 1
    rec = result[0]
    assert rec["has_imu_rmse"] is True
    assert rec["has_mediapipe_rmse"] is True
    assert rec["exclusion_reasons"] == []


def test_discover_scorable_trials_imu_only_capability(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    result = rpc.discover_scorable_trials()
    assert result[0]["has_imu_rmse"] is True
    assert result[0]["has_mediapipe_rmse"] is False
    assert result[0]["video_path"] is None


def test_discover_scorable_trials_no_optitrack_excluded(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial(optitrack_path=None)])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    assert rpc.discover_scorable_trials() == []


def test_discover_scorable_trials_conflicting_optitrack_path_excluded_as_ambiguous(monkeypatch):
    # Same trial_key from both sides, but disagreeing on which OptiTrack
    # file it maps to -- design spec §4: never heuristically resolved,
    # excluded instead.
    monkeypatch.setattr(rpc, "discover_imu_trials",
                        lambda: [_imu_trial(optitrack_path="opti_A.csv")])
    monkeypatch.setattr(rpc, "discover_video_trials",
                        lambda: [_video_trial(optitrack_path="opti_B.csv")])
    result = rpc.discover_scorable_trials()
    assert result == []


# ── excluded_trials.json filtering (Global Constraints -- added after the
# post-plan Codex consult found this repo's shared exclusion registry) ────

def test_discover_scorable_trials_filters_excluded_trial(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    # _imu_trial()'s defaults are participant=13, leg=left, condition=post,
    # trial_number=1 -- the legacy key pt_report_common.trial_key builds
    # from those same fields.
    legacy_key = "13_left_post_T1"
    monkeypatch.setattr(rpc.pt_report_common, "load_excluded_trials",
                        lambda: {legacy_key: "operator-confirmed: active swing"})
    assert rpc.discover_scorable_trials() == []


def test_discover_scorable_trials_keeps_non_excluded_trial(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    monkeypatch.setattr(rpc.pt_report_common, "load_excluded_trials",
                        lambda: {"99_right_pre_T9": "unrelated trial"})
    result = rpc.discover_scorable_trials()
    assert len(result) == 1


def test_discover_scorable_trials_empty_registry_excludes_nothing(monkeypatch):
    monkeypatch.setattr(rpc, "discover_imu_trials", lambda: [_imu_trial()])
    monkeypatch.setattr(rpc, "discover_video_trials", lambda: [])
    monkeypatch.setattr(rpc.pt_report_common, "load_excluded_trials", lambda: {})
    result = rpc.discover_scorable_trials()
    assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k discover_scorable_trials -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'discover_scorable_trials'`

- [ ] **Step 3: Implement**

Append:

```python
def discover_scorable_trials():
    """Merge discover_imu_trials()/discover_video_trials() by trial_key
    into TrialRecords with per-methodology capability flags (design spec
    §4). A trial_key with no optitrack_path on the side(s) that produced
    it, or with disagreeing optitrack_path values across sides, is
    excluded rather than heuristically resolved -- a silent wrong pairing
    is worse than a skipped trial."""
    by_key = {}
    for imu in discover_imu_trials():
        if not imu["optitrack_path"]:
            continue
        rec = by_key.setdefault(imu["trial_key"], {
            **{k: imu[k] for k in _TRIAL_KEY_FIELDS},
            "trial_key": imu["trial_key"], "optitrack_path": imu["optitrack_path"],
            "imu_anchor_path": None, "imu_component_paths": None, "video_path": None,
            "has_imu_rmse": False, "has_mediapipe_rmse": False, "exclusion_reasons": [],
        })
        if rec["optitrack_path"] != imu["optitrack_path"]:
            rec["exclusion_reasons"].append("conflicting_optitrack_path")
            continue
        rec["imu_anchor_path"] = imu["imu_anchor_path"]
        rec["imu_component_paths"] = imu["imu_component_paths"]
        rec["has_imu_rmse"] = True

    for vid in discover_video_trials():
        if not vid["optitrack_path"]:
            continue
        rec = by_key.setdefault(vid["trial_key"], {
            **{k: vid[k] for k in _TRIAL_KEY_FIELDS},
            "trial_key": vid["trial_key"], "optitrack_path": vid["optitrack_path"],
            "imu_anchor_path": None, "imu_component_paths": None, "video_path": None,
            "has_imu_rmse": False, "has_mediapipe_rmse": False, "exclusion_reasons": [],
        })
        if rec["optitrack_path"] != vid["optitrack_path"]:
            rec["exclusion_reasons"].append("conflicting_optitrack_path")
            continue
        rec["video_path"] = vid["video_path"]
        rec["has_mediapipe_rmse"] = True

    excluded = pt_report_common.load_excluded_trials()
    kept = []
    for rec in by_key.values():
        if rec["exclusion_reasons"]:
            continue
        legacy_key = pt_report_common.trial_key(
            rec["participant"], rec["leg"], rec["condition"], rec["trial_number"])
        if legacy_key in excluded:
            continue
        kept.append(rec)
    return kept
```

Add `import pt_report_common` to the module's imports (alongside the other repo-local imports).
`pt_report_common.load_excluded_trials()`/`trial_key()` are the shared exclusion-registry
functions (Global Constraints) -- always called live, never cached across `discover_scorable_
trials()` calls, so a hand-edit to `excluded_trials.json` takes effect on the next call without
restarting anything.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k discover_scorable_trials -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: merge IMU/video discovery into TrialRecords, filter excluded_trials.json"
```

---

### Task 5: Content fingerprints (input + implementation)

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Produces: `rmse_pipeline_common.sha256_file(path: str, stat_cache: dict, force: bool = False) -> str`,
  `rmse_pipeline_common.compute_input_fingerprints(trial: dict, methodology: str, stat_cache: dict, force: bool = False) -> dict`,
  `rmse_pipeline_common.compute_implementation_fingerprint() -> str`

- [ ] **Step 1: Write the failing tests**

```python
# ── sha256_file / fingerprints ───────────────────────────────────────────

def test_sha256_file_deterministic(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    h1 = rpc.sha256_file(str(f), cache)
    h2 = rpc.sha256_file(str(f), cache)
    assert h1 == h2 and len(h1) == 64


def test_sha256_file_reuses_cache_when_stat_unchanged(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    h1 = rpc.sha256_file(str(f), cache)
    # Overwrite with different content but don't touch the cache -- since
    # sha256_file only re-hashes when stat (size/mtime) changes, and we're
    # not asserting content correctness here, just that the cache path is
    # taken (returns the same digest without re-reading).
    stat_key = list(cache.keys())[0]
    cache[stat_key] = (cache[stat_key][0], "STALE_DIGEST_MARKER")
    h2 = rpc.sha256_file(str(f), cache)
    assert h2 == "STALE_DIGEST_MARKER"


def test_sha256_file_force_bypasses_cache(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    rpc.sha256_file(str(f), cache)
    stat_key = list(cache.keys())[0]
    cache[stat_key] = (cache[stat_key][0], "STALE_DIGEST_MARKER")
    h = rpc.sha256_file(str(f), cache, force=True)
    assert h != "STALE_DIGEST_MARKER"


def test_sha256_file_rehashes_when_stat_changes(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("hello", encoding="utf-8")
    cache = {}
    h1 = rpc.sha256_file(str(f), cache)
    f.write_text("hello world, much longer content now", encoding="utf-8")
    h2 = rpc.sha256_file(str(f), cache)
    assert h1 != h2


def test_compute_input_fingerprints_imu(tmp_path):
    paths = {}
    for name in ("imu", "accel", "gyro", "mag"):
        p = tmp_path / f"{name}.csv"
        p.write_text(name, encoding="utf-8")
        paths[name] = str(p)
    opti = tmp_path / "opti.csv"
    opti.write_text("opti", encoding="utf-8")
    trial = {"imu_component_paths": paths, "optitrack_path": str(opti), "video_path": None}
    fps = rpc.compute_input_fingerprints(trial, "imu", {})
    assert set(fps["imu"].keys()) == {"imu", "accel", "gyro", "mag"}
    assert "optitrack" in fps
    assert "video" not in fps


def test_compute_input_fingerprints_mediapipe(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    opti = tmp_path / "opti.csv"
    opti.write_text("opti", encoding="utf-8")
    trial = {"imu_component_paths": None, "optitrack_path": str(opti), "video_path": str(video)}
    fps = rpc.compute_input_fingerprints(trial, "mediapipe", {})
    assert "video" in fps and "optitrack" in fps
    assert "imu" not in fps


def test_compute_implementation_fingerprint_stable():
    assert rpc.compute_implementation_fingerprint() == rpc.compute_implementation_fingerprint()


def test_compute_implementation_fingerprint_changes_with_grid(monkeypatch):
    fp1 = rpc.compute_implementation_fingerprint()
    import sweep_imu_config
    monkeypatch.setattr(sweep_imu_config, "WIDE_GRID", [{"beta": 0.99}])
    fp2 = rpc.compute_implementation_fingerprint()
    assert fp1 != fp2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "sha256_file or fingerprint" -v`
Expected: FAIL — `AttributeError` on each missing function

- [ ] **Step 3: Implement**

Add imports: `import inspect`, `import sys`, `import numpy`, `import scipy`, `import cv2`,
`import mediapipe`. Append:

```python
def sha256_file(path, stat_cache, force=False):
    """Content hash of one file, gated by a size/mtime pre-filter --
    unchanged stat reuses the cached digest from a prior call within the
    same stat_cache dict, no re-read. force=True always re-hashes,
    bypassing the pre-filter entirely (needed by the watcher plan's
    reconciliation pass, which is the correctness safety net and must not
    share this speed optimization's blind spot -- design spec §7.1's
    third-round correction)."""
    st = os.stat(path)
    stat_key = (path, st.st_size, st.st_mtime_ns)
    if not force and stat_key in stat_cache:
        return stat_cache[stat_key][1]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    stat_cache[stat_key] = (stat_key, digest)
    return digest


def compute_input_fingerprints(trial, methodology, stat_cache, force=False):
    """Per design spec §7.1: every input file a candidate's score actually
    depends on, for the given methodology. optitrack is always included;
    imu's four split CSVs are included for methodology="imu", the video
    for methodology="mediapipe"."""
    fps = {"optitrack": sha256_file(trial["optitrack_path"], stat_cache, force=force)}
    if methodology == "imu":
        fps["imu"] = {name: sha256_file(p, stat_cache, force=force)
                      for name, p in trial["imu_component_paths"].items()}
    elif methodology == "mediapipe":
        fps["video"] = sha256_file(trial["video_path"], stat_cache, force=force)
    else:
        raise ValueError(f"unknown methodology: {methodology!r}")
    return fps


_FINGERPRINTED_MODULES = ("rmse_pipeline_common", "workbench_engine",
                          "imu_calibration_tuner", "reconstruct_imu_raw_logs",
                          "sweep_imu_config", "sweep_mediapipe_config", "batch_mediapipe")


def compute_implementation_fingerprint():
    """Hash of everything that can silently change a candidate's score
    without touching any trial's input files: both grids (imported live,
    per Global Constraints), the source of every module this pipeline's
    scoring path depends on, and the installed numpy/scipy/opencv/
    mediapipe package versions (design spec §7.1)."""
    import sweep_imu_config
    import sweep_mediapipe_config

    parts = [
        json.dumps(sweep_imu_config.WIDE_GRID, sort_keys=True),
        json.dumps({"model_variants": sweep_mediapipe_config.MODEL_VARIANTS,
                    "vis_thresh": sweep_mediapipe_config.VIS_THRESH_CANDIDATES},
                   sort_keys=True),
    ]
    for mod_name in _FINGERPRINTED_MODULES:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        try:
            parts.append(inspect.getsource(mod))
        except (OSError, TypeError):
            pass
    parts.append(f"numpy={numpy.__version__}")
    parts.append(f"scipy={scipy.__version__}")
    parts.append(f"opencv={cv2.__version__}")
    parts.append(f"mediapipe={mediapipe.__version__}")

    blob = "\x00".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "sha256_file or fingerprint" -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add content-hash input and implementation fingerprints"
```

---

### Task 6: IMU candidate scoring

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: `reconstruct_imu_raw_logs.reconstruct_trial`, `imu_calibration_tuner.replay_trial`,
  `workbench_engine.compare_pair`, `pendulastic_pt_score.load_optitrack` (existing)
- Produces: `rmse_pipeline_common.score_imu_candidate(trial: dict, params: dict) -> float | None`

- [ ] **Step 1: Write the failing tests**

```python
# ── score_imu_candidate ──────────────────────────────────────────────────

def test_score_imu_candidate_returns_rmse(monkeypatch):
    trial = {"imu_component_paths": {"imu": "i", "accel": "a", "gyro": "g", "mag": "m"},
            "optitrack_path": "o"}
    monkeypatch.setattr(rpc, "reconstruct_trial", lambda a, g, m: [{"t": 0.0}])
    monkeypatch.setattr(rpc.imu_calibration_tuner, "replay_trial",
                        lambda samples, params: (
                            __import__("numpy").array([0.0, 0.1]),
                            __import__("numpy").array([1.0, 2.0])))
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (__import__("numpy").array([0.0, 0.1]),
                                      __import__("numpy").array([1.0, 2.0])))
    monkeypatch.setattr(rpc.engine, "compare_pair",
                        lambda *a, **k: {"status": "ok", "rmse_deg": 3.5, "n_samples": 20})
    result = rpc.score_imu_candidate(trial, {"beta": 0.041})
    assert result == 3.5


def test_score_imu_candidate_returns_none_when_too_few_finite_samples(monkeypatch):
    import numpy as np
    trial = {"imu_component_paths": {"imu": "i", "accel": "a", "gyro": "g", "mag": "m"},
            "optitrack_path": "o"}
    monkeypatch.setattr(rpc, "reconstruct_trial", lambda a, g, m: [{"t": 0.0}])
    monkeypatch.setattr(rpc.imu_calibration_tuner, "replay_trial",
                        lambda samples, params: (np.array([0.0]), np.array([float("nan")])))
    result = rpc.score_imu_candidate(trial, {"beta": 0.041})
    assert result is None


def test_score_imu_candidate_returns_none_on_compare_pair_error(monkeypatch):
    import numpy as np
    trial = {"imu_component_paths": {"imu": "i", "accel": "a", "gyro": "g", "mag": "m"},
            "optitrack_path": "o"}
    monkeypatch.setattr(rpc, "reconstruct_trial", lambda a, g, m: [{"t": 0.0}])
    monkeypatch.setattr(rpc.imu_calibration_tuner, "replay_trial",
                        lambda samples, params: (
                            np.array([0.0] * 20), np.array([1.0] * 20)))
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (np.array([0.0] * 20), np.array([1.0] * 20)))
    monkeypatch.setattr(rpc.engine, "compare_pair",
                        lambda *a, **k: {"status": "error", "error": "no overlap"})
    result = rpc.score_imu_candidate(trial, {"beta": 0.041})
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k score_imu_candidate -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement**

Add imports:

```python
import numpy as np

import imu_calibration_tuner
import pendulastic_pt_score as pt_score
import workbench_engine as engine
from reconstruct_imu_raw_logs import reconstruct_trial
```

Append:

```python
def score_imu_candidate(trial, params):
    """RMSE-vs-OptiTrack for one IMU candidate config on one trial. Reuses
    reconstruct_imu_raw_logs.reconstruct_trial() to build the raw sample
    stream, imu_calibration_tuner.replay_trial() to run the AHRS/fusion
    candidate, and workbench_engine.compare_pair() to score -- the same
    pipeline sweep_imu_config.py's score_config() already uses per-trial
    (design spec §5). Returns None if fewer than 10 finite angle samples
    result (unscoreable, matching sweep_imu_config.py's own threshold) or
    if compare_pair reports a non-ok status."""
    comp = trial["imu_component_paths"]
    samples = reconstruct_trial(comp["accel"], comp["gyro"], comp["mag"])
    if not samples:
        return None
    t_m, ang_m = imu_calibration_tuner.replay_trial(samples, params)
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    opti_t, opti_ang = pt_score.load_optitrack(trial["optitrack_path"])
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    if result.get("status") != "ok":
        return None
    return result["rmse_deg"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k score_imu_candidate -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add IMU candidate RMSE scoring"
```

---

### Task 7: MediaPipe landmark cache and candidate scoring

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: `sweep_mediapipe_config.extract_raw_landmarks`, `sweep_mediapipe_config.angles_from_raw`,
  `workbench_engine.compare_pair`, `compute_implementation_fingerprint` (Task 5)
- Produces: `rmse_pipeline_common.extract_landmarks_cached(trial: dict, model_variant: str, model_path: str) -> list[dict]`,
  `rmse_pipeline_common.score_mediapipe_candidate(trial: dict, model_variant: str, model_path: str, vis_thresh: float) -> float | None`

- [ ] **Step 1: Write the failing tests**

```python
# ── extract_landmarks_cached / score_mediapipe_candidate ────────────────

def test_extract_landmarks_cached_calls_extraction_once(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(rpc.mediapipe_sweep, "extract_raw_landmarks",
                        lambda vp, leg, mp_: (calls.append(1) or [{"t": 0.0}]))
    trial = {"trial_key": "k1", "leg": "left", "video_path": str(video)}
    rpc.extract_landmarks_cached(trial, "full", "model.task")
    rpc.extract_landmarks_cached(trial, "full", "model.task")
    assert len(calls) == 1


def test_extract_landmarks_cached_re_extracts_on_video_change(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"fake")
    calls = []
    monkeypatch.setattr(rpc.mediapipe_sweep, "extract_raw_landmarks",
                        lambda vp, leg, mp_: (calls.append(1) or [{"t": 0.0}]))
    trial = {"trial_key": "k1", "leg": "left", "video_path": str(video)}
    rpc.extract_landmarks_cached(trial, "full", "model.task")
    video.write_bytes(b"different content, changes the hash")
    rpc.extract_landmarks_cached(trial, "full", "model.task")
    assert len(calls) == 2


def test_score_mediapipe_candidate_returns_rmse(monkeypatch):
    trial = {"trial_key": "k1", "leg": "left", "video_path": "v.mp4", "optitrack_path": "o.csv"}
    monkeypatch.setattr(rpc, "extract_landmarks_cached", lambda t, mv, mp_: [{"t": 0.0}])
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (np.array([0.0]), np.array([1.0])))
    monkeypatch.setattr(rpc.mediapipe_sweep, "score_frames",
                        lambda frames, opti_t, opti_ang, vis_thresh: 4.2)
    result = rpc.score_mediapipe_candidate(trial, "full", "model.task", 0.4)
    assert result == 4.2


def test_score_mediapipe_candidate_returns_none_when_unscoreable(monkeypatch):
    trial = {"trial_key": "k1", "leg": "left", "video_path": "v.mp4", "optitrack_path": "o.csv"}
    monkeypatch.setattr(rpc, "extract_landmarks_cached", lambda t, mv, mp_: [{"t": 0.0}])
    monkeypatch.setattr(rpc.pt_score, "load_optitrack",
                        lambda path: (np.array([0.0]), np.array([1.0])))
    monkeypatch.setattr(rpc.mediapipe_sweep, "score_frames",
                        lambda frames, opti_t, opti_ang, vis_thresh: None)
    result = rpc.score_mediapipe_candidate(trial, "full", "model.task", 0.4)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "extract_landmarks_cached or score_mediapipe_candidate" -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement**

Add `import pickle` to imports and `import sweep_mediapipe_config as mediapipe_sweep`. Append:

```python
_LANDMARK_CACHE_DIR = lambda: os.path.join(SWEEP_CACHE_DIR, "landmarks")


def extract_landmarks_cached(trial, model_variant, model_path):
    """Raw per-frame landmark extraction, cached separately from the
    per-config RMSE cache (design spec §7.1, added in the second Codex
    review round) -- a per-(trial, full-config) RMSE cache alone would
    re-run MediaPipe inference every time vis_thresh changes even though
    only the cheap re-thresholding step actually depends on it. Cache key:
    (trial_key, model_variant, video content hash)."""
    stat_cache = {}
    video_fp = sha256_file(trial["video_path"], stat_cache)
    cache_dir = _LANDMARK_CACHE_DIR()
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(
        cache_dir, f"{trial['trial_key']}_{model_variant}_{video_fp}.pkl")
    if os.path.isfile(cache_file):
        with open(cache_file, "rb") as f:
            return pickle.load(f)
    frames = mediapipe_sweep.extract_raw_landmarks(trial["video_path"], trial["leg"], model_path)
    tmp_path = cache_file + ".tmp"
    with open(tmp_path, "wb") as f:
        pickle.dump(frames, f)
    os.replace(tmp_path, cache_file)
    return frames


def score_mediapipe_candidate(trial, model_variant, model_path, vis_thresh):
    """RMSE-vs-OptiTrack for one MediaPipe candidate (model_variant,
    vis_thresh) on one trial. Landmark extraction is cached and reused
    across every vis_thresh candidate for the same (trial, model_variant)
    -- only workbench_engine.compare_pair's cheap re-thresholding runs per
    candidate (design spec §5, §7.1)."""
    frames = extract_landmarks_cached(trial, model_variant, model_path)
    opti_t, opti_ang = pt_score.load_optitrack(trial["optitrack_path"])
    return mediapipe_sweep.score_frames(frames, opti_t, opti_ang, vis_thresh)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "extract_landmarks_cached or score_mediapipe_candidate" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add MediaPipe landmark caching and candidate scoring"
```

---

### Task 8: `sweep_cache/` RMSE manifest

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: `compute_input_fingerprints`, `compute_implementation_fingerprint` (Task 5)
- Produces: `rmse_pipeline_common.compute_cache_key(methodology: str, trial: dict, candidate: dict, input_fps: dict, impl_fp: str) -> str`,
  `rmse_pipeline_common.load_sweep_cache() -> dict`, `rmse_pipeline_common.save_sweep_cache(cache: dict) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# ── cache key + manifest persistence ─────────────────────────────────────

def test_compute_cache_key_deterministic():
    trial = {"trial_key": "k1"}
    candidate = {"beta": 0.041}
    key1 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl1")
    key2 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl1")
    assert key1 == key2


def test_compute_cache_key_differs_on_implementation_fingerprint():
    trial = {"trial_key": "k1"}
    candidate = {"beta": 0.041}
    key1 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl1")
    key2 = rpc.compute_cache_key("imu", trial, candidate, {"optitrack": "h1"}, "impl2")
    assert key1 != key2


def test_compute_cache_key_differs_on_candidate():
    trial = {"trial_key": "k1"}
    key1 = rpc.compute_cache_key("imu", trial, {"beta": 0.041}, {"optitrack": "h1"}, "impl1")
    key2 = rpc.compute_cache_key("imu", trial, {"beta": 0.08}, {"optitrack": "h1"}, "impl1")
    assert key1 != key2


def test_save_and_load_sweep_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    rpc.save_sweep_cache({"key1": 3.5, "key2": 4.1})
    assert rpc.load_sweep_cache() == {"key1": 3.5, "key2": 4.1}


def test_load_sweep_cache_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "does_not_exist"))
    assert rpc.load_sweep_cache() == {}


def test_load_sweep_cache_malformed_json_treated_as_empty(tmp_path, monkeypatch, capsys):
    cache_dir = tmp_path / "sweep_cache"
    cache_dir.mkdir()
    (cache_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(cache_dir))
    assert rpc.load_sweep_cache() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "cache_key or sweep_cache" -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement**

Append:

```python
def compute_cache_key(methodology, trial, candidate, input_fingerprints, implementation_fingerprint):
    """Design spec §7.1: content-addressed, not size/mtime -- depends on
    the trial's identity, the exact candidate config, every input file's
    current content, and the current implementation fingerprint, so a code
    fix or grid change naturally misses cache rather than silently serving
    a stale result."""
    canonical = {
        "schema": 2, "methodology": methodology, "trial_key": trial["trial_key"],
        "candidate": candidate, "input_fingerprints": input_fingerprints,
        "implementation_fingerprint": implementation_fingerprint,
    }
    blob = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _sweep_cache_manifest_path():
    return os.path.join(SWEEP_CACHE_DIR, "manifest.json")


def load_sweep_cache():
    """{cache_key: rmse_deg} manifest. Missing or malformed file -> empty
    dict (defensive pattern matching pt_cohort_common.load_registry() --
    this is a file a human could plausibly delete or hand-edit)."""
    path = _sweep_cache_manifest_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{path} failed to parse -- treating as empty.")
        return {}


def save_sweep_cache(cache):
    os.makedirs(SWEEP_CACHE_DIR, exist_ok=True)
    tmp_path = _sweep_cache_manifest_path() + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp_path, _sweep_cache_manifest_path())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "cache_key or sweep_cache" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add content-addressed sweep_cache manifest"
```

---

### Task 9: Frozen ranking cohort and coverage rule

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Produces: `rmse_pipeline_common.rank_candidates(candidate_scores: dict[tuple, dict[str, float]], min_coverage_fraction: float = 0.8, min_participants: int = 3) -> list[dict]`
  — `candidate_scores` maps a JSON-stable candidate key to `{trial_key: rmse_deg}` for every
  trial where that candidate scored successfully; participant counts are derived from a
  `trial_key -> participant` map passed alongside.

- [ ] **Step 1: Write the failing tests**

```python
# ── rank_candidates ───────────────────────────────────────────────────────

def _cohort_and_participants(n_trials=5, n_participants=3):
    cohort = [f"t{i}" for i in range(n_trials)]
    # distribute trials across participants round-robin
    participant_of = {t: f"p{i % n_participants}" for i, t in enumerate(cohort)}
    return cohort, participant_of


def test_rank_candidates_full_coverage_wins_lower_median():
    cohort, participant_of = _cohort_and_participants()
    scores = {
        '{"beta": 0.041}': {t: 5.0 for t in cohort},
        '{"beta": 0.08}': {t: 2.0 for t in cohort},
    }
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    assert ranked[0]["candidate_key"] == '{"beta": 0.08}'
    assert ranked[0]["median_rmse"] == 2.0
    assert ranked[0]["low_coverage"] is False


def test_rank_candidates_excludes_candidate_missing_required_cohort_trial():
    cohort, participant_of = _cohort_and_participants(n_trials=5, n_participants=3)
    scores = {
        # scores only 3 of 5 cohort trials (60% < 80% floor) but with a
        # very low RMSE on those -- must not win by having an easier
        # denominator (design spec §7.2).
        '{"beta": 0.01}': {cohort[0]: 0.1, cohort[1]: 0.1, cohort[2]: 0.1},
        '{"beta": 0.08}': {t: 3.0 for t in cohort},
    }
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    winner = [r for r in ranked if not r["low_coverage"]]
    assert len(winner) == 1
    assert winner[0]["candidate_key"] == '{"beta": 0.08}'
    low_cov = [r for r in ranked if r["low_coverage"]]
    assert low_cov[0]["candidate_key"] == '{"beta": 0.01}'


def test_rank_candidates_reports_n_trials_and_n_participants():
    cohort, participant_of = _cohort_and_participants(n_trials=5, n_participants=3)
    scores = {'{"beta": 0.08}': {t: 3.0 for t in cohort}}
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    assert ranked[0]["n_trials"] == 5
    assert ranked[0]["n_participants"] == 3


def test_rank_candidates_cohort_below_minimum_participants_returns_empty():
    cohort, participant_of = _cohort_and_participants(n_trials=2, n_participants=2)
    scores = {'{"beta": 0.08}': {t: 3.0 for t in cohort}}
    ranked = rpc.rank_candidates(scores, cohort, participant_of)
    assert ranked == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k rank_candidates -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement**

Append:

```python
def rank_candidates(candidate_scores, cohort, participant_of,
                    min_coverage_fraction=0.8, min_participants=3):
    """Design spec §7.2: one frozen ranking cohort (every eligible trial
    for this methodology), every candidate scored against the same cohort.
    A candidate that didn't score a required cohort trial is marked
    low_coverage and reported but excluded from the winner (never
    aggregated over an easier subset -- this is what makes "same scored
    subset" literally true rather than aspirational, fixed after the
    second Codex review round caught the earlier version's
    self-contradiction). If the cohort itself has fewer than
    min_participants distinct participants, ranking is skipped for this
    sweep entirely (returns [])."""
    cohort_participants = {participant_of[t] for t in cohort}
    if len(cohort_participants) < min_participants:
        return []

    required_n = max(1, int(len(cohort) * min_coverage_fraction))
    rows = []
    for candidate_key, per_trial in candidate_scores.items():
        scored_in_cohort = [t for t in cohort if t in per_trial]
        n_trials = len(scored_in_cohort)
        n_participants = len({participant_of[t] for t in scored_in_cohort})
        low_coverage = n_trials < required_n or n_participants < min_participants
        median_rmse = (float(np.median([per_trial[t] for t in scored_in_cohort]))
                       if scored_in_cohort else None)
        rows.append({"candidate_key": candidate_key, "median_rmse": median_rmse,
                    "n_trials": n_trials, "n_participants": n_participants,
                    "low_coverage": low_coverage})

    winners = [r for r in rows if not r["low_coverage"]]
    winners.sort(key=lambda r: r["median_rmse"])
    losers = [r for r in rows if r["low_coverage"]]
    return winners + losers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k rank_candidates -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add frozen-cohort candidate ranking with coverage rule"
```

---

### Task 10: Best-config promotion with incumbent re-scoring

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: `rank_candidates` output shape (Task 9)
- Produces: `rmse_pipeline_common.load_best_config() -> dict`,
  `rmse_pipeline_common.record_sweep_result(methodology: str, ranked: list[dict], dataset_fingerprint: str, implementation_fingerprint: str, epsilon: float = 0.1) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# ── load_best_config / record_sweep_result ───────────────────────────────

def test_load_best_config_missing_file_returns_empty_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "missing.json"))
    cfg = rpc.load_best_config()
    assert cfg == {"mediapipe": None, "imu": None, "history": []}


def test_record_sweep_result_promotes_first_valid_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    ranked = [{"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
              "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    result = rpc.record_sweep_result("imu", ranked, "ds1", "impl1")
    assert result["promoted"] is True
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.041}'
    assert cfg["imu"]["rmse"] == 5.0
    assert len(cfg["history"]) == 1
    assert cfg["history"][0]["dataset_fingerprint"] == "ds1"
    assert cfg["history"][0]["implementation_fingerprint"] == "impl1"


def test_record_sweep_result_does_not_promote_within_epsilon(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    first = [{"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
             "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    rpc.record_sweep_result("imu", first, "ds1", "impl1")
    # Incumbent re-scored at 5.0 again, challenger only 0.05 better -- below
    # the default 0.1 epsilon, must not promote.
    second = [
        {"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
         "n_trials": 5, "n_participants": 3, "low_coverage": False},
        {"candidate_key": '{"beta": 0.08}', "median_rmse": 4.95,
         "n_trials": 5, "n_participants": 3, "low_coverage": False},
    ]
    result = rpc.record_sweep_result("imu", second, "ds2", "impl1")
    assert result["promoted"] is False
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.041}'
    assert len(cfg["history"]) == 1  # no new entry on a non-promotion


def test_record_sweep_result_incumbent_unrankable_promotes_best_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    first = [{"candidate_key": '{"beta": 0.041}', "median_rmse": 5.0,
             "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    rpc.record_sweep_result("imu", first, "ds1", "impl1")
    # Design spec §5's edge case: the incumbent's exact config is no longer
    # in this sweep's ranked results at all (e.g. dropped from a hand-edited
    # grid) -- must not keep the stale RMSE, must promote the best valid
    # candidate from this sweep instead.
    second = [{"candidate_key": '{"beta": 0.08}', "median_rmse": 6.0,
              "n_trials": 5, "n_participants": 3, "low_coverage": False}]
    result = rpc.record_sweep_result("imu", second, "ds2", "impl1")
    assert result["promoted"] is True
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.08}'


def test_record_sweep_result_no_valid_candidate_sets_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    ranked = [{"candidate_key": '{"beta": 0.041}', "median_rmse": None,
              "n_trials": 1, "n_participants": 1, "low_coverage": True}]
    result = rpc.record_sweep_result("imu", ranked, "ds1", "impl1")
    assert result["promoted"] is False
    cfg = rpc.load_best_config()
    assert cfg["imu"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "load_best_config or record_sweep_result" -v`
Expected: FAIL — `AttributeError`

- [ ] **Step 3: Implement**

Append:

```python
def load_best_config():
    """Missing/malformed file -> the empty structure, not an error --
    matches pt_cohort_common.load_registry()'s defensive pattern."""
    if not os.path.isfile(BEST_CONFIG_JSON):
        return {"mediapipe": None, "imu": None, "history": []}
    try:
        with open(BEST_CONFIG_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{BEST_CONFIG_JSON} failed to parse -- treating as empty.")
        return {"mediapipe": None, "imu": None, "history": []}
    cfg.setdefault("mediapipe", None)
    cfg.setdefault("imu", None)
    cfg.setdefault("history", [])
    return cfg


def _save_best_config(cfg):
    tmp_path = BEST_CONFIG_JSON + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp_path, BEST_CONFIG_JSON)


def record_sweep_result(methodology, ranked, dataset_fingerprint,
                        implementation_fingerprint, epsilon=0.1):
    """Design spec §5/§7.3: every sweep re-scores/re-ranks the incumbent on
    the SAME current cohort as every challenger (ranked already reflects
    this -- rank_candidates scores every candidate, including whatever
    config load_best_config() currently holds, against this sweep's
    cohort), so promotion is always apples-to-apples. epsilon is in
    absolute RMSE degrees (design spec §5).

    Edge case (design spec §5, third Codex review round): if the
    incumbent's exact config isn't present in `ranked` at all (e.g.
    dropped from a hand-edited grid), it's no longer rankable -- promote
    the best valid candidate from this sweep instead of keeping a stale,
    no-longer-comparable RMSE. If no candidate in `ranked` is valid
    (not low_coverage), current best becomes unavailable (None) rather
    than silently retaining an old number."""
    cfg = load_best_config()
    incumbent = cfg.get(methodology)
    valid = [r for r in ranked if not r["low_coverage"]]
    best_this_sweep = valid[0] if valid else None

    incumbent_still_ranked = None
    if incumbent is not None:
        incumbent_still_ranked = next(
            (r for r in ranked if r["candidate_key"] == incumbent["config"]
             and not r["low_coverage"]), None)

    promote = False
    if best_this_sweep is None:
        new_entry = None
    elif incumbent is None or incumbent_still_ranked is None:
        promote = True
        new_entry = best_this_sweep
    elif incumbent_still_ranked["median_rmse"] < best_this_sweep["median_rmse"] + epsilon:
        new_entry = None  # incumbent (re-scored) still wins or challenger's edge is within epsilon
    else:
        promote = True
        new_entry = best_this_sweep

    if best_this_sweep is None and incumbent is not None and incumbent_still_ranked is None:
        # No valid candidate this sweep AND the incumbent itself couldn't be
        # re-ranked -- current best becomes unavailable, not stale.
        cfg[methodology] = None
        _save_best_config(cfg)
        return {"promoted": False, "reason": "no_valid_candidate"}

    if promote:
        cfg[methodology] = {
            "config": new_entry["candidate_key"], "rmse": new_entry["median_rmse"],
            "n_trials": new_entry["n_trials"], "n_participants": new_entry["n_participants"],
        }
        cfg["history"].append({
            "methodology": methodology, "config": new_entry["candidate_key"],
            "rmse": new_entry["median_rmse"], "dataset_fingerprint": dataset_fingerprint,
            "implementation_fingerprint": implementation_fingerprint,
            "n_trials": new_entry["n_trials"], "n_participants": new_entry["n_participants"],
        })
        _save_best_config(cfg)
        return {"promoted": True}

    _save_best_config(cfg)
    return {"promoted": False, "reason": "within_epsilon"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k "load_best_config or record_sweep_result" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: add best-config promotion with incumbent re-scoring"
```

---

### Task 11: `run_full_sweep()` orchestration and outputs

**Files:**
- Modify: `rmse_pipeline_common.py`
- Test: `tests/test_rmse_pipeline_common.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 1-10
- Produces: `rmse_pipeline_common.run_full_sweep(priority_trial_keys=None) -> dict`

- [ ] **Step 1: Write the failing tests**

```python
# ── run_full_sweep orchestration ─────────────────────────────────────────

def _stub_pipeline(monkeypatch, tmp_path, trials, imu_rmse_by_config, mp_rmse_by_config):
    monkeypatch.setattr(rpc, "SWEEP_CACHE_DIR", str(tmp_path / "sweep_cache"))
    monkeypatch.setattr(rpc, "BEST_CONFIG_JSON", str(tmp_path / "best.json"))
    monkeypatch.setattr(rpc, "RMSE_TRACKING_DIR", str(tmp_path / "RMSE_Tracking"))
    monkeypatch.setattr(rpc, "discover_scorable_trials", lambda: trials)
    monkeypatch.setattr(rpc, "compute_implementation_fingerprint", lambda: "impl1")
    monkeypatch.setattr(rpc, "compute_input_fingerprints",
                        lambda trial, methodology, cache, force=False: {"optitrack": "h"})

    import sweep_imu_config
    import sweep_mediapipe_config
    monkeypatch.setattr(sweep_imu_config, "WIDE_GRID", [{"beta": 0.041}, {"beta": 0.08}])
    monkeypatch.setattr(sweep_mediapipe_config, "MODEL_VARIANTS", ["full"])
    monkeypatch.setattr(sweep_mediapipe_config, "VIS_THRESH_CANDIDATES", [0.4])

    def fake_score_imu(trial, params):
        return imu_rmse_by_config.get((trial["trial_key"], json.dumps(params, sort_keys=True)))
    monkeypatch.setattr(rpc, "score_imu_candidate", fake_score_imu)

    def fake_score_mp(trial, model_variant, model_path, vis_thresh):
        key = json.dumps({"model_variant": model_variant, "vis_thresh": vis_thresh}, sort_keys=True)
        return mp_rmse_by_config.get((trial["trial_key"], key))
    monkeypatch.setattr(rpc, "score_mediapipe_candidate", fake_score_mp)
    monkeypatch.setattr(rpc, "_make_figures", lambda *a, **k: None)


def _trial(key, participant, has_imu=True, has_mp=True):
    return {"trial_key": key, "participant": participant, "leg": "left",
           "condition": "post", "session": "post", "position": "1", "height": "none",
           "trial_number": "1", "imu_anchor_path": "a", "imu_component_paths": {"imu": "i"},
           "video_path": "v.mp4" if has_mp else None,
           "optitrack_path": "o.csv", "has_imu_rmse": has_imu, "has_mediapipe_rmse": has_mp,
           "exclusion_reasons": []}


def test_run_full_sweep_ranks_and_promotes(tmp_path, monkeypatch):
    trials = [_trial(f"k{i}", f"p{i % 3}") for i in range(5)]
    imu_scores = {}
    mp_scores = {}
    for t in trials:
        imu_scores[(t["trial_key"], '{"beta": 0.041}')] = 5.0
        imu_scores[(t["trial_key"], '{"beta": 0.08}')] = 3.0
        mp_scores[(t["trial_key"], '{"model_variant": "full", "vis_thresh": 0.4}')] = 6.0
    _stub_pipeline(monkeypatch, tmp_path, trials, imu_scores, mp_scores)

    result = rpc.run_full_sweep()

    assert result["imu"]["promoted"] is True
    assert result["mediapipe"]["promoted"] is True
    cfg = rpc.load_best_config()
    assert cfg["imu"]["config"] == '{"beta": 0.08}'
    assert os.path.isfile(os.path.join(str(tmp_path / "RMSE_Tracking"), "rmse_sweep_results.csv"))


def test_run_full_sweep_handles_no_trials(tmp_path, monkeypatch):
    _stub_pipeline(monkeypatch, tmp_path, [], {}, {})
    result = rpc.run_full_sweep()
    assert result["imu"]["promoted"] is False
    assert result["mediapipe"]["promoted"] is False


def test_run_full_sweep_isolates_per_trial_scoring_failure(tmp_path, monkeypatch, capsys):
    trials = [_trial(f"k{i}", f"p{i % 3}") for i in range(5)]
    imu_scores = {}
    mp_scores = {}
    for t in trials:
        imu_scores[(t["trial_key"], '{"beta": 0.08}')] = 3.0
        mp_scores[(t["trial_key"], '{"model_variant": "full", "vis_thresh": 0.4}')] = 6.0
    _stub_pipeline(monkeypatch, tmp_path, trials, imu_scores, mp_scores)

    def raising_score_imu(trial, params):
        if params == {"beta": 0.041}:
            raise ValueError("corrupt CSV")
        return imu_scores.get((trial["trial_key"], json.dumps(params, sort_keys=True)))
    monkeypatch.setattr(rpc, "score_imu_candidate", raising_score_imu)

    result = rpc.run_full_sweep()   # must not raise
    assert result["imu"]["promoted"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k run_full_sweep -v`
Expected: FAIL — `AttributeError: module 'rmse_pipeline_common' has no attribute 'run_full_sweep'`

- [ ] **Step 3: Implement**

Add `import csv` to imports. Append:

```python
def _score_grid(trials, has_flag, grid, score_fn, cache, methodology, model_path=None):
    """Score every (trial, candidate) pair in `grid` for trials with
    `has_flag` True, using sweep_cache when available. Returns
    {candidate_key: {trial_key: rmse}}. One failing trial/candidate is
    logged and skipped, never aborts the whole sweep (design spec §6/§10,
    matching run_pt_analysis.py's per-participant failure isolation)."""
    impl_fp = compute_implementation_fingerprint()
    stat_cache = {}
    results = {}
    for trial in trials:
        if not trial.get(has_flag):
            continue
        try:
            input_fps = compute_input_fingerprints(trial, methodology, stat_cache)
        except Exception as e:
            print(f"[rmse_pipeline_common] fingerprint failure for {trial['trial_key']}: {e}")
            continue
        for candidate in grid:
            candidate_key = json.dumps(candidate, sort_keys=True)
            cache_key = compute_cache_key(methodology, trial, candidate, input_fps, impl_fp)
            if cache_key in cache:
                rmse = cache[cache_key]
            else:
                try:
                    if methodology == "imu":
                        rmse = score_imu_candidate(trial, candidate)
                    else:
                        rmse = score_mediapipe_candidate(
                            trial, candidate["model_variant"], model_path, candidate["vis_thresh"])
                except Exception as e:
                    print(f"[rmse_pipeline_common] scoring failure for "
                         f"{trial['trial_key']} / {candidate_key}: {e}")
                    rmse = None
                if rmse is not None:
                    cache[cache_key] = rmse
            if rmse is not None:
                results.setdefault(candidate_key, {})[trial["trial_key"]] = rmse
    return results


def run_full_sweep(priority_trial_keys=None):
    """Design spec §5/§7: discover -> score both grids over the whole
    dataset (priority_trial_keys is an ordering/caching hint only, never a
    filter -- with or without it this returns the same ranking for the
    same underlying data) -> rank each methodology on its own frozen
    cohort -> promote -> write outputs. Never raises on a single trial or
    candidate's scoring failure (see _score_grid)."""
    del priority_trial_keys  # ordering hint only in this module; the watcher plan uses it
    os.makedirs(RMSE_TRACKING_DIR, exist_ok=True)
    trials = discover_scorable_trials()
    cache = load_sweep_cache()
    participant_of = {t["trial_key"]: t["participant"] for t in trials}

    import sweep_imu_config
    import sweep_mediapipe_config
    imu_scores = _score_grid(trials, "has_imu_rmse", sweep_imu_config.WIDE_GRID,
                             score_imu_candidate, cache, "imu")
    mp_grid = [{"model_variant": v, "vis_thresh": t}
              for v in sweep_mediapipe_config.MODEL_VARIANTS
              for t in sweep_mediapipe_config.VIS_THRESH_CANDIDATES]
    model_path = os.path.join(BASE_DIR, "models", "mediapipe", "pose_landmarker_full.task")
    mp_scores = _score_grid(trials, "has_mediapipe_rmse", mp_grid,
                            score_mediapipe_candidate, cache, "mediapipe", model_path=model_path)
    save_sweep_cache(cache)

    imu_cohort = [t["trial_key"] for t in trials if t["has_imu_rmse"]]
    mp_cohort = [t["trial_key"] for t in trials if t["has_mediapipe_rmse"]]
    imu_ranked = rank_candidates(imu_scores, imu_cohort, participant_of)
    mp_ranked = rank_candidates(mp_scores, mp_cohort, participant_of)

    impl_fp = compute_implementation_fingerprint()
    dataset_fp = hashlib.sha256(
        json.dumps(sorted(t["trial_key"] for t in trials)).encode("utf-8")).hexdigest()
    imu_result = record_sweep_result("imu", imu_ranked, dataset_fp, impl_fp)
    mp_result = record_sweep_result("mediapipe", mp_ranked, dataset_fp, impl_fp)

    _write_sweep_results_csv(imu_ranked, mp_ranked)
    _make_figures(imu_ranked, mp_ranked, trials, imu_cohort, mp_cohort)

    return {"imu": imu_result, "mediapipe": mp_result,
           "imu_ranked": imu_ranked, "mediapipe_ranked": mp_ranked}


def _write_sweep_results_csv(imu_ranked, mp_ranked):
    path = os.path.join(RMSE_TRACKING_DIR, "rmse_sweep_results.csv")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["methodology", "candidate", "median_rmse_deg", "n_trials",
                   "n_participants", "low_coverage"])
        for methodology, ranked in (("imu", imu_ranked), ("mediapipe", mp_ranked)):
            for row in ranked:
                w.writerow([methodology, row["candidate_key"], row["median_rmse"],
                          row["n_trials"], row["n_participants"], row["low_coverage"]])
    os.replace(tmp_path, path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k run_full_sweep -v`
Expected: FAIL initially with `AttributeError: ... '_make_figures'` — proceed to Step 4a.

- [ ] **Step 4a: Add a minimal `_make_figures` (figures are stubbed in these tests; real figure
  content is exercised by Step 6's live-data run, not unit-tested — this repo's other plotting
  functions, e.g. `pt_report_common.make_report_figure`, follow the same no-pixel-test
  convention)**

Append:

```python
def _savefig_atomic(fig, out_path):
    """Write-to-temp-then-rename for figure outputs (design spec §7.3 --
    applies to every output file, not just the CSV, so a crash mid-write
    never leaves a partially-written PNG in place)."""
    tmp_path = out_path + ".tmp"
    fig.savefig(tmp_path, dpi=150, facecolor="white", bbox_inches="tight")
    os.replace(tmp_path, out_path)


def _make_figures(imu_ranked, mp_ranked, trials, imu_cohort, mp_cohort):
    """rmse_trend.png, sweep_heatmap.png, imu_vs_mediapipe_rmse.png (design
    spec §7.3). Smoke-tested only via the live-data run in Task 11 Step 6 --
    this repo's other plotting functions have no pixel tests either."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cfg = load_best_config()
    history = cfg.get("history", [])

    fig, ax = plt.subplots(figsize=(8, 4), facecolor="white")
    for methodology, color in (("imu", "#d62728"), ("mediapipe", "#2ca02c")):
        points = [(i, h["rmse"]) for i, h in enumerate(history) if h["methodology"] == methodology]
        if points:
            xs, ys = zip(*points)
            ax.plot(xs, ys, marker="o", color=color, label=methodology)
    ax.set_xlabel("promotion #")
    ax.set_ylabel("RMSE (deg)")
    ax.legend()
    _savefig_atomic(fig, os.path.join(RMSE_TRACKING_DIR, "rmse_trend.png"))
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4), facecolor="white")
    labels = [r["candidate_key"] for r in mp_ranked]
    values = [r["median_rmse"] or 0.0 for r in mp_ranked]
    ax.bar(range(len(labels)), values, color="#2ca02c", alpha=0.6)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=6)
    ax.set_ylabel("median RMSE (deg)")
    _savefig_atomic(fig, os.path.join(RMSE_TRACKING_DIR, "sweep_heatmap.png"))
    plt.close(fig)

    intersection = set(imu_cohort) & set(mp_cohort)
    fig, ax = plt.subplots(figsize=(4, 4), facecolor="white")
    imu_best = imu_ranked[0]["median_rmse"] if imu_ranked and not imu_ranked[0]["low_coverage"] else None
    mp_best = mp_ranked[0]["median_rmse"] if mp_ranked and not mp_ranked[0]["low_coverage"] else None
    ax.bar(["IMU", "MediaPipe"], [imu_best or 0.0, mp_best or 0.0],
          color=["#d62728", "#2ca02c"], alpha=0.6)
    ax.set_ylabel("median RMSE (deg)")
    n_participants = len({p for t, p in
                         [(t, next(tr["participant"] for tr in trials if tr["trial_key"] == t))
                          for t in intersection]}) if intersection else 0
    ax.set_title(f"n={len(intersection)} trials, {n_participants} participants (intersection)",
                fontsize=8)
    _savefig_atomic(fig, os.path.join(RMSE_TRACKING_DIR, "imu_vs_mediapipe_rmse.png"))
    plt.close(fig)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -k run_full_sweep -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full new test file**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -v`
Expected: PASS (all tests from Tasks 1-11, ~50 tests total)

- [ ] **Step 7: Commit**

```bash
git add rmse_pipeline_common.py tests/test_rmse_pipeline_common.py
git commit -m "feat: wire run_full_sweep orchestration with CSV/figure outputs"
```

---

### Task 12: Dependency manifest, `.gitignore`, and full regression + real-data verification

**Files:**
- Modify: `requirements.txt`, `.gitignore`
- Verification only otherwise

- [ ] **Step 1: Add `sweep_cache/` and `rmse_best_config.json` to `.gitignore`**

Add near the existing `imu_calibration_config.json` entry (both are auto-generated,
frequently-changing local state, same category):

```
sweep_cache/
rmse_best_config.json
```

- [ ] **Step 2: Confirm `requirements.txt` already covers this module's dependencies**

`rmse_pipeline_common.py` uses `numpy`, `scipy` (via `workbench_engine`), `opencv-python`,
`mediapipe` — all already present in `requirements.txt`. No change needed here (the `watchdog`
dependency belongs to the follow-up watcher plan, not this one).

- [ ] **Step 3: Run the complete new test file**

Run: `.venv\Scripts\python.exe -m pytest tests/test_rmse_pipeline_common.py -v`
Expected: PASS, all tests from Tasks 1-11

- [ ] **Step 4: Run the full repo test suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS, or the same pre-existing failures as before this work (compare against a
baseline run if unsure — nothing in Tasks 1-11 touches any existing module's code, only reads
from it).

- [ ] **Step 5: Run the real pipeline against the live dataset**

Run: `.venv\Scripts\python.exe -c "import rmse_pipeline_common as rpc; import json; print(json.dumps(rpc.run_full_sweep(), indent=2, default=str))"`

Confirm in the output:
- `discover_scorable_trials()` finds the real Participant 13/14 trials with both IMU and
  OptiTrack data (and Participant 14's video trials, if `Recordings/Participant_14/{Left,Right}/pre/Trial_{n}.avi`
  files are present on this machine).
- No unhandled exception — a per-trial/candidate scoring failure prints a message and the sweep
  completes.
- `Model_Analysis_Outputs/RMSE_Tracking/rmse_sweep_results.csv`, `rmse_trend.png`,
  `sweep_heatmap.png`, and `imu_vs_mediapipe_rmse.png` are all written.
- If the ranking result reports 0 or very few contributing participants (this dataset currently
  has real IMU data only for Participant 13, per this design's own investigation), that's
  expected, not a bug — the coverage-floor logic (Task 9) should correctly mark results
  `low_coverage` rather than promoting on too little data. Note this to the user rather than
  treating a `low_coverage` result as a failure.

- [ ] **Step 6: Note the follow-up for the user (do not do this automatically)**

Per the design spec, this module is manually triggered here (no watcher yet — that's the
follow-up plan). Tell the user how to re-run it (`python -c "import rmse_pipeline_common as rpc; rpc.run_full_sweep()"`)
and that the automatic file-watching trigger is a separate plan to write next.

- [ ] **Step 7: Final commit (only if Step 3-5 required any fix)**

If verification surfaced a real bug, fix it, re-run the relevant test(s), and commit:

```bash
git add -A -- rmse_pipeline_common.py tests/ .gitignore requirements.txt
git commit -m "fix: <describe the issue found during end-to-end verification>"
```

If nothing needed fixing, skip this step.
