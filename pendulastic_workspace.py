"""
pendulastic_workspace.py
========================
Pendulastic Workspace — Biomechanical Benchmarking GUI
=======================================================

Production-ready CustomTkinter application that handles:
  · Session metadata entry and directory-tree management
  · Live OptiTrack Motive streaming via NatNet SDK (with mock fallback)
  · Thread-safe trial recording → quaternion CSV compatible with analysis_pipeline
  · Full multi-model benchmarking via analysis_pipeline.run_full_pipeline()
  · CubicSpline-based kinematic alignment, RMSE leaderboard, embedded Matplotlib plot

Requirements:  pip install customtkinter matplotlib scipy numpy
Optional:      NatNetClient.py from OptiTrack SDK (placed next to this file)
"""

# =============================================================================
# 1.  Imports & constants
# =============================================================================

import csv
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

import numpy as np

try:
    import customtkinter as ctk
except ImportError:
    sys.exit("customtkinter not found.  Run:  pip install customtkinter")

try:
    from scipy.interpolate import CubicSpline
    from scipy.signal import butter, filtfilt
    _SCIPY = True
except ImportError:
    _SCIPY = False
    print("[WARN] scipy missing — falling back to numpy linear interpolation.")

# Optional: real NatNet SDK
try:
    from NatNetClient import NatNetClient as _RealNNClient  # type: ignore
    _NATNET = True
except ImportError:
    _RealNNClient = None
    _NATNET = False

# Optional: existing analysis pipeline
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import analysis_pipeline as _ap
    _AP = True
except Exception:
    _ap = None  # type: ignore
    _AP = False

# Optional: motive_sync for remote-command control
try:
    import motive_sync as _ms
    _MS = True
except Exception:
    _ms = None  # type: ignore
    _MS = False

# ─── App geometry & visual constants ─────────────────────────────────────────
APP_TITLE    = "Pendulastic Workspace"
WIN_W, WIN_H = 1380, 880
SIDEBAR_W    = 320

STREAM_HZ      = 120.0   # OptiTrack native rate
RESAMPLE_HZ    = 60.0    # unified time base for model comparison
LP_CUTOFF      = 6.0     # Hz — Butterworth low-pass for OptiTrack
PLOT_Y_LO      = 50.0
PLOT_Y_HI      = 200.0

POSITIONS = ["Position_1", "Position_2", "Position_3"]
HEIGHTS   = ["Height_Joint-Level", "Height_Above-Joint", "Height_Below-Joint"]

DEFAULT_LOCAL_IP  = "127.0.0.1"
DEFAULT_SERVER_IP = "127.0.0.1"
PROXIMAL_DEFAULT  = "Thigh"
DISTAL_DEFAULT    = "Shank"

