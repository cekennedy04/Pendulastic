# MAS Entry: Stronger-Leg + Notes Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Stronger Leg" dropdown and a "Notes" text box to the MAS score entry form, and extend `mas_scores.csv`'s schema to store them without disrupting the rows already recorded in the live file.

**Architecture:** `mas_validation.py` gains two new constants (`STRONGER_LEG_OPTIONS`, `WIDENABLE_MAS_FIELDS`), a `_valid_stronger_leg()` validator, and `append_mas_score()` gains an allowlisted header-widening path (atomic temp-file + `os.replace` rewrite, matching `pendulastic_storage.save_trial`'s existing pattern) with explicit handling for malformed and empty-but-existing CSVs. `pendulastic_app.py`'s `MasEntryPanel` gains the two form widgets and wires them into `_on_save_clicked`'s row dict and post-save clearing behavior.

**Tech Stack:** Python, Tkinter, `csv` module, pytest.

## Global Constraints

- Do not modify `pendulastic_pt_score.py`, `compute_validation_stats`, or how PT scores/MAS predictions are computed. `stronger_leg`/`notes` are provenance fields only — never read by any stats or figure code.
- `mas_scores.csv` remains append-only from this UI — no edit/delete of existing rows, on this task or any prior one.
- Header widening only ever adds columns in `WIDENABLE_MAS_FIELDS = ["stronger_leg", "notes"]` — an explicit allowlist, never "any key in the row the header lacks." An unrecognized key outside that list falls through to the existing `extrasaction="ignore"` append behavior (silently dropped, exactly like today), never silently promoted to a permanent column.
- A malformed existing CSV row (more fields than the header describes) must raise `ValueError` naming the row and file, with **no** file write attempted — never silently drop the unmapped data during a widen rewrite.
- Any rewrite of `mas_scores.csv` (the widening path) must be atomic: write to `csv_path + ".tmp"`, then `os.replace(tmp_path, csv_path)` — matching `pendulastic_storage.save_trial`'s existing pattern (`pendulastic_storage.py:188-191`) exactly, so this codebase has one atomic-write convention, not two.
- No new migration script — the header widens organically on the first save after this feature ships, per the approved spec.
- Follow existing `MasEntryPanel` conventions: `ws.PALETTE` styling, `ttk.Combobox(state="readonly")` for closed-enum fields (matches `mas_grade`'s dropdown), `tk.Text` for multi-line free text (matches `status_text`'s styling, minus its read-only/scrollbar treatment since Notes is user-editable).

---

### Task 1: `mas_validation.py` — constants and `stronger_leg` validation

**Files:**
- Modify: `mas_validation.py:59-81` (constants block, `_valid_grade`), `mas_validation.py:165-185` (`append_mas_score` — validation only, not widening yet)
- Test: `tests/test_mas_validation.py`

**Interfaces:**
- Produces: `STRONGER_LEG_OPTIONS = ["", "left", "right", "equal"]`, `WIDENABLE_MAS_FIELDS = ["stronger_leg", "notes"]`, `_valid_stronger_leg(value: str) -> bool`, `DEFAULT_MAS_FIELDS` extended to 9 columns. `append_mas_score(row, csv_path=MAS_CSV)` additionally raises `ValueError` if `row.get("stronger_leg", "")` isn't a `STRONGER_LEG_OPTIONS` value — same shape/timing as the existing `mas_grade` check (before any file write). Task 2 consumes `WIDENABLE_MAS_FIELDS` for the widening logic; Task 3/4 (`pendulastic_app.py`) consume `STRONGER_LEG_OPTIONS` for the dropdown's values.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mas_validation.py`, after the existing `test_valid_grade_rejects_anything_else` block (in the "MAS_RANK / _valid_grade" section):

```python
def test_default_mas_fields_includes_new_columns():
    assert mv.DEFAULT_MAS_FIELDS[-2:] == ["stronger_leg", "notes"]


@pytest.mark.parametrize("value", ["", "left", "right", "equal"])
def test_valid_stronger_leg_accepts_all_options(value):
    assert mv._valid_stronger_leg(value)


@pytest.mark.parametrize("value", ["Left", "both", None, "LEFT"])
def test_valid_stronger_leg_rejects_anything_else(value):
    assert not mv._valid_stronger_leg(value)
```

Add to the end of the file (after the existing `test_main_empty_csv_no_crash`):

```python
def test_append_mas_score_rejects_invalid_stronger_leg(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    header = ("participant,leg,condition,diagnosis,mas_grade,assessed_by,"
              "assessed_date,stronger_leg,notes\n")
    csv_path.write_text(header)
    with pytest.raises(ValueError, match="invalid stronger_leg"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "both", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == header
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -k "stronger_leg or default_mas_fields" -v`
Expected: FAIL — `test_default_mas_fields_includes_new_columns` fails on the list slice (still 7 columns); the two `_valid_stronger_leg` tests fail with `AttributeError: module 'mas_validation' has no attribute '_valid_stronger_leg'`; `test_append_mas_score_rejects_invalid_stronger_leg` fails because no `ValueError` is raised (the field is silently ignored today).

- [ ] **Step 3: Add the constants and validator**

In `mas_validation.py`, replace (currently lines 59-73):

```python
# Single source of truth for ordinal MAS coding -- see pendulastic_pt_score.py.
MAS_ORDER = pt.MAS_ORDER
MAS_RANK = pt.MAS_RANK

# Header written when append_mas_score() has to create mas_scores.csv from
# scratch (the file is gitignored, so it's simply absent on a fresh checkout).
# This is the LIVE schema -- `diagnosis`, no `notes` -- which is what the app's
# MAS entry form targets. main()'s "file not found" message still describes the
# original 2026-08-06 column set; that's the CLI's own separate UX and is left
# alone deliberately.
DEFAULT_MAS_FIELDS = ["participant", "leg", "condition", "diagnosis",
                      "mas_grade", "assessed_by", "assessed_date"]

_MIN_N_FOR_CONFIDENCE = 5
_MIN_CLASS_N_FOR_ROC = 3
```

with:

```python
# Single source of truth for ordinal MAS coding -- see pendulastic_pt_score.py.
MAS_ORDER = pt.MAS_ORDER
MAS_RANK = pt.MAS_RANK

# Unlike condition/diagnosis/assessed_by (free text, never validated),
# stronger_leg is a closed enum like mas_grade -- "" means not assessed.
STRONGER_LEG_OPTIONS = ["", "left", "right", "equal"]

# Header written when append_mas_score() has to create mas_scores.csv from
# scratch (the file is gitignored, so it's simply absent on a fresh checkout).
# This is the LIVE schema, including stronger_leg/notes -- what the app's
# MAS entry form targets. main()'s "file not found" message still describes
# the original 2026-08-06 column set; that's the CLI's own separate UX and
# is left alone deliberately.
DEFAULT_MAS_FIELDS = ["participant", "leg", "condition", "diagnosis",
                      "mas_grade", "assessed_by", "assessed_date",
                      "stronger_leg", "notes"]

# append_mas_score() only ever widens mas_scores.csv's header for these two
# fields -- an explicit allowlist, not "any key in row the header lacks".
# Widening on any unrecognized key would let a future typo'd dict key
# permanently become a CSV column; an unrelated stray key still falls
# through to the existing extrasaction="ignore" append behavior instead.
WIDENABLE_MAS_FIELDS = ["stronger_leg", "notes"]

_MIN_N_FOR_CONFIDENCE = 5
_MIN_CLASS_N_FOR_ROC = 3
```

Then find (currently lines 80-81):

```python
def _valid_grade(grade: str) -> bool:
    return grade in MAS_RANK
```

Replace with:

```python
def _valid_grade(grade: str) -> bool:
    return grade in MAS_RANK


def _valid_stronger_leg(value: str) -> bool:
    return value in STRONGER_LEG_OPTIONS
```

- [ ] **Step 4: Add the `stronger_leg` validation check to `append_mas_score`**

In `mas_validation.py`, find (currently lines 165-180):

```python
def append_mas_score(row: dict, csv_path=MAS_CSV) -> None:
    """Appends one clinician MAS assessment to csv_path. Raises ValueError
    (no write attempted) if row["mas_grade"] isn't one of MAS_ORDER. Reads
    the file's own current header rather than assuming a fixed column set,
    so this stays correct even if mas_scores.csv's schema drifts again the
    way it already has once (see module docstring).

    If csv_path doesn't exist yet it's created with the DEFAULT_MAS_FIELDS
    header -- mas_scores.csv is gitignored, so on a fresh checkout the very
    first save would otherwise die with FileNotFoundError."""
    grade = row.get("mas_grade", "")
    if not _valid_grade(grade):
        raise ValueError(f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})")
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=DEFAULT_MAS_FIELDS).writeheader()
```

Replace with:

```python
def append_mas_score(row: dict, csv_path=MAS_CSV) -> None:
    """Appends one clinician MAS assessment to csv_path. Raises ValueError
    (no write attempted) if row["mas_grade"] isn't one of MAS_ORDER, or if
    row["stronger_leg"] is present and isn't one of STRONGER_LEG_OPTIONS.
    Reads the file's own current header rather than assuming a fixed column
    set, so this stays correct even if mas_scores.csv's schema drifts again
    the way it already has once (see module docstring).

    If csv_path doesn't exist yet it's created with the DEFAULT_MAS_FIELDS
    header -- mas_scores.csv is gitignored, so on a fresh checkout the very
    first save would otherwise die with FileNotFoundError."""
    grade = row.get("mas_grade", "")
    if not _valid_grade(grade):
        raise ValueError(f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})")
    stronger_leg = row.get("stronger_leg", "")
    if not _valid_stronger_leg(stronger_leg):
        raise ValueError(
            f"invalid stronger_leg {stronger_leg!r} (must be one of {STRONGER_LEG_OPTIONS})")
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=DEFAULT_MAS_FIELDS).writeheader()
```

(The rest of `append_mas_score` — the header read and the append — is unchanged in this task; Task 2 replaces it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -k "stronger_leg or default_mas_fields" -v`
Expected: PASS (10 tests: `test_default_mas_fields_includes_new_columns` [1] + `test_valid_stronger_leg_accepts_all_options` [4 parametrized cases] + `test_valid_stronger_leg_rejects_anything_else` [4 parametrized cases] + `test_append_mas_score_rejects_invalid_stronger_leg` [1])

