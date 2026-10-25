import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json

import pt_cohort_common as pcc


# ── classify_participant ─────────────────────────────────────────────────

def test_classify_metadata_ms():
    assert pcc.classify_participant("13", "MS", {}, True) == ("MS", "metadata")


def test_classify_metadata_unaffected_control():
    assert pcc.classify_participant("6", "Unaffected Control", {}, True) == ("Control", "metadata")


def test_classify_metadata_stroke_is_its_own_arm_other_is_excluded():
    """Stroke became a full comparison arm on 2026-08-26 -- post-stroke
    participants are part of the study, so classifying them as Excluded
    (what this test asserted before) would drop them from the analysis.
    "Other Motor Impairment" is still genuinely outside the comparison."""
    assert pcc.classify_participant("9", "Stroke", {}, True) == ("Stroke", "metadata")
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


def test_classify_non_string_registry_entry_falls_through_without_raising():
    # A malformed hand-edit to participant_groups.json (e.g. {"6": 1})
    # must fall through to Unclassified/no_entry, not raise AttributeError
    # from calling .strip() on a non-string.
    registry = {"6": 1}
    assert pcc.classify_participant("6", None, registry, True) == ("Unclassified", "no_entry")


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


def test_load_registry_json_array_treated_as_missing(tmp_path, monkeypatch, capsys):
    # Valid JSON, wrong shape: a hand-edit that turns the registry into a
    # bare list instead of a {pid: diagnosis} object.
    path = tmp_path / "participant_groups.json"
    path.write_text(json.dumps(["6", "7"]), encoding="utf-8")
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(path))
    assert pcc.load_registry() == ({}, False)
    assert "not a JSON object" in capsys.readouterr().out


def test_load_registry_json_string_treated_as_missing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "participant_groups.json"
    path.write_text(json.dumps("just a string"), encoding="utf-8")
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(path))
    assert pcc.load_registry() == ({}, False)
    assert "not a JSON object" in capsys.readouterr().out


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


# ── compute_pairwise_stats (three-arm; replaced compute_cohort_stats) ────

def _summary(pt7):
    d = {k: 1.0 for k in pcc._SCORE_KEYS}
    d["pt7"] = pt7
    return d


def _pt7(rows, arm_a, arm_b, leg="left"):
    return next(r for r in rows if r["leg"] == leg and r["parameter"] == "pt7"
                and r["arm_a"] == arm_a and r["arm_b"] == arm_b)


def test_compute_pairwise_stats_known_values():
    arms = {"MS": {"left": [_summary(1.0), _summary(2.0), _summary(3.0)], "right": []},
            "Stroke": {"left": [], "right": []},
            "Control": {"left": [_summary(10.0), _summary(11.0), _summary(12.0)], "right": []}}
    rows = pcc.compute_pairwise_stats(arms)
    row = _pt7(rows, "MS", "Control")
    assert row["n_a"] == 3 and row["n_b"] == 3
    assert row["a_median"] == 2.0
    assert row["b_median"] == 11.0
    assert row["cliffs_delta"] == 1.0
    assert row["effect_size"] == "large"
    assert row["mann_whitney_p"] is not None


def test_compute_pairwise_stats_small_n_is_na():
    arms = {"MS": {"left": [_summary(1.0)], "right": []},
            "Stroke": {"left": [], "right": []},
            "Control": {"left": [_summary(10.0), _summary(11.0)], "right": []}}
    rows = pcc.compute_pairwise_stats(arms)
    row = _pt7(rows, "MS", "Control")
    assert row["n_a"] == 1
    assert row["mann_whitney_p"] is None
    assert row["cliffs_delta"] is None
    assert row["effect_size"] == "n/a"
    assert row["holm_p"] is None      # untestable contrasts are not in the family
    assert row["a_median"] == 1.0     # still reported -- just no significance test


def test_compute_pairwise_stats_covers_every_leg_key_and_contrast():
    arms = {"MS": {"left": [_summary(1.0)], "right": [_summary(2.0)]},
            "Stroke": {"left": [_summary(5.0)], "right": [_summary(6.0)]},
            "Control": {"left": [_summary(3.0)], "right": [_summary(4.0)]}}
    rows = pcc.compute_pairwise_stats(arms)
    seen = {(r["leg"], r["parameter"], r["arm_a"], r["arm_b"]) for r in rows}
    assert seen == {(leg, key, a, b) for leg in pcc._LEGS for key in pcc._SCORE_KEYS
                    for a, b in pcc._CONTRASTS}