MODEL_COLORS = {
    "OptiTrack (Raw)":      "#e74c3c",
    "OptiTrack (Filtered)": "#ff6b6b",
    "MediaPipe":            "#2ecc71",
    "RTMPose":              "#3498db",
    "MMPose":               "#9b59b6",
    "FreMocap":             "#f39c12",
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# =============================================================================
# 2.  NatNet streaming (real SDK + mock fallback)
# =============================================================================

class MockNatNetStreamer:
    """Simulates 120 Hz sinusoidal knee swing for offline development."""

    def __init__(self, callback, proximal: str, distal: str):
        self._cb   = callback
        self._prox = proximal
        self._dist = distal
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        t0 = time.time()
        interval = 1.0 / STREAM_HZ
        while not self._stop.is_set():
            t  = time.time() - t0
            hz = math.sin(2 * math.pi * 0.5 * t) * 0.39  # ±~22° around 90°
            self._cb([
                {"name": self._prox, "qx": 0.0, "qy": 0.0, "qz": 0.0, "qw": 1.0},
                {"name": self._dist,
                 "qx": 0.0, "qy": 0.0,
                 "qz": math.sin(hz / 2), "qw": math.cos(hz / 2)},
            ])
            time.sleep(interval)


class NatNetStreamer:
    """
    Thin wrapper around the OptiTrack NatNetClient that normalises the
    frame callback to: callable(List[{"name", "qx","qy","qz","qw"}]).
    Falls back to MockNatNetStreamer when the SDK is unavailable.
    """

    def __init__(self, local_ip: str, server_ip: str,
                 callback, proximal: str, distal: str):
        self._cb     = callback
        self._mock   = not _NATNET
        self._id_map: Dict[int, str] = {}

        if not self._mock:
            self._client = _RealNNClient()
            self._client.set_client_address(local_ip)
            self._client.set_server_address(server_ip)
            self._client.set_use_multicast(True)
            self._client.new_frame_listener     = self._on_frame
            self._client.rigid_body_listener    = self._on_desc
        else:
            self._client = MockNatNetStreamer(callback, proximal, distal)

    def _on_desc(self, desc):
        for rb in getattr(desc, "RigidBodies", []):
            self._id_map[rb.id_num] = rb.sz_name

    def _on_frame(self, data: dict):
        bodies = []
        for rb in data.get("RigidBodies", []):
            rb_id = rb.get("id", rb.get("ID", -1))
            rot   = rb.get("rot", (0, 0, 0, 1))
            bodies.append({
                "name": self._id_map.get(rb_id, f"Body_{rb_id}"),
                "qx": float(rot[0]), "qy": float(rot[1]),
                "qz": float(rot[2]), "qw": float(rot[3]),
            })
        if bodies:
            self._cb(bodies)

    def start(self):
        if self._mock:
            self._client.start()
        else:
            self._client.run()

    def stop(self):
        if self._mock:
            self._client.stop()
        elif self._client:
            self._client.shutdown()

    @property
    def is_mock(self) -> bool:
        return self._mock


# =============================================================================
# 3.  Session manager
# =============================================================================

class SessionManager:
    """Owns the directory tree and trial counter for one recording session."""

    def __init__(self, base: str, participant: str, position: str, height: str):
        self.base        = base
        self.participant = participant
        self.position    = position
        self.height      = height
        self.trial_num   = 1
        self.trials: List[Dict] = []
        self.active      = False

    @property
    def session_dir(self) -> str:
        return os.path.join(self.base, self.participant, self.position, self.height)

    def initialize(self) -> str:
        os.makedirs(self.session_dir, exist_ok=True)
        self.trial_num = 1
        self.trials    = []
        self.active    = True
        return self.session_dir

    def trial_csv_path(self) -> str:
        return os.path.join(self.session_dir,
                            f"trial_{self.trial_num}_optitrack.csv")

    def motive_packet(self) -> str:
        rel = os.path.join(self.participant, self.position, self.height)
        return (f"START|id={self.participant}|position={self.position}"
                f"|height={self.height}|trial={self.trial_num}|relpath={rel}")

    def record_trial(self, csv_path: str, frames: int, duration: float):
        self.trials.append({"trial": self.trial_num,
                            "csv":   csv_path,
                            "frames": frames,
                            "dur":   duration})
        self.trial_num += 1

    def end(self):
        self.active = False


# =============================================================================
# 4.  Trial recorder  (thread-safe)
# =============================================================================

class TrialRecorder:
    """
    Buffers quaternion frames from the NatNet callback thread and writes
    a multi-row-header CSV parseable by _optitrack_knee_angle_series().

    Header layout:
        Row 0 — metadata
        Row 1 — NAME ROW  : "", "", "Thigh","Thigh","Thigh","Thigh", "Shank",…
        Row 2 — TYPE ROW  : "", "", "Rotation",×4,  "Rotation",×4
        Row 3 — COMP ROW  : "Frame","Time","X","Y","Z","W","X","Y","Z","W"
        Row 4+ — data
    """

    _COMP = ["X", "Y", "Z", "W"]

    def __init__(self, proximal: str, distal: str):
        self.proximal = proximal
        self.distal   = distal
        self._lock    = threading.Lock()
        self._buf: List[Dict] = []
        self._t0      = 0.0
        self._active  = False

    def start(self):
        with self._lock:
            self._buf    = []
            self._t0     = time.time()
            self._active = True

    def stop(self):
        with self._lock:
            self._active = False

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._buf)

    def ingest(self, bodies: List[Dict]):
        """Called from the NatNet/mock streamer daemon thread."""
        if not self._active:
            return
        ts = time.time() - self._t0
        snap: Dict[str, Dict] = {b["name"]: b for b in bodies}
        with self._lock:
            self._buf.append({"frame": len(self._buf) + 1,
                              "t": ts, "snap": snap})

    def save_csv(self, path: str) -> int:
        """Write buffered frames to CSV. Returns number of data rows."""
        with self._lock:
            buf = list(self._buf)
        if not buf:
            return 0

        bodies = [self.proximal, self.distal]
        n_cols = 2 + len(bodies) * len(self._COMP)

        name_row = ["", ""] + [nm for nm in bodies for _ in self._COMP]
        type_row = ["", ""] + ["Rotation"] * (len(bodies) * len(self._COMP))
        comp_row = ["Frame", "Time"] + self._COMP * len(bodies)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Format", "Pendulastic", "Version", "1.0",
                        *[""] * (n_cols - 4)])
            w.writerow(name_row)
            w.writerow(type_row)
            w.writerow(comp_row)
            for fr in buf:
                row: List = [fr["frame"], f"{fr['t']:.6f}"]
                for body in bodies:
                    bd = fr["snap"].get(body, {})
                    row += [bd.get("qx", ""), bd.get("qy", ""),
                            bd.get("qz", ""), bd.get("qw", "")]
                w.writerow(row)
        return len(buf)


# =============================================================================
# 5.  Kinematics analyser
# =============================================================================

