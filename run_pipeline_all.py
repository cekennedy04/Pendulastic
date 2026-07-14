"""
run_pipeline_all.py
===================
Master end-to-end pipeline for pendulum-test pose estimation.

Three automatic phases (each item skipped if output already exists):

  PHASE 1 — INFERENCE
    For every registered model variant × every raw trial video, generate
    the knee-angle CSV if it does not already exist.

  PHASE 2 — ANNOTATE
    For every CSV (new or pre-existing from any model), render a fitted
    MP4 with keypoints + knee angle overlaid on the raw video frames.
    Output: <csv_stem>_annotated.mp4 alongside the CSV.

  PHASE 3 — EVALUATE
    Run evaluate_all_participants.py to regenerate all plots and
    global_model_leaderboard.csv.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ADDING A NEW MODEL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Write a runner function:

    def run_mymodel(video_path: str, csv_path: str, **params) -> None:
        # Load model using params, process video frame-by-frame,
        # write CSV with columns:
        #   frame, time_sec, leg,
        #   hip_x, hip_y, hip_score,
        #   knee_x, knee_y, knee_score,
        #   ankle_x, ankle_y, ankle_score,
        #   knee_angle_deg
        pass

2. Append one or more entries to MODEL_REGISTRY near the bottom of
   this file:

    ModelSpec("myfamily", "variant_name", threshold_float,
              run_mymodel, {"param_key": "param_value", ...})

That is all — the rest (skip logic, annotated video, evaluation) is
automatic.

Run from the Pendulastic directory:
    .venv\\Scripts\\python.exe run_pipeline_all.py
"""

from __future__ import annotations

import csv
import dataclasses
import glob
import math
import os
import re
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE_DIR    = r"C:\Users\cladi\Pendulastic"
EVAL_SCRIPT = os.path.join(BASE_DIR, "evaluate_all_participants.py")
PLOT_DIR    = os.path.join(BASE_DIR, "Model_Analysis_Outputs")

VIDEO_ROOTS = [
    os.path.join(BASE_DIR, "OptiTrack_Recordings"),
    os.path.join(BASE_DIR, "Recordings"),
]

OPTI_ROOTS  = [
    os.path.join(BASE_DIR, "OptiTrack_Recordings"),
]

_OPTI_RE = re.compile(r"trial_(\d+)_optitrack\.csv$", re.I)

PLOT_PALETTE = [
    "#4878A8", "#C87048", "#5A9068", "#9B59B6", "#E74C3C",
    "#F39C12", "#1ABC9C", "#E67E22", "#3498DB", "#E91E63",
    "#00BCD4", "#7F8C8D",
]

_VID_RE    = re.compile(r"^trial_(\d+)\.(mp4|avi)$", re.IGNORECASE)
_CSV_RE    = re.compile(
    r"^P_(\w+)_Pos_(\w+)_H_(.+?)_T_(\d+)_([A-Za-z]\w*)_(.+)\.csv$",
    re.IGNORECASE,
)
_PID_RE    = re.compile(r"Participant_(\w+)", re.IGNORECASE)
_POS_RE    = re.compile(r"Position_(\w+)",    re.IGNORECASE)
_HEIGHT_RE = re.compile(r"Height_(.+)",       re.IGNORECASE)

# Standard COCO / HALPE_26 lower-body indices
L_HIP,  L_KNEE,  L_ANKLE  = 11, 13, 15
R_HIP,  R_KNEE,  R_ANKLE  = 12, 14, 16

CSV_FIELDNAMES = [
    "frame", "time_sec", "leg",
    "hip_x",   "hip_y",   "hip_score",
    "knee_x",  "knee_y",  "knee_score",
    "ankle_x", "ankle_y", "ankle_score",
    "knee_angle_deg",
]

ANNOTATED_SUFFIX = "_annotated.mp4"

# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASS
# ─────────────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class ModelSpec:
    family:     str
    complexity: str
    threshold:  float
    runner:     Callable
    params:     Dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def variant_tag(self) -> str:
        return f"{self.family}_{self.complexity}_{self.threshold}"


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _angle_deg(a, b, c) -> float:
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1, 1)))


def _nan_row(frame_idx: int, t_sec: float) -> dict:
    return {"frame": frame_idx, "time_sec": round(t_sec, 6), "leg": "none",
            "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
            "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
            "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
            "knee_angle_deg": float("nan")}


def _write_csv(rows: list, path: str) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES)
        w.writeheader(); w.writerows(rows)


def _leg_side_from_pid(pid: str) -> Optional[str]:
    """Extract 'left' or 'right' from participant ids like '2_left_duo'."""
    pid_lower = pid.lower()
    if "left" in pid_lower:
        return "left"
    if "right" in pid_lower:
        return "right"
    return None


def _pick_leg(kp, sc, l_idx, r_idx, leg_side: Optional[str] = None):
    """Return (leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s).
    Honors leg_side ('left'/'right') when set; falls back to confidence heuristic."""
    lhi, lki, lai = l_idx
    rhi, rki, rai = r_idx
    if leg_side == "left":
        return "left",  kp[lhi], float(sc[lhi]), kp[lki], float(sc[lki]), kp[lai], float(sc[lai])
    if leg_side == "right":
        return "right", kp[rhi], float(sc[rhi]), kp[rki], float(sc[rki]), kp[rai], float(sc[rai])
    l_conf = float(sc[lhi]) + float(sc[lki]) + float(sc[lai])
    r_conf = float(sc[rhi]) + float(sc[rki]) + float(sc[rai])
    if l_conf >= r_conf:
        return "left",  kp[lhi], float(sc[lhi]), kp[lki], float(sc[lki]), kp[lai], float(sc[lai])
    return "right", kp[rhi], float(sc[rhi]), kp[rki], float(sc[rki]), kp[rai], float(sc[rai])


def _best_person_idx(scores: np.ndarray, leg_side: Optional[str]) -> int:
    """For multi-person arrays (N, K), pick person index by designated leg confidence."""
    if leg_side == "left":
        person_scores = scores[:, L_HIP] + scores[:, L_KNEE] + scores[:, L_ANKLE]
    elif leg_side == "right":
        person_scores = scores[:, R_HIP] + scores[:, R_KNEE] + scores[:, R_ANKLE]
    else:
        person_scores = scores.mean(axis=1)
    return int(np.argmax(person_scores))


