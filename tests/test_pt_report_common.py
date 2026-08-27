import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import builtins
import json
import pytest

import pt_report_common as common
import run_pt_analysis


def test_leg_trial_counts_sums_across_conditions_for_one_participant(monkeypatch):
    fake_records = [
        {"participant": "13", "leg": "right", "condition": "pre"},
        {"participant": "13", "leg": "right", "condition": "post"},
        {"participant": "13", "leg": "left", "condition": "pre"},
        {"participant": "14", "leg": "right", "condition": "pre"},
    ]
    monkeypatch.setattr(common, "discover_all_trials", lambda: fake_records)
    assert common.leg_trial_counts("13") == {"left": 1, "right": 2}


def test_leg_trial_counts_zero_for_unknown_participant(monkeypatch):
    monkeypatch.setattr(common, "discover_all_trials", lambda: [])
    assert common.leg_trial_counts("99") == {"left": 0, "right": 0}


def test_run_pt_analysis_leg_trial_counts_is_common_function():
    assert run_pt_analysis.leg_trial_counts is common.leg_trial_counts


def test_first_recording_time_returns_earliest_mtime_for_participant(monkeypatch):
    fake_records = [
        {"participant": "13", "leg": "right", "mtime": 300.0},
        {"participant": "13", "leg": "left", "mtime": 100.0},
        {"participant": "14", "leg": "right", "mtime": 1.0},
    ]
    monkeypatch.setattr(common, "discover_all_trials", lambda: fake_records)
    assert common.first_recording_time("13") == 100.0


def test_first_recording_time_none_when_no_trials(monkeypatch):
    monkeypatch.setattr(common, "discover_all_trials", lambda: [])
    assert common.first_recording_time("13") is None


def test_release_aligned_hpe_curve_aligns_release_to_zero():
    import numpy as np
    t = np.linspace(0, 3, 90)
    # Held at 180 for 1s, then swings down -- same shape release_aligned_waveform's
    # own docstring describes for OptiTrack.
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is not None
    t_plot, a_plot = result
    # Release should land near t=0 in the shifted output -- the original
    # hold-then-swing transition was at t=1.0 in input coordinates.
    assert abs(t_plot[np.argmin(np.abs(t_plot))]) < 0.2


def test_release_aligned_hpe_curve_rejects_too_few_samples():
    import numpy as np
    result = common.release_aligned_hpe_curve(np.array([0.0, 0.1, 0.2]), np.array([180.0, 179.0, 178.0]))
    assert result is None


def test_release_aligned_hpe_curve_rejects_non_monotonic_time():
    import numpy as np
    t = np.array([0.0, 0.2, 0.1, 0.3, 0.4])
    ang = np.array([180.0, 179.0, 178.0, 177.0, 176.0])
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is None


def test_release_aligned_hpe_curve_rejects_release_index_too_early_for_a_reliable_baseline(monkeypatch):
    """Confirmed against real participant data (2026-08-10 full-report-hpe-
    accuracy plan, Task 14 manual verification): when _detect_release's
    adaptive threshold fires almost immediately -- a handful of noisy
    samples at the very start of a curve, not a real pre-release hold --
    using that index as the alignment anchor shifted a real MediaPipe
    curve to physically implausible knee angles (~260 degrees, vs
    OptiTrack's ~150). The global constraint forbids modifying
    _detect_release's own algorithm, so release_aligned_hpe_curve instead
    refuses to trust an anchor with too few pre-release samples to
    represent a real hold baseline."""
    import numpy as np
    t = np.linspace(0, 3, 90)
    ang = 140.0 + 20.0 * np.sin(t)
    monkeypatch.setattr(common.pt, "_detect_release", lambda t_, _a: 1)
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is None


def test_release_aligned_hpe_curve_keeps_working_when_release_index_has_enough_baseline(monkeypatch):
    """The early-release guard must not reject the ordinary case -- plenty
    of samples precede a normally-detected release."""
    import numpy as np
    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    monkeypatch.setattr(common.pt, "_detect_release", lambda t_, _a: 30)
    result = common.release_aligned_hpe_curve(t, ang)
    assert result is not None


def test_trial_candidates_classifies_invalid_path(tmp_path, monkeypatch):
    invalid_dir = tmp_path / "Participant_13_left" / "INVALID_bad_run"
    invalid_dir.mkdir(parents=True)
    csv_path = invalid_dir / "trial_1_optitrack.csv"
    csv_path.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "invalid_path"


def test_trial_candidates_classifies_unparseable(tmp_path, monkeypatch):
    # No participant number in the path at all -- _parse_trial_path returns None.
    bad_dir = tmp_path / "left"
    bad_dir.mkdir(parents=True)
    csv_path = bad_dir / "trial_1_optitrack.csv"
    csv_path.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unparseable"


