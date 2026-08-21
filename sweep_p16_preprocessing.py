"""
sweep_p16_preprocessing.py
===========================
Scoped copy of sweep_mediapipe_preprocessing.py's candidate grid (baseline,
rotate_+90, rotate_-90, crop, identity_tracker), filtered to Participant 16
only -- P16's Left-leg trials showed unusually low MediaPipe detection
(3-15% vs 65-80% on Right) because the two legs are pressed together and the
tracked leg is occluded by the other one in that camera framing. Reports
per-trial RMSE (not just the aggregate) so left vs right can be compared
directly, since only the left trials are suspected to benefit.

Run:
    .venv\\Scripts\\python.exe sweep_p16_preprocessing.py
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
RESULTS_CSV = os.path.join(OUT_DIR, "p16_preprocessing_sweep_results.csv")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    all_trials = rpc.discover_video_trials()
    trials = [t for t in all_trials if t["participant"] == "16"]
    print(f"{len(trials)}/{len(all_trials)} trial(s) belong to Participant 16.")
    for t in trials:
        print(f"  {t['trial_key']}  leg={t['leg']}  video={t['video_path']}")
    if not trials:
        return

    model_path = os.path.join(BASE_DIR, "models", "mediapipe",
                               f"pose_landmarker_{smp.MODEL_VARIANT}.task")
    if not os.path.isfile(model_path):
        print(f"model file not found at {model_path}")
        return

    rows = []
    for candidate in smp.CANDIDATES:
        per_trial = []
        with smp._make_landmarker(model_path) as landmarker:
            for trial in trials:
                try:
                    opti_t, opti_ang = pt.load_optitrack(trial["optitrack_path"])
                except Exception as e:
                    print(f"  [skip] {trial['trial_key']}: OptiTrack load failed: {e}")
                    continue
                rmse, reason = smp.score_candidate(
                    trial["video_path"], trial["leg"], landmarker, candidate,
                    opti_t, opti_ang, trial_key=trial["trial_key"])
                if rmse is None:
                    print(f"  [skip] {trial['trial_key']} / {candidate['key']}: {reason}")
                else:
                    print(f"  {trial['trial_key']:30s} leg={trial['leg']:5s} "
                          f"{candidate['key']:16s} rmse={rmse:.2f} deg")
                per_trial.append({
                    "candidate": candidate["key"], "trial_key": trial["trial_key"],
                    "leg": trial["leg"], "rmse_deg": rmse,
                })
        rows.extend(per_trial)

        for leg in ("left", "right"):
            leg_rmses = [r["rmse_deg"] for r in per_trial
                         if r["leg"] == leg and r["rmse_deg"] is not None]
            if leg_rmses:
                print(f"  -> {candidate['key']:16s} leg={leg:5s} "
                      f"n={len(leg_rmses)}/{sum(1 for r in per_trial if r['leg'] == leg)}  "
                      f"median={np.median(leg_rmses):.2f} deg  mean={np.mean(leg_rmses):.2f} deg")
            else:
                print(f"  -> {candidate['key']:16s} leg={leg:5s} n=0 (no scoreable trials)")
        print()

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["candidate", "trial_key", "leg", "rmse_deg"])
        w.writeheader()
        w.writerows(rows)
    print(f"-> {RESULTS_CSV}")


if __name__ == "__main__":
    main()
