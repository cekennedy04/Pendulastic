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


# ── aggregate_participant_summary ───────────────────────────────────────

def _trial(**overrides):
    base = {k: 1.0 for k in pcc._SCORE_KEYS}
    base.update(overrides)
    return base


def test_aggregate_participant_summary_empty_returns_none():
    assert pcc.aggregate_participant_summary([]) is None


def test_aggregate_participant_summary_odd_count_median():
    trials = [_trial(pt7=1.0), _trial(pt7=2.0), _trial(pt7=3.0)]
    assert pcc.aggregate_participant_summary(trials)["pt7"] == 2.0


def test_aggregate_participant_summary_even_count_median_interpolates():
    trials = [_trial(pt7=1.0), _trial(pt7=2.0), _trial(pt7=3.0), _trial(pt7=4.0)]
    assert pcc.aggregate_participant_summary(trials)["pt7"] == 2.5


def test_aggregate_participant_summary_rounds_to_four_decimals():
    trials = [_trial(pt7=1.0 / 3)] * 3
    assert pcc.aggregate_participant_summary(trials)["pt7"] == round(1.0 / 3, 4)


def test_aggregate_participant_summary_covers_all_score_keys():
    trials = [_trial(R2n=0.5, N=8.0), _trial(R2n=0.7, N=9.0)]
    summary = pcc.aggregate_participant_summary(trials)
    assert set(summary.keys()) == set(pcc._SCORE_KEYS)
    assert summary["R2n"] == 0.6
    assert summary["N"] == 8.5


# ── cliffs_delta / mann_whitney / effect_label ──────────────────────────

import math
import pytest


def test_cliffs_delta_all_b_greater():
    assert pcc.cliffs_delta([1, 2, 3], [4, 5, 6]) == 1.0


def test_cliffs_delta_all_a_greater():
    assert pcc.cliffs_delta([4, 5, 6], [1, 2, 3]) == -1.0


def test_cliffs_delta_identical_distributions_is_zero():
    assert pcc.cliffs_delta([1, 2, 3], [1, 2, 3]) == 0.0


def test_cliffs_delta_empty_input_is_nan():
    assert math.isnan(pcc.cliffs_delta([], [1, 2]))


def test_mann_whitney_below_min_n_returns_nan():
    stat, p = pcc.mann_whitney([1.0], [2.0, 3.0])
    assert math.isnan(stat) and math.isnan(p)


