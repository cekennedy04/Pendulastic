"""
imu_absolute_vs_knee.py
=======================
Measurement-scope diagnostics for single-sensor IMU trials.

Why this exists
---------------
The IMU pipeline reported knee swing amplitudes averaging 1.55x the OptiTrack
reference, and the standing assumption was that the IMU processing was at
fault. It is not. Measured across 93 paired trials on 2026-08-28:

    pipeline / gyro-integral      1.09   <- all the pipeline itself contributes
    gyro-integral / OptiTrack     1.41
    pipeline / OptiTrack          1.55

and, on the 89 trials with a usable static hold AND settle window, two
INDEPENDENT measures of the same segment rotation agree to within 3%:

    accel (gravity-direction change, no integration, no axis assumption)
    gyro  (integrated rate about the principal axis)      accel/gyro = 0.976

    accel / OptiTrack = 1.255      gyro / OptiTrack = 1.294

An integration-free measurement and an integrated one cannot agree that
closely while both being wrong in the same direction. The IMU measures its own
segment correctly.

What it measures instead is the problem. Every trial in the corpus is
SINGLE-SENSOR: 93/93 carry no ROLE_DISTAL sample, so the pipeline takes the
lone phone's ABSOLUTE rotation in space as the knee angle, which is only valid
while the other segment is stationary. It is not. Measured from the same
labeled-marker plates the reference angle is built from:

    thigh plate sweep   median 16.7 deg   (89/93 trials above 10 deg)
    shank plate sweep   median 70.8 deg

70.8 - 16.7 = 54.1, i.e. an absolute-to-relative ratio of 1.31 against a
measured 1.255-1.294. The magnitude matches.

Honest limit on that last step
------------------------------
It matches in AGGREGATE only. Per trial the predicted and measured ratios do
not correlate (Spearman rho = -0.037, p = 0.73, n = 87). Kabsch sweep angles
are magnitudes of 3-D rotations, so subtracting them is only valid when the two
segments move in a common plane, which is not guaranteed per trial. Thigh
motion is therefore established as A cause of the over-read, at roughly the
right size, but not as a per-trial predictor of it -- which is precisely why
this module reports the quantities and does NOT apply a correction factor.
Calibrating a gain against a model that fails per-trial would bake the error in.

Nothing here feeds the scored pipeline. These are diagnostics for a researcher
to read, in the same spirit as workbench_engine.compute_raw_sensor_diagnostics.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

# Minimum |omega| (rad/s) for a sample to count as deliberate motion. Matches
# pendulastic_imu_server._FLEX_CAPTURE_THRESHOLD so the motion window this
# module derives is the same one the capture path would recognise.
MOTION_THRESHOLD_RAD_S = 1.0

# Seconds of samples averaged at each end to represent the pre-release hold and
# the post-swing settle.
DEFAULT_WINDOW_S = 0.5

# A window is only "static" if its mean acceleration magnitude sits this close
# to the trial's own gravity magnitude. Expressed as a FRACTION, not an
# absolute, because these feeds arrive in g (|a| ~= 1.0), not m/s^2 -- assuming
# m/s^2 here silently rejected every trial in the corpus.
STATIC_TOLERANCE_FRAC = 0.15


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def motion_window(t: Sequence[float], gyro: Sequence[Sequence[float]],
                  threshold: float = MOTION_THRESHOLD_RAD_S):
    """(t_start, t_end) spanning the samples whose |omega| clears `threshold`.

    Returns None when the trial never moves -- there is no swing to measure,
    which is a different thing from a swing measured as zero.
    """
    t = np.asarray(t, dtype=float)
    g = np.asarray(gyro, dtype=float)
    if g.ndim != 2 or g.shape[1] != 3 or len(t) != len(g) or not len(t):
        return None
    moving = np.where(np.linalg.norm(g, axis=1) >= threshold)[0]
    if len(moving) < 2:
        return None
    return float(t[moving[0]]), float(t[moving[-1]])


def net_rotation_from_gravity(t, accel, t_start: float, t_end: float,
                              window_s: float = DEFAULT_WINDOW_S) -> Optional[float]:
    """Degrees the sensor rotated between the hold and the settle, from gravity.

    While the segment is still, the accelerometer reads gravity alone, so the
    angle between the gravity direction before the swing and after it IS the
    rotation the segment underwent. No integration, so no drift; no axis, so no
    projection error. Rotation about the gravity vector itself is invisible to
    this, which for a sagittal swing about a horizontal axis costs nothing.

    Returns None when either window is missing or is not actually static.
    """
    t = np.asarray(t, dtype=float)
    a = np.asarray(accel, dtype=float)
    if a.ndim != 2 or a.shape[1] != 3 or len(t) != len(a) or not len(t):
        return None
    hold = a[(t >= t_start - window_s) & (t < t_start)]
    settle = a[(t > t_end) & (t <= t_end + window_s)]
    if len(hold) < 3 or len(settle) < 3:
        return None

    g0 = float(np.median(np.linalg.norm(a, axis=1)))
    if g0 <= 1e-9:
        return None
    for w in (hold, settle):
        mean_mag = float(np.linalg.norm(w.mean(axis=0)))
        if abs(mean_mag - g0) / g0 > STATIC_TOLERANCE_FRAC:
            return None       # moving, so the reading is not gravity alone

    cos = float(np.dot(_unit(hold.mean(axis=0)), _unit(settle.mean(axis=0))))
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def net_rotation_from_gyro(t, gyro, axis, t_start: float,
                           t_end: float) -> Optional[float]:
    """Degrees swept about `axis`, by integrating the projected rate.

    The companion to net_rotation_from_gravity over the same window: an
    integrated measure against an integration-free one. Projection onto a unit
    axis can only ever REDUCE the recovered angle (|omega . u| <= |omega|), so
    a wrong axis makes this an under-estimate, never an over-estimate.
    """
    t = np.asarray(t, dtype=float)
    g = np.asarray(gyro, dtype=float)
    u = _unit(np.asarray(axis, dtype=float))
    if g.ndim != 2 or g.shape[1] != 3 or len(t) != len(g) or u.shape != (3,):
        return None
    sel = (t >= t_start) & (t <= t_end)
    if int(sel.sum()) < 3:
        return None
    return abs(float(np.degrees(np.trapezoid(g[sel] @ u, t[sel]))))


def plate_sweep(points, tracked, t, window_s: float = DEFAULT_WINDOW_S) -> Optional[float]:
    """Degrees a rigid marker plate rotated between the hold and the settle.

    `points` is (n_frames, n_markers, 3); `tracked` is a per-frame bool saying
    every marker of the cluster was seen. A Kabsch fit of the plate's hold shape
    onto its settle shape gives the rotation, which is read out of the trace.
    This deliberately uses only frames the cameras actually saw -- an untracked
    frame is skipped, never interpolated.
    """
    pts = np.asarray(points, dtype=float)
    ok = np.asarray(tracked, dtype=bool)
    t = np.asarray(t, dtype=float)
    if pts.ndim != 3 or pts.shape[2] != 3 or pts.shape[1] < 3:
        return None
    if len(ok) != len(pts) or len(t) != len(pts):
        return None
    idx = np.where(ok)[0]
    if len(idx) < 6:
        return None
    hold = idx[t[idx] <= t[idx[0]] + window_s]
    settle = idx[t[idx] >= t[idx[-1]] - window_s]
    if len(hold) < 1 or len(settle) < 1:
        return None

    a = pts[hold].mean(axis=0)
    b = pts[settle].mean(axis=0)
    a = a - a.mean(axis=0)
    b = b - b.mean(axis=0)
    h = a.T @ b
    try:
        u_, _s, vt = np.linalg.svd(h)
    except np.linalg.LinAlgError:
        return None
    # Reflection guard: without it a degenerate cluster can produce an
    # improper rotation whose trace is meaningless as an angle.
    d = np.sign(np.linalg.det(vt.T @ u_.T))
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u_.T
    cos = (float(np.trace(r)) - 1.0) / 2.0
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def is_single_sensor(samples: Sequence[dict], distal_role: str = "distal") -> bool:
    """True when no sample carries the distal role.

    On a single-sensor trial the pipeline reports one segment's ABSOLUTE
    rotation and calls it the knee angle, which holds only while the other
    segment is stationary. In this corpus the thigh sweeps a median 16.7 deg,
    so it does not hold, and the reported amplitude runs high.
    """
    return not any(s.get("role") == distal_role for s in samples)
