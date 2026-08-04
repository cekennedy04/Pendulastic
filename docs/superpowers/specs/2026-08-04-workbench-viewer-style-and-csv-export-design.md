# Porting Viewer Dashboard Style & Universal CSV Export to the Workbench — Design Spec

**Status:** Approved (brainstorming complete, pending user review of this document)
**Date:** 2026-08-04

## 1. Problem & Existing Landscape

`pendulastic_workbench.py` (`TrialLoadPanel`, `WorkbenchView`) uses zero custom
styling — plain default-Tk widgets, mixed `grid`/`pack` layout — and only
exports session data as JSON (`export_session`, Section 6 of the original
Workbench design spec). There is no CSV export anywhere in the Workbench.

`pendulastic_viewer.py` is a 9,000-line legacy CV-tracking file and is *not*
itself a clean reference to imitate wholesale. But one part of it,
`_HistoryWindow` (~line 8049) plus its `_C` dark-palette dict (~line 169), is
a genuinely polished, already-shipped dashboard: dark navy/steel-blue theme,
`Segoe UI` typography, card-style `tk.Frame` sections, a styled
`ttk.Treeview` (`Dash.Treeview`), MAS-color badges, and an existing
`_cmd_export_csv` (participant-history CSV via `csv.DictWriter` +
`filedialog.asksaveasfilename`). This is the concrete design language to
port — not the file as a whole.

**Scope decision:** this restyle covers only `TrialLoadPanel` and
`WorkbenchView` in `pendulastic_workbench.py` (confirmed with the user).
`pendulastic_app.py`'s other panels (`ModeSelectView`, `UploadMetaView`,
`PostProcessingPanel`, `AcquisitionPanel`) and `pendulastic_viewer.py` itself
are explicitly out of scope and untouched.

**Concurrency note:** the worktree `workbench-pt-score-panel` is currently
live-editing `pendulastic_workbench.py` (adding a PT-score panel). This work
is built in its own new worktree off `main`; rebase/merge order is decided
at landing time, not blocking the start of this work.

## 2. Architecture

Two new/changed pieces, following this repo's established config/engine/UI
separation:

- **`workbench_style.py`** (new) — dark palette dict (copied from
  `pendulastic_viewer.py`'s `_C`, not imported — `pendulastic_workbench.py`
  must not pull in `pendulastic_viewer.py`'s heavy `cv2`/`mediapipe`/
  `ultralytics` dependency chain just for six color strings), plus small
  builder helpers (`card_frame`, `primary_button`, `section_label`) and a
  `apply_ttk_theme(root)` function that configures a `clam`-based
  `ttk.Style` for `ttk.Scale`/`ttk.OptionMenu`/`ttk.PanedWindow`/
  `ttk.Scrollbar`/`ttk.Treeview` (plain `ttk` widgets ignore `bg=`/`fg=`;
  they need explicit `style.configure(...)`, the same mechanism
  `_HistoryWindow` already uses for `Dash.Treeview`). Called once from
  `App.__init__`.
- **`workbench_engine.py`** gains four pure CSV row-builder functions,
  matching the existing `export_session()` precedent (no Tkinter, no file
  I/O, fully unit-testable):
  - `traces_to_csv_rows(traces: dict, participant_id: str, session_date: str) -> (fieldnames, rows)`
    — one row per sample per visible trace: `participant_id, session_date,
    label, t_sec, angle_deg`.
  - `per_trace_metrics_to_csv_rows(per_trace: dict, participant_id, session_date) -> (...)`
    — one row per visible trace: `participant_id, session_date, label,
    area_ratio, N, f_hz, R2n, omega_max_n, omega_min_n`.
  - `vs_reference_metrics_to_csv_rows(reference: str, vs_reference: dict, participant_id, session_date) -> (...)`
    — one row per compared trace: `participant_id, session_date, label,
    reference, rmse_deg, mae_deg, lag_sec, timing_offset_sec, status`.
  - `annotations_to_csv_rows(annotations: dict, participant_id, session_date) -> (...)`
    — one row per milestone: `participant_id, session_date, label,
    frame_index, t_sec`.

  `participant_id`/`session_date` are ordinary columns on every row (not a
  comment preamble) — RFC 4180-clean, parses unmodified in `pd.read_csv`/
  `read.csv`, and matches "structured headers" more literally than a
  human-readable comment block would.

## 3. `TrialLoadPanel` Restructuring

