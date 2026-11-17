# tests/test_derive_healthy_ref.py
"""The derivation's own logic, on synthetic trials: which trials enter the
reference population, when a cached angle series may be reused, and that a
partial cohort fails closed instead of printing a plausible table."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import derive_healthy_ref as dhr


def _rec(tmp_path, pid, leg, cond, trial):
    p = tmp_path / f"{pid}_{leg}_{cond}_{trial}.csv"
    p.write_text("x")
    return {"participant": pid, "leg": leg, "condition": cond, "trial": trial,
            "path": str(p)}


@pytest.fixture
def world(monkeypatch, tmp_path):
    """Two synthetic controls and one non-control. Every trial loads to a fixed series and
    scores as interpretable unless its path says 'refused'."""
    recs = []
    loads = []

    def discover(include_archive=True, **_):
        assert include_archive is False, "archive copies would double-count trials"
        return list(recs)

    def load(path):
        loads.append(path)
        if "broken" in path:
            raise ValueError("Cluster is never fully tracked")
        return np.arange(10.0), np.zeros(10)

    params = {k: 1.0 for k in dhr.KEYS}
    monkeypatch.setattr(dhr.prc, "discover_all_trials", discover)
    monkeypatch.setattr(dhr, "_is_control", lambda pid: str(pid) in {"6", "7"})
    monkeypatch.setattr(dhr.pts, "load_optitrack", load)
    monkeypatch.setattr(dhr.pts, "compute_pt_params", lambda t, a: dict(params))
    monkeypatch.setattr(dhr.pts, "mas_estimate",
                        lambda p: {"interpretable": True})
    return recs, loads, tmp_path


def test_baseline_filter_accepts_pre_and_any_case_of_default(world):
    recs, _, tmp = world
    recs += [_rec(tmp, "6", "left", "pre", 1),
             _rec(tmp, "6", "left", "Default", 2),
             _rec(tmp, "6", "left", "default", 3),
             _rec(tmp, "6", "left", "post", 4),
             _rec(tmp, "13", "left", "pre", 1)]          # not a control
    rows, unloadable = dhr.control_trials()
    assert len(rows) == 3 and unloadable == []


def test_the_recorded_diagnosis_must_agree_with_the_control_prefix(monkeypatch, tmp_path):
    """A folder under the control prefix is not enough: the participant's
    recorded diagnosis must map to the control group too. A misfiled
    participant, or one with no diagnosis on record, stays out."""
    monkeypatch.setattr(dhr.pp, "find_participant_dir",
                        lambda root, pid: str(tmp_path / f"{dhr.pp.CONTROL_PREFIX}_{pid}"))
    diagnoses = {"6": "Unaffected Control", "7": "control", "20": "MS",
                 "21": "Stroke", "22": "Other Motor Impairment", "23": None}
    monkeypatch.setattr(dhr.pp, "diagnosis_from_metadata",
                        lambda root, pid: diagnoses.get(pid))
    assert dhr._is_control("6") is True
    assert dhr._is_control(f"{dhr.pp.CONTROL_PREFIX}_7") is True
    for pid in ("20", "21", "22", "23"):
        assert dhr._is_control(pid) is False, pid


def test_a_control_diagnosis_does_not_admit_a_folder_outside_the_control_prefix(
        monkeypatch, tmp_path):
    monkeypatch.setattr(dhr.pp, "find_participant_dir",
                        lambda root, pid: str(tmp_path / f"{dhr.pp.MS_PREFIX}_{pid}"))
    monkeypatch.setattr(dhr.pp, "diagnosis_from_metadata",
                        lambda root, pid: "Unaffected Control")
    assert dhr._is_control("6") is False


def test_only_interpretable_trials_enter(world, monkeypatch):
    recs, _, tmp = world
    recs += [_rec(tmp, "6", "left", "pre", 1), _rec(tmp, "6", "left", "pre", 2)]
    monkeypatch.setattr(dhr.pts, "mas_estimate",
                        lambda p, _n=iter([True, False]): {"interpretable": next(_n)})
    rows, _ = dhr.control_trials()
    assert len(rows) == 1


def test_cache_is_reused_only_for_the_same_source_and_scorer(world, monkeypatch, tmp_path):
    recs, loads, tmp = world
    rec = _rec(tmp, "6", "left", "pre", 1)
    recs.append(rec)
    cache = str(tmp_path / "cache")
    os.makedirs(cache)

    dhr.control_trials(cache)
    dhr.control_trials(cache)
    assert len(loads) == 1, "an unchanged trial must come from the cache"

    os.utime(rec["path"], (1, 1))                        # the recording changed
    dhr.control_trials(cache)
    assert len(loads) == 2, "a changed source file must be re-read"

    monkeypatch.setattr(dhr, "_loader_fingerprint", lambda: "different-scorer")
    dhr.control_trials(cache)
    assert len(loads) == 3, "a cache built by different scorer code must be re-read"


def test_a_pre_metadata_cache_entry_is_not_trusted(world, tmp_path):
    recs, loads, tmp = world
    recs.append(_rec(tmp, "6", "left", "pre", 1))
    cache = tmp_path / "cache"
    cache.mkdir()
    np.savez(cache / "6_left_pre_T1.npz", t=np.arange(3.0), ang=np.ones(3))
    dhr.control_trials(str(cache))
    assert len(loads) == 1


def test_unloadable_trials_fail_closed_unless_accepted(world, capsys):
    recs, _, tmp = world
    recs += [_rec(tmp, "6", "left", "pre", 1), _rec(tmp, "7", "right", "pre", 1),
             _rec(tmp, "7", "right", "pre_broken", 2)]
    assert dhr.main([]) == 2
    assert "could not be loaded" in capsys.readouterr().out
    assert dhr.main(["--allow-unloadable"]) == 0
    assert "1 unloadable, accepted" in capsys.readouterr().out