def test_compute_pairwise_stats_empty_arm_no_crash():
    """Stroke is legitimately empty until a stroke participant is recorded,
    so an absent arm must report medians of None rather than raising."""
    arms = {"MS": {"left": [], "right": []},
            "Stroke": {"left": [], "right": []},
            "Control": {"left": [_summary(1.0), _summary(2.0)], "right": []}}
    rows = pcc.compute_pairwise_stats(arms)
    row = _pt7(rows, "MS", "Control")
    assert row["n_a"] == 0
    assert row["a_median"] is None
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


def test_build_composition_rows_preserves_unrecognized_metadata_diagnosis(monkeypatch):
    # Finding 1: an unrecognized (typo'd) metadata diagnosis must not be
    # discarded just because classify_participant() didn't resolve it to
    # a known arm -- `diagnosis` should still carry the raw string through
    # to the composition row so the banner can surface it by name.
    monkeypatch.setattr(pcc, "load_registry", lambda: ({}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: "Not A Real Diagnosis")
    monkeypatch.setattr(pcc.common, "leg_trial_counts", lambda pid: {"left": 4, "right": 4})
    rows = pcc.build_composition_rows({"7"})
    assert rows[0]["group"] == "Unclassified" and rows[0]["source"] == "no_entry"
    assert rows[0]["diagnosis"] == "Not A Real Diagnosis"


def test_build_composition_rows_unrecognized_metadata_with_resolving_registry_shows_registry_diagnosis(monkeypatch):
    # Regression for the Finding-1 fix regressing this adjacent case: when
    # metadata is present but unrecognized (a typo) AND the registry entry
    # is what actually resolves classification, `diagnosis` must carry the
    # registry's string ("Other Motor Impairment"), not the metadata typo --
    # otherwise the Excluded banner shows a diagnosis that was never used
    # for classification while hiding the real reason.
    monkeypatch.setattr(pcc, "load_registry", lambda: ({"5": "Other Motor Impairment"}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: "Multipl Sclerosis")
    monkeypatch.setattr(pcc.common, "leg_trial_counts", lambda pid: {"left": 4, "right": 4})
    rows = pcc.build_composition_rows({"5"})
    assert rows[0]["group"] == "Excluded" and rows[0]["source"] == "registry"
    assert rows[0]["diagnosis"] == "Other Motor Impairment"


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


# ── write_stats_csv ─────────────────────────────────────────────────────

def test_write_contrasts_csv_header_documents_cliffs_delta_sign_convention(tmp_path):
    """With three arms the sign convention can no longer be baked into the
    header as "control_minus_ms" -- each row names its own arm_a/arm_b, so
    the header says b_minus_a and the row supplies which is which."""
    rows = pcc.compute_pairwise_stats({
        "MS": {"left": [_summary(1.0), _summary(2.0)], "right": []},
        "Stroke": {"left": [], "right": []},
        "Control": {"left": [_summary(10.0), _summary(11.0)], "right": []}})
    out_path = tmp_path / "cohort_stats.csv"
    pcc.write_contrasts_csv(rows, str(out_path))
    header = out_path.read_text(encoding="utf-8").splitlines()[0]
    assert "cliffs_delta_b_minus_a" in header
    assert header.split(",")[-2] == "cliffs_delta_b_minus_a"
    assert "arm_a" in header and "arm_b" in header and "holm_p" in header


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


def test_print_composition_banner_shows_unrecognized_diagnosis_text(capsys, monkeypatch):
    # Finding 1: a bare pid under Unclassified gives no clue that
    # metadata.json had a typo -- the raw diagnosis string must be visible.
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: False)
    rows = [
        {"pid": "7", "group": "Unclassified", "source": "no_entry",
         "diagnosis": "Not A Real Diagnosis", "n_trials_left": 4, "n_trials_right": 4},
    ]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out
    assert "Not A Real Diagnosis" in out
    assert "7 (unrecognized diagnosis:" in out


def test_print_composition_banner_no_entry_falls_back_to_folder_hint_when_no_diagnosis(capsys, monkeypatch):
    # The folder-hint suffix logic must still apply when there's no raw
    # diagnosis string at all (the pre-existing no_entry case).
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: True)
    rows = [
        {"pid": "7", "group": "Unclassified", "source": "no_entry",
         "diagnosis": None, "n_trials_left": 4, "n_trials_right": 4},
    ]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out
    assert "7 (folder suggests 'control')" in out
    assert "unrecognized diagnosis" not in out


