# Stateful MediaPipe Patient-Identity Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `batch_mediapipe.py`'s per-frame, memory-less patient/assessor selection with a stateful, hysteresis-gated tracker so a single ambiguous frame can no longer flip the whole trial onto the assessor — the confirmed dominant driver of high MediaPipe-vs-OptiTrack RMSE.

**Architecture:** A new standalone module, `patient_identity_tracker.py`, implements pure scoring functions plus a `PatientIdentityTracker` class that holds per-trial state (locked knee position, challenger streak, counters). `batch_mediapipe.py`'s `process_trial()` instantiates one tracker per trial and calls it once per frame in place of the existing stateless `_select_patient_pose()`. No other file changes.

**Tech Stack:** Python 3.13, MediaPipe PoseLandmarker (BlazePose 33-point), pytest (plain functions, `tmp_path`/`monkeypatch`, no test classes — matching this repo's existing convention).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-07-mediapipe-patient-identity-tracking-design.md` — every constant/formula below is copied from it verbatim; do not invent different defaults.
- `_select_patient_pose()` in `batch_mediapipe.py` stays exactly as-is — it is still used directly by `sweep_mediapipe_config.py` (out of scope for this plan) and has its own passing tests in `tests/test_batch_mediapipe.py` that must keep passing unmodified.
- `mediapipe_worker.py`, `pendulastic_pipeline.py`, and the `--guided` research path are out of scope — do not touch them.
- New CSV columns are **additive only** — never rename or remove an existing `batch_mediapipe.py` output column. Downstream readers (`model_vs_optitrack_eval.py` etc.) use `pandas.read_csv()` with no `usecols`, i.e. by column name, so additive columns are safe; this must remain true.
- Test style: plain functions, no test classes, `tmp_path`/`monkeypatch` for filesystem tests — matches `tests/test_batch_mediapipe.py` and `tests/test_pt_cohort_common.py`.
- Run tests with the project venv: `.venv\Scripts\python.exe -m pytest <path> -v` (Windows; use forward or back slashes consistently with the rest of this plan).

---

### Task 1: Scoring helper functions in `patient_identity_tracker.py`

**Files:**
- Create: `patient_identity_tracker.py`
- Test: `tests/test_patient_identity_tracker.py`

**Interfaces:**
- Produces: `_trunk_horizontal_score(pose) -> float`, `_visibility_score(pose, hip_idx, knee_idx, ankle_idx) -> float`, `_anatomical_penalty(pose, hip_idx, knee_idx, ankle_idx, w, h) -> float`. All three take a `pose` — an indexable sequence of landmark-like objects exposing `.x`, `.y`, and (for the latter two) `.visibility` — plus a frame width/height in pixels where noted. Consumed by Task 2's `PatientIdentityTracker`.
- Constants produced: `DEFAULT_HYSTERESIS_FRAMES = 5`, `DEFAULT_CONFIDENCE_FLOOR = 0.35`, `ANATOMICAL_MIN_RATIO = 0.4`, `ANATOMICAL_MAX_RATIO = 2.5`, `ANATOMICAL_PENALTY = 0.3`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_patient_identity_tracker.py`:

```python
import math
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import patient_identity_tracker as pit

# BlazePose indices used across these tests (right leg, matches
# mediapipe_worker.py's MP_R_HIP/MP_R_KNEE/MP_R_ANKLE = 24, 26, 28).
HIP_IDX, KNEE_IDX, ANKLE_IDX = 24, 26, 28
W, H = 640, 480


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _pose(shoulder_mid, hip_mid, knee_pt, ankle_pt, visibility=1.0):
    """33-landmark BlazePose-shaped list. Shoulders (11,12) and hips (23,24)
    are set to the same midpoint (only the midpoint is used for trunk
    orientation, matching batch_mediapipe.py's _select_patient_pose
    convention). hip/knee/ankle at HIP_IDX/KNEE_IDX/ANKLE_IDX are also set
    for the anatomical/visibility scoring under test."""
    pose = [_LM(0.0, 0.0, 0.0)] * 33
    pose[11] = pose[12] = _LM(*shoulder_mid, visibility)
    pose[23] = pose[24] = _LM(*hip_mid, visibility)
    pose[HIP_IDX] = _LM(*hip_mid, visibility)
    pose[KNEE_IDX] = _LM(*knee_pt, visibility)
    pose[ANKLE_IDX] = _LM(*ankle_pt, visibility)
    return pose


PATIENT = _pose(shoulder_mid=(0.75, 0.40), hip_mid=(0.55, 0.42),
                 knee_pt=(0.45, 0.44), ankle_pt=(0.40, 0.60))
ASSESSOR = _pose(shoulder_mid=(0.10, 0.20), hip_mid=(0.12, 0.55),
                  knee_pt=(0.14, 0.75), ankle_pt=(0.16, 0.90))


def test_trunk_horizontal_score_high_for_reclining_patient():
    assert pit._trunk_horizontal_score(PATIENT) > 0.9


def test_trunk_horizontal_score_low_for_standing_assessor():
    assert pit._trunk_horizontal_score(ASSESSOR) < 0.2


def test_trunk_horizontal_score_zero_for_degenerate_zero_length_trunk():
    degenerate = _pose((0.5, 0.5), (0.5, 0.5), (0.5, 0.6), (0.5, 0.7))
    assert pit._trunk_horizontal_score(degenerate) == 0.0


def test_visibility_score_averages_hip_knee_ankle():
    pose = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44), (0.40, 0.60),
                  visibility=0.6)
    assert math.isclose(
        pit._visibility_score(pose, HIP_IDX, KNEE_IDX, ANKLE_IDX), 0.6)


