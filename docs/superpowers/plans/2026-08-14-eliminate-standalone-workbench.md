# Eliminate Standalone Workbench Entry Point Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `pendulastic_workbench.py`'s standalone `App` class and `__main__` entry point so `pendulastic_app.py` is the only launchable clinician-facing program, eliminating a live drift risk between two duplicate controller implementations.

**Architecture:** `pendulastic_workbench.py` keeps its three shared, reusable panel classes (`TrialLoadPanel`, `WorkbenchView`, `DashboardView`) exactly where they are — only the standalone `App(tk.Tk)` class and its `if __name__ == "__main__":` block are deleted, replaced by a two-line redirect guard. `pendulastic_app.py`'s own `App` (which already embeds these panels via a guarded import) is unaffected and untouched.

**Tech Stack:** Python 3, Tkinter, pytest.

**Spec:** `docs/superpowers/specs/2026-08-14-eliminate-standalone-workbench-design.md`

## Global Constraints

- No changes to `pendulastic_app.py`, `workbench_engine.py`, or `analysis_pipeline.py` — this is a UI-hosting/dead-code removal change only.
- No changes to `TrialLoadPanel`, `WorkbenchView`, or `DashboardView`'s own behavior — only their standalone host is removed.
- Do not edit historical docs under `docs/superpowers/{plans,specs}/` except the one specific "Superseded" note in Task 2 — those are a historical record, not live documentation.
- Test scope for this change is `tests/test_pendulastic_workbench.py` and `tests/test_app.py` only. Do **not** run a bare `pytest` from the repo root or `pytest tests/` unscoped as a verification step: the repo root collects vendored third-party tests under `models/openpose/3rdparty/pybind11/tests`, and `tests/test_metrics.py` / `tests/test_pose.py` have pre-existing, unrelated collection errors (a `mediapipe` API mismatch) that have nothing to do with this change. Always invoke pytest with explicit file paths.
- Known pre-existing flake: `tests/test_app.py::test_on_source_changed_does_not_crash` occasionally fails with a Tcl/Tk `_tkinter.TclError` ("couldn't read init.tcl") when run as part of a long batch of Tk-creating tests — this is Windows Tk resource exhaustion, not a real failure, and passes clean in isolation. If it fails during a step below, re-run that single test alone before treating it as a regression.

---

### Task 1: Delete the standalone Workbench `App`, its dependent tests, and the stale comments that reference it

**Files:**
- Modify: `pendulastic_workbench.py` (module docstring at lines 1-9; `App` class + `__main__` block at lines 1360-1515)
- Modify: `workbench_style.py` (two docstrings, in `_borrow_clam_elements` and `apply_ttk_theme`)
- Modify: `tests/test_pendulastic_workbench.py` (delete 5 test functions)
- Test: `tests/test_app.py` (unmodified — used as the pre-existing safety net)

**Interfaces:**
- Consumes: nothing new — `pendulastic_app.py`'s existing guarded import (`from pendulastic_workbench import TrialLoadPanel, WorkbenchView, DashboardView`) is unaffected by anything in this task.
- Produces: `pendulastic_workbench.py` exposes `TrialLoadPanel`, `WorkbenchView`, `DashboardView` as before; it no longer exposes `App`. Nothing outside this task's file list imports `App` from `pendulastic_workbench` (verified by repo-wide grep during spec review).

- [ ] **Step 1: Confirm the replacement test coverage exists and passes (the safety net for this deletion)**

Run:
```
.venv\Scripts\python.exe -m pytest tests/test_app.py::test_on_back_to_mode_select_hides_workbench_panels tests/test_app.py::test_on_workbench_load_another_returns_to_trial_load_panel tests/test_app.py::test_on_load_trial_split_csv_binds_and_stores_imu_reference tests/test_app.py::test_on_view_dashboard_shows_dashboard_and_hides_workbench_panels tests/test_app.py::test_on_dashboard_back_returns_to_trial_load_panel -v
```
Expected: 5 passed. These are the `tests/test_app.py` equivalents of the 5 tests this task deletes from `tests/test_pendulastic_workbench.py` in Step 5 — if any of these fail, stop and investigate before deleting anything; the safety net isn't there.

- [ ] **Step 2: Update the module docstring in `pendulastic_workbench.py`**

