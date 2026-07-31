# tests/test_acquisition_panel.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk


def _root():
    r = tk.Tk(); r.withdraw(); return r


class _Ctrl:
    """Minimal fake controller."""
    def on_start(self): pass
    def on_stop(self): pass
    def on_source_changed(self, sources): pass
    def on_new_trial(self): pass
    def on_back_to_mode_select(self): pass
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
        # Multi-source: optitrack checked by default, others unchecked
        assert p._src_optitrack.get() is True
        assert p._src_rgb.get() is False
        assert p._src_imu.get() is False
        assert p.countdown_var.get() is False
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


def test_zero_sensor_button_hidden_when_imu_unchecked():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(False)
        p._on_source_changed()
        r.update()
        # _zero_frame (containing btn_zero + btn_clear_zero) should be removed from grid
        assert p._zero_frame.grid_info() == {}
    finally:
        r.destroy()


def test_zero_sensor_button_shown_when_imu_checked():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_imu.set(True)
        p._on_source_changed()
        r.update()
        # _zero_frame should be in the grid; btn_zero widget must also exist
        assert p._zero_frame.grid_info() != {}
        assert hasattr(p, "btn_zero") and p.btn_zero.winfo_exists()
    finally:
        r.destroy()


def test_preview_label_exists():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert hasattr(p, "lbl_preview"), "lbl_preview widget must exist"
        assert p.lbl_preview.winfo_exists()
    finally:
        r.destroy()


def test_rgb_source_swaps_to_preview_during_recording():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_rgb.set(True)
        p._on_source_changed()
        p.enter_recording()
        r.update()
        # preview label must be gridded; sparkline must be hidden
        assert p.lbl_preview.grid_info() != {}, "lbl_preview should be visible during RGB recording"
        assert p.canvas_tele.grid_info() == {}, "canvas_tele should be hidden during RGB recording"
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
        p._src_video_file.set(True)
        p._stored_video_path = "/data/trial.mp4"
        meta = p.get_metadata()
        assert meta["video_file_path"] == "/data/trial.mp4"
        assert "video_file" in meta["sources"]
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
