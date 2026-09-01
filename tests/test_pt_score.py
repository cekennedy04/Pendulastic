# tests/test_pt_score.py
import os, sys, math
import numpy as np
import pytest
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


def _long_hold_signal(n=400, fps=30.0, hold_frac=0.25, total_drop=55.0, freq=0.7, decay=0.3):
    """A pre-release hold long/large enough relative to the recording to
    reproduce the real-world failure mode: 1/4 of the recording flat at
    180deg, then a real swing large enough to net a big settle-point drop
    (mirrors an actual OptiTrack trial's ~3s hold / ~13s total, ~55deg drop
    from 180 to its settled resting angle)."""
    t = np.arange(n) / fps
    hold = int(hold_frac * n)
    ang = np.empty(n)
    ang[:hold] = 180.0
    settle = 180.0 - total_drop
    amp0 = total_drop
    for i in range(hold, n):
        ti = (i - hold) / fps
        ang[i] = settle + amp0 * math.exp(-decay * ti) * math.cos(2 * math.pi * freq * ti)
    return t, ang, hold


def test_release_detected_at_true_hold_boundary_not_detrend_artifact():
    """Detrending the WHOLE trial (long flat hold + real swing together)
    injects a spurious slope into the flat hold region; if release
    detection runs on that detrended signal it can fire seconds before the
    leg actually moves, discarding the trial (A0 < 3deg floor) or scoring a
    swing that never happened. Release must be detected on the raw/smoothed
    (non-detrended) signal instead -- same fix already applied in
    pt_report_common.release_aligned_waveform for plotting, here required of
    compute_pt_params itself since that's what actually computes the score."""
    t, ang, hold = _long_hold_signal()
    expected_t = t[hold]

    p = compute_pt_params(t, ang)

    assert p is not None, (
        "A real, large swing must not be discarded as sub-3deg just because "
        "detrend-induced drift during a long hold fooled release detection")
    assert abs(p["t_r"][0] - expected_t) < 0.2, (
        f"Release detected at t={p['t_r'][0]:.3f}, expected ~{expected_t:.3f} "
        "(the actual hold-to-swing boundary)")


def test_long_hold_before_large_swing_keeps_true_release_amplitude():
    """A whole-trial least-squares detrend (hold + swing together) lets a
    big real swing pull the fitted line, shrinking the apparent amplitude
    right at the (correctly-detected) release point -- confirmed against a
    real trial where a genuine ~49deg release-point swing read as ~2.65deg
    after whole-trial detrending, discarding it under the sub-3deg sanity
    floor. The drift correction must be fit from the pre-release baseline
    only, so a long hold before a large swing doesn't warp the swing it's
    supposed to leave alone."""
    t, ang, hold = _long_hold_signal(n=1600, fps=120.0, hold_frac=0.23,
                                     total_drop=55.0, freq=0.6, decay=0.35)
    p = compute_pt_params(t, ang)
    assert p is not None, "A large real swing after a long hold must not be discarded"
    # A0 should reflect the real ~55deg swing, not be shrunk by a whole-
    # trial detrend fit to a couple of degrees.
    assert p["A0_deg"] > 30.0, f"A0 too small ({p['A0_deg']:.2f}) -- swing amplitude was distorted"


def test_neutral_deg_uses_tail_window_median_not_single_last_sample():
    """neutral_deg is documented as 'tail-median of settled section (last
    25%)', but `tail_start = max(int(0.75*len(ang_r)), len(ang_r)-1)` always
    evaluates to len(ang_r)-1 for any trial longer than ~4 post-release
    samples (0.75*L < L-1 whenever L>4) -- collapsing the "window" to a
    single sample: whatever phase the oscillation happens to be at on the
    very last recorded frame, not a genuine settled-region estimate. An
    undamped oscillation ending near a trough (30deg below true center)
    while release itself lands near a peak must not report that trough
    value as neutral."""
    import math
    fs = 100.0
    hold = 60
    freq = 1.0
    total_s = 0.6 + 3.5 / freq   # recording ends ~half a cycle off release's phase
    t = np.arange(0, total_s, 1.0 / fs)
    ang = np.empty_like(t)
    ang[:hold] = 180.0
    tr = t[hold:] - t[hold]
    ang[hold:] = 165.0 + 30.0 * np.cos(2 * math.pi * freq * tr)

    p = compute_pt_params(t, ang, detrend=False)
    assert p is not None
    assert abs(p["neutral_deg"] - 165.0) < 15.0, (
        f"neutral_deg={p['neutral_deg']:.2f}, expected near the true settled "
        "center (165) -- a single end-of-recording sample let the trailing "
        "oscillation phase leak into the baseline")


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
                "f", "area_ratio", "A0_deg", "A1_deg", "neutral_deg_raw"):
        assert key in p, f"Missing key: {key}"


def test_neutral_deg_raw_is_undetrended_tail_median():
    """neutral_deg_raw must track the settled tail in the ORIGINAL (undetrended)
    signal space -- used to align external HPE/MediaPipe curves against
    angle_raw, which detrend()'s linear correction would otherwise offset."""
    t, ang = _drifting_signal(drift=6.0)
    p = compute_pt_params(t, ang, detrend=True)
    assert p is not None
    assert np.isfinite(p["neutral_deg_raw"])
    # With real drift and detrend=True, the detrended tail-median
    # (neutral_deg) and the raw tail-median (neutral_deg_raw) must diverge --
    # otherwise neutral_deg_raw is just aliasing neutral_deg and isn't doing
    # its job of representing the undetrended signal.
    assert abs(p["neutral_deg_raw"] - p["neutral_deg"]) > 0.5


def test_neutral_deg_raw_close_to_neutral_deg_when_detrend_disabled():
    """With detrend=False, neutral_deg_raw (unsmoothed tail-median) and
    neutral_deg (SG-smoothed tail-median) should be close -- the only
    remaining difference is smoothing, not detrending."""
    t, ang = _damped_sinusoid()
    p = compute_pt_params(t, ang, detrend=False)
    assert p is not None
    assert abs(p["neutral_deg_raw"] - p["neutral_deg"]) < 0.5


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


def test_detect_release_t0_rejects_mismatched_lengths():
    from pendulastic_pt_score import detect_release_t0
    t = np.arange(10) / 30.0
    signal = np.zeros(9)
    with pytest.raises(ValueError):
        detect_release_t0(t, signal)


def test_detect_release_t0_rejects_non_monotonic_time():
    from pendulastic_pt_score import detect_release_t0
    t = np.array([0.0, 0.1, 0.05, 0.2, 0.3, 0.4, 0.5, 0.6])
    signal = np.array([180.0, 179.0, 178.0, 180.0, 165.0, 160.0, 158.0, 157.0])
    with pytest.raises(ValueError):
        detect_release_t0(t, signal)


