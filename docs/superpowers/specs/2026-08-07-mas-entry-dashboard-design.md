# MAS Score Entry + Live Validation Dashboard — Design Spec

**Status:** Approved
**Date:** 2026-08-07

---

## 1. Goal

Give clinicians a live, in-app way to enter MAS scores directly into
`mas_scores.csv` and immediately see the PT-score-vs-MAS validation dashboard
update — replacing the current workflow of hand-editing the CSV and running
`mas_validation.py` from the command line.

## 2. Background / Why

`docs/superpowers/specs/2026-08-06-mas-pt-score-validation-design.md`
explicitly deferred UI wiring: *"Wiring MAS entry into a live UI
(`pendulastic_app.py`) is explicitly deferred per user decision — CSV backfill
+ ongoing manual entry only, for now."* `mas_scores.csv` now has 17 rows
entered by hand, and `mas_validation.py` already computes stats and renders a
3-panel figure (`mas_validation_figure.png`) — but only as a script run
manually after editing the CSV. This spec resumes and closes that deferred
item.

The live `mas_scores.csv` schema has also drifted from the original spec: it
gained a `diagnosis` column and never got a `notes` column. This design
targets the live schema, not the original spec's.

## 3. Scope

- New: a `MasEntryPanel` in `pendulastic_app.py`, reachable from
  `ModeSelectView`, combining an entry form with a live-refreshing validation
  dashboard.
- New: `append_mas_score()` in `mas_validation.py`.
- Refactor: `mas_validation.make_validation_figure()` splits into a pure
  `build_validation_figure()` (no I/O, returns a `Figure`) and a thin
  `save_validation_figure()` wrapper (unchanged CLI behavior).
- Fix: `mas_validation.py`'s module-level `matplotlib.use("Agg")` moves behind
  `if __name__ == "__main__":` so importing it from the interactive Tkinter
  app doesn't force the whole process onto the non-interactive backend.
- Out of scope: any change to how PT scores or MAS predictions are computed;
  any change to `pendulastic_pt_score.py`; any change to the stats formulas in
  `compute_validation_stats`; authentication/access control on the entry form;
  editing or deleting existing `mas_scores.csv` rows (append-only, matching
  how the CSV is used today).

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `mas_validation.py` | Add `append_mas_score()`. Split `make_validation_figure()` into `build_validation_figure()` (pure) + `save_validation_figure()` (I/O wrapper). Guard `matplotlib.use("Agg")` behind `__name__ == "__main__"`. |
| `pendulastic_app.py` | New `MasEntryPanel(tk.Frame)` class. `App.__init__` registers it. `ModeSelectView` gains a 5th nav button routing to it. |
| `tests/test_mas_validation.py` | New tests for `append_mas_score()`; existing figure tests updated to call `build_validation_figure()`; new test for `save_validation_figure()`. |
| `tests/test_app.py` | New headless tests for `MasEntryPanel` (validation, save-triggers-refresh, skipped-row surfacing, empty state). |

## 5. `mas_validation.py` changes

### 5.1 `append_mas_score(row: dict, csv_path=MAS_CSV) -> None`

- Validates `row["mas_grade"]` via the existing `_valid_grade()`; raises
  `ValueError(f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})")`
  if invalid — no write attempted.
- Reads the CSV's current header via `csv.DictReader` to get the live
  fieldnames (not a hardcoded column list, so a future schema drift doesn't
  silently break this function the way the original spec's schema already
  drifted once).
- Appends one row via `csv.DictWriter` in `"a"` mode, writing only the keys
  present in the file's header (any extra keys in `row` not in the header are
  ignored; any header column missing from `row` is written empty).

### 5.2 Figure building split

`make_validation_figure(pairs, stats, out_path)` (existing, does both
building and saving) becomes:

- `build_validation_figure(pairs, stats) -> matplotlib.figure.Figure` — all
  the existing panel-building logic (3-panel boxplot/heatmap/ROC), no I/O,
  does not call `plt.close()`.
- `save_validation_figure(pairs, stats, out_path) -> None` — calls
  `build_validation_figure()`, then `fig.savefig(...)`, `plt.close(fig)`,
  prints `"-> {out_path}"`. `main()` calls this instead of the old
  `make_validation_figure`; CLI behavior and output are unchanged.

### 5.3 Backend guard

```python
import matplotlib
if __name__ == "__main__":
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
```

Today `matplotlib.use("Agg")` runs unconditionally at import time. If
`pendulastic_app.py` (which needs the interactive `TkAgg` backend for its
embedded Workbench plots) imports `mas_validation`, this would force the
entire process onto `Agg` — silently breaking Tkinter plot embedding
elsewhere in the app. Gating it on `__name__ == "__main__"` preserves the
CLI's existing headless behavior while leaving the backend alone when
imported as a library.

## 6. `pendulastic_app.py` — `MasEntryPanel`

Registered in `App.__init__` alongside `AcquisitionPanel`/`PostProcessingPanel`
(`self._mas_entry = MasEntryPanel(self, controller=self)`), reachable from a
5th button on `ModeSelectView` next to Live Recording / Upload & Analyze /
Multi-Modal Comparison / Analysis.

