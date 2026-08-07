import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import matplotlib.pyplot as plt
from sklearn.metrics import cohen_kappa_score

import mas_validation as mv


# ── MAS_RANK / _valid_grade ─────────────────────────────────────────────────

def test_mas_rank_ordering():
    assert mv.MAS_RANK["0"] == 0
    assert mv.MAS_RANK["1"] == 1
    assert mv.MAS_RANK["1+"] == 2
    assert mv.MAS_RANK["2"] == 3
    assert mv.MAS_RANK["3"] == 4
    assert mv.MAS_RANK["4"] == 5
    assert mv.MAS_RANK["1"] < mv.MAS_RANK["1+"] < mv.MAS_RANK["2"]


@pytest.mark.parametrize("grade", ["0", "1", "1+", "2", "3", "4"])
def test_valid_grade_accepts_all_mas_order_values(grade):
    assert mv._valid_grade(grade)


@pytest.mark.parametrize("grade", ["5", "", "1++", "MAS 2", None])
def test_valid_grade_rejects_anything_else(grade):
    assert not mv._valid_grade(grade)


def test_default_mas_fields_includes_new_columns():
    assert mv.DEFAULT_MAS_FIELDS[-2:] == ["stronger_leg", "notes"]


@pytest.mark.parametrize("value", ["", "left", "right", "equal"])
def test_valid_stronger_leg_accepts_all_options(value):
    assert mv._valid_stronger_leg(value)


@pytest.mark.parametrize("value", ["Left", "both", None, "LEFT"])
def test_valid_stronger_leg_rejects_anything_else(value):
    assert not mv._valid_stronger_leg(value)


# ── pair_pt_and_mas ──────────────────────────────────────────────────────────

def test_pair_pt_and_mas_skips_invalid_grade():
    rows = [{"participant": "13", "leg": "right", "condition": "pre", "mas_grade": "5"}]
    result = mv.pair_pt_and_mas(rows, pt_lookup=lambda p, l, c: 0.5)
    assert len(result) == 1
    assert "_skip_reason" in result[0]
    assert "pt_score" not in result[0]


def test_pair_pt_and_mas_skips_when_pt_lookup_returns_none():
    rows = [{"participant": "13", "leg": "right", "condition": "multiple sclerosis", "mas_grade": "2"}]
    result = mv.pair_pt_and_mas(rows, pt_lookup=lambda p, l, c: None)
    assert len(result) == 1
    assert "_skip_reason" in result[0]
    assert "pt_score" not in result[0]


def test_tokenize_condition_ignores_order_and_separators():
    assert mv._tokenize_condition("1 week post") == mv._tokenize_condition("week_1_post")
    assert mv._tokenize_condition("Post") == mv._tokenize_condition("post")
    assert mv._tokenize_condition("post") != mv._tokenize_condition("post_again")


def test_pt_lookup_matches_specific_condition_only(monkeypatch):
    # Per-visit MAS grades (confirmed 2026-08-06) mean a row must be matched
    # against ITS OWN session's PT score, not pooled across every session
    # for the leg -- pooling would pair a pre-treatment PT score against a
    # post-treatment MAS grade.
    fake_by_leg_tp = {
        ("right", "pre"): [{"pt7": 1.0}, {"pt7": 2.0}],
        ("right", "week_1_post"): [{"pt7": 3.0}],
        ("left", "pre"): [{"pt7": 100.0}],
    }
    monkeypatch.setattr(mv.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))
    lookup = mv._pt_lookup_factory()
    assert lookup("13", "right", "pre") == pytest.approx(1.5)                  # mean(1,2), NOT pooled with week_1_post
    assert lookup("13", "right", "1 week post") == pytest.approx(3.0)          # word-order-insensitive match
    assert lookup("13", "right", "post") is None                               # "post" alone != "week_1_post"
    assert lookup("13", "left", "pre") == pytest.approx(100.0)                 # other leg unaffected


def test_pt_lookup_returns_none_when_leg_has_no_recorded_trials(monkeypatch):
    monkeypatch.setattr(mv.common, "collect_participant",
                        lambda pid: ({("right", "pre"): [{"pt7": 1.0}]}, []))
    lookup = mv._pt_lookup_factory()
    assert lookup("13", "left", "pre") is None


