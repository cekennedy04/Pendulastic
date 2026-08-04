# Guided Sequential 4-Component CSV Intake — Design Spec

**Status:** Approved
**Date:** 2026-08-04

---

## 1. Goal

Some trials' phone-IMU data arrives as four sibling raw CSVs (`_accel.csv`, `_gyro.csv`,
`_mag.csv`, `_imu.csv`) rather than the single JSONL raw log. The Workbench's existing
support for this format (`docs/superpowers/specs/2026-08-03-workbench-split-csv-imu-design.md`)
auto-derives all four sibling paths from a single anchor-file click and validates only
that each file's header matches and each row's `sensor_name` is *some* recognized value
— it does not confirm the anchor-derived paths are the *correct* files, nor that a file
dropped into one role doesn't actually contain another sensor's data, nor that any
file's timestamps or sample rate are sane.

This feature replaces that auto-derive flow with an explicit, one-slot-per-component
guided intake: the user browses to each of the four files individually (Accelerometer,
Gyroscope, Magnetometer, raw IMU), each file is validated independently the moment it's
selected, and a live status readout shows pass/fail per file. The pipeline only binds
the four into a single synchronized dataset once all four are independently verified —
closing the "wrong file in the wrong slot" and "silently proceeds on bad data" gaps in
today's flow. The bound dataset then feeds the same, unmodified Madgwick-fusion knee
angle computation and downstream Popović PT-score calculations as before.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `workbench_engine.py` | Remove `_derive_split_csv_siblings()` and the anchor-based `_read_split_csv_samples()`/`_read_one_split_csv()`. Add `validate_component_csv(path, kind) -> dict`, `bind_split_csv_components(validations) -> dict`, `load_imu_trial_from_components(validations, config=None, ft_ratio=None, method=None)`. `load_imu_trial()` becomes JSONL-only; given a non-`.jsonl` path it raises `ValueError` directing the caller to the new component-based entry point. |
| `pendulastic_workbench.py` | `TrialLoadPanel` gains an IMU-format toggle ("Single raw log (.jsonl)" / "Split CSV (4 files)") and, in Split CSV mode, four independent slots (Accelerometer/Gyroscope/Magnetometer/Raw IMU) each with its own Browse button and live status label, replacing the single "Phone IMU raw log" row for that mode. `App.on_load_trial()` branches on the selected format. |
| `pendulastic_app.py` | `App.on_load_trial()` (its own copy, per existing duplication between the standalone Workbench app and the main app's embedded Workbench mode) gets the same branch. |
| `tests/test_workbench_engine.py` | Remove the now-obsolete anchor-derivation tests (`test_derive_split_csv_siblings_from_non_imu_anchor`, `test_load_imu_trial_same_result_regardless_of_which_sibling_is_the_anchor`, `test_load_imu_trial_dispatches_to_split_csv_for_non_jsonl_path`). Add tests for `validate_component_csv`, `bind_split_csv_components`, and `load_imu_trial_from_components` (Section 6). |

---

## 3. Component Schemas

```python
_COMPONENT_HEADERS = {
    "accel": ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "gyro":  ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "mag":   ["timestamp_ms", "phone_ts_ms", "role", "sensor_name", "x", "y", "z"],
    "imu":   ["t_epoch", "t_rel", "phone_ts_ms", "t_phone_aligned",
              "hip_roll_deg", "hip_pitch_deg", "hip_yaw_deg",
              "prox_roll", "prox_pitch", "prox_yaw",
              "dist_roll", "dist_pitch", "dist_yaw", "paired"]
}
_COMPONENT_SENSOR_NAME = {"accel": "Accelerometer", "gyro": "Gyroscope", "mag": "Magnetometer"}
_MIN_FS_FOR_FUSION_HZ = 10.0
```

- `accel`/`gyro`/`mag` schemas and `timestamp_ms → t = timestamp_ms / 1000.0` conversion
  are unchanged from the existing split-CSV format (Section 3 of the prior spec).
- `imu` (the fourth, raw-IMU-stream file) uses the header `pendulastic_imu_server.py`'s
  `_log_sample()` writer produces (line ~870-876). Its rows are parsed into
  `{"t_epoch": float, "hip_pitch_deg": float, "prox_pitch": float, "dist_pitch": float,
  ...all other columns, string-preserved...}` — full fidelity, since this file is a
  reference/cross-check artifact, not fusion input (Section 5).
- `_MIN_FS_FOR_FUSION_HZ = 10.0` is a distinct constant from the unrelated
  `_MIN_FS_FOR_5HZ_CUTOFF_HZ = 20.0` used by the raw-sensor-crosschecks feature's
  Butterworth filter design — this floor exists to catch gross recording
  misconfigurations (e.g. the ~1 Hz recording incident referenced in that spec), not to
  guarantee a specific downstream filter's validity. It applies to all four component
  kinds, including `imu`.

---

## 4. `validate_component_csv(path: str, kind: str) -> dict`

`kind` is one of `"accel"`, `"gyro"`, `"mag"`, `"imu"`. Called by the UI immediately
when a slot's file is browsed to, independent of the other three slots' state.

Returns `{"ok": bool, "error": Optional[str], "n_samples": int,
"fs_eff": Optional[float], "rows": list}`. `rows` holds the fully parsed samples
(same shape `bind_split_csv_components` expects) so the file is never re-read once
validated. On failure, `ok=False`, `error` holds a human-readable message naming the
file and the specific problem, and `n_samples`/`fs_eff`/`rows` are `0`/`None`/`[]`.

Validation order (first failure wins, no partial-row parsing past the failure point):

1. **File existence** — `FileNotFoundError`-style message if the path doesn't exist,
   surfaced through the same `ok=False`/`error` contract (not raised — this function
   never raises; callers get a result dict either way, since the UI needs to show an
   error for *any* bad slot, not just some).
2. **Header** — read the first row, compare against `_COMPONENT_HEADERS[kind]`
   (exact match, same strictness as today's accel/gyro/mag check, newly applied to
   `imu` too). Mismatch (missing entirely, wrong columns, or wrong column count) →
   error naming the file, the expected header, and what was found.
3. **Per-row parsing, in file order:**
   - **Column count** — each row must have the same number of fields as the header.
   - **Sensor-name consistency** (`accel`/`gyro`/`mag` only) — the row's `sensor_name`
     must equal `_COMPONENT_SENSOR_NAME[kind]` exactly. A row with a different
     recognized sensor name (e.g. `"Gyroscope"` found in a file browsed into the Accel
     slot) fails with an error naming the file, the row number, the expected sensor for
     that slot, and the sensor name actually found — this is the check today's
     anchor-derived flow doesn't perform, since it only checked "is this *some* known
     sensor," not "is this file the sensor this slot claims."
   - **Timestamp monotonicity** — the timestamp column (`timestamp_ms` for
     accel/gyro/mag, `t_epoch` for imu) must be non-decreasing row-to-row *as read from
     the file* (before any cross-file merge/sort happens in `bind_split_csv_components`).
     A decrease fails with an error naming the file, the row number, and the two
     out-of-order values.
4. **`fs_eff` computation and floor** — once all rows parse and pass the above,
   `fs_eff = 1.0 / median(diff(t))` over that file's own timestamps (`t` in seconds).
   If `fs_eff < _MIN_FS_FOR_FUSION_HZ`, fails with an error naming the file and the
   computed rate. A file with fewer than 2 rows can't compute `fs_eff` at all — treated
   as a failure ("not enough samples to compute an effective sample rate"), since a
   single-sample file can never usefully feed fusion.

---

## 5. `bind_split_csv_components(validations: dict) -> dict`

`validations` is `{"accel": <result>, "gyro": <result>, "mag": <result>,
"imu": <result>}`, each value the dict `validate_component_csv` returned for that slot.

Defensive re-check: if any of the four entries is missing or has `ok=False`, raises
`ValueError` naming which kind(s) aren't ready. (The intended caller — the UI's "Load
Trial" button — is only reachable once all four slots are green, so this is a backstop,
not the primary UX gate; see Section 7.)

