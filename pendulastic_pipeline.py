"""
pendulastic_pipeline.py
=======================
Unified inference + evaluation pipeline for the Pendulastic project.

HOW TO ADD A NEW PARTICIPANT
-----------------------------
1. Drop raw videos into:
       Recordings/Participant_N_<side>_<mode>/Position_P/Height_H/Trial_T.avi
2. Drop OptiTrack CSVs into:
       OptiTrack_Recordings/Participant_N/Position_P/Height_H/trial_T_optitrack.csv
3. Add the participant's pid substrings to TARGET_PARTICIPANTS (or clear it for all).
4. Run:
       .venv\\Scripts\\python.exe -u pendulastic_pipeline.py

ENVIRONMENT ROUTING
-------------------
  YOLO / DETRPose       -> in-process  (.venv)
  HRNet-W48             -> subprocess  (openmmlab conda env)
  RTMPose S / M / L     -> subprocess  (.venv via rtmpose_worker.py)

PHASES
------
  1  Inference    -- writes CSV per (participant, position, trial, model)
  2  Annotate     -- skeleton overlay video (_annotated.mp4)
  3  Compare      -- knee-angle vs time plots + RMSE leaderboard
"""
from __future__ import annotations

import csv
import dataclasses
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial.transform import Rotation as _R

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR   = r"C:\Users\cladi\Pendulastic"
MODELS_DIR = os.path.join(BASE_DIR, "models")
OUT_DIR    = os.path.join(BASE_DIR, "Model_Analysis_Outputs")

VIDEO_ROOT = os.path.join(BASE_DIR, "Recordings")
OPTI_ROOT  = os.path.join(BASE_DIR, "OptiTrack_Recordings")

# ── Participant filter ────────────────────────────────────────────────────────
# Substring match against the pid extracted from the folder name.
# [] = all participants.  Set to specific variants to restrict this run.
TARGET_PARTICIPANTS: List[str] = []  # [] = all participants

# ── Interactive review ────────────────────────────────────────────────────────
# When True, each MediaPipe trial opens a fitting-review window after inference
# so the user can verify joint detection, adjust positions, and set the start
# frame before the CSV is accepted.  Set False for unattended batch runs.
INTERACTIVE_REVIEW: bool = False

# ── Python executables ───────────────────────────────────────────────────────
_VENV_PY      = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
_OPENMMLAB_PY = r"C:\Users\cladi\miniconda3\envs\openmmlab\python.exe"

# ── Local model weight paths ─────────────────────────────────────────────────
_YOLO_PT   = os.path.join(MODELS_DIR, "yolo26n-pose.pt")

_DETR_DIR  = os.path.join(MODELS_DIR, "DETRPose-main")
_DETR_CFG  = os.path.join(_DETR_DIR, "configs", "detrpose", "detrpose_hgnetv2_s.py")
_DETR_WTS  = os.path.join(_DETR_DIR, "weights", "detrpose_hgnetv2_s.pth")

_HRNET_WRK  = os.path.join(BASE_DIR, "hrnet_worker.py")
_HRNET_CFG  = (r"C:\Users\cladi\mmpose\configs\body_2d_keypoint\topdown_heatmap"
               r"\coco\td-hm_hrnet-w48_8xb32-210e_coco-256x192.py")
_HRNET_CKPT = os.path.join(MODELS_DIR, "PosePipeline", "checkpoints",
                            "td-hm_hrnet-w48_8xb32-210e_coco-256x192-0e67c616_20220913.pth")

_VITPOSE_MMPOSE_WRK  = os.path.join(BASE_DIR, "vitpose_mmpose_worker.py")
_VITPOSE_MMPOSE_CFG  = (r"C:\Users\cladi\mmpose\configs\body_2d_keypoint\topdown_heatmap"
                        r"\coco\td-hm_ViTPose-base_8xb64-210e_coco-256x192.py")
_VITPOSE_MMPOSE_CKPT = os.path.join(MODELS_DIR, "vitpose",
                                    "td-hm_ViTPose-base_8xb64-210e_coco-256x192-216eae50_20230314.pth")

_RTMP_ROOT   = os.path.join(MODELS_DIR, "rtmpose", "20230831", "rtmpose_onnx")
_RTMP_S_ONNX = os.path.join(_RTMP_ROOT,
    "rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504", "end2end.onnx")
_RTMP_M_ONNX = os.path.join(_RTMP_ROOT,
    "rtmpose-m_simcc-body7_pt-body7_420e-256x192-e48f03d0_20230504", "end2end.onnx")
_RTMP_L_ONNX = os.path.join(_RTMP_ROOT,
    "rtmpose-l_simcc-body7_pt-body7_420e-256x192-4dba18fc_20230504", "end2end.onnx")
_RTMP_WRK    = os.path.join(BASE_DIR, "rtmpose_worker.py")   # also handles ViTPose/RTMO

# ── ViTPose (PosePipeline) — ONNX downloaded from HuggingFace once ───────────
_VITPOSE_DET_URL = ("https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
                    "yolox_m_8xb8-300e_humanart-c2c7a14a.zip")
_VITPOSE_URLS = {
    "b": "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/coco_25/vitpose-b-coco_25.onnx",
    "l": "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/coco_25/vitpose-l-coco_25.onnx",
}

# ── RTMO (MMPose one-stage) — ONNX downloaded once ───────────────────────────
_RTMO_URLS = {
    "s": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-s_8xb32-700e_body7-640x640-dac2bf74_20231211.zip",
    "m": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip",
    "l": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-l_16xb16-600e_body7-640x640-b5bf8f02_20231211.zip",
}

# ── MediaPipe — local .task files ────────────────────────────────────────────
_MP_DIR   = os.path.join(MODELS_DIR, "mediapipe")
_MP_TASKS = {
    "lite":  os.path.join(_MP_DIR, "pose_landmarker_lite.task"),
    "full":  os.path.join(_MP_DIR, "pose_landmarker_full.task"),
    "heavy": os.path.join(_MP_DIR, "pose_landmarker_heavy.task"),
}
_MP_WRK = os.path.join(BASE_DIR, "mediapipe_worker.py")

# ── Pose2Sim — rtmlib.Body worker ────────────────────────────────────────────
_P2S_WRK = os.path.join(BASE_DIR, "pose2sim_worker.py")

# ── OpenPose — pre-built Windows binary (Body25) ─────────────────────────────
_OP_BIN    = os.path.join(MODELS_DIR, "openpose", "bin", "OpenPoseDemo.exe")
_OP_MODELS = os.path.join(MODELS_DIR, "openpose", "models")

os.makedirs(OUT_DIR, exist_ok=True)

# =============================================================================
# SHARED CONSTANTS & HELPERS
# =============================================================================

L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

CSV_FIELDS = [
    "frame", "time_sec", "leg",
    "hip_x",   "hip_y",   "hip_score",
    "knee_x",  "knee_y",  "knee_score",
    "ankle_x", "ankle_y", "ankle_score",
    "knee_angle_deg",
]


def _angle_deg(a, b, c) -> float:
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1, 1)))


def _nan_row(fi: int, t: float) -> dict:
    return {"frame": fi, "time_sec": round(t, 6), "leg": "none",
            "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
            "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
            "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
            "knee_angle_deg": float("nan")}


def _write_csv(rows: list, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)


def _leg_side_from_pid(pid: str) -> Optional[str]:
    lo = pid.lower()
    if "left" in lo:  return "left"
    if "right" in lo: return "right"
    return None


def _is_duo_from_pid(pid: str) -> bool:
    return True  # assessor is present in every clinical video


def _pick_leg(kp, sc, leg_side: Optional[str]):
    """Returns (side, hip_xy, hip_sc, knee_xy, knee_sc, ankle_xy, ankle_sc)."""
    if leg_side == "left":
        return "left",  kp[L_HIP], sc[L_HIP], kp[L_KNEE], sc[L_KNEE], kp[L_ANKLE], sc[L_ANKLE]
    if leg_side == "right":
        return "right", kp[R_HIP], sc[R_HIP], kp[R_KNEE], sc[R_KNEE], kp[R_ANKLE], sc[R_ANKLE]
    lc = float(sc[L_HIP]) + float(sc[L_KNEE]) + float(sc[L_ANKLE])
    rc = float(sc[R_HIP]) + float(sc[R_KNEE]) + float(sc[R_ANKLE])
    if lc >= rc:
        return "left",  kp[L_HIP], sc[L_HIP], kp[L_KNEE], sc[L_KNEE], kp[L_ANKLE], sc[L_ANKLE]
    return "right", kp[R_HIP], sc[R_HIP], kp[R_KNEE], sc[R_KNEE], kp[R_ANKLE], sc[R_ANKLE]


# ---------------------------------------------------------------------------
# Tracking selection helper
# ---------------------------------------------------------------------------

def _load_sel(video_path: str) -> dict:
    """Return the tracking_selections.json entry for this video, or {}."""
    import json as _json
    sel_file = os.path.join(BASE_DIR, "tracking_selections.json")
    if not os.path.exists(sel_file):
        return {}
    try:
        rel = os.path.relpath(os.path.abspath(video_path), BASE_DIR).replace("\\", "/")
        with open(sel_file, encoding="utf-8") as f:
            return _json.load(f).get(rel, {})
    except Exception:
        return {}


