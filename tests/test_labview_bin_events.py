"""Tests for labview_bin_events: ramp detection, settle detection, ring-down
metrics on a synthetic damped oscillation, and the full 4-ramp protocol parse."""
import numpy as np
import pytest

from labview_bin_events import FS_HZ, _ramps, _ringdown, _settle_index, analyze_trial


def _damped(f_hz, zeta, amp=20.0, seconds=6.0):
    """Underdamped second-order ring-down x(t) = amp * exp(-zeta*wn*t) * cos(wd*t)."""
    t = np.arange(int(seconds * FS_HZ)) / FS_HZ
    wn = 2 * np.pi * f_hz / np.sqrt(1 - zeta**2)
    wd = wn * np.sqrt(1 - zeta**2)
    return amp * np.exp(-zeta * wn * t) * np.cos(wd * t)


def test_ramps_merges_zero_crossing_gap_but_not_separate_ramps():
    v = np.zeros(4000)
    v[1000:1200] = 50.0
    v[1200:1210] = 0.0          # 10 ms dip through zero mid-trapezoid: same ramp
    v[1210:1400] = -50.0
    v[3000:3200] = 100.0        # 1.6 s later: a second ramp
    assert _ramps(v) == [(1000, 1400), (3000, 3200)]


def test_ramps_handles_ramp_touching_file_end():
    v = np.zeros(500)
    v[400:] = 10.0
    assert _ramps(v) == [(400, 500)]


def test_settle_index_requires_half_second_inside_tolerance():
    pos = np.full(3000, 37.5)
    pos[:1000] += _damped(3.0, 0.05, amp=15.0, seconds=1.0)   # ringing for the first second
    pos[1000:1300] += 3.0                                     # then parked 3 deg off target
    idx = _settle_index(pos, 37.5, start=0)
    assert idx == 1300                                        # first sample of the settled run
    assert _settle_index(pos[:1200], 37.5, start=0) is None   # never settles inside this slice


def test_ringdown_recovers_frequency_and_damping():
    f, zeta = 3.0, 0.05
    target = 37.5
    pos = target + _damped(f, zeta)
    out = _ringdown(pos, target, 0, len(pos))
    assert out["ringdown_n_peaks"] >= 6
    assert out["ringdown_freq_hz"] == pytest.approx(f, rel=0.03)
    assert out["ringdown_damping_ratio"] == pytest.approx(zeta, rel=0.15)


def test_ringdown_reports_nan_when_nothing_rings():
    pos = np.full(2000, 37.5) + np.random.default_rng(0).normal(scale=0.05, size=2000)
    out = _ringdown(pos, 37.5, 0, len(pos))
    assert out["ringdown_n_peaks"] == 0
    assert np.isnan(out["ringdown_freq_hz"])
    assert np.isnan(out["ringdown_damping_ratio"])


def _synthetic_protocol(tmp_path, amp=37.5, v=200.0, hold_s=3.0, overshoot=5.0):
    """Write a 7-channel big-endian .bin with the rig's 0->+A->-A->+A->0 protocol."""
    steps = [(0.0, amp), (amp, -amp), (-amp, amp), (amp, 0.0)]
    hold = int(hold_s * FS_HZ)
    pos_sp, vel_sp, pos = [np.zeros(hold)], [np.zeros(hold)], [np.zeros(hold)]
    for a, b in steps:
        n = int(abs(b - a) / v * FS_HZ)
        ramp = np.linspace(a, b, n, endpoint=False)
        pos_sp.append(ramp)
        vel_sp.append(np.full(n, np.sign(b - a) * v))
        pos.append(ramp)
        pos_sp.append(np.full(hold, b))
        vel_sp.append(np.zeros(hold))
        # response: overshoot in the direction of travel, ring at 3 Hz, decay
        pos.append(b + np.sign(b - a) * _damped(3.0, 0.08, amp=overshoot, seconds=hold_s))
    pos_sp, vel_sp, pos = map(np.concatenate, (pos_sp, vel_sp, pos))
    n = len(pos)
    vel = np.gradient(pos) * FS_HZ
    torque = 0.1 + 0.02 * vel / v
    emg1 = 0.07 + 0.001 * np.random.default_rng(1).normal(size=n) + 0.02 * (vel_sp != 0)
    emg2 = 0.015 + 0.001 * np.random.default_rng(2).normal(size=n)
    data = np.vstack([pos, vel, pos_sp, torque, emg1, emg2, vel_sp])
    p = tmp_path / "synthStretchTrial01Fast.bin"
    np.ascontiguousarray(data.T, dtype=">f8").tofile(p)
    return p


def test_analyze_trial_parses_the_four_ramp_protocol(tmp_path):
    p = _synthetic_protocol(tmp_path)
    summary, schedule, events = analyze_trial(p)

    assert summary["n_ramps"] == 4
    assert summary["n_channels"] == 7
    assert summary["amplitude_setpoint_deg"] == pytest.approx(37.5)
    assert summary["vel_setpoint_max_deg_s"] == pytest.approx(200.0)
    assert summary["fs_hz"] == FS_HZ

    kinds = [s["kind"] for s in schedule]
    assert kinds == ["hold", "ramp"] * 4 + ["hold"]
    assert [s["pos_setpoint_deg"] for s in schedule if s["kind"] == "ramp"] == [37.5, -37.5, 37.5, 0.0]
    assert [s["vel_setpoint_deg_s"] for s in schedule if s["kind"] == "ramp"] == [200.0, -200.0, 200.0, -200.0]

    assert [e["direction"] for e in events] == ["extension", "flexion", "extension", "flexion"]
    assert [e["amplitude_cmd_deg"] for e in events] == pytest.approx([37.5, 75.0, 75.0, 37.5])
    for e in events:
        assert e["overshoot_deg"] == pytest.approx(5.0, abs=0.3)      # positive in direction of travel
        assert e["ringdown_freq_hz"] == pytest.approx(3.0, rel=0.05)
        assert 0.3 < e["settle_time_s"] < 3.0
        assert e["emg_fds_rms_ramp_over_rest"] > 5                    # EMG burst during ramp is detected
        assert e["emg_edc_rms_ramp_over_rest"] == pytest.approx(1.0, abs=0.3)   # quiet channel stays ~1
