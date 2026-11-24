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
from imu_flex_axis import FlexAxisEstimator, principal_axis, lateral_motion, MIN_COMMIT_SAMPLES


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


def _swing(off_axis_frac=0.0, amp=3.0, n=400):
    """A planar swing about +x, optionally contaminated out of plane."""
    t = np.linspace(0.0, 4.0, n)
    w = np.zeros((n, 3))
    w[:, 0] = amp * np.sin(2 * np.pi * t)
    w[:, 1] = off_axis_frac * amp * np.sin(2 * np.pi * t)
    return w


def test_a_planar_swing_reports_no_lateral_motion():
    r = lateral_motion(_swing(0.0), [1.0, 0.0, 0.0])
    assert r["lateral_fraction"] == pytest.approx(0.0, abs=1e-12)
    assert r["lateral_peak_deg_s"] == pytest.approx(0.0, abs=1e-9)


def test_lateral_fraction_matches_the_geometry_it_claims_to_measure():
    # A constant off-axis share f gives perp/total = f / sqrt(1 + f^2) exactly.
    for f in (0.1, 0.2, 0.5):
        r = lateral_motion(_swing(f), [1.0, 0.0, 0.0])
        assert r["lateral_fraction"] == pytest.approx(f / math.sqrt(1.0 + f * f), rel=1e-6)


def test_lateral_fraction_is_scale_free():
    # Doubling the swing rate must not change the SHARE that is off-plane.
    slow = lateral_motion(_swing(0.25, amp=2.0), [1.0, 0.0, 0.0])
    fast = lateral_motion(_swing(0.25, amp=8.0), [1.0, 0.0, 0.0])
    assert slow["lateral_fraction"] == pytest.approx(fast["lateral_fraction"], rel=1e-6)
    # The PEAK is a rate, so it does scale -- that is the point of reporting both.
    assert fast["lateral_peak_deg_s"] > 3.0 * slow["lateral_peak_deg_s"]


def test_the_axis_sign_does_not_change_the_answer():
    # principal_axis reports its axis up to sign; a metric that flipped with it
    # would be reporting the estimator's arbitrary convention as anatomy.
    pos = lateral_motion(_swing(0.3), [1.0, 0.0, 0.0])
    neg = lateral_motion(_swing(0.3), [-1.0, 0.0, 0.0])
    assert pos["lateral_fraction"] == pytest.approx(neg["lateral_fraction"], rel=1e-12)


def test_an_unnormalised_axis_is_normalised_rather_than_believed():
    unit = lateral_motion(_swing(0.3), [1.0, 0.0, 0.0])
    long = lateral_motion(_swing(0.3), [17.0, 0.0, 0.0])
    assert unit["lateral_fraction"] == pytest.approx(long["lateral_fraction"], rel=1e-12)


def test_not_measured_is_none_and_never_zero():
    # None ("no axis was committed") and 0.0 ("measured, perfectly planar") are
    # different claims. Collapsing them would report a trial nobody could
    # assess as a clean one.
    assert lateral_motion(_swing(0.3), None) is None
    assert lateral_motion(np.zeros((50, 3)), [1.0, 0.0, 0.0]) is None   # all below threshold
    assert lateral_motion(np.empty((0, 3)), [1.0, 0.0, 0.0]) is None
    assert lateral_motion(_swing(0.3), [0.0, 0.0, 0.0]) is None         # degenerate axis


def test_resting_noise_is_excluded_rather_than_counted_as_lateral():
    # At rest the gyro reads noise whose direction is uniformly random, so
    # every resting sample looks almost entirely "off-axis". Without the
    # threshold those samples dominate and a clean swing reads as contaminated.
    rng = np.random.default_rng(3)
    swing = _swing(0.0)
    rest = rng.normal(0.0, 0.02, (2000, 3))      # long, quiet tail
    both = np.vstack([swing, rest])
    r = lateral_motion(both, [1.0, 0.0, 0.0])
    assert r["lateral_fraction"] == pytest.approx(0.0, abs=1e-9), (
        "resting noise leaked into the lateral fraction"
    )


