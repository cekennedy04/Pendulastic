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


# ── the B13 mismatch check, on a synthetic reference whose answer is known ──
def _ref(slope=0.0, noise=0.1, people=10):
    rng = np.random.default_rng(1)
    legs = []
    for p in range(1, people + 1):
        for _ in range(2):
            a0 = float(rng.uniform(20, 70))
            legs.append(dict(person=p, A0=a0, peak=float(350 * (a0 / 40) ** slope * np.exp(rng.normal(0, noise)))))
    return dict(legs=legs)


def _emu(pairs):
    return [dict(sid=s, group="X", A0=a0, peak=pk) for s, a0, pk in pairs]


def test_reference_stats_recover_a_flat_reference():
    st_ = FS.reference_stats(_ref()["legs"])
    assert abs(st_["b"]) < 0.2 and st_["ci"][0] < 0 < st_["ci"][1]
    assert st_["s"] == pytest.approx(0.1, abs=0.04) and st_["people"] == 10
    lo, hi = FS.band(st_, 0.0)
    assert lo < 0 < hi and hi - lo == pytest.approx(2 * 1.96 * st_["s"], rel=0.35)
    lo2, hi2 = FS.band(st_, -0.3)
    assert hi2 - lo2 > hi - lo                          # slope uncertainty widens the band off the anchor
    again = FS.reference_stats(_ref()["legs"])          # seeded: the threshold cannot drift between runs
    assert again["ci"] == st_["ci"] and FS.band(again, 0.0) == (lo, hi)


def test_the_tone_ladder_is_flagged():
    # the report's ladder: peak 281 -> 138 while A0 55 -> 44.8
    emu = _emu([("A-P0", 55.0, 281.0), ("A-P1", 50.0, 210.0), ("A-P2", 44.8, 138.0)])
    res = FS.check(emu, ["A-P0", "A-P1", "A-P2"], _ref())
    assert res["verdict"] == "MISMATCH" and res["slope_mismatch"]
    assert res["profiles"][-1]["mismatch"]


def test_a_catch_that_slows_the_fall_at_fixed_a0_is_flagged_by_its_residual():
    emu = _emu([("A-P0", 55.0, 281.0), ("K1", 55.0, 180.0)])
    res = FS.check(emu, ["K1"], _ref())
    assert res["slope"] is None                        # A0 does not move: no slope to judge
    assert res["verdict"] == "MISMATCH" and res["profiles"][0]["mismatch"]


def test_a_set_that_leaves_fall_speed_alone_passes():
    emu = _emu([("A-P0", 55.0, 281.0), ("K1", 54.0, 279.0), ("K2", 40.0, 285.0)])
    res = FS.check(emu, ["K1", "K2"], _ref())
    assert res["verdict"] == "NO FLAG", FS.check_report(res, "set")
    same_a0 = FS.check(_emu([("A-P0", 55.0, 281.0), ("K1", 55.0, 279.0)]), ["K1"], _ref())
    assert same_a0["verdict"] == "NO FLAG (slope not judged)"


def test_peak_and_a0_falling_together_is_caught_by_the_set_slope():
    # slope 1 over A0 x1.22: each profile's residual is within a 0.15-noise band, the set slope is not
    emu = _emu([("A-P0", 55.0, 281.0), ("P1", 50.0, 281.0 * 50 / 55), ("P2", 45.0, 281.0 * 45 / 55)])
    res = FS.check(emu, ["P1", "P2"], _ref(noise=0.15))
    assert not any(p["mismatch"] for p in res["profiles"])
    assert res["slope_mismatch"] and res["verdict"] == "MISMATCH"


def test_pinned_reference_anonymises_people(tmp_path):
    real = [dict(participant="13", leg=leg, study="MINT", mas="1", interpretable=True, A0=a0, peak=330.0)
            for leg, a0 in (("left", 20.0), ("right", 21.0))]
    real += [dict(participant="5", leg="left", study="Control", mas="0", interpretable=True, A0=50.0, peak=350.0)]
    ref = FS.pin_reference(real, str(tmp_path / "ref.json"))
    assert sorted(g["person"] for g in ref["legs"]) == [1, 1, 2]
    assert "participant" not in (tmp_path / "ref.json").read_text()
