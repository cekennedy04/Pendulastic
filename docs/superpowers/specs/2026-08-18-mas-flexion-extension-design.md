# MAS Flexion/Extension Scores — Design Spec

**Status:** Approved (brainstorming complete, Codex-reviewed, revised; pending final user
review of this document)
**Date:** 2026-08-18

## 1. Goal

Let the clinician record separate Modified Ashworth Scale (MAS) scores for flexion and
extension on the same MAS entry, and run an **exploratory** correlation between those
scores and the PT device's existing per-trial `spasticity_type` classification —
additively, alongside the current single-grade entry and validation pipeline, which
stay untouched.

## 2. Background

`MasEntryPanel` (`pendulastic_app.py`) currently captures one `mas_grade` per
(participant, leg, condition) assessment. `mas_validation.py` stores it in
`mas_scores.csv` and validates it against the device's PT score via
`pair_pt_and_mas()` → `compute_validation_stats()`, and `fit_mas_thresholds.py` fits
grade-boundary thresholds from the same pairs. `pt_report_common.py`'s
`clinician_mas_matches()`/`write_clinician_mas_sidecar()` surface the matched rows in
the full-report figure's Row 5 table.

Separately, `pendulastic_pt_score.py`'s `compute_pt_params()` already classifies every
individual trial's dominant swing asymmetry from the pendulum's phase-area imbalance
(`P_plus` vs `P_minus`, Popovic 2018 Fig 7), returning `spasticity_type` as
`"flexion"`, `"extension"`, or `"balanced"` — a field already present on every trial
record but never joined against clinician-entered MAS.

**Important scope caveat (added after Codex review):** `spasticity_type` classifies
which way a *single passive drop* leans, from one continuous swing. A clinician's
flexion and extension MAS scores instead come from **two separate manual
examinations** — passively flexing the joint, then separately extending it — at a
different point in the visit entirely. These are related but **not the same
measurement paradigm**: filtering trials by swing-asymmetry direction and comparing
the pooled PT score against the clinician's direction-specific manual grade is a
plausible **hypothesis to explore**, not an established clinical equivalence. This
spec (and any future report/dashboard surfacing these results) must present the
flexion/extension validation output as **exploratory correlation analysis**, never as
"the device's flexion/extension score has been validated against MAS" — that claim
would require the direction-specific pooling assumption to be checked against outcomes
first, which this pass does not attempt.

**Decisions locked in during brainstorming (revised after Codex review, see §8):**
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
  drawing, and once the exploratory-correlation premise above has been checked.
- "Balanced" trials are excluded from both directions' aggregation (they have no
  clear flexion or extension dominance to attribute a score to).
- The output of this analysis is **exploratory**, not a validated clinical claim (see
  above) — this governs the wording used anywhere these stats are surfaced.

## 3. Data Layer (`mas_validation.py`)

- **`DEFAULT_MAS_FIELDS` and `WIDENABLE_MAS_FIELDS`** both gain `"mas_flexion"` and
  `"mas_extension"` — mirroring exactly how `"stronger_leg"`/`"notes"` are already
  present in **both** lists (`DEFAULT_MAS_FIELDS` at lines 73-75, `WIDENABLE_MAS_FIELDS`
  at line 82), not just one. A brand-new `mas_scores.csv` gets the two new columns
  natively from `DEFAULT_MAS_FIELDS`; an existing narrower file still auto-widens via
  the unmodified `WIDENABLE_MAS_FIELDS` mechanism when a row carrying either key is
  saved. (Confirmed via Codex review: the prior draft of this spec only proposed
  `WIDENABLE_MAS_FIELDS`, which is inconsistent with the established two-list pattern
  and would make the fresh-file schema depend on incidental save timing.)
