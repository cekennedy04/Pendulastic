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
    def on_methodology_changed(self, m): pass
    def on_new_trial(self): pass


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
        assert p.leg_var.get()      == "Right"
        assert p.method_var.get()   == "optitrack"
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


def test_get_metadata_returns_correct_dict():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        p.pid_var.set("P7"); p.leg_var.set("Left")
        p.ms_var.set("Stroke"); p.trial_var.set("3")
        p.method_var.set("imu")
        assert p.get_metadata() == {
            "pid": "P7", "leg": "Left", "ms_status": "Stroke",
            "trial": 3, "methodology": "imu"}
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
