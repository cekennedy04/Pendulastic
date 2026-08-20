# Exact Ankle-Pin + Arc-Interpolation Correction for the Review Dialog — Design Spec

## 1. Goal

Port the accuracy-critical part of `pendulastic_viewer.py`'s ankle-pin correction
mechanism — exact single-point pinning plus arc-interpolation between pins — into
`video_review_dialog.py`'s `AnnotatedVideoReviewDialog`, which currently only offers
the coarser "Fix Person Here" (re-seed-and-retrack) correction. Persist pin and
interpolation events into the corrections sidecar (schema bump to v2), including the
exact corrected coordinate and tracker/model version per event — closing the audit-trail
gap the sidecar currently has.

## 2. Background / Why

- The original `2026-08-12-annotated-video-review-design.md` spec explicitly scoped
  `pendulastic_viewer.py`'s multi-pin arc-interpolation retrack as **out of scope**
  (§7: "not ported (§2)"), choosing the simpler single-point repick-and-retrack-forward
  mechanism instead. That was a considered decision at the time, not an oversight — the
  simpler mechanism covers the common "tracker locked onto the wrong person" failure
  mode and was faster to ship.
- This session's broader MediaPipe-accuracy investigation led to a `/codex` consult
  comparing the two mechanisms directly. Finding: they already share the same
  underlying tracker (`pendulastic_app.py`'s `BiomechanicalEngine.run_offline_track`
  instantiates `pendulastic_viewer._MPBatchTracker` — the same class the viewer's own
  UI drives), so this was never a choice between two tracking algorithms. It's a choice
  between two **correction UX layers** on top of one tracker. Codex's verdict: the
  viewer's exact-pin + interpolation is more accurate (a decaying directional prior
  alone, which is all "Fix Person Here" effectively gets from the shared tracker, lets
  a wrong lock-on recur once the ~25-frame decay fades) and better clinician UX (fewer,
  more precise interventions vs. repeated re-seeding every time tracking fails again).
  Recommendation: standardize on the pin+interpolation UX, ported into the review
  dialog, and fix the sidecar's audit log to record actual corrected coordinates and
  model/config version, not just a frame number and timestamp.
- The viewer's `_ankle_pins` mechanism has never been persisted to disk anywhere in
  `pendulastic_viewer.py` (confirmed: no save/load path for it) — porting it into the
  review dialog, on top of the sidecar this session already shipped, gives it real
  persistence for the first time.
- Scope, decided in brainstorming: port only Phase 1 of the viewer's mechanism (exact
  pin + arc-interpolation between pins). The viewer's Phase 2 (velocity-seeded
  `_MPBatchTracker` continuation beyond the last pin, with ankle-appearance-template
  learning) and its bulk shift-click pin-fill are explicitly deferred — see §7.

## 3. Design

### 3.1 Shared arc-interpolation function

Extract the pure geometry currently inline in `pendulastic_viewer.py`'s
`_cmd_retrack_from_here()` (Phase 1 only — the loop computing `_arc_theta`/`_arc_pos`
and linearly interpolating arc-angle between consecutive pins, with the existing
±180° unwrap guard) into a new function in `mediapipe_preprocessing.py` (already the
repo's home for pure per-frame geometry helpers like `knee_angle_from_points()`;
confirmed no import cycle — it currently only imports `cv2`/`numpy`, and
`pendulastic_viewer.py` does not import it today):

```python
def interpolate_ankle_arc(pins: dict[int, tuple[float, float]],
                          knee_at_frame: Callable[[int], tuple[float, float] | None],
                          shank_len: float) -> dict[int, tuple[float, float]]:
    """pins: {frame_idx: (x, y)} of two or more exact clinician-placed ankle
    positions. knee_at_frame(fi) returns that frame's own tracked knee
    position (or None if untracked). Returns {frame_idx: (x, y)} ankle
    positions for every frame spanning the pins (inclusive), interpolated in
    arc-angle space around each frame's own knee position -- not a fixed
    knee0, per design decision in the corresponding spec's §3.1. shank_len
    is the arc radius, averaged from the two pins bounding each segment.
    Frames whose knee_at_frame returns None are skipped (left unchanged by
    the caller) -- there is no arc center to interpolate against."""
```

Both `pendulastic_viewer.py`'s `_cmd_retrack_from_here()` and
`video_review_dialog.py` call this shared function; `pendulastic_viewer.py`'s
Phase 2 (unchanged) still runs after it, using its own `hip0`/`kne0` fixed-session
attributes as it does today — only the interpolation math itself moves, not the
viewer's own calling convention.

### 3.2 Dialog additions

Three new buttons alongside the existing "Fix Person Here" / exclude / save row:

- **"Pin Ankle"** — toggles pin-placement mode (visually indicated, e.g. button
  relief/text change, matching how `_btn_fix` already disables during a retrack).
  While armed, clicking on the video frame image places or updates a pin at the
  *current scrubbed frame*, at the clicked position. The click handler converts the
  `tk.Label` widget's click coordinates back to original-frame pixel coordinates by
  inverting the same scale factor `_redraw()` already applies when the frame exceeds
  `_MAX_DISPLAY_WIDTH`. Placing a pin auto-disarms the mode (one placement per arm,
  matching the deliberate two-step feel of "Exclude From/To Here" rather than a
  click-anywhere-anytime interaction that risks accidental pins).
- **"Clear Pin Here"** — removes the current frame's pin if one exists; no-op with a
  status message otherwise.