- [ ] **Step 6: Run the full mas_validation test suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -v`
Expected: PASS (all tests, including the new ones)

- [ ] **Step 7: Commit**

```bash
git add mas_validation.py tests/test_mas_validation.py
git commit -m "feat: add stronger_leg field constants and validation to mas_validation.py"
```

---

### Task 2: `mas_validation.py` — allowlisted header widening

**Files:**
- Modify: `mas_validation.py` (`append_mas_score`, from Task 1)
- Test: `tests/test_mas_validation.py`

**Interfaces:**
- Consumes: `WIDENABLE_MAS_FIELDS`, `DEFAULT_MAS_FIELDS` (Task 1). `os.replace`, `csv.DictReader`/`DictWriter` (stdlib).
- Produces: `append_mas_score()` widens `csv_path`'s header (atomic rewrite) exactly when `row` has a `WIDENABLE_MAS_FIELDS` key not yet in the header; otherwise behaves as today (plain append, `extrasaction="ignore"` for any other unrecognized key). Raises `ValueError` (no write) if an existing row has more fields than the header. A new private helper `_atomic_write_mas_csv(csv_path, fieldnames, existing_rows, new_row) -> None` is introduced and used by both the empty-file and normal-widen code paths.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mas_validation.py`, after `test_append_mas_score_round_trips_through_load_mas_scores` (in the "append_mas_score" section):

