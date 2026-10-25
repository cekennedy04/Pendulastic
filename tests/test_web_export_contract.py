"""The web app's raw-export format is a contract, and it fails silently.

KTD4 requires the exported IMU stream be consumable by
`imu_calibration_tuner.replay_trial()` directly. That function dispatches on
`sensor == "accel" | "mag" | "gyro"` and reads `samp["t"]` in SECONDS. Every
plausible way of getting the format wrong -- a combined 6-axis record, a
timestamp in milliseconds, a dropped `phone_ts_ms` -- produces either an empty
result or, worse, a clean-looking wrong one. None of them raise.

These tests pin the contract by executing it, so a future change to either side
fails here rather than in a clinic. See
`docs/superpowers/specs/2026-08-24-web-app-design.md` Section 3.4.

The trial is forward-simulated rather than recorded: gravity rotated into the
sensor frame gives accel, the motion's own derivative gives gyro, in the units a
browser actually delivers (m/s^2 and deg/s). Deterministic, no RNG, no
participant data.
"""
import math

import numpy as np
import pytest

from imu_calibration_tuner import replay_trial, score_waveform

PARAMS = {"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": False,
          "gravity_seed": True, "method": "relative"}

FS = 60.0          # iOS Safari's DeviceMotion rate, measured 2026-08-24
HOLD_S = 2.0       # >= GYRO_BIAS_WINDOW_S so the calm gate can qualify
TOTAL_S = 9.0
A0_DEG = 45.0
F_HZ = 1.0
TAU = 1.2
G = 9.81


def _browser_samples(total_s=TOTAL_S):
    """A pendulum trial as a browser would report it: accel in m/s^2 including
    gravity, rotationRate in deg/s, timestamps in page-relative ms."""
    out = []
    for i in range(int(round(total_s * FS))):
        t = i / FS
        if t < HOLD_S:
            theta, rate = 0.0, 0.0
        else:
            tt = t - HOLD_S
            a = math.radians(A0_DEG)
            w = 2.0 * math.pi * F_HZ
            env = math.exp(-tt / TAU)
            theta = a * (1.0 - env * math.cos(w * tt))
            rate = a * env * (math.cos(w * tt) / TAU + w * math.sin(w * tt))
        s, c = math.sin(theta), math.cos(theta)
        out.append({
            "ts_ms": t * 1000.0,
            # Gravity in the sensor frame, plus a small static offset.
            "accel": [0.010, s * G + 0.005, c * G - 0.008],
            # Pure x-axis rotation, reported in deg/s as browsers do.
            "gyro_deg_s": [math.degrees(rate) + 0.23, -0.11, 0.17],
        })
    return out


def export_records(samples):
    """The contract. Two records per sample, accel first, `t` in seconds,
    three-element vectors, gyro converted to rad/s."""
    recs = []
    for s in samples:
        t_s = s["ts_ms"] / 1000.0
        t_ms = int(round(s["ts_ms"]))
        recs.append({"t": t_s, "role": "distal", "sensor": "accel",
                     "v": list(s["accel"]), "phone_ts_ms": t_ms})
        recs.append({"t": t_s, "role": "distal", "sensor": "gyro",
                     "v": [math.radians(g) for g in s["gyro_deg_s"]],
                     "phone_ts_ms": t_ms})
    return recs


def _replay(recs):
    return replay_trial(recs, PARAMS)


def test_contract_round_trips_and_produces_a_scorable_trial():
    t, ang = _replay(export_records(_browser_samples()))
    assert len(t) > 0, "the contract must not produce an empty replay"

    finite = ang[np.isfinite(ang)]
    assert len(finite) > 40
    # Tick 0 is NaN by replay_trial's own documented contract.
    assert not np.isfinite(ang[0])

    sw = score_waveform(t, ang)
    p = sw["params"]
    assert p is not None, "a clean simulated swing must be scorable"
    # The simulation settles into A0_DEG of flexion at F_HZ; recovering those
    # is what proves the export preserved real timing, not just structure.
    assert p["f"] == pytest.approx(F_HZ, abs=0.15)
    assert p["neutral_deg"] == pytest.approx(180.0 - A0_DEG, abs=3.0)