def test_detect_release_t0_raises_on_flat_signal_instead_of_returning_baseline_index():
    """Regression: a constant signal has signal_range=0, so _detect_release's
    forward scan never crosses its own threshold and silently falls through
    to the baseline-window boundary index. Before this fix, detect_release_t0
    returned that boundary time as if it were a real release -- exactly the
    "bogus auto-seeded mark" failure mode the workbench's auto-seed feature
    must not hit."""
    from pendulastic_pt_score import detect_release_t0
    t = np.arange(90) / 30.0
    signal = np.full(90, 180.0)
    with pytest.raises(ValueError):
        detect_release_t0(t, signal, baseline_sec=1.0)


# ══════════════════════════════════════════════════════════════════════════
# load_hpe_model_curves: explicit csv_files bypass + nested Session_post/ discovery
# ══════════════════════════════════════════════════════════════════════════

def test_load_hpe_model_curves_empty_csv_files_skips_discovery(monkeypatch):
    """Passing csv_files=[] (not None) must short-circuit before any
    Recordings/OptiTrack_Recordings glob discovery runs -- an explicit (even
    empty) csv_files list means the caller opted out of auto-discovery."""
    import pendulastic_pt_score as pt
    calls = []
    monkeypatch.setattr(pt.glob, "glob", lambda *a, **kw: calls.append(a) or [])

    t, ang = _damped_sinusoid()
    result = pt.load_hpe_model_curves("999", "1", "1", t, ang, 180.0, csv_files=[])
    assert result == []
    assert calls == [], "discovery glob.glob should never run when csv_files is explicitly provided"


def test_load_hpe_model_curves_csv_files_bypass_reads_explicit_paths(tmp_path, monkeypatch):
    """An explicit, non-empty csv_files list must be read directly, without
    ever touching HPE_ROOT/OPTI_ROOT discovery."""
    import pendulastic_pt_score as pt
    import pandas as pd

    monkeypatch.setattr(pt, "HPE_ROOT", str(tmp_path / "no_such_recordings_dir"))
    monkeypatch.setattr(pt, "OPTI_ROOT", str(tmp_path / "no_such_optitrack_dir"))

    t, ang = _damped_sinusoid(n=300, fps=30.0)
    # A perfect copy of the OptiTrack signal as the "model" curve -- guarantees
    # it tracks the swing (ratio check) and yields rmse == 0.
    csv_path = tmp_path / "P999_T_1_perfectmodel.csv"
    pd.DataFrame({"time_sec": t, "knee_angle_deg": ang}).to_csv(csv_path, index=False)

    curves = pt.load_hpe_model_curves("999", "1", "1", t, ang, 180.0, csv_files=[str(csv_path)])
    assert len(curves) == 1
    assert curves[0]["name"] == "perfectmodel"
    # A near-identical curve, modulo the SG-smoothing/outlier-rejection cleanup
    # both signals pass through -- should track almost exactly, not necessarily bit-exact.
    assert curves[0]["rmse"] < 0.1


def test_load_hpe_model_curves_finds_nested_session_post_dir(tmp_path, monkeypatch):
    """Post-treatment sessions nest an extra Session_post/ level
    (Participant_N/Session_post/Position_1/Height_Joint-Level/) that the old
    fixed-depth path join couldn't see -- the recursive glob fallback must
    still find CSVs there."""
    import pendulastic_pt_score as pt
    import pandas as pd

    hpe_root = tmp_path / "Recordings"
    rec_dir = hpe_root / "Participant_999" / "Session_post" / "Position_1" / "Height_Joint-Level"
    rec_dir.mkdir(parents=True)
    monkeypatch.setattr(pt, "HPE_ROOT", str(hpe_root))
    monkeypatch.setattr(pt, "OPTI_ROOT", str(tmp_path / "no_such_optitrack_dir"))

    t, ang = _damped_sinusoid(n=300, fps=30.0)
    csv_path = rec_dir / "P999_T_1_perfectmodel.csv"
    pd.DataFrame({"time_sec": t, "knee_angle_deg": ang}).to_csv(csv_path, index=False)

    curves = pt.load_hpe_model_curves("999", "1", "1", t, ang, 180.0)
    assert len(curves) == 1
    assert curves[0]["name"] == "perfectmodel"


def test_load_hpe_model_curves_finds_simplified_folder_structure(tmp_path, monkeypatch):
    """Newer recordings (merged 2026-08, "simplified recording folder
    structure") lay out as Recordings/Participant_{N}/{Leg}/{characterization}/
    directly -- no Position_N/Height_Joint-Level nesting at all, and the
    on-disk Leg folder is capitalized ("Left") while pid_str's leg component
    is lowercase ("left"), so the match has to be case-insensitive."""
    import pendulastic_pt_score as pt
    import pandas as pd

    hpe_root = tmp_path / "Recordings"
    rec_dir = hpe_root / "Participant_14" / "Left" / "pre"
    rec_dir.mkdir(parents=True)
    monkeypatch.setattr(pt, "HPE_ROOT", str(hpe_root))
    monkeypatch.setattr(pt, "OPTI_ROOT", str(tmp_path / "no_such_optitrack_dir"))

    t, ang = _damped_sinusoid(n=300, fps=30.0)
    csv_path = rec_dir / "Participant_14_T_1_mediapipe_full_0.5.csv"
    pd.DataFrame({"time_sec": t, "knee_angle_deg": ang}).to_csv(csv_path, index=False)

    curves = pt.load_hpe_model_curves("14_left_pre", "1", "1", t, ang, 180.0)
    assert len(curves) == 1
    assert curves[0]["name"] == "mediapipe_full_0.5"


# ══════════════════════════════════════════════════════════════════════════
# load_hpe_model_curves: raw-IMU-replay fallback (no hand-exported imu_viewer.csv)
# ══════════════════════════════════════════════════════════════════════════

def test_load_hpe_model_curves_falls_back_to_raw_imu_replay(tmp_path, monkeypatch):
    """No manually-exported "..._imu_viewer.csv" for this trial, but raw
    split-IMU logs (Trial_1_accel/gyro/mag.csv, captured by every recording
    session) do exist -- _replay_raw_imu_fallback's synthesized curve must
    flow through the same alignment/RMSE pipeline as any CSV-sourced one."""
    import pendulastic_pt_score as pt

    hpe_root = tmp_path / "Recordings"
    rec_dir = hpe_root / "Participant_15" / "Right" / "pre"
    rec_dir.mkdir(parents=True)
    monkeypatch.setattr(pt, "HPE_ROOT", str(hpe_root))
    monkeypatch.setattr(pt, "OPTI_ROOT", str(tmp_path / "no_such_optitrack_dir"))

    # Raw component files only need to exist -- _replay_raw_imu_fallback
    # itself is monkeypatched below, so their content is never parsed.
    for suffix in ("accel", "gyro", "mag"):
        (rec_dir / f"Trial_1_{suffix}.csv").write_text("dummy")

    t, ang = _damped_sinusoid(n=300, fps=30.0)
    monkeypatch.setattr(pt, "_replay_raw_imu_fallback",
                        lambda _rec_dir, _trial: (t.copy(), ang.copy()))

    curves = pt.load_hpe_model_curves("15_right_pre", "1", "1", t, ang, 180.0)
    assert len(curves) == 1
    assert curves[0]["name"] == "imu_viewer"
    assert curves[0]["rmse"] < 0.1


