"""video_review_dialog.py
=========================
In-app annotated video review for pendulastic_app.py's PostProcessingPanel.
See docs/superpowers/specs/2026-08-12-annotated-video-review-design.md for
the full design.
"""
from __future__ import annotations

import threading


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

from pendulastic_viewer import _draw, TRAIL_LEN, resolve_person_click

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
        self._retrack_in_progress = False
        self._btn_fix.config(state="normal")
        self.status_var.set(f"Retrack complete from frame {start_frame}.")
        self._redraw()

    def _on_retrack_failed(self, exc: Exception) -> None:
        self._retrack_in_progress = False
        self._btn_fix.config(state="normal")
        self.status_var.set(f"Retrack failed: {exc}")
