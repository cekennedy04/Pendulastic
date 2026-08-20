# Exact Ankle-Pin + Arc-Interpolation Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `pendulastic_viewer.py`'s exact ankle-pin + arc-interpolation correction mechanism into `video_review_dialog.py`'s `AnnotatedVideoReviewDialog`, replacing "Fix Person Here" as the primary correction tool for salvageable frames, and bump the corrections sidecar to schema v2 with per-event coordinates and tracker provenance.

**Architecture:** A new pure function `interpolate_ankle_arc()` in `mediapipe_preprocessing.py` implements zero-drift circular-arc interpolation around one fixed anchor (hip/knee/shank-length), derived from the *first pin's own frame* — never per-frame, never averaged. Pin state is event-sourced (new `pin_set`/`pin_clear` event types, reconstructed by replaying `self._events`) rather than a separate mutable field. Four new dialog buttons wire pin placement (via a click on the video frame), clearing, and interpolation together; a retrack auto-clears any pin whose frame it overwrites.

**Tech Stack:** Python, Tkinter, OpenCV (`cv2`), NumPy, pytest (existing `tests/test_video_review_dialog.py` / `tests/test_mediapipe_preprocessing.py` conventions — real withdrawn `tk.Tk()` root, `_write_test_video`, `_FakeEngine`, `_SyncThread` monkeypatch).

**Spec:** `docs/superpowers/specs/2026-08-20-pin-interpolation-correction-design.md`

## Global Constraints

- No migration path for v1→v2 sidecars — `_load_corrections` rejects any `schema_version` mismatch (unchanged mechanism, new version number).
- Any file found at a sidecar's destination path before a save is **unconditionally** backed up (not gated on being parseable) to a timestamp-suffixed, collision-retried name — never silently overwritten or destroyed.
- The arc-interpolation anchor (hip, knee, shank length) is derived from exactly **one** frame — the first pin's — for an entire "Interpolate Pins" run. Never per-frame, never averaged across pins.
- Every pinned frame's ankle equals the clinician's exact clicked `(x, y)` — never an arc-projected approximation.
- `pin_clear` events use `frame: null` **only** for "Clear All Pins." The retrack-overlap auto-clear always emits one `pin_clear` per removed pin with that pin's specific `frame`.
- All four new buttons (Pin Ankle, Clear Pin Here, Interpolate Pins, Clear All Pins) no-op while `self._retrack_in_progress` is `True`, matching the existing pattern for "Exclude From/To Here" and "Save Corrections."
- No changes to `pendulastic_viewer.py` itself. `interpolate_ankle_arc()` is new code, not a refactor of its `_cmd_retrack_from_here()`.
- `_MAX_DISPLAY_WIDTH`, `_splice_from`, `_apply_exclusion`, `_video_fingerprint`, `_corrections_path`, `_other_leg`, `_landmark_to_json`/`_landmark_from_json`, `_build_corrections_doc`, `_now_iso` are existing functions in `video_review_dialog.py` — reuse them, don't reimplement.

---

### Task 1: `interpolate_ankle_arc()` pure function

**Files:**
- Modify: `mediapipe_preprocessing.py` (add function after `knee_angle_from_points`, ~line 58)
- Test: `tests/test_mediapipe_preprocessing.py` (append after the existing `knee_angle_from_points` tests)

**Interfaces:**
- Produces: `interpolate_ankle_arc(pins_sorted: list[tuple[int, tuple[float, float]]], anchor_knee: tuple[float, float], anchor_shank_len: float) -> dict[int, tuple[float, float]]`

- [ ] **Step 1: Write the failing tests**

```python
def test_interpolate_ankle_arc_quarter_circle_midpoint():
    # anchor knee at origin, radius 1. Pin A at frame 0 -> ankle (1, 0)
    # (theta=0). Pin B at frame 10 -> ankle (0, 1) (theta=pi/2). Midpoint
    # frame 5 should sit at theta=pi/4: (cos(pi/4), sin(pi/4)).
    pins_sorted = [(0, (1.0, 0.0)), (10, (0.0, 1.0))]
    result = mp_pre.interpolate_ankle_arc(pins_sorted, (0.0, 0.0), 1.0)
    assert set(result.keys()) == set(range(0, 11))
    mx, my = result[5]
    assert math.isclose(mx, math.cos(math.pi / 4), abs_tol=1e-6)
    assert math.isclose(my, math.sin(math.pi / 4), abs_tol=1e-6)


def test_interpolate_ankle_arc_pinned_frames_return_exact_click():
    # Pin B's click (0.3, 1.4) is NOT on the anchor's radius-1 circle --
    # the function must still return it verbatim at frame 10, not a
    # radius-1 projection of it.
    pins_sorted = [(0, (1.0, 0.0)), (10, (0.3, 1.4))]
    result = mp_pre.interpolate_ankle_arc(pins_sorted, (0.0, 0.0), 1.0)
    assert result[0] == (1.0, 0.0)
    assert result[10] == (0.3, 1.4)


def test_interpolate_ankle_arc_takes_shorter_arc_across_wrap():
    # Pin A at theta=170deg, Pin B at theta=-170deg (== 190deg). The
    # shorter arc goes 170 -> 180 -> 190 (20deg), not 170 -> 0 -> -170
    # (340deg the long way). Midpoint (frame 5 of 0..10) should land at
    # exactly 180deg: (-1, 0).
    theta_a = math.radians(170.0)
    theta_b = math.radians(-170.0)
    ank_a = (math.cos(theta_a), math.sin(theta_a))
    ank_b = (math.cos(theta_b), math.sin(theta_b))
    pins_sorted = [(0, ank_a), (10, ank_b)]
    result = mp_pre.interpolate_ankle_arc(pins_sorted, (0.0, 0.0), 1.0)
    mx, my = result[5]
    assert math.isclose(mx, -1.0, abs_tol=1e-6)
    assert math.isclose(my, 0.0, abs_tol=1e-6)


def test_interpolate_ankle_arc_three_pins_same_anchor_per_segment():
    # 3 pins, 2 segments. Both segments must interpolate around the SAME
    # anchor_knee/anchor_shank_len passed in -- not a per-segment radius
    # re-derived from each pin pair (which would make segment 2 sit on a
    # different circle than segment 1).
    pins_sorted = [(0, (1.0, 0.0)), (10, (0.0, 1.0)), (20, (-1.0, 0.0))]
    result = mp_pre.interpolate_ankle_arc(pins_sorted, (0.0, 0.0), 1.0)
    for fi in range(0, 21):
        x, y = result[fi]
        assert math.isclose(math.hypot(x, y), 1.0, abs_tol=1e-6), (
            f"frame {fi} not on the anchor's radius-1 circle: ({x}, {y})")
    assert set(result.keys()) == set(range(0, 21))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -k interpolate_ankle_arc -v`
