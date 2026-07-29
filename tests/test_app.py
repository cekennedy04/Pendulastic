# tests/test_app.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk


def test_app_starts_with_mode_select_visible():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        assert app._mode_select.winfo_ismapped(), "ModeSelectView must be visible on startup"
        assert not app._acq.winfo_ismapped()
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
    """swing_angle_deg=0 (no rotation from zero) must read 180° (full extension)."""
    import pendulastic_app as _m, types, math
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "swing_angle_deg": 0.0,
            "angles": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    from pendulastic_app import BiomechanicalEngine
    engine = BiomechanicalEngine("imu")
    assert engine.get_live_angle() == 180.0, "No swing from zero must map to 180°"

    # 90° of swing from zero → 90° clinical angle
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "swing_angle_deg": 90.0,
            "angles": {"pitch": 90.0, "roll": 0.0, "yaw": 0.0, "paired": True},
            "distal":   {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": True, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    assert engine.get_live_angle() == 90.0, "90° swing must map to 90° clinical angle"


def test_get_live_angle_returns_nan_before_zero(monkeypatch):
    """Before zero() is called, swing_angle_deg is NaN → get_live_angle returns NaN."""
    import pendulastic_app as _m, types, math
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", types.SimpleNamespace(
        get_state=lambda: {
            "swing_angle_deg": float("nan"),
            "angles": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0, "paired": False},
            "distal":   {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
            "proximal": {"connected": False, "ip": "", "packets": 0, "hz": 0.0},
        }
    ))
    from pendulastic_app import BiomechanicalEngine
    engine = BiomechanicalEngine("imu")
    result = engine.get_live_angle()
    assert math.isnan(result), f"Expected NaN before zero, got {result}"


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


def test_enter_live_mode_shows_acquisition():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_live_mode()
        app.update()
        assert app._acq.winfo_ismapped()
        assert not app._mode_select.winfo_ismapped()
        assert app._state == "idle"
    finally:
        app.destroy()


def test_upload_back_to_select_restores_mode_select(monkeypatch):
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        # Simulate being in upload_meta state
        app._mode_select.pack_forget()
        app._upload_meta.pack(fill="both", expand=True)
        app._state = "upload_meta"
        app.update()
        app._upload_back_to_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._upload_meta.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()


def test_upload_back_to_select_blocked_during_processing():
    from pendulastic_app import App
    app = App()
    try:
        app._state = "upload_processing"
        app._upload_back_to_select()
        # Should not change state
        assert app._state == "upload_processing"
    finally:
        app.destroy()


def test_on_back_to_mode_select_resets_state():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_live_mode()
        app.update()
        app._rec_angles    = {"imu": [1.0, 2.0]}
        app._active_sources = ["imu"]
        app.on_back_to_mode_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._acq.winfo_ismapped()
        assert app._state == "mode_select"
        assert app._rec_angles == {}
        assert app._active_sources == []
    finally:
        app.destroy()


def test_upload_csv_curve_style_exists():
    from pendulastic_app import PostProcessingPanel
    assert "upload_csv" in PostProcessingPanel._CURVE_STYLES
    assert "upload_csv" in PostProcessingPanel._PT_SOURCE_PRIORITY
    style = PostProcessingPanel._CURVE_STYLES["upload_csv"]
    assert style["color"] == "#0891B2"
    assert style["label"] == "CSV Upload"


def test_run_csv_analysis_reads_datamanager_format(tmp_path, monkeypatch):
    """_run_csv_analysis must parse time_s + knee_angle_deg columns."""
    import csv as _csv_mod, os
    from pendulastic_app import App, DataManager
    # Write a minimal DataManager-format CSV
    p = tmp_path / "test_trial.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = _csv_mod.writer(f)
        w.writerow(["frame", "time_s", "knee_angle_deg",
                    "pid", "leg", "ms_status", "trial", "methodology"])
        w.writerow([0, "0.0000", "170.000", "P1", "Right", "MS", "1", "upload_csv"])
        w.writerow([1, "0.0333", "165.000", "P1", "Right", "MS", "1", "upload_csv"])
        w.writerow([2, "0.0667", "160.000", "P1", "Right", "MS", "1", "upload_csv"])

    captured = {}

    app = App()
    try:
        # Patch _transition_to_review to capture what was passed
        def fake_transition(source_angles, meta):
            captured["source_angles"] = source_angles
            captured["meta"] = meta
        monkeypatch.setattr(app, "_transition_to_review", fake_transition)
        # Patch DataManager.save_trial to avoid writing files during test
        monkeypatch.setattr(DataManager, "save_trial",
                            classmethod(lambda cls, *a, **kw: ""))

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._run_csv_analysis(str(p), meta)
        app.update()  # process the after(0, ...) callback
    finally:
        app.destroy()

    assert "upload_csv" in captured.get("source_angles", {})
    angles = captured["source_angles"]["upload_csv"]
    assert len(angles) == 3
    assert abs(angles[0] - 170.0) < 0.01
