# Exact Ankle-Pin + Arc-Interpolation Correction for the Review Dialog — Design Spec

**Revision note (2026-08-20):** A `/codex` review of v1 of this spec found the "shared
function extracted from the viewer" framing was false (the real viewer code uses one
fixed knee/radius for the *entire session*; v1's design silently changed that to a
per-frame/per-segment-averaged variant while claiming to be an unchanged extraction),
and that this change broke the exact property — a truly fixed arc center — that makes
the mechanism worth porting in the first place. It also found the pin-persistence,
audit-richness, exclusion-interaction, and validity-checking gaps listed below. Every
`§` reference in this revision resolves one of those findings; see the inline notes.

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
- The viewer's `_ankle_pins` mechanism has never been persisted to disk anywhere in
  `pendulastic_viewer.py` (confirmed: no save/load path for it) — porting it into the
  review dialog, on top of the sidecar this session already shipped, gives it real
  persistence for the first time.
- Scope, decided in brainstorming: port only Phase 1 of the viewer's mechanism (exact
  pin + arc-interpolation between pins). The viewer's Phase 2 (velocity-seeded
  `_MPBatchTracker` continuation beyond the last pin, with ankle-appearance-template
  learning) and its bulk shift-click pin-fill are explicitly deferred — see §6.
- **Not a literal code-sharing extraction.** The viewer's own `_cmd_retrack_from_here()`
  is unchanged by this spec — it keeps its session-wide fixed `hip0`/`kne0` and its own
  Phase 2. The review dialog gets a *new* function, in the same file the viewer's
  helpers already live alongside (`mediapipe_preprocessing.py`), that implements the
  same physical idea (linear interpolation in arc-angle space around one fixed center)
  adapted to the dialog's data model, which has no persistent session-wide tracker
  object. This is an honest port of the algorithm, not a shared-call extraction — the
  original v1 spec's claim of the latter was the first Codex review's central finding.

## 3. Design

### 3.1 Arc-interpolation function (dialog-side, algorithmically matching the viewer)

New pure function in `mediapipe_preprocessing.py`, alongside `knee_angle_from_points()`:

```python
def interpolate_ankle_arc(pins_sorted: list[tuple[int, tuple[float, float]]],
                          anchor_knee: tuple[float, float],
                          anchor_shank_len: float) -> dict[int, tuple[float, float]]:
    """pins_sorted: [(frame_idx, (x, y)), ...] of two or more exact
    clinician-placed ankle positions, sorted by frame_idx. anchor_knee/
    anchor_shank_len: ONE fixed arc center and radius for this whole
    interpolation run, derived by the caller from the FIRST pin's own
    tracked frame (see AnnotatedVideoReviewDialog._on_interpolate_pins) --
    not per-frame, not per-segment-averaged. A single fixed anchor is what
    makes this a genuine zero-drift circular arc, matching
    pendulastic_viewer.py's actual algorithm (which fixes knee0/shank_len
    for its whole tracking session); using a moving or averaged center
    would not (round 1 Codex review finding).

    Returns {frame_idx: (x, y)} interpolated ankle positions for every
    frame spanning consecutive pin pairs (inclusive), computed by linear
    interpolation of arc-angle around anchor_knee, with the same ±180°
    shorter-arc unwrap guard pendulastic_viewer.py's Phase 1 already uses.
    Every PINNED frame's returned position is the clinician's exact
    clicked (x, y), not the arc-projected value -- callers must not
    overwrite this with a recomputed angle (round 1 finding: v1's design
    didn't guarantee exact-pin fidelity)."""
```

