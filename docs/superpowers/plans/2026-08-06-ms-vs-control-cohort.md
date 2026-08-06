# MS-vs-Control Cohort Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `run_pt_analysis.py` so every run also (re)generates a live MS-vs-Control cohort comparison, using the same 7-parameter Popovic PT score as the rest of the pipeline, growing automatically as new participants qualify.

**Architecture:** One new library module (`pt_cohort_common.py`) holds classification, participant-level statistical aggregation, and a light-themed comparison figure, built entirely on the existing `pt_report_common.collect_participant()`. `run_pt_analysis.py` gets one new call at the end of `main()`. `TRIAL_THRESHOLD`/`leg_trial_counts()` move from `run_pt_analysis.py` into `pt_report_common.py` so both modules share one "qualifies" definition without a circular import.

**Tech Stack:** Python, numpy, scipy.stats (`mannwhitneyu`), matplotlib (Agg backend, inherited from `pt_report_common.py`), pytest (`monkeypatch`/`tmp_path`/`capsys`, plain functions, no test classes — matches `tests/test_mas_validation.py`).

## Global Constraints

- Run tests and scripts with `.venv\Scripts\python.exe` (this repo's working environment; confirmed via `run_master.bat`).
- All numeric values written to `ms_vs_control_stats.csv` (medians, IQR, Mann-Whitney p, Cliff's delta) are rounded to 4 decimal places — matches the existing convention in `ms_vs_healthy_analysis.py` / `mas_validation.py`.
- Never call `matplotlib.use(...)` in `pt_cohort_common.py`. `pt_report_common.py` already resolves the backend (Agg for headless scripts, left alone when a Tk GUI is running) the moment it's imported, and `pt_cohort_common.py` always imports it first — calling `matplotlib.use()` again would fight that.
- `pt_report_common.py` is the single source of truth for `TRIAL_THRESHOLD` and `leg_trial_counts()` going forward (Task 1). `run_pt_analysis.py`'s own names become aliases, not copies.
- `pt_cohort_common.run_cohort_comparison()` takes **no arguments** — it always recomputes the full qualifying-participant set itself (`current_qualifying_participants()`), never the narrower list `run_pt_analysis.py main()` was invoked with. This is a deliberate fix for a bug caught during design review — see design spec §6.1.
- No diagnosis string is ever guessed into an arm. Unrecognized/typo'd diagnosis values and missing registry entries both fall through to `Unclassified`, never silently default to `MS` or `Control`.
- Reference: `docs/superpowers/specs/2026-08-06-ms-vs-control-cohort-design.md` (all section numbers below, e.g. "§7.1", refer to this file).

---

### Task 1: Move `TRIAL_THRESHOLD`/`leg_trial_counts()` into `pt_report_common.py`

**Files:**
- Modify: `pt_report_common.py` (insert after `list_participants()`, currently ending at line 198)
- Modify: `run_pt_analysis.py:32-46`
- Test: `tests/test_pt_report_common.py` (new)

**Interfaces:**
- Produces: `pt_report_common.TRIAL_THRESHOLD` (`int`, `4`), `pt_report_common.leg_trial_counts(participant_id: str) -> {"left": int, "right": int}`
- Consumed later by: `pt_cohort_common.py` (Task 5), and by `run_pt_analysis.py` itself via alias (unchanged call sites)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pt_report_common.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pt_report_common as common
import run_pt_analysis


def test_leg_trial_counts_sums_across_conditions_for_one_participant(monkeypatch):
    fake_records = [
        {"participant": "13", "leg": "right", "condition": "pre"},
        {"participant": "13", "leg": "right", "condition": "post"},
        {"participant": "13", "leg": "left", "condition": "pre"},
        {"participant": "14", "leg": "right", "condition": "pre"},
    ]
    monkeypatch.setattr(common, "discover_all_trials", lambda: fake_records)
    assert common.leg_trial_counts("13") == {"left": 1, "right": 2}


def test_leg_trial_counts_zero_for_unknown_participant(monkeypatch):
    monkeypatch.setattr(common, "discover_all_trials", lambda: [])
    assert common.leg_trial_counts("99") == {"left": 0, "right": 0}


def test_run_pt_analysis_trial_threshold_is_alias_of_common():
    assert run_pt_analysis.TRIAL_THRESHOLD == common.TRIAL_THRESHOLD


def test_run_pt_analysis_leg_trial_counts_is_common_function():
    assert run_pt_analysis.leg_trial_counts is common.leg_trial_counts
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -v`
Expected: FAIL — `AttributeError: module 'pt_report_common' has no attribute 'leg_trial_counts'` (or similar) on the first two tests; the alias tests fail too since `run_pt_analysis.TRIAL_THRESHOLD` still exists but isn't yet equal-by-identity/value-checked against a `common` attribute that doesn't exist yet.

- [ ] **Step 3: Move the code**

In `pt_report_common.py`, insert immediately after `list_participants()`'s closing `return dict(sorted(...))` line and before `def collect_participant(...)`:

```python
TRIAL_THRESHOLD = 4


def leg_trial_counts(participant_id):
    """Total recorded trials per leg for this participant, summed across
    every condition/session found (pre, post, side, control, etc.) -- not
    per-condition. A participant with 2 pre + 3 post right-leg trials counts
    as 5 right, matching TRIAL_THRESHOLD against the cumulative total.

    Moved here from run_pt_analysis.py (2026-08-06) so pt_cohort_common.py
    can independently recompute the full qualifying-participant set without
    importing back from run_pt_analysis.py -- see
    docs/superpowers/specs/2026-08-06-ms-vs-control-cohort-design.md, §6.1."""
    counts = {"left": 0, "right": 0}
    for r in discover_all_trials():
        if r["participant"] == participant_id and r["leg"] in counts:
            counts[r["leg"]] += 1
    return counts
```

In `run_pt_analysis.py`, replace lines 32-46:

```python
TRIAL_THRESHOLD = 4
REFERENCE_PARTICIPANTS = ("5", "13")
MAS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mas_scores.csv")


def leg_trial_counts(participant_id):
    """Total recorded trials per leg for this participant, summed across
    every condition/session found (pre, post, side, control, etc.) -- not
    per-condition. A participant with 2 pre + 3 post right-leg trials counts
    as 5 right, matching TRIAL_THRESHOLD against the cumulative total."""
    counts = {"left": 0, "right": 0}
    for r in common.discover_all_trials():
        if r["participant"] == participant_id and r["leg"] in counts:
            counts[r["leg"]] += 1
    return counts
```

with:

```python
TRIAL_THRESHOLD = common.TRIAL_THRESHOLD          # alias -- pt_report_common.py is now the source of truth
REFERENCE_PARTICIPANTS = ("5", "13")
MAS_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mas_scores.csv")

leg_trial_counts = common.leg_trial_counts        # alias, see TRIAL_THRESHOLD above
```

No other lines in `run_pt_analysis.py` change — every existing call site (`leg_trial_counts(pid)`, `TRIAL_THRESHOLD` in `run_for_participant()` and `main()`) keeps working unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add pt_report_common.py run_pt_analysis.py tests/test_pt_report_common.py
git commit -m "refactor: move TRIAL_THRESHOLD/leg_trial_counts into pt_report_common.py"
```

---

### Task 2: `pt_cohort_common.py` scaffold — classification + registry/metadata I/O

**Files:**
- Create: `participant_groups.json` (repo root)
- Create: `pt_cohort_common.py`
- Test: `tests/test_pt_cohort_common.py` (new)

**Interfaces:**
- Consumes: nothing from Task 1 yet (this task's functions don't need `TRIAL_THRESHOLD`)
- Produces:
  - `pt_cohort_common.REGISTRY_JSON`, `pt_cohort_common.REC_ROOT` (str paths)
  - `pt_cohort_common._SCORE_KEYS` (list of 8 strings: 7 PT params + `"pt7"`)
  - `pt_cohort_common._LEGS` (`("left", "right")`)
  - `pt_cohort_common.classify_participant(pid: str, metadata_diagnosis: str|None, registry: dict, registry_exists: bool) -> (group: str, source: str)`
  - `pt_cohort_common.load_registry() -> (dict, bool)`
  - `pt_cohort_common.load_metadata_diagnosis(pid: str) -> str|None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pt_cohort_common.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json

import pt_cohort_common as pcc


# ── classify_participant ─────────────────────────────────────────────────

def test_classify_metadata_ms():
    assert pcc.classify_participant("13", "MS", {}, True) == ("MS", "metadata")


def test_classify_metadata_unaffected_control():
    assert pcc.classify_participant("6", "Unaffected Control", {}, True) == ("Control", "metadata")


def test_classify_metadata_stroke_and_other_are_excluded():
    assert pcc.classify_participant("9", "Stroke", {}, True) == ("Excluded", "metadata")
    assert pcc.classify_participant("10", "Other Motor Impairment", {}, True) == ("Excluded", "metadata")


def test_classify_metadata_priority_over_registry():
    registry = {"13": "Control"}   # deliberately conflicting with metadata
    assert pcc.classify_participant("13", "MS", registry, True) == ("MS", "metadata")


def test_classify_registry_fallback_when_no_metadata():
    registry = {"6": "Control"}
    assert pcc.classify_participant("6", None, registry, True) == ("Control", "registry")


def test_classify_unrecognized_metadata_falls_through_to_registry():
    registry = {"6": "Control"}
    assert pcc.classify_participant("6", "Not A Real Diagnosis", registry, True) == ("Control", "registry")


def test_classify_unclassified_no_entry():
    assert pcc.classify_participant("6", None, {}, True) == ("Unclassified", "no_entry")


def test_classify_unclassified_registry_missing():
    assert pcc.classify_participant("6", None, {}, False) == ("Unclassified", "registry_missing")


def test_classify_registry_missing_wins_even_with_unrecognized_metadata():
    assert pcc.classify_participant("6", "typo diagnosis", {}, False) == ("Unclassified", "registry_missing")


def test_classify_case_insensitive_matching():
    assert pcc.classify_participant("13", "ms", {}, True) == ("MS", "metadata")
    assert pcc.classify_participant("6", None, {"6": "control"}, True) == ("Control", "registry")


# ── load_registry ────────────────────────────────────────────────────────

def test_load_registry_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(tmp_path / "does_not_exist.json"))
    assert pcc.load_registry() == ({}, False)


def test_load_registry_reads_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "participant_groups.json"
    path.write_text(json.dumps({"6": "Control"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(path))
    assert pcc.load_registry() == ({"6": "Control"}, True)


def test_load_registry_malformed_json_treated_as_missing(tmp_path, monkeypatch, capsys):
    path = tmp_path / "participant_groups.json"
    path.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(pcc, "REGISTRY_JSON", str(path))
    assert pcc.load_registry() == ({}, False)
    assert "failed to parse" in capsys.readouterr().out


# ── load_metadata_diagnosis ─────────────────────────────────────────────

def test_load_metadata_diagnosis_reads_diagnosis_field(tmp_path, monkeypatch):
    rec_root = tmp_path / "Recordings"
    (rec_root / "Participant_13").mkdir(parents=True)
    (rec_root / "Participant_13" / "metadata.json").write_text(
        json.dumps({"participant_id": "13", "diagnosis": "MS"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    assert pcc.load_metadata_diagnosis("13") == "MS"


def test_load_metadata_diagnosis_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pcc, "REC_ROOT", str(tmp_path / "Recordings"))
    assert pcc.load_metadata_diagnosis("13") is None


def test_load_metadata_diagnosis_prefix_collision_is_rejected(tmp_path, monkeypatch):
    # "Participant_13*" glob-matches "Participant_130" too -- must not
    # mistake participant 130's metadata for participant 13's.
    rec_root = tmp_path / "Recordings"
    (rec_root / "Participant_130").mkdir(parents=True)
    (rec_root / "Participant_130" / "metadata.json").write_text(
        json.dumps({"participant_id": "130", "diagnosis": "MS"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    assert pcc.load_metadata_diagnosis("13") is None


def test_load_metadata_diagnosis_checks_multiple_matching_folders(tmp_path, monkeypatch):
    # Real convention: Participant_13 AND Participant_13_right_post can
    # both exist; metadata.json only needs to be found in one of them.
    rec_root = tmp_path / "Recordings"
    (rec_root / "Participant_13_right_post").mkdir(parents=True)
    (rec_root / "Participant_13_right_post" / "metadata.json").write_text(
        json.dumps({"participant_id": "13", "diagnosis": "MS"}), encoding="utf-8")
    monkeypatch.setattr(pcc, "REC_ROOT", str(rec_root))
    assert pcc.load_metadata_diagnosis("13") == "MS"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pt_cohort_common'`

- [ ] **Step 3: Create `participant_groups.json`**

```json
{}
```

- [ ] **Step 4: Create `pt_cohort_common.py`**

```python
"""
pt_cohort_common.py
====================
MS-vs-Control cohort comparison, built on top of pt_report_common.py's
7-parameter Popovic PT score so it stays numerically and visually
consistent with every per-participant report run_pt_analysis.py produces --
unlike the older, disconnected ms_vs_healthy_analysis.py (4-parameter
score, static CSV, different visual style), which this supersedes for
MS-vs-Control purposes without modifying it.

See docs/superpowers/specs/2026-08-06-ms-vs-control-cohort-design.md for
the full design. Called from run_pt_analysis.py's main(); not run
standalone.
"""
from __future__ import annotations

import glob
import json
import os

import pt_report_common as common

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_JSON = os.path.join(BASE_DIR, "participant_groups.json")
REC_ROOT = os.path.join(BASE_DIR, "Recordings")
OUT_DIR = os.path.join(BASE_DIR, "Model_Analysis_Outputs", "MS_vs_Control")
COMPOSITION_CSV = os.path.join(OUT_DIR, "cohort_composition.csv")
STATS_CSV = os.path.join(OUT_DIR, "ms_vs_control_stats.csv")
FIGURE_PNG = os.path.join(OUT_DIR, "ms_vs_control_boxplots.png")

_PARAM_KEYS = common._PARAM_KEYS  # R2n, N, phi_max_ratio, omega_max_n, omega_min_n, f, area_ratio
_SCORE_KEYS = _PARAM_KEYS + ["pt7"]
_LEGS = ("left", "right")

# Same vocabulary as master_app.py's diagnosis dropdown (MS / Stroke /
# Unaffected Control / Other Motor Impairment) plus participant_groups.json's
# shorter "Control" spelling. Matched case-insensitively. Anything not in
# this map is treated as "not present" (falls through metadata -> registry
# -> unclassified), never guessed into an arm.
_DIAGNOSIS_TO_ARM = {
    "ms": "MS",
    "unaffected control": "Control",
    "control": "Control",
    "stroke": "Excluded",
    "other motor impairment": "Excluded",
}


# ══════════════════════════════════════════════════════════════════════════
# Pure functions (unit-testable, no I/O)
# ══════════════════════════════════════════════════════════════════════════

def classify_participant(pid, metadata_diagnosis, registry, registry_exists):
    """Priority: metadata.json diagnosis, then participant_groups.json
    entry, then unclassified (design spec §6.2). Returns (group, source):
      group  -- "MS" | "Control" | "Excluded" | "Unclassified"
      source -- "metadata" | "registry" | "no_entry" | "registry_missing"

    An unrecognized diagnosis string (typo, or a value not yet in
    _DIAGNOSIS_TO_ARM) is treated the same as "not present" and falls
    through to the next source, rather than being guessed into an arm."""
    if metadata_diagnosis:
        arm = _DIAGNOSIS_TO_ARM.get(metadata_diagnosis.strip().lower())
        if arm:
            return arm, "metadata"
    if not registry_exists:
        return "Unclassified", "registry_missing"
    entry = registry.get(pid)
    if entry:
        arm = _DIAGNOSIS_TO_ARM.get(entry.strip().lower())
        if arm:
            return arm, "registry"
    return "Unclassified", "no_entry"


# ══════════════════════════════════════════════════════════════════════════
# I/O
# ══════════════════════════════════════════════════════════════════════════

def load_registry():
    """Returns (dict, exists). Missing file -> ({}, False). Malformed JSON
    -> ({}, False) too (printed note), treated the same as missing rather
    than raising -- a corrupt registry shouldn't take down run_pt_analysis.py."""
    if not os.path.isfile(REGISTRY_JSON):
        return {}, False
    try:
        with open(REGISTRY_JSON, encoding="utf-8") as f:
            return json.load(f), True
    except (json.JSONDecodeError, OSError):
        print(f"{REGISTRY_JSON} failed to parse -- treating as empty.")
        return {}, False


def load_metadata_diagnosis(pid):
    """Recordings/Participant_<pid>*/metadata.json -> diagnosis field, or
    None if nothing matches. The glob pattern alone can over-match (e.g.
    "Participant_13*" also matches "Participant_130"), so every
    candidate's own participant_id field must equal `pid` exactly before
    its diagnosis is used. Multiple real folders can exist for one
    participant (e.g. Participant_13 and Participant_13_right_post); the
    first with a non-empty diagnosis wins."""
    pattern = os.path.join(REC_ROOT, f"Participant_{pid}*", "metadata.json")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if str(meta.get("participant_id", "")).strip() != pid:
            continue
        diagnosis = str(meta.get("diagnosis", "")).strip()
        if diagnosis:
            return diagnosis
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -v`
Expected: PASS (19 tests)

- [ ] **Step 6: Commit**

```bash
git add participant_groups.json pt_cohort_common.py tests/test_pt_cohort_common.py
git commit -m "feat: add MS-vs-Control classification (metadata.json + registry priority)"
```

---

### Task 3: `aggregate_participant_summary()`

**Files:**
- Modify: `pt_cohort_common.py` (add `import numpy as np`; add function under "Pure functions")
- Test: `tests/test_pt_cohort_common.py` (append)

**Interfaces:**
- Consumes: `_SCORE_KEYS` (Task 2)
- Produces: `pt_cohort_common.aggregate_participant_summary(trials: list[dict]) -> dict|None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_cohort_common.py`:

```python
# ── aggregate_participant_summary ───────────────────────────────────────

def _trial(**overrides):
    base = {k: 1.0 for k in pcc._SCORE_KEYS}
    base.update(overrides)
    return base


def test_aggregate_participant_summary_empty_returns_none():
    assert pcc.aggregate_participant_summary([]) is None


def test_aggregate_participant_summary_odd_count_median():
    trials = [_trial(pt7=1.0), _trial(pt7=2.0), _trial(pt7=3.0)]
    assert pcc.aggregate_participant_summary(trials)["pt7"] == 2.0


def test_aggregate_participant_summary_even_count_median_interpolates():
    trials = [_trial(pt7=1.0), _trial(pt7=2.0), _trial(pt7=3.0), _trial(pt7=4.0)]
    assert pcc.aggregate_participant_summary(trials)["pt7"] == 2.5


def test_aggregate_participant_summary_rounds_to_four_decimals():
    trials = [_trial(pt7=1.0 / 3)] * 3
    assert pcc.aggregate_participant_summary(trials)["pt7"] == round(1.0 / 3, 4)


def test_aggregate_participant_summary_covers_all_score_keys():
    trials = [_trial(R2n=0.5, N=8.0), _trial(R2n=0.7, N=9.0)]
    summary = pcc.aggregate_participant_summary(trials)
    assert set(summary.keys()) == set(pcc._SCORE_KEYS)
    assert summary["R2n"] == 0.6
    assert summary["N"] == 8.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k aggregate_participant_summary -v`
Expected: FAIL — `AttributeError: module 'pt_cohort_common' has no attribute 'aggregate_participant_summary'`

- [ ] **Step 3: Implement**

Add `import numpy as np` to the top of `pt_cohort_common.py` (alongside the existing `import os`), then append under the "Pure functions" section:

```python
def aggregate_participant_summary(trials):
    """trials: one participant/leg's list of scored trial records (each a
    dict with at least the _SCORE_KEYS), as returned by
    pt_report_common.collect_participant(). Returns the median across
    trials for each of the 7 PT params + pt7, rounded to 4 decimal places
    (matching this repo's existing stats-CSV rounding convention). Returns
    None for an empty list -- callers must handle that: a participant can
    pass the raw TRIAL_THRESHOLD gate (pt_report_common.leg_trial_counts)
    yet still summarize to None here if every discovered trial failed to
    score (pt_report_common.score_trial already returns None upstream for
    trials with no clean release/oscillation). An even trial count makes
    np.median interpolate between the two middle values -- expected, not
    a bug."""
    if not trials:
        return None
    return {key: round(float(np.median([t[key] for t in trials])), 4) for key in _SCORE_KEYS}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k aggregate_participant_summary -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add pt_cohort_common.py tests/test_pt_cohort_common.py
git commit -m "feat: add participant-level median aggregation (avoids trial-level pseudoreplication)"
```

---

### Task 4: Stats helpers + `compute_cohort_stats()`

**Files:**
- Modify: `pt_cohort_common.py` (add `math`, `warnings`, `scipy.stats.mannwhitneyu` imports; add 4 functions)
- Test: `tests/test_pt_cohort_common.py` (append)

**Interfaces:**
- Consumes: `_SCORE_KEYS`, `_LEGS` (Task 2)
- Produces:
  - `pt_cohort_common.cliffs_delta(a, b) -> float`
  - `pt_cohort_common.mann_whitney(a, b) -> (float, float)`
  - `pt_cohort_common.effect_label(d: float) -> str`
  - `pt_cohort_common.compute_cohort_stats(ms_summaries: {"left": list[dict], "right": list[dict]}, control_summaries: same shape) -> list[dict]` — each row has keys `leg, parameter, n_ms, n_control, ms_median, ms_iqr, control_median, control_iqr, mann_whitney_p, cliffs_delta, effect_size`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_cohort_common.py`:

```python
import math
import pytest


# ── cliffs_delta / mann_whitney / effect_label ──────────────────────────

def test_cliffs_delta_all_b_greater():
    assert pcc.cliffs_delta([1, 2, 3], [4, 5, 6]) == 1.0


def test_cliffs_delta_all_a_greater():
    assert pcc.cliffs_delta([4, 5, 6], [1, 2, 3]) == -1.0


def test_cliffs_delta_identical_distributions_is_zero():
    assert pcc.cliffs_delta([1, 2, 3], [1, 2, 3]) == 0.0


def test_cliffs_delta_empty_input_is_nan():
    assert math.isnan(pcc.cliffs_delta([], [1, 2]))


def test_mann_whitney_below_min_n_returns_nan():
    stat, p = pcc.mann_whitney([1.0], [2.0, 3.0])
    assert math.isnan(stat) and math.isnan(p)


def test_mann_whitney_computes_p_value():
    stat, p = pcc.mann_whitney([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
    assert 0.0 <= p <= 1.0


@pytest.mark.parametrize("d,label", [(0.05, "negligible"), (0.2, "small"),
                                     (0.4, "medium"), (0.9, "large")])
def test_effect_label_thresholds(d, label):
    assert pcc.effect_label(d) == label


def test_effect_label_nan_is_na():
    assert pcc.effect_label(float("nan")) == "n/a"


# ── compute_cohort_stats ─────────────────────────────────────────────────

def _summary(pt7):
    d = {k: 1.0 for k in pcc._SCORE_KEYS}
    d["pt7"] = pt7
    return d


def test_compute_cohort_stats_known_values():
    ms = {"left": [_summary(1.0), _summary(2.0), _summary(3.0)], "right": []}
    control = {"left": [_summary(10.0), _summary(11.0), _summary(12.0)], "right": []}
    rows = pcc.compute_cohort_stats(ms, control)
    row = next(r for r in rows if r["leg"] == "left" and r["parameter"] == "pt7")
    assert row["n_ms"] == 3 and row["n_control"] == 3
    assert row["ms_median"] == 2.0
    assert row["control_median"] == 11.0
    assert row["cliffs_delta"] == 1.0
    assert row["effect_size"] == "large"
    assert row["mann_whitney_p"] is not None


def test_compute_cohort_stats_small_n_is_na():
    ms = {"left": [_summary(1.0)], "right": []}
    control = {"left": [_summary(10.0), _summary(11.0)], "right": []}
    rows = pcc.compute_cohort_stats(ms, control)
    row = next(r for r in rows if r["leg"] == "left" and r["parameter"] == "pt7")
    assert row["n_ms"] == 1
    assert row["mann_whitney_p"] is None
    assert row["cliffs_delta"] is None
    assert row["effect_size"] == "n/a"
    assert row["ms_median"] == 1.0   # still reported -- just no significance test


def test_compute_cohort_stats_covers_every_leg_and_score_key():
    ms = {"left": [_summary(1.0)], "right": [_summary(2.0)]}
    control = {"left": [_summary(3.0)], "right": [_summary(4.0)]}
    rows = pcc.compute_cohort_stats(ms, control)
    seen = {(r["leg"], r["parameter"]) for r in rows}
    assert seen == {(leg, key) for leg in pcc._LEGS for key in pcc._SCORE_KEYS}


def test_compute_cohort_stats_empty_arm_no_crash():
    ms = {"left": [], "right": []}
    control = {"left": [_summary(1.0), _summary(2.0)], "right": []}
    rows = pcc.compute_cohort_stats(ms, control)
    row = next(r for r in rows if r["leg"] == "left" and r["parameter"] == "pt7")
    assert row["n_ms"] == 0
    assert row["ms_median"] is None
    assert row["mann_whitney_p"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k "cliffs_delta or mann_whitney or effect_label or compute_cohort_stats" -v`
Expected: FAIL — `AttributeError` on each missing function

- [ ] **Step 3: Implement**

Add to the top of `pt_cohort_common.py`: `import math` and `import warnings` (with the other stdlib imports), and `from scipy.stats import mannwhitneyu` (with `import numpy as np`). Then append under "Pure functions":

```python
def cliffs_delta(a, b):
    """Proportion of (b > a) pairs minus (a > b) pairs, -1..+1. Ported from
    ms_vs_healthy_analysis.py's helper of the same name -- the formula
    isn't in question, only its input granularity (see
    aggregate_participant_summary) and which module owns it."""
    n = len(a) * len(b)
    if n == 0:
        return float("nan")
    pairs = sum((1 if bi > ai else (-1 if ai > bi else 0)) for ai in a for bi in b)
    return pairs / n


def mann_whitney(a, b):
    """Two-sided Mann-Whitney U. Returns (nan, nan) if either sample has
    fewer than 2 values -- the design spec treats that as "n/a", not an
    error (§7.1)."""
    if len(a) < 2 or len(b) < 2:
        return float("nan"), float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        stat, p = mannwhitneyu(a, b, alternative="two-sided")
    return float(stat), float(p)


def effect_label(d):
    ad = abs(d)
    if math.isnan(ad):
        return "n/a"
    if ad < 0.147:
        return "negligible"
    if ad < 0.330:
        return "small"
    if ad < 0.474:
        return "medium"
    return "large"


def compute_cohort_stats(ms_summaries, control_summaries):
    """ms_summaries/control_summaries: {"left": [...], "right": [...]},
    each a list of per-participant summary dicts (aggregate_participant_
    summary() output, already filtered of None -- see run_cohort_comparison).
    Returns one row per (leg, parameter) covering every _SCORE_KEYS entry:
    median/IQR per arm, Mann-Whitney p, Cliff's delta, effect label,
    n_ms, n_control. Whenever either arm has fewer than 2 values for a
    given leg/parameter, the significance-test fields are None/"n/a" --
    never raised -- while the medians (even from n=1 or n=0) are still
    reported."""
    rows = []
    for leg in _LEGS:
        ms_leg = ms_summaries.get(leg, [])
        ctrl_leg = control_summaries.get(leg, [])
        for key in _SCORE_KEYS:
            ms_vals = np.array([s[key] for s in ms_leg], dtype=float)
            ctrl_vals = np.array([s[key] for s in ctrl_leg], dtype=float)
            row = {"leg": leg, "parameter": key,
                  "n_ms": len(ms_vals), "n_control": len(ctrl_vals)}
            if len(ms_vals):
                q1, q3 = np.percentile(ms_vals, [25, 75])
                row["ms_median"] = round(float(np.median(ms_vals)), 4)
                row["ms_iqr"] = round(float(q3 - q1), 4)
            else:
                row["ms_median"] = row["ms_iqr"] = None
            if len(ctrl_vals):
                q1, q3 = np.percentile(ctrl_vals, [25, 75])
                row["control_median"] = round(float(np.median(ctrl_vals)), 4)
                row["control_iqr"] = round(float(q3 - q1), 4)
            else:
                row["control_median"] = row["control_iqr"] = None
            if len(ms_vals) >= 2 and len(ctrl_vals) >= 2:
                _, p = mann_whitney(ms_vals, ctrl_vals)
                d = cliffs_delta(ms_vals, ctrl_vals)
                row["mann_whitney_p"] = round(p, 4)
                row["cliffs_delta"] = round(d, 4)
                row["effect_size"] = effect_label(d)
            else:
                row["mann_whitney_p"] = row["cliffs_delta"] = None
                row["effect_size"] = "n/a"
            rows.append(row)
    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k "cliffs_delta or mann_whitney or effect_label or compute_cohort_stats" -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add pt_cohort_common.py tests/test_pt_cohort_common.py
git commit -m "feat: add cohort Mann-Whitney/Cliff's delta stats at participant-level granularity"
```

---

### Task 5: Qualifying-participant discovery + composition CSV/banner

**Files:**
- Modify: `pt_cohort_common.py` (add `import csv`; add 5 functions)
- Test: `tests/test_pt_cohort_common.py` (append)

**Interfaces:**
- Consumes: `common.TRIAL_THRESHOLD`, `common.leg_trial_counts`, `common.list_participants`, `common.discover_all_trials` (Task 1 / existing `pt_report_common.py`); `classify_participant`, `load_registry`, `load_metadata_diagnosis` (Task 2)
- Produces:
  - `pt_cohort_common.current_qualifying_participants() -> set[str]`
  - `pt_cohort_common._folder_hints_control(pid: str) -> bool`
  - `pt_cohort_common.build_composition_rows(pids: set[str]) -> list[dict]` — each row: `pid, group, source, diagnosis, n_trials_left, n_trials_right`, sorted numerically by pid
  - `pt_cohort_common.write_composition_csv(rows: list[dict], out_path: str|None = None) -> None`
  - `pt_cohort_common.print_composition_banner(rows: list[dict]) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_cohort_common.py`:

```python
# ── current_qualifying_participants ─────────────────────────────────────

def test_current_qualifying_participants_filters_by_threshold(monkeypatch):
    monkeypatch.setattr(pcc.common, "list_participants", lambda: {"6": {}, "7": {}, "8": {}})
    counts = {"6": {"left": 4, "right": 4}, "7": {"left": 3, "right": 4}, "8": {"left": 4, "right": 4}}
    monkeypatch.setattr(pcc.common, "leg_trial_counts", lambda pid: counts[pid])
    assert pcc.current_qualifying_participants() == {"6", "8"}


# ── _folder_hints_control ───────────────────────────────────────────────

def test_folder_hints_control_matches_case_insensitive(monkeypatch):
    fake = [{"participant": "6", "leg": "left", "condition": "left_Control"},
           {"participant": "7", "leg": "left", "condition": "pre"}]
    monkeypatch.setattr(pcc.common, "discover_all_trials", lambda: fake)
    assert pcc._folder_hints_control("6") is True
    assert pcc._folder_hints_control("7") is False


# ── build_composition_rows ──────────────────────────────────────────────

def test_build_composition_rows_classifies_and_counts_trials(monkeypatch):
    monkeypatch.setattr(pcc, "load_registry", lambda: ({"6": "Control"}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: {"13": "MS"}.get(pid))
    monkeypatch.setattr(pcc.common, "leg_trial_counts",
                        lambda pid: {"13": {"left": 5, "right": 5}, "6": {"left": 4, "right": 4}}[pid])
    rows = pcc.build_composition_rows({"13", "6"})
    by_pid = {r["pid"]: r for r in rows}
    assert by_pid["13"]["group"] == "MS" and by_pid["13"]["source"] == "metadata"
    assert by_pid["13"]["n_trials_left"] == 5 and by_pid["13"]["n_trials_right"] == 5
    assert by_pid["6"]["group"] == "Control" and by_pid["6"]["source"] == "registry"


def test_build_composition_rows_sorted_numerically_by_pid(monkeypatch):
    monkeypatch.setattr(pcc, "load_registry", lambda: ({}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: None)
    monkeypatch.setattr(pcc.common, "leg_trial_counts", lambda pid: {"left": 4, "right": 4})
    rows = pcc.build_composition_rows({"9", "13", "6"})
    assert [r["pid"] for r in rows] == ["6", "9", "13"]   # numeric, not lexicographic


# ── write_composition_csv ────────────────────────────────────────────────

def test_write_composition_csv_writes_all_rows(tmp_path):
    rows = [
        {"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS",
         "n_trials_left": 5, "n_trials_right": 5},
        {"pid": "6", "group": "Unclassified", "source": "no_entry", "diagnosis": None,
         "n_trials_left": 4, "n_trials_right": 4},
    ]
    out_path = tmp_path / "cohort_composition.csv"
    pcc.write_composition_csv(rows, str(out_path))
    content = out_path.read_text(encoding="utf-8")
    assert "pid,group,source,n_trials_left,n_trials_right" in content
    assert "13,MS,metadata,5,5" in content
    assert "6,Unclassified,no_entry,4,4" in content


# ── print_composition_banner ─────────────────────────────────────────────

def test_print_composition_banner_lists_every_group(capsys, monkeypatch):
    monkeypatch.setattr(pcc, "_folder_hints_control", lambda pid: False)
    rows = [
        {"pid": "13", "group": "MS", "source": "metadata", "diagnosis": "MS", "n_trials_left": 5, "n_trials_right": 5},
        {"pid": "6", "group": "Control", "source": "registry", "diagnosis": "Control", "n_trials_left": 4, "n_trials_right": 4},
        {"pid": "9", "group": "Excluded", "source": "metadata", "diagnosis": "Stroke", "n_trials_left": 4, "n_trials_right": 4},
        {"pid": "7", "group": "Unclassified", "source": "no_entry", "diagnosis": None, "n_trials_left": 4, "n_trials_right": 4},
        {"pid": "8", "group": "Unclassified", "source": "registry_missing", "diagnosis": None, "n_trials_left": 4, "n_trials_right": 4},
    ]
    pcc.print_composition_banner(rows)
    out = capsys.readouterr().out
    assert "MS:" in out and "13" in out
    assert "Control:" in out and "6" in out
    assert "Excluded:" in out and "9 (Stroke)" in out
    assert "7" in out and "no_entry" in out
    assert "registry_missing" in out and "8" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k "qualifying or folder_hints or composition_rows or composition_csv or composition_banner" -v`
Expected: FAIL — `AttributeError` on each missing function

- [ ] **Step 3: Implement**

Add `import csv` to the top of `pt_cohort_common.py`. Append under "I/O":

```python
def current_qualifying_participants():
    """Every participant id currently meeting common.TRIAL_THRESHOLD on
    BOTH legs -- independent of whichever pid(s) run_pt_analysis.py was
    invoked with this run (design spec §6.1). Always recomputed from the
    full discoverable participant set."""
    qualifying = set()
    for pid in common.list_participants().keys():
        counts = common.leg_trial_counts(pid)
        if counts["left"] >= common.TRIAL_THRESHOLD and counts["right"] >= common.TRIAL_THRESHOLD:
            qualifying.add(pid)
    return qualifying


def _folder_hints_control(pid):
    """Best-effort cosmetic hint only -- NEVER used for classification.
    True if any trial path discovered for this participant has a
    condition string containing 'control' (case-insensitive), matching
    the legacy OptiTrack_Recordings/Participant_N_leg_control naming
    convention. Used only to decorate a no_entry warning line."""
    return any(r["participant"] == pid and "control" in r["condition"].lower()
              for r in common.discover_all_trials())


def build_composition_rows(pids):
    """One row per pid in `pids` (already the qualifying set): classify,
    look up raw trial counts, package for the composition CSV/banner.
    `diagnosis` carries the raw source string (for the Excluded banner
    line) and is not written to the CSV."""
    registry, registry_exists = load_registry()
    rows = []
    for pid in sorted(pids, key=int):
        metadata_diagnosis = load_metadata_diagnosis(pid)
        group, source = classify_participant(pid, metadata_diagnosis, registry, registry_exists)
        raw_diagnosis = metadata_diagnosis if source == "metadata" else registry.get(pid)
        counts = common.leg_trial_counts(pid)
        rows.append({"pid": pid, "group": group, "source": source, "diagnosis": raw_diagnosis,
                    "n_trials_left": counts["left"], "n_trials_right": counts["right"]})
    return rows


def write_composition_csv(rows, out_path=None):
    out_path = out_path or COMPOSITION_CSV
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pid", "group", "source", "n_trials_left", "n_trials_right"])
        for row in rows:
            w.writerow([row["pid"], row["group"], row["source"],
                       row["n_trials_left"], row["n_trials_right"]])
    print(f"-> {out_path}")


def print_composition_banner(rows):
    by_group = {"MS": [], "Control": [], "Excluded": [], "Unclassified": []}
    for row in rows:
        by_group[row["group"]].append(row)

    print("=" * 20 + " MS vs Control cohort " + "=" * 20)
    ms_txt = ", ".join(r["pid"] for r in by_group["MS"]) or "(none yet)"
    print(f"MS:           {ms_txt}  (n={len(by_group['MS'])})")
    ctrl_txt = ", ".join(r["pid"] for r in by_group["Control"]) or "(none yet)"
    print(f"Control:      {ctrl_txt}  (n={len(by_group['Control'])})")

    excl_txt = ", ".join(f"{r['pid']} ({r['diagnosis']})" for r in by_group["Excluded"]) or "(none)"
    print(f"Excluded:     {excl_txt}  (n={len(by_group['Excluded'])})")

    no_entry = [r for r in by_group["Unclassified"] if r["source"] == "no_entry"]
    missing = [r for r in by_group["Unclassified"] if r["source"] == "registry_missing"]
    if no_entry:
        parts = []
        for r in no_entry:
            hint = " (folder suggests 'control')" if _folder_hints_control(r["pid"]) else ""
            parts.append(f"{r['pid']}{hint}")
        print(f"Unclassified: {', '.join(parts)}  (n={len(no_entry)}, no_entry -- add to participant_groups.json)")
    if missing:
        pids_txt = ", ".join(r["pid"] for r in missing)
        print(f"              registry_missing ({pids_txt}): participant_groups.json not found --")
        print("              if you're in a worktree/isolated checkout, copy it over.")
    print("=" * 63)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k "qualifying or folder_hints or composition_rows or composition_csv or composition_banner" -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add pt_cohort_common.py tests/test_pt_cohort_common.py
git commit -m "feat: add cohort composition audit trail (CSV + console banner)"
```

---

### Task 6: `_collect_arm_data()` + `write_stats_csv()` + `run_cohort_comparison()` orchestration

**Files:**
- Modify: `pt_cohort_common.py` (add 3 functions)
- Test: `tests/test_pt_cohort_common.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 2-5, plus `common.collect_participant` (existing `pt_report_common.py`)
- Produces:
  - `pt_cohort_common._collect_arm_data(pids: list[str]) -> (summaries: dict, raw_trials: dict, contributing_pids: set[str])`
  - `pt_cohort_common.write_stats_csv(stats_rows: list[dict], out_path: str) -> None`
  - `pt_cohort_common.run_cohort_comparison() -> None` — calls `make_cohort_comparison_figure(...)` by name (defined in Task 7; stubbed here via `monkeypatch.setattr(..., raising=False)` so this task doesn't depend on Task 7's completion)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_cohort_common.py`:

```python
# ── run_cohort_comparison orchestration ─────────────────────────────────

def _stub_common(monkeypatch, qualifying, groups, trials):
    """qualifying: set of pids. groups: {pid: (group, source)}. trials:
    {pid: {"left": [...], "right": [...]}} of scored trial dicts (each
    with all _SCORE_KEYS)."""
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: qualifying)
    monkeypatch.setattr(pcc, "load_registry", lambda: ({}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: None)
    monkeypatch.setattr(pcc.common, "leg_trial_counts",
                        lambda pid: {leg: len(trials.get(pid, {}).get(leg, [])) for leg in pcc._LEGS})
    monkeypatch.setattr(pcc, "classify_participant",
                        lambda pid, md, reg, exists: groups.get(pid, ("Unclassified", "no_entry")))
    monkeypatch.setattr(pcc.common, "collect_participant",
                        lambda pid: ({(leg, "cond"): trials.get(pid, {}).get(leg, []) for leg in pcc._LEGS}, []))


def test_run_cohort_comparison_skips_when_control_arm_empty(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    _stub_common(monkeypatch, {"13"}, {"13": ("MS", "metadata")},
                {"13": {"left": [_trial()] * 4, "right": [_trial()] * 4}})
    pcc.run_cohort_comparison()
    out = capsys.readouterr().out
    assert "Cohort comparison skipped" in out
    assert (tmp_path / "cohort_composition.csv").is_file()   # written even when skipped


def test_run_cohort_comparison_writes_composition_csv_with_correct_groups(monkeypatch, tmp_path):
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    _stub_common(monkeypatch, {"13"}, {"13": ("MS", "metadata")},
                {"13": {"left": [_trial()] * 4, "right": [_trial()] * 4}})
    pcc.run_cohort_comparison()
    content = (tmp_path / "cohort_composition.csv").read_text(encoding="utf-8")
    assert "13,MS,metadata,4,4" in content


def test_run_cohort_comparison_runs_stats_and_figure_when_both_arms_present(monkeypatch, tmp_path):
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    monkeypatch.setattr(pcc, "STATS_CSV", str(tmp_path / "ms_vs_control_stats.csv"))
    figure_calls = []
    monkeypatch.setattr(pcc, "make_cohort_comparison_figure",
                        lambda *a, **k: figure_calls.append(True), raising=False)
    _stub_common(monkeypatch, {"13", "6"},
                {"13": ("MS", "metadata"), "6": ("Control", "registry")},
                {"13": {"left": [_trial(pt7=1.0)] * 4, "right": [_trial(pt7=1.0)] * 4},
                 "6": {"left": [_trial(pt7=2.0)] * 4, "right": [_trial(pt7=2.0)] * 4}})
    pcc.run_cohort_comparison()
    assert figure_calls == [True]
    assert (tmp_path / "ms_vs_control_stats.csv").is_file()


def test_run_cohort_comparison_filters_none_summaries(monkeypatch, tmp_path):
    # "13" clears the raw TRIAL_THRESHOLD gate (4 trials on file) but NONE
    # of them scored -- collect_participant's by_leg_tp reflects that as
    # empty lists for both legs. run_cohort_comparison must not crash, and
    # the resulting stats/figure must show 0 MS contributors while
    # cohort_composition.csv still shows 13's raw (non-zero) trial count.
    monkeypatch.setattr(pcc, "COMPOSITION_CSV", str(tmp_path / "cohort_composition.csv"))
    monkeypatch.setattr(pcc, "STATS_CSV", str(tmp_path / "ms_vs_control_stats.csv"))
    figure_calls = []
    monkeypatch.setattr(pcc, "make_cohort_comparison_figure",
                        lambda *a, **k: figure_calls.append(a), raising=False)
    monkeypatch.setattr(pcc, "current_qualifying_participants", lambda: {"13", "6"})
    monkeypatch.setattr(pcc, "load_registry", lambda: ({}, True))
    monkeypatch.setattr(pcc, "load_metadata_diagnosis", lambda pid: None)
    monkeypatch.setattr(pcc, "classify_participant",
                        lambda pid, md, reg, exists: {"13": ("MS", "metadata"), "6": ("Control", "metadata")}[pid])
    monkeypatch.setattr(pcc.common, "leg_trial_counts",
                        lambda pid: {"13": {"left": 4, "right": 4}, "6": {"left": 4, "right": 4}}[pid])
    monkeypatch.setattr(pcc.common, "collect_participant", lambda pid: (
        {("left", "cond"): [], ("right", "cond"): []} if pid == "13"
        else {("left", "cond"): [_trial(pt7=2.0)] * 4, ("right", "cond"): [_trial(pt7=2.0)] * 4},
        []))
    pcc.run_cohort_comparison()   # must not raise despite 13 contributing zero summaries
    assert len(figure_calls) == 1
    stats_content = (tmp_path / "ms_vs_control_stats.csv").read_text(encoding="utf-8")
    assert "left,pt7,0," in stats_content   # n_ms=0 for the leg 13 failed to contribute to
    comp_content = (tmp_path / "cohort_composition.csv").read_text(encoding="utf-8")
    assert "13,MS,metadata,4,4" in comp_content   # raw counts still shown despite 0 scored
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k run_cohort_comparison -v`
Expected: FAIL — `AttributeError: module 'pt_cohort_common' has no attribute 'run_cohort_comparison'`

- [ ] **Step 3: Implement**

Append to `pt_cohort_common.py` (under "I/O" for `_collect_arm_data`/`write_stats_csv`, and a new "Entry point" section for `run_cohort_comparison`):

```python
def _collect_arm_data(pids):
    """pids -> (summaries, raw_trials, contributing_pids). summaries /
    raw_trials: {"left": [...], "right": [...]}. summaries holds one
    aggregate_participant_summary() dict per participant that had at
    least one scored trial for that leg (the statistical layer --
    compute_cohort_stats and the figure's box/whiskers read only from
    this). raw_trials holds every individual scored trial record (the
    figure's descriptive-layer background jitter only -- never used for
    a statistic). contributing_pids is the post-filter participant set,
    which can be smaller than `pids` itself (see aggregate_participant_
    summary's None case, design spec §7.2 step 4)."""
    summaries = {"left": [], "right": []}
    raw_trials = {"left": [], "right": []}
    contributing_pids = set()
    for pid in pids:
        by_leg_tp, _ = common.collect_participant(pid)
        for leg in _LEGS:
            trials = [r for (leg_key, _cond), recs in by_leg_tp.items()
                     if leg_key == leg for r in recs]
            raw_trials[leg].extend(trials)
            summary = aggregate_participant_summary(trials)
            if summary is not None:
                summaries[leg].append(summary)
                contributing_pids.add(pid)
    return summaries, raw_trials, contributing_pids


def write_stats_csv(stats_rows, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["leg", "parameter", "ms_n", "ms_median", "ms_iqr",
                   "control_n", "control_median", "control_iqr",
                   "mann_whitney_p", "cliffs_delta", "effect_size"])
        for row in stats_rows:
            w.writerow([row["leg"], row["parameter"], row["n_ms"], row["ms_median"], row["ms_iqr"],
                       row["n_control"], row["control_median"], row["control_iqr"],
                       row["mann_whitney_p"], row["cliffs_delta"], row["effect_size"]])
    print(f"-> {out_path}")


# ══════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════

def run_cohort_comparison():
    """Called once, with no arguments, at the end of run_pt_analysis.py's
    main() -- recomputes the full MS-vs-Control cohort comparison from
    scratch every run (design spec §6.1, §7.2). Always writes
    cohort_composition.csv, even when the comparison itself ends up
    skipped for lacking one arm."""
    pids = current_qualifying_participants()
    rows = build_composition_rows(pids)
    write_composition_csv(rows)
    print_composition_banner(rows)

    ms_pids = [r["pid"] for r in rows if r["group"] == "MS"]
    control_pids = [r["pid"] for r in rows if r["group"] == "Control"]
    if not ms_pids or not control_pids:
        print(f"Cohort comparison skipped: {len(ms_pids)} MS / {len(control_pids)} Control "
             f"qualifying participants (need >=1 in each arm).")
        return

    ms_summaries, ms_raw, ms_contrib = _collect_arm_data(ms_pids)
    control_summaries, control_raw, control_contrib = _collect_arm_data(control_pids)

    stats_rows = compute_cohort_stats(ms_summaries, control_summaries)
    write_stats_csv(stats_rows, STATS_CSV)

    n_excluded_unclassified = sum(1 for r in rows if r["group"] in ("Excluded", "Unclassified"))
    make_cohort_comparison_figure(
        ms_summaries, ms_raw, len(ms_contrib), sum(len(v) for v in ms_raw.values()),
        control_summaries, control_raw, len(control_contrib), sum(len(v) for v in control_raw.values()),
        n_excluded_unclassified, FIGURE_PNG)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k run_cohort_comparison -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add pt_cohort_common.py tests/test_pt_cohort_common.py
git commit -m "feat: wire cohort orchestration (aggregation -> stats -> figure)"
```

---

### Task 7: `make_cohort_comparison_figure()`

**Files:**
- Modify: `pt_cohort_common.py` (add `import matplotlib.pyplot as plt`; add function)
- Test: `tests/test_pt_cohort_common.py` (append)

**Interfaces:**
- Consumes: `common.COLORS`, `common.BG_GRID` (existing `pt_report_common.py`), `_SCORE_KEYS`, `_LEGS`
- Produces: `pt_cohort_common.make_cohort_comparison_figure(ms_summaries, ms_raw, ms_n_participants, ms_n_trials, control_summaries, control_raw, control_n_participants, control_n_trials, n_excluded_unclassified, out_path) -> None`. This is the same name `run_cohort_comparison()` (Task 6) already calls by name, unstubbed from here on.

- [ ] **Step 1: Write the failing test**

This is a plotting function — this repo doesn't pixel-test any of its matplotlib figure functions (`pt_report_common.make_report_figure`, `mas_validation.make_validation_figure`, etc. have none either), so this is a smoke test only: confirm it runs without raising and produces a non-empty file.

Append to `tests/test_pt_cohort_common.py`:

```python
# ── make_cohort_comparison_figure (smoke test only -- pixel content isn't
# asserted anywhere else in this repo's plotting functions either) ────────

def test_make_cohort_comparison_figure_writes_png_without_raising(tmp_path):
    ms_summaries = {"left": [_summary(1.0)], "right": [_summary(1.2)]}
    control_summaries = {"left": [_summary(2.0)], "right": [_summary(2.2)]}
    ms_raw = {"left": [_trial(pt7=1.0)], "right": [_trial(pt7=1.2)]}
    control_raw = {"left": [_trial(pt7=2.0)], "right": [_trial(pt7=2.2)]}
    out_path = tmp_path / "ms_vs_control_boxplots.png"
    pcc.make_cohort_comparison_figure(
        ms_summaries, ms_raw, 1, 1, control_summaries, control_raw, 1, 1, 2, str(out_path))
    assert out_path.is_file()
    assert out_path.stat().st_size > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k make_cohort_comparison_figure -v`
Expected: FAIL — `AttributeError: module 'pt_cohort_common' has no attribute 'make_cohort_comparison_figure'`

- [ ] **Step 3: Implement**

Add `import matplotlib.pyplot as plt` to the top of `pt_cohort_common.py`, placed after `import pt_report_common as common` (the backend is already resolved by that import — see Global Constraints). Then append a new "Plotting" section:

```python
# ══════════════════════════════════════════════════════════════════════════
# Plotting
# ══════════════════════════════════════════════════════════════════════════

def make_cohort_comparison_figure(ms_summaries, ms_raw, ms_n_participants, ms_n_trials,
                                  control_summaries, control_raw, control_n_participants,
                                  control_n_trials, n_excluded_unclassified, out_path):
    """Light/clinical style matching pt_report_common.py (white background,
    same color conventions) -- NOT the dark dashboard style of the older
    ms_vs_healthy_analysis.py, so every figure run_pt_analysis.py produces
    reads as one visual system (design spec §7.3).

    Two point layers per box, deliberately: the box/whiskers are built
    from ms_summaries/control_summaries (one median per participant --
    the statistical layer compute_cohort_stats also reads from, avoiding
    pseudoreplication). ms_raw/control_raw (every individual scored
    trial) are drawn underneath as lighter background jitter for
    descriptive transparency only -- never used for a statistic."""
    ms_color = common.COLORS["red"]
    control_color = common.COLORS["green"]
    n_cols = len(_SCORE_KEYS)
    fig, axes = plt.subplots(2, n_cols, figsize=(3.2 * n_cols, 8), facecolor="white")
    rng = np.random.RandomState(13)

    for row_idx, leg in enumerate(_LEGS):
        for col_idx, key in enumerate(_SCORE_KEYS):
            ax = axes[row_idx, col_idx]
            ax.set_facecolor("#f8f9fa")
            ax.grid(True, color=common.BG_GRID, linestyle="-", linewidth=0.8, axis="y")

            ms_med = [s[key] for s in ms_summaries[leg]]
            ctrl_med = [s[key] for s in control_summaries[leg]]
            bp = ax.boxplot([ms_med, ctrl_med], positions=[0, 1], widths=0.4,
                            patch_artist=True, showfliers=False)
            bp["boxes"][0].set_facecolor(ms_color)
            bp["boxes"][0].set_alpha(0.5)
            bp["boxes"][1].set_facecolor(control_color)
            bp["boxes"][1].set_alpha(0.5)

            ms_raw_vals = [t[key] for t in ms_raw[leg]]
            ctrl_raw_vals = [t[key] for t in control_raw[leg]]
            if ms_raw_vals:
                ax.scatter(rng.uniform(-0.08, 0.08, len(ms_raw_vals)), ms_raw_vals,
                          color=ms_color, s=10, alpha=0.25, zorder=2)
            if ctrl_raw_vals:
                ax.scatter(1 + rng.uniform(-0.08, 0.08, len(ctrl_raw_vals)), ctrl_raw_vals,
                          color=control_color, s=10, alpha=0.25, zorder=2)
            if ms_med:
                ax.scatter(rng.uniform(-0.05, 0.05, len(ms_med)), ms_med, color=ms_color,
                          s=40, alpha=0.9, zorder=4, edgecolors="#333333", linewidths=0.5)
            if ctrl_med:
                ax.scatter(1 + rng.uniform(-0.05, 0.05, len(ctrl_med)), ctrl_med, color=control_color,
                          s=40, alpha=0.9, zorder=4, edgecolors="#333333", linewidths=0.5)

            ax.set_xticks([0, 1])
            ax.set_xticklabels(["MS", "Control"], fontsize=8)
            ax.set_title(key, fontsize=9, fontweight="bold")
            ax.tick_params(labelsize=7)

    for row_idx, leg_label in enumerate(("Left leg", "Right leg")):
        axes[row_idx, 0].set_ylabel(leg_label, fontsize=10, fontweight="bold")

    excl_txt = f" · {n_excluded_unclassified} excluded/unclassified" if n_excluded_unclassified else ""
    fig.suptitle(
        "MS vs Control — Pendulum Test Parameters (7-parameter Popovic PT score)\n"
        f"MS n={ms_n_participants} participants ({ms_n_trials} trials) · "
        f"Control n={control_n_participants} participants ({control_n_trials} trials)"
        f"{excl_txt} · see cohort_composition.csv",
        fontsize=11, y=1.02, color="#333333")
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out_path}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -k make_cohort_comparison_figure -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run the full cohort test file**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_cohort_common.py -v`
Expected: PASS (all tests from Tasks 2-7, including `test_run_cohort_comparison_runs_stats_and_figure_when_both_arms_present`'s figure-stub assertion still passing, and the smoke test above)

- [ ] **Step 6: Commit**

```bash
git add pt_cohort_common.py tests/test_pt_cohort_common.py
git commit -m "feat: add MS-vs-Control comparison figure (light/clinical style, matches pt_report_common)"
```

---

### Task 8: Wire `run_pt_analysis.py` to call `run_cohort_comparison()`

**Files:**
- Modify: `run_pt_analysis.py:26-30` (imports), `run_pt_analysis.py` `main()` (end of function)
- Test: `tests/test_run_pt_analysis_cohort_wiring.py` (new)

**Interfaces:**
- Consumes: `pt_cohort_common.run_cohort_comparison` (Task 6)
- Produces: `run_pt_analysis.pt_cohort_common` (module reference, for test monkeypatching)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_pt_analysis_cohort_wiring.py
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import run_pt_analysis as rpa


def test_main_calls_run_cohort_comparison_once(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py"])
    monkeypatch.setattr(rpa.common, "list_participants", lambda: {})
    calls = []
    monkeypatch.setattr(rpa.pt_cohort_common, "run_cohort_comparison", lambda: calls.append(True))
    rpa.main()
    assert calls == [True]


def test_main_calls_cohort_comparison_even_with_single_pid_arg(monkeypatch):
    # Regression guard for the exact bug this design fixed during review:
    # cohort comparison must still run when main() was invoked for one
    # specific participant, not the full sweep -- run_cohort_comparison()
    # recomputes the full qualifying set itself rather than reusing
    # main()'s pid-scoped `qualified` set.
    monkeypatch.setattr(sys, "argv", ["run_pt_analysis.py", "999"])
    monkeypatch.setattr(rpa.common, "leg_trial_counts", lambda pid: {"left": 0, "right": 0})
    monkeypatch.setattr(rpa, "run_for_participant", lambda pid: [])
    calls = []
    monkeypatch.setattr(rpa.pt_cohort_common, "run_cohort_comparison", lambda: calls.append(True))
    rpa.main()
    assert calls == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_run_pt_analysis_cohort_wiring.py -v`
Expected: FAIL — `AttributeError: module 'run_pt_analysis' has no attribute 'pt_cohort_common'`

- [ ] **Step 3: Implement**

In `run_pt_analysis.py`, change the import block (lines 26-30):

```python
import csv
import os
import sys

import pt_report_common as common
```

to:

```python
import csv
import os
import sys

import pt_cohort_common
import pt_report_common as common
```

Then in `main()`, add the new call as the last line before the function ends (after the existing MAS-nudge `if ready_for_mas:` block):

```python
    ready_for_mas = qualified & _mas_scored_participants()
    if ready_for_mas:
        print(f"{len(ready_for_mas)} participant(s) now have both trial data and MAS scores on file "
             f"-- run mas_validation.py to refresh the validation report.")

    pt_cohort_common.run_cohort_comparison()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_run_pt_analysis_cohort_wiring.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add run_pt_analysis.py tests/test_run_pt_analysis_cohort_wiring.py
git commit -m "feat: run MS-vs-Control cohort comparison at the end of every run_pt_analysis.py run"
```

---

### Task 9: Full regression pass + real-data verification

**Files:** none (verification only)

- [ ] **Step 1: Run the complete new/modified test surface**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pt_report_common.py tests/test_pt_cohort_common.py tests/test_run_pt_analysis_cohort_wiring.py -v`
Expected: PASS, all tests from Tasks 1-8 (approximately 45 tests total)

- [ ] **Step 2: Run the full repo test suite to check for regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (or the same pre-existing failures/skips as before this work, if any — compare against a baseline run if unsure; nothing in Tasks 1-8 should have touched unrelated modules)

- [ ] **Step 3: Run the real pipeline against the live dataset**

Run: `.venv\Scripts\python.exe run_pt_analysis.py`

Confirm in the console output:
- The existing per-participant report generation still runs and prints its usual output, unchanged.
- A new `==================== MS vs Control cohort ====================` banner appears at the end.
- P13 and P14 (the only participants with a real `metadata.json` diagnosis on file, both `"MS"`) are listed under `MS:`.
- Everyone else currently qualifying (if any) appears under `Unclassified` with `source=no_entry` (since `participant_groups.json` was just created empty in Task 2) — not `registry_missing`, since the file now exists in this checkout.
- Since `Control:` is empty, the console prints `Cohort comparison skipped: ... 0 Control qualifying participants ...` — this is expected, not a bug, until `participant_groups.json` gets real entries.

- [ ] **Step 4: Confirm the composition CSV was written**

Run: `.venv\Scripts\python.exe -c "import csv; print(list(csv.DictReader(open('Model_Analysis_Outputs/MS_vs_Control/cohort_composition.csv'))))"`
Expected: one row per currently-qualifying participant, matching the console banner from Step 3.

- [ ] **Step 5: Note the follow-up for the user (do not do this automatically)**

Per design spec §12, backfilling `participant_groups.json` for legacy participants (e.g. the archived `Participant_6/7/8_..._control` folders) is explicitly left to the user, not automated by this plan. After Step 3's run, tell the user which pids showed up as `Unclassified (no_entry)` with a `(folder suggests 'control')` hint, so they can decide whether to add them to `participant_groups.json` — do not add entries on their behalf.

- [ ] **Step 6: Final commit (only if Steps 1-4 required any fix)**

If verification surfaced a real bug, fix it, re-run the relevant test(s), and commit:

```bash
git add -A -- pt_cohort_common.py pt_report_common.py run_pt_analysis.py tests/
git commit -m "fix: <describe the issue found during end-to-end verification>"
```

If nothing needed fixing, skip this step — Task 8's commit is the last one.
