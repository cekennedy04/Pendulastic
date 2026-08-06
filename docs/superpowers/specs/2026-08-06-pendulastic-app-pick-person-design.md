# Pendulastic App Pick-Person (Multi-Patient Disambiguation) — Design Spec

**Status:** Approved (brainstorming complete, pending user review of this document)
**Date:** 2026-08-06

## 1. Problem & Existing Landscape

After manually testing the just-shipped annotation port (see
`2026-08-06-pendulastic-app-annotations-design.md`), the user reported that
`pendulastic_app.py`'s HPE video upload lacks user-facing tools to (a) tell
the app which of two people in frame is the patient, and (b) adjust bad
tracking frames. `pendulastic_viewer.py` has both — but they are two
distinct mechanisms solving two distinct failure modes, confirmed by
reading the tracker's own code and docstrings:

1. **Multi-person selection ("Pick Person")** — `_cmd_pick_person`
   (pendulastic_viewer.py:5954-5996), `_on_person_select_click`
   (:5997-6098), `_draw_person_select_overlay` (:3841-3882). Runs MediaPipe
   `PoseLandmarker` in `IMAGE` mode (`num_poses=4`) on a single still frame,
   shows every detected person with a numbered colored skeleton, and lets
   the user click the patient. This seeds `tracker.init(frame, hip, knee,
   ankle)` — a one-time initialization.
2. **Per-frame tracking correction ("pins" + Retrack)** — `_ankle_pins`
   dict, the sticky ankle-click-after-tracking flow in `_try_init_tracker`
   (:7609-7772), and `_cmd_retrack_from_here` (:6254+). Lets the user scrub
   to any individual bad frame post-hoc and manually correct it, then
   re-runs tracking forward from the correction.

