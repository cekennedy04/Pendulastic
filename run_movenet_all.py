"""
run_movenet_all.py
==================
Runs MoveNet (Lightning + Thunder) on every participant/position/trial video
discovered in the project tree. Skips already-generated CSVs.

MoveNet output: [1, 1, 17, 3]  — (y_norm, x_norm, score) per keypoint.
Keypoints follow COCO-17 ordering; same hip/knee/ankle indices as all other models.

Run from the Pendulastic directory:
    .venv\\Scripts\\python.exe run_movenet_all.py
"""

import os
import re
import math
import csv
import glob

import cv2
import numpy as np
import tensorflow as tf
import tensorflow_hub as hub

BASE_DIR = r"C:\Users\cladi\Pendulastic"

# Scan both roots for raw videos
VIDEO_ROOTS = [
    os.path.join(BASE_DIR, "OptiTrack_Recordings"),
    os.path.join(BASE_DIR, "Recordings"),
]

# Matches raw trial videos: trial_1.mp4, Trial_2.avi, etc.
_VID_RE    = re.compile(r"^trial_(\d+)\.(mp4|avi)$", re.IGNORECASE)
# Matches participant / position in the path
_PID_RE    = re.compile(r"Participant_(\w+)", re.IGNORECASE)
_POS_RE    = re.compile(r"Position_(\w+)",   re.IGNORECASE)
_HEIGHT_RE = re.compile(r"Height_(.+)",      re.IGNORECASE)

# COCO keypoint indices
L_HIP, L_KNEE, L_ANKLE = 11, 13, 15
R_HIP, R_KNEE, R_ANKLE = 12, 14, 16

# Score threshold for accepting a keypoint
SCORE_THRESH = 0.3

VARIANTS = [
    ("lightning", "https://tfhub.dev/google/movenet/singlepose/lightning/4", 192),
    ("thunder",   "https://tfhub.dev/google/movenet/singlepose/thunder/4",   256),
]


# ---------------------------------------------------------------------------
# Video discovery
# ---------------------------------------------------------------------------

def discover_videos():
    """Return list of (pid, pos, trial, height_label, video_path, out_dir)."""
    found = []
    for root in VIDEO_ROOTS:
        if not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.mp4"), recursive=True) + \
                    glob.glob(os.path.join(root, "**", "*.avi"), recursive=True):
            bn = os.path.basename(path)
            m  = _VID_RE.match(bn)
            if not m:
                continue
            trial = m.group(1)
            parts = path.replace("\\", "/").split("/")
            pid = pos = height = None
            for p in parts:
                pm = _PID_RE.match(p)
                if pm:
                    pid = pm.group(1)
                pm = _POS_RE.match(p)
                if pm:
                    pos = pm.group(1)
                pm = _HEIGHT_RE.match(p)
                if pm:
                    height = pm.group(1)
            if pid and pos and height:
                found.append((pid, pos, trial, height, path, os.path.dirname(path)))
    return found


# ---------------------------------------------------------------------------
# Angle helper
# ---------------------------------------------------------------------------

