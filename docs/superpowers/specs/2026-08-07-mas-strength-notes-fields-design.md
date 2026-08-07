# MAS Entry: Stronger-Leg + Notes Fields — Design Spec

**Status:** Approved
**Date:** 2026-08-07
**Revised:** 2026-08-07 — after a Codex second-opinion review (see §11), tightened
the header-widening design: restricted to an explicit allowlist (was: any
unrecognized row key), added explicit malformed/empty-file handling, and added
server-side `stronger_leg` validation. Codex's concurrency/fsync/carryover
findings were considered and deliberately not adopted — see §11 for why.

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
  row being saved has `stronger_leg`/`notes` values the file doesn't have
  columns for yet (an explicit allowlist, not any unrecognized key — see
  §6.3), instead of silently dropping them (today's `extrasaction="ignore"`
  behavior). Existing rows get blank values for the new columns; no
  existing data is lost or edited.
- New: two fields on `MasEntryPanel`'s form — a readonly "Stronger Leg"
  dropdown and a multi-line "Notes" box.
- Out of scope: analysis/statistics use of these two fields (they're
  provenance/context, not inputs to `compute_validation_stats` or the PT
  score); editing values on existing rows; a migration script (the
  auto-widen happens organically on the next save, per user's choice).

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `mas_validation.py` | `DEFAULT_MAS_FIELDS` gains `stronger_leg`/`notes`. New `STRONGER_LEG_OPTIONS`, `WIDENABLE_MAS_FIELDS` constants and `_valid_stronger_leg()`. `append_mas_score()` gains allowlisted header auto-widening (atomic rewrite) plus malformed/empty-file handling. |
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

### 6.2 `STRONGER_LEG_OPTIONS` and validation

```python
STRONGER_LEG_OPTIONS = ["", "left", "right", "equal"]


def _valid_stronger_leg(value: str) -> bool:
    return value in STRONGER_LEG_OPTIONS
```

Placed next to `MAS_ORDER`/`MAS_RANK`/`_valid_grade`. The leading `""` is the
"not assessed" default the dropdown starts on — consistent with
`stronger_leg` being optional, unlike `mas_grade`.

Unlike `condition`/`diagnosis`/`assessed_by` (genuinely free text, never
validated), `stronger_leg` is meant to be a closed 3-value enum, the same
kind of field `mas_grade` is — so it gets the same server-side validation
`mas_grade` already has, not just UI-level constraint via the combobox's
`state="readonly"`. This matters because `append_mas_score()` is a public
function other callers (tests, future scripts) can invoke directly,
bypassing the combobox entirely.

### 6.3 `append_mas_score()` — header auto-widening

Current behavior (unchanged for the common case): read the file's existing
header, append the new row via `DictWriter(..., extrasaction="ignore")`,
silently dropping any row key not already in the header.

New behavior, in validation order (all before any file write, matching the
existing "no write attempted" guarantee for invalid `mas_grade`):

1. `mas_grade` validated via `_valid_grade` (unchanged, existing check,
   runs first).
2. `stronger_leg` validated via `_valid_stronger_leg`, if present in `row`
   (raises `ValueError` the same shape as the grade check: no write
   attempted).
3. Only then: check whether `row` has any key in
   `WIDENABLE_MAS_FIELDS = ["stronger_leg", "notes"]` that isn't already in
   the file's current header. This is deliberately an **explicit allowlist**,
   not "any key in `row` the header doesn't have" — the original draft of
   this spec would have silently and permanently added a new CSV column for
   *any* unrecognized key from any future caller (a typo'd dict key becomes
   permanent schema drift). Restricting to the two fields this spec actually
   introduces means an unrelated stray key still falls through to today's
   `extrasaction="ignore"` behavior — silently dropped, not silently
   promoted to a column. If a future feature needs to widen the schema
   again, it extends `WIDENABLE_MAS_FIELDS` explicitly, the same way this
   spec does.

If none of `WIDENABLE_MAS_FIELDS` need adding, behavior is unchanged from
today — read header, plain append, no rewrite. (Every save after the first
one following this feature's release takes this path, since the header now
already has both columns.)

If widening is needed, read the existing file's rows and header first, then
handle three cases:

- **Header is falsy** (`csv.DictReader.fieldnames` is `None` — an empty,
  zero-byte file; the file exists per `append_mas_score`'s existing
  missing-file check having already run, but has no header line yet, e.g.
  a previous run created it via `open(csv_path, "w")` and crashed before
  writing the header). Treat as "no existing data to preserve": the
  widened header is `DEFAULT_MAS_FIELDS` — this branch is allowlist-gated
  exactly like the normal-widen path (only `WIDENABLE_MAS_FIELDS` keys
  present in `row` can extend it), never on arbitrary `row` keys. There are
  zero existing rows to carry forward, so skip straight to writing header +
  the new row.
- **Header exists but a data row has more fields than the header
  describes** (a genuinely malformed/hand-edited CSV — `csv.DictReader`
  puts the overflow values under a `None` key). Do **not** attempt to widen
  or rewrite: raise `ValueError` naming the exact row number and the file
  path, telling the user the file needs manual repair before this feature's
  auto-widening can run. Silently dropping the unmapped overflow data (the
  only alternative to raising) risks losing real clinical data recorded in
  those extra cells; refusing and surfacing a clear error is the safer
  default for a file this important, same reasoning as refusing to write on
  an invalid `mas_grade`.
- **Normal case** (header exists, all rows parse cleanly): compute the
  widened header as existing fieldnames, in order, plus whichever
  `WIDENABLE_MAS_FIELDS` aren't already present, appended at the end — this
  is why `DEFAULT_MAS_FIELDS` in §6.1 lists `stronger_leg`/`notes` last, so
  a brand-new file and a widened old file end up with the same column
  order. Then rewrite atomically: write header + all existing rows (missing
  new-column values default to `""` via `DictWriter`'s standard `restval`)
  + the new row, to a temp file (`csv_path + ".tmp"`, always opened in `"w"`
  mode — a stale `.tmp` left over from an earlier crashed run is overwritten
  from scratch, not appended to), then `os.replace()` it into place — the
  same pattern `pendulastic_storage.save_trial` already uses. This is
  "atomic" in the narrow sense the existing codebase convention means it:
  `os.replace()` can't leave a half-written file at `csv_path` — either the
  pre-widen file or the fully-written widened one is there, never a partial
  one. It does **not** guard against concurrent writers (two processes
  saving at once) or an OS-level power-loss during the write before
  `fsync` — the same caveats already accepted for `pendulastic_storage`'s
  identical pattern, on the same reasoning: single-user desktop tool, not a
  multi-writer service.

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

- `notes` is optional and unvalidated free text — blank is valid, no format
  constraint (matches `condition`/`diagnosis`/`assessed_by`).
- `stronger_leg` is optional but validated: blank (`""`, "not assessed") is
  valid; any non-blank value must be `"left"`/`"right"`/`"equal"` or
  `append_mas_score()` raises `ValueError` before any file is touched — same
  guarantee `mas_grade` already has. In practice the readonly combobox
  prevents an invalid value from `MasEntryPanel`, but `append_mas_score()`
  is a public function other code can call directly, so it enforces this
  itself rather than trusting every caller's UI layer.
- `mas_grade` validation is unchanged and still runs first (existing
  behavior, §6.3 step 1).
- **Malformed existing CSV** (a data row with more fields than the header):
  widening refuses and raises `ValueError` naming the row and file, rather
  than silently dropping the unmapped data or guessing. The file is
  untouched — this is the same "refuse and surface, never guess" posture as
  an invalid grade.
- **Empty-but-existing CSV** (zero bytes, `fieldnames is None`): treated as
  no existing data to preserve — writes `DEFAULT_MAS_FIELDS` as the header
  and the new row as the only data row. Not an error.
- If the atomic rewrite's temp-file write fails partway (disk full, etc.),
  the exception propagates same as any other I/O error today — the
  original file at `csv_path` is untouched (the failure is on the `.tmp`
  file, before `os.replace`), and `MasEntryPanel._on_save_clicked`'s
  existing `except Exception as e: self._set_feedback(f"Could not save:
  {e}")` already surfaces this to the user rather than a dead click.
- **Not handled, by deliberate choice** (see §11 for the Codex findings this
  responds to): concurrent writers (two processes/threads calling
  `append_mas_score` on the same file at once) and `fsync`-level durability
  against power loss. Both are accepted gaps already present in
  `pendulastic_storage.save_trial`'s identical atomic-write pattern, for
  the same reason — this is a single-user desktop tool, not a multi-writer
  service.

## 9. Testing

### `tests/test_mas_validation.py`
- `test_append_mas_score_widens_header_when_row_has_new_fields` — starting
  CSV has the old 7-column header + 1 data row; append a row with
  `stronger_leg`/`notes`; assert the file's new header includes all 9
  columns in the documented order, the old row's new-column cells are
  `""`, and the new row's values round-trip correctly via `load_mas_scores`.
- `test_append_mas_score_widening_is_atomic_on_replace_failure` —
  monkeypatch `os.replace` to raise partway through; assert the original
  file at `csv_path` is unchanged.
- `test_append_mas_score_widening_is_atomic_on_write_failure` — monkeypatch
  the temp-file write itself (not just `os.replace`) to raise partway
  through writing rows; assert `csv_path` is unchanged and `os.replace` was
  never called. Covers the gap the single replace-only test left: a crash
  while writing rows, not just while swapping the finished file in.
- `test_append_mas_score_no_widen_when_row_keys_are_subset_of_header` — CSV
  already has all 9 columns; append a row with just the 9 keys; assert no
  `.tmp` file is created (i.e. the plain-append fast path ran, not a
  rewrite) — can check via `monkeypatch` spying on whether `os.replace` was
  called.
- `test_append_mas_score_ignores_unrecognized_keys_without_widening` — CSV
  has the old 7-column header; append a row containing an extra key not in
  `WIDENABLE_MAS_FIELDS` (e.g. a typo'd `"stronger_le"`) alongside the 7
  known ones; assert the header is unchanged (7 columns, no widen) and the
  typo'd key never appears anywhere in the file — proves the allowlist,
  not "any new key," gates widening.
- `test_append_mas_score_raises_on_malformed_existing_row` — starting CSV
  has a data row with more comma-separated values than the header has
  columns; append a row with `stronger_leg`; assert `ValueError` is raised
  (mentioning the malformed row) and the file is completely unchanged
  (byte-for-byte, not just row-count).
- `test_append_mas_score_widens_empty_file` — starting CSV is zero bytes
  (exists, per the already-covered missing-file case being a separate
  scenario, but has no header line); append a row; assert the file now has
  the `DEFAULT_MAS_FIELDS` header and exactly one data row.
- `test_append_mas_score_rejects_invalid_stronger_leg` — append a row with
  `stronger_leg="both"`; assert `ValueError`, no file created/modified.
- `test_default_mas_fields_includes_new_columns` — `DEFAULT_MAS_FIELDS[-2:]
  == ["stronger_leg", "notes"]`.
- `test_valid_stronger_leg_accepts_all_options_rejects_else` —
  `_valid_stronger_leg` accepts all 4 `STRONGER_LEG_OPTIONS` values,
  rejects `"Left"` (case), `"both"`, `None`.

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

## 11. Codex Review Disposition

A Codex second-opinion review of the pre-revision spec raised 12 findings.
Adopted (folded into §§6.2–6.3, 8, 9 above):

1. Malformed existing CSV (row with more fields than the header) could crash
   or silently drop data during widening — now explicitly refused with a
   `ValueError`, no rewrite attempted.
2. Widening on "any unrecognized row key" risked permanent schema
   corruption from a future typo — restricted to an explicit
   `WIDENABLE_MAS_FIELDS` allowlist.
3. Empty-but-existing CSV (`fieldnames is None`) was unhandled — now
   explicit (§6.3).
4. `stronger_leg` had no server-side validation, unlike the closed-enum
   field it actually is — added `_valid_stronger_leg()`, same pattern as
   `_valid_grade`.
5. The "atomic" test only covered `os.replace` failing, not a failure while
   writing rows — added a second test for that gap.

Considered and **not** adopted, with reasoning:

- **Concurrent-writer locking and `fsync`-level durability** — real gaps,
  but already-accepted ones: `pendulastic_storage.save_trial` has the
  identical atomic-write pattern with the same gaps, on the same reasoning
  (single-user desktop tool, not a multi-writer service). Fixing it here
  without fixing it there would be inconsistent for no real benefit.
- **`.strip()` "destroys intentional blank lines"** — overstated: `.strip()`
  only trims the start/end of the whole string, not interior blank lines.
  No change made.
- **`stronger_leg` carryover across participants (UI batching risk)** — a
  real UX consideration, but it's the same trade-off already deliberately
  made for `diagnosis`/`condition` in the original MAS entry spec (form
  intentionally not cleared between saves), not a new risk this feature
  introduces. No change made; noted here so a future reviewer doesn't
  re-raise it as if it were new.
- **No acceptance criterion for small-screen layout** — the app's window is
  already a fixed size elsewhere in this codebase; out of scope for this
  spec, not blocking.

2026-08-07 (post-implementation): a stale §6.3 parenthetical about
"DEFAULT_MAS_FIELDS plus any row keys not already in it ... future-proofing"
in the empty-file bullet was found to still sanction the exact bug finding
2 above (item 2 in this list) exists to prevent, and was in fact the literal
wording that caused Task 2's implementer to originally write `for k in row:`
instead of `for k in WIDENABLE_MAS_FIELDS:` — a real bug caught and fixed in
Task 2's review round. Struck. The empty-file branch is allowlist-gated
identically to the normal-widen path; see `mas_validation.py`'s
`append_mas_score()`.
