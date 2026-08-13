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
