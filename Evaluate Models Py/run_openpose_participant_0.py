"""
run_openpose_participant_0.py
=============================
Runs OpenPose (BODY_25) on Participant 0 trial videos and injects the resulting
knee-angle trajectories into the existing evaluation_results.json.

SPEED NOTE: OpenPose on CPU takes ~23s/frame at full resolution.
This script uses --frame_step 10 (sample every 10th frame → ~3fps effective)
to keep runtime to ~90 minutes for all 4 videos while still resolving the
0.5–1 Hz pendulum oscillations cleanly (Nyquist satisfied at 3fps).

To run at full 30fps (15+ hours), set FRAME_STEP = 1.
To run at 1fps (~30 min), set FRAME_STEP = 30.

Run from the Pendulastic directory:
    .venv\Scripts\python.exe run_openpose_participant_0.py
"""

import os
import sys
import json
import glob
import tempfile
import shutil
import subprocess
import math

import numpy as np

BASE_DIR        = r"C:\Users\cladi\Pendulastic"
EVALUATION_JSON = r"C:\Users\cladi\OneDrive\Desktop\Participant_0\Results\evaluation_results.json"
OPTITRACK_ROOT  = os.path.join(BASE_DIR, "OptiTrack_Recordings", "Participant_0")
OPENPOSE_BIN    = os.path.join(BASE_DIR, "models", "openpose", "bin", "OpenPoseDemo.exe")
OPENPOSE_MODELS = os.path.join(BASE_DIR, "models", "openpose", "models")

# BODY_25 indices: 9=RHip 10=RKnee 11=RAnkle 12=LHip 13=LKnee 14=LAnkle
BODY25_RHIP, BODY25_RKNEE, BODY25_RANKLE = 9, 10, 11
BODY25_LHIP, BODY25_LKNEE, BODY25_LANKLE = 12, 13, 14

SCORE_THRESH = 0.10
NET_RES      = "-1x176"   # reduced height for faster CPU inference
FRAME_STEP   = 10         # process every Nth frame; effective fps = 30 / FRAME_STEP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _angle_deg(a, b, c):
    """Angle at vertex b formed by vectors ba and bc (degrees)."""
    ba = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    bc = np.asarray(c, dtype=float) - np.asarray(b, dtype=float)
    n1, n2 = np.linalg.norm(ba), np.linalg.norm(bc)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    cos_a = np.clip(np.dot(ba, bc) / (n1 * n2), -1.0, 1.0)
    return math.degrees(math.acos(cos_a))