def test_anatomical_penalty_full_score_for_plausible_ratio():
    # PATIENT's shank/thigh ratio is ~1.3 (plausible human range) at 640x480.
    assert pit._anatomical_penalty(
        PATIENT, HIP_IDX, KNEE_IDX, ANKLE_IDX, W, H) == 1.0


def test_anatomical_penalty_reduced_for_implausible_ratio():
    # Ankle placed 20x the thigh length away from the knee -- an anatomically
    # impossible shank, e.g. a hallucinated detection.
    bad = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44), (0.45, 3.44))
    assert pit._anatomical_penalty(
        bad, HIP_IDX, KNEE_IDX, ANKLE_IDX, W, H) == pit.ANATOMICAL_PENALTY


def test_anatomical_penalty_handles_zero_length_thigh():
    zero_thigh = _pose((0.75, 0.40), (0.55, 0.42), (0.55, 0.42), (0.40, 0.60))
    assert pit._anatomical_penalty(
        zero_thigh, HIP_IDX, KNEE_IDX, ANKLE_IDX, W, H) == pit.ANATOMICAL_PENALTY
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_patient_identity_tracker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'patient_identity_tracker'`

- [ ] **Step 3: Write the implementation**

Create `patient_identity_tracker.py`:

```python
"""Stateful per-trial patient-vs-assessor identity tracking for
batch_mediapipe.py. See docs/superpowers/specs/2026-08-07-mediapipe-patient-
identity-tracking-design.md for the full design rationale.
"""
from __future__ import annotations

import math
from collections import namedtuple

_SHOULDER_IDX = (11, 12)
_HIP_IDX = (23, 24)

DEFAULT_HYSTERESIS_FRAMES = 5
DEFAULT_CONFIDENCE_FLOOR = 0.35
ANATOMICAL_MIN_RATIO = 0.4
ANATOMICAL_MAX_RATIO = 2.5
ANATOMICAL_PENALTY = 0.3


def _trunk_horizontal_score(pose) -> float:
    """1.0 = perfectly horizontal shoulder-to-hip vector (reclining), 0.0 =
    perfectly vertical (standing/sitting) or degenerate (zero-length)."""
    l_sh, r_sh = pose[_SHOULDER_IDX[0]], pose[_SHOULDER_IDX[1]]
    l_hp, r_hp = pose[_HIP_IDX[0]], pose[_HIP_IDX[1]]
    dx = (l_sh.x + r_sh.x) / 2.0 - (l_hp.x + r_hp.x) / 2.0
    dy = (l_sh.y + r_sh.y) / 2.0 - (l_hp.y + r_hp.y) / 2.0
    mag = math.hypot(dx, dy)
    if mag < 1e-6:
        return 0.0
    return abs(dx) / mag


