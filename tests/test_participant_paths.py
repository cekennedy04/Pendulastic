"""participant_paths: group-prefixed folder naming and its resolver.

The 2026-09-01 rename moved every participant folder from Participant_{pid} to
its study prefix (Control_/CHAT_/MINT_ -- the MS study's folders were briefly
DOSA_ before it was renamed MINT). These tests pin the two properties the
rest of the pipeline leans on:

  1. BOTH naming schemes resolve, so a half-migrated tree -- or an archive that
     was never renamed -- still finds its data.
  2. The participant id read back out of a path is IDENTICAL under either
     scheme, so excluded_trials.json / trial_quality_tags.json keys, which are
     authored against the id and not the folder, survive the rename.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import participant_paths as pp


# ── diagnosis -> study prefix ──────────────────────────────────────────────

@pytest.mark.parametrize("diagnosis, expected", [
    ("Unaffected Control", pp.CONTROL_PREFIX),
    ("control", pp.CONTROL_PREFIX),
    ("Control", pp.CONTROL_PREFIX),
    ("Stroke", pp.STROKE_PREFIX),
    ("stroke", pp.STROKE_PREFIX),
    ("MS", pp.MS_PREFIX),
    ("ms", pp.MS_PREFIX),
    ("multiple sclerosis", pp.MS_PREFIX),
])
def test_every_spelling_the_study_has_used_maps_to_a_prefix(diagnosis, expected):
    """mas_scores.csv and metadata.json disagree on spelling ("MS" vs
    "multiple sclerosis", "control" vs "Unaffected Control"). Both are real,
    both must map, or a participant's folder silently falls back to the legacy
    name and the rename looks like it skipped them."""
    assert pp.group_for_diagnosis(diagnosis) == expected


@pytest.mark.parametrize("diagnosis", [
    "Other Motor Impairment", "Other", "", None, "   ",
])
def test_ungrouped_diagnosis_gets_no_prefix(diagnosis):
    """No study means no guess: fall back to Participant_ rather than filing
    someone under a trial they are not in."""
    assert pp.group_for_diagnosis(diagnosis) is None


# ── parsing a folder name back to an id ────────────────────────────────────

@pytest.mark.parametrize("name, pid", [
    ("Control_10", "10"),
    ("CHAT_19", "19"),
    ("MINT_11", "11"),
    ("Participant_10", "10"),
    ("control_10", "10"),          # Windows round-trips casing inconsistently
    ("MINT_5", "5"),
    ("DOSA_11", "11"),        # the MS study's former prefix still resolves
])
def test_pid_from_dir_name_reads_every_prefix(name, pid):
    assert pp.pid_from_dir_name(name) == pid


@pytest.mark.parametrize("name", ["Left", "Right", "pre", "Position_1", "", "metadata.json"])
def test_non_participant_names_are_not_mistaken_for_participants(name):
    assert pp.pid_from_dir_name(name) is None
    assert not pp.is_participant_dir_name(name)


def test_legacy_suffix_names_yield_the_same_id_they_always_did():
    """The old code did name.replace("Participant_", ""), which left the
    leg/condition tail on. Callers still depend on that exact string, so the
    replacement must not quietly start trimming it."""
    assert pp.pid_from_dir_name("Participant_13_left_post") == "13_left_post"
    assert pp.pid_from_dir_name("Participant_P001_msparticipant2") == "P001_msparticipant2"


def test_strip_prefix_passes_unprefixed_values_through():
    """strip_prefix stands in for .replace("Participant_", "") on values that
    may or may not carry a prefix; a bare id must survive unchanged."""
    assert pp.strip_prefix("Control_23") == "23"
    assert pp.strip_prefix("23") == "23"
    assert pp.strip_prefix("unknown") == "unknown"


# ── reading an id out of a whole path ──────────────────────────────────────

def test_pids_in_path_is_identical_under_both_schemes():
    """THE property that keeps excluded_trials.json valid across the rename:
    the key is built from the id, and the id does not move when the prefix
    does."""
    legacy = "Participant_15/Left/pre/Trial_4_imu.csv"
    renamed = "MINT_15/Left/pre/Trial_4_imu.csv"
    assert pp.pids_in_path(legacy) == pp.pids_in_path(renamed) == ["15"]


def test_pids_in_path_reports_every_distinct_participant():
    """Callers treat >1 as unattributable (archived data has a stray nested
    folder from another participant). That check only works if BOTH ids are
    seen, including across mixed naming."""
    mixed = "MINT_13/Participant_0_control/Position_1/trial_1.csv"
    assert pp.pids_in_path(mixed) == ["0", "13"]


def test_strip_leading_pid_leaves_the_condition_label():
    assert pp.strip_leading_pid("MINT_13_left_post") == "left_post"
    assert pp.strip_leading_pid("DOSA_13_left_post") == "left_post"
    assert pp.strip_leading_pid("Participant_13_left_post") == "left_post"
    assert pp.strip_leading_pid("Session_post") == "Session_post"


# ── resolving a participant to a directory ─────────────────────────────────

def test_find_participant_dir_accepts_either_scheme(tmp_path):
    for name, pid in (("Control_10", "10"), ("Participant_11", "11")):
        (tmp_path / name).mkdir()
    assert os.path.basename(pp.find_participant_dir(str(tmp_path), "10")) == "Control_10"
    assert os.path.basename(pp.find_participant_dir(str(tmp_path), "11")) == "Participant_11"
    assert pp.find_participant_dir(str(tmp_path), "99") is None


def test_a_short_id_never_claims_a_longer_one(tmp_path):
    """Participant 2 and participant 20 both exist in this study. A prefix or
    substring match would hand P2's resolver P20's folder -- and with it, the
    wrong person's recordings."""
    (tmp_path / "Control_20").mkdir()
    assert pp.find_participant_dir(str(tmp_path), "2") is None
    (tmp_path / "Control_2").mkdir()
    assert os.path.basename(pp.find_participant_dir(str(tmp_path), "2")) == "Control_2"