### 6.1 Entry form (top half)

Styled like `AcquisitionPanel` (`ws.PALETTE`, `tk.Entry`/`Radiobutton`/
`ttk.Combobox`). Columns match the live `mas_scores.csv` header exactly:
`participant, leg, condition, diagnosis, mas_grade, assessed_by,
assessed_date`.

| Field | Widget | Required |
|---|---|---|
| `participant` | `tk.Entry` | Yes |
| `leg` | `Radiobutton` Left/Right (lowercased on save) | Yes |
| `condition` | `tk.Entry` | No |
| `diagnosis` | `tk.Entry` | No |
| `mas_grade` | `ttk.Combobox`, values = `pendulastic_pt_score.MAS_ORDER` | Yes |
| `assessed_by` | `tk.Entry` | No |
| `assessed_date` | `tk.Entry`, pre-filled with today's date, editable | No |

**Save button:** if `participant` or `mas_grade` is blank, shows an inline
error label and does not write. Otherwise calls
`mas_validation.append_mas_score(row)`, then re-runs the refresh pipeline
(§6.3). On success the form fields are **not** cleared — batch entry commonly
means both legs of the same participant back-to-back, and clearing would
fight that.

### 6.2 Dashboard (bottom half)

A `FigureCanvasTkAgg` embedding the `Figure` from
`mas_validation.build_validation_figure()`, matching the embedding pattern
already used in `pendulastic_workbench.py`.

### 6.3 Refresh pipeline

Runs both on panel open (`tkraise`/`show()`) and after every successful save:

1. `mas_validation.load_mas_scores(MAS_CSV)`
2. `mas_validation.pair_pt_and_mas(rows, mas_validation._pt_lookup_factory())`
3. Split into `valid` (no `_skip_reason`) and `skipped` (has `_skip_reason`)
4. If `valid` is empty: canvas area shows a centered placeholder ("No
   MAS-scored trials with matching trial data yet") instead of a figure.
5. Otherwise: `compute_validation_stats(valid)` →
   `build_validation_figure(valid, stats)` → replace the embedded canvas's
   figure.
6. `skipped` rows render as lines in a small scrollable status text area
   below the save button, e.g. `"P14 left/pre: no matching trial data for
   this participant/leg/condition"` — reusing the same skip-reason strings
   `main()` already prints to stdout. This is what tells a clinician their
   just-saved row is provenance-only for now (MAS often gets assessed before
   that participant's trial is recorded/processed) rather than having it
   silently missing from the figure.

## 7. Error Handling

- Blank `participant` or `mas_grade` on Save → inline error, no write.
- `append_mas_score` raising on an invalid grade (defense in depth; the
  combobox already restricts input) → caught, shown as the same inline error,
  no write.
- Zero valid pairs after refresh → placeholder text, not a crash or blank
  canvas.
- A saved row with no matching trial data → not an error; surfaces in the
  status area per §6.3 step 6.

## 8. Testing

### `tests/test_mas_validation.py`
- `test_append_mas_score_writes_using_existing_header_order` — appends a row
  to a `tmp_path` CSV with a known header, asserts the written row matches
  that header's column order.
- `test_append_mas_score_rejects_invalid_grade` — invalid `mas_grade` raises
  `ValueError`, file is unchanged (no row appended).
- `test_append_mas_score_round_trips_through_load_mas_scores` — appended row
  is readable back via `load_mas_scores()`.
- Existing `make_validation_figure` tests updated to call
  `build_validation_figure()` directly (pure, no `out_path`/file assertions).
- `test_save_validation_figure_writes_png` — thin-wrapper coverage: calls
  `save_validation_figure()`, asserts the PNG file exists at `out_path`.

### `tests/test_app.py`
Headless (`Agg` backend, no real Tk mainloop — matching this file's existing
conventions):
- `test_mas_entry_panel_blocks_save_on_missing_required_fields` — blank
  `participant` or `mas_grade` shows the inline error, `append_mas_score` is
  not called (mock/spy).
- `test_mas_entry_panel_save_appends_and_refreshes` — valid save calls
  `append_mas_score` once and triggers a canvas re-render.
- `test_mas_entry_panel_shows_skipped_row_status` — a row with no matching
  trial data appears in the status area text, not silently dropped.
- `test_mas_entry_panel_empty_state_placeholder` — zero valid pairs renders
  the placeholder text instead of a figure.

## 9. Out of Scope / Future

- Editing or deleting existing `mas_scores.csv` rows from the UI
  (append-only for now, matching current CSV usage).
- Auto-populating `participant`/`leg` from an in-progress live recording
  session (this panel is a standalone entry point, not wired to
  `AcquisitionPanel`'s session state).
- Updating `run_pt_analysis.py`'s existing end-of-run nudge line ("...run
  mas_validation.py to refresh the validation report") — it still points at
  the CLI script, which remains valid for anyone not using the in-app panel;
  changing that message is a separate, small follow-up if desired.
