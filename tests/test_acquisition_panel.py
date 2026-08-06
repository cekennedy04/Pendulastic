# tests/test_acquisition_panel.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk


def _root():
    r = tk.Tk(); r.withdraw(); return r


class _Ctrl:
    """Minimal fake controller."""
    def __init__(self):
        self.calls = []
    def on_start(self): pass
    def on_stop(self): pass
    def on_source_changed(self, sources): pass
    def on_new_trial(self): pass
    def on_back_to_mode_select(self): pass
    def on_rescan_cameras(self): self.calls.append("rescan")
    def on_camera_selected(self, label): self.calls.append(("selected", label))
    def on_camera_disabled(self): self.calls.append("disabled")
    def on_countdown_start(self): pass
    def is_imu_calibrated(self): return True


def test_panel_instantiates():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pack()
        r.update()
    finally:
        r.destroy()




def test_default_vars():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        # Routine clinical sources (IMU + RGB) checked by default;
        # research-only sources (OptiTrack, Video File) start unchecked.
        assert p._src_imu.get() is True
        assert p._src_rgb.get() is True
        assert p._src_optitrack.get() is False
        assert p._src_video_file.get() is False
        assert p.countdown_var.get() is True
        assert int(p.trial_var.get()) == 1
    finally:
        r.destroy()




def test_telemetry_canvas_not_gridded_at_init():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p.canvas_tele.grid_info() == {}
    finally:
        r.destroy()




def test_start_without_countdown_calls_on_start():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def on_start(self): calls.append("start")
        p = AcquisitionPanel(r, C()); p.pack()
        p.pid_var.set("P1")
        p.countdown_var.set(False)
        p._on_start_clicked()
        r.update()
        assert "start" in calls
    finally:
        r.destroy()




def test_start_with_countdown_shows_cancel():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack()
        p.pid_var.set("P1")
        p.countdown_var.set(True)
        p._on_start_clicked()
        r.update()
        assert p.btn_start.cget("text") == "CANCEL"
    finally:
        r.destroy()




def test_enter_recording_shows_telemetry():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.enter_recording(); r.update()
        assert p.canvas_tele.grid_info() != {}
    finally:
        r.destroy()




def test_enter_idle_hides_telemetry():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.enter_recording()
        p.enter_idle(); r.update()
        assert p.canvas_tele.grid_info() == {}
    finally:
        r.destroy()




def test_validate_empty_pid_fails():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("")
        ok, msg = p.validate_metadata()
        assert not ok and "Participant ID" in msg
    finally:
        r.destroy()




def test_validate_no_source_checked_fails():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P1")
        p._src_optitrack.set(False)
        p._src_rgb.set(False)
        p._src_imu.set(False)
        ok, msg = p.validate_metadata()
        assert not ok
        assert "source" in msg.lower()
    finally:
        r.destroy()




def test_get_metadata_returns_sources_list():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P7"); p.leg_var.set("Left")
        p.ms_var.set("Stroke"); p.trial_var.set("3")
        p._src_optitrack.set(False)
        p._src_rgb.set(False)
        p._src_imu.set(True)
        result = p.get_metadata()
        assert result["pid"] == "P7"
        assert result["leg"] == "Left"
        assert result["ms_status"] == "Stroke"
        assert result["trial"] == 3
        assert result["sources"] == ["imu"]
        assert "methodology" not in result
    finally:
        r.destroy()




def test_get_active_sources_sorted():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p._src_optitrack.set(True)
        p._src_imu.set(True)
        p._src_rgb.set(False)
        sources = p.get_active_sources()
        assert "imu" in sources
        assert "optitrack" in sources
        assert "rgb" not in sources
        assert sources == sorted(sources)
    finally:
        r.destroy()




def test_increment_trial():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.trial_var.set("4")
        p.increment_trial()
        assert int(p.trial_var.get()) == 5
    finally:
        r.destroy()




