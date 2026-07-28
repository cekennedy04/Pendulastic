# tests/test_post_processing_panel.py
import os, sys, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

# Global root window shared across tests to avoid tkinter re-initialization issues
_root_window = None

def _get_root():
    global _root_window
    if _root_window is None:
        _root_window = tk.Tk()
        _root_window.withdraw()
    return _root_window

def _cleanup_root():
    global _root_window
    if _root_window is not None:
        try:
            _root_window.destroy()
        except:
            pass
        _root_window = None

class _Ctrl:
    def on_new_trial(self): pass


def test_panel_instantiates():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    try:
        p = PostProcessingPanel(r, _Ctrl())
        p.pack(fill="both", expand=True); r.update()
    finally:
        pass


def test_load_trial_sets_title():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    try:
        p = PostProcessingPanel(r, _Ctrl())
        p.pack(fill="both", expand=True)
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
                "trial": 1, "methodology": "imu"}
        p.load_trial([170.0] * 60, 30.0, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
        r.update()
        assert "PID_P1" in p.title_var.get()
    finally:
        pass


def test_load_trial_populates_mas():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    try:
        p = PostProcessingPanel(r, _Ctrl())
        p.pack(fill="both", expand=True)
        # Generate a damped sinusoid starting from 180° (fully extended)
        # with oscillation down to ~140° (40° initial drop), then damping.
        # This satisfies: A0_raw >= 3.0, and has >= 40 finite frames.
        angles = [180.0 - 40.0 * (1 - math.exp(-0.03 * i)) * (0.7 + 0.3 * math.sin(0.3 * i))
                  for i in range(120)]
        meta   = {"pid": "P1", "leg": "Right", "ms_status": "MS",
                  "trial": 1, "methodology": "rgb"}
        p.load_trial(angles, 30.0, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
        r.update()
        # MAS should be populated (not the placeholder "—")
        assert p.mas_var.get() != "—"
    finally:
        pass