Old (lines 1-9):
```python
"""
pendulastic_workbench.py
=========================
Pendulastic Workbench: an interactive multi-modal (phone IMU / MediaPipe-
family HPE video / OptiTrack) trial comparison tool. Follows
pendulastic_app.py's plain-Tkinter panel-swap architecture.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md.
"""
```

New:
```python
"""
pendulastic_workbench.py
=========================
Panel library for the Multi-Modal Comparison workbench (phone IMU / MediaPipe-
family HPE video / OptiTrack trial comparison): TrialLoadPanel, WorkbenchView,
DashboardView. Hosted inside pendulastic_app.py's App (its "Multi-Modal
Comparison" mode) -- this module has no standalone entry point.

See docs/superpowers/specs/2026-07-31-pendulastic-workbench-design.md and
docs/superpowers/specs/2026-08-14-eliminate-standalone-workbench-design.md.
"""
```

- [ ] **Step 3: Delete the standalone `App` class and `__main__` block in `pendulastic_workbench.py`**

Old (lines 1360-1515 — this is the last class in the file, followed immediately by the `__main__` block; nothing else follows it, so this replaces everything from `class App(tk.Tk):` to end of file):
```python
class App(tk.Tk):
    """Owns panel switching between TrialLoadPanel and WorkbenchView,
    matching pendulastic_app.py's App class pattern (pack/pack_forget
    between pre-built panel instances)."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Pendulastic Workbench")
        self.geometry("1200x800")
        self.resizable(True, True)
        self.minsize(900, 600)
        ws.apply_ttk_theme(self)
        self.configure(bg=ws.PALETTE["BG"])

        self._trial_meta: dict = {}
        self._imu_reference: list = []
        self._status_var = tk.StringVar(value="")

        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._dashboard_view = DashboardView(self, controller=self)
        self._load_panel.pack(fill="both", expand=True)
        tk.Label(self, textvariable=self._status_var, anchor="w",
                bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG3"],
                font=ws.FONT_SMALL).pack(side="bottom", fill="x", padx=8, pady=2)

    def get_trial_meta(self) -> dict:
        return dict(self._trial_meta)

    def on_back_to_mode_select(self) -> None:
        """No-op in standalone mode -- there is no landing screen to return
        to here; this only exists so TrialLoadPanel's back button has a
        controller method to call regardless of which App hosts it."""
        pass

    def on_workbench_load_another(self) -> None:
        self._workbench_view.pack_forget()
        self._load_panel.pack(fill="both", expand=True)

    def on_view_dashboard(self) -> None:
        self._load_panel.pack_forget()
        self._workbench_view.pack_forget()
        self._dashboard_view.refresh_participants()
        self._dashboard_view.pack(fill="both", expand=True)

    def on_dashboard_back(self) -> None:
        self._dashboard_view.pack_forget()
        self._load_panel.pack(fill="both", expand=True)

    def on_load_trial(self, selection: dict) -> None:
        """Loads whichever of the three modalities were selected (design
        spec Section 2: 2-of-3 is valid) and switches to WorkbenchView.
        Video HPE model inference runs on a background thread since it's
        the slow step (design spec Section 3); IMU/OptiTrack loading is
        fast enough to run inline. IMU input is either a single JSONL raw
        log or four independently-validated split-CSV components (design
        spec 2026-08-04-sequential-csv-intake) -- TrialLoadPanel.get_selection()
        distinguishes the two via selection["imu_format"]."""
        traces = {}
        imu_format = selection.get("imu_format", "jsonl")
        self._trial_meta = {
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "participant_id": selection["participant_id"],
            "session_date": selection["session_date"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        ft_ratio = None
        method_override = None
        if selection["femur_length_cm"] and selection["tibia_length_cm"]:
            # Both limb lengths supplied means the researcher wants the
            # personalized-ratio Ockendon path validated -- force the
            # method rather than silently no-op if the persisted config's
            # method is "relative".
            ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
            method_override = "ockendon_flipped"

        if imu_format == "split_csv":
            components = selection.get("imu_components", {})
            if all(components.get(k, {}).get("ok") for k in ("accel", "gyro", "mag", "imu")):
                try:
                    t, angle, imu_reference = engine.load_imu_trial_from_components(
                        components, ft_ratio=ft_ratio, method=method_override)
                    traces["imu"] = (t, angle)
                    self._trial_meta["imu_paths"] = {
                        k: components.get(k, {}).get("path")
                        for k in ("accel", "gyro", "mag", "imu")}
                    # imu_reference (the full parsed raw-IMU row list) is
                    # kept off self._trial_meta so it never flows into
                    # export_session()'s output -- it can be megabytes for a
                    # real trial. Stored separately for in-memory
                    # cross-check use only.
                    self._imu_reference = imu_reference
                except Exception as e:
                    messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")
        elif selection["imu_path"]:
            self._trial_meta["imu_path"] = selection["imu_path"]
            try:
                t, angle = engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

        if selection["optitrack_path"]:
            try:
                t, angle, method = engine.load_optitrack_trial(selection["optitrack_path"])
                traces["optitrack"] = (t, angle)
                self._trial_meta["optitrack_method"] = method
            except Exception as e:
                messagebox.showerror("OptiTrack load error", f"{type(e).__name__}: {e}")

        self._load_panel.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.reset_for_new_trial()
        self._workbench_view.set_traces(traces)

        if selection["video_path"]:
            self._workbench_view.load_video(selection["video_path"])
            if selection["models"]:
                self._load_video_models_async(selection["video_path"], selection["models"], traces)

    def _load_video_models_async(self, video_path: str, models: list, traces: dict) -> None:
        """Runs load_video_trial on a background thread (design spec
        Section 3: full-video pose inference x N models is the slow step)
        and surfaces progress via progress_cb -- Tkinter widgets may only
        be touched from the main thread, so both the progress update and
        the final traces update are marshalled through self.after(0, ...)."""
        import threading

        self._status_var.set(f"Running {len(models)} HPE model(s)... 0%")

        def on_progress(fraction: float) -> None:
            self.after(0, lambda: self._status_var.set(
                f"Running {len(models)} HPE model(s)... {fraction * 100:.0f}%"))

        def worker():
            results = engine.load_video_trial(video_path, models, progress_cb=on_progress)
            def apply():
                for name, result in results.items():
                    if isinstance(result, dict) and "error" in result:
                        print(f"[warn] model {name!r} failed: {result['error']}")
                        continue
                    traces[name] = result
                self._workbench_view.set_traces(traces)
                self._status_var.set("")
            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
```

