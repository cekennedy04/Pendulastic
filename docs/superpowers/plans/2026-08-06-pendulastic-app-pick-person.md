# Pendulastic App Pick-Person Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user disambiguate which detected person is the patient before HPE tracking runs on an uploaded video, fixing the "MediaPipe gets confused with two people in frame" bug by replacing the automatic YOLO-based patient guess with a correct one-time seed.

**Architecture:** Extract the viewer's "Pick Person" mechanics (`draw_person_select_overlay`, `resolve_person_click`) into shared, reusable module-level functions in `pendulastic_viewer.py`, same pattern as the prior annotation port. `BiomechanicalEngine` gains a `detect_people_at_frame` method (single-frame, `num_poses=4` MediaPipe Tasks detection) and an optional `manual_seed` parameter on `run_offline_track` that skips the existing per-frame auto-detect loop when provided. `PostProcessingPanel` gains a new modal `PersonPickerDialog` (0 people → automatic fallback unchanged; 1 person → seed extracted directly, no dialog; 2+ people → dialog shown) wired into `_on_upload_video` before the background tracking thread starts.

**Tech Stack:** Python 3.13, Tkinter, MediaPipe Tasks API (`mediapipe.tasks.vision.PoseLandmarker`, `IMAGE` running mode), OpenCV (`cv2`), PIL (`Image`/`ImageTk` for Tkinter image display), `pytest` with headless `tk.Tk()` + `.withdraw()` roots (existing convention).

## Global Constraints

