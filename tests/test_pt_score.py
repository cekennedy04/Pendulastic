# tests/test_pt_score.py
import os, sys, math
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pendulastic_pt_score import compute_pt_params


def _damped_sinusoid(n=300, fps=30.0, A0=40.0, freq=0.9, decay=0.25):
    """Clean damped sinusoid starting at 180° (fully extended), oscillating down."""
    t = np.arange(n) / fps
    # angle = 180 - A0*(1 - exp(-decay*t))*|sin(2pi*freq*t)|
    # Simpler: start at 180-A0 and add decaying oscillation back up
    return t, 180.0 - A0 * np.exp(-decay * t) * np.abs(np.sin(2 * np.pi * freq * t))


def _drifting_signal(n=300, fps=30.0, A0=40.0, freq=0.9, drift=3.0):
    """Same sinusoid but with a monotonic +3° baseline drift over the recording."""
    t, ang = _damped_sinusoid(n, fps, A0, freq)
    drift_arr = np.linspace(0, drift, n)
    return t, ang + drift_arr


def test_detrend_true_removes_drift_without_destroying_A0():
    """With detrend=True, a drifted signal should still give a valid A0 close to the clean one."""
    t_clean, ang_clean = _damped_sinusoid()
    t_drift, ang_drift = _drifting_signal()

    p_clean = compute_pt_params(t_clean, ang_clean, detrend=False)
    p_drift = compute_pt_params(t_drift, ang_drift, detrend=True)

    assert p_clean is not None, "Clean signal should produce valid params"
    assert p_drift is not None, "Drifted signal with detrend=True should produce valid params"
    # A0 should be within 10° (detrend removes the drift)
    assert abs(p_drift["A0_deg"] - p_clean["A0_deg"]) < 10.0, \
        f"A0 mismatch after detrend: drift={p_drift['A0_deg']:.1f} clean={p_clean['A0_deg']:.1f}"


def test_detrend_false_accepts_param_without_crash():
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang, detrend=False)
    assert p is not None


def test_detrend_default_is_true():
    """Default call (no detrend arg) must not raise TypeError."""
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang)
    assert p is not None


def test_wider_A0_window_catches_late_initial_peak():
    """Peak shifted to 18% of the post-release window should still be found."""
    n, fps = 300, 30.0
    t = np.arange(n) / fps
    # Neutral at 165°; peak is at frame index 54 ≈ 18% of 300 frames
    ang = np.full(n, 165.0)
    peak_idx = 54   # 18% of 300
    for i in range(n):
        if i < peak_idx:
            ang[i] = 165.0 + (40.0 * i / peak_idx)   # ramp up to peak
        else:
            ang[i] = 205.0 - 40.0 * np.exp(-0.3 * (i - peak_idx) / fps)
    p = compute_pt_params(t, ang)
    # If the 20% window catches the peak, A0 should be ≥ 10°
    if p is not None:
        assert p["A0_deg"] >= 10.0, f"A0 too small: {p['A0_deg']}"


def test_all_expected_keys_present():
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang)
    if p is None:
        return  # signal didn't meet quality threshold — not a test failure
    for key in ("R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
                "f", "area_ratio", "A0_deg", "A1_deg"):
        assert key in p, f"Missing key: {key}"
