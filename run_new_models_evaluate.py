"""
run_new_models_evaluate.py
==========================
Focused two-phase script for OpenPose, ViTPose, and RTMO:

  PHASE 1 — INFERENCE
    Runs OpenPose (body25), ViTPose (b, l), and RTMO (s, m, l) on all
    existing P0 / P1 trial videos.  Skips any CSV that already exists.

  PHASE 2 — EVALUATE & PLOT
    For every new-model CSV found, loads the paired OptiTrack gold standard,
    computes per-trial RMSE at oscillation extrema, and saves:
      new_models_analysis/
        trial_P{pid}_Pos{pos}_T{trial}_overlay.png  — flexion traces
        rmse_bar.png                                 — ranked RMSE bar chart
        rmse_heatmap.png                             — trial × model heat-map
        new_model_leaderboard.csv                    — sortable results table

Run from the Pendulastic directory:
    .venv\\Scripts\\python.exe run_new_models_evaluate.py
"""
from __future__ import annotations

import csv
import glob
import importlib.util
import math
import os
import re
import sys
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation as R

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR      = r"C:\Users\cladi\Pendulastic"
OUT_DIR       = os.path.join(BASE_DIR, "new_models_analysis")
OPTI_ROOTS    = [
    os.path.join(BASE_DIR, "OptiTrack_Recordings"),
]
VIDEO_ROOTS   = [
    os.path.join(BASE_DIR, "OptiTrack_Recordings"),
    os.path.join(BASE_DIR, "Recordings"),
]

OPENPOSE_BIN    = os.path.join(BASE_DIR, "models", "openpose", "bin", "OpenPoseDemo.exe")
OPENPOSE_MODELS = os.path.join(BASE_DIR, "models", "openpose", "models")

HRNET_PYTHON  = r"C:\Users\cladi\miniconda3\envs\openmmlab\python.exe"
HRNET_WORKER  = os.path.join(BASE_DIR, "hrnet_worker.py")
HRNET_CONFIG  = r"C:\Users\cladi\mmpose\configs\body_2d_keypoint\topdown_heatmap\coco\td-hm_hrnet-w48_8xb32-210e_coco-256x192.py"
HRNET_CKPT    = os.path.join(BASE_DIR, "models", "PosePipeline", "checkpoints",
                              "td-hm_hrnet-w48_8xb32-210e_coco-256x192-0e67c616_20220913.pth")

VITPOSE_DET_URL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/"
    "yolox_m_8xb8-300e_humanart-c2c7a14a.zip"
)
VITPOSE_URLS = {
    "b": "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/coco_25/vitpose-b-coco_25.onnx",
    "l": "https://huggingface.co/JunkyByte/easy_ViTPose/resolve/main/onnx/coco_25/vitpose-l-coco_25.onnx",
}
RTMO_URLS = {
    "s": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-s_8xb32-700e_body7-640x640-dac2bf74_20231211.zip",
    "m": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-m_16xb16-600e_body7-640x640-39e78cc4_20231211.zip",
    "l": "https://download.openmmlab.com/mmpose/v1/projects/rtmo/onnx_sdk/rtmo-l_16xb16-600e_body7-640x640-b5bf8f02_20231211.zip",
}

