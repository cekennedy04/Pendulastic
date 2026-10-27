"""neuromech.fall_speed's statistic, checked where the answer is known by construction.

The cohort half needs recordings this checkout never has; the scaling measure and the
report do not, and they are what turn a table of trials into the claim.
"""
import numpy as np
import pytest

from neuromech import fall_speed as FS


def test_a_pendulum_whose_peak_scales_with_its_swing_reads_slope_one():
    a0 = np.linspace(25, 75, 30)
    s = FS.scaling(a0, 5.0 * a0)                       # peak = omega * A0
    assert s["slope"] == pytest.approx(1.0, abs=1e-9)
    assert s["rho"] == pytest.approx(1.0)


def test_a_peak_set_by_the_release_reads_slope_zero():
    rng = np.random.default_rng(0)
    a0 = np.linspace(25, 75, 60)
    s = FS.scaling(a0, 350.0 + rng.normal(0, 1e-6, a0.size))   # flat, as real legs measured
    assert abs(s["slope"]) < 1e-6
    assert s["peak"] == pytest.approx(350.0, abs=1e-3)


def test_the_report_names_both_sides_and_refuses_tiny_groups():
    real = [dict(participant=str(i % 4), leg="left", study="Control", mas="0", interpretable=True,
                 A0=30.0 + i, peak=350.0) for i in range(12)]
    real.append(dict(participant="9", leg="left", study="MINT", mas="1", interpretable=True, A0=33.0, peak=356.0))
    real.append(dict(participant="9", study="MINT", mas="1", interpretable=False))   # no fall measured: excluded
    real.append(dict(participant="8", leg="left", study="MINT", mas="1", interpretable=False, A0=2.0, peak=9.0))  # below 5 deg
    emu = [dict(sid=f"S{i}", group="A", A0=40.0 + i, peak=4.0 * (40.0 + i)) for i in range(5)]
    txt = FS.report(real, emu)
    assert "REAL, per leg (leg medians)" in txt and "REAL, all measurable falls" in txt and "REAL, graded by the scorer only" in txt and "EMULATOR, group A" in txt
    assert "REAL, clinical MAS >= 1" in txt and "(too few)" in txt
    row = next(l for l in txt.splitlines() if l.startswith("EMULATOR, all scenarios"))
    assert "+1.00" in row                                # emulator: peak scales with the swing
