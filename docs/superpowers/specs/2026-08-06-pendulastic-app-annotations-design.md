# Pendulastic App Annotation Features — Design Spec

**Status:** Approved (brainstorming complete, pending user review of this document)
**Date:** 2026-08-06

## 1. Problem & Existing Landscape

`pendulastic_viewer.py` (the older, standalone data viewer, ~9136 lines) has a
mature two-part "annotation" feature that `pendulastic_app.py` (the newer
unified app, ~2513 lines) does not have at all:

1. **Plot annotations** — `_annotate_plot` (pendulastic_viewer.py:4593) draws
   clinical key-point markers on the angle-vs-time curve: a "Rest" line, a
   release-point vertical line, an A₀ amplitude bracket, a labeled dot on the
   first trough/peak (with φmax ratio), faded dots on later peaks/troughs,
   and an "N cycles" badge. It is driven entirely by the dict returned from
   `compute_pt_params()` (pendulastic_pt_score.py:630) and redrawn any time
   the active trial's angle data changes.
2. **Annotated video export** — `_cmd_export_annotated_video` /
   `_export_annotated_worker` (pendulastic_viewer.py:6946–7142) re-opens the
   source video on a background thread and burns in a skeleton/joint-dot/
   angle-arc HUD (via the module-level `_draw()` helper, line 1638) plus
   angle/time text, frame by frame, writing a new `.mp4`. This is a fully
   separate code path from (1) — it does not burn in the rest-line/A₀/
   peak-trough markers, only the live-tracking HUD.

Confirmed via grep across `pendulastic_app.py` and all 13 active worktrees:
**none of this exists in `pendulastic_app.py` today** — it has zero
annotation-related code beyond an unrelated `from __future__ import
annotations` import.

`pendulastic_app.py` also wires in `pendulastic_workbench.py`'s
`WorkbenchView`, which has its own, different, *manual* milestone-annotation
feature (scrub to a frame, click "Mark Here", labeled from a fixed
vocabulary). That is unrelated and out of scope — this spec targets
**`PostProcessingPanel`** only (the simpler single-trial video/HPE results
view, currently annotation-free), not `WorkbenchView`.

**Scope decision (confirmed with user):** port both pieces (plot annotations
+ annotated video export) into `PostProcessingPanel` only.

## 2. Architecture

**Plot annotations — extract to a shared function, don't duplicate.**

`_annotate_plot`'s drawing logic (items 1–7 below; not its own line-redraw
step, which is a viewer-specific display quirk — flattening pre-release data
to 180° for its particular plot convention) moves into a new module-level
function in `pendulastic_pt_score.py`, the module both `pendulastic_viewer.py`
and `pendulastic_app.py` already import `compute_pt_params` from:

```python
def draw_pt_annotations(ax, params: dict, manual_release: bool = False) -> list:
    """Draw PT key-point markers (rest line, release line, A0 bracket,
    min/ret peaks, faded later peaks/troughs, N badge) onto ax.
    Returns the list of created artists for the caller to track/clear."""
```

`pendulastic_viewer.py`'s `_annotate_plot` becomes a thin wrapper: call
`draw_pt_annotations`, extend `self._plot_annots` with the result, then do
its own line-redraw step as before. `PostProcessingPanel` calls the shared
function directly. One implementation; the viewer keeps working exactly as
it does today, and `pendulastic_app.py` gets the same behavior for free with
no drift risk between two copies of ~150 lines of matplotlib code.

**Annotated video export — capture data already being computed, then reuse
the viewer's drawing primitive.**

`BiomechanicalEngine.run_offline_track` (pendulastic_app.py:192) already
calls `tracker.step(frame)`, which returns `(hip, knee, ankle, angle)` per
frame — but today discards the first three (`_, _, _, angle = tracker.step(
frame)`, line 248) and returns only the angle list. The video export needs
those joint positions. Rather than re-running tracking at export time (the
viewer's fallback path, `_ank_from_ang`, reconstructs a synthetic ankle
position from the angle alone when no tracked point exists — strictly worse
than real data), `run_offline_track` is extended to optionally collect and
return the landmarks it's already computing, at zero extra tracking cost.

