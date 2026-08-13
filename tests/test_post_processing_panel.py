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
    def on_back_to_trial_list(self): pass


def test_panel_instantiates():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()


def test_metrics_frame_has_single_border():
    """Regression: _metrics_frame had both the default tk.LabelFrame relief
    AND a highlightthickness ring, producing a visible double outline. Only
    the highlightthickness ring (relief='flat', bd=0) should remain, matching
    other cards in the app."""
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    assert str(p._metrics_frame.cget("relief")) == "flat"
    assert int(p._metrics_frame.cget("bd")) == 0
    assert int(p._metrics_frame.cget("highlightthickness")) == 1


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


class _SyncThread:
    """Runs target() synchronously in start() -- makes _on_upload_video's
    background thread deterministic for testing without a real thread."""
    def __init__(self, target=None, daemon=None, **kw):
        self._target = target
    def start(self):
        self._target()


def test_on_upload_video_zero_people_uses_automatic_fallback(monkeypatch, tmp_path):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    video_path = str(tmp_path / "fake.mp4")
    monkeypatch.setattr(_app.filedialog, "askopenfilename", lambda **kw: video_path)
    monkeypatch.setattr(_app.threading, "Thread", _SyncThread)
    monkeypatch.setattr(
        _app.BiomechanicalEngine, "detect_people_at_frame",
        lambda self, path, frame_index=0: (None, []))

    captured = {}
    def _fake_run_offline_track(self, path, progress_cb, leg="right",
                                 collect_landmarks=False, manual_seed=None):
        captured["manual_seed"] = manual_seed
        progress_cb(1.0)
        return ([170.0] * 5, [None] * 5, 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    class _PassthroughReviewDialog:
        def __init__(self, parent, video_path, angles, landmarks, fps, leg, engine):
            self.angles = angles
            self.landmarks = landmarks
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "AnnotatedVideoReviewDialog", _PassthroughReviewDialog)
    monkeypatch.setattr(p, "wait_window", lambda dlg: None)

    p._on_upload_video()
    r.update()

    assert captured["manual_seed"] is None
    assert "hpe_upload" in p._source_angles


def test_on_upload_video_one_person_computes_manual_seed(monkeypatch, tmp_path):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    import numpy as np
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    video_path = str(tmp_path / "fake.mp4")
    monkeypatch.setattr(_app.filedialog, "askopenfilename", lambda **kw: video_path)
    monkeypatch.setattr(_app.threading, "Thread", _SyncThread)

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

    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    fake_poses = [_make_pose(0.5)]
    monkeypatch.setattr(
        _app.BiomechanicalEngine, "detect_people_at_frame",
        lambda self, path, frame_index=0: (fake_frame, fake_poses))

    captured = {}
    def _fake_run_offline_track(self, path, progress_cb, leg="right",
                                 collect_landmarks=False, manual_seed=None):
        captured["manual_seed"] = manual_seed
        progress_cb(1.0)
        return ([170.0] * 5, [None] * 5, 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    class _PassthroughReviewDialog:
        def __init__(self, parent, video_path, angles, landmarks, fps, leg, engine):
            self.angles = angles
            self.landmarks = landmarks
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "AnnotatedVideoReviewDialog", _PassthroughReviewDialog)
    monkeypatch.setattr(p, "wait_window", lambda dlg: None)

    p._on_upload_video()
    r.update()

    assert captured["manual_seed"] is not None
    hip, knee, ankle = captured["manual_seed"]
    assert ankle is not None


