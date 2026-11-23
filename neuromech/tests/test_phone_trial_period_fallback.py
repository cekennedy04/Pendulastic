"""A heavily damped swing on a real phone mount, replayed through the app (scorer period-estimate fallback).

A phone strapped 25 cm below the knee reads the settled angle about 0.4 deg away from where a phone at the
joint would. On swings whose second extremum sits near the scorer's 5 % amplitude floor, that was enough to
leave the first peak pass (height above the SETTLED angle) with fewer than three extrema, which switched
swing-centring off: the app showed PT7 1.23 where the same swing scores 0.39, with N 0.5 for 2.5, `f`
unmeasured and area_ratio 0.62 for 0.15 (2026-11-18). It is not sensor noise: every noise term alone, and
all of them together at the joint, score 0.37.

Needs node, a built webapp/src/wasm, and the scenario traces (`python -m neuromech.build`).
"""
import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import build as B  # noqa: E402
from neuromech import phone_trial as PT  # noqa: E402

WASM = os.path.join(PT.ROOT, "webapp", "src", "wasm", "mobile_imu_core_bg.wasm")
SIDS = ("A-P1p", "B-mixed", "M-tone_L4")


def _trace(sid):
    return os.path.join(B.OUT, "traces", f"{sid}.npz")


needs_app = pytest.mark.skipif(
    not (PT.node_available() and os.path.exists(WASM) and all(os.path.exists(_trace(s)) for s in SIDS)),
    reason="needs node, a built webapp/src/wasm (npm run build:wasm) and python -m neuromech.build")


def _replay(tmp_path, sid, phone, seed=0):
    t, th = PT.scenario(sid)
    tr = PT.synthesize(t, th, name=sid, phone=phone, seed=seed)
    return tr.truth, PT.run_app(PT.write(tr, str(tmp_path)))


@needs_app
@pytest.mark.parametrize("sid", SIDS)
@pytest.mark.parametrize("seed", [0, 11])
def test_a_damped_swing_scores_the_same_on_a_mounted_phone(tmp_path, sid, seed):
    tu, app = _replay(tmp_path, sid, PT.Phone(), seed)
    assert app["scored"], app["error"]
    assert abs(app["pt_score"]["score"] - tu["expected_pt"]) < 0.05, (app["pt_score"]["score"], tu["expected_pt"])
    assert app["params"]["n"] >= 2.0                      # was 0.5: the swing's cycles are counted again
    assert app["params"]["area_ratio"] < 0.25             # was 0.62: the swing is centred again


@needs_app
@pytest.mark.parametrize("sid", SIDS)
def test_the_lever_arm_alone_no_longer_moves_the_score(tmp_path, sid):
    """The mount, with a perfect sensor: this alone reproduced the 1.2."""
    ideal = PT.Phone.ideal()
    _, at_joint = _replay(tmp_path, sid, ideal)
    mounted = dataclasses.replace(ideal, lever_m=PT.Phone().lever_m)
    _, on_shank = _replay(tmp_path, sid, mounted)
    assert at_joint["scored"] and on_shank["scored"]
    assert abs(at_joint["pt_score"]["score"] - on_shank["pt_score"]["score"]) < 0.05