def test_push_telemetry_draws_items_on_canvas():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack()
        p.enter_recording(); r.update()
        p.push_telemetry(0.0, 160.0)
        p.push_telemetry(0.05, 155.0)
        r.update()
        assert len(p.canvas_tele.find_all()) > 0
    finally:
        r.destroy()




def test_clear_telemetry_removes_all_items():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack()
        p.enter_recording()
        p.push_telemetry(0.0, 160.0)
        p.clear_telemetry()
        r.update()
        assert len(p.canvas_tele.find_all()) == 0
    finally:
        r.destroy()




def test_preview_label_removed():
    """The embedded live preview was removed in favor of the separate
    WebcamViewerWindow -- pin that it's actually gone, not just unused."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert not hasattr(p, "lbl_preview")
    finally:
        r.destroy()




def test_telemetry_canvas_shown_during_rgb_recording():
    """With the embedded preview gone, row 13 always shows the telemetry
    canvas while recording -- including for RGB, which previously showed
    the embedded preview there instead."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_source_changed()
        p.enter_recording()
        r.update()
        assert p.canvas_tele.grid_info() != {}
    finally:
        r.destroy()




def test_video_file_checkbox_exists():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert hasattr(p, "_src_video_file"), "_src_video_file BooleanVar must exist"
        assert p._src_video_file.get() is False, "Video file checkbox must be unchecked by default"
    finally:
        r.destroy()




def test_validate_rejects_video_file_and_rgb_together():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P1")
        p._src_video_file.set(True)
        p._src_rgb.set(True)
        p._stored_video_path = "/fake/video.mp4"
        ok, msg = p.validate_metadata()
        assert not ok
        assert "rgb" in msg.lower() or "video" in msg.lower() or "simultan" in msg.lower()
    finally:
        r.destroy()




def test_validate_rejects_video_file_without_path():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P1")
        p._src_optitrack.set(False)
        p._src_rgb.set(False)
        p._src_video_file.set(True)
        p._stored_video_path = ""
        ok, msg = p.validate_metadata()
        assert not ok
        assert "file" in msg.lower() or "select" in msg.lower()
    finally:
        r.destroy()




def test_get_metadata_includes_video_file_path():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P2")
        p._src_optitrack.set(False)
        p._src_rgb.set(False)
        p._src_video_file.set(True)
        p._stored_video_path = "/data/trial.mp4"
        meta = p.get_metadata()
        assert meta["video_file_path"] == "/data/trial.mp4"
        assert "video_file" in meta["sources"]
    finally:
        r.destroy()