def test_available_conditions_lists_real_conditions_for_leg(monkeypatch):
    fake_by_leg_tp = {
        ("right", "pre"): [{"pt7": 1.0}],
        ("right", "week_1_post"): [{"pt7": 2.0}],
        ("left", "pre"): [{"pt7": 3.0}],
    }
    monkeypatch.setattr(mv.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))
    assert mv.available_conditions("13", "right") == ["pre", "week_1_post"]
    assert mv.available_conditions("13", "left") == ["pre"]


def test_pair_pt_and_mas_accepts_valid_row():
    rows = [{"participant": "13", "leg": "right", "condition": "pre", "mas_grade": "2"}]
    result = mv.pair_pt_and_mas(rows, pt_lookup=lambda p, l, c: 0.5)
    assert len(result) == 1
    assert "_skip_reason" not in result[0]
    assert result[0]["pt_score"] == 0.5
    assert result[0]["predicted_mas"] in mv.MAS_ORDER


# ── compute_validation_stats ─────────────────────────────────────────────────

def _perfect_agreement_pairs():
    # One pt_score per grade, chosen inside pt_to_mas()'s own thresholds so
    # predicted_mas == mas_grade for every row -- both a monotonic PT-score/
    # grade relationship (rho == 1.0) and a perfect-agreement confusion
    # matrix (kappa == 1.0).
    pt_by_grade = {"0": 0.05, "1": 0.20, "1+": 0.35, "2": 0.50, "3": 0.70, "4": 0.90}
    return [{"mas_grade": g, "pt_score": v, "predicted_mas": g} for g, v in pt_by_grade.items()]


def test_compute_validation_stats_known_values():
    stats = mv.compute_validation_stats(_perfect_agreement_pairs())
    assert stats["n"] == 6
    assert stats["preliminary"] is False
    assert stats["spearman_rho"] == pytest.approx(1.0)
    assert stats["weighted_kappa"] == pytest.approx(1.0)
    assert stats["per_grade"]["2"]["n"] == 1


def test_compute_validation_stats_labels_full_set():
    # "1+" (rank 2) never appears in this sample -- a *middle* category
    # missing, which is exactly the scenario where sklearn's default label
    # inference (sorted unique values actually present) would silently
    # compress the ordinal scale and distort linear-weight distances unless
    # `labels=` is passed explicitly.
    actual_grades =    ["0", "1", "2", "3", "4"]
    predicted_grades = ["0", "2", "1", "3", "4"]   # rank1 <-> rank3 swapped
    pt_scores = [0.05, 0.15, 0.25, 0.35, 0.45]
    pairs = [{"mas_grade": a, "pt_score": s, "predicted_mas": p}
            for a, p, s in zip(actual_grades, predicted_grades, pt_scores)]

    actual_ranks = [mv.MAS_RANK[g] for g in actual_grades]
    predicted_ranks = [mv.MAS_RANK[g] for g in predicted_grades]
    full_label_kappa = cohen_kappa_score(actual_ranks, predicted_ranks,
                                         labels=list(range(6)), weights="linear")
    compressed_kappa = cohen_kappa_score(actual_ranks, predicted_ranks, weights="linear")
    # Sanity check that this fixture actually exercises the label-set bug --
    # if these two ever became equal the fixture itself would need reworking.
    assert full_label_kappa != pytest.approx(compressed_kappa)

    stats = mv.compute_validation_stats(pairs)
    assert stats["weighted_kappa"] == pytest.approx(full_label_kappa)


def test_small_n_flag():
    pairs = _perfect_agreement_pairs()
    assert mv.compute_validation_stats(pairs[:4])["preliminary"] is True
    assert mv.compute_validation_stats(pairs[:5])["preliminary"] is False


def test_roc_auc_omitted_below_class_minimum():
    # Only 1 "not spastic" (grade "0") observation -- below the 3-per-class
    # minimum, so roc_auc should be omitted rather than computed on a
    # near-meaningless split.
    pairs = [{"mas_grade": "0", "pt_score": 0.05, "predicted_mas": "0"}] + [
        {"mas_grade": "2", "pt_score": 0.5 + i * 0.01, "predicted_mas": "2"} for i in range(4)
    ]
    stats = mv.compute_validation_stats(pairs)
    assert stats["roc_auc"] is None


def test_roc_auc_computed_when_class_balance_sufficient():
    pairs = [{"mas_grade": "0", "pt_score": 0.02 + i * 0.01, "predicted_mas": "0"} for i in range(3)] + [
        {"mas_grade": "2", "pt_score": 0.5 + i * 0.01, "predicted_mas": "2"} for i in range(3)
    ]
    stats = mv.compute_validation_stats(pairs)
    assert stats["roc_auc"] is not None
    assert 0.0 <= stats["roc_auc"] <= 1.0


