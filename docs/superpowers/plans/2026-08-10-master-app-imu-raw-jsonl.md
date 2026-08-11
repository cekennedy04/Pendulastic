# Live IMU Raw JSONL Log in master_app.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `master_app.py` write a single `Trial_N_imu_raw.jsonl` raw log live during
every IMU-enabled trial, alongside its existing 4-file CSV output, so a trial's IMU data
is available as one file for `pendulastic_workbench.py`'s manual jsonl upload.

**Architecture:** `master_app.py`'s `_start_imu()`/`_stop_imu()` gain a second,
independently-gated start/stop for `pendulastic_imu_server.py`'s already-existing
`start_raw_log()`/`stop_raw_log()`, which are already decoupled from the
`start_recording()`/`stop_recording()` path they call today (confirmed: on_accel/
on_gyro/on_mag already call `_raw_log_write()` unconditionally, independent of the
`_recording` flag). No server-module changes.

**Tech Stack:** Python, tkinter, pytest (existing `tests/test_master_app_paths.py`
conventions — real `MasterApp` + real `tk.Tk()`, no GUI automation, monkeypatched
collaborators).

## Global Constraints

- No changes to `pendulastic_imu_server.py` — `start_raw_log()`/`stop_raw_log()` already
  exist and are already exercised by `pendulastic_app.py`.
- No new UI control — the raw jsonl log is always attempted whenever the existing
  "Record iPhone IMU" checkbox (`self.var_record_imu`) is on, exactly like the existing
  CSV path.
- Raw log filename: `Trial_{trial}_imu_raw.jsonl` in the same directory as
  `Trial_{trial}_imu.csv`.
- The new `start_raw_log()` call catches `Exception` (not just `OSError`) — broader than
  `pendulastic_app.py`'s sibling implementation, matching `master_app.py`'s own existing
  `start_recording()` catch for consistency, so an unexpected non-`OSError` doesn't abort
  `_start_imu()` inconsistently between the two logging paths.
- `_start_imu()` resets all four IMU state fields
  (`_imu_recording`, `_imu_csv_path`, `_imu_raw_recording`, `_imu_raw_jsonl_path`) at the
  top, before attempting either logger — no state from a prior call survives into a new
  one.
- `_stop_imu()` stops each logger independently, gated on its own flag, clearing that
  flag/path in a `finally` block so a raised exception from the stop call itself never
  leaves stale `True` state.
- The existing overwrite-confirmation dialog (`start_recording()`, current text ends
  "...overwrite that trial's video, IMU CSV, and Motive take.") must also name the raw
  jsonl log, since this change makes it another file that action overwrites.

---

## File Structure

| File | Responsibility |
|---|---|
| `master_app.py` | `_start_imu()`/`_stop_imu()` (IMU recording lifecycle) and `start_recording()`'s overwrite-confirmation text. |
| `tests/test_master_app_imu.py` (new) | All new test coverage for the raw-jsonl behavior. No existing master_app test file covers IMU start/stop directly — kept separate from `test_master_app_paths.py` (video/path-focused) and `test_master_app_camera_utils.py` (camera-focused). |

---

## Task 1: Independent raw-jsonl start/stop in `_start_imu`/`_stop_imu`

**Files:**
- Modify: `master_app.py:106-119` (instance state init), `master_app.py:329-360` (`_start_imu`/`_stop_imu`)
- Test: `tests/test_master_app_imu.py` (new)

**Interfaces:**
- Consumes: `master_app.MasterApp(root)`; `master_app.imu_server` (module-level alias for
  `pendulastic_imu_server`, only bound when `_IMU_AVAIL` is `True`) with existing
  `start_recording(csv_path: str, meta: dict) -> bool`, `stop_recording() -> None`,
  `start_raw_log(path: str) -> None` (raises on `open()` failure), and
  `stop_raw_log() -> Optional[str]` (returns the path just closed, `None` if none was
  open).