Expected: FAIL with `AttributeError: module 'mediapipe_preprocessing' has no attribute 'interpolate_ankle_arc'`

- [ ] **Step 3: Add `import math` to `mediapipe_preprocessing.py` and write the function**

```python
import math

...

def interpolate_ankle_arc(pins_sorted, anchor_knee, anchor_shank_len):
    """pins_sorted: [(frame_idx, (x, y)), ...] of two or more exact
    clinician-placed ankle positions, sorted by frame_idx. anchor_knee/
    anchor_shank_len: ONE fixed arc center and radius for this whole
    interpolation run, derived by the caller from the FIRST pin's own
    tracked frame -- not per-frame, not per-segment-averaged. A single
    fixed anchor is what makes this a genuine zero-drift circular arc,
    matching pendulastic_viewer.py's actual algorithm (which fixes
    knee0/shank_len for its whole tracking session).

    Returns {frame_idx: (x, y)} interpolated ankle positions for every
    frame spanning consecutive pin pairs (inclusive), computed by linear
    interpolation of arc-angle around anchor_knee, with the same shorter-
    arc ±180-degree unwrap guard pendulastic_viewer.py's Phase 1 uses.
    Every PINNED frame's returned position is the clinician's exact
    clicked (x, y), not the arc-projected value."""
    kx, ky = anchor_knee

    def _theta(ank):
        return math.atan2(ank[1] - ky, ank[0] - kx)

    def _pos(theta):
        return (kx + math.cos(theta) * anchor_shank_len,
                ky + math.sin(theta) * anchor_shank_len)

    result = {}
    for seg_i in range(len(pins_sorted) - 1):
        fi_a, ank_a = pins_sorted[seg_i]
        fi_b, ank_b = pins_sorted[seg_i + 1]
        theta_a = _theta(ank_a)
        theta_b = _theta(ank_b)
        while theta_b - theta_a > math.pi:
            theta_b -= 2 * math.pi
        while theta_b - theta_a < -math.pi:
            theta_b += 2 * math.pi
        span = max(fi_b - fi_a, 1)
        for fi in range(fi_a, fi_b + 1):
            if fi == fi_a:
                result[fi] = ank_a
            elif fi == fi_b:
                result[fi] = ank_b
            else:
                t = (fi - fi_a) / span
                theta = theta_a + t * (theta_b - theta_a)
                result[fi] = _pos(theta)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mediapipe_preprocessing.py -k interpolate_ankle_arc -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add mediapipe_preprocessing.py tests/test_mediapipe_preprocessing.py
git commit -m "feat: add interpolate_ankle_arc pure function for pin-based correction"
```

---

### Task 2: Schema v2 fields — `tracker_version` and the bumped version number

**Files:**
- Modify: `video_review_dialog.py` (import line ~36, `CORRECTIONS_SCHEMA_VERSION` ~line 40, `_build_corrections_doc` ~line 113, `_save_corrections` ~line 125, `_load_corrections` ~line 161, `_on_save_corrections` ~line 535)
- Test: `tests/test_video_review_dialog.py` (append near the existing `_build_corrections_doc`/save/load tests)

**Interfaces:**
- Consumes: `pendulastic_viewer._MP_MODEL` (existing module-level constant, a resolved model asset path)
- Produces: `_build_corrections_doc(fingerprint, events, angles, landmarks, leg, tracker_version)`, `_save_corrections(video_path, fingerprint, events, angles, landmarks, leg, tracker_version)`, `_load_corrections(video_path, leg)` (unchanged signature — `tracker_version` is read from the loaded doc, not passed in), `_tracker_version() -> str` (new small helper)

- [ ] **Step 1: Write the failing tests**

```python
def test_tracker_version_helper_includes_mp_model_basename():
    from video_review_dialog import _tracker_version
    import pendulastic_viewer as pv
    tv = _tracker_version()
    assert tv.startswith("_MPBatchTracker;model=")
    assert os.path.basename(pv._MP_MODEL) in tv


def test_build_corrections_doc_includes_tracker_version_and_schema_2():
    from video_review_dialog import _build_corrections_doc, CORRECTIONS_SCHEMA_VERSION
    doc = _build_corrections_doc({"size": 1}, [], [1.0], [None], "left", "tv-string")
    assert doc["schema_version"] == 2
    assert CORRECTIONS_SCHEMA_VERSION == 2
    assert doc["tracker_version"] == "tv-string"


def test_save_then_load_round_trip_preserves_tracker_version(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _load_corrections, _tracker_version)
    video_path = str(tmp_path / "tv.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    tv = _tracker_version()
    _save_corrections(video_path, fp, [], [0.0] * 3, [None] * 3, "left", tv)
    loaded = _load_corrections(video_path, "left")
    assert loaded is not None
    assert loaded["tracker_version"] == tv
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k tracker_version -v`
Expected: FAIL — `ImportError: cannot import name '_tracker_version'` and `TypeError` on the extra positional args to `_build_corrections_doc`/`_save_corrections`.

- [ ] **Step 3: Implement**

Update the import line:

```python
from pendulastic_viewer import _draw, TRAIL_LEN, resolve_person_click, _MP_MODEL
```

Bump the schema version:

```python
CORRECTIONS_SCHEMA_VERSION = 2
```

Add the helper, right after `_now_iso`:

```python
def _tracker_version() -> str:
    """Minimum-viable provenance string for a corrections doc -- identifies
    the tracking engine/config that produced corrected_angles/
    corrected_landmarks. Not a full implementation-fingerprint hash (see
    rmse_pipeline_common.py for that heavier mechanism, out of scope here)
    -- a plain identifying string, honestly labeled as such."""
    return f"_MPBatchTracker;model={os.path.basename(_MP_MODEL)}"
```

Update `_build_corrections_doc`:

```python
def _build_corrections_doc(fingerprint: dict, events: list, angles: list,
                           landmarks: list, leg: str, tracker_version: str) -> dict:
    return {
        "schema_version": CORRECTIONS_SCHEMA_VERSION,
        "video_fingerprint": dict(fingerprint),
        "leg": leg,
        "tracker_version": tracker_version,
        "events": list(events),
        "corrected_angles": list(angles),
        "corrected_landmarks": [_landmark_to_json(lm) for lm in landmarks],
    }
```

Update `_save_corrections`'s signature and its call to `_build_corrections_doc`:

```python
def _save_corrections(video_path: str, fingerprint: dict, events: list,
                      angles: list, landmarks: list, leg: str,
                      tracker_version: str) -> None:
    """...(existing docstring unchanged)..."""
    doc = _build_corrections_doc(fingerprint, events, angles, landmarks, leg,
                                 tracker_version)
    ...  # rest unchanged
```

Update `_on_save_corrections` to pass it:

```python
def _on_save_corrections(self) -> None:
    if self._retrack_in_progress:
        return
    try:
        fingerprint = _video_fingerprint(self.video_path)
        _save_corrections(self.video_path, fingerprint, self._events,
                          self.angles, self.landmarks, self.leg,
                          _tracker_version())
    except Exception as exc:
        self.status_var.set(f"Save failed: {exc}")
        return
    self.status_var.set(
        f"Saved {len(self._events)} correction event(s) to "
        f"{_corrections_path(self.video_path, self.leg)}.")
```

`_load_corrections` needs no signature change — `tracker_version` just rides along inside the loaded `doc` dict like `leg` already does; no extra validation is required for it (it's informational, not used to accept/reject the sidecar).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k "tracker_version" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full existing suite to check nothing broke from the signature change**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: Several pre-existing tests that call `_build_corrections_doc`/`_save_corrections` directly with the old positional-arg count will now FAIL with `TypeError: missing 1 required positional argument: 'tracker_version'`. Fix each such call site in the test file by appending a `tracker_version` argument (e.g. `"test-tracker-v1"` as a literal string is fine for tests that don't specifically test provenance) — do this now, in this task, since Task 2 is what changed the signature.

- [ ] **Step 6: Run the full suite again to confirm it's green**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (all tests, pre-existing + new)