def test_print_composition_banner_notes_missing_recordings_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pcc, "REC_ROOT", str(tmp_path / "Recordings"))  # doesn't exist
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: False)
    rows = [{"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
            "n_trials_left": 5, "n_trials_right": 5}]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out
    assert "Recordings/ is empty or absent" in out


def test_print_composition_banner_notes_empty_recordings_root(tmp_path, monkeypatch, capsys):
    rec_root = tmp_path / "Recordings"
    rec_root.mkdir()   # exists but has no Participant_* subdirs
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: False)
    rows = [{"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
            "n_trials_left": 5, "n_trials_right": 5}]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out
    assert "Recordings/ is empty or absent" in out


def test_print_composition_banner_no_note_when_recordings_root_has_participants(tmp_path, monkeypatch, capsys):
    rec_root = tmp_path / "Recordings"
    (rec_root / "Participant_13").mkdir(parents=True)
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: False)
    rows = [{"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
            "n_trials_left": 5, "n_trials_right": 5}]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out
    assert "Recordings/ is empty or absent" not in out


def test_print_composition_banner_no_note_when_no_rows(tmp_path, monkeypatch, capsys):
    # No qualifying pids at all -- nothing to warn about.
    monkeypatch.setattr(pcc, "REC_ROOT", str(tmp_path / "Recordings"))
    pcc.print_composition_banner([])
    out = capsys.readouterr().out
    assert "Recordings/ is empty or absent" not in out


# ── run_cohort_comparison orchestration ─────────────────────────────────

