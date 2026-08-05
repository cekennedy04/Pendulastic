# Acquisition Screen Clinician UX & App-Wide Style Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify `pendulastic_app.py` onto `workbench_style.py`'s palette (Modern Clinical Dashboard direction), and fix `AcquisitionPanel`'s source defaults and information density so the routine clinical case (IMU + RGB) needs zero clicks and the research-only sources (OptiTrack, Video File) are one click away instead of always-visible.

**Architecture:** `workbench_style.py` gets a one-value palette edit (`BG` from pure white to a soft light-gray) — no structural changes. `pendulastic_app.py` promotes `workbench_style` from a Workbench-only dependency to an app-wide one, and every panel (`AcquisitionPanel`, `ModeSelectView`, `UploadMetaView`, `PostProcessingPanel`) swaps its hardcoded colors for `ws.PALETTE`/`ws.card_frame`/`ws.primary_button`/`ws.secondary_button`. `AcquisitionPanel`'s source checkboxes get new defaults (IMU+RGB checked) and OptiTrack/Video File move behind a collapsible "Research sources" disclosure, using the same `pack()`/`pack_forget()` show/hide mechanism already used elsewhere in that file.

**Tech Stack:** Python 3.13, Tkinter/`ttk`, `pytest` with headless `tk.Tk()` + `.withdraw()` roots (existing convention in `tests/test_acquisition_panel.py`).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-05-acquisition-screen-clinician-ux-design.md` — approved by the user.
- **Recording-state buttons keep their semantic colors.** `_GREEN` (START, "go"), `_RED` (STOP), `_AMBER` (countdown CANCEL) are live state indicators, not chrome — they are **not** touched by this plan. Every other button/frame/label switches to `ws.PALETTE`/`ws.primary_button`/`ws.secondary_button`.
- **No grid/pack restructuring beyond what Task 3 requires for the disclosure.** Every existing widget keeps its current parent and `grid()`/`pack()` call; only styling arguments (and, in `AcquisitionPanel`, the two behavior changes below) change. This is a restyle pass, not a rewrite.
- **`canvas_tele`'s dark-navy sparkline (`#0B1928` background, white/`#5A8AB0` text) is explicitly out of scope** — it's a self-contained live telemetry instrument, not chrome, and its text colors are tuned for that dark background specifically.
- No change to `validate_metadata()`, `get_metadata()`, `get_active_sources()`, or any recording/processing logic — defaults, visibility, and color only.
- `pendulastic_workbench.py`, `pendulastic_viewer.py`, and `workbench_engine.py` are not modified.

---

### Task 1: Promote `workbench_style` to an app-wide, unconditional dependency

