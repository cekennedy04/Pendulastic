"""Group K: HESS HM#1-2 with H refitted to the knee grade at their intended MAS.

Scored the way the library is (build.render + build.score, the production
scorer, detrend off). H sits mid-band, so each must also hold its grade with
H 5 % off either way: a model or scorer change that moves either toward a band
edge, or toward the arrest jump, shows up here before it reaches a rig.
"""
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from neuromech import build as B  # noqa: E402
from neuromech import scenarios as S  # noqa: E402

INTENDED = {"K-HM1": "1", "K-HM2": "1+"}


@pytest.fixture(scope="module")
def group_k():
    return {sc.sid: sc for sc in S.build({}) if sc.group == "K"}


def _grade(sc, h_factor=1.0):
    if h_factor != 1.0:
        sc = dataclasses.replace(sc, params=dataclasses.replace(sc.params, hess_H=sc.params.hess_H * h_factor))
    t, th, rest, tq = B.render(sc)
    prm, est = B.score(t, th, sc)
    return est


def test_group_k_is_hm1_and_hm2(group_k):
    assert sorted(group_k) == sorted(INTENDED)


@pytest.mark.parametrize("sid", sorted(INTENDED))
@pytest.mark.parametrize("h_factor", (0.95, 1.0, 1.05))
def test_each_holds_its_intended_band(group_k, sid, h_factor):
    est = _grade(group_k[sid], h_factor)
    assert est["interpretable"], est["reason"]
    assert est["mas"] == INTENDED[sid]
