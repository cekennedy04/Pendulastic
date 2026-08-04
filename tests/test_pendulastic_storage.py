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