def test_mann_whitney_computes_p_value():
    stat, p = pcc.mann_whitney([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
    assert 0.0 <= p <= 1.0


@pytest.mark.parametrize("d,label", [(0.05, "negligible"), (0.2, "small"),
                                     (0.4, "medium"), (0.9, "large")])
def test_effect_label_thresholds(d, label):
    assert pcc.effect_label(d) == label


def test_effect_label_nan_is_na():
    assert pcc.effect_label(float("nan")) == "n/a"


# ── compute_cohort_stats ─────────────────────────────────────────────────

def _summary(pt7):
    d = {k: 1.0 for k in pcc._SCORE_KEYS}
    d["pt7"] = pt7
    return d


def test_compute_cohort_stats_known_values():
    ms = {"left": [_summary(1.0), _summary(2.0), _summary(3.0)], "right": []}
    control = {"left": [_summary(10.0), _summary(11.0), _summary(12.0)], "right": []}
    rows = pcc.compute_cohort_stats(ms, control)
    row = next(r for r in rows if r["leg"] == "left" and r["parameter"] == "pt7")
    assert row["n_ms"] == 3 and row["n_control"] == 3
    assert row["ms_median"] == 2.0
    assert row["control_median"] == 11.0
    assert row["cliffs_delta"] == 1.0
    assert row["effect_size"] == "large"
    assert row["mann_whitney_p"] is not None


def test_compute_cohort_stats_small_n_is_na():
    ms = {"left": [_summary(1.0)], "right": []}
    control = {"left": [_summary(10.0), _summary(11.0)], "right": []}
    rows = pcc.compute_cohort_stats(ms, control)
    row = next(r for r in rows if r["leg"] == "left" and r["parameter"] == "pt7")
    assert row["n_ms"] == 1
    assert row["mann_whitney_p"] is None
    assert row["cliffs_delta"] is None
    assert row["effect_size"] == "n/a"
    assert row["ms_median"] == 1.0   # still reported -- just no significance test


def test_compute_cohort_stats_covers_every_leg_and_score_key():
    ms = {"left": [_summary(1.0)], "right": [_summary(2.0)]}
    control = {"left": [_summary(3.0)], "right": [_summary(4.0)]}
    rows = pcc.compute_cohort_stats(ms, control)
    seen = {(r["leg"], r["parameter"]) for r in rows}
    assert seen == {(leg, key) for leg in pcc._LEGS for key in pcc._SCORE_KEYS}


def test_compute_cohort_stats_empty_arm_no_crash():
    ms = {"left": [], "right": []}
    control = {"left": [_summary(1.0), _summary(2.0)], "right": []}
    rows = pcc.compute_cohort_stats(ms, control)
    row = next(r for r in rows if r["leg"] == "left" and r["parameter"] == "pt7")
    assert row["n_ms"] == 0
    assert row["ms_median"] is None
    assert row["mann_whitney_p"] is None


# ── current_qualifying_participants ────────────────────────────────────

def test_current_qualifying_participants_filters_by_threshold(monkeypatch):
    monkeypatch.setattr(pcc.common, "list_participants", lambda: {"6": {}, "7": {}, "8": {}})
    counts = {"6": {"left": 4, "right": 4}, "7": {"left": 3, "right": 4}, "8": {"left": 4, "right": 4}}
    monkeypatch.setattr(pcc.common, "leg_trial_counts", lambda pid: counts[pid])
    assert pcc.current_qualifying_participants() == {"6", "8"}


# ── _folder_hints_control ──────────────────────────────────────────────

def test_folder_hints_control_matches_case_insensitive(monkeypatch):
    fake = [{"participant": "6", "leg": "left", "condition": "left_Control"},
           {"participant": "7", "leg": "left", "condition": "pre"}]
    monkeypatch.setattr(pcc.common, "discover_all_trials", lambda: fake)
    assert pcc._folder_hints_control("6") is True
    assert pcc._folder_hints_control("7") is False


# ── build_composition_rows ─────────────────────────────────────────────

def test_build_composition_rows_classifies_and_counts_trials(monkeypatch):
    monkeypatch.setattr(pcc, "load_registry", lambda: ({"6": "Control"}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: {"13": "MS"}.get(pid))
    monkeypatch.setattr(pcc.common, "leg_trial_counts",
                        lambda pid: {"13": {"left": 5, "right": 5}, "6": {"left": 4, "right": 4}}[pid])
    rows = pcc.build_composition_rows({"13", "6"})
    by_pid = {r["pid"]: r for r in rows}
    assert by_pid["13"]["group"] == "MS" and by_pid["13"]["source"] == "metadata"
    assert by_pid["13"]["n_trials_left"] == 5 and by_pid["13"]["n_trials_right"] == 5
    assert by_pid["6"]["group"] == "Control" and by_pid["6"]["source"] == "registry"


def test_build_composition_rows_sorted_numerically_by_pid(monkeypatch):
    monkeypatch.setattr(pcc, "load_registry", lambda: ({}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: None)
    monkeypatch.setattr(pcc.common, "leg_trial_counts", lambda pid: {"left": 4, "right": 4})
    rows = pcc.build_composition_rows({"9", "13", "6"})
    assert [r["pid"] for r in rows] == ["6", "9", "13"]   # numeric, not lexicographic


# ── write_composition_csv ──────────────────────────────────────────────

def test_write_composition_csv_writes_all_rows(tmp_path):
    rows = [
        {"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
         "n_trials_left": 5, "n_trials_right": 5},
        {"pid": "6", "group": "Unclassified", "source": "no_entry", "diagnosis": None,
         "n_trials_left": 4, "n_trials_right": 4},
    ]
    out_path = tmp_path / "cohort_composition.csv"
    pcc.write_composition_csv(rows, str(out_path))
    content = out_path.read_text(encoding="utf-8")
    assert "pid,group,source,n_trials_left,n_trials_right" in content
    assert "13,MS,metadata,5,5" in content
    assert "6,Unclassified,no_entry,4,4" in content


# ── print_composition_banner ───────────────────────────────────────────

def test_print_composition_banner_lists_every_group(capsys, monkeypatch):
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: False)
    rows = [
        {"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS", "n_trials_left": 5, "n_trials_right": 5},
        {"pid": "6", "group": "Control", "source": "registry", "diagnosis": "Control", "n_trials_left": 4, "n_trials_right": 4},
        {"pid": "9", "group": "Excluded", "source": "metadata", "diagnosis": "Stroke", "n_trials_left": 4, "n_trials_right": 4},
        {"pid": "7", "group": "Unclassified", "source": "no_entry", "diagnosis": None, "n_trials_left": 4, "n_trials_right": 4},
        {"pid": "8", "group": "Unclassified", "source": "registry_missing", "diagnosis": None, "n_trials_left": 4, "n_trials_right": 4},
    ]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out
    assert "MS:" in out and "13" in out
    assert "Control:" in out and "6" in out
    assert "Excluded:" in out and "9 (Stroke)" in out
    assert "7" in out and "no_entry" in out
    assert "registry_missing" in out and "8" in out