New:
```python
if __name__ == "__main__":
    print(
        "pendulastic_workbench.py no longer runs standalone -- its panels are "
        "hosted inside pendulastic_app.py.\n"
        "Run instead:  .venv\\Scripts\\python.exe pendulastic_app.py"
    )
    raise SystemExit(1)
```

- [ ] **Step 4: Update the two `workbench_style.py` docstrings that name the standalone App**

`_borrow_clam_elements` docstring — old:
```python
    element raises TclError, which is exactly the "already borrowed in this
    interpreter" case (apply_ttk_theme may be called more than once per
    process, e.g. by the standalone App and again by a test root)."""
```

New:
```python
    element raises TclError, which is exactly the "already borrowed in this
    interpreter" case (apply_ttk_theme may be called more than once per
    process, e.g. by pendulastic_app.App and again by a test root)."""
```

`apply_ttk_theme` docstring — old:
```python
    affected, so this is safe to call both from pendulastic_workbench.App
    (standalone) and from pendulastic_app.App (which embeds TrialLoadPanel
    and WorkbenchView alongside panels that must not change appearance).
```

New:
```python
    affected, so this is safe to call from pendulastic_app.App (which embeds
    TrialLoadPanel, WorkbenchView, and DashboardView alongside panels that
    must not change appearance) as well as from test roots.
```

- [ ] **Step 5: Delete the 5 now-unrunnable tests in `tests/test_pendulastic_workbench.py`**

