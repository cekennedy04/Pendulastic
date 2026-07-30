# IMU Adaptive Self-Tuning Calibration Loop — Design Spec
**Date:** 2026-07-30
**Status:** Approved

---

## 1. Goal

The single-phone-on-shank IMU pendulum test (`pendulastic_imu_server.py`'s Madgwick AHRS →
`swing_angle_deg()` → `pendulastic_app.py`'s `BiomechanicalEngine`) has had several targeted fixes
(AHRS gravity-seeding, dynamic flexion-axis capture, EMA smoothing for staircase artifacts) but
still does not reliably produce a clinically usable waveform. Rather than another manual fix, build
an adaptive, self-tuning loop that:

1. Iterates over the AHRS/fusion parameters already implicated in prior fix commits (Madgwick
   `BETA`, EMA `alpha`, flexion-axis capture on/off, gravity-seeding on/off).
2. Evaluates each candidate against the pendulum test's known physical constraints — horizontal
   start at 180°, oscillation decaying through roughly 90°–180°, a continuous curve with no flat
   plateaus/steps/clipping — plus a truthfulness check via the existing Popović-parameter pipeline.
3. Persists the winning configuration to a single global config file so future trials start from the
   best-known settings automatically.

Both a live per-trial path and a standalone offline/batch CLI share one engine — no duplicated
fusion math.

---

## 2. File Impact Matrix

| File | Nature of change |
|---|---|
| `pendulastic_imu_server.py` | Add raw accel/gyro/mag JSONL logging alongside the existing fused-angle CSV (`on_accel`/`on_mag`/`on_gyro`, `start_recording`/`stop_recording`); add `current_raw_log_path()`; replace hardcoded `BETA` and the flex-axis/gravity-seed toggles with `load_config()`-driven values |
| `pendulastic_app.py` | `_imu_poll_worker`'s hardcoded `_EMA_ALPHA` becomes config-driven; IMU RECORDING→REVIEW transition calls `tune()` on the background worker thread already used for other sources, persists on improvement, feeds the REVIEW graph the tuned series |
| `imu_calibration_tuner.py` (new) | `replay_trial`, `score_waveform`, `tune`, `load_config`/`save_config` — the shared engine |
| `tune_imu.py` (new) | Thin CLI wrapper: `tune_imu.py <raw_log.jsonl> [more...] [--force]` |
| `pendulastic_pt_score.py` | No changes — `compute_pt_params` is imported and reused as-is |
| `imu_calibration_config.json` (new, generated) | Persisted winning `{beta, ema_alpha, flex_axis_capture, gravity_seed}` |

---

## 3. Raw Sample Logging

`on_accel()`, `on_mag()`, `on_gyro()` in `pendulastic_imu_server.py` already fire on every incoming
packet. Each gains one call, gated by the existing `_recording` flag `_log_sample()` already checks:

```python
_raw_log_writer(role, sensor, v, phone_ts_ms)
```

**Format — JSONL** (one packet per line; accel/gyro/mag arrive independently and asynchronously, so
a rigid CSV schema doesn't fit):

```json
{"t": 1753875600.114, "role": "distal", "sensor": "gyro", "v": [0.01, -1.42, 0.03], "phone_ts_ms": 88231044}
```

`role` is read live from `_by_role()` at capture time (not baked in at trial start), so a mid-trial
`swap_roles()` call still replays correctly.

**File naming:** derived from the existing CSV path — `start_recording(csv_path, meta)` also opens
`<csv_path without .csv>_raw.jsonl`. `current_raw_log_path() -> Optional[str]` lets
`pendulastic_app.py` find the file right after recording stops.

**Size/perf:** ~100 Hz × 3 sensors × ~15 s trial ≈ 4,500 lines (~400 KB) — trivial, buffered write,
flushed on close alongside the existing file. This is purely additive; `_log_sample()`'s existing
fused-angle CSV output is unchanged.

---

## 4. Core Replay Engine

`replay_trial(raw_samples, params) -> (t_array, angle_deg_array)`, where
`params = {"beta", "ema_alpha", "flex_axis_capture", "gravity_seed"}`. Reuses `MadgwickAHRS`,
`_gravity_seed`, `_qconj`, `_qmul`, `_FLEX_CAPTURE_THRESHOLD` imported directly from
`pendulastic_imu_server` — no duplicated math.

### The zero-reference problem

Live, the clinician presses "Zero Sensor" *before* `start_recording()`, under whatever fixed
BETA/gravity-seed was live that day. Reusing that stored zero-quaternion snapshot for every
candidate parameter set would compare each candidate against a reference computed under a
different, uncontrolled configuration — invalidating the search. **Fix:** the replay derives its
own zero-reference per candidate: the quaternion state at the instant just before the
flex-axis-capture threshold first fires (the onset of the deliberate release motion), recomputed
fresh for every candidate. This onset-of-motion detection always runs, regardless of the
`flex_axis_capture` candidate parameter — it is only a timing marker for where "zero" is measured.
`flex_axis_capture` separately controls what happens *after* that: whether the resulting quaternion
delta is projected onto the captured axis (`True`) or left as the axis-agnostic total-rotation
fallback (`False`).

### The EMA cadence problem

Live, `_imu_poll_worker` polls `get_live_angle()` at a fixed ~20 Hz (`self.after`/`time.sleep(0.05)`
cadence) and applies EMA to *that* polled series, not the raw ~100 Hz gyro-driven series. EMA's
effective smoothing strength depends on both α and the sample interval it's applied at. **Fix:** the
replay runs the AHRS at full raw-sample cadence (per-role, mirroring `on_accel`/`on_mag`/`on_gyro`
exactly, including the same `dt` derivation from `phone_ts_ms`), then resamples to the same 50 ms
tick grid before applying EMA — matching the real pipeline stage-for-stage.

### Everything else mirrors `swing_angle_deg()` directly

Two-role trials use the proximal/distal relative delta; solo trials use absolute-from-zero.
`flex_axis_capture=True` projects onto the captured axis; `False` uses the axis-agnostic
total-rotation fallback (today's behavior before the axis has been captured). The final output
applies the same `180.0 - swing` conversion `get_live_angle()` already uses, so the returned series
is exactly what would have been written to the trial CSV live.

---

## 5. Constraint Scorer

`score_waveform(t, angle_deg) -> {"passes": bool, "penalty": float, "params": dict | None}`.

**A. Horizontal start** — median of the first ~0.3 s must be `180° ± 8°`. Penalty = distance outside
tolerance.

**B. Oscillation range** — post-release minimum angle must fall in `[80°, 178°]` (the "~90°" target
with realistic slack — MS/spastic patients may not reach exactly 90°, so this isn't hard-gated at
90). Penalty = distance outside band.

**C. Continuity** — on the 20 Hz series: no single-tick jump > 25°/50 ms (catches quaternion-flip
glitches/clipping), and no run of ≥6 consecutive ticks (300 ms) with |Δangle| < 0.05° during the
active-swing window (catches the "staircase" the recent fix commits targeted). Penalty = weighted
violation count.

**D. Truthfulness gate** — `pendulastic_pt_score.compute_pt_params(t, angle_deg)` is imported and
reused as-is (same release-detection, Savitzky-Golay smoothing, detrending already covered by the
existing 84 passing tests). `None` (no detectable release) is a hard fail regardless of how smooth
the curve looks. Otherwise, broad physiological *plausibility* bounds are checked — not the clinical
`HEALTHY_REF` severity comparison used for MAS scoring, which is condition-dependent and wrong to
gate tuning on:

- `N` (swing count) ∈ [1, 10]
- `A0` (initial swing) ∈ [10°, 90°]
- `f` (frequency) ∈ [0.3, 3.0] Hz — matches the pendulum's documented ~1 Hz dynamics
- `R2n`, `omega_max_n`, `omega_min_n` finite (not NaN/inf)

This is what stops the search from "cheating" — e.g. picking heavy EMA smoothing that produces a
plateau-free but nearly-flat curve with no real oscillation. A–C alone can't distinguish a genuinely
smooth *real* swing from an over-smoothed fake one; requiring a detectable release, a real swing
count, and a frequency in the physically possible band does.

`passes = A_ok and B_ok and C_ok and params is not None and D_ok`. `penalty` is a weighted sum of
A–C's continuous distances; D is a binary gate (asking "did a real swing happen," not "how good is
it").

---

## 6. Grid Search + Persistence

**Grid** (bounded): `beta ∈ {0.02, 0.041, 0.08, 0.15}` × `ema_alpha ∈ {0.1, 0.3, 0.5}` ×
`flex_axis_capture ∈ {True, False}` × `gravity_seed ∈ {True, False}` = **48 combinations**.
`0.041`/`0.3`/`True`/`True` are today's actual hardcoded values, so the current behavior is always
in the grid as the baseline to beat.

`tune(raw_samples) -> dict` runs all 48 through `replay_trial` → `score_waveform`:

- Any candidates with `passes=True` → keep the lowest-penalty one among those.
- None pass → return the lowest-penalty candidate anyway, tagged `passes=False`; **do not persist**.
  A single bad/atypical trial must not silently overwrite a working config.

**Persistence — `imu_calibration_config.json`** at repo root:

```json
{"beta": 0.041, "ema_alpha": 0.3, "flex_axis_capture": true, "gravity_seed": true,
 "penalty": 2.31, "passes": true, "tuned_at": "...", "source_trial": "..."}
```

`save_config()` only overwrites if the new candidate strictly improves on the currently persisted
one (any passing config beats a non-passing one; among passing configs, lower penalty wins) — a
monotonic ratchet, never a regression from a fluke trial. `load_config()` returns today's hardcoded
values as the default when the file doesn't exist, so a fresh checkout behaves identically to
current behavior until a trial actually improves on it. Loads/saves validate schema (all 4 keys,
correct types) and write atomically (temp file + `os.replace`).

**Startup wiring:** `pendulastic_imu_server.py` calls `load_config()` once at import, replacing the
hardcoded `BETA` constant and threading `flex_axis_capture`/`gravity_seed` as conditionals into
`zero()`'s axis-arming and `on_accel()`'s seeding call. `pendulastic_app.py`'s `_imu_poll_worker`
reads `ema_alpha` from the same config instead of its hardcoded `0.3`. All four are mechanical:
swap a constant for a config lookup, same default value as today.

**Live trigger:** on the RECORDING→REVIEW transition for IMU trials, load that trial's raw JSONL via
`current_raw_log_path()`, call `tune()` on the existing background worker thread the app already
uses for other sources (matching `_run_video_file_hpe`'s pattern — no new async machinery). If
`tune()` finds a passing configuration, the trial's already-saved CSV is **rewritten** (same path,
same `DataManager.save_trial` call) with the tuned `replay_trial` series before the REVIEW screen
loads it — so the file every downstream tool (`pendulastic_pt_score.py` CLI, batch scripts) reads
matches what the clinician sees, not the original (possibly broken) live-computed series. The global
config is persisted only if this trial's winning configuration also improves on it. If no raw log
exists for this trial, or no candidate passes, fall back to today's behavior unmodified — the
originally-saved CSV is left untouched and REVIEW shows it as it does today — with a small
non-blocking note. Tuning failure must never block the clinician from seeing trial data.

**CLI (`tune_imu.py <raw_log.jsonl> [more...]`):** same `tune()`/`save_config()`; multiple logs
average penalty per candidate for a more robust pick; `--force` bypasses the no-regression guard for
research use. 48 candidates × ~1,500-sample replay is pure numpy — well under a second, no threading
needed.

---

## 7. Error Handling

The overriding rule: tuning failures must never block a clinician from seeing trial data.

| Failure | Handling |
|---|---|
| Malformed/truncated raw log line | Skip individually; abort only if fewer than 40 valid samples survive (matches `compute_pt_params`'s existing threshold), returning "insufficient data" |
| No raw log for this trial | Fall back to today's unmodified behavior; small non-blocking note, never a block/crash |
| Every candidate fails the truthfulness gate | Return least-bad candidate tagged `passes=False`; surface "no configuration met the physical constraints"; nothing persisted |
| Config file missing/corrupt | `load_config()` falls back to hardcoded defaults on any schema/type mismatch |
| Concurrent writes (CLI + live, or two trials in quick succession) | Not solved with file locking — single-user desktop research tool, not a multi-writer service. Worst case is a rare lost improvement (never corruption, since writes are atomic and gated by a strict-improvement check) — accepted, not engineered around |

---

## 8. Testing Plan

1. **`replay_trial` unit tests** — synthetic hand-crafted raw samples (constant-down accel + a
   scripted gyro burst rotating a known angle around a known axis); assert the output matches a
   hand-computed expected value within tolerance. Validates AHRS/flex-axis/self-derived-zero logic
   without real hardware.
2. **`score_waveform` unit tests** — one synthetic series per failure mode (bad start, clipped step,
   plateaued/staircase), plus a "trick" series: heavily over-smoothed, technically plateau-free, but
   with zero real oscillation — proves the truthfulness gate (Section 5D) rejects it where A–C alone
   would pass it.
3. **`tune`/persistence unit tests** — fixed/stubbed candidate scores verifying: best-passing-wins,
   correct fallback when none pass, the monotonic-improvement guard rejects a worse config, and a
   fresh `config.json` reproduces today's hardcoded defaults exactly.
4. **Regression** — the existing 84 passing tests must still pass after the mechanical swap of
   hardcoded `BETA`/`_EMA_ALPHA`/etc. for `load_config()` lookups.
5. **Real-hardware gap (flagged, not automatable here)** — none of the existing recorded trials have
   raw logs (they predate this feature — confirmed against
   `data/PID_test_imu_LEG_Right_MS_TRIAL_1_imu.csv`, which has no raw companion file and an all-`NaN`
   `knee_angle_deg` column), so end-to-end validation against a real pendulum drop can only happen
   after implementation, with actual Sensor Stream hardware. This is a manual acceptance step, not
   covered by automated tests.

---

## 9. Out of Scope

- The Tibial Inclination Model (κ = 90 + β − arccos(sin β / 1.2)) — explicitly deferred by the user.
- ICC or any clinical-validity metric beyond the plausibility gate in Section 5D.
- Per-participant calibration profiles — single global config only (per user decision).
- Retuning existing historical trials — no raw logs exist for them; tuning only applies to trials
  recorded after this feature ships.
- Any change to the separate MediaPipe-vs-OptiTrack video calibration pipeline
  (`calibrate.py`/`align_and_calibrate.py`) — unrelated subsystem, different data source.