# ── build_validation_figure / save_validation_figure ────────────────────────

def test_build_validation_figure_returns_figure_with_two_panels_when_roc_omitted():
    pairs = _perfect_agreement_pairs()
    stats = mv.compute_validation_stats(pairs)
    assert stats["roc_auc"] is None
    fig = mv.build_validation_figure(pairs, stats)
    try:
        assert len(fig.axes) == 2
    finally:
        plt.close(fig)


def test_build_validation_figure_returns_figure_with_three_panels_when_roc_present():
    pairs = [{"mas_grade": "0", "pt_score": 0.02 + i * 0.01, "predicted_mas": "0"} for i in range(3)] + [
        {"mas_grade": "2", "pt_score": 0.5 + i * 0.01, "predicted_mas": "2"} for i in range(3)
    ]
    stats = mv.compute_validation_stats(pairs)
    assert stats["roc_auc"] is not None
    fig = mv.build_validation_figure(pairs, stats)
    try:
        assert len(fig.axes) == 3
    finally:
        plt.close(fig)


def test_build_validation_figure_does_not_create_a_pyplot_managed_figure():
    # Regression guard: plt.subplots() under TkAgg builds a FigureManagerTk
    # with its own tk.Tk() root -- a second Tcl interpreter inside the running
    # app on every refresh(). The Figure API must not register with pyplot.
    before = set(plt.get_fignums())
    pairs = _perfect_agreement_pairs()
    stats = mv.compute_validation_stats(pairs)
    fig = mv.build_validation_figure(pairs, stats)
    try:
        assert set(plt.get_fignums()) == before
        assert fig.canvas.manager is None
    finally:
        plt.close(fig)


def test_save_validation_figure_writes_png(tmp_path):
    pairs = _perfect_agreement_pairs()
    stats = mv.compute_validation_stats(pairs)
    out_path = tmp_path / "fig.png"
    mv.save_validation_figure(pairs, stats, str(out_path))
    assert out_path.exists()


# ── main(): missing/empty mas_scores.csv ────────────────────────────────────

def test_main_missing_csv_no_crash(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mv, "MAS_CSV", str(tmp_path / "does_not_exist.csv"))
    mv.main()   # must not raise
    out = capsys.readouterr().out
    assert "not found" in out
    assert "participant,leg,condition,mas_grade" in out