def _angle_deg(a, b, c):
    ba = np.asarray(a, float) - np.asarray(b, float)
    bc = np.asarray(c, float) - np.asarray(b, float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    return math.degrees(math.acos(np.clip(np.dot(ba, bc) / (n1 * n2), -1.0, 1.0)))


# ---------------------------------------------------------------------------
# MoveNet inference helpers (adaptive cropping from TF Hub notebook)
# ---------------------------------------------------------------------------

MIN_CROP_KP_SCORE = 0.2

def _init_crop(h, w):
    if w > h:
        return {"y_min": (h/2 - w/2)/h, "x_min": 0.0,
                "y_max": (h/2 - w/2)/h + w/h, "x_max": 1.0,
                "height": w/h, "width": 1.0}
    else:
        return {"y_min": 0.0, "x_min": (w/2 - h/2)/w,
                "y_max": 1.0, "x_max": (w/2 - h/2)/w + h/w,
                "height": 1.0, "width": h/w}


def _torso_visible(kps):
    KD = {"left_hip": 11, "right_hip": 12, "left_shoulder": 5, "right_shoulder": 6}
    return ((kps[0, 0, KD["left_hip"],  2] > MIN_CROP_KP_SCORE or
             kps[0, 0, KD["right_hip"], 2] > MIN_CROP_KP_SCORE) and
            (kps[0, 0, KD["left_shoulder"],  2] > MIN_CROP_KP_SCORE or
             kps[0, 0, KD["right_shoulder"], 2] > MIN_CROP_KP_SCORE))


def _next_crop(kps, h, w):
    if not _torso_visible(kps):
        return _init_crop(h, w)
    ky = kps[0, 0, :, 0] * h
    kx = kps[0, 0, :, 1] * w
    ks = kps[0, 0, :, 2]
    torso_idx = [5, 6, 11, 12]
    cy = (ky[11] + ky[12]) / 2
    cx = (kx[11] + kx[12]) / 2
    max_ty = max(abs(cy - ky[i]) for i in torso_idx)
    max_tx = max(abs(cx - kx[i]) for i in torso_idx)
    body_mask = ks > MIN_CROP_KP_SCORE
    max_by = max((abs(cy - ky[i]) for i in range(17) if body_mask[i]), default=max_ty)
    max_bx = max((abs(cx - kx[i]) for i in range(17) if body_mask[i]), default=max_tx)
    half = np.amin([np.amax([max_ty*1.9, max_tx*1.9, max_by*1.2, max_bx*1.2]),
                    np.amax([cx, w-cx, cy, h-cy])])
    if half > max(h, w) / 2:
        return _init_crop(h, w)
    length = half * 2
    return {"y_min": (cy-half)/h, "x_min": (cx-half)/w,
            "y_max": (cy-half+length)/h, "x_max": (cx-half+length)/w,
            "height": length/h, "width": length/w}


def _run_inference(movenet_fn, img_np, crop, input_size):
    h, w = img_np.shape[:2]
    boxes = [[crop["y_min"], crop["x_min"], crop["y_max"], crop["x_max"]]]
    img_t  = tf.expand_dims(tf.constant(img_np, dtype=tf.int32), 0)
    cropped = tf.image.crop_and_resize(img_t, box_indices=[0], boxes=boxes,
                                        crop_size=[input_size, input_size])
    kps = movenet_fn(cropped)
    # un-normalize back to full-image coords
    for i in range(17):
        kps[0, 0, i, 0] = (crop["y_min"] * h + crop["height"] * h * kps[0, 0, i, 0]) / h
        kps[0, 0, i, 1] = (crop["x_min"] * w + crop["width"]  * w * kps[0, 0, i, 1]) / w
    return kps


def _pick_leg_movenet(kps17_norm, h, w):
    """kps17_norm: (17, 3) normalized (y, x, score). Returns (leg, hip_xy, knee_xy, ankle_xy, score_tuple)."""
    def px(idx):
        y_n, x_n, s = kps17_norm[idx]
        return np.array([x_n * w, y_n * h]), float(s)

    l_hip_xy, l_h_s  = px(L_HIP)
    l_knee_xy, l_k_s = px(L_KNEE)
    l_ank_xy, l_a_s  = px(L_ANKLE)
    r_hip_xy, r_h_s  = px(R_HIP)
    r_knee_xy, r_k_s = px(R_KNEE)
    r_ank_xy, r_a_s  = px(R_ANKLE)

    l_conf = l_h_s + l_k_s + l_a_s
    r_conf = r_h_s + r_k_s + r_a_s
    if l_conf >= r_conf:
        return "left", l_hip_xy, l_knee_xy, l_ank_xy, (l_h_s, l_k_s, l_a_s)
    else:
        return "right", r_hip_xy, r_knee_xy, r_ank_xy, (r_h_s, r_k_s, r_a_s)


def _nan_row(frame_idx, t_sec):
    return {"frame": frame_idx, "time_sec": round(t_sec, 6), "leg": "none",
            "hip_x": float("nan"),   "hip_y": float("nan"),   "hip_score": 0.0,
            "knee_x": float("nan"),  "knee_y": float("nan"),  "knee_score": 0.0,
            "ankle_x": float("nan"), "ankle_y": float("nan"), "ankle_score": 0.0,
            "knee_angle_deg": float("nan")}


# ---------------------------------------------------------------------------
# Per-video processing
# ---------------------------------------------------------------------------

def process_video(pid, pos, trial, height, video_path, out_dir,
                  variant_name, movenet_fn, input_size):
    out_name = (f"P_{pid}_Pos_{pos}_H_{height}_T_{trial}_movenet_"
                f"{variant_name}_{SCORE_THRESH}.csv")
    out_path = os.path.join(out_dir, out_name)
    if os.path.isfile(out_path):
        print(f"  [skip] {out_name} already exists")
        return

    cap   = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    print(f"  [P{pid} Pos{pos} T{trial} {variant_name}] {total} frames @ {fps:.1f} fps -> {out_name}")

    if h_vid == 0 or w_vid == 0:
        cap.release()
        print(f"    Skipped: could not read video dimensions (corrupt or empty).")
        return

    rows      = []
    frame_idx = 0
    crop      = _init_crop(h_vid, w_vid)

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t_sec  = frame_idx / fps
        img_np = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        kps = _run_inference(movenet_fn, img_np, crop, input_size)
        crop = _next_crop(kps, h_vid, w_vid)

        kp17 = kps[0, 0]  # (17, 3) normalized (y, x, score)
        leg, hip_xy, knee_xy, ank_xy, (h_s, k_s, a_s) = _pick_leg_movenet(kp17, h_vid, w_vid)

        if h_s >= SCORE_THRESH and k_s >= SCORE_THRESH and a_s >= SCORE_THRESH:
            ang = _angle_deg(hip_xy, knee_xy, ank_xy)
        else:
            ang = float("nan")

        rows.append({
            "frame": frame_idx, "time_sec": round(t_sec, 6), "leg": leg,
            "hip_x":   round(float(hip_xy[0]),  2), "hip_y":   round(float(hip_xy[1]),  2),
            "hip_score":   round(h_s, 4),
            "knee_x":  round(float(knee_xy[0]), 2), "knee_y":  round(float(knee_xy[1]), 2),
            "knee_score":  round(k_s, 4),
            "ankle_x": round(float(ank_xy[0]),  2), "ankle_y": round(float(ank_xy[1]),  2),
            "ankle_score": round(a_s, 4),
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
    videos = discover_videos()
    print(f"Discovered {len(videos)} raw video file(s).\n")

    for variant_name, hub_url, input_size in VARIANTS:
        print(f"\n{'='*60}")
        print(f"  Loading MoveNet-{variant_name} from TF Hub ...")
        module = hub.load(hub_url)

        def movenet_fn(x, _module=module):
            inp = tf.cast(x, dtype=tf.int32)
            return _module.signatures["serving_default"](inp)["output_0"].numpy()

        for pid, pos, trial, height, video_path, out_dir in videos:
            process_video(pid, pos, trial, height, video_path, out_dir,
                          variant_name, movenet_fn, input_size)

    print("\nAll MoveNet variants complete.")


if __name__ == "__main__":
    main()
