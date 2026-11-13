"""
run_yolo_participant_1.py
==========================
Runs yolo26n-pose on Participant 1 trial videos and outputs knee-angle CSVs
that match the format used by evaluate_models_participant_1_raw.py.

COCO keypoint indices (ultralytics convention):
  left:  hip=11, knee=13, ankle=15
  right: hip=12, knee=14, ankle=16

Picks the side (left/right) with the highest combined hip+knee+ankle confidence
on each frame. If both sides are below SCORE_THRESH the frame is written with NaN angles.

Output CSV columns (same as mediapipe/rtmpose/mmpose CSVs):
  frame, time_sec, leg,
  hip_x, hip_y, hip_score,
  knee_x, knee_y, knee_score,
  ankle_x, ankle_y, ankle_score,
  knee_angle_deg

Run from the Pendulastic directory:
    .venv\\Scripts\\python.exe run_yolo_participant_1.py
"""

import os
import math
import csv

import cv2
import numpy as np

BASE_DIR   = r"C:\Users\cladi\Pendulastic"
MODEL_PATH = os.path.join(BASE_DIR, "models", "yolo26n-pose.pt")
VIDEO_DIR  = os.path.join(BASE_DIR, "Recordings", "Participant_1",
                           "Position_2", "Height_Joint-Level")
OUTPUT_DIR = VIDEO_DIR

SCORE_THRESH = 0.5   # reflected in output filename
TRIALS       = [1, 2]

# COCO keypoint indices
L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _angle_deg(a, b, c):
    """Angle at vertex b (degrees)."""
    ba = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    bc = np.asarray(c, dtype=float) - np.asarray(b, dtype=float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    cos_a = np.clip(np.dot(ba, bc) / (n1 * n2), -1.0, 1.0)
    return math.degrees(math.acos(cos_a))


def _best_person(kps_batch):
    """
    From a (N_persons, 17, 3) array pick the person with highest mean keypoint
    confidence.  Returns (17, 3) array or None.
    """
    if kps_batch is None or len(kps_batch) == 0:
        return None
    scores = kps_batch[:, :, 2].mean(axis=1)
    return kps_batch[int(np.argmax(scores))]


def _pick_leg(kp17):
    """
    kp17: (17, 3) array of (x, y, conf).
    Returns (leg_label, hip, knee, ankle) where each is (x, y, conf).
    Picks whichever side has the higher total hip+knee+ankle confidence.
    """
    l_conf = kp17[L_HIP, 2] + kp17[L_KNEE, 2] + kp17[L_ANKLE, 2]
    r_conf = kp17[R_HIP, 2] + kp17[R_KNEE, 2] + kp17[R_ANKLE, 2]
    if l_conf >= r_conf:
        return "left", kp17[L_HIP], kp17[L_KNEE], kp17[L_ANKLE]
    else:
        return "right", kp17[R_HIP], kp17[R_KNEE], kp17[R_ANKLE]


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------

def process_trial(trial_num, model):
    video_path = os.path.join(VIDEO_DIR, f"Trial_{trial_num}.avi")
    if not os.path.isfile(video_path):
        print(f"[SKIP] Video not found: {video_path}")
        return

    out_name = (f"P_1_Pos_2_H_Joint-Level_T_{trial_num}_yolo_n_"
                f"{SCORE_THRESH}.csv")
    out_path = os.path.join(OUTPUT_DIR, out_name)

    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"[Trial {trial_num}] {total} frames @ {fps:.1f} fps → {out_name}")

    rows = []
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        t_sec = frame_idx / fps

        results = model(frame, verbose=False, conf=SCORE_THRESH)
        res = results[0]

        # Extract keypoints
        kp17 = None
        if res.keypoints is not None and res.keypoints.data is not None:
            kp_data = res.keypoints.data.cpu().numpy()  # (N, 17, 3)
            if kp_data.shape[0] > 0:
                kp17 = _best_person(kp_data)

        if kp17 is None:
            rows.append({
                "frame": frame_idx, "time_sec": round(t_sec, 6),
                "leg": "none",
                "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
                "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
                "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
                "knee_angle_deg": float("nan"),
            })
        else:
            leg, hip, knee, ankle = _pick_leg(kp17)
            # Only use keypoints above threshold
            h_ok = hip[2]   >= SCORE_THRESH
            k_ok = knee[2]  >= SCORE_THRESH
            a_ok = ankle[2] >= SCORE_THRESH
            if h_ok and k_ok and a_ok:
                ang = _angle_deg(hip[:2], knee[:2], ankle[:2])
            else:
                ang = float("nan")

            rows.append({
                "frame": frame_idx, "time_sec": round(t_sec, 6),
                "leg": leg,
                "hip_x":   round(float(hip[0]),   2),
                "hip_y":   round(float(hip[1]),   2),
                "hip_score": round(float(hip[2]),  4),
                "knee_x":  round(float(knee[0]),  2),
                "knee_y":  round(float(knee[1]),  2),
                "knee_score": round(float(knee[2]), 4),
                "ankle_x": round(float(ankle[0]), 2),
                "ankle_y": round(float(ankle[1]), 2),
                "ankle_score": round(float(ankle[2]), 4),
                "knee_angle_deg": round(ang, 4) if not math.isnan(ang) else float("nan"),
            })

        frame_idx += 1
        if frame_idx % 300 == 0:
            print(f"  frame {frame_idx}/{total}", flush=True)

    cap.release()

    fieldnames = [
        "frame", "time_sec", "leg",
        "hip_x", "hip_y", "hip_score",
        "knee_x", "knee_y", "knee_score",
        "ankle_x", "ankle_y", "ankle_score",
        "knee_angle_deg",
    ]
    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    valid = sum(1 for r in rows if not math.isnan(r["knee_angle_deg"]))
    print(f"  Done: {len(rows)} frames, {valid} with valid knee angle → {out_path}")


def main():
    from ultralytics import YOLO
    print(f"Loading model: {MODEL_PATH}")
    model = YOLO(MODEL_PATH)

    for trial in TRIALS:
        process_trial(trial, model)

    print("\nAll trials complete.")


if __name__ == "__main__":
    main()
