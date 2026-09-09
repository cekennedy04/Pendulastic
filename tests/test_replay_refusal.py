# tests/test_replay_refusal.py
"""A replay that cannot produce an angle must say why.

Diagnosed 2026-09-09: 23 of 163 corpus trials (14%) return nothing from the
scorer, and the reported cause was "compute_pt_params returned None" for every
one of them. That is not what happened in any of them:

  15  `replay_trial` returned an empty series because the zero reference was
      never captured -- 5 because no calm-then-departure ever qualified, 10
      because the gate armed, was revoked by a sub-threshold wobble, and the
      real swing arrived before a fresh calm window could requalify it.
   4  the accelerometer stream recorded ZERO samples (a 49-byte header-only
      file) while gyro and mag recorded normally at 63-141 KB. With no gravity
      reference the AHRS emitted an all-zero angle series, which then failed
      three stages downstream wearing a misleading label.
   3  release was detected where the leg already sat at neutral, discarding
      real swings of 67-77 degrees.

The thresholds themselves are NOT loosened here. They exist to stop a zero
being fabricated while the examiner is still handling the limb, and this
project has A0 = 418 deg and A0 = 169.8 deg in its history from exactly that.
What changes is that a refusal becomes evidence instead of silence -- the same
correction U5 made for the pre-release hold.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import imu_calibration_tuner as tuner  # noqa: E402


def _log(*, with_accel=True, still=True, burst=True, n_hold=200, seed=1):
    """A solo-phone raw log. `still` controls whether the hold qualifies as
    calm; `burst` whether a release-sized rotation ever happens."""
    rng = np.random.default_rng(seed)
    out, t, ts = [], 0.0, 0
    for _ in range(n_hold):
        w = [0.0, 0.0, 0.0] if still else list(rng.normal(0, 0.5, 3))
        if with_accel:
            out.append({"t": t, "role": "proximal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts})
        out.append({"t": t, "role": "proximal", "sensor": "gyro",
                    "v": w, "phone_ts_ms": ts})
        t += 0.01
        ts += 10
    if burst:
        for _ in range(120):
            if with_accel:
                out.append({"t": t, "role": "proximal", "sensor": "accel",
                            "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts})
            out.append({"t": t, "role": "proximal", "sensor": "gyro",
                        "v": [0.0, 3.0, 0.0], "phone_ts_ms": ts})
            t += 0.01
            ts += 10
    for _ in range(200):
        if with_accel:
            out.append({"t": t, "role": "proximal", "sensor": "accel",
                        "v": [0.0, 0.0, 9.81], "phone_ts_ms": ts})
        out.append({"t": t, "role": "proximal", "sensor": "gyro",
                    "v": [0.0, 0.0, 0.0], "phone_ts_ms": ts})
        t += 0.01
        ts += 10
    return out


CFG = {"beta": 0.041, "ema_alpha": 0.5, "flex_axis_capture": True,
       "gravity_seed": True, "method": "relative"}


def test_a_good_log_is_accepted_and_says_so():
    t, ang, q = tuner.replay_trial_detailed(_log(), dict(CFG))
    assert q.ok, f"a clean hold-then-swing was refused: {q.reason} {q.detail}"
    assert len(t) > 0 and np.isfinite(ang).sum() > 10


def test_a_log_with_no_accelerometer_is_refused_by_name():
    """The four P20 left-pre trials. A 49-byte accel file is a capture
    failure, and inventing a flat-zero angle from gyro alone is worse than
    refusing: it looks like a measurement.
    """
    t, ang, q = tuner.replay_trial_detailed(_log(with_accel=False), dict(CFG))
    assert not q.ok
    assert q.reason == "no_accel", q.reason
    assert "accel" in q.detail.lower()
    # and it must not hand back a plausible-looking curve
    assert len(t) == 0


def test_a_log_whose_zero_never_latches_is_refused_by_name():
    """No release-sized rotation, so the zero reference is never captured."""
    t, ang, q = tuner.replay_trial_detailed(_log(burst=False), dict(CFG))
    assert not q.ok
    assert q.reason == "no_zero_capture", q.reason
    assert q.detail, "a refusal with no explanation is the defect being fixed"


def test_the_refusal_reports_whether_the_gate_ever_armed():
    """5 of the 15 never armed; 10 armed and were revoked. An operator
    deciding whether to re-record needs to know which."""
    _, _, q = tuner.replay_trial_detailed(_log(burst=False), dict(CFG))
    assert "armed" in q.detail.lower() or "calm" in q.detail.lower()


def test_the_two_tuple_wrapper_is_unchanged():
    """Every existing caller unpacks two values; that must keep working."""
    for kwargs in ({}, {"with_accel": False}, {"burst": False}):
        res = tuner.replay_trial(_log(**kwargs), dict(CFG))
        assert isinstance(res, tuple) and len(res) == 2
        t, ang = res
        assert isinstance(t, np.ndarray) and isinstance(ang, np.ndarray)


def test_a_refused_trial_still_returns_arrays_not_none():
    """Callers finite-filter the angle series; None would crash them."""
    t, ang, q = tuner.replay_trial_detailed(_log(with_accel=False), dict(CFG))
    assert isinstance(t, np.ndarray) and isinstance(ang, np.ndarray)
    assert not q.ok
