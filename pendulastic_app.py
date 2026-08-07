"""
pendulastic_app.py  —  Unified Pendulastic Desktop App
=======================================================
Single-window Tkinter app combining acquisition and post-processing.

Run:
    .venv\\Scripts\\python.exe pendulastic_app.py
"""
from __future__ import annotations

import csv
import json
import math
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
    import imu_calibration_tuner as _tuner
except Exception:
    _tuner = None

try:
    import motive_sync as _motive
    _MOTIVE_AVAIL = True
except Exception:
    _motive = None
    _MOTIVE_AVAIL = False

# On Windows, the MSMF backend can hang for 30-120 seconds opening a USB
# camera because of hardware Media Foundation Transforms. Disabling them
# makes camera open near-instant. This MUST be set before OpenCV (cv2) is
# imported. (Same mitigation as master_app.py.)
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

try:
    import cv2 as _cv2
    from camera_utils import CameraSession, open_video_writer
    _CV2_AVAIL = True
except ImportError:
    _cv2 = None
    CameraSession = None
    open_video_writer = None
    _CV2_AVAIL = False

try:
    from pendulastic_viewer import _MPBatchTracker, _PatientDetector, _draw, TRAIL_LEN
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _draw = None
    TRAIL_LEN = 150
    _VIEWER_AVAIL = False

_mp_pose = _mp_draw = _mp_styles = None
try:
    import mediapipe as _mp
    _mp_pose   = _mp.solutions.pose
    _mp_draw   = _mp.solutions.drawing_utils
    _mp_styles = _mp.solutions.drawing_styles
except Exception:
    pass

try:
    from pendulastic_pt_score import (
        compute_pt_params, compute_pt_score_simple, pt_to_mas,
        HEALTHY_REF, load_optitrack, draw_pt_annotations,
    )
    _PT_AVAIL = True
except Exception:
    compute_pt_params = compute_pt_score_simple = pt_to_mas = None
    HEALTHY_REF = load_optitrack = draw_pt_annotations = None
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

import workbench_style as ws   # zero-dependency (tkinter only) -- always available

try:
    import pt_report_common as _report
    _REPORT_AVAIL = True
except Exception:
    _report = None
    _REPORT_AVAIL = False
    _MPL_AVAIL = False

try:
    from pendulastic_workbench import TrialLoadPanel, WorkbenchView, DashboardView
    import workbench_engine as _wb_engine
    _WORKBENCH_AVAIL = True
except Exception:
    TrialLoadPanel = WorkbenchView = DashboardView = None
    _wb_engine = None
    _WORKBENCH_AVAIL = False

try:
    import mas_validation as _mas_validation
    _MAS_VALIDATION_AVAIL = True
except Exception:
    _mas_validation = None
    _MAS_VALIDATION_AVAIL = False

_GREEN = "#1e7d34"
_RED   = "#a31515"
_BLUE  = "#1f3a93"
_AMBER = "#c07000"

# MasEntryPanel form-feedback colors (shared amber/green label).
_ERROR_FG   = "#B45309"
_SUCCESS_FG = _GREEN

_MAX_CALIB_EXTENSION_S = 5         # extra seconds beyond the base 5s countdown before asking

# ---------------------------------------------------------------------------
# DataManager
# ---------------------------------------------------------------------------

class DataManager:
    DATA_DIR = os.path.join(BASE_DIR, "data")

    @staticmethod
    def build_filename(pid: str, leg: str, ms_status: str, trial: int,
                       source: str | None = None) -> str:
        leg_s  = leg.capitalize()
        ms_s   = ms_status.replace(" ", "_")
        suffix = f"_{source}" if source else ""
        return f"PID_{pid}_LEG_{leg_s}_{ms_s}_TRIAL_{trial}{suffix}.csv"

    @classmethod
    def save_trial(
        cls,
        filename: str,
        angles: list,
        metadata: dict,
        timestamps: list | None = None,
        fps: float = 30.0,
        source: str | None = None,
    ) -> str:
        os.makedirs(cls.DATA_DIR, exist_ok=True)
        path = os.path.join(cls.DATA_DIR, filename)
        t0 = timestamps[0] if timestamps else 0.0
        method_val = source if source else metadata.get("methodology", "")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["frame", "time_s", "knee_angle_deg",
                        "pid", "leg", "ms_status", "trial", "methodology"])
            for i, a in enumerate(angles):
                t = (timestamps[i] - t0) if timestamps else i / fps
                w.writerow([i, f"{t:.4f}", f"{a:.3f}",
                            metadata["pid"], metadata["leg"],
                            metadata["ms_status"], metadata["trial"],
                            method_val])
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
            swing = _imu.get_state().get("swing_angle_deg", float("nan"))
            if math.isfinite(swing):
                return 180.0 - swing
            return float("nan")
        except Exception:
            return float("nan")

    def run_offline_track(
        self,
        video_path: str,
        progress_cb: Callable[[float], None],
        leg: str = "right",
        collect_landmarks: bool = False,
    ):
        """
        Offline MediaPipe tracking on a recorded video.
        Called on a background thread immediately after STOP (RGB methodology).

        Tracker API (from pendulastic_viewer.py):
          _PatientDetector().detect(frame) -> (patient_kps: ndarray(17,2) | None, _)
          _MPBatchTracker(side, fps).init(frame, hip, knee, ankle)
          tracker.step(frame) -> (hip, knee, ankle, angle_deg)

        COCO indices used: 11=L-hip, 12=R-hip, 13=L-knee, 14=R-knee,
                           15=L-ankle, 16=R-ankle

        When collect_landmarks is True, returns (angles, landmarks, fps) where
        landmarks[i] is (hip, knee, ankle) for frame i, or None if pose
        tracking wasn't available for that frame -- len(landmarks) ==
        len(angles) always -- and fps is the video's true source frame rate.
        When False (default), returns angles only, matching the original
        signature exactly.
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return ([], [], 30.0) if collect_landmarks else []

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return ([], [], 30.0) if collect_landmarks else []

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
        landmarks: list = []

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
                        hip_p, knee_p, ank_p, angle = tracker.step(frame)
                        angles.append(float(angle) if angle is not None
                                      else float("nan"))
                        if collect_landmarks:
                            landmarks.append((hip_p, knee_p, ank_p))
                    except Exception:
                        angles.append(float("nan"))
                        if collect_landmarks:
                            landmarks.append(None)
                else:
                    angles.append(float("nan"))
                    if collect_landmarks:
                        landmarks.append(None)

                progress_cb(len(angles) / total)
        finally:
            cap.release()

        progress_cb(1.0)
        return (angles, landmarks, fps_v) if collect_landmarks else angles


# ---------------------------------------------------------------------------
# WebcamViewerWindow
# ---------------------------------------------------------------------------

class WebcamViewerWindow(tk.Toplevel):
    """Separate, resizable window mirroring the live camera preview at a
    much larger size than the embedded panel, with a big red status overlay
    (countdown digits / "HOLD STILL" / "REC"). The operator starts the
    countdown from the laptop and then typically steps back into frame --
    this window is meant to be dragged somewhere they can still read it
    (a second monitor, or just further from the laptop) once they've
    stepped away, so status changes must be legible from across a room,
    not just from the small embedded preview next to the controls."""

    _PREVIEW_W, _PREVIEW_H = 900, 650

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Pendulastic — Camera Preview")
        self.configure(bg="black")
        self.geometry(f"{self._PREVIEW_W + 20}x{self._PREVIEW_H + 20}")
        self.minsize(480, 360)
        # Closing this window shouldn't tear down its Tk resources -- the
        # controller keeps reusing the same instance for the life of the
        # camera session and just re-shows it, so treat the close box as
        # "hide", matching set_camera_live()'s withdraw()/show() cycle.
        self.protocol("WM_DELETE_WINDOW", self.withdraw)

        self.lbl_video = tk.Label(self, bg="black")
        self.lbl_video.pack(fill="both", expand=True)

        self.lbl_overlay = tk.Label(
            self, text="", font=("Segoe UI", 140, "bold"),
            fg="#FF1E1E", bg="black", justify="center",
            wraplength=self._PREVIEW_W - 40)
        self.lbl_overlay.place(relx=0.5, rely=0.5, anchor="center")

    def update_frame(self, frame_bgr) -> None:
        """Convert a BGR numpy frame and display it, scaled to fit this
        window's (larger) target preview size."""
        import base64
        h, w = frame_bgr.shape[:2]
        scale = min(self._PREVIEW_W / max(w, 1), self._PREVIEW_H / max(h, 1))
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        small = _cv2.resize(frame_bgr, (nw, nh))
        # cv2.imencode expects BGR input and writes it out correctly on its
        # own -- do not convert to RGB first (that swaps red/blue).
        ok, buf = _cv2.imencode(".png", small)
        if ok:
            b64 = base64.b64encode(buf).decode("utf-8")
            photo = tk.PhotoImage(data=b64)
            self.lbl_video.config(image=photo)
            self.lbl_video._photo = photo   # prevent GC

    def set_overlay_text(self, text: str) -> None:
        """Big red status text centered over the video. '' clears it."""
        self.lbl_overlay.config(text=text)

    def show(self) -> None:
        """deiconify()/lift() alone only affect stacking order among this
        app's own windows -- Windows' focus-stealing prevention can still
        leave the window opened-but-buried behind whatever else has focus,
        which is exactly the failure mode this window exists to avoid (the
        operator has stepped back and can't see a hidden window). Briefly
        forcing -topmost, then releasing it, reliably pops it to the front
        without permanently pinning it above every other window forever."""
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.after(200, lambda: self.attributes("-topmost", False))


# ---------------------------------------------------------------------------
# AcquisitionPanel
# ---------------------------------------------------------------------------

