"""
sweep_imu_config.py
====================
Iterates a much wider grid of AHRS/fusion parameters than
imu_calibration_tuner.py's default TUNING_GRID, over every real recorded
trial that has both split IMU CSVs and OptiTrack ground truth. Each
candidate is replayed (imu_calibration_tuner.replay_trial) and scored
against OptiTrack via workbench_engine.compare_pair -- the same
lag-corrected, active-window RMSE comparison the live Workbench UI uses --
rather than the tuner's own internal physical-plausibility proxy, since the
literal goal here ("<5 deg RMSE vs OptiTrack") is a different metric from
that proxy.

Trial discovery/matching reuses batch_imu_vs_optitrack_rmse.discover_trials()
directly instead of re-deriving participant/leg/trial matching. Raw-sample
reconstruction reuses reconstruct_imu_raw_logs.reconstruct_trial().

This is a bounded search, not an infinite loop: it reports the best
achievable result across the whole grid plainly, including if the 5-degree
goal isn't reached, rather than running forever chasing a target that may
not be reachable through AHRS-parameter search alone.

Run:
    .venv\\Scripts\\python.exe sweep_imu_config.py
"""
from __future__ import annotations

import csv
import os

import numpy as np

import batch_imu_vs_optitrack_rmse as discovery
import pendulastic_pt_score as pt
import workbench_engine as engine
from imu_calibration_tuner import replay_trial
from reconstruct_imu_raw_logs import reconstruct_trial

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "IMU_Sweep")
RESULTS_CSV = os.path.join(OUT_DIR, "imu_sweep_results.csv")
RMSE_GOAL_DEG = 5.0

WIDE_GRID = [
    {"beta": beta, "ema_alpha": alpha, "flex_axis_capture": fac,
     "gravity_seed": gs, "method": method}
    for beta in (0.01, 0.02, 0.041, 0.06, 0.08, 0.10)
    for alpha in (0.05, 0.1, 0.2, 0.3)
    for fac in (True, False)
    for gs in (True, False)
    for method in ("relative", "ockendon", "ockendon_flipped")
]


def discover_scoreable_trials():
    """Every trial with matched IMU components + OptiTrack ground truth
    (via batch_imu_vs_optitrack_rmse.discover_trials()), reconstructed into
    a raw sample stream plus its OptiTrack (t, angle) ground truth. Trials
    with no OptiTrack match, unreadable components, or too few raw samples
    are skipped."""
    out = []
    for t_info in discovery.discover_trials():
        if not t_info.get("optitrack_path"):
            continue
        try:
            samples = reconstruct_trial(t_info["accel"], t_info["gyro"], t_info["mag"])
        except Exception:
            continue
        if len(samples) < 40:
            continue
        try:
            opti_t, opti_ang = pt.load_optitrack(t_info["optitrack_path"])
        except Exception:
            continue
        out.append({
            "label": f"P{t_info['participant']}_pos{t_info['position']}_T{t_info['trial']}",
            "samples": samples, "opti_t": opti_t, "opti_ang": opti_ang,
        })
    return out


def score_config(trials, params):
    """RMSE per trial for this config -- a list of floats, one per trial
    whose replay+comparison actually produced a score. A trial that can't
    be scored under THIS config is excluded from THIS config's list, not
    from the sweep as a whole (a different config may still score it)."""
    rmses = []
    for trial in trials:
        t_m, ang_m = replay_trial(trial["samples"], params)
        if np.count_nonzero(np.isfinite(ang_m)) < 10:
            continue
        result = engine.compare_pair(trial["opti_t"], trial["opti_ang"], t_m, ang_m)
        if result.get("status") == "ok":
            rmses.append(result["rmse_deg"])
    return rmses


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    trials = discover_scoreable_trials()
    print(f"{len(trials)} trial(s) with matched IMU components + OptiTrack ground truth.")
    if not trials:
        return

    best = None
    rows = []
    for i, params in enumerate(WIDE_GRID):
        rmses = score_config(trials, params)
        if not rmses:
            continue
        median_rmse = float(np.median(rmses))
        rows.append({**params, "n_scored": len(rmses), "median_rmse_deg": median_rmse,
                    "mean_rmse_deg": float(np.mean(rmses))})
        if best is None or median_rmse < best["median_rmse"]:
            best = {"params": params, "median_rmse": median_rmse, "n": len(rmses), "rmses": rmses}
        if (i + 1) % 100 == 0:
            shown = f"{best['median_rmse']:.1f}" if best else "n/a"
            print(f"  ...{i + 1}/{len(WIDE_GRID)} configs tried, best so far: median RMSE={shown} deg")

    if rows:
        with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"-> {RESULTS_CSV}  ({len(rows)} scoreable configs of {len(WIDE_GRID)} tried)")

    if best is None:
        print("No configuration produced a scoreable replay for any trial.")
        return

    print(f"\nBest config: {best['params']}")
    print(f"  n trials scored: {best['n']}/{len(trials)}")
    print(f"  median RMSE: {best['median_rmse']:.2f} deg   mean RMSE: {float(np.mean(best['rmses'])):.2f} deg")
    print(f"  per-trial RMSE range: {min(best['rmses']):.1f}-{max(best['rmses']):.1f} deg")
    if best["median_rmse"] < RMSE_GOAL_DEG:
        print(f"GOAL MET: median RMSE {best['median_rmse']:.2f} deg < {RMSE_GOAL_DEG} deg")
    else:
        print(f"GOAL NOT MET: best median RMSE {best['median_rmse']:.2f} deg, "
             f"{best['median_rmse'] - RMSE_GOAL_DEG:.2f} deg above the {RMSE_GOAL_DEG} deg target. "
             f"Widening the AHRS parameter grid further is unlikely to close this gap on its own -- "
             f"see the per-trial spread above for where the largest errors concentrate.")


if __name__ == "__main__":
    main()