- [ ] **Step 7: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: bump corrections schema to v2, add tracker_version provenance field"
```

---

### Task 3: Unconditional, collision-safe sidecar backup before overwrite

**Files:**
- Modify: `video_review_dialog.py` (`_save_corrections`, ~line 125)
- Test: `tests/test_video_review_dialog.py`

**Interfaces:**
- Produces: `_backup_existing_sidecar(path: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

```python
def test_backup_existing_sidecar_no_file_returns_none(tmp_path):
    from video_review_dialog import _backup_existing_sidecar
    path = str(tmp_path / "nope.json")
    assert _backup_existing_sidecar(path) is None


def test_backup_existing_sidecar_renames_aside(tmp_path):
    from video_review_dialog import _backup_existing_sidecar
    path = str(tmp_path / "existing.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"original": true}')
    backup_path = _backup_existing_sidecar(path)
    assert backup_path is not None
    assert not os.path.isfile(path)  # moved, not copied
    with open(backup_path, encoding="utf-8") as f:
        assert f.read() == '{"original": true}'


def test_backup_existing_sidecar_handles_malformed_content(tmp_path):
    # Not gated on being parseable JSON -- a garbage file must still be
    # preserved, not silently destroyed.
    from video_review_dialog import _backup_existing_sidecar
    path = str(tmp_path / "garbage.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json at all")
    backup_path = _backup_existing_sidecar(path)
    assert backup_path is not None
    with open(backup_path, encoding="utf-8") as f:
        assert f.read() == "{not valid json at all"


def test_backup_existing_sidecar_collision_retry(tmp_path, monkeypatch):
    # Two backups requested with the SAME _now_iso() (simulating same-
    # wall-clock-second saves) must not collide -- the second gets a
    # ".2" suffix instead of overwriting the first backup.
    import video_review_dialog as vrd
    monkeypatch.setattr(vrd, "_now_iso", lambda: "2026-08-20T00-00-00Z")

    path = str(tmp_path / "x.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("first")
    b1 = vrd._backup_existing_sidecar(path)

    with open(path, "w", encoding="utf-8") as f:
        f.write("second")
    b2 = vrd._backup_existing_sidecar(path)

    assert b1 != b2
    with open(b1, encoding="utf-8") as f:
        assert f.read() == "first"
    with open(b2, encoding="utf-8") as f:
        assert f.read() == "second"


def test_save_corrections_backs_up_stale_sidecar_before_overwrite(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _corrections_path, _tracker_version)
    video_path = str(tmp_path / "stale.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    tv = _tracker_version()

    _save_corrections(video_path, fp, [], [1.0] * 3, [None] * 3, "left", tv)
    path = _corrections_path(video_path, "left")
    with open(path, encoding="utf-8") as f:
        first_save_content = f.read()

    _save_corrections(video_path, fp, [], [2.0] * 3, [None] * 3, "left", tv)

    backups = [p for p in os.listdir(str(tmp_path))
               if p.startswith(os.path.basename(path) + ".bak.")]
    assert len(backups) == 1
    with open(os.path.join(str(tmp_path), backups[0]), encoding="utf-8") as f:
        assert f.read() == first_save_content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k backup -v`
Expected: FAIL — `ImportError: cannot import name '_backup_existing_sidecar'`

- [ ] **Step 3: Implement**

Add the function right before `_save_corrections`:

```python
def _backup_existing_sidecar(path: str) -> str | None:
    """If a file already exists at path, renames it aside to a unique
    timestamp-suffixed backup name before the caller overwrites path, so no
    sidecar content -- valid, stale, or unparseable -- is ever silently
    destroyed. NOT gated on the existing file successfully parsing as JSON
    or matching the current schema -- an unparseable file is backed up the
    same as a valid one. Returns the backup path used, or None if no file
    existed at `path` to begin with.

    _now_iso() has only second-level precision, so two backups requested
    within the same wall-clock second would collide on the timestamp
    suffix alone -- an incrementing counter is appended when needed
    (`<path>.bak.<ts>`, then `<path>.bak.<ts>.2`, `.3`, ...) until an
    unused name is found."""
    if not os.path.isfile(path):
        return None
    base = f"{path}.bak.{_now_iso().replace(':', '-')}"
    candidate = base
    n = 2
    while os.path.exists(candidate):
        candidate = f"{base}.{n}"
        n += 1
    os.replace(path, candidate)
    return candidate
```

Call it inside `_save_corrections`, right before the final atomic swap:

```python
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        _backup_existing_sidecar(path)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k backup -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: back up existing sidecar before overwrite, collision-safe"
```

---

### Task 4: Pin state as events — `pin_set`/`pin_clear` and reconstruction

**Files:**
- Modify: `video_review_dialog.py` (add near `_apply_exclusion`, ~line 222)
- Test: `tests/test_video_review_dialog.py`

**Interfaces:**
- Produces: `_current_pins_from_events(events: list) -> dict[int, tuple[float, float]]`

- [ ] **Step 1: Write the failing tests**

```python
def test_current_pins_from_events_empty_events_no_pins():
    from video_review_dialog import _current_pins_from_events
    assert _current_pins_from_events([]) == {}


def test_current_pins_from_events_accumulates_pin_set():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 3.0, "y": 4.0, "at": "t2"},
    ]
    assert _current_pins_from_events(events) == {5: (1.0, 2.0), 10: (3.0, 4.0)}


def test_current_pins_from_events_later_pin_set_overwrites_earlier():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 5, "x": 9.0, "y": 9.0, "at": "t2"},
    ]
    assert _current_pins_from_events(events) == {5: (9.0, 9.0)}


def test_current_pins_from_events_specific_pin_clear_removes_one():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 3.0, "y": 4.0, "at": "t2"},
        {"type": "pin_clear", "frame": 5, "at": "t3"},
    ]
    assert _current_pins_from_events(events) == {10: (3.0, 4.0)}


def test_current_pins_from_events_null_frame_clears_all():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 3.0, "y": 4.0, "at": "t2"},
        {"type": "pin_clear", "frame": None, "at": "t3"},
        {"type": "pin_set", "frame": 20, "x": 5.0, "y": 6.0, "at": "t4"},
    ]
    assert _current_pins_from_events(events) == {20: (5.0, 6.0)}


def test_current_pins_from_events_ignores_other_event_types():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "retrack", "start_frame": 0, "at": "t2"},
        {"type": "exclude_range", "start_frame": 0, "end_frame": 3, "at": "t3"},
    ]
    assert _current_pins_from_events(events) == {5: (1.0, 2.0)}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k current_pins_from_events -v`
Expected: FAIL — `ImportError: cannot import name '_current_pins_from_events'`

- [ ] **Step 3: Implement**

```python
def _current_pins_from_events(events: list) -> dict:
    """Replays pin_set/pin_clear events in order to reconstruct the
    currently-active pin set: {frame: (x, y)}. pin_clear with frame=None
    empties the whole set (used only by "Clear All Pins"); with a specific
    frame removes just that one pin. Every other event type (retrack,
    exclude_range, pin_interpolate) is ignored -- this makes pin state
    reconstructable from the same persisted event log without a redundant
    top-level "current pins" field to keep in sync."""
    pins: dict = {}
    for ev in events:
        t = ev.get("type")
        if t == "pin_set":
            pins[ev["frame"]] = (ev["x"], ev["y"])
        elif t == "pin_clear":
            fi = ev.get("frame")
            if fi is None:
                pins = {}
            else:
                pins.pop(fi, None)
    return pins
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k current_pins_from_events -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: add pin_set/pin_clear event replay for reconstructable pin state"
```

---

### Task 5: Anchor derivation from a frame's landmarks

**Files:**
- Modify: `video_review_dialog.py` (add near `_current_pins_from_events`)
- Test: `tests/test_video_review_dialog.py`

**Interfaces:**
- Consumes: nothing new (plain `landmarks` list, same shape used everywhere else in this module)
- Produces: `_anchor_from_frame(landmarks: list, frame_idx: int) -> tuple | None` — returns `(hip, knee, shank_len)` or `None`

- [ ] **Step 1: Write the failing tests**

```python
def test_anchor_from_frame_valid_returns_hip_knee_shank_len():
    from video_review_dialog import _anchor_from_frame
    landmarks = [((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))]
    anchor = _anchor_from_frame(landmarks, 0)
    assert anchor is not None
    hip, knee, shank_len = anchor
    assert hip == (0.0, 1.0)
    assert knee == (0.0, 0.0)
    assert math.isclose(shank_len, 1.0, abs_tol=1e-9)


def test_anchor_from_frame_out_of_range_returns_none():
    from video_review_dialog import _anchor_from_frame
    assert _anchor_from_frame([], 0) is None
    assert _anchor_from_frame([((0, 1), (0, 0), (1, 0))], 5) is None
    assert _anchor_from_frame([((0, 1), (0, 0), (1, 0))], -1) is None


def test_anchor_from_frame_none_landmark_returns_none():
    from video_review_dialog import _anchor_from_frame
    assert _anchor_from_frame([None], 0) is None


def test_anchor_from_frame_missing_joint_returns_none():
    from video_review_dialog import _anchor_from_frame
    assert _anchor_from_frame([(None, (0.0, 0.0), (1.0, 0.0))], 0) is None
    assert _anchor_from_frame([((0.0, 1.0), None, (1.0, 0.0))], 0) is None
    assert _anchor_from_frame([((0.0, 1.0), (0.0, 0.0), None)], 0) is None


def test_anchor_from_frame_non_finite_coordinate_returns_none():
    from video_review_dialog import _anchor_from_frame
    landmarks = [((0.0, 1.0), (0.0, 0.0), (float("nan"), 0.0))]
    assert _anchor_from_frame(landmarks, 0) is None


def test_anchor_from_frame_degenerate_shank_len_returns_none():
    from video_review_dialog import _anchor_from_frame
    # knee and ankle at the same point -- zero-radius arc is undefined.
    landmarks = [((0.0, 1.0), (0.0, 0.0), (0.0, 0.0))]
    assert _anchor_from_frame(landmarks, 0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k anchor_from_frame -v`
Expected: FAIL — `ImportError: cannot import name '_anchor_from_frame'`

- [ ] **Step 3: Implement**

Add `import math` to the top of `video_review_dialog.py` (alongside the existing `import hashlib` etc.), then:

```python
def _anchor_from_frame(landmarks: list, frame_idx: int):
    """Returns (hip, knee, shank_len) from landmarks[frame_idx], or None if
    that frame is out of range, has no landmark, is missing hip/knee/ankle,
    has a non-finite coordinate, or the resulting shank_len is degenerate
    (~0, i.e. knee and ankle collapsed to the same point -- an arc with
    zero radius is undefined). Used to derive the ONE fixed anchor an
    "Interpolate Pins" run is computed around -- see
    docs/superpowers/specs/2026-08-20-pin-interpolation-correction-design.md
    §3.1."""
    if frame_idx < 0 or frame_idx >= len(landmarks):
        return None
    lm = landmarks[frame_idx]
    if lm is None:
        return None
    hip, knee, ankle = lm
    if hip is None or knee is None or ankle is None:
        return None
    for p in (hip, knee, ankle):
        if not (math.isfinite(p[0]) and math.isfinite(p[1])):
            return None
    shank_len = math.hypot(knee[0] - ankle[0], knee[1] - ankle[1])
    if shank_len < 1e-6:
        return None
    return hip, knee, shank_len
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k anchor_from_frame -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: add anchor-from-frame derivation for pin interpolation"
```

---

### Task 6: "Pin Ankle" button — click-to-place with coordinate conversion

**Files:**
- Modify: `video_review_dialog.py` (`_redraw` ~line 365, `_build_widgets` ~line 288, `__init__` ~line 231, new handler methods)
- Test: `tests/test_video_review_dialog.py`

**Interfaces:**
- Consumes: `_anchor_from_frame` (Task 5, used only for the knee-validity check here — full anchor validation happens in Task 8)
- Produces: `self._display_scale: float` (instance attribute, set every `_redraw()`), `self._pin_armed: bool`, `AnnotatedVideoReviewDialog._on_pin_ankle_toggle()`, `AnnotatedVideoReviewDialog._on_image_click(event)`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_display_scale_is_one_when_frame_narrower_than_max_width(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "narrow.avi")
    _write_test_video(video_path, 3, w=64, h=48)  # well under _MAX_DISPLAY_WIDTH
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    assert dlg._display_scale == 1.0
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_pin_ankle_toggle_arms_and_disarms(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "arm.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    assert dlg._pin_armed is False
    dlg._on_pin_ankle_toggle()
    assert dlg._pin_armed is True
    dlg._on_pin_ankle_toggle()
    assert dlg._pin_armed is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_pin_ankle_toggle_noop_during_retrack(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "arm2.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._retrack_in_progress = True
    dlg._on_pin_ankle_toggle()
    assert dlg._pin_armed is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_image_click_ignored_while_unarmed(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog

    class _FakeEvent:
        x, y = 10, 10

    video_path = str(tmp_path / "click0.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3,
        landmarks=[((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._on_image_click(_FakeEvent())
    from video_review_dialog import _current_pins_from_events
    assert _current_pins_from_events(dlg._events) == {}
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_image_click_places_pin_converted_by_display_scale(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events

    class _FakeEvent:
        x, y = 20, 30

    video_path = str(tmp_path / "click1.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3,
        landmarks=[((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._display_scale = 0.5  # simulate a downscaled display
    dlg._frame_idx = 1
    dlg._pin_armed = True

    dlg._on_image_click(_FakeEvent())

    pins = _current_pins_from_events(dlg._events)
    assert pins == {1: (40.0, 60.0)}  # 20/0.5, 30/0.5
    assert dlg._pin_armed is False  # auto-disarms after one placement
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_image_click_rejected_at_frame_with_no_valid_knee(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events

    class _FakeEvent:
        x, y = 10, 10

    video_path = str(tmp_path / "click2.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._pin_armed = True

    dlg._on_image_click(_FakeEvent())

    assert _current_pins_from_events(dlg._events) == {}
    assert "no valid tracked knee" in dlg.status_var.get().lower()
    dlg.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k "display_scale or pin_ankle_toggle or image_click" -v`
Expected: FAIL — `AttributeError` on `_display_scale`/`_pin_armed`, missing `_on_pin_ankle_toggle`/`_on_image_click`

- [ ] **Step 3: Implement**

In `__init__`, alongside the other state initialized before `_build_widgets()` (near `self._pending_exclude_start`):

```python
        self._pin_armed: bool = False
        self._display_scale: float = 1.0
```

In `_redraw`, capture the scale factor (replace the existing resize block):

```python
        h, w = overlay.shape[:2]
        if w > _MAX_DISPLAY_WIDTH:
            scale = _MAX_DISPLAY_WIDTH / w
            overlay = _cv2.resize(overlay, (int(w * scale), int(h * scale)))
        else:
            scale = 1.0
        self._display_scale = scale
```

In `_build_widgets`, bind the click handler and add the button (in the `button_row`, after "Fix Person Here"):

```python
        self._image_label.bind("<Button-1>", self._on_image_click)
        ...
        self._btn_pin = tk.Button(
            button_row, text="Pin Ankle", command=self._on_pin_ankle_toggle)
        self._btn_pin.pack(side="left", padx=8)
```

(Insert this button creation right after `self._btn_fix.pack(...)` and before the "Exclude From Here" button, matching the left-to-right ordering: Fix Person Here, Pin Ankle, Exclude From/To Here, Save.)

New methods, placed in the "Corrections" section near `_on_exclude_from_here`:

```python
    def _on_pin_ankle_toggle(self) -> None:
        if self._retrack_in_progress:
            return
        self._pin_armed = not self._pin_armed
        self._btn_pin.config(relief="sunken" if self._pin_armed else "raised")
        if self._pin_armed:
            self.status_var.set(
                "Pin Ankle armed -- click the video frame to place a pin.")
        else:
            self.status_var.set("Pin Ankle disarmed.")

    def _on_image_click(self, event) -> None:
        if not self._pin_armed:
            return
        x = event.x / self._display_scale
        y = event.y / self._display_scale
        self._place_pin(self._frame_idx, x, y)

    def _place_pin(self, frame_idx: int, x: float, y: float) -> None:
        self._pin_armed = False
        self._btn_pin.config(relief="raised")
        if frame_idx < 0 or frame_idx >= len(self.landmarks):
            self.status_var.set("Cannot pin here -- frame out of range.")
            return
        lm = self.landmarks[frame_idx]
        knee = lm[1] if lm is not None else None
        if knee is None or not (math.isfinite(knee[0])
                                and math.isfinite(knee[1])):
            self.status_var.set(
                "Cannot pin here -- no valid tracked knee at this frame.")
            return
        self._events.append({"type": "pin_set", "frame": frame_idx,
                             "x": float(x), "y": float(y), "at": _now_iso()})
        self._redraw()
        self.status_var.set(f"Pin placed at frame {frame_idx}.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k "display_scale or pin_ankle_toggle or image_click" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: add Pin Ankle button with click-to-place coordinate conversion"
```

---

### Task 7: "Clear Pin Here" and "Clear All Pins" buttons

**Files:**
- Modify: `video_review_dialog.py` (`_build_widgets`, new handlers)
- Test: `tests/test_video_review_dialog.py`

**Interfaces:**
- Consumes: `_current_pins_from_events` (Task 4)
- Produces: `AnnotatedVideoReviewDialog._on_clear_pin_here()`, `AnnotatedVideoReviewDialog._on_clear_all_pins()`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_clear_pin_here_removes_pin_at_current_frame(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events
    video_path = str(tmp_path / "clear0.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [{"type": "pin_set", "frame": 1, "x": 5.0, "y": 5.0, "at": "t1"}]
    dlg._frame_idx = 1

    dlg._on_clear_pin_here()

    assert _current_pins_from_events(dlg._events) == {}
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_clear_pin_here_noop_when_no_pin_at_frame(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "clear1.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg._on_clear_pin_here()

    assert "no pin" in dlg.status_var.get().lower()
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_clear_all_pins_empties_via_null_frame_event(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events
    video_path = str(tmp_path / "clear2.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [
        {"type": "pin_set", "frame": 0, "x": 1.0, "y": 1.0, "at": "t1"},
        {"type": "pin_set", "frame": 2, "x": 2.0, "y": 2.0, "at": "t2"},
    ]

    dlg._on_clear_all_pins()

    assert _current_pins_from_events(dlg._events) == {}
    assert dlg._events[-1] == {"type": "pin_clear", "frame": None,
                                "at": dlg._events[-1]["at"]}
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_clear_buttons_noop_during_retrack(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "clear3.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [{"type": "pin_set", "frame": 0, "x": 1.0, "y": 1.0, "at": "t1"}]
    dlg._retrack_in_progress = True

    dlg._on_clear_pin_here()
    dlg._on_clear_all_pins()

    assert len(dlg._events) == 1  # unchanged
    dlg.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k "clear_pin_here or clear_all_pins" -v`
Expected: FAIL — missing `_on_clear_pin_here`/`_on_clear_all_pins`

- [ ] **Step 3: Implement**

In `_build_widgets`, add two buttons after "Pin Ankle" and before "Exclude From Here":

```python
        self._btn_clear_pin = tk.Button(
            button_row, text="Clear Pin Here", command=self._on_clear_pin_here)
        self._btn_clear_pin.pack(side="left", padx=8)
        self._btn_clear_all_pins = tk.Button(
            button_row, text="Clear All Pins", command=self._on_clear_all_pins)
        self._btn_clear_all_pins.pack(side="left", padx=8)
```

New methods:

```python
    def _on_clear_pin_here(self) -> None:
        if self._retrack_in_progress:
            return
        pins = _current_pins_from_events(self._events)
        if self._frame_idx not in pins:
            self.status_var.set("No pin at this frame.")
            return
        self._events.append({"type": "pin_clear", "frame": self._frame_idx,
                             "at": _now_iso()})
        self._redraw()
        self.status_var.set(f"Cleared pin at frame {self._frame_idx}.")

    def _on_clear_all_pins(self) -> None:
        if self._retrack_in_progress:
            return
        pins = _current_pins_from_events(self._events)
        if not pins:
            self.status_var.set("No pins to clear.")
            return
        self._events.append({"type": "pin_clear", "frame": None,
                             "at": _now_iso()})
        self._redraw()
        self.status_var.set("Cleared all pins.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k "clear_pin_here or clear_all_pins" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: add Clear Pin Here and Clear All Pins buttons"
```

---

### Task 8: "Interpolate Pins" button — wires anchor derivation + `interpolate_ankle_arc` + audit event

**Files:**
- Modify: `video_review_dialog.py` (`_build_widgets`, new handler, new import)
- Test: `tests/test_video_review_dialog.py`

**Interfaces:**
- Consumes: `mp_pre.interpolate_ankle_arc` (Task 1), `_anchor_from_frame` (Task 5), `_current_pins_from_events` (Task 4), `mp_pre.knee_angle_from_points` (existing)
- Produces: `AnnotatedVideoReviewDialog._on_interpolate_pins()`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_interpolate_pins_requires_at_least_two_pins(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "interp0.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3,
        landmarks=[((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [{"type": "pin_set", "frame": 0, "x": 1.0, "y": 0.0, "at": "t1"}]

    dlg._on_interpolate_pins()

    assert "at least 2 pins" in dlg.status_var.get().lower()
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_interpolate_pins_rejected_when_anchor_frame_invalid(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "interp1.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None, None, None],
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [
        {"type": "pin_set", "frame": 0, "x": 1.0, "y": 0.0, "at": "t1"},
        {"type": "pin_set", "frame": 2, "x": 0.0, "y": 1.0, "at": "t2"},
    ]

    dlg._on_interpolate_pins()

    assert "invalid" in dlg.status_var.get().lower()
    # No pin_interpolate event appended, no angles/landmarks changed.
    assert not any(e["type"] == "pin_interpolate" for e in dlg._events)
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_interpolate_pins_updates_angles_and_landmarks_and_logs_event(tmp_path):
    import math
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "interp2.avi")
    _write_test_video(video_path, 11)
    r = _get_root()
    # Frame 0's own landmark supplies the anchor: hip (0,1), knee (0,0).
    # Pins at frame 0 -> ankle (1,0) and frame 10 -> ankle (0,1).
    landmarks = [((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 11
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 11, landmarks=landmarks,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [
        {"type": "pin_set", "frame": 0, "x": 1.0, "y": 0.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 0.0, "y": 1.0, "at": "t2"},
    ]

    dlg._on_interpolate_pins()

    # Midpoint frame 5 should sit at the quarter-circle midpoint, angle
    # recomputed from the FIXED anchor hip/knee (0,1)/(0,0), not frame 5's
    # original landmark.
    hip5, knee5, ankle5 = dlg.landmarks[5]
    assert hip5 == (0.0, 1.0)
    assert knee5 == (0.0, 0.0)
    assert math.isclose(ankle5[0], math.cos(math.pi / 4), abs_tol=1e-6)
    assert math.isclose(ankle5[1], math.sin(math.pi / 4), abs_tol=1e-6)
    assert not math.isnan(dlg.angles[5])

    interp_events = [e for e in dlg._events if e["type"] == "pin_interpolate"]
    assert len(interp_events) == 1
    ev = interp_events[0]
    assert ev["anchor_frame"] == 0
    assert ev["anchor_hip"] == [0.0, 1.0]
    assert ev["anchor_knee"] == [0.0, 0.0]
    assert math.isclose(ev["anchor_shank_len"], 1.0, abs_tol=1e-9)
    assert ev["frame_range"] == [0, 10]
    assert {p["frame"] for p in ev["pins"]} == {0, 10}
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_interpolate_pins_overwrites_previously_excluded_frames(tmp_path):
    import math
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "interp3.avi")
    _write_test_video(video_path, 11)
    r = _get_root()
    landmarks = [((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 11
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 11, landmarks=landmarks,
        fps=30.0, leg="right", engine=_FakeEngine())
    # Simulate frames 3-7 having been previously excluded (NaN/None).
    for i in range(3, 8):
        dlg.angles[i] = float("nan")
        dlg.landmarks[i] = None
    dlg._events = [
        {"type": "pin_set", "frame": 0, "x": 1.0, "y": 0.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 0.0, "y": 1.0, "at": "t2"},
    ]

    dlg._on_interpolate_pins()

    # Frame 5 (inside the previously-excluded range) must now be populated
    # from the anchor, not left as None/NaN -- interpolation never read
    # frame 5's own (destroyed) landmark data.
    assert dlg.landmarks[5] is not None
    assert not math.isnan(dlg.angles[5])
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_interpolate_pins_noop_during_retrack(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "interp4.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3,
        landmarks=[((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [
        {"type": "pin_set", "frame": 0, "x": 1.0, "y": 0.0, "at": "t1"},
        {"type": "pin_set", "frame": 2, "x": 0.0, "y": 1.0, "at": "t2"},
    ]
    dlg._retrack_in_progress = True

    dlg._on_interpolate_pins()

    assert not any(e["type"] == "pin_interpolate" for e in dlg._events)
    dlg.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k interpolate_pins -v`
Expected: FAIL — missing `_on_interpolate_pins`

- [ ] **Step 3: Implement**

Add `import mediapipe_preprocessing as mp_pre` to the top imports of `video_review_dialog.py` (alongside `from pendulastic_viewer import ...`).

Add the button in `_build_widgets`, after "Clear All Pins":

```python
        self._btn_interpolate = tk.Button(
            button_row, text="Interpolate Pins",
            command=self._on_interpolate_pins)
        self._btn_interpolate.pack(side="left", padx=8)
```

New method:

```python
    def _on_interpolate_pins(self) -> None:
        if self._retrack_in_progress:
            return
        pins = _current_pins_from_events(self._events)
        if len(pins) < 2:
            self.status_var.set("Need at least 2 pins to interpolate.")
            return
        pins_sorted = sorted(pins.items())
        first_frame = pins_sorted[0][0]
        anchor = _anchor_from_frame(self.landmarks, first_frame)
        if anchor is None:
            self.status_var.set(
                "Cannot interpolate -- invalid hip/knee/ankle at the first "
                "pin's frame.")
            return
        anchor_hip, anchor_knee, anchor_shank_len = anchor

        result = mp_pre.interpolate_ankle_arc(
            pins_sorted, anchor_knee, anchor_shank_len)
        frames = sorted(result.keys())
        for fi in frames:
            ankle = result[fi]
            ang = mp_pre.knee_angle_from_points(anchor_hip, anchor_knee, ankle)
            self.angles[fi] = ang
            self.landmarks[fi] = (anchor_hip, anchor_knee, ankle)

        self._events.append({
            "type": "pin_interpolate",
            "pins": [{"frame": fi, "x": xy[0], "y": xy[1]}
                    for fi, xy in pins_sorted],
            "anchor_frame": first_frame,
            "anchor_hip": [anchor_hip[0], anchor_hip[1]],
            "anchor_knee": [anchor_knee[0], anchor_knee[1]],
            "anchor_shank_len": anchor_shank_len,
            "frame_range": [frames[0], frames[-1]],
            "at": _now_iso(),
        })
        self._redraw()
        self.status_var.set(
            f"Interpolated frames {frames[0]}-{frames[-1]} from "
            f"{len(pins)} pins.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k interpolate_pins -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: add Interpolate Pins button wiring anchor + arc interpolation"
```

---

### Task 9: Pin marker overlay + retrack-vs-pin auto-clear

**Files:**
- Modify: `video_review_dialog.py` (`_redraw`, `_on_retrack_done`)
- Test: `tests/test_video_review_dialog.py`

**Interfaces:**
- Consumes: `_current_pins_from_events` (Task 4)
- Produces: no new public interface — modifies existing `_redraw`/`_on_retrack_done` behavior

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_redraw_does_not_raise_with_a_pin_at_current_frame(tmp_path):
    # Pixel-level overlay assertions are brittle; this asserts the pin-
    # marker draw pass runs without error when the current frame has a
    # pin, which is the meaningful regression to guard against.
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "marker0.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [{"type": "pin_set", "frame": 0, "x": 5.0, "y": 5.0, "at": "t1"}]
    dlg._frame_idx = 0

    dlg._redraw()  # must not raise

    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_retrack_clears_pins_in_full_spliced_range_including_padded_tail(
        tmp_path, monkeypatch):
    # _splice_from pads through the END of self.angles regardless of how
    # short new_angles is -- a pin at frame 5, well beyond a 2-frame
    # retrack result starting at frame 2 in a 10-frame trial, must still
    # be cleared, since _splice_from's NaN/None padding overwrites it too.
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events
    video_path = str(tmp_path / "retrackpin.avi")
    _write_test_video(video_path, 10)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 10, landmarks=[None] * 10,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [
        {"type": "pin_set", "frame": 1, "x": 1.0, "y": 1.0, "at": "t1"},  # before start_frame
        {"type": "pin_set", "frame": 5, "x": 5.0, "y": 5.0, "at": "t2"},  # in padded tail
    ]

    dlg._on_retrack_done(2, [170.0, 170.0], [None, None])  # short: only 2 frames

    pins = _current_pins_from_events(dlg._events)
    assert pins == {1: (1.0, 1.0)}  # untouched -- before start_frame
    clear_events = [e for e in dlg._events
                    if e["type"] == "pin_clear" and e.get("frame") == 5]
    assert len(clear_events) == 1
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_retrack_with_no_overlapping_pins_logs_no_pin_clear_events(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "retrackpin2.avi")
    _write_test_video(video_path, 10)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 10, landmarks=[None] * 10,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._events = [{"type": "pin_set", "frame": 1, "x": 1.0, "y": 1.0, "at": "t1"}]

    dlg._on_retrack_done(5, [170.0] * 5, [None] * 5)

    assert not any(e["type"] == "pin_clear" for e in dlg._events)
    dlg.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k "marker or retrack_clears_pins or no_overlapping_pins" -v`
Expected: FAIL (or a false pass on the "no overlap" case combined with a real failure on the padded-tail case — verify by reading output, both are new assertions against unmodified `_on_retrack_done`)

- [ ] **Step 3: Implement**

In `_redraw`, add the pin-marker pass after the resize/scale block and before the `cv2.cvtColor`/`PhotoImage` conversion:

```python
        pins = _current_pins_from_events(self._events)
        if self._frame_idx in pins:
            px, py = pins[self._frame_idx]
            dx = int(px * self._display_scale)
            dy = int(py * self._display_scale)
            _cv2.circle(overlay, (dx, dy), 6, (0, 255, 255), 2)
```

In `_on_retrack_done`, after the existing `self._events.append({"type": "retrack", ...})` line, add the pin auto-clear:

```python
        pins = _current_pins_from_events(self._events)
        for fi in sorted(pins):
            if fi >= start_frame:
                self._events.append({"type": "pin_clear", "frame": fi,
                                     "at": _now_iso()})
```

(This must run after `self.angles`/`self.landmarks` have been spliced, since `_splice_from` always fills through `len(self.angles)` — every pin at or beyond `start_frame` falls in that overwritten range, matching the spec's `[start_frame, len(self.angles))` requirement exactly, without needing to separately compute that upper bound.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -k "marker or retrack_clears_pins or no_overlapping_pins" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite one final time**

Run: `.venv\Scripts\python.exe -m pytest tests/test_video_review_dialog.py -v`
Expected: PASS (all — this is the full feature, fully wired)

Also sanity-check the app still imports cleanly:

Run: `.venv\Scripts\python.exe -c "import pendulastic_app"`
Expected: exits 0, only MediaPipe/TensorFlow telemetry noise on stderr

- [ ] **Step 6: Commit**

```bash
git add video_review_dialog.py tests/test_video_review_dialog.py
git commit -m "feat: draw pin markers on overlay, auto-clear pins overwritten by retrack"
```

---

## Self-Review Notes

**Spec coverage:** §3.1 (Task 1, 5), §3.2 pin buttons (Tasks 6, 7, 8), §3.2 marker/exclusion/retrack interaction (Tasks 8, 9), §3.3 schema v2 + backup (Tasks 2, 3), §4 error handling (validity checks folded into Tasks 6/8; retrack-in-progress guards folded into every button's task; save/load unchanged-mechanics folded into Task 2/3), §5 testing (each task's own tests plus the full-suite re-runs) — all covered.

**Type consistency:** `interpolate_ankle_arc(pins_sorted, anchor_knee, anchor_shank_len)` (Task 1) is called identically in Task 8's `_on_interpolate_pins`. `_anchor_from_frame` returns `(hip, knee, shank_len)` (Task 5) and Task 8 unpacks it in that exact order. `_current_pins_from_events` (Task 4) is consumed with the same `{frame: (x, y)}` shape by Tasks 6/7/8/9. `_build_corrections_doc`/`_save_corrections` both gain the same new trailing `tracker_version` parameter (Task 2) — no call site left on the old arity, confirmed by the full-suite re-run built into Task 2 Step 5-6.
