# In-App Annotated Video Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user see the MediaPipe-annotated video in-app right after HPE tracking finishes in `PostProcessingPanel`, correct which detected person is being tracked, and fix the resulting frames by retracking forward from a chosen point.

**Architecture:** A new module `video_review_dialog.py` provides `AnnotatedVideoReviewDialog(tk.Toplevel)`, a modal review dialog that scrubs/plays back precomputed angle+landmark data with a live skeleton overlay, and a "Fix Person Here" action that re-picks the tracked person at the current frame and retracks forward from there. `BiomechanicalEngine.run_offline_track()` gains a `start_frame` parameter to support the forward retrack. `PostProcessingPanel._add_hpe_overlay()` opens the dialog modally right after tracking finishes and reads back any corrections.

**Tech Stack:** Python, Tkinter, OpenCV (`cv2`), PIL/Pillow (`ImageTk`), pytest, existing `pendulastic_app.py`/`pendulastic_viewer.py` modules.

## Global Constraints

- Target surface is `pendulastic_app.py`'s `PostProcessingPanel` only — no changes to `pendulastic_viewer.py`, `web/frontend`, or the FastAPI backend (spec §2, §7).
- Fix mechanism is single-point repick-and-retrack-forward — no multi-pin arc interpolation (spec §2, §7).
- New dialog lives in a new file `video_review_dialog.py`, not added to `pendulastic_app.py` (spec §3.1).
- Reuse `_draw()`, `PersonPickerDialog`, `resolve_person_click`, `TRAIL_LEN` unchanged — no modifications to their signatures or behavior (spec §3.1).
- Any retrack splice must produce a result of exactly `len(old) - start_frame` length (pad with `nan`/`None` if short, truncate if long) — never a silent length mismatch (spec §4 point 1).
- The splice itself must happen on the Tk main thread via `self.after(0, ...)`, with scrubbing/playback paused (not just the Fix button disabled) while a retrack is in flight (spec §4 point 2).
- `run_offline_track`'s progress must reach `1.0` even when `start_frame > 0` (spec §3.3).
- `video_review_dialog.py` must import `PersonPickerDialog` from `pendulastic_app` lazily (inside the function that uses it, not at module top level) — `pendulastic_app.py` will import `AnnotatedVideoReviewDialog` from `video_review_dialog.py`, so a top-level import the other way is circular.

---

## Task 1: `run_offline_track(start_frame=...)`

**Files:**
- Modify: `pendulastic_app.py` (`BiomechanicalEngine.run_offline_track`, currently starting around line 235)
- Test: `tests/test_biomechanical_engine.py`

**Interfaces:**
- Consumes: nothing new — same `_PatientDetector`, `_MPBatchTracker`, `manual_seed` handling already in place.
- Produces: `BiomechanicalEngine.run_offline_track(video_path, progress_cb, leg="right", collect_landmarks=False, manual_seed=None, start_frame=0)`. When `start_frame > 0`, the video is seeked to that frame before tracking begins, and the returned `angles`/`landmarks` cover **only** the suffix from `start_frame` to the end (length `total_frames - start_frame`), not the full video. `progress_cb` still reaches exactly `1.0` on completion. Default `start_frame=0` preserves today's exact behavior (full video from frame 0).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_biomechanical_engine.py`:

```python
@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_start_frame_seeks_and_returns_suffix_only(tmp_path, monkeypatch):
    """start_frame=N seeks the video and returns only the N..end suffix,
    with progress still reaching 1.0."""
    import numpy as np

    video_path = str(tmp_path / "test_start_frame.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(10):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]
    kps[14] = [160, 120]
    kps[16] = [160, 200]

    class FakeDetector:
        def detect(self, frame):
            return kps, None

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    progress = []
    seed = (kps[12], kps[14], kps[16])
    angles, landmarks, fps = engine.run_offline_track(
        video_path, lambda p: progress.append(p), leg="right",
        collect_landmarks=True, manual_seed=seed, start_frame=4)

    assert len(angles) == 6          # 10 total frames - start_frame=4
    assert len(landmarks) == 6
    assert progress and progress[-1] == 1.0


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_run_offline_track_start_frame_zero_matches_existing_behavior(tmp_path, monkeypatch):
    """start_frame=0 (the default) must return the same length as before
    this parameter existed -- a pure regression guard."""
    import numpy as np

    video_path = str(tmp_path / "test_start_frame_zero.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(7):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    kps = np.zeros((17, 2), dtype=np.float32)
    kps[12] = [160, 60]
    kps[14] = [160, 120]
    kps[16] = [160, 200]

    class FakeDetector:
        def detect(self, frame):
            return kps, None

    class FakeTracker:
        def __init__(self, side, fps): pass
        def init(self, frame, hip, knee, ankle): pass
        def step(self, frame):
            return kps[12], kps[14], kps[16], 160.0

    monkeypatch.setattr(_app, "_PatientDetector", FakeDetector)
    monkeypatch.setattr(_app, "_MPBatchTracker",  FakeTracker)
    monkeypatch.setattr(_app, "_VIEWER_AVAIL", True)
    monkeypatch.setattr(_app, "_CV2_AVAIL", True)
    monkeypatch.setattr(_app, "_cv2", _cv2_test)

    engine = BiomechanicalEngine("rgb")
    progress = []
    angles = engine.run_offline_track(
        video_path, lambda p: progress.append(p), leg="right")

    assert len(angles) == 7
    assert progress[-1] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_biomechanical_engine.py -k start_frame -v`