def test_enter_recording_locks_camera_dropdown_and_rescan_button():
    """Regression: mid-recording camera switches orphan the attached writer
    (CameraSession.open() -> close() drops the writer without releasing it).
    The dropdown and Rescan button must be disabled during recording, same as
    every other input _lock_form covers."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.enter_recording(); r.update()
        assert str(p.drop_cam.cget("state")) == "disabled"
        assert str(p.btn_rescan.cget("state")) == "disabled"
    finally:
        r.destroy()




def test_camera_frame_hidden_by_default():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p._cam_frame.winfo_manager() == ""
    finally:
        r.destroy()




def test_checking_rgb_shows_camera_frame_and_rescans():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_rgb_checkbox_toggled()
        r.update()
        assert p._cam_frame.winfo_manager() == "pack"
        assert "rescan" in ctrl.calls
    finally:
        r.destroy()




def test_unchecking_rgb_hides_camera_frame_and_disables_camera():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_rgb_checkbox_toggled()
        p._src_rgb.set(False)
        p._on_rgb_checkbox_toggled()
        r.update()
        assert p._cam_frame.winfo_manager() == ""
        assert "disabled" in ctrl.calls
    finally:
        r.destroy()




def test_set_camera_list_populates_dropdown_and_keeps_selection():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        cams = [
            {"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"},
            {"index": 1, "backend": 700, "backend_name": "MSMF", "label": "Camera 1 (MSMF)"},
        ]
        p.set_camera_list(cams)
        assert list(p.drop_cam["values"]) == ["Camera 0 (MSMF)", "Camera 1 (MSMF)"]
        assert p.cam_var.get() == "Camera 0 (MSMF)"

        p.cam_var.set("Camera 1 (MSMF)")
        p.set_camera_list(cams)   # a rescan that still finds both keeps the selection
        assert p.cam_var.get() == "Camera 1 (MSMF)"
    finally:
        r.destroy()




def test_set_camera_list_empty_shows_none_detected():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_list([])
        assert list(p.drop_cam["values"]) == ["(none detected)"]
        assert p.cam_var.get() == "(none detected)"
    finally:
        r.destroy()




def test_selecting_camera_from_dropdown_notifies_controller():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        cams = [{"index": 0, "backend": 700, "backend_name": "MSMF", "label": "Camera 0 (MSMF)"}]
        p.set_camera_list(cams)
        p.cam_var.set("Camera 0 (MSMF)")
        p._on_cam_selected()
        assert ("selected", "Camera 0 (MSMF)") in ctrl.calls
    finally:
        r.destroy()




def test_rescan_button_notifies_controller():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        ctrl = _Ctrl()
        p = AcquisitionPanel(r, ctrl); p.pack(); r.update()
        p._on_rescan_clicked()
        assert "rescan" in ctrl.calls
    finally:
        r.destroy()




def test_camera_help_button_exists_and_opens_dialog(monkeypatch):
    from pendulastic_app import AcquisitionPanel
    import pendulastic_app as _m
    r = _root()
    try:
        shown = []
        monkeypatch.setattr(_m.messagebox, "showinfo",
                            lambda title, msg: shown.append((title, msg)))
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._on_camera_help()
        assert len(shown) == 1
        assert "camera" in shown[0][0].lower() or "connect" in shown[0][0].lower()
    finally:
        r.destroy()




def test_rgb_recording_shows_telemetry_canvas_without_set_camera_live():
    """Regression: enter_recording()'s telemetry-canvas-during-RGB-recording
    behavior must hold even when set_camera_live() was never called (the
    live pre-recording preview and the embedded _camera_live flag are gone
    -- only _is_recording drives row 13 now)."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_source_changed()
        p.enter_recording()
        r.update()
        assert p.canvas_tele.grid_info() != {}
    finally:
        r.destroy()


def test_set_camera_live_true_opens_viewer_window():
    """The separate webcam viewer window must appear as soon as the camera
    goes live -- before the countdown even starts -- so the operator has a
    chance to position it (e.g. drag to a second monitor) before stepping
    back from the laptop."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p._viewer_window is None
        p.set_camera_live(True)
        r.update()
        assert p._viewer_window is not None
        assert p._viewer_window.winfo_exists()
        assert p._viewer_window.state() != "withdrawn"
    finally:
        r.destroy()


def test_set_camera_live_false_hides_viewer_window():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        p.set_camera_live(False)
        r.update()
        assert p._viewer_window.state() == "withdrawn"
    finally:
        r.destroy()


def test_set_camera_live_reuses_existing_viewer_window():
    """Toggling live on/off/on must not recreate a fresh Toplevel each
    time -- that would drop the operator's chosen window position/size."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        first = p._viewer_window
        p.set_camera_live(False)
        p.set_camera_live(True)
        r.update()
        assert p._viewer_window is first
    finally:
        r.destroy()


def test_update_preview_forwards_frame_to_viewer_window():
    from pendulastic_app import AcquisitionPanel
    import numpy as np
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        frame[:, :, 2] = 255   # BGR red
        p.update_preview(frame)
        r.update()
        assert getattr(p._viewer_window.lbl_video, "_photo", None) is not None
    finally:
        r.destroy()


