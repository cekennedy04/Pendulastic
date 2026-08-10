# tests/test_pendulastic_workbench.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

import numpy as np
import pytest

import pendulastic_storage

_root_window = None


@pytest.fixture(autouse=True)
def _isolated_participants_dir(tmp_path, monkeypatch):
    """Every test gets its own empty participants/ directory so tests never
    read/write real data or interfere with each other."""
    monkeypatch.setattr(pendulastic_storage, "PARTICIPANTS_DIR", str(tmp_path / "participants"))
    yield


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
    labels that never existed before.

    Uses flat (degenerate) traces rather than _traces()'s sinusoid: a
    non-degenerate shared shape lets Task 8's set_traces() auto-seed a
    release mark for both labels, and Task 9's now-real
    _recompute_release_lags() would then legitimately auto-fill a lag --
    which is the intended feature, not the "stale state" bug this test
    guards against. Flat traces keep this test isolated to that bug."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(90) / 30.0
    wv.set_traces({"imu": (t, np.full(90, 180.0))})
    r.update()

    wv.set_traces({"imu": (t, np.full(90, 180.0)),
                    "mediapipe": (t, np.full(90, 175.0))})
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


def test_get_metrics_snapshot_pt_score_is_healthy_for_clean_damped_signal():
    """A clean, healthy-shaped damped pendulum swing (a brief pre-release hold,
    then a decaying oscillation that settles well before the recording ends --
    what compute_pt_params's release-detection and tail-neutral logic expect
    from a real trial) must score in the healthy band. The composite score
    must come from compute_pt_params (the function HEALTHY_REF was actually
    calibrated against), not from windowed_pt_params (whose omega_min_n is a
    maximum, not the minimum compute_pt_score expects -- the mismatch that
    previously scored healthy signals as MAS 4 / maximum severity)."""
    from pendulastic_workbench import WorkbenchView
    import pendulastic_pt_score
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    fs = 100.0
    hold_s = 0.6
    total_s = 6.0
    t = np.arange(0, total_s, 1.0 / fs)
    hold_n = int(hold_s * fs)
    angle = np.empty_like(t)
    angle[:hold_n] = 180.0
    t_rel = t[hold_n:] - t[hold_n]
    angle[hold_n:] = 140.0 + 40.0 * np.exp(-0.3 * t_rel) * np.cos(2 * np.pi * 1.0 * t_rel)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    snapshot = wv.get_metrics_snapshot()
    pt = snapshot["per_trace"]["imu"]
    assert pt["pt_score"] is not None
    assert pt["pt_score"] < pendulastic_pt_score.PT_HEALTHY_MAX
    assert pt["mas"] in ("0", "1")


def test_get_metrics_snapshot_pt_score_none_for_insufficient_signal():
    """A too-short/flat trace (compute_pt_params returns None) must report
    pt_score=None and mas=None together -- never a fabricated 0.0 score or a
    None/string split between the two keys."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(0, 3.0, 1 / 60)
    angle = np.full_like(t, 180.0)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    snapshot = wv.get_metrics_snapshot()
    pt = snapshot["per_trace"]["imu"]
    assert pt["pt_score"] is None
    assert pt["mas"] is None


def _per_trace_row(wv, label):
    """Look up a labeled row's values tuple in the PER-TRACE METRICS
    Treeview, keyed by the column order in WorkbenchView._PER_TRACE_COLS."""
    for item in wv._per_trace_tree.get_children():
        values = wv._per_trace_tree.item(item)["values"]
        if values[0] == label:
            return dict(zip(wv._PER_TRACE_COLS, values))
    raise AssertionError(f"no per-trace row for label {label!r}")


def test_recompute_metrics_shows_pt_score_and_submetric_breakdown():
    """The PER-TRACE METRICS table must surface the disambiguated composite
    PT score (distinct from pendulastic_app.py's PT= line, which uses a
    different 4-parameter formula) and the full 7-parameter windowed
    breakdown per trace, restyled into the Treeview (post dark-theme
    restyle) rather than the old free-text readout."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.4 * t)
    angle = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    snapshot = wv.get_metrics_snapshot()
    pt = snapshot["per_trace"]["imu"]
    row = _per_trace_row(wv, "imu")
    assert row["pt_score"] == f"{pt['pt_score']:.3f}"
    # Treeview round-trips numeric-looking string values (e.g. mas="4") back
    # as int via Tcl, so compare as strings rather than assuming the type
    # inserted is the type read back.
    assert str(row["mas"]) == pt["mas"]
    assert row["R2n"] == f"{pt['R2n']:.3f}"
    assert row["phi_max_ratio"] == f"{pt['phi_max_ratio']:.3f}"
    assert row["omega_max_n"] == f"{pt['omega_max_n']:.3f}"
    assert row["omega_min_n"] == f"{pt['omega_min_n']:.3f}"