- **`append_mas_score(row, csv_path=MAS_CSV)`** gains two new optional keys on `row`:
  `mas_flexion`, `mas_extension`. Each is validated with the existing `_valid_grade()`
  **only when non-blank** — a blank value means "not assessed" and is always valid
  (distinct from an invalid non-blank value, which is still rejected exactly as
  `mas_grade` already is). A `row` dict that omits either key entirely is also valid —
  `csv.DictWriter`'s default `restval=""` already writes a blank cell for any fieldname
  missing from the row, so no new default-handling code is needed (the prior draft of
  this spec attributed this to a "row defaults to blank" mechanism that doesn't exist;
  it's `csv.DictWriter`'s existing behavior).
- **The write path itself is unchanged and remains as non-uniformly-atomic as it is
  today**: the fast path (header already has the needed columns) is a direct
  `open(..., "a")` append, not atomic; only the header-widening path routes through
  `_atomic_write_mas_csv()`. This spec doesn't change that split — it only adds two
  more possible columns to the same two paths. (The prior draft's "writes atomically
  ... exactly as today" line was imprecise about what "today" already does; corrected
  here.)
- **`_pt_lookup_factory(*, direction=None)`** — the existing factory (currently
  `_pt_lookup_factory()`, no parameters) gains a genuinely keyword-only `direction`
  parameter (the `*,` makes `_pt_lookup_factory("flexion")` a `TypeError` rather than
  silently accepting a positional argument no existing call site expects). `raise
  ValueError` immediately if `direction` is not one of `(None, "flexion",
  "extension")` — a typo like `direction="flexoin"` must fail loudly, not silently
  return "no data" for every lookup.
  - `direction=None` (the default) is byte-for-byte the existing behavior: pool every
    trial for the requested (participant, leg, condition) and return
    `float(np.mean([r["pt7"] for r in trials]))`, or `None` if no trials match.
  - `direction="flexion"` or `direction="extension"` additionally filters the pooled
    trials to `r.get("spasticity_type") == direction` before averaging — `.get()`, not
    `r["spasticity_type"]`, so a trial record from a source that doesn't carry the key
    (a defensive case, not expected on the live `collect_participant()` path today) is
    treated as "doesn't match this direction" rather than raising `KeyError`.
  - Still returns `None` (never `0.0`) when the filtered set is empty — callers must be
    able to distinguish "no direction-matching trial data" from "a real score of zero."
