"""
compare_2d_vs_world_angle.py
=============================
Throwaway diagnostic: is Track 1's ~28 degree knee-angle RMSE dominated by
out-of-plane projection error?

sweep_mediapipe_config.extract_raw_landmarks only ever stores
result.pose_landmarks (normalized [0,1] x,y) -- it never touches
result.pose_world_landmarks (MediaPipe's free real-world x,y,z-in-meters
output), and the landmark cache in rmse_pipeline_common.py is keyed to that
2D-only shape. So the existing pipeline cannot answer "would a 3D angle
track OptiTrack better than the current 2D pixel-space angle" without a
fresh inference pass that also captures pose_world_landmarks.

This script runs a SMALL sample of P14 trials (not the full sweep -- the
in-progress sweep_mediapipe_preprocessing.py background job owns the full
dataset) through MediaPipe once each, computing BOTH:
  - the current 2D angle (same normalized-xy formula as
    sweep_mediapipe_config.angles_from_raw, for an apples-to-apples
    comparison with existing sweep numbers)
  - a 3D angle from pose_world_landmarks (hip/knee/ankle real-world
    x,y,z), using the same hip->knee / ankle->knee vector-angle formula
and scores each against OptiTrack via the same workbench_engine.compare_pair
machinery the production pipeline uses.

Run:
    .venv\\Scripts\\python.exe compare_2d_vs_world_angle.py
"""
from __future__ import annotations

import os

import cv2
import mediapipe as mp
import numpy as np

import batch_mediapipe as bm
import sweep_mediapipe_config as smc
import workbench_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_VARIANT = "heavy"   # best-performing variant per the existing P14 sweep
VIS_THRESH = 0.30         # best-performing threshold per the existing P14 sweep
N_TRIALS = 4              # small sample -- keep runtime short, don't compete
                           # with the in-progress full preprocessing sweep


def _select_patient_pose_index(poses):
    """Same trunk-horizontality scoring as batch_mediapipe._select_patient_pose,
    but returns the winning index so the identical person can be picked out
    of pose_world_landmarks too (world landmarks don't carry their own
    trunk-orientation signal worth re-deriving -- reuse the 2D decision)."""
    if len(poses) <= 1:
        return 0 if poses else None
    best_idx, best_score = None, -1.0
    for i, pose in enumerate(poses):
        l_sh, r_sh = pose[bm._SHOULDER_IDX[0]], pose[bm._SHOULDER_IDX[1]]
        l_hp, r_hp = pose[bm._HIP_IDX[0]], pose[bm._HIP_IDX[1]]
        dx = (l_sh.x + r_sh.x) / 2.0 - (l_hp.x + r_hp.x) / 2.0
        dy = (l_sh.y + r_sh.y) / 2.0 - (l_hp.y + r_hp.y) / 2.0
        mag = (dx * dx + dy * dy) ** 0.5
        if mag < 1e-6:
            continue
        h_score = abs(dx) / mag
        if h_score > best_score:
            best_score, best_idx = h_score, i
    return best_idx if best_idx is not None else 0


def _angle(v1, v2):
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return float("nan")
    cos_a = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cos_a)))


def extract_2d_and_world(video_path, leg, model_path):
    """One inference pass per trial, capturing both landmark spaces so the
    comparison is on identical detections -- not a second, possibly
    different, inference run."""
    h_idx, k_idx, a_idx = bm.MP_LEG_IDX[leg]

    BaseOptions = mp.tasks.BaseOptions
    PoseLandmarker = mp.tasks.vision.PoseLandmarker
    PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
    VisionRunningMode = mp.tasks.vision.RunningMode
    opts = PoseLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionRunningMode.IMAGE,
        num_poses=2,
    )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [], []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    t_list = []
    ang2d_list = []
    ang3d_list = []
    with PoseLandmarker.create_from_options(opts) as landmarker:
        frame_idx = 0
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            t_sec = frame_idx / fps
            t_list.append(t_sec)
            ang2d = float("nan")
            ang3d = float("nan")
            try:
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_image)
                if result.pose_landmarks and result.pose_world_landmarks:
                    idx = _select_patient_pose_index(result.pose_landmarks)
                    if idx is not None:
                        lms2d = result.pose_landmarks[idx]
                        lms3d = result.pose_world_landmarks[idx]
                        hl, kl, al = lms2d[h_idx], lms2d[k_idx], lms2d[a_idx]
                        if (hl.visibility > VIS_THRESH and kl.visibility > VIS_THRESH
                                and al.visibility > VIS_THRESH):
                            hip2, kne2, ank2 = (np.array([hl.x, hl.y]),
                                                 np.array([kl.x, kl.y]),
                                                 np.array([al.x, al.y]))
                            ang2d = _angle(hip2 - kne2, ank2 - kne2)

                            hw, kw, aw = lms3d[h_idx], lms3d[k_idx], lms3d[a_idx]
                            hip3, kne3, ank3 = (np.array([hw.x, hw.y, hw.z]),
                                                 np.array([kw.x, kw.y, kw.z]),
                                                 np.array([aw.x, aw.y, aw.z]))
                            ang3d = _angle(hip3 - kne3, ank3 - kne3)
            except Exception:
                pass
            ang2d_list.append(ang2d)
            ang3d_list.append(ang3d)
            frame_idx += 1
    cap.release()
    return np.array(t_list), np.array(ang2d_list), np.array(ang3d_list)


def score(t_m, ang_m, opti_t, opti_ang):
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    return result["rmse_deg"] if result.get("status") == "ok" else None


def main():
    trials = smc.discover_p14_trials()[:N_TRIALS]
    print(f"Running {len(trials)} P14 trial(s), model={MODEL_VARIANT}, "
          f"vis_thresh={VIS_THRESH}\n")
    model_path = os.path.join(BASE_DIR, "models", "mediapipe",
                               f"pose_landmarker_{MODEL_VARIANT}.task")

    rows = []
    for trial in trials:
        t_m, ang2d, ang3d = extract_2d_and_world(trial["video"], trial["leg"], model_path)
        rmse2d = score(t_m, ang2d, trial["opti_t"], trial["opti_ang"])
        rmse3d = score(t_m, ang3d, trial["opti_t"], trial["opti_ang"])
        rows.append((trial["leg"], trial["trial"], rmse2d, rmse3d))
        r2d = f"{rmse2d:.2f}" if rmse2d is not None else "n/a"
        r3d = f"{rmse3d:.2f}" if rmse3d is not None else "n/a"
        better = ("n/a" if (rmse2d is None or rmse3d is None) else
                   ("3D-world" if rmse3d < rmse2d else "2D-pixel"))
        print(f"  leg={trial['leg']:<5} trial={trial['trial']:<3} "
              f"2D_RMSE={r2d:>6} deg   3D_world_RMSE={r3d:>6} deg   better={better}")

    valid = [(r2, r3) for _, _, r2, r3 in rows if r2 is not None and r3 is not None]
    if valid:
        mean2d = float(np.mean([r2 for r2, _ in valid]))
        mean3d = float(np.mean([r3 for _, r3 in valid]))
        print(f"\nAggregate over {len(valid)} scored trial(s): "
              f"mean 2D RMSE={mean2d:.2f} deg, mean 3D-world RMSE={mean3d:.2f} deg")
    else:
        print("\nNo trial produced a valid RMSE for both angle spaces.")


if __name__ == "__main__":
    main()
