# tests/test_person_picker_dialog.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk
import numpy as np

_root_window = None

def _get_root():
    global _root_window
    if _root_window is None:
        _root_window = tk.Tk()
        _root_window.withdraw()
    return _root_window


class _LM:
    def __init__(self, x, y, visibility=1.0):
        self.x, self.y, self.visibility = x, y, visibility


def _make_pose(knee_x=0.5, ankle_vis=1.0):
    lm = [_LM(0.5, 0.5) for _ in range(33)]
    lm[23] = _LM(knee_x - 0.02, 0.30)
    lm[25] = _LM(knee_x, 0.55)
    lm[27] = _LM(knee_x, 0.85, ankle_vis)
    lm[24] = _LM(knee_x - 0.02, 0.30)
    lm[26] = _LM(knee_x, 0.55)
    lm[28] = _LM(knee_x, 0.85, ankle_vis)
    return lm


def test_dialog_scales_down_wide_frame_and_maps_click_back():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    wide_frame = np.zeros((720, 1800, 3), dtype=np.uint8)   # wider than 900px
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, wide_frame, poses, "right")

    assert dlg._scale == 900 / 1800

    display_x, display_y = 450, 360
    mapped_x = display_x / dlg._scale
    mapped_y = display_y / dlg._scale
    assert abs(mapped_x - 900) < 1.0
    assert abs(mapped_y - 720) < 1.0

    dlg.destroy()


def test_dialog_does_not_scale_narrow_frame():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    narrow_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, narrow_frame, poses, "right")
    assert dlg._scale == 1.0
    dlg.destroy()


def test_dialog_click_resolves_and_sets_result():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5, ankle_vis=0.9)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    class _FakeEvent:
        x = int(0.5 * 640)
        y = int(0.55 * 480)

    dlg._on_click(_FakeEvent())
    assert dlg.result is not None
    hip, knee, ankle = dlg.result
    assert ankle is not None
    assert not dlg.winfo_exists()   # dialog auto-destroys on a resolved click


def test_dialog_click_with_low_ankle_visibility_stays_open():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5, ankle_vis=0.1)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    class _FakeEvent:
        x = int(0.5 * 640)
        y = int(0.55 * 480)

    dlg._on_click(_FakeEvent())
    assert dlg.result is None
    assert dlg.winfo_exists()
    dlg.destroy()


def test_try_next_frame_advances_index_and_redraws(monkeypatch):
    from pendulastic_app import PersonPickerDialog
    import pendulastic_app as _app
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    calls = []
    new_frame = np.ones((480, 640, 3), dtype=np.uint8)
    new_poses = [_make_pose(0.3), _make_pose(0.7)]

    def _fake_detect(self, video_path, frame_index=0):
        calls.append(frame_index)
        return new_frame, new_poses

    monkeypatch.setattr(_app.BiomechanicalEngine, "detect_people_at_frame",
                         _fake_detect)

    dlg._on_try_next_frame()

    assert calls == [15]
    assert dlg._frame_index == 15
    assert dlg._poses == new_poses
    dlg.destroy()


def test_try_next_frame_disables_button_at_end_of_clip(monkeypatch):
    from pendulastic_app import PersonPickerDialog
    import pendulastic_app as _app
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")

    def _fake_detect_end(self, video_path, frame_index=0):
        return None, []

    monkeypatch.setattr(_app.BiomechanicalEngine, "detect_people_at_frame",
                         _fake_detect_end)

    dlg._on_try_next_frame()

    assert str(dlg.btn_next_frame["state"]) == "disabled"
    assert "end of clip" in dlg._status_var.get().lower()
    dlg.destroy()


def test_cancel_leaves_result_none():
    from pendulastic_app import PersonPickerDialog
    r = _get_root()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    poses = [_make_pose(0.5)]
    dlg = PersonPickerDialog(r, "fake.mp4", 0, frame, poses, "right")
    dlg.destroy()
    assert dlg.result is None