def test_load_hpe_model_curves_skips_raw_imu_fallback_when_imu_viewer_csv_exists(tmp_path, monkeypatch):
    """A hand-exported imu_viewer.csv (the P13 reference-dataset convention)
    must win -- the raw-replay fallback must not run or override it."""
    import pendulastic_pt_score as pt
    import pandas as pd

    hpe_root = tmp_path / "Recordings"
    rec_dir = hpe_root / "Participant_13" / "Right" / "pre"
    rec_dir.mkdir(parents=True)
    monkeypatch.setattr(pt, "HPE_ROOT", str(hpe_root))
    monkeypatch.setattr(pt, "OPTI_ROOT", str(tmp_path / "no_such_optitrack_dir"))

    t, ang = _damped_sinusoid(n=300, fps=30.0)
    csv_path = rec_dir / "Participant_13_T_1_imu_viewer.csv"
    pd.DataFrame({"time_sec": t, "knee_angle_deg": ang}).to_csv(csv_path, index=False)

    calls = []
    monkeypatch.setattr(pt, "_replay_raw_imu_fallback",
                        lambda *a, **k: calls.append(a) or None)

    curves = pt.load_hpe_model_curves("13_right_pre", "1", "1", t, ang, 180.0)
    assert len(curves) == 1
    assert curves[0]["name"] == "imu_viewer"
    assert calls == [], "fallback must not run when a hand-exported imu_viewer.csv exists"


def test_replay_raw_imu_fallback_returns_none_without_raw_components(tmp_path):
    """No Trial_{n}_accel/gyro/mag.csv siblings in rec_dir -> None, not an
    exception -- matches this module's other best-effort-fallback pattern."""
    import pendulastic_pt_score as pt
    assert pt._replay_raw_imu_fallback(str(tmp_path), "1") is None


# ══════════════════════════════════════════════════════════════════════════
# draw_pt_annotations: Clinical plot annotation rendering
# ══════════════════════════════════════════════════════════════════════════

def test_draw_pt_annotations_returns_none_for_insufficient_data():
    from matplotlib.figure import Figure
    from pendulastic_pt_score import draw_pt_annotations

    fig = Figure()
    ax = fig.add_subplot(111)

    assert draw_pt_annotations(ax, {}) is None
    assert draw_pt_annotations(ax, {"neutral_deg": 170.0, "t_r": [0.0]}) is None


def test_draw_pt_annotations_returns_artists_for_valid_params():
    from matplotlib.figure import Figure
    from pendulastic_pt_score import draw_pt_annotations

    fig = Figure()
    ax = fig.add_subplot(111)
    ax.plot([0, 1, 2], [170, 150, 160])  # give the Axes real xlim/ylim

    t_r   = np.array([0.0, 0.1, 0.2, 0.3, 0.4])
    ang_r = np.array([170.0, 130.0, 145.0, 138.0, 142.0])
    params = {
        "neutral_deg": 175.0,
        "pre_release_deg": 178.0,
        "t_r": t_r,
        "ang_r": ang_r,
        "pk_i": np.array([2, 4]),
        "tr_i": np.array([1, 3]),
        "A0_deg": 8.0,
        "phi_max_ratio": 0.62,
        "N": 3.0,
    }

    artists = draw_pt_annotations(ax, params)
    assert artists is not None
    assert len(artists) > 0

    # Must not error when called again after ax.clear() -- matches the
    # PostProcessingPanel._plot_all_curves() clear-then-redraw pattern.
    ax.clear()
    artists2 = draw_pt_annotations(ax, params)
    assert artists2 is not None
    assert len(artists2) > 0


def test_draw_pt_annotations_manual_release_label():
    from matplotlib.figure import Figure
    from pendulastic_pt_score import draw_pt_annotations

    fig = Figure()
    ax = fig.add_subplot(111)
    ax.plot([0, 1], [170, 150])
    params = {"neutral_deg": 170.0, "t_r": np.array([0.0, 0.1]),
              "ang_r": np.array([170.0, 150.0])}

    artists = draw_pt_annotations(ax, params, manual_release=True)
    texts = [a.get_text() for a in artists if hasattr(a, "get_text")]
    assert any("manual" in t for t in texts)


# ══════════════════════════════════════════════════════════════════════════
# load_hpe_model_curves: return_rejected accounting mode
# ══════════════════════════════════════════════════════════════════════════

def test_load_hpe_model_curves_default_unchanged_when_return_rejected_false():
    """return_rejected=False (the default) must keep today's exact return
    shape -- a bare list, not a tuple -- so every existing caller
    (pendulastic_pt_score.py's own single-trial plots) is unaffected."""
    import pendulastic_pt_score as pt
    t = np.linspace(0, 2, 60)
    angle = 180 - 40 * np.sin(np.pi * t / 2) * (t < 1.0)
    result = pt.load_hpe_model_curves("999_left_pre", "1", "1", t, angle, 180.0, csv_files=[])
    assert isinstance(result, list)
    assert result == []


def test_load_hpe_model_curves_return_rejected_true_gives_tuple(tmp_path, monkeypatch):
    """return_rejected=True must give (accepted, rejected), both lists,
    even when nothing was ever discovered (no csv_files, no replay
    fallback) -- rejected is [] in that case, not a crash."""
    import pendulastic_pt_score as pt
    t = np.linspace(0, 2, 60)
    angle = 180 - 40 * np.sin(np.pi * t / 2) * (t < 1.0)
    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t, angle, 180.0, csv_files=[], return_rejected=True)
    assert accepted == []
    assert rejected == []


def test_load_hpe_model_curves_return_rejected_reports_did_not_track_swing(tmp_path):
    """A candidate CSV whose knee_angle_deg never leaves neutral (flat
    signal) fails the swing-tracking quality gate -- with
    return_rejected=True this must show up in `rejected` with a reason,
    not silently vanish the way it does today."""
    import pendulastic_pt_score as pt
    import pandas as pd

    t_opti = np.linspace(0, 2, 60)
    angle_opti = 180 - 40 * np.sin(np.pi * t_opti / 2) * (t_opti < 1.0)

    flat_csv = tmp_path / "P_T_1_mediapipe.csv"
    t_m = np.linspace(0, 2, 60)
    pd.DataFrame({"time_sec": t_m, "knee_angle_deg": np.full(60, 180.0)}).to_csv(flat_csv, index=False)

    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t_opti, angle_opti, 180.0,
        csv_files=[str(flat_csv)], return_rejected=True)
    assert accepted == []
    assert len(rejected) == 1
    assert rejected[0]["name"] == "mediapipe"
    assert rejected[0]["reason"] == "did_not_track_swing"