# ─────────────────────────────────────────────────────────────────────────────
# DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

def _path_meta(path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    parts = path.replace("\\", "/").split("/")
    pid = pos = height = None
    for p in parts:
        m = _PID_RE.match(p)
        if m: pid = m.group(1)
        m = _POS_RE.match(p)
        if m: pos = m.group(1)
        m = _HEIGHT_RE.match(p)
        if m: height = m.group(1)
    return pid, pos, height


def discover_videos() -> List[dict]:
    found = []
    seen  = set()
    for root in VIDEO_ROOTS:
        if not os.path.isdir(root):
            continue
        for ext in ("*.mp4", "*.avi"):
            for path in glob.glob(os.path.join(root, "**", ext), recursive=True):
                bn = os.path.basename(path)
                m  = _VID_RE.match(bn)
                if not m:
                    continue
                trial = m.group(1)
                pid, pos, height = _path_meta(path)
                if pid and pos and height:
                    key = (pid, pos, trial)
                    if key not in seen:
                        seen.add(key)
                        found.append(dict(pid=pid, pos=pos, trial=trial,
                                          height=height,
                                          leg_side=_leg_side_from_pid(pid),
                                          video_path=path,
                                          out_dir=os.path.dirname(path)))
    return found


def discover_csvs() -> List[dict]:
    found = []
    for root in VIDEO_ROOTS:
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.csv"), recursive=True):
            bn = os.path.basename(path)
            m  = _CSV_RE.match(bn)
            if not m:
                continue
            pid, pos, height_tag, trial = m.group(1), m.group(2), m.group(3), m.group(4)
            family  = m.group(5).lower()
            rest    = m.group(6)
            parts   = rest.rsplit("_", 1)
            complexity = parts[0] if len(parts) == 2 else rest
            threshold  = parts[1] if len(parts) == 2 else "?"
            # Find the matching raw video in the same directory
            vid_dir = os.path.dirname(path)
            raw_video = None
            for ext in (".mp4", ".avi"):
                for name in (f"trial_{trial}{ext}", f"Trial_{trial}{ext}"):
                    cand = os.path.join(vid_dir, name)
                    if os.path.isfile(cand):
                        raw_video = cand
                        break
                if raw_video:
                    break
            found.append(dict(
                pid=pid, pos=pos, trial=trial, height=height_tag,
                family=family, complexity=complexity, threshold=threshold,
                csv_path=path, raw_video=raw_video,
                annotated_path=path.replace(".csv", ANNOTATED_SUFFIX),
            ))
    return found


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — ANNOTATED VIDEO RENDERER  (universal, reads from CSV)
# ─────────────────────────────────────────────────────────────────────────────

_KP_COLORS = {
    "hip":   (255, 180,  30),   # orange
    "knee":  ( 50, 220,  50),   # green
    "ankle": ( 50, 130, 255),   # blue
}
_LINE_COLOR  = (200, 200, 200)
_TEXT_COLOR  = (255, 255, 255)
_ANGLE_COLOR = ( 50, 255, 200)


def render_annotated_video(csv_path: str, raw_video: str, out_path: str,
                            label: str) -> None:
    df = pd.read_csv(csv_path)
    cap = cv2.VideoCapture(raw_video)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out    = cv2.VideoWriter(out_path, fourcc, fps, (w_vid, h_vid))

    rows_by_frame: Dict[int, dict] = {int(r["frame"]): r for r in df.to_dict("records")}
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        row = rows_by_frame.get(frame_idx)

        if row is not None:
            hip_x   = row.get("hip_x");   hip_y   = row.get("hip_y")
            knee_x  = row.get("knee_x");  knee_y  = row.get("knee_y")
            ankle_x = row.get("ankle_x"); ankle_y = row.get("ankle_y")
            angle   = row.get("knee_angle_deg")
            h_sc    = row.get("hip_score",   0)
            k_sc    = row.get("knee_score",  0)
            a_sc    = row.get("ankle_score", 0)
            leg     = row.get("leg", "?")

            def valid(v):
                try: return v is not None and not math.isnan(float(v))
                except: return False

            pts = {}
            if valid(hip_x)   and valid(hip_y):   pts["hip"]   = (int(float(hip_x)),   int(float(hip_y)))
            if valid(knee_x)  and valid(knee_y):  pts["knee"]  = (int(float(knee_x)),  int(float(knee_y)))
            if valid(ankle_x) and valid(ankle_y): pts["ankle"] = (int(float(ankle_x)), int(float(ankle_y)))

            # Skeleton lines
            for (a_name, b_name) in [("hip", "knee"), ("knee", "ankle")]:
                if a_name in pts and b_name in pts:
                    cv2.line(frame, pts[a_name], pts[b_name], _LINE_COLOR, 2, cv2.LINE_AA)

            # Keypoint circles (radius scales with confidence)
            for name, color in _KP_COLORS.items():
                if name in pts:
                    sc = {"hip": h_sc, "knee": k_sc, "ankle": a_sc}.get(name, 0)
                    try: sc = float(sc)
                    except: sc = 0
                    r = max(5, int(8 * sc))
                    cv2.circle(frame, pts[name], r, color, -1, cv2.LINE_AA)
                    cv2.circle(frame, pts[name], r + 1, (0,0,0), 1, cv2.LINE_AA)

            # Knee angle label
            if "knee" in pts and valid(angle):
                ang_f = float(angle)
                ang_txt = f"{ang_f:.1f}deg"
                kx, ky = pts["knee"]
                (tw, th), _ = cv2.getTextSize(ang_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                cv2.rectangle(frame, (kx+8, ky-th-6), (kx+8+tw+4, ky+4), (0,0,0), -1)
                cv2.putText(frame, ang_txt, (kx+10, ky),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, _ANGLE_COLOR, 2, cv2.LINE_AA)

            # Leg side label (bottom of ankle)
            if "ankle" in pts:
                ax, ay = pts["ankle"]
                cv2.putText(frame, leg, (ax-15, ay+20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, _KP_COLORS["ankle"], 1, cv2.LINE_AA)

        else:
            # No row data — show no-detection notice
            cv2.putText(frame, "NO DATA", (10, h_vid - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)

        # HUD overlay — top-right corner
        t_sec = frame_idx / fps
        hud_lines = [
            label,
            f"frame {frame_idx}/{total}",
            f"t = {t_sec:.2f}s",
        ]
        for i, txt in enumerate(hud_lines):
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            x = w_vid - tw - 8
            y = 18 + i * 20
            cv2.rectangle(frame, (x-3, y-th-3), (x+tw+3, y+4), (0,0,0), -1)
            cv2.putText(frame, txt, (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, _TEXT_COLOR, 1, cv2.LINE_AA)

        out.write(frame)
        frame_idx += 1

    cap.release(); out.release()


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER FUNCTIONS  (one per model family)
# ─────────────────────────────────────────────────────────────────────────────

# ── YOLO ─────────────────────────────────────────────────────────────────────

def run_yolo(video_path: str, csv_path: str, **params) -> None:
    from ultralytics import YOLO as _YOLO
    model_path   = params["model_path"]
    score_thresh = float(params.get("score_thresh", 0.5))
    leg_side     = params.get("leg_side")
    frame_step   = int(params.get("frame_step", 5))

    model = _YOLO(model_path)
    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    eff_fps = fps / frame_step
    print(f"    YOLO | {total} frames @ {fps:.0f}fps | step={frame_step} → {eff_fps:.1f}fps  leg={leg_side or 'auto'}", flush=True)

    rows = []; frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        if frame_idx % frame_step == 0:
            t_sec = frame_idx / fps
            res   = model(frame, verbose=False, conf=score_thresh)[0]
            kp17  = None
            if res.keypoints is not None and res.keypoints.data is not None:
                kpd = res.keypoints.data.cpu().numpy()
                if kpd.shape[0] > 0:
                    best_idx = _best_person_idx(kpd[:, :, 2], leg_side)
                    kp17 = kpd[best_idx]
            if kp17 is None:
                rows.append(_nan_row(frame_idx, t_sec))
            else:
                leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s = _pick_leg(
                    kp17[:, :2], kp17[:, 2],
                    (L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE), leg_side=leg_side)
                ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
                ang = _angle_deg(hip_xy, knee_xy, ank_xy) if ok3 else float("nan")
                rows.append({"frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
                             "hip_x": round(float(hip_xy[0]),2), "hip_y": round(float(hip_xy[1]),2), "hip_score": round(float(h_s),4),
                             "knee_x": round(float(knee_xy[0]),2), "knee_y": round(float(knee_xy[1]),2), "knee_score": round(float(k_s),4),
                             "ankle_x": round(float(ank_xy[0]),2), "ankle_y": round(float(ank_xy[1]),2), "ankle_score": round(float(a_s),4),
                             "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan")})
            if frame_idx % 150 == 0:
                print(f"      frame {frame_idx}/{total}", flush=True)
        frame_idx += 1
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} sampled frames")


# ── DETRPose ─────────────────────────────────────────────────────────────────

def run_detrpose(video_path: str, csv_path: str, **params) -> None:
    import sys, torch, torch.nn as nn
    import torchvision.transforms as T
    from PIL import Image

    detr_dir    = params["detr_dir"]
    config_path = params["config_path"]
    weight_path = params["weight_path"]
    score_thresh = float(params.get("score_thresh", 0.5))
    leg_side     = params.get("leg_side")
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
    mdl = instantiate(cfg.model); ppp = instantiate(cfg.postprocessor)
    ckpt = torch.load(weight_path, map_location="cpu", weights_only=False)
    state = ckpt.get("ema", {}).get("module") or ckpt.get("model") or ckpt
    mdl.load_state_dict(state, strict=True)
    deploy = _Deploy(mdl, ppp).eval()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    deploy = deploy.to(device)
    tfm = T.Compose([T.Resize((640, 640)), T.ToTensor()])

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    eff_fps = fps / frame_step
    print(f"    DETRPose | {total} frames @ {fps:.0f}fps | step={frame_step} → {eff_fps:.1f}fps  device={device}  leg={leg_side or 'auto'}", flush=True)

    def _pick_detr(kp17, pscore):
        if leg_side == "left":
            return "left",  kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE], float(pscore)
        if leg_side == "right":
            return "right", kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE], float(pscore)
        def span(h, k, a): return max(h[1],k[1],a[1]) - min(h[1],k[1],a[1])
        l_sp = span(kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE])
        r_sp = span(kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE])
        if l_sp >= r_sp:
            return "left",  kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE], float(pscore)
        else:
            return "right", kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE], float(pscore)

    rows = []; frame_idx = 0
    with torch.no_grad():
        while True:
            ok, frame = cap.read()
            if not ok: break
            if frame_idx % frame_step == 0:
                t_sec  = frame_idx / fps
                im_pil = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                w_im, h_im = im_pil.size
                orig = torch.tensor([[w_im, h_im]], device=device)
                inp  = tfm(im_pil).unsqueeze(0).to(device)
                scores_t, _, kpts_t = deploy(inp, orig)
                sc_np = scores_t[0].cpu().numpy(); kp_np = kpts_t[0].cpu().numpy()
                valid_idx = np.where(sc_np >= score_thresh)[0]
                if len(valid_idx) == 0:
                    rows.append(_nan_row(frame_idx, t_sec))
                else:
                    best   = valid_idx[int(np.argmax(sc_np[valid_idx]))]
                    kp17   = kp_np[best]; ps = sc_np[best]
                    leg, hip, knee, ank, sc = _pick_detr(kp17, ps)
                    ang = _angle_deg(hip, knee, ank)
                    rows.append({"frame": frame_idx, "time_sec": round(t_sec,6), "leg": leg,
                                 "hip_x": round(float(hip[0]),2), "hip_y": round(float(hip[1]),2), "hip_score": round(sc,4),
                                 "knee_x": round(float(knee[0]),2), "knee_y": round(float(knee[1]),2), "knee_score": round(sc,4),
                                 "ankle_x": round(float(ank[0]),2), "ankle_y": round(float(ank[1]),2), "ankle_score": round(sc,4),
                                 "knee_angle_deg": round(ang,4) if not math.isnan(ang) else float("nan")})
                if frame_idx % 150 == 0:
                    print(f"      frame {frame_idx}/{total}", flush=True)
            frame_idx += 1
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid frames")