Otherwise:
- `fusion_samples`: concatenates `validations["accel"]["rows"]`,
  `validations["gyro"]["rows"]`, `validations["mag"]["rows"]`, sorted by `"t"` —
  identical shape/contract to today's merged split-CSV samples list, so
  `imu_calibration_tuner.replay_trial()` is unaffected.
- `imu_reference`: `validations["imu"]["rows"]`, passed through unchanged — attached to
  the trial for cross-check purposes (Section 1), never merged into `fusion_samples` and
  never fed into `replay_trial()`.

Returns `{"fusion_samples": [...], "imu_reference": [...]}`.

---

## 6. `load_imu_trial_from_components(validations, config=None, ft_ratio=None, method=None)`

The split-CSV counterpart to today's `load_imu_trial()`. Calls
`bind_split_csv_components(validations)`, runs `fusion_samples` through the existing
`_replay_samples()` (config resolution + `replay_trial()` + finite-filtering — unchanged),
and returns `(t, angle, imu_reference)` — one extra element versus `load_imu_trial()`'s
`(t, angle)`, carrying the cross-check data up to the caller.

`load_imu_trial(path, ...)` itself is simplified to JSONL-only: given a path that
doesn't end in `.jsonl`, it raises `ValueError` stating that split-CSV trials must go
through `load_imu_trial_from_components`. (No caller today passes it a bare CSV path
outside the UI flow this feature replaces — confirmed via a repo-wide search for
`load_imu_trial(` — so this is not a breaking change to any other script.)

---

## 7. UI: `TrialLoadPanel` (`pendulastic_workbench.py`)

- A new format toggle above the IMU row(s): **"Single raw log (.jsonl)"** (default,
  today's behavior — single "Phone IMU raw log" browse row, unchanged) vs. **"Split CSV
  (4 files)"** (this feature). Switching the toggle swaps which row(s) are shown.
- In Split CSV mode: four rows, one per component (**Accelerometer**, **Gyroscope**,
  **Magnetometer**, **Raw IMU**), each with its own Browse button and a status label
  to its right. No auto-fill/suggestion of sibling paths between slots — each is
  independently browsed (per the "replace auto-derive" decision; the whole point is
  explicit per-file confirmation, not convenience-driven guessing).
- Browsing a slot immediately calls `validate_component_csv(path, kind)` and updates
  that slot's status label: `"✓ 1,432 samples @ 52.1 Hz"` (green) on success, or
  `"✗ " + error` (red) on failure. Slots are independent — fixing one doesn't disturb
  the other three's already-validated state (a failed/red slot can simply be
  re-browsed).