# ══════════════════════════════════════════════════════════════════════════
# load_hpe_model_curves: minimal-overshoot (spastic) trials
#
# A spastic limb arrests at its own resting angle, so flexion-past-neutral
# collapses to ~0 even on a full-amplitude swing. Real example driving these
# tests -- Participant_19 Right/pre, a stroke participant's affected leg:
# held at 180 deg, released, travels to ~132 deg, settles at ~135 deg. Total
# excursion 44-48 deg, but overshoot past neutral only 0.3-2.8 deg. Five such
# trials exist across the dataset (P13, P14, P19), all with PT7 >= 1.42.
# ══════════════════════════════════════════════════════════════════════════

def _spastic_trial(n=300, fps=30.0, hold_deg=180.0, settle_deg=135.0, overshoot=2.5):
    """Pendulum that drops a long way but arrests at its resting angle:
    large total excursion, near-zero flexion past neutral."""
    t = np.arange(n) / fps
    ang = np.empty(n)
    hold = int(fps)
    for i in range(n):
        if i < hold:
            ang[i] = hold_deg
        else:
            ti = (i - hold) / fps
            # Heavily damped: one shallow dip below the settle angle, then flat.
            ang[i] = settle_deg - overshoot * math.exp(-3.0 * ti) * math.cos(2 * math.pi * 0.9 * ti)
    return t, ang


def _write_tracking_csv(path, t_m, ang_m):
    import pandas as pd
    pd.DataFrame({"time_sec": t_m, "knee_angle_deg": ang_m}).to_csv(path, index=False)


def test_minimal_overshoot_trial_is_not_discarded(tmp_path):
    """A spastic limb with ~45 deg of real travel but ~2.5 deg of flexion
    past neutral must still be analysed. The old opti_peak < 3.0 gate threw
    these away before any candidate was even enumerated, silently removing
    source-agreement data from the most impaired limbs in the dataset."""
    import pendulastic_pt_score as pt

    t, ang = _spastic_trial()
    assert (ang.max() - ang.min()) > 40.0        # real movement
    assert (135.0 - ang.min()) < 3.0             # but almost no overshoot

    csv_path = tmp_path / "P_T_1_mediapipe.csv"
    _write_tracking_csv(csv_path, t, ang + 1.0)  # model tracks it closely

    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t, ang, 135.0,
        csv_files=[str(csv_path)], return_rejected=True)

    assert accepted, f"tracking candidate was discarded; rejected={rejected}"
    assert accepted[0]["name"] == "mediapipe"
    # NB: not asserting a small rmse here. load_hpe_model_curves aligns a
    # candidate by shifting its first-0.5s reference window onto neutral_deg,
    # but that window sits at the HELD angle (180) not the resting angle, so
    # its internal rmse carries a constant hold-to-neutral offset. Pre-existing
    # behaviour, unrelated to overshoot; pt_report_common._lag_align_candidate
    # recomputes the reported RMSE via compare_pair. What matters here is that
    # the candidate is no longer discarded before evaluation.
    assert np.isfinite(accepted[0]["rmse"])


def test_minimal_overshoot_trial_still_rejects_untracking_model(tmp_path):
    """Admitting low-overshoot trials must not disable the quality filter:
    a flat model curve over the same trial must still be rejected. Guards
    against trading 'no RMSE bars' for 'meaningless RMSE bars'."""
    import pendulastic_pt_score as pt

    t, ang = _spastic_trial()
    csv_path = tmp_path / "P_T_1_mediapipe.csv"
    _write_tracking_csv(csv_path, t, np.full(len(t), 180.0))   # never moves

    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t, ang, 135.0,
        csv_files=[str(csv_path)], return_rejected=True)

    assert accepted == []
    assert len(rejected) == 1
    assert rejected[0]["reason"] == "did_not_track_swing"


def test_trial_with_no_real_movement_is_still_discarded(tmp_path):
    """The validity check must still reject genuinely dead recordings --
    marker dropout, leg never released. Excursion, not overshoot, is what
    separates those from a spastic limb."""
    import pendulastic_pt_score as pt

    t = np.arange(300) / 30.0
    ang = 180.0 + 0.3 * np.sin(2 * np.pi * 0.9 * t)   # ~0.6 deg of jitter
    csv_path = tmp_path / "P_T_1_mediapipe.csv"
    _write_tracking_csv(csv_path, t, ang)

    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t, ang, 180.0,
        csv_files=[str(csv_path)], return_rejected=True)

    assert accepted == []
    assert rejected == []


def test_normal_overshoot_trial_behaviour_is_unchanged(tmp_path):
    """Characterisation: a limb that DOES overshoot must keep the exact
    neutral-referenced behaviour it had before minimal-overshoot support
    was added, so no existing published RMSE number moves."""
    import pendulastic_pt_score as pt

    t, ang = _damped_sinusoid()                  # 15 deg amplitude, ~30 deg overshoot
    csv_path = tmp_path / "P_T_1_mediapipe.csv"
    _write_tracking_csv(csv_path, t, ang + 2.0)

    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t, ang, 180.0,
        csv_files=[str(csv_path)], return_rejected=True)

    assert accepted, f"rejected={rejected}"
    assert accepted[0]["name"] == "mediapipe"
    assert accepted[0]["rmse"] < 5.0


def test_candidate_is_baseline_aligned_to_hold_not_to_neutral(tmp_path):
    """A candidate that reproduces the OptiTrack curve exactly must come back
    sitting ON that curve, not shifted down by the hold-to-neutral gap.

    Regression test: alignment used to map the candidate's reference window
    (the pre-release HOLD, ~180 deg) onto neutral_deg (the RESTING angle,
    ~140 deg on real trials), displacing every curve by ~40 deg and inflating
    its RMSE by the same amount."""
    import pendulastic_pt_score as pt

    t, ang = _damped_sinusoid()          # holds at 180, settles near 165
    neutral = 165.0                      # resting angle, 15 deg below the hold
    assert abs(ang[:15].mean() - 180.0) < 0.5

    csv_path = tmp_path / "P_T_1_mediapipe.csv"
    _write_tracking_csv(csv_path, t, ang)          # perfect tracker

    accepted, rejected = pt.load_hpe_model_curves(
        "999_left_pre", "1", "1", t, ang, neutral,
        csv_files=[str(csv_path)], return_rejected=True)

    assert accepted, f"rejected={rejected}"
    cleaned = accepted[0]["ang"]
    ok = np.isfinite(cleaned)
    # A perfect tracker must land on the reference, within smoothing error.
    assert abs(float(np.nanmean(cleaned[ok] - ang[ok]))) < 1.0
    assert accepted[0]["rmse"] < 2.0


