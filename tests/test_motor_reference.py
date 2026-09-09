# tests/test_motor_reference.py
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_reference as mr


def test_the_swing_starts_at_its_commanded_amplitude():
    t, th = mr.swing(a0_deg=45.0, f_hz=0.9, tau_s=1.6, duration_s=10.0, fs_hz=1000.0)
    assert th[0] == pytest.approx(45.0, abs=1e-9)
    assert len(t) == len(th)
    assert t[0] == 0.0


def test_the_swing_decays_toward_zero():
    _t, th = mr.swing(a0_deg=45.0, f_hz=0.9, tau_s=1.6, duration_s=10.0, fs_hz=1000.0)
    assert abs(th[-1]) < 1.0, "a 10 s trace at tau=1.6 should have settled"
    assert np.max(np.abs(th[:1000])) > np.max(np.abs(th[-1000:]))


def test_the_interior_waveform_matches_the_closed_form():
    """The endpoint tests would pass against a stub that is a0 at index 0 and
    zero after. This pins the actual shape: every sample against the formula
    the docstring claims, the zero-crossing spacing that fixes f, and the
    decay rate that the loose abs(th[-1]) < 1.0 bound does not."""
    a0, f, tau, fs = 45.0, 0.9, 1.6, 1000.0
    t, th = mr.swing(a0_deg=a0, f_hz=f, tau_s=tau, duration_s=6.0, fs_hz=fs)
    expected = a0 * np.exp(-t / tau) * np.cos(2.0 * np.pi * f * t)
    assert np.allclose(th, expected, atol=1e-9)

    sign_changes = np.flatnonzero(np.diff(np.signbit(th)))
    assert len(sign_changes) >= 6, "a 6 s trace at 0.9 Hz must cross zero repeatedly"
    half_period_s = np.median(np.diff(sign_changes)) / fs
    assert half_period_s == pytest.approx(1.0 / (2.0 * f), rel=0.02)

    assert abs(th[int(tau * fs)]) == pytest.approx(
        a0 * np.exp(-1.0) * abs(np.cos(2.0 * np.pi * f * tau)), rel=0.02)


def test_the_preamble_has_exactly_three_pulses():
    t, th = mr.preamble(fs_hz=1000.0)
    # A pulse is a contiguous run above half height.
    above = th > (mr.PULSE_DEG / 2.0)
    edges = np.diff(above.astype(int))
    assert (edges == 1).sum() == mr.PULSE_COUNT
    assert (edges == -1).sum() == mr.PULSE_COUNT


def test_the_full_trace_reports_where_the_swing_begins():
    t, th, t_rel = mr.full_trace(a0_deg=45.0, f_hz=0.9, tau_s=1.6,
                                 duration_s=10.0, fs_hz=1000.0)
    i = int(round(t_rel * 1000.0))
    assert th[i] == pytest.approx(45.0, abs=1e-6), (
        "the swing must start at full amplitude at the reported release time")
    assert np.all(np.abs(th[:i - 1]) <= mr.PULSE_DEG + 1e-9), (
        "nothing before release may exceed the pulse height")


def test_the_sweep_is_dense_around_the_excursion_floor():
    """The floor is the point. 22, 25 and 28 must all be present, because the
    question is where extraction breaks relative to MIN_INTERPRETABLE_A0_DEG."""
    a0s = sorted({r["a0_deg"] for r in mr.sweep_matrix()})
    for v in (22.0, 25.0, 28.0):
        assert v in a0s, f"{v} deg missing from the sweep"


def test_every_run_is_repeated_and_uniquely_identified():
    runs = mr.sweep_matrix()
    ids = [r["run_id"] for r in runs]
    assert len(ids) == len(set(ids)), "run_id must be unique"
    assert {r["repeat"] for r in runs} == {1, 2, 3}
    assert 60 <= len(runs) <= 120, f"{len(runs)} runs is outside a bench day"


def test_the_export_round_trips_through_a_file(tmp_path):
    """The exported file must carry BOTH the sync pulse and the swing.

    Checked separately on purpose: one max over the whole file conflates them,
    and the swing is 18x the pulse height on the first run, so a dropped
    preamble would be invisible.
    """
    run = mr.sweep_matrix()[0]
    path = mr.export_trace(run, str(tmp_path), fs_hz=1000.0)
    rows = [l.split(",") for l in open(path, encoding="utf-8").read().splitlines()]
    assert rows[0] == ["t_s", "theta_deg"]

    t0, th0 = float(rows[1][0]), float(rows[1][1])
    assert t0 == 0.0 and th0 == pytest.approx(0.0), "the trace must start still"

    t_rel = mr.full_trace(run["a0_deg"], run["f_hz"], run["tau_s"], fs_hz=1000.0)[2]
    pre = [float(r[1]) for r in rows[1:] if float(r[0]) < t_rel]
    assert max(pre) == pytest.approx(mr.PULSE_DEG, abs=1e-6), (
        "the sync pulse must survive into the exported file")

    peak = max(abs(float(r[1])) for r in rows[1:])
    assert peak == pytest.approx(run["a0_deg"], abs=1e-6), (
        "the swing amplitude must survive into the exported file")