# ── Pose2Sim / rtmlib ────────────────────────────────────────────────────────

def run_pose2sim(video_path: str, csv_path: str, **params) -> None:
    import onnxruntime as ort, torch as _torch
    from rtmlib import BodyWithFeet, PoseTracker

    mode         = params.get("mode", "balanced")
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")

    if _torch.cuda.is_available() and "CUDAExecutionProvider" in ort.get_available_providers():
        backend, device = "onnxruntime", "cuda"
    else:
        backend, device = "onnxruntime", "cpu"

    tracker = PoseTracker(BodyWithFeet, det_frequency=1, mode=mode,
                          backend=backend, device=device,
                          tracking=False, to_openpose=False)

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    Pose2Sim({mode}) | {total} frames @ {fps:.0f} fps  device={device}  leg={leg_side or 'auto'}")

    rows = []; frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        t_sec = frame_idx / fps
        keypoints, scores = tracker(frame)
        if keypoints is None or len(keypoints) == 0:
            rows.append(_nan_row(frame_idx, t_sec))
        else:
            best = _best_person_idx(scores, leg_side)
            kp26 = keypoints[best]; sc26 = scores[best]
            leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s = _pick_leg(
                kp26, sc26,
                (L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE), leg_side=leg_side)
            ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
            ang = _angle_deg(hip_xy, knee_xy, ank_xy) if ok3 else float("nan")
            rows.append({"frame": frame_idx, "time_sec": round(t_sec,6), "leg": leg,
                         "hip_x": round(float(hip_xy[0]),2), "hip_y": round(float(hip_xy[1]),2), "hip_score": round(float(h_s),4),
                         "knee_x": round(float(knee_xy[0]),2), "knee_y": round(float(knee_xy[1]),2), "knee_score": round(float(k_s),4),
                         "ankle_x": round(float(ank_xy[0]),2), "ankle_y": round(float(ank_xy[1]),2), "ankle_score": round(float(a_s),4),
                         "knee_angle_deg": round(ang,4) if not math.isnan(ang) else float("nan")})
        frame_idx += 1
        if frame_idx % 300 == 0: print(f"      frame {frame_idx}/{total}", flush=True)
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid frames")


