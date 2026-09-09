# tests/test_motor_reference.py
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_reference as mr


def test_the_swing_starts_at_its_commanded_amplitude():
    t, th = mr.swing(a0_deg=45.0, f_hz=0.9, tau_s=1.6, duration_s=10.0, fs_hz=1000.0)
    assert th[0] == pytest.approx(45.0, abs=1e-9)
    assert len(t) == len(th)
    assert t[0] == 0.0


def test_the_swing_decays_toward_zero():
    _t, th = mr.swing(a0_deg=45.0, f_hz=0.9, tau_s=1.6, duration_s=10.0, fs_hz=1000.0)
    assert abs(th[-1]) < 1.0, "a 10 s trace at tau=1.6 should have settled"
    assert np.max(np.abs(th[:1000])) > np.max(np.abs(th[-1000:]))


def test_the_interior_waveform_matches_the_closed_form():
    """The endpoint tests would pass against a stub that is a0 at index 0 and
    zero after. This pins the actual shape: every sample against the formula
    the docstring claims, the zero-crossing spacing that fixes f, and the
    decay rate that the loose abs(th[-1]) < 1.0 bound does not."""
    a0, f, tau, fs = 45.0, 0.9, 1.6, 1000.0
    t, th = mr.swing(a0_deg=a0, f_hz=f, tau_s=tau, duration_s=6.0, fs_hz=fs)
    expected = a0 * np.exp(-t / tau) * np.cos(2.0 * np.pi * f * t)
    assert np.allclose(th, expected, atol=1e-9)

    sign_changes = np.flatnonzero(np.diff(np.signbit(th)))
    assert len(sign_changes) >= 6, "a 6 s trace at 0.9 Hz must cross zero repeatedly"
    half_period_s = np.median(np.diff(sign_changes)) / fs
    assert half_period_s == pytest.approx(1.0 / (2.0 * f), rel=0.02)

    assert abs(th[int(tau * fs)]) == pytest.approx(
        a0 * np.exp(-1.0) * abs(np.cos(2.0 * np.pi * f * tau)), rel=0.02)
