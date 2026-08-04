# Workbench Raw-Sensor PT-Score Cross-Checks — Design Spec

**Status:** Approved
**Date:** 2026-08-03

---

## 1. Goal

`workbench_engine.load_imu_trial()` fuses a phone's raw gyro/accel/mag samples into a
single knee-angle series via the Madgwick AHRS filter, and Popović PT-score parameters
(`A0`, `A1`, `N`, `f`, `R2n`, `omega_max_n`, `omega_min_n`, `area_ratio`) are computed
from that one fused series. This is, and remains, the authoritative source for the PT
score — there is no established methodology for computing a "PT score" independently
from raw gyro, accel, or magnetometer data on their own (raw sensor channels are not
themselves a knee angle).

This feature adds two **supplementary, non-blocking diagnostic cross-checks**, computed
directly from the raw gyro/accel data (bypassing AHRS fusion entirely), and displayed
alongside — never in place of — the existing fused PT-score readout in the Workbench's
comparison view:

1. **Peak raw angular velocity** — the maximum raw gyro vector magnitude over the whole
   trial, as an independent sanity check against the fused-angle-derived
   `omega_max_n` (which is computed by differentiating the *filtered, fused* angle and
   can inherit fusion/smoothing lag).
2. **Raw-accel release-event time** — an independent estimate of when the pendulum
   release/first-motion happened, detected directly from a low-pass-filtered
   accelerometer tilt-magnitude signal, compared against the fused-angle-derived
   release point the PT score's `A0`/`N`/etc. are already anchored to.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `workbench_engine.py` | New `compute_raw_sensor_diagnostics(anchor_path: str) -> dict`, plus private helpers for the two individual computations. No changes to `load_imu_trial`, `replay_trial`, or any existing PT-score function — purely additive. |
| `pendulastic_app.py` | `App.on_load_trial()` calls the new function once, in its own try/except, when an IMU path is selected; stores the result for `WorkbenchView` to read |
| `pendulastic_workbench.py` | `WorkbenchView` gains a new "Raw Sensor Cross-Checks" section in its metrics readout |
| `tests/test_workbench_engine.py` | New tests for both cross-check computations and the `fs_eff` guard |
| `tests/test_app.py`, `tests/test_pendulastic_workbench.py` | New tests for the non-blocking error handling and the new display section |

---

## 3. Peak Raw Angular Velocity

```python
def _peak_raw_gyro_velocity(anchor_path: str) -> float:
    """Maximum raw gyro vector magnitude over the whole trial -- no AHRS
    fusion, no differentiation of a filtered signal. A simple max() is not
    distorted by a long resting tail the way an integral/mean would be
    (see _active_window_end's own rationale), so no active-window masking
    is needed here."""
```

Reads the raw gyro CSV (reusing `_derive_split_csv_siblings`/`_read_one_split_csv` from
the split-CSV support already in this file), computes `sqrt(x^2 + y^2 + z^2)` per row,
and returns the maximum across all rows. Units: deg/s (matching the raw gyro CSV's
existing units, same as the fused `omega_max_n`/`omega_min_n` reported elsewhere).

---

## 4. Raw-Accel Release-Event Time

```python
def _accel_release_time(anchor_path: str) -> Optional[float]:
    """Independent release-event estimate from raw accelerometer tilt,
    low-pass filtered to separate genuine limb-drop tilt change from
    linear-acceleration noise (muscle twitches, sensor jolts) and from
    sensor-placement-drift sensitivity. Returns None if the file's actual
    sample rate can't support a meaningful 5 Hz cutoff (see the fs_eff
    guard below) rather than fabricate a value from data that can't
    support it."""
```

- Reads the raw accel CSV, computes a tilt-magnitude signal
  `sqrt(x^2 + y^2 + z^2)` per row (matching the same per-row magnitude approach as
  Section 3, applied to accel instead of gyro).
- Computes the file's own **effective sample rate** from its actual timestamps:
  `fs_eff = 1.0 / median(diff(t))` — never a hardcoded assumption, so files logged at
  different rates (50 Hz, 100 Hz, 200 Hz, etc.) each get a correctly-scaled filter.
- **Guard:** `_MIN_FS_FOR_5HZ_CUTOFF_HZ = 20.0`. Nyquist requires `fs > 2 x cutoff`
  (10.0 Hz here) for a 5 Hz cutoff to be meaningful at all; this constant doubles that
  bare theoretical minimum, since a filter design right at the Nyquist edge produces
  significant phase distortion even when technically valid. If
  `fs_eff < _MIN_FS_FOR_5HZ_CUTOFF_HZ`, return `None` immediately rather than design a
  filter on data that can't support it. This is the same class of low-rate
  data-integrity problem this project has hit before (a real trial recorded at ~1 Hz
  due to a phone misconfiguration earlier this session) — silently proceeding with an
  invalid filter design would produce a misleading, fabricated release time instead of
  surfacing the real limitation.
