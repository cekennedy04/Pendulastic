# tests/test_imu_flex_axis.py
"""Flex-axis capture: the estimator that replaced single-sample capture.

Background (measured on Participant_19, all 8 trials): capturing the axis
from the first gyro sample over threshold landed 58-86 deg away from the
true swing axis. Because the swing angle is recovered as
|theta * dot(u, flex_axis)|, that scaled every reported angle by its cosine
-- the pipeline returned 25-67% of the true excursion while the raw gyro
integral over the same swing recovered 116-143% of it.

These tests are synthetic on purpose: they must run without Recordings/,
which is patient data and not present in every checkout.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from imu_flex_axis import FlexAxisEstimator, principal_axis, MIN_COMMIT_SAMPLES


def _unit(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def _swing_burst(axis, n=80, peak=6.0, off_axis_first=None, noise=0.0, seed=0):
    """A half-swing of angular velocity along `axis` (a sine hump, as a real
    pendulum produces), optionally preceded by one off-axis release-transient
    sample -- the sample the old rule would have captured."""
    rng = np.random.default_rng(seed)
    axis = _unit(axis)
    out = []
    if off_axis_first is not None:
        out.append(_unit(off_axis_first) * peak * 0.35)
    for i in range(n):
        w = peak * math.sin(math.pi * (i + 1) / (n + 1))
        v = axis * w
        if noise:
            v = v + rng.normal(0.0, noise, 3)
        out.append(v)
    return np.array(out)


def _angle_between(a, b):
    d = abs(float(np.dot(_unit(a), _unit(b))))
    return math.degrees(math.acos(min(1.0, d)))


# ── The regression this whole change exists for ───────────────────────────

def test_recovers_true_axis_despite_off_axis_release_transient():
    """The defect, reproduced: the first over-threshold sample points ~70 deg
    off the swing axis. The old rule froze on exactly that sample; the
    estimator must instead converge on the direction the swing actually
    runs along."""
    true_axis = _unit([0.0, 1.0, 0.0])
    transient = _unit([0.94, 0.34, 0.0])          # ~70 deg off true_axis
    burst = _swing_burst(true_axis, off_axis_first=transient, noise=0.05)

    # What the old single-sample rule would have captured:
    old_axis = _unit(burst[0])
    assert _angle_between(old_axis, true_axis) > 45.0, "fixture must be badly off-axis"

    est = FlexAxisEstimator(threshold=1.0)
    for v in burst:
        est.update(v)

    assert est.committed
    err = _angle_between(est.axis, true_axis)
    assert err < 5.0, f"estimated axis {err:.1f} deg off the true swing axis"


def test_recovered_axis_restores_the_lost_amplitude():
    """State the fix in the units that matter. Projected angle scales by
    cos(axis error): the old capture kept cos(~70 deg) ~ 0.34 of the swing,
    the estimator must keep essentially all of it."""
    true_axis = _unit([0.0, 1.0, 0.0])
    transient = _unit([0.94, 0.34, 0.0])
    burst = _swing_burst(true_axis, off_axis_first=transient, noise=0.05)

    old_retained = abs(float(np.dot(_unit(burst[0]), true_axis)))
    est = FlexAxisEstimator(threshold=1.0)
    for v in burst:
        est.update(v)
    new_retained = abs(float(np.dot(est.axis, true_axis)))

    assert old_retained < 0.5, "fixture should reproduce the amplitude loss"
    assert new_retained > 0.99, f"only {new_retained:.3f} of the swing retained"


# ── Behavioural contract ──────────────────────────────────────────────────

def test_sub_threshold_samples_are_ignored():
    """Noise and handling below the capture threshold must not enter the
    estimate -- that is what the threshold is for."""
    est = FlexAxisEstimator(threshold=1.0)
    for _ in range(200):
        est.update([0.01, 0.02, -0.01])
    assert est.axis is None
    assert not est.committed
    assert est.n_samples == 0


def test_provisional_axis_is_available_before_commit():
    """There must never be a window with no axis at all: until the estimate
    commits, the first qualifying sample stands in, so early behaviour
    matches the old capture rather than falling back to un-projected angle."""
    est = FlexAxisEstimator(threshold=1.0)
    first = _unit([1.0, 0.0, 0.0]) * 3.0
    est.update(first)
    assert est.axis is not None
    assert not est.committed
    assert _angle_between(est.axis, first) < 1e-6


def test_commit_latches_and_ignores_later_samples():
    """Once committed the axis is frozen for the trial: a later burst in a
    different direction (the leg being repositioned, the next swing) must not
    silently redefine flexion mid-trial."""
    est = FlexAxisEstimator(threshold=1.0)
    for v in _swing_burst([0.0, 1.0, 0.0], n=MIN_COMMIT_SAMPLES + 20):
        est.update(v)
    assert est.committed
    settled = est.axis.copy()

    for v in _swing_burst([1.0, 0.0, 0.0], n=200, peak=9.0):
        est.update(v)
    assert np.allclose(est.axis, settled)


def test_reset_clears_state_between_trials():
    est = FlexAxisEstimator(threshold=1.0)
    for v in _swing_burst([0.0, 1.0, 0.0], n=MIN_COMMIT_SAMPLES + 5):
        est.update(v)
    assert est.committed
    est.reset()
    assert est.axis is None and not est.committed and est.n_samples == 0


def test_sign_is_pinned_to_the_first_qualifying_sample():
    """Eigenvector sign is arbitrary; the projection takes an absolute value
    so it cannot change the angle, but the stored axis must be reproducible
    rather than flipping run to run."""
    axis = _unit([0.3, -0.8, 0.5])
    burst = _swing_burst(axis, n=MIN_COMMIT_SAMPLES + 10, noise=0.02)
    axes = []
    for _ in range(3):
        est = FlexAxisEstimator(threshold=1.0)
        for v in burst:
            est.update(v)
        axes.append(est.axis)
    assert np.allclose(axes[0], axes[1]) and np.allclose(axes[1], axes[2])
    assert float(np.dot(axes[0], _unit(burst[0]))) > 0.0


def test_malformed_samples_do_not_corrupt_the_estimate():
    """A NaN or wrong-shaped reading off the wire must be skipped, not
    poison the accumulator -- this runs inside the live server's gyro path."""
    est = FlexAxisEstimator(threshold=1.0)
    est.update([float("nan"), 1.0, 2.0])
    est.update([1.0, 2.0])
    assert est.n_samples == 0
    for v in _swing_burst([0.0, 0.0, 1.0], n=MIN_COMMIT_SAMPLES + 5):
        est.update(v)
    assert est.committed
    assert _angle_between(est.axis, [0.0, 0.0, 1.0]) < 5.0


