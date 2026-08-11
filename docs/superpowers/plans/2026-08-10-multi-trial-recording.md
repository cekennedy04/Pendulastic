# Multi-Trial Recording Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a clinician record trials in quick succession from `pendulastic_app.py`'s `AcquisitionPanel` without a forced trip to the analysis screen after each STOP, and let them delete a just-recorded trial's saved files immediately from the same screen.

**Architecture:** A new "Record multiple trials" checkbox on `AcquisitionPanel` gates a new trial-list widget on the same screen. `App` gains an in-memory `_session_trials` list; `on_stop()` and `_transition_to_review()` gain a multi-trial branch that skips navigating to `PostProcessingPanel` and instead updates the list and returns to idle. `PostProcessingPanel` becomes reachable on demand (click a trial row) with a context-aware back button.

**Tech Stack:** Python 3, Tkinter (no new dependencies), pytest (existing test conventions: withdrawn `tk.Tk()` roots, fake `_Ctrl` controller classes, `monkeypatch` for `messagebox` and `DataManager.DATA_DIR`).

## Global Constraints

- Multi-trial mode applies only to live recording sources (`imu`, `rgb`, `optitrack`) — the standalone `video_file` research path is untouched. (Spec §3)
- Default off: with the toggle unchecked, every existing code path behaves byte-for-byte as it does today. (Spec §4)
- Deleting a trial never renumbers or reuses trial numbers. (Spec §7)
- No cancellation support is added for in-flight background processing (`_run_rgb_processing` / `_run_imu_tuning`); delete stays disabled while `status == "processing"`. (Spec §7)
- No scrollbar on the trial list, no cross-session/on-disk persistence of it, no renumbering after delete. (Spec §9, explicitly out of scope)
- Follow existing conventions in `pendulastic_app.py`: `ws.PALETTE`/`ws.card_frame`/`ws.secondary_button` styling, `tk.Frame`/grid layout patterns already used in `AcquisitionPanel` and `PostProcessingPanel`.
- Tests go in the existing files (`tests/test_acquisition_panel.py`, `tests/test_app.py`, `tests/test_post_processing_panel.py`) and follow their existing fixtures/conventions — do not introduce a new test-setup pattern.

---

## Task 1: "Record multiple trials" toggle checkbox

**Files:**
- Modify: `pendulastic_app.py:696-705` (`AcquisitionPanel._build_widgets`, row 11 — countdown checkbox), `pendulastic_app.py:733-738` (`self._lockable` list)
- Test: `tests/test_acquisition_panel.py`

**Interfaces:**
- Produces: `AcquisitionPanel._multi_trial_var: tk.BooleanVar` (default `False`), `AcquisitionPanel.multi_trial_chk: tk.Checkbutton`, both wired into `self._lockable` so they lock during recording/countdown like every other form field.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_acquisition_panel.py`:

```python
def test_multi_trial_checkbox_default_off():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        assert p._multi_trial_var.get() is False
    finally:
        r.destroy()


def test_multi_trial_checkbox_locks_during_recording():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.enter_recording()
        assert str(p.multi_trial_chk["state"]) == "disabled"
        p.enter_idle()
        assert str(p.multi_trial_chk["state"]) == "normal"
    finally:
        r.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -k multi_trial_checkbox -v`
Expected: FAIL with `AttributeError: 'AcquisitionPanel' object has no attribute '_multi_trial_var'`

- [ ] **Step 3: Add the checkbox**

In `pendulastic_app.py`, replace the row-11 block (currently lines 696-705):

```python
        # row 11 — countdown checkbox (forced on/locked while IMU is an
        # active source -- it's the only calibration path now)
        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"])
        self.countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)
```

with:

```python
        # row 11 — countdown + multi-trial checkboxes, stacked in one frame
        # so no other row needs renumbering.
        chk_stack = tk.Frame(self, bg=ws.PALETTE["BG"])
        chk_stack.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)

        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            chk_stack, text="5-second countdown before recording",
            variable=self.countdown_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"])
        self.countdown_chk.pack(side="top", anchor="w")

        self._multi_trial_var = tk.BooleanVar(value=False)
        self.multi_trial_chk = tk.Checkbutton(
            chk_stack, text="Record multiple trials",
            variable=self._multi_trial_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"])
        self.multi_trial_chk.pack(side="top", anchor="w")
```

Then in the `self._lockable` list (currently lines 733-738):

```python
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            self.countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            self._research_toggle_btn,
            self.btn_back, self.drop_cam, self.btn_rescan,
        ]
```

add `self.multi_trial_chk,` right after `self.countdown_chk,`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -k multi_trial_checkbox -v`
Expected: PASS

