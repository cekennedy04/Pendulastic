# tests/test_video_review_dialog.py
import json
import math
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


def test_splice_from_start_idx_beyond_old_length_returns_old_unchanged():
    """Finding 1: total_frames (from cv2.CAP_PROP_FRAME_COUNT) can
    over-report vs len(self.angles) (from run_offline_track's
    read-until-failure loop), so start_idx > len(old) is reachable in
    production. target_len must clamp to 0, not go negative -- a negative
    slice bound on new[:target_len] would silently slice from new's tail
    instead of yielding []."""
    old = [1, 2, 3]
    result = _splice_from(old, 5, [9, 9, 9, 9], pad_value=0)
    assert result == [1, 2, 3]
    assert len(result) == 3


def test_splice_from_start_idx_one_past_end_with_empty_new_returns_old_unchanged():
    old = [1, 2, 3]
    result = _splice_from(old, len(old) + 1, [], pad_value=0)
    assert result == [1, 2, 3]
    assert len(result) == 3


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


class _SyncThread:
    """Runs target() synchronously in start() -- makes the retrack
    background thread deterministic for testing, matching the convention
    in tests/test_post_processing_panel.py."""
    def __init__(self, target=None, daemon=None, **kw):
        self._target = target
    def start(self):
        self._target()


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _make_pose(knee_x=0.5, ankle_vis=0.9):
    lm = [_LM(0.5, 0.5) for _ in range(33)]
    lm[23] = _LM(knee_x - 0.02, 0.30)
    lm[25] = _LM(knee_x, 0.55)
    lm[27] = _LM(knee_x, 0.85, ankle_vis)
    lm[24] = _LM(knee_x - 0.02, 0.30)
    lm[26] = _LM(knee_x, 0.55)
    lm[28] = _LM(knee_x, 0.85, ankle_vis)
    return lm


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_zero_poses_shows_status_and_does_not_retrack(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    video_path = str(tmp_path / "fix0.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    class _ZeroPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (np.zeros((48, 64, 3), dtype=np.uint8), [])

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_ZeroPoseEngine())

    dlg._on_fix_person_here()

    assert "no person" in dlg.status_var.get().lower()
    assert dlg._retrack_in_progress is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_one_pose_auto_resolves_and_retracks(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    video_path = str(tmp_path / "fix1.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]

    captured = {}

    class _OnePoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            captured["manual_seed"] = manual_seed
            captured["start_frame"] = start_frame
            progress_cb(1.0)
            n = 6 - start_frame
            return ([170.0] * n, [None] * n, 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6, landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_OnePoseEngine())
    dlg._frame_idx = 2

    dlg._on_fix_person_here()
    r.update()  # flush the self.after(0, ...) callback _start_retrack scheduled

    assert captured["start_frame"] == 2
    assert captured["manual_seed"] is not None
    assert dlg.angles == [0.0, 0.0, 170.0, 170.0, 170.0, 170.0]
    assert dlg._retrack_in_progress is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_two_poses_uses_person_picker_dialog(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import pendulastic_app as _app
    video_path = str(tmp_path / "fix2.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.4), _make_pose(0.6)]

    class _TwoPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            progress_cb(1.0)
            n = 5 - start_frame
            return ([99.0] * n, [None] * n, 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    class _StubPickerDialog:
        def __init__(self, *a, **kw):
            self.result = ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _StubPickerDialog)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_TwoPoseEngine())
    monkeypatch.setattr(dlg, "wait_window", lambda w: None)
    dlg._frame_idx = 1

    dlg._on_fix_person_here()
    r.update()  # flush the self.after(0, ...) callback _start_retrack scheduled

    assert dlg.angles == [0.0, 99.0, 99.0, 99.0, 99.0]
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_cancelled_picker_dialog_does_not_retrack(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import pendulastic_app as _app
    video_path = str(tmp_path / "fix3.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.4), _make_pose(0.6)]

    called = {"retrack": False}

    class _TwoPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, *a, **kw):
            called["retrack"] = True
            return ([], [], 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    class _CancelledPickerDialog:
        def __init__(self, *a, **kw):
            self.result = None
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _CancelledPickerDialog)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_TwoPoseEngine())
    monkeypatch.setattr(dlg, "wait_window", lambda w: None)

    dlg._on_fix_person_here()

    assert called["retrack"] is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_short_retrack_result_pads_not_leaves_stale(tmp_path, monkeypatch):
    """If run_offline_track returns fewer frames than expected, the tail
    must be padded (nan/None), never left as stale pre-fix landmarks --
    spec S4 point 1."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import math
    video_path = str(tmp_path / "fix4.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]

    class _ShortReturnEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            progress_cb(1.0)
            return ([170.0], [(None, None, (99.0, 99.0))], 30.0)  # short!

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    # (None, None, (-1.0, -1.0)) is a distinguishable-but-valid sentinel --
    # a bare string here would crash the constructor's own _redraw() call,
    # since _draw() unpacks hip/knee/ankle expecting None or a coordinate.
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 6,
        landmarks=[(None, None, (-1.0, -1.0))] * 6,
        fps=30.0, leg="right", engine=_ShortReturnEngine())
    dlg._frame_idx = 2

    dlg._on_fix_person_here()
    r.update()  # flush the self.after(0, ...) callback _start_retrack scheduled

    assert dlg.angles[2] == 170.0
    assert math.isnan(dlg.angles[3])
    assert math.isnan(dlg.angles[4])
    assert math.isnan(dlg.angles[5])
    assert dlg.landmarks[3] is None
    assert dlg.landmarks[4] is None
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_retrack_in_progress_blocks_a_second_fix_call(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "fix5.avi")
    _write_test_video(video_path, 4)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 4, landmarks=[None] * 4,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._retrack_in_progress = True

    dlg._on_fix_person_here()  # must no-op, not raise

    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_retrack_engine_failure_clears_in_progress_and_shows_status(tmp_path, monkeypatch):
    """A raising run_offline_track must not leave the dialog permanently
    stuck -- _retrack_in_progress must clear and the Fix button must
    re-enable, with a status message explaining the failure."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    video_path = str(tmp_path / "fix6.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]

    class _RaisingEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, *a, **kw):
            raise RuntimeError("decoder exploded")

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_RaisingEngine())

    dlg._on_fix_person_here()
    r.update()  # flush the self.after(0, ...) callback _on_retrack_failed needs

    assert dlg._retrack_in_progress is False
    assert dlg._btn_fix["state"] == "normal"
    assert "decoder exploded" in dlg.status_var.get()
    dlg.destroy()


class _CountingEngine:
    """Counts calls into engine methods so tests can assert the engine was
    never touched -- used for Finding 1's out-of-range guard."""
    def __init__(self):
        self.detect_calls = 0
        self.retrack_calls = 0

    def detect_people_at_frame(self, video_path, frame_index=0):
        self.detect_calls += 1
        return (None, [])

    def run_offline_track(self, *a, **kw):
        self.retrack_calls += 1
        return ([], [], 30.0)


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_frame_idx_beyond_angles_length_guards_out(tmp_path):
    """Finding 1: the scrub bar's total_frames comes from
    cv2.CAP_PROP_FRAME_COUNT, which can over-report vs len(self.angles)
    (from run_offline_track's read-until-failure loop stopping early on a
    mid-file decode hiccup). frame_idx can legitimately be >= len(angles).
    _on_fix_person_here must guard this before calling into the engine at
    all, and must leave a clear status message."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "fixoob.avi")
    _write_test_video(video_path, 3)
    r = _get_root()

    engine = _CountingEngine()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=engine)
    dlg._frame_idx = 5  # beyond the 3-length angles/landmarks arrays

    dlg._on_fix_person_here()

    assert engine.detect_calls == 0
    assert engine.retrack_calls == 0
    status = dlg.status_var.get().lower()
    assert "beyond" in status or "range" in status
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_two_poses_regrabs_after_picker_confirmed(tmp_path, monkeypatch):
    """Finding 3: PersonPickerDialog's own grab_set() (in its __init__)
    steals the modal grab from the review dialog underneath it, and Tk does
    not restore the previous grab when the picker is destroyed. After the
    picker interaction concludes (confirmed here), the review dialog must
    re-acquire its own grab or the panel underneath becomes clickable while
    the review dialog is still open."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import pendulastic_app as _app
    video_path = str(tmp_path / "fix7.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.4), _make_pose(0.6)]

    class _TwoPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            progress_cb(1.0)
            n = 5 - start_frame
            return ([99.0] * n, [None] * n, 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    class _StubPickerDialog:
        def __init__(self, *a, **kw):
            self.result = ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _StubPickerDialog)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_TwoPoseEngine())
    monkeypatch.setattr(dlg, "wait_window", lambda w: None)

    # Spy installed after construction, so the constructor's own grab_set()
    # call (Task 3's existing behavior) is not counted here -- this isolates
    # the re-grab that must happen after the picker closes.
    grab_calls = {"count": 0}
    original_grab_set = dlg.grab_set
    def _spy_grab_set():
        grab_calls["count"] += 1
        return original_grab_set()
    monkeypatch.setattr(dlg, "grab_set", _spy_grab_set)

    dlg._frame_idx = 1
    dlg._on_fix_person_here()
    r.update()

    assert grab_calls["count"] == 1
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_fix_person_here_two_poses_regrabs_after_picker_cancelled(tmp_path, monkeypatch):
    """Finding 3, cancelled variant: the re-grab must happen regardless of
    whether the user confirmed or cancelled the picker."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    import pendulastic_app as _app
    video_path = str(tmp_path / "fix8.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.4), _make_pose(0.6)]

    class _TwoPoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    class _CancelledPickerDialog:
        def __init__(self, *a, **kw):
            self.result = None
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _CancelledPickerDialog)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_TwoPoseEngine())
    monkeypatch.setattr(dlg, "wait_window", lambda w: None)

    grab_calls = {"count": 0}
    original_grab_set = dlg.grab_set
    def _spy_grab_set():
        grab_calls["count"] += 1
        return original_grab_set()
    monkeypatch.setattr(dlg, "grab_set", _spy_grab_set)

    dlg._on_fix_person_here()

    assert grab_calls["count"] == 1
    dlg.destroy()


# ---------------------------------------------------------------------------
# Corrections persistence: pure functions
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_video_fingerprint_reports_size_mtime_frame_count(tmp_path):
    from video_review_dialog import _video_fingerprint
    video_path = str(tmp_path / "fp.avi")
    _write_test_video(video_path, 7)

    fp = _video_fingerprint(video_path)

    assert fp["frame_count"] == 7
    assert fp["size"] == os.path.getsize(video_path)
    assert fp["mtime"] == os.path.getmtime(video_path)
    assert isinstance(fp["sha256"], str)
    assert len(fp["sha256"]) == 64


def test_video_fingerprint_sha256_matches_manual_hash(tmp_path):
    import hashlib
    from video_review_dialog import _video_fingerprint
    video_path = str(tmp_path / "fphash.avi")
    _write_test_video(video_path, 4)

    fp = _video_fingerprint(video_path)

    with open(video_path, "rb") as f:
        expected = hashlib.sha256(f.read()).hexdigest()
    assert fp["sha256"] == expected


def test_corrections_path_appends_leg_suffix():
    from video_review_dialog import _corrections_path
    assert (_corrections_path("/a/b/vid.avi", "left")
            == "/a/b/vid.avi.corrections.left.json")
    assert (_corrections_path("/a/b/vid.avi", "right")
            == "/a/b/vid.avi.corrections.right.json")


def test_corrections_path_differs_by_leg():
    """The whole point of per-leg paths: two legs of the same video must
    never resolve to the same sidecar file, or saving one destroys the
    other."""
    from video_review_dialog import _corrections_path
    assert (_corrections_path("/a/b/vid.avi", "left")
            != _corrections_path("/a/b/vid.avi", "right"))


def test_build_corrections_doc_shape():
    from video_review_dialog import (
        _build_corrections_doc, CORRECTIONS_SCHEMA_VERSION)
    fp = {"size": 1, "mtime": 2.0, "frame_count": 3}
    events = [{"type": "retrack", "start_frame": 0, "at": "t"}]
    angles = [1.0, float("nan")]
    landmarks = [None, ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))]

    doc = _build_corrections_doc(fp, events, angles, landmarks, "left",
                                 "test-tracker-v1")

    assert doc["schema_version"] == CORRECTIONS_SCHEMA_VERSION
    assert doc["video_fingerprint"] == fp
    assert doc["leg"] == "left"
    assert doc["events"] == events
    assert doc["corrected_angles"][0] == 1.0
    assert math.isnan(doc["corrected_angles"][1])
    assert doc["corrected_landmarks"][0] is None
    assert doc["corrected_landmarks"][1] == [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_then_load_corrections_round_trip(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _load_corrections)
    video_path = str(tmp_path / "rt.avi")
    _write_test_video(video_path, 4)
    fp = _video_fingerprint(video_path)
    events = [{"type": "exclude_range", "start_frame": 1, "end_frame": 2,
              "at": "t"}]
    angles = [1.0, float("nan"), float("nan"), 4.0]
    landmarks = [((0, 0), (0, 0), (0, 0)), None, None,
                 ((1, 1), (1, 1), (1, 1))]

    _save_corrections(video_path, fp, events, angles, landmarks, "left",
                      "test-tracker-v1")
    loaded = _load_corrections(video_path, "left")

    assert loaded is not None
    assert loaded["leg"] == "left"
    assert loaded["events"] == events
    assert loaded["corrected_angles"][0] == 1.0
    assert math.isnan(loaded["corrected_angles"][1])
    assert loaded["corrected_landmarks"][0] == [[0, 0], [0, 0], [0, 0]]
    assert loaded["corrected_landmarks"][1] is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_corrections_leaves_no_stray_tmp_file_on_success(tmp_path):
    """_save_corrections uses a uniquely-named temp file (not a fixed
    "<path>.tmp") so two writers saving the same video/leg concurrently
    can't collide on it. That temp file must always end up either
    os.replace()'d into the final path or removed on failure -- never left
    behind as orphaned litter next to the sidecar."""
    from video_review_dialog import _video_fingerprint, _save_corrections
    video_path = str(tmp_path / "notmp.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)

    _save_corrections(video_path, fp, [], [0.0] * 3, [None] * 3, "left",
                      "test-tracker-v1")

    leftovers = [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]
    assert leftovers == []


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_corrections_leaves_no_stray_tmp_file_on_failure(
        tmp_path, monkeypatch):
    """Mirror of the success-path check above, but for the failure path:
    a mid-write crash must clean up its own temp file, not just leave the
    original sidecar untouched."""
    import video_review_dialog as vrd
    from video_review_dialog import _video_fingerprint, _save_corrections
    video_path = str(tmp_path / "notmp2.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)

    def _boom(*a, **kw):
        raise OSError("disk full mid-write")
    monkeypatch.setattr(vrd.json, "dump", _boom)

    with pytest.raises(OSError):
        _save_corrections(video_path, fp, [], [0.0] * 3, [None] * 3, "left",
                          "test-tracker-v1")

    leftovers = [p for p in os.listdir(str(tmp_path)) if p.endswith(".tmp")]
    assert leftovers == []


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_corrections_atomic_write_preserves_original_on_failure(
        tmp_path, monkeypatch):
    """A crash/failure partway through writing the sidecar must never
    destroy a previously-saved valid one -- _save_corrections writes to a
    temp path and only os.replace()s it into place after the write fully
    succeeds, so a failure during json.dump (which happens before the
    temp file is complete, and strictly before os.replace is ever called)
    must leave the ORIGINAL file's content completely untouched."""
    import video_review_dialog as vrd
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _corrections_path)
    video_path = str(tmp_path / "atomic.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)

    # First, a real successful save -- this is the "previously valid"
    # sidecar that must survive the next, failing, save attempt.
    _save_corrections(video_path, fp, [{"type": "retrack", "start_frame": 0,
                                        "at": "t1"}],
                      [0.0] * 3, [None] * 3, "right", "test-tracker-v1")
    path = _corrections_path(video_path, "right")
    with open(path, encoding="utf-8") as f:
        original_content = f.read()
    assert original_content  # sanity: something was actually written

    def _boom(*a, **kw):
        raise OSError("disk full mid-write")
    monkeypatch.setattr(vrd.json, "dump", _boom)

    with pytest.raises(OSError):
        _save_corrections(video_path, fp, [{"type": "retrack",
                                            "start_frame": 1, "at": "t2"}],
                          [9.0] * 3, [None] * 3, "right", "test-tracker-v2")

    with open(path, encoding="utf-8") as f:
        assert f.read() == original_content  # untouched, not truncated


def test_backup_existing_sidecar_no_file_returns_none(tmp_path):
    from video_review_dialog import _backup_existing_sidecar
    path = str(tmp_path / "nope.json")
    assert _backup_existing_sidecar(path) is None


def test_backup_existing_sidecar_renames_aside(tmp_path):
    from video_review_dialog import _backup_existing_sidecar
    path = str(tmp_path / "existing.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"original": true}')
    backup_path = _backup_existing_sidecar(path)
    assert backup_path is not None
    assert not os.path.isfile(path)  # moved, not copied
    with open(backup_path, encoding="utf-8") as f:
        assert f.read() == '{"original": true}'


def test_backup_existing_sidecar_handles_malformed_content(tmp_path):
    # Not gated on being parseable JSON -- a garbage file must still be
    # preserved, not silently destroyed.
    from video_review_dialog import _backup_existing_sidecar
    path = str(tmp_path / "garbage.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json at all")
    backup_path = _backup_existing_sidecar(path)
    assert backup_path is not None
    with open(backup_path, encoding="utf-8") as f:
        assert f.read() == "{not valid json at all"


def test_backup_existing_sidecar_collision_retry(tmp_path, monkeypatch):
    # Two backups requested with the SAME _now_iso() (simulating same-
    # wall-clock-second saves) must not collide -- the second gets a
    # ".2" suffix instead of overwriting the first backup.
    import video_review_dialog as vrd
    monkeypatch.setattr(vrd, "_now_iso", lambda: "2026-08-20T00-00-00Z")

    path = str(tmp_path / "x.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("first")
    b1 = vrd._backup_existing_sidecar(path)

    with open(path, "w", encoding="utf-8") as f:
        f.write("second")
    b2 = vrd._backup_existing_sidecar(path)

    assert b1 != b2
    with open(b1, encoding="utf-8") as f:
        assert f.read() == "first"
    with open(b2, encoding="utf-8") as f:
        assert f.read() == "second"


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_corrections_backs_up_stale_sidecar_before_overwrite(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _corrections_path, _tracker_version)
    video_path = str(tmp_path / "stale.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    tv = _tracker_version()

    _save_corrections(video_path, fp, [], [1.0] * 3, [None] * 3, "left", tv)
    path = _corrections_path(video_path, "left")
    with open(path, encoding="utf-8") as f:
        first_save_content = f.read()

    _save_corrections(video_path, fp, [], [2.0] * 3, [None] * 3, "left", tv)

    backups = [p for p in os.listdir(str(tmp_path))
               if p.startswith(os.path.basename(path) + ".bak.")]
    assert len(backups) == 1
    with open(os.path.join(str(tmp_path), backups[0]), encoding="utf-8") as f:
        assert f.read() == first_save_content


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_when_no_sidecar(tmp_path):
    from video_review_dialog import _load_corrections
    video_path = str(tmp_path / "none.avi")
    _write_test_video(video_path, 3)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_malformed_json(tmp_path):
    from video_review_dialog import _load_corrections, _corrections_path
    video_path = str(tmp_path / "bad.avi")
    _write_test_video(video_path, 3)
    with open(_corrections_path(video_path, "right"), "w") as f:
        f.write("{not valid json")

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_wrong_schema_version(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _corrections_path, _load_corrections)
    video_path = str(tmp_path / "wrongver.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    doc = {"schema_version": 999, "video_fingerprint": fp, "leg": "right",
           "events": [], "corrected_angles": [0.0] * 3,
           "corrected_landmarks": [None] * 3}
    with open(_corrections_path(video_path, "right"), "w") as f:
        json.dump(doc, f)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_fingerprint_mismatch(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _load_corrections)
    import time as _time_test
    video_path = str(tmp_path / "changed.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    _save_corrections(video_path, fp, [], [0.0] * 3, [None] * 3, "right",
                      "test-tracker-v1")

    # Simulate the video having changed since the sidecar was saved -- a
    # different frame count makes size and frame_count both differ.
    _time_test.sleep(0.01)
    _write_test_video(video_path, 5)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_sha256_mismatch_alone(tmp_path):
    """The whole point of the content hash: size/mtime/frame_count can all
    still match (nothing else about the file changed) while the hash alone
    is wrong -- e.g. a corrupted sidecar, or (in principle) two different
    videos that coincidentally share size/mtime/frame_count. Only the hash
    catches this; a fingerprint check that ignored it would wrongly accept
    the sidecar."""
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _corrections_path,
        _load_corrections)
    video_path = str(tmp_path / "hashmismatch.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    _save_corrections(video_path, fp, [], [0.0] * 3, [None] * 3, "right",
                      "test-tracker-v1")

    with open(_corrections_path(video_path, "right"), encoding="utf-8") as f:
        doc = json.load(f)
    doc["video_fingerprint"]["sha256"] = "0" * 64
    with open(_corrections_path(video_path, "right"), "w", encoding="utf-8") as f:
        json.dump(doc, f)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_leg_mismatch(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _load_corrections)
    video_path = str(tmp_path / "legmismatch.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    _save_corrections(video_path, fp, [], [0.0] * 3, [None] * 3, "left",
                      "test-tracker-v1")

    assert _load_corrections(video_path, "right") is None
    assert _load_corrections(video_path, "left") is not None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_angles_landmarks_length_mismatch(
        tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _corrections_path, _load_corrections,
        CORRECTIONS_SCHEMA_VERSION)
    video_path = str(tmp_path / "lenmismatch.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    doc = {"schema_version": CORRECTIONS_SCHEMA_VERSION,
           "video_fingerprint": fp, "leg": "right", "events": [],
           "corrected_angles": [0.0, 0.0, 0.0],
           "corrected_landmarks": [None, None]}  # one short
    with open(_corrections_path(video_path, "right"), "w", encoding="utf-8") as f:
        json.dump(doc, f)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_frame_count_mismatch(tmp_path):
    """The angles/landmarks arrays can agree with EACH OTHER in length while
    still being the wrong length for the actual video (e.g. both length 1
    for a 5-frame video) -- that must be rejected too, not just a mismatch
    between the two arrays."""
    from video_review_dialog import (
        _video_fingerprint, _corrections_path, _load_corrections,
        CORRECTIONS_SCHEMA_VERSION)
    video_path = str(tmp_path / "framecountmismatch.avi")
    _write_test_video(video_path, 5)
    fp = _video_fingerprint(video_path)
    doc = {"schema_version": CORRECTIONS_SCHEMA_VERSION,
           "video_fingerprint": fp, "leg": "right", "events": [],
           "corrected_angles": [0.0],
           "corrected_landmarks": [None]}  # equal to each other, wrong vs. 5
    with open(_corrections_path(video_path, "right"), "w", encoding="utf-8") as f:
        json.dump(doc, f)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_non_numeric_angle(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _corrections_path, _load_corrections,
        CORRECTIONS_SCHEMA_VERSION)
    video_path = str(tmp_path / "badangle.avi")
    _write_test_video(video_path, 2)
    fp = _video_fingerprint(video_path)
    doc = {"schema_version": CORRECTIONS_SCHEMA_VERSION,
           "video_fingerprint": fp, "leg": "right", "events": [],
           "corrected_angles": [0.0, "not-a-number"],
           "corrected_landmarks": [None, None]}
    with open(_corrections_path(video_path, "right"), "w", encoding="utf-8") as f:
        json.dump(doc, f)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_non_numeric_landmark_coordinate(
        tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _corrections_path, _load_corrections,
        CORRECTIONS_SCHEMA_VERSION)
    video_path = str(tmp_path / "badcoord.avi")
    _write_test_video(video_path, 2)
    fp = _video_fingerprint(video_path)
    doc = {"schema_version": CORRECTIONS_SCHEMA_VERSION,
           "video_fingerprint": fp, "leg": "right", "events": [],
           "corrected_angles": [0.0, 0.0],
           "corrected_landmarks": [None, [["x", "y"], None, None]]}
    with open(_corrections_path(video_path, "right"), "w", encoding="utf-8") as f:
        json.dump(doc, f)

    assert _load_corrections(video_path, "right") is None


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_load_corrections_returns_none_on_malformed_landmark_entry(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _corrections_path, _load_corrections,
        CORRECTIONS_SCHEMA_VERSION)
    video_path = str(tmp_path / "badlm.avi")
    _write_test_video(video_path, 2)
    fp = _video_fingerprint(video_path)
    doc = {"schema_version": CORRECTIONS_SCHEMA_VERSION,
           "video_fingerprint": fp, "leg": "right", "events": [],
           "corrected_angles": [0.0, 0.0],
           # Valid landmark entries are 3-element [hip, knee, ankle] lists --
           # a 2-element entry fails to unpack inside _landmark_from_json.
           "corrected_landmarks": [None, [1.0, 2.0]]}
    with open(_corrections_path(video_path, "right"), "w", encoding="utf-8") as f:
        json.dump(doc, f)

    assert _load_corrections(video_path, "right") is None


def test_tracker_version_helper_includes_mp_model_basename():
    from video_review_dialog import _tracker_version
    import pendulastic_viewer as pv
    tv = _tracker_version()
    assert tv.startswith("_MPBatchTracker;model=")
    assert os.path.basename(pv._MP_MODEL) in tv


def test_build_corrections_doc_includes_tracker_version_and_schema_2():
    from video_review_dialog import _build_corrections_doc, CORRECTIONS_SCHEMA_VERSION
    doc = _build_corrections_doc({"size": 1}, [], [1.0], [None], "left", "tv-string")
    assert doc["schema_version"] == 2
    assert CORRECTIONS_SCHEMA_VERSION == 2
    assert doc["tracker_version"] == "tv-string"


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_then_load_round_trip_preserves_tracker_version(tmp_path):
    from video_review_dialog import (
        _video_fingerprint, _save_corrections, _load_corrections, _tracker_version)
    video_path = str(tmp_path / "tv.avi")
    _write_test_video(video_path, 3)
    fp = _video_fingerprint(video_path)
    tv = _tracker_version()
    _save_corrections(video_path, fp, [], [0.0] * 3, [None] * 3, "left", tv)
    loaded = _load_corrections(video_path, "left")
    assert loaded is not None
    assert loaded["tracker_version"] == tv


def test_apply_exclusion_sets_nan_and_none_in_range_forward():
    from video_review_dialog import _apply_exclusion
    angles = [0.0, 1.0, 2.0, 3.0, 4.0]
    landmarks = ["a", "b", "c", "d", "e"]

    new_angles, new_landmarks, event = _apply_exclusion(angles, landmarks, 1, 3)

    assert new_angles[0] == 0.0
    assert math.isnan(new_angles[1])
    assert math.isnan(new_angles[2])
    assert math.isnan(new_angles[3])
    assert new_angles[4] == 4.0
    assert new_landmarks == ["a", None, None, None, "e"]
    assert event == {"type": "exclude_range", "start_frame": 1,
                     "end_frame": 3, "at": event["at"]}


def test_apply_exclusion_handles_reverse_scrub_direction():
    from video_review_dialog import _apply_exclusion
    angles = [0.0, 1.0, 2.0, 3.0, 4.0]
    landmarks = [None] * 5

    new_angles, _, event = _apply_exclusion(angles, landmarks, 3, 1)

    assert new_angles[0] == 0.0
    assert math.isnan(new_angles[1])
    assert math.isnan(new_angles[2])
    assert math.isnan(new_angles[3])
    assert new_angles[4] == 4.0
    assert event["start_frame"] == 1
    assert event["end_frame"] == 3


def test_apply_exclusion_clamps_out_of_bounds_indices():
    from video_review_dialog import _apply_exclusion
    angles = [0.0, 1.0, 2.0]
    landmarks = [None] * 3

    new_angles, _, event = _apply_exclusion(angles, landmarks, -5, 99)

    assert all(math.isnan(a) for a in new_angles)
    assert event["start_frame"] == 0
    assert event["end_frame"] == 2


def test_apply_exclusion_does_not_mutate_inputs():
    from video_review_dialog import _apply_exclusion
    angles = [0.0, 1.0, 2.0]
    landmarks = ["a", "b", "c"]

    _apply_exclusion(angles, landmarks, 0, 1)

    assert angles == [0.0, 1.0, 2.0]
    assert landmarks == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Corrections persistence: dialog-level behavior
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_exclude_range_sets_nan_and_none_and_records_event(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "excl.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[float(i) for i in range(6)],
        landmarks=[((0, 0), (0, 0), (0, 0))] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg._frame_idx = 1
    dlg._on_exclude_from_here()
    dlg._frame_idx = 3
    dlg._on_exclude_to_here()

    assert dlg.angles[0] == 0.0
    assert math.isnan(dlg.angles[1])
    assert math.isnan(dlg.angles[2])
    assert math.isnan(dlg.angles[3])
    assert dlg.angles[4] == 4.0
    assert dlg.landmarks[1] is None
    assert dlg.landmarks[2] is None
    assert dlg.landmarks[3] is None
    assert dlg.landmarks[4] is not None
    assert dlg._events[-1]["type"] == "exclude_range"
    assert dlg._events[-1]["start_frame"] == 1
    assert dlg._events[-1]["end_frame"] == 3
    assert dlg._pending_exclude_start is None
    assert "excluded" in dlg.status_var.get().lower()
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_exclude_range_works_in_reverse_scrub_direction(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "exclrev.avi")
    _write_test_video(video_path, 6)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[float(i) for i in range(6)],
        landmarks=[None] * 6,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg._frame_idx = 4
    dlg._on_exclude_from_here()
    dlg._frame_idx = 2
    dlg._on_exclude_to_here()

    assert dlg.angles[1] == 1.0
    assert math.isnan(dlg.angles[2])
    assert math.isnan(dlg.angles[3])
    assert math.isnan(dlg.angles[4])
    assert dlg.angles[5] == 5.0
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_exclude_to_here_without_pending_start_is_a_noop(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "exclnoop.avi")
    _write_test_video(video_path, 4)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0, 1.0, 2.0, 3.0], landmarks=[None] * 4,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg._on_exclude_to_here()

    assert dlg.angles == [0.0, 1.0, 2.0, 3.0]
    assert dlg._events == []
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_exclude_ignored_while_retrack_in_progress(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "exclbusy.avi")
    _write_test_video(video_path, 4)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0, 1.0, 2.0, 3.0], landmarks=[None] * 4,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._retrack_in_progress = True

    dlg._on_exclude_from_here()
    dlg._on_exclude_to_here()

    assert dlg._pending_exclude_start is None
    assert dlg.angles == [0.0, 1.0, 2.0, 3.0]
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_corrections_writes_sidecar_and_reports_status(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _corrections_path
    video_path = str(tmp_path / "save1.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())

    dlg._on_save_corrections()

    assert os.path.isfile(_corrections_path(video_path, "right"))
    assert "saved" in dlg.status_var.get().lower()
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_save_corrections_failure_shows_status_not_raise(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    video_path = str(tmp_path / "savefail.avi")
    _write_test_video(video_path, 3)
    r = _get_root()

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())

    def _boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(vrd, "_save_corrections", _boom)

    dlg._on_save_corrections()  # must not raise

    status = dlg.status_var.get().lower()
    assert "disk full" in status or "failed" in status
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_retrack_records_event_in_log(tmp_path, monkeypatch):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import video_review_dialog as vrd
    video_path = str(tmp_path / "evretrack.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    fake_frame = np.zeros((48, 64, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]

    class _OnePoseEngine(_FakeEngine):
        def detect_people_at_frame(self, video_path, frame_index=0):
            return (fake_frame, fake_poses)
        def run_offline_track(self, path, progress_cb, leg="right",
                               collect_landmarks=False, manual_seed=None,
                               start_frame=0):
            progress_cb(1.0)
            n = 5 - start_frame
            return ([170.0] * n, [None] * n, 30.0)

    monkeypatch.setattr(vrd.threading, "Thread", _SyncThread)

    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_OnePoseEngine())
    dlg._frame_idx = 2

    dlg._on_fix_person_here()
    r.update()  # flush the self.after(0, ...) callback _on_retrack_done needs

    assert dlg._events[-1]["type"] == "retrack"
    assert dlg._events[-1]["start_frame"] == 2
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_new_dialog_auto_loads_saved_corrections(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "reload.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    dlg1 = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg1._frame_idx = 1
    dlg1._on_exclude_from_here()
    dlg1._frame_idx = 2
    dlg1._on_exclude_to_here()
    dlg1._on_save_corrections()
    dlg1.destroy()

    dlg2 = AnnotatedVideoReviewDialog(
        r, video_path, angles=[9.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())

    assert dlg2.angles[0] == 0.0
    assert math.isnan(dlg2.angles[1])
    assert math.isnan(dlg2.angles[2])
    assert "loaded" in dlg2.status_var.get().lower()
    dlg2.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_new_dialog_ignores_stale_sidecar_after_video_changed(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    import time as _time_test
    video_path = str(tmp_path / "stale.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    dlg1 = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg1._frame_idx = 1
    dlg1._on_exclude_from_here()
    dlg1._frame_idx = 2
    dlg1._on_exclude_to_here()
    dlg1._on_save_corrections()
    dlg1.destroy()

    _time_test.sleep(0.01)
    _write_test_video(video_path, 7)  # video "changed" -- new frame count

    dlg2 = AnnotatedVideoReviewDialog(
        r, video_path, angles=[9.0] * 7, landmarks=[None] * 7,
        fps=30.0, leg="right", engine=_FakeEngine())

    assert dlg2.angles == [9.0] * 7  # sidecar ignored, fresh track kept
    assert "changed" in dlg2.status_var.get().lower()
    dlg2.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_second_dialog_different_leg_does_not_cross_contaminate(tmp_path):
    """Reviewing the same video for the OTHER leg is a routine clinical
    workflow (both legs get tracked from the same recording), not an edge
    case -- a sidecar saved for one leg must never be silently applied to a
    session tracking the other leg's angles/landmarks."""
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "twoleg.avi")
    _write_test_video(video_path, 5)
    r = _get_root()

    dlg_left = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="left", engine=_FakeEngine())
    dlg_left._frame_idx = 1
    dlg_left._on_exclude_from_here()
    dlg_left._frame_idx = 2
    dlg_left._on_exclude_to_here()
    dlg_left._on_save_corrections()
    dlg_left.destroy()

    dlg_right = AnnotatedVideoReviewDialog(
        r, video_path, angles=[9.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())

    assert dlg_right.angles == [9.0] * 5  # left leg's exclusion not applied
    assert "other leg" in dlg_right.status_var.get().lower()

    # The destructive-overwrite bug this test guards against: saving the
    # RIGHT leg's corrections must not touch the LEFT leg's sidecar at all
    # (per-leg paths, not a shared file) -- exercise a DIFFERENT exclusion
    # range here so a bug that clobbers the left file would be observable.
    dlg_right._frame_idx = 3
    dlg_right._on_exclude_from_here()
    dlg_right._frame_idx = 4
    dlg_right._on_exclude_to_here()
    dlg_right._on_save_corrections()
    dlg_right.destroy()

    # The left-leg session still auto-loads its OWN original saved
    # corrections (frames 1-2 excluded) -- not overwritten, not merged with,
    # not replaced by the right leg's later save (frames 3-4).
    dlg_left_reopened = AnnotatedVideoReviewDialog(
        r, video_path, angles=[9.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="left", engine=_FakeEngine())
    assert math.isnan(dlg_left_reopened.angles[1])
    assert math.isnan(dlg_left_reopened.angles[2])
    assert dlg_left_reopened.angles[3] == 0.0
    assert dlg_left_reopened.angles[4] == 0.0
    dlg_left_reopened.destroy()

    # And the right leg's own corrections are exactly what it saved.
    dlg_right_reopened = AnnotatedVideoReviewDialog(
        r, video_path, angles=[9.0] * 5, landmarks=[None] * 5,
        fps=30.0, leg="right", engine=_FakeEngine())
    assert dlg_right_reopened.angles[1] == 9.0
    assert dlg_right_reopened.angles[2] == 9.0
    assert math.isnan(dlg_right_reopened.angles[3])
    assert math.isnan(dlg_right_reopened.angles[4])
    dlg_right_reopened.destroy()


# ---------------------------------------------------------------------------
# Pin state reconstruction from events
# ---------------------------------------------------------------------------

def test_current_pins_from_events_empty_events_no_pins():
    from video_review_dialog import _current_pins_from_events
    assert _current_pins_from_events([]) == {}


def test_current_pins_from_events_accumulates_pin_set():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 3.0, "y": 4.0, "at": "t2"},
    ]
    assert _current_pins_from_events(events) == {5: (1.0, 2.0), 10: (3.0, 4.0)}


def test_current_pins_from_events_later_pin_set_overwrites_earlier():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 5, "x": 9.0, "y": 9.0, "at": "t2"},
    ]
    assert _current_pins_from_events(events) == {5: (9.0, 9.0)}


def test_current_pins_from_events_specific_pin_clear_removes_one():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 3.0, "y": 4.0, "at": "t2"},
        {"type": "pin_clear", "frame": 5, "at": "t3"},
    ]
    assert _current_pins_from_events(events) == {10: (3.0, 4.0)}


def test_current_pins_from_events_null_frame_clears_all():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "pin_set", "frame": 10, "x": 3.0, "y": 4.0, "at": "t2"},
        {"type": "pin_clear", "frame": None, "at": "t3"},
        {"type": "pin_set", "frame": 20, "x": 5.0, "y": 6.0, "at": "t4"},
    ]
    assert _current_pins_from_events(events) == {20: (5.0, 6.0)}


