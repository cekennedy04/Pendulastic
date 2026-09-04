# tests/test_pt_score_oscillation_window.py
"""The oscillation-counting window, and the noise floor that guards it.

Two defects, measured 2026-09-03/04 against known ground truth:

1. `_ACTIVE_WINDOW_CAP_SEC = 4.0` truncated counting, so N reported 3.5 for
   true cycle counts of 4, 6 and 8 alike. Both cohort arms sat at ~91% of
   their own `4 * f` ceiling, which made N a measurement of frequency x window
   rather than of damping -- and made it redundant with `f`, which carries its
   own 1/7 of the composite.

2. The cap was masking a second problem rather than fixing it. On a
   no-rebound trial with a noisy settled tail, both a 4 s and a 10 s window
   fabricate cycles that are not there -- N read 8-12 where the truth is 0 --
   because `min_amp` is derived from A0 alone and knows nothing about how
   noisy the trace is.

Ground truth is known by construction in every test here.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from pendulastic_pt_score import compute_pt_params  # noqa: E402

FS = 100.0


def _swing(n_cycles, A0=47.0, freq=0.9, tail_s=6.0, noise=0.3, seed=7):
    """A decaying swing with `n_cycles` cycles clearing the amplitude floor,
    then a quiet settled tail. Returns (t, angle, true_N)."""
    rng = np.random.default_rng(seed)
    hold = np.full(int(1.0 * FS), A0)
    dur = n_cycles / freq
    ti = np.arange(0, dur, 1 / FS)
    min_amp = max(1.0, 0.05 * A0)
    zeta = -np.log(min_amp * 1.15 / A0) / (2 * np.pi * n_cycles)
    motion = A0 * np.exp(-zeta * 2 * np.pi * freq * ti) * np.cos(2 * np.pi * freq * ti)
    tail = np.zeros(int(tail_s * FS))
    ang = np.concatenate([hold, motion, tail]).astype(float)
    ang += rng.normal(0, noise, size=ang.shape)
    return np.arange(len(ang)) / FS, ang, float(n_cycles)


def _no_rebound(A0=47.0, tail_s=20.0, noise=2.5, seed=11):
    """The original bug's own case: the leg falls and never comes back up, so
    the true oscillation count is zero however long the recording runs."""
    rng = np.random.default_rng(seed)
    hold = np.full(int(1.0 * FS), A0)
    ti = np.arange(0, 2.0, 1 / FS)
    motion = A0 * (1 - ti / 2.0) ** 2
    tail = np.zeros(int(tail_s * FS))
    ang = np.concatenate([hold, motion, tail]).astype(float)
    ang += rng.normal(0, noise, size=ang.shape)
    return np.arange(len(ang)) / FS, ang


@pytest.mark.parametrize("true_n", [2.0, 4.0, 6.0, 8.0])
def test_oscillation_count_is_not_truncated_by_the_active_window(true_n):
    """N must track the real cycle count, not the window length.

    Under the 4 s cap this returned 3.5 for true_n of 4, 6 and 8 alike --
    a ceiling, not a measurement. Published healthy legs oscillate ~9 times,
    which a 4 s window can never represent at ~1 Hz.
    """
    t, ang, truth = _swing(true_n)
    params = compute_pt_params(t, ang)
    assert params is not None, "synthetic swing failed to score"
    assert params["N"] == pytest.approx(truth, abs=0.75), (
        f"true {truth} cycles reported as N={params['N']}")


def test_a_noisy_non_oscillating_trial_does_not_fabricate_cycles():
    """A leg that falls without rebounding has zero oscillations.

    With a noisy tail this reported N = 8-12 once the window was widened,
    because the amplitude floor is derived from A0 and ignores trace noise.
    """
    t, ang = _no_rebound(tail_s=20.0, noise=2.5)
    params = compute_pt_params(t, ang)
    if params is None:
        return                      # refusing the trial outright is also correct
    assert params["N"] <= 1.0, (
        f"fabricated {params['N']} cycles on a trial with no rebound")


def test_the_noise_floor_is_inert_on_clean_data():
    """The guard must not cost anything on ordinary recordings.

    The phone's settled-tail sd runs 0.09-0.38 deg across the corpus, so at
    0.2 deg the noise-aware floor must not change a clean count.
    """
    t, ang, truth = _swing(6.0, noise=0.2)
    params = compute_pt_params(t, ang)
    assert params is not None
    assert params["N"] == pytest.approx(truth, abs=0.75)


def test_a_long_quiet_tail_does_not_add_cycles():
    """The regression the 4 s cap was originally added to prevent.

    Same motion, tails from 3 s to 30 s: the count must not grow with the
    length of the silence after the swing.
    """
    counts = []
    for tail in (3.0, 10.0, 20.0, 30.0):
        t, ang, truth = _swing(4.0, tail_s=tail)
        params = compute_pt_params(t, ang)
        assert params is not None, f"failed to score at tail={tail}s"
        counts.append(params["N"])
    assert max(counts) - min(counts) <= 0.75, (
        f"N drifted with tail length: {counts}")


# ── angular velocity estimator ────────────────────────────────────────────────
#
# omega_max_n is a `nanmax(|omega|)`, and a maximum over a noisy trace is biased
# UPWARD, so an estimator that reports a higher peak is not thereby more
# faithful. Measured against a closed-form truth, the old estimator
# (`np.gradient` then smooth -- a finite difference, then an attempt to clean up
# the noise it just amplified) over-read the true peak by +2.0% at realistic
# sensor noise and +10.2% at 1 deg. A Savitzky-Golay analytic derivative fits a
# local polynomial to the angle and differentiates it exactly, giving +0.5% and
# +2.5% at the same noise levels with lower variance.

def _analytic_swing(fs, A0=47.0, freq=0.9, zeta=0.06, dur=10.0, noise=0.25,
                    seed=5):
    """A damped cosine whose peak angular velocity is known in closed form."""
    rng = np.random.default_rng(seed)
    w = 2 * np.pi * freq
    t = np.arange(0, dur, 1 / fs)
    phi = A0 * np.exp(-zeta * w * t) * np.cos(w * t)
    fine = np.arange(0, dur, 1e-5)
    d = -A0 * np.exp(-zeta * w * fine) * w * (zeta * np.cos(w * fine)
                                              + np.sin(w * fine))
    true_peak = float(np.max(np.abs(d)))
    hold = np.full(int(1.0 * fs), A0)
    ang = np.concatenate([hold, phi]) + rng.normal(
        0, noise, size=len(hold) + len(phi))
    return np.arange(len(ang)) / fs, ang, true_peak, A0


@pytest.mark.parametrize("noise,max_bias_pct", [
    (0.25, 0.60),   # finite-difference pipeline measured +0.95%
    (0.50, 1.20),   # measured +2.67%
    (1.00, 3.00),   # measured +6.39%
    (2.00, 6.00),   # measured +14.67% -- optical trials reach this noise at p90
])
def test_peak_angular_velocity_is_not_inflated_by_trace_noise(noise, max_bias_pct):
    """Averaged over repeats the peak must sit near the truth, not above it.

    A max over a noisy trace collects the largest noise excursion, so the old
    finite-difference estimator's bias grew with noise: +0.95% at 0.25 deg,
    +6.4% at 1 deg, +14.7% at 2 deg. Optical trials carry a settled-tail SD of
    2.26 deg at p90, so that last row is not a hypothetical.
    """
    fs = 100.0
    errs, truth = [], None
    for seed in range(12):
        t, ang, true_peak, _ = _analytic_swing(fs, noise=noise, seed=seed)
        truth = true_peak
        params = compute_pt_params(t, ang)
        if params:
            errs.append(params["omega_peak_deg_s"] / true_peak - 1.0)
    assert len(errs) >= 10
    bias = float(np.mean(errs)) * 100.0
    assert bias <= max_bias_pct, (
        f"peak omega biased {bias:+.2f}% at {noise} deg noise "
        f"(budget {max_bias_pct}%)")


def test_peak_angular_velocity_precision_does_not_degrade_with_noise():
    """Spread across repeats must stay tight enough to be clinically usable."""
    fs = 100.0
    peaks = []
    for seed in range(12):
        t, ang, _, _ = _analytic_swing(fs, noise=1.0, seed=seed)
        params = compute_pt_params(t, ang)
        if params:
            peaks.append(params["omega_peak_deg_s"])
    assert len(peaks) >= 10
    cv = float(np.std(peaks, ddof=1) / np.mean(peaks)) * 100.0
    assert cv <= 2.0, f"peak omega CV {cv:.2f}% across identical-truth repeats"