def _parse_frame(json_path):
    """Parse one OpenPose BODY_25 JSON frame → (N,3) array or None."""
    try:
        with open(json_path, "r") as fh:
            d = json.load(fh)
        people = d.get("people", [])
        if not people:
            return None
        best = max(people, key=lambda p: sum(
            p["pose_keypoints_2d"][2::3]) / max(1, len(p["pose_keypoints_2d"]) // 3))
        kp_flat = best["pose_keypoints_2d"]
        n = len(kp_flat) // 3
        return np.array(kp_flat, dtype=float).reshape(n, 3)
    except Exception:
        return None


def _knee_angle_from_kp(kp):
    """Compute knee angle from BODY_25 keypoints; picks best side by confidence."""
    def side_angle(hip_i, knee_i, ankle_i):
        h, k, a = kp[hip_i], kp[knee_i], kp[ankle_i]
        if h[2] < SCORE_THRESH or k[2] < SCORE_THRESH or a[2] < SCORE_THRESH:
            return float("nan"), 0.0
        return _angle_deg(h[:2], k[:2], a[:2]), float(min(h[2], k[2], a[2]))

    r_ang, r_conf = side_angle(BODY25_RHIP, BODY25_RKNEE, BODY25_RANKLE)
    l_ang, l_conf = side_angle(BODY25_LHIP, BODY25_LKNEE, BODY25_LANKLE)

    if math.isnan(r_ang) and math.isnan(l_ang):
        return float("nan")
    if math.isnan(r_ang):
        return l_ang
    if math.isnan(l_ang):
        return r_ang
    return r_ang if r_conf >= l_conf else l_ang


def _video_fps(video_path):
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        return float(fps) if fps > 0 else 30.0
    except Exception:
        return 30.0


def run_openpose_on_video(video_path):
    """
    Run OpenPose BODY_25 via subprocess with frame-stepping.
    Returns (timestamps_arr, knee_angles_arr) at effective fps = 30 / FRAME_STEP.
    """
    if not os.path.isfile(OPENPOSE_BIN):
        raise FileNotFoundError(f"OpenPoseDemo.exe not found: {OPENPOSE_BIN}")

    tmp_dir = tempfile.mkdtemp(prefix="op_json_",
                               dir=os.path.dirname(video_path))
    try:
        cmd = [
            OPENPOSE_BIN,
            "--video",          os.path.abspath(video_path),
            "--write_json",     tmp_dir,
            "--model_pose",     "BODY_25",
            "--model_folder",   OPENPOSE_MODELS,
            "--net_resolution", NET_RES,
            "--frame_step",     str(FRAME_STEP),
            "--display",        "0",
            "--render_pose",    "0",
        ]
        effective_fps = 30.0 / FRAME_STEP
        print(f"  OpenPoseDemo.exe BODY_25 | net_res={NET_RES} | "
              f"frame_step={FRAME_STEP} (effective {effective_fps:.1f} fps)")
        print(f"  Estimated time: ~{(600 / FRAME_STEP) * 23 / 60:.0f} min for this video")

        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=7200,   # 2-hour timeout
            cwd=os.path.dirname(OPENPOSE_BIN),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"OpenPoseDemo.exe exited {result.returncode}:\n"
                f"{(result.stderr or '')[:800]}")

        json_files = sorted(
            f for f in os.listdir(tmp_dir) if f.endswith("_keypoints.json")
        )
        if not json_files:
            raise RuntimeError("OpenPose produced no keypoint JSON files.")

        source_fps = _video_fps(video_path)
        effective_ts_step = FRAME_STEP / source_fps

        timestamps, angles = [], []
        for i, jf in enumerate(json_files):
            kp = _parse_frame(os.path.join(tmp_dir, jf))
            ang = _knee_angle_from_kp(kp) if kp is not None else float("nan")
            timestamps.append(i * effective_ts_step)
            angles.append(ang)

        valid = sum(1 for a in angles if not math.isnan(a))
        print(f"  Done: {len(angles)} samples, {valid} valid detections "
              f"(effective {effective_fps:.1f} fps)")
        return np.array(timestamps, dtype=float), np.array(angles, dtype=float)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
print(f"OpenPose injection script — FRAME_STEP={FRAME_STEP} "
      f"(effective {30.0/FRAME_STEP:.1f} fps)\n")

with open(EVALUATION_JSON, "r", encoding="utf-8-sig") as f:
    data = json.load(f)

trials = data.get("trials", [])
print(f"Found {len(trials)} trials in evaluation JSON.\n")

updated = 0

for trial_data in trials:
    if "0" not in str(trial_data.get("participant_id", "")):
        continue

    pos   = str(trial_data.get("position", "")).replace("Position_", "").strip()
    t_num = str(trial_data.get("trial", "")).strip()

    # Skip if openpose already processed
    if "openpose" in trial_data.get("models", {}):
        traj = (trial_data["models"]["openpose"]
                .get("trajectories", {}).get("knee_angle", []))
        if traj:
            print(f"  [skip] Pos={pos} Trial={t_num} — openpose already present "
                  f"({len(traj)} samples)")
            continue

    # Find video
    patterns = [
        os.path.join(OPTITRACK_ROOT, f"Position_{pos}", "**", f"trial_{t_num}.mp4"),
        os.path.join(OPTITRACK_ROOT, f"Position_{pos}", "**", f"Trial_{t_num}.mp4"),
        os.path.join(OPTITRACK_ROOT, f"Position_{pos}", "**", f"trial_{t_num}.avi"),
        os.path.join(OPTITRACK_ROOT, f"Position_{pos}", "**", f"Trial_{t_num}.avi"),
    ]
    video_path = None
    for pat in patterns:
        hits = glob.glob(pat, recursive=True)
        if hits:
            video_path = hits[0]
            break

    if video_path is None:
        print(f"  [warn] Pos={pos} Trial={t_num} — video not found.")
        continue

    print(f"[Pos={pos} Trial={t_num}] {os.path.basename(video_path)}")

    try:
        timestamps, knee_angles = run_openpose_on_video(video_path)
    except Exception as exc:
        print(f"  [error] {exc}\n")
        continue

    if "models" not in trial_data:
        trial_data["models"] = {}
    trial_data["models"]["openpose"] = {
        "status": "ok",
        "frame_step": FRAME_STEP,
        "effective_fps": 30.0 / FRAME_STEP,
        "trajectories": {
            "knee_angle": [
                (float(v) if not math.isnan(v) else None)
                for v in knee_angles
            ],
        },
        "timestamps": [float(t) for t in timestamps],
    }
    updated += 1
    print()

if updated > 0:
    with open(EVALUATION_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {updated} new OpenPose result(s) → {EVALUATION_JSON}")
    print("\nNow re-run evaluate_models_participant_1.py to see OpenPose on the plots.")
else:
    print("No new OpenPose results to save (already present or no videos found).")

print("Done.")
