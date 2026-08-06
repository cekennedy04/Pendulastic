# tests/test_post_processing_panel.py
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk
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


class _Ctrl:
    def on_new_trial(self): pass
    def on_back_to_mode_select(self): pass


def test_panel_instantiates():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()


def test_load_trial_sets_title():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["imu"]}
    # New signature: source_angles dict, fps, metadata, base_filename
    p.load_trial({"imu": [170.0] * 60}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert "PID_P1" in p.title_var.get()


def test_load_trial_populates_mas():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    angles = [180.0 - 40.0 * (1 - math.exp(-0.03 * i)) * (0.7 + 0.3 * math.sin(0.3 * i))
              for i in range(120)]
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["rgb"]}
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert p.mas_var.get() != "—"


def test_load_trial_multi_source_stores_both():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["imu", "optitrack"]}
    p.load_trial({"imu": [170.0] * 30, "optitrack": [168.0] * 30},
                 30.0, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert "imu" in p._source_angles
    assert "optitrack" in p._source_angles


def test_upload_video_button_exists():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    # The upload button must be an attribute named btn_upload_video
    assert hasattr(p, "btn_upload_video")
    assert p.btn_upload_video.winfo_exists()


def test_add_hpe_overlay_adds_to_source_angles():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["imu"]}
    p.load_trial({"imu": [170.0] * 60}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    fake_angles = [175.0, 160.0, 145.0] * 20
    p._add_hpe_overlay(fake_angles, fps=30.0)
    r.update()
    assert "hpe_upload" in p._source_angles
    assert p._source_angles["hpe_upload"] == fake_angles


def test_add_hpe_overlay_empty_updates_status_not_crash():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    p._add_hpe_overlay([])  # empty list — should not crash
    r.update()
    assert "no pose" in p.status_var.get().lower() or "hpe" in p.status_var.get().lower()


def test_load_trial_imu_source_does_not_request_detrend(monkeypatch):
    """IMU trials are now freshly auto-tared and rate-verified, so PT scoring
    must treat the raw signal as authoritative (matching what
    imu_calibration_tuner.py already does) rather than linearly detrending
    it -- detrending before release-detection corrupted A0 and silently
    discarded valid trials."""
    import pendulastic_app as _m
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    calls = []
    def fake_compute_pt_params(t, arr, *a, **kw):
        calls.append(kw.get("detrend"))
        return None
    monkeypatch.setattr(_m, "compute_pt_params", fake_compute_pt_params)

    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["imu"]}
    p.load_trial({"imu": [170.0] * 60}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert calls == [False]


def test_load_trial_populates_plot_annotations():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    angles = [180.0 - 40.0 * (1 - math.exp(-0.03 * i)) * (0.7 + 0.3 * math.sin(0.3 * i))
              for i in range(120)]
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["rgb"]}
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert p._last_pt_params is not None
    assert len(p._plot_annots) > 0


def test_plot_all_curves_resets_annotations_list():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    angles = [180.0 - 40.0 * (1 - math.exp(-0.03 * i)) * (0.7 + 0.3 * math.sin(0.3 * i))
              for i in range(120)]
    meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
            "trial": 1, "sources": ["rgb"]}
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    first_annots = list(p._plot_annots)
    assert len(first_annots) > 0
    # Reloading clears + redraws; stale artist objects from the first pass
    # must not linger in the new list.
    p.load_trial({"rgb": angles}, 30.0, meta,
                 "PID_P1_LEG_Right_MS_TRIAL_1.csv")
    r.update()
    assert p._plot_annots is not first_annots


def test_export_video_button_exists_and_starts_disabled():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    assert hasattr(p, "btn_export_video")
    assert str(p.btn_export_video["state"]) == "disabled"


def test_add_hpe_overlay_with_landmarks_enables_export_button():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    p._video_path = "fake_video.mp4"
    fake_angles = [175.0, 160.0, 145.0] * 20
    fake_landmarks = [((160, 60), (160, 120), (160, 200))] * 60
    p._add_hpe_overlay(fake_angles, fake_landmarks, fps=30.0)
    r.update()
    assert p._hpe_landmarks == fake_landmarks
    assert str(p.btn_export_video["state"]) == "normal"


def test_add_hpe_overlay_without_landmarks_leaves_export_button_disabled():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    fake_angles = [175.0, 160.0, 145.0] * 20
    p._add_hpe_overlay(fake_angles, fps=30.0)   # landmarks defaults to None
    r.update()
    assert str(p.btn_export_video["state"]) == "disabled"


@pytest.mark.skipif(not _CV2_OK, reason="cv2 not installed")
def test_export_annotated_worker_writes_video_file(tmp_path, monkeypatch):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    import numpy as np

    video_path = str(tmp_path / "src.avi")
    out = _cv2_test.VideoWriter(
        video_path, _cv2_test.VideoWriter_fourcc(*"XVID"),
        30.0, (320, 240))
    for _ in range(5):
        out.write(np.zeros((240, 320, 3), dtype=np.uint8))
    out.release()

    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    monkeypatch.setattr(_app.messagebox, "showinfo", lambda *a, **kw: None)
    monkeypatch.setattr(_app.messagebox, "showerror", lambda *a, **kw: None)

    hip, kne, ank = (160.0, 60.0), (160.0, 120.0), (160.0, 200.0)
    snap = {
        "path": video_path,
        "fps": 30.0,
        "angles": [150.0, 152.0, 148.0, 151.0, 149.0],
        "landmarks": [(hip, kne, ank)] * 5,
    }
    out_path = str(tmp_path / "src_annotated.avi")

    p._export_annotated_worker(snap, out_path)
    r.update()

    assert os.path.exists(out_path)
    check = _cv2_test.VideoCapture(out_path)
    frame_count = int(check.get(_cv2_test.CAP_PROP_FRAME_COUNT))
    check.release()
    assert frame_count == 5
    assert "saved" in p.status_var.get().lower()