def test_on_upload_video_two_people_cancelled_dialog_aborts_upload(monkeypatch, tmp_path):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    import numpy as np
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    video_path = str(tmp_path / "fake.mp4")
    monkeypatch.setattr(_app.filedialog, "askopenfilename", lambda **kw: video_path)
    monkeypatch.setattr(_app.threading, "Thread", _SyncThread)

    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    fake_poses = [["pose1"], ["pose2"]]
    monkeypatch.setattr(
        _app.BiomechanicalEngine, "detect_people_at_frame",
        lambda self, path, frame_index=0: (fake_frame, fake_poses))

    class _CancelledDialog:
        def __init__(self, *a, **kw):
            self.result = None
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "PersonPickerDialog", _CancelledDialog)
    monkeypatch.setattr(p, "wait_window", lambda dlg: None)

    called = {"run_offline_track": False}
    def _fake_run_offline_track(self, *a, **kw):
        called["run_offline_track"] = True
        return ([], [], 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    p._on_upload_video()
    r.update()

    assert called["run_offline_track"] is False
    assert "cancel" in p.status_var.get().lower()


def test_set_back_context_true_relabels_button():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    p.set_back_context(from_trial_list=True)
    assert p.btn_new_trial["text"] == "← Back to Trials"


def test_set_back_context_false_relabels_button():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    p.set_back_context(from_trial_list=True)
    p.set_back_context(from_trial_list=False)
    assert p.btn_new_trial["text"] == "← New Trial"


def test_back_button_routes_to_trial_list_when_from_trial_list():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_back_to_trial_list(self): calls.append("trial_list")
        def on_new_trial(self): calls.append("new_trial")
    p = PostProcessingPanel(r, C())
    p.pack(fill="both", expand=True); r.update()
    p.set_back_context(from_trial_list=True)
    p._on_new_trial()
    assert calls == ["trial_list"]


def test_back_button_routes_to_new_trial_by_default():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_back_to_trial_list(self): calls.append("trial_list")
        def on_new_trial(self): calls.append("new_trial")
    p = PostProcessingPanel(r, C())
    p.pack(fill="both", expand=True); r.update()
    p._on_new_trial()
    assert calls == ["new_trial"]


def test_on_upload_video_opens_review_dialog_and_uses_corrected_results(monkeypatch, tmp_path):
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    video_path = str(tmp_path / "fake.mp4")
    monkeypatch.setattr(_app.filedialog, "askopenfilename", lambda **kw: video_path)
    monkeypatch.setattr(_app.threading, "Thread", _SyncThread)
    monkeypatch.setattr(
        _app.BiomechanicalEngine, "detect_people_at_frame",
        lambda self, path, frame_index=0: (None, []))

    def _fake_run_offline_track(self, path, progress_cb, leg="right",
                                 collect_landmarks=False, manual_seed=None,
                                 start_frame=0):
        progress_cb(1.0)
        return ([170.0] * 5, [None] * 5, 30.0)
    monkeypatch.setattr(_app.BiomechanicalEngine, "run_offline_track",
                         _fake_run_offline_track)

    opened = {}
    class _StubReviewDialog:
        def __init__(self, parent, video_path, angles, landmarks, fps, leg, engine):
            opened["video_path"] = video_path
            opened["angles"] = angles
            self.angles = [999.0] * len(angles)   # simulate a user correction
            self.landmarks = landmarks
        def destroy(self):
            pass
    monkeypatch.setattr(_app, "AnnotatedVideoReviewDialog", _StubReviewDialog)
    monkeypatch.setattr(p, "wait_window", lambda dlg: None)

    p._on_upload_video()
    r.update()

    assert opened["video_path"] == video_path
    assert p._source_angles["hpe_upload"] == [999.0] * 5


def test_add_hpe_overlay_dialog_construction_failure_preserves_run(monkeypatch):
    """Finding 2: if AnnotatedVideoReviewDialog's __init__ raises (e.g. the
    video file was moved/deleted between upload and track completion, or a
    PhotoImage/_draw failure on the constructor's own first _redraw()), the
    multi-minute tracking run must not be lost. The original angles from
    the run must still be saved to _source_angles, and the status message
    must explain the dialog failure rather than being silently swallowed by
    Tk's callback-exception handling."""
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)
    p._video_path = "fake_video.mp4"

    class _RaisingReviewDialog:
        def __init__(self, *a, **kw):
            raise RuntimeError("video file moved")
    monkeypatch.setattr(_app, "AnnotatedVideoReviewDialog", _RaisingReviewDialog)

    fake_angles = [175.0, 160.0, 145.0] * 20
    fake_landmarks = [((160, 60), (160, 120), (160, 200))] * 60

    p._add_hpe_overlay(fake_angles, fake_landmarks, fps=30.0, engine=object())
    r.update()

    assert p._source_angles["hpe_upload"] == fake_angles
    status = p.status_var.get().lower()
    assert "video review unavailable" in status
    assert "video file moved" in status


def test_add_hpe_overlay_skips_dialog_when_no_landmarks(monkeypatch):
    """Existing no-landmarks callers (e.g. a failed track) must not try to
    open a review dialog at all."""
    import pendulastic_app as _app
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True)

    opened = {"called": False}
    class _StubReviewDialog:
        def __init__(self, *a, **kw):
            opened["called"] = True
    monkeypatch.setattr(_app, "AnnotatedVideoReviewDialog", _StubReviewDialog)

    p._add_hpe_overlay([170.0] * 3, landmarks=None, fps=30.0, engine=None)

    assert opened["called"] is False
    assert p._source_angles["hpe_upload"] == [170.0] * 3