def test_legacy_sub_layout_is_still_found(tmp_path):
    """Recordings/Participant_5_left_side/ predates the Leg/condition tree and
    is still on disk in the archive."""
    (tmp_path / "Participant_5_left_side").mkdir()
    assert os.path.basename(
        pp.find_participant_dir(str(tmp_path), "5")) == "Participant_5_left_side"


def test_an_existing_folder_always_wins_over_the_diagnosis(tmp_path):
    """Never split a participant's data across two folders: if they already
    have one, new trials go there whatever the diagnosis field now says."""
    (tmp_path / "Participant_7").mkdir()
    assert pp.participant_dir_name("7", str(tmp_path), diagnosis="Stroke") == "Participant_7"


def test_a_new_participant_takes_their_study_prefix(tmp_path):
    assert pp.participant_dir_name("30", str(tmp_path), diagnosis="Stroke") == "CHAT_30"
    assert pp.participant_dir_name("31", str(tmp_path), diagnosis="MS") == "MINT_31"
    assert pp.participant_dir_name("32", str(tmp_path),
                                   diagnosis="Unaffected Control") == "Control_32"


def test_a_new_participant_with_no_study_keeps_the_legacy_name(tmp_path):
    assert pp.participant_dir_name(
        "33", str(tmp_path), diagnosis="Other Motor Impairment") == "Participant_33"


def test_the_ms_studys_former_prefix_still_resolves(tmp_path):
    """The MS study was DOSA before it was MINT. An un-renamed archive -- or a
    tree caught half way through the rename -- must still find its data, and
    must not grow a second folder beside the one it already has."""
    (tmp_path / "DOSA_34").mkdir()
    assert pp.pid_from_dir_name("DOSA_34") == "34"
    assert pp.find_participant_dir(str(tmp_path), "34") == str(tmp_path / "DOSA_34")
    # An existing folder wins over the current prefix, so nothing is split.
    assert pp.participant_dir_name("34", str(tmp_path), diagnosis="MS") == "DOSA_34"
    # ...but a participant with no folder yet gets the new name.
    assert pp.participant_dir_name("35", str(tmp_path), diagnosis="MS") == "MINT_35"


def test_prefix_is_read_from_metadata_when_no_diagnosis_is_passed(tmp_path):
    """OptiTrack folders carry no metadata.json of their own, so the prefix for
    a new one has to come from the participant's Recordings metadata."""
    import json
    d = tmp_path / "MINT_40"
    d.mkdir()
    (d / "metadata.json").write_text(json.dumps({"participant_id": "40",
                                                 "diagnosis": "MS"}),
                                     encoding="utf-8")
    assert pp.diagnosis_from_metadata(str(tmp_path), "40") == "MS"
    assert pp.participant_dir_name("40", str(tmp_path)) == "MINT_40"