**Files:**
- Modify: `workbench_style.py:52-63` (`PALETTE` dict)
- Modify: `pendulastic_app.py:102-111` (guarded import block), `pendulastic_app.py:1352-1361` (`App.__init__`'s Workbench section)
- Test: `tests/test_app.py` (new)

**Interfaces:**
- Produces: `pendulastic_app.ws` (module-level, always-available reference to `workbench_style`, replacing the guarded `_wb_style` name). `workbench_style.PALETTE["BG"] == "#F4F6F9"`.
- Consumes: nothing new — `workbench_style.apply_ttk_theme`/`PALETTE` already exist.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`:

```python
def test_ws_palette_bg_is_light_gray():
    import workbench_style as ws
    assert ws.PALETTE["BG"] == "#F4F6F9"


def test_app_applies_ttk_theme_even_when_workbench_unavailable(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m, "_WORKBENCH_AVAIL", False)
    calls = []
    monkeypatch.setattr(_m.ws, "apply_ttk_theme", lambda root: calls.append(root))
    app = _m.App()
    try:
        assert len(calls) == 1
        assert str(app.cget("bg")) == _m.ws.PALETTE["BG"]
    finally:
        app.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_app.py -k "ws_palette_bg_is_light_gray or applies_ttk_theme_even_when_workbench_unavailable" -v
```

Expected: FAIL — `ws.PALETTE["BG"]` is still `"#FFFFFF"`, and `pendulastic_app` has no module-level `ws` attribute (`AttributeError`).

- [ ] **Step 3: Edit `workbench_style.py`'s `PALETTE`**

In `workbench_style.py`, change line 53:

```python
# OLD (line 53)
    "BG":      "#FFFFFF",

# NEW
    "BG":      "#F4F6F9",
```

- [ ] **Step 4: Restructure the guarded import block in `pendulastic_app.py`**

Replace lines 102-111:

```python
# OLD (lines 102-111)
try:
    from pendulastic_workbench import TrialLoadPanel, WorkbenchView
    import workbench_engine as _wb_engine
    import workbench_style as _wb_style
    _WORKBENCH_AVAIL = True
except Exception:
    TrialLoadPanel = WorkbenchView = None
    _wb_engine = None
    _wb_style = None
    _WORKBENCH_AVAIL = False
```

```python
# NEW
import workbench_style as ws   # zero-dependency (tkinter only) -- always available

try:
    from pendulastic_workbench import TrialLoadPanel, WorkbenchView
    import workbench_engine as _wb_engine
    _WORKBENCH_AVAIL = True
except Exception:
    TrialLoadPanel = WorkbenchView = None
    _wb_engine = None
    _WORKBENCH_AVAIL = False
```

- [ ] **Step 5: Apply the theme unconditionally in `App.__init__`**

Replace lines 1352-1361:

```python
# OLD (lines 1352-1361)
        self._workbench_status_var = tk.StringVar(value="")
        if _WORKBENCH_AVAIL:
            # Registers the dark "Workbench.*" ttk styles the embedded panels
            # opt into. It does not switch this root's base ttk theme, so the
            # other panels' ttk.Combobox/ttk.Separator widgets are untouched.
            _wb_style.apply_ttk_theme(self)
            self._workbench_load = TrialLoadPanel(self, controller=self)
            self._workbench_view = WorkbenchView(self, controller=self)
            tk.Label(self, textvariable=self._workbench_status_var, anchor="w").pack(
                side="bottom", fill="x", padx=8, pady=2)
```

```python
# NEW
        self._workbench_status_var = tk.StringVar(value="")
        if _WORKBENCH_AVAIL:
            self._workbench_load = TrialLoadPanel(self, controller=self)
            self._workbench_view = WorkbenchView(self, controller=self)
            tk.Label(self, textvariable=self._workbench_status_var, anchor="w").pack(
                side="bottom", fill="x", padx=8, pady=2)
```

Then, immediately before the `self._mode_select = ModeSelectView(...)` line (line 1344), add:

```python
        # Applies the shared "clam"-based ttk theme (and dark-styled
        # Workbench.* variants) app-wide -- this switches every ttk widget's
        # base theme, not just the Workbench panels'. Kept unconditional so
        # it doesn't depend on the heavier Workbench feature-availability
        # guard below (workbench_style itself has zero non-tkinter deps).
        ws.apply_ttk_theme(self)
        self.configure(bg=ws.PALETTE["BG"])

```

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_app.py -k "ws_palette_bg_is_light_gray or applies_ttk_theme_even_when_workbench_unavailable" -v
```

Expected: both PASS

- [ ] **Step 7: Run the full test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\ -v
```

Expected: same pass/fail counts as before this change (no new failures).

- [ ] **Step 8: Commit**

```bash
git add workbench_style.py pendulastic_app.py tests/test_app.py
git commit -m "feat: promote workbench_style to an app-wide palette, light-gray BG"
```

---

### Task 2: Flip `AcquisitionPanel`'s default recording sources

**Files:**
- Modify: `pendulastic_app.py:409-412` (`AcquisitionPanel._build_widgets`)
- Test: `tests/test_acquisition_panel.py:40-52` (`test_default_vars`)

**Interfaces:**
- Produces: `AcquisitionPanel._src_imu` and `_src_rgb` default `True`; `_src_optitrack` and `_src_video_file` default `False`.
- Consumes: nothing new.

- [ ] **Step 1: Update the existing test to the new expected defaults**

In `tests/test_acquisition_panel.py`, replace `test_default_vars` (lines 40-52):

```python
# OLD
def test_default_vars():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        # Multi-source: optitrack checked by default, others unchecked
        assert p._src_optitrack.get() is True
        assert p._src_rgb.get() is False
        assert p._src_imu.get() is False
        assert p.countdown_var.get() is False
        assert int(p.trial_var.get()) == 1
    finally:
        r.destroy()
```

```python
# NEW
def test_default_vars():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl())
        # Routine clinical sources (IMU + RGB) checked by default;
        # research-only sources (OptiTrack, Video File) start unchecked.
        assert p._src_imu.get() is True
        assert p._src_rgb.get() is True
        assert p._src_optitrack.get() is False
        assert p._src_video_file.get() is False
        assert p.countdown_var.get() is False
        assert int(p.trial_var.get()) == 1
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_default_vars -v
```

Expected: FAIL — `p._src_imu.get()` is `False`, not `True`.

- [ ] **Step 3: Flip the defaults**

In `pendulastic_app.py`, replace lines 409-412:

```python
# OLD
        self._src_optitrack  = tk.BooleanVar(value=True)
        self._src_rgb        = tk.BooleanVar(value=False)
        self._src_imu        = tk.BooleanVar(value=False)
        self._src_video_file = tk.BooleanVar(value=False)
```

```python
# NEW
        self._src_optitrack  = tk.BooleanVar(value=False)
        self._src_rgb        = tk.BooleanVar(value=True)
        self._src_imu        = tk.BooleanVar(value=True)
        self._src_video_file = tk.BooleanVar(value=False)
```

Note: `_build_widgets` already calls `self._on_source_changed()` as its last line (line 517 in the current file) — this recomputes `lbl_method_status`'s text from the live `BooleanVar` values, so no separate edit is needed for the status label; it already reflects whatever the defaults are.

- [ ] **Step 4: Run the test to verify it passes**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_default_vars -v
```

Expected: PASS

- [ ] **Step 5: Run the full acquisition-panel test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```

Expected: all pass. (Tests that call `p._src_optitrack.set(...)` explicitly, e.g. lines 151/170/320/338, are unaffected — they set the value directly regardless of the initial default.)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "fix: default AcquisitionPanel to IMU+RGB, the routine clinical sources"
```

---

### Task 3: "Research sources" disclosure for OptiTrack + Video File

