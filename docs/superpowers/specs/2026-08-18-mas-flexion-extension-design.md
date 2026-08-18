# MAS Flexion/Extension Scores — Design Spec

**Status:** Approved (brainstorming complete, pending user review of this document)
**Date:** 2026-08-18

## 1. Goal

Let the clinician record separate Modified Ashworth Scale (MAS) scores for flexion and
extension on the same MAS entry, and validate those scores against the PT device's
existing per-trial `spasticity_type` classification — additively, alongside the current
single-grade entry and validation pipeline, which stay untouched.

## 2. Background

`MasEntryPanel` (`pendulastic_app.py`) currently captures one `mas_grade` per
(participant, leg, condition, date) assessment. `mas_validation.py` stores it in
`mas_scores.csv` and validates it against the device's PT score via
`pair_pt_and_mas()` → `compute_validation_stats()`, and `fit_mas_thresholds.py` fits
grade-boundary thresholds from the same pairs. `pt_report_common.py`'s
`clinician_mas_matches()`/`write_clinician_mas_sidecar()` surface the matched rows in
the full-report figure's Row 5 table.

Separately, `pendulastic_pt_score.py`'s `compute_pt_params()` already classifies every
individual trial's dominant spasticity direction from the pendulum swing's phase-area
asymmetry (`P_plus` vs `P_minus`, Popovic 2018 Fig 7), returning `spasticity_type` as
`"flexion"`, `"extension"`, or `"balanced"` — a field already present on every trial
record but never joined against clinician-entered MAS, since MAS has always been a
single number. In real MAS practice, flexor and extensor muscle groups are frequently
scored separately, so this is a natural, currently-missing pairing.

**Decisions locked in during brainstorming:**
- New `mas_flexion`/`mas_extension` fields are **additive** — the existing `mas_grade`
  field, its CSV column, and its entire validation pipeline are not modified.
- Matching is **aggregate-per-assessment**, mirroring today's `pair_pt_and_mas`: each
  clinician assessment's direction-dominant trials are pooled and averaged, not
  matched trial-by-trial against the assessment.
- Flexion and extension get **separate validation-stats blocks and separate fitted
  threshold sets** — they are physiologically distinct measurements, not pooled.
- This pass is **data model + entry UI + validation-stats functions only** — no new
  report figure section. Visualizing flexion/extension validation in the report is
  explicitly deferred to a future pass once there's enough real data to be worth
  drawing.
- "Balanced" trials are excluded from both directions' aggregation (they have no
  clear flexion or extension dominance to attribute a score to).

## 3. Data Layer (`mas_validation.py`)

- **`WIDENABLE_MAS_FIELDS`** gains `"mas_flexion"` and `"mas_extension"`, alongside the
  existing `"stronger_leg"`/`"notes"` — `append_mas_score()`'s existing header-widening
  logic (unchanged) picks these up automatically for both fresh and pre-existing CSV
  files, exactly the way `stronger_leg`/`notes` were added previously.
- **`append_mas_score(row, csv_path=MAS_CSV)`** gains two new optional keys on `row`:
  `mas_flexion`, `mas_extension`. Each is validated with the existing `_valid_grade()`
  **only when non-blank** — a blank value means "not assessed" and is always valid
  (distinct from an invalid non-blank value, which is still rejected exactly as
  `mas_grade` already is). Missing keys on `row` default to `""`, matching the existing
  default-handling for `stronger_leg`/`notes`.
- **`_pt_lookup_factory(direction=None)`** — the existing factory (currently
  `_pt_lookup_factory()`, no parameters) gains an optional keyword-only `direction`
  parameter. `direction=None` (the default) is byte-for-byte the existing behavior:
  pool every trial for the requested (participant, leg, condition) and return
  `float(np.mean([r["pt7"] for r in trials]))`, or `None` if no trials match.
  `direction="flexion"` or `direction="extension"` additionally filters the pooled
  trials to `r["spasticity_type"] == direction` before averaging, still returning
  `None` (never `0.0`) when the filtered set is empty — callers must be able to
  distinguish "no direction-matching trial data" from "a real score of zero."
- **`pair_pt_and_mas_by_direction(mas_rows, pt_lookup_flexion, pt_lookup_extension)`** —
  new function, structurally mirroring `pair_pt_and_mas()`. For each row in
  `mas_rows`: if `row["mas_flexion"]` is non-blank, look it up against
  `pt_lookup_flexion(participant, leg, condition)`; if that returns a value (not
  `None`), emit a flexion pair. Same independently for `mas_extension` against
  `pt_lookup_extension`. A row can contribute zero, one, or two pairs. Returns two
  separate lists: `(flexion_pairs, extension_pairs)`, each in the same shape
  `pair_pt_and_mas()` already produces (consumed as-is by `compute_validation_stats()`).
- **`compute_validation_stats()`** and `fit_mas_thresholds.py`'s threshold-fitting
  function are **not modified** — both already operate generically on a list of pairs;
  the caller simply invokes each once per direction with `flexion_pairs` and
  `extension_pairs` respectively, producing two independent stats/threshold results.

## 4. UI (`pendulastic_app.py`'s `MasEntryPanel`)