def test_a_swing_entirely_off_the_axis_reads_as_fully_lateral():
    w = np.zeros((400, 3))
    w[:, 1] = 3.0 * np.sin(2 * np.pi * np.linspace(0, 4, 400))
    r = lateral_motion(w, [1.0, 0.0, 0.0])
    assert r["lateral_fraction"] == pytest.approx(1.0, rel=1e-9)


# -- Whole-recording fit (the scored replay's rule, 2026-11-19) --------------

from imu_flex_axis import QUIET_STOP_S, QUIET_STOP_MIN_SAMPLES, QUIET_STOP_WEAK_S   # noqa: E402


def test_whole_recording_commits_only_on_finalize_and_is_not_levelled():
    est = FlexAxisEstimator.whole_recording()
    tilt = math.radians(20.0)             # inside the levelling guard
    v = 2.0 * np.array([math.cos(tilt), 0.0, math.sin(tilt)])
    for _ in range(200):
        est.update(v, gravity=[0.0, 0.0, 1.0])
    assert not est.committed and est.n_samples == 200
    est.finalize()
    assert est.committed and not est.leveled
    assert _angle_between(est.axis, v) < 1e-6
    before = est.axis.copy()
    est.finalize()
    assert np.array_equal(est.axis, before)


def test_whole_recording_outweighs_the_release_transient_the_opening_fit_keeps():
    true_axis = _unit([0.0, 1.0, 0.0])
    transient = np.tile(_unit([1.0, 0.2, 0.0]) * 3.0, (20, 1))
    swing = np.concatenate([_swing_burst(true_axis, n=80), -_swing_burst(true_axis, n=80, peak=4.0)])
    opening, whole = FlexAxisEstimator(), FlexAxisEstimator.whole_recording()
    for v in np.concatenate([transient, swing]):
        opening.update(v)
        whole.update(v)
    whole.finalize()
    assert _angle_between(opening.axis, true_axis) > 30.0
    assert _angle_between(whole.axis, true_axis) < 12.0


def test_finalize_without_a_qualifying_sample_leaves_no_axis():
    est = FlexAxisEstimator.whole_recording()
    est.update([0.1, 0.0, 0.0])
    est.finalize()
    assert est.axis is None and not est.committed


def test_a_single_qualifying_sample_gives_its_own_direction():
    est = FlexAxisEstimator.whole_recording()
    est.update([0.0, 0.0, -3.0])
    est.finalize()
    assert _angle_between(est.axis, [0.0, 0.0, 1.0]) < 1e-9
    assert est.axis[2] < 0.0              # sign pinned to the sample


def test_a_second_of_quiet_closes_the_fit_and_less_does_not():
    est = FlexAxisEstimator.whole_recording()
    est.quiet(5.0)                        # before any sample: not counted
    for _ in range(QUIET_STOP_MIN_SAMPLES):
        est.update([2.0, 0.0, 0.0])
    for _ in range(99):
        est.quiet(0.01)
    assert not est.closed
    est.update([2.0, 0.0, 0.0])           # a qualifying sample resets the quiet
    for _ in range(99):
        est.quiet(0.01)
    for bad in (float("nan"), -1.0, None, 0.0):
        est.quiet(bad)
    assert not est.closed
    est.quiet(0.01)
    assert est.closed and QUIET_STOP_S == 1.0
    est.update([0.0, 9.0, 0.0])           # the phone being taken off
    assert est.n_samples == QUIET_STOP_MIN_SAMPLES + 1
    est.finalize()
    assert _angle_between(est.axis, [1.0, 0.0, 0.0]) < 1e-9


def test_the_opening_fit_ignores_quiet_and_reset_reopens_a_closed_fit():
    opening = FlexAxisEstimator()
    opening.update([2.0, 0.0, 0.0])
    opening.quiet(10.0)
    assert not opening.closed
    whole = FlexAxisEstimator.whole_recording()
    for _ in range(QUIET_STOP_MIN_SAMPLES):
        whole.update([2.0, 0.0, 0.0])
    whole.quiet(2.0)
    assert whole.closed
    whole.reset()
    assert not whole.closed and whole.n_samples == 0
    whole.update([0.0, 2.0, 0.0])
    assert whole.n_samples == 1


