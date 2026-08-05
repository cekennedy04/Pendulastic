"""
analyze_accel_drift.py
=======================
One-off diagnostic: for each raw IMU trial log, double-integrates raw
accelerometer data (world-frame, gravity-subtracted) into velocity and
displacement, using verified-stationary windows (the same raw-signal
stillness check gyro-bias calibration uses) as zero-velocity-update (ZUPT)
reference points, to directly measure how much drift accumulates between
them.

This is a diagnostic only -- it does not feed back into or correct the
fused-angle pipeline. It exists to characterize whether/how much
accelerometer drift contributes to the RMSE-vs-OptiTrack problem.

Usage:
    .venv\\Scripts\\python.exe analyze_accel_drift.py <raw_log.jsonl> [<raw_log2.jsonl> ...]
"""
from __future__ import annotations

import sys

import numpy as np

from imu_calibration_tuner import replay_trial
from pendulastic_imu_server import _is_stationary_window, GYRO_BIAS_WINDOW_S


def double_integrate_drift(t: np.ndarray, accel_world: np.ndarray,
                            stationary_mask: np.ndarray) -> tuple:
    """Double-integrate world-frame linear accel into velocity and
    displacement, applying a zero-velocity-update correction at each
    verified-stationary sample: velocity is reset to 0 there, and the
    reset amount is linearly redistributed backward over the preceding
    non-stationary run so the correction doesn't appear as a discontinuous
    jump. This makes the velocity AT each stationary checkpoint directly
    readable as "how much drift accumulated since the previous checkpoint"
    -- the diagnostic quantity this script reports."""
    n = len(t)
    vel = np.zeros((n, 3))
    disp = np.zeros((n, 3))
    run_start = 0
    for i in range(1, n):
        dt = t[i] - t[i - 1]
        vel[i] = vel[i - 1] + accel_world[i] * dt
        if stationary_mask[i]:
            drift_amount = vel[i].copy()
            run_len = i - run_start
            if run_len > 0:
                for j in range(run_start, i + 1):
                    frac = (j - run_start) / run_len
                    vel[j] = vel[j] - frac * drift_amount
            run_start = i
    for i in range(1, n):
        dt = t[i] - t[i - 1]
        disp[i] = disp[i - 1] + vel[i] * dt
    return vel, disp


def _world_frame_linear_accel(raw_samples: list, params: dict) -> tuple:
    """Replay raw_samples to get per-tick orientation, then rotate each
    raw accel sample into the world frame and subtract gravity. Returns
    (t, accel_world, stationary_mask) for the available role (distal or proximal)."""
    # replay_trial() gives the final swing angle series, not per-sample
    # orientation -- for this diagnostic we only need a rough per-sample
    # rotation, so we run the same accel/gyro/mag stream through a fresh
    # MadgwickAHRS instance directly here rather than modifying
    # replay_trial()'s return contract.
    from pendulastic_imu_server import MadgwickAHRS, _gravity_seed, _qconj, _qmul

    # Determine which role is available (prioritize distal, fall back to proximal)
    available_roles = set(s.get("role") for s in raw_samples if "role" in s)
    target_role = None
    if "distal" in available_roles:
        target_role = "distal"
    elif "proximal" in available_roles:
        target_role = "proximal"
    elif available_roles:
        target_role = next(iter(available_roles))

    if not target_role:
        return (np.array([]), np.array([]), np.array([]))

    ahrs = MadgwickAHRS(beta=params["beta"])
    seeded = False
    gyro_bias = np.zeros(3)
    gyro_hold_buf, accel_hold_buf = [], []
    calib_was_stable = False
    last_ts = None
    last_accel = None

    ts_list, accel_world_list, stationary_list = [], [], []
    for samp in raw_samples:
        if samp["role"] != target_role:
            continue
        v = np.asarray(samp["v"], dtype=float)
        if samp["sensor"] == "accel":
            last_accel = v
            accel_hold_buf.append((samp["t"], v))
            accel_hold_buf[:] = [(t, vv) for t, vv in accel_hold_buf
                                 if t >= samp["t"] - GYRO_BIAS_WINDOW_S]
            if not seeded:
                ahrs.q = _gravity_seed(v)
                seeded = True
            q = ahrs.q
            qv = np.array([0.0, *v])
            world = _qmul(_qmul(q, qv), _qconj(q))[1:]
            gravity = np.array([0.0, 0.0, 9.81])
            ts_list.append(samp["t"])
            accel_world_list.append(world - gravity)
            stable = _is_stationary_window(gyro_hold_buf, accel_hold_buf, samp["t"])
            stationary_list.append(stable)
        elif samp["sensor"] == "gyro":
            ts = samp.get("phone_ts_ms") or 0
            dt = (ts - last_ts) / 1000.0 if (last_ts is not None and ts) else None
            if dt is None or not (0.0 < dt < 0.5):
                dt = 0.01
            last_ts = ts
            gyro_hold_buf.append((samp["t"], v))
            gyro_hold_buf[:] = [(t, vv) for t, vv in gyro_hold_buf
                                if t >= samp["t"] - GYRO_BIAS_WINDOW_S]
            stable = _is_stationary_window(gyro_hold_buf, accel_hold_buf, samp["t"])
            if stable and not calib_was_stable and len(gyro_hold_buf) >= 5:
                gyro_bias = np.mean([vv for _, vv in gyro_hold_buf], axis=0)
            calib_was_stable = stable
            if last_accel is not None:
                ahrs.update(v - gyro_bias, last_accel, None, dt)

    return (np.array(ts_list), np.array(accel_world_list), np.array(stationary_list))


def analyze_file(path: str) -> None:
    from tune_imu import load_raw_log

    samples = load_raw_log(path)
    if not samples:
        print(f"{path}: no samples, skipping")
        return
    params = {"beta": 0.041, "ema_alpha": 1.0,
             "flex_axis_capture": True, "gravity_seed": True}
    t, accel_world, stationary_mask = _world_frame_linear_accel(samples, params)
    if len(t) < 2:
        print(f"{path}: not enough accel samples, skipping")
        return
    vel, disp = double_integrate_drift(t, accel_world, stationary_mask)
    peak_disp = float(np.max(np.linalg.norm(disp, axis=1)))
    peak_vel_drift = float(np.max(np.linalg.norm(vel, axis=1)))
    print(f"{path}: peak displacement={peak_disp:.4f} m, "
          f"peak inter-checkpoint velocity drift={peak_vel_drift:.4f} m/s, "
          f"n_stationary_checkpoints={int(stationary_mask.sum())}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for path in sys.argv[1:]:
        analyze_file(path)


if __name__ == "__main__":
    main()