def test_trial_candidates_classifies_excluded_with_reason(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    csv_path = rec_dir / "trial_1_optitrack.csv"
    csv_path.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "excluded"
    assert candidates[0]["reason"] == "active muscle intervention"


def test_trial_candidates_classifies_unreadable(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    csv_path = rec_dir / "trial_1_optitrack.csv"
    csv_path.write_text("not,a,valid,optitrack,csv\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack_detailed",
                        lambda path: (_ for _ in ()).throw(ValueError("bad csv")))

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unreadable"
    # "unreadable" must mean the file could not be parsed, and must say why.
    assert "bad csv" in candidates[0]["reason"]


def test_trial_candidates_classifies_unscoreable_and_scored(tmp_path, monkeypatch):
    import numpy as np
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack_detailed",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 179.0]),
                                      common.pt.TrialQuality(coverage=1.0, warnings=())))
    monkeypatch.setattr(common, "score_trial", lambda pid, trial, t, angle: None)

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unscoreable"

    monkeypatch.setattr(common, "score_trial",
                        lambda pid, trial, t, angle: {"pid": pid, "trial": trial, "pt7": 0.5})
    candidates = common.trial_candidates("13", include_archive=False)
    assert candidates[0]["status"] == "scored"
    assert candidates[0]["record"]["pt7"] == 0.5
    assert candidates[0]["quality"].coverage == 1.0
    assert candidates[0]["reason"] is None, "a clean trial must carry no reason"


def test_poor_quality_trial_is_scored_and_flagged_never_dropped(tmp_path, monkeypatch):
    """The 2026-08-27 policy: the loader flags, the operator excludes.

    A trial the cameras half-missed must still reach the report as "scored",
    carrying its warnings, so the operator can see it and decide. Before this,
    a coverage gate raised inside the loader and the trial silently became
    "unreadable" -- which emptied P21's whole right leg out of the report."""
    import numpy as np
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack_detailed",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 179.0]),
                                      common.pt.TrialQuality(
                                          coverage=0.576,
                                          warnings=("Optical coverage 57.6% is below 90%.",))))
    monkeypatch.setattr(common, "score_trial",
                        lambda pid, trial, t, angle: {"pid": pid, "trial": trial, "pt7": 0.5})

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "scored", "a poor trial must not be dropped"
    assert candidates[0]["quality"].coverage == 0.576
    assert "57.6%" in candidates[0]["reason"], "the operator must be told why"


def test_only_excluded_trials_json_removes_a_trial(tmp_path, monkeypatch):
    """The single exclusion mechanism. Nothing else may drop a trial."""
    import numpy as np
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common.pt, "load_optitrack_detailed",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 179.0]),
                                      common.pt.TrialQuality(coverage=0.1, warnings=("awful",))))
    monkeypatch.setattr(common, "score_trial",
                        lambda pid, trial, t, angle: {"pid": pid, "trial": trial, "pt7": 0.5})

    # Terrible data, but nobody excluded it -> it still reaches the report.
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    assert common.trial_candidates("13", include_archive=False)[0]["status"] == "scored"

    # Same trial, now named in excluded_trials.json -> and only now dropped.
    key = common.trial_key("13", "left", "pre", 1)
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {key: "operator says so"})
    got = common.trial_candidates("13", include_archive=False)[0]
    assert got["status"] == "excluded"
    assert got["reason"] == "operator says so"


