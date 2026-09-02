"""windowed_pt_params: orientation independence, and missing-vs-zero.

Both behaviours here were defects that produced plausible numbers rather than
errors, which is why they survived to reach a results table.

The optical loader returns a RELATIVE curve since 87d7645 -- its sense comes
from a hinge eigenvector and is arbitrary -- so roughly half of all trials
arrive inverted. windowed_pt_params computed A0 = phi_0 - phi_inf, assumed an
extension-positive convention, and on an inverted curve got a NEGATIVE A0,
tripped its minimum-amplitude gate, and returned a full set of zeros. Those
zeros were then averaged into IMU-vs-OptiTrack ICC as observations: 70 of 119
trials in the 2026-09-02 paper table, taking R2n from 0.226 to 0.037.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import workbench_engine as engine


def _pendulum(n_s=8.0, fs=120.0, a0=40.0, freq=0.9, decay=0.5):
    t = np.arange(0, n_s, 1.0 / fs)
    return t, a0 * np.exp(-decay * t) * np.cos(2 * np.pi * freq * t)


# ── orientation independence ─────────────────────────────────────────────────

def test_an_inverted_curve_scores_the_same_as_an_upright_one():
    """The whole point: the reconstruction's polarity is arbitrary, so it must
    not decide whether a trial is measurable."""
    t, ang = _pendulum()
    up = engine.windowed_pt_params(t, ang)
    down = engine.windowed_pt_params(t, -ang)
    for k in engine._PT_PARAM_KEYS:
        assert up[k] == pytest.approx(down[k], rel=1e-6, abs=1e-9), k


def test_an_inverted_curve_is_not_silently_dropped():
    """The failure being fixed: negative A0 -> gate -> a full set of zeros that
    downstream could not tell from a measurement."""
    t, ang = _pendulum()
    down = engine.windowed_pt_params(t, -ang)
    assert not all(v == 0.0 for v in down.values())
    assert all(np.isfinite(v) for v in down.values())


def test_orientation_independence_holds_at_several_amplitudes():
    for a0 in (8.0, 20.0, 55.0, 90.0):
        t, ang = _pendulum(a0=a0)
        up = engine.windowed_pt_params(t, ang)
        down = engine.windowed_pt_params(t, -ang)
        for k in engine._PT_PARAM_KEYS:
            assert up[k] == pytest.approx(down[k], rel=1e-6, abs=1e-9), (a0, k)


def test_a_constant_offset_still_does_not_matter():
    """The curve is relative, so an offset is meaningless and must not move a
    parameter -- checked alongside polarity because both come from the same
    'no absolute zero' property of the reconstruction."""
    t, ang = _pendulum()
    base = engine.windowed_pt_params(t, ang)
    for shift in (-180.0, -30.0, 45.0, 180.0):
        got = engine.windowed_pt_params(t, ang + shift)
        for k in engine._PT_PARAM_KEYS:
            assert got[k] == pytest.approx(base[k], rel=1e-6, abs=1e-9), (shift, k)


# ── missing is not zero ──────────────────────────────────────────────────────

def test_too_short_a_series_is_nan_not_zero():
    got = engine.windowed_pt_params(np.arange(5) / 120.0, np.zeros(5))
    assert all(np.isnan(v) for v in got.values())


def test_a_curve_with_no_swing_is_nan_not_zero():
    """A flat curve has nothing to measure. Reporting 0.0 for every parameter
    claims it was measured and found to be zero, which is a different and much
    more confident statement than 'no measurement'."""
    t = np.arange(0, 8, 1 / 120.0)
    got = engine.windowed_pt_params(t, np.zeros_like(t))
    assert all(np.isnan(v) for v in got.values())


def test_nan_propagates_through_an_aggregate():
    """Why NaN rather than zero: an average over unmeasurable trials must come
    out unmeasurable, not report the agreement of a constant."""
    t = np.arange(0, 8, 1 / 120.0)
    missing = engine.windowed_pt_params(t, np.zeros_like(t))
    t2, ang = _pendulum()
    real = engine.windowed_pt_params(t2, ang)
    vals = np.array([missing["R2n"], real["R2n"]], dtype=float)
    assert np.isnan(np.mean(vals))
    assert np.isfinite(np.nanmean(vals))


def test_a_real_swing_is_still_measured():
    """The guard must not have become a blanket refusal."""
    t, ang = _pendulum()
    got = engine.windowed_pt_params(t, ang)
    assert all(np.isfinite(v) for v in got.values())
    assert got["N"] > 0
    assert got["f"] == pytest.approx(0.9, abs=0.15)


def test_the_amplitude_floor_is_applied_to_the_oriented_curve():
    """A swing below the floor is unmeasurable either way up -- the floor is
    about there being no swing, not about which way it points."""
    t = np.arange(0, 8, 1 / 120.0)
    tiny = 1.0 * np.exp(-0.5 * t) * np.cos(2 * np.pi * 0.9 * t)
    assert all(np.isnan(v) for v in engine.windowed_pt_params(t, tiny).values())
    assert all(np.isnan(v) for v in engine.windowed_pt_params(t, -tiny).values())
