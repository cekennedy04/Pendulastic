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
        search_s = mr.pulse_search_window_s()
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


def align(enc_t, enc_theta, imu_t, imu_theta):
    """Put both traces on the encoder's clock using the pulse edges.

    Lag is the MEDIAN of the per-pulse offsets rather than the first: three
    edges give a spread, and a spread is the only evidence available that the
    two clocks are running at the same rate. A single edge cannot show that.
    """
    e_edges = find_pulse_edges(enc_t, enc_theta)
    i_edges = find_pulse_edges(imu_t, imu_theta)
    offsets = [i - e for i, e in zip(i_edges, e_edges)]
    lag = float(np.median(offsets))
    spread = float(np.max(offsets) - np.min(offsets))

    # Rate error, reported rather than merely bounded.
    #
    # KNOW WHAT THIS CAN AND CANNOT SEE. The pulses span 0.66 s, so a clock
    # running fast by r shows up here as spread r*0.66. The 50 ms bound below
    # therefore only catches r > 7.6%. Measured: a clock 2% fast passes, having
    # drifted 240 ms by the end of a 12 s trial. Three edges over two thirds of
    # a second are barely evidence about RATE at all.
    #
    # That is tolerable because a phone clock is crystal-based at 50-100 ppm,
    # which drifts 0.6-1.2 ms across a trial -- negligible against everything
    # else measured here -- and the realistic threat is a fixed OFFSET, which
    # this handles exactly. But the number is reported so a clock that does
    # misbehave is visible instead of silently passing. If it ever matters, the
    # fix is a second pulse burst at the END of the trial: a 12 s lever arm
    # makes the same three-edge test roughly 18x more sensitive.
    lever = float(e_edges[-1] - e_edges[0])
    rate_error = (spread / lever) if lever > 0 else float("nan")

    if spread > 0.05:
        raise SyncError(
            f"pulse offsets disagree by {spread:.3f} s over a {lever:.2f} s "
            f"lever arm ({100 * rate_error:.2f}% rate error); the two clocks "
            "are not running at the same rate")

    # Release is one preamble after the encoder's first edge.
    t_release = e_edges[0] + (mr.PULSE_COUNT * (mr.PULSE_WIDTH_S + mr.PULSE_GAP_S)
                              + mr.PRE_ROLL_S)
    return {
        "lag_s": lag,
        "pulse_offset_spread_s": spread,
        "clock_rate_error": rate_error,
        "enc_t": np.asarray(enc_t, dtype=float),
        "enc_theta": np.asarray(enc_theta, dtype=float),
        "imu_t": np.asarray(imu_t, dtype=float) - lag,
        "imu_theta": np.asarray(imu_theta, dtype=float),
        "t_release_s": float(t_release),
    }