class KinematicsAnalyzer:
    """
    Loads an OptiTrack quaternion CSV + CV-model output CSVs, resamples
    everything onto a CubicSpline-interpolated common time base, and
    returns RMSE scores and plot-ready arrays.
    """

    _MODEL_HINTS = {
        "MediaPipe": ["mediapipe", "media_pipe"],
        "RTMPose":   ["rtmpose", "rtm_pose"],
        "MMPose":    ["mmpose",  "mm_pose"],
        "FreMocap":  ["fremocap", "freemocap"],
    }

    def __init__(self, session_dir: str, proximal: str, distal: str):
        self.session_dir = session_dir
        self.proximal    = proximal
        self.distal      = distal

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _resample(src_t: np.ndarray, src_y: np.ndarray,
                  dst_t: np.ndarray) -> np.ndarray:
        """CubicSpline resample; linear fallback if scipy absent."""
        if _SCIPY and len(src_t) >= 4:
            cs  = CubicSpline(src_t, src_y, extrapolate=False)
            out = cs(dst_t)
            out = np.where(dst_t < src_t[0], src_y[0],
                  np.where(dst_t > src_t[-1], src_y[-1], out))
        else:
            out = np.interp(dst_t, src_t, src_y)
        return out

    @staticmethod
    def _lowpass(y: np.ndarray, cutoff: float, fs: float) -> np.ndarray:
        if not _SCIPY or len(y) < 10:
            return y.copy()
        nyq = fs / 2.0
        b, a = butter(4, min(cutoff / nyq, 0.99), btype="low")
        return filtfilt(b, a, y)

    @staticmethod
    def _quat_local_x(q: np.ndarray) -> np.ndarray:
        """Local +X axis rotated into global frame. q shape (N, 4) [x,y,z,w]."""
        x, y, z, w = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
        return np.stack([
            1 - 2*(y*y + z*z),
            2*(x*y + w*z),
            2*(x*z - w*y),
        ], axis=1)

    @staticmethod
    def _vec_angle_deg(v1: np.ndarray, v2: np.ndarray) -> np.ndarray:
        n1 = np.linalg.norm(v1, axis=1, keepdims=True)
        n2 = np.linalg.norm(v2, axis=1, keepdims=True)
        dot = np.clip((v1 / np.maximum(n1, 1e-9) *
                       v2 / np.maximum(n2, 1e-9)).sum(axis=1), -1.0, 1.0)
        return np.degrees(np.arccos(dot))

    # ── OptiTrack CSV loader ──────────────────────────────────────────────────

    def _load_optitrack(self, csv_path: str
                        ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Returns (time_s, angle_raw_deg, angle_filtered_deg).
        Applies anatomical normalization: angle = 180 − (raw − 90).
        """
        with open(csv_path, "r", newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))

        # Locate the three structural header rows by content
        comp_idx = next((i for i, r in enumerate(rows)
                         if r and r[0].strip().lower() == "frame"), None)
        if comp_idx is None:
            raise ValueError(f"'Frame' column not found in {csv_path}")

        prox_lo = [t.lower() for t in ["thigh", "femur", "topthigh"]]
        dist_lo = [t.lower() for t in ["shank", "shin", "tibia", "calf"]]
        body_lo = prox_lo + dist_lo

        type_idx = next((i for i in range(comp_idx - 1, -1, -1)
                         if any("rotation" in c.lower() for c in rows[i])), None)
        name_idx = next((i for i in range(
                             (type_idx - 1 if type_idx is not None else comp_idx - 1),
                             -1, -1)
                         if any(tok in " ".join(rows[i]).lower()
                                for tok in body_lo)), None)

        if type_idx is None or name_idx is None:
            raise ValueError("Quaternion header rows not found — export needs "
                             "Rotation data with Thigh/Shank body names.")

        name_r = [c.strip() for c in rows[name_idx]]
        type_r = [c.strip() for c in rows[type_idx]]
        comp_r = [c.strip() for c in rows[comp_idx]]

        def find_body(tokens):
            return next((nm for nm in name_r
                         if nm and any(t in nm.lower() for t in tokens)), None)

        prox = find_body(prox_lo)
        dist = find_body(dist_lo)
        if not prox or not dist:
            raise ValueError(f"Could not find both Thigh-like and Shank-like "
                             f"rigid bodies in name row: {set(name_r)}")

        def quat_cols(body: str) -> List[int]:
            want: Dict[str, Optional[int]] = {k: None for k in "xyzw"}
            for col, (nm, tp, cp) in enumerate(zip(name_r, type_r, comp_r)):
                if nm.lower() == body.lower() and tp.lower() == "rotation":
                    if cp.lower() in want:
                        want[cp.lower()] = col
            if any(v is None for v in want.values()):
                raise ValueError(f"Incomplete quaternion columns for '{body}'")
            return [want["x"], want["y"], want["z"], want["w"]]  # type: ignore

        cols_p = quat_cols(prox)
        cols_d = quat_cols(dist)
        t_col  = next((i for i, c in enumerate(comp_r)
                       if "time" in c.lower()), None)

        data_rows = [r for r in rows[comp_idx + 1:] if r and any(v.strip() for v in r)]
        w = max((len(r) for r in data_rows), default=0)
        arr = np.full((len(data_rows), w), np.nan)
        for i, r in enumerate(data_rows):
            for j, cell in enumerate(r):
                try:
                    arr[i, j] = float(cell)
                except (ValueError, IndexError):
                    pass

        t  = arr[:, t_col] if t_col is not None else np.arange(len(arr), dtype=float)
        qp = arr[:, cols_p]
        qd = arr[:, cols_d]
        ok = np.isfinite(t) & np.isfinite(qp).all(1) & np.isfinite(qd).all(1)
        if ok.sum() < 4:
            raise ValueError("Too few valid quaternion rows.")
        t, qp, qd = t[ok], qp[ok], qd[ok]

        raw = self._vec_angle_deg(self._quat_local_x(qp), self._quat_local_x(qd))
        # Anatomical normalization: straight leg → ~180°, flexion decreases
        normed   = 180.0 - (raw - 90.0)
        filtered = self._lowpass(normed, LP_CUTOFF, STREAM_HZ)
        return t, normed, filtered

    # ── CV model loader ───────────────────────────────────────────────────────

    def _find_model_csv(self, trial: int, model: str) -> Optional[str]:
        hints = self._MODEL_HINTS.get(model, [model.lower()])
        for fname in os.listdir(self.session_dir):
            fl = fname.lower()
            if (f"trial_{trial}" in fl and fl.endswith(".csv")
                    and any(h in fl for h in hints)):
                return os.path.join(self.session_dir, fname)
        return None

    @staticmethod
    def _load_model_csv(path: str) -> Tuple[np.ndarray, np.ndarray]:
        with open(path, "r", newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            raise ValueError("Empty file.")
        hdr = [h.strip().lower() for h in rows[0]]
        ti  = next((i for i, h in enumerate(hdr) if "time" in h), None)
        ai  = next((i for i, h in enumerate(hdr)
                    if any(k in h for k in ["knee", "angle", "flexion"])), None)
        data_rows = rows[1:] if (ti is not None and ai is not None) else rows
        if ti is None:
            ti, ai = 0, 1
        t, y = [], []
        for r in data_rows:
            try:
                t.append(float(r[ti]))
                y.append(float(r[ai]))
            except (ValueError, IndexError):
                continue
        if len(t) < 4:
            raise ValueError("Fewer than 4 valid rows.")
        return np.asarray(t), np.asarray(y)

    # ── main entry point ──────────────────────────────────────────────────────

    def analyze_trial(self, trial: int, optitrack_csv: str) -> Dict[str, Any]:
        """
        Full kinematic analysis for one trial.

        Returns dict:
          "time"               – unified time array (s)
          "optitrack_raw"      – normalized raw OptiTrack angle (deg)
          "optitrack_filtered" – low-pass filtered OptiTrack angle (deg)
          "models"             – {name: {"angles": ndarray, "rmse": float}}
          "leaderboard"        – sorted [(name, rmse), …]
          "errors"             – list of warning strings
        """
        out: Dict[str, Any] = {
            "time": None, "optitrack_raw": None, "optitrack_filtered": None,
            "models": {}, "leaderboard": [], "errors": [],
        }
        try:
            opto_t, opto_raw, opto_filt = self._load_optitrack(optitrack_csv)
        except Exception as exc:
            out["errors"].append(f"OptiTrack: {exc}")
            return out

        model_data: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        for model in self._MODEL_HINTS:
            path = self._find_model_csv(trial, model)
            if path is None:
                out["errors"].append(f"{model}: output CSV not found.")
                continue
            try:
                model_data[model] = self._load_model_csv(path)
            except Exception as exc:
                out["errors"].append(f"{model}: {exc}")

        # Build intersection time window
        t_lo = opto_t.min()
        t_hi = opto_t.max()
        for mt, _ in model_data.values():
            t_lo = max(t_lo, mt.min())
            t_hi = min(t_hi, mt.max())
        if t_hi <= t_lo:
            out["errors"].append("Signals have no overlapping time window.")
            return out

        dt = 1.0 / RESAMPLE_HZ
        uni_t = np.arange(t_lo, t_hi, dt)
        if len(uni_t) < 4:
            out["errors"].append("Overlapping window is too short.")
            return out

        out["time"]               = uni_t
        out["optitrack_raw"]      = self._resample(opto_t, opto_raw,  uni_t)
        out["optitrack_filtered"] = self._resample(opto_t, opto_filt, uni_t)

        scores: List[Tuple[str, float]] = []
        ref = out["optitrack_filtered"]
        for model, (mt, my) in model_data.items():
            try:
                resampled = self._resample(mt, my, uni_t)
                mask      = np.isfinite(resampled) & np.isfinite(ref)
                if mask.sum() < 4:
                    raise ValueError("Insufficient overlap after resampling.")
                rmse = float(np.sqrt(np.mean((resampled[mask] - ref[mask]) ** 2)))
                out["models"][model] = {"angles": resampled, "rmse": rmse}
                scores.append((model, rmse))
            except Exception as exc:
                out["errors"].append(f"{model} RMSE: {exc}")

        out["leaderboard"] = sorted(scores, key=lambda x: x[1])
        return out


# =============================================================================
# 6.  GUI — Pendulastic Workspace
# =============================================================================

class PendulasticApp(ctk.CTk):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(True, True)

        # Core state
        self._session:  Optional[SessionManager] = None
        self._recorder: Optional[TrialRecorder]  = None
        self._streamer: Optional[NatNetStreamer]  = None
        self._recording = False
        self._rec_start: float = 0.0
        self._ui_queue: queue.Queue = queue.Queue()

        # Layout
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()
        self._poll_queue()

    # ── Sidebar (session metadata + NatNet settings) ──────────────────────────

    def _build_sidebar(self):
        sb = ctk.CTkScrollableFrame(self, width=SIDEBAR_W,
                                     fg_color=("gray90", "gray15"))
        sb.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=8)
        sb.grid_columnconfigure(0, weight=1)

        # — Title —
        ctk.CTkLabel(sb, text="⚡  Pendulastic",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(
                     row=0, column=0, pady=(14, 2), sticky="ew")
        ctk.CTkLabel(sb, text="Biomechanical Benchmarking Suite",
                     font=ctk.CTkFont(size=11),
                     text_color=("gray50", "gray60")).grid(
                     row=1, column=0, pady=(0, 16), sticky="ew")

        def section(parent, label: str, row: int) -> ctk.CTkFrame:
            f = ctk.CTkFrame(parent, corner_radius=10)
            f.grid(row=row, column=0, sticky="ew", padx=4, pady=6)
            f.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(f, text=label,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=("gray40", "gray70")).grid(
                         row=0, column=0, sticky="w", padx=12, pady=(10, 4))
            return f

        def labeled_entry(parent, label: str, row: int,
                          default: str = "") -> ctk.CTkEntry:
            ctk.CTkLabel(parent, text=label, anchor="w").grid(
                row=row, column=0, sticky="ew", padx=12, pady=(4, 0))
            e = ctk.CTkEntry(parent, placeholder_text=default)
            e.insert(0, default)
            e.grid(row=row + 1, column=0, sticky="ew", padx=12, pady=(0, 4))
            return e

        # — Session metadata —
        sm = section(sb, "SESSION METADATA", 2)
        self._e_pid  = labeled_entry(sm, "Participant ID", 1, "0")
        ctk.CTkLabel(sm, text="Position", anchor="w").grid(
            row=3, column=0, sticky="ew", padx=12, pady=(4, 0))
        self._dd_pos = ctk.CTkOptionMenu(sm, values=POSITIONS)
        self._dd_pos.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 4))
        ctk.CTkLabel(sm, text="Height Condition", anchor="w").grid(
            row=5, column=0, sticky="ew", padx=12, pady=(4, 0))
        self._dd_hgt = ctk.CTkOptionMenu(sm, values=HEIGHTS)
        self._dd_hgt.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 4))
        ctk.CTkLabel(sm, text="Trial #", anchor="w").grid(
            row=7, column=0, sticky="ew", padx=12, pady=(4, 0))
        self._lbl_trial = ctk.CTkLabel(sm, text="1",
                                        font=ctk.CTkFont(size=20, weight="bold"),
                                        text_color="#3498db")
        self._lbl_trial.grid(row=8, column=0, padx=12, pady=(0, 4))

        # — Workspace folder —
        wf = section(sb, "WORKSPACE FOLDER", 3)
        self._e_base = ctk.CTkEntry(wf, placeholder_text="Select base directory…")
        self._e_base.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 4))
        ctk.CTkButton(wf, text="Browse…", height=28,
                      command=self._browse_folder).grid(
                      row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

        # — NatNet settings —
        nn = section(sb, "NATNET SETTINGS", 4)
        self._e_lip = labeled_entry(nn, "Local IP",  1, DEFAULT_LOCAL_IP)
        self._e_sip = labeled_entry(nn, "Server IP", 3, DEFAULT_SERVER_IP)
        self._e_prx = labeled_entry(nn, "Proximal Body (Thigh)", 5, PROXIMAL_DEFAULT)
        self._e_dst = labeled_entry(nn, "Distal Body (Shank)",   7, DISTAL_DEFAULT)

        # — Session controls —
        ctl = section(sb, "SESSION CONTROLS", 5)
        self._btn_init = ctk.CTkButton(
            ctl, text="Initialize Session", height=38,
            fg_color="#2ecc71", hover_color="#27ae60",
            font=ctk.CTkFont(weight="bold"),
            command=self._on_init_session)
        self._btn_init.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 4))
        self._btn_end = ctk.CTkButton(
            ctl, text="End Session", height=38,
            fg_color="#e67e22", hover_color="#ca6f1e",
            font=ctk.CTkFont(weight="bold"),
            state="disabled",
            command=self._on_end_session)
        self._btn_end.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))

        # — Status indicator —
        self._lbl_status = ctk.CTkLabel(sb, text="● No active session",
                                         text_color="gray",
                                         font=ctk.CTkFont(size=11),
                                         wraplength=SIDEBAR_W - 20)
        self._lbl_status.grid(row=6, column=0, padx=8, pady=8, sticky="ew")

    # ── Main area (tabs) ──────────────────────────────────────────────────────

    def _build_main(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=8)
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)

        self._tabs = ctk.CTkTabview(main, anchor="nw")
        self._tabs.grid(row=0, column=0, sticky="nsew")
        self._tabs.add("Recording")
        self._tabs.add("Analysis")
        self._build_recording_tab(self._tabs.tab("Recording"))
        self._build_analysis_tab(self._tabs.tab("Analysis"))

    # ── Recording tab ─────────────────────────────────────────────────────────

    def _build_recording_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        # — Stream status bar —
        status_bar = ctk.CTkFrame(parent, height=56, corner_radius=10)
        status_bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 6))
        status_bar.grid_columnconfigure((0, 1, 2, 3), weight=1)

        def stat_cell(col: int, title: str) -> ctk.CTkLabel:
            ctk.CTkLabel(status_bar, text=title,
                         font=ctk.CTkFont(size=10),
                         text_color=("gray50", "gray60")).grid(
                         row=0, column=col, padx=14, pady=(6, 0), sticky="w")
            val = ctk.CTkLabel(status_bar, text="—",
                               font=ctk.CTkFont(size=15, weight="bold"))
            val.grid(row=1, column=col, padx=14, pady=(0, 6), sticky="w")
            return val

        self._lbl_frames   = stat_cell(0, "Frames")
        self._lbl_elapsed  = stat_cell(1, "Elapsed")
        self._lbl_fps      = stat_cell(2, "Rate (Hz)")
        self._lbl_stream   = stat_cell(3, "Stream")

        # — Record button —
        self._btn_rec = ctk.CTkButton(
            parent, text="▶  START RECORDING", height=56,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#2ecc71", hover_color="#27ae60",
            state="disabled",
            command=self._on_toggle_record)
        self._btn_rec.grid(row=1, column=0, sticky="ew", padx=4, pady=6)

        # — Trial list —
        ctk.CTkLabel(parent, text="Recorded Trials",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").grid(row=2, column=0, sticky="ew",
                                      padx=4, pady=(8, 2))
        self._trial_list = ctk.CTkScrollableFrame(parent, height=220,
                                                   corner_radius=10)
        self._trial_list.grid(row=3, column=0, sticky="nsew", padx=4, pady=4)
        self._trial_list.grid_columnconfigure((0, 1, 2, 3), weight=1)
        parent.grid_rowconfigure(3, weight=1)

        # Header row
        for c, hdr in enumerate(["Trial", "Frames", "Duration", "CSV path"]):
            ctk.CTkLabel(self._trial_list, text=hdr,
                         font=ctk.CTkFont(weight="bold"),
                         text_color=("gray50", "gray60")).grid(
                         row=0, column=c, padx=8, pady=4, sticky="w")

    # ── Analysis tab ──────────────────────────────────────────────────────────

    def _build_analysis_tab(self, parent):
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        # — Pipeline button —
        self._btn_pipeline = ctk.CTkButton(
            parent, text="▶  Run Benchmarking Analysis Pipeline",
            height=56, font=ctk.CTkFont(size=17, weight="bold"),
            fg_color="#3498db", hover_color="#2980b9",
            state="disabled",
            command=self._on_run_pipeline)
        self._btn_pipeline.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))

        # — Leaderboard —
        lb_frame = ctk.CTkFrame(parent, corner_radius=10)
        lb_frame.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 6))
        lb_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)
        ctk.CTkLabel(lb_frame, text="LEADERBOARD",
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=("gray40", "gray70")).grid(
                     row=0, column=0, columnspan=4, sticky="w",
                     padx=14, pady=(10, 4))
        for c, hdr in enumerate(["Rank", "Model", "RMSE (°)", "Status"]):
            ctk.CTkLabel(lb_frame, text=hdr,
                         font=ctk.CTkFont(weight="bold"),
                         text_color=("gray50", "gray60")).grid(
                         row=1, column=c, padx=10, pady=(2, 4), sticky="w")
        self._lb_frame     = lb_frame
        self._lb_rows: List[List[ctk.CTkLabel]] = []

        # — Plot canvas —
        plot_frame = ctk.CTkFrame(parent, corner_radius=10)
        plot_frame.grid(row=2, column=0, sticky="nsew", padx=4, pady=(0, 4))
        plot_frame.grid_columnconfigure(0, weight=1)
        plot_frame.grid_rowconfigure(0, weight=1)

        self._fig, self._ax = plt.subplots(figsize=(9, 4), facecolor="#1a1a2e")
        self._ax.set_facecolor("#16213e")
        self._canvas = FigureCanvasTkAgg(self._fig, master=plot_frame)
        self._canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew",
                                          padx=4, pady=4)
        # Minimal toolbar (save icon only)
        tb = NavigationToolbar2Tk(self._canvas, plot_frame, pack_toolbar=False)
        tb.grid(row=1, column=0, sticky="ew", padx=4)
        self._init_plot()

    # ── Plot helpers ──────────────────────────────────────────────────────────

    def _init_plot(self):
        ax = self._ax
        ax.cla()
        ax.set_xlim(0, 10)
        ax.set_ylim(PLOT_Y_LO, PLOT_Y_HI)
        ax.set_xlabel("Time (s)", color="white")
        ax.set_ylabel("Knee Joint Angle (°)", color="white")
        ax.set_title("Knee Angle Benchmark — waiting for data", color="white")
        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#334")
        ax.grid(color="#223", linestyle="--", linewidth=0.5)
        self._canvas.draw()

    def _draw_results(self, result: Dict[str, Any], trial: int, save_path: str):
        ax = self._ax
        ax.cla()

        t = result["time"]
        ax.set_xlim(t[0], t[-1])
        ax.set_ylim(PLOT_Y_LO, PLOT_Y_HI)
        ax.set_xlabel("Time (s)", color="white")
        ax.set_ylabel("Knee Joint Angle (°)", color="white")
        ax.tick_params(colors="white")
        for sp in ax.spines.values():
            sp.set_edgecolor("#334")
        ax.grid(color="#223", linestyle="--", linewidth=0.5)
        ax.set_facecolor("#16213e")

        if result["optitrack_raw"] is not None:
            ax.plot(t, result["optitrack_raw"],
                    color=MODEL_COLORS["OptiTrack (Raw)"],
                    linewidth=0.8, alpha=0.55, label="OptiTrack (Raw)")
        if result["optitrack_filtered"] is not None:
            ax.plot(t, result["optitrack_filtered"],
                    color=MODEL_COLORS["OptiTrack (Filtered)"],
                    linewidth=2.0, label="OptiTrack (Filtered)")

        for model, info in result["models"].items():
            lbl = f"{model}  (RMSE: {info['rmse']:.2f}°)"
            ax.plot(t, info["angles"],
                    color=MODEL_COLORS.get(model, "white"),
                    linewidth=1.6, label=lbl)

        ax.legend(loc="upper right", facecolor="#1a1a2e",
                  edgecolor="#334", labelcolor="white", fontsize=8)
        ax.set_title(f"Trial {trial} — Knee Angle Benchmark", color="white")
        self._canvas.draw()

        try:
            self._fig.savefig(save_path, dpi=120,
                              bbox_inches="tight", facecolor="#1a1a2e")
            self._post(f"Plot saved: {os.path.basename(save_path)}")
        except Exception as exc:
            self._post(f"[WARN] Could not save plot: {exc}")

    # ── Leaderboard refresh ───────────────────────────────────────────────────

    def _refresh_leaderboard(self, leaderboard: List[Tuple[str, float]]):
        for row_labels in self._lb_rows:
            for lbl in row_labels:
                lbl.grid_forget()
        self._lb_rows.clear()

        medals = ["🥇", "🥈", "🥉"]
        for i, (model, rmse) in enumerate(leaderboard):
            rank_str = medals[i] if i < 3 else f"#{i+1}"
            color    = MODEL_COLORS.get(model, "white")
            row_labels = []
            for c, txt in enumerate([rank_str, model, f"{rmse:.3f}°",
                                     "✔ Computed"]):
                lbl = ctk.CTkLabel(self._lb_frame, text=txt,
                                   text_color=color if c == 1 else None,
                                   font=ctk.CTkFont(
                                       weight="bold" if i == 0 else "normal"))
                lbl.grid(row=i + 2, column=c, padx=10, pady=3, sticky="w")
                row_labels.append(lbl)
            self._lb_rows.append(row_labels)

    # ── Session events ────────────────────────────────────────────────────────

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select workspace base directory")
        if folder:
            self._e_base.delete(0, tk.END)
            self._e_base.insert(0, folder)

    def _on_init_session(self):
        base = self._e_base.get().strip()
        pid  = self._e_pid.get().strip()
        pos  = self._dd_pos.get()
        hgt  = self._dd_hgt.get()

        if not base:
            messagebox.showwarning("Missing field", "Please select a workspace folder.")
            return
        if not pid:
            messagebox.showwarning("Missing field", "Please enter a Participant ID.")
            return

        self._session  = SessionManager(base, f"Participant_{pid}", pos, hgt)
        self._recorder = TrialRecorder(self._e_prx.get().strip() or PROXIMAL_DEFAULT,
                                       self._e_dst.get().strip() or DISTAL_DEFAULT)

        session_dir = self._session.initialize()

        # Start NatNet streamer
        self._streamer = NatNetStreamer(
            local_ip=self._e_lip.get().strip() or DEFAULT_LOCAL_IP,
            server_ip=self._e_sip.get().strip() or DEFAULT_SERVER_IP,
            callback=self._recorder.ingest,
            proximal=self._recorder.proximal,
            distal=self._recorder.distal,
        )
        self._streamer.start()

        # Optionally sync with Motive
        if _MS:
            try:
                _ms.start_local_motive(self._session.motive_packet())
            except Exception as exc:
                self._post(f"[WARN] motive_sync: {exc}")

        mock_tag = " (mock)" if self._streamer.is_mock else " (live)"
        self._set_status(f"● Session active  |  {session_dir}", "#2ecc71")
        self._lbl_stream.configure(text="LIVE" + mock_tag,
                                   text_color="#2ecc71" if not self._streamer.is_mock
                                              else "#f39c12")

        self._btn_init.configure(state="disabled")
        self._btn_end.configure(state="normal")
        self._btn_rec.configure(state="normal")
        self._lbl_trial.configure(text="1")
        self._init_plot()

        # Kick off the 1 Hz status updater
        self._tick_status()

    def _on_end_session(self):
        if self._recording:
            self._stop_recording()
        if self._streamer:
            self._streamer.stop()
        if _MS:
            try:
                _ms.stop_local_motive()
            except Exception:
                pass
        if self._session:
            self._session.end()

        self._set_status("● Session ended", "gray")
        self._btn_init.configure(state="normal")
        self._btn_end.configure(state="disabled")
        self._btn_rec.configure(state="disabled")
        self._btn_pipeline.configure(state="normal")
        self._lbl_stream.configure(text="—")

    # ── Recording events ──────────────────────────────────────────────────────

    def _on_toggle_record(self):
        if not self._recording:
            self._start_recording()
        else:
            self._stop_recording()

    def _start_recording(self):
        self._recorder.start()  # type: ignore
        self._recording   = True
        self._rec_start   = time.time()
        self._btn_rec.configure(text="⏹  STOP RECORDING",
                                fg_color="#e74c3c",
                                hover_color="#c0392b")
        self._set_status("● RECORDING…", "#e74c3c")

    def _stop_recording(self):
        self._recorder.stop()  # type: ignore
        self._recording = False
        duration = time.time() - self._rec_start

        csv_path  = self._session.trial_csv_path()         # type: ignore
        n_frames  = self._recorder.save_csv(csv_path)      # type: ignore
        trial_num = self._session.trial_num                 # type: ignore
        self._session.record_trial(csv_path, n_frames, duration)  # type: ignore

        self._btn_rec.configure(text="▶  START RECORDING",
                                fg_color="#2ecc71",
                                hover_color="#27ae60")
        self._set_status(f"● Trial {trial_num} saved  ({n_frames} frames)",
                         "#2ecc71")
        self._lbl_trial.configure(text=str(self._session.trial_num))  # type: ignore

        # Append row to trial list
        row = trial_num
        for c, txt in enumerate([
                f"Trial {trial_num}", str(n_frames),
                f"{duration:.1f} s", os.path.basename(csv_path)]):
            ctk.CTkLabel(self._trial_list, text=txt,
                         anchor="w").grid(row=row, column=c, padx=8, pady=2,
                                         sticky="w")

    # ── Analysis events ───────────────────────────────────────────────────────

    def _on_run_pipeline(self):
        if not self._session:
            messagebox.showinfo("No session", "Initialize and complete a session first.")
            return

        self._btn_pipeline.configure(state="disabled",
                                     text="⏳  Running pipeline…")
        self._set_status("● Running full analysis pipeline…", "#3498db")

        thread = threading.Thread(target=self._run_pipeline_bg, daemon=True)
        thread.start()

    def _run_pipeline_bg(self):
        session = self._session
        if session is None:
            return

        # ── Step A: run the full tracking + evaluation pipeline ───────────────
        if _AP:
            try:
                _ap.run_full_pipeline(session.session_dir, download=True)
                self._post("Full pipeline complete.")
            except Exception as exc:
                self._post(f"[WARN] pipeline error: {exc}")
        else:
            self._post("[WARN] analysis_pipeline.py not found — skipping model inference.")

        # ── Step B: kinematic alignment + RMSE for the latest trial ──────────
        target_trial = max(1, session.trial_num - 1)
        opto_csv     = os.path.join(
            session.session_dir,
            f"trial_{target_trial}_optitrack.csv")

        if not os.path.isfile(opto_csv):
            self._post(f"[WARN] OptiTrack CSV not found: {opto_csv}")
            self._post("Cannot compute RMSE without reference data.")
        else:
            analyzer = KinematicsAnalyzer(session.session_dir,
                                          self._recorder.proximal,   # type: ignore
                                          self._recorder.distal)      # type: ignore
            result   = analyzer.analyze_trial(target_trial, opto_csv)

            for err in result["errors"]:
                self._post(f"[WARN] {err}")

            if result["leaderboard"]:
                save_png = os.path.join(
                    session.session_dir,
                    f"Participant_{session.participant}_benchmark_summary.png")
                # Schedule plot + leaderboard update on the UI thread
                self._ui_queue.put(("RESULTS", result, target_trial, save_png))
            else:
                self._post("No model results to display.")

        self._ui_queue.put(("PIPELINE_DONE", None))

    # ── Status helpers ────────────────────────────────────────────────────────

    def _post(self, msg: str):
        """Thread-safe status update."""
        self._ui_queue.put(("STATUS", msg))

    def _set_status(self, msg: str, color: str = "white"):
        self._lbl_status.configure(text=msg, text_color=color)

    def _tick_status(self):
        """1 Hz UI ticker — updates frame counter / elapsed while a session is active."""
        if self._session and self._session.active:
            if self._recording and self._recorder:
                fc  = self._recorder.frame_count
                dur = time.time() - self._rec_start
                fps = fc / dur if dur > 0 else 0.0
                self._lbl_frames.configure(text=str(fc))
                self._lbl_elapsed.configure(text=f"{dur:.1f} s")
                self._lbl_fps.configure(text=f"{fps:.0f}")
            self.after(1000, self._tick_status)

    # ── Queue poller (runs on the UI thread) ──────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                msg = self._ui_queue.get_nowait()
                kind = msg[0]
                if kind == "STATUS":
                    self._set_status(msg[1])
                elif kind == "RESULTS":
                    _, result, trial, save_path = msg
                    self._refresh_leaderboard(result["leaderboard"])
                    self._draw_results(result, trial, save_path)
                elif kind == "PIPELINE_DONE":
                    self._btn_pipeline.configure(
                        state="normal",
                        text="▶  Run Benchmarking Analysis Pipeline")
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


# =============================================================================
# 7.  Entry point
# =============================================================================

if __name__ == "__main__":
    app = PendulasticApp()
    app.mainloop()
