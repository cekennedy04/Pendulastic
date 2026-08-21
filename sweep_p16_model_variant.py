"""
sweep_p16_model_variant.py
============================
Follow-up to sweep_p16_preprocessing.py: holds the winning rotate_+90
preprocessing candidate fixed and varies the MediaPipe PoseLandmarker model
variant (full vs heavy) for Participant 16's 8 trials, scored against
OptiTrack ground truth. Heavier models are typically more occlusion-robust,
which is the suspected root cause of P16's low left-leg detection.

"full" here is a direct re-measurement of the rotate_+90 numbers already
seen in sweep_p16_preprocessing.py (left median 24.1 deg, right median
30.2 deg) -- rerun here for an apples-to-apples comparison against "heavy"
in the same run.

Run:
    .venv\\Scripts\\python.exe sweep_p16_model_variant.py
"""
from __future__ import annotations

import csv
import os

import numpy as np

import pendulastic_pt_score as pt
import rmse_pipeline_common as rpc
import sweep_mediapipe_preprocessing as smp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MediaPipe_Sweep")
RESULTS_CSV = os.path.join(OUT_DIR, "p16_model_variant_sweep_results.csv")

MODEL_VARIANTS = ["full", "heavy"]
CANDIDATE = {"key": "rotate_+90", "rotate_deg": 90}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    all_trials = rpc.discover_video_trials()
    trials = [t for t in all_trials if t["participant"] == "16"]
    print(f"{len(trials)}/{len(all_trials)} trial(s) belong to Participant 16.")
    if not trials:
        return

    rows = []
    for variant in MODEL_VARIANTS:
        model_path = os.path.join(BASE_DIR, "models", "mediapipe",
                                   f"pose_landmarker_{variant}.task")
        if not os.path.isfile(model_path):
            print(f"  [skip] {variant}: model file not found at {model_path}")
            continue

        per_trial = []
        with smp._make_landmarker(model_path) as landmarker:
            for trial in trials:
                try:
                    opti_t, opti_ang = pt.load_optitrack(trial["optitrack_path"])
                except Exception as e:
                    print(f"  [skip] {trial['trial_key']}: OptiTrack load failed: {e}")
                    continue
                rmse, reason = smp.score_candidate(
                    trial["video_path"], trial["leg"], landmarker, CANDIDATE,
                    opti_t, opti_ang, trial_key=trial["trial_key"])
                if rmse is None:
                    print(f"  [skip] {trial['trial_key']} / {variant}: {reason}")
                else:
                    print(f"  {trial['trial_key']:30s} leg={trial['leg']:5s} "
                          f"model={variant:6s} rmse={rmse:.2f} deg")
                per_trial.append({
                    "model": variant, "trial_key": trial["trial_key"],
                    "leg": trial["leg"], "rmse_deg": rmse,
                })
        rows.extend(per_trial)

        for leg in ("left", "right"):
            leg_rmses = [r["rmse_deg"] for r in per_trial
                         if r["leg"] == leg and r["rmse_deg"] is not None]
            n_total = sum(1 for r in per_trial if r["leg"] == leg)
            if leg_rmses:
                print(f"  -> model={variant:6s} leg={leg:5s} n={len(leg_rmses)}/{n_total}  "
                      f"median={np.median(leg_rmses):.2f} deg  mean={np.mean(leg_rmses):.2f} deg")
            else:
                print(f"  -> model={variant:6s} leg={leg:5s} n=0/{n_total} (no scoreable trials)")
        print()

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["model", "trial_key", "leg", "rmse_deg"])
        w.writeheader()
        w.writerows(rows)
    print(f"-> {RESULTS_CSV}")


if __name__ == "__main__":
    main()
