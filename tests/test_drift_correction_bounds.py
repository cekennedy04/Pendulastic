"""The drift correction's fallback, and the invariant that caught it.

The settled-tail estimator was carefully bounded. The pre-release baseline fit
it falls back to had no bounds at all, which meant it was least restrained on
exactly the trials where the tail estimator had already declined to guess -- the
ones with the worst data. It fitted a slope to sub-second windows of marker
noise and extrapolated them across whole recordings.

That is not a cosmetic error. `neutral` is the tail median, so a correction that
drags the tail drags the reference A0 is measured from, and
`spasticity_grouping.classify_leg` thresholds A0 at 38.1 deg to LABEL a leg. An
inflated A0 moves the grouping variable, not an outcome, and moves it towards
looking healthy.
"""
import glob as _glob_mod
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import participant_paths as _pp
import pendulastic_pt_score as pts
from pendulastic_pt_score import compute_pt_params


# ── the baseline fallback is bounded now ─────────────────────────────────────

def _hold_then_swing(n=600, fps=120.0, hold_s=0.5, noise_deg=0.0, seed=0):
    """A held leg, then a released swing -- the shape every trial has."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fps
    hold_n = int(hold_s * fps)
    ang = np.zeros(n)
    tt = t[hold_n:] - t[hold_n]
    ang[hold_n:] = -40.0 * np.exp(-0.8 * tt) * np.cos(2 * np.pi * 0.9 * tt) + 40.0
    ang[hold_n:] -= 40.0
    if noise_deg:
        ang = ang + rng.normal(0.0, noise_deg, n)
    return t, ang


def test_a_noisy_hold_does_not_become_a_slope():
    """A held leg is not moving, so spread in that window is marker noise. The
    real failure: 0.53 s of hold carrying 15.6 deg of scatter fitted 12.8 deg/s
    and was extrapolated 27x its own fit window."""
    t, ang = _hold_then_swing(hold_s=0.5, noise_deg=5.0, seed=3)
    baseline_end = int(0.45 * 120.0)
    slope = pts._baseline_drift_slope(t, ang, baseline_end)
    assert slope is None, f"fitted {slope} deg/s to noise in a sub-second hold"


def test_too_short_a_hold_is_refused_however_clean():
    """The lever arm is what makes extrapolation dangerous, not the residual.
    A perfectly straight 0.4 s window is still a 0.4 s window."""
    t = np.linspace(0.0, 0.4, 50)
    ang = 2.0 * t                      # an immaculate 2 deg/s line
    assert pts._baseline_drift_slope(t, ang, len(t)) is None


def test_a_long_clean_hold_is_still_believed():
    """The guard must not become a blanket refusal -- a real hold with a real
    slope is exactly what this estimator is for."""
    t = np.linspace(0.0, 2.0, 240)
    ang = 0.8 * t
    slope = pts._baseline_drift_slope(t, ang, len(t))
    assert slope is not None
    assert slope == pytest.approx(0.8, abs=0.05)


def test_a_slope_beyond_the_drift_cap_is_refused():
    """_MAX_DRIFT_DEG_S bounded the tail path and not this one. Both estimators
    are claiming to measure the same physical thing, so both must respect it."""
    t = np.linspace(0.0, 2.0, 240)
    ang = (pts._MAX_DRIFT_DEG_S + 2.0) * t
    assert pts._baseline_drift_slope(t, ang, len(t)) is None


def test_too_few_baseline_samples_is_refused():
    t = np.linspace(0.0, 2.0, 5)
    assert pts._baseline_drift_slope(t, 0.5 * t, len(t)) is None


def test_nan_samples_do_not_reach_the_fit():
    """Low-coverage trials are the ones that land here."""
    t = np.linspace(0.0, 2.0, 240)
    ang = 0.8 * t
    ang[::3] = np.nan
    slope = pts._baseline_drift_slope(t, ang, len(t))
    assert slope is not None and np.isfinite(slope)


# ── the total-correction bound ───────────────────────────────────────────────

def test_a_correction_larger_than_the_curve_is_refused():
    """The bound that names the defect: a 'correction' moving the signal
    further than the signal goes is not removing a bias, it is rewriting it."""
    t = np.linspace(0.0, 14.0, 1400)
    ang = 30.0 * np.sin(2 * np.pi * 0.5 * t)          # 60 deg range
    assert pts._credible_drift_slope(3.5, t, ang) is None   # 49 deg over 14 s


def test_a_small_correction_passes():
    t = np.linspace(0.0, 10.0, 1000)
    ang = 30.0 * np.sin(2 * np.pi * 0.5 * t)          # 60 deg range
    assert pts._credible_drift_slope(0.8, t, ang) == pytest.approx(0.8)


def test_none_stays_none():
    t = np.linspace(0.0, 10.0, 1000)
    assert pts._credible_drift_slope(None, t, np.zeros_like(t)) is None
    assert pts._credible_drift_slope(np.nan, t, np.zeros_like(t)) is None


def test_the_settled_tail_estimator_is_deliberately_not_bounded_by_it():
    """Regression on a fix that first went too far.

    The tail estimator was tuned against 93 IMU trials by measured accuracy,
    and legitimately needs more than a quarter of a curve's range on the trials
    that drift most: a synthetic carrying 6 deg of true drift across a 27.7 deg
    swing fits 0.97 deg/s and needs 35% of the range to remove it. Bounding
    that path switched the correction off on exactly the trials it exists for,
    and broke test_neutral_deg_raw_is_undetrended_tail_median.
    """
    n, fps, drift = 300, 30.0, 6.0
    t = np.arange(n) / fps
    tt = t
    ang = 15.0 * np.exp(-0.5 * tt) * np.cos(2 * np.pi * 0.9 * tt)
    ang = ang + np.linspace(0.0, drift, n)
    p = compute_pt_params(t, ang, detrend=True)
    assert p is not None
    # The correction ran: the detrended tail median differs from the raw one.
    assert abs(p["neutral_deg_raw"] - p["neutral_deg"]) > 0.5


# ── the invariant ────────────────────────────────────────────────────────────

def test_an_amplitude_larger_than_its_curve_raises():
    t = np.linspace(0.0, 10.0, 100)
    ang = np.linspace(-35.0, 35.0, 100)          # 70 deg range
    with pytest.raises(pts.AmplitudeExceedsCurveError) as exc:
        pts._assert_amplitude_within_curve(169.8, ang, t)
    msg = str(exc.value)
    assert "169.8" in msg and "70.0" in msg
    assert "drift" in msg.lower(), "the message must name the usual cause"


def test_an_amplitude_inside_its_curve_is_silent():
    t = np.linspace(0.0, 10.0, 100)
    ang = np.linspace(-35.0, 35.0, 100)
    pts._assert_amplitude_within_curve(23.1, ang, t)     # must not raise


def test_smoothing_overshoot_is_tolerated_but_a_real_defect_is_not():
    t = np.linspace(0.0, 10.0, 100)
    ang = np.linspace(0.0, 100.0, 100)
    pts._assert_amplitude_within_curve(101.0, ang, t)          # ringing
    with pytest.raises(pts.AmplitudeExceedsCurveError):
        pts._assert_amplitude_within_curve(140.0, ang, t)      # a defect


def test_a_degenerate_curve_is_not_judged():
    """No range to compare against; the guard must not invent a verdict."""
    t = np.linspace(0.0, 10.0, 100)
    pts._assert_amplitude_within_curve(5.0, np.full(100, 3.0), t)
    pts._assert_amplitude_within_curve(5.0, np.full(100, np.nan), t)


def test_the_invariant_catches_the_defect_it_was_written_for(monkeypatch):
    """End to end: restore the unguarded fallback and the guard must fire.

    Without this, a later 'simplification' of the bounds would leave the
    invariant untested and the corpus unprotected.
    """
    def _legacy(t, ang, baseline_end, baseline_start=0):
        # main's fallback as it stood at 75109c5: the lift-trimmed window,
        # fitted with no guard at all.
        if baseline_end - baseline_start < 10:
            return None
        return float(np.polyfit(t[baseline_start:baseline_end],
                                ang[baseline_start:baseline_end], 1)[0])

    monkeypatch.setattr(pts, "_baseline_drift_slope", _legacy)
    monkeypatch.setattr(pts, "_credible_drift_slope", lambda s, t, a: s)

    path = _resolve("18", "Left", "trial_1_optitrack.csv")
    if path in (None, "MISSING"):
        pytest.skip("real corpus not present")
    t, ang, _q = pts.load_optitrack_detailed(path)
    with pytest.raises(pts.AmplitudeExceedsCurveError):
        compute_pt_params(np.asarray(t), np.asarray(ang))


# ── the two real trials that exposed it ──────────────────────────────────────

def _resolve(pid, side, filename):
    """Same contract as test_optitrack_marker_angle's resolver: None when the
    corpus is absent (a legitimate skip), "MISSING" when it is present but the
    trial cannot be found (a hard failure, so a rename cannot quietly disable
    a regression test)."""
    if not os.path.isdir(_pp.OPTI_ROOT):
        return None
    pdir = _pp.find_participant_dir(_pp.OPTI_ROOT, pid)
    if pdir is None:
        return "MISSING"
    hits = _glob_mod.glob(os.path.join(pdir, side, "**", filename), recursive=True)
    return hits[0] if hits else "MISSING"


_CORPUS = os.path.isdir(_pp.OPTI_ROOT)
_P18_LEFT_T1 = _resolve("18", "Left", "trial_1_optitrack.csv")
_P21_RIGHT_T1 = _resolve("21", "Right", "trial_1_optitrack.csv")


@pytest.mark.skipif(not _CORPUS, reason="real OptiTrack corpus not present")
@pytest.mark.parametrize("pid,side,path_ref", [
    ("18", "Left", "_P18_LEFT_T1"),
    ("21", "Right", "_P21_RIGHT_T1"),
])
def test_the_reported_trials_no_longer_exceed_their_own_curves(pid, side, path_ref):
    """MINT_18 Left trial_1 reported A0 169.8 on a curve spanning 70.3;
    CHAT_21 Right trial_1 reported 58.0 on a curve spanning 33.9. Both passed
    conditioning_verdict as ok, so neither was in the ill-conditioned
    population that the flagging change had just rescued -- they were in the
    accepted one, feeding the grouping variable."""
    path = globals()[path_ref]
    assert path != "MISSING", (
        f"corpus present but participant {pid}'s {side} trial_1 could not be "
        "resolved -- a rename must not silently disable this test")
    t, ang, _q = pts.load_optitrack_detailed(path)
    a = np.asarray(ang, dtype=float)
    span = float(np.nanmax(a) - np.nanmin(a))
    p = compute_pt_params(np.asarray(t), a)
    assert p is not None
    assert p["A0_deg"] <= span, f"A0={p['A0_deg']:.1f} span={span:.1f}"


@pytest.mark.skipif(not _CORPUS, reason="real OptiTrack corpus not present")
def test_the_correction_is_a_no_op_on_these_optical_curves():
    """Optical mocap has no integration drift -- there is no mechanism for it,
    which is why the module comment records OptiTrack tails at +0.009 deg/s.
    A correction of any size on one of these is the bug, not the fix."""
    if _P18_LEFT_T1 in (None, "MISSING"):
        pytest.skip("trial not resolvable")
    t, ang, _q = pts.load_optitrack_detailed(_P18_LEFT_T1)
    p = compute_pt_params(np.asarray(t), np.asarray(ang))
    assert p is not None
    assert abs(p["neutral_deg"] - p["neutral_deg_raw"]) < 1.0, (
        "the drift correction is still moving an optical curve")


# ── ported onto main (2026-11-10): the lift-aware window, and end to end ─────
#
# main's fallback fits [baseline_start, baseline_end) -- the still hold with the
# lift ramp trimmed off (f1584ae) -- where the September lineage fitted
# [0, baseline_end). The guards have to apply to that window, and the tests
# below fail on main before the port and pass after it.

def _creeping_hold_then_ringing_swing(hold_s, creep_deg_s, fps=100.0,
                                      swing_s=7.0, a0=50.0):
    """A hold that creeps towards 180 at `creep_deg_s`, then a swing still
    ringing when the recording stops (so the settled tail is unusable)."""
    n_hold = int(hold_s * fps)
    th = np.arange(n_hold) / fps
    hold = 180.0 + creep_deg_s * (th - th[-1])
    ts = np.arange(int(swing_s * fps)) / fps
    swing = 180.0 - a0 + a0 * np.exp(-0.25 * ts) * np.cos(2 * np.pi * 0.9 * ts)
    ang = np.concatenate([hold, swing])
    return np.arange(len(ang)) / fps, ang


def test_a_sub_second_hold_slope_no_longer_inflates_a0(monkeypatch):
    """0.6 s of hold creeping at 5 deg/s, extrapolated over 7.6 s, used to
    remove 38 deg from a curve and report A0 = 77.5 where the swing is 46.5."""
    monkeypatch.setattr(pts, "_settled_tail_drift_slope", lambda *a, **k: None)
    t, ang = _creeping_hold_then_ringing_swing(hold_s=0.6, creep_deg_s=5.0)
    plain = compute_pt_params(t, ang, detrend=False)
    corrected = compute_pt_params(t, ang, detrend=True)
    assert plain is not None and corrected is not None
    assert corrected["A0_deg"] == pytest.approx(plain["A0_deg"], abs=1e-9), (
        f"A0 {corrected['A0_deg']:.1f} with the fallback against "
        f"{plain['A0_deg']:.1f} without: a sub-second hold was extrapolated")
    # (neutral_deg is the tail median of the smoothed curve and neutral_deg_raw
    # of the unsmoothed one, so they agree to smoothing error, not exactly.)
    assert corrected["neutral_deg"] == pytest.approx(corrected["neutral_deg_raw"], abs=0.01)


def test_a_long_hold_whose_correction_would_rewrite_the_curve_is_refused(monkeypatch):
    """2 s of hold at 3 deg/s passes the window and the slope cap, but over the
    whole trial it displaces 27 deg of a 93.6 deg curve: past the 25% bound."""
    monkeypatch.setattr(pts, "_settled_tail_drift_slope", lambda *a, **k: None)
    t, ang = _creeping_hold_then_ringing_swing(hold_s=2.0, creep_deg_s=3.0)
    plain = compute_pt_params(t, ang, detrend=False)
    corrected = compute_pt_params(t, ang, detrend=True)
    assert plain is not None and corrected is not None
    assert corrected["A0_deg"] == pytest.approx(plain["A0_deg"], abs=1e-9)


def test_a_long_hold_with_modest_drift_is_still_corrected(monkeypatch):
    """The guard must not switch the fallback off: 2 s at 1 deg/s is a real
    window and a small correction (9% of the range), and it still applies."""
    monkeypatch.setattr(pts, "_settled_tail_drift_slope", lambda *a, **k: None)
    t, ang = _creeping_hold_then_ringing_swing(hold_s=2.0, creep_deg_s=1.0)
    plain = compute_pt_params(t, ang, detrend=False)
    corrected = compute_pt_params(t, ang, detrend=True)
    assert plain is not None and corrected is not None
    assert abs(corrected["neutral_deg"] - corrected["neutral_deg_raw"]) > 3.0, (
        "a credible 1 deg/s hold slope was not applied")


def test_the_guards_apply_to_the_lift_trimmed_window():
    """A 1.5 s ramp then a 0.5 s hold: the whole pre-release stretch is 2 s
    long, but the STILL part is half a second, and that is the window whose
    length must be judged."""
    fps = 100.0
    ramp = np.linspace(0.0, -55.0, int(1.5 * fps))
    hold = np.full(int(0.5 * fps), -55.0) + 0.02 * np.arange(int(0.5 * fps))
    ang = np.concatenate([ramp, hold])
    t = np.arange(len(ang)) / fps
    assert pts._baseline_drift_slope(t, ang, len(t), len(ramp)) is None
    # ...and a long enough still window after the same ramp is fitted on its
    # own, without the ramp's -36.7 deg/s leaking into the slope.
    hold2 = np.full(int(1.5 * fps), -55.0) + 0.01 * np.arange(int(1.5 * fps))
    ang2 = np.concatenate([ramp, hold2])
    t2 = np.arange(len(ang2)) / fps
    slope = pts._baseline_drift_slope(t2, ang2, len(t2), len(ramp))
    assert slope == pytest.approx(1.0, abs=1e-6)


def test_detrend_false_never_reaches_either_estimator(monkeypatch):
    """detrend=False is the app and rig convention; nothing in this change may
    run on that path."""
    def _boom(*a, **k):
        raise AssertionError("drift estimator consulted with detrend=False")
    monkeypatch.setattr(pts, "_settled_tail_drift_slope", _boom)
    monkeypatch.setattr(pts, "_baseline_drift_slope", _boom)
    monkeypatch.setattr(pts, "_credible_drift_slope", _boom)
    t, ang = _creeping_hold_then_ringing_swing(hold_s=0.6, creep_deg_s=5.0)
    assert compute_pt_params(t, ang, detrend=False) is not None
