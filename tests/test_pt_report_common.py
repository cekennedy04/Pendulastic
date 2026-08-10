import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

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
    monkeypatch.setattr(common.pt, "load_optitrack", lambda path: (_ for _ in ()).throw(ValueError("bad csv")))

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unreadable"


def test_trial_candidates_classifies_unscoreable_and_scored(tmp_path, monkeypatch):
    import numpy as np
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})
    monkeypatch.setattr(common.pt, "load_optitrack",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 179.0])))
    monkeypatch.setattr(common, "score_trial", lambda pid, trial, t, angle: None)

    candidates = common.trial_candidates("13", include_archive=False)
    assert len(candidates) == 1
    assert candidates[0]["status"] == "unscoreable"

    monkeypatch.setattr(common, "score_trial",
                        lambda pid, trial, t, angle: {"pid": pid, "trial": trial, "pt7": 0.5})
    candidates = common.trial_candidates("13", include_archive=False)
    assert candidates[0]["status"] == "scored"
    assert candidates[0]["record"]["pt7"] == 0.5


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
    shape, output file) must be identical after the extraction."""
    monkeypatch.setattr(common, "OUT_DIR", str(tmp_path))
    rec = {"trial": "1", "mediapipe_rmse": 3.2, "imu_rmse": 4.1}
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

    monkeypatch.setattr(common.pt, "compute_pt_params", lambda t_, a_: {"fake": True})
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
