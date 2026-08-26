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

# --- peak separation --------------------------------------------------------


def test_detected_extrema_cannot_be_closer_than_the_old_merge_threshold():
    """Why the quadriceps-catch merge step was removed rather than kept.

    _merge_close_extrema() existed to merge sub-peaks closer than fps/6 apart,
    on the theory that a spastic quadriceps catch makes find_peaks report one
    peak as two. It never ran: the same call already passes
    distance=max(3, fps/3.5), and find_peaks guarantees its output is at least
    that far apart. fps/3.5 exceeds fps/6 at every sample rate, so nothing was
    ever close enough to merge, and there was no test covering it.

    This pins the guarantee, so that lowering the distance parameter in future
    -- which WOULD make sub-peak merging necessary again -- fails here loudly
    instead of silently changing what gets counted as an oscillation.
    """
    import numpy as np
    from scipy.signal import find_peaks

    for fps in (20, 30, 50, 60, 100, 120, 200):
        min_dist = max(3, int(fps / 3.5))
        old_merge_sep = max(3, int(fps / 6))
        assert min_dist >= old_merge_sep, (
            "at %d fps find_peaks would allow extrema %d samples apart, "
            "closer than the %d-sample merge threshold -- sub-peak merging "
            "would be needed again" % (fps, min_dist, old_merge_sep))

        # and empirically, on a signal deliberately full of sub-peaks
        t = np.linspace(0, 4, int(fps * 4), endpoint=False)
        noisy = np.sin(2 * np.pi * 1.5 * t) + 0.3 * np.sin(2 * np.pi * 18 * t)
        idx, _ = find_peaks(noisy, distance=min_dist)
        if len(idx) > 1:
            assert int(np.min(np.diff(idx))) >= min_dist
