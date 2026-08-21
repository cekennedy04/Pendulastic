"""
evaluate_ft_ratio_sweep.py
============================
Sweeps OCKENDON_FT_RATIO for the (bug-fixed) ockendon_flipped method against
real OptiTrack RMSE across all 53 real trials, to see whether a different
femur:tibia ratio constant (vs. the paper's fixed population-average 1.2)
reduces error on this project's own data. Reuses the same trial-caching
pattern as evaluate_tuning_grid_methodology.py.

Usage:
    .venv\\Scripts\\python.exe evaluate_ft_ratio_sweep.py
"""
from __future__ import annotations

import statistics

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine
import imu_calibration_tuner as tuner

RMSE_GOAL_DEG = 5.0
BASE_PARAMS = {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": True,
               "gravity_seed": True, "method": "ockendon_flipped"}
# ft_ratio < 1.0 isn't physiologically meaningful (femur is always longer
# than tibia) and can push sin(beta)/ft_ratio out of acos's domain for large
# beta -- start at 1.0. 2026-08-17: 1.00-1.60 showed monotonically
# decreasing RMSE with no minimum found yet -- widened to find where it
# actually bottoms out (values above ~2.0 are no longer physiologically
# plausible femur:tibia ratios, but useful to see the curve's shape).
FT_RATIOS = [round(1.0 + 0.1 * i, 2) for i in range(21)]  # 1.00 .. 3.00


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
    return {"samples": bound["fusion_samples"], "ref_t": ref_t, "ref_angle": ref_angle}


def main():
    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]
    trial_cache = [d for d in (load_trial_data(t) for t in matched) if d is not None]
    print(f"{len(trial_cache)} trial(s) loaded\n")

    print(f"{'ft_ratio':>9s} {'mean':>7s} {'median':>7s} {'n<5':>4s}")
    for ft_ratio in FT_RATIOS:
        params = {**BASE_PARAMS, "ft_ratio": ft_ratio}
        rmses = []
        for tc in trial_cache:
            try:
                t, angle = tuner.replay_trial(tc["samples"], params)
            except ValueError:
                # sin(beta)/ft_ratio landed outside acos's domain for this
                # trial's beta range at this ft_ratio -- skip, don't crash
                # the whole sweep over one out-of-range combo.
                continue
            if len(t) == 0:
                continue
            result = engine.compare_pair(tc["ref_t"], tc["ref_angle"], t, angle)
            if result["status"] == "ok":
                rmses.append(result["rmse_deg"])
        if not rmses:
            print(f"{ft_ratio:9.2f}  no trials scored")
            continue
        mean_r = statistics.mean(rmses)
        median_r = statistics.median(rmses)
        n_under = sum(1 for r in rmses if r < RMSE_GOAL_DEG)
        print(f"{ft_ratio:9.2f} {mean_r:7.2f} {median_r:7.2f} {n_under:4d}")


if __name__ == "__main__":
    main()