- Two new fields, "MAS Flexion" and "MAS Extension", placed directly after the
  existing "MAS Grade" field in the form's row order. Same widget as `mas_grade_var`
  (readonly `ttk.Combobox` sourced from `_mas_validation.MAS_ORDER`), except each
  gains a leading blank `""` choice, since — unlike the required overall grade —
  recording a direction-specific score is optional per assessment.
- `self.mas_flexion_var` / `self.mas_extension_var`: new `tk.StringVar`s, default `""`.
- **Save handler (`_on_save_clicked`)**: required-field validation is unchanged
  (`participant` and `mas_grade` still required). The two new fields are read and
  passed through to `append_mas_score()`'s `row` dict as `mas_flexion`/`mas_extension`
  — blank stays blank, `_valid_grade()`'s existing rejection path (already surfaced as
  an error to the operator for `mas_grade`) naturally covers an invalid non-blank
  value for either new field too, since `append_mas_score()` validates all three the
  same way.
- **Clear-on-save**: the existing save handler already clears `mas_grade_var` and
  `notes_text` after a successful save (deliberately keeping participant/leg/
  condition/date for batch entry of both legs). `mas_flexion_var`/`mas_extension_var`
  are added to that same clear step — a fresh assessment shouldn't inherit the
  previous one's direction-specific scores by default.

## 5. Data Flow

1. Operator fills the form, optionally setting MAS Flexion and/or MAS Extension in
   addition to the required overall MAS Grade, and clicks Save.
2. `_on_save_clicked` builds the row dict (now including `mas_flexion`/`mas_extension`)
   and calls `_mas_validation.append_mas_score(row)`, which validates and atomically
   writes to `mas_scores.csv` (widening the header if needed) exactly as today, just
   with two more optional columns.
3. A validation run (a script or future dashboard code, not built in this pass) calls
   `_pt_lookup_factory(direction="flexion")` and `_pt_lookup_factory(direction="extension")`
   to build two direction-specific lookups, passes both plus the loaded MAS rows into
   `pair_pt_and_mas_by_direction()`, and runs `compute_validation_stats()` /
   `fit_mas_thresholds.py`'s fitting function once per resulting pair list.
4. The existing single-grade pipeline (`pair_pt_and_mas`, its own `compute_validation_stats`
   call, the Row 5 report table) runs completely unaffected, reading the same
   `mas_scores.csv` file's unchanged `mas_grade` column.

## 6. Error Handling

- Invalid non-blank `mas_flexion`/`mas_extension` grade: rejected by
  `append_mas_score()`'s existing `_valid_grade()` check, same error path already used
  for `mas_grade` — no new error-handling code needed, just extending which fields
  that check runs against.
- No trials of a given direction for a (participant, leg, condition):
  `_pt_lookup_factory(direction=...)`'s lookup returns `None`; `pair_pt_and_mas_by_direction()`
  skips emitting a pair for that side rather than emitting a pair with a missing PT
  value — mirrors `pair_pt_and_mas()`'s existing "skip rows with no PT match" behavior.
- Blank `mas_flexion`/`mas_extension` on a MAS row: treated as "not assessed," skipped
  by `pair_pt_and_mas_by_direction()` for that direction — not an error, just no pair
  for that side.

## 7. Testing

**Data layer** (`tests/test_mas_validation.py`, matching this file's existing
plain-function/`monkeypatch`/`tmp_path` convention):
- `append_mas_score()` round-trips `mas_flexion`/`mas_extension` through a fresh CSV
  (header includes both columns) and through an existing narrower CSV (header widens
  to include both, matching the existing `stronger_leg`/`notes` widening test's
  pattern).
- Blank `mas_flexion`/`mas_extension` is accepted; an invalid non-blank value for
  either is rejected the same way an invalid `mas_grade` already is.
- `_pt_lookup_factory(direction="flexion")` / `direction="extension")` filter pooled
  trials by `spasticity_type` before averaging `pt7`, verified against a hand-built
  trial list with mixed `spasticity_type` values; `direction=None` still reproduces
  today's `_pt_lookup_factory()` behavior exactly (regression check).
- `_pt_lookup_factory(direction=...)` returns `None` (not `0.0`) when no trials match
  the requested direction for a given (participant, leg, condition).
- `pair_pt_and_mas_by_direction()`: a row with both `mas_flexion` and `mas_extension`
  set produces both pairs; a row with only one set produces only that side's pair; a
  row where the direction-specific PT lookup returns `None` produces no pair for that
  side even though the MAS value was present; a blank `mas_flexion`/`mas_extension`
  never produces a pair for that side regardless of what the PT lookup would return.

**UI** (`tests/test_app.py`, extending `MasEntryPanel`'s existing test conventions):
- The two new fields exist and default to blank.
- The full save-row field assertion (which currently checks the exact dict passed to
  `append_mas_score` field-by-field) is extended to include `mas_flexion`/
  `mas_extension`.
- Save clears `mas_flexion_var`/`mas_extension_var` alongside the existing
  `mas_grade_var`/`notes_text` clear, while `participant`/`leg`/`condition`/date
  remain — matching the existing batch-entry-preserving clear test's pattern.
- An invalid value entered into either new field surfaces the same error path already
  tested for an invalid `mas_grade`.