def _stub_common(monkeypatch, qualifying, groups, trials):
    """qualifying: set of pids. groups: {pid: (group, source)}. trials:
    {pid: {"left": [...], "right": [...]}} of scored trial dicts (each
    with all _SCORE_KEYS)."""
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: qualifying)
    monkeypatch.setattr(pcc, "load_registry", lambda: ({}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: None)
    monkeypatch.setattr(pcc.common, "leg_trial_counts",
                        lambda pid: {leg: len(trials.get(pid, {}).get(leg, [])) for leg in pcc._LEGS})
    monkeypatch.setattr(pcc, "classify_participant",
                        lambda pid, md, _reg, exists: groups.get(pid, ("Unclassified", "no_entry")))
    monkeypatch.setattr(pcc.common, "collect_participant",
                        lambda pid: ({(leg, "cond"): trials.get(pid, {}).get(leg, []) for leg in pcc._LEGS}, []))


def test_run_cohort_comparison_skips_when_control_arm_empty(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    _stub_common(monkeypatch, {"13"}, {"13": ("MS", "metadata")},
                {"13": {"left": [_trial()] * 4, "right": [_trial()] * 4}})
    pcc.run_cohort_comparison()
    out = capsys.readouterr().out
    assert "Cohort comparison skipped" in out
    assert (tmp_path / "cohort_composition.csv").is_file()   # written even when skipped


def test_run_cohort_comparison_writes_composition_csv_with_correct_groups(monkeypatch, tmp_path):
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    _stub_common(monkeypatch, {"13"}, {"13": ("MS", "metadata")},
                {"13": {"left": [_trial()] * 4, "right": [_trial()] * 4}})
    pcc.run_cohort_comparison()
    content = (tmp_path / "cohort_composition.csv").read_text(encoding="utf-8")
    assert "13,MS,metadata,4,4" in content


def test_run_cohort_comparison_runs_stats_and_figure_when_both_arms_present(monkeypatch, tmp_path):
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    monkeypatch.setattr(pcc, "STATS_CSV", str(tmp_path / "ms_vs_control_stats.csv"))
    figure_calls = []
    monkeypatch.setattr(pcc, "make_cohort_comparison_figure",
                        lambda *a, **k: figure_calls.append(True), raising=False)
    _stub_common(monkeypatch, {"13", "6"},
                {"13": ("MS", "metadata"), "6": ("Control", "registry")},
                {"13": {"left": [_trial(pt7=1.0)] * 4, "right": [_trial(pt7=1.0)] * 4},
                 "6": {"left": [_trial(pt7=2.0)] * 4, "right": [_trial(pt7=2.0)] * 4}})
    pcc.run_cohort_comparison()
    assert figure_calls == [True]
    assert (tmp_path / "ms_vs_control_stats.csv").is_file()


def test_run_cohort_comparison_filters_none_summaries(monkeypatch, tmp_path):
    # "13" clears the raw TRIAL_THRESHOLD gate (4 trials on file) but NONE
    # of them scored -- collect_participant's by_leg_tp reflects that as
    # empty lists for both legs. run_cohort_comparison must not crash, and
    # the resulting stats/figure must show 0 MS contributors while
    # cohort_composition.csv still shows 13's raw (non-zero) trial count.
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    monkeypatch.setattr(pcc, "STATS_CSV", str(tmp_path / "ms_vs_control_stats.csv"))
    figure_calls = []
    monkeypatch.setattr(pcc, "make_cohort_comparison_figure",
                        lambda *a, **k: figure_calls.append(a), raising=False)
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: {"13", "6"})
    monkeypatch.setattr(pcc, "all_classified_pids",
                        lambda: {"MS": ["13"], "Stroke": [], "Control": ["6"]})
    monkeypatch.setattr(pcc, "load_registry", lambda: ({}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: None)
    monkeypatch.setattr(pcc, "classify_participant",
                        lambda pid, md, _reg, exists: {"13": ("MS", "metadata"), "6": ("Control", "metadata")}[pid])
    monkeypatch.setattr(pcc.common, "leg_trial_counts",
                        lambda pid: {"13": {"left": 4, "right": 4}, "6": {"left": 4, "right": 4}}[pid])
    monkeypatch.setattr(pcc.common, "collect_participant", lambda pid: (
        {("left", "cond"): [], ("right", "cond"): []} if pid == "13"
        else {("left", "cond"): [_trial(pt7=2.0)] * 4, ("right", "cond"): [_trial(pt7=2.0)] * 4},
        []))
    pcc.run_cohort_comparison()   # must not raise despite 13 contributing zero summaries
    assert len(figure_calls) == 1
    stats_content = (tmp_path / "ms_vs_control_stats.csv").read_text(encoding="utf-8")
    # Three-arm CSV: rows are (leg, parameter, arm_a, arm_b, n_a, ...), so the
    # "MS contributed nothing to this leg" case now reads as n_a=0 on the
    # MS-vs-Control contrast rather than a bare "left,pt7,0,".
    assert "left,pt7,MS,Control,0," in stats_content
    comp_content = (tmp_path / "cohort_composition.csv").read_text(encoding="utf-8")
    assert "13,MS,metadata,4,4" in comp_content   # raw counts still shown despite 0 scored


# ── make_cohort_comparison_figure (smoke test only -- pixel content isn't
# asserted anywhere else in this repo's plotting functions either) ────────

def test_run_cohort_comparison_real_figure_end_to_end(monkeypatch, tmp_path):
    # Finding 7: every other run_cohort_comparison() test stubs out
    # make_cohort_comparison_figure with a no-op spy, and the only direct
    # test of the real figure function uses hand-built summaries -- so the
    # 11-argument call site was guarded only by careful reading, not a
    # test. This one lets the REAL figure function run, fed by the REAL
    # orchestration (real collect_participant -> real
    # aggregate_participant_summary -> real compute_cohort_stats), with a
    # two-arm scenario built from per-trial values, not hand-built
    # summaries. Participant "2" only has right-leg trials, so it also
    # exercises the zero-summaries-for-a-leg path (left has only 1 MS
    # contributor) reaching the real figure.
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    monkeypatch.setattr(pcc, "STATS_CSV", str(tmp_path / "ms_vs_control_stats.csv"))
    monkeypatch.setattr(pcc, "FIGURE_PNG", str(tmp_path / "ms_vs_control_boxplots.png"))

    trials = {
        "1": {"left": [_trial(pt7=0.3)] * 4, "right": [_trial(pt7=0.35)] * 4},
        "2": {"left": [], "right": [_trial(pt7=0.5)] * 4},
        "3": {"left": [_trial(pt7=1.2)] * 4, "right": [_trial(pt7=1.1)] * 4},
        "4": {"left": [_trial(pt7=1.3)] * 4, "right": [_trial(pt7=1.4)] * 4},
    }
    groups = {"1": ("MS", "metadata"), "2": ("MS", "metadata"),
             "3": ("Control", "registry"), "4": ("Control", "registry")}
    _stub_common(monkeypatch, {"1", "2", "3", "4"}, groups, trials)

    pcc.run_cohort_comparison()

    fig_path = tmp_path / "ms_vs_control_boxplots.png"
    assert fig_path.is_file()
    assert fig_path.stat().st_size > 0


def test_make_cohort_comparison_figure_writes_png_without_raising(tmp_path):
    ms_summaries = {"left": [_summary(1.0)], "right": [_summary(1.2)]}
    control_summaries = {"left": [_summary(2.0)], "right": [_summary(2.2)]}
    ms_raw = {"left": [_trial(pt7=1.0)], "right": [_trial(pt7=1.2)]}
    control_raw = {"left": [_trial(pt7=2.0)], "right": [_trial(pt7=2.2)]}
    contrast_rows = pcc.compute_pairwise_stats({
        "MS": ms_summaries, "Stroke": {"left": [], "right": []},
        "Control": control_summaries})
    out_path = tmp_path / "cohort_boxplots.png"
    pcc.make_cohort_comparison_figure(
        ms_summaries, ms_raw, 1, 1, control_summaries, control_raw, 1, 1, 2,
        str(out_path), contrast_rows)
    assert out_path.is_file()
    assert out_path.stat().st_size > 0


# ── build_cohort_snapshot / write_cohort_artifacts / leg_cohort_reference ──

def test_collect_arm_data_returns_summaries_by_pid(monkeypatch):
    fake_by_leg_tp = {
        ("left", "pre"): [{"pid": "13_left_pre", "trial": "1", "pt7": 0.3,
                          "R2n": 0.9, "N": 3.0, "phi_max_ratio": 0.5, "omega_max_n": 1.0,
                          "omega_min_n": 0.2, "f": 1.5, "area_ratio": 0.1}],
        ("right", "pre"): [],
    }
    monkeypatch.setattr(pcc.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))

    summaries, raw_trials, contributing_pids, summaries_by_pid = pcc._collect_arm_data(["13"])

    assert summaries_by_pid[("13", "left")] is not None
    assert summaries_by_pid[("13", "left")]["pt7"] == 0.3
    assert summaries_by_pid[("13", "right")] is None


def test_build_cohort_snapshot_skipped_when_arm_empty(monkeypatch):
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: {"13"})
    monkeypatch.setattr(pcc, "build_composition_rows",
                        lambda pids: [{"pid": "13", "group": "MS", "source": "metadata",
                                      "diagnosis": "MS", "n_trials_left": 4, "n_trials_right": 4}])

    snapshot = pcc.build_cohort_snapshot()

    assert snapshot["ms_pids"] == ["13"]
    assert snapshot["control_pids"] == []
    assert snapshot["contrast_rows"] is None
    assert snapshot["ms_summaries"] is None