class AcquisitionPanel(tk.Frame):
    """
    2-column, 14-row acquisition panel (480 px wide).
    controller: App instance — receives on_start(), on_stop(),
                on_source_changed(sources: list[str]), on_new_trial().
    """

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._countdown_id: Optional[str] = None
        self._tele_buf: list = []
        self._is_recording = False
        self._calib_extension_s: int = 0
        self._build_widgets()

    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 5}
        self.configure(bg=ws.PALETTE["BG"])
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)

        # row 0 — header: mode-select back button + title
        hdr0 = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr0.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(16, 4))
        self.btn_back = ws.secondary_button(
            hdr0, "← Mode Select", self.controller.on_back_to_mode_select)
        self.btn_back.pack(side="left", padx=(0, 8))
        tk.Label(hdr0, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        # row 1 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 2 — Participant ID
        tk.Label(self, text="Participant ID:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        pid_entry = tk.Entry(self, textvariable=self.pid_var, width=22)
        pid_entry.grid(row=2, column=1, sticky="w", **pad)

        # row 3 — Leg
        tk.Label(self, text="Leg:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self, bg=ws.PALETTE["BG"])
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        rb_left  = tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var, value="Left",
                                  bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"])
        rb_right = tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                                  bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"])
        rb_left.pack(side="left", padx=4)
        rb_right.pack(side="left", padx=4)

        # row 4 — MS Status
        tk.Label(self, text="MS Status:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ms_combo = ttk.Combobox(self, textvariable=self.ms_var, width=22,
                                state="readonly",
                                values=["MS", "Stroke", "Control", "Other"])
        ms_combo.grid(row=4, column=1, sticky="w", **pad)

        # row 5 — Trial Number
        tk.Label(self, text="Trial Number:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        trial_spin = tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6)
        trial_spin.grid(row=5, column=1, sticky="w", **pad)

        # row 6 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=6, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 7 — Methodology header
        tk.Label(self, text="Methodology",
                 font=("Segoe UI", 10, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=12)

        # row 8 — Source checkboxes
        self._src_optitrack  = tk.BooleanVar(value=False)
        self._src_rgb        = tk.BooleanVar(value=True)
        self._src_imu        = tk.BooleanVar(value=True)
        self._src_video_file = tk.BooleanVar(value=False)

        meth_f = ws.card_frame(self, title="RECORDING SOURCE")
        meth_f.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=2)

        # Always-visible routine sources
        chk_row = tk.Frame(meth_f, bg=ws.PALETTE["PANEL"])
        chk_row.pack(side="top", anchor="w")
        chk_imu = tk.Checkbutton(chk_row, text="iPhone IMU",
                                 variable=self._src_imu,
                                 bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                 selectcolor=ws.PALETTE["SURFACE"],
                                 activebackground=ws.PALETTE["PANEL"],
                                 command=self._on_source_changed)
        chk_rgb = tk.Checkbutton(chk_row, text="RGB",
                                 variable=self._src_rgb,
                                 bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                 selectcolor=ws.PALETTE["SURFACE"],
                                 activebackground=ws.PALETTE["PANEL"],
                                 command=self._on_rgb_checkbox_toggled)
        for chk in (chk_imu, chk_rgb):
            chk.pack(side="left", padx=8)

        # Collapsed "Research sources" disclosure -- OptiTrack and Video
        # File are research-only extras, rarely used in routine clinical
        # sessions (design spec Section 3), so they start hidden.
        self._research_toggle_btn = tk.Button(
            meth_f, text="▸ Research sources (OptiTrack, Video File)",
            font=("Segoe UI", 8), fg=ws.PALETTE["BTN_ACT"], bg=ws.PALETTE["PANEL"],
            relief="flat", bd=0, cursor="hand2", anchor="w",
            activebackground=ws.PALETTE["PANEL"], activeforeground=ws.PALETTE["BTN_ACT"],
            command=self._on_toggle_research_sources)
        self._research_toggle_btn.pack(side="top", anchor="w", pady=(4, 0))

        self._research_sources_frame = tk.Frame(meth_f, bg=ws.PALETTE["PANEL"])
        self._research_sources_expanded = False

        # Own row for the research checkboxes -- packed "top" as a single
        # unit (mirroring the always-visible chk_row above) so the
        # side="top" _video_path_frame packed below it in this same parent
        # lands on its own row instead of sharing this one (Tk's
        # side="left"/"top" mixing in one packer parent does not force a
        # new row on its own).
        self._research_chk_row = tk.Frame(self._research_sources_frame, bg=ws.PALETTE["PANEL"])
        self._research_chk_row.pack(side="top", anchor="w")

        chk_opti = tk.Checkbutton(self._research_chk_row, text="OptiTrack",
                                  variable=self._src_optitrack,
                                  bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                  selectcolor=ws.PALETTE["SURFACE"],
                                  activebackground=ws.PALETTE["PANEL"],
                                  command=self._on_source_changed)
        chk_video = tk.Checkbutton(self._research_chk_row, text="Video File",
                                   variable=self._src_video_file,
                                   bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                   selectcolor=ws.PALETTE["SURFACE"],
                                   activebackground=ws.PALETTE["PANEL"],
                                   command=self._on_source_changed)
        chk_opti.pack(side="left", padx=8)
        chk_video.pack(side="left", padx=8)

        # Video file path selector (hidden until _src_video_file checked) --
        # nested inside the research-sources frame since it's a
        # research-only source.
        self._video_path_frame = tk.Frame(self._research_sources_frame, bg=ws.PALETTE["PANEL"])
        self._video_path_var    = tk.StringVar(value="No file selected")
        self._stored_video_path = ""
        tk.Label(self._video_path_frame,
                textvariable=self._video_path_var,
                font=("Consolas", 8), fg=ws.PALETTE["FG2"], bg=ws.PALETTE["PANEL"],
                width=38, anchor="w").pack(side="left")
        ws.secondary_button(self._video_path_frame, "Browse...",
                            self._on_browse_video).pack(side="left", padx=4)
        self._video_path_frame.pack(side="top", anchor="w", pady=(2, 0))
        self._video_path_frame.pack_forget()   # hidden until checkbox checked

        # Camera selector (hidden until RGB is checked) -- unaffected by the
        # research-sources disclosure; RGB is a routine, always-visible source.
        self._cam_frame = tk.Frame(meth_f, bg=ws.PALETTE["PANEL"])
        self.cam_var = tk.StringVar(value="")
        self.drop_cam = ttk.Combobox(self._cam_frame, textvariable=self.cam_var,
                                     width=18, state="readonly")
        self.drop_cam.pack(side="left")
        self.drop_cam.bind("<<ComboboxSelected>>", self._on_cam_selected)
        self.btn_rescan = ws.secondary_button(self._cam_frame, "Rescan", self._on_rescan_clicked)
        self.btn_rescan.pack(side="left", padx=4)
        ws.secondary_button(self._cam_frame, "\U0001f6dc Can't connect?",
                            self._on_camera_help).pack(side="left", padx=4)
        self._cam_frame.pack_forget()   # hidden until RGB is checked
        self._viewer_window: Optional[WebcamViewerWindow] = None
        self._camera_live = False   # one input to _sync_viewer_window_visibility()

        # row 9 — Modality status (calibration is now automatic during the
        # countdown -- see App._tick_calibration_check / AcquisitionPanel's
        # forced-on countdown checkbox below)
        self.lbl_method_status = tk.Label(
            self, text="● OptiTrack (Motive)", font=("Consolas", 9), fg="green",
            bg=ws.PALETTE["BG"], anchor="w")
        self.lbl_method_status.grid(row=9, column=0, sticky="w", padx=16)

        # row 10 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 11 — countdown checkbox (forced on/locked while IMU is an
        # active source -- it's the only calibration path now)
        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"])
        self.countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)

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
            self, textvariable=self.status_var, relief="sunken", anchor="w",
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"])
        self.lbl_status.grid(row=14, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))

        # Track every form widget that must be locked during recording
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            self.countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            self._research_toggle_btn,
            self.btn_back, self.drop_cam, self.btn_rescan,
        ]

        # Initialize status label and countdown lock based on default sources
        self._on_source_changed()

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
        self._is_recording = False
        self._refresh_preview_area()
        self.status_var.set("Idle — ready to record.")
        self._apply_countdown_lock()   # re-apply the IMU-forced countdown lock
        self.set_viewer_overlay_text("")
        self._sync_viewer_window_visibility()   # hide if nothing else keeps it open

    def enter_recording(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="START RECORDING",
                              bg=_GREEN, state="disabled")
        self.btn_stop.config(state="normal")
        self._is_recording = True
        self._refresh_preview_area()
        self.status_var.set("RECORDING…")
        self._sync_viewer_window_visibility()   # cover the no-countdown instant-start case
        self.set_viewer_overlay_text("● REC")

    def _refresh_preview_area(self) -> None:
        """Row 13 shows the live telemetry canvas while recording, hidden
        otherwise. Live RGB preview -- while idle and during recording --
        now lives entirely in the separate WebcamViewerWindow, so this
        panel no longer needs its own embedded copy."""
        if self._is_recording:
            self.canvas_tele.grid(row=13, column=0, columnspan=2,
                                  padx=10, pady=4)
        else:
            self.canvas_tele.grid_remove()

    def enter_processing(self) -> None:
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.status_var.set("Running MediaPipe tracking…")

    def update_preview(self, frame_bgr) -> None:
        """Forward a live BGR frame to the separate webcam viewer window,
        if it's open. There's no embedded preview any more -- the viewer
        window covers both the pre-recording live preview and the feed
        during recording."""
        if self._viewer_window is not None and self._viewer_window.winfo_exists():
            self._viewer_window.update_frame(frame_bgr)

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
        if not self.get_active_sources():
            return False, "Select at least one recording source."
        if self._src_video_file.get() and self._src_rgb.get():
            return False, "Cannot use 'Video File' and live RGB simultaneously."
        if self._src_video_file.get() and not self.get_video_file_path():
            return False, "Select a video file before starting."
        return True, ""

    def get_metadata(self) -> dict:
        return {
            "pid":             self.pid_var.get().strip(),
            "leg":             self.leg_var.get(),
            "ms_status":       self.ms_var.get(),
            "trial":           int(self.trial_var.get()),
            "sources":         self.get_active_sources(),
            "video_file_path": self.get_video_file_path() if self._src_video_file.get() else None,
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

    def _apply_countdown_lock(self) -> None:
        """IMU trials have no calibration path other than the countdown."""
        if self._src_imu.get():
            self.countdown_var.set(True)
            self.countdown_chk.config(state="disabled")
        else:
            self.countdown_chk.config(state="normal")

    def _on_source_changed(self) -> None:
        """Called on any source checkbox toggle. Updates status label and
        forces the countdown on (IMU trials have no other calibration path
        now that the manual Zero Sensor button is gone)."""
        sources = self.get_active_sources()
        self._apply_countdown_lock()
        # Show/hide video file path frame
        if self._src_video_file.get():
            self._video_path_frame.pack(side="top", anchor="w", pady=(2, 0))
        else:
            self._video_path_frame.pack_forget()
        # Show/hide camera selector frame -- pure UI sync (no controller
        # call here; _on_rgb_checkbox_toggled is what notifies the
        # controller). This keeps the frame's visibility correct both on
        # user toggle AND on initial build, since RGB now defaults to
        # checked and _build_widgets calls _on_source_changed(), not
        # _on_rgb_checkbox_toggled(), to establish the starting UI state.
        if self._src_rgb.get():
            self._cam_frame.pack(side="top", anchor="w", pady=(2, 0),
                                  before=self._research_toggle_btn)
        else:
            self._cam_frame.pack_forget()
        # Build status line
        source_labels = {
            "imu":        "iPhone IMU — waiting for phone" if _IMU_AVAIL else "iPhone IMU — unavailable",
            "rgb":        "RGB / MediaPipe" if _VIEWER_AVAIL else "RGB — MediaPipe unavailable",
            "optitrack":  "OptiTrack (Motive)" if _MOTIVE_AVAIL else "OptiTrack — Motive not found",
            "video_file": f"Video: {os.path.basename(self.get_video_file_path()) or 'no file'}",
        }
        if sources:
            label_parts = [source_labels[s] for s in sources]
            label = "● " + " + ".join(label_parts)
            color = "green"
        else:
            label = "● No source selected"
            color = "red"
        self.lbl_method_status.config(text=label, fg=color)
        self.controller.on_source_changed(sources)

    def get_active_sources(self) -> list:
        """Return sorted list of checked source keys."""
        sources = []
        if self._src_imu.get():        sources.append("imu")
        if self._src_optitrack.get():  sources.append("optitrack")
        if self._src_rgb.get():        sources.append("rgb")
        if self._src_video_file.get(): sources.append("video_file")
        return sorted(sources)

    def _on_browse_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Select pre-recorded video",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"),
                       ("All files", "*.*")])
        if path:
            self._stored_video_path = path
            self._video_path_var.set(os.path.basename(path))
        # If user cancelled, keep existing path

    def get_video_file_path(self) -> str:
        """Return the currently selected video file path, or empty string."""
        return getattr(self, "_stored_video_path", "")

    def _on_rgb_checkbox_toggled(self) -> None:
        if self._src_rgb.get():
            self._cam_frame.pack(side="top", anchor="w", pady=(2, 0),
                                  before=self._research_toggle_btn)
            self.controller.on_rescan_cameras()
        else:
            self._cam_frame.pack_forget()
            self.controller.on_camera_disabled()
        self._on_source_changed()

    def _on_toggle_research_sources(self) -> None:
        self._research_sources_expanded = not self._research_sources_expanded
        if self._research_sources_expanded:
            self._research_sources_frame.pack(side="top", anchor="w", pady=(4, 0))
            self._research_toggle_btn.config(text="▾ Research sources (OptiTrack, Video File)")
        else:
            self._research_sources_frame.pack_forget()
            self._research_toggle_btn.config(text="▸ Research sources (OptiTrack, Video File)")

    def _on_cam_selected(self, event=None) -> None:
        label = self.cam_var.get()
        if label and label != "(none detected)":
            self.controller.on_camera_selected(label)

    def _on_rescan_clicked(self) -> None:
        self.controller.on_rescan_cameras()

    def _on_camera_help(self) -> None:
        messagebox.showinfo(
            "Can't connect to a camera?",
            "If no cameras are detected:\n\n"
            "1. Make sure the USB webcam is plugged in and not in use by "
            "another app (Zoom, Teams, Camera).\n"
            "2. Check Windows camera privacy settings: Settings > Privacy & "
            "security > Camera, and make sure camera access is turned on "
            "for desktop apps.\n"
            "3. Click Rescan after making changes.")

    def set_camera_list(self, cams: list) -> None:
        """Populate the camera dropdown. Keeps the current selection if it's
        still present in `cams`, else selects the first one (or shows
        '(none detected)' if the list is empty)."""
        labels = [c["label"] for c in cams]
        self.drop_cam["values"] = labels if labels else ["(none detected)"]
        if labels:
            prev = self.cam_var.get()
            self.cam_var.set(prev if prev in labels else labels[0])
        else:
            self.cam_var.set("(none detected)")

    def set_camera_live(self, is_live: bool) -> None:
        """Called by the controller when the pre-open camera session's
        live/lost state changes. One of three independent triggers for the
        separate viewer window -- see _sync_viewer_window_visibility()."""
        self._camera_live = is_live
        self._sync_viewer_window_visibility()

    def _sync_viewer_window_visibility(self) -> None:
        """The viewer window must be visible whenever there's something for
        it to show: a live camera feed, an active countdown, or an active
        recording -- even for a phone-IMU-only trial with no webcam at all,
        since the whole point is visibility for an operator who has stepped
        back and can't read the small embedded controls."""
        should_show = (self._camera_live or self._countdown_id is not None
                       or self._is_recording)
        if should_show:
            self._ensure_viewer_window().show()
        elif self._viewer_window is not None and self._viewer_window.winfo_exists():
            self._viewer_window.withdraw()

    def _ensure_viewer_window(self) -> WebcamViewerWindow:
        if self._viewer_window is None or not self._viewer_window.winfo_exists():
            self._viewer_window = WebcamViewerWindow(self)
        return self._viewer_window

    def set_viewer_overlay_text(self, text: str) -> None:
        """Big red status text in the separate viewer window (countdown
        digits / 'HOLD STILL' / '● REC'). No-op if the window was never
        opened (e.g. RGB isn't an active source)."""
        if self._viewer_window is not None and self._viewer_window.winfo_exists():
            self._viewer_window.set_overlay_text(text)

    # ------------------------------------------------------------------
    # Countdown
    # ------------------------------------------------------------------
    def _start_countdown(self) -> None:
        self.controller.on_countdown_start()
        self._calib_extension_s = 0
        self._lock_form(True)
        self.btn_start.config(text="CANCEL",
                              command=self._cancel_countdown, bg=_AMBER)
        self.btn_stop.config(state="disabled")
        # Ensure the window exists before ticking -- _tick_countdown() calls
        # set_viewer_overlay_text(), which is a no-op until the window
        # actually exists. Shows it even without a live camera (e.g. a
        # phone-IMU-only trial).
        self._ensure_viewer_window().show()
        self._tick_countdown(5)

    def _proceed_to_recording(self) -> None:
        """Helper to complete countdown and start recording.
        Clears the countdown timer, resets button state, and calls on_start()."""
        self._countdown_id = None
        self.btn_start.config(text="START RECORDING",
                              command=self._on_start_clicked, bg=_GREEN)
        self.controller.on_start()

    def _tick_countdown(self, n: int) -> None:
        if n == 0:
            if self.controller.is_imu_calibrated():
                self._proceed_to_recording()
                return
            if self._calib_extension_s < _MAX_CALIB_EXTENSION_S:
                self._calib_extension_s += 1
                self.status_var.set("Hold steady…")
                self.set_viewer_overlay_text("HOLD STILL")
                self._countdown_id = self.after(1000, lambda: self._tick_countdown(0))
                return
            if messagebox.askyesno(
                    "Sensor Not Stable",
                    "The IMU sensor hasn't settled to a stable reading, so this "
                    "trial could not be calibrated. Recording now will reuse the "
                    "calibration from earlier in this session — the angles may "
                    "be wrong. Start anyway?"):
                self._proceed_to_recording()
            else:
                self._cancel_countdown()
            return
        if "imu" in self.get_active_sources():
            calib_suffix = (" — ✓ calibrated" if self.controller.is_imu_calibrated()
                           else " — stabilizing…")
        else:
            calib_suffix = ""
        self.status_var.set(f"Starting in {n}…{calib_suffix}")
        self.set_viewer_overlay_text(str(n))
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
        self._apply_countdown_lock()   # re-apply the IMU-forced countdown lock
        self.status_var.set("Countdown cancelled — ready to record.")
        self.set_viewer_overlay_text("")
        self._sync_viewer_window_visibility()   # hide if nothing else keeps it open

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

    # ------------------------------------------------------------------
    # Live telemetry sparkline (driven by App._tick every 50 ms)
    # ------------------------------------------------------------------
    _TELE_MAX = 120   # rolling window ~6 s at 20 Hz

    def push_telemetry(self, t: float, angle_deg: float) -> None:
        self._tele_buf.append((t, angle_deg))
        if len(self._tele_buf) > self._TELE_MAX:
            self._tele_buf.pop(0)
        self._draw_sparkline()

    def clear_telemetry(self) -> None:
        self._tele_buf.clear()
        self.canvas_tele.delete("all")

    def _draw_sparkline(self) -> None:
        import math
        c = self.canvas_tele
        c.delete("all")
        if not self._tele_buf:
            return

        W, H    = 440, 80
        NUM_W   = 110
        GRAPH_W = W - NUM_W - 8
        last_a  = self._tele_buf[-1][1]

        # Numeric readout on the right
        if math.isnan(last_a):
            txt, col = "—", "gray"
        else:
            txt, col = f"{last_a:.1f}°", "#22c55e"
        cx = W - NUM_W // 2
        c.create_text(cx, H // 2 - 6, text=txt,
                      fill="white", font=("Consolas", 18, "bold"), anchor="center")
        c.create_text(cx, H // 2 + 14, text="knee",
                      fill="#5A8AB0", font=("Consolas", 8), anchor="center")

        # Sparkline
        valid = [(t, a) for t, a in self._tele_buf if not math.isnan(a)]
        if len(valid) < 2:
            return
        vals  = [a for _, a in valid]
        lo, hi = min(vals), max(vals)
        if hi - lo < 5:
            mid = (lo + hi) / 2; lo, hi = mid - 2.5, mid + 2.5

        def px(i, a):
            x = int(8 + (i / (len(valid) - 1)) * (GRAPH_W - 16))
            y = int(H - 8 - ((a - lo) / (hi - lo)) * (H - 16))
            return x, y

        pts = [px(i, a) for i, (_, a) in enumerate(valid)]
        for i in range(len(pts) - 1):
            c.create_line(*pts[i], *pts[i + 1], fill=col, width=1.5)
        lx, ly = pts[-1]
        c.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=col, outline="")