- [ ] **Step 5: Run the full acquisition-panel test file to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -v`
Expected: all PASS (row renumbering hasn't happened yet, so no other test should be affected)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: add multi-trial recording toggle checkbox to AcquisitionPanel"
```

---

## Task 2: Session trial list data model + processing placeholder on STOP

**Files:**
- Modify: `pendulastic_app.py:2492` (`App.__init__`), `pendulastic_app.py:2585-2609` (`App.on_stop`, top of method)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `AcquisitionPanel._multi_trial_var` (Task 1)
- Produces: `App._session_trials: list[dict]` — entries shaped
  `{trial_num: int, sources: list[str], status: "processing"|"saved", meta: dict, source_angles: dict|None, fps: float|None, base_filename: str|None, file_paths: list[str]}`;
  `App._is_multi_trial_mode() -> bool`;
  `App._trial_file_paths(meta: dict, sources: list[str]) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_is_multi_trial_mode_reflects_checkbox():
    from pendulastic_app import App
    app = App()
    try:
        assert app._is_multi_trial_mode() is False
        app._acq._multi_trial_var.set(True)
        assert app._is_multi_trial_mode() is True
    finally:
        app.destroy()


def test_trial_file_paths_imu_and_rgb(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        names = [os.path.basename(p) for p in app._trial_file_paths(meta, ["imu", "rgb"])]
        assert "PID_P1_LEG_Right_MS_TRIAL_1_imu.csv" in names
        assert "PID_P1_LEG_Right_MS_TRIAL_1_rgb.csv" in names
        assert "PID_P1_LEG_Right_MS_TRIAL_1.avi" in names
        assert "PID_P1_LEG_Right_MS_TRIAL_1.avi.timestamps.csv" in names
    finally:
        app.destroy()


def test_trial_file_paths_optitrack_only(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        assert app._trial_file_paths(meta, ["optitrack"]) == []
    finally:
        app.destroy()


def test_on_stop_appends_processing_placeholder_in_multi_trial_mode(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: None)
    app = _m.App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("1")
        app._acq._multi_trial_var.set(True)
        app._active_sources = []
        app.on_stop()
        app.update()
        assert len(app._session_trials) == 1
        entry = app._session_trials[0]
        assert entry["trial_num"] == 1
        assert entry["sources"] == []
        assert entry["status"] == "processing"
    finally:
        app.destroy()


def test_on_stop_no_placeholder_when_multi_trial_off(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: None)
    app = _m.App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("1")
        app._active_sources = []
        app.on_stop()
        app.update()
        assert app._session_trials == []
    finally:
        app.destroy()
```

`os` is already imported at the top of `tests/test_app.py` — no new import needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "multi_trial_mode or trial_file_paths or processing_placeholder or no_placeholder" -v`
Expected: FAIL with `AttributeError: 'App' object has no attribute '_is_multi_trial_mode'` (and similar for the others)

- [ ] **Step 3: Add the data model and placeholder logic**

In `pendulastic_app.py`, in `App.__init__`, right after `self._pending_review: dict = {}` (line 2492), add:

```python
        self._session_trials: list = []
```

Add these two new methods to `App` (near `_transition_to_review`, e.g. right before it):

```python
    def _is_multi_trial_mode(self) -> bool:
        return self._acq._multi_trial_var.get()

    def _trial_file_paths(self, meta: dict, sources: list) -> list:
        """Every file this app itself writes for a trial with these sources
        -- mirrors what _show_recording_saved_confirmation() already
        enumerates, but returns real paths for deletion instead of a
        display message. OptiTrack writes nothing here (Motive owns that
        file)."""
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        paths = []
        if "imu" in sources:
            fn = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="imu")
            paths.append(os.path.join(DataManager.DATA_DIR, fn))
        if "rgb" in sources:
            fn = DataManager.build_filename(
                meta["pid"], meta["leg"], meta["ms_status"], meta["trial"], source="rgb")
            paths.append(os.path.join(DataManager.DATA_DIR, fn))
            video_path = os.path.join(DataManager.DATA_DIR, base_fn.replace(".csv", ".avi"))
            paths.append(video_path)
            paths.append(video_path + ".timestamps.csv")
        return paths
```

In `App.on_stop()`, right after `meta = self._acq.get_metadata()` (line 2605), add:

```python
        if self._is_multi_trial_mode():
            self._session_trials.append({
                "trial_num": meta["trial"],
                "sources": list(self._active_sources),
                "status": "processing",
                "meta": meta,
                "source_angles": None,
                "fps": None,
                "base_filename": None,
                "file_paths": self._trial_file_paths(meta, self._active_sources),
            })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "multi_trial_mode or trial_file_paths or processing_placeholder or no_placeholder" -v`
Expected: PASS

- [ ] **Step 5: Run the full app test file to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: track per-session trials with a processing placeholder on STOP"
```

