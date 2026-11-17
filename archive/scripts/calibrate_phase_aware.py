#!/usr/bin/env python3
"""
calibrate_phase_aware.py
=========================
Spike: does adding motion-dynamics features to the calibration regression
beat the existing degree-1 f(mp_angle) model in calibrate.py?

Motivation (2026-08-21 biomedical-search brainstorm): monocular pose-
estimation literature finds systematic bias is often a function of motion
*phase*, not just the instantaneous angle value -- e.g. gait papers found
error spikes during push-off/swing (fast motion), correctable by adding a
phase-aware term to the calibration. Pendulastic tracks the pendulum test
(damped-oscillation leg swing), not gait, so there is no "stride phase" --
the direct analog is angular velocity, which is high during the fast swing
and near-zero at the extremes, i.e. it plays the same role stride phase
plays in a gait cycle. The training data also carries hip_vis/knee_vis
(MediaPipe landmark visibility) that calibrate.py loads but never uses.

This script reuses calibrate.py's data loading/LOTO/quality-filter exactly
(same trials, same min_r=0.70 + exclude_flipped filters) so the LOTO RMSE
is directly comparable to calibrate.py's baseline (20.07 deg, run 2026-08-21).

Feature set: [mp_angle, mp_angle^2, mp_velocity, |mp_velocity|, hip_vis,
knee_vis], Ridge regression, same LOTO-CV protocol.

Usage
-----
  .venv\\Scripts\\python.exe calibrate_phase_aware.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.preprocessing import StandardScaler

from calibrate import INPUT_JSON, DEFAULT_MIN_R, DEFAULT_EXCLUDE_FLIPPED, _rmse

ALPHA = 1.0


def load_data_with_velocity(path: Path, min_r: float, exclude_flipped: bool):
    """
    Same trial/quality filtering as calibrate.load_data, but keeps
    frame_original/hip_vis/knee_vis and derives per-trial angular velocity
    (deg per frame, using frame_original spacing to handle dropped frames).

    Returns
    -------
    X       : (N, 6) [mp_angle, mp_angle^2, velocity, |velocity|, hip_vis, knee_vis]
    y       : (N,)   ot_angle
    groups  : (N,)   trial name
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    trial_meta = {a["trial"]: a for a in data.get("trial_alignments", [])}

    def trial_ok(name: str) -> bool:
        meta = trial_meta.get(name, {})
        r_raw = meta.get("r_best")
        r = 1.0 if r_raw is None else float(r_raw)
        flip = meta.get("flipped", False)
        if min_r is not None and r < min_r:
            return False
        if exclude_flipped and flip:
            return False
        return True

    by_trial: dict[str, list[dict]] = {}
    for s in data["samples"]:
        if s.get("mp_angle_aligned") is None or not trial_ok(s["trial"]):
            continue
        by_trial.setdefault(s["trial"], []).append(s)

    rows = []
    for trial, samples in by_trial.items():
        samples = sorted(samples, key=lambda s: s["frame_original"])
        angles = [s["mp_angle_aligned"] for s in samples]
        frames = [s["frame_original"] for s in samples]

        # Central difference where possible (better noise behavior than
        # forward diff); falls back to one-sided diff at the trial edges.
        # frame_original has occasional gaps (dropped frames during
        # tracking) -- dividing by the actual frame delta keeps velocity
        # in consistent deg-per-frame units instead of spiking across gaps.
        vel = [0.0] * len(samples)
        for i in range(len(samples)):
            lo = max(0, i - 1)
            hi = min(len(samples) - 1, i + 1)
            if hi == lo:
                vel[i] = 0.0
                continue
            dframe = frames[hi] - frames[lo]
            vel[i] = (angles[hi] - angles[lo]) / dframe if dframe else 0.0

        for s, v in zip(samples, vel):
            rows.append({
                "mp_angle": s["mp_angle_aligned"],
                "velocity": v,
                "hip_vis":  s["hip_vis"],
                "knee_vis": s["knee_vis"],
                "ot_angle": s["ot_angle"],
                "trial":    trial,
            })

    mp   = np.array([r["mp_angle"] for r in rows], dtype=np.float64)
    vel  = np.array([r["velocity"] for r in rows], dtype=np.float64)
    hipv = np.array([r["hip_vis"]  for r in rows], dtype=np.float64)
    kneev = np.array([r["knee_vis"] for r in rows], dtype=np.float64)

    X = np.column_stack([mp, mp ** 2, vel, np.abs(vel), hipv, kneev])
    y = np.array([r["ot_angle"] for r in rows], dtype=np.float64)
    groups = np.array([r["trial"] for r in rows])
    return X, y, groups


