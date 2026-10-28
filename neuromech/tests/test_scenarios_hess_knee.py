"""Group K: HESS HM#1-3 with H refitted to the knee grade at their intended MAS.

Scored the way the library is (build.render + build.score, the production
scorer, detrend off). H was bisected to each band centre, so a model change
that moves any of them out of band shows up here before it reaches a rig.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from neuromech import build as B  # noqa: E402
from neuromech import scenarios as S  # noqa: E402

INTENDED = {"K-HM1": "1", "K-HM2": "1+", "K-HM3": "2"}


@pytest.fixture(scope="module")
def group_k():
    return {sc.sid: sc for sc in S.build({}) if sc.group == "K"}


def test_group_k_is_hm1_to_hm3(group_k):
    assert sorted(group_k) == sorted(INTENDED)


@pytest.mark.parametrize("sid", sorted(INTENDED))
def test_each_lands_on_its_intended_band(group_k, sid):
    sc = group_k[sid]
    t, th, rest, tq = B.render(sc)
    prm, est = B.score(t, th, sc)
    assert est["interpretable"], est["reason"]
    assert est["mas"] == INTENDED[sid]
    assert abs(est["pt7"] - S.MAS_TARGETS[INTENDED[sid]]) < 0.03
