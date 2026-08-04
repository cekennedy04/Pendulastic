import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import pendulastic_storage as storage


@pytest.fixture(autouse=True)
def _isolated_participants_dir(tmp_path, monkeypatch):
    """Every test gets its own empty participants/ directory so tests never
    read/write real data or interfere with each other."""
    monkeypatch.setattr(storage, "PARTICIPANTS_DIR", str(tmp_path / "participants"))
    yield


def test_normalize_participant_id_strips_and_uppercases():
    assert storage.normalize_participant_id(" p5 ") == "P5"
    assert storage.normalize_participant_id("p5") == "P5"
    assert storage.normalize_participant_id("P5") == "P5"


def test_load_history_missing_file_returns_empty_skeleton():
    history = storage.load_history("P5")
    assert history["participant_id"] == "P5"
    assert history["legs"]["left"]["sessions"] == []
    assert history["legs"]["right"]["sessions"] == []
    assert "_skipped" not in history


def test_load_history_corrupt_json_returns_empty_skeleton():
    path = os.path.join(storage.PARTICIPANTS_DIR, "P5")
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "history.json"), "w", encoding="utf-8") as f:
        f.write("{not valid json")

    history = storage.load_history("P5")
    assert history["legs"]["left"]["sessions"] == []


def test_load_history_skips_malformed_session_and_reports_it():
    path = os.path.join(storage.PARTICIPANTS_DIR, "P5")
    os.makedirs(path, exist_ok=True)
    good_session = {
        "label": "Initial", "date": "2026-07-07", "reference_trace": "imu",
        "traces": {"imu": {"t": [0.0], "angle": [140.0],
                           "metrics": {"pt_score": 0.1, "mas": "0"}}},
    }
    bad_session = {"label": "Broken", "date": "not-a-date"}
    raw = {
        "participant_id": "P5",
        "legs": {"left": {"sessions": [good_session, bad_session]},
                 "right": {"sessions": []}},
    }
    with open(os.path.join(path, "history.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f)

    history = storage.load_history("P5")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["label"] == "Initial"
    assert len(history["_skipped"]) == 1
    assert "Broken" not in history["_skipped"][0] or "date" in history["_skipped"][0]


def _traces_and_metrics():
    traces = {"imu": ([0.0, 0.1, 0.2], [140.0, 138.0, 135.0])}
    metrics = {"imu": {"R2n": 0.95, "N": 6.0, "phi_max_ratio": 0.79,
                       "omega_max_n": 7.17, "omega_min_n": 0.01, "f": 1.0,
                       "area_ratio": 0.13, "pt_score": 0.115, "mas": "0"}}
    return traces, metrics


def test_save_trial_round_trip():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("p5", "left", "Initial", "2026-07-07", traces, metrics, "imu")

    history = storage.load_history("P5")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1
    session = sessions[0]
    assert session["label"] == "Initial"
    assert session["date"] == "2026-07-07"
    assert session["reference_trace"] == "imu"
    assert session["traces"]["imu"]["angle"] == [140.0, 138.0, 135.0]
    assert session["traces"]["imu"]["metrics"]["pt_score"] == 0.115


def test_save_trial_rejects_bad_date():
    traces, metrics = _traces_and_metrics()
    with pytest.raises(ValueError):
        storage.save_trial("P5", "left", "Initial", "07/07/2026", traces, metrics, "imu")


def test_save_trial_rejects_bad_leg():
    traces, metrics = _traces_and_metrics()
    with pytest.raises(ValueError):
        storage.save_trial("P5", "middle", "Initial", "2026-07-07", traces, metrics, "imu")


def test_save_trial_does_not_clobber_other_leg_or_date():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("P5", "left", "Initial", "2026-07-07", traces, metrics, "imu")
    storage.save_trial("P5", "right", "Initial", "2026-07-07", traces, metrics, "imu")
    storage.save_trial("P5", "left", "Post-Training", "2026-07-17", traces, metrics, "imu")

    history = storage.load_history("P5")
    assert len(history["legs"]["left"]["sessions"]) == 2
    assert len(history["legs"]["right"]["sessions"]) == 1
    left_labels = {s["label"] for s in history["legs"]["left"]["sessions"]}
    assert left_labels == {"Initial", "Post-Training"}


def test_save_trial_upserts_matching_label_and_date():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("P5", "left", "Initial", "2026-07-07", traces, metrics, "imu")

    traces2, metrics2 = _traces_and_metrics()
    metrics2["imu"]["pt_score"] = 0.5   # reprocessed with a different result
    storage.save_trial("P5", "left", "Initial", "2026-07-07", traces2, metrics2, "imu")

    history = storage.load_history("P5")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1   # replaced, not duplicated
    assert sessions[0]["traces"]["imu"]["metrics"]["pt_score"] == 0.5


def test_list_participant_ids_normalizes_and_sorts():
    traces, metrics = _traces_and_metrics()
    storage.save_trial("p9", "left", "Initial", "2026-07-07", traces, metrics, "imu")
    storage.save_trial(" P2 ", "left", "Initial", "2026-07-07", traces, metrics, "imu")

    assert storage.list_participant_ids() == ["P2", "P9"]


def test_list_participant_ids_empty_dir_returns_empty_list():
    assert storage.list_participant_ids() == []