def _compute_crop_region(sel: dict, w: int, h: int) -> Optional[tuple]:
    """
    Compute (cx1, cy1, cx2, cy2) from hip/knee/ankle normalised selections.
    Uses the same region as the mediapipe guided crop: bounding box of hip,
    knee, resting ankle, and hanging ankle (directly below knee), plus 25 %
    lower-leg margin.  Returns None if any joint click is missing.
    """
    hxn = sel.get("hip_x_norm") or sel.get("click_x_norm")
    hyn = sel.get("hip_y_norm") or sel.get("click_y_norm")
    kxn = sel.get("knee_x_norm")
    kyn = sel.get("knee_y_norm")
    axn = sel.get("ankle_x_norm")
    ayn = sel.get("ankle_y_norm")
    if None in (hxn, hyn, kxn, kyn, axn, ayn):
        return None
    hx, hy = hxn * w, hyn * h
    kx, ky = kxn * w, kyn * h
    ax, ay = axn * w, ayn * h
    leg_len  = float(np.linalg.norm([ax - kx, ay - ky]))
    hang_x, hang_y = kx, ky + leg_len
    pts = np.array([[hx, hy], [kx, ky], [ax, ay], [hang_x, hang_y]])
    margin = max(leg_len * 0.25, 30)
    cx1 = max(0, int(pts[:, 0].min() - margin))
    cy1 = max(0, int(pts[:, 1].min() - margin))
    cx2 = min(w, int(pts[:, 0].max() + margin))
    cy2 = min(h, int(pts[:, 1].max() + margin))
    return (cx1, cy1, cx2, cy2)


def _ensure_crop_video(video_path: str) -> Optional[str]:
    """
    If tracking_selections.json has all three joint clicks for this video,
    create a cropped .avi covering only the patient's leg arc and return its
    path.  The cropped file is cached next to the original; if it already
    exists it is returned immediately without re-encoding.

    Returns None when no selections exist or any joint click is missing.
    Angles computed in crop-space are identical to full-frame angles since
    the angle formula is translation-invariant.
    """
    crop_path = os.path.splitext(video_path)[0] + "_crop.avi"
    if os.path.isfile(crop_path):
        return crop_path

    sel = _load_sel(video_path)
    if not sel:
        return None

    cap = cv2.VideoCapture(video_path)
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    crop = _compute_crop_region(sel, w, h)
    if crop is None:
        cap.release()
        return None

    cx1, cy1, cx2, cy2 = crop
    cw, ch = cx2 - cx1, cy2 - cy1
    fourcc = cv2.VideoWriter_fourcc(*"XVID")
    out = cv2.VideoWriter(crop_path, fourcc, fps, (cw, ch))
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        out.write(frame[cy1:cy2, cx1:cx2])
    cap.release()
    out.release()
    print(f"    [crop] ({cx1},{cy1})-({cx2},{cy2})  {os.path.basename(crop_path)}", flush=True)
    return crop_path


# =============================================================================
# KNEE TRACKER  — prevents identity swapping in duo videos
# =============================================================================

class _KneeTracker:
    """
    Tracks the target person across frames by knee-position continuity.
    First frame: picks by designated-leg confidence.
    Subsequent frames: picks the person nearest to the last known knee pixel.
    Falls back to confidence if the nearest candidate jumped too far (detection gap).
    """

    def __init__(self, leg_side: Optional[str], max_jump_px: float = 150.0):
        self.leg_side    = leg_side
        self.max_jump_px = max_jump_px
        self._last_knee: Optional[np.ndarray] = None
        self._knee_idx   = L_KNEE if leg_side != "right" else R_KNEE

    def reset(self):
        self._last_knee = None

    def pick(self, kp_list: list, sc_list: list) -> int:
        """
        kp_list : list-of-(17,2) float arrays  (one per detected person)
        sc_list : list-of-(17,) float arrays   (per-keypoint scores)
        Returns the index of the chosen person.
        """
        n = len(kp_list)
        if n == 0:
            return 0
        if n == 1:
            self._update(kp_list[0])
            return 0

        def _leg_conf(i) -> float:
            sc = sc_list[i]
            if self.leg_side == "left":
                return float(sc[L_HIP]) + float(sc[L_KNEE]) + float(sc[L_ANKLE])
            if self.leg_side == "right":
                return float(sc[R_HIP]) + float(sc[R_KNEE]) + float(sc[R_ANKLE])
            return float(np.mean(sc))

        if self._last_knee is None:
            best = max(range(n), key=_leg_conf)
        else:
            dists = [np.linalg.norm(kp_list[i][self._knee_idx] - self._last_knee)
                     for i in range(n)]
            best = int(np.argmin(dists))
            if dists[best] > self.max_jump_px:
                best = max(range(n), key=_leg_conf)

        self._update(kp_list[best])
        return best

    def _update(self, kp17):
        self._last_knee = np.asarray(kp17[self._knee_idx], float).copy()


# =============================================================================
# DISCOVERY
# =============================================================================

_VID_RE  = re.compile(r"^trial_(\d+)\.(mp4|avi)$", re.I)
_PID_RE  = re.compile(r"Participant_(\w+)",  re.I)
_POS_RE  = re.compile(r"Position_(\w+)",    re.I)
_HGT_RE  = re.compile(r"Height_(.+)",       re.I)
_OPTI_RE = re.compile(r"trial_(\d+)_optitrack\.csv$", re.I)


def _path_meta(path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    pid = pos = height = None
    for part in path.replace("\\", "/").split("/"):
        m = _PID_RE.match(part);  pid    = m.group(1) if m else pid
        m = _POS_RE.match(part);  pos    = m.group(1) if m else pos
        m = _HGT_RE.match(part);  height = m.group(1) if m else height
    return pid, pos, height


def discover_videos() -> List[dict]:
    found: List[dict] = []
    seen:  set        = set()
    for ext in ("*.mp4", "*.avi"):
        for path in glob.glob(os.path.join(VIDEO_ROOT, "**", ext), recursive=True):
            bn = os.path.basename(path)
            m  = _VID_RE.match(bn)
            if not m:
                continue
            trial = m.group(1)
            pid, pos, height = _path_meta(path)
            if not (pid and pos and height):
                continue
            if TARGET_PARTICIPANTS and not any(t in pid for t in TARGET_PARTICIPANTS):
                continue
            key = (pid, pos, trial)
            if key in seen:
                continue
            seen.add(key)
            found.append(dict(
                pid=pid, pos=pos, trial=trial, height=height,
                leg_side=_leg_side_from_pid(pid),
                is_duo=_is_duo_from_pid(pid),
                video_path=path,
                out_dir=os.path.dirname(path),
            ))
    found.sort(key=lambda v: (v["pid"], v["pos"], int(v["trial"])))
    return found


def find_optitrack(pid: str, pos: str, trial: str) -> Optional[str]:
    for path in glob.glob(os.path.join(OPTI_ROOT, "**", "*.csv"), recursive=True):
        m = _OPTI_RE.search(os.path.basename(path))
        if not m or m.group(1) != trial:
            continue
        p, ps, _ = _path_meta(path)
        if p == pid and ps == pos:
            return path
    return None


def _csv_name(vid: dict, entry: "ModelEntry") -> str:
    return (f"P_{vid['pid']}_Pos_{vid['pos']}_H_{vid['height']}"
            f"_T_{vid['trial']}_{entry.family}_{entry.variant}_{entry.thresh}.csv")


# =============================================================================
# RUNNER FUNCTIONS
# =============================================================================

def run_yolo(video_path: str, csv_path: str, **params) -> None:
    from ultralytics import YOLO as _YOLO
    model_path   = params["model_path"]
    score_thresh = float(params.get("score_thresh", 0.5))
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    frame_step   = int(params.get("frame_step", 5))

    model   = _YOLO(model_path)
    tracker = _KneeTracker(leg_side)
    cap     = cv2.VideoCapture(video_path)
    fps     = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    YOLO | {total} fr @ {fps:.0f}fps | step={frame_step} -> {fps/frame_step:.1f}fps"
          f"  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)

    sel         = _load_sel(video_path)
    start_frame = int(sel.get("start_frame", 0))
    cxn = sel.get("click_x_norm")
    cyn = sel.get("click_y_norm")
    if sel:
        print(f"    [selection] start_frame={start_frame}  click={cxn is not None}", flush=True)
    if cxn is not None:
        tracker._last = np.array([cxn * int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                   cyn * int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))])

    rows: list = []
    fi = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if fi % frame_step == 0:
            t   = fi / fps
            if fi < start_frame:
                rows.append(_nan_row(fi, t))
                fi += 1
                continue
            res = model(frame, verbose=False, conf=score_thresh)[0]
            kpd = (res.keypoints.data.cpu().numpy()
                   if res.keypoints is not None and res.keypoints.data is not None
                   else None)
            if kpd is None or kpd.shape[0] == 0:
                rows.append(_nan_row(fi, t))
            else:
                kp_list = [kpd[i, :, :2] for i in range(kpd.shape[0])]
                sc_list = [kpd[i, :, 2]  for i in range(kpd.shape[0])]
                best    = tracker.pick(kp_list, sc_list) if (is_duo or len(kp_list) > 1) else 0
                kp17    = kpd[best]
                leg, hip, hs, kne, ks, ank, as_ = _pick_leg(
                    kp17[:, :2], kp17[:, 2], leg_side)
                ok3 = hs >= score_thresh and ks >= score_thresh and as_ >= score_thresh
                ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")
                rows.append({"frame": fi, "time_sec": round(t, 6), "leg": leg,
                             "hip_x":   round(float(hip[0]), 2), "hip_y":   round(float(hip[1]), 2), "hip_score":   round(hs, 4),
                             "knee_x":  round(float(kne[0]), 2), "knee_y":  round(float(kne[1]), 2), "knee_score":  round(ks, 4),
                             "ankle_x": round(float(ank[0]), 2), "ankle_y": round(float(ank[1]), 2), "ankle_score": round(as_, 4),
                             "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan")})
            if fi % 150 == 0:
                print(f"      frame {fi}/{total}", flush=True)
        fi += 1
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} sampled frames", flush=True)