**Why only #1 is in scope for this spec:** `_MPBatchTracker.step()`
(pendulastic_viewer.py:1487+) disambiguates people mid-video using an
x-gate anchored to `self._anchor_xfrac` — a value frozen once at
`init()` and *deliberately never updated by EMA*, specifically (per the
code's own docstring) "so the assessor can never pull the tracker away
through accumulated EMA drift," plus an NCC appearance-template check.
Today, `pendulastic_app.py`'s `BiomechanicalEngine.run_offline_track`
seeds this tracker automatically via `_PatientDetector` (a YOLO heuristic
guessing "most horizontal trunk = reclining patient"). If that automatic
guess is wrong — plausible whenever an assessor is also leaning over the
patient — every downstream frame inherits the wrong anchor, and the
tracker's own anti-drift guards then faithfully hold onto the *wrong*
person for the whole video. This matches the reported symptom
("MediaPipe gets confused since there are two people in frame") directly:
the failure is at the one-time seed, not a mid-video tracking-quality
problem. Pin-correction (#2) solves a different problem — a tracker that
correctly identified the patient but lost lock later (occlusion, fast
motion) — and needs a whole new scrubbable per-frame video-preview canvas
in `PostProcessingPanel` (confirmed absent today: `PostProcessingPanel`'s
upload flow is upload → batch-track the entire video in one background
pass → show the resulting angle plot, with no per-frame display or click
handling at all). Given the seed-level root cause matches the reported
problem exactly, and #2 is a substantially larger UI lift, this spec
covers #1 only. #2 remains available as a follow-up if bad frames persist
after Pick Person ships, for a *different* reason than person-confusion.

**Scope decision (confirmed with user):** port "Pick Person" only, into
`PostProcessingPanel`'s upload-for-HPE flow. Two further scope calls,
also confirmed:
- The new single-frame detection path replaces the YOLO-based
  `_PatientDetector` entirely, including in the single-person case (not
  just as a disambiguation-only path) — one code path for both 1-person
  and 2+-person videos, using the same MediaPipe model already used
  elsewhere in the pipeline.
- If the user cancels/closes the picker dialog without selecting anyone,
  the upload is aborted (status message, no tracking runs) rather than
  silently falling back to the old automatic detector — falling back
  silently would risk reintroducing the exact bug this feature exists to
  fix, without the user necessarily noticing.

## 2. Architecture

**Shared extraction, same pattern as the prior annotation port:** the two
pieces of `_cmd_pick_person`'s machinery that have no real dependency on
viewer-instance state become module-level functions in
`pendulastic_viewer.py`, next to the already-shared `_draw()`:

```python
def draw_person_select_overlay(frame: np.ndarray, poses: list) -> np.ndarray:
    """Draw every candidate's skeleton in a distinct numbered color plus an
    instruction banner. Extracted from PendulaticViewer._draw_person_select_overlay
    (was self._person_select_poses; now an explicit poses parameter)."""

def resolve_person_click(poses: list, click_xy, frame_w: int, frame_h: int,
                          leg: str) -> tuple | None:
    """Find the candidate pose nearest the click (checking all landmarks of
    all poses), resolve which anatomical leg maps to the requested screen
    side (mirroring-aware, same logic as _on_person_select_click), and
    return (hip, knee, ankle) as float32 pixel-coordinate tuples, or None
    if ankle visibility is below 0.35 (see Error Handling)."""
```

`PendulaticViewer._draw_person_select_overlay` and the corresponding
portion of `_on_person_select_click` become thin wrappers calling these
(same relationship Task 1 established between `_annotate_plot` and
`draw_pt_annotations`) — behavior-identical, no viewer regression.
`pendulastic_app.py` imports both, plus the already-referenced `_MP_MODEL`
constant (an absolute path resolved relative to `pendulastic_viewer.py`'s
own file location — correct regardless of which module imports it), by
extending the existing guarded import block that already pulls in `_draw`
and `TRAIL_LEN`.

**New detection entry point** — `BiomechanicalEngine.detect_people_on_first_frame`,
alongside `run_offline_track` in `pendulastic_app.py`: opens the video,
reads frame 0, runs the same `mp.tasks.vision.PoseLandmarker` IMAGE-mode
call `_cmd_pick_person` makes (`num_poses=4`), and returns both the raw
frame (needed to render the picker) and the pose list.

**New flow in `PostProcessingPanel._on_upload_video`** (runs synchronously
on the UI thread — a single-frame inference is fast, unlike the
whole-video batch track that follows):

1. User picks a video file (unchanged).
2. Call `detect_people_on_first_frame(path)`.
   - **Exception during detection** → treat as 0 people found (see Error
     Handling) and fall back to automatic behavior for this upload only.
   - **0 people found** → `manual_seed = None`; proceed exactly as today
     (the now-unused-for-this-path `_PatientDetector` fallback stays in
     `run_offline_track` as the safety net for this case — see Section 3).
   - **1 person found** → no dialog. Call `resolve_person_click` is not
     needed since there's no click — instead, extract hip/knee/ankle
     directly from that single pose's landmarks using the same
     leg-to-screen-side mapping, using the trial metadata's leg value
     (`self._meta.get("leg", "right")`) exactly as `resolve_person_click`
     would internally. This is the single-code-path decision: the same
     landmark-extraction logic serves both the 0-ambiguity and
     click-to-disambiguate cases.
   - **2+ people found** → open `PersonPickerDialog` (new `tk.Toplevel`,
     modal via `transient()` + `grab_set()` + `wait_window()`) showing
     frame 0 composited with `draw_person_select_overlay(frame, poses)`.
     User clicks their pick; the dialog resolves it via
     `resolve_person_click` and closes with a result, or the user closes
     the dialog and the result stays `None`.
     - Result present → that becomes `manual_seed`.
     - Result absent (cancelled) → abort the upload (status message,
       return before starting the background thread — no tracking runs).
3. Background thread: `engine.run_offline_track(path, progress_cb,
   leg=leg.lower(), collect_landmarks=True, manual_seed=manual_seed)` —
   unchanged from today except for the new parameter.

## 3. Components & Data Flow

### 3.1 `pendulastic_viewer.py`
- New module-level functions `draw_person_select_overlay` and
  `resolve_person_click`, extracted from the existing class methods as
  described above.
- `PendulaticViewer._draw_person_select_overlay` and
  `_on_person_select_click` refactored to call them; behavior unchanged.

### 3.2 `pendulastic_app.py` — `BiomechanicalEngine`
- **New:** `detect_people_on_first_frame(self, video_path: str) -> tuple[np.ndarray | None, list]`
  — opens `video_path`, reads one frame, runs the IMAGE-mode
  `PoseLandmarker` detection (`num_poses=4`, same confidence thresholds as
  the viewer's `_cmd_pick_person`), returns `(frame, poses)`. Returns
  `(None, [])` on any failure to open the video or read a frame; returns
  `(frame, [])` if detection succeeds but finds nobody. Never raises —
  any exception from the MediaPipe call itself is caught internally and
  treated as "0 people found" (so the caller's fallback path is the same
  whether detection legitimately found nobody or errored).
- **`run_offline_track`**: new optional parameter `manual_seed: tuple | None = None`
  (a `(hip, knee, ankle)` triple of pixel-coordinate arrays/tuples,
  matching what `tracker.init()` already accepts). When provided, the
  method's existing per-frame "search for the first frame where
  `_PatientDetector` succeeds" loop is skipped entirely — on the very
  first frame read, it calls `tracker.init(frame, *manual_seed)` directly
  and marks itself initialized, then proceeds exactly as today for every
  subsequent frame (`tracker.step(frame)` per frame, same landmark
  collection when `collect_landmarks=True`). When `manual_seed` is `None`
  (all 3 pre-existing callers, unchanged), behavior is completely
  unchanged — this is the fallback path for both "0 people detected in
  the picker step" and any caller that doesn't use the picker flow at
  all.

### 3.3 `pendulastic_app.py` — new `PersonPickerDialog` class
- A `tk.Toplevel` modal dialog. Constructor takes the parent panel,
  the frame (`np.ndarray`), the poses list, and the leg string.
- Renders `draw_person_select_overlay(frame, poses)` as a `PhotoImage` in
  a `tk.Label` (same cv2→PIL→ImageTk conversion pattern already used by
  `pendulastic_workbench.py`'s `WorkbenchView._video_label` — reusing an
  established in-repo pattern, not inventing a new one). If the frame is
  wider than 900px (matching the app's existing minimum-width convention
  elsewhere), it is scaled down to fit within 900px for display, with a
  stored scale factor so click coordinates can be mapped back to original
  frame-pixel space before being passed to `resolve_person_click`.
- Binds a click handler that calls `resolve_person_click(poses, click_xy,
  frame_w, frame_h, leg)`. On a successful resolution (ankle visibility
  ok), stores the result on `self.result` and calls `self.destroy()`. On
  ankle-visibility rejection (see Error Handling), shows an inline status
  message in the dialog rather than closing, so the user can click again
  (possibly picking a different, better-visible detection).
- `WM_DELETE_WINDOW` and any explicit Cancel affordance both just call
  `self.destroy()` without setting `self.result` — the caller checks
  `self.result is None` after `wait_window()` returns to detect
  cancellation.

### 3.4 `pendulastic_app.py` — `PostProcessingPanel._on_upload_video`
- Restructured per the flow in Section 2. The background-thread portion
  (the actual `run_offline_track` call and its `self.after(0, ...)`
  progress/completion callbacks) is unchanged in shape from the current
  implementation — only the seed-resolution step before it is new.

## 4. Error Handling

- **MediaPipe detection exception on frame 0** (corrupt frame, model load
  failure, etc.): caught inside `detect_people_on_first_frame`, treated as
  0 people found. The upload proceeds using the existing automatic
  `_PatientDetector` fallback inside `run_offline_track` — i.e. a
  detection-step failure degrades to today's existing behavior rather
  than blocking the upload entirely.
- **Video fails to open / frame 0 unreadable**: same treatment — `(None,
  [])` returned, upload proceeds via the automatic fallback (which will
  itself hit its own existing "video won't open" guard and report that
  clearly if the problem is really the file itself).
- **Ankle visibility below 0.35 after a click** (mirrors the viewer's
  existing guard, `pendulastic_viewer.py:6064-6075`): `resolve_person_click`
  returns `None` rather than a low-confidence position. In the dialog,
  this means "click didn't resolve" — an inline message asks the user to
  try clicking again (e.g. closer to the ankle, or pick a different
  detected candidate whose ankle is clearer), rather than seeding the
  tracker with an unreliable position. Unlike the viewer (which falls
  back to manual knee/ankle marker placement — a feature that doesn't
  exist in `PostProcessingPanel`), there is no further manual-placement
  escape hatch in this phase; if every candidate's ankle is too occluded
  to resolve, the user's only recourse is to cancel and try a clearer
  video, or a future pin-correction phase.
- **User cancels the picker dialog**: upload aborted, per the confirmed
  scope decision — status bar says something like "Upload cancelled — no
  patient selected," no background thread starts, `_video_path`/
  `_hpe_landmarks`/`_source_angles["hpe_upload"]` are left exactly as they
  were before the upload attempt (reuses the same invalidate-before-track
  pattern already added in the prior plan's final-review fix, so a
  cancelled second upload can never leave stale state pointing at a
  previous video).
- **0 or 1 person found**: no error path — both are valid, non-ambiguous
  outcomes handled without user interaction, per Section 2.

## 5. Testing

- **`detect_people_on_first_frame`**: unit tests with a mocked
  `PoseLandmarker`-equivalent (following the existing
  `tests/test_biomechanical_engine.py` convention of monkeypatching
  `_PatientDetector`/`_MPBatchTracker` — here, whatever module-level
  MediaPipe Tasks entry point the implementation calls) covering: normal
  multi-person detection, zero people found, and an exception raised
  during detection (must return `(None, [])` or `(frame, [])` as
  specified, never propagate the exception).
- **`resolve_person_click`**: unit tests with synthetic pose landmark
  lists (plain objects/namedtuples with `.x`/`.y`/`.visibility`) covering:
  nearest-pose selection among 2+ candidates, left/right leg mapping in
  both mirrored and non-mirrored configurations (matching the viewer's
  own `anat_left_is_img_left` logic), and the ankle-visibility rejection
  threshold (returns `None` below 0.35, a valid tuple at/above it).
- **`run_offline_track` with `manual_seed`**: extend
  `tests/test_biomechanical_engine.py` with a case that passes a synthetic
  `manual_seed` and a mocked tracker, asserting `tracker.init` is called
  with exactly that seed on the first frame (not derived from
  `_PatientDetector`), and that the existing `collect_landmarks`/backward
  compatibility behavior (verified in the prior plan) is unaffected when
  `manual_seed` is `None`.
- **`PostProcessingPanel._on_upload_video`'s branching**: extend
  `tests/test_post_processing_panel.py` with cases (mocking
  `detect_people_on_first_frame`'s return value) for the 0-person,
  1-person, and 2+-person-with-dialog-cancelled paths, asserting the
  correct `manual_seed` is computed (or that the upload aborts cleanly on
  cancel, per Section 4) — without needing to drive real dialog clicks in
  automated tests (the dialog's own click-resolution logic is covered
  separately via `resolve_person_click`'s unit tests).
- **Manual verification** (explicitly called out, not covered by
  automated tests, same as the prior plan's video-export feature): run
  `pendulastic_app.py`, upload a video with two people in frame, confirm
  the picker dialog appears with both people numbered and colored
  distinctly, click the correct one, and confirm tracking proceeds
  correctly for the rest of the video (no drift onto the assessor) —
  ideally on the exact footage that originally surfaced this problem.
