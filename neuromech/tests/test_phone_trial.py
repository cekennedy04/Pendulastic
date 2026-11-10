"""Synthetic whole-trial phone recordings (neuromech.phone_trial), and the app's reading of them.

The first half checks the generator on its own: the recording has the shape of a real trial, is in the
app's raw export format, and its copies of the app's gates still match the Rust source.

The second half replays recordings through the app's worker and wasm core (webapp/scripts/replay-trial.mjs),
so it needs node and a built webapp/src/wasm (`npm run build:wasm` in webapp/); it is skipped without them.
Tests marked xfail are things the app gets wrong today. They are strict: fixing the app turns each one
into a failure that says to remove the mark.
"""
import json
import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import phone_trial as PT  # noqa: E402

ROOT = PT.ROOT
CORE = os.path.join(ROOT, "mobile-imu-core", "src")
WASM = os.path.join(ROOT, "webapp", "src", "wasm", "mobile_imu_core_bg.wasm")
needs_app = pytest.mark.skipif(not (PT.node_available() and os.path.exists(WASM)),
                               reason="needs node and a built webapp/src/wasm (npm run build:wasm)")


def _rust_const(file, name):
    m = re.search(rf"pub const {name}: f64 = ([0-9.eE+-]+);", open(os.path.join(CORE, file), encoding="utf-8").read())
    assert m, f"{name} not found in {file}"
    return float(m.group(1))


def test_the_mirrored_gates_match_the_rust_core():
    assert PT.GUARD_RAD_S == _rust_const("stillness.rs", "ZERO_CAPTURE_GUARD_RAD_S")
    assert PT.CAPTURE_RAD_S == _rust_const("calibration.rs", "FLEX_CAPTURE_THRESHOLD")
    assert PT.CALM_WINDOW_S == pytest.approx(0.95 * _rust_const("stillness.rs", "GYRO_BIAS_WINDOW_S"))
    assert PT.MAX_HOLD_DRIFT_DEG == _rust_const("session.rs", "MAX_HOLD_DRIFT_DEG")
    assert PT.SETTLE_TARGET_S == _rust_const("session.rs", "SETTLE_TARGET_S")
    assert PT.LATERAL_THRESHOLD == _rust_const("flex_axis.rs", "DEFAULT_THRESHOLD")


def test_a_recording_is_a_whole_trial():
    tr = PT.synthesize(*PT.pendulum(), phone=PT.Phone.ideal())
    tu = tr.truth; t = tr.t_s; rate = np.linalg.norm(tr.gyro, axis=1)
    hold = (t > tu["hold_start_s"] + 0.05) & (t < tu["release_t_s"])
    assert tu["release_t_s"] - tu["hold_start_s"] == pytest.approx(3.0)
    assert rate[hold].max() < 1e-9                                        # a genuinely still hold
    assert np.allclose(np.linalg.norm(tr.accel[hold], axis=1), PT.G)      # reading gravity and nothing else
    assert rate[t < tu["hold_start_s"]].max() > PT.GUARD_RAD_S            # the lift is handling ...
    assert rate[t < tu["hold_start_s"]].max() < PT.CAPTURE_RAD_S          # ... and never looks like a release
    k = tu["release_sample"]
    assert t[k - 1] < tu["release_t_s"] <= t[k]
    assert rate[k:k + 30].max() > PT.CAPTURE_RAD_S                        # the release is unmistakable
    assert t[-1] - tu["calm_from_s"] > PT.SETTLE_TARGET_S                 # enough quiet tail to end itself
    assert tu["expect"]["ends_itself"] and tu["expected_params"]["A0_deg"] == pytest.approx(55.0, abs=1.5)


def test_it_is_written_in_the_apps_export_format(tmp_path):
    tr = PT.synthesize(*PT.pendulum(duration=4.0), name="fmt")
    lines = [json.loads(l) for l in open(PT.write(tr, str(tmp_path)), encoding="utf-8")]
    assert len(lines) == 2 * len(tr.t_s)
    for a, g in zip(lines[0::2], lines[1::2]):                            # accel first: the fusion reads it
        assert (a["sensor"], g["sensor"]) == ("accel", "gyro") and a["t"] == g["t"]
        assert set(a) == {"t", "role", "sensor", "v", "phone_ts_ms"} and a["role"] == "distal"
        assert isinstance(a["phone_ts_ms"], int) and len(a["v"]) == 3
    assert json.load(open(tmp_path / "fmt.truth.json"))["release_sample"] == tr.truth["release_sample"]