def test_recompute_metrics_shows_na_for_insufficient_signal():
    """A trace too short/flat for compute_pt_params must render an explicit
    'n/a' in the PT score/MAS columns rather than a fabricated 0.000 or a
    crash."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(0, 3.0, 1 / 60)
    angle = np.full_like(t, 180.0)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    row = _per_trace_row(wv, "imu")
    assert row["pt_score"] == "n/a"
    assert row["mas"] == "n/a"


def test_save_current_trial_persists_only_visible_traces():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    wv._visible_vars["optitrack"].set(False)   # hide optitrack
    r.update()

    wv._save_current_trial("test-p1", "left", "Initial", "2026-07-07")

    history = pendulastic_storage.load_history("test-p1")
    sessions = history["legs"]["left"]["sessions"]
    assert len(sessions) == 1
    saved_traces = sessions[0]["traces"]
    assert set(saved_traces.keys()) == {"imu"}
    assert "pt_score" in saved_traces["imu"]["metrics"]


def test_reference_trace_pt_score_returns_none_for_insufficient_signal():
    """A session without a usable PT score for its reference trace isn't
    useful to a longitudinal PT-score dashboard (design spec Section 7) --
    the save dialog's Save button refuses to proceed when this returns
    None. Tested directly against the helper rather than by simulating
    the Toplevel dialog's widgets."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.linspace(0, 4, 400)
    wv.set_traces({"flat": (t, np.full_like(t, 140.0))})   # flat signal -> insufficient
    r.update()
    assert wv._reference_var.get() == "flat"

    assert wv._reference_trace_pt_score() is None


def test_reference_trace_pt_score_returns_float_for_valid_signal():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    assert isinstance(wv._reference_trace_pt_score(), float)


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


def _is_packed(widget) -> bool:
    """pack_info() truthiness rather than winfo_ismapped() -- the latter
    requires the toplevel to actually be mapped on screen, which doesn't
    hold for the shared withdrawn test root (see _get_root()). Unlike
    grid_info() (which returns {} once removed), pack_info() raises
    TclError once a widget has been pack_forget()'d, so this normalizes
    that into the same truthiness check headlessly."""
    try:
        return bool(widget.pack_info())
    except tk.TclError:
        return False


def test_split_csv_format_hides_jsonl_row_and_shows_component_rows():
    from pendulastic_workbench import TrialLoadPanel
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()
    assert _is_packed(p._imu_jsonl_frame)
    assert not _is_packed(p._imu_split_frame)

    p._imu_format.set("split_csv")
    p._on_imu_format_changed()
    assert not _is_packed(p._imu_jsonl_frame)
    assert _is_packed(p._imu_split_frame)


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


def test_trial_load_panel_get_selection_includes_participant_and_session_date():
    from pendulastic_workbench import TrialLoadPanel
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    p.pack()
    p._participant_id.set("P5")
    p._session_date.set("2026-08-04")
    selection = p.get_selection()
    assert selection["participant_id"] == "P5"
    assert selection["session_date"] == "2026-08-04"


