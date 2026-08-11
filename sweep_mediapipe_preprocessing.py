"""
sweep_mediapipe_preprocessing.py
=================================
Non-gating diagnostic sweep comparing three frame-preprocessing mechanisms
(rotate-to-upright, motion-based leg crop, stateful identity-tracker reuse)
against today's baseline person-selection, in isolation, across every
participant with video + OptiTrack ground truth -- not just P14. See
docs/superpowers/specs/2026-08-11-mediapipe-hpe-preprocessing-design.md for
the full design.

This script only reports; it asserts nothing and is not part of the pytest
suite (real video inference is slow and depends on local data files not
guaranteed present on every machine -- see the design spec S6).

Run:
    .venv\\Scripts\\python.exe sweep_mediapipe_preprocessing.py
"""
from __future__ import annotations

import csv
import hashlib
import inspect
import json
import os
import sys

import cv2
import mediapipe as mp
import numpy as np

import batch_mediapipe as bm
import mediapipe_preprocessing as mp_pre
import patient_identity_tracker as pit
import pendulastic_pt_score as pt
import rmse_pipeline_common as rpc
import workbench_engine as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MediaPipe_Sweep")
RESULTS_CSV = os.path.join(OUT_DIR, "preprocessing_sweep_results.csv")
CACHE_DIR = os.path.join(OUT_DIR, "preprocessing_cache")
CACHE_MANIFEST = os.path.join(CACHE_DIR, "manifest.json")

RMSE_GOAL_DEG = 10.0
GOAL_FRACTION = 0.90
MODEL_VARIANT = "full"
VIS_THRESH = 0.40

CANDIDATES = [
    {"key": "baseline"},
    {"key": "rotate_+90", "rotate_deg": 90},
    {"key": "rotate_-90", "rotate_deg": -90},
    {"key": "crop"},
    {"key": "identity_tracker"},
]

BaseOptions = mp.tasks.BaseOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOpts = mp.tasks.vision.PoseLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode


def _make_landmarker(model_path):
    opts = PoseLandmarkerOpts(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=VisionRunningMode.IMAGE,
        num_poses=2,
    )
    return PoseLandmarker.create_from_options(opts)


def _select_pose_for_candidate(candidate, tracker, poses, w, h):
    """Dispatch person-selection by candidate key. identity_tracker uses
    the stateful PatientIdentityTracker (production's own selection logic,
    never before measured in a sweep); every other candidate uses today's
    stateless bm._select_patient_pose, the existing sweep_mediapipe_config.py
    baseline, so every non-identity-tracker candidate is compared against
    the same person-selection logic and only the frame preprocessing
    varies."""
    if candidate["key"] == "identity_tracker":
        return tracker.select(poses, w, h).pose
    return bm._select_patient_pose(poses)


def extract_landmarks_for_candidate(video_path, leg, model_path, candidate):
    """Runs one video through MediaPipe once for the given candidate's
    preprocessing. Returns per-frame dicts with PIXEL-space (not
    normalized) hip/knee/ankle coordinates -- required for
    mp_pre.knee_angle_from_points()'s rotation-invariance property to
    actually hold for the rotate_+90/rotate_-90 candidates (see that
    function's docstring)."""
    h_idx, k_idx, a_idx = bm.MP_LEG_IDX[leg]
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    raw_frames = []
    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        raw_frames.append(frame_bgr)
    cap.release()
    if not raw_frames:
        return []

    if candidate["key"] == "crop":
        raw_frames = mp_pre.crop_to_moving_leg(raw_frames, fps)

    rotate_deg = candidate.get("rotate_deg", 0)
    tracker = pit.PatientIdentityTracker(h_idx, k_idx, a_idx)

    frames_out = []
    with _make_landmarker(model_path) as landmarker:
        for i, frame_bgr in enumerate(raw_frames):
            t_sec = i / fps
            row = {"t": t_sec, "hip_px": None, "knee_px": None, "ankle_px": None,
                   "hip_v": 0.0, "knee_v": 0.0, "ankle_v": 0.0}
            try:
                proc_frame = (mp_pre.rotate_to_upright(frame_bgr, rotate_deg)
                             if rotate_deg else frame_bgr)
                fh, fw = proc_frame.shape[:2]
                rgb = cv2.cvtColor(proc_frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_image)
                poses = result.pose_landmarks or []
                pose = _select_pose_for_candidate(candidate, tracker, poses, fw, fh)
                if pose is not None:
                    hl, kl, al = pose[h_idx], pose[k_idx], pose[a_idx]
                    row.update(
                        hip_px=(hl.x * fw, hl.y * fh),
                        knee_px=(kl.x * fw, kl.y * fh),
                        ankle_px=(al.x * fw, al.y * fh),
                        hip_v=float(hl.visibility), knee_v=float(kl.visibility),
                        ankle_v=float(al.visibility))
            except Exception:
                pass
            frames_out.append(row)
    return frames_out


