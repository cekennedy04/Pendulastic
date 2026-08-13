import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

import pt_report_common


@pytest.fixture(autouse=True)
def _isolated_registries(tmp_path, monkeypatch):
    """Every test gets its own empty registry files so tests never read/
    write the real trial_quality_tags.json or excluded_trials.json."""
    monkeypatch.setattr(pt_report_common, "TRIAL_QUALITY_TAGS_PATH",
                        str(tmp_path / "trial_quality_tags.json"))
    monkeypatch.setattr(pt_report_common, "EXCLUDED_TRIALS_PATH",
                        str(tmp_path / "excluded_trials.json"))
    yield


def test_load_quality_tags_missing_file_returns_empty_dict():
    assert pt_report_common.load_quality_tags() == {}


def test_save_quality_tag_then_load_round_trips():
    pt_report_common.save_quality_tag("5_left_pre_T1", "calibration_hold",
                                      "hold tilted", timestamp="2026-08-11T00:00:00+00:00")
    tags = pt_report_common.load_quality_tags()
    assert tags == {
        "5_left_pre_T1": {
            "category": "calibration_hold",
            "details": "hold tilted",
            "timestamp": "2026-08-11T00:00:00+00:00",
        }
    }


def test_save_quality_tag_defaults_timestamp_when_not_given():
    pt_report_common.save_quality_tag("5_left_pre_T1", "other", "misc note")
    tags = pt_report_common.load_quality_tags()
    assert tags["5_left_pre_T1"]["timestamp"]  # non-empty, auto-filled


def test_save_quality_tag_rejects_invalid_category():
    with pytest.raises(ValueError, match="invalid category"):
        pt_report_common.save_quality_tag("5_left_pre_T1", "not_a_real_category", "x")
    assert pt_report_common.load_quality_tags() == {}


def test_save_quality_tag_overwrites_existing_entry_for_same_key():
    pt_report_common.save_quality_tag("5_left_pre_T1", "calibration_hold", "first",
                                      timestamp="2026-08-11T00:00:00+00:00")
    pt_report_common.save_quality_tag("5_left_pre_T1", "marker_occlusion", "second",
                                      timestamp="2026-08-11T01:00:00+00:00")
    tags = pt_report_common.load_quality_tags()
    assert len(tags) == 1
    assert tags["5_left_pre_T1"]["category"] == "marker_occlusion"
    assert tags["5_left_pre_T1"]["details"] == "second"


def test_clear_quality_tag_removes_entry():
    pt_report_common.save_quality_tag("5_left_pre_T1", "other", "x")
    pt_report_common.clear_quality_tag("5_left_pre_T1")
    assert pt_report_common.load_quality_tags() == {}


def test_clear_quality_tag_is_noop_when_key_not_tagged():
    pt_report_common.clear_quality_tag("does_not_exist")  # must not raise
    assert pt_report_common.load_quality_tags() == {}


def test_add_excluded_trial_then_load_round_trips():
    pt_report_common.add_excluded_trial("5_left_pre_T1", "mounting slip visible on video")
    excluded = pt_report_common.load_excluded_trials()
    assert excluded == {"5_left_pre_T1": "mounting slip visible on video"}


def test_add_excluded_trial_preserves_other_existing_entries():
    pt_report_common.add_excluded_trial("13_right_post_T2", "muscle intervention")
    pt_report_common.add_excluded_trial("5_left_pre_T1", "mounting slip")
    excluded = pt_report_common.load_excluded_trials()
    assert excluded == {
        "13_right_post_T2": "muscle intervention",
        "5_left_pre_T1": "mounting slip",
    }


def test_clear_excluded_trial_removes_entry_and_preserves_others():
    pt_report_common.add_excluded_trial("13_right_post_T2", "muscle intervention")
    pt_report_common.add_excluded_trial("5_left_pre_T1", "mounting slip")
    pt_report_common.clear_excluded_trial("5_left_pre_T1")
    excluded = pt_report_common.load_excluded_trials()
    assert excluded == {"13_right_post_T2": "muscle intervention"}


def test_clear_excluded_trial_is_noop_when_key_not_excluded():
    pt_report_common.clear_excluded_trial("does_not_exist")  # must not raise
    assert pt_report_common.load_excluded_trials() == {}


def test_quality_tag_write_uses_atomic_replace_not_direct_write(tmp_path, monkeypatch):
    """Confirms the temp-file-then-os.replace pattern: after a save, no
    leftover .tmp file exists, and the real file exists."""
    monkeypatch.setattr(pt_report_common, "TRIAL_QUALITY_TAGS_PATH",
                        str(tmp_path / "tags.json"))
    pt_report_common.save_quality_tag("5_left_pre_T1", "other", "x")
    assert os.path.exists(str(tmp_path / "tags.json"))
    assert not os.path.exists(str(tmp_path / "tags.json.tmp"))