def test_trial_load_panel_session_date_defaults_to_today():
    import datetime
    from pendulastic_workbench import TrialLoadPanel
    r = _get_root()
    p = TrialLoadPanel(r, _Ctrl())
    expected = datetime.datetime.now().strftime("%Y-%m-%d")
    assert p.get_selection()["session_date"] == expected


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
                (np.array([0.0, 0.05]), np.array([180.0, 170.0]), validations["imu"]["rows"])),
        # set_traces() now synchronously calls _recompute_release_lags() ->
        # _recompute_metrics() -> get_metrics_snapshot(), which needs
        # windowed_pt_params on whatever `engine` is monkeypatched to.
        "windowed_pt_params": staticmethod(lambda t, y: {
            "R2n": 0.0, "N": 0.0, "phi_max_ratio": 0.0, "omega_max_n": 0.0,
            "f": 0.0, "area_ratio": 0.0, "omega_min_n": 0.0}),
        "extrema_jitter": staticmethod(lambda t, y: {
            "pk_i": np.array([], dtype=int), "tr_i": np.array([], dtype=int),
            "cycle_times": np.array([])}),
    })()
    monkeypatch.setattr(_m, "engine", fake_engine)

    app = App()
    try:
        app.update()
        app.on_load_trial({
            "imu_format": "split_csv", "imu_path": None, "imu_components": fake_validations,
            "video_path": None, "optitrack_path": None,
            "participant_id": "", "session_date": "",
            "models": [], "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert "imu" in app._workbench_view._traces
        assert app._trial_meta["imu_paths"] == {
            "accel": "a.csv", "gyro": "g.csv", "mag": "m.csv", "imu": "i.csv"}
        assert "imu_reference" not in app._trial_meta
        assert app._imu_reference == [{"hip_pitch_deg": "180.0"}]
    finally:
        app.destroy()


def test_workbench_view_per_trace_tree_populates_from_traces():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    rows = [wv._per_trace_tree.item(i)["values"] for i in wv._per_trace_tree.get_children()]
    labels = [row[0] for row in rows]
    assert "imu" in labels
    assert "optitrack" in labels


def test_dashboard_view_load_renders_three_axes_figure():
    import pendulastic_storage
    from pendulastic_workbench import DashboardView
    traces = {"imu": ([0.0, 0.1], [140.0, 138.0])}
    metrics = {"imu": {"R2n": 0.9, "N": 6.0, "phi_max_ratio": 0.8, "omega_max_n": 7.0,
                       "omega_min_n": 0.01, "f": 1.0, "area_ratio": 0.1,
                       "pt_score": 0.1, "mas": "0"}}
    pendulastic_storage.save_trial("test-dv1", "left", "Initial", "2026-07-07",
                                   traces, metrics, "imu")

    r = _get_root()
    dv = DashboardView(r, _Ctrl())
    dv.refresh_participants()
    dv._participant_var.set("TEST-DV1")
    dv._leg_var.set("left")
    dv._on_load_clicked()
    r.update()

    assert dv._trace_var.get() == "imu"
    assert dv._canvas is not None
    assert len(dv._canvas.figure.axes) == 3


def test_dashboard_view_load_unions_trace_labels_across_sessions():
    """Regression for a dropdown that only ever showed the first session's
    traces: two sessions in the same leg with disjoint trace labels must
    both surface as selectable entries in the trace dropdown after Load,
    not just whichever one _trace_var happens to land on."""
    import pendulastic_storage
    from pendulastic_workbench import DashboardView

    traces_1 = {"imu": ([0.0, 0.1], [140.0, 138.0])}
    metrics_1 = {"imu": {"R2n": 0.9, "N": 6.0, "phi_max_ratio": 0.8, "omega_max_n": 7.0,
                         "omega_min_n": 0.01, "f": 1.0, "area_ratio": 0.1,
                         "pt_score": 0.1, "mas": "0"}}
    pendulastic_storage.save_trial("test-dv3", "left", "Initial", "2026-07-07",
                                   traces_1, metrics_1, "imu")

    traces_2 = {"optitrack": ([0.0, 0.1], [142.0, 139.0])}
    metrics_2 = {"optitrack": {"R2n": 0.9, "N": 6.0, "phi_max_ratio": 0.8, "omega_max_n": 7.0,
                               "omega_min_n": 0.01, "f": 1.0, "area_ratio": 0.1,
                               "pt_score": 0.1, "mas": "0"}}
    pendulastic_storage.save_trial("test-dv3", "left", "Post-Training", "2026-07-21",
                                   traces_2, metrics_2, "optitrack")

    r = _get_root()
    dv = DashboardView(r, _Ctrl())
    dv.refresh_participants()
    dv._participant_var.set("TEST-DV3")
    dv._leg_var.set("left")
    dv._on_load_clicked()
    r.update()

    menu = dv._trace_menu["menu"]
    labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
    assert "imu" in labels
    assert "optitrack" in labels


def test_workbench_view_vs_ref_tree_shows_placeholder_when_no_traces():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    r.update()
    rows = [wv._vs_ref_tree.item(i)["values"] for i in wv._vs_ref_tree.get_children()]
    assert rows == [["No data yet", "", "", "", "", "", ""]]


def test_workbench_view_per_trace_tree_column_widths_are_fixed():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    r.update()
    for col in wv._PER_TRACE_COLS[1:]:
        assert wv._per_trace_tree.column(col)["stretch"] == 0


def test_export_csv_menu_disabled_when_no_traces_or_annotations():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces({})
    r.update()
    assert wv._export_csv_menu.entrycget(0, "state") == "disabled"
    assert wv._export_csv_menu.entrycget(1, "state") == "disabled"
    assert wv._export_csv_menu.entrycget(2, "state") == "disabled"
    assert wv._export_csv_menu.entrycget(3, "state") == "disabled"


def test_export_csv_menu_enables_traces_items_once_loaded_annotations_after_marking():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    assert wv._export_csv_menu.entrycget(0, "state") == "normal"
    assert wv._export_csv_menu.entrycget(3, "state") == "disabled"

    wv._scrub_var.set(10)
    wv._on_mark_milestone()
    r.update()
    assert wv._export_csv_menu.entrycget(3, "state") == "normal"


def test_export_traces_csv_writes_expected_rows(tmp_path, monkeypatch):
    from pendulastic_workbench import WorkbenchView
    import pendulastic_workbench as _m
    r = _get_root()

    class C(_Ctrl):
        def get_trial_meta(self):
            return {"participant_id": "P5", "session_date": "2026-08-04"}

    wv = WorkbenchView(r, C())
    wv.set_traces(_traces("imu"))
    r.update()

    out_path = tmp_path / "traces.csv"
    monkeypatch.setattr(_m.filedialog, "asksaveasfilename", lambda **kw: str(out_path))
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **kw: None)

    wv._on_export_traces_csv()

    import csv as _csv
    with open(out_path, newline="", encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    assert len(rows) == 100   # _traces() fixture: 100-point linspace
    assert rows[0]["participant_id"] == "P5"
    assert rows[0]["session_date"] == "2026-08-04"
    assert rows[0]["label"] == "imu"


def test_metrics_tables_are_visible_at_app_minimum_window_size():
    """Regression for the pack-order bug that made the vs-reference table
    invisible at App's own minsize(900, 600): the plot canvas was packed
    with expand=True *before* the tables, so it claimed its 400px requested
    figure height first and the tables were squeezed to 0 height (no
    scrollbar, no indication they existed). Uses a Toplevel of the shared
    root -- not a second tk.Tk() -- so it stays independent of whatever
    else the suite has packed into the shared root, and pack_propagate(False)
    pins it to exactly the app's minimum size."""
    from pendulastic_workbench import WorkbenchView
    import tkinter as _tk
    r = _get_root()
    top = _tk.Toplevel(r)
    try:
        top.withdraw()
        top.geometry("900x600")
        top.pack_propagate(False)
        r.update()

        wv = WorkbenchView(top, _Ctrl())
        wv.pack(fill="both", expand=True)
        wv.set_traces(_traces("imu", "optitrack"))
        r.update()
        r.update_idletasks()

        assert top.winfo_height() == 600
        assert wv._vs_ref_tree.winfo_height() > 0
        assert wv._per_trace_tree.winfo_height() > 0
    finally:
        top.destroy()


def test_export_traces_csv_excludes_hidden_traces(tmp_path, monkeypatch):
    """The per-trace and vs-reference CSVs are fed from get_metrics_snapshot(),
    which only covers *visible* traces. The traces CSV must apply the same
    filter, or unchecking a trace would drop it from two of the three
    exported CSVs while silently leaving it in the third."""
    from pendulastic_workbench import WorkbenchView
    import pendulastic_workbench as _m
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    wv._visible_vars["optitrack"].set(False)
    wv._on_visibility_changed()
    r.update()

    out_path = tmp_path / "traces.csv"
    monkeypatch.setattr(_m.filedialog, "asksaveasfilename", lambda **kw: str(out_path))
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **kw: None)
    wv._on_export_traces_csv()

    import csv as _csv
    with open(out_path, newline="", encoding="utf-8") as f:
        rows = list(_csv.DictReader(f))
    assert {row["label"] for row in rows} == {"imu"}
    assert len(rows) == 100


def test_set_raw_diagnostics_renders_both_values():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv.set_raw_diagnostics({"peak_gyro_velocity_dps": 245.3, "accel_release_time_sec": 1.02})
    r.update()
    text = wv._raw_diag_label.cget("text")
    assert "245.3" in text
    assert "1.02" in text


def test_set_raw_diagnostics_shows_unavailable_when_release_time_is_none():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv.set_raw_diagnostics({"peak_gyro_velocity_dps": 245.3, "accel_release_time_sec": None})
    r.update()
    text = wv._raw_diag_label.cget("text")
    assert "unavailable" in text.lower()


def test_set_raw_diagnostics_none_omits_section():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv.set_raw_diagnostics(None)
    r.update()
    text = wv._raw_diag_label.cget("text")
    assert "none loaded" in text


def test_dashboard_view_shows_skipped_session_status():
    import json
    import pendulastic_storage
    from pendulastic_workbench import DashboardView

    path = os.path.join(pendulastic_storage.PARTICIPANTS_DIR, "TEST-DV2")
    os.makedirs(path, exist_ok=True)
    raw = {"participant_id": "TEST-DV2",
          "legs": {"left": {"sessions": [{"label": "Broken", "date": "bad-date"}]},
                   "right": {"sessions": []}}}
    with open(os.path.join(path, "history.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f)

    r = _get_root()
    dv = DashboardView(r, _Ctrl())
    dv._participant_var.set("TEST-DV2")
    dv._leg_var.set("left")
    dv._on_load_clicked()
    r.update()

    assert "Skipped 1" in dv._status_var.get()


def test_load_panel_view_dashboard_button_switches_to_dashboard_view():
    from pendulastic_workbench import App
    app = App()
    try:
        app.update()
        app.on_view_dashboard()
        app.update()
        assert app._dashboard_view.winfo_ismapped()
        assert not app._load_panel.winfo_ismapped()
    finally:
        app.destroy()


def test_dashboard_back_returns_to_load_panel():
    from pendulastic_workbench import App
    app = App()
    try:
        app.update()
        app.on_view_dashboard()
        app.update()
        app.on_dashboard_back()
        app.update()
        assert app._load_panel.winfo_ismapped()
        assert not app._dashboard_view.winfo_ismapped()
    finally:
        app.destroy()


def test_milestone_labels_no_longer_include_release_start():
    from pendulastic_workbench import MILESTONE_LABELS
    assert "Release Start" not in MILESTONE_LABELS
    assert MILESTONE_LABELS == ["First Peak Extension", "Maximum Flexion", "Rest/Settled"]


def test_workbench_view_starts_with_empty_release_mark_state():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    assert wv._release_marks == {}
    assert wv._lag_provenance == {}
    assert wv._armed_release_label is None


def test_mark_release_button_and_entry_exist_per_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    assert "imu" in wv._release_buttons
    assert "optitrack" in wv._release_entry_vars


def test_arm_release_sets_armed_label_and_button_text():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    wv._on_arm_release("imu")
    r.update()

    assert wv._armed_release_label == "imu"
    assert "Click" in wv._release_buttons["imu"].cget("text")


def test_arm_release_disarms_previous_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()

    wv._on_arm_release("imu")
    wv._on_arm_release("optitrack")
    r.update()

    assert wv._armed_release_label == "optitrack"
    assert wv._release_buttons["imu"].cget("text") == "Mark Release"


def test_release_entry_commit_stores_manual_mark():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._release_entry_vars["imu"].set("1.5")
    wv._on_release_entry_commit("imu")
    r.update()

    assert wv._release_marks["imu"] == {"t_trace": 1.5, "source": "manual"}


def test_release_entry_commit_ignores_unparseable_text():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    # set_traces() auto-seeds via detect_release_t0 (Task 8); capture
    # whatever it produced (if anything) as the baseline this commit must
    # leave untouched, rather than assuming an empty _release_marks.
    before = dict(wv._release_marks)

    wv._release_entry_vars["imu"].set("not-a-number")
    wv._on_release_entry_commit("imu")
    r.update()

    assert wv._release_marks == before


def test_clear_release_removes_mark_and_resets_entry():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._release_marks["imu"] = {"t_trace": 1.0, "source": "manual"}

    wv._on_clear_release("imu")
    r.update()

    assert "imu" not in wv._release_marks
    assert wv._release_entry_vars["imu"].get() == ""


def test_lag_entry_manual_edit_sets_manual_provenance():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._lag_override_vars["imu"].set("0.42")
    wv._on_lag_entry_commit("imu")
    r.update()

    assert wv._lag_provenance["imu"] == "manual"


def test_release_artist_redraw_survives_axes_clear():
    """set_traces() calls self._ax.clear() on every invocation (e.g. the
    async-HPE-results reload case), which invalidates any previously drawn
    matplotlib artists -- .remove() on such an artist raises
    NotImplementedError. _release_artists entries are not reset by
    set_traces() itself (that's Task 8's job), so _draw_release_artist and
    _on_clear_release must tolerate a stale/invalidated artist without
    crashing regardless of when Task 8 lands."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._draw_release_artist("imu", 1.0)
    wv._ax.clear()  # simulates what set_traces()'s ax.clear() does mid-reload

    # Neither call should raise NotImplementedError on the now-invalidated artists.
    wv._draw_release_artist("imu", 2.0)
    wv._on_clear_release("imu")


class _FakeClickEvent:
    def __init__(self, inaxes, xdata):
        self.inaxes = inaxes
        self.xdata = xdata


def test_plot_click_while_armed_marks_release_and_disarms():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._on_arm_release("imu")

    t_arr, _ = wv._traces["imu"]
    click_x = float(t_arr[10])
    wv._on_plot_click(_FakeClickEvent(inaxes=wv._ax, xdata=click_x))
    r.update()

    assert wv._release_marks["imu"]["t_trace"] == pytest.approx(t_arr[10])
    assert wv._release_marks["imu"]["source"] == "manual"
    assert wv._armed_release_label is None
    assert wv._release_buttons["imu"].cget("text") == "Mark Release"


def test_plot_click_snaps_to_nearest_sample():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()
    wv._on_arm_release("imu")

    t_arr, _ = wv._traces["imu"]
    # Click slightly off-grid, between sample 10 and 11 but closer to 10.
    click_x = float(t_arr[10]) + 0.3 * (float(t_arr[11]) - float(t_arr[10]))
    wv._on_plot_click(_FakeClickEvent(inaxes=wv._ax, xdata=click_x))

    assert wv._release_marks["imu"]["t_trace"] == pytest.approx(t_arr[10])


def test_plot_click_without_arming_falls_back_to_video_seek():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    wv._fps = 30.0
    wv._n_frames = 100
    r.update()
    # set_traces() auto-seeds via detect_release_t0 (Task 8); capture
    # whatever it produced (if anything) as the baseline this click-without-
    # arming must leave untouched, rather than assuming an empty
    # _release_marks.
    before = dict(wv._release_marks)

    wv._on_plot_click(_FakeClickEvent(inaxes=wv._ax, xdata=1.0))
    r.update()

    assert wv._release_marks == before
    assert wv._scrub_var.get() == pytest.approx(30.0)


def test_remarking_same_milestone_does_not_leak_axvline():
    """Regression: _draw_milestone_artist only ever removed the old text
    annotation, never the old axvline, so re-marking the same milestone
    within one session (without an intervening set_traces() call, which
    would have wiped it via _ax.clear()) accumulated stray dashed lines."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu"))
    r.update()

    wv._scrub_var.set(10)
    wv._on_mark_milestone()
    lines_after_first_mark = len(wv._ax.lines)

    wv._scrub_var.set(20)
    wv._on_mark_milestone()
    r.update()

    assert len(wv._ax.lines) == lines_after_first_mark, (
        "re-marking the same milestone must replace, not accumulate, its axvline")


def _release_traces(*labels, n=120, fps=30.0):
    """Like _traces(), but with a real hold-then-swing release event (not a
    pure sine from t=0) so detect_release_t0 has something to find."""
    t = np.arange(n) / fps
    out = {}
    for i, label in enumerate(labels):
        ang = np.full(n, 180.0)
        hold = int(fps)
        for j in range(hold, n):
            tj = (j - hold) / fps
            ang[j] = 165.0 - i + 15.0 * np.exp(-0.3 * tj) * np.cos(2 * np.pi * 0.9 * tj)
        out[label] = (t, ang)
    return out


def test_set_traces_auto_seeds_release_mark_for_new_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_release_traces("imu"))
    r.update()

    assert "imu" in wv._release_marks
    assert wv._release_marks["imu"]["source"] == "auto"
    assert 0.8 <= wv._release_marks["imu"]["t_trace"] <= 1.2


