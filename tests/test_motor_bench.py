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
