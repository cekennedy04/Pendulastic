"""
run_pose2sim_all.py
====================
Runs Pose2Sim's rtmlib-based 2D pose estimator on every participant/position/trial
video discovered in the project tree. This is Pose2Sim's single-camera 2D pathway:
RTMPose via rtmlib (HALPE_26 skeleton) in three modes: lightweight, balanced, performance.

HALPE_26 keypoint indices (body keypoints are COCO-compatible):
  Left:  hip=11, knee=13, ankle=15
  Right: hip=12, knee=14, ankle=16

Output CSVs are named:
  P_{pid}_Pos_{pos}_H_{height}_T_{trial}_pose2sim_{mode}_0.3.csv
  family=pose2sim, complexity={mode}, threshold=0.3

Run from the Pendulastic directory:
    .venv\\Scripts\\python.exe run_pose2sim_all.py
"""

import os
import re
import math
import csv
import glob

import cv2
import numpy as np
from rtmlib import BodyWithFeet, PoseTracker

BASE_DIR = r"C:\Users\cladi\Pendulastic"

VIDEO_ROOTS = [
    os.path.join(BASE_DIR, "OptiTrack_Recordings"),
    os.path.join(BASE_DIR, "Recordings"),
]

_VID_RE    = re.compile(r"^trial_(\d+)\.(mp4|avi)$", re.IGNORECASE)
_PID_RE    = re.compile(r"Participant_(\w+)", re.IGNORECASE)
_POS_RE    = re.compile(r"Position_(\w+)",   re.IGNORECASE)
_HEIGHT_RE = re.compile(r"Height_(.+)",      re.IGNORECASE)

# HALPE_26 body keypoint indices (same as COCO for 0-16)
L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

SCORE_THRESH = 0.3

# Modes to evaluate (lightweight=fastest, performance=most accurate)
MODES = ["lightweight", "balanced", "performance"]


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_videos():
    found = []
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
                parts = path.replace("\\", "/").split("/")
                pid = pos = height = None
                for p in parts:
                    pm = _PID_RE.match(p)
                    if pm: pid = pm.group(1)
                    pm = _POS_RE.match(p)
                    if pm: pos = pm.group(1)
                    pm = _HEIGHT_RE.match(p)
                    if pm: height = pm.group(1)
                if pid and pos and height:
                    found.append((pid, pos, trial, height, path, os.path.dirname(path)))
    return found


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _angle_deg(a, b, c):
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1.0, 1.0)))


def _pick_leg(kp26, scores26):
    """
    kp26: (26, 2) pixel (x, y)
    scores26: (26,) confidence
    Pick the side (left/right) with the highest combined hip+knee+ankle score.
    """
    l_conf = scores26[L_HIP] + scores26[L_KNEE] + scores26[L_ANKLE]
    r_conf = scores26[R_HIP] + scores26[R_KNEE] + scores26[R_ANKLE]
    if l_conf >= r_conf:
        return ("left",
                kp26[L_HIP], scores26[L_HIP],
                kp26[L_KNEE], scores26[L_KNEE],
                kp26[L_ANKLE], scores26[L_ANKLE])
    else:
        return ("right",
                kp26[R_HIP], scores26[R_HIP],
                kp26[R_KNEE], scores26[R_KNEE],
                kp26[R_ANKLE], scores26[R_ANKLE])


def _nan_row(frame_idx, t_sec):
    return {"frame": frame_idx, "time_sec": round(t_sec, 6), "leg": "none",
            "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
            "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
            "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
            "knee_angle_deg": float("nan")}


# ---------------------------------------------------------------------------
# Per-video processing
# ---------------------------------------------------------------------------

def process_video(pid, pos, trial, height, video_path, out_dir, mode, tracker):
    out_name = (f"P_{pid}_Pos_{pos}_H_{height}_T_{trial}_pose2sim_"
                f"{mode}_{SCORE_THRESH}.csv")
    out_path = os.path.join(out_dir, out_name)
    if os.path.isfile(out_path):
        print(f"  [skip] {out_name}")
        return

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"  [P{pid} Pos{pos} T{trial} {mode}] {total} frames @ {fps:.1f} fps -> {out_name}")

    rows = []; frame_idx = 0
    while True:
        ok, frame = cap.read()
        if not ok: break
        t_sec = frame_idx / fps

        keypoints, scores = tracker(frame)  # (N, 26, 2), (N, 26)

        if keypoints is None or len(keypoints) == 0:
            rows.append(_nan_row(frame_idx, t_sec))
        else:
            # Pick person with highest mean score
            mean_scores = scores.mean(axis=1)
            best = int(np.argmax(mean_scores))
            kp26 = keypoints[best]    # (26, 2)
            sc26 = scores[best]       # (26,)

            leg, hip_xy, h_s, knee_xy, k_s, ank_xy, a_s = _pick_leg(kp26, sc26)
            if h_s >= SCORE_THRESH and k_s >= SCORE_THRESH and a_s >= SCORE_THRESH:
                ang = _angle_deg(hip_xy, knee_xy, ank_xy)
            else:
                ang = float("nan")

            rows.append({
                "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
                "hip_x":   round(float(hip_xy[0]),  2), "hip_y":   round(float(hip_xy[1]),  2),
                "hip_score":   round(float(h_s), 4),
                "knee_x":  round(float(knee_xy[0]), 2), "knee_y":  round(float(knee_xy[1]), 2),
                "knee_score":  round(float(k_s), 4),
                "ankle_x": round(float(ank_xy[0]),  2), "ankle_y": round(float(ank_xy[1]),  2),
                "ankle_score": round(float(a_s), 4),
                "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
            })
        frame_idx += 1
        if frame_idx % 300 == 0:
            print(f"    frame {frame_idx}/{total}", flush=True)

    cap.release()

    fieldnames = ["frame","time_sec","leg",
                  "hip_x","hip_y","hip_score",
                  "knee_x","knee_y","knee_score",
                  "ankle_x","ankle_y","ankle_score",
                  "knee_angle_deg"]
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader(); w.writerows(rows)

    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"    Done: {len(rows)} frames, {valid} valid -> {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import onnxruntime as ort
    import torch
    if torch.cuda.is_available() and "CUDAExecutionProvider" in ort.get_available_providers():
        backend, device = "onnxruntime", "cuda"
    else:
        backend, device = "onnxruntime", "cpu"
    print(f"Backend: {backend} | Device: {device}")

    videos = discover_videos()
    print(f"Discovered {len(videos)} raw video file(s).\n")

    for mode in MODES:
        print(f"\n{'='*60}")
        print(f"  Initializing PoseTracker — mode={mode} ...")
        tracker = PoseTracker(
            BodyWithFeet,
            det_frequency=1,
            mode=mode,
            backend=backend,
            device=device,
            tracking=False,
            to_openpose=False,
        )
        for pid, pos, trial, height, video_path, out_dir in videos:
            process_video(pid, pos, trial, height, video_path, out_dir, mode, tracker)

    print("\nAll Pose2Sim modes complete.")


if __name__ == "__main__":
    main()