# ── ViTPose (via rtmlib Custom + HuggingFace ONNX) ───────────────────────────

_VITPOSE_DET_URL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
    "yolox_m_8xb8-300e_humanart-c2c7a14a.zip"
)

def run_vitpose(video_path: str, csv_path: str, **params) -> None:
    """ViTPose top-down pose via rtmlib Custom.
    COCO-25 keypoints; lower-body indices same as COCO-17 (11-16).
    Weights auto-downloaded from HuggingFace on first run.
    """
    import onnxruntime as ort, torch as _torch
    from rtmlib import Custom

    variant      = params.get("variant", "b")
    vitpose_url  = params["vitpose_url"]
    det_url      = params.get("det_url", _VITPOSE_DET_URL)
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")

    if _torch.cuda.is_available() and "CUDAExecutionProvider" in ort.get_available_providers():
        backend, device = "onnxruntime", "cuda"
    else:
        backend, device = "onnxruntime", "cpu"

    pose_model = Custom(
        det_class="YOLOX",  det=det_url,  det_input_size=(640, 640),
        pose_class="ViTPose", pose=vitpose_url, pose_input_size=(192, 256),
        backend=backend, device=device,
    )

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    ViTPose-{variant} | {total} frames @ {fps:.0f} fps  device={device}  leg={leg_side or 'auto'}")

    rows = []; frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        t_sec = frame_idx / fps
        keypoints, scores = pose_model(frame)
        if keypoints is None or len(keypoints) == 0:
            rows.append(_nan_row(frame_idx, t_sec))
        else:
            best = _best_person_idx(scores, leg_side)
            kp = keypoints[best]; sc = scores[best]
            leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s = _pick_leg(
                kp, sc, (L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE), leg_side=leg_side)
            ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
            ang = _angle_deg(hip_xy, knee_xy, ank_xy) if ok3 else float("nan")
            rows.append({"frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
                         "hip_x":   round(float(hip_xy[0]),2),  "hip_y":   round(float(hip_xy[1]),2),  "hip_score":   round(float(h_s),4),
                         "knee_x":  round(float(knee_xy[0]),2), "knee_y":  round(float(knee_xy[1]),2), "knee_score":  round(float(k_s),4),
                         "ankle_x": round(float(ank_xy[0]),2),  "ankle_y": round(float(ank_xy[1]),2),  "ankle_score": round(float(a_s),4),
                         "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan")})
        frame_idx += 1
        if frame_idx % 300 == 0: print(f"      frame {frame_idx}/{total}", flush=True)
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid frames")


# ── RTMO (via rtmlib Custom — single-stage, no separate detector) ─────────────

def run_rtmo(video_path: str, csv_path: str, **params) -> None:
    """RTMO single-stage bottom-up pose via rtmlib Custom.
    COCO-17 keypoints; no separate person detector needed.
    Weights auto-downloaded from OpenMMLab CDN on first run.
    """
    import onnxruntime as ort, torch as _torch
    from rtmlib import Custom

    variant      = params.get("variant", "m")
    rtmo_url     = params["rtmo_url"]
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")

    if _torch.cuda.is_available() and "CUDAExecutionProvider" in ort.get_available_providers():
        backend, device = "onnxruntime", "cuda"
    else:
        backend, device = "onnxruntime", "cpu"

    pose_model = Custom(
        pose_class="RTMO", pose=rtmo_url, pose_input_size=(640, 640),
        backend=backend, device=device,
    )

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    RTMO-{variant} | {total} frames @ {fps:.0f} fps  device={device}  leg={leg_side or 'auto'}")

    rows = []; frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        t_sec = frame_idx / fps
        keypoints, scores = pose_model(frame)
        if keypoints is None or len(keypoints) == 0:
            rows.append(_nan_row(frame_idx, t_sec))
        else:
            best = _best_person_idx(scores, leg_side)
            kp = keypoints[best]; sc = scores[best]
            leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s = _pick_leg(
                kp, sc, (L_HIP, L_KNEE, L_ANKLE), (R_HIP, R_KNEE, R_ANKLE), leg_side=leg_side)
            ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
            ang = _angle_deg(hip_xy, knee_xy, ank_xy) if ok3 else float("nan")
            rows.append({"frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
                         "hip_x":   round(float(hip_xy[0]),2),  "hip_y":   round(float(hip_xy[1]),2),  "hip_score":   round(float(h_s),4),
                         "knee_x":  round(float(knee_xy[0]),2), "knee_y":  round(float(knee_xy[1]),2), "knee_score":  round(float(k_s),4),
                         "ankle_x": round(float(ank_xy[0]),2),  "ankle_y": round(float(ank_xy[1]),2),  "ankle_score": round(float(a_s),4),
                         "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan")})
        frame_idx += 1
        if frame_idx % 300 == 0: print(f"      frame {frame_idx}/{total}", flush=True)
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid frames")