# ── settled-tail drift correction (2026-08-28) ─────────────────────────────
# A0 = (angle at release) - (median of the settled tail). A sensor whose curve
# sinks through the trial drags that tail median down and inflates A0 with no
# error in the swing at all. The drift correction used to fit ONLY the
# pre-release hold, which is where gyro bias was just calibrated and is flat by
# construction, so it could not see the drift it existed to remove. Measured on
# 93 IMU trials: baseline slope +0.193 deg/s vs settled tail -0.833 deg/s.

def _damped_swing(sweep=50.0, fs=100.0, hold_s=2.0, swing_s=16.0,
                  drift_deg_s=0.0, settle=180.0 - 50.0, drift_from_release=True):
    """Held-then-released pendulum decaying to `settle`, plus optional linear
    sensor drift. Angle convention: 180 = fully extended.

    drift_from_release models what the real IMU does, and it is the whole
    point. zero() recalibrates gyro bias from the hold buffer at the tare
    instant, so the pre-release hold is FLAT and the drift accumulates
    afterwards. Applying drift from t=0 instead would put the full slope inside
    the pre-release baseline, where the old baseline-only fit removes it
    perfectly -- a synthetic that the defect cannot reproduce on.
    """
    import numpy as np
    n_hold = int(hold_s * fs)
    n_swing = int(swing_s * fs)
    t = np.arange(n_hold + n_swing) / fs
    ang = np.empty(len(t))
    ang[:n_hold] = 180.0
    ts = np.arange(n_swing) / fs
    ang[n_hold:] = settle + sweep * np.exp(-ts / 2.0) * np.cos(2 * np.pi * 0.9 * ts)
    if drift_from_release:
        ramp = np.concatenate([np.zeros(n_hold), ts])
    else:
        ramp = t - t[0]
    return t, ang + drift_deg_s * ramp


def test_settled_tail_slope_recovers_a_known_drift():
    import pendulastic_pt_score as p
    for true_slope in (-0.8, -0.3, 0.0, 0.5):
        t, ang = _damped_swing(drift_deg_s=true_slope)
        got = p._settled_tail_drift_slope(t, ang, rel_i=200)
        assert got is not None, true_slope
        assert got == pytest.approx(true_slope, abs=0.12), (true_slope, got)


def test_settled_tail_slope_refuses_a_tail_that_is_still_swinging():
    """Over-correcting an unsettled trial would eat real swing, so a ringing
    tail must return None rather than a confident wrong number."""
    import numpy as np
    import pendulastic_pt_score as p
    t, ang = _damped_swing(swing_s=4.0)          # ends mid-oscillation
    assert p._settled_tail_drift_slope(t, ang, rel_i=200) is None


def test_settled_tail_slope_refuses_an_implausibly_large_slope():
    import pendulastic_pt_score as p
    t, ang = _damped_swing(drift_deg_s=40.0)
    assert p._settled_tail_drift_slope(t, ang, rel_i=200) is None


def test_settled_tail_slope_refuses_too_short_a_tail():
    import pendulastic_pt_score as p
    t, ang = _damped_swing(swing_s=12.0)
    assert p._settled_tail_drift_slope(t, ang, rel_i=len(t) - 5) is None


def test_drift_no_longer_inflates_A0():
    """THE defect. The same swing, scored with and without sensor drift, must
    give the same A0 -- the drift is in the sensor, not in the leg."""
    import numpy as np
    import pendulastic_pt_score as p
    t, clean = _damped_swing(drift_deg_s=0.0)
    _t, drifting = _damped_swing(drift_deg_s=-0.8)

    a_clean = p.compute_pt_params(t, clean)
    a_drift = p.compute_pt_params(t, drifting)
    assert a_clean and a_drift
    assert a_drift["A0_deg"] == pytest.approx(a_clean["A0_deg"], rel=0.10), (
        a_clean["A0_deg"], a_drift["A0_deg"])


def test_a_flat_optical_curve_is_left_alone():
    """OptiTrack tails measure +0.009 deg/s, so the correction must be a no-op
    there. A fix that only helps the IMU by disturbing the reference is not a
    fix."""
    import pendulastic_pt_score as p
    t, ang = _damped_swing(drift_deg_s=0.0)
    with_detrend = p.compute_pt_params(t, ang, detrend=True)
    without = p.compute_pt_params(t, ang, detrend=False)
    assert with_detrend and without
    assert with_detrend["A0_deg"] == pytest.approx(without["A0_deg"], rel=0.03)


def test_pendulum_still_decaying_is_not_mistaken_for_drift():
    """The guard that a first attempt at this fix got wrong.

    A decaying oscillation is LINEAR over less than one period, so a short tail
    fits a steep slope with a tiny residual and looks exactly like drift. On a
    0.32 Hz synthetic whose tail covered 0.62 of a period the fit came back at
    -4.13 deg/s with a residual of 2.17 deg, and correcting by it ate 29 deg of
    real swing (A0 45.6 -> 16.8). The tail must span several periods before its
    slope means anything.
    """
    import numpy as np
    import pendulastic_pt_score as p
    t = np.linspace(0, 10, 400)
    ang = np.where(t < 2.0, 180.0,
                   130.0 + 50.0 * np.exp(-0.4 * (t - 2.0)) * np.cos(2.0 * (t - 2.0)))
    rel = p._detect_release(t, p._sg(ang, dt=p._median_dt(t), p=3))
    assert p._settled_tail_drift_slope(t, ang, rel) is None
    params = p.compute_pt_params(t, ang)
    assert params["A0_deg"] == pytest.approx(45.6, rel=0.10), params["A0_deg"]
    assert params["quality_warn"] is False


def test_drift_cap_is_what_stops_the_decay_case_and_must_not_be_raised():
    """Pins the interaction the tuning exposed.

    The consistency tolerances were relaxed to their measured optimum (80%
    coverage), and at that point the SLOPE CAP is the last guard rejecting a
    still-decaying pendulum. Raising it to 5 lets the 0.32 Hz case through and
    eats 29 deg of real swing. This test fails if someone raises the cap
    without re-deriving the tolerances.
    """
    import numpy as np
    import pendulastic_pt_score as p
    t = np.linspace(0, 10, 400)
    ang = np.where(t < 2.0, 180.0,
                   130.0 + 50.0 * np.exp(-0.4 * (t - 2.0)) * np.cos(2.0 * (t - 2.0)))
    rel = p._detect_release(t, p._sg(ang, dt=p._median_dt(t), p=3))
    assert p._MAX_DRIFT_DEG_S <= 4.0, "raising this re-opens the 29 deg swing-eating bug"
    assert p._settled_tail_drift_slope(t, ang, rel) is None

    original = p._MAX_DRIFT_DEG_S
    try:
        p._MAX_DRIFT_DEG_S = 5.0
        assert p._settled_tail_drift_slope(t, ang, rel) is not None, (
            "if this no longer fires, the cap is not the guard doing the work "
            "and the comment on _MAX_DRIFT_DEG_S is stale")
    finally:
        p._MAX_DRIFT_DEG_S = original