def _catch_then_swing(est):
    """Let go with a sideways twitch, caught 0.15 s later, held 1.5 s, then
    the leg swings (independent review, 2026-11-19)."""
    for _ in range(12):
        est.update([0.5, 2.5, 0.0])       # the let-go, mostly about y
    for _ in range(150):
        if hasattr(est, "quiet"):
            est.quiet(0.01)               # the catch
    swing = np.concatenate([_swing_burst([1.0, 0.0, 0.0]), -_swing_burst([1.0, 0.0, 0.0], peak=4.0)])
    for v in swing:
        est.update(v)
    return swing


def test_a_catch_just_after_release_does_not_close_the_fit_on_the_transient():
    """The fit must take in as much of the swing after the catch as the
    opening rule does: it stops at the same 25 samples, and is no further
    from the swing's axis than the opening rule or than the transient."""
    whole, opening = FlexAxisEstimator.whole_recording(), FlexAxisEstimator()
    _catch_then_swing(whole)
    _catch_then_swing(opening)
    assert whole.closed and whole.n_samples == QUIET_STOP_MIN_SAMPLES == opening.n_samples
    whole.finalize()
    err = _angle_between(whole.axis, [1.0, 0.0, 0.0])
    assert err <= _angle_between(opening.axis, [1.0, 0.0, 0.0]) + 1e-9
    assert err < _angle_between([0.5, 2.5, 0.0], [1.0, 0.0, 0.0]) - 10.0


@pytest.mark.parametrize("n_swing", [1, 10, 20, 24, 25, 26, 49, 50, 51, 80])
def test_handling_after_a_weak_swing_gets_no_further_than_under_the_opening_rule(n_swing):
    """Pre-merge review, 2026-11-19 (F1): 2 rad/s about x for n samples, 1.5 s
    of quiet, then a second about y peaking at 4 rad/s. With no close below 50
    samples, n=49 gave an axis 90 deg off where the opening rule gave 0."""
    whole, opening = FlexAxisEstimator.whole_recording(), FlexAxisEstimator()
    for _ in range(n_swing):
        whole.update([2.0, 0.0, 0.0])
        opening.update([2.0, 0.0, 0.0])
    for _ in range(150):
        whole.quiet(0.01)
    assert whole.closed == (n_swing >= QUIET_STOP_MIN_SAMPLES)
    for i in range(100):
        v = [0.0, 4.0 * math.sin(math.pi * i / 100.0), 0.0]
        whole.update(v)
        opening.update(v)
    whole.finalize()
    assert whole.n_samples == max(n_swing, QUIET_STOP_MIN_SAMPLES) and opening.n_samples == QUIET_STOP_MIN_SAMPLES
    assert (_angle_between(whole.axis, [1.0, 0.0, 0.0])
            <= _angle_between(opening.axis, [1.0, 0.0, 0.0]) + 1e-9)
    if n_swing >= 20:
        assert _angle_between(whole.axis, [1.0, 0.0, 0.0]) < 5.0


def test_a_weak_fit_closes_after_the_longer_quiet():
    """Past QUIET_STOP_WEAK_S the handling is kept out entirely, which the
    opening rule (still short of its 25 samples) does not do."""
    whole, opening = FlexAxisEstimator.whole_recording(), FlexAxisEstimator()
    for _ in range(10):
        whole.update([2.0, 0.0, 0.0])
        opening.update([2.0, 0.0, 0.0])
    for _ in range(199):
        whole.quiet(0.01)
    assert not whole.closed and QUIET_STOP_WEAK_S == 2.0
    whole.quiet(0.02)
    assert whole.closed
    for _ in range(100):
        whole.update([0.0, 4.0, 0.0])
        opening.update([0.0, 4.0, 0.0])
    whole.finalize()
    assert whole.n_samples == 10
    assert _angle_between(whole.axis, [1.0, 0.0, 0.0]) < 1e-9
    assert _angle_between(opening.axis, [1.0, 0.0, 0.0]) > 60.0


