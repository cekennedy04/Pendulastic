# MediaPipe HPE Preprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and measure three isolated frame-preprocessing mechanisms (rotate-to-upright,
motion-based leg crop, stateful identity-tracker reuse) against MediaPipe's current baseline
person-selection, via a full-dataset diagnostic sweep, to work toward RMSE < 10° for ≥90% of
trials vs OptiTrack.

**Architecture:** A new pure-geometry module (`mediapipe_preprocessing.py`, no MediaPipe/video I/O,
fully unit-testable with synthetic numpy arrays) provides the two new preprocessing transforms plus
a pixel-space knee-angle helper. A new diagnostic script (`sweep_mediapipe_preprocessing.py`) wires
those transforms plus the existing `PatientIdentityTracker` into 5 independent candidates, scored
against every video+OptiTrack trial via the existing `rmse_pipeline_common`/`workbench_engine`
primitives, with a small self-contained cache. The script itself is never run under `pytest`.

**Tech Stack:** Python, OpenCV (`cv2`), NumPy, MediaPipe Tasks API (`mp.tasks.vision.PoseLandmarker`), pytest.

## Global Constraints

- Target: RMSE < 10° for ≥90% of trials (`RMSE_GOAL_DEG = 10.0`, already defined in
  `sweep_mediapipe_config.py`; this plan defines its own `GOAL_FRACTION = 0.90` in the new script).
- First pass only: the 5 candidates (`baseline`, `rotate_+90`, `rotate_-90`, `crop`,
  `identity_tracker`) are measured in isolation — never combined/stacked in this plan.
- No CI-gated RMSE assertions anywhere. `sweep_mediapipe_preprocessing.py` is a human-run
  diagnostic script, not part of the pytest suite, and never asserts an RMSE number.
- Unit tests (`tests/test_mediapipe_preprocessing.py`) use synthetic numpy arrays / stand-in
  objects only — no real video file, no MediaPipe model load, no `cv2.VideoCapture`.
- `CROP_BASELINE_SEC = 3.0` (fixed time-based skip on raw pixels, not a call into
  `pt._detect_release()` — that needs an already-extracted angle signal that doesn't exist at this
  stage). `CROP_PAD_FRACTION = 0.20` (20% of the detected motion box's width/height, each side,
  clamped to frame bounds).
- Knee-flexion angle must be computed in pixel space (`landmark.x * frame_width`,
  `landmark.y * frame_height`), not MediaPipe's raw per-axis-normalized `[0,1]` coordinates —
  normalized coordinates are scaled independently by width and height, which is NOT rotation
  invariant across a 90° rotation that swaps width and height. Pixel space is.
- No changes to `sweep_mediapipe_config.py`, `batch_mediapipe.py`, `patient_identity_tracker.py`,
  or `rmse_pipeline_common.py` — only new files that import and reuse them.

Spec: `docs/superpowers/specs/2026-08-11-mediapipe-hpe-preprocessing-design.md`

---

## Task 1: Rotation + pixel-space angle helper (`mediapipe_preprocessing.py`)

**Files:**
- Create: `mediapipe_preprocessing.py`
- Create: `tests/test_mediapipe_preprocessing.py`

**Interfaces:**
- Produces: `mediapipe_preprocessing.rotate_to_upright(frame: np.ndarray, angle_deg: int) -> np.ndarray`
- Produces: `mediapipe_preprocessing.knee_angle_from_points(hip_px, knee_px, ankle_px) -> float`
- Produces: module constants `CROP_BASELINE_SEC = 3.0`, `CROP_PAD_FRACTION = 0.20`,
  `MOTION_DIFF_THRESHOLD = 15.0` (constants live here even though the crop functions using the
  last two are added in Task 2, so all tunable constants stay in one place).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mediapipe_preprocessing.py`:

```python
import math
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import numpy as np
import cv2

import mediapipe_preprocessing as mp_pre


def test_rotate_to_upright_zero_degrees_returns_frame_unchanged():
    frame = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    result = mp_pre.rotate_to_upright(frame, 0)
    assert result is frame