def angles_from_raw(frames, vis_thresh):
    t_list, ang_list = [], []
    for row in frames:
        angle = float("nan")
        if (row["hip_px"] is not None and row["hip_v"] > vis_thresh
                and row["knee_v"] > vis_thresh and row["ankle_v"] > vis_thresh):
            angle = mp_pre.knee_angle_from_points(
                row["hip_px"], row["knee_px"], row["ankle_px"])
        t_list.append(row["t"])
        ang_list.append(angle)
    return np.array(t_list), np.array(ang_list)


def score_candidate(video_path, leg, model_path, candidate, opti_t, opti_ang,
                    vis_thresh=VIS_THRESH):
    frames = extract_landmarks_for_candidate(video_path, leg, model_path, candidate)
    t_m, ang_m = angles_from_raw(frames, vis_thresh)
    if np.count_nonzero(np.isfinite(ang_m)) < 10:
        return None
    result = engine.compare_pair(opti_t, opti_ang, t_m, ang_m)
    return result["rmse_deg"] if result.get("status") == "ok" else None


def _load_cache():
    if not os.path.isfile(CACHE_MANIFEST):
        return {}
    try:
        with open(CACHE_MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print(f"{CACHE_MANIFEST} failed to parse -- treating as empty.")
        return {}


def _save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp_path = CACHE_MANIFEST + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, sort_keys=True)
    os.replace(tmp_path, CACHE_MANIFEST)


def _implementation_fingerprint():
    parts = [inspect.getsource(sys.modules[__name__]), inspect.getsource(mp_pre)]
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _cache_key(trial, candidate_key, model_path, stat_cache, impl_fp):
    video_fp = rpc.sha256_file(trial["video_path"], stat_cache)
    model_fp = rpc.sha256_file(model_path, stat_cache)
    blob = json.dumps({
        "trial_key": trial["trial_key"], "candidate": candidate_key,
        "video": video_fp, "model": model_fp, "impl": impl_fp,
    }, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    trials = rpc.discover_video_trials()
    print(f"{len(trials)} trial(s) with video + OptiTrack ground truth found.")
    if not trials:
        return

    model_path = os.path.join(BASE_DIR, "models", "mediapipe",
                              f"pose_landmarker_{MODEL_VARIANT}.task")
    if not os.path.isfile(model_path):
        print(f"model file not found at {model_path}")
        return

    cache = _load_cache()
    stat_cache = {}
    impl_fp = _implementation_fingerprint()
    rows = []

    for candidate in CANDIDATES:
        rmses = []
        for trial in trials:
            try:
                opti_t, opti_ang = pt.load_optitrack(trial["optitrack_path"])
            except Exception as e:
                print(f"  [skip] {trial['trial_key']}: OptiTrack load failed: {e}")
                continue

            cache_key = _cache_key(trial, candidate["key"], model_path, stat_cache, impl_fp)
            if cache_key in cache:
                rmse = cache[cache_key]
            else:
                try:
                    rmse = score_candidate(trial["video_path"], trial["leg"], model_path,
                                           candidate, opti_t, opti_ang)
                except Exception as e:
                    print(f"  [error] {trial['trial_key']} / {candidate['key']}: {e}")
                    rmse = None
                cache[cache_key] = rmse
            if rmse is not None:
                rmses.append(rmse)

        _save_cache(cache)
        n_scored = len(rmses)
        n_under_goal = sum(1 for r in rmses if r < RMSE_GOAL_DEG)
        pct_under_goal = (n_under_goal / n_scored * 100.0) if n_scored else 0.0
        median_rmse = float(np.median(rmses)) if rmses else None
        mean_rmse = float(np.mean(rmses)) if rmses else None

        rows.append({
            "candidate": candidate["key"], "n_trials": len(trials), "n_scored": n_scored,
            "median_rmse_deg": median_rmse, "mean_rmse_deg": mean_rmse,
            "pct_under_10deg": pct_under_goal,
        })

        median_str = f"{median_rmse:.2f}" if median_rmse is not None else "n/a"
        print(f"{candidate['key']:16s} n_scored={n_scored}/{len(trials)}  "
             f"median={median_str} deg  %<10deg={pct_under_goal:.1f}%")
        goal_met = n_scored > 0 and pct_under_goal >= GOAL_FRACTION * 100.0
        print(f"  {'GOAL MET' if goal_met else 'goal not met'} "
             f"({pct_under_goal:.1f}% of trials < {RMSE_GOAL_DEG:.0f} deg, "
             f"target {GOAL_FRACTION*100:.0f}%)")

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"-> {RESULTS_CSV}")


if __name__ == "__main__":
    main()
