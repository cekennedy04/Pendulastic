"""Elbow outcome measures (plan 2026-11-10, U4)."""
import math

import numpy as np
import pytest

from neuromech.elbow_score import score_elbow

J, B, K = 0.07, 0.12, 3.0
DT = 0.001


def linear_trace(J=J, B=B, K=K, x0=40.0, rest=90.0, dur=12.0):
    wn = math.sqrt(K / J); zeta = B / (2 * math.sqrt(K * J))
    t = np.arange(0, dur, DT)
    if zeta < 1:
        wd = wn * math.sqrt(1 - zeta ** 2)
        x = x0 * np.exp(-zeta * wn * t) * (np.cos(wd * t) + zeta * wn / wd * np.sin(wd * t))
    else:
        r1 = -wn * (zeta - math.sqrt(zeta ** 2 - 1)); r2 = -wn * (zeta + math.sqrt(zeta ** 2 - 1))
        x = x0 * (r2 * np.exp(r1 * t) - r1 * np.exp(r2 * t)) / (r2 - r1)
    return t, rest + x, zeta, wn


def test_both_estimators_recover_zeta_and_k():
    t, q, zeta, wn = linear_trace()
    s = score_elbow(t, q, J=J)
    assert s.settled
    assert s.zeta_logdec == pytest.approx(zeta, rel=0.02)
    assert s.zeta_fit == pytest.approx(zeta, rel=0.02)
    assert s.K_logdec == pytest.approx(K, rel=0.05)
    assert s.K_fit == pytest.approx(K, rel=0.05)
    assert s.B_fit == pytest.approx(B, rel=0.05)
    assert not s.refusals


def test_mildly_overdamped_refuses_logdec_but_fits():
    t, q, zeta, _ = linear_trace(B=2 * math.sqrt(K * J) * 1.2)
    s = score_elbow(t, q, J=J)
    assert "logdec" in s.refusals and s.zeta_logdec is None
    assert s.zeta_fit >= 1.0
    assert s.zeta_fit == pytest.approx(zeta, rel=0.05)


def test_heavily_overdamped_refuses_both():
    """By the time it is inside the linear range only the slow mode is left: first order."""
    t, q, _, _ = linear_trace(B=2 * math.sqrt(K * J) * 2.0)
    s = score_elbow(t, q, J=J)
    assert s.zeta_logdec is None and s.zeta_fit is None
    assert "collinear" in s.refusals["fit"]


def test_truncated_trace_is_flagged_and_withholds_rest_measures():
    t, q, _, _ = linear_trace(dur=1.2)
    s = score_elbow(t, q, J=J)
    assert not s.settled
    for k in ("ri", "logdec", "fit", "swings_final"):
        assert k in s.refusals
    assert s.ri is None and s.zeta_fit is None


def test_ri_is_first_swing_over_release_distance():
    t, q, _, _ = linear_trace()
    s = score_elbow(t, q, J=J)
    first_peak = q[np.argmin(q[:int(1.0 / DT)])]       # releases above rest, first extremum below
    assert s.rest_deg == pytest.approx(90.0, abs=0.01)
    assert s.ri == pytest.approx((q[0] - first_peak) / (q[0] - s.rest_deg), rel=1e-4)
    assert 1.0 < s.ri < 2.0


def test_gravity_share_of_k_is_reported_separately():
    t, q, _, _ = linear_trace()
    s = score_elbow(t, q, J=J, k_gravity=1.8)
    assert s.K_tissue_fit == pytest.approx(s.K_fit - 1.8)
    assert s.K_tissue_logdec == pytest.approx(s.K_logdec - 1.8)


def test_without_j_b_and_k_are_refused():
    t, q, _, _ = linear_trace()
    s = score_elbow(t, q)
    assert "B_K" in s.refusals and s.K_fit is None
    assert s.zeta_fit is not None


def test_more_damping_fewer_swings():
    counts = [score_elbow(*linear_trace(B=b)[:2], J=J).swings for b in (0.05, 0.2, 0.5)]
    assert counts[0] > counts[1] > counts[2]