# ── MoveNet ───────────────────────────────────────────────────────────────────

_MOVENET_MODULES: Dict[str, Any] = {}   # cache loaded modules

def run_movenet(video_path: str, csv_path: str, **params) -> None:
    import tensorflow as tf
    import tensorflow_hub as hub

    variant      = params.get("variant", "thunder")
    hub_url      = params["hub_url"]
    input_size   = int(params.get("input_size", 256))
    score_thresh = float(params.get("score_thresh", 0.3))
    leg_side     = params.get("leg_side")

    if variant not in _MOVENET_MODULES:
        print(f"    Loading MoveNet-{variant} from TF Hub ...")
        _MOVENET_MODULES[variant] = hub.load(hub_url)
    module = _MOVENET_MODULES[variant]

    def _infer(x):
        return module.signatures["serving_default"](
            tf.cast(x, tf.int32))["output_0"].numpy()

    MIN_CROP = 0.2

    def _init_crop(h, w):
        if w > h:
            return {"y_min":(h/2-w/2)/h,"x_min":0.0,"y_max":(h/2-w/2)/h+w/h,"x_max":1.0,"height":w/h,"width":1.0}
        return {"y_min":0.0,"x_min":(w/2-h/2)/w,"y_max":1.0,"x_max":(w/2-h/2)/w+h/w,"height":1.0,"width":h/w}

    def _next_crop(kps, h, w):
        if not ((kps[0,0,11,2]>MIN_CROP or kps[0,0,12,2]>MIN_CROP) and
                (kps[0,0,5,2]>MIN_CROP  or kps[0,0,6,2]>MIN_CROP)):
            return _init_crop(h, w)
        ky=kps[0,0,:,0]*h; kx=kps[0,0,:,1]*w; ks=kps[0,0,:,2]
        cy=(ky[11]+ky[12])/2; cx=(kx[11]+kx[12])/2
        mt=max(abs(cy-ky[i]) for i in [5,6,11,12]); mx=max(abs(cx-kx[i]) for i in [5,6,11,12])
        by=max((abs(cy-ky[i]) for i in range(17) if ks[i]>MIN_CROP), default=mt)
        bx=max((abs(cx-kx[i]) for i in range(17) if ks[i]>MIN_CROP), default=mx)
        half=min(max(mt*1.9,mx*1.9,by*1.2,bx*1.2), max(cx,w-cx,cy,h-cy))
        if half>max(h,w)/2: return _init_crop(h,w)
        L=half*2
        return {"y_min":(cy-half)/h,"x_min":(cx-half)/w,
                "y_max":(cy-half+L)/h,"x_max":(cx-half+L)/w,"height":L/h,"width":L/w}

    def _run(img_np, crop):
        import tensorflow as tf2
        h,w=img_np.shape[:2]
        boxes=[[crop["y_min"],crop["x_min"],crop["y_max"],crop["x_max"]]]
        t=tf2.expand_dims(tf2.constant(img_np, dtype=tf2.int32), 0)
        cr=tf2.image.crop_and_resize(t, box_indices=[0], boxes=boxes, crop_size=[input_size, input_size])
        kps=_infer(cr)
        for i in range(17):
            kps[0,0,i,0]=(crop["y_min"]*h+crop["height"]*h*kps[0,0,i,0])/h
            kps[0,0,i,1]=(crop["x_min"]*w+crop["width"]*w*kps[0,0,i,1])/w
        return kps

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    print(f"    MoveNet-{variant} | {total} frames @ {fps:.0f} fps")

    rows=[]; frame_idx=0; crop=_init_crop(h_vid, w_vid)
    while True:
        ok, frame = cap.read()
        if not ok: break
        t_sec  = frame_idx / fps
        img_np = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        kps    = _run(img_np, crop)
        crop   = _next_crop(kps, h_vid, w_vid)
        kp17   = kps[0,0]   # (17,3) norm (y,x,score)

        def px(idx):
            y_n,x_n,s = kp17[idx]
            return np.array([x_n*w_vid, y_n*h_vid]), float(s)

        l_hip_xy,l_hs  = px(L_HIP);   l_knee_xy,l_ks = px(L_KNEE);  l_ank_xy,l_as = px(L_ANKLE)
        r_hip_xy,r_hs  = px(R_HIP);   r_knee_xy,r_ks = px(R_KNEE);  r_ank_xy,r_as = px(R_ANKLE)
        use_left = (leg_side == "left") or (leg_side is None and l_hs+l_ks+l_as >= r_hs+r_ks+r_as)
        if use_left:
            leg,hip_xy,h_s,knee_xy,k_s,ank_xy,a_s = "left",l_hip_xy,l_hs,l_knee_xy,l_ks,l_ank_xy,l_as
        else:
            leg,hip_xy,h_s,knee_xy,k_s,ank_xy,a_s = "right",r_hip_xy,r_hs,r_knee_xy,r_ks,r_ank_xy,r_as
        ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
        ang = _angle_deg(hip_xy, knee_xy, ank_xy) if ok3 else float("nan")
        rows.append({"frame":frame_idx,"time_sec":round(t_sec,6),"leg":leg,
                     "hip_x":round(float(hip_xy[0]),2),"hip_y":round(float(hip_xy[1]),2),"hip_score":round(h_s,4),
                     "knee_x":round(float(knee_xy[0]),2),"knee_y":round(float(knee_xy[1]),2),"knee_score":round(k_s,4),
                     "ankle_x":round(float(ank_xy[0]),2),"ankle_y":round(float(ank_xy[1]),2),"ankle_score":round(a_s,4),
                     "knee_angle_deg":round(ang,4) if not math.isnan(ang) else float("nan")})
        frame_idx += 1
        if frame_idx % 300 == 0: print(f"      frame {frame_idx}/{total}", flush=True)
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid frames")


