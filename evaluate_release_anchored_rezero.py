"""
evaluate_release_anchored_rezero.py
======================================
Diagnostic (b) from the 2026-08-18 investigation, per the literature
agent's recommendation: re-zero the IMU angle series so its value at its
OWN independently-detected release instant matches OptiTrack's value at
ITS OWN independently-detected release instant (workbench_engine's
_release_time(), the same release detector compute_pt_params uses
everywhere else in this codebase) -- i.e. anchor both curves to agree at
the one physically well-defined instant (leg fully extended, about to
drop), rather than relying on whatever pose replay_trial's own
onset-of-motion zero-capture happened to measure.

This directly tests the bias-decomposition finding (67.6%/76.0% of RMSE is
a per-trial constant offset) against a principled removal method, rather
than an idealized "subtract the trial's own mean error" upper bound.

Usage:
    .venv\\Scripts\\python.exe evaluate_release_anchored_rezero.py
"""
from __future__ import annotations

import statistics

import numpy as np

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine

RMSE_GOAL_DEG = 5.0


def main():
    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]

    baseline_rmses, rezeroed_rmses = [], []
    n_no_release = 0
    print(f"{'trial':45s} {'baseline':>9s} {'rezeroed':>9s} {'shift':>7s}")
    for t in matched:
        validations = {
            "accel": engine.validate_component_csv(t["accel"], "accel"),
            "gyro": engine.validate_component_csv(t["gyro"], "gyro"),
            "mag": engine.validate_component_csv(t["mag"], "mag"),
            "imu": engine.validate_component_csv(t["imu"], "imu"),
        }
        if any(not v["ok"] for v in validations.values()):
            continue
        try:
            test_t, test_angle, _ref = engine.load_imu_trial_from_components(
                validations, method="relative")
            ref_t, ref_angle, _m = engine.load_optitrack_trial(t["optitrack_path"])
        except Exception:
            continue

        baseline = engine.compare_pair(ref_t, ref_angle, test_t, test_angle)
        if baseline.get("status") != "ok":
            continue
        baseline_rmses.append(baseline["rmse_deg"])

        test_release = engine._release_time(test_t, test_angle)
        ref_release = engine._release_time(ref_t, ref_angle)
        label = f"{t['participant']} {t['position']} {t['trial']}"
        if test_release is None or ref_release is None:
            n_no_release += 1
            print(f"{label:45s} {baseline['rmse_deg']:9.2f} {'no release':>9s}")
            continue

        test_mask = np.isfinite(test_t) & np.isfinite(test_angle)
        ref_mask = np.isfinite(ref_t) & np.isfinite(ref_angle)
        v_test = float(np.interp(test_release, test_t[test_mask], test_angle[test_mask]))
        v_ref = float(np.interp(ref_release, ref_t[ref_mask], ref_angle[ref_mask]))
        shift = v_ref - v_test
        rezeroed_angle = test_angle + shift

        rezeroed = engine.compare_pair(ref_t, ref_angle, test_t, rezeroed_angle)
        if rezeroed.get("status") == "ok":
            rezeroed_rmses.append(rezeroed["rmse_deg"])
            print(f"{label:45s} {baseline['rmse_deg']:9.2f} {rezeroed['rmse_deg']:9.2f} {shift:7.1f}")
        else:
            print(f"{label:45s} {baseline['rmse_deg']:9.2f} {'ERR':>9s} {shift:7.1f}")

    print()
    print(f"Baseline (offline release-onset zero, no rezero): n={len(baseline_rmses)}  "
          f"mean={statistics.mean(baseline_rmses):.2f}  median={statistics.median(baseline_rmses):.2f}  "
          f"n<5deg={sum(1 for r in baseline_rmses if r < RMSE_GOAL_DEG)}")
    if rezeroed_rmses:
        print(f"Release-anchored rezero:                          n={len(rezeroed_rmses)}  "
              f"mean={statistics.mean(rezeroed_rmses):.2f}  median={statistics.median(rezeroed_rmses):.2f}  "
              f"n<5deg={sum(1 for r in rezeroed_rmses if r < RMSE_GOAL_DEG)}")
    print(f"Trials with no detectable release (skipped): {n_no_release}")


if __name__ == "__main__":
    main()
