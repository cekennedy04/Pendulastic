"""
evaluate_mag_toggle.py
========================
Real-RMSE comparison of use_mag=False (current default) vs True, on top of
the best combo found by evaluate_tuning_grid_methodology.py's full sweep
(beta=0.041, ema_alpha=0.5, flex_axis_capture=True, gravity_seed=True,
method=relative), to test the pasted spec's "9-axis" (accel+gyro+mag) claim
against this project's deliberately mag-free default.

Usage:
    .venv\\Scripts\\python.exe evaluate_mag_toggle.py
"""
from __future__ import annotations

import statistics

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine
import imu_calibration_tuner as tuner

RMSE_GOAL_DEG = 5.0
BEST_PARAMS = {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
               "gravity_seed": True, "method": "relative"}


def load_trial_data(t: dict):
    validations = {
        "accel": engine.validate_component_csv(t["accel"], "accel"),
        "gyro": engine.validate_component_csv(t["gyro"], "gyro"),
        "mag": engine.validate_component_csv(t["mag"], "mag"),
        "imu": engine.validate_component_csv(t["imu"], "imu"),
    }
    if any(not v["ok"] for v in validations.values()):
        return None
    try:
        bound = engine.bind_split_csv_components(validations)
        ref_t, ref_angle, _method = engine.load_optitrack_trial(t["optitrack_path"])
    except Exception:
        return None
    return {"samples": bound["fusion_samples"], "ref_t": ref_t, "ref_angle": ref_angle,
            "label": f"{t['participant']} {t['position']} {t['trial']}"}


def main():
    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]
    trial_cache = [d for d in (load_trial_data(t) for t in matched) if d is not None]
    print(f"{len(trial_cache)} trial(s) loaded\n")

    for use_mag in (False, True):
        params = {**BEST_PARAMS, "use_mag": use_mag}
        per_trial = []
        for tc in trial_cache:
            t, angle = tuner.replay_trial(tc["samples"], params)
            if len(t) == 0:
                continue
            result = engine.compare_pair(tc["ref_t"], tc["ref_angle"], t, angle)
            if result["status"] == "ok":
                per_trial.append((tc["label"], result["rmse_deg"]))
        rmses = [r for _, r in per_trial]
        mean_r = statistics.mean(rmses)
        median_r = statistics.median(rmses)
        n_under = sum(1 for r in rmses if r < RMSE_GOAL_DEG)
        print(f"use_mag={use_mag!s:5s}  mean={mean_r:6.2f}  median={median_r:6.2f}  "
              f"n<5deg={n_under}/{len(rmses)}")

    print("\nPer-trial (use_mag False vs True):")
    params_false = {**BEST_PARAMS, "use_mag": False}
    params_true = {**BEST_PARAMS, "use_mag": True}
    for tc in trial_cache:
        t1, a1 = tuner.replay_trial(tc["samples"], params_false)
        t2, a2 = tuner.replay_trial(tc["samples"], params_true)
        r1 = engine.compare_pair(tc["ref_t"], tc["ref_angle"], t1, a1)
        r2 = engine.compare_pair(tc["ref_t"], tc["ref_angle"], t2, a2)
        v1 = f"{r1['rmse_deg']:.1f}" if r1["status"] == "ok" else "ERR"
        v2 = f"{r2['rmse_deg']:.1f}" if r2["status"] == "ok" else "ERR"
        print(f"  {tc['label']:35s} no_mag={v1:>6s}  with_mag={v2:>6s}")


if __name__ == "__main__":
    main()