def test_set_viewer_overlay_text_is_noop_before_camera_ever_live():
    """No viewer window has been created yet (RGB never checked) -- setting
    overlay text must not crash."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_viewer_overlay_text("3")   # must not raise
    finally:
        r.destroy()


def test_tick_countdown_shows_big_red_digit_in_viewer_overlay():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        p._tick_countdown(3)
        r.update()
        p.after_cancel(p._countdown_id)
        assert p._viewer_window.lbl_overlay.cget("text") == "3"
        assert p._viewer_window.lbl_overlay.cget("fg").upper() == "#FF1E1E"
    finally:
        r.destroy()


def test_cancel_countdown_clears_viewer_overlay():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        p._tick_countdown(3)
        r.update()
        p.after_cancel(p._countdown_id)
        p._cancel_countdown()
        r.update()
        assert p._viewer_window.lbl_overlay.cget("text") == ""
    finally:
        r.destroy()


def test_tick_countdown_shows_hold_still_when_not_calibrated():
    from pendulastic_app import AcquisitionPanel

    class _NotCalibratedCtrl(_Ctrl):
        def is_imu_calibrated(self): return False

    r = _root()
    try:
        p = AcquisitionPanel(r, _NotCalibratedCtrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        p._calib_extension_s = 0
        p._tick_countdown(0)
        r.update()
        p.after_cancel(p._countdown_id)
        assert p._viewer_window.lbl_overlay.cget("text") == "HOLD STILL"
    finally:
        r.destroy()


def test_enter_recording_shows_rec_marker_in_viewer_overlay():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        p.enter_recording()
        r.update()
        assert p._viewer_window.lbl_overlay.cget("text") == "● REC"
    finally:
        r.destroy()


def test_enter_idle_clears_viewer_overlay():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        p.enter_recording()
        r.update()
        p.enter_idle()
        r.update()
        assert p._viewer_window.lbl_overlay.cget("text") == ""
    finally:
        r.destroy()


def test_start_countdown_opens_viewer_window_without_camera_live():
    """A phone-IMU-only trial (no RGB/webcam ever checked, set_camera_live()
    never called) must still get the big red countdown -- the whole point
    is visibility for an operator who's stepped back, which applies just
    as much to an IMU-only trial as an RGB one."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p._viewer_window is None
        p._src_imu.set(True)
        p._start_countdown()
        r.update()
        p.after_cancel(p._countdown_id)
        assert p._viewer_window is not None
        assert p._viewer_window.state() != "withdrawn"
        assert p._viewer_window.lbl_overlay.cget("text") == "5"
    finally:
        r.destroy()


def test_cancel_countdown_hides_viewer_window_when_camera_never_live():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._start_countdown()
        r.update()
        p.after_cancel(p._countdown_id)
        p._cancel_countdown()
        r.update()
        assert p._viewer_window.state() == "withdrawn"
    finally:
        r.destroy()


def test_viewer_window_stays_visible_through_countdown_despite_camera_loss():
    """A mixed IMU+RGB trial where the camera transiently drops mid-
    countdown must not hide the countdown itself -- any one of camera-live/
    countdown-active/recording-active is enough to keep the window up."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_camera_live(True)
        r.update()
        p._start_countdown()
        r.update()
        p.set_camera_live(False)   # camera drops mid-countdown
        r.update()
        assert p._viewer_window.state() != "withdrawn"
        p.after_cancel(p._countdown_id)
    finally:
        r.destroy()


def test_enter_recording_opens_viewer_window_without_prior_countdown_or_camera():
    """Instant-start recording (countdown checkbox off) with no camera ever
    live must still open the window and show the REC marker."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p._viewer_window is None
        p.enter_recording()
        r.update()
        assert p._viewer_window is not None
        assert p._viewer_window.state() != "withdrawn"
        assert p._viewer_window.lbl_overlay.cget("text") == "● REC"
    finally:
        r.destroy()


def test_start_countdown_calls_on_countdown_start():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def on_countdown_start(self): calls.append("countdown_start")
        p = AcquisitionPanel(r, C()); p.pack()
        p.pid_var.set("P1")
        p.countdown_var.set(True)
        p._on_start_clicked()
        r.update()
        assert "countdown_start" in calls
        assert p._calib_extension_s == 0
    finally:
        r.destroy()




