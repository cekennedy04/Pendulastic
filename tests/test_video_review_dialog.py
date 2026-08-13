# tests/test_video_review_dialog.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from video_review_dialog import _splice_from


def test_splice_from_exact_length_replaces_suffix():
    old = [1, 2, 3, 4, 5]
    result = _splice_from(old, 2, [30, 40, 50], pad_value=0)
    assert result == [1, 2, 30, 40, 50]


def test_splice_from_short_new_pads_with_pad_value():
    old = [1, 2, 3, 4, 5]
    result = _splice_from(old, 2, [30], pad_value=-1)
    assert result == [1, 2, 30, -1, -1]


def test_splice_from_long_new_truncates():
    old = [1, 2, 3, 4, 5]
    result = _splice_from(old, 2, [30, 40, 50, 60, 70], pad_value=0)
    assert result == [1, 2, 30, 40, 50]


def test_splice_from_start_idx_zero_replaces_everything():
    old = [1, 2, 3]
    result = _splice_from(old, 0, [9, 9, 9], pad_value=0)
    assert result == [9, 9, 9]


def test_splice_from_start_idx_at_end_leaves_old_unchanged():
    old = [1, 2, 3]
    result = _splice_from(old, 3, [], pad_value=0)
    assert result == [1, 2, 3]


def test_splice_from_does_not_mutate_input_lists():
    old = [1, 2, 3, 4, 5]
    new = [30, 40, 50]
    _splice_from(old, 2, new, pad_value=0)
    assert old == [1, 2, 3, 4, 5]
    assert new == [30, 40, 50]


def test_splice_from_nan_pad_value_for_angles(monkeypatch):
    import math
    old = [10.0, 20.0, 30.0]
    result = _splice_from(old, 1, [99.0], pad_value=float("nan"))
    assert result[0] == 10.0
    assert result[1] == 99.0
    assert math.isnan(result[2])