```python
def test_append_mas_score_widens_header_when_row_has_new_fields(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,right,pre,multiple sclerosis,1,VL,2026-08-01\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1",
         "assessed_by": "VL", "assessed_date": "2026-08-07",
         "stronger_leg": "right", "notes": "some notes"},
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[0] == ("participant,leg,condition,diagnosis,mas_grade,assessed_by,"
                        "assessed_date,stronger_leg,notes")
    assert lines[1] == "13,right,pre,multiple sclerosis,1,VL,2026-08-01,,"
    assert lines[2] == "20,left,pre,multiple sclerosis,1,VL,2026-08-07,right,some notes"
    rows = mv.load_mas_scores(str(csv_path))
    assert len(rows) == 2
    assert rows[1]["stronger_leg"] == "right"
    assert rows[1]["notes"] == "some notes"


def test_append_mas_score_widening_is_atomic_on_replace_failure(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    original = ("participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
               "13,right,pre,multiple sclerosis,1,VL,2026-08-01\n")
    csv_path.write_text(original)

    def raise_replace(src, dst):
        raise OSError("simulated failure")
    monkeypatch.setattr(mv.os, "replace", raise_replace)

    with pytest.raises(OSError):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "right", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == original


def test_append_mas_score_widening_is_atomic_on_write_failure(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    original = ("participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
               "13,right,pre,multiple sclerosis,1,VL,2026-08-01\n")
    csv_path.write_text(original)

    real_open = open
    def failing_open(path, *a, **kw):
        if str(path).endswith(".tmp"):
            raise OSError("simulated disk full")
        return real_open(path, *a, **kw)
    monkeypatch.setattr(mv, "open", failing_open, raising=False)

    replace_calls = []
    monkeypatch.setattr(mv.os, "replace", lambda *a: replace_calls.append(a))

    with pytest.raises(OSError):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "right", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == original
    assert replace_calls == []


def test_append_mas_score_no_widen_when_row_keys_are_subset_of_header(tmp_path, monkeypatch):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,"
        "assessed_date,stronger_leg,notes\n")
    replace_calls = []
    monkeypatch.setattr(mv.os, "replace", lambda *a: replace_calls.append(a))
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
         "mas_grade": "1", "assessed_by": "", "assessed_date": "",
         "stronger_leg": "", "notes": ""},
        csv_path=str(csv_path))
    assert replace_calls == []
    assert not os.path.exists(str(csv_path) + ".tmp")


def test_append_mas_score_ignores_unrecognized_keys_without_widening(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
         "mas_grade": "1", "assessed_by": "", "assessed_date": "",
         "stronger_le": "right"},  # typo'd key -- not in WIDENABLE_MAS_FIELDS
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[0] == "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date"
    assert len(lines) == 2
    assert "stronger_le" not in lines[0]
    assert "right" not in lines[1]


def test_append_mas_score_raises_on_malformed_existing_row(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    original = (
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n"
        "13,right,pre,multiple sclerosis,1,VL,2026-08-01,extra,cells,here\n")
    csv_path.write_text(original)
    with pytest.raises(ValueError, match="row 2"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "", "diagnosis": "",
             "mas_grade": "1", "assessed_by": "", "assessed_date": "",
             "stronger_leg": "right", "notes": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text() == original


def test_append_mas_score_widens_empty_file(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text("")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1",
         "assessed_by": "VL", "assessed_date": "2026-08-07",
         "stronger_leg": "right", "notes": "some notes"},
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[0] == ",".join(mv.DEFAULT_MAS_FIELDS)
    assert len(lines) == 2
    rows = mv.load_mas_scores(str(csv_path))
    assert len(rows) == 1
    assert rows[0]["stronger_leg"] == "right"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -k "widen or malformed" -v`