def test_a_defect_that_would_not_break_the_apps_rule_is_refused():
    with pytest.raises(ValueError):
        PT.synthesize(*PT.pendulum(), defects=("no_such_defect",))
    for d in ("early_release", "moved_in_hold"):                          # these build, so the rule IS broken
        assert PT.synthesize(*PT.pendulum(), defects=(d,)).truth["expect"]["scored"] is False


# ─────────────────────────────── through the app ───────────────────────────────

def _run(tmp_path, name="t", **kw):
    tr = PT.synthesize(*PT.pendulum(), name=name, **kw)
    app = PT.run_app(PT.write(tr, str(tmp_path)), trajectory=True)
    return tr.truth, app, {r["check"]: r for r in PT.check(tr.truth, app)}


@needs_app
def test_a_good_trial_started_in_the_hold_is_found_ended_and_scored(tmp_path):
    tu, app, rows = _run(tmp_path, phone=PT.Phone.ideal(), lift=False)
    for c in ("hold accepted at release", "live release", "scored", "scored release", "ends itself", "capture quality",
              "N", "f", "PT score", "no lateral motion"):
        assert rows[c]["ok"], rows[c]
    assert app["exported_lines"] == 2 * app["samples_used"]               # the raw log is what was replayed


@needs_app
@pytest.mark.parametrize("defect", ["early_release", "moved_in_hold"])
def test_a_release_with_no_trusted_hold_is_refused(tmp_path, defect):
    _, app, rows = _run(tmp_path, defects=(defect,))
    assert all(r["ok"] for r in rows.values()), rows
    assert not app["scored"] and app["error"] == "unscorable"


@needs_app
@pytest.mark.parametrize("defect,quality", [("short_tail", "short"), ("no_tail", "unsettled")])
def test_a_recording_stopped_too_soon_is_labelled(tmp_path, defect, quality):
    _, app, _ = _run(tmp_path, defects=(defect,))
    assert app["ended"] == "end_of_file" and app["capture_quality"] == quality


@needs_app
def test_sideways_motion_is_reported_at_the_planted_size(tmp_path):
    tu, app, rows = _run(tmp_path, defects=("lateral_shake",))
    assert tu["lateral_fraction"] > 0.2 and rows["lateral motion"]["ok"], rows["lateral motion"]


@needs_app
def test_the_hold_reads_steady_after_a_lift(tmp_path):
    """The mirrored gravity_seed (fixed 2026-11-09) made a still leg read 166..178 deg after a lift."""
    tu, app, _ = _run(tmp_path, phone=PT.Phone.ideal())
    t = np.array(app["trajectory"]["t"]); a = np.array(app["trajectory"]["angle_deg"], dtype=float)
    hold = a[(t > tu["release_t_s"] - 2.0) & (t < tu["release_t_s"] - 0.1)]
    assert np.ptp(hold) < 1.0, f"a still leg read {hold.min():.1f}..{hold.max():.1f} deg"


@needs_app
@pytest.mark.xfail(strict=True, reason="a lift faster than 1 rad/s after a still second fires the release detector")
def test_a_brisk_lift_is_not_taken_for_the_release(tmp_path):
    _, _, rows = _run(tmp_path, defects=("fast_lift",))
    assert rows["live release"]["ok"], rows["live release"]


@needs_app
@pytest.mark.xfail(strict=True, reason="the 5 deg hold-drift limit resets and restarts, so a steady sag is never caught")
def test_a_hold_that_sags_nine_degrees_is_not_shown_as_ready(tmp_path):
    _, _, rows = _run(tmp_path, defects=("hold_drift",))
    assert rows["hold drift caught"]["ok"], rows["hold drift caught"]
