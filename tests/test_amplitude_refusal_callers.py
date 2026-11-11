"""What the report and web paths do with AmplitudeExceedsCurveError.

`compute_pt_params` raises it when A0 comes out larger than the curve it was
measured from. The Rust core refuses such a trial (returns None). These callers
do the equivalent: the trial is unscorable, and the reason is kept with it --
on the candidate row, on stderr with the trial's name, or in the ValueError the
web bridge already raises for an unscorable trial. Nothing here returns a
number for it, and nothing drops it without saying so.

The curve is the one mobile-imu-core's golden fixture TRIAL_A0_OVER_CURVE uses
(gen_fixtures.py): a slow sag largely cancelled by drift, which the settled-tail
correction legitimately takes past the raw span.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pendulastic_pt_score as pt
import pt_report_common as common


def _a0_over_curve():
    fs, n_hold = 50.0, 100
    t = np.arange(n_hold + int(12 * fs)) / fs
    ang = np.full(len(t), 180.0)
    x = t[n_hold:] - t[n_hold]
    ang[n_hold:] = 180.0 - 60.0 * (1.0 - np.exp(-x / 4.0))
    return t, ang + 2.5 * t


def _clean_swing():
    fs, n_hold = 100.0, 100
    t = np.arange(n_hold + 800) / fs
    ang = np.full(len(t), 180.0)
    x = t[n_hold:] - t[n_hold]
    ang[n_hold:] = 180.0 - 45.0 * (1.0 - np.exp(-x / 1.2) * np.cos(2 * np.pi * x))
    return t, ang


def test_the_curve_really_raises_and_the_clean_one_does_not():
    """Guards the rest of this file: if the scorer stops raising here, the
    tests below would pass without exercising anything."""
    with pytest.raises(pt.AmplitudeExceedsCurveError):
        pt.compute_pt_params(*_a0_over_curve())
    assert pt.compute_pt_params(*_clean_swing()) is not None


def test_score_trial_itself_still_raises():
    """The exception is caught where the trial can be named, not swallowed in
    the shared helper -- other callers of score_trial keep seeing it."""
    with pytest.raises(pt.AmplitudeExceedsCurveError):
        common.score_trial("13_left_pre", 1, *_a0_over_curve())


def _one_trial_tree(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})


def test_trial_candidates_lists_it_unscoreable_with_the_reason(tmp_path, monkeypatch, capsys):
    _one_trial_tree(tmp_path, monkeypatch)
    t, ang = _a0_over_curve()
    monkeypatch.setattr(common.pt, "load_optitrack_detailed",
                        lambda path: (t, ang, common.pt.TrialQuality(
                            coverage=1.0, warnings=("low coverage",))))

    candidates = common.trial_candidates("13", include_archive=False)

    assert len(candidates) == 1, "the trial must stay on the list"
    row = candidates[0]
    assert row["status"] == "unscoreable"
    assert row["record"] is None
    assert "A0 exceeds its own curve" in row["reason"]
    assert "low coverage" in row["reason"], "the loader's own warnings are kept alongside"
    err = capsys.readouterr().err
    assert "13_left_pre trial 1" in err and "A0 exceeds its own curve" in err


def test_collect_participant_leaves_it_out_and_names_it(monkeypatch, capsys):
    records = [
        {"participant": "13", "leg": "left", "condition": "pre", "trial": 1,
         "path": "bad.csv", "mtime": 1.0},
        {"participant": "13", "leg": "left", "condition": "pre", "trial": 2,
         "path": "good.csv", "mtime": 2.0},
    ]
    monkeypatch.setattr(common, "discover_all_trials", lambda include_archive=True: records)
    monkeypatch.setattr(common.pt, "load_optitrack",
                        lambda path: _a0_over_curve() if path == "bad.csv" else _clean_swing())

    by_leg_tp, _ = common.collect_participant("13")

    scored = by_leg_tp[("left", "pre")]
    assert [r["trial"] for r in scored] == [2], "the clean trial still scores"
    err = capsys.readouterr().err
    assert "13_left_pre trial 1" in err and "A0 exceeds its own curve" in err


def test_row5_source_score_is_absent_not_a_number(capsys):
    t, ang = _a0_over_curve()
    rec = {"pid": "13_left_pre", "trial": 1, "imu_curve": {"t": t, "ang": ang}}
    assert common._row5_source_pt7(rec, "imu_curve") == (None, None)
    assert "13_left_pre trial 1 (imu_curve)" in capsys.readouterr().err
    tc, ac = _clean_swing()
    score, params = common._row5_source_pt7({"imu_curve": {"t": tc, "ang": ac}}, "imu_curve")
    assert score is not None and params is not None


def test_web_bridge_reports_it_as_an_unscorable_trial(tmp_path):
    pd = pytest.importorskip("pandas")
    from web.api.services.pt_score_bridge import PTScoreBridge

    t, ang = _a0_over_curve()
    csv_path = tmp_path / "trial.csv"
    pd.DataFrame({"time_sec": t, "knee_angle_deg": ang}).to_csv(csv_path, index=False)
    with pytest.raises(ValueError, match="cannot be scored.*exceeds the curve"):
        PTScoreBridge().compute(str(csv_path), {"id": "t1"})
