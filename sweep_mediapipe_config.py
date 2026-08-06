"""
sweep_mediapipe_config.py
==========================
Iterates MediaPipe PoseLandmarker model variant (lite/full/heavy) and
visibility threshold over Participant 14's trials that have OptiTrack
ground truth, scoring each candidate against OptiTrack via
workbench_engine.compare_pair (the same lag-corrected, active-window RMSE
comparison the live Workbench UI uses).

Scoped to P14 rather than the full dataset for iteration speed: P14 is the
participant currently below the goal, and P13's existing mediapipe_full_0.5
data is untouched by this sweep (it isn't regenerated here). Reuses
_select_patient_pose, MP_LEG_IDX, _leg_from_name from batch_mediapipe.py so
the person-selection fix stays identical -- only the model/threshold vary.

This is a bounded search, not an infinite loop: it reports the best
achievable result across the whole grid plainly, including if the
10-degree goal isn't reached.

Run:
    .venv\\Scripts\\python.exe sweep_mediapipe_config.py
"""
from __future__ import annotations

import csv
import os

import cv2
import mediapipe as mp
import numpy as np

import batch_mediapipe as bm
import pendulastic_pt_score as pt
import pt_report_common as common
import workbench_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MediaPipe_Sweep")
RESULTS_CSV = os.path.join(OUT_DIR, "mediapipe_sweep_results.csv")
RMSE_GOAL_DEG = 10.0

MODEL_VARIANTS = ["lite", "full", "heavy"]
VIS_THRESH_CANDIDATES = [0.30, 0.40, 0.50]


def discover_p14_trials():
    """P14 trials with valid OptiTrack params, paired with their video path
    (Recordings/Participant_14/{Left,Right}/pre/Trial_{n}.avi)."""
    out = []
    for rec in common.discover_all_trials():
        if rec["participant"] != "14":
            continue
        try:
            t, ang = pt.load_optitrack(rec["path"])
        except Exception:
            continue
        params = pt.compute_pt_params(t, ang)
        if params is None:
            continue
        video = os.path.join(BASE_DIR, "Recordings", "Participant_14",
                             rec["leg"].capitalize(), rec["condition"], f"Trial_{rec['trial']}.avi")
        if not os.path.isfile(video):
            continue
        out.append({"leg": rec["leg"], "trial": rec["trial"], "video": video,
                    "opti_t": t, "opti_ang": ang})
    return out


def extract_raw_landmarks(video_path, leg, model_path):
    """Run one video through MediaPipe ONCE for a given model, returning
    per-frame (t_sec, hip_xy, knee_xy, ankle_xy, hip_vis, knee_vis,
    ankle_vis) -- unthresholded. Splitting extraction from thresholding
    means sweeping several vis_thresh candidates over the same model only
    costs one inference pass, not one per threshold (inference dominates
    runtime; thresholding is cheap array filtering)."""
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
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    frames = []
    with PoseLandmarker.create_from_options(opts) as landmarker:
        frame_idx = 0
        while True:
            ok, frame_bgr = cap.read()
            if not ok:
                break
            t_sec = frame_idx / fps
            row = {"t": t_sec, "hip": None, "knee": None, "ankle": None,
                  "hip_v": 0.0, "knee_v": 0.0, "ankle_v": 0.0}
            try:
                rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_image)
                if result.pose_landmarks:
                    lms = bm._select_patient_pose(result.pose_landmarks)
                    hl, kl, al = lms[h_idx], lms[k_idx], lms[a_idx]
                    row.update(hip=(hl.x, hl.y), knee=(kl.x, kl.y), ankle=(al.x, al.y),
                              hip_v=float(hl.visibility), knee_v=float(kl.visibility),
                              ankle_v=float(al.visibility))
            except Exception:
                pass
            frames.append(row)
            frame_idx += 1
    cap.release()
    return frames