def _visibility_score(pose, hip_idx, knee_idx, ankle_idx) -> float:
    vis = [float(getattr(pose[i], "visibility", 0.0))
           for i in (hip_idx, knee_idx, ankle_idx)]
    return sum(vis) / 3.0


def _anatomical_penalty(pose, hip_idx, knee_idx, ankle_idx, w, h) -> float:
    """1.0 if the shank/thigh pixel-length ratio is human-plausible,
    ANATOMICAL_PENALTY (a soft down-weight, not a hard reject) otherwise."""
    hip = (pose[hip_idx].x * w, pose[hip_idx].y * h)
    knee = (pose[knee_idx].x * w, pose[knee_idx].y * h)
    ankle = (pose[ankle_idx].x * w, pose[ankle_idx].y * h)
    thigh = math.hypot(knee[0] - hip[0], knee[1] - hip[1])
    if thigh < 1e-6:
        return ANATOMICAL_PENALTY
    shank = math.hypot(ankle[0] - knee[0], ankle[1] - knee[1])
    ratio = shank / thigh
    if ANATOMICAL_MIN_RATIO <= ratio <= ANATOMICAL_MAX_RATIO:
        return 1.0
    return ANATOMICAL_PENALTY
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_patient_identity_tracker.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add patient_identity_tracker.py tests/test_patient_identity_tracker.py
git commit -m "feat: add patient-identity scoring helpers (trunk/visibility/anatomical)"
```

---

### Task 2: `PatientIdentityTracker` state machine

**Files:**
- Modify: `patient_identity_tracker.py`
- Test: `tests/test_patient_identity_tracker.py`

**Interfaces:**
- Consumes: `_trunk_horizontal_score`, `_visibility_score`, `_anatomical_penalty`, `DEFAULT_HYSTERESIS_FRAMES`, `DEFAULT_CONFIDENCE_FLOOR` from Task 1.
- Produces: `SelectionResult` (namedtuple: `pose`, `score`, `ambiguous`) and `PatientIdentityTracker(hip_idx, knee_idx, ankle_idx, hysteresis_frames=DEFAULT_HYSTERESIS_FRAMES, confidence_floor=DEFAULT_CONFIDENCE_FLOOR)` with method `select(poses, w, h) -> SelectionResult` and read-only counters `.n_switches`, `.n_ambiguous`, `.n_frames`. Consumed by Task 3's `batch_mediapipe.py` integration.

**Design note (why continuity isn't a scored factor):** the spec describes trunk/visibility/anatomical/continuity as four factors feeding one combined score. In implementation, continuity is used only to classify which of two candidates is "tracked" (nearest to the last locked knee position) versus "challenger" — it is *not* included in the score compared against the hysteresis threshold. Folding continuity into that comparison would make the tracked candidate win by construction (its distance-to-itself is always ~0), which would make a genuine identity switch nearly impossible and defeat the hysteresis mechanism's purpose. The switch decision is based on trunk/visibility/anatomical score alone; continuity only decides *who counts as the incumbent* each frame.

**Design note (why the nearest-neighbor classification uses raw pixel distance, not thigh-length normalization):** the spec calls for continuity distance normalized by thigh length, specifically to avoid the resolution/camera-distance dependency of a *fixed* pixel threshold (the `_KneeTracker` bug Codex flagged: a fixed 150px jump threshold is wrong at different zoom levels). This implementation never uses an absolute distance threshold anywhere — the tracked/challenger split is a plain argmin over exactly two candidates (`dists.sort()`, take the nearer one), and picking the nearer of two points is scale-invariant: multiplying both distances by the same per-frame scale factor never changes which one is smaller. Per-candidate thigh-length normalization would only matter if comparing distances *across* candidates with different apparent sizes against a shared absolute cutoff, which this design doesn't do. The resolution-dependence failure mode is avoided structurally rather than by normalizing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_patient_identity_tracker.py`:

```python
# ── PatientIdentityTracker ───────────────────────────────────────────────────

def _tracker(hysteresis_frames=3, confidence_floor=pit.DEFAULT_CONFIDENCE_FLOOR):
    return pit.PatientIdentityTracker(
        HIP_IDX, KNEE_IDX, ANKLE_IDX,
        hysteresis_frames=hysteresis_frames, confidence_floor=confidence_floor)


def _low_confidence_pose():
    # Near-zero horizontal score, low visibility -- geometric score well
    # below any reasonable confidence floor.
    return _pose((0.50, 0.50), (0.505, 0.90), (0.51, 0.95), (0.515, 0.99),
                  visibility=0.05)


def test_init_locks_to_higher_scoring_candidate():
    t = _tracker()
    result = t.select([ASSESSOR, PATIENT], W, H)
    assert result.pose is PATIENT
    assert result.ambiguous is False
    assert t.n_switches == 0


def test_init_order_independent():
    t = _tracker()
    result = t.select([PATIENT, ASSESSOR], W, H)
    assert result.pose is PATIENT


def test_no_poses_marks_ambiguous_without_touching_lock():
    t = _tracker()
    t.select([PATIENT, ASSESSOR], W, H)  # establish lock on PATIENT
    result = t.select([], W, H)
    assert result.pose is None
    assert result.ambiguous is True
    assert t.n_ambiguous == 1
    # Lock survived: next normal frame still tracks PATIENT's position, not
    # reset to an init-style highest-score pick.
    result2 = t.select([ASSESSOR, PATIENT], W, H)
    assert result2.pose is PATIENT


def test_single_pose_accepted_when_above_confidence_floor():
    t = _tracker()
    t.select([ASSESSOR, PATIENT], W, H)  # establish lock on PATIENT
    result = t.select([PATIENT], W, H)
    assert result.pose is PATIENT
    assert result.ambiguous is False


def test_single_low_confidence_pose_marked_ambiguous():
    t = _tracker()
    result = t.select([_low_confidence_pose()], W, H)
    assert result.ambiguous is True
    assert result.pose is None


def test_both_candidates_below_confidence_floor_marks_ambiguous():
    # No prior lock -- this exercises the init branch's ambiguous handling.
    # Two distinct low-scoring poses (same shape as _low_confidence_pose(),
    # translated so they're distinguishable objects at different positions);
    # verified score ~0.031 each, well below the default 0.35 floor.
    t = _tracker()
    low_a = _low_confidence_pose()
    low_b = _pose((0.20, 0.20), (0.205, 0.60), (0.21, 0.65), (0.215, 0.69),
                   visibility=0.05)
    result = t.select([low_a, low_b], W, H)
    assert result.ambiguous is True
    assert result.pose is None
    assert t.n_ambiguous == 1


def test_single_contradictory_frame_does_not_flip_lock():
    t = _tracker(hysteresis_frames=3)
    t.select([ASSESSOR, PATIENT], W, H)  # lock on PATIENT

    # Same positions, but visibility flipped so the pose nearest the lock
    # (still PATIENT's position) now scores lower than the challenger.
    # Verified scores: patient_weak (vis=0.0) ~0.498, assessor_strong
    # (vis=1.0) ~0.529 -- challenger genuinely outscores tracked here.
    patient_weak = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44),
                          (0.40, 0.60), visibility=0.0)
    assessor_strong = _pose((0.10, 0.20), (0.12, 0.55), (0.14, 0.75),
                             (0.16, 0.90), visibility=1.0)

    result = t.select([patient_weak, assessor_strong], W, H)
    assert result.pose is patient_weak  # still the tracked (nearest) candidate
    assert t.n_switches == 0


def test_n_minus_one_contradictory_frames_do_not_flip_lock():
    t = _tracker(hysteresis_frames=3)
    t.select([ASSESSOR, PATIENT], W, H)  # lock on PATIENT

    patient_weak = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44),
                          (0.40, 0.60), visibility=0.0)
    assessor_strong = _pose((0.10, 0.20), (0.12, 0.55), (0.14, 0.75),
                             (0.16, 0.90), visibility=1.0)

    t.select([patient_weak, assessor_strong], W, H)   # streak 1
    result = t.select([patient_weak, assessor_strong], W, H)  # streak 2
    assert result.pose is patient_weak
    assert t.n_switches == 0


def test_n_consecutive_contradictory_frames_flip_lock():
    t = _tracker(hysteresis_frames=3)
    t.select([ASSESSOR, PATIENT], W, H)  # lock on PATIENT

    patient_weak = _pose((0.75, 0.40), (0.55, 0.42), (0.45, 0.44),
                          (0.40, 0.60), visibility=0.0)
    assessor_strong = _pose((0.10, 0.20), (0.12, 0.55), (0.14, 0.75),
                             (0.16, 0.90), visibility=1.0)

    t.select([patient_weak, assessor_strong], W, H)   # streak 1
    t.select([patient_weak, assessor_strong], W, H)   # streak 2
    result = t.select([patient_weak, assessor_strong], W, H)  # streak 3 -> switch
    assert result.pose is assessor_strong
    assert t.n_switches == 1

    # Lock is now on the (former) assessor's position; a subsequent frame
    # with the same two poses continues tracking it without re-switching.
    result2 = t.select([patient_weak, assessor_strong], W, H)
    assert result2.pose is assessor_strong
    assert t.n_switches == 1


def test_frame_counter_increments_every_call():
    t = _tracker()
    t.select([ASSESSOR, PATIENT], W, H)
    t.select([], W, H)
    t.select([PATIENT], W, H)
    assert t.n_frames == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_patient_identity_tracker.py -v`
