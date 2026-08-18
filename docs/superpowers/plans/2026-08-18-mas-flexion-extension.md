# MAS Flexion/Extension Scores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the clinician record optional MAS flexion/extension scores alongside the existing overall MAS Grade, store them additively in `mas_scores.csv`, and add (but do not wire into a script/report yet) the data-layer functions needed to run an exploratory correlation between those scores and the PT device's per-trial `spasticity_type` classification.

**Architecture:** `mas_validation.py` gets three additions — the two new fields join `DEFAULT_MAS_FIELDS`/`WIDENABLE_MAS_FIELDS` and `append_mas_score()`'s validation (Task 1); `_pt_lookup_factory()` gains an optional keyword-only `direction` filter (Task 2); a new `pair_pt_and_mas_by_direction()` emits pair records under the exact canonical keys (`mas_grade`/`pt_score`/`predicted_mas`) that `compute_validation_stats()` and `fit_mas_thresholds.py` already hard-index, so both are reused completely unmodified (Task 3). `pendulastic_app.py`'s `MasEntryPanel` gets two new optional form fields (Task 4).

**Tech Stack:** Python 3.13, Tkinter/`ttk`, `pytest` with `tmp_path`/`monkeypatch` for `mas_validation.py` (plain functions, no test classes — existing convention in `tests/test_mas_validation.py`) and headless `tk.Tk()`-backed `App()` instances for `pendulastic_app.py` (existing convention in `tests/test_app.py`).

**Spec:** `docs/superpowers/specs/2026-08-18-mas-flexion-extension-design.md` — approved by the user, Codex-reviewed, revised.

## Global Constraints

- **Additive only.** The existing `mas_grade` field, its CSV column, `pair_pt_and_mas()`, `compute_validation_stats()`, `fit_mas_thresholds.py`, and the report's Row 5 table are **not modified** by any task in this plan.
- **Canonical pair-record keys are load-bearing.** `compute_validation_stats()` (`mas_validation.py:141-158`) and `fit_mas_thresholds.py`'s `_kappa_for_thresholds()`/`fit_thresholds()`/`loocv_kappa()`/`check_sample_sufficiency()` all hard-index `p["mas_grade"]` and/or `p["pt_score"]`/`p["predicted_mas"]` — they are not generic over arbitrary field names. Task 3's `pair_pt_and_mas_by_direction()` must emit records under those exact keys.
- **`_pt_lookup_factory`'s new `direction` parameter is genuinely keyword-only** (`*, direction=None`) and rejects anything other than `None`/`"flexion"`/`"extension"` with `ValueError` — no silent typo-swallowing.
- **A trial record missing `spasticity_type` must never raise** — treat it as not matching any specific `direction` filter (`.get()`, not `[...]`).
- **Every row `pair_pt_and_mas_by_direction()` considers gets an accounted-for outcome** (a valid pair, or a `_skip_reason` entry) for each side it attempted — mirrors `pair_pt_and_mas()`'s existing auditability convention (`mas_validation.py:100-126`). A blank (not-assessed) value is the one case that gets no entry at all, since nothing was attempted.
- **No new report/dashboard/script surface.** This plan stops at the data layer + entry UI. Wiring `pair_pt_and_mas_by_direction()` into a runnable validation script or the report figure is explicitly out of scope.
- **This is an exploratory analysis, not a validated clinical claim** (spec §2) — reflected in this plan only as docstring/comment language on the new functions; no runtime behavior enforces it, since there's no report surface yet to word carefully.

---

### Task 1: `mas_scores.csv` schema + `append_mas_score()` validation for `mas_flexion`/`mas_extension`

