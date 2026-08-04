# IMU Stillness-Gated Gyro-Bias Calibration & Drift Investigation — Design Spec
**Date:** 2026-08-04
**Status:** Approved

---

## 1. Goal

Real trials show high RMSE and visible drift against OptiTrack ground truth. One confirmed
contributor: gyro-bias calibration has been triggered off windows that merely *look* still —
fused pitch/roll holding steady, or being under the much coarser flex-axis motion threshold —
when the sensor was actually being gripped and repositioned by the examiner. One such window
measured a "bias" of 12.7°/s, an order of magnitude above a real MEMS static offset; subtracting
it distorted the swing instead of correcting it.

This spec covers three deliverables:

- **(A)** Replace every stillness check that gates gyro-bias calibration — both the live
  acquisition path and the offline replay/tuning path — with a direct check on raw gyro variance
  and raw accel-magnitude stability, instead of a derived-signal proxy (fused angle, or the
  flex-axis threshold). Applies continuously, both paths, so the fix reaches both new recordings
  and re-analysis of existing ones.
- **(B)** A one-off, non-pipeline investigation of accelerometer drift via double integration, to
  characterize whether/how much it contributes to the RMSE problem.
- **(C)** Extend the OptiTrack validation harness to combine full-corpus trial crawling with
  clinical-grade reliability metrics (RMSE, ICC, Bland-Altman), and use it to capture a pre-fix
  baseline and a post-fix diff — empirical proof (A) improves accuracy without regressing any
  trial.

---

## 2. Relationship to Existing Work

**Correction from an earlier draft of this spec:** this section originally claimed the IMU
auto-tare plan (`docs/superpowers/specs/2026-07-31-imu-auto-tare-design.md`) was never
implemented. That was wrong — verified directly against `pendulastic_app.py`, it is **fully
implemented and committed to main** (commits `2057914`..`4ad18ef`): `App.on_countdown_start()`,
`App.is_imu_calibrated()`, `App._tick_calibration_check()`, the countdown extend-then-confirm
fallback, and removal of the manual Zero Sensor/Clear Zero UI all exist and work today. The
corrected picture:

- `pendulastic_imu_server.py` already has bias-subtraction infrastructure
  (`calibrate_gyro_bias()`, `gyro_bias`, `_gyro_hold_buf`) added (currently uncommitted), and
  `zero()` already calls it. `zero()` is already called correctly — by
  `App._tick_calibration_check()`'s edge-trigger, not a manual button (that path is gone). The gap
  is only *what signal* `_tick_calibration_check()` uses to decide "stable": today it maintains its
  own trailing buffer of **fused pitch/roll** (`App._calib_buffer`, `_CALIB_STABILITY_RANGE_DEG`,
  `_CALIB_BUFFER_SAMPLES`, `pendulastic_app.py:1858-1898`) read from `_imu.get_state()["angles"]`.
  Section 3.3 below modifies this existing method in place — replacing its internal buffer/threshold
  logic with a call to a new module-level `pendulastic_imu_server.is_stationary()` — rather than
  building any new gating mechanism. `on_countdown_start()`, `is_imu_calibrated()`, and the
  extend-then-confirm fallback need no changes: they only depend on `_calib_ever_stable`/
  `_calib_was_stable`, which stay as the edge-trigger/latch state regardless of which signal feeds
  them.
- `imu_calibration_tuner.py`'s `replay_trial()` already has a stillness-gated calibration block
  (currently uncommitted), but it gates on *fused* pitch/roll swing — a deliberate mirror of
  `_tick_calibration_check()`'s current (soon-to-be-replaced) approach, per its own comments — not
  raw gyro/accel. Section 3.4 replaces this block's stability check with the same shared primitive
  Section 3.3 wires into the live path. The bias-subtraction mechanics it already has
  (edge-triggering, the raw-gyro hold buffer, `gyro_bias` subtraction before `ahrs.update()`) are
  correct and are kept as-is.

This spec **supersedes Section 4 ("Stability Detection Algorithm")** of
`docs/superpowers/specs/2026-07-31-imu-auto-tare-design.md` — replacing its fused-pitch/roll signal
choice with Section 3's raw gyro/accel check, inside the already-built gating mechanism that
spec's Sections 3, 5, and 6 describe (continuous re-tare, the extend-then-confirm fallback, and the
removed manual UI) — none of which change here.

---

## 3. Component A: Shared Raw-Signal Stationarity Check

### 3.1 The shared primitive

A new module-level function in `pendulastic_imu_server.py`:

```python
def _is_stationary_window(gyro_buf: list[tuple[float, np.ndarray]],
                          accel_buf: list[tuple[float, np.ndarray]],
                          now: float) -> bool
```

Pure function of two trailing raw-sample buffers (`[(t, vec), ...]`) — no dependency on
`_IMUDevice` internals — so it can be imported and reused verbatim by both the live device class
and the offline replay's per-role state, the same way `imu_calibration_tuner.py` already imports
`GYRO_BIAS_WINDOW_S`/`GYRO_BIAS_MIN_SAMPLES` from this module. One implementation; two call sites.
This directly avoids the failure mode the current uncommitted WIP has: two independently
"mirrored" copies of the same logic (per its own comments) that can silently drift out of sync.

