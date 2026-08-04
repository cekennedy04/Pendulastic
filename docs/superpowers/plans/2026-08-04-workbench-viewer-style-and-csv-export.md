# Workbench Viewer-Style Restyle & CSV Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `pendulastic_viewer.py`'s `_HistoryWindow` dark-dashboard aesthetic into `pendulastic_workbench.py`'s `TrialLoadPanel`/`WorkbenchView`, and add a universal CSV export architecture (traces, per-trace PT params, vs-reference comparison metrics, annotations) alongside the existing JSON session export.

**Architecture:** A new dependency-light `workbench_style.py` module (palette + `ttk.Style` theme + widget builders, copied — not imported — from `pendulastic_viewer.py`'s `_C` dict) restyles the two Workbench panels. Four new pure row-builder functions in `workbench_engine.py` (mirroring the existing `export_session()` pattern) format CSV rows; `pendulastic_workbench.py`'s UI layer wires them to a new "Export CSV ▾" menu, following `_HistoryWindow._cmd_export_csv`'s save-dialog pattern.

**Tech Stack:** Python, Tkinter/`ttk`, matplotlib (`FigureCanvasTkAgg`), `csv` (stdlib), `pytest` with a headless `tk.Tk()` + `.withdraw()` root (existing convention in `tests/test_pendulastic_workbench.py`).

## Global Constraints

- **Zero logic regression**: no changes to `compute_pt_params`, any `workbench_engine` math function (`compare_pair`, `windowed_pt_params`, `extrema_jitter`, `_active_window_end`), or any storage schema. New `workbench_engine` functions are pure additive row-formatters.
- **Scope**: only `TrialLoadPanel` and `WorkbenchView` in `pendulastic_workbench.py`. `pendulastic_app.py`'s other panels and `pendulastic_viewer.py` itself are not modified.
- **CSV is additive**: the existing JSON `export_session`/"Export Session (JSON)..." path is unchanged; CSV export is new, separate buttons.
- **CSV rows carry `participant_id`/`session_date` as explicit columns on every row** (not a comment preamble) — must parse cleanly in `pandas.read_csv` with no special handling.
- **Preserve existing test-facing attribute names exactly**: `self._back_button`, `self._load_another_button`, `self._browse_buttons["imu"/"video"/"optitrack"]`, `self._reference_var`, `self._visible_vars`, `self._lag_override_vars`, `self._trace_lines`, `self._ax`, `self._axvline`, `self._annotations`, `self._annotation_artists`, `self._scrub_var`. All tests in `tests/test_pendulastic_workbench.py` must keep passing unmodified.
- **Build in a new worktree off `main`** (e.g. `worktree-workbench-viewer-style`) — the `workbench-pt-score-panel` worktree is concurrently editing `pendulastic_workbench.py`; rebase/merge order is a landing-time decision, out of scope for this plan.
- Palette values (exact): `BG #0B1928`, `SURFACE #112040`, `PANEL #0D2238`, `BTN #1A3A5C`, `BTN_ACT #2A6090`, `FG #C8E0F5`, `FG2 #5A8AB0`, `FG3 #2E5070`, `BORDER #1C3A5E`, font `Segoe UI`.
- **Known pre-existing failure, unrelated to this plan**: `test_standalone_app_back_to_mode_select_is_a_genuine_noop` fails on unmodified `main` in this dev environment with `_tkinter.TclError: Can't find a usable init.tcl` (triggered by constructing a second independent `tk.Tk()` root — `App(tk.Tk)` — while `_get_root()`'s module-level shared root is still alive elsewhere in the suite). Confirmed by running the full suite before starting this plan: `1 failed, 41 passed`. Every "run full test suite" step below should show that same single pre-existing failure; a *different* failure on that test, or any failure on any other test, is a real regression.

---

### Task 1: `workbench_style.py` — palette, ttk theme, widget builders

**Files:**
- Create: `workbench_style.py`

**Interfaces:**
- Produces: `PALETTE: dict`, `FONT_TITLE`, `FONT_SECTION`, `FONT_BODY`, `FONT_SMALL` (tuples), `apply_ttk_theme(root: tk.Misc) -> None`, `card_frame(parent: tk.Misc, title: str = "") -> tk.Frame`, `primary_button(parent, text: str, command) -> tk.Button`, `secondary_button(parent, text: str, command) -> tk.Button`.

- [ ] **Step 1: Create `workbench_style.py`**

```python
"""
workbench_style.py
===================
Dark palette + ttk theme + small widget builders for the Pendulastic
Workbench UI (TrialLoadPanel, WorkbenchView). Palette values are copied
from pendulastic_viewer.py's _C dict (not imported -- pendulastic_workbench.py
must not pull in pendulastic_viewer.py's cv2/mediapipe/ultralytics
dependency chain just for six color strings; pendulastic_viewer.py itself
is not modified by this module).

See docs/superpowers/specs/2026-08-04-workbench-viewer-style-and-csv-export-design.md.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PALETTE = {
    "BG":      "#0B1928",
    "SURFACE": "#112040",
    "PANEL":   "#0D2238",
    "BTN":     "#1A3A5C",
    "BTN_ACT": "#2A6090",
    "FG":      "#C8E0F5",
    "FG2":     "#5A8AB0",
    "FG3":     "#2E5070",
    "BORDER":  "#1C3A5E",
    "MONO":    "Consolas",
}

FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_SECTION = ("Segoe UI", 8, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 7)


def apply_ttk_theme(root: tk.Misc) -> None:
    """Configure a clam-based ttk.Style so Scale/OptionMenu/PanedWindow/
    Scrollbar/Treeview widgets pick up the dark palette. Plain ttk widgets
    ignore bg=/fg= entirely -- they need explicit style.configure(...),
    the same mechanism pendulastic_viewer.py's _HistoryWindow already uses
    for its "Dash.Treeview" style."""
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure("TPanedwindow", background=PALETTE["BG"])
    style.configure("TScrollbar", background=PALETTE["PANEL"],
                    troughcolor=PALETTE["BG"], bordercolor=PALETTE["BORDER"],
                    arrowcolor=PALETTE["FG2"])
    style.configure("Horizontal.TScale", background=PALETTE["BG"],
                    troughcolor=PALETTE["SURFACE"])
    style.configure("TMenubutton", background=PALETTE["BTN"],
                    foreground=PALETTE["FG"], font=FONT_BODY)

    style.configure("Workbench.Treeview", background=PALETTE["SURFACE"],
                    foreground=PALETTE["FG"], fieldbackground=PALETTE["SURFACE"],
                    rowheight=22, font=FONT_BODY)
    style.configure("Workbench.Treeview.Heading", background=PALETTE["PANEL"],
                    foreground=PALETTE["FG2"], font=FONT_SECTION)
    style.map("Workbench.Treeview", background=[("selected", PALETTE["BTN"])],
              foreground=[("selected", PALETTE["FG"])])


def card_frame(parent: tk.Misc, title: str = "") -> tk.Frame:
    """A padded, panel-colored card frame with an optional bold section
    label packed at its top. Caller packs/grids the returned frame into
    its own parent, then packs content into it directly."""
    card = tk.Frame(parent, bg=PALETTE["PANEL"], padx=10, pady=8,
                    highlightbackground=PALETTE["BORDER"], highlightthickness=1)
    if title:
        tk.Label(card, text=title, bg=PALETTE["PANEL"], fg=PALETTE["FG3"],
                 font=FONT_SECTION).pack(anchor="w", pady=(0, 6))
    return card


def primary_button(parent: tk.Misc, text: str, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command,
                     bg=PALETTE["BTN_ACT"], fg="#FFFFFF",
                     activebackground="#1A5080", activeforeground="#FFFFFF",
                     relief="flat", bd=0, padx=10, pady=4,
                     font=FONT_BODY, cursor="hand2")


def secondary_button(parent: tk.Misc, text: str, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command,
                     bg=PALETTE["BTN"], fg=PALETTE["FG"],
                     activebackground=PALETTE["BTN_ACT"], activeforeground="#FFFFFF",
                     relief="flat", bd=0, padx=10, pady=4,
                     font=FONT_BODY, cursor="hand2")
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python -c "import workbench_style; print(workbench_style.PALETTE['BG'])"`
Expected: prints `#0B1928` with no traceback.

- [ ] **Step 3: Commit**

```bash
git add workbench_style.py
git commit -m "feat: add workbench_style module (dark palette + ttk theme + widget builders)"
```

---

### Task 2: CSV row-builder functions in `workbench_engine.py`

**Files:**
- Modify: `workbench_engine.py` (append after `export_session`, ~line 490)
- Test: `tests/test_workbench_engine.py` (append after `test_export_session_round_trips_through_json`, ~line 478)

**Interfaces:**
- Consumes: nothing new — operates on the same `traces`/`per_trace`/`vs_reference`/`annotations` shapes `WorkbenchView.get_metrics_snapshot()`/`get_annotations()` already produce (`windowed_pt_params` keys: `R2n, N, phi_max_ratio, omega_max_n, f, area_ratio, omega_min_n`; `compare_pair` keys on success: `status, rmse_deg, mae_deg, bias_deg, loa_lower_deg, loa_upper_deg, lag_sec, n_samples`, plus `timing_offset_sec` added by `WorkbenchView.get_metrics_snapshot`; on failure: `status, error`).
- Produces: `traces_to_csv_rows(traces, participant_id, session_date) -> (fieldnames: list, rows: list[dict])`, `per_trace_metrics_to_csv_rows(per_trace, participant_id, session_date) -> (...)`, `vs_reference_metrics_to_csv_rows(reference, vs_reference, participant_id, session_date) -> (...)`, `annotations_to_csv_rows(annotations, participant_id, session_date) -> (...)` — all consumed by `pendulastic_workbench.py` in Task 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_workbench_engine.py`:

```python
def test_traces_to_csv_rows_empty_traces_returns_no_rows():
    fieldnames, rows = engine.traces_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []
    assert fieldnames == ["participant_id", "session_date", "label", "t_sec", "angle_deg"]


def test_traces_to_csv_rows_one_row_per_sample_per_trace():
    traces = {
        "imu": (np.array([0.0, 0.1, 0.2]), np.array([180.0, 170.0, 160.0])),
        "optitrack": (np.array([0.0, 0.1]), np.array([181.0, 171.0])),
    }
    fieldnames, rows = engine.traces_to_csv_rows(traces, "P5", "2026-08-04")
    assert len(rows) == 5
    imu_rows = [r for r in rows if r["label"] == "imu"]
    assert len(imu_rows) == 3
    assert imu_rows[0] == {
        "participant_id": "P5", "session_date": "2026-08-04",
        "label": "imu", "t_sec": 0.0, "angle_deg": 180.0,
    }


def test_per_trace_metrics_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.per_trace_metrics_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []


def test_per_trace_metrics_to_csv_rows_one_row_per_label():
    per_trace = {
        "imu": {"R2n": 1.1, "N": 2.0, "phi_max_ratio": 0.5, "omega_max_n": 3.0,
                "f": 1.2, "area_ratio": 0.07, "omega_min_n": 0.4},
    }
    fieldnames, rows = engine.per_trace_metrics_to_csv_rows(per_trace, "P5", "2026-08-04")
    assert rows == [{
        "participant_id": "P5", "session_date": "2026-08-04", "label": "imu",
        "area_ratio": 0.07, "N": 2.0, "f_hz": 1.2, "R2n": 1.1,
        "omega_max_n": 3.0, "omega_min_n": 0.4,
    }]


def test_vs_reference_metrics_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.vs_reference_metrics_to_csv_rows("optitrack", {}, "P5", "2026-08-04")
    assert rows == []


def test_vs_reference_metrics_to_csv_rows_ok_and_error_status():
    vs_reference = {
        "imu": {"status": "ok", "rmse_deg": 5.2, "mae_deg": 3.1, "lag_sec": 0.05,
                "timing_offset_sec": 0.12},
        "mediapipe": {"status": "error",
                      "error": "Need at least 4 finite samples in both signals."},
    }
    fieldnames, rows = engine.vs_reference_metrics_to_csv_rows(
        "optitrack", vs_reference, "P5", "2026-08-04")
    assert len(rows) == 2
    ok_row = next(r for r in rows if r["label"] == "imu")
    assert ok_row["reference"] == "optitrack"
    assert ok_row["rmse_deg"] == 5.2
    assert ok_row["error"] is None
    err_row = next(r for r in rows if r["label"] == "mediapipe")
    assert err_row["status"] == "error"
    assert err_row["rmse_deg"] is None
    assert err_row["error"] == "Need at least 4 finite samples in both signals."


def test_annotations_to_csv_rows_empty_returns_no_rows():
    fieldnames, rows = engine.annotations_to_csv_rows({}, "P5", "2026-08-04")
    assert rows == []


def test_annotations_to_csv_rows_one_row_per_milestone():
    annotations = {"Release Start": (42, 0.7), "Maximum Flexion": (88, 1.47)}
    fieldnames, rows = engine.annotations_to_csv_rows(annotations, "P5", "2026-08-04")
    assert len(rows) == 2
    row = next(r for r in rows if r["label"] == "Release Start")
    assert row == {
        "participant_id": "P5", "session_date": "2026-08-04",
        "label": "Release Start", "frame_index": 42, "t_sec": 0.7,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_workbench_engine.py -k "csv_rows" -v`
Expected: FAIL with `AttributeError: module 'workbench_engine' has no attribute 'traces_to_csv_rows'` (and similarly for the other three).

- [ ] **Step 3: Implement the row-builders**

Append to `workbench_engine.py`, after `export_session`:

```python
def traces_to_csv_rows(traces: dict, participant_id: str, session_date: str) -> tuple:
    """One row per sample per trace: participant_id, session_date, label,
    t_sec, angle_deg. Pure formatter -- which traces are "in scope" (e.g.
    only currently-visible ones) is decided by the caller, which passes
    only the traces dict it wants exported."""
    fieldnames = ["participant_id", "session_date", "label", "t_sec", "angle_deg"]
    rows = []
    for label, (t, angle) in traces.items():
        for ti, ai in zip(t, angle):
            rows.append({
                "participant_id": participant_id,
                "session_date": session_date,
                "label": label,
                "t_sec": float(ti),
                "angle_deg": float(ai),
            })
    return fieldnames, rows


def per_trace_metrics_to_csv_rows(per_trace: dict, participant_id: str,
                                  session_date: str) -> tuple:
    """One row per label from a get_metrics_snapshot()["per_trace"] dict
    (windowed_pt_params output, design spec Section 4a)."""
    fieldnames = ["participant_id", "session_date", "label", "area_ratio",
                 "N", "f_hz", "R2n", "omega_max_n", "omega_min_n"]
    rows = []
    for label, pt in per_trace.items():
        rows.append({
            "participant_id": participant_id,
            "session_date": session_date,
            "label": label,
            "area_ratio": pt["area_ratio"],
            "N": pt["N"],
            "f_hz": pt["f"],
            "R2n": pt["R2n"],
            "omega_max_n": pt["omega_max_n"],
            "omega_min_n": pt["omega_min_n"],
        })
    return fieldnames, rows


def vs_reference_metrics_to_csv_rows(reference: str, vs_reference: dict,
                                     participant_id: str, session_date: str) -> tuple:
    """One row per label from a get_metrics_snapshot()["vs_reference"] dict
    (compare_pair + timing_offset_sec output, design spec Section 4). A
    status="error" result still produces a row -- metric fields blank,
    `error` populated -- rather than being silently dropped, so a CSV
    exported mid-session shows exactly which comparisons failed and why."""
    fieldnames = ["participant_id", "session_date", "label", "reference",
                 "status", "rmse_deg", "mae_deg", "lag_sec",
                 "timing_offset_sec", "error"]
    rows = []
    for label, result in vs_reference.items():
        rows.append({
            "participant_id": participant_id,
            "session_date": session_date,
            "label": label,
            "reference": reference,
            "status": result.get("status"),
            "rmse_deg": result.get("rmse_deg"),
            "mae_deg": result.get("mae_deg"),
            "lag_sec": result.get("lag_sec"),
            "timing_offset_sec": result.get("timing_offset_sec"),
            "error": result.get("error"),
        })
    return fieldnames, rows


def annotations_to_csv_rows(annotations: dict, participant_id: str,
                            session_date: str) -> tuple:
    """One row per milestone from WorkbenchView.get_annotations()."""
    fieldnames = ["participant_id", "session_date", "label", "frame_index", "t_sec"]
    rows = []
    for label, (frame_index, t_sec) in annotations.items():
        rows.append({
            "participant_id": participant_id,
            "session_date": session_date,
            "label": label,
            "frame_index": int(frame_index),
            "t_sec": float(t_sec),
        })
    return fieldnames, rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_workbench_engine.py -k "csv_rows" -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Run the full engine test suite to check for regressions**

Run: `pytest tests/test_workbench_engine.py -v`
Expected: all tests pass (no change to any existing function).

- [ ] **Step 6: Commit**

```bash
git add workbench_engine.py tests/test_workbench_engine.py
git commit -m "feat: add CSV row-builder functions to workbench_engine"
```

---

### Task 3: `TrialLoadPanel` restyle + Participant/Session fields

**Files:**
- Modify: `pendulastic_workbench.py:1-32` (imports), `pendulastic_workbench.py:46-149` (`TrialLoadPanel`), `pendulastic_workbench.py:539-553` (`App.on_load_trial`'s `trial_meta` construction)
- Test: `tests/test_pendulastic_workbench.py` (append near the existing `TrialLoadPanel` tests)

**Interfaces:**
- Consumes: `workbench_style.PALETTE`, `FONT_TITLE`, `FONT_BODY`, `card_frame`, `primary_button`, `secondary_button` (Task 1).
- Produces: `TrialLoadPanel._participant_id: tk.StringVar`, `TrialLoadPanel._session_date: tk.StringVar` (defaults to today, `YYYY-MM-DD`); `get_selection()` gains `"participant_id"` and `"session_date"` string keys; `App._trial_meta` (read via `App.get_trial_meta()`, consumed by Task 5's export handlers) gains the same two keys.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pendulastic_workbench.py -k "participant_and_session or session_date_defaults" -v`
Expected: FAIL with `AttributeError: 'TrialLoadPanel' object has no attribute '_participant_id'`.

- [ ] **Step 3: Add imports**

In `pendulastic_workbench.py`, add to the top imports block (after the existing `import os`):

```python
import datetime
```

And after `import workbench_engine as engine`:

```python
import workbench_style as ws
```

- [ ] **Step 4: Rewrite `TrialLoadPanel.__init__` and `_build_widgets`, and `_file_row`**

Replace `TrialLoadPanel.__init__` (lines 53-64) with:

```python
    def __init__(self, parent, controller) -> None:
        super().__init__(parent, bg=ws.PALETTE["BG"])
        self.controller = controller
        self._imu_path = tk.StringVar(value="")
        self._video_path = tk.StringVar(value="")
        self._optitrack_path = tk.StringVar(value="")
        self._participant_id = tk.StringVar(value="")
        self._session_date = tk.StringVar(
            value=datetime.datetime.now().strftime("%Y-%m-%d"))
        self._femur_cm = tk.StringVar(value="")
        self._tibia_cm = tk.StringVar(value="")
        self._model_vars = {name: tk.BooleanVar(value=False)
                            for name in analysis_pipeline.MODEL_FUNCTIONS}
        self._browse_buttons: dict = {}
        self._build_widgets()
```

Replace `_build_widgets` (lines 66-103) with:

```python
    def _build_widgets(self) -> None:
        header = tk.Frame(self, bg=ws.PALETTE["BG"])
        header.pack(fill="x", padx=12, pady=(10, 4))
        self._back_button = ws.secondary_button(
            header, "← Back to Main Menu",
            lambda: self.controller.on_back_to_mode_select())
        self._back_button.pack(side="left")
        tk.Label(header, text="Pendulastic Workbench", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_TITLE).pack(side="left", padx=(16, 0))

        files_card = ws.card_frame(self, "TRIAL FILES")
        files_card.pack(fill="x", padx=12, pady=6)
        self._file_row(files_card, "Phone IMU raw log (.jsonl or split CSV)",
                       self._imu_path,
                       [("IMU log", "*.jsonl *.csv"), ("All files", "*.*")], name="imu")
        self._file_row(files_card, "Video (.mp4/.avi)", self._video_path,
                       [("Video", "*.mp4 *.avi"), ("All files", "*.*")], name="video")
        self._file_row(files_card, "OptiTrack CSV", self._optitrack_path,
                       [("CSV", "*.csv"), ("All files", "*.*")], name="optitrack")

        session_card = ws.card_frame(self, "PARTICIPANT & SESSION")
        session_card.pack(fill="x", padx=12, pady=6)
        srow = tk.Frame(session_card, bg=ws.PALETTE["PANEL"])
        srow.pack(fill="x")
        tk.Label(srow, text="Participant ID:", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(srow, textvariable=self._participant_id, width=18,
                 font=ws.FONT_BODY).grid(row=0, column=1, sticky="w", padx=(0, 20), pady=4)
        tk.Label(srow, text="Session Date:", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(srow, textvariable=self._session_date, width=12,
                 font=ws.FONT_BODY).grid(row=0, column=3, sticky="w", pady=4)

        models_card = ws.card_frame(self, "HPE MODELS TO RUN")
        models_card.pack(fill="x", padx=12, pady=6)
        model_frame = tk.Frame(models_card, bg=ws.PALETTE["PANEL"])
        model_frame.pack(fill="x")
        for i, name in enumerate(analysis_pipeline.MODEL_FUNCTIONS):
            tk.Checkbutton(model_frame, text=name, variable=self._model_vars[name],
                          bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"], font=ws.FONT_BODY,
                          selectcolor=ws.PALETTE["SURFACE"],
                          activebackground=ws.PALETTE["PANEL"],
                          activeforeground=ws.PALETTE["FG"]
                         ).grid(row=i // 3, column=i % 3, sticky="w", padx=4, pady=2)

        pers_card = ws.card_frame(self, "PERSONALIZATION (OPTIONAL)")
        pers_card.pack(fill="x", padx=12, pady=6)
        prow = tk.Frame(pers_card, bg=ws.PALETTE["PANEL"])
        prow.pack(fill="x")
        tk.Label(prow, text="Femur length (cm):", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(prow, textvariable=self._femur_cm, width=10,
                 font=ws.FONT_BODY).grid(row=0, column=1, sticky="w", padx=(0, 20), pady=4)
        tk.Label(prow, text="Tibia length (cm):", bg=ws.PALETTE["PANEL"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).grid(
            row=0, column=2, sticky="w", padx=(0, 6), pady=4)
        tk.Entry(prow, textvariable=self._tibia_cm, width=10,
                 font=ws.FONT_BODY).grid(row=0, column=3, sticky="w", pady=4)

        ws.primary_button(self, "Load Trial", self._on_load_clicked).pack(pady=16)
```

Replace `_file_row` (lines 105-113) with:

```python
    def _file_row(self, parent, label: str, var: tk.StringVar, filetypes,
                  name: str) -> None:
        row = tk.Frame(parent, bg=ws.PALETTE["PANEL"])
        row.pack(fill="x", pady=3)
        tk.Label(row, text=label, bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG"],
                 font=ws.FONT_BODY, width=32, anchor="w").pack(side="left")
        tk.Entry(row, textvariable=var, width=40, state="readonly",
                 font=ws.FONT_BODY).pack(side="left", padx=4, fill="x", expand=True)
        btn = ws.secondary_button(row, "Browse...", lambda: self._browse(var, filetypes))
        btn.pack(side="left", padx=4)
        self._browse_buttons[name] = btn
```

- [ ] **Step 5: Add `participant_id`/`session_date` to `get_selection()`**

In `get_selection` (lines 120-140), add two keys to the returned dict:

```python
        return {
            "imu_path": self._imu_path.get() or None,
            "video_path": self._video_path.get() or None,
            "optitrack_path": self._optitrack_path.get() or None,
            "participant_id": self._participant_id.get().strip(),
            "session_date": self._session_date.get().strip(),
            "models": [name for name, var in self._model_vars.items() if var.get()],
            "femur_length_cm": _parse_float(self._femur_cm.get()),
            "tibia_length_cm": _parse_float(self._tibia_cm.get()),
        }
```

- [ ] **Step 6: Thread `participant_id`/`session_date` into `App.on_load_trial`'s `trial_meta`**

In `App.on_load_trial` (lines 546-553), add the two keys:

```python
        self._trial_meta = {
            "imu_path": selection["imu_path"],
            "video_path": selection["video_path"],
            "optitrack_path": selection["optitrack_path"],
            "participant_id": selection["participant_id"],
            "session_date": selection["session_date"],
            "models": selection["models"],
            "femur_length_cm": selection["femur_length_cm"],
            "tibia_length_cm": selection["tibia_length_cm"],
        }
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_pendulastic_workbench.py -v`
Expected: all tests pass, including `test_trial_load_panel_back_button_calls_controller`, `test_imu_browse_button_accepts_csv_and_jsonl`, and the two new participant/session tests (existing `_back_button`/`_browse_buttons["imu"]` attribute names are preserved by the rewrite above).

- [ ] **Step 8: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: restyle TrialLoadPanel with dark card layout, add participant/session fields"
```

---

### Task 4: `WorkbenchView` restyle + metrics Treeview tables

**Files:**
- Modify: `pendulastic_workbench.py:152-311` (`WorkbenchView.__init__`/`_build_widgets`/`set_traces`/`_on_visibility_changed`), replacing the `tk.Text` metrics readout (former lines 225-226, 371-401) with two `ttk.Treeview` tables.
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Consumes: `workbench_style` (Task 1); `WorkbenchView.get_metrics_snapshot()` (unchanged, pre-existing).
- Produces: `WorkbenchView._per_trace_tree: ttk.Treeview`, `WorkbenchView._vs_ref_tree: ttk.Treeview`, `WorkbenchView._style_axes() -> None`. `_recompute_metrics()` now populates these two trees instead of `self._metrics_text` (which is removed). Task 5 appends one line to the end of `_recompute_metrics()` and to `set_traces()`/`_on_mark_milestone()` — do not treat this task's version of those methods as final call-site content, only as the metrics-table wiring.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
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
    assert rows == [("No data yet", "", "", "", "", "", "")]


def test_workbench_view_per_trace_tree_column_widths_are_fixed():
    from pendulastic_workbench import WorkbenchView
    r = _get_root()
    wv = WorkbenchView(r, _Ctrl())
    r.update()
    for col in wv._PER_TRACE_COLS[1:]:
        assert wv._per_trace_tree.column(col)["stretch"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pendulastic_workbench.py -k "per_trace_tree or vs_ref_tree" -v`
Expected: FAIL with `AttributeError: 'WorkbenchView' object has no attribute '_per_trace_tree'`.

- [ ] **Step 3: Replace `WorkbenchView.__init__` and `_build_widgets`**

Replace `__init__` (lines 158-173) with:

```python
    def __init__(self, parent, controller) -> None:
        super().__init__(parent, bg=ws.PALETTE["BG"])
        self.controller = controller
        self._cap: Optional[cv2.VideoCapture] = None
        self._fps: float = 30.0
        self._n_frames: int = 0
        self._photo = None   # keep a reference so Tk doesn't garbage-collect it
        self._scrub_var = tk.DoubleVar(value=0.0)
        self._traces: dict = {}          # {label: (t, angle)}
        self._trace_lines: dict = {}     # {label: matplotlib Line2D}
        self._visible_vars: dict = {}    # {label: tk.BooleanVar}
        self._lag_override_vars: dict = {}   # {label: tk.StringVar}, blank = auto
        self._reference_var = tk.StringVar(value="")
        self._annotations: dict = {}     # {label: (frame_index, t_sec)}
        self._pending_milestone = tk.StringVar(value=MILESTONE_LABELS[0])
        self._build_widgets()
```

Replace `_build_widgets` (lines 175-226) with:

```python
    _PER_TRACE_COLS = ("label", "area_ratio", "N", "f_hz", "R2n",
                       "omega_max_n", "omega_min_n")
    _PER_TRACE_HDRS = ("Trace", "Area Ratio", "N", "f (Hz)", "R2n",
                       "ωmax_n", "ωmin_n")
    _PER_TRACE_W    = (110, 90, 60, 70, 70, 80, 80)

    _VS_REF_COLS = ("label", "reference", "rmse_deg", "mae_deg", "lag_sec",
                    "timing_offset_sec", "status")
    _VS_REF_HDRS = ("Trace", "Reference", "RMSE (deg)", "MAE (deg)",
                    "Lag (s)", "Timing Offset (s)", "Status")
    _VS_REF_W    = (100, 100, 90, 90, 70, 130, 110)

    def _build_widgets(self) -> None:
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = tk.Frame(paned, bg=ws.PALETTE["BG"])
        paned.add(left, weight=1)

        self._video_label = tk.Label(left, bg="black",
                                     highlightbackground=ws.PALETTE["BORDER"],
                                     highlightthickness=1)
        self._video_label.pack(fill="both", expand=True, padx=8, pady=8)

        self._scrubber = ttk.Scale(left, from_=0, to=0, orient="horizontal",
                                   variable=self._scrub_var, command=self._on_scrub)
        self._scrubber.pack(fill="x", padx=8, pady=4)

        self._right = tk.Frame(paned, bg=ws.PALETTE["BG"])
        paned.add(self._right, weight=1)

        top_controls = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        top_controls.pack(fill="x", padx=8, pady=4)
        tk.Label(top_controls, text="Reference:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).pack(side="left")
        self._reference_menu = ttk.OptionMenu(top_controls, self._reference_var, "")
        self._reference_menu.pack(side="left", padx=6)
        self._reference_var.trace_add("write", lambda *a: self._recompute_metrics())
        self._load_another_button = ws.secondary_button(
            top_controls, "← Load Different Trial",
            lambda: self.controller.on_workbench_load_another())
        self._load_another_button.pack(side="right", padx=6)

        annot_toolbar = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        annot_toolbar.pack(fill="x", padx=8, pady=4)
        tk.Label(annot_toolbar, text="Milestone:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"], font=ws.FONT_BODY).pack(side="left")
        ttk.OptionMenu(annot_toolbar, self._pending_milestone,
                      MILESTONE_LABELS[0], *MILESTONE_LABELS).pack(side="left", padx=6)
        ws.secondary_button(annot_toolbar, "Mark Here",
                            self._on_mark_milestone).pack(side="left", padx=6)
        ws.secondary_button(annot_toolbar, "Export Session (JSON)...",
                            self._on_export_clicked).pack(side="right", padx=6)

        self._visibility_frame = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        self._visibility_frame.pack(fill="x", padx=8, pady=4)

        self._fig = Figure(figsize=(6, 4), dpi=100)
        self._fig.patch.set_facecolor(ws.PALETTE["BG"])
        self._ax = self._fig.add_subplot(111)
        self._style_axes()
        self._plot_canvas = FigureCanvasTkAgg(self._fig, master=self._right)
        self._plot_canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=4)
        self._fig.canvas.mpl_connect("button_press_event", self._on_plot_click)

        tables_frame = tk.Frame(self._right, bg=ws.PALETTE["BG"])
        tables_frame.pack(fill="x", padx=8, pady=4)

        per_trace_card = ws.card_frame(tables_frame, "PER-TRACE METRICS")
        per_trace_card.pack(fill="x", pady=(0, 6))
        self._per_trace_tree = self._make_metrics_treeview(
            per_trace_card, self._PER_TRACE_COLS, self._PER_TRACE_HDRS, self._PER_TRACE_W)

        vs_ref_card = ws.card_frame(tables_frame, "VS-REFERENCE METRICS")
        vs_ref_card.pack(fill="x")
        self._vs_ref_tree = self._make_metrics_treeview(
            vs_ref_card, self._VS_REF_COLS, self._VS_REF_HDRS, self._VS_REF_W)

        self._recompute_metrics()

    def _style_axes(self) -> None:
        self._ax.set_facecolor(ws.PALETTE["SURFACE"])
        self._ax.set_xlabel("Time (s)", color=ws.PALETTE["FG2"])
        self._ax.set_ylabel("Knee Angle (deg)", color=ws.PALETTE["FG2"])
        self._ax.tick_params(colors=ws.PALETTE["FG2"])
        for spine in self._ax.spines.values():
            spine.set_color(ws.PALETTE["BORDER"])
        self._ax.grid(True, color=ws.PALETTE["BORDER"], linewidth=0.5, alpha=0.6)

    def _make_metrics_treeview(self, parent, cols, hdrs, widths) -> ttk.Treeview:
        wrap = tk.Frame(parent, bg=ws.PALETTE["PANEL"])
        wrap.pack(fill="x")
        tree = ttk.Treeview(wrap, style="Workbench.Treeview", columns=cols,
                            show="headings", height=4, selectmode="none")
        for key, hdr, w in zip(cols, hdrs, widths):
            tree.heading(key, text=hdr)
            tree.column(key, width=w, anchor="center", stretch=False)
        tree.column(cols[0], anchor="w", stretch=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="x", expand=True)
        sb.pack(side="right", fill="y")
        return tree
```

- [ ] **Step 4: Update `set_traces()` to restyle axes after `_ax.clear()`**

In `set_traces` (lines 228-293), replace the two lines immediately after `self._ax.clear()`:

```python
        self._ax.clear()
        self._ax.set_xlabel("Time (s)")
        self._ax.set_ylabel("Knee Angle (deg)")
```

with:

```python
        self._ax.clear()
        self._style_axes()
```

- [ ] **Step 5: Replace `_recompute_metrics` (removes the `tk.Text` version)**

Replace the old `_recompute_metrics` method (former lines 371-401) with:

```python
    def _recompute_metrics(self) -> None:
        """Populates both metrics Treeview tables from get_metrics_snapshot()
        -- the same method CSV export (Task 5) reads from, so displayed and
        exported values are always identical. Shows a single 'No data yet'
        placeholder row per table when its source dict is empty, rather
        than rendering a blank (ambiguous empty-vs-broken) table."""
        snapshot = self.get_metrics_snapshot()

        for tree in (self._per_trace_tree, self._vs_ref_tree):
            for item in tree.get_children():
                tree.delete(item)

        if not snapshot["per_trace"]:
            self._per_trace_tree.insert(
                "", "end", values=("No data yet", "", "", "", "", "", ""))
        for label, pt in snapshot["per_trace"].items():
            self._per_trace_tree.insert("", "end", values=(
                label, f"{pt['area_ratio']:.3f}", f"{pt['N']:.1f}",
                f"{pt['f']:.2f}", f"{pt['R2n']:.3f}",
                f"{pt['omega_max_n']:.3f}", f"{pt['omega_min_n']:.3f}"))

        if not snapshot["vs_reference"]:
            self._vs_ref_tree.insert(
                "", "end", values=("No data yet", "", "", "", "", "", ""))
        ref_label = snapshot["reference"]
        for label, result in snapshot["vs_reference"].items():
            if result["status"] == "ok":
                jitter_str = (f"{result['timing_offset_sec']:.3f}"
                             if result["timing_offset_sec"] is not None else "n/a")
                self._vs_ref_tree.insert("", "end", values=(
                    label, ref_label, f"{result['rmse_deg']:.2f}",
                    f"{result['mae_deg']:.2f}", f"{result['lag_sec']:.2f}",
                    jitter_str, "ok"))
            else:
                self._vs_ref_tree.insert("", "end", values=(
                    label, ref_label, "", "", "", "", result["error"]))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_pendulastic_workbench.py -v`
Expected: all tests pass, including all pre-existing `set_traces()` state-preservation tests (unaffected — they don't touch `_metrics_text`/the new trees) and the three new Treeview tests.

- [ ] **Step 7: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: restyle WorkbenchView chrome + replace metrics Text with styled Treeview tables"
```

---

### Task 5: "Export CSV ▾" menu wiring

**Files:**
- Modify: `pendulastic_workbench.py:1-32` (add `import csv`), `WorkbenchView._build_widgets` (annotation toolbar section, added in Task 4), `WorkbenchView.set_traces`, `WorkbenchView._on_mark_milestone`, `WorkbenchView._recompute_metrics` (append one line to each of the last three)
- Test: `tests/test_pendulastic_workbench.py` (append)

**Interfaces:**
- Consumes: `workbench_engine.traces_to_csv_rows`/`per_trace_metrics_to_csv_rows`/`vs_reference_metrics_to_csv_rows`/`annotations_to_csv_rows` (Task 2); `self.controller.get_trial_meta()` (pre-existing, now carries `participant_id`/`session_date` per Task 3).
- Produces: `WorkbenchView._export_csv_button: tk.Menubutton`, `WorkbenchView._export_csv_menu: tk.Menu` (entries indexed 0=Traces, 1=Per-Trace Metrics, 2=Comparison Metrics, 3=Annotations), `_update_export_csv_state() -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pendulastic_workbench.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pendulastic_workbench.py -k "export_csv" -v`
Expected: FAIL with `AttributeError: 'WorkbenchView' object has no attribute '_export_csv_menu'`.

- [ ] **Step 3: Add `import csv` to the top imports**

In `pendulastic_workbench.py`'s import block (after `import json`):

```python
import csv
```

- [ ] **Step 4: Add the Export CSV menu button to the annotation toolbar**

In `_build_widgets` (added in Task 4), immediately after the `"Export Session (JSON)..."` button line, add:

```python
        self._export_csv_button = tk.Menubutton(
            annot_toolbar, text="Export CSV ▾", bg=ws.PALETTE["BTN"],
            fg=ws.PALETTE["FG"], activebackground=ws.PALETTE["BTN_ACT"],
            activeforeground="#FFFFFF", relief="flat", bd=0, padx=10, pady=4,
            font=ws.FONT_BODY, cursor="hand2")
        self._export_csv_menu = tk.Menu(
            self._export_csv_button, tearoff=0, bg=ws.PALETTE["PANEL"],
            fg=ws.PALETTE["FG"], activebackground=ws.PALETTE["BTN_ACT"],
            activeforeground="#FFFFFF")
        self._export_csv_menu.add_command(label="Traces...",
                                          command=self._on_export_traces_csv)
        self._export_csv_menu.add_command(label="Per-Trace Metrics...",
                                          command=self._on_export_per_trace_csv)
        self._export_csv_menu.add_command(label="Comparison Metrics...",
                                          command=self._on_export_vs_reference_csv)
        self._export_csv_menu.add_command(label="Annotations...",
                                          command=self._on_export_annotations_csv)
        self._export_csv_button.configure(menu=self._export_csv_menu)
        self._export_csv_button.pack(side="right", padx=6)
```

- [ ] **Step 5: Add the export handlers and defensive-state method**

Add these methods to `WorkbenchView` (e.g. after `_on_export_clicked`):

```python
    def _update_export_csv_state(self) -> None:
        has_traces = bool(self._traces)
        has_annotations = bool(self._annotations)
        for i in (0, 1, 2):
            self._export_csv_menu.entryconfig(i, state="normal" if has_traces else "disabled")
        self._export_csv_menu.entryconfig(3, state="normal" if has_annotations else "disabled")

    def _meta_ids(self) -> tuple:
        meta = self.controller.get_trial_meta()
        return meta.get("participant_id", ""), meta.get("session_date", "")

    def _default_csv_filename(self, prefix: str) -> str:
        participant_id, session_date = self._meta_ids()
        parts = [prefix, participant_id or "session"] + ([session_date] if session_date else [])
        return "_".join(parts) + ".csv"

    def _prompt_and_write_csv(self, prefix: str, fieldnames: list, rows: list) -> None:
        out_path = filedialog.asksaveasfilename(
            title=f"Save {prefix.replace('_', ' ').title()} CSV",
            initialfile=self._default_csv_filename(prefix),
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not out_path:
            return
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        messagebox.showinfo("Exported", f"Saved to:\n{out_path}")

    def _on_export_traces_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        fieldnames, rows = engine.traces_to_csv_rows(self._traces, participant_id, session_date)
        self._prompt_and_write_csv("traces", fieldnames, rows)

    def _on_export_per_trace_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        snapshot = self.get_metrics_snapshot()
        fieldnames, rows = engine.per_trace_metrics_to_csv_rows(
            snapshot["per_trace"], participant_id, session_date)
        self._prompt_and_write_csv("per_trace_metrics", fieldnames, rows)

    def _on_export_vs_reference_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        snapshot = self.get_metrics_snapshot()
        fieldnames, rows = engine.vs_reference_metrics_to_csv_rows(
            snapshot["reference"], snapshot["vs_reference"], participant_id, session_date)
        self._prompt_and_write_csv("comparison_metrics", fieldnames, rows)

    def _on_export_annotations_csv(self) -> None:
        participant_id, session_date = self._meta_ids()
        fieldnames, rows = engine.annotations_to_csv_rows(
            self.get_annotations(), participant_id, session_date)
        self._prompt_and_write_csv("annotations", fieldnames, rows)
```

- [ ] **Step 6: Wire `_update_export_csv_state()` into the three state-changing call sites**

At the end of `_recompute_metrics()` (Task 4), add one line:

```python
        self._update_export_csv_state()
```

At the end of `set_traces()` (after `self._plot_canvas.draw_idle()`), add:

```python
        self._update_export_csv_state()
```

At the end of `_on_mark_milestone()` (after `self._plot_canvas.draw_idle()`), add:

```python
        self._update_export_csv_state()
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_pendulastic_workbench.py -v`
Expected: all tests pass, including the three new Export CSV tests.

- [ ] **Step 8: Run the full test suite for both modules**

Run: `pytest tests/test_workbench_engine.py tests/test_pendulastic_workbench.py -v`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add pendulastic_workbench.py tests/test_pendulastic_workbench.py
git commit -m "feat: wire Export CSV menu (traces/per-trace/comparison/annotations) with defensive disabled states"
```

---

### Task 6: Apply the ttk dark theme app-wide + final verification

**Files:**
- Modify: `pendulastic_workbench.py:510-524` (`App.__init__`)

**Interfaces:**
- Consumes: `workbench_style.apply_ttk_theme`, `workbench_style.PALETTE` (Task 1).

- [ ] **Step 1: Apply the theme in `App.__init__`**

Replace `App.__init__` (lines 510-524) with:

```python
    def __init__(self) -> None:
        super().__init__()
        self.title("Pendulastic Workbench")
        self.geometry("1200x800")
        self.resizable(True, True)
        self.minsize(900, 600)
        ws.apply_ttk_theme(self)
        self.configure(bg=ws.PALETTE["BG"])

        self._trial_meta: dict = {}
        self._status_var = tk.StringVar(value="")

        self._load_panel = TrialLoadPanel(self, controller=self)
        self._workbench_view = WorkbenchView(self, controller=self)
        self._load_panel.pack(fill="both", expand=True)
        tk.Label(self, textvariable=self._status_var, anchor="w",
                bg=ws.PALETTE["PANEL"], fg=ws.PALETTE["FG3"],
                font=ws.FONT_SMALL).pack(side="bottom", fill="x", padx=8, pady=2)
```

- [ ] **Step 2: Run the full test suite**

Run: `pytest tests/test_workbench_engine.py tests/test_pendulastic_workbench.py -v`
Expected: all tests pass **except** `test_standalone_app_back_to_mode_select_is_a_genuine_noop` — confirmed failing on unmodified `main` in this environment (`_tkinter.TclError: Can't find a usable init.tcl`, triggered by instantiating a second independent `tk.Tk()` root while `_get_root()`'s module-level root is still alive; a Tcl/Tk environment issue, not caused by any change in this plan). If that test starts failing with a *different* error after this task's edits, treat it as a real regression; the baseline `TclError` on this one test is expected and pre-existing.

- [ ] **Step 3: Manual smoke test**

Run: `python pendulastic_workbench.py`

Verify by hand:
1. `TrialLoadPanel` renders on the dark palette with four cards (Trial Files, Participant & Session, HPE Models, Personalization); resize the window narrower and wider — no card clips or overlaps.
2. Enter a Participant ID and Session Date, load a real trial with at least IMU + OptiTrack data.
3. `WorkbenchView` renders on the dark palette; the matplotlib figure background matches the theme (no pale rectangle); scrub the video and confirm the plot cursor tracks it.
4. Both metrics tables show data with no truncated/clipped column values; resize the window narrower and confirm columns hold their width rather than silently truncating text.
5. Mark a milestone; confirm "Export CSV ▾" → "Annotations..." becomes enabled.
6. Export all four CSVs plus the existing "Export Session (JSON)...", to a scratch folder.
7. Open each of the four CSVs with `python -c "import pandas as pd; print(pd.read_csv('<path>').head())"` — confirm no parse errors and that `participant_id`/`session_date` columns are populated.

- [ ] **Step 4: Commit**

```bash
git add pendulastic_workbench.py
git commit -m "feat: apply dark ttk theme app-wide in Workbench App"
```

---

## Plan Self-Review Notes

- **Spec coverage**: Section 2 (architecture split) → Tasks 1-2. Section 3 (`TrialLoadPanel` cards + participant/session) → Task 3. Section 4 (`WorkbenchView` restyle + Treeview tables) → Task 4. Section 4 (Export CSV menu) → Task 5. Section 5 (defensive disabled states) → Task 5 Steps 5-6. Section 6 (testing) → every task's Steps 1-2/verify-pass steps, all using the real `tests/test_pendulastic_workbench.py` headless-`tk.Tk()` convention (corrected from the original spec text per the earlier spec amendment). Section 7 (out of scope) — no task touches `pendulastic_app.py` or `pendulastic_viewer.py`.
- **Type/name consistency checked**: `windowed_pt_params` keys (`R2n, N, phi_max_ratio, omega_max_n, f, area_ratio, omega_min_n`) match what Task 2's `per_trace_metrics_to_csv_rows` and Task 4's `_recompute_metrics` both read. `compare_pair`/`get_metrics_snapshot`'s `vs_reference` keys (`status, rmse_deg, mae_deg, lag_sec, timing_offset_sec, error`) match Task 2's `vs_reference_metrics_to_csv_rows` and Task 4's `_recompute_metrics`. `self._export_csv_menu` entry indices (0-3) are consistent between Task 5's `add_command` order and its `_update_export_csv_state`/tests.
