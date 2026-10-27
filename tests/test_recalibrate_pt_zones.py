# tests/test_recalibrate_pt_zones.py
import os, sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import recalibrate_pt_zones as rz


def _legs(healthy, impaired):
    legs = {(f"h{i}", "left"): ("0", pt) for i, pt in enumerate(healthy)}
    legs.update({(f"i{i}", "left"): ("1", pt) for i, pt in enumerate(impaired)})
    return legs


def test_derive_emits_ordered_thresholds_when_groups_separate():
    h, b = rz.derive(_legs([.05, .08, .10, .12, .15], [.40, .45, .50, .55, .60]),
                     verbose=False)
    assert 0 < h < b


def test_derive_refuses_when_impaired_median_does_not_exceed_healthy_75th():
    """2026-10-26 real cohort: MAS-0 75th pct 0.2979 vs MAS>=1 median 0.1892
    gave HEALTHY 0.2979 > BORDERLINE 0.2435, and main() still printed 'To
    apply'. An inverted pair would grade every borderline leg healthy."""
    legs = _legs([.06, .09, .14, .30, .44], [.14, .15, .19, .42, .56])
    with pytest.raises(SystemExit) as e:
        rz.derive(legs, verbose=False)
    assert "do not separate" in str(e.value)


def test_derive_refuses_on_a_tie():
    legs = _legs([.1, .1, .2, .2, .2], [.2, .2, .2, .3, .3])
    with pytest.raises(SystemExit):
        rz.derive(legs, verbose=False)


def test_main_does_not_print_apply_instructions_when_inverted(monkeypatch, capsys):
    monkeypatch.setattr(rz, "leg_scores", lambda *a, **k: _legs(
        [.06, .09, .14, .30, .44], [.14, .15, .19, .42, .56]))
    with pytest.raises(SystemExit):
        rz.main(["--opti-root", "x"])
    assert "To apply" not in capsys.readouterr().out