Expected: FAIL — `test_append_mas_score_widens_header_when_row_has_new_fields` fails because `stronger_leg`/`notes` are silently dropped by today's `extrasaction="ignore"` (header stays 7 columns); the atomicity tests fail because no rewrite path exists yet to hit `os.replace`; `test_append_mas_score_raises_on_malformed_existing_row` fails because no `ValueError` is raised; `test_append_mas_score_widens_empty_file` fails with a `csv.Error` or similar from `DictWriter(fieldnames=None, ...)`.

- [ ] **Step 3: Replace the rest of `append_mas_score` and add `_atomic_write_mas_csv`**

In `mas_validation.py`, find the remainder of `append_mas_score` (currently the lines right after the Task 1 `if not os.path.exists(csv_path):` block):

```python
    with open(csv_path, newline="", encoding="utf-8") as f:
        fieldnames = csv.DictReader(f).fieldnames
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writerow(row)


def _tokenize_condition(text):
```

Replace with:

```python
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        existing_rows = list(reader) if fieldnames else []

    if not fieldnames:
        # Zero-byte file that exists but never got a header written -- e.g.
        # an earlier run crashed between creating the file and writing the
        # header. Nothing to preserve; start from the canonical schema.
        widened = list(DEFAULT_MAS_FIELDS)
        for k in row:
            if k not in widened:
                widened.append(k)
        _atomic_write_mas_csv(csv_path, widened, [], row)
        return

    new_fields = [k for k in WIDENABLE_MAS_FIELDS
                 if k in row and k not in fieldnames]
    if not new_fields:
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writerow(row)
        return

    for i, existing in enumerate(existing_rows, start=2):  # row 1 is the header
        if None in existing:
            raise ValueError(
                f"{csv_path}: row {i} has more fields than the header "
                f"({len(fieldnames)} columns) -- fix this row by hand before "
                f"stronger_leg/notes can be added automatically")

    widened = list(fieldnames) + new_fields
    _atomic_write_mas_csv(csv_path, widened, existing_rows, row)


def _atomic_write_mas_csv(csv_path, fieldnames, existing_rows, new_row):
    """Writes header + existing_rows + new_row to csv_path via a temp file
    + os.replace -- matches pendulastic_storage.save_trial's pattern, so a
    crash mid-write can't corrupt csv_path (either the pre-write file or
    the fully-written new one is on disk, never a partial one). The temp
    file is always opened in "w" mode, so a stale .tmp left over from an
    earlier crashed run is overwritten from scratch, not appended to."""
    tmp_path = csv_path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing_rows)
        writer.writerow(new_row)
    os.replace(tmp_path, csv_path)


def _tokenize_condition(text):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -k "widen or malformed" -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full mas_validation test suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add mas_validation.py tests/test_mas_validation.py
git commit -m "feat: add allowlisted header widening to append_mas_score()"
```

