# MS-vs-Control Cohort Comparison — Design Spec

**Status:** Approved
**Date:** 2026-08-06

---

## 1. Goal

Extend `run_pt_analysis.py` so that, on every run, it also (re)generates a cohort-level
MS-vs-Control comparison — pulling in every participant who currently qualifies (same
4+4 trial threshold already used for individual reports) and has a known diagnosis group —
using the same 7-parameter Popovic PT score already driving the per-participant reports, so
the comparison stays numerically and visually consistent with everything else the pipeline
produces. As new participants are recorded and cross the threshold, they flow into their
arm of the comparison automatically.

## 2. Background / Why

- `run_pt_analysis.py` / `pt_report_common.py` already compute a 7-parameter PT score per
  trial and produce per-participant reports plus comparisons against two fixed reference
  participants (P5, P13). This is the numerically authoritative pipeline in the repo.
- A separate, disconnected script (`ms_vs_healthy_analysis.py`) already does an MS-vs-Healthy
  comparison, but: it uses a different (4-parameter) score, reads from a static CSV built by
  a third script (`pendulastic_pt_score.py`) rather than live trial discovery, uses a dark
  dashboard visual style that doesn't match the rest of the pipeline's output, and classifies
  participants via a numeric-pid fallback (`pid >= 4 → MS`) that doesn't match the actual
  dataset (e.g. archived controls are P6/7/8, identifiable only by a `_control` folder-name
  suffix, not by pid parity). It also lumps `"Stroke"` into the `"MS"` bucket.
- Only 2 of ~14 participants seen in the data (P13, P14) have a `metadata.json` with a
  diagnosis on file. `master_app.py`'s diagnosis field actually has four values: `"MS"`,
  `"Stroke"`, `"Unaffected Control"`, `"Other Motor Impairment"` — not a binary.
- Decided during design: build a new, consistent cohort pipeline on top of the existing
  7-parameter/`pt_report_common` machinery rather than extending the 4-parameter one. Leave
  `ms_vs_healthy_analysis.py` in place but untouched (out of scope; superseded going forward).

## 3. Scope

- New: `participant_groups.json` (repo root, hand-maintained registry for legacy
  participants), `pt_cohort_common.py` (classification, aggregation, stats, plotting).
- Small addition: one new call at the end of `run_pt_analysis.py main()`.
- Out of scope: any change to how the 7-param PT score itself is computed; any change to
  `ms_vs_healthy_analysis.py` or `pendulastic_pt_score.py`; any UI for entering diagnosis
  (already exists in `master_app.py`); mixed-effects/hierarchical modeling (participant-level
  median aggregation is the chosen fix for pseudoreplication — see §7.3 — a full
  mixed-effects model is a possible future upgrade, not required now).

## 4. File Impact Matrix

| File | Nature of change |
|---|---|
| `participant_groups.json` (new, repo root) | Hand-maintained registry: legacy pid → group, for participants without a `metadata.json` diagnosis. Starts empty. |
| `pt_report_common.py` | `TRIAL_THRESHOLD` and `leg_trial_counts()` moved here from `run_pt_analysis.py` (see §6.1) so both `run_pt_analysis.py` and `pt_cohort_common.py` share one definition of "qualifies" without a circular import. |
| `pt_cohort_common.py` (new) | Classification, cohort aggregation, stats, and the comparison figure/CSV. |
| `run_pt_analysis.py` | Imports `TRIAL_THRESHOLD`/`leg_trial_counts` from `pt_report_common` instead of defining them locally. One new call at the end of `main()`. |
| `tests/test_pt_cohort_common.py` (new) | Unit tests for the pure functions: classification priority, participant-level aggregation, stats. |

## 5. Data Schema — `participant_groups.json`

```json
{
  "6": "Control",
  "7": "Control"
}
```

- Keys: participant id as it appears elsewhere in the pipeline (`"6"`, not `"P6"`).
- Values: one of `"MS"`, `"Control"`, `"Stroke"`, `"Other Motor Impairment"` — same vocabulary
  as `master_app.py`'s diagnosis dropdown, so both sources feed the same classifier logic.
