# Trial Exclusion UI Follow-Ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 6 items deferred from the trial-exclusion-ui feature's final whole-branch review (`docs/superpowers/plans/2026-08-07-trial-exclusion-ui.md`, merged to `main` at `0a336f8`): an idle polling-chain timer that never terminates, per-trial scoring failures with no operator-visible count, a raw Windows sharing-violation error on the registry write, no guard against generating a figure for a fully-excluded participant, no feedback when clicking Toggle Excluded with nothing selected, and two small coverage/cosmetic nits (untested NaN/inf formatting, excluded+duplicate row tag-color priority).

**Architecture:** No new files, no new classes. Each item is a targeted edit to already-shipped code in `AnalysisPanel` (`pendulastic_app.py`) or `set_trials_excluded` (`pt_report_common.py`), grouped into 3 tasks by which functions they touch (to avoid two tasks editing the same function independently and conflicting): Task 1 covers the trial-table background-load lifecycle (`_table_worker`/`_poll_table_queue`/`_start_table_load`/`_on_participant_selection_changed`) where items 1, 2, and the two coverage nits all live; Task 2 is the registry write path in `pt_report_common.py`; Task 3 is two independent UX guards in `_on_generate`/`_on_toggle_excluded`.

**Tech Stack:** Python 3.13, Tkinter/`ttk`, `pytest` with headless `tk.Tk()` + `.withdraw()` roots (existing convention in `tests/test_analysis_panel.py`), plain-function tests with `tmp_path`/`monkeypatch` for `pt_report_common.py` (existing convention in `tests/test_pt_report_common.py`).

## Global Constraints

- **No new files, no restructuring.** These are all narrow bug-fix/robustness edits to already-shipped, already-reviewed code — not a redesign. Every change should be the smallest correct fix.
- **The "ok" queue-payload shape changes** from `(request_id, rows, dupes)` to `(request_id, rows, dupes, unscored)` — every producer (`_table_worker`) and every consumer (`_poll_table_queue`, and the existing `test_rapid_reselection_drops_stale_table_result` test, which constructs payloads by hand) must be updated in lockstep, or that test will fail with a tuple-unpack error.
- **`_table_job_pending` is new panel state**, initialized in `__init__` alongside `_table_polling`/`_busy` (both already there). It tracks "is there a live worker whose result the current polling chain still needs to wait for" — distinct from `_table_polling` ("is a polling chain currently scheduled").
- **Do not touch** `_on_toggle_excluded`'s mixed-state rejection, duplicate-key confirmation, `RegistryCorruptError` handling, or `_end_busy`/busy-flag wiring — all of that is already correct and reviewed; Task 3 only adds two new, independent early-return branches.
- **Do not touch** `set_trials_excluded`'s corruption detection (`_load_excluded_trials_strict`), the `flush()`/`fsync()` durability write, or the temp-file cleanup `finally` — Task 2 only wraps the existing `os.replace` call with a retry, nothing else in that function changes shape.

---

### Task 1: Trial-table lifecycle fixes — idle polling termination, per-trial failure visibility, and two coverage/cosmetic nits

**Files:**
- Modify: `pendulastic_app.py` (`AnalysisPanel.__init__`, `_on_participant_selection_changed`, `_start_table_load`, `_table_worker`, `_poll_table_queue`)
- Test: `tests/test_analysis_panel.py` (modify existing test, append new tests)

**Interfaces:**
- Produces: `AnalysisPanel._table_job_pending: bool` (new instance attribute). `_table_worker`'s queued "ok" payload becomes `(request_id, rows, dupes, unscored_count)` — a 4-tuple, up from 3.
- Consumes: existing `_table_polling`, `_table_request_id`, `_table_queue`, `_fmt_metric` (all unchanged in signature).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis_panel.py`:

```python
def test_idle_polling_chain_stops_after_zero_selection(monkeypatch):
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

        # Select one participant (starts a load + polling chain), then
        # immediately deselect everything before the worker's result lands.
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        p._participant_list.selection_clear(0, "end")
        p._on_participant_selection_changed()

        deadline = time.time() + 5
        while p._table_polling and time.time() < deadline:
            r.update(); time.sleep(0.02)
        assert p._table_polling is False, (
            "polling chain must terminate once no participant is selected "
            "and no worker result is still pending, not poll forever")
    finally:
        r.destroy()