Returns `True` only when, over a window spanning the full `GYRO_BIAS_WINDOW_S` (requiring the
buffer's oldest sample to be at least `0.95 * GYRO_BIAS_WINDOW_S` old — not just "has enough
entries," the same guard the existing WIP already has for its fused-angle version):

- **Raw gyro:** peak-to-peak of `‖gyro‖` across the window is below `GYRO_STATIONARY_MAX_DPS`.
- **Raw accel:** peak-to-peak of `‖accel‖` across the window is below `ACCEL_STATIONARY_MAX_MPS2`.

Both conditions required — gyro alone would miss linear-acceleration handling (sliding the limb
without rotating it much); accel alone would miss rotational handling.

### 3.2 Threshold values — determined empirically, not guessed

`GYRO_STATIONARY_MAX_DPS` and `ACCEL_STATIONARY_MAX_MPS2` are **not** hardcoded from first
principles. Task 1 of the implementation plan pulls real pre-release segments from existing
`data/*_imu_raw.jsonl` recordings — including whichever trial produced the 12.7°/s contamination
case — and computes actual raw gyro/accel variance during known-still vs. known-handled windows,
the same empirical approach that originally produced the `2.0°` / `1.0s` fused-angle constants.
Placeholder constants in this spec get replaced with measured ones before Task 2 depends on them.

### 3.3 Live wiring

`_IMUDevice` gains a new raw buffer, `_accel_hold_buf`, appended in `on_accel(self, v, ts)`
(`pendulastic_imu_server.py:334`) exactly the way `_gyro_hold_buf` is already appended in
`on_gyro()` — trailing `GYRO_BIAS_WINDOW_S`, pruned the same way. `_IMUDevice.is_stationary() -> bool`
calls `_is_stationary_window(self._gyro_hold_buf, self._accel_hold_buf, now)`.

`pendulastic_imu_server.py` gains a new module-level `is_stationary() -> bool`, mirroring `zero()`'s
existing per-connected-device iteration pattern (`_by_role`/`_devices`): `True` only if every
*connected* device (proximal and/or distal, whichever are active) independently reports stationary
— a half-stationary reading (one device still, one being handled) must not pass. `App` imports
`pendulastic_imu_server` as `_imu` already (see `_tick_calibration_check`'s existing
`_imu.get_state()`/`_imu.zero()` calls), so this is called as `_imu.is_stationary()`.

**This modifies the already-implemented `App._tick_calibration_check()`
(`pendulastic_app.py:1858-1898`) in place** — it is not new gating logic. Delete the method's
internal fused-pitch/roll buffer entirely (the `st = _imu.get_state(); ang = ...; pitch, roll = ...`
block, `self._calib_buffer` append/trim, and the `stable = (max(pitches) - min(pitches) < ...)`
check) and replace the `stable` computation with `stable = _imu.is_stationary()`. The
edge-trigger/latch state machine around it — `self._calib_was_stable`, `self._calib_ever_stable`,
the `if stable and not self._calib_was_stable: _imu.zero(); ...` block, the guard against
re-firing every ~1s during one continuous hold — is unchanged; it only cares about the boolean
`stable`, not how it was computed. `App.__init__`'s `self._calib_buffer: list = []` and the
module-level `_CALIB_STABILITY_RANGE_DEG`/`_CALIB_BUFFER_SAMPLES` constants become dead code and are
removed. `on_countdown_start()`, `is_imu_calibrated()`, and the countdown extend-then-confirm
fallback are untouched. `zero()`'s existing `calibrate_gyro_bias()` calls are unchanged.

### 3.4 Offline wiring

`imu_calibration_tuner.py`'s `replay_trial()` currently maintains `st.stability_buf` (fused
pitch/roll) to gate calibration. This spec replaces that block: `_RoleState` gains
`accel_hold_buf` alongside its existing `gyro_hold_buf`, both appended per-sample the same way
`on_gyro()`/`on_accel()` do live, and the edge-trigger condition becomes
`_is_stationary_window(st.gyro_hold_buf, st.accel_hold_buf, samp["t"])` instead of the fused-angle
peak-to-peak check. The bias-subtraction mechanics below it (`st.gyro_bias` mean-of-buffer, subtraction
before `st.ahrs.update()`) are unchanged.

### 3.5 The missing regression test

The current WIP's only bias test (`test_replay_trial_subtracts_calibrated_gyro_bias`) proves a
*constant* bias present the whole trial gets measured and subtracted. It does not prove the actual
bug is fixed. New test: a synthetic "examiner handling" window with real raw-gyro *and* raw-accel
magnitude variation (oscillating past both new thresholds, comparable to the 12.7°/s case) held for
a full `GYRO_BIAS_WINDOW_S`+ duration — `gyro_bias` must stay at zero, not get set from it — paired
with a companion genuinely-still window (flat raw gyro and accel) that must still calibrate
correctly. Both cases go in `tests/test_imu_calibration_tuner.py`; the live-side equivalent
(`_IMUDevice.is_stationary()` rejecting a synthetic handling window) goes in
`tests/test_imu_server.py`.