def run_detrpose(video_path: str, csv_path: str, **params) -> None:
    import torch, torch.nn as nn
    import torchvision.transforms as T
    from PIL import Image

    detr_dir     = params["detr_dir"]
    config_path  = params["config_path"]
    weight_path  = params["weight_path"]
    score_thresh = float(params.get("score_thresh", 0.5))
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    frame_step   = int(params.get("frame_step", 5))

    if detr_dir not in sys.path:
        sys.path.insert(0, detr_dir)
    from src.core import LazyConfig, instantiate

    class _Deploy(nn.Module):
        def __init__(self, m, p):
            super().__init__(); self.model = m.deploy(); self.postprocessor = p.deploy()
        def forward(self, imgs, sizes):
            return self.postprocessor(self.model(imgs), sizes)

    cfg = LazyConfig.load(config_path)
    if hasattr(cfg.model.backbone, "pretrained"):
        cfg.model.backbone.pretrained = False
    mdl  = instantiate(cfg.model); ppp = instantiate(cfg.postprocessor)
    ckpt = torch.load(weight_path, map_location="cpu", weights_only=False)
    state = ckpt.get("ema", {}).get("module") or ckpt.get("model") or ckpt
    mdl.load_state_dict(state, strict=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    deploy = _Deploy(mdl, ppp).eval().to(device)
    tfm    = T.Compose([T.Resize((640, 640)), T.ToTensor()])

    tracker = _KneeTracker(leg_side)
    cap     = cv2.VideoCapture(video_path)
    fps     = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total   = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    DETRPose | {total} fr @ {fps:.0f}fps | step={frame_step} -> {fps/frame_step:.1f}fps"
          f"  device={device}  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)

    sel         = _load_sel(video_path)
    start_frame = int(sel.get("start_frame", 0))
    cxn = sel.get("click_x_norm")
    cyn = sel.get("click_y_norm")
    if sel:
        print(f"    [selection] start_frame={start_frame}  click={cxn is not None}", flush=True)
    if cxn is not None:
        tracker._last = np.array([cxn * int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                   cyn * int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))])

    rows: list = []; fi = 0
    with torch.no_grad():
        while True:
            ok, frame = cap.read()
            if not ok: break
            if fi % frame_step == 0:
                t     = fi / fps
                if fi < start_frame:
                    rows.append(_nan_row(fi, t))
                    fi += 1
                    continue
                im    = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                w, h  = im.size
                orig  = torch.tensor([[w, h]], device=device)
                inp   = tfm(im).unsqueeze(0).to(device)
                sc_t, _, kp_t = deploy(inp, orig)
                sc_np = sc_t[0].cpu().numpy()   # (N,)
                kp_np = kp_t[0].cpu().numpy()   # (N, 17, 2)
                idx   = np.where(sc_np >= score_thresh)[0]

                if len(idx) == 0:
                    rows.append(_nan_row(fi, t))
                else:
                    # Build per-person (17,2) and scalar confidence lists for tracker
                    kp_list = [kp_np[i] for i in idx]
                    sc_list = [np.full(17, sc_np[i]) for i in idx]
                    pick    = tracker.pick(kp_list, sc_list) if (is_duo or len(kp_list) > 1) else 0
                    kp17    = kp_list[pick]; ps = sc_np[idx[pick]]

                    # Leg selection: use leg_side if set, else pick side with more vertical span
                    if leg_side == "right":
                        leg, hip, kne, ank = "right", kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE]
                    elif leg_side == "left":
                        leg, hip, kne, ank = "left",  kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE]
                    else:
                        l_span = abs(float(kp17[L_HIP][1]) - float(kp17[L_ANKLE][1]))
                        r_span = abs(float(kp17[R_HIP][1]) - float(kp17[R_ANKLE][1]))
                        if r_span > l_span:
                            leg, hip, kne, ank = "right", kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE]
                        else:
                            leg, hip, kne, ank = "left",  kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE]

                    ang = _angle_deg(hip, kne, ank)
                    rows.append({"frame": fi, "time_sec": round(t, 6), "leg": leg,
                                 "hip_x":   round(float(hip[0]), 2), "hip_y":   round(float(hip[1]), 2), "hip_score":   round(float(ps), 4),
                                 "knee_x":  round(float(kne[0]), 2), "knee_y":  round(float(kne[1]), 2), "knee_score":  round(float(ps), 4),
                                 "ankle_x": round(float(ank[0]), 2), "ankle_y": round(float(ank[1]), 2), "ankle_score": round(float(ps), 4),
                                 "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan")})
                if fi % 150 == 0:
                    print(f"      frame {fi}/{total}", flush=True)
            fi += 1
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} sampled frames", flush=True)


