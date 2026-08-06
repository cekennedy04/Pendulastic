# MAS-vs-PT-Score Clinical Validation — Design Spec

**Status:** Approved
**Date:** 2026-08-06

---

## 1. Goal

Digitize the clinician MAS scores that currently exist only on paper, and validate
Pendulastic's computed PT score against them using literature-correct, ordinal-appropriate
statistics — kept explicitly separate from the existing device-vs-OptiTrack (continuous)
concurrent-validity analysis — producing a conference-ready figure and stats table.

## 2. Background / Why

- No clinician MAS ground truth is digitized anywhere in the project today.
  `pendulastic_pt_score.pt_to_mas()` only maps the computed PT score to a *predicted* MAS
  label via literature thresholds; there is currently nothing real to compare it against.
- Literature review (the Popovic 2018 source paper behind this project's 7-parameter PT
  score; Yeh et al. 2025's pose-estimation pendulum study; De Santis & Perez 2024, JNER)
  shows the field treats device-vs-reference-device validity (ICC/Pearson, continuous) and
  device-vs-clinician-MAS validity (Spearman's rho, weighted Cohen's kappa, ROC/AUC — MAS
  is ordinal) as two separate analyses, never conflated. Pendulastic's existing
  `control_validation_stats.csv` / `P5_concurrent_validity.csv` cover only the former.

## 3. Scope

- New: `mas_scores.csv` (repo root), `mas_validation.py` (cohort-level script),
  `MAS_ORDER` / `MAS_RANK` constants in `pendulastic_pt_score.py`.
- Small addition: an end-of-run nudge line in `run_pt_analysis.py`.
- Out of scope: any change to how PT scores themselves are computed; any new UI for MAS
  entry (explicitly deferred — backfill + CSV editing only); ROC/AUC is best-effort, shown
  only when class balance supports it.

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `mas_scores.csv` (new, repo root) | Source data: clinician MAS grades, participant × leg × condition. |
| `pendulastic_pt_score.py` | Add `MAS_ORDER`, `MAS_RANK` constants next to existing `_MAS` / `_MAS_COLOR`. |
| `mas_validation.py` (new) | Cohort-level validation script — load, join, compute stats, render figure. |
| `run_pt_analysis.py` | Append a nudge line at the end of `main()` if `mas_scores.csv` exists. |
| `tests/test_mas_validation.py` (new) | Unit tests for the pure functions: grade validation, rank mapping, stats computation, small-n flag. |

## 5. Data Schema — `mas_scores.csv`

Columns: `participant,leg,condition,mas_grade,assessed_by,assessed_date,notes`

- `participant`: matches the numeric-string participant id used throughout (`"13"`, not `"P13"`)
- `leg`: `left` | `right`
- `condition`: **free-text context only (e.g. a diagnosis note), not used for matching.**
  Original design assumed one row per participant/leg/session, matched against
  `pt_report_common._parse_trial_path()`'s derived condition string. Real backfilled data
  (2026-08-06) turned out to be one clinician MAS assessment per participant per leg, not
  per session — confirmed with the user. The join was changed accordingly: a MAS grade is
  matched against the mean PT score across *every* recorded condition/session for that
  leg (see `mas_validation._pt_lookup_factory()`), and `condition` is carried through only
  as provenance text.
- `mas_grade`: one of `MAS_ORDER = ["0", "1", "1+", "2", "3", "4"]`; anything else is an invalid row
- `assessed_by`, `assessed_date`: free text / ISO date, provenance only, not used in stats
- `notes`: optional free text

## 6. `pendulastic_pt_score.py` additions

```python
MAS_ORDER = ["0", "1", "1+", "2", "3", "4"]
MAS_RANK = {g: i for i, g in enumerate(MAS_ORDER)}   # 0..5, single source of truth
```

Placed immediately after the existing `_MAS` / `pt_to_mas()` definitions. `pt_to_mas()`
itself is unchanged — it already only ever returns a value in `MAS_ORDER`.

This is the single ordinal-coding source of truth for this feature: both Spearman's rho
and weighted Cohen's kappa read grades through `MAS_RANK`, rather than each statistic
defining its own numeric mapping (the existing `hpe_mas_evaluation.py` module has a
separate `"1+": 1.5`-style interval mapping for a different purpose — display coloring —
and is not reused here).

## 7. `mas_validation.py`

### 7.1 Pure functions (unit-testable, no I/O)

- `_valid_grade(grade: str) -> bool` — `grade in MAS_ORDER`
- `pair_pt_and_mas(mas_rows, pt_lookup) -> list[dict]` — for each `mas_rows` entry, look up
  the mean PT score for `(participant, leg)` via a supplied lookup callable (`condition` is
  passed through for signature symmetry but not used for matching — see §5); returns
  `{participant, leg, condition, mas_grade, pt_score, predicted_mas}` per row, skipping
  (with a caller-printed warning) any row with an invalid grade or a participant/leg with
  no recorded trials at all
- `_pt_lookup_factory()` — returns the `pt_lookup` callable above, backed by
  `pt_report_common.collect_participant()` (cached per participant); averages `pt7` across
  every `(leg, condition)` entry that matches the requested leg, ignoring `condition`
- `compute_validation_stats(pairs) -> dict`:
  - `n`
  - `spearman_rho`, `spearman_p` — `scipy.stats.spearmanr(pt_scores, [MAS_RANK[g] for g in mas_grades])`
  - `weighted_kappa` — `sklearn.metrics.cohen_kappa_score(actual_ranks, predicted_ranks, labels=list(range(6)), weights='linear')`
  - `per_grade` — `{grade: {median, iqr, n}}` for `pt_score`, ordered per `MAS_ORDER`
  - `preliminary: bool` — `True` if `n < 5`
  - `roc_auc` — computed (`sklearn.metrics.roc_auc_score`) only if both classes of the
    MAS≥1-vs-MAS==0 split have ≥3 observations each; otherwise `None`

### 7.2 I/O + plotting

- `load_mas_scores(csv_path) -> list[dict]` — reads the CSV, validates each row, prints one
  warning line per skipped row (bad grade, or a participant/leg with no recorded trials)
  naming the exact participant/leg, and continues (no exceptions for bad data)
- `main()`:
  1. If `mas_scores.csv` doesn't exist: print setup instructions (header + example row) and exit 0
  2. If it exists but has zero valid rows after loading: print "0 MAS-scored trials found" and exit 0
  3. Otherwise: load → pair → compute stats → write
     `Model_Analysis_Outputs/MAS_Validation/mas_validation_stats.csv` → render
     `mas_validation_figure.png`:
     - Panel 1: boxplot of PT score grouped by MAS grade, x-axis ordered per `MAS_ORDER`,
       annotated with `rho`, `p`, `n`
     - Panel 2: agreement heatmap, actual MAS grade × predicted MAS grade, annotated with
       weighted kappa
     - Panel 3 (only if `roc_auc is not None`): ROC curve for the MAS≥1-vs-MAS==0 split
  - Figure title states `n=X` plainly; appends "(preliminary — small n)" whenever
    `stats['preliminary']` is `True`

## 8. `run_pt_analysis.py` addition

At the end of `main()`: if `mas_scores.csv` exists, count distinct participants that have
both met the 4+4 trial threshold (already known from the existing loop) and have at least
one `mas_scores.csv` row, and print:

```
"N participant(s) now have both trial data and MAS scores on file -- run mas_validation.py to refresh the validation report."
```

No other behavior change; this is a printed nudge only, never an auto-invocation.

## 9. Error Handling

- Missing `mas_scores.csv` → clean exit with setup instructions, not a traceback
- Empty `mas_scores.csv` (header only) → clean exit, "0 MAS-scored trials found"
- Invalid `mas_grade` value → row skipped, warning printed naming the row
- `(participant, leg)` with no recorded trials at all (not yet processed, or a leg that
  was never captured) → row skipped, warning printed naming the row
- `n < 5` paired observations → stats still computed; figure/console explicitly flagged
  "(preliminary — small n)" rather than hidden
- ROC/AUC with insufficient class balance (< 3 per class) → that panel omitted, printed
  note explaining why, rest of the figure unaffected

## 10. Testing — `tests/test_mas_validation.py`

- `test_mas_rank_ordering` — `MAS_RANK` maps `"0"→0 … "4"→5`, `"1+"` sits between `"1"` and `"2"`
- `test_valid_grade` — accepts all six `MAS_ORDER` values, rejects anything else (`"5"`, `""`, `"1++"`)
- `test_pair_pt_and_mas_skips_invalid_grade` — a row with a bad grade is dropped, not raised
- `test_pair_pt_and_mas_skips_when_pt_lookup_returns_none` — a row whose pt_lookup call
  returns `None` (participant/leg has no recorded trials) is dropped, not raised
- `test_pt_lookup_aggregates_across_conditions_ignoring_condition_arg` — `_pt_lookup_factory()`
  averages `pt7` across every condition recorded for the requested leg, and the `condition`
  argument does not gate the match
- `test_pt_lookup_returns_none_when_leg_has_no_recorded_trials` — a leg absent from
  `collect_participant()`'s result returns `None`, not a `KeyError`
- `test_compute_validation_stats_known_values` — small synthetic fixture (5–8 hand-picked
  pairs) checked against hand-computed expected `spearman_rho` and `weighted_kappa`
- `test_compute_validation_stats_labels_full_set` — a sample missing an entire grade (e.g.
  no `"4"` present) still produces a kappa consistent with the full 6-category label set
  (regression test for the `labels=list(range(6))` requirement)
- `test_small_n_flag` — `n=4` → `preliminary=True`; `n=5` → `preliminary=False`
- `test_roc_auc_omitted_below_class_minimum` — unbalanced sample (e.g. 1 non-spastic) → `roc_auc is None`
- `test_main_missing_csv_no_crash` — `main()` with no `mas_scores.csv` present exits
  cleanly and prints setup instructions (`tmp_path` / `monkeypatch`, matching this repo's
  existing test conventions)

## 11. Out of Scope / Future

- ROC/AUC is best-effort, not central to this design; a proper power analysis for it, and
  ordinal logistic regression (a research finding: underused but arguably more correct in
  this literature than the methods above), are both deferred.
- Wiring MAS entry into a live UI (`pendulastic_app.py`) is explicitly deferred per user
  decision — CSV backfill + ongoing manual entry only, for now.
