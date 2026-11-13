"""
run_yolo_participant_0.py
==========================
Runs yolo26n-pose on Participant 0 trial videos (both positions) and writes
knee-angle CSVs into the OptiTrack_Recordings tree alongside existing model CSVs.

Videos: trial_1.mp4 / trial_2.mp4  (lowercase, .mp4)
Output: P_0_Pos_{pos}_H_Joint-Level_T_{trial}_yolo_n_0.5.csv

Run from the Pendulastic directory:
    .venv\\Scripts\\python.exe run_yolo_participant_0.py
"""

import os
import math
import csv

import cv2
import numpy as np

BASE_DIR   = r"C:\Users\cladi\Pendulastic"
MODEL_PATH = os.path.join(BASE_DIR, "models", "yolo26n-pose.pt")

SCORE_THRESH = 0.5
TRIALS       = [1, 2]
POSITIONS    = [1, 2]

L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16


def _angle_deg(a, b, c):
    ba = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    bc = np.asarray(c, dtype=float) - np.asarray(b, dtype=float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1.0, 1.0)))


def _best_person(kps_batch):
    if kps_batch is None or len(kps_batch) == 0:
        return None
    scores = kps_batch[:, :, 2].mean(axis=1)
    return kps_batch[int(np.argmax(scores))]


def _pick_leg(kp17):
    l_conf = kp17[L_HIP, 2] + kp17[L_KNEE, 2] + kp17[L_ANKLE, 2]
    r_conf = kp17[R_HIP, 2] + kp17[R_KNEE, 2] + kp17[R_ANKLE, 2]
    if l_conf >= r_conf:
        return "left", kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE]
    else:
        return "right", kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE]


def _nan_row(frame_idx, t_sec):
    return {
        "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": "none",
        "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
        "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
        "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
        "knee_angle_deg": float("nan"),
    }


def process_trial(pos, trial_num, model):
    vid_dir = os.path.join(BASE_DIR, "OptiTrack_Recordings",
                           "Participant_0", f"Position_{pos}", "Height_Joint-Level")
    video_path = os.path.join(vid_dir, f"trial_{trial_num}.mp4")
    if not os.path.isfile(video_path):
        print(f"[SKIP] Not found: {video_path}")
        return

    out_name = f"P_0_Pos_{pos}_H_Joint-Level_T_{trial_num}_yolo_n_{SCORE_THRESH}.csv"
    out_path = os.path.join(vid_dir, out_name)
    if os.path.isfile(out_path):
        print(f"[SKIP] Already exists: {out_name}")
        return

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[P0 Pos{pos} Trial {trial_num}] {total} frames @ {fps:.1f} fps -> {out_name}")

    rows      = []
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t_sec   = frame_idx / fps
        results = model(frame, verbose=False, conf=SCORE_THRESH)
        res     = results[0]
        kp17    = None
        if res.keypoints is not None and res.keypoints.data is not None:
            kp_data = res.keypoints.data.cpu().numpy()
            if kp_data.shape[0] > 0:
                kp17 = _best_person(kp_data)

        if kp17 is None:
            rows.append(_nan_row(frame_idx, t_sec))
        else:
            leg, hip, knee, ankle = _pick_leg(kp17)
            h_ok = hip[2]   >= SCORE_THRESH
            k_ok = knee[2]  >= SCORE_THRESH
            a_ok = ankle[2] >= SCORE_THRESH
            ang  = _angle_deg(hip[:2], knee[:2], ankle[:2]) if (h_ok and k_ok and a_ok) else float("nan")
            rows.append({
                "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
                "hip_x":   round(float(hip[0]),   2), "hip_y":   round(float(hip[1]),   2),
                "hip_score": round(float(hip[2]),  4),
                "knee_x":  round(float(knee[0]),  2), "knee_y":  round(float(knee[1]),  2),
                "knee_score": round(float(knee[2]), 4),
                "ankle_x": round(float(ankle[0]), 2), "ankle_y": round(float(ankle[1]), 2),
                "ankle_score": round(float(ankle[2]), 4),
                "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
            })
        frame_idx += 1
        if frame_idx % 300 == 0:
            print(f"  frame {frame_idx}/{total}", flush=True)

    cap.release()

    fieldnames = ["frame","time_sec","leg",
                  "hip_x","hip_y","hip_score",
                  "knee_x","knee_y","knee_score",
                  "ankle_x","ankle_y","ankle_score",
                  "knee_angle_deg"]
    with open(out_path, "w", newline="") as fh:
        csv.DictWriter(fh, fieldnames=fieldnames).writeheader()
        csv.DictWriter(fh, fieldnames=fieldnames).writerows(rows)

    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"  Done: {len(rows)} frames, {valid} valid -> {out_path}")


def main():
    from ultralytics import YOLO
    print(f"Loading YOLO: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)

    for pos in POSITIONS:
        for trial in TRIALS:
            process_trial(pos, trial, model)

    print("\nAll done.")


if __name__ == "__main__":
    main()