- Follow the existing guarded-import fallback pattern in `pendulastic_app.py` for every new cross-module import — never let a missing optional dependency crash the app at startup.
- Follow the existing background-thread + `self.after(0, ...)` pattern for any UI update triggered from a worker thread — never touch Tkinter widgets directly from a non-UI thread.
- No new third-party dependencies — `mediapipe`, `cv2`, and `PIL` are already used elsewhere in this repo (`pendulastic_viewer.py`, `pendulastic_workbench.py`).
- Preserve `run_offline_track`'s and `_add_hpe_overlay`'s existing signatures/return types for every caller that doesn't opt into the new parameters.
- `pendulastic_viewer.py`'s `_draw_person_select_overlay`/`_on_person_select_click` behavior must be unchanged after extraction (same "no viewer regression" bar as the prior plan's Task 1) — including the existing knee-headstart behavior on a rejected ankle, which the shared function's return contract must preserve (see Task 1).
- This app is Windows-only (confirmed: `OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS` Windows Media Foundation handling, `.venv\Scripts\` paths throughout) — no macOS-specific handling needed anywhere in this plan.
- Run tests with `.venv\Scripts\pytest tests\<file> -v` convention (Windows venv already present at the repo root).

---

### Task 1: Extract `draw_person_select_overlay` and `resolve_person_click` into `pendulastic_viewer.py`

**Files:**
- Modify: `pendulastic_viewer.py:1707` (insert new functions), `pendulastic_viewer.py:3830-3882` (`_PS_COLORS`/`_PS_CONNECTIONS`/`_draw_person_select_overlay`), `pendulastic_viewer.py:5997-6098` (`_on_person_select_click`)
- Test: `tests/test_person_select.py` (new)

**Interfaces:**
- Produces: `draw_person_select_overlay(frame: np.ndarray, poses: list) -> np.ndarray` — draws every candidate's skeleton in a distinct numbered color plus an instruction banner. `resolve_person_click(poses: list, click_xy, frame_w: int, frame_h: int, leg: str) -> tuple | None` — finds the nearest candidate pose to a click, resolves the requested screen-side leg (mirroring-aware), and returns `(hip, knee, ankle)` as float32 pixel-coordinate `np.ndarray`s. Returns `None` only when no pose is found near the click at all. When a pose is found but its ankle visibility is below 0.35, `ankle` is `None` **within** the returned tuple (hip and knee are still populated) — the tuple itself is never `None` in that case. Both are consumed by Tasks 4 and 5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_select.py`:

```python
# tests/test_person_select.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
from pendulastic_viewer import draw_person_select_overlay, resolve_person_click


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _make_pose(knee_x=0.5, ankle_vis=1.0):
    """A 33-point BlazePose-shaped landmark list with both anatomical sides
    at the same position -- adequate for tests that don't care about
    left/right mirroring, only nearest-pose search and ankle visibility."""
    lm = [_LM(0.5, 0.5) for _ in range(33)]
    lm[23] = _LM(knee_x - 0.02, 0.30)
    lm[25] = _LM(knee_x, 0.55)
    lm[27] = _LM(knee_x, 0.85, ankle_vis)
    lm[24] = _LM(knee_x - 0.02, 0.30)
    lm[26] = _LM(knee_x, 0.55)
    lm[28] = _LM(knee_x, 0.85, ankle_vis)
    return lm


def _make_pose_with_sides(left_knee_x, right_knee_x, ankle_vis=1.0):
    """Distinct anatomical-left vs -right knee x-positions, for testing the
    mirroring-aware leg-to-screen-side mapping."""
    lm = [_LM(0.5, 0.5) for _ in range(33)]
    lm[23] = _LM(left_knee_x - 0.02, 0.30)
    lm[25] = _LM(left_knee_x, 0.55)
    lm[27] = _LM(left_knee_x, 0.85, ankle_vis)
    lm[24] = _LM(right_knee_x - 0.02, 0.30)
    lm[26] = _LM(right_knee_x, 0.55)
    lm[28] = _LM(right_knee_x, 0.85, ankle_vis)
    return lm


def test_draw_person_select_overlay_draws_numbered_badges():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.3), _make_pose(0.7)]
    out = draw_person_select_overlay(frame, poses)
    assert out.shape == frame.shape
    assert not np.array_equal(out, frame)


def test_resolve_person_click_returns_none_for_no_poses():
    assert resolve_person_click([], (100, 100), 640, 480, "right") is None


def test_resolve_person_click_picks_nearest_pose():
    poses = [_make_pose(0.2), _make_pose(0.8)]
    result = resolve_person_click(poses, (0.8 * 640, 0.55 * 480), 640, 480, "right")
    assert result is not None
    hip, knee, ankle = result
    assert abs(float(knee[0]) - 0.8 * 640) < 5.0


def test_resolve_person_click_rejects_low_visibility_ankle_but_keeps_knee():
    poses = [_make_pose(0.5, ankle_vis=0.1)]
    result = resolve_person_click(poses, (0.5 * 640, 0.55 * 480), 640, 480, "right")
    assert result is not None
    hip, knee, ankle = result
    assert knee is not None
    assert ankle is None


def test_resolve_person_click_accepts_valid_ankle():
    poses = [_make_pose(0.5, ankle_vis=0.9)]
    result = resolve_person_click(poses, (0.5 * 640, 0.55 * 480), 640, 480, "right")
    assert result is not None
    hip, knee, ankle = result
    assert ankle is not None
    assert abs(float(ankle[0]) - 0.5 * 640) < 5.0


def test_resolve_person_click_maps_screen_left_when_not_mirrored():
    # anatomical left knee is on the left of the image (0.2 < 0.8).
    poses = [_make_pose_with_sides(left_knee_x=0.2, right_knee_x=0.8)]
    result = resolve_person_click(poses, (0.2 * 640, 0.55 * 480), 640, 480, "left")
    assert result is not None
    hip, knee, ankle = result
    assert abs(float(knee[0]) - 0.2 * 640) < 5.0


def test_resolve_person_click_maps_screen_left_when_mirrored():
    # anatomical left knee is on the RIGHT of the image (0.8 > 0.2) --
    # patient facing the camera, so "screen-left" is the anatomical right leg.
    poses = [_make_pose_with_sides(left_knee_x=0.8, right_knee_x=0.2)]
    result = resolve_person_click(poses, (0.8 * 640, 0.55 * 480), 640, 480, "left")
    assert result is not None
    hip, knee, ankle = result
    assert abs(float(knee[0]) - 0.2 * 640) < 5.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_person_select.py -v`
Expected: FAIL with `ImportError: cannot import name 'draw_person_select_overlay'`.

- [ ] **Step 3: Add the two module-level functions to `pendulastic_viewer.py`**

Insert immediately after the `_draw` function (`pendulastic_viewer.py:1707`, right before the `# ─── main application ───` divider):

```python
# ─── person-select (multi-patient disambiguation) ────────────────────────────

_PS_COLORS = [
    (0, 230, 150),   # person 1 — cyan-green
    (0, 130, 255),   # person 2 — orange
    (220,  50, 220), # person 3 — magenta
    (50,  220, 255), # person 4 — yellow
]
_PS_CONNECTIONS = [
    (11, 12), (11, 23), (12, 24), (23, 24),
    (23, 25), (24, 26), (25, 27), (26, 28),
]


def draw_person_select_overlay(frame: np.ndarray, poses: list) -> np.ndarray:
    """Draw every candidate's skeleton in a distinct numbered color plus an
    instruction banner.

    Landmark positions are 0-1 fractions, so multiplying by the frame
    dimensions works regardless of display scale.
    """
    out = frame.copy()
    h, w = out.shape[:2]

    for i, lm_set in enumerate(poses):
        color = _PS_COLORS[i % len(_PS_COLORS)]
        pts   = [(int(lm.x * w), int(lm.y * h)) for lm in lm_set]

        for a, b in _PS_CONNECTIONS:
            if a < len(pts) and b < len(pts):
                cv2.line(out, pts[a], pts[b], color, 2, cv2.LINE_AA)

        for pt in pts:
            cv2.circle(out, pt, 5, color, -1, cv2.LINE_AA)
            cv2.circle(out, pt, 6, (0, 0, 0), 1, cv2.LINE_AA)

        # Numbered badge above mid-hip
        if len(pts) > 24:
            mx = (pts[23][0] + pts[24][0]) // 2
            my = (pts[23][1] + pts[24][1]) // 2 - 28
        elif pts:
            mx, my = pts[0][0], pts[0][1] - 28
        else:
            continue
        cv2.circle(out, (mx, my), 18, (10, 10, 10), -1, cv2.LINE_AA)
        cv2.circle(out, (mx, my), 18, color, 2,  cv2.LINE_AA)
        cv2.putText(out, str(i + 1), (mx - 7, my + 7),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

    # Instruction banner
    cv2.rectangle(out, (0, 0), (w, 44), (10, 10, 30), -1)
    n = len(poses)
    cv2.putText(
        out,
        f"MediaPipe detected {n} person(s)  —  CLICK the PATIENT",
        (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 230, 130), 2, cv2.LINE_AA)
    return out


def resolve_person_click(poses: list, click_xy, frame_w: int, frame_h: int,
                          leg: str):
    """Find the candidate pose nearest a click point (checking all landmarks
    of all poses), resolve which anatomical leg maps to the requested screen
    side, and return (hip, knee, ankle) pixel coordinates.

    Returns None only when no candidate pose is found near the click at
    all. When a pose is found but its ankle visibility is below 0.35,
    ankle is None within the returned tuple (hip and knee are still
    returned) rather than the whole result being None -- this preserves
    the "place the knee as a head start, ankle stays for manual
    placement" behavior callers may support.
    """
    if not poses:
        return None

    click = np.array(click_xy, dtype=np.float32)
    fw, fh = frame_w, frame_h

    # Find the person whose ANY landmark is nearest the click
    best_set  = None
    best_dist = float("inf")
    for lm_set in poses:
        for lm in lm_set:
            pt = np.array([lm.x * fw, lm.y * fh], dtype=np.float32)
            d  = float(np.linalg.norm(pt - click))
            if d < best_dist:
                best_dist = d
                best_set  = lm_set

    if best_set is None:
        return None

    # Pick the leg that matches the requested screen side.
    # BlazePose indices: anatomical LEFT hip=23,knee=25,ankle=27
    #                    anatomical RIGHT hip=24,knee=26,ankle=28
    # IMPORTANT: anatomical left/right is NOT the same as image left/right.
    # If the patient faces the camera their anatomical left appears on the
    # RIGHT side of the image (mirrored). Map by screen-space x-position so
    # "left"/"right" here means "the leg on that side of the screen."
    lh, lk, la = best_set[23], best_set[25], best_set[27]  # anatomical left
    rh, rk, ra = best_set[24], best_set[26], best_set[28]  # anatomical right

    want_image_left = (leg.lower() == "left")
    anat_left_is_img_left = (lk.x <= rk.x)

    if anat_left_is_img_left:
        img_left  = (lh, lk, la)
        img_right = (rh, rk, ra)
    else:
        img_left  = (rh, rk, ra)
        img_right = (lh, lk, la)

    h_lm, k_lm, a_lm = img_left if want_image_left else img_right

    hip  = np.array([h_lm.x * fw, h_lm.y * fh], dtype=np.float32)
    knee = np.array([k_lm.x * fw, k_lm.y * fh], dtype=np.float32)

    # Reject ankle if visibility is too low — a hallucinated ankle seeds the
    # tracker at a wrong position and the tracker carries that error forward.
    ankle = None
    if a_lm.visibility >= 0.35:
        ankle = np.array([a_lm.x * fw, a_lm.y * fh], dtype=np.float32)

    return (hip, knee, ankle)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_person_select.py -v`
Expected: 7 passed.

- [ ] **Step 5: Refactor the viewer's class methods to call the shared functions**

Replace `pendulastic_viewer.py:3830-3882` (the `_PS_COLORS`/`_PS_CONNECTIONS` class attributes and the full `_draw_person_select_overlay` method body) with:

```python
    def _draw_person_select_overlay(self, frame: np.ndarray) -> np.ndarray:
        return draw_person_select_overlay(frame, self._person_select_poses)
```

Replace `pendulastic_viewer.py:5997-6098` (the full `_on_person_select_click` method) with:

```python
    def _on_person_select_click(self, nx: float, ny: float):
        """Handle a click during person-select mode.

        Finds the detected person nearest to the click (checking all landmarks),
        picks whichever leg is more visible, sets hip/knee/ankle, and inits tracker.
        """
        poses = self._person_select_poses
        if not poses:
            self._person_select_active = False
            return

        frame = self._get_frame(self._frame_idx)
        if frame is None:
            return
        fh, fw = frame.shape[:2]

        result = resolve_person_click(poses, (nx, ny), fw, fh, self._leg_var.get())
        if result is None:
            return

        hip, kne, ank = result

        if ank is None:
            self._status.config(
                text="Ankle visibility too low — "
                     "place Knee marker then click Ankle manually.")
            # Still place the knee so the user has a head start
            self._knee_click = tuple(float(v) for v in kne)
            self._person_select_active = False
            self._person_select_poses  = []
            self._show_frame_idx(self._frame_idx)
            return

        self._hip_click   = tuple(float(v) for v in hip)
        self._knee_click  = tuple(float(v) for v in kne)
        self._ankle_click = tuple(float(v) for v in ank)

        self.tracker.init(frame, hip, kne, ank)
        _, _, _, ang0 = self.tracker.step(frame)
        self._angles[self._frame_idx] = ang0

        # Exit person-select mode
        self._person_select_active = False
        self._person_select_poses  = []

        side = self._leg_var.get().capitalize()
        self._show_frame_idx(self._frame_idx)
        self._status.config(
            text=f"{side} leg selected — shank={self.tracker.shank_len:.0f}px  "
                 f"angle={ang0:.1f} deg  →  Press Track All.")
```

Two intentional, minor simplifications from the original (called out so they aren't mistaken for bugs): the `print("DEBUG ...")` statements are dropped (they were console-only debug noise, not behavior), and the success status message no longer includes the numeric ankle-visibility readout (`a_lm.visibility` is internal to `resolve_person_click` now, not accessible in the wrapper) — purely cosmetic, no functional change.

- [ ] **Step 6: Smoke-test the viewer still imports cleanly**

Run: `.venv\Scripts\python.exe -c "import pendulastic_viewer"`
Expected: no exception.

- [ ] **Step 7: Commit**

```bash
git add pendulastic_viewer.py tests/test_person_select.py
git commit -m "feat: extract draw_person_select_overlay and resolve_person_click, share with viewer"
```

---

### Task 2: Add `detect_people_at_frame` to `BiomechanicalEngine`

**Files:**
- Modify: `pendulastic_app.py:65-73` (import — add `_MP_MODEL`), `pendulastic_app.py` (add method to `BiomechanicalEngine`, after `get_live_angle`)
- Test: `tests/test_biomechanical_engine.py`

**Interfaces:**
- Consumes: nothing new beyond what's already imported (`_VIEWER_AVAIL`, `_CV2_AVAIL`, `_cv2`, `_mp`, plus the new `_MP_MODEL` import from this task).
- Produces: `BiomechanicalEngine.detect_people_at_frame(self, video_path: str, frame_index: int = 0) -> tuple[np.ndarray | None, list]`. Returns `(None, [])` if the video can't be opened or `frame_index` can't be read (including past-end-of-clip). Returns `(frame, [])` if the frame was read but detection found nobody, or if detection itself raised an exception. Returns `(frame, poses)` on success. Never raises. Consumed by Tasks 4 and 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_biomechanical_engine.py` (needs `import types` added to the file's imports if not already present — check the top of the file first; the existing file imports `math, os, sys, types` already per its header, so `types` is already available):

```python
def test_detect_people_at_frame_returns_poses(tmp_path, monkeypatch):
    video_path = str(tmp_path / "people_test.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    for _ in range(3):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    fake_poses = [["pose1_landmarks"], ["pose2_landmarks"]]

    class _FakeDetector:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def detect(self, image):
            return types.SimpleNamespace(pose_landmarks=fake_poses)

    class _FakePoseLandmarker:
        @staticmethod
        def create_from_options(opts):
            return _FakeDetector()

    class _FakeRunningMode:
        IMAGE = "IMAGE"

    class _FakeVision:
        PoseLandmarkerOptions = staticmethod(lambda **kw: kw)
        PoseLandmarker = _FakePoseLandmarker
        RunningMode = _FakeRunningMode

    class _FakeTasks:
        vision = _FakeVision
        BaseOptions = staticmethod(lambda **kw: kw)

    fake_mp = types.SimpleNamespace(
        tasks=_FakeTasks,
        Image=lambda **kw: kw,
        ImageFormat=types.SimpleNamespace(SRGB="SRGB"),
    )

    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)
    monkeypatch.setattr(_app, "_mp", fake_mp)
    monkeypatch.setattr(_app, "_MP_MODEL", "fake_model_path")

    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame(video_path, frame_index=0)

    assert frame is not None
    assert frame.shape == (240, 320, 3)
    assert poses == fake_poses


def test_detect_people_at_frame_returns_empty_for_bad_video():
    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame("nonexistent_file.mp4")
    assert frame is None
    assert poses == []


def test_detect_people_at_frame_returns_empty_past_end_of_clip(tmp_path):
    video_path = str(tmp_path / "short.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame(video_path, frame_index=999)
    assert frame is None
    assert poses == []


def test_detect_people_at_frame_catches_detection_exception(tmp_path, monkeypatch):
    video_path = str(tmp_path / "people_test2.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    class _RaisingOptions:
        def __init__(self, **kw):
            raise RuntimeError("model load failed")

    class _RaisingVision:
        PoseLandmarkerOptions = _RaisingOptions

    class _RaisingTasks:
        vision = _RaisingVision
        BaseOptions = staticmethod(lambda **kw: kw)

    fake_mp = types.SimpleNamespace(tasks=_RaisingTasks)

    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)
    monkeypatch.setattr(_app, "_mp", fake_mp)
    monkeypatch.setattr(_app, "_MP_MODEL", "fake_model_path")

    engine = BiomechanicalEngine("rgb")
    frame, poses = engine.detect_people_at_frame(video_path)

    assert frame is not None    # frame WAS read successfully
    assert poses == []          # detection failure -> empty poses, no raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_biomechanical_engine.py -k detect_people_at_frame -v`
Expected: FAIL with `AttributeError: 'BiomechanicalEngine' object has no attribute 'detect_people_at_frame'`.

- [ ] **Step 3: Add the `_MP_MODEL` import and implement the method**

In `pendulastic_app.py:65-73`, extend the import:

```python
try:
    from pendulastic_viewer import (
        _MPBatchTracker, _PatientDetector, _draw, TRAIL_LEN, _MP_MODEL,
    )
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _draw = None
    TRAIL_LEN = 150
    _MP_MODEL = None
    _VIEWER_AVAIL = False
```

Add this method to `BiomechanicalEngine`, directly after `get_live_angle`:

```python
    def detect_people_at_frame(
        self, video_path: str, frame_index: int = 0,
    ) -> tuple:
        """
        Run MediaPipe PoseLandmarker (IMAGE mode, up to 4 candidates) on a
        single frame of video_path, for multi-person disambiguation before
        a full offline track.

        Returns (frame, poses):
          - frame: the raw BGR frame (np.ndarray) at frame_index, or None
            if the video couldn't be opened or that frame couldn't be
            read (including a frame_index past the end of the clip).
          - poses: a list of pose landmark sets (mediapipe's
            pose_landmarks result), or [] if detection found nobody, or
            detection itself raised an exception.

        Never raises -- any exception from running detection is caught
        internally and treated as "0 people found" so callers have one
        fallback path regardless of failure cause.
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return (None, [])

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap.release()
            return (None, [])

        try:
            cap.set(_cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
        finally:
            cap.release()

        if not ret or frame is None:
            return (None, [])

        try:
            V = _mp.tasks.vision
            opts = V.PoseLandmarkerOptions(
                base_options=_mp.tasks.BaseOptions(model_asset_path=_MP_MODEL),
                running_mode=V.RunningMode.IMAGE,
                num_poses=4,
                min_pose_detection_confidence=0.25,
                min_pose_presence_confidence=0.25,
            )
            with V.PoseLandmarker.create_from_options(opts) as detector:
                rgb    = _cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)
                result = detector.detect(
                    _mp.Image(image_format=_mp.ImageFormat.SRGB, data=rgb))
            poses = result.pose_landmarks or []
        except Exception:
            poses = []

        return (frame, poses)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_biomechanical_engine.py -v`
Expected: all pass, including the pre-existing tests (confirms no regression from the import change).

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_biomechanical_engine.py
git commit -m "feat: add detect_people_at_frame to BiomechanicalEngine"
```

---

### Task 3: Add `manual_seed` parameter to `run_offline_track`

**Files:**
- Modify: `pendulastic_app.py` — the `run_offline_track` method on `BiomechanicalEngine` (was at lines 195-276 before Task 2; Task 2 inserted `detect_people_at_frame` above it in the same class, so the line numbers have shifted down — locate it by searching for `def run_offline_track(`, don't trust a stale line number)
- Test: `tests/test_biomechanical_engine.py`

**Interfaces:**
- Produces: `BiomechanicalEngine.run_offline_track(video_path, progress_cb, leg="right", collect_landmarks=False, manual_seed=None)`. When `manual_seed` is a `(hip, knee, ankle)` triple, the method's existing per-frame `_PatientDetector` search is skipped — `tracker.init(frame, *manual_seed)` is called directly on the first frame read, then tracking proceeds exactly as before for every subsequent frame. When `manual_seed` is `None` (all existing callers, unchanged), behavior is completely unchanged. Consumed by Task 5.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_biomechanical_engine.py`:

```python
def test_run_offline_track_manual_seed_skips_patient_detector(tmp_path, monkeypatch):
    """When manual_seed is given, tracker.init is called with exactly that
    seed on the first frame -- _PatientDetector.detect must never be
    called at all."""
    video_path = str(tmp_path / "seed_test.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (320, 240))
    for _ in range(4):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    detector_called = {"count": 0}

    class FakeDetector:
        def detect(self, frame):
            detector_called["count"] += 1
            return None, None   # would normally block initialisation

    init_calls = []

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle):
            init_calls.append((hip, knee, ankle))
        def step(self, frame):
            return (10, 20), (30, 40), (50, 60), 155.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    seed = ((100.0, 60.0), (100.0, 120.0), (100.0, 200.0))
    engine = BiomechanicalEngine("rgb")
    angles = engine.run_offline_track(
        video_path, lambda p: None, leg="right", manual_seed=seed)

    assert detector_called["count"] == 0
    assert len(init_calls) == 1
    assert init_calls[0] == seed
    assert len(angles) == 4
    assert all(a == 155.0 for a in angles)


def test_run_offline_track_none_seed_unaffected(monkeypatch):
    """manual_seed defaulting to None must not change the existing
    auto-detect behavior or return type for any pre-existing caller."""
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", False)
    result = BiomechanicalEngine("rgb").run_offline_track(
        "nonexistent.mp4", lambda p: None, leg="right")
    assert result == []
    assert isinstance(result, list)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_biomechanical_engine.py -k manual_seed -v`
Expected: FAIL with `TypeError: run_offline_track() got an unexpected keyword argument 'manual_seed'`.

- [ ] **Step 3: Implement `manual_seed`**

Find and replace the full `run_offline_track` method (search for `def run_offline_track(` — its line number has shifted from where Task 2's diff shows it, since `detect_people_at_frame` was inserted above it in the same class; replace from that line through the final `cap.release()`/`return` at the method's end) with:

```python
    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
        collect_landmarks: bool = False,
        manual_seed: tuple | None = None,
    ):
        """
        Offline MediaPipe tracking on a recorded video.
        Called on a background thread immediately after STOP (RGB methodology).

        Tracker API (from pendulastic_viewer.py):
          _PatientDetector().detect(frame) -> (patient_kps: ndarray(17,2) | None, _)
          _MPBatchTracker(side, fps).init(frame, hip, knee, ankle)
          tracker.step(frame) -> (hip, knee, ankle, angle_deg)

        COCO indices used: 11=L-hip, 12=R-hip, 13=L-knee, 14=R-knee,
                           15=L-ankle, 16=R-ankle

        When collect_landmarks is True, returns (angles, landmarks, fps) where
        landmarks[i] is (hip, knee, ankle) for frame i, or None if pose
        tracking wasn't available for that frame -- len(landmarks) ==
        len(angles) always -- and fps is the video's true source frame rate.
        When False (default), returns angles only, matching the original
        signature exactly.

        manual_seed, when given as a (hip, knee, ankle) triple, skips the
        per-frame _PatientDetector search entirely -- the tracker is
        initialised from that seed on the first frame read instead. When
        None (default), behavior is unchanged from before this parameter
        existed.
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return ([], [], 30.0) if collect_landmarks else []

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return ([], [], 30.0) if collect_landmarks else []

        fps_v  = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        total  = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 1

        # COCO column offsets: right leg offset=1, left leg offset=0
        col    = 1 if leg.lower() == "right" else 0
        hip_i  = 11 + col   # 12 (right) or 11 (left)
        knee_i = 13 + col   # 14 (right) or 13 (left)
        ank_i  = 15 + col   # 16 (right) or 15 (left)

        detector     = _PatientDetector()
        tracker      = _MPBatchTracker(leg.lower(), fps=fps_v)
        initialised  = False
        angles: list = []
        landmarks: list = []

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if not initialised:
                    if manual_seed is not None:
                        hip, knee, ankle = manual_seed
                        tracker.init(frame, hip, knee, ankle)
                        initialised = True
                    else:
                        patient_kps, _ = detector.detect(frame)
                        if patient_kps is not None and patient_kps.shape[0] >= 17:
                            hip   = patient_kps[hip_i].astype(float)
                            knee  = patient_kps[knee_i].astype(float)
                            ankle = patient_kps[ank_i].astype(float)
                            tracker.init(frame, hip, knee, ankle)
                            initialised = True

                if initialised:
                    try:
                        hip_p, knee_p, ank_p, angle = tracker.step(frame)
                        angles.append(float(angle) if angle is not None
                                      else float("nan"))
                        if collect_landmarks:
                            landmarks.append((hip_p, knee_p, ank_p))
                    except Exception:
                        angles.append(float("nan"))
                        if collect_landmarks:
                            landmarks.append(None)
                else:
                    angles.append(float("nan"))
                    if collect_landmarks:
                        landmarks.append(None)

                progress_cb(len(angles) / total)
        finally:
            cap.release()

        progress_cb(1.0)
        return (angles, landmarks, fps_v) if collect_landmarks else angles
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_biomechanical_engine.py -v`
Expected: all pass, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_biomechanical_engine.py
git commit -m "feat: add manual_seed parameter to run_offline_track"
```

---

### Task 4: Implement `PersonPickerDialog`

**Files:**
- Modify: `pendulastic_app.py:55-63` (guarded PIL import — add directly after the `cv2`/`camera_utils` import block), `pendulastic_app.py:65-73` (extend the `pendulastic_viewer` import with `draw_person_select_overlay`/`resolve_person_click` — note this range is from before Task 2's edit; after Task 2 it's whatever immediately follows that block, still before line ~177 where `BiomechanicalEngine` starts, so unaffected by Tasks 2-3's edits further down), `pendulastic_app.py` (add new `PersonPickerDialog` class, placed directly above `class PostProcessingPanel` — locate by searching for `class PostProcessingPanel(tk.Frame):`)
- Test: `tests/test_person_picker_dialog.py` (new)

**Interfaces:**
- Consumes: `draw_person_select_overlay`, `resolve_person_click` (Task 1), `BiomechanicalEngine.detect_people_at_frame` (Task 2).
- Produces: `PersonPickerDialog(parent, video_path: str, frame_index: int, frame: np.ndarray, poses: list, leg: str)` — a `tk.Toplevel`. After construction, the caller calls `parent.wait_window(dialog)` to block until it closes; `dialog.result` is then either a `(hip, knee, ankle)` tuple (a successful pick) or `None` (cancelled, or resolution never succeeded). Consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_person_picker_dialog.py`:

```python
# tests/test_person_picker_dialog.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk
import numpy as np

_root_window = None

def _get_root():
    global _root_window
    if _root_window is None:
        _root_window = tk.Tk()
        _root_window.withdraw()
    return _root_window


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _make_pose(knee_x=0.5, ankle_vis=1.0):
    lm = [_LM(0.5, 0.5) for _ in range(33)]
    lm[23] = _LM(knee_x - 0.02, 0.30)
    lm[25] = _LM(knee_x, 0.55)
    lm[27] = _LM(knee_x, 0.85, ankle_vis)
    lm[24] = _LM(knee_x - 0.02, 0.30)
    lm[26] = _LM(knee_x, 0.55)
    lm[28] = _LM(knee_x, 0.85, ankle_vis)
    return lm


def test_dialog_scales_down_wide_frame_and_maps_click_back():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    wide_frame = np.zeros((720, 1800, 3), dtype=np.uint8)   # wider than 900px
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, wide_frame, poses, "right")

    assert dlg._scale == 900 / 1800

    display_x, display_y = 450, 360
    mapped_x = display_x / dlg._scale
    mapped_y = display_y / dlg._scale
    assert abs(mapped_x - 900) < 1.0
    assert abs(mapped_y - 720) < 1.0

    dlg.destroy()


def test_dialog_does_not_scale_narrow_frame():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    narrow_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, narrow_frame, poses, "right")
    assert dlg._scale == 1.0
    dlg.destroy()


def test_dialog_click_resolves_and_sets_result():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5, ankle_vis=0.9)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    class _FakeEvent:
        x = int(0.5 * 640)
        y = int(0.55 * 480)

    dlg._on_click(_FakeEvent())
    assert dlg.result is not None
    hip, knee, ankle = dlg.result
    assert ankle is not None
    assert not dlg.winfo_exists()   # dialog auto-destroys on a resolved click


def test_dialog_click_with_low_ankle_visibility_stays_open():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5, ankle_vis=0.1)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    class _FakeEvent:
        x = int(0.5 * 640)
        y = int(0.55 * 480)

    dlg._on_click(_FakeEvent())
    assert dlg.result is None
    assert dlg.winfo_exists()
    dlg.destroy()


def test_try_next_frame_advances_index_and_redraws(monkeypatch):
    from pendulastic_app import PersonPickerDialog
    import pendulastic_app as _app
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    calls = []
    new_frame = np.ones((480, 640, 3), dtype=np.uint8)
    new_poses = [_make_pose(0.3), _make_pose(0.7)]

    def _fake_detect(self, video_path, frame_index=0):
        calls.append(frame_index)
        return new_frame, new_poses

    monkeypatch.setattr(_app.BiomechanicalEngine, "detect_people_at_frame",
                         _fake_detect)

    dlg._on_try_next_frame()

    assert calls == [15]
    assert dlg._frame_index == 15
    assert dlg._poses == new_poses
    dlg.destroy()


def test_try_next_frame_disables_button_at_end_of_clip(monkeypatch):
    from pendulastic_app import PersonPickerDialog
    import pendulastic_app as _app
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    def _fake_detect_end(self, video_path, frame_index=0):
        return None, []

    monkeypatch.setattr(_app.BiomechanicalEngine, "detect_people_at_frame",
                         _fake_detect_end)

    dlg._on_try_next_frame()

    assert str(dlg.btn_next_frame["state"]) == "disabled"
    assert "end of clip" in dlg._status_var.get().lower()
    dlg.destroy()


def test_cancel_leaves_result_none():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")
    dlg.destroy()
    assert dlg.result is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_person_picker_dialog.py -v`
Expected: FAIL with `ImportError: cannot import name 'PersonPickerDialog'`.

- [ ] **Step 3: Add the PIL import and implement `PersonPickerDialog`**

In `pendulastic_app.py`, add a new guarded import block right after the existing `cv2`/`camera_utils` block (`pendulastic_app.py:55-63`):

```python
try:
    from PIL import Image, ImageTk
    _PIL_AVAIL = True
except Exception:
    Image = None
    ImageTk = None
    _PIL_AVAIL = False
```

Extend the `pendulastic_viewer` import (from Task 2's edit) to also pull in the two Task 1 functions:

```python
try:
    from pendulastic_viewer import (
        _MPBatchTracker, _PatientDetector, _draw, TRAIL_LEN, _MP_MODEL,
        draw_person_select_overlay, resolve_person_click,
    )
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _draw = None
    TRAIL_LEN = 150
    _MP_MODEL = None
    draw_person_select_overlay = None
    resolve_person_click = None
    _VIEWER_AVAIL = False
```

Add this class directly above `class PostProcessingPanel(tk.Frame):`:

```python
class PersonPickerDialog(tk.Toplevel):
    """Modal dialog: shows every MediaPipe-detected person in a frame with a
    numbered colored skeleton overlay and lets the user click the patient.

    On a resolved click, self.result is set to (hip, knee, ankle) pixel
    coordinates before the dialog closes. self.result stays None if the
    user cancels/closes the dialog without a successful resolution.
    """

    MAX_DISPLAY_WIDTH   = 900
    TRY_NEXT_FRAME_STEP = 15

    def __init__(self, parent, video_path: str, frame_index: int,
                 frame: np.ndarray, poses: list, leg: str) -> None:
        super().__init__(parent)
        self.title("Select the Patient")
        self.resizable(False, False)
        self.transient(parent)

        self._video_path  = video_path
        self._frame_index = frame_index
        self._frame        = frame
        self._poses         = poses
        self._leg           = leg
        self._engine         = BiomechanicalEngine("rgb")
        self._scale          = 1.0
        self.result: tuple | None = None

        self._status_var = tk.StringVar(
            value=f"MediaPipe detected {len(poses)} person(s) — click the PATIENT.")

        self._image_label = tk.Label(self, cursor="crosshair")
        self._image_label.pack()
        self._image_label.bind("<Button-1>", self._on_click)

        status_row = tk.Frame(self)
        status_row.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(status_row, textvariable=self._status_var,
                 anchor="w", wraplength=self.MAX_DISPLAY_WIDTH).pack(
            side="left", fill="x", expand=True)

        button_row = tk.Frame(self)
        button_row.pack(fill="x", padx=8, pady=8)
        self.btn_next_frame = tk.Button(
            button_row, text="Try Next Frame", command=self._on_try_next_frame)
        self.btn_next_frame.pack(side="left")
        tk.Button(button_row, text="Cancel", command=self.destroy).pack(
            side="right")

        self._render_frame()
        self.grab_set()

    def _render_frame(self) -> None:
        overlay = draw_person_select_overlay(self._frame, self._poses)
        h, w = overlay.shape[:2]
        if w > self.MAX_DISPLAY_WIDTH:
            self._scale = self.MAX_DISPLAY_WIDTH / w
            disp = _cv2.resize(overlay, (int(w * self._scale), int(h * self._scale)))
        else:
            self._scale = 1.0
            disp = overlay
        rgb = _cv2.cvtColor(disp, _cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(img)
        self._image_label.configure(image=self._photo)

    def _on_click(self, event) -> None:
        frame_h, frame_w = self._frame.shape[:2]
        click_xy = (event.x / self._scale, event.y / self._scale)
        result = resolve_person_click(
            self._poses, click_xy, frame_w, frame_h, self._leg)

        if result is None:
            self._status_var.set(
                "No detected person near that click — try clicking directly "
                "on a numbered skeleton.")
            return

        hip, knee, ankle = result
        if ankle is None:
            self._status_var.set(
                "Ankle visibility too low for that candidate — try clicking "
                "a different candidate, or Try Next Frame.")
            return

        self.result = (hip, knee, ankle)
        self.destroy()

    def _on_try_next_frame(self) -> None:
        next_index = self._frame_index + self.TRY_NEXT_FRAME_STEP
        frame, poses = self._engine.detect_people_at_frame(
            self._video_path, frame_index=next_index)
        if frame is None:
            self.btn_next_frame.config(state="disabled")
            self._status_var.set(
                "End of clip reached — try a different video.")
            return
        self._frame_index = next_index
        self._frame        = frame
        self._poses         = poses
        self._status_var.set(
            f"MediaPipe detected {len(poses)} person(s) — click the PATIENT.")
        self._render_frame()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_person_picker_dialog.py -v`
Expected: 7 passed.

- [ ] **Step 5: Run the broader suite for regressions**

Run: `.venv\Scripts\pytest tests\test_biomechanical_engine.py tests\test_person_select.py tests\test_person_picker_dialog.py tests\test_post_processing_panel.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_person_picker_dialog.py
git commit -m "feat: implement PersonPickerDialog"
```

---

### Task 5: Wire person-picking into `PostProcessingPanel._on_upload_video`

**Files:**
- Modify: `pendulastic_app.py` — the `_on_upload_video` method on `PostProcessingPanel` (was at lines 1377-1407 before Tasks 2-4; those tasks inserted a method and a whole new class earlier in the file, so the line numbers have shifted down substantially — locate it by searching for `def _on_upload_video(`, don't trust a stale line number)
- Test: `tests/test_post_processing_panel.py`

**Interfaces:**
- Consumes: `BiomechanicalEngine.detect_people_at_frame` (Task 2), `BiomechanicalEngine.run_offline_track(..., manual_seed=...)` (Task 3), `PersonPickerDialog` (Task 4), `resolve_person_click` (Task 1).
- Produces: the completed feature — `_on_upload_video`'s new branching behavior described below.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_post_processing_panel.py`:

```python
class _SyncThread:
    """Runs target() synchronously in start() -- makes _on_upload_video's
    background thread deterministic for testing without a real thread."""
    def __init__(self, target=None, daemon=None, **kw):
        self._target = target
    def start(self):
        self._target()


def test_on_upload_video_zero_people_uses_automatic_fallback(monkeypatch, tmp_path):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    video_path = str(tmp_path / "fake.mp4")
    monkeypatch.setattr(_app.filedialog, "askopenfilename", lambda **kw: video_path)
    monkeypatch.setattr(_app.threading, "Thread", _SyncThread)
    monkeypatch.setattr(
        _app.BiomechanicalEngine, "detect_people_at_frame",
        lambda self, path, frame_index=0: (None, []))

    captured = {}
    def _fake_run_offline_track(self, path, progress_cb, leg="right",
                                 collect_landmarks=False, manual_seed=None):
        captured["manual_seed"] = manual_seed
        progress_cb(1.0)
        return ([170.0] * 5, [None] * 5, 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    p._on_upload_video()
    r.update()

    assert captured["manual_seed"] is None
    assert "hpe_upload" in p._source_angles


def test_on_upload_video_one_person_computes_manual_seed(monkeypatch, tmp_path):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    import numpy as np
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    video_path = str(tmp_path / "fake.mp4")
    monkeypatch.setattr(_app.filedialog, "askopenfilename", lambda **kw: video_path)
    monkeypatch.setattr(_app.threading, "Thread", _SyncThread)

    class _LM:
        def __init__(self, x, y, visibility=1.0):
            self.x, self.y, self.visibility = x, y, visibility

    def _make_pose(knee_x=0.5, ankle_vis=0.9):
        lm = [_LM(0.5, 0.5) for _ in range(33)]
        lm[23] = _LM(knee_x - 0.02, 0.30)
        lm[25] = _LM(knee_x, 0.55)
        lm[27] = _LM(knee_x, 0.85, ankle_vis)
        lm[24] = _LM(knee_x - 0.02, 0.30)
        lm[26] = _LM(knee_x, 0.55)
        lm[28] = _LM(knee_x, 0.85, ankle_vis)
        return lm

    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]
    monkeypatch.setattr(
        _app.BiomechanicalEngine, "detect_people_at_frame",
        lambda self, path, frame_index=0: (fake_frame, fake_poses))

    captured = {}
    def _fake_run_offline_track(self, path, progress_cb, leg="right",
                                 collect_landmarks=False, manual_seed=None):
        captured["manual_seed"] = manual_seed
        progress_cb(1.0)
        return ([170.0] * 5, [None] * 5, 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    p._on_upload_video()
    r.update()

    assert captured["manual_seed"] is not None
    hip, knee, ankle = captured["manual_seed"]
    assert ankle is not None


def test_on_upload_video_two_people_cancelled_dialog_aborts_upload(monkeypatch, tmp_path):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    import numpy as np
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    video_path = str(tmp_path / "fake.mp4")
    monkeypatch.setattr(_app.filedialog, "askopenfilename", lambda **kw: video_path)
    monkeypatch.setattr(_app.threading, "Thread", _SyncThread)

    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    fake_poses = [["pose1"], ["pose2"]]
    monkeypatch.setattr(
        _app.BiomechanicalEngine, "detect_people_at_frame",
        lambda self, path, frame_index=0: (fake_frame, fake_poses))

    class _CancelledDialog:
        def __init__(self, *a, **kw):
            self.result = None
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _CancelledDialog)
    monkeypatch.setattr(p, "wait_window", lambda dlg: None)

    called = {"run_offline_track": False}
    def _fake_run_offline_track(self, *a, **kw):
        called["run_offline_track"] = True
        return ([], [], 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    p._on_upload_video()
    r.update()

    assert called["run_offline_track"] is False
    assert "cancel" in p.status_var.get().lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -k on_upload_video -v`
Expected: FAIL — the 1-person and 2-person tests fail because `manual_seed` is always `None`/because `PersonPickerDialog` is never referenced yet (`_on_upload_video` doesn't do any person-picking yet).

- [ ] **Step 3: Restructure `_on_upload_video`**

Find and replace the full `_on_upload_video` method (search for `def _on_upload_video(`) with:

```python
    def _on_upload_video(self) -> None:
        if not _VIEWER_AVAIL:
            messagebox.showerror(
                "HPE Unavailable",
                "pendulastic_viewer not importable — cannot run MediaPipe.")
            return
        path = filedialog.askopenfilename(
            title="Select video for HPE",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"),
                       ("All files", "*.*")])
        if not path:
            return

        leg    = self._meta.get("leg", "right") if self._meta else "right"
        engine = BiomechanicalEngine("rgb")

        self.status_var.set("Detecting people…")
        self.update_idletasks()
        frame, poses = engine.detect_people_at_frame(path)

        manual_seed = None
        if len(poses) == 1:
            # Only one candidate -- resolve_person_click's nearest-pose
            # search trivially picks it regardless of click position, so
            # any point in frame bounds works here; this reuses the same
            # leg-resolution/ankle-visibility logic as the 2+-person
            # disambiguation path below.
            fh, fw = frame.shape[:2]
            result = resolve_person_click(poses, (fw / 2, fh / 2), fw, fh, leg)
            if result is not None and result[2] is not None:
                manual_seed = result
        elif len(poses) >= 2:
            dialog = PersonPickerDialog(self, path, 0, frame, poses, leg)
            self.wait_window(dialog)
            if dialog.result is None:
                self.status_var.set("Upload cancelled — no patient selected.")
                return
            manual_seed = dialog.result

        self.status_var.set("HPE processing: 0%")
        self._video_path = path
        self._hpe_leg     = leg
        self._hpe_landmarks = None
        self._source_angles.pop("hpe_upload", None)
        self.btn_export_video.config(state="disabled")

        def _progress(pct: float) -> None:
            self.after(0, lambda p=pct: self.status_var.set(
                f"HPE processing: {int(p * 100)}%"))

        def _run() -> None:
            angles, landmarks, video_fps = engine.run_offline_track(
                path, _progress, leg=leg.lower(), collect_landmarks=True,
                manual_seed=manual_seed)
            self.after(0, lambda: self._add_hpe_overlay(angles, landmarks, fps=video_fps))

        threading.Thread(target=_run, daemon=True).start()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py -v`
Expected: all pass, including every pre-existing test in the file.

- [ ] **Step 5: Run the full test suite for regressions**

Run: `.venv\Scripts\pytest tests\test_post_processing_panel.py tests\test_biomechanical_engine.py tests\test_person_select.py tests\test_person_picker_dialog.py tests\test_pt_score.py -v`
Expected: all pass.

- [ ] **Step 6: Manual verification in the real app**

Run: `.venv\Scripts\python.exe pendulastic_app.py`
- Go to Upload/analysis mode, upload a video where only one person is visible — confirm no dialog appears and tracking proceeds directly.
- Upload a video with two people in frame (patient + assessor) — confirm the picker dialog appears with both people numbered and colored distinctly, click the correct one, and confirm tracking proceeds correctly for the rest of the video (no drift onto the assessor). Ideally use the exact footage that originally surfaced this problem.
- Try "Try Next Frame" on a frame where the ankle is occluded, and confirm it steps forward and redraws.
- Cancel the dialog and confirm the upload aborts cleanly with no tracking run and the export button stays disabled.
- This end-to-end check is manual because it depends on real MediaPipe tracking output and a real second person in frame — call this out explicitly rather than treating the automated tests above as full coverage of it (per the spec's Testing section).

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_post_processing_panel.py
git commit -m "feat: wire Pick Person into PostProcessingPanel upload flow"
```
