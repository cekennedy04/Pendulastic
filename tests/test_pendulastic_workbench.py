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


def test_recompute_metrics_shows_pt_score_and_submetric_breakdown():
    """The readout text must surface the disambiguated composite PT score
    (labeled distinctly from pendulastic_app.py's PT= line, which uses a
    different 4-parameter formula) and the full 7-parameter windowed
    breakdown per trace."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    fs = 100.0
    t = np.arange(0, 4.0, 1.0 / fs)
    decay = np.exp(-0.4 * t)
    angle = 140.0 + 40.0 * decay * np.cos(2 * np.pi * 1.0 * t)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    text = wv._metrics_text.get("1.0", "end")
    snapshot = wv.get_metrics_snapshot()
    pt = snapshot["per_trace"]["imu"]
    assert f"PT(7p)={pt['pt_score']:.3f}" in text
    assert f"MAS {pt['mas']}" in text
    assert "R2n=" in text
    assert "phi_max_ratio=" in text
    assert "omega_max_n=" in text
    assert "omega_min_n=" in text


def test_recompute_metrics_shows_na_for_insufficient_signal():
    """A trace too short/flat for compute_pt_params must render an explicit
    'n/a' rather than a fabricated PT=0.000 or a crash."""
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    t = np.arange(0, 3.0, 1 / 60)
    angle = np.full_like(t, 180.0)
    wv.set_traces({"imu": (t, angle)})
    r.update()

    text = wv._metrics_text.get("1.0", "end")
    assert "PT(7p)=n/a (insufficient signal)" in text


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