def test_padding_a_short_tail_with_stable_data_erases_the_drift_it_should_find():
    """Why the 'assume a stable leg and extend the tail' approach is not used.

    Measured on the corpus: of the trials this guard rejects, 16 of 19 are still
    moving faster than 1 deg/s when the recording stops, and their honest tail
    slope (-1.059 deg/s) is STEEPER than the trials we do correct. Appending
    flat samples at the last observed value does not estimate that drift, it
    erases it -- driving the fit to ~0 and switching the correction off on the
    trials that need it most, while appearing to reach 100% coverage.
    """
    import numpy as np
    import pendulastic_pt_score as p

    # A trial whose sensor drifts and which is STILL sinking when it ends.
    fs = 100.0
    t = np.arange(int(9 * fs)) / fs
    rel = int(2 * fs)
    ang = np.full(len(t), 180.0)
    ts = t[rel:] - t[rel]
    ang[rel:] = 130.0 + 50.0 * np.exp(-ts / 3.0) * np.cos(2 * np.pi * 0.9 * ts) - 1.0 * ts

    tail_slope = float(np.polyfit(t[-150:], ang[-150:], 1)[0])
    assert tail_slope < -0.5, tail_slope        # genuinely still descending

    dt = 1.0 / fs
    pad_t = t[-1] + dt * np.arange(1, int(4.0 / dt) + 1)
    padded_t = np.concatenate([t, pad_t])
    padded_ang = np.concatenate([ang, np.full(len(pad_t), ang[-1])])

    padded_slope = p._settled_tail_drift_slope(padded_t, padded_ang, rel)
    # Padding either yields ~0 (the drift erased) or is rejected. Either way it
    # never recovers the real slope, which is the entire point.
    assert padded_slope is None or abs(padded_slope) < abs(tail_slope) / 2.0, (
        tail_slope, padded_slope)


def test_quadriceps_catch_merge_was_subsumed_by_find_peaks():
    """The removed sub-peak merge could never fire, at any sample rate.

    find_peaks is given distance = fps/3.5, so no two returned extrema are ever
    closer than that. The merge window was fps/6 -- strictly INSIDE a separation
    already guaranteed. The two constants were inverted against each other, so
    a 'spastic quadriceps catch' safeguard was advertised in the code while
    being unreachable. Removing it changed nothing on any of the 186 real
    curves in the corpus.

    This test exists so the same mistake is not reintroduced: any merge window
    must be WIDER than find_peaks' distance to do anything at all.
    """
    for fps in (30, 60, 100, 120, 200, 2000):
        find_peaks_distance = max(3, int(fps / 3.5))
        old_merge_window = max(3, int(fps / 6))
        assert old_merge_window <= find_peaks_distance, (
            f"at {fps} Hz the merge window ({old_merge_window}) exceeds "
            f"find_peaks distance ({find_peaks_distance}) -- if this ever "
            f"becomes true the removal reasoning needs revisiting")


def test_merge_helper_is_gone_not_merely_unused():
    """A dead function that claims a clinical safeguard is worse than no
    function: it reads as protection that exists."""
    import pendulastic_pt_score as p
    assert not hasattr(p, "_merge_close_extrema")


# ── excursion gate (2026-08-30) ────────────────────────────────────────────
# PT7 is non-monotonic in severity: all seven parameters are ratios normalised
# on the swing, so a collapsed swing renormalises them and a near-rigid leg
# scores healthy. The gate refuses the VERDICT in that regime; it does not
# claim to fix the non-monotonicity.

def test_excursion_gate_refuses_a_grade_for_a_barely_moving_leg():
    """The unsafe cell, in real numbers: two corpus trials with A0 = 9.0 deg
    currently report PT7 0.278 -> MAS '1+'. A leg that moved 9 degrees is not
    mildly spastic; it is a trial you cannot read."""
    import pendulastic_pt_score as p
    params = {"A0_deg": 9.0, "R2n": 1.0, "N": 3.0, "phi_max_ratio": 0.6,
              "omega_max_n": 1.0, "omega_min_n": 1.0, "f": 1.0, "area_ratio": 0.1}
    out = p.mas_estimate(params)
    assert out["interpretable"] is False
    assert out["mas"] is None, "must not hand back a grade it cannot support"
    assert out["pt7"] is not None, "the score is still reported, just not graded"
    assert "excursion" in out["reason"].lower()


def test_excursion_gate_message_is_about_the_measurement_not_the_patient():
    """Low excursion also comes from poor positioning, incomplete release,
    guarding, obstruction and sensor failure. Reporting 'severe spasticity'
    would trade one wrong answer for another."""
    import pendulastic_pt_score as p
    params = {"A0_deg": 5.0, "R2n": 1.0, "N": 3.0, "phi_max_ratio": 0.6,
              "omega_max_n": 1.0, "omega_min_n": 1.0, "f": 1.0, "area_ratio": 0.1}
    reason = p.mas_estimate(params)["reason"].lower()
    assert "repeat" in reason, "must tell the operator to re-run the trial"
    assert "positioning" in reason, "must point at the protocol, not the patient"
    assert "severe" not in reason, "must not assert a severity it cannot measure"


def test_a_normal_swing_is_graded_as_before():
    """The gate must be inert on trials it has no business touching -- it fires
    on 2 of 53 control trials, not on the population."""
    import pendulastic_pt_score as p
    params = {"A0_deg": 48.0, "R2n": 1.0, "N": 3.0, "phi_max_ratio": 0.7,
              "omega_max_n": 1.0, "omega_min_n": 1.0, "f": 1.0, "area_ratio": 0.05}
    out = p.mas_estimate(params)
    assert out["interpretable"] is True
    assert out["mas"] == p.pt_to_mas(out["pt7"]), "unchanged grading above the gate"


def test_excursion_gate_stays_clear_of_the_lowest_spastic_leg():
    """What actually constrains the threshold.

    The "two SD below the control mean" story is circular: the only two trials
    pulling that mean down are P9 left/right at A0 9.0, which are exactly the
    trials the gate catches. Excluding them the 51 clean controls give a 2-SD
    floor of 31.5 -- ABOVE the lowest spastic leg (28.7), so a clean control
    bound would refuse grades on the study's own cases.

    The real constraint is therefore the spastic minimum, and it is the one
    worth pinning: the gate must stay below 28.7 or it starts reclassifying
    the cases the study exists to measure.
    """
    import pendulastic_pt_score as p
    assert p.MIN_INTERPRETABLE_A0_DEG < 28.7, (
        "gate would refuse grades on real spastic legs")
    assert p.MIN_INTERPRETABLE_A0_DEG > 12.0, (
        "gate so low it would stop catching collapsed swings")