**Files:**
- Modify: `mas_validation.py:73-82` (`DEFAULT_MAS_FIELDS`, `WIDENABLE_MAS_FIELDS`), `mas_validation.py:192-198` (`append_mas_score`'s validation block)
- Test: `tests/test_mas_validation.py` (fix 2 existing tests, append new tests)

**Interfaces:**
- Produces: `mas_validation.DEFAULT_MAS_FIELDS` and `mas_validation.WIDENABLE_MAS_FIELDS` both end with `["stronger_leg", "notes", "mas_flexion", "mas_extension"]`. `append_mas_score(row, csv_path=MAS_CSV)` accepts optional `row["mas_flexion"]`/`row["mas_extension"]` — blank or a valid `MAS_ORDER` grade; any other non-blank value raises `ValueError` with `"invalid mas_flexion ..."`/`"invalid mas_extension ..."` in the message (no write attempted).
- Consumes: existing `_valid_grade()` (unchanged signature).

- [ ] **Step 1: Fix the two existing tests this task's schema change breaks**

In `tests/test_mas_validation.py`, replace `test_default_mas_fields_includes_new_columns` (currently lines 33-34):

```python
# OLD
def test_default_mas_fields_includes_new_columns():
    assert mv.DEFAULT_MAS_FIELDS[-2:] == ["stronger_leg", "notes"]
```

```python
# NEW
def test_default_mas_fields_includes_new_columns():
    assert mv.DEFAULT_MAS_FIELDS[-4:] == ["stronger_leg", "notes", "mas_flexion", "mas_extension"]
```

Replace `test_append_mas_score_creates_file_with_header_if_missing` (currently lines 298-321) — only the two assertion lines change, the rest of the test body stays as-is:

```python
# OLD (the two assertion lines inside the test)
    lines = csv_path.read_text().splitlines()
    assert lines[0] == "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date,stronger_leg,notes"
    assert lines[1] == "20,left,pre,multiple sclerosis,1+,VL,2026-08-07,,"
```

```python
# NEW
    lines = csv_path.read_text().splitlines()
    assert lines[0] == ("participant,leg,condition,diagnosis,mas_grade,assessed_by,"
                        "assessed_date,stronger_leg,notes,mas_flexion,mas_extension")
    assert lines[1] == "20,left,pre,multiple sclerosis,1+,VL,2026-08-07,,,,"
```

- [ ] **Step 2: Write the new failing tests**

Append to `tests/test_mas_validation.py`:

```python
def test_append_mas_score_accepts_blank_mas_flexion_and_extension(tmp_path):
    csv_path = tmp_path / "new.csv"
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "2026-08-07"},
        csv_path=str(csv_path))
    rows = mv.load_mas_scores(str(csv_path))
    assert rows[0]["mas_flexion"] == ""
    assert rows[0]["mas_extension"] == ""


def test_append_mas_score_accepts_valid_mas_flexion_and_extension(tmp_path):
    csv_path = tmp_path / "new.csv"
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "2026-08-07",
         "mas_flexion": "2", "mas_extension": "1"},
        csv_path=str(csv_path))
    rows = mv.load_mas_scores(str(csv_path))
    assert rows[0]["mas_flexion"] == "2"
    assert rows[0]["mas_extension"] == "1"


def test_append_mas_score_rejects_invalid_mas_flexion(tmp_path):
    csv_path = tmp_path / "new.csv"
    with pytest.raises(ValueError, match="invalid mas_flexion"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "pre",
             "diagnosis": "", "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "mas_flexion": "5"},
            csv_path=str(csv_path))
    assert not csv_path.exists()


def test_append_mas_score_rejects_invalid_mas_extension(tmp_path):
    csv_path = tmp_path / "new.csv"
    with pytest.raises(ValueError, match="invalid mas_extension"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "pre",
             "diagnosis": "", "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "mas_extension": "banana"},
            csv_path=str(csv_path))
    assert not csv_path.exists()


def test_append_mas_score_widens_header_for_mas_flexion_and_extension(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,right,pre,multiple sclerosis,1,VL,2026-08-01\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1",
         "assessed_by": "VL", "assessed_date": "2026-08-07",
         "mas_flexion": "2", "mas_extension": "1+"},
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[0] == ("participant,leg,condition,diagnosis,mas_grade,assessed_by,"
                        "assessed_date,mas_flexion,mas_extension")
    assert lines[1] == "13,right,pre,multiple sclerosis,1,VL,2026-08-01,,"
    assert lines[2] == "20,left,pre,multiple sclerosis,1,VL,2026-08-07,2,1+"
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_mas_validation.py -k "mas_flexion or mas_extension or default_mas_fields_includes_new_columns or creates_file_with_header_if_missing" -v
```

Expected: the two fixed tests FAIL (current `DEFAULT_MAS_FIELDS` doesn't include the new columns yet); the new tests FAIL — `append_mas_score()` doesn't recognize `mas_flexion`/`mas_extension` at all yet, so the "accepts" tests fail because the header never widens/includes them, and the "rejects" tests fail because nothing validates them (no `ValueError` raised).

- [ ] **Step 4: Extend `DEFAULT_MAS_FIELDS` and `WIDENABLE_MAS_FIELDS`**

In `mas_validation.py`, replace lines 73-82:

```python
# OLD
DEFAULT_MAS_FIELDS = ["participant", "leg", "condition", "diagnosis",
                      "mas_grade", "assessed_by", "assessed_date",
                      "stronger_leg", "notes"]

# append_mas_score() only ever widens mas_scores.csv's header for these two
# fields -- an explicit allowlist, not "any key in row the header lacks".
# Widening on any unrecognized key would let a future typo'd dict key
# permanently become a CSV column; an unrelated stray key still falls
# through to the existing extrasaction="ignore" append behavior instead.
WIDENABLE_MAS_FIELDS = ["stronger_leg", "notes"]
```

```python
# NEW
DEFAULT_MAS_FIELDS = ["participant", "leg", "condition", "diagnosis",
                      "mas_grade", "assessed_by", "assessed_date",
                      "stronger_leg", "notes", "mas_flexion", "mas_extension"]

# append_mas_score() only ever widens mas_scores.csv's header for these
# fields -- an explicit allowlist, not "any key in row the header lacks".
# Widening on any unrecognized key would let a future typo'd dict key
# permanently become a CSV column; an unrelated stray key still falls
# through to the existing extrasaction="ignore" append behavior instead.
# mas_flexion/mas_extension (design spec
# docs/superpowers/specs/2026-08-18-mas-flexion-extension-design.md) are
# optional, direction-specific companions to mas_grade -- appended at the
# end of both lists, matching where widening always inserts new columns
# (see the `widened = list(fieldnames) + new_fields` line below), so a
# freshly-created file's column order matches a widened legacy file's.
WIDENABLE_MAS_FIELDS = ["stronger_leg", "notes", "mas_flexion", "mas_extension"]
```

- [ ] **Step 5: Add validation for the two new fields to `append_mas_score`**

In `mas_validation.py`, replace lines 192-198:

```python
# OLD
    grade = row.get("mas_grade", "")
    if not _valid_grade(grade):
        raise ValueError(f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})")
    stronger_leg = row.get("stronger_leg", "")
    if not _valid_stronger_leg(stronger_leg):
        raise ValueError(
            f"invalid stronger_leg {stronger_leg!r} (must be one of {STRONGER_LEG_OPTIONS})")
```

```python
# NEW
    grade = row.get("mas_grade", "")
    if not _valid_grade(grade):
        raise ValueError(f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})")
    stronger_leg = row.get("stronger_leg", "")
    if not _valid_stronger_leg(stronger_leg):
        raise ValueError(
            f"invalid stronger_leg {stronger_leg!r} (must be one of {STRONGER_LEG_OPTIONS})")
    # mas_flexion/mas_extension are optional -- blank means "not assessed"
    # and is always valid, distinct from an invalid non-blank value.
    mas_flexion = row.get("mas_flexion", "")
    if mas_flexion and not _valid_grade(mas_flexion):
        raise ValueError(f"invalid mas_flexion {mas_flexion!r} (must be one of {MAS_ORDER})")
    mas_extension = row.get("mas_extension", "")
    if mas_extension and not _valid_grade(mas_extension):
        raise ValueError(f"invalid mas_extension {mas_extension!r} (must be one of {MAS_ORDER})")
```

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_mas_validation.py -k "mas_flexion or mas_extension or default_mas_fields_includes_new_columns or creates_file_with_header_if_missing" -v
```

Expected: all PASS

- [ ] **Step 7: Run the full `mas_validation` test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_mas_validation.py -v
```

Expected: all pass, including every other `append_mas_score`/`DEFAULT_MAS_FIELDS` test (the widening, atomicity, malformed-file, and duplicate-column tests are untouched by this task and must still pass).

- [ ] **Step 8: Commit**

```bash
git add mas_validation.py tests/test_mas_validation.py
git commit -m "feat: add optional mas_flexion/mas_extension columns to mas_scores.csv"
```

---

### Task 2: `_pt_lookup_factory(*, direction=None)` — direction-filtered PT lookup

**Files:**
- Modify: `mas_validation.py:280-304` (`_pt_lookup_factory`)
- Test: `tests/test_mas_validation.py` (append)

**Interfaces:**
- Produces: `_pt_lookup_factory(*, direction=None)` — `direction=None` is byte-for-byte today's behavior; `direction="flexion"`/`"extension"` additionally filters pooled trials by `r.get("spasticity_type") == direction` before averaging; any other value raises `ValueError` immediately (not lazily, at factory-call time); calling positionally (`_pt_lookup_factory("flexion")`) raises `TypeError`.
- Consumes: nothing new — same `common.collect_participant`/`_tokenize_condition` this function already uses.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mas_validation.py`:

```python
def test_pt_lookup_direction_none_matches_prior_behavior(monkeypatch):
    fake_by_leg_tp = {("right", "pre"): [{"pt7": 1.0}, {"pt7": 2.0}]}
    monkeypatch.setattr(mv.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))
    lookup = mv._pt_lookup_factory(direction=None)
    assert lookup("13", "right", "pre") == pytest.approx(1.5)


def test_pt_lookup_direction_filters_by_spasticity_type(monkeypatch):
    fake_by_leg_tp = {("right", "pre"): [
        {"pt7": 1.0, "spasticity_type": "flexion"},
        {"pt7": 3.0, "spasticity_type": "extension"},
        {"pt7": 5.0, "spasticity_type": "flexion"},
        {"pt7": 100.0, "spasticity_type": "balanced"},
    ]}
    monkeypatch.setattr(mv.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))
    flexion_lookup = mv._pt_lookup_factory(direction="flexion")
    extension_lookup = mv._pt_lookup_factory(direction="extension")
    assert flexion_lookup("13", "right", "pre") == pytest.approx(3.0)      # mean(1.0, 5.0)
    assert extension_lookup("13", "right", "pre") == pytest.approx(3.0)    # the one extension trial