- **"Interpolate Pins"** — requires 2+ pins to do anything (a status message explains
  this otherwise). Runs `interpolate_ankle_arc()` across every consecutive pin pair,
  using `shank_len` derived from each pin's own frame (`|ankle - knee|` at that pin,
  averaged per segment per §3.1's docstring), updates `self.angles`/`self.landmarks`
  for every spanned frame (recomputing the angle from the interpolated ankle plus that
  frame's existing hip/knee, via the same `mp_pre.knee_angle_from_points()` the rest
  of the pipeline already uses), and appends one `pin_interpolate` audit event (§3.3).

Pinned frames get a distinct marker on the overlay (reusing `_draw()`'s existing
trail/landmark rendering path with a new marker color/shape for "exact pin," not a
new renderer) so it's visually unambiguous which frames hold a clinician-placed exact
point versus tracked/interpolated ones.

**Interaction with frame-range exclusion:** placing a pin, or running "Interpolate
Pins," on a frame that currently falls inside a previously-excluded range implicitly
un-excludes those specific frames (the clinician is actively correcting them, which is
a stronger, more recent signal of intent than the earlier exclusion). This matches the
existing "last action wins for any given frame" semantics already established for
retrack-vs-exclusion overlap in the shipped feature — no new conflict-resolution
mechanism, just the same precedent applied consistently.

### 3.3 Schema v2

`CORRECTIONS_SCHEMA_VERSION` bumps from `1` to `2`. Per the brainstorming decision, no
migration path — `_load_corrections` already rejects any `schema_version` mismatch, so
existing v1 sidecars are simply ignored (treated as "no saved corrections"), which is
acceptable given v1 shipped only hours before this spec and essentially no real
clinical data has been saved under it yet.

New event types appended to the existing `events` list:
- `{"type": "pin_set", "frame": int, "x": float, "y": float, "at": iso8601}`
- `{"type": "pin_interpolate", "pin_frames": [int, ...], "at": iso8601}`

New top-level doc field, sibling to `schema_version`/`video_fingerprint`/`leg`:
- `"tracker_version": str` — identifies the tracking engine/config that produced the
  current `corrected_angles`/`corrected_landmarks` (e.g. `"_MPBatchTracker"` plus
  whatever model-complexity/`_MP_MODEL` constant is already available in this module's
  scope). Stamped fresh on every save, closing the "no model provenance" gap Codex
  flagged — this is the minimum viable provenance record, not a full replica of the
  RMSE pipeline's `_implementation_fingerprint()` hashing scheme, which is out of scope
  here (see §7).

## 4. Error handling

- **Click while unarmed:** no-op — clicking the video frame image without "Pin Ankle"
  armed does nothing (today's behavior, unchanged).
- **Interpolate with fewer than 2 pins:** status message, no state change.
- **A pin frame beyond `len(self.angles)`:** guarded the same way `_on_fix_person_here`
  already guards an out-of-range `_frame_idx` — status message, no pin placed.
- **`knee_at_frame(fi)` returns `None` for some frame inside an interpolation span**
  (that frame has no tracked knee at all): per §3.1, that frame is left unchanged by
  `interpolate_ankle_arc()` — not set to a nonsensical position, not silently NaN'd
  either, since it may already hold valid prior data (e.g. from a previous fix). The
  caller must not overwrite a frame the interpolation function chose to skip.
- **Retrack in progress:** all three new buttons guarded behind the existing
  `_retrack_in_progress` check, same pattern as "Exclude From/To Here" and "Save
  Corrections."
- **Save/load:** unchanged mechanics from the shipped feature (fingerprint validation,
  per-leg path, atomic write, structural validation) — only the doc's field set grows.

## 5. Testing

Following the existing `tests/test_video_review_dialog.py` conventions:

- `interpolate_ankle_arc()` as a pure function in a new `tests/test_mediapipe_preprocessing.py`-adjacent (or same-file, matching where `knee_angle_from_points()` is
  already tested) test module: a two-pin straight interpolation case with a known
  expected midpoint angle, the ±180° arc-wrap edge case (ported from the viewer's
  existing guard, verified it still produces the shorter arc), a frame with
  `knee_at_frame` returning `None` is left out of the result, and 3+ pins interpolating
  correctly per-segment.
- Dialog-level: click-coordinate-to-frame-pixel conversion (including the
  `_MAX_DISPLAY_WIDTH` scale-down case), pin placement/clearing, "Interpolate Pins"
  updating `self.angles`/`self.landmarks` correctly and appending the right event,
  pinning/interpolating a frame inside a prior exclusion range un-excludes it (§3.2),
  and all three buttons no-op during `_retrack_in_progress`.
- Save/load round trip: a v2 doc with `pin_set`/`pin_interpolate` events and
  `tracker_version` saves and reloads correctly; a v1 doc (missing `tracker_version`,
  old schema) is rejected exactly as the existing version-mismatch test already covers,
  no new migration test needed per §3.3.

## 6. Out of scope

- The viewer's Phase 2 (velocity-seeded `_MPBatchTracker` continuation beyond the last
  pin, with ankle-appearance-template learning from the pin frame) — deferred per the
  brainstorming decision; "Fix Person Here" remains the mechanism for frames past the
  last pin in this pass.
- The viewer's bulk shift-click pin-autofill — deferred.
- A v1→v2 sidecar migration path — decided unnecessary given v1's near-zero real
  adoption at time of writing.
- Porting this mechanism back into `pendulastic_viewer.py` itself changing behavior
  there — it keeps calling the newly-shared `interpolate_ankle_arc()` but its own
  Phase 2 and bulk-fill behavior are unchanged.
- Full `_implementation_fingerprint()`-style provenance hashing for `tracker_version`
  — a simple identifying string is the minimum viable fix for the gap Codex flagged,
  not a replica of the RMSE pipeline's heavier fingerprinting.
