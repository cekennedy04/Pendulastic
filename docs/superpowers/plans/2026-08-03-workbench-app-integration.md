# Merge Pendulastic Workbench Into the Main App UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed the Pendulastic Workbench's panels (`TrialLoadPanel`, `WorkbenchView`)
directly into `pendulastic_app.py`'s own window, reachable as a third option from its
existing landing screen, while preserving `pendulastic_workbench.py`'s standalone
`App` entry point.

**Architecture:** `pendulastic_app.py` gains a guarded import of
`pendulastic_workbench`/`workbench_engine` (mirroring its existing `_IMU_AVAIL`-style
pattern), constructs the two panels alongside its existing ones, adds a third
`ModeSelectView` button routing to a new `App._enter_workbench_mode()`, and implements
`on_load_trial()`/`get_trial_meta()`/`on_workbench_load_another()` (moved/adapted from
`pendulastic_workbench.App`, calling the same `workbench_engine` functions — no new
ingestion logic). `TrialLoadPanel`/`WorkbenchView` gain two navigation buttons shared
between both hosting apps; the standalone `pendulastic_workbench.App` gets matching
(but different-behaving) implementations of the same two controller methods.

**Tech Stack:** Python, Tkinter, pytest.

## Global Constraints

- `TrialLoadPanel`/`WorkbenchView`/`workbench_engine.py`'s ingestion, metrics,
  alignment, and export logic must not change — this is purely about where the panels
  are hosted.
- `pendulastic_workbench.py`'s standalone `App`/`if __name__ == "__main__"` entry point
  must be preserved, not removed.
- The guarded import must follow this file's own established pattern (`_IMU_AVAIL`,
  `_CV2_AVAIL`, `_VIEWER_AVAIL`, `_PT_AVAIL`, `_MPL_AVAIL`): a failed import sets an
  `_WORKBENCH_AVAIL = False` flag rather than crashing the app at startup.
- `TrialLoadPanel`'s "← Back to Main Menu" button and `WorkbenchView`'s "← Load
  Different Trial" button must each call a controller method that exists on *both*
  `pendulastic_app.App` and `pendulastic_workbench.App`, since the two panel classes
  are shared code between the embedded and standalone entry points.
  - `on_back_to_mode_select()`: real behavior on `pendulastic_app.App`; a genuine no-op
    on standalone `pendulastic_workbench.App` (no landing screen to return to there).
  - `on_workbench_load_another()`: real behavior on *both* — returns to
    `TrialLoadPanel` in either app.
- Full spec: `docs/superpowers/specs/2026-08-03-workbench-app-integration-design.md`.

---

### Task 1: Shared navigation buttons on `TrialLoadPanel`/`WorkbenchView`

**Files:**
- Modify: `pendulastic_workbench.py:66-98` (`TrialLoadPanel._build_widgets`),
  `pendulastic_workbench.py:170-217` (`WorkbenchView._build_widgets`),
  `pendulastic_workbench.py:496-518` (`App.__init__`, `App.get_trial_meta`) — adds
  `App.on_back_to_mode_select()` and `App.on_workbench_load_another()`
- Test: `tests/test_pendulastic_workbench.py`

**Interfaces:**
- Consumes: nothing from later tasks
- Produces: `TrialLoadPanel` calls `self.controller.on_back_to_mode_select()` from a
  new "← Back to Main Menu" button; `WorkbenchView` calls
  `self.controller.on_workbench_load_another()` from a new "← Load Different Trial"
  button. Both controller methods are required on any controller passed to either
  panel from this point on — later tasks add `pendulastic_app.App`'s real versions.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pendulastic_workbench.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_pendulastic_workbench.py -k "back_button or load_another or noop or returns_to_load_panel" -v`
Expected: FAIL — `AttributeError: 'TrialLoadPanel' object has no attribute '_back_button'`
(and similarly for `_load_another_button`, and `App` missing both new methods)

- [ ] **Step 3: Add the "← Back to Main Menu" button to `TrialLoadPanel`**

In `pendulastic_workbench.py`, change `TrialLoadPanel._build_widgets`'s first line
(the title label) to add a header row above it:

```python
# OLD
    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        tk.Label(self, text="Pendulastic Workbench", font=("Segoe UI", 14, "bold")
                ).grid(row=0, column=0, columnspan=3, sticky="w", **pad)

        self._file_row(1, "Phone IMU raw log (.jsonl or split CSV)", self._imu_path,
                       [("IMU log", "*.jsonl *.csv"), ("All files", "*.*")], name="imu")
        self._file_row(2, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")], name="video")
        self._file_row(3, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")], name="optitrack")

        tk.Label(self, text="HPE models to run:").grid(
            row=4, column=0, sticky="nw", **pad)
        model_frame = tk.Frame(self)
        model_frame.grid(row=4, column=1, columnspan=2, sticky="w", **pad)
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name]
                          ).grid(row=i // 3, column=i % 3, sticky="w", padx=4)

        tk.Label(self, text="Femur length (cm, optional):").grid(
            row=5, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._femur_cm, width=10).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(self, text="Tibia length (cm, optional):").grid(
            row=6, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._tibia_cm, width=10).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=7, column=0, columnspan=3, pady=16)

# NEW
    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        self._back_button = tk.Button(
            self, text="← Back to Main Menu",
            command=lambda: self.controller.on_back_to_mode_select())
        self._back_button.grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 0))

        tk.Label(self, text="Pendulastic Workbench", font=("Segoe UI", 14, "bold")
                ).grid(row=1, column=0, columnspan=3, sticky="w", **pad)

        self._file_row(2, "Phone IMU raw log (.jsonl or split CSV)", self._imu_path,
                       [("IMU log", "*.jsonl *.csv"), ("All files", "*.*")], name="imu")
        self._file_row(3, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")], name="video")
        self._file_row(4, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")], name="optitrack")

        tk.Label(self, text="HPE models to run:").grid(
            row=5, column=0, sticky="nw", **pad)
        model_frame = tk.Frame(self)
        model_frame.grid(row=5, column=1, columnspan=2, sticky="w", **pad)
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name]
                          ).grid(row=i // 3, column=i % 3, sticky="w", padx=4)

        tk.Label(self, text="Femur length (cm, optional):").grid(
            row=6, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._femur_cm, width=10).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Label(self, text="Tibia length (cm, optional):").grid(
            row=7, column=0, sticky="w", **pad)
        tk.Entry(self, textvariable=self._tibia_cm, width=10).grid(
            row=7, column=1, sticky="w", **pad)

        tk.Button(self, text="Load Trial", command=self._on_load_clicked
                 ).grid(row=8, column=0, columnspan=3, pady=16)
```