- File may not exist at all; treated as an empty registry, not an error (see §6.2, §8 for how
  this is distinguished from "file exists, this pid has no entry").

## 6. Determining who currently qualifies

### 6.1 Shared threshold logic (moved to `pt_report_common.py`)

`run_pt_analysis.py` today only computes its `qualified` set (participants meeting the 4+4
trial threshold) over whichever `pids` it was invoked with — all of them by default, but just
`[sys.argv[1]]` when called with a specific participant id (e.g. `run_pt_analysis.py 14`,
the documented per-participant trigger usage). The cohort comparison must NOT inherit that
narrowing: it needs to recompute the full qualifying set across every discoverable participant
on every run, regardless of which single pid (if any) the individual-report generation was
asked to focus on this time.

To do that without `pt_cohort_common.py` importing back from `run_pt_analysis.py` (circular),
`TRIAL_THRESHOLD` and `leg_trial_counts()` move from `run_pt_analysis.py` into
`pt_report_common.py` (both already import it). `run_pt_analysis.py`'s own behavior is
unchanged — it just references `common.TRIAL_THRESHOLD` / `common.leg_trial_counts()` instead
of module-local copies. `pt_cohort_common.py` uses the same two symbols, plus
`pt_report_common.list_participants()`, to independently compute the full current qualifying
set every time `run_cohort_comparison()` is called — it takes no pid-list argument.

### 6.2 Classification (`pt_cohort_common.classify_participant`)

Priority order, first match wins:

1. `Recordings/Participant_<pid>*/metadata.json` → `diagnosis` field, if present and non-empty.
2. `participant_groups.json` registry entry for that pid, if the file exists and contains it.
3. Neither → **unclassified**.

Both sources map through the same four-value vocabulary. Mapping to arms:

- `"MS"` → MS arm
- `"Unaffected Control"` (metadata.json spelling) / `"Control"` (registry spelling) → Control arm
- `"Stroke"`, `"Other Motor Impairment"` → **excluded** (flagged, not silently dropped — distinct
  from unclassified, since the diagnosis *is* known, it's just not part of this comparison)
- Unclassified is further split by cause for reporting purposes (see §8):
  - `registry_missing` — `participant_groups.json` doesn't exist on disk at all
  - `no_entry` — the file exists but has no key for this pid
- The `_control` (case-insensitive) folder-name convention seen in legacy `OptiTrack_Recordings`
  paths is used **only** as cosmetic suggestion text appended to a `no_entry` warning line
  (e.g. `"P6: no_entry (folder name suggests 'control')"`). It never determines classification
  and is never auto-written into the registry.

## 7. `pt_cohort_common.py`

### 7.1 Pure functions (unit-testable, no I/O beyond what's passed in)

- `classify_participant(pid, metadata_diagnosis, registry, registry_exists) -> (group, source)`
  — implements §6's priority order; `group` is one of `MS / Control / Excluded / Unclassified`,
  `source` is `metadata / registry / registry_missing / no_entry`.
- `aggregate_participant_summary(trials) -> dict | None` — given one participant/leg's list of
  scored trial records (as returned by `pt_report_common.collect_participant`), returns the
  **median** across trials for each of the 7 params + `pt7`. Returns `None` for an empty list.
  This is the fix for pseudoreplication (§7.3): cohort stats run on one row per participant per
  leg, not one row per trial.
- `cliffs_delta(a, b)`, `mann_whitney(a, b)`, `effect_label(d)` — same formulas as
  `ms_vs_healthy_analysis.py`'s existing helpers (that logic isn't in question, only its
  input granularity and where it lives); duplicated here rather than imported since the
  source module is a script, not a shared library, and this keeps `pt_cohort_common.py`
  self-contained.