The caller (`AnnotatedVideoReviewDialog._on_interpolate_pins`, §3.2) derives
`anchor_knee`/`anchor_shank_len` from `self.landmarks[first_pin_frame]` — the knee
position and hip-knee-to-ankle distance at the frame of the *first* pin in the current
sequence — **and also derives `anchor_hip` from that same frame**, used for every
frame's angle recomputation in §3.2, not each frame's own varying hip. Round 2 Codex
review finding: v1 of this section fixed only the knee/radius anchor but left angle
recomputation using "that frame's existing hip," which (a) doesn't exist for a
previously-excluded frame (`_apply_exclusion` nulls the whole landmark tuple, hip
included, not just the ankle), defeating the exclusion-interaction claim below, and
(b) isn't actually needed once hip is anchored too — `pendulastic_viewer.py`'s real
algorithm fixes *both* `hip0` and `kne0` for its whole session, not knee alone. Fixing
hip at the anchor too makes every written frame depend on exactly one source frame
(the first pin's), never on the frame being overwritten — which is what actually makes
the exclusion-interaction claim in §3.2 true, not just asserted.

All three anchor values (`anchor_hip`, `anchor_knee`, `anchor_shank_len` — where
`anchor_shank_len = |anchor_knee - anchor_ankle|`, so the first pin's frame's ankle is
also read, not just its knee) are validated per §4 before use: hip, knee, AND ankle at
the anchor frame must all be non-`None` with finite coordinates, and
`anchor_shank_len` must be non-degenerate (not ≈0).

### 3.2 Dialog additions

Four new buttons alongside the existing "Fix Person Here" / exclude / save row:

- **"Pin Ankle"** — toggles pin-placement mode (visually indicated, matching how
  `_btn_fix` already disables during a retrack). While armed, clicking on the video
  frame image places or updates a pin at the *current scrubbed frame*, at the clicked
  position. The click handler converts the `tk.Label` widget's click coordinates back
  to original-frame pixel coordinates using a display-scale factor `_redraw()` now
  stores as `self._display_scale` (previously computed inline and discarded — round 1
  P2 finding). Placing a pin requires a valid tracked knee at that frame (§4); auto-
  disarms the mode after one placement.
- **"Clear Pin Here"** — removes the current frame's pin if one exists; no-op with a
  status message otherwise. Appends a `pin_clear` event (§3.3).
- **"Interpolate Pins"** — requires 2+ pins to do anything. Derives `anchor_hip`/
  `anchor_knee`/`anchor_shank_len` from the first pin's frame (§3.1), validates them
  (§4), then calls `interpolate_ankle_arc()` across all current pins. For every frame
  in the returned result, recomputes the angle via `mp_pre.knee_angle_from_points()`
  using `anchor_hip` (fixed, not that frame's own hip — §3.1) and the interpolated/
  exact ankle, and overwrites `self.angles[fi]`/`self.landmarks[fi]` (hip and knee
  components also become `anchor_hip`/`anchor_knee` for that frame, consistent with a
  single fixed reference frame) for every such frame — including ones inside a
  previously-excluded range (see below), since nothing about this computation reads
  that frame's own prior landmark data. Appends one `pin_interpolate` audit event
  (§3.3) recording what was actually used and produced, not just frame numbers (round 1
  finding).
- **"Clear All Pins"** — (new; needed once pins are a first-class piece of state with
  their own clear semantics) removes every current pin; appends one `pin_clear` event
  with `frame: null` meaning "all." **Reserved exclusively for this button** — the
  retrack-overlap auto-clear below uses per-pin events instead (round 2 finding: v1 of
  this section had both cases using `frame: null`, which would incorrectly wipe pins
  outside the retracked range too).

Pinned frames get a distinct marker on the overlay. Rather than changing `_draw()`'s
signature (shared with `pendulastic_viewer.py`'s own rendering and the video-export
path — round 1 P2 finding: an unsequenced cross-module API change), the dialog draws
the pin marker as a small additional pass in its own `_redraw()`, after `_draw()`
returns its frame.

**Current pin set is derived by replaying `self._events`**, not stored as a separate
field: filter to `pin_set`/`pin_clear` events in order, applying each (`pin_set` adds/
updates `{frame: (x, y)}`, `pin_clear` with a `frame` removes that one, `pin_clear` with
`frame: null` empties the set). This is reconstructable from the same event log already
being persisted, so reopening a saved sidecar recovers the exact current pins without a
redundant top-level field to keep in sync (round 1 finding: v1 had no way to
reconstruct current pins after a clear).

**Interaction with frame-range exclusion:** `_apply_exclusion()` is unchanged and stays
destructive (sets angle to `NaN`, landmarks to `None`). Because §3.1's anchor (hip,
knee, AND shank length — round 2 fix) is derived entirely from the first pin's frame,
**no frame being written by "Interpolate Pins" is ever read from** — every frame in the
span gets `(anchor_hip, anchor_knee, interpolated_or_exact_ankle)`, full stop, whether
or not that frame currently holds `NaN`/`None` from a prior exclusion. This is what
makes the "no un-exclude semantics needed" claim actually true (round 2 finding: v1's
first pass still said interpolation reads "that frame's own hip," which contradicted
this claim for any excluded frame). The dropped v1 idea — "placing a pin implicitly
un-excludes nearby frames" — remains unneeded and unimplemented; nothing is being
un-excluded, frames are simply overwritten with freshly-computed values, the same as
any other frame a retrack or interpolation touches.

**Interaction with "Fix Person Here" / retrack:** a retrack overwrites the landmark
data pins were validated against. `_on_retrack_done` (existing method) now also removes
any pin whose frame falls within `[start_frame, len(self.angles))` — **the full
remainder of the array, not `[start_frame, start_frame + len(new_angles))`** (round 3
finding: `_splice_from`, which `_on_retrack_done` already calls, always fills the
*entire* suffix from `start_frame` to the end of `self.angles`, padding with `nan`/
`None` when the retrack returns fewer frames than that — so a short retrack still
overwrites pinned frames beyond its own returned length, in the padded tail, not just
within the returned data). Every removed pin appends one `pin_clear` event **with that
pin's specific `frame`** — never `frame: null` (round 2 finding: v1 of this paragraph
said `frame: null`, contradicting §3.3's exclusive reservation of that value for "Clear
All Pins"). A retrack that overlaps zero pins appends no `pin_clear` events at all.

### 3.3 Schema v2

`CORRECTIONS_SCHEMA_VERSION` bumps from `1` to `2`. No migration path — `_load_corrections`
already rejects any `schema_version` mismatch, so existing v1 sidecars are ignored
(treated as "no saved corrections"). This remains a deliberate, accepted tradeoff (v1
shipped hours before this spec, essentially no real clinical data exists under it yet)
— flagged explicitly here, not glossed over, per the round-1 review's fair point that
"hours old" is an assumption, not a guarantee; the decision stands anyway per the
brainstorming discussion.

**However** (round 2 finding): *ignoring* a v1 sidecar on load is one thing; a v2 save
subsequently **overwriting that same file path** is a stronger, avoidable form of data
loss — the old content isn't just ignored, it's destroyed. `_save_corrections` gets one
small addition: **unconditionally** back up any file that already exists at the
destination path before writing the new one — not gated on successfully parsing its
`schema_version` (round 3 finding: a version-conditional check leaves an unparseable/
malformed existing file with no defined preservation path, still silently destroyed,
which contradicts the "never destroyed" claim) — using a **timestamp-suffixed** backup
name, e.g. `<path>.bak.<UTC ISO 8601 timestamp with colons stripped, filesystem-safe>`,
never a fixed name like `<path>.v{N}.bak` (round 3 finding: a fixed name still collides
and silently clobbers an earlier backup on a second save that also finds a stale file).
This is a few lines, not a real migration, and it means no sidecar content — parseable
or not — is ever silently destroyed.

New event types appended to the existing `events` list:
- `{"type": "pin_set", "frame": int, "x": float, "y": float, "at": iso8601}`
- `{"type": "pin_clear", "frame": int | null, "at": iso8601}` — `null` means "all pins,"
  emitted **only** by "Clear All Pins." The retrack-overlap auto-clear (§3.2) always
  emits one of these per removed pin with that pin's specific `frame`, never `null`.
- `{"type": "pin_interpolate", "pins": [{"frame": int, "x": float, "y": float}, ...],
  "anchor_frame": int, "anchor_hip": [float, float], "anchor_knee": [float, float],
  "anchor_shank_len": float, "frame_range": [int, int], "at": iso8601}` — records the
  full input/output of the run, not just which frames were pinned (round 1 finding),
  including `anchor_hip` now that it's part of what's actually used (round 2 fix,
  §3.1).

New top-level doc field, sibling to `schema_version`/`video_fingerprint`/`leg`:
- `"tracker_version": str` — identifies the tracking engine/config that produced the
  current `corrected_angles`/`corrected_landmarks`. Sourced from `_MP_MODEL`, imported
  into `video_review_dialog.py` alongside the other names it already pulls from
  `pendulastic_viewer.py` (`_draw`, `TRAIL_LEN`, `resolve_person_click`) — round 1
  finding that `_MP_MODEL` wasn't actually available in this module as v1 assumed.
  Format: `f"_MPBatchTracker;model={os.path.basename(_MP_MODEL)}"`. Stamped fresh on
  every save. This is still the minimum viable provenance fix, explicitly not a replica
  of the RMSE pipeline's `_implementation_fingerprint()` hashing (see §6) — a plain
  identifying string, honestly labeled as such.

## 4. Error handling

- **Click while unarmed:** no-op (unchanged from today).
- **Interpolate with fewer than 2 pins:** status message, no state change.
- **A pin frame beyond `len(self.angles)`:** guarded the same way `_on_fix_person_here`
  already guards an out-of-range `_frame_idx` — status message, no pin placed.
- **Pin placement validity:** before accepting a pin, validate that frame's `knee` is
  non-`None` with a finite coordinate (only the knee is needed to place a pin — the
  clinician supplies the ankle via the click itself). Failure: status message ("Cannot
  pin here — no valid tracked knee at this frame"), no state change.
- **Interpolation-anchor validity:** before deriving `anchor_hip`/`anchor_knee`/
  `anchor_shank_len` from the first pin's frame, validate that frame's `hip`, `knee`,
  AND `ankle` are all non-`None` with finite coordinates (round 2 fix — the anchor now
  needs all three, not just knee, since hip is fixed too and shank length is computed
  from knee+ankle; see §3.1). Failure: status message, no state change, no partial
  write.
- **`anchor_shank_len` degenerate (≈0, i.e. anchor knee/ankle collapsed to one point):**
  same rejection path as above — cannot define an arc with zero radius.
- **Retrack in progress:** all four new buttons guarded behind the existing
  `_retrack_in_progress` check, same pattern as "Exclude From/To Here" and "Save
  Corrections."
- **A pin's frame gets overwritten by a later retrack:** the pin is auto-removed
  (§3.2), not silently left pointing at now-different data.
- **Save/load:** unchanged mechanics from the shipped feature (fingerprint validation,
  per-leg path, atomic write, structural validation) — only the doc's field set grows,
  validated the same way (v2 structural checks additionally confirm `pin_set`/
  `pin_clear`/`pin_interpolate` events, when present, have well-formed fields; a
  malformed event of these new types is treated as sidecar-invalid, same as any other
  structural failure today).

## 5. Testing

Following the existing `tests/test_video_review_dialog.py` conventions (and wherever
`knee_angle_from_points()` is already tested, for the new `mediapipe_preprocessing.py`
function):

- `interpolate_ankle_arc()` as a pure function: a two-pin case against a known fixed
  anchor with a known expected midpoint angle; the ±180° arc-wrap edge case (ported
  from the viewer's existing guard); every pinned frame's returned position exactly
  equals its clicked input, not an arc-projected approximation; 3+ pins interpolating
  correctly per-segment around the SAME fixed anchor (not re-deriving a new anchor per
  segment).
- Dialog-level: click-coordinate-to-frame-pixel conversion (including the
  `_MAX_DISPLAY_WIDTH` scale-down case, via the new `self._display_scale`); pin
  placement/clearing/clear-all; current-pin-set reconstruction from a mixed
  `pin_set`/`pin_clear` event sequence, including the `frame: null` clear-all case;
  "Interpolate Pins" updating `self.angles`/`self.landmarks` correctly **including
  over a previously-excluded span** (asserting the written hip/knee/ankle come from the
  anchor frame, not the excluded frame's now-`None` data) and appending a correctly-
  populated `pin_interpolate` event; pin placement rejected at an invalid knee frame;
  interpolation-anchor rejected when the first pin's hip/ankle (not just knee) is
  missing/degenerate/non-finite; a retrack overlapping N pinned frames appends exactly
  N per-pin `pin_clear` events (never `frame: null`) and leaves pins outside the
  retracked range untouched; all four buttons no-op during `_retrack_in_progress`.
- Save/load round trip: a v2 doc with all three new event types and `tracker_version`
  saves and reloads correctly, including correct pin-set reconstruction after reload; a
  v1 doc is rejected on load exactly as the existing version-mismatch test already
  covers; saving over an existing sidecar (v1, wrong-version, or unparseable/malformed
  JSON) always backs it up with a timestamp-suffixed name instead of destroying it
  (§3.3's round 3 fix), verified byte-identical to the pre-save original; two saves in
  quick succession that each find a stale file produce two distinct backup files, not
  one overwriting the other.
- Retrack-pin interaction: a retrack whose returned data is *shorter* than the
  remaining suffix (padded by `_splice_from`) still clears every pin in
  `[start_frame, len(self.angles))`, including pins in the padded tail beyond the
  retrack's own returned length (round 3 fix) — not just pins within the returned
  data's length.

**Known, accepted characteristic (not a bug to test against):** because pinned frames
hold the clinician's exact click while in-between frames are projected onto the fixed
arc, a pin whose click sits off the anchor's true radius produces a small **ankle
*position*** discontinuity at the frame immediately adjacent to that pin — the
displayed dot's distance from the knee jumps slightly, since the interpolated
neighbor's ankle is projected onto the fixed radius while the pin frame itself shows
the raw click. This is **not** a knee-*angle* discontinuity (round 3 correction to the
round 2 documentation, which claimed the latter): `interpolate_ankle_arc()`'s arc-angle
(theta) at each pin's own frame is computed directly from that pin's own clicked
position via `atan2`, so the resulting angle from `knee_angle_from_points()` already
matches continuously at the boundary regardless of the radius mismatch — only the
raw (x, y) position shown differs. `pendulastic_viewer.py`'s own Phase 1 has the
identical position-level characteristic (it also force-restores exact pin positions
after interpolating) — this spec inherits it deliberately rather than introducing a
new "snap pins onto the arc" behavior the viewer itself doesn't have.

## 6. Out of scope

- The viewer's Phase 2 (velocity-seeded `_MPBatchTracker` continuation beyond the last
  pin, with ankle-appearance-template learning from the pin frame) — deferred; "Fix
  Person Here" remains the mechanism for frames past the last pin in this pass.
- The viewer's bulk shift-click pin-autofill — deferred.
- A v1→v2 sidecar migration path — decided unnecessary given v1's near-zero real
  adoption at time of writing (§3.3).
- Any change to `pendulastic_viewer.py` itself — its own `_cmd_retrack_from_here`,
  Phase 2, and bulk-fill behavior are all unchanged. `interpolate_ankle_arc()` is new
  code, not a refactor of the viewer's existing function (§2).
- Full `_implementation_fingerprint()`-style provenance hashing for `tracker_version`
  — a simple identifying string is the minimum viable fix for the gap Codex flagged,
  not a replica of the RMSE pipeline's heavier fingerprinting.