- Produces: `self._imu_raw_recording: bool` and `self._imu_raw_jsonl_path: str` on the
  `MasterApp` instance — consumed by Task 2's overwrite-dialog text.

**Current code (for reference, `master_app.py:329-360`):**
```python
    def _start_imu(self, trial_dir, pid, trial):
        """Open the IMU CSV for this trial. Never fatal to the main capture."""
        if not (_IMU_AVAIL and self.var_record_imu.get()):
            return
        path = os.path.join(trial_dir, f"Trial_{trial}_imu.csv")
        meta = {
            "participant":     pid,
            "leg":             self.var_leg.get(),
            "characterization": self.entry_characterization.get().strip(),
            "trial":           trial,
            "t0_epoch":        f"{time.time():.4f}",
            "video":           f"Trial_{trial}.avi",
            "video_fps":       f"{TARGET_FPS:.3f}",
        }
        try:
            if imu_server.start_recording(path, meta):
                self._imu_recording = True
                self._imu_csv_path = path
        except Exception as e:
            messagebox.showwarning(
                "IMU Goniometer",
                f"Webcam is recording, but the IMU CSV could not be opened:\n\n"
                f"{type(e).__name__}: {e}")

    def _stop_imu(self):
        if not self._imu_recording:
            return
        self._imu_recording = False
        try:
            imu_server.stop_recording()
        except Exception:
            pass
```

- [ ] **Step 1: Write the failing test — happy path opens both CSV and raw jsonl**

Create `tests/test_master_app_imu.py`:

```python
# tests/test_master_app_imu.py
"""Coverage for master_app.py's IMU recording lifecycle: the fused/split-CSV
path (pendulastic_imu_server.start_recording/stop_recording) and the raw
JSONL path (start_raw_log/stop_raw_log) this feature adds alongside it.

Follows tests/test_master_app_paths.py's convention: real MasterApp + real
tk.Tk(), no GUI automation, monkeypatched imu_server collaborator.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tkinter as tk

import master_app


def _root():
    r = tk.Tk()
    r.withdraw()
    return r


def _app(root):
    os.makedirs(master_app.ROOT_DIR, exist_ok=True)
    return master_app.MasterApp(root)


def _teardown(app, root):
    if app is not None:
        if app.writing_flag.is_set():
            app.stop_recording()
        app._close_camera()
    root.destroy()


def test_start_imu_opens_raw_jsonl_alongside_csv(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)

        calls = {}
        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: calls.setdefault("csv_path", path) or True)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: calls.setdefault("raw_path", path))

        app._start_imu(str(tmp_path), "PYTESTIMU1", 3)

        assert calls["csv_path"] == os.path.join(str(tmp_path), "Trial_3_imu.csv")
        assert calls["raw_path"] == os.path.join(str(tmp_path), "Trial_3_imu_raw.jsonl")
        assert app._imu_recording is True
        assert app._imu_csv_path == calls["csv_path"]
        assert app._imu_raw_recording is True
        assert app._imu_raw_jsonl_path == calls["raw_path"]
    finally:
        _teardown(app, r)


def test_stop_imu_closes_both_independently():
    r = _root()
    app = None
    try:
        app = _app(r)
        stopped = []
        app._imu_recording = True
        app._imu_csv_path = "fake_csv_path"
        app._imu_raw_recording = True
        app._imu_raw_jsonl_path = "fake_raw_path"

        import master_app as _m
        orig_stop_recording = _m.imu_server.stop_recording
        orig_stop_raw_log = _m.imu_server.stop_raw_log
        _m.imu_server.stop_recording = lambda: stopped.append("csv")
        _m.imu_server.stop_raw_log = lambda: stopped.append("raw")
        try:
            app._stop_imu()
        finally:
            _m.imu_server.stop_recording = orig_stop_recording
            _m.imu_server.stop_raw_log = orig_stop_raw_log

        assert stopped == ["csv", "raw"]
        assert app._imu_recording is False
        assert app._imu_raw_recording is False
        assert app._imu_raw_jsonl_path == ""
    finally:
        _teardown(app, r)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_master_app_imu.py -v`