- `compute_cohort_stats(ms_summaries, control_summaries) -> list[dict]` — one row per
  parameter (7 params + pt7) × leg: median/IQR per arm, Mann-Whitney p, Cliff's delta,
  effect label, `n_ms`, `n_control`. `n < 2` in either arm for a given parameter → stats
  fields `None`/`"n/a"`, not raised.

### 7.2 I/O + orchestration

- `load_registry() -> (dict, bool)` — reads `participant_groups.json`; returns `({}, False)`
  if the file doesn't exist (the `False` distinguishes this from an existing-but-empty file,
  needed for the `registry_missing` vs `no_entry` split).
- `load_metadata_diagnosis(pid) -> str | None` — reads `Recordings/Participant_<pid>*/metadata.json`
  (glob, same convention as `ms_vs_healthy_analysis._load_metadata_diagnosis`).
- `current_qualifying_participants() -> set[str]` — `pt_report_common.list_participants()`
  filtered through `pt_report_common.leg_trial_counts()` / `TRIAL_THRESHOLD` (§6.1). The full,
  independently-recomputed set — never the narrower list `run_pt_analysis.py` was invoked with.
- `run_cohort_comparison()` — **no arguments**, called once at the end of `run_pt_analysis.py main()`:
  1. `current_qualifying_participants()` (§6.1), then classify every one of them (§6.2).
  2. Print the composition banner (§8) and write `cohort_composition.csv` (§8) — unconditionally,
     even if the comparison itself ends up skipped.
  3. If `MS` arm or `Control` arm is empty: print
     `"Cohort comparison skipped: N MS / M Control qualifying participants (need >=1 in each arm)."`
     and return.
  4. Otherwise, for every pid in both arms: `pt_report_common.collect_participant(pid)` →
     `aggregate_participant_summary()` per leg → build `ms_summaries` / `control_summaries`.
  5. `compute_cohort_stats()` → write `ms_vs_control_stats.csv`.
  6. `make_cohort_comparison_figure()` → `ms_vs_control_boxplots.png`.

### 7.3 `make_cohort_comparison_figure()`

Light/clinical style matching `pt_report_common.py` (white background, same font/color
conventions, `ZONE_COLORS`/`ZONE_EDGES` reused for the PT-score panel). Layout: one row per
leg (left, right), one column per parameter (7 params + PT score) — box + strip, same visual
language as `ms_vs_healthy_analysis._plot_boxplots` (box plot color-coded by arm, individual
points jittered on top) but with two point layers per box:

- **Statistical layer**: one point per participant (the median from `aggregate_participant_summary`)
  — this is what the box, whiskers, Mann-Whitney p-value, and Cliff's delta annotation are
  computed from.
- **Descriptive layer**: all individual trial values, plotted lighter/smaller in the
  background, for transparency into within-participant spread. Not used for any statistic.

Figure caption states both counts explicitly, e.g.:
`"MS n=2 participants (7 trials) · Control n=3 participants (11 trials) · 4 excluded/unclassified — see cohort_composition.csv"`
— so the participant-vs-trial distinction that motivated §7.1's aggregation choice is visible
on the artifact itself, not just in this spec.

Output: `Model_Analysis_Outputs/MS_vs_Control/ms_vs_control_boxplots.png`.

## 8. Cohort composition reporting (audit trail, not console-only)

Every call to `run_cohort_comparison()` — regardless of whether the comparison figure itself
gets generated — writes `Model_Analysis_Outputs/MS_vs_Control/cohort_composition.csv`:

```
pid,group,source,n_trials_left,n_trials_right
13,MS,metadata,5,5
14,MS,metadata,4,4
6,Unclassified,registry_missing,4,4
9,Excluded,metadata,4,4
```

And prints a banner to console on every run (not just when something changes), e.g.:

```
==================== MS vs Control cohort ====================
MS:           13, 14                                (n=2)
Control:      (none yet)                             (n=0)
Excluded:     9 (Stroke)                              (n=1)
Unclassified: 6 (no_entry, folder suggests 'control')  (n=1)
              registry_missing: participant_groups.json not found --
              if you're in a worktree/isolated checkout, copy it over.
=================================================================
```