def test_set_traces_leaves_degenerate_trace_unmarked_no_exception():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(90) / 30.0
    flat = {"imu": (t, np.full(90, 180.0))}

    wv.set_traces(flat)   # must not raise
    r.update()

    assert "imu" not in wv._release_marks


def test_set_traces_does_not_reseed_already_marked_trace():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    traces = _release_traces("imu")
    wv.set_traces(traces)
    r.update()
    wv._release_marks["imu"] = {"t_trace": 99.0, "source": "manual"}

    wv.set_traces(traces)  # same trial's async-reload style re-call
    r.update()

    assert wv._release_marks["imu"] == {"t_trace": 99.0, "source": "manual"}


def test_recompute_release_lags_fills_auto_field_from_marks():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("optitrack")
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "manual"}
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "auto"}

    wv._recompute_release_lags()
    r.update()

    assert wv._lag_override_vars["imu"].get() == "1.500"
    assert wv._lag_provenance["imu"] == "auto"


def test_recompute_release_lags_never_overwrites_manual_provenance():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("optitrack")
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "manual"}
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "manual"}
    wv._lag_override_vars["imu"].set("9.999")
    wv._lag_provenance["imu"] = "manual"

    wv._recompute_release_lags()
    r.update()

    assert wv._lag_override_vars["imu"].get() == "9.999"


