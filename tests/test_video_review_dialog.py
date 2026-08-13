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


import tkinter as tk
import numpy as np
import pytest

try:
    import cv2 as _cv2_test
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

_root_window = None

def _get_root():
    global _root_window
    if _root_window is None:
        _root_window = tk.Tk()
        _root_window.withdraw()
    return _root_window


def _write_test_video(path, n_frames, w=64, h=48):
    """Writes n_frames distinct-valued solid-color frames so tests can
    verify which frame was actually read (frame i is filled with value
    (i * 20) % 256)."""
    out = _cv2_test.VideoWriter(
        path, _cv2_test.VideoWriter_fourcc(*"XVID"), 30.0, (w, h))
    for i in range(n_frames):
        val = (i * 20) % 256
        out.write(np.full((h, w, 3), val, dtype=np.uint8))
    out.release()


class _FakeEngine:
    def detect_people_at_frame(self, video_path, frame_index=0):
        return (None, [])
    def run_offline_track(self, *a, **kw):
        return ([], [], 30.0)


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_dialog_constructs_with_correct_total_frames(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review.avi")
    _write_test_video(video_path, 8)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[1.0] * 8, landmarks=[None] * 8,
        fps=30.0, leg="right", engine=_FakeEngine())

    assert dlg.total_frames == 8
    assert dlg._frame_idx == 0
    assert dlg.angles == [1.0] * 8
    assert dlg.landmarks == [None] * 8
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_dialog_angles_and_landmarks_are_copies_not_aliases(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review2.avi")
    _write_test_video(video_path, 4)
    r = _get_root()

    original_angles = [1.0, 2.0, 3.0, 4.0]
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=original_angles, landmarks=[None] * 4,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg.angles[0] = 999.0
    assert original_angles[0] == 1.0
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_read_frame_returns_correct_frame_by_index(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review3.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())

    frame3 = dlg._read_frame(3)
    assert frame3 is not None
    # Tolerance, not exact equality: XVID is a lossy codec, so the decoded
    # pixel value can drift a few units from what was written.
    assert abs(int(frame3[0, 0, 0]) - (3 * 20) % 256) < 15
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_on_scale_change_updates_frame_idx(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review4.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg._on_scale_change("4")
    assert dlg._frame_idx == 4
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_on_scale_change_ignored_while_retrack_in_progress(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review5.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._retrack_in_progress = True

    dlg._on_scale_change("4")
    assert dlg._frame_idx == 0
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_play_tick_does_not_advance_frame_while_retrack_in_progress(tmp_path):
    """Global Constraints requires playback paused, not just scrubbing --
    _play_tick must reschedule itself without advancing _frame_idx while a
    retrack is in flight."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review6.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._playing = True
    dlg._retrack_in_progress = True
    dlg._frame_idx = 2

    dlg._play_tick()

    assert dlg._frame_idx == 2       # unchanged
    assert dlg._playing is True      # still "wants to play", just paused
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_trail_for_collects_ankle_positions_within_trail_len(tmp_path):
    """Spec S3.1 says the dialog reuses TRAIL_LEN for the ankle-path trail
    -- _trail_for must return the last TRAIL_LEN frames' ankle positions
    (skipping None landmarks), in chronological order, looking back from
    an arbitrary frame_idx (not a sequential accumulation, since
    self.landmarks is already fully available at any frame)."""
    from video_review_dialog import AnnotatedVideoReviewDialog, TRAIL_LEN
    video_path = str(tmp_path / "review7.avi")
    n = TRAIL_LEN + 5
    _write_test_video(video_path, n)
    r = _get_root()

    landmarks = []
    for i in range(n):
        if i % 4 == 0:
            landmarks.append(None)  # some frames have no detection
        else:
            landmarks.append((None, None, (float(i), float(i))))

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * n, landmarks=landmarks,
        fps=30.0, leg="right", engine=_FakeEngine())

    fi = n - 1
    trail = dlg._trail_for(fi)

    assert len(trail) <= TRAIL_LEN
    expected = [landmarks[i][2] for i in range(max(0, fi - TRAIL_LEN + 1), fi + 1)
                if landmarks[i] is not None]
    assert trail == expected
    assert trail[-1] == (float(fi), float(fi))
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_trail_for_near_start_of_video_does_not_go_negative(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "review8.avi")
    _write_test_video(video_path, 3)
    r = _get_root()

    landmarks = [(None, None, (0.0, 0.0)), (None, None, (1.0, 1.0)), None]
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=landmarks,
        fps=30.0, leg="right", engine=_FakeEngine())

    trail = dlg._trail_for(1)

    assert trail == [(0.0, 0.0), (1.0, 1.0)]
    dlg.destroy()
