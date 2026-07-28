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
# PostProcessingPanel
# ---------------------------------------------------------------------------

class PostProcessingPanel(tk.Frame):
    """
    Full-window post-processing panel: angle curve + PT metrics (rows 0-4).
    rowconfigure(1, weight=1) lets the matplotlib figure expand to fill height.
    """

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller   = controller
        self._angles: list = []
        self._fps: float   = 30.0
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        # row 0 — title (trial filename)
        self.title_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 4))

        # row 1 — matplotlib figure
        if _MPL_AVAIL:
            self._fig    = Figure(figsize=(10, 4), dpi=96, facecolor="#EEF2F7")
            self._ax     = self._fig.add_subplot(111)
            self._canvas = FigureCanvasTkAgg(self._fig, master=self)
            self._canvas.get_tk_widget().grid(
                row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)
        else:
            tk.Label(self, text="matplotlib not available — install it in .venv",
                     fg="red").grid(row=1, column=0, columnspan=2)
            self._canvas = None

        # row 2 — PT Metrics LabelFrame
        mf = tk.LabelFrame(self, text="Popovic Pendulum Test Metrics",
                           font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        mf.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)

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
            tk.Label(mf, text=lbl, font=("Segoe UI", 8), fg="#555").grid(
                row=0, column=col, padx=10, pady=1)
            tk.Label(mf, textvariable=var,
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

        # row 4 — status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w", fg="#333").grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_trial(self, angles: list, fps: float,
                   metadata: dict, filename: str) -> None:
        self._angles = angles
        self._fps    = fps
        self.title_var.set(filename)
        self._plot_curve(angles, fps)
        self._show_pt_metrics(angles, fps)
        self.status_var.set(f"Saved: {filename}")

    def load_optitrack_overlay(self, csv_path: str) -> None:
        if not _PT_AVAIL or load_optitrack is None:
            messagebox.showerror("OptiTrack", "load_optitrack not available.")
            return
        try:
            opti = load_optitrack(csv_path)
            self._plot_curve(self._angles, self._fps, overlay=opti)
            self.status_var.set(f"Overlay: {os.path.basename(csv_path)}")
        except Exception as e:
            messagebox.showerror("OptiTrack Load Error", str(e))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _plot_curve(self, angles: list, fps: float,
                    overlay: list | None = None) -> None:
        if not _MPL_AVAIL or self._canvas is None:
            return
        self._ax.clear()
        times = [i / fps for i in range(len(angles))]
        self._ax.plot(times, angles, color="#2563EB", linewidth=1.5,
                      label="Knee angle")
        if overlay:
            t_ot = [i / fps for i in range(len(overlay))]
            self._ax.plot(t_ot, overlay, color="#16A34A", linewidth=1.5,
                          linestyle="--", label="OptiTrack")
            self._ax.legend(fontsize=8)
        self._ax.set_xlabel("Time (s)", fontsize=9)
        self._ax.set_ylabel("Knee angle (deg)", fontsize=9)
        self._ax.set_title("Popovic Pendulum Test — Knee Angle", fontsize=10)
        self._ax.grid(True, alpha=0.3)
        self._fig.tight_layout()
        self._canvas.draw()

    def _show_pt_metrics(self, angles: list, fps: float) -> None:
        if not _PT_AVAIL or compute_pt_params is None:
            return
        try:
            t   = np.arange(len(angles), dtype=float) / fps
            arr = np.array(angles, dtype=float)
            p   = compute_pt_params(t, arr)
            if p is None:
                self.status_var.set("PT scoring: insufficient data (need >= 40 finite frames).")
                return
            score = compute_pt_score_simple(p)
            mas   = pt_to_mas(score)
            self.a1_var.set(f"{p['A1_deg']:.1f}")
            self.omega_var.set(f"{p['omega_peak_deg_s']:.1f}")
            self.n_var.set(f"{p['N']:.1f}")
            self.f_var.set(f"{p['f']:.2f}")
            self.r2n_var.set(f"{p['R2n']:.3f}")
            self.mas_var.set(str(mas))
            self.score_var.set(f"{score:.3f}")
        except Exception as e:
            self.status_var.set(f"PT scoring error: {e}")

    def _on_new_trial(self) -> None:
        self.controller.on_new_trial()

    def _on_load_optitrack(self) -> None:
        path = filedialog.askopenfilename(
            title="Select OptiTrack CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if path:
            self.load_optitrack_overlay(path)