The frame-drawing primitive itself, `_draw()` (pendulastic_viewer.py:1638),
is a plain module-level function with no `self`/viewer-instance dependency —
it takes `hip`/`knee`/`ankle` as plain coordinates (or `None`, handled
gracefully per-joint) and a trail list. It is imported directly into
`pendulastic_app.py`, extending the existing guarded-import pattern:

```python
from pendulastic_viewer import _MPBatchTracker, _PatientDetector, _draw, TRAIL_LEN
```

No duplication needed for `_draw` or `TRAIL_LEN`. The export worker's video-
writer fourcc fallback chain (avc1→mp4v→XVID) is small (~15 lines) and is
already duplicated once inside `pendulastic_viewer.py` itself (once for live
recording, once for export) — following that existing convention, it is
duplicated a third time in `pendulastic_app.py`'s new worker rather than
introducing a new shared module for it.

## 3. Components & Data Flow

### 3.1 `pendulastic_pt_score.py`
- **New:** `draw_pt_annotations(ax, params, manual_release=False) -> list[Artist]`
  — extracted from `pendulastic_viewer.py:4593-4727` (the marker-drawing
  portion only).

### 3.2 `pendulastic_viewer.py`
- `_annotate_plot` refactored to call `draw_pt_annotations`, keeping its own
  early-exit guard and line-redraw step unchanged. No behavior change.

### 3.3 `pendulastic_app.py` — `BiomechanicalEngine.run_offline_track`
- New optional parameter `collect_landmarks: bool = False`.
- When `False` (all 3 existing call sites, unchanged): returns `angles: list`
  exactly as today.
- When `True` (the new call in `PostProcessingPanel._on_upload_video`):
  returns `(angles, landmarks)` where `landmarks` is a list the same length
  as `angles`, each entry `(hip, knee, ankle)` or `None` for frames where
  tracking wasn't initialized/failed that frame.

### 3.4 `pendulastic_app.py` — `PostProcessingPanel`
New instance state (initialized in `__init__` alongside `_source_angles`):
- `self._plot_annots: list = []`
- `self._video_path: str | None = None`
- `self._hpe_leg: str = "right"`
- `self._hpe_landmarks: list | None = None`
- `self._last_pt_params: dict | None = None`

Changed methods:
- **`_plot_all_curves`**: after `self._ax.clear()`, reset `self._plot_annots
  = []` (the clear already invalidated the old artist handles — no need to
  call `.remove()` on them).
- **`_show_pt_metrics_from_sources`**: after `p = compute_pt_params(...)`
  succeeds, store `self._last_pt_params = p`, call `draw_pt_annotations(
  self._ax, p)`, store the returned artists in `self._plot_annots`, and
  call `self._canvas.draw_idle()`. (No manual-release UI exists in this
  panel, so `manual_release` is always `False` — out of scope, not
  requested.)
- **`_on_upload_video`**: store `self._video_path = path` and
  `self._hpe_leg = leg`; call `engine.run_offline_track(path, _progress,
  leg=leg.lower(), collect_landmarks=True)`; pass both `angles` and
  `landmarks` through to `_add_hpe_overlay`.
- **`_add_hpe_overlay`**: accept `landmarks` param, store
  `self._hpe_landmarks = landmarks`; enable
  `self.btn_export_video` once angles + landmarks are present.

New UI (row 3, 4th grid column — `columnconfigure(3, weight=1)` added
alongside the existing 0/1/2):
```python
self.btn_export_video = tk.Button(
    self, text="🎬 Export Annotated Video",
    font=("Segoe UI", 10), width=22, height=2, state="disabled",
    command=self._cmd_export_annotated_video)
self.btn_export_video.grid(row=3, column=3, padx=10, pady=12, sticky="w")
```