def angles_from_raw(frames, vis_thresh):
    """Cheap re-threshold + angle computation from cached raw landmarks
    (see extract_raw_landmarks) -- no MediaPipe inference here."""
    t_list, ang_list = [], []
    for row in frames:
        angle = float("nan")
        if (row["hip"] is not None and row["hip_v"] > vis_thresh
                and row["knee_v"] > vis_thresh and row["ankle_v"] > vis_thresh):
            hip, kne, ank = np.array(row["hip"]), np.array(row["knee"]), np.array(row["ankle"])
            v1, v2 = hip - kne, ank - kne
            n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
            if n1 > 1e-6 and n2 > 1e-6:
                cos_a = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
                angle = float(np.degrees(np.arccos(cos_a)))
        t_list.append(row["t"])
        ang_list.append(angle)
    return np.array(t_list), np.array(ang_list)


def score_frames(trial_frames, opti_t, opti_ang, vis_thresh):
    t_m, ang_m = angles_from_raw(trial_frames, vis_thresh)
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    return result["rmse_deg"] if result.get("status") == "ok" else None


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    trials = discover_p14_trials()
    print(f"{len(trials)} P14 trial(s) with OptiTrack ground truth found.")
    if not trials:
        return

    best = None
    rows = []
    for variant in MODEL_VARIANTS:
        model_path = os.path.join(BASE_DIR, "models", "mediapipe", f"pose_landmarker_{variant}.task")
        if not os.path.isfile(model_path):
            print(f"  [skip] {variant}: model file not found at {model_path}")
            continue

        print(f"Extracting landmarks for model={variant} (one inference pass, reused across "
             f"all vis_thresh candidates below)...")
        raw_by_trial = [extract_raw_landmarks(t["video"], t["leg"], model_path) for t in trials]

        for vis_thresh in VIS_THRESH_CANDIDATES:
            rmses = []
            for trial, frames in zip(trials, raw_by_trial):
                rmse = score_frames(frames, trial["opti_t"], trial["opti_ang"], vis_thresh)
                if rmse is not None:
                    rmses.append(rmse)
            if not rmses:
                print(f"  {variant} @ vis_thresh={vis_thresh}: no scoreable trials")
                continue
            median_rmse = float(np.median(rmses))
            rows.append({"model": variant, "vis_thresh": vis_thresh, "n_scored": len(rmses),
                        "median_rmse_deg": median_rmse, "mean_rmse_deg": float(np.mean(rmses))})
            print(f"  {variant} @ vis_thresh={vis_thresh}: n={len(rmses)}/{len(trials)}  "
                 f"median RMSE={median_rmse:.1f} deg")
            if best is None or median_rmse < best["median_rmse"]:
                best = {"model": variant, "vis_thresh": vis_thresh, "median_rmse": median_rmse,
                       "n": len(rmses), "rmses": rmses}

    if rows:
        with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"-> {RESULTS_CSV}")

    if best is None:
        print("No configuration produced a scoreable result for any trial.")
        return

    print(f"\nBest config: model={best['model']}, vis_thresh={best['vis_thresh']}")
    print(f"  n trials scored: {best['n']}/{len(trials)}")
    print(f"  median RMSE: {best['median_rmse']:.2f} deg   mean RMSE: {float(np.mean(best['rmses'])):.2f} deg")
    print(f"  per-trial RMSE range: {min(best['rmses']):.1f}-{max(best['rmses']):.1f} deg")
    if best["median_rmse"] < RMSE_GOAL_DEG:
        print(f"GOAL MET: median RMSE {best['median_rmse']:.2f} deg < {RMSE_GOAL_DEG} deg")
    else:
        print(f"GOAL NOT MET: best median RMSE {best['median_rmse']:.2f} deg, "
             f"{best['median_rmse'] - RMSE_GOAL_DEG:.2f} deg above the {RMSE_GOAL_DEG} deg target.")


if __name__ == "__main__":
    main()