def test_write_cohort_artifacts_no_recollection_when_arm_empty(monkeypatch, tmp_path):
    """write_cohort_artifacts must render entirely from the snapshot --
    patch collect_participant to raise if it's ever called from within
    this function, proving no rescanning happens."""
    monkeypatch.setattr(pcc.common, "collect_participant",
                        lambda pid: (_ for _ in ()).throw(AssertionError("should not recollect")))
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    snapshot = {
        "composition_rows": [{"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
                             "n_trials_left": 4, "n_trials_right": 4}],
        "ms_pids": ["13"], "control_pids": [], "ms_summaries": None, "control_summaries": None,
        "ms_raw": None, "control_raw": None, "summaries_by_pid": {},
        "ms_n_participants": None, "ms_n_trials": None,
        "control_n_participants": None, "control_n_trials": None,
        "contrast_rows": None, "n_excluded_unclassified": 0, "range_rows": [],
    }
    pcc.write_cohort_artifacts(snapshot)   # must not raise


def test_run_cohort_comparison_still_works_as_combinator(monkeypatch, tmp_path):
    """run_cohort_comparison() must remain callable exactly as today's
    tests already call it -- this is the back-compat contract for the 5
    existing tests in this file that call pcc.run_cohort_comparison()
    directly."""
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: set())
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    pcc.run_cohort_comparison()   # must not raise, same as before this task