def test_current_pins_from_events_ignores_other_event_types():
    from video_review_dialog import _current_pins_from_events
    events = [
        {"type": "pin_set", "frame": 5, "x": 1.0, "y": 2.0, "at": "t1"},
        {"type": "retrack", "start_frame": 0, "at": "t2"},
        {"type": "exclude_range", "start_frame": 0, "end_frame": 3, "at": "t3"},
    ]
    assert _current_pins_from_events(events) == {5: (1.0, 2.0)}


# ---------------------------------------------------------------------------
# Anchor derivation from frame landmarks
# ---------------------------------------------------------------------------

def test_anchor_from_frame_valid_returns_hip_knee_shank_len():
    from video_review_dialog import _anchor_from_frame
    landmarks = [((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))]
    anchor = _anchor_from_frame(landmarks, 0)
    assert anchor is not None
    hip, knee, shank_len = anchor
    assert hip == (0.0, 1.0)
    assert knee == (0.0, 0.0)
    assert math.isclose(shank_len, 1.0, abs_tol=1e-9)


def test_anchor_from_frame_out_of_range_returns_none():
    from video_review_dialog import _anchor_from_frame
    assert _anchor_from_frame([], 0) is None
    assert _anchor_from_frame([((0, 1), (0, 0), (1, 0))], 5) is None
    assert _anchor_from_frame([((0, 1), (0, 0), (1, 0))], -1) is None