- Otherwise, applies a **4th-order Butterworth low-pass filter, 5 Hz cutoff**, cutoff
  normalized against `fs_eff` (`scipy.signal.butter(4, 5.0, btype="low", fs=fs_eff)`,
  applied via `scipy.signal.filtfilt` for zero-phase filtering — critical here, since
  a non-zero-phase filter would reintroduce exactly the phase-lag distortion this
  feature exists to avoid).
- Runs an adapted version of `pendulastic_pt_score._detect_release`'s detection logic
  against the filtered tilt-magnitude signal: baseline from the first ~0.6s, threshold
  at 8% of the signal's own range (`thresh = 0.08 * signal_range`). Unlike
  `_detect_release`, this has **no fixed absolute floor** on the threshold —
  `_detect_release`'s `max(2.0, ...)` floor is calibrated for degree-scale angle
  signals and doesn't generalize to accel-magnitude data, whose units vary by source
  (the real `Participant_13_left` files are in g-units, magnitude ~1.0 at rest; this
  project's own earlier test fixtures happened to use m/s²-units, ~9.81) — a fixed
  floor would silently behave inconsistently across unit systems, while the
  percentile-based component alone self-scales to whatever range the signal actually
  has, regardless of units.

---

## 5. Integration

`App.on_load_trial()` (`pendulastic_app.py`), after the existing IMU-load block:

```python
        self._workbench_raw_diagnostics = None
        if selection["imu_path"]:
            try:
                self._workbench_raw_diagnostics = _wb_engine.compute_raw_sensor_diagnostics(
                    selection["imu_path"])
            except Exception:
                pass   # supplementary cross-check only -- never blocks the trial load
```

This is a **separate** try/except from the existing `load_imu_trial()` call (Section 1's
"never blocking" requirement) — a cross-check failure produces no dialog and no error at
all, it simply leaves `_workbench_raw_diagnostics` as `None`, and `WorkbenchView` omits
the section entirely when it's `None`.

`WorkbenchView.get_metrics_snapshot()`/`_recompute_metrics()` (`pendulastic_workbench.py`)
gain a new, clearly-separated section in the metrics text, appended after the existing
`vs_reference` lines:

```
Raw Sensor Cross-Checks (independent of PT score fusion):
  Peak angular velocity (raw gyro): 245.3 deg/s
  Release detected (raw accel, 5Hz low-pass): t=1.02s
```

If `accel_release_time_sec` is `None` (the Nyquist guard tripped), that line reads
`Release detected (raw accel, 5Hz low-pass): unavailable (sample rate too low)` rather
than being silently omitted — the researcher should know the check was attempted and
why it didn't produce a value, not just see it missing.

---

## 6. Testing Plan

1. **Peak gyro velocity** — synthetic gyro CSV with a known burst magnitude; confirm
   the returned value matches the known peak within floating-point tolerance.
2. **Accel release-time, happy path** — synthetic accel CSV at a healthy sample rate
   (e.g. 100 Hz) with a clear tilt-magnitude step at a known timestamp; confirm the
   detected release time is close to the known step.
3. **Accel release-time, Nyquist guard** — synthetic accel CSV at a low effective rate
   (e.g. ~5 Hz, computed from sparse timestamps) confirms `None` is returned rather
   than a fabricated value.
4. **`fs_eff` computed from actual file timestamps, not assumed** — two synthetic accel
   CSVs with the identical tilt-step shape but different sample spacing (e.g. 50 Hz vs
   200 Hz) both correctly detect the release near the same known timestamp — proving
   the filter design adapts to each file's own rate rather than using one hardcoded
   assumption.
5. **`compute_raw_sensor_diagnostics` integration** — ties Sections 3+4 together via
   the shared sibling-derivation path, confirms the returned dict has both keys.
6. **Non-blocking error handling** (`tests/test_app.py`) — `_wb_engine.compute_raw_sensor_diagnostics`
   monkeypatched to raise; confirm `on_load_trial` still successfully loads the trial
   and shows `WorkbenchView` (the primary IMU trace/PT score are unaffected), with
   `_workbench_raw_diagnostics` left `None`.
7. **Display section** (`tests/test_pendulastic_workbench.py`) — `WorkbenchView` shown
   the new section's text when raw diagnostics are present; omits it (or shows the
   "unavailable" line for the release-time specifically) when absent/partial.

---

## 7. Out of Scope

- Any change to `load_imu_trial()`'s existing signature, `replay_trial()`, or the
  fused-angle Popović PT-score computation itself — this feature is purely additive.
- Computing a genuinely separate "PT score" from raw channels independently (the
  rejected Approach 2 from brainstorming) — not biomechanically meaningful, and
  explicitly not what this feature does.
- Applying these cross-checks to the JSONL raw-log IMU path — the split-CSV format is
  what surfaced this need (comparing anchor-file choices), and the JSONL path already
  has `imu_calibration_tuner`'s own established diagnostics; extending cross-checks
  there is a separate, unscoped question.
- Resolving the original unreproduced "RMSE changes with anchor file" report — that
  remains open and separate from this feature (see design rationale in the initial
  investigation; a controlled test with a fixed config produced byte-identical results
  across all four anchors).
