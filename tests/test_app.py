# tests/test_app.py
import csv, os, sys
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
                            lambda *a, **kw: None)

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._run_csv_analysis(str(p), meta)
        app.update()  # process the after(0, ...) callback
    finally:
        app.destroy()

    assert "upload_csv" in captured.get("source_angles", {})
    angles = captured["source_angles"]["upload_csv"]
    assert len(angles) == 3
    assert abs(angles[0] - 170.0) < 0.01




def test_imu_poll_worker_uses_configured_ema_alpha(monkeypatch):
    import pendulastic_app as _m
    import imu_calibration_config as _cfgmod
    monkeypatch.setattr(_cfgmod, "load_config",
                        lambda: {**_cfgmod.DEFAULT_CONFIG, "ema_alpha": 0.9})
    # Re-import-equivalent: the worker reads the config fresh via
    # imu_calibration_config.load_config() each time it starts, not a cached
    # module-level constant, so patching load_config() is sufficient.
    app = _m.App()
    try:
        app._engine = _m.BiomechanicalEngine("imu")
        app._imu_poll_stop.clear()
        import threading, time as _time
        t = threading.Thread(target=app._imu_poll_worker, daemon=True)
        t.start()
        _time.sleep(0.15)
        app._imu_poll_stop.set()
        t.join(timeout=1.0)
        # No assertion on numeric output here (depends on live/absent IMU
        # hardware) — this test's purpose is only to confirm the worker
        # doesn't crash when reading ema_alpha from a monkeypatched config.
    finally:
        app._imu_poll_stop.set()
        app.destroy()




def test_start_imu_recording_opens_raw_log(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1,
                "sources": ["imu"]}
        app._start_imu_recording(meta)
        expected_raw_path = os.path.join(
            str(tmp_path), "PID_P1_LEG_Right_MS_TRIAL_1_imu_raw.jsonl")
        assert _m._imu.stop_raw_log() == expected_raw_path
        app._imu_poll_stop.set()
    finally:
        app._imu_poll_stop.set()
        app.destroy()




def test_tick_shows_low_gyro_rate_warning(monkeypatch):
    """A gyro rate below MIN_USABLE_HZ must surface a warning that takes
    priority over the flex-axis armed/captured status -- unreliable AHRS
    integration matters more than which tracking state it's in."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "proximal": {"connected": False, "hz": 0.0},
            "distal":   {"connected": True, "hz": 3.0},
            "flex_axis_captured": True,   # must be masked by the rate warning
            "flex_axis_armed": False,
        })
        app._tick()
        assert "3 Hz" in app._acq.lbl_method_status.cget("text")
        assert "update" in app._acq.lbl_method_status.cget("text").lower()
    finally:
        app.destroy()




def test_tick_shows_flex_axis_status_when_rate_is_healthy(monkeypatch):
    """Once the rate is above MIN_USABLE_HZ, the existing flex-axis label
    behavior must be unaffected by the new check."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "proximal": {"connected": False, "hz": 0.0},
            "distal":   {"connected": True, "hz": 100.0},
            "flex_axis_captured": True,
            "flex_axis_armed": False,
        })
        app._tick()
        assert "Axis locked" in app._acq.lbl_method_status.cget("text")
    finally:
        app.destroy()




def test_run_imu_tuning_rewrites_csv_when_config_passes(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import imu_calibration_tuner as _tuner
    import numpy as np
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))

    raw_path = tmp_path / "trial_raw.jsonl"
    raw_path.write_text('{"t": 0, "role": "distal", "sensor": "gyro", "v": [0,0,0], "phone_ts_ms": 0}\n',
                        encoding="utf-8")

    monkeypatch.setattr(_tuner, "tune_and_persist", lambda raw, source_trial="", **kw: {
        "params": {"beta": 0.08, "ema_alpha": 0.3,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 0.4, "passes": True,
    })
    # replay_trial's real contract always emits a leading NaN (see its
    # docstring / test_replay_trial_first_tick_is_nan_rest_are_finite) --
    # include one here so this test actually exercises _run_imu_tuning's
    # own finite-filtering rather than relying on an unrealistic all-finite
    # mock return value.
    monkeypatch.setattr(_tuner, "replay_trial", lambda raw, params: (
        np.array([0.0, 0.05, 0.1, 0.15]),
        np.array([np.nan, 180.0, 179.0, 178.0])))

    app = _m.App()
    try:
        meta = {"pid": "P2", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._pending_review = {"imu": [1.0, 2.0, 3.0]}
        csv_filename = _m.DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
        csv_path = _m.DataManager.save_trial(csv_filename, [1.0, 2.0, 3.0], meta, source="imu")

        result_holder = {}
        def _capture(source_angles, m, **kw):
            result_holder["source_angles"] = source_angles
        app._transition_to_review = _capture

        app._run_imu_tuning(str(raw_path), csv_path, csv_filename, meta)
        # _run_imu_tuning schedules the transition via self.after(0, ...) --
        # exactly the real production path (see the Note below) -- so the
        # Tk event loop must be pumped once before the callback has run.
        app.update()

        # The leading NaN tick must be dropped, not saved/displayed --
        # DataManager.save_trial formats angles as f"{a:.3f}", so an
        # unfiltered NaN would write a literal "nan" into the trial CSV.
        assert result_holder["source_angles"]["imu"] == [180.0, 179.0, 178.0]
        with open(csv_path, encoding="utf-8") as f:
            content = f.read()
        assert "179.000" in content
        assert "nan" not in content.lower()
    finally:
        app.destroy()




def test_run_imu_tuning_falls_back_when_no_config_passes(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import imu_calibration_tuner as _tuner
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))

    raw_path = tmp_path / "trial_raw.jsonl"
    raw_path.write_text('{"t": 0, "role": "distal", "sensor": "gyro", "v": [0,0,0], "phone_ts_ms": 0}\n',
                        encoding="utf-8")
    monkeypatch.setattr(_tuner, "tune_and_persist", lambda raw, source_trial="", **kw: {
        "params": {"beta": 0.08, "ema_alpha": 0.3,
                   "flex_axis_capture": True, "gravity_seed": True},
        "penalty": 99.0, "passes": False,
    })

    app = _m.App()
    try:
        meta = {"pid": "P3", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._pending_review = {"imu": [1.0, 2.0, 3.0]}
        csv_filename = _m.DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
        csv_path = _m.DataManager.save_trial(csv_filename, [1.0, 2.0, 3.0], meta, source="imu")

        result_holder = {}
        app._transition_to_review = lambda source_angles, m, **kw: result_holder.update(
            source_angles=source_angles)

        app._run_imu_tuning(str(raw_path), csv_path, csv_filename, meta)
        app.update()

        assert result_holder["source_angles"]["imu"] == [1.0, 2.0, 3.0]
    finally:
        app.destroy()




def test_run_imu_tuning_never_raises_on_missing_raw_log(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P4", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._pending_review = {"imu": [1.0, 2.0]}
        csv_filename = _m.DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
        csv_path = _m.DataManager.save_trial(csv_filename, [1.0, 2.0], meta, source="imu")

        result_holder = {}
        app._transition_to_review = lambda source_angles, m, **kw: result_holder.update(
            source_angles=source_angles)

        app._run_imu_tuning(str(tmp_path / "does_not_exist.jsonl"), csv_path, csv_filename, meta)
        app.update()
        assert result_holder["source_angles"]["imu"] == [1.0, 2.0]
    finally:
        app.destroy()




def test_transition_to_review_shows_saved_confirmation_for_imu(monkeypatch):
    """A live recording that included IMU must get an unmissable
    confirmation naming the actual saved CSV filename."""
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo",
                        lambda title, msg: shown.append((title, msg)))
    app = _m.App()
    try:
        meta = {"pid": "P9", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._transition_to_review({"imu": [1.0, 2.0]}, meta, from_recording=True)
        app.update()
        assert len(shown) == 1
        title, msg = shown[0]
        assert title == "Recording Saved"
        assert "PID_P9_LEG_Right_MS_TRIAL_1_imu.csv" in msg
        assert _m.DataManager.DATA_DIR in msg
    finally:
        app.destroy()


def test_transition_to_review_shows_saved_confirmation_for_rgb(monkeypatch):
    """A live RGB recording must name both the angles CSV and the video
    file -- these are two separate files a clinician needs to find."""
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo",
                        lambda title, msg: shown.append((title, msg)))
    app = _m.App()
    try:
        meta = {"pid": "P9", "leg": "Right", "ms_status": "MS", "trial": 2}
        app._transition_to_review({"rgb": [1.0, 2.0]}, meta, from_recording=True)
        app.update()
        assert len(shown) == 1
        _, msg = shown[0]
        assert "PID_P9_LEG_Right_MS_TRIAL_2_rgb.csv" in msg
        assert "PID_P9_LEG_Right_MS_TRIAL_2.avi" in msg
    finally:
        app.destroy()


def test_transition_to_review_no_confirmation_when_not_from_recording(monkeypatch):
    """The upload-CSV/upload-video-file review paths process an
    already-existing file rather than saving a new one, so they must not
    default into claiming 'Recording Saved'."""
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo",
                        lambda title, msg: shown.append((title, msg)))
    app = _m.App()
    try:
        meta = {"pid": "P9", "leg": "Right", "ms_status": "MS", "trial": 3}
        app._transition_to_review({"upload_csv": [1.0, 2.0]}, meta)
        app.update()
        assert shown == []
    finally:
        app.destroy()


def test_transition_to_review_no_confirmation_for_optitrack_only(monkeypatch):
    """OptiTrack's take is Motive's own file, not something this app
    writes via DataManager.save_trial -- an OptiTrack-only trial must not
    claim a save location it doesn't actually control."""
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo",
                        lambda title, msg: shown.append((title, msg)))
    app = _m.App()
    try:
        meta = {"pid": "P9", "leg": "Right", "ms_status": "MS", "trial": 4}
        app._transition_to_review({"optitrack": []}, meta, from_recording=True)
        app.update()
        assert shown == []
    finally:
        app.destroy()


