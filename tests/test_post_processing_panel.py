# tests/test_post_processing_panel.py
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

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