(Every `row=` value after the new header row shifts down by 1 — this is purely a grid
layout renumbering, no behavioral change to any existing row's content.)

- [ ] **Step 4: Add the "← Load Different Trial" button to `WorkbenchView`**

In `pendulastic_workbench.py`, in `WorkbenchView._build_widgets`, change the
`top_controls` row:

```python
# OLD
        top_controls = tk.Frame(self._right)
        top_controls.pack(fill="x", padx=8, pady=4)
        tk.Label(top_controls, text="Reference:").pack(side="left")
        self._reference_menu = ttk.OptionMenu(top_controls, self._reference_var, "")
        self._reference_menu.pack(side="left", padx=6)
        self._reference_var.trace_add("write", lambda *a: self._recompute_metrics())

# NEW
        top_controls = tk.Frame(self._right)
        top_controls.pack(fill="x", padx=8, pady=4)
        tk.Label(top_controls, text="Reference:").pack(side="left")
        self._reference_menu = ttk.OptionMenu(top_controls, self._reference_var, "")
        self._reference_menu.pack(side="left", padx=6)
        self._reference_var.trace_add("write", lambda *a: self._recompute_metrics())
        self._load_another_button = tk.Button(
            top_controls, text="← Load Different Trial",
            command=lambda: self.controller.on_workbench_load_another())
        self._load_another_button.pack(side="right", padx=6)
```

- [ ] **Step 5: Add the two new controller methods to the standalone `App`**

In `pendulastic_workbench.py`, in `App`, add these two methods right after
`get_trial_meta`:

```python
    def on_back_to_mode_select(self) -> None:
        """No-op in standalone mode -- there is no landing screen to return
        to here; this only exists so TrialLoadPanel's back button has a
        controller method to call regardless of which App hosts it."""
        pass

    def on_workbench_load_another(self) -> None:
        self._workbench_view.pack_forget()
        self._load_panel.pack(fill="both", expand=True)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_pendulastic_workbench.py -k "back_button or load_another or noop or returns_to_load_panel" -v`
Expected: 4 passed

- [ ] **Step 7: Run the full Workbench test suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_pendulastic_workbench.py tests\test_workbench_engine.py -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: add shared back/load-another navigation to Workbench panels"
```

---

### Task 2: Guarded import, panel construction, and navigation-in on `pendulastic_app.App`

**Files:**
- Modify: `pendulastic_app.py` (new guarded-import block near the top; `App.__init__`;
  new `App._enter_workbench_mode`; `App.on_back_to_mode_select` extended;
  `ModeSelectView._build_widgets`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `TrialLoadPanel`, `WorkbenchView`, `pendulastic_workbench.App.on_back_to_mode_select`/
  `on_workbench_load_another` (Task 1) as the pattern to match on `pendulastic_app.App`
- Produces: `App._enter_workbench_mode() -> None`; `App._workbench_load`,
  `App._workbench_view`, `App._workbench_trial_meta: dict`,
  `App._workbench_status_var: tk.StringVar` (consumed by Task 3)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_enter_workbench_mode_shows_trial_load_panel():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        assert app._workbench_load.winfo_ismapped()
        assert not app._mode_select.winfo_ismapped()
        assert app._state == "workbench_load"
    finally:
        app.destroy()


def test_on_back_to_mode_select_hides_workbench_panels():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app._workbench_load.pack_forget()
        app._workbench_view.pack(fill="both", expand=True)
        app.update()
        app.on_back_to_mode_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._workbench_load.winfo_ismapped()
        assert not app._workbench_view.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()


def test_enter_workbench_mode_shows_message_when_unavailable(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", False)
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo",
                        lambda title, msg: shown.append((title, msg)))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        assert len(shown) == 1
        assert app._mode_select.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k "enter_workbench_mode or hides_workbench_panels" -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute '_enter_workbench_mode'`

- [ ] **Step 3: Add the guarded import**

In `pendulastic_app.py`, add this block alongside the existing guarded imports (after
the `_MPL_AVAIL` block, around line 92):

```python
try:
    from pendulastic_workbench import TrialLoadPanel, WorkbenchView
    import workbench_engine as _wb_engine
    _WORKBENCH_AVAIL = True
except Exception:
    TrialLoadPanel = WorkbenchView = None
    _wb_engine = None
    _WORKBENCH_AVAIL = False
```

- [ ] **Step 4: Construct the panels in `App.__init__`**

In `pendulastic_app.py`, in `App.__init__`, right after the existing panel
construction block:

```python
# OLD
        self._mode_select = ModeSelectView(self, controller=self)
        self._upload_meta = UploadMetaView(self, controller=self)
        self._acq  = AcquisitionPanel(self, controller=self)
        self._post = PostProcessingPanel(self, controller=self)
        self._mode_select.pack(fill="both", expand=True)

# NEW
        self._mode_select = ModeSelectView(self, controller=self)
        self._upload_meta = UploadMetaView(self, controller=self)
        self._acq  = AcquisitionPanel(self, controller=self)
        self._post = PostProcessingPanel(self, controller=self)

        self._workbench_trial_meta: dict = {}
        self._workbench_status_var = tk.StringVar(value="")
        if _WORKBENCH_AVAIL:
            self._workbench_load = TrialLoadPanel(self, controller=self)
            self._workbench_view = WorkbenchView(self, controller=self)
            tk.Label(self, textvariable=self._workbench_status_var, anchor="w").pack(
                side="bottom", fill="x", padx=8, pady=2)

        self._mode_select.pack(fill="both", expand=True)
```

- [ ] **Step 5: Add `App._enter_workbench_mode`**

In `pendulastic_app.py`, in `App`, add this method near `_enter_upload_mode`:

```python
    def _enter_workbench_mode(self) -> None:
        if not _WORKBENCH_AVAIL:
            messagebox.showinfo(
                "Workbench Unavailable",
                "The Multi-Modal Comparison workbench could not be loaded in this "
                "environment (a required dependency is missing).")
            return
        self._mode_select.pack_forget()
        self._workbench_load.pack(fill="both", expand=True)
        self._state = "workbench_load"
```

- [ ] **Step 6: Extend `App.on_back_to_mode_select`**

In `pendulastic_app.py`, change `on_back_to_mode_select`:

```python
# OLD
    def on_back_to_mode_select(self) -> None:
        self._acq.pack_forget()
        self._post.pack_forget()
        self._upload_meta.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state        = "mode_select"
        self._active_sources  = []
        self._rec_angles      = {}
        self._rec_timestamps  = {}
        self._pending_review  = {}

# NEW
    def on_back_to_mode_select(self) -> None:
        self._acq.pack_forget()
        self._post.pack_forget()
        self._upload_meta.pack_forget()
        if _WORKBENCH_AVAIL:
            self._workbench_load.pack_forget()
            self._workbench_view.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state        = "mode_select"
        self._active_sources  = []
        self._rec_angles      = {}
        self._rec_timestamps  = {}
        self._pending_review  = {}
```

- [ ] **Step 7: Add the third `ModeSelectView` button**

In `pendulastic_app.py`, in `ModeSelectView._build_widgets`, after the existing two
buttons:

```python
        tk.Button(
            self,
            text="Upload & Analyze\nVideo or CSV file",
            font=("Segoe UI", 12, "bold"),
            bg=_BLUE, fg="white",
            width=24, height=4,
            command=self.controller._enter_upload_mode,
        ).grid(row=2, column=1, padx=40, pady=16, sticky="n")

        tk.Button(
            self,
            text="Multi-Modal Comparison\nIMU · OptiTrack · Video",
            font=("Segoe UI", 12, "bold"),
            bg=_AMBER, fg="white",
            width=24, height=4,
            command=self.controller._enter_workbench_mode,
        ).grid(row=3, column=0, columnspan=2, padx=40, pady=(0, 24), sticky="n")
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k "enter_workbench_mode or hides_workbench_panels" -v`
Expected: 3 passed

- [ ] **Step 9: Run the full `test_app.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -v`
Expected: all pass

- [ ] **Step 10: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add guarded Workbench import and mode-select entry point"
```

---

### Task 3: Wire `on_load_trial`/`get_trial_meta`/`on_workbench_load_another` on `pendulastic_app.App`

**Files:**
- Modify: `pendulastic_app.py` (new `App.get_trial_meta`, `App.on_load_trial`,
  `App._load_workbench_video_models_async`, `App.on_workbench_load_another`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `_wb_engine` (Task 2), `App._workbench_load`/`App._workbench_view`/
  `App._workbench_trial_meta`/`App._workbench_status_var` (Task 2)
- Produces: `App.get_trial_meta() -> dict`, `App.on_load_trial(selection: dict) -> None`,
  `App.on_workbench_load_another() -> None` — the full controller contract
  `TrialLoadPanel`/`WorkbenchView` require.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_on_load_trial_imu_only_switches_to_workbench_view(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0, 0.05]), np.array([180.0, 170.0])))
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.update()
        app.on_load_trial({
            "imu_path": str(tmp_path / "trial.jsonl"), "video_path": None,
            "optitrack_path": None, "models": [],
            "femur_length_cm": None, "tibia_length_cm": None,
        })
        app.update()
        assert app._workbench_view.winfo_ismapped()
        assert not app._workbench_load.winfo_ismapped()
        assert "imu" in app._workbench_view._traces
    finally:
        app.destroy()


def test_get_trial_meta_reflects_last_loaded_selection(tmp_path, monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    fake_engine = type("FakeEngine", (), {
        "load_imu_trial": staticmethod(
            lambda path, ft_ratio=None, method=None: (np.array([0.0]), np.array([180.0])))
    })()
    monkeypatch.setattr(_m, "_wb_engine", fake_engine)

    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app.on_load_trial({
            "imu_path": "some/trial.jsonl", "video_path": None,
            "optitrack_path": None, "models": [],
            "femur_length_cm": 45.0, "tibia_length_cm": 38.0,
        })
        app.update()
        meta = app.get_trial_meta()
        assert meta["imu_path"] == "some/trial.jsonl"
        assert meta["femur_length_cm"] == 45.0
    finally:
        app.destroy()


def test_on_workbench_load_another_returns_to_trial_load_panel(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", True)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_workbench_mode()
        app._workbench_load.pack_forget()
        app._workbench_view.pack(fill="both", expand=True)
        app.update()
        app.on_workbench_load_another()
        app.update()
        assert app._workbench_load.winfo_ismapped()
        assert not app._workbench_view.winfo_ismapped()
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k "on_load_trial or trial_meta_reflects or load_another_returns" -v`
Expected: FAIL — `AttributeError: 'App' object has no attribute 'on_load_trial'`

- [ ] **Step 3: Add the three methods to `pendulastic_app.App`**

In `pendulastic_app.py`, in `App`, add near `_enter_workbench_mode`:

```python
    def get_trial_meta(self) -> dict:
        return dict(self._workbench_trial_meta)

    def on_load_trial(self, selection: dict) -> None:
        """Loads whichever of the three modalities were selected (design
        spec Section 2: 2-of-3 is valid) and switches to WorkbenchView.
        Video HPE model inference runs on a background thread since it's
        the slow step (design spec Section 3); IMU/OptiTrack loading is
        fast enough to run inline."""
        traces = {}
        self._workbench_trial_meta = {
            "imu_path": selection["imu_path"],
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }

        if selection["imu_path"]:
            ft_ratio = None
            method_override = None
            if selection["femur_length_cm"] and selection["tibia_length_cm"]:
                ft_ratio = selection["femur_length_cm"] / selection["tibia_length_cm"]
                method_override = "ockendon_flipped"
            try:
                t, angle = _wb_engine.load_imu_trial(
                    selection["imu_path"], ft_ratio=ft_ratio, method=method_override)
                traces["imu"] = (t, angle)
            except Exception as e:
                messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")

        if selection["optitrack_path"]:
            try:
                t, angle, method = _wb_engine.load_optitrack_trial(selection["optitrack_path"])
                traces["optitrack"] = (t, angle)
                self._workbench_trial_meta["optitrack_method"] = method
            except Exception as e:
                messagebox.showerror("OptiTrack load error", f"{type(e).__name__}: {e}")

        self._workbench_load.pack_forget()
        self._workbench_view.pack(fill="both", expand=True)
        self._workbench_view.set_traces(traces)

        if selection["video_path"]:
            self._workbench_view.load_video(selection["video_path"])
            if selection["models"]:
                self._load_workbench_video_models_async(
                    selection["video_path"], selection["models"], traces)

    def _load_workbench_video_models_async(self, video_path: str, models: list,
                                           traces: dict) -> None:
        """Runs load_video_trial on a background thread (design spec
        Section 3: full-video pose inference x N models is the slow step)
        and surfaces progress via progress_cb -- Tkinter widgets may only
        be touched from the main thread, so both the progress update and
        the final traces update are marshalled through self.after(0, ...)."""
        self._workbench_status_var.set(f"Running {len(models)} HPE model(s)... 0%")

        def on_progress(fraction: float) -> None:
            self.after(0, lambda: self._workbench_status_var.set(
                f"Running {len(models)} HPE model(s)... {fraction * 100:.0f}%"))

        def worker():
            results = _wb_engine.load_video_trial(video_path, models, progress_cb=on_progress)
            def apply():
                for name, result in results.items():
                    if isinstance(result, dict) and "error" in result:
                        print(f"[warn] model {name!r} failed: {result['error']}")
                        continue
                    traces[name] = result
                self._workbench_view.set_traces(traces)
                self._workbench_status_var.set("")
            self.after(0, apply)

        threading.Thread(target=worker, daemon=True).start()

    def on_workbench_load_another(self) -> None:
        self._workbench_view.pack_forget()
        self._workbench_load.pack(fill="both", expand=True)
```

(`threading` is already imported at the top of `pendulastic_app.py` — no new import
needed, unlike `pendulastic_workbench.py`'s original version which imported it locally
inside the method.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -k "on_load_trial or trial_meta_reflects or load_another_returns" -v`
Expected: 3 passed

- [ ] **Step 5: Run the full `test_app.py` suite to confirm no regressions**

Run: `.venv\Scripts\pytest.exe tests\test_app.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: wire on_load_trial/get_trial_meta/on_workbench_load_another on the main app"
```

---

### Task 4: Full regression run

**Files:**
- None modified — verification only

- [ ] **Step 1: Run the full test suite**

Run: `.venv\Scripts\pytest.exe tests\ -v --ignore=tests\test_metrics.py --ignore=tests\test_pose.py --ignore=tests\test_stats.py --ignore=tests\test_video.py`

Expected: all pass. If the known pre-existing tkinter-singleton flake in
`test_acquisition_panel.py` (or elsewhere) appears when run alongside other files,
re-run the specific failing test(s) individually to confirm it's the pre-existing flake
and not a real regression (documented in this repo's other plans — a different test
fails each run, never the same one twice, and always passes in isolation).

- [ ] **Step 2: Manual acceptance step (not automatable here)**

Launch `pendulastic_app.py` via the project venv
(`.venv\Scripts\python.exe pendulastic_app.py`), confirm the "Multi-Modal Comparison"
button appears on the landing screen, click it, load a real trial (e.g. one of the
`Participant_13_left` split-CSV trials), confirm the comparison view renders, click
"← Load Different Trial," confirm it returns to the load form, then click "← Back to
Main Menu" and confirm it returns to the landing screen. This requires an interactive
Tkinter session and is out of scope for the automated test suite — flag it as the
remaining manual verification step.

- [ ] **Step 3: Commit (only if Step 1 required any fixes)**

```bash
git add -A
git commit -m "test: fix regressions found in full-suite run"
```

---
