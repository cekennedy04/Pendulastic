# Trial Exclusion UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator a way to mark a recorded trial non-viable directly from `pendulastic_app.py`'s `AnalysisPanel`, instead of hand-editing `excluded_trials.json`.

**Architecture:** `pt_report_common.py` gets three additions — a keyword-only `include_excluded` parameter on `discover_all_trials()`/`list_participants()` (default-off, byte-identical existing behavior when omitted), a pure `duplicate_trial_keys()` function, and a new `set_trials_excluded()` batch setter with a corruption-safe atomic write (`RegistryCorruptError` on a malformed on-disk registry, never silently overwritten). `pendulastic_app.py`'s `AnalysisPanel` gains a second "view mode" for its right-side figure viewer: selecting exactly one participant in the sidebar swaps the matplotlib canvas for a `ttk.Treeview` of that participant's trials (loaded on a background thread via its own queue, request-id-gated against rapid re-selection), with a "Toggle Excluded" button that batch-flips selected rows through `set_trials_excluded()`.

**Tech Stack:** Python 3.13, Tkinter/`ttk`, `pytest` with headless `tk.Tk()` + `.withdraw()` roots (existing convention in `tests/test_analysis_panel.py`), plain-function tests with `tmp_path`/`monkeypatch` for `pt_report_common.py` (existing convention in `tests/test_pt_report_common.py`, no test classes).

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-07-trial-exclusion-ui-design.md` — went through 3 Codex review rounds, all findings folded into the spec text already. No further design iteration needed; implement as specified.
- **`discover_all_trials(include_excluded=False)` (the default) must stay byte-for-byte identical to today** — no new fields on returned records, excluded trials still silently dropped. Every existing caller (`run_pt_analysis.py`, `pt_cohort_common.py`, `batch_imu_vs_optitrack_rmse.py`, `sweep_mediapipe_config.py`, `plot_multisource_trials.py`, `web/api/routers/participants.py`) calls with no `include_excluded` argument and must keep working unchanged.
- **`set_trials_excluded()` is the only write path the UI uses.** Do not route the UI through the existing `add_excluded_trial()`/`clear_excluded_trial()` single-key setters (still used by `pendulastic_workbench.py` — leave those and their non-atomic-in-the-mkstemp-sense `_atomic_write_json()` helper untouched).
- **A write path must never treat a corrupt/wrong-shape `excluded_trials.json` as empty and overwrite it.** `set_trials_excluded()` raises `RegistryCorruptError` and touches nothing on disk in that case. This is deliberately stricter than `load_excluded_trials()`'s read-time behavior (unchanged, still returns `{}` on any failure) — the asymmetry is intentional (spec §6).
- **`AnalysisPanel`'s existing Generate flow (background thread + `_result_queue`) is untouched in shape** — the new trial-load flow gets its own `_table_queue` and its own `request_id` counter (`_table_request_id`), never reusing `_result_queue`.
- **Never destroy `_viewer_canvas` or the current Matplotlib figure when switching to/from the table view** — `grid_remove()`/`grid()` only. The canvas's `vbar`/`hbar` scrollbars (currently local variables) must be promoted to `self._viewer_vbar`/`self._viewer_hbar` and hidden/shown in lockstep with the canvas.
- **`pendulastic_workbench.py`, `pendulastic_viewer.py`, and `workbench_engine.py` are not modified.**

---

### Task 1: `include_excluded` on `discover_all_trials()`/`list_participants()` + per-record discovery-failure isolation

**Files:**
- Modify: `pt_report_common.py:683-721` (`discover_all_trials`, `list_participants`)
- Test: `tests/test_pt_report_common.py` (append)

**Interfaces:**
- Produces: `discover_all_trials(include_archive=True, *, include_excluded=False)` — `include_excluded=True` adds `trial_key: str` and `excluded: bool` to every record and includes excluded trials; `include_excluded=False` (default) is unchanged. `list_participants(include_archive=True, *, include_excluded=False)` — `include_excluded=True` makes a participant whose every trial is excluded still appear, with `n_trials: 0`.
- Consumes: `_parse_trial_path`, `trial_key`, `load_excluded_trials` (all existing, unchanged signatures).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_report_common.py`:

```python
def test_discover_all_trials_default_shape_unchanged(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    records = common.discover_all_trials(include_archive=False)
    assert len(records) == 1
    assert "trial_key" not in records[0]
    assert "excluded" not in records[0]


def test_discover_all_trials_include_excluded_adds_fields_and_keeps_excluded(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    (rec_dir / "trial_2_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    records = common.discover_all_trials(include_archive=False, include_excluded=True)
    assert len(records) == 2
    by_trial = {r["trial"]: r for r in records}
    assert by_trial["1"]["excluded"] is True
    assert by_trial["1"]["trial_key"] == common.trial_key("13", "left", "pre", "1")
    assert by_trial["2"]["excluded"] is False

    # Default (include_excluded=False) still drops the excluded trial entirely.
    records_default = common.discover_all_trials(include_archive=False)
    assert len(records_default) == 1
    assert records_default[0]["trial"] == "2"


def test_discover_all_trials_skips_record_whose_getmtime_raises(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    good = rec_dir / "trial_1_optitrack.csv"
    good.write_text("t,angle\n0,180\n")
    bad = rec_dir / "trial_2_optitrack.csv"
    bad.write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials", lambda: {})

    real_getmtime = os.path.getmtime

    def flaky_getmtime(path):
        if path == str(bad):
            raise OSError("deleted mid-scan")
        return real_getmtime(path)

    monkeypatch.setattr(common.os.path, "getmtime", flaky_getmtime)

    records = common.discover_all_trials(include_archive=False)
    assert len(records) == 1
    assert records[0]["trial"] == "1"


def test_list_participants_default_hides_fully_excluded_participant(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    assert common.list_participants(include_archive=False) == {}


def test_list_participants_include_excluded_shows_zero_trial_participant(tmp_path, monkeypatch):
    rec_dir = tmp_path / "Participant_13_left_pre"
    rec_dir.mkdir(parents=True)
    (rec_dir / "trial_1_optitrack.csv").write_text("t,angle\n0,180\n")
    monkeypatch.setattr(common, "OPTI_ROOT", str(tmp_path))
    monkeypatch.setattr(common, "ARCHIVE_ROOT", "/nonexistent")
    monkeypatch.setattr(common, "load_excluded_trials",
                        lambda: {"13_left_pre_T1": "active muscle intervention"})

    result = common.list_participants(include_archive=False, include_excluded=True)
    assert result == {"13": {"legs": set(), "conditions": set(), "n_trials": 0}}
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k "discover_all_trials_default_shape_unchanged or discover_all_trials_include_excluded or discover_all_trials_skips_record or list_participants_default_hides or list_participants_include_excluded" -v
```

Expected: FAIL — `discover_all_trials()` has no `include_excluded` parameter (`TypeError`), and `list_participants()` likewise.

- [ ] **Step 3: Replace `discover_all_trials` and `list_participants`**

In `pt_report_common.py`, replace lines 683-721:

```python
# OLD
def discover_all_trials(include_archive=True):
    """Every trial_*_optitrack.csv under the live repo (and, optionally, the
    known archive) parsed into {participant, leg, condition, trial, path}
    records. Quarantined/invalid data (INVALID_ in the path) is excluded,
    as is any trial listed in excluded_trials.json (non-viable recordings,
    e.g. active muscle intervention during the swing)."""
    excluded = load_excluded_trials()
    records = []
    seen = set()
    roots = [OPTI_ROOT] + ([ARCHIVE_ROOT] if include_archive and os.path.isdir(ARCHIVE_ROOT) else [])
    for root in roots:
        for csv_path in glob.glob(os.path.join(root, "**", "trial_*_optitrack.csv"), recursive=True):
            if "INVALID" in csv_path.upper():
                continue
            real = os.path.realpath(csv_path)
            if real in seen:
                continue
            seen.add(real)
            rec = _parse_trial_path(csv_path, root)
            if rec is None:
                continue
            key = trial_key(rec["participant"], rec["leg"], rec["condition"], rec["trial"])
            if key in excluded:
                continue
            records.append(rec)
    return records


def list_participants(include_archive=True):
    """{participant_id: {"legs": {...}, "n_trials": int, "conditions": [...]}}
    sorted by participant id, for populating a UI picker."""
    records = discover_all_trials(include_archive=include_archive)
    by_pid = {}
    for r in records:
        entry = by_pid.setdefault(r["participant"], {"legs": set(), "conditions": set(), "n_trials": 0})
        entry["legs"].add(r["leg"])
        entry["conditions"].add(r["condition"])
        entry["n_trials"] += 1
    return dict(sorted(by_pid.items(), key=lambda kv: int(kv[0])))
```

```python
# NEW
def discover_all_trials(include_archive=True, *, include_excluded=False):
    """Every trial_*_optitrack.csv under the live repo (and, optionally, the
    known archive) parsed into {participant, leg, condition, trial, path}
    records. Quarantined/invalid data (INVALID_ in the path) is always
    excluded.

    include_excluded=False (default): trials listed in excluded_trials.json
    are dropped, and records carry no trial_key/excluded fields --
    byte-for-byte the shape every existing caller has always gotten.
    include_excluded=True: excluded trials are included too, and every
    returned record additionally carries trial_key (str) and excluded
    (bool) -- used by AnalysisPanel's trial-exclusion UI (design spec
    docs/superpowers/specs/2026-08-07-trial-exclusion-ui-design.md Section 3)."""
    excluded = load_excluded_trials()
    records = []
    seen = set()
    roots = [OPTI_ROOT] + ([ARCHIVE_ROOT] if include_archive and os.path.isdir(ARCHIVE_ROOT) else [])
    for root in roots:
        for csv_path in glob.glob(os.path.join(root, "**", "trial_*_optitrack.csv"), recursive=True):
            if "INVALID" in csv_path.upper():
                continue
            real = os.path.realpath(csv_path)
            if real in seen:
                continue
            seen.add(real)
            try:
                rec = _parse_trial_path(csv_path, root)
            except OSError:
                # _parse_trial_path() calls os.path.getmtime() uncaught --
                # a deleted/inaccessible file must only drop this one
                # record, not abort the whole discovery call.
                continue
            if rec is None:
                continue
            key = trial_key(rec["participant"], rec["leg"], rec["condition"], rec["trial"])
            is_excluded = key in excluded
            if is_excluded and not include_excluded:
                continue
            if include_excluded:
                rec = dict(rec, trial_key=key, excluded=is_excluded)
            records.append(rec)
    return records


def list_participants(include_archive=True, *, include_excluded=False):
    """{participant_id: {"legs": {...}, "n_trials": int, "conditions": [...]}}
    sorted by participant id, for populating a UI picker.

    include_excluded=False (default, unchanged for every existing caller): a
    participant whose every trial is excluded doesn't appear at all.
    include_excluded=True: such a participant still appears, with
    n_trials == 0 (excluded trials aren't counted into legs/conditions/
    n_trials either), so AnalysisPanel can flag and let the operator
    re-select/undo them."""
    records = discover_all_trials(include_archive=include_archive, include_excluded=include_excluded)
    by_pid = {}
    for r in records:
        entry = by_pid.setdefault(r["participant"], {"legs": set(), "conditions": set(), "n_trials": 0})
        if r.get("excluded"):
            continue
        entry["legs"].add(r["leg"])
        entry["conditions"].add(r["condition"])
        entry["n_trials"] += 1
    return dict(sorted(by_pid.items(), key=lambda kv: int(kv[0])))
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k "discover_all_trials_default_shape_unchanged or discover_all_trials_include_excluded or discover_all_trials_skips_record or list_participants_default_hides or list_participants_include_excluded" -v
```

Expected: all 5 PASS

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\ -v
```

Expected: same pass/fail counts as before this change — every existing `discover_all_trials()`/`list_participants()` caller passes no `include_excluded` argument and sees identical behavior.

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add include_excluded param to discover_all_trials/list_participants"
```

---

### Task 2: `duplicate_trial_keys()`

**Files:**
- Modify: `pt_report_common.py` (add near `trial_key`, e.g. after `list_participants`)
- Test: `tests/test_pt_report_common.py` (append)

**Interfaces:**
- Produces: `duplicate_trial_keys(records: list) -> dict` — `{trial_key: [path, ...]}` for every key shared by more than one record among the given list.
- Consumes: nothing new — operates purely on `dict` records with `"trial_key"`/`"path"` keys (the shape `discover_all_trials(include_excluded=True)` produces, per Task 1).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_report_common.py`:

```python
def test_duplicate_trial_keys_empty_for_common_case():
    records = [
        {"trial_key": "13_left_pre_T1", "path": "/a/trial_1.csv"},
        {"trial_key": "13_left_pre_T2", "path": "/a/trial_2.csv"},
    ]
    assert common.duplicate_trial_keys(records) == {}


def test_duplicate_trial_keys_finds_collision():
    records = [
        {"trial_key": "13_left_pre_T1", "path": "/a/trial_1.csv"},
        {"trial_key": "13_left_pre_T1", "path": "/a_dup/trial_1.csv"},
        {"trial_key": "13_left_pre_T2", "path": "/a/trial_2.csv"},
    ]
    assert common.duplicate_trial_keys(records) == {
        "13_left_pre_T1": ["/a/trial_1.csv", "/a_dup/trial_1.csv"],
    }


def test_duplicate_trial_keys_catches_excluded_and_nonexcluded_collision():
    records = [
        {"trial_key": "13_left_pre_T1", "path": "/a/trial_1.csv", "excluded": True},
        {"trial_key": "13_left_pre_T1", "path": "/a_dup/trial_1.csv", "excluded": False},
    ]
    dupes = common.duplicate_trial_keys(records)
    assert set(dupes["13_left_pre_T1"]) == {"/a/trial_1.csv", "/a_dup/trial_1.csv"}


def test_duplicate_trial_keys_no_internal_discovery_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("duplicate_trial_keys must not call discover_all_trials")
    monkeypatch.setattr(common, "discover_all_trials", boom)
    assert common.duplicate_trial_keys([{"trial_key": "k", "path": "/p"}]) == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k duplicate_trial_keys -v
```

Expected: FAIL — `AttributeError: module 'pt_report_common' has no attribute 'duplicate_trial_keys'`.

- [ ] **Step 3: Add `duplicate_trial_keys`**

In `pt_report_common.py`, add this function directly after `list_participants`:

```python
def duplicate_trial_keys(records: list) -> dict:
    """{trial_key: [path, ...]} for every trial_key shared by more than one
    record in the given list. A pure function over an already-fetched
    records list -- never calls discover_all_trials() itself, so the caller
    must pass it the exact same list a table/report was already built from
    (design spec Section 3): duplicate detection can't disagree with what's
    on screen, and it works whether or not the caller filtered to a single
    participant first."""
    by_key = {}
    for r in records:
        by_key.setdefault(r["trial_key"], []).append(r["path"])
    return {k: paths for k, paths in by_key.items() if len(paths) > 1}
```

- [ ] **Step 4: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k duplicate_trial_keys -v
```

Expected: all 4 PASS

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\ -v
```

Expected: same pass/fail counts as Task 1's baseline plus the 4 new passes.

- [ ] **Step 6: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add duplicate_trial_keys pure function"
```

---

### Task 3: `RegistryCorruptError` + `set_trials_excluded()`

**Files:**
- Modify: `pt_report_common.py:12-17` (imports), `pt_report_common.py` (add near `clear_excluded_trial`)
- Test: `tests/test_pt_report_common.py` (append)

