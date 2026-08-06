# tests/test_analysis_panel.py
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

from matplotlib.figure import Figure


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

    def list_participants(self):
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
