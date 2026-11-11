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
    real = [dict(participant=f"synthetic-{i % 4}", leg="left", study="GroupY", mas="0", interpretable=True,
                 A0=30.0 + i, peak=350.0) for i in range(12)]
    real.append(dict(participant="synthetic-9", leg="left", study="GroupX", mas="1", interpretable=True, A0=33.0, peak=356.0))
    real.append(dict(participant="synthetic-9", study="GroupX", mas="1", interpretable=False))   # no fall measured: excluded
    real.append(dict(participant="synthetic-8", leg="left", study="GroupX", mas="1", interpretable=False, A0=2.0, peak=9.0))  # below 5 deg
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


def test_pinned_reference_holds_only_what_the_check_reads(tmp_path):
    # synthetic people: the IDs, groups and grades below belong to nobody
    real = [dict(participant="synthetic-b", leg=leg, study="GroupX", mas="1", interpretable=True, A0=a0, peak=330.0)
            for leg, a0 in (("left", 20.0), ("right", 61.0))]
    real += [dict(participant="synthetic-a", leg="left", study="GroupY", mas="0", interpretable=True, A0=50.0, peak=350.0)]
    ref = FS.pin_reference(real, str(tmp_path / "ref.json"))
    assert all(tuple(g) == FS.REFERENCE_ROW_FIELDS for g in ref["legs"])
    # rows in A0 order, people numbered by first appearance in it: not in participant-ID order
    assert [(g["person"], g["A0"]) for g in ref["legs"]] == [(1, 20.0), (2, 50.0), (1, 61.0)]
    on_disk = FS.load_reference(str(tmp_path / "ref.json"))
    assert on_disk == ref
    txt = (tmp_path / "ref.json").read_text()
    for leak in ("synthetic", "GroupX", "GroupY", "left", "right", "trials"):     # nowhere in the file
        assert leak not in txt, leak
    for leak in ("participant", "study", "mas", "leg"):                           # nowhere in the rows
        assert leak not in str(on_disk["legs"]), leak
    # the same legs handed over in another order pin the same file: no order is left to choose
    assert FS.pin_reference(real[::-1], str(tmp_path / "ref2.json"))["legs"] == ref["legs"]


def test_the_committed_reference_carries_no_clinical_field():
    ref = FS.load_reference()
    assert all(tuple(g) == FS.REFERENCE_ROW_FIELDS for g in ref["legs"])
    assert FS.blind_rows([(g["person"], g["A0"], g["peak"]) for g in ref["legs"]]) == ref["legs"]
    assert set(ref) == {"what", "a0_source", "score_formula_id", "scorer_fingerprint", "row_fields", "legs"}


def test_a_reference_with_extra_row_fields_is_refused(tmp_path):
    import json
    path = tmp_path / "ref.json"
    path.write_text(json.dumps(dict(legs=[dict(person=1, A0=40.0, peak=350.0, mas="0")])))
    with pytest.raises(SystemExit, match="mas"):
        FS.load_reference(str(path))