New methods, modeled on `pendulastic_viewer.py:6946-7149` but simplified
since real per-frame landmarks are already in hand (no `_ank_from_ang`
reconstruction, no pin-override system — that UI doesn't exist in this
panel and isn't being added):

- **`_cmd_export_annotated_video`** (UI thread): guards — refuse if
  `self._video_path` or `self._hpe_landmarks` is missing (mirrors the
  viewer's `self.cap is None or not self.tracker.ready` guard). Prompts
  `filedialog.asksaveasfilename` (default name `{basename}_annotated.mp4`).
  Builds a snapshot dict (video path, fps, angles, landmarks) to hand to the
  worker thread — same rationale as the viewer's `snap` dict: avoid needing
  locks across threads. Disables `self.btn_export_video`, sets
  `self.status_var`, starts `self._export_annotated_worker` on a daemon
  `threading.Thread`.
- **`_export_annotated_worker(self, snap, out_path)`** (background thread):
  re-opens `snap["path"]` via a fresh `cv2.VideoCapture` (independent of any
  UI-held capture — there isn't one on this panel today, but the pattern
  matches the viewer's `cap2` isolation anyway), opens a `cv2.VideoWriter`
  with the avc1→mp4v→XVID fallback chain, then per frame: reads the frame,
  looks up `landmarks[fi]` (`(hip, knee, ankle)` or `None`), maintains a
  rolling ankle trail capped at `TRAIL_LEN`, calls `_draw(frame, hip, knee,
  ankle, angle, trail, scale=1.0)`, burns in angle/elapsed-time text (same
  outlined-`cv2.putText` style as the viewer), writes the frame. When
  `landmarks[fi]` is `None`, `_draw` already handles missing joints
  gracefully (each marker "drawn as soon as it is available" per its
  docstring) — the frame is written with whatever text/partial overlay
  applies, no special-case branch needed. Progress reported every 30 frames
  (matching the viewer's export cadence, pendulastic_viewer.py:7125) via
  `self.after(0, lambda p=pct: self.status_var.set(...))`, consistent with
  the existing `_on_upload_video` progress pattern already in this panel. On
  completion: `self.after(0, ...)` to re-enable the button, set a success
  status string, and `messagebox.showinfo` with the saved path. On
  exception: release resources, re-enable the button, `messagebox.showerror`
  — same shape as the viewer's error path.

## 4. Error Handling

- `run_offline_track` with `collect_landmarks=True` on a video where pose
  detection never initializes: returns `angles` full of `NaN` and
  `landmarks` full of `None` (same as today's angle-only failure mode) —
  `_add_hpe_overlay` already has a guard (`if not angles:`) that surfaces
  "HPE: no pose detected" and does not enable the export button.
  `self._hpe_landmarks` stays `None` in that case, so the export guard
  catches it too even if that early-return path is ever bypassed.
- Export-time video open/write failures (bad codec, disk full, path
  unwritable) surface via `messagebox.showerror`, mirroring the viewer's
  existing exception handling in `_export_annotated_worker`.
- No new failure modes are introduced by `draw_pt_annotations` beyond what
  `_annotate_plot` already guards against — the early-exit-on-insufficient-
  data check (`neutral is None or t_r is None or len(t_r) < 2`) moves with
  the extracted code.

## 5. Testing

- **`run_offline_track` landmark collection**: extend
  `tests/test_biomechanical_engine.py` with a case that calls with
  `collect_landmarks=True` against the existing mocked-tracker fixture and
  asserts the returned `landmarks` list is the same length as `angles` and
  each entry is either `None` or a 3-tuple. The existing
  `test_run_offline_track_returns_angle_per_frame` (default
  `collect_landmarks=False`) is unaffected — confirms backward compatibility.
- **`draw_pt_annotations`**: unit test in a new or existing
  `tests/test_pendulastic_pt_score.py` — build a synthetic `params` dict
  (matching `compute_pt_params`'s real output shape) against a real
  matplotlib `Axes`, call the function, assert it returns a non-empty artist
  list and that calling again after `ax.clear()` doesn't error.
- **`pendulastic_viewer.py` regression**: existing viewer tests (if any
  exercise `_annotate_plot`) must still pass unchanged after the refactor —
  behavior is identical, just relocated.
- **Manual verification**: run `pendulastic_app.py`, upload a video for HPE,
  confirm the plot shows the same rest-line/A₀/peak markers the viewer shows
  for the same trial, then export an annotated video and visually confirm
  the skeleton overlay matches what the viewer's export produces for the
  same source video. End-to-end video export correctness isn't practically
  unit-testable (real MediaPipe tracking, real video I/O) — this is a
  manual/exploratory check, called out explicitly rather than claimed as
  covered by automated tests.