def test_unscoreable_trial_is_not_interpretable():
    import pendulastic_pt_score as p
    for params in (None, {}, {"A0_deg": None}, {"A0_deg": float("nan")}):
        assert p.excursion_ok(params) is False
        assert p.mas_estimate(params)["mas"] is None


# ── the excursion gate must guard BOTH directions ────────────────────────────

def _plausible_params(a0):
    return {"A0_deg": a0, "R2n": 1.0, "N": 3.0, "phi_max_ratio": 0.6,
            "omega_max_n": 6.0, "omega_min_n": 0.001, "f": 0.9, "area_ratio": 0.08}


def test_impossibly_large_excursion_is_refused_a_mas_grade():
    import pendulastic_pt_score as pt
    """A0 = 418.1 deg is produced by the seed-window bug on P9 Left/Right
    trial_3 at 97.3% coverage. A floor-only gate passed it straight through and
    printed a MAS grade off a reconstruction that had failed."""
    got = pt.mas_estimate(_plausible_params(418.1))
    assert got["mas"] is None
    assert got["interpretable"] is False
    assert "Impossible excursion" in got["reason"]
    assert "418.1" in got["reason"]


def test_the_gate_still_refuses_a_collapsed_swing():
    import pendulastic_pt_score as pt
    got = pt.mas_estimate(_plausible_params(8.5))
    assert got["mas"] is None
    assert "Insufficient excursion" in got["reason"]


def test_the_two_refusals_give_different_reasons():
    import pendulastic_pt_score as pt
    """Too-small and too-large are different failures and must not be reported
    with the same message -- one says repeat the trial, the other says the
    reconstruction is wrong."""
    small = pt.mas_estimate(_plausible_params(8.5))["reason"]
    large = pt.mas_estimate(_plausible_params(418.1))["reason"]
    assert small != large


def test_every_excursion_measured_in_this_corpus_is_still_accepted():
    import pendulastic_pt_score as pt
    """The ceiling must reject nothing that was ever really measured. Across 218
    scored optical trials the largest genuine A0 is 89.8 deg."""
    for a0 in (25.0, 46.6, 63.5, 89.8):
        assert pt.excursion_ok(_plausible_params(a0)), a0


def test_a_knee_angle_cannot_exceed_180_so_the_ceiling_sits_below_it():
    import pendulastic_pt_score as pt
    assert pt.MAX_INTERPRETABLE_A0_DEG < 180.0
    assert pt.MAX_INTERPRETABLE_A0_DEG > 89.8
    assert not pt.excursion_ok(_plausible_params(180.0))


def test_nan_and_missing_a0_are_still_not_interpretable():
    import pendulastic_pt_score as pt
    assert not pt.excursion_ok({"A0_deg": float("nan")})
    assert not pt.excursion_ok({})
    assert not pt.excursion_ok(None)


# ── every scored parameter is invariant to a constant angle offset ───────────
#
# This is the load-bearing property of the 2026-08-31 pose-free knee-axis design
# (docs/superpowers/specs/2026-08-31-optitrack-knee-axis-design.md). That design
# gives up on recovering the absolute zero -- the rig cannot support it, since
# in all 254 trials at least one segment is a collinear bar whose roll is
# unobservable -- and instead argues that the zero does not matter, because
# every scored quantity is a difference, a ratio of differences, a derivative,
# a frequency, a count, or an integral of those.
#
# The spec verifies that by reading the source. These tests verify it by
# measurement, because if it is false the whole design collapses and the failure
# would be silent: scores would drift with an offset nobody can observe.

def _offset_invariance_probe():
    import numpy as np
    import pendulastic_pt_score as pt
    t = np.arange(0, 6.0, 1 / 120.0)
    hold = t < 1.0
    swing = 180.0 - 45.0 * (1.0 - np.exp(-1.8 * (t - 1.0)) * np.cos(2 * np.pi * 0.9 * (t - 1.0)))
    ang = np.where(hold, 180.0, swing)
    return t, ang


def test_every_scored_parameter_ignores_a_constant_angle_offset():
    import numpy as np
    import pendulastic_pt_score as pt
    t, ang = _offset_invariance_probe()
    base = pt.compute_pt_params(t, ang)
    assert base, "probe signal must score"
    for off in (-15.0, -5.0, 5.0, 15.0):
        shifted = pt.compute_pt_params(t, ang + off)
        assert shifted, f"offset {off} made the trial unscoreable"
        for key in list(pt._PARAM_KEYS) + ["A0_deg", "A1_deg"]:
            a, b = base.get(key), shifted.get(key)
            if a is None or b is None or not (np.isfinite(a) and np.isfinite(b)):
                continue
            assert abs(b - a) <= 1e-6 + 1e-6 * abs(a), (
                f"{key} moved by {b - a:.3e} under a {off:+.0f} deg offset -- the "
                f"pose-free design assumes it cannot")


def test_the_composite_pt7_score_ignores_a_constant_angle_offset():
    """The number a clinician actually reads."""
    import numpy as np
    import pendulastic_pt_score as pt
    t, ang = _offset_invariance_probe()
    base = pt.compute_pt_score(pt.compute_pt_params(t, ang))
    for off in (-15.0, 15.0):
        shifted = pt.compute_pt_score(pt.compute_pt_params(t, ang + off))
        assert abs(shifted - base) <= 1e-6 + 1e-6 * abs(base), (
            f"pt7 moved {shifted - base:.3e} under a {off:+.0f} deg offset")


def test_release_detection_ignores_a_constant_angle_offset():
    """If the release index moved, every downstream parameter would move with
    it and the invariance argument would collapse. Measured across the real
    corpus: 0 moves in 872 offset scorings."""
    import numpy as np
    import pendulastic_pt_score as pt
    t, ang = _offset_invariance_probe()
    base = pt._detect_release(t, ang)
    for off in (-15.0, -5.0, 5.0, 15.0):
        assert pt._detect_release(t, ang + off) == base, f"release moved at {off:+.0f}"


# -- physical (time-based) smoothing window -------------------------------

def _pendulum_at(fs, dur=12.0, hold=2.0, A0=50.0, freq=0.9, decay=0.4):
    """One physical swing, sampled at fs. The MOTION is identical at every
    fs -- only the sampling changes -- so any PT parameter that moves with fs
    is measuring the filter, not the patient."""
    import numpy as np
    t = np.arange(0.0, dur, 1.0 / fs)
    swing = (130.0 + A0 * np.exp(-decay * (t - hold))
             * np.cos(2 * np.pi * freq * (t - hold)))
    return t, np.where(t < hold, 180.0, swing)