---

## Task 3: Trial list widget (render, toggle visibility, delete confirm, row click)

**Files:**
- Modify: `pendulastic_app.py` — `AcquisitionPanel._build_widgets` (row 13 insertion + row 13/14 renumbering, lines 720-730), `AcquisitionPanel._refresh_preview_area` (line ~777), the `multi_trial_chk` constructor from Task 1 (add `command=`)
- Modify: `tests/test_acquisition_panel.py` (`_Ctrl` fake gains `on_view_trial`/`on_delete_trial`)
- Test: `tests/test_acquisition_panel.py`

**Interfaces:**
- Consumes: `AcquisitionPanel._multi_trial_var` (Task 1)
- Produces: `AcquisitionPanel.set_multi_trial_list(trials: list[dict]) -> None` where each entry is
  `{"trial_num": int, "sources": list[str], "status": "processing"|"saved"}`; calls
  `controller.on_view_trial(trial_num: int)` on row-label click and
  `controller.on_delete_trial(trial_num: int)` after a confirmed delete.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_acquisition_panel.py`, and extend the shared `_Ctrl` class with:

```python
    def on_view_trial(self, trial_num): pass
    def on_delete_trial(self, trial_num): pass
```

Then add:

```python
def test_set_multi_trial_list_renders_rows():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._multi_trial_var.set(True)
        p.set_multi_trial_list([
            {"trial_num": 1, "sources": ["imu", "rgb"], "status": "saved"},
            {"trial_num": 2, "sources": ["imu"], "status": "processing"},
        ])
        r.update()
        assert p._trial_list_frame.winfo_ismapped()
        assert len(p._trial_list_container.winfo_children()) == 2
    finally:
        r.destroy()


def test_trial_list_hidden_when_toggle_off():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p.set_multi_trial_list([{"trial_num": 1, "sources": ["imu"], "status": "saved"}])
        r.update()
        assert not p._trial_list_frame.winfo_ismapped()
    finally:
        r.destroy()


def test_trial_list_hidden_when_empty():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._multi_trial_var.set(True)
        p.set_multi_trial_list([])
        r.update()
        assert not p._trial_list_frame.winfo_ismapped()
    finally:
        r.destroy()


def test_multi_trial_list_persists_across_toggle_off_and_on():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._multi_trial_var.set(True)
        p.set_multi_trial_list([{"trial_num": 1, "sources": ["imu"], "status": "saved"}])
        r.update()
        p.multi_trial_chk.invoke()   # toggles var False and fires the command
        r.update()
        assert not p._trial_list_frame.winfo_ismapped()
        p.multi_trial_chk.invoke()   # toggles var True again
        r.update()
        assert p._trial_list_frame.winfo_ismapped()
        assert len(p._trial_list_container.winfo_children()) == 1
    finally:
        r.destroy()


def test_delete_button_disabled_while_processing():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._multi_trial_var.set(True)
        p.set_multi_trial_list([{"trial_num": 1, "sources": ["imu"], "status": "processing"}])
        r.update()
        row = p._trial_list_container.winfo_children()[0]
        btn_del = row.winfo_children()[-1]
        assert str(btn_del["state"]) == "disabled"
    finally:
        r.destroy()


def test_delete_click_confirms_then_calls_controller(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **k: True)
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def on_delete_trial(self, n): calls.append(n)
        p = _m.AcquisitionPanel(r, C()); p.pack(); r.update()
        p._multi_trial_var.set(True)
        p.set_multi_trial_list([{"trial_num": 3, "sources": ["imu"], "status": "saved"}])
        r.update()
        row = p._trial_list_container.winfo_children()[0]
        btn_del = row.winfo_children()[-1]
        btn_del.invoke()
        r.update()
        assert calls == [3]
    finally:
        r.destroy()


def test_delete_click_declined_does_not_call_controller(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **k: False)
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def on_delete_trial(self, n): calls.append(n)
        p = _m.AcquisitionPanel(r, C()); p.pack(); r.update()
        p._multi_trial_var.set(True)
        p.set_multi_trial_list([{"trial_num": 3, "sources": ["imu"], "status": "saved"}])
        r.update()
        row = p._trial_list_container.winfo_children()[0]
        btn_del = row.winfo_children()[-1]
        btn_del.invoke()
        r.update()
        assert calls == []
    finally:
        r.destroy()


def test_row_click_calls_view_trial():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        calls = []
        class C(_Ctrl):
            def on_view_trial(self, n): calls.append(n)
        p = AcquisitionPanel(r, C()); p.pack(); r.update()
        p._multi_trial_var.set(True)
        p.set_multi_trial_list([{"trial_num": 5, "sources": ["rgb"], "status": "saved"}])
        r.update()
        row = p._trial_list_container.winfo_children()[0]
        lbl = row.winfo_children()[0]
        lbl.event_generate("<Button-1>", x=5, y=5)
        r.update()
        assert calls == [5]
    finally:
        r.destroy()