Card-sectioned layout (mirroring `_HistoryWindow`'s card pattern), all on
the dark palette:

- **Header bar** — title + "← Back to Main Menu" (unchanged logic, restyled).
- **Card: "Trial Files"** — the three existing file pickers (IMU / video /
  OptiTrack), grouped visually, unchanged logic.
- **Card: "Participant & Session"** *(new)* — `Participant ID` (text,
  blank-safe) and `Session Date` (text, defaults to today's date,
  blank-safe) fields, threaded into `trial_meta`. Existing behavior (trial
  loads with either field blank) is unchanged; blank values render as
  empty-string columns in the CSVs rather than blocking export.
- **Card: "HPE Models"** — existing model checkboxes, restyled grid, no
  logic change.
- **Card: "Personalization (optional)"** — existing femur/tibia fields, no
  logic change.
- "Load Trial" as a primary-styled button at the bottom (unchanged command).

## 4. `WorkbenchView` Restructuring

- Top bar: reference selector + "← Load Different Trial" — restyled, same
  commands.
- Video pane: mechanically unchanged (`cv2.VideoCapture` →
  `ImageTk.PhotoImage` → `tk.Label`); dark-bordered to match the theme.
- Matplotlib figure gets a dark-theme pass: `fig.patch.set_facecolor`, axes
  facecolor/tick/label/grid colors pulled from `workbench_style`'s palette,
  so the plot doesn't render as a pale rectangle inside a dark app. No
  change to what's plotted or how (`_ax.plot`, `axvline`, annotations all
  unchanged).
- Visibility/lag-override row becomes styled "chip" frames (card-colored,
  consistent padding) instead of bare `tk.Checkbutton` rows — same
  variables/bindings, just restyled containers.
- **Metrics readout replaces the single `tk.Text` blob with two
  `ttk.Treeview` tables**, styled like `Dash.Treeview`:
  - **Per-Trace table**: columns `Trace, Area Ratio, N, f (Hz), R2n,
    ωmax_n, ωmin_n`.
  - **Comparison table**: columns `Trace, Reference, RMSE (deg), MAE (deg),
    Lag (s), Timing Offset (s), Status`.

  Both use **explicit fixed column widths and per-column anchors** (ported
  directly from `_HistoryWindow._COL_W`'s pattern — a tested width/anchor
  per column, `stretch` only on the widest/last column) and are wrapped
  with a paired `ttk.Scrollbar` inside their card frame, so dense metric
  sets never truncate silently or force a layout-breaking horizontal
  scrollbar. `get_metrics_snapshot()` (already the single source of truth
  for both the live display and export, per the original Workbench spec)
  populates both tables — same values a researcher sees are the same
  values that get exported.
- Annotation toolbar: milestone picker + "Mark Here" + existing "Export
  Session (JSON)" (unchanged) + new **"Export CSV ▾"** menu button with four
  items — Traces / Per-Trace Metrics / Comparison Metrics / Annotations.
  Each: calls the matching `workbench_engine` row-builder with the current
  `trial_meta`'s `participant_id`/`session_date`, then writes via
  `csv.DictWriter`, following `_HistoryWindow._cmd_export_csv`'s exact
  save-dialog + confirm pattern (`filedialog.asksaveasfilename` with a
  descriptive default filename — e.g. `traces_{participant_id}_
  {session_date}.csv`, `per_trace_metrics_{...}.csv` — then
  `messagebox.showinfo` on success).

## 5. Defensive UI States

- All four "Export CSV" menu items are disabled when `self._traces` is
  empty (nothing loaded yet) — matches the existing "Export Session (JSON)"
  button's implicit precondition.
- The "Annotations" CSV item is independently disabled when
  `self._annotations` is empty, even if traces are loaded (these are
  orthogonal states — a trial can have traces with zero milestones marked).
- Both Treeview tables show a single "No data yet" placeholder row (styled,
  non-interactive) when their source dict is empty, rather than rendering
  blank — avoids an ambiguous empty-vs-broken appearance.

## 6. Testing

- **`tests/test_workbench_engine.py`**: unit tests for the four new row-
  builders — empty-dict input produces empty `rows` (not an exception) with
  correct `fieldnames`; multi-trace synthetic fixtures produce the correct
  row count and correct `participant_id`/`session_date` threading onto
  every row; a `vs_reference` entry with `status != "ok"` still produces a
  row (with metric fields blank/`None`) rather than being silently dropped.
- **UI**: manual smoke test only — load a real multi-modal trial, confirm
  dark theme renders correctly at multiple window sizes (Treeview columns
  must not truncate per Section 4), export all four CSVs and the JSON,
  reopen each CSV in a plain-text viewer and in `pandas.read_csv` to
  confirm no parse errors. Matches this repo's existing precedent of not
  unit-testing Tkinter panels directly.

## 7. Explicitly Out of Scope

- `pendulastic_app.py`'s other panels (`ModeSelectView`, `UploadMetaView`,
  `PostProcessingPanel`, `AcquisitionPanel`) — not restyled in this pass.
- `pendulastic_viewer.py` — not modified; its `_C` palette is copied, not
  imported, and its `_HistoryWindow`/`ValidationWindow` classes are
  reference-only.
- No changes to `compute_pt_params`, kinematic calculations, or any
  existing `workbench_engine` math function (`compare_pair`,
  `windowed_pt_params`, `extrema_jitter`, `_active_window_end`) — the four
  new functions are pure additive row-formatters over data those functions
  already produce.
- No change to the existing JSON `export_session` path — CSV export is
  additive, not a replacement.
- Rebase/merge-order against the concurrently in-flight
  `workbench-pt-score-panel` worktree is a landing-time decision, not
  addressed here.
