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
