"""
pendulastic_viewer.py  —  Wartenberg Pendulum Test Analyzer
============================================================
Kinovea-style desktop viewer: open a video, click knee + ankle,
watch the angle trace build live.

Auto-detection: YOLO finds the two people and picks the one whose
trunk is horizontal (patient lying on table) vs vertical (assessor).

Marker workflow (simplest):
  1. Open video
  2. Click "Detect Patient"  →  markers auto-placed
       OR:  click "Knee" then click knee in video
            click "Ankle" then click ankle in video
  3. Press ▶ or "Track All"
  4. Export CSV

Run:
    .venv\\Scripts\\python.exe pendulastic_viewer.py [video.avi]
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import datetime
import json
import math
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

try:
    import pendulastic_phone_server as _pps
    _PPS_AVAIL = True
except Exception:
    _pps = None          # type: ignore[assignment]
    _PPS_AVAIL = False

try:
    import pendulastic_imu_server as _imu
    _IMU_AVAIL = True
except Exception:
    _imu = None          # type: ignore[assignment]
    _IMU_AVAIL = False

import cv2
import numpy as np

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    print("tkinter required (install python-tk)")
    sys.exit(1)

try:
    from PIL import Image, ImageTk
except ImportError:
    print("Pillow required: .venv\\Scripts\\pip install Pillow")
    sys.exit(1)

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except ImportError:
    print("matplotlib required: .venv\\Scripts\\pip install matplotlib")
    sys.exit(1)

PT_HEALTHY_MAX    = 0.09   # fallback if import fails
PT_BORDERLINE_MAX = 0.20
try:
    from pendulastic_pt_score import (compute_pt_params, compute_pt_score,
                                      compute_pt_score_simple, HEALTHY_REF, pt_to_mas,
                                      PT_HEALTHY_MAX, PT_BORDERLINE_MAX,
                                      load_optitrack, draw_pt_annotations)
    _PT_AVAIL = True
except Exception:
    _PT_AVAIL = False

_MP_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "mediapipe")
_MP_MODEL = next(
    (os.path.join(_MP_MODEL_DIR, n) for n in (
        "pendulastic_pose_landmarker.task",   # fine-tuned (preferred)
        "pose_landmarker_full.task",           # original fallback
    ) if os.path.isfile(os.path.join(_MP_MODEL_DIR, n))),
    os.path.join(_MP_MODEL_DIR, "pose_landmarker_full.task"),  # default path even if missing
)
try:
    import mediapipe as _mp_lib
    _ = _mp_lib.tasks.vision.PoseLandmarker   # verify Tasks API is present
    _MP_AVAIL = os.path.isfile(_MP_MODEL)
except Exception:
    _MP_AVAIL = False

# ── Calibration model (train_calibration.py output) ──────────────────────────
# Corrects MediaPipe's systematic, angle-dependent bias against OptiTrack.
# Loaded once at startup; used transparently in both tracker classes.
import pickle as _pickle
_CALIB_PKL   = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "models", "calibration", "angle_calibration.pkl")
_CALIB_MODEL = None
try:
    with open(_CALIB_PKL, "rb") as _f:
        _CALIB_MODEL = _pickle.load(_f)["model"]
    print(f"[Calibration] Loaded: angle_calibration.pkl")
except Exception:
    pass  # not yet trained — falls back to raw 2D angle

# ── Simple polynomial calibration (calibrate.py / recalibrate_lying_down.py) ──
# When --coefficients is passed on the CLI, these coefficients are loaded and
# take precedence over _CALIB_MODEL.  Format: [c0, c1, c2, ...]
# ot_angle = c0 + c1*mp + c2*mp^2 + ...
_POLY_COEFFS: list = []  # empty = not loaded / use _CALIB_MODEL

_MAS_COLORS = {
    "0":  "#00E87A",
    "1":  "#A0E030",
    "1+": "#FFE000",
    "2":  "#FF9000",
    "3":  "#FF4500",
    "4":  "#FF1A1A",
}

# ─── configuration ────────────────────────────────────────────────────────────

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
SEL_FILE   = os.path.join(BASE_DIR, "tracking_selections.json")
MODELS_DIR = os.path.join(BASE_DIR, "models")
YOLO_PT    = os.path.join(MODELS_DIR, "yolov8n-pose.pt")

# ─── session database ─────────────────────────────────────────────────────────

_DB_FILE = os.path.join(BASE_DIR, "sessions.json")

# ─── iPhone goniometer UDP stream ─────────────────────────────────────────────
# Matches the protocol used by motive_mobile_sync.py so the same iPhone
# goniometer app / config works unchanged: plain float or {"angle": ...} JSON.
GONIOMETER_UDP_IP   = "0.0.0.0"
GONIOMETER_UDP_PORT = 8888

def _load_db() -> dict:
    if os.path.exists(_DB_FILE):
        try:
            with open(_DB_FILE, encoding="utf-8") as f:
                d = json.load(f)
                d.setdefault("participants", [])
                d.setdefault("sessions", [])
                return d
        except Exception:
            pass
    return {"participants": [], "sessions": []}

def _save_db(db: dict):
    try:
        with open(_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[DB] save error: {e}")

# Shared dark palette for Toplevel windows
_C = {
    "BG":      "#0B1928",
    "SURFACE": "#112040",
    "PANEL":   "#0D2238",
    "BTN":     "#1A3A5C",
    "BTN_ACT": "#2A6090",
    "FG":      "#C8E0F5",
    "FG2":     "#5A8AB0",
    "FG3":     "#2E5070",
    "BORDER":  "#1C3A5E",
    "MONO":    "Consolas",
}

DISPLAY_W  = 720    # canvas display width
DISPLAY_H  = 405    # canvas display height
FPS_CAP    = 30
TRAIL_LEN  = 150    # frames of ankle trail

# Multi-seed LK tracker constants
_N_SEEDS      = 16    # feature seeds distributed along the shank
_SEED_STRIP   = 22    # half-width (px) of shank strip for goodFeaturesToTrack
_FB_THRESH    = 4.0   # max forward-backward LK error to keep a seed (px)
_RESEED_INT   = 25    # refresh seeds every N frames
_MIN_SEEDS    = 4     # force re-seed when fewer valid seeds remain
_DIR_HIST_LEN = 8     # frames of angular history for extrapolation

_LK_PARAMS = dict(
    winSize  = (27, 27),
    maxLevel = 4,
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 25, 0.02),
)

# BGR draw colours
_C_THIGH  = (0,  140, 255)    # orange
_C_SHANK  = (0,  230, 200)    # teal
_C_ARC    = (220,  50, 210)   # magenta — matches Kinovea
_C_JOINT  = (255, 255, 255)
_C_TRAIL  = (60,  210, 110)   # green trail
_C_PATIENT= (0,  210,  80)
_C_ASSES  = (120, 120, 120)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _angle_deg(a, b, c) -> float:
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1, 1)))


def _world_angle_3d(world_lm_set, side: str):
    """
    Compute interior knee angle from MediaPipe world landmarks (3D, metres).
    World landmarks are rotation-invariant — no perspective distortion.
    Returns (angle_deg, hip_visibility, knee_visibility) or (None, 0, 0).
    """
    try:
        import mediapipe as mp
        PL = mp.tasks.vision.PoseLandmark
        hi = PL.LEFT_HIP.value   if side == "left" else PL.RIGHT_HIP.value
        ki = PL.LEFT_KNEE.value  if side == "left" else PL.RIGHT_KNEE.value
        ai = PL.LEFT_ANKLE.value if side == "left" else PL.RIGHT_ANKLE.value
        hw, kw, aw = world_lm_set[hi], world_lm_set[ki], world_lm_set[ai]
        hip_v, kne_v = float(hw.visibility), float(kw.visibility)
        if hip_v < 0.4 or kne_v < 0.4 or float(aw.visibility) < 0.4:
            return None, hip_v, kne_v
        v1 = np.array([hw.x - kw.x, hw.y - kw.y, hw.z - kw.z], dtype=np.float64)
        v2 = np.array([aw.x - kw.x, aw.y - kw.y, aw.z - kw.z], dtype=np.float64)
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-6 or n2 < 1e-6:
            return None, hip_v, kne_v
        cos_a = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
        return math.degrees(math.acos(cos_a)), hip_v, kne_v
    except Exception:
        return None, 0.0, 0.0


def _calibrate_from_world(world_lm_set, side: str, fallback: float) -> float:
    """
    Return calibration-corrected knee angle.

    Priority order:
      1. _POLY_COEFFS  — simple polynomial from calibrate.py / recalibrate_lying_down.py
                         (loaded via --coefficients; position-specific if lying-down)
      2. _CALIB_MODEL  — sklearn pipeline from train_calibration.py (if pkl exists)
      3. fallback      — raw 2D pixel-plane angle (no calibration)

    Input feature is always the 2D pixel-plane angle (fallback), matching the
    training data's mp_angle_aligned to avoid train/inference mismatch.
    """
    if not (60.0 <= fallback <= 210.0):
        return fallback

    # -- Simple polynomial (position-stratified coefficients) -----------------
    if _POLY_COEFFS:
        try:
            result = _POLY_COEFFS[0]
            for power, c in enumerate(_POLY_COEFFS[1:], start=1):
                result += c * (fallback ** power)
            return float(result)
        except Exception:
            pass

    # -- Sklearn pipeline (train_calibration.py model) ------------------------
    if _CALIB_MODEL is None:
        return fallback
    try:
        hip_v, kne_v = 1.0, 1.0
        if world_lm_set is not None:
            _, hip_v_w, kne_v_w = _world_angle_3d(world_lm_set, side)
            if hip_v_w > 0:
                hip_v, kne_v = hip_v_w, kne_v_w
        X = np.array([[fallback, hip_v, kne_v]])
        return float(_CALIB_MODEL.predict(X)[0])
    except Exception:
        return fallback


def _load_sel(video_path: str) -> dict:
    """Return saved tracking_selections.json entry for this video, or {}."""
    if not os.path.exists(SEL_FILE):
        return {}
    try:
        rel = os.path.relpath(os.path.abspath(video_path), BASE_DIR).replace("\\", "/")
        with open(SEL_FILE, encoding="utf-8") as f:
            return json.load(f).get(rel, {})
    except Exception:
        return {}


# ─── patient detector (YOLO) ──────────────────────────────────────────────────

class _PatientDetector:
    """
    Finds the patient (horizontal body) vs assessor (vertical body) using
    YOLO-Pose keypoints.  Falls back gracefully when YOLO is unavailable.
    """
    def __init__(self):
        self._yolo  = None
        self._ready = False

    def load(self):
        if self._ready:
            return True
        try:
            from ultralytics import YOLO
            self._yolo  = YOLO(YOLO_PT)
            self._ready = True
        except Exception as e:
            print(f"[PatientDetector] YOLO unavailable: {e}")
        return self._ready

    def detect(self, frame: np.ndarray):
        """
        Returns (patient_kps, assessor_kps) — each is a 17×2 array (COCO) or None.
        The patient is whichever detected person has the most horizontal trunk.
        """
        if not self.load():
            return None, None

        results = self._yolo(frame, verbose=False)
        people  = []

        for r in results:
            if r.keypoints is None:
                continue
            for i in range(len(r.keypoints.xy)):
                kps = r.keypoints.xy[i].cpu().numpy()   # (17, 2)
                if kps.shape[0] < 17:
                    continue
                l_sh, r_sh = kps[5],  kps[6]
                l_hp, r_hp = kps[11], kps[12]
                sh_mid = (l_sh + r_sh) / 2
                hp_mid = (l_hp + r_hp) / 2
                trunk  = sh_mid - hp_mid
                mag    = float(np.linalg.norm(trunk))
                if mag < 10:
                    continue
                # |x-component| / mag → 1.0 for horizontal, 0.0 for vertical
                h_score = abs(trunk[0]) / mag
                people.append((h_score, kps))

        people.sort(reverse=True)   # most horizontal first = patient
        patient  = people[0][1] if people else None
        assessor = people[1][1] if len(people) > 1 else None
        return patient, assessor


# ─── multi-seed LK shank tracker ─────────────────────────────────────────────

class _Tracker:
    """
    Tracks the shank angle using N LK feature seeds spread along the whole
    shank, then takes the MEDIAN angular vote each frame.

    Robustness: even if several seeds drift to the assessor or the brace
    hardware, the majority that stay on the shank produce consistent directions
    from the fixed knee, and the median is insensitive to a minority of
    outliers.  No single-point tracker (LK or CSRT) can match this because any
    single point that jumps to the assessor corrupts the result entirely.

    Seeds are refreshed every _RESEED_INT frames along the current shank
    estimate, bounding long-term drift.
    """

    def __init__(self):
        self.hip0:       Optional[np.ndarray] = None
        self.knee0:      Optional[np.ndarray] = None
        self.ankle0:     Optional[np.ndarray] = None
        self.thigh_len:  float = 0.0
        self.shank_len:  float = 0.0
        self._ank:       Optional[np.ndarray] = None
        self._init_dir:  float = 0.0
        self._dir_hist:  list  = []
        self._prev_gray: Optional[np.ndarray] = None
        self._seeds:     Optional[np.ndarray] = None
        self._fc:        int   = 0

    @property
    def ready(self) -> bool:
        return self._ank is not None

    @property
    def _kne(self) -> Optional[np.ndarray]:
        return self.knee0

    def init(self, frame_bgr: np.ndarray, hip, knee, ankle):
        self.hip0      = np.asarray(hip,   dtype=np.float32)
        self.knee0     = np.asarray(knee,  dtype=np.float32)
        self.ankle0    = np.asarray(ankle, dtype=np.float32)
        self.thigh_len = float(np.linalg.norm(self.knee0  - self.hip0))
        self.shank_len = float(np.linalg.norm(self.ankle0 - self.knee0))
        self._ank      = self._project(self.ankle0)
        self._init_dir = self._dir_of(self._ank)
        self._dir_hist = [self._init_dir]
        self._fc       = 0
        self._prev_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        self._seeds    = self._reseed(self._prev_gray)

    # ── geometry ──────────────────────────────────────────────────────────────

    def _project(self, pt: np.ndarray) -> np.ndarray:
        vec  = pt - self.knee0
        norm = float(np.linalg.norm(vec))
        if norm < 1:
            return self.knee0 + np.array([0.0, self.shank_len], dtype=np.float32)
        return self.knee0 + vec / norm * self.shank_len

    def _dir_of(self, pt: np.ndarray) -> float:
        v = pt - self.knee0
        return math.atan2(float(v[1]), float(v[0]))

    def _pt_at(self, angle: float) -> np.ndarray:
        return self.knee0 + np.array(
            [math.cos(angle) * self.shank_len,
             math.sin(angle) * self.shank_len], dtype=np.float32)

    def _extrapolate(self) -> np.ndarray:
        if len(self._dir_hist) < 2:
            return self._ank.copy() if self._ank is not None else self._pt_at(self._init_dir)
        hist   = list(np.unwrap(self._dir_hist))
        deltas = [hist[i] - hist[i-1] for i in range(1, len(hist))]
        return self._pt_at(hist[-1] + float(np.mean(deltas)))

    # ── seed management ───────────────────────────────────────────────────────

    def _reseed(self, gray: np.ndarray) -> np.ndarray:
        mask = np.zeros(gray.shape[:2], dtype=np.uint8)
        kx = int(round(float(self.knee0[0]))); ky = int(round(float(self.knee0[1])))
        ax = int(round(float(self._ank[0])));  ay = int(round(float(self._ank[1])))
        cv2.line(mask, (kx, ky), (ax, ay), 255, 2 * _SEED_STRIP)
        pts = cv2.goodFeaturesToTrack(
            gray, maxCorners=_N_SEEDS, qualityLevel=0.004,
            minDistance=6, mask=mask)
        if pts is not None and len(pts) >= 2:
            return pts.reshape(-1, 2).astype(np.float32)
        ts = np.linspace(0.2, 0.95, _N_SEEDS)
        return np.array([self.knee0 + t * (self._ank - self.knee0) for t in ts],
                        dtype=np.float32)

    # ── per-frame step ────────────────────────────────────────────────────────

    def step(self, frame_bgr: np.ndarray) -> tuple:
        """Returns (hip, knee, ankle, angle_deg)."""
        if not self.ready:
            return self.hip0, self.knee0, self._ank, float("nan")

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        if self._seeds is None or len(self._seeds) == 0:
            self._seeds = self._reseed(gray)

        pts_in = self._seeds.reshape(-1, 1, 2)
        nxt, st_f, _ = cv2.calcOpticalFlowPyrLK(
            self._prev_gray, gray, pts_in, None, **_LK_PARAMS)
        bwd, st_b, _ = cv2.calcOpticalFlowPyrLK(
            gray, self._prev_gray, nxt, None, **_LK_PARAMS)

        fb_err = np.linalg.norm(
            pts_in.reshape(-1, 2) - bwd.reshape(-1, 2), axis=1)
        lk_ok  = (st_f.squeeze() == 1) & (st_b.squeeze() == 1) & (fb_err < _FB_THRESH)

        angles    = []
        survivors = []
        for ok, pt in zip(lk_ok, nxt.reshape(-1, 2)):
            if not ok:
                continue
            dist = float(np.linalg.norm(pt - self.knee0))
            if not (0.1 * self.shank_len < dist < 1.55 * self.shank_len):
                continue
            angle = self._dir_of(pt)
            diff  = abs((angle - self._init_dir + math.pi) % (2 * math.pi) - math.pi)
            if diff > math.radians(130):
                continue
            # Ankle cannot cross from below to above the knee between frames.
            # In image space Y increases downward, so sin(angle) < 0 means
            # the point is above the knee — anatomically impossible during the PT.
            if math.sin(angle) < -0.10:
                continue
            angles.append(angle)
            survivors.append(pt)

        if len(angles) >= 2:
            median_angle = float(np.median(list(np.unwrap(angles))))
            # Post-selection clamp: if all surviving seeds were marginal the
            # median can still land above the knee — keep the last good position
            # and force a reseed so the next frame can recover.
            if math.sin(median_angle) < -0.10:
                self._seeds = None   # reseed next frame
            else:
                self._ank   = self._pt_at(median_angle)
                self._seeds = np.array(survivors, dtype=np.float32)
        else:
            self._ank   = self._project(self._extrapolate())
            self._seeds = None

        # Hard clamp: ankle must remain below (or at) knee level in image space.
        # sin(theta) < 0 means the point is above the knee — snap to the closest
        # boundary angle that keeps the ankle at knee level (sin = -0.10).
        _cur_dir = self._dir_of(self._ank)
        if math.sin(_cur_dir) < -0.10:
            _t_bound = math.asin(-0.10)   # boundary angle in lower/right quad
            # Mirror: the valid boundary in the left quad is pi - _t_bound
            def _adist(a, b):
                d = (a - b + math.pi) % (2 * math.pi) - math.pi
                return abs(d)
            _best = _t_bound if _adist(_cur_dir, _t_bound) < _adist(_cur_dir, math.pi - _t_bound) else math.pi - _t_bound
            self._ank  = self._pt_at(_best)
            self._seeds = None   # stale seeds → reseed next frame

        self._fc += 1
        if self._seeds is None or len(self._seeds) < _MIN_SEEDS or self._fc % _RESEED_INT == 0:
            self._seeds = self._reseed(gray)

        self._prev_gray = gray
        self._dir_hist.append(self._dir_of(self._ank))
        if len(self._dir_hist) > _DIR_HIST_LEN:
            self._dir_hist.pop(0)

        ang = _angle_deg(self.hip0, self.knee0, self._ank)
        return self.hip0, self.knee0, self._ank, ang

    def reset(self):
        self._ank       = None
        self._prev_gray = None
        self._seeds     = None
        self.hip0 = self.knee0 = self.ankle0 = None
        self._dir_hist  = []
        self._init_dir  = 0.0
        self._fc        = 0


# ─── arc-angle tracker (batch tracking) ──────────────────────────────────────

class _ArcTracker:
    """
    1-D arc-angle tracker for the Wartenberg pendulum test.

    Design rationale
    ----------------
    * Only the ANGLE of the shank changes — the knee is a fixed pivot and the
      shank has a fixed length. This reduces tracking to a 1-D search.
    * We use gradient-magnitude images instead of raw pixel values so that
      shadows and illumination shifts don't mislead the template match (a shadow
      boundary has a much softer gradient than a physical edge like the knee
      brace straps; high-contrast objects like the white orthotic remain
      distinctive under any lighting).
    * The search window widens with estimated angular velocity so fast initial
      swings (where LK fails because the inter-frame displacement is too large)
      are still captured.  For the first _WIDE_FRAMES frames we open the window
      to ±_WIN_INIT degrees regardless of velocity, because the velocity EMA
      starts at zero and cannot predict the first rapid movement.
    * The template is updated SLOWLY during fast motion and FASTER when the leg
      is nearly still.  Updating eagerly during fast motion corrupts the template
      with wrong background pixels and causes the tracker to lock onto shadows
      or the assessor.
    """
    _N_ANGLES      = 240      # candidates per frame (finer angular resolution)
    _N_SAMPLES     = 48       # sample points along shank
    _WIN_INIT      = 80.0     # half-window used for first _WIDE_FRAMES frames (°)
    _WIDE_FRAMES   = 80       # frames over which to keep the wide initial window
    _WIN_BASE      = 28.0     # half-window when nearly static (°)
    _WIN_SCALE     = 4.5      # extra ° of window per °/frame of |velocity|
    _WIN_MAX       = 85.0     # hard maximum half-window (°)
    _VEL_ALPHA     = 0.45     # EMA coefficient for angular velocity (faster response)
    _VEL_MAX       = 15.0     # max °/frame fed into the velocity EMA (clips big jumps)
    _NCC_MIN       = 0.18     # minimum NCC to trust result vs. kinematic fallback
    _BLEND_SLOW    = 0.015    # template update rate while leg is moving fast
    _BLEND_FAST    = 0.10     # template update rate while leg is nearly still
    _VEL_THRESH    = 4.0      # °/frame above which we switch to slow blending
    _MOTION_THRESH    = 15.0  # peak tdiff_s above which we use temporal-diff signal
    _RETRACK_COOLDOWN = 25   # frames of narrow post-pin window after a user correction
    _MAX_ANKLE_JUMP   = 80.0 # max pixel displacement of ankle between consecutive frames

    _MOTION_CONSEC_REQ = 3   # consecutive frames above threshold before latching motion

    def __init__(self):
        self.knee:            Optional[np.ndarray] = None
        self.hip0:            Optional[np.ndarray] = None
        self.radius:          float = 0.0
        self._theta:          float = 0.0
        self._theta0:         float = 0.0
        self._vel:            float = 0.0
        self._tmpl:           Optional[np.ndarray] = None   # gradient-magnitude template
        self._fc:             int   = 0                     # frame counter
        self._prev_gray:      Optional[np.ndarray] = None   # previous frame (for temporal diff)
        self._motion_detected:  bool = False                # latched True once leg releases
        self._motion_consec:    int  = 0                   # consecutive frames above threshold
        self._retrack_cooldown: int  = 0                   # frames left in narrow post-pin window
        self._prev_ankle_px:  Optional[np.ndarray] = None  # pixel pos of ankle last frame

    # ── feature image ─────────────────────────────────────────────────────────

    @staticmethod
    def _grad(gray: np.ndarray) -> np.ndarray:
        """
        Gradient-magnitude image.

        Why this beats raw pixel values
        ─────────────────────────────────
        • Hard edges (brace straps, skin boundary) → high gradient regardless
          of whether the pixel is bright or dark.
        • Shadows → soft gradient (gentle brightness ramp), suppressed by the
          Sobel kernel relative to sharp brace edges.
        • Illumination change (clinician moves, overhead lamp) → gradient
          magnitude is insensitive to additive brightness offsets.
        """
        blur = cv2.GaussianBlur(gray, (5, 5), 1.0)
        gx   = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
        gy   = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
        return np.sqrt(gx ** 2 + gy ** 2)

    # ── geometry helpers ──────────────────────────────────────────────────────

    def _sample(self, feat: np.ndarray, theta: float) -> np.ndarray:
        h, w = feat.shape
        ts   = np.linspace(0.15, 0.75, self._N_SAMPLES)  # brace region, avoids ankle endpoint
        xs   = np.clip(
            (self.knee[0] + self.radius * ts * math.cos(theta)).astype(int), 0, w - 1)
        ys   = np.clip(
            (self.knee[1] + self.radius * ts * math.sin(theta)).astype(int), 0, h - 1)
        return feat[ys, xs].astype(float)

    def _batch(self, feat: np.ndarray, thetas: np.ndarray,
               s_lo: float = 0.15, s_hi: float = 0.75) -> np.ndarray:
        """Return (N_ang, N_samples) feature array for all candidate angles."""
        h, w = feat.shape
        ts   = np.linspace(s_lo, s_hi, self._N_SAMPLES)
        cos_ = np.cos(thetas)[:, None]
        sin_ = np.sin(thetas)[:, None]
        xs   = np.clip(
            (self.knee[0] + self.radius * ts * cos_).astype(int), 0, w - 1)
        ys   = np.clip(
            (self.knee[1] + self.radius * ts * sin_).astype(int), 0, h - 1)
        return feat[ys, xs].astype(float)

    def _ncc(self, strips: np.ndarray) -> np.ndarray:
        tc  = self._tmpl - self._tmpl.mean()
        sc  = strips - strips.mean(axis=1, keepdims=True)
        num = (sc * tc).sum(axis=1)
        den = np.sqrt((sc ** 2).sum(axis=1) * (tc ** 2).sum() + 1e-8)
        return num / den

    def _perp_var(self, gray: np.ndarray, thetas: np.ndarray) -> np.ndarray:
        """
        Perpendicular cross-section variance at three depths along each candidate shank.

        Why this helps during fast motion
        ────────────────────────────────────
        When the leg moves fast, motion blur reduces gradient magnitude everywhere
        on the moving limb — so the gradient-NCC score drops and can be beaten by
        static background texture.  The perpendicular cross-section variance is
        independent of motion blur: it asks "is there a bright/dark object (the
        leg/brace) cutting across this direction at this radius from the knee?"
        A physical limb creates a high-contrast boundary (high variance);
        a cast shadow is a gradual brightness ramp (low variance).
        Sampling at three radii (30 %, 50 %, 70 %) covers both the proximal brace
        hardware and the distal bare shank, making it robust to brace placement.
        """
        HALF = 10          # pixels each side of shank axis
        h, w = gray.shape
        total = np.zeros(len(thetas))
        for r_frac in (0.30, 0.50, 0.70):
            r   = self.radius * r_frac
            cx  = self.knee[0] + r * np.cos(thetas)   # (N,)
            cy  = self.knee[1] + r * np.sin(thetas)
            # Perpendicular direction: rotate θ by 90°
            px  = -np.sin(thetas)
            py  =  np.cos(thetas)
            off = np.arange(-HALF, HALF + 1, dtype=float)   # (2H+1,)
            xs  = np.clip((cx[:, None] + px[:, None] * off).astype(int), 0, w - 1)
            ys  = np.clip((cy[:, None] + py[:, None] * off).astype(int), 0, h - 1)
            total += gray[ys, xs].astype(float).var(axis=1)
        return total   # (N,)

    # ── public API ────────────────────────────────────────────────────────────

    def init(self, frame_bgr: np.ndarray, hip, knee, ankle):
        self.knee    = np.asarray(knee,  dtype=np.float64)
        self.hip0    = np.asarray(hip,   dtype=np.float64) if hip is not None else None
        ank          = np.asarray(ankle, dtype=np.float64)
        self.radius  = float(np.linalg.norm(ank - self.knee))
        v            = ank - self.knee
        self._theta  = math.atan2(float(v[1]), float(v[0]))
        self._theta0 = self._theta
        self._vel    = 0.0
        self._fc     = 0
        gray                  = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        self._tmpl            = self._sample(self._grad(gray), self._theta)
        self._prev_gray       = gray
        self._motion_detected = False
        self._motion_consec   = 0
        self._prev_ankle_px   = ank.copy()

    def step(self, frame_bgr: np.ndarray) -> tuple:
        """Returns (hip, knee, ankle_ndarray, angle_deg)."""
        gray_raw = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        feat     = self._grad(gray_raw)

        pred = self._theta + self._vel

        # Window sizing:
        # • Pre-release (static hold): tight ±10° window — the leg isn't moving,
        #   so a wide window only lets background edges (table base, assessor body)
        #   compete with the real ankle location.
        # • Post-release, first WIDE_FRAMES: open to WIN_INIT so the tracker can
        #   catch the initial fast swing before the velocity EMA has ramped up.
        # • Post-release, later frames: velocity-adaptive window.
        if not self._motion_detected:
            win = 10.0
        elif self._fc < self._WIDE_FRAMES:
            win = self._WIN_INIT
        else:
            win = min(self._WIN_MAX,
                      self._WIN_BASE + self._WIN_SCALE * abs(math.degrees(self._vel)))

        if self._retrack_cooldown > 0:
            # Post-pin narrow window: ignore the velocity prediction and centre
            # the search on the CURRENT theta (last confirmed correct position).
            # Starts at _WIN_BASE (28°) and widens linearly toward `win` over
            # _RETRACK_COOLDOWN frames, then normal prediction-based window resumes.
            # This prevents immediately jumping to wrong attractors (table edge,
            # assessor body) that happen to have high tdiff or pv within the ±80°
            # wide window — the main cause of single-frame-then-broken retrack.
            frac = self._retrack_cooldown / self._RETRACK_COOLDOWN   # 1.0 → ~0
            narrow = self._WIN_BASE + (win - self._WIN_BASE) * (1.0 - frac)
            lo_ = max(self._theta0 - math.radians(130),
                      self._theta  - math.radians(narrow))
            hi_ = min(self._theta0 + math.radians(130),
                      self._theta  + math.radians(narrow))
            self._retrack_cooldown -= 1
        else:
            lo_ = max(self._theta0 - math.radians(130), pred - math.radians(win))
            hi_ = min(self._theta0 + math.radians(130), pred + math.radians(win))
        if lo_ >= hi_:
            lo_ = pred - math.radians(self._WIN_BASE)
            hi_ = pred + math.radians(self._WIN_BASE)

        thetas = np.linspace(lo_, hi_, self._N_ANGLES)
        strips = self._batch(feat, thetas)

        # Gradient NCC — good for texture & edge patterns; weaker under motion blur
        ncc_s = self._ncc(strips)

        # Perpendicular variance — good for detecting the physical limb boundary;
        # not affected by motion blur; complementary to NCC
        pv_s  = self._perp_var(gray_raw, thetas)
        pv_n  = pv_s / (pv_s.max() + 1e-8)

        # Temporal difference — the moving shank creates high diff along its true direction.
        # During fast swings (assessor releases the leg), this is the most reliable signal
        # because the gradient template is tuned to the EXTENDED position and will score
        # the initial direction higher even when the leg has swung away.
        if self._prev_gray is not None:
            tdiff        = np.abs(gray_raw.astype(np.float32) - self._prev_gray.astype(np.float32))
            tdiff_strips = self._batch(tdiff, thetas, 0.10, 0.90)   # wider range: full shank
            tdiff_s      = tdiff_strips.mean(axis=1)
            motion_level = float(tdiff_s.max())
            tdiff_n      = tdiff_s / (motion_level + 1e-8)
        else:
            motion_level = 0.0
            tdiff_n      = np.zeros(len(thetas))

        # Latch motion onset: require _MOTION_CONSEC_REQ consecutive frames above threshold
        # before switching weights.  A single-frame spike (background pedestrian, PT
        # shifting weight) previously latched permanently and sent the tracker to follow
        # background motion instead of the stationary leg.
        if not self._motion_detected:
            if motion_level > self._MOTION_THRESH:
                self._motion_consec += 1
                if self._motion_consec >= self._MOTION_CONSEC_REQ:
                    self._motion_detected = True
            else:
                self._motion_consec = max(0, self._motion_consec - 1)

        # Two-tier adaptive weighting:
        # • Pre-release (static hold): 60% NCC + 35% pv keep the tracker locked to the
        #   initial position; template is accurate and leg is still.
        # • Post-release (all swings): 75% tdiff drives tracking to wherever pixels are
        #   MOVING; perp_var (15%) provides a gentle spatial anchor; NCC (10%) contributes
        #   slightly since the brace hardware is still distinctive at any angle.
        #   Perp_var weight is deliberately low here because background boundaries (table
        #   edge, assessor body) can score very high at the WRONG angle (as high as 0.85)
        #   and would overwhelm the temporal diff signal if given 30% weight.
        if self._motion_detected:
            combined = 0.10 * ncc_s + 0.75 * tdiff_n + 0.15 * pv_n
        else:
            combined = 0.60 * ncc_s + 0.05 * tdiff_n + 0.35 * pv_n

        # ── Physical validity gate 1: interior knee angle ─────────────────
        # Reject candidates where the ankle would be within 25° of the hip
        # direction from the knee (interior angle < 25°) — the ankle can
        # never be in nearly the same direction as the hip from the knee.
        if self.hip0 is not None:
            _phi   = math.atan2(float(self.hip0[1] - self.knee[1]),
                                float(self.hip0[0] - self.knee[0]))
            _diff  = thetas - _phi
            # Wrap to (–π, π] so abs gives the interior angle [0, 180°]
            _diff  = (_diff + math.pi) % (2 * math.pi) - math.pi
            _inangs = np.degrees(np.abs(_diff))   # interior angle per candidate
            _bad   = _inangs < 25.0
            if not np.all(_bad):                  # keep at least one candidate
                combined = np.where(_bad, -np.inf, combined)

        # ── Physical validity gate 2: ankle must be below the knee ──────────
        # In image space Y increases downward: ankle_y = knee_y + r·sin(θ).
        # sin(θ) < 0  → ankle above knee (always wrong).
        # sin(θ) ≈ 0  → ankle at knee level / horizontal — also wrong post-release,
        #               because once the leg swings it is always BELOW horizontal.
        #
        # Two-tier threshold:
        #   Pre-release  (examiner holds leg near horizontal): sin ≥ −0.10
        #                small above-horizontal margin for the held position.
        #   Post-release (_motion_detected): sin > +0.05 — ankle must be
        #                clearly BELOW the horizontal through the knee.
        #                This eliminates the near-horizontal wrong attractors
        #                (θ ≈ 0° pointing right, θ ≈ 180° pointing left) that
        #                the assessor's moving body parts create at knee height.
        _g2_thresh = 0.05 if self._motion_detected else -0.10
        _bad_v = np.sin(thetas) < _g2_thresh
        if not np.all(_bad_v):
            combined = np.where(_bad_v, -np.inf, combined)

        best_i = int(np.argmax(combined))
        best_t = float(thetas[best_i])
        best_s = float(ncc_s[best_i])

        # Fall back to kinematic prediction only when ALL three signals are weak
        if best_s < self._NCC_MIN and pv_n[best_i] < 0.3 and tdiff_n[best_i] < 0.3:
            best_t = float(np.clip(pred,
                                   self._theta0 - math.radians(130),
                                   self._theta0 + math.radians(130)))
            best_i = int(np.argmin(np.abs(thetas - best_t)))

        # Pre-release static freeze: if the leg has not been released and there is no
        # meaningful motion (ml < 5), lock theta completely.  The 35% perp_var weight
        # can otherwise be lured by hard background edges (table base, assessor body)
        # that score higher than the stationary ankle on quiet frames.
        if not self._motion_detected and motion_level < 5.0:
            best_t    = self._theta
            self._vel = 0.0

        # Jump guard + noise-floor freeze (active only after motion onset):
        # • ml < 5  (sensor noise floor): leg is definitively stationary — freeze theta
        #   entirely and zero the velocity so accumulated noise cannot cause drift.
        # • ml in [5, 25]: adaptive guard — max allowed step scales from 5°/frame at
        #   rest to 20°/frame at the swing threshold; oversized steps fall back to the
        #   kinematic prediction and the velocity is partially decayed on rejection to
        #   break any locked-drift state.
        # • ml ≥ 25: fast swing — no guard; large jumps are legitimate.
        if self._motion_detected:
            _d_raw = best_t - self._theta
            while _d_raw >  math.pi: _d_raw -= 2 * math.pi
            while _d_raw < -math.pi: _d_raw += 2 * math.pi
            if motion_level < 5.0:
                best_t    = self._theta   # freeze at current position
                self._vel = 0.0           # kill velocity immediately
            elif motion_level < 25.0:
                _max_step = 5.0 + (motion_level / 25.0) * 15.0
                if abs(math.degrees(_d_raw)) > _max_step:
                    best_t = float(np.clip(pred,
                                           self._theta0 - math.radians(130),
                                           self._theta0 + math.radians(130)))
                    best_i = int(np.argmin(np.abs(thetas - best_t)))
                    self._vel *= 0.7   # break locked-drift on rejection

        # ── Post-selection validity clamp ────────────────────────────────────
        # Gates 1 & 2 above mask the combined[] array, but the kinematic fallback
        # and jump guard can set best_t to a forbidden position by directly
        # overwriting it.  Re-apply both constraints to the final best_t value.
        def _ang_dist(a, b):
            d = (a - b + math.pi) % (2 * math.pi) - math.pi
            return abs(d)

        # Gate 2 clamp: re-apply the two-tier threshold to the final best_t.
        # The scoring-array gate excluded forbidden candidates but the kinematic
        # fallback and jump guard can overwrite best_t directly — clamp here too.
        if math.sin(best_t) < _g2_thresh:
            _t_lo  = math.asin(_g2_thresh)      # first valid angle (e.g. ~2.9° or ~-5.7°)
            _t_hi  = math.pi - _t_lo            # mirror (e.g. ~177.1° or ~185.7°)
            best_t = _t_lo if _ang_dist(best_t, _t_lo) < _ang_dist(best_t, _t_hi) else _t_hi
            self._vel *= 0.3

        # Gate 1 clamp: ankle must not point nearly toward the hip
        if self.hip0 is not None:
            _phi_c  = math.atan2(float(self.hip0[1] - self.knee[1]),
                                 float(self.hip0[0] - self.knee[0]))
            _diff_c = (best_t - _phi_c + math.pi) % (2 * math.pi) - math.pi
            if math.degrees(abs(_diff_c)) < 25.0:
                _cw  = _phi_c + math.radians(25.0)
                _ccw = _phi_c - math.radians(25.0)
                best_t = _cw if _ang_dist(best_t, _cw) < _ang_dist(best_t, _ccw) else _ccw
                self._vel *= 0.5

        # ── Pixel-space jump guard ────────────────────────────────────────────
        # Caps how far the ankle can travel in image space between frames.
        # Angular gates (above) allow jumps when motion_level ≥ 25 (fast swing),
        # but a different person's ankle scores high on the arc and would pass
        # that gate. Real pendulum speeds top out at ~30 px/frame; the wrong
        # ankle is typically 150–300 px away — well above _MAX_ANKLE_JUMP.
        if self._prev_ankle_px is not None:
            _cand_ank = self.knee + np.array([math.cos(best_t) * self.radius,
                                              math.sin(best_t) * self.radius])
            if float(np.linalg.norm(_cand_ank - self._prev_ankle_px)) > self._MAX_ANKLE_JUMP:
                best_t = float(np.clip(pred,
                                       self._theta0 - math.radians(130),
                                       self._theta0 + math.radians(130)))
                self._vel *= 0.5

        # Velocity EMA — clip the observed per-frame step before blending so that
        # large tracking jumps (e.g. tracker skips to the correct trough in one frame)
        # don't corrupt the velocity estimate used for the next frame's search window.
        d = best_t - self._theta
        while d >  math.pi: d -= 2 * math.pi
        while d < -math.pi: d += 2 * math.pi
        d_clipped = float(np.clip(d, math.radians(-self._VEL_MAX), math.radians(self._VEL_MAX)))
        self._vel   = self._VEL_ALPHA * self._vel + (1 - self._VEL_ALPHA) * d_clipped
        # At low-but-non-zero motion level, decay velocity to prevent score-function
        # noise from accumulating into a persistent drift via the EMA.
        if motion_level < 10.0:
            self._vel *= 0.85
        self._theta = best_t
        self._fc   += 1

        self._prev_gray = gray_raw

        # Template update: two-regime adaptive learning.
        # Pre-release (static hold): fast blend — assessor's hands are in frame;
        #   template locks onto the held extended position.
        # Post-release (motion): very slow blend, ONLY when confidence is very
        #   high (best_s > 0.80).  This gradually replaces the initial
        #   extended-position template with the ankle's appearance during swing,
        #   making the tracker more accurate as it accumulates evidence — without
        #   drifting if the current match is uncertain.
        if best_s >= self._NCC_MIN and not self._motion_detected:
            vel_deg = abs(math.degrees(self._vel))
            blend   = (self._BLEND_SLOW if vel_deg > self._VEL_THRESH
                       else self._BLEND_FAST)
            self._tmpl = (1 - blend) * self._tmpl + blend * strips[best_i]
        elif self._motion_detected and best_s > 0.80:
            self._tmpl = 0.97 * self._tmpl + 0.03 * strips[best_i]

        ankle = (self.knee + np.array([math.cos(best_t) * self.radius,
                                       math.sin(best_t) * self.radius]))
        self._prev_ankle_px = ankle.copy()
        ang = (_angle_deg(self.hip0, self.knee, ankle)
               if self.hip0 is not None else float("nan"))
        return self.hip0, self.knee, ankle.astype(np.float32), ang


# ─── MediaPipe shared helper ─────────────────────────────────────────────────

def _mp_nearest_pose(poses, ref_px, shape, side, PL,
                     x_gate_frac: float = 0.25,
                     anchor_xfrac: float = None):
    """
    From a list of MediaPipe pose-landmark sets, return (lm, dist) where lm
    is the set whose `side` knee is closest to ref_px in pixel space.
    Returns (None, inf) when poses is empty.

    x_gate_frac: any pose whose knee x-coordinate is more than this fraction of
    frame width away from the reference x is rejected before distance ranking.

    anchor_xfrac: if provided (0-1 normalised x of the patient's knee, set once
    at selection time and never drifted), uses this as the x-reference with a
    tighter 18 % gate.  This prevents the tracker ever drifting to the assessor
    even when the EMA has wandered slightly, because the anchor never moves.
    """
    if not poses:
        return None, float("inf")
    h, w = shape[:2]
    ki = PL.LEFT_KNEE.value if side == "left" else PL.RIGHT_KNEE.value
    ref = np.asarray(ref_px, dtype=np.float32)
    if anchor_xfrac is not None:
        x_ref = anchor_xfrac * w
        x_tol = 0.15 * w          # tighter gate when we have a firm anchor
    else:
        x_ref = ref[0]
        x_tol = x_gate_frac * w
    best_lm, best_d = None, float("inf")
    for lm in poses:
        kp = np.array([lm[ki].x * w, lm[ki].y * h], dtype=np.float32)
        if abs(kp[0] - x_ref) > x_tol:    # wrong person — skip
            continue
        d = float(np.linalg.norm(kp - ref))
        if d < best_d:
            best_d, best_lm = d, lm
    return best_lm, best_d


# ─── Ankle-template matching ──────────────────────────────────────────────────
# Used to disambiguate between patient and assessor when both are in frame.

_TMPL_SZ = 48   # patch side length in pixels


def _ankle_patch(frame_bgr: np.ndarray, pos) -> Optional[np.ndarray]:
    """Extract a zero-mean, unit-std BGR patch centred on pos."""
    h, w = frame_bgr.shape[:2]
    x = int(round(float(pos[0]))); y = int(round(float(pos[1])))
    half = _TMPL_SZ // 2
    x1, y1 = max(0, x - half), max(0, y - half)
    x2, y2 = min(w, x + half), min(h, y + half)
    roi = frame_bgr[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    patch = cv2.resize(roi, (_TMPL_SZ, _TMPL_SZ)).astype(np.float32)
    sigma = float(patch.std()) + 1e-6
    return (patch - patch.mean()) / sigma


def _patch_ncc(a: Optional[np.ndarray], b: Optional[np.ndarray]) -> float:
    """Normalised cross-correlation between two pre-normalised patches (−1..1)."""
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    return float(np.dot(a.ravel(), b.ravel()) / max(a.size, 1))


# ─── MediaPipe per-frame tracker ─────────────────────────────────────────────

class _MPTracker:
    """
    Per-frame MediaPipe Pose tracker for live camera and video-playback use.

    Runs MediaPipe Pose (model_complexity=2, "Full") on each frame and extracts
    hip/knee/ankle landmarks for whichever leg is closest to the user's click.
    Hip and knee are exponentially smoothed to suppress jitter at the pivot;
    the ankle is the live joint.  Falls back to the last known positions when
    detection fails so angle recording stays continuous.

    The correction workflow is unchanged: the viewer's ankle-pin corrections
    override the MediaPipe output for specific frames via the arc-interpolation
    and Retrack mechanism already in place.
    """

    def __init__(self):
        self._pose:     object               = None
        self._PL:       object               = None   # PoseLandmark enum (cached)
        self._side:     str                  = "right"
        self.hip0:      Optional[np.ndarray] = None
        self.knee0:     Optional[np.ndarray] = None
        self.ankle0:    Optional[np.ndarray] = None
        self.shank_len: float                = 0.0
        self._hip_px:   Optional[np.ndarray] = None
        self._knee_px:  Optional[np.ndarray] = None
        self._ank_px:   Optional[np.ndarray] = None
        self._ready:       bool = False
        self._ts_ms:       int  = 0
        self._ms_per_frame: int = 33   # updated to actual FPS when video loads
        self._last_world_lms: list = []   # parallel to pose_landmarks; for calibration
        self._sel_world_lm:   object = None  # world lm set for the selected pose
        self._anchor_xfrac: Optional[float] = None  # normalised x of patient knee, never drifts
        # Visibility scores for the selected pose — updated each frame, used by UI overlay
        self.last_hip_vis:  float = 0.0
        self.last_kne_vis:  float = 0.0
        self.last_ank_vis:  float = 0.0
        # Rolling quality metrics (90-frame window ≈ 3 s at 30 fps)
        from collections import deque as _deque
        self._vis_window:   _deque = _deque(maxlen=90)  # 1=good frame, 0=missed
        self._ang_window:   _deque = _deque(maxlen=150) # recent knee angles for assessor check
        # Directional prior from user corrections — biases step() toward corrected direction
        self._corr_theta:  float = 0.0
        self._corr_weight: float = 0.0

    # ── public interface ──────────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def _ank(self) -> Optional[np.ndarray]:
        return self._ank_px

    @property
    def coverage_pct(self) -> float:
        """Fraction of recent frames where knee landmark was clearly visible (0–1)."""
        if not self._vis_window:
            return 0.0
        return sum(self._vis_window) / len(self._vis_window)

    @property
    def assessor_warning(self) -> bool:
        """True when the knee angle hasn't varied enough to be a pendulum test.

        Triggers after ≥5 s of data (150 frames) with std < 4° — likely means
        the camera is tracking the assessor (standing still) not the patient.
        """
        if len(self._ang_window) < 150:
            return False
        return float(np.std(self._ang_window)) < 4.0

    def init(self, frame_bgr: np.ndarray, hip, knee, ankle):
        """Determine side from click, snapshot pivot, run first detection.

        With num_poses=2 MediaPipe may detect both the patient and a nearby
        clinician.  We iterate every detected pose and check both the left and
        right knee against the user's click to find the correct person and side.
        If the best match is still more than 2 shank-lengths away from the click,
        MediaPipe detected the wrong area entirely — we fall back to the user's
        click positions so tracking stays anchored to where they actually clicked.
        """
        self._open_pose()
        kne_click = np.asarray(knee, dtype=np.float32)
        ank_click = np.asarray(ankle, dtype=np.float32)
        shank_from_click = float(np.linalg.norm(ank_click - kne_click))
        # 1× shank: only snap to a MP detection that is within one shank-length of
        # the user's click.  The previous 3× ceiling was too permissive — it let
        # MediaPipe snap to an assessor standing right next to the patient.
        max_ok_dist = max(60.0, shank_from_click * 1.0)

        # Defaults — used when MP is absent or detects the wrong person.
        h_px = np.asarray(hip,   dtype=np.float32)
        k_px = np.asarray(knee,  dtype=np.float32)
        a_px = np.asarray(ankle, dtype=np.float32)
        self._side = "right"

        poses = self._run_mp(frame_bgr)          # list[landmarks] | None
        if poses and self._PL is not None:
            PL   = self._PL
            fh, fw = frame_bgr.shape[:2]
            best_lm, best_side, best_d = None, "right", float("inf")
            for lm in poses:
                for ki, s in ((PL.LEFT_KNEE.value, "left"),
                              (PL.RIGHT_KNEE.value, "right")):
                    kp = np.array([lm[ki].x * fw, lm[ki].y * fh],
                                  dtype=np.float32)
                    d  = float(np.linalg.norm(kp - kne_click))
                    if d < best_d:
                        best_d, best_lm, best_side = d, lm, s
            if best_lm is not None and best_d < max_ok_dist:
                self._side = best_side
                # Side detection only — do NOT snap positions to MP output.
                # If the assessor is closer to the click than the patient
                # (hold phase occlusion), MP would corrupt self.shank_len and
                # freeze the ankle in the wrong place during step().

        # Always use the user's original click positions.
        h_px = np.asarray(hip,   dtype=np.float32)
        k_px = np.asarray(knee,  dtype=np.float32)
        a_px = np.asarray(ankle, dtype=np.float32)
        self.hip0      = h_px.copy()
        self.knee0     = k_px.copy()
        self.ankle0    = a_px.copy()
        self.thigh_len = float(np.linalg.norm(k_px - h_px))
        self.shank_len = float(np.linalg.norm(a_px - k_px))
        self._hip_px   = h_px
        self._knee_px  = k_px
        self._ank_px   = a_px
        # Store the patient's knee x as a normalised fraction so step() can gate
        # tightly without ever drifting toward the assessor.
        self._anchor_xfrac = float(k_px[0]) / max(frame_bgr.shape[1], 1)
        self._ready    = True

    def step(self, frame_bgr: np.ndarray) -> tuple:
        """Returns (hip, knee, ankle, angle_deg).

        Guided 1-DOF mode: hip and knee are pinned to the user's initial click.
        Every frame we scan ALL detected ankles from ALL poses (both L and R) and
        pick the candidate whose distance from knee0 is closest to shank_len —
        i.e. the one sitting on the anatomical shank circle.  That point is then
        projected exactly onto the circle so only direction, never magnitude, comes
        from MediaPipe.

        Why this beats the old approach
        ────────────────────────────────
        The old approach picked one person by knee proximity, then read their
        ankle landmark and blended it toward the stored position with EMA.
        Two failure modes:
          1. Person selection locked onto the assessor after hold-phase occlusion.
          2. EMA carried a wrong detection smoothly into subsequent frames.
        Here, there is no person selection and no EMA.  The shank circle is the
        only discriminator: the correct ankle is always ~shank_len from the knee;
        the assessor's ankle and the floor are almost never on that circle.

        Rejection: if no candidate is within 50 % of shank_len from the circle,
        the ankle direction is left unchanged (last known good direction holds).
        """
        poses = self._run_mp(frame_bgr)

        if poses:
            fh, fw = frame_bgr.shape[:2]
            best_ank      = None
            best_d2c      = float("inf")   # |dist_from_knee - shank_len|
            best_pose_idx = 0

            # Scan every ankle landmark (both sides) from every detected pose.
            # BlazePose indices: 27 = LEFT_ANKLE, 28 = RIGHT_ANKLE (fixed).
            for pi, pose_lms in enumerate(poses):
                for ai in (27, 28):
                    lm  = pose_lms[ai]
                    ank = np.array([lm.x * fw, lm.y * fh], dtype=np.float32)
                    dfk = float(np.linalg.norm(ank - self.knee0))
                    d2c = abs(dfk - self.shank_len)
                    # Correction bias: add a directional penalty so candidates
                    # far from the user-confirmed direction score worse.
                    # Decays from weight=1.0 to ~0 over 25 frames.
                    if self._corr_weight > 0.01 and dfk > 2.0:
                        v_c   = ank - self.knee0
                        theta = math.atan2(float(v_c[1]), float(v_c[0]))
                        d_th  = abs(theta - self._corr_theta)
                        if d_th > math.pi:
                            d_th = 2 * math.pi - d_th
                        d2c  += self._corr_weight * d_th * self.shank_len * 0.15
                    if d2c < best_d2c:
                        best_d2c      = d2c
                        best_ank      = ank
                        best_pose_idx = pi

            if best_ank is not None:
                # Always project the best candidate onto the shank circle.
                # Candidate selection (smallest d2c + correction bias) handles
                # person disambiguation — no per-frame rejection threshold needed.
                vec  = best_ank - self.knee0
                norm = float(np.linalg.norm(vec))
                if norm > 2.0:
                    self._ank_px = self.knee0 + (vec / norm) * self.shank_len
                self._sel_world_lm = (
                    self._last_world_lms[best_pose_idx]
                    if self._last_world_lms and best_pose_idx < len(self._last_world_lms)
                    else None
                )
                self.last_hip_vis = 1.0
                self.last_kne_vis = 1.0
                self.last_ank_vis = 1.0
                self._vis_window.append(1)
            else:
                self.last_ank_vis = 0.0
                self._vis_window.append(0)

        # Decay correction weight each frame so the prior fades after ~25 frames.
        if self._corr_weight > 0.0:
            self._corr_weight = max(0.0, self._corr_weight * 0.85)

        # Hip and knee are always the fixed user-click positions — never drifted.
        ang = _angle_deg(self.hip0, self.knee0, self._ank_px)
        ang = _calibrate_from_world(self._sel_world_lm, self._side, ang)
        if math.isfinite(ang):
            self._ang_window.append(ang)
        return self.hip0, self.knee0, self._ank_px, ang

    def apply_correction(self, ankle_px):
        """Update the live tracker's ankle and set a directional prior.

        Projects ankle_px onto the shank circle and biases the next ~25
        step() calls toward the corrected direction so the tracker stays on
        target after a user pin without needing a full Retrack run.
        """
        if not self._ready or self.knee0 is None:
            return
        vec  = np.asarray(ankle_px, dtype=np.float32) - self.knee0
        norm = float(np.linalg.norm(vec))
        if norm < 2.0:
            return
        self._ank_px      = self.knee0 + (vec / norm) * self.shank_len
        self._corr_theta  = math.atan2(float(vec[1]), float(vec[0]))
        self._corr_weight = 1.0

    def reset(self):
        if self._pose is not None:
            try: self._pose.close()
            except Exception: pass
            self._pose = None
        self._hip_px = self._knee_px = self._ank_px = None
        self.hip0 = self.knee0 = self.ankle0 = None
        self.shank_len = 0.0
        self._ready = False
        self._ts_ms = 0
        self._corr_weight = 0.0

    # ── internals ─────────────────────────────────────────────────────────────

    def _open_pose(self):
        if self._pose is not None:
            return
        try:
            import mediapipe as mp
            V = mp.tasks.vision
            self._PL  = V.PoseLandmark
            options   = V.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=_MP_MODEL),
                # IMAGE mode: each frame detected independently — no temporal drift.
                # VIDEO mode caused the assessor-ankle lock-on during hold-phase occlusion.
                running_mode=V.RunningMode.IMAGE,
                num_poses=2,          # detect patient AND clinician; we pick the right one
                min_pose_detection_confidence=0.30,
                min_pose_presence_confidence=0.30,
            )
            self._pose = V.PoseLandmarker.create_from_options(options)
        except Exception as e:
            print(f"[MPTracker] MediaPipe unavailable: {e}")

    def _run_mp(self, frame_bgr):
        """Run MediaPipe and return the full list of detected pose-landmark sets.
        Also caches pose_world_landmarks in self._last_world_lms for calibration."""
        if self._pose is None:
            self._last_world_lms = []
            return None
        try:
            import mediapipe as mp
            rgb    = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self._pose.detect(mp_img)
            self._last_world_lms = result.pose_world_landmarks or []
            return result.pose_landmarks if result.pose_landmarks else None
        except Exception:
            self._last_world_lms = []
            return None

    def _lm_to_px(self, lm, shape):
        PL = self._PL
        h, w = shape[:2]
        hi = PL.LEFT_HIP.value   if self._side == "left" else PL.RIGHT_HIP.value
        ki = PL.LEFT_KNEE.value  if self._side == "left" else PL.RIGHT_KNEE.value
        ai = PL.LEFT_ANKLE.value if self._side == "left" else PL.RIGHT_ANKLE.value
        return (
            np.array([lm[hi].x * w, lm[hi].y * h], dtype=np.float32),
            np.array([lm[ki].x * w, lm[ki].y * h], dtype=np.float32),
            np.array([lm[ai].x * w, lm[ai].y * h], dtype=np.float32),
        )


# ─── MediaPipe batch tracker ──────────────────────────────────────────────────

class _MPBatchTracker:
    """
    MediaPipe-based batch tracker for 'Track All' and 'Retrack' operations.

    Exposes the same interface and ArcTracker-compatible attributes (_theta,
    _vel, radius) so the existing rollback logic in _cmd_track_all and
    _cmd_retrack_from_here works for both tracker types without changes.
    """

    # ArcTracker-compat class-level dummies (instance attrs shadow these)
    _motion_detected  = True
    _retrack_cooldown = 0

    def __init__(self, side: str = "right", fps: float = 30.0):
        self._side:         str = side
        self._ms_per_frame: int = max(1, int(round(1000.0 / max(fps, 1.0))))
        self._pose:   object = None
        self._PL:     object = None
        self._hip:    Optional[np.ndarray] = None
        self._kne:    Optional[np.ndarray] = None
        self._ank:    Optional[np.ndarray] = None
        self._last_world_lms: list = []
        self._sel_world_lm:   object = None
        self.hip0:    Optional[np.ndarray] = None
        self.knee:    Optional[np.ndarray] = None   # ArcTracker compat
        self.radius:  float = 0.0
        self._vel:    float = 0.0                   # ArcTracker compat (ignored)
        self._ts_ms:  int   = 0
        self._anchor_xfrac: Optional[float] = None  # normalised x of patient knee, set once
        # Appearance templates learnt from user ankle corrections
        self._correct_template: Optional[np.ndarray] = None
        self._wrong_template:   Optional[np.ndarray] = None
        # Directional prior from user corrections — biases step() toward corrected direction
        self._corr_theta:  float = 0.0
        self._corr_weight: float = 0.0

    # _theta: ArcTracker-compat property computed from current ankle position
    @property
    def _theta(self) -> float:
        if self._kne is None or self._ank is None:
            return 0.0
        v = self._ank.astype(float) - self._kne.astype(float)
        return math.atan2(float(v[1]), float(v[0]))

    @_theta.setter
    def _theta(self, value: float):
        r = self.radius if self.radius > 0 else 50.0
        if self._kne is not None:
            self._ank = (self._kne + np.array(
                [math.cos(value) * r, math.sin(value) * r], dtype=np.float32))

    # ── public ────────────────────────────────────────────────────────────────

    def init(self, frame_bgr: np.ndarray, hip, knee, ankle):
        """Initialise directly from the user's click positions.

        MediaPipe position snapping has been deliberately removed from init().
        When both the patient and the assessor are in frame (which is always the
        case during the hold phase), MP cannot reliably determine which person the
        clinician is pointing at.  The snap would silently replace the correct
        patient positions with the assessor's positions, corrupting self.radius
        so that step()'s ankle-update gate freezes the ankle in the wrong place.

        Disambiguation happens inside step() / _pick_pose() via the x-gate and
        the NCC ankle template, which work on a frame-by-frame basis and can
        tolerate occasional detection ambiguity without accumulating error.
        """
        self._open_pose()
        self._ts_ms = 0
        # Trust the user's click positions exactly.
        self._hip = np.asarray(hip,   dtype=np.float32)
        self._kne = np.asarray(knee,  dtype=np.float32)
        self._ank = np.asarray(ankle, dtype=np.float32)
        self.hip0   = self._hip.copy()
        self.knee   = self._kne.copy()
        self.radius = float(np.linalg.norm(self._ank - self._kne))
        # Store the patient's normalised x so step() never drifts to the assessor.
        self._anchor_xfrac = float(self.knee[0]) / max(frame_bgr.shape[1], 1)

    def apply_correction(self, ankle_px):
        """Set a directional prior from a user-pinned ankle position.

        Biases the next ~25 step() calls so drift away from the correction
        direction is suppressed in the frames immediately following a pin.
        """
        if self._kne is None:
            return
        vec  = np.asarray(ankle_px, dtype=np.float32) - self._kne
        norm = float(np.linalg.norm(vec))
        if norm < 2.0:
            return
        self._ank         = self._kne + (vec / norm) * self.radius
        self._corr_theta  = math.atan2(float(vec[1]), float(vec[0]))
        self._corr_weight = 1.0

    def _pick_pose(self, poses, frame_bgr):
        """Choose the best-matching pose from candidates.

        When a learnt ankle template exists AND more than one pose passes the
        x-gate, score by:
          NCC(correct_template) − 0.4·NCC(wrong_template) − 0.05·(knee_dist/radius)

        Falls back to nearest-knee distance when no template is available, or
        when only one candidate passes the gate.
        """
        if not poses or self._PL is None:
            return None, float("inf")
        PL = self._PL
        h, w = frame_bgr.shape[:2]
        ki = PL.LEFT_KNEE.value  if self._side == "left" else PL.RIGHT_KNEE.value
        ai = PL.LEFT_ANKLE.value if self._side == "left" else PL.RIGHT_ANKLE.value

        # x-gate candidates to the patient's side
        if self._anchor_xfrac is not None:
            x_ref = self._anchor_xfrac * w
        else:
            x_ref = float(self.knee[0]) if self.knee is not None else w / 2
        x_tol = 0.15 * w   # tighter: excludes assessor knee that drifts into the 0.22 zone
        candidates = [lm for lm in poses if abs(lm[ki].x * w - x_ref) <= x_tol]
        if not candidates:
            return None, float("inf")

        if len(candidates) == 1 or self._correct_template is None:
            # No template or single candidate — nearest knee wins
            best, best_d = None, float("inf")
            for lm in candidates:
                kp = np.array([lm[ki].x * w, lm[ki].y * h])
                d  = float(np.linalg.norm(kp - self.knee))
                if d < best_d:
                    best_d, best = d, lm
            return best, best_d

        # Multiple candidates with a learnt template: score and pick best
        best_lm, best_score = None, -float("inf")
        for lm in candidates:
            a_pos = np.array([lm[ai].x * w, lm[ai].y * h])
            patch = _ankle_patch(frame_bgr, a_pos)
            score = _patch_ncc(patch, self._correct_template)
            if self._wrong_template is not None:
                score -= 0.4 * _patch_ncc(patch, self._wrong_template)
            kp = np.array([lm[ki].x * w, lm[ki].y * h])
            knee_dist = float(np.linalg.norm(kp - self.knee))
            score -= 0.05 * (knee_dist / max(self.radius, 1.0))
            if score > best_score:
                best_score = score
                best_lm    = lm
        if best_lm is None:
            return None, float("inf")
        kp = np.array([best_lm[ki].x * w, best_lm[ki].y * h])
        return best_lm, float(np.linalg.norm(kp - self.knee))

    def step(self, frame_bgr: np.ndarray) -> tuple:
        """Advance one frame.

        Person identity: uses self.knee (the initial, stable knee position) — not
        the drifting EMA self._kne — as the x-gate reference so the assessor can
        never pull the tracker away through accumulated EMA drift.

        EMA weights scale with landmark visibility so dark clothing or occlusion
        barely moves the stored positions instead of injecting noisy detections.
        """
        poses = self._run_mp(frame_bgr)
        if poses and self._PL is not None:
            lm, knee_d = self._pick_pose(poses, frame_bgr)
            if lm is not None and knee_d < max(40.0, self.radius * 0.40):
                PL = self._PL
                hi = PL.LEFT_HIP.value   if self._side == "left" else PL.RIGHT_HIP.value
                ki = PL.LEFT_KNEE.value  if self._side == "left" else PL.RIGHT_KNEE.value
                ai = PL.LEFT_ANKLE.value if self._side == "left" else PL.RIGHT_ANKLE.value
                hip_vis = float(lm[hi].visibility)
                kne_vis = float(lm[ki].visibility)
                ank_vis = float(lm[ai].visibility)

                h_px, k_px, a_px = self._lm_to_px(lm, frame_bgr.shape)
                # Visibility-weighted EMA (hip + knee are reliable; update freely)
                self._hip = (1 - 0.20 * hip_vis) * self._hip + 0.20 * hip_vis * h_px
                self._kne = (1 - 0.25 * kne_vis) * self._kne + 0.25 * kne_vis * k_px

                # ── Ankle: three cascaded guards ─────────────────────────────
                # Guard 1 — shank-length range.  Ankle must be within [0.45, 1.35]×
                # radius of the knee.  This rejects the assessor's shins/feet
                # (typically >1.4× shank away from the patient's knee) and also
                # implausibly close positions (foot-near-knee = tracking failure).
                ank_d = float(np.linalg.norm(a_px - self._kne))
                in_range = (self.radius * 0.45 < ank_d < self.radius * 1.35)

                if in_range and ank_vis > 0.10:
                    # Guard 2 — NCC template match.  If a reference ankle patch
                    # was learnt (from the wizard or from Retrack), weight the EMA
                    # update by how well the detected ankle looks like the template.
                    # A floor of 0.20 prevents tracking from completely freezing
                    # when the ankle's appearance changes during swing.
                    if self._correct_template is not None:
                        patch_det = _ankle_patch(frame_bgr, a_px)
                        ncc = _patch_ncc(patch_det, self._correct_template)
                        if self._wrong_template is not None:
                            ncc -= 0.5 * _patch_ncc(patch_det, self._wrong_template)
                        ncc_w = max(0.20, ncc)
                    else:
                        ncc_w = 1.0
                    alpha_a = 0.35 * ank_vis * ncc_w

                    # Guard 3 — velocity cap.  The EMA-proposed position cannot
                    # move more than 22% of the shank length per frame (≈58 px at
                    # 30 fps for a 265 px shank).  This stops a single bad frame
                    # from dragging the ankle EMA toward the assessor and locking
                    # it there through the range gate.
                    proposed = (1 - alpha_a) * self._ank + alpha_a * a_px
                    step_d   = float(np.linalg.norm(proposed - self._ank))
                    max_step = self.radius * 0.22
                    if step_d > max_step:
                        proposed = self._ank + (proposed - self._ank) * (max_step / step_d)
                    self._ank = proposed
                pose_idx = next((i for i, p in enumerate(poses) if p is lm), 0)
                self._sel_world_lm = (
                    self._last_world_lms[pose_idx]
                    if self._last_world_lms and pose_idx < len(self._last_world_lms)
                    else None
                )
        # Correction pull: draw the ankle EMA toward the user-confirmed direction.
        # Strong for the first ~5 frames after a pin, fades to zero over ~25 frames.
        if self._corr_weight > 0.0:
            corr_ank = self._kne + np.array([
                math.cos(self._corr_theta), math.sin(self._corr_theta)
            ], dtype=np.float32) * self.radius
            pull          = self._corr_weight * 0.35
            self._ank     = (1 - pull) * self._ank + pull * corr_ank
            self._corr_weight = max(0.0, self._corr_weight * 0.85)

        # Arc projection: compute the angle from the DIRECTION of knee→ankle,
        # projected onto the radius circle.  This makes the angle robust to
        # small EMA magnitude drift — only the direction (hence the angle)
        # matters for the clinical measurement.
        ank_dir  = self._ank - self._kne
        ank_dist = float(np.linalg.norm(ank_dir))
        ank_for_angle = (self._kne + ank_dir * (self.radius / ank_dist)
                         if ank_dist > 1e-6 else self._ank)

        ang = _angle_deg(self._hip, self._kne, ank_for_angle)
        ang = _calibrate_from_world(self._sel_world_lm, self._side, ang)
        return self._hip, self._kne, self._ank, ang

    def close(self):
        if self._pose is not None:
            try: self._pose.close()
            except Exception: pass
            self._pose = None

    # ── internals ─────────────────────────────────────────────────────────────

    def _open_pose(self):
        if self._pose is not None:
            return
        try:
            import mediapipe as mp
            V = mp.tasks.vision
            self._PL  = V.PoseLandmark
            options   = V.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=_MP_MODEL),
                # IMAGE mode: each frame detected independently — no temporal drift.
                # VIDEO mode caused the assessor-ankle lock-on during hold-phase occlusion.
                running_mode=V.RunningMode.IMAGE,
                num_poses=2,          # detect patient AND clinician; we pick the right one
                min_pose_detection_confidence=0.30,
                min_pose_presence_confidence=0.30,
            )
            self._pose = V.PoseLandmarker.create_from_options(options)
        except Exception as e:
            print(f"[MPBatchTracker] MediaPipe unavailable: {e}")

    def _run_mp(self, frame_bgr):
        """Run MediaPipe and return the full list of detected pose-landmark sets.
        Also caches pose_world_landmarks in self._last_world_lms for calibration."""
        if self._pose is None:
            self._last_world_lms = []
            return None
        try:
            import mediapipe as mp
            rgb    = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self._pose.detect(mp_img)
            self._last_world_lms = result.pose_world_landmarks or []
            return result.pose_landmarks if result.pose_landmarks else None
        except Exception:
            self._last_world_lms = []
            return None

    def _lm_to_px(self, lm, shape):
        PL = self._PL
        h, w = shape[:2]
        hi = PL.LEFT_HIP.value   if self._side == "left" else PL.RIGHT_HIP.value
        ki = PL.LEFT_KNEE.value  if self._side == "left" else PL.RIGHT_KNEE.value
        ai = PL.LEFT_ANKLE.value if self._side == "left" else PL.RIGHT_ANKLE.value
        return (
            np.array([lm[hi].x * w, lm[hi].y * h], dtype=np.float32),
            np.array([lm[ki].x * w, lm[ki].y * h], dtype=np.float32),
            np.array([lm[ai].x * w, lm[ai].y * h], dtype=np.float32),
        )


# ─── overlay renderer ─────────────────────────────────────────────────────────

def _draw(frame: np.ndarray, hip, knee, ankle, angle: float,
          trail: list, scale: float) -> np.ndarray:
    """Draw skeleton + angle arc + ankle trail onto a scaled copy.

    Each marker is drawn as soon as it is available — no need to wait for all
    three joints to be placed before anything appears on screen.
    """
    dw = int(frame.shape[1] * scale)
    dh = int(frame.shape[0] * scale)
    out = cv2.resize(frame, (dw, dh), interpolation=cv2.INTER_LINEAR)

    def S(p):   # scale native coords to display coords
        return (int(p[0] * scale), int(p[1] * scale))

    lw = max(1, int(2 * scale))
    jr = max(3, int(5 * scale))

    h = S(hip)    if hip    is not None else None
    k = S(knee)   if knee   is not None else None
    a = S(ankle)  if ankle  is not None else None

    # Ankle trail (needs at least the trail positions)
    for i in range(1, len(trail)):
        t = i / len(trail)
        c = (int(_C_TRAIL[0] * t), int(_C_TRAIL[1] * t), int(_C_TRAIL[2] * t))
        p0 = (int(trail[i-1][0]*scale), int(trail[i-1][1]*scale))
        p1 = (int(trail[i][0]*scale),   int(trail[i][1]*scale))
        cv2.line(out, p0, p1, c, lw)

    # Skeleton lines — only when both endpoints are available
    if h and k:
        cv2.line(out, h, k, _C_THIGH, lw + 1)
    if k and a:
        cv2.line(out, k, a, _C_SHANK, lw + 1)

    # Joint dots — draw whichever are available
    # Hip: orange ring, Knee: large white filled + blue ring, Ankle: teal ring
    _C_HIP_DOT   = _C_THIGH       # orange — matches thigh line
    _C_KNEE_DOT  = _C_JOINT       # white fill
    _C_ANKLE_DOT = _C_SHANK       # teal — matches shank line
    if h:
        cv2.circle(out, h, jr - 1, _C_HIP_DOT,  -1)
        cv2.circle(out, h, jr - 1, (50, 50, 180), lw)
    if k:
        cv2.circle(out, k, jr + 1, _C_KNEE_DOT,  -1)
        cv2.circle(out, k, jr + 1, (50, 50, 180), lw)
    if a:
        cv2.circle(out, a, jr - 1, _C_ANKLE_DOT, -1)
        cv2.circle(out, a, jr - 1, (50, 50, 180), lw)

    # Angle arc + label — needs all three joints and a valid angle
    if h and k and a and math.isfinite(angle):
        tv = np.array(h) - np.array(k)
        sv = np.array(a) - np.array(k)
        a1 = math.degrees(math.atan2(-tv[1], tv[0]))
        a2 = math.degrees(math.atan2(-sv[1], sv[0]))
        lo, hi = sorted([a1, a2])
        if hi - lo > 180:
            lo, hi = hi, lo + 360
        r_arc = max(30, int(55 * scale))
        cv2.ellipse(out, k, (r_arc, r_arc), 0, -hi, -lo, _C_ARC, lw)

        label = f"{angle:.1f} deg"
        fs    = max(0.45, 0.75 * scale)
        thick = max(1, int(2 * scale))
        tx, ty = k[0] + int(14 * scale), k[1] - int(12 * scale)
        cv2.putText(out, label, (tx, ty), cv2.FONT_HERSHEY_DUPLEX, fs, (0,0,0), thick+2, cv2.LINE_AA)
        cv2.putText(out, label, (tx, ty), cv2.FONT_HERSHEY_DUPLEX, fs, _C_ARC,  thick,   cv2.LINE_AA)

    return out


# ─── person-select (multi-patient disambiguation) ────────────────────────────

_PS_COLORS = [
    (0, 230, 150),   # person 1 — cyan-green
    (0, 130, 255),   # person 2 — orange
    (220,  50, 220), # person 3 — magenta
    (50,  220, 255), # person 4 — yellow
]
_PS_CONNECTIONS = [
    (11, 12), (11, 23), (12, 24), (23, 24),
    (23, 25), (24, 26), (25, 27), (26, 28),
]


def draw_person_select_overlay(frame: np.ndarray, poses: list) -> np.ndarray:
    """Draw every candidate's skeleton in a distinct numbered color plus an
    instruction banner.

    Landmark positions are 0-1 fractions, so multiplying by the frame
    dimensions works regardless of display scale.
    """
    out = frame.copy()
    h, w = out.shape[:2]

    for i, lm_set in enumerate(poses):
        color = _PS_COLORS[i % len(_PS_COLORS)]
        pts   = [(int(lm.x * w), int(lm.y * h)) for lm in lm_set]

        for a, b in _PS_CONNECTIONS:
            if a < len(pts) and b < len(pts):
                cv2.line(out, pts[a], pts[b], color, 2, cv2.LINE_AA)

        for pt in pts:
            cv2.circle(out, pt, 5, color, -1, cv2.LINE_AA)
            cv2.circle(out, pt, 6, (0, 0, 0), 1, cv2.LINE_AA)

        # Numbered badge above mid-hip
        if len(pts) > 24:
            mx = (pts[23][0] + pts[24][0]) // 2
            my = (pts[23][1] + pts[24][1]) // 2 - 28
        elif pts:
            mx, my = pts[0][0], pts[0][1] - 28
        else:
            continue
        cv2.circle(out, (mx, my), 18, (10, 10, 10), -1, cv2.LINE_AA)
        cv2.circle(out, (mx, my), 18, color, 2,  cv2.LINE_AA)
        cv2.putText(out, str(i + 1), (mx - 7, my + 7),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2, cv2.LINE_AA)

    # Instruction banner
    cv2.rectangle(out, (0, 0), (w, 44), (10, 10, 30), -1)
    n = len(poses)
    cv2.putText(
        out,
        f"MediaPipe detected {n} person(s)  —  CLICK the PATIENT",
        (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (0, 230, 130), 2, cv2.LINE_AA)
    return out


def resolve_person_click(poses: list, click_xy, frame_w: int, frame_h: int,
                          leg: str):
    """Find the candidate pose nearest a click point (checking all landmarks
    of all poses), resolve which anatomical leg maps to the requested screen
    side, and return (hip, knee, ankle) pixel coordinates.

    Returns None only when no candidate pose is found near the click at
    all. When a pose is found but its ankle visibility is below 0.35,
    ankle is None within the returned tuple (hip and knee are still
    returned) rather than the whole result being None -- this preserves
    the "place the knee as a head start, ankle stays for manual
    placement" behavior callers may support.
    """
    if not poses:
        return None

    click = np.array(click_xy, dtype=np.float32)
    fw, fh = frame_w, frame_h

    # Find the person whose ANY landmark is nearest the click
    best_set  = None
    best_dist = float("inf")
    for lm_set in poses:
        for lm in lm_set:
            pt = np.array([lm.x * fw, lm.y * fh], dtype=np.float32)
            d  = float(np.linalg.norm(pt - click))
            if d < best_dist:
                best_dist = d
                best_set  = lm_set

    if best_set is None:
        return None

    # Pick the leg that matches the requested screen side.
    # BlazePose indices: anatomical LEFT hip=23,knee=25,ankle=27
    #                    anatomical RIGHT hip=24,knee=26,ankle=28
    # IMPORTANT: anatomical left/right is NOT the same as image left/right.
    # If the patient faces the camera their anatomical left appears on the
    # RIGHT side of the image (mirrored). Map by screen-space x-position so
    # "left"/"right" here means "the leg on that side of the screen."
    lh, lk, la = best_set[23], best_set[25], best_set[27]  # anatomical left
    rh, rk, ra = best_set[24], best_set[26], best_set[28]  # anatomical right

    want_image_left = (leg.lower() == "left")
    anat_left_is_img_left = (lk.x <= rk.x)

    if anat_left_is_img_left:
        img_left  = (lh, lk, la)
        img_right = (rh, rk, ra)
    else:
        img_left  = (rh, rk, ra)
        img_right = (lh, lk, la)

    h_lm, k_lm, a_lm = img_left if want_image_left else img_right

    hip  = np.array([h_lm.x * fw, h_lm.y * fh], dtype=np.float32)
    knee = np.array([k_lm.x * fw, k_lm.y * fh], dtype=np.float32)

    # Reject ankle if visibility is too low — a hallucinated ankle seeds the
    # tracker at a wrong position and the tracker carries that error forward.
    ankle = None
    if a_lm.visibility >= 0.35:
        ankle = np.array([a_lm.x * fw, a_lm.y * fh], dtype=np.float32)

    return (hip, knee, ankle)


# ─── main application ─────────────────────────────────────────────────────────

class PendulaticViewer(tk.Tk):

    # (key, display label, plot colour). Order sets the dropdown order and
    # the first entry is the default methodology.
    _METHODS = [
        ("rgb",  "RGB / HPE",  "#2563EB"),
        ("imu",  "iPhone IMU", "#D97706"),
        ("opti", "OptiTrack",  "#16A34A"),
    ]

    @classmethod
    def _method_key(cls, label: str) -> str:
        for k, lbl, _c in cls._METHODS:
            if lbl == label:
                return k
        return cls._METHODS[0][0]

    def __init__(self, initial_video: Optional[str] = None):
        super().__init__()
        self.title("Pendulastic Viewer — Wartenberg Test")
        self.configure(bg="#EEF2F7")
        self.minsize(900, 560)
        self.state("zoomed")   # start maximized

        # Video state
        self.cap:          Optional[cv2.VideoCapture] = None
        self.video_path:   str  = ""
        self.fps:          float = 30.0
        self.total_frames: int  = 0
        self._scale:       float = 1.0
        self._img_ox:      int   = 0    # x offset of image in canvas (centering)
        self._img_oy:      int   = 0    # y offset of image in canvas (centering)
        self._vid_w:       int   = 0    # native video width
        self._vid_h:       int   = 0    # native video height
        self._frame_idx:   int  = 0
        self._playing:     bool = False

        # Live camera state
        self._live:             bool = False
        self._recording:        bool = False
        self._writer:           Optional[cv2.VideoWriter] = None
        self._rec_path:         Optional[str] = None
        self._live_fi:          int  = 0
        self._last_live_frame:  Optional[np.ndarray] = None
        self._cam_w:            int  = 0
        self._cam_h:            int  = 0
        self._phone_mode:       bool = False   # True when streaming from phone
        # Countdown state
        self._countdown_remaining: int = 0
        self._countdown_after_id:  Optional[str] = None

        # iPhone goniometer (UDP angle stream) — always listening in the
        # background so the live readout works whenever the app is open,
        # independent of camera/recording state. Same protocol as
        # motive_mobile_sync.py: port 8888, plain float or {"angle": ...} JSON.
        self._goniometer_angle:     float = float("nan")
        self._goniometer_last_rx:   float = 0.0    # time.time() of last packet
        self._goniometer_lock                = threading.Lock()
        self._goniometer_recording: bool  = False
        self._goniometer_csv_file            = None
        self._goniometer_csv_writer          = None
        self._goniometer_csv_lock            = threading.Lock()
        self._goniometer_csv_path:  str   = ""     # path of the CSV from the last recording
        self._gonio_stop                     = threading.Event()
        self._gonio_bind_error:     str   = ""     # last socket failure, if any
        threading.Thread(
            target=self._goniometer_listener_worker, daemon=True, name="goniometer-udp"
        ).start()

        # iPhone IMU goniometer (Sensor Stream app → AHRS fusion). Two phones
        # stream raw MARG data; the relative Euler difference is the joint
        # angle (Andersson et al. 2024). Primary source; the UDP listener above
        # is a fallback for apps that send a pre-computed angle.
        self._imu_csv_path: str = ""
        self._imu_recording: bool = False
        # Which Euler component drives the recorded/plotted trace.
        # pitch = flexion/extension, the angle of interest for both the
        # Wartenberg knee test and the paper's hip analysis.
        self._imu_axis_var = tk.StringVar(value="pitch")
        self._imu_addr_next_scan: float = 0.0   # throttle for IP enumeration

        # ── Measurement methodologies ────────────────────────────────────────
        # Each methodology contributes one knee/hip angle series resampled onto
        # this video's frame grid, so PT scoring, the plot and export all share
        # a single index space. All are stored in the viewer's interior-angle
        # convention (180° = fully extended, decreasing with flexion), matching
        # load_optitrack().
        self._method_series: dict[str, list] = {}
        self._method_var  = tk.StringVar(value=self._METHODS[0][1])
        self._overlay_var = tk.BooleanVar(value=False)
        self._opti_csv_path: str = ""
        # Session tag written into unified exports and saved trials.
        self._session_var = tk.StringVar(value="pre")
        if _IMU_AVAIL:
            try:
                _imu.start()
            except Exception:
                pass   # port busy / no network — non-fatal, UI shows disconnected

        # Recording auto-stop timer: 0 = manual stop (click ✓ Done).
        self._rec_timer_var       = tk.StringVar(value="0")
        self._rec_stop_after_id: Optional[str] = None
        self._rec_started_at:    float = 0.0

        # Session / participant database
        self._db:             dict = _load_db()
        self._last_pt_params: Optional[dict] = None   # raw compute_pt_params output
        self._last_pt_s:      Optional[float] = None  # 4-param score
        self._last_pt_f:      Optional[float] = None  # 6-param score
        self._last_mas:       Optional[str]   = None  # MAS label
        self._manual_release_fi: Optional[int] = None  # user-pinned release frame
        self._release_fi:        Optional[int] = None  # resolved release frame (auto or manual)
        # Light-theme MAS colours (module _MAS_COLORS stays dark for History window)
        self._MAS_COLORS_LIGHT = {
            "0":  "#16A34A", "1":  "#65A30D", "1+": "#D97706",
            "2":  "#EA580C", "3":  "#DC2626", "4":  "#9B1C1C",
        }

        # Privacy
        self._blur_faces = tk.BooleanVar(value=False)
        self._face_casc:  Optional[object] = None   # lazy-loaded Haar frontal cascade
        self._prof_casc:  Optional[object] = None   # lazy-loaded Haar profile cascade

        # Ankle appearance templates learnt from user corrections (persist across
        # Track All runs so once a correction is made it sticks for the session)
        self._ankle_correct_template: Optional[np.ndarray] = None
        self._ankle_wrong_template:   Optional[np.ndarray] = None

        # Guided skeleton wizard (Set Skeleton button)
        self._skel_guide_active: bool = False
        self._skel_guide_step:   int  = 0      # 0=hip 1=knee 2=ankle
        self._skel_guide_tmp:    list = [None, None, None]

        # Tracking state — use MediaPipe when available, else LK optical flow
        self.tracker  = _MPTracker() if _MP_AVAIL else _Tracker()
        self.detector = _PatientDetector()
        self._angles: list = []
        self._trail:  list = []
        self._plot_annots: list = []   # matplotlib artists added by _annotate_plot

        # Marker placement mode
        self._mode    = "idle"        # idle | knee | ankle | hip | path
        self._hip_click:   Optional[tuple] = None
        self._knee_click:  Optional[tuple] = None
        self._ankle_click: Optional[tuple] = None

        # Person-select mode: MediaPipe shows all detected people, user clicks patient
        self._person_select_active: bool = False
        self._person_select_poses:  list = []   # list of lm-sets from last MP IMAGE detection

        # Path draw mode: {frame_idx: (vx, vy)} ankle keyframes in video coords
        self._path_keyframes: dict = {}

        # Pinned corrections: {frame_idx: np.ndarray} ankle positions the user
        # manually confirmed.  Retrack honours these and reinitialises the tracker
        # at each pin so drift doesn't undo them.
        self._ankle_pins: dict = {}

        # Trim range [_trim_start, _trim_end) in frame indices.
        # _trim_end == 0 means "not yet set" — reset to total_frames on video open.
        self._trim_start: int = 0
        self._trim_end:   int = 0

        # Release frame: the frame index where the assessor lets go of the leg.
        # All frames before this are frozen at the hold angle (eliminates pre-release
        # jitter).  None means "not set" — Track All runs the tracker on every frame.
        self._release_fi: Optional[int] = None
        self._tl_drag:    Optional[str] = None   # "left" | "right" during handle drag

        # Auto-start the phone/Expo backend so it is available as soon as the
        # viewer opens — no need to manually trigger "Phone Camera" first.
        if _PPS_AVAIL:
            try:
                _pps.start(upload_dir=os.path.join(BASE_DIR, "uploads"))
            except Exception:
                pass   # port already in use or network unavailable — non-fatal

        # Uploads directory watcher — tracks files already present at startup so
        # only videos that arrive during THIS session are auto-opened.
        self._viewer_start_time: float = time.time()
        self._uploads_seen: set        = set()
        self._uploads_next_scan: float = 0.0
        _udir_init = os.path.join(BASE_DIR, "uploads")
        if os.path.isdir(_udir_init):
            for _fn in os.listdir(_udir_init):
                if _fn.lower().endswith((".mp4", ".mov", ".avi", ".m4v", ".mkv")):
                    self._uploads_seen.add(os.path.join(_udir_init, _fn))

        self._build_ui()
        self._tick()

        # Start background webcam probe so _cmd_open_camera doesn't block the UI.
        self._available_cams: list = []   # [(idx, backend, w, h), ...]
        self._cam_probe_done: bool = False
        threading.Thread(
            target=self._probe_cameras_bg, daemon=True, name="cam-probe"
        ).start()

        # Keyboard shortcuts.
        # Space uses bind_all so it fires even when a toolbar button has keyboard
        # focus (buttons normally consume <space> to activate themselves, swallowing
        # the event before it can bubble up to self.bind).
        def _on_space_key(e):
            focused = self.focus_get()
            # Only block space for plain Entry / Text — not ttk.Combobox whose
            # inner widget also looks like an Entry via isinstance().
            wclass = focused.winfo_class() if focused else ""
            if wclass in ("Entry", "Text"):
                return          # let text-entry widgets use spacebar normally
            self._toggle_play()
            return "break"      # prevent the focused button from activating too
        self.bind_all("<KeyPress-space>", _on_space_key)
        self.bind("<Right>",  self._path_step_fwd)
        self.bind("<Left>",   self._path_step_bwd)
        self.bind_all("<Escape>", self._on_escape_key)

        if initial_video:
            self.after(100, lambda: self._open_video(initial_video))

    # ── iPhone goniometer UDP listener ───────────────────────────────────────

    def _goniometer_listener_worker(self):
        """Background daemon thread: listen for angle packets from the iPhone
        goniometer app and update the live readout. While a trial recording
        is active with 'Sync Goniometer' checked, also append rows to the
        open CSV writer. Runs for the lifetime of the app."""
        delay = 1.0
        while not self._gonio_stop.is_set():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((GONIOMETER_UDP_IP, GONIOMETER_UDP_PORT))
                sock.settimeout(1.0)
                delay = 1.0            # bound cleanly — reset the backoff
                self._gonio_bind_error = ""

                # Inner loop: only a socket-level failure escapes it. A bad
                # packet must never take the listener down.
                while not self._gonio_stop.is_set():
                    try:
                        data, _addr = sock.recvfrom(1024)
                    except socket.timeout:
                        continue       # idle tick; lets the stop flag be seen

                    try:
                        text = data.decode("utf-8", errors="ignore").strip()
                        if text.startswith("{"):
                            parsed = json.loads(text)
                            angle_val = float(parsed.get(
                                "angle", parsed.get("val", 0.0)))
                        else:
                            angle_val = float(text)
                    except (ValueError, json.JSONDecodeError, TypeError):
                        continue       # malformed packet — skip, keep listening

                    now = time.time()
                    with self._goniometer_lock:
                        self._goniometer_angle   = angle_val
                        self._goniometer_last_rx = now
                    if self._goniometer_recording:
                        with self._goniometer_csv_lock:
                            if self._goniometer_csv_writer is not None:
                                try:
                                    self._goniometer_csv_writer.writerow(
                                        [f"{now:.4f}", f"{angle_val:.2f}"])
                                except (ValueError, OSError):
                                    pass   # file closed mid-write; not fatal
            except OSError as e:
                # Adapter reset, hotspot blip, or the port briefly taken.
                self._gonio_bind_error = f"{type(e).__name__}: {e}"
                print(f"[Goniometer] UDP {GONIOMETER_UDP_PORT} hiccup: {e} — "
                      f"retrying in {delay:.0f}s")
            except Exception as e:
                self._gonio_bind_error = f"{type(e).__name__}: {e}"
                print(f"[Goniometer] listener error: {type(e).__name__}: {e}")
            finally:
                # sock may be None if socket() itself raised — guard the close.
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

            # Wait in slices so shutdown is prompt, with capped backoff.
            if self._gonio_stop.wait(timeout=delay):
                break
            delay = min(delay * 2, 20.0)

    # ── iPhone IMU goniometer commands ───────────────────────────────────────

    def _cmd_imu_zero(self):
        if not _IMU_AVAIL:
            return
        st = _imu.get_state()
        if not (st["proximal"]["connected"] or st["distal"]["connected"]):
            messagebox.showinfo(
                "No phones connected",
                "No IMU data is arriving yet.\n\n"
                f"In the Sensor Stream app set the address to "
                f"{_imu.get_local_ip()}:{_imu.PORT} and enable Accelerometer, "
                "Gyroscope and Magnetometer.")
            return
        _imu.zero()
        self._status.config(
            text="IMU zeroed — current posture is now the 0° reference.")

    def _cmd_imu_swap(self):
        if _IMU_AVAIL:
            _imu.swap_roles()
            self._status.config(text="IMU segments swapped (proximal ⇄ distal).")

    def _cmd_imu_reset(self):
        """Forget connected phones so roles are reassigned, and retry the
        server bind if it failed at startup (e.g. the port was busy)."""
        if not _IMU_AVAIL:
            return
        _imu.reset_devices()
        _imu.clear_zero()
        if not _imu.get_state()["running"]:
            _imu.start()
            err = _imu.bind_error()
            if err:
                messagebox.showwarning("IMU server offline", err)
                return
        self._status.config(
            text="IMU devices cleared — reconnect the phones to reassign segments.")

    def _imu_axis_value(self, angles: dict) -> float:
        return angles.get(self._imu_axis_var.get(), float("nan"))

    def _imu_single_mode(self) -> bool:
        """One IMU on the distal segment only.

        Valid for the Wartenberg test, where the thigh is supported and
        stationary: the shank's absolute orientation IS the knee angle once
        zeroed against the extended limb. The assumption is only as good as
        the thigh actually holding still — cross-check against the RGB trace
        with Overlay to confirm it held."""
        return self._imu_segpair_var.get().startswith("Single")

    def _cmd_imu_copy_addr(self):
        """Copy host:port to the clipboard for typing into the phone app."""
        addr = self._imu_addr_var.get()
        self.clipboard_clear()
        self.clipboard_append(addr)
        self._status.config(text=f"Copied {addr} — enter this in the Sensor Stream app.")

    def _cmd_imu_network_help(self):
        """Troubleshooting for 'the app cannot connect'.

        The usual cause on Windows is that the running interpreter has no
        inbound firewall rule, so the port is bound but unreachable from the
        phone. Adding that rule needs administrator rights, so the exact
        command is shown for the operator to run rather than applied here."""
        ips  = _imu.get_all_local_ips() if _IMU_AVAIL else ["?"]
        port = _imu.PORT if _IMU_AVAIL else 5000
        exe  = sys.executable
        err  = _imu.bind_error() if _IMU_AVAIL else "IMU module not importable"
        st   = _imu.get_state() if _IMU_AVAIL else {"running": False}

        # netsh works in an elevated cmd.exe *or* PowerShell, so it is the one
        # put on the clipboard; the PowerShell-native form is shown as well.
        netsh = (f'netsh advfirewall firewall add rule '
                 f'name="Pendulastic IMU {port}" dir=in action=allow '
                 f'protocol=TCP localport={port} program="{exe}" enable=yes')
        psrule = (f'New-NetFirewallRule -DisplayName "Pendulastic IMU {port}" '
                  f'-Direction Inbound -Action Allow -Protocol TCP '
                  f'-LocalPort {port} -Program "{exe}"')

        lines = [
            f"Server listening: {'yes' if st.get('running') else 'NO'}"
            + (f"  ({err})" if err else ""),
            f"Address for the app:  {ips[0]}:{port}",
        ]
        if len(ips) > 1:
            lines.append("Other addresses:      " + ", ".join(ips[1:]))
        lines += [
            "",
            "Checklist:",
            "  1. Phone and laptop on the SAME network. On a Personal Hotspot",
            "     the laptop address starts with 172.20.10.",
            f"  2. In Sensor Stream enter exactly  <address>:{port}  (no http://,",
            "     no path) and enable Accelerometer + Gyroscope + Magnetometer.",
            "  3. Windows Firewall must allow inbound TCP for THIS interpreter:",
            f"     {exe}",
            "     A rule registered for a different python.exe does not cover",
            "     the venv one. If the phone still cannot connect, open an",
            "     Administrator command prompt and paste the netsh command",
            "     below (already on your clipboard), then press Reconnect.",
            "",
            "  4. Only ONE Pendulastic app can own the IMU port. If master_app.py",
            "     is acquiring, the phones stream to it, not to this viewer. To",
            "     run both, start one of them on a different port, e.g.:",
            "         set PENDULASTIC_IMU_PORT=5001",
            "         python pendulastic_viewer.py",
            "     and enter that port in the Sensor Stream app.",
            "",
            "netsh (cmd.exe or PowerShell, as Administrator):",
            f"  {netsh}",
            "",
            "PowerShell equivalent:",
            f"  {psrule}",
        ]
        try:
            self.clipboard_clear()
            self.clipboard_append(netsh)
        except tk.TclError:
            pass
        messagebox.showinfo("iPhone cannot connect — network help",
                            "\n".join(lines))

    def _cmd_imu_resync(self):
        """Discard clock-offset samples and re-estimate from scratch."""
        if not _IMU_AVAIL:
            return
        _imu.reset_sync()
        self._status.config(text="Re-syncing phone clocks…")

    _SYNC_STYLE = {
        "waiting":  ("○", "#94A3B8", "Waiting for phones…"),
        "syncing":  ("◐", "#D97706", "Syncing…"),
        "unstable": ("⚠", "#DC2626", "Sync unstable"),
        "synced":   ("●", "#16A34A", "Synced"),
    }

    def _update_sync_ui(self, sync: dict):
        """Reflect clock-alignment state in the Time Sync Recording block."""
        icon, colour, text = self._SYNC_STYLE.get(
            sync["state"], ("○", "#94A3B8", sync["state"]))
        if sync["state"] == "syncing":
            text = f"Syncing…  {int(sync['progress'] * 100)}%"
        elif sync["state"] == "synced" and sync.get("offset_s") is not None:
            text = f"Synced   Δ {sync['offset_s']:+.3f} s"
        self._imu_sync_lbl.config(text=f"{icon}  {text}", fg=colour)
        self._imu_sync_detail.config(text=sync.get("detail", ""))

        w = max(1, self._sync_bar.winfo_width())
        frac = 1.0 if sync["state"] in ("synced", "unstable") else sync["progress"]
        self._sync_bar.coords(self._sync_bar_fill, 0, 0, int(w * frac), 6)
        self._sync_bar.itemconfig(self._sync_bar_fill, fill=colour)

    def _sync_ready(self) -> bool:
        """True when recording may start given the operator's sync preference.

        Two distinct conditions are checked. No signal at all is always a
        blocker — recording would write an empty goniometer CSV — and that
        holds even when 'wait for sync' is off, because that switch is an
        override for imperfect alignment, not for a missing sensor. Clock
        alignment itself is only enforced when the operator asks for it."""
        if not (_IMU_AVAIL and self._var_goniometer.get()):
            return True

        st = _imu.get_state()
        if not (st["proximal"]["connected"] or st["distal"]["connected"]):
            return False                      # no phone streaming at all

        if not self._var_wait_sync.get():
            return True
        return _imu.sync_status()["state"] == "synced"

    # ── build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Dark palette tokens
        BG      = "#EEF2F7"   # light blue-gray canvas surround
        SURFACE = "#FFFFFF"   # white card background
        PANEL   = "#F1F5F9"   # off-white panel / secondary
        BTN     = "#E2E8F0"   # button face
        BTN_ACT = "#2563EB"   # button active (brand blue)
        FG      = "#0F172A"   # primary text (slate-900)
        FG2     = "#475569"   # secondary text (slate-600)
        FG3     = "#94A3B8"   # label / muted text (slate-400)
        BORDER  = "#CBD5E1"   # border (slate-300)
        MONO    = ("Consolas", "Cascadia Code", "Courier New")

        # ── toolbar ──────────────────────────────────────────────────────────
        toolbar = tk.Frame(self, bg=PANEL, pady=7, padx=8)
        toolbar.pack(fill="x", side="top")

        _b = dict(bg=BTN, fg=FG, relief="flat", bd=0, padx=11, pady=5,
                  font=("Segoe UI", 9), cursor="hand2",
                  activebackground=BTN_ACT, activeforeground="#FFFFFF")

        def sep():
            ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=5, pady=3)

        tk.Button(toolbar, text="📂 Open",           command=self._cmd_open,         **_b).pack(side="left", padx=2)
        tk.Button(toolbar, text="↺ Reset Trim",       command=self._cmd_reset_trim,   **_b).pack(side="left", padx=2)
        self._btn_cam = tk.Button(toolbar, text="📷 Camera", command=self._cmd_open_camera, **_b)
        self._btn_cam.pack(side="left", padx=2)
        tk.Button(toolbar, text="👤 Pick Person",    command=self._cmd_pick_person,    **_b).pack(side="left", padx=2)
        sep()
        self._btn_knee   = tk.Button(toolbar, text="🦵 Knee",  command=self._cmd_knee,   **_b)
        self._btn_ankle  = tk.Button(toolbar, text="🦶 Ankle", command=self._cmd_ankle,  **_b)
        self._btn_hip    = tk.Button(toolbar, text="⬜ Hip",   command=self._cmd_hip,    **_b)
        self._btn_knee.pack(side="left", padx=2)
        self._btn_ankle.pack(side="left", padx=2)
        self._btn_hip.pack(side="left", padx=2)
        sep()
        tk.Button(toolbar, text="▶▶ Track All",  command=self._cmd_track_all,  **_b).pack(side="left", padx=2)
        self._btn_retrack = tk.Button(toolbar, text="⟳ Retrack →",
                                       command=self._cmd_retrack_from_here,
                                       state="disabled", **_b)
        self._btn_retrack.pack(side="left", padx=2)
        self._btn_release = tk.Button(toolbar, text="✂ Mark Release",
                                      command=self._cmd_mark_release, **_b)
        self._btn_release.pack(side="left", padx=2)
        tk.Button(toolbar, text="↩ Reset",        command=self._cmd_reset,      **_b).pack(side="left", padx=2)
        sep()
        self._btn_path = tk.Button(toolbar, text="✏ Path",
                                    command=self._cmd_path_mode, **_b)
        self._btn_path.pack(side="left", padx=2)
        self._btn_apply_path = tk.Button(toolbar, text="✓ Apply",
                                          command=self._cmd_apply_path,
                                          state="disabled", **_b)
        self._btn_apply_path.pack(side="left", padx=2)
        self._btn_clear_path = tk.Button(toolbar, text="✗ Clear",
                                          command=self._cmd_clear_path,
                                          state="disabled", **_b)
        self._btn_clear_path.pack(side="left", padx=2)
        sep()
        tk.Button(toolbar, text="💾 Export CSV",  command=self._cmd_export,     **_b).pack(side="left", padx=2)
        tk.Button(toolbar, text="🗃 Unified CSV", command=self._cmd_export_unified, **_b).pack(side="left", padx=2)
        self._btn_export_vid = tk.Button(toolbar, text="🎬 Export Video",
                                         command=self._cmd_export_annotated_video, **_b)
        self._btn_export_vid.pack(side="left", padx=2)
        tk.Button(toolbar, text="📈 Compare",     command=self._cmd_compare_models, **_b).pack(side="left", padx=2)

        # participant info (right side)
        self._vid_lbl = tk.Label(toolbar, text="No video loaded",
                                  bg=PANEL, fg=FG3, font=("Segoe UI", 8), anchor="e")
        self._vid_lbl.pack(side="right", padx=8)

        # Privacy: face-blur toggle
        sep()
        tk.Checkbutton(toolbar, text="🔒 Blur Faces",
                       variable=self._blur_faces,
                       bg=PANEL, fg=FG, selectcolor=PANEL,
                       activebackground=PANEL, activeforeground=FG,
                       font=("Segoe UI", 8), bd=0, highlightthickness=0,
                       cursor="hand2").pack(side="right", padx=4)

        # ── session toolbar (row 2) ───────────────────────────────────────────
        sbar = tk.Frame(self, bg=BG, pady=5, padx=8)
        sbar.pack(fill="x", side="top")

        _sb = dict(bg=BTN, fg=FG, relief="flat", bd=0, padx=9, pady=3,
                   font=("Segoe UI", 9), cursor="hand2",
                   activebackground=BTN_ACT, activeforeground="#FFFFFF")

        # Participant
        tk.Label(sbar, text="Participant:", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._part_var = tk.StringVar(value="")
        self._part_combo = ttk.Combobox(sbar, textvariable=self._part_var,
                                         width=20, state="readonly",
                                         font=("Segoe UI", 9))
        self._part_combo.pack(side="left", padx=(4, 2))
        tk.Button(sbar, text="+ New", command=self._cmd_new_participant,
                  **_sb).pack(side="left", padx=(0, 12))

        # Leg
        tk.Label(sbar, text="Leg:", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._leg_var = tk.StringVar(value="right")
        for txt, val in ((" Left", "left"), (" Right", "right")):
            tk.Radiobutton(sbar, text=txt, variable=self._leg_var, value=val,
                           bg=BG, fg=FG, selectcolor=BTN, activebackground=BG,
                           font=("Segoe UI", 9), cursor="hand2").pack(side="left", padx=1)

        ttk.Separator(sbar, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)

        # Position
        tk.Label(sbar, text="Position:", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._pos_var = tk.StringVar(value="1")
        self._pos_combo = ttk.Combobox(sbar, textvariable=self._pos_var,
                                       values=["1", "2", "3", "4"],
                                       width=3, state="readonly",
                                       font=("Segoe UI", 9))
        self._pos_combo.pack(side="left", padx=(2, 0))

        ttk.Separator(sbar, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)

        # Trial number — auto-filled with the next unused number for this
        # participant+leg, but editable so a trial can be re-recorded.
        tk.Label(sbar, text="Trial:", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._trial_var = tk.StringVar(value="1")
        tk.Spinbox(sbar, textvariable=self._trial_var, from_=1, to=99,
                   width=3, font=("Segoe UI", 9), relief="flat",
                   bg="#F1F5F9", fg=FG, buttonbackground=BTN,
                   highlightthickness=1, highlightbackground=BORDER).pack(
                       side="left", padx=(2, 2))
        tk.Button(sbar, text="↻", command=self._refresh_trial_number,
                  **_sb).pack(side="left", padx=(0, 2))
        # Keep the suggested trial number in step with participant/leg changes.
        self._part_combo.bind("<<ComboboxSelected>>",
                              lambda e: self._refresh_trial_number())
        self._leg_var.trace_add("write", lambda *a: self._refresh_trial_number())

        ttk.Separator(sbar, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)

        # Session tag — distinguishes pre- and post-intervention recordings in
        # the unified export and in saved trials.
        tk.Label(sbar, text="Session:", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        ttk.Combobox(sbar, textvariable=self._session_var,
                     values=["pre", "post"], width=5, state="readonly",
                     font=("Segoe UI", 9)).pack(side="left", padx=(2, 0))

        ttk.Separator(sbar, orient="vertical").pack(side="left", fill="y", padx=8, pady=2)

        # Methodology switcher — swaps which measurement drives the plot,
        # the stats and the PT score.
        tk.Label(sbar, text="Method:", bg=BG, fg=FG2,
                 font=("Segoe UI", 9)).pack(side="left")
        self._method_combo = ttk.Combobox(
            sbar, textvariable=self._method_var,
            values=[lbl for _k, lbl, _c in self._METHODS],
            width=12, state="readonly", font=("Segoe UI", 9))
        self._method_combo.pack(side="left", padx=(2, 2))
        self._method_combo.bind("<<ComboboxSelected>>",
                                lambda e: self._cmd_set_method())
        tk.Checkbutton(sbar, text="Overlay all", variable=self._overlay_var,
                       command=self._refresh_overlay,
                       bg=BG, fg=FG, selectcolor=BTN, activebackground=BG,
                       font=("Segoe UI", 9), cursor="hand2").pack(side="left")
        tk.Button(sbar, text="＋ OptiTrack", command=self._cmd_load_optitrack,
                  **_sb).pack(side="left", padx=(2, 0))

        ttk.Separator(sbar, orient="vertical").pack(side="left", fill="y", padx=10, pady=2)

        self._btn_save_trial = tk.Button(sbar, text="💾 Save Trial",
                                          command=self._cmd_save_trial,
                                          state="disabled", **_sb)
        self._btn_save_trial.pack(side="left", padx=2)
        tk.Button(sbar, text="📊 Dashboard", command=self._cmd_history,
                  **_sb).pack(side="left", padx=2)
        tk.Button(sbar, text="🔬 Validate vs OptiTrack",
                  command=self._cmd_validate, **_sb).pack(side="left", padx=2)

        self._refresh_participant_list()

        # ── content area ─────────────────────────────────────────────────────
        # Vertical PanedWindow: drag the sash to resize video vs. graph.
        paned = tk.PanedWindow(self, orient="vertical", bg=PANEL,
                                sashwidth=7, sashpad=0,
                                relief="flat", bd=0, opaqueresize=True)
        paned.pack(fill="both", expand=True)
        self._paned = paned

        # ─ top pane: horizontal split — video | stats ─
        content = tk.Frame(paned, bg=BG)
        paned.add(content, stretch="always", minsize=200)

        h_paned = tk.PanedWindow(content, orient="horizontal", bg=PANEL,
                                  sashwidth=6, sashpad=0, relief="flat", bd=0,
                                  opaqueresize=True)
        h_paned.pack(fill="both", expand=True)
        self._h_paned = h_paned

        # ─ video canvas (left pane) ─
        vid_wrap = tk.Frame(h_paned, bg=BG)
        h_paned.add(vid_wrap, stretch="always", minsize=300)

        self._canvas = tk.Canvas(vid_wrap, bg="#000000", highlightthickness=0,
                                  cursor="crosshair")
        self._canvas.pack(fill="both", expand=True)
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Configure>", self._on_canvas_resize)

        # instruction overlay text
        self._canvas.create_text(500, 280,
            text="Open a video to begin",
            fill=FG3, font=("Segoe UI", 14), tags="hint")

        # ─ right panel: scrollable stats + PT score (right pane) ─
        right_outer = tk.Frame(h_paned, bg=BG)
        h_paned.add(right_outer, stretch="never", minsize=180, width=440)

        _right_cv = tk.Canvas(right_outer, bg=BG, highlightthickness=0)
        _right_sb = ttk.Scrollbar(right_outer, orient="vertical",
                                   command=_right_cv.yview)
        _right_cv.configure(yscrollcommand=_right_sb.set)
        _right_sb.pack(side="right", fill="y", pady=4)
        _right_cv.pack(side="left", fill="both", expand=True, pady=4)

        right = tk.Frame(_right_cv, bg=BG)
        _right_win = _right_cv.create_window((0, 0), window=right, anchor="nw")

        right.bind("<Configure>",
                   lambda e: _right_cv.configure(
                       scrollregion=_right_cv.bbox("all")))
        _right_cv.bind("<Configure>",
                       lambda e: _right_cv.itemconfig(_right_win, width=e.width))

        def _right_scroll_enter(_e=None):
            _right_cv.bind_all("<MouseWheel>",
                lambda e: _right_cv.yview_scroll(int(-1*(e.delta/120)), "units"))
        def _right_scroll_leave(_e=None):
            _right_cv.unbind_all("<MouseWheel>")
        right_outer.bind("<Enter>", lambda e: _right_scroll_enter())
        right_outer.bind("<Leave>", lambda e: _right_scroll_leave())

        # helper: build a collapsible card section inside `right`
        def _panel(title: str):
            """Return the body frame; the header has ▸/▾ toggle and ✕ close."""
            outer_card = tk.Frame(right, bg=BG)
            outer_card.pack(fill="x", pady=(0, 6))

            hdr = tk.Frame(outer_card, bg=PANEL, pady=5, padx=10)
            hdr.pack(fill="x")

            state = {"open": True}
            body  = tk.Frame(outer_card, bg=SURFACE, padx=14, pady=12,
                             highlightthickness=1, highlightbackground=BORDER)
            body.pack(fill="x")

            btn_var = tk.StringVar(value=f"▾  {title}")
            tog = tk.Button(hdr, textvariable=btn_var, command=None,
                            bg=PANEL, fg=FG2, relief="flat", bd=0,
                            font=("Segoe UI", 9, "bold"), anchor="w",
                            cursor="hand2",
                            activebackground=PANEL, activeforeground=FG)
            tog.pack(side="left", fill="x", expand=True)

            def _toggle():
                if state["open"]:
                    body.pack_forget()
                    btn_var.set(f"▸  {title}")
                else:
                    body.pack(fill="x")
                    btn_var.set(f"▾  {title}")
                state["open"] = not state["open"]

            tog.config(command=_toggle)
            return body

        # ── KNEE ANGLE card ───────────────────────────────────────────────────
        stats_wrap = _panel("KNEE ANGLE")

        self._stat_cur  = self._stat_card(stats_wrap, "Current",   "#2563EB", SURFACE, MONO)
        self._stat_min  = self._stat_card(stats_wrap, "Min",       "#EA580C", SURFACE, MONO)
        self._stat_rest = self._stat_card(stats_wrap, "Rest",      "#16A34A", SURFACE, MONO)
        self._stat_amp  = self._stat_card(stats_wrap, "Amplitude", "#475569", SURFACE, MONO)

        # ── iPHONE GONIOMETER (IMU) card ──────────────────────────────────────
        # Two phones running the Sensor Stream app stream raw accel/gyro/mag;
        # an AHRS filter fuses each into Euler angles and the relative
        # difference is the joint angle (Andersson et al., Sensors 2024).
        imu_wrap = _panel("iPHONE GONIOMETER (IMU)")
        _IB = SURFACE

        # Address to type into the app. On a Personal Hotspot the laptop is
        # 172.20.10.x; that range is ranked first so the operator is not shown
        # a VPN or link-local address the phone could never reach.
        addr_row = tk.Frame(imu_wrap, bg=_IB)
        addr_row.pack(fill="x")
        self._imu_addr_var = tk.StringVar(
            value=f"{_imu.get_local_ip()}:{_imu.PORT}" if _IMU_AVAIL else "—")
        tk.Label(addr_row, text="Enter in app:", bg=_IB, fg=FG3,
                 font=("Segoe UI", 8)).pack(side="left")
        tk.Entry(addr_row, textvariable=self._imu_addr_var, width=20,
                 font=(MONO[0], 9, "bold"), relief="flat", bg="#F1F5F9",
                 fg="#0F172A", readonlybackground="#F1F5F9",
                 state="readonly").pack(side="left", padx=(4, 4))
        tk.Button(addr_row, text="⧉", command=self._cmd_imu_copy_addr,
                  bg=BTN, fg=FG, relief="flat", bd=0, padx=6, pady=1,
                  font=("Segoe UI", 8), cursor="hand2",
                  activebackground=BTN_ACT).pack(side="left")
        # Alternate addresses, shown only when the host is multi-homed.
        self._imu_alt_lbl = tk.Label(imu_wrap, text="", bg=_IB, fg=FG3,
                                      font=("Segoe UI", 7), anchor="w",
                                      wraplength=380, justify="left")
        self._imu_alt_lbl.pack(fill="x")
        tk.Label(imu_wrap,
                 text="Enable Accelerometer + Gyroscope + Magnetometer on both phones.",
                 bg=_IB, fg=FG3, font=("Segoe UI", 7), anchor="w",
                 wraplength=380, justify="left").pack(fill="x", pady=(0, 6))

        # Segment pair: which two body segments the phones are strapped to.
        seg_row = tk.Frame(imu_wrap, bg=_IB)
        seg_row.pack(fill="x", pady=(0, 4))
        tk.Label(seg_row, text="Segments:", bg=_IB, fg=FG3,
                 font=("Segoe UI", 8)).pack(side="left")
        self._imu_segpair_var = tk.StringVar(value="Thigh → Shank (knee)")
        ttk.Combobox(seg_row, textvariable=self._imu_segpair_var,
                     values=["Thigh → Shank (knee)",
                             "Torso → Thigh (hip)",
                             "Single IMU — shank only"],
                     width=22, state="readonly",
                     font=("Segoe UI", 8)).pack(side="left", padx=(4, 0))

        # Per-phone connection status
        self._imu_prox_lbl = tk.Label(imu_wrap, text="○  proximal — waiting",
                                       bg=_IB, fg=FG3, font=(MONO[0], 8),
                                       anchor="w")
        self._imu_prox_lbl.pack(fill="x")
        self._imu_dist_lbl = tk.Label(imu_wrap, text="○  distal — waiting",
                                       bg=_IB, fg=FG3, font=(MONO[0], 8),
                                       anchor="w")
        self._imu_dist_lbl.pack(fill="x")
        self._imu_srv_lbl = tk.Label(imu_wrap, text="", bg=_IB, fg="#DC2626",
                                      font=("Segoe UI", 7), anchor="w",
                                      wraplength=380, justify="left")
        self._imu_srv_lbl.pack(fill="x", pady=(0, 6))

        # Live relative Euler angles
        self._imu_flex_lbl = self._stat_card(imu_wrap, "Flex/Ext",  "#2563EB", _IB, MONO)
        self._imu_abd_lbl  = self._stat_card(imu_wrap, "Abd/Add",   "#7C3AED", _IB, MONO)
        self._imu_rot_lbl  = self._stat_card(imu_wrap, "Rotation",  "#0891B2", _IB, MONO)

        # ── Time Sync Recording ───────────────────────────────────────────────
        # The phones stamp packets with their own clocks; the video, the
        # goniometer CSV and motive_mobile_sync all use this laptop's
        # time.time(). This block estimates that offset and blocks recording
        # until it is trusted, so every modality lands on one timeline.
        ttk.Separator(imu_wrap, orient="horizontal").pack(fill="x", pady=(8, 6))
        sync_hdr = tk.Frame(imu_wrap, bg=_IB)
        sync_hdr.pack(fill="x")
        tk.Label(sync_hdr, text="TIME SYNC RECORDING", bg=_IB, fg=FG3,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        self._var_wait_sync = tk.BooleanVar(value=True)
        tk.Checkbutton(sync_hdr, text="wait for sync",
                       variable=self._var_wait_sync, bg=_IB, fg=FG2,
                       selectcolor=BTN, activebackground=_IB,
                       font=("Segoe UI", 7), cursor="hand2").pack(side="right")

        self._imu_sync_lbl = tk.Label(imu_wrap, text="○  Waiting for phones…",
                                       bg=_IB, fg=FG3, font=(MONO[0], 9, "bold"),
                                       anchor="w")
        self._imu_sync_lbl.pack(fill="x", pady=(3, 1))
        # Progress bar for the sample-collection phase.
        self._sync_bar = tk.Canvas(imu_wrap, height=6, bg="#E2E8F0",
                                    highlightthickness=0)
        self._sync_bar.pack(fill="x", pady=(0, 2))
        self._sync_bar_fill = self._sync_bar.create_rectangle(
            0, 0, 0, 6, fill="#2563EB", outline="")
        self._imu_sync_detail = tk.Label(imu_wrap, text="", bg=_IB, fg=FG3,
                                          font=("Segoe UI", 7), anchor="w",
                                          wraplength=380, justify="left")
        self._imu_sync_detail.pack(fill="x")
        tk.Button(imu_wrap, text="↻ Re-sync clocks", command=self._cmd_imu_resync,
                  bg=BTN, fg=FG, relief="flat", bd=0, padx=8, pady=3,
                  font=("Segoe UI", 8), cursor="hand2",
                  activebackground=BTN_ACT,
                  activeforeground="#FFFFFF").pack(anchor="w", pady=(4, 0))

        ttk.Separator(imu_wrap, orient="horizontal").pack(fill="x", pady=(8, 6))

        axis_row = tk.Frame(imu_wrap, bg=_IB)
        axis_row.pack(fill="x", pady=(4, 2))
        tk.Label(axis_row, text="Record axis:", bg=_IB, fg=FG3,
                 font=("Segoe UI", 8)).pack(side="left")
        for _txt, _val in (("Flex/Ext", "pitch"), ("Abd/Add", "roll"), ("Rot", "yaw")):
            tk.Radiobutton(axis_row, text=_txt, variable=self._imu_axis_var,
                           value=_val, bg=_IB, fg=FG2, selectcolor=BTN,
                           activebackground=_IB, font=("Segoe UI", 8),
                           cursor="hand2").pack(side="left", padx=1)

        _bi = dict(bg=BTN, fg=FG, relief="flat", bd=0, padx=8, pady=3,
                   font=("Segoe UI", 8), cursor="hand2",
                   activebackground=BTN_ACT, activeforeground="#FFFFFF")
        imu_btns = tk.Frame(imu_wrap, bg=_IB)
        imu_btns.pack(fill="x", pady=(4, 0))
        tk.Button(imu_btns, text="⊙ Zero", command=self._cmd_imu_zero,
                  **_bi).pack(side="left", padx=(0, 4))
        tk.Button(imu_btns, text="⇄ Swap", command=self._cmd_imu_swap,
                  **_bi).pack(side="left", padx=(0, 4))
        tk.Button(imu_btns, text="↺ Reconnect", command=self._cmd_imu_reset,
                  **_bi).pack(side="left", padx=(0, 4))
        tk.Button(imu_btns, text="🛜 Can't connect?",
                  command=self._cmd_imu_network_help, **_bi).pack(side="left")
        tk.Label(imu_wrap,
                 text="Zero with the participant in the reference posture "
                      "(paper uses a 5 s upright hold).",
                 bg=_IB, fg=FG3, font=("Segoe UI", 7, "italic"), anchor="w",
                 wraplength=380, justify="left").pack(fill="x", pady=(4, 0))
        self._imu_mode_note = tk.Label(
            imu_wrap, text="", bg=_IB, fg="#D97706",
            font=("Segoe UI", 7, "italic"), anchor="w",
            wraplength=380, justify="left")
        self._imu_mode_note.pack(fill="x")

        # ── POPOVIC PT SCORE card ─────────────────────────────────────────────
        _PB = SURFACE
        pt_wrap = _panel("POPOVIĆ PT SCORE")

        sc_row = tk.Frame(pt_wrap, bg=_PB)
        sc_row.pack(fill="x", pady=(0, 2))
        self._pt_score_key_lbl = tk.Label(sc_row, text="PT score (4 params selected):",
                                           bg=_PB, fg=FG3, font=("Segoe UI", 8))
        self._pt_score_key_lbl.pack(side="left")
        self._pt_score_lbl = tk.Label(sc_row, text="—", bg=_PB, fg=FG2,
                                       font=(MONO[0], 14, "bold"))
        self._pt_score_lbl.pack(side="left", padx=(6, 0))

        # PT score gauge: healthy / borderline / spastic zones
        # Zones calibrated from n=13 control, n=8 MS participants
        _GW, _GH, _PTOP = 260, 14, 12
        self._gauge_w   = _GW
        self._gauge_top = _PTOP
        self._gauge_h   = _GH
        _g_end = int(_GW * PT_HEALTHY_MAX)    # green → amber boundary
        _a_end = int(_GW * PT_BORDERLINE_MAX)  # amber → red boundary
        gauge_row = tk.Frame(pt_wrap, bg=_PB)
        gauge_row.pack(fill="x", pady=(6, 2))
        self._pt_gauge = tk.Canvas(gauge_row, width=_GW, height=_PTOP + _GH + 20,
                                    bg=_PB, highlightthickness=0)
        self._pt_gauge.pack(anchor="w")
        self._pt_gauge.create_rectangle(0,      _PTOP, _g_end, _PTOP + _GH,
                                         fill="#16A34A", outline="")
        self._pt_gauge.create_rectangle(_g_end, _PTOP, _a_end, _PTOP + _GH,
                                         fill="#D97706", outline="")
        self._pt_gauge.create_rectangle(_a_end, _PTOP, _GW,    _PTOP + _GH,
                                         fill="#DC2626", outline="")
        # Zone labels spread across full canvas width to avoid overlap —
        # the healthy and borderline zones (9 % and 20 % of width) are too
        # narrow to center text in them.
        self._pt_gauge.create_text(2,           _PTOP + _GH + 9,
                                    text="Healthy", fill="#16A34A",
                                    font=("Segoe UI", 7, "bold"), anchor="w")
        self._pt_gauge.create_text(_GW // 2,    _PTOP + _GH + 9,
                                    text="Borderline", fill="#D97706",
                                    font=("Segoe UI", 7, "bold"), anchor="center")
        self._pt_gauge.create_text(_GW - 2,     _PTOP + _GH + 9,
                                    text="Spastic", fill="#DC2626",
                                    font=("Segoe UI", 7, "bold"), anchor="e")
        # Pointer triangle (▼) — tip at bar top, updated each score
        self._gauge_ptr = self._pt_gauge.create_polygon(
            0, _PTOP, 0, 0, 0, 0, fill="#0F172A", outline="white", width=1,
            state="hidden")

        ttk.Separator(pt_wrap, orient="horizontal").pack(fill="x", pady=(6, 4))

        # Parameter table header
        _th = tk.Frame(pt_wrap, bg=_PB)
        _th.pack(fill="x")
        _th.columnconfigure(1, weight=1)
        tk.Label(_th, text="", bg=_PB, width=2).grid(row=0, column=0)
        tk.Label(_th, text="Parameter",  bg=_PB, fg=FG3,
                 font=("Segoe UI", 8), anchor="w").grid(row=0, column=1, sticky="w")
        tk.Label(_th, text="Score", bg=_PB, fg=FG3,
                 font=("Segoe UI", 8)).grid(row=0, column=2, padx=(0, 4))
        tk.Label(_th, text=" Ref", bg=_PB, fg=FG3,
                 font=("Segoe UI", 8)).grid(row=0, column=3)

        ttk.Separator(pt_wrap, orient="horizontal").pack(fill="x", pady=(2, 4))

        # Parameter rows: ☑ | description | score | ref
        # area_ratio and f excluded by default (unreliable for vision-based angles)
        _HR = HEALTHY_REF if _PT_AVAIL else {}
        _PT_PARAM_DESCS = [
            ("Relaxation (R₂ₙ)",  "R2n",           _HR.get("R2n",           1.012), True ),
            ("Swing count (N)",    "N",              _HR.get("N",             7.0),   True ),
            ("Excursion (φmax)",   "phi_max_ratio",  _HR.get("phi_max_ratio", 0.787), True ),
            ("Peak vel. (ωmax)",   "omega_max_n",    _HR.get("omega_max_n",   5.692), True ),
            ("Frequency (f) †",   "f",              _HR.get("f",             1.10),  False),
            ("Area ratio (AR) †", "area_ratio",     _HR.get("area_ratio",    0.09),  False),
        ]
        pg = tk.Frame(pt_wrap, bg=_PB)
        pg.pack(fill="x")
        pg.columnconfigure(1, weight=1)
        self._pt_param_lbls: dict = {}
        self._param_active:  dict = {}
        for _idx, (_desc, _pkey, _ref, _on) in enumerate(_PT_PARAM_DESCS):
            _bv = tk.BooleanVar(value=_on)
            self._param_active[_pkey] = _bv
            tk.Checkbutton(pg, variable=_bv, bg=_PB,
                           command=self._on_param_toggle,
                           activebackground=_PB, relief="flat", bd=0,
                           highlightthickness=0).grid(
                               row=_idx, column=0, sticky="w", pady=3)
            _fg = FG2 if _on else FG3
            tk.Label(pg, text=_desc, bg=_PB, fg=_fg,
                     font=("Segoe UI", 8), anchor="w").grid(
                         row=_idx, column=1, sticky="w", pady=3)
            _vlbl = tk.Label(pg, text="—", bg=_PB, fg=FG,
                             font=(MONO[0], 9, "bold"), anchor="e", width=6)
            _vlbl.grid(row=_idx, column=2, sticky="e", padx=(0, 6))
            tk.Label(pg, text=f"{_ref:.2f}", bg=_PB, fg=FG3,
                     font=(MONO[0], 8), anchor="e", width=5).grid(
                         row=_idx, column=3, sticky="e")
            self._pt_param_lbls[_pkey] = _vlbl
        tk.Label(pg, text="† unreliable for video-based tracking",
                 bg=_PB, fg=FG3, font=("Segoe UI", 7, "italic"),
                 anchor="w").grid(row=len(_PT_PARAM_DESCS), column=0,
                                  columnspan=4, sticky="w", pady=(4, 0))

        ttk.Separator(pt_wrap, orient="horizontal").pack(fill="x", pady=(6, 4))

        # Export buttons
        _be = dict(bg=BTN, fg=FG, relief="flat", bd=0, padx=8, pady=3,
                   font=("Segoe UI", 8), cursor="hand2",
                   activebackground=BTN_ACT, activeforeground="#FFFFFF")
        btn_row = tk.Frame(pt_wrap, bg=_PB)
        btn_row.pack(fill="x", pady=(2, 0))
        tk.Button(btn_row, text="Export Graph",
                  command=self._cmd_export_graph,   **_be).pack(side="left", padx=(0, 6))
        tk.Button(btn_row, text="Export Results",
                  command=self._cmd_export_results, **_be).pack(side="left")

        # Collapsible parameter + MAS guide
        self._guide_visible = False
        guide_frame = tk.Frame(pt_wrap, bg=PANEL, padx=6, pady=4)

        _GUIDE_DATA = [
            ("Relaxation index (R₂ₙ)",
             "Fit to an ideal underdamped pendulum. 1.0 = perfect free swing.",
             "≥ 0.85", "< 0.70"),
            ("Swing count (N)",
             "Full oscillation cycles before the leg settles. Spasticity arrests early.",
             "≥ 6 cycles", "1–3 cycles"),
            ("Excursion ratio (φmax)",
             "First return swing as a fraction of the initial drop. Higher = freer swing.",
             "≥ 0.60", "< 0.45"),
            ("Peak angular velocity (ωmax)",
             "Normalized peak swing speed. Lower = velocity-dependent spasticity.",
             "≥ 4.0", "< 2.5"),
            ("Frequency (f)",
             "Oscillation frequency in Hz. Spasticity shortens and stiffens cycles.",
             "~1.0–1.2 Hz", "> 1.3 Hz or irregular"),
            ("Area ratio (AR)",
             "Swing envelope asymmetry. Higher = more erratic trajectory.",
             "< 0.12", "> 0.20"),
            ("PT score interpretation",
             "Composite of 4 Popovic parameters calibrated on n=13 control / n=8 MS "
             "participants (ASU study).\n"
             "0.0 = perfectly healthy swing  ·  scores above 0.20 = strong spastic signal",
             f"Healthy  (PT ≤ {PT_HEALTHY_MAX:.2f})",
             f"Spastic signal  (PT > {PT_BORDERLINE_MAX:.2f})"),
        ]
        _gtxt = tk.Text(guide_frame, height=9, wrap="word",
                        bg=PANEL, fg=FG2, font=("Segoe UI", 8),
                        relief="flat", bd=0, cursor="arrow",
                        state="normal", padx=4, pady=2)
        _gsb  = ttk.Scrollbar(guide_frame, command=_gtxt.yview)
        _gtxt.config(yscrollcommand=_gsb.set)
        _gsb.pack(side="right", fill="y")
        _gtxt.pack(side="left", fill="both", expand=True)
        for _gname, _gdesc, _ghealthy, _gms in _GUIDE_DATA:
            _gtxt.insert("end", _gname + "\n", ("h",))
            _gtxt.insert("end", _gdesc + "\n", ("b",))
            _gtxt.insert("end", f"Healthy: {_ghealthy}\n", ("ok",))
            _gtxt.insert("end", f"MS:      {_gms}\n\n", ("warn",))
        _gtxt.tag_config("h",    font=("Segoe UI", 8, "bold"),   foreground=FG)
        _gtxt.tag_config("b",    font=("Segoe UI", 8),           foreground=FG2)
        _gtxt.tag_config("ok",   font=("Segoe UI", 8, "italic"), foreground="#16A34A")
        _gtxt.tag_config("warn", font=("Segoe UI", 8, "italic"), foreground="#EA580C")
        _gtxt.config(state="disabled")

        def _toggle_guide():
            self._guide_visible = not self._guide_visible
            if self._guide_visible:
                guide_frame.pack(fill="x", pady=(2, 2), before=btn_row)
                toggle_btn.config(text="▾ Parameter Guide")
            else:
                guide_frame.pack_forget()
                toggle_btn.config(text="▸ Parameter Guide")

        toggle_btn = tk.Button(pt_wrap, text="▸ Parameter Guide",
                               command=_toggle_guide,
                               bg=_PB, fg=FG3, relief="flat", bd=0,
                               font=("Segoe UI", 8), cursor="hand2",
                               anchor="w", activebackground=PANEL,
                               activeforeground=FG2)
        toggle_btn.pack(fill="x", pady=(4, 0), before=btn_row)

        # ── bottom graph strip (resizable pane, collapsible) ────────────────
        graph_outer = tk.Frame(paned, bg=BG)
        paned.add(graph_outer, stretch="never", minsize=40, height=270)
        self._graph_outer = graph_outer

        ghdr = tk.Frame(graph_outer, bg=PANEL, pady=5, padx=10)
        ghdr.pack(fill="x")

        self._graph_body = tk.Frame(graph_outer, bg=SURFACE,
                                    highlightthickness=1, highlightbackground=BORDER)
        self._graph_body.pack(fill="both", expand=True)
        self._graph_visible = True

        def _toggle_graph():
            if self._graph_visible:
                self._graph_body.pack_forget()
                _gbtn_var.set("▸  KNEE ANGLE GRAPH")
            else:
                self._graph_body.pack(fill="both", expand=True)
                _gbtn_var.set("▾  KNEE ANGLE GRAPH")
                self.after(60, self._plot_canvas.draw)
            self._graph_visible = not self._graph_visible

        _gbtn_var = tk.StringVar(value="▾  KNEE ANGLE GRAPH")
        tk.Button(ghdr, textvariable=_gbtn_var, command=_toggle_graph,
                  bg=PANEL, fg=FG2, relief="flat", bd=0,
                  font=("Segoe UI", 9, "bold"), anchor="w", cursor="hand2",
                  activebackground=PANEL, activeforeground=FG).pack(
                      side="left", fill="x", expand=True)

        # Release-frame controls (right side of graph header)
        _rb = dict(relief="flat", bd=0, font=("Segoe UI", 8), cursor="hand2",
                   padx=8, pady=2)
        self._release_mode = False
        self._release_lbl  = tk.Label(ghdr, text="", bg=PANEL, fg="#7C3AED",
                                       font=("Segoe UI", 8), anchor="e")
        self._release_lbl.pack(side="right", padx=(0, 4))

        def _clear_release():
            self._manual_release_fi = None
            self._release_mode = False
            _set_rel_btn.config(text="📍 Set Release", bg=BTN, fg=FG)
            self._release_lbl.config(text="")
            self._compute_and_show_pt()

        def _toggle_release_mode():
            self._release_mode = not self._release_mode
            if self._release_mode:
                _set_rel_btn.config(text="● Click graph to place", bg="#7C3AED",
                                    fg="#FFFFFF", activebackground="#5B21B6")
            else:
                _set_rel_btn.config(text="📍 Set Release", bg=BTN, fg=FG,
                                    activebackground=BTN_ACT)

        _set_rel_btn = tk.Button(ghdr, text="📍 Set Release",
                                  command=_toggle_release_mode,
                                  bg=BTN, fg=FG, activebackground=BTN_ACT,
                                  activeforeground="#FFFFFF", **_rb)
        _set_rel_btn.pack(side="right", padx=2)
        tk.Button(ghdr, text="✕ Clear Release", command=_clear_release,
                  bg=BTN, fg=FG3, activebackground=BTN_ACT,
                  activeforeground="#FFFFFF", **_rb).pack(side="right", padx=2)
        self._set_rel_btn = _set_rel_btn

        def _reset_rel_btn():
            self._set_rel_btn.config(text="📍 Set Release", bg=BTN, fg=FG,
                                      activebackground=BTN_ACT,
                                      activeforeground="#FFFFFF")
        self._reset_rel_btn = _reset_rel_btn

        self._fig = Figure(figsize=(8, 2.4), facecolor=SURFACE,
                           tight_layout={"pad": 0.4})
        self._ax  = self._fig.add_subplot(111)
        self._ax.set_facecolor(SURFACE)
        self._ax.tick_params(colors=FG2, labelsize=8)
        for sp in self._ax.spines.values():
            sp.set_color(BORDER)
        self._ax.set_xlabel("Time (s)", color=FG3, fontsize=8)
        self._ax.set_ylabel("Angle (°)", color=FG3, fontsize=8)
        self._ax.set_ylim(0, 185)
        self._ax.autoscale(enable=False, axis="y")
        self._ax.grid(True, color="#E2E8F0", linewidth=0.7)
        self._line_plot, = self._ax.plot([], [], color="#2563EB", linewidth=1.8,
                                          label="Active")
        # One overlay line per methodology, shown together when Overlay is on.
        self._overlay_lines: dict = {}
        for _k, _lbl, _col in self._METHODS:
            _ln, = self._ax.plot([], [], color=_col, linewidth=1.3, alpha=0.85,
                                  label=_lbl, visible=False)
            self._overlay_lines[_k] = _ln
        # Kept as the IMU overlay handle used by the recording flow.
        self._line_imu = self._overlay_lines["imu"]
        self._vline      = self._ax.axvline(0, color="#94A3B8", linewidth=0.8,
                                             alpha=0.6, linestyle="--")

        pc = FigureCanvasTkAgg(self._fig, master=self._graph_body)
        pc.get_tk_widget().pack(fill="both", expand=True)
        pc.draw()
        self._plot_canvas = pc

        # Lock y-axis on resize. tight_layout is automatic (set in Figure constructor)
        # and matplotlib's own <Configure> handler resizes the rendering buffer —
        # we add=True so we don't replace it, just run after it.
        def _on_graph_resize(event):
            if event.width < 80 or event.height < 40:
                return
            self._ax.set_ylim(0, 185)
            self._ax.autoscale(enable=False, axis="y")
            self._plot_canvas.draw_idle()

        pc.get_tk_widget().bind("<Configure>", _on_graph_resize, add=True)

        # Click on graph to set manual release frame (when release mode is active).
        def _on_graph_click(event):
            if not self._release_mode:
                return
            if event.inaxes is not self._ax:
                return
            t_clicked = event.xdata
            if t_clicked is None or self.fps <= 0:
                return
            fi = int(round(t_clicked * self.fps))
            fi = max(0, min(fi, len(self._angles) - 1))
            self._manual_release_fi = fi
            self._release_mode = False
            self._reset_rel_btn()
            t_sec = fi / self.fps
            self._release_lbl.config(
                text=f"📍 Release @ {t_sec:.2f} s  (frame {fi})")
            self._compute_and_show_pt()

        self._fig.canvas.mpl_connect("button_press_event", _on_graph_click)

        # Set initial sash positions once the window is fully rendered.
        def _init_sash():
            total_h = paned.winfo_height()
            if total_h > 400:
                paned.sash_place(0, 0, max(200, total_h - 300))
            total_w = h_paned.winfo_width()
            if total_w > 600:
                h_paned.sash_place(0, max(300, total_w - 440), 0)
            self._plot_canvas.draw()
        self.after(200, _init_sash)

        # ── transport bar ────────────────────────────────────────────────────
        transport = tk.Frame(self, bg=PANEL, pady=7)
        transport.pack(fill="x", side="bottom")

        _b2 = dict(bg=BTN, fg=FG, relief="flat", bd=0, padx=9, pady=4,
                   font=("Segoe UI", 10), cursor="hand2",
                   activebackground=BTN_ACT, activeforeground="#FFFFFF")
        self._btn_go_first = tk.Button(transport, text="⏮", command=self._go_first, **_b2)
        self._btn_go_prev  = tk.Button(transport, text="⏪", command=self._go_prev,  **_b2)
        self._btn_play     = tk.Button(transport, text="▶", command=self._toggle_play, **_b2)
        self._btn_go_next  = tk.Button(transport, text="⏩", command=self._go_next,  **_b2)
        self._btn_go_last  = tk.Button(transport, text="⏭", command=self._go_last,  **_b2)
        for b in (self._btn_go_first, self._btn_go_prev, self._btn_play,
                  self._btn_go_next, self._btn_go_last):
            b.pack(side="left", padx=2)

        self._btn_rec = tk.Button(transport, text="⏺ Record",
                                   command=self._cmd_rec_toggle,
                                   bg="#5C1A1A", fg="#F8FAFC", relief="flat", bd=0,
                                   padx=11, pady=4, font=("Segoe UI", 10, "bold"),
                                   cursor="hand2",
                                   activebackground="#8B0000",
                                   activeforeground="#FFFFFF")

        # ── camera-only controls (countdown + check; hidden until camera live) ─
        self._cam_ctrl = tk.Frame(transport, bg=PANEL)
        _bcd = dict(relief="flat", bd=0, padx=9, pady=4, cursor="hand2",
                    font=("Segoe UI", 9))
        tk.Label(self._cam_ctrl, text="delay:", bg=PANEL, fg=FG3,
                 font=("Segoe UI", 8)).pack(side="left", padx=(4, 2))
        for _secs in (3, 5, 10):
            tk.Button(self._cam_ctrl, text=f"{_secs}s",
                      command=lambda s=_secs: self._start_countdown(s),
                      bg=BTN, fg=FG2, activebackground=BTN_ACT,
                      activeforeground="#FFFFFF", **_bcd).pack(side="left", padx=1)
        ttk.Separator(self._cam_ctrl, orient="vertical").pack(
            side="left", fill="y", padx=8, pady=3)
        tk.Button(self._cam_ctrl, text="⚠ Check Setup",
                  command=self._cmd_check_setup,
                  bg=BTN, fg="#D97706", activebackground=BTN_ACT,
                  activeforeground="#FFFFFF", **_bcd).pack(side="left", padx=2)
        ttk.Separator(self._cam_ctrl, orient="vertical").pack(
            side="left", fill="y", padx=8, pady=3)
        self._var_motive = tk.BooleanVar(value=False)
        tk.Checkbutton(self._cam_ctrl, text="Sync Motive",
                       variable=self._var_motive,
                       bg=PANEL, fg="#60C0FF", activebackground=PANEL,
                       selectcolor=BTN, font=("Segoe UI", 8),
                       cursor="hand2").pack(side="left", padx=2)
        self._motive_active = False   # True while a Motive recording is live

        ttk.Separator(self._cam_ctrl, orient="vertical").pack(
            side="left", fill="y", padx=8, pady=3)
        self._var_goniometer = tk.BooleanVar(value=False)
        tk.Checkbutton(self._cam_ctrl, text="Sync Goniometer",
                       variable=self._var_goniometer,
                       bg=PANEL, fg="#60C0FF", activebackground=PANEL,
                       selectcolor=BTN, font=("Segoe UI", 8),
                       cursor="hand2").pack(side="left", padx=2)
        self._goniometer_lbl = tk.Label(self._cam_ctrl, text="⚪ --°",
                                         bg=PANEL, fg=FG3, font=(MONO[0], 9))
        self._goniometer_lbl.pack(side="left", padx=(4, 2))

        ttk.Separator(self._cam_ctrl, orient="vertical").pack(
            side="left", fill="y", padx=8, pady=3)
        # Recording auto-stop duration. 0 = record until ✓ Done is clicked.
        tk.Label(self._cam_ctrl, text="stop after:", bg=PANEL, fg=FG3,
                 font=("Segoe UI", 8)).pack(side="left", padx=(2, 2))
        ttk.Combobox(self._cam_ctrl, textvariable=self._rec_timer_var,
                     values=["0", "10", "15", "20", "30", "45", "60"],
                     width=3, state="normal",
                     font=("Segoe UI", 8)).pack(side="left")
        tk.Label(self._cam_ctrl, text="s", bg=PANEL, fg=FG3,
                 font=("Segoe UI", 8)).pack(side="left", padx=(1, 2))
        self._rec_timer_lbl = tk.Label(self._cam_ctrl, text="",
                                        bg=PANEL, fg="#DC2626",
                                        font=(MONO[0], 9, "bold"))
        self._rec_timer_lbl.pack(side="left", padx=(2, 2))
        # Hidden until camera mode is active

        # iPhone-style trim timeline: seek by clicking, drag bracket handles to set trim range.
        self._tl = tk.Canvas(transport, height=34, bg=PANEL, highlightthickness=0,
                             cursor="hand2")
        self._tl.pack(side="left", fill="x", expand=True, padx=10)
        self._tl.bind("<ButtonPress-1>",   self._on_tl_press)
        self._tl.bind("<B1-Motion>",       self._on_tl_drag)
        self._tl.bind("<ButtonRelease-1>", self._on_tl_release)
        self._tl.bind("<Configure>",       lambda e: self._tl_draw())

        self._time_lbl = tk.Label(transport, text="0.00 s",
                                   bg=PANEL, fg=FG2, font=(MONO[0], 9))
        self._time_lbl.pack(side="left", padx=4)

        # ── status bar ───────────────────────────────────────────────────────
        self._status = tk.Label(self, text="Open a video to begin.",
                                 bg=BORDER, fg=FG2, anchor="w",
                                 padx=10, font=("Segoe UI", 8))
        self._status.pack(fill="x", side="bottom")

    @staticmethod
    def _stat_card(parent, label, color, bg, mono):
        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label.upper(), bg=bg, fg="#94A3B8",
                 font=("Segoe UI", 9), width=10, anchor="w").pack(side="left")
        lbl = tk.Label(row, text="—", bg=bg, fg=color,
                       font=(mono[0], 16, "bold"), anchor="e")
        lbl.pack(side="right")
        return lbl

    # ── core video helpers ────────────────────────────────────────────────────

    def _open_video(self, path: str):
        if self._phone_mode and _PPS_AVAIL:
            try:
                _pps.stop()
            except Exception:
                pass
            self._phone_mode = False
        # Always leave live mode when opening a recorded file — otherwise
        # _try_init_tracker picks self._last_live_frame (None for upload flow)
        # and tracker.init() silently aborts, leaving tracker.ready = False.
        self._live = False
        self._last_live_frame = None
        if self.cap:
            self.cap.release()
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            messagebox.showerror("Error", f"Cannot open:\n{path}")
            return
        self.video_path    = path
        self.fps           = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.total_frames  = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.tracker._ms_per_frame = max(1, int(round(1000.0 / self.fps)))
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._vid_w = w
        self._vid_h = h
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        self._scale = min((cw if cw > 10 else DISPLAY_W) / w,
                          (ch if ch > 10 else DISPLAY_H) / h)
        self._canvas.delete("hint")
        self._playing      = False
        self._frame_idx    = 0
        self._angles       = [float("nan")] * self.total_frames
        self._trail        = []
        self._path_keyframes = {}
        self._trim_start   = 0
        self._trim_end     = self.total_frames
        self._release_fi   = None
        if hasattr(self, '_trim_spans'):
            self._update_trim_spans()
        self._btn_play.config(text="▶")
        self._manual_release_fi = None
        self._release_fi        = None
        self._release_mode = False
        self._reset_rel_btn()
        self._release_lbl.config(text="")
        # Clear graph and stat panel
        self._line_plot.set_data([], [])
        self._plot_canvas.draw_idle()
        for lbl in (self._stat_cur, self._stat_min, self._stat_rest, self._stat_amp):
            lbl.config(text="—")
        self._clear_pt_panel()
        self.title(f"Pendulastic Viewer — {os.path.basename(path)}")
        self._vid_lbl.config(text=f"{os.path.basename(path)}  {w}×{h}  {self.fps:.0f}fps")

        # Try to load saved selections
        sel = _load_sel(path)
        if sel.get("knee_x_norm") and sel.get("ankle_x_norm"):
            hip_xn   = sel.get("hip_x_norm",   sel.get("click_x_norm", 0))
            hip_yn   = sel.get("hip_y_norm",    sel.get("click_y_norm", 0))
            hip   = (hip_xn   * w, hip_yn   * h)
            knee  = (sel["knee_x_norm"]  * w, sel["knee_y_norm"]  * h)
            ankle = (sel["ankle_x_norm"] * w, sel["ankle_y_norm"] * h)
            self._hip_click, self._knee_click, self._ankle_click = hip, knee, ankle
            frame = self._get_frame(0)
            if frame is not None:
                self.tracker.init(frame, hip, knee, ankle)
            self._status.config(text=f"Loaded saved markers. thigh={self.tracker.thigh_len:.0f}px  shank={self.tracker.shank_len:.0f}px  →  Press ▶ or Track All.")
        else:
            self._status.config(text=f"Loaded {os.path.basename(path)}. Click 'Detect Patient' or set Knee + Ankle manually.")

        self._show_frame_idx(0)

    def _get_frame(self, fi: int) -> Optional[np.ndarray]:
        if self.cap is None:
            return None
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = self.cap.read()
        return frame if ok else None

    def _ankle_from_angle(self, ang_deg: float) -> Optional[np.ndarray]:
        """
        Reconstruct the ankle position in video coords from a stored knee angle.

        The tracker's hip0/knee0 are fixed, and ankle0 tells us which side of
        the thigh the shank is on.  We rotate the thigh unit-vector by ang_deg
        in that direction and scale by shank_len.
        """
        if not self.tracker.ready or not math.isfinite(ang_deg):
            return None
        kne = self.tracker.knee0
        hip = self.tracker.hip0
        sl  = self.tracker.shank_len
        if sl < 1 or hip is None:
            return None
        th   = hip - kne
        th_n = float(np.linalg.norm(th))
        if th_n < 1e-6:
            return None
        th_u = th / th_n                           # unit vector knee → hip

        ank0   = self.tracker.ankle0
        av     = ank0 - kne
        av_n   = float(np.linalg.norm(av))
        if av_n < 1e-6:
            return None
        av_u = av / av_n

        # 2-D cross product determines which side of the thigh the ankle is on
        cross    = float(th_u[0] * av_u[1] - th_u[1] * av_u[0])
        rot_sign = 1.0 if cross >= 0 else -1.0

        alpha  = rot_sign * math.radians(ang_deg)
        ca, sa = math.cos(alpha), math.sin(alpha)
        su     = np.array([ca * th_u[0] - sa * th_u[1],
                           sa * th_u[0] + ca * th_u[1]])
        return (kne + su * sl).astype(np.float32)

    def _show_frame_idx(self, fi: int):
        frame = self._get_frame(fi)
        if frame is None:
            return
        ang = self._angles[fi] if fi < len(self._angles) else float("nan")

        if self.tracker.ready:
            hip = self.tracker.hip0
            kne = self.tracker.knee0
            if fi in self._ankle_pins:
                # Show the exact position the user pinned — not the arc-reconstructed
                # position — so the marker stays exactly where they clicked.
                ank = np.array(self._ankle_pins[fi], dtype=np.float32)
            elif math.isfinite(ang):
                # Reconstruct ankle from the stored (batch-tracked) angle so that
                # scrubbing shows the correct position, not the LK tracker's stale
                # estimate which never updates during non-live scrubbing.
                ank = self._ankle_from_angle(ang)
            else:
                ank = self.tracker._ank   # pre-track fallback
        else:
            hip = np.array(self._hip_click)  if self._hip_click  else None
            kne = np.array(self._knee_click) if self._knee_click else None
            ank = np.array(self._ankle_click) if self._ankle_click else None
            if kne is not None and ank is not None:
                if hip is None:
                    hip = self._auto_hip(kne, ank)
                ang = _angle_deg(hip, kne, ank)

        # During the guided skeleton wizard, suppress the existing skeleton and
        # trail so the user sees only the frame and the guide point overlay.
        if self._skel_guide_active:
            overlay = _draw(frame, None, None, None, float("nan"), [], self._scale)
        else:
            # Windowed trail: pass at most TRAIL_LEN recent positions so the full
            # frame-indexed _trail (length = total_frames) doesn't flood the renderer.
            if len(self._trail) == self.total_frames:
                _trail_vis = [p for p in self._trail[max(0, fi - TRAIL_LEN): fi + 1]
                              if p is not None]
            else:
                _trail_vis = self._trail
            overlay = _draw(frame, hip, kne, ank, ang, _trail_vis, self._scale)
        overlay = self._overlay_path(overlay)
        if self._person_select_active and self._person_select_poses:
            overlay = self._draw_person_select_overlay(overlay)
        if self._skel_guide_active:
            overlay = self._overlay_skel_guide(overlay)
        self._push_frame(overlay)
        t = fi / self.fps
        self._time_lbl.config(text=f"{t:.2f} s / {self.total_frames/self.fps:.2f} s")
        self._tl_draw()
        self._update_stats(ang, fi)

    def _apply_face_blur(self, bgr: np.ndarray) -> np.ndarray:
        """Detect faces and apply Gaussian blur for patient privacy."""
        if self._face_casc is None:
            self._face_casc = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            self._prof_casc = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_profileface.xml")
        out = bgr.copy()
        h, w = bgr.shape[:2]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        cv2.equalizeHist(gray, gray)
        min_face = max(24, int(h * 0.07))
        all_faces = []
        for casc in (self._face_casc, self._prof_casc):
            if casc and not casc.empty():
                det = casc.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=4,
                    minSize=(min_face, min_face))
                if len(det):
                    all_faces.extend(det.tolist())
        for (x, y, fw, fh) in all_faces:
            pad = int(fh * 0.25)
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(w, x + fw + pad), min(h, y + fh + pad)
            roi = out[y1:y2, x1:x2]
            if roi.size == 0:
                continue
            k = max(21, int(max(fw, fh) * 0.9) | 1)  # must be odd
            out[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)
        return out

    def _push_frame(self, bgr: np.ndarray):
        if self._blur_faces.get():
            bgr = self._apply_face_blur(bgr)
        rgb  = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        img  = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(img)
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        ih, iw = bgr.shape[:2]
        self._img_ox = max(0, (cw - iw) // 2)
        self._img_oy = max(0, (ch - ih) // 2)
        self._canvas.delete("all")
        self._canvas.create_image(self._img_ox, self._img_oy,
                                  anchor="nw", image=self._photo)

    def _on_canvas_resize(self, event: tk.Event):
        # Keep startup hint centered
        self._canvas.coords("hint", event.width // 2, event.height // 2)
        if self._vid_w == 0:
            return
        # Debounce — reschedule 60 ms after the last resize event
        if hasattr(self, "_resize_job"):
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(
            60, lambda w=event.width, h=event.height: self._apply_resize(w, h))

    def _apply_resize(self, cw: int, ch: int):
        if self._vid_w == 0:
            return
        new_scale = min(cw / self._vid_w, ch / self._vid_h)
        if abs(new_scale - self._scale) < 0.005:
            return
        self._scale = new_scale
        # Don't interrupt active playback — _tick uses self._scale on next frame
        if self.cap is not None and not self._playing:
            self._show_frame_idx(self._frame_idx)

    def _on_param_toggle(self):
        """Called when any parameter checkbox changes — recompute PT score."""
        if self._last_pt_params is not None:
            self._compute_and_show_pt()

    def _custom_pt_score(self, params: dict) -> float:
        """Compute PT score using only the user-selected parameters."""
        _DENOM_FLOOR = 0.1
        active = [k for k, v in self._param_active.items() if v.get()]
        if not active:
            return 0.0
        n = len(active)
        total = 0.0
        for k in active:
            pij = params.get(k, float("nan"))
            phj = HEALTHY_REF.get(k, float("nan")) if _PT_AVAIL else float("nan")
            if not (math.isfinite(pij) and math.isfinite(phj) and phj > 0):
                continue
            delta = pij - phj
            denom = n * max(phj, _DENOM_FLOOR)
            if k in ("N", "R2n", "phi_max_ratio", "omega_max_n"):
                dev = max(0.0, -delta) / denom
            elif k == "area_ratio":
                dev = max(0.0,  delta) / denom
            else:  # f — bidirectional; skip if uncomputable
                if pij < 0.1 or params.get("N", 0) < 2.0:
                    dev = 0.0
                else:
                    dev = abs(delta) / denom
            total += dev
        return total

    @staticmethod
    def _auto_hip(knee: np.ndarray, ankle: np.ndarray) -> np.ndarray:
        """Estimate hip opposite to ankle direction from knee."""
        sv = ankle - knee
        sn = np.linalg.norm(sv)
        if sn < 1:
            return knee + np.array([0, -80], dtype=np.float32)
        return knee - sv / sn * sn   # hip opposite to ankle at shank distance

    # ── playback tick ─────────────────────────────────────────────────────────

    def _tick(self):
        # ── Live iPhone IMU goniometer readout ─────────────────────────────────
        _imu_ang: Optional[float] = None
        if _IMU_AVAIL and hasattr(self, "_imu_flex_lbl"):
            st  = _imu.get_state()
            ang = st["angles"]
            for lbl, key, role in (
                    (self._imu_flex_lbl, "pitch", None),
                    (self._imu_abd_lbl,  "roll",  None),
                    (self._imu_rot_lbl,  "yaw",   None)):
                v = ang.get(key, float("nan"))
                lbl.config(text=f"{v:+.1f}°" if math.isfinite(v) else "—")

            _single = self._imu_single_mode()
            _note = ("Single-IMU mode: knee angle = shank orientation. Valid "
                     "only while the thigh stays still — confirm with Overlay "
                     "against the RGB trace." if _single else "")
            if self._imu_mode_note.cget("text") != _note:
                self._imu_mode_note.config(text=_note)
            if _single:
                _prox_name, _dist_name = ("Shank", "")
            elif "Torso" in self._imu_segpair_var.get():
                _prox_name, _dist_name = ("Torso", "Thigh")
            else:
                _prox_name, _dist_name = ("Thigh", "Shank")
            _err = st.get("bind_error")
            for lbl, info, name in ((self._imu_prox_lbl, st["proximal"], _prox_name),
                                    (self._imu_dist_lbl, st["distal"],   _dist_name)):
                if _err:
                    lbl.config(text=f"⚠  {name} — server offline", fg="#DC2626")
                elif info["connected"]:
                    _hz = info.get("hz", 0.0)
                    if _hz <= 0:
                        # Streaming, but no gyro yet — AHRS cannot run on
                        # accelerometer alone.
                        lbl.config(text=f"◐  {name} — {info['ip']}  (no gyro)",
                                   fg="#D97706")
                    elif _hz < _imu.MIN_USABLE_HZ:
                        lbl.config(
                            text=f"⚠  {name} — {info['ip']}  {_hz:.0f} Hz (too slow)",
                            fg="#D97706")
                    else:
                        lbl.config(
                            text=f"●  {name} — {info['ip']}  {_hz:.0f} Hz",
                            fg="#16A34A")
                elif not name:
                    # Single-IMU mode: the second row is deliberately unused.
                    lbl.config(text="–  thigh assumed stationary (not measured)",
                               fg="#94A3B8")
                else:
                    lbl.config(text=f"○  {name} — waiting…", fg="#94A3B8")
            if _err:
                _msg, _col = _err, "#DC2626"
            else:
                # No bind problem: show live socket count and the last
                # unexpected drop, so a mid-session failure is visible.
                _drop = st.get("last_drop")
                _n    = st.get("conns", 0)
                _eps  = st.get("endpoints", {})
                _bad  = {p: v for p, v in _eps.items() if v["status"] != "ok"}
                _slow = [d for d in (st["proximal"], st["distal"])
                         if d["connected"] and 0 < d.get("hz", 0) < _imu.MIN_USABLE_HZ]
                if _slow:
                    _msg = (f"⚠ gyro only {min(d['hz'] for d in _slow):.0f} Hz — "
                            f"set the app's update interval to 10 ms "
                            f"(≥{_imu.MIN_USABLE_HZ:.0f} Hz needed for AHRS)")
                    _col = "#D97706"
                elif _bad and not any(v["status"] == "ok" for v in _eps.values()):
                    # Data is arriving but nothing parses — the usual cause is
                    # an app build whose payload schema differs from ours.
                    _p, _v = next(iter(_bad.items()))
                    _msg = (f"⚠ receiving on {_p} but {_v['status']} — "
                            f"see console for a sample payload")
                    _col = "#DC2626"
                elif _drop:
                    _ago = time.time() - _drop["t"]
                    _msg = (f"{_n} socket{'s' if _n != 1 else ''} open · last "
                            f"drop {_ago:.0f}s ago on {_drop['path']} "
                            f"({_drop['reason']})")
                    _col = "#D97706" if _ago < 60 else "#94A3B8"
                elif _n:
                    _msg, _col = (f"{_n} socket{'s' if _n != 1 else ''} open",
                                  "#16A34A")
                else:
                    _msg, _col = "", "#94A3B8"
            if self._imu_srv_lbl.cget("text") != _msg:
                self._imu_srv_lbl.config(text=_msg, fg=_col)

            self._update_sync_ui(st["sync"])

            # Refresh the advertised address if the network changed (e.g. the
            # hotspot came up after the viewer launched). Enumeration opens
            # sockets and resolves the hostname, so it is throttled rather
            # than run on every frame.
            _now = time.time()
            if _now >= self._imu_addr_next_scan:
                self._imu_addr_next_scan = _now + 5.0
                _ips  = _imu.get_all_local_ips()
                _addr = f"{_ips[0]}:{_imu.PORT}"
                if _addr != self._imu_addr_var.get():
                    self._imu_addr_var.set(_addr)
                _alt_txt = ("also reachable at: " + ", ".join(_ips[1:])
                            if len(_ips) > 1 else "")
                if _alt_txt != self._imu_alt_lbl.cget("text"):
                    self._imu_alt_lbl.config(text=_alt_txt)

            v = self._imu_axis_value(ang)
            if math.isfinite(v):
                _imu_ang = v

        # ── Live goniometer readout (IMU preferred, UDP as fallback) ───────────
        if hasattr(self, "_goniometer_lbl"):
            if _imu_ang is not None:
                dot = "🔴" if (self._goniometer_recording or self._imu_recording) else "🟢"
                self._goniometer_lbl.config(text=f"{dot} {_imu_ang:.1f}°", fg="#0F172A")
            else:
                with self._goniometer_lock:
                    g_ang, g_last = self._goniometer_angle, self._goniometer_last_rx
                if g_last == 0.0 or (time.time() - g_last) > 2.0:
                    self._goniometer_lbl.config(text="⚪ --°", fg="#94A3B8")
                else:
                    dot = "🔴" if self._goniometer_recording else "🟢"
                    self._goniometer_lbl.config(text=f"{dot} {g_ang:.1f}°", fg="#0F172A")

        # ── Recording countdown readout ────────────────────────────────────────
        if self._recording and hasattr(self, "_rec_timer_lbl"):
            _dur = self._rec_timer_seconds()
            if _dur > 0:
                _left = max(0.0, _dur - (time.time() - self._rec_started_at))
                self._rec_timer_lbl.config(text=f"⏱ {_left:4.1f}s")
            else:
                self._rec_timer_lbl.config(
                    text=f"⏱ {time.time() - self._rec_started_at:5.1f}s")

        # ── Check for uploaded videos ──────────────────────────────────────────
        # Path 1: in-process queue (server embedded in viewer — fastest)
        _auto_open_path: str | None = None
        if _PPS_AVAIL and not self._recording:
            try:
                _auto_open_path = _pps.upload_queue.get_nowait()
            except queue.Empty:
                pass

        # Path 2: uploads/ directory scan — catches files written by a standalone
        # server process (separate terminal), runs every ~2 s.
        if _auto_open_path is None and not self._recording:
            _now = time.time()
            if _now >= self._uploads_next_scan:
                self._uploads_next_scan = _now + 2.0
                _udir = os.path.join(BASE_DIR, "uploads")
                if os.path.isdir(_udir):
                    for _fn in sorted(os.listdir(_udir)):
                        if not _fn.lower().endswith(
                                (".mp4", ".mov", ".avi", ".m4v", ".mkv")):
                            continue
                        _fp = os.path.join(_udir, _fn)
                        if _fp in self._uploads_seen:
                            continue
                        self._uploads_seen.add(_fp)
                        try:
                            _mt = os.path.getmtime(_fp)
                        except OSError:
                            continue
                        if _mt >= self._viewer_start_time:
                            _auto_open_path = _fp
                            break  # open one at a time; next scan handles any others

        if _auto_open_path is not None:
            try:
                if getattr(self, "_phone_qr_dlg", None):
                    self._phone_qr_dlg.destroy()
                    self._phone_qr_dlg = None
            except Exception:
                pass
            self._uploads_seen.add(_auto_open_path)
            self._status.config(
                text=f"Received from phone: {os.path.basename(_auto_open_path)}"
                     "  —  place markers then Track All.")
            self.after(max(1, int(1000 / FPS_CAP)), self._tick)  # keep loop alive
            self._open_video(_auto_open_path)
            # _open_video may have called _pps.stop() (when _phone_mode was True).
            # Restart the server so the phone can continue to upload more videos.
            if _PPS_AVAIL:
                try:
                    _pps.start(upload_dir=os.path.join(BASE_DIR, "uploads"))
                except Exception:
                    pass
            return

        if self._live and (self.cap is not None or self._phone_mode):
            # ── Frame acquisition: USB camera or phone WebSocket stream ──────
            ok    = False
            frame = None
            if self._phone_mode and _PPS_AVAIL:
                # ── Real-time WebSocket frame (future / Android) ──────────────
                try:
                    frame = _pps.frame_queue.get_nowait()
                    ok    = True
                    # Update canvas scale and dims when first phone frame arrives
                    # (or if the phone changes resolution mid-session).
                    h, w = frame.shape[:2]
                    if w != self._cam_w or h != self._cam_h:
                        self._cam_w, self._cam_h = w, h
                        self._vid_w, self._vid_h = w, h
                        cw = self._canvas.winfo_width()
                        ch = self._canvas.winfo_height()
                        self._scale = min(
                            (cw if cw > 10 else DISPLAY_W) / w,
                            (ch if ch > 10 else DISPLAY_H) / h)
                        self._vid_lbl.config(
                            text=f"Phone Camera  {w}×{h}  {self.fps:.0f} fps")
                    # Update QR dialog status label on first frame
                    if hasattr(self, "_phone_status_lbl"):
                        try:
                            self._phone_status_lbl.config(
                                text="📱 Phone connected!", fg="#4ADE80")
                        except Exception:
                            pass
                        try:
                            del self._phone_status_lbl
                        except Exception:
                            pass
                except queue.Empty:
                    pass
            elif self.cap is not None:
                ok, frame = self.cap.read()

            if ok and frame is not None:
                self._last_live_frame = frame

                # Always step tracker (keeps _prev_gray in sync even in preview)
                if self.tracker.ready:
                    hip, kne, ank, ang = self.tracker.step(frame)
                else:
                    hip = kne = ank = None
                    ang = float("nan")

                if self._recording:
                    if self._writer is not None:
                        self._writer.write(frame)
                    self._live_fi += 1
                    self._angles.append(ang)
                    if ank is not None:
                        self._trail.append(ank.copy())
                        if len(self._trail) > TRAIL_LEN:
                            self._trail.pop(0)
                    t = self._live_fi / self.fps
                    self._time_lbl.config(text=f"● {t:.1f} s  REC")
                    fi = len(self._angles) - 1
                    self._update_stats(ang, fi)
                    self._update_plot_live()
                else:
                    # Preview mode: show live angle in Current card without accumulating
                    if math.isfinite(ang):
                        self._stat_cur.config(text=f"{ang:.1f}°")
                    self._time_lbl.config(text="⬤ LIVE")

                # Show angle overlay whenever tracker is ready (preview + recording)
                show_ang = ang if self.tracker.ready else float("nan")
                overlay = _draw(frame, hip, kne, ank,
                                show_ang,
                                self._trail if self._recording else [],
                                self._scale)
                overlay = self._overlay_path(overlay)
                # Thigh recline angle overlay (live only)
                overlay = self._overlay_recline(overlay, hip, kne)
                # Position readiness check (live preview only, not during recording)
                if not self._recording:
                    overlay = self._overlay_position_check(overlay, hip, kne, ank, show_ang)
                # Countdown overlay
                if self._countdown_remaining > 0:
                    n = self._countdown_remaining
                    oh, ow = overlay.shape[:2]
                    cx, cy = ow // 2, oh // 2
                    cv2.circle(overlay, (cx, cy), 90, (0, 0, 0), -1)
                    cv2.circle(overlay, (cx, cy), 90, (30, 144, 255), 4)
                    cv2.putText(overlay, str(n),
                                (cx - (38 if n >= 10 else 26), cy + 28),
                                cv2.FONT_HERSHEY_DUPLEX, 2.8,
                                (255, 255, 255), 4, cv2.LINE_AA)
                    cv2.putText(overlay, "STARTING IN",
                                (cx - 70, cy - 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (200, 200, 200), 1, cv2.LINE_AA)
                self._push_frame(overlay)

        elif self._playing and self.cap is not None and self.tracker.ready:
            fi = self._frame_idx
            if fi >= self.total_frames or fi >= self._trim_end_eff():
                self._playing = False
                self._btn_play.config(text="▶")
            else:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
                ok, frame = self.cap.read()
                if ok:
                    stored = self._angles[fi] if fi < len(self._angles) else float("nan")
                    hip = self.tracker.hip0
                    kne = self.tracker.knee0
                    if math.isfinite(stored):
                        # Honour user-pinned positions exactly — they must not be
                        # overwritten by arc reconstruction from a (possibly smoothed)
                        # stored angle.
                        if fi in self._ankle_pins:
                            ank = np.array(self._ankle_pins[fi], dtype=np.float32)
                            ang = _angle_deg(hip, kne, ank)
                        else:
                            ang = stored
                            ank = self._ankle_from_angle(ang)
                    else:
                        # No batch-tracked data yet — run the LK tracker.
                        hip, kne, ank, ang = self.tracker.step(frame)
                        self._angles[fi] = ang
                    # Keep the frame-indexed trail up-to-date without converting it
                    # to a ring buffer (which would break frame-index lookups).
                    _trail_is_indexed = (len(self._trail) == self.total_frames)
                    if ank is not None:
                        if _trail_is_indexed:
                            self._trail[fi] = ank.copy()
                        else:
                            # Live / pre-batch ring buffer — keep old behaviour
                            self._trail.append(ank.copy())
                            if len(self._trail) > TRAIL_LEN:
                                self._trail.pop(0)
                    # Pass only the last TRAIL_LEN ankle positions to the renderer
                    if _trail_is_indexed:
                        _trail_vis = [p for p in self._trail[max(0, fi - TRAIL_LEN): fi + 1]
                                      if p is not None]
                    else:
                        _trail_vis = self._trail
                    overlay = _draw(frame, hip, kne, ank, ang, _trail_vis, self._scale)
                    overlay = self._overlay_path(overlay)
                    self._push_frame(overlay)
                    t = fi / self.fps
                    self._time_lbl.config(text=f"{t:.2f} s / {self.total_frames/self.fps:.2f} s")
                    self._tl_draw()
                    self._update_stats(ang, fi)
                    self._update_plot(fi)
                    self._frame_idx += 1

        self.after(max(1, int(1000 / FPS_CAP)), self._tick)

    # ── recline angle overlay ─────────────────────────────────────────────────

    def _overlay_recline(self, frame: np.ndarray, hip, kne) -> np.ndarray:
        """
        Draw a thigh-recline indicator during live camera preview.

        The Wartenberg PT requires the patient to sit with the thigh close to
        horizontal (ideally within ~30° of horizontal). We measure the angle of
        the knee→hip vector above horizontal and warn if it's out of range.

        In image space Y increases downward, so an upward-pointing vector has a
        negative dy component.  We convert to "degrees above horizontal" as:
            angle = -atan2(dy, dx)  (negate dy to get world-up positive)
        """
        if hip is None or kne is None:
            return frame
        try:
            hx, hy = float(hip[0]), float(hip[1])
            kx, ky = float(kne[0]), float(kne[1])
        except Exception:
            return frame

        dx = hx - kx
        dy = hy - ky
        if abs(dx) < 1 and abs(dy) < 1:
            return frame

        # Angle above horizontal (positive = hip above knee in world space)
        thigh_angle = math.degrees(math.atan2(-dy, dx))
        # Canonicalise so we always report the acute deviation from horizontal
        # regardless of which side the hip is on.
        disp_angle = abs(thigh_angle)
        if disp_angle > 90:
            disp_angle = 180 - disp_angle

        ok = disp_angle <= 30.0
        color_bgr = (50, 220, 80) if ok else (30, 120, 255)   # green / orange

        # Scale font to frame width
        fh, fw = frame.shape[:2]
        fscale = max(0.4, fw / 1280)
        thick  = max(1, round(fscale * 1.5))

        label  = f"Thigh: {disp_angle:.0f}deg"
        status = "OK" if ok else "ADJUST - keep thigh near horizontal"
        line1  = f"{label}  [{status}]"

        # Draw at bottom-left with a dark background band for readability
        tx, ty = 10, fh - 30
        (tw, th), _ = cv2.getTextSize(line1, cv2.FONT_HERSHEY_SIMPLEX, fscale, thick)
        cv2.rectangle(frame, (tx - 4, ty - th - 6), (tx + tw + 4, ty + 6),
                      (0, 0, 0), -1)
        cv2.putText(frame, line1, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                    fscale, color_bgr, thick, cv2.LINE_AA)
        return frame

    # ── position check overlay ────────────────────────────────────────────────

    def _overlay_position_check(self, frame: np.ndarray, hip, kne, ank,
                                 ang: float) -> np.ndarray:
        """Draw a position-readiness banner during live preview (before recording).

        Runs only when the tracker is ready so markers are confirmed placed.
        Checks:
          - Shank length >= 60 px  (markers not jumbled together)
          - Ankle at or below knee in image space (y increases downward)
          - Hip-knee-ankle angle in [-30°, 120°] (covers all valid test setups)
          - Angle is finite (tracker hasn't lost the leg)
        Shows green "Ready" when all pass, orange with the first failing check otherwise.
        """
        if not self.tracker.ready:
            return frame
        if kne is None or ank is None:
            return frame

        warnings: list[str] = []

        # Check 1: shank long enough to be valid
        sl = float(getattr(self.tracker, "shank_len", 0))
        if sl < 60:
            warnings.append(f"Shank too short ({sl:.0f}px) — recheck knee/ankle markers")

        # Check 2: ankle should appear below knee (higher y = lower in image)
        kny = float(kne[1])
        any_ = float(ank[1])
        if any_ < kny - 30:   # ankle more than 30px above knee in the image
            warnings.append("Ankle appears above knee — flip camera or recheck markers")

        # Check 3: angle in expected range for the pendulum test
        if math.isfinite(ang):
            if ang < -30 or ang > 120:
                warnings.append(f"Leg angle ({ang:.0f}deg) is outside expected range")
        else:
            warnings.append("Cannot measure angle — reposition or replace markers")

        ok = len(warnings) == 0
        color_bgr = (50, 200, 70) if ok else (30, 120, 255)   # green / orange
        label = "Position OK — ready to record" if ok else warnings[0]

        fh, fw = frame.shape[:2]
        fscale = max(0.4, fw / 1280)
        thick  = max(1, round(fscale * 1.5))

        # Draw at top-left (recline indicator is at bottom-left)
        tx, ty = 10, 30
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fscale, thick)
        cv2.rectangle(frame, (tx - 4, ty - th - 6), (tx + tw + 6, ty + 6),
                      (0, 0, 0), -1)
        cv2.putText(frame, label, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                    fscale, color_bgr, thick, cv2.LINE_AA)

        # ── Visibility bars (MediaPipe-backed tracker only) ───────────────────
        if hasattr(self.tracker, "last_kne_vis"):
            def _vc(v):
                if v >= 0.70: return (50, 220, 80)    # green
                if v >= 0.50: return (30, 200, 255)   # yellow
                return (50, 60, 220)                   # red

            vis_y  = ty + th + 18
            sscale = max(0.35, fscale * 0.78)
            x_pos  = tx
            last_bh = th
            for name, vis in [("Hip", self.tracker.last_hip_vis),
                               ("Kne", self.tracker.last_kne_vis),
                               ("Ank", self.tracker.last_ank_vis)]:
                tag = f"{name}:{vis:.2f}"
                col = _vc(vis)
                (bw, bh), _ = cv2.getTextSize(
                    tag, cv2.FONT_HERSHEY_SIMPLEX, sscale, 1)
                last_bh = bh
                cv2.rectangle(frame,
                              (x_pos - 2, vis_y - bh - 3),
                              (x_pos + bw + 3, vis_y + 3), (0, 0, 0), -1)
                cv2.putText(frame, tag, (x_pos, vis_y),
                            cv2.FONT_HERSHEY_SIMPLEX, sscale, col, 1,
                            cv2.LINE_AA)
                x_pos += bw + 14

            cov     = self.tracker.coverage_pct
            cov_col = ((50, 220, 80) if cov >= 0.75
                       else (30, 200, 255) if cov >= 0.50
                       else (50, 60, 220))
            cov_tag = f"| Cov:{cov*100:.0f}%"
            (cw, ch), _ = cv2.getTextSize(
                cov_tag, cv2.FONT_HERSHEY_SIMPLEX, sscale, 1)
            cv2.rectangle(frame,
                          (x_pos - 2, vis_y - ch - 3),
                          (x_pos + cw + 3, vis_y + 3), (0, 0, 0), -1)
            cv2.putText(frame, cov_tag, (x_pos, vis_y),
                        cv2.FONT_HERSHEY_SIMPLEX, sscale, cov_col, 1,
                        cv2.LINE_AA)

            if self.tracker.assessor_warning:
                warn_y = vis_y + last_bh + 14
                wtag   = "! Assessor may be tracked -- recheck camera aim"
                (ww, wh), _ = cv2.getTextSize(
                    wtag, cv2.FONT_HERSHEY_SIMPLEX, sscale, 1)
                cv2.rectangle(frame,
                              (tx - 4, warn_y - wh - 3),
                              (tx + ww + 4, warn_y + 3), (20, 0, 80), -1)
                cv2.putText(frame, wtag, (tx, warn_y),
                            cv2.FONT_HERSHEY_SIMPLEX, sscale,
                            (100, 80, 255), 1, cv2.LINE_AA)

        return frame

    # ── person-select overlay ─────────────────────────────────────────────────

    def _draw_person_select_overlay(self, frame: np.ndarray) -> np.ndarray:
        return draw_person_select_overlay(frame, self._person_select_poses)

    # ── path draw ─────────────────────────────────────────────────────────────

    def _overlay_path(self, frame: np.ndarray) -> np.ndarray:
        """Draw ankle keyframes and straight-line segments between them."""
        if not self._path_keyframes:
            return frame
        out = frame.copy()
        sc  = self._scale
        kfs = sorted(self._path_keyframes.items())  # [(fi, (vx,vy)), ...]

        # Straight segments between consecutive keyframes (no overshoot)
        if len(kfs) >= 2:
            for i in range(len(kfs) - 1):
                p1 = (int(kfs[i][1][0]   * sc), int(kfs[i][1][1]   * sc))
                p2 = (int(kfs[i+1][1][0] * sc), int(kfs[i+1][1][1] * sc))
                cv2.line(out, p1, p2, (0, 180, 255), 1, cv2.LINE_AA)

        # Keyframe dots — current frame larger + labelled
        for fi, (vx, vy) in kfs:
            px, py  = int(vx * sc), int(vy * sc)
            is_here = (fi == self._frame_idx)
            r = 8 if is_here else 4
            cv2.circle(out, (px, py), r + 1, (0, 0, 0),      1,  cv2.LINE_AA)
            cv2.circle(out, (px, py), r,     (0, 180, 255), -1,  cv2.LINE_AA)
            if is_here:
                cv2.putText(out, f"#{fi}", (px + 9, py - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 180, 255),
                            1, cv2.LINE_AA)

        return out

    def _cmd_path_mode(self):
        if self._live:
            self._status.config(text="Path drawing is not available in live mode.")
            return
        if self.cap is None:
            self._status.config(text="Open a video first.")
            return
        if self._mode == "path":
            self._mode = "idle"
            self._hl_btn(None)
            n = len(self._path_keyframes)
            self._status.config(
                text=f"Path mode off.  {n} point{'s' if n!=1 else ''} marked."
                     + ("  Press ✓ Apply." if n >= 2 else ""))
        else:
            self._mode = "path"
            self._hl_btn(self._btn_path)
            n = len(self._path_keyframes)
            self._status.config(
                text="Path mode ON — click the ankle, use ← → to step frames.  "
                     + (f"{n} point{'s' if n!=1 else ''} already marked." if n else
                        "Mark ≥2 points, then ✓ Apply."))

    def _project_arc(self, vx: float, vy: float) -> tuple:
        """Snap (vx,vy) onto the shank-length circle centred at the knee."""
        if self._knee_click is None:
            return (vx, vy)
        sl = (self.tracker.shank_len if self.tracker.ready and
              getattr(self.tracker, "shank_len", 0) > 0 else None)
        if sl is None and self._ankle_click:
            kne = np.array(self._knee_click)
            ank = np.array(self._ankle_click)
            sl  = float(np.linalg.norm(ank - kne))
        if sl is None or sl < 1:
            return (vx, vy)
        kne = np.array(self._knee_click, dtype=float)
        v   = np.array([vx, vy]) - kne
        d   = float(np.linalg.norm(v))
        if d < 1e-6:
            return (vx, vy)
        proj = kne + v / d * sl
        return (float(proj[0]), float(proj[1]))

    def _cmd_apply_path(self):
        kfs = sorted(self._path_keyframes.items())
        if len(kfs) < 2:
            return
        if self._knee_click is None:
            messagebox.showwarning("No Knee marker", "Set the Knee marker first.")
            return

        fis = np.array([k[0] for k in kfs], dtype=float)
        pxs = np.array([k[1][0] for k in kfs])
        pys = np.array([k[1][1] for k in kfs])

        kne = np.array(self._knee_click, dtype=float)
        hip = (np.array(self._hip_click, dtype=float)
               if self._hip_click else None)

        sl = (self.tracker.shank_len if self.tracker.ready and
              getattr(self.tracker, "shank_len", 0) > 0 else None)
        if sl is None and self._ankle_click:
            sl = float(np.linalg.norm(np.array(self._ankle_click) - kne))

        fi_lo   = int(fis[0])
        fi_hi   = int(fis[-1])
        n_total = len(self._angles)
        all_fi  = np.arange(fi_lo, min(fi_hi + 1, n_total), dtype=float)

        interp_x = np.interp(all_fi, fis, pxs)
        interp_y = np.interp(all_fi, fis, pys)

        _kf_set = {int(f) for f in fis}
        _trail_indexed = (hasattr(self, '_trail') and len(self._trail) == self.total_frames)

        for i, fi in enumerate(range(fi_lo, min(fi_hi + 1, n_total))):
            ank = np.array([interp_x[i], interp_y[i]])
            if sl is not None and sl > 1:
                v = ank - kne
                d = float(np.linalg.norm(v))
                if d > 1e-6:
                    ank = kne + v / d * sl
            h = hip if hip is not None else self._auto_hip(kne, ank)
            self._angles[fi] = _angle_deg(h, kne, ank)
            # Update the frame-indexed trail so the visual trail and _current_ank()
            # both reflect the corrected ankle positions immediately.
            if _trail_indexed and fi < len(self._trail):
                self._trail[fi] = ank.astype(np.float32)
            # Store every keyframe as a pin so playback, Retrack, and _current_ank()
            # all honour the exact positions the user drew.
            if fi in _kf_set:
                self._ankle_pins[fi] = ank.copy()

        self._angles = self._postprocess_angles(self._angles)
        # Restore exact keyframe angles — smoothing must not blur path corrections.
        if self.tracker.ready:
            _h, _k = self.tracker.hip0, self.tracker.knee0
            for _pfi, _pank in self._ankle_pins.items():
                if _pfi < len(self._angles):
                    self._angles[_pfi] = _angle_deg(_h, _k,
                                                     np.array(_pank, dtype=np.float64))

        # Automatically continue tracking from the last keyframe so frames beyond
        # the path span are updated — the user doesn't need a separate Retrack step.
        last_kf_fi, last_kf_pos = kfs[-1]
        last_kf_fi = int(last_kf_fi)
        if last_kf_fi + 1 < n_total and self.tracker.ready:
            self._btn_retrack.config(state="disabled")
            self._status.config(
                text=f"Path applied ({fi_lo}–{fi_hi}).  Continuing tracking beyond last point…")
            hip0 = self.tracker.hip0.copy()
            kne0 = self.tracker.knee0.copy()
            path_vid = self.video_path

            def _continue():
                ank_init = np.array(last_kf_pos, dtype=np.float64)
                # Project to arc
                v0 = ank_init - kne0
                d0 = float(np.linalg.norm(v0))
                if d0 > 1e-6 and sl is not None and sl > 1:
                    ank_init = kne0 + v0 / d0 * sl
                cap2 = cv2.VideoCapture(path_vid)
                cap2.set(cv2.CAP_PROP_POS_FRAMES, last_kf_fi)
                ok, f0 = cap2.read()
                if ok:
                    _side_p = getattr(self.tracker, '_side', 'right')
                    t2 = _MPBatchTracker(_side_p, fps=self.fps) if _MP_AVAIL else _ArcTracker()
                    t2.init(f0, hip0, kne0, ank_init)
                    if hasattr(self.tracker, '_anchor_xfrac') and self.tracker._anchor_xfrac is not None:
                        t2._anchor_xfrac = self.tracker._anchor_xfrac
                    new_ang, new_tr = {}, {}
                    for fi in range(last_kf_fi + 1, n_total):
                        ok2, fr = cap2.read()
                        if not ok2: break
                        _, _, ank2, ang2 = t2.step(fr)
                        new_ang[fi] = ang2
                        new_tr[fi]  = ank2.copy() if ank2 is not None else ank_init.astype(np.float32)
                cap2.release()
                self.after(0, lambda: self._on_retrack_done(new_ang, new_tr, last_kf_fi))

            import threading as _th2
            _th2.Thread(target=_continue, daemon=True).start()
        else:
            self._update_plot(fi_hi)
            self._show_frame_idx(fi_lo)
            self._compute_and_show_pt()

        n_frames = fi_hi - fi_lo + 1
        if last_kf_fi + 1 >= n_total:
            self._status.config(
                text=f"Path applied — {n_frames} frames ({fi_lo}–{fi_hi}) updated.")

    def _cmd_clear_path(self):
        self._path_keyframes.clear()
        self._btn_apply_path.config(state="disabled")
        self._btn_clear_path.config(state="disabled")
        if self._mode == "path":
            self._mode = "idle"
            self._hl_btn(None)
        self._show_frame_idx(self._frame_idx)
        self._status.config(text="Path cleared.")

    def _path_step_fwd(self, _event=None):
        if self._live or self.cap is None or self._playing:
            return
        fi = min(self._frame_idx + 1, self.total_frames - 1)
        self._frame_idx = fi
        self._show_frame_idx(fi)

    def _path_step_bwd(self, _event=None):
        if self._live or self.cap is None or self._playing:
            return
        fi = max(self._frame_idx - 1, 0)
        self._frame_idx = fi
        self._show_frame_idx(fi)

    def _on_escape_key(self, _event=None):
        if self._skel_guide_active:
            self._skel_guide_active = False
            self._skel_guide_step   = 0
            self._skel_guide_tmp    = [None, None, None]
            self._hl_btn(None)
            self._status.config(text="Manual skeleton cancelled.")
            self._show_frame_idx(self._frame_idx)

    # ── angle post-processing ─────────────────────────────────────────────────

    @staticmethod
    def _postprocess_angles(angles: list) -> list:
        """9-frame rolling median — kills jitter without distorting oscillation peaks."""
        arr = np.array(angles, dtype=float)
        out = arr.copy()
        W   = 9
        for i in range(len(arr)):
            if not np.isfinite(arr[i]):
                continue
            lo  = max(0, i - W // 2)
            hi  = min(len(arr), i + W // 2 + 1)
            seg = arr[lo:hi]
            fin = seg[np.isfinite(seg)]
            if len(fin) >= 3:
                out[i] = float(np.median(fin))
        return out.tolist()

    # ── stats + plot ──────────────────────────────────────────────────────────

    def _clear_pt_panel(self):
        self._clear_annotations()
        n_sel = sum(1 for v in self._param_active.values() if v.get()) \
                if hasattr(self, "_param_active") else 6
        self._pt_score_key_lbl.config(
            text=f"PT score ({n_sel} param{'s' if n_sel != 1 else ''} selected):")
        self._pt_score_lbl.config(text="—", fg="#475569")
        for lbl in self._pt_param_lbls.values():
            lbl.config(text="—", fg="#0F172A")
        if hasattr(self, "_pt_gauge"):
            self._pt_gauge.itemconfig(self._gauge_ptr, state="hidden")
        self._last_pt_params = None
        self._last_pt_s      = None
        self._last_pt_f      = None
        self._last_mas       = None
        self._btn_save_trial.config(state="disabled")

    def _compute_and_show_pt(self):
        """Compute Popović PT score from _angles and refresh the PT panel."""
        _GOOD = "#2563EB"   # in-range: blue on white
        _BAD  = "#DC2626"   # out-of-range: red on white

        def _clear():
            self._clear_pt_panel()

        if not _PT_AVAIL:
            return

        angles = np.array(self._angles, dtype=float)
        # Mask frames outside the trim window — the PT algorithm should only
        # evaluate the swing cycles the user has selected.
        trim_s = self._trim_start
        trim_e = self._trim_end_eff()
        if trim_s > 0 or trim_e < len(angles):
            angles = angles.copy()
            if trim_s > 0:
                angles[:trim_s] = float("nan")
            if trim_e < len(angles):
                angles[trim_e:] = float("nan")

        if np.sum(np.isfinite(angles)) < 50:
            _clear(); return

        # Determine the release frame before passing trim-masked angles to
        # compute_pt_params.  When a trim window removes the pre-release hold
        # period, _detect_release has no stable baseline and misfires mid-swing,
        # causing N and all other parameters to be computed from the wrong origin.
        # Fix: always detect from the full (unmasked) array where the hold
        # baseline is present, then pass that frame as an explicit anchor.
        if self._manual_release_fi is not None:
            _release_idx = self._manual_release_fi
        else:
            _t_full = np.arange(len(self._angles)) / max(self.fps, 1.0)
            _p_full = compute_pt_params(_t_full, np.array(self._angles, dtype=float))
            if _p_full is not None and len(_p_full.get("t_r", [])) > 0:
                _release_idx = int(round(float(_p_full["t_r"][0]) * max(self.fps, 1.0)))
            else:
                _release_idx = None

        t = np.arange(len(angles)) / max(self.fps, 1.0)
        params = compute_pt_params(t, angles, release_idx=_release_idx)
        if params is None:
            _clear(); return

        pt_s = compute_pt_score_simple(params)
        pt_f = compute_pt_score(params)

        # Custom score from user-selected parameters
        pt_custom = self._custom_pt_score(params)
        mas = pt_to_mas(pt_custom)

        # Resolve release frame index (manual > pre-detected > auto from params)
        if self._manual_release_fi is not None:
            self._release_fi = self._manual_release_fi
        elif _release_idx is not None:
            self._release_fi = _release_idx
        else:
            t_r_arr = params.get("t_r")
            if t_r_arr is not None and len(t_r_arr) > 0:
                self._release_fi = int(round(float(t_r_arr[0]) * max(self.fps, 1.0)))
            else:
                self._release_fi = None

        self._last_pt_params = params
        self._last_pt_s      = pt_s
        self._last_pt_f      = pt_f
        self._last_mas       = mas
        self._btn_save_trial.config(state="normal")
        self._annotate_plot(params)
        self._update_trim_spans()

        # Update the "Rest" stat to show the pre-release held angle
        pre_rel = params.get("pre_release_deg")
        if pre_rel is not None and math.isfinite(pre_rel):
            self._stat_rest.config(text=f"{pre_rel:.1f}°")

        # Update score label to reflect selected count
        n_sel = sum(1 for v in self._param_active.values() if v.get())
        self._pt_score_key_lbl.config(
            text=f"PT score ({n_sel} param{'s' if n_sel != 1 else ''} selected):")

        # Colour score label by zone
        if pt_custom <= PT_HEALTHY_MAX:
            _zone_col = "#16A34A"   # green
        elif pt_custom <= PT_BORDERLINE_MAX:
            _zone_col = "#D97706"   # amber
        else:
            _zone_col = "#DC2626"   # red
        self._pt_score_lbl.config(text=f"{pt_custom:.3f}", fg=_zone_col)

        # Update gauge pointer (▼ sits above the colored bar)
        if hasattr(self, "_pt_gauge"):
            _gx = int(min(max(pt_custom, 0.0), 1.0) * self._gauge_w)
            _gx = max(6, min(_gx, self._gauge_w - 6))
            _pt = self._gauge_top
            self._pt_gauge.coords(self._gauge_ptr,
                _gx, _pt, _gx - 5, _pt - 10, _gx + 5, _pt - 10)
            self._pt_gauge.itemconfig(self._gauge_ptr, state="normal")

        for key, lbl in self._pt_param_lbls.items():
            val     = params.get(key, float("nan"))
            ref     = HEALTHY_REF.get(key, float("nan"))
            fmt     = f"{val:.2f}" if math.isfinite(val) else "—"
            active  = self._param_active.get(key, tk.BooleanVar(value=True)).get()
            if not active:
                lbl.config(text=fmt, fg="#CBD5E1")   # dimmed — excluded from score
                continue
            if key in ("R2n", "N", "phi_max_ratio", "omega_max_n"):
                bad = math.isfinite(val) and math.isfinite(ref) and val < ref * 0.7
            elif key == "area_ratio":
                bad = math.isfinite(val) and math.isfinite(ref) and val > ref * 1.8
            elif key == "f":
                bad = (math.isfinite(val) and val > 0.1
                       and math.isfinite(ref) and abs(val - ref) > ref * 0.5)
            else:
                bad = False
            lbl.config(text=fmt, fg=_BAD if bad else _GOOD)

    def _display_angles(self) -> list:
        """Return self._angles with pre-release frames replaced by 180° (leg extended/setup)."""
        ri = self._release_fi
        if not ri:
            return list(self._angles)
        out = list(self._angles)
        for i in range(min(ri, len(out))):
            out[i] = 180.0
        return out

    def _update_stats(self, cur: float, fi: int):
        valid = [a for a in self._angles[:fi+1] if math.isfinite(a)]
        self._stat_cur.config( text=f"{cur:.1f}°" if math.isfinite(cur) else "—")
        self._stat_min.config( text=f"{min(valid):.1f}°" if valid else "—")
        n20 = max(1, len(valid) // 5)
        rest = float(np.median(valid[-n20:])) if len(valid) > 20 else float("nan")
        self._stat_rest.config(text=f"{rest:.1f}°" if math.isfinite(rest) else "—")
        amp  = max(valid) - min(valid) if len(valid) > 5 else float("nan")
        self._stat_amp.config( text=f"{amp:.1f}°"  if math.isfinite(amp) else "—")

    def _update_trim_spans(self):
        """Grey-shade out-of-trim regions on the angle graph."""
        if not hasattr(self, '_trim_spans'):
            self._trim_spans = []
        for sp in self._trim_spans:
            try:
                sp.remove()
            except Exception:
                pass
        self._trim_spans.clear()
        if self.total_frames < 2 or self.fps == 0:
            return
        trim_s = self._trim_start
        trim_e = self._trim_end_eff()
        total_t = self.total_frames / self.fps
        if trim_s > 0:
            self._trim_spans.append(
                self._ax.axvspan(0, trim_s / self.fps,
                                 color="#94A3B8", alpha=0.18, zorder=0))
        if trim_e < self.total_frames:
            self._trim_spans.append(
                self._ax.axvspan(trim_e / self.fps, total_t + 0.5,
                                 color="#94A3B8", alpha=0.18, zorder=0))
        self._plot_canvas.draw_idle()

    def _update_plot(self, fi: int):
        pairs = [(i/self.fps, a) for i, a in enumerate(self._angles[:fi+1]) if math.isfinite(a)]
        if not pairs:
            return
        ts, angs = zip(*pairs)
        self._line_plot.set_data(ts, angs)
        t_now = fi / self.fps
        self._vline.set_xdata([t_now, t_now])
        self._ax.set_xlim(0, max(t_now + 1, self.total_frames / self.fps))
        self._update_trim_spans()

    def _update_plot_live(self):
        pairs = [(i/self.fps, a) for i, a in enumerate(self._angles) if math.isfinite(a)]
        if not pairs:
            return
        ts, angs = zip(*pairs)
        self._line_plot.set_data(ts, angs)
        t_now = self._live_fi / self.fps
        self._vline.set_xdata([t_now, t_now])
        self._ax.set_xlim(0, max(t_now + 2, 5.0))
        self._plot_canvas.draw_idle()

    def _load_imu_overlay(self, imu_csv: str, video_t0: Optional[float] = None):
        """Overlay the recorded IMU goniometer trace on the angle plot.

        Both files stamp every sample with time.time(), so the IMU trace is
        placed on the video's time axis by subtracting the video's start
        epoch — the same alignment motive_mobile_sync.py relies on."""
        if not imu_csv or not os.path.exists(imu_csv):
            return False
        axis_col = {"pitch": "hip_pitch_deg", "roll": "hip_roll_deg",
                    "yaw": "hip_yaw_deg"}[self._imu_axis_var.get()]
        ts, vals, t0_meta = [], [], None
        try:
            with open(imu_csv, encoding="utf-8") as f:
                rows = []
                for line in f:
                    if line.startswith("#"):
                        if "t0_epoch" in line:
                            try:
                                t0_meta = float(line.split(",", 1)[1].strip())
                            except (IndexError, ValueError):
                                pass
                        continue
                    rows.append(line)
                for r in csv.DictReader(rows):
                    v = r.get(axis_col, "")
                    if not v:
                        continue
                    try:
                        ts.append(float(r["t_epoch"]))
                        vals.append(float(v))
                    except (KeyError, ValueError):
                        continue
        except OSError:
            return False
        if len(ts) < 2:
            return False

        base = video_t0 if video_t0 is not None else (t0_meta or ts[0])
        rel = [t - base for t in ts]
        self._line_imu.set_data(rel, vals)
        self._line_imu.set_visible(True)
        # Explicit handles — a bare legend() would also list the other
        # (hidden) methodology lines.
        self._ax.legend([self._line_plot, self._line_imu],
                        ["Active", "iPhone IMU"],
                        loc="upper right", fontsize=7, framealpha=0.85)
        self._plot_canvas.draw_idle()
        return True

    def _clear_imu_overlay(self):
        self._line_imu.set_data([], [])
        self._line_imu.set_visible(False)
        _lg = self._ax.get_legend()
        if _lg is not None:
            _lg.remove()
        self._plot_canvas.draw_idle()

    # ── measurement methodologies ────────────────────────────────────────────

    def _resample_to_frames(self, t_src, a_src, t_offset: float = 0.0) -> list:
        """Interpolate a (time, angle) series onto this video's frame grid.

        Frames outside the source's coverage stay NaN rather than being
        clamped, so a short trace does not masquerade as a full-length one."""
        n = self.total_frames or len(self._angles)
        if n <= 0:
            return []
        t = np.asarray(t_src, dtype=float) - float(t_offset)
        a = np.asarray(a_src, dtype=float)
        ok = np.isfinite(t) & np.isfinite(a)
        if ok.sum() < 2:
            return [float("nan")] * n
        t, a = t[ok], a[ok]
        order = np.argsort(t)
        t, a = t[order], a[order]
        grid = np.arange(n) / max(self.fps, 1.0)
        out = np.interp(grid, t, a, left=float("nan"), right=float("nan"))
        out[(grid < t[0]) | (grid > t[-1])] = float("nan")
        return [float(v) for v in out]

    def _release_time(self, angles) -> Optional[float]:
        """Release instant (s) for a frame-indexed series, or None."""
        if not _PT_AVAIL:
            return None
        arr = np.asarray(angles, dtype=float)
        if np.sum(np.isfinite(arr)) < 50:
            return None
        try:
            p = compute_pt_params(np.arange(len(arr)) / max(self.fps, 1.0), arr)
        except Exception:
            return None
        if p is None:
            return None
        t_r = p.get("t_r")
        if t_r is None or len(t_r) == 0:
            return None
        return float(t_r[0])

    def _set_method_series(self, key: str, angles: list):
        self._method_series[key] = list(angles)

    def _active_method(self) -> str:
        return self._method_key(self._method_var.get())

    def _cmd_set_method(self):
        """Switch the active methodology: swaps the analysed series and
        recomputes the PT score, stats and plot from it."""
        key = self._active_method()
        series = self._method_series.get(key)
        if not series:
            lbl = dict((k, l) for k, l, _c in self._METHODS)[key]
            hint = {
                "rgb":  "Run ▶▶ Track All first.",
                "imu":  "Record with 'Sync Goniometer' on, or open a video "
                        "that has a Trial_N_imu.csv beside it.",
                "opti": "Load a Motive export with ＋ OptiTrack.",
            }[key]
            messagebox.showinfo("No data for this methodology",
                                f"No {lbl} series is loaded.\n\n{hint}")
            # Revert the dropdown to whatever is actually displayed.
            for k, l, _c in self._METHODS:
                if self._method_series.get(k) and k == getattr(
                        self, "_shown_method", None):
                    self._method_var.set(l)
                    break
            return

        self._shown_method = key
        self._angles = list(series)
        self._clear_annotations()
        self._release_fi = None
        fi = min(self._frame_idx, max(0, len(self._angles) - 1))
        self._update_plot(fi)
        self._compute_and_show_pt()
        self._refresh_overlay()
        lbl = dict((k, l) for k, l, _c in self._METHODS)[key]
        n_ok = sum(1 for a in self._angles if math.isfinite(a))
        self._status.config(
            text=f"Methodology: {lbl} — {n_ok} valid frames. "
                 "PT score and graph recomputed from this source.")

    def _refresh_overlay(self):
        """Show every loaded methodology together, or hide them all."""
        show = bool(self._overlay_var.get())
        shown = []
        for key, lbl, _col in self._METHODS:
            line = self._overlay_lines[key]
            series = self._method_series.get(key)
            if show and series:
                ts = [i / max(self.fps, 1.0) for i, a in enumerate(series)
                      if math.isfinite(a)]
                vs = [a for a in series if math.isfinite(a)]
                if len(ts) >= 2:
                    line.set_data(ts, vs)
                    line.set_visible(True)
                    shown.append((line, lbl))
                    continue
            line.set_data([], [])
            line.set_visible(False)

        # The active trace duplicates one overlay line; hide it while
        # overlaying so the legend maps one line to one methodology.
        self._line_plot.set_visible(not (show and shown))
        lg = self._ax.get_legend()
        if lg is not None:
            lg.remove()
        if shown:
            # Build from explicit handles: a default legend() would also pick
            # up the hidden active trace and show a phantom entry.
            self._ax.legend([h for h, _l in shown], [l for _h, l in shown],
                            loc="upper right", fontsize=7, framealpha=0.85)
        self._plot_canvas.draw_idle()

    def _cmd_load_optitrack(self):
        """Load a Motive/OptiTrack export as the OptiTrack methodology."""
        if not _PT_AVAIL:
            messagebox.showwarning("Unavailable",
                                   "pendulastic_pt_score is not importable.")
            return
        if self.cap is None or self.total_frames <= 0:
            messagebox.showinfo("No video", "Open or record a video first — the "
                                "OptiTrack trace is resampled onto its frame grid.")
            return
        path = filedialog.askopenfilename(
            title="Select OptiTrack CSV",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialdir=os.path.join(BASE_DIR, "Recordings"))
        if not path:
            return
        try:
            t_o, a_o = load_optitrack(path)
        except Exception as e:
            messagebox.showerror("Load error", f"OptiTrack CSV:\n{e}")
            return

        # OptiTrack has no shared clock with the video, so align on the
        # release event — the one landmark both modalities observe.
        offset = 0.0
        note   = "aligned at t=0 (no release detected)"
        rgb = self._method_series.get("rgb") or self._angles
        t_r_vid = self._release_time(rgb) if rgb else None
        t_r_opt = self._release_time(
            self._resample_to_frames(t_o, a_o, 0.0)) if len(t_o) else None
        if t_r_vid is not None and t_r_opt is not None:
            offset = t_r_opt - t_r_vid
            note   = f"release-aligned (offset {offset:+.2f} s)"

        self._set_method_series("opti", self._resample_to_frames(t_o, a_o, offset))
        self._opti_csv_path = path
        n_ok = sum(1 for a in self._method_series["opti"] if math.isfinite(a))
        self._status.config(
            text=f"OptiTrack loaded: {os.path.basename(path)} — "
                 f"{n_ok} frames, {note}.")
        self._method_var.set("OptiTrack")
        self._cmd_set_method()

    def _load_imu_series(self, imu_csv: str, video_t0: Optional[float] = None) -> bool:
        """Load the recorded IMU trace as the iPhone methodology.

        The AHRS relative angle is a flexion angle (0° at the zeroed reference
        posture); the viewer works in interior angles (180° = extended), so the
        two are related by interior = 180 - flexion. This assumes Zero was
        taken with the limb extended, which is what the panel instructs."""
        if not imu_csv or not os.path.exists(imu_csv):
            return False
        axis_col = {"pitch": "hip_pitch_deg", "roll": "hip_roll_deg",
                    "yaw": "hip_yaw_deg"}[self._imu_axis_var.get()]
        ts, vals, t0_meta = [], [], None
        try:
            with open(imu_csv, encoding="utf-8") as f:
                rows = []
                for line in f:
                    if line.startswith("#"):
                        if "t0_epoch" in line:
                            try:
                                t0_meta = float(line.split(",", 1)[1].strip())
                            except (IndexError, ValueError):
                                pass
                        continue
                    rows.append(line)
                for r in csv.DictReader(rows):
                    v = r.get(axis_col, "")
                    if not v:
                        continue
                    try:
                        ts.append(float(r["t_epoch"]))
                        vals.append(180.0 - float(v))
                    except (KeyError, ValueError):
                        continue
        except OSError:
            return False
        if len(ts) < 2:
            return False
        # Epoch stamps map onto the video timeline by subtracting the video's
        # start epoch — the alignment the shared time base exists for.
        base = video_t0 if video_t0 else (t0_meta or ts[0])
        self._set_method_series("imu", self._resample_to_frames(ts, vals, base))
        return True

    def _clear_annotations(self):
        for a in self._plot_annots:
            try:
                a.remove()
            except Exception:
                pass
        self._plot_annots.clear()

    def _annotate_plot(self, params: dict):
        """Overlay clinical key-point markers on the angle graph."""
        self._clear_annotations()

        artists = draw_pt_annotations(
            self._ax, params,
            manual_release=self._manual_release_fi is not None)
        if artists is None:
            self._plot_canvas.draw_idle()
            return
        self._plot_annots.extend(artists)

        # Redraw main trace: pre-release portion shown as flat 180° (leg extended)
        disp = self._display_angles()
        pairs = [(i / self.fps, a) for i, a in enumerate(disp) if math.isfinite(a)]
        if pairs:
            ts, angs = zip(*pairs)
            self._line_plot.set_data(ts, angs)

        self._ax.set_ylim(0, 185)   # guard after set_data: keep full 0–185° range
        self._ax.autoscale(enable=False, axis="y")
        self._plot_canvas.draw_idle()

    # ── toolbar commands ──────────────────────────────────────────────────────

    def _probe_cameras_bg(self) -> None:
        """Probe webcam indices 0-4 in a background thread (MSMF first, DirectShow fallback).
        Populates self._available_cams so _cmd_open_camera never blocks the UI thread."""
        found: list = []
        seen_idx: set = set()
        for idx in range(5):
            for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW):
                try:
                    c = cv2.VideoCapture(idx, backend)
                    if c.isOpened():
                        ok, _ = c.read()
                        if ok and idx not in seen_idx:
                            w = int(c.get(cv2.CAP_PROP_FRAME_WIDTH))
                            h = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
                            found.append((idx, backend, w, h))
                            seen_idx.add(idx)
                    c.release()
                    if idx in seen_idx:
                        break  # found it with this backend, skip DirectShow for same index
                except Exception:
                    pass
        self._available_cams = found
        self._cam_probe_done = True

    def _cmd_open_camera(self):
        if not self._cam_probe_done:
            messagebox.showinfo(
                "Camera scan",
                "Webcam scan is still in progress.\n"
                "Please wait a moment and try again.",
                parent=self)
            return

        available = [(idx, w, h) for idx, _backend, w, h in self._available_cams]

        # Fetch friendly names from Windows (best-effort)
        names = []
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-PnpDevice -Class Camera -Status OK "
                 "| Sort-Object InstanceId "
                 "| Select-Object -ExpandProperty FriendlyName"],
                capture_output=True, text=True, timeout=4,
            )
            names = [n.strip() for n in r.stdout.strip().splitlines() if n.strip()]
        except Exception:
            pass

        # Always show a menu so the phone option is always reachable.
        # If there's exactly one webcam AND phone streaming is unavailable,
        # open it directly to preserve the original behaviour.
        if len(available) == 1 and not _PPS_AVAIL:
            self._open_camera_by_index(available[0][0])
            return

        C = _C
        menu = tk.Menu(self, tearoff=0,
                       bg=C["BG"], fg=C["FG"],
                       activebackground=C["BTN_ACT"], activeforeground=C["FG"],
                       font=("Segoe UI", 10))

        # Phone camera option at the top
        if _PPS_AVAIL:
            menu.add_command(label="\U0001f4f1  Phone Camera (WiFi / QR)",
                             command=self._cmd_open_phone_camera)
            if available:
                menu.add_separator()

        if not available:
            if not _PPS_AVAIL:
                messagebox.showerror("No camera",
                                     "No webcam detected (checked indices 0-4).")
                return
        else:
            for i, (idx, w, h) in enumerate(available):
                name  = names[i] if i < len(names) else f"Camera {idx}"
                label = f"{name}  ({w}×{h})"
                menu.add_command(label=label,
                                 command=lambda _i=idx: self._open_camera_by_index(_i))

        btn = self._btn_cam
        menu.post(btn.winfo_rootx(),
                  btn.winfo_rooty() + btn.winfo_height())

    def _open_camera_by_index(self, idx: int):
        """Open the camera at the given index using the backend found during probe."""
        backend = next(
            (b for i, b, _w, _h in self._available_cams if i == idx),
            cv2.CAP_DSHOW,
        )
        cap = cv2.VideoCapture(idx, backend)
        if not cap.isOpened():
            messagebox.showerror("Camera error", f"Could not open camera {idx}.")
            return

        if self._phone_mode and _PPS_AVAIL:
            _pps.stop()
        self._phone_mode = False

        self._playing = False
        self._recording = False
        if self._writer:
            self._writer.release()
            self._writer = None
        if self.cap:
            self.cap.release()
        self.tracker.reset()
        self._hip_click = self._knee_click = self._ankle_click = None
        self._angles = []
        self._trail  = []

        self.cap   = cap
        self._live = True
        self._live_fi = 0
        self._last_live_frame = None

        ok, frame = self.cap.read()
        if not ok:
            messagebox.showerror("Camera error", f"Could not read from camera {idx}.")
            self.cap.release(); self.cap = None; self._live = False
            return
        self._last_live_frame = frame
        h, w = frame.shape[:2]
        self._cam_w, self._cam_h = w, h
        raw_fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps = raw_fps if 1 < raw_fps < 120 else 30.0
        self.total_frames = 0
        self._vid_w = w
        self._vid_h = h
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        self._scale = min((cw if cw > 10 else DISPLAY_W) / w,
                          (ch if ch > 10 else DISPLAY_H) / h)
        self._canvas.delete("hint")

        # Swap transport controls: hide seek buttons, show Rec + camera controls
        for b in (self._btn_go_first, self._btn_go_prev,
                  self._btn_play, self._btn_go_next, self._btn_go_last):
            b.pack_forget()
        self._btn_rec.pack(side="left", padx=2)
        self._cam_ctrl.pack(side="left", padx=4)

        self._line_plot.set_data([], [])
        self._plot_canvas.draw()
        for lbl in (self._stat_cur, self._stat_min, self._stat_rest, self._stat_amp):
            lbl.config(text="—")
        self._clear_pt_panel()
        self.title("Pendulastic Viewer — Live Camera")
        self._vid_lbl.config(text=f"Camera {idx}  {w}×{h}  {self.fps:.0f} fps")
        self._status.config(
            text="Camera live. Click \U0001f9b5 Knee then click the knee joint, then \U0001f9b6 Ankle → "
                 "adjust until the overlay fits, then ⏺ Record.")
        self._time_lbl.config(text="● LIVE")
        self._push_frame(_draw(frame, None, None, None, float("nan"), [], self._scale))

    # ── phone camera ───────────────────────────────────────────────────────────

    def _cmd_open_phone_camera(self):
        """Start phone-as-camera streaming: launch servers, show QR dialog."""
        if not _PPS_AVAIL:
            messagebox.showerror("Phone Camera",
                                 "pendulastic_phone_server module not found.")
            return

        # Stop existing phone session if running
        if self._phone_mode:
            _pps.stop()

        self._playing = False
        self._recording = False
        if self._writer:
            self._writer.release()
            self._writer = None
        if self.cap:
            self.cap.release()
            self.cap = None
        self.tracker.reset()
        self._hip_click = self._knee_click = self._ankle_click = None
        self._angles = []
        self._trail  = []

        # Default dimensions — will be updated from the first received frame
        default_w, default_h = 1280, 720
        self._cam_w, self._cam_h = default_w, default_h
        self.fps = 15.0
        self.total_frames = 0
        self._vid_w = default_w
        self._vid_h = default_h
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        self._scale = min((cw if cw > 10 else DISPLAY_W) / default_w,
                          (ch if ch > 10 else DISPLAY_H) / default_h)

        self._phone_mode = True
        self._live = True
        self._live_fi = 0
        self._last_live_frame = None
        self._skip_marker_check = False

        try:
            upload_dir = os.path.join(BASE_DIR, "uploads")
            local_ip, http_port, _ws_port = _pps.start(upload_dir=upload_dir)
        except Exception as exc:
            messagebox.showerror("Phone Camera", f"Failed to start server:\n{exc}")
            self._phone_mode = False
            self._live = False
            return

        self._canvas.delete("hint")
        for b in (self._btn_go_first, self._btn_go_prev,
                  self._btn_play, self._btn_go_next, self._btn_go_last):
            b.pack_forget()
        self._btn_rec.pack(side="left", padx=2)
        self._cam_ctrl.pack(side="left", padx=4)

        self._line_plot.set_data([], [])
        self._plot_canvas.draw()
        for lbl in (self._stat_cur, self._stat_min, self._stat_rest, self._stat_amp):
            lbl.config(text="—")
        self._clear_pt_panel()
        self.title("Pendulastic Viewer — Phone Camera")
        self._vid_lbl.config(text=f"Phone Camera  (waiting for connection…)")
        self._status.config(text="Scan the QR code on your phone, then place Knee + Ankle markers.")
        self._time_lbl.config(text="⬤ LIVE")

        url = f"http://{local_ip}:{http_port}"
        alt_ips: list[str] = []
        if _PPS_AVAIL and hasattr(_pps, "get_all_local_ips"):
            try:
                all_ips = _pps.get_all_local_ips()
                alt_ips = [ip for ip in all_ips if ip != local_ip]
            except Exception:
                pass
        self._phone_qr_dlg = None
        self._show_phone_qr_dialog(url, alt_ips=alt_ips)
        if _PPS_AVAIL and hasattr(_pps, "get_public_url"):
            self._poll_ngrok(0)

    def _show_phone_qr_dialog(self, url: str, alt_ips: list[str] | None = None):
        """Show a non-modal dialog with QR code and URL for phone connection."""
        C = _C
        BG  = C["BG"]
        FG  = C["FG"]
        FG2 = C["FG2"]

        dlg = tk.Toplevel(self)
        dlg.title("Connect Phone Camera")
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        self._phone_qr_dlg = dlg

        tk.Label(dlg, text="Open this URL on your phone",
                 bg=BG, fg=FG, font=("Segoe UI", 11, "bold")).pack(pady=(14, 4))

        # Try to generate QR code image using the qrcode package
        qr_photo = None
        self._phone_qr_label = None  # reset; set below if QR renders
        try:
            import qrcode as _qrmod
            qr = _qrmod.QRCode(box_size=7, border=3)
            qr.add_data(url)
            qr.make(fit=True)
            raw = qr.make_image(fill_color="black", back_color="white")
            # qrcode v8+ returns a PilImage wrapper; unwrap to PIL.Image first.
            pil_img = raw.get_image() if hasattr(raw, "get_image") else raw
            pil_img = pil_img.convert("RGB")
            qr_photo = ImageTk.PhotoImage(pil_img)
            lbl_qr = tk.Label(dlg, image=qr_photo, bg=BG, relief="flat")
            lbl_qr.image = qr_photo   # keep reference alive
            lbl_qr.pack(pady=6)
            self._phone_qr_label = lbl_qr  # updated when ngrok URL arrives
        except Exception as _qr_err:
            tk.Label(dlg, text=f"(QR unavailable: {_qr_err})",
                     bg=BG, fg=FG2, font=("Segoe UI", 8)).pack(pady=4)

        tk.Label(dlg, text="— or type this URL in your phone's browser —",
                 bg=BG, fg=FG2, font=("Segoe UI", 8)).pack()

        # ── Primary URL entry + Copy button ──────────────────────────────────
        http_port = url.rsplit(":", 1)[-1]

        # Current displayed URL (may switch when user picks an alt IP or ngrok connects)
        _active_url = [url]
        self._phone_qr_active_url = _active_url  # updated by _poll_ngrok

        row = tk.Frame(dlg, bg=BG)
        row.pack(padx=16, pady=(4, 4))
        url_var = tk.StringVar(value=url)
        self._phone_qr_url_var = url_var  # updated by _poll_ngrok
        url_ent = tk.Entry(row, textvariable=url_var, state="readonly",
                           font=("Segoe UI Mono", 12), width=26,
                           bg="#1E293B", fg="#E2E8F0",
                           readonlybackground="#1E293B",
                           insertbackground="#E2E8F0", relief="flat", bd=6)
        url_ent.pack(side="left", padx=(0, 6))

        def _copy():
            dlg.clipboard_clear()
            dlg.clipboard_append(_active_url[0])
            copy_btn.config(text="Copied!")
            dlg.after(1500, lambda: copy_btn.config(text="Copy"))

        copy_btn = tk.Button(row, text="Copy", command=_copy,
                             bg="#334155", fg="#E2E8F0", font=("Segoe UI", 9),
                             relief="flat", padx=8, pady=3, cursor="hand2")
        copy_btn.pack(side="left")

        # ── Alternate IPs ─────────────────────────────────────────────────────
        if alt_ips:
            tk.Label(dlg,
                     text="If the URL above doesn't work, try an alternate address:",
                     bg=BG, fg=FG2, font=("Segoe UI", 8)).pack(pady=(2, 0))
            for alt_ip in alt_ips[:4]:   # show at most 4 alternates
                alt_url = f"http://{alt_ip}:{http_port}"
                alt_row = tk.Frame(dlg, bg=BG)
                alt_row.pack(padx=16, pady=1)

                alt_ent = tk.Entry(alt_row, font=("Segoe UI Mono", 9), width=28,
                                   bg="#162032", fg="#94A3B8",
                                   readonlybackground="#162032",
                                   relief="flat", bd=4)
                alt_ent.insert(0, alt_url)
                alt_ent.config(state="readonly")
                alt_ent.pack(side="left", padx=(0, 4))

                def _use_this(u=alt_url):
                    _active_url[0] = u
                    url_var.set(u)

                tk.Button(alt_row, text="Use", command=_use_this,
                          bg="#1E3A5F", fg="#93C5FD", font=("Segoe UI", 8),
                          relief="flat", padx=6, pady=2, cursor="hand2").pack(side="left")

        # ── ngrok tunnel status ───────────────────────────────────────────────
        self._phone_tunnel_lbl = tk.Label(
            dlg, text="Connecting to HTTPS tunnel…",
            bg=BG, fg="#FBBF24", font=("Segoe UI", 9))
        self._phone_tunnel_lbl.pack(pady=(6, 0))

        def _setup_token():
            from tkinter import simpledialog
            token = simpledialog.askstring(
                "Set up ngrok Auth Token",
                "1. Create free account at: dashboard.ngrok.com/signup\n"
                "2. Copy token from:  dashboard.ngrok.com/get-started/your-authtoken\n\n"
                "Paste token here:",
                parent=dlg)
            if not token or not token.strip():
                return
            token = token.strip()
            try:
                from pyngrok import ngrok as _ng
                _ng.set_auth_token(token)
            except Exception as exc:
                messagebox.showerror("ngrok", f"Failed to save token:\n{exc}", parent=dlg)
                return
            # Reset state and retry tunnel
            try:
                old = _pps._ngrok_tunnel
                if old is not None:
                    from pyngrok import ngrok as _ng2
                    _ng2.disconnect(old.public_url)
            except Exception:
                pass
            _pps._ngrok_tunnel = None
            _pps._ngrok_status = "not_started"
            _pps._public_url   = None
            try:
                self._phone_tunnel_lbl.config(
                    text="Connecting to HTTPS tunnel…", fg="#FBBF24")
            except Exception:
                pass
            import threading as _thr
            _thr.Thread(target=_pps._ngrok_worker, args=(_pps.PORT_HTTP,),
                        daemon=True, name="pps-ngrok-retry").start()
            self._poll_ngrok(0)

        self._ngrok_setup_btn = tk.Button(
            dlg, text="Set up free HTTPS tunnel  →",
            command=_setup_token,
            bg=BG, fg="#60A5FA", font=("Segoe UI", 8),
            relief="flat", cursor="hand2", bd=0, pady=0)
        self._ngrok_setup_btn.pack(pady=(1, 3))

        # ── Connection status ─────────────────────────────────────────────────
        # Connection status — polled from _tick() by name
        self._phone_status_lbl = tk.Label(
            dlg, text="Waiting for phone…",
            bg=BG, fg="#FBBF24", font=("Segoe UI", 11, "bold"))
        self._phone_status_lbl.pack(pady=(6, 4))

        tk.Label(dlg, text="Record with your back camera → tap Send to Desktop.\n"
                            "The video opens here automatically when the upload finishes.",
                 bg=BG, fg=FG2, font=("Segoe UI", 8), justify="center").pack(pady=(0, 6))

        tk.Button(dlg, text="Close", command=dlg.destroy,
                  bg="#475569", fg="#E2E8F0", font=("Segoe UI", 10),
                  relief="flat", padx=14, pady=4, cursor="hand2").pack(pady=(0, 14))

    # ── ngrok URL polling ──────────────────────────────────────────────────────

    def _poll_ngrok(self, attempts: int) -> None:
        """Called periodically after phone dialog opens; updates URL/QR when tunnel ready."""
        if not self._phone_mode or not _PPS_AVAIL:
            return
        if not hasattr(_pps, "get_public_url") or not hasattr(_pps, "get_ngrok_status"):
            return

        pub = _pps.get_public_url()
        status, err = _pps.get_ngrok_status()

        if pub:
            # Update stored URL
            if hasattr(self, "_phone_qr_active_url"):
                self._phone_qr_active_url[0] = pub
            if hasattr(self, "_phone_qr_url_var"):
                try:
                    self._phone_qr_url_var.set(pub)
                except Exception:
                    pass
            # Regenerate QR for the HTTPS URL
            if getattr(self, "_phone_qr_label", None) is not None:
                try:
                    import qrcode as _qrmod
                    qr = _qrmod.QRCode(box_size=7, border=3)
                    qr.add_data(pub)
                    qr.make(fit=True)
                    raw = qr.make_image(fill_color="black", back_color="white")
                    pil_img = raw.get_image() if hasattr(raw, "get_image") else raw
                    pil_img = pil_img.convert("RGB")
                    photo = ImageTk.PhotoImage(pil_img)
                    self._phone_qr_label.config(image=photo)
                    self._phone_qr_label.image = photo
                except Exception:
                    pass
            # Update tunnel status label and hide setup button
            if getattr(self, "_phone_tunnel_lbl", None) is not None:
                try:
                    self._phone_tunnel_lbl.config(
                        text="✓ HTTPS tunnel ready — works on any network (even cellular)",
                        fg="#4ADE80")
                except Exception:
                    pass
            if getattr(self, "_ngrok_setup_btn", None) is not None:
                try:
                    self._ngrok_setup_btn.pack_forget()
                except Exception:
                    pass
        elif status == "error":
            if getattr(self, "_phone_tunnel_lbl", None) is not None:
                msg = "HTTPS tunnel unavailable — use WiFi URL above"
                if err and ("4018" in err or "401" in err or "auth" in err.lower()):
                    msg = "ngrok auth needed: ngrok.com → free account → run: ngrok authtoken TOKEN"
                try:
                    self._phone_tunnel_lbl.config(text=msg, fg="#94A3B8")
                except Exception:
                    pass
        elif status == "unavailable":
            if getattr(self, "_phone_tunnel_lbl", None) is not None:
                try:
                    self._phone_tunnel_lbl.config(text="", fg="#94A3B8")
                except Exception:
                    pass
        elif attempts < 45:
            self.after(1000, lambda a=attempts + 1: self._poll_ngrok(a))

    # ── countdown ─────────────────────────────────────────────────────────────

    def _start_countdown(self, secs: int):
        """Start a visible countdown; recording begins when it hits 0."""
        if not self._live or self._recording:
            return
        # Warn about missing markers BEFORE the countdown begins
        if not self.tracker.ready:
            if not messagebox.askyesno("No markers set",
                    "Knee + Ankle markers not placed yet.\n"
                    "Recording will start without tracking.\n\nContinue?"):
                return
            self._skip_marker_check = True   # don't ask again at record-toggle
        self._cancel_countdown()
        errors = self._validate_camera_setup()
        if errors:
            self._show_camera_guide(errors, pending_countdown=secs)
            return
        self._countdown_remaining = secs
        self._status.config(text=f"Recording starts in {secs}s — get ready…")
        self._countdown_after_id = self.after(1000,
                                               lambda: self._countdown_step(secs - 1))

    def _countdown_step(self, remaining: int):
        self._countdown_remaining = remaining
        if remaining <= 0:
            self._countdown_remaining = 0
            self._cmd_rec_toggle()
            return
        self._status.config(text=f"Recording starts in {remaining}s…")
        self._countdown_after_id = self.after(1000,
                                               lambda: self._countdown_step(remaining - 1))

    def _cancel_countdown(self):
        if self._countdown_after_id:
            self.after_cancel(self._countdown_after_id)
            self._countdown_after_id = None
        self._countdown_remaining = 0

    # ── camera setup validation ────────────────────────────────────────────────

    def _validate_camera_setup(self) -> list:
        """Return a list of error dicts; empty = all clear."""
        # Goniometer connectivity does not depend on the camera or markers, so
        # it is checked first — the later checks return early and would
        # otherwise hide it until markers were placed.
        errors = self._validate_goniometer_setup()
        frame = self._last_live_frame
        if frame is None:
            errors.append({
                "code": "no_frame",
                "msg": "No camera frame available.",
                "tip": "Reconnect the camera and try again.",
                "region": None,
            })
            return errors

        h, w = frame.shape[:2]
        import cv2 as _cv2

        # Brightness
        gray = _cv2.cvtColor(frame, _cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        if brightness < 40:
            errors.append({
                "code": "too_dark",
                "msg": "Frame is too dark — patient may not be visible.",
                "tip": "Add more lighting. Avoid placing the patient in front of a bright window.",
                "region": "lighting",
            })
        elif brightness > 220:
            errors.append({
                "code": "too_bright",
                "msg": "Frame is overexposed.",
                "tip": "Move away from direct sunlight or dim the room lights.",
                "region": "lighting",
            })

        # Blur (Laplacian variance)
        lap_var = float(_cv2.Laplacian(gray, _cv2.CV_64F).var())
        if lap_var < 25:
            errors.append({
                "code": "blurry",
                "msg": "Image is blurry — check focus and camera stability.",
                "tip": "Mount the camera on a stable surface. Tap the lens to check autofocus.",
                "region": "frame",
            })

        # Markers
        if not self.tracker.ready or self._knee_click is None or self._ankle_click is None:
            errors.append({
                "code": "no_markers",
                "msg": "Knee and/or Ankle markers not placed.",
                "tip": "Click 🦵 Knee then click the patient's knee joint on screen. "
                       "Then click 🦶 Ankle and click the ankle.",
                "region": "knee",
            })
            return errors  # can't do geometry checks without markers

        kx, ky = self._knee_click
        ax_, ay = self._ankle_click

        # Markers near edge
        margin = 0.08
        for name, px, py, region in [
                ("Knee",  kx,  ky,  "knee"),
                ("Ankle", ax_, ay,  "ankle")]:
            if px < margin * w or px > (1 - margin) * w:
                errors.append({
                    "code": f"{region}_near_h_edge",
                    "msg": f"{name} marker is too close to the left/right edge.",
                    "tip": "Recentre the camera so the full leg is visible with space on both sides.",
                    "region": region,
                })
            if py < margin * h or py > (1 - margin) * h:
                errors.append({
                    "code": f"{region}_near_v_edge",
                    "msg": f"{name} marker is too close to the top/bottom edge.",
                    "tip": "Pull the camera back or tilt it so the full leg fits vertically.",
                    "region": region,
                })

        # Leg too small
        leg_px = math.hypot(kx - ax_, ky - ay)
        diag   = math.hypot(w, h)
        if leg_px < 0.13 * diag:
            errors.append({
                "code": "leg_too_small",
                "msg": "Leg appears too small in the frame — camera is too far away.",
                "tip": "Move the camera closer until the leg fills at least 1/3 of the frame.",
                "region": "full_leg",
            })

        # Ankle below knee (rough sanity: for hanging pendulum, ankle should be
        # at greater y-pixel than knee on screen, i.e. lower on the image)
        if ay < ky - 20:
            errors.append({
                "code": "ankle_above_knee",
                "msg": "Ankle appears above the knee — markers may be swapped.",
                "tip": "Re-place the markers: 🦵 Knee = the knee joint, 🦶 Ankle = the ankle.",
                "region": "ankle",
            })

        # MediaPipe visibility checks (only after ≥30 live frames so scores stabilise)
        if hasattr(self.tracker, "last_kne_vis"):
            n_frames = len(getattr(self.tracker, "_vis_window", []))
            if n_frames >= 30:
                cov = self.tracker.coverage_pct
                if cov < 0.50:
                    errors.append({
                        "code": "low_knee_visibility",
                        "msg": (f"Knee landmark poorly visible "
                                f"({cov*100:.0f}% of recent frames)."),
                        "tip": (
                            "Expose the patient's knee: roll up the pant leg or "
                            "have them wear shorts. Avoid dark or patterned clothing "
                            "over the knee joint."
                        ),
                        "region": "knee",
                    })
                elif self.tracker.last_kne_vis < 0.50:
                    errors.append({
                        "code": "knee_occluded",
                        "msg": "Knee landmark confidence is low — knee may be partially obscured.",
                        "tip": (
                            "Aim the camera at knee height, 1.5–2.5 m to the patient's SIDE "
                            "(sagittal view). Ensure no clothing or limb overlaps the knee."
                        ),
                        "region": "knee",
                    })
            if getattr(self.tracker, "assessor_warning", False):
                errors.append({
                    "code": "assessor_tracked",
                    "msg": "Camera appears to be tracking the assessor, not the patient.",
                    "tip": (
                        "Aim the camera at the patient's leg. "
                        "The assessor should stand outside or behind the camera's field of view."
                    ),
                    "region": "full_leg",
                })

        return errors

    def _validate_goniometer_setup(self) -> list:
        """Check the iPhone goniometer link. Empty unless 'Sync Goniometer'
        is on and the phones are not both streaming."""
        if getattr(self, "_var_goniometer", None) is None or not self._var_goniometer.get():
            return []

        st = _imu.get_state() if _IMU_AVAIL else None
        n_imu = (int(st["proximal"]["connected"]) + int(st["distal"]["connected"])
                 if st else 0)
        with self._goniometer_lock:
            g_last = self._goniometer_last_rx
        udp_live = g_last > 0 and (time.time() - g_last) <= 2.0

        if n_imu == 0 and not udp_live:
            return [{
                "code": "goniometer_no_data",
                "msg": "No data received from the iPhone goniometer.",
                "tip": (
                    f"In the Sensor Stream app set the address to "
                    f"{_imu.get_local_ip() if _IMU_AVAIL else '<LAN IP>'}:"
                    f"{_imu.PORT if _IMU_AVAIL else 5000} and enable "
                    "Accelerometer, Gyroscope and Magnetometer. "
                    "Phone and laptop must be on the same Wi-Fi network."
                ),
                "region": None,
            }]
        _enough = (n_imu == 2 or (n_imu == 1 and self._imu_single_mode()))
        if _enough and self._var_wait_sync.get():
            _sy = _imu.sync_status() if _IMU_AVAIL else {"state": "synced"}
            if _sy["state"] == "unstable":
                return [{
                    "code": "goniometer_sync_unstable",
                    "msg": f"Phone clock sync is unstable — {_sy['detail']}.",
                    "tip": ("Move the phones closer to the hotspot, or turn off "
                            "unused sensor streams in the app to reduce Wi-Fi "
                            "load. Recording now risks a misaligned IMU trace."),
                    "region": None,
                }]
            if _sy["state"] != "synced":
                return [{
                    "code": "goniometer_syncing",
                    "msg": f"Still syncing phone clocks — {_sy['detail']}.",
                    "tip": ("Leave both phones streaming for a few seconds. "
                            "The Time Sync panel turns green when the IMU and "
                            "laptop timestamps are aligned."),
                    "region": None,
                }]
        if n_imu == 1 and not self._imu_single_mode():
            return [{
                "code": "goniometer_one_phone",
                "msg": "Only one IMU phone is streaming — relative joint "
                       "angle needs two.",
                "tip": (
                    "Start Sensor Stream on the second phone and point it at "
                    "the same address. One phone goes on the proximal segment "
                    "(torso or thigh), the other on the distal segment "
                    "(thigh or shank)."
                ),
                "region": None,
            }]
        return []

    def _cmd_check_setup(self):
        errors = self._validate_camera_setup()
        self._show_camera_guide(errors)

    def _show_camera_guide(self, errors: list, pending_countdown: int = 0):
        """Open a guide popup explaining the detected setup issues."""
        BG   = "#0B1928"; PNL  = "#0D2238"; SFC  = "#112040"
        FG   = "#C8E0F5"; FG2  = "#5A8AB0"; FG3  = "#2E5070"
        ERR  = "#F87171"; WARN = "#FBBF24"; OK   = "#4ADE80"
        PURP = "#A78BFA"

        top = tk.Toplevel(self)
        top.title("Camera Setup Guide")
        top.configure(bg=BG)
        top.geometry("680x640")
        top.resizable(True, True)
        top.transient(self)
        top.grab_set()

        # ── header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(top, bg=PNL, pady=10, padx=14)
        hdr.pack(fill="x")
        if errors:
            tk.Label(hdr, text="⚠  Camera Setup Issues", bg=PNL, fg=WARN,
                     font=("Segoe UI", 12, "bold")).pack(side="left")
            tk.Label(hdr,
                     text=f"{len(errors)} issue{'s' if len(errors)!=1 else ''} found",
                     bg=PNL, fg=FG2, font=("Segoe UI", 9)).pack(side="right")
        else:
            tk.Label(hdr, text="✓  Camera Setup — Best Practices", bg=PNL,
                     fg=OK, font=("Segoe UI", 12, "bold")).pack(side="left")
            tk.Label(hdr, text="All checks passed", bg=PNL, fg=FG2,
                     font=("Segoe UI", 9)).pack(side="right")

        # ── two-column body ────────────────────────────────────────────────────
        body = tk.Frame(top, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=8)

        # Left: stick figure diagram
        fig_frame = tk.Frame(body, bg=SFC, highlightthickness=1,
                             highlightbackground=FG3)
        fig_frame.pack(side="left", fill="both", expand=True, padx=(0, 8))

        stick_cv = tk.Canvas(fig_frame, bg=SFC, highlightthickness=0,
                             width=260, height=360)
        stick_cv.pack(fill="both", expand=True, padx=6, pady=6)
        tk.Label(fig_frame, text="REQUIRED FIELD OF VIEW",
                 bg=SFC, fg=FG3, font=("Segoe UI", 7, "bold")).pack(pady=(0, 4))

        def _draw_figure(cv, w, h, highlighted):
            cv.delete("all")
            # Background label
            cv.create_text(w//2, 10, text="Full leg must be in frame",
                           fill=FG3, font=("Segoe UI", 7), anchor="n")

            # Table
            ty = int(h * 0.30)
            cv.create_rectangle(20, ty, w-20, ty+10, fill="#1E4060", outline="")
            cv.create_text(w-22, ty + 5, text="table", fill=FG3,
                           font=("Segoe UI", 7), anchor="e")

            # Body on table (torso)
            bx1, bx2 = int(w*0.18), int(w*0.62)
            cv.create_line(bx1, ty, bx2, ty, fill=FG, width=3)

            # Head
            cv.create_oval(bx1-22, ty-20, bx1+2, ty+4, fill=FG3, outline="")

            # Hip joint
            hx, hy = bx2, ty
            hip_col = ERR if "full_leg" in highlighted else FG2
            cv.create_oval(hx-7, hy-7, hx+7, hy+7, fill=hip_col, outline="")
            cv.create_text(hx+10, hy, text="Hip", fill=FG2,
                           font=("Segoe UI", 7), anchor="w")

            # Thigh (angled slightly down)
            kx, ky = int(w*0.62), int(h*0.55)
            knee_col = ERR if "knee" in highlighted else OK
            cv.create_line(hx, hy, kx, ky, fill=knee_col, width=3)
            cv.create_oval(kx-8, ky-8, kx+8, ky+8, fill=knee_col, outline="")
            cv.create_text(kx+12, ky, text="Knee", fill=knee_col,
                           font=("Segoe UI", 8, "bold"), anchor="w")

            # Lower leg (hanging down toward ankle)
            ax_, ay_ = int(w*0.58), int(h*0.82)
            ank_col = ERR if "ankle" in highlighted else OK
            cv.create_line(kx, ky, ax_, ay_, fill=ank_col, width=3)
            cv.create_oval(ax_-7, ay_-7, ax_+7, ay_+7, fill=ank_col, outline="")
            cv.create_text(ax_+12, ay_, text="Ankle", fill=ank_col,
                           font=("Segoe UI", 8, "bold"), anchor="w")

            # Foot (short line)
            cv.create_line(ax_-12, ay_, ax_+8, ay_+14, fill=FG3, width=2)

            # Camera frame rectangle
            cam_col = ERR if highlighted else PURP
            margin = 0.07
            fx1 = int(w * margin); fy1 = int(h * 0.12)
            fx2 = int(w * (1-margin)); fy2 = int(h * 0.93)
            cv.create_rectangle(fx1, fy1, fx2, fy2,
                                 outline=cam_col, width=2, dash=(6, 3))
            cv.create_text(w//2, fy1 - 4, text="Camera frame",
                           fill=cam_col, font=("Segoe UI", 7), anchor="s")

            # Lighting indicator
            if "lighting" in highlighted:
                cv.create_text(w//2, h-12, text="⚠ Check lighting",
                               fill=WARN, font=("Segoe UI", 8, "bold"), anchor="s")

        highlighted_regions = {e["region"] for e in errors if e.get("region")}
        stick_cv.bind("<Configure>",
            lambda e: _draw_figure(stick_cv, e.width, e.height, highlighted_regions))
        # Initial draw after a short delay
        top.after(60, lambda: _draw_figure(stick_cv,
                                            stick_cv.winfo_width() or 260,
                                            stick_cv.winfo_height() or 360,
                                            highlighted_regions))

        # Right: error list + setup checklist
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        scroll_outer = tk.Frame(right, bg=BG)
        scroll_outer.pack(fill="both", expand=True)

        if errors:
            tk.Label(scroll_outer, text="Issues detected:", bg=BG, fg=FG,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(0, 4))
            for err in errors:
                card = tk.Frame(scroll_outer, bg=SFC, pady=6, padx=8,
                                highlightthickness=1, highlightbackground=FG3)
                card.pack(fill="x", pady=3)
                tk.Label(card, text=f"⚠  {err['msg']}", bg=SFC, fg=ERR,
                         font=("Segoe UI", 9, "bold"), wraplength=280,
                         justify="left", anchor="w").pack(anchor="w")
                tk.Label(card, text=f"→ {err['tip']}", bg=SFC, fg=FG2,
                         font=("Segoe UI", 8), wraplength=280,
                         justify="left", anchor="w").pack(anchor="w", pady=(2, 0))

        # ── Setup Checklist (always shown) ────────────────────────────────────
        sep = (8, 4) if errors else (0, 4)
        tk.Label(scroll_outer, text="Setup Checklist:", bg=BG, fg=FG,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=sep)
        _TIPS = [
            ("Knee exposed",
             "Patient wears shorts or rolls up pants — no fabric covering the knee."),
            ("Camera position",
             "Place camera at knee height, 1.5–2.5 m to the patient's SIDE (sagittal view)."),
            ("Camera stability",
             "Mount on a tripod or stable surface — handheld wobble reduces accuracy."),
            ("Lighting",
             "Avoid placing the patient in front of a bright window; "
             "backlight silhouettes the leg."),
            ("Assessor position",
             "Assessor stands outside or behind the camera's field of view during the swing."),
        ]
        bp_card = tk.Frame(scroll_outer, bg=SFC, pady=6, padx=8,
                           highlightthickness=1, highlightbackground=FG3)
        bp_card.pack(fill="x", pady=(0, 3))
        for title, tip in _TIPS:
            row = tk.Frame(bp_card, bg=SFC)
            row.pack(fill="x", pady=2)
            tk.Label(row, text="✓", bg=SFC, fg=OK,
                     font=("Segoe UI", 9, "bold"), width=2).pack(
                         side="left", anchor="n")
            inner = tk.Frame(row, bg=SFC)
            inner.pack(side="left", fill="x", expand=True)
            tk.Label(inner, text=title, bg=SFC, fg=FG,
                     font=("Segoe UI", 8, "bold"),
                     anchor="w", justify="left").pack(anchor="w")
            tk.Label(inner, text=tip, bg=SFC, fg=FG2,
                     font=("Segoe UI", 8), wraplength=260,
                     anchor="w", justify="left").pack(anchor="w")

        # ── button row ─────────────────────────────────────────────────────────
        btn_row = tk.Frame(top, bg=PNL, pady=8, padx=12)
        btn_row.pack(fill="x", side="bottom")

        _bb = dict(relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                   font=("Segoe UI", 9, "bold"))

        def _retry():
            top.destroy()
            new_errors = self._validate_camera_setup()
            if new_errors:
                self._show_camera_guide(new_errors, pending_countdown)
            else:
                if pending_countdown > 0:
                    self._start_countdown(pending_countdown)
                else:
                    messagebox.showinfo("Setup OK",
                                        "Camera setup looks good!\nYou can now Record.",
                                        parent=self)

        def _record_anyway():
            top.destroy()
            if pending_countdown > 0:
                self._countdown_remaining = pending_countdown
                self._status.config(text=f"Recording starts in {pending_countdown}s…")
                self._countdown_after_id = self.after(
                    1000, lambda: self._countdown_step(pending_countdown - 1))
            else:
                self._cmd_rec_toggle()

        tk.Button(btn_row, text="↺ Retry", command=_retry,
                  bg="#1E5F3A", fg="#4ADE80", activebackground="#276048",
                  activeforeground="#FFFFFF", **_bb).pack(side="left", padx=4)
        tk.Button(btn_row, text="⚠ Record Anyway", command=_record_anyway,
                  bg="#5C1A1A", fg="#F87171", activebackground="#8B0000",
                  activeforeground="#FFFFFF", **_bb).pack(side="left", padx=4)
        tk.Button(btn_row, text="✕ Close", command=top.destroy,
                  bg=PNL, fg=FG2, activebackground=FG3,
                  activeforeground=FG, **_bb).pack(side="right", padx=4)

    # ── recording ─────────────────────────────────────────────────────────────

    def _rec_timer_seconds(self) -> float:
        """Auto-stop duration in seconds; 0 (or invalid) means manual stop."""
        try:
            return max(0.0, float(self._rec_timer_var.get().strip()))
        except (ValueError, AttributeError):
            return 0.0

    def _on_rec_timer_elapsed(self):
        """Fires when the configured recording duration is reached."""
        self._rec_stop_after_id = None
        if self._recording:
            self._status.config(
                text=f"Recording auto-stopped after {self._rec_timer_seconds():.0f} s.")
            self._cmd_rec_toggle()

    def _close_goniometer_csv(self):
        """Stop and close both goniometer streams. Safe to call when idle."""
        if self._goniometer_recording:
            self._goniometer_recording = False
            with self._goniometer_csv_lock:
                if self._goniometer_csv_file is not None:
                    try:
                        self._goniometer_csv_file.close()
                    except Exception:
                        pass
                    self._goniometer_csv_file   = None
                    self._goniometer_csv_writer = None
        if self._imu_recording:
            self._imu_recording = False
            if _IMU_AVAIL:
                try:
                    _imu.stop_recording()
                except Exception:
                    pass

    def _cmd_rec_toggle(self):
        if not self._live:
            return
        if not self._recording:
            # Start recording
            if not self.tracker.ready and not getattr(self, "_skip_marker_check", False):
                if not messagebox.askyesno("No markers set",
                    "Knee + Ankle markers not placed yet.\n"
                    "Recording will start without tracking.\n\nContinue?"):
                    return
            # Hold off until the phone clocks are aligned with this laptop's,
            # otherwise the IMU trace cannot be placed on the same timeline as
            # the video and the Motive take.
            if not self._sync_ready():
                _sy   = _imu.sync_status()
                _st   = _imu.get_state()
                _live = (_st["proximal"]["connected"] or _st["distal"]["connected"])
                if not _live:
                    _title = "No goniometer signal"
                    _body  = (
                        "'Sync Goniometer' is on but no phone is streaming, so "
                        "the goniometer CSV would be empty.\n\n"
                        f"Enter {_imu.get_local_ip()}:{_imu.PORT} in the Sensor "
                        "Stream app, or use 🛜 Can't connect? for help.\n\n"
                        "Record without the goniometer anyway?")
                    _short = "No goniometer signal — check the phones."
                else:
                    _title = "Time sync not ready"
                    _body  = (
                        f"iPhone IMU clock sync: {_sy['state']} — {_sy['detail']}.\n\n"
                        "Recording now means the IMU trace may not align with the "
                        "video and Motive take.\n\nStart anyway?")
                    _short = (f"Waiting for time sync — {_sy['detail']}. "
                              "Recording will align once synced.")
                if not messagebox.askyesno(_title, _body):
                    self._status.config(text=_short)
                    return
            # Consume the one-shot marker bypass only once every gate has
            # passed, so an abort here does not silently discard it.
            self._skip_marker_check = False
            part = self._get_participant()
            pid  = part["id"] if part else "Unknown"
            # Find or create participant folder: Participant_{pid}_{label}
            rec_root = os.path.join(BASE_DIR, "Recordings")
            os.makedirs(rec_root, exist_ok=True)
            part_folder = None
            for d in os.listdir(rec_root):
                if os.path.isdir(os.path.join(rec_root, d)) and \
                        d.lower().startswith(f"participant_{pid.lower()}"):
                    part_folder = d
                    break
            if part_folder is None:
                label = (part.get("diagnosis") or part.get("name") or "").lower().replace(" ", "_")
                part_folder = f"Participant_{pid}" + (f"_{label}" if label else "")
            pos = self._pos_var.get()
            rec_dir = os.path.join(rec_root, part_folder,
                                   f"Position_{pos}", "Height_Joint-Level")
            os.makedirs(rec_dir, exist_ok=True)
            # Trial number comes from the operator-editable field so the video,
            # goniometer CSV, IMU CSV and Motive take all share one identifier.
            trial_n = self._trial_number()
            import datetime
            self._rec_path = os.path.join(rec_dir, f"Trial_{trial_n}.mp4")
            if os.path.exists(self._rec_path):
                if not messagebox.askyesno(
                        "Overwrite trial?",
                        f"Trial_{trial_n}.mp4 already exists in\n{rec_dir}\n\n"
                        "Recording will overwrite it (and its goniometer/IMU "
                        "CSVs).\n\nContinue?"):
                    return
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._writer = cv2.VideoWriter(
                self._rec_path, fourcc, self.fps, (self._cam_w, self._cam_h))
            if not self._writer.isOpened():
                # Fallback: avc1 (H.264 on Windows)
                self._writer.release()
                fourcc = cv2.VideoWriter_fourcc(*"avc1")
                self._writer = cv2.VideoWriter(
                    self._rec_path, fourcc, self.fps, (self._cam_w, self._cam_h))
            if not self._writer.isOpened():
                messagebox.showerror("Recording error",
                                     f"Could not create output file:\n{self._rec_path}\n\n"
                                     "Check that the directory is writable and a video codec is available.")
                self._writer = None; return
            self._recording = True
            self._live_fi   = 0
            self._angles    = []
            self._trail     = []
            # Snapshot markers at the moment Record is pressed — these correspond
            # to the leg position in frame 0 of the recording and must be used
            # for ArcTracker init later, NOT any mid-recording adjustment.
            self._rec_start_hip   = tuple(self._hip_click)   if self._hip_click   else None
            self._rec_start_knee  = tuple(self._knee_click)  if self._knee_click  else None
            self._rec_start_ankle = tuple(self._ankle_click) if self._ankle_click else None
            self._line_plot.set_data([], [])
            self._plot_canvas.draw()
            for lbl in (self._stat_min, self._stat_rest, self._stat_amp):
                lbl.config(text="—")
            self._clear_pt_panel()
            self._btn_rec.config(text="✓ Done", bg="#8B0000", fg="#FFFFFF")
            self._status.config(
                text=f"● Recording → {os.path.basename(self._rec_path)}  "
                     "Adjust knee/ankle markers at any time. Click ✓ Done to analyze.")
            # Sync Motive if requested
            if self._var_motive.get():
                _start_msg = (
                    f"START|id={pid}|position={pos}|height=Viewer|"
                    f"trial={trial_n}|relpath=Participant_{pid}"
                )
                try:
                    import motive_sync as _ms
                    _ms.start_local_motive(_start_msg)
                    self._motive_active = True
                except Exception as _me:
                    messagebox.showwarning(
                        "Motive Sync",
                        "Camera is recording, but Motive could not be triggered:\n\n"
                        f"{type(_me).__name__}: {_me}\n\n"
                        "Check that Motive is open and motive_sync.py is present.")
            # Sync iPhone goniometer if requested — CSVs live alongside the
            # video, named to match the trial. All three modalities (video,
            # goniometer, Motive) start within the same tick and every CSV row
            # carries a time.time() epoch stamp, the same base
            # motive_mobile_sync.py writes, so the traces align afterwards.
            if self._var_goniometer.get():
                gon_path = os.path.join(rec_dir, f"Trial_{trial_n}_goniometer.csv")
                try:
                    with self._goniometer_csv_lock:
                        self._goniometer_csv_file   = open(gon_path, mode="w", newline="")
                        self._goniometer_csv_writer = csv.writer(self._goniometer_csv_file)
                        self._goniometer_csv_writer.writerow(["timestamp", "angle_deg"])
                    self._goniometer_csv_path  = gon_path
                    self._goniometer_recording = True
                except Exception as _ge:
                    messagebox.showwarning(
                        "Goniometer Sync",
                        "Camera is recording, but the goniometer CSV could not be opened:\n\n"
                        f"{type(_ge).__name__}: {_ge}")

                if _IMU_AVAIL:
                    imu_path = os.path.join(rec_dir, f"Trial_{trial_n}_imu.csv")
                    _meta = {
                        "participant": pid,
                        "leg":         self._leg_var.get(),
                        "position":    pos,
                        "trial":       trial_n,
                        "segments":    self._imu_segpair_var.get(),
                        # Record the modelling assumption alongside the data:
                        # a single-IMU trace is only a knee angle if the thigh
                        # really was stationary for the whole trial.
                        "imu_mode":    ("single-distal (thigh assumed stationary)"
                                        if self._imu_single_mode()
                                        else "two-segment relative"),
                        "video":       os.path.basename(self._rec_path),
                        "video_fps":   f"{self.fps:.3f}",
                        "t0_epoch":    f"{time.time():.4f}",
                        "motive_synced": int(bool(self._var_motive.get())),
                    }
                    if _imu.start_recording(imu_path, _meta):
                        self._imu_csv_path  = imu_path
                        self._imu_recording = True

            # Arm the auto-stop timer if a duration was set. This epoch is also
            # the video's t=0, used to place the IMU trace on the same axis.
            self._rec_started_at = time.time()
            _dur = self._rec_timer_seconds()
            if _dur > 0:
                self._rec_stop_after_id = self.after(
                    int(_dur * 1000), self._on_rec_timer_elapsed)
        else:
            # Stop recording — auto-analyze
            self._recording = False
            if self._writer:
                self._writer.release()
                self._writer = None
            if self._motive_active:
                try:
                    import motive_sync as _ms
                    _ms.stop_local_motive()
                except Exception:
                    pass
                self._motive_active = False
            if self._rec_stop_after_id is not None:
                self.after_cancel(self._rec_stop_after_id)
                self._rec_stop_after_id = None
            self._rec_timer_lbl.config(text="")
            self._close_goniometer_csv()
            self._btn_rec.config(text="⏺ Record", bg="#5C1A1A", fg="#F8FAFC")
            n     = self._live_fi
            valid = sum(1 for a in self._angles if math.isfinite(a))
            self._status.config(
                text=f"Recording saved ({n} frames, {valid} tracked). Running analysis…")
            if self._rec_path and os.path.exists(self._rec_path):
                self.after(200, self._finish_live_recording)

    def _finish_live_recording(self):
        """
        Called after recording stops: switch to file-review mode then auto-run
        the batch ArcTracker so the clinician gets a fully analyzed result with
        PT score and graph without any extra clicks.
        """
        saved_angles = list(self._angles)
        # Use the marker positions snapshotted at Record-press time, not any
        # mid-recording adjustment (those correspond to a mid-swing frame and
        # would corrupt the ArcTracker initialization at frame 0).
        init_hip   = getattr(self, "_rec_start_hip",   self._hip_click)
        init_knee  = getattr(self, "_rec_start_knee",  self._knee_click)
        init_ankle = getattr(self, "_rec_start_ankle", self._ankle_click)
        self._switch_to_file_review(saved_angles, init_knee, init_ankle, init_hip)
        # Give the UI one frame to settle, then kick off Track All automatically
        self.after(300, self._auto_track_after_rec)

    def _auto_track_after_rec(self):
        """Run Track All silently after a live recording finishes."""
        if self.cap is None or not self.tracker.ready:
            self._status.config(
                text="Video loaded. Place Knee + Ankle markers, then click ▶▶ Track All.")
            return
        self._status.config(text="Analyzing recording — please wait…")
        self._cmd_track_all()

    def _switch_to_file_review(self, saved_angles: Optional[list] = None,
                                init_knee=None, init_ankle=None, init_hip=None):
        path = self._rec_path
        if saved_angles is None:
            saved_angles = list(self._angles)
        if not path or not os.path.exists(path):
            return
        self._exit_camera_mode()
        if self.cap:
            self.cap.release(); self.cap = None
        # Keep current display markers for the overlay (most recent adjustment)
        # but use init_* positions (snapshotted at Record-press) for ArcTracker.
        display_knee  = self._knee_click
        display_ankle = self._ankle_click
        display_hip   = self._hip_click
        arc_knee  = init_knee  or display_knee
        arc_ankle = init_ankle or display_ankle
        arc_hip   = init_hip   or display_hip
        self._hip_click = self._knee_click = self._ankle_click = None
        self.tracker.reset()
        self._open_video(path)
        # Restore display markers so the video overlay draws correctly
        self._knee_click  = display_knee
        self._ankle_click = display_ankle
        self._hip_click   = display_hip
        # Initialise tracker with Record-start markers so batch ArcTracker
        # sees the correct ankle position that matches frame 0 of the video.
        if arc_knee and arc_ankle:
            frame0 = self._get_frame(0)
            if frame0 is not None:
                hip_pt = arc_hip if arc_hip else self._auto_hip(
                    np.array(arc_knee), np.array(arc_ankle))
                self.tracker.init(frame0, hip_pt, arc_knee, arc_ankle)
        # Restore live-tracked angles as a quick preview while batch re-runs
        if saved_angles and len(saved_angles) == self.total_frames:
            self._angles = saved_angles
            self._update_plot(len(saved_angles) - 1)
        self._status.config(text="Analyzing…")

    def _cmd_reset_trim(self):
        """Reset both trim handles to the full video range."""
        if self.cap is None or self.total_frames == 0:
            messagebox.showinfo("No video", "Open a video first.")
            return
        self._trim_start = 0
        self._trim_end   = self.total_frames
        self._tl_draw()
        self._status.config(text="Trim reset — drag the blue handles on the timeline to set a range.")

    def _exit_camera_mode(self):
        """Restore transport controls and clear camera state. Safe to call even if not in camera mode."""
        if not self._live:
            return
        self._live = False
        self._recording = False
        self._cancel_countdown()
        self._skip_marker_check = False
        if self._writer:
            self._writer.release()
            self._writer = None
        if self._motive_active:
            try:
                import motive_sync as _ms
                _ms.stop_local_motive()
            except Exception:
                pass
            self._motive_active = False
        if self._rec_stop_after_id is not None:
            self.after_cancel(self._rec_stop_after_id)
            self._rec_stop_after_id = None
        if hasattr(self, "_rec_timer_lbl"):
            self._rec_timer_lbl.config(text="")
        self._close_goniometer_csv()
        if self._phone_mode and _PPS_AVAIL:
            try:
                _pps.stop()
            except Exception:
                pass
            self._phone_mode = False
        # Swap controls back: hide Rec + cam controls, show transport buttons
        self._btn_rec.pack_forget()
        self._cam_ctrl.pack_forget()
        for b in (self._btn_go_first, self._btn_go_prev,
                  self._btn_play, self._btn_go_next, self._btn_go_last):
            b.pack(side="left", padx=2)

    def _cmd_open(self):
        path = filedialog.askopenfilename(
            title="Open video",
            filetypes=[("Video", "*.avi *.mp4 *.mov *.mkv"), ("All", "*.*")],
            initialdir=os.path.join(BASE_DIR, "Recordings"),
        )
        if not path:
            return
        self._exit_camera_mode()   # restore transport if coming from camera mode
        # Drop any IMU trace from a previous recording, then adopt this video's
        # own sidecar CSV if one was recorded alongside it.
        self._clear_imu_overlay()
        self._method_series.clear()
        self._opti_csv_path = ""
        self._shown_method = "rgb"
        self._method_var.set("RGB / HPE")
        self._refresh_overlay()
        _sidecar = os.path.splitext(path)[0] + "_imu.csv"
        self._imu_csv_path = _sidecar if os.path.exists(_sidecar) else ""
        # This video was not recorded in this session, so the live start epoch
        # no longer applies — fall back to the t0_epoch stored in the CSV.
        self._rec_started_at = 0.0
        self._playing = False
        self._btn_play.config(text="▶")
        self.tracker.reset()
        self._hip_click = self._knee_click = self._ankle_click = None
        self._open_video(path)

    def _cmd_detect(self):
        if self.cap is None:
            messagebox.showinfo("No video", "Open a video first.")
            return
        self._status.config(text="Running YOLO patient detection…")
        frame = self._get_frame(0)
        if frame is None:
            return

        def _run():
            p_kps, a_kps = self.detector.detect(frame)
            self.after(0, lambda: self._apply_detection(frame, p_kps, a_kps))

        threading.Thread(target=_run, daemon=True).start()

    def _apply_detection(self, frame, p_kps, a_kps):
        if p_kps is None:
            self._status.config(text="Patient not found automatically. Place Knee + Ankle markers manually.")
            return
        # Pick the leg that is more visible (further from origin)
        l_kn, r_kn = p_kps[13], p_kps[14]
        l_an, r_an = p_kps[15], p_kps[16]
        use_left   = np.linalg.norm(l_kn) + np.linalg.norm(l_an) > np.linalg.norm(r_kn) + np.linalg.norm(r_an)
        kne = (l_kn if use_left else r_kn).astype(np.float32)
        ank = (l_an if use_left else r_an).astype(np.float32)
        hip_mid = ((p_kps[11] + p_kps[12]) / 2).astype(np.float32)

        self._hip_click, self._knee_click, self._ankle_click = tuple(hip_mid), tuple(kne), tuple(ank)
        self.tracker.init(frame, hip_mid, kne, ank)
        _, _, _, ang0 = self.tracker.step(frame)
        self._angles[0] = ang0
        self._frame_idx = 0
        self._show_frame_idx(0)
        self._status.config(
            text=f"Patient detected. thigh={self.tracker.thigh_len:.0f}px  "
                 f"shank={self.tracker.shank_len:.0f}px  →  Press ▶ or Track All.")

    def _cmd_pick_person(self):
        """Run MediaPipe IMAGE-mode on the current frame, show all detected persons
        with numbered colored skeletons, then let the user click the patient.
        This makes it easy to see which person MP is finding and pick the right one."""
        if self.cap is None:
            messagebox.showinfo("No video", "Open a video first.")
            return
        frame = self._get_frame(self._frame_idx)
        if frame is None:
            return
        self._status.config(text="Running MediaPipe detection…")
        self.update_idletasks()

        try:
            import mediapipe as mp
            V = mp.tasks.vision
            opts = V.PoseLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_path=_MP_MODEL),
                running_mode=V.RunningMode.IMAGE,
                num_poses=4,
                min_pose_detection_confidence=0.25,
                min_pose_presence_confidence=0.25,
            )
            with V.PoseLandmarker.create_from_options(opts) as detector:
                rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
            self._person_select_poses = result.pose_landmarks or []
        except Exception as e:
            self._status.config(text=f"MediaPipe detection failed: {e}")
            return

        n = len(self._person_select_poses)
        if n == 0:
            self._status.config(
                text="MediaPipe found 0 people in this frame. Try a different frame.")
            return

        self._person_select_active = True
        self._show_frame_idx(self._frame_idx)
        self._status.config(
            text=f"MediaPipe found {n} person(s) shown with colored numbers. "
                 f"Click anywhere on the PATIENT to select them.")

    def _on_person_select_click(self, nx: float, ny: float):
        """Handle a click during person-select mode.

        Finds the detected person nearest to the click (checking all landmarks),
        picks whichever leg is more visible, sets hip/knee/ankle, and inits tracker.
        """
        poses = self._person_select_poses
        if not poses:
            self._person_select_active = False
            return

        frame = self._get_frame(self._frame_idx)
        if frame is None:
            return
        fh, fw = frame.shape[:2]

        result = resolve_person_click(poses, (nx, ny), fw, fh, self._leg_var.get())
        if result is None:
            return

        hip, kne, ank = result

        if ank is None:
            self._status.config(
                text="Ankle visibility too low — "
                     "place Knee marker then click Ankle manually.")
            # Still place the knee so the user has a head start
            self._knee_click = tuple(float(v) for v in kne)
            self._person_select_active = False
            self._person_select_poses  = []
            self._show_frame_idx(self._frame_idx)
            return

        self._hip_click   = tuple(float(v) for v in hip)
        self._knee_click  = tuple(float(v) for v in kne)
        self._ankle_click = tuple(float(v) for v in ank)

        self.tracker.init(frame, hip, kne, ank)
        _, _, _, ang0 = self.tracker.step(frame)
        self._angles[self._frame_idx] = ang0

        # Exit person-select mode
        self._person_select_active = False
        self._person_select_poses  = []

        side = self._leg_var.get().capitalize()
        self._show_frame_idx(self._frame_idx)
        self._status.config(
            text=f"{side} leg selected — shank={self.tracker.shank_len:.0f}px  "
                 f"angle={ang0:.1f} deg  →  Press Track All.")

    def _cmd_knee(self):
        self._mode = "knee"
        self._hl_btn(self._btn_knee)
        self._status.config(text="Click on the patient's KNEE PIVOT in the video.")

    def _cmd_ankle(self):
        self._mode = "ankle"
        self._hl_btn(self._btn_ankle)
        self._status.config(text="Click on the patient's ANKLE (end of shank) in the video.")

    def _cmd_hip(self):
        self._mode = "hip"
        self._hl_btn(self._btn_hip)
        self._status.config(text="Click on the patient's HIP in the video. (Optional — auto-estimated from knee+ankle.)")

    def _cmd_mark_release(self):
        """Mark the current frame as the moment the assessor releases the leg.

        All frames before this frame are frozen at the initial hold angle after
        Track All runs, eliminating the jitter that occurs while the leg is being
        held stationary by the assessor.  Click again on the same frame to clear.
        """
        if self.cap is None:
            return
        fi = self._frame_idx
        if self._release_fi == fi:
            self._release_fi = None
            self._btn_release.config(relief="raised", bg="")
            self._status.config(
                text="Release marker cleared.  Track All will use full video range.")
        else:
            self._release_fi = fi
            self._btn_release.config(relief="sunken")
            t = fi / max(self.fps, 1.0)
            self._status.config(
                text=f"Release marked at frame {fi} ({t:.2f} s).  "
                     "Run Track All — frames before release will be frozen at hold angle.")
        self._show_frame_idx(fi)

    def _cmd_track_all(self):
        if self.cap is None or not self.tracker.ready:
            messagebox.showinfo("Set markers", "Place Knee + Ankle markers first.")
            return
        self._playing = False
        self._btn_play.config(text="▶")
        # Full re-track — old pins are now stale (new initial position may differ)
        self._ankle_pins = {}
        self._status.config(text="Tracking all frames in background…")
        hip0, kne0, ank0 = self.tracker.hip0.copy(), self.tracker.knee0.copy(), self.tracker.ankle0.copy()
        path = self.video_path

        t_start    = self._trim_start
        t_end      = self._trim_end_eff()
        release_fi = self._release_fi   # may be None
        # Always start from the beginning of the trim window.
        # Using self._frame_idx caused 0-frame runs when the user was scrubbed
        # to the last frame, producing 1/N valid frames and no PT score.
        init_fi = t_start

        def _run():
            # Pre-fill the full angles / trail arrays with NaN / None so any
            # frame before init_fi (where no tracking ran) is safely absent.
            angles = [float("nan")] * self.total_frames
            trail  = [None]         * self.total_frames

            cap2 = cv2.VideoCapture(path)
            # Init tracker on the exact frame where the user placed their marker.
            cap2.set(cv2.CAP_PROP_POS_FRAMES, init_fi)
            ok, f_init = cap2.read()
            if not ok:
                cap2.release(); return
            _side = getattr(self.tracker, '_side', 'right')
            t2 = _MPBatchTracker(_side, fps=self.fps) if _MP_AVAIL else _ArcTracker()
            t2.init(f_init, hip0, kne0, ank0)
            # Person-identity gate: use live tracker's anchor x-fraction if available,
            # but always pin self.knee to the user-placed kne0 — if init() snapped
            # self.knee to a wrong MP detection, that wrong position would persist
            # through every subsequent step() call and track the assessor instead.
            if hasattr(self.tracker, '_anchor_xfrac') and self.tracker._anchor_xfrac is not None:
                t2._anchor_xfrac = self.tracker._anchor_xfrac
            if hasattr(t2, 'knee'):
                t2.knee = kne0.astype(np.float32)
            # Copy any ankle templates learnt from previous corrections so this
            # fresh Track All run still benefits from the user's last retrack fix.
            if hasattr(t2, '_correct_template'):
                t2._correct_template = self._ankle_correct_template
                t2._wrong_template   = self._ankle_wrong_template
            hold_ang = _angle_deg(hip0, kne0, ank0)   # initial hold angle
            angles[init_fi]  = hold_ang
            trail[init_fi]   = ank0.copy()
            _last_ank        = ank0
            _last_good_theta = t2._theta
            _last_good_vel   = 0.0
            _last_good_ang   = hold_ang
            _n_autocorr      = [0]
            n_frames = t_end - init_fi

            # Determine the first frame that actually needs live tracking.
            # If the user marked a release frame, all frames from init_fi up to
            # (but not including) release_fi are frozen at the hold angle so
            # assessor-hand jitter is eliminated entirely.
            live_start = release_fi if (release_fi is not None and release_fi > init_fi) \
                         else init_fi + 1

            # Fill pre-release frames with the frozen hold angle.
            for fi in range(init_fi + 1, live_start):
                angles[fi] = hold_ang
                trail[fi]  = ank0.copy()

            # If a release was marked and is within the trim window, seek cap2
            # to that frame and re-initialise the tracker so it starts with
            # full motion-onset information from the actual release instant.
            if release_fi is not None and live_start > init_fi + 1:
                cap2.set(cv2.CAP_PROP_POS_FRAMES, live_start)
                ok, f_rel = cap2.read()
                if ok:
                    t2 = _MPBatchTracker(_side, fps=self.fps) if _MP_AVAIL else _ArcTracker()
                    t2.init(f_rel, hip0, kne0, ank0)
                    angles[live_start] = hold_ang
                    trail[live_start]  = ank0.copy()

            # Track forward from live_start+1 — cap2 is positioned there.
            for fi in range(live_start + 1, t_end):
                ok, fr = cap2.read()
                if not ok: break
                _, _, ank, ang = t2.step(fr)
                # Physics rollback: ankle above knee → restore last valid state.
                if math.sin(t2._theta) < -0.10:
                    t2._theta = _last_good_theta
                    t2._vel   = _last_good_vel * 0.5
                    ang       = _last_good_ang
                    ank       = (kne0 + np.array([
                        math.cos(_last_good_theta) * t2.radius,
                        math.sin(_last_good_theta) * t2.radius
                    ])).astype(np.float32)
                    _n_autocorr[0] += 1
                else:
                    _last_good_theta = t2._theta
                    _last_good_vel   = t2._vel
                    _last_good_ang   = ang
                angles[fi] = ang
                if ank is not None:
                    trail[fi] = ank.copy()
                    _last_ank = ank
                else:
                    trail[fi] = _last_ank.copy()
                if fi % 30 == 0:
                    pct = int((fi - init_fi) / max(1, n_frames) * 100)
                    self.after(0, lambda p=pct: self._status.config(text=f"Tracking… {p}%"))
            cap2.release()
            self.after(0, lambda n=_n_autocorr[0]: self._on_track_done(angles, trail, n))

        threading.Thread(target=_run, daemon=True).start()

    def _cmd_retrack_from_here(self):
        """Re-run tracking forward from the current frame.

        When multiple ankle pins exist (current frame + any pinned later frames):
          • Arc-angle INTERPOLATES between consecutive pins — exact, no tracker
            drift possible between two user-confirmed positions.
          • Runs _ArcTracker only BEYOND the last pin, where there is no
            reference yet.

        Single pin (or no subsequent pins): behaves as before — tracker
        re-initialises at the pin and runs forward.
        """
        if self.cap is None or not self.tracker.ready or self._live:
            return
        self._playing = False
        self._btn_play.config(text="▶")
        self._btn_retrack.config(state="disabled")

        hip0 = self.tracker.hip0.copy()
        kne0 = self.tracker.knee0.copy()

        # Fixed arc radius: the shank length never changes.
        sl = float(self.tracker.shank_len) if getattr(self.tracker, 'shank_len', 0) > 0 \
             else float(np.linalg.norm(self.tracker.ankle0 - kne0))

        path    = self.video_path
        n_total = self.total_frames

        # Build the full ordered pin list from ALL stored pins.
        # Starting from the EARLIEST pin means every user correction is honoured;
        # previously, pins before the current frame were silently discarded.
        if self._ankle_pins:
            all_pins: dict = {fi: np.array(pos, dtype=np.float64)
                              for fi, pos in self._ankle_pins.items()}
            start_fi = min(all_pins)
        else:
            # No explicit pins — retrack from current frame using trail / tracker initial pos
            start_fi = self._frame_idx
            if hasattr(self, '_trail') and start_fi < len(self._trail) \
                    and self._trail[start_fi] is not None:
                ank0 = np.array(self._trail[start_fi], dtype=np.float64)
            else:
                ank0 = self.tracker.ankle0.astype(np.float64)
            all_pins = {start_fi: ank0}

        pins_sorted = sorted(all_pins.items())   # [(fi, ank), …]
        # ank0 is the starting ankle (earliest pin)
        ank0 = pins_sorted[0][1]

        n_segs = len(pins_sorted) - 1
        if n_segs:
            self._status.config(
                text=f"Retracking from frame {start_fi}… "
                     f"interpolating {n_segs} pin segment{'s' if n_segs != 1 else ''}, "
                     "then tracking to end.")
        else:
            self._status.config(text=f"Retracking from frame {start_fi}…")

        def _run():
            new_angles: dict = {}
            new_trail:  dict = {}

            # ── helpers ────────────────────────────────────────────────────
            def _arc_theta(ank_v):
                v = np.asarray(ank_v, dtype=float) - kne0
                return math.atan2(float(v[1]), float(v[0]))

            def _arc_pos(theta):
                return (kne0 + np.array([math.cos(theta),
                                          math.sin(theta)]) * sl)

            # ── Phase 1: arc-interpolate between consecutive pins ──────────
            # The ankle travels along a circle of fixed radius (sl) centred
            # on the knee.  Linear interpolation in arc-ANGLE space gives a
            # physically correct path with zero tracker drift.
            for seg_i in range(len(pins_sorted) - 1):
                fi_a, ank_a = pins_sorted[seg_i]
                fi_b, ank_b = pins_sorted[seg_i + 1]
                theta_a = _arc_theta(ank_a)
                theta_b = _arc_theta(ank_b)
                # Unwrap so we take the shorter arc (avoids ±180° wrap jump)
                while theta_b - theta_a >  math.pi: theta_b -= 2 * math.pi
                while theta_b - theta_a < -math.pi: theta_b += 2 * math.pi
                span = max(fi_b - fi_a, 1)
                for fi in range(fi_a, fi_b + 1):
                    t     = (fi - fi_a) / span
                    theta = theta_a + t * (theta_b - theta_a)
                    ank_t = _arc_pos(theta)
                    new_angles[fi] = _angle_deg(hip0, kne0, ank_t)
                    new_trail[fi]  = ank_t.astype(np.float32)

            # ── Phase 2: run tracker beyond the last pin ──────────────────
            last_fi, last_ank = pins_sorted[-1]
            _n_autocorr_r = [0]   # defined here so the lambda below always sees it
            cap2 = cv2.VideoCapture(path)
            cap2.set(cv2.CAP_PROP_POS_FRAMES, last_fi)
            ok, f0 = cap2.read()
            if ok:
                _side_r  = getattr(self.tracker, '_side', 'right')
                init_ank = _arc_pos(_arc_theta(last_ank))   # project to arc as start hint
                if _MP_AVAIL:
                    t2 = _MPBatchTracker(_side_r, fps=self.fps)
                    t2.init(f0, hip0, kne0, init_ank)
                    # Seed correction prior so the tracker stays near the user's
                    # pin direction for the first ~25 frames beyond the last pin.
                    t2.apply_correction(init_ank)
                    # For retrack: derive anchor fresh from the user-placed kne0 rather
                    # than copying the old anchor — if the previous run was tracking the
                    # wrong person, the old anchor perpetuates that error.
                    # Also force self.knee to kne0 so init()'s MP snap can't have
                    # silently locked the distance reference onto the assessor.
                    _fw = max(f0.shape[1], 1)
                    t2.knee          = kne0.astype(np.float32)
                    t2._anchor_xfrac = float(kne0[0]) / _fw
                    # ── Learn ankle appearance from the pin frame ──────────────
                    # Run MP again on this frame to find BOTH persons if present.
                    # Correct template = patch at the user-confirmed pin position.
                    # Wrong template   = any other detected person's ankle that is
                    # clearly far from the pin (the stationary assessor ankle).
                    _all_p = t2._run_mp(f0) or []
                    _PL_t  = t2._PL
                    if _PL_t is not None and _all_p:
                        _ai_t = (_PL_t.LEFT_ANKLE.value if _side_r == "left"
                                 else _PL_t.RIGHT_ANKLE.value)
                        _h_f, _w_f = f0.shape[:2]
                        _pin_pos = np.array(last_ank, dtype=np.float32)
                        _ranked = sorted(
                            _all_p,
                            key=lambda lm: float(np.linalg.norm(
                                np.array([lm[_ai_t].x * _w_f,
                                          lm[_ai_t].y * _h_f]) - _pin_pos)))
                        _correct_tmpl = _ankle_patch(f0, _pin_pos)
                        _wrong_tmpl   = None
                        for _lm in _ranked[1:]:
                            _a_wrong = np.array([_lm[_ai_t].x * _w_f,
                                                 _lm[_ai_t].y * _h_f])
                            if float(np.linalg.norm(_a_wrong - _pin_pos)) > _TMPL_SZ * 1.5:
                                _wrong_tmpl = _ankle_patch(f0, _a_wrong)
                                break
                        t2._correct_template = _correct_tmpl
                        t2._wrong_template   = _wrong_tmpl
                        self._ankle_correct_template = _correct_tmpl
                        self._ankle_wrong_template   = _wrong_tmpl
                    # MediaPipe redetects from scratch each frame — no cooldown needed
                else:
                    t2 = _ArcTracker()
                    t2.init(f0, hip0, kne0, init_ank)
                    # ArcTracker-specific priming: force motion mode and narrow window
                    t2._motion_detected  = True
                    t2._retrack_cooldown = _ArcTracker._RETRACK_COOLDOWN
                    # Seed velocity from Phase 1 arc-interpolated positions so the
                    # first post-pin frames track in the right direction.
                    _theta_pin = t2._theta
                    for _dfi in range(1, 6):
                        _pfi = last_fi - _dfi
                        if _pfi in new_trail and new_trail[_pfi] is not None:
                            _th_prev = math.atan2(
                                float(new_trail[_pfi][1] - kne0[1]),
                                float(new_trail[_pfi][0] - kne0[0]))
                            _dth = _theta_pin - _th_prev
                            while _dth >  math.pi: _dth -= 2 * math.pi
                            while _dth < -math.pi: _dth += 2 * math.pi
                            t2._vel = _dth / _dfi
                            break

                # Record the last-pin frame itself (already in new_angles via
                # Phase 1 if it's not the lone start pin, but write it anyway)
                new_angles[last_fi] = _angle_deg(hip0, kne0, last_ank)
                new_trail[last_fi]  = last_ank.astype(np.float32)
                _last_good_theta_r  = t2._theta
                _last_good_vel_r    = t2._vel
                _last_good_ang_r    = new_angles[last_fi]
                _last_valid_ank_r   = last_ank.astype(np.float32)
                _n_autocorr_r       = [0]
                for fi in range(last_fi + 1, n_total):
                    ok2, fr = cap2.read()
                    if not ok2: break
                    _, _, ank, ang = t2.step(fr)
                    # Physics rollback: ankle appeared above knee → restore state
                    if math.sin(t2._theta) < -0.10:
                        t2._theta = _last_good_theta_r
                        t2._vel   = _last_good_vel_r * 0.5
                        ang       = _last_good_ang_r
                        ank       = (kne0 + np.array([
                            math.cos(_last_good_theta_r) * t2.radius,
                            math.sin(_last_good_theta_r) * t2.radius
                        ])).astype(np.float32)
                        _n_autocorr_r[0] += 1
                    else:
                        _last_good_theta_r = t2._theta
                        _last_good_vel_r   = t2._vel
                        _last_good_ang_r   = ang
                        if ank is not None:
                            _last_valid_ank_r = ank.copy()
                    new_angles[fi] = ang
                    new_trail[fi]  = (ank.copy() if ank is not None
                                      else _last_valid_ank_r)
                    if fi % 30 == 0:
                        pct = int((fi - start_fi)
                                  / max(n_total - start_fi, 1) * 100)
                        self.after(0, lambda p=pct: self._status.config(
                            text=f"Retracking… {p}%"))
            cap2.release()
            self.after(0,
                lambda n=_n_autocorr_r[0]: self._on_retrack_done(
                    new_angles, new_trail, start_fi, n))

        threading.Thread(target=_run, daemon=True).start()

    def _on_retrack_done(self, new_angles: dict, new_trail: dict,
                         start_fi: int, n_autocorr: int = 0):
        for fi, ang in new_angles.items():
            if fi < len(self._angles):
                self._angles[fi] = ang
        # Keep _trail current so subsequent Retracks use fresh ankle positions
        if hasattr(self, '_trail'):
            for fi, ank in new_trail.items():
                if fi < len(self._trail):
                    self._trail[fi] = ank
        self._angles = self._postprocess_angles(self._angles)
        # Smoothing must never overwrite user-confirmed pin angles.
        # Restore exact angles for every pinned frame after the median pass.
        if self.tracker.ready:
            _h, _k = self.tracker.hip0, self.tracker.knee0
            for _pfi, _pank in self._ankle_pins.items():
                if _pfi < len(self._angles):
                    self._angles[_pfi] = _angle_deg(_h, _k, np.array(_pank, dtype=np.float64))
        # After successful retrack, persist the most recent confirmed ankle pin
        # into self.tracker so the next Track All run starts from the correct
        # position and shank length rather than the original (possibly wrong) one.
        if self._ankle_pins:
            _lp_fi  = max(self._ankle_pins.keys())
            _lp_ank = np.array(self._ankle_pins[_lp_fi], dtype=np.float32)
            self.tracker.ankle0    = _lp_ank
            self.tracker.shank_len = float(np.linalg.norm(_lp_ank - self.tracker.knee0))
        last_fi = max(new_angles.keys()) if new_angles else start_fi
        self._update_plot(last_fi)
        self._show_frame_idx(last_fi)
        self._btn_retrack.config(state="normal")
        self._compute_and_show_pt()
        valid = sum(1 for a in self._angles if math.isfinite(a))
        msg = f"Retrack done. {valid}/{len(self._angles)} valid frames."
        if n_autocorr:
            msg += f"  Auto-corrected {n_autocorr} invalid frames beyond your pin."
        msg += "  Scrub to check, or correct another section and Retrack again."
        self._status.config(text=msg)

    def _on_track_done(self, angles, trail, n_autocorr=0):
        angles = self._postprocess_angles(angles)
        pad = [float("nan")] * (self.total_frames - len(angles))
        self._angles = angles + pad
        self._trail  = trail
        valid = sum(1 for a in angles if math.isfinite(a))
        self._frame_idx = len(angles) - 1
        self._update_plot(len(angles) - 1)
        self._show_frame_idx(len(angles) - 1)
        self._btn_retrack.config(state="normal")
        self._compute_and_show_pt()
        msg = f"Done. {valid}/{len(angles)} valid frames."
        if n_autocorr:
            msg += f"  Auto-corrected {n_autocorr} physically invalid frames."
        msg += "  Scrub to a bad frame → click Ankle → Retrack → to correct it."
        # This tracking result is the RGB/HPE methodology.
        self._set_method_series("rgb", self._angles)
        self._shown_method = "rgb"
        self._method_var.set("RGB / HPE")
        # Adopt the IMU trace recorded alongside this video, if any.
        _vt0 = getattr(self, "_rec_started_at", None) or None
        if self._imu_csv_path and self._load_imu_series(self._imu_csv_path, _vt0):
            msg += "  iPhone IMU series loaded — switch with the Method dropdown."
            self._load_imu_overlay(self._imu_csv_path, _vt0)
        self._refresh_overlay()
        self._status.config(text=msg)

    # ── participant / session management ──────────────────────────────────────

    def _get_participant(self) -> Optional[dict]:
        # The combobox shows "<id>  ·  <diagnosis>"; match the id exactly.
        # Substring matching would resolve "P1" to "P10" and mis-file trials.
        pid = self._part_var.get().split("·")[0].strip()
        for p in self._db["participants"]:
            if p["id"] == pid:
                return p
        return None

    def _next_trial_number(self) -> int:
        """Lowest trial number not yet saved for the current participant+leg."""
        part = self._get_participant()
        if part is None:
            return 1
        leg  = self._leg_var.get()
        used = {s.get("trial") for s in self._db["sessions"]
                if s.get("participant") == part["id"] and s.get("leg") == leg}
        n = 1
        while n in used:
            n += 1
        return n

    def _refresh_trial_number(self):
        self._trial_var.set(str(self._next_trial_number()))

    def _trial_number(self) -> int:
        """The operator-entered trial number, falling back to the auto value."""
        try:
            return max(1, int(self._trial_var.get().strip()))
        except (ValueError, AttributeError):
            return self._next_trial_number()

    def _refresh_participant_list(self):
        def _disp(p):
            dx = p.get("diagnosis", "")
            return f"{p['id']}  ·  {dx}" if dx else p["id"]
        names = [_disp(p) for p in self._db["participants"]]
        self._part_combo["values"] = names
        if names and not self._part_var.get():
            self._part_combo.current(0)

    def _cmd_new_participant(self):
        # Pre-generate the default ID
        _existing_ids = [p["id"] for p in self._db["participants"]]
        _n = len(_existing_ids) + 1
        _default_pid = f"P{_n:03d}"
        while _default_pid in _existing_ids:
            _n += 1; _default_pid = f"P{_n:03d}"

        dlg = tk.Toplevel(self)
        dlg.title("New Participant")
        dlg.geometry("400x300")
        dlg.configure(bg="#FFFFFF")
        dlg.resizable(False, False)
        dlg.grab_set()

        _lf = dict(bg="#FFFFFF", fg="#475569", font=("Segoe UI", 8), anchor="w")
        _ef = dict(font=("Segoe UI", 10), bg="#F1F5F9", fg="#0F172A",
                   insertbackground="#0F172A", relief="flat", bd=6)

        form = tk.Frame(dlg, bg="#FFFFFF", padx=20, pady=16)
        form.pack(fill="both", expand=True)
        form.columnconfigure(0, weight=1)

        tk.Label(form, text="Participant ID", **_lf).grid(row=0, column=0, sticky="w", pady=(0, 2))
        id_var = tk.StringVar(value=_default_pid)
        id_ent = tk.Entry(form, textvariable=id_var, width=34, **_ef)
        id_ent.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        id_ent.focus()

        tk.Label(form, text="Display Name", **_lf).grid(row=2, column=0, sticky="w", pady=(0, 2))
        name_var = tk.StringVar()
        tk.Entry(form, textvariable=name_var, width=34, **_ef).grid(
            row=3, column=0, sticky="ew", pady=(0, 10))

        tk.Label(form, text="Diagnosis", **_lf).grid(row=4, column=0, sticky="w", pady=(0, 2))
        dx_var = tk.StringVar()
        tk.Entry(form, textvariable=dx_var, width=34, **_ef).grid(
            row=5, column=0, sticky="ew", pady=(0, 4))
        tk.Label(form, text="e.g. Multiple Sclerosis, Stroke, Parkinson's Disease…",
                 bg="#FFFFFF", fg="#94A3B8", font=("Segoe UI", 7)).grid(
                     row=6, column=0, sticky="w")

        err_lbl = tk.Label(form, text="", bg="#FFFFFF", fg="#DC2626",
                           font=("Segoe UI", 8))
        err_lbl.grid(row=7, column=0, sticky="w", pady=(6, 0))

        def _ok():
            pid  = id_var.get().strip().upper()
            name = name_var.get().strip()
            dx   = dx_var.get().strip()
            if not pid:
                err_lbl.config(text="Participant ID is required."); return
            if pid in [p["id"] for p in self._db["participants"]]:
                err_lbl.config(text=f"ID '{pid}' already exists."); return
            self._db["participants"].append({"id": pid, "name": name or pid, "diagnosis": dx})
            _save_db(self._db)
            self._refresh_participant_list()
            self._part_combo.current(len(self._db["participants"]) - 1)
            dlg.destroy()

        dlg.bind("<Return>", lambda e: _ok())
        btn_row = tk.Frame(dlg, bg="#FFFFFF", padx=20, pady=(0, 16))
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="Submit", command=_ok,
                  bg="#2563EB", fg="#FFFFFF", relief="flat", bd=0, padx=16, pady=6,
                  font=("Segoe UI", 9, "bold"), cursor="hand2",
                  activebackground="#1D4ED8").pack(side="left", padx=(0, 8))
        tk.Button(btn_row, text="Cancel", command=dlg.destroy,
                  bg="#E2E8F0", fg="#0F172A", relief="flat", bd=0, padx=12, pady=6,
                  font=("Segoe UI", 9), cursor="hand2").pack(side="left")

    def _cmd_save_trial(self):
        part_sel = self._part_var.get()
        if not part_sel:
            messagebox.showwarning("No Participant", "Select or create a participant first.")
            return
        if self._last_pt_params is None:
            messagebox.showwarning("No Data", "Run Track All first.")
            return

        part = self._get_participant()
        if part is None:
            return

        leg   = self._leg_var.get()
        _vp = self.video_path or self._rec_path or ""
        if _vp:
            try:
                video = os.path.relpath(_vp, BASE_DIR)   # relative to project dir
            except ValueError:
                video = _vp                              # different drive — keep absolute
        else:
            video = ""
        def _rel(p: str) -> str:
            if not p:
                return ""
            try:
                return os.path.relpath(p, BASE_DIR)
            except ValueError:
                return p
        gon_csv = _rel(self._goniometer_csv_path)
        imu_csv = _rel(self._imu_csv_path)
        trial_num = self._trial_number()

        now = datetime.datetime.now()
        pp  = self._last_pt_params
        sess = {
            "id":            f"{part['id']}_{leg[0].upper()}_{now.strftime('%Y%m%d_%H%M%S')}",
            "participant":   part["id"],
            "name":          part.get("name", part["id"]),
            "date":          now.strftime("%Y-%m-%d"),
            "time":          now.strftime("%H:%M:%S"),
            "leg":           leg,
            "trial":         trial_num,
            "video":         video,
            "goniometer_csv": gon_csv,
            "imu_csv":       imu_csv,
            "session":       self._session_var.get(),
            "method":        self._active_method(),
            "mas":           self._last_mas or "—",
            "pt_4":          round(float(self._last_pt_s), 4),
            "pt_6":          round(float(self._last_pt_f), 4),
            "R2n":           round(float(pp.get("R2n", 0)),           3),
            "N":             round(float(pp.get("N", 0)),             1),
            "phi_max_ratio": round(float(pp.get("phi_max_ratio", 0)), 3),
            "omega_max_n":   round(float(pp.get("omega_max_n", 0)),   3),
            "f":             round(float(pp.get("f", 0)),             3),
            "area_ratio":    round(float(pp.get("area_ratio", 0)),    3),
            "A0_deg":        round(float(pp.get("A0_deg", 0)),        1),
            "omega_peak_deg_s": round(float(pp.get("omega_peak_deg_s", 0)), 1),
        }
        self._db["sessions"].append(sess)
        _save_db(self._db)
        self._btn_save_trial.config(state="disabled")
        self._refresh_trial_number()
        self._status.config(
            text=f"Saved: {part.get('name', part['id'])} — {leg.capitalize()} leg — Trial {trial_num}  (MAS {sess['mas']})")

    def _cmd_history(self):
        _HistoryWindow(self, self._db)

    def _cmd_validate(self):
        live_angles = list(self._angles) if self._angles else None
        live_fps    = float(self.fps) if self.fps and self.fps > 0 else 30.0
        video_path  = getattr(self, "video_path", "") or ""
        live_label  = os.path.basename(video_path) if video_path else "current trial"
        ValidationWindow(self, live_angles=live_angles,
                         live_fps=live_fps, live_label=live_label)

    def _cmd_reset(self):
        self.tracker.reset()
        self._person_select_active = False
        self._person_select_poses  = []
        self._hip_click = self._knee_click = self._ankle_click = None
        self._trail  = []
        self._path_keyframes.clear()
        self._ankle_pins.clear()
        self._release_fi = None
        self._btn_release.config(relief="raised", bg="")
        self._btn_apply_path.config(state="disabled")
        self._btn_clear_path.config(state="disabled")
        if self._mode == "path":
            self._mode = "idle"
            self._hl_btn(None)
        self._line_plot.set_data([], [])
        self._plot_canvas.draw()
        for lbl in (self._stat_cur, self._stat_rest, self._stat_amp, self._stat_min):
            lbl.config(text="—")
        self._clear_pt_panel()
        if self._live:
            self._angles = []
            self._status.config(text="Reset. Place Knee + Ankle markers, then press ⏺ Rec.")
        else:
            self._angles = [float("nan")] * (self.total_frames or 0)
            self._playing = False
            self._btn_play.config(text="▶")
            self._btn_retrack.config(state="disabled")
            self._frame_idx = 0
            self._show_frame_idx(0)
            self._status.config(text="Reset. Place markers or run Detect Patient.")

    def _cmd_export_graph(self):
        src = self._rec_path if (self._live and self._rec_path) else self.video_path
        if not src:
            messagebox.showinfo("No video", "Open a video first.")
            return
        trial_name = os.path.splitext(os.path.basename(src))[0]
        part = self._get_participant()
        leg  = self._leg_var.get().capitalize()
        fname = (f"{part['id']}_{leg}_{trial_name}_graph.png"
                 if part else f"{trial_name}_graph.png")
        out = os.path.join(os.path.dirname(src), fname)
        self._fig.savefig(out, dpi=150, bbox_inches="tight",
                          facecolor=self._fig.get_facecolor())
        self._status.config(text=f"Graph saved → {fname}")
        messagebox.showinfo("Exported", f"Graph saved:\n{fname}")

    def _cmd_export_results(self):
        if self._last_pt_params is None:
            messagebox.showinfo("No results", "Run Track All first to compute PT score.")
            return
        src = self._rec_path if (self._live and self._rec_path) else self.video_path
        if not src:
            messagebox.showinfo("No video", "Open a video first.")
            return
        trial_name = os.path.splitext(os.path.basename(src))[0]
        part = self._get_participant()
        leg  = self._leg_var.get().capitalize()
        if part:
            default_name = f"{part['id']}_{leg}_{trial_name}_results.csv"
            pid, pname = part["id"], part.get("name", part["id"])
        else:
            default_name = f"{trial_name}_results.csv"
            pid, pname = "", ""
        out = filedialog.asksaveasfilename(
            title="Save PT Results CSV",
            initialdir=os.path.dirname(src),
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not out:
            return
        pp  = self._last_pt_params
        row = {
            "participant_id":   pid,
            "participant_name": pname,
            "leg":              leg,
            "trial":            trial_name,
            "MAS":              self._last_mas if self._last_mas is not None else "",
            "pt_4param":        round(self._last_pt_s, 4) if self._last_pt_s is not None else "",
            "pt_6param":        round(self._last_pt_f, 4) if self._last_pt_f is not None else "",
            "R2n":              round(float(pp.get("R2n",          float("nan"))), 4),
            "N":                round(float(pp.get("N",            float("nan"))), 4),
            "phi_max_ratio":    round(float(pp.get("phi_max_ratio",float("nan"))), 4),
            "omega_max_n":      round(float(pp.get("omega_max_n",  float("nan"))), 4),
            "f":                round(float(pp.get("f",            float("nan"))), 4),
            "area_ratio":       round(float(pp.get("area_ratio",   float("nan"))), 4),
        }
        fields = ["participant_id", "participant_name", "leg", "trial", "MAS",
                  "pt_4param", "pt_6param", "R2n", "N", "phi_max_ratio",
                  "omega_max_n", "f", "area_ratio"]
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerow(row)
        self._status.config(text=f"Results saved → {os.path.basename(out)}")
        messagebox.showinfo("Exported", f"Results saved:\n{out}")

    # ── annotated video export ────────────────────────────────────────────────

    def _cmd_export_annotated_video(self):
        """Render every tracked frame with the skeleton/angle overlay and save as video."""
        if self._live:
            messagebox.showinfo("Export Video", "Stop live camera before exporting.")
            return
        if self.cap is None or not self.tracker.ready:
            messagebox.showinfo("Export Video",
                                "Open a video and place markers first.")
            return
        valid = sum(1 for a in self._angles if math.isfinite(a))
        if valid == 0:
            messagebox.showinfo("Export Video",
                                "No tracking data yet — run Track All first.")
            return

        base, _ = os.path.splitext(self.video_path)
        default_name = os.path.basename(base) + "_annotated.mp4"
        out_path = filedialog.asksaveasfilename(
            title="Save Annotated Video",
            initialfile=default_name,
            initialdir=os.path.dirname(self.video_path),
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4"), ("AVI Video", "*.avi"),
                       ("All files", "*.*")],
        )
        if not out_path:
            return

        # Snapshot all mutable state so the worker thread needs no locks.
        snap = {
            "path":       self.video_path,
            "fps":        self.fps,
            "angles":     list(self._angles),
            "trail":      list(self._trail) if self._trail else [],
            "ankle_pins": dict(self._ankle_pins) if hasattr(self, "_ankle_pins") else {},
            "hip0":       self.tracker.hip0.copy(),
            "kne0":       self.tracker.knee0.copy(),
            "ank0":       self.tracker.ankle0.copy(),
            "shank_len":  self.tracker.shank_len,
            "t_start":    self._trim_start,
            "t_end":      self._trim_end_eff(),
            "n_total":    self.total_frames,
        }

        self._btn_export_vid.config(state="disabled")
        self._status.config(text="Exporting annotated video… 0%")
        threading.Thread(target=self._export_annotated_worker,
                         args=(snap, out_path), daemon=True).start()

    def _export_annotated_worker(self, snap: dict, out_path: str):
        angles    = snap["angles"]
        trail_src = snap["trail"]
        pins      = snap["ankle_pins"]
        hip0      = snap["hip0"]
        kne0      = snap["kne0"]
        ank0      = snap["ank0"]
        shank_len = snap["shank_len"]
        t_start   = snap["t_start"]
        t_end     = snap["t_end"]
        n_total   = snap["n_total"]
        fps       = snap["fps"]

        # Pre-compute ankle-reconstruction constants (same logic as _ankle_from_angle)
        th   = hip0 - kne0
        th_n = float(np.linalg.norm(th))
        if th_n > 1e-6:
            th_u     = th / th_n
            av       = ank0 - kne0
            av_n     = float(np.linalg.norm(av))
            av_u     = av / av_n if av_n > 1e-6 else th_u
            cross    = float(th_u[0] * av_u[1] - th_u[1] * av_u[0])
            rot_sign = 1.0 if cross >= 0 else -1.0
        else:
            th_u = None

        def _ank_from_ang(ang_deg):
            if th_u is None or not math.isfinite(ang_deg):
                return None
            alpha  = rot_sign * math.radians(ang_deg)
            ca, sa = math.cos(alpha), math.sin(alpha)
            su = np.array([ca * th_u[0] - sa * th_u[1],
                           sa * th_u[0] + ca * th_u[1]])
            return (kne0 + su * shank_len).astype(np.float32)

        indexed_trail = (len(trail_src) == n_total)

        cap2 = cv2.VideoCapture(snap["path"])
        if not cap2.isOpened():
            self.after(0, lambda: (
                self._btn_export_vid.config(state="normal"),
                self._status.config(text="Export failed: cannot re-open video file."),
                messagebox.showerror("Export failed",
                                     f"Could not open video for reading:\n{snap['path']}")
            ))
            return
        w = int(cap2.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap2.get(cv2.CAP_PROP_FRAME_HEIGHT))

        ext = os.path.splitext(out_path)[1].lower()
        if ext == ".avi":
            fourcc_candidates = [
                cv2.VideoWriter_fourcc(*"XVID"),
                cv2.VideoWriter_fourcc(*"MJPG"),
            ]
        else:
            fourcc_candidates = [
                cv2.VideoWriter_fourcc(*"avc1"),   # H.264 — best quality on Windows
                cv2.VideoWriter_fourcc(*"mp4v"),   # MPEG-4 fallback
                cv2.VideoWriter_fourcc(*"XVID"),   # last resort (may produce .avi in .mp4 container)
            ]

        writer = None
        for fc in fourcc_candidates:
            w_ = cv2.VideoWriter(out_path, fc, fps, (w, h))
            if w_.isOpened():
                writer = w_
                break
            w_.release()

        if writer is None:
            cap2.release()
            self.after(0, lambda: (
                self._btn_export_vid.config(state="normal"),
                self._status.config(text="Export failed: no usable video codec found."),
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

                # Resolve ankle position for this frame.
                # Priority: user-pinned position > indexed trail > angle reconstruction.
                if fi in pins:
                    ank = np.array(pins[fi], dtype=np.float32)
                elif indexed_trail and fi < len(trail_src) and trail_src[fi] is not None:
                    ank = np.array(trail_src[fi], dtype=np.float32)
                else:
                    ank = _ank_from_ang(ang)

                # Maintain rolling trail for the ankle arc visible in the overlay.
                if ank is not None:
                    rolling_trail.append(ank)
                    if len(rolling_trail) > TRAIL_LEN:
                        rolling_trail.pop(0)

                # Draw skeleton + arc overlay on every frame so the skeleton is always
                # visible.  Angle text and arc appear only when tracking data is valid.
                overlay = _draw(frame, hip0, kne0, ank, ang,
                                list(rolling_trail), scale=1.0)

                if math.isfinite(ang):
                    ang_txt = f"{ang:.1f} deg"
                    cv2.putText(overlay, ang_txt, (16, h - 18),
                                cv2.FONT_HERSHEY_DUPLEX, 1.1,
                                (0, 0, 0), 4, cv2.LINE_AA)
                    cv2.putText(overlay, ang_txt, (16, h - 18),
                                cv2.FONT_HERSHEY_DUPLEX, 1.1,
                                (80, 230, 140), 2, cv2.LINE_AA)

                t_txt = f"{fi / fps:.2f} s"
                cv2.putText(overlay, t_txt, (16, h - 52),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(overlay, t_txt, (16, h - 52),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                            (190, 190, 190), 1, cv2.LINE_AA)

                writer.write(overlay)

                if fi % 30 == 0:
                    pct = int(fi / max(n_total, 1) * 100)
                    self.after(0, lambda p=pct: self._status.config(
                        text=f"Exporting annotated video… {p}%"))

        except Exception as exc:
            cap2.release()
            writer.release()
            self.after(0, lambda e=str(exc): (
                self._btn_export_vid.config(state="normal"),
                self._status.config(text=f"Export error: {e}"),
                messagebox.showerror("Export error", f"An error occurred during export:\n{e}")
            ))
            return

        cap2.release()
        writer.release()
        self.after(0, lambda p=out_path: self._on_export_video_done(p))

    def _on_export_video_done(self, out_path: str):
        self._btn_export_vid.config(state="normal")
        name = os.path.basename(out_path)
        self._status.config(text=f"Annotated video saved: {name}")
        messagebox.showinfo("Export complete",
                            f"Annotated video saved:\n{out_path}")

    def _cmd_export_unified(self):
        """Write one CSV holding every loaded methodology on a shared clock.

        Rows are the video's frame grid, so all methodologies share an index
        and a `t_s`; `t_epoch` carries the absolute laptop-clock time when the
        recording start epoch is known (the base motive_mobile_sync.py and the
        IMU CSV also use), letting this file be joined against the raw
        per-modality captures."""
        loaded = {k: s for k, s in self._method_series.items() if s}
        if not loaded:
            messagebox.showinfo("Nothing to export",
                                "Run ▶▶ Track All, or load an IMU/OptiTrack "
                                "series first.")
            return

        src = self._rec_path if (self._live and self._rec_path) else self.video_path
        part = self._get_participant()
        pid  = part["id"] if part else "Unknown"
        leg  = self._leg_var.get()
        sess = self._session_var.get()
        trial = self._trial_number()
        default_name = f"{pid}_{leg}_{sess}_Trial_{trial}_unified.csv"
        out = filedialog.asksaveasfilename(
            title="Save unified multi-modal CSV",
            initialdir=os.path.dirname(src) if src else BASE_DIR,
            initialfile=default_name, defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not out:
            return

        n = max(len(s) for s in loaded.values())
        t0 = getattr(self, "_rec_started_at", 0.0) or 0.0
        keys = [k for k, _l, _c in self._METHODS if k in loaded]
        try:
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                for k, v in (
                        ("participant", pid), ("leg", leg), ("session", sess),
                        ("trial", trial), ("position", self._pos_var.get()),
                        ("fps", f"{self.fps:.3f}"),
                        ("video", os.path.basename(src) if src else ""),
                        ("imu_csv", os.path.basename(self._imu_csv_path)
                         if self._imu_csv_path else ""),
                        ("optitrack_csv", os.path.basename(self._opti_csv_path)
                         if self._opti_csv_path else ""),
                        ("t0_epoch", f"{t0:.4f}" if t0 else ""),
                        ("angle_convention", "interior degrees, 180 = extended"),
                        ("methodologies", "|".join(keys)),
                        ("exported_at", datetime.datetime.now().isoformat(
                            timespec="seconds")),
                ):
                    w.writerow([f"# {k}", v])
                w.writerow(["frame", "t_s", "t_epoch"] +
                           [f"{k}_deg" for k in keys])
                for i in range(n):
                    t_s = i / max(self.fps, 1.0)
                    # 6 dp: at 30 fps a 4-dp step (0.0333) accumulates visible
                    # drift against the epoch column over a long trial.
                    row = [i, f"{t_s:.6f}",
                           f"{t0 + t_s:.6f}" if t0 else ""]
                    for k in keys:
                        s = loaded[k]
                        v = s[i] if i < len(s) else float("nan")
                        row.append(f"{v:.4f}" if math.isfinite(v) else "")
                    w.writerow(row)
        except OSError as e:
            messagebox.showerror("Export failed", str(e))
            return
        self._status.config(
            text=f"Unified CSV saved ({sess}, {len(keys)} methodolog"
                 f"{'ies' if len(keys) != 1 else 'y'}): {os.path.basename(out)}")

    def _cmd_export(self):
        if self._live and not self._rec_path:
            messagebox.showinfo("No data", "Record a session first.")
            return
        if not self.video_path and not self._rec_path:
            return
        src = self._rec_path if (self._live and self._rec_path) else self.video_path
        trial_name = os.path.splitext(os.path.basename(src))[0]
        part = self._get_participant()
        leg  = self._leg_var.get().capitalize()
        if part is not None:
            default_name = f"{part['id']}_{leg}_{trial_name}.csv"
        else:
            default_name = f"{trial_name}.csv"
        out = filedialog.asksaveasfilename(
            title="Save Knee Angle CSV",
            initialdir=os.path.dirname(src),
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not out:
            return
        rows = []
        for fi, ang in enumerate(self._display_angles()):
            rows.append({
                "frame": fi,
                "time_sec": round(fi / self.fps, 6),
                "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan"),
            })
        fields = ["frame", "time_sec", "knee_angle_deg"]
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
        messagebox.showinfo("Exported", f"{valid}/{len(rows)} valid frames\n→ {os.path.basename(out)}")
        self._status.config(text=f"Exported {valid}/{len(rows)} → {os.path.basename(out)}")

    # ── model comparison dialog ───────────────────────────────────────────────

    def _cmd_compare_models(self):
        """Open a comparison dialog that overlays multiple angle sources."""
        dlg = tk.Toplevel(self)
        dlg.title("Angle Comparison — OptiTrack vs Models")
        dlg.geometry("1100x640")
        dlg.configure(bg="#F1F5F9")

        _BG  = "#F1F5F9"
        _SFC = "#FFFFFF"
        _FG  = "#0F172A"
        _FG2 = "#475569"
        _FG3 = "#94A3B8"
        _BTN = "#E2E8F0"
        _ACT = "#2563EB"
        _lf  = dict(bg=_BG, fg=_FG2, font=("Segoe UI", 8))

        # Colour cycle for up to 8 sources
        _COLORS = ["#2563EB", "#DC2626", "#16A34A", "#D97706",
                   "#9333EA", "#0891B2", "#BE185D", "#92400E"]

        # sources: list of {"label": str, "times": list[float], "angles": list[float], "color": str}
        sources: list = []

        # ── left panel: source list ───────────────────────────────────────────
        left = tk.Frame(dlg, bg=_BG, width=240)
        left.pack(side="left", fill="y", padx=(10, 0), pady=10)
        left.pack_propagate(False)

        tk.Label(left, text="Data Sources", bg=_BG, fg=_FG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        src_list_frame = tk.Frame(left, bg=_SFC, relief="flat", bd=1)
        src_list_frame.pack(fill="both", expand=True)

        def _refresh_src_list():
            for w in src_list_frame.winfo_children():
                w.destroy()
            for i, s in enumerate(sources):
                row = tk.Frame(src_list_frame, bg=_SFC)
                row.pack(fill="x", padx=6, pady=3)
                dot = tk.Canvas(row, width=10, height=10, bg=_SFC, highlightthickness=0)
                dot.create_oval(1, 1, 9, 9, fill=s["color"], outline="")
                dot.pack(side="left", padx=(0, 6))
                tk.Label(row, text=s["label"], bg=_SFC, fg=_FG,
                         font=("Segoe UI", 8), anchor="w").pack(side="left", fill="x", expand=True)
                tk.Button(row, text="✕", bg=_SFC, fg=_FG2, relief="flat", bd=0,
                          font=("Segoe UI", 8), cursor="hand2",
                          command=lambda idx=i: _remove_source(idx)).pack(side="right")

        def _remove_source(idx):
            sources.pop(idx)
            _refresh_src_list()
            _redraw()

        def _next_color():
            return _COLORS[len(sources) % len(_COLORS)]

        # ── auto-load current tracking ────────────────────────────────────────
        if self._angles and any(math.isfinite(a) for a in self._angles):
            _times  = [fi / max(self.fps, 1.0) for fi in range(len(self._angles))]
            _angles = list(self._display_angles())
            sources.append({"label": "Pendulastic Tracker",
                            "times": _times, "angles": _angles,
                            "color": _next_color()})

        def _load_csv():
            path = filedialog.askopenfilename(
                title="Load angle CSV",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialdir=os.path.join(BASE_DIR, "Recordings"),
                parent=dlg,
            )
            if not path:
                return
            try:
                with open(path, newline="", encoding="utf-8-sig") as fh:
                    reader = csv.DictReader(fh)
                    rows = list(reader)
                if not rows:
                    messagebox.showwarning("Empty file", "CSV has no data rows.", parent=dlg)
                    return
                headers = list(rows[0].keys())

                # ── auto-detect angle column ───────────────────────────────────
                _angle_keywords = ["angle", "knee", "deg"]
                angle_col = next(
                    (h for h in headers
                     if any(k in h.lower() for k in _angle_keywords)),
                    None
                )
                if angle_col is None:
                    # Ask user to pick
                    _pick_dlg = tk.Toplevel(dlg)
                    _pick_dlg.title("Choose angle column")
                    _pick_dlg.geometry("320x140")
                    _pick_dlg.configure(bg=_BG)
                    _pick_dlg.grab_set()
                    tk.Label(_pick_dlg, text="Select the angle column:", **_lf).pack(pady=(12, 4))
                    _col_var = tk.StringVar(value=headers[0])
                    ttk.Combobox(_pick_dlg, textvariable=_col_var, values=headers,
                                 state="readonly", width=30).pack()
                    def _confirm_col():
                        nonlocal angle_col
                        angle_col = _col_var.get()
                        _pick_dlg.destroy()
                    tk.Button(_pick_dlg, text="OK", command=_confirm_col,
                              bg=_ACT, fg=_SFC, relief="flat", bd=0,
                              padx=14, pady=5, font=("Segoe UI", 9),
                              cursor="hand2").pack(pady=10)
                    _pick_dlg.wait_window()
                    if angle_col is None:
                        return

                # ── auto-detect time column ────────────────────────────────────
                _time_keywords = ["time", "sec", "t_"]
                time_col = next(
                    (h for h in headers
                     if any(k in h.lower() for k in _time_keywords)),
                    None
                )

                times, angles_out = [], []
                for i, row in enumerate(rows):
                    try:
                        ang = float(row[angle_col])
                    except (ValueError, KeyError):
                        ang = float("nan")
                    if time_col:
                        try:
                            t = float(row[time_col])
                        except (ValueError, KeyError):
                            t = i / max(self.fps, 1.0)
                    else:
                        t = i / max(self.fps, 1.0)
                    times.append(t)
                    angles_out.append(ang)

                label = os.path.splitext(os.path.basename(path))[0]
                sources.append({"label": label, "times": times,
                                "angles": angles_out, "color": _next_color()})
                _refresh_src_list()
                _redraw()

            except Exception as exc:
                messagebox.showerror("Load failed", str(exc), parent=dlg)

        def _export_combined():
            if not sources:
                messagebox.showinfo("No data", "Load at least one source.", parent=dlg)
                return
            out = filedialog.asksaveasfilename(
                title="Export combined CSV",
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")],
                parent=dlg,
            )
            if not out:
                return
            # Build unified time axis from longest source
            max_len = max(len(s["times"]) for s in sources)
            with open(out, "w", newline="") as fh:
                cols = ["time_sec"] + [s["label"] for s in sources]
                w = csv.writer(fh)
                w.writerow(cols)
                for i in range(max_len):
                    t = sources[0]["times"][i] if i < len(sources[0]["times"]) else i / max(self.fps, 1.0)
                    row = [round(t, 6)]
                    for s in sources:
                        if i < len(s["angles"]):
                            v = s["angles"][i]
                            row.append(round(v, 4) if math.isfinite(v) else "")
                        else:
                            row.append("")
                    w.writerow(row)
            messagebox.showinfo("Exported", f"Saved → {os.path.basename(out)}", parent=dlg)

        btn_frame = tk.Frame(left, bg=_BG)
        btn_frame.pack(fill="x", pady=(8, 0))
        _bb = dict(bg=_BTN, fg=_FG, relief="flat", bd=0, padx=10, pady=5,
                   font=("Segoe UI", 9), cursor="hand2")
        tk.Button(btn_frame, text="+ Load CSV",    command=_load_csv,       **_bb).pack(fill="x", pady=2)
        tk.Button(btn_frame, text="💾 Export Combined", command=_export_combined, **_bb).pack(fill="x", pady=2)

        # ── right panel: matplotlib plot ───────────────────────────────────────
        right = tk.Frame(dlg, bg=_SFC)
        right.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        import matplotlib as _mpl
        _mpl.use("TkAgg")
        from matplotlib.figure import Figure as _Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as _FCA

        fig  = _Figure(figsize=(7, 4.5), dpi=96, facecolor=_SFC)
        ax   = fig.add_subplot(111)
        ax.set_facecolor(_SFC)
        ax.set_xlabel("Time (s)", color=_FG2, fontsize=9)
        ax.set_ylabel("Knee Angle (°)", color=_FG2, fontsize=9)
        ax.tick_params(colors=_FG2, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color("#CBD5E1")
        ax.grid(True, color="#E2E8F0", linewidth=0.7)
        canvas = _FCA(fig, master=right)
        canvas.get_tk_widget().pack(fill="both", expand=True)

        def _redraw():
            ax.clear()
            ax.set_facecolor(_SFC)
            ax.set_xlabel("Time (s)", color=_FG2, fontsize=9)
            ax.set_ylabel("Knee Angle (°)", color=_FG2, fontsize=9)
            ax.tick_params(colors=_FG2, labelsize=8)
            for sp in ax.spines.values():
                sp.set_color("#CBD5E1")
            ax.grid(True, color="#E2E8F0", linewidth=0.7)
            for s in sources:
                ts  = s["times"]
                ang = s["angles"]
                valid_t = [t for t, a in zip(ts, ang) if math.isfinite(a)]
                valid_a = [a for a in ang if math.isfinite(a)]
                if valid_t:
                    ax.plot(valid_t, valid_a, color=s["color"], linewidth=1.5,
                            label=s["label"], alpha=0.85)
            if sources:
                ax.legend(fontsize=8, framealpha=0.9)
            canvas.draw_idle()

        _refresh_src_list()
        _redraw()

    # ── transport ─────────────────────────────────────────────────────────────

    def _toggle_play(self):
        if self.cap is None:
            return
        if not self.tracker.ready:
            messagebox.showinfo("Set markers", "Place Knee + Ankle markers first.")
            return
        if not self._playing:
            # If parked at or past trim end, restart from trim start.
            if self._frame_idx >= self._trim_end_eff():
                self._frame_idx = self._trim_start
                self._show_frame_idx(self._frame_idx)
        self._playing = not self._playing
        self._btn_play.config(text="⏸" if self._playing else "▶")

    def _go_first(self):
        self._playing = False; self._btn_play.config(text="▶")
        self._frame_idx = 0; self._show_frame_idx(0)

    def _go_last(self):
        self._playing = False; self._btn_play.config(text="▶")
        fi = max(0, self.total_frames - 1)
        self._frame_idx = fi; self._show_frame_idx(fi)

    def _go_prev(self):
        self._playing = False; self._btn_play.config(text="▶")
        fi = max(0, self._frame_idx - 1)
        self._frame_idx = fi; self._show_frame_idx(fi)

    def _go_next(self):
        self._playing = False; self._btn_play.config(text="▶")
        fi = min(self.total_frames - 1, self._frame_idx + 1)
        self._frame_idx = fi; self._show_frame_idx(fi)

    # ── timeline canvas ───────────────────────────────────────────────────────

    def _trim_end_eff(self) -> int:
        """Effective trim end frame (exclusive). Falls back to total_frames."""
        return self._trim_end if self._trim_end > 0 else self.total_frames

    def _tl_frame_to_x(self, fi: int) -> float:
        w = self._tl.winfo_width()
        PAD = 10
        usable = max(1, w - 2 * PAD)
        n = max(1, self.total_frames - 1)
        return PAD + fi / n * usable

    def _tl_x_to_frame(self, x: float) -> int:
        w = self._tl.winfo_width()
        PAD = 10
        usable = max(1, w - 2 * PAD)
        fi = round((x - PAD) / usable * max(1, self.total_frames - 1))
        return max(0, min(self.total_frames - 1, fi))

    def _tl_draw(self):
        c = self._tl
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height() or 34
        if w < 20:
            return

        PAD   = 10
        TY1   = (h - 10) // 2      # top of center track strip
        TY2   = TY1 + 10           # bottom
        HW    = 9                  # half-width of bracket handle

        if self.total_frames == 0:
            c.create_rectangle(PAD, TY1, w - PAD, TY2, fill="#CBD5E1", outline="")
            return

        trim_s = self._trim_start
        trim_e = self._trim_end_eff()
        fi     = self._frame_idx

        xs = self._tl_frame_to_x(trim_s)
        xe = self._tl_frame_to_x(trim_e - 1)
        xp = self._tl_frame_to_x(fi)

        # 1. Full dim track
        c.create_rectangle(PAD, TY1, w - PAD, TY2, fill="#CBD5E1", outline="")

        # 2. Active (in-trim) region
        if xe > xs:
            c.create_rectangle(xs, TY1, xe, TY2, fill="#93C5FD", outline="")

        # 3. Outside-trim dim overlay (semi-transparent look via solid colour)
        OUTSIDE = "#E2E8F0"
        if xs > PAD + 2:
            c.create_rectangle(PAD, 0, xs, h, fill=OUTSIDE, outline="")
        if xe < w - PAD - 2:
            c.create_rectangle(xe, 0, w - PAD, h, fill=OUTSIDE, outline="")

        # 4. Left trim handle (bracket)
        mid = h // 2
        c.create_rectangle(xs - HW, 2, xs + HW, h - 2, fill="#2563EB", outline="")
        c.create_line(xs + 2, mid - 5, xs - 3, mid, xs + 2, mid + 5,
                      fill="white", width=1.5)

        # 5. Right trim handle (bracket)
        c.create_rectangle(xe - HW, 2, xe + HW, h - 2, fill="#2563EB", outline="")
        c.create_line(xe - 2, mid - 5, xe + 3, mid, xe - 2, mid + 5,
                      fill="white", width=1.5)

        # 6. Release marker — green dashed vertical line
        if self._release_fi is not None and 0 <= self._release_fi < self.total_frames:
            xr = self._tl_frame_to_x(self._release_fi)
            c.create_line(xr, 2, xr, h - 2, fill="#4ADE80", width=2, dash=(4, 3))
            c.create_text(xr + 4, 4, text="✂", anchor="nw", fill="#4ADE80", font=("", 8))

        # 7. Playhead — orange needle with a small dot on top
        c.create_line(xp, 2, xp, h - 2, fill="#F59E0B", width=2)
        c.create_oval(xp - 4, 0, xp + 4, 8, fill="#F59E0B", outline="")

    def _on_tl_press(self, event):
        if self.total_frames == 0:
            return
        HIT = 14    # px hit-zone around each handle
        xs = self._tl_frame_to_x(self._trim_start)
        xe = self._tl_frame_to_x(self._trim_end_eff() - 1)
        if abs(event.x - xs) <= HIT:
            self._tl_drag = "left"
        elif abs(event.x - xe) <= HIT:
            self._tl_drag = "right"
        else:
            self._tl_drag = None
            fi = self._tl_x_to_frame(event.x)
            self._frame_idx = fi
            self._show_frame_idx(fi)

    def _on_tl_drag(self, event):
        if self.total_frames == 0:
            return
        if self._tl_drag == "left":
            fi = self._tl_x_to_frame(event.x)
            fi = max(0, min(fi, self._trim_end_eff() - 2))
            self._trim_start = fi
            self._frame_idx  = fi
            self._show_frame_idx(fi)
        elif self._tl_drag == "right":
            fi = self._tl_x_to_frame(event.x)
            fi = max(self._trim_start + 1, min(fi, self.total_frames - 1))
            self._trim_end  = fi + 1
            self._frame_idx = fi
            self._show_frame_idx(fi)
        # plain seek is handled in press; don't re-seek while dragging elsewhere

    def _on_tl_release(self, event):
        was_dragging = self._tl_drag is not None
        self._tl_drag = None
        if was_dragging:
            # Trim region changed — re-evaluate PT score and update plot shading.
            self._compute_and_show_pt()
            self._update_trim_spans()

    # ── canvas click: marker placement ────────────────────────────────────────

    def _on_click(self, event):
        if self.cap is None:
            return
        # Recompute scale from live canvas dimensions so a click that arrives
        # within the 60 ms debounce window after a resize still maps correctly.
        if self._vid_w > 0 and self._vid_h > 0:
            cw = self._canvas.winfo_width()
            ch = self._canvas.winfo_height()
            if cw > 1 and ch > 1:
                self._scale = min(cw / self._vid_w, ch / self._vid_h)
                dw = int(self._vid_w * self._scale)
                dh = int(self._vid_h * self._scale)
                self._img_ox = max(0, (cw - dw) // 2)
                self._img_oy = max(0, (ch - dh) // 2)
        nx = (event.x - self._img_ox) / self._scale
        ny = (event.y - self._img_oy) / self._scale

        # Guided skeleton wizard intercepts all clicks when active.
        if self._skel_guide_active:
            self._on_skel_guide_click(nx, ny)
            return

        # Person-select mode must be checked before the idle guard — the mode
        # stays "idle" during person selection so the check was silently eaten.
        if self._person_select_active:
            self._on_person_select_click(nx, ny)
            return

        if self._mode == "idle":
            return

        if self._mode == "path":
            fi = self._frame_idx
            # Snap to shank arc so positions are physically consistent
            nx, ny = self._project_arc(nx, ny)
            self._path_keyframes[fi] = (nx, ny)
            n = len(self._path_keyframes)
            self._btn_clear_path.config(state="normal")
            if n >= 2:
                self._btn_apply_path.config(state="normal")
            self._status.config(
                text=f"Path: {n} point{'s' if n>1 else ''} marked "
                     f"(frame {fi}).  → key = next frame.  ✓ Apply when done.")
            self._show_frame_idx(fi)
            return  # stay in path mode

        clicked    = self._mode   # remember which marker before resetting mode
        is_shift   = bool(event.state & 0x0001)
        already_tracked = any(math.isfinite(a) for a in self._angles)

        if self._mode == "knee":
            self._knee_click = (nx, ny)
        elif self._mode == "ankle":
            self._ankle_click = (nx, ny)
        elif self._mode == "hip":
            self._hip_click = (nx, ny)

        # In correction mode, keep ankle mode sticky so the user can scrub and
        # click multiple frames without re-pressing the Ankle button each time.
        if already_tracked and not self._live and clicked == "ankle":
            self._mode = "ankle"    # stay in ankle mode
            # (button stays highlighted — no call to _hl_btn(None))
        else:
            self._mode = "idle"
            self._hl_btn(None)

        self._try_init_tracker(clicked=clicked, shift=is_shift)

        # Always redraw the current frame so the new marker dot appears immediately.
        self._show_frame_idx(self._frame_idx)

        # Status hint is set inside _try_init_tracker for corrections;
        # only set the generic hint for initial marker placement.
        if not already_tracked or self._live:
            next_hint = ""
            if self._knee_click and not self._ankle_click:
                next_hint = " → Now click 'Ankle'."
            elif self._ankle_click and not self._knee_click:
                next_hint = " → Now click 'Knee'."
            elif self._knee_click and self._ankle_click:
                next_hint = " → Press ▶ or 'Track All'."
            self._status.config(text=f"Marker placed at ({nx:.0f}, {ny:.0f}).{next_hint}")

    def _try_init_tracker(self, clicked: str = "", shift: bool = False):
        """
        Initialise tracker on first setup, or pin a single-frame marker correction.

        Three paths:
        - Initial setup: call tracker.init() to establish knee0/hip0/ankle0/shank_len.
        - Single-frame correction (already tracked, non-live): pin the exact clicked
          position, update the stored angle, enable Retrack.  Ankle uses the raw click
          directly (no arc-snap) so the marker goes exactly where the user placed it.
          Knee and hip work the same way via tracker re-init or attribute update.
        - Shift+click (ankle only): bulk-pins all frames from the previous ankle pin
          to the current frame at the same clicked position — fast correction for
          static segments where the leg is not moving.
        """
        already_tracked = any(math.isfinite(a) for a in self._angles)

        # ── single-frame correction on an already-tracked non-live video ──────
        if already_tracked and not self._live and clicked in ("ankle", "knee", "hip"):
            if not self.tracker.ready:
                return

            fi  = self._frame_idx
            hip = self.tracker.hip0
            kne = self.tracker.knee0

            def _current_ank() -> Optional[np.ndarray]:
                """Best estimate of ankle position at the current frame."""
                if hasattr(self, '_trail') and fi < len(self._trail) and self._trail[fi] is not None:
                    return np.array(self._trail[fi], dtype=np.float64)
                stored_ang = self._angles[fi] if fi < len(self._angles) else float("nan")
                if math.isfinite(stored_ang):
                    r = self._ankle_from_angle(stored_ang)
                    return r.astype(np.float64) if r is not None else None
                return None

            if clicked == "ankle":
                # Use exact click — no arc-snap.  Store the raw position as the pin
                # so _show_frame_idx displays it there directly, bypassing the
                # angle→arc reconstruction that would otherwise move it off-click.
                raw     = np.array(self._ankle_click, dtype=np.float64)
                new_ang = _angle_deg(hip, kne, raw)

                if shift and self._ankle_pins:
                    # Shift+click: bulk-pin every frame from the previous pin to
                    # the current frame with the same position.  Ideal for static
                    # pre-release holds where the leg isn't moving but the tracker
                    # has drifted.
                    prev_fi = max(f for f in self._ankle_pins if f <= fi)
                    fill_range = range(prev_fi + 1, fi + 1)
                    for bfi in fill_range:
                        self._angles[bfi]     = new_ang
                        self._ankle_pins[bfi] = raw.copy()
                        if hasattr(self, '_trail') and bfi < len(self._trail):
                            self._trail[bfi] = raw.astype(np.float32)
                    # Teach the tracker: same update as single-pin so Track All
                    # starts from this corrected position and shank length.
                    self.tracker.ankle0    = raw.astype(np.float32)
                    self.tracker.shank_len = float(np.linalg.norm(raw - self.tracker.knee0))
                    if self.tracker.ready:
                        self.tracker.apply_correction(raw)
                    n_filled = len(fill_range)
                    self._btn_retrack.config(state="normal")
                    self._status.config(
                        text=f"Bulk-pinned {n_filled} frames ({prev_fi+1}–{fi}) "
                             f"at {new_ang:.1f}°.  Press ⟳ Retrack → to continue tracking.")
                else:
                    # Normal single-frame pin.
                    self._angles[fi]     = new_ang
                    self._ankle_pins[fi] = raw
                    # Update _trail immediately so the arc display reflects the
                    # correction without waiting for a full Retrack pass.
                    if hasattr(self, '_trail') and fi < len(self._trail):
                        self._trail[fi] = raw.astype(np.float32)
                    # Teach the tracker: update the persistent ankle position and
                    # shank length so the next Track All / Retrack starts from the
                    # corrected geometry instead of the original (wrong) one.
                    self.tracker.ankle0    = raw.astype(np.float32)
                    self.tracker.shank_len = float(np.linalg.norm(raw - self.tracker.knee0))
                    # Feed correction back into the live tracker so it immediately
                    # resumes from the correct position with a directional prior.
                    if self.tracker.ready:
                        self.tracker.apply_correction(raw)
                    n_pins = len(self._ankle_pins)
                    hint = ("  Shift+click a later frame to bulk-pin the range."
                            if n_pins == 1 else
                            "  Shift+click to bulk-pin a range, or Retrack →.")
                    self._btn_retrack.config(state="normal")
                    self._status.config(
                        text=f"Ankle pinned at frame {fi} → {new_ang:.1f}°  "
                             f"[{n_pins} pin{'s' if n_pins != 1 else ''}].  {hint}")

            elif clicked == "knee":
                new_kne  = np.array(self._knee_click, dtype=np.float64)
                cur_ank  = _current_ank()
                if cur_ank is None:
                    return
                # Re-init tracker so knee0/shank_len/ankle0 reflect the corrected
                # geometry; the NCC template is built from the current frame at the
                # current ankle position (not the frame-0 extended position).
                frame = self._get_frame(fi)
                if frame is not None:
                    self.tracker.init(frame,
                                      tuple(hip.astype(float)),
                                      tuple(new_kne.astype(float)),
                                      tuple(cur_ank.astype(float)))
                else:
                    self.tracker.knee0     = new_kne.astype(np.float32)
                    self.tracker.shank_len = float(np.linalg.norm(cur_ank - new_kne))
                    self.tracker.ankle0    = cur_ank.astype(np.float32)
                new_ang = _angle_deg(self.tracker.hip0, self.tracker.knee0, cur_ank)
                self._angles[fi] = new_ang
                self._btn_retrack.config(state="normal")
                self._status.config(
                    text=f"Knee moved at frame {fi} → {new_ang:.1f}°.  "
                         "Press ⟳ Retrack → to propagate from here.")

            elif clicked == "hip":
                new_hip = np.array(self._hip_click, dtype=np.float64)
                cur_ank = _current_ank()
                if cur_ank is None:
                    return
                self.tracker.hip0 = new_hip.astype(np.float32)
                new_ang = _angle_deg(new_hip, kne, cur_ank)
                self._angles[fi] = new_ang
                self._btn_retrack.config(state="normal")
                self._status.config(
                    text=f"Hip moved at frame {fi} → {new_ang:.1f}°.  "
                         "Press ⟳ Retrack → to propagate from here.")

            self._update_plot(fi)
            self._show_frame_idx(fi)
            return

        # ── initial setup requires both markers ──────────────────────────────
        if self._knee_click is None or self._ankle_click is None:
            return

        # ── initial setup (or live correction) ───────────────────────────────
        hip = self._hip_click
        if hip is None:
            kne = np.array(self._knee_click, dtype=np.float32)
            ank = np.array(self._ankle_click, dtype=np.float32)
            hip = tuple(self._auto_hip(kne, ank))
            self._hip_click = hip
        frame = self._last_live_frame if self._live else self._get_frame(self._frame_idx)
        if frame is None:
            return
        self.tracker.init(frame, self._hip_click, self._knee_click, self._ankle_click)

        if self._live and self._recording:
            self._status.config(
                text=f"Fitting updated at t={self._live_fi/self.fps:.1f}s — "
                     f"thigh={self.tracker.thigh_len:.0f}px  shank={self.tracker.shank_len:.0f}px  "
                     "tracking continues with new markers.")
        elif self._live:
            self._status.config(
                text=f"Fitting live — thigh={self.tracker.thigh_len:.0f}px  "
                     f"shank={self.tracker.shank_len:.0f}px.  "
                     "Adjust as needed, then press ⏺ Record.")
        elif already_tracked:
            self._btn_retrack.config(state="normal")
            self._status.config(
                text=f"Knee/Hip marker moved at frame {self._frame_idx}. "
                     "Press ▶▶ Track All to re-track the entire video.")

    # ── guided skeleton wizard ────────────────────────────────────────────────

    def _cmd_skel_guide(self):
        """Enter the 3-click manual skeleton wizard: Hip → Knee → Ankle."""
        if self.cap is None:
            return
        self._playing = False
        self._btn_play.config(text="▶")
        self._skel_guide_active = True
        self._skel_guide_step   = 0
        self._skel_guide_tmp    = [None, None, None]
        self._mode = "idle"          # disable any pending single-point modes
        self._hl_btn(self._btn_skel)
        self._status.config(
            text="Set Skeleton (1/3): Click the patient's HIP joint.  "
                 "[Esc to cancel]")
        self._show_frame_idx(self._frame_idx)

    def _on_skel_guide_click(self, nx: float, ny: float):
        """Handle one click in the guided skeleton wizard."""
        self._skel_guide_tmp[self._skel_guide_step] = np.array([nx, ny], dtype=np.float32)
        self._skel_guide_step += 1
        prompts = [
            "Set Skeleton (2/3): Click the patient's KNEE joint.  [Esc to cancel]",
            "Set Skeleton (3/3): Click the patient's ANKLE joint.  [Esc to cancel]",
        ]
        if self._skel_guide_step < 3:
            self._status.config(text=prompts[self._skel_guide_step - 1])
        else:
            self._skel_guide_active = False
            self._hl_btn(None)
            self._apply_skel_guide()
            return
        self._show_frame_idx(self._frame_idx)

    def _apply_skel_guide(self):
        """Commit the three manually placed points as the tracker skeleton.

        Crucially, this BYPASSES tracker.init() entirely.  init() runs
        MediaPipe and can snap to the nearest detected person — which is often
        the assessor when both are in frame.  Here we set all tracker attributes
        directly from the clinician's clicks so MediaPipe has zero opportunity
        to override them.
        """
        hip_a, kne_a, ank_a = self._skel_guide_tmp
        if None in (hip_a, kne_a, ank_a):
            return
        hip = hip_a.astype(np.float32)
        kne = kne_a.astype(np.float32)
        ank = ank_a.astype(np.float32)

        # Mirror into the per-point click slots so any subsequent call that
        # reads _hip_click / _knee_click / _ankle_click sees the guide values.
        self._hip_click   = (float(hip[0]), float(hip[1]))
        self._knee_click  = (float(kne[0]), float(kne[1]))
        self._ankle_click = (float(ank[0]), float(ank[1]))

        frame = self._get_frame(self._frame_idx)
        fw    = frame.shape[1] if frame is not None else max(self._vid_w, 1)

        # ── set tracker geometry directly — no MediaPipe ──────────────────────
        t = self.tracker
        t.hip0      = hip.copy()
        t.knee0     = kne.copy()
        t.ankle0    = ank.copy()
        t.thigh_len = float(np.linalg.norm(kne - hip))
        t.shank_len = float(np.linalg.norm(ank - kne))
        t._hip_px   = hip.copy()
        t._knee_px  = kne.copy()
        t._ank_px   = ank.copy()
        t._anchor_xfrac = float(kne[0]) / fw
        t._ready    = True
        # Ensure the pose detector is open so step() can run after Track All
        if hasattr(t, '_open_pose'):
            t._open_pose()

        # ── lock knee anchor on _MPBatchTracker compat attrs ─────────────────
        if hasattr(t, 'knee'):
            t.knee = kne.copy()

        # ── seed appearance template from clicked ankle ───────────────────────
        if frame is not None:
            patch = _ankle_patch(frame, ank)
            if patch is not None:
                self._ankle_correct_template = patch
                if hasattr(t, '_correct_template'):
                    t._correct_template = patch

        # ── clear stale trail so old assessor tracking doesn't show through ───
        if hasattr(self, '_trail') and self.total_frames > 0:
            self._trail  = [None] * self.total_frames
            self._angles = [float('nan')] * self.total_frames
            self._ankle_pins.clear()

        shank = t.shank_len
        self._btn_retrack.config(state="normal")
        self._status.config(
            text=f"✓ Manual skeleton set (shank {shank:.0f} px).  "
                 "Press ▶▶ Track All to track from these points.")
        self._show_frame_idx(self._frame_idx)

    def _overlay_skel_guide(self, frame: np.ndarray) -> np.ndarray:
        """Draw the already-placed guide points and a step-instruction banner."""
        NAMES  = ["Hip",     "Knee",      "Ankle"]
        COLORS = [(255, 160, 30), (50, 220, 80), (80, 180, 255)]   # orange / green / blue
        out = frame.copy()

        prev_pt = None
        for i, pt in enumerate(self._skel_guide_tmp):
            if pt is None:
                continue
            x = int(round(float(pt[0]))); y = int(round(float(pt[1])))
            col = COLORS[i]
            if prev_pt is not None:
                px = int(round(float(prev_pt[0]))); py = int(round(float(prev_pt[1])))
                cv2.line(out, (px, py), (x, y), col, 2, cv2.LINE_AA)
            cv2.circle(out, (x, y), 9, (0, 0, 0), 2, cv2.LINE_AA)
            cv2.circle(out, (x, y), 8, col, -1, cv2.LINE_AA)
            (lw, lh), _ = cv2.getTextSize(NAMES[i], cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            cv2.rectangle(out, (x + 12, y - lh - 3), (x + 14 + lw, y + 3),
                          (0, 0, 0), -1)
            cv2.putText(out, NAMES[i], (x + 13, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, col, 1, cv2.LINE_AA)
            prev_pt = pt

        # Instruction banner for the next step
        if self._skel_guide_step < 3:
            step_labels = ["HIP", "KNEE", "ANKLE"]
            step_cols   = COLORS
            label = step_labels[self._skel_guide_step]
            col   = step_cols[self._skel_guide_step]
            banner = f"({self._skel_guide_step + 1}/3)  Click the patient's {label}"
            (bw, bh), _ = cv2.getTextSize(banner, cv2.FONT_HERSHEY_SIMPLEX, 0.68, 2)
            bx, by = 14, 30
            cv2.rectangle(out, (bx - 6, by - bh - 8), (bx + bw + 8, by + 6),
                          (0, 0, 0), -1)
            cv2.putText(out, banner, (bx, by),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.68, col, 2, cv2.LINE_AA)
        return out

    def _hl_btn(self, btn):
        # Reset all to normal light-mode palette colour
        for b in (self._btn_knee, self._btn_ankle, self._btn_hip, self._btn_path, self._btn_skel):
            b.config(bg="#E2E8F0", fg="#0F172A")
        if btn:
            # Light-blue highlight — clearly visible on the light toolbar
            btn.config(bg="#BFDBFE", fg="#1E40AF")


# ─── session history window ───────────────────────────────────────────────────

class _HistoryWindow(tk.Toplevel):
    """
    Clinical dashboard — two-pane layout:
      Left  : all participants (scrollable list with latest MAS badge)
      Right : trials for selected participant (table + summary cards)
    Double-click a trial row to open its video in the main viewer.
    """

    _COL_KEYS = ("date", "time", "leg", "trial", "mas", "pt4",
                 "R2n", "N", "phimax", "omegamax", "video")
    _COL_HDRS = ("Date", "Time", "Leg", "Trial", "MAS", "PT(4)",
                 "R₂ₙ", "N", "φmax", "ωmax", "Video")
    _COL_W    = (88, 60, 44, 40, 48, 58, 52, 44, 52, 52, 160)

    def __init__(self, master, db: dict):
        super().__init__(master)
        self._db     = db
        self._master = master          # PendulaticViewer — used to open video
        self.title("Patient Dashboard — Pendulastic")
        self.geometry("1180x640")
        self.configure(bg=_C["BG"])
        self.resizable(True, True)
        self._sort_key = "date"
        self._sort_rev = True
        self._selected_pid: Optional[str] = None
        self._build()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build(self):
        BG = _C["BG"]; SFC = _C["SURFACE"]; PNL = _C["PANEL"]
        FG = _C["FG"]; FG2 = _C["FG2"]; FG3 = _C["FG3"]
        BTN = _C["BTN"]; BTN_ACT = _C["BTN_ACT"]
        BORDER = _C["BORDER"]

        # ── top action bar ────────────────────────────────────────────────────
        top = tk.Frame(self, bg=PNL, pady=6, padx=10)
        top.pack(fill="x")
        tk.Label(top, text="Patient Dashboard", bg=PNL, fg=FG,
                 font=("Segoe UI", 11, "bold")).pack(side="left")

        _bd = dict(relief="flat", bd=0, padx=10, pady=4,
                   font=("Segoe UI", 9), cursor="hand2")
        tk.Button(top, text="🔄 Refresh", command=self._cmd_refresh,
                  bg=BTN, fg=FG, activebackground=BTN_ACT,
                  activeforeground="#FFFFFF", **_bd).pack(side="right", padx=2)
        tk.Button(top, text="📤 Export CSV", command=self._cmd_export_csv,
                  bg=BTN, fg=FG, activebackground=BTN_ACT,
                  activeforeground="#FFFFFF", **_bd).pack(side="right", padx=2)
        tk.Button(top, text="▶ Open Video", command=self._cmd_open_video,
                  bg=BTN_ACT, fg="#FFFFFF", activebackground="#1A5080",
                  activeforeground="#FFFFFF", **_bd).pack(side="right", padx=2)
        tk.Button(top, text="🗑 Delete", command=self._cmd_delete,
                  bg=PNL, fg=FG3, activebackground="#3D1010",
                  activeforeground="#FF9090", **_bd).pack(side="right", padx=2)

        # ── two-pane body ─────────────────────────────────────────────────────
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # LEFT: participant roster
        left = tk.Frame(body, bg=PNL, width=220)
        left.pack(side="left", fill="y", padx=(8, 0), pady=8)
        left.pack_propagate(False)

        tk.Label(left, text="PATIENTS", bg=PNL, fg=FG3,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=8, pady=(8, 2))

        roster_wrap = tk.Frame(left, bg=PNL)
        roster_wrap.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        # Canvas + scrollbar for the roster
        roster_canvas = tk.Canvas(roster_wrap, bg=PNL, highlightthickness=0)
        roster_sb     = ttk.Scrollbar(roster_wrap, orient="vertical",
                                       command=roster_canvas.yview)
        roster_canvas.configure(yscrollcommand=roster_sb.set)
        roster_sb.pack(side="right", fill="y")
        roster_canvas.pack(side="left", fill="both", expand=True)

        self._roster_inner = tk.Frame(roster_canvas, bg=PNL)
        self._roster_win   = roster_canvas.create_window(
            (0, 0), window=self._roster_inner, anchor="nw")
        self._roster_inner.bind(
            "<Configure>",
            lambda e: roster_canvas.configure(
                scrollregion=roster_canvas.bbox("all")))
        roster_canvas.bind(
            "<Configure>",
            lambda e: roster_canvas.itemconfig(self._roster_win, width=e.width))
        self._roster_canvas = roster_canvas

        # RIGHT: summary + trials table
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        # Summary cards strip
        self._summary_frame = tk.Frame(right, bg=SFC)
        self._summary_frame.pack(fill="x", pady=(0, 6))

        # Trial table
        tbl_wrap = tk.Frame(right, bg=BG)
        tbl_wrap.pack(fill="both", expand=True)

        style = ttk.Style(self)
        style.configure("Dash.Treeview",
                         background=SFC, foreground=FG,
                         fieldbackground=SFC, rowheight=24,
                         font=("Segoe UI", 9))
        style.configure("Dash.Treeview.Heading",
                         background=PNL, foreground=FG2,
                         font=("Segoe UI", 9, "bold"))
        style.map("Dash.Treeview",
                  background=[("selected", BTN)],
                  foreground=[("selected", FG)])

        self._tree = ttk.Treeview(tbl_wrap, style="Dash.Treeview",
                                   columns=self._COL_KEYS,
                                   show="headings", selectmode="browse")
        for key, hdr, w in zip(self._COL_KEYS, self._COL_HDRS, self._COL_W):
            self._tree.heading(key, text=hdr,
                                command=lambda k=key: self._sort_by(k))
            anchor = "w" if key == "video" else "center"
            self._tree.column(key, width=w, anchor=anchor,
                               stretch=(key == "video"))

        tree_sb = ttk.Scrollbar(tbl_wrap, orient="vertical",
                                 command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_sb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        tree_sb.pack(side="right", fill="y")

        for mas, color in _MAS_COLORS.items():
            self._tree.tag_configure(f"m{mas.replace('+','p')}", foreground=color)

        self._tree.bind("<Double-1>", lambda e: self._cmd_open_video())
        self._tree.bind("<Delete>",   lambda e: self._cmd_delete())

        # hint line at bottom
        hint = tk.Frame(self, bg=_C["BG"], pady=3)
        hint.pack(fill="x")
        tk.Label(hint, text="Double-click a trial to open its video  |  "
                            "Click column headers to sort",
                 bg=_C["BG"], fg=FG3, font=("Segoe UI", 7)).pack()

        self._rebuild_roster()
        # auto-select first participant
        parts = self._db["participants"]
        if parts:
            self._select_participant(parts[0]["id"])

    # ── roster (left pane) ────────────────────────────────────────────────────

    def _rebuild_roster(self):
        PNL = _C["PANEL"]; FG = _C["FG"]; FG2 = _C["FG2"]; FG3 = _C["FG3"]
        for w in self._roster_inner.winfo_children():
            w.destroy()

        if not self._db["participants"]:
            tk.Label(self._roster_inner, text="No patients yet",
                     bg=PNL, fg=FG3, font=("Segoe UI", 9)).pack(pady=12)
            return

        for p in self._db["participants"]:
            pid = p["id"]
            sessions = [s for s in self._db["sessions"]
                        if s["participant"] == pid]
            latest_mas = sessions[-1]["mas"] if sessions else "—"
            mas_color  = _MAS_COLORS.get(latest_mas, FG3)
            n_sess     = len(sessions)

            row = tk.Frame(self._roster_inner, bg=PNL, cursor="hand2",
                           pady=6, padx=8)
            row.pack(fill="x", pady=1)

            # MAS badge
            badge = tk.Label(row, text=f"MAS\n{latest_mas}",
                             bg=PNL, fg=mas_color,
                             font=("Segoe UI", 10, "bold"),
                             width=5, justify="center")
            badge.pack(side="left")

            # Name + stats
            info = tk.Frame(row, bg=PNL)
            info.pack(side="left", padx=(6, 0), fill="x", expand=True)
            dx_txt = p.get("diagnosis", "") or p.get("name", "")
            tk.Label(info, text=pid, bg=PNL, fg=FG,
                     font=("Segoe UI", 9, "bold"), anchor="w").pack(anchor="w")
            detail = f"{dx_txt}  ·  {n_sess} trial{'s' if n_sess!=1 else ''}" if dx_txt \
                     else f"{n_sess} trial{'s' if n_sess!=1 else ''}"
            tk.Label(info, text=detail,
                     bg=PNL, fg=FG3, font=("Segoe UI", 7), anchor="w").pack(anchor="w")

            # Highlight selected
            def _hl(widget, pid=pid, row=row):
                bg = "#1A3A5C" if pid == self._selected_pid else PNL
                for w in (row, badge, info) + tuple(info.winfo_children()) + (row,):
                    try:
                        w.configure(bg=bg)
                    except Exception:
                        pass

            for w in (row, badge, info):
                w.bind("<Button-1>", lambda e, p=pid: self._select_participant(p))
                for child in info.winfo_children():
                    child.bind("<Button-1>", lambda e, p=pid: self._select_participant(p))

            if pid == self._selected_pid:
                for w in (row, badge, info) + tuple(info.winfo_children()):
                    try:
                        w.configure(bg="#1A3A5C")
                    except Exception:
                        pass

    def _select_participant(self, pid: str):
        self._selected_pid = pid
        self._rebuild_roster()
        part = next((p for p in self._db["participants"] if p["id"] == pid), None)
        self._rebuild_summary(part)
        self._rebuild_table(part)

    # ── summary cards (right-top) ─────────────────────────────────────────────

    def _rebuild_summary(self, part: Optional[dict]):
        for w in self._summary_frame.winfo_children():
            w.destroy()
        if part is None:
            return

        SFC = _C["SURFACE"]; PNL = _C["PANEL"]
        FG = _C["FG"]; FG2 = _C["FG2"]; FG3 = _C["FG3"]

        sessions = [s for s in self._db["sessions"]
                    if s["participant"] == part["id"]]

        hdr_row = tk.Frame(self._summary_frame, bg=SFC)
        hdr_row.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(hdr_row, text=part.get("name", part["id"]),
                 bg=SFC, fg=FG, font=("Segoe UI", 13, "bold")).pack(side="left")
        tk.Label(hdr_row, text=f"  [{part['id']}]",
                 bg=SFC, fg=FG2, font=("Segoe UI", 10)).pack(side="left")
        total_s = len(sessions)
        tk.Label(hdr_row, text=f"  {total_s} trial{'s' if total_s != 1 else ''}",
                 bg=SFC, fg=FG3, font=("Segoe UI", 9)).pack(side="left", padx=8)
        dx = part.get("diagnosis", "")
        if dx:
            tk.Label(hdr_row, text=dx, bg=SFC, fg=FG2,
                     font=("Segoe UI", 9, "italic")).pack(side="left")

        cards = tk.Frame(self._summary_frame, bg=SFC)
        cards.pack(fill="x", padx=10, pady=(0, 8))

        for leg_label, leg_key, icon in (
                ("Left Leg",  "left",  "←"),
                ("Right Leg", "right", "→")):
            leg_sess = sorted(
                [s for s in sessions if s["leg"] == leg_key],
                key=lambda s: s.get("date", "") + s.get("time", ""))
            card = tk.Frame(cards, bg=PNL, padx=14, pady=10)
            card.pack(side="left", padx=(0, 12))
            tk.Label(card, text=f"{icon} {leg_label}", bg=PNL, fg=FG3,
                     font=("Segoe UI", 8, "bold")).pack(anchor="w")
            if leg_sess:
                latest = leg_sess[-1]
                mas    = latest["mas"]
                color  = _MAS_COLORS.get(mas, "#FFFFFF")
                tk.Label(card, text=f"MAS {mas}", bg=PNL, fg=color,
                         font=("Segoe UI", 22, "bold")).pack(anchor="w")
                pt4 = latest.get("pt_4", 0)
                tk.Label(card,
                         text=(f"PT = {pt4:.3f}  ·  "
                               f"{latest['date']}  T{latest['trial']}"),
                         bg=PNL, fg=FG2, font=("Segoe UI", 8)).pack(anchor="w")
                if len(leg_sess) > 1:
                    tail = leg_sess[-min(6, len(leg_sess)):]
                    dots = "  ".join(
                        f"[{s['mas']}]" for s in tail)
                    tk.Label(card, text=f"Recent: {dots}",
                             bg=PNL, fg=FG3,
                             font=("Segoe UI", 7)).pack(anchor="w", pady=(3, 0))
            else:
                tk.Label(card, text="No trials", bg=PNL, fg=FG3,
                         font=("Segoe UI", 10)).pack(pady=6)

    # ── trial table (right-bottom) ────────────────────────────────────────────

    def _rebuild_table(self, part: Optional[dict]):
        self._tree.delete(*self._tree.get_children())
        if part is None:
            return
        sessions = [s for s in self._db["sessions"]
                    if s["participant"] == part["id"]]

        _key_map = {
            "phimax":   "phi_max_ratio",
            "omegamax": "omega_max_n",
            "pt4":      "pt_4",
        }

        def _sk(s):
            raw_key = _key_map.get(self._sort_key, self._sort_key)
            v = s.get(raw_key, "")
            try:
                return float(v)
            except (TypeError, ValueError):
                return str(v)

        sessions = sorted(sessions, key=_sk, reverse=self._sort_rev)

        for s in sessions:
            leg_disp = "← L" if s["leg"] == "left" else "R →"
            tag = f"m{s['mas'].replace('+','p')}"
            self._tree.insert("", "end", iid=s["id"], tags=(tag,), values=(
                s["date"],
                s.get("time", ""),
                leg_disp,
                s["trial"],
                s["mas"],
                f"{s.get('pt_4', 0):.3f}",
                f"{s.get('R2n', 0):.2f}",
                f"{s.get('N', 0):.1f}",
                f"{s.get('phi_max_ratio', 0):.2f}",
                f"{s.get('omega_max_n', 0):.2f}",
                s.get("video", ""),
            ))

    def _sort_by(self, key: str):
        if self._sort_key == key:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_key = key
            self._sort_rev = True
        part = next((p for p in self._db["participants"]
                     if p["id"] == self._selected_pid), None)
        self._rebuild_table(part)

    # ── commands ──────────────────────────────────────────────────────────────

    def _selected_session(self) -> Optional[dict]:
        sel = self._tree.selection()
        if not sel:
            return None
        sess_id = sel[0]
        return next((s for s in self._db["sessions"] if s["id"] == sess_id), None)

    def _cmd_open_video(self):
        sess = self._selected_session()
        if sess is None:
            messagebox.showinfo("Nothing selected",
                                "Click a trial row first.", parent=self)
            return
        video_name = sess.get("video", "")
        if not video_name:
            messagebox.showwarning("No video", "No video file recorded for this trial.",
                                   parent=self)
            return
        # Resolution order:
        # 1. Stored value is an absolute path that still exists.
        # 2. Stored value is relative to BASE_DIR (new format).
        # 3. Legacy basename — search Recordings/ then uploads/ then full walk.
        path = None
        if os.path.isabs(video_name) and os.path.exists(video_name):
            path = video_name
        else:
            rel = os.path.join(BASE_DIR, video_name)
            if os.path.exists(rel):
                path = rel

        if path is None:
            # Fallback: search known subdirectories by basename
            basename = os.path.basename(video_name)
            search_roots = [
                os.path.join(BASE_DIR, "Recordings"),
                os.path.join(BASE_DIR, "uploads"),
                BASE_DIR,
            ]
            for root in search_roots:
                if not os.path.isdir(root):
                    continue
                for dirpath, _, filenames in os.walk(root):
                    if basename in filenames:
                        path = os.path.join(dirpath, basename)
                        break
                if path:
                    break

        if path is None:
            messagebox.showerror("File not found",
                                 f"Cannot locate:\n{os.path.basename(video_name)}\n\n"
                                 "Move the video into the Recordings folder, or open it "
                                 "directly with File → Open Video.", parent=self)
            return
        self._master._open_video(path)
        self._master.lift()
        self._master.focus_force()

    def _cmd_refresh(self):
        self._db = _load_db()
        self._rebuild_roster()
        part = next((p for p in self._db["participants"]
                     if p["id"] == self._selected_pid), None)
        if part:
            self._rebuild_summary(part)
            self._rebuild_table(part)

    def _cmd_delete(self):
        sess = self._selected_session()
        if sess is None:
            messagebox.showinfo("Nothing selected", "Click a row first.", parent=self)
            return
        if not messagebox.askyesno(
                "Delete trial?",
                f"Delete this trial?\n\n{sess['name']}  |  "
                f"{sess['leg'].capitalize()} leg  |  "
                f"{sess['date']}  Trial {sess['trial']}  MAS {sess['mas']}",
                parent=self):
            return
        self._db["sessions"] = [
            s for s in self._db["sessions"] if s["id"] != sess["id"]]
        _save_db(self._db)
        part = next((p for p in self._db["participants"]
                     if p["id"] == self._selected_pid), None)
        self._rebuild_roster()
        self._rebuild_summary(part)
        self._rebuild_table(part)

    def _cmd_export_csv(self):
        pid = self._selected_pid
        if not pid:
            messagebox.showinfo("No patient selected",
                                "Select a patient first.", parent=self)
            return
        part = next((p for p in self._db["participants"] if p["id"] == pid), None)
        sessions = [s for s in self._db["sessions"] if s["participant"] == pid]
        if not sessions:
            messagebox.showinfo("No data",
                                "No trials for this patient.", parent=self)
            return
        default_name = f"{pid}_{part.get('name', part['id']).replace(' ','_')}_history.csv"
        out = filedialog.asksaveasfilename(
            title="Save Patient History CSV",
            initialdir=BASE_DIR,
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self,
        )
        if not out:
            return
        fields = ["date", "time", "leg", "trial", "mas", "pt_4", "pt_6",
                  "R2n", "N", "phi_max_ratio", "omega_max_n", "f", "area_ratio",
                  "A0_deg", "omega_peak_deg_s", "video"]
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(sessions)
        messagebox.showinfo("Exported", f"Saved to:\n{out}", parent=self)


# ─── OptiTrack Validation Window ─────────────────────────────────────────────

class ValidationWindow(tk.Toplevel):
    """
    Standalone comparison window: model knee-angle CSV  vs  OptiTrack CSV.
    Does not touch any existing viewer state.
    """

    # Light palette (matches main window)
    _BG  = "#EEF2F7"; _SFC = "#FFFFFF"; _PNL = "#F1F5F9"
    _FG  = "#0F172A"; _FG2 = "#475569"; _FG3 = "#94A3B8"
    _BDR = "#CBD5E1"; _BTN = "#E2E8F0"; _ACT = "#2563EB"
    _GOLD = "#B45309"; _BLUE = "#2563EB"; _RED = "#DC2626"
    _GRN = "#16A34A"; _MONO = "Consolas"

    # Parameter display rows: (key, label, unit)
    _PARAM_ROWS = [
        ("R2n",           "Relaxation R₂ₙ",  ""),
        ("N",             "Swing count N",    "cycles"),
        ("phi_max_ratio", "Excursion φmax",   ""),
        ("omega_max_n",   "Peak vel. ωmax",   ""),
        ("f",             "Frequency f",      "Hz"),
        ("area_ratio",    "Area ratio AR",    ""),
        ("A0_deg",        "Initial A₀",       "°"),
        ("omega_peak_deg_s", "Peak ω",        "°/s"),
    ]

    def __init__(self, master,
                 live_angles: Optional[list] = None,
                 live_fps:    float          = 30.0,
                 live_label:  str            = ""):
        super().__init__(master)
        self.title("OptiTrack Accuracy Validation")
        self.configure(bg=self._BG)
        self.geometry("1060x720")
        self.resizable(True, True)
        self.transient(master)

        # Pre-populated from current viewer trial (optional)
        self._live_angles = live_angles
        self._live_fps    = live_fps
        self._live_label  = live_label or "Current trial"

        # File paths
        self._model_path_var    = tk.StringVar(value="")
        self._optitrack_path_var = tk.StringVar(value="")

        # Alignment mode
        self._align_var = tk.StringVar(value="auto_sync")

        # Result holders
        self._fig  = None
        self._ax   = None
        self._pc   = None   # FigureCanvasTkAgg

        self._build()
        if live_angles:
            self._model_path_var.set(f"[{self._live_label}]")

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build(self):
        BG = self._BG; SFC = self._SFC; PNL = self._PNL
        FG = self._FG; FG2 = self._FG2; FG3 = self._FG3
        BDR = self._BDR; BTN = self._BTN; ACT = self._ACT

        # ── header bar ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=PNL, pady=8, padx=12)
        hdr.pack(fill="x")
        tk.Label(hdr, text="📊  OptiTrack Accuracy Validation",
                 bg=PNL, fg=FG, font=("Segoe UI", 11, "bold")).pack(side="left")
        self._status_lbl = tk.Label(hdr, text="Load both files and click Analyse.",
                                     bg=PNL, fg=FG2, font=("Segoe UI", 8))
        self._status_lbl.pack(side="right")

        # ── file-picker row ───────────────────────────────────────────────────
        pick = tk.Frame(self, bg=PNL, padx=12, pady=6)
        pick.pack(fill="x")

        _fe = dict(bg=SFC, fg=FG, insertbackground=FG, relief="solid", bd=1,
                   font=("Segoe UI", 8), highlightthickness=0)
        _bb = dict(relief="flat", bd=0, padx=9, pady=3, cursor="hand2",
                   font=("Segoe UI", 8))

        # Model row
        row1 = tk.Frame(pick, bg=PNL)
        row1.pack(fill="x", pady=2)
        tk.Label(row1, text="Model CSV:", bg=PNL, fg=FG2,
                 font=("Segoe UI", 8), width=12, anchor="e").pack(side="left")
        tk.Entry(row1, textvariable=self._model_path_var, width=55, **_fe).pack(
            side="left", padx=(4, 3))
        tk.Button(row1, text="Browse…", command=self._browse_model,
                  bg=BTN, fg=FG, activebackground=ACT,
                  activeforeground="#FFFFFF", **_bb).pack(side="left", padx=2)
        if self._live_angles:
            tk.Button(row1, text="Use Current Trial",
                      command=lambda: self._model_path_var.set(
                          f"[{self._live_label}]"),
                      bg=ACT, fg="#FFFFFF",
                      activebackground="#1A4FAF",
                      activeforeground="#FFFFFF", **_bb).pack(side="left", padx=2)
        tk.Label(row1, text="frame,time_sec,knee_angle_deg  (interior angle — 180° = extended)",
                 bg=PNL, fg=FG3, font=("Segoe UI", 7)).pack(side="left", padx=6)

        # OptiTrack row
        row2 = tk.Frame(pick, bg=PNL)
        row2.pack(fill="x", pady=2)
        tk.Label(row2, text="OptiTrack CSV:", bg=PNL, fg=FG2,
                 font=("Segoe UI", 8), width=12, anchor="e").pack(side="left")
        tk.Entry(row2, textvariable=self._optitrack_path_var, width=55, **_fe).pack(
            side="left", padx=(4, 3))
        tk.Button(row2, text="Browse…", command=self._browse_optitrack,
                  bg=BTN, fg=FG, activebackground=ACT,
                  activeforeground="#FFFFFF", **_bb).pack(side="left", padx=2)
        tk.Label(row2, text="Raw Motive CSV  or  pre-computed flexion CSV (0° = extended)",
                 bg=PNL, fg=FG3, font=("Segoe UI", 7)).pack(side="left", padx=6)

        # Alignment mode row
        align_row = tk.Frame(pick, bg=PNL)
        align_row.pack(fill="x", pady=(2, 0))
        tk.Label(align_row, text="Align by:", bg=PNL, fg=FG2,
                 font=("Segoe UI", 8), width=12, anchor="e").pack(side="left")
        _rb = dict(bg=PNL, fg=FG, activebackground=PNL, activeforeground=FG,
                   selectcolor=PNL, font=("Segoe UI", 8))
        tk.Radiobutton(align_row, text="Auto-sync  (cross-correlation, ±3 s)",
                       variable=self._align_var, value="auto_sync", **_rb).pack(side="left", padx=(4, 14))
        tk.Radiobutton(align_row, text="Raw time  (simultaneous start)",
                       variable=self._align_var, value="raw_time", **_rb).pack(side="left", padx=(0, 14))
        tk.Radiobutton(align_row, text="Release point",
                       variable=self._align_var, value="release", **_rb).pack(side="left")

        # Analyse button
        tk.Button(pick, text="▶  Analyse", command=self._analyse,
                  bg=ACT, fg="#FFFFFF", activebackground="#1A4FAF",
                  activeforeground="#FFFFFF", relief="flat", bd=0,
                  padx=14, pady=4, cursor="hand2",
                  font=("Segoe UI", 9, "bold")).pack(side="right", pady=4)

        ttk.Separator(self, orient="horizontal").pack(fill="x")

        # ── main body: plot (top) + metrics/table (bottom) ────────────────────
        self._body = tk.Frame(self, bg=BG)
        self._body.pack(fill="both", expand=True)

        # Plot area (will be populated on analyse)
        self._plot_frame = tk.Frame(self._body, bg=SFC,
                                    highlightthickness=1, highlightbackground=BDR)
        self._plot_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self._plot_placeholder = tk.Label(
            self._plot_frame,
            text="Load a model CSV and an OptiTrack CSV, then click  ▶ Analyse",
            bg=SFC, fg=FG3, font=("Segoe UI", 10))
        self._plot_placeholder.pack(expand=True)

        # Bottom strip: metrics | parameter table
        self._bottom = tk.Frame(self._body, bg=BG)
        self._bottom.pack(fill="x", padx=8, pady=(0, 8))

        # Metrics card (left)
        self._metrics_frame = tk.Frame(self._bottom, bg=SFC,
                                        highlightthickness=1, highlightbackground=BDR,
                                        padx=14, pady=10, width=200)
        self._metrics_frame.pack(side="left", fill="y", padx=(0, 6))
        self._metrics_frame.pack_propagate(False)
        tk.Label(self._metrics_frame, text="ACCURACY METRICS",
                 bg=SFC, fg=FG3, font=("Segoe UI", 7, "bold")).pack(anchor="w")

        # Parameter table (right)
        self._table_frame = tk.Frame(self._bottom, bg=SFC,
                                      highlightthickness=1, highlightbackground=BDR,
                                      padx=14, pady=10)
        self._table_frame.pack(side="left", fill="both", expand=True)
        tk.Label(self._table_frame, text="PARAMETER COMPARISON",
                 bg=SFC, fg=FG3, font=("Segoe UI", 7, "bold")).pack(anchor="w")

    # ── file pickers ──────────────────────────────────────────────────────────

    def _browse_model(self):
        p = filedialog.askopenfilename(
            title="Select Model Angle CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self)
        if p:
            self._model_path_var.set(p)

    def _browse_optitrack(self):
        p = filedialog.askopenfilename(
            title="Select OptiTrack CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self)
        if p:
            self._optitrack_path_var.set(p)

    # ── core analysis ─────────────────────────────────────────────────────────

    def _analyse(self):
        import pandas as pd

        try:
            from pendulastic_pt_score import (
                compute_pt_params, compute_pt_score_simple, pt_to_mas,
                load_optitrack, HEALTHY_REF,
            )
        except ImportError as e:
            messagebox.showerror("Import error",
                                 f"pendulastic_pt_score not found:\n{e}", parent=self)
            return

        self._status("Loading…")
        self.update()

        # ── load model angles ─────────────────────────────────────────────────
        model_path = self._model_path_var.get().strip()
        is_live = model_path.startswith("[") and self._live_angles is not None

        try:
            if is_live:
                angs_m = np.array(self._live_angles, dtype=float)
                fps_m  = max(self._live_fps, 1.0)
                t_m_raw = np.arange(len(angs_m)) / fps_m
            else:
                if not model_path or not os.path.exists(model_path):
                    messagebox.showerror("File not found",
                                         "Model CSV not found.", parent=self)
                    return
                df_m = pd.read_csv(model_path, encoding="utf-8-sig")
                if "knee_angle_deg" not in df_m.columns:
                    messagebox.showerror("Format error",
                        "Model CSV must have a 'knee_angle_deg' column.", parent=self)
                    return
                t_m_raw  = df_m["time_sec"].values.astype(float)
                t_m_raw -= t_m_raw[0]
                angs_m   = df_m["knee_angle_deg"].values.astype(float)
        except Exception as e:
            messagebox.showerror("Load error", f"Model CSV:\n{e}", parent=self)
            return

        # ── load OptiTrack angles ─────────────────────────────────────────────
        opti_path = self._optitrack_path_var.get().strip()
        if not opti_path or not os.path.exists(opti_path):
            messagebox.showerror("File not found",
                                 "OptiTrack CSV not found.", parent=self)
            return
        try:
            t_o_raw, angs_o = load_optitrack(opti_path)
        except Exception as e:
            messagebox.showerror("Load error", f"OptiTrack CSV:\n{e}", parent=self)
            return

        # ── run PT params on both ─────────────────────────────────────────────
        self._status("Computing PT parameters…")
        self.update()

        # For the model (video-tracker), detect release as the LOCAL MAXIMUM
        # just before the pendulum's first sustained downswing.  Velocity-based
        # detection fires too early on the small pre-release repositioning bump
        # (examiner briefly lifts the leg before letting go).  Looking for the
        # rightmost angle maximum before a drop of >15° correctly identifies the
        # held-and-extended position, matching the OptiTrack's 180° hold.
        def _drop_release_idx(t, ang, min_t=1.0, drop_thresh=15.0,
                               look_back_s=1.0):
            from scipy.ndimage import median_filter as _mf_d
            fps   = max(15.0, (len(t) - 1) / max(t[-1] - t[0] + 1e-6, 0.1))
            clean = _mf_d(
                np.nan_to_num(ang.astype(float),
                              nan=float(np.nanmedian(ang))),
                size=max(3, int(fps * 0.2)))
            min_i = int(np.searchsorted(t, t[0] + min_t))
            lb    = max(8, int(fps * look_back_s))
            for i in range(min_i + lb, len(t)):
                seg     = clean[max(0, i - lb): i + 1]
                max_val = float(seg.max())
                if max_val - float(seg[-1]) > drop_thresh:
                    # Find the RIGHTMOST frame at/near the maximum so that long
                    # flat pre-release sections (e.g. 180° plateau) return the
                    # frame immediately before the drop, not the section start.
                    rev    = seg[::-1]
                    pk_off = int(np.argmax(rev >= max_val - 0.5))
                    pk_i   = len(seg) - 1 - pk_off
                    gp     = max(0, i - lb) + pk_i
                    return max(0, gp)
            return None  # fall back to velocity-based

        def _vel_release_idx(t, ang, min_t=1.0, vel_thresh=80.0):
            from scipy.ndimage import median_filter as _mf_v
            dadt  = _mf_v(np.abs(np.gradient(ang, t)), size=7)
            min_i = int(np.searchsorted(t, t[0] + min_t))
            for i in range(min_i, len(t) - 3):
                if dadt[i] > vel_thresh and dadt[i + 1] > vel_thresh * 0.5:
                    return max(0, i - 1)
            return None

        _m_ang   = np.array(angs_m, dtype=float)
        _m_rel_i = _drop_release_idx(t_m_raw, _m_ang)
        if _m_rel_i is None:
            _m_rel_i = _vel_release_idx(t_m_raw, _m_ang)
        params_m = compute_pt_params(t_m_raw, _m_ang, release_idx=_m_rel_i)
        params_o = compute_pt_params(t_o_raw, angs_o)

        # ── alignment ─────────────────────────────────────────────────────────
        align_mode = self._align_var.get()
        actual_t_rel_m = float(params_m["t_r"][0]) if (params_m and "t_r" in params_m) else None
        actual_t_rel_o = float(params_o["t_r"][0]) if (params_o and "t_r" in params_o) else None
        xc_lag_s = 0.0  # reported in plot title for auto_sync

        if align_mode == "raw_time":
            # Both recordings started simultaneously — use original timestamps (both at t=0)
            t_m = t_m_raw.copy()
            t_o = t_o_raw.copy()

        elif align_mode == "auto_sync":
            # Cross-correlate POST-RELEASE portions only, then convert the result
            # back to an absolute time shift for t_o.
            #
            # Reason: the model's pre-release angle (~105°) and OptiTrack's forced
            # 180° baseline are completely different.  Correlating the full signal
            # matches the flat pre-release constants against each other and returns
            # the wrong lag.  Post-release oscillations share the same frequency
            # and waveform shape, so correlation there is reliable.
            from scipy.signal import correlate as _xcorr
            _dt = 0.02  # 50 Hz interpolation grid
            _m_start = actual_t_rel_m if actual_t_rel_m is not None else 0.0
            _o_start = actual_t_rel_o if actual_t_rel_o is not None else 0.0
            _t_post  = min(t_m_raw[-1] - _m_start, t_o_raw[-1] - _o_start)
            if _t_post < 2.0:  # too little post-release — fall back to full signal
                _m_start = _o_start = 0.0
                _t_post  = min(t_m_raw[-1], t_o_raw[-1])
            _tg   = np.arange(0.0, _t_post, _dt)
            # Interpolate each signal in its own post-release coordinate (t=0 = release)
            _am_g = np.interp(_tg, t_m_raw - _m_start, _m_ang)
            _ao_g = np.interp(_tg, t_o_raw - _o_start, angs_o)
            _am_n = (_am_g - np.nanmean(_am_g)) / (np.nanstd(_am_g) + 1e-9)
            _ao_n = (_ao_g - np.nanmean(_ao_g)) / (np.nanstd(_ao_g) + 1e-9)
            _corr = _xcorr(_am_n, _ao_n, mode='full')
            _mid  = len(_am_n) - 1
            _max_lag = int(3.0 / _dt)
            _lo  = max(0, _mid - _max_lag)
            _hi  = min(len(_corr), _mid + _max_lag + 1)
            _best_k = int(np.argmax(_corr[_lo:_hi])) + _lo - _mid
            xc_lag_s = float(_best_k * _dt)
            # Total absolute shift of t_o:
            #   abs_shift = post-release lag + (model_release_abs - opti_release_abs)
            # This moves OptiTrack so that its release event aligns with the model's,
            # then applies any residual oscillation-phase jitter found by correlation.
            abs_shift = xc_lag_s + _m_start - _o_start
            t_m = t_m_raw.copy()
            t_o = t_o_raw + abs_shift
            if actual_t_rel_o is not None:
                actual_t_rel_o = actual_t_rel_o + abs_shift
            xc_lag_s = abs_shift  # report total applied shift in plot title

        else:  # "release": shift each curve so its detected release = t=0
            t_m = t_m_raw - (actual_t_rel_m if actual_t_rel_m is not None else 0.0)
            t_o = t_o_raw - (actual_t_rel_o if actual_t_rel_o is not None else 0.0)

        a_m = np.array(angs_m, dtype=float)
        a_o = np.array(angs_o, dtype=float)

        # ── interpolate model onto OptiTrack grid (overlap region only) ───────
        t_start = max(t_m[np.isfinite(a_m)][0]  if np.any(np.isfinite(a_m)) else t_m[0],
                      t_o[np.isfinite(a_o)][0]  if np.any(np.isfinite(a_o)) else t_o[0])
        t_end   = min(t_m[np.isfinite(a_m)][-1] if np.any(np.isfinite(a_m)) else t_m[-1],
                      t_o[np.isfinite(a_o)][-1] if np.any(np.isfinite(a_o)) else t_o[-1])

        if t_end <= t_start + 0.5:
            messagebox.showwarning("No overlap",
                "The two time series do not overlap.\n"
                "Check that both files cover the same pendulum trial.", parent=self)
            return

        # Common grid at 100 Hz for robust comparison
        t_common = np.arange(t_start, t_end, 0.01)
        a_m_i = np.interp(t_common, t_m, a_m, left=np.nan, right=np.nan)
        a_o_i = np.interp(t_common, t_o, a_o, left=np.nan, right=np.nan)

        ok = np.isfinite(a_m_i) & np.isfinite(a_o_i)
        if ok.sum() < 10:
            messagebox.showwarning("Insufficient overlap",
                "Too few overlapping valid frames to compute RMSE.", parent=self)
            return

        diff   = a_m_i[ok] - a_o_i[ok]
        rmse   = float(np.sqrt(np.mean(diff ** 2)))
        bias   = float(np.mean(diff))
        r      = float(np.corrcoef(a_m_i[ok], a_o_i[ok])[0, 1])

        mas_m = pt_to_mas(compute_pt_score_simple(params_m)) if params_m else "—"
        mas_o = pt_to_mas(compute_pt_score_simple(params_o)) if params_o else "—"

        self._status(f"RMSE = {rmse:.2f}°  |  r = {r:.3f}  |  bias = {bias:+.2f}°"
                     f"  |  MAS model = {mas_m}  |  MAS optitrack = {mas_o}")

        # ── draw results ──────────────────────────────────────────────────────
        self._draw_plot(t_m, a_m, t_o, a_o, t_common, a_m_i, a_o_i,
                        rmse, r, bias, align_mode, actual_t_rel_m, actual_t_rel_o,
                        xc_lag_s)
        self._show_metrics(rmse, r, bias, mas_m, mas_o, ok.sum())
        self._show_param_table(params_m, params_o, HEALTHY_REF)

    # ── plot ──────────────────────────────────────────────────────────────────

    def _draw_plot(self, t_m, a_m, t_o, a_o, t_c, a_m_i, a_o_i, rmse, r, bias,
                   align_mode="release", t_rel_m=None, t_rel_o=None, xc_lag_s=0.0):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        BDR = self._BDR; SFC = self._SFC; FG2 = self._FG2; FG3 = self._FG3

        # Destroy placeholder and any old canvas
        self._plot_placeholder.pack_forget()
        if self._pc is not None:
            self._pc.get_tk_widget().destroy()

        self._fig = Figure(figsize=(9, 3.2), facecolor=SFC,
                           tight_layout={"pad": 0.4})
        ax = self._fig.add_subplot(111)
        ax.set_facecolor(SFC)
        ax.tick_params(colors=FG2, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(BDR)
        ax.set_xlabel("Time (s)" if align_mode in ("raw_time", "auto_sync")
                      else "Time from release (s)", color=FG3, fontsize=8)
        ax.set_ylabel("Knee angle (°)", color=FG3, fontsize=8)
        ax.set_ylim(0, 185)
        ax.autoscale(enable=False, axis="y")
        ax.grid(True, color="#E2E8F0", linewidth=0.6)

        # Full raw traces (faint, before release shown in lighter shade)
        ax.plot(t_m, a_m, color=self._BLUE, lw=0.8, alpha=0.25, zorder=1)
        ax.plot(t_o, a_o, color=self._GOLD, lw=0.8, alpha=0.25, zorder=1)

        # Post-release interpolated (vivid)
        ax.plot(t_c, a_o_i, color=self._GOLD, lw=2.0, label="OptiTrack", zorder=3)
        ax.plot(t_c, a_m_i, color=self._BLUE, lw=1.8, label="Model",
                linestyle="--", zorder=4)

        # Release markers
        if align_mode in ("raw_time", "auto_sync"):
            if t_rel_m is not None:
                ax.axvline(t_rel_m, color=self._BLUE, lw=1.0, ls=":", alpha=0.7, zorder=2)
                ax.text(t_rel_m + 0.05, ax.get_ylim()[1] - 3, "rel (model)",
                        color=self._BLUE, fontsize=6.5, va="top", zorder=3)
            if t_rel_o is not None:
                ax.axvline(t_rel_o, color=self._GOLD, lw=1.0, ls=":", alpha=0.7, zorder=2)
                ax.text(t_rel_o + 0.05, ax.get_ylim()[1] - 11, "rel (optitrack)",
                        color=self._GOLD, fontsize=6.5, va="top", zorder=3)
        else:
            ax.axvline(0, color="#94A3B8", lw=1.0, ls=":", alpha=0.7, zorder=2)
            ax.text(0.05, ax.get_ylim()[1] - 3, "release",
                    color="#94A3B8", fontsize=7, va="top", zorder=3)

        # RMSE annotation
        ax.text(0.98, 0.04,
                f"RMSE = {rmse:.2f}°   r = {r:.3f}   bias = {bias:+.2f}°",
                transform=ax.transAxes, ha="right", va="bottom",
                fontsize=8, color=self._FG2,
                bbox=dict(boxstyle="round,pad=0.3", fc=SFC, ec=BDR, alpha=0.9))

        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
        if align_mode == "raw_time":
            title_suffix = "raw time"
        elif align_mode == "auto_sync":
            title_suffix = f"auto-sync (OptiTrack offset {xc_lag_s:+.2f} s)"
        else:
            title_suffix = "aligned at release"
        ax.set_title(f"Knee Angle: Model vs OptiTrack ({title_suffix})",
                     color=self._FG, fontsize=9, pad=4)

        self._pc = FigureCanvasTkAgg(self._fig, master=self._plot_frame)
        self._pc.get_tk_widget().pack(fill="both", expand=True)
        self._pc.draw()

        # Lock y-axis on resize; tight_layout is automatic via Figure constructor.
        # add=True keeps matplotlib's own Configure handler (buffer resize) intact.
        def _on_resize(event, pc=self._pc, _ax=ax):
            if event.width < 80 or event.height < 40:
                return
            _ax.set_ylim(0, 185)
            _ax.autoscale(enable=False, axis="y")
            pc.draw_idle()

        self._pc.get_tk_widget().bind("<Configure>", _on_resize, add=True)

    # ── metrics cards ─────────────────────────────────────────────────────────

    def _show_metrics(self, rmse, r, bias, mas_m, mas_o, n_pts):
        SFC = self._SFC; FG = self._FG; FG2 = self._FG2; FG3 = self._FG3
        MONO = self._MONO

        for w in list(self._metrics_frame.winfo_children())[1:]:
            w.destroy()

        def _card(label, val, color):
            row = tk.Frame(self._metrics_frame, bg=SFC)
            row.pack(fill="x", pady=4)
            tk.Label(row, text=label, bg=SFC, fg=FG3,
                     font=("Segoe UI", 8), anchor="w").pack(anchor="w")
            tk.Label(row, text=val, bg=SFC, fg=color,
                     font=(MONO, 14, "bold"), anchor="w").pack(anchor="w")

        # RMSE colour: green <5°, orange <10°, red ≥10°
        rmse_col = (self._GRN if rmse < 5 else
                    "#D97706" if rmse < 10 else self._RED)
        _card("RMSE", f"{rmse:.2f}°", rmse_col)
        _card("Pearson r", f"{r:.3f}", self._BLUE)
        _card("Bias (model−ref)", f"{bias:+.2f}°",
              self._RED if abs(bias) > 5 else FG2)
        _card("Points compared", f"{n_pts}", FG2)

        # MAS comparison
        ttk.Separator(self._metrics_frame, orient="horizontal").pack(
            fill="x", pady=6)
        mas_match = mas_m == mas_o
        mas_color = self._GRN if mas_match else self._RED
        tk.Label(self._metrics_frame, text="MAS ESTIMATE",
                 bg=SFC, fg=FG3, font=("Segoe UI", 7, "bold")).pack(anchor="w")
        mas_row = tk.Frame(self._metrics_frame, bg=SFC)
        mas_row.pack(fill="x", pady=2)
        tk.Label(mas_row, text=f"Model: {mas_m}", bg=SFC, fg=self._BLUE,
                 font=(MONO, 11, "bold")).pack(anchor="w")
        tk.Label(mas_row, text=f"OptiTrack: {mas_o}", bg=SFC, fg=self._GOLD,
                 font=(MONO, 11, "bold")).pack(anchor="w")
        tk.Label(mas_row, text="✓ Match" if mas_match else "✗ Mismatch",
                 bg=SFC, fg=mas_color,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(2, 0))

    # ── parameter table ───────────────────────────────────────────────────────

    def _show_param_table(self, params_m, params_o, healthy_ref):
        SFC = self._SFC; FG = self._FG; FG2 = self._FG2; FG3 = self._FG3
        BDR = self._BDR; MONO = self._MONO

        for w in list(self._table_frame.winfo_children())[1:]:
            w.destroy()

        tbl = tk.Frame(self._table_frame, bg=SFC)
        tbl.pack(fill="both", expand=True, pady=4)

        cols = ["Parameter", "Model", "OptiTrack", "Δ (model−ref)", "Healthy ref"]
        col_w = [130, 80, 80, 100, 90]

        # Header
        for ci, (c, cw) in enumerate(zip(cols, col_w)):
            tk.Label(tbl, text=c, bg="#F1F5F9", fg=FG3,
                     font=("Segoe UI", 7, "bold"),
                     width=cw // 7, anchor="w",
                     relief="flat", padx=4, pady=3).grid(
                row=0, column=ci, sticky="ew", padx=1, pady=1)

        for ri, (key, label, unit) in enumerate(self._PARAM_ROWS, start=1):
            v_m = params_m.get(key) if params_m else None
            v_o = params_o.get(key) if params_o else None
            v_h = healthy_ref.get(key)

            def _fmt(v):
                if v is None or (isinstance(v, float) and not math.isfinite(v)):
                    return "—"
                return f"{float(v):.3f}" if abs(float(v)) < 1000 else f"{float(v):.1f}"

            delta_str = "—"
            delta_col = FG2
            if v_m is not None and v_o is not None:
                try:
                    d = float(v_m) - float(v_o)
                    delta_str = f"{d:+.3f}{unit}"
                    delta_col = self._RED if abs(d) > 0.15 * max(abs(float(v_o)), 1e-9) \
                                else self._GRN
                except Exception:
                    pass

            row_bg = "#F8FAFC" if ri % 2 == 0 else SFC
            vals = [label + (f"  ({unit})" if unit else ""),
                    _fmt(v_m), _fmt(v_o), delta_str, _fmt(v_h)]
            fg_list = [FG, self._BLUE, self._GOLD, delta_col, FG3]

            for ci, (txt, fg, cw) in enumerate(zip(vals, fg_list, col_w)):
                tk.Label(tbl, text=txt, bg=row_bg, fg=fg,
                         font=(MONO if ci > 0 else "Segoe UI", 8),
                         anchor="w", padx=4, pady=2,
                         width=cw // 7).grid(
                    row=ri, column=ci, sticky="ew", padx=1)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _status(self, msg: str):
        self._status_lbl.config(text=msg)
        self._status_lbl.update()


# ─── entry point ─────────────────────────────────────────────────────────────

def main():
    # Opt into per-monitor DPI awareness so Windows renders text at native
    # resolution instead of blurring a scaled bitmap.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Pendulastic Wartenberg Test Viewer")
    ap.add_argument("video", nargs="?", help="Optional video path to open on launch")
    ap.add_argument(
        "--coefficients", metavar="JSON",
        help="Path to calibration_coefficients*.json (e.g. calibration_coefficients_lying_down.json). "
             "Overrides the sklearn pkl model for this session.")
    args = ap.parse_args()

    # Load position-stratified polynomial coefficients when --coefficients is given.
    global _POLY_COEFFS
    if args.coefficients:
        import json as _json
        _coeff_path = os.path.abspath(args.coefficients)
        try:
            with open(_coeff_path, encoding="utf-8") as _f:
                _cd = _json.load(_f)
            _POLY_COEFFS = _cd["coefficients"]
            _group = _cd.get("position_group", "?")
            print(f"[Calibration] Loaded polynomial coefficients ({_group}): "
                  f"{_cd['coefficients']}  [{_coeff_path}]")
        except Exception as _e:
            print(f"[Calibration] WARNING: could not load --coefficients {_coeff_path}: {_e}")

    PendulaticViewer(initial_video=args.video).mainloop()


if __name__ == "__main__":
    main()