# ── Batch helper used by the offline analysis ─────────────────────────────

def test_principal_axis_matches_the_online_estimator():
    axis = _unit([0.2, 0.9, -0.3])
    burst = _swing_burst(axis, n=60, noise=0.03)
    batch = principal_axis(burst, threshold=1.0)
    est = FlexAxisEstimator(threshold=1.0)
    for v in burst:
        est.update(v)
    assert batch is not None
    assert _angle_between(batch, est.axis) < 5.0


def test_principal_axis_returns_none_without_enough_motion():
    assert principal_axis(np.zeros((50, 3)), threshold=1.0) is None
    assert principal_axis(np.zeros((0, 3)), threshold=1.0) is None


# ── Gravity levelling ─────────────────────────────────────────────────────

def test_axis_is_levelled_onto_the_horizontal_plane():
    """The knee flexes about a medio-lateral, near-horizontal axis. A swing
    whose measured axis is tilted out of that plane must have the tilt
    projected out -- on P19 the raw principal axis sits 8-20 deg off
    horizontal, and removing it cut cross-trial spread by 38%."""
    gravity = _unit([0.0, 0.0, 1.0])
    tilted = _unit([1.0, 0.0, 0.35])          # ~19 deg above horizontal
    burst = _swing_burst(tilted, n=MIN_COMMIT_SAMPLES + 15, noise=0.02)

    est = FlexAxisEstimator(threshold=1.0)
    for v in burst:
        est.update(v, gravity=gravity)

    assert est.committed and est.leveled
    assert abs(float(np.dot(est.axis, gravity))) < 1e-6, "axis must lie in the horizontal plane"
    assert _angle_between(est.axis, [1.0, 0.0, 0.0]) < 3.0


def test_levelling_is_skipped_when_the_axis_is_far_from_horizontal():
    """A near-vertical axis means the setup is not the seated sagittal
    geometry this assumes -- reclined participant, abducted leg, odd phone
    mounting. Levelling there would destroy the estimate, so it is skipped
    and the unconstrained axis kept."""
    gravity = _unit([0.0, 0.0, 1.0])
    steep = _unit([0.2, 0.0, 1.0])            # ~79 deg from horizontal
    est = FlexAxisEstimator(threshold=1.0)
    for v in _swing_burst(steep, n=MIN_COMMIT_SAMPLES + 10, noise=0.02):
        est.update(v, gravity=gravity)

    assert est.committed
    assert not est.leveled
    assert _angle_between(est.axis, steep) < 5.0


def test_without_gravity_the_axis_is_unchanged():
    """Gravity is optional -- callers with no accelerometer reading yet must
    still get the plain principal axis, not a crash or a None."""
    axis = _unit([0.4, 0.9, 0.15])
    burst = _swing_burst(axis, n=MIN_COMMIT_SAMPLES + 10, noise=0.02)
    est = FlexAxisEstimator(threshold=1.0)
    for v in burst:
        est.update(v)
    assert est.committed and not est.leveled
    assert _angle_between(est.axis, axis) < 5.0


def test_malformed_gravity_is_ignored():
    gravity_bad = [float("nan"), 0.0, 1.0]
    axis = _unit([1.0, 0.0, 0.0])
    est = FlexAxisEstimator(threshold=1.0)
    for v in _swing_burst(axis, n=MIN_COMMIT_SAMPLES + 5):
        est.update(v, gravity=gravity_bad)
    assert est.committed and not est.leveled
    assert _angle_between(est.axis, axis) < 5.0
