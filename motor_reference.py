"""The trajectory the motor commands and the analysis expects.

ONE definition, shared. LabVIEW exports from here and the analysis compares
against here, so a disagreement between them is a measurement difference
rather than a transcription error -- which is the failure mode that has cost
this project the most time.
"""
from __future__ import annotations

import os
import numpy as np

#: Sync preamble. Three pulses, because two cannot distinguish a missed
#: leading edge from a late start and one cannot be checked at all.
PULSE_COUNT = 3
PULSE_DEG = 5.0
PULSE_WIDTH_S = 0.08
PULSE_GAP_S = 0.25
#: Still time at zero AFTER the last pulse, before the lift begins. The pulse
#: search window ends halfway through THIS, so its boundary sits in stillness
#: at zero -- before the lift, whose amplitude would otherwise reinflate the
#: span inside the window and break threshold derivation. Sized so
#: pulse_search_window_s() returns exactly what it did when the window ended
#: halfway through a 1.0 s PRE_ROLL_S, which keeps motor_bench's derived
#: expectations valid.
POST_PULSE_S = 1.0
#: The lift: 0 -> a0 on a minimum-jerk profile, per the spasticity-emulator
#: spec. Velocity AND acceleration are zero at both ends (C2), so an ironless
#: rotor meets no corner in either. Peak rate is 1.875*a0/RAMP_S, which at
#: a0 = 90 over 4 s is 42 deg/s -- comfortably under the phone's 1.0 rad/s
#: (57.3 deg/s) release threshold, so the lift is never read as the release.
RAMP_S = 4.0
#: Still hold AT amplitude before release. Was 1.0, exactly the length of the
#: calm window the phone's zero capture requires, with no margin at all.
PRE_ROLL_S = 3.0
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


def _min_jerk(a0_deg, n):
    """0 -> a0 on the minimum-jerk profile 10u^3 - 15u^4 + 6u^5.

    Zero velocity and zero acceleration at both ends. Peak rate 1.875*a0/T.
    """
    u = np.arange(n, dtype=float) / max(n - 1, 1)
    return a0_deg * (10.0 * u ** 3 - 15.0 * u ** 4 + 6.0 * u ** 5)


def preamble(a0_deg=0.0, fs_hz=1000.0):
    """Pulses at zero, a settle, the lift to a0, then a still hold AT a0.

    Square rather than smooth on purpose: the alignment is taken from the
    edge, and a sharp edge is what both the encoder and the gyro resolve
    identically. The motor is in position control, so the pulse is small
    enough not to need current the drive cannot deliver.
    """
    seg = [np.zeros(int(round(LEAD_S * fs_hz)))]
    for _ in range(PULSE_COUNT):
        seg.append(np.full(int(round(PULSE_WIDTH_S * fs_hz)), PULSE_DEG))
        seg.append(np.zeros(int(round(PULSE_GAP_S * fs_hz))))
    seg.append(np.zeros(int(round(POST_PULSE_S * fs_hz))))
    seg.append(_min_jerk(a0_deg, int(round(RAMP_S * fs_hz))))
    seg.append(np.full(int(round(PRE_ROLL_S * fs_hz)), a0_deg))
    theta = np.concatenate(seg)
    t = np.arange(len(theta), dtype=float) / fs_hz
    return t, theta


def pulse_search_window_s():
    """How long to look for sync pulses: past the last one, short of the swing.

    NOT the preamble's full length. That was tried and it is fs-dependent in a
    way that reproduces the bug it was meant to fix: this returns raw seconds
    while preamble() rounds each segment's sample count per fs, so at rates
    where the roundings do not cancel the real preamble is SHORTER than the
    nominal figure. Measured: exact at 1000/500/200/100 Hz, but -0.006 s at
    250 Hz and -0.012765 s at 104.72 Hz. There the window overran into the
    swing, span reinflated to the swing amplitude, and detection failed with
    "found 1 pulse edges". A phone samples near 100-200 Hz and irregularly, so
    this was going to bite on real data while every 1 kHz test passed.

    Ending halfway through PRE_ROLL_S instead puts the boundary in the middle
    of guaranteed stillness: the last pulse ends at 0.94 s, the swing starts at
    2.19 s, and this returns 1.69 s. Roughly 0.7 s of margin on each side, so
    rounding jitter of a few milliseconds cannot reach either edge at any
    plausible rate -- and it needs no assumption that the samples are evenly
    spaced, which a phone's are not.
    """
    return (LEAD_S
            + PULSE_COUNT * (PULSE_WIDTH_S + PULSE_GAP_S)
            + POST_PULSE_S / 2.0)


def full_trace(a0_deg, f_hz, tau_s, duration_s=12.0, fs_hz=1000.0):
    """Preamble then swing, as one trace. Returns (t, theta_deg, t_release_s)."""
    tp, thp = preamble(a0_deg, fs_hz)
    ts, ths = swing(a0_deg, f_hz, tau_s, duration_s, fs_hz)
    theta = np.concatenate([thp, ths])
    t = np.arange(len(theta), dtype=float) / fs_hz
    return t, theta, float(len(thp) / fs_hz)


