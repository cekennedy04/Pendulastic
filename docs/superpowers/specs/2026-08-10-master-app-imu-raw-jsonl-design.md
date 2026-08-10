# Live IMU Raw JSONL Log in master_app.py — Design Spec

**Status:** Approved
**Date:** 2026-08-10

---

## 1. Goal

`master_app.py` records phone-IMU data as four files per trial: `Trial_N_imu.csv`
(fused AHRS angles) plus `Trial_N_accel.csv` / `Trial_N_gyro.csv` / `Trial_N_mag.csv`
(raw per-sensor samples), all written by `pendulastic_imu_server.py`'s
`start_recording()`/`stop_recording()`. `pendulastic_workbench.py` already supports
loading IMU data from a single `.jsonl` raw log instead — via a manual file-picker row,
"Single raw log (.jsonl)" — but no trial recorded through `master_app.py` has ever
produced one live. `reconstruct_imu_raw_logs.py` exists solely to rebuild that jsonl
after the fact from the split CSVs, for feeding `imu_calibration_tuner.py`'s
`replay_trial()`.

`pendulastic_app.py` (a sibling app) already writes this live, via
`_imu.start_raw_log(raw_path)` in its own `_start_imu_recording()` and
`_imu.stop_raw_log()` when stopping — independent of `start_recording()`/
`stop_recording()`, because `pendulastic_imu_server.py`'s `on_accel`/`on_gyro`/`on_mag`
call `_raw_log_write()` unconditionally (guarded only by "is a raw log currently open"),
regardless of the separate `_recording` flag the fused/split-CSV path uses.

This feature ports the same pattern to `master_app.py`: call `start_raw_log()`/
`stop_raw_log()` alongside the existing `start_recording()`/`stop_recording()` calls, so
a trial's IMU data is available as one file (for manual workbench upload) without
displacing any existing output. No changes to `pendulastic_imu_server.py` — the
decoupled logging mechanism this relies on already exists and is exercised by
`pendulastic_app.py` today.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `master_app.py` | `_start_imu()` and `_stop_imu()` gain a second, independent start/stop for the raw jsonl log. New instance state: `self._imu_raw_jsonl_path`, `self._imu_raw_recording`. The existing overwrite-confirmation dialog text is extended to name the raw jsonl file. |
| `pendulastic_imu_server.py` | No changes. `start_raw_log()`/`stop_raw_log()` and the always-on `_raw_log_write()` calls already exist and are already used by `pendulastic_app.py`. |
| `tests/test_master_app_imu.py` (new) | Tests for the new start/stop behavior (Section 6). No existing master_app test file covers IMU recording today. |

---

## 3. Behavior

### 3.1 `_start_imu(trial_dir, pid, trial)`

At the top of the method (before either logger is attempted), reset:

```python
self._imu_recording = False
self._imu_csv_path = ""
self._imu_raw_recording = False
self._imu_raw_jsonl_path = ""
```

This guarantees no state from a prior call (e.g. one that failed partway) survives
into this one.

Then, unchanged: attempt `imu_server.start_recording(path, meta)` in its existing
`try/except Exception` block, setting `self._imu_recording`/`self._imu_csv_path` on
success and warning on failure. `start_recording()` returning `False` (a distinct
outcome from raising) is not separately warned on today and stays that way — out of
scope for this feature, which only adds the raw-log path.

Independently, attempt the new raw log:

```python
raw_path = os.path.join(trial_dir, f"Trial_{trial}_imu_raw.jsonl")
try:
    imu_server.start_raw_log(raw_path)
    self._imu_raw_recording = True
    self._imu_raw_jsonl_path = raw_path
except Exception as e:
    messagebox.showwarning(
        "IMU Goniometer",
        f"IMU recording continues, but the raw JSONL log could not be "
        f"opened:\n\n{type(e).__name__}: {e}")
```

Catches `Exception`, not just `OSError` — matching the existing `start_recording()`
call's breadth (an unexpected non-`OSError` from this global server API must not aborts
this optional path while leaving the state inconsistent) rather than the narrower catch
`pendulastic_app.py` uses. This try/except is independent of the `start_recording()`
one above: either can succeed or fail without affecting the other's outcome or state.

### 3.2 `_stop_imu()`

Each logger stops independently, gated on its own flag, with the flag/path cleared in
`finally` regardless of whether the stop call raises:

```python
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

Two independently-gated stops, not one shared flag, because the two loggers can
independently succeed or fail at start (CSV opens, jsonl fails, or vice versa) — a
shared flag would leave whichever one actually opened stuck open if the other failed.

### 3.3 Overwrite confirmation

The existing re-record confirmation dialog (around line 617) currently warns that
recording again will overwrite "that trial's video, IMU CSV, and Motive mirror." Its
text is extended to also name the raw JSONL log, since this feature makes it another
file the same re-record action overwrites.

---

## 4. Known Limitations (explicitly out of scope, stated for the record)

- **Does not obsolete `reconstruct_imu_raw_logs.py`.** This only gives
  `pendulastic_workbench.py`'s manual "Single raw log (.jsonl)" file picker a real file
  to browse to for new `master_app.py` trials. Nothing auto-discovers the new file —
  the reconstruction script remains the only path for historical trials, and for any
  pipeline that wires a raw-log path into automation the way `pendulastic_app.py`'s
  `pending_imu_tune` does (`master_app.py` has no equivalent auto-tuning trigger and
  this feature does not add one).
- **Single global raw-log handle.** `pendulastic_imu_server.py` holds one raw-log file
  handle for the whole process, shared by any caller. This feature assumes
  `master_app.py` and `pendulastic_app.py` are never recording at the same time — if
  they were, one's `start_raw_log()` would silently replace the other's open handle.
  This is a pre-existing characteristic of the server module, not introduced by this
  change, and is not being fixed here.
- **Not zero-cost.** Every accel/gyro/mag sample already triggers a synchronous,
  lock-guarded write into the raw jsonl whenever any raw log is open (this is existing
  `pendulastic_imu_server.py` behavior, unconditional on `_recording`). This feature
  causes that write path to be active during every `master_app.py` IMU trial where it
  previously wasn't. This is comparable to the existing split-CSV write cost, not a new
  order of magnitude, but it is not literally free.
- **Trial-boundary contamination.** Raw logging starts before and stops after the
  video's `writing_flag` window, matching the existing split-CSV behavior — samples
  arriving in those small margins are included independent of actual video frame
  boundaries. Unchanged from today; stated here so it isn't a surprise for a file this
  feature makes newly first-class.
- **Write failures inside `pendulastic_imu_server.py` are silently swallowed**
  (`_raw_log_write`'s `except (OSError, ValueError): pass`, `stop_raw_log()`'s
  suppressed close errors) — a disk-full or disconnected-storage condition can produce
  a truncated/empty jsonl while `master_app.py` still reports success. Pre-existing
  server-module behavior; not addressed by this change.

---

## 5. Out of Scope

- Any change to `pendulastic_imu_server.py`.
- A UI checkbox to disable raw-jsonl logging independently of the existing "Record
  iPhone IMU" checkbox — always attempted whenever that checkbox is on, matching its
  additive/low-cost framing (Section 4) rather than adding a second control surface.
- Auto-discovery of the new jsonl file by `reconstruct_imu_raw_logs.py`,
  `imu_calibration_tuner.py`, or `pendulastic_workbench.py`'s `TrialLoadPanel` — the
  workbench's existing manual browse-and-select flow is the intended consumer.
- Fixing the pre-existing `start_raw_log()` replacement-failure gap (closes the old
  handle before opening the new one; a failed `open()` leaves globals pointing at a
  closed handle) — a `pendulastic_imu_server.py` issue, not touched by this change.
- Any auto-tuning trigger analogous to `pendulastic_app.py`'s `pending_imu_tune`.

---

## 6. Testing Plan

New file `tests/test_master_app_imu.py`, monkeypatching `imu_server` the way
`tests/test_app.py`'s IMU tests do:

1. **Happy path** — `_start_imu` opens both the CSV and the raw jsonl at the expected
   `Trial_N_imu.csv` / `Trial_N_imu_raw.jsonl` paths; `_stop_imu` closes both.
2. **CSV `start_recording()` returns `False`, raw log succeeds** — `_imu_recording`
   stays `False`, `_imu_raw_recording` becomes `True`; `_stop_imu` closes only the raw
   log.
3. **CSV `start_recording()` raises, raw log succeeds** — same outcome as #2, plus a
   warning shown.
4. **Raw log raises, CSV succeeds** — `_imu_raw_recording` stays `False`,
   `_imu_recording` becomes `True`; `_stop_imu` closes only the CSV; a warning shown
   naming the raw log specifically (not the CSV).
5. **Both independent stops** — `stop_recording()` raising does not prevent
   `stop_raw_log()` from being called, and vice versa.
6. **Repeated `_start_imu` calls** (simulating start/stop/start across trials) leave no
   stale flag/path from a prior call — covers the Section 3.1 reset.
7. **Overwrite-confirmation text** names the raw jsonl file alongside video/IMU
   CSV/Motive.

---
