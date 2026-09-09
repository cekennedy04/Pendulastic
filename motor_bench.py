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


def find_pulse_edges(t, signal, expected=mr.PULSE_COUNT, search_s=None):
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

    # Bound the search to the preamble BEFORE measuring anything.
    #
    # Without this the thresholds are derived from the whole trace, whose
    # largest excursion is the swing, not the pulse. On a real run that means
    # span = 45 deg, hi = 27 deg, and the 5 deg pulses never cross it: the
    # function found ONE edge (the swing) on every full trial and refused.
    # Isolated-preamble tests could not see it, because there the swing is
    # absent and span IS the pulse height.
    #
    # The window comes from the shared constants rather than from a magic
    # number, and baseline and span are computed INSIDE it -- which keeps the
    # detection scale-free, so it works on the encoder's degrees and on the
    # phone's reconstructed angle without assuming the two agree. Assuming they
    # agree is precisely what this harness exists to test.
    if search_s is None:
        search_s = mr.preamble_duration_s()
    # Strict "<": the preamble and the swing are adjacent with no gap, so the
    # boundary instant belongs to the swing (full commanded amplitude at
    # t_release), not the preamble. On a full trace built from the same
    # constants that produced search_s, that boundary sample can land exactly
    # on t[0] + search_s in floating point -- an inclusive test would then
    # pull one 45 deg sample into the window, inflate span, and reproduce the
    # exact defect this window exists to fix.
    win = t < (t[0] + search_s)
    if win.sum() < 10:
        raise SyncError(
            f"only {int(win.sum())} samples inside the {search_s:.2f} s preamble "
            f"window; expected {expected} pulses")
    tw, xw = t[win], x[win]

    baseline = float(np.median(xw[: max(1, int(len(xw) * 0.05))]))
    span = float(np.max(xw) - baseline)
    if span <= 0:
        raise SyncError(f"signal never rises; expected {expected} pulses")
    hi = baseline + 0.6 * span
    lo = baseline + 0.4 * span

    # armed starts False when the trace is ALREADY high at its first sample:
    # that is not a rising edge, it is a trace that began mid-pulse, and
    # recording t[0] as an edge would return the right COUNT with one bogus
    # member -- the one failure mode here that fails open instead of closed.
    edges, armed = [], not (xw[0] >= hi)
    for i in range(len(xw)):
        if armed and xw[i] >= hi:
            edges.append(float(tw[i]))
            armed = False
        elif not armed and xw[i] <= lo:
            armed = True
    if len(edges) != expected:
        raise SyncError(f"found {len(edges)} pulse edges, expected {expected}")
    return edges
