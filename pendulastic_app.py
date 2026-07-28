"""
pendulastic_app.py  —  Unified Pendulastic Desktop App
=======================================================
Single-window Tkinter app combining acquisition and post-processing.

Run:
    .venv\\Scripts\\python.exe pendulastic_app.py
"""
from __future__ import annotations

import csv
import os
import queue
import threading
import time
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Guarded imports — failures must not crash the app at startup
# ---------------------------------------------------------------------------
try:
    import pendulastic_imu_server as _imu
    _IMU_AVAIL = True
except Exception:
    _imu = None
    _IMU_AVAIL = False

try:
    import motive_sync as _motive
    _MOTIVE_AVAIL = True
except Exception:
    _motive = None
    _MOTIVE_AVAIL = False

try:
    import cv2 as _cv2
    _CV2_AVAIL = True
except ImportError:
    _cv2 = None
    _CV2_AVAIL = False

try:
    from pendulastic_viewer import _MPBatchTracker, _PatientDetector
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _VIEWER_AVAIL = False

try:
    from pendulastic_pt_score import (
        compute_pt_params, compute_pt_score_simple, pt_to_mas,
        HEALTHY_REF, load_optitrack,
    )
    _PT_AVAIL = True
except Exception:
    compute_pt_params = compute_pt_score_simple = pt_to_mas = None
    HEALTHY_REF = load_optitrack = None
    _PT_AVAIL = False

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    _MPL_AVAIL = True
except Exception:
    FigureCanvasTkAgg = Figure = None
    _MPL_AVAIL = False

_GREEN = "#1e7d34"
_RED   = "#a31515"
_BLUE  = "#1f3a93"
_AMBER = "#c07000"

# ---------------------------------------------------------------------------
# DataManager
# ---------------------------------------------------------------------------

class DataManager:
    DATA_DIR = os.path.join(BASE_DIR, "data")

    @staticmethod
    def build_filename(pid: str, leg: str, ms_status: str, trial: int) -> str:
        leg_s = leg.capitalize()
        ms_s  = ms_status.replace(" ", "_")
        return f"PID_{pid}_LEG_{leg_s}_{ms_s}_TRIAL_{trial}.csv"

    @classmethod
    def save_trial(
        cls,
        filename: str,
        angles: list,
        metadata: dict,
        timestamps: list | None = None,
        fps: float = 30.0,
    ) -> str:
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        path = os.path.join(cls.DATA_DIR, filename)
        t0 = timestamps[0] if timestamps else 0.0
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["frame", "time_s", "knee_angle_deg",
                        "pid", "leg", "ms_status", "trial", "methodology"])
            for i, a in enumerate(angles):
                t = (timestamps[i] - t0) if timestamps else i / fps
                w.writerow([i, f"{t:.4f}", f"{a:.3f}",
                            metadata["pid"], metadata["leg"],
                            metadata["ms_status"], metadata["trial"],
                            metadata["methodology"]])
        return path


# ---------------------------------------------------------------------------
# BiomechanicalEngine
# ---------------------------------------------------------------------------