def test_telemetry_canvas_still_not_gridded_at_init():
    """Regression: row renumbering (row 13 -> 14) must not break the
    existing telemetry-canvas visibility contract."""
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p.canvas_tele.grid_info() == {}
        p.enter_recording()
        r.update()
        assert p.canvas_tele.grid_info() != {}
        assert int(p.canvas_tele.grid_info()["row"]) == 14
    finally:
        r.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -k "multi_trial_list or delete_ or row_click or telemetry_canvas_still" -v`
Expected: FAIL with `AttributeError: 'AcquisitionPanel' object has no attribute '_trial_list_frame'`

- [ ] **Step 3: Build the widget**

In `pendulastic_app.py`, in the `multi_trial_chk` constructor added in Task 1, add a `command=`:

```python
        self._multi_trial_var = tk.BooleanVar(value=False)
        self.multi_trial_chk = tk.Checkbutton(
            chk_stack, text="Record multiple trials",
            variable=self._multi_trial_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"],
            command=self._on_multi_trial_toggle)
        self.multi_trial_chk.pack(side="top", anchor="w")
```

Replace the row-13/row-14 block (currently lines 720-730):

```python
        # row 13 — live telemetry canvas (NOT gridded at init; shown during RECORDING)
        self.canvas_tele = tk.Canvas(
            self, width=440, height=80, bg="#0B1928", highlightthickness=0)

        # row 14 — status bar
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w",
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"])
        self.lbl_status.grid(row=14, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))
```

with:

```python
        # row 13 — trial list (multi-trial mode; hidden until toggled on and
        # at least one trial exists this session)
        self._trial_rows_data: list = []
        self._trial_list_frame = ws.card_frame(self, title="TRIALS THIS SESSION")
        self._trial_list_container = tk.Frame(self._trial_list_frame, bg=ws.PALETTE["PANEL"])
        self._trial_list_container.pack(side="top", fill="x")

        # row 14 — live telemetry canvas (NOT gridded at init; shown during RECORDING)
        self.canvas_tele = tk.Canvas(
            self, width=440, height=80, bg="#0B1928", highlightthickness=0)

        # row 15 — status bar
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w",
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"])
        self.lbl_status.grid(row=15, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))
```

In `_refresh_preview_area`, change the telemetry canvas's grid row from 13 to 14:

```python
    def _refresh_preview_area(self) -> None:
        if self._is_recording:
            self.canvas_tele.grid(row=14, column=0, columnspan=2,
                                  padx=10, pady=4)
        else:
            self.canvas_tele.grid_remove()
```

Add these methods to `AcquisitionPanel` (near `_on_rgb_checkbox_toggled`):

```python
    def _on_multi_trial_toggle(self) -> None:
        self._sync_trial_list_visibility()

    def _sync_trial_list_visibility(self) -> None:
        show = self._multi_trial_var.get() and bool(self._trial_rows_data)
        if show:
            self._trial_list_frame.grid(row=13, column=0, columnspan=2,
                                        sticky="ew", padx=12, pady=4)
        else:
            self._trial_list_frame.grid_remove()

    def set_multi_trial_list(self, trials: list) -> None:
        self._trial_rows_data = list(trials)
        for w in self._trial_list_container.winfo_children():
            w.destroy()
        for t in self._trial_rows_data:
            self._build_trial_row(t)
        self._sync_trial_list_visibility()

    _SOURCE_LABELS = {"imu": "IMU", "rgb": "RGB", "optitrack": "OptiTrack"}

    def _build_trial_row(self, t: dict) -> None:
        row = tk.Frame(self._trial_list_container, bg=ws.PALETTE["PANEL"])
        row.pack(side="top", fill="x", pady=1)
        src_label = " + ".join(self._SOURCE_LABELS.get(s, s) for s in t["sources"])
        status_label = "Processing…" if t["status"] == "processing" else "Saved"
        text = f"Trial {t['trial_num']} · {src_label} · {status_label}"
        lbl = tk.Label(row, text=text, anchor="w", cursor="hand2",
                       bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"])
        lbl.pack(side="left", fill="x", expand=True, padx=(2, 4))
        lbl.bind("<Button-1>", lambda e, n=t["trial_num"]: self.controller.on_view_trial(n))
        btn_del = tk.Button(
            row, text="✕", relief="flat", bd=0, cursor="hand2",
            bg=ws.PALETTE["PANEL"], fg=_RED,
            state="disabled" if t["status"] == "processing" else "normal",
            command=lambda n=t["trial_num"]: self._on_delete_clicked(n))
        btn_del.pack(side="right", padx=4)

    def _on_delete_clicked(self, trial_num: int) -> None:
        if messagebox.askyesno(
                "Delete Trial",
                f"Delete Trial {trial_num}? This removes its saved files "
                "and can't be undone."):
            self.controller.on_delete_trial(trial_num)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -k "multi_trial_list or delete_ or row_click or telemetry_canvas_still" -v`
Expected: PASS

- [ ] **Step 5: Run the full acquisition-panel test file to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_acquisition_panel.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: render the per-session trial list with delete/view actions"
```

---

## Task 4: Multi-trial finalize path (skip forced review + confirmation)

**Files:**
- Modify: `pendulastic_app.py:3330-3348` (`App._transition_to_review`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `App._session_trials` (Task 2), `App._is_multi_trial_mode()` (Task 2), `AcquisitionPanel.set_multi_trial_list()` (Task 3)
- Produces: `App._finish_trial_multi_mode(source_angles: dict, meta: dict, base_fn: str) -> None`; `App._session_trials_view() -> list[dict]` (the `{"trial_num", "sources", "status"}` view `AcquisitionPanel.set_multi_trial_list` expects)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_transition_to_review_multi_trial_mode_skips_post_panel(monkeypatch):
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app = _m.App()
    try:
        app._acq.pid_var.set("P1")
        app._acq.trial_var.set("1")
        app._acq._multi_trial_var.set(True)
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "processing",
            "meta": meta, "source_angles": None, "fps": None,
            "base_filename": None, "file_paths": [],
        }]
        app._acq.pack(fill="both", expand=True)
        app.update()
        app._transition_to_review({"imu": [1.0, 2.0]}, meta, from_recording=True)
        app.update()
        assert shown == []
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
        assert app._state == "idle"
    finally:
        app.destroy()