def test_unknown_sensor_name_is_dropped_silently_not_rejected():
    """A combined 6-axis 'phone' record matches no dispatch branch. The
    dangerous part is that nothing raises -- the caller sees an unscorable
    trial and has no way to tell it from a patient who never moved."""
    recs = []
    for s in _browser_samples():
        recs.append({"t": s["ts_ms"] / 1000.0, "role": "distal", "sensor": "phone",
                     "v": list(s["accel"]) + [math.radians(g) for g in s["gyro_deg_s"]],
                     "phone_ts_ms": int(round(s["ts_ms"]))})
    t, ang = _replay(recs)
    assert len(t) == 0, (
        "expected the silent-drop failure mode; if this now raises or returns "
        "data, replay_trial's dispatch changed and the spec needs revisiting"
    )


def test_dropping_phone_ts_ms_silently_changes_the_measured_swing():
    """The worst failure available: no error, no empty result, a different
    trial. Without phone_ts_ms, replay_trial cannot derive dt, fails its own
    (0, 0.5) sanity range, and substitutes a fabricated 0.01s for every sample
    -- integrating a 60 Hz stream as though it were 100 Hz. The swing
    systematically under-measures, which reads as a HEALTHIER limb."""
    good = export_records(_browser_samples())
    stripped = [{k: v for k, v in r.items() if k != "phone_ts_ms"} for r in good]

    _, ang_good = _replay(good)
    t_bad, ang_bad = _replay(stripped)
    assert len(t_bad) > 0, "still replays -- that is precisely the hazard"

    a0_good = score_waveform(*_replay(good))["params"]["A0_deg"]
    a0_bad = score_waveform(t_bad, ang_bad)["params"]["A0_deg"]
    assert a0_bad < a0_good * 0.8, (
        f"expected a materially under-measured swing without per-sample "
        f"timing (got A0 {a0_bad:.2f} vs {a0_good:.2f})"
    )


def test_timestamps_must_be_seconds_not_milliseconds():
    """`t` drives n_ticks = (t_end - t0)/TICK_S. Milliseconds inflate the tick
    grid roughly 1000x, so the 50 ms cadence the scorer's window sizes assume
    silently becomes a 0.05 ms cadence."""
    short = _browser_samples(total_s=3.0)
    seconds = export_records(short)
    millis = [dict(r, t=r["t"] * 1000.0) for r in seconds]

    t_sec, _ = _replay(seconds)
    t_ms, _ = _replay(millis)
    assert len(t_ms) > len(t_sec) * 100, (
        f"expected a ~1000x inflated tick grid ({len(t_ms)} vs {len(t_sec)})"
    )


def test_accelerometer_must_precede_gyroscope_at_the_same_timestamp():
    """replay_trial's gyro branch guards `if st.accel is not None`, so a gyro
    sample arriving before the first accel sample is dropped from fusion
    entirely. Ordering is a contract, not a formatting preference."""
    samples = _browser_samples()
    swapped = []
    for i in range(0, len(export_records(samples)), 2):
        pair = export_records(samples)[i:i + 2]
        swapped.extend(reversed(pair))

    t_ok, ang_ok = _replay(export_records(samples))
    t_sw, ang_sw = _replay(swapped)
    assert len(t_ok) > 0 and len(t_sw) > 0

    ok = ang_ok[np.isfinite(ang_ok)]
    sw = ang_sw[np.isfinite(ang_sw)]
    n = min(len(ok), len(sw))
    assert not np.allclose(ok[:n], sw[:n], atol=1e-9), (
        "gyro-before-accel produced an identical trajectory; the ordering "
        "guard in replay_trial may have changed"
    )