The `registry_missing` case gets its own distinct line (as shown) rather than being folded
into the generic unclassified list — a missing registry file is an environment/setup problem
worth calling out differently from "this specific participant just hasn't been added yet."

## 9. `run_pt_analysis.py` addition

At the end of `main()`, after the existing per-participant loop and the existing MAS nudge:

```python
import pt_cohort_common
pt_cohort_common.run_cohort_comparison()
```

Takes no arguments deliberately (§6.1) — it recomputes the full qualifying set itself rather
than reusing `main()`'s local `qualified`, which is scoped to whatever pid(s) this particular
invocation was asked to generate individual reports for. `run_pt_analysis.py`'s own
`TRIAL_THRESHOLD` / `leg_trial_counts` become thin references to `pt_report_common`'s copies
(§6.1) instead of local definitions; no other behavior change to the existing per-participant
/ per-reference-participant flow.

## 10. Error Handling

- `participant_groups.json` missing entirely → `registry_missing`, loud distinct warning (§8),
  not a crash.
- `participant_groups.json` present but malformed JSON → treated as missing (same as above),
  with an additional explicit "failed to parse, treating as empty" note.
- `metadata.json` missing, malformed, or has an empty/unrecognized `diagnosis` value → falls
  through to the registry step, same as if metadata.json didn't exist.
- Diagnosis value that doesn't match any of the four known vocabulary strings (typo, new value
  added to the dropdown later) → treated as `Unclassified`/`no_entry`-equivalent, printed by
  name, never guessed into an arm.
- Either arm empty after classification → comparison figure/stats skipped with an explicit
  printed reason; `cohort_composition.csv` still written.
- `n < 2` participants in an arm for a given parameter → that parameter's stats row is `n/a`,
  not raised; the box/points for that arm may still render with n=1 (no error bar).

## 11. Testing — `tests/test_pt_cohort_common.py`

- `test_classify_metadata_priority` — a pid with both a metadata diagnosis and a registry
  entry uses the metadata one.
- `test_classify_registry_fallback` — no metadata, registry has an entry → uses it.
- `test_classify_unclassified_no_entry` — registry file exists, no entry for this pid →
  `(Unclassified, no_entry)`.
- `test_classify_unclassified_registry_missing` — registry file doesn't exist at all →
  `(Unclassified, registry_missing)`.
- `test_classify_stroke_and_other_excluded` — `"Stroke"` and `"Other Motor Impairment"` both
  map to `Excluded`, not `MS`.
- `test_classify_unknown_vocabulary_value` — an unrecognized diagnosis string → `Unclassified`,
  not guessed into an arm.
- `test_aggregate_participant_summary_median` — known synthetic trial list → hand-computed
  median per param.
- `test_aggregate_participant_summary_empty_returns_none` — empty trial list → `None`.
- `test_compute_cohort_stats_known_values` — small synthetic fixture, hand-computed
  Mann-Whitney p and Cliff's delta.
- `test_compute_cohort_stats_small_n_is_na` — arm with 1 participant → that parameter's stats
  are `"n/a"`, not raised.
- `test_run_cohort_comparison_skips_when_arm_empty` — all qualifying participants classified
  as MS, zero Control → composition CSV still written, figure/stats generation skipped,
  confirmed via a printed message.
- `test_run_cohort_comparison_writes_composition_csv_always` — even in the skip case above,
  `cohort_composition.csv` exists and lists every qualifying pid with its group/source.

## 12. Out of Scope / Future

- Mixed-effects modeling instead of participant-level median aggregation — a more powerful
  option once per-arm n grows, deferred for now (median aggregation is the immediate,
  correct-enough fix for pseudoreplication given current small n).
- Backfilling `participant_groups.json` for every legacy participant — left to the user;
  the composition report/banner is the discovery mechanism, not an automated backfill.
- Any change to `ms_vs_healthy_analysis.py` (left running as-is, effectively superseded).
