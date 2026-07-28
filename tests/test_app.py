# tests/test_app.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk


def test_app_starts_with_acquisition_visible():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_on_new_trial_increments_trial_and_returns_to_acquisition():
    from pendulastic_app import App
    app = App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("2")
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS",
                "trial": 2, "methodology": "imu"}
        app._transition_to_review("PID_P1_LEG_Right_MS_TRIAL_2.csv",
                                   [170.0] * 30, meta)
        app.update()
        app.on_new_trial()
        app.update()
        assert int(app._acq.trial_var.get()) == 3
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_on_methodology_changed_does_not_crash(monkeypatch):
    import pendulastic_app as _m, types
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu",
        types.SimpleNamespace(
            start=lambda: None, stop=lambda: None,
            get_state=lambda: {
                "distal":   {"pitch": 0.0, "roll": 0.0, "yaw": 0.0},
                "proximal": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0},
            }))
    from pendulastic_app import App
    app = App()
    try:
        app.on_methodology_changed("rgb")
        app.on_methodology_changed("imu")
        app.on_methodology_changed("optitrack")
        app.update()
    finally:
        app.destroy()
