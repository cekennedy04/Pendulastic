"""A release with an early catch, replayed through the app (calibration.rs STALL_*).

The leg falls a few degrees, is arrested for a moment, then swings. Its first drop peaks under the app's
1.0 rad/s capture rate and settles below the 0.3 rad/s guard, which the release detector used to read as
handling: it revoked the hold, did not trust the swing that followed, and the trial came back "release
not found" (severity axis lambda 1.2 to 1.5, 2026-11-18). These recordings must be found, zeroed on the
hold pose and scored; a stall that is really handling must still be refused.

Needs node and a built webapp/src/wasm, like the second half of test_phone_trial.py.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import build as B  # noqa: E402
from neuromech import phone_trial as PT  # noqa: E402
from neuromech import scenarios as S  # noqa: E402

WASM = os.path.join(PT.ROOT, "webapp", "src", "wasm", "mobile_imu_core_bg.wasm")
needs_app = pytest.mark.skipif(not (PT.node_available() and os.path.exists(WASM)),
                               reason="needs node and a built webapp/src/wasm (npm run build:wasm)")


def caught_drop(peak_rad_s=0.8, drop_s=0.2, stall_s=0.12, swing_sign=1.0, rest_deg=45.0, duration=14.0):
    """(t, theta_deg): a half-sine first drop, a stall, then a damped pendulum to rest. `swing_sign=-1`
    sends the motion after the stall back up instead: the same stall, but handling."""
    t = np.arange(0, duration, 1 / PT.FS_SIM)
    rate = np.where(t < drop_s, peak_rad_s * np.sin(math.pi * t / drop_s), 0.0)       # rad/s, flexing
    first = -np.degrees(np.cumsum(rate) / PT.FS_SIM)
    ts = np.clip(t - drop_s - stall_s, 0, None)
    wn = 2 * math.pi * 0.9; zeta = 0.15; wd = wn * math.sqrt(1 - zeta ** 2)
    ring = 1 - np.exp(-zeta * wn * ts) * (np.cos(wd * ts) + zeta * wn / wd * np.sin(wd * ts))
    return t, first - swing_sign * (rest_deg + first[-1]) * ring


def _replay(tmp_path, t, th, name, **kw):
    tr = PT.synthesize(t, th, name=name, **kw)
    app = PT.run_app(PT.write(tr, str(tmp_path)), trajectory=True)
    live = next((x["t"] for x in app["transitions"] if x["state"] == "released"), None)
    return tr.truth, app, live


def test_the_first_drop_is_one_the_old_rule_called_handling():
    """The recording is the failing case, not an easier one: under the capture rate, over the guard for
    longer than a noise blip, and still again before the swing."""
    t, th = caught_drop()
    rate = np.radians(np.abs(np.gradient(th, t)))
    first = rate[t < 0.2]
    assert PT.GUARD_RAD_S < first.max() < PT.CAPTURE_RAD_S
    assert abs(th[int(0.2 * PT.FS_SIM)]) > 2.0                             # it has really moved
    assert rate[(t > 0.21) & (t < 0.31)].max() < PT.GUARD_RAD_S            # then stalls
    assert rate[t > 0.32].max() > PT.CAPTURE_RAD_S                         # and then swings


@needs_app
@pytest.mark.parametrize("phone", [PT.Phone.ideal(), PT.Phone()], ids=["ideal", "default"])
def test_a_caught_release_is_found_zeroed_on_the_hold_and_scored(tmp_path, phone):
    t, th = caught_drop()
    tu, app, live = _replay(tmp_path, t, th, "catch", phone=phone)
    assert live is not None and 0.0 <= live - tu["release_t_s"] <= 0.7, app["transitions"]
    assert app["scored"], app["error"]
    assert app["ended"] == "settled"
    # Zeroed on the hold pose, not where the capture rate was finally crossed: the hold reads 180 and
    # the swing is measured from it, so A0 is the whole drop.
    tt = np.array(app["trajectory"]["t"]); a = np.array(app["trajectory"]["angle_deg"], dtype=float)
    hold = a[(tt > tu["release_t_s"] - 2.0) & (tt < tu["release_t_s"] - 0.1)]
    assert abs(np.nanmedian(hold) - 180.0) < 1.0, np.nanmedian(hold)
    rest = a[tt > tt[-1] - 1.0]
    assert abs(np.nanmedian(rest) - (180.0 + th[-1])) < 1.0, np.nanmedian(rest)
    # A0 reads about 2 deg low: the SCORER finds its own release on the curve (8 % of the range, backed
    # off 0.1 s), which on a slow first drop lands a couple of degrees into it. Not the zero pose.
    assert abs(app["params"]["a0_deg"] - tu["expected_params"]["A0_deg"]) < 3.5
    assert abs(app["pt_score"]["score"] - tu["expected_pt"]) < 0.03


@needs_app
def test_the_severity_axis_past_its_catch_is_scored(tmp_path):
    """Lambda 1.4 of the published axis: the model's own early catch, 6 deg into the drop."""
    t, th, _, _ = B.render(S.Scenario("S-lam1.4", "S", "severity axis, lambda 1.4", S.severity(1.4)))
    tu, app, live = _replay(tmp_path, np.asarray(t), np.asarray(th), "lam14", phone=PT.Phone.ideal())
    assert live is not None and app["scored"], (app["error"], app["transitions"])
    assert abs(app["pt_score"]["score"] - tu["expected_pt"]) < 0.03


@needs_app
def test_a_stall_followed_by_motion_the_other_way_is_still_refused(tmp_path):
    t, th = caught_drop(swing_sign=-1.0, rest_deg=25.0)       # back up, 19 deg past the hold
    _, app, live = _replay(tmp_path, t, th, "back", phone=PT.Phone.ideal())
    assert live is None and not app["scored"] and app["error"] == "unscorable"
