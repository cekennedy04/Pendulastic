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


def test_a_trace_that_starts_already_high_does_not_count_a_bogus_first_edge():
    """The one failure mode here that fails OPEN: recording t[0] as an edge
    returns the right COUNT with one bogus member, which no downstream check
    can catch.

    Constructed so it reaches the armed-flag guard. An earlier version simply
    cut into pulse 1, which made the first 5% of the window all pulse-height,
    so baseline == max, span == 0, and it exited through "signal never rises"
    -- a different path entirely. It passed while proving nothing.
    """
    fs = 1000.0
    _t, th = mr.preamble(fs_hz=fs)
    lead = np.full(30, mr.PULSE_DEG)            # starts high, but only briefly
    sig = np.concatenate([lead, th])
    t = np.arange(len(sig)) / fs
    # Baseline must still resolve low, or this exercises the wrong branch.
    win = t <= mr.pulse_search_window_s()
    assert np.median(sig[win][: int(win.sum() * 0.05)]) == 0.0, (
        "test setup is wrong: baseline is not low, so the armed guard is not reached")
    assert len(mb.find_pulse_edges(t, sig)) == 3, (
        "the leading high block must not be counted as a fourth edge")


@pytest.mark.parametrize("fs", [1000.0, 500.0, 250.0, 200.0, 104.72, 100.0, 60.0])
def test_detection_survives_sample_rates_that_are_not_1_khz(fs):
    """A phone samples near 100-200 Hz and irregularly, not at 1 kHz.

    Deriving the search window from the preamble's nominal length failed here:
    preamble() rounds each segment per fs, so the real preamble runs short at
    250 Hz (-0.006 s) and 104.72 Hz (-0.012765 s), the window overran into the
    swing, and detection failed with "found 1 pulse edges".
    """
    t, th, _rel = mr.full_trace(90.0, 0.9, 1.6, duration_s=12.0, fs_hz=fs)
    assert len(mb.find_pulse_edges(t, th)) == 3


def _rig_pair(lag_s=0.37, a0=45.0, fs=1000.0):
    """An encoder trace and a phone trace of the same motion, offset in time."""
    t, th, rel = mr.full_trace(a0_deg=a0, f_hz=0.9, tau_s=1.6,
                               duration_s=8.0, fs_hz=fs)
    return t, th, t + lag_s, th.copy(), rel


def test_alignment_recovers_a_known_clock_offset():
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.37)
    out = mb.align(et, eth, it, ith)
    assert out["lag_s"] == pytest.approx(0.37, abs=0.01)


def test_alignment_puts_the_two_traces_on_one_clock():
    et, eth, it, ith, _rel = _rig_pair(lag_s=-0.22)
    out = mb.align(et, eth, it, ith)
    n = min(len(out["enc_theta"]), len(out["imu_theta"]))
    diff = out["imu_theta"][:n] - out["enc_theta"][:n]
    assert np.nanmax(np.abs(diff)) < 0.5, "aligned traces should nearly coincide"


def test_the_clock_rate_error_is_reported_even_when_it_passes():
    """The spread bound only catches a 7.6% rate error over this lever arm, so
    a 2% clock -- 240 ms of drift across a 12 s trial -- passes. That is
    acceptable for a crystal-based phone clock, but the number must be VISIBLE
    rather than merely under a threshold, or nobody can tell a good sync from a
    barely-passing one."""
    et, eth, it, ith, _rel = _rig_pair(lag_s=0.0)
    out = mb.align(et, eth, it * 1.02 + 0.3, ith)
    assert out["clock_rate_error"] == pytest.approx(0.02, abs=0.005)
    assert out["pulse_offset_spread_s"] > 0.0


def test_release_time_is_reported_on_the_common_clock():
    et, eth, it, ith, rel = _rig_pair(lag_s=0.1)
    out = mb.align(et, eth, it, ith)
    assert out["t_release_s"] == pytest.approx(rel, abs=0.02)