def test_pt_lookup_direction_ignores_trials_missing_spasticity_type(monkeypatch):
    fake_by_leg_tp = {("right", "pre"): [
        {"pt7": 1.0},   # no spasticity_type key at all -- must not raise
        {"pt7": 9.0, "spasticity_type": "flexion"},
    ]}
    monkeypatch.setattr(mv.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))
    lookup = mv._pt_lookup_factory(direction="flexion")
    assert lookup("13", "right", "pre") == pytest.approx(9.0)


def test_pt_lookup_direction_returns_none_not_zero_when_no_direction_match(monkeypatch):
    fake_by_leg_tp = {("right", "pre"): [{"pt7": 1.0, "spasticity_type": "extension"}]}
    monkeypatch.setattr(mv.common, "collect_participant", lambda pid: (fake_by_leg_tp, []))
    lookup = mv._pt_lookup_factory(direction="flexion")
    assert lookup("13", "right", "pre") is None


def test_pt_lookup_factory_rejects_invalid_direction():
    with pytest.raises(ValueError, match="invalid direction"):
        mv._pt_lookup_factory(direction="sideways")


def test_pt_lookup_factory_direction_is_keyword_only():
    with pytest.raises(TypeError):
        mv._pt_lookup_factory("flexion")
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_mas_validation.py -k "pt_lookup_direction or pt_lookup_factory_rejects or pt_lookup_factory_direction_is_keyword_only" -v
```

Expected: `direction_is_keyword_only` and `rejects_invalid_direction` FAIL (today's `_pt_lookup_factory()` takes no arguments at all, so both currently raise `TypeError` for the wrong reason — passing any argument fails); the others FAIL because `direction=None`/`"flexion"`/`"extension"` aren't accepted yet either.

- [ ] **Step 3: Add the `direction` parameter**

In `mas_validation.py`, replace lines 280-304:

```python
# OLD
def _pt_lookup_factory():
    """pt_lookup(participant, leg, condition) -> float|None, backed by
    pt_report_common.collect_participant(), cached per participant so a
    mas_scores.csv with many rows for one participant only scans their
    trials once.

    `condition` is matched against the real condition(s) recorded for that
    participant/leg via _tokenize_condition -- every trial whose condition
    tokenizes to the same set as the requested one is pooled into the mean
    PT score. Returns None if nothing matches (wrong leg, or no condition
    with that token set recorded for this participant)."""
    cache = {}

    def lookup(participant, leg, condition):
        if participant not in cache:
            cache[participant] = common.collect_participant(participant)[0]
        wanted = _tokenize_condition(condition)
        trials = [r for (leg_key, cond_key), recs in cache[participant].items()
                 if leg_key == leg and _tokenize_condition(cond_key) == wanted
                 for r in recs]
        if not trials:
            return None
        return float(np.mean([r["pt7"] for r in trials]))

    return lookup
