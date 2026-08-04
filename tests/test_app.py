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
        def _capture(source_angles, m):
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
        app._transition_to_review = lambda source_angles, m: result_holder.update(
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
        app._transition_to_review = lambda source_angles, m: result_holder.update(
            source_angles=source_angles)

        app._run_imu_tuning(str(tmp_path / "does_not_exist.jsonl"), csv_path, csv_filename, meta)
        app.update()
        assert result_holder["source_angles"]["imu"] == [1.0, 2.0]
    finally:
        app.destroy()


def test_on_countdown_start_resets_calibration_state():
    from pendulastic_app import App
    app = App()
    try:
        app._calib_buffer = [(1.0, 2.0)]
        app._calib_was_stable = True
        app._calib_ever_stable = True
        app.on_countdown_start()
        assert app._calib_buffer == []
        assert app._calib_was_stable is False
        assert app._calib_ever_stable is False
    finally:
        app.destroy()


def test_tick_fires_zero_once_when_stable_during_countdown(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = "sentinel"   # any non-None value marks countdown active
        zero_calls = []
        # Mirror the real zero(): it retares the offset, so subsequent
        # get_state() calls report angles relative to the new zero (~0)
        # instead of the pre-tare pose. A buggy edge-trigger that doesn't
        # discard the stale buffer will see this jump as "not stable" and
        # re-fire zero() a second time for one continuous physical hold.
        state = {"pitch": 10.0, "roll": 0.0}
        monkeypatch.setattr(_m._imu, "zero", lambda: (zero_calls.append(1),
                                                       state.update(pitch=0.0, roll=0.0)))
        monkeypatch.setattr(_m._imu, "get_state", lambda: {"angles": dict(state)})
        # Run well past several buffer-refill cycles (not just one) -- a fire
        # that only survives to the next refill would still look "fixed" on
        # a short run but re-trigger every _CALIB_BUFFER_SAMPLES ticks after
        # that for as long as the hold continues.
        for _ in range(4 * _m._CALIB_BUFFER_SAMPLES + 10):
            app._tick_calibration_check()
        assert len(zero_calls) == 1
        assert app._calib_ever_stable is True
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_refires_after_drift_then_restabilizing(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        app._acq._countdown_id = "sentinel"
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        state = {"pitch": 10.0, "roll": 0.0}
        monkeypatch.setattr(_m._imu, "get_state", lambda: {"angles": dict(state)})

        for _ in range(_m._CALIB_BUFFER_SAMPLES + 2):
            app._tick_calibration_check()
        assert len(zero_calls) == 1

        # Drift: feed a swinging pitch that exceeds the stability range so the
        # buffer's peak-to-peak range fails, resetting the edge-trigger.
        for i in range(_m._CALIB_BUFFER_SAMPLES):
            state["pitch"] = 10.0 + (i % 2) * 10.0   # alternates 10/20 -> 10 deg swing
            app._tick_calibration_check()
        assert len(zero_calls) == 1, "must not re-fire while still unstable"

        # Re-stabilize at a new position.
        state["pitch"] = 45.0
        for _ in range(_m._CALIB_BUFFER_SAMPLES + 2):
            app._tick_calibration_check()
        assert len(zero_calls) == 2, "must re-fire on the next new stable window"
    finally:
        app._acq._countdown_id = None
        app.destroy()


def test_tick_calibration_skipped_outside_countdown(monkeypatch):
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._acq._countdown_id = None   # no countdown running
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "angles": {"pitch": 10.0, "roll": 0.0},
        })
        for _ in range(_m._CALIB_BUFFER_SAMPLES + 5):
            app._tick_calibration_check()
        assert zero_calls == []
        assert app._calib_buffer == []
    finally:
        app.destroy()


def test_tick_calibration_stops_after_countdown_completes_naturally(monkeypatch):
    """Regression test: verify calibration gate closes when countdown reaches n==0.
    Must call _tick_countdown(0) to exercise the actual countdown completion path,
    which clears _countdown_id. Even if the subject holds steady during recording
    (stable buffer), zero() must not fire mid-trial."""
    import pendulastic_app as _m
    app = _m.App()
    try:
        app._active_sources = ["imu"]
        app._state = "idle"
        zero_calls = []
        monkeypatch.setattr(_m._imu, "zero", lambda: zero_calls.append(1))
        monkeypatch.setattr(_m._imu, "get_state", lambda: {
            "angles": {"pitch": 10.0, "roll": 0.0},
        })

        # Start countdown manually by setting _countdown_id to a sentinel
        app._acq._countdown_id = "active"

        # Fill buffer while in countdown — should fire once when stable
        for _ in range(_m._CALIB_BUFFER_SAMPLES):
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
        # even though _countdown_id was cleared and buffer is stable
        for _ in range(_m._CALIB_BUFFER_SAMPLES + 5):
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
            "femur_length_cm": 45.0, "tibia_length_cm": 38.0,
        })
        app.update()
        meta = app.get_trial_meta()
        assert meta["imu_path"] == "some/trial.jsonl"
        assert meta["femur_length_cm"] == 45.0
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