# ---------------------------------------------------------------------------
# ModeSelectView
# ---------------------------------------------------------------------------

class ModeSelectView(tk.Frame):
    """Startup landing screen — routes to live recording or file upload."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])
        tk.Label(self, text="Pendulastic",
                 font=("Segoe UI", 20, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=0, column=0, columnspan=2, pady=(60, 4))
        tk.Label(self, text="Clinical Pendulum Test Platform",
                 font=("Segoe UI", 11),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).grid(
            row=1, column=0, columnspan=2, pady=(0, 40))

        # Live Recording is the routine clinical path -- the one primary
        # (filled-accent) action on this screen.
        live_btn = ws.primary_button(
            self, "Live Recording Session\nIMU · RGB · OptiTrack",
            self.controller._enter_live_mode)
        live_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        live_btn.grid(row=2, column=0, padx=40, pady=16, sticky="n")

        upload_btn = ws.secondary_button(
            self, "Upload & Analyze\nVideo or CSV file",
            self.controller._enter_upload_mode)
        upload_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        upload_btn.grid(row=2, column=1, padx=40, pady=16, sticky="n")

        workbench_btn = ws.secondary_button(
            self, "Multi-Modal Comparison\nIMU · OptiTrack · Video",
            self.controller._enter_workbench_mode)
        workbench_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        workbench_btn.grid(row=3, column=0, columnspan=2, padx=40, pady=(0, 12), sticky="n")

        # 4th button (added after this plan was written -- see task-5 brief
        # discrepancy note): styled as another secondary action, consistent
        # with Upload & Analyze / Multi-Modal Comparison above.
        analysis_btn = ws.secondary_button(
            self, "Analysis & Reports\nCompare Participants",
            self.controller._enter_analysis_mode)
        analysis_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        analysis_btn.grid(row=4, column=0, columnspan=2, padx=40, pady=(0, 12), sticky="n")

        mas_btn = ws.secondary_button(
            self, "MAS Score Entry\nEnter & Validate",
            self.controller._enter_mas_entry_mode)
        mas_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        mas_btn.grid(row=5, column=0, columnspan=2, padx=40, pady=(0, 24), sticky="n")


# ---------------------------------------------------------------------------
# UploadMetaView
# ---------------------------------------------------------------------------

class UploadMetaView(tk.Frame):
    """Compact metadata form for file-first upload analysis."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller  = controller
        self._file_path  = ""
        self.status_var  = tk.StringVar(value="Ready")
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])
        pad = {"padx": 12, "pady": 6}

        # Header: back button + title
        hdr = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew",
                 padx=12, pady=(16, 4))
        self.btn_back = ws.secondary_button(
            hdr, "← Back", self.controller._upload_back_to_select)
        self.btn_back.pack(side="left", padx=(0, 12))
        tk.Label(hdr, text="Upload & Analyze",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        # Selected file name
        self._file_label_var = tk.StringVar(value="No file selected")
        tk.Label(self, textvariable=self._file_label_var,
                 font=("Consolas", 9), fg=ws.PALETTE["FG2"], bg=ws.PALETTE["BG"],
                 anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

        # Participant ID
        tk.Label(self, text="Participant ID:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        tk.Entry(self, textvariable=self.pid_var, width=22).grid(
            row=2, column=1, sticky="w", **pad)

        # Leg
        tk.Label(self, text="Leg:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self, bg=ws.PALETTE["BG"])
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var, value="Left",
                       bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]).pack(
            side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                       bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]).pack(
            side="left", padx=4)

        # MS Status
        tk.Label(self, text="MS Status:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ttk.Combobox(self, textvariable=self.ms_var, width=22, state="readonly",
                     values=["MS", "Stroke", "Control", "Other"]).grid(
            row=4, column=1, sticky="w", **pad)

        # Trial number
        tk.Label(self, text="Trial Number:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6).grid(
            row=5, column=1, sticky="w", **pad)

        # Analyze button -- the single primary action on this screen
        self.btn_analyze = ws.primary_button(
            self, "Analyze →", self.controller._start_upload_analysis)
        self.btn_analyze.config(font=("Segoe UI", 11, "bold"), width=16, height=2)
        self.btn_analyze.grid(row=6, column=0, columnspan=2, pady=20)

        # Status bar
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w",
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_file(self, path: str) -> None:
        self._file_path = path
        self._file_label_var.set(f"File: {os.path.basename(path)}")

    def get_metadata(self) -> dict:
        return {
            "pid":        self.pid_var.get().strip(),
            "leg":        self.leg_var.get(),
            "ms_status":  self.ms_var.get(),
            "trial":      int(self.trial_var.get()),
            "sources":    ["upload_csv"
                           if self._file_path.lower().endswith(".csv")
                           else "video_file"],
        }

    def set_processing(self, active: bool) -> None:
        state = "disabled" if active else "normal"
        self.btn_back.config(state=state)
        self.btn_analyze.config(state=state)


# ---------------------------------------------------------------------------
# PostProcessingPanel
# ---------------------------------------------------------------------------