def test_sg_window_is_a_duration_not_a_sample_count():
    # The defect: _sg took a fixed sample COUNT, so the same 15-sample window
    # spanned 0.75 s of a 20 Hz IMU trace and 0.125 s of a 120 Hz OptiTrack
    # trace -- 75% of a swing period against 12% of one.
    import numpy as np
    import pendulastic_pt_score as p
    sig = np.zeros(400); sig[200] = 1.0
    n_50 = int((p._sg(sig, dt=1.0 / 50.0) != 0).sum())
    n_200 = int((p._sg(sig, dt=1.0 / 200.0) != 0).sum())
    # An impulse spreads over exactly the filter window, so the support IS the
    # window: 4x the rate must give ~4x the samples for the same duration.
    assert n_200 == pytest.approx(4 * n_50, rel=0.25), (n_50, n_200)


def test_sg_window_spans_the_configured_number_of_seconds():
    import numpy as np
    import pendulastic_pt_score as p
    for fs in (50.0, 100.0, 200.0):
        sig = np.zeros(600); sig[300] = 1.0
        support = int((p._sg(sig, dt=1.0 / fs) != 0).sum())
        assert support / fs == pytest.approx(p._SG_WINDOW_S, abs=0.03), fs


def test_sg_window_is_the_single_documented_knob():
    import pendulastic_pt_score as p
    assert p._SG_WINDOW_S == 0.10


def test_sg_falls_back_to_the_savgol_minimum_when_the_rate_is_too_low():
    # At 30 Hz a 0.10 s window is 3 samples, below savgol's polyorder+2
    # floor. It must widen to the minimum, not crash and not silently
    # return an unfiltered signal.
    import numpy as np
    import pendulastic_pt_score as p
    sig = np.zeros(200); sig[100] = 1.0
    out = p._sg(sig, dt=1.0 / 30.0, p=3)
    support = int((out != 0).sum())
    assert support >= 5
    assert not np.array_equal(out, sig)


def test_compute_pt_params_does_not_depend_on_sample_rate():
    # The parameters the smoothing window governs. Same swing at 50 Hz and
    # 200 Hz -- identical motion, only the sampling differs -- so anything
    # that moves here is measuring the filter rather than the patient.
    import pendulastic_pt_score as p
    slow = p.compute_pt_params(*_pendulum_at(50.0))
    fast = p.compute_pt_params(*_pendulum_at(200.0))
    assert slow is not None and fast is not None
    # omega_max_n and phi_max_ratio are both normalised by A0, so they were
    # excluded from this list while the release back-off was still counted in
    # samples. With that fixed they belong here: measured 1.58% and 1.13%.
    for k in ("omega_peak_deg_s", "N", "f", "R2n", "omega_max_n", "phi_max_ratio"):
        assert fast[k] == pytest.approx(slow[k], rel=0.05), (
            f"{k} moved {slow[k]:.4f} -> {fast[k]:.4f} on identical motion")


def test_peak_angular_velocity_survives_a_four_fold_change_in_sample_rate():
    # omega_peak is the quantity the fixed-sample window hurt most: it is the
    # peak of a numerical derivative, so its value was set by the filter
    # width. On the real corpus the same motion read 350 deg/s at 120 Hz and
    # 163 deg/s at 20 Hz. Tight tolerance on purpose -- this one is now
    # invariant to 0.3% over 50-400 Hz, and a regression here means the
    # window has gone back to being counted in samples.
    import pendulastic_pt_score as p
    slow = p.compute_pt_params(*_pendulum_at(50.0))
    fast = p.compute_pt_params(*_pendulum_at(200.0))
    assert fast["omega_peak_deg_s"] == pytest.approx(
        slow["omega_peak_deg_s"], rel=0.01)


def test_oscillation_count_is_the_same_at_20_hz_and_120_hz():
    # The rates the pipeline really runs at: the IMU replay grid and the
    # OptiTrack capture rate. With a 15-SAMPLE window these were a 0.75 s
    # and a 0.125 s filter, and the count of oscillations disagreed by a
    # median 16.7% across 143 real trials -- the same leg was scored as
    # having a different number of swings depending on which instrument
    # watched it. Measured 0.0% after the window became a duration.
    #
    # 20 Hz cannot realise a 0.10 s window (2 samples, below the savgol
    # floor), so this asserts the floor still leaves N invariant; it does
    # not claim the two rates are fully equivalent. omega does still differ
    # at 20 Hz, which is why the IMU replay grid moves off 20 Hz.
    #
    # Characterisation, not a regression guard: a synthetic pendulum is far
    # smoother than a real trace, so reverting _sg to a fixed sample count
    # does NOT break this test (verified by mutation). What actually catches
    # that reversion is test_sg_window_is_a_duration_not_a_sample_count.
    import numpy as np
    import pendulastic_pt_score as p
    rng = np.random.default_rng(11)
    def noisy(fs):
        t, a = _pendulum_at(fs)
        return t, a + rng.normal(0.0, 0.25, len(a))
    slow = p.compute_pt_params(*noisy(20.0))
    fast = p.compute_pt_params(*noisy(120.0))
    assert slow is not None and fast is not None
    assert slow["N"] == fast["N"], (slow["N"], fast["N"])


def test_a0_does_not_depend_on_sample_rate():
    """Was a strict xfail between 258ca60 and the release back-off fix.

    The smoothing window fixed what it governed -- omega_peak became
    invariant to 0.3% over 50-400 Hz -- but A0_deg kept drifting
    monotonically, 47.75 deg at 50 Hz down to 44.23 at 400 Hz, because
    _detect_release stepped back a fixed 2 SAMPLES from the threshold
    crossing and so reported a later release the faster the capture was.
    Making that back-off a duration took the A0 spread over the rates where
    the 0.10 s window is realisable from 7.8% to 1.7%, and this now holds at
    1.29% between 50 and 200 Hz.
    """
    import pendulastic_pt_score as p
    slow = p.compute_pt_params(*_pendulum_at(50.0))
    fast = p.compute_pt_params(*_pendulum_at(200.0))
    assert fast["A0_deg"] == pytest.approx(slow["A0_deg"], rel=0.02)


def test_release_detection_backoff_is_a_duration_not_a_sample_count():
    # _detect_release stepped back a fixed 2 SAMPLES from the threshold
    # crossing -- 0.040 s at 50 Hz but 0.005 s at 400 Hz -- so the faster the
    # capture, the later the release it reported. Same class of bug as the
    # smoothing window, and the reason A0_deg drifted 47.75 -> 44.23 deg
    # across 50-400 Hz on identical motion.
    import numpy as np
    import pendulastic_pt_score as p
    times = []
    for fs in (50.0, 120.0, 400.0):
        t, ang = _pendulum_at(fs)
        smoothed = p._sg(ang, dt=p._median_dt(t))
        times.append(float(t[p._detect_release(t, smoothed)]))
    assert max(times) - min(times) < 0.02, times


def test_release_backoff_matches_what_two_samples_meant_at_120_hz():
    # The duration is pinned to what the old constant meant at OptiTrack's
    # capture rate -- the reference instrument -- so the modality every other
    # one is validated against barely moves, and the rest come to it.
    import pendulastic_pt_score as p
    assert p._RELEASE_BACKOFF_S == pytest.approx(2.0 / 120.0)
