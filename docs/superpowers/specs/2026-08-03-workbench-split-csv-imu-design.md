# Workbench Split-CSV Phone-IMU Support — Design Spec

**Status:** Approved
**Date:** 2026-08-03

---

## 1. Goal

Some historical trials (e.g. `Recordings/Participant_13_left/Session_post/Position_1/
Height_Joint-Level/`) were recorded through an older/concurrent "legacy recording" path
that writes raw phone-IMU samples as four sibling per-trial CSVs
(`Trial_N_imu.csv`, `Trial_N_gyro.csv`, `Trial_N_accel.csv`, `Trial_N_mag.csv`) rather
than the single raw JSONL log (`start_raw_log()`) the Pendulastic Workbench's
`TrialLoadPanel`/`workbench_engine.load_imu_trial()` currently expect. The Workbench
has no way to load these trials at all today — the "Phone IMU raw log (.jsonl)" picker
only accepts `.jsonl`, and the "OptiTrack CSV" picker correctly rejects this data since
it isn't OptiTrack data.

This feature teaches the Workbench to load this split-CSV format as an additional Phone
IMU input, **re-fusing the raw gyroscope/accelerometer/magnetometer samples through the
same Madgwick AHRS pipeline** (`imu_calibration_tuner.replay_trial`) used everywhere else
in this project — not by reading `Trial_N_imu.csv`'s pre-computed `hip_pitch_deg` column,
which would introduce an unknown, inconsistent methodology and defeat the purpose of a
rigorous, apples-to-apples comparison against OptiTrack and MediaPipe.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `workbench_engine.py` | New `_read_split_csv_samples()`, `_read_jsonl_samples()` (extracted, unchanged behavior), `_validate_split_csv_header()`; `load_imu_trial()` becomes a format-dispatching wrapper around a shared `_replay_samples()` helper — public signature unchanged |
| `pendulastic_workbench.py` | `TrialLoadPanel`'s "Phone IMU raw log" file picker's `filetypes` widened to also accept `*.csv`; label updated to mention both formats. No other UI changes — `App.on_load_trial()` is untouched |
| `tests/test_workbench_engine.py` | New tests with synthetic split-CSV fixtures |

---

## 3. Split-CSV Format

Each of the three raw sibling files shares this header:
```
timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z
```
- `timestamp_ms`: server-side epoch milliseconds. Divide by 1000 to get the `"t"` field
  `replay_trial` expects (server epoch seconds) — confirmed to line up with
  `Trial_N_imu.csv`'s own `t_epoch` column for the same instant.