**Interfaces:**
- Produces: `RegistryCorruptError(Exception)`. `set_trials_excluded(keys: list, excluded: bool) -> None` — deduplicates `keys`, batch-sets or removes each in `excluded_trials.json`, atomic write via `tempfile.mkstemp` + `os.replace`, raises `RegistryCorruptError` (touching nothing on disk) if the existing file is unparseable or not a `{str: str}` dict.
- Consumes: `EXCLUDED_TRIALS_PATH` (existing).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_report_common.py`:

```python
def test_set_trials_excluded_dedupes_duplicate_input_keys(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    common.set_trials_excluded(["k1", "k1"], True)

    with open(reg_path) as f:
        data = json.load(f)
    assert data == {"k1": "excluded via Analysis panel"}


def test_set_trials_excluded_true_then_false_round_trips(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    common.set_trials_excluded(["k1"], True)
    assert "k1" in common.load_excluded_trials()

    common.set_trials_excluded(["k1"], False)
    assert "k1" not in common.load_excluded_trials()


def test_set_trials_excluded_preserves_unrelated_entries(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps({"other_key": "pre-existing reason"}))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    common.set_trials_excluded(["k1"], True)

    data = common.load_excluded_trials()
    assert data["other_key"] == "pre-existing reason"
    assert data["k1"] == "excluded via Analysis panel"


def test_set_trials_excluded_atomic_write_uses_same_directory(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    seen_dirs = []
    real_mkstemp = common.tempfile.mkstemp

    def spy_mkstemp(*args, **kwargs):
        seen_dirs.append(kwargs.get("dir"))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(common.tempfile, "mkstemp", spy_mkstemp)
    common.set_trials_excluded(["k1"], True)
    assert seen_dirs == [str(tmp_path)]


def test_set_trials_excluded_cleans_up_temp_file_on_replace_failure(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))

    def failing_replace(*args, **kwargs):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(common.os, "replace", failing_replace)
    with pytest.raises(OSError):
        common.set_trials_excluded(["k1"], True)

    assert not reg_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_set_trials_excluded_raises_on_malformed_json(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text("{not valid json")
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    original_bytes = reg_path.read_bytes()

    with pytest.raises(common.RegistryCorruptError):
        common.set_trials_excluded(["k1"], True)

    assert reg_path.read_bytes() == original_bytes
    # Read path (load_excluded_trials) still degrades to {} unchanged --
    # the two paths intentionally diverge (spec Section 6).
    assert common.load_excluded_trials() == {}


def test_set_trials_excluded_raises_on_wrong_shape_json(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps(["not", "a", "dict"]))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    original_bytes = reg_path.read_bytes()

    with pytest.raises(common.RegistryCorruptError):
        common.set_trials_excluded(["k1"], True)

    assert reg_path.read_bytes() == original_bytes
```

Add `import pytest` and `import json` to the top of `tests/test_pt_report_common.py` if not already present (check first — `json` may need adding, `pytest` is implicitly available but not currently imported by name in this file since no other test uses `pytest.raises`).

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k set_trials_excluded -v
```

Expected: FAIL — `AttributeError: module 'pt_report_common' has no attribute 'set_trials_excluded'`.

- [ ] **Step 3: Add `import tempfile`**

In `pt_report_common.py`, replace line 15 (`import os`) 's surrounding block:

```python
# OLD (lines 12-17)
import csv
import glob
import json
import os
import re
import sys
```

```python
# NEW
import csv
import glob
import json
import os
import re
import sys
import tempfile
```

- [ ] **Step 4: Add `RegistryCorruptError` and `set_trials_excluded`**

In `pt_report_common.py`, add this directly after `clear_excluded_trial` (which currently ends the `add_excluded_trial`/`clear_excluded_trial` pair):

```python
class RegistryCorruptError(Exception):
    """excluded_trials.json exists but isn't a valid {str: str} JSON object
    -- raised by set_trials_excluded() to refuse writing through it. A write
    path must never silently treat a corrupt/wrong-shape file as empty and
    overwrite it, unlike load_excluded_trials()'s read-time behavior (which
    intentionally does treat it as {}, since failing open at
    report-generation time is worse than temporarily un-filtering)."""


def _load_excluded_trials_strict() -> dict:
    """Like load_excluded_trials(), but raises RegistryCorruptError instead
    of silently returning {} when the file exists and is unparseable or the
    wrong shape. A missing file is not corruption -> {}."""
    if not os.path.exists(EXCLUDED_TRIALS_PATH):
        return {}
    try:
        with open(EXCLUDED_TRIALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise RegistryCorruptError(f"{EXCLUDED_TRIALS_PATH} is not valid JSON: {e}") from e
    if not isinstance(data, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise RegistryCorruptError(
            f"{EXCLUDED_TRIALS_PATH} must contain a JSON object of string "
            f"keys to string values, got {type(data).__name__}")
    return data


def set_trials_excluded(keys: list, excluded: bool) -> None:
    """The single entry point for the trial-exclusion UI (design spec
    Section 3) -- batch-toggles trial_keys and writes excluded_trials.json
    atomically. keys is deduplicated internally (a caller passing the same
    key twice, e.g. from two colliding rows, must not double-toggle).
    excluded=True sets a fixed placeholder reason; excluded=False removes
    the key entirely (a falsy/blank value would still satisfy
    `key in excluded`, corrupting the exclusion gate).

    Raises RegistryCorruptError -- without touching the file at all -- if
    the on-disk registry exists but fails to parse or isn't a {str: str}
    dict; silently treating either as {} and saving would discard every
    exclusion the file actually contained."""
    registry = _load_excluded_trials_strict()
    for key in dict.fromkeys(keys):   # dedupe, preserve first-seen order
        if excluded:
            registry[key] = "excluded via Analysis panel"
        else:
            registry.pop(key, None)

    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(EXCLUDED_TRIALS_PATH), prefix=".excluded_trials_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, sort_keys=True)
        os.replace(tmp_path, EXCLUDED_TRIALS_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
```

- [ ] **Step 5: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k set_trials_excluded -v
```

Expected: all 7 PASS

- [ ] **Step 6: Run the full test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\ -v
```

Expected: same pass/fail counts as Task 2's baseline plus the 7 new passes.

- [ ] **Step 7: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "feat: add RegistryCorruptError and set_trials_excluded atomic batch setter"
```

---

### Task 4: `AnalysisPanel` participant list shows fully-excluded participants

**Files:**
- Modify: `pendulastic_app.py:2201-2219` (`AnalysisPanel._refresh_participants`)
- Modify: `tests/test_analysis_panel.py:17-30` (`_FakeReport.list_participants`)
- Test: `tests/test_analysis_panel.py` (append)

**Interfaces:**
- Produces: `AnalysisPanel._refresh_participants()` now calls `_report.list_participants(include_excluded=True)`; a participant with `n_trials == 0` gets a `" (all excluded)"` suffix in the listbox label instead of vanishing.
- Consumes: `list_participants(include_archive=True, *, include_excluded=False)` (Task 1).

- [ ] **Step 1: Update `_FakeReport.list_participants` to accept `include_excluded`**

In `tests/test_analysis_panel.py`, replace lines 29-30:

```python
# OLD
    def list_participants(self):
        return dict(self.participants)
```

```python
# NEW
    def list_participants(self, include_excluded=False):
        return dict(self.participants)
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_analysis_panel.py`:

```python
def test_refresh_participants_labels_fully_excluded_participant(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.participants["3"] = {"legs": set(), "conditions": set(), "n_trials": 0}
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        labels = [p._participant_list.get(i) for i in range(p._participant_list.size())]
        assert any("(all excluded)" in lbl and lbl.startswith("P3") for lbl in labels)
        assert not any("(all excluded)" in lbl for lbl in labels if lbl.startswith("P1"))
    finally:
        r.destroy()


def test_refresh_participants_calls_list_participants_with_include_excluded(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    calls = []
    orig = fake.list_participants
    fake.list_participants = lambda include_excluded=False: (
        calls.append(include_excluded), orig(include_excluded))[1]
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack()
        r.update()
        p._refresh_participants()
        assert calls == [True]
    finally:
        r.destroy()
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "refresh_participants_labels_fully_excluded or refresh_participants_calls_list_participants_with_include_excluded" -v
```

Expected: FAIL — no "(all excluded)" suffix exists yet, and `_refresh_participants` calls `list_participants()` with no arguments.

- [ ] **Step 4: Update `_refresh_participants`**

In `pendulastic_app.py`, replace lines 2201-2219:

```python
# OLD
    def _refresh_participants(self) -> None:
        if not _REPORT_AVAIL:
            self.status_var.set("pt_report_common unavailable — check console for import error.")
            return
        self.status_var.set("Scanning for participants...")
        self.update_idletasks()
        try:
            self._participants = _report.list_participants()
        except Exception as e:
            self.status_var.set(f"Scan failed: {e}")
            return
        self._participant_list.delete(0, "end")
        for pid, info in self._participants.items():
            legs = "/".join(sorted(info["legs"]))
            self._participant_list.insert(
                "end", f"P{pid}  ({legs}, {info['n_trials']} trials, "
                       f"{len(info['conditions'])} condition(s))")
        self.status_var.set(f"{len(self._participants)} participant(s) found. "
                            f"Pick participant(s), then Generate.")
```

```python
# NEW
    def _refresh_participants(self) -> None:
        if not _REPORT_AVAIL:
            self.status_var.set("pt_report_common unavailable — check console for import error.")
            return
        self.status_var.set("Scanning for participants...")
        self.update_idletasks()
        try:
            self._participants = _report.list_participants(include_excluded=True)
        except Exception as e:
            self.status_var.set(f"Scan failed: {e}")
            return
        self._participant_list.delete(0, "end")
        for pid, info in self._participants.items():
            legs = "/".join(sorted(info["legs"]))
            # n_trials == 0 with include_excluded=True means every one of
            # this participant's trials is excluded (Task 1) -- they'd
            # otherwise vanish from this list with no way to re-select and
            # undo it (design spec Section 4).
            if info["n_trials"] == 0:
                self._participant_list.insert("end", f"P{pid}  (all excluded)")
                continue
            self._participant_list.insert(
                "end", f"P{pid}  ({legs}, {info['n_trials']} trials, "
                       f"{len(info['conditions'])} condition(s))")
        self.status_var.set(f"{len(self._participants)} participant(s) found. "
                            f"Pick participant(s), then Generate.")
```

- [ ] **Step 5: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "refresh_participants_labels_fully_excluded or refresh_participants_calls_list_participants_with_include_excluded" -v
```

Expected: both PASS

- [ ] **Step 6: Run the full analysis-panel test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -v
```

Expected: all pass, including `test_refresh_participants_populates_listbox` (unaffected — still finds 2 participants).

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_analysis_panel.py
git commit -m "feat: show fully-excluded participants in AnalysisPanel's list"
```

---

### Task 5: Trial table widget, view-switch, and background population

**Files:**
- Modify: `pendulastic_app.py:2079-2087` (`AnalysisPanel.__init__`)
- Modify: `pendulastic_app.py:2166-2192` (`_build_widgets`'s viewer-outer block, end of method)
- Modify: `pendulastic_app.py:2194-2229` (add selection binding, view-switch, worker, poll)
- Test: `tests/test_analysis_panel.py` (extend `_FakeReport`, append tests)

**Interfaces:**
- Consumes: `_report.discover_all_trials(include_excluded=True)`, `_report.duplicate_trial_keys(records)` (Tasks 1-2); module-level `load_optitrack`/`compute_pt_params` behind `_PT_AVAIL` (existing).
- Produces: `AnalysisPanel._table_frame`, `_trial_table: ttk.Treeview`, `_table_row_meta: dict` (Treeview item id -> record dict), `_table_dupes: dict`, `_table_queue`, `_table_request_id: int`, `_busy: bool`, `_viewer_vbar`/`_viewer_hbar` (promoted from local vars), `_on_participant_selection_changed(event=None)`, `_switch_to_table_view()`, `_switch_to_figure_view()`, `_start_table_load(idx, request_id)`, `_table_worker(pid, request_id)`, `_poll_table_queue()`, `_fmt_metric(value, decimals)` (staticmethod). `btn_toggle_excluded` is created here (disabled by default) but wired up in Task 6.

- [ ] **Step 1: Extend `_FakeReport` with the new data-layer methods**

In `tests/test_analysis_panel.py`, replace the `_FakeReport` class (lines 17-51) — add the new methods and a `records` seed used by the table:

```python
# OLD (class body, after __init__ through make_rmse_figure — keep list_participants
# as already updated in Task 4, keep collect_participant/_fig/make_*_figure unchanged)
```

```python
# NEW -- append these methods to _FakeReport, after make_rmse_figure:

    def discover_all_trials(self, include_archive=True, include_excluded=False):
        self.calls.append(("discover_all_trials", include_excluded))
        if include_excluded:
            return list(self.records)
        return [r for r in self.records if not r["excluded"]]

    def duplicate_trial_keys(self, records):
        self.calls.append(("duplicate_trial_keys", len(records)))
        by_key = {}
        for r in records:
            by_key.setdefault(r["trial_key"], []).append(r["path"])
        return {k: v for k, v in by_key.items() if len(v) > 1}

    def set_trials_excluded(self, keys, excluded):
        self.calls.append(("set_trials_excluded", list(keys), excluded))
        for r in self.records:
            if r["trial_key"] in keys:
                r["excluded"] = excluded
```

And add a `self.records` seed to `_FakeReport.__init__` (after `self.participants = {...}`):

```python
        self.records = [
            {"participant": "1", "leg": "left", "condition": "pre", "trial": "1",
             "path": "/rec/P1_left_pre_trial_1.csv", "mtime": 0.0,
             "trial_key": "1_left_pre_T1", "excluded": False},
            {"participant": "1", "leg": "left", "condition": "pre", "trial": "2",
             "path": "/rec/P1_left_pre_trial_2.csv", "mtime": 0.0,
             "trial_key": "1_left_pre_T2", "excluded": False},
        ]
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_analysis_panel.py`:

```python
def test_table_hidden_and_figure_shown_by_default():
    from pendulastic_app import AnalysisPanel
    r = _root()
    try:
        p = AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        assert p._table_frame.winfo_manager() == ""
        assert p._viewer_canvas.winfo_manager() == "grid"
    finally:
        r.destroy()


def test_single_selection_switches_to_table_view(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while p._table_frame.winfo_manager() != "grid" and time.time() < deadline:
            r.update(); time.sleep(0.02)
        assert p._table_frame.winfo_manager() == "grid"
        assert p._viewer_canvas.winfo_manager() == ""
        assert p._viewer_vbar.winfo_manager() == ""
        assert p._viewer_hbar.winfo_manager() == ""
    finally:
        r.destroy()


def test_zero_or_multi_selection_reverts_to_figure_view(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0, 1)
        p._on_participant_selection_changed()
        r.update()
        assert p._table_frame.winfo_manager() == ""
        assert p._viewer_canvas.winfo_manager() == "grid"
        assert p.btn_toggle_excluded.cget("state") == "disabled"
    finally:
        r.destroy()


def test_table_populates_with_scored_trials(monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", True)
    monkeypatch.setattr(_m, "load_optitrack",
                        lambda path: (np.array([0.0, 1.0]), np.array([180.0, 170.0])))
    monkeypatch.setattr(_m, "compute_pt_params",
                        lambda t, angle: {"N": 4.0, "phi_max_ratio": 0.63871, "area_ratio": 0.0497})
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        items = p._trial_table.get_children()
        assert len(items) == 2
        vals = p._trial_table.item(items[0], "values")
        assert vals[4] == "4.0"       # N, 1 decimal
        assert vals[5] == "0.639"     # phi_max_ratio, 3 decimals
        assert vals[6] == "0.050"     # area_ratio, 3 decimals
    finally:
        r.destroy()


def test_table_shows_na_for_failed_scoring(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", True)

    def raising_load(path):
        raise ValueError("bad csv")

    monkeypatch.setattr(_m, "load_optitrack", raising_load)
    monkeypatch.setattr(_m, "compute_pt_params", lambda t, angle: None)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        vals = p._trial_table.item(p._trial_table.get_children()[0], "values")
        assert vals[4] == vals[5] == vals[6] == "N/A"
    finally:
        r.destroy()


def test_table_marks_duplicate_trial_keys(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.records.append({
        "participant": "1", "leg": "left", "condition": "pre", "trial": "1",
        "path": "/rec_dup/P1_left_pre_trial_1.csv", "mtime": 0.0,
        "trial_key": "1_left_pre_T1", "excluded": False,
    })
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        warn_col_values = [p._trial_table.item(i, "values")[0] for i in p._trial_table.get_children()]
        assert warn_col_values.count("⚠") == 2
    finally:
        r.destroy()


def test_rapid_reselection_drops_stale_table_result(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()

        # Simulate a superseded request: manually post a stale-id result
        # directly onto the queue, then a current one, and confirm only the
        # current one's rows land.
        p._table_request_id = 5
        stale_record = dict(fake.records[0], trial="99")
        p._table_queue.put(("ok", (4, [(stale_record, None, None, None)], {}), None))
        p._table_queue.put(("ok", (5, [(fake.records[0], None, None, None)], {}), None))
        p.after(0, p._poll_table_queue)
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        vals = [p._trial_table.item(i, "values")[3] for i in p._trial_table.get_children()]
        assert "99" not in vals
        assert fake.records[0]["trial"] in vals
    finally:
        r.destroy()
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "table_hidden or single_selection_switches or zero_or_multi_selection or table_populates or table_shows_na or table_marks_duplicate or rapid_reselection" -v
```

Expected: FAIL — `AttributeError: 'AnalysisPanel' object has no attribute '_table_frame'`.

- [ ] **Step 4: Add new state to `__init__`**

In `pendulastic_app.py`, replace lines 2079-2087:

```python
# OLD
    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._result_queue: queue.Queue = queue.Queue()
        self._current_fig = None
        self._current_canvas = None
        self._last_out_path: Optional[str] = None
        self._participants: dict = {}
        self._build_widgets()
```

```python
# NEW
    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._result_queue: queue.Queue = queue.Queue()
        self._current_fig = None
        self._current_canvas = None
        self._last_out_path: Optional[str] = None
        self._participants: dict = {}
        # Trial-exclusion table state (design spec
        # docs/superpowers/specs/2026-08-07-trial-exclusion-ui-design.md).
        # A separate queue/request-id from the Generate flow above -- reusing
        # _result_queue would let a stale table-load result be decoded as a
        # figure result or vice versa.
        self._table_queue: queue.Queue = queue.Queue()
        self._table_request_id = 0
        self._table_row_meta: dict = {}
        self._table_dupes: dict = {}
        self._table_polling = False   # True while a _poll_table_queue chain is active
        self._busy = False
        self._build_widgets()
```

- [ ] **Step 5: Promote `vbar`/`hbar`, add the table frame, and wire the selection binding**

In `pendulastic_app.py`, replace lines 2166-2192 (from the `# ── Right: scrollable figure viewer` comment through the end of `_build_widgets`):

```python
# OLD
        # ── Right: scrollable figure viewer ─────────────────────────────
        viewer_outer = tk.Frame(self, relief="sunken", bd=1, bg=ws.PALETTE["BG"])
        viewer_outer.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        viewer_outer.columnconfigure(0, weight=1)
        viewer_outer.rowconfigure(0, weight=1)

        self._viewer_canvas = tk.Canvas(viewer_outer, bg=ws.PALETTE["SURFACE"],
                                        highlightthickness=0)
        vbar = tk.Scrollbar(viewer_outer, orient="vertical", command=self._viewer_canvas.yview)
        hbar = tk.Scrollbar(viewer_outer, orient="horizontal", command=self._viewer_canvas.xview)
        self._viewer_canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self._viewer_canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        self._viewer_frame = tk.Frame(self._viewer_canvas, bg=ws.PALETTE["SURFACE"])
        self._viewer_window = self._viewer_canvas.create_window(
            (0, 0), window=self._viewer_frame, anchor="nw")
        self._viewer_frame.bind(
            "<Configure>",
            lambda e: self._viewer_canvas.configure(scrollregion=self._viewer_canvas.bbox("all")))

        self._viewer_placeholder = tk.Label(
            self._viewer_frame, text="No figure generated yet.",
            font=("Segoe UI", 11), fg=ws.PALETTE["FG3"], bg=ws.PALETTE["SURFACE"],
            padx=40, pady=40)
        self._viewer_placeholder.pack()
```

```python
# NEW
        # ── Right: scrollable figure viewer / trial table (two view modes
        # sharing the same grid cell) ────────────────────────────────────
        viewer_outer = tk.Frame(self, relief="sunken", bd=1, bg=ws.PALETTE["BG"])
        viewer_outer.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        viewer_outer.columnconfigure(0, weight=1)
        viewer_outer.rowconfigure(0, weight=1)

        self._viewer_canvas = tk.Canvas(viewer_outer, bg=ws.PALETTE["SURFACE"],
                                        highlightthickness=0)
        # Promoted to self (round-3 spec fix): grid_remove()ing only the
        # canvas when switching to the table view would leave these two
        # orphaned next to it if they stayed local variables.
        self._viewer_vbar = tk.Scrollbar(viewer_outer, orient="vertical", command=self._viewer_canvas.yview)
        self._viewer_hbar = tk.Scrollbar(viewer_outer, orient="horizontal", command=self._viewer_canvas.xview)
        self._viewer_canvas.configure(yscrollcommand=self._viewer_vbar.set, xscrollcommand=self._viewer_hbar.set)
        self._viewer_canvas.grid(row=0, column=0, sticky="nsew")
        self._viewer_vbar.grid(row=0, column=1, sticky="ns")
        self._viewer_hbar.grid(row=1, column=0, sticky="ew")

        self._viewer_frame = tk.Frame(self._viewer_canvas, bg=ws.PALETTE["SURFACE"])
        self._viewer_window = self._viewer_canvas.create_window(
            (0, 0), window=self._viewer_frame, anchor="nw")
        self._viewer_frame.bind(
            "<Configure>",
            lambda e: self._viewer_canvas.configure(scrollregion=self._viewer_canvas.bbox("all")))

        self._viewer_placeholder = tk.Label(
            self._viewer_frame, text="No figure generated yet.",
            font=("Segoe UI", 11), fg=ws.PALETTE["FG3"], bg=ws.PALETTE["SURFACE"],
            padx=40, pady=40)
        self._viewer_placeholder.pack()

        # Trial table -- gridded into the same (row=0, col=0) cell as
        # _viewer_canvas, shown only when exactly one participant is
        # selected. Not grid()'d here; _switch_to_table_view() does that.
        self._table_frame = tk.Frame(viewer_outer, bg=ws.PALETTE["SURFACE"])

        table_top = tk.Frame(self._table_frame, bg=ws.PALETTE["SURFACE"])
        table_top.pack(side="top", fill="x", padx=6, pady=(6, 2))
        self.btn_toggle_excluded = ws.secondary_button(
            table_top, "Toggle Excluded", self._on_toggle_excluded)
        self.btn_toggle_excluded.config(state="disabled")
        self.btn_toggle_excluded.pack(side="left")

        table_wrap = tk.Frame(self._table_frame, bg=ws.PALETTE["SURFACE"])
        table_wrap.pack(side="top", fill="both", expand=True, padx=6, pady=(0, 6))
        table_wrap.columnconfigure(0, weight=1)
        table_wrap.rowconfigure(0, weight=1)

        cols = ("warn", "leg", "condition", "trial", "n", "phi_max_ratio", "area_ratio")
        hdrs = ("⚠", "Leg", "Condition", "Trial #", "N", "phi_max_ratio", "area_ratio")
        widths = (24, 60, 110, 60, 50, 100, 90)
        self._trial_table = ttk.Treeview(
            table_wrap, style=ws.STYLE_TREEVIEW, columns=cols, show="headings",
            selectmode="extended")
        for key, hdr, w in zip(cols, hdrs, widths):
            self._trial_table.heading(key, text=hdr)
            self._trial_table.column(key, width=w, anchor="center", stretch=False)
        self._trial_table.column("condition", stretch=True)
        self._trial_table.tag_configure("excluded", foreground=ws.PALETTE["FG3"])
        self._trial_table.tag_configure("duplicate", foreground="#B45309")
        table_sb = ttk.Scrollbar(table_wrap, orient="vertical", style=ws.STYLE_SCROLLBAR,
                                 command=self._trial_table.yview)
        self._trial_table.configure(yscrollcommand=table_sb.set)
        self._trial_table.grid(row=0, column=0, sticky="nsew")
        table_sb.grid(row=0, column=1, sticky="ns")

        self._participant_list.bind("<<ListboxSelect>>", self._on_participant_selection_changed)

    # ------------------------------------------------------------------
    # Trial table: view-switch + selection handling
    # ------------------------------------------------------------------
    def _switch_to_table_view(self) -> None:
        self._viewer_canvas.grid_remove()
        self._viewer_vbar.grid_remove()
        self._viewer_hbar.grid_remove()
        self._table_frame.grid(row=0, column=0, sticky="nsew")

    def _switch_to_figure_view(self) -> None:
        self._table_frame.grid_remove()
        self._viewer_canvas.grid()
        self._viewer_vbar.grid()
        self._viewer_hbar.grid()

    def _on_participant_selection_changed(self, event=None) -> None:
        # Bumped on every call, regardless of outcome (zero/multi selection,
        # a busy-rejected change, or a valid single selection) -- a slow,
        # still-in-flight job must never repopulate the table after the
        # selection that started it no longer applies.
        self._table_request_id += 1
        request_id = self._table_request_id
        if self._busy:
            return
        sel = self._participant_list.curselection()
        if len(sel) != 1:
            self._switch_to_figure_view()
            self._trial_table.delete(*self._trial_table.get_children())
            self._table_row_meta = {}
            self.btn_toggle_excluded.config(state="disabled")
            return
        self._switch_to_table_view()
        self._start_table_load(sel[0], request_id)

    def _start_table_load(self, idx: int, request_id: int) -> None:
        pid = list(self._participants.keys())[idx]
        self._trial_table.delete(*self._trial_table.get_children())
        self._table_row_meta = {}
        self.btn_toggle_excluded.config(state="disabled")
        self.status_var.set(f"Loading trials for P{pid}...")
        threading.Thread(target=self._table_worker, args=(pid, request_id), daemon=True).start()
        # Only start a new polling chain if none is active -- rapid
        # re-selection would otherwise spawn one self.after(150, ...) chain
        # per selection, none of which ever terminates on its own once a
        # later chain wins the race to consume the matching result (each
        # checks the queue independently; an already-empty chain would just
        # keep rescheduling itself forever). One chain is enough: it always
        # compares against the current self._table_request_id, not whichever
        # request started it.
        if not self._table_polling:
            self._table_polling = True
            self.after(150, self._poll_table_queue)

    def _table_worker(self, pid: str, request_id: int) -> None:
        try:
            records = [r for r in _report.discover_all_trials(include_excluded=True)
                       if r["participant"] == pid]
            dupes = _report.duplicate_trial_keys(records)
            rows = []
            for r in records:
                n = phi = area = None
                if _PT_AVAIL:
                    try:
                        t, angle = load_optitrack(r["path"])
                    except Exception:
                        t = angle = None
                    if t is not None:
                        try:
                            params = compute_pt_params(t, angle)
                        except Exception:
                            params = None
                        if params:
                            n = params.get("N")
                            phi = params.get("phi_max_ratio")
                            area = params.get("area_ratio")
                rows.append((r, n, phi, area))
            self._table_queue.put(("ok", (request_id, rows, dupes), None))
        except Exception as e:
            self._table_queue.put(("error", (request_id, str(e)), None))

    @staticmethod
    def _fmt_metric(value, decimals: int) -> str:
        if value is None:
            return "N/A"
        try:
            f = float(value)
        except (TypeError, ValueError):
            return "N/A"
        if not math.isfinite(f):
            return "N/A"
        return f"{f:.{decimals}f}"

    def _poll_table_queue(self) -> None:
        try:
            status, payload, _ = self._table_queue.get_nowait()
        except queue.Empty:
            self.after(150, self._poll_table_queue)
            return

        request_id = payload[0]
        if request_id != self._table_request_id:
            # Superseded by a newer selection/reload -- discard, but keep
            # the chain alive since the current request's result may still
            # be sitting behind this one in the queue.
            self.after(150, self._poll_table_queue)
            return

        self._table_polling = False   # this chain's job is done either way below

        if status == "error":
            self.status_var.set(f"Failed to load trials: {payload[1]}")
            return

        _, rows, dupes = payload
        self._table_dupes = dupes
        self.btn_toggle_excluded.config(state="normal" if rows else "disabled")

        if not _PT_AVAIL:
            for r, _n, _phi, _area in rows:
                tags = ["excluded"] if r["excluded"] else []
                warn = r["trial_key"] in dupes
                if warn:
                    tags.append("duplicate")
                item = self._trial_table.insert(
                    "", "end",
                    values=("⚠" if warn else "", r["leg"], r["condition"], r["trial"],
                            "N/A", "N/A", "N/A"),
                    tags=tuple(tags))
                self._table_row_meta[item] = r
            self.status_var.set(f"{len(rows)} trial(s) loaded (scoring unavailable — "
                                f"compute_pt_params/load_optitrack failed to import).")
            return

        for r, n, phi, area in rows:
            tags = ["excluded"] if r["excluded"] else []
            warn = r["trial_key"] in dupes
            if warn:
                tags.append("duplicate")
            item = self._trial_table.insert(
                "", "end",
                values=("⚠" if warn else "", r["leg"], r["condition"], r["trial"],
                        self._fmt_metric(n, 1), self._fmt_metric(phi, 3), self._fmt_metric(area, 3)),
                tags=tuple(tags))
            self._table_row_meta[item] = r
        self.status_var.set(f"{len(rows)} trial(s) loaded.")
```

- [ ] **Step 6: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "table_hidden or single_selection_switches or zero_or_multi_selection or table_populates or table_shows_na or table_marks_duplicate or rapid_reselection" -v
```

Expected: all 7 PASS

- [ ] **Step 7: Run the full analysis-panel test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -v
```

Expected: all pass, including every pre-existing Generate-flow test.

- [ ] **Step 8: Commit**

```bash
git add pendulastic_app.py tests/test_analysis_panel.py
git commit -m "feat: add trial table view with background-loaded per-trial PT scores"
```

---

### Task 6: "Toggle Excluded" button, busy-flag gating, and post-toggle refresh

**Files:**
- Modify: `pendulastic_app.py:2234-2255` (`_on_generate`), `pendulastic_app.py:2278-2289` (`_poll_result`'s error branch), `pendulastic_app.py` (add `_on_toggle_excluded`, `_end_busy`, `_refresh_participants_preserving_selection` near `_on_toggle_excluded`)
- Test: `tests/test_analysis_panel.py` (append)

**Interfaces:**
- Produces: `AnalysisPanel._on_toggle_excluded()`, `_end_busy()`, `_refresh_participants_preserving_selection()`. `self._busy` (introduced Task 5) now actually gates `_on_generate`/`_on_participant_selection_changed`/`_on_toggle_excluded` against each other.
- Consumes: `_report.set_trials_excluded(keys, excluded)`, `_report.RegistryCorruptError` (Task 3); `self._table_row_meta`, `self._table_dupes`, `self._start_table_load` (Task 5).

- [ ] **Step 1: Add `set_trials_excluded`/`RegistryCorruptError` support and duplicate-confirmation plumbing to `_FakeReport`**

`_FakeReport.set_trials_excluded` already exists from Task 5 Step 1. Add a `RegistryCorruptError` alias so tests can raise it through the fake:

In `tests/test_analysis_panel.py`, add near the top (after the `from matplotlib.figure import Figure` import):

```python
import pt_report_common as _real_report
```

(This gives tests access to the real `_real_report.RegistryCorruptError` class to raise/catch — `AnalysisPanel._on_toggle_excluded` catches `_report.RegistryCorruptError`, so a fake standing in for `_report` must expose the identical class object for `except _report.RegistryCorruptError` to match.)

Add to `_FakeReport`, after `set_trials_excluded`:

```python
    RegistryCorruptError = _real_report.RegistryCorruptError
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_analysis_panel.py`:

```python
def _select_and_wait_for_table(p, r, idx=0):
    p._participant_list.selection_set(idx)
    p._on_participant_selection_changed()
    deadline = time.time() + 5
    while not p._trial_table.get_children() and time.time() < deadline:
        r.update(); time.sleep(0.02)


def test_toggle_excluded_calls_set_trials_excluded_with_deduped_keys(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.records.append({
        "participant": "1", "leg": "left", "condition": "pre", "trial": "1",
        "path": "/rec_dup/P1_left_pre_trial_1.csv", "mtime": 0.0,
        "trial_key": "1_left_pre_T1", "excluded": False,
    })
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    monkeypatch.setattr(_m.messagebox, "askyesno", lambda *a, **k: True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        p._trial_table.selection_set(*p._trial_table.get_children())  # selects both colliding rows

        p._on_toggle_excluded()

        set_calls = [c for c in fake.calls if c[0] == "set_trials_excluded"]
        assert len(set_calls) == 1
        assert set_calls[0][1] == ["1_left_pre_T1"]
        assert set_calls[0][2] is True
    finally:
        r.destroy()


def test_toggle_excluded_rejects_mixed_state_selection(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.records[1]["excluded"] = True
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    infos = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda title, msg: infos.append(msg))
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        p._trial_table.selection_set(*p._trial_table.get_children())  # one excluded, one not

        p._on_toggle_excluded()

        assert not [c for c in fake.calls if c[0] == "set_trials_excluded"]
        assert len(infos) == 1
        assert "same current state" in infos[0]
    finally:
        r.destroy()


def test_toggle_excluded_registry_corrupt_leaves_rows_unchanged(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()

    def raise_corrupt(keys, excluded):
        raise fake.RegistryCorruptError("bad json")

    fake.set_trials_excluded = raise_corrupt
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        item = p._trial_table.get_children()[0]
        p._trial_table.selection_set(item)
        before_tags = p._trial_table.item(item, "tags")

        p._on_toggle_excluded()

        assert p._trial_table.item(item, "tags") == before_tags
        assert "fix or restore" in p.status_var.get()
        assert p.btn_toggle_excluded.cget("state") == "normal"
    finally:
        r.destroy()


def test_toggle_excluded_success_updates_row_tags_and_keeps_selection(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", False)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        _select_and_wait_for_table(p, r)
        item = p._trial_table.get_children()[0]
        p._trial_table.selection_set(item)

        p._on_toggle_excluded()
        deadline = time.time() + 5
        while p._participant_list.curselection() != (0,) and time.time() < deadline:
            r.update(); time.sleep(0.02)

        # Participant stays selected (not cleared by the refresh) and the
        # table reloads to reflect the just-saved state.
        assert p._participant_list.curselection() == (0,)
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        reloaded_item = p._trial_table.get_children()[0]
        assert "excluded" in p._trial_table.item(reloaded_item, "tags")
    finally:
        r.destroy()


def test_busy_flag_blocks_selection_change_during_generate(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        p._participant_list.selection_set(0)
        p._figure_type.set("full_report")

        p._on_generate()
        assert p._busy is True

        # A selection change fired mid-Generate must be ignored, not queued.
        p._participant_list.selection_set(1)
        p._on_participant_selection_changed()
        assert p._table_frame.winfo_manager() == ""   # never switched to table view

        _wait_until_enabled(p, r)
        assert p._busy is False
    finally:
        r.destroy()
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "toggle_excluded or busy_flag_blocks_selection" -v
```

Expected: FAIL — `AttributeError: 'AnalysisPanel' object has no attribute '_on_toggle_excluded'`.

- [ ] **Step 4: Wire `self._busy` into `_on_generate`**

In `pendulastic_app.py`, replace lines 2234-2255 (`_on_generate`):

```python
# OLD
    def _on_generate(self) -> None:
        if not _REPORT_AVAIL:
            messagebox.showerror("Unavailable", "pt_report_common could not be imported.")
            return
        selected = self._selected_pids()
        ft = self._figure_type.get()
        needed = 2 if ft == "comparison" else 1
        if len(selected) != needed:
            messagebox.showinfo(
                "Select Participants",
                f"{'Comparison' if needed == 2 else 'This figure type'} needs exactly "
                f"{needed} participant(s) selected — {len(selected)} selected.")
            return

        self.btn_generate.config(state="disabled")
        self.status_var.set("Working — scoring trials, this can take a bit...")
        methodologies = tuple(m for m, v in
                              (("mediapipe", self._use_mediapipe.get()), ("imu", self._use_imu.get()))
                              if v)
        threading.Thread(target=self._generate_worker, args=(ft, selected, methodologies),
                         daemon=True).start()
        self.after(150, self._poll_result)
```

```python
# NEW
    def _on_generate(self) -> None:
        if not _REPORT_AVAIL:
            messagebox.showerror("Unavailable", "pt_report_common could not be imported.")
            return
        if self._busy:
            return
        selected = self._selected_pids()
        ft = self._figure_type.get()
        needed = 2 if ft == "comparison" else 1
        if len(selected) != needed:
            messagebox.showinfo(
                "Select Participants",
                f"{'Comparison' if needed == 2 else 'This figure type'} needs exactly "
                f"{needed} participant(s) selected — {len(selected)} selected.")
            return

        self._busy = True
        self.btn_generate.config(state="disabled")
        self.btn_toggle_excluded.config(state="disabled")
        self.status_var.set("Working — scoring trials, this can take a bit...")
        methodologies = tuple(m for m, v in
                              (("mediapipe", self._use_mediapipe.get()), ("imu", self._use_imu.get()))
                              if v)
        threading.Thread(target=self._generate_worker, args=(ft, selected, methodologies),
                         daemon=True).start()
        self.after(150, self._poll_result)

    def _end_busy(self) -> None:
        self._busy = False
        sel = self._participant_list.curselection()
        self.btn_toggle_excluded.config(state="normal" if len(sel) == 1 and self._trial_table.get_children() else "disabled")
```

- [ ] **Step 5: Call `_end_busy()` from `_poll_result`'s error and completion paths**

In `pendulastic_app.py`, in `_poll_result`, replace the error branch:

```python
# OLD
        if status == "error":
            self.btn_generate.config(state="normal")
            self.status_var.set(f"Failed: {payload}")
            messagebox.showerror("Generation Failed", payload)
            return
```

```python
# NEW
        if status == "error":
            self._end_busy()
            self.btn_generate.config(state="normal")
            self.status_var.set(f"Failed: {payload}")
            messagebox.showerror("Generation Failed", payload)
            return
```

And, later in the same method, replace the exception branch inside the figure-building `try`/`except`:

```python
# OLD
        except Exception as e:
            self.btn_generate.config(state="normal")
            self.status_var.set(f"Failed: {e}")
            messagebox.showerror("Generation Failed", str(e))
            return

        self.btn_generate.config(state="normal")
        self._last_out_path = out_path
        self._show_figure(fig)
        self.status_var.set(f"Done. Saved to:\n{out_path}")
        self.btn_save.config(state="normal")
```

```python
# NEW
        except Exception as e:
            self._end_busy()
            self.btn_generate.config(state="normal")
            self.status_var.set(f"Failed: {e}")
            messagebox.showerror("Generation Failed", str(e))
            return

        self._end_busy()
        self.btn_generate.config(state="normal")
        self._last_out_path = out_path
        self._show_figure(fig)
        self.status_var.set(f"Done. Saved to:\n{out_path}")
        self.btn_save.config(state="normal")
```

- [ ] **Step 6: Add `_on_toggle_excluded` and `_refresh_participants_preserving_selection`**

In `pendulastic_app.py`, add these methods to `AnalysisPanel`, directly after `_poll_table_queue` (added in Task 5):

```python
    def _on_toggle_excluded(self) -> None:
        if self._busy:
            return
        items = self._trial_table.selection()
        if not items:
            return
        records = [self._table_row_meta[i] for i in items if i in self._table_row_meta]
        states = {r["excluded"] for r in records}
        if len(states) > 1:
            messagebox.showinfo(
                "Mixed Selection",
                "Selected rows are a mix of excluded and included trials. "
                "Select rows that are all in the same current state before toggling.")
            return
        currently_excluded = states.pop()
        new_excluded = not currently_excluded
        keys = list(dict.fromkeys(r["trial_key"] for r in records))   # dedupe, preserve order

        dupe_keys = [k for k in keys if k in self._table_dupes]
        if dupe_keys:
            lines = [f"{k} -> {len(self._table_dupes[k])} files: {', '.join(self._table_dupes[k])}"
                    for k in dupe_keys]
            if not messagebox.askyesno(
                    "Duplicate Trial Keys",
                    "The following trial_key(s) map to more than one file. Toggling "
                    "affects every trial sharing that key.\n\n" + "\n".join(lines) +
                    "\n\nContinue?"):
                return

        self._busy = True
        self.btn_toggle_excluded.config(state="disabled")
        self.btn_generate.config(state="disabled")
        try:
            _report.set_trials_excluded(keys, new_excluded)
        except _report.RegistryCorruptError as e:
            self.status_var.set(
                f"excluded_trials.json is corrupt: {e} — fix or restore it by hand before trying again.")
            self._end_busy()
            self.btn_generate.config(state="normal")
            return
        except Exception as e:
            self.status_var.set(f"Failed to toggle exclusion: {e}")
            self._end_busy()
            self.btn_generate.config(state="normal")
            return

        for item in items:
            rec = self._table_row_meta.get(item)
            if rec is None:
                continue
            rec["excluded"] = new_excluded
            tags = list(self._trial_table.item(item, "tags"))
            if new_excluded and "excluded" not in tags:
                tags.append("excluded")
            elif not new_excluded and "excluded" in tags:
                tags.remove("excluded")
            self._trial_table.item(item, tags=tuple(tags))

        self._busy = False
        self.btn_generate.config(state="normal")
        self._refresh_participants_preserving_selection()

    def _refresh_participants_preserving_selection(self) -> None:
        sel = self._participant_list.curselection()
        selected_pid = list(self._participants.keys())[sel[0]] if len(sel) == 1 else None
        self._refresh_participants()
        if selected_pid is not None and selected_pid in self._participants:
            idx = list(self._participants.keys()).index(selected_pid)
            self._participant_list.selection_set(idx)
            self._table_request_id += 1
            self.btn_toggle_excluded.config(state="disabled")
            self._start_table_load(idx, self._table_request_id)
```

- [ ] **Step 7: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "toggle_excluded or busy_flag_blocks_selection" -v
```

Expected: all 5 PASS

- [ ] **Step 8: Run the full test suite to confirm no regressions**

```
.venv\Scripts\pytest tests\ -v
```

Expected: same pass/fail counts as Task 5's baseline plus this task's new passes — no regressions in the pre-existing Generate flow or any other test file.

- [ ] **Step 9: Manual smoke test**

Run: `.venv\Scripts\python.exe pendulastic_app.py`

Verify by hand:
1. Navigate to Analysis & Reports. Select exactly one participant — the right side swaps from "No figure generated yet." to a trial table with Leg/Condition/Trial #/N/phi_max_ratio/area_ratio columns.
2. Select 0 or 2+ participants — the view reverts to the figure placeholder (or a previously generated figure, if one exists) and "Toggle Excluded" disables.
3. Select some rows, click "Toggle Excluded" — rows grey out (excluded tag). Re-select the same participant (or watch the table auto-reload) and confirm the toggle persisted; check `excluded_trials.json` in the repo root for the new entries.
4. Toggle a row back to included — confirm it un-greys and the entry is removed from `excluded_trials.json` (not just blanked).
5. Click Generate while a participant with excluded trials is selected — confirm the generated figure's data no longer includes the excluded trial (uses the default `discover_all_trials()`/`collect_participant()` path, unaffected by this feature per spec Section 5.3).

- [ ] **Step 10: Commit**

```bash
git add pendulastic_app.py tests/test_analysis_panel.py
git commit -m "feat: add Toggle Excluded button with busy-flag gating and duplicate-key confirmation"
```

---

## Plan Self-Review Notes

- **Spec coverage:** §3 (data layer: `include_excluded`, per-record isolation, `duplicate_trial_keys`, `set_trials_excluded`, `RegistryCorruptError`, atomic write) → Tasks 1-3. §4 (UI layout, participant list, selection binding, table, population, toggling, busy-state, post-toggle refresh) → Tasks 4-6. §5 (data flow) → exercised end-to-end by Task 6's manual smoke test. §6 (error handling) → per-record/per-trial failure isolation (Task 1, Task 5's worker), `RegistryCorruptError` surfaced distinctly (Task 6). §7 (testing) → each task's own test additions cover the corresponding data-layer/UI bullets; the "mixed-state rejected", "busy gates selection during Generate", "rows unchanged on `RegistryCorruptError`", and "selection preserved after toggle" bullets are Task 6's dedicated tests.
- **Type/name consistency checked:** `_report.discover_all_trials(include_excluded=True)`/`_report.duplicate_trial_keys(records)`/`_report.set_trials_excluded(keys, excluded)`/`_report.RegistryCorruptError` (Tasks 1-3) are the exact names Task 5-6's UI code calls. `_table_row_meta`, `_table_dupes`, `_table_request_id`, `_busy`, `_start_table_load`, `_switch_to_table_view`/`_switch_to_figure_view` (Task 5) are the only names Task 6 assumes exist. `_FakeReport`'s `discover_all_trials`/`duplicate_trial_keys`/`set_trials_excluded`/`RegistryCorruptError` (Task 5-6) mirror the real module's signatures.
- **Placeholder scan:** no TBDs; every step shows full replacement code.
- **Self-caught design bug, fixed before finalizing:** an earlier draft of Task 5's `_start_table_load`/`_poll_table_queue` started a fresh `self.after(150, ...)` polling chain on every selection change and terminated a chain the instant it saw a stale (superseded) result — under rapid re-selection this both leaked chains (each abandoned chain would poll an empty queue forever) and could stop polling before the current request's result had even arrived. Fixed with a single `self._table_polling` guard: only one chain runs at a time, discarding a stale result keeps the chain alive instead of ending it, and the chain terminates only once it actually consumes the result matching the current `_table_request_id`.