Expected: both tests **FAIL** — `test_start_imu_opens_raw_jsonl_alongside_csv` with
`AttributeError: 'MasterApp' object has no attribute '_imu_raw_recording'` (or a `KeyError`
on `calls["raw_path"]`, since `_start_imu` never calls `start_raw_log` today);
`test_stop_imu_closes_both_independently` similarly fails on `_imu_raw_recording`/
`stopped == ["csv", "raw"]` (today's `_stop_imu` never calls `stop_raw_log`).

- [ ] **Step 3: Implement `_start_imu`/`_stop_imu`**

Replace `master_app.py:329-360` with:

```python
    def _start_imu(self, trial_dir, pid, trial):
        """Open the IMU CSV and raw JSONL log for this trial. Never fatal to
        the main capture -- each of the two loggers is attempted and
        reported on independently, since one can fail while the other
        succeeds."""
        self._imu_recording = False
        self._imu_csv_path = ""
        self._imu_raw_recording = False
        self._imu_raw_jsonl_path = ""
        if not (_IMU_AVAIL and self.var_record_imu.get()):
            return
        path = os.path.join(trial_dir, f"Trial_{trial}_imu.csv")
        meta = {
            "participant":     pid,
            "leg":             self.var_leg.get(),
            "characterization": self.entry_characterization.get().strip(),
            "trial":           trial,
            "t0_epoch":        f"{time.time():.4f}",
            "video":           f"Trial_{trial}.avi",
            "video_fps":       f"{TARGET_FPS:.3f}",
        }
        try:
            if imu_server.start_recording(path, meta):
                self._imu_recording = True
                self._imu_csv_path = path
        except Exception as e:
            messagebox.showwarning(
                "IMU Goniometer",
                f"Webcam is recording, but the IMU CSV could not be opened:\n\n"
                f"{type(e).__name__}: {e}")

        raw_path = os.path.join(trial_dir, f"Trial_{trial}_imu_raw.jsonl")
        try:
            imu_server.start_raw_log(raw_path)
            self._imu_raw_recording = True
            self._imu_raw_jsonl_path = raw_path
        except Exception as e:
            messagebox.showwarning(
                "IMU Goniometer",
                f"IMU recording continues, but the raw JSONL log could not "
                f"be opened:\n\n{type(e).__name__}: {e}")

    def _stop_imu(self):
        if self._imu_recording:
            try:
                imu_server.stop_recording()
            except Exception:
                pass
            finally:
                self._imu_recording = False

        if self._imu_raw_recording:
            try:
                imu_server.stop_raw_log()
            except Exception:
                pass
            finally:
                self._imu_raw_recording = False
                self._imu_raw_jsonl_path = ""
```

Also update the instance-state init block at `master_app.py:106-119` (inside `__init__`,
right after the existing `self._imu_csv_path = ""` line) to add:

```python
        self._imu_raw_recording = False
        self._imu_raw_jsonl_path = ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_master_app_imu.py -v`

Expected: both tests **PASS**.

- [ ] **Step 5: Commit**

```bash
git add master_app.py tests/test_master_app_imu.py
git commit -m "feat: record a live IMU raw JSONL log alongside the CSV in master_app"
```

- [ ] **Step 6: Write failing test — CSV path fails, raw log still succeeds**

Append to `tests/test_master_app_imu.py`:

```python
def test_start_imu_csv_exception_does_not_block_raw_log(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)
        monkeypatch.setattr(master_app.messagebox, "showwarning",
                             lambda title, message: None)

        def fake_start_recording(path, meta):
            raise RuntimeError("disk full")
        calls = {}
        monkeypatch.setattr(master_app.imu_server, "start_recording", fake_start_recording)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: calls.setdefault("raw_path", path))

        app._start_imu(str(tmp_path), "PYTESTIMU2", 1)

        assert app._imu_recording is False
        assert app._imu_csv_path == ""
        assert app._imu_raw_recording is True
        assert calls["raw_path"] == os.path.join(str(tmp_path), "Trial_1_imu_raw.jsonl")
    finally:
        _teardown(app, r)


def test_start_imu_raw_log_exception_does_not_block_csv(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)
        warnings = []
        monkeypatch.setattr(master_app.messagebox, "showwarning",
                             lambda title, message: warnings.append(message))

        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: True)

        # A non-OSError on purpose: proves the new raw-log catch is
        # `except Exception`, broader than pendulastic_app.py's `except
        # OSError`-only sibling implementation (Global Constraints).
        def fake_start_raw_log(path):
            raise RuntimeError("unexpected server error")
        monkeypatch.setattr(master_app.imu_server, "start_raw_log", fake_start_raw_log)

        app._start_imu(str(tmp_path), "PYTESTIMU3", 1)

        assert app._imu_recording is True
        assert app._imu_raw_recording is False
        assert app._imu_raw_jsonl_path == ""
        assert any("raw JSONL" in w for w in warnings)
    finally:
        _teardown(app, r)


def test_start_imu_csv_returns_false_raw_log_still_succeeds(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)
        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: False)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: None)

        app._start_imu(str(tmp_path), "PYTESTIMU4", 1)

        assert app._imu_recording is False
        assert app._imu_raw_recording is True
    finally:
        _teardown(app, r)