---

### Task 3: `pendulastic_app.py` — Stronger Leg and Notes form widgets

**Files:**
- Modify: `pendulastic_app.py` (`MasEntryPanel._build_widgets`)
- Test: none (widget-construction-only; covered indirectly by Task 4's tests once these widgets are wired to Save)

**Interfaces:**
- Consumes: `_mas_validation.STRONGER_LEG_OPTIONS` (Task 1).
- Produces: `MasEntryPanel` gains `self.stronger_leg_var` (`tk.StringVar`) and `self.notes_text` (`tk.Text`). No behavior change yet — Task 4 wires these into `_on_save_clicked`.

- [ ] **Step 1: Insert the Stronger Leg dropdown and renumber the grid rows below it**

In `pendulastic_app.py`, find the `MasEntryPanel._build_widgets` block from "Leg:" through "Assessed Date:" (currently):

```python
        tk.Label(form, text="Leg:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=1, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Left")
        leg_f = tk.Frame(form, bg=ws.PALETTE["BG"])
        leg_f.grid(row=1, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left", variable=self.leg_var, value="Left",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)

        tk.Label(form, text="Condition:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=2, column=0, sticky="e", **pad)
        self.condition_var = tk.StringVar()
        tk.Entry(form, textvariable=self.condition_var, width=22).grid(
            row=2, column=1, sticky="w", **pad)

        tk.Label(form, text="Diagnosis:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=3, column=0, sticky="e", **pad)
        self.diagnosis_var = tk.StringVar()
        tk.Entry(form, textvariable=self.diagnosis_var, width=22).grid(
            row=3, column=1, sticky="w", **pad)

        tk.Label(form, text="MAS Grade:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=4, column=0, sticky="e", **pad)
        self.mas_grade_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_grade_var, width=19,
                    state="readonly",
                    values=list(_mas_validation.MAS_ORDER)).grid(
            row=4, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed By:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=5, column=0, sticky="e", **pad)
        self.assessed_by_var = tk.StringVar()
        tk.Entry(form, textvariable=self.assessed_by_var, width=22).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed Date:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=6, column=0, sticky="e", **pad)
        self.assessed_date_var = tk.StringVar(
            value=_datetime.date.today().isoformat())
        tk.Entry(form, textvariable=self.assessed_date_var, width=22).grid(
            row=6, column=1, sticky="w", **pad)
```

Replace with:

```python
        tk.Label(form, text="Leg:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=1, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Left")
        leg_f = tk.Frame(form, bg=ws.PALETTE["BG"])
        leg_f.grid(row=1, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left", variable=self.leg_var, value="Left",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)

        tk.Label(form, text="Stronger Leg:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=2, column=0, sticky="e", **pad)
        self.stronger_leg_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.stronger_leg_var, width=19,
                    state="readonly",
                    values=list(_mas_validation.STRONGER_LEG_OPTIONS)).grid(
            row=2, column=1, sticky="w", **pad)

        tk.Label(form, text="Condition:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=3, column=0, sticky="e", **pad)
        self.condition_var = tk.StringVar()
        tk.Entry(form, textvariable=self.condition_var, width=22).grid(
            row=3, column=1, sticky="w", **pad)

        tk.Label(form, text="Diagnosis:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=4, column=0, sticky="e", **pad)
        self.diagnosis_var = tk.StringVar()
        tk.Entry(form, textvariable=self.diagnosis_var, width=22).grid(
            row=4, column=1, sticky="w", **pad)

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

- [ ] **Step 2: Verify the app still launches and the new fields render**

Run: `.venv\Scripts\python.exe -c "import pendulastic_app"`
Expected: no import errors (this only proves the syntax/imports are valid; the widget layout itself is visually verified in Task 4's tests, which exercise `stronger_leg_var`/`notes_text` directly, and by the running app).

- [ ] **Step 3: Run the full test_app.py suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: PASS (all tests — nothing references the old row numbers for Condition/Diagnosis/MAS Grade/Assessed By/Assessed Date by grid position, only by StringVar name, so renumbering the grid rows doesn't break any existing test)

- [ ] **Step 4: Commit**

```bash
git add pendulastic_app.py
git commit -m "feat: add Stronger Leg and Notes fields to MasEntryPanel form"
```

---

### Task 4: `pendulastic_app.py` — wire the new fields into Save

**Files:**
- Modify: `pendulastic_app.py` (`MasEntryPanel._on_save_clicked`, from Task 3)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `self.stronger_leg_var`, `self.notes_text` (Task 3).
- Produces: `_on_save_clicked`'s row dict gains `"stronger_leg"` and `"notes"` keys. On successful save, `notes_text` is cleared (`delete("1.0", "end")`) alongside the existing `mas_grade_var.set("")`; `stronger_leg_var` is **not** cleared.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`, after the existing MAS entry Save/Export tests:

```python
def test_mas_entry_panel_save_includes_stronger_leg_and_notes(monkeypatch):
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
        app._mas_entry.stronger_leg_var.set("right")
        app._mas_entry.notes_text.insert("1.0", "gait looked steadier today")
        app._mas_entry._on_save_clicked()
        app.update()
        assert len(append_calls) == 1
        assert append_calls[0]["stronger_leg"] == "right"
        assert append_calls[0]["notes"] == "gait looked steadier today"
    finally:
        app.destroy()


def test_mas_entry_panel_save_clears_notes_but_not_stronger_leg(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "append_mas_score", lambda row, **kw: None)
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [])
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry.stronger_leg_var.set("right")
        app._mas_entry.notes_text.insert("1.0", "some notes")
        app._mas_entry._on_save_clicked()
        app.update()
        assert app._mas_entry.notes_text.get("1.0", "end").strip() == ""
        assert app._mas_entry.stronger_leg_var.get() == "right"
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "stronger_leg_and_notes or clears_notes" -v`
Expected: FAIL — the row dict doesn't have `stronger_leg`/`notes` keys yet, so the first test's assertions raise `KeyError`; the second test's `notes_text` is never touched by `_on_save_clicked` yet, so it still contains "some notes" after save.

- [ ] **Step 3: Wire the two fields into `_on_save_clicked`**

In `pendulastic_app.py`, find `MasEntryPanel._on_save_clicked` (currently):

```python
    def _on_save_clicked(self) -> None:
        participant = self.pid_var.get().strip()
        mas_grade = self.mas_grade_var.get().strip()
        if not participant or not mas_grade:
            self._set_feedback("Participant ID and MAS grade are required.")
            return
        row = {
            "participant": participant,
            "leg": self.leg_var.get().lower(),
            "condition": self.condition_var.get().strip(),
            "diagnosis": self.diagnosis_var.get().strip(),
            "mas_grade": mas_grade,
            "assessed_by": self.assessed_by_var.get().strip(),
            "assessed_date": self.assessed_date_var.get().strip(),
        }
        try:
            _mas_validation.append_mas_score(row)
        except ValueError as e:
            self._set_feedback(str(e))
            return
        except Exception as e:
            self._set_feedback(f"Could not save: {e}")
            return
        # The form is deliberately not cleared (batch entry of both legs keeps
        # participant/condition/date), but with no confirmation a clinician
        # unsure the click registered would click Save again and append an
        # identical duplicate row, biasing the Spearman/kappa stats. Confirm,
        # and clear the one field that must change between consecutive rows so
        # a resubmit takes a deliberate re-selection.
        self._set_feedback(f"Saved {participant} {row['leg']} / {mas_grade}.", ok=True)
        self.mas_grade_var.set("")
        self.refresh()
```

Replace with:

```python
    def _on_save_clicked(self) -> None:
        participant = self.pid_var.get().strip()
        mas_grade = self.mas_grade_var.get().strip()
        if not participant or not mas_grade:
            self._set_feedback("Participant ID and MAS grade are required.")
            return
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
        try:
            _mas_validation.append_mas_score(row)
        except ValueError as e:
            self._set_feedback(str(e))
            return
        except Exception as e:
            self._set_feedback(f"Could not save: {e}")
            return
        # The form is deliberately not cleared (batch entry of both legs keeps
        # participant/condition/date), but with no confirmation a clinician
        # unsure the click registered would click Save again and append an
        # identical duplicate row, biasing the Spearman/kappa stats. Confirm,
        # and clear the fields that must change between consecutive rows so a
        # resubmit takes a deliberate re-selection: mas_grade (existing) and
        # notes (specific to this one observation) -- unlike stronger_leg,
        # which typically holds across both legs' rows for the same session.
        self._set_feedback(f"Saved {participant} {row['leg']} / {mas_grade}.", ok=True)
        self.mas_grade_var.set("")
        self.notes_text.delete("1.0", "end")
        self.refresh()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "stronger_leg_and_notes or clears_notes" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py tests/test_app.py -v`
Expected: PASS (all tests; the only acceptable failures are the pre-existing, order-dependent Tcl/Tk resource-contention flakes documented earlier in this project's history — re-run any failure in isolation to confirm before treating it as a regression)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: wire Stronger Leg and Notes fields into MasEntryPanel Save"
```

## Self-Review Notes

- **Spec coverage:** §5 schema -> Task 1/2 (`DEFAULT_MAS_FIELDS`). §6.2 `STRONGER_LEG_OPTIONS`/validation -> Task 1. §6.3 widening (allowlist, malformed-row, empty-file, atomic write) -> Task 2. §7.1 form fields -> Task 3. §7.2 `_on_save_clicked`/clearing -> Task 4. §8 error handling -> Task 1 (validation ordering) + Task 2 (malformed/empty-file/atomic-failure paths). §9 testing -> one task step per spec test bullet, no gaps. §11's adopted Codex findings (allowlist, malformed-row handling, empty-file handling, `stronger_leg` validation, write-failure test) are all present in Task 1/2; the findings §11 explicitly did NOT adopt (locking/fsync, `.strip()`, carryover, layout) are correctly absent from this plan too.
- **Placeholder scan:** none — every step has literal, complete code. Task 3 intentionally builds widgets with no behavior yet (Task 4 wires them) — this mirrors the original MAS entry plan's own precedent (build skeleton, wire later) and is explicitly noted as safe in Task 3's Interfaces block, not a silent gap.
- **Type consistency:** `_valid_stronger_leg(value: str) -> bool` (Task 1) is called with `row.get("stronger_leg", "")` (always a `str`) in both Task 1's validation-only version and Task 2's full version of `append_mas_score`. `_atomic_write_mas_csv(csv_path, fieldnames, existing_rows, new_row)` (Task 2) is called with matching argument order from both its call sites (empty-file case, normal-widen case) introduced in the same task — no drift between them.