def test_rotate_to_upright_plus_90_matches_cv2_clockwise():
    frame = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    result = mp_pre.rotate_to_upright(frame, 90)
    expected = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    assert result.shape == (4, 2, 3)
    assert np.array_equal(result, expected)


def test_rotate_to_upright_minus_90_matches_cv2_counterclockwise():
    frame = np.arange(24, dtype=np.uint8).reshape(2, 4, 3)
    result = mp_pre.rotate_to_upright(frame, -90)
    expected = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    assert result.shape == (4, 2, 3)
    assert np.array_equal(result, expected)


def test_rotate_to_upright_rejects_invalid_angle():
    frame = np.zeros((2, 4, 3), dtype=np.uint8)
    try:
        mp_pre.rotate_to_upright(frame, 45)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_knee_angle_from_points_right_angle():
    hip = np.array([0.0, 1.0])
    knee = np.array([0.0, 0.0])
    ankle = np.array([1.0, 0.0])
    angle = mp_pre.knee_angle_from_points(hip, knee, ankle)
    assert math.isclose(angle, 90.0, abs_tol=1e-6)


def test_knee_angle_from_points_straight_leg():
    hip = np.array([0.0, 1.0])
    knee = np.array([0.0, 0.0])
    ankle = np.array([0.0, -1.0])
    angle = mp_pre.knee_angle_from_points(hip, knee, ankle)
    assert math.isclose(angle, 180.0, abs_tol=1e-6)


def test_knee_angle_from_points_degenerate_zero_length_vector():
    hip = np.array([0.0, 0.0])
    knee = np.array([0.0, 0.0])
    ankle = np.array([1.0, 0.0])
    angle = mp_pre.knee_angle_from_points(hip, knee, ankle)
    assert math.isnan(angle)


