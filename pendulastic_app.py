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