def test_leg_cohort_reference_leave_one_out_for_own_arm(monkeypatch):
    snapshot = {
        "ms_pids": ["13", "14"], "control_pids": ["6", "7"],
        "summaries_by_pid": {
            ("13", "left"): {"pt7": 0.30}, ("14", "left"): {"pt7": 0.50},
            ("6", "left"): {"pt7": 0.10}, ("7", "left"): {"pt7": 0.20},
        },
    }
    ref = pcc.leg_cohort_reference(snapshot, "13", "left")
    # MS arm excludes participant 13 -> only 14's 0.50 remains.
    assert ref["ms_median"] == 0.50
    assert ref["ms_n"] == 1
    # Control arm is untouched -- participant 13 isn't in it.
    assert ref["control_median"] == pytest.approx(0.15)   # median of 0.10, 0.20
    assert ref["control_n"] == 2
    assert ref["leave_one_out_arm"] == "MS"


def test_leg_cohort_reference_none_when_not_comparable():
    assert pcc.leg_cohort_reference({"ms_pids": ["13"], "control_pids": []}, "13", "left") is None


def test_all_classified_pids_covers_every_arm(monkeypatch):
    """Regression: the result dict was hardcoded {"MS": [], "Control": []},
    so when Stroke became a full arm on 2026-08-26 it was silently dropped
    here. Key off _ARMS so a fourth arm cannot repeat the mistake."""
    assert set(pcc.all_classified_pids().keys()) == set(pcc._ARMS)


def test_stroke_participant_short_on_trials_still_reaches_the_cohort_pool(monkeypatch):
    """The composition pool unions the trial-threshold-qualifying set with
    every classified participant, so a participant one trial short still
    counts. That union named only MS and Control, which meant a Stroke
    participant short a trial was dropped while an identically-placed MS
    participant was kept. Both must survive."""
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: set())
    monkeypatch.setattr(pcc, "all_classified_pids",
                        lambda: {"MS": ["4"], "Stroke": ["21"], "Control": ["6"]})
    seen = {}

    def _capture(pids):
        seen["pids"] = set(pids)
        return []

    monkeypatch.setattr(pcc, "build_composition_rows", _capture)
    try:
        pcc.build_cohort_snapshot()
    except Exception:
        pass  # downstream stages need real data; the pool is what we assert on
    assert seen.get("pids") == {"4", "21", "6"}, (
        f"Stroke participant dropped from the cohort pool: {seen.get('pids')}")


# ── secondary-view labelling (2026-08-28) ──────────────────────────────
# Grouping is by spasticity now, not diagnosis. This module is kept as a
# reference view, so its output has to SAY so -- a caveat that lives only in
# the module docstring never reaches whoever opens the PNG or reads the run log.

def test_composition_banner_marks_itself_as_the_secondary_view(capsys, monkeypatch):
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: False)
    rows = [{"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
             "n_trials_left": 5, "n_trials_right": 5}]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out.lower()
    assert "secondary" in out
    assert "spasticity" in out, "must name what the primary stratification IS"


def test_module_docstring_records_why_diagnosis_is_no_longer_primary():
    """The label is a claim about the analysis, so the reasoning has to be
    findable next to it rather than only in a commit message."""
    doc = (pcc.__doc__ or "").lower()
    assert "not the primary comparison" in doc
    assert "spasticity" in doc