def test_stop_imu_stops_raw_log_even_if_csv_stop_raises():
    r = _root()
    app = None
    try:
        app = _app(r)
        app._imu_recording = True
        app._imu_raw_recording = True
        app._imu_raw_jsonl_path = "fake_raw_path"

        import master_app as _m
        orig_stop_recording = _m.imu_server.stop_recording
        orig_stop_raw_log = _m.imu_server.stop_raw_log
        raw_stopped = []
        _m.imu_server.stop_recording = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        _m.imu_server.stop_raw_log = lambda: raw_stopped.append(True)
        try:
            app._stop_imu()
        finally:
            _m.imu_server.stop_recording = orig_stop_recording
            _m.imu_server.stop_raw_log = orig_stop_raw_log

        assert raw_stopped == [True]
        assert app._imu_recording is False
        assert app._imu_raw_recording is False
    finally:
        _teardown(app, r)


def test_stop_imu_stops_csv_even_if_raw_log_stop_raises():
    r = _root()
    app = None
    try:
        app = _app(r)
        app._imu_recording = True
        app._imu_raw_recording = True
        app._imu_raw_jsonl_path = "fake_raw_path"

        import master_app as _m
        orig_stop_recording = _m.imu_server.stop_recording
        orig_stop_raw_log = _m.imu_server.stop_raw_log
        csv_stopped = []
        _m.imu_server.stop_recording = lambda: csv_stopped.append(True)
        _m.imu_server.stop_raw_log = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            app._stop_imu()
        finally:
            _m.imu_server.stop_recording = orig_stop_recording
            _m.imu_server.stop_raw_log = orig_stop_raw_log

        assert csv_stopped == [True]
        assert app._imu_recording is False
        assert app._imu_raw_recording is False
        assert app._imu_raw_jsonl_path == ""
    finally:
        _teardown(app, r)