def run_hrnet(video_path: str, csv_path: str, **params) -> None:
    leg_side = params.get("leg_side")
    is_duo   = bool(params.get("is_duo", False))
    print(f"    HRNet-W48 | openmmlab subprocess  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)
    cmd = [_OPENMMLAB_PY, "-u", _HRNET_WRK,
           "--video", video_path,
           "--csv",   csv_path,
           "--config", _HRNET_CFG,
           "--ckpt",   _HRNET_CKPT,
           "--score-thresh", "0.3"]
    if leg_side:
        cmd += ["--leg-side", leg_side]
    res = subprocess.run(cmd, capture_output=False, text=True,
                         timeout=7200, env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", OPENBLAS_NUM_THREADS="1"))
    if res.returncode != 0:
        raise RuntimeError(f"hrnet_worker.py exited {res.returncode}")


def run_vitpose_mmpose(video_path: str, csv_path: str, **params) -> None:
    """ViTPose-Base via mmpose PyTorch API in the openmmlab conda subprocess."""
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    score_thresh = float(params.get("score_thresh", 0.3))
    print(f"    ViTPose-Base | openmmlab subprocess  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)
    if not os.path.isfile(_VITPOSE_MMPOSE_CKPT):
        raise FileNotFoundError(
            f"ViTPose checkpoint missing: {_VITPOSE_MMPOSE_CKPT}\n"
            "Run: mim download mmpose --config td-hm_ViTPose-base_8xb64-210e_coco-256x192 "
            "--dest models\\vitpose"
        )
    cmd = [_OPENMMLAB_PY, "-u", _VITPOSE_MMPOSE_WRK,
           "--video",        video_path,
           "--csv",          csv_path,
           "--config",       _VITPOSE_MMPOSE_CFG,
           "--ckpt",         _VITPOSE_MMPOSE_CKPT,
           "--score-thresh", str(score_thresh)]
    if leg_side: cmd += ["--leg-side", leg_side]
    if is_duo:   cmd += ["--is-duo"]
    res = subprocess.run(cmd, capture_output=False, text=True,
                         timeout=7200, env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", OPENBLAS_NUM_THREADS="1"))
    if res.returncode != 0:
        raise RuntimeError(f"vitpose_mmpose_worker.py exited {res.returncode}")


def run_rtmpose(video_path: str, csv_path: str, **params) -> None:
    """RTMPose subprocess via .venv python -> rtmpose_worker.py (rtmlib + onnxruntime)."""
    pose_onnx    = params["pose_onnx"]
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    variant      = os.path.basename(os.path.dirname(pose_onnx))[:10]
    print(f"    RTMPose {variant} | subprocess  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)
    cmd = [_VENV_PY, "-u", _RTMP_WRK,
           "--video",       video_path,
           "--csv",         csv_path,
           "--pose-onnx",   pose_onnx,
           "--score-thresh", str(score_thresh)]
    if leg_side:
        cmd += ["--leg-side", leg_side]
    if is_duo:
        cmd += ["--is-duo"]
    res = subprocess.run(cmd, capture_output=False, text=True,
                         timeout=7200, env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", OPENBLAS_NUM_THREADS="1"))
    if res.returncode != 0:
        raise RuntimeError(f"rtmpose_worker.py exited {res.returncode}")


def run_vitpose(video_path: str, csv_path: str, **params) -> None:
    """ViTPose-B or L via rtmlib (downloads ONNX from HuggingFace once, ~100 MB)."""
    variant      = params.get("variant", "b")
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    print(f"    ViTPose-{variant} | subprocess  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)
    cmd = [_VENV_PY, "-u", _RTMP_WRK,
           "--video",       video_path,
           "--csv",         csv_path,
           "--pose-class",  "ViTPose",
           "--pose-url",    _VITPOSE_URLS[variant],
           "--det-url",     _VITPOSE_DET_URL,
           "--score-thresh", str(score_thresh)]
    if leg_side: cmd += ["--leg-side", leg_side]
    if is_duo:   cmd += ["--is-duo"]
    res = subprocess.run(cmd, capture_output=False, text=True,
                         timeout=7200, env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", OPENBLAS_NUM_THREADS="1"))
    if res.returncode != 0:
        raise RuntimeError(f"rtmlib_worker ViTPose exited {res.returncode}")


def run_rtmo(video_path: str, csv_path: str, **params) -> None:
    """RTMO one-stage pose estimation (no separate detector, downloads ONNX once)."""
    variant      = params.get("variant", "s")
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    print(f"    RTMO-{variant} | subprocess  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)
    cmd = [_VENV_PY, "-u", _RTMP_WRK,
           "--video",       video_path,
           "--csv",         csv_path,
           "--pose-class",  "RTMO",
           "--pose-url",    _RTMO_URLS[variant],
           "--score-thresh", str(score_thresh)]
    if leg_side: cmd += ["--leg-side", leg_side]
    if is_duo:   cmd += ["--is-duo"]
    res = subprocess.run(cmd, capture_output=False, text=True,
                         timeout=7200, env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", OPENBLAS_NUM_THREADS="1"))
    if res.returncode != 0:
        raise RuntimeError(f"rtmlib_worker RTMO exited {res.returncode}")


def run_mediapipe(video_path: str, csv_path: str, **params) -> None:
    """MediaPipe PoseLandmarker (local .task file, 33-point BlazePose)."""
    variant      = params.get("variant", "full")
    score_thresh = float(params.get("score_thresh", 0.5))
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    interactive  = bool(params.get("interactive", INTERACTIVE_REVIEW))
    task_file    = _MP_TASKS[variant]

    def _run_worker():
        print(f"    MediaPipe-{variant} | subprocess  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)
        cmd = [_VENV_PY, "-u", _MP_WRK,
               "--video",        video_path,
               "--csv",          csv_path,
               "--task",         task_file,
               "--score-thresh", str(score_thresh)]
        if leg_side:                  cmd += ["--leg-side", leg_side]
        if is_duo:                    cmd += ["--is-duo"]
        if params.get("guided"):      cmd += ["--guided"]
        res = subprocess.run(cmd, capture_output=False, text=True,
                             timeout=7200,
                             env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE",
                                      OPENBLAS_NUM_THREADS="1"))
        if res.returncode != 0:
            raise RuntimeError(f"mediapipe_worker exited {res.returncode}")

    _run_worker()

    if not interactive:
        return

    # ── Post-trial interactive review ────────────────────────────────────────
    import json as _json
    from trial_review import TrialReviewer

    _sel_file = os.path.join(BASE_DIR, "tracking_selections.json")
    _rel_key  = os.path.relpath(os.path.abspath(video_path), BASE_DIR).replace("\\", "/")
    try:
        with open(_sel_file, encoding="utf-8") as _f:
            _all_sel = _json.load(_f)
    except (FileNotFoundError, ValueError):
        _all_sel = {}
    _cur_sel = _all_sel.get(_rel_key, {})

    while True:
        reviewer = TrialReviewer(video_path, csv_path, _cur_sel)
        result   = reviewer.run()
        reviewer.release()

        if result["action"] == "skip":
            print("    [review] skipped", flush=True)
            break

        # Save updated selection back to tracking_selections.json
        _cur_sel = result["sel"]
        _all_sel[_rel_key] = _cur_sel
        with open(_sel_file, "w", encoding="utf-8") as _f:
            _json.dump(_all_sel, _f, indent=2)
        print(f"    [review] saved: {_cur_sel}", flush=True)

        if result["action"] == "accept":
            break

        if result["action"] == "rerun":
            print("    [review] re-running MediaPipe with updated settings...", flush=True)
            _run_worker()
            # Reload CSV in next loop iteration


def run_pose2sim(video_path: str, csv_path: str, **params) -> None:
    """Pose2Sim 2D step: rtmlib.Body (COCO_17 RTMPose, balanced mode with tracking)."""
    mode         = params.get("mode", "balanced")
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")
    is_duo       = bool(params.get("is_duo", False))
    print(f"    Pose2Sim/Body-{mode} | subprocess  leg={leg_side or 'auto'}  duo={is_duo}", flush=True)
    cmd = [_VENV_PY, "-u", _P2S_WRK,
           "--video",       video_path,
           "--csv",         csv_path,
           "--mode",        mode,
           "--score-thresh", str(score_thresh)]
    if leg_side: cmd += ["--leg-side", leg_side]
    if is_duo:   cmd += ["--is-duo"]
    res = subprocess.run(cmd, capture_output=False, text=True,
                         timeout=7200, env=dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE", OPENBLAS_NUM_THREADS="1"))
    if res.returncode != 0:
        raise RuntimeError(f"pose2sim_worker exited {res.returncode}")


def run_openpose(video_path: str, csv_path: str, **params) -> None:
    """OpenPose Body25 via OpenPoseDemo.exe subprocess → JSON → pipeline CSV.

    Runs at frame_step=10 (3 fps from 30 fps source) because OpenPose is CPU-only
    on this machine. The annotator interpolates missing frames visually.
    Body25 indices: R_HIP=9, R_KNEE=10, R_ANKLE=11, L_HIP=12, L_KNEE=13, L_ANKLE=14.
    """
    if not os.path.isfile(_OP_BIN):
        raise FileNotFoundError(f"OpenPoseDemo.exe not found: {_OP_BIN}")

    leg_side   = params.get("leg_side")
    frame_step = int(params.get("frame_step", 10))

    _B25_L = {"hip": 12, "knee": 13, "ankle": 14}
    _B25_R = {"hip":  9, "knee": 10, "ankle": 11}

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    eff_fps = fps / frame_step
    print(f"    OpenPose B25 | {total} frames @ {fps:.0f}fps | step={frame_step} -> {eff_fps:.1f}fps  leg={leg_side or 'auto'}", flush=True)

    tmp = tempfile.mkdtemp(prefix="op_json_")
    try:
        cmd = [_OP_BIN,
               "--video",          os.path.abspath(video_path),
               "--write_json",     tmp,
               "--model_pose",     "BODY_25",
               "--model_folder",   _OP_MODELS,
               "--net_resolution", "-1x192",
               "--num_gpu",        "0",
               "--frame_step",     str(frame_step),
               "--display",        "0",
               "--render_pose",    "0"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=7200,
                             cwd=os.path.dirname(_OP_BIN))
        if res.returncode != 0:
            raise RuntimeError(f"OpenPoseDemo.exe failed: {(res.stderr or '')[-400:]}")

        json_files = sorted(f for f in os.listdir(tmp) if f.endswith("_keypoints.json"))
        if not json_files:
            raise RuntimeError("OpenPose produced no JSON output files.")

        rows = []
        for i, jf in enumerate(json_files):
            fi = i * frame_step
            t  = fi / fps
            try:
                with open(os.path.join(tmp, jf)) as fh:
                    d = json.load(fh)
                people = d.get("people", [])
                if not people:
                    rows.append(_nan_row(fi, t))
                    continue
                best = max(people, key=lambda p: sum(p["pose_keypoints_2d"][2::3]) /
                           max(1, len(p["pose_keypoints_2d"]) // 3))
                kp25 = np.array(best["pose_keypoints_2d"]).reshape(-1, 3)
            except Exception:
                rows.append(_nan_row(fi, t))
                continue

            def _kp(idx):
                return kp25[idx, :2], float(kp25[idx, 2])

            r_h, r_hs = _kp(_B25_R["hip"]);   r_k, r_ks = _kp(_B25_R["knee"]); r_a, r_as = _kp(_B25_R["ankle"])
            l_h, l_hs = _kp(_B25_L["hip"]);   l_k, l_ks = _kp(_B25_L["knee"]); l_a, l_as = _kp(_B25_L["ankle"])

            use_left = (leg_side == "left") or (leg_side is None and l_hs + l_ks + l_as >= r_hs + r_ks + r_as)
            if use_left:
                leg, hip, h_s, kne, k_s, ank, a_s = "left",  l_h, l_hs, l_k, l_ks, l_a, l_as
            else:
                leg, hip, h_s, kne, k_s, ank, a_s = "right", r_h, r_hs, r_k, r_ks, r_a, r_as

            ok3 = h_s >= 0.1 and k_s >= 0.1 and a_s >= 0.1
            ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")
            rows.append({
                "frame": fi, "time_sec": round(t, 6), "leg": leg,
                "hip_x":   round(float(hip[0]), 2), "hip_y":   round(float(hip[1]), 2), "hip_score":   round(h_s, 4),
                "knee_x":  round(float(kne[0]), 2), "knee_y":  round(float(kne[1]), 2), "knee_score":  round(k_s, 4),
                "ankle_x": round(float(ank[0]), 2), "ankle_y": round(float(ank[1]), 2), "ankle_score": round(a_s, 4),
                "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
            })
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} sampled frames  (eff. {eff_fps:.1f}fps)", flush=True)


_MN_MIN_KP_SCORE = 0.2


def _mn_init_crop(h, w):
    if w > h:
        return {"y_min": (h/2 - w/2)/h, "x_min": 0.0,
                "y_max": (h/2 - w/2)/h + w/h, "x_max": 1.0,
                "height": w/h, "width": 1.0}
    return {"y_min": 0.0, "x_min": (w/2 - h/2)/w,
            "y_max": 1.0, "x_max": (w/2 - h/2)/w + h/w,
            "height": 1.0, "width": h/w}


def _mn_next_crop(kps, h, w):
    """Adaptive crop update following the TF Hub MoveNet notebook."""
    import tensorflow as tf  # noqa: import inside fn to keep startup fast
    torso = [5, 6, 11, 12]
    s = kps[0, 0, :, 2]
    if not (any(s[i] > _MN_MIN_KP_SCORE for i in [11, 12]) and
            any(s[i] > _MN_MIN_KP_SCORE for i in [5, 6])):
        return _mn_init_crop(h, w)
    ky = kps[0, 0, :, 0] * h
    kx = kps[0, 0, :, 1] * w
    cy = (ky[11] + ky[12]) / 2
    cx = (kx[11] + kx[12]) / 2
    max_ty = max(abs(cy - ky[i]) for i in torso)
    max_tx = max(abs(cx - kx[i]) for i in torso)
    mask = s > _MN_MIN_KP_SCORE
    max_by = max((abs(cy - ky[i]) for i in range(17) if mask[i]), default=max_ty)
    max_bx = max((abs(cx - kx[i]) for i in range(17) if mask[i]), default=max_tx)
    half = float(np.amin([np.amax([max_ty*1.9, max_tx*1.9, max_by*1.2, max_bx*1.2]),
                          np.amax([cx, w-cx, cy, h-cy])]))
    if half > max(h, w) / 2:
        return _mn_init_crop(h, w)
    length = half * 2
    return {"y_min": (cy-half)/h, "x_min": (cx-half)/w,
            "y_max": (cy-half+length)/h, "x_max": (cx-half+length)/w,
            "height": length/h, "width": length/w}


def _mn_run_frame(fn, img_np, crop, input_size):
    import tensorflow as tf  # noqa
    h, w = img_np.shape[:2]
    boxes  = [[crop["y_min"], crop["x_min"], crop["y_max"], crop["x_max"]]]
    img_t  = tf.expand_dims(tf.constant(img_np, dtype=tf.int32), 0)
    cropped = tf.image.crop_and_resize(img_t, box_indices=[0], boxes=boxes,
                                        crop_size=[input_size, input_size])
    kps = fn(cropped)
    for i in range(17):
        kps[0, 0, i, 0] = (crop["y_min"] * h + crop["height"] * h * float(kps[0, 0, i, 0])) / h
        kps[0, 0, i, 1] = (crop["x_min"] * w + crop["width"]  * w * float(kps[0, 0, i, 1])) / w
    return kps


def run_movenet(video_path: str, csv_path: str, **params) -> None:
    """MoveNet singlepose (Lightning or Thunder) with TF Hub adaptive cropping.

    Runs every frame (no frame_step) since the model is fast and the adaptive
    crop loop must be continuous to maintain accuracy.
    Hub models are cached by TF Hub after first download (~10 MB each).
    """
    import tensorflow as tf
    import tensorflow_hub as hub

    variant      = params.get("variant", "lightning")
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")

    _HUB_URLS = {
        "lightning": ("https://tfhub.dev/google/movenet/singlepose/lightning/4", 192),
        "thunder":   ("https://tfhub.dev/google/movenet/singlepose/thunder/4",   256),
    }
    if variant not in _HUB_URLS:
        raise ValueError(f"Unknown MoveNet variant '{variant}'. Choose lightning or thunder.")
    hub_url, input_size = _HUB_URLS[variant]

    print(f"    MoveNet/{variant} | loading from TF Hub (cached after first run)  leg={leg_side or 'auto'}", flush=True)
    model  = hub.load(hub_url)
    mn_fn  = model.signatures["serving_default"]

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    crop = _mn_init_crop(h_vid, w_vid)
    rows: list = []
    fi = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = fi / fps
        img_np = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        try:
            kps  = _mn_run_frame(mn_fn, img_np, crop, input_size)
            crop = _mn_next_crop(kps, h_vid, w_vid)
        except Exception:
            rows.append(_nan_row(fi, t)); fi += 1; continue

        kp17 = kps[0, 0]  # (17, 3) normalized (y, x, score)

        def _px(idx):
            y_n, x_n, s = float(kp17[idx, 0]), float(kp17[idx, 1]), float(kp17[idx, 2])
            return np.array([x_n * w_vid, y_n * h_vid]), s

        r_h, r_hs = _px(R_HIP);   r_k, r_ks = _px(R_KNEE); r_a, r_as = _px(R_ANKLE)
        l_h, l_hs = _px(L_HIP);   l_k, l_ks = _px(L_KNEE); l_a, l_as = _px(L_ANKLE)

        use_left = (leg_side == "left") or (leg_side is None and l_hs + l_ks + l_as >= r_hs + r_ks + r_as)
        if use_left:
            leg, hip, h_s, kne, k_s, ank, a_s = "left",  l_h, l_hs, l_k, l_ks, l_a, l_as
        else:
            leg, hip, h_s, kne, k_s, ank, a_s = "right", r_h, r_hs, r_k, r_ks, r_a, r_as

        ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
        ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")
        rows.append({
            "frame": fi, "time_sec": round(t, 6), "leg": leg,
            "hip_x":   round(float(hip[0]), 2), "hip_y":   round(float(hip[1]), 2), "hip_score":   round(h_s, 4),
            "knee_x":  round(float(kne[0]), 2), "knee_y":  round(float(kne[1]), 2), "knee_score":  round(k_s, 4),
            "ankle_x": round(float(ank[0]), 2), "ankle_y": round(float(ank[1]), 2), "ankle_score": round(a_s, 4),
            "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
        })
        fi += 1
        if fi % 300 == 0:
            print(f"    frame {fi}/{total}", flush=True)

    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} frames valid", flush=True)


# =============================================================================
# OPTICAL FLOW TRACKER (Kinovea-style 2D marker tracking)
# =============================================================================

def run_optical_flow(video_path: str, csv_path: str, **params) -> None:
    """
    2D marker tracking using Lucas-Kanade optical flow — the same approach as
    Kinovea.  No body-shape detection needed.  Works for any patient orientation.

    Biological constraints enforced every frame:
      - Hip:   FIXED (never moves — resting on table).
      - Knee:  FIXED pivot (thigh rests on table; only the shank swings).
      - Ankle: tracked on a circle of radius=shank_len from the fixed knee.

    When LK fails (bidirectional error too high), the ankle position is
    extrapolated from the recent shank angular velocity and projected back
    onto the shank circle.
    """
    sel = _load_sel(video_path)
    if not sel.get("knee_x_norm"):
        print(f"    [optflow] no selection for {os.path.basename(video_path)} — skipping", flush=True)
        return

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    hip0   = np.array([sel["hip_x_norm"]   * w_vid, sel["hip_y_norm"]   * h_vid], dtype=np.float32)
    knee0  = np.array([sel["knee_x_norm"]  * w_vid, sel["knee_y_norm"]  * h_vid], dtype=np.float32)
    ankle0 = np.array([sel["ankle_x_norm"] * w_vid, sel["ankle_y_norm"] * h_vid], dtype=np.float32)

    thigh_len = float(np.linalg.norm(knee0  - hip0))
    shank_len = float(np.linalg.norm(ankle0 - knee0))
    start_frame = int(sel.get("start_frame", 0))

    print(f"    [optflow] {total} frames @ {fps:.0f}fps  "
          f"thigh={thigh_len:.0f}px  shank={shank_len:.0f}px", flush=True)

    TEMPL_HALF   = 17
    ARC_DEG_STEP = 0.5
    PRED_WIN_DEG = 25.0
    FULL_WIN_DEG = 80.0
    HIP_EXCL_DEG = 80.0
    MIN_NCC      = 0.30
    TEMPL_ALPHA  = 0.06
    DIR_HIST_LEN = 8

    # Precompute hip direction (knee→hip) to exclude that arc zone
    hip_v   = hip0 - knee0
    hip_dir = math.atan2(float(hip_v[1]), float(hip_v[0]))

    def _project(pt):
        vec  = pt - knee0
        norm = float(np.linalg.norm(vec))
        if norm < 1:
            return knee0 + np.array([0.0, shank_len], dtype=np.float32)
        return knee0 + vec / norm * shank_len

    def _ncc(tmpl, patch):
        t = tmpl  - tmpl.mean()
        p = patch  - patch.mean()
        denom = math.sqrt(float((t*t).sum()) * float((p*p).sum()))
        return float((t * p).sum() / denom) if denom > 1e-6 else 0.0

    def _in_hip_zone(angle):
        diff = (angle - hip_dir + math.pi) % (2 * math.pi) - math.pi
        return abs(diff) < math.radians(HIP_EXCL_DEG)

    def _arc_search(gray, pred_dir, conf, cur_ank, tmpl):
        h, w = gray.shape
        th   = TEMPL_HALF
        st   = math.radians(ARC_DEG_STEP)
        best_score, best_pt = -2.0, cur_ank.copy()

        def _scan(lo, hi):
            nonlocal best_score, best_pt
            a = lo
            while a <= hi + 1e-9:
                if not _in_hip_zone(a):
                    cx = int(round(knee0[0] + math.cos(a) * shank_len))
                    cy = int(round(knee0[1] + math.sin(a) * shank_len))
                    if cx-th >= 0 and cy-th >= 0 and cx+th+1 <= w and cy+th+1 <= h:
                        patch = gray[cy-th:cy+th+1, cx-th:cx+th+1].astype(np.float32)
                        score = _ncc(tmpl, patch)
                        if score > best_score:
                            best_score = score
                            best_pt    = np.array([cx, cy], dtype=np.float32)
                a += st

        win = math.radians(PRED_WIN_DEG if conf > 0.3 else FULL_WIN_DEG)
        _scan(pred_dir - win, pred_dir + win)
        if best_score < MIN_NCC and win < math.radians(FULL_WIN_DEG):
            full = math.radians(FULL_WIN_DEG)
            _scan(pred_dir - full, pred_dir - win)
            _scan(pred_dir + win, pred_dir + full)
        return best_pt, best_score

    ok, first = cap.read()
    if not ok:
        cap.release()
        return
    first_gray = cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)

    ank_pt  = _project(ankle0)
    cur_dir = math.atan2(float(ank_pt[1] - knee0[1]), float(ank_pt[0] - knee0[0]))
    dir_hist = [cur_dir]

    ax, ay = int(round(float(ank_pt[0]))), int(round(float(ank_pt[1])))
    th = TEMPL_HALF
    h0, w0 = first_gray.shape
    if ax-th >= 0 and ay-th >= 0 and ax+th+1 <= w0 and ay+th+1 <= h0:
        template = first_gray[ay-th:ay+th+1, ax-th:ax+th+1].astype(np.float32)
    else:
        template = np.zeros((2*th+1, 2*th+1), dtype=np.float32)

    rows: list = []
    fi = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t    = fi / fps
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Velocity-predicted search centre
        if len(dir_hist) >= 2:
            unwrapped = list(np.unwrap(dir_hist))
            deltas    = [unwrapped[i] - unwrapped[i-1] for i in range(1, len(unwrapped))]
            pred_dir  = unwrapped[-1] + float(np.mean(deltas))
            conf      = min(1.0, len(dir_hist) / DIR_HIST_LEN)
        else:
            pred_dir, conf = cur_dir, 0.0

        best_pt, ncc_score = _arc_search(gray, pred_dir, conf, ank_pt, template)

        if ncc_score >= MIN_NCC:
            ank_pt = best_pt
            ax2, ay2 = int(round(float(ank_pt[0]))), int(round(float(ank_pt[1])))
            h2, w2 = gray.shape
            if ax2-th >= 0 and ay2-th >= 0 and ax2+th+1 <= w2 and ay2+th+1 <= h2:
                new_patch = gray[ay2-th:ay2+th+1, ax2-th:ax2+th+1].astype(np.float32)
                template  = (1 - TEMPL_ALPHA) * template + TEMPL_ALPHA * new_patch
        else:
            if len(dir_hist) >= 2:
                hist   = list(np.unwrap(dir_hist))
                deltas = [hist[i] - hist[i-1] for i in range(1, len(hist))]
                next_d = hist[-1] + float(np.mean(deltas))
                ank_pt = _project(knee0 + np.array(
                    [math.cos(next_d) * shank_len, math.sin(next_d) * shank_len],
                    dtype=np.float32))

        cur_dir = math.atan2(float(ank_pt[1] - knee0[1]), float(ank_pt[0] - knee0[0]))
        dir_hist.append(cur_dir)
        if len(dir_hist) > DIR_HIST_LEN:
            dir_hist.pop(0)

        hip = list(map(float, hip0))
        kne = list(map(float, knee0))
        ank = list(map(float, ank_pt))
        ang = _angle_deg(hip, kne, ank)

        if fi >= start_frame:
            rows.append({
                "frame": fi, "time_sec": round(t, 6), "leg": "guided",
                "hip_x":   round(hip[0], 2), "hip_y":   round(hip[1], 2), "hip_score":   1.0,
                "knee_x":  round(kne[0], 2), "knee_y":  round(kne[1], 2), "knee_score":  1.0,
                "ankle_x": round(ank[0], 2), "ankle_y": round(ank[1], 2), "ankle_score": 1.0,
                "knee_angle_deg": round(ang, 4) if math.isfinite(ang) else float("nan"),
            })

        fi += 1

    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if math.isfinite(r["knee_angle_deg"]))
    print(f"    [optflow] Done: {valid}/{len(rows)} valid -> {csv_path}", flush=True)


# =============================================================================
# MODEL REGISTRY
# =============================================================================

@dataclasses.dataclass
class ModelEntry:
    family:  str
    variant: str
    thresh:  float
    runner:  Callable
    params:  dict    # extra kwargs; leg_side + is_duo injected at runtime


MODEL_REGISTRY: List[ModelEntry] = [
    # ── PRIORITY GROUP 0: Optical flow (Kinovea-style 2D marker tracking) ────
    # Requires hip/knee/ankle clicks in tracking_selections.json.
    # Tracks the three clicked pixels frame-by-frame with LK optical flow and
    # enforces fixed thigh+shank lengths.  No body-shape model needed.
    ModelEntry("optflow",   "lk",          0.0, run_optical_flow, {}),
    # ── PRIORITY GROUP 1: MediaPipe original (auto hip, knee-click leg-side) ─
    ModelEntry("mediapipe", "lite",        0.5, run_mediapipe, {"variant": "lite"}),
    ModelEntry("mediapipe", "full",        0.5, run_mediapipe, {"variant": "full"}),
    ModelEntry("mediapipe", "heavy",       0.5, run_mediapipe, {"variant": "heavy"}),
    # ── PRIORITY GROUP 1b: MediaPipe guided (fixed hip, 3-joint locking) ─────
    # Reads hip/knee/ankle clicks from tracking_selections.json; uses fixed hip
    # anchor and proximity-locks which MediaPipe landmark index to read each frame.
    # Produces *_guided CSVs for side-by-side accuracy comparison.
    ModelEntry("mediapipe", "lite_guided",  0.5, run_mediapipe, {"variant": "lite",  "guided": True}),
    ModelEntry("mediapipe", "full_guided",  0.5, run_mediapipe, {"variant": "full",  "guided": True}),
    ModelEntry("mediapipe", "heavy_guided", 0.5, run_mediapipe, {"variant": "heavy", "guided": True}),
    # ── PRIORITY GROUP 2: OpenPose Body25 (CPU, every frame) ─────────────
    # frame_step=1 → full 30 fps — needed to resolve 1 Hz pendulum oscillations
    ModelEntry("openpose",  "body25",      0.1, run_openpose, {"frame_step": 1}),
    # ── PRIORITY GROUP 3: HRNet-W48 via MMPose (openmmlab env) ───────────
    ModelEntry("hrnet",     "w48",         0.3, run_hrnet,    {}),
    # ── PRIORITY GROUP 4: RTMPose S / M / L (ONNX, .venv subprocess) ────
    ModelEntry("rtmpose",   "s",           0.3, run_rtmpose,  {"pose_onnx": _RTMP_S_ONNX}),
    ModelEntry("rtmpose",   "m",           0.3, run_rtmpose,  {"pose_onnx": _RTMP_M_ONNX}),
    ModelEntry("rtmpose",   "l",           0.3, run_rtmpose,  {"pose_onnx": _RTMP_L_ONNX}),
    # ── OTHER MODELS (run after priority groups complete) ─────────────────
    # -- DETRPose-S (in-process .venv) ------------------------------------
    ModelEntry("detrpose",  "s",           0.5, run_detrpose, {"detr_dir": _DETR_DIR,
                                                                "config_path": _DETR_CFG,
                                                                "weight_path": _DETR_WTS,
                                                                "frame_step": 5}),
    # -- YOLO-Nano-Pose (in-process .venv, fastest) -----------------------
    ModelEntry("yolo",      "n",           0.5, run_yolo,     {"model_path": _YOLO_PT, "frame_step": 5}),
    ModelEntry("yolo",      "s",           0.5, run_yolo,     {"model_path": os.path.join(MODELS_DIR, "yolov8s-pose.pt"), "frame_step": 5}),
    # -- MoveNet Lightning / Thunder (TF Hub, every frame) ----------------
    ModelEntry("movenet",   "lightning",   0.3, run_movenet,  {"variant": "lightning"}),
    ModelEntry("movenet",   "thunder",     0.3, run_movenet,  {"variant": "thunder"}),
    # -- Pose2Sim rtmlib.Body — all three speed/accuracy modes ------------
    ModelEntry("pose2sim",  "lightweight", 0.3, run_pose2sim, {"mode": "lightweight"}),
    ModelEntry("pose2sim",  "balanced",    0.3, run_pose2sim, {"mode": "balanced"}),
    ModelEntry("pose2sim",  "performance", 0.3, run_pose2sim, {"mode": "performance"}),
    # -- ViTPose B / L via rtmlib -----------------------------------------
    ModelEntry("vitpose",   "b",           0.3, run_vitpose,  {"variant": "b"}),
    ModelEntry("vitpose",   "l",           0.3, run_vitpose,  {"variant": "l"}),
    # -- ViTPose-base via MMPose (openmmlab env) ---------------------------
    ModelEntry("vitpose",   "base",        0.3, run_vitpose_mmpose, {}),
    # -- RTMO S / M / L one-stage MMPose (downloads ONNX once) -----------
    ModelEntry("rtmo",      "s",           0.3, run_rtmo,     {"variant": "s"}),
    ModelEntry("rtmo",      "m",           0.3, run_rtmo,     {"variant": "m"}),
    ModelEntry("rtmo",      "l",           0.3, run_rtmo,     {"variant": "l"}),
]


# =============================================================================
# ACTIVE-KNEE DETECTION
# =============================================================================

def _detect_active_leg(yolo_model, video_path: str,
                        leg_side_hint: Optional[str],
                        n_sample: int = 80) -> str:
    """
    YOLO pre-pass on the middle 80% of the video to find the swinging knee.
    The swinging leg has much larger y-displacement variance than the stance leg.
    Returns 'left' or 'right'.  Falls back to leg_side_hint when motion ratio
    is below 1.35x or fewer than 10 confident keypoints are collected.
    """
    cap   = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    start = int(total * 0.10)
    end   = int(total * 0.90)
    step  = max(1, (end - start) // n_sample)

    l_y: list = []
    r_y: list = []
    fi = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if start <= fi < end and (fi - start) % step == 0:
            try:
                res = yolo_model(frame, verbose=False, conf=0.25)[0]
                kpd = (res.keypoints.data.cpu().numpy()
                       if res.keypoints is not None and res.keypoints.data is not None
                       else None)
                if kpd is not None and kpd.shape[0] > 0:
                    best = int(np.argmax(kpd[:, L_KNEE, 2] + kpd[:, R_KNEE, 2]))
                    if kpd[best, L_KNEE, 2] > 0.15:
                        l_y.append(float(kpd[best, L_KNEE, 1]))
                    if kpd[best, R_KNEE, 2] > 0.15:
                        r_y.append(float(kpd[best, R_KNEE, 1]))
            except Exception:
                pass
        fi += 1
    cap.release()

    if len(l_y) < 10 or len(r_y) < 10:
        return leg_side_hint or "left"

    l_std = float(np.std(l_y))
    r_std = float(np.std(r_y))

    if l_std > r_std * 1.35:
        return "left"
    if r_std > l_std * 1.35:
        return "right"
    return leg_side_hint or ("left" if l_std >= r_std else "right")


# =============================================================================
# PHASE 1 — INFERENCE
# =============================================================================

def _detect_all_leg_sides(videos: List[dict]) -> None:
    """YOLO motion pre-pass: confirm/override folder leg-side hint per trial."""
    _yolo_det = None
    try:
        from ultralytics import YOLO as _YDet
        _yolo_det = _YDet(_YOLO_PT)
        print("  [active-leg] YOLO pre-pass: detecting swinging knee per trial...")
    except Exception as _e:
        print(f"  [active-leg] YOLO unavailable ({_e}); using folder hints only")
        return

    for vid in videos:
        hint     = vid["leg_side"]
        detected = _detect_active_leg(_yolo_det, vid["video_path"], hint)
        tag      = "confirmed" if detected == hint else "OVERRIDE"
        print(f"    P{vid['pid']}/T{vid['trial']}: folder={hint or 'none'} -> {detected} [{tag}]",
              flush=True)
        vid["leg_side"] = detected
    del _yolo_det
    print()


def _infer_one_entry(videos: List[dict], entry: "ModelEntry") -> None:
    """Run inference for a single model entry across all pending trials."""
    label   = f"{entry.family}_{entry.variant}_{entry.thresh}"
    pending = [v for v in videos
               if not os.path.isfile(os.path.join(v["out_dir"], _csv_name(v, entry)))]
    if not pending:
        print(f"  -- {label} -- [all {len(videos)} CSVs exist, skipping inference]")
        return
    print(f"  -- {label} -- inference ({len(pending)} of {len(videos)} pending)")
    for vid in pending:
        pid, pos, trial = vid["pid"], vid["pos"], vid["trial"]
        csv_path = os.path.join(vid["out_dir"], _csv_name(vid, entry))
        print(f"    [run ] P{pid}/Pos{pos}/T{trial} -> {_csv_name(vid, entry)}", flush=True)
        kw = dict(entry.params)
        kw["score_thresh"] = entry.thresh
        kw["leg_side"]     = vid.get("leg_side")
        kw["is_duo"]       = vid.get("is_duo", False)
        try:
            entry.runner(vid["video_path"], csv_path, **kw)
        except Exception as exc:
            print(f"    [ERR ] {exc}", flush=True)


def _annotate_one_entry(videos: List[dict], entry: "ModelEntry") -> None:
    """Render annotated videos for a single model entry."""
    label = f"{entry.family}_{entry.variant}_{entry.thresh}"
    rendered = skipped = missing = 0
    for vid in videos:
        csv_path = os.path.join(vid["out_dir"], _csv_name(vid, entry))
        if not os.path.isfile(csv_path):
            missing += 1
            continue
        ann_path = csv_path.replace(".csv", "_annotated.mp4")
        if os.path.isfile(ann_path):
            skipped += 1
            continue
        lbl = f"{entry.family}_{entry.variant} | P{vid['pid']} Pos{vid['pos']} T{vid['trial']}"
        print(f"    [annotate] {os.path.basename(ann_path)}", flush=True)
        try:
            _render_one(csv_path, vid["video_path"], ann_path, lbl)
            rendered += 1
        except Exception as exc:
            print(f"    [ERR] {exc}", flush=True)
    status = f"rendered={rendered}"
    if skipped:  status += f"  already-exist={skipped}"
    if missing:  status += f"  no-csv={missing}"
    print(f"  -- {label} -- annotation done  ({status})")


def phase_inference(videos: List[dict]) -> None:
    sep = "=" * 65
    print(f"\n{sep}")
    print(f"PHASE 1 -- INFERENCE  ({len(MODEL_REGISTRY)} models x {len(videos)} trials)")
    print(f"{sep}\n")
    _detect_all_leg_sides(videos)
    for entry in MODEL_REGISTRY:
        _infer_one_entry(videos, entry)
    print("\nPhase 1 complete.")


# =============================================================================
# PHASE 2 — ANNOTATED VIDEOS
# =============================================================================

_ANN_LINE  = (200, 200, 200)
_ANN_TXT   = (255, 255, 255)
_ANN_ANGLE = ( 50, 255, 200)
_ANN_KP    = {"hip": (255, 180, 30), "knee": (50, 220, 50), "ankle": (50, 130, 255)}


def _render_one(csv_path: str, raw_video: str, out_path: str, label: str) -> None:
    df    = pd.read_csv(csv_path)
    cap   = cv2.VideoCapture(raw_video)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out   = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w_vid, h_vid))
    rows_by_frame: Dict[int, dict] = {int(r["frame"]): r for r in df.to_dict("records")}
    fi = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        row = rows_by_frame.get(fi)
        if row is not None:
            def valid(v):
                try:    return v is not None and not math.isnan(float(v))
                except: return False
            pts: dict = {}
            if valid(row.get("hip_x"))   and valid(row.get("hip_y")):
                pts["hip"]   = (int(float(row["hip_x"])),   int(float(row["hip_y"])))
            if valid(row.get("knee_x"))  and valid(row.get("knee_y")):
                pts["knee"]  = (int(float(row["knee_x"])),  int(float(row["knee_y"])))
            if valid(row.get("ankle_x")) and valid(row.get("ankle_y")):
                pts["ankle"] = (int(float(row["ankle_x"])), int(float(row["ankle_y"])))
            for a_n, b_n in [("hip", "knee"), ("knee", "ankle")]:
                if a_n in pts and b_n in pts:
                    cv2.line(frame, pts[a_n], pts[b_n], _ANN_LINE, 2, cv2.LINE_AA)
            for name, color in _ANN_KP.items():
                if name in pts:
                    try:    sc = float(row.get(f"{name}_score", 0))
                    except: sc = 0.0
                    r = max(5, int(8 * sc))
                    cv2.circle(frame, pts[name], r,   color,   -1, cv2.LINE_AA)
                    cv2.circle(frame, pts[name], r+1, (0,0,0),  1, cv2.LINE_AA)
            ang = row.get("knee_angle_deg")
            if "knee" in pts and valid(ang):
                kx, ky = pts["knee"]
                txt = f"{float(ang):.1f}deg"
                (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                cv2.rectangle(frame, (kx+8, ky-th-6), (kx+8+tw+4, ky+4), (0,0,0), -1)
                cv2.putText(frame, txt, (kx+10, ky), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            _ANN_ANGLE, 2, cv2.LINE_AA)
        t_sec = fi / fps
        for i, txt in enumerate([label, f"frame {fi}/{total}", f"t={t_sec:.2f}s"]):
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            x = w_vid - tw - 8; y = 18 + i * 20
            cv2.rectangle(frame, (x-3, y-th-3), (x+tw+3, y+4), (0,0,0), -1)
            cv2.putText(frame, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                        _ANN_TXT, 1, cv2.LINE_AA)
        out.write(frame)
        fi += 1
    cap.release(); out.release()


def phase_annotate(videos: List[dict]) -> None:
    sep = "=" * 65
    print(f"\n{sep}")
    print("PHASE 2 -- ANNOTATED VIDEOS")
    print(f"{sep}\n")
    for entry in MODEL_REGISTRY:
        _annotate_one_entry(videos, entry)
    print("\nPhase 2 complete.")


# =============================================================================
# PHASE 3 — COMPARISON PLOTS + RMSE LEADERBOARD
# =============================================================================

_PALETTE = [
    "#4878A8", "#C87048", "#5A9068", "#9B59B6", "#E74C3C",
    "#F39C12", "#1ABC9C", "#E67E22", "#3498DB", "#E91E63",
    "#00BCD4", "#7F8C8D",
]


def _load_optitrack(path: str) -> Tuple[np.ndarray, np.ndarray]:
    header = 0
    with open(path, encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header = i; break
    df = (pd.read_csv(path, skiprows=header)
            .apply(pd.to_numeric, errors="coerce").ffill().bfill())
    t = df.iloc[:, 1].values.astype(float); t -= t[0]
    tx,ty,tz,tw = (df.iloc[:,c].values for c in [2,3,4,5])
    sx,sy,sz,sw = (df.iloc[:,c].values for c in [9,10,11,12])
    r_thigh = _R.from_quat(np.column_stack([tx,ty,tz,tw]))
    r_shank = _R.from_quat(np.column_stack([sx,sy,sz,sw]))
    return t, np.degrees((r_thigh.inv() * r_shank).magnitude())


def _load_model_signal(path: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    try:
        df   = pd.read_csv(path)
        t    = df["time_sec"].values.astype(float)
        ang  = df["knee_angle_deg"].values.astype(float)
        mask = np.isfinite(ang)
        return (t[mask], ang[mask]) if mask.sum() >= 5 else (None, None)
    except Exception:
        return None, None


def _find_release(t: np.ndarray, ang: np.ndarray,
                  baseline_secs: float = 1.0, thresh: float = 8.0) -> float:
    """First time the signal departs from its initial baseline by > thresh degrees."""
    bi = max(3, int(np.searchsorted(t, t[0] + baseline_secs)))
    bi = min(bi, len(t) - 1)
    baseline = float(np.nanmedian(ang[:bi]))
    for i in range(bi, len(t)):
        if not np.isfinite(ang[i]):
            continue
        if abs(float(ang[i]) - baseline) > thresh:
            return float(t[i])
    return float(t[max(1, len(t) // 4)])


def _compute_rmse(t_m: np.ndarray, ang_m: np.ndarray,
                  t_o: np.ndarray, ang_o: np.ndarray,
                  rel_o: float, window_sec: float = 4.0) -> float:
    """Align model signal to OptiTrack release time, compute RMSE over window."""
    if len(t_m) < 5 or len(t_o) < 5:
        return float("nan")
    rel_m      = _find_release(t_m, ang_m)
    t_m_shift  = t_m + (rel_o - rel_m)
    t_end      = min(rel_o + window_sec, float(t_m_shift[-1]), float(t_o[-1]))
    if t_end < rel_o + 0.5:
        return float("nan")
    grid  = np.linspace(rel_o, t_end, 200)
    a_m   = np.interp(grid, t_m_shift, ang_m)
    a_o   = np.interp(grid, t_o,       ang_o)
    diff  = a_m - a_o
    valid = np.isfinite(diff)
    if valid.sum() < 10:
        return float("nan")
    return float(np.sqrt(np.mean(diff[valid] ** 2)))


def _plot_trial(pid, pos, trial, o_t, o_ang, traces, out_path) -> None:
    fig, ax = plt.subplots(figsize=(13, 5))
    fig.patch.set_facecolor("#111122"); ax.set_facecolor("#1a1a2e")
    for tr in traces:
        ax.plot(tr["t"], tr["ang"], color=tr["color"], lw=1.4, alpha=0.82, label=tr["label"])
    ax.plot(o_t, o_ang, color="white", lw=2.5, zorder=5, label="OptiTrack GT")
    ax.set_xlabel("Time (s)", color="#cccccc")
    ax.set_ylabel("Knee Angle (deg)", color="#cccccc")
    ax.set_title(f"P{pid} | Pos{pos} | Trial{trial} -- Knee Flexion Angle vs Time",
                 color="white", fontsize=13)
    ax.tick_params(colors="#aaaaaa")
    for sp in ax.spines.values(): sp.set_edgecolor("#444466")
    ax.legend(fontsize=8, facecolor="#0f0f1a", labelcolor="white",
              edgecolor="#444466", ncol=3, loc="upper right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=140, facecolor=fig.get_facecolor())
    plt.close()
    print(f"    -> {os.path.basename(out_path)}", flush=True)


def _save_leaderboard(rmse_rows: list) -> None:
    if not rmse_rows:
        return
    df = pd.DataFrame(rmse_rows)   # columns: model, trial_key, rmse_deg
    pivot = df.pivot_table(index="model", columns="trial_key",
                           values="rmse_deg", aggfunc="mean")
    pivot["mean_RMSE"] = pivot.mean(axis=1)
    pivot = pivot.sort_values("mean_RMSE")

    lb_path = os.path.join(OUT_DIR, "P2_rmse_leaderboard.csv")
    pivot.to_csv(lb_path)
    print(f"\n  RMSE Leaderboard -> {lb_path}")
    print(pivot[["mean_RMSE"]].to_string())

    # Heatmap (exclude mean_RMSE column)
    data = pivot.drop(columns=["mean_RMSE"], errors="ignore")
    ncols = max(1, len(data.columns))
    nrows = max(1, len(data.index))
    fig, ax = plt.subplots(figsize=(max(8, ncols * 1.2 + 2), max(4, nrows * 0.55 + 2)))
    vals = data.values.astype(float)
    vmax = float(np.nanpercentile(vals, 95)) if np.any(np.isfinite(vals)) else 50.0
    im   = ax.imshow(vals, cmap="RdYlGn_r", aspect="auto", vmin=0, vmax=vmax)
    ax.set_xticks(range(ncols))
    ax.set_xticklabels(data.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(range(nrows))
    ax.set_yticklabels(data.index, fontsize=9)
    for i in range(nrows):
        for j in range(ncols):
            v = vals[i, j]
            ax.text(j, i, f"{v:.1f}" if np.isfinite(v) else "-",
                    ha="center", va="center", fontsize=7, color="black")
    plt.colorbar(im, ax=ax, label="RMSE (deg)", shrink=0.8)
    ax.set_title("Knee Flexion RMSE vs OptiTrack Ground Truth  (Participant 2)",
                 fontsize=12)
    plt.tight_layout()
    hm_path = os.path.join(OUT_DIR, "P2_rmse_heatmap.png")
    plt.savefig(hm_path, dpi=140, bbox_inches="tight")
    plt.close()
    print(f"  RMSE Heatmap     -> {hm_path}")


def phase_compare(videos: List[dict]) -> None:
    sep = "=" * 65
    print(f"\n{sep}")
    print("PHASE 3 -- COMPARISON PLOTS + RMSE LEADERBOARD")
    print(f"{sep}\n")

    rmse_rows: list = []

    for vid in videos:
        pid, pos, trial = vid["pid"], vid["pos"], vid["trial"]
        plot_out  = os.path.join(OUT_DIR, f"P{pid}_Pos{pos}_T{trial}_comparison.png")
        opti_path = find_optitrack(pid, pos, trial)
        if opti_path is None:
            print(f"  [no optitrack] P{pid}/Pos{pos}/T{trial} -- skipping")
            continue

        # Collect model traces
        traces: list = []
        for i, entry in enumerate(MODEL_REGISTRY):
            csv_path = os.path.join(vid["out_dir"], _csv_name(vid, entry))
            if not os.path.isfile(csv_path):
                continue
            t, ang = _load_model_signal(csv_path)
            if t is None:
                continue
            label = f"{entry.family}_{entry.variant}_{entry.thresh}"
            traces.append({"t": t, "ang": ang,
                           "color": _PALETTE[i % len(_PALETTE)], "label": label,
                           "model_label": label})

        if not traces:
            print(f"  [no model CSVs] P{pid}/Pos{pos}/T{trial} -- skipping")
            continue

        # Load OptiTrack once
        try:
            o_t, o_ang = _load_optitrack(opti_path)
        except Exception as exc:
            print(f"  [optitrack err] P{pid}/Pos{pos}/T{trial}: {exc}")
            continue

        rel_o = _find_release(o_t, o_ang)
        trial_key = f"P{pid}_Pos{pos}_T{trial}"

        # RMSE for each model
        for tr in traces:
            rmse = _compute_rmse(tr["t"], tr["ang"], o_t, o_ang, rel_o)
            rmse_rows.append({"model": tr["model_label"],
                              "trial_key": trial_key,
                              "rmse_deg":  round(rmse, 3) if math.isfinite(rmse) else float("nan")})
            rmse_str = f"{rmse:.2f} deg" if math.isfinite(rmse) else "n/a"
            tr["label"] = f"{tr['model_label']}  (RMSE={rmse_str})"

        # Plot
        if os.path.isfile(plot_out):
            print(f"  [exists] {os.path.basename(plot_out)}")
        else:
            print(f"  Plotting P{pid}/Pos{pos}/T{trial}  ({len(traces)} models)", flush=True)
            _plot_trial(pid, pos, trial, o_t, o_ang, traces, plot_out)

    # RMSE Leaderboard
    _save_leaderboard(rmse_rows)
    print("\nPhase 3 complete.")


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    import argparse as _ap
    _parser = _ap.ArgumentParser(description="Pendulastic inference + annotation pipeline")
    _parser.add_argument("--compare", action="store_true",
                         help="Also run Phase 3: RMSE comparison against OptiTrack CSVs")
    _parser.add_argument("--pid", default=None,
                         help="Restrict to a single participant substring, e.g. --pid 5")
    _parser.add_argument("--families", default=None,
                         help="Comma-separated model families to run, e.g. "
                              "--families mediapipe,movenet,rtmpose,vitpose,hrnet,yolo  "
                              "(default: all)")
    _args = _parser.parse_args()

    global TARGET_PARTICIPANTS
    if _args.pid:
        TARGET_PARTICIPANTS = [_args.pid]

    # Filter registry to requested families (--families) and strip guided variants
    active_registry = MODEL_REGISTRY
    if _args.families:
        wanted = {f.strip().lower() for f in _args.families.split(",")}
        active_registry = [e for e in MODEL_REGISTRY if e.family.lower() in wanted
                           and not e.variant.endswith("_guided")]

    videos = discover_videos()
    if not videos:
        print("No trial videos found.  Check VIDEO_ROOT and folder structure.")
        return

    participants = sorted(set(v["pid"] for v in videos))
    duo_count    = sum(1 for v in videos if v["is_duo"])
    print(f"Discovered {len(videos)} trial(s) -- {duo_count} duo, {len(videos)-duo_count} solo")
    print(f"  Participants : {', '.join('P'+p for p in participants)}")
    print(f"  Models       : {len(active_registry)}"
          + (f"  (filtered from {len(MODEL_REGISTRY)})" if len(active_registry) != len(MODEL_REGISTRY) else ""))
    _OPENMMLAB_RUNNERS = {run_hrnet, run_vitpose_mmpose}
    _VENV_SUB_RUNNERS  = {run_rtmpose, run_vitpose, run_rtmo, run_mediapipe, run_pose2sim}
    _INPROCESS_RUNNERS = {run_yolo, run_detrpose, run_movenet, run_openpose}
    for entry in active_registry:
        tag   = f"{entry.family}_{entry.variant}_{entry.thresh}"
        route = ("openmmlab subprocess" if entry.runner in _OPENMMLAB_RUNNERS else
                 ".venv subprocess"     if entry.runner in _VENV_SUB_RUNNERS  else
                 "in-process (.venv)")
        print(f"    {tag:<40}  [{route}]")
    print()

    # ── Active-leg detection (YOLO pre-pass, once for all trials) ─────────────
    sep = "=" * 65
    print(f"\n{sep}")
    print(f"ACTIVE-LEG DETECTION")
    print(f"{sep}\n")
    _detect_all_leg_sides(videos)

    # ── Per-model: inference immediately followed by annotation ───────────────
    print(f"\n{sep}")
    print(f"INFERENCE + ANNOTATION  ({len(active_registry)} models x {len(videos)} trials)")
    print(f"  Each model: all CSVs first, then all annotated videos, then next model.")
    print(f"{sep}\n")
    for i, entry in enumerate(active_registry, 1):
        label = f"{entry.family}_{entry.variant}_{entry.thresh}"
        print(f"\n[{i}/{len(active_registry)}] {label}")
        _infer_one_entry(videos, entry)
        _annotate_one_entry(videos, entry)

    if _args.compare:
        phase_compare(videos)
    else:
        print("\n[Phase 3 skipped] Run with --compare to generate RMSE plots vs OptiTrack.")

    print(f"\nAll outputs written alongside source videos in Recordings/")
    print("To add a new participant:")
    print("  1. Recordings/Participant_N_<side>_<mode>/Position_P/Height_H/Trial_T.avi")
    print("  2. Add pid substring to TARGET_PARTICIPANTS and re-run.")
    print("  3. For OptiTrack comparison: add CSVs to OptiTrack_Recordings/ then use --compare")


if __name__ == "__main__":
    main()