```

```python
# NEW
_PT_LOOKUP_DIRECTIONS = (None, "flexion", "extension")


def _pt_lookup_factory(*, direction=None):
    """pt_lookup(participant, leg, condition) -> float|None, backed by
    pt_report_common.collect_participant(), cached per participant so a
    mas_scores.csv with many rows for one participant only scans their
    trials once.

    `condition` is matched against the real condition(s) recorded for that
    participant/leg via _tokenize_condition -- every trial whose condition
    tokenizes to the same set as the requested one is pooled into the mean
    PT score. Returns None if nothing matches (wrong leg, or no condition
    with that token set recorded for this participant).

    direction=None (default): unchanged behavior, pools every matching
    trial regardless of spasticity_type. direction="flexion"/"extension"
    (design spec docs/superpowers/specs/2026-08-18-mas-flexion-extension-
    design.md): additionally restricts the pooled trials to
    r.get("spasticity_type") == direction before averaging -- a trial
    record missing that key is treated as not matching, never raises. Any
    other direction value raises ValueError immediately (fails loudly on a
    typo rather than silently returning "no data" for every lookup)."""
    if direction not in _PT_LOOKUP_DIRECTIONS:
        raise ValueError(
            f"invalid direction {direction!r} (must be one of {_PT_LOOKUP_DIRECTIONS})")
    cache = {}

    def lookup(participant, leg, condition):
        if participant not in cache:
            cache[participant] = common.collect_participant(participant)[0]
        wanted = _tokenize_condition(condition)
        trials = [r for (leg_key, cond_key), recs in cache[participant].items()
                 if leg_key == leg and _tokenize_condition(cond_key) == wanted
                 for r in recs]
        if direction is not None:
            trials = [r for r in trials if r.get("spasticity_type") == direction]
        if not trials:
            return None
        return float(np.mean([r["pt7"] for r in trials]))

    return lookup
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_mas_validation.py -k "pt_lookup_direction or pt_lookup_factory_rejects or pt_lookup_factory_direction_is_keyword_only" -v
```

Expected: all 6 PASS

- [ ] **Step 5: Run the full `mas_validation` test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_mas_validation.py -v
```

Expected: all pass, including `test_pt_lookup_matches_specific_condition_only` and `test_pt_lookup_returns_none_when_leg_has_no_recorded_trials` (both call `_pt_lookup_factory()` with no arguments — `direction` defaulting to `None` must keep them passing unchanged).

- [ ] **Step 6: Commit**

```bash
git add mas_validation.py tests/test_mas_validation.py
git commit -m "feat: add direction filter to _pt_lookup_factory for flexion/extension pooling"
```

---

### Task 3: `pair_pt_and_mas_by_direction()` — canonical-keyed pairing, reused unmodified by `compute_validation_stats`/`fit_mas_thresholds`

**Files:**
- Modify: `mas_validation.py` (add function directly after `pair_pt_and_mas`, i.e. after line 126)
- Test: `tests/test_mas_validation.py` (append)

**Interfaces:**
- Produces: `pair_pt_and_mas_by_direction(mas_rows, pt_lookup_flexion, pt_lookup_extension) -> (flexion_records: list, extension_records: list)`. Each record is either a valid pair (`mas_grade`, `pt_score`, `predicted_mas`, `direction`, plus every original key from the row) or `dict(row, _skip_reason=...)`. A row whose value for that side is blank contributes no entry to that side's list.
- Consumes: `_valid_grade()`, `pt.pt_to_mas()` (both existing, unchanged), and Task 2's `_pt_lookup_factory(direction=...)` as the intended real-world `pt_lookup_flexion`/`pt_lookup_extension` arguments (tests use plain lambdas instead, matching how `pair_pt_and_mas()`'s own tests already do).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mas_validation.py`:

```python
def test_pair_pt_and_mas_by_direction_blank_produces_no_entry():
    rows = [{"participant": "13", "leg": "right", "condition": "pre",
             "mas_grade": "2", "mas_flexion": "", "mas_extension": ""}]
    flexion, extension = mv.pair_pt_and_mas_by_direction(
        rows, pt_lookup_flexion=lambda p, l, c: 1.0, pt_lookup_extension=lambda p, l, c: 1.0)
    assert flexion == []
    assert extension == []


