"""
Tests for generate_figures_by_spasticity.py.

Narrow on purpose: the script is mostly orchestration and plotting, and the one
thing in it that can silently produce a wrong RESULT is whether it honours the
operator's exclusion registry.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import generate_figures_by_spasticity as gfs
import pt_report_common as common


def _fake_trials(tmp_path, pid, leg, trials, folder=None):
    """A Recordings/ tree with the component CSVs derive_component_paths wants.

    `folder` names the participant directory, so a caller can pin either the
    study-prefixed scheme (CHAT_22) or the legacy one (Participant_22)."""
    d = (tmp_path / "Recordings" / (folder or f"Participant_{pid}")
         / leg.capitalize() / "pre")
    d.mkdir(parents=True)
    for t in trials:
        for suffix in ("imu", "accel", "gyro", "mag"):
            (d / f"Trial_{t}_{suffix}.csv").write_text("t,x\n0,0\n", encoding="utf-8")
    return d


def test_imu_params_skip_trials_the_operator_excluded(tmp_path, monkeypatch):
    """excluded_trials.json is documented as THE mechanism that removes a trial,
    and its keys name a trial rather than a modality. This path used to glob the
    IMU files directly and bypass it, so a trial the operator had excluded still
    contributed its parameters.

    P22 left is the live case: excluded 2026-08-31 because the shank was outside
    camera coverage for the entire pre-release hold.
    """
    _fake_trials(tmp_path, "22", "left", [1, 2, 3, 4])
    monkeypatch.setattr(gfs, "BASE_DIR", str(tmp_path))

    seen = []

    def _spy(paths, *a, **k):
        seen.append(paths)
        raise RuntimeError("stop before scoring; we only care which trials got here")

    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {common.trial_key("22", "left", "pre", t): "excluded"
                                 for t in (1, 2, 3, 4)})
    # Every trial is excluded, so nothing should even be opened.
    assert gfs.imu_leg_params("22", "left") == {}


def test_imu_params_still_read_trials_that_are_not_excluded(tmp_path, monkeypatch):
    """The guard must not become a blanket refusal -- only the named trials go."""
    _fake_trials(tmp_path, "22", "left", [1, 2])
    monkeypatch.setattr(gfs, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {common.trial_key("22", "left", "pre", 1): "excluded"})

    opened = []
    import batch_imu_vs_optitrack_rmse as batch

    def _derive(path):
        opened.append(os.path.basename(path))
        raise RuntimeError("stop here")

    monkeypatch.setattr(batch, "derive_component_paths", _derive)
    gfs.imu_leg_params("22", "left")
    assert opened == ["Trial_2_imu.csv"], opened


def test_an_empty_registry_excludes_nothing(tmp_path, monkeypatch):
    _fake_trials(tmp_path, "22", "left", [1, 2])
    monkeypatch.setattr(gfs, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    opened = []
    import batch_imu_vs_optitrack_rmse as batch

    def _derive(path):
        opened.append(os.path.basename(path))
        raise RuntimeError("stop here")

    monkeypatch.setattr(batch, "derive_component_paths", _derive)
    gfs.imu_leg_params("22", "left")
    assert sorted(opened) == ["Trial_1_imu.csv", "Trial_2_imu.csv"], opened


def test_the_real_registry_has_p22_left_excluded():
    """Guards the operator decision itself, so a later edit that drops these
    entries is visible rather than silent. The trials cannot be reconstructed:
    across the hold the cameras made ~3-4 marker detections per frame, labelled
    and unlabelled combined, against the 6 needed for both clusters."""
    excluded = common.load_excluded_trials()
    if not excluded:
        pytest.skip("excluded_trials.json not present in this checkout")
    for t in (1, 2, 3, 4):
        key = common.trial_key("22", "left", "pre", t)
        assert key in excluded, f"{key} is no longer excluded"
        assert "camera coverage" in excluded[key], excluded[key]


def test_imu_params_find_a_study_prefixed_participant_folder(tmp_path, monkeypatch):
    """After the 2026-09-01 rename P22's folder is CHAT_22, not Participant_22.
    The IMU lookup has to resolve the participant by id: if it kept spelling the
    folder name out, every renamed participant would silently contribute zero
    trials and the figures would just get quieter rather than fail."""
    _fake_trials(tmp_path, "22", "left", [1, 2], folder="CHAT_22")
    monkeypatch.setattr(gfs, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    opened = []
    import batch_imu_vs_optitrack_rmse as batch

    def _derive(path):
        opened.append(os.path.basename(path))
        raise RuntimeError("stop here")

    monkeypatch.setattr(batch, "derive_component_paths", _derive)
    gfs.imu_leg_params("22", "left")
    assert opened == ["Trial_1_imu.csv", "Trial_2_imu.csv"], opened


def test_imu_params_still_find_a_legacy_participant_folder(tmp_path, monkeypatch):
    """The archive was never renamed, so Participant_22 must keep resolving."""
    _fake_trials(tmp_path, "22", "left", [1], folder="Participant_22")
    monkeypatch.setattr(gfs, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    opened = []
    import batch_imu_vs_optitrack_rmse as batch

    def _derive(path):
        opened.append(os.path.basename(path))
        raise RuntimeError("stop here")

    monkeypatch.setattr(batch, "derive_component_paths", _derive)
    gfs.imu_leg_params("22", "left")
    assert opened == ["Trial_1_imu.csv"], opened
