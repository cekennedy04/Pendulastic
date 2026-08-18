# tests/test_analysis_panel.py
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

from matplotlib.figure import Figure

import pt_report_common as _real_report


def _root():
    r = tk.Tk(); r.withdraw(); return r


class _Ctrl:
    def on_back_to_mode_select(self): pass


class _FakeReport:
    """Fast, deterministic stand-in for pt_report_common -- no real trial
    CSVs or matplotlib backend involved. Records calls so tests can assert
    on what AnalysisPanel asked it to do."""

    def __init__(self):
        self.calls = []
        self.participants = {
            "1": {"legs": {"left", "right"}, "n_trials": 4, "conditions": {"pre", "post"}},
            "2": {"legs": {"left"}, "n_trials": 2, "conditions": {"pre"}},
        }
        self.records = [
            {"participant": "1", "leg": "left", "condition": "pre", "trial": "1",
             "path": "/rec/P1_left_pre_trial_1.csv", "mtime": 0.0,
             "trial_key": "1_left_pre_T1", "excluded": False},
            {"participant": "1", "leg": "left", "condition": "pre", "trial": "2",
             "path": "/rec/P1_left_pre_trial_2.csv", "mtime": 0.0,
             "trial_key": "1_left_pre_T2", "excluded": False},
        ]

    def list_participants(self, include_excluded=False):
        return dict(self.participants)

    def collect_participant(self, pid):
        self.calls.append(("collect", pid))
        return ({}, [("pre", "Pre", "#111111")])

    def _fig(self):
        fig = Figure(figsize=(1, 1))
        fig.add_subplot(111)
        return fig

    def make_report_figure(self, *args, **kwargs):
        self.calls.append(("report", args, kwargs))
        return "fake_report.png", self._fig()

    def make_comparison_figure(self, *args, **kwargs):
        self.calls.append(("comparison", args, kwargs))
        return "fake_comparison.png", self._fig()

    def make_rmse_figure(self, *args, **kwargs):
        self.calls.append(("rmse", args, kwargs))
        return "fake_rmse.png", self._fig()

    def discover_all_trials(self, include_archive=True, include_excluded=False):
        self.calls.append(("discover_all_trials", include_excluded))
        if include_excluded:
            return list(self.records)
        return [r for r in self.records if not r["excluded"]]

    def duplicate_trial_keys(self, records):
        self.calls.append(("duplicate_trial_keys", len(records)))
        by_key = {}
        for r in records:
            by_key.setdefault(r["trial_key"], []).append(r["path"])
        return {k: v for k, v in by_key.items() if len(v) > 1}

    def set_trials_excluded(self, keys, excluded):
        self.calls.append(("set_trials_excluded", list(keys), excluded))
        for r in self.records:
            if r["trial_key"] in keys:
                r["excluded"] = excluded

    RegistryCorruptError = _real_report.RegistryCorruptError


def _wait_until_enabled(panel, root, timeout=5.0):
    deadline = time.time() + timeout
    while panel.btn_generate.cget("state") == "disabled" and time.time() < deadline:
        root.update()
        time.sleep(0.02)


def test_panel_instantiates():
    from pendulastic_app import AnalysisPanel
    r = _root()
    try:
        p = AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        assert p.btn_save.cget("state") == "disabled"
    finally:
        r.destroy()


def test_panel_uses_shared_palette():
    """AnalysisPanel post-dates the style-unification plan and was left on
    the old default-gray look; it should match every other restyled screen."""
    from pendulastic_app import AnalysisPanel
    import workbench_style as ws
    r = _root()
    try:
        p = AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        assert str(p.cget("bg")) == ws.PALETTE["BG"]
    finally:
        r.destroy()


