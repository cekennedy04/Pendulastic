"""Join a motor encoder log to a phone trial and score the phone against truth.

The encoder is truth. Every comparison here is `imu - truth`, and alignment
comes from sync-pulse edges only -- never cross-correlation, which on a
near-periodic signal aliases onto a wrong-by-nearly-one-cycle lag.
"""
from __future__ import annotations

import numpy as np

import motor_reference as mr


class SyncError(ValueError):
    """Alignment could not be established. Never downgraded to a warning: an
    unaligned trial produces numbers that look fine and are not."""


def find_pulse_edges(t, signal, expected=mr.PULSE_COUNT):
    """Rising-edge times of the sync pulses, in seconds.

    Threshold at half the pulse height above the pre-pulse baseline, with
    hysteresis, so noise near the threshold cannot produce doubled edges.
    """
    t = np.asarray(t, dtype=float)
    x = np.asarray(signal, dtype=float)
    ok = np.isfinite(t) & np.isfinite(x)
    t, x = t[ok], x[ok]
    if len(t) < 10:
        raise SyncError(f"only {len(t)} finite samples; expected {expected} pulses")

    baseline = float(np.median(x[: max(1, len(x) // 50)]))
    span = float(np.max(x) - baseline)
    if span <= 0:
        raise SyncError(f"signal never rises; expected {expected} pulses")
    hi = baseline + 0.6 * span
    lo = baseline + 0.4 * span

    edges, armed = [], True
    for i in range(len(x)):
        if armed and x[i] >= hi:
            edges.append(float(t[i]))
            armed = False
        elif not armed and x[i] <= lo:
            armed = True
    if len(edges) != expected:
        raise SyncError(f"found {len(edges)} pulse edges, expected {expected}")
    return edges