def test_pair_pt_and_mas_by_direction_produces_canonical_pair_keys():
    rows = [{"participant": "13", "leg": "right", "condition": "pre",
             "mas_grade": "2", "mas_flexion": "1+", "mas_extension": "3"}]
    flexion, extension = mv.pair_pt_and_mas_by_direction(
        rows, pt_lookup_flexion=lambda p, l, c: 0.4, pt_lookup_extension=lambda p, l, c: 0.9)
    assert len(flexion) == 1 and len(extension) == 1
    assert flexion[0]["mas_grade"] == "1+"     # direction-specific value, shadows row's overall "2"
    assert flexion[0]["pt_score"] == 0.4
    assert flexion[0]["predicted_mas"] in mv.MAS_ORDER
    assert flexion[0]["direction"] == "flexion"
    assert extension[0]["mas_grade"] == "3"
    assert extension[0]["direction"] == "extension"


def test_pair_pt_and_mas_by_direction_invalid_grade_gets_skip_reason():
    rows = [{"participant": "13", "leg": "right", "condition": "pre",
             "mas_grade": "2", "mas_flexion": "5", "mas_extension": ""}]
    flexion, extension = mv.pair_pt_and_mas_by_direction(
        rows, pt_lookup_flexion=lambda p, l, c: 1.0, pt_lookup_extension=lambda p, l, c: 1.0)
    assert len(flexion) == 1
    assert "_skip_reason" in flexion[0]
    assert "invalid mas_flexion" in flexion[0]["_skip_reason"]
    assert extension == []


def test_pair_pt_and_mas_by_direction_no_pt_match_gets_skip_reason_not_dropped():
    rows = [{"participant": "13", "leg": "right", "condition": "pre",
             "mas_grade": "2", "mas_flexion": "1", "mas_extension": ""}]
    flexion, extension = mv.pair_pt_and_mas_by_direction(
        rows, pt_lookup_flexion=lambda p, l, c: None, pt_lookup_extension=lambda p, l, c: None)
    assert len(flexion) == 1
    assert "_skip_reason" in flexion[0]
    assert "no matching flexion trial data" in flexion[0]["_skip_reason"]


def test_pair_pt_and_mas_by_direction_independent_sides():
    rows = [{"participant": "13", "leg": "right", "condition": "pre",
             "mas_grade": "2", "mas_flexion": "1", "mas_extension": ""}]
    flexion, extension = mv.pair_pt_and_mas_by_direction(
        rows, pt_lookup_flexion=lambda p, l, c: 0.5, pt_lookup_extension=lambda p, l, c: 0.5)
    assert len(flexion) == 1 and "_skip_reason" not in flexion[0]
    assert extension == []


def test_direction_pairs_work_unmodified_with_compute_validation_stats():
    rows = [
        {"participant": "1", "leg": "right", "condition": "pre", "mas_grade": "2",
         "mas_flexion": "0", "mas_extension": ""},
        {"participant": "2", "leg": "right", "condition": "pre", "mas_grade": "2",
         "mas_flexion": "1", "mas_extension": ""},
    ]
    pt_by_participant = {"1": 0.05, "2": 0.20}
    flexion, _ = mv.pair_pt_and_mas_by_direction(
        rows,
        pt_lookup_flexion=lambda p, l, c: pt_by_participant[p],
        pt_lookup_extension=lambda p, l, c: None)
    valid = [p for p in flexion if "_skip_reason" not in p]
    stats = mv.compute_validation_stats(valid)
    assert stats["n"] == 2
    assert stats["per_grade"]["0"]["n"] == 1
    assert stats["per_grade"]["1"]["n"] == 1