#: Amplitudes, dense around MIN_INTERPRETABLE_A0_DEG = 25.0. Finding where
#: extraction breaks relative to that floor is the experiment's main purpose.
SWEEP_A0_DEG = (90.0, 70.0, 50.0, 40.0, 30.0, 28.0, 25.0, 22.0, 20.0, 15.0, 10.0, 5.0)
#: Brackets HEALTHY_REF['f'] = 0.9137 Hz.
SWEEP_F_HZ = (0.6, 0.9, 1.2)
#: Roughly 2, 5 and 9 cycles to 3 deg from 45 -- spastic through healthy.
SWEEP_TAU_S = (0.5, 1.6, 3.2)
NOMINAL_F_HZ = 0.9
NOMINAL_TAU_S = 1.6
#: The three amplitudes at which f and tau are also varied.
CROSS_A0_DEG = (70.0, 40.0, 20.0)
REPEATS = 3
#: A dedicated repeatability block: one condition, many consecutive repeats.
#: The 3 repeats per condition above estimate spread pooled across conditions,
#: which confounds instrument noise with condition effects. The rig exists to
#: separate those, and that needs many observations of ONE known trajectory:
#: 20 gives a usable SEM and MDC95 for the instrument itself, against which
#: the 29.0 deg A0 MDC95 measured on participants can finally be read.
REPEATABILITY_REPEATS = 20
REPEATABILITY_A0_DEG = 50.0


def release_offset_s():
    """Seconds from the FIRST sync pulse edge to release.

    Exported so downstream alignment does not hold a second copy of the
    preamble layout. `motor_bench.align()` re-derived this from the constants
    and silently went wrong the moment a ramp was inserted between the pulses
    and the release; anything that needs the offset must call this instead.
    """
    return (PULSE_COUNT * (PULSE_WIDTH_S + PULSE_GAP_S)
            + POST_PULSE_S + RAMP_S + PRE_ROLL_S)


def sweep_matrix():
    """Every run of the bench session, in the order to run them.

    The repeatability block runs FIRST, while the bench is coldest and the
    operator is freshest -- it is the run that must not be compromised, and
    putting it last would let session drift masquerade as instrument noise.
    Then the full amplitude sweep at nominal f and tau, then f and tau varied
    at three amplitudes. Repeats are consecutive so a drifting bench shows up as
    within-run spread rather than as a trend across the whole session.
    """
    combos = [(a0, NOMINAL_F_HZ, NOMINAL_TAU_S) for a0 in SWEEP_A0_DEG]
    for a0 in CROSS_A0_DEG:
        for f in SWEEP_F_HZ:
            if f != NOMINAL_F_HZ:
                combos.append((a0, f, NOMINAL_TAU_S))
        for tau in SWEEP_TAU_S:
            if tau != NOMINAL_TAU_S:
                combos.append((a0, NOMINAL_F_HZ, tau))

    runs = []
    for rep in range(1, REPEATABILITY_REPEATS + 1):
        runs.append({
            "run_id": f"rep_a{REPEATABILITY_A0_DEG:g}_r{rep}",
            "a0_deg": REPEATABILITY_A0_DEG, "f_hz": NOMINAL_F_HZ,
            "tau_s": NOMINAL_TAU_S, "repeat": rep, "block": "repeatability",
        })
    for a0, f, tau in combos:
        for rep in range(1, REPEATS + 1):
            runs.append({
                "run_id": f"a{a0:g}_f{f:g}_t{tau:g}_r{rep}",
                "a0_deg": a0, "f_hz": f, "tau_s": tau, "repeat": rep,
                "block": "sweep",
            })
    return runs


def run_duration_s(run):
    """How long this run's swing must last to be measurable.

    Long enough for the tail to settle (5 tau) plus two full periods to fit a
    drift slope across, with margin. A 12 s trial at tau=3.2 is only 3.75 time
    constants: the "settled" tail still carries live oscillation, and fitting
    it reports half a degree per second of drift that is not there.
    """
    return max(12.0, 5.0 * float(run["tau_s"]) + 4.0 / float(run["f_hz"]) + 2.0)


def export_trace(run, out_dir, fs_hz=1000.0, duration_s=None):
    """Write one run's commanded trace as CSV for LabVIEW to stream."""
    if duration_s is None:
        duration_s = run_duration_s(run)
    os.makedirs(out_dir, exist_ok=True)
    t, theta, _rel = full_trace(run["a0_deg"], run["f_hz"], run["tau_s"],
                                duration_s=duration_s, fs_hz=fs_hz)
    path = os.path.join(out_dir, f"{run['run_id']}.csv")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("t_s,theta_deg\n")
        for a, b in zip(t, theta):
            fh.write(f"{a:.6f},{b:.6f}\n")
    return path
