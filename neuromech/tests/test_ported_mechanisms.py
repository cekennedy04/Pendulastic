"""The mechanisms ported from rig_neuromuscular_model (plan B9) must behave as that module says.

Each test is a signature the source module documents, not a tuned number: a tonic hold pins
the first stop whatever the release; a velocity catch fires on a full drop and not on a low
one; an end-stop truncates the overshoot; the capsule stops hyperextension; critical damping
arrives without overshoot. The scored tests check the port keeps the source's grades.
"""
import os
import sys
from dataclasses import replace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import build  # noqa: E402
from neuromech import scenarios as S  # noqa: E402
from neuromech.model_rt import (Params, critically_damped, hold_for_arrest, passive_rest_deg,  # noqa: E402
                                simulate)

PASSIVE = Params(capsule=True)
CATCH = replace(PASSIVE, hold_nm=20.0, hold_gvel=0.03, hold_vthr=180.0, hold_rise=0.015, hold_fall=0.020,
                adapt_kappa=0.98, adapt_tau=0.04)


def _first_stop_knee(p, theta0_deg=0.0, **kw):
    th = simulate(p, theta0_deg=theta0_deg, duration=4.0, **kw)["theta_deg"]
    i = next(i for i in range(1, len(th) - 1) if th[i] < th[i - 1] and th[i] <= th[i + 1])
    return 180.0 + th[i]


def test_ported_modules_are_off_by_default():
    a = simulate(Params(tone=2.0, k_f=0.75, k_df=0.025), duration=6.0)["theta_deg"]
    b = simulate(Params(tone=2.0, k_f=0.75, k_df=0.025, hold_nm=0.0, es_k=0.0, capsule=False), duration=6.0)["theta_deg"]
    assert np.array_equal(a, b)


@pytest.mark.parametrize("knee", [140.0, 132.0])
def test_hold_arrests_where_asked_and_stays(knee):
    th = simulate(hold_for_arrest(knee, PASSIVE), duration=8.0)["theta_deg"]
    assert abs(180.0 + np.median(th[-2000:]) - knee) < 0.5
    assert np.ptp(th[-3000:]) < 0.5, "no ring-down: the last 3 s must be flat"


def test_hold_pins_the_first_stop_whatever_the_release():
    """The source's INVARIANT signature: release height barely moves where a tonic hold stops the limb."""
    p = hold_for_arrest(140.0, PASSIVE)
    stops = [_first_stop_knee(p, theta0_deg=th0) for th0 in (0.0, -10.0, -20.0)]
    assert np.ptp(stops) < 1.0, stops
    free = [_first_stop_knee(PASSIVE, theta0_deg=th0) for th0 in (0.0, -20.0)]
    assert np.ptp(free) > 10.0, free     # without the hold the first stop moves a lot


def test_adaptation_lets_a_hold_give():
    """kappa = 0 is a hold that never releases; kappa near 1 catches at the same place, then gives."""
    hold = hold_for_arrest(140.0, PASSIVE)
    r0 = simulate(hold, duration=8.0)
    r1 = simulate(replace(hold, adapt_kappa=0.9, adapt_tau=0.5), duration=8.0)
    at = lambda r, s: 180.0 + np.interp(s, r["t"], r["theta_deg"])
    assert abs(at(r1, 0.6) - at(r0, 0.6)) < 1.0          # the catch is unchanged
    assert at(r0, 8.0) - at(r1, 8.0) > 4.0, (at(r0, 8.0), at(r1, 8.0))   # then it gives


def test_hold_refuses_an_arrest_past_rest():
    rest_knee = 180.0 + passive_rest_deg(PASSIVE)
    with pytest.raises(ValueError):
        hold_for_arrest(rest_knee - 2.0, PASSIVE)


def test_velocity_catch_is_discontinuous_in_release_height():
    """The source's DISCONTINUOUS signature: a full drop crosses the velocity threshold, a low one does not."""
    full = _first_stop_knee(CATCH) - _first_stop_knee(PASSIVE)
    low = _first_stop_knee(CATCH, theta0_deg=-20.0) - _first_stop_knee(PASSIVE, theta0_deg=-20.0)
    assert full > 20.0, full    # caught well above the passive first stop
    assert abs(low) < 3.0, low  # below threshold: barely fires


def test_velocity_catch_gives_and_the_swing_continues():
    th = simulate(CATCH, duration=8.0)["theta_deg"]
    rel = th - np.median(th[-1000:])
    turns = [i for i in range(1, len(rel) - 1) if (rel[i] - rel[i - 1]) * (rel[i + 1] - rel[i]) < 0 and abs(rel[i]) > 2.0]
    assert len(turns) >= 3, len(turns)


def test_end_stop_truncates_the_first_overshoot():
    p = replace(PASSIVE, es_k=0.5, es_deg=80.0, es_w=8.0, es_b=4.0)
    assert _first_stop_knee(p) - _first_stop_knee(PASSIVE) > 8.0
    # a wall the swing never reaches changes nothing
    far = replace(PASSIVE, es_k=0.5, es_deg=105.0, es_w=4.0, es_b=4.0)
    assert np.max(np.abs(simulate(far, duration=6.0)["theta_deg"] - simulate(PASSIVE, duration=6.0)["theta_deg"])) < 1e-9


def test_capsule_stops_hyperextension():
    """The case the source added it for: an extensor torque larger than gravity at extension
    drives the knee THROUGH full extension. The reference's soft wall lets it settle far past 0;
    the capsule holds it within a few degrees (McFaull & Lamontagne's end-range magnitude)."""
    off = np.median(simulate(Params(tone=10.0), duration=4.0)["theta_deg"][-500:])
    on = np.median(simulate(replace(PASSIVE, tone=10.0), duration=4.0)["theta_deg"][-500:])
    assert off > 15.0 and 0.0 < on < 5.0, (off, on)


def test_critical_damping_arrives_without_overshoot():
    p = critically_damped(PASSIVE)
    th = simulate(p, duration=10.0)["theta_deg"]
    assert th.min() > passive_rest_deg(PASSIVE) - 0.5


def test_ported_profiles_keep_the_source_grades():
    """Scored through the one scoring call: the port reproduces the source's grades and the confound."""
    lib = {s.sid: s for s in S.build({"0": .1663, "1": .3789, "1+": .5004, "2": .5872, "3": .6536}) if s.group == "H"}
    want = {"H-catch_and_hold_140": "4", "H-catch_and_hold_132": "4", "H-fall_no_ringdown": "4", "H-catch_release": "2"}
    got = {}
    for sid, mas in want.items():
        t, th = build.render(lib[sid])[:2]
        _prm, est = build.score(t, th, lib[sid])
        got[sid] = (str(est["mas"]), est["pt7"])
    assert {k: v[0] for k, v in got.items()} == want, got
    # same intended severity (2), different mechanism: PT7 spreads by more than 3x
    assert got["H-catch_and_hold_132"][1] / got["H-catch_release"][1] > 3.0, got
