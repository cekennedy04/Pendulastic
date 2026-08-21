"""
evaluate_live_vs_replay_calibration.py
=========================================
Diagnostic (a) from the 2026-08-18 investigation: every RMSE number reported
this session (including batch_imu_vs_optitrack_rmse.py's own baseline) comes
from imu_calibration_tuner.replay_trial() recomputing its OWN zero-reference
offline (captured at release-motion onset) from the raw sensor log -- NOT
from whatever pendulastic_imu_server.zero() (the live, countdown-triggered
auto-tare) actually produced during the real recording. The live app's
actual saved output is the knee_angle_deg column already sitting in each
Trial_N_imu.csv.

This script scores THAT already-recorded live column directly against
OptiTrack (bypassing replay_trial entirely) and compares it trial-by-trial
against the offline-replay RMSE, to answer: is the live auto-tare
meaningfully worse than the offline release-onset zero we've been
evaluating all session?

Usage:
    .venv\\Scripts\\python.exe evaluate_live_vs_replay_calibration.py
"""
from __future__ import annotations

import csv
import statistics

import numpy as np

import batch_imu_vs_optitrack_rmse as batch
import workbench_engine as engine
import imu_calibration_tuner as tuner

WINNING_PARAMS = {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
                  "gravity_seed": True, "method": "relative"}


def load_live_csv_angle(imu_path: str):
    """Read the already-recorded live knee_angle_deg column directly --
    the actual output the live app saved during acquisition, computed via
    pendulastic_imu_server.swing_angle_deg() and its live zero()."""
    t_vals, angle_vals = [], []
    with open(imu_path, newline="", encoding="utf-8") as f:
        lines = (row for row in f if not row.startswith("#"))
        for row in csv.DictReader(lines):
            try:
                t_vals.append(float(row["time_s"]))
                angle_vals.append(float(row["knee_angle_deg"]))
            except (KeyError, ValueError):
                continue
    return np.asarray(t_vals, dtype=float), np.asarray(angle_vals, dtype=float)


def main():
    trials = batch.discover_trials()
    matched = [t for t in trials if t["optitrack_path"] is not None]

    live_rmses, replay_rmses = [], []
    print(f"{'trial':45s} {'live_rmse':>10s} {'replay_rmse':>12s} {'delta':>8s}")
    for t in matched:
        try:
            ref_t, ref_angle, _m = engine.load_optitrack_trial(t["optitrack_path"])
        except Exception:
            continue

        live_t, live_angle = load_live_csv_angle(t["imu"])
        live_result = engine.compare_pair(ref_t, ref_angle, live_t, live_angle) \
            if len(live_t) >= 4 else {"status": "error"}

        validations = {
            "accel": engine.validate_component_csv(t["accel"], "accel"),
            "gyro": engine.validate_component_csv(t["gyro"], "gyro"),
            "mag": engine.validate_component_csv(t["mag"], "mag"),
            "imu": engine.validate_component_csv(t["imu"], "imu"),
        }
        replay_result = {"status": "error"}
        if all(v["ok"] for v in validations.values()):
            try:
                rt, rangle, _ref = engine.load_imu_trial_from_components(
                    validations, method="relative")
                rt2 = rt
                replay_result = engine.compare_pair(ref_t, ref_angle, rt2, rangle) \
                    if len(rt2) >= 4 else {"status": "error"}
            except Exception:
                pass

        label = f"{t['participant']} {t['position']} {t['trial']}"
        live_ok = live_result.get("status") == "ok"
        replay_ok = replay_result.get("status") == "ok"
        if live_ok:
            live_rmses.append(live_result["rmse_deg"])
        if replay_ok:
            replay_rmses.append(replay_result["rmse_deg"])
        lv = f"{live_result['rmse_deg']:.1f}" if live_ok else "ERR"
        rv = f"{replay_result['rmse_deg']:.1f}" if replay_ok else "ERR"
        delta = (f"{live_result['rmse_deg'] - replay_result['rmse_deg']:+.1f}"
                 if live_ok and replay_ok else "")
        print(f"{label:45s} {lv:>10s} {rv:>12s} {delta:>8s}")

    print()
    if live_rmses:
        print(f"LIVE   (actual recorded zero()):  n={len(live_rmses)}  "
              f"mean={statistics.mean(live_rmses):.2f}  median={statistics.median(live_rmses):.2f}")
    if replay_rmses:
        print(f"REPLAY (offline release-onset zero): n={len(replay_rmses)}  "
              f"mean={statistics.mean(replay_rmses):.2f}  median={statistics.median(replay_rmses):.2f}")


if __name__ == "__main__":
    main()
