# tests/test_control_pt_score_distribution.py
"""The control-distribution report's own logic, on synthetic rows and curves.
No participant data: nothing here reads Recordings or OptiTrack_Recordings."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import control_pt_score_distribution as cpd
import imu_calibration_config
import imu_calibration_tuner
import pendulastic_pt_score as pts


def _row(pid, leg, trial, group="control", excluded=False, **scores):
    """A trial row scored on the given curves, e.g. shank=0.1; a string value
    is a status instead of a score."""
    key = f"{pid}_{leg}_pre_T{trial}"
    r = {"trial_key": key, "pid": pid, "group": group, "leg": leg, "condition": "pre",
         "leg_key": f"{pid}_{leg}_pre", "baseline": True, "excluded": excluded,
         "optitrack_path": "x"}
    for curve in cpd.CURVES:
        r[f"{curve}_status"] = "absent"
    for curve, value in scores.items():
        if isinstance(value, str):
            r[f"{curve}_status"] = value
            continue
        r.update({f"{curve}_status": "scored", f"{curve}_pt": value, f"{curve}_A0": 50.0,
                  f"{curve}_flags": "", f"{curve}_top_term": "R2n"})
    return r


def test_distribution_counts_strictly_under_each_line():
    d = cpd.distribution([0.1, 0.2, 0.3, None, float("nan")], [0.2, 0.3528])
    assert d["n"] == 3 and d["median"] == pytest.approx(0.2)
    assert (d["min"], d["max"]) == (0.1, 0.3)
    assert d["n_under"] == {0.2: 1, 0.3528: 3}       # 0.2 itself is not under 0.2
    assert d["under"][0.2] == pytest.approx(1 / 3)


def test_distribution_of_nothing_has_no_numbers():
    d = cpd.distribution([], [0.2])
    assert d["n"] == 0 and d["median"] is None and d["under"][0.2] is None


def test_leg_value_is_the_median_of_its_scored_trials_only():
    rows = [_row("1", "left", 1, shank=0.10), _row("1", "left", 2, shank=0.30),
            _row("1", "left", 3, shank=0.14), _row("1", "left", 4, shank="refused: excursion too small"),
            _row("1", "right", 1, shank=0.50), _row("2", "left", 1, shank="unscored")]
    assert sorted(cpd.leg_medians(rows, "shank")) == pytest.approx([0.14, 0.50])
    assert cpd.leg_medians(rows, "phone_screen") == []


def test_retest_spread_uses_legs_with_two_or_more_trials():
    rows = [_row("1", "left", 1, shank=0.10), _row("1", "left", 2, shank=0.30),
            _row("1", "right", 1, shank=0.40), _row("1", "right", 2, shank=0.44),
            _row("2", "left", 1, shank=0.05)]                      # one trial: no spread
    s = cpd.retest_spread(rows, "shank")
    assert (s["n_legs"], s["n_trials"]) == (2, 4)
    assert s["range_max"] == pytest.approx(0.20) and s["range_median"] == pytest.approx(0.12)
    # pooled: (0.02 + 0.0008) / 2 degrees of freedom
    assert s["sd_within"] == pytest.approx(np.sqrt((0.02 + 0.0008) / 2))
    assert s["straddle"] == 1                                       # only the left leg crosses 0.2
    assert cpd.retest_spread(rows, "loader")["n_legs"] == 0


def test_status_counts_keeps_refusal_reasons_apart():
    rows = [_row("1", "left", 1, shank=0.1), _row("1", "left", 2, shank="refused: excursion too small"),
            _row("1", "left", 3, shank="unscored"), _row("1", "left", 4)]
    c = cpd.status_counts(rows, "shank")
    assert c == {"scored": 1, "refused: excursion too small": 1, "unscored": 1, "absent": 1}


@pytest.mark.parametrize("args, verdict", [
    ((8, 8, 2, 2), "holds"),
    ((8, 6, 4, 3), "partly holds"),          # most legs under, not every trial
    ((8, 3, 4, 2), "does not hold"),         # half the legs or fewer
    ((8, 1, 2, 0), "does not hold"),
    ((0, 0, 0, 0), "cannot be said"),
])
def test_claim_verdict(args, verdict):
    assert cpd.claim_verdict(*args) == verdict


def test_fill_gaps_interpolates_and_leaves_complete_curves_alone():
    t = np.arange(5.0)
    y = np.array([0.0, np.nan, 2.0, np.nan, 4.0])
    assert cpd.fill_gaps(t, y) == pytest.approx([0, 1, 2, 3, 4])
    whole = np.array([3.0, 1.0, 2.0, 5.0, 4.0])
    assert cpd.fill_gaps(t, whole) == pytest.approx(whole)
    assert np.isnan(cpd.fill_gaps(t, np.full(5, np.nan))).all()


def test_reference_without_drops_one_person_and_keeps_the_literature_anchors():
    trials = [{"pid": "1", "phi_max_ratio": 0.5, "f": 0.9, "area_ratio": 0.1},
              {"pid": "2", "phi_max_ratio": 0.7, "f": 1.0, "area_ratio": 0.2},
              {"pid": "2", "phi_max_ratio": 0.9, "f": 1.2, "area_ratio": 0.4}]
    ref = cpd.reference_without(trials, "1", pts.HEALTHY_REF)
    assert ref["phi_max_ratio"] == pytest.approx(0.8) and ref["f"] == pytest.approx(1.1)
    assert ref["area_ratio"] == pytest.approx(0.3)
    assert ref["R2n"] == pts.HEALTHY_REF["R2n"] and ref["N"] == pts.HEALTHY_REF["N"]
    assert pts.HEALTHY_REF["phi_max_ratio"] != 0.8, "the shipped reference must not be edited in place"
    # someone not in the set: the reference is the whole set's medians
    assert cpd.reference_without(trials, "9", pts.HEALTHY_REF)["phi_max_ratio"] == pytest.approx(0.7)
    assert cpd.reference_without(trials, None, pts.HEALTHY_REF)["phi_max_ratio"] == pytest.approx(0.7)
    assert cpd.reference_without([trials[0]], "1", pts.HEALTHY_REF) is None


def _pendulum(a0=50.0, zeta=0.12, f=0.95, hold_s=1.5, n_s=12.0, hz=100.0):
    """A held extension at 180 deg, then a damped swing that settles a0 lower."""
    t = np.arange(0.0, hold_s + n_s, 1.0 / hz)
    tau = np.clip(t - hold_s, 0.0, None)
    w = 2 * np.pi * f
    swing = a0 * (1 - np.exp(-zeta * w * tau) * np.cos(w * tau))
    return t, 180.0 - np.where(t < hold_s, 0.0, swing)


def test_score_angle_scores_a_swing_and_refuses_a_small_one():
    good = cpd.score_angle(*_pendulum())
    assert good["status"] == "scored" and 0.0 <= good["pt"] < 3.0
    assert good["pt_refused"] is None and good["A0"] > pts.MIN_INTERPRETABLE_A0_DEG
    assert good["top_term"] in pts._PARAM_KEYS
    assert set(pts._PARAM_KEYS) <= set(good["params"])
    small = cpd.score_angle(*_pendulum(a0=12.0))
    assert small["status"] == "refused: excursion too small"
    assert small["pt"] is None and small["pt_refused"] is not None


def test_score_angle_never_raises():
    class Boom:
        ALL_PT_KEYS = ()

        @staticmethod
        def compute_pt_params(*a, **k):
            raise ValueError("no")
    assert cpd.score_angle([0, 1], [0, 1], pt=Boom, reason_class=str)["status"] == \
        "error: scorer raised ValueError"
    flat = cpd.score_angle(np.arange(0, 5, 0.01), np.full(500, 180.0))
    assert flat["status"] != "scored" and flat.get("pt") is None


def test_phone_replay_names_the_reference_config_and_tick(monkeypatch):
    """The corpus replay must not read a config file: PR #128's table was
    withdrawn because a worktree fell back to ema_alpha 0.3 in silence."""
    import workbench_engine as engine
    seen = {}

    def replay(samples, params, tick_s=None, capture=None):
        seen.update(params=params, tick_s=tick_s)
        capture["axis_fit"] = {"flags": ["axis_fit_spans_quiet"]}
        return np.arange(3.0), np.zeros(3)

    monkeypatch.setattr(engine, "validate_component_csv", lambda path, kind: {"ok": True})
    monkeypatch.setattr(engine, "bind_split_csv_components", lambda v: {"fusion_samples": []})
    monkeypatch.setattr(imu_calibration_tuner, "replay_trial", replay)
    monkeypatch.setattr(imu_calibration_config, "load_config",
                        lambda: pytest.fail("the replay read the persisted config"))
    out = cpd.replay_phone({k: k for k in ("accel", "gyro", "mag", "imu")})
    assert seen["params"] == imu_calibration_config.REFERENCE_REPLAY_CONFIG
    assert seen["params"] is not imu_calibration_config.REFERENCE_REPLAY_CONFIG   # a copy
    assert seen["tick_s"] == imu_calibration_tuner.ANALYSIS_TICK_S
    assert out["axis_fit"]["flags"] == ["axis_fit_spans_quiet"]


def test_is_baseline_matches_the_derivation_rule():
    assert cpd.is_baseline("pre") and cpd.is_baseline("Pre_duo") and cpd.is_baseline("default")
    assert not cpd.is_baseline("post") and not cpd.is_baseline("control")


def _meta():
    return {"formula": "F", "footing": "foot", "config": "REFERENCE_REPLAY_CONFIG", "tick": 0.01,
            "ref_n_trials": 2, "ref_n_legs": 1, "ref_n_pids": 1, "ref_pids": "1",
            "shipped_source": "test",
            "shipped": {k: 1.0 for k in cpd.DATA_DERIVED_SCORED},
            "rederived": {k: 1.0 for k in cpd.DATA_DERIVED_SCORED}}


def test_tables_keep_excluded_and_other_groups_out_of_the_control_rows():
    rows = [_row("1", "left", 1, shank=0.10, phone_screen=0.15),
            _row("1", "left", 2, shank=0.30, phone_screen="refused: excursion too small"),
            _row("3", "left", 1, excluded=True, shank=0.90),
            _row("5", "left", 1, group="MS", shank=0.70)]
    for r in rows[:2]:
        r["pid_in_healthy_ref"] = True
        r["shank_pt_loo"] = r["shank_pt"] + 0.01
    text = cpd.build_tables(rows, {}, (0.1709, 0.3528), _meta())
    assert text.startswith(cpd.BEGIN) and text.endswith(cpd.END)
    g2 = text.split("### G2")[1].split("### G3")[0]
    assert "| control | optical shank, as recorded | trial | 2 | 0.200 |" in g2
    assert "0.900" not in g2 and "0.700" not in g2
    assert "| phone, on-screen (detrend off) | 2 | 1 | 1 | 0 | 0 | refused: excursion too small x1 |" in text
    assert "| MS, baseline | optical shank, as recorded | trial | 1 | 0.700 |" in text
    assert "MS, all sessions" not in text            # every MS session here is a baseline: one set
    assert "| excluded control trials, shipped ref | optical shank, as recorded | trial | 1 | 0.900 |" in text
    assert "| all controls, own data left out | optical shank, as recorded | trial | 2 | 0.210 |" in text
    g8 = text.split("### G8")[1]
    assert "| optical shank, as recorded | 1 | 1/2 (50%) | 0/1 (0%) | does not hold |" in g8
    assert "| phone, on-screen (detrend off) | 1 | 1/1 (100%) | 1/1 (100%) | holds |" in g8


def test_tables_carry_no_paths():
    rows = [_row("1", "left", 1, shank=0.25)]
    rows[0]["optitrack_path"] = "SECRET_PATH"
    rows[0]["pid_in_healthy_ref"] = False
    assert "SECRET_PATH" not in cpd.build_tables(rows, {}, (0.1709, 0.3528), _meta())


def test_write_report_replaces_only_the_generated_block(tmp_path):
    path = tmp_path / "r.md"
    path.write_text(f"# Title\n\nprose\n\n{cpd.BEGIN}\nold\n{cpd.END}\n\n## After\n", encoding="utf-8")
    cpd.write_report(str(path), f"{cpd.BEGIN}\nnew\n{cpd.END}")
    text = path.read_text(encoding="utf-8")
    assert text == f"# Title\n\nprose\n\n{cpd.BEGIN}\nnew\n{cpd.END}\n\n## After\n"
    fresh = tmp_path / "new.md"
    cpd.write_report(str(fresh), f"{cpd.BEGIN}\nnew\n{cpd.END}")
    assert fresh.read_text(encoding="utf-8") == f"{cpd.BEGIN}\nnew\n{cpd.END}\n"


def test_per_trial_csv_leaves_out_paths_and_parameter_dicts(tmp_path):
    row = _row("1", "left", 1, shank=0.1)
    row.update(phone={"imu": "p"}, shank_params={"N": 6.0})
    out = tmp_path / "t.csv"
    cpd.write_per_trial(str(out), [row])
    header = out.read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "shank_pt" in header
    assert not {"phone", "optitrack_path", "shank_params"} & set(header)


def test_reference_marking_uses_the_derivation_rules_and_the_given_shipped_set(monkeypatch):
    import derive_healthy_ref as dhr
    monkeypatch.setattr(dhr, "_is_control", lambda pid: pid != "4")     # 4: folder under another study
    params = {"R2n": 1.2, "N": 7.0, "phi_max_ratio": 0.5, "f": 0.9, "area_ratio": 0.3}
    rows = [_row("1", "left", 1, loader=0.1, shank=0.1), _row("2", "left", 1, loader=0.1),
            _row("2", "left", 2, loader="refused: excursion too small"),
            _row("3", "left", 1, excluded=True, loader=0.1), _row("4", "left", 1, loader=0.1),
            _row("5", "left", 1, group="MS", loader=0.1)]
    rows[1]["baseline"] = False
    for r in rows:
        for c in ("loader", "shank"):
            if r[f"{c}_status"] == "scored":
                r[f"{c}_params"] = dict(params)
    ref = cpd.mark_reference_set(rows, pts.HEALTHY_REF, shipped_pids={"2"})
    assert [r["in_healthy_ref"] for r in rows] == [True, False, False, False, False, False]
    assert [t["pid"] for t in ref] == ["1"]
    assert rows[0]["pid_in_healthy_ref"] is False and rows[1]["pid_in_healthy_ref"] is True
    assert "pid_in_healthy_ref" not in rows[5]
    # against its own medians a trial has no phi, f or area_ratio penalty; R2n and N are above the anchors
    assert rows[0]["shank_pt_rederived"] == pytest.approx(0.0)
    assert "shank_pt_loo" not in rows[0]              # participant 1 is the whole set
    assert rows[1]["loader_pt_loo"] == pytest.approx(0.0)


def test_an_unusable_phone_trial_is_reported_by_class_without_a_path(monkeypatch):
    import imu_optitrack_validation as v
    monkeypatch.setattr(cpd, "replay_phone",
                        lambda c: (_ for _ in ()).throw(cpd.PhoneTrialUnusable("never zeroed, no release motion found")))
    row = cpd.evaluate({"phone": {"imu": "SECRET_PATH"}})
    assert row["phone_screen_status"] == row["phone_stored_status"] == \
        "error: replay, never zeroed, no release motion found"
    monkeypatch.setattr(cpd, "replay_phone", lambda c: (_ for _ in ()).throw(OSError("SECRET_PATH missing")))
    row = cpd.evaluate({"phone": {"imu": "SECRET_PATH"}})
    assert row["phone_screen_status"] == "error: replay, OSError"
    assert row["shank_status"] == row["loader_status"] == "absent"