def test_recompute_release_lags_clears_stale_auto_field_when_mark_removed():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._reference_var.set("optitrack")
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "manual"}
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "auto"}
    wv._recompute_release_lags()
    r.update()
    assert wv._lag_override_vars["imu"].get() == "1.500"

    del wv._release_marks["imu"]
    wv._recompute_release_lags()
    r.update()

    assert wv._lag_override_vars["imu"].get() == ""


def test_recompute_release_lags_noop_when_neither_marked():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    # _traces()'s sinusoid is non-degenerate, so Task 8's set_traces()
    # auto-seed already populated both labels' _release_marks; explicitly
    # clear them here so this test actually exercises the "neither marked"
    # case its name promises, rather than the auto-fill path.
    wv._release_marks.pop("imu", None)
    wv._release_marks.pop("optitrack", None)
    wv._reference_var.set("optitrack")

    wv._recompute_release_lags()   # must not raise
    r.update()

    assert wv._lag_override_vars["imu"].get() == ""


def test_reference_change_triggers_release_lag_recompute():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    wv.set_traces(_traces("imu", "optitrack"))
    r.update()
    wv._release_marks["imu"] = {"t_trace": 0.5, "source": "auto"}
    wv._release_marks["optitrack"] = {"t_trace": 2.0, "source": "auto"}

    wv._reference_var.set("optitrack")
    r.update()

    assert wv._lag_override_vars["imu"].get() == "1.500"
