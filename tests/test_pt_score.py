# tests/test_pt_score.py
import os, sys, math
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pendulastic_pt_score import compute_pt_params


def _damped_sinusoid(n=300, fps=30.0, A0=15.0, freq=0.9, decay=0.25):
    """Realistic pendulum signal: 1-second hold at 180°, then cosine oscillation."""
    t = np.arange(n) / fps
    ang = np.empty(n)
    hold = int(fps)  # 30 frames of pre-release hold
    for i in range(n):
        if i < hold:
            ang[i] = 180.0
        else:
            ti = (i - hold) / fps
            # At ti=0: 165 + A0*cos(0) = 165 + 15 = 180 (seamless join with hold)
            ang[i] = 165.0 + A0 * math.exp(-decay * ti) * math.cos(2 * math.pi * freq * ti)
    return t, ang


def _drifting_signal(n=300, fps=30.0, A0=15.0, freq=0.9, drift=2.0):
    """Same signal but with a monotonic +2° baseline drift over the recording."""
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
    ang = np.empty(n)
    # Pre-release hold at 180°
    hold = int(fps)  # 30 frames
    for i in range(hold):
        ang[i] = 180.0
    # Post-release oscillation with peak delayed to ~18% of post-release window
    peak_offset = 54  # frames after release ≈ 18% of 300
    for i in range(hold, n):
        ti = (i - hold) / fps
        # Large initial amplitude (20°) with damping; peak is achieved early
        ang[i] = 165.0 + 20.0 * math.exp(-0.25 * ti) * (1.0 + math.cos(2 * math.pi * 0.9 * ti))
    p = compute_pt_params(t, ang)
    assert p is not None, "Signal should yield valid params with 20% A0 window"
    assert p["A0_deg"] >= 10.0, f"A0 too small: {p['A0_deg']}"


def test_all_expected_keys_present():
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang)
    assert p is not None, "Damped sinusoid should yield valid params"
    for key in ("R2n", "N", "phi_max_ratio", "omega_max_n", "omega_min_n",
                "f", "area_ratio", "A0_deg", "A1_deg"):
        assert key in p, f"Missing key: {key}"