Expected: FAIL with `TypeError: run_offline_track() got an unexpected keyword argument 'start_frame'`

- [ ] **Step 3: Write the implementation**

In `pendulastic_app.py`, change `run_offline_track`'s signature and body. Find:

```python
    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
        collect_landmarks: bool = False,
        manual_seed: tuple | None = None,
    ):
```

Replace with:

```python
    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
        collect_landmarks: bool = False,
        manual_seed: tuple | None = None,
        start_frame: int = 0,
    ):
```

Update the docstring (append after the existing `manual_seed` paragraph):

```python
        start_frame, when > 0, seeks the video to that frame before tracking
        begins. The returned angles/landmarks then cover ONLY the suffix from
        start_frame to the end of the video (length = total_frames -
        start_frame), not the full video -- callers that want a full-video
        result splice this suffix into their own existing arrays starting at
        start_frame. Default 0 preserves the exact prior behavior (full video
        from frame 0).
        """
```

Find the frame-count/seek section:

```python
        fps_v  = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        total  = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 1
```

Replace with:

```python
        fps_v  = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        total  = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 1
        if start_frame > 0:
            cap.set(_cv2.CAP_PROP_POS_FRAMES, start_frame)
            total = max(total - start_frame, 1)
```

`total` is used only for `progress_cb(len(angles) / total)` later in the method — reducing it here makes progress reach `1.0` correctly for the shorter suffix, with no other changes needed in the read loop.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_biomechanical_engine.py -v`
Expected: PASS (all existing `run_offline_track` tests plus the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_biomechanical_engine.py
git commit -m "feat: add start_frame param to run_offline_track for forward retracking"
```

---

## Task 2: `_splice_from` helper (`video_review_dialog.py`)

**Files:**
- Create: `video_review_dialog.py`
- Create: `tests/test_video_review_dialog.py`

**Interfaces:**
- Produces: `video_review_dialog._splice_from(old: list, start_idx: int, new: list, pad_value) -> list` — a pure function, no I/O. Returns `old[:start_idx] + adjusted_new`, where `adjusted_new` is `new` padded with `pad_value` (if `len(new) < len(old) - start_idx`) or truncated (if longer) so the result is always exactly `len(old)` items long.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_video_review_dialog.py`:

```python
# tests/test_video_review_dialog.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from video_review_dialog import _splice_from


def test_splice_from_exact_length_replaces_suffix():
    old = [1, 2, 3, 4, 5]
    result = _splice_from(old, 2, [30, 40, 50], pad_value=0)
    assert result == [1, 2, 30, 40, 50]


def test_splice_from_short_new_pads_with_pad_value():
    old = [1, 2, 3, 4, 5]
    result = _splice_from(old, 2, [30], pad_value=-1)
    assert result == [1, 2, 30, -1, -1]


def test_splice_from_long_new_truncates():
    old = [1, 2, 3, 4, 5]
    result = _splice_from(old, 2, [30, 40, 50, 60, 70], pad_value=0)
    assert result == [1, 2, 30, 40, 50]


def test_splice_from_start_idx_zero_replaces_everything():
    old = [1, 2, 3]
    result = _splice_from(old, 0, [9, 9, 9], pad_value=0)
    assert result == [9, 9, 9]


def test_splice_from_start_idx_at_end_leaves_old_unchanged():
    old = [1, 2, 3]
    result = _splice_from(old, 3, [], pad_value=0)
    assert result == [1, 2, 3]