class PostProcessingPanel(tk.Frame):
    """
    Full-window post-processing panel: angle curve + PT metrics (rows 0-4).
    rowconfigure(1, weight=1) lets the matplotlib figure expand to fill height.
    """

    _CURVE_STYLES = {
        "imu":        {"color": "#2563EB", "ls": "-",   "label": "IMU"},
        "rgb":        {"color": "#16A34A", "ls": "-",   "label": "RGB"},
        "optitrack":  {"color": "#D97706", "ls": "--",  "label": "OptiTrack"},
        "hpe_upload": {"color": "#7C3AED", "ls": "--",  "label": "HPE Upload"},
        "video_file": {"color": "#7C3AED", "ls": "--",  "label": "Video File (HPE)"},
        "upload_csv": {"color": "#0891B2", "ls": "--",  "label": "CSV Upload"},
    }
    _PT_SOURCE_PRIORITY = ["imu", "rgb", "optitrack", "hpe_upload", "video_file", "upload_csv"]

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller      = controller
        self._source_angles: dict  = {}
        self._fps: float           = 30.0
        self._meta: dict | None    = None
        self._plot_annots: list    = []
        self._last_pt_params: dict | None = None
        self._video_path: str | None = None
        self._hpe_leg: str           = "right"
        self._hpe_landmarks: list | None = None
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)
        self.columnconfigure(3, weight=1)

        self.configure(bg=ws.PALETTE["BG"])

        # row 0 — header: mode-select back button + trial filename
        hdr0 = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr0.grid(row=0, column=0, columnspan=4, sticky="ew",
                  padx=12, pady=(12, 4))
        ws.secondary_button(hdr0, "← Mode Select",
                            self.controller.on_back_to_mode_select).pack(
            side="left", padx=(0, 12))
        self.title_var = tk.StringVar(value="")
        tk.Label(hdr0, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w",
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        # row 1 — matplotlib figure
        if _MPL_AVAIL:
            self._fig    = Figure(figsize=(10, 4), dpi=96, facecolor=ws.PALETTE["BG"])
            self._ax     = self._fig.add_subplot(111)
            self._canvas = FigureCanvasTkAgg(self._fig, master=self)
            self._canvas.get_tk_widget().grid(
                row=1, column=0, columnspan=4, sticky="nsew", padx=8, pady=4)
        else:
            tk.Label(self, text="matplotlib not available — install it in .venv",
                     bg=ws.PALETTE["BG"], fg="red").grid(row=1, column=0, columnspan=4)
            self._canvas = None

        # row 2 — PT Metrics card
        self._metrics_frame = tk.LabelFrame(
            self, text="Popović PT Metrics", font=("Segoe UI", 9, "bold"),
            padx=8, pady=4, bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG3"],
            highlightbackground=ws.PALETTE["BORDER"], highlightthickness=1,
            relief="flat", bd=0)
        self._metrics_frame.grid(row=2, column=0, columnspan=4, sticky="ew", padx=10, pady=4)

        self.a1_var    = tk.StringVar(value="—")
        self.omega_var = tk.StringVar(value="—")
        self.n_var     = tk.StringVar(value="—")
        self.f_var     = tk.StringVar(value="—")
        self.r2n_var   = tk.StringVar(value="—")
        self.mas_var   = tk.StringVar(value="—")
        self.score_var = tk.StringVar(value="—")

        for col, (lbl, var) in enumerate([
            ("A1 (deg)",  self.a1_var),
            ("w (deg/s)", self.omega_var),
            ("N",         self.n_var),
            ("f (Hz)",    self.f_var),
            ("R2N",       self.r2n_var),
            ("MAS",       self.mas_var),
            ("Score",     self.score_var),
        ]):
            tk.Label(self._metrics_frame, text=lbl, font=("Segoe UI", 8),
                     bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG2"]).grid(
                row=0, column=col, padx=10, pady=1)
            tk.Label(self._metrics_frame, textvariable=var,
                     font=("Segoe UI", 11, "bold"),
                     bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"]).grid(
                row=1, column=col, padx=10)

        # row 3 — action buttons (utility actions, no single primary action
        # on a review-only screen -- all secondary-styled)
        ws.secondary_button(self, "← New Trial", self._on_new_trial).grid(
            row=3, column=0, padx=10, pady=12, sticky="e")
        ws.secondary_button(self, "Load OptiTrack CSV", self._on_load_optitrack).grid(
            row=3, column=1, padx=10, pady=12, sticky="w")
        self.btn_upload_video = ws.secondary_button(
            self, "\U0001f3a5 Upload Video for HPE", self._on_upload_video)
        self.btn_upload_video.grid(row=3, column=2, padx=10, pady=12, sticky="w")
        self.btn_export_video = ws.secondary_button(
            self, "🎬 Export Annotated Video",
            lambda: self._cmd_export_annotated_video())
        self.btn_export_video.config(state="disabled")
        self.btn_export_video.grid(row=3, column=3, padx=10, pady=12, sticky="w")

        # row 4 — status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w",
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).grid(
            row=4, column=0, columnspan=4, sticky="ew", padx=10, pady=(0, 8))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_trial(
        self,
        source_angles: dict,
        fps: float,
        metadata: dict,
        base_filename: str,
    ) -> None:
        self._source_angles = dict(source_angles)
        self._fps           = fps
        self._meta          = metadata
        self.title_var.set(base_filename)
        self._plot_all_curves()
        self._show_pt_metrics_from_sources()
        self.status_var.set(f"Saved: {base_filename}")

    def load_optitrack_overlay(self, csv_path: str) -> None:
        if not _PT_AVAIL or load_optitrack is None:
            messagebox.showerror("OptiTrack", "load_optitrack not available.")
            return
        try:
            _t_ot, opti = load_optitrack(csv_path)
            self._source_angles["optitrack"] = list(opti)
            self._plot_all_curves()
            self._show_pt_metrics_from_sources()
            self.status_var.set(f"Overlay: {os.path.basename(csv_path)}")
        except Exception as e:
            messagebox.showerror("OptiTrack Load Error", str(e))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _plot_all_curves(self) -> None:
        if not _MPL_AVAIL or self._canvas is None:
            return
        self._ax.clear()
        self._plot_annots = []
        n_curves = 0
        fps = self._fps or 30.0
        for src, angles in self._source_angles.items():
            if not angles:
                continue
            style = self._CURVE_STYLES.get(
                src, {"color": "gray", "ls": "-", "label": src})
            times = [i / fps for i in range(len(angles))]
            self._ax.plot(times, angles,
                          color=style["color"], linewidth=1.5,
                          linestyle=style["ls"], label=style["label"])
            n_curves += 1
        if n_curves >= 2:
            self._ax.legend(fontsize=8)
        self._ax.set_xlabel("Time (s)", fontsize=9)
        self._ax.set_ylabel("Knee angle (deg)", fontsize=9)
        self._ax.set_title("Popović Pendulum Test — Knee Angle", fontsize=10)
        self._ax.grid(True, alpha=0.3)
        self._fig.tight_layout()
        self._canvas.draw()

    def _show_pt_metrics_from_sources(self) -> None:
        if not _PT_AVAIL or compute_pt_params is None:
            return
        fps = self._fps or 30.0
        for src in self._PT_SOURCE_PRIORITY:
            angles = self._source_angles.get(src)
            if not angles:
                continue
            t   = np.arange(len(angles), dtype=float) / fps
            arr = np.array(angles, dtype=float)
            # IMU trials are now always freshly auto-tared and recorded at a
            # verified-usable sample rate (see App._tick_calibration_check and
            # the gyro-rate warning), so the drift this compensated for is no
            # longer expected -- global linear detrending before release
            # detection was instead corrupting the release-point amplitude
            # and silently discarding valid trials. imu_calibration_tuner.py's
            # own truthfulness gate already treats raw signal as authoritative
            # for IMU data; match that here.
            try:
                p = compute_pt_params(t, arr, detrend=False)
            except TypeError:
                p = compute_pt_params(t, arr)   # backward compat
            if p is None:
                continue
            score = compute_pt_score_simple(p)
            mas   = pt_to_mas(score)
            self._metrics_frame.config(
                text=f"Popović PT Metrics (source: {src.upper()})")
            self.a1_var.set(f"{p['A1_deg']:.1f}")
            self.omega_var.set(f"{p['omega_peak_deg_s']:.1f}")
            self.n_var.set(f"{p['N']:.1f}")
            self.f_var.set(f"{p['f']:.2f}")
            self.r2n_var.set(f"{p['R2n']:.3f}")
            self.mas_var.set(str(mas))
            self.score_var.set(f"{score:.3f}")

            self._last_pt_params = p
            if self._canvas is not None and draw_pt_annotations is not None:
                artists = draw_pt_annotations(self._ax, p)
                if artists is not None:
                    self._plot_annots = artists
                    self._canvas.draw_idle()
            return
        self.status_var.set("PT scoring: no valid source data.")

    def _on_upload_video(self) -> None:
        if not _VIEWER_AVAIL:
            messagebox.showerror(
                "HPE Unavailable",
                "pendulastic_viewer not importable — cannot run MediaPipe.")
            return
        path = filedialog.askopenfilename(
            title="Select video for HPE",
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"),
                       ("All files", "*.*")])
        if not path:
            return
        self.status_var.set("HPE processing: 0%")
        leg    = self._meta.get("leg", "right") if self._meta else "right"
        self._video_path = path
        self._hpe_leg     = leg
        self._hpe_landmarks = None
        self._source_angles.pop("hpe_upload", None)
        self.btn_export_video.config(state="disabled")
        engine = BiomechanicalEngine("rgb")

        def _progress(pct: float) -> None:
            self.after(0, lambda p=pct: self.status_var.set(
                f"HPE processing: {int(p * 100)}%"))

        def _run() -> None:
            angles, landmarks, video_fps = engine.run_offline_track(
                path, _progress, leg=leg.lower(), collect_landmarks=True)
            self.after(0, lambda: self._add_hpe_overlay(angles, landmarks, fps=video_fps))

        threading.Thread(target=_run, daemon=True).start()

    def _add_hpe_overlay(self, angles: list, landmarks: list | None = None,
                          fps: float = 30.0) -> None:
        if not angles:
            self.status_var.set(
                "HPE: no pose detected — check video or leg selection.")
            return
        self._source_angles["hpe_upload"] = angles
        self._hpe_landmarks = landmarks
        if not self._fps:
            self._fps = fps
        if not self.title_var.get():
            self.title_var.set("HPE upload")
        self._plot_all_curves()
        self._show_pt_metrics_from_sources()
        self.status_var.set(f"HPE overlay loaded — {len(angles)} frames")
        if landmarks and self._video_path:
            self.btn_export_video.config(state="normal")

    def _cmd_export_annotated_video(self) -> None:
        if not self._video_path or not self._hpe_landmarks:
            messagebox.showinfo(
                "Export Video",
                "Upload a video for HPE and let tracking finish first.")
            return
        angles = self._source_angles.get("hpe_upload")
        if not angles:
            messagebox.showinfo("Export Video", "No HPE angle data to export.")
            return

        base, _ = os.path.splitext(self._video_path)
        default_name = os.path.basename(base) + "_annotated.mp4"
        out_path = filedialog.asksaveasfilename(
            title="Save Annotated Video",
            initialfile=default_name,
            initialdir=os.path.dirname(self._video_path),
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4"), ("AVI Video", "*.avi"),
                       ("All files", "*.*")],
        )
        if not out_path:
            return

        snap = {
            "path":      self._video_path,
            "fps":       self._fps or 30.0,
            "angles":    list(angles),
            "landmarks": list(self._hpe_landmarks),
        }

        self.btn_export_video.config(state="disabled")
        self.status_var.set("Exporting annotated video… 0%")
        threading.Thread(target=self._export_annotated_worker,
                         args=(snap, out_path), daemon=True).start()

    def _export_annotated_worker(self, snap: dict, out_path: str) -> None:
        angles    = snap["angles"]
        landmarks = snap["landmarks"]
        fps       = snap["fps"]
        n_total   = len(angles)

        cap2 = _cv2.VideoCapture(snap["path"])
        if not cap2.isOpened():
            self.after(0, lambda: (
                self.btn_export_video.config(state="normal"),
                self.status_var.set("Export failed: cannot re-open video file."),
                messagebox.showerror("Export failed",
                                     f"Could not open video for reading:\n{snap['path']}")
            ))
            return
        w = int(cap2.get(_cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap2.get(_cv2.CAP_PROP_FRAME_HEIGHT))

        writer = open_video_writer(out_path, fps, w, h)

        if writer is None:
            cap2.release()
            self.after(0, lambda: (
                self.btn_export_video.config(state="normal"),
                self.status_var.set("Export failed: no usable video codec found."),
                messagebox.showerror("Export failed",
                                     "Could not find a working video codec.\n"
                                     "Try saving as .avi instead of .mp4.")
            ))
            return

        rolling_trail = []

        try:
            for fi in range(n_total):
                ok, frame = cap2.read()
                if not ok:
                    break

                ang = angles[fi] if fi < len(angles) else float("nan")
                lm  = landmarks[fi] if fi < len(landmarks) else None
                hip, kne, ank = lm if lm is not None else (None, None, None)

                if ank is not None:
                    rolling_trail.append(ank)
                    if len(rolling_trail) > TRAIL_LEN:
                        rolling_trail.pop(0)

                overlay = _draw(frame, hip, kne, ank, ang,
                                list(rolling_trail), scale=1.0)

                if math.isfinite(ang):
                    ang_txt = f"{ang:.1f} deg"
                    _cv2.putText(overlay, ang_txt, (16, h - 18),
                                _cv2.FONT_HERSHEY_DUPLEX, 1.1,
                                (0, 0, 0), 4, _cv2.LINE_AA)
                    _cv2.putText(overlay, ang_txt, (16, h - 18),
                                _cv2.FONT_HERSHEY_DUPLEX, 1.1,
                                (80, 230, 140), 2, _cv2.LINE_AA)

                t_txt = f"{fi / fps:.2f} s"
                _cv2.putText(overlay, t_txt, (16, h - 52),
                            _cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (0, 0, 0), 3, _cv2.LINE_AA)
                _cv2.putText(overlay, t_txt, (16, h - 52),
                            _cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (190, 190, 190), 1, _cv2.LINE_AA)

                writer.write(overlay)

                if fi % 30 == 0:
                    pct = int(fi / max(n_total, 1) * 100)
                    self.after(0, lambda p=pct: self.status_var.set(
                        f"Exporting annotated video… {p}%"))

        except Exception as exc:
            cap2.release()
            writer.release()
            self.after(0, lambda e=str(exc): (
                self.btn_export_video.config(state="normal"),
                self.status_var.set(f"Export error: {e}"),
                messagebox.showerror("Export error", f"An error occurred during export:\n{e}")
            ))
            return

        cap2.release()
        writer.release()
        self.after(0, lambda p=out_path: self._on_export_video_done(p))

    def _on_export_video_done(self, out_path: str) -> None:
        self.btn_export_video.config(state="normal")
        name = os.path.basename(out_path)
        self.status_var.set(f"Annotated video saved: {name}")
        messagebox.showinfo("Export complete",
                            f"Annotated video saved:\n{out_path}")

    def _on_new_trial(self) -> None:
        self.controller.on_new_trial()

    def _on_load_optitrack(self) -> None:
        path = filedialog.askopenfilename(
            title="Select OptiTrack CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.load_optitrack_overlay(path)


# ---------------------------------------------------------------------------
# AnalysisPanel
# ---------------------------------------------------------------------------

class AnalysisPanel(tk.Frame):
    """Cross-participant analysis: pick participants, a figure type, and
    (for RMSE) which recording methodologies to compare, then generate one
    of the pt_report_common.py figures and view it inline.

    Runs the actual scoring/plotting on a background thread (it re-reads and
    re-scores every trial CSV for the selected participants each time, which
    takes a few seconds per participant) and polls a queue on the Tk main
    thread to stay responsive -- same pattern as the app's IMU poll thread.
    """

    FIGURE_TYPES = [
        ("full_report", "Full Report (1 participant)"),
        ("comparison", "Comparison (2 participants)"),
        ("rmse", "RMSE Agreement (1 participant)"),
    ]

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._result_queue: queue.Queue = queue.Queue()
        self._current_fig = None
        self._current_canvas = None
        self._last_out_path: Optional[str] = None
        self._participants: dict = {}
        self._build_widgets()

    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        hdr = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 4))
        ws.secondary_button(hdr, "← Mode Select",
                            self.controller.on_back_to_mode_select).pack(
            side="left", padx=(0, 12))
        tk.Label(hdr, text="Analysis & Reports", font=("Segoe UI", 12, "bold"),
                anchor="w", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        # ── Left sidebar: selections ────────────────────────────────────
        side = tk.Frame(self, width=260, bg=ws.PALETTE["BG"])
        side.grid(row=1, column=0, sticky="ns", padx=(12, 6), pady=6)
        side.grid_propagate(False)

        tk.Label(side, text="Participants", font=("Segoe UI", 10, "bold"), anchor="w",
                bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(fill="x", pady=(0, 2))
        list_frame = tk.Frame(side, bg=ws.PALETTE["BG"])
        list_frame.pack(fill="both", expand=False, pady=(0, 4))
        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        self._participant_list = tk.Listbox(
            list_frame, selectmode="extended", exportselection=False,
            height=10, yscrollcommand=scrollbar.set, font=("Segoe UI", 9))
        scrollbar.config(command=self._participant_list.yview)
        self._participant_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        ws.secondary_button(side, "Refresh List", self._refresh_participants).pack(
            fill="x", pady=(0, 12))

        tk.Label(side, text="Figure Type", font=("Segoe UI", 10, "bold"), anchor="w",
                bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(fill="x", pady=(0, 2))
        self._figure_type = tk.StringVar(value="full_report")
        for key, label in self.FIGURE_TYPES:
            tk.Radiobutton(side, text=label, variable=self._figure_type, value=key,
                          font=("Segoe UI", 9), anchor="w",
                          bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"],
                          command=self._on_figure_type_changed).pack(fill="x")

        self._method_frame = tk.LabelFrame(side, text="Methodology (RMSE only)",
                                           font=("Segoe UI", 9, "bold"), padx=6, pady=4,
                                           bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG3"])
        self._method_frame.pack(fill="x", pady=(12, 0))
        self._use_mediapipe = tk.BooleanVar(value=True)
        self._use_imu = tk.BooleanVar(value=True)
        tk.Checkbutton(self._method_frame, text="MediaPipe", variable=self._use_mediapipe,
                      font=("Segoe UI", 9), bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                      selectcolor=ws.PALETTE["SURFACE"],
                      activebackground=ws.PALETTE["PANEL"]).pack(anchor="w")
        tk.Checkbutton(self._method_frame, text="IMU (Viewer)", variable=self._use_imu,
                      font=("Segoe UI", 9), bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                      selectcolor=ws.PALETTE["SURFACE"],
                      activebackground=ws.PALETTE["PANEL"]).pack(anchor="w")
        tk.Label(side, text="(needs MediaPipe/IMU data already\ngenerated for those trials)",
                font=("Segoe UI", 7), fg=ws.PALETTE["FG3"], bg=ws.PALETTE["BG"],
                justify="left").pack(fill="x", pady=(2, 12))
        self._on_figure_type_changed()   # methodology checkboxes start disabled (default is Full Report)

        # Single primary action on this screen (matches the one-primary-
        # button convention used elsewhere, e.g. ModeSelectView/UploadMetaView).
        self.btn_generate = ws.primary_button(side, "Generate", self._on_generate)
        self.btn_generate.config(font=("Segoe UI", 11, "bold"), height=2)
        self.btn_generate.pack(fill="x", pady=(4, 4))

        self.btn_save = ws.secondary_button(side, "Save As...", self._on_save_as)
        self.btn_save.config(state="disabled")
        self.btn_save.pack(fill="x")

        self.status_var = tk.StringVar(value="Pick participant(s), then Generate.")
        tk.Label(side, textvariable=self.status_var, font=("Segoe UI", 8),
                fg=ws.PALETTE["FG2"], bg=ws.PALETTE["BG"],
                wraplength=240, justify="left", anchor="w").pack(fill="x", pady=(12, 0))

        # ── Right: scrollable figure viewer ─────────────────────────────
        viewer_outer = tk.Frame(self, relief="sunken", bd=1, bg=ws.PALETTE["BG"])
        viewer_outer.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        viewer_outer.columnconfigure(0, weight=1)
        viewer_outer.rowconfigure(0, weight=1)

        self._viewer_canvas = tk.Canvas(viewer_outer, bg=ws.PALETTE["SURFACE"],
                                        highlightthickness=0)
        vbar = tk.Scrollbar(viewer_outer, orient="vertical", command=self._viewer_canvas.yview)
        hbar = tk.Scrollbar(viewer_outer, orient="horizontal", command=self._viewer_canvas.xview)
        self._viewer_canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self._viewer_canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        self._viewer_frame = tk.Frame(self._viewer_canvas, bg=ws.PALETTE["SURFACE"])
        self._viewer_window = self._viewer_canvas.create_window(
            (0, 0), window=self._viewer_frame, anchor="nw")
        self._viewer_frame.bind(
            "<Configure>",
            lambda e: self._viewer_canvas.configure(scrollregion=self._viewer_canvas.bbox("all")))

        self._viewer_placeholder = tk.Label(
            self._viewer_frame, text="No figure generated yet.",
            font=("Segoe UI", 11), fg=ws.PALETTE["FG3"], bg=ws.PALETTE["SURFACE"],
            padx=40, pady=40)
        self._viewer_placeholder.pack()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_shown(self) -> None:
        if not self._participants:
            self._refresh_participants()

    def _refresh_participants(self) -> None:
        if not _REPORT_AVAIL:
            self.status_var.set("pt_report_common unavailable — check console for import error.")
            return
        self.status_var.set("Scanning for participants...")
        self.update_idletasks()
        try:
            self._participants = _report.list_participants()
        except Exception as e:
            self.status_var.set(f"Scan failed: {e}")
            return
        self._participant_list.delete(0, "end")
        for pid, info in self._participants.items():
            legs = "/".join(sorted(info["legs"]))
            self._participant_list.insert(
                "end", f"P{pid}  ({legs}, {info['n_trials']} trials, "
                       f"{len(info['conditions'])} condition(s))")
        self.status_var.set(f"{len(self._participants)} participant(s) found. "
                            f"Pick participant(s), then Generate.")

    def _on_figure_type_changed(self) -> None:
        ft = self._figure_type.get()
        state = "normal" if ft == "rmse" else "disabled"
        for child in self._method_frame.winfo_children():
            child.config(state=state)

    def _selected_pids(self) -> list:
        pids = list(self._participants.keys())
        return [pids[i] for i in self._participant_list.curselection()]

    # ------------------------------------------------------------------
    # Generate (background thread + queue poll)
    # ------------------------------------------------------------------
    def _on_generate(self) -> None:
        if not _REPORT_AVAIL:
            messagebox.showerror("Unavailable", "pt_report_common could not be imported.")
            return
        selected = self._selected_pids()
        ft = self._figure_type.get()
        needed = 2 if ft == "comparison" else 1
        if len(selected) != needed:
            messagebox.showinfo(
                "Select Participants",
                f"{'Comparison' if needed == 2 else 'This figure type'} needs exactly "
                f"{needed} participant(s) selected — {len(selected)} selected.")
            return

        self.btn_generate.config(state="disabled")
        self.status_var.set("Working — scoring trials, this can take a bit...")
        methodologies = tuple(m for m, v in
                              (("mediapipe", self._use_mediapipe.get()), ("imu", self._use_imu.get()))
                              if v)
        threading.Thread(target=self._generate_worker, args=(ft, selected, methodologies),
                         daemon=True).start()
        self.after(150, self._poll_result)

    def _generate_worker(self, ft: str, pids: list, methodologies: tuple) -> None:
        # Only the data collection (re-reading and re-scoring every trial CSV
        # for the selected participants) happens here. Figure construction is
        # deliberately left to _poll_result on the Tk main thread: pt_report_common's
        # make_*_figure functions call plt.subplots(), which under the TkAgg
        # backend (active because this module imports tkinter) creates real
        # Tk widgets -- unsafe to do from a background thread.
        try:
            if ft == "full_report":
                data = _report.collect_participant(pids[0])
            elif ft == "comparison":
                pid_a, pid_b = pids
                data = (_report.collect_participant(pid_a), _report.collect_participant(pid_b))
            elif ft == "rmse":
                data = _report.collect_participant(pids[0])
            else:
                raise ValueError(f"Unknown figure type: {ft}")
            self._result_queue.put(("ok", (ft, pids, methodologies, data), None))
        except Exception as e:
            self._result_queue.put(("error", str(e), None))

    def _poll_result(self) -> None:
        try:
            status, payload, _ = self._result_queue.get_nowait()
        except queue.Empty:
            self.after(150, self._poll_result)
            return

        if status == "error":
            self.btn_generate.config(state="normal")
            self.status_var.set(f"Failed: {payload}")
            messagebox.showerror("Generation Failed", payload)
            return

        ft, pids, methodologies, data = payload
        try:
            if ft == "full_report":
                pid = pids[0]
                by_leg_tp, tps = data
                out_path, fig = _report.make_report_figure(
                    f"Participant {pid}", by_leg_tp, tps,
                    f"P{pid}_full_report.png",
                    "Generated from the Pendulastic app's Analysis panel.",
                    save=True, return_fig=True)
            elif ft == "comparison":
                pid_a, pid_b = pids
                (data_a, tp_a), (data_b, tp_b) = data
                out_path, fig = _report.make_comparison_figure(
                    f"P{pid_a}", data_a, tp_a, f"P{pid_b}", data_b, tp_b,
                    f"P{pid_a}_vs_P{pid_b}_comparison.png",
                    save=True, return_fig=True)
            elif ft == "rmse":
                pid = pids[0]
                by_leg_tp, tps = data
                out_path, fig = _report.make_rmse_figure(
                    f"Participant {pid}", by_leg_tp, tps,
                    f"P{pid}_rmse.png", methodologies=methodologies,
                    save=True, return_fig=True)
        except Exception as e:
            self.btn_generate.config(state="normal")
            self.status_var.set(f"Failed: {e}")
            messagebox.showerror("Generation Failed", str(e))
            return

        self.btn_generate.config(state="normal")
        self._last_out_path = out_path
        self._show_figure(fig)
        self.status_var.set(f"Done. Saved to:\n{out_path}")
        self.btn_save.config(state="normal")

    def _show_figure(self, fig) -> None:
        if not _MPL_AVAIL:
            return
        if self._current_canvas is not None:
            self._current_canvas.get_tk_widget().destroy()
        if self._current_fig is not None:
            try:
                import matplotlib.pyplot as _plt
                _plt.close(self._current_fig)
            except Exception:
                pass
        self._viewer_placeholder.pack_forget()

        self._current_fig = fig
        self._current_canvas = FigureCanvasTkAgg(fig, master=self._viewer_frame)
        self._current_canvas.draw()
        self._current_canvas.get_tk_widget().pack()

    def _on_save_as(self) -> None:
        if not self._last_out_path or not os.path.exists(self._last_out_path):
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".png",
            initialfile=os.path.basename(self._last_out_path),
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")])
        if not dest:
            return
        try:
            import shutil
            shutil.copyfile(self._last_out_path, dest)
            self.status_var.set(f"Saved copy to:\n{dest}")
        except Exception as e:
            messagebox.showerror("Save Failed", str(e))


class MasEntryPanel(tk.Frame):
    """MAS score entry form + live PT-score-vs-MAS validation dashboard.
    controller: App instance -- receives on_back_to_mode_select()."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._build_widgets()

    def _build_widgets(self) -> None:
        import datetime as _datetime
        pad = {"padx": 12, "pady": 5}
        self.configure(bg=ws.PALETTE["BG"])

        hdr = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr.pack(fill="x", padx=12, pady=(16, 4))
        ws.secondary_button(
            hdr, "← Mode Select", self.controller.on_back_to_mode_select
        ).pack(side="left", padx=(0, 8))
        tk.Label(hdr, text="Pendulastic — MAS Score Entry",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)

        form = tk.Frame(self, bg=ws.PALETTE["BG"])
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        tk.Label(form, text="Participant ID:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=0, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        tk.Entry(form, textvariable=self.pid_var, width=22).grid(
            row=0, column=1, sticky="w", **pad)

        tk.Label(form, text="Leg:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=1, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Left")
        leg_f = tk.Frame(form, bg=ws.PALETTE["BG"])
        leg_f.grid(row=1, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left", variable=self.leg_var, value="Left",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)

        tk.Label(form, text="Stronger Leg:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=2, column=0, sticky="e", **pad)
        self.stronger_leg_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.stronger_leg_var, width=19,
                    state="readonly",
                    values=list(_mas_validation.STRONGER_LEG_OPTIONS)).grid(
            row=2, column=1, sticky="w", **pad)

        tk.Label(form, text="Condition:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=3, column=0, sticky="e", **pad)
        self.condition_var = tk.StringVar()
        tk.Entry(form, textvariable=self.condition_var, width=22).grid(
            row=3, column=1, sticky="w", **pad)

        tk.Label(form, text="Diagnosis:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=4, column=0, sticky="e", **pad)
        self.diagnosis_var = tk.StringVar()
        tk.Entry(form, textvariable=self.diagnosis_var, width=22).grid(
            row=4, column=1, sticky="w", **pad)

        tk.Label(form, text="MAS Grade:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=5, column=0, sticky="e", **pad)
        self.mas_grade_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_grade_var, width=19,
                    state="readonly",
                    values=list(_mas_validation.MAS_ORDER)).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed By:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=6, column=0, sticky="e", **pad)
        self.assessed_by_var = tk.StringVar()
        tk.Entry(form, textvariable=self.assessed_by_var, width=22).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed Date:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=7, column=0, sticky="e", **pad)
        self.assessed_date_var = tk.StringVar(
            value=_datetime.date.today().isoformat())
        tk.Entry(form, textvariable=self.assessed_date_var, width=22).grid(
            row=7, column=1, sticky="w", **pad)

        tk.Label(form, text="Notes:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=8, column=0, sticky="ne", **pad)
        self.notes_text = tk.Text(form, height=3, width=22, wrap="word",
                                  bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG"])
        self.notes_text.grid(row=8, column=1, sticky="w", **pad)

        # Single feedback channel for the form: errors in amber, save
        # confirmations in green (see _set_feedback).
        self.error_var = tk.StringVar(value="")
        self.error_label = tk.Label(self, textvariable=self.error_var,
                                    fg=_ERROR_FG, bg=ws.PALETTE["BG"])
        self.error_label.pack(fill="x", padx=12, pady=(0, 4))

        ws.primary_button(self, "Save", self._on_save_clicked).pack(pady=(0, 8))

        status_frame = tk.Frame(self, bg=ws.PALETTE["BG"])
        status_frame.pack(fill="x", padx=12, pady=(4, 8))
        self.status_text = tk.Text(status_frame, height=4, wrap="word",
                                   state="disabled", bg=ws.PALETTE["SURFACE"],
                                   fg=ws.PALETTE["FG"])
        status_scroll = tk.Scrollbar(status_frame, command=self.status_text.yview)
        self.status_text.configure(yscrollcommand=status_scroll.set)
        self.status_text.pack(side="left", fill="x", expand=True)
        status_scroll.pack(side="right", fill="y")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)

        self._current_canvas = None
        self._current_fig = None
        self._last_valid: list = []
        self._last_stats = None

        self.canvas_frame = tk.Frame(self, bg=ws.PALETTE["SURFACE"])
        self.canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.canvas_placeholder = tk.Label(
            self.canvas_frame,
            text="No MAS-scored trials with matching trial data yet",
            bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG2"])
        self.canvas_placeholder.pack(pady=40)

        self.export_btn = ws.secondary_button(self, "Export", self._on_export_clicked)
        self.export_btn.config(state="disabled")
        self.export_btn.pack(pady=(0, 12))

    def refresh(self) -> None:
        try:
            rows = _mas_validation.load_mas_scores(_mas_validation.MAS_CSV)
        except (FileNotFoundError, OSError):
            rows = []
        paired = _mas_validation.pair_pt_and_mas(
            rows, _mas_validation._pt_lookup_factory())
        valid = [p for p in paired if "_skip_reason" not in p]
        skipped = [p for p in paired if "_skip_reason" in p]

        self.status_text.config(state="normal")
        self.status_text.delete("1.0", "end")
        for row in skipped:
            self.status_text.insert(
                "end",
                f"P{row.get('participant')} {row.get('leg')}/{row.get('condition')}: "
                f"{row['_skip_reason']}\n")
        self.status_text.config(state="disabled")

        if not valid:
            self._last_valid = []
            self._last_stats = None
            self._show_placeholder()
            self.export_btn.config(state="disabled")
            return

        stats = _mas_validation.compute_validation_stats(valid)
        fig = _mas_validation.build_validation_figure(valid, stats)
        self._last_valid = valid
        self._last_stats = stats
        self._show_figure(fig)
        self.export_btn.config(state="normal")

    def _set_feedback(self, text: str, ok: bool = False) -> None:
        """Both save errors and save confirmations land in error_var -- it's
        the form's one feedback line. Only the color distinguishes them."""
        self.error_label.config(fg=_SUCCESS_FG if ok else _ERROR_FG)
        self.error_var.set(text)

    def _on_save_clicked(self) -> None:
        participant = self.pid_var.get().strip()
        mas_grade = self.mas_grade_var.get().strip()
        if not participant or not mas_grade:
            self._set_feedback("Participant ID and MAS grade are required.")
            return
        row = {
            "participant": participant,
            "leg": self.leg_var.get().lower(),
            "condition": self.condition_var.get().strip(),
            "diagnosis": self.diagnosis_var.get().strip(),
            "mas_grade": mas_grade,
            "assessed_by": self.assessed_by_var.get().strip(),
            "assessed_date": self.assessed_date_var.get().strip(),
        }
        try:
            _mas_validation.append_mas_score(row)
        except ValueError as e:
            self._set_feedback(str(e))
            return
        except Exception as e:
            self._set_feedback(f"Could not save: {e}")
            return
        # The form is deliberately not cleared (batch entry of both legs keeps
        # participant/condition/date), but with no confirmation a clinician
        # unsure the click registered would click Save again and append an
        # identical duplicate row, biasing the Spearman/kappa stats. Confirm,
        # and clear the one field that must change between consecutive rows so
        # a resubmit takes a deliberate re-selection.
        self._set_feedback(f"Saved {participant} {row['leg']} / {mas_grade}.", ok=True)
        self.mas_grade_var.set("")
        self.refresh()

    def _on_export_clicked(self) -> None:
        if not self._last_valid or self._last_stats is None:
            return
        # Real file I/O (os.makedirs + writes) -- a read-only dir, a full disk,
        # or the PNG being open in another program on Windows all raise here.
        try:
            _mas_validation.write_stats_csv(self._last_stats, _mas_validation.STATS_CSV)
            _mas_validation.save_validation_figure(
                self._last_valid, self._last_stats, _mas_validation.FIGURE_PNG)
        except Exception as e:
            messagebox.showerror("Export Failed", str(e))
            return
        messagebox.showinfo("Exported", f"Saved to:\n{_mas_validation.OUT_DIR}")

    def _show_placeholder(self) -> None:
        if self._current_canvas is not None:
            self._current_canvas.get_tk_widget().destroy()
            self._current_canvas = None
        if self._current_fig is not None:
            try:
                import matplotlib.pyplot as _plt
                _plt.close(self._current_fig)
            except Exception:
                pass
            self._current_fig = None
        self.canvas_placeholder.pack(pady=40)

    def _show_figure(self, fig) -> None:
        if not _MPL_AVAIL:
            return
        self.canvas_placeholder.pack_forget()
        if self._current_canvas is not None:
            self._current_canvas.get_tk_widget().destroy()
        if self._current_fig is not None:
            try:
                import matplotlib.pyplot as _plt
                _plt.close(self._current_fig)
            except Exception:
                pass
        self._current_fig = fig
        self._current_canvas = FigureCanvasTkAgg(fig, master=self.canvas_frame)
        self._current_canvas.draw()
        self._current_canvas.get_tk_widget().pack(fill="both", expand=True)


# ---------------------------------------------------------------------------
# App  (thin host)
# ---------------------------------------------------------------------------

class App(tk.Tk):
    """
    Owns: IMU server lifecycle, UDP port 8888 lifecycle, panel switching,
    IMU poll thread -> queue -> sparkline tick.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("Pendulastic")
        self.geometry("900x740")
        self.resizable(True, True)
        self.minsize(700, 680)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._state           = "mode_select"
        self._engine: Optional[BiomechanicalEngine] = None
        self._imu_queue: queue.Queue   = queue.Queue()
        self._imu_poll_stop            = threading.Event()
        self._imu_poll_thread: Optional[threading.Thread] = None
        self._active_sources: list     = []
        self._rec_angles:     dict     = {}   # {"imu": [...], "rgb": [...]}
        self._rec_timestamps: dict     = {}   # {"imu": [...]}
        self._pending_review: dict     = {}
        self._video_path:     str      = ""
        self._preview_queue:  queue.Queue = queue.Queue(maxsize=1)
        self._pose_estimator               = None
        self._camera = (
            CameraSession(on_frame=self._on_camera_frame, on_status=self._on_camera_status)
            if _CV2_AVAIL else None
        )
        self._known_cameras: list = []
        self._calib_was_stable:  bool = False   # edge-trigger state for auto-tare
        self._calib_ever_stable: bool = False   # True once calibrated this countdown

        # Start IMU WebSocket server (port 5000) once for this process
        if _IMU_AVAIL:
            try:
                _imu.start()
            except Exception:
                pass

        # Registers the dark "Workbench.*" ttk styles the embedded panels
        # opt into. It does not switch this root's base ttk theme, so the
        # other panels' ttk.Combobox/ttk.Separator widgets are untouched.
        # Kept unconditional (not gated on Workbench availability) since
        # workbench_style itself has zero non-tkinter deps.
        ws.apply_ttk_theme(self)
        self.configure(bg=ws.PALETTE["BG"])

        self._mode_select = ModeSelectView(self, controller=self)
        self._upload_meta = UploadMetaView(self, controller=self)
        self._acq  = AcquisitionPanel(self, controller=self)
        self._post = PostProcessingPanel(self, controller=self)
        self._analysis = AnalysisPanel(self, controller=self)

        self._workbench_trial_meta: dict = {}
        self._workbench_imu_reference: list = []
        self._workbench_raw_diagnostics: Optional[dict] = None
        self._workbench_status_var = tk.StringVar(value="")
        if _WORKBENCH_AVAIL:
            self._workbench_load = TrialLoadPanel(self, controller=self)
            self._workbench_view = WorkbenchView(self, controller=self)
            self._dashboard_view = DashboardView(self, controller=self)
            tk.Label(self, textvariable=self._workbench_status_var, anchor="w",
                     bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).pack(
                side="bottom", fill="x", padx=8, pady=2)

        # Both flags: mas_validation's own deps (scipy/sklearn) can be present
        # while TkAgg/FigureCanvasTkAgg is not, and the panel embeds a canvas.
        if _MAS_VALIDATION_AVAIL and _MPL_AVAIL:
            self._mas_entry = MasEntryPanel(self, controller=self)

        self._mode_select.pack(fill="both", expand=True)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._tick()

    # ------------------------------------------------------------------
    # Controller interface (called by AcquisitionPanel / PostProcessingPanel)
    # ------------------------------------------------------------------
    def on_start(self) -> None:
        meta    = self._acq.get_metadata()
        sources = meta["sources"]
        self._active_sources = list(sources)

        # Pick primary engine for live sparkline (IMU > RGB > OptiTrack priority)
        if "imu" in sources:
            self._engine = BiomechanicalEngine("imu")
        elif "rgb" in sources:
            self._engine = BiomechanicalEngine("rgb")
        else:
            self._engine = BiomechanicalEngine("optitrack")

        self._rec_angles     = {}
        self._rec_timestamps = {}
        self._acq.clear_telemetry()

        # video_file is a fully standalone path — process it immediately and bypass
        # the live recording loop so no other co-selected sources are started.
        if "video_file" in sources:
            self._pending_review = {}
            self._start_video_file_processing(meta)
            return   # video_file is standalone — no live recording

        for src in sources:
            if src == "imu":
                self._start_imu_recording(meta)
            elif src == "rgb":
                self._start_rgb_recording(meta)
            elif src == "optitrack":
                self._start_optitrack_recording(meta)

        self._state = "recording"
        self._acq.enter_recording()

    def on_stop(self) -> None:
        # Clear the viewer window's "● REC" overlay immediately -- recording
        # has actually stopped even though the panel may still spend a
        # while in "processing"/"review" before enter_idle() next runs.
        self._acq.set_viewer_overlay_text("")
        # Stop IMU poll thread unconditionally
        self._imu_poll_stop.set()
        if self._imu_poll_thread:
            self._imu_poll_thread.join(timeout=1.0)
        self._imu_poll_stop.clear()
        self._imu_poll_thread = None

        # Close pose estimator if it was active
        if self._pose_estimator is not None:
            try:
                self._pose_estimator.close()
            except Exception:
                pass
            self._pose_estimator = None

        meta           = self._acq.get_metadata()
        source_angles: dict = {}
        pending_rgb    = False
        imu_raw_log_path: Optional[str] = None
        imu_csv_path:     Optional[str] = None
        fn_imu:           Optional[str] = None

        for src in self._active_sources:
            if src == "imu":
                angles_imu = self._rec_angles.get("imu", [])
                ts_imu     = self._rec_timestamps.get("imu") or None
                fn_imu = DataManager.build_filename(
                    meta["pid"], meta["leg"], meta["ms_status"], meta["trial"],
                    source="imu")
                imu_csv_path = DataManager.save_trial(fn_imu, angles_imu, meta,
                                       timestamps=ts_imu, source="imu")
                source_angles["imu"] = angles_imu
                if _IMU_AVAIL:
                    imu_raw_log_path = _imu.stop_raw_log()

            elif src == "rgb":
                self._stop_rgb_recording()
                pending_rgb = True

            elif src == "optitrack":
                if _MOTIVE_AVAIL:
                    try:
                        _motive.stop_local_motive()
                    except Exception:
                        pass
                source_angles["optitrack"] = []   # angles loaded from CSV in review panel

        pending_imu_tune = (
            imu_raw_log_path is not None and not pending_rgb and _tuner is not None)

        if pending_rgb:
            self._state = "processing"
            self._acq.enter_processing()
            self._pending_review = source_angles  # preserve already-done sources
            threading.Thread(
                target=self._run_rgb_processing,
                args=(meta,), daemon=True,
            ).start()
        elif pending_imu_tune:
            self._state = "processing"
            self._acq.enter_processing()
            self._pending_review = source_angles
            threading.Thread(
                target=self._run_imu_tuning,
                args=(imu_raw_log_path, imu_csv_path, fn_imu, meta), daemon=True,
            ).start()
        else:
            self._transition_to_review(source_angles, meta, from_recording=True)

    def on_new_trial(self) -> None:
        self._acq.increment_trial()
        self._post.pack_forget()
        self._mode_select.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._acq.enter_idle()
        self._state = "idle"

    def on_source_changed(self, sources: list) -> None:
        """Called by AcquisitionPanel when any source checkbox changes."""
        self._active_sources = list(sources)

    def on_rescan_cameras(self) -> None:
        if self._state == "recording":
            return
        if self._camera is None:
            return
        self._known_cameras = self._camera.rescan()
        self._acq.set_camera_list(self._known_cameras)
        if self._known_cameras:
            label = self._acq.cam_var.get()
            cam = next((c for c in self._known_cameras if c["label"] == label),
                       self._known_cameras[0])
            self._camera.open(cam)
        else:
            self._acq.set_camera_live(False)

    def on_camera_selected(self, label: str) -> None:
        if self._state == "recording":
            return
        if self._camera is None:
            return
        cam = next((c for c in self._known_cameras if c["label"] == label), None)
        if cam is None:
            return
        if self._camera.active is not None and self._camera.active["label"] == label:
            return   # already using this camera
        self._camera.open(cam)

    def on_camera_disabled(self) -> None:
        if self._camera is not None:
            self._camera.close()
        self._acq.set_camera_live(False)

    def _on_camera_frame(self, frame_bgr) -> None:
        """Runs on CameraSession's background read thread. Applies the same
        pose-overlay logic _rgb_record_worker used to apply during recording;
        passes the frame through unchanged otherwise. Never touches Tkinter —
        hands off via the existing preview queue."""
        preview = frame_bgr
        if self._pose_estimator is not None and _mp_draw is not None:
            try:
                preview = frame_bgr.copy()
                rgb_frame = _cv2.cvtColor(preview, _cv2.COLOR_BGR2RGB)
                results = self._pose_estimator.process(rgb_frame)
                if results.pose_landmarks:
                    _mp_draw.draw_landmarks(
                        preview, results.pose_landmarks, _mp_pose.POSE_CONNECTIONS,
                        landmark_drawing_spec=_mp_styles.get_default_pose_landmarks_style(),
                    )
            except Exception:
                pass
        try:
            self._preview_queue.put_nowait(preview)
        except queue.Full:
            pass

    def _on_camera_status(self, msg: str) -> None:
        """Runs on CameraSession's background read thread — marshal to Tk."""
        self.after(0, lambda m=msg: self._acq.set_camera_live(m == "live"))

    def on_countdown_start(self) -> None:
        """Called by AcquisitionPanel at the start of each countdown; resets
        the auto-tare stability tracking for this fresh countdown window."""
        self._calib_was_stable = False
        self._calib_ever_stable = False

    def is_imu_calibrated(self) -> bool:
        """True if calibration isn't required (imu not an active source) or
        has already succeeded at least once this countdown."""
        if "imu" not in self._active_sources:
            return True
        return self._calib_ever_stable

    # ------------------------------------------------------------------
    # Mode-select routing
    # ------------------------------------------------------------------
    def _enter_live_mode(self) -> None:
        self._mode_select.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._state = "idle"
        # RGB defaults to checked (a routine clinical source), and
        # AcquisitionPanel's own build step only syncs the camera frame's
        # visibility -- it never calls the controller, since self._acq
        # doesn't exist yet the moment _build_widgets runs. Do the
        # equivalent of a user's "Rescan" click here, once self._acq is
        # guaranteed to exist, so the camera list actually populates when
        # the Live Recording screen is shown with RGB active -- not just
        # on a manual Rescan click or an explicit checkbox toggle.
        if "rgb" in self._acq.get_active_sources():
            # rescan()/enumerate_cameras() blocks for the full multi-second
            # probe (see camera_utils.py) -- paint the newly-packed panel
            # and a "scanning" status first so the clinician sees the
            # screen (not a freeze) while the probe runs. Matches the
            # status_var.set() + update_idletasks() pattern master_app.py's
            # rescan_cameras() uses around the same blocking call.
            self._acq.status_var.set("Scanning for camera…")
            self.update_idletasks()
            self.on_rescan_cameras()
            self._acq.status_var.set("Idle — ready to record.")

    def _enter_upload_mode(self) -> None:
        path = filedialog.askopenfilename(
            title="Select file to analyze",
            filetypes=[
                ("Video / CSV", "*.mp4 *.avi *.mov *.mkv *.csv"),
                ("All files", "*.*"),
            ])
        if not path:
            return
        self._mode_select.pack_forget()
        self._upload_meta.set_file(path)
        self._upload_meta.status_var.set("Ready")
        self._upload_meta.set_processing(False)
        self._upload_meta.pack(fill="both", expand=True)
        self._state = "upload_meta"

    def _enter_workbench_mode(self) -> None:
        if not _WORKBENCH_AVAIL:
            messagebox.showinfo(
                "Workbench Unavailable",
                "The Multi-Modal Comparison workbench could not be loaded in this "
                "environment (a required dependency is missing).")
            return
        self._mode_select.pack_forget()
        self._workbench_load.pack(fill="both", expand=True)
        self._state = "workbench_load"

    def _enter_analysis_mode(self) -> None:
        self._mode_select.pack_forget()
        self._analysis.pack(fill="both", expand=True)
        self._state = "analysis"
        self._analysis.on_shown()

    def _enter_mas_entry_mode(self) -> None:
        if not (_MAS_VALIDATION_AVAIL and _MPL_AVAIL):
            messagebox.showinfo(
                "MAS Entry Unavailable",
                "MAS score entry could not be loaded in this environment "
                "(a required dependency is missing).")
            return
        self._mode_select.pack_forget()
        self._mas_entry.pack(fill="both", expand=True)
        self._state = "mas_entry"
        self._mas_entry.refresh()

    def get_trial_meta(self) -> dict:
        return dict(self._workbench_trial_meta)

    def on_load_trial(self, selection: dict) -> None:
        """Loads whichever of the three modalities were selected (design
        spec Section 2: 2-of-3 is valid) and switches to WorkbenchView.
        Video HPE model inference runs on a background thread since it's
        the slow step (design spec Section 3); IMU/OptiTrack loading is
        fast enough to run inline. IMU input is either a single JSONL raw
        log or four independently-validated split-CSV components (design
        spec 2026-08-04-sequential-csv-intake)."""
        traces = {}
        imu_format = selection.get("imu_format", "jsonl")
        self._workbench_trial_meta = {
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "participant_id": selection["participant_id"],
            "session_date": selection["session_date"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        self._workbench_raw_diagnostics = None
        ft_ratio = None
        method_override = None
        if selection["femur_length_cm"] and selection["tibia_length_cm"]:
            # Both limb lengths supplied means the researcher wants the
            # personalized-ratio Ockendon path validated -- force the
            # method rather than silently no-op if the persisted config's
            # method is "relative".
            ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
            method_override = "ockendon_flipped"

        if imu_format == "split_csv":
            components = selection.get("imu_components", {})
            if all(components.get(k, {}).get("ok") for k in ("accel", "gyro", "mag", "imu")):
                try:
                    t, angle, imu_reference = _wb_engine.load_imu_trial_from_components(
                        components, ft_ratio=ft_ratio, method=method_override)
                    traces["imu"] = (t, angle)
                    self._workbench_trial_meta["imu_paths"] = {
                        k: components.get(k, {}).get("path")
                        for k in ("accel", "gyro", "mag", "imu")}
                    # imu_reference (the full parsed raw-IMU row list) is
                    # kept off self._workbench_trial_meta so it never flows
                    # into export_session()'s output -- it can be megabytes
                    # for a real trial. Stored separately for in-memory
                    # cross-check use only.
                    self._workbench_imu_reference = imu_reference
                except Exception as e:
                    messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")
        elif selection["imu_path"]:
            self._workbench_trial_meta["imu_path"] = selection["imu_path"]
            try:
                t, angle = _wb_engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

            try:
                self._workbench_raw_diagnostics = _wb_engine.compute_raw_sensor_diagnostics(
                    selection["imu_path"])
            except Exception:
                pass   # supplementary cross-check only -- never blocks the trial load

        if selection["optitrack_path"]:
            try:
                t, angle, method = _wb_engine.load_optitrack_trial(selection["optitrack_path"])
                traces["optitrack"] = (t, angle)
                self._workbench_trial_meta["optitrack_method"] = method
            except Exception as e:
                messagebox.showerror("OptiTrack load error", f"{type(e).__name__}: {e}")

        self._workbench_load.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)
        self._workbench_view.set_raw_diagnostics(self._workbench_raw_diagnostics)

        if selection["video_path"]:
            self._workbench_view.load_video(selection["video_path"])
            if selection["models"]:
                self._load_workbench_video_models_async(
                    selection["video_path"], selection["models"], traces)

    def _load_workbench_video_models_async(self, video_path: str, models: list,
                                           traces: dict) -> None:
        """Runs load_video_trial on a background thread (design spec
        Section 3: full-video pose inference x N models is the slow step)
        and surfaces progress via progress_cb -- Tkinter widgets may only
        be touched from the main thread, so both the progress update and
        the final traces update are marshalled through self.after(0, ...)."""
        self._workbench_status_var.set(f"Running {len(models)} HPE model(s)... 0%")

        def on_progress(fraction: float) -> None:
            self.after(0, lambda: self._workbench_status_var.set(
                f"Running {len(models)} HPE model(s)... {fraction * 100:.0f}%"))

        def worker():
            results = _wb_engine.load_video_trial(video_path, models, progress_cb=on_progress)
            def apply():
                for name, result in results.items():
                    if isinstance(result, dict) and "error" in result:
                        print(f"[warn] model {name!r} failed: {result['error']}")
                        continue
                    traces[name] = result
                self._workbench_view.set_traces(traces)
                self._workbench_status_var.set("")
            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def on_workbench_load_another(self) -> None:
        self._workbench_view.pack_forget()
        self._workbench_load.pack(fill="both", expand=True)

    def on_view_dashboard(self) -> None:
        if not _WORKBENCH_AVAIL:
            return
        self._workbench_load.pack_forget()
        self._workbench_view.pack_forget()
        self._dashboard_view.refresh_participants()
        self._dashboard_view.pack(fill="both", expand=True)

    def on_dashboard_back(self) -> None:
        if not _WORKBENCH_AVAIL:
            return
        self._dashboard_view.pack_forget()
        self._workbench_load.pack(fill="both", expand=True)

    def _upload_back_to_select(self) -> None:
        if self._state == "upload_processing":
            return
        self._upload_meta.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state = "mode_select"

    def on_back_to_mode_select(self) -> None:
        self._acq.pack_forget()
        self._post.pack_forget()
        self._upload_meta.pack_forget()
        self._analysis.pack_forget()
        if _WORKBENCH_AVAIL:
            self._workbench_load.pack_forget()
            self._workbench_view.pack_forget()
            self._dashboard_view.pack_forget()
        if _MAS_VALIDATION_AVAIL and _MPL_AVAIL:
            self._mas_entry.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state        = "mode_select"
        self._active_sources  = []
        self._rec_angles      = {}
        self._rec_timestamps  = {}
        self._pending_review  = {}

    def _start_upload_analysis(self) -> None:
        meta = self._upload_meta.get_metadata()
        if not meta.get("pid", "").strip():
            messagebox.showerror("Metadata", "Participant ID cannot be empty.")
            return
        path = self._upload_meta._file_path
        if not path:
            messagebox.showerror("Metadata", "No file selected.")
            return
        self._state = "upload_processing"
        self._upload_meta.set_processing(True)
        self._upload_meta.status_var.set("Processing...")
        ext = os.path.splitext(path)[1].lower()
        if ext in (".mp4", ".avi", ".mov", ".mkv"):
            threading.Thread(
                target=self._run_video_file_hpe,
                args=(path, meta),
                kwargs={"progress_target": self._upload_meta.status_var},
                daemon=True,
            ).start()
        else:
            threading.Thread(
                target=self._run_csv_analysis,
                args=(path, meta),
                daemon=True,
            ).start()

    def _run_csv_analysis(self, path: str, meta: dict) -> None:
        import csv as _csv_mod
        target = self._upload_meta.status_var
        t_vals: list = []
        angle_vals: list = []
        try:
            with open(path, newline="", encoding="utf-8") as f:
                lines = (row for row in f if not row.startswith("#"))
                reader = _csv_mod.DictReader(lines)
                for row in reader:
                    try:
                        t_key = next(
                            (k for k in ("time_s", "t_rel") if k in row), None)
                        a_key = next(
                            (k for k in ("knee_angle_deg", "angle") if k in row),
                            None)
                        if t_key is None or a_key is None:
                            continue
                        t_vals.append(float(row[t_key]))
                        angle_vals.append(float(row[a_key]))
                    except (KeyError, ValueError):
                        pass
        except OSError as e:
            def _err_os(msg=str(e)):
                target.set(f"Error reading file: {msg}")
                self._upload_meta.set_processing(False)
                self._state = "upload_meta"
            self.after(0, _err_os)
            return
        if not angle_vals:
            def _err_empty():
                target.set("Error: no valid angle data found in CSV")
                self._upload_meta.set_processing(False)
                self._state = "upload_meta"
            self.after(0, _err_empty)
            return
        source_angles = {"upload_csv": angle_vals}
        fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"],
            meta["trial"], source="upload_csv")
        DataManager.save_trial(fn, angle_vals, meta,
                               timestamps=t_vals, source="upload_csv")
        self.after(0, lambda: self._transition_to_review(source_angles, meta))

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------
    def _start_imu_recording(self, meta: dict) -> None:
        # IMU server runs continuously; data flows via queue -> _tick -> _rec_angles["imu"]
        # No start_recording() call needed — we own the CSV via DataManager.save_trial.
        if _IMU_AVAIL:
            fn_imu = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"],
                source="imu")
            raw_path = os.path.join(
                DataManager.DATA_DIR, fn_imu.replace(".csv", "_raw.jsonl"))
            try:
                os.makedirs(DataManager.DATA_DIR, exist_ok=True)
                _imu.start_raw_log(raw_path)
            except OSError as e:
                # Raw logging is purely a diagnostic/auxiliary feature that
                # feeds the auto-tuning loop -- it must never be able to block
                # the core acquisition it's attached to. Warn and continue;
                # this trial simply won't be eligible for auto-tuning.
                messagebox.showwarning(
                    "IMU Raw Log",
                    f"Could not open raw IMU log:\n{type(e).__name__}: {e}\n\n"
                    "Recording will continue without a raw log (this trial "
                    "will not be eligible for auto-tuning).")
        self._imu_poll_stop.clear()
        self._imu_poll_thread = threading.Thread(
            target=self._imu_poll_worker, daemon=True)
        self._imu_poll_thread.start()

    def _imu_poll_worker(self) -> None:
        """Put (t, angle_deg) into _imu_queue at ~20 Hz."""
        import imu_calibration_config as _cfgmod
        _EMA_ALPHA = _cfgmod.load_config()["ema_alpha"]
        _ema: Optional[float] = None
        while not self._imu_poll_stop.is_set():
            if self._engine:
                angle = self._engine.get_live_angle()
                if math.isfinite(angle):
                    _ema = (angle if _ema is None
                            else _EMA_ALPHA * angle + (1.0 - _EMA_ALPHA) * _ema)
                    self._imu_queue.put((time.time(), _ema))
                else:
                    _ema = None   # reset on NaN (pre-zero or disconnected)
                    self._imu_queue.put((time.time(), angle))
            time.sleep(0.05)

    def _start_video_file_processing(self, meta: dict) -> None:
        path = self._acq.get_video_file_path()
        if not path:
            messagebox.showerror("Video File", "No video file selected.")
            return
        self._state = "processing"
        self._acq.enter_processing()
        threading.Thread(
            target=self._run_video_file_hpe,
            args=(path, meta), daemon=True,
        ).start()

    def _run_video_file_hpe(self, path: str, meta: dict,
                             progress_target: Optional[tk.StringVar] = None) -> None:
        target = progress_target or self._acq.status_var
        def progress(pct: float) -> None:
            self.after(0, lambda p=pct: target.set(
                f"HPE processing: {int(p * 100)}%"))

        try:
            leg    = meta.get("leg", "right").lower()
            engine = BiomechanicalEngine("rgb")
            angles = engine.run_offline_track(path, progress, leg=leg)

            fn = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"],
                meta["trial"], source="video_file")
            DataManager.save_trial(fn, angles, meta, fps=30.0, source="video_file")

            source_angles = {"video_file": angles}
            self.after(0, lambda: self._transition_to_review(source_angles, meta))
        except Exception as exc:
            if progress_target is not None:
                def _err_video(msg=str(exc)):
                    target.set(f"Error processing video: {msg}")
                    self._upload_meta.set_processing(False)
                    self._state = "upload_meta"
                self.after(0, _err_video)

    def _start_rgb_recording(self, meta: dict) -> None:
        if not _CV2_AVAIL:
            messagebox.showerror("RGB", "OpenCV (cv2) is not installed.")
            if "rgb" in self._active_sources:
                self._active_sources.remove("rgb")
            self._video_path = ""
            return
        if self._camera is None or self._camera.active is None \
                or self._camera.frame_size is None:
            messagebox.showerror(
                "RGB", "No camera selected. Click Rescan and pick a camera first.")
            if "rgb" in self._active_sources:
                self._active_sources.remove("rgb")
            self._video_path = ""
            return
        fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        os.makedirs(DataManager.DATA_DIR, exist_ok=True)
        self._video_path = os.path.join(
            DataManager.DATA_DIR, fn.replace(".csv", ".avi"))
        w, h = self._camera.frame_size
        self._rgb_writer = _cv2.VideoWriter(
            self._video_path, _cv2.VideoWriter_fourcc(*"XVID"), 30.0, (w, h))

        # Drain any stale frames from a previous recording
        while not self._preview_queue.empty():
            try:
                self._preview_queue.get_nowait()
            except queue.Empty:
                break

        # Init lightweight pose estimator for live overlay (guarded)
        if _mp_pose is not None:
            self._pose_estimator = _mp_pose.Pose(
                model_complexity=0,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
        else:
            self._pose_estimator = None

        self._camera.attach_writer(self._rgb_writer)

    def _stop_rgb_recording(self) -> None:
        writer = self._camera.detach_writer() if self._camera is not None else None
        if writer is not None:
            writer.release()
        self._rgb_writer = None

    def _run_imu_tuning(self, raw_log_path: str, csv_path: str,
                        csv_filename: str, meta: dict) -> None:
        """Load this trial's raw IMU log, run the grid search, and — only if
        a passing configuration is found — rewrite the trial's saved CSV and
        feed the tuned series into REVIEW. Must never raise: any failure
        falls back to the originally-recorded series so tuning can never
        block a clinician from seeing trial data."""
        source_angles = dict(self._pending_review)
        try:
            raw_samples = []
            with open(raw_log_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw_samples.append(json.loads(line))
                    except ValueError:
                        continue

            if raw_samples and _tuner is not None:
                best = _tuner.tune_and_persist(raw_samples, source_trial=csv_filename)
                if best["passes"]:
                    t, angle = _tuner.replay_trial(raw_samples, best["params"])
                    # replay_trial's own contract (see its docstring) guarantees
                    # angle[0] is always NaN -- the first tick always precedes any
                    # processed sample. Callers must finite-filter before reducing;
                    # score_waveform does this internally, but we save/display the
                    # raw series here, so we must filter explicitly ourselves or a
                    # literal "nan" gets written into the persisted trial CSV.
                    finite_mask = np.isfinite(angle)
                    t, angle = t[finite_mask], angle[finite_mask]
                    tuned_angles = [float(a) for a in angle]
                    DataManager.save_trial(
                        csv_filename, tuned_angles, meta,
                        timestamps=[float(x) for x in t], source="imu")
                    source_angles["imu"] = tuned_angles

                    # Note on config staleness: ema_alpha applies starting next
                    # trial (the poll worker reloads config fresh each start),
                    # but beta/gravity_seed/flex_axis_capture are read once at
                    # pendulastic_imu_server.py's import time and only take
                    # effect after that process is restarted. The very next
                    # trial therefore runs a hybrid parameter set that was never
                    # actually scored as a combination by the grid search --
                    # make that visible instead of silent.
                    print("IMU config updated: EMA smoothing applies next trial; "
                          "AHRS beta/gravity-seed/flex-axis-capture require an "
                          "IMU server restart to take effect.")
        except Exception:
            # Broad on purpose: this runs in an unsupervised daemon thread,
            # and imu_calibration_tuner.py has no internal exception handling
            # of its own -- a malformed-but-JSON-parseable raw sample (e.g.
            # missing "role", or "v" not a 3-element list) could raise
            # TypeError/IndexError from deep inside replay_trial. An uncaught
            # exception here would kill the thread silently, the self.after
            # transition below would never fire, and the app would sit in
            # "processing" forever -- a direct violation of "tuning must
            # never block the clinician from seeing trial data."
            pass   # fall back to the originally-recorded series
        self.after(0, lambda: self._transition_to_review(
            source_angles, meta, from_recording=True))

    def _run_rgb_processing(self, meta: dict) -> None:
        def progress(pct: float) -> None:
            self.after(0, lambda p=pct: self._acq.status_var.set(
                f"MediaPipe tracking: {int(p * 100)}%"))

        leg    = meta.get("leg", "right").lower()
        fn_rgb = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="rgb")
        angles = self._engine.run_offline_track(self._video_path, progress, leg=leg)
        DataManager.save_trial(fn_rgb, angles, meta, fps=30.0, source="rgb")

        source_angles = dict(self._pending_review)
        source_angles["rgb"] = angles
        self.after(0, lambda: self._transition_to_review(
            source_angles, meta, from_recording=True))

    def _start_optitrack_recording(self, meta: dict) -> None:
        if _MOTIVE_AVAIL:
            try:
                msg = (f"START|id={meta['pid']}|leg={meta['leg']}|"
                       f"trial={meta['trial']}")
                _motive.start_local_motive(msg)
            except Exception as e:
                messagebox.showwarning(
                    "Motive Sync",
                    f"Could not trigger Motive:\n{type(e).__name__}: {e}\n\n"
                    "Recording will continue without OptiTrack sync.")

    # ------------------------------------------------------------------
    # Panel switching
    # ------------------------------------------------------------------
    def _transition_to_review(self, source_angles: dict, meta: dict,
                              from_recording: bool = False) -> None:
        """from_recording distinguishes an actual live-recording stop (which
        gets a "Recording Saved" confirmation) from the upload-CSV/
        upload-video-file review paths, which process an already-existing
        file rather than saving a new one."""
        self._state = "review"
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        self._post.load_trial(source_angles, self._fps_for(meta), meta, base_fn)
        self._acq.pack_forget()
        self._upload_meta.pack_forget()
        self._post.pack(fill="both", expand=True)
        try:
            self.state("zoomed")
        except Exception:
            pass
        if from_recording:
            self._show_recording_saved_confirmation(source_angles, meta, base_fn)

    def _show_recording_saved_confirmation(self, source_angles: dict, meta: dict,
                                           base_fn: str) -> None:
        """Clear, unmissable confirmation of exactly what got written to
        disk and where, shown once a live recording has fully stopped and
        finished processing -- so a clinician who stepped back during the
        countdown isn't left wondering whether the trial actually saved.
        Only reports files this app itself writes via DataManager.save_trial
        (imu/rgb angle CSVs + the RGB video); OptiTrack's take is Motive's
        own file, not something written here, so it's intentionally not
        listed."""
        lines = []
        if "imu" in source_angles:
            fn_imu = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
            lines.append(f"  • IMU angles (CSV): {fn_imu}")
        if "rgb" in source_angles:
            fn_rgb = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="rgb")
            lines.append(f"  • RGB angles (CSV): {fn_rgb}")
            lines.append(f"  • RGB video: {base_fn.replace('.csv', '.avi')}")
        if not lines:
            return   # nothing this app wrote itself (e.g. an OptiTrack-only trial)
        messagebox.showinfo(
            "Recording Saved",
            "Recording stopped and saved.\n\n"
            f"Folder:\n{DataManager.DATA_DIR}\n\n"
            "Files:\n" + "\n".join(lines))

    @staticmethod
    def _fps_for(meta: dict) -> float:
        return 30.0   # RGB and OptiTrack; IMU timestamps are explicit

    # ------------------------------------------------------------------
    # 50 ms tick — drain IMU queue -> sparkline
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        try:
            while not self._imu_queue.empty():
                t, angle = self._imu_queue.get_nowait()
                if self._state == "recording":
                    self._rec_angles.setdefault("imu", []).append(angle)
                    self._rec_timestamps.setdefault("imu", []).append(t)
                    self._acq.push_telemetry(t, angle)
        except queue.Empty:
            pass

        # Drain preview queue and update acquisition canvas whenever the
        # camera session is live (idle pre-open preview, or recording).
        if self._state in ("idle", "recording") and self._camera is not None \
                and self._camera.active is not None:
            try:
                frame = self._preview_queue.get_nowait()
                self._acq.update_preview(frame)
            except queue.Empty:
                pass

        # Flip label when flex axis transitions from armed → captured
        if (_IMU_AVAIL and "imu" in self._active_sources
                and self._state in ("idle", "recording")):
            try:
                st = _imu.get_state()
                # Low gyro rate makes AHRS integration unreliable regardless of
                # flex-axis state -- surface it first. Same threshold/message
                # pattern already used in pendulastic_viewer.py.
                slow = [d for d in (st["proximal"], st["distal"])
                        if d["connected"] and 0 < d.get("hz", 0) < _imu.MIN_USABLE_HZ]
                if slow:
                    hz = min(d["hz"] for d in slow)
                    self._acq.lbl_method_status.config(
                        text=f"⚠ gyro only {hz:.0f} Hz — set the app's update "
                             f"interval to 10 ms (≥{_imu.MIN_USABLE_HZ:.0f} Hz needed)",
                        fg="#D97706")
                elif st.get("flex_axis_captured"):
                    self._acq.lbl_method_status.config(
                        text="● Axis locked — sagittal tracking", fg="green")
                elif st.get("flex_axis_armed"):
                    self._acq.lbl_method_status.config(
                        text="⚡ Flex once to capture axis...", fg="#B36B00")
            except Exception:
                pass

        self._tick_calibration_check()

        self.after(50, self._tick)

    def _tick_calibration_check(self) -> None:
        """Countdown auto-tare: continuously watch for a stable hold and
        re-tare (edge-triggered) each time a new stable window begins.
        Active only while AcquisitionPanel's countdown is running.

        Stability is read directly from _imu.is_stationary() -- a raw
        gyro-variance + accel-magnitude check computed in
        pendulastic_imu_server.py from each connected device's own trailing
        raw-sample buffers -- rather than a fused pitch/roll buffer
        maintained here. See docs/superpowers/specs/2026-08-04-imu-stillness
        -gyro-bias-design.md Section 3.3."""
        if not (_IMU_AVAIL and "imu" in self._active_sources
                and self._state == "idle"
                and self._acq._countdown_id is not None):
            return
        try:
            stable = _imu.is_stationary()
            if stable and not self._calib_was_stable:
                _imu.zero()
                self._calib_ever_stable = True
                self._calib_was_stable = True
                return
            self._calib_was_stable = stable
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    def on_close(self) -> None:
        self._imu_poll_stop.set()
        if self._imu_poll_thread:
            self._imu_poll_thread.join(timeout=0.5)
        if self._camera is not None:
            writer = self._camera.detach_writer()
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            self._camera.close()
        if _IMU_AVAIL:
            try:
                _imu.stop()
            except Exception:
                pass
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    App().mainloop()
