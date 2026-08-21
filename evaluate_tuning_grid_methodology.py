"""
evaluate_tuning_grid_methodology.py
=====================================
Loads each real trial with OptiTrack ground truth ONCE (raw samples +
reference angle), then sweeps a grid of AHRS/method parameter combos,
scoring each combo's REAL mean/median RMSE against OptiTrack directly --
not score_waveform()'s self-consistency heuristic (no ground truth, exists
for field use where OptiTrack isn't available) -- to find which combo
actually minimizes measured error on this project's own recorded data.

Loading each trial's split-CSV components and OptiTrack reference is the
expensive part (file I/O + parsing); it happens once per trial regardless
of how many param combos are swept, by caching bind_split_csv_components()'s
merged fusion_samples and the OptiTrack (ref_t, ref_angle) pair and calling
imu_calibration_tuner.replay_trial() + workbench_engine.compare_pair()
directly per combo instead of going through load_imu_trial_from_components()
(which would re-validate/re-read every CSV on every combo).

Usage:
    .venv\\Scripts\\python.exe evaluate_tuning_grid_methodology.py [--grid full|quick]
"""
from __future__ import annotations

import statistics
import sys
import time

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine
import imu_calibration_tuner as tuner

RMSE_GOAL_DEG = 5.0


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


def score_combo(trial_cache: list, params: dict) -> list:
    rmses = []
    for tc in trial_cache:
        try:
            t, angle = tuner.replay_trial(tc["samples"], params)
        except Exception:
            continue
        if len(t) == 0:
            continue
        result = engine.compare_pair(tc["ref_t"], tc["ref_angle"], t, angle)
        if result["status"] == "ok":
            rmses.append(result["rmse_deg"])
    return rmses


def build_grid(mode: str) -> list:
    if mode == "quick":
        return [
            {"beta": beta, "ema_alpha": alpha, "flex_axis_capture": fac,
             "gravity_seed": True, "method": method}
            for beta in (0.041, 0.15)
            for alpha in (0.3,)
            for fac in (True,)
            for method in ("relative", "ockendon_flipped")
        ]
    return tuner.TUNING_GRID


def main():
    mode = "quick"
    if "--grid" in sys.argv:
        mode = sys.argv[sys.argv.index("--grid") + 1]

    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]
    print(f"Loading {len(matched)} trial(s)...")
    t0 = time.time()
    trial_cache = [d for d in (load_trial_data(t) for t in matched) if d is not None]
    print(f"{len(trial_cache)} trial(s) loaded in {time.time() - t0:.1f}s\n")

    grid = build_grid(mode)
    print(f"Sweeping {len(grid)} param combo(s) [{mode}]...\n")

    results = []
    t0 = time.time()
    for i, params in enumerate(grid):
        rmses = score_combo(trial_cache, params)
        elapsed = time.time() - t0
        if not rmses:
            print(f"  [{i+1}/{len(grid)}] {params} -> 0 scored ({elapsed:.1f}s elapsed)")
            continue
        mean_r = statistics.mean(rmses)
        median_r = statistics.median(rmses)
        n_under = sum(1 for r in rmses if r < RMSE_GOAL_DEG)
        results.append((mean_r, median_r, n_under, len(rmses), params))
        print(f"  [{i+1}/{len(grid)}] mean={mean_r:6.2f} median={median_r:6.2f} "
              f"n<5deg={n_under}/{len(rmses)}  {params}  ({elapsed:.1f}s elapsed)")

    results.sort(key=lambda r: r[0])
    print(f"\n=== Top 15 by mean RMSE ===")
    print(f"{'mean':>7s} {'median':>7s} {'n<5':>4s} {'n_ok':>5s}  params")
    for mean_r, median_r, n_under, n_ok, params in results[:15]:
        print(f"{mean_r:7.2f} {median_r:7.2f} {n_under:4d} {n_ok:5d}  {params}")


if __name__ == "__main__":
    main()
