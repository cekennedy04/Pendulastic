"""The --compare-floor path of batch_imu_vs_optitrack_rmse, without a corpus.

WHY THIS EXISTS. That path gates the 2026-10-27 AHRS change against the
OptiTrack recordings, and it can only be run where those recordings are. A
crash in the reporting, or a leaked global, would surface only AFTER evaluating
the whole corpus twice -- long after the run was worth waiting for. So the
plumbing is exercised here with fabricated rows and a stubbed `evaluate_trial`,
and only the measurement itself is left to the machine that has the data.

What is covered: that both passes actually run at different floors, that the
module global is restored afterwards and after an exception, that trials are
paired by identity rather than by position in the list, and that each verdict
branch reads out correctly -- including the one that says the fix should not
ship, which is the branch most likely to be wanted and least likely to be
exercised by accident.
"""
import pytest

import batch_imu_vs_optitrack_rmse as rmse
import pendulastic_imu_server as srv


def _row(pid, pos="L", trial="1", rmse_deg=3.0, status="ok"):
    return {"participant": pid, "position": pos, "trial": trial,
            "rmse_deg": rmse_deg, "status": status, "n_samples": 100,
            "optitrack_method": "stub", "error": ""}


def _matched(n):
    return [{"imu": "i", "accel": "a", "gyro": "g", "mag": "m",
             "optitrack_path": "o", "participant": f"P{i}",
             "position": "L", "trial": "1"} for i in range(n)]


def test_both_passes_run_at_different_floors_and_the_global_is_restored(monkeypatch):
    """The comparison is only meaningful if the floor actually differs.

    A floor of 0.0 reproduces the pre-fix filter exactly, since
    max(|step|, 0.0) is |step|. If the passes ever ran at the same value the
    report would show a flat zero delta and read as "no accuracy cost", which
    is the most dangerous possible false reassurance here.
    """
    seen = []

    def fake_eval(imu, accel, gyro, mag, opti, ids=None):
        seen.append(srv.ACCEL_RESIDUAL_FLOOR)
        return _row(ids["participant"],
                    rmse_deg=3.0 + (0.1 if srv.ACCEL_RESIDUAL_FLOOR else 0.0))

    monkeypatch.setattr(rmse, "evaluate_trial", fake_eval)
    before = srv.ACCEL_RESIDUAL_FLOOR
    passes = rmse._compare_floors(_matched(3), 2e-3)

    assert sorted(set(seen)) == [0.0, 2e-3], "the two passes must differ"
    assert srv.ACCEL_RESIDUAL_FLOOR == before, "the global leaked"
    assert sorted(passes) == ["after", "before"]
    assert len(passes["before"]) == len(passes["after"]) == 3


def test_the_global_is_restored_even_when_a_pass_raises(monkeypatch):
    """Otherwise one failed comparison silently reconfigures the filter for
    everything that runs afterwards in the same process."""
    def boom(*a, **k):
        raise RuntimeError("evaluate blew up")

    monkeypatch.setattr(rmse, "evaluate_trial", boom)
    before = srv.ACCEL_RESIDUAL_FLOOR
    with pytest.raises(RuntimeError):
        rmse._compare_floors(_matched(2), 2e-3)
    assert srv.ACCEL_RESIDUAL_FLOOR == before


def test_a_regression_says_the_fix_should_not_ship(capsys):
    """The branch that matters most, and the one an accidental run never hits.

    If the floor costs accuracy against OptiTrack, the reproducibility it buys
    is not worth having, and the report has to say so rather than leave the
    number to be read as a pass.
    """
    rmse._report_comparison(
        [_row("P1", rmse_deg=3.0), _row("P2", rmse_deg=4.0)],
        [_row("P1", rmse_deg=3.9), _row("P2", rmse_deg=4.8)])
    out = capsys.readouterr().out
    assert "WORSE" in out
    assert "should not ship" in out
    assert "worsened on 2" in out


def test_an_improvement_and_a_negligible_change_read_differently(capsys):
    rmse._report_comparison(
        [_row("P1", rmse_deg=3.0)], [_row("P1", rmse_deg=2.5)])
    assert "IMPROVES" in capsys.readouterr().out

    # Under 1e-3 deg the reproducibility came free, which is a different
    # conclusion from "it improved accuracy" and must not be reported as one.
    rmse._report_comparison(
        [_row("P1", rmse_deg=3.0000)], [_row("P1", rmse_deg=3.0002)])
    out = capsys.readouterr().out
    assert "no measurable accuracy cost" in out
    assert "IMPROVES" not in out


def test_trials_are_paired_by_identity_not_by_list_position(capsys):
    """The whole point of one invocation over two commits is pairing.

    If the passes were zipped positionally, a trial that errored in one pass
    would shift every later trial against a different trial's baseline, and a
    4e-2 deg effect would be buried under trial-to-trial variation.
    """
    rmse._report_comparison(
        [_row("P9", rmse_deg=9.0), _row("P1", rmse_deg=3.0)],
        [_row("P1", rmse_deg=2.0)])
    out = capsys.readouterr().out
    assert "over 1 trial(s)" in out, "the unpaired trial must be dropped"
    assert "before 3.0000" in out, "P1 must be compared against P1"


def test_no_trial_usable_under_both_settings_refuses_a_verdict(capsys):
    rmse._report_comparison(
        [_row("P1", rmse_deg=3.0), _row("P2", rmse_deg=0.0, status="error")],
        [_row("P1", rmse_deg=0.0, status="error"), _row("P2", rmse_deg=4.0)])
    out = capsys.readouterr().out
    assert "says nothing" in out
    assert "IMPROVES" not in out and "WORSE" not in out