def test_direction_pairs_work_unmodified_with_fit_mas_thresholds():
    import fit_mas_thresholds as fmt
    pt_by_grade = {"0": 0.05, "1": 0.20, "1+": 0.35, "2": 0.50, "3": 0.70, "4": 0.90}
    rows = []
    pt_by_participant = {}
    for i, grade in enumerate(list(pt_by_grade) * 3):
        pid = str(i)
        rows.append({"participant": pid, "leg": "right", "condition": "pre",
                     "mas_grade": "2", "mas_flexion": grade, "mas_extension": ""})
        pt_by_participant[pid] = pt_by_grade[grade]
    flexion, _ = mv.pair_pt_and_mas_by_direction(
        rows,
        pt_lookup_flexion=lambda p, l, c: pt_by_participant[p],
        pt_lookup_extension=lambda p, l, c: None)
    valid = [p for p in flexion if "_skip_reason" not in p]
    ok, report = fmt.check_sample_sufficiency(valid)
    assert ok, report
    thresholds, kappa = fmt.fit_thresholds(valid)
    assert thresholds is not None
    assert kappa == pytest.approx(1.0)
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_mas_validation.py -k "pair_pt_and_mas_by_direction or direction_pairs_work_unmodified" -v
```

Expected: FAIL — `AttributeError: module 'mas_validation' has no attribute 'pair_pt_and_mas_by_direction'`.

- [ ] **Step 3: Add `pair_pt_and_mas_by_direction`**

In `mas_validation.py`, add this function directly after `pair_pt_and_mas` (after the line `return out` that closes it, i.e. after current line 126, before the `compute_validation_stats` section header):

```python
def pair_pt_and_mas_by_direction(mas_rows, pt_lookup_flexion, pt_lookup_extension):
    """Exploratory counterpart to pair_pt_and_mas() (design spec
    docs/superpowers/specs/2026-08-18-mas-flexion-extension-design.md
    Section 3) -- pairs mas_flexion/mas_extension against direction-
    filtered PT lookups instead of the single overall mas_grade. This is
    an exploratory correlation, not a validated clinical equivalence (see
    the spec's Section 2 caveat): spasticity_type classifies one passive
    swing's motion asymmetry, not two separately-assessed muscle groups.

    Returns (flexion_records, extension_records). For each side
    independently: a blank value ("not assessed") produces no entry at
    all. A non-blank invalid grade, or a valid grade with no matching
    direction-specific trial data, produces dict(row, _skip_reason=...) --
    same auditability convention pair_pt_and_mas() already uses, so
    nothing is silently dropped. A valid grade with a PT match produces a
    canonical pair record using the exact keys compute_validation_stats()
    and fit_mas_thresholds.py already require -- mas_grade (set to the
    direction-specific value), pt_score, predicted_mas -- plus every other
    key already on row, plus direction ("flexion"/"extension", bookkeeping
    only, ignored by those functions). This deliberately shadows row's own
    overall mas_grade key in the copied record; the original overall grade
    is untouched in mas_scores.csv and in row itself."""
    def _pair_one_side(direction, mas_key, pt_lookup):
        out = []
        for row in mas_rows:
            value = row.get(mas_key, "")
            if not value:
                continue
            if not _valid_grade(value):
                out.append(dict(row, _skip_reason=
                    f"invalid {mas_key} {value!r} (must be one of {MAS_ORDER})"))
                continue
            pt_score = pt_lookup(row["participant"], row["leg"], row["condition"])
            if pt_score is None:
                out.append(dict(row, _skip_reason=
                    f"no matching {direction} trial data for this participant/leg/condition"))
                continue
            paired = dict(row)
            paired["mas_grade"] = value
            paired["pt_score"] = pt_score
            paired["predicted_mas"] = pt.pt_to_mas(pt_score)
            paired["direction"] = direction
            out.append(paired)
        return out

    flexion_records = _pair_one_side("flexion", "mas_flexion", pt_lookup_flexion)
    extension_records = _pair_one_side("extension", "mas_extension", pt_lookup_extension)
    return flexion_records, extension_records
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_mas_validation.py -k "pair_pt_and_mas_by_direction or direction_pairs_work_unmodified" -v
```

Expected: all 7 PASS

- [ ] **Step 5: Run the full `mas_validation` test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_mas_validation.py -v
```

Expected: all pass.

- [ ] **Step 6: Run the `fit_mas_thresholds` test suite too (if one exists) to confirm no regressions there**

```
.venv\Scripts\pytest tests\test_fit_mas_thresholds.py -v
```

If this file doesn't exist, skip this step — `fit_mas_thresholds.py` isn't modified by this task, only imported and called from the new integration test above, which already covers it.

- [ ] **Step 7: Commit**

```bash
git add mas_validation.py tests/test_mas_validation.py
git commit -m "feat: add pair_pt_and_mas_by_direction with canonical pair-record keys"
```

---

### Task 4: `MasEntryPanel` UI — MAS Flexion/Extension fields

**Files:**
- Modify: `pendulastic_app.py:2798-2823` (`MasEntryPanel._build_widgets`, MAS Grade through Notes), `pendulastic_app.py:2908-2918` (`_on_save_clicked`'s row dict), `pendulastic_app.py:2935-2937` (`_on_save_clicked`'s clear-on-save)
- Test: `tests/test_app.py` (modify one existing test, append new tests)

**Interfaces:**
- Produces: `MasEntryPanel.mas_flexion_var`, `MasEntryPanel.mas_extension_var` (new `tk.StringVar`s, default `""`). `_on_save_clicked`'s row dict passed to `append_mas_score()` gains `"mas_flexion"`/`"mas_extension"` keys (from `self.mas_flexion_var.get().strip()`/`self.mas_extension_var.get().strip()`). Both are cleared to `""` on successful save, alongside the existing `mas_grade_var`/`notes_text` clear.
- Consumes: `_mas_validation.MAS_ORDER` (existing, unchanged), Task 1's `append_mas_score()` (now validates these two new row keys).

- [ ] **Step 1: Update the existing full-field-assertion test**

In `tests/test_app.py`, in `test_mas_entry_panel_save_appends_and_refreshes` (currently lines 1957-1997), replace the expected dict:

```python
# OLD
        assert append_calls[0] == {
            "participant": "20",
            "leg": "left",
            "condition": "pre",
            "diagnosis": "multiple sclerosis",
            "mas_grade": "1",
            "assessed_by": "VL",
            "assessed_date": "2026-08-07",
            "stronger_leg": "",
            "notes": "",
        }
```

```python
# NEW
        assert append_calls[0] == {
            "participant": "20",
            "leg": "left",
            "condition": "pre",
            "diagnosis": "multiple sclerosis",
            "mas_grade": "1",
            "assessed_by": "VL",
            "assessed_date": "2026-08-07",
            "stronger_leg": "",
            "notes": "",
            "mas_flexion": "",
            "mas_extension": "",
        }
```

- [ ] **Step 2: Write the new failing tests**

Append to `tests/test_app.py`:

```python
def test_mas_entry_panel_mas_flexion_extension_default_blank():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        assert app._mas_entry.mas_flexion_var.get() == ""
        assert app._mas_entry.mas_extension_var.get() == ""
    finally:
        app.destroy()


def test_mas_entry_panel_save_includes_mas_flexion_and_extension(monkeypatch):
    import pendulastic_app as _m
    append_calls = []
    monkeypatch.setattr(_m._mas_validation, "append_mas_score",
                        lambda row, **kw: append_calls.append(row))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry.mas_flexion_var.set("1+")
        app._mas_entry.mas_extension_var.set("2")
        app._mas_entry._on_save_clicked()
        app.update()
        assert len(append_calls) == 1
        assert append_calls[0]["mas_flexion"] == "1+"
        assert append_calls[0]["mas_extension"] == "2"
    finally:
        app.destroy()


def test_mas_entry_panel_save_clears_mas_flexion_and_extension(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "append_mas_score", lambda row, **kw: None)
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [])
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry.mas_flexion_var.set("1+")
        app._mas_entry.mas_extension_var.set("2")
        app._mas_entry._on_save_clicked()
        app.update()
        assert app._mas_entry.mas_flexion_var.get() == ""
        assert app._mas_entry.mas_extension_var.get() == ""
    finally:
        app.destroy()


def test_mas_entry_panel_save_shows_error_on_invalid_mas_flexion(monkeypatch):
    import pendulastic_app as _m

    def raise_invalid(row, **kw):
        raise ValueError(f"invalid mas_flexion {row['mas_flexion']!r} (must be one of [])")
    monkeypatch.setattr(_m._mas_validation, "append_mas_score", raise_invalid)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        # Both new fields are readonly ttk.Combobox widgets, so an operator
        # can't type an invalid value through the UI -- this exercises the
        # data-layer validation path directly via the StringVar, the same
        # way test_mas_entry_panel_save_shows_error_on_invalid_grade already
        # does for mas_grade_var.
        app._mas_entry.mas_flexion_var.set("bogus")
        app._mas_entry._on_save_clicked()
        app.update()
        assert "invalid mas_flexion" in app._mas_entry.error_var.get()
    finally:
        app.destroy()
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_app.py -k "mas_entry_panel_mas_flexion_extension_default_blank or mas_entry_panel_save_includes_mas_flexion_and_extension or mas_entry_panel_save_clears_mas_flexion_and_extension or mas_entry_panel_save_shows_error_on_invalid_mas_flexion or mas_entry_panel_save_appends_and_refreshes" -v
```