def test_transition_to_review_multi_trial_mode_skips_post_panel(monkeypatch):
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app = _m.App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("1")
        app._acq._multi_trial_var.set(True)
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "processing",
            "meta": meta, "source_angles": None, "fps": None,
            "base_filename": None, "file_paths": [],
        }]
        app._acq.pack(fill="both", expand=True)
        app.update()
        app._transition_to_review({"imu": [1.0, 2.0]}, meta, from_recording=True)
        app.update()
        assert shown == []
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
        assert app._state == "idle"
    finally:
        app.destroy()


def test_finish_trial_multi_mode_updates_entry_and_increments_trial():
    from pendulastic_app import App
    app = App()
    try:
        app._acq.trial_var.set("1")
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "processing",
            "meta": meta, "source_angles": None, "fps": None,
            "base_filename": None, "file_paths": [],
        }]
        app._finish_trial_multi_mode(
            {"imu": [1.0, 2.0]}, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
        app.update()
        entry = app._session_trials[0]
        assert entry["status"] == "saved"
        assert entry["source_angles"] == {"imu": [1.0, 2.0]}
        assert entry["base_filename"] == "PID_P1_LEG_Right_MS_TRIAL_1.csv"
        assert int(app._acq.trial_var.get()) == 2
    finally:
        app.destroy()


def test_transition_to_review_single_trial_mode_unchanged(monkeypatch):
    """Toggle off must still force the review screen + confirmation,
    exactly as before this feature existed."""
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app = _m.App()
    try:
        meta = {"pid": "P9", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._transition_to_review({"imu": [1.0, 2.0]}, meta, from_recording=True)
        app.update()
        assert len(shown) == 1
        assert app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_app_creates_camera_session_when_cv2_available():
    import pendulastic_app as _m
    app = _m.App()
    try:
        if _m._CV2_AVAIL:
            assert app._camera is not None
        else:
            assert app._camera is None
    finally:
        app.destroy()




def test_on_rescan_cameras_populates_dropdown_and_opens_first(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        fake_cams = [
            {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"},
        ]
        opened = []
        monkeypatch.setattr(_m, "enumerate_cameras", lambda: fake_cams)
        monkeypatch.setattr(app._camera, "open", lambda cam: opened.append(cam) or True)
        app.on_rescan_cameras()
        assert list(app._acq.drop_cam["values"]) == ["Camera 0 (MSMF)", _m.PHONE_CAMERA_LABEL]
        assert opened == [fake_cams[0]]
    finally:
        app.destroy()




def test_on_rescan_cameras_with_no_cameras_found(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        monkeypatch.setattr(_m, "enumerate_cameras", lambda: [])
        app.on_rescan_cameras()
        # on_rescan_cameras() always appends the phone entry when
        # _PPS_AVAIL, even with zero USB cameras found -- the phone camera
        # doesn't depend on USB enumeration, so "no USB cameras" must not
        # mean "no recording source available."
        assert list(app._acq.drop_cam["values"]) == [_m.PHONE_CAMERA_LABEL]
        assert app._acq._camera_live is False
    finally:
        app.destroy()




def test_on_camera_selected_opens_the_matching_camera(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        fake_cams = [
            {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"},
            {"index": 1, "backend": 700, "backend_name": "MSMF", "label": "Camera 1 (MSMF)"},
        ]
        app._known_cameras = fake_cams
        opened = []
        monkeypatch.setattr(app._camera, "open", lambda cam: opened.append(cam) or True)
        app.on_camera_selected("Camera 1 (MSMF)")
        assert opened == [fake_cams[1]]
    finally:
        app.destroy()




def test_on_camera_disabled_closes_session_and_hides_viewer_window(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        closed = []
        monkeypatch.setattr(app._camera, "close", lambda: closed.append(True))
        app._acq.set_camera_live(True)
        app.on_camera_disabled()
        assert closed == [True]
        assert app._acq._viewer_window.state() == "withdrawn"
    finally:
        app.destroy()




def test_camera_status_callback_shows_and_hides_viewer_window():
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._on_camera_status("live")
        app.update()   # process the self.after(0, ...) callback
        assert app._acq._viewer_window.state() != "withdrawn"
        app._on_camera_status("lost")
        app.update()
        assert app._acq._viewer_window.state() == "withdrawn"
    finally:
        app.destroy()




def test_camera_frame_callback_queues_preview_frame():
    import pendulastic_app as _m
    import numpy as np
    app = _m.App()
    try:
        app._pose_estimator = None
        frame = np.zeros((4, 4, 3), dtype="uint8")
        app._on_camera_frame(frame)
        queued = app._preview_queue.get_nowait()
        assert queued is frame
    finally:
        app.destroy()




def test_start_rgb_recording_attaches_writer_without_opening_new_capture(tmp_path, monkeypatch):
    import pendulastic_app as _m
    if not _m._CV2_AVAIL:
        return   # nothing to test without OpenCV installed
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        # Simulate an already-open, pre-warmed camera (as Rescan would leave it).
        app._camera.active = {"index": 0, "backend": 700, "backend_name": "MSMF",
                              "label": "Camera 0 (MSMF)"}
        app._camera._frame_size = (64, 48)
        attached = []
        monkeypatch.setattr(app._camera, "attach_writer", lambda w: attached.append(w))
        opened_new_capture = []
        monkeypatch.setattr(_m._cv2, "VideoCapture",
                            lambda *a, **kw: opened_new_capture.append(a) or None)
        # Fake the writer too, so this test never depends on a real codec
        # being available in the environment.
        created_writers = []
        class _FakeWriter:
            pass
        monkeypatch.setattr(_m._cv2, "VideoWriter",
                            lambda *a, **kw: created_writers.append(a) or _FakeWriter())
        monkeypatch.setattr(_m._cv2, "VideoWriter_fourcc", lambda *a: None)

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._start_rgb_recording(meta)

        assert len(attached) == 1, "must attach a writer to the existing CameraSession"
        assert isinstance(attached[0], _FakeWriter)
        assert opened_new_capture == [], "must NOT open a new cv2.VideoCapture"
    finally:
        app.destroy()




def test_start_rgb_recording_errors_when_no_camera_selected(monkeypatch):
    import pendulastic_app as _m
    if not _m._CV2_AVAIL:
        return
    app = _m.App()
    shown = []
    monkeypatch.setattr(_m.messagebox, "showerror",
                        lambda title, msg: shown.append((title, msg)))
    try:
        assert app._camera.active is None
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._start_rgb_recording(meta)
        assert shown, "must surface an error when no camera is active"
        assert not hasattr(app, "_rgb_writer") or app._rgb_writer is None
    finally:
        app.destroy()




def test_start_rgb_recording_no_camera_removes_rgb_from_active_sources_and_clears_video_path(monkeypatch):
    """Regression: previously the early-return path left self._video_path
    pointing at a PREVIOUS trial's video file (or "" on the very first
    trial), and on_stop()'s per-source loop had no way to know RGB never
    actually started, so it would re-process stale/wrong video as this
    trial's RGB result. Now _start_rgb_recording must remove "rgb" from
    _active_sources on failure so on_stop()'s loop skips RGB entirely."""
    import pendulastic_app as _m
    if not _m._CV2_AVAIL:
        return
    app = _m.App()
    monkeypatch.setattr(_m.messagebox, "showerror", lambda *a, **kw: None)
    try:
        # Simulate a prior trial's video path still hanging around.
        app._video_path = "C:/data/P1_trial1_rgb.avi"
        app._active_sources = ["rgb"]
        assert app._camera.active is None   # no camera selected

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 2}
        app._start_rgb_recording(meta)

        assert "rgb" not in app._active_sources, \
            "RGB must be removed from active sources so on_stop() skips it"
        assert app._video_path == "", \
            "stale video path from a previous trial must not survive a failed start"

        # Simulate on_stop()'s per-source loop directly: it must find nothing
        # to process for "rgb" now that it's gone from _active_sources.
        processed = []
        for src in app._active_sources:
            if src == "rgb":
                processed.append(src)
        assert processed == [], "on_stop() must not process RGB after a failed start"
    finally:
        app.destroy()




def test_start_rgb_recording_no_cv2_removes_rgb_from_active_sources(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    monkeypatch.setattr(_m, "_CV2_AVAIL", False)
    monkeypatch.setattr(_m.messagebox, "showerror", lambda *a, **kw: None)
    try:
        app._video_path = "C:/data/P1_trial1_rgb.avi"
        app._active_sources = ["rgb"]
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 2}
        app._start_rgb_recording(meta)
        assert "rgb" not in app._active_sources
        assert app._video_path == ""
    finally:
        app.destroy()




def test_on_camera_selected_ignored_while_recording(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._known_cameras = [
            {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"},
        ]
        opened = []
        monkeypatch.setattr(app._camera, "open", lambda cam: opened.append(cam) or True)
        app._state = "recording"
        app.on_camera_selected("Camera 0 (MSMF)")
        assert opened == [], "must not switch cameras mid-recording"
    finally:
        app.destroy()




def test_on_rescan_cameras_ignored_while_recording(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        rescanned = []
        monkeypatch.setattr(app._camera, "rescan", lambda: rescanned.append(True) or [])
        app._state = "recording"
        app.on_rescan_cameras()
        assert rescanned == [], "must not rescan cameras mid-recording"
    finally:
        app.destroy()




def test_stop_rgb_recording_detaches_writer_but_leaves_camera_live(monkeypatch):
    import pendulastic_app as _m
    if not _m._CV2_AVAIL:
        return
    app = _m.App()
    try:
        class _FakeWriter:
            def __init__(self):
                self.released = False
            def release(self):
                self.released = True

        writer = _FakeWriter()
        monkeypatch.setattr(app._camera, "detach_writer", lambda: writer)
        closed = []
        monkeypatch.setattr(app._camera, "close", lambda: closed.append(True))

        app._stop_rgb_recording()

        assert writer.released is True
        assert closed == [], "camera capture must stay open/live across trials"
    finally:
        app.destroy()




def test_rgb_cap_and_rgb_thread_attributes_no_longer_exist():
    """Regression: the old open-fresh-per-trial machinery must be fully removed."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        assert not hasattr(app, "_rgb_cap")
        assert not hasattr(app, "_rgb_thread")
        assert not hasattr(app, "_rgb_stop")
    finally:
        app.destroy()




def test_on_close_detaches_and_releases_writer_before_closing_camera(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    order = []
    if app._camera is not None:
        class _FakeWriter:
            def release(self):
                order.append("writer_released")
        writer = _FakeWriter()
        monkeypatch.setattr(app._camera, "detach_writer", lambda: order.append("detach") or writer)
        monkeypatch.setattr(app._camera, "close", lambda: order.append("camera_closed"))
    monkeypatch.setattr(_m, "_IMU_AVAIL", False)
    app.on_close()
    if app._camera is not None:
        assert order == ["detach", "writer_released", "camera_closed"]


def test_on_rescan_cameras_always_appends_phone_entry(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        monkeypatch.setattr(_m, "enumerate_cameras", lambda: [])
        monkeypatch.setattr(app._camera, "close", lambda: None)
        app.on_rescan_cameras()
        assert list(app._acq.drop_cam["values"])[-1] == _m.PHONE_CAMERA_LABEL
    finally:
        app.destroy()


def test_selecting_phone_entry_swaps_camera_to_phone_session(monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        app._known_cameras = [_m.PHONE_CAMERA_ENTRY]
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "open",
            lambda self, cam: (setattr(self, "active", cam) or True))
        monkeypatch.setattr(
            "pendulastic_phone_server.get_all_local_ips", lambda: ["192.168.1.50"], raising=False)
        app.on_camera_selected(_m.PHONE_CAMERA_LABEL)
        assert isinstance(app._camera, camera_utils.PhoneCameraSession)
        assert app._acq._phone_pairing_frame.winfo_manager() == "pack"
    finally:
        app.destroy()


def test_selecting_usb_entry_after_phone_swaps_back_to_camera_session(monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        fake_usb = {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"}
        app._known_cameras = [fake_usb, _m.PHONE_CAMERA_ENTRY]
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "open",
            lambda self, cam: (setattr(self, "active", cam) or True))
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "close", lambda self: setattr(self, "active", None))
        monkeypatch.setattr(
            "pendulastic_phone_server.get_all_local_ips", lambda: ["192.168.1.50"], raising=False)
        app.on_camera_selected(_m.PHONE_CAMERA_LABEL)
        assert isinstance(app._camera, camera_utils.PhoneCameraSession)

        opened = []
        monkeypatch.setattr(
            camera_utils.CameraSession, "open", lambda self, cam: opened.append(cam) or True)
        app.on_camera_selected("Camera 0 (MSMF)")
        assert isinstance(app._camera, camera_utils.CameraSession)
        assert opened == [fake_usb]
        assert app._acq._phone_pairing_frame.winfo_manager() == ""
    finally:
        app.destroy()


def test_on_camera_disabled_closes_whichever_session_type_is_active(monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    app = _m.App()
    try:
        app._known_cameras = [_m.PHONE_CAMERA_ENTRY]
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "open",
            lambda self, cam: (setattr(self, "active", cam) or True))
        closed = []
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "close", lambda self: closed.append(True))
        monkeypatch.setattr(
            "pendulastic_phone_server.get_all_local_ips", lambda: ["192.168.1.50"], raising=False)
        app.on_camera_selected(_m.PHONE_CAMERA_LABEL)
        app.on_camera_disabled()
        assert closed == [True]
        assert app._acq._phone_pairing_frame.winfo_manager() == ""
    finally:
        app.destroy()


def test_start_rgb_recording_writes_timestamp_sidecar_for_phone_camera(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import camera_utils
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        app._camera = camera_utils.PhoneCameraSession(
            on_frame=app._on_camera_frame, on_status=app._on_camera_status,
            server_module=object())   # never actually opened in this test
        app._camera.active = _m.PHONE_CAMERA_ENTRY
        app._camera._frame_size = (640, 480)

        sinks = []
        monkeypatch.setattr(
            camera_utils.PhoneCameraSession, "attach_timestamp_sink",
            lambda self, cb: sinks.append(cb))
        monkeypatch.setattr(camera_utils.PhoneCameraSession, "attach_writer", lambda self, w: None)
        monkeypatch.setattr(_m._cv2, "VideoWriter",
                             lambda *a, **k: type("W", (), {"isOpened": lambda s: True,
                                                             "write": lambda s, f: None,
                                                             "release": lambda s: None})())

        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._active_sources = ["rgb"]
        app._start_rgb_recording(meta)

        assert len(sinks) == 1
        sinks[0](3, 456789)
        sinks[0](4, 456800)
        assert os.path.exists(app._rgb_ts_path)

        monkeypatch.setattr(camera_utils.PhoneCameraSession, "detach_timestamp_sink",
                             lambda self: sinks.clear())
        monkeypatch.setattr(camera_utils.PhoneCameraSession, "detach_writer", lambda self: None)
        app._stop_rgb_recording()

        with open(app._rgb_ts_path, newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == ["frame_index", "desktop_ts_ms"]
        assert rows[1:] == [["3", "456789"], ["4", "456800"]]
    finally:
        app.destroy()


def test_start_rgb_recording_skips_sidecar_for_usb_camera(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        # app._camera is already a CameraSession (default from __init__)
        monkeypatch.setattr(app._camera, "active", {"index": 0, "label": "Camera 0"})
        app._camera._frame_size = (640, 480)
        monkeypatch.setattr(app._camera, "attach_writer", lambda w: None)
        monkeypatch.setattr(_m._cv2, "VideoWriter",
                             lambda *a, **k: type("W", (), {"isOpened": lambda s: True,
                                                             "write": lambda s, f: None,
                                                             "release": lambda s: None})())
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._active_sources = ["rgb"]
        app._start_rgb_recording(meta)
        assert getattr(app, "_rgb_ts_path", None) is None
    finally:
        app.destroy()


def test_on_countdown_start_resets_calibration_state():
    from pendulastic_app import App
    app = App()
    try:
        app._calib_was_stable = True
        app._calib_ever_stable = True
        app.on_countdown_start()
        assert app._calib_was_stable is False
        assert app._calib_ever_stable is False
    finally:
        app.destroy()




def test_tick_fires_zero_once_when_stable_during_countdown(monkeypatch):
    import pendulastic_app as _m
    zero_calls = []

    class MockIMU:
        def __init__(self):
            self.is_stationary_val = True
        def zero(self):
            zero_calls.append(1)
        def is_stationary(self):
            return self.is_stationary_val
        def start(self):
            pass
        def stop(self):
            pass

    mock_imu = MockIMU()
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", mock_imu)
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = "sentinel"   # any non-None value marks countdown active
        # Run multiple times to verify zero() only fires once during stable period
        for _ in range(50):
            app._tick_calibration_check()
        assert len(zero_calls) == 1, "zero() must only fire once when is_stationary() is continuously True"
        assert app._calib_ever_stable is True
    finally:
        app._acq._countdown_id = None
        app.destroy()




def test_tick_calibration_refires_after_drift_then_restabilizing(monkeypatch):
    import pendulastic_app as _m
    zero_calls = []

    class MockIMU:
        def __init__(self):
            self.is_stationary_val = True
        def zero(self):
            zero_calls.append(1)
        def is_stationary(self):
            return self.is_stationary_val
        def start(self):
            pass
        def stop(self):
            pass

    mock_imu = MockIMU()
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", mock_imu)
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = "sentinel"

        # First stable period
        mock_imu.is_stationary_val = True
        app._tick_calibration_check()
        assert len(zero_calls) == 1

        # Drift: report as unstable, resetting the edge-trigger
        mock_imu.is_stationary_val = False
        for _ in range(10):
            app._tick_calibration_check()
        assert len(zero_calls) == 1, "must not re-fire while unstable"

        # Re-stabilize: report as stationary again
        mock_imu.is_stationary_val = True
        app._tick_calibration_check()
        assert len(zero_calls) == 2, "must re-fire on the next stable window"
    finally:
        app._acq._countdown_id = None
        app.destroy()




def test_tick_calibration_skipped_outside_countdown(monkeypatch):
    import pendulastic_app as _m
    zero_calls = []

    class MockIMU:
        def __init__(self):
            self.is_stationary_val = True
        def zero(self):
            zero_calls.append(1)
        def is_stationary(self):
            return self.is_stationary_val
        def start(self):
            pass
        def stop(self):
            pass

    mock_imu = MockIMU()
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", mock_imu)
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = None   # no countdown running
        # Even though is_stationary() returns True, zero() should not be called
        # when _countdown_id is None (countdown not active)
        for _ in range(5):
            app._tick_calibration_check()
        assert zero_calls == []
    finally:
        app.destroy()




def test_tick_calibration_stops_after_countdown_completes_naturally(monkeypatch):
    """Regression test: verify calibration gate closes when countdown reaches n==0.
    Must call _tick_countdown(0) to exercise the actual countdown completion path,
    which clears _countdown_id. Even if the subject holds steady during recording
    (stable buffer), zero() must not fire mid-trial."""
    import pendulastic_app as _m
    zero_calls = []

    class MockIMU:
        def __init__(self):
            self.is_stationary_val = True
        def zero(self):
            zero_calls.append(1)
        def is_stationary(self):
            return self.is_stationary_val
        def start(self):
            pass
        def stop(self):
            pass

    mock_imu = MockIMU()
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", mock_imu)
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"

        # Start countdown manually by setting _countdown_id to a sentinel
        app._acq._countdown_id = "active"

        # Call calibration check with stable readings during countdown — should fire once
        app._tick_calibration_check()
        assert len(zero_calls) == 1, "Must fire once during stable countdown"

        # Mock on_start to avoid full recording startup (which would try to
        # start threads, allocate resources, etc.)
        def fake_on_start():
            app._state = "recording"
        monkeypatch.setattr(app, "on_start", fake_on_start)

        # Actually call _tick_countdown(0) to drive the countdown to completion
        # This is the real code path that clears _countdown_id
        app._acq._tick_countdown(0)

        # Verify the countdown completion cleared _countdown_id (this is the fix)
        assert app._acq._countdown_id is None, \
            "countdown_id must be None after _tick_countdown(0) completes"
        assert app._state == "recording", \
            "state must be recording after on_start() completes"

        # Now call calibration check multiple times with stable readings
        # It should NOT fire zero() because state is "recording" not "idle",
        # even though is_stationary() returns True
        for _ in range(5):
            app._tick_calibration_check()

        # This should still be 1 — no re-fire during recording
        assert len(zero_calls) == 1, \
            "Must NOT re-fire zero() during recording, even if stable"
    finally:
        app.destroy()




def test_is_imu_calibrated_true_when_imu_not_active():
    from pendulastic_app import App
    app = App()
    try:
        app._active_sources = []
        app._calib_ever_stable = False
        assert app.is_imu_calibrated() is True
    finally:
        app.destroy()




def test_is_imu_calibrated_reflects_ever_stable_when_imu_active():
    from pendulastic_app import App
    app = App()
    try:
        app._active_sources = ["imu"]
        app._calib_ever_stable = False
        assert app.is_imu_calibrated() is False
        app._calib_ever_stable = True
        assert app.is_imu_calibrated() is True
    finally:
        app.destroy()




def test_enter_workbench_mode_shows_trial_load_panel():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        assert app._workbench_load.winfo_ismapped()
        assert not app._mode_select.winfo_ismapped()
        assert app._state == "workbench_load"
    finally:
        app.destroy()




def test_on_back_to_mode_select_hides_workbench_panels():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app._workbench_load.pack_forget()
        app._workbench_view.pack(fill="both", expand=True)
        app.update()
        app.on_back_to_mode_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._workbench_load.winfo_ismapped()
        assert not app._workbench_view.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()


def test_on_back_to_mode_select_hides_dashboard_view():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app._workbench_load.pack_forget()
        app._dashboard_view.pack(fill="both", expand=True)
        app.update()
        app.on_back_to_mode_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._dashboard_view.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()


def test_enter_workbench_mode_shows_message_when_unavailable(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", False)
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo",
                        lambda title, msg: shown.append((title, msg)))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        assert len(shown) == 1
        assert app._mode_select.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()




def test_on_load_trial_imu_only_switches_to_workbench_view(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0, 0.05]), np.array([180.0, 170.0])))
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        app.on_load_trial({
            "imu_path": str(tmp_path / "trial.jsonl"), "video_path": None,
            "optitrack_path": None, "models": [],
            "participant_id": "", "session_date": "2026-08-04",
            "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert app._workbench_view.winfo_ismapped()
        assert not app._workbench_load.winfo_ismapped()
        assert "imu" in app._workbench_view._traces
    finally:
        app.destroy()




def test_get_trial_meta_reflects_last_loaded_selection(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0]), np.array([180.0])))
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.on_load_trial({
            "imu_path": "some/trial.jsonl", "video_path": None,
            "optitrack_path": None, "models": [],
            "participant_id": "P5", "session_date": "2026-08-04",
            "femur_length_cm": 45.0, "tibia_length_cm": 38.0,
        })
        app.update()
        meta = app.get_trial_meta()
        assert meta["imu_path"] == "some/trial.jsonl"
        assert meta["femur_length_cm"] == 45.0
        # Regression: the embedded-Workbench path used to drop these two
        # keys, so every CSV exported from the main app got blank
        # participant_id/session_date columns (WorkbenchView._meta_ids()
        # falls back to "" via .get(), so it failed silently).
        assert meta["participant_id"] == "P5"
        assert meta["session_date"] == "2026-08-04"
    finally:
        app.destroy()


def test_on_load_trial_split_csv_binds_and_stores_imu_reference(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)

    fake_validations = {
        "accel": {"ok": True, "rows": [], "path": "a.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "gyro":  {"ok": True, "rows": [], "path": "g.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "mag":   {"ok": True, "rows": [], "path": "m.csv", "error": None, "n_samples": 2, "fs_eff": 100.0},
        "imu":   {"ok": True, "rows": [{"hip_pitch_deg": "180.0"}], "path": "i.csv",
                  "error": None, "n_samples": 1, "fs_eff": 100.0},
    }
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial_from_components": staticmethod(
            lambda validations, ft_ratio=None, method=None:
                (np.array([0.0, 0.05]), np.array([180.0, 170.0]), validations["imu"]["rows"]))
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        app.on_load_trial({
            "imu_format": "split_csv", "imu_path": None, "imu_components": fake_validations,
            "video_path": None, "optitrack_path": None,
            "participant_id": "", "session_date": "",
            "models": [], "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert "imu" in app._workbench_view._traces
        meta = app.get_trial_meta()
        assert meta["imu_paths"] == {"accel": "a.csv", "gyro": "g.csv", "mag": "m.csv", "imu": "i.csv"}
        assert "imu_reference" not in meta
        assert app._workbench_imu_reference == [{"hip_pitch_deg": "180.0"}]
    finally:
        app.destroy()




def test_on_workbench_load_another_returns_to_trial_load_panel(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app._workbench_load.pack_forget()
        app._workbench_view.pack(fill="both", expand=True)
        app.update()
        app.on_workbench_load_another()
        app.update()
        assert app._workbench_load.winfo_ismapped()
        assert not app._workbench_view.winfo_ismapped()
    finally:
        app.destroy()


def test_on_load_trial_populates_raw_diagnostics_when_imu_selected(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0]), np.array([180.0]))),
        "compute_raw_sensor_diagnostics": staticmethod(
            lambda path: {"peak_gyro_velocity_dps": 42.0, "accel_release_time_sec": 1.23}),
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.on_load_trial({
            "imu_path": str(tmp_path / "trial.jsonl"), "video_path": None,
            "optitrack_path": None,
            "participant_id": "", "session_date": "",
            "models": [], "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert app._workbench_raw_diagnostics == {
            "peak_gyro_velocity_dps": 42.0, "accel_release_time_sec": 1.23}
    finally:
        app.destroy()


def test_on_view_dashboard_shows_dashboard_and_hides_workbench_panels(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        app.on_view_dashboard()
        app.update()
        assert app._dashboard_view.winfo_ismapped()
        assert not app._workbench_load.winfo_ismapped()
        assert not app._workbench_view.winfo_ismapped()
    finally:
        app.destroy()


def test_on_load_trial_raw_diagnostics_failure_does_not_block_trial_load(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)

    def raise_error(path):
        raise ValueError("synthetic failure")

    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0]), np.array([180.0]))),
        "compute_raw_sensor_diagnostics": staticmethod(raise_error),
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.on_load_trial({
            "imu_path": str(tmp_path / "trial.jsonl"), "video_path": None,
            "optitrack_path": None,
            "participant_id": "", "session_date": "",
            "models": [], "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert app._workbench_view.winfo_ismapped()
        assert "imu" in app._workbench_view._traces
        assert app._workbench_raw_diagnostics is None
    finally:
        app.destroy()


def test_on_dashboard_back_returns_to_trial_load_panel(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.on_view_dashboard()
        app.update()
        app.on_dashboard_back()
        app.update()
        assert app._workbench_load.winfo_ismapped()
        assert not app._dashboard_view.winfo_ismapped()
    finally:
        app.destroy()


def test_enter_mas_entry_mode_shows_panel_and_hides_mode_select():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app.update()
        assert app._mas_entry.winfo_ismapped()
        assert not app._mode_select.winfo_ismapped()
        assert app._state == "mas_entry"
    finally:
        app.destroy()


def test_on_back_to_mode_select_hides_mas_entry_panel():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app.update()
        app.on_back_to_mode_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._mas_entry.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()


def test_mas_entry_panel_empty_state_placeholder(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [])
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app._mas_entry.refresh()
        app.update()
        assert app._mas_entry.canvas_placeholder.winfo_ismapped()
        assert app._mas_entry._current_canvas is None
    finally:
        app.destroy()


def test_mas_entry_panel_shows_skipped_row_status(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "14", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: None))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app._mas_entry.refresh()
        app.update()
        text = app._mas_entry.status_text.get("1.0", "end")
        assert "14" in text
        assert "no matching trial data" in text
    finally:
        app.destroy()


def test_mas_entry_panel_refresh_renders_figure_when_data_present(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app._mas_entry.refresh()
        app.update()
        assert not app._mas_entry.canvas_placeholder.winfo_ismapped()
        assert app._mas_entry._current_canvas is not None
        assert len(app._mas_entry._last_valid) == 1
        assert app._mas_entry._last_stats is not None
    finally:
        app.destroy()


def test_enter_mas_entry_mode_refreshes_dashboard_on_open(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app.update()
        assert app._mas_entry._current_canvas is not None
        assert len(app._mas_entry._last_valid) == 1
    finally:
        app.destroy()


def test_mas_entry_panel_blocks_save_on_missing_required_fields(monkeypatch):
    import pendulastic_app as _m
    calls = []
    monkeypatch.setattr(_m._mas_validation, "append_mas_score",
                        lambda row, **kw: calls.append(row))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("")
        app._mas_entry.mas_grade_var.set("")
        app._mas_entry._on_save_clicked()
        app.update()
        assert calls == []
        assert "required" in app._mas_entry.error_var.get().lower()
    finally:
        app.destroy()


def test_mas_entry_panel_save_appends_and_refreshes(monkeypatch):
    import pendulastic_app as _m
    append_calls = []
    monkeypatch.setattr(_m._mas_validation, "append_mas_score",
                        lambda row, **kw: append_calls.append(row))
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.leg_var.set("Left")
        app._mas_entry.condition_var.set("pre")
        app._mas_entry.diagnosis_var.set("multiple sclerosis")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry.assessed_by_var.set("VL")
        app._mas_entry.assessed_date_var.set("2026-08-07")
        app._mas_entry._on_save_clicked()
        app.update()
        assert len(append_calls) == 1
        # Assert every field explicitly: condition/diagnosis/assessed_by/
        # assessed_date are four same-typed string fields, so an accidental
        # key-swap between them would otherwise go completely undetected.
        # stronger_leg and notes are new in Task 4 -- empty since not set in test.
        assert append_calls[0] == {
            "participant": "20",
            "leg": "left",
            "condition": "pre",
            "diagnosis": "multiple sclerosis",
            "mas_grade": "1",
            "assessed_by": "VL",
            "assessed_date": "2026-08-07",
            "stronger_leg": "",
            "notes": "",
        }
        assert app._mas_entry._current_canvas is not None
    finally:
        app.destroy()


def test_mas_entry_panel_save_clears_mas_grade_and_shows_confirmation(monkeypatch):
    """Double-clicking Save would otherwise append an identical duplicate row
    (the form is deliberately not cleared, for batch entry), biasing the
    downstream Spearman/kappa stats. A confirmation plus a cleared grade makes
    a resubmit deliberate rather than accidental."""
    import pendulastic_app as _m
    append_calls = []
    monkeypatch.setattr(_m._mas_validation, "append_mas_score",
                        lambda row, **kw: append_calls.append(row))
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry._on_save_clicked()
        app.update()
        assert len(append_calls) == 1
        assert app._mas_entry.mas_grade_var.get() == ""
        assert "20" in app._mas_entry.error_var.get()

        # The now-blank grade blocks a reflexive second click.
        app._mas_entry._on_save_clicked()
        app.update()
        assert len(append_calls) == 1
        assert "required" in app._mas_entry.error_var.get().lower()
    finally:
        app.destroy()


def test_mas_entry_panel_save_shows_error_on_invalid_grade(monkeypatch):
    import pendulastic_app as _m

    def raise_invalid(row, **kw):
        raise ValueError(f"invalid mas_grade {row['mas_grade']!r} (must be one of [])")
    monkeypatch.setattr(_m._mas_validation, "append_mas_score", raise_invalid)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry._on_save_clicked()
        app.update()
        assert "invalid mas_grade" in app._mas_entry.error_var.get()
    finally:
        app.destroy()


def test_mas_entry_panel_export_disabled_when_no_data(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [])
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        assert str(app._mas_entry.export_btn.cget("state")) == "disabled"
    finally:
        app.destroy()


def test_mas_entry_panel_export_writes_stats_and_figure(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    stats_calls = []
    figure_calls = []
    monkeypatch.setattr(_m._mas_validation, "write_stats_csv",
                        lambda stats, out_path: stats_calls.append((stats, out_path)))
    monkeypatch.setattr(_m._mas_validation, "save_validation_figure",
                        lambda valid, stats, out_path: figure_calls.append(
                            (valid, stats, out_path)))
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **kw: None)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        assert str(app._mas_entry.export_btn.cget("state")) == "normal"
        app._mas_entry._on_export_clicked()
        assert len(stats_calls) == 1
        assert stats_calls[0][0] == app._mas_entry._last_stats
        assert stats_calls[0][1] == _m._mas_validation.STATS_CSV
        assert len(figure_calls) == 1
        assert figure_calls[0][0] == app._mas_entry._last_valid
        assert figure_calls[0][1] == app._mas_entry._last_stats
        assert figure_calls[0][2] == _m._mas_validation.FIGURE_PNG
    finally:
        app.destroy()


def test_mas_entry_panel_export_shows_error_dialog_on_failure(monkeypatch):
    """write_stats_csv/save_validation_figure do real file I/O (os.makedirs,
    file writes) -- a read-only dir, a full disk, or the PNG being open in
    another program on Windows all raise. Unhandled, the click would look dead."""
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))

    def boom(stats, out_path):
        raise OSError("disk is full")
    monkeypatch.setattr(_m._mas_validation, "write_stats_csv", boom)

    figure_calls = []
    monkeypatch.setattr(_m._mas_validation, "save_validation_figure",
                        lambda *a, **kw: figure_calls.append(a))
    errors = []
    infos = []
    monkeypatch.setattr(_m.messagebox, "showerror",
                        lambda title, msg, *a, **kw: errors.append((title, msg)))
    monkeypatch.setattr(_m.messagebox, "showinfo",
                        lambda *a, **kw: infos.append(a))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        app._mas_entry._on_export_clicked()
        app.update()
        assert len(errors) == 1
        assert errors[0][0] == "Export Failed"
        assert "disk is full" in errors[0][1]
        # ...and the success dialog is NOT also shown.
        assert infos == []
        assert figure_calls == []
    finally:
        app.destroy()


def test_mas_entry_panel_save_includes_stronger_leg_and_notes(monkeypatch):
    import pendulastic_app as _m
    append_calls = []
    monkeypatch.setattr(_m._mas_validation, "append_mas_score",
                        lambda row, **kw: append_calls.append(row))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry.stronger_leg_var.set("right")
        app._mas_entry.notes_text.insert("1.0", "gait looked steadier today")
        app._mas_entry._on_save_clicked()
        app.update()
        assert len(append_calls) == 1
        assert append_calls[0]["stronger_leg"] == "right"
        assert append_calls[0]["notes"] == "gait looked steadier today"
    finally:
        app.destroy()


def test_mas_entry_panel_save_clears_notes_but_not_stronger_leg(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "append_mas_score", lambda row, **kw: None)
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [])
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry.stronger_leg_var.set("right")
        app._mas_entry.notes_text.insert("1.0", "some notes")
        app._mas_entry._on_save_clicked()
        app.update()
        assert app._mas_entry.notes_text.get("1.0", "end").strip() == ""
        assert app._mas_entry.stronger_leg_var.get() == "right"
    finally:
        app.destroy()


def test_mas_entry_panel_refresh_handles_repeated_placeholder_and_figure_transitions(monkeypatch):
    """Guards the figure lifecycle (canvas destroy + plt.close of a Figure that
    pyplot never registered) across repeated empty -> data -> empty flips."""
    import pendulastic_app as _m
    rows = []
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: list(rows))
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    one_row = {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()   # pack the panel, so winfo_ismapped is meaningful
        app.update()
        for expect_data in (False, True, False, True, False):
            rows[:] = [one_row] if expect_data else []
            app._mas_entry.refresh()
            app.update()
            panel = app._mas_entry
            if expect_data:
                assert panel._current_canvas is not None
                assert not panel.canvas_placeholder.winfo_ismapped()
                assert str(panel.export_btn.cget("state")) == "normal"
            else:
                assert panel._current_canvas is None
                assert panel.canvas_placeholder.winfo_ismapped()
                assert str(panel.export_btn.cget("state")) == "disabled"
    finally:
        app.destroy()


def test_tick_calibration_check_fires_zero_when_imu_reports_stationary(monkeypatch):
    """_tick_calibration_check() must now gate on _imu.is_stationary() directly,
    not on a fused pitch/roll buffer it maintains itself."""
    import pendulastic_app as _m, types
    zero_calls = []

    class MockIMU:
        def __init__(self):
            self.is_stationary_val = True
        def zero(self):
            zero_calls.append(1)
        def is_stationary(self):
            return self.is_stationary_val
        def start(self):
            pass
        def stop(self):
            pass

    mock_imu = MockIMU()
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", mock_imu)
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = "sentinel"
        app._tick_calibration_check()
        assert len(zero_calls) == 1
        assert app._calib_ever_stable is True
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_check_does_not_fire_when_imu_reports_not_stationary(monkeypatch):
    import pendulastic_app as _m
    zero_calls = []

    class MockIMU:
        def __init__(self):
            self.is_stationary_val = False
        def zero(self):
            zero_calls.append(1)
        def is_stationary(self):
            return self.is_stationary_val
        def start(self):
            pass
        def stop(self):
            pass

    mock_imu = MockIMU()
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", mock_imu)
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = "sentinel"
        app._tick_calibration_check()
        assert zero_calls == []
        assert app._calib_ever_stable is False
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_check_refires_after_drift_then_restabilizing(monkeypatch):
    """Edge-trigger behavior must be preserved: False->True fires once, stays
    latched while True, then re-fires on the next False->True transition."""
    import pendulastic_app as _m
    zero_calls = []

    class MockIMU:
        def __init__(self):
            self.is_stationary_val = False
        def zero(self):
            zero_calls.append(1)
        def is_stationary(self):
            return self.is_stationary_val
        def start(self):
            pass
        def stop(self):
            pass

    mock_imu = MockIMU()
    monkeypatch.setattr(_m, "_IMU_AVAIL", True)
    monkeypatch.setattr(_m, "_imu", mock_imu)
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = "sentinel"

        mock_imu.is_stationary_val = True
        app._tick_calibration_check()
        app._tick_calibration_check()
        app._tick_calibration_check()
        assert len(zero_calls) == 1, "must not re-fire every tick while continuously stationary"

        mock_imu.is_stationary_val = False
        app._tick_calibration_check()
        assert len(zero_calls) == 1

        mock_imu.is_stationary_val = True
        app._tick_calibration_check()
        assert len(zero_calls) == 2, "must re-fire on the next stable window"
    finally:
        app._acq._countdown_id = None
        app.destroy()




def test_ws_palette_bg_is_light_gray():
    import workbench_style as ws
    assert ws.PALETTE["BG"] == "#F4F6F9"


def test_app_applies_ttk_theme_even_when_workbench_unavailable(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", False)
    calls = []
    monkeypatch.setattr(_m.ws, "apply_ttk_theme", lambda root: calls.append(root))
    app = _m.App()
    try:
        assert len(calls) == 1
        assert str(app.cget("bg")) == _m.ws.PALETTE["BG"]
    finally:
        app.destroy()


def test_is_multi_trial_mode_reflects_checkbox():
    from pendulastic_app import App
    app = App()
    try:
        assert app._is_multi_trial_mode() is False
        app._acq._multi_trial_var.set(True)
        assert app._is_multi_trial_mode() is True
    finally:
        app.destroy()


def test_trial_file_paths_imu_and_rgb(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        names = [os.path.basename(p) for p in app._trial_file_paths(meta, ["imu", "rgb"])]
        assert "PID_P1_LEG_Right_MS_TRIAL_1_imu.csv" in names
        assert "PID_P1_LEG_Right_MS_TRIAL_1_rgb.csv" in names
        assert "PID_P1_LEG_Right_MS_TRIAL_1.avi" in names
        assert "PID_P1_LEG_Right_MS_TRIAL_1.avi.timestamps.csv" in names
    finally:
        app.destroy()


def test_trial_file_paths_optitrack_only(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        assert app._trial_file_paths(meta, ["optitrack"]) == []
    finally:
        app.destroy()


def test_on_stop_appends_processing_placeholder_in_multi_trial_mode(monkeypatch):
    """Verifies the placeholder append that happens at the top of on_stop(),
    in isolation from the finalize call at the bottom of on_stop(). With no
    active sources, on_stop() has no async work to do and -- since Task 4 --
    calls _transition_to_review() synchronously within the same on_stop()
    call, which immediately finalizes the entry to "saved" in multi-trial
    mode. _transition_to_review is stubbed out here so this test can keep
    checking the append step on its own."""
    import pendulastic_app as _m
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: None)
    app = _m.App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("1")
        app._acq._multi_trial_var.set(True)
        app._active_sources = []
        monkeypatch.setattr(app, "_transition_to_review", lambda *a, **k: None)
        app.on_stop()
        app.update()
        assert len(app._session_trials) == 1
        entry = app._session_trials[0]
        assert entry["trial_num"] == 1
        assert entry["sources"] == []
        assert entry["status"] == "processing"
    finally:
        app.destroy()


def test_on_stop_no_placeholder_when_multi_trial_off(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: None)
    app = _m.App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("1")
        app._active_sources = []
        app.on_stop()
        app.update()
        assert app._session_trials == []
    finally:
        app.destroy()


def test_on_delete_trial_removes_files_and_entry(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        f1 = tmp_path / "trial1_imu.csv"
        f1.write_text("data")
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [str(f1)],
        }]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(1)
        app.update()
        assert not f1.exists()
        assert app._session_trials == []
    finally:
        app.destroy()


def test_on_delete_trial_ignores_missing_files(tmp_path, monkeypatch):
    """A file already gone (e.g. hand-deleted) must not raise."""
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        missing = str(tmp_path / "already_gone.csv")
        app._session_trials = [{
            "trial_num": 2, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [missing],
        }]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(2)
        app.update()
        assert app._session_trials == []
    finally:
        app.destroy()


def test_on_delete_trial_unknown_trial_num_is_noop():
    from pendulastic_app import App
    app = App()
    try:
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [],
        }]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(99)
        app.update()
        assert len(app._session_trials) == 1
    finally:
        app.destroy()


def test_on_delete_trial_leaves_gap_in_numbering(tmp_path, monkeypatch):
    """Deleting Trial 2 of {1,2,3} must not renumber 3 down to 2, and a
    freshly-recorded next trial (driven by the spinner, untouched by
    delete) must not reuse 2."""
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        app._acq.trial_var.set("4")   # spinner already past trial 3
        app._session_trials = [
            {"trial_num": 1, "sources": ["imu"], "status": "saved",
             "meta": {}, "source_angles": {}, "fps": 30.0,
             "base_filename": "a.csv", "file_paths": []},
            {"trial_num": 2, "sources": ["imu"], "status": "saved",
             "meta": {}, "source_angles": {}, "fps": 30.0,
             "base_filename": "b.csv", "file_paths": []},
            {"trial_num": 3, "sources": ["imu"], "status": "saved",
             "meta": {}, "source_angles": {}, "fps": 30.0,
             "base_filename": "c.csv", "file_paths": []},
        ]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(2)
        app.update()
        remaining = sorted(e["trial_num"] for e in app._session_trials)
        assert remaining == [1, 3]
        assert int(app._acq.trial_var.get()) == 4   # untouched by delete
    finally:
        app.destroy()