These import `App` from `pendulastic_workbench`, which no longer exists after Step 3 — they must go or the module fails to collect. Two contiguous blocks (line numbers as of this writing; match on the exact text below rather than line numbers, since Steps 2-4 don't touch this file but line numbers can still drift from independent edits):

Block A — old (3 tests: `test_standalone_app_back_to_mode_select_is_a_genuine_noop`, `test_standalone_app_load_another_returns_to_load_panel`, `test_on_load_trial_split_csv_binds_and_stores_imu_reference`):
```python
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
```

Block A — new: *(delete entirely — nothing replaces it; the blank-line separator before the next test, `def test_workbench_view_per_trace_tree_populates_from_traces():`, already exists and should be left as-is)*

Block B — old (2 tests: `test_load_panel_view_dashboard_button_switches_to_dashboard_view`, `test_dashboard_back_returns_to_load_panel`):
```python
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
```

Block B — new: *(delete entirely — same reasoning as Block A; the existing blank-line separator before `def test_milestone_labels_no_longer_include_release_start():` stays)*

- [ ] **Step 6: Verify the redirect guard**

Run:
```
.venv\Scripts\python.exe pendulastic_workbench.py
```
Expected stdout:
```
pendulastic_workbench.py no longer runs standalone -- its panels are hosted inside pendulastic_app.py.
Run instead:  .venv\Scripts\python.exe pendulastic_app.py
```
Expected exit code: `1` (check with `echo %ERRORLEVEL%` immediately after, on Windows).

- [ ] **Step 7: Run the scoped test suite**

Run:
```
.venv\Scripts\python.exe -m pytest tests/test_pendulastic_workbench.py tests/test_app.py -q
```
Expected: all pass except possibly `test_on_source_changed_does_not_crash` (the known pre-existing Tcl/Tk flake — see Global Constraints). If it fails, immediately re-run:
```
.venv\Scripts\python.exe -m pytest tests/test_app.py::test_on_source_changed_does_not_crash -q
```
Expected: passes alone, confirming it's the known flake and not a regression from this task.

Also confirm collection succeeded cleanly for `tests/test_pendulastic_workbench.py` (no `ImportError: cannot import name 'App'` — this would indicate a 6th, unnoticed usage of the deleted class was missed).

This run covers the spec's "manual smoke check" (Testing Plan item 3: enter Multi-Modal Comparison mode, load a trial, confirm no behavior change) without a human at a screen — `tests/test_app.py::test_enter_workbench_mode_shows_trial_load_panel` and `tests/test_app.py::test_on_load_trial_split_csv_binds_and_stores_imu_reference` already exercise exactly that sequence (enter the mode, load a trial) against `pendulastic_app.App`, the only `App` left after this task. If you're running this on a machine with a display, it's still worth launching `.venv\Scripts\python.exe pendulastic_app.py` once and clicking through "Multi-Modal Comparison" -> load a trial by hand as a final human confirmation — but it is not required for this task to be considered done.

- [ ] **Step 8: Commit**

```bash
git add pendulastic_workbench.py workbench_style.py tests/test_pendulastic_workbench.py
git commit -m "$(cat <<'EOF'
refactor: remove standalone Workbench entry point

pendulastic_workbench.py's App class duplicated controller logic already
in pendulastic_app.py's App and had already drifted from it (missing
raw-sensor-diagnostics support). pendulastic_app.py is now the sole
launchable entry point; TrialLoadPanel/WorkbenchView/DashboardView remain
in pendulastic_workbench.py as a shared panel library.

Running pendulastic_workbench.py directly now prints a redirect message
and exits non-zero instead of launching a second, stale program.

See docs/superpowers/specs/2026-08-14-eliminate-standalone-workbench-design.md.
EOF
)"
```

---

### Task 2: Add a "Superseded" note to the 2026-08-03 integration spec

**Files:**
- Modify: `docs/superpowers/specs/2026-08-03-workbench-app-integration-design.md`

**Interfaces:**
- Consumes: nothing — independent of Task 1, can be done before or after it.
- Produces: nothing consumed elsewhere; purely a historical-accuracy note.

- [ ] **Step 1: Add the note**

Old (top of file):
```markdown
# Merge Pendulastic Workbench Into the Main App UI — Design Spec

**Status:** Approved
**Date:** 2026-08-03

---
```

New:
```markdown
# Merge Pendulastic Workbench Into the Main App UI — Design Spec

**Status:** Approved
**Date:** 2026-08-03

> **Superseded (2026-08-14):** the standalone `pendulastic_workbench.py` entry
> point this spec deliberately kept (Section 5, "Navigation") has since been
> removed — see
> `docs/superpowers/specs/2026-08-14-eliminate-standalone-workbench-design.md`.
> The panel-embedding design below (Sections 1-4) is otherwise still current.

---
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-08-03-workbench-app-integration-design.md
git commit -m "docs: mark 2026-08-03 workbench-integration spec as superseded on the standalone-entry-point decision"
```