class BiomechanicalEngine:
    """Angle pipeline — three code paths dispatched by methodology string."""

    def __init__(self, methodology: str) -> None:
        self.methodology = methodology  # "imu" | "rgb" | "optitrack"

    def get_live_angle(self) -> float:
        """Return current knee angle (degrees) or NaN if unavailable."""
        if self.methodology != "imu" or not _IMU_AVAIL:
            return float("nan")
        try:
            return float(_imu.get_state()["distal"]["pitch"])
        except Exception:
            return float("nan")

    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
    ) -> list:
        """
        Offline MediaPipe tracking on a recorded video.
        Called on a background thread immediately after STOP (RGB methodology).

        Tracker API (from pendulastic_viewer.py):
          _PatientDetector().detect(frame) -> (patient_kps: ndarray(17,2) | None, _)
          _MPBatchTracker(side, fps).init(frame, hip, knee, ankle)
          tracker.step(frame) -> (hip, knee, ankle, angle_deg)

        COCO indices used: 11=L-hip, 12=R-hip, 13=L-knee, 14=R-knee,
                           15=L-ankle, 16=R-ankle
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return []

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps_v  = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        total  = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 1

        # COCO column offsets: right leg offset=1, left leg offset=0
        col    = 1 if leg.lower() == "right" else 0
        hip_i  = 11 + col   # 12 (right) or 11 (left)
        knee_i = 13 + col   # 14 (right) or 13 (left)
        ank_i  = 15 + col   # 16 (right) or 15 (left)

        detector     = _PatientDetector()
        tracker      = _MPBatchTracker(leg.lower(), fps=fps_v)
        initialised  = False
        angles: list = []

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if not initialised:
                    patient_kps, _ = detector.detect(frame)
                    if patient_kps is not None and patient_kps.shape[0] >= 17:
                        hip   = patient_kps[hip_i].astype(float)
                        knee  = patient_kps[knee_i].astype(float)
                        ankle = patient_kps[ank_i].astype(float)
                        tracker.init(frame, hip, knee, ankle)
                        initialised = True

                if initialised:
                    try:
                        _, _, _, angle = tracker.step(frame)
                        angles.append(float(angle) if angle is not None
                                      else float("nan"))
                    except Exception:
                        angles.append(float("nan"))
                else:
                    angles.append(float("nan"))

                progress_cb(len(angles) / total)
        finally:
            cap.release()

        progress_cb(1.0)
        return angles


# ---------------------------------------------------------------------------
# AcquisitionPanel
# ---------------------------------------------------------------------------

class AcquisitionPanel(tk.Frame):
    """
    2-column, 14-row acquisition panel (480 px wide).
    controller: App instance — receives on_start(), on_stop(),
                on_methodology_changed(method), on_new_trial().
    """

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._countdown_id: Optional[str] = None
        self._tele_buf: list = []
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 5}
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)

        # row 0 — title
        tk.Label(self, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(16, 4))

        # row 1 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 2 — Participant ID
        tk.Label(self, text="Participant ID:").grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        pid_entry = tk.Entry(self, textvariable=self.pid_var, width=22)
        pid_entry.grid(row=2, column=1, sticky="w", **pad)

        # row 3 — Leg
        tk.Label(self, text="Leg:").grid(row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self)
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        rb_left  = tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var, value="Left")
        rb_right = tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right")
        rb_left.pack(side="left", padx=4)
        rb_right.pack(side="left", padx=4)

        # row 4 — MS Status
        tk.Label(self, text="MS Status:").grid(row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ms_combo = ttk.Combobox(self, textvariable=self.ms_var, width=22,
                                state="readonly",
                                values=["MS", "Stroke", "Control", "Other"])
        ms_combo.grid(row=4, column=1, sticky="w", **pad)

        # row 5 — Trial Number
        tk.Label(self, text="Trial Number:").grid(row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        trial_spin = tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6)
        trial_spin.grid(row=5, column=1, sticky="w", **pad)

        # row 6 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 7 — Methodology header
        tk.Label(self, text="Methodology",
                 font=("Segoe UI", 10, "bold")).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=12)

        # row 8 — Methodology radio buttons
        self.method_var = tk.StringVar(value="optitrack")
        meth_f = tk.Frame(self)
        meth_f.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=2)
        rb_opti = tk.Radiobutton(meth_f, text="OptiTrack",  variable=self.method_var,
                                  value="optitrack", command=self._on_method_changed)
        rb_rgb  = tk.Radiobutton(meth_f, text="RGB",         variable=self.method_var,
                                  value="rgb",       command=self._on_method_changed)
        rb_imu  = tk.Radiobutton(meth_f, text="iPhone IMU",  variable=self.method_var,
                                  value="imu",       command=self._on_method_changed)
        for rb in (rb_opti, rb_rgb, rb_imu):
            rb.pack(side="left", padx=8)

        # row 9 — Modality status
        self.lbl_method_status = tk.Label(
            self, text="● Ready", font=("Consolas", 9), fg="green", anchor="w")
        self.lbl_method_status.grid(row=9, column=0, columnspan=2, sticky="w", padx=16)

        # row 10 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 11 — countdown checkbox
        self.countdown_var = tk.BooleanVar(value=False)
        countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var)
        countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        # row 12 — START / STOP (START never moves from col 0)
        self.btn_start = tk.Button(
            self, text="START RECORDING",
            bg=_GREEN, fg="white", font=("Segoe UI", 13, "bold"),
            width=16, height=2, command=self._on_start_clicked)
        self.btn_start.grid(row=12, column=0, padx=10, pady=12)

        self.btn_stop = tk.Button(
            self, text="STOP",
            bg=_RED, fg="white", font=("Segoe UI", 13, "bold"),
            width=16, height=2, state="disabled",
            command=self._on_stop_clicked)
        self.btn_stop.grid(row=12, column=1, padx=10, pady=12)

        # row 13 — live telemetry canvas (NOT gridded at init; shown during RECORDING)
        self.canvas_tele = tk.Canvas(
            self, width=440, height=80, bg="#0B1928", highlightthickness=0)

        # row 14 — status bar
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w", fg="#333")
        self.lbl_status.grid(row=14, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))

        # Track every form widget that must be locked during recording
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            countdown_chk, rb_opti, rb_rgb, rb_imu,
        ]

    # ------------------------------------------------------------------
    # Public state transitions (called by App)
    # ------------------------------------------------------------------
    def enter_idle(self) -> None:
        self._cancel_countdown()
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked,
                              bg=_GREEN, state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_form(False)
        self.canvas_tele.grid_remove()
        self.status_var.set("Idle — ready to record.")

    def enter_recording(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="START RECORDING",
                              bg=_GREEN, state="disabled")
        self.btn_stop.config(state="normal")
        self.canvas_tele.grid(row=13, column=0, columnspan=2, padx=10, pady=4)
        self.status_var.set("RECORDING…")

    def enter_processing(self) -> None:
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.status_var.set("Running MediaPipe tracking…")

    # ------------------------------------------------------------------
    # Validation and metadata
    # ------------------------------------------------------------------
    def validate_metadata(self) -> tuple:
        pid = self.pid_var.get().strip()
        if not pid:
            return False, "Participant ID cannot be empty."
        illegal = set('<>:"/\\|?*')
        if any(c in illegal for c in pid):
            return False, 'Participant ID contains illegal characters: < > : " / \\ | ? *'
        return True, ""

    def get_metadata(self) -> dict:
        return {
            "pid":        self.pid_var.get().strip(),
            "leg":        self.leg_var.get(),
            "ms_status":  self.ms_var.get(),
            "trial":      int(self.trial_var.get()),
            "methodology": self.method_var.get(),
        }

    def increment_trial(self) -> None:
        self.trial_var.set(str(int(self.trial_var.get()) + 1))

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        ok, msg = self.validate_metadata()
        if not ok:
            messagebox.showerror("Cannot Start", msg)
            return
        if self.countdown_var.get():
            self._start_countdown()
        else:
            self.controller.on_start()

    def _on_stop_clicked(self) -> None:
        self.controller.on_stop()

    def _on_method_changed(self) -> None:
        self.controller.on_methodology_changed(self.method_var.get())

    # ------------------------------------------------------------------
    # Countdown
    # ------------------------------------------------------------------
    def _start_countdown(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="CANCEL",
                              command=self._cancel_countdown, bg=_AMBER)
        self.btn_stop.config(state="disabled")
        self._tick_countdown(5)

    def _tick_countdown(self, n: int) -> None:
        if n == 0:
            self.btn_start.config(text="START RECORDING",
                                  command=self._on_start_clicked, bg=_GREEN)
            self.controller.on_start()
            return
        self.status_var.set(f"Starting in {n}…")
        self._countdown_id = self.after(1000, lambda: self._tick_countdown(n - 1))

    def _cancel_countdown(self) -> None:
        if self._countdown_id:
            self.after_cancel(self._countdown_id)
            self._countdown_id = None
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked,
                              bg=_GREEN, state="normal")
        self.btn_stop.config(state="disabled")
        self._lock_form(False)
        self.status_var.set("Countdown cancelled — ready to record.")

    # ------------------------------------------------------------------
    # Form lock
    # ------------------------------------------------------------------
    def _lock_form(self, locked: bool) -> None:
        for w in self._lockable:
            cls = w.winfo_class()
            if cls == "TCombobox":
                w.config(state="disabled" if locked else "readonly")
            else:
                w.config(state="disabled" if locked else "normal")