def test_small_noise_does_not_add_swings_or_bias():
    t, q, zeta, _ = linear_trace()
    clean = score_elbow(t, q, J=J)
    rng = np.random.default_rng(0)
    s = score_elbow(t, q + rng.normal(0, 0.05, q.shape), J=J)
    assert abs(s.swings - clean.swings) <= 1         # a peak at the threshold may flip, no more
    assert s.ri == pytest.approx(clean.ri, abs=0.01)
    assert s.zeta_fit == pytest.approx(zeta, rel=0.05)
    assert s.K_fit == pytest.approx(K, rel=0.05)
    assert s.zeta_logdec == pytest.approx(zeta, rel=0.10)


def test_quantised_trace_still_scores():
    t, q, zeta, _ = linear_trace()
    s = score_elbow(t, np.round(q, 1), J=J)
    assert s.ri == pytest.approx(score_elbow(t, q, J=J).ri, abs=0.01)
    assert s.zeta_fit == pytest.approx(zeta, rel=0.05)


def test_noise_near_threshold_refuses_everything():
    t, q, _, _ = linear_trace()
    s = score_elbow(t, q + np.random.default_rng(1).normal(0, 0.5, q.shape), J=J)
    assert "noise" in s.refusals and s.swings is None and s.ri is None


def test_hold_before_release_is_trimmed():
    t, q, zeta, _ = linear_trace()
    hold = np.full(200, q[0])
    s = score_elbow(np.arange(len(q) + 200) * DT, np.concatenate([hold, q]), J=J)
    clean = score_elbow(t, q, J=J)
    assert s.release_index == 200          # hold is 0..199; sample 200 is the release value
    assert s.ri == pytest.approx(clean.ri, abs=1e-3)
    assert s.swings == clean.swings
    assert s.zeta_fit == pytest.approx(zeta, rel=0.02)


def test_growing_oscillation_is_refused():
    t, q, _, _ = linear_trace(B=-0.01, dur=6.0)
    # force a settled-looking tail so the refusal comes from the growth, not from settling
    q = np.concatenate([q, np.full(3000, 90.0)]); t = np.arange(len(q)) * DT
    s = score_elbow(t, q, J=J)
    assert s.zeta_logdec is None and "logdec" in s.refusals
    assert s.B_fit is None or s.B_fit > 0


@pytest.mark.parametrize("tau", [0.8, 1.2])
def test_first_order_decay_refuses_the_fit(tau):
    """x = 30 exp(-t/tau): velocity is proportional to position, so K and B cannot be split."""
    t = np.arange(0, 15, DT)
    s = score_elbow(t, 90 + 30 * np.exp(-t / tau), J=J)
    assert s.settled
    assert s.zeta_fit is None and "fit" in s.refusals


def test_grow_then_die_refuses_logdec():
    t = np.arange(0, 14, DT)
    env = np.where(t < 3, 5 + 5 * t, 20 * np.exp(-(t - 3) / 1.5))
    q = 90 + env * np.sin(2 * np.pi * 1.0 * t)
    s = score_elbow(t, q, J=J)
    assert s.zeta_logdec is None and "logdec" in s.refusals


@pytest.mark.parametrize("Kx", [10.0, 30.0])
def test_fit_unbiased_at_higher_frequency(Kx):
    t, q, zeta, _ = linear_trace(K=Kx, B=0.12)
    s = score_elbow(t, q, J=J)
    assert s.K_fit == pytest.approx(Kx, rel=0.03)
    assert s.zeta_fit == pytest.approx(zeta, rel=0.05)


def test_creeping_tail_is_not_settled():
    t = np.arange(0, 12, DT)
    q = 90 + 30 * np.exp(-t / 3.0)
    s = score_elbow(t, q, J=J)
    assert not s.settled


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        score_elbow(np.arange(5.0), np.zeros(5))
    t, q, _, _ = linear_trace()
    q[100] = np.nan
    with pytest.raises(ValueError):
        score_elbow(t, q)


def test_no_grade_anywhere():
    t, q, _, _ = linear_trace()
    d = score_elbow(t, q, J=J).as_dict()
    assert not any(k.lower() in ("mas", "pt7", "pt_score", "band", "grade") for k in d)
