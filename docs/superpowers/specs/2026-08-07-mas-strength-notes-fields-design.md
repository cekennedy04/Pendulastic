# MAS Entry: Stronger-Leg + Notes Fields — Design Spec

**Status:** Approved
**Date:** 2026-08-07

---

## 1. Goal

Add two fields to the MAS score entry form (`MasEntryPanel` in `pendulastic_app.py`):
a "Stronger Leg" dropdown and a free-text "Notes" box — and extend
`mas_scores.csv`'s schema to store them, without disrupting the 15+ rows
already recorded in the live file.

## 2. Background / Why

The user tested the MAS entry feature live (added a real row for participant
15) and asked for two more fields a clinician records alongside a MAS grade:
which leg is subjectively stronger, and free-text notes. `mas_scores.csv` is
gitignored, real, and already has data — this spec's central concern is
adding columns to that live schema without losing or corrupting what's
already there.

## 3. Scope

- New: `stronger_leg` and `notes` columns in `mas_scores.csv`.
- New: `mas_validation.append_mas_score()` auto-widens the CSV header when a
  row being saved has keys the file doesn't have yet, instead of silently
  dropping them (today's `extrasaction="ignore"` behavior). Existing rows
  get blank values for the new columns; no existing data is lost or edited.
- New: two fields on `MasEntryPanel`'s form — a readonly "Stronger Leg"
  dropdown and a multi-line "Notes" box.
- Out of scope: analysis/statistics use of these two fields (they're
  provenance/context, not inputs to `compute_validation_stats` or the PT
  score); editing values on existing rows; a migration script (the
  auto-widen happens organically on the next save, per user's choice).

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `mas_validation.py` | `DEFAULT_MAS_FIELDS` gains `stronger_leg`/`notes`. New `STRONGER_LEG_OPTIONS` constant. `append_mas_score()` gains header auto-widening (atomic rewrite) when the row has new keys. |
| `pendulastic_app.py` | `MasEntryPanel._build_widgets` gains `stronger_leg_var` (dropdown) and `notes_text` (Text widget). `_on_save_clicked`'s row dict gains both keys; `notes_text` is cleared on successful save, `stronger_leg_var` is not. |
| `tests/test_mas_validation.py` | New tests for header auto-widening: widens on new keys, preserves existing rows with blank new-column values, atomic (temp file + `os.replace`), no-op widen when row's keys are already a subset of the header. |
| `tests/test_app.py` | New tests for the two fields: included in the saved row dict, `notes_text` cleared after save, `stronger_leg_var` NOT cleared after save. |

## 5. `mas_scores.csv` schema

New header (existing live file will be widened to this on its next save):

```
participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date,stronger_leg,notes
```

- `stronger_leg`: one of `""` (not assessed), `"left"`, `"right"`, `"equal"`.
  Not used in any PT-score/MAS-validity statistic — provenance only, like
  `diagnosis`.
- `notes`: free text, may be empty. May contain commas/newlines — `csv`
  module's standard quoting handles this already (no special handling
  needed beyond what `csv.DictWriter` already does for every other field).

## 6. `mas_validation.py` changes

### 6.1 `DEFAULT_MAS_FIELDS`

```python
DEFAULT_MAS_FIELDS = ["participant", "leg", "condition", "diagnosis",
                      "mas_grade", "assessed_by", "assessed_date",
                      "stronger_leg", "notes"]
```

Only affects a brand-new `mas_scores.csv` (created when the file doesn't
exist yet, per the existing `append_mas_score` missing-file guard) — it now
starts with the full schema already.

### 6.2 `STRONGER_LEG_OPTIONS`

```python
STRONGER_LEG_OPTIONS = ["", "left", "right", "equal"]
```

Placed next to `MAS_ORDER`/`MAS_RANK`. The leading `""` is the "not
assessed" default the dropdown starts on — consistent with `stronger_leg`
being optional, unlike `mas_grade`.

### 6.3 `append_mas_score()` — header auto-widening

Current behavior (unchanged for the common case): read the file's existing
header, append the new row via `DictWriter(..., extrasaction="ignore")`,
silently dropping any row key not already in the header.

New behavior: before that append, check whether `row` has any key not in
the current header. If so — this happens exactly once, the first time a
user saves after this feature ships, since `stronger_leg`/`notes` are now
unconditionally present in every row the form submits — widen the header
instead of dropping the new fields:

1. Read all existing rows into memory via `csv.DictReader`.
2. Compute the widened header: existing fieldnames, in order, plus
   whichever of the new row's keys aren't already present, appended at the
   end (this determinism matters — it's why the fresh-file `DEFAULT_MAS_FIELDS`
   in §6.1 lists `stronger_leg`/`notes` last, so a brand-new file and a
   widened old file end up with the same column order).
3. Rewrite the file atomically: write header + all existing rows (missing
   new-column values default to `""` via `DictWriter`'s standard `restval`)
   + the new row, to a temp file (`csv_path + ".tmp"`), then `os.replace()`
   it into place — the same pattern `pendulastic_storage.save_trial` already
   uses, so a crash mid-write can't corrupt the real file; either the old
   file or the fully-written new one is on disk, never a half-written one.

If `row`'s keys are already a subset of the header (every future save,
once this widening has happened once), behavior is unchanged from today —
a plain append, no rewrite.

## 7. `pendulastic_app.py` — `MasEntryPanel` changes

### 7.1 Form fields

Inserted after the existing "Leg" row (row 1) and before "Condition" (today's
row 2), pushing Condition/Diagnosis/MAS Grade/Assessed By/Assessed Date down
one grid row each:

- **Stronger Leg:** `ttk.Combobox(state="readonly", values=STRONGER_LEG_OPTIONS)`
  bound to `self.stronger_leg_var`, default `""`.

**Notes** is placed after the existing "Assessed Date" row, before the
error/Save area:

- **Notes:** `tk.Text(form, height=3, wrap="word")` — NOT bound to a
  StringVar (Tk's `Text` widget doesn't support one); read via
  `self.notes_text.get("1.0", "end").strip()` in `_on_save_clicked`, same
  access pattern already used for `status_text` elsewhere in this class.
  No scrollbar needed at 3 rows — matches `status_text`'s general styling
  (`bg=ws.PALETTE["SURFACE"]`, `fg=ws.PALETTE["FG"]`) but doesn't need
  `status_text`'s read-only/scrollbar treatment since this one is
  user-editable.

### 7.2 `_on_save_clicked` changes

Row dict gains two keys:

```python
"stronger_leg": self.stronger_leg_var.get().strip().lower(),
"notes": self.notes_text.get("1.0", "end").strip(),
```

On successful save: `self.notes_text.delete("1.0", "end")` is added
alongside the existing `self.mas_grade_var.set("")` — notes are specific to
one observation, not the batch, so they shouldn't persist into the next
row the way participant/leg/diagnosis intentionally do.
`self.stronger_leg_var` is **not** cleared — a strength assessment
typically holds across both legs' rows for the same session, same
reasoning as `diagnosis`/`condition` already not being cleared.

## 8. Error Handling

- `stronger_leg`/`notes` are both optional — blank values are valid and
  saved as empty strings, no validation beyond what already exists for
  `condition`/`diagnosis`/`assessed_by` (i.e., none).
- The header-widening rewrite reuses `append_mas_score`'s existing
  `_valid_grade` check ordering — an invalid `mas_grade` still raises
  `ValueError` before any file is touched, widening included, matching
  today's "no write attempted" guarantee.
- If the atomic rewrite's temp-file write fails partway (disk full, etc.),
  the exception propagates same as any other I/O error today — the
  original file is untouched (the failure is on the `.tmp` file, before
  `os.replace`), and `MasEntryPanel._on_save_clicked`'s existing
  `except Exception as e: self._set_feedback(f"Could not save: {e}")`
  already surfaces this to the user rather than a dead click.

## 9. Testing

### `tests/test_mas_validation.py`
- `test_append_mas_score_widens_header_when_row_has_new_fields` — starting
  CSV has the old 7-column header + 1 data row; append a row with
  `stronger_leg`/`notes`; assert the file's new header includes all 9
  columns in the documented order, the old row's new-column cells are
  `""`, and the new row's values round-trip correctly via `load_mas_scores`.
- `test_append_mas_score_widening_is_atomic` — monkeypatch `os.replace` to
  raise partway through; assert the original file is unchanged (the `.tmp`
  file may exist or not, but `csv_path` itself still has the pre-widen
  content).
- `test_append_mas_score_no_widen_when_row_keys_are_subset_of_header` — CSV
  already has all 9 columns; append a row with just the 9 keys; assert no
  `.tmp` file is created (i.e. the plain-append fast path ran, not a
  rewrite) — can check via `monkeypatch` spying on whether `os.replace` was
  called.
- `test_default_mas_fields_includes_new_columns` — `DEFAULT_MAS_FIELDS[-2:]
  == ["stronger_leg", "notes"]`.

### `tests/test_app.py`
- `test_mas_entry_panel_save_includes_stronger_leg_and_notes` — set
  `stronger_leg_var` and fill `notes_text`, save, assert both land in the
  `append_mas_score` call's row dict with the exact values.
- `test_mas_entry_panel_save_clears_notes_but_not_stronger_leg` — after a
  successful save, assert `notes_text.get("1.0", "end").strip() == ""` and
  `stronger_leg_var.get()` is unchanged from what was set before Save.

## 10. Out of Scope / Future

- Using `stronger_leg` or `notes` in `compute_validation_stats` or any
  figure panel — both are provenance/context fields only, same tier as
  `diagnosis`/`assessed_by`.
- A standalone migration script for the CSV header — the auto-widen on next
  save covers it, per the user's explicit choice over a manual/scripted
  migration.
- Editing or backfilling `stronger_leg`/`notes` on the 15+ rows already
  recorded before this feature — those rows simply have blank values for
  the two new columns going forward, same as any pre-migration row.
