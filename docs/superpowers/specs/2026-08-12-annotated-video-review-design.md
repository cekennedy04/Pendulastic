# In-App Annotated Video Review for PostProcessingPanel — Design Spec

**Revision note (2026-08-12):** `/codex` was rate-limited (hit usage quota, resets
2026-08-14). Ran a `ce-pov` document-take critique instead, grounded in direct reads of
`pendulastic_app.py`, `pendulastic_viewer.py`'s `_MPBatchTracker`, and `trial_review.py`.
It confirmed the overall approach but found two real correctness gaps (splice-length
safety, cross-thread mutation) that the design below now specifies explicitly rather than
leaving to implementation-time judgment — see §4 and §5. It also resolved one open
question (whether `_MPBatchTracker`'s internal `_ts_ms` field creates a timing dependency
on the tracker being initialized at frame 0): it's assigned at init/reset but never read
anywhere in `pendulastic_viewer.py`, so re-initializing mid-video carries no hidden
timing hazard.

## 1. Goal

Let a user see the MediaPipe-annotated video in-app right after HPE tracking finishes,
correct which detected person is being tracked if it locked onto the wrong one, and fix
the resulting frames by retracking forward from a chosen point — without leaving
`pendulastic_app.py` or falling back to exporting a video file to inspect externally.

## 2. Background / Why

- `pendulastic_app.py`'s `PostProcessingPanel` (`_on_upload_video`) currently resolves
  which detected person is the patient **once**, at frame 0, via `PersonPickerDialog`
  when 2+ people are found. `engine.run_offline_track(...)` then tracks the entire video
  from frame 0 to the end using that single seed — there is no way to correct the pick
  if the tracker locks onto the wrong person partway through, and no in-app way to see
  the annotated result. The only way to "see" the annotated video today is the separate
  "Export Annotated Video" button, which burns the skeleton overlay into an output `.mp4`
  the user has to open in an external player.
- `pendulastic_viewer.py`'s `PendulaticViewer` (a much older, ~9000-line standalone
  Tkinter app) already has this capability: live in-app playback with skeleton overlay,
  a mid-video person re-pick (`_cmd_pick_person`), and a multi-pin arc-interpolation
  retrack (`_cmd_retrack_from_here`). `pendulastic_app.py` already imports several of
  its low-level helpers (`_draw`, `draw_person_select_overlay`, `resolve_person_click`,
  `_MPBatchTracker`, `_PatientDetector`, `TRAIL_LEN`, `_MP_MODEL`) but not the
  playback/retrack UI itself, which is tightly coupled to `PendulaticViewer`'s internal
  state.
- Decision: build this into `pendulastic_app.py` (the actively developed app), not
  `pendulastic_viewer.py` or the separate `web/frontend` (React + FastAPI). Use a
  **single-point repick-and-retrack-forward** mechanism, not `pendulastic_viewer.py`'s
  full multi-pin arc-interpolation — the simpler mechanism covers the common failure
  mode (tracker locks onto the wrong person partway through) and can be applied
  repeatedly to fix multiple bad segments in one review pass.

## 3. Design

### 3.1 Components

New module `video_review_dialog.py` (not added to `pendulastic_app.py`, already 3,687
lines) containing `AnnotatedVideoReviewDialog(tk.Toplevel)`, constructed as
`(parent, video_path, angles, landmarks, fps, leg, engine)`. It owns its own
`cv2.VideoCapture` plus a small frame cache (same pattern as `trial_review.py`'s
`_read_frame`), and reuses three things unchanged:

- `_draw()` for the skeleton/angle/trail overlay — the same renderer "Export Annotated
  Video" already uses, so the live view matches what export would produce.
- `PersonPickerDialog` for re-picking the tracked person at an arbitrary frame.
- `TRAIL_LEN` for the ankle-path trail length.

`PostProcessingPanel._add_hpe_overlay()` (existing method, called when tracking
finishes) opens this dialog modally (`wait_window`) right after storing the initial
results, then reads back `dialog.angles` / `dialog.landmarks` (unchanged if the user
made no fixes) before continuing into the existing plot/metrics code unchanged.

### 3.2 Data flow

The dialog has scrub controls (a `Scale` trackbar + play/pause, same interaction
pattern as `trial_review.py`) driving `frame_idx`. Each redraw looks up the
already-computed `landmarks[frame_idx]` / `angles[frame_idx]` and renders via `_draw()`
— no live MediaPipe calls during normal scrubbing or playback.

A "Fix Person Here" button pauses playback and calls
`engine.detect_people_at_frame(video_path, frame_index=frame_idx)`:

- 2+ people found → open the existing `PersonPickerDialog` unchanged.
- 1 person found → auto-resolve the same way `_on_upload_video` already does today
  (`resolve_person_click` at frame center).