def test_trial_candidates_only_this_participant(tmp_path, monkeypatch):
    for pid in ("13", "14"):
        rec_dir = tmp_path / f"Participant_{pid}_left_pre"
        rec_dir.mkdir(parents=True)
        (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack", lambda path: (_ for _ in ()).throw(ValueError()))

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1


def test_clinician_mas_matches_local_import_does_not_raise(tmp_path, monkeypatch):
    """Regression test for the circular-import risk: calling this function
    must not raise ImportError/circular-import errors."""
    empty_csv = tmp_path / "mas_scores.csv"
    empty_csv.write_text("participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(empty_csv))
    result = common.clinician_mas_matches("13", "left", "pre")
    assert result == []


def test_clinician_mas_matches_returns_all_matches_sorted_by_date(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,pre,MS,1,VL,8/6/2026\n"
        "13,left,pre,MS,1+,VL,12/1/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    result = common.clinician_mas_matches("13", "left", "pre")
    assert len(result) == 2
    # 12/1/2026 is chronologically AFTER 8/6/2026 -- lexicographic string
    # sort would get this backwards ("1" < "8"). Most-recent-first means
    # the 12/1/2026 row comes first.
    assert result[0]["assessed_date"] == "12/1/2026"
    assert result[1]["assessed_date"] == "8/6/2026"


def test_clinician_mas_matches_blank_date_sorts_last(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,pre,MS,1,VL,\n"
        "13,left,pre,MS,1+,VL,8/6/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    result = common.clinician_mas_matches("13", "left", "pre")
    assert result[0]["assessed_date"] == "8/6/2026"
    assert result[1]["assessed_date"] == ""


def test_clinician_mas_matches_excludes_invalid_grade(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,pre,MS,not_a_grade,VL,8/6/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    assert common.clinician_mas_matches("13", "left", "pre") == []


def test_clinician_mas_matches_condition_bag_of_tokens(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,left,1 week post,MS,2,VL,8/6/2026\n"
    )
    import mas_validation as mv
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))

    # "week_1_post" and "1 week post" tokenize to the same set.
    result = common.clinician_mas_matches("13", "left", "week_1_post")
    assert len(result) == 1


def test_write_clinician_mas_sidecar_writes_every_match(tmp_path):
    matches_by_leg_condition = {
        ("left", "pre"): [
            {"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1",
            "assessed_by": "VL", "assessed_date": "12/1/2026"},
            {"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1+",
            "assessed_by": "VL", "assessed_date": "8/6/2026"},
        ],
        ("right", "pre"): [
            {"participant": "13", "leg": "right", "condition": "pre", "mas_grade": "2",
            "assessed_by": "VL", "assessed_date": "8/6/2026"},
        ],
    }
    out_path = common.write_clinician_mas_sidecar("13", matches_by_leg_condition, out_dir=str(tmp_path))

    import csv
    with open(out_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3
    assert {r["mas_grade"] for r in rows} == {"1", "1+", "2"}


def test_write_clinician_mas_sidecar_empty_matches_still_writes_header(tmp_path):
    out_path = common.write_clinician_mas_sidecar("13", {}, out_dir=str(tmp_path))
    import csv
    with open(out_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == []


def test_draw_rmse_axes_returns_true_when_bars_drawn():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    rec = {"trial": "1", "mediapipe_rmse": 3.2, "imu_rmse": None}
    by_leg_tp = {"left": [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    any_bars = common._draw_rmse_axes(ax, "left", {("left", "pre"): [rec]}, timepoints)

    assert any_bars is True
    plt.close(fig)


def test_draw_rmse_axes_returns_false_when_no_data():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    any_bars = common._draw_rmse_axes(ax, "left", {}, [("pre", "Pre", "#d62728")])
    assert any_bars is False
    plt.close(fig)


def test_make_rmse_figure_unchanged_behavior(tmp_path, monkeypatch):
    """Regression: make_rmse_figure()'s own external contract (return
    shape, output file) must be identical after the extraction. Post
    Task 2, attach_rmse() derives mediapipe_rmse/imu_rmse from a live
    load_hpe_model_curves() lookup rather than leaving preset dict values
    alone, so the fixture mocks that lookup instead of presetting the
    RMSE fields directly."""
    import numpy as np
    monkeypatch.setattr(common, "OUT_DIR", str(tmp_path))
    fake_curves = [
        {"name": "mediapipe", "t": np.array([0.0]), "ang": np.array([180.0]), "rmse": 3.2},
        {"name": "imu_viewer", "t": np.array([0.0]), "ang": np.array([180.0]), "rmse": 4.1},
    ]
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: fake_curves)
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": np.array([0.0]),
          "angle_raw": np.array([180.0]), "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec], ("right", "pre"): []}
    timepoints = [("pre", "Pre", "#d62728")]

    out_path, any_bars = common.make_rmse_figure("P13", by_leg_tp, timepoints, "P13_rmse.png")

    assert any_bars is True
    assert out_path == str(tmp_path / "P13_rmse.png") or out_path == os.path.join(str(tmp_path), "P13_rmse.png")
    assert os.path.isfile(out_path)


def test_hpe_overlay_series_includes_both_sources_when_present():
    import numpy as np
    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    rec = {"mediapipe_curve": {"t": t, "ang": ang}, "imu_curve": {"t": t, "ang": ang}}

    series = common._hpe_overlay_series(rec)

    labels = {s[0] for s in series}
    assert labels == {"MediaPipe", "IMU"}
    linestyles = {s[0]: s[1] for s in series}
    assert linestyles["MediaPipe"] == "--"
    assert linestyles["IMU"] == ":"


def test_hpe_overlay_series_skips_missing_curve():
    rec = {"mediapipe_curve": None, "imu_curve": None}
    assert common._hpe_overlay_series(rec) == []


def test_hpe_overlay_series_skips_alignment_failure(monkeypatch):
    import numpy as np
    rec = {"mediapipe_curve": {"t": np.array([0.0]), "ang": np.array([180.0])}, "imu_curve": None}
    # Single-sample curve fails release_aligned_hpe_curve's own validation (< 4 samples).
    series = common._hpe_overlay_series(rec)
    assert series == []


def test_draw_row5_table_shows_per_source_paired_baselines(monkeypatch):
    import numpy as np
    import matplotlib.pyplot as plt

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))

    def fake_pt7(params):
        return 0.25

    monkeypatch.setattr(common.pt, "compute_pt_params", lambda t_, _a: {"fake": True})
    monkeypatch.setattr(common.pt, "compute_pt_score", fake_pt7)
    monkeypatch.setattr(common.pt, "pt_to_mas", lambda score: "1")
    monkeypatch.setattr(common.pt, "load_hpe_model_curves",
                        lambda *a, **k: ([{"name": "mediapipe", "t": t, "ang": ang, "rmse": 2.0}], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])

    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": t, "angle_raw": ang,
          "neutral_deg_raw": 180.0, "mediapipe_curve": {"t": t, "ang": ang}, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    matches = common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")

    assert isinstance(matches, dict)
    plt.close(fig)


def test_draw_row5_table_truncates_clinician_mas_to_two_most_recent_with_more_note(monkeypatch):
    """Task 14's manual-verification step calls for confirming this
    truncation policy against a real dense participant, but no
    participant in the current mas_scores.csv has 3+ MAS rows for the
    same leg/condition to exercise it -- a real-data gap, not a code
    gap. Covered directly here instead: clinician_mas_matches() can
    return more than 2 matches (it's the untruncated source, per its own
    docstring); the table must show only the 2 most recent with a
    "+N more" note, while write_clinician_mas_sidecar (Task 6) still
    gets the full, untruncated list via this function's return value."""
    import numpy as np
    import matplotlib.pyplot as plt

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    many_matches = [
        {"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1",
         "assessed_by": "VL", "assessed_date": "12/1/2026"},
        {"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "8/6/2026"},
        {"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "2",
         "assessed_by": "WD", "assessed_date": "1/1/2026"},
    ]

    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: ([], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: many_matches)

    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": t, "angle_raw": ang,
          "neutral_deg_raw": 180.0, "mediapipe_curve": None, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    matches = common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")

    # write_clinician_mas_sidecar's contract: the returned matches are
    # untruncated, regardless of what the table itself displays.
    assert matches[("left", "pre")] == many_matches

    tbl = ax.tables[0]
    clin_cell_text = tbl.get_celld()[(1, 7)].get_text().get_text()
    assert "+1 more" in clin_cell_text
    assert "1 (12/1/2026)" in clin_cell_text
    assert "1+ (8/6/2026)" in clin_cell_text
    assert "2 (1/1/2026)" not in clin_cell_text
    plt.close(fig)


def test_draw_row5_table_three_stage_accounting_distinguishes_gate_from_scoring(monkeypatch):
    """The whole point of Task 1's return_rejected mode: a trial can have a
    candidate CSV that gets REJECTED by the quality gate (never reaches
    attach_rmse's rec["mediapipe_curve"] at all), which must show up as
    "had a candidate" but NOT "passed gate" -- distinct from a trial with
    zero candidate CSVs at all. This regression-tests that _draw_row5_table
    actually calls load_hpe_model_curves(return_rejected=True) itself
    rather than only reading attach_rmse's already-filtered rec["mediapipe_curve"]."""
    import numpy as np
    import matplotlib.pyplot as plt

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    call_log = []

    def fake_load_curves(pid, pos, trial, t_opti, angle_raw, neutral_deg, csv_files=None, return_rejected=False):
        call_log.append(return_rejected)
        assert return_rejected is True, "Row 5 must request rejection accounting, not the default"
        if trial == "1":
            return [], [{"name": "mediapipe", "reason": "did_not_track_swing"}]
        return [], []

    monkeypatch.setattr(common.pt, "load_hpe_model_curves", fake_load_curves)
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])

    rec_rejected = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": t, "angle_raw": ang,
                    "neutral_deg_raw": 180.0, "mediapipe_curve": None, "imu_curve": None}
    rec_no_candidate = {"pid": "13_left_pre", "trial": "2", "pt7": 0.32, "t_raw": t, "angle_raw": ang,
                        "neutral_deg_raw": 180.0, "mediapipe_curve": None, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec_rejected, rec_no_candidate]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")

    assert True in call_log   # confirms return_rejected=True was actually requested
    plt.close(fig)


def test_draw_row5_table_returns_clinician_matches_used(monkeypatch):
    import matplotlib.pyplot as plt
    fake_matches = [{"participant": "13", "leg": "left", "condition": "pre", "mas_grade": "1",
                    "assessed_by": "VL", "assessed_date": "8/6/2026"}]
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: fake_matches)
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: ([], []))

    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": [0.0], "angle_raw": [180.0],
          "neutral_deg_raw": 180.0, "mediapipe_curve": None, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    matches = common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")

    assert matches[("left", "pre")] == fake_matches
    plt.close(fig)


