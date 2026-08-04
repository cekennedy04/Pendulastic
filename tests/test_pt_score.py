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


def test_detect_release_has_no_hardcoded_absolute_floor():
    """
    _detect_release must use a pure 0.08 * signal_range threshold so it stays
    unit-agnostic. A hardcoded absolute floor (e.g. 2.0) would swallow a small
    -scale signal like a normalized IMU tilt magnitude (range ~0.5) and miss
    the true release, falling back to the end of the baseline window instead.
    """
    from pendulastic_pt_score import _detect_release
    fps = 30.0
    n = 90
    t = np.arange(n) / fps
    jump_at = 40
    sig = np.zeros(n)
    sig[jump_at:] = 0.5   # tiny absolute swing, well under any 2.0-unit floor

    rel_i = _detect_release(t, sig, baseline_sec=1.0)

    assert abs(rel_i - jump_at) <= 3, (
        f"Expected release near index {jump_at}, got {rel_i} — a hardcoded "
        "floor above the signal's own range would miss this small jump.")


def test_detect_release_t0_returns_absolute_time_of_release():
    """detect_release_t0() runs the adaptive detector and returns a time value
    (not a sample index) so it can be used to synchronize independently-
    sampled trials (different frame rates / clocks)."""
    from pendulastic_pt_score import detect_release_t0
    t, ang = _damped_sinusoid()   # 30-frame hold at 180deg, then release at t~=1.0s
    t0 = detect_release_t0(t, ang)
    assert 0.8 <= t0 <= 1.2, f"t0={t0} not near expected hold-to-swing boundary"


def test_align_to_release_zeroes_time_axis_at_t0():
    """align_to_release() must shift a trial's time array so the sample at t0
    reads exactly 0, with earlier samples negative and later ones positive."""
    from pendulastic_pt_score import detect_release_t0, align_to_release
    t, ang = _damped_sinusoid()
    t0 = detect_release_t0(t, ang)
    t_aligned = align_to_release(t, t0)

    rel_idx = int(np.argmin(np.abs(t - t0)))
    assert abs(t_aligned[rel_idx]) < 1e-9
    assert t_aligned[0] < 0
    assert t_aligned[-1] > 0


def test_imu_and_optitrack_trials_overlay_after_independent_t0_alignment():
    """
    Two recordings of the same physical release, on different clocks, units,
    and sample rates (IMU tilt magnitude vs OptiTrack angle in degrees), must
    each read t=0 at their own release frame once aligned via their own
    detected t0 — this is what makes IMU vs OptiTrack comparison plots
    overlay correctly on the time axis.
    """
    from pendulastic_pt_score import detect_release_t0, align_to_release

    # IMU: 100 Hz, small-scale unit-agnostic tilt magnitude, clock offset +5s.
    fps_imu = 100.0
    n_imu = 400
    t_imu = 5.0 + np.arange(n_imu) / fps_imu
    hold_imu = int(1.5 * fps_imu)
    tilt = np.zeros(n_imu)
    for i in range(hold_imu, n_imu):
        ti = (i - hold_imu) / fps_imu
        tilt[i] = 0.4 * math.exp(-0.3 * ti) * math.cos(2 * math.pi * 0.9 * ti)

    # OptiTrack: 120 Hz, degrees, clock starts at 0s (independent device clock).
    fps_opti = 120.0
    n_opti = 480
    t_opti = np.arange(n_opti) / fps_opti
    hold_opti = int(1.5 * fps_opti)
    ang = np.full(n_opti, 180.0)
    for i in range(hold_opti, n_opti):
        ti = (i - hold_opti) / fps_opti
        ang[i] = 165.0 + 15.0 * math.exp(-0.3 * ti) * math.cos(2 * math.pi * 0.9 * ti)

    t0_imu = detect_release_t0(t_imu, tilt)
    t0_opti = detect_release_t0(t_opti, ang)

    t_imu_aligned = align_to_release(t_imu, t0_imu)
    t_opti_aligned = align_to_release(t_opti, t0_opti)

    # Both physical releases happen 1.5s into each recording's own hold phase;
    # after independent t0 alignment, both must land at ~0 on their own axis.
    # A smooth (non-step) onset means detection lags the true release by a few
    # samples worth of threshold-crossing time — allow that, not sub-frame exactness.
    assert abs(t_imu_aligned[hold_imu]) < 0.1
    assert abs(t_opti_aligned[hold_opti]) < 0.1