class _ScaledRidge:
    """Ridge on standardized features -- keeps regularization comparable
    across features with very different scales (angle ~0-180 vs vis ~0-1)."""

    def __init__(self, alpha: float):
        self._scaler = StandardScaler()
        self._ridge = Ridge(alpha=alpha, fit_intercept=True)

    def fit(self, X, y):
        self._ridge.fit(self._scaler.fit_transform(X), y)
        return self

    def predict(self, X):
        return self._ridge.predict(self._scaler.transform(X))


def loto_cv(X, y, groups, alpha: float):
    logo = LeaveOneGroupOut()
    y_loto = np.empty_like(y)
    rows = []
    for train_idx, test_idx in logo.split(X, y, groups):
        model = _ScaledRidge(alpha)
        model.fit(X[train_idx], y[train_idx])
        y_hat = model.predict(X[test_idx])
        y_loto[test_idx] = y_hat
        rows.append({
            "trial": groups[test_idx[0]],
            "n": len(test_idx),
            "mae": float(mean_absolute_error(y[test_idx], y_hat)),
            "rmse": _rmse(y[test_idx], y_hat),
        })
    return rows, y_loto


def main() -> None:
    print(f"Loading {INPUT_JSON} ...")
    X, y, groups = load_data_with_velocity(
        INPUT_JSON, min_r=DEFAULT_MIN_R, exclude_flipped=DEFAULT_EXCLUDE_FLIPPED)
    n_trials = len(set(groups))
    print(f"  {len(y):,} samples  /  {n_trials} trials")
    print("  Features: [mp_angle, mp_angle^2, velocity, |velocity|, hip_vis, knee_vis]")

    print(f"\n=== Leave-One-Trial-Out Cross-Validation (Ridge alpha={ALPHA}) ===\n")
    rows, y_loto = loto_cv(X, y, groups, ALPHA)
    rows.sort(key=lambda r: r["trial"])

    W = 44
    hdr = f"  {'Trial':<{W}} {'N':>5}  {'MAE (deg)':>9}  {'RMSE (deg)':>10}"
    sep = "  " + "-" * (len(hdr) - 2)
    print(sep); print(hdr); print(sep)
    for r in rows:
        print(f"  {r['trial']:<{W}} {r['n']:>5}  {r['mae']:>9.2f}  {r['rmse']:>10.2f}")
    print(sep)

    loto_overall_rmse = _rmse(y, y_loto)
    loto_overall_mae = float(mean_absolute_error(y, y_loto))
    print(f"\n  LOTO overall RMSE : {loto_overall_rmse:.2f} deg")
    print(f"  LOTO overall MAE  : {loto_overall_mae:.2f} deg")

    BASELINE_LOTO_RMSE = 20.07  # calibrate.py, degree=1, same filters, 2026-08-21 run
    delta = BASELINE_LOTO_RMSE - loto_overall_rmse
    print(f"\n  Baseline (calibrate.py, linear f(mp_angle)) LOTO RMSE : {BASELINE_LOTO_RMSE:.2f} deg")
    print(f"  Phase-aware LOTO RMSE                                 : {loto_overall_rmse:.2f} deg")
    print(f"  Delta                                                 : {delta:+.2f} deg")

    # Fit final model on all data to see which features actually carry
    # weight (sign/magnitude of standardized coefficients).
    final = _ScaledRidge(ALPHA)
    final.fit(X, y)
    feature_names = ["mp_angle", "mp_angle^2", "velocity", "|velocity|", "hip_vis", "knee_vis"]
    print("\n  Standardized coefficients (relative feature importance):")
    for name, coef in zip(feature_names, final._ridge.coef_):
        print(f"    {name:<12} {coef:+8.3f}")


if __name__ == "__main__":
    main()