def test_participant_dir_creates_with_the_right_prefix(tmp_path):
    made = pp.participant_dir(str(tmp_path), "41", diagnosis="Stroke", create=True)
    assert os.path.isdir(made)
    assert os.path.basename(made) == "CHAT_41"


# ── enumeration ────────────────────────────────────────────────────────────

def test_iter_participant_dirs_covers_both_schemes_and_skips_files(tmp_path):
    for name in ("Control_2", "CHAT_19", "MINT_11", "Participant_test"):
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "Archive").mkdir()
    found = {pid: name for pid, name, _full in pp.iter_participant_dirs(str(tmp_path))}
    assert found == {"2": "Control_2", "19": "CHAT_19",
                     "11": "MINT_11", "test": "Participant_test"}


def test_iter_participant_dirs_on_a_missing_root_is_empty_not_an_error(tmp_path):
    """Recordings/ is gitignored, so a fresh clone genuinely has no tree."""
    assert list(pp.iter_participant_dirs(str(tmp_path / "nope"))) == []


def test_glob_participants_unions_the_prefixes_without_duplicates(tmp_path):
    for name in ("Control_2", "MINT_11", "Participant_test"):
        d = tmp_path / name
        d.mkdir()
        (d / "metadata.json").write_text("{}", encoding="utf-8")
    hits = pp.glob_participants(str(tmp_path), "metadata.json")
    assert len(hits) == 3
    assert len(set(hits)) == 3


def test_participant_glob_for_scopes_to_one_participant(tmp_path):
    for name in ("Control_2", "Control_20"):
        d = tmp_path / name / "Left" / "pre"
        d.mkdir(parents=True)
        (d / "Trial_1_imu.csv").write_text("x", encoding="utf-8")
    hits = pp.participant_glob_for(str(tmp_path), "2", "**", "Trial_*_imu.csv",
                                   recursive=True)
    assert len(hits) == 1
    assert "Control_2" in hits[0] and "Control_20" not in hits[0]


def test_participant_glob_for_an_absent_participant_is_empty(tmp_path):
    assert pp.participant_glob_for(str(tmp_path), "99", "*.csv") == []


# ── The double-prefix bug ────────────────────────────────────────────────────
#
# The 2026-09-08 session -- the only capture where a clinician graded the same
# legs the instrument measured -- landed in Recordings/MINT_MINT_25 because the
# recorder prefixed an already-prefixed id. pid_from_dir_name returns "MINT_25"
# for that folder, which is not a participant id, so every discovery path
# skipped it and nothing could report a session it could not see. It surfaced
# only because someone went looking by hand.


def test_an_id_carrying_its_own_study_prefix_is_refused():
    for bad in ("MINT_25", "Control_16", "CHAT_4", "mint_4"):
        msg = pp.check_participant_id(bad)
        assert msg, f"{bad!r} should have been refused"
        assert "already carries" in msg
        assert "skipped by every analysis path" in msg


def test_the_refusal_says_what_to_type_instead():
    """An operator mid-session needs the fix, not a diagnosis."""
    msg = pp.check_participant_id("MINT_25")
    assert "'25'" in msg


def test_a_bare_id_is_accepted():
    for good in ("25", "16", "P001", "5_left_side"):
        assert pp.check_participant_id(good) is None, good


def test_a_prefix_with_no_id_is_refused_too():
    assert "only a study prefix" in pp.check_participant_id("MINT_")


def test_strip_group_prefix_round_trips():
    assert pp.strip_group_prefix("MINT_25") == ("25", "MINT")
    assert pp.strip_group_prefix("25") == ("25", None)
    assert pp.strip_group_prefix("Control_16") == ("16", "Control")


def test_the_folder_the_bug_produced_still_resolves_to_a_non_participant():
    """The reason the guard is at entry: once the folder exists, the id it
    yields is silently wrong rather than absent, and no later stage can tell."""
    assert pp.pid_from_dir_name("MINT_MINT_25") == "MINT_25"
    assert pp.pid_from_dir_name("MINT_25") == "25"
