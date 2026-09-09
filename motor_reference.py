"""The trajectory the motor commands and the analysis expects.

ONE definition, shared. LabVIEW exports from here and the analysis compares
against here, so a disagreement between them is a measurement difference
rather than a transcription error -- which is the failure mode that has cost
this project the most time.
"""
from __future__ import annotations

import numpy as np

#: Sync preamble. Three pulses, because two cannot distinguish a missed
#: leading edge from a late start and one cannot be checked at all.
PULSE_COUNT = 3
PULSE_DEG = 5.0
PULSE_WIDTH_S = 0.08
PULSE_GAP_S = 0.25
PRE_ROLL_S = 1.0
#: Still time BEFORE the first pulse. Without it the trace begins at full
#: pulse height, so pulse one has no rising edge to detect -- an edge counter
#: sees two pulses, not three. It is also what the motor physically does.
LEAD_S = 0.2


def swing(a0_deg, f_hz, tau_s, duration_s=12.0, fs_hz=1000.0):
    """A decaying cosine: theta(t) = a0 * exp(-t/tau) * cos(2*pi*f*t).

    Returns (t_s, theta_deg) with t starting at 0.
    """
    n = int(round(duration_s * fs_hz))
    t = np.arange(n, dtype=float) / fs_hz
    theta = a0_deg * np.exp(-t / tau_s) * np.cos(2.0 * np.pi * f_hz * t)
    return t, theta


def preamble(fs_hz=1000.0):
    """PULSE_COUNT square pulses, then a still pre-roll.

    Square rather than smooth on purpose: the alignment is taken from the
    edge, and a sharp edge is what both the encoder and the gyro resolve
    identically. The motor is in position control, so the pulse is small
    enough not to need current the drive cannot deliver.
    """
    seg = [np.zeros(int(round(LEAD_S * fs_hz)))]
    for _ in range(PULSE_COUNT):
        seg.append(np.full(int(round(PULSE_WIDTH_S * fs_hz)), PULSE_DEG))
        seg.append(np.zeros(int(round(PULSE_GAP_S * fs_hz))))
    seg.append(np.zeros(int(round(PRE_ROLL_S * fs_hz))))
    theta = np.concatenate(seg)
    t = np.arange(len(theta), dtype=float) / fs_hz
    return t, theta


def full_trace(a0_deg, f_hz, tau_s, duration_s=12.0, fs_hz=1000.0):
    """Preamble then swing, as one trace. Returns (t, theta_deg, t_release_s)."""
    tp, thp = preamble(fs_hz)
    ts, ths = swing(a0_deg, f_hz, tau_s, duration_s, fs_hz)
    theta = np.concatenate([thp, ths])
    t = np.arange(len(theta), dtype=float) / fs_hz
    return t, theta, float(len(thp) / fs_hz)