os.makedirs(OUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# TARGET FILTER  (Option A — directory targeting)
# Set to a non-empty list of pid substrings to restrict which participants are
# processed.  Any pid that contains at least one of these substrings is included.
# Leave as an empty list [] to process ALL discovered participants.
#
# Examples:
#   TARGET_PARTICIPANTS = ["2_left", "2_right"]   # only P2 left/right trials
#   TARGET_PARTICIPANTS = ["2_"]                  # all P2 variants
#   TARGET_PARTICIPANTS = []                      # everyone (no filter)
# ─────────────────────────────────────────────────────────────────────────────
TARGET_PARTICIPANTS: List[str] = []

# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

CSV_FIELDS = [
    "frame", "time_sec", "leg",
    "hip_x", "hip_y", "hip_score",
    "knee_x", "knee_y", "knee_score",
    "ankle_x", "ankle_y", "ankle_score",
    "knee_angle_deg",
]

PALETTE = [
    "#E65100", "#1976D2", "#2E7D32", "#6A1B9A",
    "#B71C1C", "#00695C", "#F57F17", "#4527A0",
]

MODEL_LABELS = {
    "openpose_body25_0.1": "OpenPose B25",
    "hrnet_w48_0.3":       "HRNet-W48 (PosePipe)",
    "vitpose_b_0.3":       "ViTPose-B",
    "vitpose_l_0.3":       "ViTPose-L",
    "rtmo_s_0.3":          "RTMO-S",
    "rtmo_m_0.3":          "RTMO-M",
    "rtmo_l_0.3":          "RTMO-L",
}


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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader(); w.writerows(rows)


def _leg_side_from_pid(pid: str) -> Optional[str]:
    """Extract 'left' or 'right' from participant ids like '2_left_duo'."""
    pid_lower = pid.lower()
    if "left" in pid_lower:
        return "left"
    if "right" in pid_lower:
        return "right"
    return None


def _pick_leg(kp, sc, leg_side: Optional[str] = None):
    if leg_side == "left":
        return "left",  kp[L_HIP], sc[L_HIP], kp[L_KNEE], sc[L_KNEE], kp[L_ANKLE], sc[L_ANKLE]
    if leg_side == "right":
        return "right", kp[R_HIP], sc[R_HIP], kp[R_KNEE], sc[R_KNEE], kp[R_ANKLE], sc[R_ANKLE]
    l_c = sc[L_HIP] + sc[L_KNEE] + sc[L_ANKLE]
    r_c = sc[R_HIP] + sc[R_KNEE] + sc[R_ANKLE]
    if l_c >= r_c:
        return "left",  kp[L_HIP], sc[L_HIP], kp[L_KNEE], sc[L_KNEE], kp[L_ANKLE], sc[L_ANKLE]
    return "right", kp[R_HIP], sc[R_HIP], kp[R_KNEE], sc[R_KNEE], kp[R_ANKLE], sc[R_ANKLE]


def _best_person_idx(scores: np.ndarray, leg_side: Optional[str]) -> int:
    """For multi-person (N, K) score arrays, pick by designated leg confidence."""
    if leg_side == "left":
        return int(np.argmax(scores[:, L_HIP] + scores[:, L_KNEE] + scores[:, L_ANKLE]))
    if leg_side == "right":
        return int(np.argmax(scores[:, R_HIP] + scores[:, R_KNEE] + scores[:, R_ANKLE]))
    return int(np.argmax(scores.mean(axis=1)))


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO / OPTITRACK DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

_VID_RE  = re.compile(r"^trial_(\d+)\.(mp4|avi)$", re.I)
_PID_RE  = re.compile(r"Participant_(\w+)", re.I)
_POS_RE  = re.compile(r"Position_(\w+)",   re.I)
_HGT_RE  = re.compile(r"Height_(.+)",      re.I)
_OPTI_RE = re.compile(r"trial_(\d+)_optitrack\.csv$", re.I)
_CSV_RE  = re.compile(
    r"^P_(\w+)_Pos_(\w+)_H_.+?_T_(\d+)_([A-Za-z]\w*)_(.+)\.csv$", re.I)


def _path_meta(path: str):
    parts = path.replace("\\", "/").split("/")
    pid = pos = height = None
    for p in parts:
        m = _PID_RE.match(p)
        if m: pid = m.group(1)
        m = _POS_RE.match(p)
        if m: pos = m.group(1)
        m = _HGT_RE.match(p)
        if m: height = m.group(1)
    return pid, pos, height


def discover_videos() -> List[dict]:
    found = []; seen = set()
    for root in VIDEO_ROOTS:
        if not os.path.isdir(root): continue
        for ext in ("*.mp4", "*.avi"):
            for path in glob.glob(os.path.join(root, "**", ext), recursive=True):
                bn = os.path.basename(path)
                m  = _VID_RE.match(bn)
                if not m: continue
                trial = m.group(1)
                pid, pos, height = _path_meta(path)
                if pid and pos and height:
                    if TARGET_PARTICIPANTS and not any(t in pid for t in TARGET_PARTICIPANTS):
                        continue
                    key = (pid, pos, trial)
                    if key not in seen:
                        seen.add(key)
                        found.append(dict(pid=pid, pos=pos, trial=trial,
                                          height=height,
                                          leg_side=_leg_side_from_pid(pid),
                                          video_path=path,
                                          out_dir=os.path.dirname(path)))
    return found


def find_optitrack(pid: str, pos: str, trial: str) -> Optional[str]:
    for root in OPTI_ROOTS:
        for path in glob.glob(os.path.join(root, "**", "*.csv"), recursive=True):
            m = _OPTI_RE.search(os.path.basename(path))
            if not m or m.group(1) != trial: continue
            p, ps, _ = _path_meta(path)
            if p == pid and ps == pos:
                return path
    return None


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — RUNNER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_openpose(video_path: str, csv_path: str, leg_side: Optional[str] = None) -> None:
    if not os.path.isfile(OPENPOSE_BIN):
        raise FileNotFoundError(f"OpenPoseDemo.exe not found: {OPENPOSE_BIN}")

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    frame_step = 10
    eff_fps    = fps / frame_step
    print(f"    OpenPose B25 | {total} frames @ {fps:.0f}fps | step={frame_step} -> {eff_fps:.1f}fps")

    _B25 = {"R_HIP": 9, "R_KNEE": 10, "R_ANKLE": 11,
             "L_HIP": 12, "L_KNEE": 13, "L_ANKLE": 14}

    tmp = tempfile.mkdtemp(prefix="op_json_")
    try:
        cmd = [OPENPOSE_BIN,
               "--video",          os.path.abspath(video_path),
               "--write_json",     tmp,
               "--model_pose",     "BODY_25",
               "--model_folder",   OPENPOSE_MODELS,
               "--net_resolution", "-1x176",
               "--frame_step",     str(frame_step),
               "--display",        "0",
               "--render_pose",    "0"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=7200,
                             cwd=os.path.dirname(OPENPOSE_BIN))
        if res.returncode != 0:
            raise RuntimeError(f"OpenPoseDemo.exe failed: {(res.stderr or '')[-400:]}")

        import json
        json_files = sorted(f for f in os.listdir(tmp) if f.endswith("_keypoints.json"))
        if not json_files:
            raise RuntimeError("OpenPose produced no JSON files.")

        rows = []
        for i, jf in enumerate(json_files):
            fi = i * frame_step; t = fi / fps
            try:
                with open(os.path.join(tmp, jf)) as fh:
                    d = json.load(fh)
                people = d.get("people", [])
                if not people:
                    rows.append(_nan_row(fi, t)); continue
                best = max(people, key=lambda p: sum(p["pose_keypoints_2d"][2::3]) /
                           max(1, len(p["pose_keypoints_2d"]) // 3))
                kp25 = np.array(best["pose_keypoints_2d"]).reshape(-1, 3)
            except Exception:
                rows.append(_nan_row(fi, t)); continue

            def _kp(idx): return kp25[idx, :2], float(kp25[idx, 2])
            r_h,r_hs = _kp(_B25["R_HIP"]); r_k,r_ks = _kp(_B25["R_KNEE"]); r_a,r_as = _kp(_B25["R_ANKLE"])
            l_h,l_hs = _kp(_B25["L_HIP"]); l_k,l_ks = _kp(_B25["L_KNEE"]); l_a,l_as = _kp(_B25["L_ANKLE"])
            use_left = (leg_side == "left") or (leg_side is None and l_hs+l_ks+l_as >= r_hs+r_ks+r_as)
            if use_left:
                leg,hip,h_s,kne,k_s,ank,a_s = "left",l_h,l_hs,l_k,l_ks,l_a,l_as
            else:
                leg,hip,h_s,kne,k_s,ank,a_s = "right",r_h,r_hs,r_k,r_ks,r_a,r_as
            ok3 = h_s >= 0.1 and k_s >= 0.1 and a_s >= 0.1
            ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")
            rows.append({"frame": fi, "time_sec": round(t, 6), "leg": leg,
                         "hip_x": round(float(hip[0]),2), "hip_y": round(float(hip[1]),2), "hip_score": round(h_s,4),
                         "knee_x": round(float(kne[0]),2), "knee_y": round(float(kne[1]),2), "knee_score": round(k_s,4),
                         "ankle_x": round(float(ank[0]),2), "ankle_y": round(float(ank[1]),2), "ankle_score": round(a_s,4),
                         "knee_angle_deg": round(ang,4) if not math.isnan(ang) else float("nan")})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid  (eff. {eff_fps:.1f}fps)")


def run_vitpose(video_path: str, csv_path: str, variant: str, leg_side: Optional[str] = None) -> None:
    import onnxruntime as ort, torch as _torch
    from rtmlib import Custom

    if _torch.cuda.is_available() and "CUDAExecutionProvider" in ort.get_available_providers():
        backend, device = "onnxruntime", "cuda"
    else:
        backend, device = "onnxruntime", "cpu"

    print(f"    ViTPose-{variant} | loading... device={device}  leg={leg_side or 'auto'}")
    model = Custom(
        det_class="YOLOX", det=VITPOSE_DET_URL, det_input_size=(640, 640),
        pose_class="ViTPose", pose=VITPOSE_URLS[variant], pose_input_size=(192, 256),
        backend=backend, device=device,
    )

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    ViTPose-{variant} | {total} frames @ {fps:.0f}fps  device={device}")

    rows = []; fi = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        t = fi / fps
        kps, scs = model(frame)
        if kps is None or len(kps) == 0:
            rows.append(_nan_row(fi, t))
        else:
            best = _best_person_idx(scs, leg_side)
            leg, hip, h_s, kne, k_s, ank, a_s = _pick_leg(kps[best], scs[best], leg_side)
            ok3 = h_s >= 0.3 and k_s >= 0.3 and a_s >= 0.3
            ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")
            rows.append({"frame": fi, "time_sec": round(t,6), "leg": leg,
                         "hip_x": round(float(hip[0]),2), "hip_y": round(float(hip[1]),2), "hip_score": round(float(h_s),4),
                         "knee_x": round(float(kne[0]),2), "knee_y": round(float(kne[1]),2), "knee_score": round(float(k_s),4),
                         "ankle_x": round(float(ank[0]),2), "ankle_y": round(float(ank[1]),2), "ankle_score": round(float(a_s),4),
                         "knee_angle_deg": round(ang,4) if not math.isnan(ang) else float("nan")})
        fi += 1
        if fi % 300 == 0: print(f"      frame {fi}/{total}", flush=True)
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid")


def run_rtmo(video_path: str, csv_path: str, variant: str, leg_side: Optional[str] = None) -> None:
    import onnxruntime as ort, torch as _torch
    from rtmlib import Custom

    if _torch.cuda.is_available() and "CUDAExecutionProvider" in ort.get_available_providers():
        backend, device = "onnxruntime", "cuda"
    else:
        backend, device = "onnxruntime", "cpu"

    print(f"    RTMO-{variant} | loading...  device={device}  leg={leg_side or 'auto'}")
    model = Custom(
        pose_class="RTMO", pose=RTMO_URLS[variant], pose_input_size=(640, 640),
        backend=backend, device=device,
    )

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"    RTMO-{variant} | {total} frames @ {fps:.0f}fps  device={device}")

    rows = []; fi = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        t = fi / fps
        kps, scs = model(frame)
        if kps is None or len(kps) == 0:
            rows.append(_nan_row(fi, t))
        else:
            best = _best_person_idx(scs, leg_side)
            leg, hip, h_s, kne, k_s, ank, a_s = _pick_leg(kps[best], scs[best], leg_side)
            ok3 = h_s >= 0.3 and k_s >= 0.3 and a_s >= 0.3
            ang = _angle_deg(hip, kne, ank) if ok3 else float("nan")
            rows.append({"frame": fi, "time_sec": round(t,6), "leg": leg,
                         "hip_x": round(float(hip[0]),2), "hip_y": round(float(hip[1]),2), "hip_score": round(float(h_s),4),
                         "knee_x": round(float(kne[0]),2), "knee_y": round(float(kne[1]),2), "knee_score": round(float(k_s),4),
                         "ankle_x": round(float(ank[0]),2), "ankle_y": round(float(ank[1]),2), "ankle_score": round(float(a_s),4),
                         "knee_angle_deg": round(ang,4) if not math.isnan(ang) else float("nan")})
        fi += 1
        if fi % 300 == 0: print(f"      frame {fi}/{total}", flush=True)
    cap.release()
    _write_csv(rows, csv_path)
    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {valid}/{len(rows)} valid")


def run_hrnet(video_path: str, csv_path: str, leg_side: Optional[str] = None) -> None:
    """Runs HRNet-W48-COCO via hrnet_worker.py using the openmmlab conda env (Python 3.8)."""
    if not os.path.isfile(HRNET_PYTHON):
        raise FileNotFoundError(f"openmmlab Python not found: {HRNET_PYTHON}")
    if not os.path.isfile(HRNET_CKPT):
        raise FileNotFoundError(f"HRNet checkpoint not found: {HRNET_CKPT}\n"
                                 "Run download_pipeline_deps.py first.")
    if not os.path.isfile(HRNET_CONFIG):
        raise FileNotFoundError(f"HRNet config not found: {HRNET_CONFIG}")

    cmd = [
        HRNET_PYTHON, HRNET_WORKER,
        "--video",  video_path,
        "--csv",    csv_path,
        "--config", HRNET_CONFIG,
        "--ckpt",   HRNET_CKPT,
        "--score-thresh", "0.3",
    ]
    if leg_side:
        cmd += ["--leg-side", leg_side]
    env = dict(os.environ, KMP_DUPLICATE_LIB_OK="TRUE")
    print(f"    HRNet-W48 | running via openmmlab env...  leg={leg_side or 'auto'}", flush=True)
    res = subprocess.run(cmd, capture_output=False, text=True,
                         timeout=7200, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"hrnet_worker.py exited with code {res.returncode}")


NEW_MODELS = [
    # OpenPose excluded: too slow on CPU (~30 min/trial). Re-enable if GPU available.
    # ("openpose", "body25", "0.1", lambda vp, cp, ls: run_openpose(vp, cp, ls)),
    ("hrnet",    "w48",    "0.3", lambda vp, cp, ls: run_hrnet(vp, cp, ls)),
    ("vitpose",  "b",      "0.3", lambda vp, cp, ls: run_vitpose(vp, cp, "b", ls)),
    ("vitpose",  "l",      "0.3", lambda vp, cp, ls: run_vitpose(vp, cp, "l", ls)),
    ("rtmo",     "s",      "0.3", lambda vp, cp, ls: run_rtmo(vp, cp, "s", ls)),
    ("rtmo",     "m",      "0.3", lambda vp, cp, ls: run_rtmo(vp, cp, "m", ls)),
    ("rtmo",     "l",      "0.3", lambda vp, cp, ls: run_rtmo(vp, cp, "l", ls)),
]


def phase_inference(videos: List[dict]) -> None:
    print(f"\n{'='*65}")
    print("PHASE 1 -- INFERENCE  (OpenPose | HRNet-W48 | ViTPose | RTMO)")
    print(f"{'='*65}\n")
    ran = 0
    for family, complexity, thresh, runner_fn in NEW_MODELS:
        label = f"{family}_{complexity}_{thresh}"
        # Option B — skip model load entirely if every trial's CSV already exists
        all_done = all(
            os.path.isfile(os.path.join(
                vid["out_dir"],
                f"P_{vid['pid']}_Pos_{vid['pos']}_H_{vid['height']}_T_{vid['trial']}"
                f"_{family}_{complexity}_{thresh}.csv"
            ))
            for vid in videos
        )
        if all_done:
            print(f"  -- {label} -- [SKIP: all {len(videos)} trial(s) already have CSVs]")
            continue
        print(f"  -- {label} --")
        for vid in videos:
            pid, pos, trial, height = vid["pid"], vid["pos"], vid["trial"], vid["height"]
            csv_name = f"P_{pid}_Pos_{pos}_H_{height}_T_{trial}_{family}_{complexity}_{thresh}.csv"
            csv_path = os.path.join(vid["out_dir"], csv_name)
            if os.path.isfile(csv_path):
                print(f"    [skip] P{pid}/Pos{pos}/T{trial}")
                continue
            print(f"    [run ] P{pid}/Pos{pos}/T{trial} -> {csv_name}")
            try:
                runner_fn(vid["video_path"], csv_path, vid.get("leg_side"))
                ran += 1
            except Exception as exc:
                print(f"    [ERR ] {exc}")
    print(f"\nPhase 1 done.  {ran} new inference run(s).")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — SIGNAL PROCESSING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def load_optitrack(opti_path: str):
    """Returns (time_arr, knee_angle_arr, fps_estimate)."""
    # Find header row
    header_idx = 0
    with open(opti_path, encoding="utf-8-sig") as fh:
        for i, line in enumerate(fh):
            if line.split(",")[0].strip().lower() == "frame":
                header_idx = i; break
    df = pd.read_csv(opti_path, skiprows=header_idx).apply(pd.to_numeric, errors="coerce").ffill().bfill()
    t = df.iloc[:, 1].values.astype(float); t -= t[0]
    tx,ty,tz,tw = (df.iloc[:,c].values for c in [2,3,4,5])
    sx,sy,sz,sw = (df.iloc[:,c].values for c in [9,10,11,12])
    r_thigh = R.from_quat(np.column_stack([tx,ty,tz,tw]))
    r_shank = R.from_quat(np.column_stack([sx,sy,sz,sw]))
    ang = np.degrees((r_thigh.inv() * r_shank).magnitude())
    dts = np.diff(t[t > 0])
    fps = 1.0 / float(np.median(dts)) if len(dts) else 120.0
    return t, ang, fps


def detect_optitrack_release(t, ang, fps):
    n_base = max(10, int(3.0 * fps))
    baseline = float(np.nanmean(ang[:n_base]))
    for thr in [5.0, 2.5, 2.0, 1.0]:
        hits = np.where(np.abs(ang[n_base:] - baseline) > thr)[0]
        if len(hits):
            return int(n_base + hits[0])
    return 0


def detect_model_release(ang):
    below = np.where(ang < 100)[0]
    first_below = int(below[0]) if len(below) else len(ang)
    for thr in [165, 155, 145, 135, 125]:
        mask = (np.arange(len(ang)) < first_below) & (ang >= thr)
        hits = np.where(mask)[0]
        if len(hits):
            return int(hits[-1])
    return max(0, first_below - 5)


def compute_rmse_at_extrema(o_t, o_flex, m_t, m_flex, fps_o, fps_m):
    """
    Time-align via first-peak matching, then interpolate model onto OptiTrack
    timestamps and compute RMSE only at oscillation extrema.
    Returns (rmse, n_extrema, shift_sec, aligned_m_t, aligned_m_flex)
    """
    guard_o = max(1, int(0.1 * fps_o))
    guard_m = max(1, int(0.1 * fps_m))

    def _peaks(sig, fps, guard):
        hits, _ = find_peaks(sig[guard:], distance=max(5, int(0.3*fps)),
                             prominence=3.0, height=5.0)
        return hits + guard

    o_pks = _peaks(o_flex, fps_o, guard_o)
    m_pks = _peaks(m_flex, fps_m, guard_m)

    if len(o_pks) and len(m_pks):
        shift = float(o_t[o_pks[0]]) - float(m_t[m_pks[0]])
    else:
        shift = 0.0

    m_t_shifted = m_t + shift

    # Interpolate model onto OptiTrack timestamps
    valid_o = np.isfinite(o_flex)
    valid_m = np.isfinite(m_flex)
    if valid_m.sum() < 3:
        return float("nan"), 0, shift, m_t_shifted, m_flex

    m_interp = np.interp(o_t[valid_o], m_t_shifted[valid_m], m_flex[valid_m],
                         left=float("nan"), right=float("nan"))

    # Find extrema on OptiTrack signal over common window
    common_mask = np.isfinite(m_interp)
    if common_mask.sum() < 5:
        return float("nan"), 0, shift, m_t_shifted, m_flex

    o_seg    = o_flex[valid_o][common_mask]
    o_t_seg  = o_t[valid_o][common_mask]

    # peaks + troughs
    pks_idx, _ = find_peaks(o_seg, distance=max(3, int(0.25*fps_o)), prominence=2.0)
    pts_idx, _ = find_peaks(-o_seg, distance=max(3, int(0.25*fps_o)), prominence=2.0)
    ext_idx    = np.concatenate([pks_idx, pts_idx])

    if len(ext_idx) < 2:
        # Fall back to full signal RMSE
        errors = m_interp[common_mask] - o_seg
        rmse   = float(np.sqrt(np.nanmean(errors**2)))
        return rmse, int(common_mask.sum()), shift, m_t_shifted, m_flex

    errors = m_interp[common_mask][ext_idx] - o_seg[ext_idx]
    rmse   = float(np.sqrt(np.mean(errors**2)))
    return rmse, len(ext_idx), shift, m_t_shifted, m_flex


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — LOAD MODEL CSV
# ─────────────────────────────────────────────────────────────────────────────

def load_model_signal(csv_path: str):
    """Returns (t_arr, flex_arr, fps_m) or (None, None, None)."""
    try:
        df = pd.read_csv(csv_path)
        t   = df["time_sec"].values.astype(float)
        ang = df["knee_angle_deg"].values.astype(float)
    except Exception:
        return None, None, None
    valid = np.isfinite(ang)
    if valid.sum() < 10:
        return None, None, None
    dts = np.diff(t[valid])
    fps_m = 1.0 / float(np.median(dts[dts > 0])) if len(dts) else 30.0
    rel_idx = detect_model_release(ang)
    flex  = ang[rel_idx] - ang[rel_idx:]
    t_rel = t[rel_idx:] - t[rel_idx]
    return t_rel, flex, fps_m


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1b — ANNOTATED VIDEOS  (skeleton overlay on raw video)
# ─────────────────────────────────────────────────────────────────────────────

_ANN_LINE  = (200, 200, 200)   # BGR skeleton line
_ANN_TXT   = (255, 255, 255)   # HUD text
_ANN_ANGLE = ( 50, 255, 200)   # angle readout colour
_ANN_KP    = {                  # BGR keypoint colours
    "hip":   (255, 180,  30),
    "knee":  ( 50, 220,  50),
    "ankle": ( 50, 130, 255),
}


def render_annotated_video(csv_path: str, raw_video: str, out_path: str,
                            label: str) -> None:
    df  = pd.read_csv(csv_path)
    cap = cv2.VideoCapture(raw_video)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out    = cv2.VideoWriter(out_path, fourcc, fps, (w_vid, h_vid))

    rows_by_frame: Dict[int, dict] = {int(r["frame"]): r for r in df.to_dict("records")}
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        row = rows_by_frame.get(frame_idx)

        if row is not None:
            def valid(v):
                try: return v is not None and not math.isnan(float(v))
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

            sc_map = {k: row.get(f"{k}_score", 0) for k in ("hip", "knee", "ankle")}
            for name, color in _ANN_KP.items():
                if name in pts:
                    try: sc = float(sc_map[name])
                    except: sc = 0.0
                    r = max(5, int(8 * sc))
                    cv2.circle(frame, pts[name], r,   color,   -1, cv2.LINE_AA)
                    cv2.circle(frame, pts[name], r+1, (0,0,0),  1, cv2.LINE_AA)

            angle = row.get("knee_angle_deg")
            if "knee" in pts and valid(angle):
                kx, ky  = pts["knee"]
                ang_txt = f"{float(angle):.1f}deg"
                (tw, th), _ = cv2.getTextSize(ang_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
                cv2.rectangle(frame, (kx+8, ky-th-6), (kx+8+tw+4, ky+4), (0,0,0), -1)
                cv2.putText(frame, ang_txt, (kx+10, ky),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, _ANN_ANGLE, 2, cv2.LINE_AA)

        else:
            cv2.putText(frame, "NO DATA", (10, h_vid-10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)

        t_sec = frame_idx / fps
        for i, txt in enumerate([label, f"frame {frame_idx}/{total}", f"t = {t_sec:.2f}s"]):
            (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            x = w_vid - tw - 8;  y = 18 + i * 20
            cv2.rectangle(frame, (x-3, y-th-3), (x+tw+3, y+4), (0,0,0), -1)
            cv2.putText(frame, txt, (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, _ANN_TXT, 1, cv2.LINE_AA)

        out.write(frame)
        frame_idx += 1

    cap.release()
    out.release()


def phase_annotate_new(videos: List[dict]) -> None:
    print(f"\n{'='*65}")
    print("PHASE 1b — ANNOTATED VIDEOS")
    print(f"{'='*65}\n")
    rendered = skipped = no_video = 0
    for family, complexity, thresh, _ in NEW_MODELS:
        for vid in videos:
            pid, pos, trial, height = vid["pid"], vid["pos"], vid["trial"], vid["height"]
            raw_video = vid["video_path"]
            csv_name  = f"P_{pid}_Pos_{pos}_H_{height}_T_{trial}_{family}_{complexity}_{thresh}.csv"
            csv_path  = os.path.join(vid["out_dir"], csv_name)
            ann_path  = csv_path.replace(".csv", "_annotated.mp4")
            label     = f"{family}_{complexity}_{thresh} | P{pid} Pos{pos} T{trial}"

            if not os.path.isfile(csv_path):
                continue
            if os.path.isfile(ann_path):
                skipped += 1
                continue
            if not os.path.isfile(raw_video):
                no_video += 1
                continue

            print(f"  Rendering: {os.path.basename(ann_path)}")
            try:
                render_annotated_video(csv_path, raw_video, ann_path, label)
                rendered += 1
            except Exception as exc:
                print(f"  [ERROR] {exc}")

    print(f"\nPhase 1b complete: {rendered} rendered, {skipped} existed, {no_video} no raw video.")


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2 — EVALUATE + PLOT
# ─────────────────────────────────────────────────────────────────────────────

def phase_evaluate(videos: List[dict]) -> pd.DataFrame:
    print(f"\n{'='*65}")
    print("PHASE 2 — EVALUATE & PLOT")
    print(f"{'='*65}\n")

    rows_lb = []

    for vid in videos:
        pid, pos, trial, height = vid["pid"], vid["pos"], vid["trial"], vid["height"]
        opti_path = find_optitrack(pid, pos, trial)
        if opti_path is None:
            print(f"  [no optitrack] P{pid}/Pos{pos}/T{trial} — skipping")
            continue

        print(f"  Trial P{pid}/Pos{pos}/T{trial}")
        o_t_raw, o_ang, fps_o = load_optitrack(opti_path)
        rel_o   = detect_optitrack_release(o_t_raw, o_ang, fps_o)
        o_flex  = o_ang[rel_o:] - o_ang[rel_o]
        o_t_rel = o_t_raw[rel_o:] - o_t_raw[rel_o]

        # Collect new-model traces for this trial
        trial_traces = []
        for family, complexity, thresh, _ in NEW_MODELS:
            tag   = f"{family}_{complexity}_{thresh}"
            label = MODEL_LABELS.get(tag, tag)
            csv_name = f"P_{pid}_Pos_{pos}_H_{height}_T_{trial}_{family}_{complexity}_{thresh}.csv"
            csv_path = os.path.join(vid["out_dir"], csv_name)
            if not os.path.isfile(csv_path):
                print(f"    [missing] {csv_name}")
                continue
            m_t, m_flex, fps_m = load_model_signal(csv_path)
            if m_t is None:
                print(f"    [no data] {label}")
                continue
            rmse, n_ext, shift, m_t_s, _ = compute_rmse_at_extrema(
                o_t_rel, o_flex, m_t, m_flex, fps_o, fps_m)
            print(f"    {label:<18} RMSE={rmse:.2f}° ({n_ext} extrema) shift={shift:+.2f}s")
            trial_traces.append(dict(tag=tag, label=label, t=m_t+shift,
                                     flex=m_flex, rmse=rmse, n_ext=n_ext))
            rows_lb.append(dict(pid=pid, pos=pos, trial=trial,
                                family=family, complexity=complexity,
                                threshold=thresh, variant=label,
                                rmse_deg=rmse, n_extrema=n_ext, shift_sec=shift))

        _plot_trial_overlay(pid, pos, trial, o_t_rel, o_flex, trial_traces)

    lb = pd.DataFrame(rows_lb)
    if not lb.empty:
        lb.to_csv(os.path.join(OUT_DIR, "new_model_leaderboard.csv"), index=False)
        _plot_rmse_bar(lb)
        _plot_rmse_heatmap(lb)
    return lb


def _plot_trial_overlay(pid, pos, trial, o_t, o_flex, traces):
    if not traces:
        return
    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor("#111122")
    ax.set_facecolor("#1a1a2e")

    ax.plot(o_t, o_flex, color="white", lw=2.5, label="OptiTrack (gold)", zorder=5)
    ax.axvline(0, color="#ff4444", lw=1.5, ls="--", alpha=0.6)

    for i, tr in enumerate(traces):
        rmse_str = f" (RMSE={tr['rmse']:.1f}°)" if not math.isnan(tr["rmse"]) else ""
        ax.plot(tr["t"], tr["flex"], color=PALETTE[i % len(PALETTE)],
                lw=1.4, alpha=0.85, label=f"{tr['label']}{rmse_str}")

    ax.set_xlabel("Time from release (s)", color="#cccccc")
    ax.set_ylabel("Flexion deviation (deg)", color="#cccccc")
    ax.set_title(f"Participant {pid} | Pos {pos} | Trial {trial} — New Models vs OptiTrack",
                 color="white", fontsize=13)
    ax.tick_params(colors="#aaaaaa")
    for spine in ax.spines.values(): spine.set_edgecolor("#444466")
    ax.legend(fontsize=9, facecolor="#0f0f1a", labelcolor="white",
              edgecolor="#444466", ncol=2)
    ax.set_xlim(-0.5, min(14, float(o_t[-1]) + 1))

    out = os.path.join(OUT_DIR, f"trial_P{pid}_Pos{pos}_T{trial}_overlay.png")
    plt.tight_layout()
    plt.savefig(out, dpi=140, facecolor=fig.get_facecolor())
    plt.close()
    print(f"    -> {out}")


def _plot_rmse_bar(lb: pd.DataFrame):
    # Aggregate: mean RMSE per variant across all trials
    agg = (lb.groupby(["variant", "family"])["rmse_deg"]
             .agg(["mean", "std", "count"])
             .reset_index()
             .sort_values("mean"))
    agg.columns = ["variant", "family", "mean_rmse", "std_rmse", "n"]
    agg = agg.dropna(subset=["mean_rmse"])

    fam_colors = {"openpose": "#E65100", "vitpose": "#1976D2", "rtmo": "#2E7D32"}
    colors = [fam_colors.get(r["family"], "#888888") for _, r in agg.iterrows()]

    fig, ax = plt.subplots(figsize=(10, 5))
    fig.patch.set_facecolor("#111122")
    ax.set_facecolor("#1a1a2e")

    bars = ax.bar(agg["variant"], agg["mean_rmse"], color=colors, alpha=0.85,
                  edgecolor="white", linewidth=0.5)
    ax.errorbar(agg["variant"], agg["mean_rmse"], yerr=agg["std_rmse"],
                fmt="none", ecolor="white", elinewidth=1.5, capsize=4)

    for bar, (_, row) in zip(bars, agg.iterrows()):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f"{row['mean_rmse']:.1f}°", ha="center", va="bottom",
                color="white", fontsize=9, fontweight="bold")

    # Legend patches
    import matplotlib.patches as mpatches
    legend_handles = [mpatches.Patch(color=c, label=f.capitalize())
                      for f, c in fam_colors.items() if f in lb["family"].values]
    ax.legend(handles=legend_handles, facecolor="#0f0f1a", labelcolor="white",
              edgecolor="#444466")

    ax.set_ylabel("Mean RMSE (deg)", color="#cccccc")
    ax.set_title("New Models — Knee Angle RMSE vs OptiTrack", color="white", fontsize=13)
    ax.tick_params(axis="x", rotation=25, colors="#aaaaaa")
    ax.tick_params(axis="y", colors="#aaaaaa")
    for spine in ax.spines.values(): spine.set_edgecolor("#444466")

    out = os.path.join(OUT_DIR, "rmse_bar.png")
    plt.tight_layout()
    plt.savefig(out, dpi=140, facecolor=fig.get_facecolor())
    plt.close()
    print(f"\n  RMSE bar chart -> {out}")


def _plot_rmse_heatmap(lb: pd.DataFrame):
    if lb.empty:
        return

    lb_valid = lb.dropna(subset=["rmse_deg"])
    if lb_valid.empty:
        return

    lb_valid = lb_valid.copy()
    lb_valid["trial_key"] = ("P" + lb_valid["pid"] +
                              "/Pos" + lb_valid["pos"] +
                              "/T" + lb_valid["trial"])
    pivot = lb_valid.pivot_table(index="variant", columns="trial_key",
                                  values="rmse_deg", aggfunc="mean")

    fig, ax = plt.subplots(figsize=(max(6, len(pivot.columns)*1.6),
                                     max(4, len(pivot.index)*0.7)))
    fig.patch.set_facecolor("#111122")
    ax.set_facecolor("#1a1a2e")

    im = ax.imshow(pivot.values, aspect="auto", cmap="RdYlGn_r",
                   vmin=0, vmax=max(20, float(lb_valid["rmse_deg"].quantile(0.9))))
    cbar = plt.colorbar(im, ax=ax)
    cbar.set_label("RMSE (deg)", color="white")
    cbar.ax.yaxis.set_tick_params(color="white")
    plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="white")

    ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns, color="#aaaaaa", fontsize=9)
    ax.set_yticks(range(len(pivot.index)));   ax.set_yticklabels(pivot.index,   color="#aaaaaa", fontsize=9)
    ax.tick_params(axis="x", rotation=30)

    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            val = pivot.values[i, j]
            if not math.isnan(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center",
                        color="white" if val > 15 else "black", fontsize=8)

    ax.set_title("RMSE Heatmap — New Models × Trial", color="white", fontsize=12)
    for spine in ax.spines.values(): spine.set_edgecolor("#444466")

    out = os.path.join(OUT_DIR, "rmse_heatmap.png")
    plt.tight_layout()
    plt.savefig(out, dpi=140, facecolor=fig.get_facecolor())
    plt.close()
    print(f"  RMSE heat-map  -> {out}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    videos = discover_videos()
    print(f"Discovered {len(videos)} trial video(s):")
    for v in videos:
        print(f"  P{v['pid']} Pos{v['pos']} T{v['trial']} — {os.path.basename(v['video_path'])}")

    phase_inference(videos)
    phase_annotate_new(videos)
    lb = phase_evaluate(videos)

    if not lb.empty:
        print(f"\n{'='*65}")
        print("SUMMARY — Mean RMSE per model (across all trials):")
        print(f"{'='*65}")
        summary = (lb.groupby("variant")["rmse_deg"]
                     .agg(["mean","std","count"])
                     .sort_values("mean"))
        summary.columns = ["mean_RMSE(°)", "std(°)", "n_trials"]
        print(summary.to_string())

    print(f"\nOutputs saved to: {OUT_DIR}")
    print(f"  Annotated videos : alongside each model CSV (suffix _annotated.mp4)")
    print(f"  Comparison plots : {OUT_DIR}")
    print("Done.")


if __name__ == "__main__":
    main()
