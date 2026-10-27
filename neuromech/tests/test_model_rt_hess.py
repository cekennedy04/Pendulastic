"""The HESS three-phase catch (Park, Kim & Damiano 2012) added to model_rt.

Each test pins one equation of the paper as the module reads it: the onset
angle falls with stretch speed (eq. 2/6), the catch lasts D degrees (eq. 4),
the peak is H times the onset speed and the residual Q times that (eq. 3), and
post-catch resistance grows with stretch (eq. 5). The first test keeps the
module's promise to the reference: off by default, no torque at all.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import model_rt as M  # noqa: E402

#: Onset well inside the linear part of eq. 2: a 60 deg intercept on a 0 deg
#: floor never clamps on a ~180 deg/s drop.
LINEAR = dict(hess_L_deg=60.0, hess_min_deg=0.0)


def _run(duration=2.0, **kw):
    r = M.simulate(M.Params(**kw), duration=duration, record_torques=True)
    return r["t"], r["theta_deg"], r["torques"]["hess"]


def _onset(h):
    i = int(np.argmax(h > 0))
    assert h[i] > 0, "the catch never fired"
    return i


def test_off_by_default():
    assert M.Params().hess_H == 0.0
    _, _, h = _run()
    assert np.all(h == 0.0)


@pytest.mark.parametrize("integrator", ["rk4", "euler4"])
def test_catch_fires_and_slows_the_drop(integrator):
    p = M.Params(hess_H=2.8, hess_Q=0.6, hess_D_deg=30.0, **LINEAR)
    base = M.simulate(M.Params(), duration=1.5, integrator=integrator)["theta_deg"]
    hess = M.simulate(p, duration=1.5, integrator=integrator)["theta_deg"]
    assert hess.min() > base.min() + 5.0, "a catch should cut the first drop short"


def test_onset_angle_falls_with_speed():
    """Eq. 2: a faster stretch catches sooner. A push at release raises the mean speed."""
    kw = dict(hess_H=2.0, **LINEAR)
    stretch = []
    for w0 in (0.0, -1.5):
        r = M.simulate(M.Params(**kw), omega0=w0, duration=1.0, record_torques=True)
        i = _onset(r["torques"]["hess"])
        stretch.append(-r["theta_deg"][i])
    assert stretch[1] < stretch[0] - 3.0, f"pushed onset {stretch[1]:.1f} not earlier than dropped {stretch[0]:.1f}"


def test_no_catch_below_the_speed_gate():
    _, _, h = _run(hess_H=2.0, hess_wL_dps=1000.0, **LINEAR)
    assert np.all(h == 0.0)


def test_peak_is_H_times_onset_speed_and_residual_is_Q_of_it():
    H, Q = 2.0, 0.4
    t, th, h = _run(hess_H=H, hess_Q=Q, hess_D_deg=60.0, hess_kpost=0.0, **LINEAR)
    i = _onset(h)
    w_onset = -math.radians(th[i] - th[i - 1]) / M.DT
    peak = h[i: i + 50].max()
    assert peak == pytest.approx(H * w_onset, rel=0.02)
    after = h[i + int(0.15 / M.DT)]       # past the 0.1 s plateau, still in the catch
    assert after == pytest.approx(Q * peak, rel=1e-9)


def test_catch_lasts_D_degrees():
    """Eq. 4's T_c = D / mean catch speed is D degrees of stretch past onset."""
    D = 12.0
    # a 10 ms peak, so the plateau ends well before D degrees go by
    _, th, h = _run(hess_H=1.0, hess_Q=0.5, hess_D_deg=D, hess_kpost=50.0, hess_tpeak_s=0.01, **LINEAR)
    i = _onset(h)
    residual = 0.5 * h[i]
    # post-catch is the first rise above the residual, once the peak is over
    k = i + 20
    j = k + int(np.argmax(h[k:] > residual + 1e-6))
    assert (th[i] - th[j]) == pytest.approx(D, abs=1.0)


def test_post_catch_resistance_grows_with_stretch():
    """Eq. 5: with k_post on, torque climbs above the residual; with it off, it never does."""
    kw = dict(hess_H=1.0, hess_Q=0.3, hess_D_deg=5.0, hess_tpeak_s=0.01, **LINEAR)
    _, _, h0 = _run(hess_kpost=0.0, **kw)
    _, _, h1 = _run(hess_kpost=20.0, **kw)
    i0, i1 = _onset(h0), _onset(h1)
    assert h0[i0 + 20:].max() == pytest.approx(0.3 * h0[i0], rel=1e-9)
    assert h1[i1 + 20:].max() > 2.0 * 0.3 * h1[i1]


def test_torque_relaxes_and_the_leg_returns_to_rest():
    p = M.Params(hess_H=3.8, hess_Q=0.8, hess_D_deg=10.0, **LINEAR)
    r = M.simulate(p, duration=14.0, record_torques=True)
    h = r["torques"]["hess"]
    assert h[-1] < 1e-6 * h.max()
    assert np.median(r["theta_deg"][-2000:]) == pytest.approx(M.passive_rest_deg(p), abs=0.5)


def test_resists_the_drop_but_never_reverses_it():
    """HESS's MAS-3 row at face value: 14.7 N.m against ~5 N.m of gravity. Ungated it threw
    the leg back past full extension; as a resistance it acts only while the quadriceps lengthen."""
    p = M.Params(hess_H=3.8, hess_Q=0.8, hess_D_deg=10.0, hess_L_deg=60.0)
    r = M.simulate(p, duration=3.0, record_torques=True)
    w = np.gradient(r["theta_deg"], M.DT)
    h = r["torques"]["hess"]
    assert r["theta_deg"][100:].max() < 0.0, "the catch pushed the leg back past release"
    assert np.all(h[w > 5.0] < 1e-3), "torque while the leg was extending"


def test_one_shot_on_the_first_drop():
    """The catch never re-arms. Later flexion swings meet only the decaying residual,
    so each later burst of torque is smaller than the one before, and all are small."""
    from scipy.signal import find_peaks
    _, _, h = _run(duration=8.0, hess_H=2.0, hess_Q=0.5, hess_D_deg=20.0, **LINEAR)
    late = h[int(1.5 / M.DT):]
    pk, _ = find_peaks(late)
    assert late.max() < 0.05 * h.max()
    assert np.all(np.diff(late[pk]) < 0)
