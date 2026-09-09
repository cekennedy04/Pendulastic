# tests/test_motor_bench.py
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_bench as mb
import motor_reference as mr


def test_the_three_pulse_edges_are_found_in_a_clean_preamble():
    t, th = mr.preamble(fs_hz=1000.0)
    edges = mb.find_pulse_edges(t, th)
    assert len(edges) == 3
    assert edges[0] == pytest.approx(mr.LEAD_S, abs=0.005)
    gap = mr.PULSE_WIDTH_S + mr.PULSE_GAP_S
    assert edges[1] - edges[0] == pytest.approx(gap, abs=0.01)


def test_the_edges_survive_noise():
    """The gyro sees these through sensor noise, not as a clean square."""
    rng = np.random.default_rng(0)
    t, th = mr.preamble(fs_hz=1000.0)
    noisy = th + rng.normal(0.0, 0.3, size=len(th))
    assert len(mb.find_pulse_edges(t, noisy)) == 3


def test_too_few_pulses_raises_rather_than_guessing():
    """A missed pulse means the alignment is unknown. Guessing it is how a
    whole session becomes silently wrong.

    The pulse to erase is derived from the constants, not from a sample index:
    LEAD_S puts all three pulses inside the first 0.94 s of a 2.19 s preamble,
    so anything that zeroes "the back half" erases only stillness and the test
    would pass without ever exercising the refusal.
    """
    fs = 1000.0
    t, th = mr.preamble(fs_hz=fs)
    maimed = th.copy()
    start = int(mr.LEAD_S * fs)
    maimed[start:start + int(mr.PULSE_WIDTH_S * fs)] = 0.0
    assert (maimed > mr.PULSE_DEG / 2).sum() < (th > mr.PULSE_DEG / 2).sum(), (
        "the test must actually remove a pulse, or it proves nothing")
    with pytest.raises(mb.SyncError, match="expected 3"):
        mb.find_pulse_edges(t, maimed)


def test_edges_are_found_on_a_full_trial_not_just_an_isolated_preamble():
    """The input align() actually passes. Deriving thresholds from the whole
    trace scales them to the 45 deg swing, so hi lands at 27 deg, the 5 deg
    pulses never cross it, and the function finds ONE edge and refuses every
    real trial. Isolated-preamble tests cannot see this: with no swing present,
    span IS the pulse height."""
    t, th, _rel = mr.full_trace(45.0, 0.9, 1.6, duration_s=12.0, fs_hz=1000.0)
    assert len(mb.find_pulse_edges(t, th)) == 3


def test_a_trace_that_starts_mid_pulse_is_refused_not_counted_from_zero():
    """The one failure mode here that fails OPEN: recording t[0] as an edge
    returns the right COUNT with one bogus member, which no downstream check
    can catch."""
    t, th = mr.preamble(fs_hz=1000.0)
    cut = int(mr.LEAD_S * 1000.0) + 10          # begins inside pulse 1
    with pytest.raises(mb.SyncError):
        mb.find_pulse_edges(t[cut:] - t[cut], th[cut:])