def test_draw_row5_table_empty_timepoints_does_not_raise():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    matches = common._draw_row5_table(ax, "left", {}, [], "13")
    assert matches == {}
    plt.close(fig)


def test_draw_row5_table_columns_sized_to_content(monkeypatch):
    """Regression: found via Task 14 manual verification against real
    participant 14 data -- ax.table() defaults every column to equal
    width regardless of content length, so the narrow "delta" column's
    wide numeric text (e.g. "+0.747") visually overlapped the neighboring
    "Accounting" column's long text, and long headers overlapped their
    neighbors too. auto_set_column_width() must be called so a column
    with long content (Accounting) ends up wider than one with short
    content (the bare "delta" symbol column)."""
    import numpy as np
    import matplotlib.pyplot as plt

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))

    monkeypatch.setattr(common.pt, "compute_pt_params", lambda t_, _a: {"fake": True})
    monkeypatch.setattr(common.pt, "compute_pt_score", lambda params: 0.25)
    monkeypatch.setattr(common.pt, "pt_to_mas", lambda score: "1")
    monkeypatch.setattr(common.pt, "load_hpe_model_curves",
                        lambda *a, **k: ([{"name": "mediapipe", "t": t, "ang": ang, "rmse": 2.0}], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])

    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30, "t_raw": t, "angle_raw": ang,
          "neutral_deg_raw": 180.0, "mediapipe_curve": {"t": t, "ang": ang}, "imu_curve": None}
    by_leg_tp = {("left", "pre"): [rec]}
    timepoints = [("pre", "Pre", "#d62728")]

    fig, ax = plt.subplots()
    common._draw_row5_table(ax, "left", by_leg_tp, timepoints, "13")
    fig.canvas.draw()   # auto_set_column_width needs a renderer to measure cell text

    tbl = ax.tables[0]
    col_widths = {}
    for (row, col), cell in tbl.get_celld().items():
        if row == 0:   # header row
            col_widths[col] = cell.get_width()
    accounting_col = 5   # "Accounting" -- long text
    delta_col = 4         # "Δ" -- short text
    assert col_widths[accounting_col] > col_widths[delta_col]
    plt.close(fig)


