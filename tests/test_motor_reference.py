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
