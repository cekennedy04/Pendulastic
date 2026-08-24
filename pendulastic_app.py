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
    from camera_utils import (
        CameraSession, open_video_writer, PhoneCameraSession, enumerate_cameras,
    )
    _CV2_AVAIL = True
except ImportError:
    _cv2 = None
    CameraSession = None
    open_video_writer = None
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
    from PIL import Image, ImageTk
    _PIL_AVAIL = True
except Exception:
    Image = None
    ImageTk = None
    _PIL_AVAIL = False

try:
    from pendulastic_viewer import (
        _MPBatchTracker, _PatientDetector, _draw, TRAIL_LEN, _MP_MODEL,
        draw_person_select_overlay, resolve_person_click,
    )
    _VIEWER_AVAIL = True
except Exception:
    _MPBatchTracker = None
    _PatientDetector = None
    _draw = None
    TRAIL_LEN = 150
    _MP_MODEL = None
    draw_person_select_overlay = None
    resolve_person_click = None
    _VIEWER_AVAIL = False

try:
    from video_review_dialog import AnnotatedVideoReviewDialog
except Exception:
    AnnotatedVideoReviewDialog = None

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
        manual_seed: tuple | None = None,
        start_frame: int = 0,
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

        When collect_landmarks is True, returns (angles, landmarks, fps,
        detected) where landmarks[i] is (hip, knee, ankle) for frame i, or
        None if pose tracking wasn't available for that frame -- len(landmarks)
        == len(angles) always -- and fps is the video's true source frame
        rate. detected[i] is True only if a real pose was found and accepted
        for the tracked person on frame i; False covers both "not yet
        initialised" and "tracker.step() fell through to a frozen prior
        position because nothing was detected this frame" -- angles/landmarks
        stay populated (frozen value) either way for curve continuity, so
        detected is the only reliable signal for "no person found here."
        When False (default), returns angles only, matching the original
        signature exactly.

        manual_seed, when given as a (hip, knee, ankle) triple, skips the
        per-frame _PatientDetector search entirely -- the tracker is
        initialised from that seed on the first frame read instead. When
        None (default), behavior is unchanged from before this parameter
        existed.

        start_frame, when > 0, seeks the video to that frame before tracking
        begins. The returned angles/landmarks then cover ONLY the suffix from
        start_frame to the end of the video (length = total_frames -
        start_frame), not the full video -- callers that want a full-video
        result splice this suffix into their own existing arrays starting at
        start_frame. Default 0 preserves the exact prior behavior (full video
        from frame 0).
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return ([], [], 30.0, []) if collect_landmarks else []

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return ([], [], 30.0, []) if collect_landmarks else []

        fps_v  = cap.get(_cv2.CAP_PROP_FPS) or 30.0
        total  = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT)) or 1
        if start_frame > 0:
            cap.set(_cv2.CAP_PROP_POS_FRAMES, start_frame)
            total = max(total - start_frame, 1)

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
        detected: list = []

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if not initialised:
                    if manual_seed is not None:
                        hip, knee, ankle = manual_seed
                        tracker.init(frame, hip, knee, ankle)
                        initialised = True
                    else:
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
                        detected.append(bool(getattr(tracker, "last_detected", True)))
                        if collect_landmarks:
                            landmarks.append((hip_p, knee_p, ank_p))
                    except Exception:
                        angles.append(float("nan"))
                        detected.append(False)
                        if collect_landmarks:
                            landmarks.append(None)
                else:
                    angles.append(float("nan"))
                    detected.append(False)
                    if collect_landmarks:
                        landmarks.append(None)

                progress_cb(len(angles) / total)
        finally:
            cap.release()

        progress_cb(1.0)
        return (angles, landmarks, fps_v, detected) if collect_landmarks else angles

    def detect_people_at_frame(
        self, video_path: str, frame_index: int = 0,
    ) -> tuple:
        """
        Run MediaPipe PoseLandmarker (IMAGE mode, up to 4 candidates) on a
        single frame of video_path, for multi-person disambiguation before
        a full offline track.

        Returns (frame, poses):
          - frame: the raw BGR frame (np.ndarray) at frame_index, or None
            if the video couldn't be opened or that frame couldn't be
            read (including a frame_index past the end of the clip).
          - poses: a list of pose landmark sets (mediapipe's
            pose_landmarks result), or [] if detection found nobody, or
            detection itself raised an exception.

        Never raises -- any exception from running detection is caught
        internally and treated as "0 people found" so callers have one
        fallback path regardless of failure cause.
        """
        if not (_VIEWER_AVAIL and _CV2_AVAIL):
            return (None, [])

        cap = _cv2.VideoCapture(video_path)
        if not cap.isOpened():
            cap.release()
            return (None, [])

        try:
            cap.set(_cv2.CAP_PROP_POS_FRAMES, frame_index)
            ret, frame = cap.read()
        finally:
            cap.release()

        if not ret or frame is None:
            return (None, [])

        try:
            V = _mp.tasks.vision
            opts = V.PoseLandmarkerOptions(
                base_options=_mp.tasks.BaseOptions(model_asset_path=_MP_MODEL),
                running_mode=V.RunningMode.IMAGE,
                num_poses=4,
                min_pose_detection_confidence=0.25,
                min_pose_presence_confidence=0.25,
            )
            with V.PoseLandmarker.create_from_options(opts) as detector:
                rgb    = _cv2.cvtColor(frame, _cv2.COLOR_BGR2RGB)
                result = detector.detect(
                    _mp.Image(image_format=_mp.ImageFormat.SRGB, data=rgb))
            poses = result.pose_landmarks or []
        except Exception:
            poses = []

        return (frame, poses)


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

        self._src_imu_browser = tk.BooleanVar(value=False)
        chk_imu_browser = tk.Checkbutton(
            chk_row, text="Phone IMU (browser)",
            variable=self._src_imu_browser,
            bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"],
            activebackground=ws.PALETTE["PANEL"],
            command=self._on_imu_browser_checkbox_toggled)
        chk_imu_browser.pack(side="left", padx=8)

        # IMU pairing hint -- shown whenever "iPhone IMU" is checked, so the
        # operator doesn't have to go digging in the console log for the
        # ws:// address to enter into the Sensor Stream app. Static text
        # computed once per show (the machine's IP doesn't change mid-
        # session); mirrors the existing _phone_pairing_frame pattern used
        # for the RGB phone-camera connection below.
        self._imu_pairing_frame = tk.Frame(meth_f, bg=ws.PALETTE["PANEL"])
        self._imu_pairing_var = tk.StringVar(value="")
        tk.Label(self._imu_pairing_frame, text="Sensor Stream app -> ",
                 font=("Segoe UI", 8), fg=ws.PALETTE["FG2"],
                 bg=ws.PALETTE["PANEL"]).pack(side="left")
        tk.Entry(self._imu_pairing_frame, textvariable=self._imu_pairing_var,
                 font=("Consolas", 8, "bold"), width=22,
                 state="readonly", readonlybackground=ws.PALETTE["PANEL"],
                 relief="flat", justify="left").pack(side="left")
        self._imu_pairing_hint_var = tk.StringVar(value="")
        tk.Label(self._imu_pairing_frame, textvariable=self._imu_pairing_hint_var,
                 font=("Segoe UI", 7), fg=ws.PALETTE["FG3"],
                 bg=ws.PALETTE["PANEL"]).pack(side="left", padx=(4, 0))
        self._imu_pairing_frame.pack_forget()   # hidden until IMU is checked

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
            self, text="● OptiTrack (Motive)", font=("Consolas", 9), fg="green",
            bg=ws.PALETTE["BG"], anchor="w")
        self.lbl_method_status.grid(row=9, column=0, sticky="w", padx=16)

        # row 10 — separator
        ttk.Separator(self, orient="horizontal").grid(
            row=10, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

        # row 11 — countdown + multi-trial checkboxes, stacked in one frame
        # so no other row needs renumbering.
        chk_stack = tk.Frame(self, bg=ws.PALETTE["BG"])
        chk_stack.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            chk_stack, text="5-second countdown before recording",
            variable=self.countdown_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"])
        self.countdown_chk.pack(side="top", anchor="w")

        self._multi_trial_var = tk.BooleanVar(value=False)
        self.multi_trial_chk = tk.Checkbutton(
            chk_stack, text="Record multiple trials",
            variable=self._multi_trial_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"],
            command=self._on_multi_trial_toggle)
        self.multi_trial_chk.pack(side="top", anchor="w")

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

        # row 13 — trial list (multi-trial mode; hidden until toggled on and
        # at least one trial exists this session)
        self._trial_rows_data: list = []
        self._trial_list_frame = ws.card_frame(self, title="TRIALS THIS SESSION")
        self._trial_list_container = tk.Frame(self._trial_list_frame, bg=ws.PALETTE["PANEL"])
        self._trial_list_container.pack(side="top", fill="x")

        # row 14 — live telemetry canvas (NOT gridded at init; shown during RECORDING)
        self.canvas_tele = tk.Canvas(
            self, width=440, height=80, bg="#0B1928", highlightthickness=0)

        # row 15 — status bar
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w",
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"])
        self.lbl_status.grid(row=15, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))

        # Track every form widget that must be locked during recording
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            self.countdown_chk, self.multi_trial_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            chk_imu_browser,
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
            self.canvas_tele.grid(row=14, column=0, columnspan=2,
                                  padx=10, pady=4)
        else:
            self.canvas_tele.grid_remove()

    def enter_processing(self, message: str = "Running MediaPipe tracking…") -> None:
        # Locks the whole form, including btn_back -- without this, a
        # clinician could navigate to mode select while a trial was still
        # processing in the background, orphaning it and (in multi-trial
        # mode) leaving btn_start stuck disabled once that background
        # thread later finished with nothing left to finalize into.
        self._lock_form(True)
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="disabled")
        self.status_var.set(message)

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
        if self._src_imu.get() or self._src_imu_browser.get():
            self.countdown_var.set(True)
            self.countdown_chk.config(state="disabled")
        else:
            self.countdown_chk.config(state="normal")

    def _update_imu_pairing_info(self) -> None:
        """Populate the IMU pairing hint with this machine's best-guess LAN
        IP and the Sensor Stream port. A machine can have more than one
        usable address (e.g. regular Wi-Fi vs. a phone tethered through
        Windows Mobile Hotspot use different subnets) and guessing wrong is
        a real support cost, so a second candidate is named as a fallback
        rather than only showing the primary guess."""
        try:
            ips = _imu.get_all_local_ips()
        except Exception:
            ips = []
        if not ips:
            self._imu_pairing_var.set("no network connection")
            self._imu_pairing_hint_var.set("")
            return
        port = getattr(_imu, "PORT", 5000)
        self._imu_pairing_var.set(f"{ips[0]}:{port}")
        self._imu_pairing_hint_var.set(
            f"(or {ips[1]}:{port} if tethered)" if len(ips) > 1 else "")

    def _on_source_changed(self) -> None:
        """Called on any source checkbox toggle. Updates status label and
        forces the countdown on (IMU trials have no other calibration path
        now that the manual Zero Sensor button is gone)."""
        sources = self.get_active_sources()
        self._apply_countdown_lock()
        # Show/hide IMU pairing hint, refreshing the address each time it
        # becomes visible (cheap; guards against the machine's IP having
        # changed since the frame was last shown, e.g. a different Wi-Fi
        # network since the app was launched).
        if self._src_imu.get() and _IMU_AVAIL:
            self._update_imu_pairing_info()
            self._imu_pairing_frame.pack(side="top", anchor="w", pady=(2, 0))
        else:
            self._imu_pairing_frame.pack_forget()
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
        if self._src_imu.get() or self._src_imu_browser.get():
            sources.append("imu")
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

    def _on_multi_trial_toggle(self) -> None:
        self._sync_trial_list_visibility()

    def _sync_trial_list_visibility(self) -> None:
        show = self._multi_trial_var.get() and bool(self._trial_rows_data)
        if show:
            self._trial_list_frame.grid(row=13, column=0, columnspan=2,
                                        sticky="ew", padx=12, pady=4)
        else:
            self._trial_list_frame.grid_remove()

    def set_multi_trial_list(self, trials: list) -> None:
        self._trial_rows_data = list(trials)
        for w in self._trial_list_container.winfo_children():
            w.destroy()
        for t in self._trial_rows_data:
            self._build_trial_row(t)
        self._sync_trial_list_visibility()

    _SOURCE_LABELS = {"imu": "IMU", "rgb": "RGB", "optitrack": "OptiTrack"}

    def _build_trial_row(self, t: dict) -> None:
        row = tk.Frame(self._trial_list_container, bg=ws.PALETTE["PANEL"])
        row.pack(side="top", fill="x", pady=1)
        src_label = " + ".join(self._SOURCE_LABELS.get(s, s) for s in t["sources"])
        status_label = "Processing…" if t["status"] == "processing" else "Saved"
        text = f"Trial {t['trial_num']} · {src_label} · {status_label}"
        lbl = tk.Label(row, text=text, anchor="w", cursor="hand2",
                       bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"])
        lbl.pack(side="left", fill="x", expand=True, padx=(2, 4))
        lbl.bind("<Button-1>", lambda e, n=t["trial_num"]: self.controller.on_view_trial(n))
        btn_del = tk.Button(
            row, text="✕", relief="flat", bd=0, cursor="hand2",
            bg=ws.PALETTE["PANEL"], fg=_RED,
            state="disabled" if t["status"] == "processing" else "normal",
            command=lambda n=t["trial_num"]: self._on_delete_clicked(n))
        btn_del.pack(side="right", padx=4)

    def _on_delete_clicked(self, trial_num: int) -> None:
        if messagebox.askyesno(
                "Delete Trial",
                f"Delete Trial {trial_num}? This removes its saved files "
                "and can't be undone."):
            self.controller.on_delete_trial(trial_num)

    def _on_rgb_checkbox_toggled(self) -> None:
        if self._src_rgb.get():
            self._cam_frame.pack(side="top", anchor="w", pady=(2, 0),
                                  before=self._research_toggle_btn)
            self.controller.on_rescan_cameras()
        else:
            self._cam_frame.pack_forget()
            self.controller.on_camera_disabled()
        self._on_source_changed()

    def _on_imu_browser_checkbox_toggled(self) -> None:
        self.controller.on_imu_browser_toggled()
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
        self._grow_window_to_fit()

    def hide_phone_pairing_panel(self) -> None:
        self._phone_pairing_frame.pack_forget()

    def _grow_window_to_fit(self) -> None:
        """The QR panel adds ~150-200px to this card, pushing START/STOP and
        everything below it further down the grid. The root Tk window's size
        was fixed once via geometry(WxH) at startup, so Tkinter won't
        auto-grow it to keep pace -- the extra content (including the
        recording buttons) ends up laid out below the visible/clickable
        window area. Grow the window's height (never shrink it) to keep the
        buttons reachable."""
        top = self.winfo_toplevel()
        top.update_idletasks()
        req_h = top.winfo_reqheight()
        if req_h > top.winfo_height():
            top.geometry(f"{top.winfo_width()}x{req_h}")

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
        self.rowconfigure(0, weight=1)
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])

        # A single centered content column: with weight=1 on both the row
        # and column above, this frame stays centered as the window resizes
        # instead of pinned to a fixed offset from the top-left.
        content = tk.Frame(self, bg=ws.PALETTE["BG"])
        content.grid(row=0, column=0)

        header = tk.Frame(content, bg=ws.PALETTE["BG"])
        header.pack(pady=(0, 36))
        ws.brand_mark(header, size=52).pack(side="left", padx=(0, 14))
        title_col = tk.Frame(header, bg=ws.PALETTE["BG"])
        title_col.pack(side="left")
        tk.Label(title_col, text="Pendulastic", font=("Segoe UI", 22, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(anchor="w")
        tk.Label(title_col, text="Clinical Pendulum Test Platform",
                 font=("Segoe UI", 11), bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG3"]).pack(anchor="w")

        # Live Recording is the routine clinical path -- the one hero
        # (large, filled-accent) tile on this screen.
        ws.tile(content, "Live Recording Session", "IMU · RGB · OptiTrack",
                self.controller._enter_live_mode, icon="record",
                width=520, height=104, primary=True).pack(pady=(0, 18))

        grid = tk.Frame(content, bg=ws.PALETTE["BG"])
        grid.pack()
        secondary_actions = [
            ("Upload & Analyze", "Video or CSV file",
             self.controller._enter_upload_mode, "upload"),
            ("Multi-Modal Comparison", "IMU · OptiTrack · Video",
             self.controller._enter_workbench_mode, "compare"),
            ("Analysis & Reports", "Compare participants",
             self.controller._enter_analysis_mode, "chart"),
            ("MAS Score Entry", "Enter & validate",
             self.controller._enter_mas_entry_mode, "checklist"),
        ]
        for i, (title, subtitle, command, icon) in enumerate(secondary_actions):
            row, col = divmod(i, 2)
            ws.tile(grid, title, subtitle, command, icon=icon,
                    width=248, height=92).grid(row=row, column=col, padx=8, pady=8)


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
# PersonPickerDialog
# ---------------------------------------------------------------------------

class PersonPickerDialog(tk.Toplevel):
    """Modal dialog: shows every MediaPipe-detected person in a frame with a
    numbered colored skeleton overlay and lets the user click the patient.

    On a resolved click, self.result is set to (hip, knee, ankle) pixel
    coordinates before the dialog closes. self.result stays None if the
    user cancels/closes the dialog without a successful resolution.
    """

    MAX_DISPLAY_WIDTH   = 900
    TRY_NEXT_FRAME_STEP = 15

    def __init__(self, parent, video_path: str, frame_index: int,
                 frame: np.ndarray, poses: list, leg: str) -> None:
        super().__init__(parent)
        self.title("Select the Patient")
        self.resizable(False, False)
        self.transient(parent)

        self._video_path  = video_path
        self._frame_index = frame_index
        self._frame        = frame
        self._poses         = poses
        self._leg           = leg
        self._engine         = BiomechanicalEngine("rgb")
        self._scale          = 1.0
        self.result: tuple | None = None

        self._status_var = tk.StringVar(
            value=f"MediaPipe detected {len(poses)} person(s) — click the PATIENT.")

        self._image_label = tk.Label(self, cursor="crosshair")
        self._image_label.pack()
        self._image_label.bind("<Button-1>", self._on_click)

        status_row = tk.Frame(self)
        status_row.pack(fill="x", padx=8, pady=(4, 0))
        tk.Label(status_row, textvariable=self._status_var,
                 anchor="w", wraplength=self.MAX_DISPLAY_WIDTH).pack(
            side="left", fill="x", expand=True)

        button_row = tk.Frame(self)
        button_row.pack(fill="x", padx=8, pady=8)
        self.btn_next_frame = tk.Button(
            button_row, text="Try Next Frame", command=self._on_try_next_frame)
        self.btn_next_frame.pack(side="left")
        tk.Button(button_row, text="Cancel", command=self.destroy).pack(
            side="right")

        self._render_frame()
        self.grab_set()

    def _render_frame(self) -> None:
        overlay = draw_person_select_overlay(self._frame, self._poses)
        h, w = overlay.shape[:2]
        if w > self.MAX_DISPLAY_WIDTH:
            self._scale = self.MAX_DISPLAY_WIDTH / w
            disp = _cv2.resize(overlay, (int(w * self._scale), int(h * self._scale)))
        else:
            self._scale = 1.0
            disp = overlay
        rgb = _cv2.cvtColor(disp, _cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(img)
        self._image_label.configure(image=self._photo)

    def _on_click(self, event) -> None:
        frame_h, frame_w = self._frame.shape[:2]
        click_xy = (event.x / self._scale, event.y / self._scale)
        result = resolve_person_click(
            self._poses, click_xy, frame_w, frame_h, self._leg)

        if result is None:
            self._status_var.set(
                "No detected person near that click — try clicking directly "
                "on a numbered skeleton.")
            return

        hip, knee, ankle = result
        if ankle is None:
            self._status_var.set(
                "Ankle visibility too low for that candidate — try clicking "
                "a different candidate, or Try Next Frame.")
            return

        self.result = (hip, knee, ankle)
        self.destroy()

    def _on_try_next_frame(self) -> None:
        next_index = self._frame_index + self.TRY_NEXT_FRAME_STEP
        frame, poses = self._engine.detect_people_at_frame(
            self._video_path, frame_index=next_index)
        if frame is None:
            self.btn_next_frame.config(state="disabled")
            self._status_var.set(
                "End of clip reached — try a different video.")
            return
        self._frame_index = next_index
        self._frame        = frame
        self._poses         = poses
        self._status_var.set(
            f"MediaPipe detected {len(poses)} person(s) — click the PATIENT.")
        self._render_frame()


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
        self._from_trial_list      = False
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
        self.btn_new_trial = ws.secondary_button(self, "← New Trial", self._on_new_trial)
        self.btn_new_trial.grid(row=3, column=0, padx=10, pady=12, sticky="e")
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
    def set_back_context(self, from_trial_list: bool) -> None:
        self._from_trial_list = from_trial_list
        self.btn_new_trial.config(
            text="← Back to Trials" if from_trial_list else "← New Trial")

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

        leg    = self._meta.get("leg", "right") if self._meta else "right"
        engine = BiomechanicalEngine("rgb")

        self.status_var.set("Detecting people…")
        self.update_idletasks()
        frame, poses = engine.detect_people_at_frame(path)

        manual_seed = None
        if len(poses) == 1:
            # Only one candidate -- resolve_person_click's nearest-pose
            # search trivially picks it regardless of click position, so
            # any point in frame bounds works here; this reuses the same
            # leg-resolution/ankle-visibility logic as the 2+-person
            # disambiguation path below.
            fh, fw = frame.shape[:2]
            result = resolve_person_click(poses, (fw / 2, fh / 2), fw, fh, leg)
            if result is not None and result[2] is not None:
                manual_seed = result
        elif len(poses) >= 2:
            dialog = PersonPickerDialog(self, path, 0, frame, poses, leg)
            self.wait_window(dialog)
            if dialog.result is None:
                self.status_var.set("Upload cancelled — no patient selected.")
                return
            manual_seed = dialog.result

        self.status_var.set("HPE processing: 0%")
        self._video_path = path
        self._hpe_leg     = leg
        self._hpe_landmarks = None
        self._source_angles.pop("hpe_upload", None)
        self.btn_export_video.config(state="disabled")

        def _progress(pct: float) -> None:
            self.after(0, lambda p=pct: self.status_var.set(
                f"HPE processing: {int(p * 100)}%"))

        def _run() -> None:
            angles, landmarks, video_fps, detected = engine.run_offline_track(
                path, _progress, leg=leg.lower(), collect_landmarks=True,
                manual_seed=manual_seed)
            self.after(0, lambda: self._add_hpe_overlay(
                angles, landmarks, fps=video_fps, engine=engine,
                detected=detected))

        threading.Thread(target=_run, daemon=True).start()

    def _add_hpe_overlay(self, angles: list, landmarks: list | None = None,
                          fps: float = 30.0, engine=None,
                          detected: list | None = None) -> None:
        if not angles or (detected is not None and not any(detected)):
            self.status_var.set(
                "HPE: no pose detected — check video or leg selection.")
            return
        review_error = None
        if landmarks and engine is not None and self._video_path \
                and AnnotatedVideoReviewDialog is not None:
            try:
                dialog = AnnotatedVideoReviewDialog(
                    self, self._video_path, angles, landmarks,
                    fps or self._fps, self._hpe_leg, engine)
                self.wait_window(dialog)
                angles = dialog.angles
                landmarks = dialog.landmarks
            except Exception as exc:
                # Don't let a per-call dialog failure (e.g. the video file
                # was moved/deleted between upload and track completion, or
                # a PhotoImage/_draw failure in the dialog's first _redraw())
                # discard the tracking run that already completed -- fall
                # through to the normal path with the original angles and
                # landmarks the run produced.
                review_error = exc
        self._source_angles["hpe_upload"] = angles
        self._hpe_landmarks = landmarks
        if not self._fps:
            self._fps = fps
        if not self.title_var.get():
            self.title_var.set("HPE upload")
        self._plot_all_curves()
        self._show_pt_metrics_from_sources()
        if review_error is not None:
            self.status_var.set(
                f"Video review unavailable: {review_error} -- showing "
                "results without review.")
        else:
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
        if self._from_trial_list:
            self.controller.on_back_to_trial_list()
        else:
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
        # Trial-exclusion table state (design spec
        # docs/superpowers/specs/2026-08-07-trial-exclusion-ui-design.md).
        # A separate queue/request-id from the Generate flow above -- reusing
        # _result_queue would let a stale table-load result be decoded as a
        # figure result or vice versa.
        self._table_queue: queue.Queue = queue.Queue()
        self._table_request_id = 0
        self._table_row_meta: dict = {}
        self._table_dupes: dict = {}
        self._table_polling = False   # True while a _poll_table_queue chain is active
        self._table_job_pending = False   # True while a worker's result for the
                                           # current request_id is still expected
        self._busy = False
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

        # ── Right: scrollable figure viewer / trial table (two view modes
        # sharing the same grid cell) ────────────────────────────────────
        viewer_outer = tk.Frame(self, relief="sunken", bd=1, bg=ws.PALETTE["BG"])
        viewer_outer.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        viewer_outer.columnconfigure(0, weight=1)
        viewer_outer.rowconfigure(0, weight=1)

        self._viewer_canvas = tk.Canvas(viewer_outer, bg=ws.PALETTE["SURFACE"],
                                        highlightthickness=0)
        # Promoted to self (round-3 spec fix): grid_remove()ing only the
        # canvas when switching to the table view would leave these two
        # orphaned next to it if they stayed local variables.
        self._viewer_vbar = tk.Scrollbar(viewer_outer, orient="vertical", command=self._viewer_canvas.yview)
        self._viewer_hbar = tk.Scrollbar(viewer_outer, orient="horizontal", command=self._viewer_canvas.xview)
        self._viewer_canvas.configure(yscrollcommand=self._viewer_vbar.set, xscrollcommand=self._viewer_hbar.set)
        self._viewer_canvas.grid(row=0, column=0, sticky="nsew")
        self._viewer_vbar.grid(row=0, column=1, sticky="ns")
        self._viewer_hbar.grid(row=1, column=0, sticky="ew")

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

        # Trial table -- gridded into the same (row=0, col=0) cell as
        # _viewer_canvas, shown only when exactly one participant is
        # selected. Not grid()'d here; _switch_to_table_view() does that.
        self._table_frame = tk.Frame(viewer_outer, bg=ws.PALETTE["SURFACE"])

        table_top = tk.Frame(self._table_frame, bg=ws.PALETTE["SURFACE"])
        table_top.pack(side="top", fill="x", padx=6, pady=(6, 2))
        self.btn_toggle_excluded = ws.secondary_button(
            table_top, "Toggle Excluded", self._on_toggle_excluded)
        self.btn_toggle_excluded.config(state="disabled")
        self.btn_toggle_excluded.pack(side="left")

        table_wrap = tk.Frame(self._table_frame, bg=ws.PALETTE["SURFACE"])
        table_wrap.pack(side="top", fill="both", expand=True, padx=6, pady=(0, 6))
        table_wrap.columnconfigure(0, weight=1)
        table_wrap.rowconfigure(0, weight=1)

        cols = ("warn", "leg", "condition", "trial", "n", "phi_max_ratio", "area_ratio")
        hdrs = ("⚠", "Leg", "Condition", "Trial #", "N", "phi_max_ratio", "area_ratio")
        widths = (24, 60, 110, 60, 50, 100, 90)
        self._trial_table = ttk.Treeview(
            table_wrap, style=ws.STYLE_TREEVIEW, columns=cols, show="headings",
            selectmode="extended")
        for key, hdr, w in zip(cols, hdrs, widths):
            self._trial_table.heading(key, text=hdr)
            self._trial_table.column(key, width=w, anchor="center", stretch=False)
        self._trial_table.column("condition", stretch=True)
        self._trial_table.tag_configure("excluded", foreground=ws.PALETTE["FG3"])
        self._trial_table.tag_configure("duplicate", foreground="#B45309")
        table_sb = ttk.Scrollbar(table_wrap, orient="vertical", style=ws.STYLE_SCROLLBAR,
                                 command=self._trial_table.yview)
        self._trial_table.configure(yscrollcommand=table_sb.set)
        self._trial_table.grid(row=0, column=0, sticky="nsew")
        table_sb.grid(row=0, column=1, sticky="ns")

        self._participant_list.bind("<<ListboxSelect>>", self._on_participant_selection_changed)

    # ------------------------------------------------------------------
    # Trial table: view-switch + selection handling
    # ------------------------------------------------------------------
    def _switch_to_table_view(self) -> None:
        self._viewer_canvas.grid_remove()
        self._viewer_vbar.grid_remove()
        self._viewer_hbar.grid_remove()
        self._table_frame.grid(row=0, column=0, sticky="nsew")

    def _switch_to_figure_view(self) -> None:
        self._table_frame.grid_remove()
        self._viewer_canvas.grid()
        self._viewer_vbar.grid()
        self._viewer_hbar.grid()

    def _on_participant_selection_changed(self, event=None) -> None:
        # Bumped on every call, regardless of outcome (zero/multi selection,
        # a busy-rejected change, or a valid single selection) -- a slow,
        # still-in-flight job must never repopulate the table after the
        # selection that started it no longer applies.
        self._table_request_id += 1
        request_id = self._table_request_id
        if self._busy:
            return
        sel = self._participant_list.curselection()
        if len(sel) != 1:
            self._switch_to_figure_view()
            self._trial_table.delete(*self._trial_table.get_children())
            self._table_row_meta = {}
            self.btn_toggle_excluded.config(state="disabled")
            # No worker is being started for this (newly bumped) request_id --
            # any in-flight worker from a PREVIOUS selection will still post
            # its result eventually, but it'll be discarded as stale by
            # request_id, so the polling chain has nothing left to wait for.
            self._table_job_pending = False
            return
        self._switch_to_table_view()
        self._start_table_load(sel[0], request_id)

    def _start_table_load(self, idx: int, request_id: int) -> None:
        pid = list(self._participants.keys())[idx]
        self._trial_table.delete(*self._trial_table.get_children())
        self._table_row_meta = {}
        self.btn_toggle_excluded.config(state="disabled")
        self.status_var.set(f"Loading trials for P{pid}...")
        self._table_job_pending = True
        threading.Thread(target=self._table_worker, args=(pid, request_id), daemon=True).start()
        # Only start a new polling chain if none is active -- rapid
        # re-selection would otherwise spawn one self.after(150, ...) chain
        # per selection, none of which ever terminates on its own once a
        # later chain wins the race to consume the matching result (each
        # checks the queue independently; an already-empty chain would just
        # keep rescheduling itself forever). One chain is enough: it always
        # compares against the current self._table_request_id, not whichever
        # request started it.
        if not self._table_polling:
            self._table_polling = True
            self.after(150, self._poll_table_queue)

    def _table_worker(self, pid: str, request_id: int) -> None:
        try:
            records = [r for r in _report.discover_all_trials(include_excluded=True)
                       if r["participant"] == pid]
            dupes = _report.duplicate_trial_keys(records)
            rows = []
            unscored = 0
            for r in records:
                n = phi = area = None
                if _PT_AVAIL:
                    try:
                        t, angle = load_optitrack(r["path"])
                    except Exception:
                        t = angle = None
                    if t is not None:
                        try:
                            params = compute_pt_params(t, angle)
                        except Exception:
                            params = None
                        if params:
                            n = params.get("N")
                            phi = params.get("phi_max_ratio")
                            area = params.get("area_ratio")
                        else:
                            unscored += 1
                    else:
                        unscored += 1
                rows.append((r, n, phi, area))
            self._table_queue.put(("ok", (request_id, rows, dupes, unscored), None))
        except Exception as e:
            self._table_queue.put(("error", (request_id, str(e)), None))

    @staticmethod
    def _fmt_metric(value, decimals: int) -> str:
        if value is None:
            return "N/A"
        try:
            f = float(value)
        except (TypeError, ValueError):
            return "N/A"
        if not math.isfinite(f):
            return "N/A"
        return f"{f:.{decimals}f}"

    def _poll_table_queue(self) -> None:
        try:
            status, payload, _ = self._table_queue.get_nowait()
        except queue.Empty:
            if not self._table_job_pending:
                # Nothing left to wait for: no worker is running for the
                # current request_id (the operator moved to a zero/multi
                # selection, or a load already completed), and the queue is
                # drained. Stop rescheduling -- an idle panel must not poll
                # every 150ms forever. _start_table_load()'s own guard
                # restarts a fresh chain the next time a job is actually
                # started.
                self._table_polling = False
                return
            self.after(150, self._poll_table_queue)
            return

        request_id = payload[0]
        if request_id != self._table_request_id:
            # Superseded by a newer selection/reload -- discard, but keep
            # the chain alive since the current request's result may still
            # be sitting behind this one in the queue.
            self.after(150, self._poll_table_queue)
            return

        self._table_polling = False   # this chain's job is done either way below
        self._table_job_pending = False   # the result we were waiting for has arrived

        if status == "error":
            self.status_var.set(f"Failed to load trials: {payload[1]}")
            return

        _, rows, dupes, unscored = payload
        self._table_dupes = dupes
        self.btn_toggle_excluded.config(state="normal" if rows else "disabled")

        # Deliberate deviation from spec §4's "one row of explanatory text
        # instead of per-trial N/A spam" when _PT_AVAIL is False: the shipped
        # behavior is the full per-trial row list (all metrics N/A, rows still
        # selectable/toggleable), because the operator still needs to see and
        # act on the trial list when scoring can't run -- exclusion decisions
        # don't depend on the PT metrics being computable. Since
        # _fmt_metric(None, d) already returns "N/A" for every metric in that
        # case, the two branches produced byte-identical rows; only the status
        # message differs, so only the status message branches here.
        for r, n, phi, area in rows:
            # "duplicate" listed before "excluded" so ttk's tag-priority
            # resolution (first tag with a given option wins) gives a
            # both-excluded-and-duplicate row the amber warning color, not
            # the muted excluded grey -- the duplicate-key collision is the
            # more actionable signal to notice at a glance.
            tags = []
            warn = r["trial_key"] in dupes
            if warn:
                tags.append("duplicate")
            if r["excluded"]:
                tags.append("excluded")
            item = self._trial_table.insert(
                "", "end",
                values=("⚠" if warn else "", r["leg"], r["condition"], r["trial"],
                        self._fmt_metric(n, 1), self._fmt_metric(phi, 3), self._fmt_metric(area, 3)),
                tags=tuple(tags))
            self._table_row_meta[item] = r
        if not _PT_AVAIL:
            self.status_var.set(f"{len(rows)} trial(s) loaded (scoring unavailable — "
                                f"compute_pt_params/load_optitrack failed to import).")
        elif unscored:
            self.status_var.set(f"{len(rows)} trial(s) loaded ({unscored} unscored).")
        else:
            self.status_var.set(f"{len(rows)} trial(s) loaded.")

    def _on_toggle_excluded(self) -> None:
        if self._busy:
            return
        items = self._trial_table.selection()
        if not items:
            messagebox.showinfo(
                "Select Trials",
                "Select one or more trial rows in the table before toggling.")
            return
        records = [self._table_row_meta[i] for i in items if i in self._table_row_meta]
        states = {r["excluded"] for r in records}
        if len(states) > 1:
            messagebox.showinfo(
                "Mixed Selection",
                "Selected rows are a mix of excluded and included trials. "
                "Select rows that are all in the same current state before toggling.")
            return
        currently_excluded = states.pop()
        new_excluded = not currently_excluded
        keys = list(dict.fromkeys(r["trial_key"] for r in records))   # dedupe, preserve order

        dupe_keys = [k for k in keys if k in self._table_dupes]
        if dupe_keys:
            lines = [f"{k} -> {len(self._table_dupes[k])} files: {', '.join(self._table_dupes[k])}"
                     for k in dupe_keys]
            if not messagebox.askyesno(
                    "Duplicate Trial Keys",
                    "The following trial_key(s) map to more than one file. Toggling "
                    "affects every trial sharing that key.\n\n" + "\n".join(lines) +
                    "\n\nContinue?"):
                return

        self._busy = True
        self.btn_toggle_excluded.config(state="disabled")
        self.btn_generate.config(state="disabled")
        # Same reason as _on_generate: the busy flag alone can't stop Tk from
        # moving the selection highlight before the handler runs, so the
        # listbox itself is locked for the whole write window. _end_busy()
        # re-enables it on every exit path below.
        self._participant_list.config(state="disabled")
        try:
            _report.set_trials_excluded(keys, new_excluded)
        except _report.RegistryCorruptError as e:
            self.status_var.set(
                f"excluded_trials.json is corrupt: {e} — fix or restore it by hand before trying again.")
            self._end_busy()
            return
        except Exception as e:
            self.status_var.set(f"Failed to toggle exclusion: {e}")
            self._end_busy()
            return

        # Post-write. Both halves below are load-bearing, despite looking
        # redundant:
        #   1. The in-place row-tag update is exactly what the
        #      RegistryCorruptError test proves did NOT happen when the write
        #      raised -- rows must only change after a confirmed successful
        #      save, so it can't be folded into the reload below.
        #   2. The full _refresh_participants_preserving_selection() reload is
        #      what catches UNSELECTED sibling rows that share a toggled
        #      trial_key (a duplicate-key collision toggles every file under
        #      that key, but only the selected rows are visited by the loop).
        try:
            for item in items:
                rec = self._table_row_meta.get(item)
                if rec is None:
                    continue
                rec["excluded"] = new_excluded
                tags = list(self._trial_table.item(item, "tags"))
                if new_excluded and "excluded" not in tags:
                    tags.append("excluded")
                elif not new_excluded and "excluded" in tags:
                    tags.remove("excluded")
                self._trial_table.item(item, tags=tuple(tags))
        finally:
            # The registry write already succeeded by this point; a raise here
            # (e.g. a TclError on a torn-down widget) must not leave _busy set
            # and every button disabled forever.
            self._end_busy()
        self._refresh_participants_preserving_selection()

    def _refresh_participants_preserving_selection(self) -> None:
        # INVARIANT: this relies on Tk's Listbox.delete()/.selection_set() NOT
        # firing <<ListboxSelect>> on this build -- only user-driven selection
        # changes generate that event. If that ever changed, the _refresh /
        # selection_set below would re-enter _on_participant_selection_changed
        # and double-load the table (two workers, two request-id bumps) on
        # every toggle.
        sel = self._participant_list.curselection()
        selected_pid = list(self._participants.keys())[sel[0]] if len(sel) == 1 else None
        self._refresh_participants()
        if selected_pid is not None and selected_pid in self._participants:
            idx = list(self._participants.keys()).index(selected_pid)
            self._participant_list.selection_set(idx)
            self._table_request_id += 1
            self.btn_toggle_excluded.config(state="disabled")
            self._start_table_load(idx, self._table_request_id)

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
            self._participants = _report.list_participants(include_excluded=True)
        except Exception as e:
            self.status_var.set(f"Scan failed: {e}")
            return
        self._participant_list.delete(0, "end")
        for pid, info in self._participants.items():
            # n_trials == 0 with include_excluded=True means every one of
            # this participant's trials is excluded (Task 1) -- they'd
            # otherwise vanish from this list with no way to re-select and
            # undo it (design spec Section 4).
            if info["n_trials"] == 0:
                self._participant_list.insert("end", f"P{pid}  (all excluded)")
                continue
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
        if self._busy:
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
        fully_excluded = [pid for pid in selected if self._participants[pid]["n_trials"] == 0]
        if fully_excluded:
            messagebox.showinfo(
                "No Data To Generate",
                f"Participant(s) {', '.join(fully_excluded)} have every trial "
                "excluded -- there's nothing to generate a figure from. "
                "Re-select participant(s) with at least one included trial.")
            return

        self._busy = True
        self.btn_generate.config(state="disabled")
        self.btn_toggle_excluded.config(state="disabled")
        # The _busy check in _on_participant_selection_changed only stops the
        # handler from ACTING -- Tk has already moved the highlight by the time
        # it runs. Disabling the listbox itself is what actually prevents the
        # desync spec §4's busy lock exists for: an operator clicking another
        # participant mid-Generate, seeing the highlight move, and then getting
        # the PREVIOUS participant's figure against the new highlight.
        self._participant_list.config(state="disabled")
        self.status_var.set("Working — scoring trials, this can take a bit...")
        methodologies = tuple(m for m, v in
                              (("mediapipe", self._use_mediapipe.get()), ("imu", self._use_imu.get()))
                              if v)
        threading.Thread(target=self._generate_worker, args=(ft, selected, methodologies),
                         daemon=True).start()
        self.after(150, self._poll_result)

    def _end_busy(self) -> None:
        """The single place the busy lock is released. Re-enabling Generate
        lives here (rather than at each call site) so no future call site can
        forget it and leave the panel permanently frozen."""
        self._busy = False
        self.btn_generate.config(state="normal")
        # Re-enable the participant listbox: it is disabled for the whole busy
        # window so Tk can't move the selection highlight underneath an
        # in-flight Generate (see _on_generate).
        self._participant_list.config(state="normal")
        sel = self._participant_list.curselection()
        self.btn_toggle_excluded.config(
            state="normal" if len(sel) == 1 and self._trial_table.get_children()
            else "disabled")

    def _generate_worker(self, ft: str, pids: list, methodologies: tuple) -> None:
        # Only the data collection (re-reading and re-scoring every trial CSV
        # for the selected participants) happens here. Figure construction is
        # deliberately left to _poll_result on the Tk main thread: pt_report_common's
        # make_*_figure functions call plt.subplots(), which under the TkAgg
        # backend (active because this module imports tkinter) creates real
        # Tk widgets -- unsafe to do from a background thread.
        try:
            if ft == "full_report":
                by_leg_tp, tps = _report.collect_participant(pids[0])
                # make_report_figure()'s Row 1 (HPE/IMU overlay), Row 4
                # (RMSE bars), and Row 5 (per-source paired PT7) all read
                # rec["mediapipe_curve"]/rec["mediapipe_rmse"] -- fields
                # only attach_rmse() ever sets. It must run before
                # make_report_figure() (make_rmse_figure calls it
                # internally, but make_report_figure does not) -- otherwise
                # those rows silently render empty even when real
                # MediaPipe/IMU data exists (matches run_pt_analysis.py's
                # own ordering for the same reason).
                _report.attach_rmse(by_leg_tp)
                data = (by_leg_tp, tps)
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
            self._end_busy()
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
            self._end_busy()
            self.status_var.set(f"Failed: {e}")
            messagebox.showerror("Generation Failed", str(e))
            return

        self._end_busy()
        self._last_out_path = out_path
        self._switch_to_figure_view()
        self.btn_toggle_excluded.config(state="disabled")
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
    controller: App instance -- receives on_back_to_mode_select().

    The form and the validation dashboard (the boxplot/heatmap/ROC figure,
    plus Export) are two internally-toggled sub-frames of this one panel,
    not two separate App-level modes -- "View Validation Dashboard" /
    "← Back to Entry" swap _form_frame and _dashboard_frame's pack() state.
    Kept as one panel (rather than a second App._enter_*_mode) so every
    attribute an existing caller/test already reaches through
    (canvas_frame, canvas_placeholder, export_btn, _current_canvas,
    _last_valid, _last_stats, refresh(), _on_export_clicked()) stays at the
    same app._mas_entry.<name> path it always has -- only which sub-frame
    is visible changes, not where the dashboard's state lives."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])
        self._form_frame = tk.Frame(self, bg=ws.PALETTE["BG"])
        self._dashboard_frame = tk.Frame(self, bg=ws.PALETTE["BG"])
        self._build_form(self._form_frame)
        self._build_dashboard(self._dashboard_frame)
        self._form_frame.pack(fill="both", expand=True)

    def _build_form(self, parent) -> None:
        import datetime as _datetime
        pad = {"padx": 12, "pady": 5}

        hdr = tk.Frame(parent, bg=ws.PALETTE["BG"])
        hdr.pack(fill="x", padx=12, pady=(16, 4))
        ws.secondary_button(
            hdr, "← Mode Select", self.controller.on_back_to_mode_select
        ).pack(side="left", padx=(0, 8))
        tk.Label(hdr, text="Pendulastic — MAS Score Entry",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=10, pady=4)

        form = tk.Frame(parent, bg=ws.PALETTE["BG"])
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

        # Optional, direction-specific grades (design spec
        # docs/superpowers/specs/2026-08-18-mas-flexion-extension-design.md)
        # -- unlike MAS Grade above, these start blank and stay optional, so
        # each combobox gets a leading blank choice meaning "not assessed."
        tk.Label(form, text="MAS Flexion:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=6, column=0, sticky="e", **pad)
        self.mas_flexion_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_flexion_var, width=19,
                    state="readonly",
                    values=[""] + list(_mas_validation.MAS_ORDER)).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Label(form, text="MAS Extension:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=7, column=0, sticky="e", **pad)
        self.mas_extension_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_extension_var, width=19,
                    state="readonly",
                    values=[""] + list(_mas_validation.MAS_ORDER)).grid(
            row=7, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed By:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=8, column=0, sticky="e", **pad)
        self.assessed_by_var = tk.StringVar()
        tk.Entry(form, textvariable=self.assessed_by_var, width=22).grid(
            row=8, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed Date:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=9, column=0, sticky="e", **pad)
        self.assessed_date_var = tk.StringVar(
            value=_datetime.date.today().isoformat())
        tk.Entry(form, textvariable=self.assessed_date_var, width=22).grid(
            row=9, column=1, sticky="w", **pad)

        tk.Label(form, text="Notes:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=10, column=0, sticky="ne", **pad)
        self.notes_text = tk.Text(form, height=3, width=22, wrap="word",
                                  bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG"])
        self.notes_text.grid(row=10, column=1, sticky="w", **pad)

        # Single feedback channel for the form: errors in amber, save
        # confirmations in green (see _set_feedback).
        self.error_var = tk.StringVar(value="")
        self.error_label = tk.Label(parent, textvariable=self.error_var,
                                    fg=_ERROR_FG, bg=ws.PALETTE["BG"])
        self.error_label.pack(fill="x", padx=12, pady=(0, 4))

        ws.primary_button(parent, "Save", self._on_save_clicked).pack(pady=(0, 8))

        status_frame = tk.Frame(parent, bg=ws.PALETTE["BG"])
        status_frame.pack(fill="x", padx=12, pady=(4, 8))
        self.status_text = tk.Text(status_frame, height=4, wrap="word",
                                   state="disabled", bg=ws.PALETTE["SURFACE"],
                                   fg=ws.PALETTE["FG"])
        status_scroll = tk.Scrollbar(status_frame, command=self.status_text.yview)
        self.status_text.configure(yscrollcommand=status_scroll.set)
        self.status_text.pack(side="left", fill="x", expand=True)
        status_scroll.pack(side="right", fill="y")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=10, pady=4)

        self.dashboard_btn = ws.secondary_button(
            parent, "View Validation Dashboard →", self._show_dashboard)
        self.dashboard_btn.pack(pady=(4, 16))

    def _build_dashboard(self, parent) -> None:
        hdr = tk.Frame(parent, bg=ws.PALETTE["BG"])
        hdr.pack(fill="x", padx=12, pady=(16, 4))
        ws.secondary_button(hdr, "← Back to Entry", self._show_form).pack(
            side="left", padx=(0, 8))
        tk.Label(hdr, text="Pendulastic — MAS Validation Dashboard",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        ttk.Separator(parent, orient="horizontal").pack(fill="x", padx=10, pady=4)

        self._current_canvas = None
        self._current_fig = None
        self._last_valid: list = []
        self._last_stats = None

        self.canvas_frame = tk.Frame(parent, bg=ws.PALETTE["SURFACE"])
        self.canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.canvas_placeholder = tk.Label(
            self.canvas_frame,
            text="No MAS-scored trials with matching trial data yet",
            bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG2"])
        self.canvas_placeholder.pack(pady=40)

        self.export_btn = ws.secondary_button(parent, "Export", self._on_export_clicked)
        self.export_btn.config(state="disabled")
        self.export_btn.pack(pady=(0, 12))

    def _show_dashboard(self) -> None:
        self.refresh()
        self._form_frame.pack_forget()
        self._dashboard_frame.pack(fill="both", expand=True)

    def _show_form(self) -> None:
        self._dashboard_frame.pack_forget()
        self._form_frame.pack(fill="both", expand=True)

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
            "stronger_leg": self.stronger_leg_var.get().strip().lower(),
            "notes": self.notes_text.get("1.0", "end").strip(),
            "mas_flexion": self.mas_flexion_var.get().strip(),
            "mas_extension": self.mas_extension_var.get().strip(),
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
        # and clear the fields that must change between consecutive rows so a
        # resubmit takes a deliberate re-selection: mas_grade (existing) and
        # notes (specific to this one observation) -- unlike stronger_leg,
        # which typically holds across both legs' rows for the same session.
        self._set_feedback(f"Saved {participant} {row['leg']} / {mas_grade}.", ok=True)
        self.mas_grade_var.set("")
        self.mas_flexion_var.set("")
        self.mas_extension_var.set("")
        self.notes_text.delete("1.0", "end")
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
        self._session_trials: list     = []
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
            self._start_video_file_processing(meta)
            return   # video_file is standalone — no live recording

        # enter_recording() runs BEFORE starting the individual sources so
        # that a non-blocking status message a source sets on failure (e.g.
        # _start_rgb_recording()'s "no camera" notice) is the last thing
        # written to status_var, instead of being immediately overwritten by
        # enter_recording()'s own "RECORDING…" text.
        self._state = "recording"
        self._acq.enter_recording()

        # OptiTrack is triggered last, regardless of its position in
        # `sources`: motive_sync's remote-command handshake blocks for
        # several hundred ms, and every other source should already be
        # capturing before that delay is incurred -- matches master_app.py,
        # which always starts the webcam + IMU log before calling
        # motive_sync (see its start_recording()). Triggering it earlier
        # (e.g. when "optitrack" precedes "rgb" in `sources`) pushes back
        # RGB's actual capture start by the same amount, producing an
        # IMU/RGB skew that master_app.py never has.
        for src in sources:
            if src == "imu":
                self._start_imu_recording(meta)
            elif src == "rgb":
                self._start_rgb_recording(meta)
        if "optitrack" in sources:
            self._start_optitrack_recording(meta)

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

        meta         = self._acq.get_metadata()
        multi_trial  = self._is_multi_trial_mode()
        entry: Optional[dict] = None
        if multi_trial:
            entry = {
                "trial_num": meta["trial"],
                "sources": list(self._active_sources),
                "status": "processing",
                "meta": meta,
                "source_angles": None,
                "fps": None,
                "base_filename": None,
                "file_paths": self._trial_file_paths(meta, self._active_sources),
            }
            self._session_trials.append(entry)
            self._acq.set_multi_trial_list(self._session_trials_view())
        source_angles: dict = {}
        pending_rgb    = False
        video_path: Optional[str] = None
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
                video_path = self._video_path
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

        if multi_trial:
            # Batch recording: the raw/untuned data is already safely on
            # disk above. Don't run MediaPipe tracking or the IMU auto-tune
            # grid search now -- both take real time (a minute or more) and
            # used to lock the whole form for that whole span, so back-to-back
            # trials couldn't be recorded without waiting between every one.
            # Instead, stash what's needed and defer to _run_batch_processing,
            # which runs once when the clinician leaves the batch (see
            # on_back_to_mode_select) rather than once per trial.
            entry["source_angles"] = source_angles
            entry["fps"]           = self._fps_for(meta)
            entry["base_filename"] = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
            if pending_rgb:
                entry["pending_rgb_path"] = video_path
            elif pending_imu_tune:
                entry["pending_imu_tune"] = {
                    "raw_log_path":  imu_raw_log_path,
                    "csv_path":      imu_csv_path,
                    "csv_filename":  fn_imu,
                }
            else:
                entry["status"] = "saved"
            self._acq.set_multi_trial_list(self._session_trials_view())
            self._acq.increment_trial()
            self._acq.enter_idle()
            self._state = "idle"
            return

        if pending_rgb:
            self._state = "processing"
            self._acq.enter_processing()
            threading.Thread(
                target=self._run_rgb_processing_async,
                args=(video_path, meta, dict(source_angles)), daemon=True,
            ).start()
        elif pending_imu_tune:
            self._state = "processing"
            # No MediaPipe involved here -- this is the IMU-only auto-tuning
            # grid search (imu_calibration_tuner), not RGB/video HPE. The
            # default message misleadingly claimed MediaPipe was running for
            # a pure-IMU trial.
            self._acq.enter_processing("Tuning IMU calibration…")
            threading.Thread(
                target=self._run_imu_tuning_async,
                args=(imu_raw_log_path, imu_csv_path, fn_imu, meta,
                      dict(source_angles)), daemon=True,
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
        if isinstance(self._camera, PhoneCameraSession):
            # Rescan on the phone entry doesn't re-probe hardware — it
            # restarts the stream server for a fresh pairing panel.
            self._switch_to_phone_camera()
            return
        if self._camera is None:
            return
        usb_cams = enumerate_cameras() if _CV2_AVAIL else []
        self._known_cameras = usb_cams + ([PHONE_CAMERA_ENTRY] if _PPS_AVAIL else [])
        self._acq.set_camera_list(self._known_cameras)
        if usb_cams:
            label = self._acq.cam_var.get()
            cam = next((c for c in usb_cams if c["label"] == label), usb_cams[0])
            self._camera.open(cam)
        else:
            self._acq.set_camera_live(False)

    def on_camera_selected(self, label: str) -> None:
        if self._state == "recording":
            return
        cam = next((c for c in self._known_cameras if c["label"] == label), None)
        if cam is None:
            return
        if cam.get("kind") == "phone":
            self._switch_to_phone_camera()
        else:
            self._switch_to_usb_camera(cam)

    def _switch_to_usb_camera(self, cam: dict) -> None:
        if self._camera is not None and self._camera.active is not None \
                and self._camera.active.get("label") == cam["label"] \
                and isinstance(self._camera, CameraSession):
            return   # already using this camera
        if not isinstance(self._camera, CameraSession):
            if self._camera is not None:
                self._camera.close()
            self._acq.hide_phone_pairing_panel()
            self._camera = CameraSession(
                on_frame=self._on_camera_frame, on_status=self._on_camera_status)
        self._camera.open(cam)

    def _switch_to_phone_camera(self) -> None:
        if self._camera is not None:
            self._camera.close()
        self._camera = PhoneCameraSession(
            on_frame=self._on_camera_frame, on_status=self._on_camera_status)
        self._camera.open(PHONE_CAMERA_ENTRY)
        ips = _pps.get_all_local_ips() if _PPS_AVAIL else ["127.0.0.1"]
        primary_ip = ips[0]
        port = getattr(_pps, "PORT_STREAM_HTTPS", 8880)
        url = f"https://{primary_ip}:{port}/"
        self._acq.show_phone_pairing_panel(url)

    def on_imu_browser_toggled(self) -> None:
        if self._acq._src_imu_browser.get():
            ip, port = _pps.start_imu_stream_server()
            self._acq.show_phone_pairing_panel(f"https://{ip}:{port}/")
        else:
            _pps.stop_imu_stream_server()
            self._acq.hide_phone_pairing_panel()

    def on_camera_disabled(self) -> None:
        if self._camera is not None:
            self._camera.close()
        self._acq.set_camera_live(False)
        self._acq.hide_phone_pairing_panel()

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
        # Always reset to idle on entry -- guarantees a fully usable screen
        # (unlocked form, enabled START, fresh status text) regardless of
        # whatever state the panel was left in previously, rather than
        # silently inheriting it.
        self._acq.enter_idle()
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
        self._mas_entry._show_form()
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

                    try:
                        self._workbench_raw_diagnostics = _wb_engine.compute_raw_sensor_diagnostics(
                            components["accel"]["path"])
                    except Exception:
                        pass   # supplementary cross-check only -- never blocks the trial load
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
        self._workbench_view.reset_for_new_trial()
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
        """Leaving the acquisition screen. In multi-trial mode this is also
        the natural point to run whatever MediaPipe tracking / IMU auto-tune
        passes were deferred during the batch (see on_stop()) -- once per
        batch instead of once per trial. If nothing is queued, this is just
        the plain navigation it always was."""
        queued = [e for e in self._session_trials if e["status"] == "processing"]
        if queued:
            self._run_batch_processing(queued)
            return
        self._finish_back_to_mode_select()

    def _run_batch_processing(self, queued: list) -> None:
        self._state = "batch_processing"
        self._acq.enter_processing(f"Processing batch: trial 1 of {len(queued)}…")
        threading.Thread(
            target=self._batch_processing_worker, args=(queued,), daemon=True,
        ).start()

    def _batch_processing_worker(self, queued: list) -> None:
        """Runs the deferred RGB/IMU-tune pass for each queued trial in turn.
        Sequential on purpose -- these entries' recordings already finished,
        so there's nothing left to overlap with, and running one at a time
        avoids reasoning about concurrent MediaPipe/AHRS passes."""
        total = len(queued)
        for i, entry in enumerate(queued, start=1):
            def set_status(msg):
                self.after(0, lambda: self._acq.status_var.set(msg))
            set_status(f"Processing batch: trial {i} of {total}…")
            base = entry["source_angles"] or {}
            try:
                if "pending_rgb_path" in entry:
                    def progress(pct):
                        set_status(f"Processing batch: trial {i} of {total} "
                                   f"({int(pct * 100)}%)…")
                    result = self._compute_rgb_processing(
                        entry["pending_rgb_path"], entry["meta"], base, progress)
                elif "pending_imu_tune" in entry:
                    p = entry["pending_imu_tune"]
                    result = self._compute_imu_tuning(
                        p["raw_log_path"], p["csv_path"], p["csv_filename"],
                        entry["meta"], base)
                else:
                    result = base
            except Exception:
                # One trial's MediaPipe/save_trial call raising must not kill
                # the whole worker thread -- self.after(0, self._finish_batch_
                # processing) below would never fire, leaving every remaining
                # queued trial stuck at status="processing" and the form
                # permanently locked. Fall back to this entry's originally
                # -recorded series, matching _compute_imu_tuning's own
                # never-raise contract.
                result = base
            entry["source_angles"] = result
            entry["status"] = "saved"
        self.after(0, self._finish_batch_processing)

    def _finish_batch_processing(self) -> None:
        self._acq.set_multi_trial_list(self._session_trials_view())
        self._finish_back_to_mode_select()

    def _finish_back_to_mode_select(self) -> None:
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
        self._session_trials  = []
        self._acq.set_multi_trial_list([])

    def _start_upload_analysis(self) -> None:
        meta = self._upload_meta.get_metadata()
        if not meta.get("pid", "").strip():
            messagebox.showerror("Metadata", "Participant ID cannot be empty.")
            return
        path = self._upload_meta._file_path
        if not path:
            messagebox.showerror("Metadata", "No file selected.")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in (".mp4", ".avi", ".mov", ".mkv"):
            if not _VIEWER_AVAIL:
                messagebox.showerror(
                    "HPE Unavailable",
                    "pendulastic_viewer not importable — cannot run MediaPipe.")
                return
            self._state = "upload_processing"
            self._upload_meta.set_processing(True)
            self._upload_meta.status_var.set("Detecting people…")
            self.update_idletasks()
            ok, manual_seed = self._detect_and_seed_video(
                path, meta.get("leg", "right"))
            if not ok:
                self._upload_meta.status_var.set(
                    "Upload cancelled — no patient selected.")
                self._upload_meta.set_processing(False)
                self._state = "upload_meta"
                return

            self._upload_meta.status_var.set("HPE processing: 0%")
            threading.Thread(
                target=self._run_video_file_hpe,
                args=(path, meta),
                kwargs={"progress_target": self._upload_meta.status_var,
                        "manual_seed": manual_seed},
                daemon=True,
            ).start()
        else:
            self._state = "upload_processing"
            self._upload_meta.set_processing(True)
            self._upload_meta.status_var.set("Processing...")
            threading.Thread(
                target=self._run_csv_analysis,
                args=(path, meta),
                daemon=True,
            ).start()

    def _detect_and_seed_video(self, path: str, leg: str) -> tuple:
        """Probe the first frame of `path` for people and resolve a manual
        tracking seed on the main thread -- opens PersonPickerDialog for 2+
        candidates via wait_window(), so this must only be called from the
        main thread, before any background tracking is started.

        Returns (ok, manual_seed). ok is False when nobody was found (an
        error dialog is shown) or the user cancelled the picker -- callers
        must not proceed to tracking in that case. manual_seed may be None
        even when ok is True (single candidate whose ankle wasn't resolved
        cleanly) -- run_offline_track then falls back to its own per-frame
        auto-detection, matching the pre-existing behavior for that case.
        """
        engine = BiomechanicalEngine("rgb")
        frame, poses = engine.detect_people_at_frame(path)
        if not poses:
            messagebox.showerror(
                "HPE Upload",
                "No person detected in the video — check the recording or "
                "try a different file.")
            return False, None
        if len(poses) == 1:
            # Only one candidate -- resolve_person_click's nearest-pose
            # search trivially picks it regardless of click position.
            fh, fw = frame.shape[:2]
            result = resolve_person_click(poses, (fw / 2, fh / 2), fw, fh, leg)
            manual_seed = result if result is not None and result[2] is not None else None
            return True, manual_seed
        dialog = PersonPickerDialog(self, path, 0, frame, poses, leg)
        self.wait_window(dialog)
        if dialog.result is None:
            return False, None
        return True, dialog.result

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
                            (k for k in ("time_s", "time_sec", "t_rel")
                             if k in row), None)
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
        if not _VIEWER_AVAIL:
            messagebox.showerror(
                "HPE Unavailable",
                "pendulastic_viewer not importable — cannot run MediaPipe.")
            return
        self._acq.status_var.set("Detecting people…")
        self.update_idletasks()
        ok, manual_seed = self._detect_and_seed_video(
            path, meta.get("leg", "right"))
        if not ok:
            self._acq.status_var.set(
                "Video File cancelled — no patient selected.")
            return
        self._state = "processing"
        self._acq.enter_processing()
        threading.Thread(
            target=self._run_video_file_hpe,
            args=(path, meta),
            kwargs={"manual_seed": manual_seed},
            daemon=True,
        ).start()

    def _run_video_file_hpe(self, path: str, meta: dict,
                             progress_target: Optional[tk.StringVar] = None,
                             manual_seed: tuple | None = None) -> None:
        target = progress_target or self._acq.status_var
        def progress(pct: float) -> None:
            self.after(0, lambda p=pct: target.set(
                f"HPE processing: {int(p * 100)}%"))

        try:
            leg    = meta.get("leg", "right").lower()
            engine = BiomechanicalEngine("rgb")
            angles, landmarks, video_fps, detected = engine.run_offline_track(
                path, progress, leg=leg, collect_landmarks=True,
                manual_seed=manual_seed)
            self.after(0, lambda: self._finish_video_file_hpe(
                path, meta, angles, landmarks, video_fps, detected, engine,
                progress_target))
        except Exception as exc:
            def _err_video(msg=str(exc)):
                target.set(f"Error processing video: {msg}")
                self._reset_video_file_processing_state(progress_target)
            self.after(0, _err_video)

    def _reset_video_file_processing_state(
            self, progress_target: Optional[tk.StringVar]) -> None:
        """progress_target is only passed for the upload_meta ("Upload
        File" -> new trial) entry point; the video_file acquisition-source
        entry point (_start_video_file_processing) passes None, so this
        resets whichever screen actually kicked off the run instead of
        leaving the app stuck mid-"processing" with no way back."""
        if progress_target is not None:
            self._upload_meta.set_processing(False)
            self._state = "upload_meta"
        else:
            self._state = "idle"
            self._acq.enter_idle()

    def _finish_video_file_hpe(self, path: str, meta: dict, angles: list,
                                landmarks: list, video_fps: float,
                                detected: list, engine,
                                progress_target: Optional[tk.StringVar]) -> None:
        """Runs on the main thread (scheduled via self.after) so it's safe
        to open AnnotatedVideoReviewDialog's wait_window() here.

        detected[i] is only True where run_offline_track actually found and
        accepted a pose for the tracked person that frame -- angles/landmarks
        stay populated (frozen at the last known position) even when nobody
        was detected, so `not any(detected)` is the only reliable "no person
        anywhere in this video" signal; an empty-but-not-NaN angles list
        would silently pass a naive `if not angles` check."""
        target = progress_target or self._acq.status_var
        if not angles or not any(detected):
            target.set(
                "Error: no person detected in video — check the recording "
                "or leg selection.")
            self._reset_video_file_processing_state(progress_target)
            return

        review_error = None
        if landmarks and AnnotatedVideoReviewDialog is not None:
            try:
                dialog = AnnotatedVideoReviewDialog(
                    self, path, angles, landmarks, video_fps or 30.0,
                    meta.get("leg", "right"), engine)
                self.wait_window(dialog)
                angles = dialog.angles
                landmarks = dialog.landmarks
            except Exception as exc:
                # Don't let a per-call dialog failure discard the tracking
                # run that already completed -- fall through with the
                # original angles/landmarks the run produced.
                review_error = exc

        fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"],
            meta["trial"], source="video_file")
        DataManager.save_trial(fn, angles, meta, fps=video_fps or 30.0,
                               source="video_file")

        source_angles = {"video_file": angles}
        self._transition_to_review(source_angles, meta)
        if review_error is not None:
            self._acq.status_var.set(
                f"Video review unavailable: {review_error} -- showing "
                "results without review.")

    def _start_rgb_recording(self, meta: dict) -> None:
        # Note: this runs from on_start()'s per-source dispatch loop, AFTER
        # self._acq.enter_recording() -- a blocking messagebox here used to
        # freeze the whole app mid-transition into recording, with the
        # countdown's camera-preview window already open on top and no
        # visible way to dismiss it (see the "camera view keeps crashing"
        # regression covering a Phone IMU (browser) trial that leaves the
        # default-checked RGB source active with no working camera). Surface
        # the problem via the non-blocking status line instead.
        if not _CV2_AVAIL:
            self._acq.status_var.set("RGB skipped: OpenCV (cv2) is not installed.")
            if "rgb" in self._active_sources:
                self._active_sources.remove("rgb")
            self._video_path = ""
            return
        if self._camera is None or self._camera.active is None \
                or self._camera.frame_size is None:
            self._acq.status_var.set(
                "RGB skipped: no camera selected. Click Rescan and pick a camera first.")
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

        self._rgb_ts_path = None
        if isinstance(self._camera, PhoneCameraSession):
            self._rgb_ts_path = self._video_path + ".timestamps.csv"
            self._rgb_ts_file = open(self._rgb_ts_path, "w", newline="", encoding="utf-8")
            self._rgb_ts_writer = csv.writer(self._rgb_ts_file)
            self._rgb_ts_writer.writerow(["frame_index", "desktop_ts_ms"])
            self._camera.attach_timestamp_sink(self._on_phone_frame_timestamp)

    def _on_phone_frame_timestamp(self, frame_index: int, desktop_ts_ms: int) -> None:
        """Runs on PhoneCameraSession's background thread — plain file I/O
        only, never touches Tkinter."""
        try:
            self._rgb_ts_writer.writerow([frame_index, desktop_ts_ms])
        except Exception:
            pass

    def _stop_rgb_recording(self) -> None:
        if isinstance(self._camera, PhoneCameraSession):
            self._camera.detach_timestamp_sink()
        ts_file = getattr(self, "_rgb_ts_file", None)
        if ts_file is not None:
            try:
                ts_file.close()
            except Exception:
                pass
            self._rgb_ts_file = None
        writer = self._camera.detach_writer() if self._camera is not None else None
        if writer is not None:
            writer.release()
        self._rgb_writer = None

    def _compute_imu_tuning(self, raw_log_path: str, csv_path: str,
                            csv_filename: str, meta: dict,
                            source_angles_base: dict) -> dict:
        """Load this trial's raw IMU log, run the grid search, and — only if
        a passing configuration is found — rewrite the trial's saved CSV and
        return the tuned series alongside source_angles_base. Must never
        raise: any failure falls back to the originally-recorded series so
        tuning can never block a clinician from seeing trial data. Pure
        computation — takes every input explicitly instead of reading self
        state, so it's safe to call for any trial (live or deferred/batch)
        regardless of what the app is doing with self._video_path/self._engine
        at the moment it actually runs."""
        source_angles = dict(source_angles_base)
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
            # TypeError/IndexError from deep inside replay_trial. Callers
            # must not let an exception here go uncaught, or (in the live,
            # non-batch path) the self.after transition never fires and the
            # app sits in "processing" forever -- a direct violation of
            # "tuning must never block the clinician from seeing trial data."
            pass   # fall back to the originally-recorded series
        return source_angles

    def _run_imu_tuning_async(self, raw_log_path: str, csv_path: str,
                              csv_filename: str, meta: dict,
                              source_angles_base: dict) -> None:
        """Thread target for the single-trial (non-batch) path: compute then
        hand off to the review screen on the Tk main thread."""
        result = self._compute_imu_tuning(
            raw_log_path, csv_path, csv_filename, meta, source_angles_base)
        self.after(0, lambda: self._transition_to_review(
            result, meta, from_recording=True))

    def _compute_rgb_processing(self, video_path: str, meta: dict,
                                source_angles_base: dict,
                                progress: Optional[Callable[[float], None]] = None
                                ) -> dict:
        """Run MediaPipe offline tracking over an already-recorded video and
        return the tracked series alongside source_angles_base. Pure
        computation — uses its own BiomechanicalEngine instance rather than
        self._engine (which reflects whatever trial is currently live), so
        it's safe to call for a trial other than the one currently being
        recorded."""
        leg    = meta.get("leg", "right").lower()
        fn_rgb = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="rgb")
        engine = BiomechanicalEngine("rgb")
        angles = engine.run_offline_track(video_path, progress or (lambda pct: None), leg=leg)
        DataManager.save_trial(fn_rgb, angles, meta, fps=30.0, source="rgb")

        source_angles = dict(source_angles_base)
        source_angles["rgb"] = angles
        return source_angles

    def _run_rgb_processing_async(self, video_path: str, meta: dict,
                                  source_angles_base: dict) -> None:
        """Thread target for the single-trial (non-batch) path: compute then
        hand off to the review screen on the Tk main thread."""
        def progress(pct: float) -> None:
            self.after(0, lambda p=pct: self._acq.status_var.set(
                f"MediaPipe tracking: {int(p * 100)}%"))
        result = self._compute_rgb_processing(
            video_path, meta, source_angles_base, progress)
        self.after(0, lambda: self._transition_to_review(
            result, meta, from_recording=True))

    def _start_optitrack_recording(self, meta: dict) -> None:
        if _MOTIVE_AVAIL:
            try:
                # relpath must be set or motive_sync.mirror_relpath() warns
                # and skips SetCurrentSession entirely, leaving Motive
                # recording into whatever session it last had open instead
                # of this participant's folder. Mirrors master_app.py's
                # Participant_{pid}/{leg}/{characterization} layout so both
                # apps' OptiTrack_Recordings trees stay compatible.
                rel_path = os.path.join(
                    f"Participant_{meta['pid']}", meta['leg'], meta['ms_status'])
                msg = (f"START|id={meta['pid']}|leg={meta['leg']}|"
                       f"characterization={meta['ms_status']}|"
                       f"trial={meta['trial']}|relpath={rel_path}")
                _motive.start_local_motive(msg)
            except Exception as e:
                messagebox.showwarning(
                    "Motive Sync",
                    f"Could not trigger Motive:\n{type(e).__name__}: {e}\n\n"
                    "Recording will continue without OptiTrack sync.")

    def _is_multi_trial_mode(self) -> bool:
        return self._acq._multi_trial_var.get()

    def _trial_file_paths(self, meta: dict, sources: list) -> list:
        """Candidate file paths that may be written for a trial with these
        sources. For RGB, includes <video>.timestamps.csv even though it is
        only written for phone-camera recordings (not plain USB webcam);
        Task 5's delete logic treats a missing file as a no-op. Returns imu
        and rgb CSV paths + the .avi and .avi.timestamps.csv video paths for
        RGB trials. OptiTrack writes nothing here (Motive owns that file)."""
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        paths = []
        if "imu" in sources:
            fn = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
            paths.append(os.path.join(DataManager.DATA_DIR, fn))
        if "rgb" in sources:
            fn = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="rgb")
            paths.append(os.path.join(DataManager.DATA_DIR, fn))
            video_path = os.path.join(DataManager.DATA_DIR, base_fn.replace(".csv", ".avi"))
            paths.append(video_path)
            paths.append(video_path + ".timestamps.csv")
        return paths

    # ------------------------------------------------------------------
    # Panel switching
    # ------------------------------------------------------------------
    def _transition_to_review(self, source_angles: dict, meta: dict,
                              from_recording: bool = False) -> None:
        """from_recording distinguishes an actual live-recording stop (which
        gets a "Recording Saved" confirmation) from the upload-CSV/
        upload-video-file review paths, which process an already-existing
        file rather than saving a new one. Multi-trial mode never reaches
        this screen automatically from a live recording -- on_stop() returns
        to idle directly instead (see on_stop() and _run_batch_processing)."""
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        self._state = "review"
        self._post.set_back_context(from_trial_list=False)
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

    def _session_trials_view(self) -> list:
        return [{"trial_num": e["trial_num"], "sources": e["sources"], "status": e["status"]}
                for e in self._session_trials]

    def on_delete_trial(self, trial_num: int) -> None:
        entry = next((e for e in self._session_trials
                     if e["trial_num"] == trial_num), None)
        if entry is None:
            return
        errors = []
        for path in entry["file_paths"]:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                errors.append((path, e))
        if errors:
            self._acq.status_var.set(
                f"Trial {trial_num}: could not delete all files "
                f"({os.path.basename(errors[0][0])}: {errors[0][1]}) — not removed.")
            return
        self._session_trials.remove(entry)
        self._acq.set_multi_trial_list(self._session_trials_view())

    def on_view_trial(self, trial_num: int) -> None:
        entry = next((e for e in self._session_trials
                     if e["trial_num"] == trial_num), None)
        if entry is None or entry["status"] != "saved":
            return
        self._state = "review"
        self._post.set_back_context(from_trial_list=True)
        self._post.load_trial(entry["source_angles"], entry["fps"],
                              entry["meta"], entry["base_filename"])
        self._acq.pack_forget()
        self._post.pack(fill="both", expand=True)

    def on_back_to_trial_list(self) -> None:
        self._post.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._acq.enter_idle()
        self._state = "idle"

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
        self.destroy()

    def destroy(self) -> None:
        """Full teardown. Lives here rather than in on_close() so that every
        caller gets it -- on_close() only fires for WM_DELETE_WINDOW, and a
        plain destroy() used to leak the IMU server thread and its port.
        """
        if getattr(self, "_teardown_done", False):
            return
        self._teardown_done = True
        if getattr(self, "_imu_poll_stop", None) is not None:
            self._imu_poll_stop.set()
        if getattr(self, "_imu_poll_thread", None):
            self._imu_poll_thread.join(timeout=0.5)
        if getattr(self, "_camera", None) is not None:
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
        # Drop pending after() callbacks. _tick reschedules itself every 50ms,
        # so without this a timer outlives the interpreter and Tk reports
        # 'invalid command name "..._tick"' on the way out.
        try:
            for aid in self.tk.call("after", "info"):
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
        except Exception:
            pass
        super().destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    App().mainloop()
