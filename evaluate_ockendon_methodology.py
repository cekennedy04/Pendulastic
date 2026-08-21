"""
evaluate_ockendon_methodology.py
==================================
One-off diagnostic: re-scores every real trial already known to
batch_imu_vs_optitrack_rmse.py's discover_trials() under three IMU
angle-computation methods -- "relative" (this project's current default:
AHRS-fused bilateral relative angle), "ockendon", and "ockendon_flipped"
(the single-segment tibial-inclination trig model, kappa = 90 + beta -
arccos(sin(beta)/1.2), already implemented in
imu_calibration_tuner.ockendon_deg() with the same femur:tibia=1.2 ratio
described in the methodology pasted into chat 2026-08-17) -- to test
whether that published methodology reduces real measured RMSE-vs-OptiTrack
on this project's own recorded trials, not just in theory.

Reuses batch_imu_vs_optitrack_rmse.discover_trials() for trial/OptiTrack
matching and workbench_engine's validate_component_csv /
load_imu_trial_from_components(method=...) / compare_pair for scoring --
the same tested engine every other diagnostic in this project builds on,
per this repo's own convention (see analyze_accel_drift.py,
batch_imu_vs_optitrack_rmse.py docstrings) of never reimplementing
loading/scoring logic.

Not pipeline-wired -- a one-off diagnostic, same category as
analyze_accel_drift.py and batch_imu_vs_optitrack_rmse.py.

Usage:
    .venv\\Scripts\\python.exe evaluate_ockendon_methodology.py
"""
from __future__ import annotations

import statistics

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine

METHODS = ["relative", "ockendon", "ockendon_flipped"]
RMSE_GOAL_DEG = 5.0


def evaluate_trial_with_method(imu_path: str, accel_path: str, gyro_path: str,
                               mag_path: str, opti_path: str, method: str,
                               ids: dict) -> dict:
    """Same shape/logic as batch_imu_vs_optitrack_rmse.evaluate_trial(), but
    passes method= through to load_imu_trial_from_components() instead of
    always using the config default. Never raises."""
    row = {
        "participant": ids["participant"], "position": ids["position"],
        "trial": ids["trial"], "method": method,
        "status": "error", "rmse_deg": None, "mae_deg": None,
        "bias_deg": None, "n_samples": None, "error": None,
    }
    validations = {
        "accel": engine.validate_component_csv(accel_path, "accel"),
        "gyro": engine.validate_component_csv(gyro_path, "gyro"),
        "mag": engine.validate_component_csv(mag_path, "mag"),
        "imu": engine.validate_component_csv(imu_path, "imu"),
    }
    bad = [kind for kind, v in validations.items() if not v["ok"]]
    if bad:
        row["error"] = "; ".join(f"{k}: {validations[k]['error']}" for k in bad)
        return row
    try:
        t, angle, _ref = engine.load_imu_trial_from_components(validations, method=method)
    except Exception as e:
        row["error"] = f"load_imu_trial_from_components failed: {type(e).__name__}: {e}"
        return row
    try:
        ref_t, ref_angle, _opt_method = engine.load_optitrack_trial(opti_path)
    except Exception as e:
        row["error"] = f"load_optitrack_trial failed: {type(e).__name__}: {e}"
        return row
    result = engine.compare_pair(ref_t, ref_angle, t, angle)
    if result["status"] != "ok":
        row["error"] = result.get("error")
        return row
    row["status"] = "ok"
    row["rmse_deg"] = result["rmse_deg"]
    row["mae_deg"] = result["mae_deg"]
    row["bias_deg"] = result["bias_deg"]
    row["n_samples"] = result["n_samples"]
    return row


def main():
    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]
    print(f"{len(matched)} real trial(s) with OptiTrack ground truth\n")

    results = {m: [] for m in METHODS}
    for t in matched:
        for method in METHODS:
            row = evaluate_trial_with_method(
                t["imu"], t["accel"], t["gyro"], t["mag"], t["optitrack_path"],
                method, ids=t)
            results[method].append(row)

    print(f"{'method':16s} {'n_ok':>5s} {'mean':>8s} {'median':>8s} {'n<5deg':>7s}")
    for method in METHODS:
        ok = [r["rmse_deg"] for r in results[method] if r["status"] == "ok"]
        if not ok:
            n_err = sum(1 for r in results[method] if r["status"] != "ok")
            print(f"{method:16s} {'0':>5s}  (0 scored, {n_err} errored)")
            continue
        mean_r = statistics.mean(ok)
        median_r = statistics.median(ok)
        n_under = sum(1 for r in ok if r < RMSE_GOAL_DEG)
        print(f"{method:16s} {len(ok):5d} {mean_r:8.3f} {median_r:8.3f} {n_under:7d}")

    print("\nPer-trial RMSE (deg), relative vs ockendon vs ockendon_flipped:")
    for i, t in enumerate(matched):
        vals = []
        for method in METHODS:
            r = results[method][i]
            vals.append(f"{r['rmse_deg']:.1f}" if r["status"] == "ok" else "ERR")
        print(f"  {t['participant']:15s} {t['position']:10s} {t['trial']:8s}  "
              + "  ".join(f"{m}={v}" for m, v in zip(METHODS, vals)))


if __name__ == "__main__":
    main()