Expected: `save_appends_and_refreshes` FAILS (dict comparison now includes keys the panel doesn't produce yet); the 3 new tests FAIL with `AttributeError: 'MasEntryPanel' object has no attribute 'mas_flexion_var'`.

- [ ] **Step 4: Add the two new form fields**

In `pendulastic_app.py`, in `MasEntryPanel._build_widgets`, replace lines 2798-2823 (from the `MAS Grade:` label through the end of the `Notes:` block):

```python
# OLD
        tk.Label(form, text="MAS Grade:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=5, column=0, sticky="e", **pad)
        self.mas_grade_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_grade_var, width=19,
                    state="readonly",
                    values=list(_mas_validation.MAS_ORDER)).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed By:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=6, column=0, sticky="e", **pad)
        self.assessed_by_var = tk.StringVar()
        tk.Entry(form, textvariable=self.assessed_by_var, width=22).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed Date:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=7, column=0, sticky="e", **pad)
        self.assessed_date_var = tk.StringVar(
            value=_datetime.date.today().isoformat())
        tk.Entry(form, textvariable=self.assessed_date_var, width=22).grid(
            row=7, column=1, sticky="w", **pad)

        tk.Label(form, text="Notes:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=8, column=0, sticky="ne", **pad)
        self.notes_text = tk.Text(form, height=3, width=22, wrap="word",
                                  bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG"])
        self.notes_text.grid(row=8, column=1, sticky="w", **pad)
```

```python
# NEW
        tk.Label(form, text="MAS Grade:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=5, column=0, sticky="e", **pad)
        self.mas_grade_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_grade_var, width=19,
                    state="readonly",
                    values=list(_mas_validation.MAS_ORDER)).grid(
            row=5, column=1, sticky="w", **pad)

        # Optional, direction-specific grades (design spec
        # docs/superpowers/specs/2026-08-18-mas-flexion-extension-design.md)
        # -- unlike MAS Grade above, these start blank and stay optional, so
        # each combobox gets a leading blank choice meaning "not assessed."
        tk.Label(form, text="MAS Flexion:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=6, column=0, sticky="e", **pad)
        self.mas_flexion_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_flexion_var, width=19,
                    state="readonly",
                    values=[""] + list(_mas_validation.MAS_ORDER)).grid(
            row=6, column=1, sticky="w", **pad)

        tk.Label(form, text="MAS Extension:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=7, column=0, sticky="e", **pad)
        self.mas_extension_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_extension_var, width=19,
                    state="readonly",
                    values=[""] + list(_mas_validation.MAS_ORDER)).grid(
            row=7, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed By:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=8, column=0, sticky="e", **pad)
        self.assessed_by_var = tk.StringVar()
        tk.Entry(form, textvariable=self.assessed_by_var, width=22).grid(
            row=8, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed Date:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=9, column=0, sticky="e", **pad)
        self.assessed_date_var = tk.StringVar(
            value=_datetime.date.today().isoformat())
        tk.Entry(form, textvariable=self.assessed_date_var, width=22).grid(
            row=9, column=1, sticky="w", **pad)

        tk.Label(form, text="Notes:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=10, column=0, sticky="ne", **pad)
        self.notes_text = tk.Text(form, height=3, width=22, wrap="word",
                                  bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG"])
        self.notes_text.grid(row=10, column=1, sticky="w", **pad)
```

- [ ] **Step 5: Include the two new fields in the save handler's row dict**

In `pendulastic_app.py`, in `_on_save_clicked`, replace lines 2908-2918:

```python
# OLD
        row = {
            "participant": participant,
            "leg": self.leg_var.get().lower(),
            "condition": self.condition_var.get().strip(),
            "diagnosis": self.diagnosis_var.get().strip(),
            "mas_grade": mas_grade,
            "assessed_by": self.assessed_by_var.get().strip(),
            "assessed_date": self.assessed_date_var.get().strip(),
            "stronger_leg": self.stronger_leg_var.get().strip().lower(),
            "notes": self.notes_text.get("1.0", "end").strip(),
        }
```

```python
# NEW
        row = {
            "participant": participant,
            "leg": self.leg_var.get().lower(),
            "condition": self.condition_var.get().strip(),
            "diagnosis": self.diagnosis_var.get().strip(),
            "mas_grade": mas_grade,
            "assessed_by": self.assessed_by_var.get().strip(),
            "assessed_date": self.assessed_date_var.get().strip(),
            "stronger_leg": self.stronger_leg_var.get().strip().lower(),
            "notes": self.notes_text.get("1.0", "end").strip(),
            "mas_flexion": self.mas_flexion_var.get().strip(),
            "mas_extension": self.mas_extension_var.get().strip(),
        }
```

- [ ] **Step 6: Clear the two new fields on successful save**

In `pendulastic_app.py`, in `_on_save_clicked`, replace lines 2935-2937:

```python
# OLD
        self._set_feedback(f"Saved {participant} {row['leg']} / {mas_grade}.", ok=True)
        self.mas_grade_var.set("")
        self.notes_text.delete("1.0", "end")
        self.refresh()
```

```python
# NEW
        self._set_feedback(f"Saved {participant} {row['leg']} / {mas_grade}.", ok=True)
        self.mas_grade_var.set("")
        self.mas_flexion_var.set("")
        self.mas_extension_var.set("")
        self.notes_text.delete("1.0", "end")
        self.refresh()
```

- [ ] **Step 7: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_app.py -k "mas_entry_panel_mas_flexion_extension_default_blank or mas_entry_panel_save_includes_mas_flexion_and_extension or mas_entry_panel_save_clears_mas_flexion_and_extension or mas_entry_panel_save_shows_error_on_invalid_mas_flexion or mas_entry_panel_save_appends_and_refreshes" -v
```

Expected: all 5 PASS

- [ ] **Step 8: Run the full `MasEntryPanel` test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_app.py -k mas_entry -v
```

Expected: all pass, including the stronger_leg/notes tests (`test_mas_entry_panel_save_includes_stronger_leg_and_notes`, `test_mas_entry_panel_save_clears_notes_but_not_stronger_leg`), the empty-state/skipped-row/figure-rendering tests, and the export tests — none of them touch the two new fields, so their behavior must be identical to before this task.

- [ ] **Step 9: Manual smoke test**

Run: `.venv\Scripts\python.exe pendulastic_app.py`

Verify by hand:
1. Navigate to MAS Score Entry. Confirm "MAS Flexion" and "MAS Extension" appear as two new rows directly below "MAS Grade," each a dropdown with a blank option plus 0/1/1+/2/3/4.
2. Fill in Participant ID and MAS Grade only (leave Flexion/Extension blank), click Save — confirm it saves successfully (blank direction-specific fields aren't required).
3. Fill in Participant ID, MAS Grade, MAS Flexion, and MAS Extension, click Save — confirm the confirmation message appears, and that MAS Grade, MAS Flexion, and MAS Extension all clear afterward while Participant ID/Leg/Condition/Assessed Date remain (batch-entry behavior preserved).
4. Open the resulting `mas_scores.csv` (repo root, gitignored) and confirm the new row has non-blank `mas_flexion`/`mas_extension` columns.

- [ ] **Step 10: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add MAS Flexion/Extension fields to MasEntryPanel"
```

---

## Plan Self-Review Notes

- **Spec coverage:** §3's `DEFAULT_MAS_FIELDS`/`WIDENABLE_MAS_FIELDS`/`append_mas_score` changes → Task 1. §3's `_pt_lookup_factory(*, direction=None)` → Task 2. §3's `pair_pt_and_mas_by_direction` (including the canonical-key requirement and per-row auditability) → Task 3, with two integration tests proving `compute_validation_stats`/`fit_mas_thresholds.py` really do work unmodified — the exact thing Codex's review flagged as unverified in the spec draft. §4's UI fields/save/clear → Task 4. §6's error-handling bullets are each covered by a specific test in Tasks 1-4. §7 (Known Limitations) and §2's exploratory-framing caveat are reflected as docstring language on the new functions (`pair_pt_and_mas_by_direction`, Task 3) since there's no runtime/report surface in this plan to enforce them against. §8's testing bullets map 1:1 to the tests written in each task.
- **Type/name consistency checked:** `mas_flexion`/`mas_extension` (Task 1's CSV keys) are the exact same string keys Task 3's `_pair_one_side(direction, mas_key, pt_lookup)` reads via `row.get(mas_key, "")`, and the exact same keys Task 4's UI writes into the row dict passed to `append_mas_score()`. `_pt_lookup_factory(*, direction=None)` (Task 2) is the exact signature Task 3's docstring describes as the intended real caller, though Task 3's own tests use plain lambdas (matching `pair_pt_and_mas()`'s existing test convention) rather than depending on Task 2's factory directly. `mas_flexion_var`/`mas_extension_var` (Task 4) are the only new names introduced there and are used consistently across all 4 of Task 4's new/modified tests.
- **Placeholder scan:** no TBDs; every step shows full replacement code, not a description of it.
