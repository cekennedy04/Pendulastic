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

PHONE_CAMERA_LABEL = "\U0001f4f1 Phone Camera"
PHONE_CAMERA_ENTRY = {"kind": "phone", "label": PHONE_CAMERA_LABEL}

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
    from camera_utils import CameraSession, PhoneCameraSession, enumerate_cameras
    _CV2_AVAIL = True
except ImportError:
    _cv2 = None
    CameraSession = None
    PhoneCameraSession = None
    enumerate_cameras = None
    _CV2_AVAIL = False

try:
    import pendulastic_phone_server as _pps
    _PPS_AVAIL = True
except Exception:
    _pps = None
    _PPS_AVAIL = False

try:
    from pendulastic_viewer import _MPBatchTracker, _PatientDetector
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
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

try:
    from pendulastic_workbench import TrialLoadPanel, WorkbenchView
    import workbench_engine as _wb_engine
    import workbench_style as _wb_style
    _WORKBENCH_AVAIL = True
except Exception:
    TrialLoadPanel = WorkbenchView = None
    _wb_engine = None
    _wb_style = None
    _WORKBENCH_AVAIL = False

_GREEN = "#1e7d34"
_RED   = "#a31515"
_BLUE  = "#1f3a93"
_AMBER = "#c07000"

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
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=1)

        # row 0 — header: mode-select back button + title
        hdr0 = tk.Frame(self)
        hdr0.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(16, 4))
        self.btn_back = tk.Button(hdr0, text="<- Mode Select",
                                  font=("Segoe UI", 9),
                                  command=self.controller.on_back_to_mode_select)
        self.btn_back.pack(side="left", padx=(0, 8))
        tk.Label(hdr0, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold")).pack(side="left")

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

        # row 8 — Source checkboxes
        self._src_optitrack  = tk.BooleanVar(value=True)
        self._src_rgb        = tk.BooleanVar(value=False)
        self._src_imu        = tk.BooleanVar(value=False)
        self._src_video_file = tk.BooleanVar(value=False)

        meth_f = tk.Frame(self)
        meth_f.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=2)

        # Inner row 1: the 4 source checkbuttons side-by-side
        chk_row = tk.Frame(meth_f)
        chk_row.pack(side="top", anchor="w")
        chk_opti  = tk.Checkbutton(chk_row, text="OptiTrack",
                                    variable=self._src_optitrack,
                                    command=self._on_source_changed)
        chk_rgb   = tk.Checkbutton(chk_row, text="RGB",
                                    variable=self._src_rgb,
                                    command=self._on_rgb_checkbox_toggled)
        chk_imu   = tk.Checkbutton(chk_row, text="iPhone IMU",
                                    variable=self._src_imu,
                                    command=self._on_source_changed)
        chk_video = tk.Checkbutton(chk_row, text="Video File",
                                    variable=self._src_video_file,
                                    command=self._on_source_changed)
        for chk in (chk_opti, chk_rgb, chk_imu, chk_video):
            chk.pack(side="left", padx=8)

        # Inner row 2: video file path selector (hidden until _src_video_file checked)
        self._video_path_frame = tk.Frame(meth_f)
        self._video_path_frame.pack(side="top", anchor="w", pady=(2, 0))
        self._video_path_var    = tk.StringVar(value="No file selected")
        self._stored_video_path = ""
        tk.Label(self._video_path_frame,
                 textvariable=self._video_path_var,
                 font=("Consolas", 8), fg="gray", width=38,
                 anchor="w").pack(side="left")
        tk.Button(self._video_path_frame, text="Browse...",
                  font=("Segoe UI", 8),
                  command=self._on_browse_video).pack(side="left", padx=4)
        self._video_path_frame.pack_forget()   # hidden until checkbox checked

        # Inner row 3: camera selector (hidden until RGB is checked)
        self._cam_frame = tk.Frame(meth_f)
        self.cam_var = tk.StringVar(value="")
        self.drop_cam = ttk.Combobox(self._cam_frame, textvariable=self.cam_var,
                                     width=18, state="readonly")
        self.drop_cam.pack(side="left")
        self.drop_cam.bind("<<ComboboxSelected>>", self._on_cam_selected)
        self.btn_rescan = tk.Button(self._cam_frame, text="Rescan", font=("Segoe UI", 8),
                  command=self._on_rescan_clicked)
        self.btn_rescan.pack(side="left", padx=4)
        tk.Button(self._cam_frame, text="🛜 Can't connect?", font=("Segoe UI", 8),
                  command=self._on_camera_help).pack(side="left", padx=4)
        self._cam_frame.pack_forget()   # hidden until RGB is checked
        self._camera_live = False       # updated via set_camera_live()

        # Phone pairing panel — shown when the phone dropdown entry is
        # selected; hidden otherwise. Reuses pendulastic_viewer.py's
        # qrcode-based QR generation pattern.
        self._phone_pairing_frame = tk.Frame(meth_f, relief="groove", borderwidth=1)
        self._phone_pairing_url_var = tk.StringVar(value="")
        tk.Label(self._phone_pairing_frame, text="Open on your phone:",
                  font=("Segoe UI", 8, "bold")).pack(side="top", anchor="w", padx=6, pady=(4, 0))
        self._phone_qr_label = tk.Label(self._phone_pairing_frame)
        self._phone_qr_label.pack(side="top", padx=6, pady=4)
        tk.Entry(self._phone_pairing_frame, textvariable=self._phone_pairing_url_var,
                  font=("Consolas", 8), width=32, state="readonly").pack(
            side="top", padx=6, pady=(0, 4))
        tk.Label(self._phone_pairing_frame,
                  text="Your phone will warn about the connection's security\n"
                       "certificate — tap Advanced -> Proceed. This is expected.",
                  font=("Segoe UI", 7), fg="gray", justify="left").pack(
            side="top", anchor="w", padx=6, pady=(0, 4))
        self._phone_pairing_frame.pack_forget()

        # row 9 — Modality status (calibration is now automatic during the
        # countdown -- see App._tick_calibration_check / AcquisitionPanel's
        # forced-on countdown checkbox below)
        self.lbl_method_status = tk.Label(
            self, text="● OptiTrack (Motive)", font=("Consolas", 9), fg="green", anchor="w")
        self.lbl_method_status.grid(row=9, column=0, sticky="w", padx=16)

        # row 10 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 11 — countdown checkbox (forced on/locked while IMU is an
        # active source -- it's the only calibration path now)
        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var)
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

        # row 13 alt — live video preview (shown instead of canvas_tele when RGB is recording)
        self.lbl_preview = tk.Label(self, bg="black")
        # not gridded at init; enter_recording() grids the correct one

        # row 14 — status bar
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w", fg="#333")
        self.lbl_status.grid(row=14, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))

        # Track every form widget that must be locked during recording
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            self.countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
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

    def enter_recording(self) -> None:
        self._lock_form(True)
        self.btn_start.config(text="START RECORDING",
                              bg=_GREEN, state="disabled")
        self.btn_stop.config(state="normal")
        self._is_recording = True
        self._refresh_preview_area()
        self.status_var.set("RECORDING…")

    def _refresh_preview_area(self) -> None:
        """Row 13 shows lbl_preview whenever RGB is checked and either
        currently recording or the pre-open camera session is live;
        canvas_tele only while recording and that doesn't hold; otherwise
        neither. Recording-time behavior is unchanged from before this
        feature — _camera_live only extends what's shown while idle."""
        show_preview = self._src_rgb.get() and (self._is_recording or self._camera_live)
        if show_preview:
            self.lbl_preview.grid(row=13, column=0, columnspan=2,
                                  padx=10, pady=4, sticky="nsew")
            self.canvas_tele.grid_remove()
        elif self._is_recording:
            self.canvas_tele.grid(row=13, column=0, columnspan=2,
                                  padx=10, pady=4)
            self.lbl_preview.grid_remove()
        else:
            self.lbl_preview.grid_remove()
            self.canvas_tele.grid_remove()

    def enter_processing(self) -> None:
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.status_var.set("Running MediaPipe tracking…")

    def update_preview(self, frame_bgr) -> None:
        """Convert a BGR numpy frame and display it in lbl_preview."""
        if not _CV2_AVAIL:
            return
        import base64
        h, w = frame_bgr.shape[:2]
        scale = min(440 / max(w, 1), 330 / max(h, 1))
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        small = _cv2.resize(frame_bgr, (nw, nh))
        rgb   = _cv2.cvtColor(small, _cv2.COLOR_BGR2RGB)
        ok, buf = _cv2.imencode(".png", rgb)
        if ok:
            b64 = base64.b64encode(buf).decode("utf-8")
            photo = tk.PhotoImage(data=b64)
            self.lbl_preview.config(image=photo)
            self.lbl_preview._photo = photo   # prevent GC

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
            self._cam_frame.pack(side="top", anchor="w", pady=(2, 0))
            self.controller.on_rescan_cameras()
        else:
            self._cam_frame.pack_forget()
            self.controller.on_camera_disabled()
        self._on_source_changed()

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
        live/lost state changes."""
        self._camera_live = is_live
        self._refresh_preview_area()

    def show_phone_pairing_panel(self, url: str) -> None:
        self._phone_pairing_url_var.set(url)
        try:
            import qrcode
            from PIL import ImageTk
            qr = qrcode.QRCode(box_size=5, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            raw = qr.make_image(fill_color="black", back_color="white")
            pil_img = raw.get_image() if hasattr(raw, "get_image") else raw
            photo = ImageTk.PhotoImage(pil_img.convert("RGB"))
            self._phone_qr_label.config(image=photo, text="")
            self._phone_qr_label._photo = photo   # prevent GC
        except Exception as exc:
            self._phone_qr_label.config(image="", text=f"(QR unavailable: {exc})")
        self._phone_pairing_frame.pack(side="top", anchor="w", pady=(4, 0), fill="x")

    def hide_phone_pairing_panel(self) -> None:
        self._phone_pairing_frame.pack_forget()

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
        tk.Label(self, text="Pendulastic",
                 font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(60, 4))
        tk.Label(self, text="Clinical Pendulum Test Platform",
                 font=("Segoe UI", 11), fg="#555").grid(
            row=1, column=0, columnspan=2, pady=(0, 40))

        tk.Button(
            self,
            text="Live Recording Session\nIMU · RGB · OptiTrack",
            font=("Segoe UI", 12, "bold"),
            bg=_GREEN, fg="white",
            width=24, height=4,
            command=self.controller._enter_live_mode,
        ).grid(row=2, column=0, padx=40, pady=16, sticky="n")

        tk.Button(
            self,
            text="Upload & Analyze\nVideo or CSV file",
            font=("Segoe UI", 12, "bold"),
            bg=_BLUE, fg="white",
            width=24, height=4,
            command=self.controller._enter_upload_mode,
        ).grid(row=2, column=1, padx=40, pady=16, sticky="n")

        tk.Button(
            self,
            text="Multi-Modal Comparison\nIMU · OptiTrack · Video",
            font=("Segoe UI", 12, "bold"),
            bg=_AMBER, fg="white",
            width=24, height=4,
            command=self.controller._enter_workbench_mode,
        ).grid(row=3, column=0, columnspan=2, padx=40, pady=(0, 24), sticky="n")


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
        pad = {"padx": 12, "pady": 6}

        # Header: back button + title
        hdr = tk.Frame(self)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew",
                 padx=12, pady=(16, 4))
        self.btn_back = tk.Button(hdr, text="<- Back",
                                  font=("Segoe UI", 10),
                                  command=self.controller._upload_back_to_select)
        self.btn_back.pack(side="left", padx=(0, 12))
        tk.Label(hdr, text="Upload & Analyze",
                 font=("Segoe UI", 13, "bold")).pack(side="left")

        # Selected file name
        self._file_label_var = tk.StringVar(value="No file selected")
        tk.Label(self, textvariable=self._file_label_var,
                 font=("Consolas", 9), fg="gray", anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

        # Participant ID
        tk.Label(self, text="Participant ID:").grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        tk.Entry(self, textvariable=self.pid_var, width=22).grid(
            row=2, column=1, sticky="w", **pad)

        # Leg
        tk.Label(self, text="Leg:").grid(row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self)
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var,
                       value="Left").pack(side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var,
                       value="Right").pack(side="left", padx=4)

        # MS Status
        tk.Label(self, text="MS Status:").grid(row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ttk.Combobox(self, textvariable=self.ms_var, width=22, state="readonly",
                     values=["MS", "Stroke", "Control", "Other"]).grid(
            row=4, column=1, sticky="w", **pad)

        # Trial number
        tk.Label(self, text="Trial Number:").grid(row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6).grid(
            row=5, column=1, sticky="w", **pad)

        # Analyze button
        self.btn_analyze = tk.Button(
            self, text="Analyze ->",
            bg=_BLUE, fg="white", font=("Segoe UI", 11, "bold"),
            width=16, height=2,
            command=self.controller._start_upload_analysis)
        self.btn_analyze.grid(row=6, column=0, columnspan=2, pady=20)

        # Status bar
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w", fg="#333").grid(
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
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=1)

        # row 0 — header: mode-select back button + trial filename
        hdr0 = tk.Frame(self)
        hdr0.grid(row=0, column=0, columnspan=3, sticky="ew",
                  padx=12, pady=(12, 4))
        tk.Button(hdr0, text="<- Mode Select",
                  font=("Segoe UI", 9),
                  command=self.controller.on_back_to_mode_select).pack(
            side="left", padx=(0, 12))
        self.title_var = tk.StringVar(value="")
        tk.Label(hdr0, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")

        # row 1 — matplotlib figure
        if _MPL_AVAIL:
            self._fig    = Figure(figsize=(10, 4), dpi=96, facecolor="#EEF2F7")
            self._ax     = self._fig.add_subplot(111)
            self._canvas = FigureCanvasTkAgg(self._fig, master=self)
            self._canvas.get_tk_widget().grid(
                row=1, column=0, columnspan=3, sticky="nsew", padx=8, pady=4)
        else:
            tk.Label(self, text="matplotlib not available — install it in .venv",
                     fg="red").grid(row=1, column=0, columnspan=3)
            self._canvas = None

        # row 2 — PT Metrics LabelFrame
        self._metrics_frame = tk.LabelFrame(self, text="Popović PT Metrics",
                                            font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        self._metrics_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=4)

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
            tk.Label(self._metrics_frame, text=lbl, font=("Segoe UI", 8), fg="#555").grid(
                row=0, column=col, padx=10, pady=1)
            tk.Label(self._metrics_frame, textvariable=var,
                     font=("Segoe UI", 11, "bold")).grid(
                row=1, column=col, padx=10)

        # row 3 — action buttons
        tk.Button(self, text="<- New Trial",
                  bg=_BLUE, fg="white", font=("Segoe UI", 11, "bold"),
                  width=14, height=2,
                  command=self._on_new_trial).grid(
            row=3, column=0, padx=10, pady=12, sticky="e")
        tk.Button(self, text="Load OptiTrack CSV",
                  font=("Segoe UI", 10), width=20, height=2,
                  command=self._on_load_optitrack).grid(
            row=3, column=1, padx=10, pady=12, sticky="w")
        self.btn_upload_video = tk.Button(
            self, text="🎥 Upload Video for HPE",
            font=("Segoe UI", 10), width=22, height=2,
            command=self._on_upload_video)
        self.btn_upload_video.grid(row=3, column=2, padx=10, pady=12, sticky="w")

        # row 4 — status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w", fg="#333").grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 8))

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
        engine = BiomechanicalEngine("rgb")

        def _progress(pct: float) -> None:
            self.after(0, lambda p=pct: self.status_var.set(
                f"HPE processing: {int(p * 100)}%"))

        def _run() -> None:
            angles = engine.run_offline_track(path, _progress, leg=leg.lower())
            self.after(0, lambda: self._add_hpe_overlay(angles, fps=30.0))

        threading.Thread(target=_run, daemon=True).start()

    def _add_hpe_overlay(self, angles: list, fps: float = 30.0) -> None:
        if not angles:
            self.status_var.set(
                "HPE: no pose detected — check video or leg selection.")
            return
        self._source_angles["hpe_upload"] = angles
        if not self._fps:
            self._fps = fps
        if not self.title_var.get():
            self.title_var.set("HPE upload")
        self._plot_all_curves()
        self._show_pt_metrics_from_sources()
        self.status_var.set(f"HPE overlay loaded — {len(angles)} frames")

    def _on_new_trial(self) -> None:
        self.controller.on_new_trial()

    def _on_load_optitrack(self) -> None:
        path = filedialog.askopenfilename(
            title="Select OptiTrack CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.load_optitrack_overlay(path)


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

        self._mode_select = ModeSelectView(self, controller=self)
        self._upload_meta = UploadMetaView(self, controller=self)
        self._acq  = AcquisitionPanel(self, controller=self)
        self._post = PostProcessingPanel(self, controller=self)

        self._workbench_trial_meta: dict = {}
        self._workbench_imu_reference: list = []
        self._workbench_raw_diagnostics: Optional[dict] = None
        self._workbench_status_var = tk.StringVar(value="")
        if _WORKBENCH_AVAIL:
            # Registers the dark "Workbench.*" ttk styles the embedded panels
            # opt into. It does not switch this root's base ttk theme, so the
            # other panels' ttk.Combobox/ttk.Separator widgets are untouched.
            _wb_style.apply_ttk_theme(self)
            self._workbench_load = TrialLoadPanel(self, controller=self)
            self._workbench_view = WorkbenchView(self, controller=self)
            tk.Label(self, textvariable=self._workbench_status_var, anchor="w").pack(
                side="bottom", fill="x", padx=8, pady=2)

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
            self._transition_to_review(source_angles, meta)

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
        if _WORKBENCH_AVAIL:
            self._workbench_load.pack_forget()
            self._workbench_view.pack_forget()
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
        self.after(0, lambda: self._transition_to_review(source_angles, meta))

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
        self.after(0, lambda: self._transition_to_review(source_angles, meta))

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
    def _transition_to_review(self, source_angles: dict, meta: dict) -> None:
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