- Clicking **"Load Trial"**:
  - If Split CSV mode is selected and **zero** of the four slots have a path: IMU is
    skipped entirely, same as leaving the single JSONL row blank today (video/OptiTrack
    can still proceed alone, per the existing "at least one of the three" rule).
  - If **1–3** slots have a path (regardless of validation state) or all 4 have paths
    but at least one is still `ok=False`: blocked with an error naming which
    component(s) are missing or still invalid — mirrors the existing
    `"Select at least one of: IMU log, video, OptiTrack CSV."` messagebox pattern.
  - If all 4 are `ok=True`: calls `load_imu_trial_from_components(validations, ...)`,
    stores the resulting `(t, angle)` in `traces["imu"]` exactly as `load_imu_trial()`
    does today, and stores `imu_reference` in `self._trial_meta["imu_reference"]` (no
    new visualization consumes it in this feature — Section 8).
  - `self._trial_meta` stores `imu_paths: {"accel": ..., "gyro": ..., "mag": ...,
    "imu": ...}` for the Split CSV case, instead of the single-string `imu_path` used
    for JSONL trials — `export_session()` serializes whichever is present unchanged (it
    is a pure dict-builder with no format-specific logic to update).
- `App.on_load_trial()` in both `pendulastic_workbench.py` and `pendulastic_app.py`
  (each has its own copy today) branches on which format was selected in the panel and
  calls the corresponding engine function.

---

## 8. Out of Scope

- Any new visualization/UI surface for `imu_reference` — it is parsed, validated, and
  attached to trial data only, per Section 1's cross-check-only rationale (confirmed:
  no display work in this feature).
- Cross-file validation (e.g. confirming the four files' timestamp ranges actually
  overlap/belong to the same physical recording) — each file is validated
  independently; this mirrors the prior spec's Section 7 scope decision.
- Any change to the JSONL raw-log loading path, `imu_calibration_tuner.replay_trial()`,
  or the live raw-CSV-logging path in `pendulastic_imu_server.py`.
- Auto-suggesting sibling paths as a convenience once one slot is filled — deliberately
  removed, not merely unimplemented (Section 7).
- Using `_imu.csv`'s pre-computed `hip_pitch_deg` as a fusion input or fallback — same
  single-methodology rationale as the prior spec, unchanged by this feature.

---

## 9. Testing Plan

New tests in `tests/test_workbench_engine.py`, using per-component synthetic CSV
fixture writers (extending the existing `tmp_path`-based fixture conventions):

1. **Happy path** — all four files well-formed and internally consistent →
   `validate_component_csv` returns `ok=True` with correct `n_samples`/`fs_eff` for
   each; `bind_split_csv_components` produces a chronologically-sorted
   `fusion_samples` list and a separate `imu_reference` list;
   `load_imu_trial_from_components` produces a finite, non-empty `(t, angle)` series
   equivalent to feeding the same merged samples through `load_imu_trial()` on an
   equivalent JSONL log.
2. **Header mismatch, each kind** — a wrong/missing header on the accel, gyro, mag, and
   imu files (four cases) each fail with an error naming that specific file.
3. **Sensor-name mismatch** — a file browsed into the Accel slot whose rows say
   `sensor_name == "Gyroscope"` fails, naming the file, the row, and both the expected
   and actual sensor names. Repeat for at least one other slot/sensor pairing.
4. **Non-monotonic timestamps** — a file with a timestamp that decreases partway
   through fails, naming the file, the row, and the two out-of-order values.
5. **`fs_eff` below floor** — a file with sparse timestamps giving `fs_eff` under
   10.0 Hz fails, naming the file and the computed rate.
6. **`fs_eff` at/above floor is not a false positive** — a file at a legitimately low
   but valid rate (e.g. ~12 Hz) returns `ok=True`.
7. **Fewer than 2 rows** — a file with 0 or 1 data rows fails with a
   not-enough-samples error rather than a division error or silent success.
8. **`imu` component validated and separated correctly** — a well-formed `_imu.csv`
   validates using its own header/schema, and `bind_split_csv_components` places its
   rows under `imu_reference`, never merging them into `fusion_samples`.
9. **`bind_split_csv_components` defensive re-check** — calling it with one kind
   missing, or with one kind's `ok=False`, raises `ValueError` naming that kind.
10. **Solo-role trial** — samples with only `"proximal"` role populated (matching real
    historical data, e.g. `Participant_13_left`) still produce a valid angle series
    through `load_imu_trial_from_components`, exercising the same solo-mode branch
    live sessions use.
11. **`load_imu_trial()` rejects non-JSONL paths** — calling it with a `.csv` path
    raises `ValueError` directing the caller to `load_imu_trial_from_components`.