def test_knee_angle_rotation_invariant_under_arbitrary_rotation():
    """Regression test for the design spec's rotation-invariance claim: the
    angle between two vectors sharing the knee as a common vertex is
    unchanged under any rotation of all three points -- checked with an
    arbitrary (non-90-degree) rotation so this isn't sensitive to getting
    cv2's specific 90-degree direction convention right or wrong."""
    hip = np.array([0.2, 0.3])
    knee = np.array([0.5, 0.5])
    ankle = np.array([0.6, 0.9])
    original = mp_pre.knee_angle_from_points(hip, knee, ankle)

    theta = math.radians(37.0)
    R = np.array([[math.cos(theta), -math.sin(theta)],
                  [math.sin(theta), math.cos(theta)]])
    hip_r, knee_r, ankle_r = R @ hip, R @ knee, R @ ankle
    rotated = mp_pre.knee_angle_from_points(hip_r, knee_r, ankle_r)

    assert math.isclose(original, rotated, abs_tol=1e-6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mediapipe_preprocessing'`

- [ ] **Step 3: Write the implementation**

Create `mediapipe_preprocessing.py`:

```python
"""mediapipe_preprocessing.py
=============================
Pure frame/array preprocessing helpers for the MediaPipe HPE preprocessing
experiment (see docs/superpowers/specs/2026-08-11-mediapipe-hpe-preprocessing-
design.md). No MediaPipe or video I/O here -- every function takes
already-loaded frame arrays / landmark points and returns transformed
arrays, so this module is unit-testable with synthetic numpy data alone.
"""
from __future__ import annotations

import cv2
import numpy as np

CROP_BASELINE_SEC = 3.0
CROP_PAD_FRACTION = 0.20
MOTION_DIFF_THRESHOLD = 15.0


def rotate_to_upright(frame, angle_deg):
    """Rotate a BGR frame (H, W, 3) by a fixed angle before MediaPipe
    inference, so a reclined patient's torso reads closer to the upright
    orientation BlazePose is mostly trained on. angle_deg must be 0, 90, or
    -90 -- 0 returns the frame unchanged (no copy); +/-90 dispatch to
    cv2.rotate with the matching direction constant."""
    if angle_deg == 0:
        return frame
    if angle_deg == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if angle_deg == -90:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError(f"angle_deg must be 0, 90, or -90, got {angle_deg!r}")


def knee_angle_from_points(hip_px, knee_px, ankle_px):
    """Knee-flexion angle in degrees: the angle at the knee vertex between
    the hip->knee and ankle->knee vectors, computed in pixel space (not
    MediaPipe's per-axis-normalized [0,1] coordinates) so the metric is
    genuinely rotation-invariant -- normalized coordinates are scaled
    independently by frame width and height, which would NOT be invariant
    under a 90-degree frame rotation that swaps width and height. Returns
    nan if either vector is degenerate (zero length)."""
    hip_px = np.asarray(hip_px, dtype=float)
    knee_px = np.asarray(knee_px, dtype=float)
    ankle_px = np.asarray(ankle_px, dtype=float)
    v1 = hip_px - knee_px
    v2 = ankle_px - knee_px
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    cos_a = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_a)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add mediapipe_preprocessing.py tests/test_mediapipe_preprocessing.py
git commit -m "feat: add rotate-to-upright and pixel-space knee-angle helper"
```

---

## Task 2: Motion-based leg crop (`mediapipe_preprocessing.py`)

**Files:**
- Modify: `mediapipe_preprocessing.py`
- Modify: `tests/test_mediapipe_preprocessing.py`

**Interfaces:**
- Consumes: `CROP_BASELINE_SEC`, `CROP_PAD_FRACTION`, `MOTION_DIFF_THRESHOLD` (Task 1)
- Produces: `mediapipe_preprocessing._find_motion_bbox(frames: list[np.ndarray]) -> tuple[int,int,int,int] | None`
- Produces: `mediapipe_preprocessing.crop_to_moving_leg(frames: list[np.ndarray], fps: float) -> list[np.ndarray]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mediapipe_preprocessing.py`:

```python
def _solid_frame(h, w, value):
    return np.full((h, w, 3), value, dtype=np.uint8)


def test_find_motion_bbox_locates_high_motion_region():
    h, w = 20, 30
    base = _solid_frame(h, w, 50)
    moving = base.copy()
    moving[2:6, 22:28] = 200
    frames = [base, moving, base, moving]
    bbox = mp_pre._find_motion_bbox(frames)
    assert bbox == (22, 2, 6, 4)


def test_find_motion_bbox_none_for_static_input():
    h, w = 20, 30
    frames = [_solid_frame(h, w, 50) for _ in range(4)]
    assert mp_pre._find_motion_bbox(frames) is None


def test_find_motion_bbox_none_for_fewer_than_two_frames():
    assert mp_pre._find_motion_bbox([_solid_frame(10, 10, 0)]) is None


def test_crop_to_moving_leg_crops_around_motion_region():
    h, w = 40, 60
    fps = 10.0
    n_baseline = int(mp_pre.CROP_BASELINE_SEC * fps)  # 30 static frames
    frames = [_solid_frame(h, w, 50) for _ in range(n_baseline)]
    for i in range(10):
        frame = _solid_frame(h, w, 50)
        if i % 2 == 1:
            frame[5:15, 45:58] = 200
        frames.append(frame)

    result = mp_pre.crop_to_moving_leg(frames, fps)

    assert len(result) == len(frames)
    out_h, out_w = result[0].shape[:2]
    assert out_h < h and out_w < w


def test_crop_to_moving_leg_falls_back_when_shorter_than_baseline():
    h, w = 20, 30
    fps = 10.0
    frames = [_solid_frame(h, w, 50) for _ in range(5)]  # < CROP_BASELINE_SEC * fps
    result = mp_pre.crop_to_moving_leg(frames, fps)
    assert len(result) == len(frames)
    assert result[0].shape == frames[0].shape


def test_crop_to_moving_leg_falls_back_when_no_motion_found():
    h, w = 20, 30
    fps = 10.0
    frames = [_solid_frame(h, w, 50) for _ in range(60)]  # all static
    result = mp_pre.crop_to_moving_leg(frames, fps)
    assert len(result) == len(frames)
    assert result[0].shape == frames[0].shape
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -v`
Expected: FAIL with `AttributeError: module 'mediapipe_preprocessing' has no attribute '_find_motion_bbox'`

- [ ] **Step 3: Write the implementation**

Append to `mediapipe_preprocessing.py`:

```python
def _find_motion_bbox(frames):
    """Bounding box (x, y, w, h) in pixel coordinates of the region with
    the highest cumulative motion across `frames` (BGR or grayscale numpy
    arrays, len(frames) >= 2 required). A pixel counts as "moving" when its
    mean absolute frame-to-frame grayscale difference exceeds
    MOTION_DIFF_THRESHOLD. Returns None if no pixel exceeds that threshold
    (degenerate/all-still input, or fewer than 2 frames) -- never guesses a
    box."""
    if len(frames) < 2:
        return None
    gray = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) if f.ndim == 3 else f
            for f in frames]
    accum = np.zeros(gray[0].shape, dtype=np.float64)
    for a, b in zip(gray[:-1], gray[1:]):
        accum += np.abs(a.astype(np.float64) - b.astype(np.float64))
    mean_motion = accum / (len(frames) - 1)
    mask = mean_motion > MOTION_DIFF_THRESHOLD
    if not np.any(mask):
        return None
    ys, xs = np.where(mask)
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def crop_to_moving_leg(frames, fps):
    """Crop every frame in `frames` (BGR numpy arrays, one full trial's
    worth) to a padded bounding box around the region with the most motion,
    skipping a fixed CROP_BASELINE_SEC leading window (a fixed time-based
    skip on raw pixels, NOT a call into pt._detect_release() -- that
    function needs an already-extracted scalar angle signal, which doesn't
    exist yet at this stage). Falls back to the original, uncropped frames
    (never raises) when the trial is shorter than the baseline window or no
    clear motion region is found."""
    if not frames:
        return list(frames)
    baseline_skip = int(round(CROP_BASELINE_SEC * fps))
    working = frames[baseline_skip:]
    if len(working) < 2:
        return list(frames)
    bbox = _find_motion_bbox(working)
    if bbox is None:
        return list(frames)
    x, y, w, h = bbox
    frame_h, frame_w = frames[0].shape[:2]
    pad_x = int(round(w * CROP_PAD_FRACTION))
    pad_y = int(round(h * CROP_PAD_FRACTION))
    x0 = max(0, x - pad_x)
    y0 = max(0, y - pad_y)
    x1 = min(frame_w, x + w + pad_x)
    y1 = min(frame_h, y + h + pad_y)
    return [f[y0:y1, x0:x1] for f in frames]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add mediapipe_preprocessing.py tests/test_mediapipe_preprocessing.py
git commit -m "feat: add motion-based moving-leg crop"
```

---

## Task 3: Diagnostic sweep script (`sweep_mediapipe_preprocessing.py`)

**Files:**
- Create: `sweep_mediapipe_preprocessing.py`
- Modify: `tests/test_mediapipe_preprocessing.py`

**Interfaces:**
- Consumes: `mediapipe_preprocessing.rotate_to_upright`, `.crop_to_moving_leg`,
  `.knee_angle_from_points` (Tasks 1-2); `batch_mediapipe.MP_LEG_IDX`,
  `batch_mediapipe._select_patient_pose`; `patient_identity_tracker.PatientIdentityTracker`;
  `pendulastic_pt_score.load_optitrack`; `rmse_pipeline_common.discover_video_trials`,
  `rmse_pipeline_common.sha256_file`; `workbench_engine.compare_pair`.
- Produces: `sweep_mediapipe_preprocessing._select_pose_for_candidate(candidate: dict, tracker, poses, w, h) -> pose | None`
  (the only function from this script covered by a gating unit test — the rest is exercised by
  manual runs per Task 4, matching the design spec's explicit "no RMSE assertions in CI").

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mediapipe_preprocessing.py`:

```python
import batch_mediapipe as bm
import patient_identity_tracker as pit
import sweep_mediapipe_preprocessing as smp


class _StubTracker:
    def __init__(self, pose_to_return):
        self.calls = []
        self._pose = pose_to_return

    def select(self, poses, w, h):
        self.calls.append((poses, w, h))
        return pit.SelectionResult(self._pose, 1.0, False)


def test_select_pose_for_candidate_uses_identity_tracker_when_requested():
    poses = ["pose_a", "pose_b"]
    tracker = _StubTracker(pose_to_return="pose_b")
    result = smp._select_pose_for_candidate(
        {"key": "identity_tracker"}, tracker, poses, 640, 480)
    assert result == "pose_b"
    assert tracker.calls == [(poses, 640, 480)]


def test_select_pose_for_candidate_uses_stateless_selector_for_other_candidates(monkeypatch):
    poses = ["pose_a", "pose_b"]
    calls = []

    def _stub_select_patient_pose(p):
        calls.append(p)
        return "pose_a"

    monkeypatch.setattr(bm, "_select_patient_pose", _stub_select_patient_pose)
    tracker = _StubTracker(pose_to_return="pose_b")  # must NOT be used for this candidate

    result = smp._select_pose_for_candidate({"key": "baseline"}, tracker, poses, 640, 480)

    assert result == "pose_a"
    assert calls == [poses]
    assert tracker.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sweep_mediapipe_preprocessing'`

- [ ] **Step 3: Write the implementation**

Create `sweep_mediapipe_preprocessing.py`:

```python
"""
sweep_mediapipe_preprocessing.py
=================================
Non-gating diagnostic sweep comparing three frame-preprocessing mechanisms
(rotate-to-upright, motion-based leg crop, stateful identity-tracker reuse)
against today's baseline person-selection, in isolation, across every
participant with video + OptiTrack ground truth -- not just P14. See
docs/superpowers/specs/2026-08-11-mediapipe-hpe-preprocessing-design.md for
the full design.

This script only reports; it asserts nothing and is not part of the pytest
suite (real video inference is slow and depends on local data files not
guaranteed present on every machine -- see the design spec S6).

Run:
    .venv\\Scripts\\python.exe sweep_mediapipe_preprocessing.py
"""
from __future__ import annotations

import csv
import hashlib
import inspect
import json
import os
import sys

import cv2
import mediapipe as mp
import numpy as np

import batch_mediapipe as bm
import mediapipe_preprocessing as mp_pre
import patient_identity_tracker as pit
import pendulastic_pt_score as pt
import rmse_pipeline_common as rpc
import workbench_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MediaPipe_Sweep")
RESULTS_CSV = os.path.join(OUT_DIR, "preprocessing_sweep_results.csv")
CACHE_DIR = os.path.join(OUT_DIR, "preprocessing_cache")
CACHE_MANIFEST = os.path.join(CACHE_DIR, "manifest.json")

RMSE_GOAL_DEG = 10.0
GOAL_FRACTION = 0.90
MODEL_VARIANT = "full"
VIS_THRESH = 0.40

CANDIDATES = [
    {"key": "baseline"},
    {"key": "rotate_+90", "rotate_deg": 90},
    {"key": "rotate_-90", "rotate_deg": -90},
    {"key": "crop"},
    {"key": "identity_tracker"},
]

BaseOptions = mp.tasks.BaseOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode


def _make_landmarker(model_path):
    opts = PoseLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionRunningMode.IMAGE,
        num_poses=2,
    )
    return PoseLandmarker.create_from_options(opts)


def _select_pose_for_candidate(candidate, tracker, poses, w, h):
    """Dispatch person-selection by candidate key. identity_tracker uses
    the stateful PatientIdentityTracker (production's own selection logic,
    never before measured in a sweep); every other candidate uses today's
    stateless bm._select_patient_pose, the existing sweep_mediapipe_config.py
    baseline, so every non-identity-tracker candidate is compared against
    the same person-selection logic and only the frame preprocessing
    varies."""
    if candidate["key"] == "identity_tracker":
        return tracker.select(poses, w, h).pose
    return bm._select_patient_pose(poses)


def extract_landmarks_for_candidate(video_path, leg, model_path, candidate):
    """Runs one video through MediaPipe once for the given candidate's
    preprocessing. Returns per-frame dicts with PIXEL-space (not
    normalized) hip/knee/ankle coordinates -- required for
    mp_pre.knee_angle_from_points()'s rotation-invariance property to
    actually hold for the rotate_+90/rotate_-90 candidates (see that
    function's docstring)."""
    h_idx, k_idx, a_idx = bm.MP_LEG_IDX[leg]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    raw_frames = []
    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        raw_frames.append(frame_bgr)
    cap.release()
    if not raw_frames:
        return []

    if candidate["key"] == "crop":
        raw_frames = mp_pre.crop_to_moving_leg(raw_frames, fps)

    rotate_deg = candidate.get("rotate_deg", 0)
    tracker = pit.PatientIdentityTracker(h_idx, k_idx, a_idx)

    frames_out = []
    with _make_landmarker(model_path) as landmarker:
        for i, frame_bgr in enumerate(raw_frames):
            t_sec = i / fps
            row = {"t": t_sec, "hip_px": None, "knee_px": None, "ankle_px": None,
                   "hip_v": 0.0, "knee_v": 0.0, "ankle_v": 0.0}
            try:
                proc_frame = (mp_pre.rotate_to_upright(frame_bgr, rotate_deg)
                             if rotate_deg else frame_bgr)
                fh, fw = proc_frame.shape[:2]
                rgb = cv2.cvtColor(proc_frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_image)
                poses = result.pose_landmarks or []
                pose = _select_pose_for_candidate(candidate, tracker, poses, fw, fh)
                if pose is not None:
                    hl, kl, al = pose[h_idx], pose[k_idx], pose[a_idx]
                    row.update(
                        hip_px=(hl.x * fw, hl.y * fh),
                        knee_px=(kl.x * fw, kl.y * fh),
                        ankle_px=(al.x * fw, al.y * fh),
                        hip_v=float(hl.visibility), knee_v=float(kl.visibility),
                        ankle_v=float(al.visibility))
            except Exception:
                pass
            frames_out.append(row)
    return frames_out


def angles_from_raw(frames, vis_thresh):
    t_list, ang_list = [], []
    for row in frames:
        angle = float("nan")
        if (row["hip_px"] is not None and row["hip_v"] > vis_thresh
                and row["knee_v"] > vis_thresh and row["ankle_v"] > vis_thresh):
            angle = mp_pre.knee_angle_from_points(
                row["hip_px"], row["knee_px"], row["ankle_px"])
        t_list.append(row["t"])
        ang_list.append(angle)
    return np.array(t_list), np.array(ang_list)


def score_candidate(video_path, leg, model_path, candidate, opti_t, opti_ang,
                    vis_thresh=VIS_THRESH):
    frames = extract_landmarks_for_candidate(video_path, leg, model_path, candidate)
    t_m, ang_m = angles_from_raw(frames, vis_thresh)
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    return result["rmse_deg"] if result.get("status") == "ok" else None


def _load_cache():
    if not os.path.isfile(CACHE_MANIFEST):
        return {}
    try:
        with open(CACHE_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{CACHE_MANIFEST} failed to parse -- treating as empty.")
        return {}


def _save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp_path = CACHE_MANIFEST + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp_path, CACHE_MANIFEST)


def _implementation_fingerprint():
    parts = [inspect.getsource(sys.modules[__name__]), inspect.getsource(mp_pre)]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _cache_key(trial, candidate_key, model_path, stat_cache, impl_fp):
    video_fp = rpc.sha256_file(trial["video_path"], stat_cache)
    model_fp = rpc.sha256_file(model_path, stat_cache)
    blob = json.dumps({
        "trial_key": trial["trial_key"], "candidate": candidate_key,
        "video": video_fp, "model": model_fp, "impl": impl_fp,
    }, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    trials = rpc.discover_video_trials()
    print(f"{len(trials)} trial(s) with video + OptiTrack ground truth found.")
    if not trials:
        return

    model_path = os.path.join(BASE_DIR, "models", "mediapipe",
                              f"pose_landmarker_{MODEL_VARIANT}.task")
    if not os.path.isfile(model_path):
        print(f"model file not found at {model_path}")
        return

    cache = _load_cache()
    stat_cache = {}
    impl_fp = _implementation_fingerprint()
    rows = []

    for candidate in CANDIDATES:
        rmses = []
        for trial in trials:
            try:
                opti_t, opti_ang = pt.load_optitrack(trial["optitrack_path"])
            except Exception as e:
                print(f"  [skip] {trial['trial_key']}: OptiTrack load failed: {e}")
                continue

            cache_key = _cache_key(trial, candidate["key"], model_path, stat_cache, impl_fp)
            if cache_key in cache:
                rmse = cache[cache_key]
            else:
                try:
                    rmse = score_candidate(trial["video_path"], trial["leg"], model_path,
                                           candidate, opti_t, opti_ang)
                except Exception as e:
                    print(f"  [error] {trial['trial_key']} / {candidate['key']}: {e}")
                    rmse = None
                cache[cache_key] = rmse
            if rmse is not None:
                rmses.append(rmse)

        _save_cache(cache)
        n_scored = len(rmses)
        n_under_goal = sum(1 for r in rmses if r < RMSE_GOAL_DEG)
        pct_under_goal = (n_under_goal / n_scored * 100.0) if n_scored else 0.0
        median_rmse = float(np.median(rmses)) if rmses else None
        mean_rmse = float(np.mean(rmses)) if rmses else None

        rows.append({
            "candidate": candidate["key"], "n_trials": len(trials), "n_scored": n_scored,
            "median_rmse_deg": median_rmse, "mean_rmse_deg": mean_rmse,
            "pct_under_10deg": pct_under_goal,
        })

        median_str = f"{median_rmse:.2f}" if median_rmse is not None else "n/a"
        print(f"{candidate['key']:16s} n_scored={n_scored}/{len(trials)}  "
             f"median={median_str} deg  %<10deg={pct_under_goal:.1f}%")
        goal_met = n_scored > 0 and pct_under_goal >= GOAL_FRACTION * 100.0
        print(f"  {'GOAL MET' if goal_met else 'goal not met'} "
             f"({pct_under_goal:.1f}% of trials < {RMSE_GOAL_DEG:.0f} deg, "
             f"target {GOAL_FRACTION*100:.0f}%)")

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {RESULTS_CSV}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -v`
Expected: PASS (16 tests). This does not run `main()` or touch any video/model file — only the
pure `_select_pose_for_candidate` dispatch function is exercised.

- [ ] **Step 5: Commit**

```bash
git add sweep_mediapipe_preprocessing.py tests/test_mediapipe_preprocessing.py
git commit -m "feat: add non-gating preprocessing sweep across full dataset"
```

---

## Task 4: Manual diagnostic run against real data

**Files:** none (no code changes) — this task runs the script built in Task 3 and records what it
reports. Not gated by pytest, matching the design spec's explicit non-CI convention.

- [ ] **Step 1: Run the sweep**

Run: `.venv\Scripts\python.exe sweep_mediapipe_preprocessing.py`

If this machine has no `Recordings/`/`OptiTrack_Recordings/` data or no
`models/mediapipe/pose_landmarker_full.task` file, the script prints `0 trial(s)...` or `model file
not found...` and exits — that's an environment-data-availability result, not a code failure; note
it and stop here.

- [ ] **Step 2: Inspect the report**

Read the console output and `Model_Analysis_Outputs/MediaPipe_Sweep/preprocessing_sweep_results.csv`
— 5 rows (`baseline`, `rotate_+90`, `rotate_-90`, `crop`, `identity_tracker`), each with `n_scored`,
`median_rmse_deg`, `mean_rmse_deg`, `pct_under_10deg`.

- [ ] **Step 3: Record findings**

Note, for the user: which candidate(s) beat `baseline` on `median_rmse_deg` and on
`pct_under_10deg`, and whether any candidate reaches the 90% target. This is the input to deciding
what (if anything) to promote into `batch_mediapipe.py` production and whether a second, stacked
pass (out of scope for this plan — see spec §10) is worth running.

---