---

## 4. Component B: Accelerometer Drift Investigation (One-Off)

A standalone script, `analyze_accel_drift.py`, not wired into any pipeline and not covered by the
automated test suite — matching this repo's existing standalone-analysis-script convention
(`evaluate_all_participants.py`, `validate_tracking.py`).

For each raw trial log:
1. Load raw samples (reuse `tune_imu.py`'s `load_raw_log`).
2. Replay through the AHRS via `imu_calibration_tuner.py`'s `replay_trial`, now benefiting from
   Component A's corrected bias calibration.
3. Rotate raw accel into the world frame using the replayed orientation and subtract gravity,
   giving linear acceleration.
4. Double-integrate: linear accel → velocity → displacement. As a diagnostic aid (not a
   correction), use Component A's `_is_stationary_window` results as zero-velocity reference
   points — during a verified-still window, true velocity is known to be exactly 0, so the gap
   between that and the naive double-integration's velocity at the same point directly measures
   accumulated drift, rather than inferring it indirectly.
5. Report per trial: peak drift magnitude, a drift-vs-time plot, and whether the magnitude is
   large enough relative to the pendulum swing's expected physical displacement to plausibly
   explain part of the RMSE problem.

Output goes to `figures/` or `Model_Analysis_Outputs/`, matching existing script conventions. The
deliverable is the finding — whether accel drift meaningfully contributes to the RMSE problem, and
by how much — not a permanent feature. A "yes, and here's the magnitude" result is a candidate for
a future separate task; this task only characterizes it.

---

## 5. Component C: Combined Validation Harness

`evaluate_all_participants.py`'s generic `(participant, position, trial)` auto-discovery is
extracted into a form `validate_controls.py` can also call, replacing the latter's hardcoded
`CTRL_PARTICIPANTS` allowlist. `evaluate_all_participants.py` imports (not duplicates)
`validate_controls.py`'s already-tested `_icc_one_way`, Bland-Altman, and RMSE helper functions.

One harness run then reports, for every discovered trial with both an OptiTrack CSV and an
IMU-derived angle CSV:
- Per-trial RMSE (existing `swing_rmse.png`/leaderboard behavior, now covering every discovered
  participant, not just the healthy-controls subset).
- ICC/Bland-Altman reliability metrics for any participant with ≥2 trials — the existing helper's
  own natural filter (`groups = [g for g in groups if len(g) >= 2]`) already handles this; no new
  allowlist needed.

**Before/after validation:** run the combined harness once now, before Component A lands (baseline,
pre-fix), and again afterward; diff the two leaderboards per trial. This is a manual verification
step in the implementation plan — matching this repo's existing "final task: full regression +
manual acceptance" pattern — not new persistent code. Confirms the task's explicit requirement:
RMSE improves across trials, with no trial regressing.

---

## 6. Testing Plan

1. **`_is_stationary_window`** — direct unit tests: flat raw gyro+accel → stationary; either signal
   alone exceeding its threshold → not stationary; window not yet spanning the full duration → not
   stationary (regardless of content).
2. **Live wiring** — `_IMUDevice.is_stationary()` reflects the buffers correctly; a synthetic
   handling window (Section 3.5) is rejected; a synthetic genuine hold calibrates correctly.
   `App`'s countdown only tares when *all* connected devices report stationary.
3. **Offline wiring** — `replay_trial()`'s new stability gate: same handling-window-rejected /
   genuine-hold-accepted pair as the live tests, plus the existing constant-bias-throughout-trial
   test continues to pass unchanged.
4. **Component C helpers** — the extracted discovery function returns the same trial set
   `evaluate_all_participants.py` already discovers today; ICC/Bland-Altman functions produce
   identical output to `validate_controls.py`'s existing (already-tested) results when given the
   same healthy-controls input, proving the extraction didn't change their behavior.
5. **Full regression** — existing suite stays green.
6. **Manual acceptance** — Component B's report is generated against real recorded trials and
   reviewed for plausibility; Component C's before/after diff is generated and reviewed to confirm
   no trial regresses.

---

## 7. Out of Scope

- Feeding Component B's drift measurement back into the fused-angle pipeline (ZUPT-style
  correction) — this task only characterizes drift, it does not correct for it. A future task, if
  Component B's findings justify one.
- Configurable stationarity thresholds/window duration as user-facing settings — fixed constants,
  same as the existing `2.0°`/`1.0s` fused-angle constants they replace.
- Reconciling or merging any of the currently-locked, in-flight worktrees
  (`workbench-raw-sensor-crosschecks`, `waveform-t0-alignment`) — out of scope for this task; the
  user is handling those separately.
- Changing `_FLEX_CAPTURE_THRESHOLD` or the flex-axis capture logic — unrelated mechanism, already
  correctly scoped to "big enough to be deliberate motion," not "small enough to be stillness."
