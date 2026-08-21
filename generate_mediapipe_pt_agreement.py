"""
generate_mediapipe_pt_agreement.py
=====================================
Fills the gap flagged in docs/reports/2026-08-19-results-data-analysis-draft.md:
per-PT-parameter ICC(2,1) + Bland-Altman for MediaPipe vs OptiTrack, to match
generate_paper_results_analysis.py's IMU analysis. Reuses the landmark cache
from the 2026-08-18/19 MediaPipe RMSE runs (sweep_cache/landmarks/) -- no new
pose-estimation inference.

Usage:
    .venv\\Scripts\\python.exe generate_mediapipe_pt_agreement.py
"""
from __future__ import annotations

import os

import numpy as np

import workbench_engine as engine
import rmse_pipeline_common as rpc
import sweep_mediapipe_config as mp_sweep
from generate_paper_results_analysis import PARAMS, icc_2_1, bland_altman

MODEL_VARIANT = "full"
VIS_THRESH = 0.5


def main():
    video_trials = rpc.discover_video_trials()
    imu_trials = rpc.discover_imu_trials()
    imu_keys = {t["trial_key"] for t in imu_trials if t["optitrack_path"] is not None}
    overlap = [t for t in video_trials if t["trial_key"] in imu_keys]
    print(f"{len(overlap)} video trial(s) overlapping the IMU-scored set\n")

    model_path = os.path.join(rpc.BASE_DIR, "models", "mediapipe",
                              f"pose_landmarker_{MODEL_VARIANT}.task")

    rows = []
    for i, t in enumerate(overlap):
        try:
            frames = rpc.extract_landmarks_cached(t, MODEL_VARIANT, model_path)
            m_t, m_angle = mp_sweep.angles_from_raw(frames, VIS_THRESH)
            ref_t, ref_angle, _m = engine.load_optitrack_trial(t["optitrack_path"])
        except Exception as e:
            print(f"  [{i+1}/{len(overlap)}] {t['participant']} -> ERROR {type(e).__name__}: {e}")
            continue
        if np.count_nonzero(np.isfinite(m_angle)) < 10 or len(ref_t) < 10:
            print(f"  [{i+1}/{len(overlap)}] {t['participant']} -> too few finite samples")
            continue
        mp_pt = engine.windowed_pt_params(m_t, m_angle)
        opti_pt = engine.windowed_pt_params(ref_t, ref_angle)
        rows.append({"pid": t["participant"], "mp": mp_pt, "opti": opti_pt})
        print(f"  [{i+1}/{len(overlap)}] {t['participant']} -> ok")

    print(f"\n{len(rows)} trials with computable PT parameters (of {len(overlap)})\n")
    print("=== MediaPipe vs OptiTrack: ICC(2,1) + Bland-Altman, per PT parameter ===")
    print(f"{'param':16s} {'ICC(2,1)':>10s} {'bias':>8s} {'LoA_lo':>8s} {'LoA_hi':>8s} {'n':>4s}")
    for p in PARAMS:
        a = np.array([r["opti"][p] for r in rows], dtype=float)
        b = np.array([r["mp"][p] for r in rows], dtype=float)
        icc = icc_2_1(a, b)
        ba = bland_altman(a, b)
        icc_s = f"{icc:.3f}" if icc is not None else "n/a"
        if ba:
            print(f"{p:16s} {icc_s:>10s} {ba['bias']:8.3f} {ba['loa_lower']:8.3f} "
                  f"{ba['loa_upper']:8.3f} {ba['n']:4d}")
        else:
            print(f"{p:16s} {icc_s:>10s} {'n/a':>8s}")


if __name__ == "__main__":
    main()