def test_splice_from_does_not_mutate_input_lists():
    old = [1, 2, 3, 4, 5]
    new = [30, 40, 50]
    _splice_from(old, 2, new, pad_value=0)
    assert old == [1, 2, 3, 4, 5]
    assert new == [30, 40, 50]


def test_splice_from_nan_pad_value_for_angles(monkeypatch):
    import math
    old = [10.0, 20.0, 30.0]
    result = _splice_from(old, 1, [99.0], pad_value=float("nan"))
    assert result[0] == 10.0
    assert result[1] == 99.0
    assert math.isnan(result[2])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'video_review_dialog'`

- [ ] **Step 3: Write the implementation**

Create `video_review_dialog.py`:

```python
"""video_review_dialog.py
=========================
In-app annotated video review for pendulastic_app.py's PostProcessingPanel.
See docs/superpowers/specs/2026-08-12-annotated-video-review-design.md for
the full design.
"""
from __future__ import annotations


def _splice_from(old: list, start_idx: int, new: list, pad_value) -> list:
    """Return old[:start_idx] + new, with new padded (using pad_value) or
    truncated so the result is always exactly len(old) items long. Never
    mutates old or new. This guards against a retrack returning a short or
    long suffix silently desyncing frame-index-to-array-index alignment --
    see design spec S4 point 1."""
    target_len = len(old) - start_idx
    adjusted = list(new[:target_len])
    if len(adjusted) < target_len:
        adjusted.extend([pad_value] * (target_len - len(adjusted)))
    return list(old[:start_idx]) + adjusted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: add _splice_from helper for length-safe retrack splicing"
```

---

## Task 3: `AnnotatedVideoReviewDialog` skeleton — construction, frame cache, scrub

**Files:**
- Modify: `video_review_dialog.py`
- Modify: `tests/test_video_review_dialog.py`

**Interfaces:**
- Consumes: `_splice_from` (Task 2, unused by this task but stays in the same module).
- Produces: `AnnotatedVideoReviewDialog(tk.Toplevel)`, constructed as
  `AnnotatedVideoReviewDialog(parent, video_path, angles, landmarks, fps, leg, engine)`.
  Public/testable attributes after construction: `.angles` (list, copy of the
  constructor arg), `.landmarks` (list, copy of the constructor arg),
  `.video_path`, `.leg`, `.engine`, `.total_frames` (int, from the video),
  `._frame_idx` (int, starts at 0). Method `._read_frame(fi: int) -> np.ndarray`
  reads (and caches) a frame by index. Method `._on_scale_change(value)` updates
  `._frame_idx` and redraws, and is a no-op while `._retrack_in_progress` is
  True (Task 4 sets this flag; here it always starts `False`). Method
  `._trail_for(frame_idx: int) -> list` returns the ankle positions from the
  last `TRAIL_LEN` frames up to and including `frame_idx` (skipping frames
  with no landmark), for the `_draw()` trail argument — this module
  re-exports `TRAIL_LEN` from `pendulastic_viewer` so callers/tests can
  import it from `video_review_dialog` directly.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_video_review_dialog.py`:

```python
import tkinter as tk
import numpy as np
import pytest

try:
    import cv2 as _cv2_test
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

_root_window = None

def _get_root():
    global _root_window
    if _root_window is None:
        _root_window = tk.Tk()
        _root_window.withdraw()
    return _root_window


def _write_test_video(path, n_frames, w=64, h=48):
    """Writes n_frames distinct-valued solid-color frames so tests can
    verify which frame was actually read (frame i is filled with value
    (i * 20) % 256)."""
    out = _cv2_test.VideoWriter(
        path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (w, h))
    for i in range(n_frames):
        val = (i * 20) % 256
        out.write(np.full((h, w, 3), val, dtype=np.uint8))
    out.release()


class _FakeEngine:
    def detect_people_at_frame(self, video_path, frame_index=0):
        return (None, [])
    def run_offline_track(self, *a, **kw):
        return ([], [], 30.0)


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_dialog_constructs_with_correct_total_frames(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review.avi")
    _write_test_video(video_path, 8)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[1.0] * 8, landmarks=[None] * 8,
        fps=30.0, leg="right", engine=_FakeEngine())

    assert dlg.total_frames == 8
    assert dlg._frame_idx == 0
    assert dlg.angles == [1.0] * 8
    assert dlg.landmarks == [None] * 8
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_dialog_angles_and_landmarks_are_copies_not_aliases(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review2.avi")
    _write_test_video(video_path, 4)
    r = _get_root()

    original_angles = [1.0, 2.0, 3.0, 4.0]
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=original_angles, landmarks=[None] * 4,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg.angles[0] = 999.0
    assert original_angles[0] == 1.0
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_read_frame_returns_correct_frame_by_index(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review3.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())

    frame3 = dlg._read_frame(3)
    assert frame3 is not None
    assert int(frame3[0, 0, 0]) == (3 * 20) % 256
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_on_scale_change_updates_frame_idx(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review4.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg._on_scale_change("4")
    assert dlg._frame_idx == 4
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_on_scale_change_ignored_while_retrack_in_progress(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review5.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._retrack_in_progress = True

    dlg._on_scale_change("4")
    assert dlg._frame_idx == 0
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_play_tick_does_not_advance_frame_while_retrack_in_progress(tmp_path):
    """Global Constraints requires playback paused, not just scrubbing --
    _play_tick must reschedule itself without advancing _frame_idx while a
    retrack is in flight."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review6.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._playing = True
    dlg._retrack_in_progress = True
    dlg._frame_idx = 2

    dlg._play_tick()

    assert dlg._frame_idx == 2       # unchanged
    assert dlg._playing is True      # still "wants to play", just paused
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_trail_for_collects_ankle_positions_within_trail_len(tmp_path):
    """Spec S3.1 says the dialog reuses TRAIL_LEN for the ankle-path trail
    -- _trail_for must return the last TRAIL_LEN frames' ankle positions
    (skipping None landmarks), in chronological order, looking back from
    an arbitrary frame_idx (not a sequential accumulation, since
    self.landmarks is already fully available at any frame)."""
    from video_review_dialog import AnnotatedVideoReviewDialog, TRAIL_LEN
    video_path = str(tmp_path / "review7.avi")
    n = TRAIL_LEN + 5
    _write_test_video(video_path, n)
    r = _get_root()

    landmarks = []
    for i in range(n):
        if i % 4 == 0:
            landmarks.append(None)  # some frames have no detection
        else:
            landmarks.append(("hip", "knee", (float(i), float(i))))

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * n, landmarks=landmarks,
        fps=30.0, leg="right", engine=_FakeEngine())

    fi = n - 1
    trail = dlg._trail_for(fi)

    assert len(trail) <= TRAIL_LEN
    expected = [landmarks[i][2] for i in range(max(0, fi - TRAIL_LEN + 1), fi + 1)
                if landmarks[i] is not None]
    assert trail == expected
    assert trail[-1] == (float(fi), float(fi))
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_trail_for_near_start_of_video_does_not_go_negative(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review8.avi")
    _write_test_video(video_path, 3)
    r = _get_root()

    landmarks = [("h", "k", (0.0, 0.0)), ("h", "k", (1.0, 1.0)), None]
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=landmarks,
        fps=30.0, leg="right", engine=_FakeEngine())

    trail = dlg._trail_for(1)

    assert trail == [(0.0, 0.0), (1.0, 1.0)]
    dlg.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: FAIL with `ImportError: cannot import name 'AnnotatedVideoReviewDialog'`

- [ ] **Step 3: Write the implementation**

Append to `video_review_dialog.py` (after `_splice_from`, keep the existing `from __future__ import annotations` at the top):

```python
import tkinter as tk
from tkinter import ttk

import cv2 as _cv2
from PIL import Image, ImageTk

from pendulastic_viewer import _draw, TRAIL_LEN

_MAX_DISPLAY_WIDTH = 960


