# tests/test_pendulastic_workbench.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

import numpy as np

_root_window = None


def _get_root():
    global _root_window
    if _root_window is None:
        _root_window = tk.Tk()
        _root_window.withdraw()
    return _root_window


class _Ctrl:
    def get_trial_meta(self):
        return {}


def _traces(*labels):
    t = np.linspace(0, 5, 100)
    return {label: (t, 180 - 40 * np.sin(t) + i) for i, label in enumerate(labels)}


def test_set_traces_preserves_reference_lag_and_visibility_across_reload():
    """Regression for the async-HPE-completion state-clobbering bug: a
    researcher configuring their comparison while video HPE models are
    still running (design spec Section 3's "slow step") must not have
    their reference/lag/visibility choices silently reset when the
    results merge in and set_traces() runs a second time."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    wv._reference_var.set("imu")
    wv._lag_override_vars["imu"].set("0.25")
    wv._visible_vars["optitrack"].set(False)
    wv._on_visibility_changed()
    r.update()

    wv.set_traces(_traces("imu", "optitrack", "mediapipe"))
    r.update()

    assert wv._reference_var.get() == "imu"
    assert wv._lag_override_vars["imu"].get() == "0.25"
    assert wv._visible_vars["optitrack"].get() is False
    assert wv._trace_lines["optitrack"].get_visible() is False


def test_set_traces_new_label_gets_defaults():
    """A genuinely new trace arriving in a later set_traces() call must
    still get sensible defaults (visible, no lag override) -- the fix for
    preserving prior state must not accidentally apply stale state to
    labels that never existed before."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv.set_traces(_traces("imu", "mediapipe"))
    r.update()

    assert wv._visible_vars["mediapipe"].get() is True
    assert wv._lag_override_vars["mediapipe"].get() == ""


def test_set_traces_falls_back_to_default_reference_when_prior_reference_gone():
    """If the previously-selected reference trace is no longer present in
    a later set_traces() call (e.g. a fresh trial load), the default
    reference logic must still kick in rather than leaving a reference
    pointing at a trace that no longer exists."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("imu")
    r.update()

    wv.set_traces(_traces("optitrack", "mediapipe"))
    r.update()

    assert wv._reference_var.get() == "optitrack"


def test_milestone_annotation_survives_second_set_traces_call():
    """The milestone marker data (used by get_annotations()/export) already
    survives a second set_traces() call, but the plotted visual artist
    (the red dashed line + label) was silently wiped by _ax.clear() and
    never redrawn -- confusing a researcher into thinking their milestone
    was lost when async HPE results merge in."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._scrub_var.set(30)
    wv._on_mark_milestone()
    r.update()
    marked_label = wv._pending_milestone.get()
    assert marked_label in wv.get_annotations()

    wv.set_traces(_traces("imu", "mediapipe"))
    r.update()

    assert marked_label in wv.get_annotations(), "annotation data must survive"
    assert marked_label in wv._annotation_artists, "visual marker must be redrawn"
    assert any(line.get_color() == "#DC2626" for line in wv._ax.lines), \
        "milestone axvline must reappear on the rebuilt plot"