def test_tick_countdown_extends_when_not_calibrated():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return False
            def on_start(self): calls.append("start")
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = 0
        p._tick_countdown(0)
        r.update()
        assert "start" not in calls
        assert p._calib_extension_s == 1
        assert "Hold steady" in p.status_var.get()
    finally:
        r.destroy()




def test_tick_countdown_proceeds_when_calibrated():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return True
            def on_start(self): calls.append("start")
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = 0
        p._tick_countdown(0)
        r.update()
        assert "start" in calls
    finally:
        r.destroy()




def test_tick_countdown_confirm_dialog_accept_proceeds(monkeypatch):
    from pendulastic_app import AcquisitionPanel
    import pendulastic_app as _m
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return False
            def on_start(self): calls.append("start")
        monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **kw: True)
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = _m._MAX_CALIB_EXTENSION_S
        p._tick_countdown(0)
        r.update()
        assert "start" in calls
    finally:
        r.destroy()




def test_tick_countdown_confirm_dialog_decline_cancels(monkeypatch):
    from pendulastic_app import AcquisitionPanel
    import pendulastic_app as _m
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def is_imu_calibrated(self): return False
            def on_start(self): calls.append("start")
        monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **kw: False)
        p = AcquisitionPanel(r, C()); p.pack()
        p._calib_extension_s = _m._MAX_CALIB_EXTENSION_S
        p._tick_countdown(0)
        r.update()
        assert "start" not in calls
        assert p.btn_start.cget("text") == "START RECORDING"
    finally:
        r.destroy()




def test_countdown_status_shows_stabilizing_then_calibrated():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        class C(_Ctrl):
            calibrated = False
            def is_imu_calibrated(self): return self.calibrated
        ctrl = C()
        p = AcquisitionPanel(r, ctrl); p.pack()
        p._src_imu.set(True)
        p._tick_countdown(3)
        r.update()
        assert "stabilizing" in p.status_var.get()
        ctrl.calibrated = True
        p._tick_countdown(2)
        r.update()
        assert "calibrated" in p.status_var.get()
    finally:
        if p._countdown_id:
            p.after_cancel(p._countdown_id)
        r.destroy()




def test_zero_sensor_ui_removed():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert not hasattr(p, "btn_zero")
        assert not hasattr(p, "btn_clear_zero")
        assert not hasattr(p, "_zero_frame")
        assert not hasattr(p, "_on_zero_sensor")
        assert not hasattr(p, "_on_clear_zero")
    finally:
        r.destroy()




def test_countdown_locked_checked_when_imu_active():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(True)
        p._on_source_changed()
        r.update()
        assert p.countdown_var.get() is True
        assert str(p.countdown_chk.cget("state")) == "disabled"
        p._src_imu.set(False)
        p._on_source_changed()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "normal"
    finally:
        r.destroy()




def test_enter_idle_reapplies_countdown_lock():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(True)
        p._on_source_changed()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "disabled"
        # _lock_form(False) inside enter_idle would otherwise re-enable every
        # lockable widget including the countdown checkbox -- it must be
        # re-applied afterward so an IMU trial can't slip the countdown.
        p.enter_idle()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "disabled"
    finally:
        r.destroy()




def test_cancel_countdown_reapplies_countdown_lock():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(True)
        p._on_source_changed()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "disabled"

        p.pid_var.set("P1")
        p.countdown_var.set(True)
        p._on_start_clicked()
        r.update()
        assert p.btn_start.cget("text") == "CANCEL"

        # _lock_form(False) inside _cancel_countdown would otherwise
        # re-enable every lockable widget including the countdown checkbox
        # -- reopening the "uncheck it and skip calibration" bug. It must be
        # re-applied afterward so an IMU trial can't slip the countdown.
        p._cancel_countdown()
        r.update()
        assert str(p.countdown_chk.cget("state")) == "disabled"
        assert p.countdown_var.get() is True
    finally:
        r.destroy()