class AnnotatedVideoReviewDialog(tk.Toplevel):
    """Modal review dialog: scrubs/plays back precomputed MediaPipe angle +
    landmark data with a live skeleton overlay, and lets the user correct
    the tracked person and retrack forward from any frame. See design spec
    docs/superpowers/specs/2026-08-12-annotated-video-review-design.md."""

    def __init__(self, parent, video_path: str, angles: list,
                 landmarks: list, fps: float, leg: str, engine) -> None:
        super().__init__(parent)
        self.title("Review Tracked Video")
        self.video_path = video_path
        self.angles = list(angles)
        self.landmarks = list(landmarks)
        self.fps = fps or 30.0
        self.leg = leg
        self.engine = engine

        self._cap = _cv2.VideoCapture(video_path)
        self.total_frames = max(
            1, int(self._cap.get(_cv2.CAP_PROP_FRAME_COUNT)))
        self._frame_cache: dict = {}
        self._frame_idx = 0
        self._playing = False
        self._retrack_in_progress = False

        self._build_widgets()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.transient(parent)
        self.grab_set()
        self._redraw()

    # ------------------------------------------------------------------
    # Widgets
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        self._image_label = tk.Label(self)
        self._image_label.pack()

        controls = tk.Frame(self)
        controls.pack(fill="x", padx=8, pady=4)

        self._scale = ttk.Scale(
            controls, from_=0, to=max(self.total_frames - 1, 0),
            orient="horizontal", command=self._on_scale_change)
        self._scale.pack(side="top", fill="x")

        button_row = tk.Frame(self)
        button_row.pack(fill="x", padx=8, pady=(0, 8))
        self._btn_play = tk.Button(
            button_row, text="▶", command=self._toggle_play)
        self._btn_play.pack(side="left")
        self._btn_fix = tk.Button(
            button_row, text="Fix Person Here", command=self._on_fix_person_here)
        self._btn_fix.pack(side="left", padx=8)
        self._btn_done = tk.Button(
            button_row, text="Done", command=self._on_close)
        self._btn_done.pack(side="right")

        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=8, pady=(0, 8))

    # ------------------------------------------------------------------
    # Frame reading / caching (pattern: trial_review.py's _read_frame)
    # ------------------------------------------------------------------
    def _read_frame(self, fi: int):
        if fi in self._frame_cache:
            return self._frame_cache[fi]
        self._cap.set(_cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = self._cap.read()
        if not ok:
            return None
        self._frame_cache[fi] = frame.copy()
        if len(self._frame_cache) > 40:
            del self._frame_cache[min(self._frame_cache)]
        return frame

    # ------------------------------------------------------------------
    # Trail
    # ------------------------------------------------------------------
    def _trail_for(self, frame_idx: int) -> list:
        """Ankle positions from the last TRAIL_LEN frames up to and
        including frame_idx, in chronological order, skipping frames with
        no landmark. Computed by lookback (not sequential accumulation)
        since self.landmarks is already fully available at any frame_idx --
        spec S3.1 reuses TRAIL_LEN for this."""
        start = max(0, frame_idx - TRAIL_LEN + 1)
        trail = []
        for i in range(start, frame_idx + 1):
            if i >= len(self.landmarks):
                break
            lm = self.landmarks[i]
            if lm is not None and lm[2] is not None:
                trail.append(lm[2])
        return trail

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _redraw(self) -> None:
        frame = self._read_frame(self._frame_idx)
        if frame is None:
            return
        ang = (self.angles[self._frame_idx]
               if self._frame_idx < len(self.angles) else float("nan"))
        lm = (self.landmarks[self._frame_idx]
              if self._frame_idx < len(self.landmarks) else None)
        hip, kne, ank = lm if lm is not None else (None, None, None)
        trail = self._trail_for(self._frame_idx)
        overlay = _draw(frame, hip, kne, ank, ang, trail, scale=1.0)
        h, w = overlay.shape[:2]
        if w > _MAX_DISPLAY_WIDTH:
            scale = _MAX_DISPLAY_WIDTH / w
            overlay = _cv2.resize(overlay, (int(w * scale), int(h * scale)))
        rgb = _cv2.cvtColor(overlay, _cv2.COLOR_BGR2RGB)
        self._photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self._image_label.configure(image=self._photo)

    # ------------------------------------------------------------------
    # Scrub / playback
    # ------------------------------------------------------------------
    def _on_scale_change(self, value) -> None:
        if self._retrack_in_progress:
            return
        self._frame_idx = int(float(value))
        self._redraw()

    def _toggle_play(self) -> None:
        if self._retrack_in_progress:
            return
        self._playing = not self._playing
        self._btn_play.config(text="⏸" if self._playing else "▶")
        if self._playing:
            self._play_tick()

    def _play_tick(self) -> None:
        if not self._playing:
            return
        if self._retrack_in_progress:
            self.after(100, self._play_tick)
            return
        if self._frame_idx >= self.total_frames - 1:
            self._playing = False
            self._btn_play.config(text="▶")
            return
        self._frame_idx += 1
        self._scale.set(self._frame_idx)
        self._redraw()
        self.after(int(1000 / max(self.fps, 1.0)), self._play_tick)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        if self._retrack_in_progress:
            return
        self._cap.release()
        self.destroy()

    def _on_fix_person_here(self) -> None:
        pass  # implemented in Task 4
```

Note: `self._btn_done` is bound to `_on_close`, and `_on_close` already refuses to
run while `self._retrack_in_progress` is True (satisfies spec S5's "disable
Done/close during an in-flight retrack" — implemented as a guard rather than a
literal widget-state toggle, which is simpler and equally effective).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (15 tests: 7 from Task 2 + 8 new)

- [ ] **Step 5: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: add AnnotatedVideoReviewDialog skeleton with scrub and playback"
```

---

## Task 4: "Fix Person Here" — repick, retrack, splice

**Files:**
- Modify: `video_review_dialog.py`
- Modify: `tests/test_video_review_dialog.py`

**Interfaces:**
- Consumes: `_splice_from` (Task 2), `resolve_person_click` (from `pendulastic_viewer` — imported in this task, not Task 3, since Task 3 has no caller for it yet), `PersonPickerDialog` (lazily imported from `pendulastic_app` here — see Global Constraints), `self.engine.detect_people_at_frame` / `self.engine.run_offline_track(start_frame=...)` (Task 1).
- Produces: `_on_fix_person_here()` fully implemented (replacing Task 3's `pass` stub); `_start_retrack(start_frame, seed)`; `_on_retrack_done(start_frame, new_angles, new_landmarks)`. After a successful retrack, `self.angles`/`self.landmarks` reflect the spliced result and `self._retrack_in_progress` is back to `False`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_video_review_dialog.py`:

```python
class _SyncThread:
    """Runs target() synchronously in start() -- makes the retrack
    background thread deterministic for testing, matching the convention
    in tests/test_post_processing_panel.py."""
    def __init__(self, target=None, daemon=None, **kw):
        self._target = target
    def start(self):
        self._target()


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


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_zero_poses_shows_status_and_does_not_retrack(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    video_path = str(tmp_path / "fix0.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    class _ZeroPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (np.zeros((48, 64, 3), dtype=np.uint8), [])

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_ZeroPoseEngine())

    dlg._on_fix_person_here()

    assert "no person" in dlg.status_var.get().lower()
    assert dlg._retrack_in_progress is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_one_pose_auto_resolves_and_retracks(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    video_path = str(tmp_path / "fix1.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]

    captured = {}

    class _OnePoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            captured["manual_seed"] = manual_seed
            captured["start_frame"] = start_frame
            progress_cb(1.0)
            n = 6 - start_frame
            return ([170.0] * n, [None] * n, 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_OnePoseEngine())
    dlg._frame_idx = 2

    dlg._on_fix_person_here()

    assert captured["start_frame"] == 2
    assert captured["manual_seed"] is not None
    assert dlg.angles == [0.0, 0.0, 170.0, 170.0, 170.0, 170.0]
    assert dlg._retrack_in_progress is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_two_poses_uses_person_picker_dialog(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import pendulastic_app as _app
    video_path = str(tmp_path / "fix2.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.4), _make_pose(0.6)]

    class _TwoPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            progress_cb(1.0)
            n = 5 - start_frame
            return ([99.0] * n, [None] * n, 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    class _StubPickerDialog:
        def __init__(self, *a, **kw):
            self.result = ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _StubPickerDialog)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_TwoPoseEngine())
    monkeypatch.setattr(dlg, "wait_window", lambda w: None)
    dlg._frame_idx = 1

    dlg._on_fix_person_here()

    assert dlg.angles == [0.0, 99.0, 99.0, 99.0, 99.0]
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_cancelled_picker_dialog_does_not_retrack(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import pendulastic_app as _app
    video_path = str(tmp_path / "fix3.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.4), _make_pose(0.6)]

    called = {"retrack": False}

    class _TwoPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, *a, **kw):
            called["retrack"] = True
            return ([], [], 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    class _CancelledPickerDialog:
        def __init__(self, *a, **kw):
            self.result = None
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _CancelledPickerDialog)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_TwoPoseEngine())
    monkeypatch.setattr(dlg, "wait_window", lambda w: None)

    dlg._on_fix_person_here()

    assert called["retrack"] is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_short_retrack_result_pads_not_leaves_stale(tmp_path, monkeypatch):
    """If run_offline_track returns fewer frames than expected, the tail
    must be padded (nan/None), never left as stale pre-fix landmarks --
    spec S4 point 1."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import math
    video_path = str(tmp_path / "fix4.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]

    class _ShortReturnEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            progress_cb(1.0)
            return ([170.0], [("hip", "knee", "ankle")], 30.0)  # short!

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=["stale"] * 6,
        fps=30.0, leg="right", engine=_ShortReturnEngine())
    dlg._frame_idx = 2

    dlg._on_fix_person_here()

    assert dlg.angles[2] == 170.0
    assert math.isnan(dlg.angles[3])
    assert math.isnan(dlg.angles[4])
    assert math.isnan(dlg.angles[5])
    assert dlg.landmarks[3] is None
    assert dlg.landmarks[4] is None
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_retrack_in_progress_blocks_a_second_fix_call(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "fix5.avi")
    _write_test_video(video_path, 4)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 4, landmarks=[None] * 4,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._retrack_in_progress = True

    dlg._on_fix_person_here()  # must no-op, not raise

    dlg.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: FAIL — `_on_fix_person_here` is still a no-op stub from Task 3, so
assertions like `dlg.angles == [...]` and `"no person" in dlg.status_var.get()` fail.

- [ ] **Step 3: Write the implementation**

In `video_review_dialog.py`, add `import threading` as the first import line
(before `import tkinter as tk`), and add `resolve_person_click` to the
existing `pendulastic_viewer` import line — both are unused until this task,
so neither belongs in Task 3's import block:

```python
from pendulastic_viewer import _draw, TRAIL_LEN, resolve_person_click
```

Then replace the Task 3 stub:

```python
    def _on_fix_person_here(self) -> None:
        pass  # implemented in Task 4
```

with:

```python
    def _on_fix_person_here(self) -> None:
        if self._retrack_in_progress:
            return
        self._playing = False
        self._btn_play.config(text="▶")

        frame_idx = self._frame_idx
        frame, poses = self.engine.detect_people_at_frame(
            self.video_path, frame_index=frame_idx)
        if frame is None or not poses:
            self.status_var.set(
                "No person detected at this frame -- try a nearby frame.")
            return

        fh, fw = frame.shape[:2]
        if len(poses) == 1:
            result = resolve_person_click(
                poses, (fw / 2, fh / 2), fw, fh, self.leg)
            if result is None or result[2] is None:
                self.status_var.set(
                    "No person detected at this frame -- try a nearby frame.")
                return
            seed = result
        else:
            from pendulastic_app import PersonPickerDialog
            dialog = PersonPickerDialog(
                self, self.video_path, frame_idx, frame, poses, self.leg)
            self.wait_window(dialog)
            if dialog.result is None:
                return
            seed = dialog.result

        self._start_retrack(frame_idx, seed)

    def _start_retrack(self, start_frame: int, seed: tuple) -> None:
        self._retrack_in_progress = True
        self._btn_fix.config(state="disabled")
        self.status_var.set(f"Retracking from frame {start_frame}...")

        def _run():
            new_angles, new_landmarks, _fps = self.engine.run_offline_track(
                self.video_path, lambda p: None, leg=self.leg,
                collect_landmarks=True, manual_seed=seed,
                start_frame=start_frame)
            self.after(0, lambda: self._on_retrack_done(
                start_frame, new_angles, new_landmarks))

        threading.Thread(target=_run, daemon=True).start()

    def _on_retrack_done(self, start_frame: int, new_angles: list,
                          new_landmarks: list) -> None:
        self.angles = _splice_from(self.angles, start_frame, new_angles,
                                    float("nan"))
        self.landmarks = _splice_from(self.landmarks, start_frame,
                                       new_landmarks, None)
        self._retrack_in_progress = False
        self._btn_fix.config(state="normal")
        self.status_var.set(f"Retrack complete from frame {start_frame}.")
        self._redraw()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (21 tests: 15 from Tasks 2-3 + 6 new)

- [ ] **Step 5: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: wire Fix Person Here to repick, retrack, and length-safe splice"
```

---

## Task 5: Wire into `PostProcessingPanel`

**Files:**
- Modify: `pendulastic_app.py` (`PostProcessingPanel._on_upload_video`, `_add_hpe_overlay`)
- Modify: `tests/test_post_processing_panel.py`

**Interfaces:**
- Consumes: `AnnotatedVideoReviewDialog` (Task 3/4).
- Produces: `_add_hpe_overlay(self, angles, landmarks=None, fps=30.0, engine=None)` — new
  `engine` parameter. When `landmarks` and `engine` and `self._video_path` are all
  truthy, opens `AnnotatedVideoReviewDialog` modally right after storing results, and
  uses `dialog.angles`/`dialog.landmarks` (whatever the user ended up with, corrected
  or not) for the rest of the method.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_post_processing_panel.py`:

```python
def test_on_upload_video_opens_review_dialog_and_uses_corrected_results(monkeypatch, tmp_path):
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

    def _fake_run_offline_track(self, path, progress_cb, leg="right",
                                 collect_landmarks=False, manual_seed=None,
                                 start_frame=0):
        progress_cb(1.0)
        return ([170.0] * 5, [None] * 5, 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    opened = {}
    class _StubReviewDialog:
        def __init__(self, parent, video_path, angles, landmarks, fps, leg, engine):
            opened["video_path"] = video_path
            opened["angles"] = angles
            self.angles = [999.0] * len(angles)   # simulate a user correction
            self.landmarks = landmarks
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "AnnotatedVideoReviewDialog", _StubReviewDialog)
    monkeypatch.setattr(p, "wait_window", lambda dlg: None)

    p._on_upload_video()
    r.update()

    assert opened["video_path"] == video_path
    assert p._source_angles["hpe_upload"] == [999.0] * 5


def test_add_hpe_overlay_skips_dialog_when_no_landmarks(monkeypatch):
    """Existing no-landmarks callers (e.g. a failed track) must not try to
    open a review dialog at all."""
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    opened = {"called": False}
    class _StubReviewDialog:
        def __init__(self, *a, **kw):
            opened["called"] = True
    monkeypatch.setattr(_app, "AnnotatedVideoReviewDialog", _StubReviewDialog)

    p._add_hpe_overlay([170.0] * 3, landmarks=None, fps=30.0, engine=None)

    assert opened["called"] is False
    assert p._source_angles["hpe_upload"] == [170.0] * 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_post_processing_panel.py -k "review_dialog or skips_dialog" -v`
Expected: FAIL — `AnnotatedVideoReviewDialog` doesn't exist in `pendulastic_app`'s
namespace yet, and `_add_hpe_overlay` doesn't accept `engine`.

- [ ] **Step 3: Write the implementation**

In `pendulastic_app.py`, add the import near the top, alongside the existing
`pendulastic_viewer` import block (inside the same `try`, right after it, so a
missing `video_review_dialog` degrades the same way `_VIEWER_AVAIL` already does
for the rest of this file):

```python
try:
    from video_review_dialog import AnnotatedVideoReviewDialog
except Exception:
    AnnotatedVideoReviewDialog = None
```

Find `_add_hpe_overlay`'s current signature and body:

```python
    def _add_hpe_overlay(self, angles: list, landmarks: list | None = None,
                          fps: float = 30.0) -> None:
        if not angles:
            self.status_var.set(
                "HPE: no pose detected — check video or leg selection.")
            return
        self._source_angles["hpe_upload"] = angles
        self._hpe_landmarks = landmarks
```

Replace with:

```python
    def _add_hpe_overlay(self, angles: list, landmarks: list | None = None,
                          fps: float = 30.0, engine=None) -> None:
        if not angles:
            self.status_var.set(
                "HPE: no pose detected — check video or leg selection.")
            return
        if landmarks and engine is not None and self._video_path \
                and AnnotatedVideoReviewDialog is not None:
            dialog = AnnotatedVideoReviewDialog(
                self, self._video_path, angles, landmarks, fps or self._fps,
                self._hpe_leg, engine)
            self.wait_window(dialog)
            angles = dialog.angles
            landmarks = dialog.landmarks
        self._source_angles["hpe_upload"] = angles
        self._hpe_landmarks = landmarks
```

Find the call site (inside `_on_upload_video`'s nested `_run`):

```python
            self.after(0, lambda: self._add_hpe_overlay(angles, landmarks, fps=video_fps))
```

Replace with:

```python
            self.after(0, lambda: self._add_hpe_overlay(
                angles, landmarks, fps=video_fps, engine=engine))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_post_processing_panel.py tests/test_video_review_dialog.py tests/test_biomechanical_engine.py -v`
Expected: PASS (all tests across all three files)

- [ ] **Step 5: Commit**

```bash
git add pendulastic_app.py tests/test_post_processing_panel.py
git commit -m "feat: open annotated video review after HPE tracking finishes"
```

---

## Final check: full suite

- [ ] Run the complete test suite once more to confirm nothing else regressed:

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: PASS (no failures, no new skips beyond the existing `cv2 not installed` skips)