Expected: FAIL with `AttributeError: module 'patient_identity_tracker' has no attribute 'PatientIdentityTracker'` (Task 1's tests still pass)

- [ ] **Step 3: Write the implementation**

Append to `patient_identity_tracker.py`:

```python
SelectionResult = namedtuple("SelectionResult", ["pose", "score", "ambiguous"])


class PatientIdentityTracker:
    """Stateful per-trial identity tracker. One instance per trial video."""

    def __init__(self, hip_idx, knee_idx, ankle_idx,
                 hysteresis_frames: int = DEFAULT_HYSTERESIS_FRAMES,
                 confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR):
        self._hip_idx = hip_idx
        self._knee_idx = knee_idx
        self._ankle_idx = ankle_idx
        self._hysteresis_frames = hysteresis_frames
        self._confidence_floor = confidence_floor
        self._locked_knee_px = None
        self._challenger_streak = 0
        self.n_switches = 0
        self.n_ambiguous = 0
        self.n_frames = 0

    def _knee_px(self, pose, w, h):
        lm = pose[self._knee_idx]
        return (lm.x * w, lm.y * h)

    def _geometric_score(self, pose, w, h) -> float:
        horiz = _trunk_horizontal_score(pose)
        vis = _visibility_score(pose, self._hip_idx, self._knee_idx, self._ankle_idx)
        anat = _anatomical_penalty(pose, self._hip_idx, self._knee_idx,
                                    self._ankle_idx, w, h)
        return ((horiz + vis) / 2.0) * anat

    def select(self, poses, w, h) -> "SelectionResult":
        self.n_frames += 1

        if not poses:
            self.n_ambiguous += 1
            return SelectionResult(None, 0.0, True)

        if len(poses) == 1:
            pose = poses[0]
            score = self._geometric_score(pose, w, h)
            if score < self._confidence_floor:
                self.n_ambiguous += 1
                return SelectionResult(None, score, True)
            self._challenger_streak = 0
            self._locked_knee_px = self._knee_px(pose, w, h)
            return SelectionResult(pose, score, False)

        # len(poses) == 2: MediaPipe options cap num_poses at 2 upstream.
        if self._locked_knee_px is None:
            scored = sorted(
                ((self._geometric_score(p, w, h), p) for p in poses),
                key=lambda t: t[0], reverse=True,
            )
            best_score, best_pose = scored[0]
            if best_score < self._confidence_floor:
                self.n_ambiguous += 1
                return SelectionResult(None, best_score, True)
            self._challenger_streak = 0
            self._locked_knee_px = self._knee_px(best_pose, w, h)
            return SelectionResult(best_pose, best_score, False)

        dists = sorted(
            ((math.hypot(*(a - b for a, b in
                           zip(self._knee_px(p, w, h), self._locked_knee_px))), p)
             for p in poses),
            key=lambda t: t[0],
        )
        tracked_pose, challenger_pose = dists[0][1], dists[1][1]
        tracked_score = self._geometric_score(tracked_pose, w, h)
        challenger_score = self._geometric_score(challenger_pose, w, h)

        if challenger_score > tracked_score:
            self._challenger_streak += 1
        else:
            self._challenger_streak = 0

        if self._challenger_streak >= self._hysteresis_frames:
            selected, score = challenger_pose, challenger_score
            self._challenger_streak = 0
            self.n_switches += 1
        else:
            selected, score = tracked_pose, tracked_score

        if score < self._confidence_floor:
            self.n_ambiguous += 1
            return SelectionResult(None, score, True)

        self._locked_knee_px = self._knee_px(selected, w, h)
        return SelectionResult(selected, score, False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_patient_identity_tracker.py -v`
Expected: PASS (17 passed — 7 from Task 1 + 10 from Task 2)

- [ ] **Step 5: Commit**

```bash
git add patient_identity_tracker.py tests/test_patient_identity_tracker.py
git commit -m "feat: add PatientIdentityTracker hysteresis-gated state machine"
```

---

### Task 3: Wire the tracker into `batch_mediapipe.py`

**Files:**
- Modify: `batch_mediapipe.py:36-56` (add `CSV_FIELDNAMES` constant), `batch_mediapipe.py:120-171` (`discover_new_trials` gets a `force` param), `batch_mediapipe.py:201-352` (`process_trial` uses the tracker), `batch_mediapipe.py:357-422` (`main` gets `--force`)
- Test: `tests/test_batch_mediapipe.py`

**Interfaces:**
- Consumes: `patient_identity_tracker.PatientIdentityTracker`, `patient_identity_tracker.SelectionResult` from Task 2.
- Produces: `batch_mediapipe.CSV_FIELDNAMES` (module-level list, replaces the `fieldnames` local var), `batch_mediapipe.discover_new_trials(force: bool = False)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_batch_mediapipe.py`:

```python
# ── CSV_FIELDNAMES ───────────────────────────────────────────────────────────

def test_csv_fieldnames_includes_identity_columns():
    assert "identity_score" in bm.CSV_FIELDNAMES
    assert "identity_ambiguous" in bm.CSV_FIELDNAMES
    # Existing columns must still be present -- additive only, per the
    # design's CSV backward-compatibility constraint.
    for col in ("frame", "time_sec", "leg", "hip_x", "hip_y", "knee_x",
                "knee_y", "ankle_x", "ankle_y", "hip_score", "knee_score",
                "ankle_score", "knee_angle_deg"):
        assert col in bm.CSV_FIELDNAMES


# ── discover_new_trials(force=...) ───────────────────────────────────────────

def _write_bytes(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_discover_new_trials_skips_existing_by_default(tmp_path, monkeypatch):
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    monkeypatch.setattr(bm, "OPTI_ROOT", opti_root)
    monkeypatch.setattr(bm, "REC_ROOT", rec_root)

    trial_dir = opti_root / "Participant_99" / "Right" / "pre"
    _write_bytes(trial_dir / "Trial_1_optitrack.csv")
    _write_bytes(trial_dir / "Trial_1.avi")
    _write_bytes(trial_dir / "Participant_99_T_1_mediapipe_full_0.5.csv")
    _write_bytes(trial_dir / "Participant_99_T_1_mediapipe_full_0.5_annotated.mp4")

    trials = list(bm.discover_new_trials(force=False))
    assert trials == []


def test_discover_new_trials_force_reprocesses_existing(tmp_path, monkeypatch):
    opti_root = tmp_path / "OptiTrack_Recordings"
    rec_root = tmp_path / "Recordings"
    monkeypatch.setattr(bm, "OPTI_ROOT", opti_root)
    monkeypatch.setattr(bm, "REC_ROOT", rec_root)

    trial_dir = opti_root / "Participant_99" / "Right" / "pre"
    _write_bytes(trial_dir / "Trial_1_optitrack.csv")
    _write_bytes(trial_dir / "Trial_1.avi")
    _write_bytes(trial_dir / "Participant_99_T_1_mediapipe_full_0.5.csv")
    _write_bytes(trial_dir / "Participant_99_T_1_mediapipe_full_0.5_annotated.mp4")

    trials = list(bm.discover_new_trials(force=True))
    assert len(trials) == 1
    assert trials[0]["participant"] == "Participant_99"
    assert trials[0]["trial_n"] == 1
    assert trials[0]["need_csv"] is True
    assert trials[0]["need_video"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_mediapipe.py -v -k "csv_fieldnames or discover_new_trials"`
Expected: FAIL — `AttributeError: module 'batch_mediapipe' has no attribute 'CSV_FIELDNAMES'`, and the force tests fail with `TypeError: discover_new_trials() got an unexpected keyword argument 'force'`

- [ ] **Step 3: Write the implementation**

In `batch_mediapipe.py`, add the import near the top (after the existing `import mediapipe as mp` line):

```python
import patient_identity_tracker as pit
```

After the `VIS_THRESH = 0.40` line (line 47), add:

```python
CSV_FIELDNAMES = ["frame", "time_sec", "leg",
                   "hip_x", "hip_y", "knee_x", "knee_y", "ankle_x", "ankle_y",
                   "hip_score", "knee_score", "ankle_score", "knee_angle_deg",
                   "identity_score", "identity_ambiguous"]
```

Replace `discover_new_trials()`'s signature and the `has_csv`/`has_vid` lines (original lines 120, 151-153):

```python
def discover_new_trials(force: bool = False):
    """
    Yield dicts for trials that are missing a CSV, an annotated video, or both.
    Each dict carries 'need_csv' and 'need_video' flags so process_trial knows
    which outputs to write. force=True treats every discovered trial as
    needing both, regardless of what already exists on disk.
    """
```

```python
        has_csv = (not force) and (_has_mediapipe_csv(vid_dir, trial_n) or
                                    _has_mediapipe_csv(opti_dir, trial_n))
        has_vid = (not force) and _has_annotated_video(vid_dir, trial_n)
```

Replace the `if result.pose_landmarks:` block inside `process_trial()`'s frame loop (original lines 251-286) — first, instantiate the tracker once before the `while True:` loop (right after the `vid_writer` setup, alongside `rows = []`, `mp_hits = 0`, `frame_idx = 0`):

```python
    tracker = pit.PatientIdentityTracker(h_idx, k_idx, a_idx)
```

Then replace the pose-selection block:

```python
            try:
                rgb      = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result   = landmarker.detect(mp_image)

                sel = tracker.select(result.pose_landmarks or [], w, h)
                lms = sel.pose
                identity_score = sel.score
                identity_ambiguous = sel.ambiguous

                if lms is not None:
                    hl  = lms[h_idx]; kl = lms[k_idx]; al = lms[a_idx]

                    hip_s = float(hl.visibility)
                    kne_s = float(kl.visibility)
                    ank_s = float(al.visibility)

                    if hip_s > VIS_THRESH and kne_s > VIS_THRESH:
                        hip_x = hl.x * w; hip_y = hl.y * h
                        kne_x = kl.x * w; kne_y = kl.y * h

                        if ank_s > VIS_THRESH:
                            ank_x = al.x * w; ank_y = al.y * h

                            v1 = np.array([hip_x - kne_x, hip_y - kne_y])
                            v2 = np.array([ank_x - kne_x, ank_y - kne_y])
                            n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
                            if n1 > 1e-6 and n2 > 1e-6:
                                cos_a = float(np.clip(
                                    np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
                                angle = float(np.degrees(np.arccos(cos_a)))
                                mp_hits += 1

            except Exception:
                identity_score = float("nan")
                identity_ambiguous = True
                pass
```

Note: `identity_score`/`identity_ambiguous` must be initialized before the `try` block alongside the other per-frame defaults (`hip_x = hip_y = ... = float("nan")` line) so they're defined even when `result.pose_landmarks` detection raises. Add to that existing initializer line:

```python
            identity_score = float("nan")
            identity_ambiguous = True
```

Add the two new fields to the `rows.append({...})` dict (original lines 309-323):

```python
                "identity_score":     round(identity_score, 4) if np.isfinite(identity_score) else "",
                "identity_ambiguous": identity_ambiguous,
```

Replace the CSV-writing block's local `fieldnames` variable (original lines 338-344) to use the module constant:

```python
    if need_csv:
        out_name = f"{participant}_T_{trial_n}_mediapipe_full_0.5.csv"
        out_path = vid_dir / out_name
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        print(f"  CSV   -> {out_name}")
```

Add the identity summary line after the existing `MediaPipe: {mp_hits}/{len(rows)} frames` print (original line 348):

```python
    pct = 100 * mp_hits // max(len(rows), 1)
    print(f"  MediaPipe: {mp_hits}/{len(rows)} frames ({pct}%)")
    print(f"  Identity: {tracker.n_switches} switches, "
          f"{tracker.n_ambiguous}/{len(rows)} ambiguous frames")
```

Add the `--force` CLI flag in `main()` (alongside the existing `--leg`/`--dry-run` args, original lines 360-364):

```python
    ap.add_argument("--force", action="store_true",
                    help="Reprocess trials even if CSV/annotated video already exist")
```

And pass it through to discovery (original line 372):

```python
    trials = list(discover_new_trials(force=args.force))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_batch_mediapipe.py tests/test_patient_identity_tracker.py -v`
Expected: PASS, all tests including the pre-existing `_select_patient_pose`/`_leg_from_name` tests (unmodified, still passing)

- [ ] **Step 5: Commit**

```bash
git add batch_mediapipe.py tests/test_batch_mediapipe.py
git commit -m "feat: wire PatientIdentityTracker into batch_mediapipe.py, add --force flag"
```

---

### Task 4: Real-data verification and full regression

**Files:** none (verification only, plus the RMSE output files it naturally produces)

**Interfaces:** none — this task consumes the finished pipeline from Tasks 1-3 and produces evidence (RMSE numbers, spot-checked video) that it fixed the confirmed problem.

- [ ] **Step 1: Run the full existing test suite**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: PASS, no regressions in any other test file (matches this repo's convention of a full-suite check after cross-cutting changes, e.g. the MS-vs-Control cohort work).

- [ ] **Step 2: Identify trials with known/suspected wrong-person tracking**

Review the annotated videos already flagged as showing wrong-person tracking (the ones that motivated this work). Note their `Participant_<ID>/<Right|Left>/<characterization>/Trial_<N>` paths.

- [ ] **Step 3: Reprocess those trials with `--force`**

Run: `.venv\Scripts\python.exe batch_mediapipe.py --force`

(If only specific participants should be reprocessed rather than the whole discoverable set, temporarily narrow `discover_new_trials()`'s OptiTrack scan or delete-and-let-default-discovery-pick-up-just those trials' existing CSV/video instead of using `--force` on everything — either is acceptable; `--force` on the full set is simplest if the affected trial list is small enough to re-run entirely.)

- [ ] **Step 4: Visually spot-check the reprocessed annotated videos**

Open the regenerated `*_annotated.mp4` files for the previously-bad trials and confirm the skeleton overlay now tracks the reclining patient's leg throughout, not the assessor's.

- [ ] **Step 5: Compare RMSE before/after**

Run the existing RMSE evaluation (whichever of `batch_imu_vs_optitrack_rmse.py` / `model_vs_optitrack_eval.py` covers MediaPipe-vs-OptiTrack for the affected participants) and compare against the RMSE numbers recorded before this change, for the same trials. Confirm RMSE has measurably decreased on trials that previously showed wrong-person tracking, and has not regressed on trials that were already tracking correctly.

- [ ] **Step 6: Check the new identity-log columns for anything unexpected**

Skim `identity_score`/`identity_ambiguous` in a few regenerated CSVs (and the `Identity: N switches, M/T ambiguous frames` stdout summary from Task 3) — a trial with a very high ambiguous-frame count or repeated switching is worth a manual look even if its RMSE happens to look fine, since it's a signal the geometric scorer is struggling on that trial.

- [ ] **Step 7: Commit any regenerated output artifacts that are tracked in git**

Check `git status` — `Recordings/**` and `OptiTrack_Recordings/**` outputs are gitignored per this repo's hygiene conventions (see root `CLAUDE.md` / the nightly hygiene tool), so regenerated trial CSVs/videos should **not** need a commit. If `git status` shows anything unexpected tracked here, stop and check before committing — do not blindly `git add` recording output.