def test_build_caption_text_includes_cohort_reference_when_snapshot_present(monkeypatch):
    import pt_cohort_common as pcc
    fake_ref = {"ms_median": 0.41, "ms_n": 2, "control_median": 0.15, "control_n": 3,
               "leave_one_out_arm": "MS"}
    monkeypatch.setattr(pcc, "leg_cohort_reference", lambda snap, pid, leg: fake_ref)
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: [])

    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.30}
    text = common._build_caption_text("P13", "13", {("left", "pre"): [rec]}, [("pre", "Pre", "#d62728")],
                                      cohort_snapshot={"ms_pids": ["13"], "control_pids": ["6"]})

    assert "MS arm median" in text
    assert "0.41" in text
    assert "leave-one-out" in text


def test_build_caption_text_omits_cohort_reference_when_snapshot_none():
    text = common._build_caption_text("P13", "13", {("left", "pre"): []}, [("pre", "Pre", "#d62728")],
                                      cohort_snapshot=None)
    assert "MS arm median" not in text


def test_build_caption_text_includes_data_completeness(monkeypatch):
    fake_candidates = [
        {"leg": "left", "condition": "pre", "status": "scored"},
        {"leg": "left", "condition": "pre", "status": "scored"},
        {"leg": "left", "condition": "pre", "status": "excluded", "reason": "active muscle"},
        {"leg": "left", "condition": "pre", "status": "unreadable"},
    ]
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: fake_candidates)

    text = common._build_caption_text("P13", "13", {("left", "pre"): []}, [("pre", "Pre", "#d62728")],
                                      cohort_snapshot=None)

    assert "Left/pre" in text or "Left/Pre" in text
    assert "2 scored" in text
    assert "1 excluded" in text
    assert "1 unreadable" in text


def test_attach_rmse_keeps_curve_arrays(monkeypatch):
    import numpy as np
    fake_curves = [
        {"name": "mediapipe", "t": np.array([0.0, 1.0]), "ang": np.array([180.0, 150.0]), "rmse": 3.5},
        {"name": "imu_viewer", "t": np.array([0.0, 1.0]), "ang": np.array([180.0, 155.0]), "rmse": 5.0},
    ]
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": np.array([0.0, 1.0]),
          "angle_raw": np.array([180.0, 152.0]), "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: fake_curves)

    common.attach_rmse(by_leg_tp)

    assert rec["mediapipe_rmse"] == 3.5
    assert rec["mediapipe_curve"] is not None
    assert list(rec["mediapipe_curve"]["ang"]) == [180.0, 150.0]
    assert rec["imu_rmse"] == 5.0
    assert rec["imu_curve"] is not None


def test_attach_rmse_deterministic_candidate_not_overwritten(monkeypatch):
    """Two mediapipe-name matches -- today's loop silently keeps whichever
    came last. Curves arrive sorted best-RMSE-first, so the FIRST match
    (RMSE=2.0) must win, not the second (RMSE=9.0)."""
    import numpy as np
    fake_curves = [
        {"name": "mediapipe", "t": np.array([0.0]), "ang": np.array([180.0]), "rmse": 2.0},
        {"name": "mediapipe_alt", "t": np.array([0.0]), "ang": np.array([170.0]), "rmse": 9.0},
    ]
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": np.array([0.0]),
          "angle_raw": np.array([180.0]), "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: fake_curves)

    common.attach_rmse(by_leg_tp)

    assert rec["mediapipe_rmse"] == 2.0
    assert list(rec["mediapipe_curve"]["ang"]) == [180.0]


def test_attach_rmse_no_curves_leaves_curve_fields_none(monkeypatch):
    rec = {"pid": "13_left_pre", "trial": "1", "t_raw": [0.0],
          "angle_raw": [180.0], "neutral_deg_raw": 180.0}
    by_leg_tp = {("left", "pre"): [rec]}
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: [])

    common.attach_rmse(by_leg_tp)

    assert rec.get("mediapipe_curve") is None
    assert rec.get("imu_curve") is None