def test_the_reversals_of_a_slow_swing_do_not_cap_the_fit():
    est = FlexAxisEstimator.whole_recording()
    for _ in range(20):
        for _ in range(3):
            est.update([2.0, 0.0, 0.0])
        for _ in range(90):
            est.quiet(0.01)               # under QUIET_STOP_S each time
    assert not est.closed and est.n_samples == 60


# -- fit_report: flags only, mirrored in mobile-imu-core/src/flex_axis.rs -----
#    (fit_report_tests there feeds the same sequences and expects the same
#    numbers; the REF_* fixtures pin the two ports on whole logs.)

_X, _Y, _STILL = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0)


def _feed(est, n, v, elapsed=True):
    """`n` samples 10 ms apart, as replay_trial drives the estimator."""
    v = np.asarray(v, dtype=float)
    for _ in range(n):
        if elapsed:
            est.elapsed(0.01)
        if float(np.linalg.norm(v)) < 1.0:
            est.quiet(0.01)
        else:
            est.update(v)


def _rec():
    from imu_flex_axis import FlexAxisEstimator
    return FlexAxisEstimator.whole_recording()


def test_an_opening_fit_or_an_empty_one_has_no_report():
    from imu_flex_axis import FlexAxisEstimator
    est = FlexAxisEstimator()
    _feed(est, 40, (2.0, 0.0, 0.0))
    assert est.fit_report() is None
    est = _rec()
    _feed(est, 300, _STILL)
    assert est.fit_report() is None


def test_a_clean_swing_raises_no_flag():
    est = _rec()
    for k in range(6):
        _feed(est, 45, (3.0 if k % 2 == 0 else -3.0, 0.0, 0.0))
        _feed(est, 30, _STILL)          # a slow reversal, 0.3 s
    _feed(est, 300, _STILL)
    rep = est.fit_report()
    assert rep["closed"] and rep["n_fit"] == 270 and rep["late_n"] == 0
    assert rep["max_span_s"] == pytest.approx(0.31, abs=1e-9)
    assert rep["flags"] == []


@pytest.mark.parametrize("n", [20, 23, 24])
def test_a_weak_fit_with_nothing_after_it_raises_no_flag(n):
    """The corpus has five such fits (20 to 24 samples, 2026-11-20). They are
    weak, not wrong, and must not fire."""
    est = _rec()
    _feed(est, n, (1.5, 0.0, 0.0))
    _feed(est, 400, _STILL)
    rep = est.fit_report()
    assert rep["closed"] and rep["n_fit"] == n and rep["flags"] == []


def test_brief_motion_then_a_long_hold_then_the_swing_is_flagged():
    """Known regression 1 of the stop rule (PR #130): the fit closes on the
    brief motion and never sees the swing. The flag says so; the axis is
    still wrong."""
    est = _rec()
    _feed(est, 8, (0.0, 1.6, 0.0))
    _feed(est, 220, _STILL)             # 2.2 s: past QUIET_STOP_WEAK_S
    _feed(est, 100, (3.0, 0.0, 0.0))
    est.finalize()
    rep = est.fit_report()
    assert (rep["n_fit"], rep["closed"], rep["late_n"]) == (8, True, 100)
    assert rep["flags"] == ["axis_fit_weak_then_motion", "motion_after_swing_settled"]
    assert rep["late_energy_ratio"] == pytest.approx(900.0 / (8 * 1.6 * 1.6), abs=1e-9)
    assert abs(float(est.axis[1])) > 0.999


def test_motion_before_the_quiet_second_is_up_is_flagged():
    """Known regression 2: the fit is still open and takes the second motion."""
    est = _rec()
    _feed(est, 26, (2.0, 0.0, 0.0))
    _feed(est, 90, _STILL)
    _feed(est, 100, (0.0, 3.0, 0.0))
    rep = est.fit_report()
    assert not rep["closed"] and rep["n_fit"] == 126 and rep["late_n"] == 0
    assert rep["max_span_s"] == pytest.approx(0.91, abs=1e-9)
    assert rep["flags"] == ["axis_fit_spans_quiet"]


