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


def test_imu_browse_button_accepts_csv_and_jsonl(monkeypatch):
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
    assert "*.csv" in exts


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
