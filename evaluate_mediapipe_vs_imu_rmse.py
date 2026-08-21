"""
evaluate_mediapipe_vs_imu_rmse.py
====================================
Real RMSE-vs-OptiTrack for the MediaPipe/vision pipeline ("full" model,
vis_thresh=0.5 -- the variant this project's own batch_mediapipe.py has
already been extracting), scored on the INTERSECTION of trials that have
both a matching video and the IMU trial set already evaluated in
evaluate_tuning_grid_methodology.py's baseline -- for a direct,
apples-to-apples comparison against the 14.84 deg (tuned) / 16.83 deg
(currently-live-config) IMU RMSE numbers found 2026-08-17.

Reuses rmse_pipeline_common.py's discovery/scoring/caching (design spec
docs/superpowers/specs/2026-08-07-rmse-validation-pipeline-design.md) --
landmark extraction is cached under sweep_cache/landmarks/ so re-running
this script (or later config sweeps) doesn't re-run MediaPipe inference.

Usage:
    .venv\\Scripts\\python.exe evaluate_mediapipe_vs_imu_rmse.py
"""
from __future__ import annotations

import os
import statistics
import time

import rmse_pipeline_common as rpc

RMSE_GOAL_DEG = 5.0
MODEL_VARIANT = "full"
VIS_THRESH = 0.5


def main():
    video_trials = rpc.discover_video_trials()
    imu_trials = rpc.discover_imu_trials()
    imu_keys = {t["trial_key"] for t in imu_trials}
    overlap = [t for t in video_trials if t["trial_key"] in imu_keys]
    print(f"{len(video_trials)} video trial(s) total, {len(overlap)} overlap "
          f"with the IMU-scored trial set\n")

    model_path = os.path.join(rpc.BASE_DIR, "models", "mediapipe",
                              f"pose_landmarker_{MODEL_VARIANT}.task")

    results = []
    t0 = time.time()
    for i, trial in enumerate(overlap):
        try:
            rmse = rpc.score_mediapipe_candidate(trial, MODEL_VARIANT, model_path, VIS_THRESH)
        except Exception as e:
            print(f"  [{i+1}/{len(overlap)}] {trial['participant']} {trial['trial_key'][:12]} "
                  f"-> ERROR {type(e).__name__}: {e}  ({time.time()-t0:.0f}s elapsed)")
            continue
        elapsed = time.time() - t0
        if rmse is None:
            print(f"  [{i+1}/{len(overlap)}] {trial['participant']} {trial['trial_key'][:12]} "
                  f"-> unscoreable  ({elapsed:.0f}s elapsed)")
            continue
        results.append((trial["participant"], trial["trial_key"], rmse))
        print(f"  [{i+1}/{len(overlap)}] {trial['participant']} {trial['trial_key'][:12]} "
              f"-> rmse={rmse:.2f} deg  ({elapsed:.0f}s elapsed)")

    rmses = [r for _, _, r in results]
    if rmses:
        mean_r = statistics.mean(rmses)
        median_r = statistics.median(rmses)
        n_under = sum(1 for r in rmses if r < RMSE_GOAL_DEG)
        print(f"\nMediaPipe ({MODEL_VARIANT}, vis_thresh={VIS_THRESH}): "
              f"n={len(rmses)}/{len(overlap)}  mean={mean_r:.2f}  median={median_r:.2f}  "
              f"n<{RMSE_GOAL_DEG}deg={n_under}")
    else:
        print("\nNo trials scored successfully.")


if __name__ == "__main__":
    main()