def test_anchor_from_frame_none_landmark_returns_none():
    from video_review_dialog import _anchor_from_frame
    assert _anchor_from_frame([None], 0) is None


def test_anchor_from_frame_missing_joint_returns_none():
    from video_review_dialog import _anchor_from_frame
    assert _anchor_from_frame([(None, (0.0, 0.0), (1.0, 0.0))], 0) is None
    assert _anchor_from_frame([((0.0, 1.0), None, (1.0, 0.0))], 0) is None
    assert _anchor_from_frame([((0.0, 1.0), (0.0, 0.0), None)], 0) is None


def test_anchor_from_frame_non_finite_coordinate_returns_none():
    from video_review_dialog import _anchor_from_frame
    landmarks = [((0.0, 1.0), (0.0, 0.0), (float("nan"), 0.0))]
    assert _anchor_from_frame(landmarks, 0) is None


def test_anchor_from_frame_degenerate_shank_len_returns_none():
    from video_review_dialog import _anchor_from_frame
    # knee and ankle at the same point -- zero-radius arc is undefined.
    landmarks = [((0.0, 1.0), (0.0, 0.0), (0.0, 0.0))]
    assert _anchor_from_frame(landmarks, 0) is None


# ---------------------------------------------------------------------------
# Pin Ankle button: click-to-place with coordinate conversion
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_display_scale_is_one_when_frame_narrower_than_max_width(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "narrow.avi")
    _write_test_video(video_path, 3, w=64, h=48)  # well under _MAX_DISPLAY_WIDTH
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    assert dlg._display_scale == 1.0
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_pin_ankle_toggle_arms_and_disarms(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "arm.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    assert dlg._pin_armed is False
    dlg._on_pin_ankle_toggle()
    assert dlg._pin_armed is True
    dlg._on_pin_ankle_toggle()
    assert dlg._pin_armed is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_pin_ankle_toggle_noop_during_retrack(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog
    video_path = str(tmp_path / "arm2.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._retrack_in_progress = True
    dlg._on_pin_ankle_toggle()
    assert dlg._pin_armed is False
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_image_click_noop_during_retrack(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events

    class _FakeEvent:
        x, y = 10, 10

    video_path = str(tmp_path / "arm3.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3,
        landmarks=[((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._pin_armed = True
    dlg._retrack_in_progress = True

    dlg._on_image_click(_FakeEvent())

    assert _current_pins_from_events(dlg._events) == {}
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_image_click_ignored_while_unarmed(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog

    class _FakeEvent:
        x, y = 10, 10

    video_path = str(tmp_path / "click0.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3,
        landmarks=[((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._on_image_click(_FakeEvent())
    from video_review_dialog import _current_pins_from_events
    assert _current_pins_from_events(dlg._events) == {}
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_image_click_places_pin_converted_by_display_scale(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events

    class _FakeEvent:
        x, y = 20, 30

    video_path = str(tmp_path / "click1.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3,
        landmarks=[((0.0, 1.0), (0.0, 0.0), (1.0, 0.0))] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._display_scale = 0.5  # simulate a downscaled display
    dlg._frame_idx = 1
    dlg._pin_armed = True

    dlg._on_image_click(_FakeEvent())

    pins = _current_pins_from_events(dlg._events)
    assert pins == {1: (40.0, 60.0)}  # 20/0.5, 30/0.5
    assert dlg._pin_armed is False  # auto-disarms after one placement
    dlg.destroy()


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_image_click_rejected_at_frame_with_no_valid_knee(tmp_path):
    from video_review_dialog import AnnotatedVideoReviewDialog, _current_pins_from_events

    class _FakeEvent:
        x, y = 10, 10

    video_path = str(tmp_path / "click2.avi")
    _write_test_video(video_path, 3)
    r = _get_root()
    dlg = AnnotatedVideoReviewDialog(
        r, video_path, angles=[0.0] * 3, landmarks=[None] * 3,
        fps=30.0, leg="right", engine=_FakeEngine())
    dlg._pin_armed = True

    dlg._on_image_click(_FakeEvent())

    assert _current_pins_from_events(dlg._events) == {}
    assert "no valid tracked knee" in dlg.status_var.get().lower()
    assert dlg._pin_armed is False
    dlg.destroy()