- **`pair_pt_and_mas_by_direction(mas_rows, pt_lookup_flexion, pt_lookup_extension)`** —
  new function. Returns `(flexion_records, extension_records)`, each built the same
  auditable way `pair_pt_and_mas()` already builds its one list (every considered row
  gets an entry — either a valid pair or a `_skip_reason` — rather than silently
  vanishing; the prior draft of this spec dropped unmatched rows instead of recording
  why, which loses exactly the auditability `pair_pt_and_mas()` was designed to keep).
  For each side (`mas_flexion` → `flexion_records`, `mas_extension` →
  `extension_records`) independently:
  - If the row's value for that side is blank ("not assessed"), the row contributes
    **no entry** to that side's list — this is the one case treated as "nothing to
    audit," since nothing was attempted.
  - If non-blank but not a valid grade, append `dict(row, _skip_reason=f"invalid
    mas_{side} {value!r} ...")` (mirrors `pair_pt_and_mas()`'s invalid-grade skip).
  - If valid and the direction-specific lookup returns `None` (no matching trial
    data), append `dict(row, _skip_reason="no matching {direction} trial data for
    this participant/leg/condition")`.
  - If valid and the lookup returns a score, append a **canonical pair record** using
    the exact key names `compute_validation_stats()` already requires —
    `mas_grade` (set to the direction-specific value, e.g. `row["mas_flexion"]`),
    `pt_score`, `predicted_mas` (via `pt.pt_to_mas(pt_score)`) — plus every other key
    already on `row`, plus `direction` (`"flexion"`/`"extension"`, bookkeeping only,
    ignored by `compute_validation_stats()`). **This is the load-bearing correction
    from Codex review:** `compute_validation_stats()` (lines 141-158) and
    `fit_mas_thresholds.py`'s fitting function both hard-index `p["mas_grade"]`,
    `p["pt_score"]`, and `p["predicted_mas"]` — they are not generic over arbitrary
    field names, contrary to the prior draft's claim. Emitting records under these
    exact canonical keys is what makes reuse-without-modification actually true.
    **Note the deliberate shadowing:** `row` already has its own `mas_grade` key (the
    overall single-value grade, unrelated to this analysis) — `dict(row, mas_grade=...,
    ...)` overwrites it in the *copied record only*. The original overall grade is
    never lost (it's still in `mas_scores.csv` and in every other consumer of the raw
    row); this derived record's `mas_grade` key means "the grade being validated in
    *this* direction's analysis," which is exactly what `compute_validation_stats()`
    needs it to mean.
- **`compute_validation_stats()`** and `fit_mas_thresholds.py`'s threshold-fitting
  function are **not modified** — the caller filters each direction's list to just the
  valid-pair entries (`[p for p in records if "_skip_reason" not in p]`, the same
  filter the existing single-grade pipeline already applies to `pair_pt_and_mas()`'s
  output) and invokes `compute_validation_stats()` once per direction, producing two
  independent stats results.

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
   and calls `_mas_validation.append_mas_score(row)`, which validates and writes to
   `mas_scores.csv` via the existing fast-path-append-or-widen-then-atomic-rewrite
   split (unchanged), just with two more possible columns.
3. An exploratory validation run (a script or future dashboard code, not built in this
   pass) calls `_pt_lookup_factory(direction="flexion")` and
   `_pt_lookup_factory(direction="extension")` to build two direction-specific
   lookups, passes both plus the loaded MAS rows into
   `pair_pt_and_mas_by_direction()`, filters each resulting list to its valid pairs,
   and runs `compute_validation_stats()` / `fit_mas_thresholds.py`'s fitting function
   once per direction. Any output is labeled exploratory per §2's caveat.
4. The existing single-grade pipeline (`pair_pt_and_mas`, its own `compute_validation_stats`
   call, the Row 5 report table) runs completely unaffected, reading the same
   `mas_scores.csv` file's unchanged `mas_grade` column.

## 6. Error Handling

- Invalid non-blank `mas_flexion`/`mas_extension` grade (at save time):
  rejected by `append_mas_score()`'s existing `_valid_grade()` check, same error path
  already used for `mas_grade`.
- Invalid non-blank `mas_flexion`/`mas_extension` grade (at pairing time, e.g. a row
  written by some other path that bypassed `append_mas_score`'s validation):
  `pair_pt_and_mas_by_direction()` emits a `_skip_reason` entry for that side, per §3.
- `_pt_lookup_factory(direction=...)` called with anything other than `None`,
  `"flexion"`, or `"extension"`: raises `ValueError` immediately.
- A trial record missing `spasticity_type` entirely: treated as not matching any
  specific `direction` filter (via `.get()`), never raises.
- No trials of a given direction for a (participant, leg, condition):
  `_pt_lookup_factory(direction=...)`'s lookup returns `None`; `pair_pt_and_mas_by_direction()`
  emits a `_skip_reason` entry for that side (see §3) rather than silently vanishing.
- Blank `mas_flexion`/`mas_extension` on a MAS row: treated as "not assessed" — no
  entry at all for that side, not a skip (see §3).

## 7. Known Limitations (explicitly out of scope for this pass)

- **Exploratory, not validated** (§2): the core premise — that swing-asymmetry
  direction is a usable proxy for manually-assessed flexor/extensor MAS — is untested
  by this pass. Any consumer of this data must present it as such.
- **Variable, unreported denominators:** the pooled trial count behind each
  direction-specific PT average isn't surfaced by `_pt_lookup_factory`'s `float|None`
  return contract (this pass doesn't change that contract, to avoid scope creep into a
  wider `_pt_lookup_factory`/`pair_pt_and_mas` return-shape redesign). A future pass
  that wants a minimum-trial-count policy or denominator reporting needs that redesign
  first.
- **No pseudo-replication guard:** `fit_mas_thresholds.py`'s `check_sample_sufficiency()`
  counts rows/grades, not distinct patients — a pre-existing limitation of the
  single-grade pipeline that this pass inherits rather than introduces. Splitting into
  two directions roughly doubles the row count drawn from the same patients without
  doubling independent information; worth flagging to whoever interprets the fitted
  thresholds, not solved here.
- **No new report/dashboard surface:** per the locked-in scope decision, this pass
  stops at data model + entry UI + validation-stats functions.

## 8. Testing

**Data layer** (`tests/test_mas_validation.py`, matching this file's existing
plain-function/`monkeypatch`/`tmp_path` convention):
- `append_mas_score()` round-trips `mas_flexion`/`mas_extension` through a fresh CSV
  (header includes both columns natively, from `DEFAULT_MAS_FIELDS`) and through an
  existing narrower CSV (header widens to include both, matching the existing
  `stronger_leg`/`notes` widening test's pattern).
- Blank `mas_flexion`/`mas_extension` is accepted; an invalid non-blank value for
  either is rejected the same way an invalid `mas_grade` already is.
- `_pt_lookup_factory(direction="flexion")` / `direction="extension")` filter pooled
  trials by `spasticity_type` before averaging `pt7`, verified against a hand-built
  trial list with mixed `spasticity_type` values, including a trial record with no
  `spasticity_type` key at all (must not raise, must not match either direction);
  `direction=None` still reproduces today's `_pt_lookup_factory()` behavior exactly
  (regression check).
- `_pt_lookup_factory(direction=...)` returns `None` (not `0.0`) when no trials match
  the requested direction for a given (participant, leg, condition).
- `_pt_lookup_factory(direction="bogus")` raises `ValueError`; `_pt_lookup_factory("flexion")`
  (positional) raises `TypeError` (keyword-only enforcement).
- `pair_pt_and_mas_by_direction()`:
  - A row with both `mas_flexion` and `mas_extension` set (and both PT-matchable)
    produces one valid pair in each of `flexion_records`/`extension_records`, each
    keyed `mas_grade`/`pt_score`/`predicted_mas`/`direction`.
  - A row with only one side set produces an entry only in that side's list; the
    other side contributes nothing (not even a skip entry).
  - A row with a non-blank but invalid `mas_flexion` produces a `_skip_reason` entry
    in `flexion_records`.
  - A row with a valid `mas_flexion` but no matching flexion-dominant trial data
    produces a `_skip_reason` entry in `flexion_records` (not silently dropped).
  - `compute_validation_stats()` runs unmodified against the valid-pair-filtered
    output of `pair_pt_and_mas_by_direction()` and produces the same shape of result
    it already produces for `pair_pt_and_mas()`'s output.

**UI** (`tests/test_app.py`, extending `MasEntryPanel`'s existing test conventions):
- The two new fields exist and default to blank.
- The full save-row field assertion (which currently checks the exact dict passed to
  `append_mas_score` field-by-field) is extended to include `mas_flexion`/
  `mas_extension`.
- Save clears `mas_flexion_var`/`mas_extension_var` alongside the existing
  `mas_grade_var`/`notes_text` clear, while `participant`/`leg`/`condition`/date
  remain — matching the existing batch-entry-preserving clear test's pattern.
- An invalid value assigned to either new field's `StringVar` and saved surfaces the
  same error path already tested for an invalid `mas_grade`. (Note: since both are
  readonly `ttk.Combobox` widgets, an operator can't actually type an invalid value
  through the UI — this test exercises the data-layer validation path directly via
  the `StringVar`, the same way the existing equivalent `mas_grade` test necessarily
  does for the same reason.)
