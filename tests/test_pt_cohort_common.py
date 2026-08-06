import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json

import pt_cohort_common as pcc


# ── classify_participant ─────────────────────────────────────────────────

def test_classify_metadata_ms():
    assert pcc.classify_participant("13", "MS", {}, True) == ("MS", "metadata")


def test_classify_metadata_unaffected_control():
    assert pcc.classify_participant("6", "Unaffected Control", {}, True) == ("Control", "metadata")


def test_classify_metadata_stroke_and_other_are_excluded():
    assert pcc.classify_participant("9", "Stroke", {}, True) == ("Excluded", "metadata")
    assert pcc.classify_participant("10", "Other Motor Impairment", {}, True) == ("Excluded", "metadata")


def test_classify_metadata_priority_over_registry():
    registry = {"13": "Control"}   # deliberately conflicting with metadata
    assert pcc.classify_participant("13", "MS", registry, True) == ("MS", "metadata")


def test_classify_registry_fallback_when_no_metadata():
    registry = {"6": "Control"}
    assert pcc.classify_participant("6", None, registry, True) == ("Control", "registry")


def test_classify_unrecognized_metadata_falls_through_to_registry():
    registry = {"6": "Control"}
    assert pcc.classify_participant("6", "Not A Real Diagnosis", registry, True) == ("Control", "registry")


def test_classify_unclassified_no_entry():
    assert pcc.classify_participant("6", None, {}, True) == ("Unclassified", "no_entry")


def test_classify_unclassified_registry_missing():
    assert pcc.classify_participant("6", None, {}, False) == ("Unclassified", "registry_missing")


def test_classify_registry_missing_wins_even_with_unrecognized_metadata():
    assert pcc.classify_participant("6", "typo diagnosis", {}, False) == ("Unclassified", "registry_missing")


def test_classify_case_insensitive_matching():
    assert pcc.classify_participant("13", "ms", {}, True) == ("MS", "metadata")
    assert pcc.classify_participant("6", None, {"6": "control"}, True) == ("Control", "registry")


# ── load_registry ────────────────────────────────────────────────────────

def test_load_registry_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(tmp_path / "does_not_exist.json"))
    assert pcc.load_registry() == ({}, False)


def test_load_registry_reads_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "participant_groups.json"
    path.write_text(json.dumps({"6": "Control"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(path))
    assert pcc.load_registry() == ({"6": "Control"}, True)


def test_load_registry_malformed_json_treated_as_missing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "participant_groups.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(path))
    assert pcc.load_registry() == ({}, False)
    assert "failed to parse" in capsys.readouterr().out


# ── load_metadata_diagnosis ─────────────────────────────────────────────

def test_load_metadata_diagnosis_reads_diagnosis_field(tmp_path, monkeypatch):
    rec_root = tmp_path / "Recordings"
    (rec_root / "Participant_13").mkdir(parents=True)
    (rec_root / "Participant_13" / "metadata.json").write_text(
        json.dumps({"participant_id": "13", "diagnosis": "MS"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    assert pcc.load_metadata_diagnosis("13") == "MS"


def test_load_metadata_diagnosis_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pcc, "REC_ROOT", str(tmp_path / "Recordings"))
    assert pcc.load_metadata_diagnosis("13") is None


def test_load_metadata_diagnosis_prefix_collision_is_rejected(tmp_path, monkeypatch):
    # "Participant_13*" glob-matches "Participant_130" too -- must not
    # mistake participant 130's metadata for participant 13's.
    rec_root = tmp_path / "Recordings"
    (rec_root / "Participant_130").mkdir(parents=True)
    (rec_root / "Participant_130" / "metadata.json").write_text(
        json.dumps({"participant_id": "130", "diagnosis": "MS"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    assert pcc.load_metadata_diagnosis("13") is None


def test_load_metadata_diagnosis_checks_multiple_matching_folders(tmp_path, monkeypatch):
    # Real convention: Participant_13 AND Participant_13_right_post can
    # both exist; metadata.json only needs to be found in one of them.
    rec_root = tmp_path / "Recordings"
    (rec_root / "Participant_13_right_post").mkdir(parents=True)
    (rec_root / "Participant_13_right_post" / "metadata.json").write_text(
        json.dumps({"participant_id": "13", "diagnosis": "MS"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    assert pcc.load_metadata_diagnosis("13") == "MS"