def test_main_empty_csv_no_crash(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text("participant,leg,condition,mas_grade,assessed_by,assessed_date,notes\n")
    monkeypatch.setattr(mv, "MAS_CSV", str(csv_path))
    mv.main()   # must not raise
    out = capsys.readouterr().out
    assert "0 MAS-scored trials found" in out


# ── append_mas_score ─────────────────────────────────────────────────────────

def test_append_mas_score_writes_using_existing_header_order(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "2026-08-07"},
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[1] == "20,left,pre,multiple sclerosis,1+,VL,2026-08-07"


def test_append_mas_score_rejects_invalid_grade(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    with pytest.raises(ValueError, match="invalid mas_grade"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "pre",
             "diagnosis": "", "mas_grade": "5", "assessed_by": "", "assessed_date": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text().splitlines() == [
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date"]


def test_append_mas_score_round_trips_through_load_mas_scores(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "2026-08-07"},
        csv_path=str(csv_path))
    rows = mv.load_mas_scores(str(csv_path))
    assert len(rows) == 1
    assert rows[0]["participant"] == "20"
    assert rows[0]["mas_grade"] == "1+"


def test_append_mas_score_creates_file_with_header_if_missing(tmp_path):
    # mas_scores.csv is gitignored -- on a fresh checkout the very first Save
    # from the app hits a path that doesn't exist yet.
    csv_path = tmp_path / "new.csv"
    assert not csv_path.exists()
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "2026-08-07"},
        csv_path=str(csv_path))
    assert csv_path.exists()
    lines = csv_path.read_text().splitlines()
    assert lines[0] == "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date,stronger_leg,notes"
    assert lines[1] == "20,left,pre,multiple sclerosis,1+,VL,2026-08-07,,"

    rows = mv.load_mas_scores(str(csv_path))
    assert len(rows) == 1
    assert rows[0]["participant"] == "20"
    assert rows[0]["leg"] == "left"
    assert rows[0]["condition"] == "pre"
    assert rows[0]["diagnosis"] == "multiple sclerosis"
    assert rows[0]["mas_grade"] == "1+"
    assert rows[0]["assessed_by"] == "VL"
    assert rows[0]["assessed_date"] == "2026-08-07"


def test_append_mas_score_does_not_create_file_on_invalid_grade(tmp_path):
    csv_path = tmp_path / "new.csv"
    with pytest.raises(ValueError, match="invalid mas_grade"):
        mv.append_mas_score({"participant": "20", "mas_grade": "5"},
                            csv_path=str(csv_path))
    assert not csv_path.exists()


def test_append_mas_score_rejects_invalid_stronger_leg(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    header = ("participant,leg,condition,diagnosis,mas_grade,assessed_by,"
              "assessed_date,stronger_leg,notes\n")
    csv_path.write_text(header)
    with pytest.raises(ValueError, match="invalid stronger_leg"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "both", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == header


def test_append_mas_score_widens_header_when_row_has_new_fields(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,right,pre,multiple sclerosis,1,VL,2026-08-01\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1",
         "assessed_by": "VL", "assessed_date": "2026-08-07",
         "stronger_leg": "right", "notes": "some notes"},
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[0] == ("participant,leg,condition,diagnosis,mas_grade,assessed_by,"
                        "assessed_date,stronger_leg,notes")
    assert lines[1] == "13,right,pre,multiple sclerosis,1,VL,2026-08-01,,"
    assert lines[2] == "20,left,pre,multiple sclerosis,1,VL,2026-08-07,right,some notes"
    rows = mv.load_mas_scores(str(csv_path))
    assert len(rows) == 2
    assert rows[1]["stronger_leg"] == "right"
    assert rows[1]["notes"] == "some notes"


def test_append_mas_score_widening_is_atomic_on_replace_failure(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    original = ("participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
               "13,right,pre,multiple sclerosis,1,VL,2026-08-01\n")
    csv_path.write_text(original)

    def raise_replace(src, dst):
        raise OSError("simulated failure")
    monkeypatch.setattr(mv.os, "replace", raise_replace)

    with pytest.raises(OSError):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "right", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == original


def test_append_mas_score_widening_is_atomic_on_write_failure(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    original = ("participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
               "13,right,pre,multiple sclerosis,1,VL,2026-08-01\n")
    csv_path.write_text(original)

    real_open = open
    def failing_open(path, *a, **kw):
        if str(path).endswith(".tmp"):
            raise OSError("simulated disk full")
        return real_open(path, *a, **kw)
    monkeypatch.setattr(mv, "open", failing_open, raising=False)

    replace_calls = []
    monkeypatch.setattr(mv.os, "replace", lambda *a: replace_calls.append(a))

    with pytest.raises(OSError):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "right", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == original
    assert replace_calls == []


def test_append_mas_score_no_widen_when_row_keys_are_subset_of_header(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,"
        "assessed_date,stronger_leg,notes\n")
    replace_calls = []
    monkeypatch.setattr(mv.os, "replace", lambda *a: replace_calls.append(a))
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
         "mas_grade": "1", "assessed_by": "", "assessed_date": "",
         "stronger_leg": "", "notes": ""},
        csv_path=str(csv_path))
    assert replace_calls == []
    assert not os.path.exists(str(csv_path) + ".tmp")


def test_append_mas_score_ignores_unrecognized_keys_without_widening(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
         "mas_grade": "1", "assessed_by": "", "assessed_date": "",
         "stronger_le": "right"},  # typo'd key -- not in WIDENABLE_MAS_FIELDS
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[0] == "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date"
    assert len(lines) == 2
    assert "stronger_le" not in lines[0]
    assert "right" not in lines[1]


def test_append_mas_score_raises_on_malformed_existing_row(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    original = (
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,right,pre,multiple sclerosis,1,VL,2026-08-01,extra,cells,here\n")
    csv_path.write_text(original)
    with pytest.raises(ValueError, match="row 2"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "right", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == original


def test_append_mas_score_widens_empty_file(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text("")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1",
         "assessed_by": "VL", "assessed_date": "2026-08-07",
         "stronger_leg": "right", "notes": "some notes"},
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[0] == ",".join(mv.DEFAULT_MAS_FIELDS)
    assert len(lines) == 2
    rows = mv.load_mas_scores(str(csv_path))
    assert len(rows) == 1
    assert rows[0]["stronger_leg"] == "right"