def test_set_traces_repositions_scrub_indicator_to_current_time():
    """The gray scrub-position indicator line was unconditionally reset to
    x=0 on every set_traces() call, snapping back visually even though the
    researcher had already scrubbed elsewhere and self._scrub_var hadn't
    changed -- it must track the actual current scrub position instead."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._scrub_var.set(40)
    expected_t = wv.current_time_sec()

    wv.set_traces(_traces("imu", "mediapipe"))
    r.update()

    assert wv._axvline.get_xdata()[0] == expected_t


def test_imu_jsonl_browse_button_only_accepts_jsonl(monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()

    captured = {}
    def fake_askopenfilename(**kwargs):
        captured.update(kwargs)
        return ""
    monkeypatch.setattr(_m.filedialog, "askopenfilename", fake_askopenfilename)

    p._browse_buttons["imu"].invoke()

    exts = " ".join(pattern for _label, pattern in captured["filetypes"])
    assert "*.jsonl" in exts
    assert "*.csv" not in exts


def test_split_csv_format_hides_jsonl_row_and_shows_component_rows():
    # Uses grid_info() truthiness rather than winfo_ismapped() -- the
    # latter requires the toplevel to actually be mapped on screen, which
    # doesn't hold for the shared withdrawn test root (see _get_root()).
    # grid_info() returns {} once a widget is grid_remove()'d and a
    # populated dict once it's grid()'d back, without needing a real
    # screen render, so it verifies the same "which frame is currently
    # gridded" behavior headlessly.
    from pendulastic_workbench import TrialLoadPanel
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()
    assert p._imu_jsonl_frame.grid_info()
    assert not p._imu_split_frame.grid_info()

    p._imu_format.set("split_csv")
    p._on_imu_format_changed()
    assert not p._imu_jsonl_frame.grid_info()
    assert p._imu_split_frame.grid_info()


def test_component_browse_validates_and_updates_status(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()

    path = tmp_path / "Trial_1_accel.csv"
    path.write_text(
        "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"
        "0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n"
        "10.0,10,proximal,Accelerometer,0.0,0.0,9.81\n",
        encoding="utf-8")
    monkeypatch.setattr(_m.filedialog, "askopenfilename", lambda **kw: str(path))

    p._browse_component("accel")

    assert p._component_paths["accel"].get() == str(path)
    assert p._component_validations["accel"]["ok"] is True
    assert "100.0 Hz" in p._component_status["accel"].get()
    assert p._component_validations["accel"]["path"] == str(path)


def test_component_browse_shows_error_status_on_invalid_file(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()

    path = tmp_path / "bad.csv"
    path.write_text("wrong,header\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(_m.filedialog, "askopenfilename", lambda **kw: str(path))

    p._browse_component("gyro")

    assert p._component_validations["gyro"]["ok"] is False
    assert p._component_status["gyro"].get().startswith("✗")


def test_load_trial_blocks_on_incomplete_split_csv_slots(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_load_trial(self, selection):
            calls.append(selection)
    p = TrialLoadPanel(r, C())
    p.pack()
    p._imu_format.set("split_csv")
    p._on_imu_format_changed()

    path = tmp_path / "Trial_1_accel.csv"
    path.write_text(
        "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n"
        "0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n"
        "10.0,10,proximal,Accelerometer,0.0,0.0,9.81\n",
        encoding="utf-8")
    monkeypatch.setattr(_m.filedialog, "askopenfilename", lambda **kw: str(path))
    p._browse_component("accel")   # only 1 of 4 filled

    errors = []
    monkeypatch.setattr(_m.messagebox, "showerror", lambda title, msg: errors.append(msg))
    p._on_load_clicked()

    assert calls == []
    assert len(errors) == 1
    assert "gyro" in errors[0] and "mag" in errors[0] and "imu" in errors[0]


def test_load_trial_proceeds_when_all_four_split_csv_slots_are_valid(tmp_path, monkeypatch):
    from pendulastic_workbench import TrialLoadPanel
    import pendulastic_workbench as _m
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_load_trial(self, selection):
            calls.append(selection)
    p = TrialLoadPanel(r, C())
    p.pack()
    p._imu_format.set("split_csv")
    p._on_imu_format_changed()

    csv_bodies = {
        "accel": "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n0.0,0,proximal,Accelerometer,0.0,0.0,9.81\n10.0,10,proximal,Accelerometer,0.0,0.0,9.81\n",
        "gyro":  "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n0.0,0,proximal,Gyroscope,0.0,0.0,0.0\n10.0,10,proximal,Gyroscope,0.0,0.0,0.0\n",
        "mag":   "timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z\n0.0,0,proximal,Magnetometer,-50.0,20.0,30.0\n10.0,10,proximal,Magnetometer,-50.0,20.0,30.0\n",
        "imu":   "t_epoch,t_rel,phone_ts_ms,t_phone_aligned,hip_roll_deg,hip_pitch_deg,hip_yaw_deg,prox_roll,prox_pitch,prox_yaw,dist_roll,dist_pitch,dist_yaw,paired\n"
                 "1700000000.0,0.0,0,1700000000.0,0.0,180.0,0.0,0.0,90.0,0.0,0.0,90.0,0.0,True\n"
                 "1700000000.01,0.01,10,1700000000.01,0.0,180.0,0.0,0.0,90.0,0.0,0.0,90.0,0.0,True\n",
    }
    paths = {}
    for kind, body in csv_bodies.items():
        path = tmp_path / f"Trial_1_{kind}.csv"
        path.write_text(body, encoding="utf-8")
        paths[kind] = path

    _next_kind = ["accel"]
    monkeypatch.setattr(_m.filedialog, "askopenfilename",
                        lambda **kw: str(paths[_next_kind[0]]))
    for kind in ("accel", "gyro", "mag", "imu"):
        _next_kind[0] = kind
        p._browse_component(kind)

    p._on_load_clicked()

    assert len(calls) == 1
    selection = calls[0]
    assert selection["imu_format"] == "split_csv"
    assert all(selection["imu_components"][k]["ok"] for k in ("accel", "gyro", "mag", "imu"))


def test_trial_load_panel_back_button_calls_controller():
    from pendulastic_workbench import TrialLoadPanel
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_back_to_mode_select(self):
            calls.append("back")
    p = TrialLoadPanel(r, C())
    p.pack()
    p._back_button.invoke()
    assert calls == ["back"]


def test_workbench_view_load_another_button_calls_controller():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_workbench_load_another(self):
            calls.append("load_another")
    wv = WorkbenchView(r, C())
    wv.pack()
    wv._load_another_button.invoke()
    assert calls == ["load_another"]


def test_standalone_app_back_to_mode_select_is_a_genuine_noop():
    from pendulastic_workbench import App
    app = App()
    try:
        app.update()
        app._load_panel.pack_forget()
        app._workbench_view.pack(fill="both", expand=True)
        app.update()
        app.on_back_to_mode_select()   # must not raise
        app.update()
        # Still showing whatever was showing before -- nothing changed.
        assert app._workbench_view.winfo_ismapped()
        assert not app._load_panel.winfo_ismapped()
    finally:
        app.destroy()


def test_standalone_app_load_another_returns_to_load_panel():
    from pendulastic_workbench import App
    app = App()
    try:
        app.update()
        app._load_panel.pack_forget()
        app._workbench_view.pack(fill="both", expand=True)
        app.update()
        app.on_workbench_load_another()
        app.update()
        assert app._load_panel.winfo_ismapped()
        assert not app._workbench_view.winfo_ismapped()
    finally:
        app.destroy()


def test_on_load_trial_split_csv_binds_and_stores_imu_reference(tmp_path, monkeypatch):
    from pendulastic_workbench import App
    import pendulastic_workbench as _m
    import numpy as np

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
    monkeypatch.setattr(_m, "engine", fake_engine)

    app = App()
    try:
        app.update()
        app.on_load_trial({
            "imu_format": "split_csv", "imu_path": None, "imu_components": fake_validations,
            "video_path": None, "optitrack_path": None, "models": [],
            "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert "imu" in app._workbench_view._traces
        assert app._trial_meta["imu_paths"] == {
            "accel": "a.csv", "gyro": "g.csv", "mag": "m.csv", "imu": "i.csv"}
        assert app._trial_meta["imu_reference"] == [{"hip_pitch_deg": "180.0"}]
    finally:
        app.destroy()