- 0 people found → status message asking the user to try a nearby frame; no state
  change.

A confirmed pick becomes a new `manual_seed`.

### 3.3 Backend change: `run_offline_track(start_frame=...)`

`BiomechanicalEngine.run_offline_track()` gets a new `start_frame: int = 0` parameter:
seek the video to `start_frame` (`cap.set(CAP_PROP_POS_FRAMES, start_frame)`) before the
existing read loop; everything else, including existing `manual_seed` handling, is
unchanged. It returns only the suffix from `start_frame` onward.

**`total` must change too.** The existing progress math is
`progress_cb(len(angles) / total)` where `total = cap.get(CAP_PROP_FRAME_COUNT)` — the
*full* video length, unaffected by seeking. With `start_frame > 0`, `len(angles)` can
only ever reach `total - start_frame`, so progress would visibly stall short of 100%.
`total` must become `total - start_frame` when `start_frame` is passed.

## 4. Retrack correctness (from the `ce-pov` critique — required, not optional)

The dialog runs the retrack on a background thread (mirroring `_on_upload_video`'s
existing thread pattern), then must splice the result into the dialog's `angles` /
`landmarks` lists. Two things the naive version of this gets wrong:

1. **Exact-length splice, not a bare slice assignment.** Python's
   `angles[start_frame:] = new_angles` does not require
   `len(new_angles) == len(angles) - start_frame` — a short return (seek drift on a
   long-GOP codec, a mid-video read failure) silently shrinks `angles`, desyncing every
   downstream index-to-frame-time assumption, including `PostProcessingPanel`'s own
   `times = [i / fps for i in range(len(angles))]`. Before assigning, pad `new_angles`
   with `nan` (and `new_landmarks` with `None`) or truncate so
   `len(new_angles) == len(angles) - start_frame` exactly, every time.
2. **Splice on the main thread, with scrubbing paused.** Follow the same convention
   `_on_upload_video`/`_run` already establishes elsewhere in this codebase: the
   background thread only computes; the actual list mutation happens inside
   `self.after(0, lambda: ...)` on the Tk main thread. Additionally, pause (not just
   disable the "Fix Person Here" button, but pause) playback/scrubbing while a retrack
   is in flight, so the redraw loop can't read `angles[frame_idx]` mid-splice.

Documented (not fixed) semantic: fixing progressively **later** points in the timeline
composes cleanly (each fix only overwrites from its own `start_frame` onward, leaving
earlier fixes intact). Fixing an **earlier** point after a later one silently discards
the later fix's contribution to the overlap region, since the earlier splice overwrites
it. This should be a one-line note in the dialog's own help text or status bar, not a
larger mechanism.

## 5. Error handling

- Cancelled person-pick (`PersonPickerDialog.result is None`): no retrack, dialog
  returns to playback unchanged.
- 0 people detected at the chosen frame: status message, no state change.
- Retrack returns a short suffix: pad per §4 point 1, never leave stale wrong-person
  landmarks in an unfilled tail.
- Concurrent fixes: "Fix Person Here" disabled AND scrubbing/playback paused while a
  retrack thread runs; both re-enabled on completion (§4 point 2).
- **Closing the dialog mid-retrack:** disable the Done/close control while a retrack
  thread is in flight — the same reasoning as disabling Fix Person Here, since a
  pending `self.after(0, ...)` callback would otherwise fire against a destroyed
  widget.
- Per-frame tracking failures during the retrack itself are already handled inside
  `run_offline_track` (appends `nan`/`None` and continues) — nothing new needed.

## 6. Testing

Following `tests/test_person_picker_dialog.py`'s convention: instantiate the real
dialog against a hidden Tkinter root (`_get_root().withdraw()`) with synthetic numpy
frames and fake landmark objects — no real video file, no event loop needed.

- Frame-index/scrub-bar bounds.
- A small pure `_splice_from(old, start_idx, new)` helper (§4.1's pad/truncate logic),
  unit-tested directly with plain lists — including the short-return and long-return
  cases.
- The three dispatch branches of "Fix Person Here" (0/1/2+ poses).
- `run_offline_track(start_frame=...)`: seeks correctly, returns a suffix of the
  expected length, and reports progress that reaches 1.0 (covering the §3.3 `total`
  fix).
- Dialog close while a retrack thread is (simulated as) in flight does not raise.

## 7. Out of scope

- `pendulastic_viewer.py`'s multi-pin arc-interpolation retrack — not ported (§2).
- Any change to `web/frontend` or the FastAPI backend.
- On-demand review from the trial list for previously-saved trials — this pass only
  covers the review opening automatically right after HPE tracking finishes.
- Any change to `PatientIdentityTracker` (used by `batch_mediapipe.py`'s separate batch
  pipeline, not by `run_offline_track`).