- `phone_ts_ms`: passed straight through.
- `role`: already `"proximal"`/`"distal"` — passed straight through. Solo-role trials
  (like `Participant_13_left`'s, where `dist_*` never populates) need no special
  handling: `replay_trial` already branches on whichever roles are actually present,
  since it's the same engine live sessions use.
- `sensor_name`: `"Gyroscope"` / `"Accelerometer"` / `"Magnetometer"` → mapped to
  `"gyro"` / `"accel"` / `"mag"`.
- `x,y,z` → `"v": [x, y, z]`.

`Trial_N_imu.csv` (the fourth sibling) is never read for its data — it's only ever used
as a possible *anchor* file so a user can click on it (the most intuitive of the four
names) and have the other three paths derived from it.

---

## 4. Architecture

**Sibling-path derivation:** given any one of the four sibling paths, derive the other
three by suffix substitution in the same directory
(`Trial_N_imu.csv` ↔ `Trial_N_gyro.csv` ↔ `Trial_N_accel.csv` ↔ `Trial_N_mag.csv`).

**Header validation (closes the "wrong/malformed file" gap):** for each of the three
*raw* sibling files (gyro/accel/mag — never `_imu.csv` itself, which isn't read),
after confirming the derived path exists, read its header row and verify it matches
`timestamp_ms,phone_ts_ms,role,sensor_name,x,y,z` before parsing any data rows. A
missing sibling raises `FileNotFoundError` naming the specific missing file and its
expected path. A sibling that exists but has the wrong header raises a `ValueError`
naming the specific file and what's wrong with it (missing header entirely, wrong
column count, or an unrecognized `sensor_name` value) — this is what a user selecting
an unrelated or corrupt CSV via this picker will actually hit, instead of an obscure
crash three layers deep inside `replay_trial()`.

**Parsing and merge:** read all data rows from the three validated files, convert each
to `{"t": timestamp_ms / 1000.0, "role": role, "sensor": mapped_sensor_name,
"v": [x, y, z], "phone_ts_ms": phone_ts_ms}`, concatenate the three lists, and sort by
`"t"` — satisfying `replay_trial`'s documented "chronologically-sorted list" contract.

**Dispatch, keeping `load_imu_trial()`'s public signature unchanged:**
```python
def load_imu_trial(path, config=None, ft_ratio=None, method=None):
    if path.endswith(".jsonl"):
        samples = _read_jsonl_samples(path)
    else:
        samples = _read_split_csv_samples(path)
    return _replay_samples(samples, config, ft_ratio, method)
```
`_replay_samples()` is the config-resolution + `replay_trial()` call + finite-filtering
logic already in `load_imu_trial()` today, extracted unchanged. Everything downstream
of sample-loading — config defaults, `ft_ratio`/`method` overrides (the Ockendon
personalization workflow), finite-filtering — is shared, untouched code.

**UI:** `TrialLoadPanel`'s "Phone IMU raw log" file picker's `filetypes` list widens to
`[("IMU log", "*.jsonl *.csv"), ("All files", "*.*")]`, label updated to "Phone IMU raw
log (.jsonl or split CSV)". `App.on_load_trial()` needs no changes at all — it already
just calls `engine.load_imu_trial(selection["imu_path"], ...)` regardless of format.

---

## 5. Error Handling

- Missing sibling file → `FileNotFoundError`, names the specific missing file and its
  expected derived path.
- Sibling file exists but has an unexpected header or unrecognized `sensor_name` →
  `ValueError`, names the specific file and the specific problem.
- Both surface through `App.on_load_trial()`'s existing
  `except Exception as e: messagebox.showerror("IMU load error", f"{type(e).__name__}: {e}")`
  path, unchanged.

---

## 6. Testing Plan

New tests in `tests/test_workbench_engine.py`, using synthetic split-CSV fixtures
written to `tmp_path` (matching this file's existing fixture conventions):

1. **Happy path** — three well-formed sibling CSVs (gyro/accel/mag) → `_read_split_csv_samples`
   returns a merged, chronologically-sorted list with correctly mapped `sensor`/`role`/`v`
   fields; feeding it through `load_imu_trial()` produces a finite, non-empty `(t, angle)`
   series matching what `load_imu_trial()` on an equivalent JSONL log would produce.
2. **Anchor-file independence** — passing the `_gyro.csv` path vs. the `_imu.csv` path
   (both pointing at the same trial) produces identical results.
3. **Missing sibling** — delete one raw sibling file, confirm `FileNotFoundError` names
   that specific file.
4. **Malformed header** — one sibling file has a wrong/missing header row, confirm a
   `ValueError` naming that file, raised before any row-parsing is attempted.
5. **Solo-role trial** — samples with only `"proximal"` role (matching
   `Participant_13_left`'s real data) still produce a valid angle series, exercising the
   same solo-mode branch live sessions use.

---

## 7. Out of Scope

- Reading `Trial_N_imu.csv`'s pre-computed `hip_pitch_deg`/`prox_pitch`/`dist_pitch`
  columns as an alternative or fallback data source — deliberately not built, per
  Section 1's rationale.
- Any change to `pendulastic_imu_server.py`, `imu_calibration_tuner.replay_trial()`, or
  the live raw-JSONL-logging path — this feature only adds a new *sample source* feeding
  the existing, unmodified fusion engine.
- Validating that the three sibling files' timestamp ranges actually overlap/belong to
  the same physical recording beyond the naming-convention/header checks above (e.g. no
  deeper participant/trial metadata cross-check against `Trial_N_imu.csv`'s comment
  header) — the naming convention is treated as sufficient identification, consistent
  with how this project's other file-pairing logic (e.g. video/JSONL sibling lookups)
  already works.
