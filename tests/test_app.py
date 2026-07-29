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
        # Directly simulate the review state without going through load_trial
        # (PostProcessingPanel.load_trial signature changes in Task 5 — bypass here)
        app._state = "review"
        app._acq.pack_forget()
        app._post.pack(fill="both", expand=True)
        app.update()
        app.on_new_trial()
        app.update()
        assert int(app._acq.trial_var.get()) == 3
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_on_source_changed_does_not_crash(monkeypatch):
    import pendulastic_app as _m, types
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu",
        types.SimpleNamespace(
            start=lambda: None, stop=lambda: None,
            get_state=lambda: {
                "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
                "proximal": {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
                "angles":   {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": False},
            }))
    from pendulastic_app import App
    app = App()
    try:
        app.on_source_changed(["rgb"])
        app.on_source_changed(["imu"])
        app.on_source_changed(["imu", "optitrack"])
        app.on_source_changed([])
        app.update()
    finally:
        app.destroy()


def test_get_live_angle_maps_to_180_convention(monkeypatch):
    """Full extension (pitch=0 after zero) must read 180°; flexion increases pitch, lowers clinical angle."""
    import pendulastic_app as _m, types
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "angles": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    from pendulastic_app import BiomechanicalEngine
    engine = BiomechanicalEngine("imu")
    assert engine.get_live_angle() == 180.0, "Horizontal extension (pitch=0) must map to 180°"

    # Flexion: pitch increases → clinical angle decreases
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "angles": {"pitch": 45.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    assert engine.get_live_angle() == 135.0, "45° pitch must map to 135° clinical"


def test_root_window_is_resizable_and_wide():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        min_w, min_h = app.minsize()
        assert min_w >= 700, f"minsize width should be ≥700, got {min_w}"
        assert min_h >= 680, f"minsize height should be ≥680, got {min_h}"
    finally:
        app.destroy()
