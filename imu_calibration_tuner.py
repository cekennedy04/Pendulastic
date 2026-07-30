"""
imu_calibration_tuner.py
=========================
Shared engine for the IMU adaptive self-tuning calibration loop: replays a
recorded trial's raw sensor log through candidate AHRS/fusion parameter sets,
scores each against the pendulum test's physical constraints, and persists
the winning configuration. Used by both the live per-trial path
(pendulastic_app.py) and the standalone CLI (tune_imu.py).

See docs/superpowers/specs/2026-07-30-imu-adaptive-calibration-design.md.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from pendulastic_imu_server import (
    MadgwickAHRS, _gravity_seed, _qconj, _qmul,
    _FLEX_CAPTURE_THRESHOLD, ROLE_PROXIMAL, ROLE_DISTAL,
)
from pendulastic_pt_score import compute_pt_params
from imu_calibration_config import load_config, save_config

# Matches pendulastic_app.py's _imu_poll_worker 50 ms (~20 Hz) poll cadence —
# EMA's effective smoothing depends on both alpha and the sample interval it's
# applied at, so the replay must resample to this exact grid before applying it.
TICK_S = 0.05

TUNING_GRID = [
    {"beta": beta, "ema_alpha": alpha,
     "flex_axis_capture": fac, "gravity_seed": gs}
    for beta in (0.02, 0.041, 0.08, 0.15)
    for alpha in (0.1, 0.3, 0.5)
    for fac in (True, False)
    for gs in (True, False)
]


class _RoleState:
    """Per-role AHRS + bookkeeping used during a single replay_trial() run."""

    def __init__(self, beta: float):
        self.ahrs = MadgwickAHRS(beta=beta)
        self.accel: Optional[np.ndarray] = None
        self.mag: Optional[np.ndarray] = None
        self.last_ts: Optional[int] = None
        self.seeded = False


def replay_trial(raw_samples: list, params: dict):
    """
    Re-simulate the AHRS + flex-axis + zero-referencing + EMA pipeline over a
    raw trial log, mirroring pendulastic_imu_server.py's on_accel/on_mag/
    on_gyro + swing_angle_deg() + pendulastic_app.py's _imu_poll_worker, but
    parameterized by `params` instead of live global state.

    raw_samples: chronologically-sorted list of
        {"t": float, "role": str, "sensor": str, "v": [x,y,z], "phone_ts_ms": int}
    params: {"beta": float, "ema_alpha": float,
             "flex_axis_capture": bool, "gravity_seed": bool}

    Returns (t_seconds: np.ndarray, angle_deg: np.ndarray) at the same 50 ms
    cadence the live app displays and saves. Returns two empty arrays if the
    log is empty or no motion above the flex-axis threshold is ever detected
    (the trial never "zeroes" and can't be scored).

    Contract: angle_deg[0] is always NaN — the very first tick is always
    emitted before any raw sample has been processed (tick_times[0] always
    equals raw_samples[0]["t"] exactly), so no device state exists yet at
    that instant. This mirrors the live app's own contract: _imu_poll_worker
    (pendulastic_app.py) puts a non-finite angle onto its queue and resets
    the EMA "on NaN (pre-zero or disconnected)" under the same condition —
    a NaN-bearing angle series is normal, pre-existing behavior in this
    codebase, not a defect. Callers (score_waveform, App._run_imu_tuning)
    must finite-filter before reducing (e.g. np.nanmedian, or
    arr[np.isfinite(arr)]) rather than assume every tick is a number.
    """
    if not raw_samples:
        return np.array([]), np.array([])

    beta = params["beta"]
    roles: dict = {}

    def _state(role):
        if role not in roles:
            roles[role] = _RoleState(beta)
        return roles[role]

    def _snapshot():
        return {r: s.ahrs.q.copy() for r, s in roles.items()}

    flex_axis: Optional[np.ndarray] = None
    flex_axis_armed = True
    q_zero: dict = {}
    zero_captured = False

    # Mirrors on_gyro()'s "only the distal segment (or the solo phone)
    # defines the axis" restriction (pendulastic_imu_server.py's on_gyro,
    # is_distal/is_solo). Without this, a proximal-only motion burst in a
    # two-phone trial would incorrectly arm the axis/zero, which live never
    # does. "Solo" here means no distal-role sample ever appears in this log.
    has_distal = any(s["role"] == ROLE_DISTAL for s in raw_samples)

    t0 = raw_samples[0]["t"]
    t_end = raw_samples[-1]["t"]
    n_ticks = max(1, int((t_end - t0) / TICK_S) + 1)
    tick_times = t0 + np.arange(n_ticks) * TICK_S

    # tick_quats[i] = per-role quaternion snapshot as of tick i, taken just
    # before any sample at/after that tick's time is processed — i.e. "as it
    # would have been polled live". Onset-of-motion / q_zero is captured
    # later in this same pass and applied retroactively to every tick
    # (including pre-onset ones) in the second pass below.
    tick_quats: list = []
    next_tick_i = 0

    for samp in raw_samples:
        while next_tick_i < n_ticks and tick_times[next_tick_i] <= samp["t"]:
            tick_quats.append(_snapshot())
            next_tick_i += 1

        role = samp["role"]
        st = _state(role)
        v = np.asarray(samp["v"], dtype=float)
        sensor = samp["sensor"]

        if sensor == "accel":
            st.accel = v
            if not st.seeded:
                if params["gravity_seed"]:
                    st.ahrs.q = _gravity_seed(v)
                st.seeded = True
        elif sensor == "mag":
            st.mag = v
        elif sensor == "gyro":
            ts = samp.get("phone_ts_ms") or 0
            dt = None
            if st.last_ts is not None and ts:
                dt = (ts - st.last_ts) / 1000.0
            if dt is None or not (0.0 < dt < 0.5):
                dt = 0.01
            st.last_ts = ts

            # Onset-of-motion detection runs BEFORE this sample's rotation is
            # integrated below, so q_zero captures the state truly "just
            # before" onset (spec Section 4) rather than one step into it.
            # It always runs regardless of flex_axis_capture — it is only a
            # timing marker for where "zero" is measured; flex_axis_capture
            # separately controls whether the resulting delta is
            # axis-projected further down.
            if flex_axis_armed:
                omega_mag = float(np.linalg.norm(v))
                if omega_mag >= _FLEX_CAPTURE_THRESHOLD:
                    # Only a qualifying role's burst may arm/capture — a
                    # non-qualifying role's motion is ignored entirely
                    # (flex_axis_armed stays True), exactly matching
                    # on_gyro()'s is_distal/is_solo gate.
                    is_distal = (role == ROLE_DISTAL)
                    is_solo = (not has_distal) and (role == ROLE_PROXIMAL)
                    if is_distal or is_solo:
                        if not zero_captured:
                            q_zero = _snapshot()
                            zero_captured = True
                        if params["flex_axis_capture"]:
                            flex_axis = v / omega_mag
                        flex_axis_armed = False

            if st.accel is not None:
                st.ahrs.update(v, st.accel, st.mag, dt)

    while next_tick_i < n_ticks:
        tick_quats.append(_snapshot())
        next_tick_i += 1

    if not zero_captured:
        return np.array([]), np.array([])

    def _swing_from_quats(quats: dict) -> float:
        if (ROLE_PROXIMAL in quats and ROLE_DISTAL in quats
                and ROLE_PROXIMAL in q_zero and ROLE_DISTAL in q_zero):
            q_rel_zero = _qmul(_qconj(q_zero[ROLE_PROXIMAL]), q_zero[ROLE_DISTAL])
            q_rel_cur  = _qmul(_qconj(quats[ROLE_PROXIMAL]), quats[ROLE_DISTAL])
            q_delta = _qmul(_qconj(q_rel_zero), q_rel_cur)
        else:
            solo_role = ROLE_DISTAL if ROLE_DISTAL in quats else (
                ROLE_PROXIMAL if ROLE_PROXIMAL in quats else None)
            if solo_role is None or solo_role not in q_zero:
                return float("nan")
            q_delta = _qmul(_qconj(q_zero[solo_role]), quats[solo_role])

        if params["flex_axis_capture"] and flex_axis is not None:
            q = q_delta if q_delta[0] >= 0.0 else -q_delta
            sin_half = float(np.linalg.norm(q[1:]))
            if sin_half > 1e-9:
                theta = 2.0 * math.acos(min(1.0, float(q[0])))
                u = q[1:] / sin_half
                swing = abs(math.degrees(theta * float(np.dot(u, flex_axis))))
            else:
                swing = 0.0
        else:
            dot = max(-1.0, min(1.0, abs(float(q_delta[0]))))
            swing = 2.0 * math.degrees(math.acos(dot))
        return swing

    t_arr = tick_times - t0
    angle_raw = np.array([180.0 - _swing_from_quats(q) for q in tick_quats])

    alpha = params["ema_alpha"]
    ema = None
    smoothed = np.empty_like(angle_raw)
    for i, a in enumerate(angle_raw):
        if math.isnan(a):
            ema = None
            smoothed[i] = a
        else:
            ema = a if ema is None else alpha * a + (1.0 - alpha) * ema
            smoothed[i] = ema

    return t_arr, smoothed
