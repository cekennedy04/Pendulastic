import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import fit_mas_thresholds as fmt


# ── check_sample_sufficiency ────────────────────────────────────────────────

def test_check_sample_sufficiency_flags_sparse_data():
    pairs = [{"mas_grade": "0", "pt_score": 0.1} for _ in range(3)]
    ok, report = fmt.check_sample_sufficiency(pairs)
    assert ok is False
    assert any("n = 3" in line for line in report)


def test_check_sample_sufficiency_passes_with_enough_spread():
    # 4 grades represented, 4 observations each -> n=16 >= MIN_TOTAL_N (15)
    pairs = []
    for grade in ["0", "1", "1+", "2"]:
        pairs += [{"mas_grade": grade, "pt_score": 0.1} for _ in range(4)]
    ok, report = fmt.check_sample_sufficiency(pairs)
    assert ok is True


def test_check_sample_sufficiency_reports_missing_grades():
    pairs = [{"mas_grade": "0", "pt_score": 0.1} for _ in range(20)]
    ok, report = fmt.check_sample_sufficiency(pairs)
    assert ok is False
    missing_line = next(line for line in report if "zero observations" in line)
    for grade in ["1", "1+", "2", "3", "4"]:
        assert grade in missing_line


# ── _predict_rank boundary semantics ────────────────────────────────────────

def test_predict_rank_matches_pt_to_mas_boundary_convention():
    thresholds = (0.12, 0.28, 0.44, 0.60, 0.78)
    # Exactly-at-threshold goes to the LOWER grade, matching pt_to_mas()'s `<=`.
    assert fmt._predict_rank(0.12, thresholds) == 0
    assert fmt._predict_rank(0.120001, thresholds) == 1
    assert fmt._predict_rank(0.78, thresholds) == 4
    assert fmt._predict_rank(100.0, thresholds) == 5   # past the last threshold -> top grade


# ── fit_thresholds ───────────────────────────────────────────────────────────

def test_fit_thresholds_recovers_clean_separation():
    # Six cleanly-separated clusters, one per grade, zero overlap -- a
    # perfect fit (kappa == 1.0) must be achievable and found.
    pairs = []
    for grade, center in zip(fmt.pt.MAS_ORDER, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]):
        pairs += [{"mas_grade": grade, "pt_score": center} for _ in range(3)]
    thresholds, kappa = fmt.fit_thresholds(pairs, grid_points=12)
    assert thresholds is not None
    assert kappa == pytest.approx(1.0)


def test_fit_thresholds_returns_none_when_too_few_candidates():
    # All pt_scores identical -> only 1 candidate cut point, fewer than the
    # 5 needed -- must degrade gracefully, not crash.
    pairs = [{"mas_grade": "0", "pt_score": 0.5} for _ in range(5)]
    thresholds, kappa = fmt.fit_thresholds(pairs)
    assert thresholds is None
    assert kappa != kappa   # nan


# ── loocv_kappa ──────────────────────────────────────────────────────────────

def test_loocv_kappa_bounded_and_no_crash():
    pairs = []
    for grade, center in zip(fmt.pt.MAS_ORDER, [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]):
        pairs += [{"mas_grade": grade, "pt_score": center + i * 0.01} for i in range(3)]
    kappa = fmt.loocv_kappa(pairs, grid_points=12)
    assert kappa == kappa   # not nan
    assert -1.0 <= kappa <= 1.0


# ── main(): guardrail refusal ────────────────────────────────────────────────

def test_main_refuses_to_fit_when_data_too_sparse(tmp_path, monkeypatch, capsys):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,mas_grade,assessed_by\n"
        "13,right,MS,0,Dr. Smith\n"
        "13,left,MS,1,Dr. Smith\n"
    )
    monkeypatch.setattr(fmt.mv, "MAS_CSV", str(csv_path))
    monkeypatch.setattr(fmt.mv, "_pt_lookup_factory", lambda: (lambda p, l, c: 0.5))
    fmt.main()   # must not raise
    out = capsys.readouterr().out
    assert "Not enough data to fit thresholds responsibly yet" in out