def test_repeated_start_imu_calls_leave_no_stale_state(tmp_path, monkeypatch):
    r = _root()
    app = None
    try:
        app = _app(r)
        app.var_record_imu.set(True)

        # First trial: raw log fails.
        monkeypatch.setattr(master_app.imu_server, "start_recording",
                             lambda path, meta: True)
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: (_ for _ in ()).throw(OSError("nope")))
        app._start_imu(str(tmp_path), "PYTESTIMU5", 1)
        assert app._imu_raw_recording is False

        # Second trial: raw log succeeds. Must not inherit trial 1's failure.
        monkeypatch.setattr(master_app.imu_server, "start_raw_log",
                             lambda path: None)
        app._start_imu(str(tmp_path), "PYTESTIMU5", 2)
        assert app._imu_raw_recording is True
        assert app._imu_raw_jsonl_path == os.path.join(str(tmp_path), "Trial_2_imu_raw.jsonl")
    finally:
        _teardown(app, r)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_master_app_imu.py -v`

Expected: all 8 tests **PASS** (the Step 3 implementation already handles every one of
these cases — this step is confirming that, not writing new implementation).

- [ ] **Step 8: Commit**

```bash
git add tests/test_master_app_imu.py
git commit -m "test: cover independent CSV/raw-jsonl failure and reset paths in master_app IMU recording"
```

---

## Task 2: Name the raw JSONL log in the overwrite-confirmation dialog

**Files:**
- Modify: `master_app.py:613-620` (`start_recording()`'s overwrite-confirmation dialog)
- Test: `tests/test_master_app_imu.py`

**Interfaces:**
- Consumes: `master_app.MasterApp.start_recording()`, `master_app.messagebox.askyesno`
  (monkeypatched the same way `tests/test_master_app_paths.py`'s
  `test_start_recording_prompts_before_overwriting_existing_trial` already does).
- Produces: nothing consumed by other tasks — this is the final task in this plan.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_master_app_imu.py`:

```python
def test_overwrite_confirmation_names_raw_jsonl_log(monkeypatch):
    import shutil

    r = _root()
    app = None
    pid = "PYTESTIMUOVW1"
    try:
        app = _app(r)
        app.var_record_imu.set(False)  # skip the unrelated IMU-readiness prompt
        app.entry_id.delete(0, tk.END)
        app.entry_id.insert(0, pid)
        app.var_leg.set("Right")
        app.entry_characterization.delete(0, tk.END)
        app.entry_characterization.insert(0, "pre")
        app.var_trial.set("1")

        _, video_path, _ = app._build_paths(pid)
        with open(video_path, "wb") as f:
            f.write(b"placeholder")

        asked = {}
        def fake_askyesno(title, message):
            asked["message"] = message
            return False
        monkeypatch.setattr(master_app.messagebox, "askyesno", fake_askyesno)

        app.start_recording()

        assert "raw IMU JSONL" in asked["message"]
    finally:
        if app is not None:
            if app.writing_flag.is_set():
                app.stop_recording()
            app._close_camera()
        r.destroy()
        p = os.path.join(master_app.ROOT_DIR, f"Participant_{pid}")
        if os.path.isdir(p):
            shutil.rmtree(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_master_app_imu.py::test_overwrite_confirmation_names_raw_jsonl_log -v`

Expected: **FAIL** — `assert "raw IMU JSONL" in asked["message"]` fails because today's
message text only mentions "video, IMU CSV, and Motive take."

- [ ] **Step 3: Update the dialog text**

In `master_app.py`, change (around line 617-618):

```python
                    "Recording again will overwrite that trial's video, IMU CSV, "
                    "and Motive take.\n\nOverwrite it?"):
```

to:

```python
                    "Recording again will overwrite that trial's video, IMU CSV, "
                    "raw IMU JSONL log, and Motive take.\n\nOverwrite it?"):
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_master_app_imu.py -v`

Expected: all 9 tests in the file **PASS**.

- [ ] **Step 5: Commit**

```bash
git add master_app.py tests/test_master_app_imu.py
git commit -m "fix: mention the raw IMU JSONL log in the trial-overwrite confirmation"
```

---

## Final Verification

- [ ] Run the full test file once more end to end: `.venv\Scripts\python.exe -m pytest tests/test_master_app_imu.py -v` — all 9 tests pass.
- [ ] Run the pre-existing master_app suites to confirm no regression: `.venv\Scripts\python.exe -m pytest tests/test_master_app_paths.py tests/test_master_app_camera_utils.py -v`.