def test_make_report_figure_5x2_grid_with_all_rows(tmp_path, monkeypatch):
    import numpy as np
    monkeypatch.setattr(common, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: ([], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: [])

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    rec = {"pid": "13_left_pre", "trial": "1", "pt7": 0.3, "t_raw": t, "angle_raw": ang,
          "neutral_deg_raw": 180.0, "R2n": 0.9, "N": 3.0, "phi_max_ratio": 0.5,
          "omega_max_n": 1.0, "omega_min_n": 0.2, "f": 1.5, "area_ratio": 0.1,
          "mediapipe_curve": None, "imu_curve": None, "mediapipe_rmse": None, "imu_rmse": None}
    by_leg_tp = {("left", "pre"): [rec], ("right", "pre"): []}
    timepoints = [("pre", "Pre", "#d62728")]

    out_path, fig = common.make_report_figure("P13", by_leg_tp, timepoints, "P13_full_report.png",
                                               "test caveat", cohort_snapshot=None,
                                               save=False, return_fig=True)

    assert len(fig.axes) >= 10   # 5 rows x 2 cols, at minimum (table axes count too)
    import matplotlib.pyplot as plt
    plt.close(fig)


def test_make_report_figure_default_cohort_snapshot_none_does_not_raise(tmp_path, monkeypatch):
    """Existing callers (p13_full_report.py, p5_full_report.py,
    run_pt_analysis.py before Task 13) don't pass cohort_snapshot -- the
    default must keep working exactly as before this task."""
    monkeypatch.setattr(common, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(common.pt, "load_hpe_model_curves", lambda *a, **k: ([], []))
    monkeypatch.setattr(common, "clinician_mas_matches", lambda pid, leg, cond: [])
    monkeypatch.setattr(common, "trial_candidates", lambda pid, include_archive=True: [])

    out_path = common.make_report_figure("P13", {}, [], "P13_full_report.png", "caveat")
    assert out_path == os.path.join(str(tmp_path), "P13_full_report.png")
    assert os.path.isfile(out_path)


def test_main_builds_cohort_snapshot_once_before_participant_loop(monkeypatch):
    calls = []
    monkeypatch.setattr(run_pt_analysis.common, "list_participants", lambda: {"13": {}})
    monkeypatch.setattr(run_pt_analysis, "run_for_participant",
                        lambda pid, cohort_snapshot=None: calls.append(("run_for_participant", pid, cohort_snapshot)) or [])
    fake_snapshot = {"ms_pids": [], "control_pids": []}
    monkeypatch.setattr(run_pt_analysis.pt_cohort_common, "build_cohort_snapshot",
                        lambda: (calls.append(("build_cohort_snapshot",)) or fake_snapshot))
    monkeypatch.setattr(run_pt_analysis.pt_cohort_common, "write_cohort_artifacts",
                        lambda snap: calls.append(("write_cohort_artifacts", snap)))
    monkeypatch.setattr(run_pt_analysis, "_mas_scored_participants", lambda: set())
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py"])

    run_pt_analysis.main()

    assert calls[0] == ("build_cohort_snapshot",)
    assert calls[1] == ("run_for_participant", "13", fake_snapshot)
    assert calls[2] == ("write_cohort_artifacts", fake_snapshot)


def test_discover_all_trials_default_shape_unchanged(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    records = common.discover_all_trials(include_archive=False)
    assert len(records) == 1
    assert "trial_key" not in records[0]
    assert "excluded" not in records[0]


def test_discover_all_trials_include_excluded_adds_fields_and_keeps_excluded(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    (rec_dir / "trial_2_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    records = common.discover_all_trials(include_archive=False, include_excluded=True)
    assert len(records) == 2
    by_trial = {r["trial"]: r for r in records}
    assert by_trial["1"]["excluded"] is True
    assert by_trial["1"]["trial_key"] == common.trial_key("13", "left", "pre", "1")
    assert by_trial["2"]["excluded"] is False

    # Default (include_excluded=False) still drops the excluded trial entirely.
    records_default = common.discover_all_trials(include_archive=False)
    assert len(records_default) == 1
    assert records_default[0]["trial"] == "2"


def test_discover_all_trials_skips_record_whose_getmtime_raises(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    good = rec_dir / "trial_1_optitrack.csv"
    good.write_text("t,angle\n0,180\n")
    bad = rec_dir / "trial_2_optitrack.csv"
    bad.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    real_getmtime = os.path.getmtime

    def flaky_getmtime(path):
        if path == str(bad):
            raise OSError("deleted mid-scan")
        return real_getmtime(path)

    monkeypatch.setattr(common.os.path, "getmtime", flaky_getmtime)

    records = common.discover_all_trials(include_archive=False)
    assert len(records) == 1
    assert records[0]["trial"] == "1"


def test_list_participants_default_hides_fully_excluded_participant(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    assert common.list_participants(include_archive=False) == {}


def test_list_participants_include_excluded_shows_zero_trial_participant(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    result = common.list_participants(include_archive=False, include_excluded=True)
    assert result == {"13": {"legs": set(), "conditions": set(), "n_trials": 0}}


def test_duplicate_trial_keys_empty_for_common_case():
    records = [
        {"trial_key": "13_left_pre_T1", "path": "/a/trial_1.csv"},
        {"trial_key": "13_left_pre_T2", "path": "/a/trial_2.csv"},
    ]
    assert common.duplicate_trial_keys(records) == {}


def test_duplicate_trial_keys_finds_collision():
    records = [
        {"trial_key": "13_left_pre_T1", "path": "/a/trial_1.csv"},
        {"trial_key": "13_left_pre_T1", "path": "/a_dup/trial_1.csv"},
        {"trial_key": "13_left_pre_T2", "path": "/a/trial_2.csv"},
    ]
    assert common.duplicate_trial_keys(records) == {
        "13_left_pre_T1": ["/a/trial_1.csv", "/a_dup/trial_1.csv"],
    }


def test_duplicate_trial_keys_catches_excluded_and_nonexcluded_collision():
    records = [
        {"trial_key": "13_left_pre_T1", "path": "/a/trial_1.csv", "excluded": True},
        {"trial_key": "13_left_pre_T1", "path": "/a_dup/trial_1.csv", "excluded": False},
    ]
    dupes = common.duplicate_trial_keys(records)
    assert set(dupes["13_left_pre_T1"]) == {"/a/trial_1.csv", "/a_dup/trial_1.csv"}


def test_duplicate_trial_keys_no_internal_discovery_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("duplicate_trial_keys must not call discover_all_trials")
    monkeypatch.setattr(common, "discover_all_trials", boom)
    assert common.duplicate_trial_keys([{"trial_key": "k", "path": "/p"}]) == {}


def test_set_trials_excluded_dedupes_duplicate_input_keys(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    common.set_trials_excluded(["k1", "k1"], True)

    with open(reg_path) as f:
        data = json.load(f)
    assert data == {"k1": "excluded via Analysis panel"}


def test_set_trials_excluded_true_then_false_round_trips(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    common.set_trials_excluded(["k1"], True)
    assert "k1" in common.load_excluded_trials()

    common.set_trials_excluded(["k1"], False)
    assert "k1" not in common.load_excluded_trials()


def test_set_trials_excluded_preserves_unrelated_entries(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps({"other_key": "pre-existing reason"}))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    common.set_trials_excluded(["k1"], True)

    data = common.load_excluded_trials()
    assert data["other_key"] == "pre-existing reason"
    assert data["k1"] == "excluded via Analysis panel"


def test_set_trials_excluded_atomic_write_uses_same_directory(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    seen_dirs = []
    real_mkstemp = common.tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        seen_dirs.append(kwargs.get("dir"))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(common.tempfile, "mkstemp", spy_mkstemp)
    common.set_trials_excluded(["k1"], True)
    assert seen_dirs == [str(tmp_path)]


def test_set_trials_excluded_cleans_up_temp_file_on_replace_failure(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    # Pre-seed a real registry: the spec's requirement is that a failed
    # os.replace leaves the ORIGINAL file's content untouched, which an
    # empty directory can't demonstrate.
    original_content = json.dumps({"other_key": "pre-existing reason"})
    reg_path.write_text(original_content)
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    def failing_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(common.os, "replace", failing_replace)
    with pytest.raises(OSError):
        common.set_trials_excluded(["k1"], True)

    assert reg_path.read_text() == original_content
    # ...and no stray temp file was left behind next to it.
    assert list(tmp_path.iterdir()) == [reg_path]


def test_set_trials_excluded_raises_on_malformed_json(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text("{not valid json")
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    original_bytes = reg_path.read_bytes()

    with pytest.raises(common.RegistryCorruptError):
        common.set_trials_excluded(["k1"], True)

    assert reg_path.read_bytes() == original_bytes
    # Read path (load_excluded_trials) still degrades to {} unchanged --
    # the two paths intentionally diverge (spec Section 6).
    assert common.load_excluded_trials() == {}


def test_set_trials_excluded_raises_on_wrong_shape_json(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps(["not", "a", "dict"]))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    original_bytes = reg_path.read_bytes()

    with pytest.raises(common.RegistryCorruptError):
        common.set_trials_excluded(["k1"], True)

    assert reg_path.read_bytes() == original_bytes


def test_set_trials_excluded_raises_on_non_string_value(tmp_path, monkeypatch):
    """A dict-shaped registry whose values aren't strings is corrupt too --
    the wrong-shape check must cover the {str: non-str} case, not just a
    non-dict top level."""
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps({"k1": 123}))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    original_bytes = reg_path.read_bytes()

    with pytest.raises(common.RegistryCorruptError):
        common.set_trials_excluded(["k1"], True)

    assert reg_path.read_bytes() == original_bytes


def test_set_trials_excluded_propagates_oserror_not_registry_corrupt(tmp_path, monkeypatch):
    """An unreadable (locked / permission-denied) registry is NOT corruption:
    the OSError must propagate as-is so the UI reports the generic
    "Failed to toggle exclusion" rather than telling the operator to
    hand-repair a file that may be perfectly valid."""
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps({"k1": "a reason"}))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    real_open = builtins.open

    def denying_open(path, *args, **kwargs):
        if str(path) == str(reg_path):
            raise PermissionError("file is locked by another process")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denying_open)

    with pytest.raises(PermissionError):
        common.set_trials_excluded(["k2"], True)


def test_set_trials_excluded_retries_once_on_replace_failure(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(32, "The process cannot access the file because it is being used by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(common.os, "replace", flaky_replace)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)   # don't actually wait in tests

    common.set_trials_excluded(["k1"], True)

    assert calls["n"] == 2
    assert "k1" in common.load_excluded_trials()
    assert list(tmp_path.iterdir()) == [reg_path]   # temp file cleaned up


def test_set_trials_excluded_raises_clear_message_after_two_failures(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps({"other_key": "pre-existing reason"}))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    original_content = reg_path.read_text()

    def always_fails(src, dst):
        raise OSError(32, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(common.os, "replace", always_fails)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)

    with pytest.raises(OSError) as exc_info:
        common.set_trials_excluded(["k1"], True)

    assert "retried once" in str(exc_info.value)
    assert reg_path.read_text() == original_content
    assert list(tmp_path.iterdir()) == [reg_path]   # temp file cleaned up, original untouched


# -- Full-report layout defects (2026-08-27) -------------------------------
# P24's report had three: the row-5 table's text overflowed its cells (headers
# clipped to "Timepoin"/"Clinician MA", long accounting strings running into
# the next column), the left PT-score panel drew its "Impaired" band label
# outside the axes, and the table left a wide empty band above itself.


def _row5_axes(monkeypatch, clinician_matches=None):
    """A row-5 table drawn on a figure the same width as the real report, so
    the fitted font size is measured against realistic space."""
    import numpy as np
    import matplotlib.pyplot as plt

    t = np.linspace(0, 3, 90)
    ang = np.where(t < 1.0, 180.0, 180.0 - 30.0 * np.sin((t - 1.0) * 2))
    monkeypatch.setattr(common.pt, "load_hpe_model_curves",
                        lambda *a, **k: ([{"name": "mediapipe", "t": t, "ang": ang,
                                           "rmse": 2.0}], []))
    monkeypatch.setattr(common, "clinician_mas_matches",
                        lambda pid, leg, cond: clinician_matches or [])
    rec = {"pid": "24_left_pre", "trial": "1", "pt7": 0.30, "t_raw": t, "angle_raw": ang,
           "neutral_deg_raw": 180.0, "mediapipe_curve": {"t": t, "ang": ang},
           "imu_curve": None}
    # The real report is a 5x2 grid on a 15x21 figure: one cell is 7.5 wide.
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    common._draw_row5_table(ax, "left", {("left", "pre"): [rec]},
                            [("pre", "Pre", "#d62728")], "24")
    return fig, ax


def _overflowing_cells(fig, ax):
    """Cells whose text is wider than the space the cell gives it."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    table = next(child for child in ax.get_children()
                 if child.__class__.__name__ == "Table")
    bad = []
    for (row, col), cell in table.get_celld().items():
        label = cell.get_text()
        if not label.get_text():
            continue
        usable = cell.get_window_extent(renderer).width * (1 - 2 * getattr(cell, "PAD", 0.1))
        if label.get_window_extent(renderer).width > usable:
            bad.append((row, col, label.get_text()))
    return bad


def test_row5_table_text_fits_inside_its_cells(monkeypatch):
    """The defect: a hardcoded 6.5pt overflowed, and matplotlib never shrinks
    cell text to fit. Column widths are now measured, not counted in
    characters, and the font size is solved from what is left."""
    import matplotlib.pyplot as plt

    fig, ax = _row5_axes(monkeypatch)
    try:
        assert _overflowing_cells(fig, ax) == []
    finally:
        plt.close(fig)


def test_row5_table_stays_legible_rather_than_shrinking_without_limit(monkeypatch):
    """Fitting must not be allowed to solve the problem by disappearing."""
    import matplotlib.pyplot as plt

    fig, ax = _row5_axes(monkeypatch)
    try:
        table = next(child for child in ax.get_children()
                     if child.__class__.__name__ == "Table")
        sizes = {cell.get_text().get_fontsize() for cell in table.get_celld().values()}
        assert all(size >= 3.5 for size in sizes), sizes
    finally:
        plt.close(fig)


def test_row5_table_fills_its_axes_instead_of_floating_in_the_middle(monkeypatch):
    """loc='center' left a wide empty band between the caveat and the table."""
    import matplotlib.pyplot as plt

    fig, ax = _row5_axes(monkeypatch)
    try:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        table = next(child for child in ax.get_children()
                     if child.__class__.__name__ == "Table")
        cells = [c.get_window_extent(renderer) for c in table.get_celld().values()]
        table_height = max(c.y1 for c in cells) - min(c.y0 for c in cells)
        axes_height = ax.get_window_extent(renderer).height
        assert table_height / axes_height > 0.6, table_height / axes_height
    finally:
        plt.close(fig)


def test_zone_bands_skip_a_band_above_the_visible_range():
    """P24's left leg tops out at 0.35, below the Impaired floor of 0.44. Only
    `hi` was clamped to y_max, so the midpoint landed past the top of the axes
    and 'Impaired' was drawn floating outside the plot."""
    bands = common.visible_zone_bands(0.35)

    assert [common.ZONE_LABELS[i] for i, _lo, _hi in bands] == ["Healthy", "Borderline"]
    assert all(lo < hi for _i, lo, hi in bands), bands
    assert all(hi <= 0.35 for _i, _lo, hi in bands), bands


def test_zone_bands_keep_every_band_when_all_are_visible():
    bands = common.visible_zone_bands(3.0)

    assert [i for i, _lo, _hi in bands] == [0, 1, 2]
    assert bands[-1][2] == 3.0


def test_zone_band_midpoints_stay_inside_the_axes():
    """The label is drawn at the band midpoint, so that is what must be in
    range -- clamping `hi` alone was not enough to guarantee it."""
    for y_max in (0.05, 0.35, 0.5, 1.6, 3.0):
        for _i, lo, hi in common.visible_zone_bands(y_max):
            assert 0 <= (lo + hi) / 2 <= y_max, (y_max, lo, hi)
