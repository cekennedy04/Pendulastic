"""video_review_dialog.py
=========================
In-app annotated video review for pendulastic_app.py's PostProcessingPanel.
See docs/superpowers/specs/2026-08-12-annotated-video-review-design.md for
the full design.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import threading
import time


def _splice_from(old: list, start_idx: int, new: list, pad_value) -> list:
    """Return old[:start_idx] + new, with new padded (using pad_value) or
    truncated so the result is always exactly len(old) items long. Never
    mutates old or new. This guards against a retrack returning a short or
    long suffix silently desyncing frame-index-to-array-index alignment --
    see design spec S4 point 1."""
    target_len = max(0, len(old) - start_idx)
    adjusted = list(new[:target_len])
    if len(adjusted) < target_len:
        adjusted.extend([pad_value] * (target_len - len(adjusted)))
    return list(old[:start_idx]) + adjusted


import tkinter as tk
from tkinter import ttk

import cv2 as _cv2
from PIL import Image, ImageTk

from pendulastic_viewer import _draw, TRAIL_LEN, resolve_person_click, _MP_MODEL

_MAX_DISPLAY_WIDTH = 960

CORRECTIONS_SCHEMA_VERSION = 2


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tracker_version() -> str:
    """Minimum-viable provenance string for a corrections doc -- identifies
    the tracking engine/config that produced corrected_angles/
    corrected_landmarks. Not a full implementation-fingerprint hash (see
    rmse_pipeline_common.py for that heavier mechanism, out of scope here)
    -- a plain identifying string, honestly labeled as such."""
    return f"_MPBatchTracker;model={os.path.basename(_MP_MODEL)}"


def _video_fingerprint(video_path: str) -> dict:
    """Identity fingerprint for a video file: a full-file SHA-256 content
    hash, plus size, mtime, and MediaPipe-visible frame count. The hash is
    the actual identity check -- size/mtime/frame_count alone can
    coincidentally match a replacement video (same dimensions/duration,
    re-saved at the same path), which would let that video silently inherit
    another trial's corrections. Trial videos in this repo are ~15-20MB, so
    hashing the whole file is fast (well under a second); reading in 1MB
    chunks avoids loading the whole file into memory at once."""
    size = os.path.getsize(video_path)
    mtime = os.path.getmtime(video_path)
    cap = _cv2.VideoCapture(video_path)
    try:
        frame_count = max(1, int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)))
    finally:
        cap.release()
    hasher = hashlib.sha256()
    with open(video_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return {"size": size, "mtime": mtime, "frame_count": frame_count,
            "sha256": hasher.hexdigest()}


def _corrections_path(video_path: str, leg: str) -> str:
    """Per-leg sidecar path. The dialog tracks one leg per session, and a
    clinician reviewing both legs of the same video is a routine workflow --
    a single shared path would let saving the second leg's corrections
    silently overwrite and destroy the first leg's already-saved
    corrections. Keying the file itself by leg (not just a `leg` field
    inside a shared doc) makes that collision structurally impossible."""
    return f"{video_path}.corrections.{leg}.json"


def _other_leg(leg: str):
    """Returns the opposite leg for the two known values, or None for
    anything else (defensive -- never guess for an unrecognized leg
    string)."""
    if leg == "left":
        return "right"
    if leg == "right":
        return "left"
    return None


def _landmark_to_json(lm):
    if lm is None:
        return None
    hip, knee, ankle = lm
    def _pt(p):
        return None if p is None else [p[0], p[1]]
    return [_pt(hip), _pt(knee), _pt(ankle)]


def _landmark_from_json(obj):
    if obj is None:
        return None
    hip, knee, ankle = obj
    def _pt(p):
        if p is None:
            return None
        x, y = p
        return (float(x), float(y))  # raises on a non-numeric coordinate
    return (_pt(hip), _pt(knee), _pt(ankle))


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


def _save_corrections(video_path: str, fingerprint: dict, events: list,
                      angles: list, landmarks: list, leg: str,
                      tracker_version: str) -> None:
    """Writes the corrections sidecar. IO/serialization errors propagate to
    the caller -- this layer does not swallow them; only the dialog's button
    handler (the UI layer) turns a failure into a status message.

    Writes to a temp path and atomically os.replace()s it into place
    (matching sweep_mediapipe_preprocessing.py's _save_cache pattern) so a
    crash or failure mid-write can never leave a truncated/corrupt sidecar
    in place of a previously-valid one -- the old file stays intact until
    the new one is fully written and the swap is atomic.

    Uses a uniquely-named temp file (tempfile.mkstemp, same directory as the
    destination so os.replace stays on one filesystem) rather than a fixed
    "<path>.tmp" name -- two writers saving the same video/leg at once (two
    app instances pointed at the same file, observed as a real scenario in
    this repo) would otherwise collide on that shared temp name. The temp
    file is removed on any failure before the swap, so a failed save never
    leaves stray partial files behind."""
    doc = _build_corrections_doc(fingerprint, events, angles, landmarks, leg,
                                 tracker_version)
    path = _corrections_path(video_path, leg)
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)) or ".",
        prefix=os.path.basename(path) + ".", suffix=".tmp")
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


def _load_corrections(video_path: str, leg: str):
    """Reads and validates a corrections sidecar for video_path/leg. Returns
    the parsed doc only when ALL of the following hold, otherwise returns
    None: it exists at this leg's own path and parses as JSON; its
    schema_version matches; its stored `leg` field also matches (defense in
    depth against a sidecar file being manually copied/renamed across legs
    -- the path is the primary separation, this is a second check); its
    stored video_fingerprint (including content hash) matches the video's
    CURRENT fingerprint; corrected_angles/corrected_landmarks are both
    lists of equal length AND that length matches the video's actual
    frame_count (not just each other -- two equal-but-wrong-length arrays
    must not pass); every angle is float-convertible; and every landmark
    coordinate is float-convertible. Never raises: a missing, malformed,
    wrong-leg, stale, or structurally-corrupt sidecar must be treated as
    "no saved corrections", not crash the dialog."""
    path = _corrections_path(video_path, leg)
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        if doc.get("schema_version") != CORRECTIONS_SCHEMA_VERSION:
            return None
        if doc.get("leg") != leg:
            return None
        fingerprint = doc.get("video_fingerprint")
        if fingerprint != _video_fingerprint(video_path):
            return None
        corrected_angles = doc.get("corrected_angles")
        corrected_landmarks = doc.get("corrected_landmarks")
        frame_count = fingerprint.get("frame_count")
        if (not isinstance(corrected_angles, list)
                or not isinstance(corrected_landmarks, list)
                or len(corrected_angles) != len(corrected_landmarks)
                or len(corrected_angles) != frame_count):
            return None
        for a in corrected_angles:
            float(a)  # raises on a non-numeric angle
        for lm in corrected_landmarks:
            _landmark_from_json(lm)  # raises on a malformed/non-numeric entry
        return doc
    except Exception:
        return None


def _apply_exclusion(angles: list, landmarks: list, frame_idx_a: int,
                     frame_idx_b: int):
    """Returns (new_angles, new_landmarks, event) with frames
    [min(a,b), max(a,b)] (inclusive, clamped to the array bounds) set to
    NaN/None -- marking that span unscoreable. Works regardless of scrub
    direction (a > b is fine, matching a clinician scrubbing backwards to
    set the exclusion end). Never mutates the input lists, matching
    _splice_from's existing "never mutates old/new" convention."""
    n = len(angles)
    start = max(0, min(frame_idx_a, frame_idx_b))
    end = min(n - 1, max(frame_idx_a, frame_idx_b))
    new_angles = list(angles)
    new_landmarks = list(landmarks)
    for i in range(start, end + 1):
        new_angles[i] = float("nan")
        new_landmarks[i] = None
    event = {"type": "exclude_range", "start_frame": start,
             "end_frame": end, "at": _now_iso()}
    return new_angles, new_landmarks, event


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

        self._pending_exclude_start: int | None = None
        self._pin_armed: bool = False
        self._display_scale: float = 1.0
        self._events: list = []
        self._pending_status: str | None = None
        own_sidecar_exists = os.path.isfile(_corrections_path(video_path, leg))
        loaded = _load_corrections(video_path, leg)
        if loaded is not None:
            self.angles = [float(a) for a in loaded.get("corrected_angles", [])]
            self.landmarks = [_landmark_from_json(lm)
                              for lm in loaded.get("corrected_landmarks", [])]
            self._events = list(loaded.get("events", []))
            self._pending_status = (
                f"Loaded {len(self._events)} saved correction event(s).")
        elif own_sidecar_exists:
            # This leg has its own sidecar, but it failed validation (stale
            # video, wrong schema, or structurally corrupt) -- not a
            # cross-leg issue, since each leg now has its own path.
            self._pending_status = (
                "Saved corrections found but video has changed since -- "
                "ignoring.")
        else:
            other_leg = _other_leg(leg)
            if other_leg is not None and os.path.isfile(
                    _corrections_path(video_path, other_leg)):
                self._pending_status = (
                    "Saved corrections exist for the other leg on this "
                    "video -- not applied here.")

        self._cap = _cv2.VideoCapture(video_path)
        self.total_frames = max(
            1, int(self._cap.get(_cv2.CAP_PROP_FRAME_COUNT)))
        self._frame_cache: dict = {}
        self._frame_idx = 0
        self._playing = False
        self._retrack_in_progress = False

        self._build_widgets()
        if self._pending_status:
            self.status_var.set(self._pending_status)
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
        self._image_label.bind("<Button-1>", self._on_image_click)

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
        self._btn_pin = tk.Button(
            button_row, text="Pin Ankle", command=self._on_pin_ankle_toggle)
        self._btn_pin.pack(side="left", padx=8)
        self._btn_exclude_from = tk.Button(
            button_row, text="Exclude From Here",
            command=self._on_exclude_from_here)
        self._btn_exclude_from.pack(side="left", padx=8)
        self._btn_exclude_to = tk.Button(
            button_row, text="Exclude To Here",
            command=self._on_exclude_to_here)
        self._btn_exclude_to.pack(side="left", padx=8)
        self._btn_save = tk.Button(
            button_row, text="Save Corrections",
            command=self._on_save_corrections)
        self._btn_save.pack(side="left", padx=8)
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
        else:
            scale = 1.0
        self._display_scale = scale
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
        if self._retrack_in_progress:
            return
        self._playing = False
        self._btn_play.config(text="▶")

        frame_idx = self._frame_idx
        if frame_idx >= len(self.angles):
            self.status_var.set(
                "Cannot fix a frame beyond the tracked range -- try an "
                "earlier frame.")
            return
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
            # PersonPickerDialog's own grab_set() (in its __init__) steals
            # the modal grab from this dialog, and Tk does not restore the
            # previous grab when the picker is destroyed -- re-acquire it
            # here regardless of whether the user confirmed or cancelled,
            # or the panel underneath becomes clickable while this dialog
            # is still open (see design spec / Finding 3).
            self.grab_set()
            if dialog.result is None:
                return
            seed = dialog.result

        self._start_retrack(frame_idx, seed)

    def _start_retrack(self, start_frame: int, seed: tuple) -> None:
        self._retrack_in_progress = True
        self._btn_fix.config(state="disabled")
        self.status_var.set(f"Retracking from frame {start_frame}...")

        def _run():
            try:
                new_angles, new_landmarks, _fps = self.engine.run_offline_track(
                    self.video_path, lambda p: None, leg=self.leg,
                    collect_landmarks=True, manual_seed=seed,
                    start_frame=start_frame)
            except Exception as exc:
                self.after(0, lambda exc=exc: self._on_retrack_failed(exc))
                return
            self.after(0, lambda: self._on_retrack_done(
                start_frame, new_angles, new_landmarks))

        threading.Thread(target=_run, daemon=True).start()

    def _on_retrack_done(self, start_frame: int, new_angles: list,
                          new_landmarks: list) -> None:
        self.angles = _splice_from(self.angles, start_frame, new_angles,
                                    float("nan"))
        self.landmarks = _splice_from(self.landmarks, start_frame,
                                       new_landmarks, None)
        self._events.append({"type": "retrack", "start_frame": start_frame,
                             "at": _now_iso()})
        self._retrack_in_progress = False
        self._btn_fix.config(state="normal")
        self.status_var.set(f"Retrack complete from frame {start_frame}.")
        self._redraw()

    def _on_retrack_failed(self, exc: Exception) -> None:
        self._retrack_in_progress = False
        self._btn_fix.config(state="normal")
        self.status_var.set(f"Retrack failed: {exc}")

    # ------------------------------------------------------------------
    # Corrections: frame-range exclusion + persistence
    # ------------------------------------------------------------------
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

    def _on_exclude_from_here(self) -> None:
        if self._retrack_in_progress:
            return
        self._pending_exclude_start = self._frame_idx
        self.status_var.set(
            f"Exclusion start set at frame {self._frame_idx} -- scrub to "
            "the end and click 'Exclude To Here'.")

    def _on_exclude_to_here(self) -> None:
        if self._retrack_in_progress:
            return
        if self._pending_exclude_start is None:
            self.status_var.set(
                "No exclusion start set -- click 'Exclude From Here' first.")
            return
        self.angles, self.landmarks, event = _apply_exclusion(
            self.angles, self.landmarks, self._pending_exclude_start,
            self._frame_idx)
        self._events.append(event)
        self._pending_exclude_start = None
        self._redraw()
        self.status_var.set(
            f"Excluded frames {event['start_frame']}-{event['end_frame']}.")

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