def test_finish_trial_multi_mode_updates_entry_and_increments_trial():
    from pendulastic_app import App
    app = App()
    try:
        app._acq.trial_var.set("1")
        meta = {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "processing",
            "meta": meta, "source_angles": None, "fps": None,
            "base_filename": None, "file_paths": [],
        }]
        app._finish_trial_multi_mode(
            {"imu": [1.0, 2.0]}, meta, "PID_P1_LEG_Right_MS_TRIAL_1.csv")
        app.update()
        entry = app._session_trials[0]
        assert entry["status"] == "saved"
        assert entry["source_angles"] == {"imu": [1.0, 2.0]}
        assert entry["base_filename"] == "PID_P1_LEG_Right_MS_TRIAL_1.csv"
        assert int(app._acq.trial_var.get()) == 2
    finally:
        app.destroy()


def test_transition_to_review_single_trial_mode_unchanged(monkeypatch):
    """Toggle off must still force the review screen + confirmation,
    exactly as before this feature existed."""
    import pendulastic_app as _m
    shown = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda *a, **k: shown.append(a))
    app = _m.App()
    try:
        meta = {"pid": "P9", "leg": "Right", "ms_status": "MS", "trial": 1}
        app._transition_to_review({"imu": [1.0, 2.0]}, meta, from_recording=True)
        app.update()
        assert len(shown) == 1
        assert app._post.winfo_ismapped()
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "multi_trial_mode_skips or finish_trial_multi_mode or single_trial_mode_unchanged" -v`
Expected: FAIL — `test_transition_to_review_multi_trial_mode_skips_post_panel` fails because `_post` is still packed; `test_finish_trial_multi_mode_updates_entry_and_increments_trial` fails with `AttributeError: 'App' object has no attribute '_finish_trial_multi_mode'`

- [ ] **Step 3: Branch `_transition_to_review` and add the finalizer**

In `pendulastic_app.py`, replace `_transition_to_review` (currently lines 3330-3348):

```python
    def _transition_to_review(self, source_angles: dict, meta: dict,
                              from_recording: bool = False) -> None:
        """from_recording distinguishes an actual live-recording stop (which
        gets a "Recording Saved" confirmation) from the upload-CSV/
        upload-video-file review paths, which process an already-existing
        file rather than saving a new one."""
        self._state = "review"
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        self._post.load_trial(source_angles, self._fps_for(meta), meta, base_fn)
        self._acq.pack_forget()
        self._upload_meta.pack_forget()
        self._post.pack(fill="both", expand=True)
        try:
            self.state("zoomed")
        except Exception:
            pass
        if from_recording:
            self._show_recording_saved_confirmation(source_angles, meta, base_fn)
