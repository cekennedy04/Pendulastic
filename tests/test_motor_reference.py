# tests/test_motor_reference.py
import os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_reference as mr

#: End of the sync pulses. Everything before this is a 5 deg square-edged
#: alignment mark, deliberately sharp so that the encoder and the gyro resolve
#: the same edge. The physical-executability bounds below apply to the LIMB
#: motion after it, not to the mark.
_PULSES_END_S = mr.LEAD_S + mr.PULSE_COUNT * (mr.PULSE_WIDTH_S + mr.PULSE_GAP_S)


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


def test_the_arm_is_held_at_amplitude_before_release():
    """A pendulum test releases FROM the held position.

    This test previously asserted the opposite -- that nothing before release
    exceeded the pulse height, which FORCED the trace to jump from 0 to a0 in
    one sample. At a0=90 and 1 kHz that is a commanded 90,000 deg/s, against
    438 deg/s in the swing itself and a ~597 deg/s physical ceiling for a
    shank. The motor cannot execute it, and the phone read the step instead of
    the swing: recovered A0 was +95% at a commanded 50 deg and +319% at 5 deg,
    worst exactly in the amplitude band the sweep exists to probe.
    """
    a0 = 45.0
    t, th, t_rel = mr.full_trace(a0_deg=a0, f_hz=0.9, tau_s=1.6,
                                 duration_s=10.0, fs_hz=1000.0)
    i = int(round(t_rel * 1000.0))
    assert th[i] == pytest.approx(a0, abs=1e-6), (
        "the swing must start at full amplitude at the reported release time")
    hold = th[i - int(0.5 * 1000.0):i]
    assert np.allclose(hold, a0, atol=1e-6), (
        "the half second before release must be a still hold AT amplitude, "
        f"not a jump into it -- got {hold.min():.3f}..{hold.max():.3f}")


def test_no_commanded_step_exceeds_what_the_motor_can_follow():
    """The whole trace, not just the release, must stay physically executable.

    Holding the pre-roll at a0 alone does not fix this: it moves the 0 -> a0
    step earlier, to where the pulses end. Only a ramp removes it.
    """
    for a0 in (5.0, 45.0, 90.0):
        t, th, _ = mr.full_trace(a0_deg=a0, f_hz=0.9, tau_s=1.6,
                                 duration_s=10.0, fs_hz=1000.0)
        after = int(round(_PULSES_END_S * 1000.0))
        rate = np.abs(np.diff(th[after:])) * 1000.0
        assert rate.max() <= 600.0, (
            f"a0={a0}: commanded {rate.max():.0f} deg/s at "
            f"t={_PULSES_END_S + rate.argmax()/1000.0:.3f}s, "
            "past what a limb can do")


def test_the_lift_stays_under_the_release_detection_threshold():
    """The ramp is motion, and the phone must not mistake it for the release.

    Zero capture fires on |omega| crossing 1.0 rad/s (57.3 deg/s) once a calm
    window has armed the gate. The lift has to stay under that or the ramp
    itself can be taken as the release.
    """
    t, th, t_rel = mr.full_trace(a0_deg=90.0, f_hz=0.9, tau_s=1.6,
                                 duration_s=10.0, fs_hz=1000.0)
    i = int(round(t_rel * 1000.0))
    after = int(round(_PULSES_END_S * 1000.0))
    pre = np.abs(np.diff(th[after:i])) * 1000.0
    assert pre.max() < 57.3, (
        f"the lift reaches {pre.max():.1f} deg/s, at or past the 1.0 rad/s "
        "release threshold")


def test_the_exported_release_offset_matches_the_actual_trace():
    """The offset motor_bench aligns on must BE the trace's release.

    This is the regression guard for a real breakage: align() re-derived the
    offset from the constants, and inserting the ramp moved the real release
    without moving the derivation, so alignment silently pointed 4 s early.
    Anything that re-derives it instead of calling release_offset_s() will
    drift again the next time the preamble changes -- so pin them together
    across amplitude and sample rate, where a nominal-length derivation fails.
    """
    for fs in (500.0, 1000.0, 2000.0):
        for a0 in (5.0, 45.0, 90.0):
            _, _, t_rel = mr.full_trace(a0_deg=a0, f_hz=0.9, tau_s=1.6,
                                        duration_s=12.0, fs_hz=fs)
            expected = mr.LEAD_S + mr.release_offset_s()
            assert t_rel == pytest.approx(expected, abs=1.0 / fs), (
                f"fs={fs} a0={a0}: trace releases at {t_rel:.4f} but "
                f"release_offset_s() implies {expected:.4f}")


def test_the_lift_keeps_real_margin_under_the_release_threshold():
    """Not just under the threshold -- under it with room.

    A ramp tuned to pass at 57.2 deg/s would be one rounding away from the
    phone reading the lift itself as the release, and the bench has no way to
    tell that failure from a real one.
    """
    t, th, t_rel = mr.full_trace(a0_deg=max(mr.SWEEP_A0_DEG), f_hz=0.9,
                                 tau_s=1.6, duration_s=12.0, fs_hz=1000.0)
    i = int(round(t_rel * 1000.0))
    after = int(round(_PULSES_END_S * 1000.0))
    peak = (np.abs(np.diff(th[after:i])) * 1000.0).max()
    assert peak < 0.75 * 57.3, (
        f"lift peaks at {peak:.1f} deg/s, only {57.3 - peak:.1f} deg/s under "
        "the release threshold -- lengthen RAMP_S")


def test_the_hold_is_long_enough_to_arm_the_zero_capture():
    """The gate needs a full calm window of stillness before the release."""
    import imu_calibration_tuner as tuner
    assert mr.PRE_ROLL_S >= 2.0 * tuner.GYRO_BIAS_WINDOW_S, (
        f"PRE_ROLL_S={mr.PRE_ROLL_S} leaves no margin over the "
        f"{tuner.GYRO_BIAS_WINDOW_S} s calm window the zero capture needs")


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
    sweep = [r for r in runs if r["block"] == "sweep"]
    assert {r["repeat"] for r in sweep} == {1, 2, 3}
    assert 60 <= len(runs) <= 120, f"{len(runs)} runs is outside a bench day"


def test_the_repeatability_block_runs_first_and_is_long_enough():
    """One condition, many repeats -- the only run that measures the
    INSTRUMENT rather than the condition. It must not be scheduled after the
    sweep, or a session that drifts reports that drift as instrument noise.
    """
    runs = mr.sweep_matrix()
    block = [r for r in runs if r["block"] == "repeatability"]
    assert len(block) >= 15, (
        f"{len(block)} repeats gives an SEM too wide to compare against the "
        "29.0 deg A0 MDC95 measured on participants")
    assert all(r["block"] == "repeatability" for r in runs[:len(block)]), (
        "the repeatability block must run first, while the bench is coldest")
    assert len({(r["a0_deg"], r["f_hz"], r["tau_s"]) for r in block}) == 1, (
        "a repeatability block that varies its condition measures nothing")


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

    win = mr.pulse_search_window_s()
    pre = [float(r[1]) for r in rows[1:] if float(r[0]) < win]
    assert max(pre) == pytest.approx(mr.PULSE_DEG, abs=1e-6), (
        "the sync pulse must survive into the exported file")

    peak = max(abs(float(r[1])) for r in rows[1:])
    assert peak == pytest.approx(run["a0_deg"], abs=1e-6), (
        "the swing amplitude must survive into the exported file")