def test_refresh_participants_populates_listbox(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        assert p._participant_list.size() == 2
        assert "2 participant(s) found" in p.status_var.get()
    finally:
        r.destroy()


def test_figure_type_change_toggles_methodology_frame(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_report", _FakeReport())
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        assert str(p._method_frame.winfo_children()[0].cget("state")) == "disabled"
        p._figure_type.set("rmse")
        p._on_figure_type_changed()
        assert str(p._method_frame.winfo_children()[0].cget("state")) == "normal"
    finally:
        r.destroy()


def test_generate_requires_exact_participant_count(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_report", _FakeReport())
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    infos = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda title, msg: infos.append(msg))
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        p._figure_type.set("comparison")
        # only 0 selected, comparison needs exactly 2
        p._on_generate()
        assert len(infos) == 1
        assert "2 participant(s)" in infos[0]
    finally:
        r.destroy()


def test_generate_full_report_runs_off_main_thread_and_shows_figure(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._figure_type.set("full_report")

        p._on_generate()
        assert p.btn_generate.cget("state") == "disabled"
        _wait_until_enabled(p, r)

        assert p.btn_generate.cget("state") == "normal"
        assert p._last_out_path == "fake_report.png"
        assert p.btn_save.cget("state") == "normal"
        assert "Done" in p.status_var.get()
        # collect_participant (the slow part) ran in _generate_worker;
        # make_report_figure ran afterward, in _poll_result on the main thread.
        assert [c[0] for c in fake.calls] == ["collect", "report"]
    finally:
        r.destroy()


def test_generate_comparison_passes_both_participants_data(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0, 1)
        p._figure_type.set("comparison")

        p._on_generate()
        _wait_until_enabled(p, r)

        assert p._last_out_path == "fake_comparison.png"
        assert [c[0] for c in fake.calls] == ["collect", "collect", "comparison"]
    finally:
        r.destroy()


def test_generate_rmse_passes_selected_methodologies(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._figure_type.set("rmse")
        p._use_mediapipe.set(True)
        p._use_imu.set(False)

        p._on_generate()
        _wait_until_enabled(p, r)

        assert p._last_out_path == "fake_rmse.png"
        _, _, kwargs = fake.calls[-1]
        assert kwargs["methodologies"] == ("mediapipe",)
    finally:
        r.destroy()


def test_generate_worker_error_reenables_button_and_shows_error(monkeypatch):
    import pendulastic_app as _m

    class _FailingReport(_FakeReport):
        def collect_participant(self, pid):
            raise RuntimeError("boom")

    monkeypatch.setattr(_m, "_report", _FailingReport())
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    errors = []
    monkeypatch.setattr(_m.messagebox, "showerror", lambda title, msg: errors.append(msg))
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._figure_type.set("full_report")

        p._on_generate()
        _wait_until_enabled(p, r)

        assert p.btn_generate.cget("state") == "normal"
        assert len(errors) == 1
        assert "boom" in errors[0]
        assert "Failed" in p.status_var.get()
    finally:
        r.destroy()


def test_refresh_participants_labels_fully_excluded_participant(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.participants["3"] = {"legs": set(), "conditions": set(), "n_trials": 0}
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        labels = [p._participant_list.get(i) for i in range(p._participant_list.size())]
        assert any("(all excluded)" in lbl and lbl.startswith("P3") for lbl in labels)
        assert not any("(all excluded)" in lbl for lbl in labels if lbl.startswith("P1"))
    finally:
        r.destroy()


def test_refresh_participants_calls_list_participants_with_include_excluded(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    calls = []
    orig = fake.list_participants
    fake.list_participants = lambda include_excluded=False: (
        calls.append(include_excluded), orig(include_excluded))[1]
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        assert calls == [True]
    finally:
        r.destroy()


def test_table_hidden_and_figure_shown_by_default():
    from pendulastic_app import AnalysisPanel
    r = _root()
    try:
        p = AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        assert p._table_frame.winfo_manager() == ""
        assert p._viewer_canvas.winfo_manager() == "grid"
    finally:
        r.destroy()


def test_single_selection_switches_to_table_view(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while p._table_frame.winfo_manager() != "grid" and time.time() < deadline:
            r.update(); time.sleep(0.02)
        assert p._table_frame.winfo_manager() == "grid"
        assert p._viewer_canvas.winfo_manager() == ""
        assert p._viewer_vbar.winfo_manager() == ""
        assert p._viewer_hbar.winfo_manager() == ""
    finally:
        r.destroy()


def test_zero_or_multi_selection_reverts_to_figure_view(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0, 1)
        p._on_participant_selection_changed()
        r.update()
        assert p._table_frame.winfo_manager() == ""
        assert p._viewer_canvas.winfo_manager() == "grid"
        assert p.btn_toggle_excluded.cget("state") == "disabled"
    finally:
        r.destroy()


def test_table_populates_with_scored_trials(monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", True)
    monkeypatch.setattr(_m, "load_optitrack",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 170.0])))
    monkeypatch.setattr(_m, "compute_pt_params",
                        lambda t, angle: {"N": 4.0, "phi_max_ratio": 0.63871, "area_ratio": 0.0497})
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        items = p._trial_table.get_children()
        assert len(items) == 2
        vals = p._trial_table.item(items[0], "values")
        assert vals[4] == "4.0"       # N, 1 decimal
        assert vals[5] == "0.639"     # phi_max_ratio, 3 decimals
        assert vals[6] == "0.050"     # area_ratio, 3 decimals
    finally:
        r.destroy()


def test_table_shows_na_for_failed_scoring(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", True)

    def raising_load(path):
        raise ValueError("bad csv")

    monkeypatch.setattr(_m, "load_optitrack", raising_load)
    monkeypatch.setattr(_m, "compute_pt_params", lambda t, angle: None)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        vals = p._trial_table.item(p._trial_table.get_children()[0], "values")
        assert vals[4] == vals[5] == vals[6] == "N/A"
    finally:
        r.destroy()


def test_table_marks_duplicate_trial_keys(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.records.append({
        "participant": "1", "leg": "left", "condition": "pre", "trial": "1",
        "path": "/rec_dup/P1_left_pre_trial_1.csv", "mtime": 0.0,
        "trial_key": "1_left_pre_T1", "excluded": False,
    })
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        warn_col_values = [p._trial_table.item(i, "values")[0] for i in p._trial_table.get_children()]
        assert warn_col_values.count("⚠") == 2
    finally:
        r.destroy()


def test_rapid_reselection_drops_stale_table_result(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()

        # Simulate a superseded request: manually post a stale-id result
        # directly onto the queue, then a current one, and confirm only the
        # current one's rows land.
        p._table_request_id = 5
        stale_record = dict(fake.records[0], trial="99")
        p._table_queue.put(("ok", (4, [(stale_record, None, None, None)], {}), None))
        p._table_queue.put(("ok", (5, [(fake.records[0], None, None, None)], {}), None))
        p.after(0, p._poll_table_queue)
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        vals = [p._trial_table.item(i, "values")[3] for i in p._trial_table.get_children()]
        assert "99" not in vals
        assert fake.records[0]["trial"] in vals
    finally:
        r.destroy()


def _select_and_wait_for_table(p, r, idx=0):
    p._participant_list.selection_set(idx)
    p._on_participant_selection_changed()
    deadline = time.time() + 5
    while not p._trial_table.get_children() and time.time() < deadline:
        r.update(); time.sleep(0.02)


def test_toggle_excluded_calls_set_trials_excluded_with_deduped_keys(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.records.append({
        "participant": "1", "leg": "left", "condition": "pre", "trial": "1",
        "path": "/rec_dup/P1_left_pre_trial_1.csv", "mtime": 0.0,
        "trial_key": "1_left_pre_T1", "excluded": False,
    })
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **k: True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        # Selects all 3 rows for this participant: the base fixture's
        # "1_left_pre_T1"/"1_left_pre_T2" plus the appended row that
        # collides with "1_left_pre_T1". "Deduped" means the colliding
        # key is passed once (not twice), not that the unrelated
        # "1_left_pre_T2" key is dropped from the selection.
        p._trial_table.selection_set(*p._trial_table.get_children())

        p._on_toggle_excluded()

        set_calls = [c for c in fake.calls if c[0] == "set_trials_excluded"]
        assert len(set_calls) == 1
        assert set_calls[0][1] == ["1_left_pre_T1", "1_left_pre_T2"]
        assert set_calls[0][2] is True
    finally:
        r.destroy()


def test_toggle_excluded_rejects_mixed_state_selection(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.records[1]["excluded"] = True
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    infos = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda title, msg: infos.append(msg))
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        p._trial_table.selection_set(*p._trial_table.get_children())  # one excluded, one not

        p._on_toggle_excluded()

        assert not [c for c in fake.calls if c[0] == "set_trials_excluded"]
        assert len(infos) == 1
        assert "same current state" in infos[0]
    finally:
        r.destroy()


def test_toggle_excluded_registry_corrupt_leaves_rows_unchanged(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()

    def raise_corrupt(keys, excluded):
        raise fake.RegistryCorruptError("bad json")

    fake.set_trials_excluded = raise_corrupt
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        item = p._trial_table.get_children()[0]
        p._trial_table.selection_set(item)
        before_tags = p._trial_table.item(item, "tags")

        p._on_toggle_excluded()

        assert p._trial_table.item(item, "tags") == before_tags
        assert "fix or restore" in p.status_var.get()
        assert p.btn_toggle_excluded.cget("state") == "normal"
    finally:
        r.destroy()


def test_toggle_excluded_success_updates_row_tags_and_keeps_selection(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        item = p._trial_table.get_children()[0]
        p._trial_table.selection_set(item)

        p._on_toggle_excluded()
        deadline = time.time() + 5
        while p._participant_list.curselection() != (0,) and time.time() < deadline:
            r.update(); time.sleep(0.02)

        # Participant stays selected (not cleared by the refresh) and the
        # table reloads to reflect the just-saved state.
        assert p._participant_list.curselection() == (0,)
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        reloaded_item = p._trial_table.get_children()[0]
        assert "excluded" in p._trial_table.item(reloaded_item, "tags")
    finally:
        r.destroy()


def test_busy_flag_blocks_selection_change_during_generate(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._figure_type.set("full_report")

        p._on_generate()
        assert p._busy is True

        # A selection change fired mid-Generate must be ignored, not queued.
        p._participant_list.selection_set(1)
        p._on_participant_selection_changed()
        assert p._table_frame.winfo_manager() == ""   # never switched to table view

        _wait_until_enabled(p, r)
        assert p._busy is False
    finally:
        r.destroy()


def test_generate_from_table_view_switches_back_to_figure_view(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        assert p._table_frame.winfo_manager() == "grid"   # table view showing
        p._figure_type.set("full_report")

        p._on_generate()
        _wait_until_enabled(p, r)

        # Generate must switch the pane back to the figure view -- the table
        # staying up would leave the just-generated figure invisible behind it.
        assert p._table_frame.winfo_manager() == ""
        assert p._viewer_canvas.winfo_manager() == "grid"
        assert p._current_canvas is not None
        assert p.btn_toggle_excluded.cget("state") == "disabled"
    finally:
        r.destroy()
