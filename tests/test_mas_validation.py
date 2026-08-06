import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
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