def test_a_sample_dropout_inside_the_quiet_is_flagged():
    """Known regression 3: a gap in the stream adds 10 ms to the quiet clock
    (the bounded interval), and all of itself to the report."""
    est = _rec()
    _feed(est, 30, (2.0, 0.0, 0.0))
    _feed(est, 20, _STILL)
    est.elapsed(2.5)
    est.quiet(0.01)
    _feed(est, 100, (0.0, 3.0, 0.0))
    rep = est.fit_report()
    assert not rep["closed"] and rep["n_fit"] == 130
    assert rep["max_span_s"] == pytest.approx(2.71, abs=1e-9)
    assert rep["flags"] == ["axis_fit_spans_quiet"]


def test_handling_after_the_swing_is_flagged_by_its_energy():
    est = _rec()
    _feed(est, 100, (3.0, 0.0, 0.0))
    _feed(est, 150, _STILL)
    _feed(est, 2, (0.0, 1.0, 0.0))      # 2 / 900 of the fit's energy
    rep = est.fit_report()
    assert rep["closed"] and rep["late_n"] == 2 and rep["flags"] == []
    _feed(est, 50, (0.0, 2.0, 0.0))     # now 202 / 900
    rep = est.fit_report()
    assert rep["late_energy_ratio"] == pytest.approx(202.0 / 900.0, abs=1e-12)
    assert rep["flags"] == ["motion_after_swing_settled"]


def test_the_report_does_not_touch_the_fit():
    """With and without the report's clock the axis is the same to the bit,
    and reset clears the counters."""
    a, b = _rec(), _rec()
    seq = [(20, (1.7, 0.4, 0.1)), (130, _STILL), (9, (0.2, 2.2, 0.3)),
           (250, _STILL), (60, (0.3, 0.1, 2.9))]
    for n, v in seq:
        _feed(a, n, v)
        _feed(b, n, v, elapsed=False)
    a.finalize()
    b.finalize()
    assert np.array_equal(a.axis, b.axis)
    assert (a.n_samples, a.closed) == (b.n_samples, b.closed)
    assert a.fit_report()["late_n"] > 0
    a.reset()
    assert a.fit_report() is None
    _feed(a, 5, (2.0, 0.0, 0.0))
    rep = a.fit_report()
    assert (rep["n_fit"], rep["late_n"], rep["max_span_s"]) == (5, 0, 0.01)


def test_a_stray_sample_after_a_weak_fit_is_not_flagged():
    """Independent review of the flags, 2026-11-20: 24 samples, a long quiet,
    then ONE sample at the threshold used to read "the main movement came
    after it". The weak flag needs the same energy as the general one."""
    est = _rec()
    _feed(est, 24, (1.5, 0.0, 0.0))
    _feed(est, 300, _STILL)
    _feed(est, 1, (0.0, 1.0, 0.0))
    rep = est.fit_report()
    assert rep["closed"] and rep["n_fit"] == 24 and rep["late_n"] == 1
    assert rep["late_energy_ratio"] == pytest.approx(1.0 / 54.0, abs=1e-12)
    assert rep["flags"] == []
    _feed(est, 5, (0.0, 1.1, 0.0))      # 7.05 / 54: past the ratio
    assert est.fit_report()["flags"] == ["axis_fit_weak_then_motion", "motion_after_swing_settled"]


def test_motion_after_the_fit_closed_never_reaches_the_axis():
    """The late-motion branch only counts. The axis after any amount of late
    motion is the axis of an estimator that never saw it, to the bit, and bad
    input after the close is ignored as it was before the report existed."""
    a, b = _rec(), _rec()
    for est in (a, b):
        _feed(est, 60, (1.7, 0.4, 0.1))
        _feed(est, 150, _STILL)
    assert a.closed and b.closed
    _feed(a, 400, (0.2, 5.0, 0.3))
    for bad in ("abc", None, [1.0, 2.0], [float("nan"), 0.0, 0.0]):
        a.update(bad)
    a.finalize()
    b.finalize()
    assert np.array_equal(a.axis, b.axis) and a.n_samples == b.n_samples == 60
    assert a.fit_report()["late_n"] == 400 and b.fit_report()["late_n"] == 0