```

with:

```python
    def _transition_to_review(self, source_angles: dict, meta: dict,
                              from_recording: bool = False) -> None:
        """from_recording distinguishes an actual live-recording stop (which
        gets a "Recording Saved" confirmation) from the upload-CSV/
        upload-video-file review paths, which process an already-existing
        file rather than saving a new one. In multi-trial mode, a live
        recording never reaches this screen automatically at all -- see
        _finish_trial_multi_mode()."""
        base_fn = DataManager.build_filename(
            meta["pid"], meta["leg"], meta["ms_status"], meta["trial"])
        if from_recording and self._is_multi_trial_mode():
            self._finish_trial_multi_mode(source_angles, meta, base_fn)
            return
        self._state = "review"
        self._post.load_trial(source_angles, self._fps_for(meta), meta, base_fn)
        self._acq.pack_forget()
        self._upload_meta.pack_forget()
        self._post.pack(fill="both", expand=True)
        try:
            self.state("zoomed")
        except Exception:
            pass
        if from_recording:
            self._show_recording_saved_confirmation(source_angles, meta, base_fn)

    def _finish_trial_multi_mode(self, source_angles: dict, meta: dict, base_fn: str) -> None:
        entry = next((e for e in self._session_trials
                     if e["trial_num"] == meta["trial"]), None)
        if entry is not None:
            entry["status"] = "saved"
            entry["source_angles"] = source_angles
            entry["fps"] = self._fps_for(meta)
            entry["base_filename"] = base_fn
        self._acq.increment_trial()
        self._acq.set_multi_trial_list(self._session_trials_view())
        self._acq.enter_idle()
        self._state = "idle"

    def _session_trials_view(self) -> list:
        return [{"trial_num": e["trial_num"], "sources": e["sources"], "status": e["status"]}
                for e in self._session_trials]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "multi_trial_mode_skips or finish_trial_multi_mode or single_trial_mode_unchanged" -v`
Expected: PASS

- [ ] **Step 5: Run the full app test file to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: finalize multi-trial recordings without forcing the review screen"
```

---

## Task 5: Delete a recorded trial

**Files:**
- Modify: `pendulastic_app.py` — add `App.on_delete_trial` (near `on_view_trial`'s future location, e.g. right after `_session_trials_view`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `App._session_trials`, `App._session_trials_view()`, `AcquisitionPanel.set_multi_trial_list()` (Task 2-4)
- Produces: `App.on_delete_trial(trial_num: int) -> None` (called by `AcquisitionPanel` per Task 3's `controller.on_delete_trial`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_on_delete_trial_removes_files_and_entry(tmp_path, monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        f1 = tmp_path / "trial1_imu.csv"
        f1.write_text("data")
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [str(f1)],
        }]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(1)
        app.update()
        assert not f1.exists()
        assert app._session_trials == []
    finally:
        app.destroy()


def test_on_delete_trial_ignores_missing_files(tmp_path, monkeypatch):
    """A file already gone (e.g. hand-deleted) must not raise."""
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        missing = str(tmp_path / "already_gone.csv")
        app._session_trials = [{
            "trial_num": 2, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [missing],
        }]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(2)
        app.update()
        assert app._session_trials == []
    finally:
        app.destroy()


def test_on_delete_trial_unknown_trial_num_is_noop():
    from pendulastic_app import App
    app = App()
    try:
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [],
        }]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(99)
        app.update()
        assert len(app._session_trials) == 1
    finally:
        app.destroy()


def test_on_delete_trial_leaves_gap_in_numbering(tmp_path, monkeypatch):
    """Deleting Trial 2 of {1,2,3} must not renumber 3 down to 2, and a
    freshly-recorded next trial (driven by the spinner, untouched by
    delete) must not reuse 2."""
    import pendulastic_app as _m
    monkeypatch.setattr(_m.DataManager, "DATA_DIR", str(tmp_path))
    app = _m.App()
    try:
        app._acq.trial_var.set("4")   # spinner already past trial 3
        app._session_trials = [
            {"trial_num": 1, "sources": ["imu"], "status": "saved",
             "meta": {}, "source_angles": {}, "fps": 30.0,
             "base_filename": "a.csv", "file_paths": []},
            {"trial_num": 2, "sources": ["imu"], "status": "saved",
             "meta": {}, "source_angles": {}, "fps": 30.0,
             "base_filename": "b.csv", "file_paths": []},
            {"trial_num": 3, "sources": ["imu"], "status": "saved",
             "meta": {}, "source_angles": {}, "fps": 30.0,
             "base_filename": "c.csv", "file_paths": []},
        ]
        app._acq.pack(fill="both", expand=True)
        app.on_delete_trial(2)
        app.update()
        remaining = sorted(e["trial_num"] for e in app._session_trials)
        assert remaining == [1, 3]
        assert int(app._acq.trial_var.get()) == 4   # untouched by delete
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k on_delete_trial -v`
Expected: FAIL with `AttributeError: 'App' object has no attribute 'on_delete_trial'`

- [ ] **Step 3: Implement the deletion**

Add to `pendulastic_app.py`, right after `_session_trials_view` (Task 4):

```python
    def on_delete_trial(self, trial_num: int) -> None:
        entry = next((e for e in self._session_trials
                     if e["trial_num"] == trial_num), None)
        if entry is None:
            return
        for path in entry["file_paths"]:
            try:
                os.remove(path)
            except OSError:
                pass
        self._session_trials.remove(entry)
        self._acq.set_multi_trial_list(self._session_trials_view())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k on_delete_trial -v`
Expected: PASS

- [ ] **Step 5: Run the full app test file to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: delete a recorded trial's saved files from the trial list"
```