**Files:**
- Modify: `pendulastic_app.py:414-463` (`AcquisitionPanel._build_widgets`'s `meth_f` block), `pendulastic_app.py:510-514` (`_lockable` list)
- Test: `tests/test_acquisition_panel.py` (append)

**Interfaces:**
- Consumes: `AcquisitionPanel._on_source_changed` (existing), `_on_rgb_checkbox_toggled` (existing), `_on_browse_video` (existing) — all unchanged.
- Produces: `AcquisitionPanel._research_toggle_btn: tk.Button`, `AcquisitionPanel._research_sources_frame: tk.Frame` (starts unpacked/hidden), `AcquisitionPanel._research_sources_expanded: bool` (starts `False`), `AcquisitionPanel._on_toggle_research_sources() -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_acquisition_panel.py`:

```python
def test_research_sources_frame_hidden_by_default():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert p._research_sources_frame.winfo_manager() == ""
    finally:
        r.destroy()


def test_toggle_research_sources_shows_and_hides_frame():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._on_toggle_research_sources()
        r.update()
        assert p._research_sources_frame.winfo_manager() == "pack"
        p._on_toggle_research_sources()
        r.update()
        assert p._research_sources_frame.winfo_manager() == ""
    finally:
        r.destroy()


def test_toggling_research_sources_does_not_change_source_values():
    from pendulastic_app import AcquisitionPanel
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        p._src_optitrack.set(True)
        p._on_source_changed()
        p._on_toggle_research_sources()   # expand
        r.update()
        assert p._src_optitrack.get() is True
        assert "optitrack" in p.get_active_sources()
        p._on_toggle_research_sources()   # collapse
        r.update()
        assert p._src_optitrack.get() is True
        assert "optitrack" in p.get_active_sources()
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -k "research_sources" -v
```

Expected: FAIL — `AttributeError: 'AcquisitionPanel' object has no attribute '_research_sources_frame'`.

- [ ] **Step 3: Replace the `meth_f` block**

In `pendulastic_app.py`, replace lines 414-463 (from `meth_f = tk.Frame(self)` through the `self._camera_live = False` line):

```python
        meth_f = tk.Frame(self, bg=ws.PALETTE["PANEL"],
                         highlightbackground=ws.PALETTE["BORDER"],
                         highlightthickness=1, padx=10, pady=8)
        meth_f.grid(row=8, column=0, columnspan=2, sticky="w", padx=12, pady=2)

        tk.Label(meth_f, text="RECORDING SOURCE", font=("Segoe UI", 8, "bold"),
                bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG3"]).pack(
            side="top", anchor="w", pady=(0, 4))

        # Always-visible routine sources
        chk_row = tk.Frame(meth_f, bg=ws.PALETTE["PANEL"])
        chk_row.pack(side="top", anchor="w")
        chk_imu = tk.Checkbutton(chk_row, text="iPhone IMU",
                                 variable=self._src_imu,
                                 bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                 selectcolor=ws.PALETTE["SURFACE"],
                                 activebackground=ws.PALETTE["PANEL"],
                                 command=self._on_source_changed)
        chk_rgb = tk.Checkbutton(chk_row, text="RGB",
                                 variable=self._src_rgb,
                                 bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                 selectcolor=ws.PALETTE["SURFACE"],
                                 activebackground=ws.PALETTE["PANEL"],
                                 command=self._on_rgb_checkbox_toggled)
        for chk in (chk_imu, chk_rgb):
            chk.pack(side="left", padx=8)

        # Collapsed "Research sources" disclosure -- OptiTrack and Video
        # File are research-only extras, rarely used in routine clinical
        # sessions (design spec Section 3), so they start hidden.
        self._research_toggle_btn = tk.Button(
            meth_f, text="▸ Research sources (OptiTrack, Video File)",
            font=("Segoe UI", 8), fg=ws.PALETTE["BTN_ACT"], bg=ws.PALETTE["PANEL"],
            relief="flat", bd=0, cursor="hand2", anchor="w",
            activebackground=ws.PALETTE["PANEL"], activeforeground=ws.PALETTE["BTN_ACT"],
            command=self._on_toggle_research_sources)
        self._research_toggle_btn.pack(side="top", anchor="w", pady=(4, 0))

        self._research_sources_frame = tk.Frame(meth_f, bg=ws.PALETTE["PANEL"])
        self._research_sources_expanded = False

        chk_opti = tk.Checkbutton(self._research_sources_frame, text="OptiTrack",
                                  variable=self._src_optitrack,
                                  bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                  selectcolor=ws.PALETTE["SURFACE"],
                                  activebackground=ws.PALETTE["PANEL"],
                                  command=self._on_source_changed)
        chk_video = tk.Checkbutton(self._research_sources_frame, text="Video File",
                                   variable=self._src_video_file,
                                   bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                                   selectcolor=ws.PALETTE["SURFACE"],
                                   activebackground=ws.PALETTE["PANEL"],
                                   command=self._on_source_changed)
        chk_opti.pack(side="left", padx=8)
        chk_video.pack(side="left", padx=8)

        # Video file path selector (hidden until _src_video_file checked) --
        # nested inside the research-sources frame since it's a
        # research-only source.
        self._video_path_frame = tk.Frame(self._research_sources_frame, bg=ws.PALETTE["PANEL"])
        self._video_path_var    = tk.StringVar(value="No file selected")
        self._stored_video_path = ""
        tk.Label(self._video_path_frame,
                textvariable=self._video_path_var,
                font=("Consolas", 8), fg=ws.PALETTE["FG2"], bg=ws.PALETTE["PANEL"],
                width=38, anchor="w").pack(side="left")
        ws.secondary_button(self._video_path_frame, "Browse...",
                            self._on_browse_video).pack(side="left", padx=4)
        self._video_path_frame.pack(side="top", anchor="w", pady=(2, 0))
        self._video_path_frame.pack_forget()   # hidden until checkbox checked

        # Camera selector (hidden until RGB is checked) -- unaffected by the
        # research-sources disclosure; RGB is a routine, always-visible source.
        self._cam_frame = tk.Frame(meth_f, bg=ws.PALETTE["PANEL"])
        self.cam_var = tk.StringVar(value="")
        self.drop_cam = ttk.Combobox(self._cam_frame, textvariable=self.cam_var,
                                     width=18, state="readonly")
        self.drop_cam.pack(side="left")
        self.drop_cam.bind("<<ComboboxSelected>>", self._on_cam_selected)
        self.btn_rescan = ws.secondary_button(self._cam_frame, "Rescan", self._on_rescan_clicked)
        self.btn_rescan.pack(side="left", padx=4)
        ws.secondary_button(self._cam_frame, "\U0001f6dc Can't connect?",
                            self._on_camera_help).pack(side="left", padx=4)
        self._cam_frame.pack_forget()   # hidden until RGB is checked
        self._viewer_window: Optional[WebcamViewerWindow] = None
        self._camera_live = False   # one input to _sync_viewer_window_visibility()
```

- [ ] **Step 4: Add `_on_toggle_research_sources`**

Add this method to `AcquisitionPanel`, near `_on_rgb_checkbox_toggled`:

```python
    def _on_toggle_research_sources(self) -> None:
        self._research_sources_expanded = not self._research_sources_expanded
        if self._research_sources_expanded:
            self._research_sources_frame.pack(side="top", anchor="w", pady=(4, 0))
            self._research_toggle_btn.config(text="▾ Research sources (OptiTrack, Video File)")
        else:
            self._research_sources_frame.pack_forget()
            self._research_toggle_btn.config(text="▸ Research sources (OptiTrack, Video File)")
```

- [ ] **Step 5: Add the toggle button to `_lockable`**

Replace lines 510-514:

```python
# OLD
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            self.countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            self.btn_back, self.drop_cam, self.btn_rescan,
        ]
```

```python
# NEW
        self._lockable = [
            pid_entry, rb_left, rb_right, ms_combo, trial_spin,
            self.countdown_chk, chk_opti, chk_rgb, chk_imu, chk_video,
            self._research_toggle_btn,
            self.btn_back, self.drop_cam, self.btn_rescan,
        ]
```

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -k "research_sources" -v
```

Expected: all 3 PASS

- [ ] **Step 7: Run the full acquisition-panel test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```

Expected: all pass, including `test_camera_frame_hidden_by_default`, `test_checking_rgb_shows_camera_frame_and_rescans`, and `test_unchecking_rgb_hides_camera_frame_and_disables_camera` (the RGB/camera-frame relationship is untouched by this task).

- [ ] **Step 8: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "feat: collapse OptiTrack/Video File behind a Research sources disclosure"
```

---

### Task 4: Restyle the rest of `AcquisitionPanel`

**Files:**
- Modify: `pendulastic_app.py:339-407` (`__init__`, header, participant/session fields), `pendulastic_app.py:463-509` (status label, countdown, status bar)
- Test: `tests/test_acquisition_panel.py` (append)

**Interfaces:**
- Consumes: `ws.PALETTE`, `ws.secondary_button` (Task 1/3).
- Produces: no new public attributes — styling only. `AcquisitionPanel.cget("bg") == ws.PALETTE["BG"]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_acquisition_panel.py`:

```python
def test_panel_and_header_use_shared_palette():
    from pendulastic_app import AcquisitionPanel
    import workbench_style as ws
    r = _root()
    try:
        p = AcquisitionPanel(r, _Ctrl()); p.pack(); r.update()
        assert str(p.cget("bg")) == ws.PALETTE["BG"]
        assert str(p.lbl_status.cget("bg")) == ws.PALETTE["BG"]
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the test to verify it fails**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_panel_and_header_use_shared_palette -v
```

Expected: FAIL — `p.cget("bg")` is the default Tk system color, not `"#F4F6F9"`.

- [ ] **Step 3: Set the panel's own background**

In `pendulastic_app.py`, in `_build_widgets` (the method starting at line 348), change the first line of the method body:

```python
# OLD (line 349)
        pad = {"padx": 12, "pady": 5}
```

```python
# NEW
        pad = {"padx": 12, "pady": 5}
        self.configure(bg=ws.PALETTE["BG"])
```

- [ ] **Step 4: Restyle the header block**

Replace lines 354-362:

```python
# OLD
        hdr0 = tk.Frame(self)
        hdr0.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(16, 4))
        self.btn_back = tk.Button(hdr0, text="<- Mode Select",
                                  font=("Segoe UI", 9),
                                  command=self.controller.on_back_to_mode_select)
        self.btn_back.pack(side="left", padx=(0, 8))
        tk.Label(hdr0, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold")).pack(side="left")
```

```python
# NEW
        hdr0 = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr0.grid(row=0, column=0, columnspan=2, sticky="ew",
                  padx=12, pady=(16, 4))
        self.btn_back = ws.secondary_button(
            hdr0, "← Mode Select", self.controller.on_back_to_mode_select)
        self.btn_back.pack(side="left", padx=(0, 8))
        tk.Label(hdr0, text="Pendulastic — Trial Setup",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")
```

- [ ] **Step 5: Restyle the participant/session field rows**

Replace lines 369-397 (Participant ID through Trial Number):

```python
# OLD
        tk.Label(self, text="Participant ID:").grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        pid_entry = tk.Entry(self, textvariable=self.pid_var, width=22)
        pid_entry.grid(row=2, column=1, sticky="w", **pad)

        # row 3 — Leg
        tk.Label(self, text="Leg:").grid(row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self)
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        rb_left  = tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var, value="Left")
        rb_right = tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right")
        rb_left.pack(side="left", padx=4)
        rb_right.pack(side="left", padx=4)

        # row 4 — MS Status
        tk.Label(self, text="MS Status:").grid(row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ms_combo = ttk.Combobox(self, textvariable=self.ms_var, width=22,
                                state="readonly",
                                values=["MS", "Stroke", "Control", "Other"])
        ms_combo.grid(row=4, column=1, sticky="w", **pad)

        # row 5 — Trial Number
        tk.Label(self, text="Trial Number:").grid(row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        trial_spin = tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6)
        trial_spin.grid(row=5, column=1, sticky="w", **pad)
```

```python
# NEW
        tk.Label(self, text="Participant ID:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        pid_entry = tk.Entry(self, textvariable=self.pid_var, width=22)
        pid_entry.grid(row=2, column=1, sticky="w", **pad)

        # row 3 — Leg
        tk.Label(self, text="Leg:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self, bg=ws.PALETTE["BG"])
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        rb_left  = tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var, value="Left",
                                  bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"])
        rb_right = tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                                  bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"])
        rb_left.pack(side="left", padx=4)
        rb_right.pack(side="left", padx=4)

        # row 4 — MS Status
        tk.Label(self, text="MS Status:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ms_combo = ttk.Combobox(self, textvariable=self.ms_var, width=22,
                                state="readonly",
                                values=["MS", "Stroke", "Control", "Other"])
        ms_combo.grid(row=4, column=1, sticky="w", **pad)

        # row 5 — Trial Number
        tk.Label(self, text="Trial Number:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        trial_spin = tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6)
        trial_spin.grid(row=5, column=1, sticky="w", **pad)
```

- [ ] **Step 6: Restyle the Methodology label, status label, and status bar**

Replace line 404-406:

```python
# OLD
        tk.Label(self, text="Methodology",
                 font=("Segoe UI", 10, "bold")).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=12)
```

```python
# NEW
        tk.Label(self, text="Methodology",
                 font=("Segoe UI", 10, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=7, column=0, columnspan=2, sticky="w", padx=12)
```

Replace lines 468-470 (`lbl_method_status`):

```python
# OLD
        self.lbl_method_status = tk.Label(
            self, text="● OptiTrack (Motive)", font=("Consolas", 9), fg="green", anchor="w")
        self.lbl_method_status.grid(row=9, column=0, sticky="w", padx=16)
```

```python
# NEW
        self.lbl_method_status = tk.Label(
            self, text="● OptiTrack (Motive)", font=("Consolas", 9), fg="green",
            bg=ws.PALETTE["BG"], anchor="w")
        self.lbl_method_status.grid(row=9, column=0, sticky="w", padx=16)
```

(`fg` stays dynamic — `_on_source_changed` already sets it to `"green"`/`"red"` per active-source state; those are status colors, not chrome, and are left as-is.)

Replace lines 478-482 (`countdown_chk`):

```python
# OLD
        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var)
        self.countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)
```

```python
# NEW
        self.countdown_var = tk.BooleanVar(value=False)
        self.countdown_chk = tk.Checkbutton(
            self, text="5-second countdown before recording",
            variable=self.countdown_var,
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"],
            selectcolor=ws.PALETTE["SURFACE"], activebackground=ws.PALETTE["BG"])
        self.countdown_chk.grid(row=11, column=0, columnspan=2, sticky="w", padx=12, pady=4)
```

Replace lines 503-507 (status bar):

```python
# OLD
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w", fg="#333")
        self.lbl_status.grid(row=14, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))
```

```python
# NEW
        self.status_var = tk.StringVar(value="Idle — ready to record.")
        self.lbl_status = tk.Label(
            self, textvariable=self.status_var, relief="sunken", anchor="w",
            bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"])
        self.lbl_status.grid(row=14, column=0, columnspan=2,
                             sticky="ew", padx=10, pady=(4, 10))
```

- [ ] **Step 7: Run the test to verify it passes**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py::test_panel_and_header_use_shared_palette -v
```

Expected: PASS

- [ ] **Step 8: Run the full acquisition-panel test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_acquisition_panel.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add pendulastic_app.py tests/test_acquisition_panel.py
git commit -m "style: restyle AcquisitionPanel chrome onto the shared palette"
```

---

### Task 5: Restyle `ModeSelectView`, `UploadMetaView`, `PostProcessingPanel` + final verification

**Files:**
- Modify: `pendulastic_app.py:892-937` (`ModeSelectView`), `pendulastic_app.py:943-1040` (`UploadMetaView`), `pendulastic_app.py:1076-1148` (`PostProcessingPanel`'s header/figure/buttons/status)

**Interfaces:**
- Consumes: `ws.PALETTE`, `ws.primary_button`, `ws.secondary_button` (Task 1).
- Produces: no new public attributes — styling only.

- [ ] **Step 1: Restyle `ModeSelectView`**

Replace lines 903-936 (`_build_widgets`):

```python
# OLD
    def _build_widgets(self) -> None:
        tk.Label(self, text="Pendulastic",
                 font=("Segoe UI", 20, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(60, 4))
        tk.Label(self, text="Clinical Pendulum Test Platform",
                 font=("Segoe UI", 11), fg="#555").grid(
            row=1, column=0, columnspan=2, pady=(0, 40))

        tk.Button(
            self,
            text="Live Recording Session\nIMU · RGB · OptiTrack",
            font=("Segoe UI", 12, "bold"),
            bg=_GREEN, fg="white",
            width=24, height=4,
            command=self.controller._enter_live_mode,
        ).grid(row=2, column=0, padx=40, pady=16, sticky="n")

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

```python
# NEW
    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])
        tk.Label(self, text="Pendulastic",
                 font=("Segoe UI", 20, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=0, column=0, columnspan=2, pady=(60, 4))
        tk.Label(self, text="Clinical Pendulum Test Platform",
                 font=("Segoe UI", 11),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).grid(
            row=1, column=0, columnspan=2, pady=(0, 40))

        # Live Recording is the routine clinical path -- the one primary
        # (filled-accent) action on this screen.
        live_btn = ws.primary_button(
            self, "Live Recording Session\nIMU · RGB · OptiTrack",
            self.controller._enter_live_mode)
        live_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        live_btn.grid(row=2, column=0, padx=40, pady=16, sticky="n")

        upload_btn = ws.secondary_button(
            self, "Upload & Analyze\nVideo or CSV file",
            self.controller._enter_upload_mode)
        upload_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        upload_btn.grid(row=2, column=1, padx=40, pady=16, sticky="n")

        workbench_btn = ws.secondary_button(
            self, "Multi-Modal Comparison\nIMU · OptiTrack · Video",
            self.controller._enter_workbench_mode)
        workbench_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        workbench_btn.grid(row=3, column=0, columnspan=2, padx=40, pady=(0, 24), sticky="n")
```

- [ ] **Step 2: Restyle `UploadMetaView`**

Replace lines 955-1016 (`_build_widgets`):

```python
# OLD
    def _build_widgets(self) -> None:
        pad = {"padx": 12, "pady": 6}

        # Header: back button + title
        hdr = tk.Frame(self)
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew",
                 padx=12, pady=(16, 4))
        self.btn_back = tk.Button(hdr, text="<- Back",
                                  font=("Segoe UI", 10),
                                  command=self.controller._upload_back_to_select)
        self.btn_back.pack(side="left", padx=(0, 12))
        tk.Label(hdr, text="Upload & Analyze",
                 font=("Segoe UI", 13, "bold")).pack(side="left")

        # Selected file name
        self._file_label_var = tk.StringVar(value="No file selected")
        tk.Label(self, textvariable=self._file_label_var,
                 font=("Consolas", 9), fg="gray", anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

        # Participant ID
        tk.Label(self, text="Participant ID:").grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        tk.Entry(self, textvariable=self.pid_var, width=22).grid(
            row=2, column=1, sticky="w", **pad)

        # Leg
        tk.Label(self, text="Leg:").grid(row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self)
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var,
                       value="Left").pack(side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var,
                       value="Right").pack(side="left", padx=4)

        # MS Status
        tk.Label(self, text="MS Status:").grid(row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ttk.Combobox(self, textvariable=self.ms_var, width=22, state="readonly",
                     values=["MS", "Stroke", "Control", "Other"]).grid(
            row=4, column=1, sticky="w", **pad)

        # Trial number
        tk.Label(self, text="Trial Number:").grid(row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6).grid(
            row=5, column=1, sticky="w", **pad)

        # Analyze button
        self.btn_analyze = tk.Button(
            self, text="Analyze ->",
            bg=_BLUE, fg="white", font=("Segoe UI", 11, "bold"),
            width=16, height=2,
            command=self.controller._start_upload_analysis)
        self.btn_analyze.grid(row=6, column=0, columnspan=2, pady=20)

        # Status bar
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w", fg="#333").grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))
```

```python
# NEW
    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])
        pad = {"padx": 12, "pady": 6}

        # Header: back button + title
        hdr = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr.grid(row=0, column=0, columnspan=2, sticky="ew",
                 padx=12, pady=(16, 4))
        self.btn_back = ws.secondary_button(
            hdr, "← Back", self.controller._upload_back_to_select)
        self.btn_back.pack(side="left", padx=(0, 12))
        tk.Label(hdr, text="Upload & Analyze",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        # Selected file name
        self._file_label_var = tk.StringVar(value="No file selected")
        tk.Label(self, textvariable=self._file_label_var,
                 font=("Consolas", 9), fg=ws.PALETTE["FG2"], bg=ws.PALETTE["BG"],
                 anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 8))

        # Participant ID
        tk.Label(self, text="Participant ID:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=2, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        tk.Entry(self, textvariable=self.pid_var, width=22).grid(
            row=2, column=1, sticky="w", **pad)

        # Leg
        tk.Label(self, text="Leg:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=3, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Right")
        leg_f = tk.Frame(self, bg=ws.PALETTE["BG"])
        leg_f.grid(row=3, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left",  variable=self.leg_var, value="Left",
                       bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]).pack(
            side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                       bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]).pack(
            side="left", padx=4)

        # MS Status
        tk.Label(self, text="MS Status:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=4, column=0, sticky="e", **pad)
        self.ms_var = tk.StringVar(value="MS")
        ttk.Combobox(self, textvariable=self.ms_var, width=22, state="readonly",
                     values=["MS", "Stroke", "Control", "Other"]).grid(
            row=4, column=1, sticky="w", **pad)

        # Trial number
        tk.Label(self, text="Trial Number:", bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).grid(
            row=5, column=0, sticky="e", **pad)
        self.trial_var = tk.StringVar(value="1")
        tk.Spinbox(self, from_=1, to=99, textvariable=self.trial_var, width=6).grid(
            row=5, column=1, sticky="w", **pad)

        # Analyze button -- the single primary action on this screen
        self.btn_analyze = ws.primary_button(
            self, "Analyze →", self.controller._start_upload_analysis)
        self.btn_analyze.config(font=("Segoe UI", 11, "bold"), width=16, height=2)
        self.btn_analyze.grid(row=6, column=0, columnspan=2, pady=20)

        # Status bar
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w",
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).grid(
            row=7, column=0, columnspan=2, sticky="ew", padx=10, pady=(4, 10))
```

- [ ] **Step 3: Restyle `PostProcessingPanel`**

Replace lines 1077-1148 (header through status bar):

```python
# OLD
        # row 0 — header: mode-select back button + trial filename
        hdr0 = tk.Frame(self)
        hdr0.grid(row=0, column=0, columnspan=3, sticky="ew",
                  padx=12, pady=(12, 4))
        tk.Button(hdr0, text="<- Mode Select",
                  font=("Segoe UI", 9),
                  command=self.controller.on_back_to_mode_select).pack(
            side="left", padx=(0, 12))
        self.title_var = tk.StringVar(value="")
        tk.Label(hdr0, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")

        # row 1 — matplotlib figure
        if _MPL_AVAIL:
            self._fig    = Figure(figsize=(10, 4), dpi=96, facecolor="#EEF2F7")
            self._ax     = self._fig.add_subplot(111)
            self._canvas = FigureCanvasTkAgg(self._fig, master=self)
            self._canvas.get_tk_widget().grid(
                row=1, column=0, columnspan=3, sticky="nsew", padx=8, pady=4)
        else:
            tk.Label(self, text="matplotlib not available — install it in .venv",
                     fg="red").grid(row=1, column=0, columnspan=3)
            self._canvas = None

        # row 2 — PT Metrics LabelFrame
        self._metrics_frame = tk.LabelFrame(self, text="Popović PT Metrics",
                                            font=("Segoe UI", 9, "bold"), padx=8, pady=4)
        self._metrics_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=4)

        self.a1_var    = tk.StringVar(value="—")
        self.omega_var = tk.StringVar(value="—")
        self.n_var     = tk.StringVar(value="—")
        self.f_var     = tk.StringVar(value="—")
        self.r2n_var   = tk.StringVar(value="—")
        self.mas_var   = tk.StringVar(value="—")
        self.score_var = tk.StringVar(value="—")

        for col, (lbl, var) in enumerate([
            ("A1 (deg)",  self.a1_var),
            ("w (deg/s)", self.omega_var),
            ("N",         self.n_var),
            ("f (Hz)",    self.f_var),
            ("R2N",       self.r2n_var),
            ("MAS",       self.mas_var),
            ("Score",     self.score_var),
        ]):
            tk.Label(self._metrics_frame, text=lbl, font=("Segoe UI", 8), fg="#555").grid(
                row=0, column=col, padx=10, pady=1)
            tk.Label(self._metrics_frame, textvariable=var,
                     font=("Segoe UI", 11, "bold")).grid(
                row=1, column=col, padx=10)

        # row 3 — action buttons
        tk.Button(self, text="<- New Trial",
                  bg=_BLUE, fg="white", font=("Segoe UI", 11, "bold"),
                  width=14, height=2,
                  command=self._on_new_trial).grid(
            row=3, column=0, padx=10, pady=12, sticky="e")
        tk.Button(self, text="Load OptiTrack CSV",
                  font=("Segoe UI", 10), width=20, height=2,
                  command=self._on_load_optitrack).grid(
            row=3, column=1, padx=10, pady=12, sticky="w")
        self.btn_upload_video = tk.Button(
            self, text="🎥 Upload Video for HPE",
            font=("Segoe UI", 10), width=22, height=2,
            command=self._on_upload_video)
        self.btn_upload_video.grid(row=3, column=2, padx=10, pady=12, sticky="w")

        # row 4 — status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w", fg="#333").grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 8))
```

```python
# NEW
        self.configure(bg=ws.PALETTE["BG"])

        # row 0 — header: mode-select back button + trial filename
        hdr0 = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr0.grid(row=0, column=0, columnspan=3, sticky="ew",
                  padx=12, pady=(12, 4))
        ws.secondary_button(hdr0, "← Mode Select",
                            self.controller.on_back_to_mode_select).pack(
            side="left", padx=(0, 12))
        self.title_var = tk.StringVar(value="")
        tk.Label(hdr0, textvariable=self.title_var,
                 font=("Segoe UI", 12, "bold"), anchor="w",
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        # row 1 — matplotlib figure
        if _MPL_AVAIL:
            self._fig    = Figure(figsize=(10, 4), dpi=96, facecolor=ws.PALETTE["BG"])
            self._ax     = self._fig.add_subplot(111)
            self._canvas = FigureCanvasTkAgg(self._fig, master=self)
            self._canvas.get_tk_widget().grid(
                row=1, column=0, columnspan=3, sticky="nsew", padx=8, pady=4)
        else:
            tk.Label(self, text="matplotlib not available — install it in .venv",
                     bg=ws.PALETTE["BG"], fg="red").grid(row=1, column=0, columnspan=3)
            self._canvas = None

        # row 2 — PT Metrics card
        self._metrics_frame = tk.LabelFrame(
            self, text="Popović PT Metrics", font=("Segoe UI", 9, "bold"),
            padx=8, pady=4, bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG3"],
            highlightbackground=ws.PALETTE["BORDER"], highlightthickness=1)
        self._metrics_frame.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=4)

        self.a1_var    = tk.StringVar(value="—")
        self.omega_var = tk.StringVar(value="—")
        self.n_var     = tk.StringVar(value="—")
        self.f_var     = tk.StringVar(value="—")
        self.r2n_var   = tk.StringVar(value="—")
        self.mas_var   = tk.StringVar(value="—")
        self.score_var = tk.StringVar(value="—")

        for col, (lbl, var) in enumerate([
            ("A1 (deg)",  self.a1_var),
            ("w (deg/s)", self.omega_var),
            ("N",         self.n_var),
            ("f (Hz)",    self.f_var),
            ("R2N",       self.r2n_var),
            ("MAS",       self.mas_var),
            ("Score",     self.score_var),
        ]):
            tk.Label(self._metrics_frame, text=lbl, font=("Segoe UI", 8),
                     bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG2"]).grid(
                row=0, column=col, padx=10, pady=1)
            tk.Label(self._metrics_frame, textvariable=var,
                     font=("Segoe UI", 11, "bold"),
                     bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"]).grid(
                row=1, column=col, padx=10)

        # row 3 — action buttons (utility actions, no single primary action
        # on a review-only screen -- all secondary-styled)
        ws.secondary_button(self, "← New Trial", self._on_new_trial).grid(
            row=3, column=0, padx=10, pady=12, sticky="e")
        ws.secondary_button(self, "Load OptiTrack CSV", self._on_load_optitrack).grid(
            row=3, column=1, padx=10, pady=12, sticky="w")
        self.btn_upload_video = ws.secondary_button(
            self, "\U0001f3a5 Upload Video for HPE", self._on_upload_video)
        self.btn_upload_video.grid(row=3, column=2, padx=10, pady=12, sticky="w")

        # row 4 — status bar
        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var,
                 relief="sunken", anchor="w",
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 8))
```

- [ ] **Step 4: Run the full test suite**

```
.venv\Scripts\pytest tests\ -v
```

Expected: same pass/fail counts as the baseline established in Task 1 Step 7 — no new failures introduced by this restyle pass.

- [ ] **Step 5: Manual smoke test**

Run: `.venv\Scripts\python.exe pendulastic_app.py`

Verify by hand:
1. Mode Select renders on the light-gray palette; "Live Recording Session" is the filled blue primary button, the other two are lighter secondary buttons.
2. Acquisition screen: IMU and RGB checkboxes are pre-checked; OptiTrack/Video File are hidden behind "▸ Research sources"; clicking it expands to show both, clicking again collapses (checked state is retained across the toggle).
3. Start a countdown — the START/CANCEL/STOP buttons still show their green/amber/red state colors unchanged.
4. Upload & Analyze and the post-processing review screen both render on the same light-gray/white-card/blue-accent look, with no clipped or unreadable (e.g. white-on-white) text.
5. Navigate into the Workbench panel (Multi-Modal Comparison) — confirm it still renders correctly now that `PALETTE["BG"]` changed (it inherits the same light-gray automatically).

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py
git commit -m "style: restyle ModeSelectView, UploadMetaView, PostProcessingPanel onto the shared palette"
```

---

## Plan Self-Review Notes

- **Spec coverage:** Design spec Section 2 (architecture/palette) → Task 1. Section 3 (acquisition defaults/disclosure) → Tasks 2-3. Sections 2-3's "restyle every panel" → Tasks 4-5. Section 4 (testing) → each task's Steps 1-2/verify-pass steps plus Task 5 Step 4's full-suite check. Section 5 (out of scope) — no task touches `pendulastic_workbench.py`'s structure or `pendulastic_viewer.py`.
- **Type/name consistency checked:** `ws` (Task 1) is the name every later task's code uses — no task refers to the old `_wb_style` name. `_research_toggle_btn`/`_research_sources_frame`/`_research_sources_expanded`/`_on_toggle_research_sources` (Task 3) are used consistently in Task 3's own tests and are the only names Task 4 assumes exist (it doesn't touch them further). `ws.PALETTE`/`ws.primary_button`/`ws.secondary_button`/`ws.card_frame` signatures match `workbench_style.py`'s existing definitions verbatim — no new functions invented.
- **Placeholder scan:** no TBDs; every step shows full replacement code, not a description of it.