# ── OpenPose ──────────────────────────────────────────────────────────────────

def run_openpose(video_path: str, csv_path: str, **params) -> None:
    """
    Run OpenPose BODY_25 via OpenPoseDemo.exe (subprocess).
    Uses --frame_step to subsample frames for CPU-speed tradeoff.
    BODY_25 lower-body: RHip=9, RKnee=10, RAnkle=11, LHip=12, LKnee=13, LAnkle=14.
    """
    import json, shutil, tempfile

    openpose_bin    = params["openpose_bin"]
    openpose_models = params["openpose_models"]
    frame_step      = int(params.get("frame_step", 10))
    score_thresh    = float(params.get("score_thresh", 0.1))
    net_res         = params.get("net_resolution", "-1x176")

    _B25 = {"R_HIP": 9, "R_KNEE": 10, "R_ANKLE": 11,
             "L_HIP": 12, "L_KNEE": 13, "L_ANKLE": 14}

    if not os.path.isfile(openpose_bin):
        raise FileNotFoundError(f"OpenPoseDemo.exe not found: {openpose_bin}")

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    effective_fps = fps / frame_step
    print(f"    OpenPose BODY_25 | {total} frames @ {fps:.0f} fps | "
          f"frame_step={frame_step} (eff. {effective_fps:.1f} fps) | net_res={net_res}")

    tmp_dir = tempfile.mkdtemp(prefix="op_json_")
    try:
        cmd = [
            openpose_bin,
            "--video",          os.path.abspath(video_path),
            "--write_json",     tmp_dir,
            "--model_pose",     "BODY_25",
            "--model_folder",   openpose_models,
            "--net_resolution", net_res,
            "--frame_step",     str(frame_step),
            "--display",        "0",
            "--render_pose",    "0",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=7200,
            cwd=os.path.dirname(openpose_bin),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"OpenPoseDemo.exe exited {result.returncode}:\n"
                + (result.stderr or "")[-600:])

        json_files = sorted(
            f for f in os.listdir(tmp_dir) if f.endswith("_keypoints.json"))
        if not json_files:
            raise RuntimeError("OpenPose produced no keypoint JSON files.")

        rows = []
        for i, jf in enumerate(json_files):
            frame_idx = i * frame_step
            t_sec     = frame_idx / fps
            try:
                with open(os.path.join(tmp_dir, jf)) as fh:
                    d = json.load(fh)
                people = d.get("people", [])
                if not people:
                    rows.append(_nan_row(frame_idx, t_sec))
                    continue
                best_p  = max(people, key=lambda p: sum(
                    p["pose_keypoints_2d"][2::3]) / max(1, len(p["pose_keypoints_2d"]) // 3))
                kp25 = np.array(best_p["pose_keypoints_2d"], dtype=float).reshape(-1, 3)
            except Exception:
                rows.append(_nan_row(frame_idx, t_sec))
                continue

            def _kp(idx):
                return kp25[idx, :2], float(kp25[idx, 2])

            r_h, r_hs = _kp(_B25["R_HIP"]);   r_k, r_ks = _kp(_B25["R_KNEE"]); r_a, r_as = _kp(_B25["R_ANKLE"])
            l_h, l_hs = _kp(_B25["L_HIP"]);   l_k, l_ks = _kp(_B25["L_KNEE"]); l_a, l_as = _kp(_B25["L_ANKLE"])

            if l_hs + l_ks + l_as >= r_hs + r_ks + r_as:
                leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s = "left",  l_h, l_hs, l_k, l_ks, l_a, l_as
            else:
                leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s = "right", r_h, r_hs, r_k, r_ks, r_a, r_as

            ok3 = h_s >= score_thresh and k_s >= score_thresh and a_s >= score_thresh
            ang = _angle_deg(hip_xy, knee_xy, ank_xy) if ok3 else float("nan")
            rows.append({
                "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
                "hip_x":   round(float(hip_xy[0]), 2),  "hip_y":   round(float(hip_xy[1]), 2),  "hip_score":   round(h_s, 4),
                "knee_x":  round(float(knee_xy[0]), 2), "knee_y":  round(float(knee_xy[1]), 2), "knee_score":  round(k_s, 4),
                "ankle_x": round(float(ank_xy[0]), 2),  "ankle_y": round(float(ank_xy[1]), 2),  "ankle_score": round(a_s, 4),
                "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
            })
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid samples (eff. {effective_fps:.1f} fps)")


# ─────────────────────────────────────────────────────────────────────────────
# MODEL REGISTRY
# Add new entries here to include additional models in the pipeline.
# ─────────────────────────────────────────────────────────────────────────────

_YOLO_PATH      = os.path.join(BASE_DIR, "models", "yolo26n-pose.pt")
_OPENPOSE_BIN    = os.path.join(BASE_DIR, "models", "openpose", "bin", "OpenPoseDemo.exe")
_OPENPOSE_MODELS = os.path.join(BASE_DIR, "models", "openpose", "models")
_DETR_DIR    = os.path.join(BASE_DIR, "models", "DETRPose-main")
_DETR_CFG    = os.path.join(_DETR_DIR, "configs", "detrpose", "detrpose_hgnetv2_s.py")
_DETR_WTS    = os.path.join(_DETR_DIR, "weights", "detrpose_hgnetv2_s.pth")


# ─────────────────────────────────────────────────────────────────────────────
# OPTITRACK HELPERS  (for comparison plots)
# ─────────────────────────────────────────────────────────────────────────────

def _find_optitrack(pid: str, pos: str, trial: str) -> Optional[str]:
    for root in OPTI_ROOTS:
        for path in glob.glob(os.path.join(root, "**", "*.csv"), recursive=True):
            m = _OPTI_RE.search(os.path.basename(path))
            if not m or m.group(1) != trial:
                continue
            p2, ps, _ = _path_meta(path)
            if p2 == pid and ps == pos:
                return path
    return None


def _load_optitrack_angle(csv_path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load OptiTrack CSV → (time_s, knee_angle_deg) using quaternion columns."""
    from scipy.spatial.transform import Rotation as _R
    header_idx = 0
    with open(csv_path, encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header_idx = i
                break
    df = (pd.read_csv(csv_path, skiprows=header_idx)
            .apply(pd.to_numeric, errors="coerce")
            .ffill().bfill())
    t = df.iloc[:, 1].values.astype(float)
    t -= t[0]
    tx, ty, tz, tw = (df.iloc[:, c].values for c in [2, 3, 4, 5])
    sx, sy, sz, sw = (df.iloc[:, c].values for c in [9, 10, 11, 12])
    r_thigh = _R.from_quat(np.column_stack([tx, ty, tz, tw]))
    r_shank = _R.from_quat(np.column_stack([sx, sy, sz, sw]))
    ang = np.degrees((r_thigh.inv() * r_shank).magnitude())
    return t, ang


def _load_model_angle(csv_path: str) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    df = pd.read_csv(csv_path)
    if "time_sec" not in df.columns or "knee_angle_deg" not in df.columns:
        return None, None
    df = df.dropna(subset=["knee_angle_deg"])
    if df.empty:
        return None, None
    return df["time_sec"].values, df["knee_angle_deg"].values


def _plot_trial_comparison(pid, pos, trial, o_t, o_ang, traces) -> None:
    os.makedirs(PLOT_DIR, exist_ok=True)
    fig, ax = plt.subplots(figsize=(13, 5))
    fig.patch.set_facecolor("#111122")
    ax.set_facecolor("#1a1a2e")

    for tr in traces:
        ax.plot(tr["t"], tr["ang"], color=tr["color"], lw=1.4, alpha=0.80, label=tr["label"])
    ax.plot(o_t, o_ang, color="white", lw=2.5, zorder=5, label="OptiTrack GT")

    ax.set_xlabel("Time (s)", color="#cccccc")
    ax.set_ylabel("Knee Angle (deg)", color="#cccccc")
    ax.set_title(f"P{pid} | Pos{pos} | Trial{trial} — Knee Angle vs Time",
                 color="white", fontsize=13)
    ax.tick_params(colors="#aaaaaa")
    for sp in ax.spines.values():
        sp.set_edgecolor("#444466")
    ax.legend(fontsize=8, facecolor="#0f0f1a", labelcolor="white",
              edgecolor="#444466", ncol=3, loc="upper right")

    out = os.path.join(PLOT_DIR, f"P{pid}_Pos{pos}_T{trial}_angle_comparison.png")
    plt.tight_layout()
    plt.savefig(out, dpi=140, facecolor=fig.get_facecolor())
    plt.close()
    print(f"    → {os.path.basename(out)}")


def phase_comparison_plots(videos: List[dict]) -> None:
    print(f"\n{'='*70}")
    print("PHASE 3 — COMPARISON PLOTS  (all models vs OptiTrack)")
    print(f"{'='*70}")
    n_plots = 0
    for vid in videos:
        pid, pos, trial = vid["pid"], vid["pos"], vid["trial"]
        height  = vid["height"]
        out_dir = vid["out_dir"]

        opti_path = _find_optitrack(pid, pos, trial)
        if opti_path is None:
            print(f"  [no optitrack] P{pid}/Pos{pos}/T{trial}")
            continue
        try:
            o_t, o_ang = _load_optitrack_angle(opti_path)
        except Exception as exc:
            print(f"  [optitrack error] P{pid}/Pos{pos}/T{trial}: {exc}")
            continue

        traces = []
        for i, spec in enumerate(MODEL_REGISTRY):
            csv_name = _csv_name(pid, pos, height, trial, spec)
            csv_path = os.path.join(out_dir, csv_name)
            if not os.path.isfile(csv_path):
                continue
            m_t, m_ang = _load_model_angle(csv_path)
            if m_t is None:
                continue
            traces.append(dict(label=spec.variant_tag, t=m_t, ang=m_ang,
                               color=PLOT_PALETTE[i % len(PLOT_PALETTE)]))

        if not traces:
            print(f"  [no model data] P{pid}/Pos{pos}/T{trial}")
            continue

        print(f"  P{pid}/Pos{pos}/T{trial} — {len(traces)} model(s) + OptiTrack")
        _plot_trial_comparison(pid, pos, trial, o_t, o_ang, traces)
        n_plots += 1

    print(f"\nPhase 3 complete: {n_plots} plot(s) saved to {PLOT_DIR}")


MODEL_REGISTRY: List[ModelSpec] = [
    # ── YOLO ────────────────────────────────────────────────────────────────
    ModelSpec("yolo", "n", 0.5, run_yolo,
              {"model_path": _YOLO_PATH, "score_thresh": 0.5, "frame_step": 5}),

    # ── DETRPose ─────────────────────────────────────────────────────────────
    ModelSpec("detrpose", "s", 0.5, run_detrpose,
              {"detr_dir": _DETR_DIR, "config_path": _DETR_CFG,
               "weight_path": _DETR_WTS, "score_thresh": 0.5, "frame_step": 5}),

    # ── Pose2Sim (rtmlib) ───────────────────────────────────────────────────
    ModelSpec("pose2sim", "lightweight",  0.3, run_pose2sim, {"mode": "lightweight",  "score_thresh": 0.3}),
    ModelSpec("pose2sim", "balanced",     0.3, run_pose2sim, {"mode": "balanced",     "score_thresh": 0.3}),
    ModelSpec("pose2sim", "performance",  0.3, run_pose2sim, {"mode": "performance",  "score_thresh": 0.3}),

    # ── MoveNet ─────────────────────────────────────────────────────────────
    ModelSpec("movenet", "lightning", 0.3, run_movenet,
              {"variant": "lightning", "input_size": 192, "score_thresh": 0.3,
               "hub_url": "https://tfhub.dev/google/movenet/singlepose/lightning/4"}),
    ModelSpec("movenet", "thunder", 0.3, run_movenet,
              {"variant": "thunder", "input_size": 256, "score_thresh": 0.3,
               "hub_url": "https://tfhub.dev/google/movenet/singlepose/thunder/4"}),

    # ── ViTPose ──────────────────────────────────────────────────────────────
    # Weights auto-downloaded from HuggingFace (JunkyByte/easy_ViTPose) on first run.
    ModelSpec("vitpose", "b", 0.3, run_vitpose,
              {"variant": "b", "score_thresh": 0.3,
               "vitpose_url": "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/coco_25/vitpose-b-coco_25.onnx"}),
    ModelSpec("vitpose", "l", 0.3, run_vitpose,
              {"variant": "l", "score_thresh": 0.3,
               "vitpose_url": "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/coco_25/vitpose-l-coco_25.onnx"}),

    # ── RTMO (single-stage, no detector) ─────────────────────────────────────
    # Weights auto-downloaded from OpenMMLab CDN on first run.
    ModelSpec("rtmo", "s", 0.3, run_rtmo,
              {"variant": "s", "score_thresh": 0.3,
               "rtmo_url": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-s_8xb32-700e_body7-640x640-dac2bf74_20231211.zip"}),
    ModelSpec("rtmo", "m", 0.3, run_rtmo,
              {"variant": "m", "score_thresh": 0.3,
               "rtmo_url": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip"}),
    ModelSpec("rtmo", "l", 0.3, run_rtmo,
              {"variant": "l", "score_thresh": 0.3,
               "rtmo_url": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-l_16xb16-600e_body7-640x640-b5bf8f02_20231211.zip"}),

    # ── OpenPose ─────────────────────────────────────────────────────────────
    ModelSpec("openpose", "body25", 0.1, run_openpose,
              {"openpose_bin":    _OPENPOSE_BIN,
               "openpose_models": _OPENPOSE_MODELS,
               "frame_step":      10,
               "score_thresh":    0.1,
               "net_resolution":  "-1x176"}),

    # ── ADD NEW MODELS BELOW ────────────────────────────────────────────────
    # ModelSpec("myfamily", "myvariant", 0.5, run_mymodel,
    #           {"param_key": "value", ...}),
]


# ─────────────────────────────────────────────────────────────────────────────
# CSV PATH BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def _csv_name(pid, pos, height, trial, spec: ModelSpec) -> str:
    return (f"P_{pid}_Pos_{pos}_H_{height}_T_{trial}_"
            f"{spec.family}_{spec.complexity}_{spec.threshold}.csv")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — INFERENCE
# ─────────────────────────────────────────────────────────────────────────────

def phase_inference(videos: List[dict]) -> None:
    print(f"\n{'='*70}")
    print("PHASE 1 — INFERENCE")
    print(f"{'='*70}")
    total_run = 0
    for spec in MODEL_REGISTRY:
        print(f"\n  Model: {spec.variant_tag}")
        for vid in videos:
            pid, pos, trial = vid["pid"], vid["pos"], vid["trial"]
            height    = vid["height"]
            out_dir   = vid["out_dir"]
            csv_name  = _csv_name(pid, pos, height, trial, spec)
            csv_path  = os.path.join(out_dir, csv_name)

            if os.path.isfile(csv_path):
                print(f"    [skip] P{pid}/Pos{pos}/T{trial} — CSV exists")
                continue

            print(f"    [run]  P{pid}/Pos{pos}/T{trial} -> {csv_name}")
            try:
                runner_params = dict(spec.params)
                if vid.get("leg_side"):
                    runner_params["leg_side"] = vid["leg_side"]
                spec.runner(vid["video_path"], csv_path, **runner_params)
                total_run += 1
            except Exception as exc:
                print(f"    [ERROR] {exc}")

    print(f"\nPhase 1 complete. Ran inference for {total_run} new (video, model) pair(s).")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — ANNOTATED VIDEOS
# ─────────────────────────────────────────────────────────────────────────────

def phase_annotate(csvs: List[dict]) -> None:
    print(f"\n{'='*70}")
    print("PHASE 2 — ANNOTATED VIDEOS")
    print(f"{'='*70}")
    rendered = skipped = no_video = 0

    for entry in csvs:
        raw_video      = entry["raw_video"]
        csv_path       = entry["csv_path"]
        annotated_path = entry["annotated_path"]
        label = (f"P{entry['pid']} Pos{entry['pos']} T{entry['trial']} | "
                 f"{entry['family']}_{entry['complexity']}_{entry['threshold']}")

        if os.path.isfile(annotated_path):
            skipped += 1
            continue
        if raw_video is None or not os.path.isfile(raw_video):
            print(f"  [no video] {os.path.basename(csv_path)}")
            no_video += 1
            continue

        print(f"  Rendering: {os.path.basename(annotated_path)}")
        try:
            render_annotated_video(csv_path, raw_video, annotated_path, label)
            rendered += 1
        except Exception as exc:
            print(f"  [ERROR] {exc}")

    print(f"\nPhase 2 complete: {rendered} rendered, {skipped} already existed, {no_video} no raw video.")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3 — EVALUATION
# ─────────────────────────────────────────────────────────────────────────────

def phase_evaluate() -> None:
    print(f"\n{'='*70}")
    print("PHASE 3 — EVALUATION")
    print(f"{'='*70}")
    result = subprocess.run(
        [sys.executable, EVAL_SCRIPT],
        cwd=BASE_DIR,
    )
    if result.returncode != 0:
        print("[WARN] evaluate_all_participants.py exited with non-zero code")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Pendulastic Master Pipeline")
    print(f"  Base dir : {BASE_DIR}")
    print(f"  Models   : {len(MODEL_REGISTRY)} registered")

    videos = discover_videos()
    print(f"  Videos   : {len(videos)} raw trial video(s) found\n")
    for v in videos:
        print(f"    P{v['pid']} Pos{v['pos']} T{v['trial']} — {os.path.basename(v['video_path'])}")

    phase_inference(videos)

    # Re-discover CSVs after inference so newly generated ones are included
    csvs = discover_csvs()
    print(f"\n  CSVs discovered (all models): {len(csvs)}")

    phase_annotate(csvs)
    phase_comparison_plots(videos)
    phase_evaluate()

    print(f"\n{'='*70}")
    print("Pipeline complete.")
    print(f"  Annotated videos : alongside each model CSV")
    print(f"  Comparison plots : {PLOT_DIR}")


if __name__ == "__main__":
    main()