---

## Task 6: View trial detail + context-aware back navigation

**Files:**
- Modify: `pendulastic_app.py` — `PostProcessingPanel.__init__` and `_build_widgets` (row 3 "← New Trial" button, currently lines 1563-1564), `PostProcessingPanel._on_new_trial` (lines 1896-1897), `App` (add `on_view_trial`, `on_back_to_trial_list`; modify `_transition_to_review`'s non-multi branch to call `set_back_context(False)`)
- Modify: `tests/test_post_processing_panel.py` (`_Ctrl` fake gains `on_back_to_trial_list`)
- Test: `tests/test_post_processing_panel.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `App._session_trials` (Task 2), `PostProcessingPanel.load_trial()` (existing)
- Produces: `PostProcessingPanel.set_back_context(from_trial_list: bool) -> None`; `PostProcessingPanel.btn_new_trial: tk.Button`; `App.on_view_trial(trial_num: int) -> None`; `App.on_back_to_trial_list() -> None`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_post_processing_panel.py`, and extend the `_Ctrl` class with:

```python
    def on_back_to_trial_list(self): pass
```

Then add:

```python
def test_set_back_context_true_relabels_button():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    p.set_back_context(from_trial_list=True)
    assert p.btn_new_trial["text"] == "← Back to Trials"


def test_set_back_context_false_relabels_button():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    p = PostProcessingPanel(r, _Ctrl())
    p.pack(fill="both", expand=True); r.update()
    p.set_back_context(from_trial_list=True)
    p.set_back_context(from_trial_list=False)
    assert p.btn_new_trial["text"] == "← New Trial"


def test_back_button_routes_to_trial_list_when_from_trial_list():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_back_to_trial_list(self): calls.append("trial_list")
        def on_new_trial(self): calls.append("new_trial")
    p = PostProcessingPanel(r, C())
    p.pack(fill="both", expand=True); r.update()
    p.set_back_context(from_trial_list=True)
    p._on_new_trial()
    assert calls == ["trial_list"]


def test_back_button_routes_to_new_trial_by_default():
    from pendulastic_app import PostProcessingPanel
    r = _get_root()
    calls = []
    class C(_Ctrl):
        def on_back_to_trial_list(self): calls.append("trial_list")
        def on_new_trial(self): calls.append("new_trial")
    p = PostProcessingPanel(r, C())
    p.pack(fill="both", expand=True); r.update()
    p._on_new_trial()
    assert calls == ["new_trial"]
```

Add to `tests/test_app.py`:

```python
def test_on_view_trial_shows_post_processing_panel():
    from pendulastic_app import App
    app = App()
    try:
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "saved",
            "meta": {"pid": "P1", "leg": "Right", "ms_status": "MS", "trial": 1},
            "source_angles": {"imu": [170.0, 165.0]}, "fps": 30.0,
            "base_filename": "PID_P1_LEG_Right_MS_TRIAL_1.csv",
            "file_paths": [],
        }]
        app._acq.pack(fill="both", expand=True)
        app.on_view_trial(1)
        app.update()
        assert app._post.winfo_ismapped()
        assert not app._acq.winfo_ismapped()
        assert app._post._from_trial_list is True
    finally:
        app.destroy()


def test_on_view_trial_ignores_unknown_trial():
    from pendulastic_app import App
    app = App()
    try:
        app._acq.pack(fill="both", expand=True)
        app.on_view_trial(99)
        app.update()
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
    finally:
        app.destroy()


def test_on_back_to_trial_list_returns_to_acquisition_without_incrementing():
    from pendulastic_app import App
    app = App()
    try:
        app._acq.trial_var.set("4")
        app._acq._multi_trial_var.set(True)
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [],
        }]
        app._acq.set_multi_trial_list(app._session_trials_view())
        app._post.pack(fill="both", expand=True)
        app.on_back_to_trial_list()
        app.update()
        assert app._acq.winfo_ismapped()
        assert not app._post.winfo_ismapped()
        assert int(app._acq.trial_var.get()) == 4
        # The trial list survives the round trip -- on_back_to_trial_list
        # must not clear or touch it.
        assert len(app._acq._trial_rows_data) == 1
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_post_processing_panel.py -k "back_context or back_button" -v`
Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "on_view_trial or on_back_to_trial_list" -v`
Expected: FAIL — `AttributeError: 'PostProcessingPanel' object has no attribute 'set_back_context'` / `'App' object has no attribute 'on_view_trial'`

- [ ] **Step 3: Implement**

In `pendulastic_app.py`, in `PostProcessingPanel.__init__`, add `self._from_trial_list = False` alongside the other instance attributes (near `self._meta: dict | None = None`).

Replace the "← New Trial" button line (currently lines 1563-1564):

```python
        ws.secondary_button(self, "← New Trial", self._on_new_trial).grid(
            row=3, column=0, padx=10, pady=12, sticky="e")
```

with:

```python
        self.btn_new_trial = ws.secondary_button(self, "← New Trial", self._on_new_trial)
        self.btn_new_trial.grid(row=3, column=0, padx=10, pady=12, sticky="e")
```

Add `set_back_context` near `load_trial` in the "Public API" section:

```python
    def set_back_context(self, from_trial_list: bool) -> None:
        self._from_trial_list = from_trial_list
        self.btn_new_trial.config(
            text="← Back to Trials" if from_trial_list else "← New Trial")
```

Replace `_on_new_trial` (currently lines 1896-1897):

```python
    def _on_new_trial(self) -> None:
        self.controller.on_new_trial()
```

with:

```python
    def _on_new_trial(self) -> None:
        if self._from_trial_list:
            self.controller.on_back_to_trial_list()
        else:
            self.controller.on_new_trial()
```

In `App._transition_to_review`'s non-multi-trial branch (Task 4's version), add a call to reset the context flag every time the traditional flow shows the panel — right before `self._post.load_trial(...)`:

```python
        self._state = "review"
        self._post.set_back_context(from_trial_list=False)
        self._post.load_trial(source_angles, self._fps_for(meta), meta, base_fn)
```

Add to `App`, near `on_delete_trial` (Task 5):

```python
    def on_view_trial(self, trial_num: int) -> None:
        entry = next((e for e in self._session_trials
                     if e["trial_num"] == trial_num), None)
        if entry is None or entry["status"] != "saved":
            return
        self._state = "review"
        self._post.set_back_context(from_trial_list=True)
        self._post.load_trial(entry["source_angles"], entry["fps"],
                              entry["meta"], entry["base_filename"])
        self._acq.pack_forget()
        self._post.pack(fill="both", expand=True)

    def on_back_to_trial_list(self) -> None:
        self._post.pack_forget()
        self._acq.pack(fill="both", expand=True)
        self._acq.enter_idle()
        self._state = "idle"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_post_processing_panel.py -k "back_context or back_button" -v`
Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "on_view_trial or on_back_to_trial_list" -v`
Expected: PASS

- [ ] **Step 5: Run both full test files to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_post_processing_panel.py tests/test_app.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_post_processing_panel.py tests/test_app.py
git commit -m "feat: view a session trial's analysis on demand with context-aware back nav"
```

---

## Task 7: Clear session trial list on leaving the acquisition screen

**Files:**
- Modify: `pendulastic_app.py:2987-3004` (`App.on_back_to_mode_select`)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `App._session_trials` (Task 2), `AcquisitionPanel.set_multi_trial_list()` (Task 3)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app.py`:

```python
def test_on_back_to_mode_select_clears_session_trials():
    from pendulastic_app import App
    app = App()
    try:
        app._session_trials = [{
            "trial_num": 1, "sources": ["imu"], "status": "saved",
            "meta": {}, "source_angles": {}, "fps": 30.0,
            "base_filename": "x.csv", "file_paths": [],
        }]
        app._acq._multi_trial_var.set(True)
        app._acq.set_multi_trial_list(app._session_trials_view())
        app.on_back_to_mode_select()
        app.update()
        assert app._session_trials == []
        assert app._acq._trial_rows_data == []
    finally:
        app.destroy()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k clears_session_trials -v`
Expected: FAIL — `app._session_trials` still has 1 entry after `on_back_to_mode_select()`

- [ ] **Step 3: Clear the list**

In `pendulastic_app.py`, in `on_back_to_mode_select` (currently lines 2987-3004), add two lines after `self._pending_review  = {}`:

```python
        self._pending_review  = {}
        self._session_trials  = []
        self._acq.set_multi_trial_list([])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k clears_session_trials -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py tests/test_acquisition_panel.py tests/test_post_processing_panel.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: clear the session trial list on returning to mode select"
```