def test_table_status_reports_unscored_count(monkeypatch):
    import pendulastic_app as _m
    import numpy as np
    fake = _FakeReport()
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    monkeypatch.setattr(_m, "_PT_AVAIL", True)

    # fake.records has 2 trials; make the first one fail to load, the
    # second score successfully -- expect "(1 unscored)" in the status.
    calls = {"n": 0}

    def flaky_load(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("bad csv")
        return (np.array([0.0, 1.0]), np.array([180.0, 170.0]))

    monkeypatch.setattr(_m, "load_optitrack", flaky_load)
    monkeypatch.setattr(_m, "compute_pt_params",
                        lambda t, angle: {"N": 4.0, "phi_max_ratio": 0.6, "area_ratio": 0.05})
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
        assert "(1 unscored)" in p.status_var.get()
    finally:
        r.destroy()


def test_fmt_metric_handles_nan_inf_and_bad_types():
    from pendulastic_app import AnalysisPanel
    assert AnalysisPanel._fmt_metric(float("nan"), 2) == "N/A"
    assert AnalysisPanel._fmt_metric(float("inf"), 2) == "N/A"
    assert AnalysisPanel._fmt_metric(float("-inf"), 2) == "N/A"
    assert AnalysisPanel._fmt_metric("not-a-number", 2) == "N/A"
    assert AnalysisPanel._fmt_metric(3.14159, 2) == "3.14"


def test_duplicate_and_excluded_row_prioritizes_duplicate_color(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.records[0]["excluded"] = True
    fake.records.append({
        "participant": "1", "leg": "left", "condition": "pre", "trial": "1",
        "path": "/rec_dup/P1_left_pre_trial_1.csv", "mtime": 0.0,
        "trial_key": "1_left_pre_T1", "excluded": True,
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
        dup_item = next(i for i in p._trial_table.get_children()
                        if p._trial_table.item(i, "values")[0] == "⚠")
        tags = p._trial_table.item(dup_item, "tags")
        assert tags.index("duplicate") < tags.index("excluded"), (
            "duplicate must be listed before excluded so its tag_configure "
            "color (amber) takes priority over excluded's (grey)")
    finally:
        r.destroy()
```

- [ ] **Step 2: Update the existing payload-shape test**

In `tests/test_analysis_panel.py`, replace lines in `test_rapid_reselection_drops_stale_table_result` (the two `p._table_queue.put(...)` calls):

```python
# OLD
        p._table_queue.put(("ok", (4, [(stale_record, None, None, None)], {}), None))
        p._table_queue.put(("ok", (5, [(fake.records[0], None, None, None)], {}), None))
```

```python
# NEW
        p._table_queue.put(("ok", (4, [(stale_record, None, None, None)], {}, 0), None))
        p._table_queue.put(("ok", (5, [(fake.records[0], None, None, None)], {}, 0), None))
```

- [ ] **Step 3: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "idle_polling_chain_stops or table_status_reports_unscored_count or fmt_metric_handles_nan_inf or duplicate_and_excluded_row_prioritizes or rapid_reselection" -v
```

Expected: `test_rapid_reselection_drops_stale_table_result` FAILS with a tuple-unpack error (`_poll_table_queue` still expects a 3-tuple); the other 4 new tests FAIL — `test_idle_polling_chain_stops_after_zero_selection` times out with `_table_polling` still `True`, `test_table_status_reports_unscored_count` fails the `"(1 unscored)"` assertion, `test_fmt_metric_handles_nan_inf_and_bad_types` currently PASSES already (no code change needed for it — it's pure coverage, included here for completeness; confirm it passes both before and after this task), `test_duplicate_and_excluded_row_prioritizes_duplicate_color` fails the tag-order assertion.

- [ ] **Step 4: Add `_table_job_pending` to `__init__`**

In `pendulastic_app.py`, find the line `self._table_polling = False   # True while a _poll_table_queue chain is active` (in `AnalysisPanel.__init__`) and add directly after it:

```python
        self._table_polling = False   # True while a _poll_table_queue chain is active
        self._table_job_pending = False   # True while a worker's result for the
                                           # current request_id is still expected
```

- [ ] **Step 5: Set `_table_job_pending` in `_on_participant_selection_changed` and `_start_table_load`**

In `pendulastic_app.py`, in `_on_participant_selection_changed`, replace:

```python
# OLD
        sel = self._participant_list.curselection()
        if len(sel) != 1:
            self._switch_to_figure_view()
            self._trial_table.delete(*self._trial_table.get_children())
            self._table_row_meta = {}
            self.btn_toggle_excluded.config(state="disabled")
            return
```

```python
# NEW
        sel = self._participant_list.curselection()
        if len(sel) != 1:
            self._switch_to_figure_view()
            self._trial_table.delete(*self._trial_table.get_children())
            self._table_row_meta = {}
            self.btn_toggle_excluded.config(state="disabled")
            # No worker is being started for this (newly bumped) request_id --
            # any in-flight worker from a PREVIOUS selection will still post
            # its result eventually, but it'll be discarded as stale by
            # request_id, so the polling chain has nothing left to wait for.
            self._table_job_pending = False
            return
```

In `_start_table_load`, add `self._table_job_pending = True` right before the thread is started:

```python
# OLD
        self.status_var.set(f"Loading trials for P{pid}...")
        threading.Thread(target=self._table_worker, args=(pid, request_id), daemon=True).start()
```

```python
# NEW
        self.status_var.set(f"Loading trials for P{pid}...")
        self._table_job_pending = True
        threading.Thread(target=self._table_worker, args=(pid, request_id), daemon=True).start()
```

- [ ] **Step 6: Make `_poll_table_queue`'s empty-queue branch stop the chain once no job is pending**

In `pendulastic_app.py`, replace the top of `_poll_table_queue`:

```python
# OLD
    def _poll_table_queue(self) -> None:
        try:
            status, payload, _ = self._table_queue.get_nowait()
        except queue.Empty:
            self.after(150, self._poll_table_queue)
            return
```

```python
# NEW
    def _poll_table_queue(self) -> None:
        try:
            status, payload, _ = self._table_queue.get_nowait()
        except queue.Empty:
            if not self._table_job_pending:
                # Nothing left to wait for: no worker is running for the
                # current request_id (the operator moved to a zero/multi
                # selection, or a load already completed), and the queue is
                # drained. Stop rescheduling -- an idle panel must not poll
                # every 150ms forever. _start_table_load()'s own guard
                # restarts a fresh chain the next time a job is actually
                # started.
                self._table_polling = False
                return
            self.after(150, self._poll_table_queue)
            return
```

Then, further down in the same method, update the payload unpacking and the status message. Replace:

```python
# OLD
        self._table_polling = False   # this chain's job is done either way below

        if status == "error":
            self.status_var.set(f"Failed to load trials: {payload[1]}")
            return

        _, rows, dupes = payload
        self._table_dupes = dupes
```

```python
# NEW
        self._table_polling = False   # this chain's job is done either way below
        self._table_job_pending = False   # the result we were waiting for has arrived

        if status == "error":
            self.status_var.set(f"Failed to load trials: {payload[1]}")
            return

        _, rows, dupes, unscored = payload
        self._table_dupes = dupes
```

And replace the row-building loop and final status message:

```python
# OLD
        # Deliberate deviation from spec §4's "one row of explanatory text
        # instead of per-trial N/A spam" when _PT_AVAIL is False: the shipped
        # behavior is the full per-trial row list (all metrics N/A, rows still
        # selectable/toggleable), because the operator still needs to see and
        # act on the trial list when scoring can't run -- exclusion decisions
        # don't depend on the PT metrics being computable. Since
        # _fmt_metric(None, d) already returns "N/A" for every metric in that
        # case, the two branches produced byte-identical rows; only the status
        # message differs, so only the status message branches here.
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
        self.status_var.set(
            f"{len(rows)} trial(s) loaded." if _PT_AVAIL else
            f"{len(rows)} trial(s) loaded (scoring unavailable — "
            f"compute_pt_params/load_optitrack failed to import).")
```

```python
# NEW
        # Deliberate deviation from spec §4's "one row of explanatory text
        # instead of per-trial N/A spam" when _PT_AVAIL is False: the shipped
        # behavior is the full per-trial row list (all metrics N/A, rows still
        # selectable/toggleable), because the operator still needs to see and
        # act on the trial list when scoring can't run -- exclusion decisions
        # don't depend on the PT metrics being computable. Since
        # _fmt_metric(None, d) already returns "N/A" for every metric in that
        # case, the two branches produced byte-identical rows; only the status
        # message differs, so only the status message branches here.
        for r, n, phi, area in rows:
            # "duplicate" listed before "excluded" so ttk's tag-priority
            # resolution (first tag with a given option wins) gives a
            # both-excluded-and-duplicate row the amber warning color, not
            # the muted excluded grey -- the duplicate-key collision is the
            # more actionable signal to notice at a glance.
            tags = []
            warn = r["trial_key"] in dupes
            if warn:
                tags.append("duplicate")
            if r["excluded"]:
                tags.append("excluded")
            item = self._trial_table.insert(
                "", "end",
                values=("⚠" if warn else "", r["leg"], r["condition"], r["trial"],
                        self._fmt_metric(n, 1), self._fmt_metric(phi, 3), self._fmt_metric(area, 3)),
                tags=tuple(tags))
            self._table_row_meta[item] = r
        if not _PT_AVAIL:
            self.status_var.set(f"{len(rows)} trial(s) loaded (scoring unavailable — "
                                f"compute_pt_params/load_optitrack failed to import).")
        elif unscored:
            self.status_var.set(f"{len(rows)} trial(s) loaded ({unscored} unscored).")
        else:
            self.status_var.set(f"{len(rows)} trial(s) loaded.")
```

- [ ] **Step 7: Track and emit the unscored count in `_table_worker`**

In `pendulastic_app.py`, replace `_table_worker`:

```python
# OLD
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
```

```python
# NEW
    def _table_worker(self, pid: str, request_id: int) -> None:
        try:
            records = [r for r in _report.discover_all_trials(include_excluded=True)
                       if r["participant"] == pid]
            dupes = _report.duplicate_trial_keys(records)
            rows = []
            unscored = 0
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
                        else:
                            unscored += 1
                    else:
                        unscored += 1
                rows.append((r, n, phi, area))
            self._table_queue.put(("ok", (request_id, rows, dupes, unscored), None))
        except Exception as e:
            self._table_queue.put(("error", (request_id, str(e)), None))
```

- [ ] **Step 8: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "idle_polling_chain_stops or table_status_reports_unscored_count or fmt_metric_handles_nan_inf or duplicate_and_excluded_row_prioritizes or rapid_reselection" -v
```

Expected: all 5 PASS

- [ ] **Step 9: Run the full regression check**

```
.venv\Scripts\pytest tests\test_pt_report_common.py tests\test_analysis_panel.py -q
```

Expected: same pass count as baseline plus 4 new tests (the 5th, `test_fmt_metric_handles_nan_inf_and_bad_types`, needed no code change and was already passing, so it's +4 net new passes from this task, not +5, relative to a baseline run that didn't yet include it). If a single test fails with a `TclError`/"tk wasn't installed properly" at `tk.Tk()` construction, re-run `tests\test_analysis_panel.py` alone to confirm — this is a known, pre-existing, environment-level flake on this machine (unrelated to this plan), not a regression.

- [ ] **Step 10: Commit**

```bash
git add pendulastic_app.py tests/test_analysis_panel.py
git commit -m "fix: terminate idle table-load polling chain, surface unscored trial count"
```

---

### Task 2: Registry write robustness — retry a transient Windows sharing violation

**Files:**
- Modify: `pt_report_common.py` (imports, `set_trials_excluded`)
- Test: `tests/test_pt_report_common.py` (append)

**Interfaces:**
- Produces: no new public names — `set_trials_excluded`'s external behavior on success is unchanged; on an `os.replace` failure it now retries once before raising.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pt_report_common.py`:

```python
def test_set_trials_excluded_retries_once_on_replace_failure(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(32, "The process cannot access the file because it is being used by another process")
        return real_replace(src, dst)

    monkeypatch.setattr(common.os, "replace", flaky_replace)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)   # don't actually wait in tests

    common.set_trials_excluded(["k1"], True)

    assert calls["n"] == 2
    assert "k1" in common.load_excluded_trials()
    assert list(tmp_path.iterdir()) == [reg_path]   # temp file cleaned up


def test_set_trials_excluded_raises_clear_message_after_two_failures(tmp_path, monkeypatch):
    reg_path = tmp_path / "excluded_trials.json"
    reg_path.write_text(json.dumps({"other_key": "pre-existing reason"}))
    monkeypatch.setattr(common, "EXCLUDED_TRIALS_PATH", str(reg_path))
    original_content = reg_path.read_text()

    def always_fails(src, dst):
        raise OSError(32, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(common.os, "replace", always_fails)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)

    with pytest.raises(OSError) as exc_info:
        common.set_trials_excluded(["k1"], True)

    assert "retried once" in str(exc_info.value)
    assert reg_path.read_text() == original_content
    assert list(tmp_path.iterdir()) == [reg_path]   # temp file cleaned up, original untouched
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k "retries_once_on_replace_failure or raises_clear_message_after_two_failures" -v
```

Expected: FAIL — `test_set_trials_excluded_retries_once_on_replace_failure` fails because `set_trials_excluded` doesn't retry (the flaky mock's first `OSError` propagates immediately, un-retried); `test_set_trials_excluded_raises_clear_message_after_two_failures` fails because the raised message doesn't contain "retried once" (today's code just lets the raw `OSError` propagate as-is).

- [ ] **Step 3: Add `import time`**

In `pt_report_common.py`, replace the import block:

```python
# OLD
import csv
import glob
import json
import os
import re
import sys
import tempfile
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
import time
```

- [ ] **Step 4: Wrap `os.replace` with a one-shot retry**

In `pt_report_common.py`, in `set_trials_excluded`, replace:

```python
# OLD
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(EXCLUDED_TRIALS_PATH), prefix=".excluded_trials_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, sort_keys=True)
            # Force the bytes to disk before the rename: this registry holds
            # hand-curated clinical exclusion decisions with no other copy,
            # so a crash/power loss between the rename and the OS flushing
            # its page cache must not be able to leave an empty or truncated
            # file where the old good one used to be.
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, EXCLUDED_TRIALS_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
```

```python
# NEW
    fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(EXCLUDED_TRIALS_PATH), prefix=".excluded_trials_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2, sort_keys=True)
            # Force the bytes to disk before the rename: this registry holds
            # hand-curated clinical exclusion decisions with no other copy,
            # so a crash/power loss between the rename and the OS flushing
            # its page cache must not be able to leave an empty or truncated
            # file where the old good one used to be.
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp_path, EXCLUDED_TRIALS_PATH)
        except OSError:
            # On Windows, os.replace can transiently fail with a sharing
            # violation (WinError 32) if another thread/process has
            # EXCLUDED_TRIALS_PATH open for reading at this exact instant --
            # e.g. AnalysisPanel's background trial-table worker calling
            # load_excluded_trials() concurrently with this write. One short
            # retry clears the overwhelming majority of these without
            # surfacing a confusing raw WinError to the operator.
            time.sleep(0.05)
            try:
                os.replace(tmp_path, EXCLUDED_TRIALS_PATH)
            except OSError as e:
                raise OSError(
                    f"{e} (retried once and still failed -- another process "
                    f"or thread may still have {EXCLUDED_TRIALS_PATH} open; "
                    f"try again)") from e
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
```

- [ ] **Step 5: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_pt_report_common.py -k "retries_once_on_replace_failure or raises_clear_message_after_two_failures" -v
```

Expected: both PASS

- [ ] **Step 6: Run the full regression check**

```
.venv\Scripts\pytest tests\test_pt_report_common.py tests\test_analysis_panel.py -q
```

Expected: same pass count as Task 1's baseline plus 2 new passes. Pay particular attention to the existing `test_set_trials_excluded_cleans_up_temp_file_on_replace_failure` and `test_set_trials_excluded_raises_on_wrong_shape_json`/`test_set_trials_excluded_raises_on_non_string_value` tests (from the original feature's final-review fix wave) — they monkeypatch `os.replace` to always raise, matching this task's retry-then-raise path; confirm they still pass with the new two-attempt logic (their assertions on temp-file cleanup and original-content preservation should be unaffected, since both still go through the same `finally` cleanup regardless of one attempt or two).

- [ ] **Step 7: Commit**

```bash
git add pt_report_common.py tests/test_pt_report_common.py
git commit -m "fix: retry a transient Windows sharing violation on the registry write"
```

---

### Task 3: Generate/Toggle UX guards — fully-excluded participant, empty Toggle selection

**Files:**
- Modify: `pendulastic_app.py` (`_on_generate`, `_on_toggle_excluded`)
- Test: `tests/test_analysis_panel.py` (append)

**Interfaces:**
- Produces: no new public names — both are new early-return branches with a `messagebox.showinfo` call, using the same pattern as the existing "Select Participants"/"Mixed Selection" messages in these same two methods.
- Consumes: `self._participants[pid]["n_trials"]` (existing, from `list_participants(include_excluded=True)`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_analysis_panel.py`:

```python
def test_generate_rejects_fully_excluded_participant(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
    fake.participants["3"] = {"legs": set(), "conditions": set(), "n_trials": 0}
    monkeypatch.setattr(_m, "_report", fake)
    monkeypatch.setattr(_m, "_REPORT_AVAIL", True)
    infos = []
    monkeypatch.setattr(_m.messagebox, "showinfo", lambda title, msg: infos.append(msg))
    r = _root()
    try:
        p = _m.AnalysisPanel(r, _Ctrl())
        p.pack(); r.update()
        p._refresh_participants()
        # P3 is the last entry inserted (participants dict is insertion-order
        # for "1", "2", then "3" added above) -- select it via curselection index.
        idx = list(p._participants.keys()).index("3")
        p._participant_list.selection_set(idx)
        p._figure_type.set("full_report")

        p._on_generate()

        assert p._busy is False
        assert len(infos) == 1
        assert "3" in infos[0]
        assert not fake.calls   # collect_participant never called
    finally:
        r.destroy()


def test_toggle_excluded_with_nothing_selected_shows_feedback(monkeypatch):
    import pendulastic_app as _m
    fake = _FakeReport()
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
        p._participant_list.selection_set(0)
        p._on_participant_selection_changed()
        deadline = time.time() + 5
        while not p._trial_table.get_children() and time.time() < deadline:
            r.update(); time.sleep(0.02)
        # Deliberately do not select any row.

        p._on_toggle_excluded()

        assert len(infos) == 1
        assert not [c for c in fake.calls if c[0] == "set_trials_excluded"]
    finally:
        r.destroy()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "generate_rejects_fully_excluded_participant or toggle_excluded_with_nothing_selected_shows_feedback" -v
```

Expected: FAIL — `test_generate_rejects_fully_excluded_participant` fails because `_on_generate` proceeds and calls `collect_participant` (fake.calls is non-empty) instead of showing an info message; `test_toggle_excluded_with_nothing_selected_shows_feedback` fails because `_on_toggle_excluded` returns silently (`infos` stays empty).

- [ ] **Step 3: Add the fully-excluded-participant guard to `_on_generate`**

In `pendulastic_app.py`, in `_on_generate`, replace:

```python
# OLD
        selected = self._selected_pids()
        ft = self._figure_type.get()
        needed = 2 if ft == "comparison" else 1
        if len(selected) != needed:
            messagebox.showinfo(
                "Select Participants",
                f"{'Comparison' if needed == 2 else 'This figure type'} needs exactly "
                f"{needed} participant(s) selected — {len(selected)} selected.")
            return
```

```python
# NEW
        selected = self._selected_pids()
        ft = self._figure_type.get()
        needed = 2 if ft == "comparison" else 1
        if len(selected) != needed:
            messagebox.showinfo(
                "Select Participants",
                f"{'Comparison' if needed == 2 else 'This figure type'} needs exactly "
                f"{needed} participant(s) selected — {len(selected)} selected.")
            return
        fully_excluded = [pid for pid in selected if self._participants[pid]["n_trials"] == 0]
        if fully_excluded:
            messagebox.showinfo(
                "No Data To Generate",
                f"Participant(s) {', '.join(fully_excluded)} have every trial "
                "excluded -- there's nothing to generate a figure from. "
                "Re-select participant(s) with at least one included trial.")
            return
```

- [ ] **Step 4: Add the empty-selection guard to `_on_toggle_excluded`**

In `pendulastic_app.py`, in `_on_toggle_excluded`, replace:

```python
# OLD
        items = self._trial_table.selection()
        if not items:
            return
```

```python
# NEW
        items = self._trial_table.selection()
        if not items:
            messagebox.showinfo(
                "Select Trials",
                "Select one or more trial rows in the table before toggling.")
            return
```

- [ ] **Step 5: Run the tests to verify they pass**

```
.venv\Scripts\pytest tests\test_analysis_panel.py -k "generate_rejects_fully_excluded_participant or toggle_excluded_with_nothing_selected_shows_feedback" -v
```

Expected: both PASS

- [ ] **Step 6: Run the full regression check**

```
.venv\Scripts\pytest tests\test_pt_report_common.py tests\test_analysis_panel.py -q
```

Expected: same pass count as Task 2's baseline plus 2 new passes. In particular, confirm `test_generate_requires_exact_participant_count` and `test_toggle_excluded_rejects_mixed_state_selection` (the pre-existing sibling guards this task's new guards sit next to) are unaffected.

- [ ] **Step 7: Commit**

```bash
git add pendulastic_app.py tests/test_analysis_panel.py
git commit -m "feat: guard Generate against a fully-excluded participant, add empty-selection Toggle feedback"
```

---

## Plan Self-Review Notes

- **Spec coverage:** all 6 deferred items from the trial-exclusion-ui final review are covered — idle polling chain (Task 1 Steps 4-6), per-trial unscored count (Task 1 Steps 6-7), NaN/inf coverage (Task 1 Step 1, pure test addition), tag-color priority (Task 1 Step 6), Windows write retry (Task 2), fully-excluded Generate guard and empty-selection Toggle feedback (Task 3).
- **Payload-shape consistency checked:** `_table_worker`'s "ok" tuple (Task 1 Step 7) produces `(request_id, rows, dupes, unscored)`; `_poll_table_queue` (Task 1 Step 6) unpacks that exact 4-tuple; the hand-constructed payloads in `test_rapid_reselection_drops_stale_table_result` (Task 1 Step 2) and the two new tests that post/consume payloads all use the same 4-element shape. No task references the old 3-tuple shape after Task 1 lands.
- **`_table_job_pending` consistency checked:** set `True` only in `_start_table_load` (the only place a worker thread is spawned), set `False` in both places a "no more results are coming for the current id" fact becomes true (`_on_participant_selection_changed`'s zero/multi branch, and `_poll_table_queue`'s non-stale-result branch). `_refresh_participants_preserving_selection` (unchanged, calls `_start_table_load` directly) correctly re-sets it `True` via that same code path.
- **Task ordering/file-overlap checked:** Task 1 and Task 3 both touch `pendulastic_app.py`'s `AnalysisPanel` but different methods (`_table_worker`/`_poll_table_queue`/`_start_table_load`/`_on_participant_selection_changed` vs `_on_generate`/`_on_toggle_excluded`) — Task 3's `_on_toggle_excluded` edit is its `if not items: return` early branch, before Task 1's territory in `_poll_table_queue`/`_table_worker`; no line overlap. Task 2 is entirely `pt_report_common.py`, independent of the other two.
- **Placeholder scan:** no TBDs; every step shows full replacement code, not a description of it.
