"""
pendulastic_workbench.py
=========================
Pendulastic Workbench: an interactive multi-modal (phone IMU / MediaPipe-
family HPE video / OptiTrack) trial comparison tool. Follows
pendulastic_app.py's plain-Tkinter panel-swap architecture.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
from __future__ import annotations

import os
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import cv2
from PIL import Image, ImageTk

import analysis_pipeline
import workbench_engine as engine

MILESTONE_LABELS = ["Release Start", "First Peak Extension",
                    "Maximum Flexion", "Rest/Settled"]


class TrialLoadPanel(tk.Frame):
    """3 independent file pickers (IMU raw log / video / OptiTrack CSV),
    HPE model checkboxes, and optional femur/tibia length fields for
    Ockendon-ratio personalization (design spec Section 3a).

    controller: App instance -- receives on_load_trial(selection: dict)."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._imu_path = tk.StringVar(value="")
        self._video_path = tk.StringVar(value="")
        self._optitrack_path = tk.StringVar(value="")
        self._femur_cm = tk.StringVar(value="")
        self._tibia_cm = tk.StringVar(value="")
        self._model_vars = {name: tk.BooleanVar(value=False)
                            for name in analysis_pipeline.MODEL_FUNCTIONS}
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        tk.Label(self, text="Pendulastic Workbench", font=("Segoe UI", 14, "bold")
                ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        self._file_row(1, "Phone IMU raw log (.jsonl)", self._imu_path,
                       [("JSONL", "*.jsonl"), ("All files", "*.*")])
        self._file_row(2, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")])
        self._file_row(3, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")])

        tk.Label(self, text="HPE models to run:").grid(
            row=4, column=0, sticky="nw", **pad)
        model_frame = tk.Frame(self)
        model_frame.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name]
                          ).grid(row=i // 3, column=i % 3, sticky="w", padx=4)

        tk.Label(self, text="Femur length (cm, optional):").grid(
            row=5, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._femur_cm, width=10).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(self, text="Tibia length (cm, optional):").grid(
            row=6, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._tibia_cm, width=10).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=7, column=0, columnspan=3, pady=16)

    def _file_row(self, row: int, label: str, var: tk.StringVar, filetypes) -> None:
        tk.Label(self, text=label).grid(row=row, column=0, sticky="w", padx=12, pady=6)
        tk.Entry(self, textvariable=var, width=48, state="readonly").grid(
            row=row, column=1, sticky="we", padx=4)
        tk.Button(self, text="Browse...",
                 command=lambda: self._browse(var, filetypes)).grid(
            row=row, column=2, sticky="w", padx=4)

    def _browse(self, var: tk.StringVar, filetypes) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)

    def get_selection(self) -> dict:
        """Snapshot of the current form state. Numeric fields left blank
        parse to None (Section 3a: leaving them blank keeps the default
        1.2 femur:tibia ratio unchanged)."""
        def _parse_float(s: str) -> Optional[float]:
            s = s.strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None

        return {
            "imu_path": self._imu_path.get() or None,
            "video_path": self._video_path.get() or None,
            "optitrack_path": self._optitrack_path.get() or None,
            "models": [name for name, var in self._model_vars.items() if var.get()],
            "femur_length_cm": _parse_float(self._femur_cm.get()),
            "tibia_length_cm": _parse_float(self._tibia_cm.get()),
        }

    def _on_load_clicked(self) -> None:
        selection = self.get_selection()
        if not any([selection["imu_path"], selection["video_path"],
                   selection["optitrack_path"]]):
            messagebox.showerror("No trial data",
                                 "Select at least one of: IMU log, video, OptiTrack CSV.")
            return
        self.controller.on_load_trial(selection)


class WorkbenchView(tk.Frame):
    """Main workbench panel: synced video scrubber + multi-trace plot +
    annotation toolbar + metrics readout (built up across Tasks 11-14).

    controller: App instance."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps: float = 30.0
        self._n_frames: int = 0
        self._photo = None   # keep a reference so Tk doesn't garbage-collect it
        self._scrub_var = tk.DoubleVar(value=0.0)
        self._build_widgets()

    def _build_widgets(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned)
        paned.add(left, weight=1)

        self._video_label = tk.Label(left, bg="black")
        self._video_label.pack(fill="both", expand=True)

        self._scrubber = ttk.Scale(left, from_=0, to=0, orient="horizontal",
                                   variable=self._scrub_var,
                                   command=self._on_scrub)
        self._scrubber.pack(fill="x", padx=8, pady=4)

        self._right = tk.Frame(paned)
        paned.add(self._right, weight=1)

    def load_video(self, path: str) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = cv2.VideoCapture(path)
        self._fps = self._cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._n_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        self._scrubber.configure(to=max(0, self._n_frames - 1))
        self._scrub_var.set(0)
        self.seek_to_frame(0)

    def seek_to_frame(self, fi: int) -> None:
        if self._cap is None:
            return
        fi = max(0, min(fi, self._n_frames - 1))
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ret, frame = self._cap.read()
        if not ret or frame is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(img)
        self._video_label.configure(image=self._photo)

    def _on_scrub(self, value_str: str) -> None:
        fi = int(round(float(value_str)))
        self.seek_to_frame(fi)

    def current_frame_index(self) -> int:
        """Reads the scrubber's bound Tkinter variable directly -- never
        infers the frame from canvas paint state (design spec Section 6's
        stale-frame binding requirement)."""
        return int(round(self._scrub_var.get()))

    def current_time_sec(self) -> float:
        return self.current_frame_index() / self._fps if self._fps > 0 else 0.0
